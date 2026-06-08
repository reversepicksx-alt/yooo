"""
NCAAW Bayesian Projection Engine (College Basketball Women)
Mirrors WNBA engine with college women's calibrated hyper-priors.
"""
import math
import statistics as _stats_mod
from typing import Optional
from bayesian_engine import _monte_carlo_probability as _baye_mc

def _mc(projection, values, line, is_count):
    var = _stats_mod.variance(values) if len(values) > 1 else max(projection * 0.30, 0.5)
    var = max(var, 0.1)
    std = math.sqrt(var)
    return _baye_mc(projection, std, line, n_sims=5000, is_count_stat=is_count, variance=var)

NCAAW_PROPS = {
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

HYPER_PRIOR = {
    "points":        9.5,
    "rebounds":      5.0,
    "assists":       2.3,
    "steals":        1.0,
    "blocks":        0.7,
    "turnovers":     2.0,
    "three_pointers": 0.8,
    "fantasy_points": 18.0,
    "pts_reb_ast":   16.8,
    "pts_reb":       14.5,
    "pts_ast":       11.8,
    "reb_ast":        7.3,
    "stl_blk":        1.7,
    "free_throws":    2.5,
    "field_goals":    3.5,
}

VENUE_MULT = {"home": 1.04, "away": 0.95, "neutral": 1.0}
DECAY_WEIGHTS = [1.0, 0.92, 0.85, 0.78, 0.72, 0.66, 0.60, 0.55]


def _weighted_mean(values: list, weights: list) -> float:
    total_w = sum(weights[:len(values)])
    if total_w == 0:
        return 0.0
    return sum(v * w for v, w in zip(values, weights[:len(values)])) / total_w


def compute_ncaaw_projection(
    game_logs: list,
    prop_type: str,
    line: float,
    venue: str = "home",
    rest_days: Optional[int] = None,
    season_avg: Optional[dict] = None,
) -> dict:
    field = NCAAW_PROPS.get(prop_type)
    if not field:
        return {"error": f"Unknown NCAAW prop: {prop_type}"}

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

    if prop_type in COUNT_PROPS:
        projection = round(posterior)
    else:
        projection = round(posterior, 2)

    _po, _pu, *_ = _mc(projection, values, line, prop_type in COUNT_PROPS)
    p_over  = round(_po * 100, 2)
    p_under = round(_pu * 100, 2)

    streak_flag = "NEUTRAL"
    if len(values) >= 4:
        if all(v > line for v in values[:4]):
            streak_flag = "OVER_STREAK"
        elif all(v < line for v in values[:4]):
            streak_flag = "UNDER_STREAK"

    return {
        "projection":     projection,
        "priorMean":      round(prior_mean, 2),
        "momentum":       round(momentum_mean, 2),
        "pOver":          p_over,
        "pUnder":         p_under,
        "recommendation": "over" if p_over >= p_under else "under",
        "confidenceScore": round(max(p_over, p_under)),
        "confidenceLevel": "High" if max(p_over, p_under) >= 70 else "Medium" if max(p_over, p_under) >= 60 else "Low",
        "sampleSize":     n,
        "streakFlag":     streak_flag,
        "recentValues":   values[:8],
    }
