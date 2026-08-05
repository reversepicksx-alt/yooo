"""
WTA Tennis prediction routes — /api/wta/*
Mirrors /api/cs2 structure: player search, predict, rankings, h2h, tournaments.
"""
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

    # ── Serve/return profiles from /match_stats (best-effort covariate) ───────
    subject_serve_profile = None
    opp_serve_profile     = None
    try:
        subj_mids = [m.get("matchId") for m in match_logs[:12] if m.get("matchId")]
        _prof_tasks = [wta_client.get_player_serve_profile(player_id, subj_mids)]
        opp_logs = None
        if opponent_id:
            opp_logs = await wta_client.get_player_recent_matches(opponent_id, limit=12)
            opp_mids = [m.get("matchId") for m in (opp_logs or [])[:12] if m.get("matchId")]
            if opp_mids:
                _prof_tasks.append(wta_client.get_player_serve_profile(opponent_id, opp_mids))
        _profs = await asyncio.gather(*_prof_tasks, return_exceptions=True)
        if _profs and not isinstance(_profs[0], Exception):
            subject_serve_profile = _profs[0]
        if len(_profs) > 1 and not isinstance(_profs[1], Exception):
            opp_serve_profile = _profs[1]
        log.info(f"[WTA PREDICT] serve profiles: subj={'yes' if subject_serve_profile else 'no'} "
                 f"opp={'yes' if opp_serve_profile else 'no'}")
    except Exception as e:
        log.warning(f"[WTA PREDICT] serve profile fetch failed (non-fatal): {e}")

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
        subject_serve_profile=subject_serve_profile,
        opp_serve_profile=opp_serve_profile,
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
        "matchupOverview": {
            "homeTeam":         player_name,
            "awayTeam":         opponent_name,
            "playerIsHome":     True,
            "surface":          surface,
            "expectedGameType": f"{(surface or 'Hard').title()} court" + (f" — {req.round}" if req.round else ""),
            "keyMatchupFactor": (
                f"H2H {h2h.get('p1Wins', 0)}–{h2h.get('p2Wins', 0)}"
                + (f" | #{subject_rank} vs #{opp_rank}" if subject_rank and opp_rank else "")
            ),
        },
        "matchLogs":       match_logs[:15],
        "gameLogs":        [
            {
                **m,
                "venue":    "neutral",
                "score":    m.get("matchScore") or m.get("score"),
                "opponent": m.get("opponent") or m.get("opponentName"),
                "value":    m.get("value"),
            }
            for m in match_logs[:15]
        ],
        "bayesianMetrics": {
            "priorMean":       result["priorMean"],
            "momentumMean":    result["momentumMean"],
            "sampleSize":      result["sampleSize"],
            "tacticalMetrics": tactical_metrics,
        },
    }
