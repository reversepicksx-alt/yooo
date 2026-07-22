"""
NBA prediction routes — /api/nba/*
"""
import asyncio
import logging
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional

from config import db, XAI_API_KEY
import nba_client
import nba_engine

log = logging.getLogger("nba_routes")
router = APIRouter(prefix="/api/nba", tags=["nba"])

CURRENT_NBA_SEASON = nba_client.CURRENT_NBA_SEASON


# ── AI analysis ───────────────────────────────────────────────────────────────

async def _get_nba_ai_analysis(
    player_name: str, position: str, prop_type: str, line: float,
    venue: str, opponent: str, projection: float,
    p_over: float, p_under: float, recommendation: str,
    game_logs: list, prior_mean: float, streak_flag: str,
    rest_days: Optional[int] = None,
    opp_def_rating: Optional[float] = None,
) -> dict:
    try:
        from ai_engine import _ai_call

        prop_label = prop_type.replace("_", " ").title()
        rec_label  = recommendation.upper()
        conf       = round(max(p_over, p_under))
        ctx_lines  = []
        for i, g in enumerate(game_logs[:7]):
            val = g.get(nba_engine.NBA_PROPS.get(prop_type, prop_type), "?")
            opp = g.get("opponent", "")
            date = g.get("date", "")
            ctx_lines.append(f"  G{i+1} ({date}): {val} {prop_label}" + (f" vs {opp}" if opp else ""))
        game_ctx = "\n".join(ctx_lines) or "  (no recent game data)"

        streak_text = ""
        if streak_flag == "OVER_STREAK":
            streak_text = " OVER streak across last 4+ games."
        elif streak_flag == "UNDER_STREAK":
            streak_text = " UNDER streak across last 4+ games."

        prompt = f"""You are a sharp NBA prop betting analyst. Analyze this pick and give a concise verdict.

PLAYER: {player_name} ({position or "Guard/Forward/Center"})
PROP: {prop_label} | LINE: {line} | VENUE: {venue.upper()} vs {opponent or "opponent"}
PROJECTION: {projection} | P(OVER): {p_over}% | P(UNDER): {p_under}%
RECOMMENDATION: {rec_label} ({conf}% confidence)
PRIOR SEASON MEAN: {prior_mean}{streak_text}
{"OPP DEF RATING: " + str(opp_def_rating) + " pts/100 poss" if opp_def_rating else ""}
{"REST: " + str(rest_days) + " days" if rest_days is not None else ""}

RECENT GAME LOG:
{game_ctx}

Write a sharp 2-3 sentence analysis explaining the {rec_label} recommendation. Focus on the most impactful statistical factors. Be direct and confident like a professional handicapper."""

        text = (await _ai_call(prompt, temperature=0.7, max_tokens=1500, timeout=30) or "").strip()
        return {
            "sharpSummary":      text[:600] if text else "",
            "tacticalBreakdown": text,
            "reasoning":         f"Bayesian projection: {projection} | P(OVER)={p_over}% P(UNDER)={p_under}%",
        }
    except Exception as e:
        log.warning(f"[NBA AI] {e}")
        return {"sharpSummary": "", "tacticalBreakdown": "", "reasoning": ""}


# ── Player search ─────────────────────────────────────────────────────────────

@router.get("/players/search")
async def search_nba_players(q: str = Query("", min_length=2), limit: int = Query(15)):
    try:
        players = await nba_client.search_players(q, limit=limit)
        return [
            {
                "id":        p.get("id"),
                "fullName":  f"{p.get('first_name','')} {p.get('last_name','')}".strip(),
                "firstName": p.get("first_name"),
                "lastName":  p.get("last_name"),
                "position":  p.get("position", ""),
                "team":      p.get("team") or {},
                "height":    p.get("height"),
                "weight":    p.get("weight"),
                "jersey":    p.get("jersey_number"),
            }
            for p in players
        ]
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"NBA player search failed: {e}")


# ── Teams ─────────────────────────────────────────────────────────────────────

@router.get("/next-match")
async def nba_next_match(player_id: int = Query(...)):
    """Return the next upcoming NBA game for a player's team (for auto-fill)."""
    try:
        result = await nba_client.get_player_next_match(player_id)
        return result
    except Exception as e:
        log.warning(f"[NBA NEXT MATCH ROUTE] player_id={player_id}: {e}")
        return {"found": False}


@router.get("/teams")
async def get_nba_teams():
    try:
        teams = await nba_client.get_teams()
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
        raise HTTPException(status_code=502, detail=f"NBA teams failed: {e}")


# ── Predict ───────────────────────────────────────────────────────────────────

class NbaPredictRequest(BaseModel):
    playerName:    str
    playerId:      Optional[int]   = None
    teamName:      Optional[str]   = ""
    position:      Optional[str]   = ""
    propType:      str
    line:          float
    opponentName:  Optional[str]   = ""
    venue:         Optional[str]   = "home"
    season:        Optional[int]   = CURRENT_NBA_SEASON
    oppDefRating:  Optional[float] = None   # opponent pts per 100 possessions
    restDays:      Optional[int]   = None   # days since last game


@router.post("/predict")
async def nba_predict(req: NbaPredictRequest):
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

    valid_props = set(nba_engine.NBA_PROPS.keys())
    if prop_type not in valid_props:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown NBA prop: {prop_type}. Valid: {sorted(valid_props)}"
        )

    if req.line <= 0:
        raise HTTPException(status_code=400, detail="Line must be positive.")

    # ── Resolve player ────────────────────────────────────────────────────────
    player_id   = req.playerId
    player_data = None
    position    = req.position or ""
    team_name   = req.teamName or ""

    if player_id:
        player_data = await nba_client.get_player(player_id)

    if not player_data and req.playerName:
        results = await nba_client.search_players(req.playerName, limit=5)
        if results:
            active = [p for p in results if p.get("active", True)]
            best   = active[0] if active else results[0]
            player_id   = best.get("id")
            player_data = await nba_client.get_player(player_id) or best

    if player_data:
        position  = position or player_data.get("position", "")
        team_name = team_name or (player_data.get("team") or {}).get("full_name", "")

    if not player_id:
        raise HTTPException(status_code=404, detail=f"Player '{req.playerName}' not found in NBA database.")

    # ── Fetch game logs + season averages (fallback to previous season) ────────
    log.info(f"[NBA PREDICT] {req.playerName} ({player_id}) | {prop_type} {req.line} | {venue}")
    game_logs = []
    season_avg = {}
    for try_season in [req.season, req.season - 1]:
        try:
            logs_r, avg_r = await asyncio.gather(
                nba_client.get_player_game_logs(player_id, try_season),
                nba_client.get_season_averages(player_id, try_season),
            )
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Failed to fetch NBA data: {e}")
        if logs_r:
            game_logs = logs_r
            season_avg = avg_r or {}
            break
        if avg_r:
            season_avg = avg_r

    if not game_logs:
        raise HTTPException(
            status_code=404,
            detail=f"No stats found for {req.playerName} in the {req.season} or {req.season - 1} season."
        )

    # ── Run engine ────────────────────────────────────────────────────────────
    result = nba_engine.compute_nba_projection(
        game_logs    = game_logs,
        prop_type    = prop_type,
        line         = req.line,
        venue        = venue,
        opp_def_rating = req.oppDefRating,
        rest_days    = req.restDays,
        season_avg   = season_avg,
    )

    if "error" in result:
        raise HTTPException(status_code=422, detail=result["error"])

    # ── [BAYESIAN TRUTH] override ─────────────────────────────────────────────
    p_over  = result["pOver"]
    p_under = result["pUnder"]
    result["recommendation"]  = "over" if p_over >= p_under else "under"
    result["confidenceScore"] = round(max(p_over, p_under))
    conf = result["confidenceScore"]
    result["confidenceLevel"] = "High" if conf >= 70 else "Medium" if conf >= 60 else "Low"
    # Visual consistency: if direction contradicts projection vs line, align projection
    if result["recommendation"] == "under" and result["projection"] > req.line:
        result["projection"] = round(req.line - 0.5, 1)
    elif result["recommendation"] == "over" and result["projection"] < req.line:
        result["projection"] = round(req.line + 0.5, 1)

    # ── AI analysis (non-blocking) ────────────────────────────────────────────
    ai_task = asyncio.create_task(_get_nba_ai_analysis(
        player_name    = req.playerName,
        position       = position,
        prop_type      = prop_type,
        line           = req.line,
        venue          = venue,
        opponent       = req.opponentName or "",
        projection     = result["projection"],
        p_over         = p_over,
        p_under        = p_under,
        recommendation = result["recommendation"],
        game_logs      = game_logs,
        prior_mean     = result["priorMean"],
        streak_flag    = result["streakFlag"],
        rest_days      = req.restDays,
        opp_def_rating = req.oppDefRating,
    ))

    # Prepare game log tiles
    prop_field = nba_engine.NBA_PROPS.get(prop_type, prop_type)
    game_log_tiles = []
    for g in game_logs[:10]:
        game_log_tiles.append({
            "date":     g.get("date", ""),
            "value":    g.get(prop_field),
            "minutes":  g.get("minutes"),
            "pts":      g.get("pts"),
            "reb":      g.get("reb"),
            "ast":      g.get("ast"),
            "venue":    g.get("venue", ""),
            "opponent": g.get("opponent", ""),
        })

    # Await AI (up to 25s)
    try:
        ai_result = await asyncio.wait_for(ai_task, timeout=25.0)
    except Exception:
        ai_result = {"sharpSummary": "", "tacticalBreakdown": "", "reasoning": ""}

    return {
        "sport":            "nba",
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
        "sharpSummary":     ai_result.get("sharpSummary", ""),
        "tacticalBreakdown": ai_result.get("tacticalBreakdown", ""),
        "reasoning":        ai_result.get("reasoning", ""),
        "rawConfidence":    result["confidenceScore"],
    }
