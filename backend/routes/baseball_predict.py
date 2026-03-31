"""
Baseball Prediction Pipeline — completely separate from soccer.
Uses same 5-AI consensus + synthesis engine pattern.
Team data from API-Sports Baseball, player analysis from AI knowledge.
"""
import json
import uuid
import asyncio as aio
import time as _t
import traceback
import litellm
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from openai import OpenAI

from config import db, EMERGENT_LLM_KEY, XAI_API_KEY
from baseball_utils import (
    get_baseball_team_games, get_baseball_team_stats,
    get_baseball_standings, get_baseball_h2h, get_baseball_odds,
    search_baseball_teams, parse_game_for_team,
    MLB_LEAGUE_ID, BASEBALL_CURRENT_SEASON,
)

router = APIRouter(prefix="/api/baseball", tags=["baseball"])

EMERGENT_PROXY = "https://integrations.emergentagent.com/llm"


class BaseballPredictionRequest(BaseModel):
    teamId: int
    teamName: str
    opponentId: int
    opponentName: str
    playerName: str
    venue: str  # "home" or "away"
    propType: str  # hits, home_runs, rbis, strikeouts, stolen_bases, walks, total_bases, pitcher_strikeouts, earned_runs, etc.
    line: float


class BaseballTeamSearchRequest(BaseModel):
    query: str


@router.post("/search-teams")
async def search_teams(req: BaseballTeamSearchRequest):
    results = await search_baseball_teams(req.query)
    teams = []
    for t in results:
        teams.append({
            "id": t.get("id"),
            "name": t.get("name"),
            "logo": t.get("logo"),
            "country": t.get("country", {}).get("name", ""),
        })
    return {"teams": teams}


@router.post("/predict")
async def baseball_predict(req: BaseballPredictionRequest):
    try:
        _t0 = _t.time()
        player_venue = req.venue.lower()

        # =============================================
        # WAVE 1: Parallel team data fetching
        # =============================================
        team_games_task = get_baseball_team_games(req.teamId, last=30)
        opp_games_task = get_baseball_team_games(req.opponentId, last=30)
        team_stats_task = get_baseball_team_stats(req.teamId)
        opp_stats_task = get_baseball_team_stats(req.opponentId)
        standings_task = get_baseball_standings()
        h2h_task = get_baseball_h2h(req.teamId, req.opponentId, 10)

        team_games, opp_games, team_stats, opp_stats, standings_raw, h2h_games = await aio.gather(
            team_games_task, opp_games_task, team_stats_task, opp_stats_task, standings_task, h2h_task
        )
        print(f"[BASEBALL TIMING] Wave 1: {_t.time()-_t0:.1f}s")

        # =============================================
        # PARSE GAME DATA — venue-prioritized
        # =============================================
        parsed_team_games = [parse_game_for_team(g, req.teamId) for g in team_games]
        parsed_opp_games = [parse_game_for_team(g, req.opponentId) for g in opp_games]

        # Venue-prioritized: matching venue first
        venue_games = [g for g in parsed_team_games if g["venue"] == player_venue]
        other_games = [g for g in parsed_team_games if g["venue"] != player_venue]
        all_sorted_games = venue_games + other_games

        opp_venue = "away" if player_venue == "home" else "home"
        opp_venue_games = [g for g in parsed_opp_games if g["venue"] == opp_venue]

        # =============================================
        # BUILD DATA DIGEST
        # =============================================
        def build_digest():
            parts = []

            # 1. Team recent games (venue-prioritized)
            if all_sorted_games:
                lines = []
                for g in all_sorted_games[:20]:
                    lines.append(f"  {g['date']} {g['venue'].upper()[:1]} vs {g['opponent']}: {g['result']} {g['teamRuns']}-{g['oppRuns']} (H:{g['teamHits']} E:{g['teamErrors']})")
                parts.append(f"[{req.teamName} RECENT GAMES ({len(venue_games)} {player_venue}, {len(other_games)} {opp_venue})]\n" + "\n".join(lines))

            # 2. Team season stats
            if team_stats:
                ts_games = team_stats.get("games", {})
                ts_points = team_stats.get("points", {})
                wins_all = ts_games.get("wins", {}).get("all", {})
                loses_all = ts_games.get("loses", {}).get("all", {})
                wins_venue = ts_games.get("wins", {}).get(player_venue, {})
                loses_venue = ts_games.get("loses", {}).get(player_venue, {})
                pts_for = ts_points.get("for", {})
                pts_against = ts_points.get("against", {})
                parts.append(f"""[{req.teamName} SEASON STATS]
- Overall: {wins_all.get('total','?')}W-{loses_all.get('total','?')}L ({wins_all.get('percentage','?')})
- {player_venue.upper()}: {wins_venue.get('total','?')}W-{loses_venue.get('total','?')}L ({wins_venue.get('percentage','?')})
- Runs/Game: {pts_for.get('average',{}).get('all','?')} | Allowed: {pts_against.get('average',{}).get('all','?')}
- {player_venue.upper()} Runs/Game: {pts_for.get('average',{}).get(player_venue,'?')} | Allowed: {pts_against.get('average',{}).get(player_venue,'?')}
- Total Runs ({player_venue}): {pts_for.get('total',{}).get(player_venue,'?')} in {ts_games.get('played',{}).get(player_venue,'?')} games""")

            # 3. Opponent season stats
            if opp_stats:
                os_games = opp_stats.get("games", {})
                os_points = opp_stats.get("points", {})
                ow = os_games.get("wins", {}).get("all", {})
                ol = os_games.get("loses", {}).get("all", {})
                ow_v = os_games.get("wins", {}).get(opp_venue, {})
                ol_v = os_games.get("loses", {}).get(opp_venue, {})
                op_for = os_points.get("for", {})
                op_against = os_points.get("against", {})
                parts.append(f"""[{req.opponentName} SEASON STATS ({opp_venue.upper()})]
- Overall: {ow.get('total','?')}W-{ol.get('total','?')}L ({ow.get('percentage','?')})
- {opp_venue.upper()}: {ow_v.get('total','?')}W-{ol_v.get('total','?')}L ({ow_v.get('percentage','?')})
- Runs/Game: {op_for.get('average',{}).get('all','?')} | Allowed: {op_against.get('average',{}).get('all','?')}
- {opp_venue.upper()} Runs Allowed: {op_against.get('average',{}).get(opp_venue,'?')}""")

            # 4. Opponent recent games
            if opp_venue_games:
                lines = []
                for g in opp_venue_games[:10]:
                    lines.append(f"  {g['date']} {g['venue'].upper()[:1]} vs {g['opponent']}: {g['result']} {g['teamRuns']}-{g['oppRuns']} (H:{g['teamHits']})")
                parts.append(f"[{req.opponentName} RECENT {opp_venue.upper()} GAMES]\n" + "\n".join(lines))

            # 5. H2H
            if h2h_games:
                h2h_parsed = [parse_game_for_team(g, req.teamId) for g in h2h_games]
                lines = []
                for g in h2h_parsed[:8]:
                    lines.append(f"  {g['date']} {g['venue'].upper()[:1]}: {g['result']} {g['teamRuns']}-{g['oppRuns']} (H:{g['teamHits']})")
                parts.append(f"[H2H vs {req.opponentName} ({len(h2h_parsed)} games)]\n" + "\n".join(lines))

            # 6. Standings
            if standings_raw:
                try:
                    flat = []
                    for group in standings_raw:
                        if isinstance(group, list):
                            flat.extend(group)
                        elif isinstance(group, dict):
                            flat.append(group)
                    # Find team's division
                    team_div = None
                    for s in flat:
                        if s.get("team", {}).get("id") == req.teamId:
                            team_div = s.get("group", {}).get("name", "")
                            break
                    # Show relevant standings
                    relevant = [s for s in flat if s.get("group", {}).get("name") == team_div] if team_div else flat[:15]
                    lines = []
                    for s in sorted(relevant, key=lambda x: x.get("position", 99)):
                        t = s.get("team", {}).get("name", "?")
                        w = s.get("games", {}).get("win", {}).get("total", 0)
                        l_val = s.get("games", {}).get("lose", {}).get("total", 0)
                        pf = s.get("points", {}).get("for", 0)
                        pa = s.get("points", {}).get("against", 0)
                        lines.append(f"  {s.get('position','?')}. {t} — {w}W-{l_val}L (RF:{pf} RA:{pa})")
                    if lines:
                        header = f"[STANDINGS — {team_div}]" if team_div else "[STANDINGS]"
                        parts.append(header + "\n" + "\n".join(lines[:10]))
                except Exception:
                    pass

            return "\n\n".join(parts)

        data_digest = build_digest()

        # Venue averages from parsed games
        if venue_games:
            avg_runs = round(sum(g["teamRuns"] for g in venue_games) / len(venue_games), 1)
            avg_hits = round(sum(g["teamHits"] for g in venue_games) / len(venue_games), 1)
            venue_summary = f"Team {player_venue} averages: {avg_runs} runs, {avg_hits} hits per game ({len(venue_games)} games)"
        else:
            venue_summary = "No venue-specific games available"

        # =============================================
        # 5-AI CONSENSUS ENGINE (same pattern as soccer)
        # =============================================
        prop_display_map = {
            "hits": "Hits", "home_runs": "Home Runs", "rbis": "RBIs",
            "runs": "Runs", "strikeouts": "Strikeouts", "stolen_bases": "Stolen Bases",
            "walks": "Walks", "total_bases": "Total Bases",
            "pitcher_strikeouts": "Pitcher Strikeouts", "earned_runs": "Earned Runs",
            "hits_allowed": "Hits Allowed", "walks_allowed": "Walks Allowed",
            "outs_recorded": "Outs Recorded", "singles": "Singles",
            "doubles": "Doubles", "triples": "Triples",
        }
        prop_display = prop_display_map.get(req.propType, req.propType.replace("_", " ").title())

        PREDICTION_SYSTEM = f"""Elite MLB player prop prediction engine. You have deep knowledge of MLB player statistics, tendencies, and matchups.

SPORT: BASEBALL (MLB)
You are analyzing a {prop_display} prop for {req.playerName} ({req.teamName}).

Use the provided team-level data AND your knowledge of this specific MLB player to generate a calibrated prediction. Consider:
- Player's season stats, recent form, batting average, OPS, plate discipline
- Pitcher matchup (if batter prop: opposing starter's stats; if pitcher prop: opposing lineup quality)
- Ballpark factors (some parks favor hitters/pitchers)
- Lineup position and batting order
- L/R splits, day/night splits
- Team's offensive/defensive tendencies from provided data
- Weather and game conditions when relevant

REQUIREMENTS:
- "reasoning": 3-5 sentences citing player-specific stats + team data
- "tacticalBreakdown": ~1500 char markdown with **Verdict**, **Analysis** (cite player averages, matchup data), **Scenarios**, **Key Evidence**, **Risk Radar**, **TL;DR**
- "scenarioAnalysis": 2-3 sentences with projections per scenario
- "sharpSummary": 2 sentences on why projection differs from line
- "keyEvidence": 2-3 strongest data points as string
- "gameFlowDynamics", "sensitivityTests", "subRisk", "uncertaintyNote": 1-2 sentences each

RULES: |proj-line|<0.3 → max 52% conf. recentSamples=[]. No AI model names.

JSON: {{"projectedValue":0,"recommendation":"over|under","confidenceScore":0,"confidenceLevel":"","sharpSummary":"","reasoning":"","scenarioAnalysis":"","keyEvidence":"","sensitivityTests":"","subRisk":"","gameFlowDynamics":"","uncertaintyNote":"","tacticalBreakdown":"","matchupOverview":{{"homeTeam":"","awayTeam":"","favorite":"","moneyline":{{"home":"","away":""}},"expectedGameType":"","keyMatchupFactor":""}},"bayesianMetrics":{{"priorMean":0,"momentumEffect":0,"covariateAdjustment":0,"reversalFlag":"stable"}},"probabilityCurve":[],"recentSamples":[],"player":{{"id":0,"name":"","team":"","position":""}},"opponent":"","propType":"","line":0,"confidenceInterval":[0,0],"tacticalAlerts":[]}}"""

        prompt = f"""{req.playerName} | {req.teamName} vs {req.opponentName} | {player_venue.upper()} | {prop_display} line {req.line}
{venue_summary}
recentSamples=[]

{data_digest[:6000]}

Analyze ALL data + your MLB knowledge thoroughly. Return JSON only."""

        # 5 AIs in TRUE parallel
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
                print(f"[BASEBALL AI] {label} failed: {e}")
                return None

        async def call_grok(label="grok"):
            try:
                grok_client = OpenAI(api_key=XAI_API_KEY, base_url="https://api.x.ai/v1")
                loop = aio.get_event_loop()
                def _run():
                    return grok_client.chat.completions.create(
                        model="grok-4-fast-reasoning",
                        messages=[
                            {"role": "system", "content": PREDICTION_SYSTEM},
                            {"role": "user", "content": prompt},
                        ],
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
                raise ValueError("No valid JSON found")
            except Exception as e:
                print(f"[BASEBALL AI] {label} failed: {e}")
                return None

        ai_tasks = [
            aio.ensure_future(call_ai("gemini-2.0-flash", "gemini", "gemini")),
            aio.ensure_future(call_ai("gemini-2.5-flash", "gemini25", "gemini")),
            aio.ensure_future(call_ai("gpt-4o", "gpt4o")),
            aio.ensure_future(call_ai("claude-sonnet-4-20250514", "claude")),
            aio.ensure_future(call_grok("grok")),
        ]

        # First-3-wins
        MIN_RESULTS = 3
        ai_results = []
        pending = set(ai_tasks)
        deadline = _t0 + 48

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
        print(f"[BASEBALL TIMING] AIs done: {_t.time()-_t0:.1f}s, {len(ai_results)} succeeded ({', '.join(r.get('_source','?') for r in ai_results)})")

        if not ai_results:
            raise ValueError("All AI models failed for baseball prediction")

        # =============================================
        # MERGE CONSENSUS (same pattern as soccer)
        # =============================================
        valid_preds = ai_results
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

            # Text: prioritize Grok
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

        # =============================================
        # SYNTHESIS (Gemini combines all AI analyses)
        # =============================================
        rec = prediction.get("recommendation", "over").upper()
        proj = prediction.get("projectedValue", "?")
        conf = prediction.get("confidenceScore", "?")
        consensus_note = prediction.get("consensusNote", "")

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

        try:
            synth_prompt = f"""Synthesize multiple AI analyses into ONE elite tactical breakdown for an MLB {prop_display} prop.

FINAL VERDICT: {rec} {req.line} {prop_display} (Projected: {proj}, Confidence: {conf}%, {consensus_note})
Player: {req.playerName} ({req.teamName}) vs {req.opponentName} ({player_venue.upper()})

Individual AI analyses:
{chr(10).join(all_texts)[:4000]}

Write ~1500 char markdown:
**Verdict: {rec} {req.line} {prop_display}**
[Sharp summary]

**Analysis** [Cite specific stats, matchup data, trends]
**Game Script Scenarios** [Best/worst/likely with stat projections]
**Key Evidence** [3-4 bullet points]
**Risk Radar** [What could go wrong]
**TL;DR** — {rec} {req.line} at {conf}% confidence. Projected: {proj} {prop_display.lower()}. {consensus_note}

No AI model names. Be specific with numbers."""

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
                print(f"[BASEBALL TIMING] Synthesis: {_t.time()-_t0:.1f}s, {len(synth_text)} chars")
        except Exception as synth_err:
            print(f"[BASEBALL SYNTHESIS] Fallback: {synth_err}")

        # Clean up
        for p in valid_preds:
            p.pop("_source", None)
        prediction.pop("_source", None)

        # Set confidence level
        cs = prediction.get("confidenceScore", 50)
        prediction["confidenceLevel"] = "Very High" if cs >= 75 else "High" if cs >= 65 else "Medium" if cs >= 50 else "Low"

        # Defaults
        prediction.setdefault("player", {"id": 0, "name": req.playerName, "team": req.teamName, "position": "Unknown"})
        prediction.setdefault("opponent", req.opponentName)
        prediction.setdefault("propType", req.propType)
        prediction.setdefault("line", req.line)
        prediction.setdefault("projectedValue", req.line)
        prediction.setdefault("recommendation", "over")
        prediction.setdefault("confidenceScore", 50)
        prediction.setdefault("confidenceLevel", "Medium")
        prediction.setdefault("confidenceInterval", [req.line * 0.7, req.line * 1.3])
        prediction.setdefault("recentSamples", [])
        prediction.setdefault("bayesianMetrics", {"priorMean": req.line, "momentumEffect": 0, "covariateAdjustment": 0, "reversalFlag": "stable"})
        prediction.setdefault("probabilityCurve", [])
        prediction.setdefault("reasoning", "Analysis based on available data.")
        prediction.setdefault("tacticalInsights", "")
        prediction.setdefault("consensusNote", "")

        # Build recentSamples from team game data (team-level, not player-level)
        # These represent team performance in recent games for context
        team_samples = []
        for g in all_sorted_games[:20]:
            team_samples.append({
                "date": g["date"],
                "opponent": g["opponent"],
                "venue": g["venue"],
                "value": g["teamHits"] if "hit" in req.propType.lower() else g["teamRuns"],
                "minutesPlayed": 9,  # innings
                "result": g["result"],
            })
        prediction["recentSamples"] = team_samples

        # Matchup override with real data
        real_matchup = prediction.get("matchupOverview", {})
        real_matchup["homeTeam"] = req.teamName if player_venue == "home" else req.opponentName
        real_matchup["awayTeam"] = req.opponentName if player_venue == "home" else req.teamName
        prediction["matchupOverview"] = real_matchup

        # Sport identifier
        prediction["sport"] = "baseball"

        # Game log metadata
        prediction["playerGameLogs"] = {
            "sampleSize": len(all_sorted_games),
            "venueGames": len(venue_games),
            "source": "team-level (MLB API does not provide individual player box scores)"
        }

        # Save to MongoDB (separate collection)
        prediction["_created"] = datetime.now(timezone.utc).isoformat()
        prediction["_request"] = req.model_dump()
        await db.baseball_predictions.insert_one(prediction)
        prediction.pop("_id", None)

        return prediction

    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Baseball prediction failed: {str(e)}")
