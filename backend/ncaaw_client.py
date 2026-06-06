"""
BallDontLie NCAAW (College Basketball Women) API client.
Base URL: https://api.balldontlie.io/ncaaw/v1
"""
import asyncio
import time
import os
import logging
from datetime import datetime, timezone
from typing import Optional

import httpx
from config import db

log = logging.getLogger("ncaaw_client")

NCAAW_API_BASE = "https://api.balldontlie.io/ncaaw/v1"
NCAAW_API_KEY  = os.environ.get("MLB_BDL_API_KEY", "")

_rate_sem = asyncio.Semaphore(6)
_last_req_time: float = 0.0
_MIN_INTERVAL = 0.10

CACHE_TTL = {
    "teams":         7 * 86400,
    "player":        2 * 3600,
    "player_search": 4 * 3600,
    "stats":         2 * 3600,
    "season_stats":  6 * 3600,
}

CURRENT_NCAAW_SEASON = 2025


async def _get(path: str, params: dict = None) -> dict:
    global _last_req_time
    headers = {"Authorization": NCAAW_API_KEY}
    url = f"{NCAAW_API_BASE}{path}"

    async with _rate_sem:
        elapsed = time.monotonic() - _last_req_time
        if elapsed < _MIN_INTERVAL:
            await asyncio.sleep(_MIN_INTERVAL - elapsed)
        try:
            async with httpx.AsyncClient(timeout=20) as c:
                resp = await c.get(url, headers=headers, params=params or {})
        except Exception as e:
            raise RuntimeError(f"NCAAW API network error: {e}")
        finally:
            _last_req_time = time.monotonic()

        if resp.status_code != 429:
            if resp.status_code >= 400:
                raise RuntimeError(f"NCAAW API error {resp.status_code}: {resp.text[:200]}")
            return resp.json()

        retry_after = min(int(resp.headers.get("retry-after", "5")), 10)

    log.warning(f"[NCAAW CLIENT] 429 on {path} — waiting {retry_after}s")
    await asyncio.sleep(retry_after)

    async with _rate_sem:
        elapsed = time.monotonic() - _last_req_time
        if elapsed < _MIN_INTERVAL:
            await asyncio.sleep(_MIN_INTERVAL - elapsed)
        try:
            async with httpx.AsyncClient(timeout=20) as c:
                resp = await c.get(url, headers=headers, params=params or {})
        except Exception as e:
            raise RuntimeError(f"NCAAW API network error on retry: {e}")
        finally:
            _last_req_time = time.monotonic()

        if resp.status_code >= 400:
            raise RuntimeError(f"NCAAW API error {resp.status_code}: {resp.text[:200]}")
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
        return await db.ncaaw_cache.find_one({"key": key}, {"_id": 0})
    except Exception:
        return None


async def _cache_set(key: str, data) -> None:
    try:
        await db.ncaaw_cache.update_one(
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
            log.warning(f"[NCAAW SEARCH] {e}")
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
        data = await _get(f"/players/{player_id}")
        player = data.get("data", {})
        await _cache_set(cache_key, player)
        return player
    except Exception as e:
        log.warning(f"[NCAAW PLAYER] {e}")
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
        log.warning(f"[NCAAW TEAMS] {e}")
        return []


async def get_season_averages(player_id: int, season: int = CURRENT_NCAAW_SEASON) -> dict:
    cache_key = f"season_avg:{player_id}:{season}"
    cached = await _cache_get(cache_key)
    if _cache_fresh(cached, CACHE_TTL["season_stats"]):
        return cached["data"]
    try:
        data = await _get("/season_averages", {"player_ids[]": player_id, "season": season})
        avgs = (data.get("data") or [{}])[0] if data.get("data") else {}
        await _cache_set(cache_key, avgs)
        return avgs
    except Exception as e:
        log.warning(f"[NCAAW AVG] {e}")
        return {}


async def get_player_game_logs(player_id: int, season: int = CURRENT_NCAAW_SEASON) -> list:
    cache_key = f"gamelogs:{player_id}:{season}"
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
            log.warning(f"[NCAAW GAME LOGS] player={player_id}: {e}")
            break
        all_rows.extend(data.get("data", []))
        meta = data.get("meta", {})
        cursor = meta.get("next_cursor")
        if not cursor:
            break

    logs = []
    for row in all_rows:
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
        fantasy = pts * 1.0 + reb * 1.25 + ast * 1.5 + stl * 2.0 + blk * 2.0 - tov * 0.5

        logs.append({
            "date":        date_str,
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
            "pts_reb_ast": pts + reb + ast,
            "pts_reb":     pts + reb,
            "pts_ast":     pts + ast,
            "reb_ast":     reb + ast,
            "stl_blk":     stl + blk,
            "fantasy_pts": round(fantasy, 1),
            "_source": "bdl",
        })

    logs.sort(key=lambda x: x.get("date", ""), reverse=True)

    if not logs:
        avgs = await get_season_averages(player_id, season)
        if avgs:
            log.info(f"[NCAAW] No game logs for {player_id}, using season averages")
            logs = [{"date": "", "venue": "home",
                     "pts": avgs.get("pts", 0), "reb": avgs.get("reb", 0),
                     "ast": avgs.get("ast", 0), "stl": avgs.get("stl", 0),
                     "blk": avgs.get("blk", 0), "tov": avgs.get("turnover", 0),
                     "fg3m": avgs.get("fg3m", 0), "fgm": avgs.get("fgm", 0),
                     "fga": avgs.get("fga", 0), "ftm": avgs.get("ftm", 0),
                     "fta": avgs.get("fta", 0),
                     "pts_reb_ast": (avgs.get("pts", 0) + avgs.get("reb", 0) + avgs.get("ast", 0)),
                     "pts_reb": (avgs.get("pts", 0) + avgs.get("reb", 0)),
                     "pts_ast": (avgs.get("pts", 0) + avgs.get("ast", 0)),
                     "reb_ast": (avgs.get("reb", 0) + avgs.get("ast", 0)),
                     "stl_blk": (avgs.get("stl", 0) + avgs.get("blk", 0)),
                     "fantasy_pts": 0, "_source": "bdl_avg"}]

    await _cache_set(cache_key, logs)
    return logs
