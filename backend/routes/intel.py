"""INTEL Dashboard — Owner-only analytics on prediction accuracy patterns."""
from fastapi import APIRouter
from config import db, OWNER_EMAIL
from calibration import _game_context, LEAGUE_NAMES

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


GENERIC_POSITION_LABELS = {
    "goalkeeper": "GK", "defender": "DEF", "midfielder": "MID",
    "attacker": "FWD", "guard": "Guard", "big": "Big", "any": "Unknown",
}

# Specific positions that are already exact (no mapping needed)
EXACT_POSITIONS = {
    "GK", "CB", "LB", "RB", "LWB", "RWB",
    "CDM", "CM", "CAM", "LM", "RM", "LW", "RW",
    "CF", "ST", "SS",
    "PG", "SG", "SF", "PF", "C",  # Basketball specific
    "G", "F",  # Basketball short
    "Guard", "Forward", "Center",  # Basketball generic
}

# Sport-specific position sets to prevent cross-contamination
SOCCER_POSITIONS = {
    "GK", "CB", "LB", "RB", "LWB", "RWB",
    "CDM", "CM", "CAM", "LM", "RM", "LW", "RW",
    "CF", "ST", "SS", "DEF", "MID", "FWD",
}


def _infer_favorite(venue, score_str):
    """Infer if player's team was favorite based on result + venue."""
    if not score_str:
        return "unknown"
    try:
        parts = score_str.replace(" ", "").split("-")
        home_goals, away_goals = int(parts[0]), int(parts[1])
        player_goals = home_goals if venue == "home" else away_goals
        opp_goals = away_goals if venue == "home" else home_goals
        if player_goals > opp_goals:
            return "team_won"
        if player_goals < opp_goals:
            return "team_lost"
        return "draw"
    except Exception:
        return "unknown"


def _moneyline_bucket(venue, score_str, sport):
    """Classify the game by margin to approximate moneyline outcome."""
    if not score_str:
        return "unknown"
    try:
        parts = score_str.replace(" ", "").split("-")
        home_goals, away_goals = int(parts[0]), int(parts[1])
        player_goals = home_goals if venue == "home" else away_goals
        opp_goals = away_goals if venue == "home" else home_goals
        diff = player_goals - opp_goals
        # Soccer
        if diff >= 3:
            return "blowout_win"
        if diff > 0:
            return "win"
        if diff <= -3:
            return "blowout_loss"
        if diff < 0:
            return "loss"
        return "draw"
    except Exception:
        return "unknown"


@router.get("/dashboard")
async def intel_dashboard(email: str, token: str, sport: str = "soccer"):
    """Full analytics dashboard — owner only, filtered by sport."""
    if email.lower() != OWNER_EMAIL:
        return {"error": "Owner only"}

    session = await db.sessions.find_one(
        {"email": email.lower(), "session_token": token}, {"_id": 0}
    )
    if not session:
        return {"error": "Invalid session"}

    picks = await db.picks.find(
        {"status": "settled", "result": {"$in": ["hit", "miss", "push"]}, "sport": sport},
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
    by_result_type = {}     # "team_won" → {hit, miss}
    by_result_prop = {}     # "team_won|saves" → {hit, miss}
    by_moneyline = {}       # "blowout_win" → {hit, miss}
    by_moneyline_prop = {}  # "blowout_win|saves" → {hit, miss}
    by_conf_band = {}       # "high_70+" → {hit, miss}
    worst_lines = []        # individual miss details
    # Calibration-specific accumulators
    by_prop_rec = {}        # "saves|over" → {hit, miss, errors}
    by_prop_venue = {}      # "saves|home" → {hit, miss, errors}
    edge_buckets = {"strong": {"hit": 0, "miss": 0}, "lean": {"hit": 0, "miss": 0}, "low": {"hit": 0, "miss": 0}, "unknown": {"hit": 0, "miss": 0}}

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

        # Use stored exact position if available, validate strictly
        stored_pos = (p.get("position") or "").strip()
        if stored_pos and stored_pos.upper() in EXACT_POSITIONS:
            position = stored_pos.upper()
        elif stored_pos and stored_pos.lower() in GENERIC_POSITION_LABELS:
            position = GENERIC_POSITION_LABELS[stored_pos.lower()]
        elif stored_pos and not stored_pos.isdigit() and stored_pos not in LEAGUE_NAMES.values():
            position = stored_pos
        else:
            position = "Unknown"

        # Sport cross-contamination guard
        valid_for_sport = SOCCER_POSITIONS
        if position != "Unknown" and position not in valid_for_sport:
            position = "Unknown"

        stored_role = (p.get("role") or "").strip()
        context = _game_context(score, sport)
        result_type = _infer_favorite(venue, score)
        moneyline = _moneyline_bucket(venue, score, sport)
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
        _add(by_moneyline, moneyline)
        _add(by_moneyline_prop, f"{moneyline}|{pt}")

        band = "high_70+" if conf >= 70 else "mid_55-69" if conf >= 55 else "low_<55"
        _add(by_conf_band, band)

        # Calibration-specific tracking
        _add(by_prop_rec, f"{pt}|{rec}")
        if venue != "unknown":
            _add(by_prop_venue, f"{pt}|{venue}")

        # Edge strength bucket (from stored prediction)
        edge_str = p.get("edgeStrength", "").lower()
        if edge_str in edge_buckets:
            edge_buckets[edge_str][res] = edge_buckets[edge_str].get(res, 0) + 1
        else:
            edge_buckets["unknown"][res] = edge_buckets["unknown"].get(res, 0) + 1

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
                "role": stored_role,
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

    # Build calibration stats
    def _cal_summarize(d):
        result = {}
        for k, v in d.items():
            h, m = v.get("hit", 0), v.get("miss", 0)
            t = h + m
            if t == 0:
                continue
            errs = v.get("errors", [])
            entry = {"hits": h, "misses": m, "total": t, "rate": _rate(h, m)}
            if errs:
                entry["avgError"] = round(sum(errs) / len(errs), 1)
            result[k] = entry
        return result

    # Confidence band accuracy (for recalibration insight)
    conf_accuracy = {}
    for band_key, band_data in by_conf_band.items():
        h, m = band_data.get("hit", 0), band_data.get("miss", 0)
        t = h + m
        if t > 0:
            conf_accuracy[band_key] = {
                "hits": h, "misses": m, "total": t, "rate": _rate(h, m),
                "label": band_key.replace("_", " ").replace("high ", "High ").replace("mid ", "Med ").replace("low ", "Low "),
            }

    # Flip candidates (prop+rec combos with < 50% and 5+ samples)
    flip_candidates = []
    prop_rec_summary = _cal_summarize(by_prop_rec)
    for key, v in prop_rec_summary.items():
        if v["total"] >= 5 and v["rate"] < 50:
            parts = key.split("|")
            flip_candidates.append({
                "prop": parts[0], "rec": parts[1] if len(parts) > 1 else "?",
                "rate": v["rate"], "total": v["total"], "hits": v["hits"],
                "avgError": v.get("avgError"),
            })
    flip_candidates.sort(key=lambda x: x["rate"])

    # Edge strength performance
    edge_perf = {}
    for ek, ev in edge_buckets.items():
        h, m = ev.get("hit", 0), ev.get("miss", 0)
        t = h + m
        if t > 0:
            edge_perf[ek.upper()] = {"hits": h, "misses": m, "total": t, "rate": _rate(h, m)}

    # Error direction per prop+venue
    error_map = {}
    prop_venue_summary = _cal_summarize(by_prop_venue)
    for key, v in prop_venue_summary.items():
        if v.get("avgError") is not None and v["total"] >= 3:
            parts = key.split("|")
            error_map[key] = {
                "prop": parts[0], "venue": parts[1] if len(parts) > 1 else "?",
                "avgError": v["avgError"], "total": v["total"], "rate": v["rate"],
                "direction": "over-projecting" if v["avgError"] < 0 else "under-projecting",
            }

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
        "byMoneyline": _summarize(by_moneyline),
        "byMoneylineProp": _summarize(by_moneyline_prop),
        "byConfBand": _summarize(by_conf_band),
        "worstMisses": sorted(worst_lines, key=lambda x: abs(x.get("actual", 0) - x.get("projected", 0)), reverse=True)[:20],
        "leagueNames": LEAGUE_NAMES,
        # Calibration engine insights
        "calibration": {
            "confidenceAccuracy": conf_accuracy,
            "flipCandidates": flip_candidates,
            "edgePerformance": edge_perf,
            "errorMap": error_map,
            "propRecBreakdown": prop_rec_summary,
        },
    }



@router.get("/sheet")
async def intel_sheet(email: str, token: str, sport: str = "soccer"):
    """Flat spreadsheet view — every settled pick as a row with all dimensions."""
    if email.lower() != OWNER_EMAIL:
        return {"error": "Owner only"}

    session = await db.sessions.find_one(
        {"email": email.lower(), "session_token": token}, {"_id": 0}
    )
    if not session:
        return {"error": "Invalid session"}

    picks = await db.picks.find(
        {"status": "settled", "result": {"$in": ["hit", "miss", "push"]}, "sport": sport},
        {"_id": 0}
    ).to_list(5000)

    if not picks:
        return {"total": 0, "rows": []}

    rows = []
    total_h, total_m = 0, 0
    for p in picks:
        try:
            res = p.get("result", "miss")
            if res == "hit":
                total_h += 1
            else:
                total_m += 1

            # Safely convert numeric fields
            def _num(v):
                if v is None:
                    return 0
                try:
                    return float(v)
                except (TypeError, ValueError):
                    return 0

            proj = _num(p.get("projectedValue"))
            actual = _num(p.get("actualValue"))
            line = _num(p.get("line"))
            error = round(actual - proj, 1) if proj else 0
            rec = p.get("recommendation", "") or ""
            conf = int(_num(p.get("confidenceScore")))
            pt = p.get("propType", "") or ""
            venue = p.get("venue", "") or ""

            try:
                context = _game_context(p.get("matchScore"), sport)
            except Exception:
                context = ""

            league_id = str(p.get("leagueId", "") or "")
            league_name = LEAGUE_NAMES.get(league_id, league_id)

            # Match result
            score = p.get("matchScore", "") or ""
            match_result = ""
            if score and "-" in str(score):
                try:
                    parts = str(score).split("-")
                    gh, ga = int(parts[0].strip()), int(parts[1].strip())
                    if venue == "home":
                        match_result = "win" if gh > ga else "loss" if gh < ga else "draw"
                    elif venue == "away":
                        match_result = "win" if ga > gh else "loss" if ga < gh else "draw"
                except (TypeError, ValueError, IndexError):
                    pass

            # Position — validate strictly, reject league IDs/names that leaked in
            stored_pos = (p.get("position") or "").strip()
            position = ""
            if stored_pos:
                upper_pos = stored_pos.upper()
                if upper_pos in EXACT_POSITIONS:
                    position = upper_pos
                elif stored_pos.lower() in GENERIC_POSITION_LABELS:
                    position = GENERIC_POSITION_LABELS[stored_pos.lower()]
                elif stored_pos.isdigit():
                    position = ""  # league ID leaked into position — discard
                elif stored_pos in LEAGUE_NAMES.values():
                    position = ""  # league name leaked into position — discard
                else:
                    position = ""  # unknown value — discard to keep filter clean

            # Sport cross-contamination guard: reject positions from wrong sport
            valid_for_sport = SOCCER_POSITIONS
            if position and position not in valid_for_sport:
                position = ""

            role = (p.get("role") or "").strip()
            edge = p.get("edgeStrength", "") or ""

            # Error direction
            err_dir = ""
            if error < -0.5:
                err_dir = "over"
            elif error > 0.5:
                err_dir = "under"

            rows.append({
                "player": p.get("playerName", "") or "",
                "team": p.get("teamName", "") or "",
                "opponent": p.get("opponentName", "") or "",
                "prop": pt,
                "line": line,
                "proj": proj,
                "actual": actual,
                "error": error,
                "errDir": err_dir,
                "result": res,
                "rec": rec,
                "position": position,
                "role": role,
                "league": league_name,
                "venue": venue,
                "gameType": context,
                "matchResult": match_result,
                "score": str(score),
                "confidence": conf,
                "edge": edge,
                "timestamp": p.get("timestamp", "") or "",
            })
        except Exception as e:
            print(f"[INTEL SHEET] Skipping pick {p.get('pickId', '?')}: {e}")
            continue

    # Sort by timestamp descending (newest first)
    rows.sort(key=lambda r: str(r.get("timestamp", "")), reverse=True)

    # Grok on-the-fly resolution for any remaining empty positions
    unresolved = [r for r in rows if not r.get("position")]
    if unresolved:
        try:
            from grok_positions import resolve_positions_grok_batch
            seen = set()
            batch = []
            for r in unresolved:
                if r["player"] not in seen:
                    seen.add(r["player"])
                    batch.append({"playerName": r["player"], "sport": sport})
            if batch:
                resolved = await resolve_positions_grok_batch(batch)
                for r in rows:
                    if not r["position"] and r["player"] in resolved:
                        r["position"] = resolved[r["player"]].get("position", "")
                        r["role"] = resolved[r["player"]].get("role", r.get("role", ""))
                        # Update DB directly (awaited)
                        await db.picks.update_many(
                            {"playerName": r["player"], "$or": [{"position": {"$exists": False}}, {"position": ""}, {"position": None}]},
                            {"$set": {"position": r["position"], "role": r["role"]}}
                        )
        except Exception as e:
            print(f"[INTEL SHEET] Grok position resolve error: {e}")

    return {
        "total": total_h + total_m,
        "hits": total_h,
        "misses": total_m,
        "rate": round(total_h / (total_h + total_m) * 100, 1) if (total_h + total_m) > 0 else 0,
        "rows": rows,
    }


@router.post("/backfill-positions")
async def backfill_positions(email: str, token: str):
    """Background migration: populate position/role for existing picks. Returns immediately."""
    if email.lower() != OWNER_EMAIL:
        return {"error": "Owner only"}
    session = await db.sessions.find_one(
        {"email": email.lower(), "session_token": token}, {"_id": 0}
    )
    if not session:
        return {"error": "Invalid session"}

    import asyncio

    async def _run_backfill():
        try:
            # Step 1: Clean picks with invalid position data (league IDs/names leaked in)
            all_league_names = set(LEAGUE_NAMES.values())
            bad_picks = await db.picks.find(
                {"position": {"$exists": True, "$ne": "", "$ne": None}},
                {"_id": 0, "pickId": 1, "position": 1}
            ).to_list(5000)
            cleaned = 0
            for p in bad_picks:
                pos = (p.get("position") or "").strip()
                if pos.isdigit() or pos in all_league_names:
                    await db.picks.update_one(
                        {"pickId": p["pickId"]},
                        {"$set": {"position": "", "role": ""}}
                    )
                    cleaned += 1
            print(f"[BACKFILL] Cleaned {cleaned} picks with invalid position data")

            # Step 2: Find picks missing position data
            picks = await db.picks.find(
                {"$or": [{"position": {"$exists": False}}, {"position": ""}, {"position": None}]},
                {"_id": 0, "pickId": 1, "playerId": 1, "playerName": 1}
            ).to_list(5000)

            updated = 0
            for p in picks:
                pid = p.get("playerId")
                pname = p.get("playerName", "")
                pos_found, role_found = "", ""

                if pid:
                    cached = await db.player_positions.find_one(
                        {"playerId": pid}, {"_id": 0, "specificPosition": 1, "role": 1}
                    )
                    if cached and cached.get("specificPosition"):
                        pos_found = cached["specificPosition"]
                        role_found = cached.get("role", "")

                if not pos_found and pid:
                    pred = await db.predictions.find_one(
                        {"player.id": pid, "player.position": {"$nin": ["Unknown", "", None]}},
                        {"_id": 0, "player.position": 1, "player.role": 1}
                    )
                    if pred:
                        pos_found = pred.get("player", {}).get("position", "")
                        role_found = pred.get("player", {}).get("role", "")

                if pos_found:
                    await db.picks.update_many(
                        {"playerId": pid, "$or": [{"position": {"$exists": False}}, {"position": ""}, {"position": None}]},
                        {"$set": {"position": pos_found, "role": role_found or ""}}
                    )
                    if not pid or pid == 0:
                        await db.picks.update_many(
                            {"playerName": pname, "$or": [{"position": {"$exists": False}}, {"position": ""}, {"position": None}]},
                            {"$set": {"position": pos_found, "role": role_found or ""}}
                        )
                    updated += 1

            print(f"[BACKFILL] Complete: {updated} players updated out of {len(picks)} checked")
        except Exception as e:
            print(f"[BACKFILL] Error: {e}")

    asyncio.create_task(_run_backfill())
    return {"success": True, "picksUpdated": 0, "message": "Backfill started in background. Refresh in 30 seconds."}
