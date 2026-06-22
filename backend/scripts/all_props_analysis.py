"""
Deep analysis of ALL settled props — hit rates by direction, possession, venue,
scenario, confidence, league, and edge rating.
591 GK + all other prop types in Atlas.
"""
import asyncio, sys, os, statistics
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import mongo_client

DB_NAME = "reversepicks"
SETTLED = {"status": "settled", "result": {"$in": ["hit", "miss"]}}

# ── League ID → name lookup (pre-baked for speed) ──────────────────────────
LEAGUE_NAMES = {
    39: "EPL", 140: "La Liga", 307: "Saudi Pro", 1: "World Cup",
    253: "MLS", 135: "Serie A IT", 78: "Bundesliga", 61: "Ligue 1",
    2: "UCL", 254: "NWSL Women", 128: "Argentina Liga", 71: "Brazil SA",
    40: "Championship", 262: "Liga MX", 188: "A-League", 13: "CONMEBOL Lib",
    3: "UEFA EL", 667: "Friendlies", 61: "Ligue 1",
}

async def main():
    db = mongo_client[DB_NAME]

    # ── 1. Global prop census ─────────────────────────────────────────────────
    print("=" * 70)
    print("GLOBAL PROP CENSUS — SETTLED PICKS")
    print("=" * 70)
    async for doc in db.picks.aggregate([
        {"$match": SETTLED},
        {"$group": {
            "_id": {"prop": "$propType", "rec": "$recommendation"},
            "n": {"$sum": 1},
            "hits": {"$sum": {"$cond": [{"$eq": ["$result", "hit"]}, 1, 0]}},
            "avg_line": {"$avg": "$line"},
            "avg_actual": {"$avg": "$actualValue"},
        }},
        {"$sort": {"_id.prop": 1, "_id.rec": 1}},
    ]):
        n = doc["n"]; h = doc["hits"]
        p = doc["_id"]["prop"]; r = doc["_id"]["rec"]
        al = doc["avg_line"] or 0; aa = doc["avg_actual"] or 0
        bias = aa - al
        print(f"  {p:30s} {r:6s}  n={n:4d}  hit={h/n*100:5.1f}%  "
              f"avg_line={al:6.1f}  avg_actual={aa:6.1f}  bias={bias:+5.1f}")
    print()

    # ── 2. Soccer prop analysis by possession ────────────────────────────────
    SOCCER_PROPS = ["pass_attempts", "shots", "dribbles", "tackles", "clearances",
                    "key_passes", "crosses", "shots_on_target", "shots_assisted",
                    "fouls_committed", "soccer_fantasy_outfield", "soccer_fantasy_gk"]

    print("=" * 70)
    print("SOCCER PROPS — HIT RATE BY TEAM POSSESSION (actual match)")
    print("=" * 70)
    bands = [
        ("<35%",   0,  35),
        ("35-42%", 35, 42),
        ("42-48%", 42, 48),
        ("48-55%", 48, 55),
        ("55-62%", 55, 62),
        (">62%",   62, 100),
    ]
    for prop in SOCCER_PROPS:
        results = []
        async for doc in db.picks.aggregate([
            {"$match": {**SETTLED, "propType": prop,
                        "homePoss": {"$exists": True}, "awayPoss": {"$exists": True}}},
            {"$addFields": {
                "team_poss": {"$cond": [
                    {"$eq": ["$venue", "home"]},
                    {"$toDouble": "$homePoss"},
                    {"$toDouble": "$awayPoss"}
                ]},
                "is_hit": {"$cond": [{"$eq": ["$result", "hit"]}, 1, 0]},
            }},
            {"$addFields": {
                "poss_band": {"$switch": {"branches": [
                    {"case": {"$lt": ["$team_poss", 35]},  "then": "A_<35"},
                    {"case": {"$lt": ["$team_poss", 42]},  "then": "B_35-42"},
                    {"case": {"$lt": ["$team_poss", 48]},  "then": "C_42-48"},
                    {"case": {"$lt": ["$team_poss", 55]},  "then": "D_48-55"},
                    {"case": {"$lt": ["$team_poss", 62]},  "then": "E_55-62"},
                ], "default": "F_>62"}},
            }},
            {"$group": {
                "_id": {"band": "$poss_band", "rec": "$recommendation"},
                "n": {"$sum": 1},
                "hits": {"$sum": "$is_hit"},
            }},
            {"$sort": {"_id.band": 1, "_id.rec": 1}},
        ]):
            results.append(doc)
        if not results:
            continue
        total_n = sum(d["n"] for d in results)
        if total_n < 10:
            continue
        print(f"\n  ── {prop.upper()} (total with poss data: {total_n}) ──")
        for band_label, lo, hi in bands:
            band_key = {"<35%": "A_<35", "35-42%": "B_35-42", "42-48%": "C_42-48",
                        "48-55%": "D_48-55", "55-62%": "E_55-62", ">62%": "F_>62"}[band_label]
            over_d  = next((d for d in results if d["_id"]["band"] == band_key and d["_id"]["rec"] == "over"), None)
            under_d = next((d for d in results if d["_id"]["band"] == band_key and d["_id"]["rec"] == "under"), None)
            if not over_d and not under_d:
                continue
            over_s  = f"OVER={over_d['hits']/over_d['n']*100:4.0f}% ({over_d['n']}n)" if over_d else f"{'':20s}"
            under_s = f"UNDER={under_d['hits']/under_d['n']*100:4.0f}% ({under_d['n']}n)" if under_d else ""
            print(f"    poss {band_label:8s}  {over_s:22s}  {under_s}")
    print()

    # ── 3. Soccer props by VENUE ──────────────────────────────────────────────
    print("=" * 70)
    print("SOCCER PROPS — HIT RATE BY VENUE × RECOMMENDATION")
    print("=" * 70)
    async for doc in db.picks.aggregate([
        {"$match": {**SETTLED, "propType": {"$in": SOCCER_PROPS},
                    "venue": {"$in": ["home", "away", "neutral"]}}},
        {"$group": {
            "_id": {"prop": "$propType", "venue": "$venue", "rec": "$recommendation"},
            "n": {"$sum": 1},
            "hits": {"$sum": {"$cond": [{"$eq": ["$result", "hit"]}, 1, 0]}},
        }},
        {"$sort": {"_id.prop": 1, "_id.venue": 1, "_id.rec": 1}},
    ]):
        n = doc["n"]; h = doc["hits"]
        p = doc["_id"]["prop"]; v = doc["_id"]["venue"]; r = doc["_id"]["rec"]
        if n < 5: continue
        flag = "⭐" if h/n > 0.72 else ("❌" if h/n < 0.45 else "")
        print(f"  {p:30s} {v:8s} {r:6s}  n={n:3d}  hit={h/n*100:5.1f}% {flag}")
    print()

    # ── 4. Soccer props by SCENARIO BUCKET ────────────────────────────────────
    print("=" * 70)
    print("SOCCER PROPS — HIT RATE BY SCENARIO BUCKET")
    print("=" * 70)
    async for doc in db.picks.aggregate([
        {"$match": {**SETTLED, "propType": {"$in": SOCCER_PROPS},
                    "scenarioBucket": {"$exists": True, "$ne": None}}},
        {"$group": {
            "_id": {"prop": "$propType", "bucket": "$scenarioBucket", "rec": "$recommendation"},
            "n": {"$sum": 1},
            "hits": {"$sum": {"$cond": [{"$eq": ["$result", "hit"]}, 1, 0]}},
        }},
        {"$sort": {"_id.prop": 1, "_id.bucket": 1, "_id.rec": 1}},
    ]):
        n = doc["n"]; h = doc["hits"]
        p = doc["_id"]["prop"]; b = doc["_id"]["bucket"]; r = doc["_id"]["rec"]
        if n < 5: continue
        flag = "⭐" if h/n > 0.72 else ("❌" if h/n < 0.45 else "")
        print(f"  {p:30s} {b:20s} {r:6s}  n={n:3d}  hit={h/n*100:5.1f}% {flag}")
    print()

    # ── 5. ALL SPORTS — best/worst hitting prop × direction ──────────────────
    print("=" * 70)
    print("ALL SPORTS — OVER/UNDER HIT RATES RANKED (n≥20)")
    print("=" * 70)
    all_combos = []
    async for doc in db.picks.aggregate([
        {"$match": SETTLED},
        {"$group": {
            "_id": {"prop": "$propType", "rec": "$recommendation"},
            "n": {"$sum": 1},
            "hits": {"$sum": {"$cond": [{"$eq": ["$result", "hit"]}, 1, 0]}},
            "avg_line": {"$avg": "$line"},
            "avg_actual": {"$avg": "$actualValue"},
        }},
    ]):
        if doc["n"] >= 20:
            hr = doc["hits"] / doc["n"] * 100
            all_combos.append({
                "prop": doc["_id"]["prop"], "rec": doc["_id"]["rec"],
                "n": doc["n"], "hr": hr,
                "avg_line": doc["avg_line"] or 0, "avg_actual": doc["avg_actual"] or 0,
            })
    all_combos.sort(key=lambda x: -x["hr"])
    print("  TOP 20 HIGHEST HIT RATE (n≥20):")
    for c in all_combos[:20]:
        bias = c["avg_actual"] - c["avg_line"]
        flag = "⭐" if c["hr"] > 72 else ""
        print(f"  {flag} {c['prop']:30s} {c['rec']:6s}  n={c['n']:4d}  hit={c['hr']:5.1f}%  bias={bias:+5.1f}")
    print()
    print("  BOTTOM 20 LOWEST HIT RATE (n≥20):")
    for c in all_combos[-20:]:
        bias = c["avg_actual"] - c["avg_line"]
        flag = "❌" if c["hr"] < 45 else ""
        print(f"  {flag} {c['prop']:30s} {c['rec']:6s}  n={c['n']:4d}  hit={c['hr']:5.1f}%  bias={bias:+5.1f}")
    print()

    # ── 6. Confidence calibration for all props ───────────────────────────────
    print("=" * 70)
    print("CONFIDENCE CALIBRATION — OVER HIT RATE BY CONFIDENCE BAND (n≥15)")
    print("=" * 70)
    async for doc in db.picks.aggregate([
        {"$match": {**SETTLED, "recommendation": "over", "confidenceScore": {"$gt": 0}}},
        {"$addFields": {"cb": {"$switch": {"branches": [
            {"case": {"$lt": ["$confidenceScore", 55]}, "then": "1_<55"},
            {"case": {"$lt": ["$confidenceScore", 60]}, "then": "2_55-59"},
            {"case": {"$lt": ["$confidenceScore", 65]}, "then": "3_60-64"},
            {"case": {"$lt": ["$confidenceScore", 70]}, "then": "4_65-69"},
            {"case": {"$lt": ["$confidenceScore", 75]}, "then": "5_70-74"},
            {"case": {"$lt": ["$confidenceScore", 80]}, "then": "6_75-79"},
        ], "default": "7_80+"}}}},
        {"$group": {
            "_id": {"prop": "$propType", "cb": "$cb"},
            "n": {"$sum": 1},
            "hits": {"$sum": {"$cond": [{"$eq": ["$result", "hit"]}, 1, 0]}},
        }},
        {"$sort": {"_id.prop": 1, "_id.cb": 1}},
    ]):
        n = doc["n"]; h = doc["hits"]
        if n < 15: continue
        p = doc["_id"]["prop"]; cb = doc["_id"]["cb"]
        flag = "⭐" if h/n > 0.72 else ("❌" if h/n < 0.48 else "")
        print(f"  {p:30s} conf={cb:8s}  n={n:3d}  OVER hit={h/n*100:5.1f}% {flag}")
    print()

    print("=" * 70)
    print("CONFIDENCE CALIBRATION — UNDER HIT RATE BY CONFIDENCE BAND (n≥15)")
    print("=" * 70)
    async for doc in db.picks.aggregate([
        {"$match": {**SETTLED, "recommendation": "under", "confidenceScore": {"$gt": 0}}},
        {"$addFields": {"cb": {"$switch": {"branches": [
            {"case": {"$lt": ["$confidenceScore", 55]}, "then": "1_<55"},
            {"case": {"$lt": ["$confidenceScore", 60]}, "then": "2_55-59"},
            {"case": {"$lt": ["$confidenceScore", 65]}, "then": "3_60-64"},
            {"case": {"$lt": ["$confidenceScore", 70]}, "then": "4_65-69"},
            {"case": {"$lt": ["$confidenceScore", 75]}, "then": "5_70-74"},
            {"case": {"$lt": ["$confidenceScore", 80]}, "then": "6_75-79"},
        ], "default": "7_80+"}}}},
        {"$group": {
            "_id": {"prop": "$propType", "cb": "$cb"},
            "n": {"$sum": 1},
            "hits": {"$sum": {"$cond": [{"$eq": ["$result", "hit"]}, 1, 0]}},
        }},
        {"$sort": {"_id.prop": 1, "_id.cb": 1}},
    ]):
        n = doc["n"]; h = doc["hits"]
        if n < 15: continue
        p = doc["_id"]["prop"]; cb = doc["_id"]["cb"]
        flag = "⭐" if h/n > 0.72 else ("❌" if h/n < 0.48 else "")
        print(f"  {p:30s} conf={cb:8s}  n={n:3d}  UNDER hit={h/n*100:5.1f}% {flag}")
    print()

    # ── 7. Safety / edge rating for all props ─────────────────────────────────
    print("=" * 70)
    print("SAFETY + EDGE RATING — HIT RATES BY PROP (n≥10)")
    print("=" * 70)
    async for doc in db.picks.aggregate([
        {"$match": {**SETTLED, "safetyRating": {"$exists": True, "$ne": None}}},
        {"$group": {
            "_id": {"prop": "$propType", "safety": "$safetyRating", "rec": "$recommendation"},
            "n": {"$sum": 1},
            "hits": {"$sum": {"$cond": [{"$eq": ["$result", "hit"]}, 1, 0]}},
        }},
        {"$sort": {"_id.prop": 1, "_id.safety": 1, "_id.rec": 1}},
    ]):
        n = doc["n"]; h = doc["hits"]
        if n < 10: continue
        p = doc["_id"]["prop"]; s = doc["_id"]["safety"]; r = doc["_id"]["rec"]
        flag = "⭐" if h/n > 0.72 else ("❌" if h/n < 0.48 else "")
        print(f"  {p:30s} {s:12s} {r:6s}  n={n:3d}  hit={h/n*100:5.1f}% {flag}")
    print()

    # ── 8. Position × prop deep analysis ─────────────────────────────────────
    print("=" * 70)
    print("POSITION × PROP COMBINATION — OVER HIT RATES (n≥15)")
    print("=" * 70)
    pos_combos = []
    async for doc in db.picks.aggregate([
        {"$match": {**SETTLED, "recommendation": "over",
                    "position": {"$exists": True, "$ne": None}}},
        {"$group": {
            "_id": {"prop": "$propType", "pos": "$position"},
            "n": {"$sum": 1},
            "hits": {"$sum": {"$cond": [{"$eq": ["$result", "hit"]}, 1, 0]}},
        }},
    ]):
        if doc["n"] >= 15:
            hr = doc["hits"] / doc["n"] * 100
            pos_combos.append({
                "prop": doc["_id"]["prop"], "pos": doc["_id"]["pos"],
                "n": doc["n"], "hr": hr,
            })
    pos_combos.sort(key=lambda x: -x["hr"])
    print("  TOP 25 OVER combos (pos × prop, n≥15):")
    for c in pos_combos[:25]:
        flag = "⭐" if c["hr"] > 72 else ""
        print(f"  {flag} {c['prop']:30s} pos={c['pos']:6s}  n={c['n']:3d}  OVER={c['hr']:5.1f}%")
    print()
    print("  BOTTOM 15 OVER combos (pos × prop, n≥15) — fading signals:")
    for c in pos_combos[-15:]:
        flag = "❌" if c["hr"] < 45 else ""
        print(f"  {flag} {c['prop']:30s} pos={c['pos']:6s}  n={c['n']:3d}  OVER={c['hr']:5.1f}%")
    print()

    print("=" * 70)
    print("POSITION × PROP COMBINATION — UNDER HIT RATES (n≥15)")
    print("=" * 70)
    pos_under = []
    async for doc in db.picks.aggregate([
        {"$match": {**SETTLED, "recommendation": "under",
                    "position": {"$exists": True, "$ne": None}}},
        {"$group": {
            "_id": {"prop": "$propType", "pos": "$position"},
            "n": {"$sum": 1},
            "hits": {"$sum": {"$cond": [{"$eq": ["$result", "hit"]}, 1, 0]}},
        }},
    ]):
        if doc["n"] >= 15:
            hr = doc["hits"] / doc["n"] * 100
            pos_under.append({
                "prop": doc["_id"]["prop"], "pos": doc["_id"]["pos"],
                "n": doc["n"], "hr": hr,
            })
    pos_under.sort(key=lambda x: -x["hr"])
    print("  TOP 25 UNDER combos:")
    for c in pos_under[:25]:
        flag = "⭐" if c["hr"] > 72 else ""
        print(f"  {flag} {c['prop']:30s} pos={c['pos']:6s}  n={c['n']:3d}  UNDER={c['hr']:5.1f}%")
    print()

    # ── 9. CS2 kills analysis ─────────────────────────────────────────────────
    print("=" * 70)
    print("CS2 KILLS — DEEP DIVE (maps_1_2_kills / map1_kills)")
    print("=" * 70)
    for prop in ["maps_1_2_kills", "map1_kills", "maps_1_3_kills", "maps_1_2_headshots"]:
        docs = []
        async for doc in db.picks.aggregate([
            {"$match": {**SETTLED, "propType": prop}},
            {"$group": {
                "_id": {"rec": "$recommendation", "pos": {"$ifNull": ["$position", "?"]}},
                "n": {"$sum": 1},
                "hits": {"$sum": {"$cond": [{"$eq": ["$result", "hit"]}, 1, 0]}},
                "avg_line": {"$avg": "$line"},
                "avg_actual": {"$avg": "$actualValue"},
            }},
            {"$sort": {"_id.rec": 1, "_id.pos": 1}},
        ]):
            docs.append(doc)
        if not docs: continue
        total = sum(d["n"] for d in docs)
        print(f"\n  {prop.upper()} (total: {total})")
        for doc in docs:
            n = doc["n"]; h = doc["hits"]
            r = doc["_id"]["rec"]; p = doc["_id"]["pos"]
            al = doc["avg_line"] or 0; aa = doc["avg_actual"] or 0
            flag = "⭐" if h/n > 0.72 else ("❌" if h/n < 0.45 else "")
            print(f"    {r:6s} pos={p:8s}  n={n:3d}  hit={h/n*100:5.1f}%  "
                  f"avg_line={al:5.1f}  avg_actual={aa:5.1f}  bias={aa-al:+5.1f} {flag}")
    print()

    # ── 10. MLB/Baseball props ────────────────────────────────────────────────
    MLB_PROPS = ["pitcher_strikeouts", "hitter_fantasy_points", "hits",
                 "runs", "hits_runs_rbis", "total_bases", "earned_runs",
                 "hits_allowed", "walks_allowed", "pitches_thrown",
                 "pitcher_fantasy_score", "home_runs", "walks", "plate_appearances",
                 "innings_pitched", "strikeouts"]
    print("=" * 70)
    print("BASEBALL PROPS — FULL BREAKDOWN (n≥5)")
    print("=" * 70)
    async for doc in db.picks.aggregate([
        {"$match": {**SETTLED, "propType": {"$in": MLB_PROPS}}},
        {"$group": {
            "_id": {"prop": "$propType", "rec": "$recommendation",
                    "pos": {"$ifNull": ["$position", "?"]}},
            "n": {"$sum": 1},
            "hits": {"$sum": {"$cond": [{"$eq": ["$result", "hit"]}, 1, 0]}},
            "avg_line": {"$avg": "$line"},
            "avg_actual": {"$avg": "$actualValue"},
        }},
        {"$sort": {"_id.prop": 1, "_id.rec": 1}},
    ]):
        n = doc["n"]; h = doc["hits"]
        if n < 5: continue
        p = doc["_id"]["prop"]; r = doc["_id"]["rec"]; pos = doc["_id"]["pos"]
        al = doc["avg_line"] or 0; aa = doc["avg_actual"] or 0
        flag = "⭐" if h/n > 0.72 else ("❌" if h/n < 0.45 else "")
        print(f"  {p:30s} {r:6s} pos={pos:5s}  n={n:3d}  hit={h/n*100:5.1f}%  "
              f"avg_line={al:6.2f}  avg_actual={aa:6.2f}  bias={aa-al:+5.2f} {flag}")
    print()

    # ── 11. Tennis / WTA / ATP ────────────────────────────────────────────────
    TENNIS_PROPS = ["total_games", "player_games_won", "set_1_player_games"]
    print("=" * 70)
    print("TENNIS PROPS (total_games / player_games_won) — BREAKDOWN")
    print("=" * 70)
    async for doc in db.picks.aggregate([
        {"$match": {**SETTLED, "propType": {"$in": TENNIS_PROPS}}},
        {"$group": {
            "_id": {"prop": "$propType", "rec": "$recommendation"},
            "n": {"$sum": 1},
            "hits": {"$sum": {"$cond": [{"$eq": ["$result", "hit"]}, 1, 0]}},
            "avg_line": {"$avg": "$line"},
            "avg_actual": {"$avg": "$actualValue"},
        }},
        {"$sort": {"_id.prop": 1, "_id.rec": 1}},
    ]):
        n = doc["n"]; h = doc["hits"]
        if n < 3: continue
        p = doc["_id"]["prop"]; r = doc["_id"]["rec"]
        al = doc["avg_line"] or 0; aa = doc["avg_actual"] or 0
        flag = "⭐" if h/n > 0.72 else ("❌" if h/n < 0.45 else "")
        print(f"  {p:30s} {r:6s}  n={n:3d}  hit={h/n*100:5.1f}%  bias={aa-al:+5.1f} {flag}")
    print()

    # ── 12. Saves (hockey/soccer GK) ─────────────────────────────────────────
    print("=" * 70)
    print("SAVES — BREAKDOWN BY POSITION × REC (n≥5)")
    print("=" * 70)
    async for doc in db.picks.aggregate([
        {"$match": {**SETTLED, "propType": "saves"}},
        {"$group": {
            "_id": {"rec": "$recommendation", "pos": {"$ifNull": ["$position", "?"]},
                    "venue": {"$ifNull": ["$venue", "?"]}},
            "n": {"$sum": 1},
            "hits": {"$sum": {"$cond": [{"$eq": ["$result", "hit"]}, 1, 0]}},
            "avg_line": {"$avg": "$line"},
            "avg_actual": {"$avg": "$actualValue"},
        }},
        {"$sort": {"_id.rec": 1, "_id.pos": 1}},
    ]):
        n = doc["n"]; h = doc["hits"]
        if n < 5: continue
        r = doc["_id"]["rec"]; p = doc["_id"]["pos"]; v = doc["_id"]["venue"]
        al = doc["avg_line"] or 0; aa = doc["avg_actual"] or 0
        flag = "⭐" if h/n > 0.72 else ("❌" if h/n < 0.45 else "")
        print(f"  saves {r:6s} pos={p:6s} venue={v:8s}  n={n:3d}  hit={h/n*100:5.1f}%  "
              f"avg_line={al:.1f}  avg_actual={aa:.1f}  bias={aa-al:+.1f} {flag}")
    print()

    # ── 13. Cross-sport OVERALL summary with line bias ────────────────────────
    print("=" * 70)
    print("LINE BIAS ANALYSIS — WHERE ARE LINES SET WRONG? (avg actual vs line, n≥20)")
    print("=" * 70)
    print("  (positive bias = actual avg HIGHER than line = OVER value. "
          "negative = UNDER value)")
    print()
    biases = []
    async for doc in db.picks.aggregate([
        {"$match": {**SETTLED, "actualValue": {"$exists": True},
                    "line": {"$exists": True}}},
        {"$group": {
            "_id": {"prop": "$propType", "rec": "$recommendation"},
            "n": {"$sum": 1},
            "avg_line": {"$avg": "$line"},
            "avg_actual": {"$avg": "$actualValue"},
        }},
    ]):
        if doc["n"] >= 20 and doc["avg_actual"] and doc["avg_line"]:
            bias = doc["avg_actual"] - doc["avg_line"]
            biases.append({
                "prop": doc["_id"]["prop"], "rec": doc["_id"]["rec"],
                "n": doc["n"], "bias": bias,
                "avg_line": doc["avg_line"], "avg_actual": doc["avg_actual"],
            })
    biases.sort(key=lambda x: -abs(x["bias"]))
    print("  Biggest mispricings (both over and under — actual far from line):")
    for b in biases[:25]:
        arrow = "↑ OVER VALUE" if b["bias"] > 0 else "↓ UNDER VALUE"
        print(f"  {b['prop']:30s} {b['rec']:6s}  n={b['n']:4d}  "
              f"avg_line={b['avg_line']:7.1f}  avg_actual={b['avg_actual']:7.1f}  "
              f"bias={b['bias']:+6.1f}  {arrow}")
    print()

asyncio.run(main())
