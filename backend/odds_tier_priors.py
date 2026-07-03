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
    "buckets_fine": {},
    "loaded": False,
}


def _bucket_position(raw_pos):
    if not raw_pos:
        return None
    p = str(raw_pos).upper().strip()
    return POS_BUCKET.get(p, p)


def _bucket_key(odds_tier, pos_bucket, prop_type, recommendation, venue=None):
    """
    5-tuple key when venue is given (fine-grained), 4-tuple when omitted
    (coarse, venue-agnostic — used as the fallback bucket).
    """
    base = (
        (odds_tier or "").lower().strip(),
        (pos_bucket or "").upper().strip(),
        (prop_type or "").lower().strip(),
        (recommendation or "").lower().strip(),
    )
    if venue:
        return base + ((venue or "").lower().strip(),)
    return base


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

    IMPORTANT: when possession data itself is unavailable (e.g. an
    international friendly vs a minnow with no cached lineup/possession
    model output), we must NOT silently default to 50/50 "close" — that
    falsely tells the odds-tier-priors system "this is an even matchup"
    for what may actually be a massive mismatch, applying a wrong-direction
    nudge instead of no nudge at all. Return "unknown" so lookup_single()
    finds no bucket and correctly applies zero adjustment (see
    possession-fallback-unknown-tier.md).
    """
    if venue == "home":
        if proj_home is None:
            return "unknown"
        poss = float(proj_home)
    elif venue == "away":
        if proj_away is None:
            return "unknown"
        poss = float(proj_away)
    else:
        if proj_home is not None and proj_away is not None:
            gap = abs(float(proj_home) - float(proj_away))
            poss = 50.0 + gap / 2
        else:
            return "unknown"

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
    """
    Recompute two parallel bucket sets from settled picks:
      - fine:   oddsTier × pos × prop × side × venue  (more precise, sparser)
      - coarse: oddsTier × pos × prop × side           (current behavior, denser)
    lookup_single() tries fine first and falls back to coarse when the
    venue-split sample is too thin — strictly additive precision, never a
    loss of coverage vs the pre-venue system.
    """
    cursor = db.picks.find(
        {"result": {"$in": ["hit", "miss"]},
         "recommendation": {"$in": ["over", "under"]},
         "actualValue": {"$ne": None},
         "projectedValue": {"$ne": None},
         "oddsTier": {"$exists": True, "$ne": None}},
        {"_id": 0, "oddsTier": 1, "position": 1, "propType": 1,
         "recommendation": 1, "result": 1, "actualValue": 1,
         "projectedValue": 1, "venue": 1},
    )
    rows = await cursor.to_list(length=20000)

    agg_fine = defaultdict(lambda: {"n": 0, "hits": 0, "errors": []})
    agg_coarse = defaultdict(lambda: {"n": 0, "hits": 0, "errors": []})
    for p in rows:
        pos_b = _bucket_position(p.get("position"))
        if not pos_b:
            continue
        coarse_key = _bucket_key(p.get("oddsTier"), pos_b,
                                 p.get("propType"), p.get("recommendation"))
        if not all(coarse_key):
            continue
        try:
            err = float(p["actualValue"]) - float(p["projectedValue"])
        except (TypeError, ValueError):
            continue

        b = agg_coarse[coarse_key]
        b["n"] += 1
        if p.get("result") == "hit":
            b["hits"] += 1
        b["errors"].append(err)

        venue = p.get("venue")
        if venue in ("home", "away"):
            fine_key = _bucket_key(p.get("oddsTier"), pos_b,
                                   p.get("propType"), p.get("recommendation"),
                                   venue=venue)
            fb = agg_fine[fine_key]
            fb["n"] += 1
            if p.get("result") == "hit":
                fb["hits"] += 1
            fb["errors"].append(err)

    def _finalize(agg):
        out = {}
        for key, b in agg.items():
            n = b["n"]
            if n < _MIN_SAMPLE:
                continue
            mean_err = sum(b["errors"]) / n
            hit_rate = b["hits"] / n
            shrink = n / (n + _SHRINK_K)
            out[key] = {
                "n":         n,
                "hit_rate":  round(hit_rate, 3),
                "mean_err":  round(mean_err, 2),
                "shrink":    round(shrink, 3),
            }
        return out

    coarse_buckets = _finalize(agg_coarse)
    fine_buckets = _finalize(agg_fine)

    _cache["buckets"] = coarse_buckets
    _cache["buckets_fine"] = fine_buckets
    _cache["ts"]      = time.time()
    _cache["loaded"]  = True
    print(f"[ODDS-TIER PRIORS] refreshed: {len(coarse_buckets)} coarse + "
          f"{len(fine_buckets)} venue-split buckets from {len(rows)} settled picks")


async def ensure_loaded(db) -> None:
    if not _cache["loaded"] or (time.time() - _cache["ts"]) > _REFRESH_SECS:
        try:
            await _refresh(db)
        except Exception as e:
            print(f"[ODDS-TIER PRIORS] refresh failed: {e}")


def lookup_single(odds_tier, position, prop_type, recommendation,
                  posterior_mean: float, venue=None) -> dict:
    """
    Single odds-tier lookup. Same return shape as league_priors/scenario_priors.

    Tries the fine-grained (odds tier × position × prop × side × venue) bucket
    first when `venue` is given — this is strictly more precise since home/away
    can carry a systematic bias on top of favorite/underdog status (e.g. a home
    heavy-favorite's CDM recycles possession differently than an away one).
    Falls back to the coarse venue-agnostic bucket when the fine bucket doesn't
    exist or hasn't reached the minimum sample size, so venue-splitting never
    reduces coverage vs the original venue-agnostic system.
    """
    inert = {"multiplier": 1.0, "bias": 0.0, "hit_rate": None,
             "n": 0, "direction": "neutral", "found": False,
             "oddsTier": odds_tier, "venueSplit": False}
    if not _cache["loaded"] or posterior_mean is None or posterior_mean == 0:
        return inert
    pos_b = _bucket_position(position)
    if not pos_b:
        return inert

    b = None
    venue_split = False
    if venue in ("home", "away"):
        fine_key = _bucket_key(odds_tier, pos_b, prop_type, recommendation, venue=venue)
        b = _cache["buckets_fine"].get(fine_key)
        if b:
            venue_split = True

    if not b:
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
        "venueSplit": venue_split,
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
            "venue": None,
            "n": v["n"], "hit_rate": v["hit_rate"], "mean_err": v["mean_err"],
        })

    populated_fine = []
    by_venue = defaultdict(int)
    for key, v in _cache["buckets_fine"].items():
        tier, pos, prop, side, venue = key
        by_venue[venue] += 1
        populated_fine.append({
            "oddsTier": tier, "position": pos, "prop": prop, "side": side,
            "venue": venue,
            "n": v["n"], "hit_rate": v["hit_rate"], "mean_err": v["mean_err"],
        })

    populated.sort(key=lambda r: (-r["n"], -(r["hit_rate"] or 0)))
    populated_fine.sort(key=lambda r: (-r["n"], -(r["hit_rate"] or 0)))
    return {
        "loaded":        _cache["loaded"],
        "ts":            _cache["ts"],
        "buckets":       len(_cache["buckets"]),
        "bucketsFine":   len(_cache["buckets_fine"]),
        "by_tier":       dict(by_tier),
        "by_prop":       dict(by_prop),
        "by_venue":      dict(by_venue),
        "populated":     populated,
        "populatedFine": populated_fine,
    }
