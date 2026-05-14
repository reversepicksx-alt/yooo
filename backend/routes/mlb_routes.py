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

CURRENT_MLB_SEASON = 2026


# ── AI analysis helper ─────────────────────────────────────────────────────────

async def _get_mlb_ai_analysis(
    player_name: str, position: str, prop_type: str, line: float,
    venue: str, opponent: str, projection: float, p_over: float, p_under: float,
    recommendation: str, game_logs: list, momentum_label: str,
    prior_mean: float, streak_flag: str,
    pitcher_name: str = "",
    park_team: str = "",
    park_factor_pct: float = 0.0,
    early_exit_risk: bool = False,
    zero_k_count: int = 0,
) -> dict:
    """Call Grok for MLB sharp verdict + reasoning, with park + pitcher + early-exit context."""
    is_pitcher = prop_type in mlb_engine.PITCHER_PROPS
    prop_label = prop_type.replace("_", " ").title()

    # Build game log context string
    ctx_lines = []
    for g in game_logs[:7]:
        gn  = g.get("gameNumber", "?")
        val = g.get("value", "?")
        opp = g.get("opponent", "")
        opp_str = f" vs {opp}" if opp else ""
        if is_pitcher:
            ip = g.get("ip", "?")
            pc = g.get("pitchCount", "?")
            ctx_lines.append(f"  G{gn}: {val} K, {ip} IP, {pc} pitches{opp_str}")
        else:
            h  = g.get("hits", "?")
            ab = g.get("atBats", "?")
            ctx_lines.append(f"  G{gn}: {val} {prop_label}, {h}/{ab} AB{opp_str}")
    game_ctx = "\n".join(ctx_lines) or "  (no recent game data)"

    # Streak context
    streak_text = ""
    if streak_flag == "OVER_STREAK":
        streak_text = " OVER streak detected across last 5 games."
    elif streak_flag == "UNDER_STREAK":
        streak_text = " UNDER streak detected across last 5 games."

    # Park factor context
    park_text = ""
    if park_team and abs(park_factor_pct) >= 2.0:
        direction = "hitter-friendly" if park_factor_pct > 0 else "pitcher-friendly"
        park_text = f"\nPark factor: {park_team} stadium is {direction} ({park_factor_pct:+.1f}% for {prop_label})."

    # Pitcher matchup context
    pitcher_text = ""
    if pitcher_name:
        pitcher_text = f"\nOpposing pitcher: {pitcher_name}. Use your knowledge of their 2025 ERA, K-rate, WHIP, and handedness to assess matchup quality."
    elif opponent and not is_pitcher:
        pitcher_text = f"\nNote: opposing pitcher for {opponent} is unknown — factor in their typical rotation quality."

    # Early-exit / scratch risk warning for pitcher strikeout OVER picks
    risk_text = ""
    if prop_type == "pitcher_strikeouts" and early_exit_risk:
        risk_text = (
            f"\n⚠ EARLY-EXIT RISK: This pitcher has {zero_k_count} starts with 0 K "
            f"in their last 5 game log — indicating early scratches or 1st-inning pulls. "
            f"Weight this heavily in your reasoning; the model has already discounted the OVER probability."
        )

    prompt = f"""MLB Props sharp analysis (for experienced sports bettors):

Player: {player_name} ({position})
Prop: {prop_label} | Line: {line} | Venue: {venue.upper()} vs {opponent or 'TBD'}
Season avg: {prior_mean:.1f} | Recent form: {momentum_label}
Model projection: {projection:.1f} → {recommendation} (P(OVER)={p_over}%, P(UNDER)={p_under}%){streak_text}{park_text}{pitcher_text}{risk_text}

Recent game log (G1 = most recent):
{game_ctx}

Write a sharp analysis covering:
1. Core edge: why project {projection:.1f} vs the {line} line
2. Park and/or pitcher matchup impact — be specific if pitcher is named
3. Main risk / counter-argument (mention early-exit risk if flagged above)

Be direct, data-driven. Return JSON ONLY:
{{"sharpSummary": "<1 tight sentence with the core edge>", "reasoning": "<2-3 sharp sentences covering matchup, park, momentum, risk>"}}"""

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
            timeout=8,
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
    pitcherName:  Optional[str] = ""   # opposing SP if known


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

    # ── Extract team_id for schedule enrichment ───────────────────────────────
    team_id = 0
    if player_data:
        team_id = (player_data.get("team") or {}).get("id", 0) or 0

    # ── Auto-remap prop type for pitchers ─────────────────────────────────────
    _PITCHER_POSITIONS = {"SP", "RP", "P", "CL", "SU", "MR", "LR"}
    if position.upper() in _PITCHER_POSITIONS and prop_type == "strikeouts":
        print(f"[MLB PREDICT] Auto-remapped strikeouts→pitcher_strikeouts for {position} {req.playerName}")
        prop_type = "pitcher_strikeouts"

    # ── Fetch data (game logs + season stats + team schedule) ─────────────────
    print(f"[MLB PREDICT] {req.playerName} ({player_id}) | {prop_type} {req.line} | {venue} vs {req.opponentName or '?'} | team_id={team_id}")

    try:
        game_logs, season_stats, prev_season_stats, team_games = await _fetch_mlb_data(
            player_id, req.season, team_id=team_id
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch MLB data: {e}")

    if not game_logs and not season_stats:
        raise HTTPException(
            status_code=404,
            detail=f"No stats found for {req.playerName} in the {req.season} season. "
                   f"They may not have played yet this season."
        )

    log.info(f"[MLB PREDICT] team_games fetched: {len(team_games)} regular-season games for team_id={team_id}")

    # ── Determine park team (home team owns the ballpark) ─────────────────────
    # home game → player's own team park; away game → opponent's park
    park_team = team_name if venue == "home" else (req.opponentName or "")

    # ── Run engine ────────────────────────────────────────────────────────────
    result = mlb_engine.compute_mlb_projection(
        game_logs=game_logs,
        season_stats=season_stats,
        prop_type=prop_type,
        line=req.line,
        venue=venue,
        position=position,
        prev_season_stats=prev_season_stats,
        park_team=park_team,
    )

    # ── Enrich game log tiles with opponent/date/venue from team schedule ──────
    if team_games and result.get("gameLogs"):
        result["gameLogs"] = _enrich_game_logs(
            result["gameLogs"], team_games, team_name
        )

    bm = result.get("bayesianMetrics", {})

    # ── Run AI analysis concurrently (non-blocking — falls back to empty) ─────
    ai_task = asyncio.create_task(_get_mlb_ai_analysis(
        player_name    = req.playerName,
        position       = position,
        prop_type      = prop_type,
        line           = req.line,
        venue          = venue,
        opponent       = req.opponentName or "",
        projection     = result["projection"],
        p_over         = result["pOver"],
        p_under        = result["pUnder"],
        recommendation = result["recommendation"],
        game_logs      = result["gameLogs"],
        momentum_label = result["momentumLabel"],
        prior_mean     = result["priorMean"],
        streak_flag    = result["streakFlag"],
        pitcher_name   = req.pitcherName or "",
        park_team      = park_team,
        park_factor_pct= bm.get("parkFactorPct", 0.0),
        early_exit_risk= bm.get("earlyExitRisk", False),
        zero_k_count   = bm.get("zeroKCount", 0),
    ))

    # ── Build response (same shape as soccer predict for UI compatibility) ────
    response = {
        **result,
        "playerName":     req.playerName,
        "playerId":       player_id,
        "teamName":       team_name,
        "teamId":         team_id,
        "opponentName":   req.opponentName or "",
        "playerPosition": position,
        "playerRole":     "Pitcher" if prop_type in mlb_engine.PITCHER_PROPS else "Batter",
        "leagueId":       None,
        "leagueName":     "MLB",
        "season":         req.season,
        "sport":          "mlb",
        "generatedAt":    datetime.now(timezone.utc).isoformat(),
    }

    # Await AI result and merge into response — hard 12 s cap so a slow AI
    # never blocks the full predict response from reaching the user.
    try:
        ai_data = await asyncio.wait_for(asyncio.shield(ai_task), timeout=12)
        if ai_data:
            response["sharpSummary"] = ai_data.get("sharpSummary", "")
            response["reasoning"]    = ai_data.get("reasoning", "")
            print(f"[MLB AI] summary: {str(ai_data.get('sharpSummary',''))[:80]}")
    except Exception as e:
        log.warning(f"[MLB AI] timed out or failed: {e}")
        response.setdefault("sharpSummary", "")
        response.setdefault("reasoning", "")

    # Cache prediction in MongoDB for analytics (upsert by player+prop+line+date)
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        await db.mlb_predictions.update_one(
            {
                "playerId":     player_id,
                "propType":     prop_type,
                "line":         req.line,
                "opponentName": req.opponentName or "",
                "venue":        venue,
                "date":         today_str,
            },
            {"$set": {**response, "cachedAt": datetime.now(timezone.utc).isoformat()}},
            upsert=True,
        )
    except Exception:
        pass

    return response


async def _fetch_mlb_data(player_id: int, season: int, team_id: int = 0):
    """Fetch game logs, season stats, and team schedule concurrently.
    Always fetches previous season game logs too and appends them so players
    with fewer than 30 current-season games still show a full 30-game history."""
    import asyncio

    async def _empty_list(): return []

    game_logs_task      = mlb_client.get_player_game_logs(player_id, season,     limit=30)
    prev_logs_task      = mlb_client.get_player_game_logs(player_id, season - 1, limit=30)
    season_stats_task   = mlb_client.get_season_stats(player_id, season)
    prev_stats_task     = mlb_client.get_season_stats(player_id, season - 1)
    team_games_task     = mlb_client.get_team_games(team_id, season) if team_id else _empty_list()

    game_logs, prev_logs, season_stats, prev_stats, team_games = await asyncio.gather(
        game_logs_task, prev_logs_task, season_stats_task, prev_stats_task, team_games_task,
        return_exceptions=True,
    )

    if isinstance(game_logs,    Exception): game_logs    = []
    if isinstance(prev_logs,    Exception): prev_logs    = []
    if isinstance(season_stats, Exception): season_stats = None
    if isinstance(prev_stats,   Exception): prev_stats   = None
    if isinstance(team_games,   Exception): team_games   = []

    # Backfill with previous season so we always have up to 30 games of history
    if len(game_logs) < 30 and prev_logs:
        needed = 30 - len(game_logs)
        game_logs = list(game_logs) + list(prev_logs[:needed])

    return game_logs, season_stats, prev_stats, team_games


def _enrich_game_logs(display_logs: list, team_games: list, player_team_name: str) -> list:
    """
    Positionally match per-game stat entries (newest first) to team schedule
    games (newest first).  Each stat at position i → team_games[i].
    Adds: gameDate, opponent (abbreviation), isHome, homeScore, awayScore.
    Falls back gracefully — unmatched entries keep their existing fields.
    """
    if not team_games:
        return display_logs

    enriched = []
    team_lower = (player_team_name or "").lower().strip()

    for i, log in enumerate(display_logs):
        if i >= len(team_games):
            enriched.append(log)
            continue

        game = team_games[i]
        home_obj  = game.get("home_team", {})
        away_obj  = game.get("away_team", {})
        home_full = (home_obj.get("display_name") or "").lower()
        away_full = (away_obj.get("display_name") or "").lower()

        # Determine if the player's team is home or away
        home_match = (
            team_lower and (
                team_lower in home_full or
                home_full in team_lower or
                (team_lower.split() and team_lower.split()[-1] in home_full)
            )
        )
        is_home   = bool(home_match)
        opp_obj   = away_obj if is_home else home_obj

        raw_date  = game.get("date", "")
        game_date = raw_date[:10] if raw_date else None  # "YYYY-MM-DD"
        home_runs = (game.get("home_team_data") or {}).get("runs")
        away_runs = (game.get("away_team_data") or {}).get("runs")

        enriched.append({
            **log,
            "gameDate":  game_date,
            "opponent":  opp_obj.get("abbreviation") or None,
            "isHome":    is_home,
            "homeScore": home_runs,
            "awayScore": away_runs,
        })

    return enriched
