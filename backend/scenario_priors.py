"""
SCENARIO-AWARE EMPIRICAL PRIORS

Mines settled picks to compute the systematic projection bias for each
(scenarioBucket, position, propType, recommendation) bucket — i.e. the
cheat-sheet's contextual findings, expressed as a queryable shrinkage
table that feeds the Bayesian engine alongside league_priors.

Key difference vs league_priors:
  * league_priors slices by (league × pos × prop × side)
  * scenario_priors slices by (scenarioBucket × pos × prop × side)
  * Stricter min-sample (8 vs 4) because scenario buckets are smaller
  * Same shrinkage shape, same ±6% nudge cap, same lookup() return shape
    so the engine can apply both layers compositionally.

Scenario buckets are computed by game_script_engine.bucket_from_final_score
and stored on each pick at settle time (field: scenarioBucket).

Position bucketing matches the cheat sheet:
  GK            → GK
  CB/LB/RB/...  → CB
  CDM/CM        → CDM
  CAM/LM/RM     → AM
  LW/RW/SS      → WING
  CF/ST         → ST
"""
from __future__ import annotations
import time
from collections import defaultdict


_REFRESH_SECS = 6 * 3600        # 6 hours
_MIN_SAMPLE   = 8               # stricter than league priors (4)
_SHRINK_K     = 30              # half-strength at n=30
_MAX_NUDGE    = 0.06            # cap correction at ±6%

POS_BUCKET = {
    "GK": "GK",
    "CB": "CB", "LB": "CB", "RB": "CB", "LWB": "CB", "RWB": "CB",
    "LCB": "CB", "RCB": "CB",
    "CDM": "CDM", "DM": "CDM", "DMF": "CDM", "CM": "CDM", "MC": "CDM",
    "CAM": "AM", "AM": "AM", "LM": "AM", "RM": "AM",
    "LW": "WING", "RW": "WING", "SS": "WING", "WF": "WING",
    "CF": "ST", "ST": "ST", "FW": "ST",
}

_cache = {
    "ts":      0.0,
    "buckets": {},   # key: (scenario, pos_bucket, prop, side) → stats
    "loaded":  False,
}


def _bucket_position(raw_pos):
    if not raw_pos:
        return None
    p = str(raw_pos).upper().strip()
    return POS_BUCKET.get(p, p)  # falls through with raw if unknown


def _bucket_key(scenario, pos_bucket, prop_type, recommendation):
    return (
        (scenario or "").lower().strip(),
        (pos_bucket or "").upper().strip(),
        (prop_type or "").lower().strip(),
        (recommendation or "").lower().strip(),
    )


async def _refresh(db) -> None:
    """Recompute every scenario × pos × prop × side bucket from settled picks."""
    cursor = db.picks.find(
        {"result": {"$in": ["hit", "miss"]},
         "recommendation": {"$in": ["over", "under"]},
         "actualValue": {"$ne": None},
         "projectedValue": {"$ne": None},
         "scenarioBucket": {"$exists": True, "$ne": None}},
        {"_id": 0, "scenarioBucket": 1, "position": 1, "propType": 1,
         "recommendation": 1, "result": 1, "actualValue": 1,
         "projectedValue": 1},
    )
    rows = await cursor.to_list(length=20000)

    agg = defaultdict(lambda: {"n": 0, "hits": 0, "errors": []})
    for p in rows:
        pos_b = _bucket_position(p.get("position"))
        if not pos_b:
            continue
        key = _bucket_key(p.get("scenarioBucket"), pos_b,
                          p.get("propType"), p.get("recommendation"))
        if not all(key):
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
    print(f"[SCENARIO PRIORS] refreshed: {len(buckets)} buckets from {len(rows)} settled picks")


async def ensure_loaded(db) -> None:
    if not _cache["loaded"] or (time.time() - _cache["ts"]) > _REFRESH_SECS:
        try:
            await _refresh(db)
        except Exception as e:
            print(f"[SCENARIO PRIORS] refresh failed: {e}")


def lookup_single(scenario, position, prop_type, recommendation,
                  posterior_mean: float) -> dict:
    """Single-scenario lookup. Same shape as league_priors.lookup()."""
    inert = {"multiplier": 1.0, "bias": 0.0, "hit_rate": None,
             "n": 0, "direction": "neutral", "found": False,
             "scenario": scenario}
    if not _cache["loaded"] or posterior_mean is None or posterior_mean == 0:
        return inert
    pos_b = _bucket_position(position)
    if not pos_b:
        return inert
    key = _bucket_key(scenario, pos_b, prop_type, recommendation)
    b = _cache["buckets"].get(key)
    if not b:
        return inert
    bias = b["mean_err"]
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
        "scenario":   scenario,
    }


def lookup_weighted(scenario_probs: dict, position, prop_type, recommendation,
                    posterior_mean: float) -> dict:
    """
    Probability-weighted lookup over all scenarios.

    Composes Σ P(scenario_i) × nudge_i where each nudge is the multiplier
    delta from 1.0. Returns a single multiplier in the same shape as
    league_priors.lookup() so the bayesian engine can apply it identically.

    Inert when no scenario buckets have data for this (pos × prop × side).
    """
    inert = {"multiplier": 1.0, "bias": 0.0, "hit_rate": None,
             "n": 0, "direction": "neutral", "found": False,
             "components": [], "shadow": False}
    if not _cache["loaded"] or posterior_mean is None or posterior_mean == 0:
        return inert
    if not isinstance(scenario_probs, dict):
        return inert

    weighted_nudge = 0.0
    total_weight = 0.0
    total_n = 0
    components = []
    for k, p in scenario_probs.items():
        if not k.startswith("P_"):
            continue
        try:
            prob = float(p)
        except (TypeError, ValueError):
            continue
        if prob <= 0:
            continue
        scen = k[2:]  # strip "P_"
        single = lookup_single(scen, position, prop_type, recommendation,
                               posterior_mean)
        if not single["found"]:
            continue
        nudge = single["multiplier"] - 1.0
        weighted_nudge += prob * nudge
        total_weight += prob
        total_n += single["n"]
        components.append({
            "scenario": scen, "prob": round(prob, 3),
            "n": single["n"], "hit_rate": single["hit_rate"],
            "mult": single["multiplier"], "bias": single["bias"],
        })

    if not components:
        return inert

    # Cap composed nudge at ±_MAX_NUDGE so it can't exceed single-bucket cap
    final_nudge = max(-_MAX_NUDGE, min(_MAX_NUDGE, weighted_nudge))
    direction = "boost" if final_nudge > 0.005 else ("cut" if final_nudge < -0.005 else "neutral")
    return {
        "multiplier":     round(1.0 + final_nudge, 4),
        "bias":           0.0,                # composed, raw bias not meaningful
        "hit_rate":       None,
        "n":              total_n,
        "direction":      direction,
        "found":          True,
        "components":     components,
        "coverage":       round(total_weight, 3),  # Σ probs over scenarios that hit
        "shadow":         False,                    # caller flips this in shadow mode
    }


def stats() -> dict:
    """Inspector helper for /admin endpoints."""
    by_scenario = defaultdict(int)
    by_prop = defaultdict(int)
    populated = []
    for key, v in _cache["buckets"].items():
        scen, pos, prop, side = key
        by_scenario[scen] += 1
        by_prop[prop] += 1
        populated.append({
            "scenario": scen, "position": pos, "prop": prop, "side": side,
            "n": v["n"], "hit_rate": v["hit_rate"], "mean_err": v["mean_err"],
        })
    populated.sort(key=lambda r: (-r["n"], -(r["hit_rate"] or 0)))
    return {
        "loaded":   _cache["loaded"],
        "ts":       _cache["ts"],
        "buckets":  len(_cache["buckets"]),
        "by_scenario": dict(by_scenario),
        "by_prop":     dict(by_prop),
        "populated":   populated,
    }
