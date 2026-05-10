"""
MLB prediction routes — /api/mlb/*
"""
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional

from config import db
import mlb_client
import mlb_engine

router = APIRouter(prefix="/api/mlb", tags=["mlb"])

CURRENT_MLB_SEASON = 2025

# ── Player search ─────────────────────────────────────────────────────────────

@router.get("/players/search")
async def search_players(q: str = Query(..., min_length=2)):
    try:
        players = await mlb_client.search_players(q, limit=15)
        return [
            {
                "id":        p.get("id"),
                "fullName":  p.get("full_name"),
                "firstName": p.get("first_name"),
                "lastName":  p.get("last_name"),
                "position":  p.get("position", ""),
                "team":      p.get("team", {}),
                "active":    p.get("active", True),
                "jersey":    p.get("jersey"),
                "batsThrows":p.get("bats_throws"),
                "age":       p.get("age"),
            }
            for p in players
        ]
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"MLB player search failed: {e}")


# ── Teams ─────────────────────────────────────────────────────────────────────

@router.get("/teams")
async def get_teams():
    try:
        teams = await mlb_client.get_teams()
        return [
            {
                "id":           t.get("id"),
                "displayName":  t.get("display_name"),
                "abbreviation": t.get("abbreviation"),
                "location":     t.get("location"),
                "name":         t.get("name"),
                "league":       t.get("league"),
                "division":     t.get("division"),
                "slug":         t.get("slug"),
            }
            for t in teams
        ]
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"MLB teams fetch failed: {e}")


# ── Predict ───────────────────────────────────────────────────────────────────

class MlbPredictRequest(BaseModel):
    playerName:   str
    playerId:     Optional[int] = None
    teamName:     Optional[str] = ""
    position:     Optional[str] = ""
    propType:     str
    line:         float
    opponentName: Optional[str] = ""
    venue:        Optional[str] = "home"
    season:       Optional[int] = CURRENT_MLB_SEASON


@router.post("/predict")
async def mlb_predict(req: MlbPredictRequest):
    prop_type = req.propType.lower().strip()
    venue = (req.venue or "home").lower()
    if venue not in ("home", "away"):
        venue = "home"

    valid_props = set(mlb_engine.ALL_PROP_FIELDS.keys())
    if prop_type not in valid_props:
        raise HTTPException(status_code=400, detail=f"Unknown MLB prop type: {prop_type}. Valid: {sorted(valid_props)}")

    if req.line <= 0:
        raise HTTPException(status_code=400, detail="Line must be positive.")

    # ── Resolve player ────────────────────────────────────────────────────────
    player_id = req.playerId
    player_data = None
    position = req.position or ""
    team_name = req.teamName or ""

    if player_id:
        player_data = await mlb_client.get_player(player_id)

    if not player_data and req.playerName:
        results = await mlb_client.search_players(req.playerName, limit=5)
        if results:
            # Pick best match: prefer active players
            active = [p for p in results if p.get("active")]
            player_data = active[0] if active else results[0]
            player_id = player_data.get("id")

    if player_data:
        position = position or player_data.get("position", "")
        if not team_name:
            team_name = (player_data.get("team") or {}).get("display_name", "")

    if not player_id:
        raise HTTPException(status_code=404, detail=f"Player '{req.playerName}' not found in MLB database.")

    # ── Auto-remap prop type for pitchers ─────────────────────────────────────
    # If user picks "strikeouts" (batter K) for an SP/RP/P, they almost
    # certainly mean pitcher strikeouts — silently correct it.
    _PITCHER_POSITIONS = {"SP", "RP", "P", "CL", "SU", "MR", "LR"}
    if position.upper() in _PITCHER_POSITIONS and prop_type == "strikeouts":
        print(f"[MLB PREDICT] Auto-remapped strikeouts→pitcher_strikeouts for {position} {req.playerName}")
        prop_type = "pitcher_strikeouts"

    # ── Fetch data ────────────────────────────────────────────────────────────
    print(f"[MLB PREDICT] {req.playerName} ({player_id}) | {prop_type} {req.line} | {venue} vs {req.opponentName}")

    try:
        game_logs, season_stats, prev_season_stats = await _fetch_mlb_data(
            player_id, req.season
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch MLB data: {e}")

    if not game_logs and not season_stats:
        raise HTTPException(
            status_code=404,
            detail=f"No stats found for {req.playerName} in the {req.season} season. "
                   f"They may not have played yet this season."
        )

    # ── Run engine ────────────────────────────────────────────────────────────
    result = mlb_engine.compute_mlb_projection(
        game_logs=game_logs,
        season_stats=season_stats,
        prop_type=prop_type,
        line=req.line,
        venue=venue,
        position=position,
        prev_season_stats=prev_season_stats,
    )

    # ── Build response (same shape as soccer predict for UI compatibility) ────
    response = {
        **result,
        "playerName":    req.playerName,
        "playerId":      player_id,
        "teamName":      team_name,
        "opponentName":  req.opponentName or "",
        "playerPosition":position,
        "playerRole":    "Pitcher" if prop_type in mlb_engine.PITCHER_PROPS else "Batter",
        "leagueId":      None,
        "leagueName":    "MLB",
        "season":        req.season,
        "generatedAt":   datetime.now(timezone.utc).isoformat(),
    }

    # Cache prediction in MongoDB for analytics
    try:
        await db.mlb_predictions.update_one(
            {
                "playerId": player_id,
                "propType": prop_type,
                "line": req.line,
                "opponentName": req.opponentName or "",
                "venue": venue,
                "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            },
            {"$set": {**response, "cachedAt": datetime.now(timezone.utc).isoformat()}},
            upsert=True,
        )
    except Exception:
        pass

    return response


async def _fetch_mlb_data(player_id: int, season: int):
    """Fetch game logs and season stats concurrently."""
    import asyncio
    game_logs_task     = mlb_client.get_player_game_logs(player_id, season, limit=30)
    season_stats_task  = mlb_client.get_season_stats(player_id, season)
    prev_stats_task    = mlb_client.get_season_stats(player_id, season - 1)

    game_logs, season_stats, prev_stats = await asyncio.gather(
        game_logs_task, season_stats_task, prev_stats_task,
        return_exceptions=True,
    )

    if isinstance(game_logs,    Exception): game_logs    = []
    if isinstance(season_stats, Exception): season_stats = None
    if isinstance(prev_stats,   Exception): prev_stats   = None

    return game_logs, season_stats, prev_stats
