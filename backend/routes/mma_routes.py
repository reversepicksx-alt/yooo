"""
MMA prediction routes — /api/mma/*
"""
import logging
from typing import Optional
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

import mma_client
import mma_engine

log = logging.getLogger("mma_routes")
router = APIRouter(prefix="/api/mma", tags=["mma"])


@router.get("/fighters/search")
async def search_mma_fighters(q: str = Query("", min_length=2), limit: int = Query(15)):
    try:
        fighters = await mma_client.search_fighters(q, limit=limit)
        return [{"id": f.get("id"),
                 "fullName": f.get("name") or f.get("full_name") or f"{f.get('first_name','')} {f.get('last_name','')}".strip(),
                 "nickname": f.get("nickname", ""),
                 "weightClass": f.get("weight_class") or f.get("division", ""),
                 "record": f.get("record") or f"{f.get('wins',0)}-{f.get('losses',0)}-{f.get('draws',0)}",
                 "team": f.get("team") or f.get("gym", "")} for f in fighters]
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"MMA fighter search failed: {e}")


class MmaPredictRequest(BaseModel):
    playerName:   str
    playerId:     Optional[int]  = None
    teamName:     Optional[str]  = ""
    opponentName: Optional[str]  = ""
    propType:     str
    line:         float
    eventName:    Optional[str]  = ""


@router.post("/predict")
async def mma_predict(req: MmaPredictRequest):
    from routes.auth import verify_session
    sess = await verify_session(req)
    if not sess.get("valid"):
        raise HTTPException(status_code=401, detail="Invalid or expired session. Please sign in again.")
    access = sess.get("access_type", "")
    if not access or access == "NoSubscription":
        raise HTTPException(status_code=403, detail="Active subscription required")
    prop_type = req.propType.lower().strip()
    if prop_type not in mma_engine.MMA_PROPS:
        raise HTTPException(status_code=400, detail=f"Unknown MMA prop: {prop_type}")
    if req.line <= 0:
        raise HTTPException(status_code=400, detail="Line must be positive.")

    fighter_id = req.playerId
    fighter_data = None
    team_name = req.teamName or ""

    if fighter_id:
        fighter_data = await mma_client.get_fighter(fighter_id)
    if not fighter_data and req.playerName:
        results = await mma_client.search_fighters(req.playerName, limit=5)
        if results:
            best = results[0]
            fighter_id = best.get("id")
            fighter_data = await mma_client.get_fighter(fighter_id) or best

    if fighter_data:
        team_name = team_name or fighter_data.get("team") or fighter_data.get("gym", "")

    if not fighter_id:
        raise HTTPException(status_code=404, detail=f"Fighter '{req.playerName}' not found in MMA database.")

    try:
        fight_logs = await mma_client.get_fighter_fight_logs(fighter_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch MMA data: {e}")

    if not fight_logs:
        raise HTTPException(status_code=404,
                            detail=f"No fight data found for {req.playerName}.")

    result = mma_engine.compute_mma_projection(
        fight_logs=fight_logs, prop_type=prop_type, line=req.line,
    )
    if "error" in result:
        raise HTTPException(status_code=422, detail=result["error"])

    p_over = result["pOver"]
    p_under = result["pUnder"]
    result["recommendation"] = "over" if p_over >= p_under else "under"
    result["confidenceScore"] = round(max(p_over, p_under))
    conf = result["confidenceScore"]
    result["confidenceLevel"] = "High" if conf >= 70 else "Medium" if conf >= 60 else "Low"

    prop_field = mma_engine.MMA_PROPS.get(prop_type, prop_type)
    game_log_tiles = [{"date": g.get("date",""), "value": g.get(prop_field),
                       "opponent": g.get("opponent",""), "won": g.get("won"),
                       "method": g.get("method"), "round": g.get("round")}
                      for g in fight_logs[:10]]

    ai_result = {"sharpSummary": "", "tacticalBreakdown": "", "reasoning": ""}

    return {
        "sport": "mma", "playerName": req.playerName, "playerId": fighter_id,
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
