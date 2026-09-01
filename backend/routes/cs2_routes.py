"""
CS2 prediction routes — /api/cs2/*
v4 Ultra: LAN/Online · Map KPR · CT/T Side · Enhanced Role · ADR Trend · Form Bias · Underdog Compression
"""
import logging
import json
import re
from fastapi import APIRouter, HTTPException, Query
from models import Cs2PredictRequest
from typing import Optional

from config import db
import cs2_client
import cs2_engine
from engine_base import normalize_response

log    = logging.getLogger("cs2_routes")
router = APIRouter(prefix="/api/cs2", tags=["cs2"])

CS2_PROP_LABELS = {
    "kills":                "Kills",
    "deaths":               "Deaths",
    "assists":              "Assists",
    "adr":                  "ADR",
    "headshot_pct":         "Headshot %",
    "headshots":            "Headshots",
    "first_kills":          "First Kills",
    "clutches_won":         "Clutches Won",
    "rating":               "Rating",
    "maps_1_2_kills":       "Maps 1-2 Kills",
    "maps_1_2_deaths":      "Maps 1-2 Deaths",
    "maps_1_2_assists":     "Maps 1-2 Assists",
    "maps_1_2_adr":         "Maps 1-2 ADR",
    "maps_1_2_headshots":   "Maps 1-2 Headshots",
    "map1_kills":           "Map 1 Kills",
    "map3_kills":           "Map 3 Kills",
    "map3_headshots":       "Map 3 Headshots",
    "map3_deaths":          "Map 3 Deaths",
    "map3_assists":         "Map 3 Assists",
    "map3_adr":             "Map 3 ADR",
    "maps_1_3_kills":       "Maps 1-3 Kills",
    "maps_1_3_headshots":   "Maps 1-3 Headshots",
}

# Role → human-readable label for structured analysis
_ROLE_LABELS = {
    "awper":         "AWPer (boom-bust, low HS%)",
    "entry_fragger": "Entry Fragger (high FK rate, high KPR)",
    "star_rifler":   "Star Rifler (consistent high KPR)",
    "lurker":        "Lurker (clutch-dependent, flanks)",
    "igl":           "IGL (sacrifices kills for utility/info)",
    "support":       "Support (capped ceiling, very consistent)",
    "rifler":        "Rifler (balanced)",
    "unknown":       "Unknown",
}


# ── Player search ─────────────────────────────────────────────────────────────

@router.get("/players/search")
async def search_players(q: str = Query(..., min_length=2)):
    try:
        players = await cs2_client.search_players(q)
        return [
            {
                "id":       p.get("id"),
                "nickname": p.get("nickname", ""),
                "fullName": p.get("fullName", ""),
                "team":     p.get("team"),
                "isActive": p.get("isActive"),
                "age":      p.get("age"),
            }
            for p in players[:15]
        ]
    except Exception as e:
        log.error(f"CS2 player search error: {e}")
        return []


# ── Team search ───────────────────────────────────────────────────────────────

@router.get("/teams/search")
async def search_teams(q: str = Query(..., min_length=2)):
    try:
        return await cs2_client.search_teams(q)
    except Exception as e:
        log.error(f"CS2 team search error: {e}")
        return []


# ── Rankings ─────────────────────────────────────────────────────────────────

@router.get("/rankings")
async def get_rankings():
    try:
        return await cs2_client.get_rankings(30)
    except Exception as e:
        log.error(f"CS2 rankings error: {e}")
        return []


# ── Next match ────────────────────────────────────────────────────────────────

@router.get("/next-match")
async def cs2_next_match(playerId: Optional[int] = None, teamId: Optional[int] = None):
    """Return the next upcoming match for a CS2 player/team."""
    try:
        return await cs2_client.get_player_next_match(
            player_id=playerId or 0,
            team_id=teamId,
        )
    except Exception as e:
        log.error(f"CS2 next-match error: {e}")
        return {"found": False}


# ── Predict ───────────────────────────────────────────────────────────────────

@router.post("/predict")
async def cs2_predict(req: Cs2PredictRequest):
    from routes.auth import verify_session
    sess = await verify_session(req)
    if not sess.get("valid"):
        raise HTTPException(status_code=401, detail="Invalid or expired session. Please sign in again.")
    access = sess.get("access_type", "")
    if not access or access == "NoSubscription":
        raise HTTPException(status_code=403, detail="Active subscription required")
    prop_type = req.propType.lower().strip()
    if prop_type not in cs2_engine.CS2_PROPS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown CS2 prop: {prop_type}. Valid: {sorted(cs2_engine.CS2_PROPS)}",
        )
    if req.line <= 0:
        raise HTTPException(status_code=400, detail="Line must be positive.")

    # ── Resolve player ────────────────────────────────────────────────────────
    player_id = req.playerId
    team_id   = req.teamId
    team_name = req.teamName or ""
    nickname  = req.playerNickname.strip()

    if not player_id:
        results = await cs2_client.search_players(nickname)
        if not results:
            raise HTTPException(status_code=404, detail=f"Player '{nickname}' not found in CS2 database.")
        best = None
        nick_low = nickname.lower()
        for p in results:
            if p.get("nickname", "").lower() == nick_low and p.get("isActive"):
                best = p
                break
        if not best:
            best = results[0]
        player_id = best["id"]
        nickname  = best.get("nickname", nickname)
        if best.get("team"):
            team_id   = team_id   or best["team"].get("id")
            team_name = team_name or best["team"].get("name", "")

    if not team_id:
        raise HTTPException(
            status_code=422,
            detail=f"Could not determine team for '{nickname}'. Provide teamId.",
        )

    print(f"[CS2 PREDICT] {nickname} ({player_id}) | {prop_type} {req.line} | team={team_name}({team_id}) | opp={req.opponentName or '?'} | map={req.mapName or '?'} | teamRank={req.playerTeamRank} | startsCT={req.playerTeamStartsCt}")

    # ── Fetch stats ───────────────────────────────────────────────────────────
    is_match_level = prop_type in cs2_engine.MATCH_LEVEL_PROPS
    try:
        if is_match_level:
            map_logs = await cs2_client.get_player_recent_match_stats(player_id, team_id, limit=30)
        else:
            map_logs = await cs2_client.get_player_recent_map_stats(player_id, team_id, limit=30)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch CS2 data: {e}")

    if not map_logs:
        data_kind = "match" if is_match_level else "map"
        raise HTTPException(
            status_code=404,
            detail=f"No recent {data_kind} stats found for {nickname}. They may not have played recently.",
        )

    # ── Fetch opponent rank if opponentName given ────────────────────────────
    opp_rank = req.opponentRank
    player_team_rank = req.playerTeamRank
    if req.opponentName and (not opp_rank or not player_team_rank):
        try:
            rankings = await cs2_client.get_rankings(100)
            opp_name_low  = (req.opponentName or "").lower()
            team_name_low = team_name.lower()
            for r in rankings:
                rname = r.get("team", {}).get("name", "").lower()
                if not opp_rank and opp_name_low and opp_name_low in rname:
                    opp_rank = r.get("rank")
                if not player_team_rank and team_name_low and team_name_low in rname:
                    player_team_rank = r.get("rank")
                if opp_rank and player_team_rank:
                    break
        except Exception:
            pass

    # ── Run engine ────────────────────────────────────────────────────────────
    result = cs2_engine.compute_cs2_projection(
        map_logs=map_logs,
        prop_type=prop_type,
        line=req.line,
        opponent_rank=opp_rank,
        opponent_name=req.opponentName or None,
        map_name=req.mapName or None,
        player_team_rank=player_team_rank,
        player_team_starts_ct=req.playerTeamStartsCt,
    )

    if result.get("error") == "insufficient_data":
        raise HTTPException(
            status_code=404,
            detail=f"Insufficient data for {nickname} on {prop_type}. Need at least 2 matches/maps.",
        )

    # ── [BAYESIAN TRUTH] override ─────────────────────────────────────────────
    # Pins recommendation and confidence to Bayesian probability — prevents
    # badge/direction mismatch and overrides the engine's internal soft caps.
    _p_over  = result.get("pOver", 50)
    _p_under = result.get("pUnder", 50)
    result["recommendation"]  = "over" if _p_over >= _p_under else "under"
    result["confidenceScore"] = round(max(_p_over, _p_under))
    _conf = result["confidenceScore"]
    result["confidenceLevel"] = "High" if _conf >= 70 else "Medium" if _conf >= 60 else "Low"
    # Flip projection to sit on the correct side of the line if direction changed
    if result["recommendation"] == "under" and result.get("projection", 0) > req.line:
        result["projection"] = round(req.line - 0.5, 1)
    elif result["recommendation"] == "over" and result.get("projection", 999) < req.line:
        result["projection"] = round(req.line + 0.5, 1)

    tactical_metrics = result.get("tacticalMetrics", {})

    ai = {"sharpSummary": "", "reasoning": "", "tacticalBreakdown": ""}

    from deterministic_explanations import build_sport_deterministic_explanation

    prop_label = CS2_PROP_LABELS.get(prop_type, prop_type.replace("_", " ").title())

    response = {
        "sport":               "cs2",
        "playerName":          nickname,
        "playerId":            player_id,
        "teamName":            team_name,
        "teamId":              team_id,
        "propType":            prop_type,
        "propLabel":           prop_label,
        "line":                req.line,
        "opponentName":        req.opponentName or "",
        "opponentRank":        opp_rank,
        "mapName":             req.mapName or "",
        "playerTeamRank":      player_team_rank,
        "playerTeamStartsCt":  req.playerTeamStartsCt,
        "projection":          result["projection"],
        "pOver":               result["pOver"],
        "pUnder":              result["pUnder"],
        "recommendation":      result["recommendation"],
        "confidenceScore":     result["confidenceScore"],
        "confidenceLevel":     result["confidenceLevel"],
        "priorMean":           result["priorMean"],
        "momentumMean":        result["momentumMean"],
        "sampleSize":          result["sampleSize"],
        "streakFlag":          result.get("streakFlag", ""),
        "sharpSummary":        "",
        "reasoning":           "",
        "tacticalBreakdown":   "",
        "gameLogs":            map_logs[:15],
        "bayesianMetrics": {
            "priorMean":        result["priorMean"],
            "momentumMean":     result["momentumMean"],
            "sampleSize":       result["sampleSize"],
            "tacticalMetrics":  tactical_metrics,
        },
    }
    build_sport_deterministic_explanation(response, "cs2")
    return normalize_response(response)
