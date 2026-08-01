import json
import os
import re
import uuid
import hashlib
import asyncio as aio
import statistics as stats_mod
import traceback
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException
from emergentintegrations.llm.chat import LlmChat, UserMessage

from openai import OpenAI

from config import (
    db, EMERGENT_LLM_KEY, XAI_API_KEY, CURRENT_SEASON,
    WOMENS_LEAGUE_IDS, STAT_FIELD_MAP, STAT_LAMBDA_MAP, GROK_MODEL,
    INTERNATIONAL_LEAGUES, NATIONAL_TEAM_TIER, GEMINI_AI_ENABLED,
)
from models import PredictionRequest
from utils import (
    api_football_request, get_recent_fixtures_fast, strip_accents, get_soccer_odds,
    decimal_to_american, set_api_request_priority, reset_api_request_priority,
    resolve_verified_fixture,
)
from ai_engine import fetch_web_intel, fetch_ai_press_intensity
from prop_safety_cache import (
    get_prop_safety as _get_prop_safety,
    get_recent_prop_safety as _get_recent_prop_safety,
)
import soccer_bdl_client as _bdl_soc
# game_script_intelligence removed — was distorting confidence scores for GK pass picks

router = APIRouter(prefix="/api", tags=["predict"])

# H2H history is intentionally broader than the current-season prediction
# window. The player-specific pass still caps the displayed sample so older
# meetings cannot dominate the model, but it must inspect enough real fixtures
# to find 4-5+ appearances when they exist.
H2H_HISTORY_SEASONS = 6
H2H_FIXTURE_LIMIT = 20
H2H_PLAYER_SCAN_LIMIT = 20
H2H_PLAYER_RESULT_LIMIT = 10
_H2H_FINISHED_STATUSES = {"FT", "AET", "PEN", "AWD", "WO"}


def _merge_h2h_fixtures(*responses: list, limit: int = H2H_FIXTURE_LIMIT) -> list:
    """Merge real API-Football H2H responses into newest-first finished games."""
    by_id = {}
    for response in responses:
        if not isinstance(response, list):
            continue
        for fixture in response:
            if not isinstance(fixture, dict):
                continue
            fixture_id = (fixture.get("fixture") or {}).get("id")
            status = ((fixture.get("fixture") or {}).get("status") or {}).get("short")
            if not fixture_id or status not in _H2H_FINISHED_STATUSES:
                continue
            by_id[str(fixture_id)] = fixture

    return sorted(
        by_id.values(),
        key=lambda item: (item.get("fixture") or {}).get("date", ""),
        reverse=True,
    )[:limit]

# ── CALIBRATION TOGGLE ────────────────────────────────────────────────────────
# Nightly-learned bias offsets from historical pick outcomes.
# Priority: prop_rec (direction) > prop_league > prop_venue > prop (general).
# Direction offsets are the strongest signal — applied first.
# Each offset is dampened to 40% of raw mean error and capped at ±20% of posterior.
CALIBRATION_ENABLED = False  # Disabled — raw Bayesian projections proved more accurate than the learned-offset corrections
# ─────────────────────────────────────────────────────────────────────────────

# Match dominance cache: keyed by (home_team_id, away_team_id)
# Ensures the SAME game always returns identical possession numbers regardless of which player is scanned.
import time as _time
_match_dom_cache: dict = {}
_MATCH_DOM_TTL = 3600 * 6  # 6 hours

# ── Background AI synthesis task registry ─────────────────────────────────────
# asyncio.create_task() does NOT keep a strong reference to the task by itself —
# if the only reference (a local variable in the request handler) goes out of
# scope once the HTTP response is returned, the event loop is free to garbage
# collect the task mid-flight, silently killing the AI synthesis before it ever
# writes to ai_response_cache/ai_pending_jobs. This was the root cause of
# "AI analysis loading..." getting stuck forever on some picks. Keeping a
# strong reference in this module-level set (with a done-callback to clean up)
# guarantees every fired background synthesis task actually runs to completion.
_bg_ai_tasks: set = set()

def _track_bg_task(task):
    _bg_ai_tasks.add(task)
    task.add_done_callback(_bg_ai_tasks.discard)
    return task


def _fixture_matchup(fixture: dict, team_id: int) -> dict | None:
    """Return the canonical matchup for team_id from an API-Football fixture."""
    home = fixture.get("teams", {}).get("home", {}) or {}
    away = fixture.get("teams", {}).get("away", {}) or {}
    if home.get("id") == team_id:
        player_team, opponent = home, away
        player_is_home = True
    elif away.get("id") == team_id:
        player_team, opponent = away, home
        player_is_home = False
    else:
        return None
    if not player_team.get("id") or not opponent.get("id"):
        return None
    return {
        "fixtureTeamId": player_team.get("id"),
        "fixtureTeamName": player_team.get("name", ""),
        "fixtureOpponentId": opponent.get("id"),
        "fixtureOpponentName": opponent.get("name", ""),
        "playerIsHome": player_is_home,
    }


def _select_player_context_for_league(
    docs: list[dict],
    league_id: int,
    requested_team_id: int = 0,
) -> dict | None:
    """Choose the player's club context for the selected competition.

    A single player ID can have both a national-team cache record and a club
    record.  The request's selected league is the authoritative context for
    fixture resolution; a national-team row must not make a Liga MX request
    resolve fixtures for Mexico instead of the player's Liga MX club.
    """
    if not league_id or league_id in INTERNATIONAL_LEAGUES:
        return None
    candidates = [
        d for d in docs
        if d.get("teamId") and d.get("leagueId") == league_id
    ]
    if not candidates:
        return None
    if requested_team_id:
        for doc in candidates:
            if doc.get("teamId") == requested_team_id:
                return doc
    # Prefer a real club row when a league has multiple cache contexts.
    return next(
        (d for d in candidates if d.get("leagueId") not in INTERNATIONAL_LEAGUES),
        candidates[0],
    )


@router.post("/predict")
async def predict(req: PredictionRequest):
    from routes.auth import verify_session
    sess = await verify_session(req)
    if not sess.get("valid"):
        raise HTTPException(status_code=401, detail="Invalid or expired session. Please sign in again.")
    access = sess.get("access_type", "")
    if not access or access == "NoSubscription":
        raise HTTPException(status_code=403, detail="Active subscription required")
    # User-triggered predictions must not be starved by the shared background
    # soft budget. The provider's actual 429/daily-quota response still trips
    # the real circuit breaker in utils.py.
    _priority_token = set_api_request_priority(True)
    try:
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        # Ordered numeric audit trail for the explanation layer.  This is
        # intentionally separate from analysisFactors: analysisFactors describe
        # evidence quality, while this ledger describes how the displayed
        # projection was actually transformed.
        _factor_ledger: list[dict] = []

        def _ledger_num(value):
            try:
                return round(float(value), 4) if value is not None else None
            except (TypeError, ValueError):
                return None

        def _record_projection_factor(
            factor_id: str,
            title: str,
            before,
            after,
            *,
            status: str = "applied",
            reason: str = "",
            inputs: dict | None = None,
            sample_size=None,
            multiplier=None,
        ):
            b = _ledger_num(before)
            a = _ledger_num(after)
            _factor_ledger.append({
                "id": factor_id,
                "title": title,
                "status": status,
                "before": b,
                "after": a,
                "delta": _ledger_num(a - b) if a is not None and b is not None else None,
                "direction": (
                    "up" if a is not None and b is not None and a > b
                    else "down" if a is not None and b is not None and a < b
                    else "neutral"
                ),
                "multiplier": _ledger_num(multiplier),
                "sampleSize": sample_size,
                "inputs": inputs or {},
                "reason": reason,
            })

        def _record_confidence_control(control_id: str, title: str, before, after, reason: str):
            _factor_ledger.append({
                "id": control_id,
                "title": title,
                "status": "applied" if before != after else "measured",
                "before": _ledger_num(before),
                "after": _ledger_num(after),
                "delta": _ledger_num(after - before) if before is not None and after is not None else None,
                "direction": "down" if after is not None and before is not None and after < before else "neutral",
                "multiplier": None,
                "sampleSize": None,
                "inputs": {},
                "reason": reason,
                "kind": "confidence",
            })
        # Prediction cache REMOVED: returning stale cached predictions caused
        # contradictions (e.g., wrong possession narrative when match data changed)
        # and undermined user trust. Every request now runs full fresh analysis.
        # Results are still stored in db.predictions for analytics/top-props.

        async def safe_fetch(endpoint, params, fallback=None):
            try:
                return await api_football_request(endpoint, params)
            except Exception:
                return fallback

        async def get_h2h_history(team_id: int, opponent_id: int, league_id: int):
            """Fetch a deep, deduplicated H2H history across recent seasons.

            API-Football's headtohead endpoint is season-scoped when `season`
            is supplied. A single current-season request silently omits older
            meetings, so search the recent six provider seasons and merge them.
            """
            if not team_id or not opponent_id:
                return []

            # Current-season config is 2025 for European competitions, while
            # calendar-year leagues (and the current date) are already in 2026.
            # Starting at 2026 covers both without changing the global season
            # constant used by the rest of the prediction pipeline.
            start_season = 2026 if league_id == 254 else max(CURRENT_SEASON + 1, 2026)
            seasons = list(range(start_season, start_season - H2H_HISTORY_SEASONS, -1))
            responses = await aio.gather(
                *[
                    safe_fetch(
                        "fixtures/headtohead",
                        {
                            "h2h": f"{team_id}-{opponent_id}",
                            "season": season,
                            "last": H2H_FIXTURE_LIMIT,
                        },
                        [],
                    )
                    for season in seasons
                ],
                return_exceptions=True,
            )
            merged = _merge_h2h_fixtures(*responses)
            print(
                f"[H2H HISTORY] {team_id} vs {opponent_id}: "
                f"{len(merged)} finished meetings across seasons {seasons[0]}-{seasons[-1]}"
            )
            return merged

        async def get_player_data():
            if not req.playerId:
                return None
            # ── Local DB first (no API call if cached) ────────────────────
            try:
                from cache import get_cached_player_season_stats
                seasons_to_check = [CURRENT_SEASON + 1, CURRENT_SEASON, CURRENT_SEASON - 1, CURRENT_SEASON - 2]
                local_records = await get_cached_player_season_stats(req.playerId, seasons_to_check)
                if local_records:
                    all_data = local_records[0]
                    for rec in local_records[1:]:
                        all_data.setdefault("statistics", []).extend(rec.get("statistics", []))
                    return all_data
            except Exception:
                pass
            # ── Live API fallback (only when not yet cached) ──────────────
            # Skip for all soccer predictions — BDL is the sole data source.
            try:
                if _is_bdl_league:
                    return None
            except NameError:
                pass
            all_data = None
            for s in [CURRENT_SEASON + 1, CURRENT_SEASON, CURRENT_SEASON - 1, CURRENT_SEASON - 2]:
                try:
                    data = await api_football_request("players", {"id": req.playerId, "season": s})
                    if data:
                        entry = data[0]
                        if all_data is None:
                            all_data = entry
                        else:
                            all_data.setdefault("statistics", []).extend(entry.get("statistics", []))
                        # Write back to player_season_stats cache so future predictions
                        # survive quota exhaustion without hitting the API again
                        try:
                            _pid = entry.get("player", {}).get("id") or req.playerId
                            _doc = {
                                "_id_key": f"{_pid}_{s}",
                                "playerId": _pid,
                                "season": s,
                                "teamId": actual_team_id or 0,
                                "leagueId": league_id or 0,
                                "player": entry.get("player", {}),
                                "statistics": entry.get("statistics", []),
                                "_ts": __import__("time").time(),
                                "_dt": datetime.now(timezone.utc),
                            }
                            await db.player_season_stats.update_one(
                                {"_id_key": _doc["_id_key"]},
                                {"$set": _doc},
                                upsert=True
                            )
                        except Exception:
                            pass
                except Exception:
                    continue
            return all_data

        actual_team_id = req.teamId
        league_id = req.leagueId or 39
        # ── World Cup / International tournament mode ──────────────────────────
        # leagueId=1 = FIFA World Cup. Stats not available in API-Football for WC
        # (statistics_players=False), so we use club stats as the prior and apply
        # a neutral-venue + high-stakes treatment throughout the pipeline.
        _is_wc = False

        # ── AUTO-RESOLVE missing IDs from team/player names using local cache ──
        # This runs BEFORE ai_only_mode is decided, so predictions always have
        # real fixture data even when the scan didn't return numeric IDs.
        _resolved_opp_id = req.opponentId or 0
        _resolved_player_id = req.playerId or 0
        _resolved_team_name = req.teamName or ""
        _player_candidates: list = []  # populated when name-based resolution finds multiple matches

        try:
            from team_resolver import find_team as _find_team
            from cache import get_player_by_name as _get_player_by_name

            # 1. Resolve team ID from team name — always verify, never blindly trust req.teamId
            if req.teamName:
                try:
                    _t = await _find_team(req.teamName, league_id=league_id if league_id and league_id != 39 else None)
                    if _t and _t.get("teamId"):
                        _resolved_tid = _t["teamId"]
                        if _resolved_tid != actual_team_id:
                            print(f"[ID RESOLVE] '{req.teamName}' teamId corrected: {actual_team_id} → {_resolved_tid}")
                            actual_team_id = _resolved_tid
                        else:
                            print(f"[ID RESOLVE] '{req.teamName}' → teamId={actual_team_id} (confirmed)")
                    elif not actual_team_id or actual_team_id == 0:
                        print(f"[ID RESOLVE] '{req.teamName}' not found in local cache, keeping req.teamId={actual_team_id}")
                except Exception as _re:
                    print(f"[ID RESOLVE] team lookup failed: {_re}")

            # 2. Resolve opponent ID from opponent name — always verify.
            # Guard: if the frontend already supplied a national-team opponentId
            # (leagueId=0 from /api/search/teams) don't clobber it with a clubs hit.
            if req.opponentName:
                try:
                    from cache import COL_NATIONAL as _COL_NAT
                    _opp_is_national = req.opponentId and await db[_COL_NAT].count_documents(
                        {"teamId": req.opponentId}, limit=1
                    ) > 0
                    if _opp_is_national:
                        _resolved_opp_id = req.opponentId
                        print(f"[ID RESOLVE] '{req.opponentName}' opponentId={_resolved_opp_id} (national team — kept)")
                    else:
                        _o = await _find_team(req.opponentName)
                        if _o and _o.get("teamId"):
                            _resolved_opp_id = _o["teamId"]
                            print(f"[ID RESOLVE] '{req.opponentName}' → opponentId={_resolved_opp_id}")
                except Exception as _re:
                    print(f"[ID RESOLVE] opponent lookup failed: {_re}")

            # 3. Resolve player ID from player name
            if (not _resolved_player_id or _resolved_player_id == 0) and req.playerName:
                try:
                    # If the supplied team is a national-team context but the
                    # selected competition is domestic, do not constrain the
                    # player lookup to that national team. The player may be
                    # shown as "Mexico" in an older search result while the
                    # requested fixture is Liga MX.
                    _lookup_team_id = actual_team_id if actual_team_id and actual_team_id != 0 else None
                    _lookup_team_hint = req.teamName or None
                    if league_id not in INTERNATIONAL_LEAGUES and _lookup_team_id:
                        try:
                            from cache import COL_NATIONAL as _COL_NAT_PLAYER
                            if await db[_COL_NAT_PLAYER].count_documents(
                                {"teamId": _lookup_team_id}, limit=1
                            ) > 0:
                                _lookup_team_id = None
                                _lookup_team_hint = None
                        except Exception:
                            pass
                    _p = await _get_player_by_name(
                        req.playerName,
                        _lookup_team_id,
                        league_id=league_id if league_id and league_id != 39 else None,
                        team_name_hint=_lookup_team_hint,
                        prop_type=req.propType or None,
                    )
                    if _p and _p.get("playerId"):
                        _resolved_player_id = _p["playerId"]
                        if not actual_team_id or actual_team_id == 0:
                            actual_team_id = _p.get("teamId") or actual_team_id
                        if _p.get("teamName") and (
                            not actual_team_id or actual_team_id == _p.get("teamId")
                        ):
                            _resolved_team_name = _p.get("teamName") or _resolved_team_name
                        print(f"[ID RESOLVE] '{req.playerName}' → playerId={_resolved_player_id}, teamId={actual_team_id}")

                        # [PLAYER AMBIGUITY] If the player was resolved by name (no playerId supplied),
                        # check whether the cache holds multiple players with the same abbreviated nameClean.
                        # If so, surface all candidates in the response so the frontend can warn the user.
                        try:
                            _nc = (_p.get("nameClean") or "").strip()
                            if _nc:
                                from cache import COL_PLAYERS
                                _all_nc = await db[COL_PLAYERS].find(
                                    {"nameClean": _nc},
                                    {"playerId": 1, "name": 1, "teamName": 1, "position": 1, "leagueId": 1, "_id": 0}
                                ).to_list(15)
                                if len(_all_nc) > 1:
                                    _player_candidates = [
                                        {
                                            "playerId": m["playerId"],
                                            "playerName": m.get("name", ""),
                                            "teamName":   m.get("teamName", ""),
                                            "position":   m.get("position", ""),
                                            "leagueId":   m.get("leagueId"),
                                        }
                                        for m in _all_nc
                                    ]
                                    print(f"[PLAYER AMBIGUITY] '{_nc}' — {len(_all_nc)} candidates: "
                                          f"{[m.get('teamName','?') for m in _all_nc]}")
                        except Exception as _ae:
                            print(f"[PLAYER AMBIGUITY] check failed: {_ae}")
                except Exception as _re:
                    print(f"[ID RESOLVE] player lookup failed: {_re}")

            # A supplied playerId is still not enough to identify the team:
            # player IDs legitimately have both club and national-team cache
            # rows. For domestic requests, select the row belonging to the
            # requested league before resolving the fixture.
            if _resolved_player_id and league_id not in INTERNATIONAL_LEAGUES:
                try:
                    from cache import COL_PLAYERS as _COL_PLAYER_CONTEXT
                    _context_docs = await db[_COL_PLAYER_CONTEXT].find(
                        {"playerId": _resolved_player_id},
                        {"_id": 0, "playerId": 1, "teamId": 1, "teamName": 1, "leagueId": 1},
                    ).to_list(30)
                    _league_context = _select_player_context_for_league(
                        _context_docs, league_id, actual_team_id
                    )
                    if _league_context and _league_context.get("teamId") != actual_team_id:
                        print(
                            f"[PLAYER CONTEXT ALIGN] playerId={_resolved_player_id} "
                            f"league={league_id}: team {actual_team_id}/{req.teamName} "
                            f"→ {_league_context.get('teamId')}/{_league_context.get('teamName')}"
                        )
                        actual_team_id = _league_context["teamId"]
                        _resolved_team_name = _league_context.get("teamName") or _resolved_team_name
                except Exception as _context_err:
                    print(f"[PLAYER CONTEXT ALIGN] lookup failed: {_context_err}")

            # Bake resolved IDs back into req so all downstream references see them
            if (
                _resolved_opp_id != req.opponentId
                or _resolved_player_id != req.playerId
                or actual_team_id != req.teamId
                or _resolved_team_name != req.teamName
            ):
                req = req.model_copy(update={
                    "teamId": actual_team_id or 0,
                    "teamName": _resolved_team_name or req.teamName,
                    "opponentId": _resolved_opp_id,
                    "playerId": _resolved_player_id,
                })
        except Exception as _global_resolve_err:
            print(f"[ID RESOLVE] Global error: {_global_resolve_err}")

        ai_only_mode = (not actual_team_id or actual_team_id == 0 or not req.opponentId or req.opponentId == 0)
        if ai_only_mode:
            print(f"[ID RESOLVE] After resolution: teamId={actual_team_id}, opponentId={req.opponentId}, playerId={req.playerId}")

        # Guard: skip team/opponent API calls when IDs are missing
        safe_team_id = actual_team_id if actual_team_id and actual_team_id != 0 else None
        safe_opp_id = req.opponentId if req.opponentId and req.opponentId != 0 else None

        # Fire ALL API calls at once (optimized — kept odds for game context)
        async def get_team_stats_multi_season(team_id, lid):
            # ── Local DB first ─────────────────────────────────────────────
            try:
                from cache import get_cached_team_season_stats
                cached = await get_cached_team_season_stats(team_id, lid)
                if cached:
                    return cached
            except Exception:
                pass
            # ── Live API fallback ──────────────────────────────────────────
            for s in [CURRENT_SEASON + 1, CURRENT_SEASON, CURRENT_SEASON - 1]:
                result = await safe_fetch("teams/statistics", {"team": team_id, "league": lid, "season": s})
                if result:
                    return result
            return None

        async def get_match_odds():
            """Get bookmaker odds for the specific upcoming fixture between team and opponent.
            Uses team's next fixtures (across ALL competitions) to find the correct match."""
            try:
                canonical = await resolve_verified_fixture(
                    actual_team_id,
                    opponent_id=req.opponentId,
                    opponent_name=req.opponentName,
                    league_id=(
                        league_id
                        if league_id and league_id not in {39, 667, 666}
                        else None
                    ),
                )
                if canonical:
                    fixture_match = canonical["fixture"]
                else:
                    fixture_match = None

                if not fixture_match:
                    return None

                fid = fixture_match.get("fixture", {}).get("id")
                result = {}
                canonical_matchup = _fixture_matchup(fixture_match, actual_team_id)
                if not canonical_matchup:
                    # Never attach odds/context from a fixture that does not
                    # actually contain the requested player's team.
                    return None
                result.update(canonical_matchup)
                if fid:
                    result["fixtureId"] = fid
                # Tag whether the player's team is the API-Football fixture's home team.
                # Used later to normalise moneyline home/away keys so they always
                # correspond to real_matchup.homeTeam / awayTeam regardless of
                # how API-Football labels the fixture.
                # Extract competition context (league/cup name + round)
                match_round = fixture_match.get("league", {}).get("round", "")
                match_league = fixture_match.get("league", {}).get("name", "")
                match_league_id = fixture_match.get("league", {}).get("id")
                match_date = fixture_match.get("fixture", {}).get("date", "")
                if match_round:
                    result["matchRound"] = match_round
                if match_league:
                    result["matchLeague"] = match_league
                if match_league_id:
                    result["matchLeagueId"] = match_league_id  # actual competition (e.g. Europa League = 3)
                if match_date:
                    result["matchDate"] = match_date
                try:
                    odds = await api_football_request("odds", {"fixture": fid})
                    if odds:
                        for bk in odds[0].get("bookmakers", [])[:1]:
                            for bet in bk.get("bets", []):
                                if bet.get("name") == "Match Winner":
                                    vals = {v["value"]: v["odd"] for v in bet.get("values", [])}
                                    result["bookmakerOdds"] = {
                                        "source": bk.get("name", ""),
                                        "homeWin": vals.get("Home", ""),
                                        "draw": vals.get("Draw", ""),
                                        "awayWin": vals.get("Away", ""),
                                    }
                                    # Convert to American odds
                                    try:
                                        home_dec = float(vals.get("Home", 0))
                                        away_dec = float(vals.get("Away", 0))
                                        draw_dec = float(vals.get("Draw", 0))
                                        result["americanOdds"] = {
                                            "home": decimal_to_american(home_dec) if home_dec else "",
                                            "away": decimal_to_american(away_dec) if away_dec else "",
                                            "draw": decimal_to_american(draw_dec) if draw_dec else "",
                                        }
                                        result["favorite"] = "home" if home_dec < away_dec else "away"
                                        # Game type from odds spread
                                        fav_odds = min(home_dec, away_dec)
                                        if fav_odds < 1.3:
                                            result["gameType"] = "HEAVY FAVORITE — expect dominant performance, possible early subs"
                                        elif fav_odds < 1.7:
                                            result["gameType"] = "CLEAR FAVORITE — should control the game"
                                        elif fav_odds < 2.2:
                                            result["gameType"] = "SLIGHT FAVORITE — competitive match expected"
                                        else:
                                            result["gameType"] = "PICK'EM — very close, could go either way"
                                    except Exception:
                                        result["favorite"] = "home" if float(vals.get("Home", 99)) < float(vals.get("Away", 99)) else "away"
                except Exception:
                    pass
                return result if result else None
            except Exception:
                return None

        # When in AI-only mode (missing IDs), skip API calls that would waste quota
        async def noop_none(): return None
        async def noop_list(): return []

        _is_bdl_league = False  # API-Football is the primary soccer data source

        # Resolve the actual fixture before launching opponent-dependent
        # requests. Previously get_match_odds() could fall back to the team's
        # next fixture while leaving the stale requested opponent in req. That
        # produced contradictory cards such as Corinthians vs Bahia when the
        # actual fixture was Corinthians vs Athletico.
        match_odds_prefetched = None
        if not ai_only_mode and actual_team_id and not _is_bdl_league:
            match_odds_prefetched = await get_match_odds()
            if not match_odds_prefetched:
                # Do not analyze a stale OCR/manual opponent when the current
                # fixture cannot be verified.  A clear retry is safer than a
                # polished prediction for the wrong game.
                raise HTTPException(
                    status_code=409,
                    detail="Could not verify the player's current or next fixture. Please retry shortly.",
                )
            _fixture_opp_id = (match_odds_prefetched or {}).get("fixtureOpponentId")
            _fixture_opp_name = (match_odds_prefetched or {}).get("fixtureOpponentName")
            _fixture_team_name = (match_odds_prefetched or {}).get("fixtureTeamName")
            if _fixture_opp_id and _fixture_opp_name:
                if (
                    _fixture_opp_id != req.opponentId
                    or _fixture_opp_name.strip().lower() != (req.opponentName or "").strip().lower()
                ):
                    print(
                        f"[FIXTURE CONTEXT ALIGN] requested={req.opponentName}({req.opponentId}) "
                        f"→ actual={_fixture_opp_name}({_fixture_opp_id}) "
                        f"fixture={(match_odds_prefetched or {}).get('fixtureId')}"
                    )
                req = req.model_copy(update={
                    "opponentId": _fixture_opp_id,
                    "opponentName": _fixture_opp_name,
                    "teamName": _fixture_team_name or req.teamName,
                    "venue": "home" if (match_odds_prefetched or {}).get("playerIsHome") else "away",
                })
                actual_team_id = (match_odds_prefetched or {}).get("fixtureTeamId") or actual_team_id

        # Recompute after canonical fixture alignment.
        safe_team_id = actual_team_id if actual_team_id and actual_team_id != 0 else None
        safe_opp_id = req.opponentId if req.opponentId and req.opponentId != 0 else None
        _manager_task = None   # set below in the API-Football branch

        if ai_only_mode:
            print(f"[AI-ONLY] Running in AI-only mode for {req.playerName} — teamId={actual_team_id}, opponentId={req.opponentId}")

            player_data_task = get_player_data() if req.playerId and req.playerId != 0 else noop_none()
            team_stats_task = noop_none()
            opponent_stats_task = noop_none()
            h2h_task = noop_list()
            standings_task = noop_none()
            fixtures_task = noop_list()
            odds_task = noop_none()
        elif _is_bdl_league:
            # BDL leagues: skip all API-Football enrichment — no H2H, odds, or fixture cache
            print(f"[BDL-GATE] Skipping API-Football Wave 1 tasks for BDL league {league_id}")
            player_data_task = get_player_data() if req.playerId and req.playerId != 0 else noop_none()
            team_stats_task = noop_none()
            opponent_stats_task = noop_none()
            h2h_task = noop_list()
            standings_task = noop_none()
            fixtures_task = noop_list()
            odds_task = noop_none()
        else:
            player_data_task = get_player_data()
            team_stats_task = get_team_stats_multi_season(actual_team_id, league_id)
            opponent_stats_task = get_team_stats_multi_season(req.opponentId, league_id)
            h2h_task = get_h2h_history(actual_team_id, req.opponentId, league_id)

            async def get_standings_multi_season():
                for s in [CURRENT_SEASON + 1, CURRENT_SEASON, CURRENT_SEASON - 1]:
                    result = await safe_fetch("standings", {"league": league_id, "season": s})
                    if result:
                        return result
                return None

            standings_task = get_standings_multi_season()
            fixtures_task = get_recent_fixtures_fast(actual_team_id, 40)
            odds_task = aio.sleep(0, result=match_odds_prefetched)

            # ── MANAGER CHANGE DETECTION (async, 7-day cached) ─────────────────
            # Runs concurrently with Wave-1 so it adds ~0 latency on cache hits.
            try:
                from manager_tracker import get_team_coach_info as _get_coach_info
                _manager_task = aio.ensure_future(
                    _get_coach_info(actual_team_id, db, api_football_request)
                )
            except Exception as _mgt_init_err:
                print(f"[MANAGER] task init error: {_mgt_init_err}")

        import time as _t
        _t0 = _t.time()
        player_stats, team_stats, opponent_stats, h2h_data, standings_raw, recent_fixtures, match_odds = await aio.gather(
            player_data_task, team_stats_task, opponent_stats_task, h2h_task, standings_task, fixtures_task, odds_task
        )
        print(f"[TIMING] Wave 1: {_t.time()-_t0:.1f}s")

        if actual_team_id == 0 and player_stats:
            _pl_nat = (player_stats.get("player") or {}).get("nationality", "")
            for _st in (player_stats.get("statistics") or []):
                _t_name = (_st.get("team") or {}).get("name", "")
                if _pl_nat and _t_name and _t_name.strip().lower() == _pl_nat.strip().lower():
                    continue
                _t_id = (_st.get("team") or {}).get("id", 0)
                if _t_id:
                    actual_team_id = _t_id
                    break

        if not league_id and player_stats:
            _pl_nat = (player_stats.get("player") or {}).get("nationality", "")
            for _st in (player_stats.get("statistics") or []):
                _t_name = (_st.get("team") or {}).get("name", "")
                if _pl_nat and _t_name and _t_name.strip().lower() == _pl_nat.strip().lower():
                    continue
                _l_id = (_st.get("league") or {}).get("id", 0)
                if _l_id:
                    league_id = _l_id
                    break
            if not league_id:
                league_id = 39

        # Recovery: if ai_only_mode skipped fixture fetching but we now have a real team ID,
        # fetch recent fixtures retroactively so the Reverse Formula has game log data.
        # Skipped for BDL leagues — BDL game logs are fetched separately.
        if actual_team_id and actual_team_id != 0 and not recent_fixtures and not _is_bdl_league:
            try:
                print(f"[FIXTURE RECOVERY] Fetching fixtures for recovered teamId={actual_team_id}")
                recent_fixtures = await get_recent_fixtures_fast(actual_team_id, 40)
            except Exception as _fre:
                print(f"[FIXTURE RECOVERY] Error: {_fre}")

        # ── SINGLE SOURCE OF TRUTH: correct club team name ──────────────────────
        # Trust req.teamName (what the user explicitly scanned) as primary.
        # Only use API-Football stats to SUPPLEMENT when req.teamName is empty.
        # Never let a national-team or historical-club entry override the user's input.
        corrected_team_name = req.teamName or ""
        if player_stats and not corrected_team_name:
            _pl_nat2 = (player_stats.get("player") or {}).get("nationality", "")
            for _st2 in (player_stats.get("statistics") or []):
                _t2_name = (_st2.get("team") or {}).get("name", "")
                if _pl_nat2 and _t2_name and _t2_name.strip().lower() == _pl_nat2.strip().lower():
                    continue  # skip national team entries
                if _t2_name:
                    corrected_team_name = _t2_name
                    break
        print(f"[TEAM] corrected_team_name={corrected_team_name!r} (req.teamName={req.teamName!r})")

        standings = []
        if standings_raw:
            try:
                standings = standings_raw[0].get("league", {}).get("standings", [[]])[0]
            except (IndexError, AttributeError):
                pass

        # =============================================
        # WAVE 2: Deep per-fixture data (uses fixture IDs from Wave 1)
        # =============================================

        # 1. Per-fixture team stats (possession, shots, passes per match)
        async def fetch_fixture_team_stats(fixture_list, team_id, limit=5):
            """Fetch per-match team stats — cached in MongoDB for finished fixtures.

            Fetches two data sources per fixture:
              1. /fixtures/statistics  → possession, passes, shots, fouls (team-level)
              2. /fixtures/players     → player-level data aggregated for tackles +
                                         interceptions (not available at team level in
                                         /fixtures/statistics)

            Cached together under fxt_{fid}_{team_id}. Existing cache entries missing
            tackles data are enriched incrementally (one extra API call, then re-cached).
            """
            async def fetch_one(fix):
                fid = fix.get("fixtureId")
                if not fid:
                    return None
                try:
                    cache_key = f"fxt_{fid}_{team_id}"
                    cached = await db.fixture_player_cache.find_one({"_k": cache_key}, {"_id": 0, "d": 1})

                    # Full cache hit — has all four PPDA denominator components cached
                    if cached and cached.get("d") and "fouls_committed_agg" in cached["d"]:
                        r = cached["d"]
                        r["date"] = fix.get("date", "")[:10]
                        r["opponent"] = fix.get("opponent", "")
                        r["venue"] = fix.get("venue", "")
                        r["score"] = f"{fix.get('homeGoals',0)}-{fix.get('awayGoals',0)}"
                        # goals_conceded: goals scored AGAINST the opponent in this fixture
                        _fv = fix.get("venue", "home")
                        r["goals_conceded"] = (fix.get("awayGoals", 0)
                                               if _fv == "home"
                                               else fix.get("homeGoals", 0))
                        return r

                    # Partial cache hit — has team stats but no tackles yet
                    if cached and cached.get("d"):
                        result = dict(cached["d"])
                    else:
                        # Cold fetch — get team-level stats from /fixtures/statistics
                        data = await api_football_request("fixtures/statistics", {"fixture": fid})
                        if not data:
                            return None
                        result = None
                        for team_data in data:
                            if team_data.get("team", {}).get("id") == team_id:
                                raw_stats = {}
                                for s in team_data.get("statistics", []):
                                    raw_stats[s.get("type", "")] = s.get("value")
                                result = {
                                    "possession": raw_stats.get("Ball Possession", ""),
                                    "totalShots": raw_stats.get("Total Shots"),
                                    "shotsOnTarget": raw_stats.get("Shots on Goal"),
                                    "shotsOffTarget": raw_stats.get("Shots off Goal"),
                                    "blockedShots": raw_stats.get("Blocked Shots"),
                                    "shotsInsideBox": raw_stats.get("Shots insidebox"),
                                    "shotsOutsideBox": raw_stats.get("Shots outsidebox"),
                                    "totalPasses": raw_stats.get("Total passes"),
                                    "passAccuracy": raw_stats.get("Passes %"),
                                    "accuratePasses": raw_stats.get("Passes accurate"),
                                    "fouls": raw_stats.get("Fouls"),
                                    "corners": raw_stats.get("Corner Kicks"),
                                    "expectedGoals": raw_stats.get("expected_goals"),
                                }
                                break
                        if not result:
                            return None

                    # Fetch player-level data to aggregate tackles + interceptions
                    # (these are not available from /fixtures/statistics at team level)
                    try:
                        player_data = await api_football_request(
                            "fixtures/players", {"fixture": fid, "team": team_id}
                        )
                        tkl_total  = 0
                        tkl_int    = 0
                        tkl_blocks = 0
                        fls_committed = 0
                        cards_yellow = 0
                        cards_red = 0
                        got_tkl = False
                        if player_data:
                            for team_block in player_data:
                                if team_block.get("team", {}).get("id") == team_id:
                                    for p in team_block.get("players", []):
                                        st  = (p.get("statistics") or [{}])[0]
                                        tkl = st.get("tackles") or {}
                                        fls = st.get("fouls")   or {}
                                        crd = st.get("cards")   or {}
                                        tkl_total     += (tkl.get("total")          or 0)
                                        tkl_int       += (tkl.get("interceptions")  or 0)
                                        tkl_blocks    += (tkl.get("blocks")         or 0)
                                        fls_committed += (fls.get("committed")      or 0)
                                        cards_yellow  += (crd.get("yellow")         or 0)
                                        cards_red     += (crd.get("red")            or 0)
                                    got_tkl = True
                                    break
                        # All four components of the PPDA denominator
                        # (tackles + interceptions + fouls + blocks — full-pitch approximation)
                        result["tackles_total"]         = tkl_total     if got_tkl else None
                        result["tackles_interceptions"] = tkl_int       if got_tkl else None
                        result["tackles_blocks"]        = tkl_blocks    if got_tkl else None
                        result["fouls_committed_agg"]   = fls_committed if got_tkl else None
                        result["cards_yellow_agg"]      = cards_yellow  if got_tkl else None
                        result["cards_red_agg"]         = cards_red     if got_tkl else None
                    except Exception:
                        result["tackles_total"]         = None
                        result["tackles_interceptions"] = None
                        result["tackles_blocks"]        = None
                        result["fouls_committed_agg"]   = None
                        result["cards_yellow_agg"]      = None
                        result["cards_red_agg"]         = None

                    # Cache the enriched result
                    await db.fixture_player_cache.update_one(
                        {"_k": cache_key}, {"$set": {"_k": cache_key, "_ts": datetime.now(timezone.utc), "d": result}}, upsert=True
                    )
                    result["date"]     = fix.get("date", "")[:10]
                    result["opponent"] = fix.get("opponent", "")
                    result["venue"]    = fix.get("venue", "")
                    result["score"]    = f"{fix.get('homeGoals',0)}-{fix.get('awayGoals',0)}"
                    # goals_conceded: goals scored AGAINST the opponent in this fixture
                    _fv2 = fix.get("venue", "home")
                    result["goals_conceded"] = (fix.get("awayGoals", 0)
                                                if _fv2 == "home"
                                                else fix.get("homeGoals", 0))
                    return result
                except Exception:
                    return None

            tasks = [fetch_one(fix) for fix in fixture_list[:limit]]
            results_raw = await aio.gather(*tasks, return_exceptions=True)
            return [r for r in results_raw if r and not isinstance(r, Exception)]

        # 2. Player game-by-game box scores from recent fixtures
        async def fetch_player_game_logs(fixture_list, player_id, limit=35):
            """Fetch player's individual stats — always live from API, all competitions."""

            def _build_game_log(stats: dict) -> dict:
                minutes = stats.get("games", {}).get("minutes") or 0
                rating = stats.get("games", {}).get("rating")
                gl = {
                    "minutes": minutes,
                    "rating": float(rating) if rating else None,
                    "passes_total": stats.get("passes", {}).get("total"),
                    "passes_key": stats.get("passes", {}).get("key"),
                    "passes_accuracy": stats.get("passes", {}).get("accuracy"),
                    "shots_total": stats.get("shots", {}).get("total"),
                    "shots_on": stats.get("shots", {}).get("on"),
                    "tackles_total": stats.get("tackles", {}).get("total"),
                    "tackles_interceptions": stats.get("tackles", {}).get("interceptions"),
                    "tackles_blocks": stats.get("tackles", {}).get("blocks"),
                    "dribbles_attempts": stats.get("dribbles", {}).get("attempts"),
                    "dribbles_success": stats.get("dribbles", {}).get("success"),
                    "fouls_drawn": stats.get("fouls", {}).get("drawn"),
                    "fouls_committed": stats.get("fouls", {}).get("committed"),
                    "duels_total": stats.get("duels", {}).get("total"),
                    "duels_won": stats.get("duels", {}).get("won"),
                    "goals_saves": stats.get("goals", {}).get("saves"),
                    "goals_total": stats.get("goals", {}).get("total"),
                    "goals_assists": stats.get("goals", {}).get("assists"),
                    "passes_crosses": stats.get("passes", {}).get("cross"),
                    "tackles_clearances": stats.get("tackles", {}).get("clearances"),
                    "cards_yellow": stats.get("cards", {}).get("yellow"),
                    # Fields needed for PrizePicks soccer fantasy scoring
                    "goals_conceded": stats.get("goals", {}).get("conceded"),
                    "penalty_saved": stats.get("penalty", {}).get("saved"),
                    "penalty_missed": stats.get("penalty", {}).get("missed"),
                    "offsides": stats.get("offsides"),
                    "cards_red": stats.get("cards", {}).get("red"),
                }
                return gl

            stat_field_map = {
                "goals": "goals_total", "assists": "goals_assists",
                "shots_assisted": "passes_key",
                "pass_attempts": "passes_total", "passes": "passes_total",
                "shots": "shots_total", "shots_on_target": "shots_on",
                "tackles": "tackles_total", "key_passes": "passes_key",
                "saves": "goals_saves", "goalie_saves": "goals_saves",
                "interceptions": "tackles_interceptions",
                "blocks": "tackles_blocks", "dribbles": "dribbles_attempts",
                "fouls_drawn": "fouls_drawn", "fouls_committed": "fouls_committed",
                "crosses": "passes_crosses", "clearances": "tackles_clearances",
                "duels_won": "duels_won", "yellow_cards": "cards_yellow",
            }

            collected = []
            if not player_id or not actual_team_id:
                return collected

            # ── STAGE 0: Read per-game stats directly from MongoDB cache ──────────
            # This fires first and avoids ANY API call. Key pattern: fxp_{fid}_{player_id}
            try:
                cached_games = await db.fixture_player_cache.find(
                    {"_k": {"$regex": f"_{player_id}$"}}
                ).sort("_k", -1).limit(60).to_list(60)
                if cached_games:
                    print(f"[CACHE-STAGE0] {req.playerName}: {len(cached_games)} cached game logs from MongoDB")
                    target_field = stat_field_map.get(req.propType, "")

                    # Extract fixture IDs from keys (fxp_{fid}_{player_id})
                    fid_map: dict = {}  # fid_str -> entry
                    for entry in cached_games:
                        key = entry.get("_k", "")
                        parts = key.split("_")
                        # key format: fxp_{fid}_{pid} — parts[0]="fxp", parts[1]=fid, parts[2]=pid
                        if len(parts) >= 3:
                            fid_map[parts[1]] = entry

                    # Track which fixture IDs come from Stage 0 so Stage 1 can dedup
                    _stage0_fids: set = set(fid_map.keys())

                    # Batch-fetch fixture metadata (home/away team IDs) stored by prefetch
                    fxm_docs: dict = {}
                    if fid_map:
                        meta_keys = [f"fxm_{fid}" for fid in fid_map]
                        meta_results = await db.fixture_player_cache.find(
                            {"_k": {"$in": meta_keys}}, {"_id": 0}
                        ).to_list(len(meta_keys))
                        for meta in meta_results:
                            fid_str = meta.get("_k", "")[4:]  # strip "fxm_"
                            fxm_docs[fid_str] = meta.get("d", {})

                    for fid_str, entry in fid_map.items():
                        d = entry.get("d", {})
                        if not d:
                            continue
                        minutes = d.get("minutes") or 0
                        if not minutes:
                            continue
                        gl = dict(d)
                        gl["date"] = ""
                        gl["score"] = ""
                        gl["league"] = ""
                        gl["round"] = ""

                        # Populate venue and opponent from fixture metadata if available
                        meta = fxm_docs.get(fid_str, {})
                        if meta:
                            home_id_meta = meta.get("home_id")
                            away_id_meta = meta.get("away_id")
                            # Club filter: if BOTH team IDs are known and NEITHER matches
                            # the player's current team, this is a fixture from a previous
                            # club — drop it so stale old-club stats don't corrupt the prior.
                            if (home_id_meta is not None and away_id_meta is not None
                                    and home_id_meta != actual_team_id
                                    and away_id_meta != actual_team_id):
                                print(f"[STAGE0 CLUB FILTER] fid={fid_str} "
                                      f"home={home_id_meta} away={away_id_meta} "
                                      f"≠ current team {actual_team_id} — dropped (old-club fixture)")
                                continue
                            is_home = (home_id_meta == actual_team_id)
                            gl["venue"] = "home" if is_home else "away"
                            gl["opponent"] = meta.get("away_name", "") if is_home else meta.get("home_name", "")
                        else:
                            gl["venue"] = ""
                            gl["opponent"] = ""

                        raw_val = gl.get(target_field) if target_field else None
                        if raw_val is not None and minutes > 0:
                            gl["targetStatPer90"] = round((raw_val / minutes) * 90, 2)
                        # Mark with fixture ID so Stage 1 can dedup
                        gl["_fid"] = fid_str
                        collected.append(gl)

                    # Only short-circuit if we have enough games with venue data.
                    # Minimum 15 games required — a proper Bayesian prior needs enough
                    # samples to split home/away and compute stable rolling averages.
                    # Below 15 we always fall through to Stage 1 so the live API fetches
                    # all 40 team fixtures and fills the gaps (Stage 1 still uses cache
                    # hits for any fixture already stored, so no wasted API calls).
                    good = [g for g in collected if g.get("venue")]
                    # For saves prop: also require that at least SOME cached logs actually
                    # have goals_saves data. The prefetch cache often stores a game log
                    # entry with goals_saves=None (the stat was null at cache time).
                    # If Stage 0 returns early with 17 logs all having goals_saves=None,
                    # the Bayesian engine gets an empty series, falls back to _empty_metrics
                    # (posteriorMean=line, P=50/50), and the coin-flip guard pins the
                    # result to UNDER — exactly the Oblak bug.
                    _saves_ok = True
                    if req.propType in {"saves", "goalie_saves"}:
                        target_f = stat_field_map.get(req.propType, "")
                        _saves_ok = any(g.get(target_f) is not None for g in collected)
                        if not _saves_ok:
                            print(f"[CACHE-STAGE0] {req.playerName}/saves: 0 of {len(collected)} cached logs have goals_saves — falling through to Stage 1")
                    if len(collected) >= 15 and len(good) >= len(collected) // 2 and _saves_ok:
                        print(f"[CACHE-STAGE0] Returning {len(collected)} real (cached) game logs — skipping API")
                        return collected
                    elif collected:
                        print(f"[CACHE-STAGE0] Only {len(collected)} games (venue ok: {len(good)}, saves_ok={_saves_ok}) — falling through to Stage 1 for more data")
            except Exception as _ce:
                print(f"[CACHE-STAGE0] Error: {_ce}")

            try:
                # Fetch the team's last 40 finished fixtures across ALL competitions from API.
                # 20 was too shallow — for GKs (and any player on a busy team), 20 games
                # may only yield 6-8 venue-specific samples once home/away are split,
                # causing the venue-split prior to fall back to combined and mix
                # home/away stats. 40 games gives enough coverage for proper venue splits.

                # ── On-demand cache: check team_fixture_history before calling API ──
                team_fixtures_raw = None
                _tfh_cache_ttl = 6 * 3600  # 6 hours — refresh often for accurate rest-day calculation
                try:
                    _tfh_doc = await db.team_fixture_history.find_one(
                        {"teamId": actual_team_id}, {"_id": 0, "fixtures": 1, "_ts": 1}
                    )
                    if _tfh_doc and _tfh_doc.get("fixtures"):
                        import time as _t2
                        _age = _t2.time() - _tfh_doc.get("_ts", 0)
                        if _age < _tfh_cache_ttl:
                            team_fixtures_raw = _tfh_doc["fixtures"]
                            print(f"[API-DIRECT] {req.playerName}: {len(team_fixtures_raw)} team fixtures from CACHE (age {int(_age/3600)}h)")
                except Exception:
                    pass

                if team_fixtures_raw is None and not _is_bdl_league:
                    team_fixtures_raw = await api_football_request(
                        "fixtures", {"team": actual_team_id, "last": 25, "status": "FT"}
                    )
                    if not team_fixtures_raw:
                        print(f"[API-DIRECT] No fixtures found for teamId={actual_team_id}")
                        return collected
                    print(f"[API-DIRECT] {req.playerName}: {len(team_fixtures_raw)} team fixtures from API")
                    # Write-back: cache for next prediction on same team
                    import time as _t3
                    try:
                        await db.team_fixture_history.update_one(
                            {"teamId": actual_team_id},
                            {"$set": {
                                "teamId": actual_team_id,
                                "fixtures": team_fixtures_raw,
                                "_ts": _t3.time(),
                                "_dt": datetime.now(timezone.utc),
                            }},
                            upsert=True
                        )
                    except Exception as _ce:
                        pass  # non-fatal — prediction continues

                async def _fetch_one(fix_raw):
                    try:
                        fid = fix_raw.get("fixture", {}).get("id")
                        if not fid:
                            return None
                        home_id = fix_raw.get("teams", {}).get("home", {}).get("id")
                        fix_venue = "home" if home_id == actual_team_id else "away"
                        fix_date = fix_raw.get("fixture", {}).get("date", "")[:10]
                        fix_league = fix_raw.get("league", {}).get("name", "")
                        fix_round = fix_raw.get("league", {}).get("round", "")
                        opp_key = "away" if home_id == actual_team_id else "home"
                        fix_opponent = fix_raw.get("teams", {}).get(opp_key, {}).get("name", "")
                        home_goals = fix_raw.get("goals", {}).get("home", 0) or 0
                        away_goals = fix_raw.get("goals", {}).get("away", 0) or 0

                        # Helper: enrich game log with team possession from fixtures/statistics
                        async def _enrich_possession(gl_dict: dict) -> dict:
                            try:
                                poss_cache_key = f"fxt_poss_{fid}"
                                cached_poss = await db.fixture_player_cache.find_one(
                                    {"_k": poss_cache_key}, {"_id": 0, "d": 1}
                                )
                                home_poss = away_poss = None
                                if cached_poss and cached_poss.get("d"):
                                    home_poss = cached_poss["d"].get("home_poss")
                                    away_poss = cached_poss["d"].get("away_poss")
                                else:
                                    # Fetch live from fixtures/statistics — one call per fixture
                                    fix_stats = await api_football_request("fixtures/statistics", {"fixture": fid})
                                    if fix_stats:
                                        for team_stats in fix_stats:
                                            t_id = (team_stats.get("team") or {}).get("id")
                                            stats_list = team_stats.get("statistics") or []
                                            for s in stats_list:
                                                if s.get("type") == "Ball Possession":
                                                    raw = str(s.get("value") or "").replace("%", "").strip()
                                                    try:
                                                        pval = int(raw)
                                                        if t_id == fix_raw.get("teams", {}).get("home", {}).get("id"):
                                                            home_poss = pval
                                                        else:
                                                            away_poss = pval
                                                    except (ValueError, TypeError):
                                                        pass
                                        # Cache for future calls (permanent — historical fixtures don't change)
                                        if home_poss is not None or away_poss is not None:
                                            await db.fixture_player_cache.update_one(
                                                {"_k": poss_cache_key},
                                                {"$set": {"_k": poss_cache_key, "d": {
                                                    "home_poss": home_poss, "away_poss": away_poss
                                                }}},
                                                upsert=True
                                            )
                                # Assign to the game log based on this player's venue
                                if fix_venue == "home" and home_poss is not None:
                                    gl_dict["teamPossession"] = home_poss
                                    gl_dict["opponentPossession"] = away_poss if away_poss is not None else 100 - home_poss
                                elif fix_venue == "away" and away_poss is not None:
                                    gl_dict["teamPossession"] = away_poss
                                    gl_dict["opponentPossession"] = home_poss if home_poss is not None else 100 - away_poss
                            except Exception:
                                pass
                            return gl_dict

                        # Check prefetch cache first — avoids extra API call if already cached
                        cache_key = f"fxp_{fid}_{player_id}"
                        cached_doc = await db.fixture_player_cache.find_one({"_k": cache_key}, {"_id": 0, "d": 1, "_ts": 1})
                        if cached_doc and cached_doc.get("d"):
                            # Freshness guard: API-Football can take 2-4h to finalize player
                            # stats after FT. If the entry was cached < 4h ago it may reflect
                            # mid-match or early-post-FT data (e.g. 3 shots at HT vs 6 final).
                            # Re-fetch live so the cache gets overwritten with final values.
                            _doc_ts = cached_doc.get("_ts")
                            _doc_age_h = ((datetime.now(timezone.utc) - (
                                _doc_ts if _doc_ts and _doc_ts.tzinfo else
                                (_doc_ts.replace(tzinfo=timezone.utc) if _doc_ts else datetime.now(timezone.utc))
                            )).total_seconds() / 3600) if _doc_ts else 999
                            _cache_stale = _doc_age_h < 4.0
                            if _cache_stale:
                                pass  # fall through to live API fetch + overwrite
                            else:
                                gl = dict(cached_doc["d"])
                                minutes = gl.get("minutes", 0)
                                if not minutes or minutes == 0:
                                    return None
                                # For saves prop: bypass cache if saves value is None
                                # (pre-fetch cache often misses saves for GKs — always fetch fresh)
                                saves_cache_miss = req.propType == "saves" and gl.get("goals_saves") is None
                                if not saves_cache_miss:
                                    gl["date"] = fix_date
                                    gl["opponent"] = fix_opponent
                                    gl["venue"] = fix_venue
                                    gl["score"] = f"{home_goals}-{away_goals}"
                                    gl["league"] = fix_league
                                    gl["round"] = fix_round
                                    raw_val = gl.get(stat_field_map.get(req.propType, ""), None)
                                    if raw_val is not None and minutes > 0:
                                        gl["targetStatPer90"] = round((raw_val / minutes) * 90, 2)
                                    gl["_fid"] = str(fid)
                                    gl = await _enrich_possession(gl)
                                    return gl
                                # Fall through to live API fetch for saves

                        fix_data = await api_football_request("fixtures/players", {"fixture": fid})
                        if not fix_data:
                            return None

                        matched_stats = None
                        all_player_logs = {}
                        # Build a name→stats map for fallback matching
                        name_stats_map: dict = {}
                        _target_name_norm = req.playerName.lower().strip() if req.playerName else ""
                        for team_data in fix_data:
                            for p in team_data.get("players", []):
                                pid = p.get("player", {}).get("id")
                                pname = (p.get("player", {}).get("name") or "").lower().strip()
                                stats = p.get("statistics", [{}])[0] if p.get("statistics") else {}
                                mins = stats.get("games", {}).get("minutes") or 0
                                if pid:
                                    all_player_logs[pid] = _build_game_log(stats)
                                    if pid == player_id and mins > 0:
                                        matched_stats = stats
                                if pname and mins > 0:
                                    name_stats_map[pname] = stats

                        # Fallback: name-based match when ID lookup misses
                        if not matched_stats and _target_name_norm and name_stats_map:
                            # Try exact name match first
                            if _target_name_norm in name_stats_map:
                                matched_stats = name_stats_map[_target_name_norm]
                                print(f"[NAME-MATCH] fid={fid}: matched '{req.playerName}' by exact name")
                            else:
                                # Try partial match: target surname in API name or vice versa
                                target_parts = set(_target_name_norm.split())
                                for api_name, s in name_stats_map.items():
                                    api_parts = set(api_name.split())
                                    # At least one word must match and names share >50% of tokens
                                    common = target_parts & api_parts
                                    if common and len(common) / max(len(target_parts), len(api_parts)) >= 0.5:
                                        matched_stats = s
                                        print(f"[NAME-MATCH] fid={fid}: matched '{req.playerName}' → '{api_name}' (partial)")
                                        break

                        # Cache all players from this fixture (fire-and-forget, for position comparisons)
                        # Also write/refresh fxm_ doc (no _ts so it never expires via TTL)
                        _fix_home_id = fix_raw.get("teams", {}).get("home", {}).get("id")
                        _fix_away_id = fix_raw.get("teams", {}).get("away", {}).get("id")
                        _fix_home_name = fix_raw.get("teams", {}).get("home", {}).get("name", "")
                        _fix_away_name = fix_raw.get("teams", {}).get("away", {}).get("name", "")
                        async def _cache_fix(fid_c, logs_c, fhid=_fix_home_id, faid=_fix_away_id, fhn=_fix_home_name, fan=_fix_away_name):
                            ops = [
                                db.fixture_player_cache.update_one(
                                    {"_k": f"fxp_{fid_c}_{pk}"},
                                    {"$set": {"_k": f"fxp_{fid_c}_{pk}", "_ts": datetime.now(timezone.utc), "d": lv}},
                                    upsert=True
                                ) for pk, lv in logs_c.items()
                            ]
                            # Refresh fxm_ without _ts so venue metadata is permanent (not TTL-expired)
                            if fhid and faid:
                                fxm_k = f"fxm_{fid_c}"
                                ops.append(db.fixture_player_cache.update_one(
                                    {"_k": fxm_k},
                                    {"$set": {"_k": fxm_k, "d": {
                                        "home_id": fhid, "away_id": faid,
                                        "home_name": fhn, "away_name": fan,
                                    }}},
                                    upsert=True
                                ))
                            if ops:
                                await aio.gather(*ops, return_exceptions=True)
                        aio.ensure_future(_cache_fix(fid, all_player_logs))

                        if not matched_stats:
                            return None

                        gl = _build_game_log(matched_stats)
                        gl["date"] = fix_date
                        gl["opponent"] = fix_opponent
                        gl["venue"] = fix_venue
                        gl["score"] = f"{home_goals}-{away_goals}"
                        gl["league"] = fix_league
                        gl["round"] = fix_round
                        minutes = gl.get("minutes", 0)
                        raw_val = gl.get(stat_field_map.get(req.propType, ""), None)
                        if raw_val is not None and minutes > 0:
                            gl["targetStatPer90"] = round((raw_val / minutes) * 90, 2)
                        gl["_fid"] = str(fid)
                        gl = await _enrich_possession(gl)
                        return gl
                    except Exception:
                        return None

                if not team_fixtures_raw:
                    return collected

                sem = aio.Semaphore(10)
                async def _sem_fetch(fix_raw):
                    async with sem:
                        return await _fetch_one(fix_raw)

                tasks = [_sem_fetch(fx) for fx in team_fixtures_raw]
                results = await aio.gather(*tasks, return_exceptions=True)
                for r in results:
                    if r and not isinstance(r, Exception):
                        collected.append(r)

                print(f"[API-DIRECT] {req.playerName}/{req.propType}: {len(collected)} real game logs from {len(team_fixtures_raw)} fixtures")
            except Exception as _e:
                print(f"[API-DIRECT] Error: {_e}")

            # ── Dedup by fixture ID ────────────────────────────────────────────
            # Stage 0 (MongoDB cache) and Stage 1 (team fixture loop) both read
            # from fixture_player_cache for the same fixture IDs — the same game
            # can appear twice: once without date/score (Stage 0) and once with
            # date/score/possession (Stage 1). Keep Stage 1's richer entry.
            if collected:
                _fid_index: dict = {}   # fid_str -> index in _deduped
                _deduped: list = []
                for _g in collected:
                    _g_fid = _g.get("_fid")
                    if not _g_fid:
                        _deduped.append(_g)
                    elif _g_fid not in _fid_index:
                        _fid_index[_g_fid] = len(_deduped)
                        _deduped.append(_g)
                    else:
                        # If the new entry has a real date and the existing one doesn't,
                        # replace — Stage 1 (with date) beats Stage 0 (empty date).
                        _existing = _deduped[_fid_index[_g_fid]]
                        if _g.get("date") and not _existing.get("date"):
                            _deduped[_fid_index[_g_fid]] = _g
                if len(_deduped) < len(collected):
                    print(f"[DEDUP] {req.playerName}: removed {len(collected) - len(_deduped)} duplicate fixture(s)")
                collected = _deduped
                # Strip internal marker before handing off
                for _g in collected:
                    _g.pop("_fid", None)

            return collected

        # =============================================
        # POSITION COMPARISON: Same-position players vs opponent
        # =============================================
        FIXTURE_POS_MAP = {"Goalkeeper": "G", "Defender": "D", "Midfielder": "M", "Attacker": "F"}
        PROP_STAT_KEYS = {
            "pass_attempts": ("passes", "total"), "shots": ("shots", "total"),
            "shots_on_target": ("shots", "on"), "tackles": ("tackles", "total"),
            "key_passes": ("passes", "key"), "shots_assisted": ("passes", "key"),
            "saves": ("goals", "saves"),
            "interceptions": ("tackles", "interceptions"), "blocks": ("tackles", "blocks"),
            "dribbles": ("dribbles", "attempts"), "fouls_drawn": ("fouls", "drawn"),
            "goals": ("goals", "total"), "assists": ("goals", "assists"),
            "crosses": ("passes", "cross"), "clearances": ("tackles", "clearances"),
            "duels_won": ("duels", "won"), "yellow_cards": ("cards", "yellow"),
        }

        async def fetch_position_comparison(opp_fixtures, target_pos, prop_type, opponent_id, player_venue_filter, limit=10, target_specific_pos=None):
            """Fetch same-position players who played against the opponent recently.
            Filters by venue: if target player is AWAY, only show comparison players' AWAY performances.
            Also fetches possession data for each match.
            If target_specific_pos is set (e.g., 'CB'), filters out players with cached positions that don't match."""
            fixture_pos = FIXTURE_POS_MAP.get(target_pos, "")
            if not fixture_pos or not opp_fixtures:
                return []
            stat_cat, stat_sub = PROP_STAT_KEYS.get(prop_type, ("passes", "total"))
            # The comparison players' venue should match the TARGET player's venue
            # If target is AWAY, we want other players who also played AWAY against this opponent
            comp_venue = player_venue_filter  # "home" or "away"

            async def fetch_pos_from_fixture(fix):
                fid = fix.get("fixtureId")
                if not fid:
                    return []
                try:
                    # Fetch players AND fixture statistics (possession) in parallel
                    players_task = api_football_request("fixtures/players", {"fixture": fid})
                    stats_task = api_football_request("fixtures/statistics", {"fixture": fid})
                    players_data, fixture_stats_data = await aio.gather(players_task, stats_task)

                    if not players_data:
                        return []

                    # Parse possession from fixture stats
                    possession_map = {}  # team_id -> possession %
                    if fixture_stats_data:
                        for team_stats in fixture_stats_data:
                            tid = team_stats.get("team", {}).get("id")
                            for stat in team_stats.get("statistics", []):
                                if stat.get("type") == "Ball Possession":
                                    poss_str = str(stat.get("value", "0")).replace("%", "")
                                    try:
                                        possession_map[tid] = int(poss_str)
                                    except (ValueError, TypeError):
                                        pass

                    results = []
                    for team_data in players_data:
                        tid = team_data.get("team", {}).get("id")
                        team_name = team_data.get("team", {}).get("name", "")
                        if tid == opponent_id:
                            continue  # Skip opponent — we want teams who PLAYED AGAINST them

                        # Venue filter: determine if this team was home or away in this fixture
                        # The opponent's fixture list has opp_venue (opponent's venue)
                        # If opponent was HOME, the comparison team was AWAY, and vice versa
                        opp_fixture_venue = fix.get("venue", "")  # opponent's venue in this fixture
                        comp_team_venue = "away" if opp_fixture_venue == "home" else "home"
                        if comp_team_venue != comp_venue:
                            continue  # Skip — wrong venue for comparison

                        team_poss = possession_map.get(tid, None)
                        opp_poss = possession_map.get(opponent_id, None)

                        for p in team_data.get("players", []):
                            pstats = p.get("statistics", [{}])[0] if p.get("statistics") else {}
                            pos = pstats.get("games", {}).get("position", "")
                            minutes = pstats.get("games", {}).get("minutes") or 0
                            if pos != fixture_pos or minutes < 30:
                                continue
                            stat_val = pstats.get(stat_cat, {}).get(stat_sub)
                            if stat_val is None:
                                continue
                            rating = pstats.get("games", {}).get("rating")
                            p_id = p.get("player", {}).get("id")
                            p_name = p.get("player", {}).get("name", "")

                            # Look up cached specific position + role
                            cached_pr = await db.player_positions.find_one(
                                {"playerId": p_id}, {"_id": 0, "specificPosition": 1, "role": 1}
                            ) if p_id else None
                            spec_pos = (cached_pr or {}).get("specificPosition", "")
                            spec_role = (cached_pr or {}).get("role", "")

                            # Filter by specific position if target has one
                            if target_specific_pos and spec_pos and spec_pos != target_specific_pos:
                                continue  # Skip — cached position doesn't match target

                            # GK-specific: capture goals conceded for per-game save rate.
                            # For saves prop: stat_cat="goals", stat_sub="saves" per PROP_STAT_KEYS.
                            # Conceded is at the same "goals" block in the fixture player API.
                            _gk_conceded = None
                            if prop_type == "saves":
                                _raw_conceded = pstats.get("goals", {}).get("conceded")
                                if _raw_conceded is not None:
                                    try:
                                        _gk_conceded = int(_raw_conceded)
                                    except (TypeError, ValueError):
                                        pass

                            results.append({
                                "name": p_name,
                                "playerId": p_id,
                                "team": team_name,
                                "minutes": minutes,
                                "statValue": stat_val,
                                "rating": float(rating) if rating else None,
                                "date": fix.get("date", "")[:10],
                                "per90": round((stat_val / minutes) * 90, 2) if minutes > 0 else 0,
                                "venue": comp_team_venue,
                                "position": spec_pos or pos,
                                "role": spec_role,
                                "teamPossession": team_poss,
                                "oppPossession": opp_poss,
                                "goalsConceded": _gk_conceded,
                            })
                    return results
                except Exception:
                    return []

            tasks = [fetch_pos_from_fixture(f) for f in opp_fixtures[:limit]]
            raw_results = await aio.gather(*tasks, return_exceptions=True)
            all_players = []
            for r in raw_results:
                if isinstance(r, list):
                    all_players.extend(r)
            # Sort by stat value descending, max 1 per team for diversity, take top 7
            seen_names = set()
            seen_teams = {}
            unique = []
            for p in sorted(all_players, key=lambda x: x.get("statValue", 0), reverse=True):
                team = p.get("team", "")
                if p["name"] in seen_names:
                    continue
                if team and seen_teams.get(team, 0) >= 1:
                    continue  # Max 1 player per team
                seen_names.add(p["name"])
                if team:
                    seen_teams[team] = seen_teams.get(team, 0) + 1
                unique.append(p)
                if len(unique) >= 7:
                    break
            return unique

        # =============================================
        # VENUE-FILTERED DATA: Everything is venue-based
        # =============================================
        # If player is HOME → team's HOME games + opponent's AWAY games
        # If player is AWAY → team's AWAY games + opponent's HOME games
        player_venue = req.venue.lower()  # "home" or "away" (legacy clients may still send "neutral")
        # "Neutral" venue is a fiction — even at a neutral tournament site, one team
        # effectively plays like the home side (bigger following in the crowd, more
        # expected support) and the other like the away side. There is no real
        # in-between, so we always resolve a definite home/away here rather than
        # letting "neutral" skip venue-aware logic downstream. Priority of signals:
        #   1. Betting-market favorite (proxy for which team the world is backing)
        #   2. The fixture's own home/away designation from API-Football
        #   3. A deterministic team-ID tiebreaker (last resort, no data available)
        if player_venue == "neutral":
            _fav = (match_odds or {}).get("favorite")       # "home"/"away", relative to FIXTURE home/away
            _pih = (match_odds or {}).get("playerIsHome")
            if _fav is not None and _pih is not None:
                _player_is_favorite = (_fav == "home") == bool(_pih)
                player_venue = "home" if _player_is_favorite else "away"
                _ev_source = "odds"
            elif _pih is not None:
                player_venue = "home" if _pih else "away"
                _ev_source = "fixture"
            else:
                player_venue = "home" if (actual_team_id or 0) < (req.opponentId or 0) else "away"
                _ev_source = "tiebreaker"
            print(f"[EFFECTIVE VENUE] neutral→{player_venue} source={_ev_source} player={req.playerName}")
        # API-Football always designates one team as home (1) and one as away (2) for
        # every fixture — including World Cup matches. We trust that designation and the
        # playerIsHome flag from get_match_odds().
        _is_neutral = False  # normalized above — nothing downstream should treat a match as neutral anymore
        # ── VENUE ALIGNMENT: override user-selected venue with fixture reality ──
        # If the user typed a venue that contradicts the actual fixture assignment
        # (e.g. selected HOME for a team API-Football designated as AWAY), the entire
        # pipeline — game log filtering, possession calculation, and AI prompt — must
        # use a SINGLE consistent venue. We trust the fixture data because it determines
        # the actual match context (home/away possession, opponent venue, etc.).
        _pih_after_odds = match_odds.get("playerIsHome") if match_odds else None
        if _pih_after_odds is not None:
            _fixture_venue = "home" if _pih_after_odds else "away"
            if player_venue != _fixture_venue:
                print(f"[VENUE ALIGN] user={player_venue} → fixture={_fixture_venue} "
                      f"player={req.playerName} team={corrected_team_name}")
                player_venue = _fixture_venue
        opponent_venue = "away" if player_venue == "home" else "home"
        is_womens = req.leagueId in WOMENS_LEAGUE_IDS
        pronoun_note = "IMPORTANT: This is a WOMEN'S league. Use she/her/her pronouns for all players. Never use he/him/his." if is_womens else ""

        # Filter team's recent fixtures by venue (skipped for neutral — no venue preference)
        venue_filtered_team_fixtures = (
            [] if _is_neutral else [f for f in recent_fixtures if f.get("venue") == player_venue]
        )
        # Also keep all fixtures for general context
        all_team_fixtures = recent_fixtures

        # Get opponent's recent fixtures — local DB first, API fallback
        opponent_recent_raw = None
        if safe_opp_id:
            try:
                from cache import get_cached_team_fixtures as _get_opp_fixtures
                _opp_local = await _get_opp_fixtures(safe_opp_id)
                if _opp_local:
                    opponent_recent_raw = _opp_local[:15]
                    print(f"[LOCAL] Opponent fixtures from DB: {len(opponent_recent_raw)} games")
            except Exception:
                pass
            if not opponent_recent_raw and not _is_bdl_league:
                opponent_recent_raw = await api_football_request("fixtures", {"team": safe_opp_id, "last": 8})
        opponent_fixture_list = []
        if opponent_recent_raw:
            for f in opponent_recent_raw[:8]:
                opp_home_id = f.get("teams", {}).get("home", {}).get("id")
                opp_venue = "home" if opp_home_id == req.opponentId else "away"
                opponent_fixture_list.append({
                    "fixtureId": f.get("fixture", {}).get("id"),
                    "date": f.get("fixture", {}).get("date", ""),
                    "opponent": f.get("teams", {}).get("away" if opp_venue == "home" else "home", {}).get("name", "Unknown"),
                    "venue": opp_venue,
                    "homeGoals": f.get("goals", {}).get("home", 0) or 0,
                    "awayGoals": f.get("goals", {}).get("away", 0) or 0,
                })

        # Filter opponent fixtures by their venue in THIS matchup (skipped for neutral)
        venue_filtered_opp_fixtures = (
            [] if _is_neutral else [f for f in opponent_fixture_list if f.get("venue") == opponent_venue]
        )

        # Wave 2: Use VENUE-FILTERED fixtures for deep stats
        # For neutral venue: use all fixtures (no venue preference)
        team_fixture_stats_task = fetch_fixture_team_stats(
            all_team_fixtures[:5] if _is_neutral else (venue_filtered_team_fixtures[:5] if len(venue_filtered_team_fixtures) >= 3 else all_team_fixtures[:5]),
            actual_team_id or 40, 5
        )
        opponent_fixture_stats_task = fetch_fixture_team_stats(
            opponent_fixture_list[:5] if _is_neutral else (venue_filtered_opp_fixtures[:5] if len(venue_filtered_opp_fixtures) >= 3 else opponent_fixture_list[:5]),
            req.opponentId, 5
        )
        # Player game logs: VENUE-PRIORITIZED ordering
        # For neutral: use all fixtures equally (no venue priority — WC/tournament game)
        # For home/away: search venue-matching fixtures first (target: 15-20 venue-matched games)
        venue_first_fixtures = (
            all_team_fixtures if _is_neutral
            else venue_filtered_team_fixtures + [f for f in all_team_fixtures if f.get("venue") != player_venue]
        )
        player_game_logs_task = fetch_player_game_logs(venue_first_fixtures, req.playerId, 35)

        # Position comparison task — same-position players vs this opponent
        # (started later after player_position is resolved)
        async def _empty_list():
            return []
        # =============================================
        # BUILD STRUCTURED DATA DIGEST (no AI needed — pure code extraction)
        # =============================================
        def build_data_digest():
            """Build a compact data digest directly from raw API data — no AI summarization needed."""
            parts = []

            # 1. Player basics
            if player_stats:
                pstats = player_stats.get("statistics", [{}])[0] if player_stats.get("statistics") else {}
                games_data = pstats.get("games", {})
                passes = pstats.get("passes", {})
                shots = pstats.get("shots", {})
                tackles = pstats.get("tackles", {})
                goals = pstats.get("goals", {})
                dribbles = pstats.get("dribbles", {})
                fouls = pstats.get("fouls", {})
                parts.append(f"""[PLAYER PROFILE]
- Position: {games_data.get('position', 'Unknown')} | Apps: {games_data.get('appearences', 'N/A')} | Avg Rating: {games_data.get('rating', 'N/A')}
- Avg Minutes: {(games_data.get('minutes') or 0) / max((games_data.get('appearences') or 1), 1):.0f} per game
- Passes: total={passes.get('total','N/A')}, key={passes.get('key','N/A')}, accuracy={passes.get('accuracy','N/A')}%
- Shots: total={shots.get('total','N/A')}, on_target={shots.get('on','N/A')}
- Tackles: total={tackles.get('total','N/A')}, interceptions={tackles.get('interceptions','N/A')}, blocks={tackles.get('blocks','N/A')}
- Saves: {goals.get('saves','N/A')} | Dribbles: attempts={dribbles.get('attempts','N/A')}, success={dribbles.get('success','N/A')}
- Fouls drawn: {fouls.get('drawn','N/A')}""")

            # 2. Team stats (venue-specific; for neutral use overall totals)
            if team_stats:
                fixtures = team_stats.get("fixtures", {})
                goals_for = team_stats.get("goals", {}).get("for", {}).get("total", {})
                goals_against = team_stats.get("goals", {}).get("against", {}).get("total", {})
                _pv_label = "OVERALL" if _is_neutral else player_venue.upper()
                _pv_key   = None if _is_neutral else player_venue  # None → fall back gracefully
                _gf_val   = sum(goals_for.values()) if _is_neutral else goals_for.get(player_venue, "N/A")
                _ga_val   = sum(goals_against.values()) if _is_neutral else goals_against.get(player_venue, "N/A")
                _w = sum(fixtures.get("wins", {}).values()) if _is_neutral else fixtures.get("wins", {}).get(player_venue, "N/A")
                _d = sum(fixtures.get("draws", {}).values()) if _is_neutral else fixtures.get("draws", {}).get(player_venue, "N/A")
                _l = sum(fixtures.get("loses", {}).values()) if _is_neutral else fixtures.get("loses", {}).get(player_venue, "N/A")
                parts.append(f"""[TEAM {_pv_label} PROFILE]
- Record: W{_w} D{_d} L{_l}
- Goals For: {_gf_val} | Against: {_ga_val}""")

            # 3. Opponent stats (opposite venue; for neutral use overall totals)
            if opponent_stats:
                opp_fix = opponent_stats.get("fixtures", {})
                opp_gf = opponent_stats.get("goals", {}).get("for", {}).get("total", {})
                opp_ga = opponent_stats.get("goals", {}).get("against", {}).get("total", {})
                _ov_label = "OVERALL" if _is_neutral else opponent_venue.upper()
                _ogf_val  = sum(opp_gf.values()) if _is_neutral else opp_gf.get(opponent_venue, "N/A")
                _oga_val  = sum(opp_ga.values()) if _is_neutral else opp_ga.get(opponent_venue, "N/A")
                _ow = sum(opp_fix.get("wins", {}).values()) if _is_neutral else opp_fix.get("wins", {}).get(opponent_venue, "N/A")
                _od = sum(opp_fix.get("draws", {}).values()) if _is_neutral else opp_fix.get("draws", {}).get(opponent_venue, "N/A")
                _ol = sum(opp_fix.get("loses", {}).values()) if _is_neutral else opp_fix.get("loses", {}).get(opponent_venue, "N/A")
                parts.append(f"""[OPPONENT {_ov_label} PROFILE]
- Record: W{_ow} D{_od} L{_ol}
- Goals For: {_ogf_val} | Against: {_oga_val}""")

            # 4. H2H
            if h2h_data:
                h2h_lines = []
                for h in h2h_data[:5]:
                    h2h_lines.append(f"  {h.get('date', '')[:10]}: {h.get('homeTeam', '')} {h.get('homeGoals', 0)}-{h.get('awayGoals', 0)} {h.get('awayTeam', '')}")
                parts.append(f"[H2H ({len(h2h_data)} matches)]\n" + "\n".join(h2h_lines))

            # 5. Standings
            if standings:
                standing_lines = [f"  {s.get('rank','')}. {s.get('team','')} — {s.get('points','')}pts (GD: {s.get('goalsDiff','')})" for s in standings[:8]]
                parts.append("[STANDINGS]\n" + "\n".join(standing_lines))

            # 6. Odds & Game Type
            if match_odds and match_odds.get("bookmakerOdds"):
                bo = match_odds["bookmakerOdds"]
                ao = match_odds.get("americanOdds", {})
                gt = match_odds.get("gameType", "")
                if ao:
                    parts.append(f"""[MONEYLINE & GAME TYPE]
- Home ({ao.get('home', '')}) | Draw ({ao.get('draw', '')}) | Away ({ao.get('away', '')})
- Favorite: {match_odds.get('favorite', 'Unknown').upper()}
- Game Type: {gt}
>>> Moneyline tells you expected game flow. Heavy favorites control possession and tempo. Underdogs may sit deep (deflating pass/shot stats for attacker props). CRITICAL FOR GOALKEEPERS: GK pass volume is INVERTED — a team sitting deep and defending (low possession) produces MORE back-passes to the GK, not fewer. An away GK protecting a lead is the highest-volume scenario for GK passes. A GK on a dominant possession team sees FEWER back-passes. <<<""")
                else:
                    parts.append(f"""[ODDS]
- Home: {bo.get('homeWin', 'N/A')} | Draw: {bo.get('draw', 'N/A')} | Away: {bo.get('awayWin', 'N/A')}
- Favorite: {match_odds.get('favorite', 'Unknown').upper()}""")

            return "\n\n".join(parts)

        data_digest = build_data_digest()

        # =============================================
        # MATCH DOMINANCE ENGINE: Calculate expected possession & context multiplier
        # Uses opponent-aware formula + odds adjustment for accurate matchup prediction
        # =============================================
        match_dominance = {
            "expectedPoss": 50.0,
            "oppExpectedPoss": 50.0,
            "multiplier": 1.0,
            "notes": [],
            "seasonAvgIsReal": False,
            "hasRealPossData": False,
        }

        # Wave 2: Fetch deep fixture data + Situation Engine in parallel
        # AI digest, web intel, and AI press intensity removed — Gemini is summary-only.
        # Press intensity falls back to the heuristic engine; digest/web intel were
        # pre-processing context that AI no longer needs for math.
        from situation_engine import build_game_situation

        async def _noop_str(): return ""
        async def _noop_none(): return None
        ai_digest_task = _noop_str()

        # Situation engine inputs
        # Use the fixture's canonical home/away assignment (from match_odds) when available,
        # just like we do for possession/moneyline/team labels. This ensures the situation
        # engine (knockout aggregate, home/away multipliers) also sees correct orientation.
        _sit_pih = (match_odds or {}).get("playerIsHome")
        _sit_is_home = bool(_sit_pih) if _sit_pih is not None else (player_venue == "home")
        _sit_home_id = actual_team_id if _sit_is_home else req.opponentId
        _sit_away_id = req.opponentId if _sit_is_home else actual_team_id
        _sit_match_round = (match_odds or {}).get("matchRound", "")
        _sit_match_league = (match_odds or {}).get("matchLeague", "")
        _sit_match_date = (match_odds or {}).get("matchDate", "")
        _sit_fixture_id = (match_odds or {}).get("fixtureId")

        # Use the fixture's actual competition league_id (e.g. Europa League = 3),
        # not the player's domestic league. Domestic league_id breaks H2H lookup
        # for European ties (e.g. Braga in Europa League vs Primeira Liga = 94).
        _sit_fixture_league_id = (match_odds or {}).get("matchLeagueId") or league_id or 39
        situation_task = build_game_situation(
            home_team_id=_sit_home_id,
            away_team_id=_sit_away_id,
            is_player_home=_sit_is_home,
            league_id=_sit_fixture_league_id,
            match_round=_sit_match_round,
            fixture_id=_sit_fixture_id,
            player_team_name=corrected_team_name or req.teamName or "",
            opponent_name=req.opponentName or "",
            prop_type=req.propType,
            standings=standings,
            player_team_id=actual_team_id or req.teamId,
            opponent_id=req.opponentId,
        )

        # Web intel: live injury/lineup news from AI web search
        web_intel_task = fetch_web_intel(
            player_team=corrected_team_name or req.teamName or "",
            opponent=req.opponentName or "",
            match_date=(match_odds or {}).get("matchDate", ""),
            match_round=(match_odds or {}).get("matchRound", ""),
            league=(match_odds or {}).get("matchLeague", ""),
            timeout=18,
        )
        ai_press_task = fetch_ai_press_intensity(
            opponent=req.opponentName or "",
            league=(match_odds or {}).get("matchLeague", ""),
            timeout=15,
        )

        all_wave2 = aio.gather(
            team_fixture_stats_task, opponent_fixture_stats_task, player_game_logs_task,
            ai_digest_task, situation_task, web_intel_task, ai_press_task,
            return_exceptions=True
        )
        try:
            results = await aio.wait_for(all_wave2, timeout=55)
        except aio.TimeoutError:
            results = [None, None, None, None, None, None, None]
            print(f"[WAVE2 TIMEOUT] Wave 2 exceeded 55s for {req.playerName}")

        team_fixture_stats = results[0] if not isinstance(results[0], (Exception, type(None))) else []
        opponent_fixture_stats = results[1] if not isinstance(results[1], (Exception, type(None))) else []
        player_game_logs = results[2] if not isinstance(results[2], (Exception, type(None))) else []
        ai_digest = results[3] if len(results) > 3 and not isinstance(results[3], (Exception, type(None))) else ""

        # ── Await manager task (nearly instant on cache hit, <1 API call/7 days) ───
        _manager_ctx = {}
        _manager_possession_drift = {}
        if _manager_task is not None:
            try:
                _manager_ctx = await _manager_task or {}
                if _manager_ctx.get("isRecent"):
                    print(
                        f"[MANAGER] ⚠ Recent change: {_manager_ctx.get('prevCoachName','?')} → "
                        f"{_manager_ctx.get('coachName','?')} "
                        f"({_manager_ctx.get('daysElapsed')}d ago, start={_manager_ctx.get('coachStartDate')})"
                    )
                else:
                    print(
                        f"[MANAGER] {_manager_ctx.get('coachName', 'unknown')} "
                        f"(stable, {_manager_ctx.get('daysElapsed','?')}d)"
                    )
            except Exception as _mgr_err:
                print(f"[MANAGER] await error: {_mgr_err}")

        # ── Possession drift: last-5 vs season average for tactical-shift detection ──
        if team_fixture_stats:
            try:
                from manager_tracker import compute_possession_drift as _cpd
                _manager_possession_drift = _cpd(team_fixture_stats) or {}
                if _manager_possession_drift.get("isShift"):
                    print(
                        f"[MANAGER POSS DRIFT] {req.teamName}: "
                        f"season={_manager_possession_drift['seasonAvg']}% → "
                        f"last5={_manager_possession_drift['last5Avg']}% "
                        f"({_manager_possession_drift['drift']:+.1f}pp) ⚠ TACTICAL SHIFT"
                    )
            except Exception as _pd_err:
                print(f"[MANAGER POSS DRIFT] error: {_pd_err}")
        game_situation = results[4] if len(results) > 4 and not isinstance(results[4], (Exception, type(None))) else {}
        web_intel = results[5] if len(results) > 5 and not isinstance(results[5], (Exception, type(None))) else ""
        ai_press_intensity = results[6] if len(results) > 6 and not isinstance(results[6], (Exception, type(None))) else None
        if not game_situation:
            game_situation = {"isKnockout": False, "isSecondLeg": False, "aggregate": {}, "multipliers": {}, "injuries": {}, "contextBlock": ""}

        # =============================================
        # BDL SOCCER STAGE: For BDL-covered leagues (EPL, La Liga, Serie A, Bundesliga,
        # Ligue 1, UCL, MLS, World Cup) try BDL as the PRIMARY source — no daily quota.
        # Runs even when the fixture cache already has API-Football logs: if the BDL
        # quality gate passes (≥3 games with the target stat populated), BDL overrides
        # the fixture cache and the PLAYER-DIRECT stage below is skipped.  When the
        # quality gate fails (Tier-2 stat like passes_total not yet available in BDL),
        # player_game_logs retains fixture-cache data; PLAYER-DIRECT still runs if empty.
        # =============================================
        if _is_bdl_league and _bdl_soc.is_bdl_league(league_id) and req.playerName:
            _bdl_stat_field_map = {
                "goals": "goals_total", "assists": "goals_assists",
                "shots_assisted": "passes_key", "pass_attempts": "passes_total",
                "passes": "passes_total", "shots": "shots_total",
                "shots_on_target": "shots_on", "tackles": "tackles_total",
                 "key_passes": "passes_key", "saves": "goals_saves",
                 "goalie_saves": "goals_saves",
                "interceptions": "tackles_interceptions", "blocks": "tackles_blocks",
                "dribbles": "dribbles_attempts", "fouls_drawn": "fouls_drawn",
                "fouls_committed": "fouls_committed", "crosses": "passes_crosses",
                "clearances": "tackles_clearances", "duels_won": "duels_won",
                "yellow_cards": "cards_yellow",
            }
            _bdl_gl_key = _bdl_stat_field_map.get(req.propType, "passes_total")
            try:
                _bdl_logs, _bdl_pid = await _bdl_soc.get_game_logs(
                    league_id, req.playerName, last_n=25
                )
                if _bdl_logs:
                    # Quality gate: only adopt BDL logs when the target stat
                    # field is actually populated (BDL tier-2 stats like
                    # passes_total / tackles are often None for new seasons).
                    # If fewer than 3 logs have data for this prop, fall
                    # through to the API-Football PLAYER-DIRECT stage instead.
                    _useful = sum(
                        1 for _g in _bdl_logs if _g.get(_bdl_gl_key) is not None
                    )
                    if _useful >= 3:
                        # Add per-90 for the target stat where possible
                        for _g in _bdl_logs:
                            _mins = _g.get("minutes") or 0
                            _sval = _g.get(_bdl_gl_key)
                            if _sval is not None and _mins > 0:
                                _g["targetStatPer90"] = round((_sval / _mins) * 90, 2)
                        player_game_logs = _bdl_logs
                        print(f"[BDL-SOCCER] {req.playerName}/{req.propType}: "
                              f"{len(_bdl_logs)} logs, {_useful} with {_bdl_gl_key} "
                              f"(league {league_id})")
                    else:
                        print(f"[BDL-SOCCER] {req.playerName}/{req.propType}: "
                              f"only {_useful}/3 logs have '{_bdl_gl_key}' data — "
                              f"using cached game logs (no API-Football fallback)")
            except Exception as _bdl_err:
                print(f"[BDL-SOCCER] Error for {req.playerName}: {_bdl_err}")


        # =============================================
        # PLAYER-DIRECT API FALLBACK: When fixture cache misses, fetch the player's
        # recent fixtures directly from the API by player ID — no team cache needed.
        # Skipped for BDL leagues — BDL is the sole source, no API-Football fallback.
        # =============================================
        if not player_game_logs and req.playerId and not _is_bdl_league:
            _gl_field_map2 = {
                "goals": "goals_total", "assists": "goals_assists",
                "shots_assisted": "passes_key", "pass_attempts": "passes_total",
                "passes": "passes_total", "shots": "shots_total",
                "shots_on_target": "shots_on", "tackles": "tackles_total",
                 "key_passes": "passes_key", "saves": "goals_saves",
                 "goalie_saves": "goals_saves",
                "interceptions": "tackles_interceptions", "blocks": "tackles_blocks",
                "dribbles": "dribbles_attempts", "fouls_drawn": "fouls_drawn",
                "fouls_committed": "fouls_committed", "crosses": "passes_crosses",
                "clearances": "tackles_clearances", "duels_won": "duels_won",
                "yellow_cards": "cards_yellow",
            }
            _stat_key_map2 = {
                "goals": ("goals", "total"), "assists": ("goals", "assists"),
                "shots_assisted": ("passes", "key"), "pass_attempts": ("passes", "total"),
                "passes": ("passes", "total"), "shots": ("shots", "total"),
                "shots_on_target": ("shots", "on"), "tackles": ("tackles", "total"),
                "key_passes": ("passes", "key"), "saves": ("goals", "saves"),
                "interceptions": ("tackles", "interceptions"), "blocks": ("tackles", "blocks"),
                "dribbles": ("dribbles", "attempts"), "fouls_drawn": ("fouls", "drawn"),
                "fouls_committed": ("fouls", "committed"), "crosses": ("passes", "cross"),
                "clearances": ("tackles", "clearances"), "duels_won": ("duels", "won"),
                "yellow_cards": ("cards", "yellow"),
            }
            _gl_key2 = _gl_field_map2.get(req.propType, "passes_total")

            # Stage 1: Pull the player's last 20 fixtures directly from API by player ID.
            # 20 is sufficient: after home/away split we get ~10 venue-specific samples,
            # which is plenty for the Bayesian engine. Reduced from 40 to save API quota.
            try:
                print(f"[PLAYER-DIRECT] {req.playerName}: fetching fixtures directly by playerId={req.playerId}")
                _player_fixtures_raw = await api_football_request(
                    "fixtures", {"player": req.playerId, "last": 20}
                )
                if _player_fixtures_raw and actual_team_id and not _is_wc:
                    # Filter to ONLY fixtures where the player's club team appears.
                    # Fetching by player ID returns ALL competitions including national
                    # team games — strip those out so we only analyse club fixtures.
                    # For WC (leagueId=1), skip this filter: we WANT club fixtures
                    # since WC stats are not yet available from API-Football.
                    _before_filter = len(_player_fixtures_raw)
                    _player_fixtures_raw = [
                        fx for fx in _player_fixtures_raw
                        if (fx.get("teams", {}).get("home", {}).get("id") == actual_team_id
                            or fx.get("teams", {}).get("away", {}).get("id") == actual_team_id)
                    ]
                    if len(_player_fixtures_raw) < _before_filter:
                        print(f"[PLAYER-DIRECT] {req.playerName}: filtered {_before_filter} → {len(_player_fixtures_raw)} club fixtures (dropped national-team games)")
                elif _player_fixtures_raw and _is_wc:
                    print(f"[WC MODE] {req.playerName}: keeping all {len(_player_fixtures_raw)} fixtures as club-stat prior for WC")

                if _player_fixtures_raw:
                    # For each fixture, fetch per-game stats
                    _sem2 = aio.Semaphore(10)
                    async def _fetch_player_fix_stats(fix_raw):
                        try:
                            fid = fix_raw.get("fixture", {}).get("id")
                            if not fid:
                                return None
                            home_team_id = fix_raw.get("teams", {}).get("home", {}).get("id")
                            player_fix_venue = "home" if home_team_id == actual_team_id else "away"
                            fix_date = fix_raw.get("fixture", {}).get("date", "")[:10]
                            fix_league = fix_raw.get("league", {}).get("name", "")
                            fix_round = fix_raw.get("league", {}).get("round", "")
                            fix_opp_key = "away" if home_team_id == actual_team_id else "home"
                            fix_opponent = fix_raw.get("teams", {}).get(fix_opp_key, {}).get("name", "")
                            home_goals = fix_raw.get("goals", {}).get("home", 0) or 0
                            away_goals = fix_raw.get("goals", {}).get("away", 0) or 0

                            # Check cache first
                            ck = f"fxp_{fid}_{req.playerId}"
                            cached_doc = await db.fixture_player_cache.find_one({"_k": ck}, {"_id": 0, "d": 1})
                            if cached_doc and cached_doc.get("d"):
                                gl = cached_doc["d"]
                            else:
                                # Hit the API
                                async with _sem2:
                                    fix_data = await api_football_request("fixtures/players", {"fixture": fid})
                                if not fix_data:
                                    return None
                                gl = None
                                all_player_logs_inner = {}
                                for team_data in fix_data:
                                    for p in team_data.get("players", []):
                                        pid = p.get("player", {}).get("id")
                                        stats = p.get("statistics", [{}])[0] if p.get("statistics") else {}
                                        mins = stats.get("games", {}).get("minutes") or 0
                                        if pid:
                                            built = {
                                                "minutes": mins,
                                                "passes_total": stats.get("passes", {}).get("total"),
                                                "passes_key": stats.get("passes", {}).get("key"),
                                                "passes_crosses": stats.get("passes", {}).get("cross"),
                                                "shots_total": stats.get("shots", {}).get("total"),
                                                "shots_on": stats.get("shots", {}).get("on"),
                                                "tackles_total": stats.get("tackles", {}).get("total"),
                                                "tackles_interceptions": stats.get("tackles", {}).get("interceptions"),
                                                "tackles_blocks": stats.get("tackles", {}).get("blocks"),
                                                "tackles_clearances": stats.get("tackles", {}).get("clearances"),
                                                "dribbles_attempts": stats.get("dribbles", {}).get("attempts"),
                                                "fouls_drawn": stats.get("fouls", {}).get("drawn"),
                                                "fouls_committed": stats.get("fouls", {}).get("committed"),
                                                "duels_won": stats.get("duels", {}).get("won"),
                                                "goals_total": stats.get("goals", {}).get("total"),
                                                "goals_assists": stats.get("goals", {}).get("assists"),
                                                "goals_saves": stats.get("goals", {}).get("saves"),
                                                "cards_yellow": stats.get("cards", {}).get("yellow"),
                                            }
                                            all_player_logs_inner[pid] = built
                                            if pid == req.playerId and mins > 0:
                                                gl = built
                                # Name-based fallback for Stage 2
                                if gl is None and req.playerName:
                                    _tname = req.playerName.lower().strip()
                                    _tparts = set(_tname.split())
                                    for team_data2 in fix_data:
                                        for p2 in team_data2.get("players", []):
                                            p2name = (p2.get("player", {}).get("name") or "").lower().strip()
                                            p2stats = p2.get("statistics", [{}])[0] if p2.get("statistics") else {}
                                            p2mins = p2stats.get("games", {}).get("minutes") or 0
                                            if not p2name or not p2mins:
                                                continue
                                            p2parts = set(p2name.split())
                                            common2 = _tparts & p2parts
                                            if common2 and len(common2) / max(len(_tparts), len(p2parts)) >= 0.5:
                                                gl = all_player_logs_inner.get(p2.get("player", {}).get("id"))
                                                if gl:
                                                    print(f"[NAME-MATCH-S2] fid={fid}: matched '{req.playerName}' → '{p2name}' (partial)")
                                                    break
                                        if gl:
                                            break
                                # Cache all players from this fixture
                                async def _cache_all_inner(fid_inner, logs_inner):
                                    ops = [
                                        db.fixture_player_cache.update_one(
                                            {"_k": f"fxp_{fid_inner}_{pid_k}"},
                                            {"$set": {"_k": f"fxp_{fid_inner}_{pid_k}", "_ts": datetime.now(timezone.utc), "d": gl_v}},
                                            upsert=True
                                        ) for pid_k, gl_v in logs_inner.items()
                                    ]
                                    if ops:
                                        await aio.gather(*ops, return_exceptions=True)
                                aio.ensure_future(_cache_all_inner(fid, all_player_logs_inner))
                                if gl is None:
                                    return None

                            minutes = gl.get("minutes", 0)
                            if not minutes or minutes == 0:
                                return None
                            gl["date"] = fix_date
                            gl["opponent"] = fix_opponent
                            gl["venue"] = player_fix_venue
                            gl["score"] = f"{home_goals}-{away_goals}"
                            gl["league"] = fix_league
                            gl["round"] = fix_round
                            stat_val = gl.get(_gl_key2)
                            if stat_val is not None and minutes > 0:
                                gl["targetStatPer90"] = round((stat_val / minutes) * 90, 2)
                            return gl
                        except Exception:
                            return None

                    _pf_tasks = [_fetch_player_fix_stats(fx) for fx in _player_fixtures_raw]
                    _pf_results = await aio.gather(*_pf_tasks, return_exceptions=True)
                    for r in _pf_results:
                        if r and not isinstance(r, Exception):
                            player_game_logs.append(r)

                    if player_game_logs:
                        print(f"[PLAYER-DIRECT] {req.playerName}/{req.propType}: fetched {len(player_game_logs)} real game logs via player API")
            except Exception as _pde:
                print(f"[PLAYER-DIRECT] Error: {_pde}")

        # Stage 2: Season aggregate fallback — only if API direct also returned nothing
        if not player_game_logs and player_stats:
            _sfm_fallback = {
                "goals": ("goals", "total"), "assists": ("goals", "assists"),
                "shots_assisted": ("passes", "key"), "pass_attempts": ("passes", "total"),
                "passes": ("passes", "total"), "shots": ("shots", "total"),
                "shots_on_target": ("shots", "on"), "tackles": ("tackles", "total"),
                "key_passes": ("passes", "key"), "saves": ("goals", "saves"),
                "interceptions": ("tackles", "interceptions"), "blocks": ("tackles", "blocks"),
                "dribbles": ("dribbles", "attempts"), "fouls_drawn": ("fouls", "drawn"),
                "fouls_committed": ("fouls", "committed"), "crosses": ("passes", "cross"),
                "clearances": ("tackles", "clearances"), "duels_won": ("duels", "won"),
                "yellow_cards": ("cards", "yellow"),
            }
            _gl_field_map3 = {
                "goals": "goals_total", "assists": "goals_assists",
                "shots_assisted": "passes_key", "pass_attempts": "passes_total",
                "passes": "passes_total", "shots": "shots_total",
                "shots_on_target": "shots_on", "tackles": "tackles_total",
                 "key_passes": "passes_key", "saves": "goals_saves",
                 "goalie_saves": "goals_saves",
                "interceptions": "tackles_interceptions", "blocks": "tackles_blocks",
                "dribbles": "dribbles_attempts", "fouls_drawn": "fouls_drawn",
                "fouls_committed": "fouls_committed", "crosses": "passes_crosses",
                "clearances": "tackles_clearances", "duels_won": "duels_won",
                "yellow_cards": "cards_yellow",
            }
            _best_stat = None
            _best_appearances = 0
            _best_minutes = 0
            for _stat_entry in (player_stats.get("statistics") or []):
                _apps = _stat_entry.get("games", {}).get("appearences") or 0
                _mins = _stat_entry.get("games", {}).get("minutes") or 0
                if _apps >= 3 and _mins >= 270 and _apps > _best_appearances:
                    _cat, _sub = _sfm_fallback.get(req.propType, ("passes", "total"))
                    _raw = _stat_entry.get(_cat, {}).get(_sub)
                    if _raw is not None:
                        _best_stat = _stat_entry
                        _best_appearances = _apps
                        _best_minutes = _mins

            if _best_stat:
                _cat, _sub = _sfm_fallback.get(req.propType, ("passes", "total"))
                _raw_total = _best_stat.get(_cat, {}).get(_sub) or 0
                _avg_per_game = round(_raw_total / _best_appearances, 2) if _best_appearances else 0
                _avg_minutes = round(_best_minutes / _best_appearances, 1) if _best_appearances else 90
                _gl_key3 = _gl_field_map3.get(req.propType, "passes_total")
                _n_synthetic = min(_best_appearances, 20)
                for _i in range(_n_synthetic):
                    _syn_log = {
                        _gl_key3: _avg_per_game,
                        "minutes": _avg_minutes,
                        "date": "", "opponent": "",
                        "venue": "home" if _i % 2 == 0 else "away",
                        "score": "",
                        "league": (_best_stat.get("league") or {}).get("name", ""),
                        "round": "", "synthetic": True,
                    }
                    if _avg_per_game and _avg_minutes > 0:
                        _syn_log["targetStatPer90"] = round((_avg_per_game / _avg_minutes) * 90, 2)
                    player_game_logs.append(_syn_log)
                print(f"[SEASON FALLBACK] {req.playerName}/{req.propType}: built {_n_synthetic} synthetic logs from season avg={_avg_per_game}/game")
            else:
                print(f"[NO GAME LOGS] {req.playerName}/{req.propType}: no game logs anywhere. Using line as prior.")

        # =============================================
        # MATCH DOMINANCE: Opponent-aware possession + context multiplier
        # =============================================
        def compute_match_dominance(team_stats_list, opp_stats_list, odds, is_home, standing_data, is_neutral=False):
            """Compute expected possession using opponent-aware model + odds adjustment.
            SYMMETRIC: Always computes from HOME team perspective first, then maps back.
            This ensures the SAME match always produces identical possession numbers
            regardless of which player (home or away) triggers the analysis.

            Uses venue-split averages: home team's HOME-game possession avg vs
            away team's AWAY-game possession avg. Overall averages inflate expected
            possession for away teams (e.g. Braga 54% overall but ~48% away).

            For is_neutral=True: uses overall averages for both teams and skips the
            home-venue possession boost (+1.5pp). Used for World Cup / tournament
            games where neither team has a real home-ground advantage."""
            dom = {
                "expectedPoss": 50.0,
                "oppExpectedPoss": 50.0,
                "multiplier": 1.0,
                "notes": [],
                "seasonAvgIsReal": False,
                "hasRealPossData": False,
            }

            def avg_poss(sl, venue_filter=None):
                vals = []
                for s in (sl or []):
                    if venue_filter and s.get("venue") != venue_filter:
                        continue
                    p = s.get("possession")
                    if p is not None:
                        try:
                            vals.append(float(str(p).replace("%", "")))
                        except (ValueError, TypeError):
                            pass
                return round(sum(vals) / len(vals), 1) if vals else None

            def avg_passes(sl):
                """Average total passes per game from fixture stats."""
                vals = []
                for s in (sl or []):
                    v = s.get("totalPasses")
                    if v is not None:
                        try:
                            vals.append(int(v))
                        except (ValueError, TypeError):
                            pass
                return round(sum(vals) / len(vals), 1) if vals else None

            if is_neutral:
                # Neutral venue: no home/away split — use overall averages for both teams.
                # Home/away splits inflate numbers from qualifier mismatches (e.g. a team
                # that averaged 67% possession at home against weak qualifiers). Using
                # overall averages is more honest for a neutral-venue tournament match.
                if is_home:
                    home_avg = avg_poss(team_stats_list)
                    away_avg = avg_poss(opp_stats_list)
                    home_rank = standing_data.get("teamRank") if standing_data else None
                    away_rank = standing_data.get("oppRank") if standing_data else None
                else:
                    home_avg = avg_poss(opp_stats_list)
                    away_avg = avg_poss(team_stats_list)
                    home_rank = standing_data.get("oppRank") if standing_data else None
                    away_rank = standing_data.get("teamRank") if standing_data else None
            elif is_home:
                # Player's team is HOME → use their home game avg; opponent uses away game avg
                home_avg = avg_poss(team_stats_list, "home") or avg_poss(team_stats_list)
                away_avg = avg_poss(opp_stats_list, "away") or avg_poss(opp_stats_list)
                home_rank = standing_data.get("teamRank") if standing_data else None
                away_rank = standing_data.get("oppRank") if standing_data else None
            else:
                # Player's team is AWAY → use their away game avg; opponent (home) uses home game avg
                home_avg = avg_poss(opp_stats_list, "home") or avg_poss(opp_stats_list)
                away_avg = avg_poss(team_stats_list, "away") or avg_poss(team_stats_list)
                home_rank = standing_data.get("oppRank") if standing_data else None
                away_rank = standing_data.get("teamRank") if standing_data else None

            # For the possession squeeze engine, also compute overall season averages
            team_avg = avg_poss(team_stats_list)
            opp_avg = avg_poss(opp_stats_list)

            # Fallback: when possession data is unavailable, estimate from standings
            # gap only. Each rank position ≈ 0.8% possession difference.
            if (home_avg is None or away_avg is None) and home_rank and away_rank:
                gap = away_rank - home_rank  # positive = home team stronger
                raw_poss = 50.0 + 2.5 + min(8.0, max(-8.0, gap * 0.8))
                home_poss_fallback = min(72.0, max(28.0, round(raw_poss, 1)))
                away_poss_fallback = round(100.0 - home_poss_fallback, 1)
                # Use 50% as season avg so the squeeze can activate on big gaps
                fallback_home_avg = 50.0
                fallback_away_avg = 50.0
                # Neutral: formula maps player_team→"away", opponent→"home".
                # Use away_poss_fallback for player regardless of user-entered venue.
                if is_home:
                    dom["expectedPoss"] = home_poss_fallback
                    dom["oppExpectedPoss"] = away_poss_fallback
                    dom["teamSeasonAvg"] = fallback_home_avg
                    dom["oppSeasonAvg"] = fallback_away_avg
                else:
                    dom["expectedPoss"] = away_poss_fallback
                    dom["oppExpectedPoss"] = home_poss_fallback
                    dom["teamSeasonAvg"] = fallback_away_avg
                    dom["oppSeasonAvg"] = fallback_home_avg
                dom["homePoss"] = home_poss_fallback
                dom["awayPoss"] = away_poss_fallback
                dom["notes"].append(f"Rank-gap fallback (no poss data): #{home_rank} vs #{away_rank} → {home_poss_fallback:.0f}% home / {away_poss_fallback:.0f}% away")
                player_team_poss = dom["expectedPoss"]
                poss_ratio = player_team_poss / 50.0
                PASS_PROPS = {"pass_attempts", "key_passes", "crosses", "passes"}
                DEF_PROPS = {"tackles", "interceptions", "blocks", "clearances"}
                if req.propType in PASS_PROPS:
                    raw_adj = poss_ratio - 1.0
                    capped_adj = max(-0.35, min(0.35, raw_adj))
                    dom["multiplier"] = round(1.0 + capped_adj, 3)
                elif req.propType in DEF_PROPS:
                    inverse_ratio = (100.0 - player_team_poss) / 50.0
                    raw_adj = inverse_ratio - 1.0
                    capped_adj = max(-0.25, min(0.25, raw_adj))
                    dom["multiplier"] = round(1.0 + capped_adj, 3)

            elif (home_avg is None or away_avg is None):
                # No possession stats AND no standings rankings.
                # Common for WC/Euro/Copa tournament group stage where API-Football
                # doesn't return possession averages for the tournament league.
                # Last-resort: derive expected possession from match odds probability.
                # A 70% win-prob favourite is realistically ~55% possession territory.
                _has_bk = odds and odds.get("bookmakerOdds")
                _has_ao = odds and odds.get("americanOdds")
                if _has_bk or _has_ao:
                    try:
                        if _has_bk:
                            _ho = float(odds["bookmakerOdds"].get("homeWin", 3.0))
                            _ao_v = float(odds["bookmakerOdds"].get("awayWin", 3.0))
                            _hp = 1.0 / max(_ho, 1.01)
                            _ap = 1.0 / max(_ao_v, 1.01)
                        else:
                            # americanOdds: convert to implied win probability
                            def _ml_to_prob_inner(ml):
                                try:
                                    ml = float(ml)
                                    return (-ml) / (-ml + 100.0) if ml < 0 else 100.0 / (ml + 100.0)
                                except Exception:
                                    return 0.33
                            _aod = odds["americanOdds"]
                            _pih = odds.get("playerIsHome")
                            if _pih is None:
                                _pih = is_home
                            _fx_h_p = _ml_to_prob_inner(_aod.get("home", 0))
                            _fx_a_p = _ml_to_prob_inner(_aod.get("away", 0))
                            _no_flip = (is_home == _pih)
                            _hp = _fx_h_p if _no_flip else _fx_a_p
                            _ap = _fx_a_p if _no_flip else _fx_h_p
                        _tot = _hp + _ap
                        if _tot > 0:
                            _norm_h = _hp / _tot   # fixture home team win-prob
                            # 50% win-prob → 50% poss; 75% win-prob → ~56% poss
                            # Slope raised 25→50 so France 91.7% fav → ~73% poss
                            # (old slope: 90% fav → only 60%, missing elite mismatches)
                            _fx_home_poss = round(min(76.0, max(28.0, 50.0 + (_norm_h - 0.5) * 50.0)), 1)
                            _fx_away_poss = round(100.0 - _fx_home_poss, 1)
                            dom["homePoss"] = _fx_home_poss
                            dom["awayPoss"] = _fx_away_poss
                            if is_home:
                                dom["expectedPoss"]    = _fx_home_poss
                                dom["oppExpectedPoss"] = _fx_away_poss
                            else:
                                dom["expectedPoss"]    = _fx_away_poss
                                dom["oppExpectedPoss"] = _fx_home_poss
                            dom["teamSeasonAvg"] = 50.0
                            dom["oppSeasonAvg"]  = 50.0
                            dom["notes"].append(
                                f"Odds-only possession (no stats/standings): "
                                f"{_fx_home_poss:.0f}%/{_fx_away_poss:.0f}%"
                            )
                            _otp = dom["expectedPoss"]
                            _otr = _otp / 50.0
                            _PASS_P = {"pass_attempts", "key_passes", "crosses", "passes"}
                            _DEF_P  = {"tackles", "interceptions", "blocks", "clearances"}
                            _SHT_P  = {"shots", "shots_on_target"}
                            if req.propType in _PASS_P:
                                dom["multiplier"] = round(1.0 + max(-0.35, min(0.35, _otr - 1.0)), 3)
                            elif req.propType in _DEF_P:
                                _inv = (100.0 - _otp) / 50.0
                                dom["multiplier"] = round(1.0 + max(-0.25, min(0.25, _inv - 1.0)), 3)
                            elif req.propType in _SHT_P:
                                dom["multiplier"] = round(1.0 + max(-0.20, min(0.20, (_otr - 1.0) * 0.6)), 3)
                    except Exception as _oe:
                        dom["notes"].append(f"Odds-only possession fallback failed: {_oe}")

            if home_avg is not None and away_avg is not None:

                # ── Qualifying/weak-opponent contamination guard ───────────────
                # National teams in WC/AFCON/CONCACAF qualifying often average
                # 60-70% possession against weak sides (e.g. SA vs Lesotho).
                # These stats contaminate the possession monster when the same
                # team travels to play a much stronger opponent (e.g. Mexico at
                # Azteca). Caps raised (68/72) so elite away teams like France
                # (~63-65% away avg) aren't artificially cut to 58%.
                home_avg = min(home_avg, 72.0)
                away_avg = min(away_avg, 68.0)

                away_concedes = 100.0 - away_avg

                # FIX 3 — Lower monster threshold from 57 → 53.
                # Teams like PSG, Atlético, Inter Miami average 53-57% away
                # possession and consistently suppress opponents more than the
                # old neutral blend captured. Activating the weighted blend
                # earlier gives their possession dominance proper weight.
                if away_avg > 53:
                    extremity = min((away_avg - 53) / 9.0, 1.0)
                    away_weight = 0.60 + extremity * 0.30
                    home_weight = 1.0 - away_weight
                    home_poss = home_weight * home_avg + away_weight * away_concedes
                    dom["notes"].append(f"Possession monster: away avg {away_avg:.0f}% → weight {away_weight*100:.0f}% away-driven (raw base {home_poss:.1f}%)")
                elif home_avg > 57:
                    extremity = min((home_avg - 57) / 11.0, 1.0)
                    home_weight = 0.60 + extremity * 0.30
                    away_weight_blend = 1.0 - home_weight
                    home_concedes = 100.0 - home_avg
                    away_poss_raw = away_weight_blend * away_avg + home_weight * home_concedes
                    home_poss = 100.0 - away_poss_raw
                    dom["notes"].append(f"Possession monster: home avg {home_avg:.0f}% → weight {home_weight*100:.0f}% home-driven (raw base {home_poss:.1f}%)")
                else:
                    home_poss = (home_avg + away_concedes) / 2.0

                # FIX 3 — Home-field possession advantage trimmed 2.5 → 1.5.
                # Data shows home teams don't gain 2.5% possession from venue alone;
                # 1.5% is calibrated from settled pick residuals.
                # Neutral venues (World Cup, etc.) get NO home-field boost.
                if is_neutral:
                    home_boost = 0.0
                else:
                    home_boost = 1.5
                    higher_avg = max(home_avg, away_avg)
                    if higher_avg > 60:
                        dampen = min((higher_avg - 60) / 10.0, 0.7)
                        home_boost *= (1.0 - dampen)
                        dom["notes"].append(f"Home poss boost dampened: {home_boost:.1f}% (dominant team avg {higher_avg:.0f}%)")
                home_poss += home_boost

                if home_rank and away_rank:
                    gap = away_rank - home_rank
                    quality_adj = min(4.0, max(-4.0, gap * 0.4))
                    home_poss += quality_adj
                    if abs(quality_adj) > 1:
                        dom["notes"].append(f"Standings gap (#{home_rank} vs #{away_rank}): {quality_adj:+.1f}% poss adj")

                if odds and odds.get("bookmakerOdds"):
                    try:
                        home_odds_val = float(odds["bookmakerOdds"].get("homeWin", 3.0))
                        away_odds_val = float(odds["bookmakerOdds"].get("awayWin", 3.0))

                        home_prob = 1.0 / max(home_odds_val, 1.01)
                        away_prob = 1.0 / max(away_odds_val, 1.01)
                        prob_diff = home_prob - away_prob

                        odds_dampener = 1.0
                        if away_avg >= 53 or home_avg >= 57:
                            odds_dampener = 0.3
                            dom["notes"].append(f"Possession-dominant team in match ({max(home_avg, away_avg):.0f}% avg): odds signal dampened")
                        elif away_avg >= 50 or home_avg >= 53:
                            odds_dampener = 0.6

                        odds_adj = round(prob_diff * 12 * odds_dampener, 1)
                        odds_adj = min(7.0, max(-7.0, odds_adj))
                        home_poss += odds_adj
                        if abs(odds_adj) > 1:
                            dom["notes"].append(f"Odds signal (home={home_odds_val:.2f}, away={away_odds_val:.2f}): {odds_adj:+.1f}% poss adj")
                    except Exception:
                        pass

                # FIX 2 — Regression to mean (22% shrink toward 50%).
                home_poss = round(50.0 + (home_poss - 50.0) * 0.78, 1)

                # Ceiling raised 67% → 73%. 67% made France vs Iraq
                # (72-76% realistic) physically impossible.
                home_poss = min(73.0, max(28.0, round(home_poss, 1)))

                # ── EXTREME MISMATCH POST-CORRECTION ─────────────────────────
                # When odds show one team is a massive favourite (≥ 85% implied
                # win prob), the season-avg monster formula can land on the wrong
                # value — e.g. Iraq averages 59% possession at home against weak
                # Asian sides but is +2200 against France.
                #
                # The correction is applied AFTER all formula steps so it can't
                # get confused by home/away/neutral direction logic.
                #
                # We work purely in "formula-home" space: home_poss is always the
                # formula-home team's possession, and the formula-home team is the
                # OPPONENT when the player is away/neutral (see home_avg assignment
                # above). So we need the formula-home team's WIN PROBABILITY.
                #
                # Formula-home win prob:
                #   player is home (non-neutral) → formula-home = player team
                #   player is away or neutral    → formula-home = opponent team
                try:
                    _ep_fh_prob = None   # formula-home team's win prob (0-1)
                    # Determine which odds key maps to the FORMULA-HOME team.
                    #
                    # formula-home team is defined by the code above:
                    #   if is_home and not is_neutral → formula-home = player team
                    #   else (away OR neutral)        → formula-home = opponent team
                    #
                    # odds.home / bookmakerOdds.homeWin always = FIXTURE-HOME team.
                    #
                    # Non-neutral games:
                    #   player home  → formula-home = player = fixture-home  → use home odds
                    #   player away  → formula-home = opp   = fixture-home   → use home odds
                    #   Either way: use home odds. ✓
                    #
                    # Neutral games (is_neutral=True) → ELSE branch, formula-home = opponent:
                    #   playerIsHome=True  → player = fixture-home, opp = fixture-AWAY
                    #                       → formula-home = opp = fixture-away → use AWAY odds
                    #   playerIsHome=False → player = fixture-away, opp = fixture-home
                    #                       → formula-home = opp = fixture-home → use HOME odds
                    #
                    # So: use AWAY odds only when (is_neutral AND playerIsHome).
                    _ep_pih = odds.get("playerIsHome")
                    if _ep_pih is None:
                        _ep_pih = bool(is_home)
                    _ep_use_away = False

                    if odds and odds.get("bookmakerOdds"):
                        _bh = float(odds["bookmakerOdds"].get("homeWin", 3.0))
                        _ba = float(odds["bookmakerOdds"].get("awayWin", 3.0))
                        _bkh = 1.0 / max(_bh, 1.01)
                        _bka = 1.0 / max(_ba, 1.01)
                        _bkt = _bkh + _bka
                        if _bkt > 0:
                            _ep_fh_prob = (_bka if _ep_use_away else _bkh) / _bkt
                    elif odds and odds.get("americanOdds"):
                        def _ml2p_ep(ml):
                            ml = float(ml)
                            return (-ml) / (-ml + 100.0) if ml < 0 else 100.0 / (ml + 100.0)
                        _eaod = odds["americanOdds"]
                        _eih  = _ml2p_ep(_eaod.get("home", 0))  # fixture-home team prob
                        _eia  = _ml2p_ep(_eaod.get("away", 0))  # fixture-away team prob
                        _eit  = _eih + _eia
                        if _eit > 0:
                            _ep_fh_prob = (_eia if _ep_use_away else _eih) / _eit

                    if _ep_fh_prob is not None:
                        _ep_fav_prob = max(_ep_fh_prob, 1.0 - _ep_fh_prob)
                        if _ep_fav_prob >= 0.82:
                            # Odds-only expected possession for formula-home:
                            # calibrated so 95% fav → ~75%, 85% fav → ~61%
                            _ep_odds_hp = max(25.0, min(75.0,
                                50.0 + (_ep_fh_prob - 0.5) * 55.0))
                            # Blend weight: 82% → 0%, 90% → 80%, 95% → 100%
                            _ep_w = min(1.0, (_ep_fav_prob - 0.82) / 0.08)
                            _old_hp = home_poss
                            home_poss = round(
                                _ep_w * _ep_odds_hp + (1.0 - _ep_w) * home_poss, 1)
                            home_poss = min(73.0, max(28.0, home_poss))
                            dom["notes"].append(
                                f"Extreme mismatch corr (w={_ep_w:.0%}, "
                                f"fh_prob={_ep_fh_prob:.1%}): "
                                f"{_old_hp:.0f}%→{home_poss:.0f}%")
                except Exception:
                    pass
                away_poss = round(100.0 - home_poss, 1)

                if is_home:
                    dom["expectedPoss"] = home_poss
                    dom["oppExpectedPoss"] = away_poss
                    dom["teamSeasonAvg"] = home_avg
                    dom["oppSeasonAvg"] = away_avg
                else:
                    dom["expectedPoss"] = away_poss
                    dom["oppExpectedPoss"] = home_poss
                    dom["teamSeasonAvg"] = away_avg
                    dom["oppSeasonAvg"] = home_avg

                dom["homePoss"] = home_poss
                dom["awayPoss"] = away_poss
                # This branch can only be reached when both teams have
                # possession observations.  Keep this separate from the
                # expected possession itself: rank-gap and odds-only fallbacks
                # also produce a number, but their synthetic 50% season
                # baselines must not activate possession-dependent layers.
                dom["hasRealPossData"] = bool(team_avg is not None and opp_avg is not None)
                dom["seasonAvgIsReal"] = dom["hasRealPossData"]

                player_team_poss = dom["expectedPoss"]
                poss_ratio = player_team_poss / team_avg if team_avg > 0 else 1.0
                PASS_PROPS = {"pass_attempts", "key_passes", "crosses", "passes"}
                DEF_PROPS = {"tackles", "interceptions", "blocks", "clearances"}

                if req.propType in PASS_PROPS:
                    raw_adj = poss_ratio - 1.0
                    capped_adj = max(-0.35, min(0.35, raw_adj))
                    dom["multiplier"] = round(1.0 + capped_adj, 3)
                    if abs(capped_adj) > 0.03:
                        direction = "boost" if capped_adj > 0 else "drop"
                        dom["notes"].append(f"Pass volume {direction}: expected {player_team_poss:.0f}% poss vs {team_avg:.0f}% avg (ratio={poss_ratio:.2f}) → {capped_adj*100:+.0f}%")
                elif req.propType in DEF_PROPS:
                    inverse_ratio = (100.0 - player_team_poss) / (100.0 - team_avg) if team_avg < 100 else 1.0
                    raw_adj = inverse_ratio - 1.0
                    capped_adj = max(-0.25, min(0.25, raw_adj))
                    dom["multiplier"] = round(1.0 + capped_adj, 3)
                    if abs(capped_adj) > 0.03:
                        direction = "boost" if capped_adj > 0 else "drop"
                        dom["notes"].append(f"Def action {direction}: expected {100-player_team_poss:.0f}% without ball vs {100-team_avg:.0f}% avg → {capped_adj*100:+.0f}%")
                elif req.propType in {"shots", "shots_on_target"}:
                    raw_adj = (poss_ratio - 1.0) * 0.6
                    capped_adj = max(-0.20, min(0.20, raw_adj))
                    dom["multiplier"] = round(1.0 + capped_adj, 3)
                    if abs(capped_adj) > 0.03:
                        dom["notes"].append(f"Shot volume adj from possession ratio → {capped_adj*100:+.0f}%")

            # Team pass-rate for positional baseline (Layer 2)
            _tap = avg_passes(team_stats_list)
            if _tap is not None:
                dom["teamAvgPasses"] = _tap

            return dom

        # Compute standings data for match dominance
        standing_data = {}
        if standings:
            for s in standings:
                s_team = s.get("team", "")
                s_team_name = s_team.get("name", "") if isinstance(s_team, dict) else str(s_team)
                s_team_id = s_team.get("id", "") if isinstance(s_team, dict) else s.get("team_id", "")
                if s_team_name.lower() == req.teamName.lower() or str(s_team_id) == str(req.teamId):
                    standing_data["teamRank"] = s.get("rank")
                if s_team_name.lower() == req.opponentName.lower() or str(s_team_id) == str(req.opponentId):
                    standing_data["oppRank"] = s.get("rank")

        # Determine canonical (home_team_id, away_team_id) for cache key.
        # Use the fixture's playerIsHome flag when available — this tells us
        # which team API-Football actually designated as "home" in the fixture,
        # regardless of what the user typed in the venue field. This is the
        # ONLY reliable source of truth for home/away orientation.
        _pih_flag = match_odds.get("playerIsHome") if match_odds else None
        if _pih_flag is not None:
            _is_home = bool(_pih_flag)
        elif _is_neutral:
            # No odds data available — use team ID as a deterministic tiebreaker
            # so BOTH player scans always produce the same fixture-perspective
            # homePoss/awayPoss values. Without this, both teams are assigned
            # is_home=False (formula-away), the formula is symmetric, and teams
            # with similar qualifier stats produce identical possession numbers.
            _is_home = (actual_team_id or 0) < (req.opponentId or 0)
        else:
            _is_home = player_venue == "home"
        _home_id = actual_team_id if _is_home else req.opponentId
        _away_id = req.opponentId if _is_home else actual_team_id
        _dom_cache_key = (_home_id, _away_id) if (_home_id and _away_id) else None

        # Check cache first — same game always returns same possession
        _cached_dom = None
        if _dom_cache_key:
            _entry = _match_dom_cache.get(_dom_cache_key)
            if _entry and (_time.time() - _entry["ts"]) < _MATCH_DOM_TTL:
                _cached_dom = _entry["dom"]

        if _cached_dom is not None:
            # Remap expectedPoss/oppExpectedPoss for this player's perspective
            match_dominance = dict(_cached_dom)
            if _is_home:
                match_dominance["expectedPoss"] = _cached_dom["homePoss"]
                match_dominance["oppExpectedPoss"] = _cached_dom["awayPoss"]
                match_dominance["teamSeasonAvg"] = _cached_dom.get("homeSeasonAvg", _cached_dom.get("teamSeasonAvg"))
                match_dominance["oppSeasonAvg"] = _cached_dom.get("awaySeasonAvg", _cached_dom.get("oppSeasonAvg"))
            else:
                match_dominance["expectedPoss"] = _cached_dom["awayPoss"]
                match_dominance["oppExpectedPoss"] = _cached_dom["homePoss"]
                match_dominance["teamSeasonAvg"] = _cached_dom.get("awaySeasonAvg", _cached_dom.get("oppSeasonAvg"))
                match_dominance["oppSeasonAvg"] = _cached_dom.get("homeSeasonAvg", _cached_dom.get("teamSeasonAvg"))

            # CRITICAL: multiplier is prop-type-specific — MUST be recomputed from
            # cached possession data for the CURRENT prop type.  The cached value was
            # set by whichever prop type hit this match first (e.g. clearances → +17%
            # defensive boost) and is WRONG for a different prop type (e.g. pass_attempts).
            _cp = match_dominance["expectedPoss"]
            _ca = match_dominance.get("teamSeasonAvg") or 50.0
            _PASS_PROPS_C  = {"pass_attempts", "key_passes", "crosses", "passes"}
            _DEF_PROPS_C   = {"tackles", "interceptions", "blocks", "clearances"}
            _SHOT_PROPS_C  = {"shots", "shots_on_target"}
            if req.propType in _PASS_PROPS_C:
                _poss_ratio_c = _cp / _ca if _ca > 0 else 1.0
                _capped_c = max(-0.35, min(0.35, _poss_ratio_c - 1.0))
                match_dominance["multiplier"] = round(1.0 + _capped_c, 3)
            elif req.propType in _DEF_PROPS_C:
                _inv_ratio_c = (100.0 - _cp) / (100.0 - _ca) if _ca < 100 else 1.0
                _capped_c = max(-0.25, min(0.25, _inv_ratio_c - 1.0))
                match_dominance["multiplier"] = round(1.0 + _capped_c, 3)
            elif req.propType in _SHOT_PROPS_C:
                _poss_ratio_c = _cp / _ca if _ca > 0 else 1.0
                _capped_c = max(-0.20, min(0.20, (_poss_ratio_c - 1.0) * 0.6))
                match_dominance["multiplier"] = round(1.0 + _capped_c, 3)
            else:
                match_dominance["multiplier"] = 1.0

            print(f"[MATCH DOMINANCE CACHE HIT] {req.playerName}: home={_cached_dom['homePoss']}% away={_cached_dom['awayPoss']}% mult_recalc={match_dominance['multiplier']} for {req.propType}")
        else:
            # Build effective odds: API-fetched match_odds is preferred, but for
            # WC/tournament games the odds API often returns nothing. Fall back to
            # req.odds (user-supplied from the mobile app) so the possession
            # extreme-mismatch correction fires correctly for e.g. Portugal -1111
            # vs Uzbekistan +2200 WC group-stage predictions.
            _eff_odds = match_odds or {}
            if req.odds and not _eff_odds.get("bookmakerOdds") and not _eff_odds.get("americanOdds"):
                _req_o = req.odds if isinstance(req.odds, dict) else (req.odds.dict() if hasattr(req.odds, "dict") else {})
                if _req_o.get("bookmakerOdds") or _req_o.get("americanOdds"):
                    _eff_odds = dict(_eff_odds)
                    _eff_odds.update(_req_o)
            match_dominance = compute_match_dominance(
                team_fixture_stats, opponent_fixture_stats, _eff_odds,
                _is_home, standing_data, is_neutral=_is_neutral
            )
            # Store in cache with home/away season avgs for perspective remapping
            if _dom_cache_key and match_dominance.get("homePoss") is not None:
                _cache_entry = dict(match_dominance)
                if _is_home:
                    _cache_entry["homeSeasonAvg"] = match_dominance.get("teamSeasonAvg")
                    _cache_entry["awaySeasonAvg"] = match_dominance.get("oppSeasonAvg")
                else:
                    _cache_entry["homeSeasonAvg"] = match_dominance.get("oppSeasonAvg")
                    _cache_entry["awaySeasonAvg"] = match_dominance.get("teamSeasonAvg")
                _match_dom_cache[_dom_cache_key] = {"ts": _time.time(), "dom": _cache_entry}

        # hasRealPossData: True only when SOME real signal (possession stats,
        # standings rank-gap, or odds-implied) actually populated expectedPoss —
        # i.e. compute_match_dominance appended a note. When notes is empty the
        # 50.0/50.0 values are a pure hardcoded default with zero information
        # behind them (common for international friendlies vs minnows with no
        # cached possession/standings/odds data at all) and must NOT be treated
        # downstream as a genuine "close matchup" signal (see
        # possession-fallback-unknown-tier.md).
        match_dominance["hasRealPossData"] = bool(match_dominance.get("notes"))
        if match_dominance.get("notes"):
            print(f"[MATCH DOMINANCE] {req.playerName}: poss={match_dominance['expectedPoss']}%, mult={match_dominance['multiplier']}, {' | '.join(match_dominance['notes'])}")
        else:
            print(f"[MATCH DOMINANCE] {req.playerName}: NO real data available (poss/standings/odds all missing) — 50/50 default is uninformative")

        # ─────────────────────────────────────────────────────────────────────
        # H2H POSSESSION OVERRIDE
        # The season-average model can't know that Damac dominates 63%
        # possession specifically against Al-Fayha even if their overall home
        # average is lower. When we have ≥2 H2H fixtures with possession data,
        # we override expectedPoss with a weighted blend:
        #   H2H avg × (50-70%) + season avg × (30-50%)
        # Weight grows with sample count: 2 games=50%, 3=56%, 4=62%, 5+=68%.
        # This is the single biggest source of missed high-pass CB/CDM props.
        #
        # Source priority: DB cache (instant) → /fixtures/statistics API call
        # ─────────────────────────────────────────────────────────────────────
        async def _get_h2h_fixture_poss(fid: int, team_id: int) -> float | None:
            """Return team's possession % in a fixture. Tries cache first, then API."""
            # 1. Try fixture_player_cache (populated from previous predictions)
            try:
                _doc = await db.fixture_player_cache.find_one(
                    {"_k": f"fxt_{fid}_{team_id}"}, {"_id": 0, "d.possession": 1}
                )
                if _doc and _doc.get("d"):
                    _raw = str(_doc["d"].get("possession", "")).replace("%", "").strip()
                    if _raw:
                        return float(_raw)
            except Exception:
                pass
            # 2. Fallback: fetch /fixtures/statistics directly from the API
            try:
                _stats = await api_football_request("fixtures/statistics", {"fixture": fid})
                for _s in (_stats or []):
                    if _s.get("team", {}).get("id") == team_id:
                        for _st in _s.get("statistics", []):
                            if _st.get("type") == "Ball Possession":
                                _val = str(_st.get("value", "")).replace("%", "").strip()
                                if _val:
                                    return float(_val)
            except Exception:
                pass
            return None

        _h2h_team_poss_vals: list[float] = []
        if h2h_data and actual_team_id:
            _h2h_poss_tasks = []
            _h2h_fxt_ids_used = []
            for _hf in h2h_data[:8]:
                _hf_fid  = _hf.get("fixture", {}).get("id")
                _hf_home = _hf.get("teams", {}).get("home", {}).get("id")
                if not _hf_fid:
                    continue
                # CRITICAL: venue-match — only include H2H fixtures where the
                # player's team had the SAME venue as the current prediction.
                # Mixing home and away possession averages to ~50% and wipes out
                # the opponent-specific possession advantage (e.g. Damac 63% HOME
                # vs Fayha but only 38% AWAY → naive avg = 50.5%, useless).
                _player_is_home_in_h2h = (_hf_home == actual_team_id)
                if _player_is_home_in_h2h != _is_home:
                    continue  # skip wrong-venue fixture
                _h2h_poss_tasks.append(_get_h2h_fixture_poss(_hf_fid, actual_team_id))
                _h2h_fxt_ids_used.append(_hf_fid)
            try:
                _h2h_poss_results = await aio.wait_for(
                    aio.gather(*_h2h_poss_tasks), timeout=8
                )
                _h2h_team_poss_vals = [r for r in _h2h_poss_results if r is not None]
                print(f"[H2H POSS FETCH] {req.playerName}: venue={'home' if _is_home else 'away'} "
                      f"venue-matched fixtures={len(_h2h_poss_tasks)}/{len(h2h_data[:8])}, "
                      f"got possession for {len(_h2h_team_poss_vals)}: {_h2h_team_poss_vals}")
            except aio.TimeoutError:
                print(f"[H2H POSS FETCH] timeout for {req.playerName}")
                _h2h_team_poss_vals = []

        _h2h_poss_avg: float | None = None
        if len(_h2h_team_poss_vals) >= 2:
            _h2h_poss_avg = round(sum(_h2h_team_poss_vals) / len(_h2h_team_poss_vals), 1)
            _season_base = match_dominance.get("expectedPoss", 50.0)
            # More H2H samples → higher trust in H2H signal (caps at 70% weight at 5+ games)
            _h2h_n = len(_h2h_team_poss_vals)
            _h2h_weight = min(0.70, 0.50 + (_h2h_n - 2) * 0.06)
            _blended_poss = round(_h2h_weight * _h2h_poss_avg + (1 - _h2h_weight) * _season_base, 1)
            _blended_poss = min(78.0, max(28.0, _blended_poss))
            _blended_opp  = round(100.0 - _blended_poss, 1)
            print(f"[H2H POSS OVERRIDE] {req.playerName}: H2H avg={_h2h_poss_avg}% "
                  f"(n={_h2h_n}, wt={_h2h_weight:.0%}) season={_season_base}% "
                  f"→ blended={_blended_poss}%")
            # Update match_dominance with H2H-blended possession
            if _is_home:
                match_dominance["homePoss"] = _blended_poss
                match_dominance["awayPoss"] = _blended_opp
            else:
                match_dominance["homePoss"] = _blended_opp
                match_dominance["awayPoss"] = _blended_poss
            match_dominance["expectedPoss"]    = _blended_poss
            match_dominance["oppExpectedPoss"] = _blended_opp
            match_dominance["h2hPossAvg"]      = _h2h_poss_avg
            match_dominance["hasRealPossData"] = True
            match_dominance["h2hPossCount"]    = _h2h_n
            # Recompute multiplier from blended possession
            _PASS_H = {"pass_attempts", "key_passes", "crosses", "passes"}
            _DEF_H  = {"tackles", "interceptions", "blocks", "clearances"}
            _t_avg  = match_dominance.get("teamSeasonAvg") or 50.0
            if req.propType in _PASS_H:
                _raw = max(-0.35, min(0.35, (_blended_poss / max(_t_avg, 38.0)) - 1.0))
                match_dominance["multiplier"] = round(1.0 + _raw, 3)
            elif req.propType in _DEF_H:
                _inv = (100.0 - _blended_poss) / max(_t_avg, 38.0)
                _raw = max(-0.25, min(0.25, _inv - 1.0))
                match_dominance["multiplier"] = round(1.0 + _raw, 3)
            match_dominance["notes"].append(
                f"H2H poss override ({_h2h_n} matches): avg {_h2h_poss_avg}% → blended {_blended_poss}%"
            )
            print(f"[H2H POSS OVERRIDE] new multiplier={match_dominance['multiplier']} for {req.propType}")

        # =============================================
        # SITUATION ENGINE: Apply possession boost from knockout/2nd-leg context
        # Overrides the season-average-based possession model when game state demands it
        # =============================================
        _sit_mults = game_situation.get("multipliers", {})
        _sit_poss_boost = _sit_mults.get("possessionBoostHome", 0.0)
        if _sit_poss_boost != 0.0 and match_dominance.get("homePoss") is not None:
            # Apply boost to home team's raw possession, recalculate both sides
            old_home_poss = match_dominance["homePoss"]
            new_home_poss = min(80.0, max(30.0, old_home_poss + _sit_poss_boost))
            new_away_poss = round(100.0 - new_home_poss, 1)
            print(f"[SITUATION BOOST] Possession: home {old_home_poss:.1f}% → {new_home_poss:.1f}% (boost={_sit_poss_boost:+.1f}%)")
            match_dominance["homePoss"] = new_home_poss
            match_dominance["awayPoss"] = new_away_poss
            # Remap player perspective
            if _sit_is_home:
                match_dominance["expectedPoss"] = new_home_poss
                match_dominance["oppExpectedPoss"] = new_away_poss
            else:
                match_dominance["expectedPoss"] = new_away_poss
                match_dominance["oppExpectedPoss"] = new_home_poss
            match_dominance["notes"].extend(_sit_mults.get("notes", []))
            # NOTE: do NOT write boosted values back into _match_dom_cache.
            # The cache holds the clean season-stats-derived possession.
            # The situation boost is applied fresh each call from that clean base.
            # Writing boosted values into the cache causes a compounding spiral:
            # each subsequent call for the same fixture reads the already-boosted
            # value as its new baseline and adds the boost again, e.g.
            # 63% → 72% → 81% → capped 80% across 3 requests.

        # =============================================
        # GAME TEMPO ESTIMATION — Expected match intensity
        # A 2-2 draw = high tempo → both teams pass MORE.
        # A 0-0 grind = low tempo → both teams pass LESS.
        # This adjusts the dominance multiplier based on expected total game activity.
        # =============================================
        game_tempo = {"expectedTempo": "normal", "tempoMultiplier": 1.0, "notes": []}
        try:
            # Signal 1: Both teams' goals-per-game from team stats
            team_gpg = 0.0
            opp_gpg = 0.0
            team_ga_pg = 0.0
            opp_ga_pg = 0.0
            if team_stats:
                fixtures_played = team_stats.get("fixtures", {})
                total_played = (fixtures_played.get("played", {}).get("total") or 0)
                goals_for = team_stats.get("goals", {}).get("for", {}).get("total", {}).get("total", 0) or 0
                goals_against = team_stats.get("goals", {}).get("against", {}).get("total", {}).get("total", 0) or 0
                if total_played > 0:
                    team_gpg = goals_for / total_played
                    team_ga_pg = goals_against / total_played
            if opponent_stats:
                opp_played = (opponent_stats.get("fixtures", {}).get("played", {}).get("total") or 0)
                opp_gf = opponent_stats.get("goals", {}).get("for", {}).get("total", {}).get("total", 0) or 0
                opp_ga = opponent_stats.get("goals", {}).get("against", {}).get("total", {}).get("total", 0) or 0
                if opp_played > 0:
                    opp_gpg = opp_gf / opp_played
                    opp_ga_pg = opp_ga / opp_played

            # Expected total goals in match = (team_gpg + opp_ga_pg)/2 + (opp_gpg + team_ga_pg)/2
            if team_gpg > 0 or opp_gpg > 0:
                expected_team_goals = (team_gpg + opp_ga_pg) / 2.0
                expected_opp_goals = (opp_gpg + team_ga_pg) / 2.0
                expected_total = expected_team_goals + expected_opp_goals

                # Signal 2: Odds-implied over/under (if available)
                if match_odds and match_odds.get("bookmakerOdds"):
                    try:
                        home_odds = float(match_odds["bookmakerOdds"].get("homeWin", 3.0))
                        away_odds = float(match_odds["bookmakerOdds"].get("awayWin", 3.0))
                        # Low home+away odds = both teams expected to score
                        total_implied = 1.0/max(home_odds, 1.01) + 1.0/max(away_odds, 1.01)
                        if total_implied > 0.65:  # Both teams strong favorites to score
                            expected_total += 0.3
                            game_tempo["notes"].append("Odds suggest competitive match")
                    except Exception:
                        pass

                # Classify tempo
                if expected_total >= 3.2:
                    game_tempo["expectedTempo"] = "high"
                    # High-tempo: scale up pass volume by 4-8%
                    tempo_boost = min(0.08, (expected_total - 2.5) * 0.04)
                    game_tempo["tempoMultiplier"] = round(1.0 + tempo_boost, 3)
                    game_tempo["notes"].append(f"High-tempo expected ({expected_total:.1f} total goals) → +{tempo_boost*100:.0f}% pass boost")
                elif expected_total <= 1.8:
                    game_tempo["expectedTempo"] = "low"
                    # Low-tempo: dampen pass volume by 3-6%
                    tempo_drop = max(-0.06, -(2.5 - expected_total) * 0.03)
                    game_tempo["tempoMultiplier"] = round(1.0 + tempo_drop, 3)
                    game_tempo["notes"].append(f"Low-tempo expected ({expected_total:.1f} total goals) → {tempo_drop*100:.0f}% pass reduction")
                else:
                    game_tempo["expectedTempo"] = "normal"
                    game_tempo["tempoMultiplier"] = 1.0

                game_tempo["expectedTotalGoals"] = round(expected_total, 2)
                game_tempo["teamGPG"] = round(team_gpg, 2)
                game_tempo["oppGPG"] = round(opp_gpg, 2)

            if game_tempo["notes"]:
                print(f"[GAME TEMPO] {req.playerName}: tempo={game_tempo['expectedTempo']}, mult={game_tempo['tempoMultiplier']}, goals={game_tempo.get('expectedTotalGoals', '?')}")
        except Exception as e:
            print(f"[GAME TEMPO] Error: {e}")

        # =============================================
        # HEAVY FAVORITE DAMPENING — for OVER pass props
        # When a team is a heavy favorite (odds < 1.6), they're likely
        # to score early and then reduce passing tempo (game management).
        # This creates a "leading-team tempo drop" effect.
        # =============================================
        favorite_dampening = {"applied": False}
        try:
            poss_sensitive_for_fav = {"pass_attempts", "passes", "key_passes", "crosses"}
            if req.propType in poss_sensitive_for_fav and match_odds and match_odds.get("bookmakerOdds"):
                home_odds = float(match_odds["bookmakerOdds"].get("homeWin", 3.0))
                away_odds = float(match_odds["bookmakerOdds"].get("awayWin", 3.0))
                # Use fixture's playerIsHome tag so we pick the right odds regardless
                # of whether player_venue matches the API-Football fixture designation.
                _pifh_damp = match_odds.get("playerIsHome", player_venue == "home")
                team_odds = home_odds if _pifh_damp else away_odds

                if team_odds < 1.60:
                    # Heavy favorite — game management likely in 2nd half
                    # The heavier the favorite, the stronger the dampening
                    fav_dampen = round(min(0.06, (1.60 - team_odds) * 0.10), 3)
                    favorite_dampening = {
                        "applied": True,
                        "teamOdds": team_odds,
                        "dampeningFactor": fav_dampen,
                        "note": f"Heavy favorite ({team_odds:.2f}): leading teams reduce tempo → -{fav_dampen*100:.0f}% pass dampening"
                    }
                    print(f"[FAVORITE DAMPENING] {req.playerName}: odds={team_odds:.2f}, dampen={fav_dampen*100:.0f}%")
        except Exception as e:
            print(f"[FAVORITE DAMPENING] Error: {e}")

        print(f"[TIMING] Wave 2: {_t.time()-_t0:.1f}s total")

        historical_data = {
            "playerStats": player_stats,
            "teamStats": team_stats,
            "opponentStats": opponent_stats,
            "h2hData": h2h_data,
            "standings": standings,
            "recentFixtures": recent_fixtures,
            "matchOdds": match_odds,
        }

        # =============================================
        # Per-fixture deep data (Wave 2 results)
        # =============================================
        if team_fixture_stats:
            historical_data["teamMatchStats"] = team_fixture_stats
        if opponent_fixture_stats:
            historical_data["opponentMatchStats"] = opponent_fixture_stats
        if player_game_logs:
            # Add summary stats for the game logs
            target_field_map = {
                "pass_attempts":          "passes_total",
                "shots":                  "shots_total",
                "shots_on_target":        "shots_on",
                "tackles":                "tackles_total",
                "key_passes":             "passes_key",
                "shots_assisted":         "passes_key",
                "saves":                  "goals_saves",
                "interceptions":          "tackles_interceptions",
                "clearances":             "tackles_clearances",
                "blocks":                 "tackles_blocks",
                "dribbles":               "dribbles_attempts",
                "fouls_drawn":            "fouls_drawn",
                "fouls_committed":        "fouls_committed",
                "crosses":                "passes_crosses",
                "duels_won":              "duels_won",
                "yellow_cards":           "cards_yellow",
            }
            target_field = target_field_map.get(req.propType, "passes_total")
            values = [g.get(target_field) for g in player_game_logs if g.get(target_field) is not None]
            minutes_list = [g.get("minutes", 0) for g in player_game_logs if g.get("minutes")]
            per90_values = [g.get("targetStatPer90") for g in player_game_logs if g.get("targetStatPer90") is not None]

            game_log_summary = {
                "games": player_game_logs,
                "targetProp": req.propType,
                "sampleSize": len(values),
            }
            if values:
                game_log_summary["rawAvg"] = round(sum(values) / len(values), 2)
                game_log_summary["rawMin"] = min(values)
                game_log_summary["rawMax"] = max(values)
                if len(values) >= 3:
                    game_log_summary["stdDev"] = round(stats_mod.stdev(values), 2)
                # Home/away splits
                home_vals = [g.get(target_field) for g in player_game_logs if g.get("venue") == "home" and g.get(target_field) is not None]
                away_vals = [g.get(target_field) for g in player_game_logs if g.get("venue") == "away" and g.get(target_field) is not None]
                if home_vals:
                    game_log_summary["homeAvg"] = round(sum(home_vals) / len(home_vals), 2)
                if away_vals:
                    game_log_summary["awayAvg"] = round(sum(away_vals) / len(away_vals), 2)
            if per90_values:
                game_log_summary["per90Avg"] = round(sum(per90_values) / len(per90_values), 2)
            if minutes_list:
                game_log_summary["avgMinutes"] = round(sum(minutes_list) / len(minutes_list), 1)
            if values and req.line:
                over_hits = sum(1 for v in values if v > req.line)
                under_hits = sum(1 for v in values if v < req.line)
                game_log_summary["hitRates"] = {
                    "overHits": over_hits,
                    "underHits": under_hits,
                    "overPct": round(over_hits / len(values) * 100, 1),
                    "underPct": round(under_hits / len(values) * 100, 1),
                    "total": len(values),
                }

            # ── Annotate each game log with opponent league rank ────────────────
            # Build a quick lookup: lowercased team name → rank from standings.
            # This lets the tile UI show "#7" without extra API calls.
            if standings:
                _rank_map: dict = {}
                for _s in standings:
                    _tname = (_s.get("team") or {}).get("name", "") if isinstance(_s.get("team"), dict) else str(_s.get("team", ""))
                    _rank = _s.get("rank")
                    if _tname and _rank:
                        _rank_map[_tname.lower().strip()] = _rank
                for _gl in game_log_summary["games"]:
                    _opp = (_gl.get("opponent") or "").lower().strip()
                    if _opp and _rank_map:
                        # Try exact match first, then fuzzy prefix match
                        _gl["oppRank"] = _rank_map.get(_opp) or next(
                            (v for k, v in _rank_map.items() if _opp in k or k in _opp), None
                        )

            # ── Quality flag + opponent tier per game log ──────────────────────
            # Standings-based rank only covers opponents that share the SAME
            # standings table as the current prediction's league_id. A
            # national team's game log frequently spans multiple confederations/
            # competitions (qualifying groups, playoffs, friendlies) that never
            # share one table — so most historical opponents would otherwise
            # get no tier at all. Fall back to the curated NATIONAL_TEAM_TIER
            # map (by opponent name) whenever a real rank isn't available.
            for _gl in game_log_summary["games"]:
                _mins = _gl.get("minutes", 0) or 0
                _gl["quality"] = _mins >= 60
                _opp_rank = _gl.get("oppRank")
                if _opp_rank is not None:
                    if _opp_rank <= 6:
                        _gl["oppTier"] = "ELITE"
                    elif _opp_rank <= 15:
                        _gl["oppTier"] = "STRONG"
                    elif _opp_rank <= 30:
                        _gl["oppTier"] = "MID"
                    else:
                        _gl["oppTier"] = "WEAK"
                else:
                    _opp_name = (_gl.get("opponent") or "").lower().strip()
                    _gl["oppTier"] = NATIONAL_TEAM_TIER.get(_opp_name)
                    if _gl["oppTier"] is None and _opp_name:
                        _match = next((v for k, v in NATIONAL_TEAM_TIER.items() if _opp_name in k or k in _opp_name), None)
                        _gl["oppTier"] = _match

            # ── Quality-filtered hit rates (≥60 min games only) ───────────────
            if req.line and "hitRates" in game_log_summary:
                _qual_vals = [
                    g.get(target_field) for g in game_log_summary["games"]
                    if g.get(target_field) is not None and (g.get("minutes", 0) or 0) >= 60
                ]
                if _qual_vals:
                    _q_over = sum(1 for v in _qual_vals if v > req.line)
                    _q_under = sum(1 for v in _qual_vals if v < req.line)
                    game_log_summary["hitRates"]["qualityTotal"] = len(_qual_vals)
                    game_log_summary["hitRates"]["qualityOverHits"] = _q_over
                    game_log_summary["hitRates"]["qualityOverPct"] = round(_q_over / len(_qual_vals) * 100, 1)

            historical_data["playerGameLogs"] = game_log_summary

        # =============================================
        # EARLY BAYESIAN — Compute math BEFORE AI prompt
        # This anchors the AI's reasoning so it doesn't
        # contradict the mathematical evidence.
        # =============================================
        early_bayes = None
        bayesian_prompt_anchor = ""
        # Safety defaults for T003/T004 — always defined even if exception occurs
        _redist_alerts: list = []
        _redist_multiplier: float = 1.0
        _lineup_alert: str | None = None
        _lineup_status: str = "unknown"
        _quality_prior_applied: bool = False
        _quality_prior_dropped: int = 0
        _opp_tier_filter_applied: bool = False
        _opp_tier_filter_dropped: int = 0
        _opp_tier_filter_kept_tiers: list = []
        try:
            from bayesian_engine import compute_bayesian_projection

            # ── Quick position cache lookup (fast indexed read) ──────────────
            # We look up the cached position so the engine can apply the correct
            # momentum decay table AND the position-aware press multiplier
            # (attackers decay faster, GKs decay slower; defenders get press boost).
            #
            # The cache is written by the [POS RESOLVE] block keyed on playerId,
            # Prefer playerId-keyed entries (written by the stats-aware resolver
            # with a versioned prompt). Fall back to playerName only when there
            # is no playerId entry — avoids stale batch-resolver entries that are
            # stored by name only and may have wrong positions (e.g. Vitinha=CB).
            _bayes_position = ""
            _bayes_role     = ""
            try:
                _pos_doc = await db.player_positions.find_one(
                    {"playerId": req.playerId}
                ) if req.playerId else None
                if not _pos_doc:
                    _pos_doc = await db.player_positions.find_one(
                        {"playerName": req.playerName, "playerId": {"$exists": True}}
                    )
                if _pos_doc:
                    _bayes_position = _pos_doc.get("specificPosition", "")
                    _bayes_role     = _pos_doc.get("role", "")
            except Exception:
                pass

            # ── GK detection — always override for saves prop ────────────────
            # "saves" is an exclusively GK stat. If the position cache has a
            # stale/wrong outfield entry (e.g. Oblak cached as "RB"), every
            # downstream GK-specific branch misfires: opponent-concession cap,
            # press boost, venue-split threshold, inverted possession model.
            # Guard: always force GK when propType is saves, regardless of cache.
            if req.propType in {"saves", "goalie_saves"}:
                _bayes_position = "GK"
            elif not _bayes_position:
                if req.propType in {"pass_attempts", "passes"}:
                    # Any saves value in logs = goalkeeper
                    if any(g.get("goals_saves") is not None and g.get("goals_saves", -1) >= 0
                           for g in player_game_logs):
                        _bayes_position = "GK"

            # ── Hyperprior for low-sample players (n < 6) ───────────────────
            # Derive a league-context anchor from opponent fixture stats.
            # Same field map as _estimate_opponent_concession in bayesian_engine.
            # If a player has very few logs this pulls the prior toward the
            # "typical output for this prop type in this match context."
            _bayes_hyperprior = None
            _hp_map = {
                "shots":           ("totalShots",     0.18),
                "shots_on_target": ("shotsOnTarget",  0.18),
                "goals":           ("goals",           0.40),
                "assists":         ("goals",           0.25),
                "saves":           ("shotsOnTarget",   0.70),
                "goalie_saves":    ("shotsOnTarget",   0.70),
                "tackles":         ("totalPasses",     0.015),
                "key_passes":      ("keyPasses",       0.28),
                "crosses":         ("totalCrosses",    0.35),
                "interceptions":   ("totalInterceptions", 0.22),
                "clearances":      ("totalClearances", 0.18),
                "dribbles":        ("dribbleAttempts", 0.30),
                "fouls_drawn":     ("foulsDrawn",      0.25),
                "fouls_committed": ("foulsCommitted",  0.22),
                "duels_won":       ("totalDuels",      0.22),
            }
            if opponent_fixture_stats and len(player_game_logs) < 6:
                _hp_entry = _hp_map.get(req.propType)
                if _hp_entry:
                    _hp_field, _hp_share = _hp_entry
                    _hp_vals = [
                        s.get(_hp_field) for s in opponent_fixture_stats
                        if s.get(_hp_field) is not None
                    ]
                    if len(_hp_vals) >= 3:
                        _bayes_hyperprior = (sum(_hp_vals) / len(_hp_vals)) * _hp_share

            # ── Expected minutes for this match ─────────────────────────────
            # Use the MEDIAN of the player's recent minutes to estimate playing
            # time. Median is more robust than mean — one 120-min ET game won't
            # inflate the expectation. Clamp to [30, 90].
            _all_mins = sorted([
                g.get("minutes", 90) for g in player_game_logs
                if (g.get("minutes") or 0) > 0
            ])
            if _all_mins:
                _mid = len(_all_mins) // 2
                _exp_mins = (_all_mins[_mid] if len(_all_mins) % 2 == 1
                             else (_all_mins[_mid - 1] + _all_mins[_mid]) / 2)
                _exp_mins = max(30.0, min(90.0, _exp_mins))
            else:
                _exp_mins = 90.0

            _sfm = {
                "goals": "goals_total", "assists": "goals_assists",
                "shots_assisted": "passes_key",
                "pass_attempts": "passes_total", "passes_attempted": "passes_total",
                "shots": "shots_total",
                "shots_on_target": "shots_on", "tackles": "tackles_total",
                "key_passes": "passes_key", "saves": "goals_saves",
                "goalie_saves": "goals_saves",
                "interceptions": "tackles_interceptions", "blocks": "tackles_blocks",
                "dribbles": "dribbles_attempts", "dribbles_success": "dribbles_success",
                "fouls_drawn": "fouls_drawn", "fouls_committed": "fouls_committed",
                "crosses": "passes_crosses", "clearances": "tackles_clearances",
                "duels_won": "duels_won", "yellow_cards": "cards_yellow",
            }
            # VENUE-SPLIT PRIOR for possession-sensitive props
            # Pass attempts/passes vary by 10-15 for GKs and 5-10 for outfield players
            # between home and away games. Using combined logs biases the prior toward
            # whichever venue had more recent games and systematically over/under-projects.
            # Fix: use only venue-matching logs as the primary sample when ≥5 are available.
            # Saves also differ by venue (away GKs face more shots) so apply the same logic.
            # Sort game logs newest-first so the Bayesian engine's momentum layer
            # (recent_5 = all_vals[:5]) correctly captures the most recent games.
            # The API returns fixtures in ascending date order; without this sort the
            # engine would apply the highest decay weight to the OLDEST game — completely
            # reversing the momentum signal (e.g. Cáceres showed COLD -11.5 momentum
            # when his true recent form was HOT +8.8, causing a wrong UNDER call).
            player_game_logs = sorted(
                player_game_logs,
                key=lambda g: g.get("date", ""),
                reverse=True,
            )

            # ── ROTATION RISK — minutes trend detection ───────────────────────
            # Detects whether a player is being rotated out (declining minutes)
            # or returning to full duty (increasing minutes) by comparing their
            # average minutes in the last 3 games vs games 4-8.
            #
            # Why median alone misses this: a player who played 90, 90, 90, 65,
            # 60, 58 has a median of ~77, completely hiding the clear trend.
            # The trend layer adjusts _exp_mins proportionally, capped at ±15%.
            _rotation_risk   = "stable"
            _rotation_adj_pct = 0.0
            try:
                _ordered_mins = [
                    float(g.get("minutes") or 0)
                    for g in player_game_logs
                    if (g.get("minutes") or 0) >= 20
                ]
                if len(_ordered_mins) >= 5:
                    _recent_3   = _ordered_mins[:3]
                    _prior_pool = _ordered_mins[3:min(8, len(_ordered_mins))]
                    _recent_avg = sum(_recent_3) / len(_recent_3)
                    _prior_avg  = sum(_prior_pool) / len(_prior_pool)
                    _delta      = _recent_avg - _prior_avg
                    # Require a meaningful absolute shift (≥8 min) to avoid
                    # noise from minor fixture-length variance (e.g. 88 vs 90).
                    if _prior_avg > 0 and abs(_delta) >= 8:
                        # Scale proportional to the magnitude of the drop/rise,
                        # but cap at ±15% so one anomalous sample can't swing
                        # the projection by an absurd amount.
                        _raw_adj = (_delta / _prior_avg)
                        if _delta < 0:
                            _rotation_risk    = "declining"
                            _rotation_adj_pct = max(-0.15, _raw_adj * 0.6)
                        else:
                            _rotation_risk    = "returning"
                            _rotation_adj_pct = min(0.10, _raw_adj * 0.4)
                        print(
                            f"[ROTATION] {req.playerName}: recent3={_recent_avg:.1f}min "
                            f"prior={_prior_avg:.1f}min delta={_delta:+.1f} "
                            f"→ {_rotation_risk} adj={_rotation_adj_pct:+.1%}"
                        )
            except Exception as _rot_err:
                print(f"[ROTATION] detection error: {_rot_err}")

            # Apply rotation multiplier to the median-based expected minutes
            if _rotation_adj_pct != 0.0:
                _exp_mins = max(30.0, min(90.0, _exp_mins * (1.0 + _rotation_adj_pct)))
                print(f"[ROTATION] adjusted _exp_mins → {_exp_mins:.1f}min")

            _VENUE_SPLIT_PROPS = {"pass_attempts", "passes", "saves", "goalie_saves"}
            _bayes_logs = player_game_logs
            if league_id in INTERNATIONAL_LEAGUES:
                # International tournament (WC, Euros, Copa America, qualifiers, etc.):
                # player_game_logs are CLUB matches, unrelated to whether the player's
                # NATIONAL team is the effective home/away side in this fixture — pool
                # the full club log set as the prior instead of splitting by club venue.
                print(f"[INTL PRIOR] Skipping club venue split — using all {len(player_game_logs)} club logs")
            elif req.propType in _VENUE_SPLIT_PROPS and player_venue:
                _venue_logs = [g for g in player_game_logs if g.get("venue") == player_venue]
                # GK saves are HIGHLY venue-dependent (away GKs face far more shots
                # than home GKs — e.g. Oblak home avg 2.3 vs away avg 5.8). Using
                # combined logs when away samples exist biases the prior toward home
                # game values and systematically under-projects away GK saves.
                # Lower the threshold to 3 for GK saves so 4 away samples activate
                # the venue split instead of falling back to the combined pool.
                _is_gk_saves = (
                    req.propType in {"saves", "goalie_saves"}
                    and _bayes_position.upper() in {"GK", "GOALKEEPER"}
                )
                _venue_min = 3 if _is_gk_saves else 5
                if len(_venue_logs) >= _venue_min:
                    _bayes_logs = _venue_logs
                    print(
                        f"[VENUE PRIOR] {req.playerName}/{req.propType}: "
                        f"using {len(_venue_logs)} {player_venue} logs "
                        f"(dropped {len(player_game_logs) - len(_venue_logs)} opposite-venue logs, "
                        f"threshold={_venue_min})"
                    )
                else:
                    print(
                        f"[VENUE PRIOR] {req.playerName}/{req.propType}: "
                        f"only {len(_venue_logs)} {player_venue} logs — keeping combined {len(player_game_logs)}"
                    )

            # ── MANAGER CHANGE LOG SPLIT ──────────────────────────────────────────
            # When a recent coaching change is detected, pre-change game logs reflect
            # a completely different tactical system. Split at the change date and
            # use ONLY post-change logs as the Bayesian prior so the model prices
            # the new system — not a blended history from two different managers.
            #
            # Threshold: ≥ 3 post-change logs → use them exclusively.
            # < 3 post-change logs → keep combined (flag thin sample for AI + UI).
            _manager_split_info = {}
            if _manager_ctx.get("isRecent") and _manager_ctx.get("coachStartDate"):
                try:
                    from manager_tracker import detect_log_split as _dls
                    _post_logs, _pre_logs, _post_n, _pre_n = _dls(
                        _bayes_logs, _manager_ctx["coachStartDate"]
                    )
                    _sfm_field = _sfm.get(req.propType, "passes_total")
                    _pre_vals_ms  = [g.get(_sfm_field) for g in _pre_logs  if g.get(_sfm_field) is not None]
                    _post_vals_ms = [g.get(_sfm_field) for g in _post_logs if g.get(_sfm_field) is not None]
                    _pre_avg_ms   = round(sum(_pre_vals_ms)  / len(_pre_vals_ms),  1) if _pre_vals_ms  else None
                    _post_avg_ms  = round(sum(_post_vals_ms) / len(_post_vals_ms), 1) if _post_vals_ms else None
                    _manager_split_info = {
                        "postCount": _post_n, "preCount": _pre_n,
                        "preAvg":    _pre_avg_ms, "postAvg": _post_avg_ms,
                        "thinSample": _post_n < 5,
                    }
                    if _post_n >= 3:
                        _bayes_logs = _post_logs
                        print(
                            f"[MANAGER SPLIT] {req.playerName}: using {_post_n} post-"
                            f"{_manager_ctx.get('coachName','new manager')!r} logs "
                            f"(dropped {_pre_n} pre-change) | avg "
                            f"{_pre_avg_ms} → {_post_avg_ms} ({req.propType})"
                        )
                    else:
                        print(
                            f"[MANAGER SPLIT] {req.playerName}: only {_post_n} post-change "
                            f"logs — keeping combined {len(_bayes_logs)} (THIN SAMPLE)"
                        )
                except Exception as _msp_err:
                    print(f"[MANAGER SPLIT] error: {_msp_err}")

            # ── SAMPLE-QUALITY FILTER (luck strip) ───────────────────────
            # Drop garbage-time cameos and severe blowouts when we have
            # abundance — these samples are distorted by game state, not
            # representative of the player's normal output. Conservative:
            # never reduces sample size below 6.
            #
            # Gated behind env flag LUCK_STRIP_ENABLED=1 because we don't yet
            # have an empirical backtest proving it improves hit rate on this
            # specific dataset. When enabled, every filter event is logged so
            # the impact can be measured against settled outcomes over time.
            if os.environ.get("LUCK_STRIP_ENABLED") == "1":
                try:
                    from sample_quality import filter_low_quality_samples
                    _pre_n = len(_bayes_logs)
                    _bayes_logs, _drop_reasons = filter_low_quality_samples(_bayes_logs)
                    if _drop_reasons:
                        print(
                            f"[LUCK STRIP] {req.playerName}/{req.propType}: "
                            f"dropped {len(_drop_reasons)}/{_pre_n} samples "
                            f"({'; '.join(_drop_reasons[:3])}{'...' if len(_drop_reasons) > 3 else ''})"
                        )
                except Exception as _e:
                    print(f"[LUCK STRIP] skipped due to error: {_e}")

            # ── QUALITY PRIOR FILTER — exclude sub-60-min games from Bayesian prior ──
            # Cameos, cup rotations, and partial appearances produce stat lines that
            # are NOT representative of a player's full-game output. A player averaging
            # 36.7 passes in full games but only 31.1 across all games (including 19-min
            # substitute appearances) must have their prior anchored to the 36.7, not 31.1.
            # Only filters when enough full-game samples exist to maintain Bayesian stability.
            _MIN_QUALITY_BAYES = 6
            _quality_bayes_pool = [g for g in _bayes_logs if (g.get("minutes", 0) or 0) >= 60]
            if len(_quality_bayes_pool) >= _MIN_QUALITY_BAYES and len(_quality_bayes_pool) < len(_bayes_logs):
                _quality_prior_dropped = len(_bayes_logs) - len(_quality_bayes_pool)
                _bayes_logs = _quality_bayes_pool
                _quality_prior_applied = True
                print(
                    f"[QUALITY PRIOR] {req.playerName}/{req.propType}: "
                    f"dropped {_quality_prior_dropped} sub-60-min game{'s' if _quality_prior_dropped != 1 else ''} from prior, "
                    f"using {len(_quality_bayes_pool)} full-game logs"
                )

            # ── OPPONENT TIER AUTO-FILTER ─────────────────────────────────
            # If the current opponent is ELITE/STRONG, the prior should only
            # draw from games where the player faced comparably tough sides.
            # Games vs weak opposition skew the prior optimistically for an
            # ELITE opponent (opponent parks less, presses more, concedes
            # fewer touches). Filter stacks on top of the 60-min filter.
            _cur_opp_rank_for_tier = (standing_data or {}).get("oppRank")
            if _cur_opp_rank_for_tier is not None:
                if _cur_opp_rank_for_tier <= 15:
                    _keep_tiers = {"ELITE", "STRONG"}          # facing top-15: only top-15 history
                elif _cur_opp_rank_for_tier <= 30:
                    _keep_tiers = {"ELITE", "STRONG", "MID"}   # facing mid: exclude weak history
                else:
                    _keep_tiers = None                          # facing weak: no tier filter needed
                if _keep_tiers:
                    # Keep games vs matching tiers; keep unknowns (oppTier=None) conservatively
                    _tier_pool = [
                        g for g in _bayes_logs
                        if g.get("oppTier") in _keep_tiers or g.get("oppTier") is None
                    ]
                    if len(_tier_pool) >= _MIN_QUALITY_BAYES and len(_tier_pool) < len(_bayes_logs):
                        _opp_tier_filter_dropped = len(_bayes_logs) - len(_tier_pool)
                        _opp_tier_filter_kept_tiers = sorted(
                            _keep_tiers, key=lambda t: {"ELITE": 0, "STRONG": 1, "MID": 2, "WEAK": 3}.get(t, 4)
                        )
                        _bayes_logs = _tier_pool
                        _opp_tier_filter_applied = True
                        print(
                            f"[OPP TIER FILTER] {req.playerName}/{req.propType}: "
                            f"opp_rank={_cur_opp_rank_for_tier}, kept={_opp_tier_filter_kept_tiers}, "
                            f"dropped {_opp_tier_filter_dropped} games, using {len(_tier_pool)} remaining"
                        )

            # ── LEAGUE-EMPIRICAL CALIBRATION lookup ──────────────────────
            # Returns a small, well-shrunken multiplicative nudge on the
            # posterior, derived from settled-pick history of this exact
            # (league, position, prop, side) bucket.
            _league_calib = None
            try:
                from league_priors import lookup as _league_lookup, ensure_loaded as _ensure_lp
                # Make sure the cache is warm (no-op if already loaded recently)
                await _ensure_lp(db)
                # Pass BOTH sides of the bucket — over/under are independently
                # estimated populations, so we let the engine pick the bucket
                # that matches the side we end up recommending.
                _league_calib = {
                    "over":  _league_lookup(
                        league_id=req.leagueId or league_id,
                        position=_bayes_position,
                        prop_type=req.propType,
                        recommendation="over",
                        posterior_mean=req.line,
                    ),
                    "under": _league_lookup(
                        league_id=req.leagueId or league_id,
                        position=_bayes_position,
                        prop_type=req.propType,
                        recommendation="under",
                        posterior_mean=req.line,
                    ),
                }
            except Exception as _lc_err:
                print(f"[LEAGUE CALIB] lookup failed: {_lc_err}")

            # ── GAME-SCRIPT extraction from Vegas odds (already fetched) ─
            # We derive expected_total_goals + expected_goal_diff so the engine
            # can apply chase-mode / nailbiter nudges (cheat-sheet patterns).
            # ALSO produce a scenario probability vector (P_draw, P_low_scoring,
            # ...) used by the new scenario_priors layer.
            _game_script = None
            _scenario_probs = None
            try:
                from game_script_engine import compute_scenario_probs, expected_total_from_game_tempo
                _bo = (match_odds or {}).get("bookmakerOdds") if match_odds else None
                _gt_local = locals().get("game_tempo") or {}
                _expected_total = expected_total_from_game_tempo(_gt_local) or 2.6
                _scenario_probs = compute_scenario_probs(_bo, _expected_total)
                if _bo and _scenario_probs.get("available"):
                    _expected_diff = (_scenario_probs["impliedHome"]
                                      - _scenario_probs["impliedAway"]) * 2.5
                    # NEUTRAL-VENUE FIX: `player_venue` is forced to "neutral" for
                    # non-host World Cup / tournament fixtures (see venueOverride
                    # logic client-side), which previously caused every venue-gated
                    # game-script boost below (CB managing-lead, CDM chase-mode, GK
                    # high-scoring) to silently never fire — even for a huge favourite
                    # like Argentina vs Cape Verde. `playerIsHome` reflects the
                    # fixture's true home/away slot (from the odds/fixture data)
                    # regardless of the neutral-venue display label, so the engine can
                    # still tell which side is favoured. Falls back to the real venue
                    # when not neutral, and to None (skip) when truly unknown.
                    _pih_for_script = (match_odds or {}).get("playerIsHome")
                    if _pih_for_script is None and not _is_neutral:
                        _pih_for_script = (player_venue == "home")
                    _game_script = {
                        "expected_total_goals": _scenario_probs["expectedTotal"],
                        "expected_goal_diff":   round(_expected_diff, 2),
                        "implied_home":         _scenario_probs["impliedHome"],
                        "implied_away":         _scenario_probs["impliedAway"],
                        "player_is_home":       _pih_for_script,
                    }
            except Exception as _gs_err:
                print(f"[GAME SCRIPT] extraction failed: {_gs_err}")

            # ── CONDITIONAL POSSESSION ADJUSTMENT ────────────────────────────
            # Adjusts expectedPoss for game-state-conditional team style before
            # the Bayesian engine runs. France cedes possession when leading 1-0;
            # Morocco's CDM pass volume follows that shift upward. Spain doesn't
            # cede — their CDM numbers hold regardless of score.
            # Controlled by COND_POSS_MODE env var: off | shadow | live (default: live)
            _cond_poss_result = None
            _cond_poss_mode = os.environ.get("COND_POSS_MODE", "live").lower()
            try:
                from game_state_possession import (
                    PASS_ADJACENT_PROPS as _PASS_ADJ_PROPS,
                    compute_conditional_possession as _compute_cond_poss,
                )
                _cond_poss_eligible = (
                    _cond_poss_mode != "off"
                    and req.sport == "soccer"
                    and req.propType in _PASS_ADJ_PROPS
                    and match_dominance.get("seasonAvgIsReal", False)
                )
                if _cond_poss_eligible:
                    # Determine player_is_home: prefer game_script (fixture-derived),
                    # fall back to req.venue
                    _pih_cp = (player_venue == "home")
                    if _game_script is not None:
                        _gs_pih = _game_script.get("player_is_home")
                        if _gs_pih is not None:
                            _pih_cp = _gs_pih

                    # Derive implied win/loss probs: game_script > req.odds > balanced default
                    if _game_script is not None and _game_script.get("implied_home") is not None:
                        # p_trail = probability player's team loses this match
                        _cp_p_trail = (
                            float(_game_script["implied_away"]) if _pih_cp
                            else float(_game_script["implied_home"])
                        )
                        _cp_p_lead = (
                            float(_game_script["implied_home"]) if _pih_cp
                            else float(_game_script["implied_away"])
                        )
                    elif req.odds:
                        # Convert req.odds American lines → implied probs
                        _ro = req.odds if isinstance(req.odds, dict) else (req.odds.dict() if hasattr(req.odds, "dict") else {})
                        _h_ml = _ro.get("home") or _ro.get("homeOdds") or _ro.get("americanHome")
                        _a_ml = _ro.get("away") or _ro.get("awayOdds") or _ro.get("americanAway")
                        def _ml_to_prob(ml):
                            if ml is None: return 0.5
                            ml = float(ml)
                            return abs(ml) / (abs(ml) + 100) if ml < 0 else 100 / (ml + 100)
                        _h_raw = _ml_to_prob(_h_ml)
                        _a_raw = _ml_to_prob(_a_ml)
                        _tot_raw = _h_raw + _a_raw
                        _h_imp = _h_raw / _tot_raw if _tot_raw > 0 else 0.50
                        _a_imp = _a_raw / _tot_raw if _tot_raw > 0 else 0.50
                        _cp_p_trail = _a_imp if _pih_cp else _h_imp
                        _cp_p_lead  = _h_imp if _pih_cp else _a_imp
                    else:
                        # No odds signal — use balanced defaults (style still fires if opp_cede is strong)
                        _cp_p_trail = 0.33
                        _cp_p_lead  = 0.33
                    _cond_poss_result = await _compute_cond_poss(
                        base_poss=match_dominance["expectedPoss"],
                        p_trail=_cp_p_trail,
                        p_lead=_cp_p_lead,
                        player_team_name=(
                            locals().get("corrected_team_name") or req.teamName or ""
                        ),
                        opp_team_name=req.opponentName or "",
                        db=db,
                        team_fixture_stats=team_fixture_stats,
                        opp_fixture_stats=opponent_fixture_stats,
                    )
                    if _cond_poss_result and _cond_poss_result.get("adjusted_poss"):
                        if _cond_poss_mode == "live":
                            _cp_old = match_dominance["expectedPoss"]
                            match_dominance["expectedPoss"] = _cond_poss_result["adjusted_poss"]
                            match_dominance["notes"].append(
                                f"Conditional poss: {_cp_old:.0f}%→{_cond_poss_result['adjusted_poss']:.1f}% "
                                f"(Δ{_cond_poss_result['delta_pp']:+.1f}pp, "
                                f"p_trail={_cp_p_trail:.2f}, "
                                f"opp_cede={_cond_poss_result['opp_style'].get('possession_cede_when_leading', 0):.2f})"
                            )
                        else:
                            print(
                                f"[COND POSS SHADOW] {req.playerName}: "
                                f"would adjust {match_dominance['expectedPoss']:.0f}% → "
                                f"{_cond_poss_result['adjusted_poss']:.1f}%"
                            )
            except Exception as _cp_err:
                print(f"[COND POSS] Error: {_cp_err}")

            # ── SCENARIO PRIORS lookup (cheat-sheet conditional layer) ────
            # Mode controlled by env var SCENARIO_PRIORS_MODE: off|shadow|live
            # Default = shadow (compute & log, do NOT change projection).
            _scenario_priors_result = None
            _scen_mode = os.environ.get("SCENARIO_PRIORS_MODE", "live").lower()
            if _scen_mode not in {"off", "shadow", "live"}:
                _scen_mode = "shadow"
            if _scen_mode != "off" and _scenario_probs and _scenario_probs.get("available"):
                try:
                    from scenario_priors import (lookup_weighted as _scen_lookup,
                                                 ensure_loaded as _ensure_scen)
                    await _ensure_scen(db)
                    # Look up BOTH sides; the engine has already chosen which
                    # to apply by the time scenario_priors runs in shadow/live.
                    # We emit both so the inspector and downstream consumers
                    # can see what each side would have done.
                    _scen_over = _scen_lookup(_scenario_probs, _bayes_position,
                                              req.propType, "over",
                                              posterior_mean=req.line)
                    _scen_under = _scen_lookup(_scenario_probs, _bayes_position,
                                               req.propType, "under",
                                               posterior_mean=req.line)
                    # Pick the bucket that matches the side we'll likely
                    # recommend (compare line vs. baseline). The engine itself
                    # will not re-choose — it consumes whatever we hand it.
                    _scenario_priors_result = (_scen_over if _scen_over.get("found")
                                               else _scen_under)
                    if _scenario_priors_result and _scenario_priors_result.get("found"):
                        _scenario_priors_result["sideOver"]  = _scen_over
                        _scenario_priors_result["sideUnder"] = _scen_under
                except Exception as _sp_err:
                    print(f"[SCENARIO PRIORS] lookup failed: {_sp_err}")

            # ── ODDS-TIER PRIORS lookup ("alive" self-learning layer) ──────
            # Mode controlled by env var ODDS_TIER_PRIORS_MODE: off|shadow|live
            # Default = shadow (compute & log, do NOT change projection yet).
            _odds_tier_priors_result = None
            _ot_mode = os.environ.get("ODDS_TIER_PRIORS_MODE", "shadow").lower()
            if _ot_mode not in {"off", "shadow", "live"}:
                _ot_mode = "shadow"
            _odds_tier = "unknown"
            try:
                if _ot_mode != "off":
                    from odds_tier_priors import (lookup_single as _ot_lookup,
                                                 odds_tier_from_moneyline as _ot_from_ml,
                                                 odds_tier_from_possession as _ot_from_poss,
                                                 ensure_loaded as _ensure_ot)
                    await _ensure_ot(db)
                    # Resolve odds tier deterministically: moneyline first, then
                    # projected possession (already computed by match_dominance).
                    if match_odds and match_odds.get("americanOdds"):
                        _odds_tier = _ot_from_ml(match_odds["americanOdds"], player_venue)
                    elif not match_dominance.get("hasRealPossData"):
                        # No moneyline AND no real possession/standings/odds signal
                        # (compute_match_dominance left pure 50/50 defaults with no
                        # notes) — e.g. an international friendly vs a minnow with
                        # sparse pre-match data. Do NOT let a fake "close" tier feed
                        # the odds-tier-priors nudge; "unknown" finds no bucket and
                        # lookup_single() correctly applies zero adjustment instead.
                        _odds_tier = "unknown"
                    else:
                        # match_dominance["expectedPoss"]/["oppExpectedPoss"] are already
                        # remapped to the player's own team vs opponent (see remap logic
                        # above) — NOT a {"home":.., "away":..} dict. Pass them straight
                        # through as (team_poss, opp_poss) using the player's own venue.
                        _team_poss = match_dominance.get("expectedPoss")
                        _opp_poss = match_dominance.get("oppExpectedPoss")
                        if player_venue == "home":
                            _odds_tier = _ot_from_poss(_team_poss, _opp_poss, "home")
                        else:
                            _odds_tier = _ot_from_poss(_opp_poss, _team_poss, "away")
                    print(f"[ODDS TIER] {req.playerName} ({player_venue}): {_odds_tier} "
                          f"(from={'moneyline' if (match_odds and match_odds.get('americanOdds')) else ('projPoss' if match_dominance.get('hasRealPossData') else 'no-data')})")
                    # Look up BOTH sides; engine applies the one matching recommendation.
                    # Pass player_venue so the lookup can try the fine-grained
                    # (tier x pos x prop x side x venue) bucket first and fall
                    # back to the venue-agnostic bucket automatically.
                    _ot_over = _ot_lookup(_odds_tier, _bayes_position,
                                         req.propType, "over",
                                         posterior_mean=req.line,
                                         venue=player_venue)
                    _ot_under = _ot_lookup(_odds_tier, _bayes_position,
                                          req.propType, "under",
                                          posterior_mean=req.line,
                                          venue=player_venue)
                    _odds_tier_priors_result = (_ot_over if _ot_over.get("found")
                                                 else _ot_under)
                    if _odds_tier_priors_result and _odds_tier_priors_result.get("found"):
                        _odds_tier_priors_result["sideOver"]  = _ot_over
                        _odds_tier_priors_result["sideUnder"] = _ot_under
                        _odds_tier_priors_result["resolvedTier"] = _odds_tier
            except Exception as _ot_err:
                print(f"[ODDS-TIER PRIORS] lookup failed: {_ot_err}")

            # ── Ultra v4: compute 4 new Bayesian inputs ──────────────────────
            # 1. REST DAYS — days since player's team last played
            _rest_days_v4: int | None = None
            try:
                _match_date_str_v4 = (match_odds or {}).get("matchDate", "") or ""
                if _match_date_str_v4 and player_game_logs:
                    from datetime import date as _dt_v4
                    _md_obj = _dt_v4.fromisoformat(_match_date_str_v4[:10])
                    _last_dates = [
                        g.get("date", "")[:10] for g in player_game_logs
                        if g.get("date", "")[:10]
                    ]
                    if _last_dates:
                        _ld_obj = _dt_v4.fromisoformat(max(_last_dates))
                        _rest_days_v4 = max(0, (_md_obj - _ld_obj).days)
                        print(f"[REST DAYS] {req.playerName}: last={max(_last_dates)} "
                              f"match={_match_date_str_v4[:10]} → {_rest_days_v4}d rest")
            except Exception as _rd_err:
                print(f"[REST DAYS] err: {_rd_err}")

            # 2. OPPONENT CLEAN SHEET RATE — fraction of recent games opp kept CS
            _opp_cs_rate_v4: float | None = None
            try:
                _cs_vals = [
                    s.get("goals_conceded")
                    for s in (opponent_fixture_stats or [])
                    if s.get("goals_conceded") is not None
                ]
                if len(_cs_vals) >= 3:
                    _opp_cs_rate_v4 = round(
                        sum(1 for v in _cs_vals if v == 0) / len(_cs_vals), 3
                    )
                    print(f"[CS RATE] {req.opponentName}: "
                          f"cs={sum(1 for v in _cs_vals if v==0)}/{len(_cs_vals)} "
                          f"= {_opp_cs_rate_v4:.0%}")
            except Exception as _cs_err:
                print(f"[CS RATE] err: {_cs_err}")

            # 2b. TOURNAMENT GAME INDEX — derive from round string for compounding fatigue
            _tourn_game_idx = None
            _raw_round = (match_odds or {}).get("matchRound", "")
            if _raw_round:
                _round_digits = re.findall(r'\d+', _raw_round)
                if _round_digits:
                    _tourn_game_idx = int(_round_digits[0])
                elif "group" in _raw_round.lower():
                    _tourn_game_idx = 1
                elif any(k in _raw_round.lower() for k in ("round of", "16", "eighth")):
                    _tourn_game_idx = 4
                elif any(k in _raw_round.lower() for k in ("quarter", "qf")):
                    _tourn_game_idx = 5
                elif any(k in _raw_round.lower() for k in ("semi", "sf")):
                    _tourn_game_idx = 6
                elif any(k in _raw_round.lower() for k in ("final", "3rd", "third")):
                    _tourn_game_idx = 7

            # 3. ALTITUDE — high-altitude league mapping (away teams only)
            _HIGH_ALTITUDE_LEAGUES_V4 = {
                270: 3640,   # Bolivia (La Paz, Sucre) — Liga Profesional
                285: 2850,   # Ecuador (Quito) — Liga Pro
                239: 2640,   # Colombia (Bogotá) — Primera A
                262: 2240,   # Mexico (Mexico City) — Liga MX (moderate)
                300: 2800,   # Peru (Lima is sea-level but Cusco/Arequipa) — rough avg
            }
            _altitude_m_v4: int | None = None
            _lid_v4 = req.leagueId or locals().get("league_id")
            if _lid_v4 and _lid_v4 in _HIGH_ALTITUDE_LEAGUES_V4:
                # Only pass altitude for AWAY team (home teams are acclimatised)
                if player_venue == "away":
                    _altitude_m_v4 = _HIGH_ALTITUDE_LEAGUES_V4[_lid_v4]
                    print(f"[ALTITUDE] {req.opponentName} league={_lid_v4} "
                          f"altitude={_altitude_m_v4}m (away penalty active)")

            # 4. OPPONENT FOUL RATE — avg fouls/game from opponent's recent fixtures
            _opp_foul_rate_v4: float | None = None
            try:
                _foul_vals = [
                    s.get("fouls_committed_agg")
                    for s in (opponent_fixture_stats or [])
                    if s.get("fouls_committed_agg") is not None
                ]
                if len(_foul_vals) >= 2:
                    _opp_foul_rate_v4 = round(sum(_foul_vals) / len(_foul_vals), 1)
                    print(f"[FOUL RATE] {req.opponentName}: "
                          f"avg={_opp_foul_rate_v4:.1f} fouls/game "
                          f"(n={len(_foul_vals)})")
            except Exception as _fr_err:
                print(f"[FOUL RATE] err: {_fr_err}")

            # 5. DISMISSAL / RED-CARD RISK — combined card volatility for both teams.
            # Not a stat prediction; a volatility flag so users know a 10-man scenario
            # is a live possibility that can swing the whole match (and the prop).
            _risk_signals: dict = {"level": "normal", "note": None, "teamCardsAvg": None, "oppCardsAvg": None}
            try:
                def _avg_cards(fixture_stats):
                    yv = [s.get("cards_yellow_agg") for s in (fixture_stats or []) if s.get("cards_yellow_agg") is not None]
                    rv = [s.get("cards_red_agg") for s in (fixture_stats or []) if s.get("cards_red_agg") is not None]
                    if len(yv) < 2:
                        return None, None
                    y_avg = round(sum(yv) / len(yv), 2)
                    r_avg = round(sum(rv) / len(rv), 2) if rv else 0.0
                    return y_avg, r_avg

                _team_y, _team_r = _avg_cards(team_fixture_stats)
                _opp_y, _opp_r = _avg_cards(opponent_fixture_stats)
                _risk_signals["teamCardsAvg"] = _team_y
                _risk_signals["oppCardsAvg"] = _opp_y
                _combined_y = (_team_y or 0) + (_opp_y or 0)
                _combined_r = (_team_r or 0) + (_opp_r or 0)
                if _team_y is not None and _opp_y is not None:
                    if _combined_r >= 0.25 or _combined_y >= 5.0:
                        _risk_signals["level"] = "elevated"
                        _risk_signals["note"] = (
                            f"Elevated dismissal risk — combined card rate {_combined_y:.1f} yellow"
                            f"{f' / {_combined_r:.2f} red' if _combined_r else ''} per game across both sides. "
                            "A red card can flip possession/tempo and swing this prop either way."
                        )
                        print(f"[RISK] elevated dismissal risk: team={_team_y}/{_team_r} opp={_opp_y}/{_opp_r}")
                    elif _combined_y >= 3.8:
                        _risk_signals["level"] = "moderate"
                        _risk_signals["note"] = f"Moderate card volatility ({_combined_y:.1f} combined yellows/game)."
            except Exception as _risk_err:
                print(f"[RISK] err: {_risk_err}")
            # ─────────────────────────────────────────────────────────────────

            early_bayes = compute_bayesian_projection(
                game_logs=_bayes_logs,
                prop_type=req.propType,
                line=req.line,
                venue=player_venue,
                stat_field=_sfm.get(req.propType, "passes_total"),
                opponent_fixture_stats=opponent_fixture_stats,
                match_dominance=match_dominance,
                position=_bayes_position,
                hyperprior_mean=_bayes_hyperprior,
                expected_minutes=_exp_mins,
                ai_press_intensity=ai_press_intensity,
                league_calibration=_league_calib,
                game_script=_game_script,
                scenario_priors_result=_scenario_priors_result,
                scenario_priors_mode=_scen_mode,
                odds_tier_priors_result=_odds_tier_priors_result,
                odds_tier_priors_mode=_ot_mode,
                role=locals().get("player_role", ""),
                match_stakes={
                    **(game_situation.get("matchStakes") or {}),
                    # Inject live expectedPoss so Bayesian can gate the
                    # direct-play debuff when possession shows dominance
                    "teamExpectedPoss": match_dominance.get("expectedPoss", 50.0),
                    "h2hPossAvg": match_dominance.get("h2hPossAvg"),
                    # World Cup: every match is max-stakes elimination pressure
                    "isWorldCup": _is_wc,
                },
                league_id=req.leagueId,
                # ── Ultra v4 new layers ────────────────────────────────────
                rest_days=_rest_days_v4,
                opponent_clean_sheet_rate=_opp_cs_rate_v4,
                altitude_m=_altitude_m_v4,
                opponent_foul_rate=_opp_foul_rate_v4,
                tournament_game_index=_tourn_game_idx,
                player_stats=player_stats,
            )
            _eb_samples = early_bayes.get("priorSamples", 0) if early_bayes else 0
            print(f"[BAYESIAN] {req.playerName}/{req.propType}: samples={_eb_samples}, logs={len(_bayes_logs)} (venue={player_venue})")

            # ── POSITIONAL ROLE BASELINE ──────────────────────────────────────
            # Reality-check: does the projection make sense for this position
            # in this possession context? A CDM who played for a high-possession
            # club (80-pass history) but is now at a low-possession club should
            # NOT be projected at 80 passes. The baseline knows what CDMs at
            # low-possession teams actually produce (median ~50) and squeezes
            # the projection back toward the realistic ceiling when sample count
            # is low enough that the player's personal history is still "tainted"
            # by a very different team context.
            # No squeeze at 8+ game logs — by then the player's own data is law.
            try:
                from positional_baseline import get_positional_baseline, apply_positional_squeeze
                _pos_for_baseline = (
                    _bayes_position
                    or locals().get("display_position", "")
                    or ""
                )
                _poss_for_baseline = match_dominance.get("expectedPoss", 50.0) if match_dominance else 50.0
                _team_avg_passes   = match_dominance.get("teamAvgPasses") if match_dominance else None
                _press_label       = (ai_press_intensity or {}).get("label") if ai_press_intensity else None
                _pos_baseline = get_positional_baseline(
                    position=_pos_for_baseline,
                    expected_poss=_poss_for_baseline,
                    prop_type=req.propType,
                    role=_bayes_role,
                    team_avg_passes=_team_avg_passes,
                    press_intensity_label=_press_label,
                )
                if early_bayes and _pos_baseline:
                    _raw_pm = early_bayes.get("posteriorMean", req.line)
                    _adj_pm, _pos_note = apply_positional_squeeze(
                        posterior_mean=_raw_pm,
                        baseline=_pos_baseline,
                        n_samples=early_bayes.get("priorSamples", len(_bayes_logs)),
                    )
                    if _pos_note:
                        print(_pos_note)
                        early_bayes["posteriorMean"] = _adj_pm
                        # Recalculate recommendation direction from adjusted projection
                        early_bayes["recommendation"] = "over" if _adj_pm > req.line else "under"
                        _pos_baseline["squeezedFrom"] = _raw_pm
                        _pos_baseline["squeezedTo"]   = _adj_pm
                        _pos_baseline["note"] = _pos_note
                        # ── Recompute P(over)/P(under) from the adjusted mean ──────────
                        # When squeeze fires from n=0 centering, pOver/pUnder are still
                        # 50/50 from _empty_metrics.  Recompute from a normal distribution
                        # centered at _adj_pm with σ = IQR/1.35 (empirical normal approx).
                        try:
                            import math as _math
                            _bl_iqr = (_pos_baseline.get("p75", req.line) -
                                       _pos_baseline.get("p25", req.line))
                            _bl_std = _bl_iqr / 1.35 if _bl_iqr > 0 else max(req.line * 0.25, 1.0)
                            _z      = (_adj_pm - req.line) / max(_bl_std, 0.01)
                            _po_raw = 50.0 + 50.0 * _math.erf(_z / _math.sqrt(2))
                            _po     = round(max(1.0, min(99.0, _po_raw)), 1)
                            early_bayes["pOver"]  = _po
                            early_bayes["pUnder"] = round(100.0 - _po, 1)
                            print(f"[POS BASELINE] pOver recalc: adj_pm={_adj_pm:.2f} "
                                  f"line={req.line} std={_bl_std:.2f} → P(over)={_po:.1f}%")
                        except Exception as _po_err:
                            print(f"[POS BASELINE] pOver recalc failed (non-fatal): {_po_err}")
                    else:
                        _pos_baseline["note"] = "within realistic range — no adjustment"
                    early_bayes["positionalBaseline"] = _pos_baseline
            except Exception as _pb_err:
                print(f"[POS BASELINE] error (non-fatal): {_pb_err}")
            # ─────────────────────────────────────────────────────────────────

            # ── LOW-SAMPLE MID/CAM UNDER GUARD ───────────────────────────────
            # Evidence: CM/DLP UNDER picks have 0% win rate (4 picks, avg_err=+27.5).
            # CM/Mezzala UNDER: 33% win rate. CDM/Ball Winner UNDER: 54% (borderline).
            # When the engine has < 4 game logs AND projects significantly below the
            # line for a midfielder/attacker, the UNDER recommendation is unreliable —
            # the model is mostly anchored to the hyperprior, which is often too low.
            # Guard: cap pUnder at 65 in this scenario so the UI shows "Medium" not "High".
            _guard_positions = {"CM", "CDM", "CAM", "DM", "AM", "MF", "DMF", "OMF"}
            if (early_bayes
                    and req.propType in {"pass_attempts", "passes"}
                    and _bayes_position.upper() in _guard_positions
                    and early_bayes.get("recommendation") == "under"
                    and _eb_samples < 4):
                _proj = early_bayes.get("posteriorMean", req.line)
                _proj_ratio = _proj / req.line if req.line > 0 else 1.0
                if _proj_ratio < 0.88:
                    _old_pu = early_bayes.get("pUnder", 50)
                    if _old_pu > 65:
                        early_bayes["pUnder"] = 65.0
                        early_bayes["pOver"]  = 35.0
                        print(f"[LOW-SAMPLE UNDER GUARD] {req.playerName}/{req.propType}: "
                              f"samples={_eb_samples}, proj/line={_proj_ratio:.2f} "
                              f"pUnder {_old_pu:.1f}→65.0 (low data, mid UNDER unreliable)")

            # ── T003: Redistribution model ───────────────────────────────────
            # When a teammate of the same position is absent, the subject player
            # absorbs a portion of their typical contribution. We detect absences
            # from the situation-engine injury data and apply a per-prop-type
            # multiplier to the Bayesian posteriorMean.
            #
            # Position groups: A/F → attacker, M → midfielder, D → defender.
            # Redistribution only applies when >= 1 same-position teammate absent.
            # Cap: total boost ≤ 25%, never applied to goalkeepers (G).
            _player_team_absences = game_situation.get("injuries", {}).get("playerTeamAbsences", [])
            _redist_multiplier = 1.0

            # Map raw API-Football position codes → canonical group
            def _pos_group(pos_code: str) -> str:
                p = (pos_code or "").upper().strip()
                if p in ("A", "F", "ST", "CF", "LW", "RW", "LF", "RF", "SS"):
                    return "attacker"
                if p in ("M", "AM", "CM", "DM", "CAM", "CDM", "LM", "RM", "MF", "W"):
                    return "midfielder"
                if p in ("D", "CB", "LB", "RB", "LWB", "RWB", "SW", "DF"):
                    return "defender"
                return "other"

            # Determine subject player's position group
            _subject_pos_group = _pos_group(_bayes_position)

            # Redistribution table: (prop_type → boost per absent same-position teammate)
            # Boosts are fractional multipliers above 1.0; typical squad size per position:
            # attacker ~2, midfielder ~4, defender ~4 — so 1 absence = bigger impact for attacker
            _REDIST_TABLE = {
                "attacker": {
                    "goals": 0.12, "shots": 0.12, "shots_on_target": 0.10,
                    "key_passes": 0.07, "dribbles": 0.08, "dribbles_success": 0.07,
                    "assists": 0.06, "fouls_drawn": 0.05,
                },
                "midfielder": {
                    "pass_attempts": 0.08, "key_passes": 0.10, "assists": 0.08,
                    "tackles": 0.06, "interceptions": 0.06, "fouls_committed": 0.05,
                    "dribbles": 0.06, "crosses": 0.07,
                },
                "defender": {
                    "tackles": 0.10, "clearances": 0.12, "interceptions": 0.09,
                    "blocks": 0.08, "fouls_committed": 0.06, "duels_won": 0.07,
                    # Pass redistribution: when a fellow defender is absent, the remaining
                    # defenders take on more build-up passing — especially CBs in possession systems
                    "pass_attempts": 0.07, "passes": 0.07, "key_passes": 0.06, "crosses": 0.04,
                },
            }

            _redist_alerts = []
            if _subject_pos_group in _REDIST_TABLE and _player_team_absences:
                _prop_boosts = _REDIST_TABLE[_subject_pos_group]
                _per_absence_boost = _prop_boosts.get(req.propType, 0.0)
                if _per_absence_boost > 0:
                    _absent_same_pos = [
                        a for a in _player_team_absences
                        if _pos_group(a.get("position", "")) == _subject_pos_group
                    ]
                    if _absent_same_pos:
                        _raw_boost = len(_absent_same_pos) * _per_absence_boost
                        _capped_boost = min(_raw_boost, 0.25)
                        _redist_multiplier = 1.0 + _capped_boost
                        _absent_names = ", ".join(a["name"] for a in _absent_same_pos[:3])
                        _redist_alerts.append(
                            f"Redistribution: {len(_absent_same_pos)} same-position teammate(s) absent "
                            f"({_absent_names}) → +{round(_capped_boost*100)}% {req.propType} boost applied"
                        )
                        print(f"[REDIST] {req.playerName}/{req.propType}: "
                              f"×{_redist_multiplier:.3f} from {len(_absent_same_pos)} absence(s)")

            # Apply redistribution to early_bayes posteriorMean
            if early_bayes and _redist_multiplier != 1.0:
                _orig_pm = early_bayes["posteriorMean"]
                _new_pm  = round(_orig_pm * _redist_multiplier, 1)
                early_bayes["posteriorMean"] = _new_pm
                early_bayes["recommendation"] = "over" if _new_pm > req.line else "under"
                early_bayes["redistribution"] = {
                    "multiplier": round(_redist_multiplier, 3),
                    "originalMean": _orig_pm,
                    "adjustedMean": _new_pm,
                    "absentCount": len([a for a in _player_team_absences
                                        if _pos_group(a.get("position", "")) == _subject_pos_group]),
                }

            # ── Pitch diagram data — grid "row:col" (API-Football) -> normalized x,y ──
            def _grid_to_xy(grid: str, is_home: bool) -> tuple:
                try:
                    row, col = grid.split(":")
                    row, col = int(row), int(col)
                except Exception:
                    return (0.5, 0.5)
                # y: 0 = own goal line, 1 = opponent goal line. Home attacks "up" (y grows),
                # away is mirrored so both teams render facing each other on one pitch.
                y = min(0.92, 0.08 + (row - 1) * 0.20)
                if not is_home:
                    y = 1.0 - y
                # x spread within the row (col starts at 1)
                row_counts = {1: 1, 2: 5, 3: 5, 4: 5, 5: 3}
                n = max(row_counts.get(row, 4), col)
                x = (col) / (n + 1)
                return (round(x, 3), round(y, 3))

            def _build_pitch_team(team_lineup: dict, is_home: bool, target_id: int | None) -> dict:
                players = []
                for p in team_lineup.get("startXI", []):
                    pl = p.get("player", {})
                    x, y = _grid_to_xy(pl.get("grid") or "", is_home)
                    players.append({
                        "id": pl.get("id"),
                        "name": pl.get("name"),
                        "pos": pl.get("pos"),
                        "number": pl.get("number"),
                        "x": x, "y": y,
                        "isTarget": bool(target_id) and pl.get("id") == target_id,
                    })
                return {
                    "formation": team_lineup.get("formation"),
                    "coach": (team_lineup.get("coach") or {}).get("name"),
                    "players": players,
                }

            _pitch_lineup: dict = {
                "status": "unavailable", "formation": None, "players": [],
                "opponentFormation": None, "opponentPlayers": [], "coach": None, "opponentCoach": None,
            }

            # ── T004: Lineup confirmation gate ───────────────────────────────
            # Fetch the confirmed starting XI for the upcoming fixture.
            # If available and the subject player is NOT in the XI → confidence floor.
            # If confirmed starting → positive tactical signal.
            _lineup_alert = None
            _lineup_confidence_floor = None
            _lineup_status = "unknown"  # "starting" | "substitute" | "not_in_squad" | "unknown"
            if _sit_fixture_id and req.playerId:
                try:
                    _lineup_raw = await api_football_request("fixtures/lineups", {"fixture": _sit_fixture_id})
                    _lineup_responses = (_lineup_raw or {}).get("response", [])
                    _player_id_int = int(req.playerId) if str(req.playerId).isdigit() else None

                    if _lineup_responses:
                        # Build pitch data for both teams (confirmed)
                        try:
                            for _tl in _lineup_responses:
                                _tl_id = (_tl.get("team") or {}).get("id")
                                _is_home_tl = (_tl_id == _sit_home_id)
                                _team_pitch = _build_pitch_team(_tl, _is_home_tl, _player_id_int)
                                if _tl_id == actual_team_id or (actual_team_id is None and _tl_id != req.opponentId):
                                    _pitch_lineup["formation"] = _team_pitch["formation"]
                                    _pitch_lineup["players"] = _team_pitch["players"]
                                    _pitch_lineup["coach"] = _team_pitch["coach"]
                                else:
                                    _pitch_lineup["opponentFormation"] = _team_pitch["formation"]
                                    _pitch_lineup["opponentPlayers"] = _team_pitch["players"]
                                    _pitch_lineup["opponentCoach"] = _team_pitch["coach"]
                            if _pitch_lineup["players"] or _pitch_lineup["opponentPlayers"]:
                                _pitch_lineup["status"] = "confirmed"
                        except Exception as _pitch_err:
                            print(f"[PITCH] build error: {_pitch_err}")
                    else:
                        # Not posted yet — build a "predicted" XI from each team's most
                        # recent fixture lineup as a reasonable proxy (last-used shape/personnel).
                        try:
                            async def _last_lineup(team_id):
                                if not team_id:
                                    return None
                                _lf = await api_football_request(
                                    "fixtures", {"team": team_id, "last": 1}
                                )
                                _fx = (_lf or {}).get("response", [])
                                if not _fx:
                                    return None
                                _fid = (_fx[0].get("fixture") or {}).get("id")
                                if not _fid:
                                    return None
                                _lu = await api_football_request("fixtures/lineups", {"fixture": _fid})
                                for _tl in (_lu or {}).get("response", []):
                                    if (_tl.get("team") or {}).get("id") == team_id:
                                        return _tl
                                return None

                            _own_last, _opp_last = await aio.gather(
                                _last_lineup(actual_team_id), _last_lineup(req.opponentId),
                                return_exceptions=True
                            )
                            if _own_last and not isinstance(_own_last, Exception):
                                _tp = _build_pitch_team(_own_last, _sit_is_home, _player_id_int)
                                _pitch_lineup["formation"] = _tp["formation"]
                                _pitch_lineup["players"] = _tp["players"]
                                _pitch_lineup["coach"] = _tp["coach"]
                            if _opp_last and not isinstance(_opp_last, Exception):
                                _tp = _build_pitch_team(_opp_last, not _sit_is_home, None)
                                _pitch_lineup["opponentFormation"] = _tp["formation"]
                                _pitch_lineup["opponentPlayers"] = _tp["players"]
                                _pitch_lineup["opponentCoach"] = _tp["coach"]
                            if _pitch_lineup["players"] or _pitch_lineup["opponentPlayers"]:
                                _pitch_lineup["status"] = "predicted"
                                print(f"[PITCH] predicted XI built from last-match lineups for {req.playerName}'s fixture")
                        except Exception as _pred_pitch_err:
                            print(f"[PITCH] predicted build error: {_pred_pitch_err}")
                    if _lineup_responses and _player_id_int:
                        # Determine which team the subject player belongs to by scanning both
                        for _team_lineup in _lineup_responses:
                            _starters = _team_lineup.get("startXI", [])
                            _subs     = _team_lineup.get("substitutes", [])
                            _starter_ids = {
                                p.get("player", {}).get("id")
                                for p in _starters
                                if p.get("player", {}).get("id") is not None
                            }
                            _sub_ids = {
                                p.get("player", {}).get("id")
                                for p in _subs
                                if p.get("player", {}).get("id") is not None
                            }
                            if _player_id_int in _starter_ids:
                                _lineup_status = "starting"
                                _lineup_alert = "✓ Confirmed in starting XI"
                                print(f"[LINEUP] {req.playerName}: confirmed STARTING in fixture {_sit_fixture_id}")
                                break
                            elif _player_id_int in _sub_ids:
                                _lineup_status = "substitute"
                                _lineup_alert = "⚠ Listed as substitute — reduced involvement expected"
                                _lineup_confidence_floor = 0.45
                                print(f"[LINEUP] {req.playerName}: confirmed SUBSTITUTE in fixture {_sit_fixture_id}")
                                break
                        else:
                            # Lineups posted but player found in neither — possibly not in squad
                            if _lineup_responses:
                                _lineup_status = "not_in_squad"
                                _lineup_alert = "⚠ Player not found in confirmed lineup"
                                _lineup_confidence_floor = 0.45
                                print(f"[LINEUP] {req.playerName}: NOT in lineup for fixture {_sit_fixture_id}")
                except Exception as _lineup_err:
                    print(f"[LINEUP] fetch error for fixture {_sit_fixture_id}: {_lineup_err}")

            # Apply confidence floor — cap pOver / pUnder at 45% if substitute / not in squad
            if early_bayes and _lineup_confidence_floor is not None:
                _dir = early_bayes["recommendation"]
                if _dir == "over" and early_bayes["pOver"] > _lineup_confidence_floor * 100:
                    early_bayes["pOver"]  = round(_lineup_confidence_floor * 100, 1)
                    early_bayes["pUnder"] = round((1 - _lineup_confidence_floor) * 100, 1)
                elif _dir == "under" and early_bayes["pUnder"] > _lineup_confidence_floor * 100:
                    early_bayes["pUnder"] = round(_lineup_confidence_floor * 100, 1)
                    early_bayes["pOver"]  = round((1 - _lineup_confidence_floor) * 100, 1)
                early_bayes["lineupStatus"] = _lineup_status

            if early_bayes and early_bayes.get("priorSamples", 0) >= 3:
                # ── PREFLIGHT PROJECTION: apply major downstream adjustments now ──
                # early_bayes.posteriorMean is the raw Bayesian estimate BEFORE
                # H2H, OPP-profile, and dominance adjustments that happen later.
                # If the dominance boost (Ball-Playing CB, GK inverted etc.) will
                # significantly move the final projection, we must tell AI the
                # RIGHT direction now — not the pre-adjustment direction.
                # Without this, AI writes "57.8 under" and the badge shows 66 OVER,
                # which is the exact contradiction the user is complaining about.
                _pf_proj = early_bayes["posteriorMean"]
                _pf_poss_props = {"pass_attempts", "passes", "key_passes", "crosses", "dribbles"}
                _pf_is_gk = _bayes_position.upper() in {"GK", "GOALKEEPER"}
                if match_dominance and req.propType in _pf_poss_props and not _pf_is_gk:
                    _pf_dom   = match_dominance.get("multiplier", 1.0)
                    _pf_avg   = match_dominance.get("teamSeasonAvg", 50)
                    _pf_exp   = match_dominance.get("expectedPoss", 50)
                    if _pf_avg < 52 and _pf_dom < 0.92:
                        # Pinned-back team — squeeze applies
                        _pf_proj = round(_pf_proj * _pf_dom, 1)
                    elif _pf_dom > 1.08 and _pf_exp > _pf_avg + 8:
                        # Positive dominance surge — apply damped boost (same logic as main pipeline)
                        _pf_damp = 0.65 if _pf_avg < 42 else (0.50 if _pf_avg < 48 else 0.35)
                        _pf_mult = 1.0 + (_pf_dom - 1.0) * _pf_damp
                        _pf_proj = round(_pf_proj * _pf_mult, 1)
                # Apply redistribution if it was calculated (already applied to early_bayes in some paths)
                # Note: early_bayes['posteriorMean'] may already include _redist_multiplier if it was applied above.
                # _pf_proj uses early_bayes['posteriorMean'] which is the post-redist value.

                # Use Monte Carlo P values for direction — not just mean vs line.
                # When P(UNDER) > P(OVER), recommend UNDER even if posteriorMean > line.
                _pf_p_over  = early_bayes.get("pOver", 50)
                _pf_p_under = early_bayes.get("pUnder", 50)
                _pf_rec_by_mean = "OVER" if _pf_proj > req.line else "UNDER"
                _pf_rec_by_prob = "OVER" if _pf_p_over >= _pf_p_under else "UNDER"
                _pf_rec = _pf_rec_by_prob
                if _pf_rec != _pf_rec_by_mean:
                    print(f"[PROB DIRECTION] {req.playerName}: mean→{_pf_rec_by_mean} but P(OVER)={_pf_p_over}%/P(UNDER)={_pf_p_under}% → using {_pf_rec}")
                _pf_bprob = early_bayes['pOver'] if _pf_rec == 'OVER' else early_bayes['pUnder']
                bdir = _pf_rec  # Use preflight direction as the anchor direction
                bprob = _pf_bprob
                if _pf_proj != early_bayes["posteriorMean"]:
                    print(f"[ANCHOR PREFLIGHT] {req.playerName}: raw={early_bayes['posteriorMean']} → preflight={_pf_proj} ({_pf_rec}) after dominance adjustment")

                bayesian_prompt_anchor = f"""
[MATHEMATICAL ENGINE — FINAL VERDICT — DO NOT CONTRADICT]
3-Layer Reverse Formula analysis ({early_bayes['priorSamples']} games): projects {_pf_proj} — VERDICT: {bdir} {req.line} (P={bprob}%).
Season avg: {early_bayes['priorMean']} | Recent form (decay-weighted): {early_bayes['momentumMean']} ({early_bayes['momentumLabel']}) | Context adj: {early_bayes['covariateAdjustment']:+.1f}
Streak: {early_bayes['streakFlag']} | Volatility: {early_bayes['volatility']} (CV={early_bayes['cv']}) | Reversal: {early_bayes['reversalFlag']}
IMPORTANT: Never use the word "Bayesian" in your response. Always say "Reverse Formula" instead.
>>> DIRECTION LOCK: The model's verdict is {bdir} {req.line} with projection {_pf_proj}. This is FINAL. Your ENTIRE analysis — every section, every sentence — must explain and support the {bdir} verdict. Do NOT argue for {'OVER' if bdir == 'UNDER' else 'UNDER'}. Do NOT present "tension" or "balanced" views. The math has already weighed all factors; your job is to narrate WHY the {bdir} verdict is tactically correct. Set aiProjection to a number on the {bdir} side of {req.line} (i.e. {'below' if bdir == 'UNDER' else 'above'} {req.line}). <<<"""
                # ── Inject quality-filtered hit rate into AI prompt ──────────────
                _ql_hr   = (historical_data.get("playerGameLogs") or {}).get("hitRates", {})
                _ql_tot  = _ql_hr.get("qualityTotal", 0)
                if _ql_tot >= 3 and req.line:
                    _ql_ov   = _ql_hr.get("qualityOverHits", 0)
                    _ql_pct  = _ql_hr.get("qualityOverPct", 0.0)
                    _ql_un   = _ql_tot - _ql_ov
                    _ql_un_pct = round(100 - _ql_pct, 1)
                    _ql_raw_tot = len((historical_data.get("playerGameLogs") or {}).get("games", []))
                    _ql_excl = _ql_raw_tot - _ql_tot
                    _ql_excl_note = (
                        f"{_ql_excl} sub-60-min game{'s' if _ql_excl != 1 else ''} excluded — partial-minute appearances distort the raw rate."
                        if _ql_excl > 0 else "All logged games were 60+ minutes (full sample)."
                    )
                    _ql_dir = "OVER" if _ql_pct >= 50 else "UNDER"
                    _ql_excl_suffix = f" — {_ql_excl} sub-60-min game{'s' if _ql_excl != 1 else ''} excluded" if _ql_excl > 0 else ""
                    bayesian_prompt_anchor += f"""
[QUALITY-FILTERED HIT RATE — 60+ MINUTE GAMES ONLY — USE AS PRIMARY SIGNAL]
Full-game appearances: {_ql_ov}/{_ql_tot} ({_ql_pct}%) OVER {req.line} | {_ql_un}/{_ql_tot} ({_ql_un_pct}%) UNDER {req.line}
{_ql_excl_note}
This quality-filtered rate is the TRUE historical signal. Include it in qualitySignal as: '{_ql_ov} of {_ql_tot} full-game appearances ({_ql_pct}%) went {_ql_dir} {req.line}{_ql_excl_suffix}.'"""
                # Inject quality prior note when Bayesian prior was quality-filtered
                if _quality_prior_applied and early_bayes:
                    bayesian_prompt_anchor += f"""
[QUALITY PRIOR — CRITICAL: WHY THE PRIOR IS {early_bayes.get('priorMean', '?')}]
The Reverse Formula EXCLUDED {_quality_prior_dropped} sub-60-min game{'s' if _quality_prior_dropped != 1 else ''} from the prior calculation. These were partial appearances (cameos, rotations, injury-limited games) — NOT representative of this player's full-game output.
Prior mean {early_bayes.get('priorMean', '?')} is based on {early_bayes.get('priorSamples', '?')} FULL GAMES (60+ minutes) only.
IMPORTANT: When narrating the projection, reference {early_bayes.get('priorMean', '?')} as the player's full-game average. Do NOT use a lower number — the lower raw average includes games where the player barely featured."""
                # Inject opponent tier filter note
                if _opp_tier_filter_applied and early_bayes:
                    _kept_str = " + ".join(_opp_tier_filter_kept_tiers)
                    bayesian_prompt_anchor += f"""
[OPPONENT QUALITY FILTER — CRITICAL]
The Reverse Formula also EXCLUDED {_opp_tier_filter_dropped} game{'s' if _opp_tier_filter_dropped != 1 else ''} vs lower-ranked opponents from the prior.
Current opponent rank: {_cur_opp_rank_for_tier}. Only kept games vs {_kept_str} opposition (comparable difficulty).
This ensures the prior reflects performance against teams of similar calibre, not inflated by results against easier sides.
Prior mean {early_bayes.get('priorMean', '?')} is drawn exclusively from {_kept_str} matchups. Reference this as the player's quality-opposition average."""
                # Inject redistribution context into prompt
                if _redist_alerts:
                    _redist_mult_pct = round((_redist_multiplier - 1) * 100)
                    bayesian_prompt_anchor += f"""
[TEAMMATE ABSENCE REDISTRIBUTION]
{" | ".join(_redist_alerts)}
The Reverse Formula has already boosted the projected {req.propType} by {_redist_mult_pct}% to account for this vacancy. Acknowledge this in your analysis."""
                # Inject lineup status context into prompt
                if _lineup_alert:
                    if _lineup_status == "starting":
                        bayesian_prompt_anchor += f"""
[LINEUP CONFIRMATION — POSITIVE SIGNAL]
{_lineup_alert}. Full minute involvement expected — no playing-time uncertainty for this projection."""
                    elif _lineup_status in ("substitute", "not_in_squad"):
                        bayesian_prompt_anchor += f"""
[LINEUP WARNING — REDUCED INVOLVEMENT]
{_lineup_alert}. Confidence capped at 45%. Flag this clearly in your analysis as a significant risk factor."""
                # Inject press intensity context into AI prompt
                _pi = early_bayes.get("pressIntensity", {})
                if _pi.get("label") not in (None, "Unknown", "Low") and req.propType in {"pass_attempts", "passes"}:
                    _pi_label = _pi["label"]
                    _pi_mult  = _pi.get("multiplier", 1.0)
                    _pi_sig   = _pi.get("signal_used", "possession")
                    if _pi_sig == "tackles":
                        _pi_da  = _pi.get("avg_defensive_actions", "?")
                        _pi_tkl = _pi.get("avg_tackles", "?")
                        _pi_int = _pi.get("avg_interceptions", "?")
                        bayesian_prompt_anchor += f"""
[OPPONENT PRESS INTENSITY — {_pi_label.upper()} (PPDA Proxy)]
PPDA Proxy (tackles + interceptions + fouls + blocks/game): {_pi_label} | Opponent avg {_pi_da} defensive actions/game ({_pi_tkl} tackles + {_pi_int} interceptions).
High defensive actions = opponent aggressively hunts the ball → subject player has less time/space with the ball, disrupted in possession.
Mathematical press penalty already applied: ×{_pi_mult} reduction to pass projection.
CRITICAL: This opponent actively disrupts passing lanes. Account for the subject player being pressured even when their team has the ball."""
                    else:
                        _pi_poss   = _pi.get("avg_poss", "?")
                        _pi_passes = _pi.get("avg_passes", "?")
                        bayesian_prompt_anchor += f"""
[OPPONENT POSSESSION PRESSURE — {_pi_label.upper()}]
Possession Pressure Index: {_pi_label} | Opponent avg {_pi_poss}% ball possession per game ({_pi_passes} total passes/game).
High opponent possession = the subject player's team has less time on the ball → subject player makes fewer pass attempts.
Mathematical possession penalty already applied: ×{_pi_mult} reduction to pass projection.
CRITICAL: This opponent dominates ball possession. Do NOT project pass totals near season average — the subject player's team will have significantly reduced time with the ball."""

                # Inject positional baseline context into the AI prompt
                _pb = (early_bayes or {}).get("positionalBaseline")
                if _pb and _pb.get("note") and "within realistic range" not in _pb.get("note", ""):
                    _pb_group = _pb.get("posGroup", "")
                    _pb_tier  = _pb.get("possessionTier", "")
                    _pb_p25   = _pb.get("p25")
                    _pb_p50   = _pb.get("p50")
                    _pb_p75   = _pb.get("p75")
                    _pb_from  = _pb.get("squeezedFrom")
                    _pb_to    = _pb.get("squeezedTo")
                    if _pb_from and _pb_to:
                        bayesian_prompt_anchor += f"""
[POSITIONAL ROLE BASELINE — CONTEXT CORRECTION APPLIED]
Position group: {_pb_group} | Team possession tier: {_pb_tier} (expected {_poss_for_baseline:.0f}%)
Realistic range for {_pb_group} in {_pb_tier}-possession team: p25={_pb_p25} / p50={_pb_p50} / p75={_pb_p75} per 90 min.
The raw projection ({_pb_from:.1f}) was outside this range and has been corrected to {_pb_to:.1f}.
IMPORTANT: In your analysis, explain WHY this player's current team context limits their output relative to their historical numbers. Do NOT cite the player's stats from a previous higher-possession club as evidence the OVER is likely."""

                # Inject game tempo context into the AI prompt
                if game_tempo.get("expectedTempo") != "normal" and req.propType in {"pass_attempts", "passes", "key_passes", "crosses", "dribbles"}:
                    tempo_label = game_tempo["expectedTempo"].upper()
                    exp_goals = game_tempo.get("expectedTotalGoals", "?")
                    bayesian_prompt_anchor += f"""
[GAME TEMPO WARNING]
Expected match tempo: {tempo_label} ({exp_goals} expected total goals).
{"HIGH tempo = more open play, more touches, higher pass volumes for ALL players." if tempo_label == "HIGH" else "LOW tempo = defensive, fewer passes, compressed stat lines."}
Factor this into your projection — do NOT ignore game flow."""
                # Inject favorite dampening context
                if favorite_dampening.get("applied") and req.propType in {"pass_attempts", "passes", "key_passes", "crosses"}:
                    bayesian_prompt_anchor += f"""
[HEAVY FAVORITE ALERT]
This player's team is a heavy favorite (odds: {favorite_dampening['teamOdds']:.2f}).
CRITICAL: Teams leading early often shift to game management mode — fewer passes, direct play, time-wasting.
If recommending OVER on passes, account for potential 2nd-half tempo drop."""
                print(f"[BAYESIAN ANCHOR] {req.playerName}: math={early_bayes['posteriorMean']} {bdir} ({bprob}%), momentum={early_bayes['momentumLabel']}, streak={early_bayes['streakFlag']}")
        except Exception as e:
            print(f"[BAYESIAN ANCHOR] Error: {e}")

        # =============================================
        # BUILD REAL RECENT SAMPLES FROM GAME LOGS
        # =============================================
        # These replace Gemini-generated samples with actual API-Sports data
        real_recent_samples = []
        if player_game_logs:
            gl_target_field_map = {
                "pass_attempts":   "passes_total",
                "shots":           "shots_total",
                "shots_on_target": "shots_on",
                "tackles":         "tackles_total",
                "key_passes":      "passes_key",
                "shots_assisted":  "passes_key",
                "saves":           "goals_saves",
                "interceptions":   "tackles_interceptions",
                "clearances":      "tackles_clearances",
                "blocks":          "tackles_blocks",
                "dribbles":        "dribbles_attempts",
                "fouls_drawn":     "fouls_drawn",
                "fouls_committed": "fouls_committed",
                "crosses":         "passes_crosses",
                "duels_won":       "duels_won",
                "yellow_cards":    "cards_yellow",
            }
            gl_target = gl_target_field_map.get(req.propType, "passes_total")
            for g in player_game_logs:
                stat_val = g.get(gl_target)
                if stat_val is not None and (g.get("minutes") or 0) > 0:
                    real_recent_samples.append({
                        "date": g.get("date", ""),
                        "opponent": g.get("opponent", ""),
                        "value": stat_val,
                        "minutesPlayed": g.get("minutes", 0),
                        "matchDifficulty": "medium",
                        "venue": g.get("venue", ""),
                    })

        # =============================================
        # UPGRADE #4: Per-90 minute normalization
        # =============================================
        # Extract per-90 rates from player's season stats so Gemini sees
        # normalized numbers, not raw totals skewed by minutes played
        per90_stats = {}
        if player_stats:
            stat_key_map = {
                "pass_attempts": ("passes", "total"),
                "shots": ("shots", "total"),
                "shots_on_target": ("shots", "on"),
                "tackles": ("tackles", "total"),
                "key_passes": ("passes", "key"),
                "shots_assisted": ("passes", "key"),
                "saves": ("goals", "saves"),
                "interceptions": ("tackles", "interceptions"),
                "blocks": ("tackles", "blocks"),
                "dribbles": ("dribbles", "attempts"),
                "fouls_drawn": ("fouls", "drawn"),
                "crosses": ("passes", "cross"),
                "clearances": ("tackles", "clearances"),
                "goals": ("goals", "total"),
                "assists": ("goals", "assists"),
                "duels_won": ("duels", "won"),
                "yellow_cards": ("cards", "yellow"),
                "fouls_committed": ("fouls", "committed"),
            }
            for stat_entry in player_stats.get("statistics", []):
                league_name = stat_entry.get("league", {}).get("name", "Unknown")
                season = stat_entry.get("league", {}).get("season", "")
                games = stat_entry.get("games", {})
                minutes = games.get("minutes") or 0
                appearances = games.get("appearences") or 0
                if minutes < 90 or appearances < 2:
                    continue  # Skip tiny samples

                entry = {
                    "league": league_name,
                    "season": season,
                    "appearances": appearances,
                    "totalMinutes": minutes,
                    "avgMinutesPerGame": round(minutes / appearances, 1) if appearances else 0,
                    "per90": {},
                    "rawPerGame": {},
                }

                for prop_key, (cat, sub) in stat_key_map.items():
                    raw_val = stat_entry.get(cat, {}).get(sub)
                    if raw_val is not None and raw_val > 0:
                        per_90 = round((raw_val / minutes) * 90, 2)
                        per_game = round(raw_val / appearances, 2) if appearances else 0
                        entry["per90"][prop_key] = per_90
                        entry["rawPerGame"][prop_key] = per_game

                if entry["per90"]:
                    per90_stats[f"{league_name}_{season}"] = entry

        if per90_stats:
            historical_data["per90Analysis"] = per90_stats

        # =============================================
        # UPGRADE #3: H2H player-specific stat extraction
        # =============================================
        # For each H2H fixture, fetch the player's individual stats in THAT match
        h2h_player_stats = []
        if h2h_data:
            h2h_fixture_ids = []
            for h in h2h_data[:H2H_PLAYER_SCAN_LIMIT]:
                fid = h.get("fixture", {}).get("id")
                if fid:
                    h2h_fixture_ids.append((fid, h))

            async def fetch_h2h_player_stat(fid, fixture_info):
                """Fetch the target player's stats from a specific H2H fixture"""
                try:
                    pstats = await api_football_request("fixtures/players", {"fixture": fid})
                    if not pstats:
                        return None

                    # Determine which team is the player's team in this fixture
                    home_id = fixture_info.get("teams", {}).get("home", {}).get("id")
                    away_id = fixture_info.get("teams", {}).get("away", {}).get("id")
                    home_name = fixture_info.get("teams", {}).get("home", {}).get("name", "")
                    away_name = fixture_info.get("teams", {}).get("away", {}).get("name", "")
                    home_goals = fixture_info.get("goals", {}).get("home", 0)
                    away_goals = fixture_info.get("goals", {}).get("away", 0)

                    # Player's team is home → opponent is away, and vice versa
                    player_is_home = (home_id == actual_team_id)
                    opponent_name = away_name if player_is_home else home_name
                    venue_in_match = "home" if player_is_home else "away"

                    # Find our player in the fixture stats
                    for team_data in pstats:
                        for p in team_data.get("players", []):
                            if p.get("player", {}).get("id") == req.playerId:
                                stats = p.get("statistics", [{}])[0] if p.get("statistics") else {}
                                minutes_played = stats.get("games", {}).get("minutes") or 0
                                # A team meeting is not a player H2H appearance.
                                # API-Football can return bench/DNP rows with
                                # zero minutes; those must not inflate the H2H
                                # sample or trigger the model's H2H adjustment.
                                if minutes_played <= 0:
                                    return None
                                stat_key_map_h2h = {
                                    "pass_attempts": stats.get("passes", {}).get("total"),
                                    "shots": stats.get("shots", {}).get("total"),
                                    "shots_on_target": stats.get("shots", {}).get("on"),
                                    "tackles": stats.get("tackles", {}).get("total"),
                                    "key_passes": stats.get("passes", {}).get("key"),
                                    "shots_assisted": stats.get("passes", {}).get("key"),
                                    "saves": stats.get("goals", {}).get("saves"),
                                    "interceptions": stats.get("tackles", {}).get("interceptions"),
                                    "blocks": stats.get("tackles", {}).get("blocks"),
                                    "dribbles": stats.get("dribbles", {}).get("attempts"),
                                    "fouls_drawn": stats.get("fouls", {}).get("drawn"),
                                    "crosses": stats.get("passes", {}).get("cross"),
                                    "clearances": stats.get("tackles", {}).get("clearances"),
                                    "goals": stats.get("goals", {}).get("total"),
                                    "assists": stats.get("goals", {}).get("assists"),
                                    "duels_won": stats.get("duels", {}).get("won"),
                                    "yellow_cards": stats.get("cards", {}).get("yellow"),
                                    "fouls_committed": stats.get("fouls", {}).get("committed"),
                                }
                                # Enrich with possession from team fixture cache
                                _h2h_poss_team = None
                                _h2h_poss_opp  = None
                                try:
                                    _h2h_ck = f"fxt_{fid}_{actual_team_id}"
                                    _h2h_poss_doc = await db.fixture_player_cache.find_one(
                                        {"_k": _h2h_ck}, {"_id": 0, "d.possession": 1}
                                    )
                                    if _h2h_poss_doc and _h2h_poss_doc.get("d"):
                                        _raw = str(_h2h_poss_doc["d"].get("possession", "")).replace("%", "").strip()
                                        if _raw:
                                            _h2h_poss_team = int(_raw)
                                            _h2h_poss_opp  = 100 - _h2h_poss_team
                                except Exception:
                                    pass
                                return {
                                    "date": fixture_info.get("fixture", {}).get("date", ""),
                                    "opponent": opponent_name,
                                    "venue": venue_in_match,
                                    "minutesPlayed": minutes_played,
                                    "statValues": {k: v for k, v in stat_key_map_h2h.items() if v is not None},
                                    "targetStat": stat_key_map_h2h.get(req.propType),
                                    "targetStatPer90": round((stat_key_map_h2h.get(req.propType, 0) or 0) / minutes_played * 90, 2) if minutes_played > 0 and stat_key_map_h2h.get(req.propType) else None,
                                    "matchScore": f"{home_goals}-{away_goals}",
                                    "teamPossession": _h2h_poss_team,
                                    "opponentPossession": _h2h_poss_opp,
                                }
                    return None
                except Exception:
                    return None

            if h2h_fixture_ids:
                try:
                    h2h_results = await aio.wait_for(
                        aio.gather(*[
                            fetch_h2h_player_stat(fid, fi)
                            for fid, fi in h2h_fixture_ids[:H2H_PLAYER_SCAN_LIMIT]
                        ]),
                        timeout=12
                    )
                    h2h_player_stats = [
                        r for r in h2h_results if r
                    ][:H2H_PLAYER_RESULT_LIMIT]
                except aio.TimeoutError:
                    h2h_player_stats = []
        print(f"[TIMING] H2H+prep: {_t.time()-_t0:.1f}s total")

        if h2h_player_stats:
            # Calculate H2H averages for the target stat
            h2h_values = [s["targetStat"] for s in h2h_player_stats if s.get("targetStat") is not None]
            h2h_summary = {
                "matches": h2h_player_stats,
                "targetProp": req.propType,
                "sampleSize": len(h2h_values),
                "searchedFixtureCount": min(len(h2h_fixture_ids), H2H_PLAYER_SCAN_LIMIT),
                "historySeasons": H2H_HISTORY_SEASONS,
                "historyDepth": "six seasons",
            }
            if h2h_values:
                h2h_summary["avgVsOpponent"] = round(sum(h2h_values) / len(h2h_values), 2)
                h2h_summary["minVsOpponent"] = min(h2h_values)
                h2h_summary["maxVsOpponent"] = max(h2h_values)

            # ── Enriched H2H metadata for the pro analysis display ──────────
            # Total team meetings found (not just ones the player appeared in)
            h2h_summary["teamMeetings"] = len(h2h_data) if h2h_data else 0

            # Season span from team H2H fixture dates
            try:
                _h2h_years = []
                for _hd in (h2h_data or []):
                    _hd_date = (_hd.get("fixture") or {}).get("date", "")
                    if _hd_date and len(_hd_date) >= 4:
                        try:
                            _h2h_years.append(int(_hd_date[:4]))
                        except (ValueError, TypeError):
                            pass
                if _h2h_years:
                    h2h_summary["seasonsCovered"] = {
                        "min": min(_h2h_years), "max": max(_h2h_years),
                        "range": f"{min(_h2h_years)}–{max(_h2h_years)}",
                    }
            except Exception:
                pass

            # Trend: recent 3 appearances vs prior (positive = improving)
            if len(h2h_values) >= 4:
                try:
                    _recent_3_avg = sum(h2h_values[:3]) / 3
                    _prior_avg = sum(h2h_values[3:]) / len(h2h_values[3:])
                    _trend_delta = _recent_3_avg - _prior_avg
                    h2h_summary["trendDirection"] = (
                        "improving" if _trend_delta > 3
                        else "declining" if _trend_delta < -3
                        else "stable"
                    )
                    h2h_summary["trendDelta"] = round(_trend_delta, 2)
                except Exception:
                    h2h_summary["trendDirection"] = "stable"
            else:
                h2h_summary["trendDirection"] = "stable"

            # Venue hit rate at the player's current venue
            try:
                _vh_hits = 0
                _vh_total = 0
                for _hs in h2h_player_stats:
                    if _hs.get("venue") == player_venue and _hs.get("targetStat") is not None:
                        _vh_total += 1
                        if _hs["targetStat"] > req.line:
                            _vh_hits += 1
                if _vh_total > 0:
                    h2h_summary["venueHitRate"] = {
                        "hits": _vh_hits, "total": _vh_total,
                        "pct": round(_vh_hits / _vh_total * 100),
                        "venue": player_venue,
                    }
            except Exception:
                pass

            historical_data["h2hPlayerStats"] = h2h_summary

        # Extract player's ACTUAL position from API-Sports data
        player_position = ""
        if player_stats:
            stats_list = player_stats.get("statistics", [])
            # Find the stat entry with most appearances (most relevant)
            best_entry = None
            best_apps = 0
            for s in stats_list:
                apps = s.get("games", {}).get("appearences") or 0
                pos = s.get("games", {}).get("position", "")
                if apps > best_apps and pos:
                    best_apps = apps
                    best_entry = s
                    player_position = pos
            # If we found a better entry, also try to get stats from multiple seasons
            if not player_position:
                for s in stats_list:
                    pos = s.get("games", {}).get("position", "")
                    if pos:
                        player_position = pos
                        break

        # =============================================
        # AI POSITION RESOLVER: Get specific position (RW, CM, CB, etc.)
        # Uses cache first, then AI as fallback with API-Sports context
        # =============================================
        specific_position = ""
        player_role = ""
        GENERIC_POSITIONS = {"Goalkeeper", "Defender", "Midfielder", "Attacker", ""}

        # Position-to-role compatibility: ensures roles match positions
        POSITION_ROLE_MAP = {
            "GK": {"Shot-Stopper", "Sweeper Keeper"},
            "CB": {"Ball-Playing CB", "Stopper"},
            "LB": {"Fullback", "Wing-Back", "Inverted Fullback"},
            "RB": {"Fullback", "Wing-Back", "Inverted Fullback"},
            "LWB": {"Wing-Back", "Fullback"},
            "RWB": {"Wing-Back", "Fullback"},
            "CDM": {"Anchor", "Ball Winner", "Deep-Lying Playmaker"},
            "CM": {"Box-to-Box", "Mezzala", "Deep-Lying Playmaker", "Ball Winner"},
            "CAM": {"Advanced Playmaker", "Wide Playmaker", "Shadow Striker"},
            "LM": {"Wide Playmaker", "Traditional Winger"},
            "RM": {"Wide Playmaker", "Traditional Winger"},
            "LW": {"Traditional Winger", "Inverted Winger", "Inside Forward", "Progressive Carrier"},
            "RW": {"Traditional Winger", "Inverted Winger", "Inside Forward", "Progressive Carrier"},
            "CF": {"Complete Forward", "False 9", "Target Man", "Pressing Forward"},
            "ST": {"Poacher", "Target Man", "Complete Forward", "Pressing Forward"},
            "SS": {"Shadow Striker", "False 9"},
        }

        # Constrain valid positions by API-Sports generic category
        GENERIC_TO_SPECIFIC = {
            "Goalkeeper": {"GK"},
            "Defender": {"CB", "LB", "RB", "LWB", "RWB"},
            "Midfielder": {"CDM", "CM", "CAM", "LM", "RM", "LW", "RW"},
            "Attacker": {"LW", "RW", "CF", "ST", "SS", "CAM"},
        }

        if player_position in GENERIC_POSITIONS or not player_position:
            # Check user-provided position override first
            if req.positionOverride:
                specific_position = req.positionOverride
                player_role = req.roleOverride or ""
                print(f"[POS RESOLVE] User override: {req.playerName} → {specific_position} ({player_role})")
            else:
                # Check cache (with 30-day expiry and prompt-version check)
                from config import POSITION_PROMPT_VERSION
                cached_pos = await db.player_positions.find_one(
                    {"playerId": req.playerId}, {"_id": 0, "specificPosition": 1, "role": 1, "updatedAt": 1, "promptVersion": 1, "source": 1}
                )
                cache_valid = False
                if cached_pos and cached_pos.get("specificPosition"):
                    # Manual overrides are permanent — never re-resolve regardless of version or TTL
                    if cached_pos.get("source") == "manual_override":
                        cache_valid = True
                    # Check prompt version first — stale version always forces re-resolution
                    elif cached_pos.get("promptVersion", 0) < POSITION_PROMPT_VERSION:
                        stored_version = cached_pos.get("promptVersion", 0)
                        print(f"[POS RESOLVE] Prompt version outdated (v{stored_version} < v{POSITION_PROMPT_VERSION}): {req.playerName} — re-resolving")
                        cache_valid = False
                    else:
                        # Check if cache is fresh (< 30 days)
                        cached_at = cached_pos.get("updatedAt", "")
                        if cached_at:
                            try:
                                cached_dt = datetime.fromisoformat(cached_at.replace("Z", "+00:00"))
                                age_days = (datetime.now(timezone.utc) - cached_dt).days
                                cache_valid = age_days < 30
                                if not cache_valid:
                                    print(f"[POS RESOLVE] Cache expired ({age_days} days): {req.playerName}")
                            except Exception:
                                cache_valid = True  # If we can't parse date, trust the cache
                        else:
                            cache_valid = True  # Legacy cache entries without updatedAt

                if cache_valid:
                    cached_specific = cached_pos["specificPosition"]
                    allowed_cached_positions = GENERIC_TO_SPECIFIC.get(player_position)
                    if (
                        allowed_cached_positions
                        and cached_specific not in allowed_cached_positions
                    ):
                        # A versioned cache entry is not enough: API-Sports'
                        # generic category is a hard safety boundary.  A
                        # Defender must never inherit ST/Poacher math just
                        # because an earlier AI resolution was wrong.
                        print(
                            f"[POS RESOLVE] Category guard: {req.playerName} "
                            f"{player_position} rejects {cached_specific}/{cached_pos.get('role', '')}"
                        )
                        cache_valid = False
                    else:
                        specific_position = cached_specific
                        player_role = cached_pos.get("role", "")
                    valid_roles = POSITION_ROLE_MAP.get(specific_position, set())
                    if valid_roles and (not player_role or player_role not in valid_roles):
                        corrected_role = sorted(valid_roles)[0] if valid_roles else ""
                        print(f"[POS RESOLVE] Cache role fix: {req.playerName} {specific_position}/{player_role} → {corrected_role}")
                        player_role = corrected_role
                        await db.player_positions.update_one(
                            {"playerId": req.playerId},
                            {"$set": {"role": corrected_role}}
                        )
                    else:
                        print(f"[POS RESOLVE] Cache hit: {req.playerName} → {specific_position} ({player_role})")

            if not specific_position:
                try:
                    from openai import OpenAI as SyncOpenAI

                    # Build advisory (not hard-constraining) category hint based on API-Sports category.
                    # We always allow ALL positions — stats evidence can override the API category.
                    # API-Football sometimes miscategorizes players (e.g., CM tagged as "Attacker"),
                    # so treating the category as a hard constraint causes systematic errors.
                    pos_list = "GK, CB, LB, RB, LWB, RWB, CDM, CM, CAM, LM, RM, LW, RW, CF, ST, SS"
                    allowed_positions = None  # allow all — stats are the authority
                    suggested_positions = GENERIC_TO_SPECIFIC.get(player_position, None)
                    if suggested_positions and player_position:
                        pos_hint_list = ", ".join(sorted(suggested_positions))
                        category_hint = (
                            f"\nAPI-Sports categorizes this player as: {player_position} "
                            f"(suggested positions: {pos_hint_list}). "
                            f"Use the stats below to confirm — if the stats strongly suggest a different position, "
                            f"you may pick ANY position from the full list: {pos_list}."
                        )
                    else:
                        category_hint = ""

                    # STATS-AWARE: Extract position-relevant stats for evidence-based resolution
                    stats_evidence = ""
                    stats_list = player_stats.get("statistics", []) if player_stats else []
                    if stats_list:
                        latest = stats_list[-1] if stats_list else {}
                        tck = latest.get("tackles", {})
                        duels = latest.get("duels", {})
                        pss = latest.get("passes", {})
                        drb = latest.get("dribbles", {})
                        sht = latest.get("shots", {})
                        gls = latest.get("goals", {})
                        fls = latest.get("fouls", {})
                        cards = latest.get("cards", {})
                        games = latest.get("games", {})
                        clr_total = tck.get("clearances", 0) or 0
                        apps_total = max(1, games.get("appearances", 1) or 1)
                        clr_pg = round(clr_total / apps_total, 1)
                        stats_evidence = f"""
ACTUAL SEASON STATS (use these to determine position — stats don't lie):
- Appearances: {games.get('appearances', '?')}, Minutes: {games.get('minutes', '?')}, Rating: {games.get('rating', '?')}
- Tackles: {tck.get('total', 0)}, Interceptions: {tck.get('interceptions', 0)}, Blocks: {tck.get('blocks', 0)}
- Clearances (season total): {clr_total} → {clr_pg}/game  ← KEY CB SIGNAL (≥2.0/game = almost certainly CB; <1.0/game with forward runs = fullback)
- Duels won: {duels.get('won', 0)}/{duels.get('total', 0)}
- Passes total: {pss.get('total', 0)}, Key passes: {pss.get('key', 0)}, Accuracy: {pss.get('accuracy', '?')}%
- Dribbles: {drb.get('attempts', 0)} attempts, {drb.get('success', 0)} successful
- Shots: {sht.get('total', 0)}, On target: {sht.get('on', 0)}
- Goals: {gls.get('total', 0)}, Assists: {gls.get('assists', 0)}
- Fouls drawn: {fls.get('drawn', 0)}, Committed: {fls.get('committed', 0)}
- Yellow cards: {cards.get('yellow', 0)}, Red: {cards.get('red', 0)}
POSITION CLUES — distinguish DEEP vs ADVANCED roles:
- CB (Centre-Back): CENTRAL defender — stays in the middle/back line, does NOT overlap forward. CB is the correct code for ALL central defenders regardless of whether they play on the left or right side of a back-4. A right-sided CB is STILL CB, NEVER RB. Key stats: clearances ≥2/game (strongest CB signal), high aerial duels, low dribbles (<0.8/game), low shots (<0.4/game). Examples: Van Dijk, Kompany, Dias, Stones, Akanji, Botman, Finn Surman.
- RB / LB (Fullback): WIDE defenders who overlap forward. Low clearances (<1.5/game), higher dribbles/crosses. NEVER assign RB/LB to a player who is primarily a central defender.
- CDM / deep-lying playmaker (regista): the team's tempo-setter and build-up hub. HIGHEST pass volume on the team (touches the ball most when in possession), VERY HIGH pass accuracy, sits DEEPEST in midfield, LOW shots, LOW dribbles. Interceptions can be moderate (a regista is a passer first, not a destroyer). Role = "Deep-Lying Playmaker". Vitinha at PSG = CDM / Deep-Lying Playmaker (regista) — he is the metronome who orchestrates from deep and leads the team in touches/passes. He is NOT a Box-to-Box runner and NOT a CAM.
- CDM (ball-winning pivot): HIGH interceptions/tackles, high pass accuracy, LOW key passes, LOW shots. Role = "Ball Winner" or "Anchor".
- CM (box-to-box): balanced tackles + passes + key passes, MODERATE shots AND noticeable dribbles/forward runs, contributes goals/assists. Role = "Box-to-Box". Only pick this when the player visibly gets forward (shots + key passes + dribbles all moderate-to-high), NOT for a deep metronome.
- CAM (advanced playmaker): HIGH key passes (3+), moderate dribbles, LOW tackles. Plays AHEAD of midfield.
- Winger: high dribbles/crosses, low tackles
- ST: high shots/goals, low tackles

CRITICAL: The single highest-pass-volume midfielder who sits deepest, dictates tempo, with VERY HIGH pass accuracy + LOW shots + LOW dribbles = CDM / Deep-Lying Playmaker (regista), NOT Box-to-Box and NOT CAM. Box-to-Box requires visible forward output (shots + dribbles + goal contributions). CAM requires high key passes (3+)."""

                    pos_prompt = f"What is {req.playerName}'s primary position and tactical role at {corrected_team_name}?{category_hint}{stats_evidence}\nPosition must be one of: {pos_list}\nRole must be one of: Shot-Stopper, Sweeper Keeper, Ball-Playing CB, Stopper, Fullback, Wing-Back, Inverted Fullback, Anchor, Box-to-Box, Deep-Lying Playmaker, Ball Winner, Mezzala, Advanced Playmaker, Wide Playmaker, Traditional Winger, Inverted Winger, Progressive Carrier, Inside Forward, Target Man, Poacher, False 9, Shadow Striker, Complete Forward, Pressing Forward\nReply ONLY: POSITION|ROLE"

                    # GROK POSITION RESOLUTION
                    is_defender = player_position != "Goalkeeper"

                    async def resolve_pos_grok() -> str:
                        """Call Gemini to resolve position. Returns raw POSITION|ROLE string."""
                        from ai_engine import _ai_call
                        sys_msg = "You are a football/soccer tactical analyst. Reply in EXACTLY this format on one line:\nPOSITION|ROLE\nNothing else."
                        return await _ai_call(
                            pos_prompt, system=sys_msg,
                            temperature=0, max_tokens=20, timeout=15,
                        )

                    def parse_pos_response(resp_text, allowed):
                        parts = resp_text.strip().split("|")
                        pos = parts[0].strip().upper().replace(".", "").replace(",", "") if parts else ""
                        role = parts[1].strip() if len(parts) > 1 else ""
                        if pos in (allowed or {"GK","CB","LB","RB","LWB","RWB","CDM","CM","CAM","LM","RM","LW","RW","CF","ST","SS"}):
                            return pos, role
                        return None, None

                    valid_positions = allowed_positions or {"GK","CB","LB","RB","LWB","RWB","CDM","CM","CAM","LM","RM","LW","RW","CF","ST","SS"}

                    if is_defender:
                        try:
                            grok_text = await resolve_pos_grok()
                            grok_pos, grok_role = parse_pos_response(grok_text, valid_positions)

                            if grok_pos:
                                pos_code = grok_pos
                                role_text = grok_role or ""
                                print(f"[POS RESOLVE] Gemini: {req.playerName} → {pos_code}")
                            else:
                                raise ValueError("Gemini returned invalid position")
                        except Exception as e:
                            print(f"[POS RESOLVE] Gemini position failed ({e}), retrying...")
                            grok_text2 = await resolve_pos_grok()
                            pos_code, role_text = parse_pos_response(grok_text2, valid_positions)
                            if not pos_code:
                                raise ValueError("Gemini returned invalid position on retry")
                    else:
                        # Non-defenders: single Gemini call (with stats context)
                        pos_text = await resolve_pos_grok()
                        pos_code, role_text = parse_pos_response(pos_text, valid_positions)
                        if not pos_code:
                            raise ValueError("AI returned invalid position on retry")

                    if pos_code:
                        specific_position = pos_code
                        # Validate role matches position
                        valid_roles = POSITION_ROLE_MAP.get(pos_code, set())
                        if role_text and valid_roles and role_text not in valid_roles:
                            print(f"[POS RESOLVE] Role '{role_text}' invalid for {pos_code}, defaulting to first valid role")
                            role_text = sorted(valid_roles)[0] if valid_roles else ""
                        elif not role_text and valid_roles:
                            role_text = sorted(valid_roles)[0]
                        player_role = role_text
                        await db.player_positions.update_one(
                            {"playerId": req.playerId},
                            {"$set": {
                                "playerId": req.playerId,
                                "playerName": req.playerName,
                                "team": corrected_team_name,
                                "genericPosition": player_position,
                                "specificPosition": specific_position,
                                "role": player_role,
                                "promptVersion": POSITION_PROMPT_VERSION,
                                "updatedAt": datetime.now(timezone.utc).isoformat(),
                            }},
                            upsert=True
                        )
                        print(f"[POS RESOLVE] AI resolved: {req.playerName} → {specific_position} | {player_role} (cached)")
                    else:
                        print("[POS RESOLVE] AI returned invalid position")
                except Exception as e:
                    print(f"[POS RESOLVE] Error: {e}")

                # Gemini is intentionally disabled for credit protection. If
                # the API category is known but there is no valid specific
                # cache, use the conservative category default instead of
                # leaving the engine with a stale or impossible attacking
                # position.
                if not specific_position and player_position in GENERIC_TO_SPECIFIC:
                    category_defaults = {
                        "Goalkeeper": ("GK", "Shot-Stopper"),
                        "Defender": ("CB", "Stopper"),
                        "Midfielder": ("CM", "Box-to-Box"),
                        "Attacker": ("ST", "Pressing Forward"),
                    }
                    specific_position, player_role = category_defaults[player_position]
                    print(
                        f"[POS RESOLVE] Category fallback: {req.playerName} "
                        f"{player_position} → {specific_position} | {player_role}"
                    )
        else:
            specific_position = player_position

        # Use specific position if available, otherwise fall back to generic
        display_position = specific_position or player_position
        display_role = player_role

        # ── POSITION-CORRECTED BASELINE RE-SQUEEZE ────────────────────────────
        # The positional baseline ran at line ~2866 using _bayes_position from
        # the early cache lookup (which may have been empty or wrong on first run).
        # Now that specific_position is resolved via the stats-aware AI resolver,
        # re-run the baseline + squeeze if the position changed — so the CURRENT
        # prediction benefits from the correct position, not just the next one.
        try:
            if (
                specific_position
                and early_bayes
                and specific_position != _bayes_position
                and req.propType not in {"saves", "goalie_saves"}
            ):
                from positional_baseline import get_positional_baseline, apply_positional_squeeze
                _poss_rb = match_dominance.get("expectedPoss", 50.0) if match_dominance else 50.0
                _tavg_rb = match_dominance.get("teamAvgPasses") if match_dominance else None
                _plab_rb = (ai_press_intensity or {}).get("label") if ai_press_intensity else None
                _pos_baseline_new = get_positional_baseline(
                    position=specific_position,
                    expected_poss=_poss_rb,
                    prop_type=req.propType,
                    role=player_role,
                    team_avg_passes=_tavg_rb,
                    press_intensity_label=_plab_rb,
                )
                if _pos_baseline_new:
                    # Re-squeeze from original pre-squeeze posteriorMean.
                    # _pos_baseline["squeezedFrom"] holds the pre-squeeze value when
                    # the first (wrong-position) squeeze fired; fall back to current pm.
                    _origin_pm = _pos_baseline.get("squeezedFrom") if _pos_baseline else None
                    _resqueeze_pm = _origin_pm if _origin_pm is not None else early_bayes.get("posteriorMean", req.line)
                    _adj_pm2, _pos_note2 = apply_positional_squeeze(
                        posterior_mean=_resqueeze_pm,
                        baseline=_pos_baseline_new,
                        n_samples=early_bayes.get("priorSamples", 0),
                    )
                    # ALWAYS apply the result — even when no squeeze fires we must
                    # restore posteriorMean to the pre-wrong-squeeze value.
                    import math as _math2
                    early_bayes["posteriorMean"] = _adj_pm2
                    early_bayes["recommendation"] = "over" if _adj_pm2 > req.line else "under"
                    _pos_baseline_new["squeezedFrom"] = _resqueeze_pm
                    _pos_baseline_new["squeezedTo"]   = _adj_pm2
                    if _pos_note2:
                        _pos_baseline_new["note"] = f"[RERESOLVED] {_pos_note2}"
                    else:
                        _pos_baseline_new["note"] = f"[RERESOLVED {specific_position}] within realistic range — no squeeze"
                    _bl_iqr2 = _pos_baseline_new.get("p75", req.line) - _pos_baseline_new.get("p25", req.line)
                    _bl_std2 = _bl_iqr2 / 1.35 if _bl_iqr2 > 0 else max(req.line * 0.25, 1.0)
                    _z2 = (_adj_pm2 - req.line) / max(_bl_std2, 0.01)
                    _po2 = round(max(1.0, min(99.0, 50.0 + 50.0 * _math2.erf(_z2 / _math2.sqrt(2)))), 1)
                    early_bayes["pOver"]  = _po2
                    early_bayes["pUnder"] = round(100.0 - _po2, 1)
                    early_bayes["positionalBaseline"] = _pos_baseline_new
                    print(f"[POS RE-RESOLVE] {req.playerName}: {_bayes_position or 'none'}→{specific_position} "
                          f"role={player_role} pm={_resqueeze_pm:.2f}→{_adj_pm2:.2f} P(over)={_po2}%")
        except Exception as _rrb_err:
            print(f"[POS RE-RESOLVE] non-fatal: {_rrb_err}")

        # ── DEFENDER POSSESSION MULTIPLIER OVERRIDE ──────────────────────────
        # The match-dominance possession multiplier uses poss_ratio = expected/season_avg.
        # For defenders on pass_attempts, this formula can PENALIZE slightly-below-average
        # expected possession even when the team is still a neutral-to-dominant possession side.
        # Root cause: if Huracan avg away = 52% and expected = 50.9%, ratio = 0.979 → multiplier
        # reduces passes by 2%. But 50.9% is basically neutral, not a deficit.
        #
        # Fix: recompute the possession multiplier for defenders using an ABSOLUTE 50% neutral
        # baseline so that any possession above 50% gives a positive (not relative-neutral) boost.
        # Also widen the cap to 0.55 (vs 0.35) since defender passes scale tightly with possession.
        _is_def_pass = (
            req.propType in {"pass_attempts", "passes"}
            and player_position in {"Defender"}
            and match_dominance is not None
        )
        if _is_def_pass:
            _def_exp_poss = match_dominance.get("expectedPoss", 50.0)
            _def_raw_adj  = (_def_exp_poss - 50.0) / 50.0  # +0.30 at 65%, +0.018 at 50.9%
            _def_capped   = max(-0.40, min(0.55, _def_raw_adj))
            _def_new_mult = round(1.0 + _def_capped, 3)
            _def_old_mult = match_dominance.get("multiplier", 1.0)
            if abs(_def_new_mult - _def_old_mult) > 0.02:
                match_dominance["multiplier"] = _def_new_mult
                match_dominance["notes"].append(
                    f"Defender pass override: absolute baseline → ×{_def_new_mult} "
                    f"(was ×{_def_old_mult}, exp poss {_def_exp_poss:.1f}%)"
                )
                print(f"[DEF PASS MULT] {req.playerName}: poss={_def_exp_poss:.1f}% → ×{_def_old_mult}→×{_def_new_mult}")

        # =============================================
        # MULTI-AI CONSENSUS ENGINE (3 AIs)
        # Gemini Flash (GK) — single AI engine
        # =============================================
        PREDICTION_SYSTEM = """You are a soccer prop analyst. The Reverse Formula math engine has ALREADY computed the final projection and recommendation — your ONLY job is to explain the tactical reasons WHY that math verdict is correct. You are an explainer and narrator of the model's output, NOT an independent analyst reaching your own conclusions.

⚠️ CRITICAL RULE — READ FIRST:
The [MATHEMATICAL ENGINE] block in the user message contains the model's verdict (e.g. "projects 48.0 UNDER"). That verdict is FINAL and LOCKED. Every word you write must support and explain that direction. You MUST NOT write analysis that argues for the opposite side, even if your own tactical instinct says otherwise. If you personally would have called OVER, your job is still to explain why the model's UNDER verdict is tactically defensible.

REQUIRED JSON FIELDS:

"aiProjection": A number close to the Reverse Formula projection — your tactical read should align with the math direction. If the math says UNDER the line, your aiProjection must be below the line. If OVER, above it. Do NOT produce a number that contradicts the model's direction.

"reasoning": 4-6 sentences explaining the TACTICAL REASONS why the model's verdict is correct. Explain the specific structural factors in THIS matchup that suppress or inflate this stat in the direction the model has already identified. Cite real numbers from the game logs and opponent data. This is NOT independent analysis — it is tactical explanation of the model's output.

"tacticalBreakdown": Rich markdown (~1800 chars) with these MANDATORY sections. Every section must be written to SUPPORT the model's direction:

  **Verdict** — One decisive sentence stating the model's call, projection, and edge. Must match the [MATHEMATICAL ENGINE] direction exactly.

  **Matchup** — Explain WHY the opponent's defensive or pressing shape creates the outcome the model has identified. Focus only on the factors that SUPPORT the model's direction. For GKs: does this opponent press high forcing back-passes, or do they sit deep letting the GK play out calmly? For midfielders: how does the opponent's shape specifically affect VOLUME in the model's predicted direction? Cite the [POSITION COMPARISON] average.

  **Situation** — Read the MONEYLINE and possession context to explain why game flow supports the model's verdict. If the model says UNDER, explain the structural game-flow reasons volume will be suppressed. If OVER, explain the amplification factors. Do NOT present a "tension" — commit to the direction the math has already chosen.

  **Analysis** — MANDATORY: Reference each recent game BY NAME with its exact number (e.g. "72 vs Villarreal, 43 vs Osasuna"). For every outlier — explain the tactical reason WHY that number happened. Identify which past game is most tactically similar to TODAY'S OPPONENT and use it as your anchor. The historical pattern must support the model's direction. Home/away split matters — explain WHY structurally.

  **Scenarios** — Three tactical scenarios with specific stat ranges. If [FIRST GOAL PROFILE] is provided, use those rates. The BASE CASE scenario must land in the direction the model projects. Worst/best cases represent deviation risk:
  Best case: [trigger] → [range]
  Base case: [expected game flow] → [range — must be on the model's side of the line]
  Worst case: [risk trigger] → [range]
  Populate scenarioProbabilities.best / .base / .worst as decimals summing to 1.0.

  **Risk** — What specific event would INVALIDATE the model's call? Be precise about timing and mechanism.

  **TL;DR** — 1-2 sentences closing the case for the model's verdict. Must state the direction and WHY the model is right. No hedging, no "tension" — the math has spoken.

"sharpSummary": 2 decisive sentences stating WHY the model's projection is correct and what the market misses. Must commit to the direction — do NOT describe tension or present both sides. This is the first thing users read — it must reinforce the badge they see.

"scenarioAnalysis": 3 sentences covering best/base/worst scenarios with values that bracket the model's projection on the correct side of the line.

"keyEvidence": The 3 most important data points supporting the model's direction, including opponent positional allowance and its tactical explanation.

"gameFlowDynamics": How expected possession and game state specifically drive volume in the MODEL'S PREDICTED DIRECTION. Be tactical, not generic.

"sensitivityTests": One specific scenario that would flip the model's recommendation (the main risk).
"subRisk": One specific substitution or rotation risk with timing.
"uncertaintyNote": One honest limitation of this projection.

"qualitySignal": Exactly one sentence summarizing the quality-filtered (60+ min games) hit rate. Use the exact sentence provided in the [QUALITY-FILTERED HIT RATE] block if present (e.g. "9 of 11 full-game appearances (82%) went OVER 47.5 — 3 sub-60-min games excluded."). If no quality data was provided, set to empty string "".

"keyFactors": Array of exactly 3 strings (max 65 chars each). The 3 most decisive tactical facts that explain the model's verdict. Each MUST cite a specific number or rate. Must directly support the model's direction. Bad: "Player has good form". Good: "6/8 home starts vs low-block teams went OVER 52". Good: "Away avg 43 passes — 12 below home avg (venue split key)". Good: "Opp allows 58% poss avg — CDM becomes recycling hub".

POSITION-SPECIFIC REASONING FRAMEWORKS (apply the relevant one):

GOALKEEPER (pass_attempts/saves):
- pass_attempts: The INVERTED possession rule is everything. Low team possession = defenders constantly recycling under pressure to the GK = volume explosion. High team possession = GK barely involved in build-up = volume suppression. But READ THE OPPONENT — a team that presses relentlessly forces even dominant-possession GKs into rapid distribution. For saves: opponent SoT rate × GK save% × match tempo = your anchor. A high-block defensive team facing a prolific attacker on a high-tempo away game is the max-saves scenario.

STRIKER/FORWARD (shots, goals, assists):
- Think about SPACE, not just volume. A striker facing a high defensive line gets in behind for shots. A striker facing a deep block needs service from midfield — check if that midfield creates. Shots depend on penalty box entries, not just possession. An isolated striker in a low-block game can still pop off 4-5 shots if the team plays direct.

MIDFIELDER (passes, key_passes, assists):
- Ball-circulation midfielders: possession % is the primary driver. Every 5% more possession = roughly 8-12 more passes for the deepest midfielder. Key passes / assists: look at how many times the team reaches the final third AND how the striker presses — a high striker press creates more through-ball opportunities.
- CRITICAL — HOME CDM DEEP-BLOCK RULE: When a dominant home team (60%+ expected possession) faces a deep-sitting weak opponent (opponent expected possession < 36%), the CDM/DM/DLP becomes a ball-RECYCLING HUB. The deep block creates endless short-cycle sequences that all funnel back through the deepest midfielder. In this scenario, the CDM's pass count EXCEEDS their historical season average — sometimes significantly. Do NOT apply a low-motivation or dead-rubber penalty to CDM pass counts when the dominant team is still retaining comfortable possession — the passes still happen, they are just slower-paced and more circular. A CDM averaging 55 passes/game can easily hit 75-85 in this scenario. This is the single biggest source of CDM pass prop errors.

MIDFIELDER ROLE CLASSIFICATION (applies to all CDM/DM/CM pass props):
Identify which role the player actually performs — the same position label covers wildly different volume profiles:
  • BALL-PLAYING PIVOT / REGISTA / DLP (e.g. Rodri, Busquets, Kroos, Thiago): These players ARE the possession system. Every recycling sequence goes through them. In dominant-possession scenarios, they accumulate 90-130+ passes. In chase/underdog mode, they remain the first option out of pressure. Pass volume scales strongly with team possession AND with match intensity — both possession dominance AND trailing scripts push this role's count UP. This role is the primary driver of the cross-team GK correlation below.
  • BOX-TO-BOX / MEZZALA (e.g. De Bruyne, Milinkovic-Savic, Kimmich): High volume but distributed — they move between areas. Still possession-sensitive but less extreme than the pivot.
  • DESTROYER / ANCHOR / HOLDING MID (e.g. Kanté, Henderson, Elneny): Lower pass volume because direct play bypasses them under pressure. In chase/underdog mode, team often switches to long balls, REDUCING this role's pass count relative to normal. Do NOT apply possession-dominant CDM logic to destroyers.
  • PRESS-FIRST CDM: Similar to destroyer. High energy, low distribution. Possession has less impact on their pass count.

CROSS-TEAM SCRIPT EFFECTS — CORRELATED, NOT INVERSE (critical for all positions):
  • When a ball-playing pivot (Rodri, Busquets) dominates possession for their team → the OPPONENT'S GOALKEEPER's pass attempts go UP simultaneously. They are CORRELATED, not inverse. The low-block pass-back loop, over-hit crosses, and goal kicks all create GK pass volume. Do NOT interpret a high-possession midfielder's volume as a negative signal for the opponent's GK.
  • A dominant home CDM recycling 100+ passes → opponent GK averages 60+ pass attempts from clearances/distributions under press. Both rise together.
  • Dominant fullback overlaps (many crosses) → opponent GK has more aerial collections → more distributions. Correlated UP.
  • When this player's team is the DOMINANT possession side: CBs and CDMs on the DEFENDING side will have more tackles/clearances (inverse props rise for them). But pass volume for the defending side's CDM goes DOWN (direct play bypasses them).

DEFENDER (passes, tackles, clearances):
- Ball-playing CBs in 55%+ possession teams easily hit 70-90 passes. The key variable is HOW the team builds — short from back (inflates defender passes) vs long-ball (suppresses). Tackles/clearances invert with possession: low possession = more defensive actions.

CRITICAL ACCURACY RULES:
- NEVER double-count minutes. A player averaging 43 passes in 26 minutes per game — the 43 IS their game output. Do NOT scale down.
- Match context OVERRIDES raw averages for pass-dependent props in high-possession scenarios.
- GOALKEEPER INVERTED RULE: Low possession = MORE GK passes. High possession = FEWER GK passes. An away GK holding a lead = maximum volume scenario.
- CROSS-TEAM CORRELATION RULE: When the opponent's CDM/DM dominates possession (65%+), the defending GK's passes go UP (correlated). They do NOT go down. The mechanism is: low-block → back-passes to GK + over-hit crosses + goal kicks. Never penalise a GK's projection because the opponent's midfielders are generating high pass volume — that IS the mechanism causing this GK's volume to rise.
- ROLE SENSITIVITY: Ball-playing pivots are 3× more sensitive to possession context than destroyers. Always identify the player's role before applying possession multipliers. Applying "CDM possession dominant" logic to a destroyer who plays direct-ball anchor is a common over-projection error.
- NEVER say "Bayesian" — always say "Reverse Formula".
- DIRECTION LOCK: Your analysis direction MUST match the [MATHEMATICAL ENGINE] verdict. If math says UNDER, write UNDER analysis. If math says OVER, write OVER analysis. This is non-negotiable.

CALIBRATION RULES:
- TIGHT EDGE: If projected value is within ±1.0 of the line, cap confidence at 60%.
- BINARY LINES (0.5): UNDER 0.5 confidence NEVER exceeds 55%.
- DEFENDER PASSES: Ball-playing CBs/LBs in possession teams hit 60-90+ per game routinely.

JSON: {"confidenceScore":0,"confidenceLevel":"","aiProjection":0,"sharpSummary":"","reasoning":"","scenarioAnalysis":"","keyEvidence":"","sensitivityTests":"","subRisk":"","gameFlowDynamics":"","uncertaintyNote":"","qualitySignal":"","keyFactors":[],"tacticalBreakdown":"","matchupOverview":{"homeTeam":"","awayTeam":"","favorite":"","moneyline":{"home":"","draw":"","away":""},"expectedPossession":{"home":0,"away":0},"expectedGameType":"","keyMatchupFactor":""},"bayesianMetrics":{"priorMean":0,"momentumEffect":0,"covariateAdjustment":0,"reversalFlag":"stable"},"scenarioProbabilities":{"best":0,"base":0,"worst":0},"probabilityCurve":[],"recentSamples":[],"player":{"id":0,"name":"","team":"","position":""},"opponent":"","propType":"","line":0,"confidenceInterval":[0,0],"tacticalAlerts":[]}"""

        # Build the data payload — use GPT summary as primary + Wave 2 deep data as supplement
        wave2_supplement = {}
        if player_game_logs:
            target_field_map = {
                "pass_attempts": "passes_total", "shots": "shots_total", "shots_on_target": "shots_on",
                "tackles": "tackles_total", "key_passes": "passes_key", "shots_assisted": "passes_key",
                "saves": "goals_saves", "goalie_saves": "goals_saves",
                "interceptions": "tackles_interceptions", "blocks": "tackles_blocks",
                "dribbles": "dribbles_attempts", "fouls_drawn": "fouls_drawn",
                "crosses": "passes_crosses", "clearances": "tackles_clearances",
                "goals": "goals_total", "assists": "goals_assists",
                "duels_won": "duels_won", "yellow_cards": "cards_yellow",
                "fouls_committed": "fouls_committed",
            }
            target_field = target_field_map.get(req.propType, "passes_total")
            values = [g.get(target_field) for g in player_game_logs if g.get(target_field) is not None]
            game_log_brief = []
            for g in player_game_logs:
                val = g.get(target_field)
                _form_str = f", {g['formation']}" if g.get("formation") else ""
                game_log_brief.append(f"{g.get('date','')[:10]} vs {g.get('opponent','')} ({g.get('venue','')}, {g.get('minutes',0)}min{_form_str}): {val}")
            wave2_supplement["playerGameLogs"] = {
                "games": game_log_brief,
                "rawAvg": round(sum(values) / len(values), 2) if values else 0,
                "homeAvg": round(sum(v for g, v in zip(player_game_logs, [g.get(target_field) for g in player_game_logs]) if g.get("venue") == "home" and v) / max(1, sum(1 for g in player_game_logs if g.get("venue") == "home" and g.get(target_field))), 2) if values else 0,
                "awayAvg": round(sum(v for g, v in zip(player_game_logs, [g.get(target_field) for g in player_game_logs]) if g.get("venue") == "away" and v) / max(1, sum(1 for g in player_game_logs if g.get("venue") == "away" and g.get(target_field))), 2) if values else 0,
                "sampleSize": len(values),
            }
            # Pre-compute OVER/UNDER hit rates from actual game logs
            if values and req.line:
                over_hits = sum(1 for v in values if v > req.line)
                under_hits = sum(1 for v in values if v < req.line)
                push_hits = len(values) - over_hits - under_hits
                over_pct = round(over_hits / len(values) * 100, 1)
                under_pct = round(under_hits / len(values) * 100, 1)
                wave2_supplement["playerGameLogs"]["hitRates"] = {
                    "overHits": over_hits, "underHits": under_hits, "pushHits": push_hits,
                    "overPct": over_pct, "underPct": under_pct, "total": len(values),
                    "summary": f"OVER {req.line} in {over_hits}/{len(values)} games ({over_pct}%), UNDER in {under_hits}/{len(values)} ({under_pct}%)"
                }
        if team_fixture_stats:
            wave2_supplement["teamMatchStats"] = team_fixture_stats
        if opponent_fixture_stats:
            wave2_supplement["opponentMatchStats"] = opponent_fixture_stats

        # GK PASS CONTEXT — injected for GK pass_attempts props
        gk_pass_context = ""
        _is_gk_for_passes = (
            req.propType in {"pass_attempts", "passes"}
            and (
                (specific_position or "").upper() in {"GK", "GOALKEEPER"}
                or (player_position or "").lower() in {"goalkeeper", "gk"}
            )
        )
        if _is_gk_for_passes and match_dominance:
            _gk_exp_poss  = match_dominance.get("expectedPoss", 50)
            _gk_team_avg  = match_dominance.get("teamSeasonAvg", 50)
            _gk_opp_poss  = match_dominance.get("oppExpectedPoss", 50)
            _gk_venue_lbl = "AWAY" if player_venue == "away" else "HOME"
            _gk_poss_gap  = round(_gk_exp_poss - _gk_team_avg, 1)
            if _gk_exp_poss < 45:
                _gk_scenario = "LOW POSSESSION — HIGH GK VOLUME RISK: Team expected to defend deep. Defenders will constantly recycle to the GK under pressure. Model RAISES projection for this scenario. Do NOT underestimate."
            elif _gk_exp_poss < 50:
                _gk_scenario = "SLIGHTLY LOW POSSESSION — moderate back-pass volume expected."
            elif _gk_exp_poss > 58:
                _gk_scenario = "HIGH POSSESSION — LOW GK VOLUME: Team controls the ball through midfield. Fewer back-passes to the GK. Model LOWERS projection for this scenario."
            else:
                _gk_scenario = "BALANCED POSSESSION — normal GK pass volume expected."
            # Blowout risk: if the GK's team is a heavy favourite, flag the
            # game-script risk that a large winning margin suppresses second-half
            # GK distribution. Defenders stop recycling and just clear it long
            # to kill the clock when up 3+. This is irreducible variance that the
            # model cannot project in advance — user must be aware of the risk.
            _gk_blowout_warning = ""
            try:
                _bk_odds = (odds or {}).get("bookmakerOdds", {})
                _pifh_gk = (odds or {}).get("playerIsHome", player_venue == "home")
                _team_win_odds = float(_bk_odds.get("homeWin" if _pifh_gk else "awayWin", 99))
                _opp_win_odds  = float(_bk_odds.get("awayWin" if _pifh_gk else "homeWin", 99))
                if _team_win_odds <= 1.50:
                    _gk_blowout_warning = (
                        f"\n⚠️ BLOWOUT RISK: {req.teamName} are heavy favourites ({_team_win_odds:.2f}). "
                        f"If they lead by 3+ goals, defenders stop recycling and the GK's second-half "
                        f"distribution collapses — actual passes can finish 30-40% below first-half pace. "
                        f"This is irreducible game-script variance. Flag this in your analysis."
                    )
                elif _opp_win_odds <= 1.50:
                    _gk_blowout_warning = (
                        f"\n⚠️ COMEBACK PRESSURE RISK: {req.opponentName} are heavy favourites ({_opp_win_odds:.2f}). "
                        f"If the opponent leads big, the GK's team may chase the game — more open play, "
                        f"fewer back-passes as defenders push forward. GK distribution can drop late."
                    )
            except Exception:
                pass

            # Determine cross-team correlation note for dominant-possession opponent
            _gk_cross_team_note = ""
            if _gk_opp_poss >= 62.0 and _gk_exp_poss < 40.0:
                _ct_severity_pct = round(min(100.0, (_gk_opp_poss - 62.0) / 15.0 * 100))
                _gk_cross_team_note = (
                    f"\n\n⚡ CROSS-TEAM CORRELATION ACTIVE — CORRELATED (NOT INVERSE):\n"
                    f"Opponent expected possession: {_gk_opp_poss}% — this is the 'Rodri Effect' scenario.\n"
                    f"When a dominant possession team (like Spain with Rodri) controls {_gk_opp_poss:.0f}% of the ball:\n"
                    f"  1. LOW-BLOCK PASS-BACK LOOP: {req.teamName} defenders are compressed deep → every ball won "
                    f"is recycled BACK to {req.playerName} under press (safe release = GK pass).\n"
                    f"  2. OVER-HIT CROSSES: {req.opponentName}'s high crossing volume leads to GK collections "
                    f"→ {req.playerName} must immediately distribute (= pass attempt).\n"
                    f"  3. GOAL KICKS: More opponent possession sequences = more shots/crosses = more goal kicks "
                    f"(each counts as a pass attempt).\n"
                    f"CRITICAL: {req.playerName}'s and {req.opponentName}'s ball-playing midfielder's pass totals "
                    f"RISE TOGETHER (correlated ↑↑), NOT inversely. Do not penalise {req.playerName}'s projection "
                    f"just because the opponent's midfielders have high pass volumes — that IS the mechanism driving "
                    f"this GK's volume up. Cross-team correlation severity: {_ct_severity_pct}%."
                )

            gk_pass_context = f"""
[GK PASS VOLUME CONTEXT — INVERTED POSSESSION MODEL]
{req.playerName} is a GOALKEEPER. Pass volume rules are INVERTED vs outfield players.
Venue: {_gk_venue_lbl} | Expected possession: {_gk_exp_poss}% (team season avg: {_gk_team_avg}%, gap: {_gk_poss_gap:+.1f}pp)
Opponent expected possession: {_gk_opp_poss}%
Scenario: {_gk_scenario}
KEY PRINCIPLE: A GK defending deep = maximum back-pass recycling. A GK on a dominant team = barely touched. This is the single most important factor for GK pass props.{_gk_cross_team_note}{_gk_blowout_warning}"""

            # ── Inject GK possession logic DIRECTLY into bayesian_prompt_anchor ──
            # The anchor sits immediately before the main prompt and is the AI's
            # primary reference for WHY the direction is what it is. Without this,
            # the AI applies outfield logic (high poss → more passes) to GK props.
            if bayesian_prompt_anchor:
                _gk_anchor_team = corrected_team_name or req.teamName
                if _gk_exp_poss > 55:
                    _gk_anchor_reason = (
                        f"{_gk_anchor_team} are the DOMINANT team at {_gk_exp_poss:.0f}% possession. "
                        f"BECAUSE they dominate, {req.playerName} barely receives back-passes — "
                        f"teammates circulate through midfield, rarely returning to the keeper. "
                        f"HIGH team possession = SUPPRESSED GK pass volume. This is why the verdict is UNDER."
                    )
                    _gk_forbidden = f"Do NOT say {_gk_anchor_team} struggle/fight for possession — they control {_gk_exp_poss:.0f}%."
                elif _gk_exp_poss < 45:
                    _gk_anchor_reason = (
                        f"{_gk_anchor_team} have only {_gk_exp_poss:.0f}% possession — they sit deep and defend. "
                        f"LOW team possession = constant back-pass recycling to the GK under pressure. "
                        f"Defenders use the keeper as a safe release repeatedly. "
                        f"LOW team possession = RAISED GK pass volume. This is why the verdict is OVER."
                    )
                    _gk_forbidden = f"Do NOT say {_gk_anchor_team} dominate — they have only {_gk_exp_poss:.0f}% possession."
                else:
                    _gk_anchor_reason = (
                        f"{_gk_anchor_team} have {_gk_exp_poss:.0f}% possession — balanced match. "
                        f"GK inverted rule: moderate volume, close to season average expected."
                    )
                    _gk_forbidden = f"Do not exaggerate possession imbalance."
                bayesian_prompt_anchor += f"""
[GK PASS PROP — POSSESSION NARRATIVE RULE — MANDATORY — READ BEFORE WRITING]
GOALKEEPER PROP. Standard possession → pass-volume logic is INVERTED for keepers.
Possession: {_gk_anchor_team} = {_gk_exp_poss:.0f}% | {req.opponentName} = {_gk_opp_poss:.0f}%
{_gk_anchor_reason}
⛔ {_gk_forbidden}
⛔ Do NOT apply outfield logic ("high possession = more passes") to this GK prop.
⛔ Do NOT flip or swap the possession numbers. {_gk_anchor_team} = {_gk_exp_poss:.0f}%. {req.opponentName} = {_gk_opp_poss:.0f}%. <<<"""

        # SAVES-SPECIFIC: Elite GK Formula
        # Projected Saves = Opponent Avg SoT × GK Save% × Match Context Multiplier
        saves_context = ""
        gk_formula_data = None
        if req.propType == "saves":
            # 1. Opponent SoT per game (venue-filtered from fixture stats)
            opp_shots_list = []
            if opponent_fixture_stats:
                for mf in opponent_fixture_stats:
                    shots = mf.get("totalShots")
                    shots_on = mf.get("shotsOnTarget")
                    if shots is not None:
                        opp_shots_list.append({"total": shots, "on_target": shots_on or 0, "date": mf.get("date", ""), "venue": mf.get("venue", "")})
            opp_avg_shots = round(sum(s["total"] for s in opp_shots_list) / len(opp_shots_list), 1) if opp_shots_list else 0
            opp_avg_sot = round(sum(s["on_target"] for s in opp_shots_list) / len(opp_shots_list), 1) if opp_shots_list else 0

            # 2. GK save rate — prefer venue-specific logs (away GKs face more shots,
            # mixing home/away inflates the save-rate baseline in the wrong direction).
            gk_saves_list = []
            gk_ga_from_logs = []
            _saves_venue_logs = [g for g in player_game_logs if g.get("venue") == player_venue and g.get("goals_saves") is not None and (g.get("minutes") or 0) > 0]
            # Lower threshold to 3 for GK saves (same as Bayesian venue-split fix):
            # away GK save averages are radically different from home averages.
            # 3 venue-specific samples are enough to anchor the gk_avg_saves here.
            _saves_pool = _saves_venue_logs if len(_saves_venue_logs) >= 3 else player_game_logs
            recent_gk_logs = [g for g in _saves_pool if g.get("goals_saves") is not None and (g.get("minutes") or 0) > 0][:7]
            for g in recent_gk_logs:
                gk_saves_list.append(g.get("goals_saves"))
                # Compute GA directly from game score + venue (most reliable source)
                score = g.get("score", "")
                venue = g.get("venue", "")
                try:
                    parts = score.split("-")
                    home_goals = int(parts[0].strip())
                    away_goals = int(parts[1].strip())
                    ga_this_game = away_goals if venue == "home" else home_goals
                    gk_ga_from_logs.append(ga_this_game)
                except Exception:
                    pass
            gk_avg_saves = round(sum(gk_saves_list) / len(gk_saves_list), 2) if gk_saves_list else 0
            gk_saves_per90 = round(sum(gk_saves_list) / max(1, sum((g.get("minutes") or 0) for g in recent_gk_logs)) * 90, 2) if gk_saves_list else 0

            # Goals against: prefer game-log-derived, fallback to team stats
            total_saves = sum(gk_saves_list) if gk_saves_list else 0
            games_with_saves = len(gk_saves_list)
            total_ga_from_logs = sum(gk_ga_from_logs) if gk_ga_from_logs else 0
            goals_against = round(total_ga_from_logs / len(gk_ga_from_logs), 2) if gk_ga_from_logs else None

            # Fallback to team stats if game logs didn't yield GA
            if goals_against is None and team_stats:
                ga = team_stats.get("goals", {}).get("against", {})
                if ga:
                    ga_total = ga.get("total", {})
                    if isinstance(ga_total, dict):
                        total_ga = ga_total.get(player_venue) or ga_total.get("total") or 0
                    else:
                        total_ga = ga_total or 0
                    played_data = team_stats.get("fixtures", {}).get("played", {})
                    if isinstance(played_data, dict):
                        played = played_data.get(player_venue) or played_data.get("total") or 1
                    else:
                        played = played_data or 1
                    goals_against = round(total_ga / max(played, 1), 2) if total_ga else None

            # Save % = saves / (saves + goals conceded)
            if total_saves > 0 and total_ga_from_logs > 0:
                est_sot_faced = total_saves + total_ga_from_logs
                gk_save_pct = round((total_saves / max(est_sot_faced, 1)) * 100, 1)
            elif total_saves > 0 and goals_against is not None and games_with_saves > 0:
                est_sot_faced = total_saves + (goals_against * games_with_saves)
                gk_save_pct = round((total_saves / max(est_sot_faced, 1)) * 100, 1)
            elif total_saves > 0:
                # Fallback: assume 1.3 GA/game (league average)
                gk_save_pct = round(min(80, (total_saves / max(total_saves + games_with_saves * 1.3, 1)) * 100), 1)
            else:
                gk_save_pct = 65.0  # Conservative league average fallback
            # Cap save rate at realistic bounds
            gk_save_pct = min(80.0, max(50.0, gk_save_pct))

            # 3. Match context multiplier (symmetric adjustments)
            context_multiplier = 1.0
            context_factors = []
            if match_odds and match_odds.get("favorite"):
                fav = match_odds["favorite"]
                if fav == player_venue:
                    context_multiplier -= 0.10
                    context_factors.append(f"Team favored ({fav}) → -10% (fewer opponent shots)")
                else:
                    context_multiplier += 0.07
                    context_factors.append("Team underdog → +7% (more opponent shots)")

            # POSSESSION DOMINANCE PENALTY for saves
            # When the GK's team dominates possession, opponents have less ball
            # → fewer shots on target → fewer saves. Ann-Katrin Berger (62% poss,
            # won 1-0) projected OVER 2 saves but actual was 1 — classic dominance miss.
            if match_dominance and isinstance(match_dominance, dict):
                _saves_exp_poss = match_dominance.get("expectedPoss")
                _saves_avg_poss = match_dominance.get("teamSeasonAvg")
                if (_saves_exp_poss and _saves_avg_poss and _saves_avg_poss > 0):
                    _saves_poss_ratio = _saves_exp_poss / _saves_avg_poss
                    if _saves_poss_ratio > 1.08:
                        # Team significantly more dominant than usual → opponent barely touches ball
                        _poss_save_penalty = min(0.20, (_saves_poss_ratio - 1.0) * 1.0)
                        context_multiplier = round(context_multiplier * (1.0 - _poss_save_penalty), 2)
                        context_factors.append(
                            f"Possession dominance ({_saves_exp_poss:.0f}% vs {_saves_avg_poss:.0f}% avg) "
                            f"→ -{_poss_save_penalty*100:.0f}% saves (opponent less ball)"
                        )

            context_multiplier = round(context_multiplier, 2)

            # 4. THE FORMULA: Projected Saves = Opp Avg SoT × GK Save% × Context
            # Weighted blend: 40% formula (match-specific) + 60% GK average (form).
            # Saves is a high-variance stat — individual-game SOT fluctuates sharply
            # even when a team's season average looks high. Anchoring more heavily to
            # the GK's own recent save average reduces formula-driven over-projection
            # in cagey or low-tempo matchups.
            raw_formula = round(opp_avg_sot * (gk_save_pct / 100) * context_multiplier, 1) if opp_avg_sot > 0 else gk_avg_saves
            if gk_avg_saves > 0 and raw_formula > 0:
                projected_saves = round(raw_formula * 0.4 + gk_avg_saves * 0.6, 1)
            else:
                projected_saves = raw_formula if raw_formula > 0 else gk_avg_saves

            gk_formula_data = {
                "opponentAvgShots": opp_avg_shots,
                "opponentAvgSOT": opp_avg_sot,
                "opponentVenue": opponent_venue.upper(),
                "opponentShotsSample": len(opp_shots_list),
                "gkSaveRate": gk_save_pct,
                "gkAvgSaves": gk_avg_saves,
                "gkSavesPer90": gk_saves_per90,
                "gkSampleSize": games_with_saves,
                "goalsAgainstPerGame": goals_against,
                "contextMultiplier": context_multiplier,
                "contextFactors": context_factors,
                "formulaProjection": projected_saves,
                "formula": f"{opp_avg_sot} SoT × {gk_save_pct}% save rate × {context_multiplier} context → {raw_formula} formula (40%) + {gk_avg_saves} avg (60%) = {projected_saves}",
            }
            wave2_supplement["savesAnalysis"] = gk_formula_data

            saves_context = f"""
[ELITE GK SAVES FORMULA]
FORMULA: Projected Saves = Opponent Avg SoT × GK Save% × Match Context Multiplier

1. OPPONENT SHOTS ON TARGET ({opponent_venue.upper()} venue, last {len(opp_shots_list)} games):
   - Avg total shots/game: {opp_avg_shots}
   - Avg shots on TARGET/game: {opp_avg_sot}

2. GK SAVE RATE (last {games_with_saves} games):
   - Avg saves/game: {gk_avg_saves}
   - Saves per 90: {gk_saves_per90}
   - Estimated save %: {gk_save_pct}%
   - Team goals against/game ({player_venue}): {goals_against or 'N/A'}

3. MATCH CONTEXT MULTIPLIER: {context_multiplier}
   {chr(10).join('   - ' + f for f in context_factors) if context_factors else '   - Neutral'}

4. FORMULA RESULT: {opp_avg_sot} × {gk_save_pct}% × {context_multiplier} = {raw_formula} (blended with {gk_avg_saves} avg → {projected_saves})

COMPARE TO LINE: Line is {req.line}. Formula projects {projected_saves}.
{'LEAN OVER' if projected_saves > req.line else 'LEAN UNDER' if projected_saves < req.line else 'PUSH ZONE'} — but weight scenarios (blowout, cagey game, etc.)
"""

        # POSITION COMPARISON: Fetch same-position players vs opponent (run after player_position resolved)
        position_comparison = []
        try:
            position_comparison = await aio.wait_for(
                fetch_position_comparison(
                    opponent_fixture_list, player_position, req.propType, req.opponentId,
                    player_venue, 10, target_specific_pos=specific_position
                ) if player_position else _empty_list(),
                timeout=10
            )
        except Exception as e:
            print(f"[POS COMP] Error/timeout: {e}")

        # ── COMPARISON ENRICHMENT: Add season save rate (GK) or venue pass avg to each player ──
        if position_comparison:
            _enrich_prop = req.propType

            async def _fetch_comp_player_stats(p_entry):
                """Enrich one comparison player with save rate (GK) or season avg passes."""
                _pid = p_entry.get("playerId")

                # ── SAVES: compute per-game save rate from fixture data — no API call needed.
                # API-Football does NOT return goalkeeper.saves in season stats for many leagues.
                # Per-game rate (saves vs this opponent) is directly available and highly relevant.
                if _enrich_prop == "saves":
                    _gc = p_entry.get("goalsConceded")
                    _sv = p_entry.get("statValue", 0)
                    if _gc is not None and (_sv + _gc) > 0:
                        p_entry["saveRate"] = round(_sv / (_sv + _gc) * 100, 1)
                    return  # no API call needed for saves

                # ── PASSES: fetch season stats for avg passes per game
                if _enrich_prop not in {"pass_attempts", "passes", "key_passes", "crosses"}:
                    return
                if not _pid:
                    return
                _enrich_lid = req.leagueId or league_id or 39
                # Fetch both seasons in parallel and use whichever returns data
                async def _try_season(_s):
                    try:
                        return await aio.wait_for(
                            api_football_request("players", {"id": _pid, "season": _s, "league": _enrich_lid}),
                            timeout=5
                        )
                    except Exception:
                        return None
                try:
                    _results = await aio.wait_for(
                        aio.gather(_try_season(CURRENT_SEASON), _try_season(CURRENT_SEASON - 1)),
                        timeout=6
                    )
                    _sdata = next((r for r in _results if r), None)
                    if not _sdata:
                        return
                    _stats = (_sdata[0].get("statistics") or [{}])[0]
                    _apps       = (_stats.get("games") or {}).get("appearences") or 0
                    _pass_total = (_stats.get("passes") or {}).get("total") or 0
                    if _apps > 0 and _pass_total > 0:
                        p_entry["seasonAvgStat"] = round(_pass_total / _apps, 1)
                except Exception as _e:
                    print(f"[POS ENRICH] {p_entry.get('name')} pass avg skip: {type(_e).__name__}: {str(_e)[:80]}")

            # Run enrichment for all comparison players in parallel
            _enrich_tasks = [_fetch_comp_player_stats(p) for p in position_comparison]
            try:
                await aio.wait_for(aio.gather(*_enrich_tasks, return_exceptions=True), timeout=8)
                _enriched = sum(1 for p in position_comparison if p.get("saveRate") or p.get("seasonAvgStat"))
                if _enriched:
                    print(f"[POS ENRICH] Enriched {_enriched}/{len(position_comparison)} comparison players for {req.propType}")
            except Exception as _ee:
                print(f"[POS ENRICH] Batch timeout/error: {_ee}")

        # ── CATEGORY SAFETY VALVE ──────────────────────────────────────────────
        # Hard override: API-Football generic category is the ground truth.  If a
        # stale/wrong position cache resolved an attacking role for a player the
        # API categorises as "Defender", silently correct it here so the AI
        # narrative NEVER says "playing as a Poacher" for a centre-back.
        _ATTACKING_ROLES = {
            "Poacher", "Target Man", "False 9", "Shadow Striker",
            "Complete Forward", "Pressing Forward",
        }
        _ATTACKER_POSITIONS = {"ST", "CF", "SS"}
        if player_position == "Defender" and (
            player_role in _ATTACKING_ROLES or specific_position in _ATTACKER_POSITIONS
        ):
            print(
                f"[SAFETY VALVE] Defender {req.playerName} had attacking "
                f"pos={specific_position}/role={player_role} — correcting to CB/Stopper"
            )
            specific_position = specific_position if specific_position not in _ATTACKER_POSITIONS else "CB"
            player_role = "Stopper"
            display_position = specific_position
            display_role = player_role
            # Also correct the cached entry so this doesn't repeat
            try:
                await db.player_positions.update_one(
                    {"playerId": req.playerId},
                    {"$set": {"specificPosition": specific_position, "role": player_role}},
                )
            except Exception:
                pass

        # POSITION CONTEXT: Compute position-specific baseline from game logs + comparison
        position_context = ""
        position_comp_data = None
        if display_position:
            pos_map = {"Goalkeeper": "GK", "Defender": "DEF", "Midfielder": "MID", "Attacker": "FWD"}
            pos_short = specific_position if specific_position else pos_map.get(player_position, player_position)
            position_context = f"\n[PLAYER POSITION] {req.playerName} plays as {pos_short}"
            if player_role:
                position_context += f" — Role: {player_role}"
            if specific_position and player_position:
                position_context += f" (API category: {player_position})"
            if position_comparison:
                comp_values = [p["statValue"] for p in position_comparison]
                comp_per90 = [p["per90"] for p in position_comparison if p.get("per90")]
                comp_poss = [p["teamPossession"] for p in position_comparison if p.get("teamPossession")]
                comp_avg = round(sum(comp_values) / len(comp_values), 2) if comp_values else 0
                comp_per90_avg = round(sum(comp_per90) / len(comp_per90), 2) if comp_per90 else 0
                comp_poss_avg = round(sum(comp_poss) / len(comp_poss), 1) if comp_poss else None
                comp_lines = []
                for p in position_comparison[:7]:
                    p_pos_label = f"{p.get('position', '?')}"
                    if p.get('role'):
                        p_pos_label += f" ({p['role']})"
                    poss_str = f" | team poss: {p['teamPossession']}%" if p.get('teamPossession') else ""
                    comp_lines.append(f"  {p['name']} [{p_pos_label}] ({p['team']}, {p.get('venue','').upper()}) — {p['statValue']} {req.propType} in {p['minutes']}min (per90: {p['per90']}) | {p['date']} | rating: {p.get('rating', 'N/A')}{poss_str}")
                venue_note = f"All comparisons are {player_venue.upper()} performances only."
                poss_note = f"\nAverage team possession in these matches: {comp_poss_avg}%" if comp_poss_avg else ""
                position_context += f"""
[POSITION COMPARISON — {pos_short}s vs {req.opponentName} ({player_venue.upper()} only)]
{req.playerName} is a {pos_short}{f' ({player_role})' if player_role else ''}. {venue_note}
Below are other {player_position}s who played {player_venue.upper()} against {req.opponentName} recently:
{chr(10).join(comp_lines)}
Average {req.propType}: {comp_avg} | Per-90 avg: {comp_per90_avg} | Sample: {len(comp_values)} players{poss_note}
>>> Compare {req.playerName}'s projected {req.propType} against this positional baseline.
>>> Factor in possession context: teams with more possession tend to have more passing/creative stats; teams with less tend to have more defensive/counter-attacking stats.
>>> Consider {req.playerName}'s team expected possession profile vs the opponent. <<<"""
                position_comp_data = {
                    "position": display_position,
                    "positionShort": pos_short,
                    "players": position_comparison,
                    "avgStatValue": comp_avg,
                    "avgPer90": comp_per90_avg,
                    "avgPossession": comp_poss_avg,
                    "sampleSize": len(comp_values),
                    "propType": req.propType,
                    "opponent": req.opponentName,
                    "venue": player_venue,
                }

        # ── First-Goal Profile (both teams, concurrent) ──────────────────────────
        _fg_team: dict = {}
        _fg_opp:  dict = {}
        _fg_scenario_weights: dict = {}
        if not ai_only_mode and actual_team_id and req.opponentId and not _is_bdl_league:
            try:
                from first_goal_engine import get_first_goal_profile, compute_scenario_weights as _fg_sw
                _fg_season = 2025
                _fg_results = await aio.gather(
                    get_first_goal_profile(actual_team_id, _fg_season, api_football_request, db),
                    get_first_goal_profile(req.opponentId,  _fg_season, api_football_request, db),
                    return_exceptions=True,
                )
                _fg_team = _fg_results[0] if not isinstance(_fg_results[0], Exception) else {}
                _fg_opp  = _fg_results[1] if not isinstance(_fg_results[1], Exception) else {}
                if _fg_team.get("available"):
                    _fg_scenario_weights = _fg_sw(_fg_team, req.propType)
                    print(f"[FIRST GOAL] {req.playerName}: teamFirst={_fg_team.get('teamScoredFirstPct'):.0%} oppFirst={_fg_team.get('opponentScoredFirstPct'):.0%} n={_fg_team.get('dataPoints')}")
            except Exception as _fge:
                print(f"[FIRST GOAL] engine failed: {_fge}")

        # Compose data for AI prediction
        final_data_parts = []
        if ai_digest:
            final_data_parts.append(f"[AI INTEL BRIEF]\n{ai_digest}")
        if data_digest:
            final_data_parts.append(f"[DATA DIGEST]\n{data_digest}")
        if wave2_supplement:
            final_data_parts.append(f"[GAME LOGS]\n{json.dumps(wave2_supplement, default=str)[:5000]}")

        if _fg_team.get("available"):
            _fg_prompt_block = (
                f"[FIRST GOAL PROFILE — last {_fg_team.get('dataPoints', 0)} matches]\n"
                f"Team ({corrected_team_name}) scored first: {round(_fg_team.get('teamScoredFirstPct', 0) * 100)}% of games\n"
                f"Opponent ({req.opponentName}) scored first: {round(_fg_team.get('opponentScoredFirstPct', 0) * 100)}% of games\n"
                f"No goal / goalless half: {round(_fg_team.get('noGoalPct', 0) * 100)}% of games\n"
                f"Avg first-goal minute: {_fg_team.get('avgFirstGoalMin', 35)}\n"
                f"Math-derived scenario weights → best: {round(_fg_scenario_weights.get('best', 0.40) * 100)}% / "
                f"base: {round(_fg_scenario_weights.get('base', 0.35) * 100)}% / "
                f"worst: {round(_fg_scenario_weights.get('worst', 0.25) * 100)}%\n"
                f">>> Use these rates to anchor scenarioProbabilities in your JSON. They are real data, not estimates. <<<"
            )
            if _fg_opp.get("available"):
                _fg_prompt_block += (
                    f"\nOpponent ({req.opponentName}) first-goal profile (their own recent matches): "
                    f"scored first {round(_fg_opp.get('teamScoredFirstPct', 0) * 100)}% / "
                    f"conceded first {round(_fg_opp.get('opponentScoredFirstPct', 0) * 100)}%"
                )
            final_data_parts.append(_fg_prompt_block)

        # ── Script regime context block (all positions) ──────────────────────────
        # Builds a match-script situation summary so the AI can reason about
        # role-specific effects and cross-team correlations without having to
        # derive them from raw possession numbers alone.
        script_regime_context = ""
        try:
            _sr_own_poss = float(match_dominance.get("expectedPoss") or 50.0)
            _sr_opp_poss = float(match_dominance.get("oppExpectedPoss") or 50.0)
            _sr_pos_upper = (player_position or "").upper()
            _sr_role_lower = (player_role or "").lower()
            _sr_is_mid = _sr_pos_upper in {
                "CDM", "DM", "DMF", "CM", "MC", "CMF", "CAM", "AM", "OM", "ACM",
                "CM", "MIDFIELDER",
            }
            _sr_is_gk_pos = _sr_pos_upper in {"GK", "GKP", "GOALKEEPER", "KEEPER"}

            # Identify regime
            _dominant_team = None
            _sr_regime = "balanced"
            if _sr_own_poss >= 62.0 and _sr_opp_poss < 40.0:
                _dominant_team = corrected_team_name
                _sr_regime = "dominant"
            elif _sr_opp_poss >= 62.0 and _sr_own_poss < 40.0:
                _dominant_team = req.opponentName
                _sr_regime = "pinned"
            elif _sr_own_poss >= 57.0:
                _sr_regime = "slight_control"
            elif _sr_opp_poss >= 57.0:
                _sr_regime = "slight_pressure"

            # Role classification for midfielders
            _sr_is_pivot = any(r in _sr_role_lower for r in (
                "ball-playing", "ball playing", "regista", "deep-lying playmaker",
                "pivot", "playmaker", "half-back",
            ))
            _sr_is_destroyer = any(r in _sr_role_lower for r in (
                "destroyer", "anchor", "ball winner", "holding midfielder", "defensive midfielder",
            ))

            _sr_lines = []
            if _sr_regime == "dominant":
                _sr_lines.append(
                    f"SCRIPT REGIME: DOMINANT ({corrected_team_name} {_sr_own_poss:.0f}% vs "
                    f"{req.opponentName} {_sr_opp_poss:.0f}%)"
                )
                if _sr_is_mid:
                    if _sr_is_pivot:
                        _sr_lines.append(
                            f"Role effect: {req.playerName} is a BALL-PLAYING PIVOT — the possession recycling hub. "
                            f"Every sequence goes through them. Deep-block by {req.opponentName} creates endless "
                            f"short-cycle triangles ALL routed through this player. Volume is maximised."
                        )
                    elif _sr_is_destroyer:
                        _sr_lines.append(
                            f"Role effect: {req.playerName} is a DESTROYER/ANCHOR — team dominates through ball-playing "
                            f"CBs and wider midfielders. This player waits and breaks play up rather than circulating. "
                            f"Their pass volume does NOT scale as strongly with possession dominance as a pivot would."
                        )
                    else:
                        _sr_lines.append(
                            f"Match script: dominant possession for {corrected_team_name}. Midfielders circulate "
                            f"frequently. Volume scales positively with possession advantage."
                        )
                if _sr_is_gk_pos:
                    _sr_lines.append(
                        f"GK effect: Own team dominant ({_sr_own_poss:.0f}%) — GK is barely touched in build-up. "
                        f"Volume SUPPRESSED. BUT: check cross-team note if opponent possession is very high — "
                        f"in this dominant scenario opp poss is only {_sr_opp_poss:.0f}% (low) so suppression applies."
                    )
            elif _sr_regime == "pinned":
                _sr_lines.append(
                    f"SCRIPT REGIME: PINNED/LOW-BLOCK ({corrected_team_name} {_sr_own_poss:.0f}% vs "
                    f"{req.opponentName} {_sr_opp_poss:.0f}% dominant)"
                )
                if _sr_is_mid:
                    if _sr_is_pivot:
                        _sr_lines.append(
                            f"Role effect: {req.playerName} is a BALL-PLAYING PIVOT facing a dominant opponent. "
                            f"Their team plays direct/reactive rather than building. Volume is REDUCED — even pivots "
                            f"can't circulate much when the team is permanently pinned deep and playing long."
                        )
                    elif _sr_is_destroyer:
                        _sr_lines.append(
                            f"Role effect: {req.playerName} is a DESTROYER/ANCHOR. In pinned-back scripts, their "
                            f"team plays direct — BYPASSING this player. Pass volume likely BELOW season average."
                        )
                if _sr_is_gk_pos:
                    _sr_lines.append(
                        f"⚡ GK CROSS-TEAM CORRELATION: {req.opponentName} controls {_sr_opp_poss:.0f}% possession. "
                        f"{req.playerName}'s pass attempts go UP — low-block defence forces constant back-passes to GK, "
                        f"over-hit crosses create collections, and frequent goal kicks all count as pass attempts. "
                        f"The opponent's high-possession MID and this GK's pass volume are CORRELATED ↑↑, not inverse."
                    )
            elif _sr_regime in ("slight_control", "slight_pressure"):
                _lbl = "SLIGHT POSSESSION CONTROL" if _sr_regime == "slight_control" else "SLIGHT POSSESSION PRESSURE"
                _sr_lines.append(
                    f"SCRIPT REGIME: {_lbl} ({corrected_team_name} {_sr_own_poss:.0f}% vs "
                    f"{req.opponentName} {_sr_opp_poss:.0f}%)"
                )
                if _sr_is_mid:
                    _sr_lines.append(
                        "Mild possession edge — role classification matters most here. "
                        "Ball-playing pivots still see meaningful volume boost; destroyers see minimal change."
                    )

            if _sr_lines:
                script_regime_context = "[MATCH SCRIPT REGIME & CROSS-TEAM EFFECTS]\n" + "\n".join(_sr_lines)
        except Exception as _sre:
            print(f"[SCRIPT REGIME] context build error: {_sre}")

        if final_data_parts:
            final_data = "\n\n".join(final_data_parts)[:10000]
            if saves_context:
                final_data += f"\n\n{saves_context}"
            if gk_pass_context:
                final_data += f"\n\n{gk_pass_context}"
            if script_regime_context:
                final_data += f"\n\n{script_regime_context}"
            # NOTE: position_context is injected separately in the prompt (never truncated)
        else:
            final_data = json.dumps(historical_data, default=str)[:8000]

        # =============================================
        # MATCH DOMINANCE CONTEXT — kept as separate prompt block (not inside final_data)
        # =============================================
        dom_context = ""
        if match_dominance.get("expectedPoss", 50) != 50 or match_dominance.get("notes"):
            dom_notes = "\n".join(f"  - {n}" for n in match_dominance.get("notes", []))
            _ep = match_dominance['expectedPoss']
            _op = match_dominance['oppExpectedPoss']
            if _is_gk_for_passes:
                # ── GK pass prop: inverted possession logic ──────────────────────────
                # The generic DLP/CM/CAM outfield instruction MUST NOT appear here;
                # it causes the AI to flip possession numbers to reconcile UNDER with
                # high-possession scenarios ("team must have 35% because it's UNDER").
                _gk_dom_note = (
                    f">>> ⚠️  GOALKEEPER PASS PROP — INVERTED POSSESSION RULE — READ CAREFULLY:\n"
                    f">>> {corrected_team_name} have {_ep}% expected possession. {req.opponentName} have {_op}%.\n"
                    f">>> DO NOT FLIP THESE NUMBERS. {corrected_team_name} = {_ep}%. {req.opponentName} = {_op}%.\n"
                    f">>> For a GK, HIGH team possession ({_ep}%) = FEWER passes. The team recycles through\n"
                    f">>>   midfield, rarely needing to go back to the keeper.\n"
                    f">>> The UNDER is predicted BECAUSE {corrected_team_name} dominates — NOT because they struggle.\n"
                    f">>> CORRECT narrative: '{corrected_team_name} control {_ep}% possession, keeping the ball\n"
                    f">>>   through midfield and rarely needing back-passes to the GK — suppressing his volume.'\n"
                    f">>> FORBIDDEN: Do NOT say '{corrected_team_name} struggle/fight for possession' or assign\n"
                    f">>>   {_op}% to {corrected_team_name}. That number belongs to {req.opponentName}. <<<"
                )
                dom_context = f"""
[MATCH DOMINANCE ANALYSIS — DO NOT IGNORE]
Expected possession for {corrected_team_name}: {_ep}% (season avg: {match_dominance.get('teamSeasonAvg', '?')}%)
Expected possession for {req.opponentName}: {_op}% (season avg: {match_dominance.get('oppSeasonAvg', '?')}%)
{_gk_dom_note}"""
            else:
                dom_context = f"""
[MATCH DOMINANCE ANALYSIS — DO NOT IGNORE]
Expected possession for {corrected_team_name}: {_ep}% (season avg: {match_dominance.get('teamSeasonAvg', '?')}%)
Expected possession for {req.opponentName}: {_op}% (season avg: {match_dominance.get('oppSeasonAvg', '?')}%)
>>> CRITICAL: The two possession numbers above are FINAL. Do NOT derive team names from any game-log opponent abbreviations (e.g. TOR, CIN, PHI) — those are historical matches, not this fixture.
>>> {corrected_team_name} = {_ep}% | {req.opponentName} = {_op}%. These labels are authoritative. Never swap or reassign them.
>>> If expected possession is HIGHER than season average, pass-dependent players (DLP, CM, CAM) WILL exceed their historical averages.
>>> A deep-lying playmaker on a team expected at 65%+ possession will have significantly MORE pass attempts than their season average suggests.
>>> Conversely, defenders on low-possession teams will have MORE tackles/interceptions than average.
>>> DO NOT just project from historical averages when match context predicts a clear possession advantage or disadvantage.
>>> NARRATIVE ALIGNMENT: Your `keyMatchupFactor` and `gameFlowDynamics` MUST match the computed possession numbers above. If {req.opponentName} has HIGHER expected possession, say they control possession — never claim {corrected_team_name} dominates possession if their number is lower. <<<"""

        # Build match context (round/stage, knockout detection)
        match_context = ""
        if match_odds:
            match_round = match_odds.get("matchRound", "")
            match_league_name = match_odds.get("matchLeague", "")
            match_date = match_odds.get("matchDate", "")
            if match_round or match_league_name:
                knockout_keywords = ["final", "quarter", "semi", "round of", "knockout", "elimination", "playoff"]
                is_knockout = any(kw in match_round.lower() for kw in knockout_keywords) if match_round else False
                match_context = f"\n[MATCH CONTEXT] {match_league_name} — {match_round}"
                if match_date:
                    match_context += f" | Date: {match_date[:10]}"
                if is_knockout:
                    match_context += (
                        "\n** KNOCKOUT/ELIMINATION MATCH — The mathematical engine has already "
                        "applied a ×1.10 extra-time probability uplift to all count-stat projections "
                        "(pass_attempts, shots, saves, etc.) because ~30% of knockout games go to "
                        "extra time (+30 min). Do NOT separately penalise count stats for 'caution' — "
                        "the ET adjustment is already baked in. Focus your tactical analysis on: "
                        "(1) which team is the effective favourite and how that shapes possession "
                        "dominance; (2) whether the player's role (ball-winner vs recycler) means "
                        "their volume rises or falls specifically in a knockout defensive setup; "
                        "(3) any confirmed lineup/injury intel that changes the base outlook. "
                        "If the match can still end in a draw after 90 min → ET → penalties, "
                        "surface that uncertainty in your uncertaintyNote.**"
                    )

        # ── SITUATION ENGINE CONTEXT BLOCK ─────────────────────────────────────
        _sit_context_block = game_situation.get("contextBlock", "")
        if _sit_context_block:
            match_context += f"\n\n{_sit_context_block}"

        # ── WEB INTELLIGENCE ────────────────────────────────────────────────────
        if web_intel:
            match_context += (
                f"\n\n[LIVE WEB INTELLIGENCE — Pre-match intel fetched in real-time TODAY]\n{web_intel}\n"
                f">>> CRITICAL INSTRUCTION: The web intelligence above is authoritative real-time data. "
                f"(1) Manager/coach names MUST come ONLY from this web intel or the LINEUP section — "
                f"DO NOT reference any coach or manager by name from your training data, as coaching staff "
                f"changes frequently and your training knowledge IS OUTDATED. "
                f"(2) If the web intel does not confirm the current manager's name, describe the team's "
                f"tactical approach WITHOUT naming the coach. "
                f"(3) Lineup, formation, and injury information from this section overrides all training knowledge. <<<"
            ) 

        # ── LINEUP / FORMATION CONTEXT — feeds real formation matchup into the AI write-up ──
        _pl = locals().get("_pitch_lineup") or {}
        if _pl.get("formation") or _pl.get("opponentFormation"):
            _pl_status_txt = "CONFIRMED" if _pl.get("status") == "confirmed" else "PROJECTED (based on last match, not yet officially confirmed)"
            _own_coach_txt = f", coach {_pl.get('coach')}" if _pl.get("coach") else ""
            _opp_coach_txt = f", coach {_pl.get('opponentCoach')}" if _pl.get("opponentCoach") else ""
            match_context += (
                f"\n\n[LINEUP — {_pl_status_txt}]\n"
                f"{corrected_team_name or req.teamName}: {_pl.get('formation') or 'unknown'} formation{_own_coach_txt}\n"
                f"{req.opponentName}: {_pl.get('opponentFormation') or 'unknown'} formation{_opp_coach_txt}\n"
                ">>> Write ONE short paragraph of tactical analysis grounded in this exact formation matchup "
                "(e.g. numerical overloads/underloads in specific zones, where the subject player's zone sits relative "
                "to the opponent's shape, how the opponent's setup should specifically help or hurt this prop). "
                "Do not give generic team-form commentary — reference the actual formations above. <<<"
            )
        if locals().get("_risk_signals", {}).get("note"):
            match_context += f"\n\n[VOLATILITY] {_risk_signals['note']}"

        _pp_ctx_dict: dict = {}

        # Inject hit rate context into prompt
        hit_rate_context = ""
        hit_rates = wave2_supplement.get("playerGameLogs", {}).get("hitRates")
        if hit_rates:
            hit_rate_context = f"""
[OVER/UNDER HIT RATE — CRITICAL DATA]
{hit_rates['summary']}
>>> If over-rate >= 65%, strongly lean OVER. If under-rate >= 65%, lean UNDER. If neither exceeds 60%, treat as close call — lower confidence. <<<"""

        # Team disambiguation notes — injected when similar-named clubs could be confused
        _TEAM_DISAMBIGUATION = {
            "los angeles fc": "LAFC (Los Angeles FC) — NOT LA Galaxy. These are two completely separate MLS clubs. Do NOT mention LA Galaxy.",
            "lafc": "LAFC (Los Angeles FC) — NOT LA Galaxy. These are two completely separate MLS clubs. Do NOT mention LA Galaxy.",
            "la galaxy": "LA Galaxy (Los Angeles Galaxy) — NOT LAFC. These are two completely separate MLS clubs. Do NOT mention LAFC.",
            "los angeles galaxy": "LA Galaxy (Los Angeles Galaxy) — NOT LAFC. These are two completely separate MLS clubs. Do NOT mention LAFC.",
            "new york city fc": "New York City FC (NYCFC) — NOT New York Red Bulls. Do NOT mention Red Bulls.",
            "new york red bulls": "New York Red Bulls — NOT NYCFC, NOT Toronto FC. This player's CURRENT club is New York Red Bulls. If you know this player from a previous club (e.g. Toronto FC), that is OUTDATED. They NOW play for New York Red Bulls. Do NOT mention Toronto FC, NYCFC, or any other club.",
            "toronto fc": "Toronto FC — Do NOT confuse players who formerly played here with current Toronto FC players. If this player is listed as playing for Toronto FC, that is their CURRENT club.",
        }
        _team_disambig = _TEAM_DISAMBIGUATION.get((corrected_team_name or "").lower().strip(), "")
        _disambig_note = f"\nTEAM DISAMBIGUATION: {_team_disambig}" if _team_disambig else ""

        # ── FORMATTED RECENT GAME LOG ──────────────────────────────────────────
        import re as _re_log
        _recent_log_str = ""
        _gl_data = wave2_supplement.get("playerGameLogs", {})
        _gl_games = _gl_data.get("games", [])
        if _gl_games:
            _fmt_games = []
            for _gs in _gl_games[-8:]:
                # raw format: "2025-03-15 vs Osasuna (away, 90min[, 4-2-3-1]): 43"
                _m = _re_log.match(
                    r"(\d{4}-(\d{2})-(\d{2})) vs (.+?) \((.+?), (\d+)min(?:, ([^)]+))?\): (.+)",
                    _gs,
                )
                if _m:
                    _date_lbl  = f"{int(_m.group(2))}/{int(_m.group(3))}"
                    _opp_lbl   = _m.group(4).strip()
                    _venue_lbl = _m.group(5).strip()
                    _min_lbl   = _m.group(6)
                    _form_lbl  = (_m.group(7) or "").strip()  # formation, optional
                    _val_lbl   = _m.group(8).strip()
                    _form_part = f", {_form_lbl}" if _form_lbl else ""
                    _fmt_games.append(
                        f"{_val_lbl} vs {_opp_lbl} ({_date_lbl}, {_min_lbl}min, {_venue_lbl}{_form_part})"
                    )
                else:
                    _fmt_games.append(_gs)
            _gl_raw_avg  = _gl_data.get("rawAvg", "?")
            _gl_home_avg = _gl_data.get("homeAvg", "?")
            _gl_away_avg = _gl_data.get("awayAvg", "?")
            _gl_sample   = _gl_data.get("sampleSize", len(_fmt_games))
            _recent_log_str = f"""
[PLAYER RECENT GAME LOG — {req.propType.upper()} — LAST {len(_fmt_games)} GAMES]
⚠️ These are {corrected_team_name}'s matches. All opponent names below are teams {corrected_team_name} played AGAINST — they are NOT this player's team.
{" | ".join(_fmt_games)}
Season avg: {_gl_raw_avg} | Home avg: {_gl_home_avg} | Away avg: {_gl_away_avg} | Sample: {_gl_sample} games
>>> CRITICAL INSTRUCTION: In your Analysis section you MUST reference each of these games by opponent name and exact number. For every high result AND every low result, explain the specific tactical reason WHY that number happened (opponent style, defensive shape, game state, possession context). Then identify which past game above is most tactically similar to today's opponent ({req.opponentName}) and explicitly name it as your anchor. <<<"""

        # ── SUPPRESSION / AMPLIFICATION CONTEXT ─────────────────────────────────
        # When the model's projection is significantly below the player's season avg
        # (UNDER call) or above it (OVER call), AI tends to anchor on the season avg
        # and argue the wrong direction.  Inject an explicit "here is the gap and why"
        # block so Gemini explains the suppression/amplification instead of fighting it.
        _suppression_context = ""
        if early_bayes and early_bayes.get("priorMean") and bayesian_prompt_anchor:
            _eb_prior = early_bayes["priorMean"]
            _eb_proj  = _pf_proj
            _eb_dir   = bdir
            _gap_from_avg = round(_eb_prior - _eb_proj, 1)   # + = UNDER scenario, - = OVER
            _gap_pct  = abs(_gap_from_avg) / max(_eb_prior, 1) * 100
            _venue_avg_label = "away avg" if player_venue == "away" else "home avg"
            _venue_avg_val   = _gl_away_avg if player_venue == "away" else _gl_home_avg

            if _eb_dir == "UNDER" and _gap_from_avg >= 8:
                # Model projects significantly BELOW season average — explain suppression
                _suppression_context = f"""
[MODEL SUPPRESSION SIGNAL — READ THIS BEFORE WRITING ANYTHING]
⚠️ The season average is {_eb_prior} but the Reverse Formula projects only {_eb_proj} — a suppression of {_gap_from_avg} passes ({_gap_pct:.0f}% below average). The {_venue_avg_label} is {_venue_avg_val}.
This means the model has found SPECIFIC MATCHUP-SUPPRESSION FACTORS that override the seasonal norm.
Your Analysis section MUST focus on: WHY does THIS specific opponent ({req.opponentName}) suppress this stat below the season average? Look at the game logs for games where the output was low — those opponents share traits with today's matchup. The HIGH games in the log are NOT the anchor — the LOW games that resemble today's opponent are the anchor.
Do NOT reference or anchor to the highest games in the log. The model's {_eb_proj} projection is correct — explain it by finding the tactical suppression factors, not by citing games where the output was high.
Suppression factors to explore: opponent defensive shape reducing ball access, specific pressing patterns, H2H history vs this opponent, positional opponent allowance ({req.opponentName}'s CDM/MF positional avg is likely below seasonal norm)."""

            elif _eb_dir == "OVER" and _gap_from_avg <= -8:
                # Model projects significantly ABOVE season average — explain amplification
                _gap_above = abs(_gap_from_avg)
                _suppression_context = f"""
[MODEL AMPLIFICATION SIGNAL — READ THIS BEFORE WRITING ANYTHING]
⚠️ The season average is {_eb_prior} but the Reverse Formula projects {_eb_proj} — an amplification of {_gap_above} above average ({_gap_pct:.0f}% above norm). The {_venue_avg_label} is {_venue_avg_val}.
This means the model has found SPECIFIC MATCHUP-AMPLIFICATION FACTORS that drive the output above the seasonal norm.
Your Analysis section MUST focus on: WHY does THIS specific opponent ({req.opponentName}) amplify this stat above the season average? Look at the game logs for the player's highest outputs — those opponents share traits with today's matchup. The LOW games are NOT the anchor.
Amplification factors to explore: opponent defensive passivity, possession dominance scenario, positional matchup that inflates volume."""

        _prop_display = req.propType

        # ── H2H Intelligence block for AI prompt ──────────────────────────────
        _h2h_snap = historical_data.get("h2hPlayerStats") or {}
        _h2h_prompt_block = ""
        if _h2h_snap.get("sampleSize", 0) > 0:
            _hb = "[HEAD-TO-HEAD INTELLIGENCE — MANDATORY REFERENCE]\n"
            if _h2h_snap.get("teamMeetings"):
                _hb += f"Total team meetings (last {H2H_HISTORY_SEASONS} seasons): {_h2h_snap['teamMeetings']}"
                if _h2h_snap.get("seasonsCovered"):
                    _hb += f" ({_h2h_snap['seasonsCovered']['range']})"
                _hb += "\n"
            _hb += f"Player appeared in {_h2h_snap['sampleSize']} of those games with minutes vs {req.opponentName}\n"
            if _h2h_snap.get("avgVsOpponent") is not None:
                _hb += (
                    f"Player H2H avg ({req.propType}): {_h2h_snap['avgVsOpponent']:.1f} "
                    f"(season avg is {wave2_supplement.get('playerGameLogs',{}).get('rawAvg','?')})\n"
                )
            if _h2h_snap.get("trendDirection") and _h2h_snap["trendDirection"] != "stable":
                _hb += f"H2H trend: {_h2h_snap['trendDirection'].upper()} over recent appearances\n"
            if _h2h_snap.get("venueHitRate") and _h2h_snap["venueHitRate"]["total"] >= 2:
                _vhr = _h2h_snap["venueHitRate"]
                _hb += (
                    f"At {_vhr['venue'].upper()} vs this opponent: {_vhr['hits']}/{_vhr['total']} times "
                    f"exceeded line {req.line} ({_vhr['pct']}% hit rate)\n"
                )
            _hb += ">>> MANDATORY: Your keyEvidence and matchup analysis MUST explicitly cite this H2H record. <<<\n"
            _h2h_prompt_block = _hb

        # ── Opponent Defensive Profile block for AI prompt ─────────────────────
        _opp_def_prompt_block = ""
        try:
            _pcd_pp = position_comp_data if isinstance(position_comp_data, dict) else {}
            _pcd_avg_pp = _pcd_pp.get("avgStatValue")
            if not _pcd_avg_pp:
                _pcd_raw_pp = [p.get("statValue") for p in (_pcd_pp.get("players") or []) if p.get("statValue") is not None]
                if _pcd_raw_pp:
                    _pcd_avg_pp = round(sum(_pcd_raw_pp) / len(_pcd_raw_pp), 1)
            _pcd_n_pp = int(_pcd_pp.get("sampleSize") or len(_pcd_pp.get("players") or []))
            if _pcd_avg_pp is not None and _pcd_n_pp >= 2:
                _odf_b = (
                    f"[OPPONENT DEFENSIVE PROFILE — {req.opponentName} vs {display_position or req.propType.replace('_',' ')}]\n"
                    f"{req.opponentName} allows {float(_pcd_avg_pp):.1f} {req.propType.replace('_',' ')} "
                    f"per game to {display_position or 'same-position'} players (n={_pcd_n_pp} fixtures)\n"
                )
                _ps_avg_pp = wave2_supplement.get("playerGameLogs", {}).get("rawAvg")
                if _ps_avg_pp and float(_ps_avg_pp) > 0:
                    _delta_pp = round((float(_pcd_avg_pp) / float(_ps_avg_pp) - 1) * 100, 1)
                    _dir_pp = "ABOVE" if _delta_pp > 0 else "BELOW"
                    _odf_b += f"That is {abs(_delta_pp):.1f}% {_dir_pp} this player's season average of {_ps_avg_pp}\n"
                    if abs(_delta_pp) >= 15:
                        _odf_b += (
                            f">>> STRONG {'FAVOURABLE' if _delta_pp > 0 else 'UNFAVOURABLE'} MATCHUP: "
                            f"cite this opponent allowance rate as a PRIMARY factor in keyEvidence <<<\n"
                        )
                    else:
                        _odf_b += ">>> Cite this opponent allowance data explicitly in your matchup analysis. <<<\n"
                _opp_def_prompt_block = _odf_b
        except Exception:
            pass

        # ── MANAGER CHANGE PROMPT BLOCK ──────────────────────────────────────────
        _manager_change_block = ""
        try:
            if _manager_ctx.get("isRecent") and _manager_ctx.get("coachStartDate"):
                _mc = _manager_ctx
                _ms = _manager_split_info if "_manager_split_info" in dir() else {}
                _mpd = _manager_possession_drift
                _mb  = (
                    f"\n\n[⚠ MANAGER CHANGE — CRITICAL SYSTEM CONTEXT — READ BEFORE ANALYSIS]\n"
                    f"CONFIRMED: {req.teamName} appointed {_mc.get('coachName','new manager')} "
                    f"{_mc.get('daysElapsed')} days ago (from {_mc.get('coachStartDate')}).\n"
                )
                if _mc.get("prevCoachName"):
                    _mb += f"Previous coach: {_mc['prevCoachName']}\n"
                if _ms.get("preAvg") is not None and _ms.get("postAvg") is not None:
                    _mb += (
                        f"Stat split ({req.propType}): "
                        f"pre-{_mc.get('coachName','new coach')} avg = {_ms['preAvg']} "
                        f"({_ms.get('preCount',0)} games)  →  "
                        f"post-{_mc.get('coachName','new coach')} avg = {_ms['postAvg']} "
                        f"({_ms.get('postCount',0)} games)\n"
                    )
                    if _ms.get("thinSample"):
                        _mb += (
                            f"⚠ THIN SAMPLE: Only {_ms.get('postCount',0)} games under "
                            f"{_mc.get('coachName','new coach')} — high uncertainty; lean on "
                            f"the post-change trend over the full history.\n"
                        )
                    else:
                        _mb += "✓ Model used ONLY post-change game logs for this projection.\n"
                if _mpd.get("isShift"):
                    _mb += (
                        f"Team possession drift: season avg {_mpd['seasonAvg']}% → "
                        f"last-5 avg {_mpd['last5Avg']}% ({_mpd['drift']:+.1f}pp) — "
                        f"TACTICAL IDENTITY SHIFT CONFIRMED.\n"
                    )
                _mb += (
                    ">>> MANDATORY: The manager change is the PRIMARY narrative driver for this "
                    "prediction. In keyEvidence and matchup analysis you MUST: "
                    "(1) explicitly name the new coach and state the tactical identity shift, "
                    "(2) reference the pre vs post statistical split above, "
                    "(3) discuss whether this player's role in the new system is a PRIMARY beneficiary "
                    "(higher volume) or secondary (lower volume), "
                    "(4) set your uncertaintyNote to reflect thin-sample risk if applicable. <<<"
                )
                _manager_change_block = _mb
        except Exception as _mcb_err:
            print(f"[MANAGER BLOCK] error: {_mcb_err}")

        prompt = f"""⛔⛔⛔ PLAYER IDENTITY — READ THIS FIRST — MANDATORY ⛔⛔⛔
Player name: {req.playerName}. Current team: {corrected_team_name}. Opponent today: {req.opponentName}.
RULES:
1. This player's team is {corrected_team_name}. Use ONLY this team name. Never say they play for Toronto FC, NYCFC, or any other club.
2. If your training data associates "{req.playerName}" with a different club, that information is OUTDATED. They NOW play for {corrected_team_name}.
3. Do NOT add, change, or combine the player's name. The name is exactly: {req.playerName}. Do NOT append another player's name to it.
4. Opponent abbreviations and names in game logs (e.g. TOR, CIN, PHI) are teams {corrected_team_name} played AGAINST — they are NOT this player's club.
⛔⛔⛔ END IDENTITY LOCK ⛔⛔⛔

{req.playerName} ({display_position}) — plays for {corrected_team_name} ({player_venue.upper()}) | OPPONENT: {req.opponentName} | {_prop_display} line {req.line}
IMPORTANT: This player's current CLUB is {corrected_team_name}. Do NOT reference any national team or previous club in your analysis — use only "{corrected_team_name}" when referring to this player's team.{_disambig_note}
Odds: {json.dumps(match_odds.get('bookmakerOdds',{}), default=str) if match_odds else 'N/A'}{match_context}
{pronoun_note}
{_recent_log_str}
{hit_rate_context}
{bayesian_prompt_anchor}
{_suppression_context}
{dom_context}
{position_context}
{_h2h_prompt_block}{_opp_def_prompt_block}{_manager_change_block}{final_data[:3500]}

Analyze ALL data thoroughly. Return JSON only."""

        async def call_gemini(label="grok-fallback", model=GROK_MODEL, prompt_override=None):
            """Gemini fallback — mirrors call_grok but as a named secondary attempt."""
            return await call_grok(label=label, model=model, prompt_override=prompt_override)

        # =============================================
        # AI SYNTHESIS: Gemini primary, secondary fallback
        # Projection comes ONLY from the math engine — AI projectedValue is NEVER used.
        # =============================================
        ai_result = None

        # pv is set from early_bayes here as a temporary anchor; real_bayes overwrites it later.
        pv = early_bayes["posteriorMean"] if early_bayes and early_bayes.get("posteriorMean") else req.line

        async def call_grok(label="ai", model=None, prompt_override=None):
            """Primary AI synthesis — Replit Gemini AI Integration."""
            from ai_engine import _ai_call as _engine_call
            import re as _re
            import html as _html
            try:
                text = await _engine_call(
                    prompt_override if prompt_override is not None else prompt,
                    system=PREDICTION_SYSTEM,
                    temperature=0.0,
                    max_tokens=4000,
                    timeout=45,
                    json_mode=False,
                )
                if not text:
                    return None
                text = _re.sub(r"```(?:json)?\s*", "", text)
                text = _re.sub(r"```\s*$", "", text, flags=_re.MULTILINE)
                text = _html.unescape(text).strip()
                start = text.find("{")
                if start >= 0:
                    candidate = text[start:]
                    try:
                        result = json.loads(candidate)
                        # Gemini occasionally returns string fields as nested dicts
                        _STR_FIELDS = ("tacticalBreakdown","sharpSummary","reasoning",
                                       "scenarioAnalysis","keyEvidence","sensitivityTests",
                                       "subRisk","gameFlowDynamics","uncertaintyNote",
                                       "confidenceLevel","recommendation")
                        for _sf in _STR_FIELDS:
                            if _sf in result and not isinstance(result[_sf], str):
                                _sf_val = result[_sf]
                                if _sf_val and isinstance(_sf_val, dict):
                                    if _sf == "tacticalBreakdown":
                                        # Format sections as readable markdown
                                        _parts = []
                                        for _k, _v in _sf_val.items():
                                            if isinstance(_v, str) and _v.strip():
                                                _parts.append(f"**{_k}**\n{_v.strip()}")
                                        result[_sf] = "\n\n".join(_parts) if _parts else ""
                                    else:
                                        # For sharpSummary/reasoning/etc, join string values
                                        _sv = [str(v) for v in _sf_val.values() if v and str(v).strip()]
                                        result[_sf] = " ".join(_sv) if _sv else ""
                                else:
                                    result[_sf] = str(_sf_val) if _sf_val else ""
                        result["_source"] = label
                        return result
                    except json.JSONDecodeError:
                        pass
                    for end_pos in range(len(text), start, -1):
                        if text[end_pos - 1] == "}":
                            try:
                                result = json.loads(text[start:end_pos])
                                result["_source"] = label
                                return result
                            except json.JSONDecodeError:
                                continue
                    _repaired: dict = {"_source": label, "_repaired": True}
                    for _key in ("sharpSummary", "tacticalBreakdown", "reasoning", "aiProjection",
                                 "confidenceScore", "confidenceLevel", "recommendation"):
                        _m = _re.search(rf'"{_key}"\s*:\s*"((?:[^"\\]|\\.)*)', text[start:])
                        if _m:
                            _repaired[_key] = _m.group(1)
                    if _repaired.get("tacticalBreakdown") or _repaired.get("sharpSummary"):
                        print(f"[MULTI-AI] {label} — JSON truncated, repaired: {list(_repaired.keys())}")
                        return _repaired
                print(f"[MULTI-AI] {label} non-JSON response: {text[:300]!r}")
                raise ValueError("No valid JSON in AI response")
            except Exception as e:
                print(f"[MULTI-AI] {label} failed: {e}")
                return None

        # AI is deliberately deferred until the final projection ledger exists.
        # Looking up or generating a narrative here would allow a stale/preflight
        # projection to be cached against the request.
        ai_result = None
        _pred_cached = None
        _ai_task = None
        print("[AI] Narrative deferred until final projection ledger is locked.")

        # If no cached AI and background task is running, seed with math-only result
        if not ai_result:
            if early_bayes and early_bayes.get("posteriorMean"):
                pv = early_bayes["posteriorMean"]
                _raw_bayes_conf = max(early_bayes.get("pOver", 50), early_bayes.get("pUnder", 50))
                _capped_conf = min(_raw_bayes_conf, 72)
                ai_result = {
                    "projectedValue": pv,
                    "recommendation": early_bayes.get("recommendation", "over"),
                    "confidenceScore": _capped_conf,
                    "reasoning": "",
                    "_source": "bayesian_async_pending",
                }
                print(f"[AI-ASYNC] Math-only seed while AI runs in background: {pv}")
            else:
                pv = req.line
                ai_result = {
                    "projectedValue": pv,
                    "recommendation": "over",
                    "confidenceScore": 50,
                    "reasoning": "",
                    "_source": "fallback_async_pending",
                }
                print(f"[AI-ASYNC] Fallback seed while AI runs: {pv}")
        else:
            # Cached AI available — use its source marker for timing
            pass

        source_model = ai_result.get("_source", "gemini")
        print(f"[TIMING] {source_model} ready: {_t.time()-_t0:.1f}s, proj={pv}")

        prediction = ai_result.copy()
        prediction.pop("_source", None)
        prediction["projectedValue"] = pv
        prediction["recommendation"] = "over" if pv > req.line else "under"
        prediction["sport"] = req.sport
        # Expose current opponent quality tier so the frontend can display it.
        # Standings-based rank only exists when the CURRENT prediction's league_id
        # has a domestic/qualifying-group table — this silently fails for
        # friendlies, intercontinental playoffs, and any match without a table
        # (the exact case that was hiding the "vs {opponent} [TIER]" badge).
        # Fall back, in order: (1) curated national-team tier by name,
        # (2) odds-implied opponent win probability — always available for any
        # match with a betting market, regardless of competition.
        _cur_opp_rank = (standing_data or {}).get("oppRank")
        if _cur_opp_rank is not None:
            prediction["currentOppRank"] = _cur_opp_rank
            if _cur_opp_rank <= 6:
                prediction["currentOppTier"] = "ELITE"
            elif _cur_opp_rank <= 15:
                prediction["currentOppTier"] = "STRONG"
            elif _cur_opp_rank <= 30:
                prediction["currentOppTier"] = "MID"
            else:
                prediction["currentOppTier"] = "WEAK"
        else:
            _opp_name_l = (req.opponentName or "").lower().strip()
            _nat_tier = NATIONAL_TEAM_TIER.get(_opp_name_l)
            if _nat_tier is None and _opp_name_l:
                _nat_tier = next(
                    (v for k, v in NATIONAL_TEAM_TIER.items() if _opp_name_l in k or k in _opp_name_l), None
                )
            if _nat_tier is not None:
                prediction["currentOppTier"] = _nat_tier
                prediction["currentOppTierSource"] = "nationalTeamTable"
            elif match_odds and match_odds.get("bookmakerOdds"):
                try:
                    _hw = float(match_odds["bookmakerOdds"].get("homeWin") or 0)
                    _aw = float(match_odds["bookmakerOdds"].get("awayWin") or 0)
                    if _hw > 1.0 and _aw > 1.0:
                        _p_home = 1.0 / _hw
                        _p_away = 1.0 / _aw
                        _total = _p_home + _p_away
                        _p_home_norm = _p_home / _total if _total > 0 else 0.5
                        _player_is_home = match_odds.get("playerIsHome")
                        _opp_win_prob = (1.0 - _p_home_norm) if _player_is_home else _p_home_norm
                        if _opp_win_prob >= 0.55:
                            prediction["currentOppTier"] = "ELITE"
                        elif _opp_win_prob >= 0.40:
                            prediction["currentOppTier"] = "STRONG"
                        elif _opp_win_prob >= 0.25:
                            prediction["currentOppTier"] = "MID"
                        else:
                            prediction["currentOppTier"] = "WEAK"
                        prediction["currentOppTierSource"] = "oddsImplied"
                except (TypeError, ValueError):
                    pass
        # Tell frontend AI text is loading in background
        prediction["aiPending"] = _ai_task is not None and not _pred_cached

        # scenarioProbabilities: prefer AI-assigned values; fall back to first-goal math
        _sp = prediction.get("scenarioProbabilities")
        if (not isinstance(_sp, dict) or
                not all(isinstance(_sp.get(k), (int, float)) for k in ("best", "base", "worst")) or
                sum(_sp.get(k, 0) for k in ("best", "base", "worst")) < 0.5):
            if _fg_scenario_weights:
                prediction["scenarioProbabilities"] = _fg_scenario_weights
        else:
            # Normalise AI's values (they may not sum to 1.0 exactly)
            _sp_total = sum(_sp[k] for k in ("best", "base", "worst"))
            if _sp_total > 0:
                prediction["scenarioProbabilities"] = {
                    k: round(_sp[k] / _sp_total, 3) for k in ("best", "base", "worst")
                }

        # Confidence normalization
        cs = prediction.get("confidenceScore", 50)
        if isinstance(cs, (int, float)):
            prediction["confidenceScore"] = round(cs * 100 if cs <= 1 else cs)
        else:
            prediction["confidenceScore"] = 50

        prediction["consensusNote"] = f"Reverse Formula projection. Tactical analysis powered by ReverseScan."
        prediction["modelBreakdown"] = [{
            "model": "ReverseScan",
            "recommendation": prediction["recommendation"],
            "projectedValue": pv,
            "confidenceScore": prediction["confidenceScore"],
        }]

        # Set confidence level
        cs = prediction.get("confidenceScore", 50)
        prediction["confidenceLevel"] = "Very High" if cs >= 80 else "High" if cs >= 70 else "Medium" if cs >= 55 else "Low"

        # Store dominance info — will be applied POST-FUSION to the final number
        _dom_avg_is_real = bool(match_dominance.get("seasonAvgIsReal"))
        prediction["matchDominance"] = {
            "applied": match_dominance["multiplier"] != 1.0,
            "multiplier": match_dominance["multiplier"],
            "expectedPoss": match_dominance["expectedPoss"],
            # Only expose teamSeasonAvg/oppSeasonAvg when they're real season
            # averages — the odds-only fallback hardcodes 50.0/50.0 with zero
            # real signal behind it, and showing that to the UI as a static
            # "season avg" badge is misleading (see possession-fallback-unknown-tier.md).
            "teamSeasonAvg": match_dominance.get("teamSeasonAvg") if _dom_avg_is_real else None,
            "oppSeasonAvg": match_dominance.get("oppSeasonAvg") if _dom_avg_is_real else None,
            "seasonAvgIsReal": _dom_avg_is_real,
            "notes": match_dominance["notes"],
        }

        # =============================================
        # BAYESIAN — Reuse early computation (already done before AI prompt)
        # =============================================
        real_bayes = early_bayes
        if real_bayes:
            prediction["bayesianMetrics"] = real_bayes
            prediction["confidenceInterval"] = real_bayes.get("confidenceInterval", prediction.get("confidenceInterval"))

        # Expose the key engine inputs the UI needs to show "Model Factors"
        prediction["matchFactors"] = {
            "expectedPoss":   match_dominance.get("expectedPoss"),
            "oppExpectedPoss":match_dominance.get("oppExpectedPoss"),
            "firstGoalProfile":     _fg_team if _fg_team.get("available") else None,
            "firstGoalOppProfile":  _fg_opp  if _fg_opp.get("available")  else None,
            "scenarioProbabilities": prediction.get("scenarioProbabilities") or _fg_scenario_weights or None,
            "h2hPossAvg":     match_dominance.get("h2hPossAvg"),
            "h2hPossCount":   match_dominance.get("h2hPossCount"),
            "possMultiplier": match_dominance.get("multiplier"),
            "matchStakes":    game_situation.get("matchStakes"),
            "bayesian": {
                "priorMean":     (real_bayes or {}).get("priorMean"),
                "posteriorMean": (real_bayes or {}).get("posteriorMean"),
                "priorSamples":  (real_bayes or {}).get("priorSamples"),
                "pOver":         (real_bayes or {}).get("pOver"),
                "pUnder":        (real_bayes or {}).get("pUnder"),
                "matchStakes":   (real_bayes or {}).get("matchStakes"),
                "cdmInversion":  (real_bayes or {}).get("cdmInversion"),
                "homeCdmDeepBlock": (real_bayes or {}).get("homeCdmDeepBlock"),
                "condPossAdj": locals().get("_cond_poss_result") and {
                    "basePoss":      locals()["_cond_poss_result"].get("base_poss"),
                    "adjustedPoss":  locals()["_cond_poss_result"].get("adjusted_poss"),
                    "deltaPP":       locals()["_cond_poss_result"].get("delta_pp"),
                    "trailScenario": locals()["_cond_poss_result"].get("trailing_scenario_poss"),
                    "leadScenario":  locals()["_cond_poss_result"].get("leading_scenario_poss"),
                    "pTrail":        locals()["_cond_poss_result"].get("p_trail"),
                    "pLead":         locals()["_cond_poss_result"].get("p_lead"),
                    "playerCede":    (locals()["_cond_poss_result"].get("player_style") or {}).get("possession_cede_when_leading"),
                    "playerChase":   (locals()["_cond_poss_result"].get("player_style") or {}).get("possession_chase_when_trailing"),
                    "oppCede":       (locals()["_cond_poss_result"].get("opp_style") or {}).get("possession_cede_when_leading"),
                    "oppStyleNotes": (locals()["_cond_poss_result"].get("opp_style") or {}).get("style_notes"),
                    "settledWinPoss": (locals()["_cond_poss_result"].get("player_settled") or {}).get("winning_poss"),
                    "settledLosePoss": (locals()["_cond_poss_result"].get("player_settled") or {}).get("losing_poss"),
                    "method":        locals()["_cond_poss_result"].get("method"),
                } or None,
                "leagueCalib":   (real_bayes or {}).get("leagueCalib"),
                "scenarioPriors":(real_bayes or {}).get("scenarioPriors"),
                "oppAllowedAvg": (real_bayes or {}).get("opponentAllowedAvg"),
                "oppAllowedN":   (real_bayes or {}).get("opponentAllowedSamples"),
                "oppAllowedWeight": (real_bayes or {}).get("opponentAllowedWeight"),
                "momentumLabel": (real_bayes or {}).get("momentumLabel"),
                "momentumEffect":(real_bayes or {}).get("momentumEffect"),
                "priorStd":      (real_bayes or {}).get("priorStd"),
                "pairShare":     (real_bayes or {}).get("pairShare"),
                "compSeasonAvg": (real_bayes or {}).get("compSeasonAvg"),
                "rawOppAllowedAvg": (real_bayes or {}).get("rawOppAllowedAvg"),
                "rotationRisk":  locals().get("_rotation_risk", "stable"),
                "rotationAdjPct": round(locals().get("_rotation_adj_pct", 0.0) * 100, 1),
                "expectedMinutes": round(locals().get("_exp_mins", 90.0), 1),
            },
        }

        # Mirror condPossAdj into bayesianMetrics so the mobile tactical-AI
        # prompt can find it at pred.bayesianMetrics.condPossAdj
        _cp_res = locals().get("_cond_poss_result")
        if _cp_res and prediction.get("bayesianMetrics") is not None:
            prediction["bayesianMetrics"]["condPossAdj"] = {
                "basePoss":      _cp_res.get("base_poss"),
                "adjustedPoss":  _cp_res.get("adjusted_poss"),
                "deltaPP":       _cp_res.get("delta_pp"),
                "trailingPoss":  _cp_res.get("trailing_scenario_poss"),
                "leadingPoss":   _cp_res.get("leading_scenario_poss"),
                "pTrail":        _cp_res.get("p_trail"),
                "pLead":         _cp_res.get("p_lead"),
                "playerCede":    (_cp_res.get("player_style") or {}).get("possession_cede_when_leading"),
                "playerChase":   (_cp_res.get("player_style") or {}).get("possession_chase_when_trailing"),
                "oppCede":       (_cp_res.get("opp_style") or {}).get("possession_cede_when_leading"),
                "oppCedeSrc":    _cp_res.get("method"),
                "oppStyleNotes": (_cp_res.get("opp_style") or {}).get("style_notes"),
                "signals":       _cp_res.get("signals"),
                "settledWinPoss":  (_cp_res.get("player_settled") or {}).get("winning_poss"),
                "settledLosePoss": (_cp_res.get("player_settled") or {}).get("losing_poss"),
            }

        # =============================================
        # =============================================
        # BAYESIAN-ONLY PROJECTION
        #
        # The math OWNS the number. Period.
        # Gemini provides tactical reasoning text only — no numeric influence.
        # The Bayesian posterior IS the projected value.
        # =============================================
        if real_bayes and real_bayes.get("priorSamples", 0) >= 3:
            bayesian_posterior = real_bayes["posteriorMean"]
            _record_projection_factor(
                "bayesian_engine",
                "Three-layer Bayesian engine",
                real_bayes.get("priorMean"),
                bayesian_posterior,
                inputs={
                    "priorMean": real_bayes.get("priorMean"),
                    "momentumMean": real_bayes.get("momentumMean"),
                    "momentumEffect": real_bayes.get("momentumEffect"),
                    "covariateAdjustment": real_bayes.get("covariateAdjustment"),
                    "priorSamples": real_bayes.get("priorSamples"),
                    "priorWeight": real_bayes.get("priorWeight"),
                    "momentumWeight": real_bayes.get("momentumWeight"),
                    "covariateWeight": real_bayes.get("covariateWeight"),
                },
                sample_size=real_bayes.get("priorSamples"),
                reason="Initial posterior after prior, recent-form momentum, and capped match covariates.",
            )

            # ─── OPPONENT H2H PRIOR ADJUSTMENT ────────────────────────────────────
            # Blend player's historical stats vs THIS specific opponent into the prior.
            # Captures opponent-specific patterns season averages can't see:
            # e.g., a player who averages 70 passes/game but only 55 vs this opponent.
            # Weight is proportional to H2H sample size, capped at 25% max influence —
            # season average always holds at least 75% authority.
            # Venue-filtered when enough same-venue H2H games exist (home vs home, away vs away).
            _h2h_summary = historical_data.get("h2hPlayerStats", {})
            _h2h_avg = _h2h_summary.get("avgVsOpponent")
            _h2h_n = _h2h_summary.get("sampleSize", 0)

            if _h2h_avg is not None and _h2h_n >= 2:
                # Prefer same-venue H2H data when available (>= 2 games at same venue)
                _venue_vals = [
                    s["targetStat"] for s in h2h_player_stats
                    if s.get("venue") == req.venue and s.get("targetStat") is not None
                ]
                if len(_venue_vals) >= 2:
                    _h2h_avg_use = round(sum(_venue_vals) / len(_venue_vals), 2)
                    _h2h_n_use = len(_venue_vals)
                    _venue_note = f"venue-filtered ({req.venue})"
                else:
                    _h2h_avg_use = _h2h_avg
                    _h2h_n_use = _h2h_n
                    _venue_note = "all venues"

                # Weight: 5% per H2H game, max 25% — season data always dominates
                _h2h_weight = min(_h2h_n_use * 0.05, 0.25)
                # HIGH-TRUST H2H WEIGHT (13% per game, cap 40%):
                # Opponent-specific history dominates over season baseline for
                # defensive volume props where press shape is highly predictive.
                # GK pass_attempts: opponent pressing style is the single most predictive
                # factor after venue. CB/CDM pass props: specific opponent's press
                # intensity and block depth are highly repeatable patterns.
                _is_gk_h2h = (specific_position or "").upper() in {"GK", "GOALKEEPER"} or \
                              (player_position or "").lower() == "goalkeeper"
                _DEF_VOL_ROLES = {"CB", "CDM", "DM", "LB", "RB", "LWB", "RWB", "SW"}
                _DEF_VOL_PROPS = {"pass_attempts", "passes", "tackles", "interceptions", "blocks", "clearances"}
                _is_def_vol_h2h = (
                    req.propType in _DEF_VOL_PROPS and
                    ((specific_position or "").upper() in _DEF_VOL_ROLES or
                     (player_role or "").upper() in _DEF_VOL_ROLES)
                )
                if (_is_gk_h2h and req.propType in {"pass_attempts", "passes"}) or _is_def_vol_h2h:
                    _h2h_weight = min(_h2h_n_use * 0.13, 0.40)  # 13% per game, cap 40%
                _old_bp = bayesian_posterior
                bayesian_posterior = round(
                    _old_bp * (1 - _h2h_weight) + _h2h_avg_use * _h2h_weight, 1
                )
                _record_projection_factor(
                    "opponent_h2h_blend",
                    "Direct player H2H blend",
                    _old_bp,
                    bayesian_posterior,
                    inputs={"h2hAverage": _h2h_avg_use, "weightPct": round(_h2h_weight * 100), "venue": _venue_note},
                    sample_size=_h2h_n_use,
                    multiplier=1 - _h2h_weight,
                    reason="Blended the player's verified appearances against this opponent into the posterior.",
                )
                real_bayes["opponentH2HAvg"] = _h2h_avg_use
                real_bayes["opponentH2HSamples"] = _h2h_n_use
                real_bayes["opponentH2HWeight"] = round(_h2h_weight * 100)
                real_bayes["posteriorMean"] = bayesian_posterior

                if abs(bayesian_posterior - _old_bp) >= 0.3:
                    direction = "▲" if bayesian_posterior > _old_bp else "▼"
                    print(
                        f"[H2H ADJ] {req.playerName} vs {req.opponentName}: "
                        f"H2H avg={_h2h_avg_use} ({_h2h_n_use} games, {_venue_note}, "
                        f"weight={_h2h_weight:.0%}) {direction} {_old_bp:.1f} → {bayesian_posterior:.1f}"
                    )

                # ── H2H LINE HIT RATE — UNANIMOUS SIGNAL ─────────────────────────
                # Separate from the avg-blend above. When ALL same-venue H2H games
                # cleared the line the same way (e.g., 2/2 OVER 38.5), the
                # weighted-average approach will always land between the season avg
                # and the H2H avg — which may never cross the line when the two
                # anchors straddle it. This block treats unanimous line-crossing as
                # independent hard evidence and applies an ADDITIONAL pull toward the
                # H2H avg, strong enough to cross the line.
                #
                # Weight: 20% per same-venue game, capped at 55%.
                # Fires when: ≥2 same-venue H2H games AND ≥75% went same direction.
                # Guard: "all venues" fallback does NOT trigger this — only
                # venue-filtered data (we need location-specific evidence).
                # ─────────────────────────────────────────────────────────────────
                if req.line and len(_venue_vals) >= 2:
                    _h2h_over_n   = sum(1 for v in _venue_vals if v > req.line)
                    _h2h_under_n  = len(_venue_vals) - _h2h_over_n
                    _h2h_line_n   = len(_venue_vals)
                    _h2h_over_pct = _h2h_over_n / _h2h_line_n

                    if _h2h_over_pct >= 0.75 or _h2h_over_pct <= 0.25:
                        # Pull toward a target that is definitively on the dominant side
                        if _h2h_over_pct >= 0.75:
                            # ≥75% of same-venue H2H went OVER → target above the line
                            _h2h_line_target = max(_h2h_avg_use, req.line + 1.5)
                        else:
                            # ≥75% went UNDER → target below the line
                            _h2h_line_target = min(_h2h_avg_use, req.line - 1.5)

                        _h2h_line_weight = min(_h2h_line_n * 0.20, 0.55)
                        _old_bp2 = bayesian_posterior
                        bayesian_posterior = round(
                            _old_bp2 * (1 - _h2h_line_weight) + _h2h_line_target * _h2h_line_weight, 1
                        )
                        _record_projection_factor(
                            "h2h_line_signal",
                            "Unanimous same-venue H2H line signal",
                            _old_bp2,
                            bayesian_posterior,
                            inputs={
                                "target": _h2h_line_target,
                                "overPct": round(_h2h_over_pct * 100),
                                "line": req.line,
                                "weightPct": round(_h2h_line_weight * 100),
                            },
                            sample_size=_h2h_line_n,
                            multiplier=1 - _h2h_line_weight,
                            reason="Same-venue H2H appearances consistently cleared one side of the line.",
                        )
                        real_bayes["h2hLineHitRate"]   = round(_h2h_over_pct * 100)
                        real_bayes["h2hLineSampleN"]   = _h2h_line_n
                        real_bayes["posteriorMean"]    = bayesian_posterior

                        if abs(bayesian_posterior - _old_bp2) >= 0.3:
                            _ldir = "▲" if bayesian_posterior > _old_bp2 else "▼"
                            _ldir_word = "OVER" if _h2h_over_pct >= 0.75 else "UNDER"
                            print(
                                f"[H2H LINE SIGNAL] {req.playerName} vs {req.opponentName}: "
                                f"{_h2h_over_n}/{_h2h_line_n} same-venue H2H {_ldir_word} {req.line} "
                                f"({_h2h_over_pct:.0%}) → target={_h2h_line_target:.1f} "
                                f"weight={_h2h_line_weight:.0%} {_ldir} {_old_bp2:.1f} → {bayesian_posterior:.1f}"
                            )
                # ─────────────────────────────────────────────────────────────────

            # ─────────────────────────────────────────────────────────────────────

            # ─── OPPONENT DEFENSIVE PROFILE ADJUSTMENT ────────────────────────────
            # Blend in what same-position players produce against THIS opponent.
            # Captures opponent-style effects that season averages can't see:
            # e.g., PSG's press suppresses opposing CB pass volume league-wide,
            # or a low-block team inflates opposition shot attempts.
            # Data source: fetch_position_comparison — same position, same venue,
            # opponent's last 10 fixtures (already computed above for AI context).
            # Weight: 2.5% per comparison player, max 15%.
            # Requires at least 3 sampled players to fire (noise guard).
            # Applied AFTER personal H2H blend, BEFORE situational multiplier.
            # ──────────────────────────────────────────────────────────────────────
            if position_comp_data:
                _opp_allowed_avg = position_comp_data.get("avgStatValue", 0)
                _opp_allowed_n   = position_comp_data.get("sampleSize", 0)
                _opp_pos_label   = position_comp_data.get("positionShort", "?")
                if _opp_allowed_avg and _opp_allowed_n >= 3:
                    _opp_weight = min(_opp_allowed_n * 0.025, 0.15)  # base: 2.5% per player, max 15%
                    _old_bp = bayesian_posterior

                    # ── PAIR CALIBRATION ──────────────────────────────────────────────
                    # Comparison players' raw stat vs this opponent reflects their actual
                    # output — but these players may be dominant role players (e.g. primary
                    # CB averaging 55+), while the target is secondary (averaging 38-42).
                    # Blending toward the raw comparison avg over-projects the secondary
                    # player. Fix: compute the opponent's RELATIVE uplift vs those same
                    # players' normal season averages, then apply that same uplift ratio
                    # to THIS player's own baseline level.
                    #
                    #   uplift         = opp_allowed_avg / comp_players_season_avg
                    #   calibrated_opp = player_posterior × uplift
                    #
                    # Only fires for pass-sensitive props when ≥2 comparison players have
                    # a known season average (populated by _fetch_comp_player_stats).
                    # Capped at ±50% of raw opp avg to prevent runaway adjustments.
                    # ──────────────────────────────────────────────────────────────────
                    _pair_calib_props = {"pass_attempts", "passes", "key_passes", "crosses"}
                    if req.propType in _pair_calib_props and position_comparison:
                        _comp_seas = [
                            p["seasonAvgStat"] for p in position_comparison
                            if p.get("seasonAvgStat") and p["seasonAvgStat"] > 0
                        ]
                        if len(_comp_seas) >= 2:
                            _comp_seas_avg = sum(_comp_seas) / len(_comp_seas)
                            if _comp_seas_avg > 0:
                                _opp_uplift = _opp_allowed_avg / _comp_seas_avg
                                _cal_opp    = round(_old_bp * _opp_uplift, 1)
                                # Cap: calibrated must stay within [50%, 150%] of raw opp avg
                                _cal_opp = max(
                                    round(_opp_allowed_avg * 0.50, 1),
                                    min(round(_opp_allowed_avg * 1.50, 1), _cal_opp)
                                )
                                _pair_share = round(_old_bp / _comp_seas_avg, 3)
                                real_bayes["pairShare"]        = _pair_share
                                real_bayes["compSeasonAvg"]    = round(_comp_seas_avg, 1)
                                real_bayes["rawOppAllowedAvg"] = _opp_allowed_avg
                                if abs(_cal_opp - _opp_allowed_avg) >= 0.5:
                                    print(
                                        f"[PAIR CAL] {req.propType} {_opp_pos_label}: "
                                        f"player={_old_bp:.1f} comp_seas={_comp_seas_avg:.1f} "
                                        f"share={_pair_share:.2f} uplift={_opp_uplift:.2f}× "
                                        f"opp {_opp_allowed_avg:.1f}→{_cal_opp:.1f}"
                                    )
                                _opp_allowed_avg = _cal_opp

                    # ── CONVERGENCE BOOST ────────────────────────────────────────────────
                    # When possession dominance AND opponent profile BOTH point the same
                    # direction with meaningful magnitude for pass-sensitive props,
                    # they are measuring the same underlying truth (this matchup inflates/
                    # suppresses pass volume). Compound them by increasing opp_weight.
                    # Without this boost the 15% cap keeps the signal too weak vs the
                    # Bayesian season-average anchor — e.g. a dominant home CB vs a
                    # low-block side where opp avg=85 and poss=63% still lands <line.
                    # ────────────────────────────────────────────────────────────────────
                    _poss_sens = {"pass_attempts", "passes", "key_passes", "crosses", "dribbles"}
                    _is_gk_conv = (specific_position or "").upper() in {"GK", "GOALKEEPER"} or (player_position or "").lower() == "goalkeeper"
                    if req.propType in _poss_sens and not _is_gk_conv:
                        _exp_poss  = match_dominance.get("expectedPoss")
                        _avg_poss  = match_dominance.get("teamSeasonAvg")
                        _opp_diff  = _opp_allowed_avg - _old_bp # +ve = opp allows more than proj
                        # expectedPoss/teamSeasonAvg are ALWAYS floats (default 50.0),
                        # never None — checking hasRealPossData (set only when
                        # compute_match_dominance found a genuine signal) is the only
                        # reliable way to know whether poss_diff below is real or a
                        # meaningless 0.0-vs-0.0 default comparison.
                        _has_poss_data = (
                            match_dominance.get("hasRealPossData")
                            and _exp_poss is not None and _avg_poss is not None
                        )
                        if _has_poss_data:
                            _poss_diff = _exp_poss - _avg_poss      # +ve = more poss than usual
                        else:
                            _poss_diff = 0.0
                        # Same-direction AND both material (≥5pp poss gap, ≥5 stat gap)
                        if (_has_poss_data
                                and _poss_diff * _opp_diff > 0
                                and abs(_poss_diff) >= 5
                                and abs(_opp_diff) >= 5):
                            # Boost scales with possession gap: 5pp→0.05 extra, 10pp→0.10, cap 0.15
                            _conv_boost = min(abs(_poss_diff) / 100.0, 0.15)
                            _opp_weight = min(_opp_weight + _conv_boost, 0.30)  # hard cap 30%
                            print(
                                f"[OPP CONVERGENCE] {req.propType}: poss_diff={_poss_diff:+.1f}pp "
                                f"opp_diff={_opp_diff:+.1f} → weight {_opp_weight:.0%} "
                                f"(+{_conv_boost:.0%} alignment boost)"
                            )
                        elif not _has_poss_data and _old_bp:
                            # ── INDEPENDENT-SIGNAL BOOST ────────────────────────────
                            # No possession projection exists for this fixture (common
                            # for international friendlies vs minnows with sparse
                            # pre-match data), so the convergence check above can never
                            # fire. But the opponent-allowed-avg signal is itself real
                            # and independently measured (recent games vs this opponent
                            # at this position) — it shouldn't be strangled to an 8-15%
                            # weight just because a SEPARATE data source is missing.
                            # Only fires for a strong signal (≥30% relative gap, i.e.
                            # "elite leak"/"elite suppressor" tier) with a decent sample,
                            # and the boost is smaller than full convergence (cap 22%
                            # vs 30%) since it isn't cross-confirmed by possession data.
                            _opp_rel_pct = abs(_opp_diff) / max(abs(_old_bp), 1e-6)
                            if _opp_rel_pct >= 0.30 and _opp_allowed_n >= 3:
                                _indep_boost = min(_opp_rel_pct * 0.25, 0.10)
                                _opp_weight = min(_opp_weight + _indep_boost, 0.22)
                                print(
                                    f"[OPP INDEPENDENT SIGNAL] {req.propType}: "
                                    f"opp_diff={_opp_diff:+.1f} ({_opp_rel_pct:.0%} of prior, "
                                    f"no possession data available) → weight {_opp_weight:.0%} "
                                    f"(+{_indep_boost:.0%} boost, n={_opp_allowed_n})"
                                )

                    bayesian_posterior = round(
                        _old_bp * (1 - _opp_weight) + _opp_allowed_avg * _opp_weight, 1
                    )
                    _record_projection_factor(
                        "opponent_profile",
                        "Same-position opponent profile",
                        _old_bp,
                        bayesian_posterior,
                        inputs={
                            "allowedAverage": _opp_allowed_avg,
                            "sampleSize": _opp_allowed_n,
                            "weightPct": round(_opp_weight * 100),
                            "pairShare": real_bayes.get("pairShare"),
                            "comparisonSeasonAverage": real_bayes.get("compSeasonAvg"),
                            "rawAllowedAverage": real_bayes.get("rawOppAllowedAvg"),
                            "convergence": bool(_has_poss_data and _poss_diff * _opp_diff > 0
                                                and abs(_poss_diff) >= 5 and abs(_opp_diff) >= 5),
                        },
                        sample_size=_opp_allowed_n,
                        multiplier=1 - _opp_weight,
                        reason="Compared the opponent's recent output allowed to same-position players, pair-calibrated to this player's baseline.",
                    )
                    real_bayes["opponentAllowedAvg"]     = round(_opp_allowed_avg, 1)
                    real_bayes["opponentAllowedSamples"] = _opp_allowed_n
                    real_bayes["opponentAllowedWeight"]  = round(_opp_weight * 100)
                    real_bayes["posteriorMean"] = bayesian_posterior
                    if abs(bayesian_posterior - _old_bp) >= 0.2:
                        _dir = "▲" if bayesian_posterior > _old_bp else "▼"
                        print(
                            f"[OPP PROFILE] {_opp_pos_label}s vs {req.opponentName} "
                            f"({player_venue.upper()}): allowed avg={_opp_allowed_avg:.1f} "
                            f"({_opp_allowed_n} players, weight={_opp_weight:.0%}) "
                            f"{_dir} {_old_bp:.1f} → {bayesian_posterior:.1f}"
                        )
            # ─────────────────────────────────────────────────────────────────────

            # ─── SITUATIONAL MULTIPLIER — applied BEFORE final number is locked ───
            # When game state demands different output than seasonal avg, scale the projection.
            _sit_m = game_situation.get("multipliers", {})
            _sit_bayes_mult = _sit_m.get("bayesianMultiplierHome", 1.0) if _sit_is_home else _sit_m.get("bayesianMultiplierAway", 1.0)
            if _sit_bayes_mult != 1.0:
                _old_bp = bayesian_posterior
                bayesian_posterior = round(bayesian_posterior * _sit_bayes_mult, 1)
                _record_projection_factor(
                    "situational_multiplier",
                    "Match situation multiplier",
                    _old_bp,
                    bayesian_posterior,
                    inputs={"multiplier": _sit_bayes_mult, "matchStakes": game_situation.get("matchStakes")},
                    multiplier=_sit_bayes_mult,
                    reason="Adjusted the posterior for the match-state and tactical situation.",
                )
                print(f"[SITUATION MULT] Bayesian {_old_bp:.1f} × {_sit_bayes_mult:.3f} = {bayesian_posterior:.1f} ({req.propType})")
                real_bayes["posteriorMean"] = bayesian_posterior
                real_bayes["situationalMultiplier"] = _sit_bayes_mult
            # ─────────────────────────────────────────────────────────────────────

            # ── KNOCKOUT EXTRA-TIME (ET) ADJUSTMENT ──────────────────────────────
            # Knockout games go to ET (2×15 additional minutes) ~30% of the time.
            # Count stats (pass_attempts, shots, saves…) scale linearly with
            # minutes played. Without this adjustment the engine chronically
            # under-projects for UNDER bets → actual >>> projected when ET fires.
            # Settled WC knockout data: 50% hit rate vs 64% group stage.
            # Multiplier = 1 + P(ET) × (30 extra min / 90 base min) ≈ 1.100
            #
            # DESIGN: applied BEFORE P-REFRESH so the normal-distribution CDF
            # that recomputes p_over/p_under already sees the ET-inflated mean.
            # Consequently UNDER edges shrink (correct) and OVER edges grow.
            # Separate confidence penalty blocks UNDER confidence further.
            # ──────────────────────────────────────────────────────────────────────
            # Safe defaults — must be initialized here so async code paths
            # that skip the main bayesian block still have these defined when
            # the KNOCKOUT UNDER CONFIDENCE PENALTY check fires at line ~7159.
            _final_is_knockout = False
            _KO_COUNT_PROPS = {
                "pass_attempts", "passes", "shots", "shots_on_target",
                "saves", "key_passes", "crosses", "dribbles", "tackles", "clearances",
            }
            # Resolve is_knockout: prefer situation engine flag (always defined),
            # fall back to the match_context local var which is only set when
            # match_odds is present.
            _final_is_knockout = game_situation.get("isKnockout", False)
            if not _final_is_knockout:
                _ko_kws = ("final", "quarter", "semi", "round of", "knockout", "elimination", "playoff")
                _raw_round_ko = (match_odds or {}).get("matchRound", "") if match_odds else ""
                if _raw_round_ko:
                    _final_is_knockout = any(kw in _raw_round_ko.lower() for kw in _ko_kws)

            if _final_is_knockout and req.propType in _KO_COUNT_PROPS:
                _KO_ET_PROB  = 0.30           # 30 % of knockout games go to ET historically
                _KO_ET_MULT  = round(1.0 + _KO_ET_PROB * (30.0 / 90.0), 4)  # ≈ 1.1000
                _ko_old_bp   = bayesian_posterior
                bayesian_posterior = round(bayesian_posterior * _KO_ET_MULT, 1)
                _record_projection_factor(
                    "knockout_extra_time",
                    "Knockout extra-time exposure",
                    _ko_old_bp,
                    bayesian_posterior,
                    inputs={"extraTimeProbability": _KO_ET_PROB, "extraMinutes": 30, "knockout": True},
                    multiplier=_KO_ET_MULT,
                    reason="Added expected count volume from the possibility of 30 minutes of extra time.",
                )
                real_bayes["posteriorMean"]    = bayesian_posterior
                real_bayes["koExtraTimeAdj"]   = _KO_ET_MULT
                real_bayes["koExtraTimeProb"]  = _KO_ET_PROB
                print(
                    f"[KNOCKOUT ET ADJ] {req.playerName}/{req.propType}: "
                    f"{_ko_old_bp:.1f} × {_KO_ET_MULT:.4f} → {bayesian_posterior:.1f} "
                    f"(P(ET)={_KO_ET_PROB:.0%})"
                )
            # ─────────────────────────────────────────────────────────────────────

            # ── RECOMPUTE P(over)/P(under) AFTER OPP-PROFILE + SITUATION MULT ──
            # The opponent profile (and situational multiplier) can shift bayesian_posterior
            # significantly — e.g. 39.1 → 43.0 — AFTER pOver/pUnder were frozen by the
            # Bayesian engine. If we don't refresh the probabilities here, BAYESIAN TRUTH
            # reads the stale pOver=35.6% and locks in UNDER even though the final
            # projection is clearly in OVER territory.
            # Use the predictive std (game-to-game variability), not posteriorStd
            # which is the credible interval for the mean (often ~0.3) and far too
            # tight for P(over a line). Mirror the engine's effective_std formula:
            # max(posterior_std, prior_std*0.55, posterior_mean*0.17)
            _rb_prior_std    = real_bayes.get("priorStd") or 0.0
            _rb_post_std_raw = real_bayes.get("posteriorStd") or 0.0
            _rb_eff_std = max(
                _rb_post_std_raw,
                _rb_prior_std * 0.55,
                bayesian_posterior * 0.17,
            )
            if _rb_eff_std > 0 and req.line:
                try:
                    import math as _math
                    def _norm_cdf(x):
                        return 0.5 * (1 + _math.erf(x / _math.sqrt(2)))
                    _z = (req.line - bayesian_posterior) / _rb_eff_std
                    _new_p_under = round(100 * _norm_cdf(_z), 1)
                    _new_p_over  = round(100 - _new_p_under, 1)
                    _old_p_over  = real_bayes.get("pOver", 50)
                    if abs(_new_p_over - _old_p_over) >= 2.0:
                        real_bayes["pOver"]  = _new_p_over
                        real_bayes["pUnder"] = _new_p_under
                        _new_rec = "over" if _new_p_over >= _new_p_under else "under"
                        real_bayes["recommendation"] = _new_rec
                        print(
                            f"[P-REFRESH] {req.playerName}/{req.propType}: "
                            f"posterior={bayesian_posterior} eff_std={_rb_eff_std:.2f} "
                            f"→ P(over) {_old_p_over}% → {_new_p_over}% rec={_new_rec.upper()}"
                        )
                except Exception as _pr_err:
                    print(f"[P-REFRESH-ERR] {_pr_err}")
            # ─────────────────────────────────────────────────────────────────────

            bayesian_prob = max(real_bayes.get("pOver", 50), real_bayes.get("pUnder", 50)) / 100
            bayesian_rec = real_bayes.get("recommendation", "over")
            # early_proj = early_bayes estimate before full multi-factor Bayesian run
            early_proj = prediction.get("projectedValue", req.line)
            early_rec  = prediction.get("recommendation", "over")

            divergence_pct = abs(early_proj - bayesian_posterior) / max(bayesian_posterior, 1) * 100

            # Log when early estimate and full Bayesian differ noticeably (adjustment audit trail)
            if divergence_pct > 10 and bayesian_rec != early_rec:
                print(f"[BAYES ADJUST] Early={early_proj}({early_rec}) → Full Bayes={bayesian_posterior}({bayesian_rec}) — {divergence_pct:.0f}% shift after all adjustments.")

            print(f"[PROJECTION] Bayesian={bayesian_posterior}({bayesian_rec}, {bayesian_prob:.0%}) | Early estimate={early_proj}({early_rec}) — MATH IS FINAL. Gemini = explanation only.")

            # ── Apply nightly-learned bias offsets ──────────────────────────
            # GK pass_attempts UNDER: the GK inverted possession model already achieves
            # 70% UNDER hit rate through position-specific logic. The general UNDER offset
            # (+1.94) is driven by MID UNDER failures and must NOT be applied to GKs —
            # it would push correct GK UNDER projections above the line and flip them to OVER.
            # GK pass_attempts OVER still benefits from the direction correction (-1.14).
            _is_gk_pass_under = (
                req.propType == "pass_attempts"
                and bayesian_rec == "under"
                and (specific_position or "").upper() in {"GK", "GOALKEEPER"}
            )
            if CALIBRATION_ENABLED:
                try:
                    from calibration import apply_learned_offsets
                    _offset_venue = player_venue or req.venue or "home"
                    # For GK UNDER: skip direction offset, fall through to venue/league
                    _cal_rec = None if _is_gk_pass_under else bayesian_rec
                    bayesian_posterior, _offset_note = await apply_learned_offsets(
                        posterior=bayesian_posterior,
                        prop_type=req.propType,
                        venue=_offset_venue,
                        recommendation=_cal_rec,
                        league_id=req.leagueId,
                        sport="soccer",
                    )
                    if _offset_note:
                        # Recalculate direction from calibrated posterior, then apply
                        # probability override: when P(UNDER) > P(OVER), prefer UNDER
                        # even if the calibrated mean is slightly above the line.
                        _cal_rec_by_mean = "over" if bayesian_posterior > req.line else "under"
                        _rb_p_over  = real_bayes.get("pOver", 50)
                        _rb_p_under = real_bayes.get("pUnder", 50)
                        if _cal_rec_by_mean == "over" and _rb_p_under > _rb_p_over:
                            bayesian_rec = "under"
                            print(f"[PROB DIRECTION] {req.playerName}: post-cal mean={bayesian_posterior} (OVER) "
                                  f"but P(UNDER)={_rb_p_under}%>P(OVER)={_rb_p_over}% → UNDER")
                        elif _cal_rec_by_mean == "under" and _rb_p_over > _rb_p_under:
                            bayesian_rec = "over"
                            print(f"[PROB DIRECTION] {req.playerName}: post-cal mean={bayesian_posterior} (UNDER) "
                                  f"but P(OVER)={_rb_p_over}%>P(UNDER)={_rb_p_under}% → OVER")
                        else:
                            bayesian_rec = _cal_rec_by_mean
                        real_bayes["posteriorMean"] = bayesian_posterior
                except Exception as _oe:
                    print(f"[NIGHTLY CAL APPLY] Error applying offsets: {_oe}")
            else:
                print("[NIGHTLY CAL] Calibration disabled — raw Bayesian posterior used.")
            # ───────────────────────────────────────────────────────────────

            prediction["projectedValue"] = bayesian_posterior
            prediction["recommendation"] = bayesian_rec
            prediction["fusionApplied"] = {
                "earlyEstimate": early_proj,        # math's early_bayes estimate before all adjustments
                "earlyEstimateRec": early_rec,
                "bayesianPosterior": bayesian_posterior,
                "bayesianRecommendation": bayesian_rec,
                "bayesianConfidence": round(bayesian_prob * 100, 1),
                "fusedProjection": bayesian_posterior,
                "fusedRecommendation": bayesian_rec,
                "weights": {"math": 1.0, "gemini": 0},  # Gemini = explanation only, zero weight in projection
                "agreement": bayesian_rec == early_rec,
                "divergencePct": round(divergence_pct, 1),
                "note": "projectedValue is determined entirely by the Reverse Formula math engine. Gemini writes explanation text only.",
            }

            pass  # Math Lock runs after PASS GATE below — see [MATH LOCK] block

        # =============================================
        # POST-PROJECTION DOMINANCE SCALING — NON-PASS PROPS ONLY
        #
        # The Bayesian engine owns possession-sensitive pass volume. Applying
        # match_dominance["multiplier"] again here double-counts possession and
        # was the source of clustered passing-prop projection errors. Keep this
        # route-level adjustment disabled for every prop in this set; the
        # variable remains available for the non-pass tempo/favorite audit below.
        # =============================================
        poss_sensitive = {"pass_attempts", "passes", "key_passes", "crosses", "dribbles"}
        _post_dom_props = set()

        _is_gk_dom = (specific_position or "").upper() in {"GK", "GOALKEEPER"} or (player_position or "").lower() == "goalkeeper"
        if req.propType in _post_dom_props and not _is_gk_dom and match_dominance.get("multiplier", 1.0) != 1.0:
            dom_mult = match_dominance["multiplier"]
            team_avg_poss = match_dominance.get("teamSeasonAvg", 50)
            exp_poss      = match_dominance.get("expectedPoss", 50)
            current = prediction.get("projectedValue", req.line)

            if team_avg_poss < 52 and dom_mult < 0.92:
                # Low-possession team facing a dominant opponent — scale down
                post_dom = round(current * dom_mult, 1)
                prediction["projectedValue"] = post_dom
                prediction["recommendation"] = "over" if post_dom > req.line else "under"
                print(f"[DOMINANCE] APPLIED: {current} × {dom_mult:.3f} → {post_dom} (team avg {team_avg_poss:.0f}% < 52% threshold)")
            elif dom_mult > 1.08 and exp_poss > team_avg_poss + 8 and team_avg_poss < 52:
                # Team expected to significantly exceed their own season-average possession.
                # ONLY applies to LOW-possession teams (avg < 52%). High-possession teams
                # already have their Bayesian calibrated to their possession style.
                #
                # COLD-STREAK GATE: If the player's recent form (momentumMean) is already
                # running >4 passes below their season average, the form is the dominant
                # signal — it likely reflects WHY possession isn't translating to more volume
                # for this specific player (tactical role, fatigue, manager decisions).
                # Applying a possession boost on top fights this signal and over-inflates.
                _eb_momentum = (early_bayes or {}).get("momentumMean")
                _eb_prior    = (early_bayes or {}).get("priorMean")
                _cold_streak = (
                    _eb_momentum is not None and _eb_prior is not None
                    and _eb_momentum < _eb_prior - 4
                )
                if _cold_streak:
                    print(
                        f"[DOMINANCE] SKIP positive boost — cold streak: "
                        f"form={_eb_momentum:.1f} vs season_avg={_eb_prior:.1f} "
                        f"(gap={_eb_prior - _eb_momentum:.1f}). Form is the lead signal."
                    )
                else:
                    # Damping schedule (fraction of raw mult excess applied):
                    #   team_avg < 42% → 55% (rarely in possession — surge is highly anomalous)
                    #   team_avg < 48% → 40% (below-average — meaningful departure from norm)
                    #   team_avg 48-52% → 20% (approaching normal — Bayesian covers most of it)
                    if team_avg_poss < 42:
                        _damp_frac = 0.55
                    elif team_avg_poss < 48:
                        _damp_frac = 0.40
                    else:
                        _damp_frac = 0.20
                    _damped_mult = 1.0 + (dom_mult - 1.0) * _damp_frac
                    post_dom = round(current * _damped_mult, 1)
                    _old_rec = prediction.get("recommendation", "over")
                    prediction["projectedValue"] = post_dom
                    prediction["recommendation"] = "over" if post_dom > req.line else "under"
                    print(
                        f"[DOMINANCE] POSITIVE: {current} × {_damped_mult:.3f} → {post_dom} "
                        f"(exp {exp_poss:.0f}% vs avg {team_avg_poss:.0f}%, raw mult={dom_mult:.3f})"
                    )
                    # If the positive boost flipped the recommendation, the AI confidence was
                    # calibrated for the opposite direction — reset it based on the new edge.
                    _new_rec = prediction["recommendation"]
                    if _new_rec != _old_rec or True:  # always recalibrate after DOMINANCE
                        _dom_edge = abs(post_dom - req.line)
                        # Base: 55% + 1.5% per pass over the line, capped at 68%
                        _base_conf = min(68, round(55 + _dom_edge * 1.5))
                    prediction["confidenceScore"] = _base_conf
                    print(f"[DOMINANCE] Confidence recalibrated: {_base_conf}% (edge={_dom_edge:.1f})")
                    # Recalibrate edgeZ so downstream guards use the final edge
                    if real_bayes:
                        _bstd = real_bayes.get("posteriorStd", 10) or 10
                        real_bayes["edgeZ"] = round(abs(post_dom - req.line) / max(_bstd, 5), 2)
            else:
                would_be = round(current * dom_mult, 1)
                print(f"[DOMINANCE] SKIPPED: {current} × {dom_mult:.3f} would be {would_be} (team avg {team_avg_poss:.0f}% — Bayesian covers this)")

        if req.propType in poss_sensitive and game_tempo.get("tempoMultiplier", 1.0) != 1.0:
            tempo_mult = game_tempo["tempoMultiplier"]
            current = prediction.get("projectedValue", req.line)
            print(f"[TEMPO] LOGGED ONLY: {current} × {tempo_mult:.3f} (NOT applied)")

        if favorite_dampening.get("applied") and req.propType in poss_sensitive:
            fav_factor = favorite_dampening["dampeningFactor"]
            current = prediction.get("projectedValue", req.line)
            print(f"[FAV DAMPEN] LOGGED ONLY: {current} × {1.0-fav_factor:.3f} (NOT applied)")

        # HARD GUARD: recommendation MUST match the FINAL projected value vs line
        final_proj = prediction.get("projectedValue", req.line)
        prediction["recommendation"] = "over" if final_proj > req.line else "under"

        # ── Inject redistribution + lineup alerts into tacticalAlerts ────────
        if _redist_alerts:
            prediction["tacticalAlerts"] = prediction.get("tacticalAlerts", []) + _redist_alerts
        if _lineup_alert:
            prediction["tacticalAlerts"] = prediction.get("tacticalAlerts", []) + [_lineup_alert]
        if _lineup_status == "starting":
            prediction["lineupConfirmed"] = True
        elif _lineup_status in ("substitute", "not_in_squad"):
            prediction["lineupWarning"] = True

        # ── Risk signals (red-card/dismissal volatility) + fixture congestion ──
        # NOTE: mobile/lib/api.ts#PredictionResult['riskSignals'] expects
        # {yellowCardAvg, redCardRisk: 'low'|'elevated'|'high', opponentYellowCardAvg, note}
        # — keep this mapping in sync with that interface, not the internal _risk_signals shape.
        try:
            _rs = _risk_signals
            _level_map = {"normal": "low", "moderate": "elevated", "elevated": "high"}
            prediction["riskSignals"] = {
                "yellowCardAvg": _rs.get("teamCardsAvg"),
                "opponentYellowCardAvg": _rs.get("oppCardsAvg"),
                "redCardRisk": _level_map.get(_rs.get("level"), "low"),
                "note": _rs.get("note"),
            }
            if _rs.get("note"):
                prediction["tacticalAlerts"] = prediction.get("tacticalAlerts", []) + [_rs["note"]]
        except NameError:
            prediction["riskSignals"] = {"yellowCardAvg": None, "opponentYellowCardAvg": None, "redCardRisk": "low", "note": None}

        # NOTE: mobile/lib/api.ts#PredictionResult['congestion'] expects
        # {teamRestDays, opponentRestDays, teamGamesIn14d, opponentGamesIn14d, fatigueFlag: 'low'|'moderate'|'high'}
        try:
            _fatigue_layer = (early_bayes or {}).get("fatigueLayer", {}) or {}
            # NOTE: congestion_games is None when there wasn't enough dated
            # game-log history to compute a real games-in-14d count (common
            # for national-team/tournament contexts with sparse logs) — keep
            # it None rather than coercing to 0, which would misleadingly
            # read as "confirmed zero games" instead of "not enough data".
            _cong_games = _fatigue_layer.get("congestion_games")
            _fatigue_flag = "high" if (_cong_games or 0) >= 4 else ("moderate" if (_cong_games or 0) >= 3 else "low")
            prediction["congestion"] = {
                "teamRestDays": _fatigue_layer.get("rest_days"),
                "opponentRestDays": _fatigue_layer.get("opponent_rest_days"),
                "teamGamesIn14d": _cong_games,
                "opponentGamesIn14d": _fatigue_layer.get("opponent_congestion_games"),
                "fatigueFlag": _fatigue_flag,
            }
        except Exception:
            prediction["congestion"] = {
                "teamRestDays": None, "opponentRestDays": None,
                "teamGamesIn14d": None, "opponentGamesIn14d": None, "fatigueFlag": "low",
            }

        # ── Lineup pitch data (predicted or confirmed XI + formation) ──
        # NOTE: mobile/components/PitchDiagram.tsx expects {status, home:{teamName,formation,coach,players[]}, away:{...}}
        _raw_lineup = locals().get("_pitch_lineup") or {}
        _is_player_home = (locals().get("player_venue") == "home")
        _team_side = {
            "teamName": req.teamName or None,
            "formation": _raw_lineup.get("formation"),
            "coach": _raw_lineup.get("coach"),
            "players": _raw_lineup.get("players") or [],
        }
        _opp_side = {
            "teamName": req.opponentName or None,
            "formation": _raw_lineup.get("opponentFormation"),
            "coach": _raw_lineup.get("opponentCoach"),
            "players": _raw_lineup.get("opponentPlayers") or [],
        }
        _has_lineup_data = bool(_team_side["players"] or _opp_side["players"])
        prediction["lineup"] = {
            "status": _raw_lineup.get("status") or "unavailable",
            "home": _team_side if _is_player_home else _opp_side,
            "away": _opp_side if _is_player_home else _team_side,
        } if _has_lineup_data else None

        # =============================================
        # POST-CONSENSUS CONFIDENCE GUARDS
        # =============================================
        conf = prediction.get("confidenceScore", 50)
        proj_val = prediction.get("projectedValue", req.line)
        edge = abs(proj_val - req.line)
        rec = prediction.get("recommendation", "over")

        # Guard 0: Direction-specific blocked prop types
        # clearances OVER: 0% hit rate (0W 5L) — all picks had margin ≤ 0.5 above line.
        # Clearances UNDER hits at 100% (4W 0L) and is NOT penalized.
        # shots OVER at thin margins (margin < 1.0): 10% hit rate — discrete count means
        # proj=2 vs line=1.5 is a 50/50 coin flip that the model systematically over-calls.
        if req.propType == "clearances" and rec == "over":
            prediction["confidenceScore"] = 45
            prediction["coinFlip"] = True
            prediction["tacticalAlerts"] = prediction.get("tacticalAlerts", []) + [
                "CLEARANCES OVER blocked: 0% historical hit rate — bookmakers set these lines precisely. Clearances UNDER remains viable."
            ]
            print(f"[GUARD 0] clearances OVER → forced to 45% coin-flip (0% hit rate, data n=5)")

        if req.propType in {"shots", "shots_on_target"} and rec == "over" and edge < 1.0:
            prediction["confidenceScore"] = 45
            prediction["coinFlip"] = True
            prediction["tacticalAlerts"] = prediction.get("tacticalAlerts", []) + [
                f"SHOTS OVER blocked: proj={proj_val} only +{edge:.1f} above line {req.line}. "
                "For discrete shot counts a margin < 1 is a coin flip — model shows 10% hit rate here."
            ]
            print(f"[GUARD 0b] shots OVER margin={edge:.1f} < 1.0 → forced to 45% coin-flip")

        # Guard 1: Binary line (0.5) — UNDER means zero, very risky
        if req.line <= 0.5 and rec == "under" and conf > 55:
            prediction["confidenceScore"] = 55
            prediction["tacticalAlerts"] = prediction.get("tacticalAlerts", []) + [
                "Binary line (0.5): UNDER requires ZERO of this stat — high-risk"
            ]
            print(f"[GUARD] Binary line 0.5 UNDER: confidence capped at 55% (was {conf})")

        # Guard 2: Tight edge — projected value within ±1 of line
        if edge < 1.0 and conf > 58:
            prediction["confidenceScore"] = 58
            prediction["tacticalAlerts"] = prediction.get("tacticalAlerts", []) + [
                f"Tight edge: projection {proj_val} is within 1.0 of line {req.line} — marginal"
            ]
            print(f"[GUARD] Tight edge ({edge:.1f}): confidence capped at 58% (was {conf})")

        # Guard 3: Coin-flip zone
        # Hard threshold: any pick with edge < 2.0 is a coin flip regardless of
        # Bayesian probability — the projected value is so close to the line that
        # market noise dominates. Previously gated by bayes_conf < 60% which
        # allowed near-zero edge picks (e.g. proj=66 vs line=65.5) to slip through
        # as full-confidence picks.
        _bayes_conf_g3 = 50
        if real_bayes:
            _bayes_conf_g3 = max(real_bayes.get("pOver", 50), real_bayes.get("pUnder", 50))
        if edge < 2.0 or (edge < 3.0 and _bayes_conf_g3 < 60):
            old_conf = prediction.get("confidenceScore", 50)
            prediction["confidenceScore"] = min(old_conf, 52)
            prediction["coinFlip"] = True
            prediction["tacticalAlerts"] = prediction.get("tacticalAlerts", []) + [
                f"COIN FLIP: Edge only {edge:.1f} vs line {req.line} (proj={prediction.get('projectedValue','?')}). Bayesian P={_bayes_conf_g3}%. Near-line picks are variance-driven."
            ]
            print(f"[GUARD] Coin-flip zone: edge={edge:.1f}, Bayesian P={_bayes_conf_g3}% → capped at 52% (was {old_conf})")

        # Guard 3-PASS: pass_attempts OVER thin-margin extension.
        # Backtest (n=479 OVER pass_attempts picks):
        #   edge < 3 → 45% hit rate regardless of Bayesian confidence.
        #   edge 3-10 → 56% hit rate.
        #   edge 10+ → 56% hit rate.
        # Guard 3 only catches edge < 2.0 unconditionally. Picks with edge 2-3 and
        # bayes_conf ≥ 60% slip through and hit at 45% — worse than random.
        # Fix: extend coin-flip zone to edge < 3.0 for pass_attempts OVER.
        # UNDER is NOT penalized (UNDER hits at 65% across all margin buckets).
        if req.propType in {"pass_attempts", "passes"} and rec == "over" and edge < 3.0:
            old_conf = prediction.get("confidenceScore", 50)
            if old_conf > 52:
                prediction["confidenceScore"] = 52
                prediction["coinFlip"] = True
                prediction["tacticalAlerts"] = prediction.get("tacticalAlerts", []) + [
                    f"PASS OVER thin edge: proj {proj_val} is only +{edge:.1f} above line {req.line}. "
                    "Backtest shows <3 edge OVER picks hit at 45% — coin flip territory."
                ]
                print(f"[GUARD 3-PASS] pass_attempts OVER edge={edge:.1f} < 3.0 → capped at 52% (was {old_conf})")

        # Guard 3a: High-confidence OVER coin-flip flag.
        # 30-day backtest: OVER ≥70% confidence hits at only 45.7% (32/70) — WORSE
        # than random. Breakdown by prop: pass_attempts OVER ≥70% = 22/55 = 40%.
        # The model's upward projection bias is most extreme at high confidence.
        # When the model is very "certain" about an OVER, the upward bias has pulled
        # the projection far above the line — exactly where the model is most wrong.
        #
        # Fix: flag all OVER picks at ≥70% confidence as coin flips (capped at 55%).
        # They remain visible in the app but are clearly marked as uncertain.
        # UNDER picks are NOT penalized — UNDER 50-59% hits at 65.8% (better than
        # high-confidence OVER), so the confidence score for UNDER is already
        # mis-calibrated low and should not be penalised further.
        if rec == "over":
            _over_conf = prediction.get("confidenceScore", 50)
            if _over_conf >= 70:
                prediction["confidenceScore"] = min(_over_conf, 55)
                prediction["coinFlip"] = True
                prediction["tacticalAlerts"] = prediction.get("tacticalAlerts", []) + [
                    f"OVER BIAS: High-confidence OVER picks hit at only 45.7% in 30-day data — model's upward projection bias is strongest here. Treat as coin flip."
                ]
                print(f"[GUARD 3a] High-conf OVER ({_over_conf}%) flagged as coin flip → 55%")

        # Guard 3b: High-scoring game CB pass volatility
        # CBs in high expected-total games (Vegas line ≥ 4.0 goals) show extreme
        # pass variance — goals create chaos, shape changes kill steady build-up.
        # Moussa Niakhaté (Lyon 4-2 Rennes): two OVER picks both missed badly.
        # Reduce confidence so users aren't overexposed to volatile defender props
        # in goal-fests.
        _cb_volatile_pos = {"CB", "LB", "RB", "LCB", "RCB", "WB", "WBL", "WBR"}
        _pos_upper_g3b = (player_position or "").upper()
        if (_pos_upper_g3b in _cb_volatile_pos
                and req.propType in {"pass_attempts", "passes"}
                and _game_script and isinstance(_game_script, dict)):
            _gs_total_g3b = _game_script.get("expected_total_goals", 0) or 0
            if _gs_total_g3b >= 4.0:
                _hs_penalty = min(14, round((_gs_total_g3b - 3.5) * 5))
                _pre_hs = prediction.get("confidenceScore", 50)
                prediction["confidenceScore"] = max(47, _pre_hs - _hs_penalty)
                prediction["tacticalAlerts"] = prediction.get("tacticalAlerts", []) + [
                    f"HIGH-SCORING GAME: Defender pass volume highly volatile in {_gs_total_g3b}-goal expected games — confidence reduced"
                ]
                if _pre_hs != prediction["confidenceScore"]:
                    print(f"[GUARD] High-scoring CB volatility: total={_gs_total_g3b} -{_hs_penalty}% ({_pre_hs}→{prediction['confidenceScore']})")

        # Guard 3c: open_close scenario — high confidence picks in close-game scenarios
        # hit at only 31% (8/26) in 30-day backtest. When the pre-game model assigns
        # >35% probability to an open/close (1-goal game) result, the outcome is
        # too random for high-confidence calls. Cap these at 62%.
        _p_open_close = (_scenario_probs or {}).get("P_open_close", 0)
        if _p_open_close > 0.35:
            _oc_conf = prediction.get("confidenceScore", 50)
            if _oc_conf >= 70:
                _oc_penalty = min(22, round(_p_open_close * 35))
                prediction["confidenceScore"] = max(52, _oc_conf - _oc_penalty)
                prediction["tacticalAlerts"] = prediction.get("tacticalAlerts", []) + [
                    f"OPEN/CLOSE SCENARIO ({_p_open_close*100:.0f}% game probability): High-confidence picks in close-game scenarios hit at only 31% — confidence reduced"
                ]
                if _oc_conf != prediction["confidenceScore"]:
                    print(f"[GUARD 3c] open_close scenario: P={_p_open_close:.2f}, -{_oc_penalty}% ({_oc_conf}→{prediction['confidenceScore']})")

        # Guard 3d: draw scenario — low confidence picks in draw-probability games
        # hit at only 50% (75/149) — indistinguishable from random.
        # When draw probability > 30% and the model has low confidence anyway (<60%),
        # the pick has no edge. Cap at 50%.
        _p_draw = (_scenario_probs or {}).get("P_draw", 0)
        if _p_draw > 0.30:
            _draw_conf = prediction.get("confidenceScore", 50)
            if _draw_conf < 60:
                _draw_penalty = min(10, round(_p_draw * 20))
                prediction["confidenceScore"] = max(45, _draw_conf - _draw_penalty)
                prediction["tacticalAlerts"] = prediction.get("tacticalAlerts", []) + [
                    f"DRAW SCENARIO ({_p_draw*100:.0f}% probability): Low-confidence picks in draw-likely games are near-random — confidence reduced"
                ]
                if _draw_conf != prediction["confidenceScore"]:
                    print(f"[GUARD 3d] draw scenario: P={_p_draw:.2f}, -{_draw_penalty}% ({_draw_conf}→{prediction['confidenceScore']})")

        # Guard 3d-ii: draw scenario + OVER + CB/CM/CAM pass_attempts = catastrophic
        # Empirical: owner DRAW OVER pass_attempts hits only 25.9% (7/27).
        # CB in draws: 33.3%, CM in draws: 0%, CAM in draws: 0%.
        # The model applies CB lead-manage boosts and CDM chase-mode boosts which
        # OVERFIRE in draw scenarios — predicting OVER when possession stays even
        # and no lead needs managing. Hard cap confidence at 52% for these combos.
        _draw_over_pos_set = {"CB", "LCB", "RCB", "CM", "MC", "CAM", "AM", "LM", "RM"}
        if (_p_draw > 0.25
                and req.propType in {"pass_attempts", "passes"}
                and str(prediction.get("recommendation", "")).lower() == "over"
                and str(_bayes_position or "").upper() in _draw_over_pos_set):
            _d2_pre = prediction.get("confidenceScore", 50)
            if _d2_pre > 52:
                prediction["confidenceScore"] = 52
                prediction["confidenceLevel"] = "Low"
                prediction["tacticalAlerts"] = prediction.get("tacticalAlerts", []) + [
                    f"DRAW + OVER WARNING ({_p_draw*100:.0f}% draw probability): "
                    f"{_bayes_position} pass OVER picks in draw scenarios hit only 26% historically — confidence capped"
                ]
                print(f"[GUARD 3d-ii] draw+OVER+{_bayes_position} pass_attempts: P_draw={_p_draw:.2f} "
                      f"conf {_d2_pre}→52 (empirical 26% hit rate)")

        # Guard 3e: home_blowout + away + OVER pass_attempts
        # Empirical: owner OVER in home_blowout scenarios hits only 25% (3/12).
        # Away players in blowouts park the bus / defend deep → minimal passing,
        # long clearances replace build-up sequences. Model over-projects away
        # pass volume because it expects normal game-state possession fractions.
        _p_home_blowout = (_scenario_probs or {}).get("P_home_blowout", 0)
        if (_p_home_blowout > 0.25
                and req.propType in {"pass_attempts", "passes"}
                and str(prediction.get("recommendation", "")).lower() == "over"
                and str(player_venue or "").lower() == "away"):
            _hb_pre = prediction.get("confidenceScore", 50)
            if _hb_pre > 52:
                prediction["confidenceScore"] = 52
                prediction["confidenceLevel"] = "Low"
                prediction["tacticalAlerts"] = prediction.get("tacticalAlerts", []) + [
                    f"HOME BLOWOUT + AWAY OVER WARNING ({_p_home_blowout*100:.0f}% blowout probability): "
                    f"Away pass OVER picks in blowout scenarios hit only 25% — away team parks bus and passes fall"
                ]
                print(f"[GUARD 3e] home_blowout+away OVER pass_attempts: P={_p_home_blowout:.2f} "
                      f"conf {_hb_pre}→52 (empirical 25% hit rate)")

        # Guard 3f: Bundesliga home OVER pass_attempts confidence cap
        # Empirical: Bundesliga (ID 78) home OVER hits only 30.8% (4/13).
        # High-press vertical style — GKs/CBs pass count runs 13% below model's
        # cross-league prior. Bundesliga deflation already applied in the Bayesian
        # engine (×0.87), but if projection still lands OVER after deflation
        # we add a visible warning and cap confidence at 58%.
        if (req.leagueId == 78
                and req.propType in {"pass_attempts", "passes"}
                and str(prediction.get("recommendation", "")).lower() == "over"
                and str(player_venue or "").lower() == "home"):
            _bf_pre = prediction.get("confidenceScore", 50)
            if _bf_pre > 58:
                prediction["confidenceScore"] = 58
                prediction["tacticalAlerts"] = prediction.get("tacticalAlerts", []) + [
                    "BUNDESLIGA HOME OVER: High-press league — pass counts run 13% below model prior. "
                    "Historical hit rate 31% on home OVER pass picks. Confidence capped."
                ]
                print(f"[GUARD 3f] Bundesliga home OVER pass_attempts: conf {_bf_pre}→58")

        # Guard 4: Base-rate conflict — model recommendation fights the player's own season average.
        # When the season average sits on the OPPOSITE side of the line from the recommendation,
        # an external factor (possession squeeze, opponent matchup) is overriding the base rate.
        # These picks historically have lower accuracy because the base rate is a very strong prior.
        # Apply a confidence penalty proportional to how far the average is on the wrong side.
        _prior_m = (real_bayes or {}).get("priorMean")
        if _prior_m is not None and req.line > 0:
            _base_says_over = _prior_m > req.line
            _model_says_over = rec == "over"
            if _base_says_over != _model_says_over:
                _conflict_gap = abs(_prior_m - req.line)
                # Penalty: 15% flat minimum, +3% per pass of conflict gap beyond 2, capped at 25%
                _conflict_penalty = min(25, max(15, round(15 + (_conflict_gap - 2) * 3)))
                _pre_conflict = prediction.get("confidenceScore", 50)
                prediction["confidenceScore"] = max(45, _pre_conflict - _conflict_penalty)
                _conflict_dir = "OVER" if _base_says_over else "UNDER"
                prediction["tacticalAlerts"] = prediction.get("tacticalAlerts", []) + [
                    f"BASE-RATE CONFLICT: Season avg {_prior_m} is on the {_conflict_dir} side of line {req.line} — contextual model fights historical norm"
                ]
                print(
                    f"[GUARD] Base-rate conflict: season avg {_prior_m} is {_conflict_dir} of line {req.line}, "
                    f"rec={rec.upper()}, gap={_conflict_gap:.1f} → -{_conflict_penalty}% conf "
                    f"({_pre_conflict} → {prediction['confidenceScore']})"
                )

        # Guard 5: Line-Deviation Intelligence — data-driven market asymmetry guard.
        # Uses the deviation band system (calibration.py) to adjust confidence
        # based on how far the book's line is from our model's projection.
        # The further our rec disagrees with where the book set the line, the more
        # we trust the book's information over our model's historical baseline.
        #
        # Hit rates by band are LEARNED from settled picks (self-improving).
        # When insufficient settled data exists, empirically-researched defaults apply.
        try:
            from calibration import get_line_deviation_intel
            _dev_proj = prediction.get("projectedValue", req.line)
            if _dev_proj and req.line > 0 and rec in ("over", "under"):
                _dev_intel = await get_line_deviation_intel(
                    line=req.line,
                    projected_value=_dev_proj,
                    recommendation=rec,
                    prop_type=req.propType,
                )
                _dev_band       = _dev_intel.get("band", "aligned")
                _dev_pct        = _dev_intel.get("deviationPct", 0)
                _dev_against    = _dev_intel.get("againstBook", False)
                _dev_hit_rate   = _dev_intel.get("hitRate", 55)
                _dev_delta      = _dev_intel.get("confidenceDelta", 0)
                _dev_note       = _dev_intel.get("note", "")
                _dev_n          = _dev_intel.get("hitRateN", 0)
                _dev_src        = _dev_intel.get("hitRateSource", "default")

                # Always expose band + deviation for frontend display (regardless of conf adjustment)
                prediction["lineDeviationBand"]    = _dev_band
                prediction["lineDeviationPct"]     = _dev_pct
                prediction["lineDeviationHitRate"] = _dev_hit_rate

                # Apply confidence adjustment for non-aligned, against-book bands
                if _dev_against and _dev_band not in ("aligned",) and abs(_dev_delta) >= 2:
                    _is_def_dev = player_position in {"Defender"}
                    # Extra damping for defenders on pass props (extra possession-sensitive)
                    _dev_extra = 0
                    if _is_def_dev and req.propType in {"pass_attempts", "passes"} and _dev_band in ("elevated", "extreme"):
                        _dev_extra = -5  # additional caution for defenders
                    _pre_dev = prediction.get("confidenceScore", 50)
                    _adj_dev = max(45, _pre_dev + _dev_delta + _dev_extra)
                    prediction["confidenceScore"] = _adj_dev

                    _src_note = f"{_dev_n} settled picks" if _dev_src == "learned" else f"default/{_dev_n} picks"
                    _def_note = " Defender pass extra-sensitive to possession." if _is_def_dev and req.propType in {"pass_attempts", "passes"} else ""
                    _alert = (
                        f"LINE DEVIATION [{_dev_band.upper()}]: Line {req.line} is {_dev_pct}% "
                        f"{'above' if _dev_intel.get('direction') == 'above' else 'below'} model projection {_dev_proj} — "
                        f"historical {rec.upper()} hit rate in this band: {_dev_hit_rate}% ({_src_note}).{_def_note}"
                    )
                    prediction["tacticalAlerts"] = prediction.get("tacticalAlerts", []) + [_alert]
                    prediction["lineDeviationBand"] = _dev_band
                    prediction["lineDeviationPct"]  = _dev_pct
                    prediction["lineDeviationHitRate"] = _dev_hit_rate

                    if abs(_adj_dev - _pre_dev) >= 1:
                        print(f"[DEV GUARD] {req.playerName} {rec.upper()} {req.propType}: "
                              f"band={_dev_band} dev={_dev_pct}% hit_rate={_dev_hit_rate}% ({_src_note}) "
                              f"delta={_dev_delta} → conf {_pre_dev}→{_adj_dev}")
                elif _dev_band == "aligned":
                    # Line is near our projection — apply historical hit rate nudge
                    _pre_dev = prediction.get("confidenceScore", 50)
                    if _dev_delta > 0:
                        # Book agrees with direction — slight boost
                        _boost = min(5, _dev_delta)
                        prediction["confidenceScore"] = min(85, _pre_dev + _boost)
                        prediction["lineDeviationBand"] = "aligned"
                        if _boost > 0:
                            print(f"[DEV GUARD] {req.playerName}: aligned band +{_boost}% ({_pre_dev}→{prediction['confidenceScore']})")
                    elif _dev_delta <= -5:
                        # Historical hit rate below 50% — warn and penalize
                        _penalty = min(10, abs(_dev_delta))
                        _adj = max(48, _pre_dev - _penalty)
                        prediction["confidenceScore"] = _adj
                        prediction["lineDeviationBand"] = "aligned_warn"
                        _alert_w = (
                            f"LINE DEVIATION [ALIGNED CAUTION]: Historically this {rec.upper()} "
                            f"direction hits only {_dev_hit_rate}% ({_dev_n} settled picks) "
                            f"when line is near model projection."
                        )
                        prediction["tacticalAlerts"] = prediction.get("tacticalAlerts", []) + [_alert_w]
                        print(f"[DEV GUARD] {req.playerName}: aligned CAUTION {rec.upper()} "
                              f"hit_rate={_dev_hit_rate}% → -{_penalty}% ({_pre_dev}→{_adj})")

        except Exception as _dev_e:
            print(f"[DEV GUARD] Error: {_dev_e}")

        # ── Market Edge Calibration ───────────────────────────────────────────
        # edgeZ = (|posteriorMean - line|) / effective_std.
        # It measures how many standard deviations our projection sits away from
        # the prop line — a true measure of edge sharpness vs the market price.
        #
        # A fair prop line implies ~50% probability either side. Any deviation
        # from 50% must be justified by the magnitude of our edge relative to
        # our own uncertainty.  We apply a final calibration nudge:
        #   edgeZ ≥ 2.0 → very sharp → +7% confidence
        #   edgeZ ≥ 1.5 → sharp      → +4% confidence
        #   edgeZ ≥ 1.0 → moderate   → +2% confidence
        #   edgeZ < 0.5 → weak       → -4% confidence (marginal edge)
        #   edgeZ < 0.3 → razor thin → -7% confidence (near-random)
        # Cap: confidence stays in [45, 85] regardless.
        if real_bayes:
            _ez = real_bayes.get("edgeZ", 0)
            if _ez >= 2.0:
                _edge_nudge = 7
            elif _ez >= 1.5:
                _edge_nudge = 4
            elif _ez >= 1.0:
                _edge_nudge = 2
            elif _ez >= 0.5:
                _edge_nudge = 0
            elif _ez >= 0.3:
                _edge_nudge = -4
            else:
                _edge_nudge = -7
            if _edge_nudge != 0:
                _pre_edge_conf = prediction.get("confidenceScore", 50)
                prediction["confidenceScore"] = max(45, min(85, _pre_edge_conf + _edge_nudge))
                if prediction["confidenceScore"] != _pre_edge_conf:
                    print(f"[EDGE CAL] edgeZ={_ez:.2f} nudge={_edge_nudge:+d}% "
                          f"({_pre_edge_conf} → {prediction['confidenceScore']})")
            prediction["edgeZ"] = round(_ez, 2)

        # ── UNDERDOG GK SCORE-EFFECT RISK ────────────────────────────────────
        # When a GK belongs to a HEAVY underdog team, losing badly forces constant
        # ball recycling through the GK: defenders back-pass under pressure, team
        # chases the game → GK volume EXPLODES above model estimates.
        # Only fires for true heavy underdogs (< 25% implied win probability,
        # i.e. decimal odds ≥ 4.0). The 25-35% "clear underdog" tier was removed
        # because it produced false positives (e.g. Borgognono actual=17 vs boost→OVER).
        # ─────────────────────────────────────────────────────────────────────
        if _is_gk_dom and req.propType in {"pass_attempts", "passes"} and match_odds:
            _bo = (match_odds or {}).get("bookmakerOdds", {})
            _home_dec = _bo.get("homeWin") or _bo.get("home")
            _away_dec = _bo.get("awayWin") or _bo.get("away")
            _gk_venue = (player_venue or req.venue or "home").lower()
            _team_dec = _home_dec if _gk_venue == "home" else _away_dec
            if _team_dec:
                try:
                    _team_dec_f = float(_team_dec)
                    _implied_prob = 1.0 / _team_dec_f if _team_dec_f > 0 else None
                    if _implied_prob is not None:
                        _current_proj = prediction.get("projectedValue", req.line)
                        _rec_now = prediction.get("recommendation", "under")
                        if _implied_prob < 0.25:
                            # Heavy underdog (≥ 4.0 decimal odds) — GK blow-up risk HIGH
                            _gk_boost = 1.20
                            _conf_cap = 50
                            _risk_label = "HEAVY UNDERDOG"
                        else:
                            _gk_boost = None
                            _conf_cap = None
                            _risk_label = None
                        if _gk_boost:
                            _boosted_proj = round(_current_proj * _gk_boost, 1)
                            prediction["projectedValue"] = _boosted_proj
                            prediction["recommendation"] = "over" if _boosted_proj > req.line else "under"
                            if _rec_now == "under" and prediction.get("confidenceScore", 50) > _conf_cap:
                                prediction["confidenceScore"] = _conf_cap
                            prediction["tacticalAlerts"] = prediction.get("tacticalAlerts", []) + [
                                f"GK SCORE-EFFECT RISK: Team is a {_risk_label} (implied {_implied_prob:.0%} win prob) — GK volume tends to spike in heavy losses via back-pass recycling"
                            ]
                            print(f"[UNDERDOG GK] {_risk_label}: implied_prob={_implied_prob:.2f}, "
                                  f"boost={_gk_boost}× {_current_proj} → {_boosted_proj} "
                                  f"(line={req.line}, conf cap={_conf_cap}%)")
                except (ValueError, TypeError, ZeroDivisionError):
                    pass
        # ─────────────────────────────────────────────────────────────────────

        # Recalculate confidence level after guards
        cs = prediction.get("confidenceScore", 50)
        prediction["confidenceLevel"] = "Very High" if cs >= 80 else "High" if cs >= 70 else "Medium" if cs >= 55 else "Low"

        # HARD GUARD: recommendation MUST match the FINAL projected value vs line
        final_proj_cal = prediction.get("projectedValue", req.line)
        prediction["recommendation"] = "over" if final_proj_cal > req.line else "under"

        # Use the single corrected team name resolved early (trusts req.teamName from scan)
        player_team_display = corrected_team_name
        prediction["player"] = {
            "id": req.playerId,
            "name": req.playerName,
            "team": player_team_display,
            "position": display_position or "Unknown",
            "role": display_role or "",
        }
        prediction["opponent"] = req.opponentName
        prediction["propType"] = req.propType
        prediction["line"] = req.line
        # Tag WC predictions so the mobile UI / settlement loop can handle them correctly
        if _is_wc:
            prediction["wcMode"] = True
        prediction.setdefault("projectedValue", req.line)
        prediction.setdefault("recommendation", "over")
        prediction.setdefault("confidenceScore", 50)
        prediction.setdefault("confidenceLevel", "Medium")
        prediction.setdefault("confidenceInterval", None)
        prediction.setdefault("recentSamples", [])
        if real_recent_samples:
            prediction["recentSamples"] = real_recent_samples
        prediction.setdefault("bayesianMetrics", {"priorMean": req.line, "momentumEffect": 0, "covariateAdjustment": 0, "reversalFlag": "stable"})

        _COUNT_STATS = {
            "pass_attempts", "passes", "shots", "shots_on_target", "tackles",
            "key_passes", "shots_assisted", "saves", "interceptions", "blocks",
            "dribbles", "dribbles_success", "fouls_drawn", "fouls_committed",
            "crosses", "clearances", "duels_won", "yellow_cards", "goals", "assists",
        }
        if req.propType in _COUNT_STATS:
            pv = prediction.get("projectedValue")
            if pv is not None:
                rounded_pv = round(pv)
                prediction["projectedValue"] = rounded_pv
                # Re-sync recommendation after rounding — round() can change the
                # integer value relative to the line (e.g. pv=1.5 line=1.5 rounds
                # to 2 via banker's rounding, but guard set "under" since 1.5 ≯ 1.5).
                prediction["recommendation"] = "over" if rounded_pv > req.line else "under"
            ci = prediction.get("confidenceInterval")
            if ci and len(ci) >= 2:
                lo = round(float(ci[0]), 1)
                hi = round(float(ci[1]), 1)
                prediction["confidenceInterval"] = [lo, hi] if hi > lo else None
            for s in prediction.get("recentSamples", []):
                if not isinstance(s, dict):
                    continue
                v = s.get("value")
                if v is not None:
                    s["value"] = int(round(v))

        # ── BAYESIAN IS FINAL — AI IS ANALYSIS ONLY ──────────────────────────
        # The Bayesian math projection is the sole source of truth for both the
        # projectedValue and the OVER/UNDER recommendation. The AI tactical
        # projection is stored for display context only and never moves the number.
        # Rationale: the 85/15 blend was causing the final projected value to cross
        # the line when the AI disagreed, silently flipping the recommendation
        # against the math. The user's money follows the math — the math decides.
        _ai_proj_raw = None
        if ai_result:
            _ai_proj_raw = ai_result.get("aiProjection") or ai_result.get("projectedValue") or None
        _bayes_final = prediction.get("projectedValue", req.line)
        prediction["bayesianComponent"] = _bayes_final
        if _ai_proj_raw and isinstance(_ai_proj_raw, (int, float)) and 0 < _ai_proj_raw < 500:
            prediction["aiProjection"] = _ai_proj_raw
            prediction["blendNote"] = f"Reverse Formula {_bayes_final} (math only) | AI tactical read: {_ai_proj_raw} (context only, not applied)"
            print(f"[MATH ONLY] Bayes={_bayes_final} locked. AI={_ai_proj_raw} stored for display only — not applied to projection.")

        # ═══════════════════════════════════════════════════════════════════
        # NARROW EDGE — GK PASS_ATTEMPTS ONLY
        # Fades the model's lean when the projection is close to the line.
        # SCOPE: ONLY fires for goalkeepers on pass_attempts props.
        # The fade pattern (tight lean lands opposite direction) was empirically
        # validated exclusively on GK pass picks. It must NEVER fire on outfield
        # players — for them a tight edge just means a close call, not a fade signal.
        #
        # HOME GK OVER threshold is widened to 12%:
        # Historical data shows home GK OVER recs on pass_attempts hit only
        # 37.5% (3/8 picks). Home teams hold more possession → fewer back-passes
        # to GK → actual runs UNDER the line. All other GKs: 8% threshold.
        #
        # SEASON AVG ANCHOR GUARD: even within the GK scope, never flip when
        # the season average independently confirms the lean by >5% beyond the line.
        # ═══════════════════════════════════════════════════════════════════
        _pass_proj = prediction.get("projectedValue", req.line)
        # Both position systems must agree on GK — _bayes_position is the early
        # DB-lookup estimate (can misfire for outfield players who have goals_saves=0
        # in logs); specific_position is the authoritative POS RESOLVE result.
        # Requiring both to agree prevents the narrow-edge flip from ever touching
        # outfield players like Victor Braga who are RBs, not GKs.
        _is_gk_pass = (
            req.propType == "pass_attempts"
            and _bayes_position.upper() in {"GK", "GOALKEEPER"}
            and specific_position.upper() in {"GK", "GOALKEEPER", "G"}
        )
        if _is_gk_pass and req.line > 0 and _pass_proj is not None:
            _edge_pct = abs(_pass_proj - req.line) / req.line * 100

            _is_home_gk_over = (
                req.venue == "home"
                and _pass_proj > req.line
            )
            _narrow_threshold = 12.0 if _is_home_gk_over else 8.0

            # Season avg anchor — block flip if season avg clearly confirms lean
            _season_avg = early_bayes.get("priorMean") if early_bayes else None
            _avg_anchor_blocks_flip = False
            _model_lean_over = _pass_proj > req.line
            if _season_avg and req.line > 0:
                _avg_edge_pct = (_season_avg - req.line) / req.line * 100
                if _model_lean_over and _avg_edge_pct > 5.0:
                    _avg_anchor_blocks_flip = True
                    print(f"[GK NARROW EDGE BLOCKED] {req.playerName}: season_avg={_season_avg} "
                          f"{_avg_edge_pct:.1f}% above line — anchor confirms OVER, no flip")
                elif not _model_lean_over and _avg_edge_pct < -5.0:
                    _avg_anchor_blocks_flip = True
                    print(f"[GK NARROW EDGE BLOCKED] {req.playerName}: season_avg={_season_avg} "
                          f"{abs(_avg_edge_pct):.1f}% below line — anchor confirms UNDER, no flip")

            # Possession context anchor: if the model is leaning UNDER because the team
            # has above-average expected possession (GK DOM POSS PENALTY fired), block the
            # narrow edge from flipping UNDER → OVER even when the season avg is over the line.
            # Example: Escandell (Oviedo HOME, 55.7% poss / 52.4% avg = ratio 1.063) → UNDER.
            # The season avg (35.3) is above the 33.5 line, but the possession context is real.
            if not _model_lean_over and match_dominance:
                _ne_exp_poss  = match_dominance.get("expectedPoss")
                _ne_team_avg  = match_dominance.get("teamSeasonAvg")
                if _ne_exp_poss and _ne_team_avg and _ne_team_avg > 0:
                    _ne_poss_ratio = _ne_exp_poss / _ne_team_avg
                    if _ne_poss_ratio > 1.05:
                        _avg_anchor_blocks_flip = True
                        print(f"[GK POSS ANCHOR] {req.playerName}: possession context "
                              f"({_ne_exp_poss:.1f}% > avg {_ne_team_avg:.1f}%, ratio={_ne_poss_ratio:.2f}) "
                              f"confirms UNDER lean — blocking flip to OVER")

            if _edge_pct < _narrow_threshold and not _avg_anchor_blocks_flip:
                _leaning = "over" if _pass_proj > req.line else "under"
                _flipped = "UNDER" if _leaning == "over" else "OVER"
                prediction["recommendation"] = _flipped
                prediction["passLeaning"] = _leaning.upper()
                _reason_tag = "[HOME GK OVER FADE]" if _is_home_gk_over else "[GK NARROW EDGE]"
                prediction["passReason"] = (
                    f"Edge only {_edge_pct:.1f}% — fading model's {_leaning.upper()} lean → {_flipped}"
                )
                print(
                    f"{_reason_tag} {req.playerName} {req.propType}: "
                    f"proj={_pass_proj}, line={req.line}, gap={_edge_pct:.1f}% → fading to {_flipped}"
                )

        # ── PROJECTION CONSISTENCY GUARD ─────────────────────────────────────────────────
        # Ensure projectedValue and recommendation can never contradict each other.
        # Any gate (GK narrow edge, home-GK fade, etc.) may flip the recommendation
        # without touching projectedValue — this guard realigns the number so the UI
        # never shows "Projection: 30, Line: 29.5 — UNDER" or vice-versa.
        #
        # FIX: use the actual Bayesian posterior mean (real_bayes["posteriorMean"])
        # instead of the hardcoded "line ± 0.5" anchor.  The old anchor caused the
        # displayed projection to track the sportsbook line perfectly (proj = line - 0.5
        # regardless of player stats) whenever a gate flipped the recommendation.
        # The posterior mean is computed independently from game logs and only lightly
        # fused with the line (20%), so it reflects the player's actual statistical level.
        _cg_rec  = str(prediction.get("recommendation", "")).lower()
        _cg_proj = prediction.get("projectedValue")
        _cg_bayes_mean = (real_bayes or {}).get("posteriorMean")
        if _cg_proj is not None and req.line and req.line > 0:
            if _cg_rec == "under" and _cg_proj > req.line:
                # Prefer the real posterior mean; fall back to line-0.5 only if unavailable
                _cg_fixed = round(_cg_bayes_mean, 1) if _cg_bayes_mean is not None else round((req.line - 0.5) * 2) / 2
                prediction["projectedValue"] = _cg_fixed
                print(f"[CONSISTENCY GUARD] {req.playerName}: projectedValue {_cg_proj} → {_cg_fixed} "
                      f"(rec=UNDER, was above line {req.line}; using posterior={'real' if _cg_bayes_mean is not None else 'line-anchor'})")
            elif _cg_rec == "over" and _cg_proj < req.line:
                _cg_fixed = round(_cg_bayes_mean, 1) if _cg_bayes_mean is not None else round((req.line + 0.5) * 2) / 2
                prediction["projectedValue"] = _cg_fixed
                print(f"[CONSISTENCY GUARD] {req.playerName}: projectedValue {_cg_proj} → {_cg_fixed} "
                      f"(rec=OVER, was below line {req.line}; using posterior={'real' if _cg_bayes_mean is not None else 'line-anchor'})")

        # ── BAYESIAN TRUTH OVERRIDE ──────────────────────────────────────────
        # By user directive: the Bayesian Monte-Carlo probability is the
        # source of truth for both direction AND displayed confidence.
        # Eight upstream branches set `recommendation` from `projection > line`,
        # which ignores the posterior distribution's variance/skew. Result: the
        # badge can say OVER while P(UNDER) > 50% (real example: Tielemans
        # 51.0 vs 50.5 line, P(UNDER)=59.4%, badge said OVER, actual landed 40).
        #
        # This block runs AFTER all upstream adjustments and BEFORE the MATH
        # LOCK + calibration so that downstream consumers (lock text, calibrator,
        # mobile UI) all see the corrected values.
        #
        # Safe-defaults for knockout variables: must be at this outer 8-space
        # scope so ALL code paths (including the async/no-logs path that skips
        # the inner `if real_bayes:` block above) reach the KNOCKOUT UNDER
        # CONFIDENCE PENALTY check below with these variables defined.
        # The inner `if real_bayes:` block may later override _final_is_knockout
        # to the correct game_situation value; these are just safe fallbacks.
        if "_final_is_knockout" not in locals():
            _final_is_knockout = False
        if "_KO_COUNT_PROPS" not in locals():
            _KO_COUNT_PROPS = {
                "pass_attempts", "passes", "shots", "shots_on_target",
                "saves", "key_passes", "crosses", "dribbles", "tackles", "clearances",
            }
        _bt_src = real_bayes if isinstance(real_bayes, dict) else (early_bayes if isinstance(early_bayes, dict) else None)
        if prediction.get("recommendation", "").upper() != "PASS" and _bt_src is not None and "pOver" in _bt_src and "pUnder" in _bt_src:
            _bt_p_over  = _bt_src["pOver"]
            _bt_p_under = _bt_src["pUnder"]
            _bt_max_pct = max(_bt_p_over, _bt_p_under)
            _bt_dir     = "over" if _bt_p_over >= _bt_p_under else "under"
            _bt_old_rec  = str(prediction.get("recommendation", "")).lower()
            _bt_old_conf = prediction.get("confidenceScore")
            _bt_new_conf = int(round(_bt_max_pct))
            _bt_new_lvl  = (
                "Very High" if _bt_max_pct >= 80
                else "High"   if _bt_max_pct >= 70
                else "Medium" if _bt_max_pct >= 55
                else "Low"
            )

            prediction["recommendation"] = _bt_dir
            prediction["confidenceScore"] = _bt_new_conf
            prediction["rawConfidence"] = _bt_new_conf
            prediction["confidenceLevel"] = _bt_new_lvl

            # Clear stale coinFlip flags from upstream guards (e.g. Guard 3a).
            # Guard 3a fires before BAYESIAN TRUTH and can set coinFlip=True on
            # any high-conf OVER. If Bayesian genuinely confirms ≥70% probability,
            # the pick is not a coin flip — clear the flag so the UI doesn't
            # show a contradictory warning.
            if _bt_max_pct >= 70.0 and prediction.get("coinFlip"):
                prediction["coinFlip"] = False
                print(
                    f"[BAYESIAN TRUTH] Cleared coinFlip — P={_bt_max_pct:.0f}% "
                    f"confirms genuine {_bt_dir.upper()} signal, not a coin flip"
                )

            # If direction flipped, align projectedValue with the new direction.
            # Use the actual Bayesian posterior mean — NOT "line ± 0.5" — so the
            # projection reflects the player's real statistical level independent
            # of what the sportsbook set the line to.
            if _bt_old_rec != _bt_dir:
                _bt_proj = prediction.get("projectedValue", req.line)
                _bt_posterior = (real_bayes or {}).get("posteriorMean")
                if _bt_dir == "under" and _bt_proj > req.line:
                    _bt_fixed = round(_bt_posterior, 1) if _bt_posterior is not None else round((req.line - 0.5) * 2) / 2
                    prediction["projectedValue"] = _bt_fixed
                    print(f"[BAYESIAN TRUTH] projectedValue flip UNDER: {_bt_proj} → {_bt_fixed} "
                          f"({'posterior' if _bt_posterior is not None else 'line-anchor'})")
                elif _bt_dir == "over" and _bt_proj < req.line:
                    _bt_fixed = round(_bt_posterior, 1) if _bt_posterior is not None else round((req.line + 0.5) * 2) / 2
                    prediction["projectedValue"] = _bt_fixed
                    print(f"[BAYESIAN TRUTH] projectedValue flip OVER: {_bt_proj} → {_bt_fixed} "
                          f"({'posterior' if _bt_posterior is not None else 'line-anchor'})")

            # ── KNOCKOUT UNDER CONFIDENCE PENALTY ────────────────────────────
            # Even after the ET projection uplift, UNDER bets in knockout games
            # carry residual extra-time risk that the normal distribution doesn't
            # fully capture (the distribution is symmetric; ET is asymmetric —
            # it only ADDS minutes, never subtracts).  Settled data: WC knockout
            # UNDER 50% hit rate.  Apply a -8pt confidence cap for UNDER bets
            # on count stats in knockout games, floor 52 so we never suppress to
            # noise levels when the edge is genuinely strong.
            if _final_is_knockout and req.propType in _KO_COUNT_PROPS and _bt_dir == "under":
                _ko_under_pre = prediction["confidenceScore"]
                _ko_under_cap = max(52, _ko_under_pre - 8)
                if _ko_under_cap < _ko_under_pre:
                    prediction["confidenceScore"] = _ko_under_cap
                    prediction["rawConfidence"]   = _ko_under_cap
                    if _ko_under_cap < 70:
                        prediction["confidenceLevel"] = "High" if _ko_under_cap >= 65 else "Medium" if _ko_under_cap >= 55 else "Low"
                    print(
                        f"[KNOCKOUT UNDER PENALTY] {req.playerName}/{req.propType}: "
                        f"conf {_ko_under_pre}% → {_ko_under_cap}% (ET risk on UNDER bets)"
                    )
            # ─────────────────────────────────────────────────────────────────

            print(
                f"[BAYESIAN TRUTH] {req.playerName}/{req.propType}: "
                f"P(OVER)={_bt_p_over}% P(UNDER)={_bt_p_under}% → "
                f"{_bt_dir.upper()} {_bt_new_conf}% ({_bt_new_lvl})"
                + (f" [FLIPPED from {_bt_old_rec.upper()} {_bt_old_conf}%]" if _bt_old_rec != _bt_dir else f" [confidence {_bt_old_conf}→{_bt_new_conf}]")
            )

            # ── SHARP SUMMARY DIRECTION GUARD ─────────────────────────────────
            # The prediction cache stores AI narrative. When BAYESIAN TRUTH pins
            # a different direction than what the AI wrote (common when the AI
            # explains OVER but Bayesian says UNDER), the sharpSummary displayed
            # to users flatly contradicts the recommendation badge.
            # Detect the conflict and replace sharpSummary with a math-based one.
            # Also purge the prediction cache so the next request regenerates
            # fresh AI text with the correct direction anchor.
            _ss_text = prediction.get("sharpSummary", "") or ""
            if _ss_text:
                _ss_lo = _ss_text.lower()
                _over_markers = ("exceed", " over ", "above the line", "more than",
                                 "surpass", "push past", "eclips", "over 46", "over 47",
                                 "over 48", "over 49", "over 50", "strong over",
                                 "projects to exceed", "will exceed")
                _under_markers = (" under ", "going under", "is under ", "stays under",
                                  "come under ", "fall under ", "land under",
                                  "below", "fewer than", "less than",
                                  "suppress", "fall short", "won't reach", "won't hit",
                                  "short of the", "not reach", "miss the line")
                _ss_has_over  = any(m in _ss_lo for m in _over_markers)
                _ss_has_under = any(m in _ss_lo for m in _under_markers)
                _ss_conflicts = (
                    (_bt_dir == "under" and _ss_has_over and not _ss_has_under) or
                    (_bt_dir == "over"  and _ss_has_under and not _ss_has_over)
                )
                if _ss_conflicts:
                    _dir_word = "UNDER" if _bt_dir == "under" else "OVER"
                    _alt_dir  = "OVER"  if _bt_dir == "under" else "UNDER"
                    _bt_proj  = prediction.get("projectedValue", req.line)
                    _p_dir    = _bt_p_under if _bt_dir == "under" else _bt_p_over
                    _proj_disp = f"{_bt_proj:.1f}" if isinstance(_bt_proj, (int, float)) else str(_bt_proj)
                    _replacement_summary = (
                        f"The Reverse Formula projects {req.playerName} to finish at "
                        f"{_proj_disp} — {_dir_word} {req.line}. The 3-layer statistical "
                        f"model gives {_p_dir:.0f}% probability the {_dir_word} lands; "
                        f"structural matchup and possession factors suppress the stat "
                        f"below the line despite the {_alt_dir.lower()} narrative in "
                        f"market commentary."
                        if _bt_dir == "under" else
                        f"The Reverse Formula projects {req.playerName} to finish at "
                        f"{_proj_disp} — {_dir_word} {req.line}. The 3-layer statistical "
                        f"model gives {_p_dir:.0f}% probability the {_dir_word} lands; "
                        f"volume and possession factors push the stat above the line "
                        f"despite the cautious market pricing."
                    )
                    prediction["sharpSummary"] = _replacement_summary
                    # Do not discard a substantive Gemini explanation when the
                    # final Bayesian pass changes direction. Gemini is called
                    # before the full posterior is available, so this can happen
                    # even though the explanation contains valuable matchup,
                    # role, manager, and game-flow evidence. Replace only the
                    # direction-bearing sections and add an authoritative
                    # reconciliation note; retain the evidence and its source.
                    _existing_td = prediction.get("tacticalBreakdown", "") or ""
                    if isinstance(_existing_td, str) and len(_existing_td.strip()) > 100:
                        _final_note = (
                            f"**Final Model Reconciliation**\n"
                            f"The completed Reverse Formula posterior is authoritative: "
                            f"{_proj_disp} {_dir_word} the {req.line} line with "
                            f"{_p_dir:.0f}% probability. The tactical evidence below is "
                            f"retained as matchup context; the final Bayesian direction "
                            f"overrides any earlier {_alt_dir} lean.\n\n"
                        )
                        # Replace a generated Verdict section when present so the
                        # first section can never contradict the recommendation.
                        _existing_td = re.sub(
                            r"\*\*Verdict\*\*.*?(?=\n\s*\*\*[A-Za-z][^*]*\*\*|\Z)",
                            (
                                f"**Verdict**\n"
                                f"The completed Reverse Formula projects {_proj_disp} "
                                f"— {_dir_word} {req.line} ({_p_dir:.0f}% probability)."
                            ),
                            _existing_td,
                            count=1,
                            flags=re.IGNORECASE | re.DOTALL,
                        )
                        # Likewise replace a generated TL;DR so the visible close
                        # of the analysis agrees with the final badge.
                        _existing_td = re.sub(
                            r"\*\*TL;DR\*\*.*?(?=\n\s*\*\*[A-Za-z][^*]*\*\*|\Z)",
                            (
                                f"**TL;DR**\n"
                                f"{_dir_word} at {_proj_disp} is the final model call "
                                f"against the {req.line} line ({_p_dir:.0f}% probability)."
                            ),
                            _existing_td,
                            count=1,
                            flags=re.IGNORECASE | re.DOTALL,
                        )
                        prediction["tacticalBreakdown"] = _final_note + _existing_td.strip()
                        prediction["aiSource"] = "gemini"
                        print(
                            f"[DIRECTION GUARD] {req.playerName}/{req.propType}: "
                            f"final rec={_bt_dir.upper()} — reconciled Gemini narrative "
                            f"without discarding tactical evidence"
                        )
                    else:
                        # No substantive AI text exists, so the normal math
                        # fallback below remains the correct source marker.
                        prediction["aiSource"] = "math"
                    # Keep the daily AI cache. The final direction is computed
                    # fresh on every request, and this same reconciliation is
                    # applied to cached prose when necessary.

            # ── LOW CONVICTION FILTER ─────────────────────────────────────────
            # When Bayesian max(P(OVER), P(UNDER)) < 57%, the model has weak
            # signal — the line is close to the projection mean and the
            # distribution straddles both sides. Cap confidence at 58% and
            # expose lowConviction=True so the UI can surface a warning.
            # Fires inside the _bt_src guard so it only runs when Bayesian
            # data is available.
            # Note: threshold raised from 57% (was 60%) so only genuinely weak
            # signals are penalised — WC/tournament props with limited history
            # were hitting this too aggressively at 60%.
            _bt_conv = max(_bt_p_over, _bt_p_under)
            if _bt_conv < 57.0 and prediction.get("recommendation", "").upper() != "PASS":
                prediction["lowConviction"] = True
                if (prediction.get("confidenceScore") or 0) > 58:
                    prediction["confidenceScore"] = 58
                    prediction["confidenceLevel"] = "Medium"
                print(f"[LOW CONV] {req.playerName}/{req.propType}: P(max)={_bt_conv:.1f}% < 57% → capped 58% Medium")
            else:
                prediction.setdefault("lowConviction", False)

            # ── SMALL SAMPLE CONFIDENCE DECAY ─────────────────────────────────
            # With n<10 game logs the Bayesian prior is unreliable — small samples
            # produce artificially tight distributions. Decay confidence toward a
            # safe floor: n<6 → cap 57%, n<10 → cap 63%, n<15 → cap 68%.
            # Runs inside the _bt_src guard so it only fires with real Bayesian data.
            _bt_n = (_bt_src or {}).get("sampleSize", 20)
            if _bt_n is not None:
                _ss_cap = 57 if _bt_n < 6 else (63 if _bt_n < 10 else (68 if _bt_n < 15 else 100))
                if _ss_cap < 100 and (prediction.get("confidenceScore") or 0) > _ss_cap:
                    prediction["confidenceScore"] = _ss_cap
                    prediction["confidenceLevel"] = "High" if _ss_cap >= 70 else "Medium" if _ss_cap >= 55 else "Low"
                    print(f"[SMALL SAMPLE] {req.playerName}/{req.propType}: n={_bt_n} → cap {_ss_cap}%")

        # ── HARD BLOCK: clearances OVER (0% hit rate, runs AFTER Bayesian Truth) ──
        # Bayesian Truth may still output OVER because the prior over-projects
        # clearances for forwards/midfielders who rarely block crosses.
        # 0W/11L empirical record → hard-flip to UNDER and set 60% Medium.
        if req.propType == "clearances" and prediction.get("recommendation", "").lower() == "over":
            prediction["recommendation"]  = "under"
            prediction["confidenceScore"] = 60
            prediction["confidenceLevel"] = "Medium"
            prediction["coinFlip"]        = False
            prediction["tacticalAlerts"]  = prediction.get("tacticalAlerts", []) + [
                "CLEARANCES OVER → UNDER (data override): 0% hit rate on 11 settled clearances OVER picks. "
                "Books set these lines precisely; clearances are volatile and hard to project. "
                "Clearances UNDER is viable."
            ]
            if prediction.get("projectedValue") is not None and prediction["projectedValue"] > req.line:
                prediction["projectedValue"] = round((req.line - 0.5) * 2) / 2
            print(f"[HARD BLOCK] clearances OVER → forced UNDER 60% for {req.playerName}")

        # ── RECENT PASS-PROP SUPPRESSION ─────────────────────────────────────
        # All-time safety is useful for context, but it can hide a short-lived
        # league/role regime change.  For soccer passing props only, suppress
        # a direction when the most-specific rolling bucket has at least ten
        # deduplicated settled events and is at or below a 50% hit rate.
        # Do not reverse the recommendation: PASS means the model has no
        # actionable side and protects both the UI and direct save callers.
        if (
            str(req.sport or "").lower() == "soccer"
            and req.propType in {"pass_attempts", "passes"}
            and prediction.get("recommendation", "").upper() in {"OVER", "UNDER"}
        ):
            _pass_dir = prediction["recommendation"].upper()
            _pass_position = (
                prediction.get("player", {}).get("position")
                or prediction.get("position")
                or req.positionOverride
                or ""
            )
            _recent_pass = _get_recent_prop_safety(
                req.propType,
                _pass_dir,
                league_id=req.leagueId,
                position=_pass_position,
            )
            if (
                _recent_pass
                and _recent_pass.get("hitRate") is not None
                and _recent_pass.get("hitRate") <= 50
            ):
                _pass_rate = _recent_pass["hitRate"]
                _pass_n = _recent_pass["n"]
                prediction["recommendation"] = "PASS"
                prediction["passReason"] = (
                    f"PASS — recent {_pass_dir} pass-prop results in this "
                    f"league/role bucket are {_pass_rate:.0f}% "
                    f"({_pass_n} settled events)."
                )
                prediction["skipReason"] = "RECENT_PASS_PROP_BUCKET"
                prediction["skipDetails"] = {
                    "direction": _pass_dir,
                    "hitRate": _pass_rate,
                    "sampleSize": _pass_n,
                    "windowDays": 45,
                    "minSampleSize": 10,
                }
                prediction["confidenceScore"] = 50
                prediction["rawConfidence"] = 50
                prediction["confidenceLevel"] = "Low"
                prediction["coinFlip"] = False
                prediction["tacticalAlerts"] = prediction.get("tacticalAlerts", []) + [
                    prediction["passReason"] + " No opposite-side recommendation is implied."
                ]
                print(
                    f"[PASS PROP SUPPRESSION] {req.playerName}/{req.propType}: "
                    f"{_pass_dir} {_pass_rate:.1f}% ({_pass_n}n, rolling 45d)"
                )

        # ── MARKET DISTANCE GUARD ────────────────────────────────────────────
        # When our projection is ≥35% away from the market line, the prior is
        # likely contaminated (stale seasons, old-club era, position mismatch).
        # Normally caps confidence at 55% and surfaces a caution alert.
        #
        # BAYESIAN TRUTH exception: if the Bayesian Monte-Carlo gives P ≥ 80%
        # in the winning direction, the posterior distribution already accounts
        # for data quality — its mass is solidly on one side for structural
        # reasons (e.g., a 65% possession team vs a 35% expected opponent).
        # In that case, cap confidence only to 72% (not 55%) and still show
        # the caution alert, but don't override a strong Bayesian signal.
        _mg_proj = prediction.get("projectedValue", req.line)
        _market_distance_fired = False
        if req.line > 0 and _mg_proj is not None:
            _mg_gap_pct = abs(_mg_proj - req.line) / req.line * 100
            if _mg_gap_pct >= 35:
                _market_distance_fired = True
                _mg_pre = prediction.get("confidenceScore", 50)
                # Check how strong the Bayesian posterior is
                _mg_bt_p = max(
                    (real_bayes or {}).get("pOver", 0),
                    (real_bayes or {}).get("pUnder", 0)
                )
                _mg_bt_strong = _mg_bt_p >= 80.0   # posterior is genuinely confident
                _mg_cap = 72 if _mg_bt_strong else 55
                if _mg_pre > _mg_cap:
                    prediction["confidenceScore"] = _mg_cap
                    prediction["confidenceLevel"] = (
                        "High" if _mg_cap >= 70 else "Medium"
                    )
                    prediction["tacticalAlerts"] = prediction.get("tacticalAlerts", []) + [
                        f"MARKET DISTANCE: Model projects {_mg_proj} but line is {req.line} "
                        f"({_mg_gap_pct:.0f}% gap) — prior may be from wrong club era or "
                        f"stale season. Treat with caution."
                    ]
                    _bt_note = f" (Bayesian P={_mg_bt_p:.0f}% — soft cap {_mg_cap}%)" if _mg_bt_strong else ""
                    print(f"[MARKET DIST] {req.playerName}: proj={_mg_proj} line={req.line} "
                          f"gap={_mg_gap_pct:.0f}% → confidence capped {_mg_pre}→{_mg_cap}%{_bt_note}")
                else:
                    prediction["tacticalAlerts"] = prediction.get("tacticalAlerts", []) + [
                        f"MARKET DISTANCE: Model projects {_mg_proj} but line is {req.line} "
                        f"({_mg_gap_pct:.0f}% gap) — verify this is the right era/club data."
                    ]
                    print(f"[MARKET DIST] {req.playerName}: gap={_mg_gap_pct:.0f}% — alert only "
                          f"(conf={_mg_pre}% already ≤ cap={_mg_cap}%)")
                # When the gap is extreme (≥60%), direction is structural not a coin-flip.
                if _mg_gap_pct >= 60 and prediction.get("coinFlip"):
                    prediction["coinFlip"] = False
                    print(f"[MARKET DIST] gap={_mg_gap_pct:.0f}% ≥ 60% — coinFlip cleared; "
                          f"direction is structural, not a near-line coin-flip")

        # ── POSITION-SPECIFIC CONFIDENCE CAP ─────────────────────────────────────
        # Empirical data (1291 settled picks) shows certain position+prop+direction
        # combos are systematically overconfident at 90-99%. Hard caps prevent the
        # model from displaying confidence the data does not support.
        #   GK  OVER pass_attempts: 41.4% actual hit rate → cap 72%
        #   CAM OVER pass_attempts:  0.0% actual hit rate → cap 62%
        #   CM  OVER pass_attempts: 41.7% actual hit rate → cap 68%
        #   CDM OVER pass_attempts: 33.3% actual hit rate → cap 68%
        _POS_CONF_CAPS = {
            ("GK",  "pass_attempts", "over"): 72,
            ("CAM", "pass_attempts", "over"): 62,
            ("CM",  "pass_attempts", "over"): 68,
            ("CDM", "pass_attempts", "over"): 68,
        }
        _cap_key = (
            str(prediction.get("position") or "").upper(),
            req.propType,
            str(prediction.get("recommendation") or "").lower(),
        )
        _pos_cap = _POS_CONF_CAPS.get(_cap_key)
        if _pos_cap is not None and (prediction.get("confidenceScore") or 0) > _pos_cap:
            _pre_pos_cap = prediction["confidenceScore"]
            prediction["confidenceScore"] = _pos_cap
            prediction["rawConfidence"]   = _pos_cap
            prediction["confidenceLevel"] = "High" if _pos_cap >= 70 else "Medium"
            print(
                f"[POS CAP] {prediction.get('position')} {req.propType} "
                f"{prediction.get('recommendation')}: {_pre_pos_cap}% → {_pos_cap}%"
            )

        # MATH LOCK removed — pure math analysis is built below, no AI text to patch.
        _lock_final_rec = str(prediction.get("recommendation", "")).upper()  # PASS, OVER, or UNDER
        _lock_proj_raw  = prediction.get("projectedValue", req.line)
        _lock_proj_str  = str(int(_lock_proj_raw)) if _lock_proj_raw == int(_lock_proj_raw) else f"{_lock_proj_raw:.1f}"
        # ── EDGE & SAFETY RATING (DATA-DRIVEN) ───────────────────────────────────
        # Computed AFTER BAYESIAN TRUTH + MATH LOCK — all values are final here.
        # edgeRating  : SHARP EDGE | EDGE | MARGINAL | NO EDGE
        # safetyRating: SAFE | MODERATE | RISKY | AVOID
        #
        # Safety comes from the LIVE prop_safety_cache which queries all settled
        # picks in MongoDB, computing empirical hit rates per (propType, direction).
        # Cache refreshes every 6h — always reflects the latest real data.
        # Edge is projection-margin-based, gated by the historical safety.
        _er_rec   = prediction.get("recommendation", "").upper()
        _er_prop  = req.propType or ""
        _er_conf  = prediction.get("confidenceScore", 50)
        _er_proj  = prediction.get("projectedValue", req.line)
        _er_line  = req.line or 0
        _er_coin  = prediction.get("coinFlip", False)

        try:
            _er_margin = abs(float(_er_proj) - float(_er_line)) if _er_line > 0 else 0
        except (TypeError, ValueError):
            _er_margin = 0

        # ── Safety: pull from live DB-derived cache ───────────────────────────
        # Hierarchical v2: league-aware + position-aware. Falls back to global
        # prop+direction when the child bucket is too thin.
        _er_position = prediction.get("player", {}).get("position") or prediction.get("position") or ""
        if _er_rec == "PASS":
            _safety_rating = "AVOID"
            _er_hit_rate   = None
            _er_n          = 0
        elif _er_coin:
            _safety_rating = "RISKY"
            _er_hit_rate   = None
            _er_n          = 0
        else:
            _ps = _get_prop_safety(_er_prop, _er_rec, league_id=req.leagueId, position=_er_position)
            if _ps:
                _safety_rating = _ps["safety"]
                _er_hit_rate   = _ps["hitRate"]
                _er_n          = _ps["n"]
            else:
                # No historical data for this prop+direction — treat as unknown risk
                _safety_rating = "RISKY"
                _er_hit_rate   = None
                _er_n          = 0

        # ── Edge: projection margin, gated by safety ──────────────────────────
        # SHARP EDGE requires both a meaningful margin AND a historically SAFE prop.
        # AVOID/RISKY props are capped at MARGINAL even with large projection margins.
        # MARKET DISTANCE override: when the line is structurally far from projection
        # (gap ≥ 60%), even a RISKY prop gets at least MARGINAL if margin is large —
        # the line itself is the anomaly, not the model.
        _er_market_dist = _market_distance_fired and _er_margin >= 10
        if _er_rec == "PASS" or _er_coin:
            _edge_rating = "NO EDGE"
        elif _safety_rating == "AVOID":
            # Historically proven loser — never call it an edge
            _edge_rating = "NO EDGE"
        elif _safety_rating == "SAFE":
            if _er_margin >= 5 and _er_conf >= 60:
                _edge_rating = "SHARP EDGE"
            elif _er_margin >= 3 and _er_conf >= 55:
                _edge_rating = "EDGE"
            elif _er_margin >= 2:
                _edge_rating = "MARGINAL"
            else:
                _edge_rating = "NO EDGE"
        elif _safety_rating == "MODERATE":
            if _er_margin >= 8 and _er_conf >= 65:
                _edge_rating = "SHARP EDGE"
            elif _er_margin >= 5 and _er_conf >= 58:
                _edge_rating = "EDGE"
            elif _er_margin >= 3:
                _edge_rating = "MARGINAL"
            else:
                _edge_rating = "NO EDGE"
        else:  # RISKY
            # Even with a big margin, a historically unreliable prop can't be SHARP EDGE
            if _er_margin >= 10 and _er_conf >= 70:
                _edge_rating = "MARGINAL"
            elif _er_market_dist:
                # Market distance override: the line itself is the anomaly, margin is real
                _edge_rating = "MARGINAL"
            else:
                _edge_rating = "NO EDGE"

        # Market distance structural override: when the projection gap is extreme (≥60%
        # from line) AND the model has a clear direction, floor the edge at MARGINAL
        # regardless of safety rating. The line is the outlier — not the model.
        if _er_market_dist and _edge_rating == "NO EDGE":
            _edge_rating = "MARGINAL"
            print(f"[MARKET DIST EDGE] margin={_er_margin:.1f} gap≥60% → floor to MARGINAL")

        prediction["edgeRating"]        = _edge_rating
        prediction["safetyRating"]      = _safety_rating
        prediction["propHistoricalRate"] = _er_hit_rate  # expose to frontend
        prediction["propHistoricalN"]   = _er_n
        print(
            f"[EDGE/SAFETY] {_er_rec} {_er_prop}: margin={_er_margin:.1f} conf={_er_conf} "
            f"hist={_er_hit_rate}% (n={_er_n}) → {_edge_rating} / {_safety_rating}"
        )

        # ── AVOID / RISKY CONFIDENCE SUPPRESSION ─────────────────────────────────
        # The Bayesian engine computes P(OVER)/P(UNDER) from the prior + momentum,
        # but has no knowledge of the prop+direction's historical hit rate.
        # When prop safety has enough evidence that a direction is a loser, we
        # suppress the Bayesian confidence to match the empirical reality.
        #
        # AVOID (≤44% hit rate, n≥5): cap confidence at the empirical rate (floor 50)
        # RISKY (45–57%, n≥8):        soft −5 pp reduction when confidence > 65
        #
        # This runs AFTER edgeRating is computed (which used the pre-suppression
        # confidence) so the NO EDGE label is already correct for AVOID props.
        if prediction.get("recommendation", "").upper() not in ("PASS",):
            _sup_conf = prediction.get("confidenceScore", 50)
            if _safety_rating == "AVOID" and _er_hit_rate is not None:
                _avoid_cap = max(50, round(_er_hit_rate))
                if _sup_conf > _avoid_cap:
                    prediction["confidenceScore"] = _avoid_cap
                    prediction["confidenceLevel"] = (
                        "Medium" if _avoid_cap >= 55 else "Low"
                    )
                    print(
                        f"[AVOID CAP] {_er_prop} {_er_rec}: bayesian={_sup_conf}% "
                        f"→ capped at empirical {_avoid_cap}% (n={_er_n})"
                    )
            elif _safety_rating == "RISKY" and _er_hit_rate is not None and _sup_conf > 65:
                _risky_adj = max(55, _sup_conf - 5)
                if _risky_adj != _sup_conf:
                    prediction["confidenceScore"] = _risky_adj
                    prediction["confidenceLevel"] = (
                        "High" if _risky_adj >= 70 else "Medium"
                    )
                    print(
                        f"[RISKY ADJ] {_er_prop} {_er_rec}: {_sup_conf}% → {_risky_adj}% "
                        f"(RISKY hist={_er_hit_rate:.1f}%)"
                    )

            # ── LINE-DEVIATION HARD CAP ────────────────────────────────────────
            # Independent of the prop-safety cache above (which is keyed on
            # propType+direction across ALL deviation levels and can have no
            # data for a specific combo, silently skipping the cap). The
            # line-deviation band hit rate measures a different, always-
            # available signal: how this exact "book strongly disagrees with
            # our projection" scenario has historically resolved. Guard 5
            # only applies a damped proportional nudge (e.g. 44% hit rate →
            # ~-3 to -8 pts), which can leave confidence sitting in "High"
            # territory (e.g. 72%) for a bet that has historically LOST more
            # than it won. Never show High/Strong confidence on a sub-50%
            # empirical hit rate — cap it the same way AVOID does above.
            _dev_band_final = prediction.get("lineDeviationBand")
            _dev_hit_final  = prediction.get("lineDeviationHitRate")
            if _dev_band_final in ("elevated", "extreme") and _dev_hit_final is not None:
                _post_conf = prediction.get("confidenceScore", 50)
                if _dev_hit_final <= 44:
                    _dev_cap = max(50, round(_dev_hit_final))
                    if _post_conf > _dev_cap:
                        prediction["confidenceScore"] = _dev_cap
                        prediction["confidenceLevel"] = "Medium" if _dev_cap >= 55 else "Low"
                        print(
                            f"[DEV CAP] {_er_prop} {_er_rec}: {_post_conf}% → {_dev_cap}% "
                            f"({_dev_band_final} band hist={_dev_hit_final}%)"
                        )
                elif _dev_hit_final < 50 and _post_conf > 65:
                    _dev_adj = max(55, _post_conf - 5)
                    if _dev_adj != _post_conf:
                        prediction["confidenceScore"] = _dev_adj
                        prediction["confidenceLevel"] = "High" if _dev_adj >= 70 else "Medium"
                        print(
                            f"[DEV ADJ] {_er_prop} {_er_rec}: {_post_conf}% → {_dev_adj}% "
                            f"({_dev_band_final} band hist={_dev_hit_final}%)"
                        )
        # ── CALIBRATION ALERT SUPPRESSION ────────────────────────────────────────
        # Walk-forward Brier score and calibration gap scans run every 6h and
        # flag sports/props where the model systematically over-states confidence.
        # When a sport or prop is flagged AVOID/RISKY at the walk-forward level,
        # apply the same cap logic as prop_safety above so users never see
        # "High" confidence from a statistically over-confident sport.
        if prediction.get("recommendation", "").upper() not in ("PASS",):
            try:
                from calibration_alerts import get_calibration_alert as _get_cal_alert
                _cal_alert = _get_cal_alert(
                    str(getattr(req, "sport", "") or ""),
                    str(getattr(req, "propType", "") or ""),
                )
                if _cal_alert and _cal_alert.get("alertLevel") in ("AVOID", "RISKY"):
                    _cal_level  = _cal_alert["alertLevel"]
                    _cal_brier  = _cal_alert.get("brierScore")
                    _cal_gap    = _cal_alert.get("maxOverGapPp")
                    _cal_src    = _cal_alert.get("source", "sport")
                    _post_conf  = prediction.get("confidenceScore", 50)
                    if _cal_level == "AVOID":
                        # Cap at 60 — systematic over-confidence should never show as High/Strong
                        _cal_cap = 60
                        if _post_conf > _cal_cap:
                            prediction["confidenceScore"] = _cal_cap
                            prediction["confidenceLevel"] = "Medium"
                            prediction["calibrationAlertApplied"] = {
                                "level": _cal_level, "source": _cal_src,
                                "brierScore": _cal_brier, "maxOverGapPp": _cal_gap,
                                "capApplied": _cal_cap, "from": _post_conf,
                            }
                            print(
                                f"[CAL AVOID] {_cal_src} alert: {_post_conf}% → capped {_cal_cap}% "
                                f"(Brier={_cal_brier}, gap={_cal_gap}pp)"
                            )
                    elif _cal_level == "RISKY" and _post_conf > 70:
                        # Soft −5pp reduction when walk-forward shows mild over-confidence
                        _cal_adj = max(60, _post_conf - 5)
                        if _cal_adj != _post_conf:
                            prediction["confidenceScore"] = _cal_adj
                            prediction["confidenceLevel"] = "High" if _cal_adj >= 70 else "Medium"
                            prediction["calibrationAlertApplied"] = {
                                "level": _cal_level, "source": _cal_src,
                                "brierScore": _cal_brier, "maxOverGapPp": _cal_gap,
                                "capApplied": _cal_adj, "from": _post_conf,
                            }
                            print(
                                f"[CAL RISKY] {_cal_src} alert: {_post_conf}% → {_cal_adj}% "
                                f"(Brier={_cal_brier}, gap={_cal_gap}pp)"
                            )
            except Exception as _cal_err:
                print(f"[CAL ALERT SUP] error: {_cal_err}")
        # ─────────────────────────────────────────────────────────────────────────────

        prediction.setdefault("probabilityCurve", [])
        prediction.setdefault("reasoning", "Analysis based on available data.")
        prediction.setdefault("tacticalInsights", "")

        # OVERRIDE: Lock matchupOverview to REAL DATA so it never fluctuates between predictions
        real_matchup = prediction.get("matchupOverview", {})
        # 1. Possession: Use MATCH DOMINANCE model (symmetric — always computed from HOME perspective)
        if match_dominance.get("homePoss") is not None:
            real_matchup["expectedPossession"] = {
                "home": match_dominance["homePoss"],
                "away": match_dominance["awayPoss"]
            }
        elif team_fixture_stats or opponent_fixture_stats:
            def avg_possession(stats_list):
                vals = []
                for s in (stats_list or []):
                    p = s.get("possession")
                    if p is not None:
                        try:
                            vals.append(float(str(p).replace("%", "")))
                        except (ValueError, TypeError):
                            pass
                return round(sum(vals) / len(vals), 0) if vals else None
            team_poss = avg_possession(team_fixture_stats)
            opp_poss = avg_possession(opponent_fixture_stats)
            if player_venue == "home":
                fb_home_avg = team_poss
                fb_away_avg = opp_poss
            elif player_venue == "away":
                fb_home_avg = opp_poss
                fb_away_avg = team_poss
            else:
                # Neutral venue: use _is_home (tiebreaker already applied above)
                # so the home/away orientation is consistent between both team scans.
                fb_home_avg = team_poss if _is_home else opp_poss
                fb_away_avg = opp_poss if _is_home else team_poss
            if fb_home_avg is not None and fb_away_avg is not None:
                fb_away_concedes = 100 - fb_away_avg
                fb_home_poss = round((fb_home_avg + fb_away_concedes) / 2.0 + 2.5)
                fb_home_poss = min(75, max(30, fb_home_poss))
                fb_away_poss = 100 - fb_home_poss
                real_matchup["expectedPossession"] = {"home": fb_home_poss, "away": fb_away_poss}
            elif fb_home_avg is not None:
                fb_home_poss = round(min(75, max(30, fb_home_avg + 2.5)))
                real_matchup["expectedPossession"] = {"home": fb_home_poss, "away": 100 - fb_home_poss}
            elif fb_away_avg is not None:
                fb_away_poss = round(min(75, max(30, fb_away_avg - 2.5)))
                real_matchup["expectedPossession"] = {"home": 100 - fb_away_poss, "away": fb_away_poss}
        # 2. Moneyline + favorite from real odds data
        # IMPORTANT: API-Football's americanOdds.home/away keys refer to whoever
        # API-Football designates as "home" in the fixture — which may NOT match
        # the player_venue sent by the frontend (especially for neutral-venue
        # competitions like the World Cup where "home" is arbitrary).
        # We normalise here so moneyline.home ALWAYS = the team in
        # real_matchup["homeTeam"] and moneyline.away ALWAYS = awayTeam.
        if match_odds:
            # _is_home is the canonical truth: it tells us whether the player's team
            # is the fixture's home team (from playerIsHome in match_odds). When
            # _is_home is True, real_matchup.homeTeam == player's team == fixture home,
            # so moneyline.home should use the fixture's home odds directly.
            # When _is_home is False, real_matchup.homeTeam == opponent == fixture away,
            # so moneyline.home must use the fixture's away odds (swap required).
            _pred_home_is_fixture_home = _is_home

            if match_odds.get("americanOdds"):
                ao = match_odds["americanOdds"]
                if ao.get("home") and ao.get("away") and ao.get("draw"):
                    if _pred_home_is_fixture_home:
                        home_ml, away_ml = str(ao["home"]), str(ao["away"])
                    else:
                        # Prediction home is the fixture away team — swap odds.
                        home_ml, away_ml = str(ao["away"]), str(ao["home"])
                    real_matchup["moneyline"] = {
                        "home": home_ml,
                        "draw": str(ao["draw"]),
                        "away": away_ml,
                    }
            elif match_odds.get("bookmakerOdds"):
                bo = match_odds["bookmakerOdds"]
                h, d, a = bo.get("homeWin", ""), bo.get("draw", ""), bo.get("awayWin", "")
                if h and d and a and h != "N/A" and d != "N/A" and a != "N/A":
                    if _pred_home_is_fixture_home:
                        real_matchup["moneyline"] = {"home": h, "draw": d, "away": a}
                    else:
                        real_matchup["moneyline"] = {"home": a, "draw": d, "away": h}
            # Normalize favorite to prediction's perspective (not fixture's)
            if match_odds.get("favorite"):
                raw_fav = match_odds["favorite"]  # "home" or "away" in fixture terms
                if _pred_home_is_fixture_home:
                    real_matchup["favorite"] = raw_fav
                else:
                    # Flip: fixture "home" maps to prediction "away" and vice versa
                    real_matchup["favorite"] = "away" if raw_fav == "home" else "home"
        # 3. Game type from real stats — deterministic classification
        # ALWAYS override AI's expectedGameType. AI invents values like
        # "KNOCKOUT (HIGH-PRESSURE, END-TO-END)" for group stage matches.
        # Valid labels: open | cagey | one-sided | high-tempo only.
        _poss_diff = abs((real_matchup.get("expectedPossession", {}).get("home", 50)) - 50)
        if team_fixture_stats and opponent_fixture_stats:
            def avg_stat(stats_list, key):
                vals = [s.get(key) for s in stats_list if s.get(key) is not None]
                return sum(vals) / len(vals) if vals else 0
            team_avg_shots = avg_stat(team_fixture_stats, "totalShots")
            opp_avg_shots = avg_stat(opponent_fixture_stats, "totalShots")
            combined_shots = team_avg_shots + opp_avg_shots
            if combined_shots >= 28:
                real_matchup["expectedGameType"] = "open"
            elif combined_shots <= 18:
                real_matchup["expectedGameType"] = "cagey"
            elif _poss_diff >= 12:
                real_matchup["expectedGameType"] = "one-sided"
            else:
                real_matchup["expectedGameType"] = "high-tempo" if combined_shots >= 23 else "cagey"
        else:
            # No shot data — classify purely from possession imbalance
            if _poss_diff >= 14:
                real_matchup["expectedGameType"] = "one-sided"
            elif _poss_diff >= 6:
                real_matchup["expectedGameType"] = "open"
            else:
                real_matchup["expectedGameType"] = "open"

        # Final sanitisation — reject any value AI invented that isn't in the approved set
        _valid_game_types = {"open", "cagey", "one-sided", "high-tempo"}
        if real_matchup.get("expectedGameType", "open").lower().strip() not in _valid_game_types:
            real_matchup["expectedGameType"] = "one-sided" if _poss_diff >= 12 else "open"

        # 4. Always set team names from request data (deterministic)
        # Use _is_home (which is now based on playerIsHome from the fixture) to
        # determine which team is the home team, NOT the user's venue input.
        real_matchup["homeTeam"] = player_team_display if _is_home else req.opponentName
        real_matchup["awayTeam"] = req.opponentName if _is_home else player_team_display

        # Expose team/opponent names at the TOP LEVEL of the response so the
        # frontend can use them directly without digging into matchupOverview.
        # The frontend checks prediction.opponentName, prediction.teamName,
        # prediction.homeTeam, and prediction.awayTeam — these were missing,
        # causing "HOME" / "AWAY" fallback labels in the possession bar.
        prediction["opponentName"] = req.opponentName or ""
        prediction["teamName"]     = corrected_team_name or req.teamName or ""
        prediction["homeTeam"]     = real_matchup["homeTeam"]
        prediction["awayTeam"]     = real_matchup["awayTeam"]
        prediction["isHome"]       = _is_home
        if match_odds and match_odds.get("fixtureId"):
            prediction["fixtureId"] = match_odds["fixtureId"]
            prediction["fixtureDate"] = match_odds.get("matchDate", "")
            prediction["fixtureTeamId"] = match_odds.get("fixtureTeamId")
            prediction["fixtureOpponentId"] = match_odds.get("fixtureOpponentId")

        # 5. Deterministic keyMatchupFactor — MUST align with computed possession numbers.
        # Overrides AI-generated text to prevent contradictions like "Liverpool dominates
        # possession" when the model computed PSG at 62% and Liverpool at 38%.
        _ep = real_matchup.get("expectedPossession", {})
        _home_p = _ep.get("home", 50)
        _away_p = _ep.get("away", 50)
        _home_team = real_matchup.get("homeTeam", "Home")
        _away_team = real_matchup.get("awayTeam", "Away")
        _game_type = real_matchup.get("expectedGameType", "open")
        _game_type_label = {"open": "open", "cagey": "cagey", "one-sided": "one-sided", "high-tempo": "high-tempo"}.get(_game_type, _game_type)
        if _home_p >= 58:
            _kmf = f"{_home_team}'s possession dominance ({_home_p:.0f}%) expected to control tempo at home"
        elif _away_p >= 58:
            _kmf = f"{_away_team}'s possession superiority ({_away_p:.0f}%) expected to control the ball despite playing away"
        elif _home_p >= 53:
            _kmf = f"{_home_team} holds home possession edge ({_home_p:.0f}% vs {_away_p:.0f}%) in an {_game_type_label} game"
        elif _away_p >= 53:
            _kmf = f"{_away_team} holds possession edge ({_away_p:.0f}% vs {_home_p:.0f}%) in an {_game_type_label} game despite being away"
        else:
            _kmf = f"Balanced possession expected ({_home_p:.0f}% vs {_away_p:.0f}%) — {_game_type_label} game"
        real_matchup["keyMatchupFactor"] = _kmf

        prediction["matchupOverview"] = real_matchup

        # ── OPPONENT DEFENSIVE PROFILE ────────────────────────────────────────
        # How does the opponent's recent defending compare to what this player
        # typically produces?  Uses the opponentAllowedAvg already computed by
        # the Bayesian engine (weighted average of what this position/prop gets
        # vs this opponent in recent matches) vs. the player's prior mean.
        # Only attach when we have ≥3 opponent samples so the signal is reliable.
        if real_bayes and req.propType not in {"goals", "assists"}:
            _op_allowed  = real_bayes.get("opponentAllowedAvg")
            _op_n        = int(real_bayes.get("opponentAllowedSamples") or 0)
            _op_baseline = real_bayes.get("priorMean")
            if _op_allowed is not None and _op_baseline and _op_baseline > 0 and _op_n >= 3:
                _op_diff_pct = round((_op_allowed - _op_baseline) / _op_baseline * 100, 1)
                _op_tier = (
                    "elite suppressor" if _op_diff_pct <= -30 else
                    "strong suppressor" if _op_diff_pct <= -15 else
                    "slight suppressor" if _op_diff_pct <= -5  else
                    "elite leak"        if _op_diff_pct >= 30  else
                    "notable leak"      if _op_diff_pct >= 15  else
                    "slight lean"       if _op_diff_pct >= 5   else
                    "neutral"
                )
                _op_is_neg = _op_diff_pct < 0
                prediction["opponentProfile"] = {
                    "allowedAvg":    round(_op_allowed, 1),
                    "playerBaseline": round(_op_baseline, 1),
                    "diffPct":       _op_diff_pct,
                    "tier":          _op_tier,
                    "sampleSize":    _op_n,
                    "propType":      req.propType,
                    "description": (
                        f"{req.opponentName} allows {abs(_op_diff_pct):.0f}% "
                        f"{'fewer' if _op_is_neg else 'more'} "
                        f"{req.propType.replace('_', ' ')} than baseline "
                        f"to this position ({_op_n} games)"
                    ),
                }
                print(
                    f"[OPP PROFILE] {req.playerName}/{req.propType}: "
                    f"allowed={_op_allowed:.1f} baseline={_op_baseline:.1f} "
                    f"diff={_op_diff_pct:+.1f}% → {_op_tier} (n={_op_n})"
                )

        # Add match context (competition name, round) for frontend display
        if match_odds:
            mc = {}
            if match_odds.get("matchLeague"):
                mc["league"] = match_odds["matchLeague"]
            if match_odds.get("matchRound"):
                mc["round"] = match_odds["matchRound"]
            if match_odds.get("matchDate"):
                mc["date"] = match_odds["matchDate"][:10]
            if mc:
                prediction["matchContext"] = mc

        # Expose situation engine result to frontend (second leg, aggregate, injuries)
        if game_situation:
            _agg = game_situation.get("aggregate", {})
            prediction["gameSituation"] = {
                "isKnockout": game_situation.get("isKnockout", False),
                "isSecondLeg": game_situation.get("isSecondLeg", False),
                "aggregate": {
                    "firstLegFound": _agg.get("firstLegFound", False),
                    "firstLegScore": _agg.get("firstLegScore", ""),
                    "homeTeamAggregate": _agg.get("homeTeamAggregate", 0),
                    "awayTeamAggregate": _agg.get("awayTeamAggregate", 0),
                    "goalDeficit": _agg.get("goalDeficit", 0),
                    "homeTeamTrailing": _agg.get("homeTeamTrailing", False),
                    "mustWinByGoals": _agg.get("mustWinByGoals", 0),
                },
                "injuries": game_situation.get("injuries", {}).get("summaryText", ""),
                "matchStakes": game_situation.get("matchStakes"),
            }

        # DATA QUALITY INDICATOR — flag when API data might be unreliable
        total_game_logs = len(player_game_logs)
        _is_synthetic = total_game_logs > 0 and all(g.get("synthetic") for g in player_game_logs)
        gl_target_field_map_check = {
            "pass_attempts": "passes_total", "shots": "shots_total", "shots_on_target": "shots_on",
            "tackles": "tackles_total", "key_passes": "passes_key", "shots_assisted": "passes_key",
            "saves": "goals_saves", "goalie_saves": "goals_saves",
            "interceptions": "tackles_interceptions", "blocks": "tackles_blocks",
            "dribbles": "dribbles_attempts", "fouls_drawn": "fouls_drawn",
            "crosses": "passes_crosses", "clearances": "tackles_clearances",
            "goals": "goals_total", "assists": "goals_assists",
            "duels_won": "duels_won", "yellow_cards": "cards_yellow",
            "fouls_committed": "fouls_committed",
        }
        target_check = gl_target_field_map_check.get(req.propType, "passes_total")
        games_with_data = sum(1 for g in player_game_logs if g.get(target_check) is not None)
        games_with_none = total_game_logs - games_with_data
        if _is_synthetic:
            prediction["dataQuality"] = {
                "level": "medium",
                "message": f"No recent match logs cached. Analysis based on season averages ({total_game_logs} appearances).",
                "gamesWithData": games_with_data,
                "totalGames": total_game_logs,
            }
        elif total_game_logs > 0 and games_with_none / total_game_logs >= 0.3:
            prediction["dataQuality"] = {
                "level": "limited",
                "message": f"API data incomplete — {games_with_none} of {total_game_logs} recent games missing {req.propType} stats. Cross-referenced sources used for analysis.",
                "gamesWithData": games_with_data,
                "totalGames": total_game_logs,
            }
        elif total_game_logs < 3:
            prediction["dataQuality"] = {
                "level": "low",
                "message": f"Only {total_game_logs} game logs available. Limited sample size for accurate projection.",
                "gamesWithData": games_with_data,
                "totalGames": total_game_logs,
            }
        else:
            prediction["dataQuality"] = {
                "level": "good",
                "message": "",
                "gamesWithData": games_with_data,
                "totalGames": total_game_logs,
            }

        # Compact analysis summary for the UI
        prop_key = req.propType or ""
        if prop_key == "shots_on_target":
            stat_label = "Shots on Target"
        elif prop_key == "saves":
            stat_label = "Goalkeeper Saves"
        else:
            stat_label = {
                "pass_attempts": "Pass Attempts",
                "shots": "Shots",
                "tackles": "Tackles",
                "key_passes": "Key Passes",
                "saves": "Saves",
                "interceptions": "Interceptions",
                "blocks": "Blocks",
                "dribbles": "Dribbles",
                "fouls_drawn": "Fouls Drawn",
            }.get(prop_key, prop_key.replace("_", " ").title())

        venue_samples = [g for g in player_game_logs if g.get("venue") == player_venue and g.get(target_check) is not None]
        venue_avg = round(sum((g.get(target_check) or 0) for g in venue_samples) / len(venue_samples), 2) if venue_samples else None
        opp_allowed_avg = None
        opp_stat_field_map = {
            # ONLY include props where team-level opponent stats are actually meaningful.
            # Pass-volume props (pass_attempts, passes, key_passes, crosses, dribbles) are
            # possession-dependent: opponent's totalPasses tells us nothing about what
            # they concede to individual players in those categories — removed.
            "shots": "totalShots",
            "shots_on_target": "shotsOnTarget",
            "saves": "shotsOnTarget",
            "tackles": "totalShots",
            "interceptions": "totalShots",
            "blocks": "totalShots",
            "fouls_drawn": "fouls",
            "clearances": "totalShots",
        }
        opp_stat_key = opp_stat_field_map.get(req.propType)
        if opp_stat_key and opponent_fixture_stats:
            opp_vals = [g.get(opp_stat_key) for g in opponent_fixture_stats if g.get(opp_stat_key) is not None]
            if opp_vals:
                try:
                    opp_vals_num = [float(str(v).replace("%", "")) for v in opp_vals]
                    opp_allowed_avg = round(sum(opp_vals_num) / len(opp_vals_num), 1)
                except (ValueError, TypeError):
                    pass

        # For saves props: the "Opponent Profile OPP AVG" must reflect the avg saves
        # that other GKs at the same venue made vs this opponent — not the opponent's SOT.
        # positionComparison already sampled exactly that (same position, same venue, same opponent).
        if req.propType == "saves" and position_comp_data and position_comp_data.get("avgStatValue"):
            opp_allowed_avg = round(float(position_comp_data["avgStatValue"]), 1)

        prediction["analysisSummary"] = {
            "statLabel": stat_label,
            "venue": player_venue,
            "venueSampleSize": len(venue_samples),
            "venueAverage": venue_avg,
            "opponentAllowedAverage": opp_allowed_avg,
            "goalkeeperSaveRate": gk_formula_data.get("gkSaveRate") if gk_formula_data else None,
            "goalkeeperSaveSample": gk_formula_data.get("gkSampleSize") if gk_formula_data else None,
            "opponentShotsOnTarget": gk_formula_data.get("opponentAvgSOT") if gk_formula_data else None,
        }

        # ── PURE MATH ANALYSIS — no AI paragraphs ────────────────────────────────
        _m_rec    = prediction.get("recommendation", "over").upper()
        _m_proj   = prediction.get("projectedValue", req.line)
        _m_conf   = prediction.get("confidenceScore", 50)
        _m_lvl    = prediction.get("confidenceLevel", "Medium")
        _m_proj_s = str(int(_m_proj)) if _m_proj == int(_m_proj) else f"{_m_proj:.1f}"
        _m_line_s = str(int(req.line)) if req.line == int(req.line) else f"{req.line:.1f}"
        _m_edge   = round(abs(_m_proj - req.line), 1)

        _m_rb     = real_bayes or {}
        _m_pover  = _m_rb.get("pOver", 50)
        _m_punder = _m_rb.get("pUnder", 50)
        _m_pwin   = max(_m_pover, _m_punder)
        _m_mom    = _m_rb.get("momentumLabel", "STABLE")
        _m_rev    = _m_rb.get("reversalFlag", "stable").upper()
        _m_cov    = _m_rb.get("covariateAdjustment", 0) or 0
        _m_prior  = _m_rb.get("priorMean") or (early_bayes.get("priorMean") if early_bayes else None) or "—"

        # Verdict line
        _m_dir_lbl = "clearing" if _m_rec == "OVER" else ("within noise of" if _m_rec == "PASS" else "falling short of")
        _m_verdict = (
            f"**Verdict** — Reverse Formula projects **{_m_proj_s}**, {_m_dir_lbl} the {_m_line_s} line "
            f"({_m_rec} | {_m_pwin:.0f}% | edge: {_m_edge})."
        )

        # Math Engine numbers block
        _m_math = (
            f"**Math Engine**\n"
            f"Projection: {_m_proj_s}  |  Line: {_m_line_s}  |  Edge: {_m_edge}\n"
            f"P(OVER): {_m_pover:.0f}%  |  P(UNDER): {_m_punder:.0f}%\n"
            f"Season avg: {_m_prior}  |  Covariate adj: {_m_cov:+.1f}\n"
            f"Momentum: {_m_mom}  |  Reversal flag: {_m_rev}  |  Confidence: {_m_conf}% ({_m_lvl})"
        )

        # Game Log section (reuse pre-parsed data from wave2_supplement)
        _m_log_str = ""
        _gl_d2 = wave2_supplement.get("playerGameLogs", {}) if wave2_supplement else {}
        _gl_g2 = _gl_d2.get("games", [])
        if _gl_g2:
            import re as _re_ml2
            _gl_fmt2 = []
            for _gs2 in _gl_g2[-8:]:
                _mm2 = _re_ml2.match(r"(\d{4}-(\d{2})-(\d{2})) vs (.+?) \((.+?), (\d+)min\): (.+)", _gs2)
                if _mm2:
                    _gl_fmt2.append(f"{_mm2.group(7)} vs {_mm2.group(4)} ({_mm2.group(5)}, {_mm2.group(6)}min)")
                else:
                    _gl_fmt2.append(_gs2)
            _gl_avg2   = _gl_d2.get("rawAvg", "—")
            _gl_h_avg2 = _gl_d2.get("homeAvg", "—")
            _gl_a_avg2 = _gl_d2.get("awayAvg", "—")
            _gl_n2     = _gl_d2.get("sampleSize", len(_gl_fmt2))
            _m_log_str = (
                f"**Game Log** ({req.propType}, last {len(_gl_fmt2)} games)\n"
                + " | ".join(_gl_fmt2) + "\n"
                + f"Season avg: {_gl_avg2}  |  Home avg: {_gl_h_avg2}  |  Away avg: {_gl_a_avg2}  |  n={_gl_n2}"
            )

        # Hit Rate section
        _m_hr_str = ""
        _hr2 = _gl_d2.get("hitRates") if _gl_d2 else None
        if _hr2:
            _m_hr_str = f"**Hit Rate**\n{_hr2.get('summary', '')}"

        # Opponent Profile
        _m_opp_parts = []
        if position_comp_data and position_comp_data.get("avgStatValue"):
            _opp_avg2 = position_comp_data["avgStatValue"]
            _opp_n2   = position_comp_data.get("sampleSize", 0)
            _opp_pos2 = position_comp_data.get("positionShort", "position")
            _m_opp_parts.append(
                f"{req.opponentName} allows {_opp_avg2:.1f} {req.propType} "
                f"to {_opp_pos2}s ({_opp_n2} matchups)"
            )
        if h2h_data:
            _h2h_v2 = [g.get("stat_value") or g.get("statValue") for g in h2h_data
                       if g.get("stat_value") or g.get("statValue")]
            if _h2h_v2:
                _h2h_avg2 = round(sum(_h2h_v2) / len(_h2h_v2), 1)
                _m_opp_parts.append(f"H2H avg: {_h2h_avg2} ({len(_h2h_v2)} games vs {req.opponentName})")
        if _m_opp_parts:
            _m_opp_str = "**Opponent Profile**\n" + "  |  ".join(_m_opp_parts)
        else:
            _m_opp_str = ""

        # Scenarios block
        _m_sp2 = prediction.get("scenarioProbabilities", {}) or {}
        _m_scen_str = ""
        if _m_sp2 and any(_m_sp2.get(k) is not None for k in ("best", "base", "worst")):
            _s_best = round((_m_sp2.get("best") or 0) * 100)
            _s_base = round((_m_sp2.get("base") or 0) * 100)
            _s_wrst = round((_m_sp2.get("worst") or 0) * 100)
            _m_scen_str = f"**Scenarios**\nBest: {_s_best}%  |  Base: {_s_base}%  |  Worst: {_s_wrst}%"

        # TL;DR
        _m_tldr = (
            f"**TL;DR** — {_m_proj_s} {_m_rec} {_m_line_s}  |  "
            f"P({_m_rec}): {_m_pwin:.0f}%  |  Edge: {_m_edge}  |  "
            f"{_m_conf}% confidence ({_m_lvl})"
        )

        # ── Assemble the math engine block (always computed — used as footer
        #    when AI succeeded, or as full breakdown when AI failed).
        _m_sections = [_m_verdict, _m_math]
        if _m_log_str:  _m_sections.append(_m_log_str)
        if _m_hr_str:   _m_sections.append(_m_hr_str)
        if _m_opp_str:  _m_sections.append(_m_opp_str)
        if _m_scen_str: _m_sections.append(_m_scen_str)
        _m_sections.append(_m_tldr)
        _m_full_block = "\n\n".join(_m_sections)

        _m_ev_note = ""
        if position_comp_data and position_comp_data.get("avgStatValue"):
            _m_ev_note = (
                f" Opponent allows {position_comp_data['avgStatValue']:.1f} "
                f"to {position_comp_data.get('positionShort','pos')}s "
                f"({position_comp_data.get('sampleSize',0)} matchups)."
            )
        _m_sharp_summary = (
            f"Reverse Formula: {_m_proj_s} {_m_rec} {_m_line_s} "
            f"(P({_m_rec}): {_m_pwin:.0f}%, edge: {_m_edge})."
            f"{_m_ev_note}"
        )

        _ai_td = prediction.get("tacticalBreakdown", "")
        if not isinstance(_ai_td, str):
            _ai_td = json.dumps(_ai_td) if _ai_td else ""
        _ai_ss = prediction.get("sharpSummary", "")
        if not isinstance(_ai_ss, str):
            _ai_ss = json.dumps(_ai_ss) if _ai_ss else ""

        if _ai_td and len(_ai_td.strip()) > 100:
            # ── AI produced a real narrative — keep it, append math footer ──
            prediction["tacticalBreakdown"] = _ai_td.strip() + "\n\n---\n" + _m_math + "\n" + _m_tldr
            prediction["aiSource"] = "gemini"
            # Keep AI's sharpSummary if it's non-empty and substantive
            if not (_ai_ss and len(_ai_ss.strip()) > 20):
                prediction["sharpSummary"] = _m_sharp_summary
            print(f"[AI SUMMARY] Using AI tacticalBreakdown ({len(_ai_td)} chars) + math footer appended")
        else:
            # ── AI failed or returned empty — fall back to pure-math breakdown ──
            prediction["tacticalBreakdown"] = _m_full_block
            prediction["sharpSummary"] = _m_sharp_summary
            prediction["aiSource"] = "math"
            print(f"[PURE MATH] AI summary absent — using math-only tacticalBreakdown ({len(_m_full_block)} chars)")

        # ── Game Script — attach computed scenario probabilities + script analysis
        # The gameScript engine uses Poisson(λ_h) × Poisson(λ_a) to forecast likely
        # match scenarios (draw, low_scoring, high_scoring, open_close, blowouts).
        # Settled data revealed: draw/blowout predictions are unreliable (0%/44% hit).
        # We apply a "smart remap" that spreads draw prob into low_scoring/open_close
        # and blowout prob into high_scoring, so the engine surfaces the macro
        # buckets we actually nail (high / low / open = 100% accuracy).
        if _scenario_probs and _scenario_probs.get("available"):
            _raw_probs = {k: v for k, v in _scenario_probs.items() if k.startswith("P_")}
            # Smart remap: collapse unreliable micro-buckets into reliable macro ones
            _smart = {
                "P_low_scoring": (
                    _raw_probs.get("P_low_scoring", 0)
                    + _raw_probs.get("P_draw", 0) * 0.83   # 82.7% of draws are low-scoring
                ),
                "P_open_close": (
                    _raw_probs.get("P_open_close", 0)
                    + _raw_probs.get("P_draw", 0) * 0.17   # 17.2% of draws are high-scoring
                ),
                "P_high_scoring": (
                    _raw_probs.get("P_high_scoring", 0)
                    + _raw_probs.get("P_home_blowout", 0) * 0.53  # 53.2% of home_blowouts are high-scoring
                    + _raw_probs.get("P_away_blowout", 0) * 0.50   # similar pattern for away
                ),
                "P_home_blowout": _raw_probs.get("P_home_blowout", 0) * 0.47,
                "P_away_blowout": _raw_probs.get("P_away_blowout", 0) * 0.50,
                "P_draw": 0.0,  # draw probability fully absorbed into low/open
            }
            # Renormalise
            _total = sum(_smart.values())
            if _total > 0:
                for k in _smart:
                    _smart[k] /= _total
            # Pick dominant macro script
            _macro = {k[2:]: v for k, v in _smart.items() if not k.startswith("P_draw")}
            _dominant = max(_macro, key=_macro.get)
            _dom_prob = round(_macro[_dominant], 3)

            _script_labels = {
                "low_scoring":   "LOW-SCORING MATCH",
                "high_scoring":  "HIGH-SCORING MATCH",
                "open_close":    "OPEN MATCH",
                "home_blowout":  "HOME DOMINANT",
                "away_blowout":  "AWAY DOMINANT",
            }
            _script_colors = {
                "low_scoring":   "#6B7280",
                "high_scoring":  "#39FF14",
                "open_close":    "#60A5FA",
                "home_blowout":  "#FBBF24",
                "away_blowout":  "#FBBF24",
            }

            prediction["gameScript"] = {
                "key_finding": _script_labels.get(_dominant, "OPEN MATCH"),
                "scenarios": [
                    {"name": k.replace("_", " ").title(), "probability": round(v, 3)}
                    for k, v in sorted(_macro.items(), key=lambda x: -x[1])
                    if v > 0.01
                ],
                "dominant": _dominant,
                "dominant_probability": _dom_prob,
                "color": _script_colors.get(_dominant, "#60A5FA"),
                "expected_total_goals": _scenario_probs.get("expectedTotal"),
                "implied_home": _scenario_probs.get("impliedHome"),
                "implied_away": _scenario_probs.get("impliedAway"),
                "implied_draw": _scenario_probs.get("impliedDraw"),
                "raw_scenarios": [
                    {"name": k[2:].replace("_", " ").title(), "probability": round(v, 3)}
                    for k, v in sorted(_raw_probs.items(), key=lambda x: -x[1])
                    if v > 0.01
                ],
                "smart_remap": _scenario_priors_result is not None,
            }
        else:
            prediction["gameScript"] = {"key_finding": "Game script unavailable", "scenarios": []}

        # Attach player disambiguation candidates when the name was ambiguous
        if _player_candidates:
            prediction["playerCandidates"] = _player_candidates

        # Save to MongoDB
        prediction["_created"] = datetime.now(timezone.utc).isoformat()
        prediction["_request"] = req.model_dump()
        if match_odds and match_odds.get("fixtureId"):
            prediction["_request"]["fixtureId"] = match_odds["fixtureId"]

        # Attach match stat data for frontend heat maps/visualizations
        if team_fixture_stats:
            prediction["teamMatchStats"] = team_fixture_stats
        if opponent_fixture_stats:
            prediction["opponentMatchStats"] = opponent_fixture_stats
        if historical_data.get("h2hPlayerStats"):
            prediction["h2hPlayerStats"] = historical_data["h2hPlayerStats"]
        if historical_data.get("playerGameLogs"):
            prediction["playerGameLogs"] = historical_data["playerGameLogs"]
        elif player_game_logs:
            # Safety net: historical_data path missed — rebuild from final player_game_logs
            _pgl_target_map = {
                "pass_attempts": "passes_total", "shots": "shots_total", "shots_on_target": "shots_on",
                "tackles": "tackles_total", "key_passes": "passes_key",
                "saves": "goals_saves", "goalie_saves": "goals_saves",
                "interceptions": "tackles_interceptions", "blocks": "tackles_blocks",
                "dribbles": "dribbles_attempts", "fouls_drawn": "fouls_drawn",
                "crosses": "passes_crosses", "clearances": "tackles_clearances",
                "goals": "goals_total", "assists": "goals_assists",
            }
            _pgl_tf = _pgl_target_map.get(req.propType, "passes_total")
            _pgl_vals = [g.get(_pgl_tf) for g in player_game_logs if g.get(_pgl_tf) is not None]
            _pgl_home = [v for g, v in zip(player_game_logs, _pgl_vals) if g.get("venue") == "home" and g.get(_pgl_tf) is not None]
            _pgl_away = [v for g, v in zip(player_game_logs, _pgl_vals) if g.get("venue") == "away" and g.get(_pgl_tf) is not None]
            _pgl_summary = {
                "games": player_game_logs,
                "targetProp": req.propType,
                "sampleSize": len(_pgl_vals),
            }
            if _pgl_vals:
                _pgl_summary["rawAvg"] = round(sum(_pgl_vals) / len(_pgl_vals), 2)
            if _pgl_home:
                _pgl_summary["homeAvg"] = round(sum(_pgl_home) / len(_pgl_home), 2)
            if _pgl_away:
                _pgl_summary["awayAvg"] = round(sum(_pgl_away) / len(_pgl_away), 2)
            if _pgl_vals and req.line:
                _pgl_over = sum(1 for v in _pgl_vals if v > req.line)
                _pgl_under = sum(1 for v in _pgl_vals if v < req.line)
                _pgl_summary["hitRates"] = {
                    "overHits": _pgl_over, "underHits": _pgl_under,
                    "overPct": round(_pgl_over / len(_pgl_vals) * 100, 1),
                    "underPct": round(_pgl_under / len(_pgl_vals) * 100, 1),
                    "total": len(_pgl_vals),
                }
            prediction["playerGameLogs"] = _pgl_summary
            print(f"[SAFETY NET] playerGameLogs rebuilt from {len(player_game_logs)} logs for {req.playerName}")
        if gk_formula_data:
            prediction["gkFormula"] = gk_formula_data
        # positionComparison stored but not surfaced directly; used for opponent profile below

        # ── OPPONENT DEFENSIVE PROFILE ───────────────────────────────────────
        # Derived from position-comparison data already fetched above.
        # Quantifies how many of this stat the opponent allows per game to
        # same-position players, versus the player's own season average.
        try:
            _pcd = position_comp_data if isinstance(position_comp_data, dict) else {}
            _pcd_players = _pcd.get("players") or []
            _pcd_n = int(_pcd.get("sampleSize") or len(_pcd_players) or 0)
            _pcd_avg = None
            if _pcd.get("avgStatValue") is not None:
                _pcd_avg = float(_pcd["avgStatValue"])
            elif _pcd_players:
                _pcd_vals = [p.get("statValue") for p in _pcd_players if p.get("statValue") is not None]
                if _pcd_vals:
                    _pcd_avg = round(sum(_pcd_vals) / len(_pcd_vals), 1)
            if _pcd_avg is not None and _pcd_n >= 2:
                _player_s_avg = wave2_supplement.get("playerGameLogs", {}).get("rawAvg")
                _odf_delta_pct = None
                _odf_favorable = None
                if _player_s_avg and float(_player_s_avg) > 0:
                    _odf_delta_pct = round((float(_pcd_avg) / float(_player_s_avg) - 1) * 100, 1)
                    _odf_favorable = _odf_delta_pct > 0
                prediction["opponentDefensiveProfile"] = {
                    "opponent": req.opponentName,
                    "propType": req.propType,
                    "position": (prediction.get("player") or {}).get("position") or player_position or "",
                    "avgAllowed": _pcd_avg,
                    "sampleSize": _pcd_n,
                    "vsPlayerSeasonAvg": _odf_delta_pct,
                    "isFavorable": _odf_favorable,
                    "playerSeasonAvg": float(_player_s_avg) if _player_s_avg else None,
                }
        except Exception as _odf_err:
            print(f"[OPP DEF PROFILE] failed: {_odf_err}")

        # ── MANAGER CONTEXT ──────────────────────────────────────────────────────
        try:
            if _manager_ctx:
                prediction["managerContext"] = {
                    **_manager_ctx,
                    "logSplitInfo": _manager_split_info if "_manager_split_info" in vars() else {},
                    "possessionDrift": _manager_possession_drift if "_manager_possession_drift" in vars() else {},
                }
        except Exception as _mc_err:
            print(f"[MANAGER CONTEXT] failed: {_mc_err}")

        # ── FINAL PASS-PROJECTION CALIBRATION (SHADOW BY DEFAULT) ───────────
        # This is deliberately the only projection-calibration boundary.  It
        # runs after Bayesian Truth, H2H, scenario, odds, and route-level
        # guards, so the extractor measures the projection users actually saw.
        # PASS suppression and confidence calibration remain separate concerns.
        if (
            str(req.sport or "").lower() == "soccer"
            and req.propType in {"pass_attempts", "passes"}
            and str(prediction.get("recommendation") or "").lower() in {"over", "under"}
        ):
            try:
                from pass_projection_calibration import ensure_loaded, lookup

                await ensure_loaded(db, datetime.now(timezone.utc))
                _cal_position = (
                    prediction.get("player", {}).get("position")
                    or prediction.get("position")
                    or specific_position
                    or req.positionOverride
                    or ""
                )
                _cal_role = (
                    prediction.get("player", {}).get("role")
                    or prediction.get("role")
                    or player_role
                    or req.roleOverride
                    or ""
                )
                _cal_mean = prediction.get("projectedValue")
                _pass_calibration = lookup(
                    req.leagueId,
                    _cal_position,
                    _cal_role,
                    str(prediction.get("recommendation") or "").lower(),
                    float(_cal_mean) if _cal_mean is not None else None,
                )
                _cal_metrics = prediction.setdefault("bayesianMetrics", {})
                _cal_metrics["passProjectionCalibration"] = _pass_calibration

                if _pass_calibration.get("applied"):
                    _corrected_mean = round(
                        float(_cal_mean) * _pass_calibration["multiplier"], 1
                    )
                    _record_projection_factor(
                        "pass_projection_calibration",
                        "Learned pass-projection calibration",
                        _cal_mean,
                        _corrected_mean,
                        inputs={
                            "multiplier": _pass_calibration.get("multiplier"),
                            "bucket": _pass_calibration.get("bucket"),
                            "mode": _pass_calibration.get("mode"),
                        },
                        sample_size=_pass_calibration.get("n"),
                        multiplier=_pass_calibration.get("multiplier"),
                        reason="Applied only when the learned walk-forward calibration bucket is live.",
                    )
                    prediction["projectedValue"] = _corrected_mean
                    prediction["recommendation"] = (
                        "over" if _corrected_mean > req.line else "under"
                    )
                    _pass_calibration["appliedValue"] = _corrected_mean
                    _cal_metrics["passProjectionCalibration"] = _pass_calibration
                    print(
                        f"[PASS PROJECTION CAL] applied {_cal_mean} → {_corrected_mean} "
                        f"bucket={_pass_calibration.get('bucket')} "
                        f"n={_pass_calibration.get('n')}"
                    )
            except Exception as _pass_cal_err:
                prediction.setdefault("bayesianMetrics", {})[
                    "passProjectionCalibration"
                ] = {
                    "found": False,
                    "mode": os.environ.get("PASS_PROJECTION_CALIBRATION_MODE", "shadow"),
                    "applied": False,
                    "error": str(_pass_cal_err)[:240],
                }
                print(f"[PASS PROJECTION CAL] application failed: {_pass_cal_err}")

        # ── FINAL EDGE-GAP RECOMPUTE ─────────────────────────────────────
        # The engine computes edgeGap from its own posterior, but several
        # post-engine guards (dominance, consistency, GK risk) can still
        # mutate `prediction["projectedValue"]`. Refresh the surfaced
        # gap/band so the UI pills always reflect the FINAL projection.
        try:
            _final_pv = prediction.get("projectedValue")
            _final_line = prediction.get("line") or req.line
            if _final_pv is not None and _final_line and _final_line > 0:
                _gap_abs = round(float(_final_pv) - float(_final_line), 2)
                _gap_pct = round((_gap_abs / float(_final_line)) * 100, 1)
                if abs(_gap_pct) >= 20:
                    _band = "DEEP"
                elif abs(_gap_pct) >= 10:
                    _band = "STRONG"
                elif abs(_gap_pct) >= 5:
                    _band = "MODERATE"
                else:
                    _band = "THIN"
                bm = prediction.setdefault("bayesianMetrics", {})
                bm["edgeGapAbs"]  = _gap_abs
                bm["edgeGapPct"]  = _gap_pct
                bm["edgeGapBand"] = _band
        except Exception as _eg_err:
            print(f"[EDGE GAP RECOMPUTE] failed: {_eg_err}")

        # ── EMPIRICAL CONFIDENCE CALIBRATION ──────────────────────────
        # When the calibration table has enough data (n≥30 per bucket), replace
        # the displayed confidenceScore with the empirical hit rate — but ONLY
        # downward. We never boost confidence via calibration; we only correct
        # overconfidence. This preserves the Bayesian direction (over/under) while
        # making the displayed number match what the data actually shows.
        try:
            from confidence_calibration import calibrate as _calibrate
            _raw_conf = prediction.get("confidenceScore")
            if _raw_conf is not None:
                prediction.setdefault("rawConfidence", _raw_conf)
                _calibrated = _calibrate(
                    req.propType,
                    float(_raw_conf),
                    prediction.get("recommendation", "").upper() or None,
                    line=req.line,
                    league_id=req.leagueId,
                    position=prediction.get("player", {}).get("position") or prediction.get("position") or None,
                    role=prediction.get("player", {}).get("role") or prediction.get("role") or None,
                )
                if _calibrated is not None:
                    _calibrated_rounded = round(_calibrated)
                    prediction["calibratedConfidence"] = _calibrated_rounded
                    if _calibrated < float(_raw_conf):
                        # Empirical rate is lower than Bayesian → system is
                        # overconfident for this bucket. Correct the display.
                        prediction["confidenceScore"] = _calibrated_rounded
                        prediction["confidenceLevel"] = (
                            "Very High" if _calibrated_rounded >= 80
                            else "High"   if _calibrated_rounded >= 70
                            else "Medium" if _calibrated_rounded >= 55
                            else "Low"
                        )
                        print(
                            f"[CONF CALIB] {req.propType}: bayesian={_raw_conf}% "
                            f"→ empirical={_calibrated_rounded}% (overconfidence corrected)"
                        )
                    else:
                        print(
                            f"[CONF CALIB] {req.propType}: bayesian={_raw_conf}% "
                            f"empirical={_calibrated_rounded}% (no correction needed)"
                            + (f" [line={req.line}]" if req.line else "")
                        )
        except Exception as _calib_err:
            print(f"[CONF CALIB] application failed: {_calib_err}")

        # Opponent-specific samples are useful context, but 1–2 meetings are
        # not enough to justify Very High confidence. Keep the broad empirical
        # calibration intact and only cap the display when this unusually thin
        # matchup signal is present.
        try:
            _bm_final = prediction.get("bayesianMetrics") or {}
            _opp_sample_final = int(_bm_final.get("opponentAllowedSamples") or 0)
            _final_conf = float(prediction.get("confidenceScore") or 0)
            if (
                req.propType not in {"goals", "assists"}
                and 0 < _opp_sample_final < 3
                and _final_conf > 72
            ):
                prediction["confidenceScore"] = 72
                prediction["confidenceLevel"] = "High"
                prediction["tacticalAlerts"] = prediction.get("tacticalAlerts", []) + [
                    f"THIN OPPONENT SAMPLE: opponent-specific evidence uses only "
                    f"{_opp_sample_final} matchup(s); confidence capped at 72%."
                ]
                print(
                    f"[THIN OPP SAMPLE] {req.playerName}/{req.propType}: "
                    f"n={_opp_sample_final}, confidence {_final_conf:.0f}%→72%"
                )
        except Exception as _thin_sample_err:
            print(f"[THIN OPP SAMPLE] application failed: {_thin_sample_err}")

        # ── WORLD CUP CALIBRATION TRACKING ──────────────────────────────
        # The World Cup happens once every 4 years, so there's almost no settled-pick
        # history for "World Cup knockout" specifically — the calibration table above
        # is trained overwhelmingly on domestic-league picks. Flag it honestly rather
        # than let a WC pick display the same false precision as a league pick, and
        # keep it isolated (isWorldCup on the saved doc) so its own sample can build.
        try:
            if (req.leagueId or 0) == 1:
                prediction["isWorldCup"] = True
                _wc_conf = prediction.get("confidenceScore")
                if _wc_conf is not None and _wc_conf >= 75:
                    prediction["confidenceScore"] = 75
                    prediction["confidenceLevel"] = "High"
                prediction["tacticalAlerts"] = prediction.get("tacticalAlerts", []) + [
                    "World Cup pick: confidence is capped conservatively — there isn't enough "
                    "settled World Cup history yet to fully trust the model's calibration here."
                ]
        except Exception as _wc_err:
            print(f"[WC CALIB] err: {_wc_err}")

        # ── AUDITABLE MODEL FACTORS ─────────────────────────────────────────
        # Keep the explanation attached to the exact prediction that produced
        # the number.  This is deliberately built at the end of the pipeline,
        # after Bayesian Truth, calibration, thin-sample guards, and the final
        # matchup override have all run.  The mobile Analysis page renders all
        # ten factors, including unavailable inputs, so "not enough data" is
        # visible instead of being silently treated as neutral evidence.
        try:
            def _af_num(value):
                try:
                    return float(value) if value is not None and str(value).strip() != "" else None
                except (TypeError, ValueError):
                    return None

            def _af_avg(values):
                nums = [_af_num(v) for v in values]
                nums = [v for v in nums if v is not None]
                return round(sum(nums) / len(nums), 2) if nums else None

            def _af_factor(fid, title, status, summary, value=None, sample_size=None,
                           impact="context", direction="neutral", detail=""):
                return {
                    "id": fid,
                    "title": title,
                    "status": status,
                    "summary": summary,
                    "value": value,
                    "sampleSize": sample_size,
                    "impact": impact,
                    "direction": direction,
                    "detail": detail,
                }

            _af_logs = [g for g in (player_game_logs or []) if isinstance(g, dict)]
            _af_team_stats = [g for g in (team_fixture_stats or []) if isinstance(g, dict)]
            _af_opp_stats = [g for g in (opponent_fixture_stats or []) if isinstance(g, dict)]
            _af_h2h = [g for g in (h2h_player_stats or []) if isinstance(g, dict)]
            _af_target_map = {
                "pass_attempts": "passes_total", "passes": "passes_total",
                "shots": "shots_total", "shots_on_target": "shots_on",
                "tackles": "tackles_total", "key_passes": "passes_key",
                "shots_assisted": "passes_key", "saves": "goals_saves",
                "goalie_saves": "goals_saves", "interceptions": "tackles_interceptions",
                "blocks": "tackles_blocks", "dribbles": "dribbles_attempts",
                "crosses": "passes_crosses", "clearances": "tackles_clearances",
                "fouls_drawn": "fouls_drawn", "fouls_committed": "fouls_committed",
                "duels_won": "duels_won", "goals": "goals_total", "assists": "goals_assists",
            }
            _af_target = _af_target_map.get(req.propType, "passes_total")
            _af_values = [_af_num(g.get(_af_target)) for g in _af_logs]
            _af_values = [v for v in _af_values if v is not None]
            _af_h2h_values = [_af_num(g.get("targetStat")) for g in _af_h2h]
            _af_h2h_values = [v for v in _af_h2h_values if v is not None]

            # Team pass-volume history.  This is separate from the player's
            # own logs: a 65-pass player on a 500-pass team is a different
            # opportunity profile from a 65-pass player on a 300-pass team.
            _af_team_passes = [
                _af_num(g.get("totalPasses")) for g in _af_team_stats
                if _af_num(g.get("totalPasses")) is not None
            ]
            _af_team_pass_avg = _af_avg(_af_team_passes)
            _af_pass_prop = req.propType in {"pass_attempts", "passes", "key_passes", "crosses"}

            # Join player logs to team fixture totals by date to estimate the
            # player's share of team passes when both sides expose the field.
            _af_team_pass_by_date = {}
            for g in _af_team_stats:
                _d = str(g.get("date") or "")[:10]
                _p = _af_num(g.get("totalPasses"))
                if _d and _p and _p > 0:
                    _af_team_pass_by_date[_d] = _p
            _af_shares = []
            for g in _af_logs:
                _d = str(g.get("date") or "")[:10]
                _p = _af_num(g.get("passes_total"))
                _tp = _af_team_pass_by_date.get(_d)
                if _p is not None and _tp and _tp > 0:
                    _af_shares.append((_p / _tp) * 100)
            _af_share_avg = _af_avg(_af_shares)

            # Possession is represented as a range, not a falsely precise
            # single point.  Use observed team possession volatility when
            # available; otherwise expose a conservative uncertainty band.
            _af_poss_obs = []
            for g in _af_team_stats:
                raw_poss = g.get("possession")
                if isinstance(raw_poss, str):
                    raw_poss = raw_poss.replace("%", "").strip()
                val = _af_num(raw_poss)
                if val is not None and 0 < val < 100:
                    _af_poss_obs.append(val)
            _af_expected_poss = _af_num((match_dominance or {}).get("expectedPoss"))
            if _af_expected_poss is None:
                _af_expected_poss = _af_num(
                    ((prediction.get("matchupOverview") or {}).get("expectedPossession") or {}).get(
                        "home" if prediction.get("isHome") else "away"
                    )
                )
            _af_poss_std = None
            if len(_af_poss_obs) >= 3:
                try:
                    _af_poss_std = float(stats_mod.stdev(_af_poss_obs))
                except Exception:
                    _af_poss_std = None
            _af_range_width = max(4.0, min(12.0, (_af_poss_std or 6.0)))
            _af_poss_range = (
                [round(max(0, _af_expected_poss - _af_range_width), 1),
                 round(min(100, _af_expected_poss + _af_range_width), 1)]
                if _af_expected_poss is not None else None
            )
            _af_real_poss = bool((match_dominance or {}).get("hasRealPossData"))

            _af_lineup = prediction.get("lineup")
            _af_lineup_status = (_af_lineup or {}).get("status") if isinstance(_af_lineup, dict) else None
            _af_role = (
                (prediction.get("player") or {}).get("role")
                or prediction.get("role")
                or locals().get("player_role")
                or ""
            )
            _af_position = (
                (prediction.get("player") or {}).get("position")
                or prediction.get("position")
                or locals().get("player_position")
                or ""
            )

            _af_game_situation = game_situation if isinstance(game_situation, dict) else {}
            _af_game_script = prediction.get("gameScript") or {}
            _af_event_warning = (
                "Pre-match estimate only: an early goal, red card, or substitution can change the pace and role."
            )
            _af_comp = {
                "leagueId": req.leagueId,
                "league": (prediction.get("matchContext") or {}).get("league") or None,
                "venue": prediction.get("venue") or req.venue,
                "opponentTier": prediction.get("currentOppTier"),
                "opponentRank": prediction.get("currentOppRank"),
                "fixtureId": prediction.get("fixtureId") or (match_odds or {}).get("fixtureId"),
            }
            _af_missing = []
            for _k, _v in {
                "fixture": _af_comp.get("fixtureId"),
                "possession": _af_expected_poss if _af_real_poss else None,
                "playerHistory": len(_af_values),
                "opponentHistory": len(_af_h2h_values),
                "lineup": _af_lineup_status,
                "teamPassVolume": _af_team_pass_avg if _af_pass_prop else True,
            }.items():
                if _v is None or _v == 0 or _v == "":
                    _af_missing.append(_k)

            _af_opponent_n = len(_af_h2h_values)
            _af_comparable_n = int((position_comp_data or {}).get("sampleSize") or 0)
            _af_history_status = "applied" if len(_af_values) >= 3 else ("warning" if _af_values else "unavailable")
            _af_opp_status = "applied" if (_af_opponent_n >= 3 or _af_comparable_n >= 3) else (
                "warning" if (_af_opponent_n or _af_comparable_n) else "unavailable"
            )
            _af_poss_status = "applied" if _af_real_poss and _af_expected_poss is not None else (
                "warning" if _af_expected_poss is not None else "unavailable"
            )
            _af_team_pass_status = "applied" if _af_team_pass_avg is not None else (
                "measured" if not _af_pass_prop else "unavailable"
            )
            _af_share_status = "applied" if _af_share_avg is not None else (
                "warning" if _af_pass_prop else "measured"
            )
            _af_lineup_status_label = "applied" if _af_lineup_status in {"confirmed", "predicted"} else (
                "warning" if _af_role or _af_position else "unavailable"
            )
            _af_script_status = "applied" if _af_game_script or _af_game_situation else "warning"
            _af_comp_status = "applied" if _af_comp.get("fixtureId") and _af_comp.get("leagueId") else "warning"
            _af_tactical_status = "applied" if _af_comparable_n >= 3 else (
                "warning" if _af_comparable_n else "unavailable"
            )

            # Evidence-quality is intentionally descriptive.  It never boosts
            # the model; it explains why confidence was capped or left alone.
            _af_applied_count = sum(
                1 for s in (
                    _af_history_status, _af_opp_status, _af_poss_status,
                    _af_team_pass_status, _af_share_status, _af_lineup_status_label,
                    _af_script_status, _af_comp_status, _af_tactical_status,
                ) if s == "applied"
            )
            _af_warning_count = sum(
                1 for s in (
                    _af_history_status, _af_opp_status, _af_poss_status,
                    _af_team_pass_status, _af_share_status, _af_lineup_status_label,
                    _af_script_status, _af_comp_status, _af_tactical_status,
                ) if s == "warning"
            )
            _af_quality_score = round(min(100, max(20, 45 + _af_applied_count * 5 - _af_warning_count * 3)))
            _af_quality_level = "high" if _af_quality_score >= 78 else ("medium" if _af_quality_score >= 58 else "low")
            _af_conf = _af_num(prediction.get("confidenceScore")) or 50
            _af_conf_cap = 72 if _af_opponent_n < 3 and _af_comparable_n < 3 else None
            _af_evidence_detail = (
                f"{_af_applied_count} of 9 evidence groups applied; "
                f"{_af_warning_count} need caution. Displayed confidence is {_af_conf:.0f}%."
            )
            if _af_conf_cap:
                _af_evidence_detail += " Opponent-specific evidence is thin, so confidence is capped conservatively."

            prediction["analysisFactors"] = [
                _af_factor(
                    "historical_depth", "Multi-season player history", _af_history_status,
                    f"{len(_af_values)} usable {_af_target.replace('_', ' ')} game logs",
                    {"games": len(_af_values), "avg": _af_avg(_af_values), "seasonsSearched": H2H_HISTORY_SEASONS},
                    len(_af_values), "projection", "neutral",
                    "Logs are filtered for usable stat evidence and minutes before entering the prior."
                ),
                _af_factor(
                    "opponent_history", "Opponent and comparable-player history", _af_opp_status,
                    f"{_af_opponent_n} direct H2H games · {_af_comparable_n} comparable matchups",
                    {"h2hAverage": _af_avg(_af_h2h_values), "h2hGames": _af_opponent_n, "comparableGames": _af_comparable_n},
                    _af_opponent_n + _af_comparable_n, "projection", "neutral",
                    "Direct H2H is weighted only when it has enough appearances; comparable position history is a fallback."
                ),
                _af_factor(
                    "possession_range", "Possession range and upside", _af_poss_status,
                    (f"Expected {_af_expected_poss:.1f}% possession; likely range "
                     f"{_af_poss_range[0]:.1f}–{_af_poss_range[1]:.1f}%") if _af_poss_range else "No verified possession range",
                    {"expected": _af_expected_poss, "range": _af_poss_range, "observations": len(_af_poss_obs),
                     "realData": _af_real_poss, "multiplier": (match_dominance or {}).get("multiplier")},
                    len(_af_poss_obs), "projection", "up" if (_af_expected_poss or 50) > 52 else ("down" if (_af_expected_poss or 50) < 48 else "neutral"),
                    "The range exposes uncertainty around the point estimate; it is not a guarantee of possession."
                ),
                _af_factor(
                    "team_pass_volume", "Team pass-volume environment", _af_team_pass_status,
                    f"Team averaged {_af_team_pass_avg:.1f} passes per match" if _af_team_pass_avg is not None else (
                        "Measured but not needed for this prop" if not _af_pass_prop else "Team pass totals unavailable"
                    ),
                    {"average": _af_team_pass_avg, "observations": len(_af_team_passes), "propSensitive": _af_pass_prop},
                    len(_af_team_passes), "projection" if _af_pass_prop else "context", "up" if _af_pass_prop and (_af_team_pass_avg or 0) >= 450 else "neutral",
                    "Team opportunity is separated from the player's own recent production."
                ),
                _af_factor(
                    "player_share", "Player share of team passes", _af_share_status,
                    f"Player averaged {_af_share_avg:.1f}% of team passes" if _af_share_avg is not None else (
                        "Player share unavailable from matching fixture totals" if _af_pass_prop else "Measured only for passing props"
                    ),
                    {"averagePct": _af_share_avg, "gamesJoined": len(_af_shares)},
                    len(_af_shares), "projection" if _af_pass_prop else "context", "up" if _af_share_avg is not None and _af_share_avg >= 8 else "neutral",
                    "Share is joined by fixture date; it is unavailable when provider data lacks team totals for the same match."
                ),
                _af_factor(
                    "availability_role", "Availability, lineup, and role", _af_lineup_status_label,
                    f"{_af_lineup_status or 'Lineup unavailable'} · {_af_position or 'position unknown'}"
                    f"{' · ' + _af_role if _af_role else ''}",
                    {"lineupStatus": _af_lineup_status, "position": _af_position, "role": _af_role,
                     "teamPlayers": len((((_af_lineup or {}).get("home") or {}).get("players") or [])) if isinstance(_af_lineup, dict) else 0},
                    None, "projection", "neutral",
                    "Confirmed or predicted lineup data can change expected minutes; role is kept separate from raw position."
                ),
                _af_factor(
                    "game_state", "Game-state and event scenarios", _af_script_status,
                    (_af_game_script.get("key_finding") or "Scenario model available; live match events are not known pre-match."),
                    {"gameScript": _af_game_script or None, "situation": _af_game_situation or None,
                     "earlyGoalProfile": locals().get("_fg_scenario_weights") or None,
                     "liveEventsAvailable": False},
                    None, "projection", "neutral", _af_event_warning
                ),
                _af_factor(
                    "competition_context", "Competition, venue, and opponent strength", _af_comp_status,
                    f"{_af_comp.get('league') or 'League ' + str(_af_comp.get('leagueId') or '?')} · "
                    f"{_af_comp.get('venue') or 'venue unknown'} · "
                    f"opponent {_af_comp.get('opponentTier') or 'tier unknown'}",
                    _af_comp, None, "context", "neutral",
                    "Fixture identity, venue, odds, and opponent tier are kept together to avoid mixing matches."
                ),
                _af_factor(
                    "tactical_similarity", "Tactical and role similarity", _af_tactical_status,
                    f"{_af_comparable_n} same-position opponent matchups" if _af_comparable_n else "No comparable tactical sample",
                    {"sampleSize": _af_comparable_n, "position": _af_position,
                     "role": _af_role, "formation": ((_af_lineup or {}).get("home") or {}).get("formation") if isinstance(_af_lineup, dict) else None},
                    _af_comparable_n, "projection", "neutral",
                    "Comparable history is weighted by position, venue, opponent, and available possession context."
                ),
                _af_factor(
                    "evidence_quality", "Evidence quality and confidence controls", "applied" if _af_quality_level != "low" else "warning",
                    f"{_af_quality_level.title()} evidence quality · {_af_conf:.0f}% displayed confidence",
                    {"score": _af_quality_score, "level": _af_quality_level, "appliedGroups": _af_applied_count,
                     "warningGroups": _af_warning_count, "confidence": _af_conf, "confidenceCap": _af_conf_cap,
                     "missingInputs": _af_missing},
                    _af_applied_count, "confidence", "down" if _af_conf_cap else "neutral", _af_evidence_detail
                ),
            ]
            prediction["modelInputSnapshot"] = {
                "capturedAt": datetime.now(timezone.utc).isoformat(),
                "fixture": {
                    "fixtureId": prediction.get("fixtureId") or (match_odds or {}).get("fixtureId"),
                    "teamId": prediction.get("fixtureTeamId") or actual_team_id,
                    "opponentId": prediction.get("fixtureOpponentId") or req.opponentId,
                    "venue": prediction.get("venue") or req.venue,
                    "leagueId": req.leagueId,
                },
                "request": {"playerId": req.playerId, "playerName": req.playerName,
                            "propType": req.propType, "line": req.line},
                "sampleCounts": {
                    "playerLogs": len(_af_logs), "teamFixtures": len(_af_team_stats),
                    "opponentFixtures": len(_af_opp_stats), "h2hPlayerGames": _af_opponent_n,
                    "comparableGames": _af_comparable_n, "possessionObservations": len(_af_poss_obs),
                    "teamPassObservations": len(_af_team_passes), "shareJoins": len(_af_shares),
                },
                "final": {
                    "projectedValue": prediction.get("projectedValue"),
                    "recommendation": prediction.get("recommendation"),
                    "confidenceScore": prediction.get("confidenceScore"),
                    "confidenceLevel": prediction.get("confidenceLevel"),
                    "expectedPossession": _af_expected_poss,
                    "lineupStatus": _af_lineup_status,
                    "gameScript": _af_game_script or None,
                },
            }
        except Exception as _af_err:
            # A diagnostic explanation must never make a valid prediction fail.
            print(f"[MODEL FACTORS] snapshot failed: {_af_err}")
            prediction["analysisFactors"] = []

        # ── FINAL PROJECTION LEDGER + LEDGER-BOUND AI ─────────────────────────
        # This is intentionally the last model boundary.  The earlier
        # analysisFactors snapshot is evidence-oriented; factorLedger is the
        # ordered numeric audit trail used by the explanation model.
        try:
            _ledger_projection = next(
                (
                    item.get("after")
                    for item in reversed(_factor_ledger)
                    if item.get("after") is not None and item.get("kind") != "confidence"
                ),
                None,
            )
            _final_projection = prediction.get("projectedValue", req.line)
            if _ledger_projection != _ledger_num(_final_projection):
                _record_projection_factor(
                    "final_projection_lock",
                    "Final displayed projection",
                    _ledger_projection,
                    _final_projection,
                    inputs={"line": req.line},
                    reason="Captures any late guard or calibration change before the result is returned.",
                )
            else:
                _record_projection_factor(
                    "final_projection_lock",
                    "Final displayed projection",
                    _ledger_projection,
                    _final_projection,
                    status="measured",
                    inputs={"line": req.line},
                    reason="Final projection is locked for display and explanation.",
                )

            # A late pass calibration or hard guard can move the displayed
            # projection after the Bayesian Truth block refreshed pOver/pUnder.
            # Recompute the probabilities from the value the user actually
            # sees, using the same predictive standard deviation used earlier.
            # This keeps the ledger, bayesianMetrics, badge, and AI prompt on
            # one final numeric snapshot.
            _final_bm = prediction.setdefault("bayesianMetrics", {})
            _final_line_num = float(req.line) if req.line is not None else 0.0
            _final_pv_num = float(_final_projection) if _final_projection is not None else _final_line_num
            _final_std = max(
                float(_final_bm.get("posteriorStd") or 0),
                float(_final_bm.get("priorStd") or 0) * 0.55,
                abs(_final_pv_num) * 0.17,
            )
            if _final_std > 0 and _final_line_num:
                try:
                    import math as _final_math
                    _final_z = (_final_line_num - _final_pv_num) / _final_std
                    _final_p_under = round(
                        100 * (0.5 * (1 + _final_math.erf(_final_z / _final_math.sqrt(2)))),
                        1,
                    )
                    _final_p_over = round(100 - _final_p_under, 1)
                    _final_bm["pOver"] = _final_p_over
                    _final_bm["pUnder"] = _final_p_under
                    if str(prediction.get("recommendation") or "").upper() != "PASS":
                        _final_rec = "over" if _final_p_over >= _final_p_under else "under"
                        prediction["recommendation"] = _final_rec
                        _final_bm["recommendation"] = _final_rec
                except (TypeError, ValueError, ZeroDivisionError):
                    pass

            # Reassert the display invariant after every late projection stage.
            # PASS is an intentional suppression state; OVER/UNDER must agree
            # with the final displayed projection relative to the line.
            if str(prediction.get("recommendation") or "").upper() != "PASS":
                prediction["recommendation"] = (
                    "over" if _final_pv_num > _final_line_num else "under"
                )
                _final_bm["recommendation"] = prediction["recommendation"]

            # Recompute edge and safety after all late projection stages. The
            # normal edge/safety block runs before pass-projection calibration,
            # so using its values here could describe an earlier projection or
            # earlier direction in the final ledger.
            _final_rec_upper = str(prediction.get("recommendation") or "").upper()
            _final_position = (
                prediction.get("player", {}).get("position")
                or prediction.get("position")
                or specific_position
                or ""
            )
            _final_conf_pre_safety = float(prediction.get("confidenceScore") or 50)
            if _final_rec_upper == "PASS":
                _final_safety = "AVOID"
                _final_hist_rate = None
                _final_hist_n = 0
            elif prediction.get("coinFlip"):
                _final_safety = "RISKY"
                _final_hist_rate = None
                _final_hist_n = 0
            else:
                _final_safety_data = _get_prop_safety(
                    req.propType,
                    _final_rec_upper,
                    league_id=req.leagueId,
                    position=_final_position,
                )
                _final_safety = (_final_safety_data or {}).get("safety", "RISKY")
                _final_hist_rate = (_final_safety_data or {}).get("hitRate")
                _final_hist_n = (_final_safety_data or {}).get("n", 0)

            _final_margin = abs(_final_pv_num - _final_line_num) if _final_line_num > 0 else 0
            _final_gap_pct = (
                abs(_final_pv_num - _final_line_num) / _final_line_num * 100
                if _final_line_num > 0 else 0
            )
            _final_market_dist = _final_gap_pct >= 35
            if _final_rec_upper == "PASS" or prediction.get("coinFlip") or _final_safety == "AVOID":
                _final_edge_rating = "NO EDGE"
            elif _final_safety == "SAFE":
                _final_edge_rating = (
                    "SHARP EDGE" if _final_margin >= 5 and _final_conf_pre_safety >= 60
                    else "EDGE" if _final_margin >= 3 and _final_conf_pre_safety >= 55
                    else "MARGINAL" if _final_margin >= 2 else "NO EDGE"
                )
            elif _final_safety == "MODERATE":
                _final_edge_rating = (
                    "SHARP EDGE" if _final_margin >= 8 and _final_conf_pre_safety >= 65
                    else "EDGE" if _final_margin >= 5 and _final_conf_pre_safety >= 58
                    else "MARGINAL" if _final_margin >= 3 else "NO EDGE"
                )
            else:
                _final_edge_rating = (
                    "MARGINAL"
                    if (_final_margin >= 10 and _final_conf_pre_safety >= 70) or _final_market_dist
                    else "NO EDGE"
                )
            if _final_market_dist and _final_edge_rating == "NO EDGE":
                _final_edge_rating = "MARGINAL"

            prediction["edgeRating"] = _final_edge_rating
            prediction["safetyRating"] = _final_safety
            prediction["propHistoricalRate"] = _final_hist_rate
            prediction["propHistoricalN"] = _final_hist_n

            # Preserve the existing suppression policy, but apply it against
            # the final direction/rating if late calibration changed either.
            if _final_rec_upper != "PASS":
                if _final_safety == "AVOID" and _final_hist_rate is not None:
                    _final_cap = max(50, round(_final_hist_rate))
                    if float(prediction.get("confidenceScore") or 50) > _final_cap:
                        _record_confidence_control(
                            "final_safety_cap",
                            "Final safety confidence cap",
                            prediction.get("confidenceScore"),
                            _final_cap,
                            f"Final {_final_rec_upper} safety is AVOID at {_final_hist_rate:.1f}% "
                            f"over {_final_hist_n} settled events.",
                        )
                        prediction["confidenceScore"] = _final_cap
                        prediction["confidenceLevel"] = "Medium" if _final_cap >= 55 else "Low"
                elif _final_safety == "RISKY" and _final_hist_rate is not None:
                    _final_risky_conf = float(prediction.get("confidenceScore") or 50)
                    if _final_hist_rate < 50 and _final_risky_conf > 65:
                        _final_adj = max(55, _final_risky_conf - 5)
                        _record_confidence_control(
                            "final_risky_adjustment",
                            "Final risky-prop confidence adjustment",
                            _final_risky_conf,
                            _final_adj,
                            f"Final {_final_rec_upper} safety is RISKY at {_final_hist_rate:.1f}% "
                            f"over {_final_hist_n} settled events.",
                        )
                        prediction["confidenceScore"] = _final_adj
                        prediction["confidenceLevel"] = "High" if _final_adj >= 70 else "Medium"

            # Confidence is a separate control stream from projection. Keep it
            # explicit so Gemini can explain a PASS/RISKY/capped result without
            # implying the cap changed the math projection.
            _raw_conf_final = prediction.get("rawConfidence")
            _display_conf_final = prediction.get("confidenceScore", 50)
            if _raw_conf_final is not None and _ledger_num(_raw_conf_final) != _ledger_num(_display_conf_final):
                _record_confidence_control(
                    "final_confidence_control",
                    "Final confidence controls",
                    _raw_conf_final,
                    _display_conf_final,
                    "Displayed confidence includes empirical, sample-size, market-distance, and safety controls.",
                )

            _ledger_final = {
                "projectedValue": _ledger_num(_final_projection),
                "line": _ledger_num(req.line),
                "recommendation": str(prediction.get("recommendation") or "").upper(),
                "pOver": _ledger_num(_final_bm.get("pOver")),
                "pUnder": _ledger_num(_final_bm.get("pUnder")),
                "confidenceScore": _ledger_num(_display_conf_final),
                "confidenceLevel": prediction.get("confidenceLevel"),
                "edge": _ledger_num(abs(float(_final_projection) - float(req.line)))
                if _final_projection is not None and req.line is not None else None,
                "edgeRating": prediction.get("edgeRating"),
                "safetyRating": prediction.get("safetyRating"),
            }
            for _idx, _factor in enumerate(_factor_ledger, start=1):
                _factor["sequence"] = _idx
            _ledger_payload = {
                "version": "projection-ledger-v1",
                "factors": _factor_ledger,
                "final": _ledger_final,
            }
            _ledger_fingerprint = hashlib.sha256(
                json.dumps(_ledger_payload, sort_keys=True, separators=(",", ":"), default=str).encode()
            ).hexdigest()[:20]
            prediction["factorLedger"] = _ledger_payload
            prediction["factorLedgerVersion"] = "projection-ledger-v1"
            prediction["factorLedgerFingerprint"] = _ledger_fingerprint

            # Use the final ledger as an authoritative suffix rather than
            # relying on any earlier preflight values embedded in `prompt`.
            _final_ledger_prompt = f"""

⛔ FINAL LEDGER — AUTHORITATIVE AND COMPLETE ⛔
The following values are the exact values shown to the user. Do not recompute,
round differently, or use any earlier estimate in the request. Explain every
factor with status "applied" in sequence order. Mention skipped/unavailable
factors only as limitations. Never invent a numeric adjustment that is absent
from this ledger. Projection and recommendation are mathematical; Gemini must
not change them.

{json.dumps(_ledger_payload, indent=2, default=str)}

Your opening Verdict, sharpSummary, TL;DR, aiProjection, scenario base case,
and every probability claim must agree with FINAL. If recommendation is PASS,
describe it as no actionable edge rather than a winning OVER/UNDER pick. If
confidence or safety was capped, say that explicitly and distinguish the cap
from the projection.
"""
            _final_ai_prompt = prompt + _final_ledger_prompt
            _soc_ck = (
                f"soc|{req.playerId or req.playerName}|{req.propType}|{req.line}|"
                f"{req.opponentName or ''}|{today_str}|{_ledger_fingerprint}"
            )
            _final_ai_result = None
            _ai_cache_hit = False
            try:
                _pred_hit = await db.ai_response_cache.find_one(
                    {"_k": _soc_ck}, {"_id": 0, "v": 1}
                )
                if (
                    _pred_hit
                    and isinstance(_pred_hit.get("v"), dict)
                    and _pred_hit["v"].get("tacticalBreakdown")
                ):
                    _final_ai_result = _pred_hit["v"]
                    _ai_cache_hit = True
                    print(f"[PRED CACHE HIT] final ledger {_ledger_fingerprint}")
            except Exception as _cache_read_err:
                print(f"[PRED CACHE READ] skipped: {_cache_read_err}")

            if _final_ai_result is None and GEMINI_AI_ENABLED:
                from ai_engine import (
                    check_prediction_budget as _check_budget,
                    increment_prediction_budget as _incr_budget,
                )
                if await _check_budget():
                    try:
                        _final_ai_result = await aio.wait_for(
                            call_grok(
                                label="gemini-final-ledger",
                                model="gemini-2.0-flash",
                                prompt_override=_final_ai_prompt,
                            ),
                            timeout=50,
                        )
                        if _final_ai_result and _final_ai_result.get("tacticalBreakdown"):
                            await _incr_budget()
                            try:
                                await db.ai_response_cache.replace_one(
                                    {"_k": _soc_ck},
                                    {
                                        "_k": _soc_ck,
                                        "v": _final_ai_result,
                                        "ledgerFingerprint": _ledger_fingerprint,
                                        "ts": datetime.now(timezone.utc),
                                    },
                                    upsert=True,
                                )
                            except Exception as _cache_write_err:
                                print(f"[PRED CACHE WRITE] skipped: {_cache_write_err}")
                    except Exception as _final_ai_err:
                        print(f"[AI FINAL LEDGER] synthesis failed: {_final_ai_err}")
                        _final_ai_result = None
                else:
                    print("[AI BUDGET] Daily limit reached — final math-only prediction.")
            else:
                print("[AI DISABLED] Gemini final-ledger synthesis skipped.")

            # Merge only narrative fields. Deterministic fixture, player,
            # projection, recommendation, and model metrics remain untouched.
            _narrative_fields = (
                "aiProjection", "reasoning", "tacticalBreakdown", "sharpSummary",
                "scenarioAnalysis", "keyEvidence", "sensitivityTests", "subRisk",
                "gameFlowDynamics", "uncertaintyNote", "qualitySignal", "keyFactors",
            )
            if _final_ai_result:
                for _field in _narrative_fields:
                    if _field in _final_ai_result:
                        prediction[_field] = _final_ai_result[_field]
                prediction["aiSource"] = "gemini"
                prediction["aiPending"] = False
                prediction["aiLedgerFingerprint"] = _ledger_fingerprint
            else:
                prediction["aiSource"] = "math"
                prediction["aiPending"] = False

            # Rebuild the authoritative math footer after all late calibration
            # and guard stages. This prevents a correct AI narrative from being
            # followed by stale pre-calibration numbers.
            _final_bm = prediction.get("bayesianMetrics") or {}
            _fpv = prediction.get("projectedValue", req.line)
            _frec = str(prediction.get("recommendation") or "PASS").upper()
            _fline = req.line
            _fpover = _final_bm.get("pOver", 50) or 50
            _fpunder = _final_bm.get("pUnder", 50) or 50
            _fpwin = max(_fpover, _fpunder)
            _fedge = abs(float(_fpv) - float(_fline)) if _fpv is not None and _fline is not None else 0
            _fp_s = str(int(_fpv)) if isinstance(_fpv, (int, float)) and _fpv == int(_fpv) else f"{_fpv}"
            _fl_s = str(int(_fline)) if isinstance(_fline, (int, float)) and _fline == int(_fline) else f"{_fline}"
            _final_math_footer = (
                f"**Final Math Ledger**\n"
                f"Projection: {_fp_s} | Line: {_fl_s} | Recommendation: {_frec} | Edge: {_fedge:.1f}\n"
                f"P(OVER): {_fpover:.1f}% | P(UNDER): {_fpunder:.1f}% | "
                f"Confidence: {_display_conf_final:.0f}% ({prediction.get('confidenceLevel', 'Medium')})\n"
                f"Ledger: {_ledger_fingerprint} | Factors recorded: {len(_factor_ledger)}"
            )
            _existing_final_td = prediction.get("tacticalBreakdown")
            if isinstance(_existing_final_td, str) and len(_existing_final_td.strip()) > 100 and _final_ai_result:
                prediction["tacticalBreakdown"] = _existing_final_td.strip() + "\n\n---\n" + _final_math_footer
            else:
                prediction["tacticalBreakdown"] = _final_math_footer
            prediction["sharpSummary"] = (
                f"Reverse Formula final call: {_fp_s} {_frec} {_fl_s} "
                f"with {_fpwin:.1f}% probability and {_fedge:.1f} edge. "
                f"Confidence is {_display_conf_final:.0f}% ({prediction.get('confidenceLevel', 'Medium')}); "
                f"{prediction.get('safetyRating', 'risk not rated')} safety."
            )
        except Exception as _ledger_err:
            # The ledger is diagnostic/explanatory and must never take down a
            # valid math prediction. Keep the explicit math source marker.
            print(f"[FINAL LEDGER] failed: {_ledger_err}")
            prediction["aiSource"] = "math"
            prediction["aiPending"] = False

        prediction["_ts"] = datetime.now(timezone.utc)
        try:
            await db.predictions.insert_one(prediction)
        except Exception as _persist_err:
            # Atlas can hard-block writes when the free-tier cluster reaches
            # its storage limit. Persistence is useful for analytics, but it
            # must not turn an already-computed prediction into a 500.
            # Keep this fail-open temporarily until the cluster is cleaned up
            # or upgraded; normal writes resume automatically afterward.
            print(
                f"[PREDICTION PERSISTENCE] skipped; returning computed prediction: "
                f"{type(_persist_err).__name__}: {_persist_err}"
            )
        prediction.pop("_id", None)


        return prediction
    except (json.JSONDecodeError, aio.TimeoutError):
        # Return a safe fallback prediction
        return {
            "player": {"id": req.playerId, "name": req.playerName, "team": req.teamName, "position": "Unknown"},
            "opponent": req.opponentName,
            "propType": req.propType,
            "line": req.line,
            "projectedValue": req.line,
            "recommendation": "over",
            "confidenceScore": 50,
            "confidenceLevel": "Medium",
            "confidenceInterval": None,
            "recentSamples": [],
            "bayesianMetrics": {"priorMean": req.line, "momentumEffect": 0, "covariateAdjustment": 0, "reversalFlag": "stable"},
            "probabilityCurve": [],
            "reasoning": "AI analysis returned an invalid format. Displaying fallback prediction.",
            "tacticalInsights": "",
            "explanation": "Fallback prediction due to AI parsing error."
        }
    except HTTPException:
        raise  # Re-raise HTTPException directly (e.g., 400 for teamId=0)
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Prediction failed: {str(e)}")
    finally:
        reset_api_request_priority(_priority_token)


# ── AI async polling endpoint ──────────────────────────────────────────────
# F5 decoupling: frontend polls for AI narrative after receiving math result
@router.post("/predict/ai-poll")
async def ai_poll(req: PredictionRequest):
    _ck = f"soc|{req.playerId or req.playerName}|{req.propType}|{req.line}|{req.opponentName or ''}|{datetime.now(timezone.utc).strftime('%Y-%m-%d')}"
    try:
        _hit = await db.ai_pending_jobs.find_one({"_k": _ck}, {"_id": 0})
        if _hit and _hit.get("done"):
            if _hit.get("failed"):
                return {"ready": True, "failed": True, "data": None}
            return {"ready": True, "failed": False, "data": _hit.get("v")}
        return {"ready": False, "failed": False, "data": None}
    except Exception as e:
        print(f"[AI-POLL] error: {e}")
        return {"ready": False, "failed": False, "data": None}
# ─────────────────────────────────────────────────────────────────────────


# ── Match Script endpoint ───────────────────────────────────────────────────
# Fires right after a player/match is identified, BEFORE the user enters a
# stat line. Fast, moneyline + odds-derived-possession classification — see
# match_script.py for the tier table and cross-check logic.
@router.get("/match-script")
async def match_script(teamId: int, opponentId: int, leagueId: int, isHome: bool,
                        teamName: str = "This team", opponentName: str = "Opponent",
                        leagueName: str = ""):
    from match_script import get_match_script
    try:
        if not teamId or not opponentId:
            return {"available": False, "noCleanScript": True, "primaryScript": None,
                    "isFavorable": False, "explanation": "Missing team data.",
                    "tacticalModifier": None, "expectedEffects": []}
        result = await get_match_script(
            team_id=teamId, opponent_id=opponentId, league_id=leagueId, is_home=isHome,
            team_name=teamName, opponent_name=opponentName, league_name=leagueName,
        )
        return result
    except Exception as e:
        print(f"[MATCH SCRIPT] error: {e}")
        return {"available": False, "noCleanScript": True, "primaryScript": None,
                "isFavorable": False, "explanation": "Could not classify this match right now.",
                "tacticalModifier": None, "expectedEffects": []}
