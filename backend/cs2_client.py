"""
BallDontLie CS2 API client — same API key as MLB (MLB_BDL_API_KEY).
Base URL: https://api.balldontlie.io/cs/v1
Per-map player stats are the primary data source for prop prediction.
"""
import asyncio
import time
import os
import logging
from datetime import datetime, timezone
from typing import Optional

import httpx
from config import db

log = logging.getLogger("cs2_client")

CS2_API_BASE = "https://api.balldontlie.io/cs/v1"
CS2_API_KEY  = os.environ.get("MLB_BDL_API_KEY", "951b8b73-a036-4b30-924f-19f322766545")

_rate_sem      = asyncio.Semaphore(3)
_last_req_time: float = 0.0
_MIN_INTERVAL  = 0.15   # 600 req/min paid tier

CACHE_TTL = {
    "player_search": 6  * 3600,
    "player_maps":   1  * 3600,
    "teams":         24 * 3600,
    "rankings":      3600,
}


async def _get(path: str, params: dict = None) -> dict:
    global _last_req_time
    headers = {"Authorization": CS2_API_KEY}
    url = f"{CS2_API_BASE}{path}"

    async with _rate_sem:
        elapsed = time.monotonic() - _last_req_time
        if elapsed < _MIN_INTERVAL:
            await asyncio.sleep(_MIN_INTERVAL - elapsed)
        try:
            async with httpx.AsyncClient(timeout=20) as c:
                resp = await c.get(url, headers=headers, params=params or {})
        except Exception as e:
            raise RuntimeError(f"CS2 API network error: {e}")
        finally:
            _last_req_time = time.monotonic()

        if resp.status_code != 429:
            if resp.status_code >= 400:
                raise RuntimeError(f"CS2 API error {resp.status_code}: {resp.text[:200]}")
            return resp.json()

    # 429 — wait outside semaphore
    print(f"[CS2 CLIENT] 429 on {path} — waiting 10s before retry")
    await asyncio.sleep(10)
    return await _get(path, params)


async def _cache_get(key: str) -> Optional[dict]:
    try:
        return await db.cs2_cache.find_one({"key": key}, {"_id": 0})
    except Exception:
        return None


async def _cache_set(key: str, data) -> None:
    try:
        await db.cs2_cache.update_one(
            {"key": key},
            {"$set": {"key": key, "data": data, "_ts": datetime.now(timezone.utc).isoformat()}},
            upsert=True,
        )
    except Exception:
        pass


def _fresh(doc: Optional[dict], ttl: int) -> bool:
    if not doc or not doc.get("_ts"):
        return False
    try:
        ts = datetime.fromisoformat(doc["_ts"])
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - ts).total_seconds() < ttl
    except Exception:
        return False


async def search_players(query: str) -> list:
    """Search CS2 players by nickname."""
    key = f"cs2_psearch_{query.lower().strip()}"
    doc = await _cache_get(key)
    if _fresh(doc, CACHE_TTL["player_search"]) and doc.get("data") is not None:
        return doc["data"]

    results = []
    try:
        r = await _get("/players", {"search": query.strip(), "per_page": 25})
        results = [
            {
                "id":       p["id"],
                "nickname": p.get("nickname", ""),
                "fullName": (p.get("full_name") or
                             f"{p.get('first_name','') or ''} {p.get('last_name','') or ''}".strip()),
                "team":     p.get("team"),
                "isActive": p.get("is_active"),
                "age":      p.get("age"),
            }
            for p in r.get("data", [])
            if p.get("id") and p.get("nickname")
        ]
        await _cache_set(key, results)
    except Exception as e:
        log.error(f"CS2 player search error: {e}")
    return results


async def get_player_recent_map_stats(player_id: int, team_id: int, limit: int = 30) -> list:
    """
    Fetch recent per-map stats for a player.
    Strategy:
      1. Get team's last 20 finished matches
      2. For each match → get match_maps
      3. For each map → get player_match_map_stats filtered to player_id
    Returns list of per-map dicts, newest first.
    """
    key = f"cs2_pmaps_{player_id}_{team_id}"
    doc = await _cache_get(key)
    if _fresh(doc, CACHE_TTL["player_maps"]) and doc.get("data") is not None:
        return doc["data"]

    map_stats = []
    try:
        matches_r = await _get("/matches", {
            "team_ids[]": team_id,
            "per_page":   20,
            "status":     "finished",
        })
        matches = matches_r.get("data", [])

        for match in matches:
            if len(map_stats) >= limit:
                break
            match_id = match.get("id")
            if not match_id:
                continue

            slug       = match.get("slug", "")
            tournament = match.get("tournament") or {}
            t_name     = tournament.get("name", "")
            t_tier     = tournament.get("tier", "")

            # Parse date from slug (format: team1-vs-team2-DD-MM-YYYY)
            try:
                parts = slug.rsplit("-", 3)
                date_str = f"{parts[-1]}-{parts[-2]:>02}-{parts[-3]:>02}" if len(parts) >= 4 else ""
            except Exception:
                date_str = ""

            # Get maps for this match
            maps_r = await _get("/match_maps", {"match_ids[]": match_id, "per_page": 10})
            maps   = maps_r.get("data", [])

            for map_obj in maps:
                if len(map_stats) >= limit:
                    break
                map_id     = map_obj.get("id")
                map_name   = map_obj.get("map_name", "")
                map_number = map_obj.get("map_number", 0)
                winner_id  = (map_obj.get("winner") or {}).get("id")

                # Build opponent name from scores context
                t1_score = map_obj.get("team1_score", 0) or 0
                t2_score = map_obj.get("team2_score", 0) or 0

                pmms_r = await _get("/player_match_map_stats", {
                    "match_map_id": map_id,
                    "per_page":     20,
                })
                pmms = pmms_r.get("data", [])

                for stat in pmms:
                    if stat.get("player", {}).get("id") == player_id:
                        map_stats.append({
                            "matchId":          match_id,
                            "mapId":            map_id,
                            "mapName":          map_name,
                            "mapNumber":        map_number,
                            "tournament":       t_name,
                            "tier":             t_tier,
                            "date":             date_str,
                            "wonMap":           winner_id == team_id,
                            "kills":            stat.get("kills") or 0,
                            "deaths":           stat.get("deaths") or 0,
                            "assists":          stat.get("assists") or 0,
                            "adr":              float(stat.get("adr") or 0),
                            "kast":             float(stat.get("kast") or 0),
                            "rating":           float(stat.get("rating") or 0),
                            "headshotPct":      float(stat.get("headshot_percentage") or 0),
                            "firstKills":       stat.get("first_kills") or 0,
                            "firstDeaths":      stat.get("first_deaths") or 0,
                            "clutchesWon":      stat.get("clutches_won") or 0,
                        })
                        break

                await asyncio.sleep(0.05)

        await _cache_set(key, map_stats)
    except Exception as e:
        log.error(f"CS2 player map stats error: {e}")

    return map_stats


async def search_teams(query: str) -> list:
    """Search CS2 teams by name, returning clean results."""
    key = f"cs2_tsearch_{query.lower().strip()}"
    doc = await _cache_get(key)
    if _fresh(doc, CACHE_TTL["teams"]) and doc.get("data") is not None:
        return doc["data"]
    try:
        r = await _get("/teams", {"search": query.strip(), "per_page": 20})
        results = [
            {"id": t["id"], "name": t.get("name", ""), "shortName": t.get("short_name")}
            for t in r.get("data", [])
            if t.get("id") and t.get("name") and t.get("slug")  # slug present = real team
        ]
        await _cache_set(key, results)
        return results
    except Exception as e:
        log.error(f"CS2 team search error: {e}")
        return []


async def get_player_recent_match_stats(player_id: int, team_id: int, limit: int = 15) -> list:
    """
    Fetch per-MATCH aggregated stats for maps_1_2_* props.
    For each match: sums kills/deaths/assists on map1+map2; averages ADR/rating.
    Returns list of match-level dicts, newest first.
    """
    key = f"cs2_pmatches_{player_id}_{team_id}"
    doc = await _cache_get(key)
    if _fresh(doc, CACHE_TTL["player_maps"]) and doc.get("data") is not None:
        return doc["data"]

    match_stats = []
    try:
        matches_r = await _get("/matches", {
            "team_ids[]": team_id,
            "per_page":   25,
            "status":     "finished",
        })
        matches = matches_r.get("data", [])

        for match in matches:
            if len(match_stats) >= limit:
                break
            match_id = match.get("id")
            if not match_id:
                continue

            slug       = match.get("slug", "")
            tournament = match.get("tournament") or {}
            t_name     = tournament.get("name", "")
            t_tier     = tournament.get("tier", "")

            try:
                parts    = slug.rsplit("-", 3)
                date_str = f"{parts[-1]}-{parts[-2]:>02}-{parts[-3]:>02}" if len(parts) >= 4 else ""
            except Exception:
                date_str = ""

            maps_r = await _get("/match_maps", {"match_ids[]": match_id, "per_page": 10})
            maps   = maps_r.get("data", [])
            if not maps:
                continue

            # Collect per-map player stats
            map_player_stats = {}  # map_number → stat dict
            for map_obj in maps:
                map_id     = map_obj.get("id")
                map_number = map_obj.get("map_number", 0)
                map_name   = map_obj.get("map_name", "")
                winner_id  = (map_obj.get("winner") or {}).get("id")

                pmms_r = await _get("/player_match_map_stats", {
                    "match_map_id": map_id,
                    "per_page":     20,
                })
                for stat in pmms_r.get("data", []):
                    if stat.get("player", {}).get("id") == player_id:
                        map_player_stats[map_number] = {
                            "mapNumber":    map_number,
                            "mapName":      map_name,
                            "wonMap":       winner_id == team_id,
                            "kills":        stat.get("kills") or 0,
                            "deaths":       stat.get("deaths") or 0,
                            "assists":      stat.get("assists") or 0,
                            "adr":          float(stat.get("adr") or 0),
                            "kast":         float(stat.get("kast") or 0),
                            "rating":       float(stat.get("rating") or 0),
                            "headshotPct":  float(stat.get("headshot_percentage") or 0),
                            "firstKills":   stat.get("first_kills") or 0,
                            "clutchesWon":  stat.get("clutches_won") or 0,
                        }
                        break
                await asyncio.sleep(0.04)

            if not map_player_stats:
                continue

            # Maps 1+2 aggregate
            m1 = map_player_stats.get(1, {})
            m2 = map_player_stats.get(2, {})
            m1m2_maps = [m for m in (m1, m2) if m]

            def _sum(field):
                return sum(m.get(field, 0) for m in m1m2_maps)

            def _avg(field):
                vals = [m.get(field, 0) for m in m1m2_maps if m.get(field, 0) > 0]
                return sum(vals) / len(vals) if vals else 0.0

            match_stats.append({
                "matchId":          match_id,
                "tournament":       t_name,
                "tier":             t_tier,
                "date":             date_str,
                "mapsPlayed":       len(map_player_stats),
                "maps":             list(map_player_stats.values()),
                # Maps 1-2 aggregates
                "maps_1_2_kills":   _sum("kills"),
                "maps_1_2_deaths":  _sum("deaths"),
                "maps_1_2_assists": _sum("assists"),
                "maps_1_2_adr":     round(_avg("adr"), 1),
                "maps_1_2_rating":  round(_avg("rating"), 2),
                # Map 1 only
                "map1_kills":       m1.get("kills", 0),
                "map2_kills":       m2.get("kills", 0),
                "wonMatch":         any(m.get("wonMap") for m in map_player_stats.values()),
            })

        await _cache_set(key, match_stats)
    except Exception as e:
        log.error(f"CS2 player match stats error: {e}")

    return match_stats


async def get_rankings(limit: int = 30) -> list:
    """Current HLTV-style team rankings."""
    key = "cs2_rankings"
    doc = await _cache_get(key)
    if _fresh(doc, CACHE_TTL["rankings"]) and doc.get("data") is not None:
        return doc["data"]
    try:
        r    = await _get("/rankings", {"per_page": limit})
        data = r.get("data", [])
        await _cache_set(key, data)
        return data
    except Exception as e:
        log.error(f"CS2 rankings error: {e}")
        return []
