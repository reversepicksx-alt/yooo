"""
BallDontLie Soccer API client.
Covers: EPL, La Liga, Serie A, Bundesliga, Ligue 1, Champions League, MLS, World Cup.
Same API key as all other BDL sports (MLB_BDL_API_KEY). No daily quota.

Field mapping — BDL returns these fields; we normalise to the RP game-log format
that the rest of the prediction pipeline expects (same as _build_game_log in predict.py).
"""

import asyncio
import time
import os
import logging
from datetime import datetime, timezone
from typing import Optional

import httpx
from config import db

log = logging.getLogger("soccer_bdl_client")

BDL_BASE = "https://api.balldontlie.io"
BDL_KEY  = os.environ.get("MLB_BDL_API_KEY", "")

# ── League routing ────────────────────────────────────────────────────────────
# API-Football league ID  →  BDL path prefix + version
LEAGUE_TO_BDL: dict[int, str] = {
    39:  "/epl/v2",              # English Premier League
    140: "/laliga/v1",           # La Liga
    135: "/seriea/v1",           # Serie A
    78:  "/bundesliga/v1",       # Bundesliga
    61:  "/ligue1/v1",           # Ligue 1
    2:   "/ucl/v1",              # UEFA Champions League
    3:   "/ucl/v1",              # Europa League — route to UCL client (similar)
    253: "/mls/v1",              # MLS
    1:   "/fifa/worldcup/v1",    # FIFA World Cup
}

# Seasons to try, newest-first.  MLS uses the calendar year (2026 season = 2026).
# European leagues also use the year the season STARTS (2025-26 = 2025 in BDL).
# We compute dynamically so MLS 2026 is always the first candidate.
_cur_yr = datetime.now(tz=timezone.utc).year
_CURRENT_SEASONS = [_cur_yr, _cur_yr - 1, _cur_yr - 2]   # e.g. [2026, 2025, 2024]


def is_bdl_league(league_id: int) -> bool:
    """Return True when this league is supported by the BDL soccer API."""
    return league_id in LEAGUE_TO_BDL


# ── HTTP client ───────────────────────────────────────────────────────────────
_rate_sem   = asyncio.Semaphore(6)   # shared key — keep burst polite
_last_call: float = 0.0
_MIN_GAP    = 0.12                   # ~8 req/s


async def _get(path: str, params: dict | None = None) -> Optional[dict]:
    global _last_call
    if not BDL_KEY:
        log.warning("[BDL-SOC] No API key (MLB_BDL_API_KEY not set)")
        return None

    url     = f"{BDL_BASE}{path}"
    headers = {"Authorization": BDL_KEY}

    async with _rate_sem:
        gap = time.monotonic() - _last_call
        if gap < _MIN_GAP:
            await asyncio.sleep(_MIN_GAP - gap)
        try:
            async with httpx.AsyncClient(timeout=20) as c:
                resp = await c.get(url, headers=headers, params=params or {})
        except Exception as exc:
            log.error(f"[BDL-SOC] Network error {path}: {exc}")
            return None
        finally:
            _last_call = time.monotonic()

    if resp.status_code == 200:
        return resp.json()
    if resp.status_code == 429:
        log.warning(f"[BDL-SOC] 429 rate-limited on {path}")
        await asyncio.sleep(5)
        return None
    log.warning(f"[BDL-SOC] {resp.status_code} on {path}: {resp.text[:200]}")
    return None


# ── Field normalisation ───────────────────────────────────────────────────────
def _norm(raw: dict) -> dict:
    """
    Convert one BDL SoccerPlayerMatchStats row into the RP game-log format
    (matches the dict produced by _build_game_log in routes/predict.py).

    BDL soccer data has two tiers of availability:
      Tier-1 (always populated): goals, assists, shots_total, shots_on_target,
        fouls_committed, fouls_suffered, yellow_cards, red_cards, offsides.
      Tier-2 (often None): passes_total, tackles, clearances, minutes_played,
        key_passes, dribbles, interceptions, rating, xg — these fields are
        populated by secondary data providers and may be absent for some
        seasons/leagues. We pass through whatever is present; the prediction
        pipeline tolerates None values via `if g.get(field) is not None`.

    For minutes_played=None we fall back to appearances*90 so the log is not
    discarded outright by the minutes>0 filter downstream.
    """
    gk_saves = (raw.get("goalkeeper_saves") or raw.get("saves") or 0)
    duels_w  = raw.get("duels_won")  or 0
    duels_l  = raw.get("duels_lost") or 0
    # Infer minutes from appearances when minutes_played is not provided.
    # appearances=1 → player started (90 min estimate); 0 → DNP.
    # This avoids discarding valid stat rows just because minutes is missing.
    raw_mins = raw.get("minutes_played")
    apps     = raw.get("appearances") or 0
    minutes  = int(raw_mins) if raw_mins is not None else (90 if apps >= 1 else 0)
    return {
        "minutes":             minutes,
        "rating":              raw.get("rating"),
        # passes
        "passes_total":        raw.get("passes_total"),
        "passes_key":          raw.get("key_passes"),
        "passes_accuracy":     raw.get("passes_accurate"),
        "passes_crosses":      raw.get("crosses_total"),
        # shots
        "shots_total":         raw.get("shots_total"),
        "shots_on":            raw.get("shots_on_target"),
        # tackles / defence
        "tackles_total":       raw.get("tackles"),
        "tackles_interceptions": raw.get("interceptions"),
        "tackles_blocks":      raw.get("blocked_shots"),
        "tackles_clearances":  raw.get("clearances"),
        # dribbles
        "dribbles_attempts":   raw.get("dribbles_attempted"),
        "dribbles_success":    raw.get("dribbles_completed"),
        # fouls
        "fouls_drawn":         raw.get("fouls_suffered") or raw.get("was_fouled"),
        "fouls_committed":     raw.get("fouls_committed"),
        # duels
        "duels_won":           duels_w or None,
        "duels_total":         (duels_w + duels_l) or None,
        # aerial
        "aerial_duels_won":    raw.get("aerial_duels_won"),
        # goals
        "goals_total":         raw.get("goals") or 0,
        "goals_assists":       raw.get("assists") or 0,
        "goals_saves":         gk_saves or None,
        # cards
        "cards_yellow":        raw.get("yellow_cards") or 0,
        # bonus (not in API-Football format but harmless extras)
        "xg":                  raw.get("expected_goals"),
        "xa":                  raw.get("expected_assists"),
        "ball_recoveries":     raw.get("ball_recoveries"),
        "big_chances_created": raw.get("big_chances_created"),
        # BDL match reference (used for enrichment below)
        "_bdl_match_id":       raw.get("match_id"),
        "_bdl_team_id":        raw.get("team_id"),
        "_bdl_player_id":      raw.get("player_id"),
    }


# ── Teams lookup (cached per league per day) ──────────────────────────────────
async def _teams_lookup(league_id: int) -> dict[int, str]:
    """Return {bdl_team_id: team_name} for all teams in the league (24 h cache)."""
    path      = LEAGUE_TO_BDL[league_id]
    cache_key = f"bdl_soc_teams_{league_id}"
    try:
        doc = await db.bdl_soccer_cache.find_one({"_k": cache_key})
        if doc and doc.get("d"):
            return {int(k): v for k, v in doc["d"].items()}
    except Exception:
        pass

    teams: dict[int, str] = {}
    cursor = None
    for _ in range(5):          # max 5 pages
        params: dict = {"per_page": 100}
        if cursor:
            params["cursor"] = cursor
        result = await _get(f"{path}/teams", params)
        if not result:
            break
        for t in result.get("data", []):
            tid  = t.get("id")
            name = t.get("name") or t.get("display_name") or ""
            if tid:
                teams[tid] = name
        cursor = result.get("meta", {}).get("next_cursor")
        if not cursor:
            break

    if teams:
        try:
            await db.bdl_soccer_cache.update_one(
                {"_k": cache_key},
                {"$set": {"_k": cache_key, "d": {str(k): v for k, v in teams.items()},
                          "_ts": datetime.now(timezone.utc)}},
                upsert=True
            )
        except Exception:
            pass
    return teams


# ── Match metadata lookup ─────────────────────────────────────────────────────
async def _matches_for_team(league_id: int, bdl_team_id: int, season: int) -> dict[int, dict]:
    """
    Return {match_id: match_data} for all matches the team played in that season.
    Cached for 6 h.
    """
    path      = LEAGUE_TO_BDL[league_id]
    cache_key = f"bdl_soc_tm_{league_id}_{bdl_team_id}_{season}"
    try:
        doc = await db.bdl_soccer_cache.find_one({"_k": cache_key})
        if doc and doc.get("d"):
            return {int(k): v for k, v in doc["d"].items()}
    except Exception:
        pass

    match_map: dict[int, dict] = {}
    params = {"team_ids[]": bdl_team_id, "season": season, "per_page": 60}
    result = await _get(f"{path}/matches", params)
    if result:
        for m in result.get("data", []):
            mid = m.get("id")
            if mid:
                match_map[mid] = m

    if match_map:
        try:
            await db.bdl_soccer_cache.update_one(
                {"_k": cache_key},
                {"$set": {"_k": cache_key, "d": {str(k): v for k, v in match_map.items()},
                          "_ts": datetime.now(timezone.utc)}},
                upsert=True
            )
        except Exception:
            pass
    return match_map


# ── Player search ─────────────────────────────────────────────────────────────
async def _search_player(league_id: int, name: str) -> list[dict]:
    """Search for players by (partial) name in one BDL soccer league. 4-h cache."""
    path      = LEAGUE_TO_BDL[league_id]
    slug      = name.lower().strip()
    cache_key = f"bdl_soc_ps_{league_id}_{slug}"
    try:
        doc = await db.bdl_soccer_cache.find_one({"_k": cache_key})
        if doc and doc.get("d") is not None:
            return doc["d"]
    except Exception:
        pass

    result = await _get(f"{path}/players", {"search": name, "per_page": 10})
    players = result.get("data", []) if result else []

    try:
        await db.bdl_soccer_cache.update_one(
            {"_k": cache_key},
            {"$set": {"_k": cache_key, "d": players, "_ts": datetime.now(timezone.utc)}},
            upsert=True
        )
    except Exception:
        pass
    return players


async def _find_player(league_id: int, player_name: str) -> Optional[dict]:
    """
    Find the best-matching player dict for player_name in the given league.
    Falls back to last-name-only search if full-name search returns nothing.
    """
    players = await _search_player(league_id, player_name)
    if not players:
        # try last-name token
        parts = player_name.strip().split()
        if len(parts) > 1:
            players = await _search_player(league_id, parts[-1])
    if not players:
        return None

    name_lc = player_name.lower()
    # Prefer exact / substring match on display_name
    for p in players:
        display = (p.get("display_name") or "").lower()
        short   = (p.get("short_name")   or "").lower()
        if (name_lc in display or display in name_lc or
                name_lc in short  or short  in name_lc):
            return p
    return players[0]


# ── Per-match stats ───────────────────────────────────────────────────────────
async def _player_match_stats_raw(
    league_id: int, bdl_player_id: int, season: int
) -> list[dict]:
    """Fetch raw BDL player_match_stats rows for one player+season (6-h cache)."""
    path      = LEAGUE_TO_BDL[league_id]
    cache_key = f"bdl_soc_pms_{league_id}_{bdl_player_id}_{season}"
    try:
        doc = await db.bdl_soccer_cache.find_one({"_k": cache_key})
        if doc and doc.get("d") is not None:
            return doc["d"]
    except Exception:
        pass

    result = await _get(f"{path}/player_match_stats",
                        {"player_ids[]": bdl_player_id, "seasons[]": season, "per_page": 40})
    rows = result.get("data", []) if result else []

    try:
        await db.bdl_soccer_cache.update_one(
            {"_k": cache_key},
            {"$set": {"_k": cache_key, "d": rows, "_ts": datetime.now(timezone.utc)}},
            upsert=True
        )
    except Exception:
        pass
    return rows


# ── Main entry point ──────────────────────────────────────────────────────────
async def get_game_logs(
    league_id:   int,
    player_name: str,
    last_n:      int = 25,
) -> tuple[list[dict], Optional[int]]:
    """
    High-level function used by predict.py.

    Returns (game_logs, bdl_player_id).
    game_logs is a list of dicts in the RP game-log format (same fields as
    _build_game_log in routes/predict.py), newest match first.
    Returns ([], None) on any failure.
    """
    if not is_bdl_league(league_id) or not BDL_KEY:
        return [], None

    # 1. Find player
    player = await _find_player(league_id, player_name)
    if not player:
        log.info(f"[BDL-SOC] player not found: '{player_name}' league={league_id}")
        return [], None

    bdl_pid = player.get("id")
    if not bdl_pid:
        return [], None

    # 2. Fetch raw stats — try current season then previous
    all_raw: list[dict] = []
    for season in _CURRENT_SEASONS:
        rows = await _player_match_stats_raw(league_id, bdl_pid, season)
        all_raw.extend(rows)
        if len(all_raw) >= last_n:
            break

    if not all_raw:
        log.info(f"[BDL-SOC] no stats for '{player_name}' (bdl_id={bdl_pid}) league={league_id}")
        return [], None

    # 3. Normalise stats
    logs: list[dict] = []
    for raw in all_raw[:last_n]:
        gl = _norm(raw)
        if not gl.get("minutes"):
            continue
        # Blank match-context fields (filled in step 4)
        gl.update(date="", opponent="", venue="", score="", league="", round="")
        logs.append(gl)

    if not logs:
        return [], None

    # 4. Enrich with match metadata (date, opponent, home/away, score)
    #
    # BDL player_match_stats.match_id is a BDL-internal round scheduling ID
    # that cannot be directly joined to matches.id. Instead we:
    #   (a) use player.team_ids[0] as the reliable team ID (from search result)
    #   (b) fetch the full team schedule for the season via team_ids[] filter
    #   (c) apply sequential matching — both series are newest-first, so
    #       stat_row[i] ↔ team_match[i] (minor drift when player misses a game
    #       only affects opponent-name metadata, never the stat values).
    bdl_team_id: Optional[int] = (player.get("team_ids") or [None])[0]
    if bdl_team_id:
        try:
            # Fetch teams lookup (for opponent name) + team season schedule concurrently
            teams_task   = asyncio.ensure_future(_teams_lookup(league_id))
            match_tasks  = [
                asyncio.ensure_future(_matches_for_team(league_id, bdl_team_id, s))
                for s in _CURRENT_SEASONS
            ]
            teams_map, *match_maps_raw = await asyncio.gather(
                teams_task, *match_tasks, return_exceptions=True
            )
            if isinstance(teams_map, Exception):
                teams_map = {}

            # Build a flat list of all team matches sorted newest-first
            all_team_matches: list[dict] = []
            for mm in match_maps_raw:
                if not isinstance(mm, Exception):
                    all_team_matches.extend(mm.values())
            all_team_matches.sort(key=lambda m: m.get("date", ""), reverse=True)

            # Sequential mapping: stat row i ↔ team match i
            for i, gl in enumerate(logs):
                if i >= len(all_team_matches):
                    break
                m       = all_team_matches[i]
                home_id = m.get("home_team_id")
                away_id = m.get("away_team_id")
                gl["venue"] = "home" if bdl_team_id == home_id else "away"
                opp_id      = away_id if bdl_team_id == home_id else home_id
                gl["opponent"] = (teams_map or {}).get(opp_id, "") if opp_id else ""
                raw_date    = m.get("date") or ""
                gl["date"]  = raw_date[:10] if raw_date else ""
                h_score     = m.get("home_score")
                a_score     = m.get("away_score")
                if h_score is not None and a_score is not None:
                    gl["score"] = f"{h_score}-{a_score}"
                gl["round"] = str(m.get("round_number", "")) if m.get("round_number") else ""

            enriched = sum(1 for g in logs if g.get("opponent"))
            log.info(f"[BDL-SOC] '{player_name}': {len(logs)} logs, {enriched} enriched")
        except Exception as exc:
            log.warning(f"[BDL-SOC] enrichment error for '{player_name}': {exc}")

    # 5. Add per-90 for the target stat (generic — caller will recompute if needed)
    #    Remove internal BDL reference fields before returning
    for gl in logs:
        gl.pop("_bdl_match_id", None)
        gl.pop("_bdl_team_id",  None)
        gl.pop("_bdl_player_id", None)

    return logs, bdl_pid


async def get_player_props(league_id: int, bdl_match_id: int) -> list[dict]:
    """
    Fetch player prop betting lines for a specific BDL match.
    Returns list of prop dicts (market_type, player_id, line, over_odds, under_odds).
    """
    if not is_bdl_league(league_id):
        return []
    path   = LEAGUE_TO_BDL[league_id]
    result = await _get(f"{path}/odds/player_props", {"match_id": bdl_match_id})
    return result.get("data", []) if result else []


async def get_player_injuries(league_id: int) -> list[dict]:
    """
    Fetch the current injury report for a BDL soccer league.
    Returns list of injury dicts with player name and status.
    """
    if not is_bdl_league(league_id):
        return []
    path   = LEAGUE_TO_BDL[league_id]
    result = await _get(f"{path}/player_injuries")
    return result.get("data", []) if result else []
