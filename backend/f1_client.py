"""
BallDontLie Formula 1 API client.
Base URL: https://api.balldontlie.io/f1/v1
"""
import asyncio
import time
import os
import logging
from datetime import datetime, timezone
from typing import Optional

import httpx
from config import db

log = logging.getLogger("f1_client")

F1_API_BASE = "https://api.balldontlie.io/f1/v1"
F1_API_KEY  = os.environ.get("MLB_BDL_API_KEY", "")

_rate_sem = asyncio.Semaphore(6)
_last_req_time: float = 0.0
_MIN_INTERVAL = 0.10

CACHE_TTL = {
    "driver_search": 6 * 3600,
    "driver":        2 * 3600,
    "race_results":  2 * 3600,
    "standings":     1 * 3600,
}

CURRENT_F1_SEASON = 2025


async def _get(path: str, params: dict = None, _retry: int = 0) -> dict:
    global _last_req_time
    headers = {"Authorization": F1_API_KEY}
    url = f"{F1_API_BASE}{path}"

    async with _rate_sem:
        elapsed = time.monotonic() - _last_req_time
        if elapsed < _MIN_INTERVAL:
            await asyncio.sleep(_MIN_INTERVAL - elapsed)
        try:
            async with httpx.AsyncClient(timeout=20) as c:
                resp = await c.get(url, headers=headers, params=params or {})
        except Exception as e:
            raise RuntimeError(f"F1 API network error: {e}")
        finally:
            _last_req_time = time.monotonic()

        if resp.status_code != 429:
            if resp.status_code >= 400:
                raise RuntimeError(f"F1 API error {resp.status_code}: {resp.text[:200]}")
            return resp.json()

        retry_after = min(int(resp.headers.get("retry-after", "5")), 15)

    if _retry >= 2:
        raise RuntimeError("F1 API rate-limit exceeded after retries")
    log.warning(f"[F1 CLIENT] 429 on {path} — waiting {retry_after}s")
    await asyncio.sleep(retry_after)
    return await _get(path, params, _retry + 1)


def _cache_fresh(doc: Optional[dict], ttl_seconds: int) -> bool:
    if not doc:
        return False
    ts = doc.get("ts", "")
    try:
        age = (datetime.now(timezone.utc) -
               datetime.fromisoformat(str(ts)).replace(tzinfo=timezone.utc)).total_seconds()
        return age < ttl_seconds
    except Exception:
        return False


async def _cache_get(key: str) -> Optional[dict]:
    try:
        return await db.f1_cache.find_one({"key": key}, {"_id": 0})
    except Exception:
        return None


async def _cache_set(key: str, data) -> None:
    try:
        await db.f1_cache.update_one(
            {"key": key},
            {"$set": {"key": key, "data": data, "ts": datetime.now(timezone.utc).isoformat()}},
            upsert=True,
        )
    except Exception:
        pass


def _safe(val, default=0.0) -> float:
    try:
        return float(val) if val is not None else default
    except Exception:
        return default


async def search_drivers(query: str, limit: int = 15) -> list:
    cache_key = f"search:{query.lower()}"
    cached = await _cache_get(cache_key)
    if _cache_fresh(cached, CACHE_TTL["driver_search"]):
        return cached["data"][:limit]

    results = []
    try:
        data = await _get("/drivers", {"search": query, "per_page": 25})
        results = data.get("data", [])
    except Exception as e:
        log.warning(f"[F1 SEARCH] {e}")

    await _cache_set(cache_key, results[:limit])
    return results[:limit]


async def get_driver(driver_id: int) -> Optional[dict]:
    cache_key = f"driver:{driver_id}"
    cached = await _cache_get(cache_key)
    if _cache_fresh(cached, CACHE_TTL["driver"]):
        return cached["data"]
    try:
        data = await _get(f"/drivers/{driver_id}")
        driver = data.get("data", {})
        await _cache_set(cache_key, driver)
        return driver
    except Exception as e:
        log.warning(f"[F1 DRIVER] {e}")
        return None


async def get_driver_race_logs(driver_id: int, season: int = CURRENT_F1_SEASON, limit: int = 25) -> list:
    """Fetch recent race results for a driver (newest-first)."""
    cache_key = f"races:{driver_id}:{season}"
    cached = await _cache_get(cache_key)
    if _cache_fresh(cached, CACHE_TTL["race_results"]):
        return cached["data"]

    all_results = []
    cursor = None
    for _ in range(5):
        params = {"driver_id": driver_id, "season": season, "per_page": 25}
        if cursor:
            params["cursor"] = cursor
        try:
            data = await _get("/results", params)
        except Exception as e:
            log.warning(f"[F1 RESULTS] driver={driver_id}: {e}")
            break
        all_results.extend(data.get("data", []))
        meta = data.get("meta", {})
        cursor = meta.get("next_cursor")
        if not cursor or len(all_results) >= limit:
            break

    logs = []
    for r in all_results:
        race = r.get("race") or r.get("grand_prix") or {}
        race_name = race.get("name") or race.get("circuit") or "Race"
        race_date = (race.get("date") or r.get("date") or "")[:10]

        position    = _safe(r.get("position") or r.get("finish_position"), 99)
        grid        = _safe(r.get("grid") or r.get("grid_position") or r.get("starting_grid"), 20)
        points      = _safe(r.get("points") or r.get("championship_points"))
        fastest_lap = bool(r.get("fastest_lap") or r.get("has_fastest_lap"))
        laps        = _safe(r.get("laps") or r.get("laps_completed"))
        laps_led    = _safe(r.get("laps_led"))
        pit_stops   = _safe(r.get("pit_stops") or r.get("pit_stop_count"))
        status      = r.get("status") or r.get("finish_status") or "Finished"
        dnf         = 1.0 if "dnf" in status.lower() or "ret" in status.lower() else 0.0
        pos_gain    = grid - position  # positive = gained places

        logs.append({
            "date":         race_date,
            "race":         race_name,
            "finish_pos":   int(position) if position < 99 else 99,
            "grid_pos":     int(grid),
            "points":       round(points, 1),
            "fastest_lap":  1.0 if fastest_lap else 0.0,
            "laps":         int(laps),
            "laps_led":     int(laps_led),
            "pit_stops":    int(pit_stops),
            "dnf":          dnf,
            "pos_gain":     round(pos_gain, 0),
            "status":       status,
            "_source": "bdl",
        })

    logs.sort(key=lambda x: x.get("date", ""), reverse=True)
    await _cache_set(cache_key, logs)
    return logs
