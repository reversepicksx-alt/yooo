"""
BallDontLie PGA Tour API client.
Base URL: https://api.balldontlie.io/pga/v1
"""
import asyncio
import time
import os
import logging
from datetime import datetime, timezone
from typing import Optional

import httpx
from config import db

log = logging.getLogger("pga_client")

PGA_API_BASE = "https://api.balldontlie.io/pga/v1"
PGA_API_KEY  = os.environ.get("MLB_BDL_API_KEY", "")

_rate_sem = asyncio.Semaphore(6)
_last_req_time: float = 0.0
_MIN_INTERVAL = 0.10

CACHE_TTL = {
    "player_search": 6 * 3600,
    "player":        2 * 3600,
    "round_logs":    2 * 3600,
    "rankings":      3600,
}

CURRENT_PGA_SEASON = 2026


async def _get(path: str, params: dict = None, _retry: int = 0) -> dict:
    global _last_req_time
    headers = {"Authorization": PGA_API_KEY}
    url = f"{PGA_API_BASE}{path}"

    async with _rate_sem:
        elapsed = time.monotonic() - _last_req_time
        if elapsed < _MIN_INTERVAL:
            await asyncio.sleep(_MIN_INTERVAL - elapsed)
        try:
            async with httpx.AsyncClient(timeout=20) as c:
                resp = await c.get(url, headers=headers, params=params or {})
        except Exception as e:
            raise RuntimeError(f"PGA API network error: {e}")
        finally:
            _last_req_time = time.monotonic()

        if resp.status_code != 429:
            if resp.status_code >= 400:
                raise RuntimeError(f"PGA API error {resp.status_code}: {resp.text[:200]}")
            return resp.json()

        retry_after = min(int(resp.headers.get("retry-after", "5")), 15)

    if _retry >= 2:
        raise RuntimeError("PGA API rate-limit exceeded after retries")
    log.warning(f"[PGA CLIENT] 429 on {path} — waiting {retry_after}s")
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
        return await db.pga_cache.find_one({"key": key}, {"_id": 0})
    except Exception:
        return None


async def _cache_set(key: str, data) -> None:
    try:
        await db.pga_cache.update_one(
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


async def search_players(query: str, limit: int = 15) -> list:
    cache_key = f"search:{query.lower()}"
    cached = await _cache_get(cache_key)
    if _cache_fresh(cached, CACHE_TTL["player_search"]):
        return cached["data"][:limit]

    results = []
    try:
        data = await _get("/players", {"search": query, "per_page": 25})
        results = data.get("data", [])
    except Exception as e:
        log.warning(f"[PGA SEARCH] {e}")

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
        log.warning(f"[PGA PLAYER] {e}")
        return None


async def get_player_round_logs(player_id: int, season: int = CURRENT_PGA_SEASON, limit: int = 40) -> list:
    """Fetch recent round/tournament results for a golfer (newest-first)."""
    cache_key = f"rounds:{player_id}:{season}"
    cached = await _cache_get(cache_key)
    if _cache_fresh(cached, CACHE_TTL["round_logs"]):
        return cached["data"]

    all_results = []
    cursor = None
    for _ in range(5):
        params = {"player_id": player_id, "season": season, "per_page": 25}
        if cursor:
            params["cursor"] = cursor
        try:
            data = await _get("/stats", params)
        except Exception as e:
            log.warning(f"[PGA STATS] player={player_id}: {e}")
            break
        all_results.extend(data.get("data", []))
        meta = data.get("meta", {})
        cursor = meta.get("next_cursor")
        if not cursor or len(all_results) >= limit:
            break

    logs = []
    for r in all_results:
        tournament = r.get("tournament") or r.get("event") or {}
        t_name = tournament.get("name") or tournament.get("event_name") or "Tournament"
        t_date = (tournament.get("date") or r.get("date") or "")[:10]

        strokes     = _safe(r.get("strokes") or r.get("score") or r.get("total_strokes"))
        round_score = _safe(r.get("round_score") or r.get("round_strokes"))
        to_par      = _safe(r.get("to_par") or r.get("score_to_par"))
        position    = _safe(r.get("position") or r.get("finish_position"), 100)
        birdies     = _safe(r.get("birdies"))
        bogeys      = _safe(r.get("bogeys"))
        eagles      = _safe(r.get("eagles"))
        pars        = _safe(r.get("pars"))
        putts       = _safe(r.get("putts"))
        fwy_hit     = _safe(r.get("fairways_hit") or r.get("fairway_percentage"))
        gir         = _safe(r.get("greens_in_regulation") or r.get("gir") or r.get("gir_percentage"))
        drv_dist    = _safe(r.get("driving_distance"))
        made_cut    = 1.0 if not (r.get("missed_cut") or r.get("withdrew") or r.get("disqualified")) else 0.0

        logs.append({
            "date":         t_date,
            "tournament":   t_name,
            "strokes":      round(strokes),
            "round_score":  round(round_score),
            "to_par":       round(to_par, 0),
            "finish_pos":   int(position) if position < 100 else 100,
            "birdies":      round(birdies),
            "bogeys":       round(bogeys),
            "eagles":       round(eagles),
            "pars":         round(pars),
            "putts":        round(putts),
            "fairways_hit": round(fwy_hit, 1),
            "gir":          round(gir, 1),
            "driving_distance": round(drv_dist, 1),
            "made_cut":     made_cut,
            "_source": "bdl",
        })

    logs.sort(key=lambda x: x.get("date", ""), reverse=True)
    await _cache_set(cache_key, logs)
    return logs
