"""
NFL prediction routes — /api/nfl/*
"""
import asyncio
import logging
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional

from config import db, XAI_API_KEY
import nfl_client
import nfl_engine

log = logging.getLogger("nfl_routes")
router = APIRouter(prefix="/api/nfl", tags=["nfl"])

CURRENT_NFL_SEASON = nfl_client.CURRENT_NFL_SEASON


# ── AI analysis ───────────────────────────────────────────────────────────────

async def _get_nfl_ai_analysis(
    player_name: str, position: str, prop_type: str, line: float,
    venue: str, opponent: str, projection: float,
    p_over: float, p_under: float, recommendation: str,
    game_logs: list, prior_mean: float, streak_flag: str,
    game_total: Optional[float] = None,
) -> dict:
    try:
        from grok_engine import _grok_call

        prop_label = prop_type.replace("_", " ").title()
        rec_label  = recommendation.upper()
        conf       = round(max(p_over, p_under))
        field      = nfl_engine.NFL_PROPS.get(prop_type, prop_type)
        ctx_lines  = []
        for i, g in enumerate(game_logs[:6]):
            val  = g.get(field, "?")
            date = g.get("date", "")
            wk   = g.get("week", "")
            ctx_lines.append(f"  Wk{wk} ({date}): {val} {prop_label}")
        game_ctx = "\n".join(ctx_lines) or "  (no recent data)"

        streak_text = ""
        if streak_flag == "OVER_STREAK":
            streak_text = " OVER streak across last 4+ games."
        elif streak_flag == "UNDER_STREAK":
            streak_text = " UNDER streak across last 4+ games."

        prompt = f"""You are a sharp NFL prop betting analyst. Analyze this pick concisely.

PLAYER: {player_name} ({position or "NFL"})
PROP: {prop_label} | LINE: {line} | VENUE: {venue.upper()} vs {opponent or "opponent"}
PROJECTION: {projection} | P(OVER): {p_over}% | P(UNDER): {p_under}%
RECOMMENDATION: {rec_label} ({conf}% confidence)
PRIOR MEAN: {prior_mean}{streak_text}
{"GAME O/U: " + str(game_total) if game_total else ""}

RECENT GAME LOG:
{game_ctx}

Write a sharp 2-3 sentence analysis of the {rec_label}. Focus on matchup, usage, and game script. Be direct."""

        text = (await _grok_call(prompt, temperature=0.7, max_tokens=1500, timeout=30) or "").strip()
        return {
            "sharpSummary":      text[:600] if text else "",
            "tacticalBreakdown": text,
            "reasoning":         f"Bayesian projection: {projection} | P(OVER)={p_over}% P(UNDER)={p_under}%",
        }
    except Exception as e:
        log.warning(f"[NFL AI] {e}")
        return {"sharpSummary": "", "tacticalBreakdown": "", "reasoning": ""}


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


# ── Predict ───────────────────────────────────────────────────────────────────

class NflPredictRequest(BaseModel):
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

    # ── Fetch game logs (with fallback to previous season) ────────────────────
    game_logs = []
    for try_season in [req.season, req.season - 1]:
        try:
            logs_r = await nfl_client.get_player_game_logs(player_id, try_season)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Failed to fetch NFL data: {e}")
        if logs_r:
            game_logs = logs_r
            break

    if not game_logs:
        raise HTTPException(
            status_code=404,
            detail=f"No stats found for {req.playerName} in the {req.season} or {req.season - 1} season."
        )

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

    # ── AI analysis ───────────────────────────────────────────────────────────
    ai_task = asyncio.create_task(_get_nfl_ai_analysis(
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
        game_total     = req.gameTotal,
    ))

    field = nfl_engine.NFL_PROPS.get(prop_type, prop_type)
    game_log_tiles = []
    for g in game_logs[:10]:
        game_log_tiles.append({
            "date":             g.get("date", ""),
            "value":            g.get(field),
            "week":             g.get("week"),
            "venue":            g.get("venue", ""),
            "passing_yards":    g.get("passing_yards"),
            "rushing_yards":    g.get("rushing_yards"),
            "receiving_yards":  g.get("receiving_yards"),
            "receptions":       g.get("receptions"),
        })

    try:
        ai_result = await asyncio.wait_for(ai_task, timeout=25.0)
    except Exception:
        ai_result = {"sharpSummary": "", "tacticalBreakdown": "", "reasoning": ""}

    return {
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
        "sharpSummary":     ai_result.get("sharpSummary", ""),
        "tacticalBreakdown": ai_result.get("tacticalBreakdown", ""),
        "reasoning":        ai_result.get("reasoning", ""),
        "rawConfidence":    result["confidenceScore"],
    }
