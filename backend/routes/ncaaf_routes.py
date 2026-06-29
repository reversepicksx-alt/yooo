"""
NCAAF prediction routes — /api/ncaaf/*
"""
import asyncio
import logging
from typing import Optional
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from config import db, XAI_API_KEY
import ncaaf_client
import ncaaf_engine

log = logging.getLogger("ncaaf_routes")
router = APIRouter(prefix="/api/ncaaf", tags=["ncaaf"])

CURRENT_NCAAF_SEASON = ncaaf_client.CURRENT_NCAAF_SEASON


async def _get_ai_analysis(
    player_name: str, prop_type: str, line: float, venue: str,
    opponent: str, projection: float, p_over: float, p_under: float,
    recommendation: str, game_logs: list, prior_mean: float, streak_flag: str,
) -> dict:
    try:
        from grok_engine import _grok_call
        prop_label = ncaaf_engine.PROP_LABELS.get(prop_type, prop_type.replace("_", " ").title())
        conf = round(max(p_over, p_under))
        ctx_lines = []
        for i, g in enumerate(game_logs[:6]):
            field = ncaaf_engine.NCAAF_PROPS.get(prop_type, prop_type)
            val = g.get(field, "?")
            ctx_lines.append(f"  G{i+1} ({g.get('date','?')}, {g.get('venue','?')}): {val} {prop_label}")
        game_ctx = "\n".join(ctx_lines) or "  (no recent data)"
        prompt = f"""You are a sharp college football prop betting analyst.

PLAYER: {player_name} | PROP: {prop_label} {line} | VENUE: {venue.upper()} vs {opponent or 'opponent'}
PROJECTION: {projection} | P(OVER)={p_over}% | P(UNDER)={p_under}%
RECOMMENDATION: {recommendation.upper()} ({conf}% confidence)

RECENT GAME LOG:
{game_ctx}

Write a sharp 2-3 sentence analysis focusing on matchup, role, and volume. Be direct and confident."""
        text = (await _grok_call(prompt, temperature=0.7, max_tokens=1500, timeout=30) or "").strip()
        return {"sharpSummary": text[:600], "tacticalBreakdown": text,
                "reasoning": f"Bayesian: {projection} | P(OVER)={p_over}% P(UNDER)={p_under}%"}
    except Exception as e:
        log.warning(f"[NCAAF AI] {e}")
        return {"sharpSummary": "", "tacticalBreakdown": "", "reasoning": ""}


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

    ai_task = asyncio.create_task(_get_ai_analysis(
        player_name=req.playerName, prop_type=prop_type, line=req.line,
        venue=venue, opponent=req.opponentName or "",
        projection=result["projection"], p_over=p_over, p_under=p_under,
        recommendation=result["recommendation"], game_logs=game_logs,
        prior_mean=result["priorMean"], streak_flag=result["streakFlag"],
    ))

    prop_field = ncaaf_engine.NCAAF_PROPS.get(prop_type, prop_type)
    game_log_tiles = [{"date": g.get("date",""), "value": g.get(prop_field),
                       "venue": g.get("venue",""), "minutes": g.get("minutes")}
                      for g in game_logs[:10]]

    try:
        ai_result = await asyncio.wait_for(ai_task, timeout=25.0)
    except Exception:
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
