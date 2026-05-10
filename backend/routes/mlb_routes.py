"""
MLB prediction routes — /api/mlb/*
"""
import asyncio
import logging
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional

from config import db, XAI_API_KEY, EMERGENT_LLM_KEY
import mlb_client
import mlb_engine

log = logging.getLogger("mlb_routes")

EMERGENT_PROXY = "https://llm.chutes.ai"

router = APIRouter(prefix="/api/mlb", tags=["mlb"])

CURRENT_MLB_SEASON = 2025


# ── AI analysis helper ─────────────────────────────────────────────────────────

async def _get_mlb_ai_analysis(
    player_name: str, position: str, prop_type: str, line: float,
    venue: str, opponent: str, projection: float, p_over: float, p_under: float,
    recommendation: str, game_logs: list, momentum_label: str,
    prior_mean: float, streak_flag: str,
) -> dict:
    """Call Grok (primary) or Gemini (fallback) for MLB sharp verdict + reasoning."""
    is_pitcher = prop_type in mlb_engine.PITCHER_PROPS
    prop_label = prop_type.replace("_", " ").title()

    # Build game log context string
    ctx_lines = []
    for g in game_logs[:7]:
        gn = g.get("gameNumber", "?")
        val = g.get("value", "?")
        if is_pitcher:
            ip  = g.get("ip", "?")
            pc  = g.get("pitchCount", "?")
            ctx_lines.append(f"  G{gn}: {val} K, {ip} IP, {pc} pitches")
        else:
            h  = g.get("hits", "?")
            ab = g.get("atBats", "?")
            ctx_lines.append(f"  G{gn}: {val} {prop_label}, {h}/{ab} AB")
    game_ctx = "\n".join(ctx_lines) or "  (no recent game data)"

    # Streak context
    streak_text = ""
    if streak_flag == "OVER_STREAK":
        streak_text = f" The model detects an OVER streak in the last 5 games."
    elif streak_flag == "UNDER_STREAK":
        streak_text = f" The model detects an UNDER streak in the last 5 games."

    prompt = f"""MLB Props sharp analysis (for experienced bettors):

Player: {player_name} ({position})
Prop: {prop_label} | Line: {line}
Venue: {venue.upper()} vs {opponent or 'TBD'}
Season avg: {prior_mean:.1f} | Momentum: {momentum_label}
Model projection: {projection:.1f} → {recommendation} (P(OVER)={p_over}%, P(UNDER)={p_under}%){streak_text}

Recent game log (G1 = most recent):
{game_ctx}

Write a sharp 2-3 sentence betting analysis covering:
1. Why the model projects {projection:.1f} vs the {line} line — what's the key edge?
2. Any matchup or momentum factor that confirms or challenges the {recommendation}
3. The main risk factor a bettor should know

Be direct, data-driven, no fluff. Return a JSON object ONLY:
{{"sharpSummary": "<1 tight sentence: core edge/tension>", "reasoning": "<2-3 sharp sentences of analysis>"}}"""

    # Try Grok first
    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=XAI_API_KEY, base_url="https://api.x.ai/v1")
        resp = await asyncio.wait_for(
            client.chat.completions.create(
                model="grok-4-1-fast-non-reasoning",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=300,
                temperature=0.5,
            ),
            timeout=18,
        )
        raw = resp.choices[0].message.content.strip()
        import json, re
        m = re.search(r'\{[\s\S]*\}', raw)
        if m:
            data = json.loads(m.group(0))
            log.info(f"[MLB AI] Grok OK for {player_name}")
            return data
    except Exception as e:
        log.warning(f"[MLB AI] Grok failed: {e}")

    # Gemini fallback
    try:
        import asyncio as aio
        from openai import OpenAI as SyncOpenAI
        sync_client = SyncOpenAI(api_key=EMERGENT_LLM_KEY, base_url=EMERGENT_PROXY + "/v1")
        loop = asyncio.get_event_loop()
        def _call_gemini():
            return sync_client.chat.completions.create(
                model="gemini/gemini-2.5-flash",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=300,
                temperature=0.5,
            )
        resp = await asyncio.wait_for(loop.run_in_executor(None, _call_gemini), timeout=15)
        raw = resp.choices[0].message.content.strip()
        import json, re
        m = re.search(r'\{[\s\S]*\}', raw)
        if m:
            data = json.loads(m.group(0))
            log.info(f"[MLB AI] Gemini OK for {player_name}")
            return data
    except Exception as e:
        log.warning(f"[MLB AI] Gemini failed: {e}")

    return {}

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

    # ── Run AI analysis concurrently (non-blocking — falls back to None) ──────
    ai_task = asyncio.create_task(_get_mlb_ai_analysis(
        player_name   = req.playerName,
        position      = position,
        prop_type     = prop_type,
        line          = req.line,
        venue         = venue,
        opponent      = req.opponentName or "",
        projection    = result["projection"],
        p_over        = result["pOver"],
        p_under       = result["pUnder"],
        recommendation= result["recommendation"],
        game_logs     = result["gameLogs"],
        momentum_label= result["momentumLabel"],
        prior_mean    = result["priorMean"],
        streak_flag   = result["streakFlag"],
    ))

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

    # Await AI result and merge into response
    try:
        ai_data = await asyncio.wait_for(asyncio.shield(ai_task), timeout=20)
        if ai_data:
            response["sharpSummary"] = ai_data.get("sharpSummary", "")
            response["reasoning"]    = ai_data.get("reasoning", "")
            print(f"[MLB AI] summary: {str(ai_data.get('sharpSummary',''))[:80]}")
    except Exception as e:
        log.warning(f"[MLB AI] timed out or failed: {e}")
        response.setdefault("sharpSummary", "")
        response.setdefault("reasoning", "")

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
