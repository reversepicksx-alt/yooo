"""
GK Pass Attempts Cheat Sheet — analysis of all settled picks
Uses Atlas MongoDB via backend config.
Correct schema: result in ["hit","miss"], status="settled"
"""
import asyncio, sys, os, statistics
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import mongo_client

DB_NAME = "reversepicks"

SETTLED_FILTER = {"status": "settled", "result": {"$in": ["hit", "miss"]}}

async def main():
    db = mongo_client[DB_NAME]

    # ── 1. Quick position/propType census ────────────────────────────────────
    print("=== SETTLED PICKS BY POSITION ===")
    async for doc in db.picks.aggregate([
        {"$match": SETTLED_FILTER},
        {"$group": {"_id": "$position", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}}, {"$limit": 25},
    ]):
        print(f"  {doc['_id']!r}: {doc['count']}")
    print()

    print("=== SETTLED PICKS BY PROP TYPE ===")
    async for doc in db.picks.aggregate([
        {"$match": SETTLED_FILTER},
        {"$group": {"_id": "$propType", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}}, {"$limit": 25},
    ]):
        print(f"  {doc['_id']!r}: {doc['count']}")
    print()

    # ── 2. Gather GK pass_attempts picks ─────────────────────────────────────
    GK_POSITIONS = ["GK", "GKP", "Goalkeeper", "G", "goalkeeper", "gk"]
    gk_picks = []
    seen_ids = set()

    # A) by position field
    async for p in db.picks.find({**SETTLED_FILTER, "propType": "pass_attempts",
                                   "position": {"$in": GK_POSITIONS}}):
        p["_src"] = "position"
        gk_picks.append(p); seen_ids.add(p["_id"])

    # B) bayesianMetrics GK key
    async for p in db.picks.find({**SETTLED_FILTER, "propType": "pass_attempts",
                                   "bayesianMetrics.gkInvertedPoss": {"$exists": True}}):
        if p["_id"] not in seen_ids:
            p["_src"] = "bm_gk"
            gk_picks.append(p); seen_ids.add(p["_id"])

    # C) text signals (tactical breakdown mentions goalkeeper/keeper)
    async for p in db.picks.find({**SETTLED_FILTER, "propType": "pass_attempts",
                                   "$or": [
                                       {"tacticalBreakdown": {"$regex": "goalkeeper|keeper|\\bGK\\b", "$options": "i"}},
                                       {"sharpSummary": {"$regex": "goalkeeper|keeper|\\bGK\\b", "$options": "i"}},
                                   ]}):
        if p["_id"] not in seen_ids:
            p["_src"] = "text"
            gk_picks.append(p); seen_ids.add(p["_id"])

    print(f"GK pass_attempts settled picks found: {len(gk_picks)}")
    src_counts = defaultdict(int)
    for p in gk_picks:
        src_counts[p.get("_src","?")] += 1
    for src, cnt in src_counts.items():
        print(f"  via {src}: {cnt}")
    print()

    if not gk_picks:
        # Debug: show ALL pass_attempts settled picks regardless of position
        print("=== ALL pass_attempts SETTLED PICKS (debug) ===")
        total_pa = await db.picks.count_documents({**SETTLED_FILTER, "propType": "pass_attempts"})
        print(f"  Total settled pass_attempts picks: {total_pa}")
        async for p in db.picks.find({**SETTLED_FILTER, "propType": "pass_attempts"}, limit=20):
            bm = p.get("bayesianMetrics") or {}
            print(f"  {p.get('playerName')} | pos={p.get('position')!r} | venue={p.get('venue')} "
                  f"| homePoss={p.get('homePoss')} | awayPoss={p.get('awayPoss')} "
                  f"| bm_keys={list(bm.keys())[:8]}")
        return

    # ── 3. Schema sample ─────────────────────────────────────────────────────
    print("=== SAMPLE GK PICK FIELDS ===")
    for k, v in sorted(gk_picks[0].items()):
        if k not in ("_id", "userId", "email", "_src"):
            print(f"  {k}: {repr(v)[:120]}")
    print()

    # ── 4. Helper: extract possession from GK's team POV ─────────────────────
    def team_poss(p):
        venue = (p.get("venue") or "").lower()
        hp, ap = p.get("homePoss"), p.get("awayPoss")
        if hp is not None and ap is not None:
            hp, ap = float(hp), float(ap)
            if venue == "home":   return hp, ap
            if venue == "away":   return ap, hp
        return None, None

    def proj_poss(p):
        venue = (p.get("venue") or "").lower()
        ph, pa = p.get("projHomePoss"), p.get("projAwayPoss")
        if ph is not None and pa is not None:
            return float(ph) if venue == "home" else float(pa)
        ep = ((p.get("matchupOverview") or {}).get("expectedPossession") or {})
        if ep:
            return ep.get("home") if venue == "home" else ep.get("away")
        return None

    # Build enriched rows
    rows = []
    for p in gk_picks:
        tp, op = team_poss(p)
        hg = p.get("finalHomeGoals"); ag = p.get("finalAwayGoals")
        venue = (p.get("venue") or "").lower()
        if hg is not None and ag is not None:
            margin = abs(int(hg) - int(ag))
            winning = (venue == "home" and int(hg) > int(ag)) or (venue == "away" and int(ag) > int(hg))
        else:
            margin = winning = None
        bm = p.get("bayesianMetrics") or {}
        rows.append({
            "player":     p.get("playerName","?"),
            "hit":        1 if p.get("result") == "hit" else 0,
            "rec":        (p.get("recommendation") or "").lower(),
            "line":       p.get("line"),
            "actual":     p.get("actualValue"),
            "venue":      venue,
            "team_poss":  tp,
            "opp_poss":   op,
            "proj_poss":  proj_poss(p),
            "league":     p.get("league") or p.get("leagueName") or str(p.get("leagueId") or "?"),
            "gk_inv":     bm.get("gkInvertedPoss") or bm.get("gkPossessionInversion"),
            "margin":     margin,
            "winning":    winning,
        })

    # ── 5. Possession band analysis ───────────────────────────────────────────
    has_poss  = [r for r in rows if r["team_poss"] is not None]
    no_poss   = [r for r in rows if r["team_poss"] is None]

    def band_table(title, subset, poss_key="team_poss", bands=None):
        if bands is None:
            bands = [("<35%", 0, 35), ("35-42%", 35, 42), ("42-48%", 42, 48),
                     ("48-54%", 48, 54), ("54-62%", 54, 62), (">62%", 62, 100)]
        print(f"=== {title} ===")
        for label, lo, hi in bands:
            b = [r for r in subset if r.get(poss_key) is not None
                 and lo <= r[poss_key] < hi]
            if not b: continue
            n = len(b)
            hits = sum(r["hit"] for r in b)
            over_r  = [r for r in b if r["rec"] == "over"]
            under_r = [r for r in b if r["rec"] == "under"]
            avg_p   = statistics.mean(r[poss_key] for r in b)
            over_s  = (f"OVER={sum(r['hit'] for r in over_r)/len(over_r)*100:.0f}% ({len(over_r)}n)"
                       if over_r else "")
            under_s = (f"UNDER={sum(r['hit'] for r in under_r)/len(under_r)*100:.0f}% ({len(under_r)}n)"
                       if under_r else "")
            print(f"  {label:12s} n={n:3d}  overall={hits/n*100:5.1f}%  {over_s:22s} {under_s}  avg={avg_p:.1f}%")
        print()

    print(f"Possession data: {len(has_poss)}/{len(rows)} picks  (no data: {len(no_poss)})\n")

    band_table(
        "GK PASS_ATTEMPTS HIT RATE BY TEAM POSSESSION (actual match)\n"
        "  Lower team poss → GK distributes more under pressure",
        has_poss, "team_poss"
    )

    band_table(
        "GK PASS_ATTEMPTS HIT RATE BY OPPONENT POSSESSION\n"
        "  Higher opp poss → GK team pinned back → more short GK passes",
        has_poss, "opp_poss",
        bands=[("<38%", 0, 38), ("38-48%", 38, 48), ("48-54%", 48, 54),
               ("54-62%", 54, 62), (">62%", 62, 100)]
    )

    # ── 6. Venue split ────────────────────────────────────────────────────────
    print("=== GK PASS_ATTEMPTS BY VENUE ===")
    for venue in ["home", "away", "neutral", ""]:
        v = [r for r in rows if r["venue"] == venue]
        if not v: continue
        n = len(v); hits = sum(r["hit"] for r in v)
        over_r  = [r for r in v if r["rec"] == "over"]
        under_r = [r for r in v if r["rec"] == "under"]
        poss_v = [r["team_poss"] for r in v if r["team_poss"] is not None]
        avg_p = f"avg_team_poss={statistics.mean(poss_v):.1f}%" if poss_v else ""
        print(f"  {venue or 'unknown':8s}  n={n}  overall={hits/n*100:.1f}%  {avg_p}")
        if over_r:
            print(f"            OVER  n={len(over_r)}  hit={sum(r['hit'] for r in over_r)/len(over_r)*100:.1f}%")
        if under_r:
            print(f"            UNDER n={len(under_r)}  hit={sum(r['hit'] for r in under_r)/len(under_r)*100:.1f}%")
    print()

    # ── 7. Score margin / game state ─────────────────────────────────────────
    margin_rows = [r for r in rows if r["margin"] is not None]
    if margin_rows:
        print("=== GK PASS_ATTEMPTS BY MATCH MARGIN (goal difference) ===")
        for label, lo, hi in [("draw (0)", 0, 1), ("1-goal", 1, 2),
                               ("2-goal", 2, 3), ("3+ blowout", 3, 20)]:
            b = [r for r in margin_rows if lo <= r["margin"] < hi]
            if not b: continue
            hits = sum(r["hit"] for r in b)
            over_r = [r for r in b if r["rec"] == "over"]
            over_s = (f"  OVER={sum(r['hit'] for r in over_r)/len(over_r)*100:.0f}% ({len(over_r)}n)"
                      if over_r else "")
            print(f"  margin={label:14s} n={len(b)}  hit={hits/len(b)*100:.1f}%{over_s}")
        print()

        print("=== GK PASS_ATTEMPTS — GK TEAM WINNING vs LOSING ===")
        for label, flag in [("WINNING", True), ("LOSING", False)]:
            b = [r for r in margin_rows if r["winning"] == flag and r["margin"] > 0]
            if not b: continue
            hits = sum(r["hit"] for r in b)
            over_r = [r for r in b if r["rec"] == "over"]
            over_s = (f"  OVER={sum(r['hit'] for r in over_r)/len(over_r)*100:.0f}% ({len(over_r)}n)"
                      if over_r else "")
            print(f"  GK team {label}: n={len(b)}  hit={hits/len(b)*100:.1f}%{over_s}")
        print()

    # ── 8. GK inversion flag ──────────────────────────────────────────────────
    inv_y  = [r for r in rows if r["gk_inv"]]
    inv_n  = [r for r in rows if not r["gk_inv"]]
    if inv_y or inv_n:
        print("=== GK INVERSION FLAG vs HIT RATE ===")
        for label, grp in [("INVERSION ACTIVE", inv_y), ("NO INVERSION", inv_n)]:
            if not grp: continue
            hits = sum(r["hit"] for r in grp); n = len(grp)
            over_r = [r for r in grp if r["rec"] == "over"]
            over_s = (f"  OVER={sum(r['hit'] for r in over_r)/len(over_r)*100:.0f}% ({len(over_r)}n)"
                      if over_r else "")
            print(f"  {label}: n={n}  hit={hits/n*100:.1f}%{over_s}")
        print()

    # ── 9. League breakdown ───────────────────────────────────────────────────
    print("=== GK PASS_ATTEMPTS HIT RATE BY LEAGUE ===")
    by_league = defaultdict(list)
    for r in rows: by_league[r["league"]].append(r)
    for league, grp in sorted(by_league.items(), key=lambda x: -len(x[1])):
        if len(grp) < 3: continue
        hits = sum(r["hit"] for r in grp); n = len(grp)
        poss_v = [r["team_poss"] for r in grp if r["team_poss"] is not None]
        avg_p = f"avg_poss={statistics.mean(poss_v):.1f}%" if poss_v else ""
        print(f"  {league}: n={n}  hit={hits/n*100:.1f}%  {avg_p}")
    print()

    # ── 10. Line buckets (which lines are accurate) ───────────────────────────
    line_rows = [r for r in rows if r["line"] is not None and r["actual"] is not None]
    if line_rows:
        print("=== GK LINE ACCURACY (actual vs line) ===")
        for label, lo, hi in [("<35", 0, 35), ("35-45", 35, 45), ("45-55", 45, 55),
                               ("55-65", 55, 65), (">65", 65, 999)]:
            b = [r for r in line_rows if lo <= r["line"] < hi]
            if not b: continue
            hits = sum(r["hit"] for r in b)
            over_r = [r for r in b if r["rec"] == "over"]
            avg_actual = statistics.mean(r["actual"] for r in b)
            avg_line   = statistics.mean(r["line"] for r in b)
            print(f"  line {label:8s}  n={len(b)}  hit={hits/len(b)*100:.1f}%  "
                  f"avg_line={avg_line:.1f}  avg_actual={avg_actual:.1f}  "
                  + (f"OVER={sum(r['hit'] for r in over_r)/len(over_r)*100:.0f}% ({len(over_r)}n)" if over_r else ""))
        print()

    # ── 11. Projected vs actual possession delta ──────────────────────────────
    proj_rows = [(r["proj_poss"], r["team_poss"], r["hit"], r["rec"])
                 for r in has_poss if r["proj_poss"] is not None]
    if proj_rows:
        print("=== MODEL POSSESSION ACCURACY (proj – actual) ===")
        delta_map = [("overest >5%", 5, 999), ("accurate ±5%", -5, 5), ("underest <-5%", -999, -5)]
        for label, lo, hi in delta_map:
            b = [(pp, ap, h, r) for pp, ap, h, r in proj_rows if lo <= pp-ap < hi]
            if not b: continue
            hits = sum(x[2] for x in b)
            over_b = [(pp, ap, h, r) for pp, ap, h, r in b if r == "over"]
            over_s = (f"  OVER={sum(x[2] for x in over_b)/len(over_b)*100:.0f}% ({len(over_b)}n)"
                      if over_b else "")
            print(f"  model {label:18s}: n={len(b)}  hit={hits/len(b)*100:.1f}%{over_s}")
        print()

    # ── 12. Overall summary ───────────────────────────────────────────────────
    print("=" * 60)
    print("OVERALL SUMMARY")
    n = len(rows); hits = sum(r["hit"] for r in rows)
    over_r  = [r for r in rows if r["rec"] == "over"]
    under_r = [r for r in rows if r["rec"] == "under"]
    print(f"  Total GK pass_attempts settled picks: {n}")
    print(f"  Overall hit rate: {hits/n*100:.1f}%")
    if over_r:
        print(f"  OVER  n={len(over_r):3d}  hit={sum(r['hit'] for r in over_r)/len(over_r)*100:.1f}%")
    if under_r:
        print(f"  UNDER n={len(under_r):3d}  hit={sum(r['hit'] for r in under_r)/len(under_r)*100:.1f}%")

    # Best possession edge zones
    if has_poss:
        print()
        print("  KEY EDGES (possession-based):")
        for tp_lo, tp_hi, label in [(0, 40, "team_poss<40% (GK pinned back)"),
                                     (55, 100, "team_poss>55% (dominant team)")]:
            b = [r for r in has_poss if tp_lo <= r["team_poss"] < tp_hi]
            over_b = [r for r in b if r["rec"] == "over"]
            if over_b:
                hr = sum(r["hit"] for r in over_b)/len(over_b)*100
                print(f"    OVER when {label}: {hr:.0f}% hit ({len(over_b)}n)")

asyncio.run(main())
