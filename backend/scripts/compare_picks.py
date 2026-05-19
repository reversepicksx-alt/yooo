import asyncio, os
from motor.motor_asyncio import AsyncIOMotorClient
from collections import defaultdict

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
TARGET_EMAIL = "letwins04@gmail.com"

async def analyze_user_picks(db, email_or_id, label):
    # Try to find userId by email
    user = await db.users.find_one({"email": email_or_id})
    if not user:
        # Try picks directly with userEmail field
        sample = await db.picks.find_one({"userEmail": email_or_id})
        user_id = sample["userId"] if sample else email_or_id
    else:
        user_id = str(user["_id"])

    picks = await db.picks.find({
        "$or": [{"userId": user_id}, {"userEmail": email_or_id}],
        "status": "settled"
    }).to_list(None)

    if not picks:
        print(f"\n[{label}] No settled picks found for {email_or_id}")
        return None

    total = len(picks)
    hits = sum(1 for p in picks if p.get("hit") == True)
    hit_rate = round(hits / total * 100, 1) if total else 0

    # Breakdown by propType
    by_prop = defaultdict(lambda: {"total": 0, "hits": 0})
    by_rec = defaultdict(lambda: {"total": 0, "hits": 0})
    by_conf_bucket = defaultdict(lambda: {"total": 0, "hits": 0})
    by_league = defaultdict(lambda: {"total": 0, "hits": 0})
    by_venue = defaultdict(lambda: {"total": 0, "hits": 0})
    confidence_scores = []
    lines = []
    proj_errors = []

    for p in picks:
        prop = p.get("propType", "unknown")
        rec = p.get("recommendation", "unknown")
        conf = p.get("confidence", p.get("confidenceScore", 0))
        if isinstance(conf, float) and conf <= 1:
            conf = round(conf * 100)
        league = p.get("league", p.get("leagueName", "unknown"))
        venue = p.get("venue", "unknown")
        hit = p.get("hit") == True
        line = p.get("line", 0)
        proj = p.get("projectedValue", None)

        by_prop[prop]["total"] += 1
        by_rec[rec]["total"] += 1
        by_venue[venue]["total"] += 1
        if league:
            by_league[str(league)]["total"] += 1

        if hit:
            by_prop[prop]["hits"] += 1
            by_rec[rec]["hits"] += 1
            by_venue[venue]["hits"] += 1
            if league:
                by_league[str(league)]["hits"] += 1

        # Confidence bucket
        if conf >= 70:
            bucket = "70+"
        elif conf >= 60:
            bucket = "60-69"
        elif conf >= 50:
            bucket = "50-59"
        else:
            bucket = "<50"
        by_conf_bucket[bucket]["total"] += 1
        if hit:
            by_conf_bucket[bucket]["hits"] += 1

        confidence_scores.append(conf)
        lines.append(line)

        # Projection accuracy
        if proj is not None and line:
            proj_errors.append(abs(proj - line))

    avg_conf = round(sum(confidence_scores) / len(confidence_scores), 1) if confidence_scores else 0
    avg_line = round(sum(lines) / len(lines), 2) if lines else 0

    print(f"\n{'='*55}")
    print(f"  {label.upper()} — {email_or_id}")
    print(f"{'='*55}")
    print(f"  Settled picks : {total}")
    print(f"  Hits          : {hits}")
    print(f"  Hit rate      : {hit_rate}%")
    print(f"  Avg confidence: {avg_conf}%")
    print(f"  Avg line      : {avg_line}")

    print(f"\n  --- By Prop Type ---")
    for prop, v in sorted(by_prop.items(), key=lambda x: -x[1]["total"]):
        r = round(v["hits"]/v["total"]*100, 1) if v["total"] else 0
        print(f"    {prop:<22} {v['hits']}/{v['total']} = {r}%")

    print(f"\n  --- By Recommendation ---")
    for rec, v in sorted(by_rec.items(), key=lambda x: -x[1]["total"]):
        r = round(v["hits"]/v["total"]*100, 1) if v["total"] else 0
        print(f"    {rec:<10} {v['hits']}/{v['total']} = {r}%")

    print(f"\n  --- By Confidence Bucket ---")
    for bucket in ["70+", "60-69", "50-59", "<50"]:
        v = by_conf_bucket[bucket]
        r = round(v["hits"]/v["total"]*100, 1) if v["total"] else 0
        print(f"    {bucket:<10} {v['hits']}/{v['total']} = {r}%")

    print(f"\n  --- By Venue ---")
    for venue, v in sorted(by_venue.items(), key=lambda x: -x[1]["total"]):
        r = round(v["hits"]/v["total"]*100, 1) if v["total"] else 0
        print(f"    {venue:<10} {v['hits']}/{v['total']} = {r}%")

    print(f"\n  --- By League (top 10) ---")
    for league, v in sorted(by_league.items(), key=lambda x: -x[1]["total"])[:10]:
        r = round(v["hits"]/v["total"]*100, 1) if v["total"] else 0
        print(f"    {str(league):<30} {v['hits']}/{v['total']} = {r}%")

    return {
        "total": total, "hits": hits, "hit_rate": hit_rate,
        "by_prop": by_prop, "by_rec": by_rec, "by_conf": by_conf_bucket,
        "by_venue": by_venue, "by_league": by_league, "avg_conf": avg_conf,
        "picks": picks
    }

async def main():
    client = AsyncIOMotorClient(MONGO_URL)
    db = client["reversepicks"]

    # List all users
    print("=== ALL USERS IN DB ===")
    all_users = await db.users.find({}).to_list(None)
    for u in all_users:
        email = u.get("email", "?")
        role = u.get("role", "?")
        uid = str(u.get("_id", ""))
        print(f"  {email} | role={role} | id={uid}")

    print("\n=== ALL UNIQUE EMAILS IN PICKS ===")
    emails = await db.picks.distinct("userEmail")
    user_ids = await db.picks.distinct("userId")
    print("Emails:", emails[:20])
    print("UserIds:", user_ids[:20])

    # Analyze target user
    target_data = await analyze_user_picks(db, TARGET_EMAIL, "LETWINS04")

    # Analyze owner — try each userId/email that isn't the target
    for eid in emails:
        if eid and eid != TARGET_EMAIL:
            await analyze_user_picks(db, eid, "OWNER/OTHER")

asyncio.run(main())
