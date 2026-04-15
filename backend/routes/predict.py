import json
import uuid
import asyncio as aio
import statistics as stats_mod
import traceback
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException
from emergentintegrations.llm.chat import LlmChat, UserMessage

from openai import OpenAI

from config import (
    db, EMERGENT_LLM_KEY, XAI_API_KEY, CURRENT_SEASON,
    WOMENS_LEAGUE_IDS, STAT_FIELD_MAP, STAT_LAMBDA_MAP,
)
from models import PredictionRequest
from utils import api_football_request, get_recent_fixtures_fast, strip_accents, get_soccer_odds, decimal_to_american

router = APIRouter(prefix="/api", tags=["predict"])

# Match dominance cache: keyed by (home_team_id, away_team_id)
# Ensures the SAME game always returns identical possession numbers regardless of which player is scanned.
import time as _time
_match_dom_cache: dict = {}
_MATCH_DOM_TTL = 3600 * 6  # 6 hours

@router.post("/predict")
async def predict(req: PredictionRequest):
    try:
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        cache_query = {
            "player.id": req.playerId,
            "propType": req.propType,
            "line": req.line,
            "_request.opponentId": req.opponentId,
            "_created": {"$gte": today_str},
        }
        if req.playerId and req.playerId != 0:
            cached = await db.predictions.find_one(cache_query, sort=[("_created", -1)])
            if cached:
                cached.pop("_id", None)
                return cached

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
            all_data = None
            for s in [CURRENT_SEASON + 1, CURRENT_SEASON, CURRENT_SEASON - 1, CURRENT_SEASON - 2]:
                try:
                    data = await api_football_request("players", {"id": req.playerId, "season": s})
                    if data:
                        if all_data is None:
                            all_data = data[0]
                        else:
                            all_data.setdefault("statistics", []).extend(data[0].get("statistics", []))
                except Exception:
                    continue
            return all_data

        actual_team_id = req.teamId
        league_id = req.leagueId or 39

        # ── AUTO-RESOLVE missing IDs from team/player names using local cache ──
        # This runs BEFORE ai_only_mode is decided, so predictions always have
        # real fixture data even when the scan didn't return numeric IDs.
        _resolved_opp_id = req.opponentId or 0
        _resolved_player_id = req.playerId or 0

        try:
            from team_resolver import find_team as _find_team
            from cache import get_player_by_name as _get_player_by_name

            # 1. Resolve team ID from team name
            if (not actual_team_id or actual_team_id == 0) and req.teamName:
                try:
                    _t = await _find_team(req.teamName, league_id=league_id if league_id and league_id != 39 else None)
                    if _t and _t.get("teamId"):
                        actual_team_id = _t["teamId"]
                        print(f"[ID RESOLVE] '{req.teamName}' → teamId={actual_team_id}")
                except Exception as _re:
                    print(f"[ID RESOLVE] team lookup failed: {_re}")

            # 2. Resolve opponent ID from opponent name
            if (not _resolved_opp_id or _resolved_opp_id == 0) and req.opponentName:
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
                    )
                    if _p and _p.get("playerId"):
                        _resolved_player_id = _p["playerId"]
                        if not actual_team_id or actual_team_id == 0:
                            actual_team_id = _p.get("teamId") or actual_team_id
                        print(f"[ID RESOLVE] '{req.playerName}' → playerId={_resolved_player_id}, teamId={actual_team_id}")
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
                match_date = fixture_match.get("fixture", {}).get("date", "")
                if match_round:
                    result["matchRound"] = match_round
                if match_league:
                    result["matchLeague"] = match_league
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
        if ai_only_mode:
            print(f"[AI-ONLY] Running in AI-only mode for {req.playerName} — teamId={actual_team_id}, opponentId={req.opponentId}")

            async def noop_none(): return None
            async def noop_list(): return []

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
            h2h_task = safe_fetch("fixtures/headtohead", {"h2h": f"{actual_team_id}-{req.opponentId}", "last": 10}, [])

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
        if actual_team_id and actual_team_id != 0 and not recent_fixtures:
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
                        {"_k": cache_key}, {"$set": {"_k": cache_key, "d": result}}, upsert=True
                    )
                    result["date"]     = fix.get("date", "")[:10]
                    result["opponent"] = fix.get("opponent", "")
                    result["venue"]    = fix.get("venue", "")
                    result["score"]    = f"{fix.get('homeGoals',0)}-{fix.get('awayGoals',0)}"
                    return result
                except Exception:
                    return None

            tasks = [fetch_one(fix) for fix in fixture_list[:limit]]
            results_raw = await aio.gather(*tasks, return_exceptions=True)
            return [r for r in results_raw if r and not isinstance(r, Exception)]

        # 2. Player game-by-game box scores from recent fixtures
        async def fetch_player_game_logs(fixture_list, player_id, limit=35):
            """Fetch player's individual stats — uses MongoDB cache for finished fixtures."""
            player_name_lower = strip_accents(req.playerName.lower().split()[-1]) if req.playerName else ""

            async def fetch_one_log(fix):
                fid = fix.get("fixtureId")
                if not fid:
                    return None
                try:
                    # Check MongoDB cache first
                    cache_key = f"fxp_{fid}_{player_id}"
                    cached = await db.fixture_player_cache.find_one({"_k": cache_key}, {"_id": 0, "d": 1})
                    if cached and cached.get("d"):
                        gl = cached["d"]
                        gl["date"] = fix.get("date", "")[:10]
                        gl["opponent"] = fix.get("opponent", "")
                        gl["venue"] = fix.get("venue", "")
                        gl["score"] = f"{fix.get('homeGoals',0)}-{fix.get('awayGoals',0)}"
                        gl["league"] = fix.get("league", "")
                        gl["round"] = fix.get("round", "")
                        stat_field_map = {
                            "goals": "goals_total", "assists": "goals_assists",
                            "shots_assisted": "passes_key",
                            "pass_attempts": "passes_total", "shots": "shots_total",
                            "shots_on_target": "shots_on", "tackles": "tackles_total",
                            "key_passes": "passes_key", "saves": "goals_saves",
                            "interceptions": "tackles_interceptions", "blocks": "tackles_blocks",
                            "dribbles": "dribbles_attempts", "dribbles_success": "dribbles_success",
                            "fouls_drawn": "fouls_drawn", "fouls_committed": "fouls_committed",
                            "crosses": "passes_crosses", "clearances": "tackles_clearances",
                            "duels_won": "duels_won", "yellow_cards": "cards_yellow",
                        }
                        raw_val = gl.get(stat_field_map.get(req.propType, ""), None)
                        minutes = gl.get("minutes", 0)
                        if raw_val is not None and minutes > 0:
                            gl["targetStatPer90"] = round((raw_val / minutes) * 90, 2)
                        return gl

                    data = await api_football_request("fixtures/players", {"fixture": fid})
                    if not data:
                        return None

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

                    # One API call returns all 22+ players — cache ALL of them now
                    # so any teammate/opponent scanned next gets instant cache hits
                    matched_stats = None
                    matched_pid = None
                    all_player_logs = {}  # pid -> game_log dict

                    for team_data in data:
                        for p in team_data.get("players", []):
                            pid = p.get("player", {}).get("id")
                            pname = strip_accents((p.get("player", {}).get("name") or "").lower())
                            stats = p.get("statistics", [{}])[0] if p.get("statistics") else {}
                            minutes = stats.get("games", {}).get("minutes") or 0
                            if pid:
                                gl = _build_game_log(stats)
                                all_player_logs[pid] = gl
                                if minutes > 0 and matched_stats is None:
                                    if pid == player_id or (player_name_lower and player_name_lower in pname):
                                        matched_stats = stats
                                        matched_pid = pid

                    # Bulk-write every player to cache (fire-and-forget, don't await one-by-one)
                    import asyncio as _asyncio
                    async def _bulk_cache():
                        ops = []
                        for pid_k, gl_v in all_player_logs.items():
                            k = f"fxp_{fid}_{pid_k}"
                            ops.append(db.fixture_player_cache.update_one(
                                {"_k": k}, {"$set": {"_k": k, "d": gl_v}}, upsert=True
                            ))
                        if ops:
                            await _asyncio.gather(*ops, return_exceptions=True)
                    _asyncio.ensure_future(_bulk_cache())

                    if not matched_stats:
                        # Cache a None sentinel for this specific player so we skip them in future
                        await db.fixture_player_cache.update_one(
                            {"_k": cache_key}, {"$set": {"_k": cache_key, "d": None}}, upsert=True
                        )
                        return None
                    stats = matched_stats
                    game_log = _build_game_log(stats)
                    # Add contextual fields for return
                    game_log["date"] = fix.get("date", "")[:10]
                    game_log["opponent"] = fix.get("opponent", "")
                    game_log["venue"] = fix.get("venue", "")
                    game_log["score"] = f"{fix.get('homeGoals',0)}-{fix.get('awayGoals',0)}"
                    game_log["league"] = fix.get("league", "")
                    game_log["round"] = fix.get("round", "")
                    stat_field_map = {
                        "goals": "goals_total", "assists": "goals_assists",
                        "shots_assisted": "passes_key",
                        "pass_attempts": "passes_total", "shots": "shots_total",
                        "shots_on_target": "shots_on", "tackles": "tackles_total",
                        "key_passes": "passes_key", "saves": "goals_saves",
                        "interceptions": "tackles_interceptions", "blocks": "tackles_blocks",
                        "dribbles": "dribbles_attempts", "dribbles_success": "dribbles_success",
                        "fouls_drawn": "fouls_drawn", "fouls_committed": "fouls_committed",
                        "crosses": "passes_crosses", "clearances": "tackles_clearances",
                        "duels_won": "duels_won", "yellow_cards": "cards_yellow",
                    }
                    raw_val = game_log.get(stat_field_map.get(req.propType, ""), None)
                    if raw_val is not None and minutes > 0:
                        game_log["targetStatPer90"] = round((raw_val / minutes) * 90, 2)
                    return game_log
                except Exception:
                    return None

            # Phase 1: Batch-check MongoDB cache for all fixtures at once
            fixture_subset = fixture_list[:limit]
            cache_keys = [f"fxp_{fix.get('fixtureId')}_{player_id}" for fix in fixture_subset if fix.get("fixtureId")]
            cached_map: dict = {}
            if cache_keys:
                async for cdoc in db.fixture_player_cache.find({"_k": {"$in": cache_keys}}, {"_k": 1, "d": 1}):
                    cached_map[cdoc["_k"]] = cdoc.get("d")

            # Phase 2: Sort fixtures — cached hits first, then API misses
            cached_fixes = []
            api_fixes = []
            for fix in fixture_subset:
                fid = fix.get("fixtureId")
                if not fid:
                    continue
                ck = f"fxp_{fid}_{player_id}"
                if ck in cached_map and cached_map[ck] is not None:
                    cached_fixes.append(fix)
                else:
                    api_fixes.append(fix)

            # Phase 3: Collect cached results instantly
            collected = []
            for fix in cached_fixes:
                r = await fetch_one_log(fix)
                if r:
                    collected.append(r)

            # Phase 4: Fetch API misses with a semaphore (max 6 concurrent requests)
            if len(collected) < 20 and api_fixes:
                _sem = aio.Semaphore(6)
                async def _sem_fetch(fix):
                    async with _sem:
                        return await fetch_one_log(fix)

                api_tasks = [_sem_fetch(fix) for fix in api_fixes]
                api_results = await aio.gather(*api_tasks, return_exceptions=True)
                for r in api_results:
                    if r and not isinstance(r, Exception):
                        collected.append(r)

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

                            results.append({
                                "name": p_name,
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
            if not opponent_recent_raw:
                opponent_recent_raw = await api_football_request("fixtures", {"team": safe_opp_id, "last": 15})
        opponent_fixture_list = []
        if opponent_recent_raw:
            for f in opponent_recent_raw[:15]:
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
>>> Moneyline tells you expected game flow. Heavy favorites control possession and tempo. Underdogs may sit deep (deflating pass/shot stats for attacker props). <<<""")
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

        # Wave 2: Fetch deep fixture data + Grok digest + Situation Engine + Web Intel in parallel
        from grok_engine import build_grok_digest, fetch_web_intel
        from situation_engine import build_game_situation
        grok_digest_task = build_grok_digest(
            player_name=req.playerName, team_name=corrected_team_name or "",
            opponent_name=req.opponentName, prop_type=req.propType,
            line=req.line, venue=player_venue,
            player_stats=player_stats, team_stats=team_stats,
            opponent_stats=opponent_stats, h2h_data=h2h_data,
            match_odds=match_odds, standings=standings,
            player_game_logs=[], team_fixture_stats=[],
            opponent_fixture_stats=[], match_dominance={},
            sport="soccer"
        )

        # Situation engine inputs
        _sit_is_home = player_venue == "home"
        _sit_home_id = actual_team_id if _sit_is_home else req.opponentId
        _sit_away_id = req.opponentId if _sit_is_home else actual_team_id
        _sit_match_round = (match_odds or {}).get("matchRound", "")
        _sit_match_league = (match_odds or {}).get("matchLeague", "")
        _sit_match_date = (match_odds or {}).get("matchDate", "")
        _sit_fixture_id = (match_odds or {}).get("fixtureId")

        situation_task = build_game_situation(
            home_team_id=_sit_home_id,
            away_team_id=_sit_away_id,
            is_player_home=_sit_is_home,
            league_id=league_id or 39,
            match_round=_sit_match_round,
            fixture_id=_sit_fixture_id,
            player_team_name=corrected_team_name or req.teamName or "",
            opponent_name=req.opponentName or "",
            prop_type=req.propType,
        )

        web_intel_task = fetch_web_intel(
            player_team=corrected_team_name or req.teamName or "",
            opponent=req.opponentName or "",
            match_date=_sit_match_date,
            match_round=_sit_match_round,
            league=_sit_match_league,
        )

        all_wave2 = aio.gather(
            team_fixture_stats_task, opponent_fixture_stats_task, player_game_logs_task,
            grok_digest_task, situation_task, web_intel_task,
            return_exceptions=True
        )
        try:
            results = await aio.wait_for(all_wave2, timeout=40)
        except aio.TimeoutError:
            results = [None, None, None, None, None, None]
            print(f"[WAVE2 TIMEOUT] Wave 2 exceeded 40s for {req.playerName}")

        team_fixture_stats = results[0] if not isinstance(results[0], (Exception, type(None))) else []
        opponent_fixture_stats = results[1] if not isinstance(results[1], (Exception, type(None))) else []
        player_game_logs = results[2] if not isinstance(results[2], (Exception, type(None))) else []
        grok_digest = results[3] if len(results) > 3 and not isinstance(results[3], (Exception, type(None))) else ""
        game_situation = results[4] if len(results) > 4 and not isinstance(results[4], (Exception, type(None))) else {}
        web_intel = results[5] if len(results) > 5 and not isinstance(results[5], (Exception, type(None))) else ""
        if not game_situation:
            game_situation = {"isKnockout": False, "isSecondLeg": False, "aggregate": {}, "multipliers": {}, "injuries": {}, "contextBlock": ""}

        # =============================================
        if not player_game_logs:
            print(f"[NO GAME LOGS] {req.playerName}/{req.propType}: no fixture-level game logs available. Using line as prior.")

        # =============================================
        # MATCH DOMINANCE: Opponent-aware possession + context multiplier
        # =============================================
        def compute_match_dominance(team_stats_list, opp_stats_list, odds, is_home, standing_data):
            """Compute expected possession using opponent-aware model + odds adjustment.
            SYMMETRIC: Always computes from HOME team perspective first, then maps back.
            This ensures the SAME match always produces identical possession numbers
            regardless of which player (home or away) triggers the analysis."""
            dom = {"expectedPoss": 50.0, "oppExpectedPoss": 50.0, "multiplier": 1.0, "notes": []}

            def avg_poss(sl):
                vals = []
                for s in (sl or []):
                    p = s.get("possession")
                    if p is not None:
                        try:
                            vals.append(float(str(p).replace("%", "")))
                        except (ValueError, TypeError):
                            pass
                return round(sum(vals) / len(vals), 1) if vals else None

            team_avg = avg_poss(team_stats_list)
            opp_avg = avg_poss(opp_stats_list)

            if team_avg is not None and opp_avg is not None:
                if is_home:
                    home_avg = team_avg
                    away_avg = opp_avg
                    home_rank = standing_data.get("teamRank") if standing_data else None
                    away_rank = standing_data.get("oppRank") if standing_data else None
                else:
                    home_avg = opp_avg
                    away_avg = team_avg
                    home_rank = standing_data.get("oppRank") if standing_data else None
                    away_rank = standing_data.get("teamRank") if standing_data else None

                away_concedes = 100.0 - away_avg

                if away_avg > 57:
                    extremity = min((away_avg - 57) / 11.0, 1.0)
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

                home_boost = 2.5
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
                        if away_avg >= 57 or home_avg >= 57:
                            odds_dampener = 0.3
                            dom["notes"].append(f"Possession-dominant team in match ({max(home_avg, away_avg):.0f}% avg): odds signal dampened")
                        elif away_avg >= 53 or home_avg >= 53:
                            odds_dampener = 0.6

                        odds_adj = round(prob_diff * 12 * odds_dampener, 1)
                        odds_adj = min(7.0, max(-7.0, odds_adj))
                        home_poss += odds_adj
                        if abs(odds_adj) > 1:
                            dom["notes"].append(f"Odds signal (home={home_odds_val:.2f}, away={away_odds_val:.2f}): {odds_adj:+.1f}% poss adj")
                    except Exception:
                        pass

                home_poss = min(75.0, max(30.0, round(home_poss, 1)))
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
            print(f"[MATCH DOMINANCE CACHE HIT] {req.playerName}: home={_cached_dom['homePoss']}% away={_cached_dom['awayPoss']}%")
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
            # Also update the in-process cache with boosted values
            if _dom_cache_key:
                _ce = _match_dom_cache.get(_dom_cache_key, {}).get("dom", {})
                if _ce:
                    _ce["homePoss"] = new_home_poss
                    _ce["awayPoss"] = new_away_poss

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
                "pass_attempts": "passes_total",
                "shots": "shots_total",
                "shots_on_target": "shots_on",
                "tackles": "tackles_total",
                "key_passes": "passes_key",
                "shots_assisted": "passes_key",
                "saves": "goals_saves",
                "interceptions": "tackles_interceptions",
                "blocks": "tackles_blocks",
                "dribbles": "dribbles_attempts",
                "fouls_drawn": "fouls_drawn",
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

            historical_data["playerGameLogs"] = game_log_summary

        # =============================================
        # EARLY BAYESIAN — Compute math BEFORE AI prompt
        # This anchors the AI's reasoning so it doesn't
        # contradict the mathematical evidence.
        # =============================================
        early_bayes = None
        bayesian_prompt_anchor = ""
        try:
            from bayesian_engine import compute_bayesian_projection
            _sfm = {
                "goals": "goals_total", "assists": "goals_assists",
                "shots_assisted": "passes_key",
                "pass_attempts": "passes_total", "shots": "shots_total",
                "shots_on_target": "shots_on", "tackles": "tackles_total",
                "key_passes": "passes_key", "saves": "goals_saves",
                "interceptions": "tackles_interceptions", "blocks": "tackles_blocks",
                "dribbles": "dribbles_attempts", "dribbles_success": "dribbles_success",
                "fouls_drawn": "fouls_drawn", "fouls_committed": "fouls_committed",
                "crosses": "passes_crosses", "clearances": "tackles_clearances",
                "duels_won": "duels_won", "yellow_cards": "cards_yellow",
            }
            early_bayes = compute_bayesian_projection(
                game_logs=player_game_logs,
                prop_type=req.propType,
                line=req.line,
                venue=player_venue,
                stat_field=_sfm.get(req.propType, "passes_total"),
                opponent_fixture_stats=opponent_fixture_stats,
                match_dominance=match_dominance,
            )
            print(f"[BAYESIAN] {req.playerName}/{req.propType}: samples={early_bayes.get('priorSamples') if early_bayes else 0}, logs={len(player_game_logs)}")

            if early_bayes and early_bayes.get("priorSamples", 0) >= 3:
                bdir = early_bayes['recommendation'].upper()
                bprob = early_bayes['pOver'] if bdir == 'OVER' else early_bayes['pUnder']
                bayesian_prompt_anchor = f"""
[MATHEMATICAL ENGINE — DO NOT IGNORE]
3-Layer Reverse Formula analysis ({early_bayes['priorSamples']} games): projects {early_bayes['posteriorMean']} {bdir} (P={bprob}%).
Season avg: {early_bayes['priorMean']} | Recent form (decay-weighted): {early_bayes['momentumMean']} ({early_bayes['momentumLabel']}) | Context adj: {early_bayes['covariateAdjustment']:+.1f}
Streak: {early_bayes['streakFlag']} | Volatility: {early_bayes['volatility']} (CV={early_bayes['cv']}) | Reversal: {early_bayes['reversalFlag']}
IMPORTANT: Never use the word "Bayesian" in your response. Always say "Reverse Formula" instead.
>>> Your projectedValue MUST be within 20% of {early_bayes['posteriorMean']}. If you disagree, explain specifically why in your reasoning. <<<"""
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
                "pass_attempts": "passes_total", "shots": "shots_total", "shots_on_target": "shots_on",
                "tackles": "tackles_total", "key_passes": "passes_key", "shots_assisted": "passes_key",
                "saves": "goals_saves",
                "interceptions": "tackles_interceptions", "blocks": "tackles_blocks",
                "dribbles": "dribbles_attempts", "fouls_drawn": "fouls_drawn",
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
                                return {
                                    "date": fixture_info.get("fixture", {}).get("date", ""),
                                    "opponent": opponent_name,
                                    "venue": venue_in_match,
                                    "minutesPlayed": minutes_played,
                                    "statValues": {k: v for k, v in stat_key_map_h2h.items() if v is not None},
                                    "targetStat": stat_key_map_h2h.get(req.propType),
                                    "targetStatPer90": round((stat_key_map_h2h.get(req.propType, 0) or 0) / minutes_played * 90, 2) if minutes_played > 0 and stat_key_map_h2h.get(req.propType) else None,
                                    "matchScore": f"{home_goals}-{away_goals}",
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
        # Uses cache first, then Grok as fallback with API-Sports context
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
                # Check cache (with 30-day expiry)
                cached_pos = await db.player_positions.find_one(
                    {"playerId": req.playerId}, {"_id": 0, "specificPosition": 1, "role": 1, "updatedAt": 1}
                )
                cache_valid = False
                if cached_pos and cached_pos.get("specificPosition"):
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

                    # Build constrained position list based on API-Sports category
                    allowed_positions = GENERIC_TO_SPECIFIC.get(player_position, None)
                    if allowed_positions:
                        pos_list = ", ".join(sorted(allowed_positions))
                        category_hint = f"\nAPI-Sports categorizes this player as: {player_position}. ONLY choose from positions within that category: {pos_list}"
                    else:
                        pos_list = "GK, CB, LB, RB, LWB, RWB, CDM, CM, CAM, LM, RM, LW, RW, CF, ST, SS"
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
POSITION CLUES: CB=high tackles/blocks/aerial duels, low crosses/key passes/dribbles. LB/RB=crosses, some key passes, overlapping runs. CDM=high interceptions, moderate passing. CM=balanced. CAM=high key passes. Winger=high dribbles/crosses. ST=high shots/goals."""

                    pos_prompt = f"What is {req.playerName}'s primary position and tactical role at {corrected_team_name}?{category_hint}{stats_evidence}\nPosition must be one of: {pos_list}\nRole must be one of: Shot-Stopper, Sweeper Keeper, Ball-Playing CB, Stopper, Fullback, Wing-Back, Inverted Fullback, Anchor, Box-to-Box, Deep-Lying Playmaker, Ball Winner, Mezzala, Advanced Playmaker, Wide Playmaker, Traditional Winger, Inverted Winger, Progressive Carrier, Inside Forward, Target Man, Poacher, False 9, Shadow Striker, Complete Forward, Pressing Forward\nReply ONLY: POSITION|ROLE"

                    # DUAL-AI POSITION VALIDATION: Grok + Gemini in parallel for defenders
                    is_defender = player_position == "Defender"

                    pos_client = SyncOpenAI(api_key=XAI_API_KEY, base_url="https://api.x.ai/v1")

                    async def resolve_pos_grok():
                        return await aio.wait_for(
                            aio.to_thread(
                                pos_client.chat.completions.create,
                                model="grok-4-1-fast-non-reasoning",
                                messages=[
                                    {"role": "system", "content": "You are a football/soccer tactical analyst. Reply in EXACTLY this format on one line:\nPOSITION|ROLE\nNothing else."},
                                    {"role": "user", "content": pos_prompt},
                                ],
                                temperature=0,
                            ),
                            timeout=8
                        )

                    async def resolve_pos_gemini():
                        EMERGENT_PROXY = "https://integrations.emergentagent.com/llm"
                        gemini_client = OpenAI(api_key=EMERGENT_LLM_KEY, base_url=EMERGENT_PROXY + "/v1")
                        loop = aio.get_event_loop()
                        def _run_gemini():
                            return gemini_client.chat.completions.create(
                                model="gemini/gemini-2.0-flash",
                                messages=[
                                    {"role": "system", "content": "You are a football/soccer tactical analyst. Reply in EXACTLY this format on one line:\nPOSITION|ROLE\nNothing else."},
                                    {"role": "user", "content": pos_prompt},
                                ],
                                temperature=0,
                                max_tokens=50,
                            )
                        return await aio.wait_for(loop.run_in_executor(None, _run_gemini), timeout=8)

                    def parse_pos_response(resp_text, allowed):
                        parts = resp_text.strip().split("|")
                        pos = parts[0].strip().upper().replace(".", "").replace(",", "") if parts else ""
                        role = parts[1].strip() if len(parts) > 1 else ""
                        if pos in (allowed or {"GK","CB","LB","RB","LWB","RWB","CDM","CM","CAM","LM","RM","LW","RW","CF","ST","SS"}):
                            return pos, role
                        return None, None

                    valid_positions = allowed_positions or {"GK","CB","LB","RB","LWB","RWB","CDM","CM","CAM","LM","RM","LW","RW","CF","ST","SS"}

                    if is_defender:
                        # Dual-AI for defenders (most common source of errors)
                        try:
                            grok_resp, gemini_resp = await aio.gather(
                                resolve_pos_grok(), resolve_pos_gemini(),
                                return_exceptions=True
                            )
                            grok_pos, grok_role = None, None
                            gemini_pos, gemini_role = None, None
                            if not isinstance(grok_resp, Exception):
                                grok_pos, grok_role = parse_pos_response(grok_resp.choices[0].message.content, valid_positions)
                            if not isinstance(gemini_resp, Exception):
                                gemini_pos, gemini_role = parse_pos_response(gemini_resp.choices[0].message.content, valid_positions)

                            if grok_pos and gemini_pos:
                                if grok_pos == gemini_pos:
                                    pos_code = grok_pos
                                    role_text = grok_role or gemini_role or ""
                                    print(f"[POS RESOLVE] Dual-AI AGREE: {req.playerName} → {pos_code} (Grok={grok_pos}, Gemini={gemini_pos})")
                                else:
                                    # Disagreement — use stats heuristic as tiebreaker
                                    pos_code = grok_pos  # default to Grok
                                    role_text = grok_role or ""
                                    if stats_list:
                                        latest_s = stats_list[-1]
                                        key_passes = latest_s.get("passes", {}).get("key", 0) or 0
                                        dribble_att = latest_s.get("dribbles", {}).get("attempts", 0) or 0
                                        tackles_total = latest_s.get("tackles", {}).get("total", 0) or 0
                                        blocks = latest_s.get("tackles", {}).get("blocks", 0) or 0
                                        # CB indicators: high tackles+blocks, low key passes & dribbles
                                        cb_score = (tackles_total + blocks * 2) - (key_passes + dribble_att)
                                        if cb_score > 10 and "CB" in {grok_pos, gemini_pos}:
                                            pos_code = "CB"
                                            role_text = grok_role if grok_pos == "CB" else gemini_role or "Ball-Playing CB"
                                        elif cb_score < -5 and ("LB" in {grok_pos, gemini_pos} or "RB" in {grok_pos, gemini_pos}):
                                            pos_code = grok_pos if grok_pos in ("LB", "RB") else gemini_pos
                                            role_text = grok_role if grok_pos == pos_code else gemini_role or "Fullback"
                                    print(f"[POS RESOLVE] Dual-AI DISAGREE: Grok={grok_pos}, Gemini={gemini_pos} → tiebreak={pos_code} (stats-based)")
                            elif grok_pos:
                                pos_code = grok_pos
                                role_text = grok_role or ""
                                print(f"[POS RESOLVE] Grok only: {req.playerName} → {pos_code}")
                            elif gemini_pos:
                                pos_code = gemini_pos
                                role_text = gemini_role or ""
                                print(f"[POS RESOLVE] Gemini only: {req.playerName} → {pos_code}")
                            else:
                                raise ValueError("Both AIs failed for position")
                        except Exception as e:
                            # Fallback to single Grok call
                            print(f"[POS RESOLVE] Dual-AI failed ({e}), trying single Grok...")
                            pos_resp = await resolve_pos_grok()
                            pos_code, role_text = parse_pos_response(pos_resp.choices[0].message.content, valid_positions)
                            if not pos_code:
                                raise ValueError("Grok returned invalid position")
                    else:
                        # Non-defenders: single Grok call (with stats context)
                        pos_resp = await resolve_pos_grok()
                        pos_code, role_text = parse_pos_response(pos_resp.choices[0].message.content, valid_positions)
                        if not pos_code:
                            raise ValueError("Grok returned invalid position")

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

        # =============================================
        # MULTI-AI CONSENSUS ENGINE (3 AIs)
        # Grok 3 Mini (GK) — single AI engine
        # =============================================
        PREDICTION_SYSTEM = """Elite soccer prop prediction engine. Analyze data thoroughly, return calibrated JSON.

REQUIREMENTS:
- "reasoning": 3-5 sentences citing specific per-game averages, venue splits, and — CRITICALLY — what the opponent allows to THIS POSITION (use [POSITION COMPARISON] data). If it's a knockout/2nd leg, explain how the aggregate situation changes expected game flow.
- "tacticalBreakdown": ~1500 char markdown with these MANDATORY sections:
  **Verdict** (1 sentence with recommendation and projected value)
  **Matchup** (How this specific opponent has allowed this stat to same-position players — cite the average from [POSITION COMPARISON]. Note venue context.)
  **Analysis** (Player's recent form with real numbers. Home/away split. Possession context and how it affects this prop.)
  **Situation** (Knockout leg? Aggregate score? Tournament stakes? How does this change expected tactics and tempo?)
  **Scenarios** (Best/base/worst case with specific stat ranges)
  **Risk** (Rotation risk, sub timing, tactical shifts, injury concerns)
  **TL;DR** (1-2 sentence sharp summary of the bet)
- "scenarioAnalysis": 2-3 sentences with specific projections per scenario
- "sharpSummary": 2 sentences explaining why projection differs from line, referencing opponent positional allowance if available
- "keyEvidence": cite the 2-3 most important data points as a string — must include the opponent's positional allowed average if [POSITION COMPARISON] data is present
- "gameFlowDynamics": How game state, aggregate score, and expected possession impact this specific stat (1-2 sentences)
- "sensitivityTests", "subRisk", "uncertaintyNote": 1 sentence each

CRITICAL RULES:
- NEVER double-count minutes. If data shows a player averaging 43 passes in 26 minutes per game, the 43 IS their actual game output. Do NOT scale down by minutes. The average already reflects their real playing time.
- Match context OVERRIDES raw averages: If [MATCH DOMINANCE ANALYSIS] shows expected possession significantly above the team's season average, RAISE projections for pass-dependent props (pass_attempts, key_passes) accordingly. Historical averages are baselines, not ceilings.
- For pass/creative props: weight POSSESSION EXPECTATION heavily. A deep-lying playmaker on a team expected at 65%+ possession WILL exceed their season average.

TERMINOLOGY RULES:
- NEVER use the word "Bayesian" anywhere in your response. Always say "Reverse Formula" instead.
- Example: "Reverse Formula projects..." or "The Reverse Formula math shows..." NOT "Bayesian analysis suggests..."

CALIBRATION RULES (MUST FOLLOW):
- UNDER SKEW: Stats have positive skew — a player can have a monster game but can't go below 0. UNDER bets are inherently riskier. If recommending UNDER, your confidence should be 3-5% LOWER than you'd give for an equivalent OVER edge.
- BINARY LINES: If the line is 0.5 (e.g., UNDER 0.5 means ZERO of that stat), be EXTREMELY cautious recommending UNDER. One event loses the bet. UNDER 0.5 confidence should NEVER exceed 55% unless data overwhelmingly supports it.
- TIGHT EDGE: If your projected value is within ±1.0 of the line, this is a MARGINAL edge. Confidence should NOT exceed 60% regardless of how much data you have.
- DEFENDER PASSES: Ball-playing center-backs (CB, LB, RB) in possession-dominant teams (55%+ possession) routinely hit 60-85+ passes per game. Do NOT assume defenders have low pass counts — check their actual per-game averages carefully.

JSON: {"projectedValue":0,"recommendation":"over|under","confidenceScore":0,"confidenceLevel":"","sharpSummary":"","reasoning":"","scenarioAnalysis":"","keyEvidence":"","sensitivityTests":"","subRisk":"","gameFlowDynamics":"","uncertaintyNote":"","tacticalBreakdown":"","matchupOverview":{"homeTeam":"","awayTeam":"","favorite":"","moneyline":{"home":"","draw":"","away":""},"expectedPossession":{"home":0,"away":0},"expectedGameType":"","keyMatchupFactor":""},"bayesianMetrics":{"priorMean":0,"momentumEffect":0,"covariateAdjustment":0,"reversalFlag":"stable"},"probabilityCurve":[],"recentSamples":[],"player":{"id":0,"name":"","team":"","position":""},"opponent":"","propType":"","line":0,"confidenceInterval":[0,0],"tacticalAlerts":[]}"""

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

            # 2. GK save rate from LAST 5-7 game logs only (recent form)
            gk_saves_list = []
            gk_ga_from_logs = []
            recent_gk_logs = [g for g in player_game_logs if g.get("goals_saves") is not None and g.get("minutes", 0) > 0][:7]
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
                    context_multiplier += 0.10
                    context_factors.append("Team underdog → +10% (more opponent shots)")
            if player_venue == "away":
                context_multiplier += 0.05
                context_factors.append("Away GK → +5% (typically face more pressure)")
            context_multiplier = round(context_multiplier, 2)

            # 4. THE FORMULA: Projected Saves = Opp Avg SoT × GK Save% × Context
            # Weighted blend: 60% formula (match-specific) + 40% GK average (form)
            raw_formula = round(opp_avg_sot * (gk_save_pct / 100) * context_multiplier, 1) if opp_avg_sot > 0 else gk_avg_saves
            if gk_avg_saves > 0 and raw_formula > 0:
                projected_saves = round(raw_formula * 0.6 + gk_avg_saves * 0.4, 1)
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
                "formula": f"{opp_avg_sot} SoT × {gk_save_pct}% save rate × {context_multiplier} context → {raw_formula} formula, blended with {gk_avg_saves} avg = {projected_saves}",
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

        # Compose data for Grok prediction
        final_data_parts = []
        if grok_digest:
            final_data_parts.append(f"[GROK INTEL BRIEF]\n{grok_digest}")
        if data_digest:
            final_data_parts.append(f"[DATA DIGEST]\n{data_digest}")
        if wave2_supplement:
            final_data_parts.append(f"[GAME LOGS]\n{json.dumps(wave2_supplement, default=str)[:5000]}")

        if final_data_parts:
            final_data = "\n\n".join(final_data_parts)[:10000]
            if saves_context:
                final_data += f"\n\n{saves_context}"
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

        prompt = f"""{req.playerName} ({display_position}) — plays for {corrected_team_name} ({player_venue.upper()}) | OPPONENT: {req.opponentName} | {req.propType} line {req.line}
IMPORTANT: This player's current CLUB is {corrected_team_name}. Do NOT reference any national team or previous club in your analysis — use only "{corrected_team_name}" when referring to this player's team.
Odds: {json.dumps(match_odds.get('bookmakerOdds',{}), default=str) if match_odds else 'N/A'}{match_context}
{pronoun_note}
recentSamples=[]
{hit_rate_context}
{bayesian_prompt_anchor}
{dom_context}
{position_context}
{final_data[:3500]}

Analyze ALL data thoroughly. Return JSON only."""

        EMERGENT_PROXY = "https://integrations.emergentagent.com/llm"

        async def call_emergent_direct(model_name, label):
            """Call Claude/other models directly via OpenAI SDK through Emergent proxy."""
            try:
                client = OpenAI(api_key=EMERGENT_LLM_KEY, base_url=EMERGENT_PROXY + "/v1")
                loop = aio.get_event_loop()
                def _run():
                    return client.chat.completions.create(
                        model=model_name,
                        messages=[
                            {"role": "system", "content": PREDICTION_SYSTEM},
                            {"role": "user", "content": prompt},
                        ],
                        max_tokens=2500,
                        temperature=0.0,
                    )
                resp = await aio.wait_for(loop.run_in_executor(None, _run), timeout=40)
                text = resp.choices[0].message.content.strip()
                if text.startswith("```"):
                    text = "\n".join(ln for ln in text.split("\n") if not ln.strip().startswith("```"))
                start = text.find("{")
                if start >= 0:
                    for end_pos in range(len(text), start, -1):
                        if text[end_pos - 1] == "}":
                            try:
                                result = json.loads(text[start:end_pos])
                                result["_source"] = label
                                return result
                            except json.JSONDecodeError:
                                continue
                raise ValueError("No valid JSON in response")
            except Exception as e:
                print(f"[MULTI-AI] {label} failed: {e}")
                return None

        async def call_grok(label="grok", model="grok-4-1-fast-non-reasoning"):
            try:
                grok_client = OpenAI(api_key=XAI_API_KEY, base_url="https://api.x.ai/v1")
                grok_messages = [
                    {"role": "system", "content": PREDICTION_SYSTEM},
                    {"role": "user", "content": prompt},
                ]
                loop = aio.get_event_loop()
                def _run():
                    return grok_client.chat.completions.create(
                        model=model,
                        messages=grok_messages,
                        max_tokens=1600,
                        temperature=0.0,
                    )
                grok_result = await aio.wait_for(loop.run_in_executor(None, _run), timeout=30)
                text = grok_result.choices[0].message.content.strip()
                if text.startswith("```"):
                    text = "\n".join(ln for ln in text.split("\n") if not ln.strip().startswith("```"))
                # Robust JSON extraction
                start = text.find("{")
                if start >= 0:
                    for end_pos in range(len(text), start, -1):
                        if text[end_pos - 1] == "}":
                            try:
                                result = json.loads(text[start:end_pos])
                                result["_source"] = label
                                return result
                            except json.JSONDecodeError:
                                continue
                raise ValueError("No valid JSON found in Grok response")
            except Exception as e:
                print(f"[MULTI-AI] {label} failed: {e}")
                return None

        # =============================================
        # GROK SYNTHESIS: grok-4-1-fast (primary — reliable, fast)
        # Falls back to Bayesian-only if model fails
        # =============================================
        grok_result = None
        try:
            grok_result = await aio.wait_for(
                call_grok(label="grok41fast", model="grok-4-1-fast-non-reasoning"),
                timeout=35
            )
        except Exception as e:
            print(f"[HYBRID] grok-4-1-fast exception: {e}")

        pv = grok_result.get("projectedValue", 0) if grok_result and isinstance(grok_result, dict) else 0
        if not isinstance(pv, (int, float)) or pv <= 0:
            # Invalid projection — one retry
            print(f"[HYBRID] Invalid projection {pv}, retrying")
            try:
                grok_result = await aio.wait_for(
                    call_grok(label="grok41fast_retry", model="grok-4-1-fast-non-reasoning"),
                    timeout=30
                )
                pv = grok_result.get("projectedValue", 0) if grok_result and isinstance(grok_result, dict) else 0
            except Exception as e:
                print(f"[HYBRID] retry exception: {e}")
                pv = 0

        # BAYESIAN FALLBACK: If ALL Grok models failed, use Bayesian projection directly
        if not grok_result or not isinstance(grok_result, dict) or not isinstance(pv, (int, float)) or pv <= 0:
            if early_bayes and early_bayes.get("posteriorMean"):
                pv = early_bayes["posteriorMean"]
                grok_result = {
                    "projectedValue": pv,
                    "recommendation": early_bayes.get("recommendation", "over"),
                    "confidenceScore": max(early_bayes.get("pOver", 50), early_bayes.get("pUnder", 50)),
                    "reasoning": "AI models unavailable — projection based on Reverse Formula mathematical analysis.",
                    "_source": "bayesian_fallback",
                }
                print(f"[BAYESIAN FALLBACK] All Grok models failed — using Bayesian projection: {pv}")
            else:
                # No Bayesian data either — use the line as last resort
                pv = req.line
                grok_result = {
                    "projectedValue": pv,
                    "recommendation": "over",
                    "confidenceScore": 50,
                    "reasoning": "Insufficient data for mathematical projection. AI models unavailable.",
                    "_source": "fallback",
                }
                print(f"[FALLBACK] No Bayesian data and all Grok models failed — using line: {pv}")

        source_model = grok_result.get("_source", "grok3mini")
        print(f"[TIMING] {source_model} done: {_t.time()-_t0:.1f}s, proj={pv}")

        prediction = grok_result.copy()
        prediction.pop("_source", None)
        prediction["projectedValue"] = pv
        prediction["recommendation"] = "over" if pv > req.line else "under"

        # Confidence normalization
        cs = prediction.get("confidenceScore", 50)
        if isinstance(cs, (int, float)):
            prediction["confidenceScore"] = round(cs * 100 if cs <= 1 else cs)
        else:
            prediction["confidenceScore"] = 50

        prediction["consensusNote"] = f"Reverse Formula projection. Grok provides tactical analysis only."
        prediction["modelBreakdown"] = [{
            "model": source_model,
            "recommendation": prediction["recommendation"],
            "projectedValue": pv,
            "confidenceScore": prediction["confidenceScore"],
        }]

        # Set confidence level
        cs = prediction.get("confidenceScore", 50)
        prediction["confidenceLevel"] = "Very High" if cs >= 75 else "High" if cs >= 65 else "Medium" if cs >= 50 else "Low"

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

        # =============================================
        # =============================================
        # BAYESIAN-ONLY PROJECTION
        #
        # The math OWNS the number. Period.
        # Grok provides tactical reasoning text only — no numeric influence.
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
                    _opp_weight = min(_opp_allowed_n * 0.025, 0.15)
                    _old_bp = bayesian_posterior
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

            bayesian_prob = max(real_bayes.get("pOver", 50), real_bayes.get("pUnder", 50)) / 100
            bayesian_rec = real_bayes.get("recommendation", "over")
            ai_proj = prediction.get("projectedValue", req.line)
            ai_rec = prediction.get("recommendation", "over")

            divergence_pct = abs(ai_proj - bayesian_posterior) / max(bayesian_posterior, 1) * 100

            # Log when AI disagrees (for transparency in the UI)
            if divergence_pct > 10 and bayesian_rec != ai_rec:
                prediction["tacticalAlerts"] = prediction.get("tacticalAlerts", []) + [
                    f"AI reasoning suggested {ai_proj:.1f} ({ai_rec.upper()}) but math projects {bayesian_posterior:.1f} ({bayesian_rec.upper()}). Math prevails."
                ]
                print(f"[MATH OVERRIDE] AI={ai_proj}({ai_rec}) vs Bayes={bayesian_posterior}({bayesian_rec}) — {divergence_pct:.0f}% gap. Using math.")

            print(f"[PROJECTION] Bayesian={bayesian_posterior}({bayesian_rec}, {bayesian_prob:.0%}) | AI opinion={ai_proj}({ai_rec}) — MATH IS FINAL")

            prediction["projectedValue"] = bayesian_posterior
            prediction["recommendation"] = bayesian_rec
            prediction["fusionApplied"] = {
                "aiProjection": ai_proj,
                "aiRecommendation": ai_rec,
                "bayesianPosterior": bayesian_posterior,
                "bayesianRecommendation": bayesian_rec,
                "bayesianConfidence": round(bayesian_prob * 100, 1),
                "fusedProjection": bayesian_posterior,
                "fusedRecommendation": bayesian_rec,
                "weights": {"ai": 0, "bayesian": 1.0},
                "agreement": bayesian_rec == ai_rec,
                "divergencePct": round(divergence_pct, 1),
            }

        # =============================================
        # POST-PROJECTION DOMINANCE SCALING — SELECTIVE
        # Applied ONLY when a low-possession team faces a possession monster.
        # High-possession teams (>52% avg) keep their Bayesian projection as-is
        # because their pass counts remain high regardless of matchup.
        # =============================================
        poss_sensitive = {"pass_attempts", "passes", "key_passes", "crosses", "dribbles"}

        if req.propType in poss_sensitive and match_dominance.get("multiplier", 1.0) != 1.0:
            dom_mult = match_dominance["multiplier"]
            team_avg_poss = match_dominance.get("teamSeasonAvg", 50)
            current = prediction.get("projectedValue", req.line)

            if team_avg_poss < 52 and dom_mult < 0.92:
                # Low-possession team facing a dominant opponent — scale down
                post_dom = round(current * dom_mult, 1)
                prediction["projectedValue"] = post_dom
                prediction["recommendation"] = "over" if post_dom > req.line else "under"
                print(f"[DOMINANCE] APPLIED: {current} × {dom_mult:.3f} → {post_dom} (team avg {team_avg_poss:.0f}% < 52% threshold)")
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

        # =============================================
        # POST-CONSENSUS CONFIDENCE GUARDS
        # =============================================
        conf = prediction.get("confidenceScore", 50)
        proj_val = prediction.get("projectedValue", req.line)
        edge = abs(proj_val - req.line)
        rec = prediction.get("recommendation", "over")

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

        # Guard 3: Coin-flip zone — projection within ±3 of line AND Bayesian confidence < 60%
        if edge < 3.0 and real_bayes:
            bayes_conf = max(real_bayes.get("pOver", 50), real_bayes.get("pUnder", 50))
            if bayes_conf < 60:
                old_conf = prediction.get("confidenceScore", 50)
                prediction["confidenceScore"] = min(old_conf, 52)
                prediction["coinFlip"] = True
                prediction["tacticalAlerts"] = prediction.get("tacticalAlerts", []) + [
                    f"COIN FLIP: Math projects {real_bayes.get('posteriorMean')} vs line {req.line} (edge {edge:.1f}). Bayesian P={bayes_conf}%. This is a variance-driven outcome."
                ]
                print(f"[GUARD] Coin-flip zone: edge={edge:.1f}, Bayesian P={bayes_conf}% → capped at 52% (was {old_conf})")

        # Guard 3: UNDER skew penalty — stats have positive skew
        if rec == "under":
            adj_conf = prediction.get("confidenceScore", 50)
            penalty = min(4, max(2, round(edge * 0.5)))  # 2-4% penalty based on edge size
            prediction["confidenceScore"] = max(45, adj_conf - penalty)
            if adj_conf != prediction["confidenceScore"]:
                print(f"[GUARD] UNDER skew penalty: -{penalty}% confidence ({adj_conf} → {prediction['confidenceScore']})")

        # Recalculate confidence level after guards
        cs = prediction.get("confidenceScore", 50)
        prediction["confidenceLevel"] = "Very High" if cs >= 75 else "High" if cs >= 65 else "Medium" if cs >= 50 else "Low"

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
                prediction["projectedValue"] = round(pv)
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
        if team_fixture_stats and opponent_fixture_stats:
            def avg_stat(stats_list, key):
                vals = [s.get(key) for s in stats_list if s.get(key) is not None]
                return sum(vals) / len(vals) if vals else 0
            team_avg_shots = avg_stat(team_fixture_stats, "totalShots")
            opp_avg_shots = avg_stat(opponent_fixture_stats, "totalShots")
            combined_shots = team_avg_shots + opp_avg_shots
            poss_diff = abs((real_matchup.get("expectedPossession", {}).get("home", 50)) - 50)
            if combined_shots >= 28:
                real_matchup["expectedGameType"] = "open"
            elif combined_shots <= 18:
                real_matchup["expectedGameType"] = "cagey"
            elif poss_diff >= 12:
                real_matchup["expectedGameType"] = "one-sided"
            else:
                real_matchup["expectedGameType"] = "high-tempo" if combined_shots >= 23 else "cagey"
        # 4. Always set team names from request data (deterministic)
        real_matchup["homeTeam"] = player_team_display if player_venue == "home" else req.opponentName
        real_matchup["awayTeam"] = req.opponentName if player_venue == "home" else player_team_display

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

        # DATA QUALITY INDICATOR — flag when API data might be unreliable
        total_game_logs = len(player_game_logs)
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
        if total_game_logs > 0 and games_with_none / total_game_logs >= 0.3:
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
            "pass_attempts": "totalPasses",
            "shots": "totalShots",
            "shots_on_target": "shotsOnTarget",
            "saves": "shotsOnTarget",
            "key_passes": "totalPasses",
            "tackles": "totalShots",
            "interceptions": "totalShots",
            "blocks": "totalShots",
            "fouls_drawn": "fouls",
            "crosses": "totalPasses",
            "clearances": "totalShots",
            "dribbles": "totalPasses",
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

        # SYNTHESIS STEP: Combine all AI analyses into one rich tactical breakdown
        # This recreates the original Grok+Gemini depth — one AI synthesizes all others' insights
        rec = prediction.get('recommendation', 'over').upper()
        line = prediction.get('line', req.line)
        proj = prediction.get('projectedValue', '?')
        conf = prediction.get('confidenceScore', '?')
        pl = {
            "pass_attempts": "Pass Attempts",
            "shots": "Shots",
            "shots_on_target": "Shots on Target",
            "tackles": "Tackles",
            "key_passes": "Key Passes",
            "saves": "Saves",
            "interceptions": "Interceptions",
            "blocks": "Blocks",
            "dribbles": "Dribbles",
            "fouls_drawn": "Fouls Drawn",
        }.get(req.propType, req.propType)
        consensus_note = prediction.get('consensusNote', '')

        # Gather text from Grok response for synthesis
        all_texts = []
        bits = []
        for field in ["tacticalBreakdown", "reasoning", "scenarioAnalysis", "keyEvidence", "sharpSummary", "gameFlowDynamics", "sensitivityTests", "subRisk", "uncertaintyNote"]:
            val = prediction.get(field, "")
            if isinstance(val, dict):
                val = json.dumps(val)
            if val and len(str(val)) > 10:
                bits.append(f"{field}: {val}")
        if bits:
            all_texts.append("[grok]\n" + "\n".join(bits))

        synthesis_input = "\n\n".join(all_texts)

        # Fast Gemini synthesis — combine insights into one cohesive breakdown
        # Build dynamic context from all available data
        pos_context_for_synth = ""
        if display_position:
            pos_context_for_synth = f"\nPosition: {display_position}"
            if display_role:
                pos_context_for_synth += f" ({display_role})"
        comp_context = ""
        if position_comp_data:
            pc = position_comp_data
            comp_context = f"\nPosition Comparison ({player_venue.upper()} only): {pc['sampleSize']} {pc.get('positionShort', '')}s vs {pc['opponent']} averaged {pc['avgStatValue']} {pl.lower()} (per-90: {pc['avgPer90']})"
            if pc.get('avgPossession'):
                comp_context += f" | Avg team possession: {pc['avgPossession']}%"

        try:
            synth_prompt = f"""You are synthesizing multiple AI analyses into ONE elite tactical breakdown for a {pl} prop prediction.

FINAL VERDICT: {rec} {line} {pl} (Projected: {proj}, Confidence: {conf}%, {consensus_note})
Player: {req.playerName} vs {req.opponentName} ({player_venue.upper()}){pos_context_for_synth}{comp_context}

Here are the individual AI analyses to synthesize:

{synthesis_input[:4000]}

Write a single cohesive ~1500 char markdown tactical breakdown. Format:
**Verdict: {rec} {line} {pl}**
[1-2 sentence sharp summary with projection vs line]

**Position & Role Context**
[1-2 sentences about how the player's specific position ({display_position}) and role ({display_role or 'N/A'}) affects their {pl.lower()} output. How does this role generate or limit this stat? Reference the positional comparison data if available. Mention venue ({player_venue.upper()}) — are {player_venue} performances typically better or worse for this stat? If possession data is available, note how expected possession impacts this prop type.]

**Analysis**
[3-4 sentences combining the BEST insights from ALL analyses. Cite specific numbers: per-game averages, venue splits ({player_venue.upper()} context), sample sizes, opponent tendencies. Reference the positional comparison baseline when relevant. Factor in possession context — how does team possession % correlate with {pl.lower()} output for a {display_position}? Merge complementary insights, resolve contradictions]

**Game Script Scenarios**
[Best case / Worst case / Most likely — with stat projections for each]

**Key Evidence**
[3-4 bullet points — strongest data points from across all analyses, including position comparison baseline and venue-specific trends]

**Risk Radar**
[Sub risk, sensitivity factors, what would flip the pick]

**TL;DR** — {rec} {line} at {conf}% confidence. Projected: {proj} {pl.lower()}. {consensus_note}

Rules: No AI model names. Be specific with numbers. Be decisive. ALWAYS reference the player's position/role and how it impacts the prop. ALWAYS reference venue (home/away) context."""

            synth_client = OpenAI(api_key=EMERGENT_LLM_KEY, base_url=EMERGENT_PROXY + "/v1")
            loop = aio.get_event_loop()
            def _run_synth():
                return synth_client.chat.completions.create(
                    model="gemini/gemini-2.0-flash",
                    messages=[{"role": "user", "content": synth_prompt}],
                    max_tokens=1500,
                    temperature=0.2,
                )
            synth_resp = await aio.wait_for(loop.run_in_executor(None, _run_synth), timeout=10)
            synth_text = synth_resp.choices[0].message.content.strip()
            if synth_text and len(synth_text) > 200:
                prediction["tacticalBreakdown"] = synth_text
                print(f"[TIMING] Synthesis done: {_t.time()-_t0:.1f}s total, {len(synth_text)} chars")
            else:
                raise ValueError("Synthesis too short")
        except Exception as synth_err:
            print(f"[SYNTHESIS] Fallback — {synth_err}")
            # Fallback: Build from fields manually
            tb_parts = []
            if prediction.get('sharpSummary'):
                tb_parts.append(f"**Verdict: {rec} {line} {pl}**\n{prediction['sharpSummary']}")
            else:
                tb_parts.append(f"**Verdict: {rec} {line} {pl}**\nProjected {proj} {pl.lower()} — consensus favors {rec.lower()}.")

            if prediction.get('reasoning') and len(str(prediction['reasoning'])) > 30:
                tb_parts.append(f"**Analysis**\n{prediction['reasoning']}")

            if prediction.get('scenarioAnalysis'):
                tb_parts.append(f"**Game Script Scenarios**\n{prediction['scenarioAnalysis']}")
            if prediction.get('keyEvidence'):
                tb_parts.append(f"**Key Evidence**\n{prediction['keyEvidence']}")

            risk = []
            if prediction.get('sensitivityTests'):
                risk.append(f"- Sensitivity: {prediction['sensitivityTests']}")
            if prediction.get('subRisk'):
                risk.append(f"- Sub Risk: {prediction['subRisk']}")
            if prediction.get('uncertaintyNote'):
                risk.append(f"- Key Risk: {prediction['uncertaintyNote']}")
            if risk:
                tb_parts.append("**Risk Radar**\n" + "\n".join(risk))
            if prediction.get('gameFlowDynamics'):
                tb_parts.append(f"**Game Flow**\n{prediction['gameFlowDynamics']}")

            tb_parts.append(f"**TL;DR** — {rec} {line} at {conf}% confidence. Projected: {proj} {pl.lower()}. {consensus_note}")
            prediction["tacticalBreakdown"] = "\n\n".join(tb_parts)

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
        if gk_formula_data:
            prediction["gkFormula"] = gk_formula_data
        if position_comp_data:
            prediction["positionComparison"] = position_comp_data

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

