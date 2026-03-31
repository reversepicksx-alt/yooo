"""
Basketball (NBA) Prediction Engine
- Finds player ID via /players endpoint
- Fetches ALL game stats in ONE call via /games/statistics/players?player=ID&season=SEASON
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
    search_nba_teams, search_player, get_player_season_stats,
    get_team_games, get_h2h, get_team_stats, get_standings,
    parse_player_stat, parse_game_for_team,
    BBALL_CURRENT_SEASON,
)

router = APIRouter(prefix="/api", tags=["basketball"])

# Basketball prop → parsed stat field mapping
# NOTE: API only provides: points, rebounds, assists, fgm/fga, tpm/tpa, ftm/fta
BBALL_STAT_FIELD_MAP = {
    "points": "points",
    "rebounds": "rebounds",
    "assists": "assists",
    "pts_reb_ast": None,  # composite
    "three_pointers": "tpm",
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
    "fgm": "FG Made",
    "ftm": "FT Made",
    "fga": "FG Attempted",
    "fta": "FT Attempted",
    "tpa": "3PT Attempted",
}


def get_stat_value(parsed: dict, prop_type: str):
    """Extract the relevant stat value from a parsed player game stat."""
    if prop_type == "pts_reb_ast":
        return (parsed.get("points", 0) or 0) + (parsed.get("rebounds", 0) or 0) + (parsed.get("assists", 0) or 0)
    field = BBALL_STAT_FIELD_MAP.get(prop_type, prop_type)
    return parsed.get(field, 0) or 0 if field else 0


async def build_player_game_logs(player_id: int, team_id: int, prop_type: str, team_games: list):
    """
    Build player game logs by:
    1. Fetching ALL player stats for the season in a SINGLE API call
    2. Cross-referencing with team games for venue/opponent context
    """
    raw_stats = await get_player_season_stats(player_id)
    if not raw_stats:
        return []

    # Build a game_id → game_info lookup from team games
    game_lookup = {}
    for g in team_games:
        gid = g.get("id")
        if gid:
            game_lookup[gid] = parse_game_for_team(g, team_id)

    logs = []
    for entry in raw_stats:
        parsed = parse_player_stat(entry)
        game_id = parsed.get("gameId")
        stat_val = get_stat_value(parsed, prop_type)

        # Get game context from team games lookup
        game_info = game_lookup.get(game_id, {})

        logs.append({
            "gameId": game_id,
            "date": game_info.get("date", ""),
            "venue": game_info.get("venue", ""),
            "opponent": game_info.get("opponent", ""),
            "result": game_info.get("result", ""),
            "teamScore": game_info.get("teamScore", 0),
            "oppScore": game_info.get("oppScore", 0),
            "targetStat": stat_val,
            "minutes": parsed.get("minutes", "0:00"),
            "playerStats": parsed,
        })

    # Sort by date descending (most recent first), filter to games with context
    logs_with_context = [l for l in logs if l.get("date")]
    logs_without = [l for l in logs if not l.get("date")]
    logs_with_context.sort(key=lambda x: x["date"], reverse=True)

    return logs_with_context + logs_without


@router.post("/basketball/predict")
async def basketball_predict(req: BasketballPredictionRequest):
    try:
        t0 = _t.time()

        # ═══════════════════════════════════════
        # WAVE 1: Find player + parallel data fetch
        # ═══════════════════════════════════════
        async def safe_fetch(coro):
            try:
                return await coro
            except Exception:
                return None

        # Find the player ID first (critical for game log fetching)
        player_info = await search_player(req.playerName, req.teamId)
        player_id = player_info.get("id") if player_info else None
        print(f"[BBALL] Player lookup: '{req.playerName}' → ID={player_id} ({player_info.get('name') if player_info else 'NOT FOUND'})")

        # Now fetch everything in parallel
        team_games_task = get_team_games(req.teamId)
        h2h_task = safe_fetch(get_h2h(req.teamId, req.opponentId))
        team_stats_task = safe_fetch(get_team_stats(req.teamId))
        opp_stats_task = safe_fetch(get_team_stats(req.opponentId))
        standings_task = safe_fetch(get_standings())
        opp_games_task = safe_fetch(get_team_games(req.opponentId))

        team_games, h2h_data, team_stats, opp_stats, standings_raw, opp_games_raw = await aio.gather(
            team_games_task, h2h_task, team_stats_task, opp_stats_task, standings_task, opp_games_task
        )
        team_games = team_games or []
        h2h_data = h2h_data or []
        opp_games_raw = opp_games_raw or []

        # Build player game logs (uses single API call for ALL season stats)
        player_game_logs = []
        if player_id:
            player_game_logs = await build_player_game_logs(player_id, req.teamId, req.propType, team_games)

        print(f"[BBALL TIMING] Wave 1: {_t.time()-t0:.1f}s | Player logs: {len(player_game_logs)} | Team games: {len(team_games)}")

        # ═══════════════════════════════════════
        # VENUE FILTERING & STATS
        # ═══════════════════════════════════════
        player_venue = req.venue.lower()
        stat_values = [g["targetStat"] for g in player_game_logs if g.get("targetStat") is not None]
        venue_logs = [g for g in player_game_logs if g.get("venue") == player_venue]
        venue_values = [g["targetStat"] for g in venue_logs if g.get("targetStat") is not None]

        # Team games parsed
        team_games_parsed = [parse_game_for_team(g, req.teamId) for g in team_games[:20]]

        # ═══════════════════════════════════════
        # BUILD DATA DIGEST
        # ═══════════════════════════════════════
        prop_label = BBALL_PROP_LABELS.get(req.propType, req.propType)
        parts = []

        if stat_values:
            recent_vals = stat_values[:20]
            avg_val = round(sum(recent_vals) / len(recent_vals), 1)
            min_val = min(recent_vals)
            max_val = max(recent_vals)
            std_dev = round(stats_mod.stdev(recent_vals), 2) if len(recent_vals) >= 3 else 0

            home_vals = [g["targetStat"] for g in player_game_logs[:20] if g.get("venue") == "home" and g.get("targetStat") is not None]
            away_vals = [g["targetStat"] for g in player_game_logs[:20] if g.get("venue") == "away" and g.get("targetStat") is not None]

            parts.append(f"""[PLAYER {prop_label.upper()} GAME LOGS — Last {len(recent_vals)} games]
- Average: {avg_val} | Min: {min_val} | Max: {max_val} | StdDev: {std_dev}
- Home avg: {round(sum(home_vals)/len(home_vals),1) if home_vals else 'N/A'} ({len(home_vals)} games)
- Away avg: {round(sum(away_vals)/len(away_vals),1) if away_vals else 'N/A'} ({len(away_vals)} games)
- Venue-filtered ({player_venue}): avg {round(sum(venue_values)/len(venue_values),1) if venue_values else 'N/A'} ({len(venue_values)} games)""")

            game_lines = []
            for g in player_game_logs[:15]:
                ps = g.get("playerStats", {})
                game_lines.append(
                    f"  {g.get('date','')} vs {g.get('opponent','')} ({g.get('venue','')}) {g.get('result','')}: "
                    f"{prop_label}={g.get('targetStat',0)} | "
                    f"PTS={ps.get('points',0)} REB={ps.get('rebounds',0)} AST={ps.get('assists',0)} "
                    f"3PM={ps.get('tpm',0)} FGM={ps.get('fgm',0)} "
                    f"MIN={ps.get('minutes','0')}"
                )
            parts.append("[GAME-BY-GAME]\n" + "\n".join(game_lines))

        # Team recent form
        if team_games_parsed:
            recent = team_games_parsed[:10]
            wins = sum(1 for g in recent if g.get("result") == "W")
            losses = sum(1 for g in recent if g.get("result") == "L")
            avg_score = round(sum(g.get("teamScore", 0) for g in recent) / max(len(recent), 1), 1)
            avg_opp = round(sum(g.get("oppScore", 0) for g in recent) / max(len(recent), 1), 1)
            parts.append(f"""[TEAM RECENT FORM — Last {len(recent)} games]
- Record: {wins}W-{losses}L | Avg Score: {avg_score} | Avg Opp Score: {avg_opp}""")

        # Opponent form
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
            parts.append(f"[H2H — Last {min(5,len(h2h_data))} meetings]\n" + "\n".join(h2h_lines))

        # H2H player stats (if we have player_id)
        if h2h_data and player_id:
            h2h_game_ids = [h.get("id") for h in h2h_data[:5] if h.get("id")]
            # Check if any of these games are in our player_game_logs
            h2h_player_vals = []
            h2h_game_set = set(h2h_game_ids)
            for g in player_game_logs:
                if g.get("gameId") in h2h_game_set:
                    h2h_player_vals.append(g.get("targetStat", 0))

            if h2h_player_vals:
                parts.append(f"""[H2H PLAYER {prop_label.upper()} vs THIS OPPONENT]
- Avg: {round(sum(h2h_player_vals)/len(h2h_player_vals),1)} | Games: {len(h2h_player_vals)} | Values: {h2h_player_vals}""")

        data_digest = "\n\n".join(parts)
        print(f"[BBALL TIMING] Data digest: {_t.time()-t0:.1f}s, {len(data_digest)} chars")

        # ═══════════════════════════════════════
        # REAL RECENT SAMPLES for frontend
        # ═══════════════════════════════════════
        real_recent_samples = []
        for g in player_game_logs[:20]:
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

        MIN_RESULTS = 3
        ai_results = []
        pending = set(ai_tasks)
        deadline = t0 + 48

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

        for t in pending:
            t.cancel()
        print(f"[BBALL TIMING] AIs done: {_t.time()-t0:.1f}s, {len(ai_results)} succeeded ({', '.join(r.get('_source','?') for r in ai_results)})")

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

            grok_pred = next((p for p in valid_preds if p.get("_source") == "grok"), None)
            for field in ["tacticalBreakdown", "reasoning", "sharpSummary", "scenarioAnalysis", "keyEvidence"]:
                if grok_pred and len(str(grok_pred.get(field, ""))) > 50:
                    prediction[field] = grok_pred[field]
                else:
                    best = max(valid_preds, key=lambda p: len(str(p.get(field, ""))))
                    prediction[field] = best.get(field, "")

            recs = [p.get("recommendation", "over") for p in valid_preds]
            sources = [p.get("_source", "?") for p in valid_preds]
            over_count = sum(1 for r in recs if r == "over")
            consensus = f"{len(valid_preds)} AI models analyzed ({', '.join(sources)}). "
            if all(r == prediction["recommendation"] for r in recs):
                consensus += f"Unanimous {prediction['recommendation'].upper()}."
            else:
                consensus += f"Split: {over_count} OVER, {len(recs)-over_count} UNDER. Consensus → {prediction['recommendation'].upper()}."
            prediction["consensusNote"] = consensus
        else:
            pv = prediction.get("projectedValue", req.line)
            prediction["recommendation"] = "over" if pv > req.line else "under"

        for p in valid_preds:
            p.pop("_source", None)
        prediction.pop("_source", None)

        cs = prediction.get("confidenceScore", 50)
        prediction["confidenceLevel"] = "Very High" if cs >= 75 else "High" if cs >= 65 else "Medium" if cs >= 50 else "Low"

        # Ensure required fields
        player_pos = player_info.get("position", "") if player_info else ""
        prediction.setdefault("player", {"id": player_id or 0, "name": req.playerName, "team": req.teamName, "position": player_pos})
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

        if real_recent_samples:
            prediction["recentSamples"] = real_recent_samples

        real_matchup = prediction.get("matchupOverview", {})
        real_matchup["homeTeam"] = req.teamName if player_venue == "home" else req.opponentName
        real_matchup["awayTeam"] = req.opponentName if player_venue == "home" else req.teamName
        prediction["matchupOverview"] = real_matchup

        if stat_values:
            recent_vals = stat_values[:20]
            home_v = [g["targetStat"] for g in player_game_logs[:20] if g.get("venue") == "home" and g.get("targetStat") is not None]
            away_v = [g["targetStat"] for g in player_game_logs[:20] if g.get("venue") == "away" and g.get("targetStat") is not None]
            prediction["playerGameLogs"] = {
                "targetProp": req.propType,
                "sampleSize": len(recent_vals),
                "rawAvg": round(sum(recent_vals) / len(recent_vals), 1),
                "rawMin": min(recent_vals),
                "rawMax": max(recent_vals),
                "stdDev": round(stats_mod.stdev(recent_vals), 2) if len(recent_vals) >= 3 else 0,
                "homeAvg": round(sum(home_v) / len(home_v), 1) if home_v else 0,
                "awayAvg": round(sum(away_v) / len(away_v), 1) if away_v else 0,
            }

        total_logs = len(player_game_logs)
        if total_logs >= 10:
            prediction["dataQuality"] = {"level": "good", "message": "", "gamesWithData": total_logs, "totalGames": total_logs}
        elif total_logs >= 3:
            prediction["dataQuality"] = {"level": "limited", "message": f"Only {total_logs} game logs available.", "gamesWithData": total_logs, "totalGames": total_logs}
        else:
            prediction["dataQuality"] = {"level": "low", "message": f"Only {total_logs} game logs. Limited sample size.", "gamesWithData": total_logs, "totalGames": total_logs}

        # ═══════════════════════════════════════
        # SYNTHESIS STEP
        # ═══════════════════════════════════════
        rec = prediction.get('recommendation', 'over').upper()
        proj = prediction.get('projectedValue', '?')
        conf = prediction.get('confidenceScore', '?')
        consensus_note = prediction.get('consensusNote', '')

        all_texts = []
        for p in valid_preds:
            src = p.get("_source", "AI")
            bits = []
            for field in ["tacticalBreakdown", "reasoning", "scenarioAnalysis", "keyEvidence", "sharpSummary", "gameFlowDynamics"]:
                val = p.get(field, "")
                if isinstance(val, dict):
                    val = json.dumps(val)
                if val and len(str(val)) > 10:
                    bits.append(f"{field}: {val}")
            if bits:
                all_texts.append(f"[{src}]\n" + "\n".join(bits))

        synthesis_input = "\n\n".join(all_texts)

        try:
            synth_prompt = f"""Synthesize multiple AI analyses into ONE elite tactical breakdown for an NBA {prop_label} prop prediction.

FINAL VERDICT: {rec} {req.line} {prop_label} (Projected: {proj}, Confidence: {conf}%, {consensus_note})
Player: {req.playerName} ({req.teamName}) vs {req.opponentName} ({player_venue.upper()})

AI analyses:
{synthesis_input[:4000]}

Write ~1500 char markdown. Format:
**Verdict: {rec} {req.line} {prop_label}**
[1-2 sentence sharp summary with projection vs line]

**Analysis**
[3-4 sentences. Cite specific numbers: per-game averages, venue splits, sample sizes]

**Game Script Scenarios**
[Best case / Worst case / Most likely — with stat projections]

**Key Evidence**
[3-4 bullet points — strongest data]

**Risk Radar**
[Blowout risk, minutes, matchup, back-to-back, injury]

**TL;DR** — {rec} {req.line} at {conf}% confidence. Projected: {proj} {prop_label.lower()}. {consensus_note}

Rules: No AI model names. Specific numbers. Decisive."""

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
                print(f"[BBALL TIMING] Synthesis done: {_t.time()-t0:.1f}s, {len(synth_text)} chars")
        except Exception as synth_err:
            print(f"[BBALL SYNTHESIS] Fallback — {synth_err}")

        prediction["sport"] = "basketball"
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
    """Search for NBA basketball teams."""
    query = req.get("query", "")
    if not query:
        raise HTTPException(status_code=400, detail="Query required")
    teams = await search_nba_teams(query)
    return {"teams": [{"id": t.get("id"), "name": t.get("name"), "logo": t.get("logo", "")} for t in teams]}
