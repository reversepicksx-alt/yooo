"""
NFL prediction routes — /api/nfl/*
"""
import logging
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional

from config import db
import nfl_client
import nfl_engine
from engine_base import normalize_response

log = logging.getLogger("nfl_routes")
router = APIRouter(prefix="/api/nfl", tags=["nfl"])

CURRENT_NFL_SEASON = nfl_client.CURRENT_NFL_SEASON


# ── Player search ─────────────────────────────────────────────────────────────

@router.get("/players/search")
async def search_nfl_players(q: str = Query("", min_length=2), limit: int = Query(15)):
    try:
        players = await nfl_client.search_players(q, limit=limit)
        return [
            {
                "id":        p.get("id"),
                "fullName":  f"{p.get('first_name','')} {p.get('last_name','')}".strip(),
                "firstName": p.get("first_name"),
                "lastName":  p.get("last_name"),
                "position":  p.get("position_abbreviation") or p.get("position", ""),
                "team":      p.get("team") or {},
                "jersey":    p.get("jersey_number"),
                "age":       p.get("age"),
                "college":   p.get("college"),
            }
            for p in players
        ]
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"NFL player search failed: {e}")


# ── Teams ─────────────────────────────────────────────────────────────────────

@router.get("/teams")
async def get_nfl_teams():
    try:
        teams = await nfl_client.get_teams()
        return [
            {
                "id":           t.get("id"),
                "fullName":     t.get("full_name"),
                "abbreviation": t.get("abbreviation"),
                "location":     t.get("location"),
                "name":         t.get("name"),
                "conference":   t.get("conference"),
                "division":     t.get("division"),
            }
            for t in teams
        ]
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"NFL teams failed: {e}")


# ── Next match ────────────────────────────────────────────────────────────────

@router.get("/next-match")
async def nfl_next_match(player_id: int = Query(...)):
    try:
        result = await nfl_client.get_next_match(player_id)
        return result
    except Exception as e:
        log.warning(f"[NFL NEXT MATCH] {e}")
        return {"found": False}


# ── Predict ───────────────────────────────────────────────────────────────────

class NflPredictRequest(BaseModel):
    # Session credentials are sent in the JSON body by the mobile client,
    # matching the MLB/NBA/NHL prediction contracts.
    email:              str = ""
    token:              str = ""
    playerName:         str
    playerId:           Optional[int]   = None
    teamName:           Optional[str]   = ""
    position:           Optional[str]   = ""
    propType:           str
    line:               float
    opponentName:       Optional[str]   = ""
    venue:              Optional[str]   = "home"
    season:             Optional[int]   = CURRENT_NFL_SEASON
    gameTotal:          Optional[float] = None   # O/U total
    oppRankPercentile:  Optional[float] = None   # 0.0=best D, 1.0=worst D
    restDays:           Optional[int]   = None


@router.post("/predict")
async def nfl_predict(req: NflPredictRequest):
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

    valid_props = set(nfl_engine.NFL_PROPS.keys())
    if prop_type not in valid_props:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown NFL prop: {prop_type}. Valid: {sorted(valid_props)}"
        )

    if req.line <= 0:
        raise HTTPException(status_code=400, detail="Line must be positive.")

    # ── Resolve player ────────────────────────────────────────────────────────
    player_id   = req.playerId
    player_data = None
    position    = req.position or ""
    team_name   = req.teamName or ""

    if player_id:
        player_data = await nfl_client.get_player(player_id)

    if not player_data and req.playerName:
        results = await nfl_client.search_players(req.playerName, limit=5)
        if results:
            best        = results[0]
            player_id   = best.get("id")
            player_data = await nfl_client.get_player(player_id) or best

    if player_data:
        position  = position or (player_data.get("position_abbreviation") or player_data.get("position", ""))
        team_name = team_name or (player_data.get("team") or {}).get("full_name", "")

    if not player_id:
        raise HTTPException(status_code=404, detail=f"Player '{req.playerName}' not found in NFL database.")

    log.info(f"[NFL PREDICT] {req.playerName} ({player_id}) | {prop_type} {req.line} | {venue}")

    # ── Fetch bounded model logs plus deeper user-visible history ─────────────
    # Recent logs drive the projection; older seasons remain available as
    # labelled evidence instead of being silently discarded after one fallback.
    history_logs = []
    history_seasons = []
    for try_season in [req.season, req.season - 1, req.season - 2, req.season - 3]:
        try:
            logs_r = await nfl_client.get_player_game_logs(player_id, try_season)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Failed to fetch NFL data: {e}")
        if logs_r:
            history_seasons.append(try_season)
            for row in logs_r:
                enriched = dict(row)
                enriched["season"] = try_season
                history_logs.append(enriched)

    if not history_logs:
        raise HTTPException(
            status_code=404,
            detail=f"No stats found for {req.playerName} in {req.season} or the three prior seasons."
        )

    history_logs.sort(key=lambda x: x.get("date", ""), reverse=True)
    game_logs = history_logs[:30]

    # ── Run engine ────────────────────────────────────────────────────────────
    result = nfl_engine.compute_nfl_projection(
        game_logs           = game_logs,
        prop_type           = prop_type,
        line                = req.line,
        venue               = venue,
        game_total          = req.gameTotal,
        opp_rank_percentile = req.oppRankPercentile,
        rest_days           = req.restDays,
        position            = position,
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

    field = nfl_engine.NFL_PROPS.get(prop_type, prop_type)
    game_log_tiles = []
    for g in history_logs[:60]:
        game_log_tiles.append({
            "date":             g.get("date", ""),
            "value":            g.get(field),
            "week":             g.get("week"),
            "season":           g.get("season"),
            "venue":            g.get("venue", ""),
            "opponent":         g.get("opponent"),
            "score":            g.get("score"),
            "won":              g.get("won"),
            "passing_yards":    g.get("passing_yards"),
            "rushing_yards":    g.get("rushing_yards"),
            "receiving_yards":  g.get("receiving_yards"),
            "receptions":       g.get("receptions"),
        })

    response = {
        "sport":            "nfl",
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
        "rawConfidence":     result["confidenceScore"],
        "historyGameCount": len(history_logs),
        "historySeasons":   history_seasons,
        "historyRange": {
            "min": min(history_seasons) if history_seasons else req.season,
            "max": max(history_seasons) if history_seasons else req.season,
        },
        "matchupOverview": {
            "homeTeam":         team_name if venue == "home" else (req.opponentName or "Opponent"),
            "awayTeam":         (req.opponentName or "Opponent") if venue == "home" else team_name,
            "playerIsHome":     venue == "home",
            "expectedGameType": "High-scoring game" if req.gameTotal and req.gameTotal >= 48
                                else "Low-scoring game" if req.gameTotal and req.gameTotal <= 40
                                else "Balanced matchup",
            "keyMatchupFactor": (
                f"Game total {req.gameTotal:.1f}" if req.gameTotal is not None else None
            ),
        },
    }
    from deterministic_explanations import build_sport_deterministic_explanation
    from prop_safety_cache import get_prop_safety as _get_prop_safety
    # Attach empirical safety data so the explanation layer can surface AVOID evidence.
    _nfl_rec_upper = str(response.get("recommendation") or "").upper()
    if _nfl_rec_upper in {"OVER", "UNDER"}:
        _nfl_safety_data = _get_prop_safety(
            prop_type,
            _nfl_rec_upper,
            league_id=None,
            position=position or "",
        )
        if _nfl_safety_data:
            response["safetyRating"] = _nfl_safety_data.get("safety", "RISKY")
            response["propHistoricalRate"] = _nfl_safety_data.get("hitRate")
            response["propHistoricalN"] = _nfl_safety_data.get("n")
        else:
            response.setdefault("safetyRating", "RISKY")
    build_sport_deterministic_explanation(response, "nfl")
    return normalize_response(response)
