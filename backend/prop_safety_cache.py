"""
Prop Safety Cache — live hit-rate table for every (propType, direction, leagueId, position) bucket.

Reads from settled picks in MongoDB, computes empirical hit rates, and exposes
a lookup used by the prediction engine to assign data-driven safety ratings.

Refresh cycle: every 6 hours (same cadence as confidence_calibration).
Dedup logic: same player+prop+line+direction+date counts as ONE event regardless
of how many users saved the same pick (prevents multi-save skew).

Safety thresholds (calibrated against n=1,331 settled picks):
  SAFE     : hitRate >= 65% AND n >= 10   (or >= 80% AND n >= 5)
  MODERATE : hitRate >= 57% AND n >= 8
  RISKY    : hitRate 45-57%  OR low sample
  AVOID    : hitRate <= 44%  AND n >= 5

Hierarchical (v2): because league+position buckets get thin quickly, we also
build parent (propType|direction) buckets and fall back to them when a child
bucket is too small. This lets a Liga MX midfielder prop get a specific tag
while a brand-new tournament gets a global tag.
"""
from __future__ import annotations
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

_MIN_N_SAFE     = 10
_MIN_N_MODERATE = 8
_MIN_N_AVOID    = 5
_MIN_N_LEAGUE   = 5   # child buckets can fire with fewer samples than global
_MIN_N_POSITION = 3
_MIN_N_RECENT_PASS = 10
_ROLLING_DAYS = 45

_RATE_SAFE_HIGH   = 80
_RATE_SAFE        = 65
_RATE_MODERATE    = 57
_RATE_AVOID       = 44

# In-memory cache:
#   "pass_attempts|UNDER" -> { hitRate, n, wins, losses, safety }
#   "pass_attempts|UNDER|262" -> { ... }
#   "pass_attempts|UNDER|262|midfielder" -> { ... }
_CACHE: Dict[str, dict] = {}
_CACHE_LOCK = asyncio.Lock()


def _safety_from_rate(hit_rate: float, n: int, child_min_n: int = _MIN_N_AVOID) -> str:
    if n < child_min_n:
        return "RISKY"
    if hit_rate <= _RATE_AVOID and n >= _MIN_N_AVOID:
        return "AVOID"
    if n >= _MIN_N_SAFE and hit_rate >= _RATE_SAFE:
        return "SAFE"
    if n >= _MIN_N_MODERATE and hit_rate >= _RATE_SAFE_HIGH:
        return "SAFE"
    if n >= _MIN_N_MODERATE and hit_rate >= _RATE_MODERATE:
        return "MODERATE"
    return "RISKY"


async def refresh_prop_safety(db) -> dict:
    """
    Recompute the prop safety table from all settled picks.
    Deduplicates so each unique (player, prop, line, direction, date) = 1 event.
    Builds hierarchical keys: global, league, and league+position.
    """
    pipe = [
        {"$match": {"status": "settled", "result": {"$exists": True}}},
        # normalise result to win/loss
        {"$addFields": {
            "_win": {"$cond": [
                {"$in": ["$result", ["hit", "win", "Hit", "Win"]]}, 1, 0
            ]},
            "_playerKey": {"$ifNull": ["$playerName", "unknown"]},
            "_dateKey": {
                "$dateToString": {
                    "format": "%Y-%m-%d",
                    "date": {
                        "$convert": {
                            "input": {
                                "$ifNull": [
                                    "$timestamp",
                                    {"$ifNull": ["$createdAt", "$_id"]},
                                ]
                            },
                            "to": "date",
                            "onError": None,
                            "onNull": None,
                        }
                    },
                    "onNull": "",
                }
            },
            "_position": {"$ifNull": ["$position", "$player.position", "any"]},
            "_leagueId": {"$ifNull": ["$leagueId", 0]},
        }},
        # dedup: one data point per unique event regardless of multi-user saves
        {"$group": {
            "_id": {
                "playerKey":      "$_playerKey",
                "propType":       "$propType",
                "line":           "$line",
                "recommendation": "$recommendation",
                "date":           "$_dateKey",
            },
            "propType":      {"$first": "$propType"},
            "recommendation":{"$first": "$recommendation"},
            "win":           {"$first": "$_win"},
            "leagueId":      {"$first": "$_leagueId"},
            "position":      {"$first": "$_position"},
        }},
    ]

    rows = await db.picks.aggregate(pipe).to_list(None)

    # Build both all-time and rolling-window buckets from one row per unique
    # event.  The previous implementation grouped before this point, which
    # made it impossible to tell whether an apparently bad bucket was still
    # bad recently or only reflected an old regime.
    new_cache: Dict[str, dict] = {}
    global_stats: Dict[str, dict] = {}
    league_stats: Dict[str, dict] = {}
    pos_stats: Dict[str, dict] = {}
    recent_global_stats: Dict[str, dict] = {}
    recent_league_stats: Dict[str, dict] = {}
    recent_pos_stats: Dict[str, dict] = {}
    recent_cutoff = datetime.now(timezone.utc).date() - timedelta(days=_ROLLING_DAYS)

    def _ensure(d, key):
        if key not in d:
            d[key] = {"wins": 0, "total": 0}
        return d[key]

    for r in rows:
        event_id = r.get("_id") or {}
        prop      = (r.get("propType") or event_id.get("propType") or "").strip()
        direction = (r.get("recommendation") or event_id.get("recommendation") or "").upper().strip()
        if not prop or direction not in ("OVER", "UNDER"):
            continue
        wins   = int(r.get("win", 0))
        league_id = r.get("leagueId", 0)
        position = (r.get("position") or "any").lower()

        # Global
        g = _ensure(global_stats, f"{prop}|{direction}")
        g["wins"] += wins
        g["total"] += 1

        # League
        l = _ensure(league_stats, f"{prop}|{direction}|{league_id}")
        l["wins"] += wins
        l["total"] += 1

        # League + position
        p = _ensure(pos_stats, f"{prop}|{direction}|{league_id}|{position}")
        p["wins"] += wins
        p["total"] += 1

        event_date = event_id.get("date")
        try:
            event_date = datetime.strptime(str(event_date), "%Y-%m-%d").date()
        except (TypeError, ValueError):
            event_date = None
        if event_date and event_date >= recent_cutoff:
            rg = _ensure(recent_global_stats, f"{prop}|{direction}")
            rg["wins"] += wins
            rg["total"] += 1
            rl = _ensure(recent_league_stats, f"{prop}|{direction}|{league_id}")
            rl["wins"] += wins
            rl["total"] += 1
            rp = _ensure(recent_pos_stats, f"{prop}|{direction}|{league_id}|{position}")
            rp["wins"] += wins
            rp["total"] += 1

    def _finalize(stats, min_n, recent_stats=None):
        for key, v in stats.items():
            total = v["total"]
            if total == 0:
                continue
            hit_rate = round(v["wins"] / total * 100, 1)
            safety = _safety_from_rate(hit_rate, total, min_n)
            recent = (recent_stats or {}).get(key, {})
            recent_total = recent.get("total", 0)
            recent_rate = (
                round(recent["wins"] / recent_total * 100, 1)
                if recent_total else None
            )
            new_cache[key] = {
                "hitRate": hit_rate,
                "n":       total,
                "wins":    v["wins"],
                "losses":  total - v["wins"],
                "safety":  safety,
                "recentHitRate": recent_rate,
                "recentN": recent_total,
                "recentWins": recent.get("wins", 0),
                "recentLosses": recent_total - recent.get("wins", 0),
            }

    _finalize(global_stats, _MIN_N_AVOID, recent_global_stats)
    _finalize(league_stats, _MIN_N_LEAGUE, recent_league_stats)
    _finalize(pos_stats, _MIN_N_POSITION, recent_pos_stats)

    async with _CACHE_LOCK:
        _CACHE.clear()
        _CACHE.update(new_cache)

    summary = {k: f"{v['hitRate']}% ({v['n']}n) -> {v['safety']}" for k, v in sorted(new_cache.items())}
    print(f"[PROP SAFETY] refreshed {len(new_cache)} buckets: {summary}")
    return {"buckets": len(new_cache), "data": new_cache}


def get_prop_safety(
    prop_type: str,
    direction: str,
    league_id: Optional[int] = None,
    position: Optional[str] = None,
) -> Optional[dict]:
    """
    Returns { hitRate, n, wins, losses, safety } for the given prop+direction,
    optionally league-aware and position-aware.
    Direction should be 'OVER' or 'UNDER'.
    """
    direction = direction.upper()

    # Try most specific first
    if league_id is not None and position:
        key = f"{prop_type}|{direction}|{league_id}|{position.lower()}"
        if key in _CACHE and _CACHE[key]["n"] >= _MIN_N_POSITION:
            return _CACHE[key]

    # League only
    if league_id is not None:
        key = f"{prop_type}|{direction}|{league_id}"
        if key in _CACHE and _CACHE[key]["n"] >= _MIN_N_LEAGUE:
            return _CACHE[key]

    # Global fallback
    key = f"{prop_type}|{direction}"
    return _CACHE.get(key)


def get_recent_prop_safety(
    prop_type: str,
    direction: str,
    league_id: Optional[int] = None,
    position: Optional[str] = None,
    min_n: int = _MIN_N_RECENT_PASS,
) -> Optional[dict]:
    """Return the most specific bucket with enough recent settled events."""
    direction = direction.upper()
    keys = []
    if league_id is not None and position:
        keys.append(f"{prop_type}|{direction}|{league_id}|{position.lower()}")
    if league_id is not None:
        keys.append(f"{prop_type}|{direction}|{league_id}")
    keys.append(f"{prop_type}|{direction}")
    for key in keys:
        bucket = _CACHE.get(key)
        if bucket and bucket.get("recentN", 0) >= min_n:
            return {
                **bucket,
                "hitRate": bucket.get("recentHitRate"),
                "n": bucket.get("recentN", 0),
                "wins": bucket.get("recentWins", 0),
                "losses": bucket.get("recentLosses", 0),
            }
    return None


def get_all() -> Dict[str, dict]:
    """Return the full cache snapshot (for debug / admin endpoints)."""
    return dict(_CACHE)
