"""
BallDontLie WNBA API client with rate-limiting and MongoDB caching.
Base URL: https://api.balldontlie.io/wnba/v1
Elite tier: 600 req/min shared across ALL BDL sport clients.

WNBA confirmed endpoints (verified June 2026):
  /games                  - game schedule/scores
  /teams                  - team list
  /players                - player search
  /player_season_averages - season average stats per player  (NOT /season_averages)
  /player_stats           - per-game stats                  (NOT /stats)
"""
import asyncio
import time
import os
import logging
from datetime import datetime, timezone
from typing import Optional

import httpx
from config import db

log = logging.getLogger("wnba_client")

WNBA_API_BASE = "https://api.balldontlie.io/wnba/v1"
WNBA_API_KEY  = os.environ.get("MLB_BDL_API_KEY", "")

_rate_sem = asyncio.Semaphore(2)   # shared BDL key — keep burst low
_last_req_time: float = 0.0
_MIN_INTERVAL = 0.25               # max ~4 req/s from this client

CACHE_TTL = {
    "teams":          7 * 86400,
    "player":         2 * 3600,
    "player_search":  4 * 3600,
    "stats":          2 * 3600,
    "season_stats":   6 * 3600,
}

CURRENT_WNBA_SEASON = 2026


async def _get(path: str, params: dict = None) -> dict:
    global _last_req_time
    headers = {"Authorization": WNBA_API_KEY}
    url = f"{WNBA_API_BASE}{path}"

    async with _rate_sem:
        elapsed = time.monotonic() - _last_req_time
        if elapsed < _MIN_INTERVAL:
            await asyncio.sleep(_MIN_INTERVAL - elapsed)
        try:
            async with httpx.AsyncClient(timeout=20) as c:
                resp = await c.get(url, headers=headers, params=params or {})
        except Exception as e:
            raise RuntimeError(f"WNBA API network error: {e}")
        finally:
            _last_req_time = time.monotonic()

        if resp.status_code != 429:
            if resp.status_code >= 400:
                raise RuntimeError(f"WNBA API error {resp.status_code}: {resp.text[:200]}")
            return resp.json()

        retry_after = min(int(resp.headers.get("retry-after", "5")), 10)

    log.warning(f"[WNBA CLIENT] 429 on {path} — waiting {retry_after}s")
    await asyncio.sleep(retry_after)

    async with _rate_sem:
        elapsed = time.monotonic() - _last_req_time
        if elapsed < _MIN_INTERVAL:
            await asyncio.sleep(_MIN_INTERVAL - elapsed)
        try:
            async with httpx.AsyncClient(timeout=20) as c:
                resp = await c.get(url, headers=headers, params=params or {})
        except Exception as e:
            raise RuntimeError(f"WNBA API network error on retry: {e}")
        finally:
            _last_req_time = time.monotonic()

        if resp.status_code >= 400:
            raise RuntimeError(f"WNBA API error {resp.status_code}: {resp.text[:200]}")
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
        return await db.wnba_cache.find_one({"key": key}, {"_id": 0})
    except Exception:
        return None


async def _cache_set(key: str, data) -> None:
    try:
        await db.wnba_cache.update_one(
            {"key": key},
            {"$set": {"key": key, "data": data, "ts": datetime.now(timezone.utc).isoformat()}},
            upsert=True,
        )
    except Exception:
        pass


async def search_players(query: str, limit: int = 15) -> list:
    cache_key = f"search3:{query.lower()}"
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
            log.warning(f"[WNBA SEARCH] {e}")
            break
        results.extend(data.get("data", []))
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
            log.warning(f"[WNBA SEARCH fallback] {e}")
        else:
            rows = data.get("data", [])
            q_tokens = query.lower().split()
            rows.sort(key=lambda p: sum(1 for t in q_tokens if t in (p.get("full_name") or f"{p.get('first_name','')} {p.get('last_name','')}").lower()), reverse=True)
            results = rows

    if results:
        await _cache_set(cache_key, results[:limit])
    return results[:limit]


async def get_player(player_id: int) -> Optional[dict]:
    cache_key = f"player:{player_id}"
    cached = await _cache_get(cache_key)
    if _cache_fresh(cached, CACHE_TTL["player"]):
        return cached["data"]
    try:
        data = await _get(f"/players/{player_id}")
        player = data.get("data", {})
        await _cache_set(cache_key, player)
        return player
    except Exception as e:
        log.warning(f"[WNBA PLAYER] {e}")
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
        log.warning(f"[WNBA TEAMS] {e}")
        return []


async def get_season_averages(player_id: int, season: int = CURRENT_WNBA_SEASON) -> dict:
    """Get season averages.
    NOTE: BDL WNBA does NOT expose /season_averages or /player_season_averages (both return 404).
    This function is kept as a stub for callers; always returns {}.
    Use get_player_game_logs() for per-game stats.
    """
    return {}


def _transform_wnba_log(row: dict) -> dict:
    """Transform a WNBA stats row to unified schema."""
    game = row.get("game") or {}
    date_str = (game.get("date") or "")[:10]
    home_team_id = (game.get("home_team") or {}).get("id")
    player_team_id = (row.get("team") or {}).get("id")
    venue = "home" if player_team_id == home_team_id else "away"

    pts  = (row.get("pts") or 0)
    reb  = (row.get("reb") or 0)
    ast  = (row.get("ast") or 0)
    stl  = (row.get("stl") or 0)
    blk  = (row.get("blk") or 0)
    tov  = (row.get("turnover") or 0)
    fg3m = (row.get("fg3m") or 0)
    fgm  = (row.get("fgm") or 0)
    fga  = (row.get("fga") or 0)
    ftm  = (row.get("ftm") or 0)
    fta  = (row.get("fta") or 0)
    oreb = (row.get("oreb") or 0)
    dreb = (row.get("dreb") or 0)

    fantasy = pts * 1.0 + reb * 1.25 + ast * 1.5 + stl * 2.0 + blk * 2.0 - tov * 0.5

    return {
        "date":        date_str,
        "game_id":     game.get("id"),
        "venue":       venue,
        "pts":         pts,
        "reb":         reb,
        "ast":         ast,
        "stl":         stl,
        "blk":         blk,
        "tov":         tov,
        "fg3m":        fg3m,
        "fgm":         fgm,
        "fga":         fga,
        "ftm":         ftm,
        "fta":         fta,
        "oreb":        oreb,
        "dreb":        dreb,
        "pts_reb_ast": pts + reb + ast,
        "pts_reb":     pts + reb,
        "pts_ast":     pts + ast,
        "reb_ast":     reb + ast,
        "fantasy_pts": round(fantasy, 1),
        "_source": "bdl",
    }


async def get_player_game_logs(player_id: int, season: int = CURRENT_WNBA_SEASON) -> list:
    """Fetch per-game stats for a player via /player_stats, newest-first.
    Cache key uses prefix 'gl3:' so stale Atlas entries from old broken
    /stats calls (prefix 'gamelogs:') are naturally bypassed.
    Empty results are NOT cached so a transient 429/empty doesn't persist.
    """
    cache_key = f"gl3:{player_id}:{season}"
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
            data = await _get("/player_stats", params)
        except Exception as e:
            log.warning(f"[WNBA GAME LOGS] player={player_id}: {e}")
            break
        all_rows.extend(data.get("data", []))
        meta = data.get("meta", {})
        cursor = meta.get("next_cursor")
        if not cursor:
            break

    logs = [_transform_wnba_log(r) for r in all_rows]
    logs.sort(key=lambda x: x.get("date", ""), reverse=True)

    # Only cache non-empty results — a transient 429 or missing season
    # must not poison the cache and block future real data.
    if logs:
        await _cache_set(cache_key, logs)
    return logs
