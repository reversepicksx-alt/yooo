"""
PGA Tour prediction routes — /api/pga/*
"""
import asyncio
import logging
from typing import Optional
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from config import XAI_API_KEY
import pga_client
import pga_engine

log = logging.getLogger("pga_routes")
router = APIRouter(prefix="/api/pga", tags=["pga"])

CURRENT_PGA_SEASON = pga_client.CURRENT_PGA_SEASON


async def _get_ai_analysis(
    player_name: str, prop_type: str, line: float, tournament: str,
    projection: float, p_over: float, p_under: float,
    recommendation: str, round_logs: list,
) -> dict:
    try:
        from grok_engine import _grok_call
        prop_label = pga_engine.PROP_LABELS.get(prop_type, prop_type.replace("_", " ").title())
        conf = round(max(p_over, p_under))
        ctx_lines = []
        for i, r in enumerate(round_logs[:5]):
            val = r.get(pga_engine.PGA_PROPS.get(prop_type, prop_type), "?")
            ctx_lines.append(
                f"  R{i+1} ({r.get('date','?')}, {r.get('tournament','?')}): "
                f"{val} {prop_label}, pos={r.get('finish_pos','?')}, birdies={r.get('birdies','?')}"
            )
        ctx = "\n".join(ctx_lines) or "  (no recent data)"
        prompt = f"""You are a sharp PGA Tour prop betting analyst.

GOLFER: {player_name} | PROP: {prop_label} | LINE: {line}
TOURNAMENT: {tournament or 'upcoming event'}
PROJECTION: {projection} | P(OVER)={p_over}% | P(UNDER)={p_under}%
RECOMMENDATION: {recommendation.upper()} ({conf}% confidence)

RECENT ROUND LOG:
{ctx}

Write a sharp 2-3 sentence analysis focusing on course fit, current form, and stat trends. Be direct."""
        text = (await _grok_call(prompt, temperature=0.7, max_tokens=1500, timeout=30) or "").strip()
        return {"sharpSummary": text[:600], "tacticalBreakdown": text,
                "reasoning": f"Bayesian: {projection} | P(OVER)={p_over}% P(UNDER)={p_under}%"}
    except Exception as e:
        log.warning(f"[PGA AI] {e}")
        return {"sharpSummary": "", "tacticalBreakdown": "", "reasoning": ""}


@router.get("/players/search")
async def search_pga_players(q: str = Query("", min_length=2), limit: int = Query(15)):
    try:
        players = await pga_client.search_players(q, limit=limit)
        return [{"id": p.get("id"),
                 "fullName": p.get("name") or p.get("full_name") or f"{p.get('first_name','')} {p.get('last_name','')}".strip(),
                 "country": p.get("country") or p.get("nationality", ""),
                 "worldRanking": p.get("world_ranking") or p.get("ranking"),
                 "team": {}} for p in players]
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"PGA player search failed: {e}")


class PgaPredictRequest(BaseModel):
    playerName:   str
    playerId:     Optional[int]  = None
    propType:     str
    line:         float
    tournament:   Optional[str]  = ""
    season:       Optional[int]  = CURRENT_PGA_SEASON


@router.post("/predict")
async def pga_predict(req: PgaPredictRequest):
    from routes.auth import verify_session
    sess = await verify_session(req)
    if not sess.get("valid"):
        raise HTTPException(status_code=401, detail="Invalid or expired session. Please sign in again.")
    access = sess.get("access_type", "")
    if not access or access == "NoSubscription":
        raise HTTPException(status_code=403, detail="Active subscription required")
    prop_type = req.propType.lower().strip()
    if prop_type not in pga_engine.PGA_PROPS:
        raise HTTPException(status_code=400, detail=f"Unknown PGA prop: {prop_type}")
    if req.line <= 0:
        raise HTTPException(status_code=400, detail="Line must be positive.")

    player_id = req.playerId
    player_data = None

    if player_id:
        player_data = await pga_client.get_player(player_id)
    if not player_data and req.playerName:
        results = await pga_client.search_players(req.playerName, limit=5)
        if results:
            best = results[0]
            player_id = best.get("id")
            player_data = await pga_client.get_player(player_id) or best

    if not player_id:
        raise HTTPException(status_code=404, detail=f"Golfer '{req.playerName}' not found in PGA database.")

    try:
        round_logs = await pga_client.get_player_round_logs(player_id, req.season)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch PGA data: {e}")

    if not round_logs:
        raise HTTPException(status_code=404,
                            detail=f"No round data found for {req.playerName} in {req.season} season.")

    result = pga_engine.compute_pga_projection(
        round_logs=round_logs, prop_type=prop_type, line=req.line,
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
        tournament=req.tournament or "",
        projection=result["projection"], p_over=p_over, p_under=p_under,
        recommendation=result["recommendation"], round_logs=round_logs,
    ))

    prop_field = pga_engine.PGA_PROPS.get(prop_type, prop_type)
    game_log_tiles = [{"date": g.get("date",""), "value": g.get(prop_field),
                       "tournament": g.get("tournament",""), "finish_pos": g.get("finish_pos"),
                       "birdies": g.get("birdies"), "putts": g.get("putts")}
                      for g in round_logs[:10]]

    try:
        ai_result = await asyncio.wait_for(ai_task, timeout=25.0)
    except Exception:
        ai_result = {"sharpSummary": "", "tacticalBreakdown": "", "reasoning": ""}

    return {
        "sport": "pga", "playerName": req.playerName, "playerId": player_id,
        "teamName": "", "propType": prop_type,
        "line": req.line, "venue": "neutral", "opponentName": req.tournament or "",
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
