import asyncio, os
from motor.motor_asyncio import AsyncIOMotorClient
from collections import defaultdict

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")

async def main():
    client = AsyncIOMotorClient(MONGO_URL)
    db = client["reversepicks"]

    settled = await db.picks.find({"status": "settled"}).to_list(None)

    by_email = defaultdict(list)
    for p in settled:
        email = p.get("email", "unknown")
        by_email[email].append(p)

    print(f"Total settled: {len(settled)}\n")
    print("=== FULL USER SUMMARY (sorted by pick count) ===")
    rows = []
    for email, plist in by_email.items():
        hits = sum(1 for p in plist if p.get("result") == "hit")
        misses = sum(1 for p in plist if p.get("result") == "miss")
        settled_cnt = hits + misses
        rate = round(hits / settled_cnt * 100, 1) if settled_cnt else 0
        rows.append((email, len(plist), settled_cnt, hits, misses, rate))

    for row in sorted(rows, key=lambda x: -x[1]):
        email, total, settled_cnt, hits, misses, rate = row
        print(f"  {email:<40} total={total:>4}  settled={settled_cnt:>4}  hits={hits:>3}  misses={misses:>3}  rate={rate}%")

    # Check for letwins04 specifically — case insensitive + any field
    print("\n=== SEARCHING ALL FIELDS FOR 'letwins' ===")
    for p in settled[:5000]:
        for k, v in p.items():
            if isinstance(v, str) and "letwins" in v.lower():
                print(f"  Found in field '{k}': {v}")

asyncio.run(main())
