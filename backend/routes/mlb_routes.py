"""
MLB prediction routes — /api/mlb/*
"""
import asyncio
import logging
from datetime import date, datetime, timezone
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional

from config import db
import mlb_client
import mlb_engine
from engine_base import normalize_response
from saved_sport_analysis import merge_saved_analysis

log = logging.getLogger("mlb_routes")

router = APIRouter(prefix="/api/mlb", tags=["mlb"])

CURRENT_MLB_SEASON = 2026


# ── Player search ─────────────────────────────────────────────────────────────

@router.get("/players/search")
async def search_players(q: str = Query(..., min_length=2)):
    try:
        # Older mobile clients wait for soccer, MLB, and NFL search requests
        # together.  MLB is optional context during a soccer search, so a
        # provider/Stats API stall must not consume the whole dropdown timeout.
        players = await asyncio.wait_for(
            mlb_client.search_players(q, limit=15),
            timeout=1.5,
        )
    except (asyncio.TimeoutError, TimeoutError):
        log.warning("[MLB SEARCH] provider exceeded 1.5s for query=%r", q)
        return []
    except Exception as e:
        log.warning("[MLB SEARCH] unavailable for query=%r: %s", q, e)
        return []

    try:
        # BallDontLie's search endpoint omits team for traded/recently-moved players.
        # Enrich by fetching the full player record (cached at 2h TTL) for the top 8
        # active results that are missing team data.
        fetch_tasks = []
        fetch_indices = []
        for i, p in enumerate(players[:8]):
            if p.get("active") and not p.get("team"):
                fetch_tasks.append(mlb_client.get_player(p["id"]))
                fetch_indices.append(i)

        if fetch_tasks:
            try:
                fetched = await asyncio.wait_for(
                    asyncio.gather(*fetch_tasks, return_exceptions=True),
                    timeout=0.6,
                )
            except (asyncio.TimeoutError, TimeoutError):
                log.warning("[MLB SEARCH] team enrichment exceeded 0.6s for query=%r", q)
                fetched = []
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
        log.warning("[MLB SEARCH] response mapping failed for query=%r: %s", q, e)
        return []


# ── Teams ─────────────────────────────────────────────────────────────────────

@router.get("/next-match")
async def mlb_next_match(player_id: int = Query(...)):
    """Return the next upcoming MLB game for a player's team (for auto-fill)."""
    try:
        result = await mlb_client.get_player_next_match(player_id)
        # Never let a stale provider/cache response populate the prediction form.
        # MLB dates are calendar dates, so compare them against the UTC date used
        # by the backend rather than allowing yesterday's game to appear as next.
        if result.get("found"):
            game_date = str(result.get("date") or "")[:10]
            if not game_date:
                return {"found": False}
            try:
                if date.fromisoformat(game_date) < datetime.now(timezone.utc).date():
                    log.warning(
                        "[MLB NEXT MATCH] rejecting stale game player_id=%s date=%s",
                        player_id,
                        game_date,
                    )
                    return {"found": False}
            except ValueError:
                log.warning(
                    "[MLB NEXT MATCH] rejecting invalid game date player_id=%s date=%s",
                    player_id,
                    game_date,
                )
                return {"found": False}
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
    if (not game_logs and player_id >= _STATSAPI_THRESHOLD and req.playerName):
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

    # ── H2H: fetch head-to-head stats vs the opponent team ────────────────────
    # For BDL players (id < 100k): StatsAPI name search → vsTeam aggregate.
    # For StatsAPI players (id >= 100k): use player_id directly as the SA ID.
    # Non-fatal: any error leaves h2h_stats = None (neutral / no adjustment).
    h2h_stats = None
    if req.opponentName and req.playerName:
        _PITCHER_POSITIONS_SET = {"SP", "RP", "P", "CL", "SU", "MR", "LR"}
        _h2h_group = "pitching" if (position or "").upper() in _PITCHER_POSITIONS_SET else "hitting"
        try:
            _h2h_sa_id = player_id if player_id >= _STATSAPI_THRESHOLD else 0
            h2h_stats = await mlb_client.get_player_h2h_stats(
                player_name        = req.playerName,
                opp_name           = req.opponentName,
                season             = req.season,
                group              = _h2h_group,
                player_statsapi_id = _h2h_sa_id,
            )
            if h2h_stats:
                log.info(
                    f"[MLB PREDICT] H2H {req.playerName} vs {req.opponentName}: "
                    f"gp={h2h_stats['gamesPlayed']} ({h2h_stats['source']})"
                )
        except Exception as _h2h_err:
            log.warning(f"[MLB PREDICT] H2H fetch non-fatal: {_h2h_err}")

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
        h2h_stats           = h2h_stats,
    )

    # ── Enrich game log tiles with opponent/date/venue from team schedule ──────
    if team_games and result.get("gameLogs"):
        result["gameLogs"] = _enrich_game_logs(
            result["gameLogs"], team_games, team_name
        )

    # ── StatsAPI positional enrichment for BDL players (no dates from /stats) ─
    # BDL /stats never includes dates or opponent info. For players with id < 100k,
    # look them up in MLB Stats API by name and merge date/opponent/isHome/venue/won.
    if player_id < _STATSAPI_THRESHOLD and result.get("gameLogs"):
        result["gameLogs"] = await _statsapi_enrich_game_logs(
            result["gameLogs"], req.playerName, position, req.season
        )

    bm = result.get("bayesianMetrics", {})

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
        "position":       position,
        "role":            "Pitcher" if prop_type in mlb_engine.PITCHER_PROPS else "Batter",
        "pitcherName":    req.pitcherName or "",
        "pitcherHandedness": pitcher_hand,
        "batterHandedness": batter_hand,
        "pitcherEra":     req.pitcherEra,
        "lineupSpot":     req.lineupSpot,
        "gameTotalUsed":  effective_game_total,
        "gameTotalSource": game_total_source,
        "gameTotal":      effective_game_total,
        "generatedAt":    datetime.now(timezone.utc).isoformat(),
    }

    # Attach moneyline when we fetched odds
    ml_h = odds.get("moneylineHome") if odds else None
    ml_a = odds.get("moneylineAway") if odds else None
    # Sanity check: real MLB moneylines live between -2000 and +2000.
    # BDL sometimes returns garbage (e.g. -10000 / +1625) when data is bad.
    if ml_h is not None and ml_a is not None and abs(ml_h) <= 2000 and abs(ml_a) <= 2000:
        response["moneyline"] = {"home": ml_h, "away": ml_a}
    elif ml_h is not None or ml_a is not None:
        log.warning(f"[MLB PREDICT] Rejected out-of-range moneyline home={ml_h} away={ml_a}")
    # MLB used to explicitly erase these fields, leaving a math-only result
    # even though the shared deterministic explanation layer can describe the
    # actual baseball factors without inventing a narrative.
    from deterministic_explanations import build_sport_deterministic_explanation
    from prop_safety_cache import get_prop_safety as _get_prop_safety
    response["historyGameCount"] = len(game_logs)
    response["historySeasons"] = sorted({
        int(g.get("season"))
        for g in game_logs
        if str(g.get("season", "")).isdigit()
    })
    if response["historySeasons"]:
        response["historyRange"] = {
            "min": min(response["historySeasons"]),
            "max": max(response["historySeasons"]),
        }
    # Attach empirical safety data so the explanation layer can surface AVOID evidence.
    _mlb_rec_upper = str(response.get("recommendation") or "").upper()
    if _mlb_rec_upper in {"OVER", "UNDER"}:
        _mlb_safety_data = _get_prop_safety(
            prop_type,
            _mlb_rec_upper,
            league_id=None,
            position=position or "",
        )
        if _mlb_safety_data:
            response["safetyRating"] = _mlb_safety_data.get("safety", "RISKY")
            response["propHistoricalRate"] = _mlb_safety_data.get("hitRate")
            response["propHistoricalN"] = _mlb_safety_data.get("n")
        else:
            response.setdefault("safetyRating", "RISKY")
    build_sport_deterministic_explanation(response, "mlb")

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

    return normalize_response(response)


@router.get("/picks/{pick_id}/analysis")
async def get_mlb_saved_analysis(
    pick_id: str,
    email: str = Query(...),
    token: str = Query(...),
):
    """Return the complete durable analysis for one saved MLB pick."""
    session = await db.sessions.find_one(
        {"email": email.lower().strip(), "session_token": token},
        {"_id": 0},
    )
    if not session:
        raise HTTPException(status_code=401, detail="Invalid session")

    pick = await db.picks.find_one(
        {"pickId": pick_id, "email": email.lower().strip(), "sport": "mlb"},
        {"_id": 0},
    )
    if not pick:
        raise HTTPException(status_code=404, detail="MLB pick not found")

    prop_type = str(pick.get("propType") or "")
    prediction = None
    prediction_filter = {
        "propType": prop_type,
        "line": pick.get("line"),
    }
    if pick.get("playerId"):
        prediction = await db.mlb_predictions.find_one(
            {"playerId": pick["playerId"], **prediction_filter},
            {"_id": 0},
            sort=[("generatedAt", -1)],
        )
    if not prediction and pick.get("playerName"):
        prediction = await db.mlb_predictions.find_one(
            {
                "playerName": {"$regex": f"^{str(pick['playerName'])}$", "$options": "i"},
                **prediction_filter,
            },
            {"_id": 0},
            sort=[("generatedAt", -1)],
        )

    analysis = merge_saved_analysis(pick, prediction, "mlb")
    return {"found": True, "sport": "mlb", "analysis": analysis}


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

    # Preserve the multi-season evidence set.  The engine caps the sample used
    # for projection math, while the response keeps all fetched rows and their
    # season provenance for the history view.
    merged = []
    for source_season, rows in ((season, game_logs), (season - 1, prev_logs), (season - 2, prev2_logs)):
        for row in rows:
            enriched = dict(row)
            enriched["season"] = source_season
            merged.append(enriched)
    game_logs = merged

    # If season-1 stats are also missing, fall back to season-2 stats
    if prev_stats is None and prev2_stats is not None:
        prev_stats = prev2_stats

    return game_logs, season_stats, prev_stats, team_games


async def _statsapi_enrich_game_logs(game_logs: list, player_name: str, position: str, season: int) -> list:
    """For BDL players (id < 100k) whose /stats records lack dates, fetch the
    corresponding MLB Stats API per-game schedule and positionally merge
    date / opponent / isHome / venue / won into the BDL logs.

    Both BDL and StatsAPI return games newest-first, so position[i] in BDL
    corresponds to position[i] in StatsAPI for the same season.  We fetch
    current + prior season logs so backfilled prior-season entries also get
    opponent labels.
    """
    if not game_logs or not player_name:
        return game_logs
    # Skip if logs already have dates (StatsAPI player or already enriched)
    if any((gl.get("date") or "")[:4].isdigit() for gl in game_logs[:5]):
        return game_logs
    try:
        _PITCHER_POSITIONS = {"SP", "RP", "P", "CL", "SU", "MR", "LR"}
        group = "pitching" if (position or "").upper() in _PITCHER_POSITIONS else "hitting"

        candidates = await mlb_client._statsapi_search_players(player_name, limit=3)
        if not candidates:
            return game_logs
        sa_id = candidates[0]["id"]

        import asyncio as _aio
        sa_curr, sa_prev = await _aio.gather(
            mlb_client._statsapi_game_logs(sa_id, season,     group=group),
            mlb_client._statsapi_game_logs(sa_id, season - 1, group=group),
            return_exceptions=True,
        )
        if isinstance(sa_curr, Exception): sa_curr = []
        if isinstance(sa_prev, Exception): sa_prev = []
        sa_all = list(sa_curr) + list(sa_prev)
        if not sa_all:
            return game_logs

        log.info(f"[MLB ENRICH] StatsAPI positional-enrich: {len(sa_all)} logs for {player_name} (sa_id={sa_id})")

        enriched = list(game_logs)
        for i, gl in enumerate(enriched):
            if i >= len(sa_all):
                break
            sa  = sa_all[i]
            gl  = dict(gl)
            gl["date"]     = sa.get("date") or gl.get("date", "")
            gl["gameDate"] = gl["date"]
            gl["opponent"] = sa.get("opponent") or gl.get("opponent")
            if sa.get("isHome") is not None:
                gl["isHome"] = sa["isHome"]
                gl["venue"]  = sa.get("venue") or gl.get("venue")
            if sa.get("won") is not None:
                gl["won"] = sa["won"]
            if sa.get("game_id"):
                gl["game_id"] = sa["game_id"]
            enriched[i] = gl
        return enriched
    except Exception as _e:
        log.warning(f"[MLB ENRICH] StatsAPI positional-enrich failed for {player_name}: {_e}")
        return game_logs


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
    games_by_id: dict = {}
    for game in team_games:
        d = (game.get("date") or "")[:10]
        if d and d not in games_by_date:
            games_by_date[d] = game
        gid = game.get("id")
        if gid:
            games_by_id[gid] = game

    def _enrich_one(log: dict) -> dict:
        log_date = (log.get("date") or log.get("gameDate") or "")[:10]
        game = games_by_date.get(log_date)
        # BDL /stats logs have no date — fall back to game_id lookup
        if not game:
            gid = log.get("game_id")
            if gid:
                game = games_by_id.get(gid)
                if game:
                    log_date = (game.get("date") or "")[:10]
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
        # Determine win/loss for the player's team
        won = None
        if home_runs is not None and away_runs is not None:
            won = (home_runs > away_runs) if is_home else (away_runs > home_runs)

        # Get opponent name/abbreviation — prefer abbreviation, fall back to name
        opp_abbr = (
            opp_obj.get("abbreviation") or
            opp_obj.get("full_name") or
            opp_obj.get("display_name") or
            None
        )
        return {
            **log,
            "gameDate":  log_date or None,
            "opponent":  opp_abbr,
            "isHome":    is_home,
            "venue":     "home" if is_home else "away",
            "score":     score_str,
            "homeScore": home_runs,
            "awayScore": away_runs,
            "won":       won,
        }

    return [_enrich_one(log) for log in display_logs]
