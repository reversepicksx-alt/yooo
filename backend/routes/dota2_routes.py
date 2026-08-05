"""
Dota 2 prediction routes — /api/dota2/*
"""
import logging
from typing import Optional
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

import dota2_client
import dota2_engine

log = logging.getLogger("dota2_routes")
router = APIRouter(prefix="/api/dota2", tags=["dota2"])


@router.get("/players/search")
async def search_dota2_players(q: str = Query("", min_length=2), limit: int = Query(15)):
    try:
        players = await dota2_client.search_players(q, limit=limit)
        return [{"id": p.get("id"),
                 "fullName": p.get("name") or p.get("nickname") or p.get("full_name") or
                             f"{p.get('first_name','')} {p.get('last_name','')}".strip(),
                 "nickname": p.get("nickname", ""),
                 "team": p.get("team") or {},
                 "country": p.get("country") or p.get("nationality", "")} for p in players]
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Dota2 player search failed: {e}")


class Dota2PredictRequest(BaseModel):
    playerName:   str
    playerId:     Optional[int]  = None
    teamName:     Optional[str]  = ""
    opponentName: Optional[str]  = ""
    propType:     str
    line:         float
    tournament:   Optional[str]  = ""


@router.post("/predict")
async def dota2_predict(req: Dota2PredictRequest):
    from routes.auth import verify_session
    sess = await verify_session(req)
    if not sess.get("valid"):
        raise HTTPException(status_code=401, detail="Invalid or expired session. Please sign in again.")
    access = sess.get("access_type", "")
    if not access or access == "NoSubscription":
        raise HTTPException(status_code=403, detail="Active subscription required")
    prop_type = req.propType.lower().strip()
    if prop_type not in dota2_engine.DOTA2_PROPS:
        raise HTTPException(status_code=400, detail=f"Unknown Dota2 prop: {prop_type}")
    if req.line <= 0:
        raise HTTPException(status_code=400, detail="Line must be positive.")

    player_id = req.playerId
    player_data = None
    team_name = req.teamName or ""

    if player_id:
        player_data = await dota2_client.get_player(player_id)
    if not player_data and req.playerName:
        results = await dota2_client.search_players(req.playerName, limit=5)
        if results:
            best = results[0]
            player_id = best.get("id")
            player_data = await dota2_client.get_player(player_id) or best

    if player_data:
        team_name = team_name or (player_data.get("team") or {}).get("name", "")

    if not player_id:
        raise HTTPException(status_code=404, detail=f"Player '{req.playerName}' not found in Dota2 database.")

    try:
        match_logs = await dota2_client.get_player_match_logs(player_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch Dota2 data: {e}")

    if not match_logs:
        raise HTTPException(status_code=404, detail=f"No match data found for {req.playerName}.")

    result = dota2_engine.compute_dota2_projection(
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

    prop_field = dota2_engine.DOTA2_PROPS.get(prop_type, prop_type)
    game_log_tiles = [{"date": g.get("date",""), "value": g.get(prop_field),
                       "hero": g.get("hero",""), "won": g.get("won"),
                       "kills": g.get("kills"), "deaths": g.get("deaths"), "assists": g.get("assists")}
                      for g in match_logs[:10]]

    ai_result = {"sharpSummary": "", "tacticalBreakdown": "", "reasoning": ""}

    return {
        "sport": "dota2", "playerName": req.playerName, "playerId": player_id,
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
    }
