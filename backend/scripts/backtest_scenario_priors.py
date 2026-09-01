"""
LEAVE-ONE-OUT BACKTEST: scenario_priors layer

For a sample of N settled picks across different prop types, recompute the
scenario_priors nudge with the test pick EXCLUDED from its training bucket
(no leakage), apply it to the original projectedValue, and check whether the
recommendation would have flipped — and whether that flip would have helped
or hurt.

Methodology:
  * For each test pick, rebuild the (scenario × pos × prop × side) bucket
    using all OTHER settled picks (the test pick excluded).
  * Compute the same shrinkage / multiplier as scenario_priors.lookup_single,
    but on the leakage-free bucket.
  * NEW projection = original projectedValue × multiplier
  * NEW recommendation = "over" if NEW projection > line else "under"
    (kept "push" if exact equal — vanishingly rare for projections)
  * NEW result = compare actualValue vs line under NEW recommendation
  * Compare ORIGINAL result vs NEW result.

Important caveat: this assumes "perfect knowledge" of the actual game
script (we use scenarioBucket = the bucket the game actually finished
in, with prob 1.0). In production, the predicted scenario probability
vector would be derived from pre-game odds and would be noisier — so
this backtest is an UPPER BOUND on the layer's value.
"""
import asyncio
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from motor.motor_asyncio import AsyncIOMotorClient
from scenario_priors import POS_BUCKET, _MIN_SAMPLE, _SHRINK_K, _MAX_NUDGE


def _bucket_pos(raw):
    if not raw:
        return None
    return POS_BUCKET.get(str(raw).upper().strip(), str(raw).upper().strip())


def compute_loo_nudge(test_pick, all_picks):
    """Compute the scenario_priors multiplier for test_pick using all OTHER picks."""
    scen = test_pick.get("scenarioBucket")
    pos = _bucket_pos(test_pick.get("position"))
    prop = test_pick.get("propType")
    side = test_pick.get("recommendation")
    test_id = test_pick.get("pickId")
    if not all([scen, pos, prop, side]):
        return None
    errors = []
    hits = 0
    n = 0
    for p in all_picks:
        if p.get("pickId") == test_id:
            continue
        if (p.get("scenarioBucket") != scen
                or _bucket_pos(p.get("position")) != pos
                or p.get("propType") != prop
                or p.get("recommendation") != side):
            continue
        try:
            err = float(p["actualValue"]) - float(p["projectedValue"])
        except (TypeError, ValueError, KeyError):
            continue
        errors.append(err)
        n += 1
        if p.get("result") == "hit":
            hits += 1
    if n < _MIN_SAMPLE:
        return {"available": False, "n": n, "reason": f"bucket too small ({n} < {_MIN_SAMPLE})"}
    mean_err = sum(errors) / n
    hit_rate = hits / n
    proj = float(test_pick["projectedValue"])
    if proj == 0:
        return {"available": False, "n": n, "reason": "projection=0"}
    rel_bias = mean_err / max(abs(proj), 1e-6)
    shrink = n / (n + _SHRINK_K)
    nudge = max(-_MAX_NUDGE, min(_MAX_NUDGE, rel_bias * shrink))
    return {
        "available": True,
        "n": n, "hit_rate": round(hit_rate, 3),
        "mean_err": round(mean_err, 2),
        "shrink": round(shrink, 3),
        "multiplier": round(1.0 + nudge, 4),
    }


def grade(actual, line, recommendation):
    if actual is None or line is None:
        return None
    rec = (recommendation or "").lower()
    if actual > line:
        return "hit" if rec == "over" else "miss"
    if actual < line:
        return "hit" if rec == "under" else "miss"
    return "push"


async def run_full_backtest(all_picks):
    """Same logic, run silently across the entire dataset."""
    summary = {"flipped_to_hit": 0, "flipped_to_miss": 0,
               "kept_hit": 0, "kept_miss": 0, "no_data": 0,
               "tested": 0}
    flips_detail = []
    for pick in all_picks:
        loo = compute_loo_nudge(pick, all_picks)
        if not loo or not loo.get("available"):
            summary["no_data"] += 1
            continue
        try:
            proj = float(pick["projectedValue"])
            line = float(pick["line"])
            actual = float(pick["actualValue"])
        except (KeyError, TypeError, ValueError):
            summary["no_data"] += 1
            continue
        summary["tested"] += 1
        orig_rec = pick["recommendation"]
        orig_result = pick["result"]
        new_proj = proj * loo["multiplier"]
        if new_proj > line:
            new_rec = "over"
        elif new_proj < line:
            new_rec = "under"
        else:
            new_rec = orig_rec
        if new_rec == orig_rec:
            if orig_result == "hit":
                summary["kept_hit"] += 1
            else:
                summary["kept_miss"] += 1
        else:
            new_result = grade(actual, line, new_rec)
            if orig_result == "miss" and new_result == "hit":
                summary["flipped_to_hit"] += 1
                flips_detail.append((pick, loo, "MISS→HIT", proj, new_proj, line))
            elif orig_result == "hit" and new_result == "miss":
                summary["flipped_to_miss"] += 1
                flips_detail.append((pick, loo, "HIT→MISS", proj, new_proj, line))
    return summary, flips_detail


async def main():
    db = AsyncIOMotorClient(os.environ.get("MONGO_URL", "mongodb://localhost:27017"))[
        os.environ.get("DB_NAME", "test_database")
    ]
    # Pull every settled pick with the data we need
    cursor = db.picks.find(
        {"result": {"$in": ["hit", "miss"]},
         "recommendation": {"$in": ["over", "under"]},
         "actualValue": {"$ne": None},
         "projectedValue": {"$ne": None},
         "scenarioBucket": {"$exists": True, "$ne": None}},
        {"_id": 0, "pickId": 1, "playerName": 1, "teamName": 1, "opponentName": 1,
         "leagueId": 1, "scenarioBucket": 1, "position": 1, "propType": 1,
         "recommendation": 1, "result": 1, "actualValue": 1, "projectedValue": 1,
         "line": 1, "venue": 1, "matchScore": 1},
    )
    all_picks = await cursor.to_list(length=20000)
    print(f"[BACKTEST] dataset = {len(all_picks)} settled picks")

    # Pick a representative sample: ≥1 from each prop type with enough data
    # AND prefer cases where a populated bucket exists (otherwise nothing happens)
    by_prop = defaultdict(list)
    for p in all_picks:
        by_prop[p["propType"]].append(p)

    sample = []
    # Try to grab variety: pass_attempts (most), saves, shots, dribbles, crosses
    for prop in ["pass_attempts", "saves", "shots", "dribbles", "crosses",
                 "clearances", "shots_on_target", "tackles"]:
        picks = by_prop.get(prop, [])
        if not picks:
            continue
        # Prefer picks where the LOO bucket would actually fire
        for p in picks:
            cand = compute_loo_nudge(p, all_picks)
            if cand and cand.get("available"):
                sample.append(p)
                break
        if len(sample) >= 5 and prop in {"crosses", "clearances", "shots_on_target", "tackles"}:
            # Have 5 with variety, optionally add a small-sample one to show it correctly no-ops
            sample.append(picks[0])
            break
    if len(sample) < 5:
        # fall back: any picks where bucket fires
        for p in all_picks:
            if len(sample) >= 5:
                break
            if p in sample:
                continue
            cand = compute_loo_nudge(p, all_picks)
            if cand and cand.get("available"):
                sample.append(p)

    print(f"[BACKTEST] selected {len(sample)} test picks across prop types")
    print()
    print("=" * 110)

    # Run backtest
    summary = {"flipped_to_hit": 0, "flipped_to_miss": 0,
               "kept_hit": 0, "kept_miss": 0, "no_change_inert": 0,
               "no_data": 0}

    for i, pick in enumerate(sample, 1):
        loo = compute_loo_nudge(pick, all_picks)
        proj = float(pick["projectedValue"])
        line = float(pick["line"])
        actual = float(pick["actualValue"])
        orig_rec = pick["recommendation"]
        orig_result = pick["result"]

        print(f"\n#{i}  {pick.get('playerName','')} ({pick.get('teamName','')} "
              f"{pick.get('venue','')} vs {pick.get('opponentName','')}) "
              f"{pick.get('matchScore','')}")
        print(f"    {pick['propType']:14s} pos={pick.get('position','?'):4s} "
              f"scenario={pick.get('scenarioBucket'):14s}")
        print(f"    line={line:5.1f}  proj={proj:5.1f}  actual={actual:5.1f}  "
              f"orig_rec={orig_rec:5s}  orig_result={orig_result.upper()}")

        if not loo or not loo.get("available"):
            print(f"    SCENARIO PRIORS: INERT — {loo.get('reason') if loo else 'no data'}")
            summary["no_data"] += 1
            continue

        new_proj = round(proj * loo["multiplier"], 1)
        # New rec: which side of the line is the new projection on?
        if new_proj > line:
            new_rec = "over"
        elif new_proj < line:
            new_rec = "under"
        else:
            new_rec = orig_rec  # exactly on line — no opinion change
        new_result = grade(actual, line, new_rec)

        nudge_pct = (loo["multiplier"] - 1.0) * 100
        print(f"    SCENARIO PRIORS: bucket n={loo['n']:3d}  hit_rate={loo['hit_rate']:.2f}  "
              f"mean_err={loo['mean_err']:+.1f}  multiplier={loo['multiplier']:.4f} "
              f"({nudge_pct:+.2f}%)")
        print(f"    NEW: proj={new_proj:5.1f}  new_rec={new_rec:5s}  new_result={new_result.upper()}")

        if new_rec == orig_rec:
            tag = "NO CHANGE (same side)"
            if orig_result == "hit":
                summary["kept_hit"] += 1
            else:
                summary["kept_miss"] += 1
        else:
            if orig_result == "miss" and new_result == "hit":
                tag = "*** FLIPPED MISS → HIT ***"
                summary["flipped_to_hit"] += 1
            elif orig_result == "hit" and new_result == "miss":
                tag = "!!! FLIPPED HIT → MISS !!!"
                summary["flipped_to_miss"] += 1
            else:
                tag = f"FLIPPED side → result {new_result.upper()}"
        print(f"    OUTCOME: {tag}")

    print()
    print("=" * 110)
    print("5-PICK SAMPLE SUMMARY:")
    for k, v in summary.items():
        print(f"  {k:20s} {v}")
    flips_helped = summary["flipped_to_hit"]
    flips_hurt   = summary["flipped_to_miss"]
    net = flips_helped - flips_hurt
    print(f"  NET: {net:+d}  (would have flipped {flips_helped} miss→hit, "
          f"{flips_hurt} hit→miss)")

    # ───── Full-population backtest ─────
    print()
    print("=" * 110)
    print("FULL POPULATION BACKTEST (all settled picks, leave-one-out)")
    print("=" * 110)
    full, flips = await run_full_backtest(all_picks)
    orig_hits = sum(1 for p in all_picks if p["result"] == "hit")
    orig_n = len(all_picks)
    orig_rate = orig_hits / orig_n if orig_n else 0
    new_hits = full["kept_hit"] + full["flipped_to_hit"]
    new_n = full["tested"]
    # picks that didn't fire keep their original result
    inert_hits = sum(1 for p in all_picks
                     if p["result"] == "hit"
                     and (compute_loo_nudge(p, all_picks) is None
                          or not compute_loo_nudge(p, all_picks).get("available")))
    total_new_hits = new_hits + inert_hits
    new_rate_overall = total_new_hits / orig_n if orig_n else 0

    print(f"  total picks            {orig_n}")
    print(f"  picks where layer fires {new_n}  ({new_n/orig_n*100:.1f}%)")
    print(f"  picks where layer inert {full['no_data']}")
    print()
    print(f"  ORIG  hits/total = {orig_hits}/{orig_n}   = {orig_rate*100:.2f}%")
    print(f"  NEW   hits/total = {total_new_hits}/{orig_n}   = {new_rate_overall*100:.2f}%")
    delta_pp = (new_rate_overall - orig_rate) * 100
    print(f"  DELTA hit-rate       = {delta_pp:+.2f} percentage points")
    print()
    print(f"  flipped MISS → HIT  {full['flipped_to_hit']}")
    print(f"  flipped HIT  → MISS {full['flipped_to_miss']}")
    print(f"  NET FLIPS           {full['flipped_to_hit'] - full['flipped_to_miss']:+d}")
    print()
    if flips:
        print("  ─ Flip details ─")
        for pick, loo, kind, proj, new_proj, line in flips:
            print(f"   {kind:10s} {pick.get('playerName','?'):22s} "
                  f"{pick['propType']:14s} {pick.get('scenarioBucket'):14s} "
                  f"line={line:5.1f} proj={proj:5.1f} → {new_proj:5.1f} "
                  f"mult={loo['multiplier']:.4f}")


if __name__ == "__main__":
    asyncio.run(main())
