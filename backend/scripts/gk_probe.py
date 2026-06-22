"""Quick probe of what's actually in Atlas picks collection"""
import asyncio, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import mongo_client

async def main():
    # Try multiple database names
    for db_name in ["reversepicks", "reverse_picks", "rp", "picks"]:
        db = mongo_client[db_name]
        colls = await db.list_collection_names()
        if colls:
            print(f"DB '{db_name}' collections: {colls}")

    db = mongo_client["reversepicks"]

    # Total picks
    total = await db.picks.count_documents({})
    settled = await db.picks.count_documents({"settled": True})
    with_result = await db.picks.count_documents({"result": {"$in": ["HIT","MISS"]}})
    print(f"\npicks total={total}  settled={settled}  with_result={with_result}")

    # Sample any pick
    sample = await db.picks.find_one({})
    if sample:
        print("\nSample pick fields:")
        for k, v in sorted(sample.items()):
            if k not in ("_id", "userId", "email"):
                print(f"  {k}: {repr(v)[:100]}")
    
    # Sample settled pick
    s2 = await db.picks.find_one({"settled": True})
    if s2:
        print("\nSample SETTLED pick fields:")
        for k, v in sorted(s2.items()):
            if k not in ("_id", "userId", "email"):
                print(f"  {k}: {repr(v)[:100]}")

    # What propTypes exist?
    pipeline = [
        {"$group": {"_id": "$propType", "count": {"$sum": 1}, "settled": {"$sum": {"$cond": [{"$eq": ["$settled", True]}, 1, 0]}}}},
        {"$sort": {"settled": -1}},
    ]
    print("\nProp types (total vs settled):")
    async for doc in db.picks.aggregate(pipeline):
        print(f"  {doc['_id']}: total={doc['count']} settled={doc['settled']}")

    # Distinct positions on settled picks
    positions = await db.picks.distinct("position", {"settled": True})
    print(f"\nDistinct positions on settled picks: {positions}")

    # Soccer settled picks
    soc = await db.picks.count_documents({"settled": True, "sport": "soccer"})
    soc2 = await db.picks.count_documents({"settled": True, "leagueId": {"$exists": True}})
    print(f"\nSettled soccer picks (by sport field): {soc}")
    print(f"Settled picks with leagueId: {soc2}")

asyncio.run(main())
