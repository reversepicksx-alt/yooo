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
from routes.miss_analysis import get_calibration_adjustment

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

        # Guard: reject if critical IDs are missing
        if not actual_team_id or actual_team_id == 0:
            raise HTTPException(status_code=400, detail="Player's team could not be resolved. Please try scanning again.")
        if not req.opponentId or req.opponentId == 0:
            raise HTTPException(status_code=400, detail="Opponent team could not be resolved. Please try scanning again.")

        # Fire ALL API calls at once (optimized — kept odds for game context)
        player_data_task = get_player_data()
        async def get_team_stats_multi_season(team_id, lid):
            for s in [CURRENT_SEASON + 1, CURRENT_SEASON, CURRENT_SEASON - 1]:
                result = await safe_fetch("teams/statistics", {"team": team_id, "league": lid, "season": s})
                if result:
                    return result
            return None

        async def get_match_odds():
            """Get bookmaker odds for the specific upcoming fixture between team and opponent"""
            try:
                fixtures = []
                # First try: h2h next fixture (no season filter — next=N handles it)
                try:
                    h2h_fixtures = await api_football_request("fixtures/headtohead", {
                        "h2h": f"{actual_team_id or 40}-{req.opponentId}",
                        "next": 3,
                    })
                    if h2h_fixtures:
                        fixtures = h2h_fixtures
                except Exception:
                    pass

                # Fallback: get team's next matches and find opponent
                if not fixtures:
                    try:
                        next_fixtures = await api_football_request("fixtures", {"team": actual_team_id or 40, "next": 5})
                        if next_fixtures:
                            for nf in next_fixtures:
                                home_id = nf.get("teams", {}).get("home", {}).get("id")
                                away_id = nf.get("teams", {}).get("away", {}).get("id")
                                if req.opponentId in (home_id, away_id):
                                    fixtures = [nf]
                                    break
                            if not fixtures:
                                fixtures = next_fixtures[:1]
                    except Exception:
                        pass
                if not fixtures:
                    return None
                fid = fixtures[0].get("fixture", {}).get("id")
                result = {}
                # Extract round/stage info (e.g., "Quarter-finals", "Group A - 3")
                match_round = fixtures[0].get("league", {}).get("round", "")
                match_league = fixtures[0].get("league", {}).get("name", "")
                match_date = fixtures[0].get("fixture", {}).get("date", "")
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

        team_stats_task = get_team_stats_multi_season(actual_team_id or 40, league_id)
        opponent_stats_task = get_team_stats_multi_season(req.opponentId, league_id)
        h2h_task = safe_fetch("fixtures/headtohead", {"h2h": f"{actual_team_id or 40}-{req.opponentId}", "last": 10}, [])

        async def get_standings_multi_season():
            for s in [CURRENT_SEASON + 1, CURRENT_SEASON, CURRENT_SEASON - 1]:
                result = await safe_fetch("standings", {"league": league_id, "season": s})
                if result:
                    return result
            return None

        standings_task = get_standings_multi_season()
        fixtures_task = get_recent_fixtures_fast(actual_team_id or 40, 50)
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
            """Fetch per-match team stats — ALL fixtures fetched in parallel"""
            async def fetch_one(fix):
                fid = fix.get("fixtureId")
                if not fid:
                    return None
                try:
                    data = await api_football_request("fixtures/statistics", {"fixture": fid})
                    if not data:
                        return None
                    for team_data in data:
                        if team_data.get("team", {}).get("id") == team_id:
                            raw_stats = {}
                            for s in team_data.get("statistics", []):
                                raw_stats[s.get("type", "")] = s.get("value")
                            return {
                                "date": fix.get("date", "")[:10],
                                "opponent": fix.get("opponent", ""),
                                "venue": fix.get("venue", ""),
                                "score": f"{fix.get('homeGoals',0)}-{fix.get('awayGoals',0)}",
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
                except Exception:
                    return None

            tasks = [fetch_one(fix) for fix in fixture_list[:limit]]
            results_raw = await aio.gather(*tasks, return_exceptions=True)
            return [r for r in results_raw if r and not isinstance(r, Exception)]

        # 2. Player game-by-game box scores from recent fixtures
        async def fetch_player_game_logs(fixture_list, player_id, limit=8):
            """Fetch player's individual stats — ALL fixtures fetched in parallel"""
            player_name_lower = strip_accents(req.playerName.lower().split()[-1]) if req.playerName else ""

            async def fetch_one_log(fix):
                fid = fix.get("fixtureId")
                if not fid:
                    return None
                try:
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
                        return None
                    stats = matched_stats
                    minutes = stats.get("games", {}).get("minutes") or 0
                    rating = stats.get("games", {}).get("rating")
                    game_log = {
                        "date": fix.get("date", "")[:10],
                        "opponent": fix.get("opponent", ""),
                        "venue": fix.get("venue", ""),
                        "score": f"{fix.get('homeGoals',0)}-{fix.get('awayGoals',0)}",
                        "minutes": minutes,
                        "rating": float(rating) if rating else None,
                        "league": fix.get("league", ""),
                        "round": fix.get("round", ""),
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
                    }
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
            return results

        # =============================================
        # POSITION COMPARISON: Same-position players vs opponent
        # =============================================
        FIXTURE_POS_MAP = {"Goalkeeper": "G", "Defender": "D", "Midfielder": "M", "Attacker": "F"}
        PROP_STAT_KEYS = {
            "pass_attempts": ("passes", "total"), "shots": ("shots", "total"),
            "shots_on_target": ("shots", "on"), "tackles": ("tackles", "total"),
            "key_passes": ("passes", "key"), "saves": ("goals", "saves"),
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
        opponent_recent_raw = await api_football_request("fixtures", {"team": req.opponentId, "last": 15})
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
        player_game_logs_task = fetch_player_game_logs(venue_first_fixtures, req.playerId, 30)

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

        # Wave 2: Fetch deep fixture data in parallel (NO Grok — too slow for proxy timeout)
        # Grok stays in follow-up chat (tactical.py) where it's user-initiated
        all_wave2 = aio.gather(
            team_fixture_stats_task, opponent_fixture_stats_task, player_game_logs_task,
            return_exceptions=True
        )
        try:
            results = await aio.wait_for(all_wave2, timeout=12)
        except aio.TimeoutError:
            results = [None, None, None]

        team_fixture_stats = results[0] if not isinstance(results[0], (Exception, type(None))) else []
        opponent_fixture_stats = results[1] if not isinstance(results[1], (Exception, type(None))) else []
        player_game_logs = results[2] if not isinstance(results[2], (Exception, type(None))) else []
        grok_analysis = None
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

            historical_data["playerGameLogs"] = game_log_summary

        # =============================================
        # BUILD REAL RECENT SAMPLES FROM GAME LOGS
        # =============================================
        # These replace Gemini-generated samples with actual API-Sports data
        real_recent_samples = []
        if player_game_logs:
            gl_target_field_map = {
                "pass_attempts": "passes_total", "shots": "shots_total", "shots_on_target": "shots_on",
                "tackles": "tackles_total", "key_passes": "passes_key", "saves": "goals_saves",
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
                "saves": ("goals", "saves"),
                "interceptions": ("tackles", "interceptions"),
                "blocks": ("tackles", "blocks"),
                "dribbles": ("dribbles", "attempts"),
                "fouls_drawn": ("fouls", "drawn"),
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
                                    "saves": stats.get("goals", {}).get("saves"),
                                    "interceptions": stats.get("tackles", {}).get("interceptions"),
                                    "blocks": stats.get("tackles", {}).get("blocks"),
                                    "dribbles": stats.get("dribbles", {}).get("attempts"),
                                    "fouls_drawn": stats.get("fouls", {}).get("drawn"),
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
                            from datetime import datetime, timezone
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
                        import litellm
                        litellm.drop_params = True
                        return await aio.wait_for(
                            litellm.acompletion(
                                model="gemini/gemini-2.0-flash",
                                messages=[
                                    {"role": "system", "content": "You are a football/soccer tactical analyst. Reply in EXACTLY this format on one line:\nPOSITION|ROLE\nNothing else."},
                                    {"role": "user", "content": pos_prompt},
                                ],
                                api_key=EMERGENT_LLM_KEY,
                                api_base=EMERGENT_PROXY,
                                custom_llm_provider="openai",
                                temperature=0,
                                max_tokens=50,
                            ),
                            timeout=8
                        )

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
                        from datetime import datetime, timezone
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
        # Gemini Flash (GE) + Grok (GK) + GPT-4.1-mini (GP)
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

RULES: GK saves capped by opp SoT. |proj-line|<0.3 → max 52% conf. Knockout = tactical conservatism + possible ET. recentSamples=[]. No AI model names.

JSON: {"projectedValue":0,"recommendation":"over|under","confidenceScore":0,"confidenceLevel":"","sharpSummary":"","reasoning":"","scenarioAnalysis":"","keyEvidence":"","sensitivityTests":"","subRisk":"","gameFlowDynamics":"","uncertaintyNote":"","tacticalBreakdown":"","matchupOverview":{"homeTeam":"","awayTeam":"","favorite":"","moneyline":{"home":"","draw":"","away":""},"expectedPossession":{"home":0,"away":0},"expectedGameType":"","keyMatchupFactor":""},"bayesianMetrics":{"priorMean":0,"momentumEffect":0,"covariateAdjustment":0,"reversalFlag":"stable"},"probabilityCurve":[],"recentSamples":[],"player":{"id":0,"name":"","team":"","position":""},"opponent":"","propType":"","line":0,"confidenceInterval":[0,0],"tacticalAlerts":[]}"""

        # Build the data payload — use GPT summary as primary + Wave 2 deep data as supplement
        wave2_supplement = {}
        if player_game_logs:
            target_field_map = {
                "pass_attempts": "passes_total", "shots": "shots_total", "shots_on_target": "shots_on",
                "tackles": "tackles_total", "key_passes": "passes_key", "saves": "goals_saves",
                "interceptions": "tackles_interceptions", "blocks": "tackles_blocks",
                "dribbles": "dribbles_attempts", "fouls_drawn": "fouls_drawn",
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
            gk_sot_faced_list = []
            recent_gk_logs = [g for g in player_game_logs if g.get("goals_saves") is not None and g.get("minutes", 0) > 0][:7]
            for g in recent_gk_logs:
                gk_saves_list.append(g.get("goals_saves"))
            gk_avg_saves = round(sum(gk_saves_list) / len(gk_saves_list), 2) if gk_saves_list else 0
            gk_saves_per90 = round(sum(gk_saves_list) / max(1, sum((g.get("minutes") or 0) for g in recent_gk_logs)) * 90, 2) if gk_saves_list else 0

            # Calculate save % from LAST 5-7 games only
            total_saves = sum(gk_saves_list) if gk_saves_list else 0
            games_with_saves = len(gk_saves_list)
            # Estimate goals conceded per game from team stats
            goals_against = None
            if team_stats:
                ga = team_stats.get("goals", {}).get("against", {})
                if ga:
                    total_ga = ga.get("total", {}).get(player_venue) or ga.get("total", {}).get("total") or 0
                    played = team_stats.get("fixtures", {}).get("played", {}).get(player_venue) or team_stats.get("fixtures", {}).get("played", {}).get("total") or 1
                    goals_against = round(total_ga / max(played, 1), 2) if total_ga else None

            # Save % calculation
            if total_saves > 0 and goals_against is not None and games_with_saves > 0:
                est_sot_faced = total_saves + (goals_against * games_with_saves)
                gk_save_pct = round((total_saves / max(est_sot_faced, 1)) * 100, 1)
            elif total_saves > 0:
                gk_save_pct = round(min(85, (total_saves / max(total_saves + games_with_saves * 0.8, 1)) * 100), 1)
            else:
                gk_save_pct = 70.0  # League average fallback

            # 3. Match context multiplier
            # Base = 1.0, adjust: underdog at home +0.10, favorite at home -0.10, etc.
            context_multiplier = 1.0
            context_factors = []
            if match_odds and match_odds.get("favorite"):
                fav = match_odds["favorite"]
                if fav == player_venue:
                    # GK's team is favored → fewer shots faced → fewer saves
                    context_multiplier -= 0.10
                    context_factors.append(f"Team favored ({fav}) → -10% (fewer opponent shots)")
                else:
                    # GK's team is underdog → more shots faced → more saves
                    context_multiplier += 0.10
                    context_factors.append("Team underdog → +10% (more opponent shots)")
            if player_venue == "away":
                context_multiplier += 0.05
                context_factors.append("Away GK → +5% (typically face more pressure)")
            context_multiplier = round(context_multiplier, 2)

            # 4. THE FORMULA: Projected Saves = Opp Avg SoT × GK Save% × Context Multiplier
            projected_saves = round(opp_avg_sot * (gk_save_pct / 100) * context_multiplier, 1) if opp_avg_sot > 0 else gk_avg_saves

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
                "formula": f"{opp_avg_sot} SoT × {gk_save_pct}% save rate (last {games_with_saves} games) × {context_multiplier} context = {projected_saves}",
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

4. FORMULA RESULT: {opp_avg_sot} × {gk_save_pct}% × {context_multiplier} = {projected_saves} projected saves

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

        # Compose data for Gemini
        final_data_parts = []
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
        # SELF-LEARNING CALIBRATION: Fetch historical miss patterns
        # =============================================
        calibration = await get_calibration_adjustment("soccer", req.propType, player_venue)
        if calibration["applied"]:
            final_data += f"\n\n{calibration['context']}"
            print(f"[CALIBRATE] Soccer {req.propType}: {calibration['adjustment']:+.1f}% adjustment ({calibration['missCount']} misses)")

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

        prompt = f"""{req.playerName} ({display_position}) — plays for {req.teamName} ({player_venue.upper()}) | OPPONENT: {req.opponentName} | {req.propType} line {req.line}
Odds: {json.dumps(match_odds.get('bookmakerOdds',{}), default=str) if match_odds else 'N/A'}{match_context}
{pronoun_note}
recentSamples=[]

{final_data[:6000]}

Analyze ALL data thoroughly. Return JSON only."""

        # Run 3 AIs in TRULY PARALLEL (using litellm.acompletion, not blocking LlmChat)
        # LlmChat uses litellm.completion (sync) which blocks the event loop.
        # litellm.acompletion is truly async — all 3 AIs execute concurrently.
        import litellm
        litellm.drop_params = True
        EMERGENT_PROXY = "https://integrations.emergentagent.com/llm"

        async def call_ai(model_name, label, provider="openai"):
            try:
                model_id = f"gemini/{model_name}" if provider == "gemini" else model_name
                resp = await aio.wait_for(
                    litellm.acompletion(
                        model=model_id,
                        messages=[
                            {"role": "system", "content": PREDICTION_SYSTEM},
                            {"role": "user", "content": prompt},
                        ],
                        api_key=EMERGENT_LLM_KEY,
                        api_base=EMERGENT_PROXY,
                        custom_llm_provider="openai",
                        max_tokens=2500,
                        temperature=0.0,
                    ),
                    timeout=40
                )
                text = resp.choices[0].message.content.strip()
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
                raise ValueError("No valid JSON in response")
            except Exception as e:
                print(f"[MULTI-AI] {label} failed: {e}")
                return None


        async def call_emergent_direct(model_name, label):
            """Call Claude/other models directly via OpenAI SDK to bypass litellm provider detection."""
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

        ai_tasks = [
            aio.ensure_future(call_ai("gemini-2.0-flash", "gemini", "gemini")),
            aio.ensure_future(call_ai("gpt-4.1-mini", "gpt41mini")),
            aio.ensure_future(call_grok("grok", "grok-4-1-fast-non-reasoning")),
        ]

        # FORCE-3-MODELS: Wait for ALL 3 AIs, retry failures once
        ai_results = []
        deadline = _t0 + 48  # absolute cap: 48s from route start

        # First pass: wait for all 3 to complete
        done, pending = await aio.wait(ai_tasks, timeout=max(0.1, deadline - _t.time()))
        for t in done:
            try:
                r = t.result()
                if r and isinstance(r, dict) and r.get("projectedValue") is not None:
                    pv = r.get("projectedValue", 0)
                    if isinstance(pv, (int, float)) and pv > 0:
                        ai_results.append(r)
            except Exception:
                pass
        for t in pending:
            t.cancel()

        # Retry any failed models (one retry each, only if time allows)
        responded_sources = {r.get("_source") for r in ai_results}
        if len(ai_results) < 3 and _t.time() < deadline - 10:
            retry_tasks = []
            if "gemini" not in responded_sources:
                retry_tasks.append(aio.ensure_future(call_ai("gemini-2.0-flash", "gemini", "gemini")))
                print("[MULTI-AI] Retrying gemini...")
            if "gpt41mini" not in responded_sources:
                retry_tasks.append(aio.ensure_future(call_ai("gpt-4.1-mini", "gpt41mini")))
                print("[MULTI-AI] Retrying gpt41mini...")
            if "grok" not in responded_sources:
                retry_tasks.append(aio.ensure_future(call_grok("grok", "grok-4-1-fast-non-reasoning")))
                print("[MULTI-AI] Retrying grok...")

            if retry_tasks:
                done_retry, pending_retry = await aio.wait(retry_tasks, timeout=max(0.1, deadline - _t.time()))
                for t in done_retry:
                    try:
                        r = t.result()
                        if r and isinstance(r, dict) and r.get("projectedValue") is not None:
                            pv = r.get("projectedValue", 0)
                            if isinstance(pv, (int, float)) and pv > 0:
                                ai_results.append(r)
                    except Exception:
                        pass
                for t in pending_retry:
                    t.cancel()

        print(f"[TIMING] AIs done: {_t.time()-_t0:.1f}s total, {len(ai_results)}/3 succeeded ({', '.join(r.get('_source','?') for r in ai_results)})")

        # Collect valid predictions
        valid_preds = []
        for i, r in enumerate(ai_results):
            if isinstance(r, dict) and r.get("projectedValue") is not None:
                pv = r.get("projectedValue", 0)
                # Filter out obviously bad predictions (0 or negative)
                if isinstance(pv, (int, float)) and pv > 0:
                    # ENFORCE: each model's recommendation MUST match its projected value vs line
                    r["recommendation"] = "over" if pv > req.line else "under"
                    valid_preds.append(r)
                    print(f"[MULTI-AI] {r.get('_source','AI'+str(i))}: proj={pv} rec={r.get('recommendation')} conf={r.get('confidenceScore')}")
                else:
                    print(f"[MULTI-AI] {r.get('_source','AI'+str(i))}: REJECTED proj={pv}")

        if not valid_preds:
            raise ValueError("All AI models failed to produce predictions")

        # MERGE: Weighted consensus
        # Use first valid as base, merge numbers from all
        prediction = valid_preds[0].copy()

        if len(valid_preds) > 1:
            # Average projectedValue across all models
            proj_values = [p.get("projectedValue", 0) for p in valid_preds if p.get("projectedValue")]
            avg_proj = round(sum(proj_values) / len(proj_values), 1)
            prediction["projectedValue"] = avg_proj

            # ENFORCE: recommendation must match projectedValue vs line (eliminates contradiction)
            prediction["recommendation"] = "over" if avg_proj > req.line else "under"

            # Average confidence (normalize 0-1 to 0-100)
            conf_values = []
            for p in valid_preds:
                c = p.get("confidenceScore", 50)
                if isinstance(c, (int, float)):
                    conf_values.append(c * 100 if c <= 1 else c)
            prediction["confidenceScore"] = round(sum(conf_values) / len(conf_values)) if conf_values else 50

            # TEXT FIELDS: Prioritize Grok, fall back to longest
            grok_pred = next((p for p in valid_preds if p.get("_source") == "grok"), None)

            for field in ["tacticalBreakdown", "reasoning", "sharpSummary", "scenarioAnalysis", "keyEvidence"]:
                # Try Grok first
                if grok_pred and len(str(grok_pred.get(field, ""))) > 50:
                    prediction[field] = grok_pred[field]
                else:
                    # Fall back to longest text from any AI
                    best = max(valid_preds, key=lambda p: len(str(p.get(field, ""))))
                    prediction[field] = best.get(field, "")

            # Consensus note
            recs = [p.get("recommendation", "over") for p in valid_preds]
            over_count = sum(1 for r in recs if r == "over")
            under_count = len(recs) - over_count
            if all(r == prediction["recommendation"] for r in recs):
                consensus = f"Unanimous {prediction['recommendation'].upper()} — {len(valid_preds)}/{len(valid_preds)} AI models agree."
            else:
                majority_rec = prediction["recommendation"]
                dissenters = [p for p in valid_preds if p.get("recommendation") != majority_rec]
                dissent_reasons = []
                for d in dissenters:
                    reason = d.get("sharpSummary") or d.get("reasoning") or ""
                    if reason:
                        dissent_reasons.append(reason[:200])
                dissent_text = " Dissent: " + " | ".join(dissent_reasons) if dissent_reasons else ""
                consensus = f"Split: {over_count}/{len(valid_preds)} OVER, {under_count}/{len(valid_preds)} UNDER. Consensus → {prediction['recommendation'].upper()}.{dissent_text}"
            prediction["consensusNote"] = consensus

        else:
            # Single AI result — still enforce recommendation consistency
            pv = prediction.get("projectedValue", req.line)
            prediction["recommendation"] = "over" if pv > req.line else "under"

        # Clean up internal source tags AFTER building model breakdown
        model_breakdown = []
        for p in valid_preds:
            src = p.get("_source", "AI")
            model_breakdown.append({
                "model": src,
                "recommendation": p.get("recommendation", ""),
                "projectedValue": p.get("projectedValue", 0),
                "confidenceScore": p.get("confidenceScore", 50),
            })
        prediction["modelBreakdown"] = model_breakdown

        for p in valid_preds:
            p.pop("_source", None)
        prediction.pop("_source", None)

        # Always set consensusNote even for single AI
        if not prediction.get("consensusNote"):
            prediction["consensusNote"] = f"Single AI analysis — {prediction.get('recommendation', 'over').upper()} recommendation."

        # Set confidence level
        cs = prediction.get("confidenceScore", 50)
        prediction["confidenceLevel"] = "Very High" if cs >= 75 else "High" if cs >= 65 else "Medium" if cs >= 50 else "Low"

        # =============================================
        # APPLY SELF-LEARNING CALIBRATION TO PROJECTION
        # =============================================
        if calibration["applied"]:
            old_proj = prediction.get("projectedValue", req.line)
            adj_factor = calibration["adjustment"] / 100.0
            new_proj = round(old_proj * (1 + adj_factor), 1)
            prediction["projectedValue"] = new_proj
            prediction["recommendation"] = "over" if new_proj > req.line else "under"
            prediction["calibration"] = {
                "applied": True,
                "adjustment": calibration["adjustment"],
                "oldProjection": old_proj,
                "newProjection": new_proj,
                "missCount": calibration["missCount"],
                "biasDirection": calibration["biasDirection"],
            }
            print(f"[CALIBRATE] Adjusted projection: {old_proj} → {new_proj} ({calibration['adjustment']:+.1f}%)")
        else:
            prediction["calibration"] = {"applied": False}

        response_text = json.dumps(prediction)

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
        # 1. Possession from real fixture stats (venue-filtered averages)
        if team_fixture_stats or opponent_fixture_stats:
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
                # Normalize so they add to 100
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
            if match_odds.get("bookmakerOdds"):
                bo = match_odds["bookmakerOdds"]
                real_matchup["moneyline"] = {
                    "home": bo.get("homeWin", "N/A"),
                    "draw": bo.get("draw", "N/A"),
                    "away": bo.get("awayWin", "N/A")
                }
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

        # DATA QUALITY INDICATOR — flag when API data might be unreliable
        total_game_logs = len(player_game_logs)
        gl_target_field_map_check = {
            "pass_attempts": "passes_total", "shots": "shots_total", "shots_on_target": "shots_on",
            "tackles": "tackles_total", "key_passes": "passes_key", "saves": "goals_saves",
            "interceptions": "tackles_interceptions", "blocks": "tackles_blocks",
            "dribbles": "dribbles_attempts", "fouls_drawn": "fouls_drawn",
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

        # SYNTHESIS STEP: Combine all AI analyses into one rich tactical breakdown
        # This recreates the original Grok+Gemini depth — one AI synthesizes all others' insights
        rec = prediction.get('recommendation', 'over').upper()
        line = prediction.get('line', req.line)
        proj = prediction.get('projectedValue', '?')
        conf = prediction.get('confidenceScore', '?')
        prop_map = {"pass_attempts":"Pass Attempts","shots":"Shots","shots_on_target":"Shots on Target","tackles":"Tackles","key_passes":"Key Passes","saves":"Saves","interceptions":"Interceptions","blocks":"Blocks","dribbles":"Dribbles","fouls_drawn":"Fouls Drawn"}
        pl = prop_map.get(req.propType, req.propType)
        consensus_note = prediction.get('consensusNote', '')

        # Gather ALL text from every AI that responded (not just 3 — use everything available)
        all_texts = []
        for p in valid_preds:
            src = p.get("_source", "AI")
            bits = []
            for field in ["tacticalBreakdown", "reasoning", "scenarioAnalysis", "keyEvidence", "sharpSummary", "gameFlowDynamics", "sensitivityTests", "subRisk", "uncertaintyNote"]:
                val = p.get(field, "")
                if isinstance(val, dict):
                    val = json.dumps(val)
                if val and len(str(val)) > 10:
                    bits.append(f"{field}: {val}")
            if bits:
                all_texts.append(f"[{src}]\n" + "\n".join(bits))

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

            synth_resp = await aio.wait_for(
                litellm.acompletion(
                    model="gemini/gemini-2.0-flash",
                    messages=[{"role": "user", "content": synth_prompt}],
                    api_key=EMERGENT_LLM_KEY,
                    api_base=EMERGENT_PROXY,
                    custom_llm_provider="openai",
                    max_tokens=1500,
                    temperature=0.2,
                ),
                timeout=10
            )
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

