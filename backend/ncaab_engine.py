"""
NCAAB Bayesian Projection Engine (College Basketball Men)
Mirrors NBA engine with college-calibrated hyper-priors.
"""
import math
import random
import statistics as stats_mod
from typing import Optional
from bayesian_engine import _monte_carlo_probability as _baye_mc

def _mc(mean, var, line, is_count):
    std = math.sqrt(max(var, 0.01))
    return _baye_mc(mean, std, line, n_sims=5000, is_count_stat=is_count, variance=var)

NCAAB_PROPS = {
    "points":        "pts",
    "rebounds":      "reb",
    "assists":       "ast",
    "steals":        "stl",
    "blocks":        "blk",
    "turnovers":     "tov",
    "three_pointers": "fg3m",
    "fantasy_points": "fantasy_pts",
    "pts_reb_ast":   "pts_reb_ast",
    "pts_reb":       "pts_reb",
    "pts_ast":       "pts_ast",
    "reb_ast":       "reb_ast",
    "stl_blk":       "stl_blk",
    "free_throws":   "ftm",
    "field_goals":   "fgm",
}

COUNT_PROPS = {
    "points", "rebounds", "assists", "steals", "blocks", "turnovers",
    "three_pointers", "pts_reb_ast", "pts_reb", "pts_ast", "reb_ast",
    "stl_blk", "free_throws", "field_goals",
}

# College hyper-priors (lower scoring than NBA)
HYPER_PRIOR = {
    "points":        10.5,
    "rebounds":       4.5,
    "assists":        2.5,
    "steals":         0.7,
    "blocks":         0.5,
    "turnovers":      1.8,
    "three_pointers": 1.1,
    "fantasy_points": 20.0,
    "pts_reb_ast":   17.5,
    "pts_reb":       15.0,
    "pts_ast":       13.0,
    "reb_ast":        7.0,
    "stl_blk":        1.2,
    "free_throws":    2.2,
    "field_goals":    3.8,
}

VENUE_MULT = {"home": 1.04, "away": 0.96, "neutral": 1.0}
DECAY_WEIGHTS = [1.0, 0.92, 0.85, 0.78, 0.72, 0.66, 0.60, 0.55]


def _weighted_mean(values: list, weights: list) -> float:
    total_w = sum(weights[:len(values)])
    if total_w == 0:
        return 0.0
    return sum(v * w for v, w in zip(values, weights[:len(values)])) / total_w


def compute_ncaab_projection(
    game_logs: list,
    prop_type: str,
    line: float,
    venue: str = "home",
    rest_days: Optional[int] = None,
    season_avg: Optional[dict] = None,
) -> dict:
    field = NCAAB_PROPS.get(prop_type)
    if not field:
        return {"error": f"Unknown NCAAB prop: {prop_type}"}

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
    k0 = max(4, 12 - n)
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
        elif rest_days >= 4:
            rest_m = 1.02
    posterior *= rest_m

    # Round discrete counts
    if prop_type in COUNT_PROPS:
        projection = round(posterior)
    else:
        projection = round(posterior, 2)

    _variance = stats_mod.variance(values) if len(values) > 1 else max(projection * 0.30, 0.5)
    _po, _pu, *_ = _mc(projection, _variance, line, prop_type in COUNT_PROPS)
    p_over  = round(_po * 100, 2)
    p_under = round(_pu * 100, 2)

    streak_flag = "NEUTRAL"
    if len(values) >= 4:
        if all(v > line for v in values[:4]):
            streak_flag = "OVER_STREAK"
        elif all(v < line for v in values[:4]):
            streak_flag = "UNDER_STREAK"

    return {
        "projection":    projection,
        "priorMean":     round(prior_mean, 2),
        "momentum":      round(momentum_mean, 2),
        "pOver":         p_over,
        "pUnder":        p_under,
        "recommendation": "over" if p_over >= p_under else "under",
        "confidenceScore": round(max(p_over, p_under)),
        "confidenceLevel": "High" if max(p_over, p_under) >= 70 else "Medium" if max(p_over, p_under) >= 60 else "Low",
        "sampleSize":    n,
        "streakFlag":    streak_flag,
        "recentValues":  values[:8],
    }
