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

_rate_sem = asyncio.Semaphore(3)   # paid tier: 600 req/min — allow 3 concurrent slots
_last_req_time: float = 0.0
_MIN_INTERVAL = 0.15  # seconds between requests — paid tier: 600 req/min ≈ 10/s; 0.15s is conservative

CACHE_TTL = {
    "teams":         7 * 86400,   # 7 days
    "player":        6 * 3600,    # 6 hours
    "player_search": 24 * 3600,   # 24 hours
    "stats":         2 * 3600,    # 2 hours (live season)
    "season_stats":  2 * 3600,
}


async def _get(path: str, params: dict = None) -> dict:
    """Single BDL request with rate-limiting.
    IMPORTANT: the 429-retry sleep happens OUTSIDE the semaphore so other
    requests are not blocked while we wait for the rate-limit window to reset."""
    global _last_req_time
    headers = {"Authorization": MLB_API_KEY}
    url = f"{MLB_API_BASE}{path}"

    # ── First attempt ─────────────────────────────────────────────────────────
    async with _rate_sem:
        elapsed = time.monotonic() - _last_req_time
        if elapsed < _MIN_INTERVAL:
            await asyncio.sleep(_MIN_INTERVAL - elapsed)
        try:
            async with httpx.AsyncClient(timeout=20) as c:
                resp = await c.get(url, headers=headers, params=params or {})
        except Exception as e:
            raise RuntimeError(f"MLB API network error: {e}")
        finally:
            _last_req_time = time.monotonic()

        if resp.status_code != 429:
            if resp.status_code >= 400:
                raise RuntimeError(f"MLB API error {resp.status_code}: {resp.text[:200]}")
            return resp.json()

        # Got a 429 — capture retry-after BEFORE releasing the semaphore
        retry_after = min(int(resp.headers.get("retry-after", "5")), 10)

    # ── Sleep OUTSIDE the semaphore so other slots stay available ─────────────
    log.warning(f"[MLB CLIENT] 429 on {path} — waiting {retry_after}s before retry")
    await asyncio.sleep(retry_after)

    # ── Retry attempt ─────────────────────────────────────────────────────────
    async with _rate_sem:
        elapsed = time.monotonic() - _last_req_time
        if elapsed < _MIN_INTERVAL:
            await asyncio.sleep(_MIN_INTERVAL - elapsed)
        try:
            async with httpx.AsyncClient(timeout=20) as c:
                resp = await c.get(url, headers=headers, params=params or {})
        except Exception as e:
            raise RuntimeError(f"MLB API network error on retry: {e}")
        finally:
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
    """Search BDL for players by name.

    BDL's /players?search= only matches on a single token — multi-word queries
    like "Noah Cameron" return 0 results even though the player exists.  We work
    around this by trying the full query first, then falling back to last-name-only
    and first-name-only searches, deduplicating by player id.
    """
    q = query.strip()
    key = f"mlb_ps:{q.lower()}"
    doc = await _cache_get(key)
    if _cache_fresh(doc, CACHE_TTL["player_search"]) and doc.get("data") is not None:
        return doc["data"]

    seen: dict = {}  # id → player

    async def _search(term: str) -> list:
        try:
            r = await _get("/players", {"search": term, "per_page": limit})
            return r.get("data", [])
        except Exception:
            return []

    # 1. Try full query
    for p in await _search(q):
        seen[p["id"]] = p

    # 2. ONLY if nothing found and query has multiple words, try last name then first name.
    #    Do NOT do a supplemental second call when the first call already returned results —
    #    that was doubling every multi-word search and causing a huge rate-limit queue.
    words = q.split()
    if not seen and len(words) > 1:
        for p in await _search(words[-1]):
            seen[p["id"]] = p
        if not seen:
            for p in await _search(words[0]):
                seen[p["id"]] = p

    players = list(seen.values())[:limit]
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


async def get_player_game_logs(player_id: int, season: int = 2026, limit: int = 30) -> list:
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


async def get_season_stats(player_id: int, season: int = 2026) -> Optional[dict]:
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


async def get_team_games(team_id: int, season: int = 2026) -> list:
    """Fetch completed regular-season games for a team, newest first.
    Used to enrich per-game stat tiles with opponent/date/venue/score.
    Cached 15 minutes — refreshes quickly during active season."""
    if not team_id:
        return []
    key = f"mlb_tg:{team_id}:{season}"
    doc = await _cache_get(key)
    if _cache_fresh(doc, 900) and doc.get("data") is not None:
        return doc["data"]

    all_games: list = []
    try:
        cursor = None
        for _ in range(4):  # up to 400 games — covers a full season
            params: dict = {"team_ids[]": team_id, "season": season, "per_page": 100}
            if cursor:
                params["cursor"] = cursor
            result = await _get("/games", params)
            batch = result.get("data", [])
            all_games.extend(batch)
            cursor = result.get("meta", {}).get("next_cursor")
            if not cursor or not batch:
                break
    except Exception as e:
        log.warning(f"[MLB CLIENT] get_team_games({team_id},{season}) failed: {e}")
        return []

    # Keep only completed regular-season games, sorted newest first
    regular = [
        g for g in all_games
        if g.get("season_type") in ("regular", None, "")
        and g.get("status") == "STATUS_FINAL"
    ]
    regular.sort(key=lambda g: g.get("date", ""), reverse=True)

    if regular:
        await _cache_set(key, regular)
    return regular


async def get_today_and_live_games(team_id: int, season: int = 2026) -> list:
    """Fetch today's and in-progress games for a team.
    Uses a 2-minute cache so the live-tracking loop stays fresh without hammering BDL.

    BDL returns games oldest-first, so we MUST use the dates[] filter to target
    today specifically — otherwise per_page=10 would give April games, not May.
    We also scan the most-recent page of games for any STATUS_IN_PROGRESS game
    as a safety net (in case a game runs past midnight UTC)."""
    if not team_id:
        return []
    from datetime import date as _date
    today = _date.today().isoformat()
    key = f"mlb_live:{team_id}:{season}:{today}"
    doc = await _cache_get(key)
    if _cache_fresh(doc, 120) and doc.get("data") is not None:
        return doc["data"]

    relevant: list = []
    try:
        # Primary: request only today's game by date
        result = await _get("/games", {
            "team_ids[]": team_id,
            "season": season,
            "per_page": 5,
            "dates[]": today,
        })
        for g in result.get("data", []):
            status = (g.get("status") or "").upper()
            gdate = (g.get("date") or "")[:10]
            if "IN_PROGRESS" in status or "LIVE" in status or gdate == today:
                relevant.append(g)
    except Exception as e:
        log.warning(f"[MLB CLIENT] get_today_and_live_games dates filter failed ({team_id}): {e}")

    # Fallback / safety net: get the last page of season games — catches live
    # games that started yesterday or any in-progress game BDL didn't date-match.
    if not relevant:
        try:
            # Paginate to the most recent games (BDL is oldest-first, so we
            # follow cursors until the last page which has the newest games)
            cursor = None
            last_batch: list = []
            for _ in range(20):  # max 20 pages × 10 = 200 games
                params: dict = {"team_ids[]": team_id, "season": season, "per_page": 10}
                if cursor:
                    params["cursor"] = cursor
                r = await _get("/games", params)
                batch = r.get("data", [])
                if batch:
                    last_batch = batch
                next_cursor = r.get("meta", {}).get("next_cursor")
                if not next_cursor:
                    break
                cursor = next_cursor
            for g in last_batch:
                status = (g.get("status") or "").upper()
                gdate = (g.get("date") or "")[:10]
                if "IN_PROGRESS" in status or "LIVE" in status or gdate == today:
                    relevant.append(g)
        except Exception as e2:
            log.warning(f"[MLB CLIENT] get_today_and_live_games fallback failed ({team_id}): {e2}")

    # Only cache when no game is actively in progress — live games must not
    # be cached so the loop always sees the latest status and score.
    game_is_live = any("IN_PROGRESS" in (g.get("status") or "").upper() for g in relevant)
    if relevant and not game_is_live:
        await _cache_set(key, relevant)
    return relevant


async def get_game_player_stats(player_id: int, game_id: int, season: int = 2026,
                                live: bool = False) -> Optional[dict]:
    """Fetch a player's stats for a specific game.

    When `live=True` (game still in progress) we skip the cache entirely so
    every loop iteration reads the latest values from BDL.  Completed games
    are cached for 24 h (they won't change).
    """
    key = f"mlb_gps:{player_id}:{game_id}"
    if not live:
        doc = await _cache_get(key)
        if _cache_fresh(doc, 86400) and doc.get("data") is not None:
            return doc["data"]
    try:
        result = await _get("/stats", {
            "player_ids[]": player_id,
            "game_ids[]": game_id,
            "season": season,
        })
        stats_list = result.get("data", [])
        data = stats_list[0] if stats_list else None
    except Exception as e:
        log.warning(f"[MLB CLIENT] get_game_player_stats({player_id},{game_id}) failed: {e}")
        return None
    # Only cache completed-game stats — live stats must never be cached
    if data is not None and not live:
        await _cache_set(key, data)
    return data


async def get_game_by_teams(home_abbrev: str, away_abbrev: str, season: int = 2026) -> Optional[dict]:
    """Find a specific game by team abbreviations (used for settlement)."""
    try:
        result = await _get("/games", {"season": season, "per_page": 50})
        for g in result.get("data", []):
            h = g.get("home_team", {}).get("abbreviation", "")
            a = g.get("away_team", {}).get("abbreviation", "")
            if (h == home_abbrev and a == away_abbrev) or (h == away_abbrev and a == home_abbrev):
                return g
    except Exception:
        pass
    return None
