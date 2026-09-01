"""
NHL prediction routes — /api/nhl/*
"""
import logging
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional

from config import db
import nhl_client
import nhl_engine
from engine_base import normalize_response

log = logging.getLogger("nhl_routes")
router = APIRouter(prefix="/api/nhl", tags=["nhl"])

CURRENT_NHL_SEASON = nhl_client.CURRENT_NHL_SEASON


# ── Player search ─────────────────────────────────────────────────────────────

@router.get("/players/search")
async def search_nhl_players(q: str = Query("", min_length=2), limit: int = Query(15)):
    try:
        players = await nhl_client.search_players(q, limit=limit)
        def _nhl_full(p):
            n = (p.get("full_name") or "").strip()
            if not n:
                n = f"{p.get('first_name','') or ''} {p.get('last_name','') or ''}".strip()
            return n or None

        def _nhl_team(p):
            t = p.get("team") or {}
            if t and "full_name" not in t:
                t["full_name"] = (t.get("display_name") or
                                  f"{t.get('city','') or ''} {t.get('name','') or ''}".strip())
            return t

        return [
            {
                "id":          p.get("id"),
                "fullName":    _nhl_full(p),
                "firstName":   p.get("first_name"),
                "lastName":    p.get("last_name"),
                "position":    p.get("position", ""),
                "team":        _nhl_team(p),
                "nationality": p.get("nationality"),
            }
            for p in players
            if _nhl_full(p)
        ]
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"NHL player search failed: {e}")


# ── Teams ─────────────────────────────────────────────────────────────────────

@router.get("/next-match")
async def nhl_next_match(player_id: int = Query(...)):
    """Return the next upcoming NHL game for a player's team (for auto-fill)."""
    try:
        result = await nhl_client.get_player_next_match(player_id)
        return result
    except Exception as e:
        log.warning(f"[NHL NEXT MATCH ROUTE] player_id={player_id}: {e}")
        return {"found": False}


@router.get("/teams")
async def get_nhl_teams():
    try:
        teams = await nhl_client.get_teams()
        return [
            {
                "id":           t.get("id"),
                "fullName":     t.get("full_name"),
                "abbreviation": t.get("abbreviation"),
                "city":         t.get("city"),
                "name":         t.get("name"),
                "conference":   t.get("conference"),
                "division":     t.get("division"),
            }
            for t in teams
        ]
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"NHL teams failed: {e}")


# ── Predict ───────────────────────────────────────────────────────────────────

class NhlPredictRequest(BaseModel):
    playerName:        str
    playerId:          Optional[int]   = None
    teamName:          Optional[str]   = ""
    position:          Optional[str]   = ""
    propType:          str
    line:              float
    opponentName:      Optional[str]   = ""
    venue:             Optional[str]   = "home"
    season:            Optional[int]   = CURRENT_NHL_SEASON
    oppGoalsPerGame:   Optional[float] = None
    restDays:          Optional[int]   = None


@router.post("/predict")
async def nhl_predict(req: NhlPredictRequest):
    from routes.auth import verify_session
    sess = await verify_session(req)
    if not sess.get("valid"):
        raise HTTPException(status_code=401, detail="Invalid or expired session. Please sign in again.")
    access = sess.get("access_type", "")
    if not access or access == "NoSubscription":
        raise HTTPException(status_code=403, detail="Active subscription required")

    prop_type = req.propType.lower().strip()
    venue     = (req.venue or "home").lower()
    if venue not in ("home", "away"):
        venue = "home"

    valid_props = set(nhl_engine.NHL_PROPS.keys())
    if prop_type not in valid_props:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown NHL prop: {prop_type}. Valid: {sorted(valid_props)}"
        )

    if req.line <= 0:
        raise HTTPException(status_code=400, detail="Line must be positive.")

    # ── Resolve player ────────────────────────────────────────────────────────
    player_id   = req.playerId
    player_data = None
    position    = req.position or ""
    team_name   = req.teamName or ""

    if player_id:
        player_data = await nhl_client.get_player(player_id)

    if not player_data and req.playerName:
        results = await nhl_client.search_players(req.playerName, limit=5)
        if results:
            best        = results[0]
            player_id   = best.get("id")
            player_data = await nhl_client.get_player(player_id) or best

    if player_data:
        position  = position or player_data.get("position_code", player_data.get("position", ""))
        teams     = player_data.get("teams") or []
        if teams and not team_name:
            teams_sorted = sorted(teams, key=lambda t: t.get("season", 0), reverse=True)
            team_name = teams_sorted[0].get("full_name", "")

    if not player_id:
        raise HTTPException(status_code=404, detail=f"Player '{req.playerName}' not found in NHL database.")

    log.info(f"[NHL PREDICT] {req.playerName} ({player_id}) | {prop_type} {req.line} | {venue}")

    # Try current season first; fall back one year if no data yet
    cur_season  = req.season or CURRENT_NHL_SEASON
    game_logs   = []
    for try_season in [cur_season, cur_season - 1]:
        try:
            logs_r = await nhl_client.get_player_game_logs(player_id, try_season)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Failed to fetch NHL data: {e}")
        if logs_r:
            game_logs = logs_r
            break

    if not game_logs:
        raise HTTPException(
            status_code=422,
            detail=f"No game log data found for {req.playerName} in NHL season {cur_season} or {cur_season - 1}.",
        )

    result = nhl_engine.compute_nhl_projection(
        game_logs          = game_logs,
        prop_type          = prop_type,
        line               = req.line,
        venue              = venue,
        opp_goals_per_game = req.oppGoalsPerGame,
        rest_days          = req.restDays,
    )

    if "error" in result:
        raise HTTPException(status_code=422, detail=result["error"])

    # ── [BAYESIAN TRUTH] override ─────────────────────────────────────────────
    p_over  = result["pOver"]
    p_under = result["pUnder"]
    result["recommendation"]  = "over" if p_over >= p_under else "under"
    if result["recommendation"] == "under" and result.get("projection", 0) > req.line:
        result["projection"] = round(req.line - 0.5, 1)
    elif result["recommendation"] == "over" and result.get("projection", 999) < req.line:
        result["projection"] = round(req.line + 0.5, 1)
    result["confidenceScore"] = round(max(p_over, p_under))
    conf = result["confidenceScore"]
    result["confidenceLevel"] = "High" if conf >= 70 else "Medium" if conf >= 60 else "Low"

    field = nhl_engine.NHL_PROPS.get(prop_type, prop_type)
    game_log_tiles = []
    for g in game_logs[:10]:
        game_log_tiles.append({
            "date":     g.get("date", ""),
            "value":    g.get(field),
            "venue":    g.get("venue", ""),
            "opponent": g.get("opponent"),
            "won":      g.get("won"),
            "goals":    g.get("goals"),
            "assists":  g.get("assists"),
            "shots":    g.get("shots"),
            "toi":      g.get("toi"),
            "saves":    g.get("saves"),
        })

    ai_result = {"sharpSummary": "", "tacticalBreakdown": "", "reasoning": "", "keyFactors": []}

    return normalize_response({
        "sport":            "nhl",
        "playerName":       req.playerName,
        "playerId":         player_id,
        "teamName":         team_name,
        "position":         position,
        "propType":         prop_type,
        "line":             req.line,
        "venue":            venue,
        "opponentName":     req.opponentName or "",
        "projection":       result["projection"],
        "pOver":            result["pOver"],
        "pUnder":           result["pUnder"],
        "recommendation":   result["recommendation"],
        "confidenceScore":  result["confidenceScore"],
        "confidenceLevel":  result["confidenceLevel"],
        "priorMean":        result["priorMean"],
        "momentum":         result["momentum"],
        "sampleSize":       result["sampleSize"],
        "streakFlag":       result["streakFlag"],
        "gameLogs":         game_log_tiles,
        "recentValues":     result.get("recentValues", []),
        "sharpSummary":      ai_result.get("sharpSummary", ""),
        "tacticalBreakdown": ai_result.get("tacticalBreakdown", ""),
        "reasoning":         ai_result.get("reasoning", ""),
        "keyFactors":        ai_result.get("keyFactors", []),
        "rawConfidence":     result["confidenceScore"],
    })
