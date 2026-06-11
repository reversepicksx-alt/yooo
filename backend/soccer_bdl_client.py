"""
BallDontLie Soccer API client.
Covers: EPL, La Liga, Serie A, Bundesliga, Ligue 1, Champions League, MLS, World Cup.
Same API key as all other BDL sports (MLB_BDL_API_KEY). No daily quota.

Field mapping — BDL returns these fields; we normalise to the RP game-log format
that the rest of the prediction pipeline expects (same as _build_game_log in predict.py).
"""

import asyncio
import time
import os
import logging
from datetime import datetime, timezone
from typing import Optional

import httpx
from config import db

log = logging.getLogger("soccer_bdl_client")

BDL_BASE = "https://api.balldontlie.io"
BDL_KEY  = os.environ.get("MLB_BDL_API_KEY", "")

# ── League routing ────────────────────────────────────────────────────────────
# API-Football league ID  →  BDL path prefix + version
LEAGUE_TO_BDL: dict[int, str] = {
    39:  "/epl/v2",              # English Premier League
    140: "/laliga/v1",           # La Liga
    135: "/seriea/v1",           # Serie A
    78:  "/bundesliga/v1",       # Bundesliga
    61:  "/ligue1/v1",           # Ligue 1
    2:   "/ucl/v1",              # UEFA Champions League
    3:   "/ucl/v1",              # Europa League — route to UCL client (similar)
    253: "/mls/v1",              # MLS
    1:   "/fifa/worldcup/v1",    # FIFA World Cup
}

# Seasons to try, newest-first.  MLS uses the calendar year (2026 season = 2026).
# European leagues also use the year the season STARTS (2025-26 = 2025 in BDL).
# We compute dynamically so MLS 2026 is always the first candidate.
_cur_yr = datetime.now(tz=timezone.utc).year
_CURRENT_SEASONS = [_cur_yr, _cur_yr - 1, _cur_yr - 2]   # e.g. [2026, 2025, 2024]


def is_bdl_league(league_id: int) -> bool:
    """Return True when this league is supported by the BDL soccer API."""
    return league_id in LEAGUE_TO_BDL


# ── HTTP client ───────────────────────────────────────────────────────────────
_rate_sem   = asyncio.Semaphore(6)   # shared key — keep burst polite
_last_call: float = 0.0
_MIN_GAP    = 0.12                   # ~8 req/s


async def _get(path: str, params: dict | None = None) -> Optional[dict]:
    global _last_call
    if not BDL_KEY:
        log.warning("[BDL-SOC] No API key (MLB_BDL_API_KEY not set)")
        return None

    url     = f"{BDL_BASE}{path}"
    headers = {"Authorization": BDL_KEY}

    async with _rate_sem:
        gap = time.monotonic() - _last_call
        if gap < _MIN_GAP:
            await asyncio.sleep(_MIN_GAP - gap)
        try:
            async with httpx.AsyncClient(timeout=20) as c:
                resp = await c.get(url, headers=headers, params=params or {})
        except Exception as exc:
            log.error(f"[BDL-SOC] Network error {path}: {exc}")
            return None
        finally:
            _last_call = time.monotonic()

    if resp.status_code == 200:
        return resp.json()
    if resp.status_code == 429:
        log.warning(f"[BDL-SOC] 429 rate-limited on {path}")
        await asyncio.sleep(5)
        return None
    log.warning(f"[BDL-SOC] {resp.status_code} on {path}: {resp.text[:200]}")
    return None


# ── Field normalisation ───────────────────────────────────────────────────────
def _norm(raw: dict) -> dict:
    """
    Convert one BDL SoccerPlayerMatchStats row into the RP game-log format
    (matches the dict produced by _build_game_log in routes/predict.py).

    BDL soccer data has two tiers of availability:
      Tier-1 (always populated): goals, assists, shots_total, shots_on_target,
        fouls_committed, fouls_suffered, yellow_cards, red_cards, offsides.
      Tier-2 (often None): passes_total, tackles, clearances, minutes_played,
        key_passes, dribbles, interceptions, rating, xg — these fields are
        populated by secondary data providers and may be absent for some
        seasons/leagues. We pass through whatever is present; the prediction
        pipeline tolerates None values via `if g.get(field) is not None`.

    For minutes_played=None we fall back to appearances*90 so the log is not
    discarded outright by the minutes>0 filter downstream.
    """
    gk_saves = (raw.get("goalkeeper_saves") or raw.get("saves") or 0)
    duels_w  = raw.get("duels_won")  or 0
    duels_l  = raw.get("duels_lost") or 0
    # Infer minutes from appearances when minutes_played is not provided.
    # appearances=1 → player started (90 min estimate); 0 → DNP.
    # This avoids discarding valid stat rows just because minutes is missing.
    raw_mins = raw.get("minutes_played")
    apps     = raw.get("appearances") or 0
    minutes  = int(raw_mins) if raw_mins is not None else (90 if apps >= 1 else 0)
    return {
        "minutes":             minutes,
        "rating":              raw.get("rating"),
        # passes
        "passes_total":        raw.get("passes_total"),
        "passes_key":          raw.get("key_passes"),
        "passes_accuracy":     raw.get("passes_accurate"),
        "passes_crosses":      raw.get("crosses_total"),
        # shots
        "shots_total":         raw.get("shots_total"),
        "shots_on":            raw.get("shots_on_target"),
        # tackles / defence
        "tackles_total":       raw.get("tackles"),
        "tackles_interceptions": raw.get("interceptions"),
        "tackles_blocks":      raw.get("blocked_shots"),
        "tackles_clearances":  raw.get("clearances"),
        # dribbles
        "dribbles_attempts":   raw.get("dribbles_attempted"),
        "dribbles_success":    raw.get("dribbles_completed"),
        # fouls
        "fouls_drawn":         raw.get("fouls_suffered") or raw.get("was_fouled"),
        "fouls_committed":     raw.get("fouls_committed"),
        # duels
        "duels_won":           duels_w or None,
        "duels_total":         (duels_w + duels_l) or None,
        # aerial
        "aerial_duels_won":    raw.get("aerial_duels_won"),
        # goals
        "goals_total":         raw.get("goals") or 0,
        "goals_assists":       raw.get("assists") or 0,
        "goals_saves":         gk_saves or None,
        # cards
        "cards_yellow":        raw.get("yellow_cards") or 0,
        # bonus (not in API-Football format but harmless extras)
        "xg":                  raw.get("expected_goals"),
        "xa":                  raw.get("expected_assists"),
        "ball_recoveries":     raw.get("ball_recoveries"),
        "big_chances_created": raw.get("big_chances_created"),
        # BDL match reference (used for enrichment below)
        "_bdl_match_id":       raw.get("match_id"),
        "_bdl_team_id":        raw.get("team_id"),
        "_bdl_player_id":      raw.get("player_id"),
        # is_home comes directly from player_match_stats and is authoritative —
        # the schedule home_team field may differ (WC format quirk).
        "_is_home_raw":        raw.get("is_home"),
    }


# ── Teams lookup (cached per league per day) ──────────────────────────────────
async def _teams_lookup(league_id: int) -> dict[int, str]:
    """Return {bdl_team_id: team_name} for all teams in the league (24 h cache)."""
    path      = LEAGUE_TO_BDL[league_id]
    cache_key = f"bdl_soc_teams_{league_id}"
    try:
        doc    = await db.bdl_soccer_cache.find_one({"_k": cache_key})
        cached = _cache_hit(doc, ttl_full=24 * 3600)
        if cached is not None:
            return {int(k): v for k, v in cached.items()} if isinstance(cached, dict) else {}
    except Exception:
        pass

    teams: dict[int, str] = {}
    cursor = None
    for _ in range(5):          # max 5 pages
        params: dict = {"per_page": 100}
        if cursor:
            params["cursor"] = cursor
        result = await _get(f"{path}/teams", params)
        if not result:
            break
        for t in result.get("data", []):
            tid  = t.get("id")
            name = t.get("name") or t.get("display_name") or ""
            if tid:
                teams[tid] = name
        cursor = result.get("meta", {}).get("next_cursor")
        if not cursor:
            break

    if teams:
        try:
            await db.bdl_soccer_cache.update_one(
                {"_k": cache_key},
                {"$set": {"_k": cache_key, "d": {str(k): v for k, v in teams.items()},
                          "_ts": datetime.now(timezone.utc)}},
                upsert=True
            )
        except Exception:
            pass
    return teams


# ── Match metadata lookup ─────────────────────────────────────────────────────
async def _matches_for_team(league_id: int, bdl_team_id: int, season: int) -> dict[int, dict]:
    """
    Return {match_id: match_data} for all matches the team played in that season.
    Cached for 6 h.
    """
    path      = LEAGUE_TO_BDL[league_id]
    cache_key = f"bdl_soc_tm_{league_id}_{bdl_team_id}_{season}"
    try:
        doc    = await db.bdl_soccer_cache.find_one({"_k": cache_key})
        cached = _cache_hit(doc, ttl_full=6 * 3600)
        if cached is not None:
            return {int(k): v for k, v in cached.items()} if isinstance(cached, dict) else {}
    except Exception:
        pass

    match_map: dict[int, dict] = {}
    params = {"team_ids[]": bdl_team_id, "season": season, "per_page": 60}
    result = await _get(f"{path}/matches", params)
    if result:
        for m in result.get("data", []):
            mid = m.get("id")
            if mid:
                match_map[mid] = m

    if match_map:
        try:
            await db.bdl_soccer_cache.update_one(
                {"_k": cache_key},
                {"$set": {"_k": cache_key, "d": {str(k): v for k, v in match_map.items()},
                          "_ts": datetime.now(timezone.utc)}},
                upsert=True
            )
        except Exception:
            pass
    return match_map


# ── Cache helpers ─────────────────────────────────────────────────────────────
def _cache_hit(doc: Optional[dict], ttl_full: int, ttl_empty: int = 1800) -> Optional[list]:
    """
    Return cached list if still fresh, else None (indicating a re-fetch is needed).
    ttl_full  = seconds to honour a non-empty cached result.
    ttl_empty = seconds to honour an empty cached result (default 30 min).
               Keeps empty results from being stuck forever during tournaments
               that have no stats yet (e.g. WC 2026 before first match).
    """
    if not doc or doc.get("d") is None:
        return None
    data = doc["d"]
    ts   = doc.get("_ts")
    if ts is None:
        return None
    age = (datetime.now(timezone.utc) - ts).total_seconds()
    ttl = ttl_empty if not data else ttl_full
    return data if age < ttl else None


# ── Player search ─────────────────────────────────────────────────────────────
async def _search_player(league_id: int, name: str) -> list[dict]:
    """Search for players by (partial) name in one BDL soccer league.
    Non-empty result: 4 h cache.  Empty result: 30 min cache (so WC squads
    populate once the first matches are played without waiting hours)."""
    path      = LEAGUE_TO_BDL[league_id]
    slug      = name.lower().strip()
    cache_key = f"bdl_soc_ps_{league_id}_{slug}"
    try:
        doc    = await db.bdl_soccer_cache.find_one({"_k": cache_key})
        cached = _cache_hit(doc, ttl_full=4 * 3600)
        if cached is not None:
            return cached
    except Exception:
        pass

    result = await _get(f"{path}/players", {"search": name, "per_page": 10})
    players = result.get("data", []) if result else []

    try:
        await db.bdl_soccer_cache.update_one(
            {"_k": cache_key},
            {"$set": {"_k": cache_key, "d": players, "_ts": datetime.now(timezone.utc)}},
            upsert=True
        )
    except Exception:
        pass
    return players


async def _find_player(league_id: int, player_name: str) -> Optional[dict]:
    """
    Find the best-matching player dict for player_name in the given league.
    Falls back to last-name-only search if full-name search returns nothing.
    """
    players = await _search_player(league_id, player_name)
    if not players:
        # try last-name token
        parts = player_name.strip().split()
        if len(parts) > 1:
            players = await _search_player(league_id, parts[-1])
    if not players:
        return None

    name_lc = player_name.lower()
    # Match against any available name field (endpoints differ: EPL uses
    # display_name, WC uses name; short_name is normalised across all).
    for p in players:
        display = (p.get("display_name") or p.get("name") or "").lower()
        short   = (p.get("short_name") or "").lower()
        if not display and not short:
            continue
        if (name_lc in display or display in name_lc or
                name_lc in short  or short  in name_lc):
            return p
    return players[0]


async def search_bdl_players(query: str) -> list[dict]:
    """
    Search for players across ALL BDL-supported soccer leagues in parallel.
    Returns records in the same format as extract_player() in routes/players.py
    so results can be returned directly from the /api/players/search endpoint.
    Called when API-Football quota is exhausted / account suspended.
    """
    if not BDL_KEY or not query or len(query.strip()) < 3:
        return []

    query = query.strip()

    # Search all unique BDL paths concurrently (UCL/Europa share one path)
    seen_paths: set[str] = set()
    tasks: list = []
    task_league_ids: list[int] = []
    for league_id, path in LEAGUE_TO_BDL.items():
        if path in seen_paths:
            continue
        seen_paths.add(path)
        tasks.append(_search_player(league_id, query))
        task_league_ids.append(league_id)

    results_raw = await asyncio.gather(*tasks, return_exceptions=True)

    out: list[dict] = []
    seen_ids: set[int] = set()
    for league_id, raw in zip(task_league_ids, results_raw):
        if isinstance(raw, Exception) or not raw:
            continue
        for p in raw:
            pid = p.get("id")
            if pid and pid in seen_ids:
                continue
            if pid:
                seen_ids.add(pid)

            display_name = (
                p.get("display_name") or p.get("name") or p.get("short_name") or ""
            ).strip()

            firstname = p.get("first_name") or p.get("firstname") or ""
            lastname  = p.get("last_name")  or p.get("lastname")  or ""
            if not firstname and not lastname and display_name:
                parts     = display_name.split()
                firstname = parts[0] if parts else ""
                lastname  = " ".join(parts[1:]) if len(parts) > 1 else ""

            # Team: BDL returns team_ids list; name sometimes in player record
            team_ids  = p.get("team_ids") or []
            team_id   = team_ids[0] if team_ids else 0
            team_obj  = p.get("team") or {}
            team_name = (
                team_obj.get("name") if isinstance(team_obj, dict) else ""
            ) or p.get("team_name") or ""

            out.append({
                "id":         pid or 0,
                "name":       display_name,
                "firstname":  firstname,
                "lastname":   lastname,
                "age":        p.get("age") or 0,
                "nationality": p.get("nationality") or p.get("country") or "",
                "photo":      "",
                "teamId":     team_id,
                "teamName":   team_name,
                "leagueId":   league_id,
                "position":   p.get("position") or "",
            })

    log.info(f"[BDL-SOC] search_bdl_players('{query}'): {len(out)} results across {len(seen_paths)} leagues")
    return out


# ── Per-match stats ───────────────────────────────────────────────────────────
async def _player_match_stats_raw(
    league_id: int, bdl_player_id: int, season: int
) -> list[dict]:
    """Fetch raw BDL player_match_stats rows for one player+season.
    Non-empty result: 6 h cache.  Empty result: 30 min cache (WC tournament
    starts with 0 stats; re-check frequently so the first match populates fast)."""
    path      = LEAGUE_TO_BDL[league_id]
    cache_key = f"bdl_soc_pms_{league_id}_{bdl_player_id}_{season}"
    try:
        doc    = await db.bdl_soccer_cache.find_one({"_k": cache_key})
        cached = _cache_hit(doc, ttl_full=6 * 3600)
        if cached is not None:
            return cached
    except Exception:
        pass

    result = await _get(f"{path}/player_match_stats",
                        {"player_ids[]": bdl_player_id, "seasons[]": season, "per_page": 40})
    rows = result.get("data", []) if result else []

    try:
        await db.bdl_soccer_cache.update_one(
            {"_k": cache_key},
            {"$set": {"_k": cache_key, "d": rows, "_ts": datetime.now(timezone.utc)}},
            upsert=True
        )
    except Exception:
        pass
    return rows


# ── Main entry point ──────────────────────────────────────────────────────────
async def get_game_logs(
    league_id:   int,
    player_name: str,
    last_n:      int = 25,
) -> tuple[list[dict], Optional[int]]:
    """
    High-level function used by predict.py.

    Returns (game_logs, bdl_player_id).
    game_logs is a list of dicts in the RP game-log format (same fields as
    _build_game_log in routes/predict.py), newest match first.
    Returns ([], None) on any failure.
    """
    if not is_bdl_league(league_id) or not BDL_KEY:
        return [], None

    # 1. Find player
    player = await _find_player(league_id, player_name)
    if not player:
        log.info(f"[BDL-SOC] player not found: '{player_name}' league={league_id}")
        return [], None

    bdl_pid = player.get("id")
    if not bdl_pid:
        return [], None

    # 2. Fetch raw stats — try current season then previous.
    # Deduplicate by match_id: tournament APIs (e.g. World Cup) return identical
    # rows for every season query (2026/2025/2024 all yield the same 3 WC matches).
    all_raw: list[dict] = []
    _seen_match_ids: set = set()
    for season in _CURRENT_SEASONS:
        rows = await _player_match_stats_raw(league_id, bdl_pid, season)
        for row in rows:
            mid = row.get("match_id")
            if mid and mid in _seen_match_ids:
                continue
            if mid:
                _seen_match_ids.add(mid)
            all_raw.append(row)
        if len(all_raw) >= last_n:
            break

    if not all_raw:
        log.info(f"[BDL-SOC] no stats for '{player_name}' (bdl_id={bdl_pid}) league={league_id}")
        return [], None

    # 3. Normalise stats
    logs: list[dict] = []
    for raw in all_raw[:last_n]:
        gl = _norm(raw)
        if not gl.get("minutes"):
            continue
        # Blank match-context fields (filled in step 4)
        gl.update(date="", opponent="", venue="", score="", league="", round="")
        logs.append(gl)

    if not logs:
        return [], None

    # 4. Enrich with match metadata (date, opponent, home/away, score)
    #
    # BDL player_match_stats.match_id is a BDL-internal round scheduling ID
    # that cannot be directly joined to matches.id. Instead we:
    #   (a) use player.team_ids[0] as the reliable team ID (from search result)
    #   (b) fetch the full team schedule for the season via team_ids[] filter
    #   (c) apply sequential matching — both series are newest-first, so
    #       stat_row[i] ↔ team_match[i] (minor drift when player misses a game
    #       only affects opponent-name metadata, never the stat values).
    bdl_team_id: Optional[int] = (player.get("team_ids") or [None])[0]

    # World Cup (and similar tournament) players use country_code instead of team_ids.
    # Resolve team ID by matching country_code against the league's teams list.
    if bdl_team_id is None and player.get("country_code"):
        try:
            _cc   = player["country_code"]
            _path = LEAGUE_TO_BDL[league_id]
            _tr   = await _get(f"{_path}/teams", {"per_page": 100})
            for _t in (_tr or {}).get("data", []):
                if (_t.get("country_code") or _t.get("abbreviation")) == _cc:
                    bdl_team_id = _t.get("id")
                    log.info(f"[BDL-SOC] WC team resolved: country_code={_cc} → bdl_team_id={bdl_team_id}")
                    break
        except Exception as _te:
            log.warning(f"[BDL-SOC] WC team resolve failed: {_te}")

    if bdl_team_id:
        try:
            # Fetch teams lookup (for opponent name) + team season schedule concurrently
            teams_task   = asyncio.ensure_future(_teams_lookup(league_id))
            match_tasks  = [
                asyncio.ensure_future(_matches_for_team(league_id, bdl_team_id, s))
                for s in _CURRENT_SEASONS
            ]
            teams_map, *match_maps_raw = await asyncio.gather(
                teams_task, *match_tasks, return_exceptions=True
            )
            if isinstance(teams_map, Exception):
                teams_map = {}

            # Build a flat list of all team matches sorted newest-first.
            # WC matches use "datetime" field; club matches use "date".
            # Deduplicate by match ID — tournament APIs return the same match
            # for every season query (WC 2026 matches appear under 2026/2025/2024).
            all_team_matches: list[dict] = []
            _seen_tm_ids: set = set()
            for mm in match_maps_raw:
                if isinstance(mm, Exception):
                    continue
                for m in mm.values():
                    mid = m.get("id")
                    if mid and mid in _seen_tm_ids:
                        continue
                    if mid:
                        _seen_tm_ids.add(mid)
                    all_team_matches.append(m)
            all_team_matches.sort(
                key=lambda m: (m.get("date") or m.get("datetime") or ""),
                reverse=True
            )

            # Sequential mapping: stat row i ↔ team match i (newest-first both sides).
            # Venue uses _is_home_raw from the stat row when present — it is authoritative.
            # The schedule home_team field can disagree with player_match_stats.is_home
            # in tournament formats (e.g. WC group stage scheduling conventions).
            for i, gl in enumerate(logs):
                if i >= len(all_team_matches):
                    break
                m = all_team_matches[i]
                # WC matches use nested objects; club matches use flat IDs
                home_id = m.get("home_team_id") or (m.get("home_team") or {}).get("id")
                away_id = m.get("away_team_id") or (m.get("away_team") or {}).get("id")
                # Venue: prefer the authoritative is_home field from the stat row.
                # The schedule home_team label can disagree with player_match_stats.is_home
                # in tournament formats (WC group stage scheduling conventions), so we
                # never rely on the schedule for venue.
                _is_home_raw = gl.get("_is_home_raw")
                if _is_home_raw is not None:
                    gl["venue"] = "home" if _is_home_raw else "away"
                else:
                    gl["venue"] = "home" if bdl_team_id == home_id else "away"
                # Opponent: always determined from schedule position (bdl_team_id vs home_id).
                # This is independent of venue — when the schedule says "Mexico is home_team"
                # the opponent is always the away_team, regardless of is_home_raw value.
                _sched_is_home = (bdl_team_id == home_id)
                opp_id = away_id if _sched_is_home else home_id
                # Try teams_map first (id→name); fall back to nested team object name
                opp_name = (teams_map or {}).get(opp_id, "")
                if not opp_name and opp_id:
                    _opp_obj = m.get("away_team") if _sched_is_home else m.get("home_team")
                    opp_name = (_opp_obj or {}).get("name", "") if isinstance(_opp_obj, dict) else ""
                gl["opponent"] = opp_name
                raw_date    = m.get("date") or m.get("datetime") or ""
                gl["date"]  = raw_date[:10] if raw_date else ""
                h_score     = m.get("home_score")
                a_score     = m.get("away_score")
                if h_score is not None and a_score is not None:
                    gl["score"] = f"{h_score}-{a_score}"
                gl["round"] = (
                    m.get("round_name") or str(m.get("round_number", ""))
                    if (m.get("round_name") or m.get("round_number")) else ""
                )
                # Record the real BDL match ID so shot spatial data can be joined below
                gl["_real_match_id"] = m.get("id")

            enriched = sum(1 for g in logs if g.get("opponent"))
            log.info(f"[BDL-SOC] '{player_name}': {len(logs)} logs, {enriched} enriched")
        except Exception as exc:
            log.warning(f"[BDL-SOC] enrichment error for '{player_name}': {exc}")

    # 4b. Spatial shot enrichment — BDL /match_shots provides per-shot xG, xGoT,
    #     and coordinates. We group by match_id and attach per-game aggregates to
    #     each log, then use spatial counts to fill BDL Tier-2 data gaps (shots_total
    #     and shots_on are often None in player_match_stats for older seasons).
    if bdl_pid:
        try:
            shots_data = await _fetch_player_shots(league_id, bdl_pid)
            if shots_data:
                enriched_shots = 0
                for gl in logs:
                    mid = gl.get("_real_match_id")
                    if mid and mid in shots_data:
                        sd = shots_data[mid]
                        gl.update(sd)
                        # Fill Tier-2 gaps with spatial counts
                        if gl.get("shots_total") is None and sd.get("shots_spatial"):
                            gl["shots_total"] = sd["shots_spatial"]
                        if gl.get("shots_on") is None and sd.get("shots_on_target_spatial"):
                            gl["shots_on"] = sd["shots_on_target_spatial"]
                        enriched_shots += 1
                if enriched_shots:
                    log.info(f"[BDL-SOC] Shot spatial: {enriched_shots}/{len(logs)} logs "
                             f"enriched with xG/xGoT for '{player_name}'")
        except Exception as exc:
            log.warning(f"[BDL-SOC] Shot spatial enrichment error for '{player_name}': {exc}")

    # 4c. Formation + starter-status enrichment from match_lineups, and yellow-card
    #     enrichment from match_events — both share the same internal match_id as
    #     player_match_stats, so we join directly by _bdl_match_id.
    #
    #     Strategy:
    #       (a) Fetch the team's lineup rows (team_ids[]=bdl_team_id).
    #           Find the player's row by accent-normalised name comparison.
    #           → produces {match_id → {formation, is_starter, lineup_player_id}}
    #       (b) Use the resolved lineup_player_id to fetch match_events filtered
    #           by that player_ids[] value, then count yellow cards per match_id.
    #     Both requests run concurrently.
    if bdl_team_id and logs:
        import unicodedata as _ud
        def _anorm(s: str) -> str:
            return ''.join(
                c for c in _ud.normalize('NFD', s.lower().strip())
                if _ud.category(c) != 'Mn'
            )
        _pnorm = _anorm(player_name)
        _lup_path = LEAGUE_TO_BDL[league_id]

        try:
            # (a) Fetch team lineups
            _lup_r = await _get(
                f"{_lup_path}/match_lineups",
                {"team_ids[]": bdl_team_id, "per_page": 100},
            )
            _lup_rows = (_lup_r or {}).get("data", [])

            # Build {match_id → lineup_info} and find the player's internal ID
            _lup_map: dict[int, dict] = {}
            _lineup_pid: int | None = None
            for _lr in _lup_rows:
                _lup_mid  = _lr.get("match_id")
                _lup_pl   = _lr.get("player") or {}
                _lup_pnm  = _anorm(_lup_pl.get("name") or "")
                if _lup_pnm == _pnorm:
                    _lup_map[_lup_mid] = {
                        "formation":  _lr.get("formation"),
                        "is_starter": _lr.get("is_starter", True),
                    }
                    if _lineup_pid is None:
                        _lineup_pid = _lup_pl.get("id")

            if _lup_map:
                for gl in logs:
                    _mid = gl.get("_bdl_match_id")
                    if _mid and _mid in _lup_map:
                        gl["formation"]  = _lup_map[_mid]["formation"]
                        gl["is_starter"] = _lup_map[_mid]["is_starter"]
                log.info(
                    f"[BDL-SOC] Formation enrichment: {len(_lup_map)} match(es) "
                    f"for '{player_name}' (lineup_pid={_lineup_pid})"
                )

            # (b) Fetch card events using the lineup player_id (if resolved)
            if _lineup_pid:
                _ev_r = await _get(
                    f"{_lup_path}/match_events",
                    {"player_ids[]": _lineup_pid,
                     "incident_types[]": "card", "per_page": 100},
                )
                _ev_rows = (_ev_r or {}).get("data", [])
                _card_by_mid: dict[int, int] = {}
                for _ev in _ev_rows:
                    _ev_pl = _ev.get("player") or {}
                    # Confirm it's the player's own card (not a teammate in the same match)
                    if _ev.get("incident_type") == "card" and \
                       _ev.get("incident_class") == "yellow" and \
                       _ev_pl.get("id") == _lineup_pid:
                        _emid = _ev.get("match_id")
                        if _emid:
                            _card_by_mid[_emid] = _card_by_mid.get(_emid, 0) + 1

                if _card_by_mid:
                    for gl in logs:
                        _mid = gl.get("_bdl_match_id")
                        if _mid in _card_by_mid:
                            gl["cards_yellow"] = _card_by_mid[_mid]
                    log.info(
                        f"[BDL-SOC] Card enrichment: {sum(_card_by_mid.values())} "
                        f"yellow card(s) across {len(_card_by_mid)} match(es) "
                        f"for '{player_name}'"
                    )
        except Exception as _lup_err:
            log.warning(
                f"[BDL-SOC] Formation/card enrichment error for '{player_name}': {_lup_err}"
            )

    # 5. Add per-90 for the target stat (generic — caller will recompute if needed)
    #    Remove internal BDL reference fields before returning
    for gl in logs:
        gl.pop("_bdl_match_id",   None)
        gl.pop("_bdl_team_id",    None)
        gl.pop("_bdl_player_id",  None)
        gl.pop("_real_match_id",  None)
        gl.pop("_is_home_raw",    None)

    return logs, bdl_pid


async def _fetch_player_shots(
    league_id: int,
    bdl_player_id: int,
) -> dict[int, dict]:
    """
    Fetch all shot-level spatial data for one player from BDL /match_shots.

    Returns {match_id: {xg_shot, xgot_shot, shots_spatial, shots_on_target_spatial, avg_shot_x}}

    shot_type perspective: player_id is always the SHOOTER.
      "goal"    = scored
      "miss"    = off target (xgot=0)
      "save"    = keeper saved their shot (xgot>0 → on target)
      "blocked" = blocked before keeper

    xgot > 0  ↔  shot required a save or scored  ↔  "on target" proxy.
    avg_shot_x: mean of player_x coordinate (lower = closer to own goal ≈ defensive;
                higher = closer to opponent goal ≈ attacking). BDL uses 0-100 scale
                where ~50 = midfield.

    Cached 6 h per player.
    """
    if not is_bdl_league(league_id) or not BDL_KEY:
        return {}

    cache_key = f"bdl_shots_p_{league_id}_{bdl_player_id}"
    try:
        doc    = await db.bdl_soccer_cache.find_one({"_k": cache_key})
        cached = _cache_hit(doc, ttl_full=6 * 3600)
        if cached is not None:
            return {int(k): v for k, v in cached.items()} if isinstance(cached, dict) else {}
    except Exception:
        pass

    path  = LEAGUE_TO_BDL[league_id]
    shots: list[dict] = []
    cursor = None
    for _page in range(3):          # max 3 pages (300 shots) — covers 3+ active seasons
        params: dict = {"player_ids[]": bdl_player_id, "per_page": 100}
        if cursor:
            params["cursor"] = cursor
        result = await _get(f"{path}/match_shots", params)
        if not result:
            break
        shots.extend(result.get("data", []))
        cursor = result.get("meta", {}).get("next_cursor")
        if not cursor:
            break

    # Aggregate per match
    by_match: dict[int, dict] = {}
    for s in shots:
        mid = s.get("match_id")
        if not mid:
            continue
        if mid not in by_match:
            by_match[mid] = {"xg": 0.0, "xgot": 0.0, "cnt": 0, "sot": 0, "xs": []}
        xg   = s.get("xg")   or 0.0
        xgot = s.get("xgot") or 0.0
        by_match[mid]["xg"]   += xg
        by_match[mid]["xgot"] += xgot
        by_match[mid]["cnt"]  += 1
        if xgot > 0:                          # required keeper action → on target
            by_match[mid]["sot"] += 1
        px = s.get("player_x")
        if px is not None:
            by_match[mid]["xs"].append(px)

    result_map: dict[int, dict] = {}
    for mid, d in by_match.items():
        xs = d["xs"]
        result_map[mid] = {
            "xg_shot":                 round(d["xg"],  4),
            "xgot_shot":               round(d["xgot"], 4),
            "shots_spatial":           d["cnt"],
            "shots_on_target_spatial": d["sot"],
            "avg_shot_x":              round(sum(xs) / len(xs), 1) if xs else None,
        }

    try:
        await db.bdl_soccer_cache.update_one(
            {"_k": cache_key},
            {"$set": {"_k": cache_key,
                      "d":  {str(k): v for k, v in result_map.items()},
                      "_ts": datetime.now(timezone.utc)}},
            upsert=True
        )
    except Exception:
        pass

    log.info(f"[BDL-SOC] Shot spatial: fetched {len(shots)} shots / "
             f"{len(result_map)} matches for player_id={bdl_player_id}")
    return result_map


async def get_player_props(league_id: int, bdl_match_id: int) -> list[dict]:
    """
    Fetch player prop betting lines for a specific BDL match.
    Returns list of prop dicts (market_type, player_id, line, over_odds, under_odds).
    """
    if not is_bdl_league(league_id):
        return []
    path   = LEAGUE_TO_BDL[league_id]
    result = await _get(f"{path}/odds/player_props", {"match_id": bdl_match_id})
    return result.get("data", []) if result else []


async def get_player_injuries(league_id: int) -> list[dict]:
    """
    Fetch the current injury report for a BDL soccer league.
    Returns list of injury dicts with player name and status.
    """
    if not is_bdl_league(league_id):
        return []
    path   = LEAGUE_TO_BDL[league_id]
    result = await _get(f"{path}/player_injuries")
    return result.get("data", []) if result else []


# ── Live match tracking ────────────────────────────────────────────────────────

_BDL_STATUS_FINISHED: frozenset = frozenset({
    "STATUS_FINAL", "final", "STATUS_FULL_TIME", "STATUS_AFTER_EXTRA_TIME",
    "STATUS_AFTER_PENALTIES", "FT", "AET", "PEN",
})
_BDL_STATUS_LIVE: frozenset = frozenset({
    "STATUS_IN_PROGRESS", "in_progress", "STATUS_HALFTIME", "halftime",
    "STATUS_EXTRA_TIME", "STATUS_PENALTY_SHOOTOUT",
    "1H", "2H", "HT", "ET", "P", "LIVE", "BT",
})

# propType → normalized field name produced by _norm()
BDL_SOCCER_STAT_MAP: dict = {
    "goals":           "goals_total",
    "assists":         "goals_assists",
    "shots":           "shots_total",
    "shots_on_target": "shots_on",
    "pass_attempts":   "passes_total",
    "key_passes":      "passes_key",
    "shots_assisted":  "passes_key",
    "saves":           "goals_saves",
    "tackles":         "tackles_total",
    "interceptions":   "tackles_interceptions",
    "blocks":          "tackles_blocks",
    "clearances":      "tackles_clearances",
    "dribbles":        "dribbles_attempts",
    "fouls_drawn":     "fouls_drawn",
    "fouls_committed": "fouls_committed",
    "crosses":         "passes_crosses",
    "duels_won":       "duels_won",
    "yellow_cards":    "cards_yellow",
}


def _normalize_match(raw: dict, league_id: int) -> Optional[dict]:
    """Normalize a raw BDL match dict to the common picks-tracking schema."""
    mid = raw.get("id")
    if not mid:
        return None

    status_raw  = str(raw.get("status") or "").strip()
    is_finished = status_raw in _BDL_STATUS_FINISHED
    is_live     = status_raw in _BDL_STATUS_LIVE

    # WC uses nested home_team / away_team objects; MLS/EPL/etc use flat IDs
    if isinstance(raw.get("home_team"), dict):
        home_id   = raw["home_team"].get("id")
        away_id   = raw["away_team"].get("id")
        home_name = raw["home_team"].get("name", "")
        away_name = raw["away_team"].get("name", "")
    else:
        home_id   = raw.get("home_team_id")
        away_id   = raw.get("away_team_id")
        name      = raw.get("name", "")
        if " at " in name:
            parts     = name.split(" at ", 1)
            away_name = parts[0].strip()
            home_name = parts[1].strip()
        else:
            home_name = away_name = ""

    home_score = raw.get("home_score") if raw.get("home_score") is not None else 0
    away_score = raw.get("away_score") if raw.get("away_score") is not None else 0
    date_str   = (raw.get("date") or raw.get("datetime") or "")[:10]

    return {
        "id":              mid,
        "league_id":       league_id,
        "status":          status_raw,
        "is_live":         is_live,
        "is_finished":     is_finished,
        "home_score":      home_score,
        "away_score":      away_score,
        "home_team_id":    home_id,
        "away_team_id":    away_id,
        "home_team_name":  home_name,
        "away_team_name":  away_name,
        "date":            date_str,
        "first_half_home": raw.get("first_half_home_score"),
        "first_half_away": raw.get("first_half_away_score"),
    }


async def get_live_and_recent_matches(league_id: int) -> list:
    """
    Return normalized BDL match dicts covering today ± 2 days.
    Cache TTL: 15 min for non-empty results, 5 min for empty (live polling).
    MLS: team names resolved via _teams_lookup (match objects only have IDs).
    WC/EPL/etc: team names inline from nested objects or match name field.
    """
    if not is_bdl_league(league_id) or not BDL_KEY:
        return []
    path      = LEAGUE_TO_BDL[league_id]
    cache_key = f"bdl_soc_live_{league_id}"
    try:
        doc    = await db.bdl_soccer_cache.find_one({"_k": cache_key})
        cached = _cache_hit(doc, ttl_full=15 * 60, ttl_empty=5 * 60)
        if cached is not None:
            return cached
    except Exception:
        pass

    from datetime import timedelta
    today     = datetime.now(tz=timezone.utc)
    date_strs = [
        (today - timedelta(days=2)).strftime("%Y-%m-%d"),
        (today - timedelta(days=1)).strftime("%Y-%m-%d"),
        today.strftime("%Y-%m-%d"),
        (today + timedelta(days=1)).strftime("%Y-%m-%d"),
    ]

    raw_all: list = []
    for d in date_strs:
        result = await _get(f"{path}/matches", {"dates[]": d, "per_page": 20})
        if result:
            raw_all.extend(result.get("data", []))

    normalized = [n for raw in raw_all if (n := _normalize_match(raw, league_id)) is not None]

    if league_id == 253 and normalized:
        try:
            teams_map = await _teams_lookup(league_id)
            for m in normalized:
                if not m["home_team_name"] and m.get("home_team_id"):
                    m["home_team_name"] = teams_map.get(m["home_team_id"], "")
                if not m["away_team_name"] and m.get("away_team_id"):
                    m["away_team_name"] = teams_map.get(m["away_team_id"], "")
        except Exception:
            pass

    try:
        await db.bdl_soccer_cache.update_one(
            {"_k": cache_key},
            {"$set": {"_k": cache_key, "d": normalized,
                      "_ts": datetime.now(timezone.utc)}},
            upsert=True
        )
    except Exception:
        pass
    return normalized


def find_match_for_pick(matches: list, pick: dict) -> Optional[dict]:
    """
    Fuzzy-match a pick to a BDL match using opponent name.
    Priority: live > finished > scheduled (upcoming).
    """
    opp = (pick.get("opponentName") or "").lower().strip()
    if not opp:
        return None

    def _opp_matches(m: dict) -> bool:
        h = (m.get("home_team_name") or "").lower()
        a = (m.get("away_team_name") or "").lower()
        return bool(h or a) and (opp in h or h in opp or opp in a or a in opp)

    for m in matches:
        if m.get("is_live") and _opp_matches(m):
            return m
    for m in matches:
        if m.get("is_finished") and _opp_matches(m):
            return m
    for m in matches:
        if _opp_matches(m):
            return m
    return None


async def get_player_settled_stat(
    league_id: int, player_name: str, stat_field: str,
) -> tuple:
    """
    Fetch the player's most recent match stat from BDL (bypasses 6h cache).
    Returns (stat_value, minutes_played).  Used for settlement in picks.py.
    """
    if not is_bdl_league(league_id) or not BDL_KEY:
        return None, 0

    player = await _find_player(league_id, player_name)
    if not player:
        return None, 0
    bdl_pid = player.get("id")
    if not bdl_pid:
        return None, 0

    path   = LEAGUE_TO_BDL[league_id]
    result = await _get(f"{path}/player_match_stats",
                        {"player_ids[]": bdl_pid, "seasons[]": _cur_yr, "per_page": 5})
    rows = result.get("data", []) if result else []
    if not rows:
        return None, 0

    norm = _norm(rows[0])
    val  = norm.get(stat_field)
    mins = norm.get("minutes") or 0
    return val, int(mins)
