"""
Prop Safety Cache — live hit-rate table for every (propType, direction) bucket.

Reads from settled picks in MongoDB, computes empirical hit rates, and exposes
a lookup used by the prediction engine to assign data-driven safety ratings.

Refresh cycle: every 6 hours (same cadence as confidence_calibration).
Dedup logic: same player+prop+line+direction+date counts as ONE event regardless
of how many users saved the same pick (prevents multi-save skew).

Safety thresholds (calibrated against n=1,331 settled picks):
  SAFE     : hitRate ≥ 65% AND n ≥ 10   (or ≥ 80% AND n ≥ 5)
  MODERATE : hitRate ≥ 57% AND n ≥ 8
  RISKY    : hitRate 45–57%  OR low sample
  AVOID    : hitRate ≤ 44%  AND n ≥ 5
"""
from __future__ import annotations
import asyncio
from typing import Dict, Optional

_MIN_N_SAFE     = 10
_MIN_N_MODERATE = 8
_MIN_N_AVOID    = 5

_RATE_SAFE_HIGH   = 80   # override: ≥80% with small sample still "SAFE"
_RATE_SAFE        = 65
_RATE_MODERATE    = 57
_RATE_AVOID       = 44

# { "pass_attempts|UNDER": {"hitRate": 64.3, "n": 596, "wins": 383, "losses": 213, "safety": "MODERATE"}, ... }
_CACHE: Dict[str, dict] = {}
_CACHE_LOCK = asyncio.Lock()


def _safety_from_rate(hit_rate: float, n: int) -> str:
    if n < _MIN_N_AVOID:
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
                    "date": {"$ifNull": ["$createdAt", {"$toDate": "$_id"}]},
                }
            },
        }},
        # dedup: one data point per unique event regardless of multi-user saves
        {"$group": {
            "_id": {
                "playerKey":     "$_playerKey",
                "propType":      "$propType",
                "line":          "$line",
                "recommendation":"$recommendation",
                "date":          "$_dateKey",
            },
            "propType":      {"$first": "$propType"},
            "recommendation":{"$first": "$recommendation"},
            "win":           {"$first": "$_win"},
        }},
        # aggregate by prop+direction
        {"$group": {
            "_id": {
                "propType":      "$propType",
                "recommendation":"$recommendation",
            },
            "wins":  {"$sum": "$win"},
            "total": {"$sum": 1},
        }},
    ]

    rows = await db.picks.aggregate(pipe).to_list(None)

    new_cache: Dict[str, dict] = {}
    for r in rows:
        prop      = (r["_id"].get("propType") or "").strip()
        direction = (r["_id"].get("recommendation") or "").upper().strip()
        if not prop or direction not in ("OVER", "UNDER"):
            continue
        wins   = r["wins"]
        total  = r["total"]
        losses = total - wins
        if total == 0:
            continue
        hit_rate = round(wins / total * 100, 1)
        safety   = _safety_from_rate(hit_rate, total)
        key = f"{prop}|{direction}"
        new_cache[key] = {
            "hitRate": hit_rate,
            "n":       total,
            "wins":    wins,
            "losses":  losses,
            "safety":  safety,
        }

    async with _CACHE_LOCK:
        _CACHE.clear()
        _CACHE.update(new_cache)

    summary = {k: f"{v['hitRate']}% ({v['n']}n) → {v['safety']}" for k, v in sorted(new_cache.items())}
    print(f"[PROP SAFETY] refreshed {len(new_cache)} buckets: {summary}")
    return {"buckets": len(new_cache), "data": new_cache}


def get_prop_safety(prop_type: str, direction: str) -> Optional[dict]:
    """
    Returns { hitRate, n, wins, losses, safety } for the given prop+direction,
    or None if no settled data exists for that combination.
    Direction should be 'OVER' or 'UNDER'.
    """
    key = f"{prop_type}|{direction.upper()}"
    return _CACHE.get(key)


def get_all() -> Dict[str, dict]:
    """Return the full cache snapshot (for debug / admin endpoints)."""
    return dict(_CACHE)
