import json
import uuid
import asyncio as aio
import statistics as stats_mod
import traceback
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException
from emergentintegrations.llm.chat import LlmChat, UserMessage

from openai import OpenAI

from config import (
    db, EMERGENT_LLM_KEY, XAI_API_KEY, CURRENT_SEASON,
    WOMENS_LEAGUE_IDS, STAT_FIELD_MAP, STAT_LAMBDA_MAP,
)
from models import PredictionRequest
from utils import api_football_request, get_recent_fixtures_fast, strip_accents, get_soccer_odds, decimal_to_american

router = APIRouter(prefix="/api", tags=["predict"])

@router.post("/predict")
async def predict(req: PredictionRequest):
    try:
        # Fetch player stats + recent fixtures + supplementary data ALL IN PARALLEL
        async def safe_fetch(endpoint, params, fallback=None):
            try:
                return await api_football_request(endpoint, params)
            except Exception:
                return fallback

        async def get_player_data():
            # Try to get data from multiple seasons for richer context
            all_data = None
            for s in [CURRENT_SEASON + 1, CURRENT_SEASON, CURRENT_SEASON - 1, CURRENT_SEASON - 2]:
                try:
                    data = await api_football_request("players", {"id": req.playerId, "season": s})
                    if data:
                        if all_data is None:
                            all_data = data[0]
                        else:
                            # Merge additional season stats
                            all_data.setdefault("statistics", []).extend(data[0].get("statistics", []))
                except Exception:
                    continue
            return all_data

        actual_team_id = req.teamId
        league_id = req.leagueId or 39
        ai_only_mode = (not actual_team_id or actual_team_id == 0 or not req.opponentId or req.opponentId == 0)

        # Guard: skip team/opponent API calls when IDs are missing
        safe_team_id = actual_team_id if actual_team_id and actual_team_id != 0 else None
        safe_opp_id = req.opponentId if req.opponentId and req.opponentId != 0 else None

        # Fire ALL API calls at once (optimized — kept odds for game context)
        player_data_task = get_player_data()
        async def get_team_stats_multi_season(team_id, lid):
            for s in [CURRENT_SEASON + 1, CURRENT_SEASON, CURRENT_SEASON - 1]:
                result = await safe_fetch("teams/statistics", {"team": team_id, "league": lid, "season": s})
                if result:
                    return result
            return None

        async def get_match_odds():
            """Get bookmaker odds for the specific upcoming fixture between team and opponent.
            Uses team's next fixtures (across ALL competitions) to find the correct match."""
            try:
                fixture_match = None

                # Primary: Get team's upcoming + today's fixtures across ALL competitions
                try:
                    next_fixtures = await api_football_request("fixtures", {"team": actual_team_id, "next": 10})
                    if not next_fixtures:
                        next_fixtures = []

                    # Also check today's live/scheduled fixtures (catches matches about to start or in progress)
                    from datetime import date as date_type
                    today_str = date_type.today().isoformat()
                    try:
                        today_fixtures = await api_football_request("fixtures", {"team": actual_team_id, "date": today_str})
                        if today_fixtures:
                            # Prepend today's fixtures (higher priority — game is today)
                            existing_ids = {f.get("fixture", {}).get("id") for f in next_fixtures}
                            for tf in today_fixtures:
                                if tf.get("fixture", {}).get("id") not in existing_ids:
                                    next_fixtures.insert(0, tf)
                    except Exception:
                        pass

                    if next_fixtures:
                        # Find fixtures against this specific opponent
                        opponent_matches = []
                        for nf in next_fixtures:
                            home_id = nf.get("teams", {}).get("home", {}).get("id")
                            away_id = nf.get("teams", {}).get("away", {}).get("id")
                            if req.opponentId in (home_id, away_id):
                                opponent_matches.append(nf)

                        if opponent_matches:
                            # Pick the SOONEST one (first in list — API returns date-ascending)
                            fixture_match = opponent_matches[0]
                        else:
                            # No opponent match found — take team's next match as fallback
                            fixture_match = next_fixtures[0]
                except Exception:
                    pass

                # Fallback: H2H (limited to next: 2 per API-Football max)
                if not fixture_match:
                    try:
                        h2h = await api_football_request("fixtures/headtohead", {
                            "h2h": f"{actual_team_id}-{req.opponentId}",
                            "next": 2,
                        })
                        if h2h:
                            fixture_match = h2h[0]
                    except Exception:
                        pass

                if not fixture_match:
                    return None

                fid = fixture_match.get("fixture", {}).get("id")
                result = {}
                # Extract competition context (league/cup name + round)
                match_round = fixture_match.get("league", {}).get("round", "")
                match_league = fixture_match.get("league", {}).get("name", "")
                match_date = fixture_match.get("fixture", {}).get("date", "")
                if match_round:
                    result["matchRound"] = match_round
                if match_league:
                    result["matchLeague"] = match_league
                if match_date:
                    result["matchDate"] = match_date
                try:
                    odds = await api_football_request("odds", {"fixture": fid})
                    if odds:
                        for bk in odds[0].get("bookmakers", [])[:1]:
                            for bet in bk.get("bets", []):
                                if bet.get("name") == "Match Winner":
                                    vals = {v["value"]: v["odd"] for v in bet.get("values", [])}
                                    result["bookmakerOdds"] = {
                                        "source": bk.get("name", ""),
                                        "homeWin": vals.get("Home", ""),
                                        "draw": vals.get("Draw", ""),
                                        "awayWin": vals.get("Away", ""),
                                    }
                                    # Convert to American odds
                                    try:
                                        home_dec = float(vals.get("Home", 0))
                                        away_dec = float(vals.get("Away", 0))
                                        draw_dec = float(vals.get("Draw", 0))
                                        result["americanOdds"] = {
                                            "home": decimal_to_american(home_dec) if home_dec else "",
                                            "away": decimal_to_american(away_dec) if away_dec else "",
                                            "draw": decimal_to_american(draw_dec) if draw_dec else "",
                                        }
                                        result["favorite"] = "home" if home_dec < away_dec else "away"
                                        # Game type from odds spread
                                        fav_odds = min(home_dec, away_dec)
                                        if fav_odds < 1.3:
                                            result["gameType"] = "HEAVY FAVORITE — expect dominant performance, possible early subs"
                                        elif fav_odds < 1.7:
                                            result["gameType"] = "CLEAR FAVORITE — should control the game"
                                        elif fav_odds < 2.2:
                                            result["gameType"] = "SLIGHT FAVORITE — competitive match expected"
                                        else:
                                            result["gameType"] = "PICK'EM — very close, could go either way"
                                    except Exception:
                                        result["favorite"] = "home" if float(vals.get("Home", 99)) < float(vals.get("Away", 99)) else "away"
                except Exception:
                    pass
                return result if result else None
            except Exception:
                return None

        # When in AI-only mode (missing IDs), skip API calls that would waste quota
        if ai_only_mode:
            print(f"[AI-ONLY] Running in AI-only mode for {req.playerName} — teamId={actual_team_id}, opponentId={req.opponentId}")

            async def noop_none(): return None
            async def noop_list(): return []

            player_data_task = get_player_data() if req.playerId and req.playerId != 0 else noop_none()
            team_stats_task = noop_none()
            opponent_stats_task = noop_none()
            h2h_task = noop_list()
            standings_task = noop_none()
            fixtures_task = noop_list()
            odds_task = noop_none()
        else:
            team_stats_task = get_team_stats_multi_season(actual_team_id, league_id)
            opponent_stats_task = get_team_stats_multi_season(req.opponentId, league_id)
            h2h_task = safe_fetch("fixtures/headtohead", {"h2h": f"{actual_team_id}-{req.opponentId}", "last": 10}, [])

            async def get_standings_multi_season():
                for s in [CURRENT_SEASON + 1, CURRENT_SEASON, CURRENT_SEASON - 1]:
                    result = await safe_fetch("standings", {"league": league_id, "season": s})
                    if result:
                        return result
                return None

            standings_task = get_standings_multi_season()
            fixtures_task = get_recent_fixtures_fast(actual_team_id, 40)
            odds_task = get_match_odds()

        import time as _t
        _t0 = _t.time()
        player_stats, team_stats, opponent_stats, h2h_data, standings_raw, recent_fixtures, match_odds = await aio.gather(
            player_data_task, team_stats_task, opponent_stats_task, h2h_task, standings_task, fixtures_task, odds_task
        )
        print(f"[TIMING] Wave 1: {_t.time()-_t0:.1f}s")

        if actual_team_id == 0 and player_stats:
            stats_list = player_stats.get("statistics", [])
            if stats_list:
                actual_team_id = stats_list[-1].get("team", {}).get("id", 0)

        if not league_id and player_stats:
            stats_list = player_stats.get("statistics", [])
            if stats_list:
                league_id = stats_list[-1].get("league", {}).get("id", 39)

        # Recovery: if ai_only_mode skipped fixture fetching but we now have a real team ID,
        # fetch recent fixtures retroactively so the Reverse Formula has game log data.
        if actual_team_id and actual_team_id != 0 and not recent_fixtures:
            try:
                print(f"[FIXTURE RECOVERY] Fetching fixtures for recovered teamId={actual_team_id}")
                recent_fixtures = await get_recent_fixtures_fast(actual_team_id, 40)
            except Exception as _fre:
                print(f"[FIXTURE RECOVERY] Error: {_fre}")

        standings = []
        if standings_raw:
            try:
                standings = standings_raw[0].get("league", {}).get("standings", [[]])[0]
            except (IndexError, AttributeError):
                pass

        # =============================================
        # WAVE 2: Deep per-fixture data (uses fixture IDs from Wave 1)
        # =============================================

        # 1. Per-fixture team stats (possession, shots, passes per match)
        async def fetch_fixture_team_stats(fixture_list, team_id, limit=5):
            """Fetch per-match team stats — cached in MongoDB for finished fixtures."""
            async def fetch_one(fix):
                fid = fix.get("fixtureId")
                if not fid:
                    return None
                try:
                    cache_key = f"fxt_{fid}_{team_id}"
                    cached = await db.fixture_player_cache.find_one({"_k": cache_key}, {"_id": 0, "d": 1})
                    if cached and cached.get("d"):
                        r = cached["d"]
                        r["date"] = fix.get("date", "")[:10]
                        r["opponent"] = fix.get("opponent", "")
                        r["venue"] = fix.get("venue", "")
                        r["score"] = f"{fix.get('homeGoals',0)}-{fix.get('awayGoals',0)}"
                        return r

                    data = await api_football_request("fixtures/statistics", {"fixture": fid})
                    if not data:
                        return None
                    for team_data in data:
                        if team_data.get("team", {}).get("id") == team_id:
                            raw_stats = {}
                            for s in team_data.get("statistics", []):
                                raw_stats[s.get("type", "")] = s.get("value")
                            result = {
                                "possession": raw_stats.get("Ball Possession", ""),
                                "totalShots": raw_stats.get("Total Shots"),
                                "shotsOnTarget": raw_stats.get("Shots on Goal"),
                                "shotsOffTarget": raw_stats.get("Shots off Goal"),
                                "blockedShots": raw_stats.get("Blocked Shots"),
                                "shotsInsideBox": raw_stats.get("Shots insidebox"),
                                "shotsOutsideBox": raw_stats.get("Shots outsidebox"),
                                "totalPasses": raw_stats.get("Total passes"),
                                "passAccuracy": raw_stats.get("Passes %"),
                                "accuratePasses": raw_stats.get("Passes accurate"),
                                "fouls": raw_stats.get("Fouls"),
                                "corners": raw_stats.get("Corner Kicks"),
                                "expectedGoals": raw_stats.get("expected_goals"),
                            }
                            await db.fixture_player_cache.update_one(
                                {"_k": cache_key}, {"$set": {"_k": cache_key, "d": result}}, upsert=True
                            )
                            result["date"] = fix.get("date", "")[:10]
                            result["opponent"] = fix.get("opponent", "")
                            result["venue"] = fix.get("venue", "")
                            result["score"] = f"{fix.get('homeGoals',0)}-{fix.get('awayGoals',0)}"
                            return result
                except Exception:
                    return None

            tasks = [fetch_one(fix) for fix in fixture_list[:limit]]
            results_raw = await aio.gather(*tasks, return_exceptions=True)
            return [r for r in results_raw if r and not isinstance(r, Exception)]

        # 2. Player game-by-game box scores from recent fixtures
        async def fetch_player_game_logs(fixture_list, player_id, limit=35):
            """Fetch player's individual stats — uses MongoDB cache for finished fixtures."""
            player_name_lower = strip_accents(req.playerName.lower().split()[-1]) if req.playerName else ""

            async def fetch_one_log(fix):
                fid = fix.get("fixtureId")
                if not fid:
                    return None
                try:
                    # Check MongoDB cache first
                    cache_key = f"fxp_{fid}_{player_id}"
                    cached = await db.fixture_player_cache.find_one({"_k": cache_key}, {"_id": 0, "d": 1})
                    if cached and cached.get("d"):
                        gl = cached["d"]
                        gl["date"] = fix.get("date", "")[:10]
                        gl["opponent"] = fix.get("opponent", "")
                        gl["venue"] = fix.get("venue", "")
                        gl["score"] = f"{fix.get('homeGoals',0)}-{fix.get('awayGoals',0)}"
                        gl["league"] = fix.get("league", "")
                        gl["round"] = fix.get("round", "")
                        stat_field_map = {
                            "goals": "goals_total", "assists": "goals_assists",
                            "shots_assisted": "passes_key",
                            "pass_attempts": "passes_total", "shots": "shots_total",
                            "shots_on_target": "shots_on", "tackles": "tackles_total",
                            "key_passes": "passes_key", "saves": "goals_saves",
                            "interceptions": "tackles_interceptions", "blocks": "tackles_blocks",
                            "dribbles": "dribbles_attempts", "dribbles_success": "dribbles_success",
                            "fouls_drawn": "fouls_drawn", "fouls_committed": "fouls_committed",
                            "crosses": "passes_crosses", "clearances": "tackles_clearances",
                            "duels_won": "duels_won", "yellow_cards": "cards_yellow",
                        }
                        raw_val = gl.get(stat_field_map.get(req.propType, ""), None)
                        minutes = gl.get("minutes", 0)
                        if raw_val is not None and minutes > 0:
                            gl["targetStatPer90"] = round((raw_val / minutes) * 90, 2)
                        return gl

                    data = await api_football_request("fixtures/players", {"fixture": fid})
                    if not data:
                        return None
                    matched_stats = None
                    for team_data in data:
                        for p in team_data.get("players", []):
                            pid = p.get("player", {}).get("id")
                            pname = strip_accents((p.get("player", {}).get("name") or "").lower())
                            if pid == player_id or (player_name_lower and player_name_lower in pname):
                                stats = p.get("statistics", [{}])[0] if p.get("statistics") else {}
                                minutes = stats.get("games", {}).get("minutes") or 0
                                if minutes > 0:
                                    matched_stats = stats
                                    break
                        if matched_stats:
                            break
                    if not matched_stats:
                        # Cache miss (player not found) to avoid re-fetching
                        await db.fixture_player_cache.update_one(
                            {"_k": cache_key}, {"$set": {"_k": cache_key, "d": None}}, upsert=True
                        )
                        return None
                    stats = matched_stats
                    minutes = stats.get("games", {}).get("minutes") or 0
                    rating = stats.get("games", {}).get("rating")
                    game_log = {
                        "minutes": minutes,
                        "rating": float(rating) if rating else None,
                        "passes_total": stats.get("passes", {}).get("total"),
                        "passes_key": stats.get("passes", {}).get("key"),
                        "passes_accuracy": stats.get("passes", {}).get("accuracy"),
                        "shots_total": stats.get("shots", {}).get("total"),
                        "shots_on": stats.get("shots", {}).get("on"),
                        "tackles_total": stats.get("tackles", {}).get("total"),
                        "tackles_interceptions": stats.get("tackles", {}).get("interceptions"),
                        "tackles_blocks": stats.get("tackles", {}).get("blocks"),
                        "dribbles_attempts": stats.get("dribbles", {}).get("attempts"),
                        "dribbles_success": stats.get("dribbles", {}).get("success"),
                        "fouls_drawn": stats.get("fouls", {}).get("drawn"),
                        "fouls_committed": stats.get("fouls", {}).get("committed"),
                        "duels_total": stats.get("duels", {}).get("total"),
                        "duels_won": stats.get("duels", {}).get("won"),
                        "goals_saves": stats.get("goals", {}).get("saves"),
                        "goals_total": stats.get("goals", {}).get("total"),
                        "goals_assists": stats.get("goals", {}).get("assists"),
                        "passes_crosses": stats.get("passes", {}).get("cross"),
                        "tackles_clearances": stats.get("tackles", {}).get("clearances"),
                        "cards_yellow": stats.get("cards", {}).get("yellow"),
                    }
                    # Cache the player stat data
                    await db.fixture_player_cache.update_one(
                        {"_k": cache_key}, {"$set": {"_k": cache_key, "d": game_log}}, upsert=True
                    )
                    # Add contextual fields for return
                    game_log["date"] = fix.get("date", "")[:10]
                    game_log["opponent"] = fix.get("opponent", "")
                    game_log["venue"] = fix.get("venue", "")
                    game_log["score"] = f"{fix.get('homeGoals',0)}-{fix.get('awayGoals',0)}"
                    game_log["league"] = fix.get("league", "")
                    game_log["round"] = fix.get("round", "")
                    stat_field_map = {
                        "goals": "goals_total", "assists": "goals_assists",
                        "shots_assisted": "passes_key",
                        "pass_attempts": "passes_total", "shots": "shots_total",
                        "shots_on_target": "shots_on", "tackles": "tackles_total",
                        "key_passes": "passes_key", "saves": "goals_saves",
                        "interceptions": "tackles_interceptions", "blocks": "tackles_blocks",
                        "dribbles": "dribbles_attempts", "dribbles_success": "dribbles_success",
                        "fouls_drawn": "fouls_drawn", "fouls_committed": "fouls_committed",
                        "crosses": "passes_crosses", "clearances": "tackles_clearances",
                        "duels_won": "duels_won", "yellow_cards": "cards_yellow",
                    }
                    raw_val = game_log.get(stat_field_map.get(req.propType, ""), None)
                    if raw_val is not None and minutes > 0:
                        game_log["targetStatPer90"] = round((raw_val / minutes) * 90, 2)
                    return game_log
                except Exception:
                    return None

            tasks = [fetch_one_log(fix) for fix in fixture_list[:limit]]
            results_raw = await aio.gather(*tasks, return_exceptions=True)
            return [r for r in results_raw if r and not isinstance(r, Exception)]

        # =============================================
        # POSITION COMPARISON: Same-position players vs opponent
        # =============================================
        FIXTURE_POS_MAP = {"Goalkeeper": "G", "Defender": "D", "Midfielder": "M", "Attacker": "F"}
        PROP_STAT_KEYS = {
            "pass_attempts": ("passes", "total"), "shots": ("shots", "total"),
            "shots_on_target": ("shots", "on"), "tackles": ("tackles", "total"),
            "key_passes": ("passes", "key"), "shots_assisted": ("passes", "key"),
            "saves": ("goals", "saves"),
            "interceptions": ("tackles", "interceptions"), "blocks": ("tackles", "blocks"),
            "dribbles": ("dribbles", "attempts"), "fouls_drawn": ("fouls", "drawn"),
            "goals": ("goals", "total"), "assists": ("goals", "assists"),
            "crosses": ("passes", "cross"), "clearances": ("tackles", "clearances"),
            "duels_won": ("duels", "won"), "yellow_cards": ("cards", "yellow"),
        }

        async def fetch_position_comparison(opp_fixtures, target_pos, prop_type, opponent_id, player_venue_filter, limit=10, target_specific_pos=None):
            """Fetch same-position players who played against the opponent recently.
            Filters by venue: if target player is AWAY, only show comparison players' AWAY performances.
            Also fetches possession data for each match.
            If target_specific_pos is set (e.g., 'CB'), filters out players with cached positions that don't match."""
            fixture_pos = FIXTURE_POS_MAP.get(target_pos, "")
            if not fixture_pos or not opp_fixtures:
                return []
            stat_cat, stat_sub = PROP_STAT_KEYS.get(prop_type, ("passes", "total"))
            # The comparison players' venue should match the TARGET player's venue
            # If target is AWAY, we want other players who also played AWAY against this opponent
            comp_venue = player_venue_filter  # "home" or "away"

            async def fetch_pos_from_fixture(fix):
                fid = fix.get("fixtureId")
                if not fid:
                    return []
                try:
                    # Fetch players AND fixture statistics (possession) in parallel
                    players_task = api_football_request("fixtures/players", {"fixture": fid})
                    stats_task = api_football_request("fixtures/statistics", {"fixture": fid})
                    players_data, fixture_stats_data = await aio.gather(players_task, stats_task)

                    if not players_data:
                        return []

                    # Parse possession from fixture stats
                    possession_map = {}  # team_id -> possession %
                    if fixture_stats_data:
                        for team_stats in fixture_stats_data:
                            tid = team_stats.get("team", {}).get("id")
                            for stat in team_stats.get("statistics", []):
                                if stat.get("type") == "Ball Possession":
                                    poss_str = str(stat.get("value", "0")).replace("%", "")
                                    try:
                                        possession_map[tid] = int(poss_str)
                                    except (ValueError, TypeError):
                                        pass

                    results = []
                    for team_data in players_data:
                        tid = team_data.get("team", {}).get("id")
                        team_name = team_data.get("team", {}).get("name", "")
                        if tid == opponent_id:
                            continue  # Skip opponent — we want teams who PLAYED AGAINST them

                        # Venue filter: determine if this team was home or away in this fixture
                        # The opponent's fixture list has opp_venue (opponent's venue)
                        # If opponent was HOME, the comparison team was AWAY, and vice versa
                        opp_fixture_venue = fix.get("venue", "")  # opponent's venue in this fixture
                        comp_team_venue = "away" if opp_fixture_venue == "home" else "home"
                        if comp_team_venue != comp_venue:
                            continue  # Skip — wrong venue for comparison

                        team_poss = possession_map.get(tid, None)
                        opp_poss = possession_map.get(opponent_id, None)

                        for p in team_data.get("players", []):
                            pstats = p.get("statistics", [{}])[0] if p.get("statistics") else {}
                            pos = pstats.get("games", {}).get("position", "")
                            minutes = pstats.get("games", {}).get("minutes") or 0
                            if pos != fixture_pos or minutes < 30:
                                continue
                            stat_val = pstats.get(stat_cat, {}).get(stat_sub)
                            if stat_val is None:
                                continue
                            rating = pstats.get("games", {}).get("rating")
                            p_id = p.get("player", {}).get("id")
                            p_name = p.get("player", {}).get("name", "")

                            # Look up cached specific position + role
                            cached_pr = await db.player_positions.find_one(
                                {"playerId": p_id}, {"_id": 0, "specificPosition": 1, "role": 1}
                            ) if p_id else None
                            spec_pos = (cached_pr or {}).get("specificPosition", "")
                            spec_role = (cached_pr or {}).get("role", "")

                            # Filter by specific position if target has one
                            if target_specific_pos and spec_pos and spec_pos != target_specific_pos:
                                continue  # Skip — cached position doesn't match target

                            results.append({
                                "name": p_name,
                                "team": team_name,
                                "minutes": minutes,
                                "statValue": stat_val,
                                "rating": float(rating) if rating else None,
                                "date": fix.get("date", "")[:10],
                                "per90": round((stat_val / minutes) * 90, 2) if minutes > 0 else 0,
                                "venue": comp_team_venue,
                                "position": spec_pos or pos,
                                "role": spec_role,
                                "teamPossession": team_poss,
                                "oppPossession": opp_poss,
                            })
                    return results
                except Exception:
                    return []

            tasks = [fetch_pos_from_fixture(f) for f in opp_fixtures[:limit]]
            raw_results = await aio.gather(*tasks, return_exceptions=True)
            all_players = []
            for r in raw_results:
                if isinstance(r, list):
                    all_players.extend(r)
            # Sort by stat value descending, max 1 per team for diversity, take top 7
            seen_names = set()
            seen_teams = {}
            unique = []
            for p in sorted(all_players, key=lambda x: x.get("statValue", 0), reverse=True):
                team = p.get("team", "")
                if p["name"] in seen_names:
                    continue
                if team and seen_teams.get(team, 0) >= 1:
                    continue  # Max 1 player per team
                seen_names.add(p["name"])
                if team:
                    seen_teams[team] = seen_teams.get(team, 0) + 1
                unique.append(p)
                if len(unique) >= 7:
                    break
            return unique

        # =============================================
        # VENUE-FILTERED DATA: Everything is venue-based
        # =============================================
        # If player is HOME → team's HOME games + opponent's AWAY games
        # If player is AWAY → team's AWAY games + opponent's HOME games
        player_venue = req.venue.lower()  # "home" or "away"
        opponent_venue = "away" if player_venue == "home" else "home"
        is_womens = req.leagueId in WOMENS_LEAGUE_IDS
        pronoun_note = "IMPORTANT: This is a WOMEN'S league. Use she/her/her pronouns for all players. Never use he/him/his." if is_womens else ""

        # Filter team's recent fixtures by venue
        venue_filtered_team_fixtures = [f for f in recent_fixtures if f.get("venue") == player_venue]
        # Also keep all fixtures for general context
        all_team_fixtures = recent_fixtures

        # Get opponent's recent fixtures (need fixture IDs)
        opponent_recent_raw = None
        if safe_opp_id:
            opponent_recent_raw = await api_football_request("fixtures", {"team": safe_opp_id, "last": 15})
        opponent_fixture_list = []
        if opponent_recent_raw:
            for f in opponent_recent_raw[:15]:
                opp_home_id = f.get("teams", {}).get("home", {}).get("id")
                opp_venue = "home" if opp_home_id == req.opponentId else "away"
                opponent_fixture_list.append({
                    "fixtureId": f.get("fixture", {}).get("id"),
                    "date": f.get("fixture", {}).get("date", ""),
                    "opponent": f.get("teams", {}).get("away" if opp_venue == "home" else "home", {}).get("name", "Unknown"),
                    "venue": opp_venue,
                    "homeGoals": f.get("goals", {}).get("home", 0) or 0,
                    "awayGoals": f.get("goals", {}).get("away", 0) or 0,
                })

        # Filter opponent fixtures by their venue in THIS matchup
        venue_filtered_opp_fixtures = [f for f in opponent_fixture_list if f.get("venue") == opponent_venue]

        # Wave 2: Use VENUE-FILTERED fixtures for deep stats
        # Team's last 5 HOME/AWAY games (matching this match's venue)
        team_fixture_stats_task = fetch_fixture_team_stats(
            venue_filtered_team_fixtures[:5] if len(venue_filtered_team_fixtures) >= 3 else all_team_fixtures[:5],
            actual_team_id or 40, 5
        )
        # Opponent's last 5 AWAY/HOME games (opposite venue — how they perform when visiting/hosting)
        opponent_fixture_stats_task = fetch_fixture_team_stats(
            venue_filtered_opp_fixtures[:5] if len(venue_filtered_opp_fixtures) >= 3 else opponent_fixture_list[:5],
            req.opponentId, 5
        )
        # Player game logs: VENUE-PRIORITIZED ordering
        # Search venue-matching fixtures first (away if away prop, home if home prop)
        # so we maximize relevant venue samples (target: 15-20 venue-matched games)
        venue_first_fixtures = venue_filtered_team_fixtures + [f for f in all_team_fixtures if f.get("venue") != player_venue]
        player_game_logs_task = fetch_player_game_logs(venue_first_fixtures, req.playerId, 35)

        # Position comparison task — same-position players vs this opponent
        # (started later after player_position is resolved)
        async def _empty_list():
            return []
        # =============================================
        # BUILD STRUCTURED DATA DIGEST (no AI needed — pure code extraction)
        # =============================================
        def build_data_digest():
            """Build a compact data digest directly from raw API data — no AI summarization needed."""
            parts = []

            # 1. Player basics
            if player_stats:
                pstats = player_stats.get("statistics", [{}])[0] if player_stats.get("statistics") else {}
                games_data = pstats.get("games", {})
                passes = pstats.get("passes", {})
                shots = pstats.get("shots", {})
                tackles = pstats.get("tackles", {})
                goals = pstats.get("goals", {})
                dribbles = pstats.get("dribbles", {})
                fouls = pstats.get("fouls", {})
                parts.append(f"""[PLAYER PROFILE]
- Position: {games_data.get('position', 'Unknown')} | Apps: {games_data.get('appearences', 'N/A')} | Avg Rating: {games_data.get('rating', 'N/A')}
- Avg Minutes: {(games_data.get('minutes') or 0) / max((games_data.get('appearences') or 1), 1):.0f} per game
- Passes: total={passes.get('total','N/A')}, key={passes.get('key','N/A')}, accuracy={passes.get('accuracy','N/A')}%
- Shots: total={shots.get('total','N/A')}, on_target={shots.get('on','N/A')}
- Tackles: total={tackles.get('total','N/A')}, interceptions={tackles.get('interceptions','N/A')}, blocks={tackles.get('blocks','N/A')}
- Saves: {goals.get('saves','N/A')} | Dribbles: attempts={dribbles.get('attempts','N/A')}, success={dribbles.get('success','N/A')}
- Fouls drawn: {fouls.get('drawn','N/A')}""")

            # 2. Team stats (venue-specific)
            if team_stats:
                fixtures = team_stats.get("fixtures", {})
                goals_for = team_stats.get("goals", {}).get("for", {}).get("total", {})
                goals_against = team_stats.get("goals", {}).get("against", {}).get("total", {})
                parts.append(f"""[TEAM {player_venue.upper()} PROFILE]
- Record: W{fixtures.get('wins', {}).get(player_venue, 'N/A')} D{fixtures.get('draws', {}).get(player_venue, 'N/A')} L{fixtures.get('loses', {}).get(player_venue, 'N/A')}
- Goals For ({player_venue}): {goals_for.get(player_venue, 'N/A')} | Against ({player_venue}): {goals_against.get(player_venue, 'N/A')}""")

            # 3. Opponent stats (opposite venue)
            if opponent_stats:
                opp_fix = opponent_stats.get("fixtures", {})
                opp_gf = opponent_stats.get("goals", {}).get("for", {}).get("total", {})
                opp_ga = opponent_stats.get("goals", {}).get("against", {}).get("total", {})
                parts.append(f"""[OPPONENT {opponent_venue.upper()} PROFILE]
- Record: W{opp_fix.get('wins', {}).get(opponent_venue, 'N/A')} D{opp_fix.get('draws', {}).get(opponent_venue, 'N/A')} L{opp_fix.get('loses', {}).get(opponent_venue, 'N/A')}
- Goals For ({opponent_venue}): {opp_gf.get(opponent_venue, 'N/A')} | Against ({opponent_venue}): {opp_ga.get(opponent_venue, 'N/A')}""")

            # 4. H2H
            if h2h_data:
                h2h_lines = []
                for h in h2h_data[:5]:
                    h2h_lines.append(f"  {h.get('date', '')[:10]}: {h.get('homeTeam', '')} {h.get('homeGoals', 0)}-{h.get('awayGoals', 0)} {h.get('awayTeam', '')}")
                parts.append(f"[H2H ({len(h2h_data)} matches)]\n" + "\n".join(h2h_lines))

            # 5. Standings
            if standings:
                standing_lines = [f"  {s.get('rank','')}. {s.get('team','')} — {s.get('points','')}pts (GD: {s.get('goalsDiff','')})" for s in standings[:8]]
                parts.append("[STANDINGS]\n" + "\n".join(standing_lines))

            # 6. Odds & Game Type
            if match_odds and match_odds.get("bookmakerOdds"):
                bo = match_odds["bookmakerOdds"]
                ao = match_odds.get("americanOdds", {})
                gt = match_odds.get("gameType", "")
                if ao:
                    parts.append(f"""[MONEYLINE & GAME TYPE]
- Home ({ao.get('home', '')}) | Draw ({ao.get('draw', '')}) | Away ({ao.get('away', '')})
- Favorite: {match_odds.get('favorite', 'Unknown').upper()}
- Game Type: {gt}
>>> Moneyline tells you expected game flow. Heavy favorites control possession and tempo. Underdogs may sit deep (deflating pass/shot stats for attacker props). <<<""")
                else:
                    parts.append(f"""[ODDS]
- Home: {bo.get('homeWin', 'N/A')} | Draw: {bo.get('draw', 'N/A')} | Away: {bo.get('awayWin', 'N/A')}
- Favorite: {match_odds.get('favorite', 'Unknown').upper()}""")

            return "\n\n".join(parts)

        data_digest = build_data_digest()

        # =============================================
        # MATCH DOMINANCE ENGINE: Calculate expected possession & context multiplier
        # Uses opponent-aware formula + odds adjustment for accurate matchup prediction
        # =============================================
        match_dominance = {"expectedPoss": 50.0, "oppExpectedPoss": 50.0, "multiplier": 1.0, "notes": []}

        # Wave 2: Fetch deep fixture data + Grok digest in parallel
        from grok_engine import build_grok_digest
        grok_digest_task = build_grok_digest(
            player_name=req.playerName, team_name=req.teamName or "",
            opponent_name=req.opponentName, prop_type=req.propType,
            line=req.line, venue=player_venue,
            player_stats=player_stats, team_stats=team_stats,
            opponent_stats=opponent_stats, h2h_data=h2h_data,
            match_odds=match_odds, standings=standings,
            player_game_logs=[], team_fixture_stats=[],
            opponent_fixture_stats=[], match_dominance={},
            sport="soccer"
        )
        all_wave2 = aio.gather(
            team_fixture_stats_task, opponent_fixture_stats_task, player_game_logs_task,
            grok_digest_task,
            return_exceptions=True
        )
        try:
            results = await aio.wait_for(all_wave2, timeout=15)
        except aio.TimeoutError:
            results = [None, None, None, None]

        team_fixture_stats = results[0] if not isinstance(results[0], (Exception, type(None))) else []
        opponent_fixture_stats = results[1] if not isinstance(results[1], (Exception, type(None))) else []
        player_game_logs = results[2] if not isinstance(results[2], (Exception, type(None))) else []
        grok_digest = results[3] if len(results) > 3 and not isinstance(results[3], (Exception, type(None))) else ""

        # =============================================
        # SEASON STATS FALLBACK: When no fixture-level game logs exist,
        # synthesize approximate per-game logs from the player's season aggregate.
        # This ensures the Reverse Formula has real data instead of using the line as prior.
        # =============================================
        if not player_game_logs and player_stats:
            try:
                pstats_list = player_stats.get("statistics", [])
                if pstats_list:
                    pstats = pstats_list[0]
                    games_data = pstats.get("games", {})
                    appearances = games_data.get("appearences") or 0
                    minutes_total = games_data.get("minutes") or 0
                    avg_minutes = round(minutes_total / appearances, 1) if appearances > 0 else 70
                    stat_field_map_season = {
                        "pass_attempts": ("passes", "total"),
                        "shots": ("shots", "total"),
                        "shots_on_target": ("shots", "on"),
                        "tackles": ("tackles", "total"),
                        "key_passes": ("passes", "key"),
                        "shots_assisted": ("passes", "key"),
                        "saves": ("goals", "saves"),
                        "interceptions": ("tackles", "interceptions"),
                        "blocks": ("tackles", "blocks"),
                        "dribbles": ("dribbles", "attempts"),
                        "fouls_drawn": ("fouls", "drawn"),
                        "fouls_committed": ("fouls", "committed"),
                        "crosses": ("passes", "crosses"),
                        "goals": ("goals", "total"),
                        "assists": ("goals", "assists"),
                        "yellow_cards": ("cards", "yellow"),
                        "duels_won": ("duels", "won"),
                    }
                    season_stat_keys = stat_field_map_season.get(req.propType)
                    season_total = None
                    if season_stat_keys:
                        section, key = season_stat_keys
                        season_total = pstats.get(section, {}).get(key)
                    if season_total is not None and appearances >= 3:
                        per_game_avg = round(season_total / appearances, 2)
                        import random as _rng
                        _seed_rng = _rng.Random(f"{req.playerId}_{req.propType}_{season_total}_{appearances}")
                        synthetic_logs = []
                        for i in range(min(appearances, 10)):
                            jitter = _seed_rng.uniform(-per_game_avg * 0.08, per_game_avg * 0.08)
                            val = max(0, round(per_game_avg + jitter, 1))
                            synthetic_logs.append({
                                "targetStat": val,
                                "passes_total": val if req.propType in ("pass_attempts", "passes") else None,
                                "minutes": avg_minutes,
                                "venue": player_venue,
                                "date": "",
                                "opponent": "",
                                "_synthetic": True,
                            })
                        player_game_logs = synthetic_logs
                        print(f"[SEASON FALLBACK] {req.playerName}/{req.propType}: no fixture logs, synthesized {len(synthetic_logs)} entries from season avg={per_game_avg} ({appearances} apps)")
            except Exception as _sf_err:
                print(f"[SEASON FALLBACK] Error: {_sf_err}")

        # =============================================
        # MATCH DOMINANCE: Opponent-aware possession + context multiplier
        # =============================================
        def compute_match_dominance(team_stats_list, opp_stats_list, odds, is_home, standing_data):
            """Compute expected possession using opponent-aware model + odds adjustment."""
            dom = {"expectedPoss": 50.0, "oppExpectedPoss": 50.0, "multiplier": 1.0, "notes": []}

            # 1. Get each team's average possession from fixture stats
            def avg_poss(sl):
                vals = []
                for s in (sl or []):
                    p = s.get("possession")
                    if p is not None:
                        try:
                            vals.append(float(str(p).replace("%", "")))
                        except (ValueError, TypeError):
                            pass
                return round(sum(vals) / len(vals), 1) if vals else None

            team_avg = avg_poss(team_stats_list)
            opp_avg = avg_poss(opp_stats_list)

            if team_avg is not None and opp_avg is not None:
                opp_concedes = 100.0 - opp_avg

                if opp_avg > 57:
                    # Possession-dominant opponent: their style DICTATES the match
                    # At 57%: 60/40 opponent-weighted → At 68%+: 90/10 opponent-weighted
                    extremity = min((opp_avg - 57) / 11.0, 1.0)
                    opp_weight = 0.60 + extremity * 0.30
                    team_weight = 1.0 - opp_weight
                    base_poss = team_weight * team_avg + opp_weight * opp_concedes
                    dom["notes"].append(f"Possession monster: opp avg {opp_avg:.0f}% → weight {opp_weight*100:.0f}% opp-driven (raw base {base_poss:.1f}%)")
                else:
                    # Normal matchup: symmetric 50/50 blend
                    base_poss = (team_avg + opp_concedes) / 2.0

                # Home advantage — dampened against possession monsters
                if is_home:
                    home_boost = 2.5
                    if opp_avg > 60:
                        dampen = min((opp_avg - 60) / 10.0, 0.7)
                        home_boost *= (1.0 - dampen)
                        dom["notes"].append(f"Home poss boost dampened: {home_boost:.1f}% (vs {opp_avg:.0f}% opp)")
                    base_poss += home_boost
                else:
                    base_poss -= 1.0

                # Standings quality gap
                if standing_data:
                    team_rank = standing_data.get("teamRank")
                    opp_rank = standing_data.get("oppRank")
                    if team_rank and opp_rank:
                        gap = opp_rank - team_rank  # positive = team is higher
                        quality_adj = min(4.0, max(-4.0, gap * 0.4))
                        base_poss += quality_adj
                        if abs(quality_adj) > 1:
                            dom["notes"].append(f"Standings gap (#{team_rank} vs #{opp_rank}): {quality_adj:+.1f}% poss adj")

                # Odds-based dominance adjustment
                # IMPORTANT: Odds predict WHO WINS, not WHO HAS POSSESSION.
                # A possession-dominant team like Barcelona can have 65% possession even as underdogs.
                # So we REDUCE the odds impact when the opponent is a known possession team.
                if odds and odds.get("bookmakerOdds"):
                    try:
                        home_odds = float(odds["bookmakerOdds"].get("homeWin", 3.0))
                        away_odds = float(odds["bookmakerOdds"].get("awayWin", 3.0))
                        team_odds = home_odds if is_home else away_odds
                        opp_odds = away_odds if is_home else home_odds

                        team_prob = 1.0 / max(team_odds, 1.01)
                        opp_prob = 1.0 / max(opp_odds, 1.01)
                        prob_diff = team_prob - opp_prob

                        # Dampen odds signal when opponent is a possession-heavy team (55%+ avg)
                        # Barcelona/Man City averaging 63% possession should override "team is favorite"
                        odds_dampener = 1.0
                        if opp_avg and opp_avg >= 57:
                            odds_dampener = 0.3  # 70% reduction — opponent style dominates
                            dom["notes"].append(f"Opponent possession-dominant ({opp_avg:.0f}% avg): odds signal dampened")
                        elif opp_avg and opp_avg >= 53:
                            odds_dampener = 0.6  # 40% reduction

                        odds_adj = round(prob_diff * 12 * odds_dampener, 1)
                        odds_adj = min(7.0, max(-7.0, odds_adj))
                        base_poss += odds_adj
                        if abs(odds_adj) > 1:
                            dom["notes"].append(f"Odds signal (team={team_odds:.2f}, opp={opp_odds:.2f}): {odds_adj:+.1f}% poss adj")
                    except Exception:
                        pass

                # Clamp to realistic range: 30-75%
                base_poss = min(75.0, max(30.0, base_poss))
                opp_poss = 100.0 - base_poss

                dom["expectedPoss"] = round(base_poss, 1)
                dom["oppExpectedPoss"] = round(opp_poss, 1)
                dom["teamSeasonAvg"] = team_avg
                dom["oppSeasonAvg"] = opp_avg

                # Match dominance multiplier for prop adjustments
                # RATIO-BASED: Pass attempts scale proportionally with possession share.
                # If a team normally has 50% possession but expects 35% in this match,
                # their players will have ~30% fewer passes (35/50 = 0.70).
                poss_ratio = base_poss / team_avg if team_avg > 0 else 1.0
                poss_diff = base_poss - team_avg
                PASS_PROPS = {"pass_attempts", "key_passes", "crosses", "passes"}
                DEF_PROPS = {"tackles", "interceptions", "blocks", "clearances"}

                if req.propType in PASS_PROPS:
                    # Ratio-based scaling — capped at ±35% to prevent extreme outliers
                    raw_adj = poss_ratio - 1.0  # e.g., 0.70 - 1.0 = -0.30 (30% reduction)
                    capped_adj = max(-0.35, min(0.35, raw_adj))
                    dom["multiplier"] = round(1.0 + capped_adj, 3)
                    if abs(capped_adj) > 0.03:
                        direction = "boost" if capped_adj > 0 else "drop"
                        dom["notes"].append(f"Pass volume {direction}: expected {base_poss:.0f}% poss vs {team_avg:.0f}% avg (ratio={poss_ratio:.2f}) → {capped_adj*100:+.0f}%")
                elif req.propType in DEF_PROPS:
                    # Defensive props scale INVERSELY with possession — less ball = more defending
                    inverse_ratio = (100.0 - base_poss) / (100.0 - team_avg) if team_avg < 100 else 1.0
                    raw_adj = inverse_ratio - 1.0
                    capped_adj = max(-0.25, min(0.25, raw_adj))
                    dom["multiplier"] = round(1.0 + capped_adj, 3)
                    if abs(capped_adj) > 0.03:
                        direction = "boost" if capped_adj > 0 else "drop"
                        dom["notes"].append(f"Def action {direction}: expected {100-base_poss:.0f}% without ball vs {100-team_avg:.0f}% avg → {capped_adj*100:+.0f}%")
                elif req.propType in {"shots", "shots_on_target"}:
                    # Shot props scale with possession but less aggressively
                    raw_adj = (poss_ratio - 1.0) * 0.6  # 60% of possession ratio
                    capped_adj = max(-0.20, min(0.20, raw_adj))
                    dom["multiplier"] = round(1.0 + capped_adj, 3)
                    if abs(capped_adj) > 0.03:
                        dom["notes"].append(f"Shot volume adj from possession ratio → {capped_adj*100:+.0f}%")

            return dom

        # Compute standings data for match dominance
        standing_data = {}
        if standings:
            for s in standings:
                s_team = s.get("team", "")
                s_team_name = s_team.get("name", "") if isinstance(s_team, dict) else str(s_team)
                s_team_id = s_team.get("id", "") if isinstance(s_team, dict) else s.get("team_id", "")
                if s_team_name.lower() == req.teamName.lower() or str(s_team_id) == str(req.teamId):
                    standing_data["teamRank"] = s.get("rank")
                if s_team_name.lower() == req.opponentName.lower() or str(s_team_id) == str(req.opponentId):
                    standing_data["oppRank"] = s.get("rank")

        match_dominance = compute_match_dominance(
            team_fixture_stats, opponent_fixture_stats, match_odds,
            player_venue == "home", standing_data
        )
        if match_dominance.get("notes"):
            print(f"[MATCH DOMINANCE] {req.playerName}: poss={match_dominance['expectedPoss']}%, mult={match_dominance['multiplier']}, {' | '.join(match_dominance['notes'])}")

        # =============================================
        # GAME TEMPO ESTIMATION — Expected match intensity
        # A 2-2 draw = high tempo → both teams pass MORE.
        # A 0-0 grind = low tempo → both teams pass LESS.
        # This adjusts the dominance multiplier based on expected total game activity.
        # =============================================
        game_tempo = {"expectedTempo": "normal", "tempoMultiplier": 1.0, "notes": []}
        try:
            # Signal 1: Both teams' goals-per-game from team stats
            team_gpg = 0.0
            opp_gpg = 0.0
            team_ga_pg = 0.0
            opp_ga_pg = 0.0
            if team_stats:
                fixtures_played = team_stats.get("fixtures", {})
                total_played = (fixtures_played.get("played", {}).get("total") or 0)
                goals_for = team_stats.get("goals", {}).get("for", {}).get("total", {}).get("total", 0) or 0
                goals_against = team_stats.get("goals", {}).get("against", {}).get("total", {}).get("total", 0) or 0
                if total_played > 0:
                    team_gpg = goals_for / total_played
                    team_ga_pg = goals_against / total_played
            if opponent_stats:
                opp_played = (opponent_stats.get("fixtures", {}).get("played", {}).get("total") or 0)
                opp_gf = opponent_stats.get("goals", {}).get("for", {}).get("total", {}).get("total", 0) or 0
                opp_ga = opponent_stats.get("goals", {}).get("against", {}).get("total", {}).get("total", 0) or 0
                if opp_played > 0:
                    opp_gpg = opp_gf / opp_played
                    opp_ga_pg = opp_ga / opp_played

            # Expected total goals in match = (team_gpg + opp_ga_pg)/2 + (opp_gpg + team_ga_pg)/2
            if team_gpg > 0 or opp_gpg > 0:
                expected_team_goals = (team_gpg + opp_ga_pg) / 2.0
                expected_opp_goals = (opp_gpg + team_ga_pg) / 2.0
                expected_total = expected_team_goals + expected_opp_goals

                # Signal 2: Odds-implied over/under (if available)
                if match_odds and match_odds.get("bookmakerOdds"):
                    try:
                        home_odds = float(match_odds["bookmakerOdds"].get("homeWin", 3.0))
                        away_odds = float(match_odds["bookmakerOdds"].get("awayWin", 3.0))
                        # Low home+away odds = both teams expected to score
                        total_implied = 1.0/max(home_odds, 1.01) + 1.0/max(away_odds, 1.01)
                        if total_implied > 0.65:  # Both teams strong favorites to score
                            expected_total += 0.3
                            game_tempo["notes"].append("Odds suggest competitive match")
                    except Exception:
                        pass

                # Classify tempo
                if expected_total >= 3.2:
                    game_tempo["expectedTempo"] = "high"
                    # High-tempo: scale up pass volume by 4-8%
                    tempo_boost = min(0.08, (expected_total - 2.5) * 0.04)
                    game_tempo["tempoMultiplier"] = round(1.0 + tempo_boost, 3)
                    game_tempo["notes"].append(f"High-tempo expected ({expected_total:.1f} total goals) → +{tempo_boost*100:.0f}% pass boost")
                elif expected_total <= 1.8:
                    game_tempo["expectedTempo"] = "low"
                    # Low-tempo: dampen pass volume by 3-6%
                    tempo_drop = max(-0.06, -(2.5 - expected_total) * 0.03)
                    game_tempo["tempoMultiplier"] = round(1.0 + tempo_drop, 3)
                    game_tempo["notes"].append(f"Low-tempo expected ({expected_total:.1f} total goals) → {tempo_drop*100:.0f}% pass reduction")
                else:
                    game_tempo["expectedTempo"] = "normal"
                    game_tempo["tempoMultiplier"] = 1.0

                game_tempo["expectedTotalGoals"] = round(expected_total, 2)
                game_tempo["teamGPG"] = round(team_gpg, 2)
                game_tempo["oppGPG"] = round(opp_gpg, 2)

            if game_tempo["notes"]:
                print(f"[GAME TEMPO] {req.playerName}: tempo={game_tempo['expectedTempo']}, mult={game_tempo['tempoMultiplier']}, goals={game_tempo.get('expectedTotalGoals', '?')}")
        except Exception as e:
            print(f"[GAME TEMPO] Error: {e}")

        # =============================================
        # HEAVY FAVORITE DAMPENING — for OVER pass props
        # When a team is a heavy favorite (odds < 1.6), they're likely
        # to score early and then reduce passing tempo (game management).
        # This creates a "leading-team tempo drop" effect.
        # =============================================
        favorite_dampening = {"applied": False}
        try:
            poss_sensitive_for_fav = {"pass_attempts", "passes", "key_passes", "crosses"}
            if req.propType in poss_sensitive_for_fav and match_odds and match_odds.get("bookmakerOdds"):
                home_odds = float(match_odds["bookmakerOdds"].get("homeWin", 3.0))
                away_odds = float(match_odds["bookmakerOdds"].get("awayWin", 3.0))
                team_odds = home_odds if player_venue == "home" else away_odds

                if team_odds < 1.60:
                    # Heavy favorite — game management likely in 2nd half
                    # The heavier the favorite, the stronger the dampening
                    fav_dampen = round(min(0.06, (1.60 - team_odds) * 0.10), 3)
                    favorite_dampening = {
                        "applied": True,
                        "teamOdds": team_odds,
                        "dampeningFactor": fav_dampen,
                        "note": f"Heavy favorite ({team_odds:.2f}): leading teams reduce tempo → -{fav_dampen*100:.0f}% pass dampening"
                    }
                    print(f"[FAVORITE DAMPENING] {req.playerName}: odds={team_odds:.2f}, dampen={fav_dampen*100:.0f}%")
        except Exception as e:
            print(f"[FAVORITE DAMPENING] Error: {e}")

        print(f"[TIMING] Wave 2: {_t.time()-_t0:.1f}s total")

        historical_data = {
            "playerStats": player_stats,
            "teamStats": team_stats,
            "opponentStats": opponent_stats,
            "h2hData": h2h_data,
            "standings": standings,
            "recentFixtures": recent_fixtures,
            "matchOdds": match_odds,
        }

        # =============================================
        # Per-fixture deep data (Wave 2 results)
        # =============================================
        if team_fixture_stats:
            historical_data["teamMatchStats"] = team_fixture_stats
        if opponent_fixture_stats:
            historical_data["opponentMatchStats"] = opponent_fixture_stats
        if player_game_logs:
            # Add summary stats for the game logs
            target_field_map = {
                "pass_attempts": "passes_total",
                "shots": "shots_total",
                "shots_on_target": "shots_on",
                "tackles": "tackles_total",
                "key_passes": "passes_key",
                "shots_assisted": "passes_key",
                "saves": "goals_saves",
                "interceptions": "tackles_interceptions",
                "blocks": "tackles_blocks",
                "dribbles": "dribbles_attempts",
                "fouls_drawn": "fouls_drawn",
            }
            target_field = target_field_map.get(req.propType, "passes_total")
            values = [g.get(target_field) for g in player_game_logs if g.get(target_field) is not None]
            minutes_list = [g.get("minutes", 0) for g in player_game_logs if g.get("minutes")]
            per90_values = [g.get("targetStatPer90") for g in player_game_logs if g.get("targetStatPer90") is not None]

            game_log_summary = {
                "games": player_game_logs,
                "targetProp": req.propType,
                "sampleSize": len(values),
            }
            if values:
                game_log_summary["rawAvg"] = round(sum(values) / len(values), 2)
                game_log_summary["rawMin"] = min(values)
                game_log_summary["rawMax"] = max(values)
                if len(values) >= 3:
                    game_log_summary["stdDev"] = round(stats_mod.stdev(values), 2)
                # Home/away splits
                home_vals = [g.get(target_field) for g in player_game_logs if g.get("venue") == "home" and g.get(target_field) is not None]
                away_vals = [g.get(target_field) for g in player_game_logs if g.get("venue") == "away" and g.get(target_field) is not None]
                if home_vals:
                    game_log_summary["homeAvg"] = round(sum(home_vals) / len(home_vals), 2)
                if away_vals:
                    game_log_summary["awayAvg"] = round(sum(away_vals) / len(away_vals), 2)
            if per90_values:
                game_log_summary["per90Avg"] = round(sum(per90_values) / len(per90_values), 2)
            if minutes_list:
                game_log_summary["avgMinutes"] = round(sum(minutes_list) / len(minutes_list), 1)
            if values and req.line:
                over_hits = sum(1 for v in values if v > req.line)
                under_hits = sum(1 for v in values if v < req.line)
                game_log_summary["hitRates"] = {
                    "overHits": over_hits,
                    "underHits": under_hits,
                    "overPct": round(over_hits / len(values) * 100, 1),
                    "underPct": round(under_hits / len(values) * 100, 1),
                    "total": len(values),
                }

            historical_data["playerGameLogs"] = game_log_summary

        # =============================================
        # EARLY BAYESIAN — Compute math BEFORE AI prompt
        # This anchors the AI's reasoning so it doesn't
        # contradict the mathematical evidence.
        # =============================================
        early_bayes = None
        bayesian_prompt_anchor = ""
        try:
            from bayesian_engine import compute_bayesian_projection
            _sfm = {
                "goals": "goals_total", "assists": "goals_assists",
                "shots_assisted": "passes_key",
                "pass_attempts": "passes_total", "shots": "shots_total",
                "shots_on_target": "shots_on", "tackles": "tackles_total",
                "key_passes": "passes_key", "saves": "goals_saves",
                "interceptions": "tackles_interceptions", "blocks": "tackles_blocks",
                "dribbles": "dribbles_attempts", "dribbles_success": "dribbles_success",
                "fouls_drawn": "fouls_drawn", "fouls_committed": "fouls_committed",
                "crosses": "passes_crosses", "clearances": "tackles_clearances",
                "duels_won": "duels_won", "yellow_cards": "cards_yellow",
            }
            early_bayes = compute_bayesian_projection(
                game_logs=player_game_logs,
                prop_type=req.propType,
                line=req.line,
                venue=player_venue,
                stat_field=_sfm.get(req.propType, "passes_total"),
                opponent_fixture_stats=opponent_fixture_stats,
                match_dominance=match_dominance,
            )
            print(f"[BAYESIAN] {req.playerName}/{req.propType}: samples={early_bayes.get('priorSamples') if early_bayes else 0}, logs={len(player_game_logs)}")
            if early_bayes and early_bayes.get("priorSamples", 0) >= 3:
                bdir = early_bayes['recommendation'].upper()
                bprob = early_bayes['pOver'] if bdir == 'OVER' else early_bayes['pUnder']
                bayesian_prompt_anchor = f"""
[MATHEMATICAL ENGINE — DO NOT IGNORE]
3-Layer Bayesian analysis ({early_bayes['priorSamples']} games): projects {early_bayes['posteriorMean']} {bdir} (P={bprob}%).
Season avg: {early_bayes['priorMean']} | Recent form (decay-weighted): {early_bayes['momentumMean']} ({early_bayes['momentumLabel']}) | Context adj: {early_bayes['covariateAdjustment']:+.1f}
Streak: {early_bayes['streakFlag']} | Volatility: {early_bayes['volatility']} (CV={early_bayes['cv']}) | Reversal: {early_bayes['reversalFlag']}
>>> Your projectedValue MUST be within 20% of {early_bayes['posteriorMean']}. If you disagree, explain specifically why in your reasoning. <<<"""
                # Inject game tempo context into the AI prompt
                if game_tempo.get("expectedTempo") != "normal" and req.propType in {"pass_attempts", "passes", "key_passes", "crosses", "dribbles"}:
                    tempo_label = game_tempo["expectedTempo"].upper()
                    exp_goals = game_tempo.get("expectedTotalGoals", "?")
                    bayesian_prompt_anchor += f"""
[GAME TEMPO WARNING]
Expected match tempo: {tempo_label} ({exp_goals} expected total goals).
{"HIGH tempo = more open play, more touches, higher pass volumes for ALL players." if tempo_label == "HIGH" else "LOW tempo = defensive, fewer passes, compressed stat lines."}
Factor this into your projection — do NOT ignore game flow."""
                # Inject favorite dampening context
                if favorite_dampening.get("applied") and req.propType in {"pass_attempts", "passes", "key_passes", "crosses"}:
                    bayesian_prompt_anchor += f"""
[HEAVY FAVORITE ALERT]
This player's team is a heavy favorite (odds: {favorite_dampening['teamOdds']:.2f}).
CRITICAL: Teams leading early often shift to game management mode — fewer passes, direct play, time-wasting.
If recommending OVER on passes, account for potential 2nd-half tempo drop."""
                print(f"[BAYESIAN ANCHOR] {req.playerName}: math={early_bayes['posteriorMean']} {bdir} ({bprob}%), momentum={early_bayes['momentumLabel']}, streak={early_bayes['streakFlag']}")
        except Exception as e:
            print(f"[BAYESIAN ANCHOR] Error: {e}")

        # =============================================
        # BUILD REAL RECENT SAMPLES FROM GAME LOGS
        # =============================================
        # These replace Gemini-generated samples with actual API-Sports data
        real_recent_samples = []
        if player_game_logs:
            gl_target_field_map = {
                "pass_attempts": "passes_total", "shots": "shots_total", "shots_on_target": "shots_on",
                "tackles": "tackles_total", "key_passes": "passes_key", "shots_assisted": "passes_key",
                "saves": "goals_saves",
                "interceptions": "tackles_interceptions", "blocks": "tackles_blocks",
                "dribbles": "dribbles_attempts", "fouls_drawn": "fouls_drawn",
            }
            gl_target = gl_target_field_map.get(req.propType, "passes_total")
            for g in player_game_logs:
                stat_val = g.get(gl_target)
                if stat_val is not None and (g.get("minutes") or 0) > 0:
                    real_recent_samples.append({
                        "date": g.get("date", ""),
                        "opponent": g.get("opponent", ""),
                        "value": stat_val,
                        "minutesPlayed": g.get("minutes", 0),
                        "matchDifficulty": "medium",
                        "venue": g.get("venue", ""),
                    })

        # =============================================
        # UPGRADE #4: Per-90 minute normalization
        # =============================================
        # Extract per-90 rates from player's season stats so Gemini sees
        # normalized numbers, not raw totals skewed by minutes played
        per90_stats = {}
        if player_stats:
            stat_key_map = {
                "pass_attempts": ("passes", "total"),
                "shots": ("shots", "total"),
                "shots_on_target": ("shots", "on"),
                "tackles": ("tackles", "total"),
                "key_passes": ("passes", "key"),
                "shots_assisted": ("passes", "key"),
                "saves": ("goals", "saves"),
                "interceptions": ("tackles", "interceptions"),
                "blocks": ("tackles", "blocks"),
                "dribbles": ("dribbles", "attempts"),
                "fouls_drawn": ("fouls", "drawn"),
                "crosses": ("passes", "cross"),
                "clearances": ("tackles", "clearances"),
                "goals": ("goals", "total"),
                "assists": ("goals", "assists"),
                "duels_won": ("duels", "won"),
                "yellow_cards": ("cards", "yellow"),
                "fouls_committed": ("fouls", "committed"),
            }
            for stat_entry in player_stats.get("statistics", []):
                league_name = stat_entry.get("league", {}).get("name", "Unknown")
                season = stat_entry.get("league", {}).get("season", "")
                games = stat_entry.get("games", {})
                minutes = games.get("minutes") or 0
                appearances = games.get("appearences") or 0
                if minutes < 90 or appearances < 2:
                    continue  # Skip tiny samples

                entry = {
                    "league": league_name,
                    "season": season,
                    "appearances": appearances,
                    "totalMinutes": minutes,
                    "avgMinutesPerGame": round(minutes / appearances, 1) if appearances else 0,
                    "per90": {},
                    "rawPerGame": {},
                }

                for prop_key, (cat, sub) in stat_key_map.items():
                    raw_val = stat_entry.get(cat, {}).get(sub)
                    if raw_val is not None and raw_val > 0:
                        per_90 = round((raw_val / minutes) * 90, 2)
                        per_game = round(raw_val / appearances, 2) if appearances else 0
                        entry["per90"][prop_key] = per_90
                        entry["rawPerGame"][prop_key] = per_game

                if entry["per90"]:
                    per90_stats[f"{league_name}_{season}"] = entry

        if per90_stats:
            historical_data["per90Analysis"] = per90_stats

        # =============================================
        # UPGRADE #3: H2H player-specific stat extraction
        # =============================================
        # For each H2H fixture, fetch the player's individual stats in THAT match
        h2h_player_stats = []
        if h2h_data:
            h2h_fixture_ids = []
            for h in h2h_data[:5]:
                fid = h.get("fixture", {}).get("id")
                if fid:
                    h2h_fixture_ids.append((fid, h))

            async def fetch_h2h_player_stat(fid, fixture_info):
                """Fetch the target player's stats from a specific H2H fixture"""
                try:
                    pstats = await api_football_request("fixtures/players", {"fixture": fid})
                    if not pstats:
                        return None

                    # Determine which team is the player's team in this fixture
                    home_id = fixture_info.get("teams", {}).get("home", {}).get("id")
                    away_id = fixture_info.get("teams", {}).get("away", {}).get("id")
                    home_name = fixture_info.get("teams", {}).get("home", {}).get("name", "")
                    away_name = fixture_info.get("teams", {}).get("away", {}).get("name", "")
                    home_goals = fixture_info.get("goals", {}).get("home", 0)
                    away_goals = fixture_info.get("goals", {}).get("away", 0)

                    # Player's team is home → opponent is away, and vice versa
                    player_is_home = (home_id == actual_team_id)
                    opponent_name = away_name if player_is_home else home_name
                    venue_in_match = "home" if player_is_home else "away"

                    # Find our player in the fixture stats
                    for team_data in pstats:
                        for p in team_data.get("players", []):
                            if p.get("player", {}).get("id") == req.playerId:
                                stats = p.get("statistics", [{}])[0] if p.get("statistics") else {}
                                minutes_played = stats.get("games", {}).get("minutes") or 0
                                stat_key_map_h2h = {
                                    "pass_attempts": stats.get("passes", {}).get("total"),
                                    "shots": stats.get("shots", {}).get("total"),
                                    "shots_on_target": stats.get("shots", {}).get("on"),
                                    "tackles": stats.get("tackles", {}).get("total"),
                                    "key_passes": stats.get("passes", {}).get("key"),
                                    "shots_assisted": stats.get("passes", {}).get("key"),
                                    "saves": stats.get("goals", {}).get("saves"),
                                    "interceptions": stats.get("tackles", {}).get("interceptions"),
                                    "blocks": stats.get("tackles", {}).get("blocks"),
                                    "dribbles": stats.get("dribbles", {}).get("attempts"),
                                    "fouls_drawn": stats.get("fouls", {}).get("drawn"),
                                    "crosses": stats.get("passes", {}).get("cross"),
                                    "clearances": stats.get("tackles", {}).get("clearances"),
                                    "goals": stats.get("goals", {}).get("total"),
                                    "assists": stats.get("goals", {}).get("assists"),
                                    "duels_won": stats.get("duels", {}).get("won"),
                                    "yellow_cards": stats.get("cards", {}).get("yellow"),
                                    "fouls_committed": stats.get("fouls", {}).get("committed"),
                                }
                                return {
                                    "date": fixture_info.get("fixture", {}).get("date", ""),
                                    "opponent": opponent_name,
                                    "venue": venue_in_match,
                                    "minutesPlayed": minutes_played,
                                    "statValues": {k: v for k, v in stat_key_map_h2h.items() if v is not None},
                                    "targetStat": stat_key_map_h2h.get(req.propType),
                                    "targetStatPer90": round((stat_key_map_h2h.get(req.propType, 0) or 0) / minutes_played * 90, 2) if minutes_played > 0 and stat_key_map_h2h.get(req.propType) else None,
                                    "matchScore": f"{home_goals}-{away_goals}",
                                }
                    return None
                except Exception:
                    return None

            if h2h_fixture_ids:
                try:
                    h2h_results = await aio.wait_for(
                        aio.gather(*[fetch_h2h_player_stat(fid, fi) for fid, fi in h2h_fixture_ids[:5]]),
                        timeout=6
                    )
                    h2h_player_stats = [r for r in h2h_results if r]
                except aio.TimeoutError:
                    h2h_player_stats = []
        print(f"[TIMING] H2H+prep: {_t.time()-_t0:.1f}s total")

        if h2h_player_stats:
            # Calculate H2H averages for the target stat
            h2h_values = [s["targetStat"] for s in h2h_player_stats if s.get("targetStat") is not None]
            h2h_summary = {
                "matches": h2h_player_stats,
                "targetProp": req.propType,
                "sampleSize": len(h2h_values),
            }
            if h2h_values:
                h2h_summary["avgVsOpponent"] = round(sum(h2h_values) / len(h2h_values), 2)
                h2h_summary["minVsOpponent"] = min(h2h_values)
                h2h_summary["maxVsOpponent"] = max(h2h_values)
            historical_data["h2hPlayerStats"] = h2h_summary

        # Extract player's ACTUAL position from API-Sports data
        player_position = ""
        if player_stats:
            stats_list = player_stats.get("statistics", [])
            # Find the stat entry with most appearances (most relevant)
            best_entry = None
            best_apps = 0
            for s in stats_list:
                apps = s.get("games", {}).get("appearences") or 0
                pos = s.get("games", {}).get("position", "")
                if apps > best_apps and pos:
                    best_apps = apps
                    best_entry = s
                    player_position = pos
            # If we found a better entry, also try to get stats from multiple seasons
            if not player_position:
                for s in stats_list:
                    pos = s.get("games", {}).get("position", "")
                    if pos:
                        player_position = pos
                        break

        # =============================================
        # AI POSITION RESOLVER: Get specific position (RW, CM, CB, etc.)
        # Uses cache first, then Grok as fallback with API-Sports context
        # =============================================
        specific_position = ""
        player_role = ""
        GENERIC_POSITIONS = {"Goalkeeper", "Defender", "Midfielder", "Attacker", ""}

        # Position-to-role compatibility: ensures roles match positions
        POSITION_ROLE_MAP = {
            "GK": {"Shot-Stopper", "Sweeper Keeper"},
            "CB": {"Ball-Playing CB", "Stopper"},
            "LB": {"Fullback", "Wing-Back", "Inverted Fullback"},
            "RB": {"Fullback", "Wing-Back", "Inverted Fullback"},
            "LWB": {"Wing-Back", "Fullback"},
            "RWB": {"Wing-Back", "Fullback"},
            "CDM": {"Anchor", "Ball Winner", "Deep-Lying Playmaker"},
            "CM": {"Box-to-Box", "Mezzala", "Deep-Lying Playmaker", "Ball Winner"},
            "CAM": {"Advanced Playmaker", "Wide Playmaker", "Shadow Striker"},
            "LM": {"Wide Playmaker", "Traditional Winger"},
            "RM": {"Wide Playmaker", "Traditional Winger"},
            "LW": {"Traditional Winger", "Inverted Winger", "Inside Forward", "Progressive Carrier"},
            "RW": {"Traditional Winger", "Inverted Winger", "Inside Forward", "Progressive Carrier"},
            "CF": {"Complete Forward", "False 9", "Target Man", "Pressing Forward"},
            "ST": {"Poacher", "Target Man", "Complete Forward", "Pressing Forward"},
            "SS": {"Shadow Striker", "False 9"},
        }

        # Constrain valid positions by API-Sports generic category
        GENERIC_TO_SPECIFIC = {
            "Goalkeeper": {"GK"},
            "Defender": {"CB", "LB", "RB", "LWB", "RWB"},
            "Midfielder": {"CDM", "CM", "CAM", "LM", "RM", "LW", "RW"},
            "Attacker": {"LW", "RW", "CF", "ST", "SS", "CAM"},
        }

        if player_position in GENERIC_POSITIONS or not player_position:
            # Check user-provided position override first
            if req.positionOverride:
                specific_position = req.positionOverride
                player_role = req.roleOverride or ""
                print(f"[POS RESOLVE] User override: {req.playerName} → {specific_position} ({player_role})")
            else:
                # Check cache (with 30-day expiry)
                cached_pos = await db.player_positions.find_one(
                    {"playerId": req.playerId}, {"_id": 0, "specificPosition": 1, "role": 1, "updatedAt": 1}
                )
                cache_valid = False
                if cached_pos and cached_pos.get("specificPosition"):
                    # Check if cache is fresh (< 30 days)
                    cached_at = cached_pos.get("updatedAt", "")
                    if cached_at:
                        try:
                            cached_dt = datetime.fromisoformat(cached_at.replace("Z", "+00:00"))
                            age_days = (datetime.now(timezone.utc) - cached_dt).days
                            cache_valid = age_days < 30
                            if not cache_valid:
                                print(f"[POS RESOLVE] Cache expired ({age_days} days): {req.playerName}")
                        except Exception:
                            cache_valid = True  # If we can't parse date, trust the cache
                    else:
                        cache_valid = True  # Legacy cache entries without updatedAt

                if cache_valid:
                    specific_position = cached_pos["specificPosition"]
                    player_role = cached_pos.get("role", "")
                    valid_roles = POSITION_ROLE_MAP.get(specific_position, set())
                    if valid_roles and (not player_role or player_role not in valid_roles):
                        corrected_role = sorted(valid_roles)[0] if valid_roles else ""
                        print(f"[POS RESOLVE] Cache role fix: {req.playerName} {specific_position}/{player_role} → {corrected_role}")
                        player_role = corrected_role
                        await db.player_positions.update_one(
                            {"playerId": req.playerId},
                            {"$set": {"role": corrected_role}}
                        )
                    else:
                        print(f"[POS RESOLVE] Cache hit: {req.playerName} → {specific_position} ({player_role})")

            if not specific_position:
                try:
                    from openai import OpenAI as SyncOpenAI

                    # Build constrained position list based on API-Sports category
                    allowed_positions = GENERIC_TO_SPECIFIC.get(player_position, None)
                    if allowed_positions:
                        pos_list = ", ".join(sorted(allowed_positions))
                        category_hint = f"\nAPI-Sports categorizes this player as: {player_position}. ONLY choose from positions within that category: {pos_list}"
                    else:
                        pos_list = "GK, CB, LB, RB, LWB, RWB, CDM, CM, CAM, LM, RM, LW, RW, CF, ST, SS"
                        category_hint = ""

                    # STATS-AWARE: Extract position-relevant stats for evidence-based resolution
                    stats_evidence = ""
                    stats_list = player_stats.get("statistics", []) if player_stats else []
                    if stats_list:
                        latest = stats_list[-1] if stats_list else {}
                        tck = latest.get("tackles", {})
                        duels = latest.get("duels", {})
                        pss = latest.get("passes", {})
                        drb = latest.get("dribbles", {})
                        sht = latest.get("shots", {})
                        gls = latest.get("goals", {})
                        fls = latest.get("fouls", {})
                        cards = latest.get("cards", {})
                        games = latest.get("games", {})
                        stats_evidence = f"""
ACTUAL SEASON STATS (use these to determine position — stats don't lie):
- Appearances: {games.get('appearances', '?')}, Minutes: {games.get('minutes', '?')}, Rating: {games.get('rating', '?')}
- Tackles: {tck.get('total', 0)}, Interceptions: {tck.get('interceptions', 0)}, Blocks: {tck.get('blocks', 0)}
- Duels won: {duels.get('won', 0)}/{duels.get('total', 0)}
- Passes total: {pss.get('total', 0)}, Key passes: {pss.get('key', 0)}, Accuracy: {pss.get('accuracy', '?')}%
- Dribbles: {drb.get('attempts', 0)} attempts, {drb.get('success', 0)} successful
- Shots: {sht.get('total', 0)}, On target: {sht.get('on', 0)}
- Goals: {gls.get('total', 0)}, Assists: {gls.get('assists', 0)}
- Fouls drawn: {fls.get('drawn', 0)}, Committed: {fls.get('committed', 0)}
- Yellow cards: {cards.get('yellow', 0)}, Red: {cards.get('red', 0)}
POSITION CLUES: CB=high tackles/blocks/aerial duels, low crosses/key passes/dribbles. LB/RB=crosses, some key passes, overlapping runs. CDM=high interceptions, moderate passing. CM=balanced. CAM=high key passes. Winger=high dribbles/crosses. ST=high shots/goals."""

                    pos_prompt = f"What is {req.playerName}'s primary position and tactical role at {req.teamName}?{category_hint}{stats_evidence}\nPosition must be one of: {pos_list}\nRole must be one of: Shot-Stopper, Sweeper Keeper, Ball-Playing CB, Stopper, Fullback, Wing-Back, Inverted Fullback, Anchor, Box-to-Box, Deep-Lying Playmaker, Ball Winner, Mezzala, Advanced Playmaker, Wide Playmaker, Traditional Winger, Inverted Winger, Progressive Carrier, Inside Forward, Target Man, Poacher, False 9, Shadow Striker, Complete Forward, Pressing Forward\nReply ONLY: POSITION|ROLE"

                    # DUAL-AI POSITION VALIDATION: Grok + Gemini in parallel for defenders
                    is_defender = player_position == "Defender"

                    pos_client = SyncOpenAI(api_key=XAI_API_KEY, base_url="https://api.x.ai/v1")

                    async def resolve_pos_grok():
                        return await aio.wait_for(
                            aio.to_thread(
                                pos_client.chat.completions.create,
                                model="grok-4-1-fast-non-reasoning",
                                messages=[
                                    {"role": "system", "content": "You are a football/soccer tactical analyst. Reply in EXACTLY this format on one line:\nPOSITION|ROLE\nNothing else."},
                                    {"role": "user", "content": pos_prompt},
                                ],
                                temperature=0,
                            ),
                            timeout=8
                        )

                    async def resolve_pos_gemini():
                        EMERGENT_PROXY = "https://integrations.emergentagent.com/llm"
                        gemini_client = OpenAI(api_key=EMERGENT_LLM_KEY, base_url=EMERGENT_PROXY + "/v1")
                        loop = aio.get_event_loop()
                        def _run_gemini():
                            return gemini_client.chat.completions.create(
                                model="gemini/gemini-2.0-flash",
                                messages=[
                                    {"role": "system", "content": "You are a football/soccer tactical analyst. Reply in EXACTLY this format on one line:\nPOSITION|ROLE\nNothing else."},
                                    {"role": "user", "content": pos_prompt},
                                ],
                                temperature=0,
                                max_tokens=50,
                            )
                        return await aio.wait_for(loop.run_in_executor(None, _run_gemini), timeout=8)

                    def parse_pos_response(resp_text, allowed):
                        parts = resp_text.strip().split("|")
                        pos = parts[0].strip().upper().replace(".", "").replace(",", "") if parts else ""
                        role = parts[1].strip() if len(parts) > 1 else ""
                        if pos in (allowed or {"GK","CB","LB","RB","LWB","RWB","CDM","CM","CAM","LM","RM","LW","RW","CF","ST","SS"}):
                            return pos, role
                        return None, None

                    valid_positions = allowed_positions or {"GK","CB","LB","RB","LWB","RWB","CDM","CM","CAM","LM","RM","LW","RW","CF","ST","SS"}

                    if is_defender:
                        # Dual-AI for defenders (most common source of errors)
                        try:
                            grok_resp, gemini_resp = await aio.gather(
                                resolve_pos_grok(), resolve_pos_gemini(),
                                return_exceptions=True
                            )
                            grok_pos, grok_role = None, None
                            gemini_pos, gemini_role = None, None
                            if not isinstance(grok_resp, Exception):
                                grok_pos, grok_role = parse_pos_response(grok_resp.choices[0].message.content, valid_positions)
                            if not isinstance(gemini_resp, Exception):
                                gemini_pos, gemini_role = parse_pos_response(gemini_resp.choices[0].message.content, valid_positions)

                            if grok_pos and gemini_pos:
                                if grok_pos == gemini_pos:
                                    pos_code = grok_pos
                                    role_text = grok_role or gemini_role or ""
                                    print(f"[POS RESOLVE] Dual-AI AGREE: {req.playerName} → {pos_code} (Grok={grok_pos}, Gemini={gemini_pos})")
                                else:
                                    # Disagreement — use stats heuristic as tiebreaker
                                    pos_code = grok_pos  # default to Grok
                                    role_text = grok_role or ""
                                    if stats_list:
                                        latest_s = stats_list[-1]
                                        key_passes = latest_s.get("passes", {}).get("key", 0) or 0
                                        dribble_att = latest_s.get("dribbles", {}).get("attempts", 0) or 0
                                        tackles_total = latest_s.get("tackles", {}).get("total", 0) or 0
                                        blocks = latest_s.get("tackles", {}).get("blocks", 0) or 0
                                        # CB indicators: high tackles+blocks, low key passes & dribbles
                                        cb_score = (tackles_total + blocks * 2) - (key_passes + dribble_att)
                                        if cb_score > 10 and "CB" in {grok_pos, gemini_pos}:
                                            pos_code = "CB"
                                            role_text = grok_role if grok_pos == "CB" else gemini_role or "Ball-Playing CB"
                                        elif cb_score < -5 and ("LB" in {grok_pos, gemini_pos} or "RB" in {grok_pos, gemini_pos}):
                                            pos_code = grok_pos if grok_pos in ("LB", "RB") else gemini_pos
                                            role_text = grok_role if grok_pos == pos_code else gemini_role or "Fullback"
                                    print(f"[POS RESOLVE] Dual-AI DISAGREE: Grok={grok_pos}, Gemini={gemini_pos} → tiebreak={pos_code} (stats-based)")
                            elif grok_pos:
                                pos_code = grok_pos
                                role_text = grok_role or ""
                                print(f"[POS RESOLVE] Grok only: {req.playerName} → {pos_code}")
                            elif gemini_pos:
                                pos_code = gemini_pos
                                role_text = gemini_role or ""
                                print(f"[POS RESOLVE] Gemini only: {req.playerName} → {pos_code}")
                            else:
                                raise ValueError("Both AIs failed for position")
                        except Exception as e:
                            # Fallback to single Grok call
                            print(f"[POS RESOLVE] Dual-AI failed ({e}), trying single Grok...")
                            pos_resp = await resolve_pos_grok()
                            pos_code, role_text = parse_pos_response(pos_resp.choices[0].message.content, valid_positions)
                            if not pos_code:
                                raise ValueError("Grok returned invalid position")
                    else:
                        # Non-defenders: single Grok call (with stats context)
                        pos_resp = await resolve_pos_grok()
                        pos_code, role_text = parse_pos_response(pos_resp.choices[0].message.content, valid_positions)
                        if not pos_code:
                            raise ValueError("Grok returned invalid position")

                    if pos_code:
                        specific_position = pos_code
                        # Validate role matches position
                        valid_roles = POSITION_ROLE_MAP.get(pos_code, set())
                        if role_text and valid_roles and role_text not in valid_roles:
                            print(f"[POS RESOLVE] Role '{role_text}' invalid for {pos_code}, defaulting to first valid role")
                            role_text = sorted(valid_roles)[0] if valid_roles else ""
                        elif not role_text and valid_roles:
                            role_text = sorted(valid_roles)[0]
                        player_role = role_text
                        await db.player_positions.update_one(
                            {"playerId": req.playerId},
                            {"$set": {
                                "playerId": req.playerId,
                                "playerName": req.playerName,
                                "team": req.teamName,
                                "genericPosition": player_position,
                                "specificPosition": specific_position,
                                "role": player_role,
                                "updatedAt": datetime.now(timezone.utc).isoformat(),
                            }},
                            upsert=True
                        )
                        print(f"[POS RESOLVE] AI resolved: {req.playerName} → {specific_position} | {player_role} (cached)")
                    else:
                        print("[POS RESOLVE] AI returned invalid position")
                except Exception as e:
                    print(f"[POS RESOLVE] Error: {e}")
        else:
            specific_position = player_position

        # Use specific position if available, otherwise fall back to generic
        display_position = specific_position or player_position
        display_role = player_role

        # =============================================
        # MULTI-AI CONSENSUS ENGINE (3 AIs)
        # Grok 3 Mini (GK) — single AI engine
        # =============================================
        PREDICTION_SYSTEM = """Elite soccer prop prediction engine. Analyze data thoroughly, return calibrated JSON.

REQUIREMENTS:
- "reasoning": 3-5 sentences citing specific per-game averages, venue splits, opponent tendencies from data
- "tacticalBreakdown": ~1500 char markdown. Sections: **Verdict** (1 sentence), **Analysis** (cite real numbers, venue/sample context), **Scenarios** (best/worst/likely with stat ranges), **Risk** (sub risk, rotation, tactical shifts), **TL;DR**. Mention knockout/tournament stage if applicable
- "scenarioAnalysis": 2-3 sentences with specific projections per scenario
- "sharpSummary": 2 sentences explaining why projection differs from line
- "keyEvidence": 2-3 strongest data points as string
- "gameFlowDynamics": How game state impacts this stat (1-2 sentences)
- "sensitivityTests", "subRisk", "uncertaintyNote": 1 sentence each

CRITICAL RULES:
- NEVER double-count minutes. If data shows a player averaging 43 passes in 26 minutes per game, the 43 IS their actual game output. Do NOT scale down by minutes. The average already reflects their real playing time.
- Match context OVERRIDES raw averages: If [MATCH DOMINANCE ANALYSIS] shows expected possession significantly above the team's season average, RAISE projections for pass-dependent props (pass_attempts, key_passes) accordingly. Historical averages are baselines, not ceilings.
- For pass/creative props: weight POSSESSION EXPECTATION heavily. A deep-lying playmaker on a team expected at 65%+ possession WILL exceed their season average.

CALIBRATION RULES (MUST FOLLOW):
- UNDER SKEW: Stats have positive skew — a player can have a monster game but can't go below 0. UNDER bets are inherently riskier. If recommending UNDER, your confidence should be 3-5% LOWER than you'd give for an equivalent OVER edge.
- BINARY LINES: If the line is 0.5 (e.g., UNDER 0.5 means ZERO of that stat), be EXTREMELY cautious recommending UNDER. One event loses the bet. UNDER 0.5 confidence should NEVER exceed 55% unless data overwhelmingly supports it.
- TIGHT EDGE: If your projected value is within ±1.0 of the line, this is a MARGINAL edge. Confidence should NOT exceed 60% regardless of how much data you have.
- DEFENDER PASSES: Ball-playing center-backs (CB, LB, RB) in possession-dominant teams (55%+ possession) routinely hit 60-85+ passes per game. Do NOT assume defenders have low pass counts — check their actual per-game averages carefully.

JSON: {"projectedValue":0,"recommendation":"over|under","confidenceScore":0,"confidenceLevel":"","sharpSummary":"","reasoning":"","scenarioAnalysis":"","keyEvidence":"","sensitivityTests":"","subRisk":"","gameFlowDynamics":"","uncertaintyNote":"","tacticalBreakdown":"","matchupOverview":{"homeTeam":"","awayTeam":"","favorite":"","moneyline":{"home":"","draw":"","away":""},"expectedPossession":{"home":0,"away":0},"expectedGameType":"","keyMatchupFactor":""},"bayesianMetrics":{"priorMean":0,"momentumEffect":0,"covariateAdjustment":0,"reversalFlag":"stable"},"probabilityCurve":[],"recentSamples":[],"player":{"id":0,"name":"","team":"","position":""},"opponent":"","propType":"","line":0,"confidenceInterval":[0,0],"tacticalAlerts":[]}"""

        # Build the data payload — use GPT summary as primary + Wave 2 deep data as supplement
        wave2_supplement = {}
        if player_game_logs:
            target_field_map = {
                "pass_attempts": "passes_total", "shots": "shots_total", "shots_on_target": "shots_on",
                "tackles": "tackles_total", "key_passes": "passes_key", "shots_assisted": "passes_key",
                "saves": "goals_saves",
                "interceptions": "tackles_interceptions", "blocks": "tackles_blocks",
                "dribbles": "dribbles_attempts", "fouls_drawn": "fouls_drawn",
                "crosses": "passes_crosses", "clearances": "tackles_clearances",
                "goals": "goals_total", "assists": "goals_assists",
                "duels_won": "duels_won", "yellow_cards": "cards_yellow",
                "fouls_committed": "fouls_committed",
            }
            target_field = target_field_map.get(req.propType, "passes_total")
            values = [g.get(target_field) for g in player_game_logs if g.get(target_field) is not None]
            game_log_brief = []
            for g in player_game_logs:
                val = g.get(target_field)
                game_log_brief.append(f"{g.get('date','')[:10]} vs {g.get('opponent','')} ({g.get('venue','')}, {g.get('minutes',0)}min): {val}")
            wave2_supplement["playerGameLogs"] = {
                "games": game_log_brief,
                "rawAvg": round(sum(values) / len(values), 2) if values else 0,
                "homeAvg": round(sum(v for g, v in zip(player_game_logs, [g.get(target_field) for g in player_game_logs]) if g.get("venue") == "home" and v) / max(1, sum(1 for g in player_game_logs if g.get("venue") == "home" and g.get(target_field))), 2) if values else 0,
                "awayAvg": round(sum(v for g, v in zip(player_game_logs, [g.get(target_field) for g in player_game_logs]) if g.get("venue") == "away" and v) / max(1, sum(1 for g in player_game_logs if g.get("venue") == "away" and g.get(target_field))), 2) if values else 0,
                "sampleSize": len(values),
            }
            # Pre-compute OVER/UNDER hit rates from actual game logs
            if values and req.line:
                over_hits = sum(1 for v in values if v > req.line)
                under_hits = sum(1 for v in values if v < req.line)
                push_hits = len(values) - over_hits - under_hits
                over_pct = round(over_hits / len(values) * 100, 1)
                under_pct = round(under_hits / len(values) * 100, 1)
                wave2_supplement["playerGameLogs"]["hitRates"] = {
                    "overHits": over_hits, "underHits": under_hits, "pushHits": push_hits,
                    "overPct": over_pct, "underPct": under_pct, "total": len(values),
                    "summary": f"OVER {req.line} in {over_hits}/{len(values)} games ({over_pct}%), UNDER in {under_hits}/{len(values)} ({under_pct}%)"
                }
        if team_fixture_stats:
            wave2_supplement["teamMatchStats"] = team_fixture_stats
        if opponent_fixture_stats:
            wave2_supplement["opponentMatchStats"] = opponent_fixture_stats

        # SAVES-SPECIFIC: Elite GK Formula
        # Projected Saves = Opponent Avg SoT × GK Save% × Match Context Multiplier
        saves_context = ""
        gk_formula_data = None
        if req.propType == "saves":
            # 1. Opponent SoT per game (venue-filtered from fixture stats)
            opp_shots_list = []
            if opponent_fixture_stats:
                for mf in opponent_fixture_stats:
                    shots = mf.get("totalShots")
                    shots_on = mf.get("shotsOnTarget")
                    if shots is not None:
                        opp_shots_list.append({"total": shots, "on_target": shots_on or 0, "date": mf.get("date", ""), "venue": mf.get("venue", "")})
            opp_avg_shots = round(sum(s["total"] for s in opp_shots_list) / len(opp_shots_list), 1) if opp_shots_list else 0
            opp_avg_sot = round(sum(s["on_target"] for s in opp_shots_list) / len(opp_shots_list), 1) if opp_shots_list else 0

            # 2. GK save rate from LAST 5-7 game logs only (recent form)
            gk_saves_list = []
            gk_ga_from_logs = []
            recent_gk_logs = [g for g in player_game_logs if g.get("goals_saves") is not None and g.get("minutes", 0) > 0][:7]
            for g in recent_gk_logs:
                gk_saves_list.append(g.get("goals_saves"))
                # Compute GA directly from game score + venue (most reliable source)
                score = g.get("score", "")
                venue = g.get("venue", "")
                try:
                    parts = score.split("-")
                    home_goals = int(parts[0].strip())
                    away_goals = int(parts[1].strip())
                    ga_this_game = away_goals if venue == "home" else home_goals
                    gk_ga_from_logs.append(ga_this_game)
                except Exception:
                    pass
            gk_avg_saves = round(sum(gk_saves_list) / len(gk_saves_list), 2) if gk_saves_list else 0
            gk_saves_per90 = round(sum(gk_saves_list) / max(1, sum((g.get("minutes") or 0) for g in recent_gk_logs)) * 90, 2) if gk_saves_list else 0

            # Goals against: prefer game-log-derived, fallback to team stats
            total_saves = sum(gk_saves_list) if gk_saves_list else 0
            games_with_saves = len(gk_saves_list)
            total_ga_from_logs = sum(gk_ga_from_logs) if gk_ga_from_logs else 0
            goals_against = round(total_ga_from_logs / len(gk_ga_from_logs), 2) if gk_ga_from_logs else None

            # Fallback to team stats if game logs didn't yield GA
            if goals_against is None and team_stats:
                ga = team_stats.get("goals", {}).get("against", {})
                if ga:
                    ga_total = ga.get("total", {})
                    if isinstance(ga_total, dict):
                        total_ga = ga_total.get(player_venue) or ga_total.get("total") or 0
                    else:
                        total_ga = ga_total or 0
                    played_data = team_stats.get("fixtures", {}).get("played", {})
                    if isinstance(played_data, dict):
                        played = played_data.get(player_venue) or played_data.get("total") or 1
                    else:
                        played = played_data or 1
                    goals_against = round(total_ga / max(played, 1), 2) if total_ga else None

            # Save % = saves / (saves + goals conceded)
            if total_saves > 0 and total_ga_from_logs > 0:
                est_sot_faced = total_saves + total_ga_from_logs
                gk_save_pct = round((total_saves / max(est_sot_faced, 1)) * 100, 1)
            elif total_saves > 0 and goals_against is not None and games_with_saves > 0:
                est_sot_faced = total_saves + (goals_against * games_with_saves)
                gk_save_pct = round((total_saves / max(est_sot_faced, 1)) * 100, 1)
            elif total_saves > 0:
                # Fallback: assume 1.3 GA/game (league average)
                gk_save_pct = round(min(80, (total_saves / max(total_saves + games_with_saves * 1.3, 1)) * 100), 1)
            else:
                gk_save_pct = 65.0  # Conservative league average fallback
            # Cap save rate at realistic bounds
            gk_save_pct = min(80.0, max(50.0, gk_save_pct))

            # 3. Match context multiplier (symmetric adjustments)
            context_multiplier = 1.0
            context_factors = []
            if match_odds and match_odds.get("favorite"):
                fav = match_odds["favorite"]
                if fav == player_venue:
                    context_multiplier -= 0.10
                    context_factors.append(f"Team favored ({fav}) → -10% (fewer opponent shots)")
                else:
                    context_multiplier += 0.10
                    context_factors.append("Team underdog → +10% (more opponent shots)")
            if player_venue == "away":
                context_multiplier += 0.05
                context_factors.append("Away GK → +5% (typically face more pressure)")
            context_multiplier = round(context_multiplier, 2)

            # 4. THE FORMULA: Projected Saves = Opp Avg SoT × GK Save% × Context
            # Weighted blend: 60% formula (match-specific) + 40% GK average (form)
            raw_formula = round(opp_avg_sot * (gk_save_pct / 100) * context_multiplier, 1) if opp_avg_sot > 0 else gk_avg_saves
            if gk_avg_saves > 0 and raw_formula > 0:
                projected_saves = round(raw_formula * 0.6 + gk_avg_saves * 0.4, 1)
            else:
                projected_saves = raw_formula if raw_formula > 0 else gk_avg_saves

            gk_formula_data = {
                "opponentAvgShots": opp_avg_shots,
                "opponentAvgSOT": opp_avg_sot,
                "opponentVenue": opponent_venue.upper(),
                "opponentShotsSample": len(opp_shots_list),
                "gkSaveRate": gk_save_pct,
                "gkAvgSaves": gk_avg_saves,
                "gkSavesPer90": gk_saves_per90,
                "gkSampleSize": games_with_saves,
                "goalsAgainstPerGame": goals_against,
                "contextMultiplier": context_multiplier,
                "contextFactors": context_factors,
                "formulaProjection": projected_saves,
                "formula": f"{opp_avg_sot} SoT × {gk_save_pct}% save rate × {context_multiplier} context → {raw_formula} formula, blended with {gk_avg_saves} avg = {projected_saves}",
            }
            wave2_supplement["savesAnalysis"] = gk_formula_data

            saves_context = f"""
[ELITE GK SAVES FORMULA]
FORMULA: Projected Saves = Opponent Avg SoT × GK Save% × Match Context Multiplier

1. OPPONENT SHOTS ON TARGET ({opponent_venue.upper()} venue, last {len(opp_shots_list)} games):
   - Avg total shots/game: {opp_avg_shots}
   - Avg shots on TARGET/game: {opp_avg_sot}

2. GK SAVE RATE (last {games_with_saves} games):
   - Avg saves/game: {gk_avg_saves}
   - Saves per 90: {gk_saves_per90}
   - Estimated save %: {gk_save_pct}%
   - Team goals against/game ({player_venue}): {goals_against or 'N/A'}

3. MATCH CONTEXT MULTIPLIER: {context_multiplier}
   {chr(10).join('   - ' + f for f in context_factors) if context_factors else '   - Neutral'}

4. FORMULA RESULT: {opp_avg_sot} × {gk_save_pct}% × {context_multiplier} = {raw_formula} (blended with {gk_avg_saves} avg → {projected_saves})

COMPARE TO LINE: Line is {req.line}. Formula projects {projected_saves}.
{'LEAN OVER' if projected_saves > req.line else 'LEAN UNDER' if projected_saves < req.line else 'PUSH ZONE'} — but weight scenarios (blowout, cagey game, etc.)
"""

        # POSITION COMPARISON: Fetch same-position players vs opponent (run after player_position resolved)
        position_comparison = []
        try:
            position_comparison = await aio.wait_for(
                fetch_position_comparison(
                    opponent_fixture_list, player_position, req.propType, req.opponentId,
                    player_venue, 10, target_specific_pos=specific_position
                ) if player_position else _empty_list(),
                timeout=10
            )
        except Exception as e:
            print(f"[POS COMP] Error/timeout: {e}")

        # POSITION CONTEXT: Compute position-specific baseline from game logs + comparison
        position_context = ""
        position_comp_data = None
        if display_position:
            pos_map = {"Goalkeeper": "GK", "Defender": "DEF", "Midfielder": "MID", "Attacker": "FWD"}
            pos_short = specific_position if specific_position else pos_map.get(player_position, player_position)
            position_context = f"\n[PLAYER POSITION] {req.playerName} plays as {pos_short}"
            if player_role:
                position_context += f" — Role: {player_role}"
            if specific_position and player_position:
                position_context += f" (API category: {player_position})"
            if position_comparison:
                comp_values = [p["statValue"] for p in position_comparison]
                comp_per90 = [p["per90"] for p in position_comparison if p.get("per90")]
                comp_poss = [p["teamPossession"] for p in position_comparison if p.get("teamPossession")]
                comp_avg = round(sum(comp_values) / len(comp_values), 2) if comp_values else 0
                comp_per90_avg = round(sum(comp_per90) / len(comp_per90), 2) if comp_per90 else 0
                comp_poss_avg = round(sum(comp_poss) / len(comp_poss), 1) if comp_poss else None
                comp_lines = []
                for p in position_comparison[:7]:
                    p_pos_label = f"{p.get('position', '?')}"
                    if p.get('role'):
                        p_pos_label += f" ({p['role']})"
                    poss_str = f" | team poss: {p['teamPossession']}%" if p.get('teamPossession') else ""
                    comp_lines.append(f"  {p['name']} [{p_pos_label}] ({p['team']}, {p.get('venue','').upper()}) — {p['statValue']} {req.propType} in {p['minutes']}min (per90: {p['per90']}) | {p['date']} | rating: {p.get('rating', 'N/A')}{poss_str}")
                venue_note = f"All comparisons are {player_venue.upper()} performances only."
                poss_note = f"\nAverage team possession in these matches: {comp_poss_avg}%" if comp_poss_avg else ""
                position_context += f"""
[POSITION COMPARISON — {pos_short}s vs {req.opponentName} ({player_venue.upper()} only)]
{req.playerName} is a {pos_short}{f' ({player_role})' if player_role else ''}. {venue_note}
Below are other {player_position}s who played {player_venue.upper()} against {req.opponentName} recently:
{chr(10).join(comp_lines)}
Average {req.propType}: {comp_avg} | Per-90 avg: {comp_per90_avg} | Sample: {len(comp_values)} players{poss_note}
>>> Compare {req.playerName}'s projected {req.propType} against this positional baseline.
>>> Factor in possession context: teams with more possession tend to have more passing/creative stats; teams with less tend to have more defensive/counter-attacking stats.
>>> Consider {req.playerName}'s team expected possession profile vs the opponent. <<<"""
                position_comp_data = {
                    "position": display_position,
                    "positionShort": pos_short,
                    "players": position_comparison,
                    "avgStatValue": comp_avg,
                    "avgPer90": comp_per90_avg,
                    "avgPossession": comp_poss_avg,
                    "sampleSize": len(comp_values),
                    "propType": req.propType,
                    "opponent": req.opponentName,
                    "venue": player_venue,
                }

        # Compose data for Grok prediction
        final_data_parts = []
        if grok_digest:
            final_data_parts.append(f"[GROK INTEL BRIEF]\n{grok_digest}")
        if data_digest:
            final_data_parts.append(f"[DATA DIGEST]\n{data_digest}")
        if wave2_supplement:
            final_data_parts.append(f"[GAME LOGS]\n{json.dumps(wave2_supplement, default=str)[:5000]}")

        if final_data_parts:
            final_data = "\n\n".join(final_data_parts)[:10000]
            if saves_context:
                final_data += f"\n\n{saves_context}"
            if position_context:
                final_data += f"\n{position_context}"
        else:
            final_data = json.dumps(historical_data, default=str)[:8000]

        # =============================================
        # MATCH DOMINANCE CONTEXT: Inject possession & multiplier into AI prompt
        # =============================================
        if match_dominance.get("expectedPoss", 50) != 50 or match_dominance.get("notes"):
            dom_notes = "\n".join(f"  - {n}" for n in match_dominance.get("notes", []))
            dom_context = f"""
[MATCH DOMINANCE ANALYSIS — DO NOT IGNORE]
Expected possession for {req.teamName}: {match_dominance['expectedPoss']}% (season avg: {match_dominance.get('teamSeasonAvg', '?')}%)
Expected possession for {req.opponentName}: {match_dominance['oppExpectedPoss']}% (season avg: {match_dominance.get('oppSeasonAvg', '?')}%)
{dom_notes}
>>> CRITICAL: If expected possession is HIGHER than season average, pass-dependent players (DLP, CM, CAM) WILL exceed their historical averages.
>>> A deep-lying playmaker on a team expected at 65%+ possession will have significantly MORE pass attempts than their season average suggests.
>>> Conversely, defenders on low-possession teams will have MORE tackles/interceptions than average.
>>> DO NOT just project from historical averages when match context predicts a clear possession advantage or disadvantage. <<<"""
            final_data += dom_context

        # Build match context (round/stage, knockout detection)
        match_context = ""
        if match_odds:
            match_round = match_odds.get("matchRound", "")
            match_league_name = match_odds.get("matchLeague", "")
            match_date = match_odds.get("matchDate", "")
            if match_round or match_league_name:
                knockout_keywords = ["final", "quarter", "semi", "round of", "knockout", "elimination", "playoff"]
                is_knockout = any(kw in match_round.lower() for kw in knockout_keywords) if match_round else False
                match_context = f"\n[MATCH CONTEXT] {match_league_name} — {match_round}"
                if match_date:
                    match_context += f" | Date: {match_date[:10]}"
                if is_knockout:
                    match_context += "\n** KNOCKOUT/ELIMINATION MATCH — Higher stakes, tactical conservatism likely, possible extra time. Account for this in projections.**"

        # Inject hit rate context into prompt
        hit_rate_context = ""
        hit_rates = wave2_supplement.get("playerGameLogs", {}).get("hitRates")
        if hit_rates:
            hit_rate_context = f"""
[OVER/UNDER HIT RATE — CRITICAL DATA]
{hit_rates['summary']}
>>> If over-rate >= 65%, strongly lean OVER. If under-rate >= 65%, lean UNDER. If neither exceeds 60%, treat as close call — lower confidence. <<<"""

        prompt = f"""{req.playerName} ({display_position}) — plays for {req.teamName} ({player_venue.upper()}) | OPPONENT: {req.opponentName} | {req.propType} line {req.line}
Odds: {json.dumps(match_odds.get('bookmakerOdds',{}), default=str) if match_odds else 'N/A'}{match_context}
{pronoun_note}
recentSamples=[]
{hit_rate_context}
{bayesian_prompt_anchor}
{final_data[:6000]}

Analyze ALL data thoroughly. Return JSON only."""

        EMERGENT_PROXY = "https://integrations.emergentagent.com/llm"

        async def call_emergent_direct(model_name, label):
            """Call Claude/other models directly via OpenAI SDK through Emergent proxy."""
            try:
                client = OpenAI(api_key=EMERGENT_LLM_KEY, base_url=EMERGENT_PROXY + "/v1")
                loop = aio.get_event_loop()
                def _run():
                    return client.chat.completions.create(
                        model=model_name,
                        messages=[
                            {"role": "system", "content": PREDICTION_SYSTEM},
                            {"role": "user", "content": prompt},
                        ],
                        max_tokens=2500,
                        temperature=0.0,
                    )
                resp = await aio.wait_for(loop.run_in_executor(None, _run), timeout=40)
                text = resp.choices[0].message.content.strip()
                if text.startswith("```"):
                    text = "\n".join(ln for ln in text.split("\n") if not ln.strip().startswith("```"))
                start = text.find("{")
                if start >= 0:
                    for end_pos in range(len(text), start, -1):
                        if text[end_pos - 1] == "}":
                            try:
                                result = json.loads(text[start:end_pos])
                                result["_source"] = label
                                return result
                            except json.JSONDecodeError:
                                continue
                raise ValueError("No valid JSON in response")
            except Exception as e:
                print(f"[MULTI-AI] {label} failed: {e}")
                return None

        async def call_grok(label="grok", model="grok-4-1-fast-non-reasoning"):
            try:
                grok_client = OpenAI(api_key=XAI_API_KEY, base_url="https://api.x.ai/v1")
                grok_messages = [
                    {"role": "system", "content": PREDICTION_SYSTEM},
                    {"role": "user", "content": prompt},
                ]
                loop = aio.get_event_loop()
                def _run():
                    return grok_client.chat.completions.create(
                        model=model,
                        messages=grok_messages,
                        max_tokens=2500,
                        temperature=0.0,
                    )
                grok_result = await aio.wait_for(loop.run_in_executor(None, _run), timeout=40)
                text = grok_result.choices[0].message.content.strip()
                if text.startswith("```"):
                    text = "\n".join(ln for ln in text.split("\n") if not ln.strip().startswith("```"))
                # Robust JSON extraction
                start = text.find("{")
                if start >= 0:
                    for end_pos in range(len(text), start, -1):
                        if text[end_pos - 1] == "}":
                            try:
                                result = json.loads(text[start:end_pos])
                                result["_source"] = label
                                return result
                            except json.JSONDecodeError:
                                continue
                raise ValueError("No valid JSON found in Grok response")
            except Exception as e:
                print(f"[MULTI-AI] {label} failed: {e}")
                return None

        # =============================================
        # GROK-ONLY: grok-3-mini (cost-efficient, Bayesian does the heavy lifting)
        # Falls back to Bayesian-only if all AI models fail
        # =============================================
        grok_result = None
        try:
            grok_result = await aio.wait_for(
                call_grok(label="grok3mini", model="grok-3-mini"),
                timeout=45
            )
        except Exception as e:
            print(f"[HYBRID] grok-3-mini exception: {e}")

        if not grok_result or not isinstance(grok_result, dict) or not grok_result.get("projectedValue"):
            # Grok failed — try once more with fast model
            print("[HYBRID] grok-3-mini failed, retrying with grok-4-1-fast")
            try:
                grok_result = await aio.wait_for(
                    call_grok(label="grok41fast", model="grok-4-1-fast-non-reasoning"),
                    timeout=40
                )
            except Exception as e:
                print(f"[HYBRID] grok-4-1-fast exception: {e}")

        pv = grok_result.get("projectedValue", 0) if grok_result and isinstance(grok_result, dict) else 0
        if not isinstance(pv, (int, float)) or pv <= 0:
            # Invalid projection — retry with fast model
            print(f"[HYBRID] Grok returned invalid projection: {pv}, retrying with fast model")
            try:
                grok_result = await aio.wait_for(
                    call_grok(label="grok41fast", model="grok-4-1-fast-non-reasoning"),
                    timeout=40
                )
                pv = grok_result.get("projectedValue", 0) if grok_result and isinstance(grok_result, dict) else 0
            except Exception as e:
                print(f"[HYBRID] grok-4-1-fast retry exception: {e}")
                pv = 0

        # BAYESIAN FALLBACK: If ALL Grok models failed, use Bayesian projection directly
        if not grok_result or not isinstance(grok_result, dict) or not isinstance(pv, (int, float)) or pv <= 0:
            if early_bayes and early_bayes.get("posteriorMean"):
                pv = early_bayes["posteriorMean"]
                grok_result = {
                    "projectedValue": pv,
                    "recommendation": early_bayes.get("recommendation", "over"),
                    "confidenceScore": max(early_bayes.get("pOver", 50), early_bayes.get("pUnder", 50)),
                    "reasoning": "AI models unavailable — projection based on pure Bayesian mathematical analysis.",
                    "_source": "bayesian_fallback",
                }
                print(f"[BAYESIAN FALLBACK] All Grok models failed — using Bayesian projection: {pv}")
            else:
                # No Bayesian data either — use the line as last resort
                pv = req.line
                grok_result = {
                    "projectedValue": pv,
                    "recommendation": "over",
                    "confidenceScore": 50,
                    "reasoning": "Insufficient data for mathematical projection. AI models unavailable.",
                    "_source": "fallback",
                }
                print(f"[FALLBACK] No Bayesian data and all Grok models failed — using line: {pv}")

        source_model = grok_result.get("_source", "grok3mini")
        print(f"[TIMING] {source_model} done: {_t.time()-_t0:.1f}s, proj={pv}")

        prediction = grok_result.copy()
        prediction.pop("_source", None)
        prediction["projectedValue"] = pv
        prediction["recommendation"] = "over" if pv > req.line else "under"

        # Confidence normalization
        cs = prediction.get("confidenceScore", 50)
        if isinstance(cs, (int, float)):
            prediction["confidenceScore"] = round(cs * 100 if cs <= 1 else cs)
        else:
            prediction["confidenceScore"] = 50

        prediction["consensusNote"] = f"Bayesian math projection. Grok provides tactical analysis only."
        prediction["modelBreakdown"] = [{
            "model": source_model,
            "recommendation": prediction["recommendation"],
            "projectedValue": pv,
            "confidenceScore": prediction["confidenceScore"],
        }]

        # Set confidence level
        cs = prediction.get("confidenceScore", 50)
        prediction["confidenceLevel"] = "Very High" if cs >= 75 else "High" if cs >= 65 else "Medium" if cs >= 50 else "Low"

        # Store dominance info — will be applied POST-FUSION to the final number
        prediction["matchDominance"] = {
            "applied": match_dominance["multiplier"] != 1.0,
            "multiplier": match_dominance["multiplier"],
            "expectedPoss": match_dominance["expectedPoss"],
            "teamSeasonAvg": match_dominance.get("teamSeasonAvg"),
            "oppSeasonAvg": match_dominance.get("oppSeasonAvg"),
            "notes": match_dominance["notes"],
        }

        # =============================================
        # BAYESIAN — Reuse early computation (already done before AI prompt)
        # =============================================
        real_bayes = early_bayes
        if real_bayes:
            prediction["bayesianMetrics"] = real_bayes
            prediction["confidenceInterval"] = real_bayes.get("confidenceInterval", prediction.get("confidenceInterval"))

        # =============================================
        # =============================================
        # BAYESIAN-ONLY PROJECTION
        #
        # The math OWNS the number. Period.
        # Grok provides tactical reasoning text only — no numeric influence.
        # The Bayesian posterior IS the projected value.
        # =============================================
        if real_bayes and real_bayes.get("priorSamples", 0) >= 3:
            bayesian_posterior = real_bayes["posteriorMean"]
            bayesian_prob = max(real_bayes.get("pOver", 50), real_bayes.get("pUnder", 50)) / 100
            bayesian_rec = real_bayes.get("recommendation", "over")
            ai_proj = prediction.get("projectedValue", req.line)
            ai_rec = prediction.get("recommendation", "over")

            divergence_pct = abs(ai_proj - bayesian_posterior) / max(bayesian_posterior, 1) * 100

            # Log when AI disagrees (for transparency in the UI)
            if divergence_pct > 10 and bayesian_rec != ai_rec:
                prediction["tacticalAlerts"] = prediction.get("tacticalAlerts", []) + [
                    f"AI reasoning suggested {ai_proj:.1f} ({ai_rec.upper()}) but math projects {bayesian_posterior:.1f} ({bayesian_rec.upper()}). Math prevails."
                ]
                print(f"[MATH OVERRIDE] AI={ai_proj}({ai_rec}) vs Bayes={bayesian_posterior}({bayesian_rec}) — {divergence_pct:.0f}% gap. Using math.")

            print(f"[PROJECTION] Bayesian={bayesian_posterior}({bayesian_rec}, {bayesian_prob:.0%}) | AI opinion={ai_proj}({ai_rec}) — MATH IS FINAL")

            prediction["projectedValue"] = bayesian_posterior
            prediction["recommendation"] = bayesian_rec
            prediction["fusionApplied"] = {
                "aiProjection": ai_proj,
                "aiRecommendation": ai_rec,
                "bayesianPosterior": bayesian_posterior,
                "bayesianRecommendation": bayesian_rec,
                "bayesianConfidence": round(bayesian_prob * 100, 1),
                "fusedProjection": bayesian_posterior,
                "fusedRecommendation": bayesian_rec,
                "weights": {"ai": 0, "bayesian": 1.0},
                "agreement": bayesian_rec == ai_rec,
                "divergencePct": round(divergence_pct, 1),
            }

        # =============================================
        # POST-PROJECTION DOMINANCE SCALING — SELECTIVE
        # Applied ONLY when a low-possession team faces a possession monster.
        # High-possession teams (>52% avg) keep their Bayesian projection as-is
        # because their pass counts remain high regardless of matchup.
        # =============================================
        poss_sensitive = {"pass_attempts", "passes", "key_passes", "crosses", "dribbles"}

        if req.propType in poss_sensitive and match_dominance.get("multiplier", 1.0) != 1.0:
            dom_mult = match_dominance["multiplier"]
            team_avg_poss = match_dominance.get("teamSeasonAvg", 50)
            current = prediction.get("projectedValue", req.line)

            if team_avg_poss < 52 and dom_mult < 0.92:
                # Low-possession team facing a dominant opponent — scale down
                post_dom = round(current * dom_mult, 1)
                prediction["projectedValue"] = post_dom
                prediction["recommendation"] = "over" if post_dom > req.line else "under"
                print(f"[DOMINANCE] APPLIED: {current} × {dom_mult:.3f} → {post_dom} (team avg {team_avg_poss:.0f}% < 52% threshold)")
            else:
                would_be = round(current * dom_mult, 1)
                print(f"[DOMINANCE] SKIPPED: {current} × {dom_mult:.3f} would be {would_be} (team avg {team_avg_poss:.0f}% — Bayesian covers this)")

        if req.propType in poss_sensitive and game_tempo.get("tempoMultiplier", 1.0) != 1.0:
            tempo_mult = game_tempo["tempoMultiplier"]
            current = prediction.get("projectedValue", req.line)
            print(f"[TEMPO] LOGGED ONLY: {current} × {tempo_mult:.3f} (NOT applied)")

        if favorite_dampening.get("applied") and req.propType in poss_sensitive:
            fav_factor = favorite_dampening["dampeningFactor"]
            current = prediction.get("projectedValue", req.line)
            print(f"[FAV DAMPEN] LOGGED ONLY: {current} × {1.0-fav_factor:.3f} (NOT applied)")

        # HARD GUARD: recommendation MUST match the FINAL projected value vs line
        final_proj = prediction.get("projectedValue", req.line)
        prediction["recommendation"] = "over" if final_proj > req.line else "under"

        # =============================================
        # POST-CONSENSUS CONFIDENCE GUARDS
        # =============================================
        conf = prediction.get("confidenceScore", 50)
        proj_val = prediction.get("projectedValue", req.line)
        edge = abs(proj_val - req.line)
        rec = prediction.get("recommendation", "over")

        # Guard 1: Binary line (0.5) — UNDER means zero, very risky
        if req.line <= 0.5 and rec == "under" and conf > 55:
            prediction["confidenceScore"] = 55
            prediction["tacticalAlerts"] = prediction.get("tacticalAlerts", []) + [
                "Binary line (0.5): UNDER requires ZERO of this stat — high-risk"
            ]
            print(f"[GUARD] Binary line 0.5 UNDER: confidence capped at 55% (was {conf})")

        # Guard 2: Tight edge — projected value within ±1 of line
        if edge < 1.0 and conf > 58:
            prediction["confidenceScore"] = 58
            prediction["tacticalAlerts"] = prediction.get("tacticalAlerts", []) + [
                f"Tight edge: projection {proj_val} is within 1.0 of line {req.line} — marginal"
            ]
            print(f"[GUARD] Tight edge ({edge:.1f}): confidence capped at 58% (was {conf})")

        # Guard 3: Coin-flip zone — projection within ±3 of line AND Bayesian confidence < 60%
        if edge < 3.0 and real_bayes:
            bayes_conf = max(real_bayes.get("pOver", 50), real_bayes.get("pUnder", 50))
            if bayes_conf < 60:
                old_conf = prediction.get("confidenceScore", 50)
                prediction["confidenceScore"] = min(old_conf, 52)
                prediction["coinFlip"] = True
                prediction["tacticalAlerts"] = prediction.get("tacticalAlerts", []) + [
                    f"COIN FLIP: Math projects {real_bayes.get('posteriorMean')} vs line {req.line} (edge {edge:.1f}). Bayesian P={bayes_conf}%. This is a variance-driven outcome."
                ]
                print(f"[GUARD] Coin-flip zone: edge={edge:.1f}, Bayesian P={bayes_conf}% → capped at 52% (was {old_conf})")

        # Guard 3: UNDER skew penalty — stats have positive skew
        if rec == "under":
            adj_conf = prediction.get("confidenceScore", 50)
            penalty = min(4, max(2, round(edge * 0.5)))  # 2-4% penalty based on edge size
            prediction["confidenceScore"] = max(45, adj_conf - penalty)
            if adj_conf != prediction["confidenceScore"]:
                print(f"[GUARD] UNDER skew penalty: -{penalty}% confidence ({adj_conf} → {prediction['confidenceScore']})")

        # Recalculate confidence level after guards
        cs = prediction.get("confidenceScore", 50)
        prediction["confidenceLevel"] = "Very High" if cs >= 75 else "High" if cs >= 65 else "Medium" if cs >= 50 else "Low"

        # HARD GUARD: recommendation MUST match the FINAL projected value vs line
        final_proj_cal = prediction.get("projectedValue", req.line)
        prediction["recommendation"] = "over" if final_proj_cal > req.line else "under"

        # Force-set identity fields from REQUEST data — never trust AI output for these
        player_team_display = req.teamName or (player_stats.get("statistics", [{}])[0].get("team", {}).get("name", "") if player_stats else "")
        prediction["player"] = {
            "id": req.playerId,
            "name": req.playerName,
            "team": player_team_display,
            "position": display_position or "Unknown",
            "role": display_role or "",
        }
        prediction["opponent"] = req.opponentName
        prediction["propType"] = req.propType
        prediction["line"] = req.line
        prediction.setdefault("projectedValue", req.line)
        prediction.setdefault("recommendation", "over")
        prediction.setdefault("confidenceScore", 50)
        prediction.setdefault("confidenceLevel", "Medium")
        prediction.setdefault("confidenceInterval", [req.line * 0.8, req.line * 1.2])
        prediction.setdefault("recentSamples", [])
        # OVERRIDE: Always use real game log data instead of AI-generated samples
        if real_recent_samples:
            prediction["recentSamples"] = real_recent_samples
        prediction.setdefault("bayesianMetrics", {"priorMean": req.line, "momentumEffect": 0, "covariateAdjustment": 0, "reversalFlag": "stable"})

        prediction.setdefault("probabilityCurve", [])
        prediction.setdefault("reasoning", "Analysis based on available data.")
        prediction.setdefault("tacticalInsights", "")

        # OVERRIDE: Lock matchupOverview to REAL DATA so it never fluctuates between predictions
        real_matchup = prediction.get("matchupOverview", {})
        # 1. Possession: Use MATCH DOMINANCE model (opponent-aware + odds-adjusted)
        if match_dominance.get("expectedPoss", 50) != 50:
            if player_venue == "home":
                real_matchup["expectedPossession"] = {
                    "home": match_dominance["expectedPoss"],
                    "away": match_dominance["oppExpectedPoss"]
                }
            else:
                real_matchup["expectedPossession"] = {
                    "home": match_dominance["oppExpectedPoss"],
                    "away": match_dominance["expectedPoss"]
                }
        elif team_fixture_stats or opponent_fixture_stats:
            # Fallback: simple average if dominance model has no data
            def avg_possession(stats_list):
                vals = []
                for s in (stats_list or []):
                    p = s.get("possession")
                    if p is not None:
                        try:
                            vals.append(float(str(p).replace("%", "")))
                        except (ValueError, TypeError):
                            pass
                return round(sum(vals) / len(vals), 0) if vals else None
            team_poss = avg_possession(team_fixture_stats)
            opp_poss = avg_possession(opponent_fixture_stats)
            if team_poss is not None and opp_poss is not None:
                total = team_poss + opp_poss
                if total > 0:
                    team_poss = round(team_poss / total * 100)
                    opp_poss = 100 - team_poss
                real_matchup["expectedPossession"] = {
                    "home": team_poss if player_venue == "home" else opp_poss,
                    "away": opp_poss if player_venue == "home" else team_poss
                }
            elif team_poss is not None:
                real_matchup["expectedPossession"] = {
                    "home": team_poss if player_venue == "home" else (100 - team_poss),
                    "away": (100 - team_poss) if player_venue == "home" else team_poss
                }
        # 2. Moneyline + favorite from real odds data
        if match_odds:
            if match_odds.get("americanOdds"):
                ao = match_odds["americanOdds"]
                if ao.get("home") and ao.get("away") and ao.get("draw"):
                    real_matchup["moneyline"] = {
                        "home": str(ao["home"]),
                        "draw": str(ao["draw"]),
                        "away": str(ao["away"]),
                    }
            elif match_odds.get("bookmakerOdds"):
                bo = match_odds["bookmakerOdds"]
                h, d, a = bo.get("homeWin", ""), bo.get("draw", ""), bo.get("awayWin", "")
                if h and d and a and h != "N/A" and d != "N/A" and a != "N/A":
                    real_matchup["moneyline"] = {"home": h, "draw": d, "away": a}
            if match_odds.get("favorite"):
                real_matchup["favorite"] = match_odds["favorite"]
        # 3. Game type from real stats — deterministic classification
        if team_fixture_stats and opponent_fixture_stats:
            def avg_stat(stats_list, key):
                vals = [s.get(key) for s in stats_list if s.get(key) is not None]
                return sum(vals) / len(vals) if vals else 0
            team_avg_shots = avg_stat(team_fixture_stats, "totalShots")
            opp_avg_shots = avg_stat(opponent_fixture_stats, "totalShots")
            combined_shots = team_avg_shots + opp_avg_shots
            poss_diff = abs((real_matchup.get("expectedPossession", {}).get("home", 50)) - 50)
            if combined_shots >= 28:
                real_matchup["expectedGameType"] = "open"
            elif combined_shots <= 18:
                real_matchup["expectedGameType"] = "cagey"
            elif poss_diff >= 12:
                real_matchup["expectedGameType"] = "one-sided"
            else:
                real_matchup["expectedGameType"] = "high-tempo" if combined_shots >= 23 else "cagey"
        # 4. Always set team names from request data (deterministic)
        player_team = req.teamName or (player_stats.get("statistics", [{}])[0].get("team", {}).get("name", "") if player_stats else "")
        real_matchup["homeTeam"] = player_team if player_venue == "home" else req.opponentName
        real_matchup["awayTeam"] = req.opponentName if player_venue == "home" else player_team
        prediction["matchupOverview"] = real_matchup

        # Add match context (competition name, round) for frontend display
        if match_odds:
            mc = {}
            if match_odds.get("matchLeague"):
                mc["league"] = match_odds["matchLeague"]
            if match_odds.get("matchRound"):
                mc["round"] = match_odds["matchRound"]
            if match_odds.get("matchDate"):
                mc["date"] = match_odds["matchDate"][:10]
            if mc:
                prediction["matchContext"] = mc

        # DATA QUALITY INDICATOR — flag when API data might be unreliable
        total_game_logs = len(player_game_logs)
        gl_target_field_map_check = {
            "pass_attempts": "passes_total", "shots": "shots_total", "shots_on_target": "shots_on",
            "tackles": "tackles_total", "key_passes": "passes_key", "shots_assisted": "passes_key",
            "saves": "goals_saves",
            "interceptions": "tackles_interceptions", "blocks": "tackles_blocks",
            "dribbles": "dribbles_attempts", "fouls_drawn": "fouls_drawn",
            "crosses": "passes_crosses", "clearances": "tackles_clearances",
            "goals": "goals_total", "assists": "goals_assists",
            "duels_won": "duels_won", "yellow_cards": "cards_yellow",
            "fouls_committed": "fouls_committed",
        }
        target_check = gl_target_field_map_check.get(req.propType, "passes_total")
        games_with_data = sum(1 for g in player_game_logs if g.get(target_check) is not None)
        games_with_none = total_game_logs - games_with_data
        if total_game_logs > 0 and games_with_none / total_game_logs >= 0.3:
            prediction["dataQuality"] = {
                "level": "limited",
                "message": f"API data incomplete — {games_with_none} of {total_game_logs} recent games missing {req.propType} stats. Cross-referenced sources used for analysis.",
                "gamesWithData": games_with_data,
                "totalGames": total_game_logs,
            }
        elif total_game_logs < 3:
            prediction["dataQuality"] = {
                "level": "low",
                "message": f"Only {total_game_logs} game logs available. Limited sample size for accurate projection.",
                "gamesWithData": games_with_data,
                "totalGames": total_game_logs,
            }
        else:
            prediction["dataQuality"] = {
                "level": "good",
                "message": "",
                "gamesWithData": games_with_data,
                "totalGames": total_game_logs,
            }

        # Compact analysis summary for the UI
        prop_key = req.propType or ""
        if prop_key == "shots_on_target":
            stat_label = "Shots on Target"
        elif prop_key == "saves":
            stat_label = "Goalkeeper Saves"
        else:
            stat_label = {
                "pass_attempts": "Pass Attempts",
                "shots": "Shots",
                "tackles": "Tackles",
                "key_passes": "Key Passes",
                "saves": "Saves",
                "interceptions": "Interceptions",
                "blocks": "Blocks",
                "dribbles": "Dribbles",
                "fouls_drawn": "Fouls Drawn",
            }.get(prop_key, prop_key.replace("_", " ").title())

        venue_samples = [g for g in player_game_logs if g.get("venue") == player_venue and g.get(target_check) is not None]
        opp_samples = [g for g in opponent_fixture_stats if g.get("shotsOnTarget") is not None] if req.propType == "saves" else []
        venue_avg = round(sum((g.get(target_check) or 0) for g in venue_samples) / len(venue_samples), 2) if venue_samples else None
        opp_allowed_avg = None
        if req.propType in ("shots_on_target", "saves"):
            if req.propType == "shots_on_target":
                opp_allowed_avg = round(sum((g.get("shotsOnTarget") or 0) for g in opp_samples) / len(opp_samples), 2) if opp_samples else None
            else:
                opp_allowed_avg = round(sum((g.get("shotsOnTarget") or 0) for g in opp_samples) / len(opp_samples), 2) if opp_samples else None

        prediction["analysisSummary"] = {
            "statLabel": stat_label,
            "venue": player_venue,
            "venueSampleSize": len(venue_samples),
            "venueAverage": venue_avg,
            "opponentAllowedAverage": opp_allowed_avg,
            "goalkeeperSaveRate": gk_formula_data.get("gkSaveRate") if gk_formula_data else None,
            "goalkeeperSaveSample": gk_formula_data.get("gkSampleSize") if gk_formula_data else None,
            "opponentShotsOnTarget": gk_formula_data.get("opponentAvgSOT") if gk_formula_data else None,
        }

        # SYNTHESIS STEP: Combine all AI analyses into one rich tactical breakdown
        # This recreates the original Grok+Gemini depth — one AI synthesizes all others' insights
        rec = prediction.get('recommendation', 'over').upper()
        line = prediction.get('line', req.line)
        proj = prediction.get('projectedValue', '?')
        conf = prediction.get('confidenceScore', '?')
        pl = {
            "pass_attempts": "Pass Attempts",
            "shots": "Shots",
            "shots_on_target": "Shots on Target",
            "tackles": "Tackles",
            "key_passes": "Key Passes",
            "saves": "Saves",
            "interceptions": "Interceptions",
            "blocks": "Blocks",
            "dribbles": "Dribbles",
            "fouls_drawn": "Fouls Drawn",
        }.get(req.propType, req.propType)
        consensus_note = prediction.get('consensusNote', '')

        # Gather text from Grok response for synthesis
        all_texts = []
        bits = []
        for field in ["tacticalBreakdown", "reasoning", "scenarioAnalysis", "keyEvidence", "sharpSummary", "gameFlowDynamics", "sensitivityTests", "subRisk", "uncertaintyNote"]:
            val = prediction.get(field, "")
            if isinstance(val, dict):
                val = json.dumps(val)
            if val and len(str(val)) > 10:
                bits.append(f"{field}: {val}")
        if bits:
            all_texts.append("[grok]\n" + "\n".join(bits))

        synthesis_input = "\n\n".join(all_texts)

        # Fast Gemini synthesis — combine insights into one cohesive breakdown
        # Build dynamic context from all available data
        pos_context_for_synth = ""
        if display_position:
            pos_context_for_synth = f"\nPosition: {display_position}"
            if display_role:
                pos_context_for_synth += f" ({display_role})"
        comp_context = ""
        if position_comp_data:
            pc = position_comp_data
            comp_context = f"\nPosition Comparison ({player_venue.upper()} only): {pc['sampleSize']} {pc.get('positionShort', '')}s vs {pc['opponent']} averaged {pc['avgStatValue']} {pl.lower()} (per-90: {pc['avgPer90']})"
            if pc.get('avgPossession'):
                comp_context += f" | Avg team possession: {pc['avgPossession']}%"

        try:
            synth_prompt = f"""You are synthesizing multiple AI analyses into ONE elite tactical breakdown for a {pl} prop prediction.

FINAL VERDICT: {rec} {line} {pl} (Projected: {proj}, Confidence: {conf}%, {consensus_note})
Player: {req.playerName} vs {req.opponentName} ({player_venue.upper()}){pos_context_for_synth}{comp_context}

Here are the individual AI analyses to synthesize:

{synthesis_input[:4000]}

Write a single cohesive ~1500 char markdown tactical breakdown. Format:
**Verdict: {rec} {line} {pl}**
[1-2 sentence sharp summary with projection vs line]

**Position & Role Context**
[1-2 sentences about how the player's specific position ({display_position}) and role ({display_role or 'N/A'}) affects their {pl.lower()} output. How does this role generate or limit this stat? Reference the positional comparison data if available. Mention venue ({player_venue.upper()}) — are {player_venue} performances typically better or worse for this stat? If possession data is available, note how expected possession impacts this prop type.]

**Analysis**
[3-4 sentences combining the BEST insights from ALL analyses. Cite specific numbers: per-game averages, venue splits ({player_venue.upper()} context), sample sizes, opponent tendencies. Reference the positional comparison baseline when relevant. Factor in possession context — how does team possession % correlate with {pl.lower()} output for a {display_position}? Merge complementary insights, resolve contradictions]

**Game Script Scenarios**
[Best case / Worst case / Most likely — with stat projections for each]

**Key Evidence**
[3-4 bullet points — strongest data points from across all analyses, including position comparison baseline and venue-specific trends]

**Risk Radar**
[Sub risk, sensitivity factors, what would flip the pick]

**TL;DR** — {rec} {line} at {conf}% confidence. Projected: {proj} {pl.lower()}. {consensus_note}

Rules: No AI model names. Be specific with numbers. Be decisive. ALWAYS reference the player's position/role and how it impacts the prop. ALWAYS reference venue (home/away) context."""

            synth_client = OpenAI(api_key=EMERGENT_LLM_KEY, base_url=EMERGENT_PROXY + "/v1")
            loop = aio.get_event_loop()
            def _run_synth():
                return synth_client.chat.completions.create(
                    model="gemini/gemini-2.0-flash",
                    messages=[{"role": "user", "content": synth_prompt}],
                    max_tokens=1500,
                    temperature=0.2,
                )
            synth_resp = await aio.wait_for(loop.run_in_executor(None, _run_synth), timeout=10)
            synth_text = synth_resp.choices[0].message.content.strip()
            if synth_text and len(synth_text) > 200:
                prediction["tacticalBreakdown"] = synth_text
                print(f"[TIMING] Synthesis done: {_t.time()-_t0:.1f}s total, {len(synth_text)} chars")
            else:
                raise ValueError("Synthesis too short")
        except Exception as synth_err:
            print(f"[SYNTHESIS] Fallback — {synth_err}")
            # Fallback: Build from fields manually
            tb_parts = []
            if prediction.get('sharpSummary'):
                tb_parts.append(f"**Verdict: {rec} {line} {pl}**\n{prediction['sharpSummary']}")
            else:
                tb_parts.append(f"**Verdict: {rec} {line} {pl}**\nProjected {proj} {pl.lower()} — consensus favors {rec.lower()}.")

            if prediction.get('reasoning') and len(str(prediction['reasoning'])) > 30:
                tb_parts.append(f"**Analysis**\n{prediction['reasoning']}")

            if prediction.get('scenarioAnalysis'):
                tb_parts.append(f"**Game Script Scenarios**\n{prediction['scenarioAnalysis']}")
            if prediction.get('keyEvidence'):
                tb_parts.append(f"**Key Evidence**\n{prediction['keyEvidence']}")

            risk = []
            if prediction.get('sensitivityTests'):
                risk.append(f"- Sensitivity: {prediction['sensitivityTests']}")
            if prediction.get('subRisk'):
                risk.append(f"- Sub Risk: {prediction['subRisk']}")
            if prediction.get('uncertaintyNote'):
                risk.append(f"- Key Risk: {prediction['uncertaintyNote']}")
            if risk:
                tb_parts.append("**Risk Radar**\n" + "\n".join(risk))
            if prediction.get('gameFlowDynamics'):
                tb_parts.append(f"**Game Flow**\n{prediction['gameFlowDynamics']}")

            tb_parts.append(f"**TL;DR** — {rec} {line} at {conf}% confidence. Projected: {proj} {pl.lower()}. {consensus_note}")
            prediction["tacticalBreakdown"] = "\n\n".join(tb_parts)

        # Save to MongoDB
        prediction["_created"] = datetime.now(timezone.utc).isoformat()
        prediction["_request"] = req.model_dump()

        # Attach match stat data for frontend heat maps/visualizations
        if team_fixture_stats:
            prediction["teamMatchStats"] = team_fixture_stats
        if opponent_fixture_stats:
            prediction["opponentMatchStats"] = opponent_fixture_stats
        if historical_data.get("h2hPlayerStats"):
            prediction["h2hPlayerStats"] = historical_data["h2hPlayerStats"]
        if historical_data.get("playerGameLogs"):
            prediction["playerGameLogs"] = historical_data["playerGameLogs"]
        if gk_formula_data:
            prediction["gkFormula"] = gk_formula_data
        if position_comp_data:
            prediction["positionComparison"] = position_comp_data

        await db.predictions.insert_one(prediction)
        prediction.pop("_id", None)

        return prediction

    except (json.JSONDecodeError, aio.TimeoutError):
        # Return a safe fallback prediction
        return {
            "player": {"id": req.playerId, "name": req.playerName, "team": req.teamName, "position": "Unknown"},
            "opponent": req.opponentName,
            "propType": req.propType,
            "line": req.line,
            "projectedValue": req.line,
            "recommendation": "over",
            "confidenceScore": 50,
            "confidenceLevel": "Medium",
            "confidenceInterval": [req.line * 0.8, req.line * 1.2],
            "recentSamples": [],
            "bayesianMetrics": {"priorMean": req.line, "momentumEffect": 0, "covariateAdjustment": 0, "reversalFlag": "stable"},
            "probabilityCurve": [],
            "reasoning": "AI analysis returned an invalid format. Displaying fallback prediction.",
            "tacticalInsights": "",
            "explanation": "Fallback prediction due to AI parsing error."
        }
    except HTTPException:
        raise  # Re-raise HTTPException directly (e.g., 400 for teamId=0)
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Prediction failed: {str(e)}")

