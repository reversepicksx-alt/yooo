"""Get safety rating, scenario bucket, and confidence breakdown for GK picks"""
import asyncio, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import mongo_client

SETTLED = {"status": "settled", "result": {"$in": ["hit", "miss"]}}
GK_FILTER = {**SETTLED, "propType": "pass_attempts", "position": {"$in": ["GK","GKP","Goalkeeper"]}}

async def main():
    db = mongo_client["reversepicks"]

    print("=== GK PASS_ATTEMPTS BY SAFETY RATING × REC ===")
    async for doc in db.picks.aggregate([
        {"$match": GK_FILTER},
        {"$group": {
            "_id": {"safety": {"$ifNull": ["$safetyRating", "N/A"]}, "rec": "$recommendation"},
            "n": {"$sum": 1},
            "hits": {"$sum": {"$cond": [{"$eq": ["$result", "hit"]}, 1, 0]}},
        }},
        {"$sort": {"_id.safety": 1, "_id.rec": 1}},
    ]):
        n = doc["n"]; h = doc["hits"]
        s = doc["_id"]["safety"]; r = doc["_id"]["rec"]
        print(f"  {s:20s} {r:6s}  n={n:3d}  hit={h/n*100:.1f}%")
    print()

    print("=== GK PASS_ATTEMPTS BY SCENARIO BUCKET × REC ===")
    async for doc in db.picks.aggregate([
        {"$match": GK_FILTER},
        {"$group": {
            "_id": {"bucket": {"$ifNull": ["$scenarioBucket", "none"]}, "rec": "$recommendation"},
            "n": {"$sum": 1},
            "hits": {"$sum": {"$cond": [{"$eq": ["$result", "hit"]}, 1, 0]}},
        }},
        {"$sort": {"_id.bucket": 1, "_id.rec": 1}},
    ]):
        n = doc["n"]; h = doc["hits"]
        b = doc["_id"]["bucket"]; r = doc["_id"]["rec"]
        print(f"  {b:22s} {r:6s}  n={n:3d}  hit={h/n*100:.1f}%")
    print()

    print("=== GK OVER HIT RATE BY CONFIDENCE BAND ===")
    async for doc in db.picks.aggregate([
        {"$match": {**GK_FILTER, "recommendation": "over"}},
        {"$addFields": {"cb": {"$switch": {"branches": [
            {"case": {"$lt": ["$confidenceScore", 55]}, "then": "1_<55"},
            {"case": {"$lt": ["$confidenceScore", 60]}, "then": "2_55-59"},
            {"case": {"$lt": ["$confidenceScore", 65]}, "then": "3_60-64"},
            {"case": {"$lt": ["$confidenceScore", 70]}, "then": "4_65-69"},
            {"case": {"$lt": ["$confidenceScore", 75]}, "then": "5_70-74"},
            {"case": {"$lt": ["$confidenceScore", 80]}, "then": "6_75-79"},
        ], "default": "7_80+"}}}},
        {"$group": {"_id": "$cb", "n": {"$sum": 1},
                    "hits": {"$sum": {"$cond": [{"$eq": ["$result", "hit"]}, 1, 0]}}}},
        {"$sort": {"_id": 1}},
    ]):
        n = doc["n"]; h = doc["hits"]
        print(f"  conf {doc['_id']:8s}  n={n:3d}  OVER hit={h/n*100:.1f}%")
    print()

    print("=== GK UNDER HIT RATE BY CONFIDENCE BAND ===")
    async for doc in db.picks.aggregate([
        {"$match": {**GK_FILTER, "recommendation": "under"}},
        {"$addFields": {"cb": {"$switch": {"branches": [
            {"case": {"$lt": ["$confidenceScore", 55]}, "then": "1_<55"},
            {"case": {"$lt": ["$confidenceScore", 60]}, "then": "2_55-59"},
            {"case": {"$lt": ["$confidenceScore", 65]}, "then": "3_60-64"},
            {"case": {"$lt": ["$confidenceScore", 70]}, "then": "4_65-69"},
            {"case": {"$lt": ["$confidenceScore", 75]}, "then": "5_70-74"},
            {"case": {"$lt": ["$confidenceScore", 80]}, "then": "6_75-79"},
        ], "default": "7_80+"}}}},
        {"$group": {"_id": "$cb", "n": {"$sum": 1},
                    "hits": {"$sum": {"$cond": [{"$eq": ["$result", "hit"]}, 1, 0]}}}},
        {"$sort": {"_id": 1}},
    ]):
        n = doc["n"]; h = doc["hits"]
        print(f"  conf {doc['_id']:8s}  n={n:3d}  UNDER hit={h/n*100:.1f}%")
    print()

    # Role breakdown (Shot-Stopper vs Sweeper-Keeper vs Distribution)
    print("=== GK PASS_ATTEMPTS BY ROLE × REC ===")
    async for doc in db.picks.aggregate([
        {"$match": GK_FILTER},
        {"$group": {
            "_id": {"role": {"$ifNull": ["$role", "Unknown"]}, "rec": "$recommendation"},
            "n": {"$sum": 1},
            "hits": {"$sum": {"$cond": [{"$eq": ["$result", "hit"]}, 1, 0]}},
            "avg_line": {"$avg": "$line"},
            "avg_actual": {"$avg": "$actualValue"},
        }},
        {"$sort": {"_id.role": 1, "_id.rec": 1}},
    ]):
        n = doc["n"]; h = doc["hits"]
        ro = doc["_id"]["role"]; r = doc["_id"]["rec"]
        al = doc["avg_line"] or 0; aa = doc["avg_actual"] or 0
        print(f"  {ro:25s} {r:6s}  n={n:3d}  hit={h/n*100:5.1f}%  avg_line={al:.1f}  avg_actual={aa:.1f}")
    print()

    # Edge rating breakdown
    print("=== GK PASS_ATTEMPTS BY EDGE RATING × REC ===")
    async for doc in db.picks.aggregate([
        {"$match": GK_FILTER},
        {"$group": {
            "_id": {"edge": {"$ifNull": ["$edgeRating", "N/A"]}, "rec": "$recommendation"},
            "n": {"$sum": 1},
            "hits": {"$sum": {"$cond": [{"$eq": ["$result", "hit"]}, 1, 0]}},
        }},
        {"$sort": {"_id.edge": 1, "_id.rec": 1}},
    ]):
        n = doc["n"]; h = doc["hits"]
        e = doc["_id"]["edge"]; r = doc["_id"]["rec"]
        print(f"  {e:20s} {r:6s}  n={n:3d}  hit={h/n*100:.1f}%")

asyncio.run(main())
