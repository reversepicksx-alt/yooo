"""
BallDontLie NCAAF (College Football) API client.
Base URL: https://api.balldontlie.io/ncaaf/v1
"""
import asyncio
import time
import os
import logging
from datetime import datetime, timezone
from typing import Optional

import httpx
from config import db

log = logging.getLogger("ncaaf_client")

NCAAF_API_BASE = "https://api.balldontlie.io/ncaaf/v1"
NCAAF_API_KEY  = os.environ.get("MLB_BDL_API_KEY", "")

_rate_sem = asyncio.Semaphore(6)
_last_req_time: float = 0.0
_MIN_INTERVAL = 0.10

CACHE_TTL = {
    "teams":         7 * 86400,
    "player":        2 * 3600,
    "player_search": 4 * 3600,
    "stats":         2 * 3600,
    "season_stats":  2 * 3600,
}

CURRENT_NCAAF_SEASON = 2025


async def _get(path: str, params: dict = None, _retry: int = 0) -> dict:
    global _last_req_time
    headers = {"Authorization": NCAAF_API_KEY}
    url = f"{NCAAF_API_BASE}{path}"

    async with _rate_sem:
        elapsed = time.monotonic() - _last_req_time
        if elapsed < _MIN_INTERVAL:
            await asyncio.sleep(_MIN_INTERVAL - elapsed)
        try:
            async with httpx.AsyncClient(timeout=20) as c:
                resp = await c.get(url, headers=headers, params=params or {})
        except Exception as e:
            raise RuntimeError(f"NCAAF API network error: {e}")
        finally:
            _last_req_time = time.monotonic()

        if resp.status_code != 429:
            if resp.status_code >= 400:
                raise RuntimeError(f"NCAAF API error {resp.status_code}: {resp.text[:200]}")
            return resp.json()

        retry_after = min(int(resp.headers.get("retry-after", "5")), 15)

    if _retry >= 2:
        raise RuntimeError("NCAAF API rate-limit exceeded after retries")
    log.warning(f"[NCAAF CLIENT] 429 on {path} — waiting {retry_after}s")
    await asyncio.sleep(retry_after)
    return await _get(path, params, _retry + 1)


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
        return await db.ncaaf_cache.find_one({"key": key}, {"_id": 0})
    except Exception:
        return None


async def _cache_set(key: str, data) -> None:
    try:
        await db.ncaaf_cache.update_one(
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
            log.warning(f"[NCAAF SEARCH] {e}")
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
        log.warning(f"[NCAAF PLAYER] {e}")
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
        log.warning(f"[NCAAF TEAMS] {e}")
        return []


async def get_season_averages(player_id: int, season: int = CURRENT_NCAAF_SEASON) -> Optional[dict]:
    cache_key = f"season_avg:{player_id}:{season}"
    cached = await _cache_get(cache_key)
    if _cache_fresh(cached, CACHE_TTL["season_stats"]):
        return cached["data"]
    try:
        data = await _get("/season_averages", {"season": season, "player_ids[]": player_id})
        avgs = (data.get("data") or [{}])[0] if data.get("data") else {}
        await _cache_set(cache_key, avgs)
        return avgs
    except Exception as e:
        log.warning(f"[NCAAF SEASON AVG] {e}")
        return {}


def _safe(val) -> float:
    try:
        return float(val) if val is not None else 0.0
    except Exception:
        return 0.0


async def get_player_game_logs(player_id: int, season: int = CURRENT_NCAAF_SEASON) -> list:
    cache_key = f"stats:{player_id}:{season}"
    cached = await _cache_get(cache_key)
    if _cache_fresh(cached, CACHE_TTL["stats"]):
        return cached["data"]

    all_stats = []
    cursor = None
    for _ in range(10):
        params = {"player_ids[]": player_id, "seasons[]": season, "per_page": 100}
        if cursor:
            params["cursor"] = cursor
        try:
            data = await _get("/stats", params)
        except Exception as e:
            log.warning(f"[NCAAF STATS] player={player_id}: {e}")
            break
        rows = data.get("data", [])
        all_stats.extend(rows)
        meta = data.get("meta", {})
        cursor = meta.get("next_cursor")
        if not cursor:
            break

    logs = []
    for row in all_stats:
        game = row.get("game") or {}
        date_str = (game.get("date") or "")[:10]
        home_team_id = (game.get("home_team") or {}).get("id")
        player_team_id = (row.get("team") or {}).get("id")
        venue = "home" if player_team_id == home_team_id else "away"

        min_str = row.get("min") or row.get("minutes") or "0"
        try:
            parts = str(min_str).split(":")
            minutes = int(parts[0]) + (int(parts[1]) / 60 if len(parts) > 1 else 0)
        except Exception:
            minutes = 0.0

        # Football stats — BDL uses these field names
        pass_yds  = _safe(row.get("passing_yards"))
        pass_att  = _safe(row.get("pass_attempts") or row.get("passing_attempts"))
        pass_comp = _safe(row.get("completions"))
        pass_td   = _safe(row.get("passing_touchdowns") or row.get("pass_td"))
        interceptions = _safe(row.get("interceptions"))
        rush_yds  = _safe(row.get("rushing_yards"))
        rush_att  = _safe(row.get("rushing_attempts"))
        rush_td   = _safe(row.get("rushing_touchdowns") or row.get("rush_td"))
        rec_yds   = _safe(row.get("receiving_yards"))
        receptions = _safe(row.get("receptions"))
        rec_td    = _safe(row.get("receiving_touchdowns") or row.get("rec_td"))
        targets   = _safe(row.get("targets"))
        sacks     = _safe(row.get("sacks"))
        total_td  = pass_td + rush_td + rec_td
        total_yds = pass_yds + rush_yds + rec_yds
        fantasy_pts = (
            pass_yds * 0.04 + pass_td * 4 + interceptions * -2 +
            rush_yds * 0.1  + rush_td * 6 +
            rec_yds  * 0.1  + rec_td  * 6 + receptions * 0.5
        )

        logs.append({
            "date":           date_str,
            "venue":          venue,
            "minutes":        round(minutes, 1),
            "passing_yards":  round(pass_yds),
            "rushing_yards":  round(rush_yds),
            "receiving_yards": round(rec_yds),
            "pass_attempts":  round(pass_att),
            "completions":    round(pass_comp),
            "receptions":     round(receptions),
            "touchdowns":     round(total_td),
            "interceptions":  round(interceptions),
            "rushing_attempts": round(rush_att),
            "sacks":          round(sacks, 1),
            "targets":        round(targets),
            "total_yards":    round(total_yds),
            "fantasy_pts":    round(fantasy_pts, 1),
            "_source": "bdl",
        })

    logs.sort(key=lambda x: x.get("date", ""), reverse=True)

    if not logs:
        avgs = await get_season_averages(player_id, season)
        if avgs:
            p_yds = _safe(avgs.get("passing_yards"))
            ru_yds = _safe(avgs.get("rushing_yards"))
            re_yds = _safe(avgs.get("receiving_yards"))
            logs = [{
                "date": "", "venue": "home", "minutes": 0,
                "passing_yards": round(p_yds), "rushing_yards": round(ru_yds),
                "receiving_yards": round(re_yds),
                "pass_attempts": round(_safe(avgs.get("pass_attempts"))),
                "completions": round(_safe(avgs.get("completions"))),
                "receptions": round(_safe(avgs.get("receptions"))),
                "touchdowns": round(_safe(avgs.get("touchdowns"))),
                "interceptions": round(_safe(avgs.get("interceptions"))),
                "rushing_attempts": round(_safe(avgs.get("rushing_attempts"))),
                "sacks": round(_safe(avgs.get("sacks")), 1),
                "targets": round(_safe(avgs.get("targets"))),
                "total_yards": round(p_yds + ru_yds + re_yds),
                "fantasy_pts": 0,
                "_source": "bdl_avg",
            }]

    await _cache_set(cache_key, logs)
    return logs
