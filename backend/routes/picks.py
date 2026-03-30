import json
import uuid
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

router = APIRouter(prefix="/api", tags=["picks"])

@router.post("/picks/save")
async def save_pick(req: SavePickRequest):
    session = await db.sessions.find_one({"email": req.email.lower(), "session_token": req.token}, {"_id": 0})
    if not session:
        raise HTTPException(status_code=401, detail="Invalid session")
    pick = req.pick
    pick_id = pick.get("id") or str(uuid.uuid4())[:8]
    doc = {
        "pickId": pick_id,
        "email": req.email.lower(),
        "playerId": pick.get("player", {}).get("id"),
        "playerName": pick.get("player", {}).get("name", ""),
        "teamName": pick.get("player", {}).get("team", ""),
        "teamId": pick.get("_request", {}).get("teamId", 0),
        "opponentId": pick.get("_request", {}).get("opponentId", 0),
        "opponentName": pick.get("opponent", ""),
        "leagueId": pick.get("_request", {}).get("leagueId", 0),
        "propType": pick.get("propType", ""),
        "line": pick.get("line", 0),
        "recommendation": pick.get("recommendation", "over"),
        "projectedValue": pick.get("projectedValue", 0),
        "confidenceScore": pick.get("confidenceScore", 50),
        "confidenceLevel": pick.get("confidenceLevel", "Medium"),
        "confidenceInterval": pick.get("confidenceInterval", []),
        "venue": pick.get("_request", {}).get("venue", "home"),
        "status": "live",
        "result": "pending",
        "actualValue": None,
        "matchScore": None,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "settledAt": None,
    }
    await db.picks.update_one({"pickId": pick_id, "email": req.email.lower()}, {"$set": doc}, upsert=True)
    return {"success": True, "pickId": pick_id}

@router.post("/picks/list")
async def list_picks(req: GetPicksRequest):
    session = await db.sessions.find_one({"email": req.email.lower(), "session_token": req.token}, {"_id": 0})
    if not session:
        raise HTTPException(status_code=401, detail="Invalid session")
    picks = await db.picks.find({"email": req.email.lower()}, {"_id": 0}).sort("timestamp", -1).to_list(100)
    return {"picks": picks}

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


@router.post("/picks/live-update")
async def live_update_picks(req: LiveUpdateRequest):
    """For each live pick, check if match is in progress or finished. Return current stats."""
    session = await db.sessions.find_one({"email": req.email.lower(), "session_token": req.token}, {"_id": 0})
    if not session:
        raise HTTPException(status_code=401, detail="Invalid session")

    live_picks = await db.picks.find({"email": req.email.lower(), "status": "live"}, {"_id": 0}).to_list(50)
    if not live_picks:
        return {"updates": []}

    stat_map = {
        "pass_attempts": lambda s: s.get("passes", {}).get("total"),
        "shots": lambda s: s.get("shots", {}).get("total"),
        "shots_on_target": lambda s: s.get("shots", {}).get("on"),
        "tackles": lambda s: s.get("tackles", {}).get("total"),
        "key_passes": lambda s: s.get("passes", {}).get("key"),
        "saves": lambda s: s.get("goals", {}).get("saves"),
        "interceptions": lambda s: s.get("tackles", {}).get("interceptions"),
        "blocks": lambda s: s.get("tackles", {}).get("blocks"),
        "dribbles": lambda s: s.get("dribbles", {}).get("attempts"),
        "fouls_drawn": lambda s: s.get("fouls", {}).get("drawn"),
    }

    # Group picks by team to minimize API calls
    team_picks = {}
    for pick in live_picks:
        tid = pick.get("teamId", 0)
        if tid not in team_picks:
            team_picks[tid] = []
        team_picks[tid].append(pick)

    updates = []

    async def process_team_picks(team_id, picks_for_team):
        """Find the fixture for this team and get live/final stats for each pick."""
        results = []
        try:
            # Get team's most recent/live fixtures
            fixtures = await api_football_request("fixtures", {"team": team_id, "last": 3})
            if not fixtures:
                # Also check live fixtures
                fixtures = await api_football_request("fixtures", {"live": "all"})
                if fixtures:
                    fixtures = [f for f in fixtures if
                        f.get("teams", {}).get("home", {}).get("id") == team_id or
                        f.get("teams", {}).get("away", {}).get("id") == team_id]

            if not fixtures:
                return results

            for pick in picks_for_team:
                opponent_name = pick.get("opponentName", "")
                pick_ts = pick.get("timestamp", "")

                # Find the matching fixture
                matched_fixture = None
                for f in fixtures:
                    home_name = f.get("teams", {}).get("home", {}).get("name", "")
                    away_name = f.get("teams", {}).get("away", {}).get("name", "")
                    status_short = f.get("fixture", {}).get("status", {}).get("short", "")
                    fixture_date = f.get("fixture", {}).get("date", "")

                    if not (opponent_name.lower() in home_name.lower() or opponent_name.lower() in away_name.lower()):
                        continue

                    # Must be after pick was created
                    try:
                        if pick_ts:
                            pick_dt = datetime.fromisoformat(pick_ts.replace("Z", "+00:00")) if isinstance(pick_ts, str) else datetime.fromtimestamp(pick_ts / 1000, tz=timezone.utc)
                            fix_dt = datetime.fromisoformat(fixture_date.replace("Z", "+00:00"))
                            if fix_dt < pick_dt:
                                continue
                    except Exception:
                        pass

                    matched_fixture = f
                    break

                if not matched_fixture:
                    results.append({"pickId": pick["pickId"], "matchStatus": "scheduled"})
                    continue

                fixture_id = matched_fixture.get("fixture", {}).get("id")
                status_short = matched_fixture.get("fixture", {}).get("status", {}).get("short", "")
                elapsed = matched_fixture.get("fixture", {}).get("status", {}).get("elapsed") or 0
                home_goals = matched_fixture.get("goals", {}).get("home", 0) or 0
                away_goals = matched_fixture.get("goals", {}).get("away", 0) or 0
                match_score = f"{home_goals}-{away_goals}"

                # Status categories
                live_statuses = {"1H", "2H", "ET", "BT", "P", "LIVE", "HT"}
                finished_statuses = {"FT", "AET", "PEN"}
                is_live = status_short in live_statuses
                is_finished = status_short in finished_statuses

                if not is_live and not is_finished:
                    results.append({"pickId": pick["pickId"], "matchStatus": "scheduled", "fixtureId": fixture_id})
                    continue

                # Fetch player's current in-game stats
                player_stats_data = await api_football_request("fixtures/players", {"fixture": fixture_id})
                current_value = None
                minutes_played = 0

                if player_stats_data:
                    player_id = pick.get("playerId")
                    for team_data in player_stats_data:
                        for p in team_data.get("players", []):
                            if p.get("player", {}).get("id") == player_id:
                                pstats = p.get("statistics", [{}])[0] if p.get("statistics") else {}
                                minutes_played = pstats.get("games", {}).get("minutes") or 0
                                getter = stat_map.get(pick.get("propType", ""))
                                if getter:
                                    current_value = getter(pstats)
                                break
                        if current_value is not None:
                            break

                current_value = current_value or 0
                line = pick.get("line", 0)
                recommendation = pick.get("recommendation", "over")

                # Calculate pace (extrapolate to 90 min)
                effective_elapsed = max(elapsed, 1)
                pace = round((current_value / effective_elapsed) * 90, 1) if effective_elapsed > 0 else 0

                # Calculate hit probability
                if is_finished:
                    hit_pct = 100 if ((recommendation == "over" and current_value > line) or
                                     (recommendation == "under" and current_value < line)) else 0
                    if current_value == line:
                        hit_pct = 50  # push
                else:
                    # Based on pace vs line
                    if recommendation == "over":
                        if pace > line * 1.3:
                            hit_pct = min(95, 60 + (elapsed / 90) * 35)
                        elif pace > line:
                            hit_pct = min(85, 50 + (elapsed / 90) * 30)
                        elif pace > line * 0.7:
                            hit_pct = max(15, 40 - (line - pace) / line * 30)
                        else:
                            hit_pct = max(5, 20 - (elapsed / 90) * 15)
                    else:  # under
                        if pace < line * 0.7:
                            hit_pct = min(95, 60 + (elapsed / 90) * 35)
                        elif pace < line:
                            hit_pct = min(85, 50 + (elapsed / 90) * 30)
                        elif pace < line * 1.3:
                            hit_pct = max(15, 40 - (pace - line) / max(line, 1) * 30)
                        else:
                            hit_pct = max(5, 20 - (elapsed / 90) * 15)
                    hit_pct = round(hit_pct)

                update = {
                    "pickId": pick["pickId"],
                    "matchStatus": "final" if is_finished else "live",
                    "fixtureId": fixture_id,
                    "elapsed": elapsed,
                    "currentValue": current_value,
                    "minutesPlayed": minutes_played,
                    "pace": pace,
                    "hitPct": hit_pct,
                    "matchScore": match_score,
                }

                # If finished, settle the pick in DB
                if is_finished:
                    if current_value == line:
                        result_str = "push"
                    elif (current_value > line and recommendation == "over") or \
                         (current_value < line and recommendation == "under"):
                        result_str = "hit"
                    else:
                        result_str = "miss"
                    update["result"] = result_str
                    update["actualValue"] = current_value
                    await db.picks.update_one(
                        {"pickId": pick["pickId"], "email": req.email.lower()},
                        {"$set": {"status": "settled", "result": result_str, "actualValue": current_value, "matchScore": match_score, "minutesPlayed": minutes_played, "settledAt": datetime.now(timezone.utc).isoformat()}}
                    )

                results.append(update)
        except Exception:
            pass
        return results

    # Process all teams in parallel
    tasks = [process_team_picks(tid, picks) for tid, picks in team_picks.items()]
    all_results = await aio.gather(*tasks)
    for r in all_results:
        updates.extend(r)

    return {"updates": updates}


@router.post("/settle-picks")
async def settle_picks(req: SettlePicksRequest):
    """Check match results and settle picks that have finished."""
    settled = []
    for pick in req.picks:
        if pick.get("status") != "live":
            continue

        player_id = pick.get("player", {}).get("id", 0)
        team_name = pick.get("player", {}).get("team", "")
        prop_type = pick.get("propType", "")
        opponent = pick.get("opponent", "")
        league_id = pick.get("_request", {}).get("leagueId", 39)

        stat_map = {
            "pass_attempts": lambda s: s.get("passes", {}).get("total"),
            "shots": lambda s: s.get("shots", {}).get("total"),
            "shots_on_target": lambda s: s.get("shots", {}).get("on"),
            "tackles": lambda s: s.get("tackles", {}).get("total"),
            "key_passes": lambda s: s.get("passes", {}).get("key"),
            "saves": lambda s: s.get("goals", {}).get("saves"),
            "interceptions": lambda s: s.get("tackles", {}).get("interceptions"),
            "blocks": lambda s: s.get("tackles", {}).get("blocks"),
            "dribbles": lambda s: s.get("dribbles", {}).get("attempts"),
            "fouls_drawn": lambda s: s.get("fouls", {}).get("drawn"),
        }

        try:
            # Find the player's team ID from recent data
            team_id = pick.get("_request", {}).get("teamId", 0)
            if not team_id:
                # Try to get from player search
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
                continue

            # Find the relevant finished fixture (try current and next season)
            # CRITICAL: Only match fixtures that happened AFTER the pick was created
            # to avoid settling against old meetings between the same teams
            pick_timestamp = pick.get("timestamp", 0)
            pick_created = datetime.fromtimestamp(pick_timestamp / 1000, tz=timezone.utc) if pick_timestamp else datetime.min.replace(tzinfo=timezone.utc)

            recent = None
            for s in [CURRENT_SEASON + 1, CURRENT_SEASON]:
                try:
                    data = await api_football_request("fixtures", {"team": team_id, "last": 5, "season": s})
                    if data:
                        for f in data:
                            home = f.get("teams", {}).get("home", {}).get("name", "")
                            away = f.get("teams", {}).get("away", {}).get("name", "")
                            status = f.get("fixture", {}).get("status", {}).get("short", "")
                            fixture_date_str = f.get("fixture", {}).get("date", "")

                            # Only consider finished matches
                            if status not in ("FT", "AET", "PEN"):
                                continue
                            # Must match opponent name
                            if not (opponent.lower() in home.lower() or opponent.lower() in away.lower()):
                                continue
                            # MUST have occurred AFTER the pick was saved
                            try:
                                fixture_dt = datetime.fromisoformat(fixture_date_str.replace("Z", "+00:00"))
                                if fixture_dt < pick_created:
                                    continue  # This is an OLD match, skip it
                            except Exception:
                                continue  # Can't parse date, skip to be safe

                            recent = f
                            break
                        if recent:
                            break
                except Exception:
                    continue

            if not recent:
                continue

            fixture_id = recent.get("fixture", {}).get("id")
            fixture_date = recent.get("fixture", {}).get("date", "")

            # Get player stats from fixtures/players endpoint
            fixture_players = await api_football_request("fixtures/players", {"fixture": fixture_id})
            actual_value = None

            if fixture_players:
                for team_data in fixture_players:
                    for p in team_data.get("players", []):
                        if p.get("player", {}).get("id") == player_id:
                            pstats = p.get("statistics", [{}])[0]
                            getter = stat_map.get(prop_type)
                            if getter:
                                actual_value = getter(pstats)
                            break
                    if actual_value is not None:
                        break

            if actual_value is not None:
                line = pick.get("line", 0)
                recommendation = pick.get("recommendation", "over")

                # Handle push (exact match on whole-number lines)
                if actual_value == line:
                    result_str = "push"
                elif (actual_value > line and recommendation == "over") or \
                     (actual_value < line and recommendation == "under"):
                    result_str = "hit"
                else:
                    result_str = "miss"

                settled.append({
                    "pickId": pick.get("id"),
                    "status": "settled",
                    "result": result_str,
                    "actualValue": actual_value,
                    "fixtureDate": fixture_date,
                    "matchScore": f"{recent.get('goals',{}).get('home',0)}-{recent.get('goals',{}).get('away',0)}",
                })

        except Exception:
            continue

    return {"settled": settled}

