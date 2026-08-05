"""
WNBA prediction routes — /api/wnba/*
"""
import logging
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional

from config import db
import wnba_client
import wnba_engine

log = logging.getLogger("wnba_routes")
router = APIRouter(prefix="/api/wnba", tags=["wnba"])

CURRENT_WNBA_SEASON = wnba_client.CURRENT_WNBA_SEASON


# ── Player search ─────────────────────────────────────────────────────────────

@router.get("/players/search")
async def search_wnba_players(q: str = Query("", min_length=2), limit: int = Query(15)):
    try:
        players = await wnba_client.search_players(q, limit=limit)
        return [
            {
                "id":        p.get("id"),
                "fullName":  f"{p.get('first_name','')} {p.get('last_name','')}".strip(),
                "firstName": p.get("first_name"),
                "lastName":  p.get("last_name"),
                "position":  p.get("position", ""),
                "team":      p.get("team") or {},
                "jersey":    p.get("jersey_number"),
                "college":   p.get("college"),
            }
            for p in players
        ]
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"WNBA player search failed: {e}")


# ── Teams ─────────────────────────────────────────────────────────────────────

@router.get("/teams")
async def get_wnba_teams():
    try:
        teams = await wnba_client.get_teams()
        return [
            {
                "id":           t.get("id"),
                "fullName":     t.get("full_name"),
                "abbreviation": t.get("abbreviation"),
                "city":         t.get("city"),
                "name":         t.get("name"),
                "conference":   t.get("conference"),
            }
            for t in teams
        ]
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"WNBA teams failed: {e}")


# ── Predict ───────────────────────────────────────────────────────────────────

class WnbaPredictRequest(BaseModel):
    playerName:    str
    playerId:      Optional[int]   = None
    teamName:      Optional[str]   = ""
    position:      Optional[str]   = ""
    propType:      str
    line:          float
    opponentName:  Optional[str]   = ""
    venue:         Optional[str]   = "home"
    season:        Optional[int]   = CURRENT_WNBA_SEASON
    oppDefRating:  Optional[float] = None
    restDays:      Optional[int]   = None


@router.post("/predict")
async def wnba_predict(req: WnbaPredictRequest):
    from routes.auth import verify_session
    sess = await verify_session(req)
    if not sess.get("valid"):
        raise HTTPException(status_code=401, detail="Invalid or expired session. Please sign in again.")
    access = sess.get("access_type", "")
    if not access or access == "NoSubscription":
        raise HTTPException(status_code=403, detail="Active subscription required")
    prop_type = req.propType.lower().strip()
    venue     = (req.venue or "home").lower()
    if venue not in ("home", "away"):
        venue = "home"

    valid_props = set(wnba_engine.WNBA_PROPS.keys())
    if prop_type not in valid_props:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown WNBA prop: {prop_type}. Valid: {sorted(valid_props)}"
        )

    if req.line <= 0:
        raise HTTPException(status_code=400, detail="Line must be positive.")

    # ── Resolve player ────────────────────────────────────────────────────────
    player_id   = req.playerId
    player_data = None
    position    = req.position or ""
    team_name   = req.teamName or ""

    if player_id:
        player_data = await wnba_client.get_player(player_id)

    if not player_data and req.playerName:
        results = await wnba_client.search_players(req.playerName, limit=5)
        if results:
            best        = results[0]
            player_id   = best.get("id")
            player_data = await wnba_client.get_player(player_id) or best

    if player_data:
        position  = position or player_data.get("position", "")
        team_name = team_name or (player_data.get("team") or {}).get("full_name", "")

    if not player_id:
        raise HTTPException(status_code=404, detail=f"Player '{req.playerName}' not found in WNBA database.")

    log.info(f"[WNBA PREDICT] {req.playerName} ({player_id}) | {prop_type} {req.line} | {venue}")

    seasons_to_try = [req.season, req.season - 1]
    game_logs = []
    season_avg = {}
    used_season = req.season

    for try_season in seasons_to_try:
        try:
            logs_res, avg_res = await asyncio.gather(
                wnba_client.get_player_game_logs(player_id, try_season),
                wnba_client.get_season_averages(player_id, try_season),
            )
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Failed to fetch WNBA data: {e}")
        if logs_res:
            game_logs  = logs_res
            season_avg = avg_res
            used_season = try_season
            break
        if avg_res:
            season_avg = avg_res

    if not game_logs:
        raise HTTPException(
            status_code=404,
            detail=f"No stats found for {req.playerName} in the {req.season} or {req.season - 1} season."
        )

    result = wnba_engine.compute_wnba_projection(
        game_logs     = game_logs,
        prop_type     = prop_type,
        line          = req.line,
        venue         = venue,
        opp_def_rating = req.oppDefRating,
        rest_days     = req.restDays,
        season_avg    = season_avg,
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
    if result["recommendation"] == "under" and result["projection"] > req.line:
        result["projection"] = round(req.line - 0.5, 1)
    elif result["recommendation"] == "over" and result["projection"] < req.line:
        result["projection"] = round(req.line + 0.5, 1)

    field = wnba_engine.WNBA_PROPS.get(prop_type, prop_type)
    game_log_tiles = []
    for g in game_logs[:10]:
        game_log_tiles.append({
            "date":     g.get("date", ""),
            "value":    g.get(field),
            "venue":    g.get("venue", ""),
            "opponent": g.get("opponent", ""),
            "pts":      g.get("pts"),
            "reb":      g.get("reb"),
            "ast":      g.get("ast"),
        })

    ai_result = {"sharpSummary": "", "tacticalBreakdown": "", "reasoning": ""}

    return {
        "sport":            "wnba",
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
