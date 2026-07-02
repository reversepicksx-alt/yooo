"""
Generic AI-only prediction routes for sports without structured data APIs.
Supports: NCAAF, F1, MMA, PGA Tour, Dota 2, League of Legends, College Baseball.

POST /api/ai-sport/predict
Uses Gemini 2.5 Flash to generate Bayesian-style predictions from AI knowledge.
"""
import asyncio
import json
import logging
import random
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config import XAI_API_KEY

log = logging.getLogger("ai_sports_routes")
router = APIRouter(prefix="/api/ai-sport", tags=["ai_sport"])

# ── Prop definitions per sport ─────────────────────────────────────────────────

SPORT_PROPS = {
    "ncaaf": {
        "passing_yards":   "Passing Yards",
        "passing_tds":     "Passing TDs",
        "completions":     "Completions",
        "rushing_yards":   "Rushing Yards",
        "rushing_tds":     "Rushing TDs",
        "carries":         "Carries",
        "receiving_yards": "Receiving Yards",
        "receiving_tds":   "Receiving TDs",
        "receptions":      "Receptions",
        "tackles":         "Tackles",
        "sacks":           "Sacks",
        "fantasy_points":  "Fantasy Points",
    },
    "f1": {
        "finishing_position":     "Finishing Position",
        "championship_points":    "Championship Points",
        "fastest_lap":            "Fastest Lap (Yes/No)",
        "laps_led":               "Laps Led",
        "pit_stops":              "Total Pit Stops",
        "qualifying_position":    "Qualifying Position",
        "podium_finish":          "Podium Finish (Yes/No)",
        "points_scored":          "Points Scored",
    },
    "mma": {
        "total_rounds":           "Total Rounds",
        "significant_strikes":    "Significant Strikes",
        "takedowns_landed":       "Takedowns Landed",
        "submission_attempts":    "Submission Attempts",
        "knockdowns":             "Knockdowns",
        "method_decision":        "Win by Decision (Yes/No)",
        "method_ko_tko":          "Win by KO/TKO (Yes/No)",
        "method_submission":      "Win by Submission (Yes/No)",
    },
    "pga": {
        "score_to_par":           "Score to Par (round)",
        "birdies":                "Birdies",
        "bogeys":                 "Bogeys",
        "eagles":                 "Eagles",
        "greens_in_regulation":   "Greens in Regulation",
        "fairways_hit":           "Fairways Hit",
        "putts":                  "Total Putts",
        "driving_distance":       "Driving Distance (yards)",
        "top_10_finish":          "Top 10 Finish (Yes/No)",
        "cut_made":               "Make Cut (Yes/No)",
        "strokes_gained_putting": "SG Putting",
    },
    "dota2": {
        "kills":           "Kills",
        "deaths":          "Deaths",
        "assists":         "Assists",
        "kda":             "KDA",
        "gpm":             "Gold Per Minute",
        "xpm":             "XP Per Minute",
        "last_hits":       "Last Hits",
        "maps_played":     "Maps Played",
        "first_blood":     "First Blood (Yes/No)",
        "fantasy_points":  "Fantasy Points",
    },
    "lol": {
        "kills":           "Kills",
        "deaths":          "Deaths",
        "assists":         "Assists",
        "kda":             "KDA",
        "cs":              "CS (Creep Score)",
        "cs_per_min":      "CS per Minute",
        "damage_dealt":    "Total Damage Dealt",
        "vision_score":    "Vision Score",
        "first_blood":     "First Blood (Yes/No)",
        "maps_played":     "Maps Played",
        "fantasy_points":  "Fantasy Points",
    },
    "cbase": {
        "hits":            "Hits",
        "rbis":            "RBIs",
        "runs":            "Runs Scored",
        "home_runs":       "Home Runs",
        "strikeouts":      "Strikeouts (Batter)",
        "walks":           "Walks",
        "stolen_bases":    "Stolen Bases",
        "total_bases":     "Total Bases",
        "pitcher_ks":      "Pitcher Strikeouts",
        "innings_pitched": "Innings Pitched",
        "earned_runs":     "Earned Runs",
        "hits_runs_rbis":  "Hits + Runs + RBIs",
    },
}

SPORT_LABELS = {
    "ncaaf": "College Football",
    "f1":    "Formula 1",
    "mma":   "MMA/UFC",
    "pga":   "PGA Tour",
    "dota2": "Dota 2",
    "lol":   "League of Legends",
    "cbase": "College Baseball",
}

SPORT_CONTEXTS = {
    "ncaaf": "college football (NCAA)",
    "f1":    "Formula 1 Grand Prix racing",
    "mma":   "MMA/UFC professional combat sports",
    "pga":   "PGA Tour professional golf",
    "dota2": "Dota 2 esports",
    "lol":   "League of Legends esports",
    "cbase": "NCAA college baseball",
}


class AiSportPredictRequest(BaseModel):
    sport:        str
    playerName:   str
    propType:     str
    line:         float
    teamName:     Optional[str]     = ""
    opponentName: Optional[str]     = ""
    venue:        Optional[str]     = "home"
    tournament:   Optional[str]     = ""
    extraContext: Optional[str]     = ""


@router.get("/props/{sport}")
async def get_sport_props(sport: str):
    props = SPORT_PROPS.get(sport.lower())
    if not props:
        raise HTTPException(status_code=404, detail=f"No prop definitions for sport: {sport}")
    return [{"value": k, "label": v} for k, v in props.items()]


@router.post("/predict")
async def ai_sport_predict(req: AiSportPredictRequest):
    from routes.auth import verify_session
    sess = await verify_session(req)
    if not sess.get("valid"):
        raise HTTPException(status_code=401, detail="Invalid or expired session. Please sign in again.")
    access = sess.get("access_type", "")
    if not access or access == "NoSubscription":
        raise HTTPException(status_code=403, detail="Active subscription required")
    sport = req.sport.lower().strip()
    if sport not in SPORT_PROPS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported sport: {sport}. Supported: {list(SPORT_PROPS.keys())}"
        )

    prop_type = req.propType.lower().strip()
    valid_props = SPORT_PROPS[sport]
    prop_label = valid_props.get(prop_type, prop_type.replace("_", " ").title())
    sport_label = SPORT_LABELS.get(sport, sport.upper())
    sport_context = SPORT_CONTEXTS.get(sport, sport)

    if req.line <= 0:
        raise HTTPException(status_code=400, detail="Line must be positive.")

    try:
        result = await asyncio.wait_for(
            _run_ai_prediction(req, sport, prop_label, sport_label, sport_context, valid_props),
            timeout=45.0
        )
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="AI prediction timed out — try again.")
    except Exception as e:
        log.error(f"[AI SPORT] {sport} prediction error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    return result


async def _run_ai_prediction(
    req: AiSportPredictRequest,
    sport: str,
    prop_label: str,
    sport_label: str,
    sport_context: str,
    valid_props: dict,
) -> dict:
    from ai_engine import _ai_call as _ai_sport_call

    opponent_str = f" vs {req.opponentName}" if req.opponentName else ""
    venue_str    = f" ({req.venue.upper()})" if req.venue else ""
    tourney_str  = f" at {req.tournament}" if req.tournament else ""
    extra_str    = f"\nAdditional context: {req.extraContext}" if req.extraContext else ""

    prop_type = req.propType.lower().strip()
    is_binary = prop_label.endswith("(Yes/No)")

    if is_binary:
        line_note = f"The sportsbook line is {req.line} (over = yes, under = no)."
        over_label = "YES"
        under_label = "NO"
    else:
        line_note = f"The sportsbook line is {req.line}."
        over_label = "OVER"
        under_label = "UNDER"

    prompt = f"""You are a sharp {sport_context} analytics expert and prop betting analyst.

PREDICTION REQUEST:
Sport: {sport_label}
Player/Athlete: {req.playerName}
Team: {req.teamName or "Unknown"}{opponent_str}{venue_str}{tourney_str}
Prop: {prop_label}
{line_note}{extra_str}

Using your knowledge of {req.playerName}'s recent form, statistics, historical performance, 
opponent analysis, and any relevant situational factors for {sport_label}, provide a sharp prediction.

Respond ONLY with valid JSON in this exact format:
{{
  "projection": <your best estimate of the actual value as a number>,
  "pOver": <probability of OVER as integer 0-100>,
  "pUnder": <probability of UNDER as integer 0-100>,
  "recommendation": "<over or under>",
  "confidenceScore": <integer 50-95>,
  "confidenceLevel": "<High|Medium|Low>",
  "priorMean": <historical/career average for this prop>,
  "streakFlag": "<OVER_STREAK|UNDER_STREAK|NEUTRAL>",
  "sharpSummary": "<2-3 sentence sharp analysis explaining the recommendation>",
  "tacticalBreakdown": "<detailed 3-5 sentence breakdown including form, matchup, and key factors>",
  "reasoning": "<1 sentence technical summary of the mathematical basis>"
}}

Rules:
- pOver + pUnder must equal 100
- recommendation must match whichever of pOver/pUnder is higher  
- confidenceLevel: High if confidenceScore >= 70, Medium if >= 60, else Low
- Be direct and confident. Base everything on real statistics and form you know about {req.playerName}.
- If you have limited information about this player, use league averages and be transparent in the summary."""

    try:
        import re as _re
        raw_text = await _ai_sport_call(prompt, temperature=0.3, max_tokens=1400, timeout=40, json_mode=True)
        raw_text = (raw_text or "").strip()
        if raw_text.startswith("```"):
            raw_text = _re.sub(r"```(?:json)?\s*", "", raw_text)
            raw_text = _re.sub(r"```\s*$", "", raw_text).strip()

        ai = json.loads(raw_text)
    except Exception as e:
        log.warning(f"[AI SPORT] Gemini error for {sport}/{req.playerName}: {e}")
        # Fallback: coin-flip near 50/50 based on line vs estimated mean
        ai = {
            "projection":       req.line,
            "pOver":            52,
            "pUnder":           48,
            "recommendation":   "over",
            "confidenceScore":  52,
            "confidenceLevel":  "Low",
            "priorMean":        req.line,
            "streakFlag":       "NEUTRAL",
            "sharpSummary":     f"Limited data available for {req.playerName}. Slight lean OVER {req.line} based on general {sport_label} trends.",
            "tacticalBreakdown": "",
            "reasoning":        "AI analysis unavailable — fallback to neutral projection.",
        }

    # Enforce BAYESIAN TRUTH: recommendation must match highest probability
    p_over  = int(ai.get("pOver", 50))
    p_under = int(ai.get("pUnder", 50))
    if p_over + p_under != 100:
        p_under = 100 - p_over

    rec   = "over" if p_over >= p_under else "under"
    conf  = max(p_over, p_under)
    level = "High" if conf >= 70 else "Medium" if conf >= 60 else "Low"

    projection = float(ai.get("projection", req.line))
    prior_mean = float(ai.get("priorMean", req.line))

    return {
        "sport":             sport,
        "playerName":        req.playerName,
        "teamName":          req.teamName or "",
        "opponentName":      req.opponentName or "",
        "propType":          req.propType,
        "line":              req.line,
        "venue":             req.venue or "home",
        "projection":        round(projection, 2),
        "pOver":             p_over,
        "pUnder":            p_under,
        "recommendation":    rec,
        "confidenceScore":   conf,
        "confidenceLevel":   level,
        "rawConfidence":     conf,
        "priorMean":         round(prior_mean, 2),
        "sampleSize":        0,
        "momentum":          0.0,
        "streakFlag":        ai.get("streakFlag", "NEUTRAL"),
        "sharpSummary":      ai.get("sharpSummary", ""),
        "tacticalBreakdown": ai.get("tacticalBreakdown", ""),
        "reasoning":         ai.get("reasoning", ""),
        "gameLogs":          [],
        "recentValues":      [],
        "bayesianMetrics":   {"priorMean": prior_mean, "sampleSize": 0, "aiOnly": True},
    }
