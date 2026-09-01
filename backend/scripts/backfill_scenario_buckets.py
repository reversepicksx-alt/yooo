"""
One-shot backfill: derive finalHomeGoals/finalAwayGoals/scenarioBucket
for every settled pick that already has matchScore but lacks the new fields.

matchScore is stored player-perspective ("player_goals-opp_goals") so we
flip with the pick's venue to recover home/away.
"""
import asyncio
import os
import sys
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from game_script_engine import bucket_from_final_score


async def main():
    mongo_url = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
    db_name = os.environ.get("DB_NAME", "test_database")
    db = AsyncIOMotorClient(mongo_url)[db_name]

    cursor = db.picks.find(
        {"result": {"$in": ["hit", "miss", "push"]},
         "matchScore": {"$exists": True, "$ne": None},
         "scenarioBucket": {"$in": [None, ""]} if False else {"$exists": False}},
        {"pickId": 1, "matchScore": 1, "venue": 1, "_id": 0},
    )
    docs = await cursor.to_list(length=20000)
    print(f"[BACKFILL] candidates: {len(docs)}")

    updated = 0
    skipped = 0
    by_bucket = {}
    for d in docs:
        ms = (d.get("matchScore") or "").strip()
        if "-" not in ms:
            skipped += 1
            continue
        try:
            pg, og = ms.split("-", 1)
            player_goals = int(pg.strip())
            opp_goals = int(og.strip())
        except (ValueError, AttributeError):
            skipped += 1
            continue
        venue = (d.get("venue") or "home").lower()
        if venue == "home":
            home_goals, away_goals = player_goals, opp_goals
        else:
            home_goals, away_goals = opp_goals, player_goals
        bucket = bucket_from_final_score(home_goals, away_goals)
        by_bucket[bucket] = by_bucket.get(bucket, 0) + 1
        await db.picks.update_one(
            {"pickId": d["pickId"]},
            {"$set": {
                "finalHomeGoals": home_goals,
                "finalAwayGoals": away_goals,
                "scenarioBucket": bucket,
            }}
        )
        updated += 1

    print(f"[BACKFILL] updated={updated} skipped={skipped}")
    print(f"[BACKFILL] bucket distribution:")
    for k, v in sorted(by_bucket.items(), key=lambda x: -x[1]):
        print(f"  {k:18s} {v:4d}")


if __name__ == "__main__":
    asyncio.run(main())
