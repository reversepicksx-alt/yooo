"""
BallDontLie Dota 2 API client.
Base URL: https://api.balldontlie.io/dota2/v1
"""
import asyncio
import time
import os
import logging
from datetime import datetime, timezone
from typing import Optional

import httpx
from config import db

log = logging.getLogger("dota2_client")

DOTA2_API_BASE = "https://api.balldontlie.io/dota2/v1"
DOTA2_API_KEY  = os.environ.get("MLB_BDL_API_KEY", "")

_rate_sem = asyncio.Semaphore(6)
_last_req_time: float = 0.0
_MIN_INTERVAL = 0.10

CACHE_TTL = {
    "player_search": 6 * 3600,
    "player":        2 * 3600,
    "match_logs":    2 * 3600,
    "team_search":   6 * 3600,
}


async def _get(path: str, params: dict = None, _retry: int = 0) -> dict:
    global _last_req_time
    headers = {"Authorization": DOTA2_API_KEY}
    url = f"{DOTA2_API_BASE}{path}"

    async with _rate_sem:
        elapsed = time.monotonic() - _last_req_time
        if elapsed < _MIN_INTERVAL:
            await asyncio.sleep(_MIN_INTERVAL - elapsed)
        try:
            async with httpx.AsyncClient(timeout=20) as c:
                resp = await c.get(url, headers=headers, params=params or {})
        except Exception as e:
            raise RuntimeError(f"Dota2 API network error: {e}")
        finally:
            _last_req_time = time.monotonic()

        if resp.status_code != 429:
            if resp.status_code >= 400:
                raise RuntimeError(f"Dota2 API error {resp.status_code}: {resp.text[:200]}")
            return resp.json()

        retry_after = min(int(resp.headers.get("retry-after", "5")), 15)

    if _retry >= 2:
        raise RuntimeError("Dota2 API rate-limit exceeded after retries")
    log.warning(f"[DOTA2 CLIENT] 429 on {path} — waiting {retry_after}s")
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
        return await db.dota2_cache.find_one({"key": key}, {"_id": 0})
    except Exception:
        return None


async def _cache_set(key: str, data) -> None:
    try:
        await db.dota2_cache.update_one(
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
        log.warning(f"[DOTA2 SEARCH] {e}")

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
        log.warning(f"[DOTA2 PLAYER] {e}")
        return None


async def get_player_match_logs(player_id: int, limit: int = 30) -> list:
    """Fetch recent match stats for a player (newest-first)."""
    cache_key = f"matches:{player_id}:{limit}"
    cached = await _cache_get(cache_key)
    if _cache_fresh(cached, CACHE_TTL["match_logs"]):
        return cached["data"]

    all_stats = []
    cursor = None
    for _ in range(5):
        params = {"player_id": player_id, "per_page": 25}
        if cursor:
            params["cursor"] = cursor
        try:
            data = await _get("/stats", params)
        except Exception as e:
            log.warning(f"[DOTA2 STATS] player={player_id}: {e}")
            break
        all_stats.extend(data.get("data", []))
        meta = data.get("meta", {})
        cursor = meta.get("next_cursor")
        if not cursor or len(all_stats) >= limit:
            break

    logs = []
    for r in all_stats:
        match = r.get("match") or {}
        date_str = (match.get("date") or r.get("date") or "")[:10]
        hero = r.get("hero") or r.get("hero_name") or "Unknown"
        team = r.get("team") or {}
        opponent = r.get("opponent_team") or r.get("opponent") or {}
        won = bool(r.get("win") or r.get("won") or r.get("match_won"))

        kills     = _safe(r.get("kills"))
        deaths    = _safe(r.get("deaths"))
        assists   = _safe(r.get("assists"))
        last_hits = _safe(r.get("last_hits"))
        denies    = _safe(r.get("denies"))
        gpm       = _safe(r.get("gold_per_min") or r.get("gpm"))
        xpm       = _safe(r.get("xp_per_min") or r.get("xpm"))
        h_damage  = _safe(r.get("hero_damage"))
        t_damage  = _safe(r.get("tower_damage"))
        healing   = _safe(r.get("hero_healing") or r.get("healing"))
        net_worth = _safe(r.get("net_worth") or r.get("gold"))
        duration  = _safe(r.get("duration") or match.get("duration"))

        kda = (kills + assists) / max(deaths, 1)
        fantasy_pts = kills * 3 + assists * 1.5 - deaths * 1 + last_hits * 0.003 + gpm * 0.01

        logs.append({
            "date":        date_str,
            "hero":        hero,
            "opponent":    (opponent.get("name") or "?"),
            "won":         1.0 if won else 0.0,
            "kills":       int(kills),
            "deaths":      int(deaths),
            "assists":     int(assists),
            "kda":         round(kda, 2),
            "last_hits":   int(last_hits),
            "denies":      int(denies),
            "gpm":         round(gpm),
            "xpm":         round(xpm),
            "hero_damage": round(h_damage),
            "tower_damage": round(t_damage),
            "healing":     round(healing),
            "net_worth":   round(net_worth),
            "duration":    round(duration),
            "fantasy_pts": round(fantasy_pts, 1),
            "_source": "bdl",
        })

    logs.sort(key=lambda x: x.get("date", ""), reverse=True)
    await _cache_set(cache_key, logs)
    return logs
