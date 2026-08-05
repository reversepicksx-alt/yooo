"""
Formula 1 prediction routes — /api/f1/*
"""
import logging
from typing import Optional
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

import f1_client
import f1_engine

log = logging.getLogger("f1_routes")
router = APIRouter(prefix="/api/f1", tags=["f1"])

CURRENT_F1_SEASON = f1_client.CURRENT_F1_SEASON


@router.get("/drivers/search")
async def search_f1_drivers(q: str = Query("", min_length=2), limit: int = Query(15)):
    try:
        drivers = await f1_client.search_drivers(q, limit=limit)
        return [{"id": d.get("id"),
                 "fullName": d.get("name") or d.get("full_name") or f"{d.get('first_name','')} {d.get('last_name','')}".strip(),
                 "team": d.get("team") or d.get("constructor") or {},
                 "nationality": d.get("nationality") or d.get("country", ""),
                 "number": d.get("number") or d.get("car_number")} for d in drivers]
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"F1 driver search failed: {e}")


class F1PredictRequest(BaseModel):
    playerName:   str
    playerId:     Optional[int]  = None
    teamName:     Optional[str]  = ""
    propType:     str
    line:         float
    raceName:     Optional[str]  = ""
    season:       Optional[int]  = CURRENT_F1_SEASON


@router.post("/predict")
async def f1_predict(req: F1PredictRequest):
    from routes.auth import verify_session
    sess = await verify_session(req)
    if not sess.get("valid"):
        raise HTTPException(status_code=401, detail="Invalid or expired session. Please sign in again.")
    access = sess.get("access_type", "")
    if not access or access == "NoSubscription":
        raise HTTPException(status_code=403, detail="Active subscription required")
    prop_type = req.propType.lower().strip()
    if prop_type not in f1_engine.F1_PROPS:
        raise HTTPException(status_code=400, detail=f"Unknown F1 prop: {prop_type}")
    if req.line <= 0:
        raise HTTPException(status_code=400, detail="Line must be positive.")

    driver_id = req.playerId
    driver_data = None
    team_name = req.teamName or ""

    if driver_id:
        driver_data = await f1_client.get_driver(driver_id)
    if not driver_data and req.playerName:
        results = await f1_client.search_drivers(req.playerName, limit=5)
        if results:
            best = results[0]
            driver_id = best.get("id")
            driver_data = await f1_client.get_driver(driver_id) or best

    if driver_data:
        team_name = team_name or (driver_data.get("team") or {}).get("name", "") or str(driver_data.get("team", ""))

    if not driver_id:
        raise HTTPException(status_code=404, detail=f"Driver '{req.playerName}' not found in F1 database.")

    try:
        race_logs = await f1_client.get_driver_race_logs(driver_id, req.season)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch F1 data: {e}")

    if not race_logs:
        raise HTTPException(status_code=404,
                            detail=f"No race data found for {req.playerName} in {req.season} season.")

    result = f1_engine.compute_f1_projection(
        race_logs=race_logs, prop_type=prop_type, line=req.line,
    )
    if "error" in result:
        raise HTTPException(status_code=422, detail=result["error"])

    p_over = result["pOver"]
    p_under = result["pUnder"]
    result["recommendation"] = "over" if p_over >= p_under else "under"
    result["confidenceScore"] = round(max(p_over, p_under))
    conf = result["confidenceScore"]
    result["confidenceLevel"] = "High" if conf >= 70 else "Medium" if conf >= 60 else "Low"

    game_log_tiles = [{"date": g.get("date",""), "value": g.get(f1_engine.F1_PROPS.get(prop_type, prop_type)),
                       "race": g.get("race",""), "grid_pos": g.get("grid_pos"), "points": g.get("points")}
                      for g in race_logs[:10]]

    ai_result = {"sharpSummary": "", "tacticalBreakdown": "", "reasoning": ""}

    return {
        "sport": "f1", "playerName": req.playerName, "playerId": driver_id,
        "teamName": team_name, "propType": prop_type,
        "line": req.line, "venue": "neutral", "opponentName": req.raceName or "",
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
