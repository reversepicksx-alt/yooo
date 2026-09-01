"""
Deep analysis of ALL settled props — SPORT-SEPARATED.
Soccer, CS2, Baseball, Tennis each analysed independently.
No position/prop data bleeds across sports.
"""
import asyncio, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import mongo_client

DB_NAME = "reversepicks"
SETTLED  = {"status": "settled", "result": {"$in": ["hit", "miss"]}}

# ── Sport → canonical prop types ─────────────────────────────────────────────
SPORT_PROPS = {
    "⚽ SOCCER": [
        "pass_attempts", "shots", "shots_on_target", "shots_assisted",
        "tackles", "clearances", "dribbles", "crosses", "key_passes",
        "goals", "assists", "fouls_committed",
        "saves",                         # soccer GK saves
    ],
    "🎮 CS2": [
        "maps_1_2_kills", "map1_kills", "maps_1_3_kills",
        "maps_1_2_headshots", "map1_headshots",
        "maps_1_2_rating", "maps_1_2_assists",
    ],
    "⚾ BASEBALL": [
        "pitcher_strikeouts", "hitter_fantasy_points",
        "pitcher_fantasy_score", "pitches_thrown",
        "hits", "runs", "earned_runs", "home_runs",
        "hits_allowed", "walks_allowed", "walks",
        "total_bases", "hits_runs_rbis",
        "innings_pitched", "strikeouts", "plate_appearances",
    ],
    "🎾 TENNIS": [
        "total_games", "player_games_won", "set_1_player_games",
        "player_sets_won",
    ],
    "🏒 HOCKEY": [
        "hockey_saves", "shots_on_goal", "points",
        "goals_allowed", "assists_hockey",
    ],
}

# Soccer positions only (for soccer prop × position breakdowns)
SOCCER_POSITIONS = {"GK","GKP","Goalkeeper","CB","LB","RB","LWB","RWB",
                    "CDM","CM","CAM","LM","RM","LW","RW","SS","ST","CF","FW"}

# CS2 positions only
CS2_POSITIONS = {"IGL","Rifler","AWPer","Lurker","Support","Entry","Fragger",
                 "Sniper","Hybrid","CM"}

POSS_BANDS = [
    ("<35%",   "A_<35",  0,  35),
    ("35-42%", "B_35-42",35, 42),
    ("42-48%", "C_42-48",42, 48),
    ("48-55%", "D_48-55",48, 55),
    ("55-62%", "E_55-62",55, 62),
    (">62%",   "F_>62",  62, 100),
]

CONF_BANDS_AGG = {"$switch": {"branches": [
    {"case": {"$lt": ["$confidenceScore", 55]}, "then": "1_<55"},
    {"case": {"$lt": ["$confidenceScore", 60]}, "then": "2_55-59"},
    {"case": {"$lt": ["$confidenceScore", 65]}, "then": "3_60-64"},
    {"case": {"$lt": ["$confidenceScore", 70]}, "then": "4_65-69"},
    {"case": {"$lt": ["$confidenceScore", 75]}, "then": "5_70-74"},
    {"case": {"$lt": ["$confidenceScore", 80]}, "then": "6_75-79"},
], "default": "7_80+"}}

def sep(title="", w=72):
    if title:
        print(f"\n{'═'*w}\n  {title}\n{'═'*w}")
    else:
        print("─" * w)

def flag(hit, n, thresh_good=0.72, thresh_bad=0.45):
    if n < 5: return "  (small n)"
    if hit > thresh_good: return "  ⭐"
    if hit < thresh_bad:  return "  ❌"
    return ""

async def census(db, props, label):
    """Global census for a sport's props."""
    sep(f"{label} — PROP CENSUS (all settled picks)")
    async for doc in db.picks.aggregate([
        {"$match": {**SETTLED, "propType": {"$in": props}}},
        {"$group": {
            "_id": {"prop": "$propType", "rec": "$recommendation"},
            "n":   {"$sum": 1},
            "hits": {"$sum": {"$cond": [{"$eq": ["$result","hit"]},1,0]}},
            "avg_line":   {"$avg": "$line"},
            "avg_actual": {"$avg": "$actualValue"},
        }},
        {"$sort": {"_id.prop":1,"_id.rec":1}},
    ]):
        n=doc["n"]; h=doc["hits"]
        p=doc["_id"]["prop"]; r=doc["_id"]["rec"]
        al=doc["avg_line"] or 0; aa=doc["avg_actual"] or 0
        bias=aa-al
        f=flag(h/n,n)
        print(f"  {p:32s} {r:6s}  n={n:4d}  hit={h/n*100:5.1f}%  "
              f"line={al:7.1f}  actual={aa:7.1f}  bias={bias:+6.1f}{f}")

async def by_venue(db, props, label):
    """Hit rate by venue for each prop."""
    sep(f"{label} — BY VENUE")
    async for doc in db.picks.aggregate([
        {"$match": {**SETTLED, "propType": {"$in": props},
                    "venue": {"$in": ["home","away","neutral"]}}},
        {"$group": {
            "_id": {"prop":"$propType","venue":"$venue","rec":"$recommendation"},
            "n":   {"$sum":1},
            "hits":{"$sum":{"$cond":[{"$eq":["$result","hit"]},1,0]}},
        }},
        {"$sort": {"_id.prop":1,"_id.venue":1,"_id.rec":1}},
    ]):
        n=doc["n"]; h=doc["hits"]
        if n<5: continue
        p=doc["_id"]["prop"]; v=doc["_id"]["venue"]; r=doc["_id"]["rec"]
        f=flag(h/n,n)
        print(f"  {p:32s} {v:8s} {r:6s}  n={n:3d}  hit={h/n*100:5.1f}%{f}")

async def by_scenario(db, props, label):
    """Hit rate by scenario bucket."""
    sep(f"{label} — BY SCENARIO BUCKET")
    async for doc in db.picks.aggregate([
        {"$match": {**SETTLED, "propType": {"$in": props},
                    "scenarioBucket": {"$exists":True,"$nin":[None,""]}}},
        {"$group": {
            "_id": {"prop":"$propType","bucket":"$scenarioBucket","rec":"$recommendation"},
            "n":   {"$sum":1},
            "hits":{"$sum":{"$cond":[{"$eq":["$result","hit"]},1,0]}},
        }},
        {"$sort": {"_id.prop":1,"_id.bucket":1,"_id.rec":1}},
    ]):
        n=doc["n"]; h=doc["hits"]
        if n<5: continue
        p=doc["_id"]["prop"]; b=doc["_id"]["bucket"]; r=doc["_id"]["rec"]
        f=flag(h/n,n)
        print(f"  {p:32s} {b:20s} {r:6s}  n={n:3d}  hit={h/n*100:5.1f}%{f}")

async def by_possession(db, props, label):
    """Hit rate by team possession band (soccer only)."""
    sep(f"{label} — BY TEAM POSSESSION BAND")
    async for doc in db.picks.aggregate([
        {"$match": {**SETTLED, "propType": {"$in": props},
                    "homePoss": {"$exists":True,"$ne":None},
                    "awayPoss": {"$exists":True,"$ne":None}}},
        {"$addFields": {"team_poss": {"$cond": [
            {"$eq":["$venue","home"]},
            {"$toDouble":"$homePoss"},
            {"$toDouble":"$awayPoss"}
        ]}}},
        {"$addFields": {"pb": {"$switch": {"branches": [
            {"case":{"$lt":["$team_poss",35]}, "then":"A_<35"},
            {"case":{"$lt":["$team_poss",42]}, "then":"B_35-42"},
            {"case":{"$lt":["$team_poss",48]}, "then":"C_42-48"},
            {"case":{"$lt":["$team_poss",55]}, "then":"D_48-55"},
            {"case":{"$lt":["$team_poss",62]}, "then":"E_55-62"},
        ], "default":"F_>62"}}}},
        {"$group": {
            "_id": {"prop":"$propType","pb":"$pb","rec":"$recommendation"},
            "n":   {"$sum":1},
            "hits":{"$sum":{"$cond":[{"$eq":["$result","hit"]},1,0]}},
        }},
        {"$sort": {"_id.prop":1,"_id.pb":1,"_id.rec":1}},
    ]):
        n=doc["n"]; h=doc["hits"]
        if n<5: continue
        p=doc["_id"]["prop"]; pb=doc["_id"]["pb"]; r=doc["_id"]["rec"]
        band = next((b[0] for b in POSS_BANDS if b[1]==pb), pb)
        f=flag(h/n,n)
        print(f"  {p:32s} poss={band:7s} {r:6s}  n={n:3d}  hit={h/n*100:5.1f}%{f}")

async def by_position(db, props, label, allowed_positions=None):
    """Hit rate by position (filtered to sport-appropriate positions)."""
    sep(f"{label} — BY POSITION (n≥10)")
    match = {**SETTLED, "propType": {"$in": props},
             "position": {"$exists":True,"$nin":[None,""]}}
    if allowed_positions:
        match["position"] = {"$in": list(allowed_positions)}
    async for doc in db.picks.aggregate([
        {"$match": match},
        {"$group": {
            "_id": {"prop":"$propType","pos":"$position","rec":"$recommendation"},
            "n":   {"$sum":1},
            "hits":{"$sum":{"$cond":[{"$eq":["$result","hit"]},1,0]}},
            "avg_line":   {"$avg":"$line"},
            "avg_actual": {"$avg":"$actualValue"},
        }},
        {"$sort": {"_id.prop":1,"_id.pos":1,"_id.rec":1}},
    ]):
        n=doc["n"]; h=doc["hits"]
        if n<10: continue
        p=doc["_id"]["prop"]; pos=doc["_id"]["pos"]; r=doc["_id"]["rec"]
        al=doc["avg_line"] or 0; aa=doc["avg_actual"] or 0
        f=flag(h/n,n)
        print(f"  {p:32s} pos={pos:8s} {r:6s}  n={n:3d}  hit={h/n*100:5.1f}%  "
              f"line={al:6.1f}  actual={aa:6.1f}  bias={aa-al:+5.1f}{f}")

async def by_confidence(db, props, label, direction="over"):
    """Hit rate by confidence band for a given direction."""
    sep(f"{label} — CONFIDENCE CALIBRATION ({direction.upper()}, n≥10)")
    async for doc in db.picks.aggregate([
        {"$match": {**SETTLED, "propType": {"$in": props},
                    "recommendation": direction, "confidenceScore": {"$gt":0}}},
        {"$addFields": {"cb": CONF_BANDS_AGG}},
        {"$group": {
            "_id": {"prop":"$propType","cb":"$cb"},
            "n":   {"$sum":1},
            "hits":{"$sum":{"$cond":[{"$eq":["$result","hit"]},1,0]}},
        }},
        {"$sort": {"_id.prop":1,"_id.cb":1}},
    ]):
        n=doc["n"]; h=doc["hits"]
        if n<10: continue
        p=doc["_id"]["prop"]; cb=doc["_id"]["cb"]
        f=flag(h/n,n)
        print(f"  {p:32s} conf={cb:8s}  n={n:3d}  {direction.upper()} hit={h/n*100:5.1f}%{f}")

async def ranked_summary(db):
    """Cross-sport ranked summary — but clearly labelled by sport."""
    sep("GLOBAL RANKING — BEST/WORST BETS ACROSS ALL SPORTS (n≥20)")
    all_rows = []
    # tag each prop with its sport
    prop_to_sport = {}
    for sport, props in SPORT_PROPS.items():
        for p in props:
            prop_to_sport[p] = sport

    async for doc in db.picks.aggregate([
        {"$match": SETTLED},
        {"$group": {
            "_id": {"prop":"$propType","rec":"$recommendation"},
            "n":   {"$sum":1},
            "hits":{"$sum":{"$cond":[{"$eq":["$result","hit"]},1,0]}},
            "avg_line":   {"$avg":"$line"},
            "avg_actual": {"$avg":"$actualValue"},
        }},
    ]):
        if doc["n"] < 20: continue
        n=doc["n"]; h=doc["hits"]
        p=doc["_id"]["prop"]; r=doc["_id"]["rec"]
        al=doc["avg_line"] or 0; aa=doc["avg_actual"] or 0
        sport = prop_to_sport.get(p, "❓ OTHER")
        all_rows.append({"sport":sport,"prop":p,"rec":r,"n":n,"hr":h/n*100,
                          "bias":aa-al,"avg_line":al,"avg_actual":aa})

    all_rows.sort(key=lambda x: -x["hr"])
    print(f"\n  {'SPORT':14s} {'PROP':32s} {'DIR':6s}  {'n':>4}  {'HIT':>6}  {'BIAS':>6}")
    print(f"  {'TOP BETS':─<72s}")
    for row in all_rows:
        if row["hr"] < 70: break
        f="⭐" if row["hr"]>74 else ""
        print(f"  {row['sport']:14s} {row['prop']:32s} {row['rec']:6s}  "
              f"n={row['n']:4d}  hit={row['hr']:5.1f}%  bias={row['bias']:+5.1f}  {f}")

    print(f"\n  {'WORST BETS':─<72s}")
    for row in reversed(all_rows):
        if row["hr"] > 45: break
        f="❌" if row["hr"]<35 else ""
        print(f"  {row['sport']:14s} {row['prop']:32s} {row['rec']:6s}  "
              f"n={row['n']:4d}  hit={row['hr']:5.1f}%  bias={row['bias']:+5.1f}  {f}")

async def main():
    db = mongo_client[DB_NAME]

    # ══════════════════════════════════════════════════════════════════════════
    # 1. SOCCER
    # ══════════════════════════════════════════════════════════════════════════
    soc_props = SPORT_PROPS["⚽ SOCCER"]
    sep("⚽ SOCCER — COMPLETE ANALYSIS")

    await census(db, soc_props, "⚽ SOCCER")
    await by_venue(db, soc_props, "⚽ SOCCER")
    await by_possession(db, soc_props, "⚽ SOCCER")
    await by_scenario(db, soc_props, "⚽ SOCCER")
    await by_position(db, soc_props, "⚽ SOCCER", allowed_positions=SOCCER_POSITIONS)
    await by_confidence(db, soc_props, "⚽ SOCCER", "over")
    await by_confidence(db, soc_props, "⚽ SOCCER", "under")

    # ══════════════════════════════════════════════════════════════════════════
    # 2. CS2
    # ══════════════════════════════════════════════════════════════════════════
    cs2_props = SPORT_PROPS["🎮 CS2"]
    sep("🎮 CS2 — COMPLETE ANALYSIS")

    await census(db, cs2_props, "🎮 CS2")
    await by_position(db, cs2_props, "🎮 CS2", allowed_positions=CS2_POSITIONS)
    await by_confidence(db, cs2_props, "🎮 CS2", "over")
    await by_confidence(db, cs2_props, "🎮 CS2", "under")

    # ══════════════════════════════════════════════════════════════════════════
    # 3. BASEBALL
    # ══════════════════════════════════════════════════════════════════════════
    bb_props = SPORT_PROPS["⚾ BASEBALL"]
    # baseball-specific positions only
    bb_positions = {"SP","RP","CP","P","C","1B","2B","3B","SS","LF","CF","RF","OF","DH","PH","PR"}
    sep("⚾ BASEBALL — COMPLETE ANALYSIS")

    await census(db, bb_props, "⚾ BASEBALL")
    await by_position(db, bb_props, "⚾ BASEBALL", allowed_positions=bb_positions)
    await by_confidence(db, bb_props, "⚾ BASEBALL", "over")
    await by_confidence(db, bb_props, "⚾ BASEBALL", "under")

    # ══════════════════════════════════════════════════════════════════════════
    # 4. TENNIS
    # ══════════════════════════════════════════════════════════════════════════
    tennis_props = SPORT_PROPS["🎾 TENNIS"]
    sep("🎾 TENNIS — COMPLETE ANALYSIS")
    await census(db, tennis_props, "🎾 TENNIS")

    # ══════════════════════════════════════════════════════════════════════════
    # 5. CROSS-SPORT GLOBAL RANKING (sport-labelled)
    # ══════════════════════════════════════════════════════════════════════════
    await ranked_summary(db)

    print("\n✅ All-sport analysis complete — sports cleanly separated.\n")

asyncio.run(main())
