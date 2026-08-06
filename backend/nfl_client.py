"""
BallDontLie NFL API client with rate-limiting and MongoDB caching.
Base URL: https://api.balldontlie.io/nfl/v1
Elite tier: 600 req/min.

NFL stat fields per game row:
  passing: passing_completions, passing_attempts, passing_yards, passing_touchdowns, passing_interceptions, qb_rating, sacks
  rushing: rushing_attempts, rushing_yards, rushing_touchdowns
  receiving: receptions, receiving_yards, receiving_touchdowns, targets
"""
import asyncio
import time
import os
import logging
from datetime import datetime, timezone
from typing import Optional

import httpx
from config import db

log = logging.getLogger("nfl_client")

NFL_API_BASE = "https://api.balldontlie.io/nfl/v1"
NFL_API_KEY  = os.environ.get("MLB_BDL_API_KEY", "")

_rate_sem = asyncio.Semaphore(2)   # shared BDL key — keep burst low
_last_req_time: float = 0.0
_MIN_INTERVAL = 0.25               # max ~4 req/s from this client
_rate_limited_until: float = 0.0
_RATE_LIMIT_COOLDOWN = 30.0        # fail fast while BDL is throttling us
_RATE_LIMIT_MESSAGE = "NFL API rate limited"

CACHE_TTL = {
    "teams":         7 * 86400,
    "player":        2 * 3600,
    "player_search": 4 * 3600,
    "stats":         2 * 3600,
}

# NFL seasons follow the calendar year in the provider.  Keeping this dynamic
# matters in the offseason: a hard-coded prior season makes valid upcoming
# schedules look like "no next game" and also sends prediction requests to
# stale stat buckets.
CURRENT_NFL_SEASON = int(os.environ.get("NFL_SEASON", str(datetime.now(timezone.utc).year)))


async def _get(path: str, params: dict = None) -> dict:
    global _last_req_time, _rate_limited_until
    headers = {"Authorization": NFL_API_KEY}
    url = f"{NFL_API_BASE}{path}"

    async with _rate_sem:
        if time.monotonic() < _rate_limited_until:
            raise RuntimeError(_RATE_LIMIT_MESSAGE)
        elapsed = time.monotonic() - _last_req_time
        if elapsed < _MIN_INTERVAL:
            await asyncio.sleep(_MIN_INTERVAL - elapsed)
        try:
            async with httpx.AsyncClient(timeout=20) as c:
                resp = await c.get(url, headers=headers, params=params or {})
        except Exception as e:
            raise RuntimeError(f"NFL API network error: {e}")
        finally:
            _last_req_time = time.monotonic()

        if resp.status_code != 429:
            if resp.status_code >= 400:
                raise RuntimeError(f"NFL API error {resp.status_code}: {resp.text[:200]}")
            return resp.json()

        # Do not sleep and retry here. Search-as-you-type can have several
        # requests in flight, and sleeping each one creates a retry storm that
        # keeps the provider throttled for longer. Search routes can fall back
        # to the local player index immediately instead.
        _rate_limited_until = time.monotonic() + _RATE_LIMIT_COOLDOWN
        retry_after = resp.headers.get("retry-after", "?")
        log.warning(
            f"[NFL CLIENT] 429 on {path} — failing fast for "
            f"{_RATE_LIMIT_COOLDOWN:.0f}s (provider retry-after={retry_after})"
        )
        raise RuntimeError(_RATE_LIMIT_MESSAGE)


def _cache_fresh(doc: Optional[dict], ttl_seconds: int) -> bool:
    if not doc:
        return False
    ts = doc.get("ts", "")
    if not ts:
        return False
    try:
        age = (datetime.now(timezone.utc) -
               datetime.fromisoformat(str(ts)).replace(tzinfo=timezone.utc)).total_seconds()
        return age < ttl_seconds
    except Exception:
        return False


async def _cache_get(key: str) -> Optional[dict]:
    try:
        return await db.nfl_cache.find_one({"key": key}, {"_id": 0})
    except Exception:
        return None


async def _cache_set(key: str, data) -> None:
    try:
        await db.nfl_cache.update_one(
            {"key": key},
            {"$set": {"key": key, "data": data, "ts": datetime.now(timezone.utc).isoformat()}},
            upsert=True,
        )
    except Exception:
        pass


_player_index: dict[int, dict] = {}
_player_index_loaded = False


def _normalise_player(player: dict) -> dict:
    """Give cached and provider player records one consistent shape."""
    p = dict(player or {})
    if not p.get("full_name"):
        p["full_name"] = f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()
    return p


async def _load_cached_player_index() -> None:
    """Load previously resolved players once after a process restart.

    Search results are also written as player:<id> records below. This makes
    the fallback durable across VM restarts instead of relying only on the
    in-memory index.
    """
    global _player_index_loaded
    if _player_index_loaded:
        return
    _player_index_loaded = True
    try:
        cursor = db.nfl_cache.find(
            {"key": {"$regex": r"^(player:|search3:)"}},
            {"_id": 0, "data": 1},
        )
        async for doc in cursor:
            data = doc.get("data")
            candidates = data if isinstance(data, list) else [data]
            for candidate in candidates:
                player = _normalise_player(candidate or {})
                if player.get("id") is not None:
                    _player_index[int(player["id"])] = player
    except Exception as e:
        log.debug(f"[NFL CACHE] player index load skipped: {e}")


def _local_player_search(query: str, limit: int) -> list:
    """Search resolved NFL players without making a provider request."""
    q = " ".join((query or "").lower().split())
    if len(q) < 2:
        return []
    tokens = q.split()
    matches = []
    for player in _player_index.values():
        name = " ".join(
            str(player.get("full_name") or
                f"{player.get('first_name', '')} {player.get('last_name', '')}").lower().split()
        )
        if all(token in name for token in tokens):
            # Exact/full-prefix matches should beat a surname-only match.
            exact = name == q
            prefix = name.startswith(q)
            token_score = sum(name.startswith(token) for token in tokens)
            matches.append((0 if exact else 1, 0 if prefix else 1, -token_score, name, player))
    matches.sort(key=lambda row: row[:4])
    return [row[4] for row in matches[:limit]]


async def search_players(query: str, limit: int = 15) -> list:
    query = " ".join((query or "").strip().split())
    # search3: prefix busts stale caches poisoned by old-key 429 storms
    cache_key = f"search3:{query.lower()}"
    cached = await _cache_get(cache_key)
    if _cache_fresh(cached, CACHE_TTL["player_search"]):
        rows = [_normalise_player(p) for p in (cached.get("data") or [])]
        for player in rows:
            if player.get("id") is not None:
                _player_index[int(player["id"])] = player
        return rows[:limit]

    await _load_cached_player_index()
    local_results = _local_player_search(query, limit)
    if local_results:
        # A local hit is authoritative for identity and avoids spending a
        # provider call on every partial keystroke.
        return local_results

    results = []
    cursor = None
    for _ in range(3):
        params = {"search": query, "per_page": 25}
        if cursor:
            params["cursor"] = cursor
        try:
            data = await _get("/players", params)
        except Exception as e:
            log.warning(f"[NFL SEARCH] {e}")
            break
        rows = data.get("data", [])
        # Synthesise full_name for NFL (BDL returns first_name + last_name only)
        for p in rows:
            p = _normalise_player(p)
            if p.get("id") is not None:
                _player_index[int(p["id"])] = p
        results.extend(rows)
        meta = data.get("meta", {})
        cursor = meta.get("next_cursor")
        if not cursor or len(results) >= limit:
            break

    # BDL search only matches single name tokens — fall back to last name
    if not results and " " in query:
        last_name = query.rsplit(" ", 1)[-1]
        try:
            data = await _get("/players", {"search": last_name, "per_page": 25})
        except Exception as e:
            log.warning(f"[NFL SEARCH fallback] {e}")
        else:
            rows = data.get("data", [])
            rows = [_normalise_player(p) for p in rows]
            for p in rows:
                if p.get("id") is not None:
                    _player_index[int(p["id"])] = p
            # Sort by how many original query tokens appear in the player name
            q_tokens = query.lower().split()
            rows.sort(key=lambda p: sum(1 for t in q_tokens if t in p.get("full_name","").lower()), reverse=True)
            results = rows

    # Only cache non-empty results — transient 429 must not poison cache
    if results:
        results = [_normalise_player(p) for p in results]
        for player in results:
            if player.get("id") is not None:
                _player_index[int(player["id"])] = player
                await _cache_set(f"player:{player['id']}", player)
        await _cache_set(cache_key, results[:limit])
    return results[:limit]


async def get_player(player_id: int) -> Optional[dict]:
    await _load_cached_player_index()
    cache_key = f"player:{player_id}"
    cached = await _cache_get(cache_key)
    if _cache_fresh(cached, CACHE_TTL["player"]):
        return cached["data"]
    try:
        data = await _get(f"/players/{player_id}")
        player = data.get("data", {})
        player = _normalise_player(player)
        if player.get("id") is not None:
            _player_index[int(player["id"])] = player
        await _cache_set(cache_key, player)
        return player
    except Exception as e:
        log.warning(f"[NFL PLAYER] {e}")
        return None


async def get_teams() -> list:
    cache_key = "teams:all"
    cached = await _cache_get(cache_key)
    if _cache_fresh(cached, CACHE_TTL["teams"]):
        return cached["data"]
    try:
        data = await _get("/teams", {"per_page": 100})
        teams = data.get("data", [])
        await _cache_set(cache_key, teams)
        return teams
    except Exception as e:
        log.warning(f"[NFL TEAMS] {e}")
        return []


def _transform_nfl_log(row: dict) -> dict:
    """Normalise a raw BDL NFL /stats row to a unified schema."""
    game = row.get("game") or {}
    date_str = (game.get("date") or "")[:10]
    home_team    = game.get("home_team") or {}
    visitor_team = game.get("visitor_team") or {}
    home_team_id   = home_team.get("id")
    player_team_id = (row.get("team") or {}).get("id")
    is_home = player_team_id == home_team_id
    venue   = "home" if is_home else "away"

    # Opponent abbreviation / name
    opp_team = visitor_team if is_home else home_team
    opp_abbr = opp_team.get("abbreviation") or opp_team.get("name") or None

    # W/L from game scores
    home_sc = game.get("home_team_score")
    vis_sc  = game.get("visitor_team_score")
    score = f"{home_sc}-{vis_sc}" if home_sc is not None and vis_sc is not None else None
    won = None
    if home_sc is not None and vis_sc is not None:
        won = (home_sc > vis_sc) if is_home else (vis_sc > home_sc)

    pc   = (row.get("passing_completions") or 0)
    pa   = (row.get("passing_attempts") or 0)
    py   = (row.get("passing_yards") or 0)
    ptd  = (row.get("passing_touchdowns") or 0)
    pint = (row.get("passing_interceptions") or 0)
    sack = (row.get("sacks") or 0)
    qbr  = (row.get("qb_rating") or 0.0)
    ratt = (row.get("rushing_attempts") or 0)
    ry   = (row.get("rushing_yards") or 0)
    rtd  = (row.get("rushing_touchdowns") or 0)
    rec  = (row.get("receptions") or 0)
    recy = (row.get("receiving_yards") or 0)
    retd = (row.get("receiving_touchdowns") or 0)
    tgt  = (row.get("targets") or 0)
    lng_rush = (row.get("long_rushing") or 0)
    lng_recv = (row.get("long_reception") or 0)

    # DraftKings NFL fantasy: pass_yd/25 + pass_td*4 - int*1 + rush_yd/10 + rush_td*6 + rec*1 + recv_yd/10 + recv_td*6 - fum*2
    fantasy = (py / 25.0) + (ptd * 4) - (pint * 1) + (ry / 10.0) + (rtd * 6) + (rec * 1.0) + (recy / 10.0) + (retd * 6)

    return {
        "date":                date_str,
        "game_id":             game.get("id"),
        "venue":               venue,
        "opponent":            opp_abbr,
        "score":               score,
        "won":                 won,
        "week":                game.get("week"),
        "season":              game.get("season"),
        # Passing
        "passing_completions": pc,
        "passing_attempts":    pa,
        "passing_yards":       py,
        "passing_tds":         ptd,
        "interceptions":       pint,
        "sacks":               sack,
        "qb_rating":           qbr,
        "completion_pct":      round(pc / pa, 3) if pa > 0 else 0.0,
        # Rushing
        "carries":             ratt,
        "rushing_yards":       ry,
        "rushing_tds":         rtd,
        "long_rushing":        lng_rush,
        # Receiving
        "receptions":          rec,
        "receiving_yards":     recy,
        "receiving_tds":       retd,
        "targets":             tgt,
        "long_reception":      lng_recv,
        # Combos
        "passing_rushing_yards": py + ry,
        "anytime_td":          1 if (ptd + rtd + retd) > 0 else 0,
        "fantasy_pts":         round(fantasy, 1),
        "_source": "bdl",
    }


async def get_next_match(player_id: int) -> dict:
    """Find the next scheduled game for a given NFL player."""
    player = await get_player(player_id)
    if not player:
        return {"found": False}

    team = player.get("team") or {}
    team_id = team.get("id")
    if not team_id:
        return {"found": False}

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    games = []
    # In the offseason the provider can publish the next schedule under the
    # active season while the current season has no completed games yet.  Try
    # the active season and one forward season without treating a missing
    # active-season schedule as a terminal failure.
    for try_season in (CURRENT_NFL_SEASON, CURRENT_NFL_SEASON + 1):
        try:
            data = await _get("/games", {
                "team_ids[]": team_id,
                "seasons[]":  try_season,
                "per_page":   100,
            })
            games.extend(data.get("data", []))
        except Exception as e:
            log.warning(f"[NFL NEXT MATCH] season={try_season}: {e}")

    future = [
        g for g in games
        if (g.get("date") or "")[:10] >= today
        and g.get("status") not in ("Final", "completed", "closed", "complete")
    ]
    future.sort(key=lambda g: g.get("date", ""))

    if not future:
        return {"found": False}

    ng = future[0]
    home_team = ng.get("home_team") or {}
    away_team = ng.get("visitor_team") or {}
    is_home   = (home_team.get("id") == team_id)
    opp       = away_team if is_home else home_team

    return {
        "found":    True,
        "gameId":   ng.get("id"),
        "date":     (ng.get("date") or "")[:10],
        "venue":    "home" if is_home else "away",
        "opponent": {
            "id":           opp.get("id"),
            "name":         opp.get("full_name") or f"{opp.get('location', '')} {opp.get('name', '')}".strip(),
            "abbreviation": opp.get("abbreviation"),
        },
    }


async def get_player_game_logs(player_id: int, season: int = CURRENT_NFL_SEASON) -> list:
    """Fetch per-game stats for a player, newest-first."""
    cache_key = f"stats:{player_id}:{season}"
    cached = await _cache_get(cache_key)
    if _cache_fresh(cached, CACHE_TTL["stats"]):
        return cached["data"]

    all_rows = []
    cursor = None
    for _ in range(10):
        params = {"player_ids[]": player_id, "seasons[]": season, "per_page": 100}
        if cursor:
            params["cursor"] = cursor
        try:
            data = await _get("/stats", params)
        except Exception as e:
            log.warning(f"[NFL STATS] player={player_id}: {e}")
            break
        all_rows.extend(data.get("data", []))
        meta = data.get("meta", {})
        cursor = meta.get("next_cursor")
        if not cursor:
            break

    logs = [_transform_nfl_log(r) for r in all_rows]
    logs.sort(key=lambda x: x.get("date", ""), reverse=True)
    if logs:
        await _cache_set(cache_key, logs)
    return logs
