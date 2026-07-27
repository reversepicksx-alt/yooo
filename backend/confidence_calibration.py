"""
Confidence calibration — maps the engine's predicted confidence to the
empirical hit rate observed in settled picks.

Why: a model that says "85% confidence" should hit ~85% of the time.
If the empirical rate at 85% predicted is actually 72%, the model is
overconfident and users sizing bets against it will lose money even if
direction is correct.

How: bucket settled picks by (propType, direction, lineBand, leagueId, position, role),
compute actual hit rate per bucket, expose a lookup that the predict
endpoint applies *only when the bucket has enough samples* (n>=50).
Below that threshold we pass the raw confidence through unchanged so a
small underpopulated bucket can't move the model in a noisy direction.

Direction-aware (v2): OVER and UNDER are calibrated separately per propType.
Mixing them dilutes the signal — e.g. hitter_fantasy_points OVER (32% hit)
and UNDER (70% hit) would average to ~45%, giving no useful correction.

Line-specific (v3): additionally bucket by line band (Low/Mid/High) per
propType. OVER 55.5 passes and OVER 79.5 passes have very different hit
rates; mixing them hides the signal. Falls back to direction-only when
the line-band bucket is too thin (n<50).

Hierarchical (v5): buckets now include leagueId, position, and role.
Because the more dimensions you add, the thinner each bucket gets, we use
hierarchical shrinkage: a child bucket borrows strength from its parent
bucket until it has enough data of its own.

Hierarchical lookup order (most specific → most general):
  propType|DIRECTION|lineBand|leagueId|position|role
  propType|DIRECTION|lineBand|leagueId|position
  propType|DIRECTION|lineBand|leagueId
  propType|DIRECTION|lineBand
  propType|DIRECTION

Blended (v4): instead of a hard override (return empirical rate wholesale),
use James-Stein shrinkage to blend raw score with empirical rate.
    shrink  = n / (n + BLEND_K)   where BLEND_K = 50
    output  = raw * (1 - shrink) + empirical * shrink
At n=50:  50% weight on empirical — avoids slamming the score from a thin bucket.
At n=200: 80% weight on empirical.
At n=500: 91% weight on empirical — near-full trust at high volume.
This prevents the "36-point haircut on Rodri from 20 picks" failure mode while
still allowing large, well-populated buckets to dominate.

Buckets are 10pp wide starting at 50: [50-60), [60-70), [70-80), [80-90), [90-100].
"""
from __future__ import annotations
from typing import Optional, Dict, List, Tuple
import asyncio


# Minimum samples per bucket before calibration fires. Deeper buckets need
# fewer samples because they borrow strength from parent buckets via shrinkage.
_MIN_BUCKET_N = {
    "role":     15,   # propType|dir|band|league|position|role
    "position": 20,   # propType|dir|band|league|position
    "league":   30,   # propType|dir|band|league
    "line":     50,   # propType|dir|band
    "dir":      50,   # propType|dir
}
_BLEND_K      = 50  # James-Stein shrinkage constant: shrink = n/(n+_BLEND_K)
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
#   "pass_attempts|OVER|Mid|262|midfielder|Box-to-Box" -> { bucketLabel: {n, hits, actualRate} }
#   "pass_attempts|OVER|Mid|262|midfielder" -> ...
#   "pass_attempts|OVER|Mid|262" -> ...
#   "pass_attempts|OVER|Mid" -> ...
#   "pass_attempts|OVER" -> ...
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


def _cache_key(
    prop_type: str,
    direction: Optional[str] = None,
    line_band: Optional[str] = None,
    league_id: Optional[int] = None,
    position: Optional[str] = None,
    role: Optional[str] = None,
) -> str:
    """Build the in-memory cache key from most-specific to least-specific dimensions."""
    parts = [prop_type]
    if direction:
        parts.append(direction.upper())
    if line_band:
        parts.append(line_band)
    if league_id is not None:
        parts.append(str(league_id))
    if position:
        parts.append(position.lower())
    if role:
        parts.append(role.lower())
    return "|".join(parts)


def _parse_key_level(key: str) -> str:
    """Return the bucket level of a hierarchical key."""
    n = len(key.split("|"))
    if n >= 6:
        return "role"
    if n == 5:
        return "position"
    if n == 4:
        return "league"
    if n == 3:
        return "line"
    if n == 2:
        return "dir"
    return "prop"


def _hierarchy_keys(
    prop_type: str,
    direction: Optional[str],
    line_band: Optional[str],
    league_id: Optional[int],
    position: Optional[str],
    role: Optional[str],
) -> List[Tuple[str, str]]:
    """
    Return a list of (key, level) tuples from most-specific to least-specific.
    This lets calibrate() walk up the tree until it finds a bucket with data.
    """
    keys: List[Tuple[str, str]] = []
    # role
    if role and position and league_id is not None and line_band and direction:
        keys.append((_cache_key(prop_type, direction, line_band, league_id, position, role), "role"))
    # position
    if position and league_id is not None and line_band and direction:
        keys.append((_cache_key(prop_type, direction, line_band, league_id, position), "position"))
    # league
    if league_id is not None and line_band and direction:
        keys.append((_cache_key(prop_type, direction, line_band, league_id), "league"))
    # line
    if line_band and direction:
        keys.append((_cache_key(prop_type, direction, line_band), "line"))
    # direction
    if direction:
        keys.append((_cache_key(prop_type, direction), "dir"))
    return keys


async def refresh_calibration(db) -> dict:
    """
    Recompute the calibration table from settled picks.
    Direction-aware: separate OVER/UNDER buckets per propType.
    Line-aware (v3): also builds propType|DIRECTION|line_band buckets.
    Hierarchical (v5): also builds league, position, and role buckets.
    Stores one doc per (propType, direction, lineBand, leagueId, position, role) in MongoDB
    collection `confidence_calibration`, and keeps an in-memory cache for hot lookups.
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
            "_position": {"$ifNull": ["$position", "$player.position", "any"]},
            "_role": {"$ifNull": ["$role", "$player.role", ""]},
            "_leagueId": {"$ifNull": ["$leagueId", 0]},
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
            "_leagueId":      {"$first": "$_leagueId"},
            "_position":      {"$first": "$_position"},
            "_role":          {"$first": "$_role"},
        }},
        # ── CALIBRATION BUCKETS — carry all dimensions through for Python-side band calc ─
        {"$group": {
            "_id": {
                "propType":  "$propType",
                "direction": {"$toUpper": {"$ifNull": ["$recommendation", "OVER"]}},
                "score":     "$_trainScore",
                "line":      "$line",
                "leagueId":  "$_leagueId",
                "position":  "$_position",
                "role":      "$_role",
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
        league_id = r["_id"].get("leagueId")
        position  = r["_id"].get("position") or "any"
        role      = r["_id"].get("role") or ""

        # Build every level of the hierarchy so we can walk up at lookup time.
        for key, _ in _hierarchy_keys(prop, direction, _line_band_for(prop, line_val), league_id, position, role):
            _add(key, bucket, n, hits)

    # Compute actual rates and persist to mongo
    total_buckets = 0
    for key, buckets in new_cache.items():
        parts = key.split("|")
        prop = parts[0]
        direction = parts[1] if len(parts) >= 2 else None
        line_band = parts[2] if len(parts) >= 3 else None
        league_id = int(parts[3]) if len(parts) >= 4 and parts[3].isdigit() else None
        position = parts[4] if len(parts) >= 5 else None
        role = parts[5] if len(parts) >= 6 else None
        for bucket, cell in buckets.items():
            cell["actualRate"] = round(cell["hits"] / cell["n"] * 100, 1) if cell["n"] else None
            total_buckets += 1
        await db.confidence_calibration.update_one(
            {"propType": prop, "direction": direction, "lineBand": line_band,
             "leagueId": league_id, "position": position, "role": role},
            {"$set": {
                "propType":  prop,
                "direction": direction,
                "lineBand":  line_band,
                "leagueId":  league_id,
                "position":  position,
                "role":      role,
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


def _blend(raw_score: float, empirical: float, n: int) -> float:
    """
    James-Stein blend of raw engine score and empirical hit rate.
    shrink = n / (n + _BLEND_K)
    output = raw*(1-shrink) + empirical*shrink
    Clamped to [50, 100].
    """
    shrink = n / (n + _BLEND_K)
    blended = raw_score * (1.0 - shrink) + empirical * shrink
    return round(max(50.0, min(100.0, blended)), 1)


def calibrate(
    prop_type: str,
    raw_score: float,
    direction: Optional[str] = None,
    line: Optional[float] = None,
    league_id: Optional[int] = None,
    position: Optional[str] = None,
    role: Optional[str] = None,
) -> Optional[float]:
    """
    Returns calibrated confidence (0-100) for a (propType, direction, raw_score, league, position, role).

    Lookup priority (v5 — hierarchical shrinkage):
      1. propType|DIRECTION|line_band|leagueId|position|role  — most specific
      2. propType|DIRECTION|line_band|leagueId|position
      3. propType|DIRECTION|line_band|leagueId
      4. propType|DIRECTION|line_band
      5. propType|DIRECTION
      6. None — pass raw score through unchanged

    Uses James-Stein shrinkage so thin buckets only nudge the score rather than
    slamming it. Returns None when no qualified bucket exists so the caller passes
    raw confidence through unchanged.
    """
    if raw_score is None or raw_score <= 0:
        return None
    bucket = _bucket_for(raw_score)

    if not direction:
        return None

    line_band = _line_band_for(prop_type, line)
    for key, level in _hierarchy_keys(prop_type, direction.upper(), line_band, league_id, position, role):
        buckets = _CALIBRATION_CACHE.get(key)
        if not buckets:
            continue
        cell = buckets.get(bucket)
        if not cell:
            continue
        n = cell.get("n", 0)
        min_n = _MIN_BUCKET_N.get(level, 50)
        if n >= min_n:
            actual = cell.get("actualRate")
            if actual is not None:
                return _blend(raw_score, actual, n)

    return None


def get_cache_snapshot() -> Dict[str, Dict[str, dict]]:
    """For debugging: return the current calibration cache."""
    return dict(_CALIBRATION_CACHE)
