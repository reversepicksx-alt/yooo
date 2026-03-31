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
from utils import api_football_request, get_recent_fixtures_fast, strip_accents

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
                # First try: h2h next fixture between the two specific teams
                for s in [CURRENT_SEASON + 1, CURRENT_SEASON]:
                    try:
                        h2h_fixtures = await api_football_request("fixtures/headtohead", {
                            "h2h": f"{actual_team_id or 40}-{req.opponentId}",
                            "next": 5,
                            "season": s
                        })
                        if h2h_fixtures:
                            fixtures = h2h_fixtures
                            break
                    except Exception:
                        continue

                # Fallback: get team's next match if h2h didn't find upcoming fixture
                if not fixtures:
                    for s in [CURRENT_SEASON + 1, CURRENT_SEASON]:
                        try:
                            next_fixtures = await api_football_request("fixtures", {"team": actual_team_id or 40, "next": 5, "season": s})
                            if next_fixtures:
                                # Try to find the specific opponent match
                                for nf in next_fixtures:
                                    home_id = nf.get("teams", {}).get("home", {}).get("id")
                                    away_id = nf.get("teams", {}).get("away", {}).get("id")
                                    if req.opponentId in (home_id, away_id):
                                        fixtures = [nf]
                                        break
                                if not fixtures:
                                    fixtures = next_fixtures[:1]
                                break
                        except Exception:
                            continue
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
                                    try:
                                        home_odd = float(vals.get("Home", 99))
                                        away_odd = float(vals.get("Away", 99))
                                        result["favorite"] = "home" if home_odd < away_odd else "away"
                                    except Exception:
                                        pass
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
                        "pass_attempts": "passes_total", "shots": "shots_total",
                        "shots_on_target": "shots_on", "tackles": "tackles_total",
                        "key_passes": "passes_key", "saves": "goals_saves",
                        "interceptions": "tackles_interceptions", "blocks": "tackles_blocks",
                        "dribbles": "dribbles_attempts", "fouls_drawn": "fouls_drawn",
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

            # 6. Odds
            if match_odds and match_odds.get("bookmakerOdds"):
                bo = match_odds["bookmakerOdds"]
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
        # MULTI-AI CONSENSUS ENGINE (5 AIs — first 3 valid responses win)
        # Grok + Gemini Flash + Gemini 2.5 + GPT-4o + Claude
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

        # POSITION CONTEXT: Compute position-specific baseline from game logs
        position_context = ""
        if player_position and player_game_logs:
            pos_map = {"Goalkeeper": "GK", "Defender": "DEF", "Midfielder": "MID", "Attacker": "FWD"}
            pos_short = pos_map.get(player_position, player_position)
            position_context = f"\n[POSITION BASELINE] Player position: {player_position} ({pos_short}). Calibrate expectations for this position — {pos_short}s have different stat ceilings than other positions."

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

        prompt = f"""{req.playerName} ({player_position}) | {req.opponentName} | {player_venue.upper()} | {req.propType} line {req.line}
Odds: {json.dumps(match_odds.get('bookmakerOdds',{}), default=str) if match_odds else 'N/A'}{match_context}
{pronoun_note}
recentSamples=[]

{final_data[:6000]}

Analyze ALL data thoroughly. Return JSON only."""

        # Run 5 AIs in TRULY PARALLEL (using litellm.acompletion, not blocking LlmChat)
        # LlmChat uses litellm.completion (sync) which blocks the event loop.
        # litellm.acompletion is truly async — all 5 AIs execute concurrently.
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
            aio.ensure_future(call_emergent_direct("claude-haiku-4-5", "claude")),
            aio.ensure_future(call_grok("grok", "grok-4-1-fast-non-reasoning")),
        ]

        # FIRST-3-WINS: Take the first 3 valid results, then grab any extras
        MIN_RESULTS = 3
        ai_results = []
        pending = set(ai_tasks)
        deadline = _t0 + 48  # absolute cap: 48s from route start guarantees < 55s total

        while pending and len(ai_results) < MIN_RESULTS and _t.time() < deadline:
            remaining_time = max(0.1, deadline - _t.time())
            done, pending = await aio.wait(pending, timeout=remaining_time, return_when=aio.FIRST_COMPLETED)
            for t in done:
                try:
                    r = t.result()
                    if r and isinstance(r, dict) and r.get("projectedValue") is not None:
                        pv = r.get("projectedValue", 0)
                        if isinstance(pv, (int, float)) and pv > 0:
                            ai_results.append(r)
                except Exception:
                    pass

        # Grab any additional results that finished while we were processing
        if pending:
            done_extra, still_pending = await aio.wait(pending, timeout=15.0, return_when=aio.ALL_COMPLETED)
            for t in done_extra:
                try:
                    r = t.result()
                    if r and isinstance(r, dict) and r.get("projectedValue") is not None:
                        pv = r.get("projectedValue", 0)
                        if isinstance(pv, (int, float)) and pv > 0:
                            ai_results.append(r)
                except Exception:
                    pass
            for t in still_pending:
                t.cancel()
        print(f"[TIMING] AIs done: {_t.time()-_t0:.1f}s total, {len(ai_results)} succeeded ({', '.join(r.get('_source','?') for r in ai_results)})")

        # Collect valid predictions
        valid_preds = []
        for i, r in enumerate(ai_results):
            if isinstance(r, dict) and r.get("projectedValue") is not None:
                pv = r.get("projectedValue", 0)
                # Filter out obviously bad predictions (0 or negative)
                if isinstance(pv, (int, float)) and pv > 0:
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
                consensus = f"Unanimous {prediction['recommendation'].upper()} — {len(valid_preds)}/4 AI models agree."
            else:
                majority_rec = prediction["recommendation"]
                dissenters = [p for p in valid_preds if p.get("recommendation") != majority_rec]
                dissent_reasons = []
                for d in dissenters:
                    reason = d.get("sharpSummary") or d.get("reasoning") or ""
                    if reason:
                        dissent_reasons.append(reason[:200])
                dissent_text = " Dissent: " + " | ".join(dissent_reasons) if dissent_reasons else ""
                consensus = f"Split: {over_count}/4 OVER, {under_count}/4 UNDER. Consensus → {prediction['recommendation'].upper()}.{dissent_text}"
            prediction["consensusNote"] = consensus

        else:
            # Single AI result — still enforce recommendation consistency
            pv = prediction.get("projectedValue", req.line)
            prediction["recommendation"] = "over" if pv > req.line else "under"

        # Clean up internal source tags (after synthesis used them)
        for p in valid_preds:
            p.pop("_source", None)
        prediction.pop("_source", None)

        # Set confidence level
        cs = prediction.get("confidenceScore", 50)
        prediction["confidenceLevel"] = "Very High" if cs >= 75 else "High" if cs >= 65 else "Medium" if cs >= 50 else "Low"

        response_text = json.dumps(prediction)

        # Ensure all required fields have fallback values
        prediction.setdefault("player", {"id": req.playerId, "name": req.playerName, "team": str(req.teamId), "role": "Unknown", "position": "Unknown"})
        prediction.setdefault("opponent", req.opponentName)
        prediction.setdefault("propType", req.propType)
        prediction.setdefault("line", req.line)
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
                "message": f"API data incomplete — {games_with_none} of {total_game_logs} recent games missing {req.propType} stats. Web-verified stats from Grok used for analysis.",
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
        try:
            synth_prompt = f"""You are synthesizing multiple AI analyses into ONE elite tactical breakdown for a {pl} prop prediction.

FINAL VERDICT: {rec} {line} {pl} (Projected: {proj}, Confidence: {conf}%, {consensus_note})
Player: {req.playerName} vs {req.opponentName} ({player_venue.upper()})

Here are the individual AI analyses to synthesize:

{synthesis_input[:4000]}

Write a single cohesive ~1500 char markdown tactical breakdown. Format:
**Verdict: {rec} {line} {pl}**
[1-2 sentence sharp summary with projection vs line]

**Analysis**
[3-4 sentences combining the BEST insights from ALL analyses. Cite specific numbers: per-game averages, venue splits, sample sizes, opponent tendencies. Merge complementary insights, resolve contradictions]

**Game Script Scenarios**
[Best case / Worst case / Most likely — with stat projections for each]

**Key Evidence**
[3-4 bullet points — strongest data points from across all analyses]

**Risk Radar**
[Sub risk, sensitivity factors, what would flip the pick]

**TL;DR** — {rec} {line} at {conf}% confidence. Projected: {proj} {pl.lower()}. {consensus_note}

Rules: No AI model names. Be specific with numbers. Be decisive."""

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

        await db.predictions.insert_one(prediction)
        prediction.pop("_id", None)

        return prediction

    except (json.JSONDecodeError, aio.TimeoutError):
        # Return a safe fallback prediction
        return {
            "player": {"id": req.playerId, "name": req.playerName, "team": str(req.teamId), "role": "Unknown", "position": "Unknown"},
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
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Prediction failed: {str(e)}")

