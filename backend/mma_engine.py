"""
MMA Bayesian Projection Engine.
Fight stats: significant strikes, takedowns, etc.
"""
from typing import Optional
from bayesian_engine import _monte_carlo_probability as _baye_mc

MMA_PROPS = {
    "significant_strikes": "sig_strikes_landed",
    "total_strikes":       "total_strikes_landed",
    "takedowns":           "takedowns_landed",
    "submission_attempts": "submission_attempts",
    "knockdowns":          "knockdowns",
    "fight_time_mins":     "fight_time_mins",
    "control_time_secs":   "control_time_secs",
}

COUNT_PROPS = {
    "significant_strikes", "total_strikes", "takedowns",
    "submission_attempts", "knockdowns",
}

# MMA hyper-priors based on typical UFC averages
HYPER_PRIOR = {
    "significant_strikes": 45.0,
    "total_strikes":       70.0,
    "takedowns":            1.5,
    "submission_attempts":  0.7,
    "knockdowns":           0.3,
    "fight_time_mins":      9.5,
    "control_time_secs":  120.0,
}

DECAY_WEIGHTS = [1.0, 0.90, 0.82, 0.74, 0.66, 0.58, 0.50, 0.44]

PROP_LABELS = {
    "significant_strikes": "Significant Strikes",
    "total_strikes":       "Total Strikes",
    "takedowns":           "Takedowns Landed",
    "submission_attempts": "Submission Attempts",
    "knockdowns":          "Knockdowns",
    "fight_time_mins":     "Fight Time (Minutes)",
    "control_time_secs":   "Control Time (Seconds)",
}


def _weighted_mean(values: list, weights: list) -> float:
    total_w = sum(weights[:len(values)])
    if total_w == 0:
        return 0.0
    return sum(v * w for v, w in zip(values, weights[:len(values)])) / total_w


def compute_mma_projection(
    fight_logs: list,
    prop_type: str,
    line: float,
) -> dict:
    field = MMA_PROPS.get(prop_type)
    if not field:
        return {"error": f"Unknown MMA prop: {prop_type}"}

    values = []
    for g in fight_logs:
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
    k0 = max(3, 8 - n)
    prior_mean = (sum(values) + k0 * hyper) / (n + k0)

    recent = values[:8]
    weights = DECAY_WEIGHTS[:len(recent)]
    momentum_mean = _weighted_mean(recent, weights)

    alpha = min(n / (n + k0), 0.75)
    posterior = alpha * momentum_mean + (1 - alpha) * prior_mean

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
    if len(values) >= 3:
        if all(v > line for v in values[:3]):
            streak_flag = "OVER_STREAK"
        elif all(v < line for v in values[:3]):
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
