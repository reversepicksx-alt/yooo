"""
NCAAF prediction routes — /api/ncaaf/*
"""
import logging
from typing import Optional
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from config import db
import ncaaf_client
import ncaaf_engine

log = logging.getLogger("ncaaf_routes")
router = APIRouter(prefix="/api/ncaaf", tags=["ncaaf"])

CURRENT_NCAAF_SEASON = ncaaf_client.CURRENT_NCAAF_SEASON


@router.get("/players/search")
async def search_ncaaf_players(q: str = Query("", min_length=2), limit: int = Query(15)):
    try:
        players = await ncaaf_client.search_players(q, limit=limit)
        return [{"id": p.get("id"),
                 "fullName": f"{p.get('first_name','')} {p.get('last_name','')}".strip(),
                 "firstName": p.get("first_name"), "lastName": p.get("last_name"),
                 "position": p.get("position", ""), "team": p.get("team") or {},
                 "college": p.get("college", "")} for p in players]
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"NCAAF player search failed: {e}")


@router.get("/teams")
async def get_ncaaf_teams():
    try:
        teams = await ncaaf_client.get_teams()
        return [{"id": t.get("id"), "fullName": t.get("full_name") or t.get("name"),
                 "abbreviation": t.get("abbreviation"), "conference": t.get("conference")}
                for t in teams]
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"NCAAF teams failed: {e}")


class NcaafPredictRequest(BaseModel):
    playerName:   str
    playerId:     Optional[int]  = None
    teamName:     Optional[str]  = ""
    position:     Optional[str]  = ""
    propType:     str
    line:         float
    opponentName: Optional[str]  = ""
    venue:        Optional[str]  = "home"
    season:       Optional[int]  = CURRENT_NCAAF_SEASON
    restDays:     Optional[int]  = None


@router.post("/predict")
async def ncaaf_predict(req: NcaafPredictRequest):
    from routes.auth import verify_session
    sess = await verify_session(req)
    if not sess.get("valid"):
        raise HTTPException(status_code=401, detail="Invalid or expired session. Please sign in again.")
    access = sess.get("access_type", "")
    if not access or access == "NoSubscription":
        raise HTTPException(status_code=403, detail="Active subscription required")
    prop_type = req.propType.lower().strip()
    venue = (req.venue or "home").lower()
    if venue not in ("home", "away", "neutral"):
        venue = "home"

    if prop_type not in ncaaf_engine.NCAAF_PROPS:
        raise HTTPException(status_code=400, detail=f"Unknown NCAAF prop: {prop_type}")
    if req.line <= 0:
        raise HTTPException(status_code=400, detail="Line must be positive.")

    player_id = req.playerId
    player_data = None
    position = req.position or ""
    team_name = req.teamName or ""

    if player_id:
        player_data = await ncaaf_client.get_player(player_id)
    if not player_data and req.playerName:
        results = await ncaaf_client.search_players(req.playerName, limit=5)
        if results:
            best = results[0]
            player_id = best.get("id")
            player_data = await ncaaf_client.get_player(player_id) or best

    if player_data:
        position = position or player_data.get("position", "")
        team_name = team_name or (player_data.get("team") or {}).get("full_name", "")

    if not player_id:
        raise HTTPException(status_code=404, detail=f"Player '{req.playerName}' not found in NCAAF database.")

    try:
        game_logs = await ncaaf_client.get_player_game_logs(player_id, req.season)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch NCAAF data: {e}")

    if not game_logs:
        raise HTTPException(status_code=404,
                            detail=f"No stats found for {req.playerName} in the {req.season} season.")

    result = ncaaf_engine.compute_ncaaf_projection(
        game_logs=game_logs, prop_type=prop_type, line=req.line,
        venue=venue, rest_days=req.restDays,
    )
    if "error" in result:
        raise HTTPException(status_code=422, detail=result["error"])

    p_over = result["pOver"]
    p_under = result["pUnder"]
    result["recommendation"] = "over" if p_over >= p_under else "under"
    result["confidenceScore"] = round(max(p_over, p_under))
    conf = result["confidenceScore"]
    result["confidenceLevel"] = "High" if conf >= 70 else "Medium" if conf >= 60 else "Low"

    prop_field = ncaaf_engine.NCAAF_PROPS.get(prop_type, prop_type)
    game_log_tiles = [{"date": g.get("date",""), "value": g.get(prop_field),
                       "venue": g.get("venue",""), "minutes": g.get("minutes")}
                      for g in game_logs[:10]]

    ai_result = {"sharpSummary": "", "tacticalBreakdown": "", "reasoning": ""}

    return {
        "sport": "ncaaf", "playerName": req.playerName, "playerId": player_id,
        "teamName": team_name, "position": position, "propType": prop_type,
        "line": req.line, "venue": venue, "opponentName": req.opponentName or "",
        "projection": result["projection"], "pOver": p_over, "pUnder": p_under,
        "recommendation": result["recommendation"], "confidenceScore": result["confidenceScore"],
        "confidenceLevel": result["confidenceLevel"], "priorMean": result["priorMean"],
        "momentum": result["momentum"], "sampleSize": result["sampleSize"],
        "streakFlag": result["streakFlag"], "gameLogs": game_log_tiles,
        "recentValues": result.get("recentValues", []),
        "sharpSummary": ai_result.get("sharpSummary", ""),
        "tacticalBreakdown": ai_result.get("tacticalBreakdown", ""),
        "reasoning": ai_result.get("reasoning", ""),
        "rawConfidence": result["confidenceScore"],
    }
