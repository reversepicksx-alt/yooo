"""
API Sports (v3.football.api-sports.io) client for FIFA World Cup predictions.
Uses API_SPORTS_KEY — a separate account from the suspended API-Football key.

Data sources:
  - WC 2026 group stage     (league_id=1,  season=2026) — live as games are played
  - CONCACAF WC qualifiers  (league_id=31, season=2026) — qualifier history
  - WC 2022 historical      (league_id=1,  season=2022) — previous tournament

Game log schema matches BDL soccer client output so the Bayesian engine works unchanged.
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

API_SPORTS_BASE = "https://v3.football.api-sports.io"
API_SPORTS_KEY  = os.environ.get("API_SPORTS_KEY", "")

WC_LEAGUE          = 1
CONCACAF_QUAL_LEAGUE = 31
CURRENT_SEASON     = 2026
PREVIOUS_SEASON    = 2022

CACHE_COL = db["api_sports_wc_cache"]

_rate_sem  = asyncio.Semaphore(3)
_last_req  = 0.0
_MIN_INTERVAL = 0.4   # ~150 req/min to stay well within Mega plan limits


def _norm(s: str) -> str:
    """Accent-strip + lower for fuzzy name matching."""
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


async def _cache_get(key: str) -> Optional[dict]:
    try:
        return await asyncio.to_thread(
            lambda: CACHE_COL.find_one({"_id": key})
        )
    except Exception:
        return None


async def _cache_set(key: str, data, ttl: int = 3600):
    import time as _time
    try:
        doc = {"_id": key, "data": data, "ts": int(_time.time()), "ttl": ttl}
        await asyncio.to_thread(
            lambda: CACHE_COL.replace_one({"_id": key}, doc, upsert=True)
        )
    except Exception:
        pass


def _cache_fresh(doc: Optional[dict], ttl: int) -> bool:
    if not doc:
        return False
    import time as _time
    return (int(_time.time()) - doc.get("ts", 0)) < ttl


async def get_wc_team_id(team_name: str) -> Optional[int]:
    """Resolve a national team name to its API Sports team ID using the WC 2026 team list."""
    cache_key = f"wc_teams:{CURRENT_SEASON}"
    doc = await _cache_get(cache_key)
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
            await _cache_set(cache_key, teams, ttl=86400)
        except Exception as e:
            log.warning(f"[API-SPORTS-WC] Failed to fetch WC teams: {e}")
            return None

    needle = _norm(team_name)
    for t in teams:
        if _norm(t["name"]) == needle:
            return t["id"]
    # Partial match fallback
    for t in teams:
        if needle in _norm(t["name"]) or _norm(t["name"]) in needle:
            return t["id"]
    return None


async def get_finished_fixtures(team_id: int, league_id: int, season: int) -> list:
    """Get all finished (FT) fixtures for a team in a given league+season."""
    cache_key = f"wc_fixtures:{team_id}:{league_id}:{season}"
    doc = await _cache_get(cache_key)
    if _cache_fresh(doc, 1800):
        return doc["data"]

    try:
        r = await _get("/fixtures", {
            "team":   team_id,
            "league": league_id,
            "season": season,
            "status": "FT",
        })
        fixtures = r.get("response", [])
        await _cache_set(cache_key, fixtures, ttl=1800)
        return fixtures
    except Exception as e:
        log.warning(f"[API-SPORTS-WC] Fixtures fetch failed (team={team_id} league={league_id}): {e}")
        return []


async def get_player_stats_in_fixture(
    fixture_id: int, team_id: int, player_name: str
) -> Optional[dict]:
    """
    Fetch all player stats for a fixture+team and return the named player's stats dict.
    Returns None if player not found or data unavailable.
    """
    cache_key = f"wc_fxp:{fixture_id}:{team_id}"
    doc = await _cache_get(cache_key)
    if _cache_fresh(doc, 86400):
        players_data = doc["data"]
    else:
        try:
            r = await _get("/fixtures/players", {"fixture": fixture_id, "team": team_id})
            players_data = []
            for team_entry in r.get("response", []):
                players_data.extend(team_entry.get("players", []))
            await _cache_set(cache_key, players_data, ttl=86400)
        except Exception as e:
            log.warning(f"[API-SPORTS-WC] Fixture players fetch failed ({fixture_id}): {e}")
            return None

    needle = _norm(player_name)
    for p_entry in players_data:
        p = p_entry.get("player", {})
        if _norm(p.get("name", "")) == needle:
            return p_entry
        # Partial: last name match
        p_parts = _norm(p.get("name", "")).split()
        n_parts  = needle.split()
        if p_parts and n_parts and p_parts[-1] == n_parts[-1]:
            return p_entry
    return None


def _extract_stats(p_entry: dict, fixture: dict, team_id: int) -> dict:
    """Convert an API Sports fixture player entry to our Bayesian-engine log format."""
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
    fx_goals = fixture.get("goals")   or {}
    home_id  = (teams.get("home") or {}).get("id")
    away_id  = (teams.get("away") or {}).get("id")
    is_home  = (team_id == home_id)
    opp      = teams.get("away" if is_home else "home") or {}
    opp_name = opp.get("name", "")

    date_str = (fx.get("date") or "")[:10]

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
        "date":                date_str,
        "opponent":            opp_name,
        "venue":               "home" if is_home else "away",
        "minutes":             _int(games.get("minutes")) or 90,
        "passes_total":        _int(passes.get("total")),
        "passes_key":          _int(passes.get("key")),
        "passes_accuracy":     _int(passes.get("accuracy")),
        "shots_total":         _int(shots.get("total")),
        "shots_on":            _int(shots.get("on")),
        "goals":               _int(goals.get("total")) or 0,
        "assists":             _int(goals.get("assists")) or 0,
        "tackles_total":       _int(tackles.get("total")),
        "tackles_interceptions": _int(tackles.get("interceptions")),
        "tackles_clearances":  _int(tackles.get("blocks")),
        "dribbles_attempts":   _int(dribbles.get("attempts")),
        "dribbles_success":    _int(dribbles.get("success")),
        "duels_total":         _int(duels.get("total")),
        "duels_won":           _int(duels.get("won")),
        "fouls_drawn":         _int(fouls.get("drawn")),
        "fouls_committed":     _int(fouls.get("committed")),
        "cards_yellow":        _int(cards.get("yellow")) or 0,
        "cards_red":           _int(cards.get("red")) or 0,
        "rating":              _float(games.get("rating")),
        "position":            games.get("position", ""),
        "_source":             "api_sports",
    }


async def get_game_logs(
    player_name: str,
    team_name:   str,
    include_qualifiers: bool = True,
    include_prev_wc:    bool = True,
) -> list:
    """
    Main entry point. Returns a list of game logs (newest-first) from:
      - WC 2026 group stage finished games
      - CONCACAF WC 2026 qualifiers (if include_qualifiers=True)
      - WC 2022 historical (if include_prev_wc=True)

    Returns [] if API Sports key is missing or team cannot be resolved.
    """
    if not API_SPORTS_KEY:
        return []

    team_id = await get_wc_team_id(team_name)
    if not team_id:
        log.info(f"[API-SPORTS-WC] Could not resolve team '{team_name}' — no data")
        return []

    log.info(f"[API-SPORTS-WC] {player_name} / {team_name} (team_id={team_id})")

    # Build list of (league_id, season) pairs to fetch fixtures for
    league_seasons = [(WC_LEAGUE, CURRENT_SEASON)]
    if include_qualifiers:
        league_seasons.append((CONCACAF_QUAL_LEAGUE, CURRENT_SEASON))
    if include_prev_wc:
        league_seasons.append((WC_LEAGUE, PREVIOUS_SEASON))

    all_logs: list[dict] = []
    seen_dates: set[str] = set()

    # Fetch fixtures for each league+season in parallel
    fixture_tasks = [
        get_finished_fixtures(team_id, lg_id, season)
        for lg_id, season in league_seasons
    ]
    fixture_results = await asyncio.gather(*fixture_tasks, return_exceptions=True)

    for (lg_id, season), fixtures in zip(league_seasons, fixture_results):
        if isinstance(fixtures, Exception) or not fixtures:
            continue

        # Fetch player stats for each fixture (up to 12 to limit API calls)
        stat_tasks = [
            get_player_stats_in_fixture(fx["fixture"]["id"], team_id, player_name)
            for fx in fixtures[:12]
        ]
        stat_results = await asyncio.gather(*stat_tasks, return_exceptions=True)

        for fx, player_entry in zip(fixtures[:12], stat_results):
            if isinstance(player_entry, Exception) or not player_entry:
                continue
            stats = player_entry.get("statistics") or [{}]
            minutes = (stats[0].get("games") or {}).get("minutes")
            if not minutes:
                continue

            log_dict = _extract_stats(player_entry, fx, team_id)
            date_key = log_dict.get("date", "")
            if date_key in seen_dates:
                continue
            seen_dates.add(date_key)
            all_logs.append(log_dict)

    all_logs.sort(key=lambda g: g.get("date", ""), reverse=True)
    log.info(f"[API-SPORTS-WC] {player_name}: {len(all_logs)} total logs")
    return all_logs
