"""
ODDS-TIER EMPIRICAL PRIORS — Self-Learning "Alive" Layer

Mirrors scenario_priors.py but buckets by (oddsTier, position, propType, recommendation).

For each (odds tier × position bucket × prop type × recommendation direction),
mines settled picks to compute:
  - mean_err     = average projection error (actual - projected)
  - hit_rate     = % of picks in this bucket that hit
  - shrink       = n/(n+k) where k=30 (James-Stein shrinkage)

The Bayesian engine calls lookup_single() with the pre-match odds tier,
which returns a multiplicative nudge (1.0 ± capped correction).

Key design decisions:
  * oddsTier is derived from match_odds OR projected_possession at prediction time.
  * Single lookup (not weighted like scenario_priors) because the odds tier
    is known deterministically before the match.
  * ±6% cap per bucket, same as league_priors and scenario_priors.
  * Min sample = 8 (stricter than league_priors = 4).
  * Refreshed every 6h from settled picks so it "learns" as new data arrives.
"""
from __future__ import annotations
import time
from collections import defaultdict

_REFRESH_SECS = 6 * 3600
_MIN_SAMPLE   = 8
_SHRINK_K     = 30
_MAX_NUDGE    = 0.06

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
    "ts": 0.0,
    "buckets": {},
    "loaded": False,
}


def _bucket_position(raw_pos):
    if not raw_pos:
        return None
    p = str(raw_pos).upper().strip()
    return POS_BUCKET.get(p, p)


def _bucket_key(odds_tier, pos_bucket, prop_type, recommendation):
    return (
        (odds_tier or "").lower().strip(),
        (pos_bucket or "").upper().strip(),
        (prop_type or "").lower().strip(),
        (recommendation or "").lower().strip(),
    )


def odds_tier_from_moneyline(ml_dict, venue):
    """
    Same logic as backfill script — convert moneyline dict to odds tier.
    Returns "unknown" if moneyline is missing or malformed.
    """
    if not ml_dict or not isinstance(ml_dict, dict):
        return "unknown"
    home_str = str(ml_dict.get("home", "")).strip()
    away_str = str(ml_dict.get("away", "")).strip()
    try:
        home_odds = int(home_str) if home_str else None
        away_odds = int(away_str) if away_str else None
    except ValueError:
        return "unknown"

    if venue == "home" and home_odds is not None:
        ml = home_odds
    elif venue == "away" and away_odds is not None:
        ml = away_odds
    else:
        if home_odds is not None and away_odds is not None:
            ml = home_odds if abs(home_odds) > abs(away_odds) else away_odds
        elif home_odds is not None:
            ml = home_odds
        elif away_odds is not None:
            ml = away_odds
        else:
            return "unknown"

    if ml < 0:
        prob = -ml / (-ml + 100)
    else:
        prob = 100 / (ml + 100)

    if prob >= 0.75:
        return "heavy_favorite"
    elif prob >= 0.667:
        return "strong_favorite"
    elif prob >= 0.565:
        return "moderate_favorite"
    elif prob >= 0.524:
        return "slight_favorite"
    elif prob >= 0.476:
        return "close"
    elif prob >= 0.4:
        return "slight_underdog"
    elif prob >= 0.286:
        return "moderate_underdog"
    else:
        return "heavy_underdog"


def odds_tier_from_possession(proj_home, proj_away, venue):
    """
    Fallback odds-tier classifier using projected possession %.
    Used when moneyline is unavailable.
    """
    if venue == "home":
        poss = float(proj_home) if proj_home is not None else 50.0
    elif venue == "away":
        poss = float(proj_away) if proj_away is not None else 50.0
    else:
        if proj_home is not None and proj_away is not None:
            gap = abs(float(proj_home) - float(proj_away))
            poss = 50.0 + gap / 2
        else:
            poss = 50.0

    if poss >= 72:
        return "heavy_favorite"
    elif poss >= 65:
        return "strong_favorite"
    elif poss >= 60:
        return "moderate_favorite"
    elif poss >= 52:
        return "slight_favorite"
    elif poss >= 48:
        return "close"
    elif poss >= 40:
        return "slight_underdog"
    elif poss >= 33:
        return "moderate_underdog"
    else:
        return "heavy_underdog"


async def _refresh(db) -> None:
    """Recompute every oddsTier × pos × prop × side bucket from settled picks."""
    cursor = db.picks.find(
        {"result": {"$in": ["hit", "miss"]},
         "recommendation": {"$in": ["over", "under"]},
         "actualValue": {"$ne": None},
         "projectedValue": {"$ne": None},
         "oddsTier": {"$exists": True, "$ne": None}},
        {"_id": 0, "oddsTier": 1, "position": 1, "propType": 1,
         "recommendation": 1, "result": 1, "actualValue": 1,
         "projectedValue": 1},
    )
    rows = await cursor.to_list(length=20000)

    agg = defaultdict(lambda: {"n": 0, "hits": 0, "errors": []})
    for p in rows:
        pos_b = _bucket_position(p.get("position"))
        if not pos_b:
            continue
        key = _bucket_key(p.get("oddsTier"), pos_b,
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
    print(f"[ODDS-TIER PRIORS] refreshed: {len(buckets)} buckets from {len(rows)} settled picks")


async def ensure_loaded(db) -> None:
    if not _cache["loaded"] or (time.time() - _cache["ts"]) > _REFRESH_SECS:
        try:
            await _refresh(db)
        except Exception as e:
            print(f"[ODDS-TIER PRIORS] refresh failed: {e}")


def lookup_single(odds_tier, position, prop_type, recommendation,
                  posterior_mean: float) -> dict:
    """Single odds-tier lookup. Same return shape as league_priors/scenario_priors."""
    inert = {"multiplier": 1.0, "bias": 0.0, "hit_rate": None,
             "n": 0, "direction": "neutral", "found": False,
             "oddsTier": odds_tier}
    if not _cache["loaded"] or posterior_mean is None or posterior_mean == 0:
        return inert
    pos_b = _bucket_position(position)
    if not pos_b:
        return inert
    key = _bucket_key(odds_tier, pos_b, prop_type, recommendation)
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
        "oddsTier":   odds_tier,
    }


def stats() -> dict:
    by_tier = defaultdict(int)
    by_prop = defaultdict(int)
    populated = []
    for key, v in _cache["buckets"].items():
        tier, pos, prop, side = key
        by_tier[tier] += 1
        by_prop[prop] += 1
        populated.append({
            "oddsTier": tier, "position": pos, "prop": prop, "side": side,
            "n": v["n"], "hit_rate": v["hit_rate"], "mean_err": v["mean_err"],
        })
    populated.sort(key=lambda r: (-r["n"], -(r["hit_rate"] or 0)))
    return {
        "loaded":   _cache["loaded"],
        "ts":       _cache["ts"],
        "buckets":  len(_cache["buckets"]),
        "by_tier":  dict(by_tier),
        "by_prop":  dict(by_prop),
        "populated": populated,
    }
