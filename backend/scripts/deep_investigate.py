import asyncio, os
from motor.motor_asyncio import AsyncIOMotorClient
from collections import defaultdict
import statistics

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
OWNER = "reversepicksx@gmail.com"
TARGET = "letwins04@gmail.com"

def pct(h, t): return round(h/t*100,1) if t else 0

async def main():
    client = AsyncIOMotorClient(MONGO_URL)
    db = client["reversepicks"]

    settled = await db.picks.find({"status":"settled"}).to_list(None)
    owner_picks  = [p for p in settled if p.get("email") == OWNER]
    target_picks = [p for p in settled if p.get("email") == TARGET]
    pa_owner  = [p for p in owner_picks  if p.get("propType") == "pass_attempts"]
    pa_target = [p for p in target_picks if p.get("propType") == "pass_attempts"]

    # ================================================================
    # INVESTIGATION 1: OVER failure analysis for owner pass_attempts
    # ================================================================
    print("\n" + "="*65)
    print("  INVESTIGATION 1: OWNER OVER vs UNDER pass_attempts breakdown")
    print("="*65)

    pa_over  = [p for p in pa_owner if (p.get("recommendation") or "").lower() == "over"]
    pa_under = [p for p in pa_owner if (p.get("recommendation") or "").lower() == "under"]
    pa_over_hit  = [p for p in pa_over  if p.get("result") == "hit"]
    pa_over_miss = [p for p in pa_over  if p.get("result") == "miss"]
    pa_under_hit = [p for p in pa_under if p.get("result") == "hit"]

    print(f"\n  pass_attempts OVER  : {len(pa_over_hit)}/{len(pa_over)} = {pct(len(pa_over_hit),len(pa_over))}%")
    print(f"  pass_attempts UNDER : {len(pa_under_hit)}/{len(pa_under)} = {pct(len(pa_under_hit),len(pa_under))}%")

    # What do the OVER misses look like?
    print(f"\n  OVER MISSES analysis ({len(pa_over_miss)} picks):")
    # By position
    by_pos = defaultdict(lambda:{"t":0,"h":0})
    by_venue = defaultdict(lambda:{"t":0,"h":0})
    by_scenario = defaultdict(lambda:{"t":0,"h":0})
    by_league = defaultdict(lambda:{"t":0,"h":0})
    lines_over, proj_over, actual_over = [], [], []
    proj_err_over_hit, proj_err_over_miss = [], []

    for p in pa_over:
        pos   = p.get("position","?")
        venue = (p.get("venue") or "?").lower()
        scen  = p.get("scenarioBucket","?")
        lg    = str(p.get("leagueId","?"))
        hit   = p.get("result")=="hit"
        line  = p.get("line") or 0
        proj  = p.get("projectedValue") or 0
        actual= p.get("actualValue") or 0

        by_pos[pos]["t"]+=1; by_venue[venue]["t"]+=1
        by_scenario[scen]["t"]+=1; by_league[lg]["t"]+=1
        if hit:
            by_pos[pos]["h"]+=1; by_venue[venue]["h"]+=1
            by_scenario[scen]["h"]+=1; by_league[lg]["h"]+=1
            proj_err_over_hit.append(proj-line)
        else:
            proj_err_over_miss.append(proj-line)
        lines_over.append(line)
        proj_over.append(proj)
        actual_over.append(actual)

    print(f"\n  OVER by position:")
    for k,v in sorted(by_pos.items(), key=lambda x:-x[1]["t"]):
        print(f"    {k:<20} {v['h']}/{v['t']} = {pct(v['h'],v['t'])}%")

    print(f"\n  OVER by venue:")
    for k,v in sorted(by_venue.items(), key=lambda x:-x[1]["t"]):
        print(f"    {k:<12} {v['h']}/{v['t']} = {pct(v['h'],v['t'])}%")

    print(f"\n  OVER by scenario:")
    for k,v in sorted(by_scenario.items(), key=lambda x:-x[1]["t"]):
        print(f"    {k:<22} {v['h']}/{v['t']} = {pct(v['h'],v['t'])}%")

    print(f"\n  OVER by league:")
    for k,v in sorted(by_league.items(), key=lambda x:-x[1]["t"])[:12]:
        print(f"    {k:<8} {v['h']}/{v['t']} = {pct(v['h'],v['t'])}%")

    avg_line_over = round(sum(lines_over)/len(lines_over),1) if lines_over else 0
    avg_proj_over = round(sum(proj_over)/len(proj_over),1) if proj_over else 0
    avg_actual_over = round(sum(actual_over)/len(actual_over),1) if actual_over else 0
    print(f"\n  OVER picks — avg line={avg_line_over}, avg proj={avg_proj_over}, avg actual={avg_actual_over}")
    print(f"  Avg proj surplus on hits : {round(sum(proj_err_over_hit)/len(proj_err_over_hit),2) if proj_err_over_hit else 'n/a'}")
    print(f"  Avg proj surplus on misses: {round(sum(proj_err_over_miss)/len(proj_err_over_miss),2) if proj_err_over_miss else 'n/a'}")

    # Compare to letwins04 OVER
    print(f"\n  letwins04 OVER pass_attempts breakdown:")
    t_over = [p for p in pa_target if (p.get("recommendation") or "").lower() == "over"]
    t_over_hit = [p for p in t_over if p.get("result")=="hit"]
    t_over_miss = [p for p in t_over if p.get("result")=="miss"]
    print(f"  OVER: {len(t_over_hit)}/{len(t_over)} = {pct(len(t_over_hit),len(t_over))}%")
    by_pos_t = defaultdict(lambda:{"t":0,"h":0})
    by_scen_t = defaultdict(lambda:{"t":0,"h":0})
    for p in t_over:
        pos  = p.get("position","?")
        scen = p.get("scenarioBucket","?")
        hit  = p.get("result")=="hit"
        by_pos_t[pos]["t"]+=1
        by_scen_t[scen]["t"]+=1
        if hit:
            by_pos_t[pos]["h"]+=1
            by_scen_t[scen]["h"]+=1
    print(f"\n  letwins04 OVER by position:")
    for k,v in sorted(by_pos_t.items(), key=lambda x:-x[1]["t"]):
        print(f"    {k:<20} {v['h']}/{v['t']} = {pct(v['h'],v['t'])}%")
    print(f"\n  letwins04 OVER by scenario:")
    for k,v in sorted(by_scen_t.items(), key=lambda x:-x[1]["t"]):
        print(f"    {k:<22} {v['h']}/{v['t']} = {pct(v['h'],v['t'])}%")

    # ================================================================
    # INVESTIGATION 2: Bundesliga deep-dive
    # ================================================================
    print("\n\n" + "="*65)
    print("  INVESTIGATION 2: BUNDESLIGA pass_attempts failure analysis")
    print("="*65)

    BUNDES_ID = "78"
    bundes = [p for p in pa_owner if str(p.get("leagueId","")) == BUNDES_ID]
    bundes_hit  = [p for p in bundes if p.get("result")=="hit"]
    bundes_miss = [p for p in bundes if p.get("result")=="miss"]
    print(f"\n  Bundesliga pass_attempts: {len(bundes_hit)}/{len(bundes)} = {pct(len(bundes_hit),len(bundes))}%")

    # Breakdown
    by_pos_b  = defaultdict(lambda:{"t":0,"h":0})
    by_rec_b  = defaultdict(lambda:{"t":0,"h":0})
    by_scen_b = defaultdict(lambda:{"t":0,"h":0})
    by_ven_b  = defaultdict(lambda:{"t":0,"h":0})
    for p in bundes:
        pos  = p.get("position","?")
        rec  = (p.get("recommendation") or "?").lower()
        scen = p.get("scenarioBucket","?")
        ven  = (p.get("venue") or "?").lower()
        hit  = p.get("result")=="hit"
        for d,k in [(by_pos_b,pos),(by_rec_b,rec),(by_scen_b,scen),(by_ven_b,ven)]:
            d[k]["t"]+=1
            if hit: d[k]["h"]+=1

    print(f"\n  By position:")
    for k,v in sorted(by_pos_b.items(), key=lambda x:-x[1]["t"]):
        print(f"    {k:<20} {v['h']}/{v['t']} = {pct(v['h'],v['t'])}%")
    print(f"\n  By recommendation:")
    for k,v in sorted(by_rec_b.items(), key=lambda x:-x[1]["t"]):
        print(f"    {k:<12} {v['h']}/{v['t']} = {pct(v['h'],v['t'])}%")
    print(f"\n  By scenario:")
    for k,v in sorted(by_scen_b.items(), key=lambda x:-x[1]["t"]):
        print(f"    {k:<22} {v['h']}/{v['t']} = {pct(v['h'],v['t'])}%")
    print(f"\n  By venue:")
    for k,v in sorted(by_ven_b.items(), key=lambda x:-x[1]["t"]):
        print(f"    {k:<12} {v['h']}/{v['t']} = {pct(v['h'],v['t'])}%")

    # Show misses in detail
    print(f"\n  BUNDESLIGA MISS DETAILS:")
    for p in bundes_miss:
        print(f"    {p.get('playerName','?'):<22} pos={p.get('position','?'):<8} rec={p.get('recommendation','?'):<6} "
              f"line={p.get('line','?'):<6} proj={p.get('projectedValue','?'):<6} actual={p.get('actualValue','?'):<6} "
              f"venue={p.get('venue','?'):<6} scen={p.get('scenarioBucket','?')}")

    # Compare Bundesliga vs Premier League for same metrics
    prem = [p for p in pa_owner if str(p.get("leagueId","")) == "39"]
    prem_hit = [p for p in prem if p.get("result")=="hit"]
    print(f"\n  Premier League pass_attempts (for comparison): {len(prem_hit)}/{len(prem)} = {pct(len(prem_hit),len(prem))}%")

    # Projection accuracy comparison
    bundes_proj_err = [abs((p.get("projectedValue") or 0) - (p.get("actualValue") or 0)) for p in bundes if p.get("actualValue")]
    prem_proj_err   = [abs((p.get("projectedValue") or 0) - (p.get("actualValue") or 0)) for p in prem if p.get("actualValue")]
    bundes_line_diff = [(p.get("projectedValue") or 0) - (p.get("line") or 0) for p in bundes]
    print(f"  Bundesliga avg proj error : {round(sum(bundes_proj_err)/len(bundes_proj_err),2) if bundes_proj_err else 'n/a'}")
    print(f"  Premier   avg proj error  : {round(sum(prem_proj_err)/len(prem_proj_err),2) if prem_proj_err else 'n/a'}")
    print(f"  Bundesliga avg (proj-line): {round(sum(bundes_line_diff)/len(bundes_line_diff),2) if bundes_line_diff else 'n/a'} (positive=predicting OVER)")

    # ================================================================
    # INVESTIGATION 3: What exactly separates letwins OVER wins
    # from owner OVER losses? — projection margin analysis
    # ================================================================
    print("\n\n" + "="*65)
    print("  INVESTIGATION 3: Projection margin & confidence deep-dive")
    print("="*65)

    print(f"\n  For OWNER pass_attempts OVER picks:")
    margin_buckets = defaultdict(lambda:{"t":0,"h":0})
    for p in pa_over:
        proj = p.get("projectedValue") or 0
        line = p.get("line") or 0
        margin = proj - line
        if   margin >= 10: bucket = "proj 10+ over line"
        elif margin >= 5:  bucket = "proj 5-9 over line"
        elif margin >= 2:  bucket = "proj 2-4 over line"
        elif margin >= 0:  bucket = "proj 0-1 over line"
        else:              bucket = "proj UNDER line???"
        margin_buckets[bucket]["t"]+=1
        if p.get("result")=="hit": margin_buckets[bucket]["h"]+=1
    for k,v in sorted(margin_buckets.items()):
        print(f"    {k:<28} {v['h']}/{v['t']} = {pct(v['h'],v['t'])}%")

    print(f"\n  For LETWINS04 pass_attempts OVER picks:")
    margin_buckets_t = defaultdict(lambda:{"t":0,"h":0})
    for p in t_over:
        proj = p.get("projectedValue") or 0
        line = p.get("line") or 0
        margin = proj - line
        if   margin >= 10: bucket = "proj 10+ over line"
        elif margin >= 5:  bucket = "proj 5-9 over line"
        elif margin >= 2:  bucket = "proj 2-4 over line"
        elif margin >= 0:  bucket = "proj 0-1 over line"
        else:              bucket = "proj UNDER line???"
        margin_buckets_t[bucket]["t"]+=1
        if p.get("result")=="hit": margin_buckets_t[bucket]["h"]+=1
    for k,v in sorted(margin_buckets_t.items()):
        print(f"    {k:<28} {v['h']}/{v['t']} = {pct(v['h'],v['t'])}%")

    # ================================================================
    # INVESTIGATION 4: Line value analysis — is the owner taking
    # harder lines?
    # ================================================================
    print("\n\n" + "="*65)
    print("  INVESTIGATION 4: Line difficulty analysis")
    print("="*65)

    lines_o = [p.get("line") or 0 for p in pa_owner]
    lines_t = [p.get("line") or 0 for p in pa_target]
    print(f"\n  Owner pass_attempts line distribution:")
    print(f"    min={min(lines_o):.1f}  max={max(lines_o):.1f}  avg={round(sum(lines_o)/len(lines_o),1)}  median={round(statistics.median(lines_o),1)}")
    print(f"  letwins04 pass_attempts line distribution:")
    print(f"    min={min(lines_t):.1f}  max={max(lines_t):.1f}  avg={round(sum(lines_t)/len(lines_t),1)}  median={round(statistics.median(lines_t),1)}")

    # Line buckets
    print(f"\n  Owner by line bucket:")
    lb_o = defaultdict(lambda:{"t":0,"h":0})
    lb_t = defaultdict(lambda:{"t":0,"h":0})
    for p in pa_owner:
        l = p.get("line") or 0
        if   l >= 80: b = "80+"
        elif l >= 60: b = "60-79"
        elif l >= 40: b = "40-59"
        elif l >= 20: b = "20-39"
        else:         b = "<20"
        lb_o[b]["t"]+=1
        if p.get("result")=="hit": lb_o[b]["h"]+=1
    for p in pa_target:
        l = p.get("line") or 0
        if   l >= 80: b = "80+"
        elif l >= 60: b = "60-79"
        elif l >= 40: b = "40-59"
        elif l >= 20: b = "20-39"
        else:         b = "<20"
        lb_t[b]["t"]+=1
        if p.get("result")=="hit": lb_t[b]["h"]+=1
    print(f"    {'Bucket':<10} {'Owner':<22} {'letwins04'}")
    for b in ["80+","60-79","40-59","20-39","<20"]:
        vo, vt = lb_o[b], lb_t[b]
        print(f"    {b:<10} {vo['h']}/{vo['t']}={pct(vo['h'],vo['t'])}%{'':<8} {vt['h']}/{vt['t']}={pct(vt['h'],vt['t'])}%")

    # ================================================================
    # INVESTIGATION 5: Position analysis — who hits/misses most
    # ================================================================
    print("\n\n" + "="*65)
    print("  INVESTIGATION 5: Position breakdown for pass_attempts")
    print("="*65)

    by_pos_o = defaultdict(lambda:{"t":0,"h":0})
    by_pos_t2 = defaultdict(lambda:{"t":0,"h":0})
    for p in pa_owner:
        pos = p.get("position","?")
        by_pos_o[pos]["t"]+=1
        if p.get("result")=="hit": by_pos_o[pos]["h"]+=1
    for p in pa_target:
        pos = p.get("position","?")
        by_pos_t2[pos]["t"]+=1
        if p.get("result")=="hit": by_pos_t2[pos]["h"]+=1

    all_pos = set(by_pos_o) | set(by_pos_t2)
    print(f"\n  {'Position':<20} {'Owner':<22} {'letwins04'}")
    for k in sorted(all_pos, key=lambda x: -(by_pos_o[x]["t"]+by_pos_t2[x]["t"])):
        vo,vt = by_pos_o[k], by_pos_t2[k]
        print(f"  {k:<20} {vo['h']}/{vo['t']}={pct(vo['h'],vo['t'])}%{'':<8} {vt['h']}/{vt['t']}={pct(vt['h'],vt['t'])}%")

    # ================================================================
    # INVESTIGATION 6: Draw scenario deep-dive (owner 50%)
    # ================================================================
    print("\n\n" + "="*65)
    print("  INVESTIGATION 6: DRAW scenario — why owner hits 50%")
    print("="*65)

    draws_o = [p for p in pa_owner if p.get("scenarioBucket") == "draw"]
    draws_t = [p for p in pa_target if p.get("scenarioBucket") == "draw"]
    draw_hit_o = [p for p in draws_o if p.get("result")=="hit"]
    draw_hit_t = [p for p in draws_t if p.get("result")=="hit"]
    print(f"\n  Owner draw: {len(draw_hit_o)}/{len(draws_o)} = {pct(len(draw_hit_o),len(draws_o))}%")
    print(f"  letwins draw: {len(draw_hit_t)}/{len(draws_t)} = {pct(len(draw_hit_t),len(draws_t))}%")

    by_rec_d = defaultdict(lambda:{"t":0,"h":0})
    for p in draws_o:
        rec = (p.get("recommendation") or "?").lower()
        by_rec_d[rec]["t"]+=1
        if p.get("result")=="hit": by_rec_d[rec]["h"]+=1
    print(f"\n  Owner DRAW by recommendation:")
    for k,v in sorted(by_rec_d.items(), key=lambda x:-x[1]["t"]):
        print(f"    {k:<12} {v['h']}/{v['t']} = {pct(v['h'],v['t'])}%")

    by_pos_d = defaultdict(lambda:{"t":0,"h":0})
    for p in draws_o:
        pos = p.get("position","?")
        by_pos_d[pos]["t"]+=1
        if p.get("result")=="hit": by_pos_d[pos]["h"]+=1
    print(f"\n  Owner DRAW by position:")
    for k,v in sorted(by_pos_d.items(), key=lambda x:-x[1]["t"]):
        print(f"    {k:<20} {v['h']}/{v['t']} = {pct(v['h'],v['t'])}%")

    # ================================================================
    # INVESTIGATION 7: Confidence vs actual accuracy calibration
    # ================================================================
    print("\n\n" + "="*65)
    print("  INVESTIGATION 7: Is our confidence score calibrated?")
    print("="*65)
    print(f"  (owner pass_attempts only — expected hit% should match conf%)")
    print(f"\n  {'Conf bucket':<14} {'Expected':<12} {'Actual hits':<15} {'Calibration error'}")
    conf_expected = {"80+":85,"70-79":75,"60-69":65,"50-59":55,"<50":45}
    by_conf_all = defaultdict(lambda:{"t":0,"h":0})
    for p in pa_owner:
        conf = p.get("confidenceScore") or 0
        if isinstance(conf, float) and conf<=1: conf=round(conf*100)
        conf = int(conf)
        if   conf>=80: b="80+"
        elif conf>=70: b="70-79"
        elif conf>=60: b="60-69"
        elif conf>=50: b="50-59"
        else:          b="<50"
        by_conf_all[b]["t"]+=1
        if p.get("result")=="hit": by_conf_all[b]["h"]+=1
    for k in ["80+","70-79","60-69","50-59","<50"]:
        v = by_conf_all[k]
        actual_pct = pct(v["h"],v["t"])
        expected_pct = conf_expected[k]
        error = actual_pct - expected_pct
        flag = "  <<< OVER-CONFIDENT" if error < -10 else ("  <<< UNDER-CONFIDENT" if error > 10 else "  OK")
        print(f"  {k:<14} ~{expected_pct}%{'':<8} {v['h']}/{v['t']}={actual_pct}%{'':<8} {error:+.1f}%{flag}")

asyncio.run(main())
