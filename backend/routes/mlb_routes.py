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
    # v2 new factors
    pitcher_handedness: str = "",
    batter_handedness: str = "",
    pitcher_era: Optional[float] = None,
    game_total: Optional[float] = None,
    lineup_spot: Optional[int] = None,
    platoon_mult: float = 1.0,
    era_mult: float = 1.0,
    total_mult: float = 1.0,
    lineup_mult: float = 1.0,
    babip_mult: float = 1.0,
    rolling_babip: Optional[float] = None,
    krate_mult: float = 1.0,
    rolling_k_rate: Optional[float] = None,
    pitcher_role: str = "",
    pitch_traj_mult: float = 1.0,
) -> dict:
    """Gemini AI: MLB sharp verdict + reasoning — v2 Ultra with all 9 model layers."""
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
        pitcher_text = f"\nOpposing pitcher: {pitcher_name}"
        if pitcher_era is not None:
            pitcher_text += f" (ERA {pitcher_era:.2f})"
        if pitcher_handedness:
            pitcher_text += f" — {pitcher_handedness}HP"
        pitcher_text += f". Use your knowledge of their {CURRENT_MLB_SEASON} K-rate, WHIP, and stuff to assess matchup quality."
    elif opponent and not is_pitcher:
        pitcher_text = f"\nOpposing pitcher for {opponent} unknown — factor in their typical rotation quality."

    # Platoon split context
    platoon_text = ""
    if batter_handedness and pitcher_handedness and abs(platoon_mult - 1.0) >= 0.015:
        direction = "advantage" if platoon_mult > 1.0 else "disadvantage"
        platoon_text = (
            f"\nPlatoon split: {batter_handedness}HB vs {pitcher_handedness}HP → "
            f"{platoon_mult:.2f}× ({direction}, {abs(platoon_mult - 1.0)*100:.0f}% effect on {prop_label})."
        )
    elif batter_handedness and pitcher_handedness:
        platoon_text = f"\nPlatoon: {batter_handedness}HB vs {pitcher_handedness}HP (neutral matchup)."

    # ERA tier context
    era_text = ""
    if pitcher_era is not None and abs(era_mult - 1.0) >= 0.03:
        direction = "suppresses" if era_mult < 1.0 else "boosts"
        era_text = f"\nPitcher ERA {pitcher_era:.2f} {direction} batter output by {abs(era_mult - 1.0)*100:.0f}% (model applied {era_mult:.2f}× ERA adjustment)."

    # Game total context
    total_text = ""
    if game_total is not None:
        env = "high-scoring" if game_total > 9.5 else ("low-scoring" if game_total < 7.5 else "average-scoring")
        total_text = f"\nGame total O/U {game_total}: {env} environment (×{total_mult:.2f} environment factor applied)."

    # Lineup position context
    lineup_text = ""
    if lineup_spot is not None and abs(lineup_mult - 1.0) >= 0.02:
        slot_label = {1:"leadoff", 2:"2-hole", 3:"3-hole", 4:"cleanup", 5:"5-hole",
                      6:"6-hole", 7:"7-hole", 8:"8-hole", 9:"9-hole"}.get(lineup_spot, f"spot {lineup_spot}")
        lineup_text = f"\nLineup spot: {slot_label} (#{lineup_spot}) → {lineup_mult:.2f}× PA opportunity vs league avg."

    # BABIP regression context
    babip_text = ""
    if rolling_babip is not None and abs(babip_mult - 1.0) >= 0.02:
        direction = "regression pressure" if babip_mult < 1.0 else "bounce-back signal"
        babip_text = (
            f"\nBABIP regression: rolling BABIP {rolling_babip:.3f} vs .295 league avg → "
            f"{direction} applied ({babip_mult:.2f}× hits adjustment)."
        )

    # K-rate trend context
    krate_text = ""
    if rolling_k_rate is not None and abs(krate_mult - 1.0) >= 0.02:
        direction = "high K rate (poor contact)" if krate_mult < 1.0 else "low K rate (elite contact)"
        krate_text = f"\nContact quality: {rolling_k_rate:.1%} recent K rate → {direction} ({krate_mult:.2f}× hits adjustment)."

    # Pitch count trajectory / opener
    role_text = ""
    if pitcher_role and pitcher_role not in ("unknown", "starter", "standard_starter"):
        role_text = f"\nPitcher role detection: '{pitcher_role}' (avg pitch count pattern → {pitch_traj_mult:.2f}× IP/K adjustment)."

    # Early-exit / scratch risk
    risk_text = ""
    if prop_type == "pitcher_strikeouts" and early_exit_risk:
        risk_text = (
            f"\n⚠ EARLY-EXIT RISK: This pitcher has {zero_k_count} starts with 0 K "
            f"in their last 5 game log — indicating early scratches or 1st-inning pulls. "
            f"Model has discounted OVER probability accordingly."
        )

    prompt = f"""MLB Props sharp analysis (for experienced sports bettors) — v2 Ultra:

Player: {player_name} ({position})
Prop: {prop_label} | Line: {line} | Venue: {venue.upper()} vs {opponent or 'TBD'}
Season avg: {prior_mean:.1f} | Recent form: {momentum_label}
Model projection: {projection:.1f} → {recommendation} (P(OVER)={p_over}%, P(UNDER)={p_under}%){streak_text}{park_text}{pitcher_text}{platoon_text}{era_text}{total_text}{lineup_text}{babip_text}{krate_text}{role_text}{risk_text}

Recent game log (G1 = most recent):
{game_ctx}

You are explaining WHY the model's verdict is correct — not reaching your own conclusion. The math has already decided: {recommendation} {projection:.1f} vs {line} line.

Return JSON ONLY (no markdown outside the JSON values):
{{
  "sharpSummary": "<1 decisive sentence under 22 words committing to {recommendation} — state the single biggest factor>",
  "reasoning": "<2-4 sharp sentences covering platoon split, ERA tier, game environment, BABIP/contact quality, and park — only mention factors that are actually provided above>",
  "tacticalBreakdown": "## Model Verdict\\n**{recommendation} {projection:.1f}** vs {line} line — P(OVER)={p_over}%, P(UNDER)={p_under}%\\n\\n## Pitching Environment\\n<2-3 sentences: ERA tier, handedness matchup, park factor — use the numbers provided above>\\n\\n## Batter / Pitcher Context\\n<2-3 sentences: platoon advantage, lineup spot, BABIP regression signal, K-rate trend, pitch count trajectory — cite specific multipliers>\\n\\n## Recent Form\\n<2 sentences: summarise last 4-5 game log values with specific numbers to support the direction>\\n\\n## Key Risk\\n<1-2 sentences: the single factor most likely to invalidate this pick, and why the model still favours the stated direction>"
}}"""

    # Gemini AI synthesis
    try:
        import json, re
        from ai_engine import _ai_call as _mlb_ai
        raw = await _mlb_ai(prompt, temperature=0.4, max_tokens=1400, timeout=12, json_mode=True)
        if raw:
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

        # BallDontLie's search endpoint omits team for traded/recently-moved players.
        # Enrich by fetching the full player record (cached at 2h TTL) for the top 8
        # active results that are missing team data.
        enriched: list = []
        fetch_tasks = []
        fetch_indices = []
        for i, p in enumerate(players[:8]):
            if p.get("active") and not p.get("team"):
                fetch_tasks.append(mlb_client.get_player(p["id"]))
                fetch_indices.append(i)

        if fetch_tasks:
            import asyncio
            fetched = await asyncio.gather(*fetch_tasks, return_exceptions=True)
            for idx, result in zip(fetch_indices, fetched):
                if isinstance(result, dict) and result:
                    players[idx] = result

        # Sort: full-query match first, then active, then has-team, then alphabetical.
        # BDL returns players in ID order (oldest first), so without this sort
        # an active rookie like "Sal Stewart" (id≈3M) would be buried under a
        # dozen retired veterans named "Stewart".
        q_words = q.lower().split()
        def _rank(p):
            full = (p.get("full_name") or "").lower()
            full_match  = 0 if all(w in full for w in q_words) else 1
            is_active   = 0 if p.get("active") else 1
            has_team    = 0 if p.get("team") else 1
            return (full_match, is_active, has_team, full)
        players.sort(key=_rank)

        def _team(p):
            t = p.get("team") or {}
            # BDL MLB team uses display_name, not full_name — normalise both keys
            if t and "full_name" not in t:
                t["full_name"] = (t.get("display_name") or
                                  f"{t.get('location','')} {t.get('name','')}".strip())
            return t

        def _full_name(p):
            fn = p.get("full_name") or ""
            if not fn.strip():
                fn = f"{p.get('first_name','') or ''} {p.get('last_name','') or ''}".strip()
            return fn or None  # None so we can filter these out below

        result_list = [
            {
                "id":        p.get("id"),
                "fullName":  _full_name(p),
                "firstName": p.get("first_name"),
                "lastName":  p.get("last_name"),
                "position":  p.get("position", ""),
                "team":      _team(p),
                "active":    p.get("active", True),
                "jersey":    p.get("jersey"),
                "batsThrows":p.get("bats_throws"),
                "age":       p.get("age"),
            }
            for p in players
            if _full_name(p)  # drop nameless records
        ]
        return result_list
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"MLB player search failed: {e}")


# ── Teams ─────────────────────────────────────────────────────────────────────

@router.get("/next-match")
async def mlb_next_match(player_id: int = Query(...)):
    """Return the next upcoming MLB game for a player's team (for auto-fill)."""
    try:
        result = await mlb_client.get_player_next_match(player_id)
        return result
    except Exception as e:
        log.warning(f"[MLB NEXT MATCH ROUTE] player_id={player_id}: {e}")
        return {"found": False}


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


# ── Game Context (auto-fill) ───────────────────────────────────────────────────

@router.get("/game-context")
async def get_game_context(
    teamName: str = Query(""),
    teamAbbr: str = Query(""),
    playerId: int = Query(0),
    season:   int = Query(2026),
):
    """
    Returns today's game context for a team:
    - Probable opponent pitcher: name, hand (L/R), season ERA
    - Player lineup spot (only available ~2h before first pitch)
    Used by the mobile app to auto-fill MLB prediction fields.
    """
    try:
        result = await mlb_client.get_game_context(
            team_name=teamName,
            team_abbr=teamAbbr,
            player_id=playerId,
            season=season,
        )
        return result
    except Exception as e:
        log.warning(f"[MLB GAME CTX] {e}")
        return {"error": str(e), "probablePitcher": None, "lineupSpot": None}


# ── Predict ───────────────────────────────────────────────────────────────────

class MlbPredictRequest(BaseModel):
    email:             str = ""
    token:             str = ""
    playerName:        str
    playerId:          Optional[int] = None
    teamName:          Optional[str] = ""
    position:          Optional[str] = ""
    propType:          str
    line:              float
    opponentName:      Optional[str] = ""
    venue:             Optional[str] = "home"
    season:            Optional[int] = CURRENT_MLB_SEASON
    pitcherName:       Optional[str] = ""    # opposing SP name if known
    # ── v2 Ultra parameters ──────────────────────────────────────────────────
    pitcherHandedness: Optional[str] = None  # 'L' or 'R' — opposing pitcher
    batterHandedness:  Optional[str] = None  # 'L', 'R', or 'S' — this batter
    pitcherEra:        Optional[float] = None # opposing pitcher's current-season ERA
    gameTotal:         Optional[float] = None # game O/U total line
    lineupSpot:        Optional[int]   = None # batting order 1-9


@router.post("/predict")
async def mlb_predict(req: MlbPredictRequest):
    from routes.auth import verify_session
    sess = await verify_session(req)
    if not sess.get("valid"):
        raise HTTPException(status_code=401, detail="Invalid or expired session. Please sign in again.")
    access = sess.get("access_type", "")
    if not access or access == "NoSubscription":
        raise HTTPException(status_code=403, detail="Active subscription required")

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
            best_match = active[0] if active else results[0]
            player_id = best_match.get("id")
            # Always fetch the full player record — search results omit team for traded players
            player_data = await mlb_client.get_player(player_id) or best_match

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

    # ── Large-BDL-ID remap ────────────────────────────────────────────────────
    # BDL's own database can assign player IDs ≥ 100k (e.g. 4668116 for Andrew
    # Painter).  These are BDL internal IDs, NOT MLB Stats API IDs — but our
    # routing sends them to the MLB Stats API, which returns "Object not found".
    # When data is empty for such a player, search MLB Stats API by name to find
    # the real statsapi ID (e.g. 691725) and retry data fetching.
    _STATSAPI_THRESHOLD = mlb_client._STATSAPI_ID_THRESHOLD
    if (not game_logs and not season_stats and not prev_season_stats
            and player_id >= _STATSAPI_THRESHOLD and req.playerName):
        try:
            statsapi_candidates = await mlb_client._statsapi_search_players(
                req.playerName, limit=5
            )
            for sp in statsapi_candidates:
                alt_id = sp.get("id", 0)
                if alt_id and alt_id != player_id:
                    print(f"[MLB PREDICT] Large-BDL ID remap: {player_id}→{alt_id} "
                          f"for {req.playerName}")
                    alt_team_id = (sp.get("team") or {}).get("id", 0) or team_id
                    alt_logs, alt_ss, alt_ps, alt_tg = await _fetch_mlb_data(
                        alt_id, req.season, team_id=alt_team_id
                    )
                    if alt_logs or alt_ss or alt_ps:
                        player_id      = alt_id
                        team_id        = alt_team_id
                        game_logs      = alt_logs
                        season_stats   = alt_ss
                        prev_season_stats = alt_ps
                        team_games     = alt_tg or team_games
                        break
        except Exception as _e:
            log.warning(f"[MLB PREDICT] ID remap attempt failed: {_e}")

    if not game_logs and not season_stats and not prev_season_stats:
        raise HTTPException(
            status_code=404,
            detail=f"No stats found for {req.playerName} in recent MLB seasons. "
                   f"They may not have played recently or may not be in the database."
        )

    # ── Team fallback: derive from season stats when player object had no team ──
    # (happens for recently traded players where BallDontLie player record lags)
    if not team_name and season_stats and season_stats.get("team_name"):
        team_name = season_stats["team_name"]
        print(f"[MLB PREDICT] Team resolved from season_stats: {team_name}")

    if not team_name and game_logs:
        # Use the team_name from the most recent current-season game log entry
        for gl in game_logs:
            tn = gl.get("team_name", "")
            if tn:
                team_name = tn
                print(f"[MLB PREDICT] Team resolved from game log: {team_name}")
                break

    # If we now have a team name but still no team_id, look it up from the teams list
    if team_name and not team_id:
        try:
            all_teams = await mlb_client.get_teams()
            team_lower = team_name.lower()
            for t in all_teams:
                dn = t.get("display_name", "").lower()
                nm = t.get("name", "").lower()
                loc = t.get("location", "").lower()
                if team_lower in dn or dn in team_lower or nm in team_lower or loc in team_lower:
                    team_id = t.get("id", 0)
                    team_name = t.get("display_name", team_name)
                    print(f"[MLB PREDICT] Resolved team_id={team_id} for '{team_name}'")
                    break
        except Exception as e:
            log.warning(f"[MLB PREDICT] Team ID lookup failed: {e}")

    # If team_id was just found but team_games weren't fetched yet, fetch them now
    if team_id and not team_games:
        try:
            team_games = await mlb_client.get_team_games(team_id, req.season)
            log.info(f"[MLB PREDICT] Deferred team_games fetch: {len(team_games)} games for team_id={team_id}")
        except Exception as e:
            log.warning(f"[MLB PREDICT] Deferred team_games fetch failed: {e}")
            team_games = []

    log.info(f"[MLB PREDICT] team_games fetched: {len(team_games)} regular-season games for team_id={team_id}")

    # ── Determine park team (home team owns the ballpark) ─────────────────────
    # home game → player's own team park; away game → opponent's park
    park_team = team_name if venue == "home" else (req.opponentName or "")

    # ── Auto game total from BDL /odds when the user didn't supply one ────────
    # Median total across vendors (fanduel/draftkings/caesars/...) for today's
    # game. Best-effort: any failure leaves game_total as None (neutral factor).
    effective_game_total = req.gameTotal
    game_total_source    = "user" if req.gameTotal is not None else None
    odds = None
    if effective_game_total is None and team_id:
        try:
            todays = await mlb_client.get_today_and_live_games(team_id, req.season)
            gid = todays[0].get("id") if todays else None
            if gid:
                odds = await mlb_client.get_game_odds(gid)
                if odds:
                    if odds.get("gameTotal") is not None:
                        effective_game_total = float(odds["gameTotal"])
                        game_total_source    = "odds"
                        log.info(f"[MLB PREDICT] Auto game total from odds: "
                                 f"O/U {effective_game_total} (game {gid}, "
                                 f"{odds.get('vendorCount', 0)} vendors)")
                    # Store moneyline so the UI can display it
                    if odds.get("moneylineHome") is not None or odds.get("moneylineAway") is not None:
                        log.info(f"[MLB PREDICT] Moneyline fetched for game {gid}")
        except Exception as e:
            log.warning(f"[MLB PREDICT] Auto game-total fetch failed (non-fatal): {e}")

    # ── Normalize v2 handedness params ────────────────────────────────────────
    pitcher_hand = (req.pitcherHandedness or "").upper().strip() or None
    batter_hand  = (req.batterHandedness  or "").upper().strip() or None
    if pitcher_hand and pitcher_hand not in ("L", "R"):
        pitcher_hand = None
    if batter_hand and batter_hand not in ("L", "R", "S"):
        batter_hand = None

    # ── Run engine v2 ─────────────────────────────────────────────────────────
    result = mlb_engine.compute_mlb_projection(
        game_logs           = game_logs,
        season_stats        = season_stats,
        prop_type           = prop_type,
        line                = req.line,
        venue               = venue,
        position            = position,
        prev_season_stats   = prev_season_stats,
        park_team           = park_team,
        pitcher_handedness  = pitcher_hand,
        batter_handedness   = batter_hand,
        pitcher_era         = req.pitcherEra,
        game_total          = effective_game_total,
        lineup_spot         = req.lineupSpot,
    )

    # ── Enrich game log tiles with opponent/date/venue from team schedule ──────
    if team_games and result.get("gameLogs"):
        result["gameLogs"] = _enrich_game_logs(
            result["gameLogs"], team_games, team_name
        )

    bm = result.get("bayesianMetrics", {})

    # ── Run AI analysis concurrently (non-blocking — falls back to empty) ─────
    ai_task = asyncio.create_task(_get_mlb_ai_analysis(
        player_name        = req.playerName,
        position           = position,
        prop_type          = prop_type,
        line               = req.line,
        venue              = venue,
        opponent           = req.opponentName or "",
        projection         = result["projection"],
        p_over             = result["pOver"],
        p_under            = result["pUnder"],
        recommendation     = result["recommendation"],
        game_logs          = result["gameLogs"],
        momentum_label     = result["momentumLabel"],
        prior_mean         = result["priorMean"],
        streak_flag        = result["streakFlag"],
        pitcher_name       = req.pitcherName or "",
        park_team          = park_team,
        park_factor_pct    = bm.get("parkFactorPct", 0.0),
        early_exit_risk    = bm.get("earlyExitRisk", False),
        zero_k_count       = bm.get("zeroKCount", 0),
        # v2 factors
        pitcher_handedness = pitcher_hand or "",
        batter_handedness  = batter_hand  or "",
        pitcher_era        = req.pitcherEra,
        game_total         = effective_game_total,
        lineup_spot        = req.lineupSpot,
        platoon_mult       = bm.get("platoonSplitMult", 1.0),
        era_mult           = bm.get("eraFactor", 1.0),
        total_mult         = bm.get("gameTotalFactor", 1.0),
        lineup_mult        = bm.get("lineupPositionMult", 1.0),
        babip_mult         = bm.get("babipMult", 1.0),
        rolling_babip      = bm.get("rollingBabip"),
        krate_mult         = bm.get("kRateMult", 1.0),
        rolling_k_rate     = bm.get("rollingKRate"),
        pitcher_role       = bm.get("pitcherRole", ""),
        pitch_traj_mult    = bm.get("pitchTrajMult", 1.0),
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
        "gameTotalUsed":  effective_game_total,
        "gameTotalSource": game_total_source,
        "generatedAt":    datetime.now(timezone.utc).isoformat(),
    }

    # Attach moneyline when we fetched odds
    if odds and (odds.get("moneylineHome") is not None or odds.get("moneylineAway") is not None):
        response["moneyline"] = {
            "home": odds.get("moneylineHome"),
            "away": odds.get("moneylineAway"),
        }

    # Await AI result and merge into response — hard 12 s cap so a slow AI
    # never blocks the full predict response from reaching the user.
    try:
        ai_data = await asyncio.wait_for(asyncio.shield(ai_task), timeout=12)
        if ai_data:
            response["sharpSummary"]      = ai_data.get("sharpSummary", "")
            response["reasoning"]         = ai_data.get("reasoning", "")
            response["tacticalBreakdown"] = ai_data.get("tacticalBreakdown", "")
            print(f"[MLB AI] summary: {str(ai_data.get('sharpSummary',''))[:80]} | td={len(ai_data.get('tacticalBreakdown',''))}c")
    except Exception as e:
        log.warning(f"[MLB AI] timed out or failed: {e}")
        response.setdefault("sharpSummary", "")
        response.setdefault("reasoning", "")
        response.setdefault("tacticalBreakdown", "")

    # ── Standard matchupOverview (unified UI — works for all sports) ─────────
    _home_team = team_name if venue == "home" else (req.opponentName or "Opponent")
    _away_team = (req.opponentName or "Opponent") if venue == "home" else team_name
    _gt = effective_game_total
    if _gt is not None and _gt >= 10:
        _game_type = "High-scoring game"
    elif _gt is not None and _gt <= 7:
        _game_type = "Pitcher's duel"
    else:
        _game_type = "Balanced matchup"
    _bm = response.get("bayesianMetrics", {})
    _factors = []
    _park = _bm.get("parkFactorPct", 0.0) or 0.0
    if abs(_park) >= 2:
        _factors.append(f"Park {'+' if _park >= 0 else ''}{_park:.1f}%")
    _era = _bm.get("eraFactor", 1.0) or 1.0
    if abs(_era - 1.0) > 0.05:
        _factors.append("ERA favors " + ("batter" if _era > 1.0 else "pitcher"))
    _plat = _bm.get("platoonSplitMult", 1.0) or 1.0
    if abs(_plat - 1.0) > 0.03:
        _factors.append(f"Platoon {'+' if _plat >= 1.0 else ''}{(_plat - 1) * 100:.0f}%")
    response["matchupOverview"] = {
        "homeTeam":         _home_team,
        "awayTeam":         _away_team,
        "playerIsHome":     venue == "home",
        "expectedGameType": _game_type,
        "keyMatchupFactor": " | ".join(_factors) if _factors else None,
    }
    if response.get("moneyline"):
        response["matchupOverview"]["moneyline"] = response["moneyline"]

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
    Fetches up to 3 seasons (current, season-1, season-2) and backfills game
    logs so players with limited recent data (e.g. returning from Tommy John)
    still get a full 30-game history.  season-2 stats are used as a fallback
    for prev_season_stats when both current and season-1 are empty."""
    import asyncio

    async def _empty_list(): return []

    game_logs_task      = mlb_client.get_player_game_logs(player_id, season,     limit=30)
    prev_logs_task      = mlb_client.get_player_game_logs(player_id, season - 1, limit=30)
    prev2_logs_task     = mlb_client.get_player_game_logs(player_id, season - 2, limit=30)
    season_stats_task   = mlb_client.get_season_stats(player_id, season)
    prev_stats_task     = mlb_client.get_season_stats(player_id, season - 1)
    prev2_stats_task    = mlb_client.get_season_stats(player_id, season - 2)
    team_games_task     = mlb_client.get_team_games(team_id, season) if team_id else _empty_list()

    game_logs, prev_logs, prev2_logs, season_stats, prev_stats, prev2_stats, team_games = \
        await asyncio.gather(
            game_logs_task, prev_logs_task, prev2_logs_task,
            season_stats_task, prev_stats_task, prev2_stats_task,
            team_games_task,
            return_exceptions=True,
        )

    if isinstance(game_logs,    Exception): game_logs    = []
    if isinstance(prev_logs,    Exception): prev_logs    = []
    if isinstance(prev2_logs,   Exception): prev2_logs   = []
    if isinstance(season_stats, Exception): season_stats = None
    if isinstance(prev_stats,   Exception): prev_stats   = None
    if isinstance(prev2_stats,  Exception): prev2_stats  = None
    if isinstance(team_games,   Exception): team_games   = []

    # Backfill with previous seasons so we always have up to 30 games of history
    if len(game_logs) < 30 and prev_logs:
        needed = 30 - len(game_logs)
        game_logs = list(game_logs) + list(prev_logs[:needed])
    if len(game_logs) < 30 and prev2_logs:
        needed = 30 - len(game_logs)
        game_logs = list(game_logs) + list(prev2_logs[:needed])

    # If season-1 stats are also missing, fall back to season-2 stats
    if prev_stats is None and prev2_stats is not None:
        prev_stats = prev2_stats

    return game_logs, season_stats, prev_stats, team_games


def _enrich_game_logs(display_logs: list, team_games: list, player_team_name: str) -> list:
    """
    Date-based match per-game stat entries to team schedule games.
    Adds: gameDate, opponent (abbreviation), isHome, homeScore, awayScore.
    Falls back gracefully — unmatched entries keep their existing fields.

    Uses date-based lookup (not positional) so players who miss games due to
    injury/rest don't get the wrong opponent label.
    """
    if not team_games:
        return display_logs

    team_lower = (player_team_name or "").lower().strip()

    # Build date → game lookup (prefer exact match; handle doubleheaders by keeping first)
    games_by_date: dict = {}
    for game in team_games:
        d = (game.get("date") or "")[:10]
        if d and d not in games_by_date:
            games_by_date[d] = game

    def _enrich_one(log: dict) -> dict:
        log_date = (log.get("date") or log.get("gameDate") or "")[:10]
        game = games_by_date.get(log_date)
        if not game:
            return log

        home_obj  = game.get("home_team", {})
        away_obj  = game.get("away_team", {})
        home_full = (home_obj.get("display_name") or "").lower()

        home_match = bool(
            team_lower and (
                team_lower in home_full or
                home_full in team_lower or
                (team_lower.split() and team_lower.split()[-1] in home_full)
            )
        )
        is_home   = home_match
        opp_obj   = away_obj if is_home else home_obj
        home_runs = (game.get("home_team_data") or {}).get("runs")
        away_runs = (game.get("away_team_data") or {}).get("runs")

        score_str = (
            f"{home_runs}-{away_runs}"
            if home_runs is not None and away_runs is not None
            else None
        )
        return {
            **log,
            "gameDate":  log_date or None,
            "opponent":  opp_obj.get("abbreviation") or None,
            "isHome":    is_home,
            "venue":     "home" if is_home else "away",
            "score":     score_str,
            "homeScore": home_runs,
            "awayScore": away_runs,
        }

    return [_enrich_one(log) for log in display_logs]
