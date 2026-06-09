"""
BallDontLie MMA API client.
Base URL: https://api.balldontlie.io/mma/v1
"""
import asyncio
import time
import os
import logging
from datetime import datetime, timezone
from typing import Optional

import httpx
from config import db

log = logging.getLogger("mma_client")

MMA_API_BASE = "https://api.balldontlie.io/mma/v1"
MMA_API_KEY  = os.environ.get("MLB_BDL_API_KEY", "")

_rate_sem = asyncio.Semaphore(6)
_last_req_time: float = 0.0
_MIN_INTERVAL = 0.10

CACHE_TTL = {
    "fighter_search": 6 * 3600,
    "fighter":        2 * 3600,
    "fight_logs":     2 * 3600,
    "rankings":       3600,
}


async def _get(path: str, params: dict = None, _retry: int = 0) -> dict:
    global _last_req_time
    headers = {"Authorization": MMA_API_KEY}
    url = f"{MMA_API_BASE}{path}"

    async with _rate_sem:
        elapsed = time.monotonic() - _last_req_time
        if elapsed < _MIN_INTERVAL:
            await asyncio.sleep(_MIN_INTERVAL - elapsed)
        try:
            async with httpx.AsyncClient(timeout=20) as c:
                resp = await c.get(url, headers=headers, params=params or {})
        except Exception as e:
            raise RuntimeError(f"MMA API network error: {e}")
        finally:
            _last_req_time = time.monotonic()

        if resp.status_code != 429:
            if resp.status_code >= 400:
                raise RuntimeError(f"MMA API error {resp.status_code}: {resp.text[:200]}")
            return resp.json()

        retry_after = min(int(resp.headers.get("retry-after", "5")), 15)

    if _retry >= 2:
        raise RuntimeError("MMA API rate-limit exceeded after retries")
    log.warning(f"[MMA CLIENT] 429 on {path} — waiting {retry_after}s")
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
        return await db.mma_cache.find_one({"key": key}, {"_id": 0})
    except Exception:
        return None


async def _cache_set(key: str, data) -> None:
    try:
        await db.mma_cache.update_one(
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


async def search_fighters(query: str, limit: int = 15) -> list:
    cache_key = f"search:{query.lower()}"
    cached = await _cache_get(cache_key)
    if _cache_fresh(cached, CACHE_TTL["fighter_search"]):
        return cached["data"][:limit]

    results = []
    try:
        data = await _get("/fighters", {"search": query, "per_page": 25})
        results = data.get("data", [])
    except Exception as e:
        log.warning(f"[MMA SEARCH] {e}")

    await _cache_set(cache_key, results[:limit])
    return results[:limit]


async def get_fighter(fighter_id: int) -> Optional[dict]:
    cache_key = f"fighter:{fighter_id}"
    cached = await _cache_get(cache_key)
    if _cache_fresh(cached, CACHE_TTL["fighter"]):
        return cached["data"]
    try:
        data = await _get(f"/fighters/{fighter_id}")
        fighter = data.get("data", {})
        await _cache_set(cache_key, fighter)
        return fighter
    except Exception as e:
        log.warning(f"[MMA FIGHTER] {e}")
        return None


async def get_fighter_fight_logs(fighter_id: int, limit: int = 20) -> list:
    """Fetch recent fight stats for a fighter via /fight_stats endpoint (newest-first)."""
    cache_key = f"fight_stats2:{fighter_id}:{limit}"
    cached = await _cache_get(cache_key)
    if _cache_fresh(cached, CACHE_TTL["fight_logs"]):
        return cached["data"]

    all_stats = []
    cursor = None
    for _ in range(5):
        params = {"fighter_id": fighter_id, "per_page": 25}
        if cursor:
            params["cursor"] = cursor
        try:
            data = await _get("/fight_stats", params)
        except Exception as e:
            log.warning(f"[MMA FIGHT_STATS] fighter={fighter_id}: {e}")
            break
        all_stats.extend(data.get("data", []))
        meta = data.get("meta", {})
        cursor = meta.get("next_cursor")
        if not cursor or len(all_stats) >= limit:
            break

    logs = []
    for s in all_stats:
        # /fight_stats returns per-fighter stats directly — BDL field names
        sig_str_land = _safe(s.get("significant_strikes_landed") or s.get("sig_strikes_landed") or s.get("sig_str_landed"))
        sig_str_att  = _safe(s.get("significant_strikes_attempted") or s.get("sig_str_att") or sig_str_land * 1.5)
        tot_str_land = _safe(s.get("total_strikes_landed") or s.get("total_str_landed") or sig_str_land)
        td_land      = _safe(s.get("takedowns_landed") or s.get("td_landed"))
        td_att       = _safe(s.get("takedowns_attempted") or s.get("td_att") or td_land * 2)
        sub_att      = _safe(s.get("submissions_attempted") or s.get("submission_attempts") or s.get("sub_att"))
        knockdowns   = _safe(s.get("knockdowns") or s.get("kd"))
        ctrl_secs    = _safe(s.get("control_time_seconds") or s.get("ctrl"))
        won          = bool(s.get("is_winner"))

        logs.append({
            "date":                  "",
            "opponent":              "?",
            "won":                   1.0 if won else 0.0,
            "method":                "decision",
            "round":                 3,
            "fight_time_mins":       15.0,
            "sig_strikes_landed":    round(sig_str_land),
            "sig_strikes_attempted": round(sig_str_att),
            "total_strikes_landed":  round(tot_str_land),
            "takedowns_landed":      round(td_land),
            "takedowns_attempted":   round(td_att),
            "submission_attempts":   round(sub_att),
            "knockdowns":            round(knockdowns),
            "control_time_secs":     round(ctrl_secs),
            "_source": "bdl_fight_stats",
        })

    await _cache_set(cache_key, logs)
    return logs
