"""INTEL Dashboard — Owner-only analytics on prediction accuracy patterns."""
from fastapi import APIRouter
from config import db, OWNER_EMAIL
from calibration import _infer_position, _game_context, LEAGUE_NAMES

router = APIRouter(prefix="/api/intel", tags=["intel"])


def _rate(h, m):
    t = h + m
    return round(h / t * 100, 1) if t else 0


def _bucket_line(line):
    """Group lines into meaningful ranges."""
    if line <= 0.5:
        return "0.5 (binary)"
    if line <= 1.5:
        return "0.5-1.5"
    if line <= 3.5:
        return "1.5-3.5"
    if line <= 5.5:
        return "3.5-5.5"
    if line <= 10:
        return "5.5-10"
    if line <= 25:
        return "10-25"
    if line <= 40:
        return "25-40"
    return "40+"


def _infer_favorite(venue, score_str):
    """Infer if player's team was favorite based on result + venue."""
    if not score_str:
        return "unknown"
    try:
        parts = score_str.replace(" ", "").split("-")
        home_goals, away_goals = int(parts[0]), int(parts[1])
        if venue == "home":
            if home_goals > away_goals:
                return "favorite_won"
            if home_goals < away_goals:
                return "underdog_lost"
            return "draw"
        else:
            if away_goals > home_goals:
                return "favorite_won"
            if away_goals < home_goals:
                return "underdog_lost"
            return "draw"
    except Exception:
        return "unknown"


@router.get("/dashboard")
async def intel_dashboard(email: str, token: str):
    """Full analytics dashboard — owner only."""
    if email.lower() != OWNER_EMAIL:
        return {"error": "Owner only"}

    # Verify session
    session = await db.sessions.find_one(
        {"email": email.lower(), "session_token": token}, {"_id": 0}
    )
    if not session:
        return {"error": "Invalid session"}

    picks = await db.picks.find(
        {"status": "settled", "result": {"$in": ["hit", "miss", "push"]}},
        {"_id": 0}
    ).to_list(5000)

    if not picks:
        return {"total": 0}

    total_h, total_m = 0, 0

    # Accumulators
    by_prop_line = {}       # "saves|1.5-3.5" → {hit, miss}
    by_exact_line = {}      # "saves|2.5" → {hit, miss}
    by_position = {}        # "goalkeeper" → {hit, miss}
    by_pos_prop = {}        # "goalkeeper|saves" → {hit, miss, errors}
    by_context = {}         # "blowout" → {hit, miss}
    by_context_prop = {}    # "blowout|saves" → {hit, miss}
    by_venue = {}           # "home" → {hit, miss}
    by_venue_prop = {}      # "home|saves" → {hit, miss}
    by_league = {}          # "39" → {hit, miss}
    by_rec = {}             # "over" → {hit, miss}
    by_prop = {}            # "saves" → {hit, miss, errors}
    by_result_type = {}     # "favorite_won" → {hit, miss}
    by_result_prop = {}     # "favorite_won|saves" → {hit, miss}
    by_conf_band = {}       # "high_70+" → {hit, miss}
    worst_lines = []        # individual miss details

    for p in picks:
        pt = p.get("propType", "unknown")
        res = p.get("result")
        line = p.get("line", 0)
        venue = p.get("venue", "unknown")
        league = str(p.get("leagueId", "unknown"))
        rec = p.get("recommendation", "unknown")
        conf = p.get("confidenceScore", 50)
        proj = p.get("projectedValue", 0)
        actual = p.get("actualValue", 0)
        score = p.get("matchScore", "")
        sport = p.get("sport", "soccer")
        position = _infer_position(pt, sport)
        context = _game_context(score, sport)
        result_type = _infer_favorite(venue, score)
        error = round(actual - proj, 1) if actual is not None and proj else None

        if res == "hit":
            total_h += 1
        elif res == "miss":
            total_m += 1
        else:
            continue

        def _add(d, key):
            if key not in d:
                d[key] = {"hit": 0, "miss": 0, "errors": []}
            d[key][res] += 1
            if error is not None:
                d[key]["errors"].append(error)

        # Core buckets
        _add(by_prop, pt)
        _add(by_prop_line, f"{pt}|{_bucket_line(line)}")
        _add(by_exact_line, f"{pt}|{line}")
        _add(by_position, position)
        _add(by_pos_prop, f"{position}|{pt}")
        _add(by_context, context)
        _add(by_context_prop, f"{context}|{pt}")
        _add(by_venue, venue)
        _add(by_venue_prop, f"{venue}|{pt}")
        _add(by_league, league)
        _add(by_rec, rec)
        _add(by_result_type, result_type)
        _add(by_result_prop, f"{result_type}|{pt}")

        band = "high_70+" if conf >= 70 else "mid_55-69" if conf >= 55 else "low_<55"
        _add(by_conf_band, band)

        # Track individual misses for worst lines
        if res == "miss":
            worst_lines.append({
                "player": p.get("playerName"),
                "team": p.get("teamName"),
                "opponent": p.get("opponentName"),
                "prop": pt,
                "line": line,
                "projected": proj,
                "actual": actual,
                "rec": rec,
                "score": score,
                "venue": venue,
                "context": context,
                "position": position,
                "confidence": conf,
            })

    def _summarize(d):
        result = {}
        for k, v in d.items():
            h, m = v["hit"], v["miss"]
            t = h + m
            errs = v.get("errors", [])
            entry = {"hits": h, "misses": m, "total": t, "rate": _rate(h, m)}
            if errs:
                entry["avgError"] = round(sum(errs) / len(errs), 1)
            result[k] = entry
        return result

    return {
        "total": total_h + total_m,
        "totalHits": total_h,
        "totalMisses": total_m,
        "overallRate": _rate(total_h, total_m),
        "byProp": _summarize(by_prop),
        "byPropLine": _summarize(by_prop_line),
        "byExactLine": _summarize(by_exact_line),
        "byPosition": _summarize(by_position),
        "byPositionProp": _summarize(by_pos_prop),
        "byContext": _summarize(by_context),
        "byContextProp": _summarize(by_context_prop),
        "byVenue": _summarize(by_venue),
        "byVenueProp": _summarize(by_venue_prop),
        "byLeague": _summarize(by_league),
        "byRec": _summarize(by_rec),
        "byResultType": _summarize(by_result_type),
        "byResultProp": _summarize(by_result_prop),
        "byConfBand": _summarize(by_conf_band),
        "worstMisses": sorted(worst_lines, key=lambda x: abs(x.get("actual", 0) - x.get("projected", 0)), reverse=True)[:20],
        "leagueNames": LEAGUE_NAMES,
    }
