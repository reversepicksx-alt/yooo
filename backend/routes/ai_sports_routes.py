"""
Generic AI-only prediction routes for sports without structured data APIs.
Supports: NCAAF, F1, MMA, PGA Tour, Dota 2, League of Legends, College Baseball.

POST /api/ai-sport/predict
"""
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from engine_base import normalize_response

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

    return normalize_response({
        "available": False,
        "sport": sport,
        "playerName": req.playerName,
        "propType": req.propType,
        "line": req.line,
        "message": "This sport does not have a structured-data model in the current release.",
    })
