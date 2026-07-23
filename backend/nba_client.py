"""
BallDontLie NBA API client with rate-limiting and MongoDB caching.
Base URL: https://api.balldontlie.io/v1
Elite tier: 600 req/min.
"""
import asyncio
import time
import os
import logging
from datetime import datetime, timezone
from typing import Optional

import httpx
from config import db

log = logging.getLogger("nba_client")

NBA_API_BASE = "https://api.balldontlie.io/v1"
NBA_API_KEY  = os.environ.get("MLB_BDL_API_KEY", "")

_rate_sem = asyncio.Semaphore(2)   # shared BDL key — keep burst low
_last_req_time: float = 0.0
_MIN_INTERVAL = 0.25               # max ~4 req/s from this client

CACHE_TTL = {
    "teams":         7 * 86400,
    "player":        2 * 3600,
    "player_search": 4 * 3600,
    "stats":         2 * 3600,
    "season_stats":  2 * 3600,
    "games":         3 * 3600,
}

CURRENT_NBA_SEASON = 2025  # 2025-26 season


async def _get(path: str, params: dict = None) -> dict:
    global _last_req_time
    headers = {"Authorization": NBA_API_KEY}
    url = f"{NBA_API_BASE}{path}"

    async with _rate_sem:
        elapsed = time.monotonic() - _last_req_time
        if elapsed < _MIN_INTERVAL:
            await asyncio.sleep(_MIN_INTERVAL - elapsed)
        try:
            async with httpx.AsyncClient(timeout=20) as c:
                resp = await c.get(url, headers=headers, params=params or {})
        except Exception as e:
            raise RuntimeError(f"NBA API network error: {e}")
        finally:
            _last_req_time = time.monotonic()

        if resp.status_code != 429:
            if resp.status_code >= 400:
                raise RuntimeError(f"NBA API error {resp.status_code}: {resp.text[:200]}")
            return resp.json()

        retry_after = min(int(resp.headers.get("retry-after", "5")), 10)

    log.warning(f"[NBA CLIENT] 429 on {path} — waiting {retry_after}s")
    await asyncio.sleep(retry_after)

    async with _rate_sem:
        elapsed = time.monotonic() - _last_req_time
        if elapsed < _MIN_INTERVAL:
            await asyncio.sleep(_MIN_INTERVAL - elapsed)
        try:
            async with httpx.AsyncClient(timeout=20) as c:
                resp = await c.get(url, headers=headers, params=params or {})
        except Exception as e:
            raise RuntimeError(f"NBA API network error on retry: {e}")
        finally:
            _last_req_time = time.monotonic()

        if resp.status_code >= 400:
            raise RuntimeError(f"NBA API error {resp.status_code}: {resp.text[:200]}")
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
        return await db.nba_cache.find_one({"key": key}, {"_id": 0})
    except Exception:
        return None


async def _cache_set(key: str, data) -> None:
    try:
        await db.nba_cache.update_one(
            {"key": key},
            {"$set": {"key": key, "data": data, "ts": datetime.now(timezone.utc).isoformat()}},
            upsert=True,
        )
    except Exception:
        pass


async def search_players(query: str, limit: int = 15) -> list:
    cache_key = f"search3:{query.lower()}"
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
            log.warning(f"[NBA SEARCH] {e}")
            break
        results.extend(data.get("data", []))
        meta = data.get("meta", {})
        cursor = meta.get("next_cursor")
        if not cursor or len(results) >= limit:
            break

    # BDL search only matches single name tokens — fall back to last name
    if not results and " " in query:
        last_name = query.rsplit(" ", 1)[-1]
        try:
            data = await _get("/players", {"search": last_name, "per_page": 25})
        except Exception as e:
            log.warning(f"[NBA SEARCH fallback] {e}")
        else:
            rows = data.get("data", [])
            q_tokens = query.lower().split()
            rows.sort(key=lambda p: sum(1 for t in q_tokens if t in (p.get("full_name") or f"{p.get('first_name','')} {p.get('last_name','')}").lower()), reverse=True)
            results = rows

    if results:
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
        log.warning(f"[NBA PLAYER] {e}")
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
        log.warning(f"[NBA TEAMS] {e}")
        return []


async def get_player_game_logs(player_id: int, season: int = CURRENT_NBA_SEASON) -> list:
    """Fetch per-game stats for a player in a given season, newest-first."""
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
            log.warning(f"[NBA STATS] player={player_id} season={season}: {e}")
            break
        rows = data.get("data", [])
        all_stats.extend(rows)
        meta = data.get("meta", {})
        cursor = meta.get("next_cursor")
        if not cursor:
            break

    # Transform to unified schema, newest-first
    logs = []
    for row in all_stats:
        game = row.get("game") or {}
        date_str = (game.get("date") or "")[:10]
        home_team   = game.get("home_team") or {}
        visitor_team = game.get("visitor_team") or {}
        home_team_id    = home_team.get("id")
        visitor_team_id = visitor_team.get("id")
        player_team_id  = (row.get("team") or {}).get("id")
        is_home = player_team_id == home_team_id
        venue   = "home" if is_home else "away"

        # Opponent abbreviation / name
        opp_team  = visitor_team if is_home else home_team
        opp_abbr  = opp_team.get("abbreviation") or opp_team.get("name") or None

        # W/L from game scores
        home_sc = game.get("home_team_score")
        vis_sc  = game.get("visitor_team_score")
        won = None
        if home_sc is not None and vis_sc is not None:
            won = (home_sc > vis_sc) if is_home else (vis_sc > home_sc)

        # Minutes played: "37:24" → float
        min_str = row.get("min") or "0"
        try:
            parts = str(min_str).split(":")
            minutes = int(parts[0]) + (int(parts[1]) / 60 if len(parts) > 1 else 0)
        except Exception:
            minutes = 0.0

        fg3m = (row.get("fg3m") or 0)
        pts   = (row.get("pts") or 0)
        reb   = (row.get("reb") or 0)
        ast   = (row.get("ast") or 0)
        stl   = (row.get("stl") or 0)
        blk   = (row.get("blk") or 0)
        tov   = (row.get("turnover") or 0)
        fgm   = (row.get("fgm") or 0)
        fga   = (row.get("fga") or 0)
        ftm   = (row.get("ftm") or 0)
        fta   = (row.get("fta") or 0)
        oreb  = (row.get("oreb") or 0)
        dreb  = (row.get("dreb") or 0)
        pf    = (row.get("pf") or 0)

        logs.append({
            "date":            date_str,
            "game_id":         game.get("id"),
            "venue":           venue,
            "opponent":        opp_abbr,
            "won":             won,
            "minutes":         round(minutes, 1),
            # Core counting stats
            "pts":             pts,
            "reb":             reb,
            "ast":             ast,
            "stl":             stl,
            "blk":             blk,
            "tov":             tov,
            "fg3m":            fg3m,
            "fgm":             fgm,
            "fga":             fga,
            "ftm":             ftm,
            "fta":             fta,
            "oreb":            oreb,
            "dreb":            dreb,
            "pf":              pf,
            # Combo props
            "pts_reb_ast":     pts + reb + ast,
            "pts_reb":         pts + reb,
            "pts_ast":         pts + ast,
            "reb_ast":         reb + ast,
            "stl_blk":         stl + blk,
            # Fantasy points (DraftKings scoring)
            "fantasy_pts":     pts * 1.0 + reb * 1.25 + ast * 1.5 + stl * 2.0 + blk * 2.0 - tov * 0.5 + (1.5 if pts >= 10 and reb >= 10 else 0) + (1.5 if pts >= 10 and ast >= 10 else 0),
            "_source": "bdl",
        })

    # Sort newest-first
    logs.sort(key=lambda x: x.get("date", ""), reverse=True)
    # Only cache non-empty results — a transient 429 must not poison the cache
    if logs:
        await _cache_set(cache_key, logs)
    return logs


async def get_season_averages(player_id: int, season: int = CURRENT_NBA_SEASON) -> Optional[dict]:
    cache_key = f"season_avg:{player_id}:{season}"
    cached = await _cache_get(cache_key)
    if _cache_fresh(cached, CACHE_TTL["season_stats"]):
        return cached["data"]
    try:
        data = await _get("/season_averages", {"season": season, "player_id": player_id})
        avgs = (data.get("data") or [{}])[0] if data.get("data") else {}
        await _cache_set(cache_key, avgs)
        return avgs
    except Exception as e:
        log.warning(f"[NBA SEASON AVG] {e}")
        return {}


async def get_player_next_match(player_id: int, season: int = CURRENT_NBA_SEASON) -> dict:
    """Get the next upcoming NBA game for a player's team.
    Returns {found, gameId, date, venue, opponent} or {found: False}.
    Cache is bypassed if the stored match date is before today (stale past game).
    """
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    cache_key = f"nba_next:{player_id}"
    cached = await _cache_get(cache_key)
    if cached:
        stored = cached.get("data", {})
        if stored.get("found") and (stored.get("date", "") or "") >= today_str:
            return stored
        if not stored.get("found") and cached.get("ts", ""):
            # Re-fetch if stale (cache may be old "not found")
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(str(cached["ts"])).replace(tzinfo=timezone.utc)).total_seconds()
            if age < 900:
                return stored

    player = await get_player(player_id)
    if not player:
        return {"found": False}
    team = player.get("team") or {}
    team_id = team.get("id")
    if not team_id:
        return {"found": False}

    try:
        data = await _get("/games", {
            "team_ids[]": team_id,
            "seasons[]":  season,
            "start_date": today_str,
            "per_page":   5,
        })
    except Exception as e:
        log.warning(f"[NBA NEXT MATCH] player={player_id}: {e}")
        return {"found": False}

    games = data.get("data", [])
    future = [g for g in games
              if (g.get("date") or "")[:10] >= today_str
              and (g.get("status") or "").upper() != "FINAL"]
    future.sort(key=lambda g: g.get("date", ""))

    if not future:
        result = {"found": False}
        await _cache_set(cache_key, result)
        return result

    g = future[0]
    home_team    = g.get("home_team") or {}
    visitor_team = g.get("visitor_team") or {}
    is_home      = home_team.get("id") == team_id
    opp          = visitor_team if is_home else home_team

    result = {
        "found":    True,
        "gameId":   g.get("id"),
        "date":     (g.get("date") or "")[:10],
        "venue":    "home" if is_home else "away",
        "opponent": {
            "id":           opp.get("id"),
            "name":         opp.get("full_name") or opp.get("name") or "",
            "abbreviation": opp.get("abbreviation") or "",
        },
    }
    log.info(f"[NBA NEXT MATCH] player={player_id} → {result['date']} vs {result['opponent']['name']} ({result['venue']})")
    await _cache_set(cache_key, result)
    return result
