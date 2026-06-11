"""
API Sports (v3.football.api-sports.io) client for FIFA World Cup / international predictions.
Uses API_SPORTS_KEY — separate account from the suspended API-Football key.

Strategy for 20-30 game logs per player:
  - Scan ALL finished fixtures for the national team across 5 seasons (no league filter).
    This automatically captures: WC 2026 group stage, WC 2022/2018, Copa America,
    UEFA Nations League, CONCACAF Nations League, Gold Cup, AFCON, Asian Cup, Friendlies.
  - Per-fixture player stats are fetched in parallel and cached 24h (historical) / 30min (live).
  - Cap at 40 candidate fixtures → return up to 30 where player actually played (minutes > 0).
"""
import asyncio
import logging
import os
import time
import unicodedata
from typing import Optional

import httpx
from config import db

log = logging.getLogger("api_sports_wc")

API_SPORTS_BASE  = "https://v3.football.api-sports.io"
API_SPORTS_KEY   = os.environ.get("API_SPORTS_KEY", "")

WC_LEAGUE        = 1
CURRENT_SEASON   = 2026
SEASONS_TO_SCAN  = [2026, 2025, 2024, 2023, 2022]

MAX_CANDIDATE_FIXTURES = 50
MAX_RETURNED_LOGS      = 30

CACHE_COL    = db["api_sports_wc_cache"]
_rate_sem    = asyncio.Semaphore(3)
_last_req    = 0.0
_MIN_INTERVAL = 0.4


def _norm(s: str) -> str:
    return unicodedata.normalize("NFD", s or "").encode("ascii", "ignore").decode().lower().strip()


async def _get(path: str, params: dict = None) -> dict:
    global _last_req
    headers = {"x-apisports-key": API_SPORTS_KEY}
    url = f"{API_SPORTS_BASE}{path}"

    async with _rate_sem:
        elapsed = time.monotonic() - _last_req
        if elapsed < _MIN_INTERVAL:
            await asyncio.sleep(_MIN_INTERVAL - elapsed)
        try:
            async with httpx.AsyncClient(timeout=20) as c:
                r = await c.get(url, headers=headers, params=params or {})
        except Exception as e:
            raise RuntimeError(f"API Sports request failed: {e}")
        finally:
            _last_req = time.monotonic()

    if r.status_code == 429:
        raise RuntimeError("API Sports rate-limited (429)")
    if r.status_code >= 400:
        raise RuntimeError(f"API Sports {r.status_code}: {r.text[:200]}")
    return r.json()


def _cache_get_sync(key: str) -> Optional[dict]:
    """Synchronous cache read — safe to call from async context (sub-5ms)."""
    try:
        return CACHE_COL.find_one({"_id": key})
    except Exception:
        return None


def _cache_set_sync(key: str, data, ttl: int = 3600):
    """Synchronous cache write — safe to call from async context."""
    try:
        doc = {"_id": key, "data": data, "ts": int(time.time()), "ttl": ttl}
        CACHE_COL.replace_one({"_id": key}, doc, upsert=True)
    except Exception:
        pass


def _cache_fresh(doc: Optional[dict], ttl: int) -> bool:
    if not doc:
        return False
    import time as _t
    return (int(_t.time()) - doc.get("ts", 0)) < ttl


async def get_wc_team_id(team_name: str) -> Optional[int]:
    """Resolve national team name → API Sports team ID via WC 2026 participant list."""
    cache_key = f"wc_teams:{CURRENT_SEASON}"
    doc = _cache_get_sync(cache_key)
    if _cache_fresh(doc, 86400):
        teams = doc["data"]
    else:
        try:
            r = await _get("/teams", {"league": WC_LEAGUE, "season": CURRENT_SEASON})
            teams = [
                {"id": t["team"]["id"], "name": t["team"]["name"]}
                for t in r.get("response", [])
                if t.get("team")
            ]
            _cache_set_sync(cache_key, teams, ttl=86400)
        except Exception as e:
            log.warning(f"[API-SPORTS] Failed to fetch WC teams: {e}")
            return None

    needle = _norm(team_name)
    for t in teams:
        if _norm(t["name"]) == needle:
            return t["id"]
    for t in teams:
        if needle in _norm(t["name"]) or _norm(t["name"]) in needle:
            return t["id"]
    return None


async def _get_team_fixtures_for_season(team_id: int, season: int) -> list:
    """All FT fixtures for a team in a given season — NO league filter (all competitions)."""
    cache_key = f"as_all_fx:{team_id}:{season}"
    doc = _cache_get_sync(cache_key)
    ttl = 86400 if season < CURRENT_SEASON else 1800
    if _cache_fresh(doc, ttl):
        return doc["data"]

    try:
        r = await _get("/fixtures", {
            "team":   team_id,
            "season": season,
            "status": "FT",
        })
        fixtures = r.get("response", [])
        _cache_set_sync(cache_key, fixtures, ttl=ttl)
        return fixtures
    except Exception as e:
        log.warning(f"[API-SPORTS] Fixtures season={season} team={team_id} failed: {e}")
        return []


async def _get_all_team_fixtures(team_id: int) -> list:
    """
    Fetch finished fixtures across all configured seasons in parallel,
    deduplicate by fixture ID, sort newest-first.
    """
    tasks = [_get_team_fixtures_for_season(team_id, s) for s in SEASONS_TO_SCAN]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    all_fx: list[dict] = []
    seen_ids: set[int] = set()
    for result in results:
        if isinstance(result, Exception) or not result:
            continue
        for fx in result:
            fxid = (fx.get("fixture") or {}).get("id")
            if fxid and fxid not in seen_ids:
                seen_ids.add(fxid)
                all_fx.append(fx)

    all_fx.sort(
        key=lambda f: (f.get("fixture") or {}).get("date", ""),
        reverse=True,
    )
    return all_fx


async def _get_player_stats_in_fixture(
    fixture_id: int, team_id: int, player_name: str
) -> Optional[dict]:
    """Return the named player's stats dict for a specific fixture, or None."""
    cache_key = f"wc_fxp:{fixture_id}:{team_id}"
    doc = _cache_get_sync(cache_key)
    if _cache_fresh(doc, 86400):
        players_data = doc["data"]
    else:
        try:
            r = await _get("/fixtures/players", {"fixture": fixture_id, "team": team_id})
            players_data = []
            for team_entry in r.get("response", []):
                players_data.extend(team_entry.get("players", []))
            _cache_set_sync(cache_key, players_data, ttl=86400)
        except Exception as e:
            log.warning(f"[API-SPORTS] Fixture players {fixture_id} failed: {e}")
            return None

    needle = _norm(player_name)
    needle_parts = needle.split()

    for p_entry in players_data:
        p = p_entry.get("player") or {}
        p_norm = _norm(p.get("name", ""))
        if p_norm == needle:
            return p_entry
        p_parts = p_norm.split()
        if p_parts and needle_parts:
            if p_parts[-1] == needle_parts[-1]:
                return p_entry
            if needle_parts[0] == p_parts[0] and len(needle_parts) > 1 and len(p_parts) > 1:
                return p_entry
    return None


def _extract_log(p_entry: dict, fixture: dict, team_id: int) -> dict:
    """Convert API Sports fixture-player entry to Bayesian engine log format."""
    stats    = (p_entry.get("statistics") or [{}])[0]
    games    = stats.get("games")    or {}
    shots    = stats.get("shots")    or {}
    goals    = stats.get("goals")    or {}
    passes   = stats.get("passes")   or {}
    tackles  = stats.get("tackles")  or {}
    duels    = stats.get("duels")    or {}
    dribbles = stats.get("dribbles") or {}
    fouls    = stats.get("fouls")    or {}
    cards    = stats.get("cards")    or {}

    fx       = fixture.get("fixture") or {}
    teams    = fixture.get("teams")   or {}
    home_id  = (teams.get("home") or {}).get("id")
    is_home  = (team_id == home_id)
    opp      = teams.get("away" if is_home else "home") or {}

    def _int(v):
        try:
            return int(v) if v is not None else None
        except Exception:
            return None

    def _float(v):
        try:
            return float(v) if v is not None else None
        except Exception:
            return None

    return {
        "date":                  (fx.get("date") or "")[:10],
        "opponent":              opp.get("name", ""),
        "venue":                 "home" if is_home else "away",
        "minutes":               _int(games.get("minutes")) or 90,
        "passes_total":          _int(passes.get("total")),
        "passes_key":            _int(passes.get("key")),
        "passes_accuracy":       _int(passes.get("accuracy")),
        "shots_total":           _int(shots.get("total")),
        "shots_on":              _int(shots.get("on")),
        "goals":                 _int(goals.get("total")) or 0,
        "assists":               _int(goals.get("assists")) or 0,
        "tackles_total":         _int(tackles.get("total")),
        "tackles_interceptions": _int(tackles.get("interceptions")),
        "tackles_clearances":    _int(tackles.get("blocks")),
        "dribbles_attempts":     _int(dribbles.get("attempts")),
        "dribbles_success":      _int(dribbles.get("success")),
        "duels_total":           _int(duels.get("total")),
        "duels_won":             _int(duels.get("won")),
        "fouls_drawn":           _int(fouls.get("drawn")),
        "fouls_committed":       _int(fouls.get("committed")),
        "cards_yellow":          _int(cards.get("yellow")) or 0,
        "cards_red":             _int(cards.get("red")) or 0,
        "rating":                _float(games.get("rating")),
        "position":              games.get("position", ""),
        "_source":               "api_sports",
    }


async def get_game_logs(player_name: str, team_name: str) -> list:  # noqa: C901
    """
    Main entry point.  Scans ALL international competitions (WC, Copa America,
    Nations League, Gold Cup, AFCON, Asian Cup, Friendlies…) across the last
    5 seasons to collect 20–30 game logs for the player.

    Returns [] if API Sports key is missing or team cannot be resolved.
    """
    if not API_SPORTS_KEY:
        return []

    team_id = await get_wc_team_id(team_name)
    if not team_id:
        log.info(f"[API-SPORTS] Could not resolve team '{team_name}'")
        return []

    log.info(f"[API-SPORTS] {player_name} / {team_name} (id={team_id}) — scanning {SEASONS_TO_SCAN}")

    try:
        all_fixtures = await _get_all_team_fixtures(team_id)
    except Exception as _afx_err:
        import traceback
        log.error(f"[API-SPORTS] _get_all_team_fixtures failed: {_afx_err}\n{traceback.format_exc()}")
        return []
    candidates   = all_fixtures[:MAX_CANDIDATE_FIXTURES]
    log.info(f"[API-SPORTS] {len(all_fixtures)} total FT fixtures → scanning {len(candidates)}")

    # Build aligned (fixture, coroutine) pairs — must stay in sync so zip is correct.
    # The old code filtered in the comprehension but zipped against full candidates,
    # causing a Future to land in p_entry whenever a fixture was skipped.
    valid_pairs = [
        (fx, _get_player_stats_in_fixture(fx["fixture"]["id"], team_id, player_name))
        for fx in candidates
        if (fx.get("fixture") or {}).get("id")
    ]
    valid_fixtures = [fx  for fx, _  in valid_pairs]
    stat_coros    = [coro for _,  coro in valid_pairs]

    try:
        stat_results = await asyncio.gather(*stat_coros, return_exceptions=True)
    except Exception as _sg_err:
        import traceback
        log.error(f"[API-SPORTS] gather failed: {_sg_err}\n{traceback.format_exc()}")
        return []

    logs: list[dict] = []
    seen_dates: set[str] = set()

    for fx, p_entry in zip(valid_fixtures, stat_results):
        if isinstance(p_entry, Exception) or p_entry is None:
            continue
        if not isinstance(p_entry, dict):
            log.warning(f"[API-SPORTS] Unexpected p_entry type {type(p_entry)} — skipping")
            continue
        stats   = (p_entry.get("statistics") or [{}])[0]
        minutes = (stats.get("games") or {}).get("minutes")
        if not minutes:
            continue
        log_dict = _extract_log(p_entry, fx, team_id)
        date_key = log_dict.get("date", "")
        if date_key in seen_dates:
            continue
        seen_dates.add(date_key)
        logs.append(log_dict)
        if len(logs) >= MAX_RETURNED_LOGS:
            break

    logs.sort(key=lambda g: g.get("date", ""), reverse=True)
    log.info(f"[API-SPORTS] {player_name}: {len(logs)} logs returned")
    return logs
