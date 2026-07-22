"""
BallDontLie NHL API client with rate-limiting and MongoDB caching.
Base URL: https://api.balldontlie.io/nhl/v1
ALL-ACCESS tier: full player + game + box_score endpoints available.

Season format: integer year (2025 = 2024-25 season, 2024 = 2023-24 season).
Player stats: /players/{id}/season_stats?season={year}
Game logs: /games?team_ids[]={id}&seasons[]={year} → /box_scores?game_ids[]=...
"""
import asyncio
import time
import os
import logging
from datetime import datetime, timezone
from typing import Optional

import httpx
from config import db

log = logging.getLogger("nhl_client")

NHL_API_BASE = "https://api.balldontlie.io/nhl/v1"
NHL_API_KEY  = os.environ.get("MLB_BDL_API_KEY", "")

_rate_sem = asyncio.Semaphore(2)   # shared BDL key — keep burst low
_last_req_time: float = 0.0
_MIN_INTERVAL = 0.25               # max ~4 req/s from this client

CACHE_TTL = {
    "teams":         7 * 86400,
    "player":        2 * 3600,
    "player_search": 4 * 3600,
    "stats":         2 * 3600,
    "season_stats":  6 * 3600,
    "games":         6 * 3600,
}

# Current NHL season: 2025 = 2024-25 season (ended June 2025)
CURRENT_NHL_SEASON = 2025


async def _get(path: str, params=None) -> dict:
    """GET request — params may be a dict or a list of (key, value) tuples
    (the latter supports repeated keys like game_ids[])."""
    global _last_req_time
    headers = {"Authorization": NHL_API_KEY}
    # If path already contains a query string (pre-built), use it directly
    if "?" in path:
        url = f"{NHL_API_BASE}{path}"
        params = None
    else:
        url = f"{NHL_API_BASE}{path}"

    async with _rate_sem:
        elapsed = time.monotonic() - _last_req_time
        if elapsed < _MIN_INTERVAL:
            await asyncio.sleep(_MIN_INTERVAL - elapsed)
        try:
            async with httpx.AsyncClient(timeout=20) as c:
                resp = await c.get(url, headers=headers, params=params or {})
        except Exception as e:
            raise RuntimeError(f"NHL API network error: {e}")
        finally:
            _last_req_time = time.monotonic()

        if resp.status_code != 429:
            if resp.status_code >= 400:
                raise RuntimeError(f"NHL API error {resp.status_code}: {resp.text[:200]}")
            return resp.json()

        retry_after = min(int(resp.headers.get("retry-after", "5")), 10)

    log.warning(f"[NHL CLIENT] 429 on {path} — waiting {retry_after}s")
    await asyncio.sleep(retry_after)

    async with _rate_sem:
        elapsed = time.monotonic() - _last_req_time
        if elapsed < _MIN_INTERVAL:
            await asyncio.sleep(_MIN_INTERVAL - elapsed)
        try:
            async with httpx.AsyncClient(timeout=20) as c:
                resp = await c.get(url, headers=headers, params=params or {})
        except Exception as e:
            raise RuntimeError(f"NHL API network error on retry: {e}")
        finally:
            _last_req_time = time.monotonic()

        if resp.status_code >= 400:
            raise RuntimeError(f"NHL API error {resp.status_code}: {resp.text[:200]}")
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
        return await db.nhl_cache.find_one({"key": key}, {"_id": 0})
    except Exception:
        return None


async def _cache_set(key: str, data) -> None:
    try:
        await db.nhl_cache.update_one(
            {"key": key},
            {"$set": {"key": key, "data": data, "ts": datetime.now(timezone.utc).isoformat()}},
            upsert=True,
        )
    except Exception:
        pass


async def _get_all_current_players(season: int = CURRENT_NHL_SEASON) -> list:
    """Fetch all NHL players for a given season and cache them.
    BDL NHL ignores the search= param so we cache the full roster and search locally.
    Uses cursor pagination; ~8-10 pages for a full NHL season roster.
    """
    cache_key = f"all_players:{season}"
    cached = await _cache_get(cache_key)
    if _cache_fresh(cached, 7 * 86400):  # 7-day TTL
        return cached["data"]

    all_players: list = []
    cursor = None
    for _ in range(20):  # safety cap — 20 × 100 = 2000 players max
        params: list = [("seasons[]", season), ("per_page", 100)]
        if cursor:
            params.append(("cursor", cursor))
        try:
            data = await _get("/players", params)
        except Exception as e:
            log.warning(f"[NHL ALL PLAYERS] page fetch failed: {e}")
            break
        rows = data.get("data", [])
        for p in rows:
            if "full_name" not in p:
                p["full_name"] = f"{p.get('first_name','')} {p.get('last_name','')}".strip()
        all_players.extend(rows)
        cursor = data.get("meta", {}).get("next_cursor")
        if not cursor:
            break

    if all_players:
        log.info(f"[NHL ALL PLAYERS] cached {len(all_players)} players for season {season}")
        await _cache_set(cache_key, all_players)
    return all_players


async def search_players(query: str, limit: int = 15) -> list:
    """Local fuzzy search from cached full-season player list.
    BDL NHL /players?search= silently ignores the query and returns all players
    sorted by ID — so we fetch the full current-season roster once, cache it,
    and do token-overlap scoring locally.
    """
    cache_key = f"nhl_search:{query.lower()}"
    cached = await _cache_get(cache_key)
    if _cache_fresh(cached, CACHE_TTL["player_search"]):
        return cached["data"][:limit]

    all_players = await _get_all_current_players()
    if not all_players:
        all_players = await _get_all_current_players(CURRENT_NHL_SEASON - 1)

    q_tokens = query.lower().split()

    def _score(p: dict) -> int:
        name = p.get("full_name", "").lower()
        return sum(1 for t in q_tokens if t in name)

    matches = [p for p in all_players if _score(p) > 0]
    matches.sort(key=_score, reverse=True)
    results = matches[:limit]

    if results:
        await _cache_set(cache_key, results)
    return results


async def get_player(player_id: int) -> Optional[dict]:
    cache_key = f"player:{player_id}"
    cached = await _cache_get(cache_key)
    if _cache_fresh(cached, CACHE_TTL["player"]):
        return cached["data"]
    try:
        data = await _get("/players", [("player_ids[]", player_id), ("per_page", 1)])
        players = data.get("data") or []
        player = players[0] if players else {}
        if player:
            await _cache_set(cache_key, player)
        return player or None
    except Exception as e:
        log.warning(f"[NHL PLAYER] {e}")
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
        log.warning(f"[NHL TEAMS] {e}")
        return []


async def get_player_season_stats(player_id: int, season: int = CURRENT_NHL_SEASON) -> dict:
    """Get season totals/averages for a player.
    Endpoint: GET /players/{id}/season_stats?season={year}
    Returns [{name: 'goals', value: 44}, ...] — converted to flat dict.
    """
    cache_key = f"season_stats4:{player_id}:{season}"
    cached = await _cache_get(cache_key)
    if _cache_fresh(cached, CACHE_TTL["season_stats"]):
        return cached["data"]
    try:
        data = await _get(f"/players/{player_id}/season_stats", {"season": season})
        raw = data.get("data") or []
        # Convert [{name, value}, ...] → flat dict
        stats = {item["name"]: item["value"] for item in raw if "name" in item and "value" in item}
        if stats:
            await _cache_set(cache_key, stats)
        return stats
    except Exception as e:
        log.warning(f"[NHL SEASON STATS] {e}")
        return {}


def _transform_nhl_log(row: dict) -> dict:
    """Transform a BDL NHL box_score row into unified schema."""
    game = row.get("game") or {}
    date_str = (game.get("game_date") or "")[:10]
    home_team_id = (game.get("home_team") or {}).get("id")
    player_team_id = (row.get("team") or {}).get("id")
    venue = "home" if player_team_id == home_team_id else "away"

    goals   = (row.get("goals") or 0)
    assists = (row.get("assists") or 0)
    # BDL box_score uses shots_on_goal (not shots)
    shots   = (row.get("shots_on_goal") or row.get("shots") or 0)
    blocks  = (row.get("blocked_shots") or 0)
    hits    = (row.get("hits") or 0)
    pm      = (row.get("plus_minus") or 0)
    pim     = (row.get("penalty_minutes") or 0)
    toi_str = row.get("time_on_ice") or "0:00"
    try:
        toi_parts = str(toi_str).split(":")
        toi = int(toi_parts[0]) + (int(toi_parts[1]) / 60 if len(toi_parts) > 1 else 0)
    except Exception:
        toi = 0.0

    # Goalie stats (present if player is goalie)
    saves         = (row.get("saves") or 0)
    goals_against = (row.get("goals_against") or 0)
    shots_against = saves + goals_against
    save_pct      = round(saves / shots_against, 3) if shots_against > 0 else 0.0

    return {
        "date":          date_str,
        "game_id":       game.get("id"),
        "venue":         venue,
        "goals":         goals,
        "assists":       assists,
        "points":        goals + assists,
        "shots":         shots,
        "blocked_shots": blocks,
        "hits":          hits,
        "plus_minus":    pm,
        "pim":           pim,
        "toi":           round(toi, 2),
        "saves":         saves,
        "goals_against": goals_against,
        "save_pct":      save_pct,
        "shots_against": shots_against,
        "_source": "bdl",
    }


async def get_player_game_logs(player_id: int, season: int = CURRENT_NHL_SEASON) -> list:
    """Fetch per-game stats for a player via games + box_scores.
    Strategy:
      1. Get player's current team from /players list
      2. Fetch that team's completed games for the season
      3. Batch-fetch box_scores (10 games per call)
      4. Filter rows by player_id and transform
    Cache key gl4: busts stale entries from old broken implementation.
    """
    cache_key = f"gl4:{player_id}:{season}"
    cached = await _cache_get(cache_key)
    if _cache_fresh(cached, CACHE_TTL["stats"]):
        return cached["data"]

    # Step 1: Get player to find team_id
    player = await get_player(player_id)
    if not player:
        log.warning(f"[NHL GAME LOGS] player {player_id} not found")
        return []

    # Most-recent team from player.teams array
    teams = player.get("teams") or []
    if not teams:
        log.warning(f"[NHL GAME LOGS] no team data for player {player_id}")
        return []
    teams_sorted = sorted(teams, key=lambda t: t.get("season", 0), reverse=True)
    team_id = teams_sorted[0].get("id")
    if not team_id:
        return []

    # Step 2: Get completed games for this team this season
    games_cache_key = f"nhl_games:{team_id}:{season}"
    games_cached = await _cache_get(games_cache_key)
    if _cache_fresh(games_cached, CACHE_TTL["games"]):
        target_games = games_cached["data"]
    else:
        try:
            games_data = await _get("/games", [
                ("team_ids[]", team_id),
                ("seasons[]", season),
                ("per_page", 100),
            ])
        except Exception as e:
            log.warning(f"[NHL GAME LOGS] games fetch failed: {e}")
            return []

        all_games = sorted(
            games_data.get("data", []),
            key=lambda g: g.get("game_date", ""),
            reverse=True,
        )
        # Only completed games (game_state = "OFF" = Official/Final)
        target_games = [g for g in all_games if g.get("game_state") in ("OFF", "Final", "F")][:30]
        if target_games:
            await _cache_set(games_cache_key, target_games)

    if not target_games:
        log.info(f"[NHL GAME LOGS] no completed games for team={team_id} season={season}")
        return []

    game_ids = [g["id"] for g in target_games]

    # Step 3: Batch-fetch box_scores (10 game IDs per call)
    all_rows = []
    batch_size = 10
    for i in range(0, len(game_ids), batch_size):
        batch = game_ids[i:i + batch_size]
        params = [("game_ids[]", gid) for gid in batch] + [("per_page", 100)]
        try:
            data = await _get("/box_scores", params)
        except Exception as e:
            log.warning(f"[NHL GAME LOGS] box_scores batch {i//batch_size} failed: {e}")
            continue
        rows = data.get("data", [])
        # Filter to this specific player
        player_rows = [r for r in rows if (r.get("player") or {}).get("id") == player_id]
        all_rows.extend(player_rows)

    if not all_rows:
        log.info(f"[NHL GAME LOGS] no box_score rows for player={player_id} season={season}")
        return []

    logs = sorted(
        [_transform_nhl_log(r) for r in all_rows],
        key=lambda l: l.get("date", ""),
        reverse=True,
    )

    if logs:
        await _cache_set(cache_key, logs)
    return logs


async def get_player_next_match(player_id: int, season: int = CURRENT_NHL_SEASON) -> dict:
    """Get the next upcoming NHL game for a player's team.
    Returns {found, gameId, date, venue, opponent} or {found: False}.
    Cache is bypassed if the stored date is before today.
    """
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    cache_key = f"nhl_next:{player_id}"
    cached = await _cache_get(cache_key)
    if cached:
        stored = cached.get("data", {})
        if stored.get("found") and (stored.get("date", "") or "") >= today_str:
            return stored
        if not stored.get("found") and cached.get("ts", ""):
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(str(cached["ts"])).replace(tzinfo=timezone.utc)).total_seconds()
            if age < 900:
                return stored

    player = await get_player(player_id)
    if not player:
        return {"found": False}
    teams = player.get("teams") or []
    if not teams:
        return {"found": False}
    teams_sorted = sorted(teams, key=lambda t: t.get("season", 0), reverse=True)
    team_id = teams_sorted[0].get("id")
    if not team_id:
        return {"found": False}

    try:
        data = await _get("/games", [
            ("team_ids[]", team_id),
            ("seasons[]",  season),
            ("per_page",   100),
        ])
    except Exception as e:
        log.warning(f"[NHL NEXT MATCH] player={player_id}: {e}")
        return {"found": False}

    all_games = data.get("data", [])
    # BDL NHL: game_state "OFF" = Final; "Pre-Game"/"Scheduled"/"Live" = not finished
    future = [g for g in all_games
              if (g.get("game_date") or "")[:10] >= today_str
              and g.get("game_state") not in ("OFF", "Final", "F")]
    future.sort(key=lambda g: g.get("game_date", ""))

    if not future:
        result = {"found": False}
        await _cache_set(cache_key, result)
        return result

    g        = future[0]
    home_t   = g.get("home_team") or {}
    away_t   = g.get("away_team") or {}
    is_home  = home_t.get("id") == team_id
    opp      = away_t if is_home else home_t

    result = {
        "found":    True,
        "gameId":   g.get("id"),
        "date":     (g.get("game_date") or "")[:10],
        "venue":    "home" if is_home else "away",
        "opponent": {
            "id":           opp.get("id"),
            "name":         opp.get("full_name") or opp.get("name") or "",
            "abbreviation": opp.get("abbreviation") or "",
        },
    }
    log.info(f"[NHL NEXT MATCH] player={player_id} → {result['date']} vs {result['opponent']['name']} ({result['venue']})")
    await _cache_set(cache_key, result)
    return result
