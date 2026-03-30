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
from utils import api_football_request, get_recent_fixtures_fast

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
        fixtures_task = get_recent_fixtures_fast(actual_team_id or 40, 30)
        odds_task = get_match_odds()

        player_stats, team_stats, opponent_stats, h2h_data, standings_raw, recent_fixtures, match_odds = await aio.gather(
            player_data_task, team_stats_task, opponent_stats_task, h2h_task, standings_task, fixtures_task, odds_task
        )

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
            """Fetch per-match team stats (possession, shots, passes) from fixtures/statistics"""
            results = []
            for fix in fixture_list[:limit]:
                fid = fix.get("fixtureId")
                if not fid:
                    continue
                try:
                    data = await api_football_request("fixtures/statistics", {"fixture": fid})
                    if not data:
                        continue
                    # Find the stats for our team
                    for team_data in data:
                        if team_data.get("team", {}).get("id") == team_id:
                            raw_stats = {}
                            for s in team_data.get("statistics", []):
                                raw_stats[s.get("type", "")] = s.get("value")
                            results.append({
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
                            })
                            break
                except Exception:
                    continue
            return results

        # 2. Player game-by-game box scores from recent fixtures
        async def fetch_player_game_logs(fixture_list, player_id, limit=8):
            """Fetch player's individual stats from each recent fixture. Falls back to name match for duplicate IDs."""
            results = []
            player_name_lower = req.playerName.lower().split()[-1] if req.playerName else ""
            for fix in fixture_list[:limit]:
                fid = fix.get("fixtureId")
                if not fid:
                    continue
                try:
                    data = await api_football_request("fixtures/players", {"fixture": fid})
                    if not data:
                        continue
                    # Find our player — try ID first, then name fallback for duplicate ID cases
                    matched_stats = None
                    for team_data in data:
                        for p in team_data.get("players", []):
                            pid = p.get("player", {}).get("id")
                            pname = (p.get("player", {}).get("name") or "").lower()
                            if pid == player_id or (player_name_lower and player_name_lower in pname):
                                stats = p.get("statistics", [{}])[0] if p.get("statistics") else {}
                                minutes = stats.get("games", {}).get("minutes") or 0
                                if minutes > 0:
                                    matched_stats = stats
                                    break
                        if matched_stats:
                            break
                    if not matched_stats:
                        continue
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
                    raw_val = game_log.get(stat_field_map.get(req.propType, ""), None)
                    if raw_val is not None and minutes > 0:
                        game_log["targetStatPer90"] = round((raw_val / minutes) * 90, 2)
                    results.append(game_log)
                except Exception:
                    continue
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
        # Player game logs: use ALL recent fixtures for maximum sample size
        # AI will weight venue-matching games higher in analysis
        player_game_logs_task = fetch_player_game_logs(all_team_fixtures[:20], req.playerId, 15)

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

        # =============================================
        # DUAL AI: Grok runs tactical analysis with LIVE WEB SEARCH
        # =============================================
        async def grok_tactical_analysis():
            """Grok-4 with web search for real-time injury/lineup intel + stat verification + tactical analysis"""
            try:
                grok_client = OpenAI(api_key=XAI_API_KEY, base_url="https://api.x.ai/v1")
                player_team = player_stats.get("statistics", [{}])[0].get("team", {}).get("name", "") if player_stats else ""

                tactical_brief = {
                    "opponent_stats": opponent_stats,
                    "team_stats": team_stats,
                    "h2h": h2h_data[:3] if h2h_data else [],
                    "standings": standings[:6] if standings else [],
                    "odds": match_odds,
                }
                tactical_json = json.dumps(tactical_brief, default=str)[:5000]

                odds_context = ""
                if match_odds and match_odds.get("bookmakerOdds"):
                    bo = match_odds["bookmakerOdds"]
                    fav = match_odds.get("favorite", "unknown")
                    odds_context = f"ODDS: Home={bo.get('homeWin','')} Draw={bo.get('draw','')} Away={bo.get('awayWin','')} FAV={fav.upper()}"

                loop = aio.get_event_loop()
                def _call_grok():
                    return grok_client.responses.create(
                        model="grok-4.20-reasoning",
                        tools=[{"type": "web_search"}],
                        input=[
                            {"role": "system", "content": "Elite soccer analyst with deep web research. You MUST use web search to find ACTUAL per-game player statistics. Search SofaScore, FotMob, and WhoScored specifically. Do NOT guess or estimate — search and report exact numbers you find. Be concise."},
                            {"role": "user", "content": f"""Analyze {req.propType} prop on {req.playerName} ({player_position or 'Unknown'}) — {player_team} vs {req.opponentName} ({player_venue.upper()}). Line: {req.line}. {odds_context}
{pronoun_note}

TASK 1 — FIND REAL STATS (MOST IMPORTANT):
Search specifically for:
- "site:sofascore.com {req.playerName}" to find the player's SofaScore profile
- "{req.playerName} {player_team} player statistics {req.propType}"
- "{req.playerName} recent matches stats"
Go to SofaScore.com or FotMob and find this player's ACTUAL per-game {req.propType} from their last 5 matches.
Report per game: date, opponent, exact {req.propType} count, minutes played.
Example format: "Mar 14 vs Kansas City: 2 shots (90 min) — source: SofaScore"
DO NOT use any numbers I gave you in the DATA section below — those may be wrong. Only report what you find from your web search.

TASK 2 — LIVE NEWS:
Search for injuries, confirmed lineups, key absences for {player_team} and {req.opponentName}.

TASK 3 — TACTICAL ANALYSIS:
- Matchup: favorite (from odds), expected possession %, game type (open/cagey/one-sided)
- Position baseline: {player_position} expected range for {req.propType}
- If saves prop: Opponent SOT avg → saves ceiling. Favored GK = fewer saves.
- Scenarios: Base/Blowout/Trailing/Cagey — probability % and expected {req.propType} value
- Sensitivity: Sub risk, red card impact — ROBUST/MODERATE/FRAGILE
- Verdict: Over or under {req.line}? Confidence 0-100?

Format your response:
[VERIFIED STATS] — exact per-game {req.propType} numbers from web sources (cite the source)
[LIVE NEWS] — injuries, lineups
[ANALYSIS] — scenarios, verdict

DATA: {tactical_json}"""}
                        ],
                        max_output_tokens=2000,
                    )
                result = await loop.run_in_executor(None, _call_grok)
                return result.output_text if result else None
            except Exception:
                return None

        grok_tactical_task = grok_tactical_analysis()

        try:
            team_fixture_stats, opponent_fixture_stats, player_game_logs, grok_analysis = await aio.wait_for(
                aio.gather(team_fixture_stats_task, opponent_fixture_stats_task, player_game_logs_task, grok_tactical_task),
                timeout=90
            )
        except aio.TimeoutError:
            team_fixture_stats, opponent_fixture_stats, player_game_logs, grok_analysis = [], [], [], None

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
            for h in h2h_data[:10]:
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
                h2h_results = await aio.gather(*[fetch_h2h_player_stat(fid, fi) for fid, fi in h2h_fixture_ids])
                h2h_player_stats = [r for r in h2h_results if r]

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

        # 2. Send to Gemini — FINAL PREDICTOR (receives Grok analysis + structured data)
        chat = LlmChat(
            api_key=EMERGENT_LLM_KEY,
            session_id=f"predict-{uuid.uuid4().hex[:8]}",
            system_message="""You are the final-stage predictor in a DUAL AI pipeline. You receive:
1. A STRUCTURED DATA DIGEST (real API stats, compiled by code — 100% accurate numbers)
2. A TACTICAL ANALYSIS from Grok (matchup analysis, live web search intel, scenarios, sensitivity tests, sub risk)
3. DEEP MATCH DATA (per-fixture stats and player game logs from the real API)

Your job: SYNTHESIZE all inputs into a single, calibrated prediction JSON. Do NOT re-derive what Grok already analyzed — USE their analysis directly. Focus on:

STEP 1: VALIDATE the player's role and POSITION from the data digest. Apply position baselines:
  - GK saves: Capped by opponent shots on target. If opponent avg 10 shots/game with 35% on target = 3.5 SOT. GK saves ~70% = ~2.5 saves. NEVER project above opponent SOT avg.
  - GK saves INVERSE: Favored team's GK = FEWER saves. Underdog GK = MORE saves.
  - Defender: tackles 2-4, interceptions 1-3, blocks 0-2, shots 0-1
  - Midfielder: shots 1-2 (AM 2-3), passes 30-50, key passes 1-3
  - Forward: shots 2-4 (elite 3-5), key passes 0-2

STEP 2: CROSS-REFERENCE Grok's scenario analysis with the game log data — does the evidence support Grok's assessment?
  - For SAVES: Check if Grok's saves projection exceeds opponent's shots-on-target average. If yes, CAP IT.
  - For SHOTS: Check if projection exceeds what's normal for this position. Midfielders rarely exceed 3 shots/game.

STEP 3: CALIBRATE the final projected value using:
- Grok's VERIFIED STATS section as ground truth (web-searched actual game numbers)
- Grok's weighted scenario projection as the starting point
- Game log data as secondary reference (API data may be incomplete — Grok's web-verified stats take priority)
- Data digest numbers for team/opponent context
- If Grok's verified stats and API game logs disagree, USE GROK'S NUMBERS and note the discrepancy
STEP 4: SET CONFIDENCE based on:
- Data quality (sample size, recency)
- Grok's sensitivity test results (ROBUST = 75+, MODERATE = 55-74, FRAGILE = 40-54)
- How close the projection is to the line (within 0.5 = coin flip = max 55 confidence)

DRIBBLE-SPECIFIC: Most volatile stat. AWAY + low-block = default UNDER when line is close to average.

Return ONLY valid JSON (no markdown).

JSON structure:
{"player":{"id":int,"name":"","team":"","role":"","position":""},"opponent":"","league":"","propType":"","line":0,"projectedValue":0,"recommendation":"over|under","confidenceScore":0-100,"confidenceLevel":"Low|Medium|High|Very High","confidenceInterval":[lo,hi],"matchupOverview":{"homeTeam":"","awayTeam":"","favorite":"home|away|even","moneyline":{"home":"","draw":"","away":""},"expectedPossession":{"home":0,"away":0},"expectedGameType":"open|cagey|one-sided|high-tempo","keyMatchupFactor":""},"recentSamples":[],"bayesianMetrics":{"priorMean":0,"momentumEffect":0,"covariateAdjustment":0,"reversalFlag":"stable|upward_reversal_likely|downward_reversal_likely"},"probabilityCurve":[{"value":0,"probability":0}],"tacticalAlerts":[{"type":"injury|lineup|tactical|sub_risk","message":"","severity":"low|medium|high"}],"sharpSummary":"","reasoning":"","scenarioAnalysis":"","keyEvidence":"","uncertaintyNote":"","sensitivityTests":"","subRisk":"","gameFlowDynamics":""}

Field requirements:
- matchupOverview: Home/away teams, moneyline odds, expected possession split, expected game type, key matchup factor. Use Grok's web intel + odds data.
- sharpSummary: 2-3 sentences. Core edge. Be direct.
- reasoning: 2-3 paragraphs synthesizing Grok's analysis + data digest numbers. Quote specific stats.
- scenarioAnalysis: Take Grok's scenario breakdown directly — refine if game log data contradicts it.
- keyEvidence: Quote 5-8 specific values from the data. Format: "Last 5: X (vs Team, venue), Y (vs Team, venue)..."
- sensitivityTests: Take Grok's sensitivity results directly.
- subRisk: Take Grok's sub risk quantification directly.
- gameFlowDynamics: Take Grok's game flow analysis directly.
- uncertaintyNote: Key risk factor + data limitations.
- recentSamples: MUST BE EMPTY ARRAY []. DO NOT generate any. Backend injects real data.
- probabilityCurve: 10 data points centered on projection."""
        )
        chat.with_model("gemini", "gemini-2.5-flash")

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
                    context_factors.append(f"Team underdog → +10% (more opponent shots)")
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

        # Compose final data: Structured data digest (code-built) + Grok tactical analysis + Wave 2 deep data
        final_data_parts = []
        if data_digest:
            final_data_parts.append(f"[DATA DIGEST — REAL API STATS]\n{data_digest}")
        if grok_analysis:
            final_data_parts.append(f"[GROK TACTICAL ANALYSIS — LIVE WEB SEARCH]\n{grok_analysis}")
        if wave2_supplement:
            final_data_parts.append(f"[DEEP MATCH DATA]\n{json.dumps(wave2_supplement, default=str)[:5000]}")

        if final_data_parts:
            final_data = "\n\n".join(final_data_parts)
            if saves_context:
                final_data += f"\n\n{saves_context}"
            if position_context:
                final_data += f"\n{position_context}"
        else:
            # Fallback: raw data if both AIs failed
            final_data = json.dumps(historical_data, default=str)[:18000]

        prompt = f"""DUAL AI PREDICTION — FINAL STAGE
{pronoun_note}

Player: {req.playerName} (ID: {req.playerId}) | Position: {player_position or 'Unknown'} | Opponent: {req.opponentName} | Venue: {req.venue.upper()} | Prop: {req.propType} | Line: {req.line}

CRITICAL VENUE CONTEXT: Player is {player_venue.upper()}. ALL data below is venue-filtered:
- Team stats = their {player_venue.upper()} performance
- Opponent stats = their {opponent_venue.upper()} performance
- Player game logs = prioritized {player_venue.upper()} games
- The {player_venue.upper()} average is MORE predictive than the overall average. Weight it 60-70%.

Stat mapping: pass_attempts=passes.total, shots=shots.total, shots_on_target=shots.on, tackles=tackles.total, key_passes=passes.key, saves=goals.saves, interceptions=tackles.interceptions, blocks=tackles.blocks, dribbles=dribbles.attempts, fouls_drawn=fouls.drawn

You have received pre-analyzed intel from Grok (live web search + tactical analysis):
1. DATA DIGEST — 100% real API numbers compiled by code (no AI distortion)
2. Grok's TACTICAL ANALYSIS — live injury/lineup news, scenario analysis, sensitivity tests, sub risk

YOUR JOB: Synthesize both into the final calibrated prediction JSON.
- Use Grok's scenario weights as your starting framework
- Cross-check against the data digest's real numbers
- If they disagree, explain why in your reasoning and go with the more evidence-based number
- DO NOT generate recentSamples — return empty array []. Backend injects real API data.
- Take Grok's sensitivityTests, subRisk, and gameFlowDynamics assessments — refine only if game log data contradicts them

TACTICAL INTEL:
- See Grok's tactical analysis above for live web intel (injuries, lineups, news) and matchup context
- Bookmaker odds: {json.dumps(match_odds, default=str) if match_odds else 'Not available — use Grok analysis and standings'}

CRITICAL ODDS RULE: If bookmaker odds present, lower odds = favored team. Trust bookmaker odds over all other signals.

ALL DATA:
{final_data}

Return ONLY valid JSON. recentSamples MUST be []. 10pt probabilityCurve."""

        response = await chat.send_message(UserMessage(text=prompt))
        response_text = response.strip()

        # Clean up response - remove markdown code fences if present
        if response_text.startswith("```"):
            lines = response_text.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            response_text = "\n".join(lines)

        prediction = json.loads(response_text)

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

    except json.JSONDecodeError as e:
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

