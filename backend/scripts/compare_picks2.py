import asyncio, os
from motor.motor_asyncio import AsyncIOMotorClient
from collections import defaultdict

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
TARGET_EMAIL = "letwins04@gmail.com"

def analyze(picks, label, email):
    total = len(picks)
    if not total:
        print(f"\n[{label}] No settled picks found.")
        return None

    hits = sum(1 for p in picks if p.get("hit") is True)
    hit_rate = round(hits / total * 100, 1)

    by_prop    = defaultdict(lambda: {"t": 0, "h": 0})
    by_rec     = defaultdict(lambda: {"t": 0, "h": 0})
    by_conf    = defaultdict(lambda: {"t": 0, "h": 0})
    by_venue   = defaultdict(lambda: {"t": 0, "h": 0})
    by_league  = defaultdict(lambda: {"t": 0, "h": 0})
    by_sport   = defaultdict(lambda: {"t": 0, "h": 0})
    confs, lines = [], []

    for p in picks:
        prop   = p.get("propType", "unknown")
        rec    = (p.get("recommendation") or "unknown").lower()
        venue  = (p.get("venue") or "unknown").lower()
        league = str(p.get("leagueId") or p.get("league") or "unknown")
        sport  = p.get("sport", "soccer")
        hit    = p.get("hit") is True
        line   = p.get("line") or 0

        conf = p.get("confidence") or p.get("confidenceScore") or 0
        if isinstance(conf, float) and conf <= 1.0:
            conf = round(conf * 100)
        conf = int(conf)

        if   conf >= 70: bucket = "70+"
        elif conf >= 60: bucket = "60-69"
        elif conf >= 50: bucket = "50-59"
        else:            bucket = "<50"

        for d, key in [(by_prop, prop), (by_rec, rec), (by_venue, venue),
                       (by_league, league), (by_sport, sport), (by_conf, bucket)]:
            d[key]["t"] += 1
            if hit: d[key]["h"] += 1

        confs.append(conf)
        lines.append(line)

    avg_conf = round(sum(confs) / len(confs), 1) if confs else 0
    avg_line = round(sum(lines) / len(lines), 2) if lines else 0

    def pct(d): return round(d["h"]/d["t"]*100,1) if d["t"] else 0

    print(f"\n{'='*58}")
    print(f"  {label} — {email}")
    print(f"{'='*58}")
    print(f"  Settled  : {total}  |  Hits: {hits}  |  Hit rate: {hit_rate}%")
    print(f"  Avg conf : {avg_conf}%  |  Avg line: {avg_line}")

    print(f"\n  ── Prop Type ──────────────────────────────")
    for k, v in sorted(by_prop.items(), key=lambda x: -x[1]["t"]):
        print(f"    {k:<26} {v['h']}/{v['t']} = {pct(v)}%")

    print(f"\n  ── Recommendation ─────────────────────────")
    for k, v in sorted(by_rec.items(), key=lambda x: -x[1]["t"]):
        print(f"    {k:<12} {v['h']}/{v['t']} = {pct(v)}%")

    print(f"\n  ── Confidence Bucket ───────────────────────")
    for k in ["70+", "60-69", "50-59", "<50"]:
        v = by_conf[k]
        print(f"    {k:<12} {v['h']}/{v['t']} = {pct(v)}%")

    print(f"\n  ── Venue ───────────────────────────────────")
    for k, v in sorted(by_venue.items(), key=lambda x: -x[1]["t"]):
        print(f"    {k:<12} {v['h']}/{v['t']} = {pct(v)}%")

    print(f"\n  ── Sport ───────────────────────────────────")
    for k, v in sorted(by_sport.items(), key=lambda x: -x[1]["t"]):
        print(f"    {k:<12} {v['h']}/{v['t']} = {pct(v)}%")

    print(f"\n  ── League (top 12 by volume) ───────────────")
    for k, v in sorted(by_league.items(), key=lambda x: -x[1]["t"])[:12]:
        print(f"    {k:<30} {v['h']}/{v['t']} = {pct(v)}%")

    return {"total": total, "hits": hits, "hit_rate": hit_rate,
            "by_prop": by_prop, "by_rec": by_rec, "by_conf": by_conf,
            "by_venue": by_venue, "by_sport": by_sport, "avg_conf": avg_conf}

async def main():
    client = AsyncIOMotorClient(MONGO_URL)
    db = client["reversepicks"]

    # Discover all userIds / emails in settled picks
    settled = await db.picks.find({"status": "settled"}).to_list(None)
    print(f"Total settled picks in DB: {len(settled)}")

    by_user = defaultdict(list)
    for p in settled:
        key = p.get("userEmail") or p.get("userId") or "unknown"
        by_user[key].append(p)

    print("\n=== USER SUMMARY ===")
    for k, v in sorted(by_user.items(), key=lambda x: -len(x[1])):
        hits = sum(1 for p in v if p.get("hit") is True)
        rate = round(hits/len(v)*100,1) if v else 0
        print(f"  {k:<35} picks={len(v)}  hits={hits}  rate={rate}%")

    # Full breakdown for letwins04
    target_picks = by_user.get(TARGET_EMAIL, [])
    target_data  = analyze(target_picks, "LETWINS04", TARGET_EMAIL)

    # Full breakdown for every other user
    for email, picks in sorted(by_user.items(), key=lambda x: -len(x[1])):
        if email != TARGET_EMAIL:
            analyze(picks, "OTHER USER", email)

asyncio.run(main())
