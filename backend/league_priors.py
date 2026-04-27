"""
LEAGUE-AWARE EMPIRICAL CALIBRATION

Mines all settled picks to compute the systematic projection bias and
hit-rate edge for each (leagueId, position, propType, recommendation)
bucket, then exposes a small, well-shrunken corrective nudge that the
Bayesian engine can apply post-posterior.

The headline insight from the cheat-sheet analysis:
  Same (position, prop, side) rules behave radically differently
  across leagues — Away CB UNDER passes hits 100% in Ligue 1 / La Liga,
  but only 33% in Bundesliga. This module captures that empirically and
  feeds the engine a league-specific correction.

Design principles:
  * SHRUNK: tiny samples get tiny corrections. We use a simple
    James-Stein-flavoured shrinkage so a 4-pick bucket nudges <1%
    while a 30-pick bucket can nudge up to ~6%.
  * BIAS-BASED: we measure mean(actualValue - projectedValue) inside
    the bucket and turn that into a multiplicative correction on the
    posterior mean. (e.g. if Bundesliga CBs systematically over-pass
    by 8 vs the engine's projection, future Bundesliga CB pass UNDERs
    are nudged UP a bit so the engine stops calling them under so often.)
  * CACHED: refreshed every 6 hours, never blocks a request.
"""
from __future__ import annotations
import os
import time
import asyncio
from typing import Optional
from collections import defaultdict


_REFRESH_SECS = 6 * 3600        # 6 hours
_MIN_SAMPLE   = 4               # below this, return no correction
_SHRINK_K     = 25              # half-strength at n=25, full strength asymptote
_MAX_NUDGE    = 0.06            # cap correction at ±6% of posterior

_cache = {
    "ts":      0.0,
    "buckets": {},   # key: (league_id, position, prop_type, recommendation) → stats dict
    "loaded":  False,
}


def _bucket_key(league_id, position, prop_type, recommendation):
    return (
        int(league_id) if league_id else 0,
        (position or "").upper().strip(),
        (prop_type or "").lower().strip(),
        (recommendation or "").lower().strip(),
    )


async def _refresh(db) -> None:
    """Recompute every bucket from scratch using settled picks."""
    cursor = db.picks.find(
        {"result": {"$in": ["hit", "miss"]},
         "recommendation": {"$in": ["over", "under"]},
         "actualValue": {"$ne": None},
         "projectedValue": {"$ne": None}},
        {"_id": 0, "leagueId": 1, "position": 1, "propType": 1,
         "recommendation": 1, "result": 1, "actualValue": 1,
         "projectedValue": 1, "line": 1},
    )
    rows = await cursor.to_list(length=20000)

    agg = defaultdict(lambda: {"n": 0, "hits": 0, "errors": []})
    for p in rows:
        key = _bucket_key(p.get("leagueId"), p.get("position"),
                          p.get("propType"), p.get("recommendation"))
        if key[1] == "" or key[2] == "" or key[3] == "":
            continue
        try:
            err = float(p["actualValue"]) - float(p["projectedValue"])
        except (TypeError, ValueError):
            continue
        b = agg[key]
        b["n"] += 1
        if p.get("result") == "hit":
            b["hits"] += 1
        b["errors"].append(err)

    buckets = {}
    for key, b in agg.items():
        n = b["n"]
        if n < _MIN_SAMPLE:
            continue
        mean_err = sum(b["errors"]) / n
        hit_rate = b["hits"] / n
        # Shrinkage factor: 0 at n=1 → 0.5 at n=25 → 1.0 at n=∞
        shrink = n / (n + _SHRINK_K)
        buckets[key] = {
            "n":         n,
            "hit_rate":  round(hit_rate, 3),
            "mean_err":  round(mean_err, 2),
            "shrink":    round(shrink, 3),
        }

    _cache["buckets"] = buckets
    _cache["ts"]      = time.time()
    _cache["loaded"]  = True
    print(f"[LEAGUE PRIORS] refreshed: {len(buckets)} buckets from {len(rows)} settled picks")


async def ensure_loaded(db) -> None:
    """Load the calibration table if stale or missing."""
    if not _cache["loaded"] or (time.time() - _cache["ts"]) > _REFRESH_SECS:
        try:
            await _refresh(db)
        except Exception as e:
            print(f"[LEAGUE PRIORS] refresh failed: {e}")


def lookup(league_id, position, prop_type, recommendation,
           posterior_mean: float) -> dict:
    """
    Return a calibration nudge for this (league, pos, prop, side) bucket.

    Returns a dict with:
      multiplier:  small multiplicative factor on posterior_mean (1.0 ± _MAX_NUDGE)
      bias:        raw additive bias estimate from the sample (informational)
      hit_rate:    historical hit-rate of this exact bucket (informational)
      n:           bucket sample size
      direction:   'boost'|'cut'|'neutral' (intent of the nudge)

    Special case: if both (league_id, pos, prop, rec) AND a fallback
    (league_id=0, pos, prop, rec) lookup miss, returns an inert
    {'multiplier': 1.0, ...} so the caller can blindly apply it.
    """
    inert = {"multiplier": 1.0, "bias": 0.0, "hit_rate": None,
             "n": 0, "direction": "neutral", "found": False}
    if not _cache["loaded"] or posterior_mean is None or posterior_mean == 0:
        return inert

    key = _bucket_key(league_id, position, prop_type, recommendation)
    b = _cache["buckets"].get(key)

    # Fallback: cross-league bucket (position+prop+side only)
    cross_key = (0, key[1], key[2], key[3])
    if not b:
        cross = None
        # build a virtual cross-league average on the fly
        cross_n = 0; cross_err = 0.0; cross_hits = 0
        for k, v in _cache["buckets"].items():
            if (k[1], k[2], k[3]) == (key[1], key[2], key[3]):
                cross_n += v["n"]; cross_err += v["mean_err"] * v["n"]; cross_hits += int(round(v["hit_rate"] * v["n"]))
        if cross_n >= _MIN_SAMPLE:
            mean_err = cross_err / cross_n
            shrink = (cross_n / (cross_n + _SHRINK_K)) * 0.5  # cross-league: half strength
            b = {
                "n": cross_n,
                "hit_rate": round(cross_hits / cross_n, 3),
                "mean_err": round(mean_err, 2),
                "shrink":   round(shrink, 3),
            }
        else:
            return inert

    bias = b["mean_err"]                 # raw error in the same units as the projection
    rel_bias = bias / max(abs(posterior_mean), 1e-6)
    nudge = max(-_MAX_NUDGE, min(_MAX_NUDGE, rel_bias * b["shrink"]))
    direction = "boost" if nudge > 0.005 else ("cut" if nudge < -0.005 else "neutral")
    return {
        "multiplier": round(1.0 + nudge, 4),
        "bias":       round(bias, 2),
        "hit_rate":   b["hit_rate"],
        "n":          b["n"],
        "direction":  direction,
        "found":      True,
    }


def stats() -> dict:
    """Inspector helper for /admin endpoints."""
    return {
        "loaded":  _cache["loaded"],
        "ts":      _cache["ts"],
        "buckets": len(_cache["buckets"]),
    }
