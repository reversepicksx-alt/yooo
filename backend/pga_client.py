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
    """Build synthetic round logs from player season stats (BDL /player_season_stats).
    Falls back to previous season if current has no data.
    """
    import random
    import math

    HYPER_PRIOR = {
        "birdies": 3.8, "bogeys": 3.2, "putts": 28.5,
        "fairways_hit": 8.0, "gir": 11.0, "round_score": 70.0, "made_cut": 0.7,
    }
    ROUND_STD = {
        "birdies": 1.8, "bogeys": 1.5, "putts": 2.3,
        "fairways_hit": 2.0, "gir": 2.5, "round_score": 2.5, "made_cut": 0.0,
    }

    for s in [season, season - 1]:
        cache_key = f"rounds2:{player_id}:{s}"
        cached = await _cache_get(cache_key)
        if _cache_fresh(cached, CACHE_TTL["round_logs"]):
            if cached.get("data"):
                return cached["data"]

        all_stats = []
        cursor = None
        for _ in range(5):
            params = [("player_ids[]", player_id), ("season", s), ("per_page", 25)]
            if cursor:
                params.append(("cursor", cursor))
            try:
                data = await _get("/player_season_stats", dict(params))
            except Exception as e:
                log.warning(f"[PGA STATS] player={player_id}: {e}")
                break
            batch = data.get("data", [])
            all_stats.extend(batch)
            cursor = data.get("meta", {}).get("next_cursor")
            if not cursor:
                break

        if not all_stats:
            continue

        stat_map: dict = {}
        measured_rounds = 0
        for stat in all_stats:
            stat_name = (stat.get("stat_name") or "").lower()
            for sv in (stat.get("stat_value") or []):
                sn = (sv.get("statName") or "").lower()
                raw = str(sv.get("statValue") or "")
                try:
                    numeric = float(raw.replace(",", "").replace('"', "").replace("'", "").split(" ")[0].lstrip("+"))
                except Exception:
                    continue
                if "avg" in sn:
                    if "scoring average" in stat_name and "actual" in stat_name:
                        stat_map["round_score"] = numeric
                    elif "birdies" in stat_name and "pct" not in stat_name and "percentage" not in stat_name:
                        stat_map.setdefault("birdies", numeric)
                    elif "bogey" in stat_name and "avoidance" not in stat_name:
                        stat_map.setdefault("bogeys", numeric)
                    elif "putt" in stat_name:
                        stat_map.setdefault("putts", numeric)
                    elif "driving accuracy" in stat_name:
                        stat_map.setdefault("fairways_hit", numeric)
                    elif "green" in stat_name and "regulation" in stat_name:
                        stat_map.setdefault("gir", numeric)
                elif "measured" in sn and "round" in sn and numeric > 0:
                    measured_rounds = max(measured_rounds, int(numeric))

        if not stat_map:
            continue

        n_rounds = min(max(measured_rounds or 18, 14), 32)
        rng = random.Random(player_id * 100 + s)
        logs = []
        for i in range(n_rounds):
            entry: dict = {
                "date": f"{s}-01-{(i % 28) + 1:02d}",
                "tournament": f"Season {s}",
                "eagles": 0, "pars": 0, "strokes": 0,
                "to_par": 0, "finish_pos": 20, "driving_distance": 295.0,
                "_source": "bdl_season_avg",
            }
            for field in ("birdies", "bogeys", "putts", "fairways_hit", "gir", "round_score", "made_cut"):
                center = stat_map.get(field) or HYPER_PRIOR[field]
                std = ROUND_STD[field]
                if field == "made_cut":
                    entry[field] = 1.0 if center >= 0.5 else 0.0
                elif std > 0:
                    val = max(0.0, center + rng.gauss(0, std))
                    entry[field] = round(val) if field in ("birdies", "bogeys", "putts", "gir", "fairways_hit") else round(val, 1)
                else:
                    entry[field] = round(center, 1)
            entry["strokes"] = entry.get("round_score", 70)
            entry["to_par"] = round(entry.get("round_score", 70) - 72, 0)
            logs.append(entry)

        await _cache_set(cache_key, logs)
        return logs

    return []
