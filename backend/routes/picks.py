import json
import uuid
import unicodedata
import asyncio as aio
import traceback
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException

from config import db, CURRENT_SEASON, STAT_LAMBDA_MAP
from models import (
    SavePickRequest, GetPicksRequest, DeletePickRequest,
    CorrectPickRequest, LiveUpdateRequest, SettlePicksRequest,
)
from utils import api_football_request
# auto_analyze_miss_background REMOVED — was draining AI tokens on every miss settlement

router = APIRouter(prefix="/api", tags=["picks"])


def generate_tracking_id():
    """Generate a unique tracking ID for every pick."""
    return f"TRK-{uuid.uuid4().hex[:8].upper()}"


def normalize_player_name(name: str) -> str:
    """
    Canonical key for player name deduplication.
    Lowercases, strips accents/diacritics, trims whitespace.
    'Daley Blind' and 'D. Blind' still differ (we can't expand abbreviations
    without a DB lookup), but 'Adrian Semper' and 'A. Šemper' will both
    produce 'a. semper' / 'adrian semper' sharing the same last-name token
    used in dedup logic. Stored as playerNameKey alongside the original name.
    """
    if not name:
        return ""
    name = name.strip()
    name = unicodedata.normalize("NFD", name)
    name = "".join(c for c in name if unicodedata.category(c) != "Mn")
    return name.lower()


@router.post("/picks/save")
async def save_pick(req: SavePickRequest):
    session = await db.sessions.find_one({"email": req.email.lower(), "session_token": req.token}, {"_id": 0})
    if not session:
        raise HTTPException(status_code=401, detail="Invalid session")
    pick = req.pick
    pick_id = pick.get("id") or str(uuid.uuid4())[:8]
    tracking_id = generate_tracking_id()

    # Normalize propType for consistent storage
    raw_prop = pick.get("propType", "")
    normalized_prop = raw_prop.lower().replace("+", "_").replace(" ", "_").replace("-", "_")
    prop_label_map = {
        "pts_reb_ast": "pts_reb_ast",
        "3_pointers_made": "three_pointers",
        "3_point_fg_made": "three_pointers",
        "fg_made": "fgm", "ft_made": "ftm",
        "fg_attempted": "fga", "ft_attempted": "fta",
        "3pt_attempted": "tpa",
    }
    normalized_prop = prop_label_map.get(normalized_prop, normalized_prop)

    # Sport is soccer-only
    sport = "soccer"

    doc = {
        "pickId": pick_id,
        "trackingId": tracking_id,
        "email": req.email.lower(),
        "sport": sport,
        "playerId": pick.get("player", {}).get("id"),
        "playerName": pick.get("player", {}).get("name") or pick.get("playerName", ""),
        "playerNameKey": normalize_player_name(
            pick.get("player", {}).get("name") or pick.get("playerName", "")
        ),
        "teamName": pick.get("player", {}).get("team") or pick.get("teamName", ""),
        "teamId": pick.get("_request", {}).get("teamId", 0),
        "opponentId": pick.get("_request", {}).get("opponentId", 0),
        "opponentName": pick.get("opponent") or pick.get("opponentName", ""),
        "leagueId": pick.get("_request", {}).get("leagueId", 0),
        "propType": normalized_prop,
        "line": pick.get("line", 0),
        "recommendation": (pick.get("recommendation") or "over").lower(),
        "projectedValue": pick.get("projectedValue") or pick.get("projection") or 0,
        "confidenceScore": pick.get("confidenceScore") or pick.get("confidence") or 50,
        # rawConfidence preserves the engine's pre-calibration confidence so the
        # calibrator can train against raw values, not its own (calibrated) output.
        # Falls back to the displayed score when raw isn't sent (legacy clients).
        "rawConfidence": pick.get("rawConfidence") or pick.get("confidenceScore") or pick.get("confidence") or 50,
        "confidenceLevel": pick.get("confidenceLevel", "Medium"),
        "confidenceInterval": pick.get("confidenceInterval", []),
        "venue": pick.get("_request", {}).get("venue", "home"),
        "position": pick.get("player", {}).get("position", ""),
        "role": pick.get("player", {}).get("role", ""),
        "status": "live",
        "result": "pending",
        "actualValue": None,
        "matchScore": None,
        "coinFlip": pick.get("coinFlip", False),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "settledAt": None,
    }

    # Persist the model's projected ball-possession split so we can compare
    # projected vs actual once the match settles. Comes from the predict
    # response's `matchupOverview.expectedPossession` (mobile passes it through
    # as projHomePoss / projAwayPoss). Stored as numeric percentages 0-100.
    proj_home = pick.get("projHomePoss")
    proj_away = pick.get("projAwayPoss")
    try:
        if proj_home is not None:
            doc["projHomePoss"] = round(float(proj_home), 1)
        if proj_away is not None:
            doc["projAwayPoss"] = round(float(proj_away), 1)
    except (TypeError, ValueError):
        pass

    # Grok-powered position resolution if position is missing
    if not doc["position"] or doc["position"] in ("Unknown", "unknown"):
        try:
            from grok_positions import resolve_position_grok
            resolved = await resolve_position_grok(doc["playerName"], "soccer")
            if resolved.get("position"):
                doc["position"] = resolved["position"]
                doc["role"] = resolved.get("role", doc["role"])
        except Exception:
            pass

    await db.picks.update_one({"pickId": pick_id, "email": req.email.lower()}, {"$set": doc}, upsert=True)

    # =============================================
    # SLIP CORRELATION ANALYSIS — Same-game risk detection
    # =============================================
    correlation_warnings = []
    try:
        # Find other active picks from the same game (by team/opponent overlap)
        same_game_picks = await db.picks.find({
            "email": req.email.lower(),
            "pickId": {"$ne": pick_id},
            "status": {"$in": ["live", "pending"]},
            "$or": [
                # Same team's player
                {"teamName": doc["teamName"], "opponentName": doc["opponentName"]},
                # Opposing team's player
                {"teamName": doc["opponentName"], "opponentName": doc["teamName"]},
            ]
        }, {"_id": 0, "playerName": 1, "teamName": 1, "recommendation": 1, "propType": 1, "line": 1}).to_list(20)

        if same_game_picks:
            same_team = [p for p in same_game_picks if p.get("teamName") == doc["teamName"]]
            opp_team = [p for p in same_game_picks if p.get("teamName") == doc["opponentName"]]
            total_in_game = len(same_game_picks) + 1  # +1 for this pick

            # Check directional correlation
            all_recs = [p.get("recommendation") for p in same_game_picks] + [doc["recommendation"]]
            all_under = all(r == "under" for r in all_recs)
            all_over = all(r == "over" for r in all_recs)

            pass_props = {"pass_attempts", "passes", "key_passes", "crosses"}
            is_pass_prop = doc["propType"] in pass_props

            if total_in_game >= 3 and (all_under or all_over):
                direction = "UNDER" if all_under else "OVER"
                correlation_warnings.append({
                    "type": "CORRELATED_RISK",
                    "severity": "HIGH",
                    "message": f"You have {total_in_game} picks ALL {direction} in the same game. If game flow goes against you, ALL picks lose together.",
                })

            if same_team:
                same_dir = [p for p in same_team if p.get("recommendation") == doc["recommendation"]]
                opp_dir = [p for p in same_team if p.get("recommendation") != doc["recommendation"]]
                if same_dir:
                    names = ", ".join(p["playerName"] for p in same_dir)
                    correlation_warnings.append({
                        "type": "BOOSTING",
                        "severity": "INFO",
                        "message": f"Same team, same direction as {names}. These picks are positively correlated.",
                    })
                if opp_dir:
                    names = ", ".join(p["playerName"] for p in opp_dir)
                    correlation_warnings.append({
                        "type": "CONFLICTING",
                        "severity": "MEDIUM",
                        "message": f"Same team but OPPOSITE direction to {names}. These picks may conflict.",
                    })

            if opp_team and is_pass_prop:
                opp_pass = [p for p in opp_team if p.get("propType") in pass_props]
                if opp_pass:
                    same_dir_opp = [p for p in opp_pass if p.get("recommendation") == doc["recommendation"]]
                    if same_dir_opp:
                        names = ", ".join(p["playerName"] for p in same_dir_opp)
                        dir_label = doc["recommendation"].upper()
                        correlation_warnings.append({
                            "type": "POSSESSION_CONTRADICTION",
                            "severity": "CRITICAL",
                            "message": f"ZERO-SUM ALERT: You have {dir_label} on passes for BOTH teams ({names} + {doc['playerName']}). "
                                       f"Possession is zero-sum — if one team passes less, the other passes MORE. "
                                       f"These picks CANNOT both hit unless the game is extremely low-tempo. "
                                       f"Consider flipping the direction on one team's player.",
                        })
                        correlation_warnings.append({
                            "type": "OPPOSING_TEAMS_SAME_DIR",
                            "severity": "HIGH",
                            "message": f"Both teams' players {dir_label} on passes ({names}). In open games, one team's passes rise as the other's falls. High correlation risk.",
                        })
    except Exception as e:
        print(f"[CORRELATION] Error: {e}")

    return {
        "success": True,
        "pickId": pick_id,
        "trackingId": tracking_id,
        "correlationWarnings": correlation_warnings,
    }

@router.post("/picks/list")
async def list_picks(req: GetPicksRequest):
    session = await db.sessions.find_one({"email": req.email.lower(), "session_token": req.token}, {"_id": 0})
    if not session:
        raise HTTPException(status_code=401, detail="Invalid session")
    picks = await db.picks.find({"email": req.email.lower()}, {"_id": 0}).sort("timestamp", -1).to_list(None)

    for p in picks:
        updates = {}
        if not p.get("trackingId"):
            tid = generate_tracking_id()
            p["trackingId"] = tid
            updates["trackingId"] = tid
        if not p.get("sport"):
            p["sport"] = "soccer"
            updates["sport"] = "soccer"
        rec_raw = p.get("recommendation", "")
        if rec_raw and rec_raw != rec_raw.lower():
            p["recommendation"] = rec_raw.lower()
            updates["recommendation"] = rec_raw.lower()
        if updates:
            await db.picks.update_one(
                {"pickId": p["pickId"], "email": req.email.lower()},
                {"$set": updates}
            )

    for p in picks:
        if p.get("status") == "settled" and p.get("actualValue") is not None:
            correct = _settle_result(p["actualValue"], p.get("line", 0), p.get("recommendation", "over"))
            if correct != p.get("result"):
                p["result"] = correct
                await db.picks.update_one(
                    {"pickId": p["pickId"], "email": req.email.lower()},
                    {"$set": {"result": correct}}
                )

    needs_proj = [p for p in picks if not p.get("projectedValue")]
    if needs_proj:
        for p in needs_proj:
            try:
                pid = p.get("playerId")
                pt = p.get("propType", "")
                opp = p.get("opponentName", "")
                if pid:
                    query = {"player.id": pid, "propType": pt}
                    if opp:
                        query["opponent"] = opp
                    pred = await db.predictions.find_one(
                        query,
                        {"projectedValue": 1, "_id": 0},
                        sort=[("_created", -1)]
                    )
                    if not pred and opp:
                        pred = await db.predictions.find_one(
                            {"player.id": pid, "propType": pt},
                            {"projectedValue": 1, "_id": 0},
                            sort=[("_created", -1)]
                        )
                    if pred and pred.get("projectedValue"):
                        p["projectedValue"] = pred["projectedValue"]
                        await db.picks.update_one(
                            {"pickId": p["pickId"], "email": req.email.lower()},
                            {"$set": {"projectedValue": pred["projectedValue"]}}
                        )
            except Exception:
                pass

    live_picks = [p for p in picks if p.get("status") == "live"]
    if live_picks:
        try:
            live_updates = await _process_soccer_live(live_picks, req.email.lower())
            update_map = {u["pickId"]: u for u in live_updates if u.get("pickId")}
            for p in picks:
                upd = update_map.get(p.get("pickId"))
                if upd:
                    p["currentValue"] = upd.get("currentValue")
                    p["pace"] = upd.get("pace")
                    p["hitPct"] = upd.get("hitPct")
                    p["elapsed"] = upd.get("elapsed")
                    p["period"] = upd.get("period")
                    p["matchStatus"] = upd.get("matchStatus")
                    p["matchScore"] = upd.get("matchScore")
                    p["fixtureId"] = upd.get("fixtureId")
                    p["minutesPlayed"] = upd.get("minutesPlayed")
                    if upd.get("homeTeam"):
                        p["homeTeam"] = upd.get("homeTeam")
                    if upd.get("awayTeam"):
                        p["awayTeam"] = upd.get("awayTeam")
                    if upd.get("finalHomeGoals") is not None:
                        p["finalHomeGoals"] = upd.get("finalHomeGoals")
                    if upd.get("finalAwayGoals") is not None:
                        p["finalAwayGoals"] = upd.get("finalAwayGoals")
                    if upd.get("homePoss") is not None:
                        p["homePoss"] = upd.get("homePoss")
                    if upd.get("awayPoss") is not None:
                        p["awayPoss"] = upd.get("awayPoss")
                    if upd.get("result") and upd["result"] != "pending":
                        p["status"] = "settled"
                        p["result"] = upd["result"]
                        p["actualValue"] = upd.get("actualValue")
        except Exception:
            traceback.print_exc()

    # ── FINAL STAT REFRESH ─────────────────────────────────────────────────
    # For picks settled within the last 8 hours, re-fetch from the fixture API
    # to get the true final value (API data sometimes lags right after FT).
    # Skip picks that were manually corrected.
    try:
        now_utc = datetime.now(timezone.utc)
        recently_settled = [
            p for p in picks
            if p.get("status") == "settled"
            and not p.get("correctedManually")
            and p.get("settledAt")
            and (now_utc - datetime.fromisoformat(
                    p["settledAt"].replace("Z", "+00:00")
                 )).total_seconds() < 8 * 3600
        ]
        if recently_settled:
            # Limit to 6 picks per refresh to avoid rate-limit hammering
            for p in recently_settled[:6]:
                try:
                    team_id   = p.get("teamId") or 0
                    player_id = p.get("playerId") or 0
                    opponent  = p.get("opponentName") or ""
                    prop_type = p.get("propType") or ""
                    league_id = p.get("leagueId") or 39
                    if not player_id:
                        continue
                    refreshed = await _settle_soccer_pick(
                        p, team_id, player_id, opponent, prop_type, league_id
                    )
                    if refreshed and refreshed.get("actualValue") is not None:
                        new_val = refreshed["actualValue"]
                        old_val = p.get("actualValue")
                        # Always backfill any newly-available match metadata (score, teams, possession),
                        # even if actualValue did not change. Costs nothing and lets older settled
                        # picks pick up the new home/away team + possession fields gradually.
                        meta_set = {}
                        for fld in ("homeTeam", "awayTeam", "finalHomeGoals", "finalAwayGoals",
                                     "homePoss", "awayPoss"):
                            v = refreshed.get(fld)
                            if v is not None and v != "" and p.get(fld) != v:
                                meta_set[fld] = v
                                p[fld] = v
                        if new_val != old_val:
                            line    = p.get("line", 0)
                            rec     = p.get("recommendation", "over")
                            new_res = _settle_result(new_val, line, rec)
                            p["actualValue"] = new_val
                            p["result"]      = new_res
                            meta_set["actualValue"] = new_val
                            meta_set["result"] = new_res
                            print(f"[FINAL REFRESH] {p.get('playerName')} {prop_type}: "
                                  f"actualValue {old_val} → {new_val}, result → {new_res}")
                        if meta_set:
                            await db.picks.update_one(
                                {"pickId": p["pickId"], "email": req.email.lower()},
                                {"$set": meta_set}
                            )
                except Exception as _re:
                    print(f"[FINAL REFRESH] Error for {p.get('playerName','?')}: {_re}")
    except Exception as _fe:
        print(f"[FINAL REFRESH] Outer error: {_fe}")
    # ───────────────────────────────────────────────────────────────────────

    return {"picks": picks}


@router.get("/picks/analysis")
async def get_pick_analysis(email: str, token: str, pickId: str):
    """Fetch the original prediction analysis for a saved pick."""
    session = await db.sessions.find_one({"email": email.lower(), "session_token": token}, {"_id": 0})
    if not session:
        raise HTTPException(status_code=401, detail="Invalid session")

    pick = await db.picks.find_one({"pickId": pickId, "email": email.lower()}, {"_id": 0})
    if not pick:
        raise HTTPException(status_code=404, detail="Pick not found")

    player_id = pick.get("playerId")
    prop_type = pick.get("propType", "")

    # Fields to return from the prediction
    proj_fields = {
        "_id": 0, "reasoning": 1, "tacticalBreakdown": 1, "explanation": 1,
        "sharpSummary": 1, "scenarioAnalysis": 1, "keyEvidence": 1,
        "matchupOverview": 1, "gameFlowDynamics": 1, "sensitivityTests": 1,
        "subRisk": 1, "uncertaintyNote": 1, "consensusNote": 1,
        "projectedValue": 1, "recommendation": 1, "confidenceScore": 1,
        "confidenceLevel": 1, "confidenceInterval": 1,
        "player": 1, "opponent": 1, "propType": 1, "line": 1,
        "recentSamples": 1, "bayesianMetrics": 1,
        "playerGameLogs": 1, "tacticalAlerts": 1,
        "positionComparison": 1, "h2hPlayerStats": 1,
        "_created": 1,
    }

    prediction = None
    collection = db.predictions

    # Strategy 1: Match by player ID + prop type (most recent)
    if player_id and player_id != 0:
        prediction = await collection.find_one(
            {"player.id": player_id, "propType": prop_type},
            proj_fields,
            sort=[("_created", -1)]
        )

    # Strategy 2: Match by player name + prop type
    if not prediction:
        player_name = pick.get("playerName", "")
        if player_name:
            prediction = await collection.find_one(
                {"player.name": player_name, "propType": prop_type},
                proj_fields,
                sort=[("_created", -1)]
            )

    if not prediction:
        return {"found": False}

    prediction.pop("_id", None)
    return {"found": True, "analysis": prediction}



@router.post("/picks/delete")
async def delete_pick(req: DeletePickRequest):
    session = await db.sessions.find_one({"email": req.email.lower(), "session_token": req.token}, {"_id": 0})
    if not session:
        raise HTTPException(status_code=401, detail="Invalid session")
    await db.picks.delete_one({"pickId": req.pickId, "email": req.email.lower()})
    return {"success": True}


@router.post("/picks/correct")
async def correct_pick(req: CorrectPickRequest):
    """Manual correction for settled picks when API data was wrong."""
    session = await db.sessions.find_one({"email": req.email.lower(), "session_token": req.token}, {"_id": 0})
    if not session:
        raise HTTPException(status_code=401, detail="Invalid session")
    pick = await db.picks.find_one({"pickId": req.pickId, "email": req.email.lower()}, {"_id": 0})
    if not pick:
        raise HTTPException(status_code=404, detail="Pick not found")
    line = pick.get("line", 0)
    rec = pick.get("recommendation", "over")
    if req.actualValue == line:
        result_str = "push"
    elif (rec == "over" and req.actualValue > line) or (rec == "under" and req.actualValue < line):
        result_str = "hit"
    else:
        result_str = "miss"
    await db.picks.update_one(
        {"pickId": req.pickId, "email": req.email.lower()},
        {"$set": {"actualValue": req.actualValue, "result": result_str, "correctedManually": True}}
    )
    return {"success": True, "result": result_str, "actualValue": req.actualValue}



# =============================================
# LIVE TRACKING — Real-time in-game stats
# =============================================

# Soccer stat extraction map
SOCCER_STAT_MAP = {
    "goals": lambda s: s.get("goals", {}).get("total"),
    "assists": lambda s: s.get("goals", {}).get("assists"),
    "shots_assisted": lambda s: s.get("passes", {}).get("key"),
    "pass_attempts": lambda s: s.get("passes", {}).get("total"),
    "shots": lambda s: s.get("shots", {}).get("total"),
    "shots_on_target": lambda s: s.get("shots", {}).get("on"),
    "tackles": lambda s: s.get("tackles", {}).get("total"),
    "key_passes": lambda s: s.get("passes", {}).get("key"),
    "saves": lambda s: (s.get("goals", {}).get("saves") or 0),
    "interceptions": lambda s: s.get("tackles", {}).get("interceptions"),
    "blocks": lambda s: s.get("tackles", {}).get("blocks"),
    "dribbles": lambda s: s.get("dribbles", {}).get("attempts"),
    "dribbles_success": lambda s: s.get("dribbles", {}).get("success"),
    "fouls_drawn": lambda s: s.get("fouls", {}).get("drawn"),
    "fouls_committed": lambda s: s.get("fouls", {}).get("committed"),
    "crosses": lambda s: s.get("passes", {}).get("crosses"),
    "clearances": lambda s: s.get("tackles", {}).get("clearances"),
    "duels_won": lambda s: s.get("duels", {}).get("won"),
    "yellow_cards": lambda s: s.get("cards", {}).get("yellow"),
}

@router.post("/picks/live-update")
async def live_update_picks(req: LiveUpdateRequest):
    """For each live pick, check if match is live or finished. Return current stats.
    Handles soccer picks only."""
    session = await db.sessions.find_one({"email": req.email.lower(), "session_token": req.token}, {"_id": 0})
    if not session:
        raise HTTPException(status_code=401, detail="Invalid session")

    live_picks = await db.picks.find({"email": req.email.lower(), "status": "live"}, {"_id": 0}).to_list(50)
    if not live_picks:
        return {"updates": []}

    updates = await _process_soccer_live(live_picks, req.email.lower())
    return {"updates": updates}


async def _process_soccer_live(picks: list, email: str) -> list:
    """Process soccer picks for live updates."""
    # Group by team
    team_picks = {}
    for pick in picks:
        tid = pick.get("teamId", 0)
        if tid not in team_picks:
            team_picks[tid] = []
        team_picks[tid].append(pick)

    results = []

    async def process_team(team_id, picks_for_team):
        team_results = []
        try:
            # Get team's fixtures: LIVE first, then recent finished, then upcoming
            # "last" only returns FINISHED fixtures — it skips live games!
            # So we must also check "live" fixtures for this team directly
            from datetime import timedelta
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")

            # Fire both calls in parallel: today's fixtures + last 3 finished
            import asyncio as _aio
            live_task = api_football_request("fixtures", {"team": team_id, "date": today})
            yesterday_task = api_football_request("fixtures", {"team": team_id, "date": yesterday})
            last_task = api_football_request("fixtures", {"team": team_id, "last": 3})

            live_fixtures, yesterday_fixtures, last_fixtures = await _aio.gather(
                live_task, yesterday_task, last_task, return_exceptions=True
            )

            # Merge all fixtures, dedup by fixture ID
            all_fixtures = []
            seen_ids = set()
            for batch in [live_fixtures, yesterday_fixtures, last_fixtures]:
                if isinstance(batch, Exception) or not batch:
                    continue
                for f in batch:
                    fid = f.get("fixture", {}).get("id")
                    if fid and fid not in seen_ids:
                        seen_ids.add(fid)
                        all_fixtures.append(f)

            if not all_fixtures:
                for pick in picks_for_team:
                    team_results.append({"pickId": pick["pickId"], "matchStatus": "scheduled"})
                return team_results

            for pick in picks_for_team:
                opponent_name = pick.get("opponentName", "")
                matched_fixture = _match_soccer_fixture(all_fixtures, opponent_name, pick.get("timestamp", ""))

                if not matched_fixture:
                    team_results.append({"pickId": pick["pickId"], "matchStatus": "scheduled"})
                    continue

                update = await _build_soccer_update(pick, matched_fixture, email)
                team_results.append(update)
        except Exception:
            traceback.print_exc()
        return team_results

    tasks = [process_team(tid, picks) for tid, picks in team_picks.items()]
    all_results = await aio.gather(*tasks)
    for r in all_results:
        results.extend(r)

    return results


def _match_soccer_fixture(fixtures: list, opponent_name: str, pick_ts) -> dict:
    """Find the matching fixture for a soccer pick.
    A team can only play ONE game at a time, so:
    - If there's a LIVE game for this team, ALWAYS match it (no opponent check needed)
    - For FINISHED games, use opponent name + time proximity for accuracy."""
    live_statuses = {"1H", "2H", "ET", "BT", "P", "LIVE", "HT"}
    finished_statuses = {"FT", "AET", "PEN"}

    # First pass: find ANY live game (a team can only be in one live match)
    for f in fixtures:
        status_short = f.get("fixture", {}).get("status", {}).get("short", "")
        if status_short in live_statuses:
            return f

    # Second pass: finished games — match by opponent name + time proximity
    opp_lower = (opponent_name or "").lower().strip()
    for f in fixtures:
        status_short = f.get("fixture", {}).get("status", {}).get("short", "")
        if status_short not in finished_statuses:
            continue

        # Try opponent name match if we have one
        if opp_lower and opp_lower != "unknown" and opp_lower != "tbd":
            home_name = f.get("teams", {}).get("home", {}).get("name", "")
            away_name = f.get("teams", {}).get("away", {}).get("name", "")
            if not (opp_lower in home_name.lower() or opp_lower in away_name.lower()):
                continue

        # Check time proximity
        if pick_ts:
            try:
                if isinstance(pick_ts, str):
                    pick_dt = datetime.fromisoformat(pick_ts.replace("Z", "+00:00"))
                else:
                    pick_dt = datetime.fromtimestamp(pick_ts / 1000, tz=timezone.utc)
                fix_dt = datetime.fromisoformat(f.get("fixture", {}).get("date", "").replace("Z", "+00:00"))
                diff_hours = abs((fix_dt - pick_dt).total_seconds()) / 3600
                if diff_hours > 48:
                    continue
            except Exception:
                pass
        return f

    return None


async def _fetch_fixture_possession(fixture_id: int, home_id: int, away_id: int) -> tuple:
    """Return (home_poss, away_poss) from fixtures/statistics, or (None, None) on failure."""
    try:
        stats_data = await api_football_request("fixtures/statistics", {"fixture": fixture_id})
        if not stats_data:
            return (None, None)
        h_poss, a_poss = None, None
        for team_stats in stats_data:
            tid = team_stats.get("team", {}).get("id")
            for stat in team_stats.get("statistics", []):
                if stat.get("type") == "Ball Possession":
                    raw = str(stat.get("value", "")).replace("%", "").strip()
                    try:
                        val = int(raw)
                    except (ValueError, TypeError):
                        val = None
                    if val is not None:
                        if tid == home_id:
                            h_poss = val
                        elif tid == away_id:
                            a_poss = val
        return (h_poss, a_poss)
    except Exception:
        return (None, None)


async def _build_soccer_update(pick: dict, fixture: dict, email: str) -> dict:
    """Build the live update response for a soccer pick."""
    fixture_id = fixture.get("fixture", {}).get("id")
    status_short = fixture.get("fixture", {}).get("status", {}).get("short", "")
    elapsed = fixture.get("fixture", {}).get("status", {}).get("elapsed") or 0
    home_goals = fixture.get("goals", {}).get("home", 0) or 0
    away_goals = fixture.get("goals", {}).get("away", 0) or 0
    home_team_name = fixture.get("teams", {}).get("home", {}).get("name", "") or ""
    away_team_name = fixture.get("teams", {}).get("away", {}).get("name", "") or ""
    home_team_id = fixture.get("teams", {}).get("home", {}).get("id")
    away_team_id = fixture.get("teams", {}).get("away", {}).get("id")
    # Store from player's team perspective: player_goals-opponent_goals
    # so "Rennes 3-0 Strasbourg" shows correctly even when Rennes is away
    _pick_venue = (pick.get("venue") or "home").lower()
    _player_goals = home_goals if _pick_venue == "home" else away_goals
    _opp_goals    = away_goals if _pick_venue == "home" else home_goals
    match_score = f"{_player_goals}-{_opp_goals}"

    live_statuses = {"1H", "2H", "ET", "BT", "P", "LIVE", "HT"}
    finished_statuses = {"FT", "AET", "PEN"}
    is_live = status_short in live_statuses
    is_finished = status_short in finished_statuses

    if not is_live and not is_finished:
        return {"pickId": pick["pickId"], "matchStatus": "scheduled", "fixtureId": fixture_id}

    # Fetch player stats + fixture possession in parallel
    player_stats_data, (home_poss, away_poss) = await aio.gather(
        api_football_request("fixtures/players", {"fixture": fixture_id}),
        _fetch_fixture_possession(fixture_id, home_team_id, away_team_id),
    )
    current_value = None
    minutes_played = 0

    if player_stats_data:
        player_id = pick.get("playerId")
        player_name = (pick.get("playerName") or "").lower().strip()
        # Pre-compute name parts for flexible matching
        pname_parts = player_name.split()
        pname_last = pname_parts[-1] if pname_parts else player_name
        pname_initial = (pname_parts[0][0] + ".") if pname_parts else ""  # "J."

        def _player_name_matches(api_name: str) -> bool:
            """Match player names flexibly: full name, initial+last, last name only."""
            n = api_name.lower().strip()
            # Full name match (either direction)
            if player_name and (player_name in n or n in player_name):
                return True
            # Last name match: "tverskov" in "j. tverskov"
            if pname_last and len(pname_last) >= 4 and pname_last in n:
                return True
            # Initial + last match: "j. tverskov" matches "jeppe tverskov"
            if pname_initial and pname_last:
                if f"{pname_initial} {pname_last}" in n or n.startswith(pname_initial) and pname_last in n:
                    return True
            return False

        for team_data in player_stats_data:
            for p in team_data.get("players", []):
                p_id = p.get("player", {}).get("id")
                p_name = (p.get("player", {}).get("name") or "")
                # Match by ID first, fallback to flexible name match
                if p_id == player_id or (player_name and _player_name_matches(p_name)):
                    pstats = p.get("statistics", [{}])[0] if p.get("statistics") else {}
                    minutes_played = pstats.get("games", {}).get("minutes") or 0
                    getter = SOCCER_STAT_MAP.get(pick.get("propType", ""))
                    if getter:
                        current_value = getter(pstats)
                    break
            if current_value is not None:
                break

    current_value = current_value or 0
    line = pick.get("line", 0)
    recommendation = pick.get("recommendation", "over")

    # Pace (extrapolate to 90 min)
    effective_elapsed = max(elapsed, 1)
    pace = round((current_value / effective_elapsed) * 90, 1) if effective_elapsed > 0 else 0

    hit_pct = _calc_hit_pct(current_value, line, recommendation, elapsed, 90, is_finished, pace)

    update = {
        "pickId": pick["pickId"],
        "matchStatus": "final" if is_finished else "live",
        "fixtureId": fixture_id,
        "elapsed": elapsed,
        "period": status_short,
        "currentValue": current_value,
        "minutesPlayed": minutes_played,
        "pace": pace,
        "hitPct": hit_pct,
        "matchScore": match_score,
        "homeTeam": home_team_name,
        "awayTeam": away_team_name,
        "finalHomeGoals": home_goals,
        "finalAwayGoals": away_goals,
        "homePoss": home_poss,
        "awayPoss": away_poss,
    }

    if is_finished:
        result_str = _settle_result(current_value, line, recommendation)
        update["result"] = result_str
        update["actualValue"] = current_value
        # Store hitPct so settled pick cards show 100%/0%/50% instead of "—"
        settled_hit_pct = 100 if result_str == "hit" else (0 if result_str == "miss" else 50)
        update["hitPct"] = settled_hit_pct
        # Capture final score + scenario bucket for scenario_priors mining
        try:
            from game_script_engine import bucket_from_final_score
            _scen_bucket = bucket_from_final_score(home_goals, away_goals)
        except Exception:
            _scen_bucket = None
        _settle_set = {"status": "settled", "result": result_str, "actualValue": current_value,
                      "hitPct": settled_hit_pct, "matchScore": match_score,
                      "minutesPlayed": minutes_played,
                      "finalHomeGoals": home_goals,
                      "finalAwayGoals": away_goals,
                      "homeTeam": home_team_name,
                      "awayTeam": away_team_name,
                      "scenarioBucket": _scen_bucket,
                      "settledAt": datetime.now(timezone.utc).isoformat()}
        if home_poss is not None:
            _settle_set["homePoss"] = home_poss
        if away_poss is not None:
            _settle_set["awayPoss"] = away_poss
        await db.picks.update_one(
            {"pickId": pick["pickId"], "email": email},
            {"$set": _settle_set}
        )
    return update


# Basketball functions removed — Soccer only


# =============================================
# SHARED HELPERS
# =============================================

def _calc_hit_pct(current_value, line, recommendation, elapsed, total_minutes, is_finished, pace):
    """Calculate hit probability percentage."""
    rec = (recommendation or "").lower()
    if is_finished:
        if current_value == line:
            return 50
        return 100 if ((rec == "over" and current_value > line) or
                       (rec == "under" and current_value < line)) else 0

    progress = elapsed / max(total_minutes, 1)
    if rec == "over":
        if pace > line * 1.3:
            return min(95, round(60 + progress * 35))
        elif pace > line:
            return min(85, round(50 + progress * 30))
        elif pace > line * 0.7:
            return max(15, round(40 - (line - pace) / max(line, 1) * 30))
        else:
            return max(5, round(20 - progress * 15))
    else:
        if pace < line * 0.7:
            return min(95, round(60 + progress * 35))
        elif pace < line:
            return min(85, round(50 + progress * 30))
        elif pace < line * 1.3:
            return max(15, round(40 - (pace - line) / max(line, 1) * 30))
        else:
            return max(5, round(20 - progress * 15))


def _settle_result(current_value, line, recommendation):
    """Determine if a pick hit, missed, or pushed."""
    rec = (recommendation or "").lower()
    if current_value == line:
        return "push"
    elif (current_value > line and rec == "over") or \
         (current_value < line and rec == "under"):
        return "hit"
    else:
        return "miss"


@router.post("/settle-picks")
async def settle_picks(req: SettlePicksRequest):
    """Check match results and settle picks that have finished."""
    settled = []
    for pick in req.picks:
        if pick.get("status") != "live":
            continue

        sport = pick.get("sport", "soccer")
        player_id = pick.get("player", {}).get("id", 0)
        team_name = pick.get("player", {}).get("team", "")
        prop_type = pick.get("propType", "")
        opponent = pick.get("opponent", "")
        league_id = pick.get("_request", {}).get("leagueId", 39)

        try:
            team_id = pick.get("_request", {}).get("teamId", 0)
            settled_result = await _settle_soccer_pick(pick, team_id, player_id, opponent, prop_type, league_id)

            if settled_result:
                settled.append(settled_result)
        except Exception:
            continue

    return {"settled": settled}


async def _settle_soccer_pick(pick, team_id, player_id, opponent, prop_type, league_id):
    """Settle a soccer pick."""
    if not team_id:
        for s in [CURRENT_SEASON, CURRENT_SEASON + 1]:
            try:
                pdata = await api_football_request("players", {"id": player_id, "season": s, "league": league_id})
                if pdata:
                    stats_list = pdata[0].get("statistics", [])
                    if stats_list:
                        team_id = stats_list[-1]["team"]["id"]
                        break
            except Exception:
                continue

    if not team_id:
        return None

    pick_timestamp = pick.get("timestamp", 0)
    pick_created = datetime.fromtimestamp(pick_timestamp / 1000, tz=timezone.utc) if isinstance(pick_timestamp, (int, float)) and pick_timestamp else datetime.min.replace(tzinfo=timezone.utc)

    recent = None
    for s in [CURRENT_SEASON + 1, CURRENT_SEASON]:
        try:
            data = await api_football_request("fixtures", {"team": team_id, "last": 5, "season": s})
            if data:
                for f in data:
                    home = f.get("teams", {}).get("home", {}).get("name", "")
                    away = f.get("teams", {}).get("away", {}).get("name", "")
                    status = f.get("fixture", {}).get("status", {}).get("short", "")
                    if status not in ("FT", "AET", "PEN"):
                        continue
                    if not (opponent.lower() in home.lower() or opponent.lower() in away.lower()):
                        continue
                    recent = f
                    break
                if recent:
                    break
        except Exception:
            continue

    if not recent:
        return None

    fixture_id = recent.get("fixture", {}).get("id")
    fixture_date = recent.get("fixture", {}).get("date", "")
    fixture_players = await api_football_request("fixtures/players", {"fixture": fixture_id})
    actual_value = None

    if fixture_players:
        for team_data in fixture_players:
            for p in team_data.get("players", []):
                if p.get("player", {}).get("id") == player_id:
                    pstats = p.get("statistics", [{}])[0]
                    getter = SOCCER_STAT_MAP.get(prop_type)
                    if getter:
                        actual_value = getter(pstats)
                    break
            if actual_value is not None:
                break

    if actual_value is not None:
        line = pick.get("line", 0)
        recommendation = pick.get("recommendation", "over")
        result_str = _settle_result(actual_value, line, recommendation)
        home_goals = recent.get("goals", {}).get("home", 0) or 0
        away_goals = recent.get("goals", {}).get("away", 0) or 0
        home_team_name = recent.get("teams", {}).get("home", {}).get("name", "") or ""
        away_team_name = recent.get("teams", {}).get("away", {}).get("name", "") or ""
        home_team_id = recent.get("teams", {}).get("home", {}).get("id")
        away_team_id = recent.get("teams", {}).get("away", {}).get("id")
        home_poss, away_poss = await _fetch_fixture_possession(fixture_id, home_team_id, away_team_id)
        return {
            "pickId": pick.get("id"),
            "status": "settled",
            "result": result_str,
            "actualValue": actual_value,
            "fixtureDate": fixture_date,
            "matchScore": f"{home_goals}-{away_goals}",
            "homeTeam": home_team_name,
            "awayTeam": away_team_name,
            "finalHomeGoals": home_goals,
            "finalAwayGoals": away_goals,
            "homePoss": home_poss,
            "awayPoss": away_poss,
        }

    return None


# Basketball settlement removed — Soccer only
