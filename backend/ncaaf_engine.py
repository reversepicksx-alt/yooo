"""
NCAAF Bayesian Projection Engine (College Football)
Mirrors NFL engine with college-calibrated hyper-priors.
"""
import math
from typing import Optional
from bayesian_engine import _monte_carlo_probability as _baye_mc

NCAAF_PROPS = {
    "passing_yards":   "passing_yards",
    "rushing_yards":   "rushing_yards",
    "receiving_yards": "receiving_yards",
    "pass_attempts":   "pass_attempts",
    "completions":     "completions",
    "receptions":      "receptions",
    "touchdowns":      "touchdowns",
    "interceptions":   "interceptions",
    "rushing_attempts": "rushing_attempts",
    "total_yards":     "total_yards",
    "fantasy_pts":     "fantasy_pts",
}

COUNT_PROPS = {
    "passing_yards", "rushing_yards", "receiving_yards", "pass_attempts",
    "completions", "receptions", "touchdowns", "interceptions",
    "rushing_attempts", "total_yards",
}

# College football hyper-priors (lower volume than NFL)
HYPER_PRIOR = {
    "passing_yards":   180.0,
    "rushing_yards":    60.0,
    "receiving_yards":  45.0,
    "pass_attempts":    28.0,
    "completions":      17.0,
    "receptions":        4.5,
    "touchdowns":        1.2,
    "interceptions":     0.8,
    "rushing_attempts":  9.0,
    "total_yards":     220.0,
    "fantasy_pts":      14.0,
}

VENUE_MULT = {"home": 1.04, "away": 0.96, "neutral": 1.0}
DECAY_WEIGHTS = [1.0, 0.92, 0.85, 0.78, 0.72, 0.66, 0.60, 0.55]

PROP_LABELS = {k: k.replace("_", " ").title() for k in NCAAF_PROPS}


def _weighted_mean(values: list, weights: list) -> float:
    total_w = sum(weights[:len(values)])
    if total_w == 0:
        return 0.0
    return sum(v * w for v, w in zip(values, weights[:len(values)])) / total_w


def compute_ncaaf_projection(
    game_logs: list,
    prop_type: str,
    line: float,
    venue: str = "home",
    rest_days: Optional[int] = None,
) -> dict:
    field = NCAAF_PROPS.get(prop_type)
    if not field:
        return {"error": f"Unknown NCAAF prop: {prop_type}"}

    values = []
    for g in game_logs:
        v = g.get(field)
        if v is not None:
            try:
                values.append(float(v))
            except Exception:
                pass

    if len(values) < 2:
        return {"error": f"Insufficient data for {prop_type} (n={len(values)})"}

    n = len(values)
    hyper = HYPER_PRIOR.get(prop_type, line)
    k0 = max(4, 10 - n)
    prior_mean = (sum(values) + k0 * hyper) / (n + k0)

    recent = values[:8]
    weights = DECAY_WEIGHTS[:len(recent)]
    momentum_mean = _weighted_mean(recent, weights)

    alpha = min(n / (n + k0), 0.80)
    posterior = alpha * momentum_mean + (1 - alpha) * prior_mean

    venue_m = VENUE_MULT.get(venue, 1.0)
    posterior *= venue_m

    rest_m = 1.0
    if rest_days is not None:
        if rest_days == 0:
            rest_m = 0.94
        elif rest_days >= 7:
            rest_m = 1.02
    posterior *= rest_m

    if prop_type in COUNT_PROPS:
        projection = round(posterior)
    else:
        projection = round(posterior, 2)

    _mc_vals = values[:12]
    _mc_std = (sum((x - posterior) ** 2 for x in _mc_vals) / len(_mc_vals)) ** 0.5 if len(_mc_vals) > 1 else max(posterior * 0.3, 1.0)
    _mc_std = max(_mc_std, 0.01)
    p_over, p_under, _, _ = _baye_mc(posterior, _mc_std, line, n_sims=5000, is_count_stat=prop_type in COUNT_PROPS)
    p_over  = round(p_over  * 100, 2)
    p_under = round(p_under * 100, 2)

    streak_flag = "NEUTRAL"
    if len(values) >= 4:
        if all(v > line for v in values[:4]):
            streak_flag = "OVER_STREAK"
        elif all(v < line for v in values[:4]):
            streak_flag = "UNDER_STREAK"

    _max_p = max(p_over, p_under)
    _conf  = min(round(_max_p), 54) if _max_p < 60.0 else round(_max_p)
    _level = "Low" if _max_p < 60.0 else ("High" if _max_p >= 70 else "Medium" if _max_p >= 60 else "Low")
    _low_conviction = _max_p < 60.0
    return {
        "projection":     projection,
        "priorMean":      round(prior_mean, 2),
        "momentum":       round(momentum_mean, 2),
        "pOver":          p_over,
        "pUnder":         p_under,
        "recommendation": "over" if p_over >= p_under else "under",
        "confidenceScore": _conf,
        "confidenceLevel": _level,
        "lowConviction":   _low_conviction,
        "sampleSize":     n,
        "streakFlag":     streak_flag,
        "recentValues":   values[:8],
    }
