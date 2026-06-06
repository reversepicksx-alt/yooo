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

_rate_sem = asyncio.Semaphore(6)
_last_req_time: float = 0.0
_MIN_INTERVAL = 0.10

CACHE_TTL = {
    "teams":         7 * 86400,
    "player":        2 * 3600,
    "player_search": 4 * 3600,
    "stats":         2 * 3600,
}

CURRENT_NFL_SEASON = 2024


async def _get(path: str, params: dict = None) -> dict:
    global _last_req_time
    headers = {"Authorization": NFL_API_KEY}
    url = f"{NFL_API_BASE}{path}"

    async with _rate_sem:
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

        retry_after = min(int(resp.headers.get("retry-after", "5")), 10)

    log.warning(f"[NFL CLIENT] 429 on {path} — waiting {retry_after}s")
    await asyncio.sleep(retry_after)

    async with _rate_sem:
        elapsed = time.monotonic() - _last_req_time
        if elapsed < _MIN_INTERVAL:
            await asyncio.sleep(_MIN_INTERVAL - elapsed)
        try:
            async with httpx.AsyncClient(timeout=20) as c:
                resp = await c.get(url, headers=headers, params=params or {})
        except Exception as e:
            raise RuntimeError(f"NFL API network error on retry: {e}")
        finally:
            _last_req_time = time.monotonic()

        if resp.status_code >= 400:
            raise RuntimeError(f"NFL API error {resp.status_code}: {resp.text[:200]}")
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
            log.warning(f"[NFL SEARCH] {e}")
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
    home_team_id = (game.get("home_team") or {}).get("id")
    player_team_id = (row.get("team") or {}).get("id")
    venue = "home" if player_team_id == home_team_id else "away"

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
    await _cache_set(cache_key, logs)
    return logs
