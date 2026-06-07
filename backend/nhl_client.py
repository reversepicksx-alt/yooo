"""
BallDontLie NHL API client with rate-limiting and MongoDB caching.
Base URL: https://api.balldontlie.io/nhl/v1
Elite tier: 600 req/min.

NHL seasons use format: "20232024" (start year + end year concatenated).
Player stats endpoint: /player_season_stats (season averages) and /games for schedule.
"""
import asyncio
import time
import os
import logging
from datetime import datetime, timezone
from typing import Optional

import httpx
from config import db

log = logging.getLogger("nhl_client")

NHL_API_BASE = "https://api.balldontlie.io/nhl/v1"
NHL_API_KEY  = os.environ.get("MLB_BDL_API_KEY", "")

_rate_sem = asyncio.Semaphore(2)   # shared BDL key — keep burst low
_last_req_time: float = 0.0
_MIN_INTERVAL = 0.25               # max ~4 req/s from this client

CACHE_TTL = {
    "teams":         7 * 86400,
    "player":        2 * 3600,
    "player_search": 4 * 3600,
    "stats":         2 * 3600,
    "season_stats":  6 * 3600,
}

# Current NHL season: "20242025"
CURRENT_NHL_SEASON = "20252026"


async def _get(path: str, params: dict = None) -> dict:
    global _last_req_time
    headers = {"Authorization": NHL_API_KEY}
    url = f"{NHL_API_BASE}{path}"

    async with _rate_sem:
        elapsed = time.monotonic() - _last_req_time
        if elapsed < _MIN_INTERVAL:
            await asyncio.sleep(_MIN_INTERVAL - elapsed)
        try:
            async with httpx.AsyncClient(timeout=20) as c:
                resp = await c.get(url, headers=headers, params=params or {})
        except Exception as e:
            raise RuntimeError(f"NHL API network error: {e}")
        finally:
            _last_req_time = time.monotonic()

        if resp.status_code != 429:
            if resp.status_code >= 400:
                raise RuntimeError(f"NHL API error {resp.status_code}: {resp.text[:200]}")
            return resp.json()

        retry_after = min(int(resp.headers.get("retry-after", "5")), 10)

    log.warning(f"[NHL CLIENT] 429 on {path} — waiting {retry_after}s")
    await asyncio.sleep(retry_after)

    async with _rate_sem:
        elapsed = time.monotonic() - _last_req_time
        if elapsed < _MIN_INTERVAL:
            await asyncio.sleep(_MIN_INTERVAL - elapsed)
        try:
            async with httpx.AsyncClient(timeout=20) as c:
                resp = await c.get(url, headers=headers, params=params or {})
        except Exception as e:
            raise RuntimeError(f"NHL API network error on retry: {e}")
        finally:
            _last_req_time = time.monotonic()

        if resp.status_code >= 400:
            raise RuntimeError(f"NHL API error {resp.status_code}: {resp.text[:200]}")
        return resp.json()


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
        return await db.nhl_cache.find_one({"key": key}, {"_id": 0})
    except Exception:
        return None


async def _cache_set(key: str, data) -> None:
    try:
        await db.nhl_cache.update_one(
            {"key": key},
            {"$set": {"key": key, "data": data, "ts": datetime.now(timezone.utc).isoformat()}},
            upsert=True,
        )
    except Exception:
        pass


async def search_players(query: str, limit: int = 15) -> list:
    cache_key = f"search:{query.lower()}"
    cached = await _cache_get(cache_key)
    if _cache_fresh(cached, CACHE_TTL["player_search"]):
        return cached["data"][:limit]

    results = []
    cursor = None
    for _ in range(3):
        params = {"search": query, "per_page": 25}
        if cursor:
            params["cursor"] = cursor
        try:
            data = await _get("/players", params)
        except Exception as e:
            log.warning(f"[NHL SEARCH] {e}")
            break
        results.extend(data.get("data", []))
        meta = data.get("meta", {})
        cursor = meta.get("next_cursor")
        if not cursor or len(results) >= limit:
            break

    await _cache_set(cache_key, results[:limit])
    return results[:limit]


async def get_player(player_id: int) -> Optional[dict]:
    cache_key = f"player:{player_id}"
    cached = await _cache_get(cache_key)
    if _cache_fresh(cached, CACHE_TTL["player"]):
        return cached["data"]
    try:
        # BDL NHL has no single-player endpoint — fetch via list filter
        data = await _get("/players", {"player_ids[]": player_id, "per_page": 1})
        players = data.get("data") or []
        player = players[0] if players else {}
        if player:
            await _cache_set(cache_key, player)
        return player or None
    except Exception as e:
        log.warning(f"[NHL PLAYER] {e}")
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
        log.warning(f"[NHL TEAMS] {e}")
        return []


async def get_player_season_stats(player_id: int, season: str = CURRENT_NHL_SEASON) -> dict:
    """Get season averages for a player. Season format: '20242025'."""
    cache_key = f"season_stats:{player_id}:{season}"
    cached = await _cache_get(cache_key)
    if _cache_fresh(cached, CACHE_TTL["season_stats"]):
        return cached["data"]
    try:
        data = await _get("/player_season_stats", {"player_ids[]": player_id, "seasons[]": season, "per_page": 1})
        stats = (data.get("data") or [{}])[0] if data.get("data") else {}
        await _cache_set(cache_key, stats)
        return stats
    except Exception as e:
        log.warning(f"[NHL SEASON STATS] {e}")
        return {}


def _transform_nhl_log(row: dict) -> dict:
    """Transform a BDL NHL stat row into unified schema."""
    game = row.get("game") or {}
    date_str = (game.get("date") or "")[:10]
    home_team_id = (game.get("home_team") or {}).get("id")
    player_team_id = (row.get("team") or {}).get("id")
    venue = "home" if player_team_id == home_team_id else "away"

    goals   = (row.get("goals") or 0)
    assists = (row.get("assists") or 0)
    shots   = (row.get("shots") or 0)
    blocks  = (row.get("blocked_shots") or row.get("blocks") or 0)
    hits    = (row.get("hits") or 0)
    pm      = (row.get("plus_minus") or 0)
    pim     = (row.get("penalty_minutes") or 0)
    toi_str = row.get("time_on_ice") or "0:00"
    # Parse TOI: "20:31" → float minutes
    try:
        toi_parts = str(toi_str).split(":")
        toi = int(toi_parts[0]) + (int(toi_parts[1]) / 60 if len(toi_parts) > 1 else 0)
    except Exception:
        toi = 0.0

    # Goalie stats
    saves        = (row.get("saves") or 0)
    goals_against = (row.get("goals_against") or 0)
    shots_against = saves + goals_against
    save_pct     = round(saves / shots_against, 3) if shots_against > 0 else 0.0

    return {
        "date":          date_str,
        "game_id":       game.get("id"),
        "venue":         venue,
        # Skater
        "goals":         goals,
        "assists":       assists,
        "points":        goals + assists,
        "shots":         shots,
        "blocked_shots": blocks,
        "hits":          hits,
        "plus_minus":    pm,
        "pim":           pim,
        "toi":           round(toi, 2),
        # Goalie
        "saves":         saves,
        "goals_against": goals_against,
        "save_pct":      save_pct,
        "shots_against": shots_against,
        "_source": "bdl",
    }


async def get_player_game_logs(player_id: int, season: str = CURRENT_NHL_SEASON) -> list:
    """Fetch per-game stats for a player via /player_game_stats, newest-first.
    Cache key prefix 'gl3:' bypasses stale Atlas entries from old broken calls.
    Empty results are NOT cached so a transient 429 doesn't block future data.
    """
    cache_key = f"gl3:{player_id}:{season}"
    cached = await _cache_get(cache_key)
    if _cache_fresh(cached, CACHE_TTL["stats"]):
        return cached["data"]

    # BDL NHL provides no per-game or season-average stat endpoints at the current
    # subscription tier — all stat routes return 404.  Skip the API call entirely
    # to conserve quota.  Return [] so the route can surface a clear error.
    log.debug(f"[NHL GAME LOGS] player={player_id}: BDL NHL stats unavailable — no endpoint")
    logs = []
    return logs
