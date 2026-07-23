"""
BallDontLie MLB API client with rate-limiting and MongoDB caching.
Paid tier: 600 req/min. We use a semaphore + min interval to stay safe.
All MongoDB cache operations are fully fault-tolerant — if Atlas is unreachable
the client falls through to the BDL API directly.
"""
import asyncio
import time
import os
import logging
from datetime import datetime, timezone, date
from typing import Optional

import httpx
from config import db

log = logging.getLogger("mlb_client")

MLB_API_BASE = "https://api.balldontlie.io/mlb/v1"
# Key hardcoded as fallback; override via MLB_BDL_API_KEY env var
MLB_API_KEY = os.environ.get("MLB_BDL_API_KEY", "951b8b73-a036-4b30-924f-19f322766545")

_rate_sem = asyncio.Semaphore(2)   # shared key across all BDL sports — keep 2 slots
_last_req_time: float = 0.0
_MIN_INTERVAL = 0.25  # 4 req/s per slot → ~8 req/s total; comfortable under 600 req/min

CACHE_TTL = {
    "teams":         7 * 86400,   # 7 days
    "player":        2 * 3600,    # 2 hours (was 6h — trades need to surface quickly)
    "player_search": 4 * 3600,    # 4 hours (was 24h — stale teams on traded players)
    "stats":         2 * 3600,    # 2 hours (live season)
    "season_stats":  2 * 3600,
}


async def _get(path: str, params: dict = None) -> dict:
    """Single BDL request with rate-limiting.
    IMPORTANT: the 429-retry sleep happens OUTSIDE the semaphore so other
    requests are not blocked while we wait for the rate-limit window to reset."""
    global _last_req_time
    headers = {"Authorization": MLB_API_KEY}
    url = f"{MLB_API_BASE}{path}"

    # ── First attempt ─────────────────────────────────────────────────────────
    async with _rate_sem:
        elapsed = time.monotonic() - _last_req_time
        if elapsed < _MIN_INTERVAL:
            await asyncio.sleep(_MIN_INTERVAL - elapsed)
        try:
            async with httpx.AsyncClient(timeout=20) as c:
                resp = await c.get(url, headers=headers, params=params or {})
        except Exception as e:
            raise RuntimeError(f"MLB API network error: {e}")
        finally:
            _last_req_time = time.monotonic()

        if resp.status_code != 429:
            if resp.status_code >= 400:
                raise RuntimeError(f"MLB API error {resp.status_code}: {resp.text[:200]}")
            return resp.json()

        # Got a 429 — capture retry-after BEFORE releasing the semaphore
        retry_after = min(int(resp.headers.get("retry-after", "5")), 10)

    # ── Sleep OUTSIDE the semaphore so other slots stay available ─────────────
    log.warning(f"[MLB CLIENT] 429 on {path} — waiting {retry_after}s before retry")
    await asyncio.sleep(retry_after)

    # ── Retry attempt ─────────────────────────────────────────────────────────
    async with _rate_sem:
        elapsed = time.monotonic() - _last_req_time
        if elapsed < _MIN_INTERVAL:
            await asyncio.sleep(_MIN_INTERVAL - elapsed)
        try:
            async with httpx.AsyncClient(timeout=20) as c:
                resp = await c.get(url, headers=headers, params=params or {})
        except Exception as e:
            raise RuntimeError(f"MLB API network error on retry: {e}")
        finally:
            _last_req_time = time.monotonic()

        if resp.status_code >= 400:
            raise RuntimeError(f"MLB API error {resp.status_code}: {resp.text[:200]}")
        return resp.json()


def _cache_fresh(doc: Optional[dict], ttl_seconds: int) -> bool:
    if not doc:
        return False
    ts = doc.get("ts", "")
    if not ts:
        return False
    try:
        age = (datetime.now(timezone.utc) -
               datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)).total_seconds()
        return age < ttl_seconds
    except Exception:
        return False


async def _cache_get(key: str) -> Optional[dict]:
    """Read from MongoDB cache. Returns None if unreachable."""
    try:
        return await db.mlb_cache.find_one({"key": key}, {"_id": 0})
    except Exception as e:
        log.debug(f"[MLB CACHE] read miss (DB unreachable): {e}")
        return None


async def _cache_set(key: str, data) -> None:
    """Write to MongoDB cache. Silently skips if DB is unreachable."""
    try:
        await db.mlb_cache.update_one(
            {"key": key},
            {"$set": {"key": key, "data": data, "ts": datetime.now(timezone.utc)}},
            upsert=True,
        )
    except Exception as e:
        log.debug(f"[MLB CACHE] write skip (DB unreachable): {e}")


# ── MLB Stats API (free, no auth) ─────────────────────────────────────────
# Used as a fallback when BallDontLie doesn't have a player (young call-ups,
# recent signings, etc.). Player IDs from statsapi are ≥ 500 000 and never
# collide with BDL IDs (which are typically < 10 000).

MLBSTATS_BASE = "https://statsapi.mlb.com/api/v1"
_STATSAPI_ID_THRESHOLD = 100_000  # IDs above this are MLB Stats API


def _transform_bdl_log(raw: dict) -> dict:
    """Normalise a raw BDL /stats entry to the same field schema as _statsapi_game_logs.

    The BDL /stats endpoint returns stats directly on the object (not nested
    under a "game" sub-dict).  Field names are:
      Pitcher: p_k, p_hits, p_bb, er, ip, pitch_count, batters_faced
      Batter:  hits, hr, rbi, bb, k, runs, total_bases, stolen_bases, doubles, plate_appearances

    date is NOT in the /stats response — it's resolved later in _enrich_game_logs
    by matching game_id against the team-schedule cache.
    """
    # Pitcher fields — actual BDL field names
    pk    = raw.get("p_k")           # pitcher strikeouts
    p_h   = raw.get("p_hits")        # hits allowed
    p_bb_ = raw.get("p_bb")          # walks allowed
    er    = raw.get("er") or raw.get("earned_runs")
    ip    = raw.get("ip")
    pc    = raw.get("pitch_count") or raw.get("pitches") or raw.get("number_of_pitches")

    # Batter fields
    h     = raw.get("hits")          # null for pitchers
    bb    = raw.get("bb")            # null for pitchers
    ks_b  = raw.get("k")             # batter strikeouts (null for pitchers)

    # date: BDL /stats does not include it directly; game_id lets _enrich resolve it
    date_str = (raw.get("date") or "")[:10]
    game_id  = raw.get("game_id")

    return {
        "date":              date_str,
        "game_id":           game_id,
        # pitcher Stats-API-shaped fields
        "p_k":               pk,
        "ip":                ip,
        "p_hits":            p_h,
        "er":                er,
        "p_bb":              p_bb_,
        "pitch_count":       pc,
        "batters_faced":     raw.get("batters_faced"),
        # batter Stats-API-shaped fields
        "hits":              h,
        "hr":                raw.get("hr") or raw.get("home_runs"),
        "rbi":               raw.get("rbi"),
        "bb":                bb,
        "k":                 ks_b,
        "runs":              raw.get("runs"),
        "total_bases":       raw.get("total_bases"),
        "stolen_bases":      raw.get("stolen_bases"),
        "doubles":           raw.get("doubles"),
        "plate_appearances": raw.get("plate_appearances"),
        "_bdl_source":       True,
    }


async def _statsapi_get(path: str, params: dict = None) -> dict:
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(f"{MLBSTATS_BASE}{path}", params=params or {})
            r.raise_for_status()
            return r.json()
    except Exception as e:
        log.warning(f"[MLBSTATS] GET {path} failed: {e}")
        return {}


def _statsapi_person_to_bdl(p: dict) -> dict:
    """Convert an MLB Stats API person dict into BDL-compatible format."""
    pos_info = p.get("primaryPosition", {})
    pos_abbr = pos_info.get("abbreviation", "")
    pos_type = pos_info.get("type", "")
    if pos_type == "Pitcher" or pos_abbr in ("SP", "RP", "P"):
        position = pos_abbr if pos_abbr in ("SP", "RP") else "P"
    else:
        position = pos_abbr or pos_type

    team = p.get("currentTeam", {})
    team_name = team.get("name", "")
    return {
        "id":         p["id"],
        "full_name":  p.get("fullName", ""),
        "first_name": p.get("firstName", ""),
        "last_name":  p.get("lastName", ""),
        "position":   position,
        "team": {
            "id":           team.get("id"),
            "name":         team_name,
            "display_name": team_name,
            "slug":         team_name.lower().replace(" ", "-"),
            "abbreviation": "",
        },
        "active": p.get("active", True),
        "jersey": p.get("primaryNumber"),
        "age":    p.get("currentAge"),
        "bats_throws": "",
        "_source": "mlbstats",
    }


async def _statsapi_search_players(query: str, limit: int = 15) -> list:
    """Search MLB Stats API for players by name."""
    data = await _statsapi_get("/people/search", {"names": query, "sportId": 1})
    people = data.get("people", [])

    # Fallback: try last name only for multi-word queries
    if not people:
        words = query.strip().split()
        if len(words) > 1:
            data2 = await _statsapi_get("/people/search", {"names": words[-1], "sportId": 1})
            people = data2.get("people", [])

    # Prefer active players; hydrate currentTeam if missing
    active = [p for p in people if p.get("active")]
    if not active:
        active = people

    results = []
    for p in active[:limit]:
        # If person has no currentTeam, try fetching full record
        if not p.get("currentTeam") and p.get("id"):
            full = await _statsapi_get(f"/people/{p['id']}", {"hydrate": "currentTeam"})
            people_list = full.get("people", [])
            if people_list:
                p = people_list[0]
        results.append(_statsapi_person_to_bdl(p))

    return results


async def _statsapi_get_player(player_id: int) -> Optional[dict]:
    """Fetch a single player from MLB Stats API and return in BDL format."""
    data = await _statsapi_get(f"/people/{player_id}", {"hydrate": "currentTeam"})
    people = data.get("people", [])
    if not people:
        return None
    return _statsapi_person_to_bdl(people[0])


async def _statsapi_game_logs(player_id: int, season: int, group: str = "hitting") -> list:
    """Fetch per-game stats from MLB Stats API and normalise to BDL field names.

    The Stats API gameLog split already contains isHome, isWin, and opponent —
    we extract them here so tiles don't need a separate team-schedule enrichment pass.
    """
    data = await _statsapi_get(
        f"/people/{player_id}/stats",
        {"stats": "gameLog", "group": group, "season": season, "sportId": 1},
    )
    splits = []
    for stat_block in data.get("stats", []):
        splits.extend(stat_block.get("splits", []))

    logs = []
    for split in splits:
        st = split.get("stat", {})
        game_info = split.get("game", {})
        game_id = game_info.get("gamePk", 0)

        # ── Game context — present in basic gameLog response, no hydration needed ──
        is_home: bool | None = split.get("isHome")          # True/False/None
        is_win:  bool | None = split.get("isWin")           # True/False/None
        opp_obj  = split.get("opponent") or {}
        opp_abbr = (
            opp_obj.get("abbreviation") or
            opp_obj.get("teamCode") or
            opp_obj.get("shortName") or
            opp_obj.get("name") or
            None
        )
        venue = ("home" if is_home else "away") if is_home is not None else None

        ctx = {
            "opponent": opp_abbr,
            "isHome":   is_home,
            "venue":    venue,
            "won":      is_win,
        }

        if group == "hitting":
            entry = {
                "game_id":          game_id,
                "hits":             st.get("hits"),
                "runs":             st.get("runs"),
                "rbi":              st.get("rbi"),
                "hr":               st.get("homeRuns"),
                "bb":               st.get("baseOnBalls"),
                "k":                st.get("strikeOuts"),
                "total_bases":      st.get("totalBases"),
                "stolen_bases":     st.get("stolenBases"),
                "doubles":          st.get("doubles"),
                "plate_appearances":st.get("plateAppearances"),
                "at_bats":          st.get("atBats"),
                "avg":              st.get("avg"),
                "date":             split.get("date", ""),
                **ctx,
            }
        else:  # pitching
            ip_str = st.get("inningsPitched", "")
            try:
                ip_val = float(ip_str) if ip_str else None
            except (ValueError, TypeError):
                ip_val = None
            entry = {
                "game_id":      game_id,
                "ip":           ip_val,
                "p_k":          st.get("strikeOuts"),
                "p_hits":       st.get("hits"),
                "er":           st.get("earnedRuns"),
                "p_bb":         st.get("baseOnBalls"),
                "pitch_count":  st.get("numberOfPitches"),
                "batters_faced":st.get("battersFaced"),
                "era":          st.get("era"),
                "date":         split.get("date", ""),
                **ctx,
            }
        logs.append(entry)

    # Sort newest-first (same as BDL)
    logs.sort(key=lambda g: g.get("date", ""), reverse=True)
    return logs


async def _statsapi_season_stats(player_id: int, season: int, group: str = "hitting") -> Optional[dict]:
    """Fetch season aggregate stats from MLB Stats API and normalise to BDL field names."""
    data = await _statsapi_get(
        f"/people/{player_id}/stats",
        {"stats": "season", "group": group, "season": season, "sportId": 1},
    )
    splits = []
    for stat_block in data.get("stats", []):
        splits.extend(stat_block.get("splits", []))
    if not splits:
        return None
    st = splits[0].get("stat", {})

    if group == "hitting":
        gp = st.get("gamesPlayed") or 0
        return {
            "batting_gp":  gp,
            "batting_h":   st.get("hits"),
            "batting_hr":  st.get("homeRuns"),
            "batting_rbi": st.get("rbi"),
            "batting_bb":  st.get("baseOnBalls"),
            "batting_so":  st.get("strikeOuts"),
            "batting_r":   st.get("runs"),
            "batting_tb":  st.get("totalBases"),
            "batting_sb":  st.get("stolenBases"),
            "batting_2b":  st.get("doubles"),
            "batting_ab":  st.get("atBats"),
            "batting_avg": st.get("avg"),
        }
    else:  # pitching
        gp = st.get("gamesPlayed") or 0
        ip_str = st.get("inningsPitched", "")
        try:
            ip_val = float(ip_str) if ip_str else None
        except (ValueError, TypeError):
            ip_val = None
        return {
            "pitching_gp": gp,
            "pitching_k":  st.get("strikeOuts"),
            "pitching_ip": ip_val,
            "pitching_h":  st.get("hits"),
            "pitching_er": st.get("earnedRuns"),
            "pitching_bb": st.get("baseOnBalls"),
            "pitching_pc": st.get("numberOfPitches"),
            "pitching_bf": st.get("battersFaced"),
        }


# ── MLB Stats API team lookup cache ──────────────────────────────────────────
_MLB_TEAMS_CACHE: dict = {}       # teamId → team object
_MLB_TEAMS_CACHE_TIME: float = 0.0
_MLB_TEAMS_TTL: float = 86400.0   # 24h — team rosters change slowly


async def _get_mlb_teams() -> list:
    """Fetch (and memory-cache for 24 h) the full MLB team list from Stats API."""
    global _MLB_TEAMS_CACHE, _MLB_TEAMS_CACHE_TIME
    if _MLB_TEAMS_CACHE and (time.time() - _MLB_TEAMS_CACHE_TIME) < _MLB_TEAMS_TTL:
        return list(_MLB_TEAMS_CACHE.values())
    data = await _statsapi_get("/teams", {"sportId": 1})
    teams = data.get("teams", [])
    _MLB_TEAMS_CACHE = {t["id"]: t for t in teams}
    _MLB_TEAMS_CACHE_TIME = time.time()
    return teams


async def _resolve_opp_team_id(opp_name: str) -> int:
    """Fuzzy-match an opponent name / abbreviation to an MLB Stats API team ID.

    Checks (in order): abbreviation exact-match, full name contains, location
    name contains, shortName contains, or the token is a substring of any field.
    Returns 0 when no match is found.
    """
    if not opp_name:
        return 0
    q = opp_name.strip().lower()
    teams = await _get_mlb_teams()
    # Exact abbreviation match first (fastest / most precise)
    for t in teams:
        if t.get("abbreviation", "").lower() == q:
            return t["id"]
    # Broader fuzzy pass
    for t in teams:
        name  = t.get("name", "").lower()
        loc   = t.get("locationName", "").lower()
        short = t.get("shortName", "").lower()
        if q in name or q in loc or q in short or loc in q or short in q:
            return t["id"]
    return 0


async def get_player_h2h_stats(
    player_name: str,
    opp_name: str,
    season: int,
    group: str = "pitching",
    player_statsapi_id: int = 0,
) -> Optional[dict]:
    """Fetch a player's head-to-head aggregate stats vs a specific opposing team.

    Tries the current season first (more recent signal); falls back to
    career-cumulative when the seasonal sample is too small (< 2 games).

    Returns a dict with:
        gamesPlayed  — how many games in the H2H sample
        source       — "season" | "career"
        rawStat      — the raw stat dict from StatsAPI (keys = StatsAPI field names)
        oppTeamId    — resolved MLB Stats API team ID
    Returns None when the opponent cannot be resolved or no H2H data exists.
    """
    if not opp_name or not (player_name or player_statsapi_id):
        return None

    # Resolve opposing team ID
    opp_team_id = await _resolve_opp_team_id(opp_name)
    if not opp_team_id:
        log.debug(f"[H2H] Could not resolve team ID for opponent '{opp_name}'")
        return None

    # Resolve player's StatsAPI ID (skip search when caller already knows it)
    sa_id = player_statsapi_id
    if not sa_id and player_name:
        candidates = await _statsapi_search_players(player_name, limit=3)
        if not candidates:
            log.debug(f"[H2H] StatsAPI player not found: {player_name}")
            return None
        sa_id = candidates[0]["id"]

    async def _fetch_vsTeam(with_season: bool) -> Optional[dict]:
        """Fetch vsTeam aggregate stats. MLB Stats API only supports the overall
        vsTeam type — vsTeamHome/vsTeamAway return 400, so we use one request."""
        params: dict = {"stats": "vsTeam", "group": group, "opposingTeamId": opp_team_id}
        if with_season:
            params["season"] = season
        try:
            data = await _statsapi_get(f"/people/{sa_id}/stats", params)
        except Exception:
            return None
        splits: list = []
        for sb in data.get("stats", []):
            splits.extend(sb.get("splits", []))
        if not splits:
            return None
        st = splits[0].get("stat", {})
        gp = int(st.get("gamesPlayed") or st.get("gamesPitched") or 0)
        if gp < 1:
            return None
        return {"gamesPlayed": gp,
                "source": "season" if with_season else "career",
                "rawStat": st}

    # Fetch current-season AND career in parallel; prefer season if it has data
    import asyncio as _aio
    season_data, career_data = await _aio.gather(
        _fetch_vsTeam(True),
        _fetch_vsTeam(False),
        return_exceptions=True,
    )
    def _safe(x): return x if isinstance(x, dict) else None

    best = _safe(season_data) if (_safe(season_data) or {}).get("gamesPlayed", 0) >= 1 \
           else _safe(career_data)
    if not best:
        log.debug(f"[H2H] No data for {player_name} vs {opp_name}")
        return None

    gp = best["gamesPlayed"]
    log.info(f"[H2H] {player_name} vs {opp_name} ({best['source']}): "
             f"{gp} games, teamId={opp_team_id}, sa_id={sa_id}")

    return {
        "gamesPlayed": gp,
        "source":      best["source"],
        "rawStat":     best["rawStat"],
        "oppTeamId":   opp_team_id,
        # MLB Stats API does not expose per-venue vsTeam splits (vsTeamHome/Away → 400).
        # Venue-awareness is handled at the engine level by combining the overall H2H
        # with the existing venue multiplier (Layer 3) that is already applied upstream.
        "homeSplit":   None,
        "awaySplit":   None,
    }


async def search_players(query: str, limit: int = 15) -> list:
    """Search BDL for players by name.

    BDL's /players?search= only matches on a single token — multi-word queries
    like "Noah Cameron" return 0 results even though the player exists.  We work
    around this by trying the full query first, then falling back to last-name-only
    and first-name-only searches, deduplicating by player id.

    Falls back to MLB Stats API (free, no auth) when BDL has no record of the player.
    """
    q = query.strip()
    key = f"mlb_ps:{q.lower()}"
    doc = await _cache_get(key)
    if _cache_fresh(doc, CACHE_TTL["player_search"]) and doc.get("data") is not None:
        return doc["data"]

    seen: dict = {}  # id → player

    async def _search(term: str) -> list:
        try:
            r = await _get("/players", {"search": term, "per_page": limit})
            return r.get("data", [])
        except Exception:
            return []

    # 1. Try full query
    for p in await _search(q):
        seen[p["id"]] = p

    # 2. ONLY if nothing found and query has multiple words, try last name then first name.
    #    Do NOT do a supplemental second call when the first call already returned results —
    #    that was doubling every multi-word search and causing a huge rate-limit queue.
    words = q.split()
    if not seen and len(words) > 1:
        for p in await _search(words[-1]):
            seen[p["id"]] = p
        if not seen:
            for p in await _search(words[0]):
                seen[p["id"]] = p

    players = list(seen.values())[:limit]

    # 3. MLB Stats API fallback — for young/recently-called-up players BDL may not
    #    have yet (e.g. Cole Young, Sal Stewart). IDs from statsapi are large (≥500000)
    #    so they never collide with BDL IDs.
    #
    #    Trigger condition: no players found at all, OR for multi-word queries where
    #    none of the BDL results contain ALL query words in the full_name — this
    #    handles "cole young" returning 15 Youngs but no Cole Young.
    q_words = q.lower().split()
    bdl_has_full_match = any(
        all(w in (p.get("full_name") or "").lower() for w in q_words)
        for p in players
    )
    # Also run statsapi search when BDL returned large IDs (≥ _STATSAPI_ID_THRESHOLD).
    # BDL assigns IDs > 100k to some players (e.g. Andrew Painter = 4668116), but
    # those IDs are BDL-internal and invalid for MLB Stats API endpoints.
    # statsapi will return the correct low-range ID (e.g. 691725) for such players.
    bdl_has_large_id = any(p.get("id", 0) >= _STATSAPI_ID_THRESHOLD for p in players)
    if not players or (len(q_words) > 1 and not bdl_has_full_match) or bdl_has_large_id:
        statsapi_players = await _statsapi_search_players(q, limit)
        if statsapi_players:
            # Prepend statsapi results (exact matches) ahead of BDL partials
            existing_ids = {p["id"] for p in players}
            for p in statsapi_players:
                if p["id"] not in existing_ids:
                    players.insert(0, p)
                    existing_ids.add(p["id"])
            players = players[:limit]

    # Normalize: always populate full_name from first+last if BDL left it null
    for p in players:
        if not p.get("full_name"):
            fn = (p.get("first_name") or "").strip()
            ln = (p.get("last_name") or "").strip()
            name = f"{fn} {ln}".strip()
            if name:
                p["full_name"] = name

    if players:
        await _cache_set(key, players)
    return players


async def get_player(player_id: int) -> Optional[dict]:
    if player_id >= _STATSAPI_ID_THRESHOLD:
        return await _statsapi_get_player(player_id)
    key = f"mlb_p:{player_id}"
    doc = await _cache_get(key)
    if _cache_fresh(doc, CACHE_TTL["player"]) and doc.get("data") is not None:
        return doc["data"]
    try:
        result = await _get(f"/players/{player_id}")
        data = result.get("data")
    except Exception:
        return None
    if data:
        await _cache_set(key, data)
    return data


async def get_teams() -> list:
    doc = await _cache_get("mlb_teams")
    if _cache_fresh(doc, CACHE_TTL["teams"]) and doc.get("data"):
        return doc["data"]
    result = await _get("/teams")
    teams = result.get("data", [])
    await _cache_set("mlb_teams", teams)
    return teams


async def get_bdl_team_id_for_statsapi(statsapi_team_id: int, season: int = 2026) -> int:
    """
    Resolve a BDL team ID (1-30) from a Stats API team ID (100+).

    Stats API and BDL use different team ID spaces.  Picks store Stats API
    team IDs; BDL's game/stats endpoints use BDL IDs.  This function
    cross-references by team abbreviation (e.g. TOR → Blue Jays BDL id).

    Result is cached 24 h to avoid repeated cross-API lookups.
    """
    cache_key = f"mlb_bdl_tid:{statsapi_team_id}"
    doc = await _cache_get(cache_key)
    if _cache_fresh(doc, 86400) and doc.get("data") is not None:
        return int(doc["data"])

    # Step 1: get the abbreviation from Stats API
    try:
        teams_data = await _statsapi_get("/teams", {"sportId": 1, "season": season})
        abbr = ""
        for t in teams_data.get("teams", []):
            if t.get("id") == statsapi_team_id:
                abbr = (t.get("abbreviation") or "").upper()
                break
    except Exception as _e:
        log.debug(f"[MLB CLIENT] statsapi team lookup failed for {statsapi_team_id}: {_e}")
        return 0

    if not abbr:
        return 0

    # Step 2: match by abbreviation in BDL teams list
    try:
        bdl_teams = await get_teams()
        for t in bdl_teams:
            if (t.get("abbreviation") or "").upper() == abbr:
                bdl_id = int(t["id"])
                await _cache_set(cache_key, bdl_id)
                return bdl_id
    except Exception as _e:
        log.debug(f"[MLB CLIENT] BDL team lookup failed for abbr={abbr}: {_e}")

    return 0


async def get_player_game_logs(player_id: int, season: int = 2026, limit: int = 30) -> list:
    """Per-game stats, newest first (API returns newest first via cursor pagination).

    For MLB Stats API players (id ≥ 100 000) we try hitting logs first, then
    pitching logs if hitting comes back empty (pitcher check).
    """
    if player_id >= _STATSAPI_ID_THRESHOLD:
        logs = await _statsapi_game_logs(player_id, season, group="hitting")
        if not logs:
            logs = await _statsapi_game_logs(player_id, season, group="pitching")
        return logs[:limit]

    key = f"mlb_gl3:{player_id}:{season}"
    doc = await _cache_get(key)
    if _cache_fresh(doc, CACHE_TTL["stats"]) and doc.get("data") is not None:
        return doc["data"]

    params = {"player_ids[]": player_id, "season": season, "per_page": min(limit, 50)}
    result = await _get("/stats", params)
    logs = result.get("data", [])

    # Paginate to get more if needed (up to limit)
    cursor = result.get("meta", {}).get("next_cursor")
    while cursor and len(logs) < limit:
        r2 = await _get("/stats", {**params, "cursor": cursor})
        logs.extend(r2.get("data", []))
        cursor = r2.get("meta", {}).get("next_cursor")

    logs = logs[:limit]
    # Normalise BDL entries to the same field schema as _statsapi_game_logs
    # so _try_settle_mlb and the live loop use a single code path.
    logs = [_transform_bdl_log(l) for l in logs]
    await _cache_set(key, logs)
    return logs


async def get_season_stats(player_id: int, season: int = 2026) -> Optional[dict]:
    """Season aggregate stats (regular season only).

    For MLB Stats API players (id ≥ 100 000) we try hitting first, then pitching.
    """
    if player_id >= _STATSAPI_ID_THRESHOLD:
        data = await _statsapi_season_stats(player_id, season, group="hitting")
        if not data:
            data = await _statsapi_season_stats(player_id, season, group="pitching")
        return data

    key = f"mlb_ss:{player_id}:{season}"
    doc = await _cache_get(key)
    if _cache_fresh(doc, CACHE_TTL["season_stats"]) and doc.get("data") is not None:
        return doc["data"]
    try:
        result = await _get("/season_stats", {"player_ids[]": player_id, "season": season})
    except Exception:
        return None
    records = result.get("data", [])
    reg = [r for r in records if r.get("season_type") == "regular"]
    data = reg[0] if reg else (records[0] if records else None)
    if data is not None:
        await _cache_set(key, data)
    return data


async def get_team_games(team_id: int, season: int = 2026) -> list:
    """Fetch completed regular-season games for a team, newest first.
    Used to enrich per-game stat tiles with opponent/date/venue/score.
    Cached 15 minutes — refreshes quickly during active season."""
    if not team_id:
        return []
    key = f"mlb_tg:{team_id}:{season}"
    doc = await _cache_get(key)
    if _cache_fresh(doc, 900) and doc.get("data") is not None:
        return doc["data"]

    all_games: list = []
    try:
        cursor = None
        for _ in range(4):  # up to 400 games — covers a full season
            params: dict = {"team_ids[]": team_id, "season": season, "per_page": 100}
            if cursor:
                params["cursor"] = cursor
            result = await _get("/games", params)
            batch = result.get("data", [])
            all_games.extend(batch)
            cursor = result.get("meta", {}).get("next_cursor")
            if not cursor or not batch:
                break
    except Exception as e:
        log.warning(f"[MLB CLIENT] get_team_games({team_id},{season}) failed: {e}")
        return []

    # Keep only completed regular-season games, sorted newest first
    regular = [
        g for g in all_games
        if g.get("season_type") in ("regular", None, "")
        and g.get("status") == "STATUS_FINAL"
    ]
    regular.sort(key=lambda g: g.get("date", ""), reverse=True)

    if regular:
        await _cache_set(key, regular)
    return regular


async def get_today_and_live_games(team_id: int, season: int = 2026) -> list:
    """Fetch today's and in-progress games for a team.
    Uses a 2-minute cache so the live-tracking loop stays fresh without hammering BDL.

    BDL returns games oldest-first, so we MUST use the dates[] filter to target
    today specifically — otherwise per_page=10 would give April games, not May.
    We also scan the most-recent page of games for any STATUS_IN_PROGRESS game
    as a safety net (in case a game runs past midnight UTC)."""
    if not team_id:
        return []
    from datetime import date as _date
    today = _date.today().isoformat()
    key = f"mlb_live:{team_id}:{season}:{today}"
    doc = await _cache_get(key)
    if _cache_fresh(doc, 120) and doc.get("data") is not None:
        return doc["data"]

    relevant: list = []
    try:
        # Primary: request only today's game by date
        result = await _get("/games", {
            "team_ids[]": team_id,
            "season": season,
            "per_page": 5,
            "dates[]": today,
        })
        for g in result.get("data", []):
            status = (g.get("status") or "").upper()
            gdate = (g.get("date") or "")[:10]
            if "IN_PROGRESS" in status or "LIVE" in status or gdate == today:
                relevant.append(g)
    except Exception as e:
        log.warning(f"[MLB CLIENT] get_today_and_live_games dates filter failed ({team_id}): {e}")

    # Fallback / safety net: get the last page of season games — catches live
    # games that started yesterday or any in-progress game BDL didn't date-match.
    if not relevant:
        try:
            # Paginate to the most recent games (BDL is oldest-first, so we
            # follow cursors until the last page which has the newest games)
            cursor = None
            last_batch: list = []
            for _ in range(20):  # max 20 pages × 10 = 200 games
                params: dict = {"team_ids[]": team_id, "season": season, "per_page": 10}
                if cursor:
                    params["cursor"] = cursor
                r = await _get("/games", params)
                batch = r.get("data", [])
                if batch:
                    last_batch = batch
                next_cursor = r.get("meta", {}).get("next_cursor")
                if not next_cursor:
                    break
                cursor = next_cursor
            for g in last_batch:
                status = (g.get("status") or "").upper()
                gdate = (g.get("date") or "")[:10]
                if "IN_PROGRESS" in status or "LIVE" in status or gdate == today:
                    relevant.append(g)
        except Exception as e2:
            log.warning(f"[MLB CLIENT] get_today_and_live_games fallback failed ({team_id}): {e2}")

    # Only cache when no game is actively in progress — live games must not
    # be cached so the loop always sees the latest status and score.
    game_is_live = any("IN_PROGRESS" in (g.get("status") or "").upper() for g in relevant)
    if relevant and not game_is_live:
        await _cache_set(key, relevant)
    return relevant


async def get_game_odds(game_id: int) -> Optional[dict]:
    """Fetch betting odds for a game from BDL /odds and aggregate across vendors.

    Returns {gameTotal, moneylineHome, moneylineAway, spreadHome, vendorCount}
    using the median across vendors (fanduel/draftkings/caesars/betmgm/...)
    so a single stale book can't skew the number. None when no odds posted.
    Cached 30 minutes — totals move slowly pre-game.
    """
    if not game_id:
        return None
    key = f"mlb_odds:{game_id}"
    doc = await _cache_get(key)
    if _cache_fresh(doc, 1800):
        return doc.get("data")

    try:
        r = await _get("/odds", {"game_ids[]": game_id, "per_page": 50})
        rows = r.get("data") or []
    except Exception as e:
        log.warning(f"[MLB CLIENT] get_game_odds({game_id}) failed: {e}")
        return doc.get("data") if doc else None

    def _median(vals: list) -> Optional[float]:
        vals = sorted(v for v in vals if v is not None)
        if not vals:
            return None
        mid = len(vals) // 2
        return float(vals[mid]) if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2.0

    def _num(v):
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    totals     = [_num(row.get("total_value")) for row in rows]
    ml_home    = [_num(row.get("moneyline_home_odds")) for row in rows]
    ml_away    = [_num(row.get("moneyline_away_odds")) for row in rows]
    spread_h   = [_num(row.get("spread_home_value")) for row in rows]

    out = None
    total_med = _median(totals)
    if total_med is not None or _median(ml_home) is not None:
        out = {
            "gameTotal":     total_med,
            "moneylineHome": _median(ml_home),
            "moneylineAway": _median(ml_away),
            "spreadHome":    _median(spread_h),
            "vendorCount":   sum(1 for t in totals if t is not None),
        }
        log.info(f"[MLB CLIENT] odds game {game_id}: total={total_med} "
                 f"({out['vendorCount']} vendors)")
    await _cache_set(key, out)
    return out


async def get_game_player_stats(player_id: int, game_id: int, season: int = 2026,
                                live: bool = False) -> Optional[dict]:
    """Fetch a player's stats for a specific game.

    When `live=True` (game still in progress) we skip the cache entirely so
    every loop iteration reads the latest values from BDL.  Completed games
    are cached for 24 h (they won't change).
    """
    key = f"mlb_gps2:{player_id}:{game_id}"
    if not live:
        doc = await _cache_get(key)
        if _cache_fresh(doc, 86400) and doc.get("data") is not None:
            return doc["data"]
    try:
        result = await _get("/stats", {
            "player_ids[]": player_id,
            "game_ids[]": game_id,
            "season": season,
        })
        stats_list = result.get("data", [])
        data = stats_list[0] if stats_list else None
    except Exception as e:
        log.warning(f"[MLB CLIENT] get_game_player_stats({player_id},{game_id}) failed: {e}")
        return None
    # Only cache completed-game stats — live stats must never be cached
    if data is not None and not live:
        await _cache_set(key, data)
    # Normalise to Stats-API field names so live-loop can use a single lookup path
    if data is not None:
        data = _transform_bdl_log(data)
    return data


async def get_game_by_teams(home_abbrev: str, away_abbrev: str, season: int = 2026) -> Optional[dict]:
    """Find a specific game by team abbreviations (used for settlement)."""
    try:
        result = await _get("/games", {"season": season, "per_page": 50})
        for g in result.get("data", []):
            h = g.get("home_team", {}).get("abbreviation", "")
            a = g.get("away_team", {}).get("abbreviation", "")
            if (h == home_abbrev and a == away_abbrev) or (h == away_abbrev and a == home_abbrev):
                return g
    except Exception:
        pass
    return None


async def get_game_context(
    team_name: str = "",
    team_abbr: str = "",
    player_id: int = 0,
    season: int = 2026,
) -> dict:
    """
    Fetch today's game context from MLB Stats API:
    - Probable opponent pitcher (name, hand L/R, season ERA)
    - Player lineup spot (if lineup posted ~2h before game)
    Cached 10 minutes.
    """
    from datetime import date as _date
    today = _date.today().isoformat()
    cache_key = f"mlb_game_ctx:{team_name}:{team_abbr}:{player_id}:{today}"
    doc = await _cache_get(cache_key)
    if _cache_fresh(doc, 600) and doc.get("data") is not None:
        return doc["data"]

    # ── 1. Resolve Stats API team ID ─────────────────────────────────────────
    teams_data = await _statsapi_get("/teams", {"sportId": 1, "season": season})
    stats_team_id = None
    tn_lower = team_name.lower()
    ta_upper = team_abbr.upper()

    for t in teams_data.get("teams", []):
        t_abbr = t.get("abbreviation", "").upper()
        t_loc  = t.get("locationName", "").lower()
        t_team = t.get("teamName", "").lower()
        full   = f"{t_loc} {t_team}".strip()

        if ta_upper and t_abbr == ta_upper:
            stats_team_id = t["id"]
            break
        if tn_lower and (
            tn_lower in full or full in tn_lower or
            t_team in tn_lower or tn_lower in t_team
        ):
            stats_team_id = t["id"]
            break

    if not stats_team_id:
        return {"error": "Team not found", "probablePitcher": None, "lineupSpot": None}

    # ── 2. Today's schedule with probable pitchers + lineups ─────────────────
    schedule = await _statsapi_get("/schedule", {
        "sportId": 1,
        "teamId":  stats_team_id,
        "date":    today,
        "hydrate": "probablePitcher,lineups",
    })

    all_games = []
    for d in schedule.get("dates", []):
        all_games.extend(d.get("games", []))

    if not all_games:
        result = {
            "message": "No game scheduled today",
            "probablePitcher": None,
            "lineupSpot": None,
            "isHome": None,
            "opponentTeam": "",
        }
        await _cache_set(cache_key, result)
        return result

    game = all_games[0]
    home_info = game.get("teams", {}).get("home", {})
    away_info = game.get("teams", {}).get("away", {})
    is_home   = (home_info.get("team", {}).get("id") == stats_team_id)
    our_info  = home_info if is_home else away_info
    opp_info  = away_info if is_home else home_info

    # ── 3. Probable pitcher (opponent's starter) ──────────────────────────────
    prob_pitcher = opp_info.get("probablePitcher") or {}
    pitcher_result = None

    if prob_pitcher.get("id"):
        pitcher_id = prob_pitcher["id"]
        p_data = await _statsapi_get(f"/people/{pitcher_id}", {
            "hydrate": f"stats(group=pitching,type=season,season={season})",
        })
        person = (p_data.get("people") or [{}])[0]
        pitch_hand = (person.get("pitchHand") or {}).get("code", "")

        era = None
        for sb in person.get("stats", []):
            for split in sb.get("splits", []):
                era_raw = split.get("stat", {}).get("era")
                if era_raw and era_raw not in ("-.--", "0.00", ""):
                    try:
                        era = float(era_raw)
                    except Exception:
                        pass

        pitcher_result = {
            "name": prob_pitcher.get("fullName") or person.get("fullName", ""),
            "id":   pitcher_id,
            "hand": pitch_hand,
            "era":  era,
        }

    # ── 4. Lineup spot ────────────────────────────────────────────────────────
    lineup_spot = None
    lineups = game.get("lineups") or {}
    batters_key = "homeTeamBatters" if is_home else "awayTeamBatters"
    batters = lineups.get(batters_key, [])
    if player_id and batters:
        for i, b in enumerate(batters, start=1):
            b_id = b.get("id") if isinstance(b, dict) else b
            if b_id == player_id:
                lineup_spot = i
                break

    result = {
        "probablePitcher": pitcher_result,
        "lineupSpot":      lineup_spot,
        "isHome":          is_home,
        "opponentTeam":    (opp_info.get("team") or {}).get("name", ""),
        "gameDate":        today,
    }
    await _cache_set(cache_key, result)
    return result


async def _statsapi_schedule_next_game(team_id: int, today_str: str) -> dict:
    """Use the MLB Stats API schedule to find the next upcoming regular-season game
    for a Stats API team.  Called instead of BDL /games for Stats API players because
    BDL returns old/wrong season data for those players."""
    from datetime import timedelta
    end_str = (date.today() + timedelta(days=30)).isoformat()
    data = await _statsapi_get("/schedule", {
        "sportId":   1,
        "teamId":    team_id,
        "startDate": today_str,
        "endDate":   end_str,
        "gameType":  "R",
    })
    for date_entry in data.get("dates", []):
        if (date_entry.get("date") or "") < today_str:
            continue  # skip any past-date entries the API may return
        for game in date_entry.get("games", []):
            state = (game.get("status") or {}).get("abstractGameState", "")
            if state in ("Final", "Game Over"):
                continue
            teams = game.get("teams") or {}
            home_team = teams.get("home", {}).get("team", {})
            away_team = teams.get("away", {}).get("team", {})
            is_home = home_team.get("id") == team_id
            opp = away_team if is_home else home_team
            return {
                "found":  True,
                "gameId": game.get("gamePk"),
                "date":   date_entry.get("date"),
                "venue":  "home" if is_home else "away",
                "opponent": {
                    "id":           opp.get("id"),
                    "name":         opp.get("name") or "",
                    "abbreviation": opp.get("abbreviation") or "",
                },
            }
    return {"found": False}


async def get_player_next_match(player_id: int, season: int = 2026) -> dict:
    """Get the next upcoming MLB game for a player's team.
    Returns {found, gameId, date, venue, opponent} or {found: False}.
    Cache is bypassed if the stored date is before today.
    """
    today_str = date.today().isoformat()
    cache_key = f"mlb_next_player:{player_id}"
    cached = await _cache_get(cache_key)
    if cached:
        stored = cached.get("data", {})
        if stored.get("found") and (stored.get("date", "") or "") >= today_str:
            return stored
        if not stored.get("found") and cached.get("ts", ""):
            try:
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(str(cached["ts"])).replace(tzinfo=timezone.utc)).total_seconds()
                if age < 900:
                    return stored
            except Exception:
                pass

    player = await get_player(player_id)
    if not player:
        return {"found": False}
    team = player.get("team") or {}
    team_id = team.get("id")
    if not team_id:
        return {"found": False}

    # Stats API player IDs (>= 100k): BDL's /games endpoint returns stale/wrong
    # season data for these players.  Use the MLB Stats API schedule instead.
    if player_id >= _STATSAPI_ID_THRESHOLD:
        result = await _statsapi_schedule_next_game(team_id, today_str)
        log.info(f"[MLB NEXT MATCH] statsapi player={player_id} → {result}")
        await _cache_set(cache_key, result)
        return result

    try:
        data = await _get("/games", {
            "team_ids[]": team_id,
            "season":     season,
            "start_date": today_str,
            "per_page":   5,
        })
    except Exception as e:
        log.warning(f"[MLB NEXT MATCH] player={player_id}: {e}")
        return {"found": False}

    games = data.get("data", [])
    future = [g for g in games
              if (g.get("date") or "")[:10] >= today_str
              and "FINAL" not in (g.get("status") or "").upper()]
    future.sort(key=lambda g: g.get("date", ""))

    if not future:
        # BDL schedule data is stale for some teams (returns year-2000 dates etc.).
        # Fall back: look up team via MLB Stats API teams list, then fetch schedule.
        team_obj   = player.get("team") or {}
        team_name  = (team_obj.get("display_name") or team_obj.get("full_name")
                      or team_obj.get("name") or "")
        player_name = (player.get("full_name")
                       or f"{player.get('first_name') or ''} {player.get('last_name') or ''}".strip())
        sa_team_id: Optional[int] = None
        try:
            if team_name:
                # Resolve StatsAPI team ID from team display name
                sa_teams_data = await _statsapi_get("/teams", {"sportId": 1, "season": 2026})
                for t in sa_teams_data.get("teams", []):
                    full = f"{t.get('locationName','')} {t.get('teamName','')}".strip()
                    if team_name.lower() in full.lower() or full.lower() in team_name.lower():
                        sa_team_id = t.get("id")
                        break
            if not sa_team_id and player_name:
                # Fallback: search by player name to get their StatsAPI team ID
                sa_players = await _statsapi_search_players(player_name, limit=3)
                if sa_players:
                    sa_team_id = (sa_players[0].get("team") or {}).get("id")
            if sa_team_id:
                result = await _statsapi_schedule_next_game(sa_team_id, today_str)
                if result.get("found"):
                    log.info(f"[MLB NEXT MATCH] StatsAPI fallback team={team_name!r} id={sa_team_id} → {result}")
                    await _cache_set(cache_key, result)
                    return result
        except Exception as _fe:
            log.warning(f"[MLB NEXT MATCH] StatsAPI fallback failed ({team_name!r}): {_fe}")
        result = {"found": False}
        await _cache_set(cache_key, result)
        return result

    g       = future[0]
    home_t  = g.get("home_team") or {}
    away_t  = g.get("away_team") or {}
    is_home = home_t.get("id") == team_id
    opp     = away_t if is_home else home_t

    result = {
        "found":    True,
        "gameId":   g.get("id"),
        "date":     (g.get("date") or "")[:10],
        "venue":    "home" if is_home else "away",
        "opponent": {
            "id":   opp.get("id"),
            "name": opp.get("full_name") or opp.get("name") or "",
            "abbreviation": opp.get("abbreviation") or "",
        },
    }
    log.info(f"[MLB NEXT MATCH] player={player_id} → {result['date']} vs {result['opponent']['name']} ({result['venue']})")
    await _cache_set(cache_key, result)
    return result
