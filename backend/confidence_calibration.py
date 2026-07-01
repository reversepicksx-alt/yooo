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

Line-specific (v3): additionally bucket by line band (Low/Mid/High) per
propType. OVER 55.5 passes and OVER 79.5 passes have very different hit
rates; mixing them hides the signal. Falls back to direction-only when
the line-band bucket is too thin (n<20).

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

# Line bands per prop type — list of (lower_inclusive, upper_exclusive, label).
# Defines what counts as a "Low", "Mid", or "High" line for each prop.
# Props not listed here fall back to direction-only calibration.
_LINE_BANDS: Dict[str, list] = {
    "pass_attempts":    [(0, 55, "Low"), (55, 70, "Mid"), (70, 9999, "High")],
    "passes":           [(0, 55, "Low"), (55, 70, "Mid"), (70, 9999, "High")],
    "passes_attempted": [(0, 55, "Low"), (55, 70, "Mid"), (70, 9999, "High")],
    "shots":            [(0, 2.0, "Low"), (2.0, 3.5, "Mid"), (3.5, 9999, "High")],
    "shots_on_target":  [(0, 1.0, "Low"), (1.0, 2.0, "Mid"), (2.0, 9999, "High")],
    "saves":            [(0, 3.0, "Low"), (3.0, 5.0, "Mid"), (5.0, 9999, "High")],
    "goalie_saves":     [(0, 3.0, "Low"), (3.0, 5.0, "Mid"), (5.0, 9999, "High")],
    "tackles":          [(0, 2.0, "Low"), (2.0, 4.0, "Mid"), (4.0, 9999, "High")],
    "key_passes":       [(0, 1.0, "Low"), (1.0, 2.0, "Mid"), (2.0, 9999, "High")],
    "clearances":       [(0, 3.0, "Low"), (3.0, 6.0, "Mid"), (6.0, 9999, "High")],
    "interceptions":    [(0, 1.0, "Low"), (1.0, 3.0, "Mid"), (3.0, 9999, "High")],
    "dribbles":         [(0, 1.0, "Low"), (1.0, 2.5, "Mid"), (2.5, 9999, "High")],
    "dribbles_success": [(0, 1.0, "Low"), (1.0, 2.5, "Mid"), (2.5, 9999, "High")],
    "duels_won":        [(0, 3.0, "Low"), (3.0, 6.0, "Mid"), (6.0, 9999, "High")],
    "goals":            [(0, 0.5, "Low"), (0.5, 9999, "High")],
    "assists":          [(0, 0.5, "Low"), (0.5, 9999, "High")],
    "fouls_drawn":      [(0, 1.5, "Low"), (1.5, 3.0, "Mid"), (3.0, 9999, "High")],
    "fouls_committed":  [(0, 1.5, "Low"), (1.5, 3.0, "Mid"), (3.0, 9999, "High")],
    "crosses":          [(0, 1.5, "Low"), (1.5, 3.5, "Mid"), (3.5, 9999, "High")],
}

# In-memory cache:
#   direction-only:  { "pass_attempts|OVER":     { bucketLabel: {n, hits, actualRate} } }
#   line-specific:   { "pass_attempts|OVER|Mid": { bucketLabel: {n, hits, actualRate} } }
_CALIBRATION_CACHE: Dict[str, Dict[str, dict]] = {}
_CACHE_LOCK = asyncio.Lock()


def _bucket_for(score: float) -> str:
    """Map a numeric confidence score to its bucket label."""
    for i, upper in enumerate(_BUCKET_BOUNDARIES[1:], start=1):
        if score < upper:
            return _BUCKET_LABELS[i - 1]
    return _BUCKET_LABELS[-1]


def _line_band_for(prop_type: str, line: Optional[float]) -> Optional[str]:
    """
    Map a line value to a band label (Low/Mid/High) for a given prop type.
    Returns None if the prop type has no configured bands or line is missing.
    """
    if line is None:
        return None
    bands = _LINE_BANDS.get(prop_type)
    if not bands:
        return None
    for lo, hi, label in bands:
        if lo <= line < hi:
            return label
    return bands[-1][2]


def _cache_key(prop_type: str, direction: Optional[str] = None, line_band: Optional[str] = None) -> str:
    """Build the in-memory cache key from prop_type, optional direction, and optional line_band."""
    key = prop_type
    if direction:
        key = f"{key}|{direction.upper()}"
    if line_band:
        key = f"{key}|{line_band}"
    return key


async def refresh_calibration(db) -> dict:
    """
    Recompute the calibration table from settled picks.
    Direction-aware: separate OVER/UNDER buckets per propType.
    Line-aware (v3): also builds propType|DIRECTION|line_band buckets.
    Stores one doc per (propType, direction, lineBand) in MongoDB collection
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
            "_dateKey": {"$substr": [{"$ifNull": ["$settledAt", "$timestamp"]}, 0, 10]},
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
            "line":           {"$first": "$line"},
        }},
        # ── CALIBRATION BUCKETS — carry line through for Python-side band calc ─
        {"$group": {
            "_id": {
                "propType":  "$propType",
                "direction": {"$toUpper": {"$ifNull": ["$recommendation", "OVER"]}},
                "score":     "$_trainScore",
                "line":      "$line",
            },
            "n":    {"$sum": 1},
            "hits": {"$sum": {"$cond": [{"$eq": ["$result", "hit"]}, 1, 0]}},
        }},
    ]
    rows = await db.picks.aggregate(pipe_per_prop).to_list(None)

    new_cache: Dict[str, Dict[str, dict]] = {}

    def _add(key: str, bucket: str, n: int, hits: int):
        cell = new_cache.setdefault(key, {}).setdefault(bucket, {"n": 0, "hits": 0})
        cell["n"]    += n
        cell["hits"] += hits

    for r in rows:
        prop      = r["_id"]["propType"]
        direction = r["_id"].get("direction", "OVER")
        score     = r["_id"]["score"]
        line_val  = r["_id"].get("line")
        bucket    = _bucket_for(score)
        n         = r["n"]
        hits      = r["hits"]

        # Direction-only bucket (existing behaviour)
        _add(_cache_key(prop, direction), bucket, n, hits)

        # Line-specific bucket (v3)
        band = _line_band_for(prop, line_val)
        if band:
            _add(_cache_key(prop, direction, band), bucket, n, hits)

    # Compute actual rates and persist to mongo
    total_buckets = 0
    for key, buckets in new_cache.items():
        parts     = key.split("|")
        prop      = parts[0]
        direction = parts[1] if len(parts) >= 2 else None
        line_band = parts[2] if len(parts) >= 3 else None
        for bucket, cell in buckets.items():
            cell["actualRate"] = round(cell["hits"] / cell["n"] * 100, 1) if cell["n"] else None
            total_buckets += 1
        await db.confidence_calibration.update_one(
            {"propType": prop, "direction": direction, "lineBand": line_band},
            {"$set": {
                "propType":  prop,
                "direction": direction,
                "lineBand":  line_band,
                "buckets":   buckets,
            }},
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


def calibrate(
    prop_type: str,
    raw_score: float,
    direction: Optional[str] = None,
    line: Optional[float] = None,
) -> Optional[float]:
    """
    Returns calibrated confidence (0-100) for a (propType, direction, raw_score).

    Lookup priority (v3):
      1. propType|DIRECTION|line_band  — most specific, needs n>=20
      2. propType|DIRECTION            — direction-only fallback
      3. None                          — pass raw score through unchanged

    Returns None if no reliable bucket exists — caller should pass through raw.
    """
    if raw_score is None or raw_score <= 0:
        return None
    bucket = _bucket_for(raw_score)

    if direction:
        # 1. Try line-specific bucket first (most precise signal)
        band = _line_band_for(prop_type, line)
        if band:
            line_key = _cache_key(prop_type, direction, band)
            line_buckets = _CALIBRATION_CACHE.get(line_key)
            if line_buckets:
                cell = line_buckets.get(bucket)
                if cell and cell.get("n", 0) >= _MIN_BUCKET_N:
                    actual = cell.get("actualRate")
                    if actual is not None:
                        return float(actual)

        # 2. Fall back to direction-only bucket
        dir_key = _cache_key(prop_type, direction)
        prop_buckets = _CALIBRATION_CACHE.get(dir_key)
        if prop_buckets:
            cell = prop_buckets.get(bucket)
            if cell and cell.get("n", 0) >= _MIN_BUCKET_N:
                actual = cell.get("actualRate")
                return float(actual) if actual is not None else None

    return None


def get_cache_snapshot() -> Dict[str, Dict[str, dict]]:
    """For debugging: return the current calibration cache."""
    return dict(_CALIBRATION_CACHE)
