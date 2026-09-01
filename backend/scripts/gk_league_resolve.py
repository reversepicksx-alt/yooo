"""Resolve league IDs and get extra GK stats for cheat sheet"""
import asyncio, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import mongo_client
from collections import defaultdict

SETTLED = {"status": "settled", "result": {"$in": ["hit", "miss"]}}

async def main():
    db = mongo_client["reversepicks"]

    # Resolve league IDs → names
    league_ids = [667, 140, 307, 39, 1, 253, 135, 78, 61, 2, 254, 128, 71, 40, 262, 188, 13, 3]
    print("=== LEAGUE ID → NAME MAP ===")
    for lid in league_ids:
        doc = await db.cache_leagues.find_one({"id": lid})
        if not doc:
            doc = await db.cache_leagues.find_one({"leagueId": lid})
        name = (doc or {}).get("name") or (doc or {}).get("league", {}).get("name") if doc else None
        country = (doc or {}).get("country") or (doc or {}).get("country", {}).get("name") if doc else None
        print(f"  {lid}: {name or '?'} | {country or '?'}")
    print()

    # Per-player GK breakdown (top GKs with most picks)
    print("=== TOP GKs BY PICK COUNT ===")
    pipeline = [
        {"$match": {**SETTLED, "propType": "pass_attempts", "position": {"$in": ["GK","GKP","Goalkeeper"]}}},
        {"$group": {
            "_id": "$playerName",
            "count": {"$sum": 1},
            "hits": {"$sum": {"$cond": [{"$eq": ["$result", "hit"]}, 1, 0]}},
            "over_count": {"$sum": {"$cond": [{"$eq": ["$recommendation", "over"]}, 1, 0]}},
            "over_hits": {"$sum": {"$cond": [{"$and": [{"$eq": ["$result", "hit"]},
                                                        {"$eq": ["$recommendation", "over"]}]}, 1, 0]}},
            "avg_line": {"$avg": "$line"},
            "avg_actual": {"$avg": "$actualValue"},
            "avg_poss": {"$avg": "$homePoss"},  # rough
        }},
        {"$sort": {"count": -1}},
        {"$limit": 25},
    ]
    async for doc in db.picks.aggregate(pipeline):
        n = doc["count"]; hits = doc["hits"]
        ov = doc["over_count"]; oh = doc["over_hits"]
        un = n - ov; uh = hits - oh
        over_s = f"OVER={oh/ov*100:.0f}% ({ov}n)" if ov else ""
        under_s = f"UNDER={uh/un*100:.0f}% ({un}n)" if un else ""
        print(f"  {doc['_id']:25s}  n={n:3d}  hit={hits/n*100:5.1f}%  "
              f"avg_line={doc['avg_line'] or 0:.1f}  avg_actual={doc['avg_actual'] or 0:.1f}  "
              f"{over_s}  {under_s}")
    print()

    # Possession cross-tab: team_poss quartile × venue × recommendation
    print("=== GK OVER HIT RATE: POSS < 40% vs > 55% BREAKDOWN BY VENUE ===")
    async for p in db.picks.find({**SETTLED, "propType": "pass_attempts",
                                   "position": {"$in": ["GK","GKP","Goalkeeper"]},
                                   "homePoss": {"$exists": True},
                                   "awayPoss": {"$exists": True}}):
        pass  # aggregate below

    pipeline2 = [
        {"$match": {**SETTLED, "propType": "pass_attempts",
                    "position": {"$in": ["GK","GKP","Goalkeeper"]},
                    "homePoss": {"$exists": True}, "awayPoss": {"$exists": True}}},
        {"$addFields": {
            "team_poss": {"$cond": [{"$eq": ["$venue", "home"]}, {"$toDouble": "$homePoss"}, {"$toDouble": "$awayPoss"}]},
            "is_hit": {"$cond": [{"$eq": ["$result", "hit"]}, 1, 0]},
        }},
        {"$addFields": {
            "poss_band": {"$switch": {"branches": [
                {"case": {"$lt": ["$team_poss", 35]}, "then": "A_<35%"},
                {"case": {"$lt": ["$team_poss", 42]}, "then": "B_35-42%"},
                {"case": {"$lt": ["$team_poss", 48]}, "then": "C_42-48%"},
                {"case": {"$lt": ["$team_poss", 55]}, "then": "D_48-55%"},
                {"case": {"$lt": ["$team_poss", 62]}, "then": "E_55-62%"},
            ], "default": "F_>62%"}},
        }},
        {"$group": {
            "_id": {"band": "$poss_band", "rec": "$recommendation", "venue": "$venue"},
            "n": {"$sum": 1},
            "hits": {"$sum": "$is_hit"},
            "avg_poss": {"$avg": "$team_poss"},
        }},
        {"$sort": {"_id.band": 1, "_id.rec": 1, "_id.venue": 1}},
    ]
    results = defaultdict(lambda: defaultdict(dict))
    async for doc in db.picks.aggregate(pipeline2):
        band = doc["_id"]["band"]
        rec  = doc["_id"]["rec"]
        venue = doc["_id"]["venue"]
        n = doc["n"]; hits = doc["hits"]
        results[band][rec][venue] = {"n": n, "hr": hits/n*100, "avg_poss": doc["avg_poss"]}

    for band in sorted(results.keys()):
        print(f"\n  POSS BAND: {band}")
        for rec in ["over", "under"]:
            if rec not in results[band]: continue
            for venue, stats in sorted(results[band][rec].items()):
                n = stats["n"]; hr = stats["hr"]; ap = stats["avg_poss"]
                print(f"    {rec.upper():6s} @ {venue:8s}: n={n:3d}  hit={hr:5.1f}%  avg_poss={ap:.1f}%")

    print()

    # Safety rating breakdown
    print("=== GK PASS_ATTEMPTS BY SAFETY RATING ===")
    pipeline3 = [
        {"$match": {**SETTLED, "propType": "pass_attempts",
                    "position": {"$in": ["GK","GKP","Goalkeeper"]}}},
        {"$group": {
            "_id": {"safety": "$safetyRating", "rec": "$recommendation"},
            "n": {"$sum": 1},
            "hits": {"$sum": {"$cond": [{"$eq": ["$result", "hit"]}, 1, 0]}},
        }},
        {"$sort": {"_id.safety": 1, "_id.rec": 1}},
    ]
    async for doc in db.picks.aggregate(pipeline3):
        n = doc["n"]; hits = doc["hits"]
        print(f"  {doc['_id']['safety']:20s} {doc['_id']['rec']:6s}  n={n:3d}  hit={hits/n*100:.1f}%")
    print()

    # Scenario bucket breakdown
    print("=== GK PASS_ATTEMPTS BY SCENARIO BUCKET ===")
    pipeline4 = [
        {"$match": {**SETTLED, "propType": "pass_attempts",
                    "position": {"$in": ["GK","GKP","Goalkeeper"]}}},
        {"$group": {
            "_id": {"bucket": "$scenarioBucket", "rec": "$recommendation"},
            "n": {"$sum": 1},
            "hits": {"$sum": {"$cond": [{"$eq": ["$result", "hit"]}, 1, 0]}},
        }},
        {"$sort": {"_id.bucket": 1, "_id.rec": 1}},
    ]
    async for doc in db.picks.aggregate(pipeline4):
        n = doc["n"]; hits = doc["hits"]
        print(f"  {str(doc['_id']['bucket']):20s} {doc['_id']['rec']:6s}  n={n:3d}  hit={hits/n*100:.1f}%")
    print()

    # Confidence band
    print("=== GK PASS_ATTEMPTS OVER HIT RATE BY CONFIDENCE BAND ===")
    pipeline5 = [
        {"$match": {**SETTLED, "propType": "pass_attempts",
                    "position": {"$in": ["GK","GKP","Goalkeeper"]},
                    "recommendation": "over"}},
        {"$addFields": {
            "conf_band": {"$switch": {"branches": [
                {"case": {"$lt": ["$confidenceScore", 55]}, "then": "45-54%"},
                {"case": {"$lt": ["$confidenceScore", 60]}, "then": "55-59%"},
                {"case": {"$lt": ["$confidenceScore", 65]}, "then": "60-64%"},
                {"case": {"$lt": ["$confidenceScore", 70]}, "then": "65-69%"},
                {"case": {"$lt": ["$confidenceScore", 75]}, "then": "70-74%"},
                {"case": {"$lt": ["$confidenceScore", 80]}, "then": "75-79%"},
            ], "default": "80%+"}},
        }},
        {"$group": {
            "_id": "$conf_band",
            "n": {"$sum": 1},
            "hits": {"$sum": {"$cond": [{"$eq": ["$result", "hit"]}, 1, 0]}},
        }},
        {"$sort": {"_id": 1}},
    ]
    async for doc in db.picks.aggregate(pipeline5):
        n = doc["n"]; hits = doc["hits"]
        print(f"  conf {doc['_id']:8s}  n={n:3d}  OVER hit={hits/n*100:.1f}%")

asyncio.run(main())
