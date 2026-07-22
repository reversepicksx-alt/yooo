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
CS2_API_KEY  = os.environ.get("MLB_BDL_API_KEY", "")
if not CS2_API_KEY:
    print("[CS2] WARNING: MLB_BDL_API_KEY env var not set — CS2 API calls will fail")

_rate_sem      = asyncio.Semaphore(6)   # paid tier: 600 req/min → 6 concurrent safe
_last_req_time: float = 0.0
_MIN_INTERVAL  = 0.10   # 600 req/min ÷ 6 slots = 100ms minimum between requests

CACHE_TTL = {
    "player_search": 6  * 3600,
    "player_maps":   6  * 3600,   # 6h — matches happen infrequently
    "teams":         24 * 3600,
    "rankings":      3600,
    # Settle-path caches — short TTL but SHARED across every user + every pick
    # for the same team/match. Without these, 5 users × 3 live picks each on
    # the same team = 15 parallel calls to /matches + 375 calls to /match_maps
    # and /player_match_map_stats per poll, instantly hitting the 600/min cap.
    "team_matches": 180,          # 3 min — finished match list per team
    "match_maps":   600,          # 10 min — per-match map+stat bundle
}


async def _get(path: str, params: dict = None, _retry: int = 0) -> dict:
    global _last_req_time
    headers = {"Authorization": CS2_API_KEY}
    url = f"{CS2_API_BASE}{path}"

    # Hard cap: 2 retries on 429 then raise so caller can bail out cleanly.
    # Previously unlimited retries caused cascading 429 storms when the
    # settling logic fired on every picks/list call from multiple users.
    MAX_429_RETRIES = 2

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

    # 429 — exponential back-off then retry (capped)
    if _retry >= MAX_429_RETRIES:
        raise RuntimeError(f"CS2 API 429 rate-limited on {path} after {_retry} retries — aborting")
    wait = 10 * (2 ** _retry)   # 10s, 20s
    print(f"[CS2 CLIENT] 429 on {path} — waiting {wait}s before retry {_retry + 1}/{MAX_429_RETRIES}")
    await asyncio.sleep(wait)
    return await _get(path, params, _retry=_retry + 1)


async def _cache_get(key: str) -> Optional[dict]:
    try:
        return await db.cs2_cache.find_one({"key": key}, {"_id": 0})
    except Exception:
        return None


async def _cache_set(key: str, data) -> None:
    try:
        await db.cs2_cache.update_one(
            {"key": key},
            {"$set": {"key": key, "data": data, "_ts": datetime.now(timezone.utc)}},
            upsert=True,
        )
    except Exception:
        pass


def _fresh(doc: Optional[dict], ttl: int) -> bool:
    if not doc or not doc.get("_ts"):
        return False
    try:
        raw = doc["_ts"]
        # `_ts` may be either a BSON datetime (what _cache_set writes) or an
        # ISO string (older docs). Handle both — the previous version only
        # accepted strings and silently treated all freshly-written cache
        # entries as stale, defeating the new short-TTL settle caches.
        if isinstance(raw, datetime):
            ts = raw
        else:
            ts = datetime.fromisoformat(str(raw))
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


async def _fetch_matches_paginated(team_id: int, max_matches: int = 200) -> list:
    """
    Fetch finished matches for a team using cursor pagination.
    Returns up to max_matches match objects, newest first.
    Per_page=100 (API max) keeps the number of page fetches to ≤2.

    Short-TTL cached so multiple settles for the same team — across users
    and across the user's own picks — share one API call. This is the
    single most important defence against the 429 storm.
    """
    # Settle path needs ~25 matches, history needs up to 200. Always read the
    # larger cache first (it satisfies smaller requests too); fall back to the
    # small bucket if only that one is warm. Write to the bucket sized for
    # *this* call so we don't pollute "lg" with truncated lists.
    bucket   = "lg" if max_matches > 50 else "sm"
    key      = f"cs2_matches_{team_id}_{bucket}"
    key_lg   = f"cs2_matches_{team_id}_lg"
    doc_lg   = await _cache_get(key_lg)
    if _fresh(doc_lg, CACHE_TTL["team_matches"]) and doc_lg.get("data") is not None:
        return list(doc_lg["data"])[:max_matches]
    if bucket == "sm":
        doc_sm = await _cache_get(key)
        if _fresh(doc_sm, CACHE_TTL["team_matches"]) and doc_sm.get("data") is not None:
            return list(doc_sm["data"])[:max_matches]

    # Fetch finished matches first, then also fetch "current" matches that
    # have a winner set — the BDL API sometimes marks completed matches as
    # "current" for a few minutes while they ingest final data.
    matches = []
    seen    = set()
    for _status in ("finished", "current"):
        params = {"team_ids[]": team_id, "per_page": 100, "status": _status}
        while len(matches) < max_matches:
            r      = await _get("/matches", params)
            page   = r.get("data", [])
            for m in page:
                m_id = m.get("id")
                if m_id not in seen:
                    seen.add(m_id)
                    # Only accept "current" matches that have a winner (meaning
                    # the match is truly finished even if the API hasn't flipped
                    # the status yet).  "current" without a winner = truly ongoing.
                    if _status == "current" and not (m.get("winner") or {}).get("id"):
                        continue
                    matches.append(m)
            cursor = (r.get("meta") or {}).get("next_cursor")
            if not cursor or not page:
                break
            params = {**params, "cursor": cursor}

    matches = matches[:max_matches]
    try:
        await _cache_set(key, matches)
    except Exception:
        pass
    return matches


def _parse_date_from_slug(slug: str) -> str:
    try:
        parts = slug.rsplit("-", 3)
        return f"{parts[-1]}-{parts[-2]:>02}-{parts[-3]:>02}" if len(parts) >= 4 else ""
    except Exception:
        return ""


async def _fetch_map_player_stat(map_obj: dict, team_id: int, player_id: int) -> Optional[dict]:
    """
    Fetch player_match_map_stats for one map and return a structured stat dict,
    or None if the player didn't appear.  Runs concurrently inside a match.
    """
    map_id          = map_obj.get("id")
    map_name        = map_obj.get("map_name", "")
    map_number      = map_obj.get("map_number", 0)
    winner_id       = (map_obj.get("winner") or {}).get("id")
    t1_score        = map_obj.get("team1_score", 0) or 0
    t2_score        = map_obj.get("team2_score", 0) or 0
    total_rounds    = t1_score + t2_score
    overtime_rounds = map_obj.get("overtime_rounds") or 0
    duration_secs   = map_obj.get("duration_seconds") or 0

    try:
        pmms_r = await _get("/player_match_map_stats", {
            "match_map_id": map_id,
            "per_page":     20,
        })
    except Exception:
        return None

    for stat in pmms_r.get("data", []):
        if stat.get("player", {}).get("id") == player_id:
            kills       = stat.get("kills") or 0
            hs_pct      = float(stat.get("headshot_percentage") or 0)
            hs_count    = round(kills * hs_pct / 100)   # derived headshot count
            return {
                "mapNumber":      map_number,
                "mapName":        map_name,
                "mapId":          map_id,
                "wonMap":         winner_id == team_id,
                "totalRounds":    total_rounds,
                "overtimeRounds": overtime_rounds,
                "durationSecs":   duration_secs,
                "kills":          kills,
                "deaths":         stat.get("deaths") or 0,
                "assists":        stat.get("assists") or 0,
                "adr":            float(stat.get("adr") or 0),
                "kast":           float(stat.get("kast") or 0),
                "rating":         float(stat.get("rating") or 0),
                "headshotPct":    hs_pct,
                "headshotCount":  hs_count,
                "firstKills":     stat.get("first_kills") or 0,
                "firstDeaths":    stat.get("first_deaths") or 0,
                "clutchesWon":    stat.get("clutches_won") or 0,
                "killsPerRound":  round(kills / total_rounds, 3) if total_rounds > 0 else 0,
            }
    return None  # player not in this map


async def _fetch_match_maps_and_stats(match: dict, team_id: int, player_id: int) -> list:
    """
    Fetch all maps for a match and all player stats in parallel.
    Returns list of per-map stat dicts (only maps where player appeared).

    Cached per (match_id, team_id, player_id) for CACHE_TTL["match_maps"]
    so multiple settle attempts for the same pick (and concurrent settles
    for the same player from any poll/cron) don't re-fetch.
    """
    match_id = match.get("id")
    if not match_id:
        return []

    key = f"cs2_mstats_{match_id}_{team_id}_{player_id}"
    doc = await _cache_get(key)
    if _fresh(doc, CACHE_TTL["match_maps"]) and doc.get("data") is not None:
        return list(doc["data"])

    try:
        maps_r = await _get("/match_maps", {"match_ids[]": match_id, "per_page": 10})
        maps   = maps_r.get("data", [])
    except Exception:
        return []

    if not maps:
        return []

    # Fetch all per-map player stats concurrently
    results = await asyncio.gather(
        *[_fetch_map_player_stat(m, team_id, player_id) for m in maps],
        return_exceptions=True,
    )
    stats = [r for r in results if r and not isinstance(r, Exception)]
    # Only cache once we have real data — empty result may just mean the API
    # hasn't ingested player stats yet, and we want to retry shortly.
    try:
        if stats:
            await _cache_set(key, stats)
    except Exception:
        pass
    return stats


async def get_player_recent_map_stats(player_id: int, team_id: int, limit: int = 30) -> list:
    """
    Fetch recent per-map stats for a player (newest first).
    Uses cursor pagination + parallel per-map stat fetches.
    Scans up to 150 team matches to collect `limit` player map entries.
    Serves stale cache on API error so predictions survive subscription lapses.
    """
    key = f"cs2_pmaps_{player_id}_{team_id}"
    doc = await _cache_get(key)
    if _fresh(doc, CACHE_TTL["player_maps"]) and doc.get("data") is not None:
        return doc["data"]

    stale = doc.get("data") if doc and doc.get("data") is not None else None

    map_stats = []
    BATCH = 8   # process N matches concurrently

    try:
        matches = await _fetch_matches_paginated(team_id, max_matches=150)

        # Process matches in parallel batches; stop as soon as we hit `limit` map entries
        for i in range(0, len(matches), BATCH):
            if len(map_stats) >= limit:
                break
            batch = matches[i : i + BATCH]

            # Fetch maps + player stats for each match in the batch concurrently
            batch_results = await asyncio.gather(
                *[_fetch_match_maps_and_stats(m, team_id, player_id) for m in batch],
                return_exceptions=True,
            )

            for match, per_map_stats in zip(batch, batch_results):
                if len(map_stats) >= limit:
                    break
                if isinstance(per_map_stats, Exception) or not per_map_stats:
                    continue

                slug          = match.get("slug", "")
                tournament    = match.get("tournament") or {}
                t_name        = tournament.get("name", "")
                t_tier        = tournament.get("tier", "")
                date_str      = _parse_date_from_slug(slug)
                team1_obj     = match.get("team1") or {}
                team2_obj     = match.get("team2") or {}
                opponent_name = team2_obj.get("name", "") if team1_obj.get("id") == team_id \
                                else team1_obj.get("name", "")
                match_id      = match.get("id")

                # Sort maps by map_number so they come out in order
                per_map_stats.sort(key=lambda m: m.get("mapNumber", 0))
                for ms in per_map_stats:
                    if len(map_stats) >= limit:
                        break
                    map_stats.append({
                        **ms,
                        "matchId":    match_id,
                        "tournament": t_name,
                        "tier":       t_tier,
                        "date":       date_str,
                        "opponent":   opponent_name,
                    })

        await _cache_set(key, map_stats)
        log.info(f"[CS2] player {player_id} map stats: {len(map_stats)} maps fetched")
    except Exception as e:
        log.error(f"CS2 player map stats error: {e}")
        if stale:
            log.warning(f"[CS2] Serving {len(stale)} stale cached map stats for player {player_id}")
            return stale

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


async def get_player_recent_match_stats(player_id: int, team_id: int, limit: int = 30) -> list:
    """
    Fetch per-MATCH aggregated stats for maps_1_2_* props (newest first).
    Uses cursor pagination + parallel per-map stat fetches.
    Scans up to 150 team matches to collect `limit` matches where player appeared.
    For each match: sums kills/deaths/assists on map1+map2; averages ADR/rating/KAST.
    Serves stale cache on API error so predictions survive subscription lapses.
    """
    key = f"cs2_pmatches_{player_id}_{team_id}"
    doc = await _cache_get(key)
    if _fresh(doc, CACHE_TTL["player_maps"]) and doc.get("data") is not None:
        return doc["data"]

    stale = doc.get("data") if doc and doc.get("data") is not None else None

    match_stats = []
    BATCH = 8   # process N matches concurrently

    try:
        matches = await _fetch_matches_paginated(team_id, max_matches=150)

        for i in range(0, len(matches), BATCH):
            if len(match_stats) >= limit:
                break
            batch = matches[i : i + BATCH]

            # Fetch maps + player stats for all matches in batch concurrently
            batch_results = await asyncio.gather(
                *[_fetch_match_maps_and_stats(m, team_id, player_id) for m in batch],
                return_exceptions=True,
            )

            for match, per_map_stats in zip(batch, batch_results):
                if len(match_stats) >= limit:
                    break
                if isinstance(per_map_stats, Exception) or not per_map_stats:
                    continue

                match_id      = match.get("id")
                slug          = match.get("slug", "")
                tournament    = match.get("tournament") or {}
                t_name        = tournament.get("name", "")
                t_tier        = tournament.get("tier", "")
                date_str      = _parse_date_from_slug(slug)
                mt1           = match.get("team1") or {}
                mt2           = match.get("team2") or {}
                opponent_name = mt2.get("name", "") if mt1.get("id") == team_id else mt1.get("name", "")

                # Build map_number → stat dict
                map_player_stats = {ms["mapNumber"]: ms for ms in per_map_stats}

                # Maps 1+2 aggregate
                m1        = map_player_stats.get(1, {})
                m2        = map_player_stats.get(2, {})
                m1m2_maps = [m for m in (m1, m2) if m]
                if not m1m2_maps:
                    # Fallback: use whatever maps exist
                    m1m2_maps = sorted(per_map_stats, key=lambda x: x.get("mapNumber", 0))[:2]

                total_rounds_m1m2  = sum(m.get("totalRounds", 0) for m in m1m2_maps)
                total_kast_vals    = [m.get("kast", 0) for m in m1m2_maps if (m.get("kast") or 0) > 0]
                total_first_kills  = sum(m.get("firstKills", 0) for m in m1m2_maps)
                total_first_deaths = sum(m.get("firstDeaths", 0) for m in m1m2_maps)

                def _sum(field, maps=m1m2_maps):
                    return sum(m.get(field, 0) for m in maps)

                def _avg(field, maps=m1m2_maps):
                    vals = [m.get(field, 0) for m in maps if (m.get(field) or 0) > 0]
                    return sum(vals) / len(vals) if vals else 0.0

                # Map 3 aggregates (None when match didn't go to map 3)
                m3         = map_player_stats.get(3, {})
                m3_played  = bool(m3)
                m3_rounds  = m3.get("totalRounds", 0) if m3_played else 0
                m3_kills   = m3.get("kills", 0)       if m3_played else None

                match_stats.append({
                    "matchId":              match_id,
                    "tournament":           t_name,
                    "tier":                 t_tier,
                    "date":                 date_str,
                    "opponent":             opponent_name,
                    "mapsPlayed":           len(map_player_stats),
                    "maps":                 list(map_player_stats.values()),
                    # Maps 1-2 aggregates
                    "maps_1_2_kills":       _sum("kills"),
                    "maps_1_2_deaths":      _sum("deaths"),
                    "maps_1_2_assists":     _sum("assists"),
                    "maps_1_2_adr":         round(_avg("adr"), 1),
                    "maps_1_2_rating":      round(_avg("rating"), 2),
                    "maps_1_2_headshots":   _sum("headshotCount"),
                    "maps_1_2_kast":        round(sum(total_kast_vals) / len(total_kast_vals), 1) if total_kast_vals else 0,
                    "maps_1_2_firstKills":  total_first_kills,
                    "maps_1_2_firstDeaths": total_first_deaths,
                    "maps_1_2_rounds":      total_rounds_m1m2,
                    "killsPerRound_m1m2":   round(_sum("kills") / total_rounds_m1m2, 3) if total_rounds_m1m2 > 0 else 0,
                    "map1_kills":           m1.get("kills", 0),
                    "map1_rounds":          m1.get("totalRounds", 0),
                    "map1_kpr":             round(m1.get("kills", 0) / m1.get("totalRounds", 1), 3) if (m1.get("totalRounds") or 0) > 0 else 0,
                    "map1_kast":            m1.get("kast", 0),
                    "map2_kills":           m2.get("kills", 0) if m2 else 0,
                    # Map 3 aggregates (None = match didn't go to map 3)
                    "map3_played":          m3_played,
                    "map3_kills":           m3_kills,
                    "map3_headshots":       m3.get("headshotCount", 0) if m3_played else None,
                    "map3_deaths":          m3.get("deaths", 0)        if m3_played else None,
                    "map3_assists":         m3.get("assists", 0)        if m3_played else None,
                    "map3_adr":             m3.get("adr", 0.0)          if m3_played else None,
                    "map3_kast":            m3.get("kast", 0.0)         if m3_played else None,
                    "map3_rounds":          m3_rounds,
                    "map3_kpr":             round(m3_kills / m3_rounds, 3) if (m3_played and m3_rounds > 0 and m3_kills is not None) else None,
                    # Maps 1-3 totals (maps 1-2 + map 3 when played; None when no map 3)
                    "maps_1_3_kills":       (_sum("kills") + m3_kills) if m3_played and m3_kills is not None else None,
                    "maps_1_3_headshots":   (_sum("headshotCount") + (m3.get("headshotCount", 0) or 0)) if m3_played else None,
                    "wonMatch":             any(m.get("wonMap") for m in map_player_stats.values()),
                })

        await _cache_set(key, match_stats)
        log.info(f"[CS2] player {player_id} match stats: {len(match_stats)} matches fetched")
    except Exception as e:
        log.error(f"CS2 player match stats error: {e}")
        if stale:
            log.warning(f"[CS2] Serving {len(stale)} stale cached match stats for player {player_id}")
            return stale

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


async def get_player_next_match(player_id: int, team_id: Optional[int] = None) -> dict:
    """Fetch the next upcoming match for a CS2 player (via their team_id)."""
    cache_key = f"cs2_next_{team_id or player_id}"
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    doc = await _cache_get(cache_key)
    if _fresh(doc, 900) and doc.get("data") is not None:   # 15-min cache
        cached = doc["data"]
        # Extra guard: bypass cache if the stored match date is already past.
        # BDL's "upcoming" status filter is unreliable — old matches can linger.
        cached_date = cached.get("date", "")
        if not cached_date or cached_date >= today_str:
            return cached
        log.info(f"[CS2] cache bypass — stale match date {cached_date} < {today_str}")

    result: dict = {"found": False}
    try:
        if team_id:
            r = await _get("/matches", {"team_ids[]": team_id, "status": "upcoming", "per_page": 10})
        else:
            r = await _get("/matches", {"player_ids[]": player_id, "status": "upcoming", "per_page": 10})

        matches = r.get("data", [])

        # Sort by scheduled date so we always consider the soonest match first.
        def _cs2_date_key(m):
            for k in ("scheduled_at", "begin_at"):
                v = m.get(k)
                if v:
                    return str(v)
            slug_date = _parse_date_from_slug(m.get("slug", ""))
            return slug_date or "9999"

        matches.sort(key=_cs2_date_key)

        # Drop past matches — BDL can return completed rounds as "upcoming".
        future = [m for m in matches if _cs2_date_key(m)[:10] >= today_str]

        if future:
            m    = future[0]
            mt1  = m.get("team1") or {}
            mt2  = m.get("team2") or {}

            if team_id:
                is_team1  = mt1.get("id") == team_id
                opponent  = mt2 if is_team1 else mt1
            else:
                opponent  = mt2   # best guess when no team_id

            tour     = m.get("tournament") or {}
            date_raw = m.get("scheduled_at") or m.get("begin_at") or ""
            date_str = date_raw[:10] if date_raw else _parse_date_from_slug(m.get("slug", ""))

            result = {
                "found":      True,
                "matchId":    m.get("id"),
                "opponent":   {
                    "id":   opponent.get("id"),
                    "name": opponent.get("name") or "",
                    "rank": opponent.get("current_video_game", {}).get("ranking") if opponent.get("current_video_game") else None,
                },
                "tournament": tour.get("name") or "",
                "tier":       tour.get("tier") or "",
                "date":       date_str,
            }
        else:
            log.info(f"[CS2] next-match: {len(matches)} match(es) returned, all in past — no future match found")
    except Exception as e:
        log.warning(f"[CS2] next-match fetch error: {e}")

    await _cache_set(cache_key, result)
    return result


async def get_cs2_completed_match_result(
    team_id: int,
    player_id: int,
    opponent_name: str,
    prop_type: str,
    after_iso: str,
) -> Optional[dict]:
    """
    Find a completed CS2 match for team_id vs opponent_name that occurred
    after after_iso (pick save timestamp).  Returns a dict with the player's
    actual stat values so the pick can be settled as hit/miss/push.

    Returns None if no matching finished match is found yet.
    Logs the reason for failure so settlement issues can be diagnosed.
    """
    from datetime import timedelta
    try:
        after_dt = datetime.fromisoformat(after_iso.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        after_dt = None

    try:
        # Scan the 60 most recent finished team matches — wider window catches
        # picks that are several days old when the team plays frequently.
        matches = await _fetch_matches_paginated(team_id, max_matches=60)
        if not matches:
            log.info(f"[CS2 SETTLE] team={team_id} vs '{opponent_name}': no recent matches returned by API")
            return None

        target = opponent_name.strip().lower()
        # Normalised target for ultra-fuzzy match: strip common esports suffixes
        # and non-alphanumerics, so "ninjasinpyjamas" matches "Ninjas in Pyjamas",
        # "the mongolz" matches "MongolZ", etc.
        import re as _re
        def _norm(s: str) -> str:
            s = s.lower()
            for suffix in (" esports", " gaming", " team", " club"):
                s = s.replace(suffix, "")
            s = _re.sub(r"^the\s+", "", s)
            return _re.sub(r"[^a-z0-9]", "", s)
        target_norm = _norm(target)
        blank_opponent = not target  # pick saved without opponentName

        opponent_candidates = []   # for diagnostics
        for match in matches:
            mt1 = match.get("team1") or {}
            mt2 = match.get("team2") or {}
            opp = mt2 if mt1.get("id") == team_id else mt1
            opp_name = (opp.get("name") or "").lower()
            opponent_candidates.append(opp_name)

            if not blank_opponent:
                # Fuzzy opponent match: substring, first-word, OR normalised match.
                # Guard against false positives on short tokens (e.g. "OG", "G2",
                # "9z", "MIBR") — for any name normalised to ≤4 chars we require
                # an exact normalised equality, not a substring hit.
                opp_word = opp_name.split()[0] if opp_name else ""
                opp_norm = _norm(opp_name)
                min_len  = min(len(target_norm or ""), len(opp_norm or ""))
                if min_len <= 4:
                    norm_match = bool(target_norm) and (target_norm == opp_norm)
                else:
                    norm_match = bool(target_norm) and bool(opp_norm) and (
                        target_norm in opp_norm or opp_norm in target_norm
                    )
                sub_match = (
                    (len(target) >= 4 and target in opp_name) or
                    (len(opp_name) >= 4 and opp_name in target) or
                    (opp_word and len(opp_word) >= 4 and opp_word in target)
                )
                if not (sub_match or norm_match):
                    continue
            # blank_opponent: accept any finished match after pick timestamp

            # Date guard — match must be on or after the pick was saved.
            # Slug dates are day-only (e.g. "2026-05-19"), so compare at
            # date granularity — never skip a match that falls on the same
            # calendar day as the pick save (the old ±6h window wrongly
            # skipped same-day matches when picks were saved late in the day).
            date_str = _parse_date_from_slug(match.get("slug", ""))
            if date_str and after_dt:
                try:
                    match_date = datetime.fromisoformat(date_str).date()
                    pick_date  = after_dt.date()
                    if match_date < pick_date:
                        continue   # match was definitely before this pick was saved
                except Exception:
                    pass

            # Fetch maps + per-player stats in one cached call (CACHE_TTL["match_maps"]).
            per_map_stats = await _fetch_match_maps_and_stats(match, team_id, player_id)
            if not per_map_stats:
                # If the match is finished (or "current" with a winner) but the
                # player has no stats on ANY map, they were a DNP → void as push.
                _is_done = match.get("status") == "finished" or (
                    match.get("status") == "current" and (match.get("winner") or {}).get("id")
                )
                if _is_done:
                    mt1_n = (match.get("team1") or {}).get("name", "")
                    mt2_n = (match.get("team2") or {}).get("name", "")
                    score_str = f"{mt1_n} vs {mt2_n}"
                    log.info(
                        f"[CS2 SETTLE] Player {player_id} not in match stats (DNP) — "
                        f"voiding as push: {prop_type} {score_str}"
                    )
                    return {
                        "actualValue": None,
                        "playerDNP": True,
                        "matchScore": score_str,
                        "matchDate": date_str or "",
                        "opponent": opp.get("name", opponent_name),
                        "mapsPlayed": 0,
                    }
                continue

            map_lookup = {ms["mapNumber"]: ms for ms in per_map_stats}

            # Determine actual value based on prop_type
            if prop_type in ("maps_1_2_kills", "maps_1_2_deaths", "maps_1_2_assists"):
                m1 = map_lookup.get(1, {})
                m2 = map_lookup.get(2, {})
                m12 = [m for m in (m1, m2) if m]
                if not m12:
                    m12 = sorted(per_map_stats, key=lambda x: x.get("mapNumber", 0))[:2]
                stat_key = {
                    "maps_1_2_kills":   "kills",
                    "maps_1_2_deaths":  "deaths",
                    "maps_1_2_assists": "assists",
                }.get(prop_type, "kills")
                actual = sum(m.get(stat_key, 0) for m in m12)
            elif prop_type in ("maps_1_2_adr",):
                m1 = map_lookup.get(1, {})
                m2 = map_lookup.get(2, {})
                m12 = [m for m in (m1, m2) if m]
                adrs = [m.get("adr", 0) for m in m12 if (m.get("adr") or 0) > 0]
                actual = round(sum(adrs) / len(adrs), 1) if adrs else None
            elif prop_type in ("maps_1_3_kills", "maps_1_3_headshots"):
                # All 3 maps combined (maps 1-2 + map 3 if played)
                m1 = map_lookup.get(1, {})
                m2 = map_lookup.get(2, {})
                m3 = map_lookup.get(3, {})
                all_maps = [m for m in (m1, m2, m3) if m]
                if not all_maps:
                    all_maps = sorted(per_map_stats, key=lambda x: x.get("mapNumber", 0))[:3]
                stat_key = "kills" if prop_type == "maps_1_3_kills" else "headshotCount"
                actual = sum(m.get(stat_key, 0) or 0 for m in all_maps)
            elif prop_type == "map1_kills":
                actual = map_lookup.get(1, {}).get("kills")
            elif prop_type == "map1_headshots":
                actual = map_lookup.get(1, {}).get("headshotCount")
            elif prop_type == "map1_deaths":
                actual = map_lookup.get(1, {}).get("deaths")
            elif prop_type == "map1_assists":
                actual = map_lookup.get(1, {}).get("assists")
            elif prop_type == "map1_adr":
                actual = map_lookup.get(1, {}).get("adr")
            # ── Map 3 props ───────────────────────────────────────────────────
            elif prop_type == "map3_kills":
                actual = map_lookup.get(3, {}).get("kills")
            elif prop_type == "map3_headshots":
                actual = map_lookup.get(3, {}).get("headshotCount")
            elif prop_type == "map3_deaths":
                actual = map_lookup.get(3, {}).get("deaths")
            elif prop_type == "map3_assists":
                actual = map_lookup.get(3, {}).get("assists")
            elif prop_type == "map3_adr":
                m3 = map_lookup.get(3, {})
                actual = m3.get("adr") if m3 else None
            # ── Maps 1-2 headshots (separate from kills) ──────────────────────
            elif prop_type == "maps_1_2_headshots":
                m1 = map_lookup.get(1, {})
                m2 = map_lookup.get(2, {})
                m12 = [m for m in (m1, m2) if m]
                if not m12:
                    m12 = sorted(per_map_stats, key=lambda x: x.get("mapNumber", 0))[:2]
                actual = sum(m.get("headshotCount", 0) or 0 for m in m12)
            elif prop_type == "kills":
                # Per-map prop — use map 1
                actual = map_lookup.get(1, {}).get("kills")
            elif prop_type == "deaths":
                actual = map_lookup.get(1, {}).get("deaths")
            elif prop_type == "assists":
                actual = map_lookup.get(1, {}).get("assists")
            elif prop_type == "adr":
                actual = map_lookup.get(1, {}).get("adr")
            elif prop_type == "rating":
                actual = map_lookup.get(1, {}).get("rating")
            elif prop_type == "first_kills":
                actual = map_lookup.get(1, {}).get("firstKills")
            elif prop_type == "clutches_won":
                actual = map_lookup.get(1, {}).get("clutchesWon")
            else:
                actual = None

            if actual is None:
                # For map3_ props: if the player appeared in the match but map 3
                # simply wasn't played (match went 2-0 or 0-2), push the pick
                # so it never hangs live indefinitely.
                if prop_type.startswith("map3_") and per_map_stats and 3 not in map_lookup:
                    mt1_n = (mt1.get("name") or "")
                    mt2_n = (mt2.get("name") or "")
                    maps_played_n = len(per_map_stats)
                    log.info(
                        f"[CS2 SETTLE] map3_ prop but only {maps_played_n} map(s) played — "
                        f"voiding as push: {prop_type} vs {opp.get('name')}"
                    )
                    return {
                        "actualValue": None,
                        "noMap3": True,
                        "matchScore": f"{mt1_n} vs {mt2_n}",
                        "matchDate": date_str or "",
                        "opponent": opp.get("name", opponent_name),
                        "mapsPlayed": maps_played_n,
                    }
                continue

            # Build score string from per_map_stats (which carries wonMap per
            # map the player appeared in). This is an approximation when the
            # player sat out a map — but per-map stat rows are the only source
            # we still have once we removed the redundant /match_maps call.
            mt1_name      = (mt1.get("name") or "")
            mt2_name      = (mt2.get("name") or "")
            maps_played   = len(per_map_stats)
            team_map_wins = sum(1 for ms in per_map_stats if ms.get("wonMap"))
            opp_map_wins  = maps_played - team_map_wins
            home_name = mt1_name if mt1.get("id") == team_id else mt2_name
            away_name = mt2_name if mt1.get("id") == team_id else mt1_name
            score_str = f"{home_name} {team_map_wins}–{opp_map_wins} {away_name}"

            log.info(
                f"[CS2 SETTLE] {prop_type} vs {opp.get('name')} "
                f"actual={actual} date={date_str} score={score_str}"
            )
            return {
                "actualValue": actual,
                "matchScore":  score_str,
                "matchDate":   date_str,
                "opponent":    opp.get("name", opponent_name),
                "mapsPlayed":  maps_played,
            }

        # No match found — log what we DID see so the failure mode is obvious
        log.info(
            f"[CS2 SETTLE] team={team_id} no finished match for opponent "
            f"'{opponent_name}' after {after_iso} — recent opponents scanned: "
            f"{opponent_candidates[:8]}"
        )
    except Exception as e:
        log.error(f"[CS2 SETTLE] Error fetching completed match result: {e}")

    return None
