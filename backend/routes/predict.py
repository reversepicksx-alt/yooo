import json
import os
import uuid
import asyncio as aio
import statistics as stats_mod
import traceback
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException
from emergentintegrations.llm.chat import LlmChat, UserMessage

from openai import OpenAI

from config import (
    db, EMERGENT_LLM_KEY, XAI_API_KEY, GEMINI_API_KEY, CURRENT_SEASON,
    WOMENS_LEAGUE_IDS, STAT_FIELD_MAP, STAT_LAMBDA_MAP,
)
from models import PredictionRequest
from utils import api_football_request, get_recent_fixtures_fast, strip_accents, get_soccer_odds, decimal_to_american
from grok_engine import fetch_web_intel
from prop_safety_cache import get_prop_safety as _get_prop_safety
import soccer_bdl_client as _bdl_soc
# game_script_intelligence removed — was distorting confidence scores for GK pass picks

router = APIRouter(prefix="/api", tags=["predict"])

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

@router.post("/predict")
async def predict(req: PredictionRequest):
    try:
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        # Prediction cache REMOVED: returning stale cached predictions caused
        # contradictions (e.g., wrong possession narrative when match data changed)
        # and undermined user trust. Every request now runs full fresh analysis.
        # Results are still stored in db.predictions for analytics/top-props.

        async def safe_fetch(endpoint, params, fallback=None):
            try:
                return await api_football_request(endpoint, params)
            except Exception:
                return fallback

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
        _is_wc = (league_id == 1)
        if _is_wc:
            print(f"[WC MODE] World Cup prediction — player={req.playerName}, venue will be treated as NEUTRAL")

        # ── AUTO-RESOLVE missing IDs from team/player names using local cache ──
        # This runs BEFORE ai_only_mode is decided, so predictions always have
        # real fixture data even when the scan didn't return numeric IDs.
        _resolved_opp_id = req.opponentId or 0
        _resolved_player_id = req.playerId or 0
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

            # 2. Resolve opponent ID from opponent name — always verify
            if req.opponentName:
                try:
                    _o = await _find_team(req.opponentName)
                    if _o and _o.get("teamId"):
                        _resolved_opp_id = _o["teamId"]
                        print(f"[ID RESOLVE] '{req.opponentName}' → opponentId={_resolved_opp_id}")
                except Exception as _re:
                    print(f"[ID RESOLVE] opponent lookup failed: {_re}")

            # 3. Resolve player ID from player name
            if (not _resolved_player_id or _resolved_player_id == 0) and req.playerName:
                try:
                    _p = await _get_player_by_name(
                        req.playerName,
                        actual_team_id if actual_team_id and actual_team_id != 0 else None,
                        league_id=league_id if league_id and league_id != 39 else None,
                        team_name_hint=req.teamName or None,
                        prop_type=req.propType or None,
                    )
                    if _p and _p.get("playerId"):
                        _resolved_player_id = _p["playerId"]
                        if not actual_team_id or actual_team_id == 0:
                            actual_team_id = _p.get("teamId") or actual_team_id
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

            # Bake resolved IDs back into req so all downstream references see them
            if _resolved_opp_id != req.opponentId or _resolved_player_id != req.playerId or actual_team_id != req.teamId:
                req = req.model_copy(update={
                    "teamId": actual_team_id or 0,
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
                fixture_match = None

                # Primary: Get team's upcoming + today's fixtures across ALL competitions
                try:
                    next_fixtures = await api_football_request("fixtures", {"team": actual_team_id, "next": 10})
                    if not next_fixtures:
                        next_fixtures = []

                    # Also check today's live/scheduled fixtures (catches matches about to start or in progress)
                    from datetime import date as date_type
                    today_str = date_type.today().isoformat()
                    try:
                        today_fixtures = await api_football_request("fixtures", {"team": actual_team_id, "date": today_str})
                        if today_fixtures:
                            # Prepend today's fixtures (higher priority — game is today)
                            existing_ids = {f.get("fixture", {}).get("id") for f in next_fixtures}
                            for tf in today_fixtures:
                                if tf.get("fixture", {}).get("id") not in existing_ids:
                                    next_fixtures.insert(0, tf)
                    except Exception:
                        pass

                    if next_fixtures:
                        # Find fixtures against this specific opponent
                        opponent_matches = []
                        for nf in next_fixtures:
                            home_id = nf.get("teams", {}).get("home", {}).get("id")
                            away_id = nf.get("teams", {}).get("away", {}).get("id")
                            if req.opponentId in (home_id, away_id):
                                opponent_matches.append(nf)

                        if opponent_matches:
                            # Pick the SOONEST one (first in list — API returns date-ascending)
                            fixture_match = opponent_matches[0]
                        else:
                            # No opponent match found — take team's next match as fallback
                            fixture_match = next_fixtures[0]
                except Exception:
                    pass

                # Fallback: H2H (limited to next: 2 per API-Football max)
                if not fixture_match:
                    try:
                        h2h = await api_football_request("fixtures/headtohead", {
                            "h2h": f"{actual_team_id}-{req.opponentId}",
                            "next": 2,
                        })
                        if h2h:
                            fixture_match = h2h[0]
                    except Exception:
                        pass

                if not fixture_match:
                    return None

                fid = fixture_match.get("fixture", {}).get("id")
                result = {}
                if fid:
                    result["fixtureId"] = fid
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

        _is_bdl_league = True  # BDL is the sole soccer data source — never call API-Football

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
            h2h_task = safe_fetch("fixtures/headtohead", {"h2h": f"{actual_team_id}-{req.opponentId}", "season": CURRENT_SEASON}, [])

            async def get_standings_multi_season():
                for s in [CURRENT_SEASON + 1, CURRENT_SEASON, CURRENT_SEASON - 1]:
                    result = await safe_fetch("standings", {"league": league_id, "season": s})
                    if result:
                        return result
                return None

            standings_task = get_standings_multi_season()
            fixtures_task = get_recent_fixtures_fast(actual_team_id, 40)
            odds_task = get_match_odds()

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
                        got_tkl = False
                        if player_data:
                            for team_block in player_data:
                                if team_block.get("team", {}).get("id") == team_id:
                                    for p in team_block.get("players", []):
                                        st  = (p.get("statistics") or [{}])[0]
                                        tkl = st.get("tackles") or {}
                                        fls = st.get("fouls")   or {}
                                        tkl_total     += (tkl.get("total")          or 0)
                                        tkl_int       += (tkl.get("interceptions")  or 0)
                                        tkl_blocks    += (tkl.get("blocks")         or 0)
                                        fls_committed += (fls.get("committed")      or 0)
                                    got_tkl = True
                                    break
                        # All four components of the PPDA denominator
                        # (tackles + interceptions + fouls + blocks — full-pitch approximation)
                        result["tackles_total"]         = tkl_total     if got_tkl else None
                        result["tackles_interceptions"] = tkl_int       if got_tkl else None
                        result["tackles_blocks"]        = tkl_blocks    if got_tkl else None
                        result["fouls_committed_agg"]   = fls_committed if got_tkl else None
                    except Exception:
                        result["tackles_total"]         = None
                        result["tackles_interceptions"] = None
                        result["tackles_blocks"]        = None
                        result["fouls_committed_agg"]   = None

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
                return {
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
                }

            stat_field_map = {
                "goals": "goals_total", "assists": "goals_assists",
                "shots_assisted": "passes_key",
                "pass_attempts": "passes_total", "passes": "passes_total",
                "shots": "shots_total", "shots_on_target": "shots_on",
                "tackles": "tackles_total", "key_passes": "passes_key",
                "saves": "goals_saves", "interceptions": "tackles_interceptions",
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
                _tfh_cache_ttl = 24 * 3600  # 24 hours
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

                        # Helper: enrich game log with team possession from team stats cache
                        async def _enrich_possession(gl_dict: dict) -> dict:
                            try:
                                team_cache_key = f"fxt_{fid}_{actual_team_id}"
                                team_cached = await db.fixture_player_cache.find_one(
                                    {"_k": team_cache_key}, {"_id": 0, "d.possession": 1}
                                )
                                if team_cached and team_cached.get("d"):
                                    raw_poss = team_cached["d"].get("possession", "")
                                    if raw_poss:
                                        poss_str = str(raw_poss).replace("%", "").strip()
                                        try:
                                            team_poss = int(poss_str)
                                            gl_dict["teamPossession"] = team_poss
                                            gl_dict["opponentPossession"] = 100 - team_poss
                                        except (ValueError, TypeError):
                                            pass
                            except Exception:
                                pass
                            return gl_dict

                        # Check prefetch cache first — avoids extra API call if already cached
                        cache_key = f"fxp_{fid}_{player_id}"
                        cached_doc = await db.fixture_player_cache.find_one({"_k": cache_key}, {"_id": 0, "d": 1})
                        if cached_doc and cached_doc.get("d"):
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
        player_venue = req.venue.lower()  # "home" or "away"
        opponent_venue = "away" if player_venue == "home" else "home"
        is_womens = req.leagueId in WOMENS_LEAGUE_IDS
        pronoun_note = "IMPORTANT: This is a WOMEN'S league. Use she/her/her pronouns for all players. Never use he/him/his." if is_womens else ""

        # Filter team's recent fixtures by venue
        venue_filtered_team_fixtures = [f for f in recent_fixtures if f.get("venue") == player_venue]
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

        # Filter opponent fixtures by their venue in THIS matchup
        venue_filtered_opp_fixtures = [f for f in opponent_fixture_list if f.get("venue") == opponent_venue]

        # Wave 2: Use VENUE-FILTERED fixtures for deep stats
        # Team's last 5 HOME/AWAY games (matching this match's venue)
        team_fixture_stats_task = fetch_fixture_team_stats(
            venue_filtered_team_fixtures[:5] if len(venue_filtered_team_fixtures) >= 3 else all_team_fixtures[:5],
            actual_team_id or 40, 5
        )
        # Opponent's last 5 AWAY/HOME games (opposite venue — how they perform when visiting/hosting)
        opponent_fixture_stats_task = fetch_fixture_team_stats(
            venue_filtered_opp_fixtures[:5] if len(venue_filtered_opp_fixtures) >= 3 else opponent_fixture_list[:5],
            req.opponentId, 5
        )
        # Player game logs: VENUE-PRIORITIZED ordering
        # Search venue-matching fixtures first (away if away prop, home if home prop)
        # so we maximize relevant venue samples (target: 15-20 venue-matched games)
        venue_first_fixtures = venue_filtered_team_fixtures + [f for f in all_team_fixtures if f.get("venue") != player_venue]
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

            # 2. Team stats (venue-specific)
            if team_stats:
                fixtures = team_stats.get("fixtures", {})
                goals_for = team_stats.get("goals", {}).get("for", {}).get("total", {})
                goals_against = team_stats.get("goals", {}).get("against", {}).get("total", {})
                parts.append(f"""[TEAM {player_venue.upper()} PROFILE]
- Record: W{fixtures.get('wins', {}).get(player_venue, 'N/A')} D{fixtures.get('draws', {}).get(player_venue, 'N/A')} L{fixtures.get('loses', {}).get(player_venue, 'N/A')}
- Goals For ({player_venue}): {goals_for.get(player_venue, 'N/A')} | Against ({player_venue}): {goals_against.get(player_venue, 'N/A')}""")

            # 3. Opponent stats (opposite venue)
            if opponent_stats:
                opp_fix = opponent_stats.get("fixtures", {})
                opp_gf = opponent_stats.get("goals", {}).get("for", {}).get("total", {})
                opp_ga = opponent_stats.get("goals", {}).get("against", {}).get("total", {})
                parts.append(f"""[OPPONENT {opponent_venue.upper()} PROFILE]
- Record: W{opp_fix.get('wins', {}).get(opponent_venue, 'N/A')} D{opp_fix.get('draws', {}).get(opponent_venue, 'N/A')} L{opp_fix.get('loses', {}).get(opponent_venue, 'N/A')}
- Goals For ({opponent_venue}): {opp_gf.get(opponent_venue, 'N/A')} | Against ({opponent_venue}): {opp_ga.get(opponent_venue, 'N/A')}""")

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
        match_dominance = {"expectedPoss": 50.0, "oppExpectedPoss": 50.0, "multiplier": 1.0, "notes": []}

        # Wave 2: Fetch deep fixture data + Situation Engine in parallel
        # AI digest, web intel, and AI press intensity removed — Gemini is summary-only.
        # Press intensity falls back to the heuristic engine; digest/web intel were
        # pre-processing context that AI no longer needs for math.
        from situation_engine import build_game_situation

        async def _noop_str(): return ""
        async def _noop_none(): return None
        ai_digest_task = _noop_str()

        # Situation engine inputs
        _sit_is_home = player_venue == "home"
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
        ai_press_task = _noop_none()

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
        if _bdl_soc.is_bdl_league(league_id) and req.playerName:
            _bdl_stat_field_map = {
                "goals": "goals_total", "assists": "goals_assists",
                "shots_assisted": "passes_key", "pass_attempts": "passes_total",
                "passes": "passes_total", "shots": "shots_total",
                "shots_on_target": "shots_on", "tackles": "tackles_total",
                "key_passes": "passes_key", "saves": "goals_saves",
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
        def compute_match_dominance(team_stats_list, opp_stats_list, odds, is_home, standing_data):
            """Compute expected possession using opponent-aware model + odds adjustment.
            SYMMETRIC: Always computes from HOME team perspective first, then maps back.
            This ensures the SAME match always produces identical possession numbers
            regardless of which player (home or away) triggers the analysis.

            Uses venue-split averages: home team's HOME-game possession avg vs
            away team's AWAY-game possession avg. Overall averages inflate expected
            possession for away teams (e.g. Braga 54% overall but ~48% away)."""
            dom = {"expectedPoss": 50.0, "oppExpectedPoss": 50.0, "multiplier": 1.0, "notes": []}

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

            if is_home:
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
                home_poss_fallback = min(65.0, max(35.0, round(raw_poss, 1)))
                away_poss_fallback = round(100.0 - home_poss_fallback, 1)
                # Use 50% as season avg so the squeeze can activate on big gaps
                fallback_home_avg = 50.0
                fallback_away_avg = 50.0
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

            if home_avg is not None and away_avg is not None:

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
                # 1291-pick audit: actual possession is -4.7pp below projected on
                # average (mean abs error 9.6pp). Stronger regression closes this:
                # 15% was insufficient (original fix). 22% brings extremes in further:
                #   70% → 64.6%,  65% → 60.8%,  58% → 56.2%,  42% → 43.8%
                # This also reduces the GK dominant possession penalty by making
                # extreme possession ratios (>1.20) far less common.
                home_poss = round(50.0 + (home_poss - 50.0) * 0.78, 1)

                # FIX 1 — Lower ceiling from 75% → 67%.
                # No professional soccer team sustains 75% possession in a
                # real fixture; the 531-pick sample never produced an actual
                # reading above 71%. 67% is the realistic upper bound.
                home_poss = min(67.0, max(30.0, round(home_poss, 1)))
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

        # Determine canonical (home_team_id, away_team_id) for cache key
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
            match_dominance = compute_match_dominance(
                team_fixture_stats, opponent_fixture_stats, match_odds,
                _is_home, standing_data
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

        if match_dominance.get("notes"):
            print(f"[MATCH DOMINANCE] {req.playerName}: poss={match_dominance['expectedPoss']}%, mult={match_dominance['multiplier']}, {' | '.join(match_dominance['notes'])}")

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
                team_odds = home_odds if player_venue == "home" else away_odds

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
        try:
            from bayesian_engine import compute_bayesian_projection

            # ── Quick position cache lookup (fast indexed read) ──────────────
            # We look up the cached position so the engine can apply the correct
            # momentum decay table AND the position-aware press multiplier
            # (attackers decay faster, GKs decay slower; defenders get press boost).
            #
            # The cache is written by the [POS RESOLVE] block keyed on playerId,
            # but legacy entries may only have playerName — try both so the
            # Bayesian engine never falls back to "midfielder" by accident.
            _bayes_position = ""
            try:
                _pos_doc = await db.player_positions.find_one(
                    {"$or": [{"playerId": req.playerId}, {"playerName": req.playerName}]}
                )
                if _pos_doc:
                    _bayes_position = _pos_doc.get("specificPosition", "")
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

            _VENUE_SPLIT_PROPS = {"pass_attempts", "passes", "saves", "goalie_saves"}
            _bayes_logs = player_game_logs
            if _is_wc:
                # World Cup: all games played at neutral venues — skip home/away split
                # Club stats already include both home and away games which averages to neutral
                print(f"[WC MODE] Neutral venue — skipping venue split, using all {len(player_game_logs)} club logs")
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
                    _game_script = {
                        "expected_total_goals": _scenario_probs["expectedTotal"],
                        "expected_goal_diff":   round(_expected_diff, 2),
                        "implied_home":         _scenario_probs["impliedHome"],
                        "implied_away":         _scenario_probs["impliedAway"],
                    }
            except Exception as _gs_err:
                print(f"[GAME SCRIPT] extraction failed: {_gs_err}")

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
            )
            _eb_samples = early_bayes.get("priorSamples", 0) if early_bayes else 0
            print(f"[BAYESIAN] {req.playerName}/{req.propType}: samples={_eb_samples}, logs={len(_bayes_logs)} (venue={player_venue})")

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
            for h in h2h_data[:5]:
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
                        aio.gather(*[fetch_h2h_player_stat(fid, fi) for fid, fi in h2h_fixture_ids[:5]]),
                        timeout=6
                    )
                    h2h_player_stats = [r for r in h2h_results if r]
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
            }
            if h2h_values:
                h2h_summary["avgVsOpponent"] = round(sum(h2h_values) / len(h2h_values), 2)
                h2h_summary["minVsOpponent"] = min(h2h_values)
                h2h_summary["maxVsOpponent"] = max(h2h_values)
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
                    {"playerId": req.playerId}, {"_id": 0, "specificPosition": 1, "role": 1, "updatedAt": 1, "promptVersion": 1}
                )
                cache_valid = False
                if cached_pos and cached_pos.get("specificPosition"):
                    # Check prompt version first — stale version always forces re-resolution
                    stored_version = cached_pos.get("promptVersion", 0)
                    if stored_version < POSITION_PROMPT_VERSION:
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
                    specific_position = cached_pos["specificPosition"]
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
                        stats_evidence = f"""
ACTUAL SEASON STATS (use these to determine position — stats don't lie):
- Appearances: {games.get('appearances', '?')}, Minutes: {games.get('minutes', '?')}, Rating: {games.get('rating', '?')}
- Tackles: {tck.get('total', 0)}, Interceptions: {tck.get('interceptions', 0)}, Blocks: {tck.get('blocks', 0)}
- Duels won: {duels.get('won', 0)}/{duels.get('total', 0)}
- Passes total: {pss.get('total', 0)}, Key passes: {pss.get('key', 0)}, Accuracy: {pss.get('accuracy', '?')}%
- Dribbles: {drb.get('attempts', 0)} attempts, {drb.get('success', 0)} successful
- Shots: {sht.get('total', 0)}, On target: {sht.get('on', 0)}
- Goals: {gls.get('total', 0)}, Assists: {gls.get('assists', 0)}
- Fouls drawn: {fls.get('drawn', 0)}, Committed: {fls.get('committed', 0)}
- Yellow cards: {cards.get('yellow', 0)}, Red: {cards.get('red', 0)}
POSITION CLUES — distinguish DEEP vs ADVANCED roles:
- CB: very high tackles/blocks, low key passes, low dribbles
- CDM / deep-lying playmaker (regista): the team's tempo-setter and build-up hub. HIGHEST pass volume on the team (touches the ball most when in possession), VERY HIGH pass accuracy, sits DEEPEST in midfield, LOW shots, LOW dribbles. Interceptions can be moderate (a regista is a passer first, not a destroyer). Role = "Deep-Lying Playmaker". Vitinha at PSG = CDM / Deep-Lying Playmaker (regista) — he is the metronome who orchestrates from deep and leads the team in touches/passes. He is NOT a Box-to-Box runner and NOT a CAM.
- CDM (ball-winning pivot): HIGH interceptions/tackles, high pass accuracy, LOW key passes, LOW shots. Role = "Ball Winner" or "Anchor".
- CM (box-to-box): balanced tackles + passes + key passes, MODERATE shots AND noticeable dribbles/forward runs, contributes goals/assists. Role = "Box-to-Box". Only pick this when the player visibly gets forward (shots + key passes + dribbles all moderate-to-high), NOT for a deep metronome.
- CAM (advanced playmaker): HIGH key passes (3+), moderate dribbles, LOW tackles. Plays AHEAD of midfield.
- Winger: high dribbles/crosses, low tackles
- ST: high shots/goals, low tackles

CRITICAL: The single highest-pass-volume midfielder who sits deepest, dictates tempo, with VERY HIGH pass accuracy + LOW shots + LOW dribbles = CDM / Deep-Lying Playmaker (regista), NOT Box-to-Box and NOT CAM. Box-to-Box requires visible forward output (shots + dribbles + goal contributions). CAM requires high key passes (3+)."""

                    pos_prompt = f"What is {req.playerName}'s primary position and tactical role at {corrected_team_name}?{category_hint}{stats_evidence}\nPosition must be one of: {pos_list}\nRole must be one of: Shot-Stopper, Sweeper Keeper, Ball-Playing CB, Stopper, Fullback, Wing-Back, Inverted Fullback, Anchor, Box-to-Box, Deep-Lying Playmaker, Ball Winner, Mezzala, Advanced Playmaker, Wide Playmaker, Traditional Winger, Inverted Winger, Progressive Carrier, Inside Forward, Target Man, Poacher, False 9, Shadow Striker, Complete Forward, Pressing Forward\nReply ONLY: POSITION|ROLE"

                    # GEMINI POSITION RESOLUTION
                    is_defender = player_position != "Goalkeeper"

                    async def resolve_pos_gemini() -> str:
                        """Call Gemini Flash to resolve position. Returns raw POSITION|ROLE string."""
                        from grok_engine import _gemini_call
                        sys_msg = "You are a football/soccer tactical analyst. Reply in EXACTLY this format on one line:\nPOSITION|ROLE\nNothing else."
                        return await _gemini_call(
                            pos_prompt, system=sys_msg,
                            temperature=0, max_tokens=20, timeout=10,
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
                            gem_text = await resolve_pos_gemini()
                            gem_pos, gem_role = parse_pos_response(gem_text, valid_positions)

                            if gem_pos:
                                pos_code = gem_pos
                                role_text = gem_role or ""
                                print(f"[POS RESOLVE] Gemini: {req.playerName} → {pos_code}")
                            else:
                                raise ValueError("Gemini returned invalid position")
                        except Exception as e:
                            print(f"[POS RESOLVE] Gemini position failed ({e}), retrying...")
                            gem_text2 = await resolve_pos_gemini()
                            pos_code, role_text = parse_pos_response(gem_text2, valid_positions)
                            if not pos_code:
                                raise ValueError("Gemini returned invalid position on retry")
                    else:
                        # Non-defenders: single Gemini call (with stats context)
                        pos_text = await resolve_pos_gemini()
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
        else:
            specific_position = player_position

        # Use specific position if available, otherwise fall back to generic
        display_position = specific_position or player_position
        display_role = player_role

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

POSITION-SPECIFIC REASONING FRAMEWORKS (apply the relevant one):

GOALKEEPER (pass_attempts/saves):
- pass_attempts: The INVERTED possession rule is everything. Low team possession = defenders constantly recycling under pressure to the GK = volume explosion. High team possession = GK barely involved in build-up = volume suppression. But READ THE OPPONENT — a team that presses relentlessly forces even dominant-possession GKs into rapid distribution. For saves: opponent SoT rate × GK save% × match tempo = your anchor. A high-block defensive team facing a prolific attacker on a high-tempo away game is the max-saves scenario.

STRIKER/FORWARD (shots, goals, assists):
- Think about SPACE, not just volume. A striker facing a high defensive line gets in behind for shots. A striker facing a deep block needs service from midfield — check if that midfield creates. Shots depend on penalty box entries, not just possession. An isolated striker in a low-block game can still pop off 4-5 shots if the team plays direct.

MIDFIELDER (passes, key_passes, assists):
- Ball-circulation midfielders: possession % is the primary driver. Every 5% more possession = roughly 8-12 more passes for the deepest midfielder. Key passes / assists: look at how many times the team reaches the final third AND how the striker presses — a high striker press creates more through-ball opportunities.
- CRITICAL — HOME CDM DEEP-BLOCK RULE: When a dominant home team (60%+ expected possession) faces a deep-sitting weak opponent (opponent expected possession < 36%), the CDM/DM/DLP becomes a ball-RECYCLING HUB. The deep block creates endless short-cycle sequences that all funnel back through the deepest midfielder. In this scenario, the CDM's pass count EXCEEDS their historical season average — sometimes significantly. Do NOT apply a low-motivation or dead-rubber penalty to CDM pass counts when the dominant team is still retaining comfortable possession — the passes still happen, they are just slower-paced and more circular. A CDM averaging 55 passes/game can easily hit 75-85 in this scenario. This is the single biggest source of CDM pass prop errors.

DEFENDER (passes, tackles, clearances):
- Ball-playing CBs in 55%+ possession teams easily hit 70-90 passes. The key variable is HOW the team builds — short from back (inflates defender passes) vs long-ball (suppresses). Tackles/clearances invert with possession: low possession = more defensive actions.

CRITICAL ACCURACY RULES:
- NEVER double-count minutes. A player averaging 43 passes in 26 minutes per game — the 43 IS their game output. Do NOT scale down.
- Match context OVERRIDES raw averages for pass-dependent props in high-possession scenarios.
- GOALKEEPER INVERTED RULE: Low possession = MORE GK passes. High possession = FEWER GK passes. An away GK holding a lead = maximum volume scenario.
- NEVER say "Bayesian" — always say "Reverse Formula".
- DIRECTION LOCK: Your analysis direction MUST match the [MATHEMATICAL ENGINE] verdict. If math says UNDER, write UNDER analysis. If math says OVER, write OVER analysis. This is non-negotiable.

CALIBRATION RULES:
- TIGHT EDGE: If projected value is within ±1.0 of the line, cap confidence at 60%.
- BINARY LINES (0.5): UNDER 0.5 confidence NEVER exceeds 55%.
- DEFENDER PASSES: Ball-playing CBs/LBs in possession teams hit 60-90+ per game routinely.

JSON: {"confidenceScore":0,"confidenceLevel":"","aiProjection":0,"sharpSummary":"","reasoning":"","scenarioAnalysis":"","keyEvidence":"","sensitivityTests":"","subRisk":"","gameFlowDynamics":"","uncertaintyNote":"","tacticalBreakdown":"","matchupOverview":{"homeTeam":"","awayTeam":"","favorite":"","moneyline":{"home":"","draw":"","away":""},"expectedPossession":{"home":0,"away":0},"expectedGameType":"","keyMatchupFactor":""},"bayesianMetrics":{"priorMean":0,"momentumEffect":0,"covariateAdjustment":0,"reversalFlag":"stable"},"scenarioProbabilities":{"best":0,"base":0,"worst":0},"probabilityCurve":[],"recentSamples":[],"player":{"id":0,"name":"","team":"","position":""},"opponent":"","propType":"","line":0,"confidenceInterval":[0,0],"tacticalAlerts":[]}"""

        # Build the data payload — use GPT summary as primary + Wave 2 deep data as supplement
        wave2_supplement = {}
        if player_game_logs:
            target_field_map = {
                "pass_attempts": "passes_total", "shots": "shots_total", "shots_on_target": "shots_on",
                "tackles": "tackles_total", "key_passes": "passes_key", "shots_assisted": "passes_key",
                "saves": "goals_saves",
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
                game_log_brief.append(f"{g.get('date','')[:10]} vs {g.get('opponent','')} ({g.get('venue','')}, {g.get('minutes',0)}min): {val}")
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
                _team_win_odds = float(_bk_odds.get("homeWin" if player_venue == "home" else "awayWin", 99))
                _opp_win_odds  = float(_bk_odds.get("awayWin" if player_venue == "home" else "homeWin", 99))
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

            gk_pass_context = f"""
[GK PASS VOLUME CONTEXT — INVERTED POSSESSION MODEL]
{req.playerName} is a GOALKEEPER. Pass volume rules are INVERTED vs outfield players.
Venue: {_gk_venue_lbl} | Expected possession: {_gk_exp_poss}% (team season avg: {_gk_team_avg}%, gap: {_gk_poss_gap:+.1f}pp)
Opponent expected possession: {_gk_opp_poss}%
Scenario: {_gk_scenario}
KEY PRINCIPLE: A GK defending deep = maximum back-pass recycling. A GK on a dominant team = barely touched. This is the single most important factor for GK pass props.{_gk_blowout_warning}"""

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

        if final_data_parts:
            final_data = "\n\n".join(final_data_parts)[:10000]
            if saves_context:
                final_data += f"\n\n{saves_context}"
            if gk_pass_context:
                final_data += f"\n\n{gk_pass_context}"
            # NOTE: position_context is injected separately in the prompt (never truncated)
        else:
            final_data = json.dumps(historical_data, default=str)[:8000]

        # =============================================
        # MATCH DOMINANCE CONTEXT — kept as separate prompt block (not inside final_data)
        # =============================================
        dom_context = ""
        if match_dominance.get("expectedPoss", 50) != 50 or match_dominance.get("notes"):
            dom_notes = "\n".join(f"  - {n}" for n in match_dominance.get("notes", []))
            dom_context = f"""
[MATCH DOMINANCE ANALYSIS — DO NOT IGNORE]
Expected possession for {corrected_team_name}: {match_dominance['expectedPoss']}% (season avg: {match_dominance.get('teamSeasonAvg', '?')}%)
Expected possession for {req.opponentName}: {match_dominance['oppExpectedPoss']}% (season avg: {match_dominance.get('oppSeasonAvg', '?')}%)
{dom_notes}
>>> CRITICAL: If expected possession is HIGHER than season average, pass-dependent players (DLP, CM, CAM) WILL exceed their historical averages.
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
                    match_context += "\n** KNOCKOUT/ELIMINATION MATCH — Higher stakes, tactical conservatism likely, possible extra time. Account for this in projections.**"

        # ── SITUATION ENGINE CONTEXT BLOCK ─────────────────────────────────────
        _sit_context_block = game_situation.get("contextBlock", "")
        if _sit_context_block:
            match_context += f"\n\n{_sit_context_block}"

        # ── WEB INTELLIGENCE ────────────────────────────────────────────────────
        if web_intel:
            match_context += f"\n\n[LIVE WEB INTELLIGENCE — Pre-match intel fetched in real-time]\n{web_intel}\n>>> Integrate this live intelligence into your analysis. Prioritize confirmed injuries and lineup changes. <<<" 

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
            "new york red bulls": "New York Red Bulls — NOT NYCFC. Do NOT mention New York City FC.",
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
                # raw format: "2025-03-15 vs Osasuna (away, 90min): 43"
                _m = _re_log.match(r"(\d{4}-(\d{2})-(\d{2})) vs (.+?) \((.+?), (\d+)min\): (.+)", _gs)
                if _m:
                    _date_lbl = f"{int(_m.group(2))}/{int(_m.group(3))}"
                    _opp_lbl  = _m.group(4).strip()
                    _venue_lbl = _m.group(5).strip()
                    _min_lbl  = _m.group(6)
                    _val_lbl  = _m.group(7).strip()
                    _fmt_games.append(f"{_val_lbl} vs {_opp_lbl} ({_date_lbl}, {_min_lbl}min, {_venue_lbl})")
                else:
                    _fmt_games.append(_gs)
            _gl_raw_avg  = _gl_data.get("rawAvg", "?")
            _gl_home_avg = _gl_data.get("homeAvg", "?")
            _gl_away_avg = _gl_data.get("awayAvg", "?")
            _gl_sample   = _gl_data.get("sampleSize", len(_fmt_games))
            _recent_log_str = f"""
[PLAYER RECENT GAME LOG — {req.propType.upper()} — LAST {len(_fmt_games)} GAMES]
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

        prompt = f"""{req.playerName} ({display_position}) — plays for {corrected_team_name} ({player_venue.upper()}) | OPPONENT: {req.opponentName} | {req.propType} line {req.line}
IMPORTANT: This player's current CLUB is {corrected_team_name}. Do NOT reference any national team or previous club in your analysis — use only "{corrected_team_name}" when referring to this player's team.{_disambig_note}
Odds: {json.dumps(match_odds.get('bookmakerOdds',{}), default=str) if match_odds else 'N/A'}{match_context}
{pronoun_note}
{_recent_log_str}
{hit_rate_context}
{bayesian_prompt_anchor}
{_suppression_context}
{dom_context}
{position_context}
{final_data[:3500]}

Analyze ALL data thoroughly. Return JSON only."""

        async def call_gemini(label="gemini", model="gemini-2.5-flash"):
            """Gemini — primary AI synthesis engine ."""
            if not GEMINI_API_KEY:
                return None
            import httpx as _httpx
            import re as _re
            import html as _html
            try:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
                payload = {
                    "systemInstruction": {"parts": [{"text": PREDICTION_SYSTEM}]},
                    "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "temperature": 0.0,
                        "maxOutputTokens": 4000,
                        "thinkingConfig": {"thinkingBudget": 2048},
                        "responseMimeType": "application/json",
                    },
                }
                async with _httpx.AsyncClient(timeout=_httpx.Timeout(50, connect=10)) as _c:
                    resp = await _c.post(url, json=payload)
                    if resp.status_code != 200:
                        print(f"[MULTI-AI] Gemini error {resp.status_code}: {resp.text[:200]}")
                        return None
                    parts = resp.json().get("candidates", [{}])[0].get("content", {}).get("parts", [])
                    text = "".join(p.get("text", "") for p in parts).strip()

                text = _re.sub(r"```(?:json)?\s*", "", text)
                text = _re.sub(r"```\s*$", "", text, flags=_re.MULTILINE)
                text = _html.unescape(text).strip()
                start = text.find("{")
                if start >= 0:
                    candidate = text[start:]
                    try:
                        result = json.loads(candidate)
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
                raise ValueError("No valid JSON in Gemini response")
            except Exception as e:
                print(f"[MULTI-AI] {label} failed: {e}")
                return None

        # =============================================
        # AI SYNTHESIS: Grok primary, Gemini fallback
        # Projection comes ONLY from the math engine — AI projectedValue is NEVER used.
        # =============================================
        ai_result = None

        # pv is set from early_bayes here as a temporary anchor; real_bayes overwrites it later.
        pv = early_bayes["posteriorMean"] if early_bayes and early_bayes.get("posteriorMean") else req.line

        async def call_grok(label="grok", model="grok-3"):
            """Grok — primary AI synthesis engine."""
            if not XAI_API_KEY:
                return None
            import re as _re
            import html as _html
            try:
                url = "https://api.x.ai/v1/chat/completions"
                payload = {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": PREDICTION_SYSTEM},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.0,
                    "max_tokens": 4000,
                }
                import httpx as _httpx
                async with _httpx.AsyncClient(timeout=_httpx.Timeout(45, connect=10)) as _c:
                    resp = await _c.post(url, json=payload, headers={"Authorization": f"Bearer {XAI_API_KEY}", "Content-Type": "application/json"})
                    if resp.status_code != 200:
                        print(f"[MULTI-AI] Grok error {resp.status_code}: {resp.text[:200]}")
                        return None
                    text = resp.json().get("choices", [{}])[0].get("message", {}).get("content", "").strip()
                text = _re.sub(r"```(?:json)?\s*", "", text)
                text = _re.sub(r"```\s*$", "", text, flags=_re.MULTILINE)
                text = _html.unescape(text).strip()
                start = text.find("{")
                if start >= 0:
                    candidate = text[start:]
                    try:
                        result = json.loads(candidate)
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
                raise ValueError("No valid JSON in Grok response")
            except Exception as e:
                print(f"[MULTI-AI] {label} failed: {e}")
                return None

        # AI synthesis: Grok primary, Gemini fallback
        ai_result = await call_grok()
        if ai_result:
            print("[AI] Grok synthesis succeeded")

        # BAYESIAN FALLBACK: If Grok AI failed (no text), try Gemini, then build minimal result from math
        if not ai_result or not isinstance(ai_result, dict) or not ai_result.get("tacticalBreakdown"):
            ai_result = await call_gemini()
            if ai_result:
                print("[AI] Gemini fallback synthesis succeeded")

        if not ai_result or not isinstance(ai_result, dict) or not ai_result.get("tacticalBreakdown"):
            if early_bayes and early_bayes.get("posteriorMean"):
                pv = early_bayes["posteriorMean"]
                # Cap confidence at 72% (shows "High") when AI fails — the math had
                # no AI sanity check so claiming "Very High" confidence would be misleading.
                _raw_bayes_conf = max(early_bayes.get("pOver", 50), early_bayes.get("pUnder", 50))
                _capped_conf = min(_raw_bayes_conf, 72)
                ai_result = {
                    "projectedValue": pv,
                    "recommendation": early_bayes.get("recommendation", "over"),
                    "confidenceScore": _capped_conf,
                    "reasoning": "AI models unavailable — projection based on Reverse Formula mathematical analysis.",
                    "_source": "bayesian_fallback",
                }
                print(f"[BAYESIAN FALLBACK] All AI models failed — using Bayesian projection: {pv}")
            else:
                # No Bayesian data either — use the line as last resort
                pv = req.line
                ai_result = {
                    "projectedValue": pv,
                    "recommendation": "over",
                    "confidenceScore": 50,
                    "reasoning": "Insufficient data for mathematical projection. AI models unavailable.",
                    "_source": "fallback",
                }
                print(f"[FALLBACK] No Bayesian data and all AI models failed — using line: {pv}")

        source_model = ai_result.get("_source", "gemini")
        print(f"[TIMING] {source_model} done: {_t.time()-_t0:.1f}s, proj={pv}")

        prediction = ai_result.copy()
        prediction.pop("_source", None)
        prediction["projectedValue"] = pv
        prediction["recommendation"] = "over" if pv > req.line else "under"
        prediction["sport"] = req.sport

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
        prediction["matchDominance"] = {
            "applied": match_dominance["multiplier"] != 1.0,
            "multiplier": match_dominance["multiplier"],
            "expectedPoss": match_dominance["expectedPoss"],
            "teamSeasonAvg": match_dominance.get("teamSeasonAvg"),
            "oppSeasonAvg": match_dominance.get("oppSeasonAvg"),
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
            },
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
                # GK pass_attempts: opponent pressing style is the single most predictive
                # factor for GK pass volume after venue. When facing Betis (3 home H2H
                # games → 24.67 avg) vs a general home avg of 35, the H2H is the clearest
                # signal of how this specific opponent affects this GK's distribution.
                # Raise GK H2H rate (12% per game, cap 40%) to let opponent-specific
                # history dominate over the general season baseline.
                _is_gk_h2h = (specific_position or "").upper() in {"GK", "GOALKEEPER"} or \
                              (player_position or "").lower() == "goalkeeper"
                if _is_gk_h2h and req.propType in {"pass_attempts", "passes"}:
                    _h2h_weight = min(_h2h_n_use * 0.13, 0.40)  # GK: 13% per game, cap 40%
                _old_bp = bayesian_posterior
                bayesian_posterior = round(
                    _old_bp * (1 - _h2h_weight) + _h2h_avg_use * _h2h_weight, 1
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
                        _exp_poss  = match_dominance.get("expectedPoss", 50.0)
                        _avg_poss  = match_dominance.get("teamSeasonAvg") or 50.0
                        _poss_diff = _exp_poss - _avg_poss      # +ve = more poss than usual
                        _opp_diff  = _opp_allowed_avg - _old_bp # +ve = opp allows more than proj
                        # Same-direction AND both material (≥5pp poss gap, ≥5 stat gap)
                        if (_poss_diff * _opp_diff > 0
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

                    bayesian_posterior = round(
                        _old_bp * (1 - _opp_weight) + _opp_allowed_avg * _opp_weight, 1
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
                print(f"[SITUATION MULT] Bayesian {_old_bp:.1f} × {_sit_bayes_mult:.3f} = {bayesian_posterior:.1f} ({req.propType})")
                real_bayes["posteriorMean"] = bayesian_posterior
                real_bayes["situationalMultiplier"] = _sit_bayes_mult
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
        # POST-PROJECTION DOMINANCE SCALING — SELECTIVE
        # Negative branch: low-possession team facing a possession monster → scale DOWN.
        # Positive branch: team expected to dominate well above their own season avg → scale UP.
        # The positive branch only fires when the OPP CONVERGENCE boost above was NOT
        # sufficient (i.e., the expected poss gap is very large — a historically rare setup).
        # In most cases the OPP CONVERGENCE boost inside the Bayesian step already handles it.
        # =============================================
        poss_sensitive = {"pass_attempts", "passes", "key_passes", "crosses", "dribbles"}

        _is_gk_dom = (specific_position or "").upper() in {"GK", "GOALKEEPER"} or (player_position or "").lower() == "goalkeeper"
        if req.propType in poss_sensitive and not _is_gk_dom and match_dominance.get("multiplier", 1.0) != 1.0:
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
        _cg_rec  = str(prediction.get("recommendation", "")).lower()
        _cg_proj = prediction.get("projectedValue")
        if _cg_proj is not None and req.line and req.line > 0:
            if _cg_rec == "under" and _cg_proj > req.line:
                _cg_fixed = round((req.line - 0.5) * 2) / 2
                prediction["projectedValue"] = _cg_fixed
                print(f"[CONSISTENCY GUARD] {req.playerName}: projectedValue {_cg_proj} → {_cg_fixed} (rec=UNDER, was above line {req.line})")
            elif _cg_rec == "over" and _cg_proj < req.line:
                _cg_fixed = round((req.line + 0.5) * 2) / 2
                prediction["projectedValue"] = _cg_fixed
                print(f"[CONSISTENCY GUARD] {req.playerName}: projectedValue {_cg_proj} → {_cg_fixed} (rec=OVER, was below line {req.line})")

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

            # If direction flipped, the projected value must be on the right
            # side of the line for visual consistency. Use the closer half-
            # integer offset like CONSISTENCY GUARD did.
            if _bt_old_rec != _bt_dir:
                _bt_proj = prediction.get("projectedValue", req.line)
                if _bt_dir == "under" and _bt_proj > req.line:
                    prediction["projectedValue"] = round((req.line - 0.5) * 2) / 2
                elif _bt_dir == "over" and _bt_proj < req.line:
                    prediction["projectedValue"] = round((req.line + 0.5) * 2) / 2

            print(
                f"[BAYESIAN TRUTH] {req.playerName}/{req.propType}: "
                f"P(OVER)={_bt_p_over}% P(UNDER)={_bt_p_under}% → "
                f"{_bt_dir.upper()} {_bt_new_conf}% ({_bt_new_lvl})"
                + (f" [FLIPPED from {_bt_old_rec.upper()} {_bt_old_conf}%]" if _bt_old_rec != _bt_dir else f" [confidence {_bt_old_conf}→{_bt_new_conf}]")
            )

            # ── LOW CONVICTION FILTER ─────────────────────────────────────────
            # When Bayesian max(P(OVER), P(UNDER)) < 60%, the model has weak
            # signal — the line is close to the projection mean and the
            # distribution straddles both sides. Cap confidence at 54% and
            # expose lowConviction=True so the UI can surface a warning.
            # Fires inside the _bt_src guard so it only runs when Bayesian
            # data is available.
            _bt_conv = max(_bt_p_over, _bt_p_under)
            if _bt_conv < 60.0 and prediction.get("recommendation", "").upper() != "PASS":
                prediction["lowConviction"] = True
                if (prediction.get("confidenceScore") or 0) > 54:
                    prediction["confidenceScore"] = 54
                    prediction["confidenceLevel"] = "Low"
                print(f"[LOW CONV] {req.playerName}/{req.propType}: P(max)={_bt_conv:.1f}% < 60% → capped 54% Low")
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

        # ── MARKET DISTANCE GUARD ────────────────────────────────────────────
        # When our projection is ≥35% away from the market line, the prior is
        # likely contaminated (stale seasons, old-club era, position mismatch).
        # The Bayesian distribution places almost all mass on one side when the
        # gap is this large — producing 90-99% confidence that is not earned.
        # Cap at 55% (Medium) and surface a visible warning.
        # Fires AFTER BAYESIAN TRUTH so the cap applies to the final Bayesian
        # confidence, not an intermediate AI estimate.
        _mg_proj = prediction.get("projectedValue", req.line)
        _market_distance_fired = False
        if req.line > 0 and _mg_proj is not None:
            _mg_gap_pct = abs(_mg_proj - req.line) / req.line * 100
            if _mg_gap_pct >= 35:
                _market_distance_fired = True
                _mg_pre = prediction.get("confidenceScore", 50)
                if _mg_pre > 55:
                    prediction["confidenceScore"] = 55
                    prediction["confidenceLevel"] = "Medium"
                    prediction["tacticalAlerts"] = prediction.get("tacticalAlerts", []) + [
                        f"MARKET DISTANCE: Model projects {_mg_proj} but line is {req.line} "
                        f"({_mg_gap_pct:.0f}% gap) — prior may be from wrong club era or "
                        f"stale season. Treat with caution."
                    ]
                    print(f"[MARKET DIST] {req.playerName}: proj={_mg_proj} line={req.line} "
                          f"gap={_mg_gap_pct:.0f}% → confidence capped {_mg_pre}→55%")
                # When the gap is extreme (≥60%), the direction is so obvious the pick cannot
                # be a coin-flip. Clear any coinFlip flag set by upstream guards so the edge
                # rating uses the real projection margin instead of forcing NO EDGE.
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
        if _er_rec == "PASS":
            _safety_rating = "AVOID"
            _er_hit_rate   = None
            _er_n          = 0
        elif _er_coin:
            _safety_rating = "RISKY"
            _er_hit_rate   = None
            _er_n          = 0
        else:
            _ps = _get_prop_safety(_er_prop, _er_rec)
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
            else:
                fb_home_avg = opp_poss
                fb_away_avg = team_poss
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
        if match_odds:
            if match_odds.get("americanOdds"):
                ao = match_odds["americanOdds"]
                if ao.get("home") and ao.get("away") and ao.get("draw"):
                    real_matchup["moneyline"] = {
                        "home": str(ao["home"]),
                        "draw": str(ao["draw"]),
                        "away": str(ao["away"]),
                    }
            elif match_odds.get("bookmakerOdds"):
                bo = match_odds["bookmakerOdds"]
                h, d, a = bo.get("homeWin", ""), bo.get("draw", ""), bo.get("awayWin", "")
                if h and d and a and h != "N/A" and d != "N/A" and a != "N/A":
                    real_matchup["moneyline"] = {"home": h, "draw": d, "away": a}
            if match_odds.get("favorite"):
                real_matchup["favorite"] = match_odds["favorite"]
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
        real_matchup["homeTeam"] = player_team_display if player_venue == "home" else req.opponentName
        real_matchup["awayTeam"] = req.opponentName if player_venue == "home" else player_team_display

        # Expose team/opponent names at the TOP LEVEL of the response so the
        # frontend can use them directly without digging into matchupOverview.
        # The frontend checks prediction.opponentName, prediction.teamName,
        # prediction.homeTeam, and prediction.awayTeam — these were missing,
        # causing "HOME" / "AWAY" fallback labels in the possession bar.
        prediction["opponentName"] = req.opponentName or ""
        prediction["teamName"]     = corrected_team_name or req.teamName or ""
        prediction["homeTeam"]     = real_matchup["homeTeam"]
        prediction["awayTeam"]     = real_matchup["awayTeam"]
        prediction["isHome"]       = (player_venue == "home")

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
            "saves": "goals_saves",
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
        _ai_ss = prediction.get("sharpSummary", "")

        if _ai_td and len(_ai_td.strip()) > 100:
            # ── AI produced a real narrative — keep it, append math footer ──
            prediction["tacticalBreakdown"] = _ai_td.strip() + "\n\n---\n" + _m_math + "\n" + _m_tldr
            # Keep AI's sharpSummary if it's non-empty and substantive
            if not (_ai_ss and len(_ai_ss.strip()) > 20):
                prediction["sharpSummary"] = _m_sharp_summary
            print(f"[AI SUMMARY] Using AI tacticalBreakdown ({len(_ai_td)} chars) + math footer appended")
        else:
            # ── AI failed or returned empty — fall back to pure-math breakdown ──
            prediction["tacticalBreakdown"] = _m_full_block
            prediction["sharpSummary"] = _m_sharp_summary
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
                "tackles": "tackles_total", "key_passes": "passes_key", "saves": "goals_saves",
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
        # positionComparison removed — not shown in UI

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
                        )
        except Exception as _calib_err:
            print(f"[CONF CALIB] application failed: {_calib_err}")

        prediction["_ts"] = datetime.now(timezone.utc)
        await db.predictions.insert_one(prediction)
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

