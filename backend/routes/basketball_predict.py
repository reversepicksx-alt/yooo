"""
Basketball (NBA) Prediction Engine
Mirrors the soccer predict.py architecture:
- Parallel data fetching (team games → player game logs)
- 5-AI consensus engine with first-3-wins pattern
- Gemini synthesis step for tactical breakdown
- Strict <55s execution budget
"""
import json
import uuid
import asyncio as aio
import statistics as stats_mod
import traceback
import time as _t
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException
from openai import OpenAI

from config import db, EMERGENT_LLM_KEY, XAI_API_KEY
from models import BasketballPredictionRequest
from basketball_utils import (
    get_team_games, get_player_game_stats, get_h2h,
    get_team_stats, get_standings, search_teams,
    parse_game_for_team, parse_player_game_stat,
    NBA_LEAGUE_ID, BBALL_CURRENT_SEASON,
)

router = APIRouter(prefix="/api", tags=["basketball"])

# Basketball prop → stat field mapping
BBALL_STAT_FIELD_MAP = {
    "points": "points",
    "rebounds": "rebounds",
    "assists": "assists",
    "pts_reb_ast": None,  # computed composite
    "three_pointers": "tpm",
    "steals": "steals",
    "blocks": "blocks",
    "turnovers": "turnovers",
    "fgm": "fgm",
    "ftm": "ftm",
    "fga": "fga",
    "fta": "fta",
    "tpa": "tpa",
}

BBALL_PROP_LABELS = {
    "points": "Points",
    "rebounds": "Rebounds",
    "assists": "Assists",
    "pts_reb_ast": "Pts+Reb+Ast",
    "three_pointers": "3-Point FG Made",
    "steals": "Steals",
    "blocks": "Blocks",
    "turnovers": "Turnovers",
    "fgm": "FG Made",
    "ftm": "FT Made",
    "fga": "FG Attempted",
    "fta": "FT Attempted",
    "tpa": "3PT Attempted",
}


def get_stat_value(parsed: dict, prop_type: str):
    """Extract the relevant stat value from a parsed player game stat."""
    if prop_type == "pts_reb_ast":
        p = parsed.get("points", 0) or 0
        r = parsed.get("rebounds", 0) or 0
        a = parsed.get("assists", 0) or 0
        return p + r + a
    field = BBALL_STAT_FIELD_MAP.get(prop_type, prop_type)
    if field:
        return parsed.get(field, 0) or 0
    return 0


async def fetch_player_game_logs(team_id: int, player_name: str, prop_type: str, limit: int = 20):
    """
    Fetch player's individual game logs by:
    1. Getting team's recent finished games
    2. For each game, fetching player stats via games/statistics/players
    """
    games = await get_team_games(team_id)
    if not games:
        return [], []

    player_name_lower = player_name.lower().strip()
    last_name = player_name_lower.split()[-1] if player_name_lower else ""

    parsed_games = []
    for g in games[:limit]:
        parsed_games.append(parse_game_for_team(g, team_id))

    # Fetch player stats for each game in parallel
    async def fetch_one(game_info, raw_game):
        game_id = game_info.get("gameId")
        if not game_id:
            return None
        try:
            stats = await get_player_game_stats(game_id)
            if not stats:
                return None
            # Find our player in the stats
            for entry in stats:
                parsed = parse_player_game_stat(entry)
                pname = parsed.get("playerName", "").lower()
                if (last_name and last_name in pname) or player_name_lower in pname or pname in player_name_lower:
                    # Check this player is on the right team
                    if parsed.get("teamId") == team_id or not parsed.get("teamId"):
                        stat_val = get_stat_value(parsed, prop_type)
                        return {
                            **game_info,
                            "playerStats": parsed,
                            "targetStat": stat_val,
                            "minutes": parsed.get("minutes", "0"),
                        }
            return None
        except Exception:
            return None

    tasks = [fetch_one(pg, g) for pg, g in zip(parsed_games, games[:limit])]
    results = await aio.gather(*tasks, return_exceptions=True)
    logs = [r for r in results if r and not isinstance(r, Exception)]
    return logs, parsed_games


@router.post("/basketball/predict")
async def basketball_predict(req: BasketballPredictionRequest):
    try:
        t0 = _t.time()

        # ═══════════════════════════════════════
        # WAVE 1: Parallel data fetching
        # ═══════════════════════════════════════
        async def safe_fetch(coro):
            try:
                return await coro
            except Exception:
                return None

        player_logs_task = fetch_player_game_logs(req.teamId, req.playerName, req.propType, 20)
        h2h_task = safe_fetch(get_h2h(req.teamId, req.opponentId))
        team_stats_task = safe_fetch(get_team_stats(req.teamId))
        opp_stats_task = safe_fetch(get_team_stats(req.opponentId))
        standings_task = safe_fetch(get_standings())
        opp_games_task = safe_fetch(get_team_games(req.opponentId))

        (player_logs_result, team_games_parsed), h2h_data, team_stats, opp_stats, standings_raw, opp_games_raw = await aio.gather(
            player_logs_task, h2h_task, team_stats_task, opp_stats_task, standings_task, opp_games_task
        )

        player_game_logs = player_logs_result or []
        h2h_data = h2h_data or []
        team_games_parsed = team_games_parsed or []
        opp_games_raw = opp_games_raw or []

        print(f"[BBALL TIMING] Wave 1: {_t.time()-t0:.1f}s, {len(player_game_logs)} player logs")

        # ═══════════════════════════════════════
        # VENUE FILTERING
        # ═══════════════════════════════════════
        player_venue = req.venue.lower()
        opponent_venue = "away" if player_venue == "home" else "home"

        venue_logs = [g for g in player_game_logs if g.get("venue") == player_venue]
        all_logs = player_game_logs

        # Extract target stat values
        stat_values = [g.get("targetStat", 0) for g in player_game_logs if g.get("targetStat") is not None]
        venue_values = [g.get("targetStat", 0) for g in venue_logs if g.get("targetStat") is not None]

        # ═══════════════════════════════════════
        # BUILD DATA DIGEST
        # ═══════════════════════════════════════
        prop_label = BBALL_PROP_LABELS.get(req.propType, req.propType)
        parts = []

        # Player game log summary
        if stat_values:
            avg_val = round(sum(stat_values) / len(stat_values), 1)
            min_val = min(stat_values)
            max_val = max(stat_values)
            std_dev = round(stats_mod.stdev(stat_values), 2) if len(stat_values) >= 3 else 0

            home_vals = [g.get("targetStat", 0) for g in player_game_logs if g.get("venue") == "home" and g.get("targetStat") is not None]
            away_vals = [g.get("targetStat", 0) for g in player_game_logs if g.get("venue") == "away" and g.get("targetStat") is not None]

            parts.append(f"""[PLAYER {prop_label.upper()} GAME LOGS — Last {len(stat_values)} games]
- Average: {avg_val} | Min: {min_val} | Max: {max_val} | StdDev: {std_dev}
- Home avg: {round(sum(home_vals)/len(home_vals),1) if home_vals else 'N/A'} ({len(home_vals)} games)
- Away avg: {round(sum(away_vals)/len(away_vals),1) if away_vals else 'N/A'} ({len(away_vals)} games)
- Venue-filtered ({player_venue}): avg {round(sum(venue_values)/len(venue_values),1) if venue_values else 'N/A'} ({len(venue_values)} games)""")

            # Individual game lines
            game_lines = []
            for g in player_game_logs[:15]:
                ps = g.get("playerStats", {})
                game_lines.append(
                    f"  {g.get('date','')} vs {g.get('opponent','')} ({g.get('venue','')}) {g.get('result','')}: "
                    f"{prop_label}={g.get('targetStat',0)} | "
                    f"PTS={ps.get('points',0)} REB={ps.get('rebounds',0)} AST={ps.get('assists',0)} "
                    f"3PM={ps.get('tpm',0)} STL={ps.get('steals',0)} BLK={ps.get('blocks',0)} "
                    f"MIN={ps.get('minutes','0')}"
                )
            parts.append("[GAME-BY-GAME]\n" + "\n".join(game_lines))

        # Team record
        if team_games_parsed:
            wins = sum(1 for g in team_games_parsed[:10] if g.get("result") == "W")
            losses = sum(1 for g in team_games_parsed[:10] if g.get("result") == "L")
            avg_score = round(sum(g.get("teamScore", 0) for g in team_games_parsed[:10]) / max(len(team_games_parsed[:10]), 1), 1)
            avg_opp = round(sum(g.get("oppScore", 0) for g in team_games_parsed[:10]) / max(len(team_games_parsed[:10]), 1), 1)
            parts.append(f"""[TEAM RECENT FORM — Last {min(10, len(team_games_parsed))} games]
- Record: {wins}W-{losses}L | Avg Score: {avg_score} | Avg Opp Score: {avg_opp}""")

        # Opponent recent form
        if opp_games_raw:
            opp_parsed = [parse_game_for_team(g, req.opponentId) for g in opp_games_raw[:10]]
            opp_wins = sum(1 for g in opp_parsed if g.get("result") == "W")
            opp_losses = sum(1 for g in opp_parsed if g.get("result") == "L")
            opp_avg = round(sum(g.get("teamScore", 0) for g in opp_parsed) / max(len(opp_parsed), 1), 1)
            opp_against = round(sum(g.get("oppScore", 0) for g in opp_parsed) / max(len(opp_parsed), 1), 1)
            parts.append(f"""[OPPONENT RECENT FORM — Last {len(opp_parsed)} games]
- Record: {opp_wins}W-{opp_losses}L | Avg Score: {opp_avg} | Avg Against: {opp_against}""")

        # H2H
        if h2h_data:
            h2h_lines = []
            for h in h2h_data[:5]:
                hp = parse_game_for_team(h, req.teamId)
                h2h_lines.append(f"  {hp.get('date','')} {hp.get('result','')} {hp.get('teamScore',0)}-{hp.get('oppScore',0)} vs {hp.get('opponent','')}")
            parts.append(f"[H2H — Last {len(h2h_data)} meetings]\n" + "\n".join(h2h_lines))

        # H2H player stats (fetch player stats from H2H games)
        h2h_player_stats = []
        if h2h_data:
            async def fetch_h2h_player(game):
                gid = game.get("id")
                if not gid:
                    return None
                try:
                    stats = await get_player_game_stats(gid)
                    if not stats:
                        return None
                    player_name_lower = req.playerName.lower().strip()
                    last_name = player_name_lower.split()[-1]
                    for entry in stats:
                        parsed = parse_player_game_stat(entry)
                        pname = parsed.get("playerName", "").lower()
                        if last_name in pname or player_name_lower in pname:
                            val = get_stat_value(parsed, req.propType)
                            return {
                                "date": game.get("date", "")[:10],
                                "targetStat": val,
                                "stats": parsed,
                            }
                    return None
                except Exception:
                    return None

            try:
                h2h_results = await aio.wait_for(
                    aio.gather(*[fetch_h2h_player(g) for g in h2h_data[:5]]),
                    timeout=8
                )
                h2h_player_stats = [r for r in h2h_results if r]
            except aio.TimeoutError:
                pass

        if h2h_player_stats:
            h2h_vals = [s["targetStat"] for s in h2h_player_stats if s.get("targetStat") is not None]
            if h2h_vals:
                parts.append(f"""[H2H PLAYER {prop_label.upper()} vs THIS OPPONENT]
- Avg: {round(sum(h2h_vals)/len(h2h_vals),1)} | Games: {len(h2h_vals)} | Values: {h2h_vals}""")

        data_digest = "\n\n".join(parts)
        print(f"[BBALL TIMING] Data digest built: {_t.time()-t0:.1f}s")

        # ═══════════════════════════════════════
        # BUILD REAL RECENT SAMPLES
        # ═══════════════════════════════════════
        real_recent_samples = []
        for g in player_game_logs:
            val = g.get("targetStat")
            if val is not None:
                real_recent_samples.append({
                    "date": g.get("date", ""),
                    "opponent": g.get("opponent", ""),
                    "value": val,
                    "minutesPlayed": g.get("minutes", "0"),
                    "venue": g.get("venue", ""),
                    "result": g.get("result", ""),
                })

        # ═══════════════════════════════════════
        # MULTI-AI CONSENSUS ENGINE (5 AIs — first 3 valid responses win)
        # ═══════════════════════════════════════
        PREDICTION_SYSTEM = f"""Elite NBA player prop prediction engine. Analyze basketball data thoroughly, return calibrated JSON.

REQUIREMENTS:
- "reasoning": 3-5 sentences citing specific per-game averages, venue splits, opponent tendencies from data
- "tacticalBreakdown": ~1500 char markdown. Sections: **Verdict** (1 sentence), **Analysis** (cite real numbers, venue/sample context), **Scenarios** (best/worst/likely with stat ranges), **Risk** (injury, rotation, matchup), **TL;DR**
- "scenarioAnalysis": 2-3 sentences with specific projections per scenario (blowout, close game, etc.)
- "sharpSummary": 2 sentences explaining why projection differs from line
- "keyEvidence": 2-3 strongest data points as string
- "gameFlowDynamics": How game state/pace impacts this stat (1-2 sentences)
- "sensitivityTests", "subRisk", "uncertaintyNote": 1 sentence each

RULES: |proj-line|<0.3 → max 52% conf. recentSamples=[]. No AI model names. Consider pace of play, blowout risk (reduced minutes), back-to-back fatigue, and matchup quality.

JSON: {{"projectedValue":0,"recommendation":"over|under","confidenceScore":0,"confidenceLevel":"","sharpSummary":"","reasoning":"","scenarioAnalysis":"","keyEvidence":"","sensitivityTests":"","subRisk":"","gameFlowDynamics":"","uncertaintyNote":"","tacticalBreakdown":"","matchupOverview":{{"homeTeam":"","awayTeam":"","favorite":"","expectedGameType":"","keyMatchupFactor":""}},"bayesianMetrics":{{"priorMean":0,"momentumEffect":0,"covariateAdjustment":0,"reversalFlag":"stable"}},"probabilityCurve":[],"recentSamples":[],"player":{{"id":0,"name":"","team":"","position":""}},"opponent":"","propType":"","line":0,"confidenceInterval":[0,0],"tacticalAlerts":[]}}"""

        prompt = f"""{req.playerName} | {req.teamName} vs {req.opponentName} | {player_venue.upper()} | {prop_label} line {req.line}
Sport: NBA Basketball
recentSamples=[]

{data_digest[:6000]}

Analyze ALL data thoroughly. Return JSON only."""

        # Run 5 AIs in TRULY PARALLEL using litellm.acompletion
        import litellm
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
                        temperature=0.3,
                    ),
                    timeout=40
                )
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
                print(f"[BBALL MULTI-AI] {label} failed: {e}")
                return None

        async def call_grok(label="grok"):
            try:
                grok_client = OpenAI(api_key=XAI_API_KEY, base_url="https://api.x.ai/v1")
                grok_messages = [
                    {"role": "system", "content": PREDICTION_SYSTEM},
                    {"role": "user", "content": prompt},
                ]
                loop = aio.get_event_loop()
                def _run():
                    return grok_client.chat.completions.create(
                        model="grok-4-fast-reasoning",
                        messages=grok_messages,
                        max_tokens=2500,
                        temperature=0.3,
                    )
                grok_result = await aio.wait_for(loop.run_in_executor(None, _run), timeout=40)
                text = grok_result.choices[0].message.content.strip()
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
                raise ValueError("No valid JSON found in Grok response")
            except Exception as e:
                print(f"[BBALL MULTI-AI] {label} failed: {e}")
                return None

        ai_tasks = [
            aio.ensure_future(call_ai("gemini-2.0-flash", "gemini", "gemini")),
            aio.ensure_future(call_ai("gemini-2.5-flash", "gemini25", "gemini")),
            aio.ensure_future(call_ai("gpt-4o", "gpt4o")),
            aio.ensure_future(call_ai("claude-sonnet-4-20250514", "claude")),
            aio.ensure_future(call_grok("grok")),
        ]

        # FIRST-3-WINS: Take the first 3 valid results
        MIN_RESULTS = 3
        ai_results = []
        pending = set(ai_tasks)
        deadline = t0 + 48  # absolute cap: 48s from route start

        while pending and len(ai_results) < MIN_RESULTS and _t.time() < deadline:
            remaining_time = max(0.1, deadline - _t.time())
            done, pending = await aio.wait(pending, timeout=remaining_time, return_when=aio.FIRST_COMPLETED)
            for t in done:
                try:
                    r = t.result()
                    if r and isinstance(r, dict) and r.get("projectedValue") is not None:
                        pv = r.get("projectedValue", 0)
                        if isinstance(pv, (int, float)) and pv >= 0:
                            ai_results.append(r)
                except Exception:
                    pass

        # Cancel stragglers
        for t in pending:
            t.cancel()
        print(f"[BBALL TIMING] AIs done: {_t.time()-t0:.1f}s total, {len(ai_results)} succeeded ({', '.join(r.get('_source','?') for r in ai_results)})")

        # Collect valid predictions
        valid_preds = []
        for i, r in enumerate(ai_results):
            if isinstance(r, dict) and r.get("projectedValue") is not None:
                pv = r.get("projectedValue", 0)
                if isinstance(pv, (int, float)) and pv >= 0:
                    valid_preds.append(r)
                    print(f"[BBALL MULTI-AI] {r.get('_source','AI'+str(i))}: proj={pv} rec={r.get('recommendation')} conf={r.get('confidenceScore')}")

        if not valid_preds:
            raise ValueError("All AI models failed to produce predictions")

        # MERGE: Weighted consensus
        prediction = valid_preds[0].copy()

        if len(valid_preds) > 1:
            proj_values = [p.get("projectedValue", 0) for p in valid_preds if p.get("projectedValue") is not None]
            avg_proj = round(sum(proj_values) / len(proj_values), 1)
            prediction["projectedValue"] = avg_proj
            prediction["recommendation"] = "over" if avg_proj > req.line else "under"

            conf_values = []
            for p in valid_preds:
                c = p.get("confidenceScore", 50)
                if isinstance(c, (int, float)):
                    conf_values.append(c * 100 if c <= 1 else c)
            prediction["confidenceScore"] = round(sum(conf_values) / len(conf_values)) if conf_values else 50

            # TEXT FIELDS: Prioritize Grok, fall back to longest
            grok_pred = next((p for p in valid_preds if p.get("_source") == "grok"), None)
            for field in ["tacticalBreakdown", "reasoning", "sharpSummary", "scenarioAnalysis", "keyEvidence"]:
                if grok_pred and len(str(grok_pred.get(field, ""))) > 50:
                    prediction[field] = grok_pred[field]
                else:
                    best = max(valid_preds, key=lambda p: len(str(p.get(field, ""))))
                    prediction[field] = best.get(field, "")

            # Consensus note
            recs = [p.get("recommendation", "over") for p in valid_preds]
            sources = [p.get("_source", "?") for p in valid_preds]
            over_count = sum(1 for r in recs if r == "over")
            consensus = f"{len(valid_preds)} AI models analyzed ({', '.join(sources)}). "
            if all(r == prediction["recommendation"] for r in recs):
                consensus += f"Unanimous {prediction['recommendation'].upper()}."
            else:
                consensus += f"Split: {over_count} OVER, {len(recs)-over_count} UNDER. Consensus projection {avg_proj} vs line {req.line} → {prediction['recommendation'].upper()}."
            prediction["consensusNote"] = consensus
        else:
            pv = prediction.get("projectedValue", req.line)
            prediction["recommendation"] = "over" if pv > req.line else "under"

        # Clean up source tags
        for p in valid_preds:
            p.pop("_source", None)
        prediction.pop("_source", None)

        # Set confidence level
        cs = prediction.get("confidenceScore", 50)
        prediction["confidenceLevel"] = "Very High" if cs >= 75 else "High" if cs >= 65 else "Medium" if cs >= 50 else "Low"

        # Ensure required fields
        prediction.setdefault("player", {"id": 0, "name": req.playerName, "team": req.teamName, "position": ""})
        prediction.setdefault("opponent", req.opponentName)
        prediction.setdefault("propType", req.propType)
        prediction.setdefault("line", req.line)
        prediction.setdefault("projectedValue", req.line)
        prediction.setdefault("recommendation", "over")
        prediction.setdefault("confidenceScore", 50)
        prediction.setdefault("confidenceLevel", "Medium")
        prediction.setdefault("confidenceInterval", [req.line * 0.8, req.line * 1.2])
        prediction.setdefault("recentSamples", [])
        prediction.setdefault("bayesianMetrics", {"priorMean": req.line, "momentumEffect": 0, "covariateAdjustment": 0, "reversalFlag": "stable"})
        prediction.setdefault("probabilityCurve", [])

        # Override with real game log data
        if real_recent_samples:
            prediction["recentSamples"] = real_recent_samples

        # Set matchup overview from real data
        real_matchup = prediction.get("matchupOverview", {})
        real_matchup["homeTeam"] = req.teamName if player_venue == "home" else req.opponentName
        real_matchup["awayTeam"] = req.opponentName if player_venue == "home" else req.teamName
        prediction["matchupOverview"] = real_matchup

        # Player game log summary for frontend
        if stat_values:
            prediction["playerGameLogs"] = {
                "targetProp": req.propType,
                "sampleSize": len(stat_values),
                "rawAvg": round(sum(stat_values) / len(stat_values), 1),
                "rawMin": min(stat_values),
                "rawMax": max(stat_values),
                "stdDev": round(stats_mod.stdev(stat_values), 2) if len(stat_values) >= 3 else 0,
                "homeAvg": round(sum(v for g, v in zip(player_game_logs, stat_values) if g.get("venue") == "home") / max(1, sum(1 for g in player_game_logs if g.get("venue") == "home")), 1) if stat_values else 0,
                "awayAvg": round(sum(v for g, v in zip(player_game_logs, stat_values) if g.get("venue") == "away") / max(1, sum(1 for g in player_game_logs if g.get("venue") == "away")), 1) if stat_values else 0,
            }

        # Data quality
        total_logs = len(player_game_logs)
        if total_logs >= 5:
            prediction["dataQuality"] = {"level": "good", "message": "", "gamesWithData": total_logs, "totalGames": total_logs}
        elif total_logs >= 2:
            prediction["dataQuality"] = {"level": "limited", "message": f"Only {total_logs} game logs available.", "gamesWithData": total_logs, "totalGames": total_logs}
        else:
            prediction["dataQuality"] = {"level": "low", "message": f"Only {total_logs} game logs. Limited sample size.", "gamesWithData": total_logs, "totalGames": total_logs}

        # ═══════════════════════════════════════
        # SYNTHESIS STEP
        # ═══════════════════════════════════════
        rec = prediction.get('recommendation', 'over').upper()
        line = prediction.get('line', req.line)
        proj = prediction.get('projectedValue', '?')
        conf = prediction.get('confidenceScore', '?')
        consensus_note = prediction.get('consensusNote', '')

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

        try:
            synth_prompt = f"""You are synthesizing multiple AI analyses into ONE elite tactical breakdown for an NBA {prop_label} prop prediction.

FINAL VERDICT: {rec} {line} {prop_label} (Projected: {proj}, Confidence: {conf}%, {consensus_note})
Player: {req.playerName} ({req.teamName}) vs {req.opponentName} ({player_venue.upper()})

Here are the individual AI analyses to synthesize:

{synthesis_input[:4000]}

Write a single cohesive ~1500 char markdown tactical breakdown. Format:
**Verdict: {rec} {line} {prop_label}**
[1-2 sentence sharp summary with projection vs line]

**Analysis**
[3-4 sentences combining the BEST insights. Cite specific numbers: per-game averages, venue splits, sample sizes, opponent tendencies]

**Game Script Scenarios**
[Best case / Worst case / Most likely — with stat projections for each]

**Key Evidence**
[3-4 bullet points — strongest data points]

**Risk Radar**
[Blowout risk, minutes, matchup, back-to-back, injury]

**TL;DR** — {rec} {line} at {conf}% confidence. Projected: {proj} {prop_label.lower()}. {consensus_note}

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
                print(f"[BBALL TIMING] Synthesis done: {_t.time()-t0:.1f}s total, {len(synth_text)} chars")
        except Exception as synth_err:
            print(f"[BBALL SYNTHESIS] Fallback — {synth_err}")

        # Mark as basketball
        prediction["sport"] = "basketball"

        # Save to MongoDB
        prediction["_created"] = datetime.now(timezone.utc).isoformat()
        prediction["_request"] = req.model_dump()
        await db.basketball_predictions.insert_one(prediction)
        prediction.pop("_id", None)

        print(f"[BBALL TIMING] TOTAL: {_t.time()-t0:.1f}s")
        return prediction

    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Basketball prediction failed: {str(e)}")


@router.post("/basketball/search-teams")
async def basketball_search_teams(req: dict):
    """Search for basketball teams."""
    query = req.get("query", "")
    if not query:
        raise HTTPException(status_code=400, detail="Query required")
    teams = await search_teams(query)
    return {"teams": [{"id": t.get("id"), "name": t.get("name"), "logo": t.get("logo", "")} for t in teams]}
