"""
WTA Tennis prediction routes — /api/wta/*
Mirrors /api/cs2 structure: player search, predict, rankings, h2h, tournaments.
"""
import asyncio
import json
import re
import logging
import datetime as _dt
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from models import WtaPredictRequest

from config import db
import wta_client
import wta_engine

log    = logging.getLogger("wta_routes")
router = APIRouter(prefix="/api/wta", tags=["wta"])


# ── AI analysis helper (Gemini) ────────────────────────────────────────────────

async def _get_wta_ai_analysis(
    player_name: str,
    opponent: str,
    prop_type: str,
    line: float,
    projection: float,
    p_over: float,
    p_under: float,
    recommendation: str,
    match_logs: list,
    prior_mean: float,
    momentum_mean: float,
    streak_flag: str,
    surface: Optional[str],
    round_name: Optional[str],
    opp_rank: Optional[int],
    subject_rank: Optional[int],
    h2h: Optional[dict],
    tactical_metrics: dict,
) -> dict:
    prop_label = wta_engine.PROP_LABELS.get(prop_type, prop_type.replace("_", " ").title())

    ctx_lines = []
    for i, m in enumerate(match_logs[:5]):
        result = "W" if m.get("wonMatch") else "L"
        ctx_lines.append(
            f"  Match {i+1} ({m.get('date','?')}, {m.get('surface','?')}, "
            f"{m.get('round','?')}, {result} vs {m.get('opponent','?')}): "
            f"{m.get('playerGamesWon','?')}-{m.get('opponentGamesWon','?')} games, "
            f"{m.get('setsPlayed','?')} sets"
        )
    game_ctx = "\n".join(ctx_lines) or "  (no recent data)"

    tm_lines = []
    if tactical_metrics.get("surfaceMult") and tactical_metrics["surfaceMult"] != 1.0:
        direction = "favorable" if tactical_metrics["surfaceMult"] > 1.0 else "tougher"
        tm_lines.append(f"• Surface adj ({surface}): {tactical_metrics['surfaceMult']:.2f}× ({direction})")
    if tactical_metrics.get("roundMult") and tactical_metrics["roundMult"] != 1.0:
        tm_lines.append(f"• Round adj ({round_name}): {tactical_metrics['roundMult']:.2f}×")
    if tactical_metrics.get("oppRankMult") and tactical_metrics["oppRankMult"] != 1.0:
        tm_lines.append(f"• Opp rank adj: {tactical_metrics['oppRankMult']:.2f}×")
    if tactical_metrics.get("h2hMult") and tactical_metrics["h2hMult"] != 1.0:
        tm_lines.append(f"• H2H adj: {tactical_metrics['h2hMult']:.2f}×")
    if (h2h or {}).get("p1Wins") or (h2h or {}).get("p2Wins"):
        tm_lines.append(f"• H2H record: {tactical_metrics.get('h2hWins',0)}–{tactical_metrics.get('h2hLosses',0)}")
    if streak_flag:
        tm_lines.append(f"• Form: {streak_flag.upper()}")
    if subject_rank and opp_rank:
        tm_lines.append(f"• Rankings: #{subject_rank} vs #{opp_rank}")
    tactical_ctx = "\n".join(tm_lines) if tm_lines else "  (standard)"

    prompt = f"""You are a sharp WTA tennis betting analyst.

Player: {player_name}{' (#' + str(subject_rank) + ')' if subject_rank else ''}
Opponent: {opponent or 'TBD'}{' (#' + str(opp_rank) + ')' if opp_rank else ''}
Tournament context: surface={surface or 'TBD'}, round={round_name or 'TBD'}
Prop: {prop_label} | Line: {line}
Season avg: {prior_mean:.1f} | Recent (last 5): {momentum_mean:.1f}
Model projection: {projection} → {recommendation.upper()} (P(OVER)={p_over}%, P(UNDER)={p_under}%)

Tactical factors:
{tactical_ctx}

Recent match log (newest first):
{game_ctx}

You are explaining WHY the model's verdict is correct — not reaching your own conclusion. The math has already decided: {recommendation.upper()} {projection} vs {line} line.

Return JSON ONLY (no markdown outside the JSON values):
{{
  "sharpSummary": "<1 decisive sentence under 22 words committing to {recommendation.upper()} — state the single biggest factor>",
  "reasoning": "<2-3 sharp sentences with specific numbers from the data above>",
  "tacticalBreakdown": "## Model Verdict\\n**{recommendation.upper()} {projection}** vs {line} line — P(OVER)={p_over}%, P(UNDER)={p_under}%\\n\\n## Surface & Tournament Context\\n<2-3 sentences: surface multiplier, round difficulty, tournament tier — use specific adjustment values if available>\\n\\n## Matchup Analysis\\n<2-3 sentences: ranking gap #{subject_rank or '?'} vs #{opp_rank or '?'}, H2H record, playing style clash on this surface>\\n\\n## Recent Form\\n<2 sentences: summarise last 4 matches with game totals and set scores to support the direction>\\n\\n## Key Risk\\n<1-2 sentences: main factor that could invalidate this pick — scheduling, fitness, surface mismatch — and why the model still favours the stated direction>"
}}"""

    try:
        from ai_engine import _ai_call as _wta_ai
        raw = await _wta_ai(prompt, temperature=0.35, max_tokens=1400, timeout=18, json_mode=True)
        if raw:
            m = re.search(r'\{[\s\S]*\}', raw)
            if m:
                return json.loads(m.group(0))
    except Exception as e:
        log.warning(f"[WTA AI] Gemini failed: {e}")
    return {}


# ── Player search ─────────────────────────────────────────────────────────────

@router.get("/players/search")
async def search_players(q: str = Query(..., min_length=2)):
    try:
        players = await wta_client.search_players(q)
        return players[:15]
    except Exception as e:
        log.error(f"WTA player search error: {e}")
        return []


# ── Rankings ──────────────────────────────────────────────────────────────────

@router.get("/rankings")
async def get_rankings(limit: int = 50):
    try:
        return await wta_client.get_rankings(limit)
    except Exception as e:
        log.error(f"WTA rankings error: {e}")
        return []


# ── Tournaments ───────────────────────────────────────────────────────────────

@router.get("/tournaments")
async def list_tournaments(season: Optional[int] = None):
    try:
        return await wta_client.list_tournaments(season)
    except Exception as e:
        log.error(f"WTA tournaments error: {e}")
        return []


# ── Head-to-head ──────────────────────────────────────────────────────────────

@router.get("/h2h")
async def head_to_head(p1: int, p2: int):
    try:
        return await wta_client.get_head_to_head(p1, p2)
    except Exception as e:
        log.error(f"WTA h2h error: {e}")
        return {"p1Wins": 0, "p2Wins": 0, "matches": []}


# ── Next match ────────────────────────────────────────────────────────────────

@router.get("/next-match")
async def wta_next_match(playerId: Optional[int] = None):
    """Return the next upcoming match for a WTA player."""
    try:
        if not playerId:
            return {"found": False}
        return await wta_client.get_player_next_match(player_id=playerId)
    except Exception as e:
        log.error(f"WTA next-match error: {e}")
        return {"found": False}


# ── Predict ───────────────────────────────────────────────────────────────────

@router.post("/predict")
async def wta_predict(req: WtaPredictRequest):
    from routes.auth import verify_session
    sess = await verify_session(req)
    if not sess.get("valid"):
        raise HTTPException(status_code=401, detail="Invalid or expired session. Please sign in again.")
    access = sess.get("access_type", "")
    if not access or access == "NoSubscription":
        raise HTTPException(status_code=403, detail="Active subscription required")
    prop_type = req.propType.lower().strip()
    if prop_type not in wta_engine.WTA_PROPS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown WTA prop: {prop_type}. Valid: {sorted(wta_engine.WTA_PROPS)}",
        )
    if req.line < 0:
        raise HTTPException(status_code=400, detail="Line must be non-negative.")

    # Resolve player
    player_id    = req.playerId
    player_name  = (req.playerName or "").strip()
    opponent_id  = req.opponentId
    opponent_name = (req.opponentName or "").strip()
    subject_rank = req.subjectRank
    opp_rank     = req.opponentRank

    if not player_id:
        results = await wta_client.search_players(player_name)
        if not results:
            raise HTTPException(status_code=404, detail=f"Player '{player_name}' not found in WTA database.")
        best = None
        ql = player_name.lower()
        for p in results:
            if p.get("fullName", "").lower() == ql:
                best = p
                break
        if not best:
            best = results[0]
        player_id   = best["id"]
        player_name = best.get("fullName") or player_name
        if not subject_rank:
            subject_rank = best.get("currentRank")

    if not opponent_id and opponent_name:
        opps = await wta_client.search_players(opponent_name)
        if opps:
            opponent_id = opps[0]["id"]
            if not opp_rank:
                opp_rank = opps[0].get("currentRank")
            if not opponent_name:
                opponent_name = opps[0].get("fullName", "")

    log.info(f"[WTA PREDICT] {player_name}({player_id}) vs {opponent_name or '?'}({opponent_id}) | "
             f"{prop_type} {req.line} | {req.surface or '?'} {req.round or '?'}")

    # Fetch recent matches
    try:
        match_logs = await wta_client.get_player_recent_matches(player_id, limit=25)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch WTA data: {e}")
    if not match_logs:
        raise HTTPException(
            status_code=404,
            detail=f"No recent matches found for {player_name}.",
        )

    # H2H (best-effort)
    h2h = None
    subject_is_p1 = True
    if opponent_id:
        try:
            h2h = await wta_client.get_head_to_head(player_id, opponent_id)
            # By convention in our call we passed player_id as p1.
            subject_is_p1 = True
        except Exception:
            h2h = None

    # Surface inference: if not provided, use the last match's surface
    surface = req.surface or (match_logs[0].get("surface") if match_logs else None)

    # ── Compute rest_days from last match date (feeds fatigue layer) ──────────
    rest_days: Optional[int] = None
    if match_logs:
        last_date_str = match_logs[0].get("date")
        if last_date_str:
            try:
                last_played = _dt.date.fromisoformat(str(last_date_str)[:10])
                rest_days   = (_dt.date.today() - last_played).days
            except Exception:
                pass

    # ── Tournament tier inference (feeds tournament multiplier) ───────────────
    # Prefer explicit req.tournament text; fall back to category from last match.
    tournament_tier: Optional[str] = None
    if req.tournament:
        tournament_tier = req.tournament
    elif match_logs:
        cat = (match_logs[0].get("category") or "").strip()
        if cat:
            tournament_tier = cat

    # Run engine
    result = wta_engine.compute_wta_projection(
        match_logs=match_logs,
        prop_type=prop_type,
        line=req.line,
        surface=surface,
        round_name=req.round,
        opp_rank=opp_rank,
        subject_rank=subject_rank,
        h2h=h2h,
        subject_is_p1=subject_is_p1,
        rest_days=rest_days,
        tournament_tier=tournament_tier,
    )

    if result.get("error") == "insufficient_data":
        raise HTTPException(
            status_code=404,
            detail=f"Insufficient match data for {player_name} on {prop_type}.",
        )
    if result.get("error"):
        raise HTTPException(status_code=400, detail=str(result["error"]))

    # ── [BAYESIAN TRUTH] override ─────────────────────────────────────────────
    _p_over  = result.get("pOver", 50)
    _p_under = result.get("pUnder", 50)
    result["recommendation"]  = "over" if _p_over >= _p_under else "under"
    result["confidenceScore"] = round(max(_p_over, _p_under))
    _conf = result["confidenceScore"]
    result["confidenceLevel"] = "High" if _conf >= 70 else "Medium" if _conf >= 60 else "Low"
    if result["recommendation"] == "under" and result.get("projection", 0) > req.line:
        result["projection"] = round(req.line - 0.5, 1)
    elif result["recommendation"] == "over" and result.get("projection", 999) < req.line:
        result["projection"] = round(req.line + 0.5, 1)

    tactical_metrics = result.get("tacticalMetrics", {})

    # AI analysis (best-effort)
    ai_task = asyncio.create_task(_get_wta_ai_analysis(
        player_name=player_name,
        opponent=opponent_name,
        prop_type=prop_type,
        line=req.line,
        projection=result["projection"],
        p_over=result["pOver"],
        p_under=result["pUnder"],
        recommendation=result["recommendation"],
        match_logs=match_logs,
        prior_mean=result["priorMean"],
        momentum_mean=result["momentumMean"],
        streak_flag=result.get("streakFlag", ""),
        surface=surface,
        round_name=req.round,
        opp_rank=opp_rank,
        subject_rank=subject_rank,
        h2h=h2h,
        tactical_metrics=tactical_metrics,
    ))
    try:
        ai = await asyncio.wait_for(ai_task, timeout=14)
    except Exception:
        ai = {}

    prop_label = wta_engine.PROP_LABELS.get(prop_type, prop_type.replace("_", " ").title())

    return {
        "sport":           "wta",
        "playerName":      player_name,
        "playerId":        player_id,
        "opponentName":    opponent_name,
        "opponentId":      opponent_id,
        "subjectRank":     subject_rank,
        "opponentRank":    opp_rank,
        "propType":        prop_type,
        "propLabel":       prop_label,
        "line":            req.line,
        "surface":         surface,
        "round":           req.round,
        "tournament":      req.tournament,
        "projection":      result["projection"],
        "pOver":           result["pOver"],
        "pUnder":          result["pUnder"],
        "recommendation":  result["recommendation"],
        "confidenceScore": result["confidenceScore"],
        "confidenceLevel": result["confidenceLevel"],
        "priorMean":       result["priorMean"],
        "momentumMean":    result["momentumMean"],
        "sampleSize":      result["sampleSize"],
        "streakFlag":      result.get("streakFlag", ""),
        "h2h":             h2h,
        "sharpSummary":      ai.get("sharpSummary", ""),
        "reasoning":         ai.get("reasoning", ""),
        "tacticalBreakdown": ai.get("tacticalBreakdown", ""),
        "matchLogs":       match_logs[:15],
        "gameLogs":        match_logs[:15],   # alias for shared mobile renderer
        "bayesianMetrics": {
            "priorMean":       result["priorMean"],
            "momentumMean":    result["momentumMean"],
            "sampleSize":      result["sampleSize"],
            "tacticalMetrics": tactical_metrics,
        },
    }
