"""
ATP Tennis prediction routes — /api/atp/*
Mirrors WTA routes for men's ATP tour.
"""
import asyncio
import logging
from typing import Optional
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from config import GEMINI_API_KEY
import atp_client
import atp_engine

log = logging.getLogger("atp_routes")
router = APIRouter(prefix="/api/atp", tags=["atp"])

ATP_SURFACES = ["Hard", "Clay", "Grass", "Indoor Hard"]
ATP_ROUNDS   = ["F", "SF", "QF", "R16", "R32", "R64", "R128", "RR"]


async def _get_atp_ai_analysis(
    player_name: str, opponent: str, prop_type: str, line: float,
    projection: float, p_over: float, p_under: float, recommendation: str,
    match_logs: list, surface: Optional[str], round_name: Optional[str],
    opp_rank: Optional[int], subject_rank: Optional[int],
) -> dict:
    try:
        import google.generativeai as genai
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel("gemini-2.5-flash")
        prop_label = atp_engine.PROP_LABELS.get(prop_type, prop_type.replace("_", " ").title())
        conf = round(max(p_over, p_under))
        ctx_lines = []
        for i, m in enumerate(match_logs[:5]):
            result = "W" if m.get("wonMatch") else "L"
            ctx_lines.append(
                f"  Match {i+1} ({m.get('date','?')}, {m.get('surface','?')}, "
                f"{m.get('round','?')}, {result} vs {m.get('opponent','?')}): "
                f"{m.get('playerGamesWon','?')}-{m.get('opponentGamesWon','?')} games"
            )
        game_ctx = "\n".join(ctx_lines) or "  (no recent data)"
        prompt = f"""You are a sharp ATP Tour tennis prop betting analyst.

PLAYER: {player_name} (Rank #{subject_rank or '?'}) vs {opponent or 'opponent'} (Rank #{opp_rank or '?'})
PROP: {prop_label} | LINE: {line} | SURFACE: {surface or 'Hard'} | ROUND: {round_name or '?'}
PROJECTION: {projection} | P(OVER)={p_over}% | P(UNDER)={p_under}%
RECOMMENDATION: {recommendation.upper()} ({conf}% confidence)

RECENT MATCH LOG:
{game_ctx}

Write a sharp 2-3 sentence analysis. Focus on surface, form, and head-to-head dynamics. Be direct."""
        resp = await asyncio.to_thread(model.generate_content, prompt)
        text = (resp.text or "").strip()
        return {"sharpSummary": text[:600], "tacticalBreakdown": text,
                "reasoning": f"Bayesian: {projection} | P(OVER)={p_over}% P(UNDER)={p_under}%"}
    except Exception as e:
        log.warning(f"[ATP AI] {e}")
        return {"sharpSummary": "", "tacticalBreakdown": "", "reasoning": ""}


@router.get("/players/search")
async def search_atp_players(q: str = Query("", min_length=2), limit: int = Query(15)):
    try:
        players = await atp_client.search_players(q, limit=limit)
        return [{"id": p.get("id"),
                 "fullName": p.get("full_name") or p.get("name") or f"{p.get('first_name','')} {p.get('last_name','')}".strip(),
                 "firstName": p.get("first_name"), "lastName": p.get("last_name"),
                 "ranking": p.get("ranking"), "country": p.get("country")}
                for p in players]
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"ATP player search failed: {e}")


class AtpPredictRequest(BaseModel):
    playerName:    str
    playerId:      Optional[int]  = None
    opponentName:  Optional[str]  = ""
    opponentId:    Optional[int]  = None
    propType:      str
    line:          float
    surface:       Optional[str]  = "Hard"
    round:         Optional[str]  = None
    restDays:      Optional[int]  = None
    subjectRank:   Optional[int]  = None
    opponentRank:  Optional[int]  = None


@router.post("/predict")
async def atp_predict(req: AtpPredictRequest):
    prop_type = req.propType.lower().strip()
    if prop_type not in atp_engine.ATP_PROPS:
        raise HTTPException(status_code=400, detail=f"Unknown ATP prop: {prop_type}")
    if req.line <= 0:
        raise HTTPException(status_code=400, detail="Line must be positive.")

    player_id = req.playerId
    subject_rank = req.subjectRank
    opp_rank = req.opponentRank
    opp_id = req.opponentId

    if not player_id and req.playerName:
        results = await atp_client.search_players(req.playerName, limit=3)
        if results:
            player_id = results[0].get("id")
            subject_rank = subject_rank or results[0].get("ranking")

    if not player_id:
        raise HTTPException(status_code=404, detail=f"Player '{req.playerName}' not found in ATP database.")

    if not opp_id and req.opponentName:
        opp_results = await atp_client.search_players(req.opponentName, limit=3)
        if opp_results:
            opp_id = opp_results[0].get("id")
            opp_rank = opp_rank or opp_results[0].get("ranking")

    try:
        tasks = [atp_client.get_player_match_logs(player_id, limit=30)]
        if opp_id:
            tasks.append(atp_client.get_h2h(player_id, opp_id))
        if not subject_rank:
            tasks.append(atp_client.get_player_ranking(player_id))
        if not opp_rank and opp_id:
            tasks.append(atp_client.get_player_ranking(opp_id))
        results_gathered = await asyncio.gather(*tasks, return_exceptions=True)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"ATP data fetch failed: {e}")

    match_logs = results_gathered[0] if not isinstance(results_gathered[0], Exception) else []
    h2h = None
    if len(results_gathered) > 1 and not isinstance(results_gathered[1], Exception):
        h2h = results_gathered[1]

    if not match_logs:
        raise HTTPException(status_code=404, detail=f"No ATP match logs found for {req.playerName}.")

    # Filter by surface if requested
    surface_logs = [m for m in match_logs if m.get("surface", "").lower() == (req.surface or "Hard").lower()]
    engine_logs = surface_logs if len(surface_logs) >= 4 else match_logs

    result = atp_engine.compute_atp_projection(
        match_logs=engine_logs, prop_type=prop_type, line=req.line,
        surface=req.surface, round_name=req.round,
        opp_rank=opp_rank, subject_rank=subject_rank,
        h2h=h2h, rest_days=req.restDays,
    )
    if "error" in result:
        raise HTTPException(status_code=422, detail=result["error"])

    p_over = result["pOver"]
    p_under = result["pUnder"]
    result["recommendation"] = "over" if p_over >= p_under else "under"
    result["confidenceScore"] = round(max(p_over, p_under))
    conf = result["confidenceScore"]
    result["confidenceLevel"] = "High" if conf >= 70 else "Medium" if conf >= 60 else "Low"
    if result["recommendation"] == "under" and result["projection"] > req.line:
        result["projection"] = round(req.line - 0.5, 1)
    elif result["recommendation"] == "over" and result["projection"] < req.line:
        result["projection"] = round(req.line + 0.5, 1)

    ai_task = asyncio.create_task(_get_atp_ai_analysis(
        player_name=req.playerName, opponent=req.opponentName or "",
        prop_type=prop_type, line=req.line, projection=result["projection"],
        p_over=p_over, p_under=p_under, recommendation=result["recommendation"],
        match_logs=match_logs[:6], surface=req.surface, round_name=req.round,
        opp_rank=opp_rank, subject_rank=subject_rank,
    ))

    try:
        ai_result = await asyncio.wait_for(ai_task, timeout=25.0)
    except Exception:
        ai_result = {"sharpSummary": "", "tacticalBreakdown": "", "reasoning": ""}

    field = atp_engine.ATP_PROPS.get(prop_type, prop_type)
    game_log_tiles = [{"date": m.get("date",""), "venue": "", "value": m.get(field),
                       "surface": m.get("surface",""), "round": m.get("round",""),
                       "opponent": m.get("opponent",""), "wonMatch": m.get("wonMatch")}
                      for m in match_logs[:10]]

    return {
        "sport": "atp", "playerName": req.playerName, "playerId": player_id,
        "opponentName": req.opponentName or "", "propType": prop_type,
        "line": req.line, "surface": req.surface or "Hard", "round": req.round or "",
        "projection": result["projection"], "pOver": p_over, "pUnder": p_under,
        "recommendation": result["recommendation"], "confidenceScore": result["confidenceScore"],
        "confidenceLevel": result["confidenceLevel"], "priorMean": result["priorMean"],
        "momentum": result["momentum"], "sampleSize": result["sampleSize"],
        "streakFlag": result["streakFlag"], "gameLogs": game_log_tiles,
        "subjectRank": subject_rank, "opponentRank": opp_rank,
        "h2h": h2h,
        "sharpSummary": ai_result.get("sharpSummary", ""),
        "tacticalBreakdown": ai_result.get("tacticalBreakdown", ""),
        "reasoning": ai_result.get("reasoning", ""),
        "rawConfidence": result["confidenceScore"],
    }
