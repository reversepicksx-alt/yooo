"""
PGA Tour Bayesian Projection Engine.
Golf stats: birdies, bogeys, putts, greens in regulation, etc.
Strokes (score) is per-round; lower is better.
"""
from typing import Optional
from bayesian_engine import _monte_carlo_probability as _baye_mc

PGA_PROPS = {
    "birdies":      "birdies",
    "bogeys":       "bogeys",
    "putts":        "putts",
    "fairways_hit": "fairways_hit",
    "gir":          "gir",
    "round_score":  "round_score",
    "made_cut":     "made_cut",
}

COUNT_PROPS = {"birdies", "bogeys", "putts", "round_score"}

# PGA Tour averages across field
HYPER_PRIOR = {
    "birdies":      3.5,
    "bogeys":       2.2,
    "putts":       28.0,
    "fairways_hit": 9.0,   # out of ~14 fairways
    "gir":         11.0,   # out of 18 greens
    "round_score": 70.5,   # avg round score on tour
    "made_cut":     0.55,  # ~55% make cut rate
}

DECAY_WEIGHTS = [1.0, 0.93, 0.86, 0.79, 0.72, 0.65, 0.58, 0.52]

PROP_LABELS = {
    "birdies":      "Birdies",
    "bogeys":       "Bogeys",
    "putts":        "Putts",
    "fairways_hit": "Fairways Hit",
    "gir":          "Greens in Regulation",
    "round_score":  "Round Score (Strokes)",
    "made_cut":     "Made Cut (0/1)",
}


def _weighted_mean(values: list, weights: list) -> float:
    total_w = sum(weights[:len(values)])
    if total_w == 0:
        return 0.0
    return sum(v * w for v, w in zip(values, weights[:len(values)])) / total_w


def compute_pga_projection(
    round_logs: list,
    prop_type: str,
    line: float,
) -> dict:
    field = PGA_PROPS.get(prop_type)
    if not field:
        return {"error": f"Unknown PGA prop: {prop_type}"}

    values = []
    for g in round_logs:
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

    if prop_type in COUNT_PROPS:
        projection = round(posterior)
    else:
        projection = round(posterior, 2)

    _mc_vals = values[:12]
    _mc_std = (sum((x - posterior) ** 2 for x in _mc_vals) / len(_mc_vals)) ** 0.5 if len(_mc_vals) > 1 else max(posterior * 0.3, 1.0)
    _mc_std = max(_mc_std, 0.01)
    p_over, p_under, _, _ = _baye_mc(posterior, _mc_std, line, n_sims=5000, is_count_stat=prop_type in COUNT_PROPS)

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
