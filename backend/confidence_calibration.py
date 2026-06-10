"""
Confidence calibration — maps the engine's predicted confidence to the
empirical hit rate observed in settled picks.

Why: a model that says "85% confidence" should hit ~85% of the time.
If the empirical rate at 85% predicted is actually 72%, the model is
overconfident and users sizing bets against it will lose money even if
direction is correct.

How: bucket settled picks by (propType, direction, predicted-confidence-bucket),
compute actual hit rate per bucket, expose a lookup that the predict
endpoint applies *only when the bucket has enough samples* (n>=20).
Below that threshold we pass the raw confidence through unchanged so a
small underpopulated bucket can't move the model in a noisy direction.

Direction-aware (v2): OVER and UNDER are calibrated separately per propType.
Mixing them dilutes the signal — e.g. hitter_fantasy_points OVER (32% hit)
and UNDER (70% hit) would average to ~45%, giving no useful correction.

Buckets are 10pp wide starting at 50: [50-60), [60-70), [70-80), [80-90), [90-100].
"""
from __future__ import annotations
from typing import Optional, Dict
import asyncio


_MIN_BUCKET_N = 20  # minimum samples per bucket for reliable calibration
_BUCKET_BOUNDARIES = [0, 50, 60, 70, 80, 90, 101]
_BUCKET_LABELS = ["<50", "50-59", "60-69", "70-79", "80-89", "90+"]

# Calibration only uses picks settled AFTER this cutoff. Reason: every pick
# saved before this date has confidenceScore=50 (the mobile-side placeholder
# bug — fixed on 2026-04-30). The cutoff is set forward to give the production
# deploy time to ship AND for real-confidence picks to accumulate + settle.
# Until the cutoff is reached, calibrate() returns None for every call and
# the engine passes raw confidence through unchanged. This is intentional:
# bad calibration is worse than no calibration.
_CUTOFF_ISO = "2026-04-30T00:00:00+00:00"

# In-memory cache: { "propType|DIRECTION": { bucketLabel: {n, hits, actualRate} } }
# Direction-specific keys: "pass_attempts|OVER", "pass_attempts|UNDER"
_CALIBRATION_CACHE: Dict[str, Dict[str, dict]] = {}
_CACHE_LOCK = asyncio.Lock()


def _bucket_for(score: float) -> str:
    """Map a numeric confidence score to its bucket label."""
    for i, upper in enumerate(_BUCKET_BOUNDARIES[1:], start=1):
        if score < upper:
            return _BUCKET_LABELS[i - 1]
    return _BUCKET_LABELS[-1]


def _cache_key(prop_type: str, direction: Optional[str] = None) -> str:
    """Build the in-memory cache key from prop_type and optional direction."""
    if direction:
        return f"{prop_type}|{direction.upper()}"
    return prop_type


async def refresh_calibration(db) -> dict:
    """
    Recompute the calibration table from settled picks.
    Direction-aware: separate OVER/UNDER buckets per propType.
    Stores one doc per (propType, direction) in MongoDB collection
    `confidence_calibration`, and keeps an in-memory cache for hot lookups.
    """
    pipe_per_prop = [
        {"$match": {
            "result": {"$in": ["hit", "miss"]},
            "propType": {"$ne": None},
            "settledAt": {"$gte": _CUTOFF_ISO},
        }},
        {"$addFields": {
            "_trainScore": {"$ifNull": ["$rawConfidence", "$confidenceScore"]},
            # Date portion of settledAt for same-day dedup
            "_dateKey": {"$substr": [{"$ifNull": ["$settledAt", "$timestamp"]}, 0, 10]},
            # Canonical player key
            "_playerKey": {"$ifNull": [
                {"$toString": "$playerId"},
                {"$ifNull": ["$playerNameKey", "$playerName"]},
            ]},
        }},
        {"$match": {"_trainScore": {"$ne": None, "$gt": 0}}},
        # ── DEDUP ──────────────────────────────────────────────────────────────
        # Each unique prediction (same player + prop + line + direction + day)
        # should count as ONE data point regardless of how many users saved it.
        {"$group": {
            "_id": {
                "playerKey":      "$_playerKey",
                "propType":       "$propType",
                "line":           "$line",
                "recommendation": "$recommendation",
                "date":           "$_dateKey",
            },
            "propType":       {"$first": "$propType"},
            "recommendation": {"$first": "$recommendation"},
            "_trainScore":    {"$first": "$_trainScore"},
            "result":         {"$first": "$result"},
        }},
        # ── CALIBRATION BUCKETS — direction-aware ─────────────────────────────
        {"$group": {
            "_id": {
                "propType":  "$propType",
                "direction": {"$toUpper": {"$ifNull": ["$recommendation", "OVER"]}},
                "score":     "$_trainScore",
            },
            "n":    {"$sum": 1},
            "hits": {"$sum": {"$cond": [{"$eq": ["$result", "hit"]}, 1, 0]}},
        }},
    ]
    rows = await db.picks.aggregate(pipe_per_prop).to_list(None)

    new_cache: Dict[str, Dict[str, dict]] = {}
    for r in rows:
        prop      = r["_id"]["propType"]
        direction = r["_id"].get("direction", "OVER")
        score     = r["_id"]["score"]
        bucket    = _bucket_for(score)
        key       = _cache_key(prop, direction)
        cell      = new_cache.setdefault(key, {}).setdefault(bucket, {"n": 0, "hits": 0})
        cell["n"]    += r["n"]
        cell["hits"] += r["hits"]

    # Compute actual rates and persist to mongo
    total_buckets = 0
    for key, buckets in new_cache.items():
        parts     = key.split("|", 1)
        prop      = parts[0]
        direction = parts[1] if len(parts) > 1 else None
        for bucket, cell in buckets.items():
            cell["actualRate"] = round(cell["hits"] / cell["n"] * 100, 1) if cell["n"] else None
            total_buckets += 1
        await db.confidence_calibration.update_one(
            {"propType": prop, "direction": direction},
            {"$set": {"propType": prop, "direction": direction, "buckets": buckets}},
            upsert=True,
        )

    async with _CACHE_LOCK:
        _CALIBRATION_CACHE.clear()
        _CALIBRATION_CACHE.update(new_cache)

    return {
        "keys":         list(new_cache.keys()),
        "totalBuckets": total_buckets,
        "minBucketN":   _MIN_BUCKET_N,
    }


def calibrate(prop_type: str, raw_score: float, direction: Optional[str] = None) -> Optional[float]:
    """
    Returns calibrated confidence (0-100) for a (propType, direction, raw_score).
    Tries direction-specific bucket first; no generic fallback since mixing
    OVER/UNDER rates would dilute the signal.
    Returns None if no reliable bucket exists — caller should pass through raw.
    """
    if raw_score is None or raw_score <= 0:
        return None
    bucket = _bucket_for(raw_score)

    if direction:
        key = _cache_key(prop_type, direction)
        prop_buckets = _CALIBRATION_CACHE.get(key)
        if prop_buckets:
            cell = prop_buckets.get(bucket)
            if cell and cell.get("n", 0) >= _MIN_BUCKET_N:
                actual = cell.get("actualRate")
                return float(actual) if actual is not None else None

    return None


def get_cache_snapshot() -> Dict[str, Dict[str, dict]]:
    """For debugging: return the current calibration cache."""
    return dict(_CALIBRATION_CACHE)
