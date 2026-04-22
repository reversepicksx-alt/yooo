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
# game_script_intelligence removed — was distorting confidence scores for GK pass picks

router = APIRouter(prefix="/api", tags=["predict"])

# ── CALIBRATION TOGGLE ────────────────────────────────────────────────────────
# Set to True to re-enable nightly-learned bias offsets.
# OFF by default — only turn on when explicitly requested.
CALIBRATION_ENABLED = False
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

            try:
                # Fetch the team's last 20 finished fixtures across ALL competitions from API
                team_fixtures_raw = await api_football_request(
                    "fixtures", {"team": actual_team_id, "last": 20, "status": "FT"}
                )
                if not team_fixtures_raw:
                    print(f"[API-DIRECT] No fixtures found for teamId={actual_team_id}")
                    return collected

                print(f"[API-DIRECT] {req.playerName}: {len(team_fixtures_raw)} team fixtures from API")

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
                                return gl
                            # Fall through to live API fetch for saves

                        fix_data = await api_football_request("fixtures/players", {"fixture": fid})
                        if not fix_data:
                            return None

                        matched_stats = None
                        all_player_logs = {}
                        for team_data in fix_data:
                            for p in team_data.get("players", []):
                                pid = p.get("player", {}).get("id")
                                stats = p.get("statistics", [{}])[0] if p.get("statistics") else {}
                                mins = stats.get("games", {}).get("minutes") or 0
                                if pid:
                                    all_player_logs[pid] = _build_game_log(stats)
                                    if pid == player_id and mins > 0:
                                        matched_stats = stats

                        # Cache all players from this fixture (fire-and-forget, for position comparisons)
                        async def _cache_fix(fid_c, logs_c):
                            ops = [
                                db.fixture_player_cache.update_one(
                                    {"_k": f"fxp_{fid_c}_{pk}"},
                                    {"$set": {"_k": f"fxp_{fid_c}_{pk}", "d": lv}},
                                    upsert=True
                                ) for pk, lv in logs_c.items()
                            ]
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
                        return gl
                    except Exception:
                        return None

                sem = aio.Semaphore(5)
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
        # PLAYER-DIRECT API FALLBACK: When fixture cache misses, fetch the player's
        # recent fixtures directly from the API by player ID — no team cache needed.
        # =============================================
        if not player_game_logs and req.playerId:
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

            # Stage 1: Pull the player's last 20 fixtures directly from API by player ID
            try:
                print(f"[PLAYER-DIRECT] {req.playerName}: fetching fixtures directly by playerId={req.playerId}")
                _player_fixtures_raw = await api_football_request(
                    "fixtures", {"player": req.playerId, "last": 20}
                )
                if _player_fixtures_raw:
                    # For each fixture, fetch per-game stats
                    _sem2 = aio.Semaphore(5)
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
                                # Cache all players from this fixture
                                async def _cache_all_inner(fid_inner, logs_inner):
                                    ops = [
                                        db.fixture_player_cache.update_one(
                                            {"_k": f"fxp_{fid_inner}_{pid_k}"},
                                            {"$set": {"_k": f"fxp_{fid_inner}_{pid_k}", "d": gl_v}},
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
        # Safety defaults for T003/T004 — always defined even if exception occurs
        _redist_alerts: list = []
        _redist_multiplier: float = 1.0
        _lineup_alert: str | None = None
        _lineup_status: str = "unknown"
        try:
            from bayesian_engine import compute_bayesian_projection

            # ── Quick position cache lookup (fast indexed read) ──────────────
            # We look up the cached position so the engine can apply the correct
            # momentum decay table (attackers decay faster, GKs decay slower).
            _bayes_position = ""
            try:
                _pos_doc = await db.player_positions.find_one({"playerName": req.playerName})
                if _pos_doc:
                    _bayes_position = _pos_doc.get("specificPosition", "")
            except Exception:
                pass

            # ── GK detection fallback (position cache miss) ─────────────────
            # If the position cache doesn't know this player yet, detect GK from
            # game logs (saves data present) or from the propType being "saves".
            # This prevents the outfield possession squeeze from firing on GKs.
            if not _bayes_position:
                if req.propType == "saves":
                    _bayes_position = "GK"
                elif req.propType in {"pass_attempts", "passes"}:
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
                if g.get("minutes", 0) > 0
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
                "pass_attempts": "passes_total", "shots": "shots_total",
                "shots_on_target": "shots_on", "tackles": "tackles_total",
                "key_passes": "passes_key", "saves": "goals_saves",
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
            _VENUE_SPLIT_PROPS = {"pass_attempts", "passes", "saves"}
            _bayes_logs = player_game_logs
            if req.propType in _VENUE_SPLIT_PROPS and player_venue:
                _venue_logs = [g for g in player_game_logs if g.get("venue") == player_venue]
                if len(_venue_logs) >= 5:
                    _bayes_logs = _venue_logs
                    print(
                        f"[VENUE PRIOR] {req.playerName}/{req.propType}: "
                        f"using {len(_venue_logs)} {player_venue} logs "
                        f"(dropped {len(player_game_logs) - len(_venue_logs)} opposite-venue logs)"
                    )
                else:
                    print(
                        f"[VENUE PRIOR] {req.playerName}/{req.propType}: "
                        f"only {len(_venue_logs)} {player_venue} logs — keeping combined {len(player_game_logs)}"
                    )

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
            )
            print(f"[BAYESIAN] {req.playerName}/{req.propType}: samples={early_bayes.get('priorSamples') if early_bayes else 0}, logs={len(_bayes_logs)} (venue={player_venue})")

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
                # significantly move the final projection, we must tell Grok the
                # RIGHT direction now — not the pre-adjustment direction.
                # Without this, Grok writes "57.8 under" and the badge shows 66 OVER,
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

                _pf_rec  = "OVER" if _pf_proj > req.line else "UNDER"
                _pf_bprob = early_bayes['pOver'] if _pf_rec == 'OVER' else early_bayes['pUnder']
                bdir = _pf_rec  # Use preflight direction as the anchor direction
                bprob = _pf_bprob
                if _pf_proj != early_bayes["posteriorMean"]:
                    print(f"[ANCHOR PREFLIGHT] {req.playerName}: raw={early_bayes['posteriorMean']} → preflight={_pf_proj} ({_pf_rec}) after dominance adjustment")

                bayesian_prompt_anchor = f"""
[MATHEMATICAL ENGINE — DO NOT IGNORE]
3-Layer Reverse Formula analysis ({early_bayes['priorSamples']} games): projects {_pf_proj} {bdir} (P={bprob}%).
Season avg: {early_bayes['priorMean']} | Recent form (decay-weighted): {early_bayes['momentumMean']} ({early_bayes['momentumLabel']}) | Context adj: {early_bayes['covariateAdjustment']:+.1f}
Streak: {early_bayes['streakFlag']} | Volatility: {early_bayes['volatility']} (CV={early_bayes['cv']}) | Reversal: {early_bayes['reversalFlag']}
IMPORTANT: Never use the word "Bayesian" in your response. Always say "Reverse Formula" instead.
>>> CONTEXT: The Reverse Formula's preliminary estimate is {_pf_proj} (pointing {bdir} the line of {req.line}). This is based on season logs and momentum — it will be refined by H2H history and opponent position profile after you respond. Do NOT state a final projection number or commit to "OVER" or "UNDER" — that final verdict belongs to the math engine. Your job is to provide the tactical analysis that EXPLAINS the data: the factors that push this stat higher, the factors that suppress it, what the matchup reveals, and what the market misses. Write a balanced analysis. For sharpSummary: describe the KEY TENSION in this matchup (e.g. "Aramburu's 29.7 home avg meets a Getafe defense that concedes high volume to wing-backs — the Reverse Formula weighs this tension"). Do NOT write "OVER" or "UNDER" conclusions in sharpSummary. <<<"""
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
POSITION CLUES: CB=high tackles/blocks/aerial duels, low crosses/key passes/dribbles. LB/RB=crosses, some key passes, overlapping runs. CDM=high interceptions, moderate passing. CM=balanced. CAM=high key passes. Winger=high dribbles/crosses. ST=high shots/goals."""

                    pos_prompt = f"What is {req.playerName}'s primary position and tactical role at {corrected_team_name}?{category_hint}{stats_evidence}\nPosition must be one of: {pos_list}\nRole must be one of: Shot-Stopper, Sweeper Keeper, Ball-Playing CB, Stopper, Fullback, Wing-Back, Inverted Fullback, Anchor, Box-to-Box, Deep-Lying Playmaker, Ball Winner, Mezzala, Advanced Playmaker, Wide Playmaker, Traditional Winger, Inverted Winger, Progressive Carrier, Inside Forward, Target Man, Poacher, False 9, Shadow Striker, Complete Forward, Pressing Forward\nReply ONLY: POSITION|ROLE"

                    # DUAL-AI POSITION VALIDATION: Always run Grok + Gemini in parallel.
                    # Previously only ran for defenders, but API-Football miscategorizes
                    # players across all positions (e.g., CM as "Attacker") so all players
                    # need cross-validation. Only skip dual-AI for GKs (unambiguous).
                    is_defender = player_position != "Goalkeeper"  # dual-AI for everyone except GK

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
        # Grok 3 Mini (GK) — single AI engine
        # =============================================
        PREDICTION_SYSTEM = """You are the sharpest soccer prop analyst on the planet. You think like a professional handicapper who has watched thousands of matches, understands exactly how each position influences each stat, and knows what the public systematically gets wrong. Your job is to produce an analysis that makes the reader feel like they've just been briefed by an insider — not handed a stats printout.

REQUIRED JSON FIELDS:

"reasoning": 4-6 sentences of sharp analyst thinking. Go beyond averages — explain the TACTICAL CHAIN that produces this stat in this matchup. Why does THIS opponent create THIS outcome for THIS position? What does the betting market not understand about this player's role in their team's system? Cite real numbers from the data but frame them through tactical insight, not data recitation.

"tacticalBreakdown": Rich markdown (~1800 chars) with these MANDATORY sections — each must read like expert analysis, not a stats summary:

  **Verdict** — One punchy sentence: the call, the projection, the edge. Make it sound decisive.

  **Matchup** — Don't just state what the opponent allows. Explain WHY they allow it. What is it about their defensive or pressing shape that creates vulnerability for THIS position? For GKs: does this opponent press high forcing back-passes, or do they sit deep letting the GK play out calmly? For attackers: do they leave space in behind, or do they defend deep giving strikers no service? For midfielders: do they press high creating turnovers and transition touches, or do they let the ball circulate freely? Cite the [POSITION COMPARISON] average AND explain the structural reason behind it.

  **Situation** — This is where most analysts fail. Read the MONEYLINE and possession context like a sharp. If this team is a heavy favourite, what does that do to game flow — do they set up to control possession, or do they press high and create an open game? If they're underdogs, are they likely to park the bus (low block = more GK back-passes) or press high (transition-heavy = more touches for attackers, more saves for GKs)? For knockout/2nd legs, explain the aggregate math and EXACTLY how it changes team shape and the prop. For regular-season close-odds games, explain what a balanced/contested game tempo means for this prop.

  **Analysis** — Player's recent output with specific numbers. But frame it: is this player operating in a system that inflates or suppresses their stats? Are they on a hot streak because of specific tactical advantages, or is momentum artificial? Home/away split matters — explain WHY the venue split exists for this player, not just that it exists.

  **Scenarios** — Three tactical scenarios with specific stat ranges and the TRIGGER that makes each one happen:
  Best case: [specific tactical trigger] → [stat range]
  Base case: [expected game flow] → [stat range]
  Worst case: [specific risk trigger] → [stat range]

  **Risk** — What specific event would kill this bet? Be precise: "if [team] goes down early and chases the game, expect more open play which [raises/lowers] this stat by X-Y". Mention sub timing, tactical shape changes, injury context.

  **TL;DR** — 1-2 sentences that sound like a sharp closing their case to a fellow bettor. No hedging. State the bet and why it's right. Example style: "Miami are going to spend 65 minutes camped in their own half — Clair is catching everything defenders panic-pass backward. Smash the over."

"sharpSummary": 2 sharp sentences that nail the core edge — WHY is the projection above/below the line and what does the market miss? Reference the opponent's positional allowance AND the tactical explanation for it. This is the first thing users read — make it land.

"scenarioAnalysis": 3 sentences covering best/base/worst tactical scenarios with specific projected values.

"keyEvidence": The 3 most important data points as a string — must include opponent positional allowance AND the tactical reason it exists.

"gameFlowDynamics": How expected possession and game state specifically change this prop's volume. Be tactical, not generic.

"sensitivityTests": One specific scenario that would flip the recommendation.
"subRisk": One specific substitution or rotation risk with timing.
"uncertaintyNote": One honest limitation of this projection.

POSITION-SPECIFIC REASONING FRAMEWORKS (apply the relevant one):

GOALKEEPER (pass_attempts/saves):
- pass_attempts: The INVERTED possession rule is everything. Low team possession = defenders constantly recycling under pressure to the GK = volume explosion. High team possession = GK barely involved in build-up = volume suppression. But READ THE OPPONENT — a team that presses relentlessly forces even dominant-possession GKs into rapid distribution. For saves: opponent SoT rate × GK save% × match tempo = your anchor. A high-block defensive team facing a prolific attacker on a high-tempo away game is the max-saves scenario.

STRIKER/FORWARD (shots, goals, assists):
- Think about SPACE, not just volume. A striker facing a high defensive line gets in behind for shots. A striker facing a deep block needs service from midfield — check if that midfield creates. Shots depend on penalty box entries, not just possession. An isolated striker in a low-block game can still pop off 4-5 shots if the team plays direct.

MIDFIELDER (passes, key_passes, assists):
- Ball-circulation midfielders: possession % is the primary driver. Every 5% more possession = roughly 8-12 more passes for the deepest midfielder. Key passes / assists: look at how many times the team reaches the final third AND how the striker presses — a high striker press creates more through-ball opportunities.

DEFENDER (passes, tackles, clearances):
- Ball-playing CBs in 55%+ possession teams easily hit 70-90 passes. The key variable is HOW the team builds — short from back (inflates defender passes) vs long-ball (suppresses). Tackles/clearances invert with possession: low possession = more defensive actions.

CRITICAL ACCURACY RULES:
- NEVER double-count minutes. A player averaging 43 passes in 26 minutes per game — the 43 IS their game output. Do NOT scale down.
- Match context OVERRIDES raw averages for pass-dependent props in high-possession scenarios.
- GOALKEEPER INVERTED RULE: Low possession = MORE GK passes. High possession = FEWER GK passes. An away GK holding a lead = maximum volume scenario.
- NEVER say "Bayesian" — always say "Reverse Formula".

CALIBRATION RULES:
- UNDER SKEW: Recommend UNDER only with 3-5% lower confidence than an equivalent OVER edge.
- TIGHT EDGE: If projected value is within ±1.0 of the line, cap confidence at 60%.
- BINARY LINES (0.5): UNDER 0.5 confidence NEVER exceeds 55%.
- DEFENDER PASSES: Ball-playing CBs/LBs in possession teams hit 60-90+ per game routinely.

JSON: {"confidenceScore":0,"confidenceLevel":"","sharpSummary":"","reasoning":"","scenarioAnalysis":"","keyEvidence":"","sensitivityTests":"","subRisk":"","gameFlowDynamics":"","uncertaintyNote":"","tacticalBreakdown":"","matchupOverview":{"homeTeam":"","awayTeam":"","favorite":"","moneyline":{"home":"","draw":"","away":""},"expectedPossession":{"home":0,"away":0},"expectedGameType":"","keyMatchupFactor":""},"bayesianMetrics":{"priorMean":0,"momentumEffect":0,"covariateAdjustment":0,"reversalFlag":"stable"},"probabilityCurve":[],"recentSamples":[],"player":{"id":0,"name":"","team":"","position":""},"opponent":"","propType":"","line":0,"confidenceInterval":[0,0],"tacticalAlerts":[]}"""

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
            gk_pass_context = f"""
[GK PASS VOLUME CONTEXT — INVERTED POSSESSION MODEL]
{req.playerName} is a GOALKEEPER. Pass volume rules are INVERTED vs outfield players.
Venue: {_gk_venue_lbl} | Expected possession: {_gk_exp_poss}% (team season avg: {_gk_team_avg}%, gap: {_gk_poss_gap:+.1f}pp)
Opponent expected possession: {_gk_opp_poss}%
Scenario: {_gk_scenario}
KEY PRINCIPLE: A GK defending deep = maximum back-pass recycling. A GK on a dominant team = barely touched. This is the single most important factor for GK pass props."""

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
            _saves_venue_logs = [g for g in player_game_logs if g.get("venue") == player_venue and g.get("goals_saves") is not None and g.get("minutes", 0) > 0]
            _saves_pool = _saves_venue_logs if len(_saves_venue_logs) >= 5 else player_game_logs
            recent_gk_logs = [g for g in _saves_pool if g.get("goals_saves") is not None and g.get("minutes", 0) > 0][:7]
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

        prompt = f"""{req.playerName} ({display_position}) — plays for {corrected_team_name} ({player_venue.upper()}) | OPPONENT: {req.opponentName} | {req.propType} line {req.line}
IMPORTANT: This player's current CLUB is {corrected_team_name}. Do NOT reference any national team or previous club in your analysis — use only "{corrected_team_name}" when referring to this player's team.{_disambig_note}
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

        from config import GEMINI_API_KEY as _GEMINI_KEY
        _GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
        _GEMINI_MODEL = "gemini-2.5-pro"

        async def call_gemini_direct(label="gemini25pro") -> dict | None:
            """Gemini 2.5 Pro with JSON mode — clean JSON guaranteed, no markdown fences."""
            if not _GEMINI_KEY:
                return None
            try:
                import httpx as _httpx
                url = f"{_GEMINI_BASE}/{_GEMINI_MODEL}:generateContent?key={_GEMINI_KEY}"
                payload = {
                    "systemInstruction": {"parts": [{"text": PREDICTION_SYSTEM}]},
                    "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "temperature": 0.0,
                        "maxOutputTokens": 8192,
                        "responseMimeType": "application/json",
                    },
                }
                async with _httpx.AsyncClient(timeout=_httpx.Timeout(70, connect=10)) as client:
                    resp = await client.post(url, json=payload)
                    if resp.status_code == 200:
                        data = resp.json()
                        candidates = data.get("candidates", [])
                        if candidates:
                            parts = candidates[0].get("content", {}).get("parts", [])
                            text = "".join(p.get("text", "") for p in parts).strip()
                            if text:
                                result = json.loads(text)
                                result["_source"] = label
                                print(f"[MULTI-AI] Gemini 2.5 Pro OK — summary: {str(result.get('sharpSummary',''))[:80]}")
                                return result
                    else:
                        print(f"[MULTI-AI] Gemini error {resp.status_code}: {resp.text[:200]}")
            except Exception as e:
                print(f"[MULTI-AI] {label} failed: {type(e).__name__}: {e}")
            return None

        async def call_grok(label="grok", model="grok-4-1-fast-non-reasoning"):
            """Grok fallback — used only when Gemini fails."""
            if not XAI_API_KEY:
                return None
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
                        max_tokens=2200,
                        temperature=0.0,
                    )
                grok_result = await aio.wait_for(loop.run_in_executor(None, _run), timeout=35)
                text = grok_result.choices[0].message.content.strip()
                import re as _re
                text = _re.sub(r"```(?:json)?\s*", "", text)
                text = _re.sub(r"```\s*$", "", text, flags=_re.MULTILINE)
                text = text.strip()
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
                print(f"[MULTI-AI] {label} non-JSON response: {text[:300]!r}")
                raise ValueError("No valid JSON found in Grok response")
            except Exception as e:
                print(f"[MULTI-AI] {label} failed: {e}")
                return None

        # =============================================
        # AI SYNTHESIS: Gemini 2.5 Pro (primary) → Grok fallback
        # Gemini JSON mode guarantees clean parseable output.
        # Projection comes ONLY from the math engine — AI projectedValue is NEVER used.
        # =============================================
        grok_result = None
        try:
            grok_result = await aio.wait_for(call_gemini_direct(label="gemini25pro"), timeout=75)
        except Exception as e:
            print(f"[HYBRID] Gemini primary exception: {e}")

        # pv is set from early_bayes here as a temporary anchor; real_bayes overwrites it later.
        pv = early_bayes["posteriorMean"] if early_bayes and early_bayes.get("posteriorMean") else req.line

        # If Gemini failed, fall back to Grok
        if not grok_result or not isinstance(grok_result, dict) or not grok_result.get("tacticalBreakdown"):
            print(f"[HYBRID] Gemini returned no text — falling back to Grok")
            try:
                grok_result = await aio.wait_for(
                    call_grok(label="grok_fallback", model="grok-4-1-fast-non-reasoning"),
                    timeout=35
                )
            except Exception as e:
                print(f"[HYBRID] Grok fallback exception: {e}")

        # BAYESIAN FALLBACK: If ALL Grok models failed (no text), build minimal result from math
        if not grok_result or not isinstance(grok_result, dict) or not grok_result.get("tacticalBreakdown"):
            if early_bayes and early_bayes.get("posteriorMean"):
                pv = early_bayes["posteriorMean"]
                # Cap confidence at 72% (shows "High") when Grok fails — the math had
                # no AI sanity check so claiming "Very High" confidence would be misleading.
                _raw_bayes_conf = max(early_bayes.get("pOver", 50), early_bayes.get("pUnder", 50))
                _capped_conf = min(_raw_bayes_conf, 72)
                grok_result = {
                    "projectedValue": pv,
                    "recommendation": early_bayes.get("recommendation", "over"),
                    "confidenceScore": _capped_conf,
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

            bayesian_prob = max(real_bayes.get("pOver", 50), real_bayes.get("pUnder", 50)) / 100
            bayesian_rec = real_bayes.get("recommendation", "over")
            # early_proj = early_bayes estimate before full multi-factor Bayesian run
            early_proj = prediction.get("projectedValue", req.line)
            early_rec  = prediction.get("recommendation", "over")

            divergence_pct = abs(early_proj - bayesian_posterior) / max(bayesian_posterior, 1) * 100

            # Log when early estimate and full Bayesian differ noticeably (adjustment audit trail)
            if divergence_pct > 10 and bayesian_rec != early_rec:
                print(f"[BAYES ADJUST] Early={early_proj}({early_rec}) → Full Bayes={bayesian_posterior}({bayesian_rec}) — {divergence_pct:.0f}% shift after all adjustments.")

            print(f"[PROJECTION] Bayesian={bayesian_posterior}({bayesian_rec}, {bayesian_prob:.0%}) | Early estimate={early_proj}({early_rec}) — MATH IS FINAL. Grok = explanation only.")

            # ── Apply nightly-learned bias offsets ──────────────────────────
            if CALIBRATION_ENABLED:
                try:
                    from calibration import apply_learned_offsets
                    _offset_venue = player_venue or req.venue or "home"
                    bayesian_posterior, _offset_note = await apply_learned_offsets(
                        posterior=bayesian_posterior,
                        prop_type=req.propType,
                        venue=_offset_venue,
                        recommendation=bayesian_rec,
                        league_id=req.leagueId,
                        sport="soccer",
                    )
                    if _offset_note:
                        bayesian_rec = "over" if bayesian_posterior > req.line else "under"
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
                "weights": {"math": 1.0, "grok": 0},  # Grok = explanation only, zero weight in projection
                "agreement": bayesian_rec == early_rec,
                "divergencePct": round(divergence_pct, 1),
                "note": "projectedValue is determined entirely by the Reverse Formula math engine. Grok writes explanation text only.",
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

        # Guard 3: UNDER skew penalty — stats have positive skew (outlier games pull mean up).
        # Penalty scales with edge: close lines get 3%, large gaps get up to 10%.
        # This prevents inflated confidence on aggressive UNDER calls like "proj=30, line=38.5".
        if rec == "under":
            adj_conf = prediction.get("confidenceScore", 50)
            penalty = min(10, max(3, round(edge * 0.9)))  # 3-10% penalty based on edge size
            prediction["confidenceScore"] = max(45, adj_conf - penalty)
            if adj_conf != prediction["confidenceScore"]:
                print(f"[GUARD] UNDER skew penalty: -{penalty}% confidence ({adj_conf} → {prediction['confidenceScore']})")

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

        # ═══════════════════════════════════════════════════════════════════
        # PASS GATE — Edge too narrow to recommend confidently
        # If the model's projection is within 8% of the book's line, the
        # real edge is inside the noise band of the model itself — variance
        # in a single match easily swings the outcome either way.
        # Recommending OVER/UNDER in this zone burns more picks than it wins.
        # ═══════════════════════════════════════════════════════════════════
        _pass_proj = prediction.get("projectedValue", req.line)
        if req.line > 0 and _pass_proj is not None:
            _edge_pct = abs(_pass_proj - req.line) / req.line * 100
            if _edge_pct < 8.0:
                _leaning = "over" if _pass_proj > req.line else "under"
                prediction["recommendation"] = "PASS"
                prediction["passReason"] = (
                    f"Projection ({_pass_proj}) is within {_edge_pct:.1f}% of line ({req.line}). "
                    f"Edge too narrow to call confidently — skip this one."
                )
                prediction["passLeaning"] = _leaning.upper()
                print(
                    f"[PASS GATE] {req.playerName} {req.propType}: "
                    f"proj={_pass_proj}, line={req.line}, gap={_edge_pct:.1f}% < 8% → PASS "
                    f"(leans {_leaning.upper()})"
                )

        # ── MATH LOCK: Always align sharpSummary + Verdict to the FINAL math outcome ──
        # Runs after PASS GATE so we use the true final recommendation (PASS/OVER/UNDER).
        # Gemini wrote analysis using an early Bayesian estimate. H2H + opponent profile
        # adjustments may have moved the projection significantly — this ensures the user-
        # visible text always matches the badge and projection number.
        import re as _re_lock
        _lock_final_rec = str(prediction.get("recommendation", "")).upper()  # PASS, OVER, or UNDER
        _lock_proj_raw  = prediction.get("projectedValue", req.line)
        _lock_proj_str  = str(int(_lock_proj_raw)) if _lock_proj_raw == int(_lock_proj_raw) else f"{_lock_proj_raw:.1f}"
        _lock_line_str  = str(int(req.line)) if req.line == int(req.line) else f"{req.line:.1f}"

        # Key evidence phrases from the math pipeline
        _lock_ev = []
        if position_comp_data and position_comp_data.get("avgStatValue") and position_comp_data.get("sampleSize", 0) >= 3:
            _lk_opp_avg = position_comp_data["avgStatValue"]
            _lk_opp_n   = position_comp_data.get("sampleSize", 0)
            _lk_opp_pos = position_comp_data.get("positionShort", "same-position players")
            _lock_ev.append(f"opponent profile ({_lk_opp_pos}s avg {_lk_opp_avg:.1f} in {_lk_opp_n} matchups)")
        if h2h_data:
            _h2h_lock_vals = [g.get("stat_value") or g.get("statValue") for g in h2h_data if g.get("stat_value") or g.get("statValue")]
            if _h2h_lock_vals:
                _h2h_lock_avg = round(sum(_h2h_lock_vals) / len(_h2h_lock_vals), 1)
                _lock_ev.append(f"H2H avg {_h2h_lock_avg:.1f} ({len(_h2h_lock_vals)} games)")
        if early_bayes and early_bayes.get("momentumLabel") in ("HOT", "COOLING"):
            _lock_ev.append(f"{early_bayes['momentumLabel'].lower()} recent form")

        _lock_season_avg = early_bayes.get("priorMean", "?") if early_bayes else "?"
        _lock_ev0 = _lock_ev[0] if _lock_ev else "matchup factors"

        if _lock_final_rec == "PASS":
            _lock_leaning = str(prediction.get("passLeaning", "")).upper() or "EVEN"
            prediction["sharpSummary"] = (
                f"Reverse Formula projects {_lock_proj_str} — close to the {_lock_line_str} line (leans {_lock_leaning}). "
                f"Edge is within the model's noise band — the math sees both sides. Season avg {_lock_season_avg} adjusted by {_lock_ev0}. Skip or use as a secondary pick only."
            )
            _lock_verdict = (
                f"**Verdict** — Reverse Formula projects **{_lock_proj_str}** vs line of {_lock_line_str}. "
                f"Gap is too narrow for a confident call — PASS. Leans {_lock_leaning} but noise band is too wide to commit."
            )
        elif _lock_final_rec == "OVER":
            _lock_market = f"The market underestimates this matchup — season avg {_lock_season_avg} gets boosted by {_lock_ev0}."
            prediction["sharpSummary"] = (
                f"Reverse Formula projects {_lock_proj_str} — OVER the {_lock_line_str} line. {_lock_market}"
            )
            _lock_verdict = (
                f"**Verdict** — Reverse Formula projects **{_lock_proj_str}**, clearing the {_lock_line_str} line (OVER). "
                f"Key driver: {_lock_ev0}."
            )
        else:  # UNDER
            _lock_market = f"The market overestimates output here — season avg {_lock_season_avg} gets suppressed by {_lock_ev0}."
            prediction["sharpSummary"] = (
                f"Reverse Formula projects {_lock_proj_str} — UNDER the {_lock_line_str} line. {_lock_market}"
            )
            _lock_verdict = (
                f"**Verdict** — Reverse Formula projects **{_lock_proj_str}**, falling short of the {_lock_line_str} line (UNDER). "
                f"Key suppressor: {_lock_ev0}."
            )

        # Replace the **Verdict** section in tacticalBreakdown
        _lock_tb = prediction.get("tacticalBreakdown", "")
        if _lock_tb:
            _lock_tb = _re_lock.sub(
                r'\*\*Verdict\*\*.*?(?=\n\n|\n\*\*|\Z)',
                _lock_verdict,
                _lock_tb,
                count=1,
                flags=_re_lock.DOTALL | _re_lock.IGNORECASE
            )
            prediction["tacticalBreakdown"] = _lock_tb

        print(f"[MATH LOCK] sharpSummary + Verdict → {_lock_final_rec} {_lock_proj_str} vs {_lock_line_str}")
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
        # ALWAYS override Grok's expectedGameType. Grok invents values like
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

        # Final sanitisation — reject any value Grok invented that isn't in the approved set
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

        # USE GROK OUTPUT DIRECTLY — no secondary synthesis
        rec = prediction.get('recommendation', 'over').upper()
        line = prediction.get('line', req.line)
        proj = prediction.get('projectedValue', '?')
        conf = prediction.get('confidenceScore', '?')
        pl = {
            "pass_attempts": "Pass Attempts", "passes": "Passes",
            "shots": "Shots", "shots_on_target": "Shots on Target",
            "tackles": "Tackles", "key_passes": "Key Passes",
            "saves": "Saves", "interceptions": "Interceptions",
            "blocks": "Blocks", "dribbles": "Dribbles",
            "fouls_drawn": "Fouls Drawn", "fouls_committed": "Fouls Committed",
            "crosses": "Crosses", "clearances": "Clearances",
            "duels_won": "Duels Won", "yellow_cards": "Yellow Cards",
            "shots_assisted": "Shots Assisted", "goals": "Goals", "assists": "Assists",
        }.get(req.propType, req.propType)
        consensus_note = prediction.get('consensusNote', '')

        # If Grok returned a solid tacticalBreakdown, use it directly
        grok_tb = prediction.get('tacticalBreakdown', '')
        if not grok_tb or len(str(grok_tb)) < 200:
            # Build from individual Grok fields
            tb_parts = []
            if prediction.get('sharpSummary'):
                tb_parts.append(f"**Verdict: {rec} {line} {pl}**\n{prediction['sharpSummary']}")
            else:
                tb_parts.append(f"**Verdict: {rec} {line} {pl}**\nProjected {proj} {pl.lower()} — favors {rec.lower()}.")

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
            print(f"[TIMING] Built tacticalBreakdown from AI fields: {len(prediction['tacticalBreakdown'])} chars")
        else:
            print(f"[TIMING] Using AI tacticalBreakdown directly ({grok_result.get('_source','?')}): {len(grok_tb)} chars")

        # ── DIRECTION GUARD: If math recommendation contradicts AI text, fix ALL text ──
        # The AI generates analysis based on its own projection which may differ from the
        # final math-anchored number. When they disagree on direction, we must sanitize
        # EVERY text field — not just the Verdict line — to prevent contradictions like
        # "smash over" appearing in the TL;DR while the badge shows UNDER.
        final_rec = prediction.get("recommendation", "").lower()
        final_proj = prediction.get("projectedValue", req.line)
        tb = prediction.get("tacticalBreakdown", "")
        if tb and final_rec:
            wrong_dir = "under" if final_rec == "over" else "over"
            right_dir = final_rec          # lowercase
            right_dir_cap = final_rec.capitalize()
            right_dir_up  = final_rec.upper()

            # Detect wrong-direction text by scanning the tacticalBreakdown directly.
            # Grok is explanation-only now (no projectedValue/recommendation), but it may
            # still accidentally write the wrong direction. Catch it by checking the text.
            import re as _re_scan
            _tb_raw = prediction.get("tacticalBreakdown", "")
            _sharp_raw = prediction.get("sharpSummary", "")
            # Look for definitive wrong-direction conclusion phrases in body/sharp
            _wrong_conclusion_patterns = [
                rf'(?i)(smash|bang|hammer|pound|back|take|play|fade)\s+the\s+{wrong_dir}',
                rf'(?i)reverse formula\s+(nails|projects|lands at)\s+[\d.]+\s+{wrong_dir}',
                rf'(?i)caps at\s+[\d.]+\s+{wrong_dir}',
                rf'(?i)\b{wrong_dir}\s+is\s+(the\s+)?(right|correct|clear|obvious)\s+(play|call|bet|side)',
                rf'(?i)(clear|obvious|easy)\s+{wrong_dir}',
            ]
            _ai_text_disagrees = any(
                _re_scan.search(p, _tb_raw + " " + _sharp_raw)
                for p in _wrong_conclusion_patterns
            )

            if _ai_text_disagrees:
                import re as _re_dg
                # ── Step 1: Fix action phrases and conclusion statements ──
                # Standard action phrase swaps
                _action_subs = {
                    f"smash {wrong_dir}": f"slight {right_dir} edge",
                    f"bang {wrong_dir}": f"lean {right_dir}",
                    f"hammer {wrong_dir}": f"lean {right_dir}",
                    f"pound {wrong_dir}": f"lean {right_dir}",
                    f"load up {wrong_dir}": f"take {right_dir}",
                    f"back {wrong_dir}": f"lean {right_dir}",
                    f"confident {wrong_dir}": f"marginal {right_dir}",
                    f"strong {wrong_dir}": f"marginal {right_dir}",
                    f"clears {req.line}": f"falls short of {req.line}",
                    f"racks {req.line}+": f"projects near {final_proj}",
                }
                for _bad, _good in _action_subs.items():
                    tb = _re_dg.sub(_re_dg.escape(_bad), _good, tb, flags=_re_dg.IGNORECASE)

                # ── Step 1b: Fix "Reverse Formula" conclusion phrases ──
                # These are body-text conclusions Grok writes when it believes UNDER/OVER.
                # Pattern: "Reverse Formula nails 57.8 under" → "Reverse Formula projects 66.0 over"
                # Pattern: "caps at 45.8 under" → "targets 50.0 over"
                # Pattern: "adjusts +19.8 context but caps at 45.8 under" → "lands at 50.0 over"
                _fin_p_dg = int(round(final_proj)) if final_proj == int(final_proj) else f"{final_proj:.1f}"
                tb = _re_dg.sub(
                    r'(?i)(reverse formula\s+(?:nails|projects|anchors|lands at|shows))\s+[\d.]+\s+' + wrong_dir,
                    rf'\1 {_fin_p_dg} {right_dir}',
                    tb
                )
                tb = _re_dg.sub(
                    r'(?i)(adjusts\s+[\+\-][\d.]+\s+context\s+but\s+caps\s+at)\s+[\d.]+\s+' + wrong_dir,
                    rf'lands at {_fin_p_dg} {right_dir}',
                    tb
                )
                tb = _re_dg.sub(
                    r'(?i)(caps\s+at)\s+[\d.]+\s+' + wrong_dir + r'\b',
                    rf'targets {_fin_p_dg} {right_dir}',
                    tb
                )
                # Generic "X under/over" conclusion at end of sentence (last word in sentence)
                tb = _re_dg.sub(
                    r'(?i)(\d+\.?\d*)\s+' + wrong_dir + r'\s*\.',
                    rf'{_fin_p_dg} {right_dir}.',
                    tb
                )

                # ── Step 2: Fully rewrite TL;DR section ──
                _tldr_match = _re_dg.search(r'\*\*TL;DR\*\*.*?(?=\n\*\*|\Z)', tb, _re_dg.DOTALL | _re_dg.IGNORECASE)
                if _tldr_match:
                    _punder = real_bayes.get("pUnder", 50) if real_bayes else 50
                    _pover  = real_bayes.get("pOver",  50) if real_bayes else 50
                    _winning_p = max(_punder, _pover)
                    _edge = round(abs(final_proj - req.line), 1)
                    _new_tldr = (
                        f"**TL;DR** — Math projects {final_proj} vs the {req.line} line — "
                        f"{right_dir_cap} ({_winning_p:.0f}% probability, {_edge} edge). "
                        f"Reverse Formula anchors the call; qualitative analysis was overridden by the model."
                    )
                    tb = tb[:_tldr_match.start()] + _new_tldr + tb[_tldr_match.end():]

                # ── Step 3: Fix Verdict line ──
                lines = tb.split("\n")
                for _i, _line in enumerate(lines):
                    if "**verdict**" in _line.lower():
                        lines[_i] = _re_dg.sub(
                            r'\b' + wrong_dir + r'\b', right_dir, _line, flags=_re_dg.IGNORECASE
                        )
                        break
                tb = "\n".join(lines)

                prediction["tacticalBreakdown"] = tb
                print(f"[DIRECTION GUARD] Full text rewrite: AI argued {wrong_dir.upper()}, math says {right_dir_up}. TL;DR replaced, body sanitized.")

            else:
                # Lighter fix: AI direction matches or is neutral — just patch Verdict line if needed
                first_line = tb.split("\n")[0] if tb else ""
                if "**verdict**" in first_line.lower() and wrong_dir in first_line.lower() and right_dir not in first_line.lower():
                    import re as _re_dg2
                    corrected = _re_dg2.sub(r'\b' + wrong_dir + r'\b', right_dir, first_line, flags=_re_dg2.IGNORECASE)
                    prediction["tacticalBreakdown"] = corrected + tb[len(first_line):]
                    print(f"[DIRECTION GUARD] Verdict line patched: {wrong_dir.upper()} → {right_dir_up}")

            # Fix sharpSummary
            sharp = prediction.get("sharpSummary", "")
            if sharp and _ai_text_disagrees:
                # AI text argued the WRONG direction → its sharpSummary is built around
                # that wrong premise and cannot be salvaged by appending a note.
                # Replace it entirely with a direction-correct expert summary.
                _punder = real_bayes.get("pUnder", 50) if real_bayes else 50
                _pover  = real_bayes.get("pOver",  50) if real_bayes else 50
                _winning_p = max(_punder, _pover)
                _edge_dg = round(abs(final_proj - req.line), 1)
                prediction["sharpSummary"] = (
                    f"Reverse Formula projects {final_proj} {right_dir_up} vs the {req.line} line "
                    f"({_winning_p:.0f}% P({right_dir_up}), {_edge_dg} edge). "
                    f"Qualitative analysis leaned {wrong_dir.upper()} but the mathematical model overrides — "
                    f"sharp bettors follow the math when it disagrees with narrative."
                )
                print(f"[DIRECTION GUARD] sharpSummary fully replaced: AI={wrong_dir.upper()} → math={right_dir_up}")
            elif sharp:
                # Direction agreed — just clean up any stray wrong-direction language
                import re as _re_sharp
                _sharp_has_wrong_action = any(
                    f"{a} {wrong_dir}" in sharp.lower()
                    for a in ("smash", "bang", "hammer", "pound", "load up", "strong")
                )
                if _sharp_has_wrong_action:
                    for _bad_s, _good_s in [
                        (f"smash {wrong_dir}", f"lean {right_dir}"),
                        (f"strong {wrong_dir}", f"marginal {right_dir}"),
                        (f"bang {wrong_dir}", f"lean {right_dir}"),
                    ]:
                        sharp = _re_sharp.sub(_re_sharp.escape(_bad_s), _good_s, sharp, flags=_re_sharp.IGNORECASE)
                    prediction["sharpSummary"] = sharp

        # ── POSSESSION NARRATIVE GUARD: fix AI attributing possession to the wrong team ──
        # e.g. AI says "Lanús possession mastery" when Lanús actually has 44% possession.
        # Checks the final possession numbers against the narrative and corrects misattribution.
        try:
            import re as _re_poss
            _poss_home_team   = match_dominance.get("homeTeamName", "")
            _poss_away_team   = match_dominance.get("awayTeamName", "")
            _home_poss_pct    = float(match_dominance.get("homePoss", 50) or 50)
            _away_poss_pct    = float(match_dominance.get("awayPoss", 50) or 50)
            # Normalise: who is the player's team vs opponent?
            _player_team_norm = (corrected_team_name or "").lower()
            _opp_team_norm    = (req.opponentName or "").lower()
            _player_poss = match_dominance.get("expectedPoss", 50) or 50
            _opp_poss    = match_dominance.get("oppExpectedPoss", 50) or 50
            _poss_gap = abs(_player_poss - _opp_poss)

            # Only correct when possession split is meaningful (>5pp gap)
            if _poss_gap >= 5 and corrected_team_name and req.opponentName:
                _dom_team  = corrected_team_name if _player_poss > _opp_poss else req.opponentName
                _sub_team  = req.opponentName    if _player_poss > _opp_poss else corrected_team_name
                _dom_team_lc = _dom_team.lower()
                _sub_team_lc = _sub_team.lower()

                # Possession-dominance keywords — phrases the AI uses for the controlling team
                _dom_keywords = [
                    "possession mastery", "possession dominance", "possession monster",
                    "controls possession", "control possession", "controlling possession",
                    "holds possession", "dominate possession", "possession edge",
                    "possession advantage", "higher possession", "more possession",
                    "keep the ball", "keeps the ball", "set the tempo", "sets the tempo",
                    "dictate play", "dictates play", "possession-heavy",
                ]

                _tb = prediction.get("tacticalBreakdown", "")
                if _tb:
                    _tb_lower = _tb.lower()
                    for _kw in _dom_keywords:
                        # Find each occurrence of the keyword
                        for _m in _re_poss.finditer(_re_poss.escape(_kw), _tb_lower):
                            # Look at the 60 chars before the keyword to see which team is mentioned
                            _ctx_start = max(0, _m.start() - 60)
                            _ctx = _tb_lower[_ctx_start:_m.start()]
                            # If the subordinate team (lower possession) is in context, that's wrong
                            if _sub_team_lc in _ctx and _dom_team_lc not in _ctx:
                                print(f"[POSS GUARD] AI attributed '{_kw}' to '{_sub_team}' ({_sub_team} has {_opp_poss if _sub_team_lc==_opp_team_norm else _player_poss:.0f}%) — correcting narrative.")
                                # Swap team names in a sentence window around the keyword
                                _sent_start = _tb_lower.rfind(".", 0, _m.start())
                                _sent_end   = _tb.find(".", _m.end())
                                if _sent_start < 0: _sent_start = 0
                                if _sent_end < 0: _sent_end = len(_tb)
                                _sent = _tb[_sent_start:_sent_end + 1]
                                _corrected_sent = _re_poss.sub(
                                    _re_poss.escape(_sub_team),
                                    f"{_dom_team}",
                                    _sent, flags=_re_poss.IGNORECASE
                                )
                                _tb = _tb[:_sent_start] + _corrected_sent + _tb[_sent_end + 1:]
                                _tb_lower = _tb.lower()
                    prediction["tacticalBreakdown"] = _tb

                # Also check sharpSummary
                _ss = prediction.get("sharpSummary", "")
                if _ss:
                    _ss_lower = _ss.lower()
                    for _kw in _dom_keywords:
                        for _m in _re_poss.finditer(_re_poss.escape(_kw), _ss_lower):
                            _ctx = _ss_lower[max(0, _m.start()-60):_m.start()]
                            if _sub_team_lc in _ctx and _dom_team_lc not in _ctx:
                                _ss = _re_poss.sub(
                                    _re_poss.escape(_sub_team), _dom_team, _ss, flags=_re_poss.IGNORECASE
                                )
                                _ss_lower = _ss.lower()
                    prediction["sharpSummary"] = _ss
        except Exception as _poss_guard_err:
            print(f"[POSS GUARD] Error: {_poss_guard_err}")

        # ── CONFIDENCE LANGUAGE GUARD: strip overconfident phrasing on low-edge calls ──
        _is_coin_flip = prediction.get("coinFlip", False)
        _final_conf   = prediction.get("confidenceScore", 100)
        if _is_coin_flip or _final_conf <= 55:
            import re as _re
            _overconfident_phrases = [
                ("strong value",   "slim edge"),
                ("strong edge",    "slim edge"),
                ("strong lean",    "slight lean"),
                ("reliable over",  "marginal over"),
                ("reliable under", "marginal under"),
                ("reliable",       "marginal"),
                ("high confidence","low confidence"),
                ("at strong value", "at slim value"),
            ]
            for _field in ("tacticalBreakdown", "sharpSummary"):
                _txt = prediction.get(_field, "")
                if not _txt:
                    continue
                _changed = False
                for _bad, _good in _overconfident_phrases:
                    if _bad in _txt.lower():
                        _txt = _re.sub(_bad, _good, _txt, flags=_re.IGNORECASE)
                        _changed = True
                if _changed:
                    prediction[_field] = _txt
                    print(f"[CONFIDENCE GUARD] Replaced overconfident phrasing in {_field} (conf={_final_conf}%)")

        # ── TEXT FINALIZATION: Normalize projection numbers + strip false overrides ──
        # Problem: Grok writes analysis using its own projected value (e.g. 42.8).
        # But the final badge uses the math-anchored+calibrated value (e.g. 48).
        # This creates "projection says 42.8 but badge shows 48" contradictions.
        # Fix: after ALL numeric adjustments are locked, replace the AI's stale
        # projection number in all text fields with the final authoritative value.
        # Also: only say "overrides qualitative lean" when directions *actually* differed.
        import re as _re_fin
        _fin_proj  = prediction.get("projectedValue", req.line)
        _fin_rec   = prediction.get("recommendation", "over").lower()
        _fusion    = prediction.get("fusionApplied", {})
        # earlyEstimate = math's early_bayes before all multi-factor adjustments (not Grok's opinion)
        _ai_proj   = _fusion.get("earlyEstimate", _fin_proj)
        _ai_rec    = _fusion.get("earlyEstimateRec", _fin_rec)
        _dirs_differed = (_ai_rec.lower() != _fin_rec.lower())

        # Build patterns for the AI's stale number (e.g. 42.8, 42, ~43)
        _ai_p_int   = int(round(_ai_proj))
        _ai_p_float = f"{_ai_proj:.1f}"
        _fin_p_str  = str(int(round(_fin_proj))) if _fin_proj == int(_fin_proj) else f"{_fin_proj:.1f}"

        def _normalize_text(txt: str) -> str:
            if not txt:
                return txt

            # ── Step A: Always strip false "overrides qualitative lean" notes ──
            # These get appended by the direction guard or AI even when directions agree.
            # Remove unconditionally when AI and math called the same direction.
            if not _dirs_differed:
                txt = _re_fin.sub(
                    r'\.\s*Math \(\d+% P\((OVER|UNDER)\)\) overrides qualitative lean'
                    r' — (narrow|solid|strong|slim|marginal|clear) (OVER|UNDER) edge\.',
                    '', txt, flags=_re_fin.IGNORECASE
                )

            # ── Step B-0: Fix projection-attribution phrases regardless of which number Grok wrote ──
            # Grok may internally reason about a stale number and write it in phrases like
            # "Reverse Formula's 50.1 projection" or "projects 52 under" or "at 48.3 projected".
            # Replace the number in these phrases with _fin_p_str unconditionally.
            _proj_phrase_patterns = [
                # "Reverse Formula's 50.1 projection" → "Reverse Formula's 49 projection"
                (r"(Reverse Formula(?:'s)?)\s+(\d+\.?\d*)\s+(projection)", rf"\1 {_fin_p_str} \3"),
                # "Reverse Formula projects/nails/lands at 52.3" → "Reverse Formula projects 49"
                (r"(Reverse Formula\s+(?:nails|projects|lands at|estimates))\s+(\d+\.?\d*)", rf"\1 {_fin_p_str}"),
                # "projects 52.3 over/under" → "projects 49 over/under"
                (r"(projects?|projected(?:\s+at)?|projects\s+to\s+be)\s+(\d+\.?\d*)\s+(over|under)", rf"\1 {_fin_p_str} \3"),
                # "projecting 26.8" (gerund form, any context) → "projecting 24"
                (r"(projecting\s+)(\d+\.?\d*)(\b)", rf"\g<1>{_fin_p_str}\3"),
                # "50.1 projected" or "50.1 projection" → "49 projected/projection"
                (r"(\d+\.?\d*)\s+(project(?:ed|ion)?\b)", rf"{_fin_p_str} \2"),
                # "projection 60.3" (projection before number) → "projection 62"
                (r"(project(?:ed|ion|ing)?\s+)(\d+\.?\d*)(\b)", rf"\g<1>{_fin_p_str}\3"),
                # "at 50.1 projected" → "at 49 projected"
                (r"(at\s+)(\d+\.?\d*)(\s+project(?:ed|ion)?)", rf"\g<1>{_fin_p_str}\3"),
                # "with 50.1 projection" / "with a 50.1 projection" → "with 49 projection"
                (r"(with\s+(?:a\s+)?)(\d+\.?\d*)(\s+projection)", rf"\g<1>{_fin_p_str}\3"),
                # "lands at 60.3" → "lands at 62"
                (r"(\blands?\s+at\s+)(\d+\.?\d*)(\b)", rf"\g<1>{_fin_p_str}\3"),
                # "sits 60.3 under 64.5" → "sits 62 under 64.5" (only when followed by over/under)
                (r"(\bsits?\s+(?:at\s+)?)(\d+\.?\d*)(\s+(?:over|under)\b)", rf"\g<1>{_fin_p_str}\3"),
                # "clears X passes" / "clears X with" / "clears X at" / "clears X:" (OVER language)
                (r"(clears?\s+[\d.]+\s+(?:passes?|attempts?|with|at|:)\s*)(\d+\.?\d*)(\b)", rf"\g<1>{_fin_p_str}\3"),
                # "falls short of X.X at Y.Y" / "cruises under X.X at Y.Y"
                (r"((?:falls short of|cruises (?:over|under))\s+[\d.]+\s+at\s+)(\d+\.?\d*)(\b)", rf"\g<1>{_fin_p_str}\3"),
                # "sharp +3 edge on Reverse Formula's 50.1 projection"
                (r"(sharp\s+[+-]?\d+\.?\d*\s+edge\s+on\s+Reverse Formula's?\s+)(\d+\.?\d*)(\s+projection)", rf"\g<1>{_fin_p_str}\3"),
                # "pushing him past 50.1" / "pushing him to 50.1"
                (r"(pushing\s+(?:him|her|them)\s+(?:past|to|above|over)\s+)(\d+\.?\d*)(\b)", rf"\g<1>{_fin_p_str}\3"),
                # "inflates ... to 50.1 territory" / "to 50.1 range" / "to 50.1 levels"
                (r"(\bto\s+)(\d+\.?\d*)(\s+(?:territory|range|level[s]?)\b)", rf"\g<1>{_fin_p_str}\3"),
                # "capping Ginter at 60.3" / "caps Ginter at 60.3" / "caps him at 60.3"
                (r"(cap(?:ping|s)?\s+(?:\w+\s+)?at\s+)(\d+\.?\d*)(\b)", rf"\g<1>{_fin_p_str}\3"),
                # "ceiling of 60.3" / "ceiling at 60.3"
                (r"(ceiling\s+(?:of|at)\s+)(\d+\.?\d*)(\b)", rf"\g<1>{_fin_p_str}\3"),
            ]
            for _pat, _repl in _proj_phrase_patterns:
                txt = _re_fin.sub(_pat, _repl, txt, flags=_re_fin.IGNORECASE)

            # ── Step B: Number substitution — only when gap is meaningful (≥1.0) ──
            if abs(_ai_proj - _fin_proj) < 1.0:
                pass  # Numbers very close — skip number substitution but still run Step D
            else:
                # Replace float version (42.8) first, then int (42), to avoid partial matches
                for _old_num in [_ai_p_float, str(_ai_p_int)]:
                    # Only replace when it appears as a standalone number (not part of a larger number)
                    txt = _re_fin.sub(r'(?<!\d)' + _re_fin.escape(_old_num) + r'(?!\d)', _fin_p_str, txt)

            # ── Step D: Direction-conflict correction ──
            # When AI wrote OVER language but math says UNDER (or vice versa), fix the directional verbs.
            # Also catches cases where the AI's Grok projection (not the Bayesian anchor) differs.
            _grok_proj_str = str(int(round(_fin_proj))) if _fin_proj == int(_fin_proj) else f"{_fin_proj:.1f}"
            if _fin_rec == "under":
                # Replace OVER-implying phrases with UNDER-appropriate ones
                # "clears X passes" / "clears X attempts" → "stays near X" (projection is already the correct number)
                txt = _re_fin.sub(r'\bclears?\s+([\d.]+)\s+(passes?|attempts?|shots?|saves?)',
                    rf'stays near \1 \2', txt, flags=_re_fin.IGNORECASE)
                # "clears X" (standalone) → "stays under X"
                txt = _re_fin.sub(r'\bclears?\s+([\d.]+)(?!\s*(?:passes?|attempts?|shots?|saves?))',
                    rf'stays under \1', txt, flags=_re_fin.IGNORECASE)
                # "surpasses/exceeds/hits X+" → "stays below X"
                txt = _re_fin.sub(r'\b(surpasses?|exceeds?|hits)\s+([\d.]+)\+?',
                    rf'stays below \2', txt, flags=_re_fin.IGNORECASE)
                # "+X% edge" when line is UNDER → no change needed (edge direction, not direction word)
            elif _fin_rec == "over":
                # Replace UNDER-implying phrases with OVER-appropriate ones
                # "falls short of X" → "reaches X" 
                txt = _re_fin.sub(r'\bfalls?\s+short\s+of\s+([\d.]+)',
                    rf'reaches \1', txt, flags=_re_fin.IGNORECASE)
                # "stays below/under X" → "reaches X"
                txt = _re_fin.sub(r'\bstays?\s+(?:below|under)\s+([\d.]+)',
                    rf'reaches \1', txt, flags=_re_fin.IGNORECASE)

            # ── Step C: Patch "narrow edge" language when actual edge is large ──
            _edge_size = abs(_fin_proj - req.line)
            if _edge_size > 5:
                txt = _re_fin.sub(r'\bnarrow (over|under) edge\b', r'strong \1 edge', txt, flags=_re_fin.IGNORECASE)
                txt = _re_fin.sub(r'\bnarrow edge\b', 'strong edge', txt, flags=_re_fin.IGNORECASE)
                txt = _re_fin.sub(r'\bslight (over|under) edge\b', r'clear \1 edge', txt, flags=_re_fin.IGNORECASE)
            elif _edge_size > 2.5:
                txt = _re_fin.sub(r'\bnarrow (over|under) edge\b', r'solid \1 edge', txt, flags=_re_fin.IGNORECASE)

            return txt

        for _tf in ("tacticalBreakdown", "sharpSummary", "reasoning", "scenarioAnalysis", "gameFlowDynamics"):
            _old_txt = prediction.get(_tf, "")
            if _old_txt:
                _new_txt = _normalize_text(_old_txt)
                if _new_txt != _old_txt:
                    prediction[_tf] = _new_txt

        if abs(_ai_proj - _fin_proj) >= 1.0:
            print(f"[TEXT NORM] AI proj={_ai_proj:.1f} → final={_fin_proj:.1f} — normalized numeric references in text fields")

        # GAME SCRIPT INTELLIGENCE REMOVED.
        # Was applying confidence deltas based on trailing-game scenarios,
        # which systematically pushed borderline home-GK picks toward OVER.
        # The underlying model's projection + confidence is sufficient.
        prediction["gameScript"] = {"key_finding": "Game script analysis disabled.", "scenarios": []}

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
        # positionComparison removed — not shown in UI

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

