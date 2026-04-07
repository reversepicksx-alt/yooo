"""
BACKTEST: Re-run 4 missed predictions through the upgraded engine
Tests: Game Tempo Estimation, Favorite Dampening, Possession Contradiction

Missed picks to re-run:
1. L. Ayling - Middlesbrough AWAY vs Swansea - UNDER 59.5 Pass Attempts (actual: 76) → MISS
2. A. Morris - Middlesbrough AWAY vs Swansea - UNDER 58.5 Pass Attempts (actual: 78) → MISS
3. D. Sanderson - Derby HOME vs Stoke City - OVER 35.5 Pass Attempts (actual: 30) → MISS
4. B. Whiteman - Preston HOME vs QPR - OVER 50.5 Pass Attempts (actual: 44) → MISS
"""
import asyncio
import httpx
import json
import os
import sys

API_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://props-ai-predict.preview.emergentagent.com")

MISSED_PICKS = [
    {
        "name": "L. Ayling",
        "playerId": 19116,
        "teamId": 70,
        "teamName": "Middlesbrough",
        "opponentId": 76,
        "opponentName": "Swansea",
        "leagueId": 40,
        "propType": "pass_attempts",
        "line": 59.5,
        "venue": "away",
        "old_recommendation": "under",
        "actual_value": 76,
        "old_result": "MISS",
    },
    {
        "name": "A. Morris",
        "playerId": 201714,
        "teamId": 70,
        "teamName": "Middlesbrough",
        "opponentId": 76,
        "opponentName": "Swansea",
        "leagueId": 40,
        "propType": "pass_attempts",
        "line": 58.5,
        "venue": "away",
        "old_recommendation": "under",
        "actual_value": 78,
        "old_result": "MISS",
    },
    {
        "name": "D. Sanderson",
        "playerId": 123437,
        "teamId": 69,
        "teamName": "Derby",
        "opponentId": 75,
        "opponentName": "Stoke City",
        "leagueId": 40,
        "propType": "pass_attempts",
        "line": 35.5,
        "venue": "home",
        "old_recommendation": "over",
        "actual_value": 30,
        "old_result": "MISS",
    },
    {
        "name": "B. Whiteman",
        "playerId": 19941,
        "teamId": 59,
        "teamName": "Preston",
        "opponentId": 72,
        "opponentName": "QPR",
        "leagueId": 40,
        "propType": "pass_attempts",
        "line": 50.5,
        "venue": "home",
        "old_recommendation": "over",
        "actual_value": 44,
        "old_result": "MISS",
    },
]


async def run_prediction(client: httpx.AsyncClient, pick: dict) -> dict:
    """Call the /api/predict endpoint for a single pick."""
    payload = {
        "playerId": pick["playerId"],
        "playerName": pick["name"],
        "teamId": pick["teamId"],
        "teamName": pick["teamName"],
        "opponentId": pick["opponentId"],
        "opponentName": pick["opponentName"],
        "leagueId": pick["leagueId"],
        "propType": pick["propType"],
        "line": pick["line"],
        "venue": pick["venue"],
    }
    try:
        resp = await client.post(f"{API_URL}/api/predict", json=payload, timeout=50)
        if resp.status_code == 200:
            return resp.json()
        else:
            return {"error": f"HTTP {resp.status_code}: {resp.text[:200]}"}
    except Exception as e:
        return {"error": str(e)}


async def main():
    print("=" * 80)
    print("BACKTEST: Re-running 4 missed predictions through upgraded engine")
    print("=" * 80)
    print()

    async with httpx.AsyncClient() as client:
        results = []

        for i, pick in enumerate(MISSED_PICKS):
            print(f"\n{'─' * 70}")
            print(f"[{i+1}/4] {pick['name']} | {pick['teamName']} {pick['venue'].upper()} vs {pick['opponentName']}")
            print(f"       Old: {pick['old_recommendation'].upper()} {pick['line']} Pass Attempts → Actual: {pick['actual_value']} → {pick['old_result']}")
            print(f"       Running prediction...")

            result = await run_prediction(client, pick)

            if "error" in result:
                print(f"       ERROR: {result['error']}")
                results.append({"pick": pick, "result": result, "flipped": False})
                continue

            new_rec = result.get("recommendation", "?")
            new_proj = result.get("projectedValue", "?")
            new_conf = result.get("confidenceScore", "?")
            tempo = result.get("tempoScaling", {})
            fav_damp = result.get("favoriteDampening", {})
            poss_scale = result.get("possessionScaling", {})
            match_dom = result.get("matchDominance", {})
            coin_flip = result.get("coinFlip", False)
            fusion = result.get("fusionApplied", {})
            alerts = result.get("tacticalAlerts", [])
            bayes = result.get("bayesianMetrics", {})

            # Determine if the new recommendation would have been correct
            actual = pick["actual_value"]
            line = pick["line"]
            would_hit = (new_rec == "over" and actual > line) or (new_rec == "under" and actual < line)
            flipped = new_rec != pick["old_recommendation"]

            print(f"\n       ┌─── NEW PREDICTION ───────────────────────────")
            print(f"       │ Recommendation: {new_rec.upper()} {line}")
            print(f"       │ Projected Value: {new_proj}")
            print(f"       │ Confidence: {new_conf}%")
            print(f"       │ Coin Flip: {'YES' if coin_flip else 'No'}")

            if fusion:
                print(f"       │ Fusion: AI={fusion.get('aiProjection')}({fusion.get('aiRecommendation')}) + Bayes={fusion.get('bayesianPosterior')}({fusion.get('bayesianRecommendation')}) → {fusion.get('fusedProjection')}")

            if match_dom and match_dom.get("applied") is not False:
                print(f"       │ Match Dominance: {match_dom.get('expectedPoss', '?')}% expected poss, mult={match_dom.get('multiplier', '?')}")

            if tempo:
                print(f"       │ TEMPO SCALING: {tempo.get('expectedTempo', '?')} tempo, mult={tempo.get('tempoMultiplier', '?')}, goals={tempo.get('expectedTotalGoals', '?')}")
                print(f"       │   Pre-tempo: {tempo.get('preTempoValue')} → Post-tempo: {tempo.get('postTempoValue')}")

            if poss_scale:
                print(f"       │ POSSESSION SCALING: {poss_scale.get('expectedPossession', '?')}% poss, mult={poss_scale.get('multiplier', '?')}")
                print(f"       │   Pre-poss: {poss_scale.get('prePossession')} → Post-poss: {poss_scale.get('postPossession')}")

            if fav_damp:
                print(f"       │ FAVORITE DAMPENING: odds={fav_damp.get('teamOdds', '?')}, dampen={fav_damp.get('dampeningFactor', '?')}")
                print(f"       │   Pre-dampen: {fav_damp.get('preDampenValue')} → Post-dampen: {fav_damp.get('postDampenValue')}")

            if alerts:
                print(f"       │ Tactical Alerts:")
                for a in alerts:
                    print(f"       │   ⚠ {a}")

            if bayes:
                print(f"       │ Bayesian: posterior={bayes.get('posteriorMean')}, momentum={bayes.get('momentumLabel')}, streak={bayes.get('streakFlag')}")

            print(f"       │")
            print(f"       │ Old rec: {pick['old_recommendation'].upper()} → New rec: {new_rec.upper()}")
            print(f"       │ Direction flipped: {'YES ✓' if flipped else 'NO'}")
            print(f"       │ Would have HIT: {'YES ✅' if would_hit else 'NO ❌'}")
            print(f"       └─────────────────────────────────────────────")

            results.append({"pick": pick, "result": result, "flipped": flipped, "would_hit": would_hit})

    # Summary
    print(f"\n\n{'=' * 80}")
    print("BACKTEST SUMMARY")
    print(f"{'=' * 80}")
    flipped_count = sum(1 for r in results if r.get("flipped"))
    hit_count = sum(1 for r in results if r.get("would_hit"))
    error_count = sum(1 for r in results if "error" in r.get("result", {}))

    for r in results:
        p = r["pick"]
        status = "✅ WOULD HIT" if r.get("would_hit") else ("⚠️ FLIPPED but missed" if r.get("flipped") else "❌ SAME MISS")
        if "error" in r.get("result", {}):
            status = "⚠️ ERROR"
        new_rec = r.get("result", {}).get("recommendation", "?")
        new_proj = r.get("result", {}).get("projectedValue", "?")
        print(f"  {p['name']:15} | Old: {p['old_recommendation'].upper():5} → New: {new_rec.upper():5} | Proj: {new_proj:>6} vs Line: {p['line']:>5} | Actual: {p['actual_value']:>3} | {status}")

    print(f"\n  Flipped: {flipped_count}/4 | Would Hit: {hit_count}/4 | Errors: {error_count}/4")

    # Save results to file
    with open("/app/test_reports/backtest_missed_picks.json", "w") as f:
        sanitized = []
        for r in results:
            sanitized.append({
                "player": r["pick"]["name"],
                "old_rec": r["pick"]["old_recommendation"],
                "new_rec": r.get("result", {}).get("recommendation", "error"),
                "new_proj": r.get("result", {}).get("projectedValue"),
                "line": r["pick"]["line"],
                "actual": r["pick"]["actual_value"],
                "flipped": r.get("flipped", False),
                "would_hit": r.get("would_hit", False),
                "tempo": r.get("result", {}).get("tempoScaling"),
                "fav_dampening": r.get("result", {}).get("favoriteDampening"),
                "poss_scaling": r.get("result", {}).get("possessionScaling"),
                "match_dominance": r.get("result", {}).get("matchDominance"),
                "coin_flip": r.get("result", {}).get("coinFlip", False),
                "confidence": r.get("result", {}).get("confidenceScore"),
                "alerts": r.get("result", {}).get("tacticalAlerts", []),
            })
        json.dump(sanitized, f, indent=2)

    print(f"\n  Full results saved to /app/test_reports/backtest_missed_picks.json")


if __name__ == "__main__":
    asyncio.run(main())
