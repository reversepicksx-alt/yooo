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
from basketball_utils import _api_get as bball_api_get, parse_player_stat, get_current_nba_season
# auto_analyze_miss_background REMOVED — was draining AI tokens on every miss settlement

router = APIRouter(prefix="/api", tags=["picks"])


def generate_tracking_id():
    """Generate a unique tracking ID for every pick."""
    return f"TRK-{uuid.uuid4().hex[:8].upper()}"


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

    # Detect sport from prediction data
    sport = pick.get("sport", "soccer")
    if not sport or sport == "soccer":
        bball_props = {"points", "rebounds", "assists", "pts_reb_ast", "pts_reb", "pts_ast", "reb_ast", "blk_stl", "steals", "blocks", "turnovers", "three_pointers", "fgm", "ftm", "fga", "fta", "tpa"}
        if normalized_prop in bball_props:
            sport = "basketball"

    doc = {
        "pickId": pick_id,
        "trackingId": tracking_id,
        "email": req.email.lower(),
        "sport": sport,
        "playerId": pick.get("player", {}).get("id"),
        "playerName": pick.get("player", {}).get("name", ""),
        "teamName": pick.get("player", {}).get("team", ""),
        "teamId": pick.get("_request", {}).get("teamId", 0),
        "opponentId": pick.get("_request", {}).get("opponentId", 0),
        "opponentName": pick.get("opponent", ""),
        "leagueId": pick.get("_request", {}).get("leagueId", 0),
        "propType": normalized_prop,
        "line": pick.get("line", 0),
        "recommendation": pick.get("recommendation", "over"),
        "projectedValue": pick.get("projectedValue", 0),
        "confidenceScore": pick.get("confidenceScore", 50),
        "confidenceLevel": pick.get("confidenceLevel", "Medium"),
        "confidenceInterval": pick.get("confidenceInterval", []),
        "venue": pick.get("_request", {}).get("venue", "home"),
        "position": pick.get("player", {}).get("position", ""),
        "role": pick.get("player", {}).get("role", ""),
        "status": "live",
        "result": "pending",
        "actualValue": None,
        "matchScore": None,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "settledAt": None,
    }
    await db.picks.update_one({"pickId": pick_id, "email": req.email.lower()}, {"$set": doc}, upsert=True)
    return {"success": True, "pickId": pick_id, "trackingId": tracking_id}

@router.post("/picks/list")
async def list_picks(req: GetPicksRequest):
    session = await db.sessions.find_one({"email": req.email.lower(), "session_token": req.token}, {"_id": 0})
    if not session:
        raise HTTPException(status_code=401, detail="Invalid session")
    picks = await db.picks.find({"email": req.email.lower()}, {"_id": 0}).sort("timestamp", -1).to_list(100)

    # Backfill trackingId and sport for any old picks that don't have them
    bball_props = {"points", "rebounds", "assists", "pts_reb_ast", "three_pointers", "fgm", "ftm", "fga", "fta", "tpa"}
    from basketball_cache import get_bball_player_by_name
    for p in picks:
        updates = {}
        if not p.get("trackingId"):
            tid = generate_tracking_id()
            p["trackingId"] = tid
            updates["trackingId"] = tid
        if not p.get("sport") or p.get("sport") == "soccer":
            prop_lower = (p.get("propType", "") or "").lower()
            # Check if this player is in the basketball cache
            if prop_lower in bball_props:
                bball_player = await get_bball_player_by_name(p.get("playerName", ""))
                if bball_player:
                    sport = "basketball"
                    p["sport"] = sport
                    updates["sport"] = sport
                    # Fix teamId/opponentId from cache
                    if bball_player.get("teamId"):
                        updates["teamId"] = bball_player["teamId"]
                        updates["teamName"] = bball_player.get("teamName", "")
                        p["teamId"] = bball_player["teamId"]
                        p["teamName"] = bball_player.get("teamName", "")
                    # Normalize propType to lowercase
                    if prop_lower != p.get("propType", ""):
                        updates["propType"] = prop_lower
                        p["propType"] = prop_lower
                elif not p.get("sport"):
                    p["sport"] = "soccer"
                    updates["sport"] = "soccer"
            elif not p.get("sport"):
                p["sport"] = "soccer"
                updates["sport"] = "soccer"
        if updates:
            await db.picks.update_one(
                {"pickId": p["pickId"], "email": req.email.lower()},
                {"$set": updates}
            )

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
    "saves": lambda s: s.get("goals", {}).get("saves"),
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

# Basketball stat extraction (from parsed player stat)
BBALL_STAT_MAP = {
    "points": "points",
    "rebounds": "rebounds",
    "assists": "assists",
    "three_pointers": "tpm",
    "fgm": "fgm",
    "ftm": "ftm",
    "fga": "fga",
    "fta": "fta",
    "tpa": "tpa",
}


def get_bball_stat_value(parsed: dict, prop_type: str):
    """Extract basketball stat value from parsed player stat."""
    # Normalize: "Pts+Reb+Ast" → "pts_reb_ast", "Points" → "points"
    pt = prop_type.lower().replace("+", "_").replace(" ", "_").replace("-", "_")
    # Also handle display labels
    label_map = {
        "pts_reb_ast": "pts_reb_ast",
        "pts_reb": "pts_reb",
        "pts_ast": "pts_ast",
        "reb_ast": "reb_ast",
        "blk_stl": "blk_stl",
        "3_pointers_made": "three_pointers",
        "3_point_fg_made": "three_pointers",
        "fg_made": "fgm",
        "ft_made": "ftm",
        "fg_attempted": "fga",
        "ft_attempted": "fta",
        "3pt_attempted": "tpa",
    }
    pt = label_map.get(pt, pt)

    if pt == "pts_reb_ast":
        return (parsed.get("points", 0) or 0) + (parsed.get("rebounds", 0) or 0) + (parsed.get("assists", 0) or 0)
    if pt == "reb_ast":
        return (parsed.get("rebounds", 0) or 0) + (parsed.get("assists", 0) or 0)
    if pt == "pts_reb":
        return (parsed.get("points", 0) or 0) + (parsed.get("rebounds", 0) or 0)
    if pt == "pts_ast":
        return (parsed.get("points", 0) or 0) + (parsed.get("assists", 0) or 0)
    if pt == "blk_stl":
        return (parsed.get("blocks", 0) or 0) + (parsed.get("steals", 0) or 0)
    field = BBALL_STAT_MAP.get(pt, pt)
    return parsed.get(field, 0) or 0


@router.post("/picks/live-update")
async def live_update_picks(req: LiveUpdateRequest):
    """For each live pick, check if match is live or finished. Return current stats.
    Handles BOTH soccer and basketball picks."""
    session = await db.sessions.find_one({"email": req.email.lower(), "session_token": req.token}, {"_id": 0})
    if not session:
        raise HTTPException(status_code=401, detail="Invalid session")

    live_picks = await db.picks.find({"email": req.email.lower(), "status": "live"}, {"_id": 0}).to_list(50)
    if not live_picks:
        return {"updates": []}

    # Split picks by sport
    soccer_picks = []
    basketball_picks = []
    for pick in live_picks:
        sport = pick.get("sport", "soccer")
        if sport == "basketball":
            basketball_picks.append(pick)
        else:
            soccer_picks.append(pick)

    updates = []

    # ── SOCCER LIVE TRACKING ──
    if soccer_picks:
        soccer_updates = await _process_soccer_live(soccer_picks, req.email.lower())
        updates.extend(soccer_updates)

    # ── BASKETBALL LIVE TRACKING ──
    if basketball_picks:
        bball_updates = await _process_basketball_live(basketball_picks, req.email.lower())
        updates.extend(bball_updates)

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


async def _build_soccer_update(pick: dict, fixture: dict, email: str) -> dict:
    """Build the live update response for a soccer pick."""
    fixture_id = fixture.get("fixture", {}).get("id")
    status_short = fixture.get("fixture", {}).get("status", {}).get("short", "")
    elapsed = fixture.get("fixture", {}).get("status", {}).get("elapsed") or 0
    home_goals = fixture.get("goals", {}).get("home", 0) or 0
    away_goals = fixture.get("goals", {}).get("away", 0) or 0
    match_score = f"{home_goals}-{away_goals}"

    live_statuses = {"1H", "2H", "ET", "BT", "P", "LIVE", "HT"}
    finished_statuses = {"FT", "AET", "PEN"}
    is_live = status_short in live_statuses
    is_finished = status_short in finished_statuses

    if not is_live and not is_finished:
        return {"pickId": pick["pickId"], "matchStatus": "scheduled", "fixtureId": fixture_id}

    # Fetch player stats
    player_stats_data = await api_football_request("fixtures/players", {"fixture": fixture_id})
    current_value = None
    minutes_played = 0

    if player_stats_data:
        player_id = pick.get("playerId")
        player_name = (pick.get("playerName") or "").lower()
        for team_data in player_stats_data:
            for p in team_data.get("players", []):
                p_id = p.get("player", {}).get("id")
                p_name = (p.get("player", {}).get("name") or "").lower()
                # Match by ID first, fallback to name substring match
                if p_id == player_id or (player_name and (player_name in p_name or p_name in player_name)):
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
    }

    if is_finished:
        result_str = _settle_result(current_value, line, recommendation)
        update["result"] = result_str
        update["actualValue"] = current_value
        await db.picks.update_one(
            {"pickId": pick["pickId"], "email": email},
            {"$set": {"status": "settled", "result": result_str, "actualValue": current_value,
                      "matchScore": match_score, "minutesPlayed": minutes_played,
                      "settledAt": datetime.now(timezone.utc).isoformat()}}
        )
    return update


async def _process_basketball_live(picks: list, email: str) -> list:
    """Process basketball picks for live updates."""
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
            season = get_current_nba_season()
            # Get team's games for today and recent
            games = await bball_api_get("games", {
                "team": team_id,
                "league": 12,
                "season": season,
            })
            # Also check WNBA
            wnba_games = await bball_api_get("games", {
                "team": team_id,
                "league": 13,
                "season": str(datetime.utcnow().year),
            })
            all_games = (games or []) + (wnba_games or [])

            if not all_games:
                for pick in picks_for_team:
                    team_results.append({"pickId": pick["pickId"], "matchStatus": "scheduled"})
                return team_results

            for pick in picks_for_team:
                opponent_name = pick.get("opponentName", "")
                matched_game = _match_basketball_game(all_games, opponent_name, team_id, pick.get("timestamp", ""))

                if not matched_game:
                    team_results.append({"pickId": pick["pickId"], "matchStatus": "scheduled"})
                    continue

                update = await _build_basketball_update(pick, matched_game, team_id, email)
                team_results.append(update)
        except Exception:
            traceback.print_exc()
        return team_results

    tasks = [process_team(tid, picks) for tid, picks in team_picks.items()]
    all_results = await aio.gather(*tasks)
    for r in all_results:
        results.extend(r)

    return results


def _match_basketball_game(games: list, opponent_name: str, team_id: int, pick_ts) -> dict:
    """Find the matching basketball game.
    A team can only play ONE game at a time, so:
    - LIVE game → ALWAYS match (no opponent check needed)
    - FINISHED game → match by opponent name + time proximity"""
    live_statuses = {"Q1", "Q2", "Q3", "Q4", "OT", "BT", "HT"}
    finished_statuses = {"FT", "AOT"}

    # First pass: ANY live game for this team (a team can only be in one live match)
    for g in games:
        status = g.get("status", {}).get("short", "")
        if status in live_statuses:
            return g

    # Second pass: finished games — use opponent name + time proximity
    opp_lower = (opponent_name or "").lower().strip()
    for g in games:
        status = g.get("status", {}).get("short", "")
        if status not in finished_statuses:
            continue
        # Try opponent name match if we have one
        if opp_lower and opp_lower != "unknown" and opp_lower != "tbd":
            home = g.get("teams", {}).get("home", {})
            away = g.get("teams", {}).get("away", {})
            opp = away if home.get("id") == team_id else home
            if not (opp_lower in opp.get("name", "").lower() or opp.get("name", "").lower() in opp_lower):
                continue
        # Check time proximity
        if pick_ts:
            try:
                if isinstance(pick_ts, str):
                    pick_dt = datetime.fromisoformat(pick_ts.replace("Z", "+00:00"))
                else:
                    pick_dt = datetime.fromtimestamp(pick_ts / 1000, tz=timezone.utc)
                game_date = g.get("date", "")
                if game_date:
                    game_dt = datetime.fromisoformat(game_date.replace("Z", "+00:00"))
                    diff_hours = abs((game_dt - pick_dt).total_seconds()) / 3600
                    if diff_hours > 48:
                        continue
            except Exception:
                pass
        return g

    return None


async def _build_basketball_update(pick: dict, game: dict, team_id: int, email: str) -> dict:
    """Build the live update response for a basketball pick."""
    game_id = game.get("id")
    status = game.get("status", {})
    status_short = status.get("short", "")
    timer = status.get("timer")

    home_score = game.get("scores", {}).get("home", {}).get("total", 0) or 0
    away_score = game.get("scores", {}).get("away", {}).get("total", 0) or 0
    match_score = f"{home_score}-{away_score}"

    live_statuses = {"Q1", "Q2", "Q3", "Q4", "OT", "BT", "HT"}
    finished_statuses = {"FT", "AOT"}
    is_live = status_short in live_statuses
    is_finished = status_short in finished_statuses

    # Build period/quarter display
    period = status_short
    if status_short == "Q1":
        period = "Q1"
    elif status_short == "Q2":
        period = "Q2"
    elif status_short == "Q3":
        period = "Q3"
    elif status_short == "Q4":
        period = "Q4"
    elif status_short == "OT":
        period = "OT"
    elif status_short == "HT":
        period = "HT"
    elif status_short == "BT":
        period = "Break"

    # Estimate elapsed minutes (48 min game)
    quarter_map = {"Q1": 12, "Q2": 24, "HT": 24, "Q3": 36, "BT": 36, "Q4": 48, "OT": 53}
    elapsed = quarter_map.get(status_short, 0)
    if timer and str(timer) != "0":
        # timer is minutes within current quarter (might be string or int)
        q_base = {"Q1": 0, "Q2": 12, "Q3": 24, "Q4": 36, "OT": 48}
        try:
            timer_val = int(timer) if isinstance(timer, str) else (timer or 0)
            if timer_val > 0:
                elapsed = q_base.get(status_short, 0) + timer_val
        except (ValueError, TypeError):
            pass

    if not is_live and not is_finished:
        return {"pickId": pick["pickId"], "matchStatus": "scheduled", "gameId": game_id}

    # Fetch player stats from this game
    player_stats = await bball_api_get("games/statistics/players", {"id": game_id})
    current_value = 0
    minutes_played = "0:00"

    print(f"[BBALL LIVE] Game {game_id} | Status: {status_short} | Stats entries: {len(player_stats) if player_stats else 0}")

    if player_stats:
        # Find our player by name matching
        player_name_lower = (pick.get("playerName") or "").lower().strip()
        last_name = player_name_lower.split()[-1] if player_name_lower else ""

        for entry in player_stats:
            parsed = parse_player_stat(entry)
            pname = parsed.get("playerName", "").lower()
            if (last_name and last_name in pname) or player_name_lower in pname or pname in player_name_lower:
                if parsed.get("teamId") == team_id or not team_id:
                    current_value = get_bball_stat_value(parsed, pick.get("propType", "points"))
                    minutes_played = parsed.get("minutes", "0:00")
                    print(f"[BBALL LIVE] Matched player: {pname} | prop: {pick.get('propType')} | value: {current_value} | parsed: pts={parsed.get('points')} reb={parsed.get('rebounds')} ast={parsed.get('assists')}")
                    break

    line = pick.get("line", 0)
    recommendation = pick.get("recommendation", "over")

    # Pace (extrapolate to 48 min)
    effective_elapsed = max(elapsed, 1)
    pace = round((current_value / effective_elapsed) * 48, 1) if effective_elapsed > 0 else 0

    hit_pct = _calc_hit_pct(current_value, line, recommendation, elapsed, 48, is_finished, pace)

    update = {
        "pickId": pick["pickId"],
        "matchStatus": "final" if is_finished else "live",
        "gameId": game_id,
        "elapsed": elapsed,
        "period": period,
        "currentValue": current_value,
        "minutesPlayed": minutes_played,
        "pace": pace,
        "hitPct": hit_pct,
        "matchScore": match_score,
        "quarter": period,
    }

    if is_finished:
        result_str = _settle_result(current_value, line, recommendation)
        update["result"] = result_str
        update["actualValue"] = current_value
        await db.picks.update_one(
            {"pickId": pick["pickId"], "email": email},
            {"$set": {"status": "settled", "result": result_str, "actualValue": current_value,
                      "matchScore": match_score, "minutesPlayed": minutes_played,
                      "settledAt": datetime.now(timezone.utc).isoformat()}}
        )
    return update


# =============================================
# SHARED HELPERS
# =============================================

def _calc_hit_pct(current_value, line, recommendation, elapsed, total_minutes, is_finished, pace):
    """Calculate hit probability percentage."""
    if is_finished:
        if current_value == line:
            return 50
        return 100 if ((recommendation == "over" and current_value > line) or
                       (recommendation == "under" and current_value < line)) else 0

    progress = elapsed / max(total_minutes, 1)
    if recommendation == "over":
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
    if current_value == line:
        return "push"
    elif (current_value > line and recommendation == "over") or \
         (current_value < line and recommendation == "under"):
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

            if sport == "basketball":
                settled_result = await _settle_basketball_pick(pick, team_id, opponent, prop_type)
            else:
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
        return {
            "pickId": pick.get("id"),
            "status": "settled",
            "result": result_str,
            "actualValue": actual_value,
            "fixtureDate": fixture_date,
            "matchScore": f"{recent.get('goals',{}).get('home',0)}-{recent.get('goals',{}).get('away',0)}",
        }

    return None


async def _settle_basketball_pick(pick, team_id, opponent, prop_type):
    """Settle a basketball pick."""
    season = get_current_nba_season()
    games = await bball_api_get("games", {"team": team_id, "league": 12, "season": season})
    if not games:
        games = await bball_api_get("games", {"team": team_id, "league": 13, "season": str(datetime.utcnow().year)})

    if not games:
        return None

    opp_lower = opponent.lower()
    matched = None
    for g in games:
        status = g.get("status", {}).get("short", "")
        if status not in ("FT", "AOT"):
            continue
        home = g.get("teams", {}).get("home", {})
        away = g.get("teams", {}).get("away", {})
        opp = away if home.get("id") == team_id else home
        if opp_lower in opp.get("name", "").lower():
            matched = g
            break

    if not matched:
        return None

    game_id = matched.get("id")
    player_stats = await bball_api_get("games/statistics/players", {"id": game_id})

    if not player_stats:
        return None

    player_name_lower = (pick.get("player", {}).get("name", "") or "").lower()
    last_name = player_name_lower.split()[-1] if player_name_lower else ""
    actual_value = None

    for entry in player_stats:
        parsed = parse_player_stat(entry)
        pname = parsed.get("playerName", "").lower()
        if last_name and last_name in pname:
            actual_value = get_bball_stat_value(parsed, prop_type)
            break

    if actual_value is not None:
        line = pick.get("line", 0)
        recommendation = pick.get("recommendation", "over")
        result_str = _settle_result(actual_value, line, recommendation)
        home_score = matched.get("scores", {}).get("home", {}).get("total", 0) or 0
        away_score = matched.get("scores", {}).get("away", {}).get("total", 0) or 0
        return {
            "pickId": pick.get("id"),
            "status": "settled",
            "result": result_str,
            "actualValue": actual_value,
            "matchScore": f"{home_score}-{away_score}",
        }

    return None
