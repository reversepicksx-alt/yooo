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
from datetime import datetime, timezone
from typing import Optional

import httpx
from config import db

log = logging.getLogger("mlb_client")

MLB_API_BASE = "https://api.balldontlie.io/mlb/v1"
# Key hardcoded as fallback; override via MLB_BDL_API_KEY env var
MLB_API_KEY = os.environ.get("MLB_BDL_API_KEY", "951b8b73-a036-4b30-924f-19f322766545")

_rate_sem = asyncio.Semaphore(1)
_last_req_time: float = 0.0
_MIN_INTERVAL = 3.1   # seconds between requests — BDL free tier: 5 req / 15s ≈ 1 per 3s

CACHE_TTL = {
    "teams":         7 * 86400,   # 7 days
    "player":        6 * 3600,    # 6 hours
    "player_search": 24 * 3600,   # 24 hours
    "stats":         2 * 3600,    # 2 hours (live season)
    "season_stats":  2 * 3600,
}


async def _get(path: str, params: dict = None) -> dict:
    global _last_req_time
    async with _rate_sem:
        elapsed = time.monotonic() - _last_req_time
        if elapsed < _MIN_INTERVAL:
            await asyncio.sleep(_MIN_INTERVAL - elapsed)
        headers = {"Authorization": MLB_API_KEY}
        url = f"{MLB_API_BASE}{path}"
        try:
            async with httpx.AsyncClient(timeout=30) as c:
                resp = await c.get(url, headers=headers, params=params or {})
        except Exception as e:
            raise RuntimeError(f"MLB API network error: {e}")
        finally:
            _last_req_time = time.monotonic()

        if resp.status_code == 429:
            retry_after = int(resp.headers.get("retry-after", "15"))
            await asyncio.sleep(retry_after + 1)
            async with httpx.AsyncClient(timeout=30) as c:
                resp = await c.get(url, headers=headers, params=params or {})
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
            {"$set": {"key": key, "data": data, "ts": datetime.now(timezone.utc).isoformat()}},
            upsert=True,
        )
    except Exception as e:
        log.debug(f"[MLB CACHE] write skip (DB unreachable): {e}")


async def search_players(query: str, limit: int = 15) -> list:
    key = f"mlb_ps:{query.lower().strip()}"
    doc = await _cache_get(key)
    if _cache_fresh(doc, CACHE_TTL["player_search"]) and doc.get("data") is not None:
        return doc["data"]
    result = await _get("/players", {"search": query, "per_page": limit})
    players = result.get("data", [])
    await _cache_set(key, players)
    return players


async def get_player(player_id: int) -> Optional[dict]:
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


async def get_player_game_logs(player_id: int, season: int = 2025, limit: int = 30) -> list:
    """Per-game stats, newest first (API returns newest first via cursor pagination)."""
    key = f"mlb_gl:{player_id}:{season}"
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
    await _cache_set(key, logs)
    return logs


async def get_season_stats(player_id: int, season: int = 2025) -> Optional[dict]:
    """Season aggregate stats (regular season only)."""
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
