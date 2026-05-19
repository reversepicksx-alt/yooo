import asyncio, os
from motor.motor_asyncio import AsyncIOMotorClient
from collections import defaultdict

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
TARGET_EMAIL = "letwins04@gmail.com"

async def main():
    client = AsyncIOMotorClient(MONGO_URL)
    db = client["reversepicks"]

    # Inspect 3 settled picks to understand schema
    samples = await db.picks.find({"status": "settled"}).limit(3).to_list(None)
    print("=== SAMPLE SETTLED PICK FIELDS ===")
    for i, p in enumerate(samples):
        print(f"\n--- Pick {i+1} ---")
        for k, v in p.items():
            if k not in ("_id",):
                print(f"  {k}: {repr(v)[:80]}")

    # Check what the 'hit' field actually looks like
    print("\n=== HIT FIELD VALUES (distinct) ===")
    hit_vals = await db.picks.distinct("hit")
    print("hit values:", hit_vals[:20])

    # Check userId / userEmail fields
    print("\n=== USER IDENTIFICATION FIELDS ===")
    uid_vals = await db.picks.distinct("userId")
    email_vals = await db.picks.distinct("userEmail")
    print("distinct userId:", uid_vals[:15])
    print("distinct userEmail:", email_vals[:15])

    # Find letwins04 picks by any means
    print(f"\n=== SEARCHING FOR {TARGET_EMAIL} ===")
    by_email = await db.picks.count_documents({"userEmail": TARGET_EMAIL})
    print(f"  by userEmail: {by_email}")

    # Search sessions to find userId
    session = await db.sessions.find_one({"email": TARGET_EMAIL})
    if session:
        print(f"  session found: userId={session.get('userId')}")
        uid = session.get("userId")
        by_uid = await db.picks.count_documents({"userId": uid})
        print(f"  picks by userId={uid}: {by_uid}")

asyncio.run(main())
