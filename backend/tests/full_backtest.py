"""
FULL BACKTEST: All picks from user's 3 screenshots
Tests the Bayesian-First Fusion v3 engine against real results.
"""
import asyncio
import httpx
import json
import os

API_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://props-ai-predict.preview.emergentagent.com")

ALL_PICKS = [
    # ═══ MISSES (should ideally flip or flag as coin flip) ═══
    {"name": "G. Simeone", "playerId": 323935, "teamId": 530, "teamName": "Atletico Madrid", "opponentId": 529, "opponentName": "Barcelona", "leagueId": 140, "propType": "pass_attempts", "line": 27.5, "venue": "home", "old_rec": "over", "actual": 18, "old_result": "miss"},
    {"name": "Leo Román", "playerId": 179139, "teamId": 798, "teamName": "Mallorca", "opponentId": 541, "opponentName": "Real Madrid", "leagueId": 140, "propType": "saves", "line": 3.5, "venue": "home", "old_rec": "under", "actual": 5, "old_result": "miss"},
    {"name": "G. Mamardashvili", "playerId": 24760, "teamId": 40, "teamName": "Liverpool", "opponentId": 50, "opponentName": "Manchester City", "leagueId": 39, "propType": "saves", "line": 3.5, "venue": "away", "old_rec": "over", "actual": 3, "old_result": "miss"},
    {"name": "T. Iwata", "playerId": 32938, "teamId": 54, "teamName": "Birmingham", "opponentId": 67, "opponentName": "Blackburn", "leagueId": 40, "propType": "pass_attempts", "line": 47.5, "venue": "home", "old_rec": "over", "actual": 44, "old_result": "miss"},
    {"name": "H. Darling", "playerId": 17943, "teamId": 71, "teamName": "Norwich", "opponentId": 1355, "opponentName": "Portsmouth", "leagueId": 40, "propType": "pass_attempts", "line": 55.5, "venue": "home", "old_rec": "over", "actual": 42, "old_result": "miss"},
    {"name": "Luíz Júnior", "playerId": 278619, "teamId": 533, "teamName": "Villarreal", "opponentId": 547, "opponentName": "Girona", "leagueId": 140, "propType": "saves", "line": 2.5, "venue": "away", "old_rec": "over", "actual": 2, "old_result": "miss"},
    {"name": "C. Rushworth", "playerId": 278088, "teamId": 1346, "teamName": "Coventry", "opponentId": 64, "opponentName": "Hull City", "leagueId": 40, "propType": "saves", "line": 2.5, "venue": "away", "old_rec": "under", "actual": 4, "old_result": "miss"},
    {"name": "S. Pierotti", "playerId": 6662, "teamId": 867, "teamName": "Lecce", "opponentId": 499, "opponentName": "Atalanta", "leagueId": 135, "propType": "pass_attempts", "line": 17.5, "venue": "home", "old_rec": "under", "actual": 26, "old_result": "miss"},
    {"name": "M. Carnesecchi", "playerId": 30417, "teamId": 499, "teamName": "Atalanta", "opponentId": 867, "opponentName": "Lecce", "leagueId": 135, "propType": "saves", "line": 2.0, "venue": "away", "old_rec": "over", "actual": 0, "old_result": "miss"},
    # ═══ HITS (should still predict correctly — regression check) ═══
    {"name": "D. Blind", "playerId": 531, "teamId": 547, "teamName": "Girona", "opponentId": 533, "opponentName": "Villarreal", "leagueId": 140, "propType": "pass_attempts", "line": 82.5, "venue": "home", "old_rec": "under", "actual": 64, "old_result": "hit"},
    {"name": "P. Gazzaniga", "playerId": 158, "teamId": 547, "teamName": "Girona", "opponentId": 533, "opponentName": "Villarreal", "leagueId": 140, "propType": "pass_attempts", "line": 33.5, "venue": "home", "old_rec": "under", "actual": 25, "old_result": "hit"},
    {"name": "Luíz Júnior", "playerId": 278619, "teamId": 533, "teamName": "Villarreal", "opponentId": 547, "opponentName": "Girona", "leagueId": 140, "propType": "pass_attempts", "line": 21.5, "venue": "away", "old_rec": "over", "actual": 24, "old_result": "hit"},
    {"name": "N. Pépé", "playerId": 3246, "teamId": 533, "teamName": "Villarreal", "opponentId": 547, "opponentName": "Girona", "leagueId": 140, "propType": "pass_attempts", "line": 27.5, "venue": "away", "old_rec": "under", "actual": 23, "old_result": "hit"},
    {"name": "L. Kitching", "playerId": 18202, "teamId": 1346, "teamName": "Coventry", "opponentId": 64, "opponentName": "Hull City", "leagueId": 40, "propType": "pass_attempts", "line": 59.5, "venue": "away", "old_rec": "under", "actual": 58, "old_result": "hit"},
]


async def run_prediction(client, pick, idx, total):
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
        resp = await client.post(f"{API_URL}/api/predict", json=payload, timeout=55)
        if resp.status_code == 200:
            return resp.json()
        return {"error": f"HTTP {resp.status_code}"}
    except Exception as e:
        return {"error": str(e)}


async def main():
    print("=" * 85)
    print("  FULL BACKTEST — Bayesian-First Fusion v3 vs Real Results")
    print("  14 picks: 9 misses + 5 hits from user's 3 screenshots")
    print("=" * 85)

    results = []
    async with httpx.AsyncClient() as client:
        for i, pick in enumerate(ALL_PICKS):
            print(f"\n[{i+1}/14] {pick['name']} ({pick['teamName']} {pick['venue'].upper()}) | {pick['propType']} {pick['old_rec'].upper()} {pick['line']} | Actual: {pick['actual']} | Old: {pick['old_result'].upper()}")

            result = await run_prediction(client, pick, i, len(ALL_PICKS))

            if "error" in result:
                print(f"  ERROR: {result['error'][:80]}")
                results.append({**pick, "new_rec": "error", "new_proj": None, "would_hit": False, "flipped": False, "coin_flip": False, "error": True})
                continue

            new_rec = result.get("recommendation", "?")
            new_proj = result.get("projectedValue", "?")
            coin = result.get("coinFlip", False)
            fus = result.get("fusionApplied", {})
            divergence = fus.get("divergencePct", 0)
            weights = fus.get("weights", {})

            actual = pick["actual"]
            line = pick["line"]
            would_hit = (new_rec == "over" and actual > line) or (new_rec == "under" and actual < line)
            flipped = new_rec != pick["old_rec"]

            # Status indicator
            if pick["old_result"] == "miss":
                if would_hit:
                    status = "FIXED ✅"
                elif coin:
                    status = "COIN FLIP ⚠️"
                else:
                    status = "STILL MISS ❌"
            else:  # was a hit
                if would_hit:
                    status = "STILL HIT ✅"
                else:
                    status = "REGRESSION ❌"

            bayes_info = ""
            if fus:
                bayes_info = f" | Fusion: AI={fus.get('aiProjection')}({fus.get('aiRecommendation')}) Bayes={fus.get('bayesianPosterior')}({fus.get('bayesianRecommendation')}) → {fus.get('fusedProjection')} | Div={divergence:.0f}% | W={weights}"

            print(f"  → {new_rec.upper()} {line} (proj={new_proj}) {'COIN FLIP' if coin else ''}{bayes_info}")
            print(f"  → {status}")

            results.append({
                **pick,
                "new_rec": new_rec,
                "new_proj": new_proj,
                "would_hit": would_hit,
                "flipped": flipped,
                "coin_flip": coin,
                "error": False,
                "divergence": divergence,
                "weights": weights,
                "status": status,
            })

    # ═══ SUMMARY ═══
    print(f"\n\n{'=' * 85}")
    print("  BACKTEST RESULTS SUMMARY")
    print(f"{'=' * 85}")

    old_misses = [r for r in results if r["old_result"] == "miss" and not r["error"]]
    old_hits = [r for r in results if r["old_result"] == "hit" and not r["error"]]
    errors = [r for r in results if r["error"]]

    print(f"\n  PREVIOUSLY MISSED ({len(old_misses)} picks):")
    fixed = sum(1 for r in old_misses if r["would_hit"])
    coin_flips = sum(1 for r in old_misses if r["coin_flip"] and not r["would_hit"])
    still_miss = sum(1 for r in old_misses if not r["would_hit"] and not r["coin_flip"])
    for r in old_misses:
        print(f"    {r['name']:18} | Old: {r['old_rec'].upper():5} → New: {r['new_rec'].upper():5} | Proj: {str(r['new_proj']):>5} vs Line: {r['line']:>5} | Actual: {r['actual']:>3} | {r['status']}")
    print(f"    Fixed: {fixed} | Coin Flip: {coin_flips} | Still Miss: {still_miss}")

    print(f"\n  PREVIOUSLY HIT ({len(old_hits)} picks — regression check):")
    regressions = sum(1 for r in old_hits if not r["would_hit"])
    still_hit = sum(1 for r in old_hits if r["would_hit"])
    for r in old_hits:
        print(f"    {r['name']:18} | {r['new_rec'].upper():5} | Proj: {str(r['new_proj']):>5} vs Line: {r['line']:>5} | Actual: {r['actual']:>3} | {r['status']}")
    print(f"    Still Hit: {still_hit} | Regressions: {regressions}")

    if errors:
        print(f"\n  ERRORS ({len(errors)} picks — API timeout/failure):")
        for r in errors:
            print(f"    {r['name']:18} | {r['propType']} {r['old_rec'].upper()} {r['line']}")

    # Overall accuracy
    all_valid = [r for r in results if not r["error"]]
    new_correct = sum(1 for r in all_valid if r["would_hit"])
    old_correct = sum(1 for r in all_valid if r["old_result"] == "hit")
    total = len(all_valid)

    print(f"\n  {'─' * 50}")
    print(f"  OLD ENGINE: {old_correct}/{total} correct ({old_correct/total*100:.0f}%)")
    print(f"  NEW ENGINE: {new_correct}/{total} correct ({new_correct/total*100:.0f}%)")
    improvement = new_correct - old_correct
    print(f"  IMPROVEMENT: +{improvement} picks fixed ({improvement/max(total,1)*100:.0f}% improvement)")
    print(f"  REGRESSIONS: {regressions}")
    print(f"  {'─' * 50}")

    # Save
    with open("/app/test_reports/full_backtest.json", "w") as f:
        json.dump({
            "summary": {
                "total_picks": total,
                "old_correct": old_correct,
                "new_correct": new_correct,
                "improvement": improvement,
                "regressions": regressions,
                "errors": len(errors),
            },
            "results": [{k: v for k, v in r.items() if k != "error"} for r in results],
        }, f, indent=2, default=str)

    print(f"\n  Full results: /app/test_reports/full_backtest.json")

if __name__ == "__main__":
    asyncio.run(main())
