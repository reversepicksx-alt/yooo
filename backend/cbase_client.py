"""
BallDontLie College Baseball API client.
Base URL: https://api.balldontlie.io/cbase/v1
"""
import asyncio
import time
import os
import logging
from datetime import datetime, timezone
from typing import Optional

import httpx
from config import db

log = logging.getLogger("cbase_client")

CBASE_API_BASE = "https://api.balldontlie.io/cbase/v1"
CBASE_API_KEY  = os.environ.get("MLB_BDL_API_KEY", "")

_rate_sem = asyncio.Semaphore(6)
_last_req_time: float = 0.0
_MIN_INTERVAL = 0.10

CACHE_TTL = {
    "player_search": 4 * 3600,
    "player":        2 * 3600,
    "stats":         2 * 3600,
    "season_stats":  2 * 3600,
}

CURRENT_CBASE_SEASON = 2026


async def _get(path: str, params: dict = None, _retry: int = 0) -> dict:
    global _last_req_time
    headers = {"Authorization": CBASE_API_KEY}
    url = f"{CBASE_API_BASE}{path}"

    async with _rate_sem:
        elapsed = time.monotonic() - _last_req_time
        if elapsed < _MIN_INTERVAL:
            await asyncio.sleep(_MIN_INTERVAL - elapsed)
        try:
            async with httpx.AsyncClient(timeout=20) as c:
                resp = await c.get(url, headers=headers, params=params or {})
        except Exception as e:
            raise RuntimeError(f"CBase API network error: {e}")
        finally:
            _last_req_time = time.monotonic()

        if resp.status_code != 429:
            if resp.status_code >= 400:
                raise RuntimeError(f"CBase API error {resp.status_code}: {resp.text[:200]}")
            return resp.json()

        retry_after = min(int(resp.headers.get("retry-after", "5")), 15)

    if _retry >= 2:
        raise RuntimeError("CBase API rate-limit exceeded after retries")
    log.warning(f"[CBASE CLIENT] 429 on {path} — waiting {retry_after}s")
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
        return await db.cbase_cache.find_one({"key": key}, {"_id": 0})
    except Exception:
        return None


async def _cache_set(key: str, data) -> None:
    try:
        await db.cbase_cache.update_one(
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
    cursor = None
    for _ in range(3):
        params = {"search": query, "per_page": 25}
        if cursor:
            params["cursor"] = cursor
        try:
            data = await _get("/players", params)
        except Exception as e:
            log.warning(f"[CBASE SEARCH] {e}")
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
        log.warning(f"[CBASE PLAYER] {e}")
        return None


async def get_season_averages(player_id: int, season: int = CURRENT_CBASE_SEASON) -> Optional[dict]:
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
        log.warning(f"[CBASE SEASON AVG] {e}")
        return {}


async def get_player_game_logs(player_id: int, season: int = CURRENT_CBASE_SEASON) -> list:
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
            log.warning(f"[CBASE STATS] player={player_id}: {e}")
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

        # Batting stats
        at_bats    = _safe(row.get("at_bats") or row.get("ab"))
        hits       = _safe(row.get("hits") or row.get("h"))
        runs       = _safe(row.get("runs") or row.get("r"))
        rbi        = _safe(row.get("rbi"))
        home_runs  = _safe(row.get("home_runs") or row.get("hr"))
        doubles    = _safe(row.get("doubles") or row.get("d") or row.get("_2b"))
        triples    = _safe(row.get("triples") or row.get("_3b"))
        walks      = _safe(row.get("walks") or row.get("bb"))
        k_bat      = _safe(row.get("strikeouts") or row.get("so") or row.get("k"))
        stolen_b   = _safe(row.get("stolen_bases") or row.get("sb"))
        total_bases = hits + doubles + triples * 2 + home_runs * 3

        # Pitching stats
        ip          = _safe(row.get("innings_pitched") or row.get("ip"))
        earned_runs = _safe(row.get("earned_runs") or row.get("er"))
        k_pitch     = _safe(row.get("strikeouts_pitching") or row.get("so_p") or row.get("k_p"))
        bb_pitch    = _safe(row.get("walks_allowed") or row.get("bb_p"))
        hits_allowed = _safe(row.get("hits_allowed") or row.get("h_p"))
        win         = 1.0 if row.get("win") or row.get("pitcher_win") else 0.0
        save        = 1.0 if row.get("save") else 0.0

        batting_avg = hits / at_bats if at_bats > 0 else 0.0

        logs.append({
            "date":          date_str,
            "venue":         venue,
            "at_bats":       round(at_bats),
            "hits":          round(hits),
            "runs":          round(runs),
            "rbi":           round(rbi),
            "home_runs":     round(home_runs),
            "walks":         round(walks),
            "strikeouts":    round(k_bat),
            "stolen_bases":  round(stolen_b),
            "total_bases":   round(total_bases),
            "batting_avg":   round(batting_avg, 3),
            "innings_pitched": round(ip, 1),
            "earned_runs":   round(earned_runs),
            "strikeouts_pitching": round(k_pitch),
            "walks_allowed": round(bb_pitch),
            "hits_allowed":  round(hits_allowed),
            "pitcher_win":   win,
            "save":          save,
            "_source": "bdl",
        })

    logs.sort(key=lambda x: x.get("date", ""), reverse=True)

    if not logs:
        avgs = await get_season_averages(player_id, season)
        if avgs:
            logs = [{
                "date": "", "venue": "home",
                "at_bats": round(_safe(avgs.get("at_bats"))),
                "hits": round(_safe(avgs.get("hits"))),
                "runs": round(_safe(avgs.get("runs"))),
                "rbi": round(_safe(avgs.get("rbi"))),
                "home_runs": round(_safe(avgs.get("home_runs"))),
                "walks": round(_safe(avgs.get("walks"))),
                "strikeouts": round(_safe(avgs.get("strikeouts"))),
                "stolen_bases": round(_safe(avgs.get("stolen_bases"))),
                "total_bases": 0,
                "batting_avg": round(_safe(avgs.get("batting_avg")), 3),
                "innings_pitched": round(_safe(avgs.get("innings_pitched")), 1),
                "earned_runs": round(_safe(avgs.get("earned_runs"))),
                "strikeouts_pitching": round(_safe(avgs.get("strikeouts_pitching"))),
                "walks_allowed": 0, "hits_allowed": 0,
                "pitcher_win": 0, "save": 0,
                "_source": "bdl_avg",
            }]

    await _cache_set(cache_key, logs)
    return logs
