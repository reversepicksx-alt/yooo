"""
NHL prediction routes — /api/nhl/*
"""
import asyncio
import logging
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional

from config import db, GEMINI_API_KEY
import nhl_client
import nhl_engine

log = logging.getLogger("nhl_routes")
router = APIRouter(prefix="/api/nhl", tags=["nhl"])

CURRENT_NHL_SEASON = nhl_client.CURRENT_NHL_SEASON


# ── AI analysis ───────────────────────────────────────────────────────────────

async def _get_nhl_ai_analysis(
    player_name: str, position: str, prop_type: str, line: float,
    venue: str, opponent: str, projection: float,
    p_over: float, p_under: float, recommendation: str,
    game_logs: list, prior_mean: float, streak_flag: str,
    opp_goals_per_game: Optional[float] = None,
) -> dict:
    try:
        import google.generativeai as genai
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel("gemini-2.5-flash")

        prop_label = prop_type.replace("_", " ").title()
        rec_label  = recommendation.upper()
        conf       = round(max(p_over, p_under))
        field      = nhl_engine.NHL_PROPS.get(prop_type, prop_type)
        ctx_lines  = []
        for i, g in enumerate(game_logs[:7]):
            val  = g.get(field, "?")
            date = g.get("date", "")
            ctx_lines.append(f"  G{i+1} ({date}): {val} {prop_label}")
        game_ctx = "\n".join(ctx_lines) or "  (no recent data)"

        streak_text = ""
        if streak_flag == "OVER_STREAK":
            streak_text = " OVER streak across last 4+ games."
        elif streak_flag == "UNDER_STREAK":
            streak_text = " UNDER streak across last 4+ games."

        prompt = f"""You are a sharp NHL prop betting analyst. Analyze this pick concisely.

PLAYER: {player_name} ({position or "Skater/Goalie"})
PROP: {prop_label} | LINE: {line} | VENUE: {venue.upper()} vs {opponent or "opponent"}
PROJECTION: {projection} | P(OVER): {p_over}% | P(UNDER): {p_under}%
RECOMMENDATION: {rec_label} ({conf}% confidence)
PRIOR MEAN: {prior_mean}{streak_text}
{"OPP G/GAME: " + str(opp_goals_per_game) if opp_goals_per_game else ""}

RECENT GAME LOG:
{game_ctx}

Write a sharp 2-3 sentence analysis of the {rec_label}. Focus on form, matchup, and ice time. Be direct."""

        resp = await asyncio.to_thread(model.generate_content, prompt)
        text = (resp.text or "").strip()
        return {
            "sharpSummary":      text[:600] if text else "",
            "tacticalBreakdown": text,
            "reasoning":         f"Bayesian projection: {projection} | P(OVER)={p_over}% P(UNDER)={p_under}%",
        }
    except Exception as e:
        log.warning(f"[NHL AI] {e}")
        return {"sharpSummary": "", "tacticalBreakdown": "", "reasoning": ""}


# ── Player search ─────────────────────────────────────────────────────────────

@router.get("/players/search")
async def search_nhl_players(q: str = Query("", min_length=2), limit: int = Query(15)):
    try:
        players = await nhl_client.search_players(q, limit=limit)
        return [
            {
                "id":        p.get("id"),
                "fullName":  f"{p.get('first_name','')} {p.get('last_name','')}".strip(),
                "firstName": p.get("first_name"),
                "lastName":  p.get("last_name"),
                "position":  p.get("position", ""),
                "team":      p.get("team") or {},
                "nationality": p.get("nationality"),
            }
            for p in players
        ]
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"NHL player search failed: {e}")


# ── Teams ─────────────────────────────────────────────────────────────────────

@router.get("/teams")
async def get_nhl_teams():
    try:
        teams = await nhl_client.get_teams()
        return [
            {
                "id":           t.get("id"),
                "fullName":     t.get("full_name"),
                "abbreviation": t.get("abbreviation"),
                "city":         t.get("city"),
                "name":         t.get("name"),
                "conference":   t.get("conference"),
                "division":     t.get("division"),
            }
            for t in teams
        ]
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"NHL teams failed: {e}")


# ── Predict ───────────────────────────────────────────────────────────────────

class NhlPredictRequest(BaseModel):
    playerName:        str
    playerId:          Optional[int]   = None
    teamName:          Optional[str]   = ""
    position:          Optional[str]   = ""
    propType:          str
    line:              float
    opponentName:      Optional[str]   = ""
    venue:             Optional[str]   = "home"
    season:            Optional[int]   = CURRENT_NHL_SEASON
    oppGoalsPerGame:   Optional[float] = None
    restDays:          Optional[int]   = None


@router.post("/predict")
async def nhl_predict(req: NhlPredictRequest):
    prop_type = req.propType.lower().strip()
    venue     = (req.venue or "home").lower()
    if venue not in ("home", "away"):
        venue = "home"

    valid_props = set(nhl_engine.NHL_PROPS.keys())
    if prop_type not in valid_props:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown NHL prop: {prop_type}. Valid: {sorted(valid_props)}"
        )

    if req.line <= 0:
        raise HTTPException(status_code=400, detail="Line must be positive.")

    # ── Resolve player ────────────────────────────────────────────────────────
    player_id   = req.playerId
    player_data = None
    position    = req.position or ""
    team_name   = req.teamName or ""

    if player_id:
        player_data = await nhl_client.get_player(player_id)

    if not player_data and req.playerName:
        results = await nhl_client.search_players(req.playerName, limit=5)
        if results:
            best        = results[0]
            player_id   = best.get("id")
            player_data = await nhl_client.get_player(player_id) or best

    if player_data:
        position  = position or player_data.get("position_code", player_data.get("position", ""))
        teams     = player_data.get("teams") or []
        if teams and not team_name:
            teams_sorted = sorted(teams, key=lambda t: t.get("season", 0), reverse=True)
            team_name = teams_sorted[0].get("full_name", "")

    if not player_id:
        raise HTTPException(status_code=404, detail=f"Player '{req.playerName}' not found in NHL database.")

    log.info(f"[NHL PREDICT] {req.playerName} ({player_id}) | {prop_type} {req.line} | {venue}")

    # Try current season first; fall back one year if no data yet
    cur_season  = req.season or CURRENT_NHL_SEASON
    game_logs   = []
    for try_season in [cur_season, cur_season - 1]:
        try:
            logs_r = await nhl_client.get_player_game_logs(player_id, try_season)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Failed to fetch NHL data: {e}")
        if logs_r:
            game_logs = logs_r
            break

    if not game_logs:
        raise HTTPException(
            status_code=422,
            detail=f"No game log data found for {req.playerName} in NHL season {cur_season} or {cur_season - 1}.",
        )

    result = nhl_engine.compute_nhl_projection(
        game_logs          = game_logs,
        prop_type          = prop_type,
        line               = req.line,
        venue              = venue,
        opp_goals_per_game = req.oppGoalsPerGame,
        rest_days          = req.restDays,
    )

    if "error" in result:
        raise HTTPException(status_code=422, detail=result["error"])

    # ── [BAYESIAN TRUTH] override ─────────────────────────────────────────────
    p_over  = result["pOver"]
    p_under = result["pUnder"]
    result["recommendation"]  = "over" if p_over >= p_under else "under"
    result["confidenceScore"] = round(max(p_over, p_under))
    conf = result["confidenceScore"]
    result["confidenceLevel"] = "High" if conf >= 70 else "Medium" if conf >= 60 else "Low"

    ai_task = asyncio.create_task(_get_nhl_ai_analysis(
        player_name        = req.playerName,
        position           = position,
        prop_type          = prop_type,
        line               = req.line,
        venue              = venue,
        opponent           = req.opponentName or "",
        projection         = result["projection"],
        p_over             = p_over,
        p_under            = p_under,
        recommendation     = result["recommendation"],
        game_logs          = game_logs,
        prior_mean         = result["priorMean"],
        streak_flag        = result["streakFlag"],
        opp_goals_per_game = req.oppGoalsPerGame,
    ))

    field = nhl_engine.NHL_PROPS.get(prop_type, prop_type)
    game_log_tiles = []
    for g in game_logs[:10]:
        game_log_tiles.append({
            "date":    g.get("date", ""),
            "value":   g.get(field),
            "venue":   g.get("venue", ""),
            "goals":   g.get("goals"),
            "assists": g.get("assists"),
            "shots":   g.get("shots"),
            "toi":     g.get("toi"),
            "saves":   g.get("saves"),
        })

    try:
        ai_result = await asyncio.wait_for(ai_task, timeout=25.0)
    except Exception:
        ai_result = {"sharpSummary": "", "tacticalBreakdown": "", "reasoning": ""}

    return {
        "sport":            "nhl",
        "playerName":       req.playerName,
        "playerId":         player_id,
        "teamName":         team_name,
        "position":         position,
        "propType":         prop_type,
        "line":             req.line,
        "venue":            venue,
        "opponentName":     req.opponentName or "",
        "projection":       result["projection"],
        "pOver":            result["pOver"],
        "pUnder":           result["pUnder"],
        "recommendation":   result["recommendation"],
        "confidenceScore":  result["confidenceScore"],
        "confidenceLevel":  result["confidenceLevel"],
        "priorMean":        result["priorMean"],
        "momentum":         result["momentum"],
        "sampleSize":       result["sampleSize"],
        "streakFlag":       result["streakFlag"],
        "gameLogs":         game_log_tiles,
        "recentValues":     result.get("recentValues", []),
        "sharpSummary":     ai_result.get("sharpSummary", ""),
        "tacticalBreakdown": ai_result.get("tacticalBreakdown", ""),
        "reasoning":        ai_result.get("reasoning", ""),
        "rawConfidence":    result["confidenceScore"],
    }
