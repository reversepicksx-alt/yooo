import asyncio, os
from motor.motor_asyncio import AsyncIOMotorClient
from collections import defaultdict

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
TARGET = "letwins04@gmail.com"

def analyze(picks, label):
    total = len(picks)
    if not total:
        print(f"\n[{label}] No settled picks found.")
        return None

    hits   = sum(1 for p in picks if p.get("result") == "hit")
    misses = sum(1 for p in picks if p.get("result") == "miss")
    hr     = round(hits / total * 100, 1)

    by_prop   = defaultdict(lambda: {"t":0,"h":0})
    by_rec    = defaultdict(lambda: {"t":0,"h":0})
    by_conf   = defaultdict(lambda: {"t":0,"h":0})
    by_venue  = defaultdict(lambda: {"t":0,"h":0})
    by_sport  = defaultdict(lambda: {"t":0,"h":0})
    by_league = defaultdict(lambda: {"t":0,"h":0})
    by_edge   = defaultdict(lambda: {"t":0,"h":0})
    by_safety = defaultdict(lambda: {"t":0,"h":0})
    by_scenario = defaultdict(lambda: {"t":0,"h":0})
    confs, proj_errs = [], []
    proj_dir_hits, proj_dir_total = 0, 0

    for p in picks:
        prop     = p.get("propType", "?")
        rec      = (p.get("recommendation") or "?").lower()
        venue    = (p.get("venue") or "?").lower()
        sport    = p.get("sport", "soccer")
        league   = str(p.get("leagueId") or "?")
        edge     = p.get("edgeRating", "?")
        safety   = p.get("safetyRating", "?")
        scenario = p.get("scenarioBucket", "?")
        result   = p.get("result")
        hit      = result == "hit"

        conf = p.get("confidenceScore") or 0
        if isinstance(conf, float) and conf <= 1.0:
            conf = round(conf * 100)
        conf = int(conf)

        if   conf >= 80: bucket = "80+"
        elif conf >= 70: bucket = "70-79"
        elif conf >= 60: bucket = "60-69"
        elif conf >= 50: bucket = "50-59"
        else:            bucket = "<50"

        for d, key in [(by_prop,prop),(by_rec,rec),(by_venue,venue),
                       (by_sport,sport),(by_league,league),(by_edge,edge),
                       (by_safety,safety),(by_scenario,scenario),(by_conf,bucket)]:
            d[key]["t"] += 1
            if hit: d[key]["h"] += 1

        confs.append(conf)

        # Projection direction accuracy
        proj = p.get("projectedValue")
        line = p.get("line")
        actual = p.get("actualValue")
        if proj is not None and line is not None and actual is not None:
            proj_side = "over" if proj > line else "under"
            actual_side = "over" if actual > line else ("under" if actual < line else "push")
            if actual_side != "push":
                proj_dir_total += 1
                if proj_side == actual_side:
                    proj_dir_hits += 1
            proj_errs.append(abs(proj - actual))

    avg_conf = round(sum(confs)/len(confs), 1) if confs else 0
    proj_dir_acc = round(proj_dir_hits/proj_dir_total*100,1) if proj_dir_total else 0
    avg_proj_err = round(sum(proj_errs)/len(proj_errs),2) if proj_errs else 0

    def row(d, k): return f"{d[k]['h']}/{d[k]['t']} = {round(d[k]['h']/d[k]['t']*100,1) if d[k]['t'] else 0}%"

    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}")
    print(f"  Settled picks  : {total}  ({hits} hits / {misses} misses)")
    print(f"  Hit rate       : {hr}%")
    print(f"  Avg confidence : {avg_conf}%")
    print(f"  Proj direction : {proj_dir_hits}/{proj_dir_total} = {proj_dir_acc}% correct side")
    print(f"  Avg proj error : {avg_proj_err} units off actual")

    print(f"\n  ── Prop Type ────────────────────────────────")
    for k,v in sorted(by_prop.items(), key=lambda x:-x[1]["t"]):
        bar = "█" * int(round(v["h"]/v["t"]*20)) if v["t"] else ""
        print(f"    {k:<24} {v['h']:>3}/{v['t']:<4} = {round(v['h']/v['t']*100,1) if v['t'] else 0:>5}%  {bar}")

    print(f"\n  ── Recommendation ───────────────────────────")
    for k,v in sorted(by_rec.items(), key=lambda x:-x[1]["t"]):
        print(f"    {k:<12} {v['h']:>3}/{v['t']:<4} = {round(v['h']/v['t']*100,1) if v['t'] else 0:>5}%")

    print(f"\n  ── Confidence Bucket ────────────────────────")
    for k in ["80+","70-79","60-69","50-59","<50"]:
        v = by_conf[k]
        print(f"    {k:<12} {v['h']:>3}/{v['t']:<4} = {round(v['h']/v['t']*100,1) if v['t'] else 0:>5}%")

    print(f"\n  ── Venue ────────────────────────────────────")
    for k,v in sorted(by_venue.items(), key=lambda x:-x[1]["t"]):
        print(f"    {k:<12} {v['h']:>3}/{v['t']:<4} = {round(v['h']/v['t']*100,1) if v['t'] else 0:>5}%")

    print(f"\n  ── Edge Rating ──────────────────────────────")
    for k,v in sorted(by_edge.items(), key=lambda x:-x[1]["t"]):
        print(f"    {k:<20} {v['h']:>3}/{v['t']:<4} = {round(v['h']/v['t']*100,1) if v['t'] else 0:>5}%")

    print(f"\n  ── Safety Rating ────────────────────────────")
    for k,v in sorted(by_safety.items(), key=lambda x:-x[1]["t"]):
        print(f"    {k:<20} {v['h']:>3}/{v['t']:<4} = {round(v['h']/v['t']*100,1) if v['t'] else 0:>5}%")

    print(f"\n  ── Scenario Bucket ──────────────────────────")
    for k,v in sorted(by_scenario.items(), key=lambda x:-x[1]["t"]):
        print(f"    {k:<20} {v['h']:>3}/{v['t']:<4} = {round(v['h']/v['t']*100,1) if v['t'] else 0:>5}%")

    print(f"\n  ── Sport ────────────────────────────────────")
    for k,v in sorted(by_sport.items(), key=lambda x:-x[1]["t"]):
        print(f"    {k:<12} {v['h']:>3}/{v['t']:<4} = {round(v['h']/v['t']*100,1) if v['t'] else 0:>5}%")

    print(f"\n  ── League ID (top 12) ───────────────────────")
    for k,v in sorted(by_league.items(), key=lambda x:-x[1]["t"])[:12]:
        print(f"    {k:<12} {v['h']:>3}/{v['t']:<4} = {round(v['h']/v['t']*100,1) if v['t'] else 0:>5}%")

    return {"total":total,"hits":hits,"hr":hr,"avg_conf":avg_conf,
            "by_prop":by_prop,"by_rec":by_rec,"by_conf":by_conf,
            "by_edge":by_edge,"by_safety":by_safety,"by_venue":by_venue,
            "by_sport":by_sport,"proj_dir_acc":proj_dir_acc}

async def main():
    client = AsyncIOMotorClient(MONGO_URL)
    db = client["reversepicks"]

    settled = await db.picks.find({"status":"settled"}).to_list(None)
    print(f"Total settled in DB: {len(settled)}")

    by_email = defaultdict(list)
    for p in settled:
        email = p.get("email","unknown")
        by_email[email].append(p)

    print("\n=== ALL USERS (email field) ===")
    for email, plist in sorted(by_email.items(), key=lambda x: -len(x[1])):
        hits = sum(1 for p in plist if p.get("result")=="hit")
        rate = round(hits/len(plist)*100,1) if plist else 0
        print(f"  {email:<38} picks={len(plist):>4}  hits={hits:>3}  rate={rate}%")

    # Analyze target
    letwins = by_email.get(TARGET, [])
    t_data = analyze(letwins, f"LETWINS04  ({TARGET})")

    # Analyze all others combined & individually
    others_all = []
    for email, plist in by_email.items():
        if email != TARGET:
            others_all.extend(plist)

    if others_all:
        analyze(others_all, f"ALL OTHER USERS COMBINED ({len(others_all)} picks)")

    # Individual breakdown for each non-target user with >5 picks
    for email, plist in sorted(by_email.items(), key=lambda x: -len(x[1])):
        if email != TARGET and len(plist) >= 5:
            analyze(plist, f"{email}")

asyncio.run(main())
