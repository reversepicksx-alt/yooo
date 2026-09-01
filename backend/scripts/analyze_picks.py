import asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from collections import defaultdict

TARGET_EMAIL = "letwins04@gmail.com"

async def main():
    client = AsyncIOMotorClient("mongodb://localhost:27017")
    db = client["reversepicks"]

    cols = await db.list_collection_names()
    print("Collections:", cols)

    # Find userId for target email
    # Try both 'users' and 'accounts' collections
    target_user_id = None
    owner_user_id = None

    for col in ["users", "accounts", "user"]:
        if col in cols:
            all_users = await db[col].find({}).to_list(None)
            print(f"\n=== {col} collection ({len(all_users)} docs) ===")
            for u in all_users:
                email = u.get("email", "")
                uid = str(u.get("_id", u.get("userId", "")))
                role = u.get("role", "")
                print(f"  {email} | id={uid} | role={role}")
                if email == TARGET_EMAIL:
                    target_user_id = uid

    # Get all unique userIds from picks
    all_uids = await db.picks.distinct("userId")
    print(f"\n=== Unique userIds in picks ({len(all_uids)}) ===")
    for uid in all_uids:
        cnt = await db.picks.count_documents({"userId": uid, "status": "settled"})
        hits = await db.picks.count_documents({"userId": uid, "status": "settled", "hit": True})
        rate = round(hits/cnt*100, 1) if cnt else 0
        print(f"  {uid} | settled={cnt} | hits={hits} | hitRate={rate}%")
        if cnt > 0:
            # show sample email if in pick
            sample = await db.picks.find_one({"userId": uid})
            if sample and sample.get("userEmail"):
                print(f"    email={sample['userEmail']}")

asyncio.run(main())
