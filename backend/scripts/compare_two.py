import asyncio, os
from motor.motor_asyncio import AsyncIOMotorClient
from collections import defaultdict

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")

LEAGUE_NAMES = {
    "39": "Premier League", "140": "La Liga", "135": "Serie A",
    "78": "Bundesliga", "61": "Ligue 1", "2": "Champions League",
    "667": "Europa League", "307": "Saudi Pro League", "253": "MLS",
    "188": "Saudi Pro", "262": "Copa America", "254": "Nations League",
    "71": "Brasileirao", "13": "Euro Q", "3": "Europa Conference"
}

def analyze(picks, label):
    total = len(picks)
    hits   = sum(1 for p in picks if p.get("result") == "hit")
    misses = sum(1 for p in picks if p.get("result") == "miss")
    hr     = round(hits / total * 100, 1) if total else 0

    by_prop     = defaultdict(lambda: {"t":0,"h":0})
    by_rec      = defaultdict(lambda: {"t":0,"h":0})
    by_conf     = defaultdict(lambda: {"t":0,"h":0})
    by_venue    = defaultdict(lambda: {"t":0,"h":0})
    by_sport    = defaultdict(lambda: {"t":0,"h":0})
    by_league   = defaultdict(lambda: {"t":0,"h":0})
    by_edge     = defaultdict(lambda: {"t":0,"h":0})
    by_safety   = defaultdict(lambda: {"t":0,"h":0})
    by_scenario = defaultdict(lambda: {"t":0,"h":0})
    confs = []
    proj_dir_hits = proj_dir_total = 0
    proj_errors = []

    for p in picks:
        prop     = p.get("propType", "?")
        rec      = (p.get("recommendation") or "?").lower()
        venue    = (p.get("venue") or "?").lower()
        sport    = p.get("sport", "soccer")
        league   = str(p.get("leagueId") or "?")
        edge     = p.get("edgeRating", "?")
        safety   = p.get("safetyRating", "?")
        scenario = p.get("scenarioBucket", "?")
        hit      = p.get("result") == "hit"

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

        proj   = p.get("projectedValue")
        line   = p.get("line")
        actual = p.get("actualValue")
        if proj is not None and line is not None and actual is not None:
            proj_side   = "over" if proj > line else "under"
            actual_side = "over" if actual > line else ("under" if actual < line else "push")
            if actual_side != "push":
                proj_dir_total += 1
                if proj_side == actual_side:
                    proj_dir_hits += 1
            proj_errors.append(abs(proj - actual))

    avg_conf     = round(sum(confs)/len(confs), 1) if confs else 0
    proj_dir_acc = round(proj_dir_hits/proj_dir_total*100,1) if proj_dir_total else 0
    avg_proj_err = round(sum(proj_errors)/len(proj_errors),2) if proj_errors else 0

    def pct(v): return round(v["h"]/v["t"]*100,1) if v["t"] else 0

    print(f"\n{'='*62}")
    print(f"  {label}")
    print(f"{'='*62}")
    print(f"  Settled : {total}  |  Hits: {hits}  |  Misses: {misses}")
    print(f"  Hit rate: {hr}%  |  Avg confidence: {avg_conf}%")
    print(f"  Proj dir accuracy: {proj_dir_hits}/{proj_dir_total} = {proj_dir_acc}%")
    print(f"  Avg proj error   : {avg_proj_err} units")

    print(f"\n  ── Prop Type ──────────────────────────────────────")
    for k,v in sorted(by_prop.items(), key=lambda x:-x[1]["t"]):
        bar = "█" * int(pct(v)/5)
        print(f"    {k:<28} {v['h']:>3}/{v['t']:<4} = {pct(v):>5}%  {bar}")

    print(f"\n  ── Recommendation ─────────────────────────────────")
    for k,v in sorted(by_rec.items(), key=lambda x:-x[1]["t"]):
        print(f"    {k:<12} {v['h']:>3}/{v['t']:<4} = {pct(v):>5}%")

    print(f"\n  ── Confidence Bucket ──────────────────────────────")
    for k in ["80+","70-79","60-69","50-59","<50"]:
        v = by_conf[k]
        print(f"    {k:<12} {v['h']:>3}/{v['t']:<4} = {pct(v):>5}%")

    print(f"\n  ── Venue ──────────────────────────────────────────")
    for k,v in sorted(by_venue.items(), key=lambda x:-x[1]["t"]):
        print(f"    {k:<12} {v['h']:>3}/{v['t']:<4} = {pct(v):>5}%")

    print(f"\n  ── Edge Rating ────────────────────────────────────")
    for k,v in sorted(by_edge.items(), key=lambda x:-x[1]["t"]):
        print(f"    {k:<22} {v['h']:>3}/{v['t']:<4} = {pct(v):>5}%")

    print(f"\n  ── Safety Rating ──────────────────────────────────")
    for k,v in sorted(by_safety.items(), key=lambda x:-x[1]["t"]):
        print(f"    {k:<22} {v['h']:>3}/{v['t']:<4} = {pct(v):>5}%")

    print(f"\n  ── Scenario Bucket ────────────────────────────────")
    for k,v in sorted(by_scenario.items(), key=lambda x:-x[1]["t"]):
        print(f"    {k:<22} {v['h']:>3}/{v['t']:<4} = {pct(v):>5}%")

    print(f"\n  ── Sport ──────────────────────────────────────────")
    for k,v in sorted(by_sport.items(), key=lambda x:-x[1]["t"]):
        print(f"    {k:<12} {v['h']:>3}/{v['t']:<4} = {pct(v):>5}%")

    print(f"\n  ── League (top 15) ────────────────────────────────")
    for k,v in sorted(by_league.items(), key=lambda x:-x[1]["t"])[:15]:
        name = LEAGUE_NAMES.get(k, k)
        print(f"    {name:<32} {v['h']:>3}/{v['t']:<4} = {pct(v):>5}%")

    return {"hr":hr,"avg_conf":avg_conf,"proj_dir_acc":proj_dir_acc,
            "by_prop":by_prop,"by_rec":by_rec,"by_conf":by_conf,
            "by_edge":by_edge,"by_safety":by_safety,"by_venue":by_venue,
            "by_scenario":by_scenario,"avg_proj_err":avg_proj_err}

async def main():
    client = AsyncIOMotorClient(MONGO_URL)
    db = client["reversepicks"]

    settled = await db.picks.find({"status":"settled"}).to_list(None)
    by_email = defaultdict(list)
    for p in settled:
        by_email[p.get("email","?")].append(p)

    letwins = by_email["letwins04@gmail.com"]
    owner   = by_email["reversepicksx@gmail.com"]
    # Also pull luzelena for comparison — also 82.8%
    luzelena = by_email["luzelena0015@gmail.com"]

    d1 = analyze(letwins,  "LETWINS04 (letwins04@gmail.com)")
    d2 = analyze(owner,    "OWNER (reversepicksx@gmail.com)")
    d3 = analyze(luzelena, "LUZELENA (luzelena0015@gmail.com) — also 82.8%")

    # ── COMPARISON SUMMARY ──
    print(f"\n\n{'='*62}")
    print(f"  COMPARISON: What letwins04 does differently")
    print(f"{'='*62}")

    print(f"\n  Hit rates: letwins={d1['hr']}%  |  owner={d2['hr']}%  |  luzelena={d3['hr']}%")
    print(f"  Avg conf : letwins={d1['avg_conf']}%  |  owner={d2['avg_conf']}%  |  luzelena={d3['avg_conf']}%")
    print(f"  Proj dir : letwins={d1['proj_dir_acc']}%  |  owner={d2['proj_dir_acc']}%  |  luzelena={d3['proj_dir_acc']}%")
    print(f"  Proj err : letwins={d1['avg_proj_err']}  |  owner={d2['avg_proj_err']}  |  luzelena={d3['avg_proj_err']}")

    print(f"\n  ── Confidence Comparison ──")
    print(f"    Bucket     letwins     owner       luzelena")
    for k in ["80+","70-79","60-69","50-59","<50"]:
        def pct(d,k):
            v=d["by_conf"][k]
            return f"{round(v['h']/v['t']*100,1) if v['t'] else 0}% ({v['t']}picks)"
        print(f"    {k:<10} {pct(d1,k):<20} {pct(d2,k):<20} {pct(d3,k)}")

    print(f"\n  ── Edge Rating Comparison ──")
    all_edges = set(d1["by_edge"]) | set(d2["by_edge"])
    for k in sorted(all_edges):
        def pct(d,k):
            v=d["by_edge"].get(k,{"t":0,"h":0})
            return f"{round(v['h']/v['t']*100,1) if v['t'] else '-'}% ({v['t']})"
        print(f"    {k:<22} letwins={pct(d1,k):<18} owner={pct(d2,k)}")

    print(f"\n  ── Safety Rating Comparison ──")
    all_safety = set(d1["by_safety"]) | set(d2["by_safety"])
    for k in sorted(all_safety):
        def pct(d,k):
            v=d["by_safety"].get(k,{"t":0,"h":0})
            return f"{round(v['h']/v['t']*100,1) if v['t'] else '-'}% ({v['t']})"
        print(f"    {k:<22} letwins={pct(d1,k):<18} owner={pct(d2,k)}")

    print(f"\n  ── Scenario Comparison ──")
    all_scen = set(d1["by_scenario"]) | set(d2["by_scenario"])
    for k in sorted(all_scen):
        def pct(d,k):
            v=d["by_scenario"].get(k,{"t":0,"h":0})
            return f"{round(v['h']/v['t']*100,1) if v['t'] else '-'}% ({v['t']})"
        print(f"    {k:<22} letwins={pct(d1,k):<18} owner={pct(d2,k)}")

    print(f"\n  ── Venue Comparison ──")
    for k in ["home","away"]:
        def pct(d,k):
            v=d["by_venue"].get(k,{"t":0,"h":0})
            return f"{round(v['h']/v['t']*100,1) if v['t'] else '-'}% ({v['t']})"
        print(f"    {k:<12} letwins={pct(d1,k):<18} owner={pct(d2,k)}")

    print(f"\n  ── Top Prop Type Comparison ──")
    all_props = set(d1["by_prop"]) | set(d2["by_prop"])
    rows = []
    for k in all_props:
        v1=d1["by_prop"].get(k,{"t":0,"h":0})
        v2=d2["by_prop"].get(k,{"t":0,"h":0})
        rows.append((k,v1,v2))
    for k,v1,v2 in sorted(rows, key=lambda x:-(x[1]["t"]+x[2]["t"])):
        def pct(v): return f"{round(v['h']/v['t']*100,1) if v['t'] else '-'}% ({v['t']})"
        print(f"    {k:<26} letwins={pct(v1):<18} owner={pct(v2)}")

asyncio.run(main())
