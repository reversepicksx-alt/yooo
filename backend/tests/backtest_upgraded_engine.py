"""
BACKTEST: Re-run historically MISSED picks through the upgraded engine.

The upgrade ships three new signals:
  1. League-empirical calibration (post-posterior nudge from settled picks)
  2. Projection-gap surfacing (edgeGapPct / edgeGapBand on the response)
  3. Game-script ingestion (Vegas-derived chase / nailbiter nudges)

We pull a handful of MISSED picks from the live Mongo collection — biased
toward bucket types where the cheat-sheet showed real systematic bias —
then re-run each one against the running backend and report whether the
upgraded engine FLIPS the recommendation, produces a meaningfully
different projection, or surfaces a stronger / weaker edge band.
"""
import asyncio
import os
import sys
import json
import httpx

# Make the backend importable so we can hit Mongo directly
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config import db  # noqa: E402

API_URL = os.environ.get("LOCAL_API_URL", "http://localhost:8000")


async def fetch_missed_candidates(limit: int = 8) -> list:
    """Pull a diverse set of missed pass_attempts picks from Mongo."""
    # We over-fetch then dedupe by (player, prop, line) so we can hit the
    # cheat-sheet hot buckets first (CB-away-under, GK-away-under,
    # CDM-home-over) and still have variety.
    cursor = db.picks.find(
        {"result": "miss",
         "recommendation": {"$in": ["over", "under"]},
         "actualValue": {"$ne": None},
         "projectedValue": {"$ne": None},
         "propType": "pass_attempts",
         "playerId": {"$ne": None},
         "teamId": {"$ne": None},
         "opponentId": {"$ne": None}},
    ).sort("createdAt", -1).limit(40)

    rows = await cursor.to_list(length=40)

    # Bucket priorities — these are the buckets where league-priors
    # actually has signal worth applying.
    def _priority(p):
        pos = (p.get("position") or "").upper()
        rec = p.get("recommendation")
        ven = p.get("venue")
        if pos in {"CB", "LB", "RB"} and ven == "away" and rec == "under": return 0
        if pos == "GK" and rec == "under":                                   return 1
        if pos in {"CDM", "DM", "CM"} and ven == "home" and rec == "over":   return 2
        if pos == "GK" and ven == "home":                                    return 3
        return 4

    rows.sort(key=_priority)

    seen = set()
    out = []
    for p in rows:
        key = (p.get("playerId"), p.get("propType"), p.get("line"))
        if key in seen: continue
        seen.add(key)
        out.append(p)
        if len(out) >= limit: break
    return out


async def run_prediction(client: httpx.AsyncClient, pick: dict) -> dict:
    payload = {
        "playerId":     pick["playerId"],
        "playerName":   pick.get("playerName") or pick.get("name") or "Unknown",
        "teamId":       pick["teamId"],
        "teamName":     pick.get("teamName") or "",
        "opponentId":   pick["opponentId"],
        "opponentName": pick.get("opponentName") or "",
        "leagueId":     pick.get("leagueId"),
        "propType":     pick["propType"],
        "line":         float(pick["line"]),
        "venue":        pick.get("venue") or "home",
    }
    try:
        r = await client.post(f"{API_URL}/api/predict", json=payload, timeout=90)
        if r.status_code == 200:
            return r.json()
        return {"error": f"HTTP {r.status_code}: {r.text[:300]}"}
    except Exception as e:
        return {"error": str(e)}


def _hit(rec: str, line: float, actual: float) -> bool:
    if actual is None: return False
    if rec == "over":  return actual > line
    if rec == "under": return actual < line
    return False


async def main():
    print("=" * 88)
    print("BACKTEST: ≥3 historically MISSED picks re-run through the UPGRADED engine")
    print("Upgrades active: (1) league calibration  (2) edge-gap surfacing  (3) game-script")
    print("=" * 88)

    candidates = await fetch_missed_candidates(limit=4)
    if not candidates:
        print("No missed picks found in Mongo. Aborting.")
        return

    print(f"\nPulled {len(candidates)} candidate missed picks. Running through engine...\n")

    results = []
    async with httpx.AsyncClient() as client:
        for i, pick in enumerate(candidates, 1):
            name = pick.get("playerName") or "?"
            pos  = pick.get("position") or "?"
            ven  = pick.get("venue") or "?"
            old_rec = pick["recommendation"]
            line = float(pick["line"])
            actual = pick.get("actualValue")
            old_proj = pick.get("projectedValue")
            league = pick.get("leagueId")

            print(f"[{i}/{len(candidates)}] {name} ({pos}, {ven}) — league {league}")
            print(f"          OLD: {old_rec.upper()} {line} → projected {old_proj}, actual {actual} → MISS")

            res = await run_prediction(client, pick)
            if "error" in res:
                print(f"          ENGINE ERROR: {res['error'][:150]}")
                results.append({"pick": pick, "error": res["error"]})
                continue

            new_rec  = res.get("recommendation", "?")
            new_proj = res.get("projectedValue", "?")
            new_conf = res.get("confidenceScore", "?")
            bayes    = res.get("bayesianMetrics", {}) or {}

            edge_pct  = bayes.get("edgeGapPct")
            edge_band = bayes.get("edgeGapBand")
            lcal      = bayes.get("leagueCalibration", {}) or {}
            gscript   = bayes.get("gameScript", {}) or {}

            flipped  = new_rec != old_rec
            would_hit = _hit(new_rec, line, actual)

            print(f"          NEW: {new_rec.upper()} {line} → projected {new_proj}, conf {new_conf}%")
            print(f"               edgeGap = {edge_pct}%  band = {edge_band}")
            if lcal.get("applied"):
                print(f"               league_calib n={lcal.get('n')} hit_rate={lcal.get('hit_rate')} "
                      f"mult={lcal.get('multiplier')} dir={lcal.get('direction')}")
            else:
                print(f"               league_calib: not applied (no bucket / inert)")
            if gscript.get("applied"):
                print(f"               game_script  mult={gscript.get('multiplier')} :: {gscript.get('reason')}")
            else:
                print(f"               game_script: not applied")
            print(f"          ── flipped={flipped}  would_hit_now={would_hit}\n")

            results.append({
                "pick": {k: pick.get(k) for k in
                         ["playerName", "position", "venue", "leagueId",
                          "propType", "line", "actualValue", "projectedValue",
                          "recommendation"]},
                "new_rec": new_rec,
                "new_proj": new_proj,
                "new_conf": new_conf,
                "edge_gap_pct": edge_pct,
                "edge_gap_band": edge_band,
                "league_calibration": lcal,
                "game_script": gscript,
                "flipped": flipped,
                "would_hit_now": would_hit,
            })

    # ── Summary ─────────────────────────────────────────────────────────
    print("=" * 88)
    print("SUMMARY")
    print("=" * 88)
    n          = len([r for r in results if "error" not in r])
    flipped    = sum(1 for r in results if r.get("flipped"))
    new_hits   = sum(1 for r in results if r.get("would_hit_now"))
    lcal_app   = sum(1 for r in results if (r.get("league_calibration") or {}).get("applied"))
    gs_app     = sum(1 for r in results if (r.get("game_script") or {}).get("applied"))

    for r in results:
        if "error" in r:
            print(f"  ⚠ ERROR  {r['pick'].get('playerName', '?')}: {str(r['error'])[:80]}")
            continue
        p = r["pick"]
        marker = "✅ NEW HIT  " if r["would_hit_now"] else ("↔ FLIPPED   " if r["flipped"] else "❌ STILL MISS")
        print(f"  {marker}  {p.get('playerName', '?'):20} "
              f"old={p['recommendation']:>5} → new={r['new_rec']:>5}  "
              f"line={p['line']:>5}  actual={p['actualValue']:>4}  "
              f"edge={r.get('edge_gap_pct'):>5}% [{r.get('edge_gap_band')}]")

    print()
    print(f"  Picks tested:               {n}")
    print(f"  Recommendation flipped:     {flipped}/{n}")
    print(f"  Would HIT under new engine: {new_hits}/{n}  ← accuracy lift")
    print(f"  League calibration fired:   {lcal_app}/{n}")
    print(f"  Game-script nudge fired:    {gs_app}/{n}")

    out_path = "/tmp/backtest_upgraded_engine.json"
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\n  Full results saved to {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
