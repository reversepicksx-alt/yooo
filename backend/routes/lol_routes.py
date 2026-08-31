"""
League of Legends prediction routes — /api/lol/*
"""
import logging
from typing import Optional
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

import lol_client
import lol_engine
from engine_base import normalize_response

log = logging.getLogger("lol_routes")
router = APIRouter(prefix="/api/lol", tags=["lol"])


@router.get("/players/search")
async def search_lol_players(q: str = Query("", min_length=2), limit: int = Query(15)):
    try:
        players = await lol_client.search_players(q, limit=limit)
        return [{"id": p.get("id"),
                 "fullName": p.get("name") or p.get("nickname") or p.get("full_name") or
                             f"{p.get('first_name','')} {p.get('last_name','')}".strip(),
                 "nickname": p.get("nickname", ""),
                 "team": p.get("team") or {},
                 "role": p.get("role") or p.get("position", ""),
                 "country": p.get("country") or p.get("nationality", "")} for p in players]
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"LoL player search failed: {e}")


class LolPredictRequest(BaseModel):
    playerName:   str
    playerId:     Optional[int]  = None
    teamName:     Optional[str]  = ""
    opponentName: Optional[str]  = ""
    propType:     str
    line:         float
    tournament:   Optional[str]  = ""


@router.post("/predict")
async def lol_predict(req: LolPredictRequest):
    from routes.auth import verify_session
    sess = await verify_session(req)
    if not sess.get("valid"):
        raise HTTPException(status_code=401, detail="Invalid or expired session. Please sign in again.")
    access = sess.get("access_type", "")
    if not access or access == "NoSubscription":
        raise HTTPException(status_code=403, detail="Active subscription required")
    prop_type = req.propType.lower().strip()
    if prop_type not in lol_engine.LOL_PROPS:
        raise HTTPException(status_code=400, detail=f"Unknown LoL prop: {prop_type}")
    if req.line <= 0:
        raise HTTPException(status_code=400, detail="Line must be positive.")

    player_id = req.playerId
    player_data = None
    team_name = req.teamName or ""

    if player_id:
        player_data = await lol_client.get_player(player_id)
    if not player_data and req.playerName:
        results = await lol_client.search_players(req.playerName, limit=5)
        if results:
            best = results[0]
            player_id = best.get("id")
            player_data = await lol_client.get_player(player_id) or best

    if player_data:
        team_name = team_name or (player_data.get("team") or {}).get("name", "")

    if not player_id:
        raise HTTPException(status_code=404, detail=f"Player '{req.playerName}' not found in LoL database.")

    try:
        match_logs = await lol_client.get_player_match_logs(player_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch LoL data: {e}")

    if not match_logs:
        raise HTTPException(status_code=404, detail=f"No match data found for {req.playerName}.")

    result = lol_engine.compute_lol_projection(
        match_logs=match_logs, prop_type=prop_type, line=req.line,
    )
    if "error" in result:
        raise HTTPException(status_code=422, detail=result["error"])

    p_over = result["pOver"]
    p_under = result["pUnder"]
    result["recommendation"] = "over" if p_over >= p_under else "under"
    result["confidenceScore"] = round(max(p_over, p_under))
    conf = result["confidenceScore"]
    result["confidenceLevel"] = "High" if conf >= 70 else "Medium" if conf >= 60 else "Low"

    prop_field = lol_engine.LOL_PROPS.get(prop_type, prop_type)
    game_log_tiles = [{"date": g.get("date",""), "value": g.get(prop_field),
                       "champion": g.get("champion",""), "won": g.get("won"),
                       "kills": g.get("kills"), "deaths": g.get("deaths"),
                       "assists": g.get("assists"), "cs": g.get("cs")}
                      for g in match_logs[:10]]

    ai_result = {"sharpSummary": "", "tacticalBreakdown": "", "reasoning": ""}

    return normalize_response({
        "sport": "lol", "playerName": req.playerName, "playerId": player_id,
        "teamName": team_name, "propType": prop_type,
        "line": req.line, "venue": "neutral", "opponentName": req.opponentName or "",
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
    })
