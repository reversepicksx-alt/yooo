import asyncio, os
from motor.motor_asyncio import AsyncIOMotorClient

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")

async def main():
    client = AsyncIOMotorClient(MONGO_URL)

    # List all databases
    dbs = await client.list_database_names()
    print("=== DATABASES ===")
    for db_name in dbs:
        print(f"  {db_name}")
        db = client[db_name]
        cols = await db.list_collection_names()
        for col in cols:
            cnt = await db[col].estimated_document_count()
            print(f"    └─ {col}: ~{cnt} docs")

asyncio.run(main())
