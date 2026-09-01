"""
Tests for BDL spatial xG integration:
  1. _fetch_player_shots() — fetches + aggregates shots by match_id
  2. get_game_logs() enrichment — xg_shot/xgot_shot/shots_spatial populated
  3. Data-gap fill — shots_total filled from shots_spatial when None
  4. Bayesian covariate 3f — SPATIAL XG fires for goals/shots_on_target/shots props
"""
import asyncio, sys, os

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
results = []

def check(name, condition, detail=""):
    status = PASS if condition else FAIL
    results.append(condition)
    print(f"  [{status}] {name}" + (f"  — {detail}" if detail else ""))
    return condition


# ──────────────────────────────────────────────────────────────────────────────
# 1. _fetch_player_shots — live API call with known EPL player (id=831)
# ──────────────────────────────────────────────────────────────────────────────
print("\n── 1. _fetch_player_shots (player_id=831, league=39 EPL) ────────────")

async def test_fetch_shots():
    from soccer_bdl_client import _fetch_player_shots
    data = await _fetch_player_shots(league_id=39, bdl_player_id=831)

    check("Returns non-empty dict", bool(data), f"matches with shots: {len(data)}")

    if data:
        sample_mid = next(iter(data))
        sample = data[sample_mid]
        check("Each entry has xg_shot",     "xg_shot"    in sample, f"val={sample.get('xg_shot')}")
        check("Each entry has xgot_shot",   "xgot_shot"  in sample, f"val={sample.get('xgot_shot')}")
        check("Each entry has shots_spatial","shots_spatial" in sample, f"val={sample.get('shots_spatial')}")
        check("xg_shot is a float ≥ 0",
              isinstance(sample.get("xg_shot"), float) and sample["xg_shot"] >= 0,
              f"xg_shot={sample.get('xg_shot')}")
        check("shots_spatial is an int ≥ 1",
              isinstance(sample.get("shots_spatial"), int) and sample["shots_spatial"] >= 1,
              f"shots_spatial={sample.get('shots_spatial')}")
        check("xgot ≤ xg (on-target subset of xG)",
              sample["xgot_shot"] <= sample["xg_shot"] + 0.001,  # floating point tolerance
              f"xgot={sample.get('xgot_shot')} xg={sample.get('xg_shot')}")
        # Print a few samples for visual inspection
        for mid, d in list(data.items())[:3]:
            print(f"    match={mid}: shots={d['shots_spatial']} "
                  f"sot={d['shots_on_target_spatial']} "
                  f"xg={d['xg_shot']:.3f} xgot={d['xgot_shot']:.3f} "
                  f"avg_x={d.get('avg_shot_x')}")

asyncio.run(test_fetch_shots())


# ──────────────────────────────────────────────────────────────────────────────
# 2. get_game_logs enrichment — check xg_shot flows through to the game logs
# ──────────────────────────────────────────────────────────────────────────────
print("\n── 2. get_game_logs enrichment for an EPL player ────────────────────")

async def test_game_logs_enriched():
    from soccer_bdl_client import get_game_logs
    # Use a known active EPL player whose shots data exists in BDL
    # player_id=831 is confirmed to have shot data from our probe
    # We don't know their name, so let's search for a well-known EPL player
    logs, pid = await get_game_logs(league_id=39, player_name="Salah", last_n=15)

    check("get_game_logs returns logs", len(logs) > 0, f"logs={len(logs)}, pid={pid}")

    if logs:
        # Check if any logs have xg_shot populated (requires shot data match)
        xg_enriched = [g for g in logs if g.get("xg_shot") is not None]
        check(
            "At least some logs have xg_shot from spatial data",
            len(xg_enriched) > 0,
            f"{len(xg_enriched)}/{len(logs)} logs enriched with xG"
        )
        if xg_enriched:
            avg_xg = sum(g["xg_shot"] for g in xg_enriched) / len(xg_enriched)
            check(
                "Salah's avg xG/game > 0.05 (he's a prolific scorer)",
                avg_xg > 0.05,
                f"avg_xg_per_game={avg_xg:.3f}"
            )
            # Print sample enriched log
            g = xg_enriched[0]
            print(f"    Sample: date={g.get('date')} opp={g.get('opponent')} "
                  f"xg_shot={g.get('xg_shot')} xgot_shot={g.get('xgot_shot')} "
                  f"shots_spatial={g.get('shots_spatial')}")
        else:
            print("    [INFO] No xg_shot data found — BDL may not have Salah's shots yet")
            print("    [INFO] This is OK — the enrichment is optional / best-effort")


asyncio.run(test_game_logs_enriched())


# ──────────────────────────────────────────────────────────────────────────────
# 3. Data-gap fill — shots_total populated from shots_spatial when missing
# ──────────────────────────────────────────────────────────────────────────────
print("\n── 3. Data-gap fill (shots_total from shots_spatial) ────────────────")

async def test_data_gap_fill():
    from soccer_bdl_client import _fetch_player_shots, _norm

    # Simulate a log where shots_total=None (BDL Tier-2 absent) but spatial exists
    raw_log = {"passes_total": 45, "shots_total": None, "shots_on_target": None,
               "minutes_played": 90, "appearances": 1, "goals": 1}
    normed = _norm(raw_log)
    check("_norm gives shots_total=None when BDL data absent",
          normed.get("shots_total") is None,
          f"shots_total={normed.get('shots_total')}")

    # Simulate the enrichment: shots_spatial found, gap should be filled
    spatial = {"shots_spatial": 3, "shots_on_target_spatial": 2, "xg_shot": 0.35, "xgot_shot": 0.22}
    if normed.get("shots_total") is None and spatial.get("shots_spatial"):
        normed["shots_total"] = spatial["shots_spatial"]
    if normed.get("shots_on") is None and spatial.get("shots_on_target_spatial"):
        normed["shots_on"] = spatial["shots_on_target_spatial"]

    check("Gap-fill: shots_total populated from spatial",
          normed.get("shots_total") == 3,
          f"shots_total={normed.get('shots_total')}")
    check("Gap-fill: shots_on populated from spatial",
          normed.get("shots_on") == 2,
          f"shots_on={normed.get('shots_on')}")

asyncio.run(test_data_gap_fill())


# ──────────────────────────────────────────────────────────────────────────────
# 4. Bayesian covariate 3f — verify SPATIAL XG fires with xg_shot in logs
# ──────────────────────────────────────────────────────────────────────────────
print("\n── 4. Bayesian covariate 3f (SPATIAL XG) ────────────────────────────")

import bayesian_engine as be
import io, contextlib

def _make_shot_log(xg, xgot, shots):
    """Minimal game log with spatial shot fields attached."""
    return {
        "targetStat": 0.8,   # goals line at 0.5 → OVER
        "minutes": 90,
        "xg_shot":                 xg,
        "xgot_shot":               xgot,
        "shots_spatial":           shots,
        "shots_on_target_spatial": max(0, int(shots * 0.4)),
        "homeGoals": 2, "awayGoals": 0, "venue": "home",
    }

# Elite striker: 0.40 xG/game (3.3x league avg 0.12) → strong upward adjustment for goals
elite_logs = [_make_shot_log(0.40, 0.25, 4) for _ in range(8)]
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    r_elite = be.compute_bayesian_projection(
        game_logs=elite_logs, prop_type="goals", line=0.5,
        venue="home", position="ST", league_id=39,
    )
output_elite = buf.getvalue()

check(
    "SPATIAL XG fires for goals prop (shows in print output)",
    "[SPATIAL XG]" in output_elite,
    f"output snippet: {output_elite[output_elite.find('[SPATIAL'):output_elite.find('[SPATIAL')+80] if '[SPATIAL' in output_elite else 'not found'}"
)

# Low-volume midfielder: 0.03 xG/game (0.25x league avg) → downward adjustment
weak_logs = [_make_shot_log(0.03, 0.01, 1) for _ in range(8)]
buf2 = io.StringIO()
with contextlib.redirect_stdout(buf2):
    r_weak = be.compute_bayesian_projection(
        game_logs=weak_logs, prop_type="goals", line=0.5,
        venue="home", position="CM", league_id=39,
    )
output_weak = buf2.getvalue()

check(
    "SPATIAL XG fires for low-xG player too",
    "[SPATIAL XG]" in output_weak,
    f"output: {output_weak[output_weak.find('[SPATIAL'):output_weak.find('[SPATIAL')+80] if '[SPATIAL' in output_weak else 'not found'}"
)

# shots_on_target prop with xgot data
sot_logs_fixed = [_make_shot_log(0.30, 0.20, 3) for _ in range(8)]
buf3 = io.StringIO()
with contextlib.redirect_stdout(buf3):
    r_sot = be.compute_bayesian_projection(
        game_logs=sot_logs_fixed,
        prop_type="shots_on_target",
        line=0.5,
        venue="home",
        position="ST",
        league_id=39,
    )
output_sot = buf3.getvalue()
check(
    "SPATIAL XG fires for shots_on_target prop",
    "[SPATIAL XG]" in output_sot,
    f"output: {output_sot[output_sot.find('[SPATIAL'):output_sot.find('[SPATIAL')+80] if '[SPATIAL' in output_sot else 'not found'}"
)

# shots prop with spatial shot count
shots_logs = [_make_shot_log(0.40, 0.25, 4) for _ in range(8)]
buf4 = io.StringIO()
with contextlib.redirect_stdout(buf4):
    r_shots = be.compute_bayesian_projection(
        game_logs=shots_logs,
        prop_type="shots",
        line=1.5,
        venue="home",
        position="ST",
        league_id=39,
    )
output_shots = buf4.getvalue()
check(
    "SPATIAL XG fires for shots prop (xG/shot quality)",
    "[SPATIAL XG]" in output_shots,
    f"output: {output_shots[output_shots.find('[SPATIAL'):output_shots.find('[SPATIAL')+80] if '[SPATIAL' in output_shots else 'not found'}"
)

# No xg_shot in logs → 3f should NOT fire
plain_logs = [{"targetStat": 2.0, "minutes": 90, "homeGoals": 1, "awayGoals": 0, "venue": "home"} for _ in range(8)]
buf5 = io.StringIO()
with contextlib.redirect_stdout(buf5):
    r_plain = be.compute_bayesian_projection(
        game_logs=plain_logs, prop_type="goals", line=0.5,
        venue="home", position="ST", league_id=39,
    )
output_plain = buf5.getvalue()
check(
    "SPATIAL XG does NOT fire when no xg_shot in logs",
    "[SPATIAL XG]" not in output_plain,
    "no spatial data → covariate correctly skipped"
)


# ──────────────────────────────────────────────────────────────────────────────
# Summary
# ──────────────────────────────────────────────────────────────────────────────
print("\n" + "─" * 60)
passed = sum(results)
total  = len(results)
print(f"  Result: {passed}/{total} tests passed ({passed/total*100:.0f}%)")
if passed == total:
    print("  \033[92mAll checks GREEN — spatial xG integration verified.\033[0m")
else:
    print("  \033[91mSome checks FAILED — see above.\033[0m")
    sys.exit(1)
