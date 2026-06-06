"""
League of Legends Bayesian Projection Engine.
Match stats: kills, deaths, assists, CS, vision, damage, gold, etc.
"""
from typing import Optional
from bayesian_engine import _monte_carlo_probability as _baye_mc

LOL_PROPS = {
    "kills":        "kills",
    "deaths":       "deaths",
    "assists":      "assists",
    "kda":          "kda",
    "cs":           "cs",
    "vision_score": "vision_score",
    "damage":       "damage",
    "gold":         "gold",
    "fantasy_pts":  "fantasy_pts",
}

COUNT_PROPS = {"kills", "deaths", "assists", "cs", "vision_score"}

# LoL pro-level hyper-priors (LCS/LCK/LPL level)
HYPER_PRIOR = {
    "kills":        4.0,
    "deaths":       2.8,
    "assists":      7.5,
    "kda":          4.0,
    "cs":          220.0,
    "vision_score": 35.0,
    "damage":    22000.0,
    "gold":       12000.0,
    "fantasy_pts":  30.0,
}

DECAY_WEIGHTS = [1.0, 0.92, 0.84, 0.76, 0.68, 0.60, 0.53, 0.46]

PROP_LABELS = {
    "kills":        "Kills",
    "deaths":       "Deaths",
    "assists":      "Assists",
    "kda":          "KDA Ratio",
    "cs":           "Creep Score (CS)",
    "vision_score": "Vision Score",
    "damage":       "Damage to Champions",
    "gold":         "Gold Earned",
    "fantasy_pts":  "Fantasy Points",
}


def _weighted_mean(values: list, weights: list) -> float:
    total_w = sum(weights[:len(values)])
    if total_w == 0:
        return 0.0
    return sum(v * w for v, w in zip(values, weights[:len(values)])) / total_w


def compute_lol_projection(
    match_logs: list,
    prop_type: str,
    line: float,
) -> dict:
    field = LOL_PROPS.get(prop_type)
    if not field:
        return {"error": f"Unknown LoL prop: {prop_type}"}

    values = []
    for g in match_logs:
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

    alpha = min(n / (n + k0), 0.80)
    posterior = alpha * momentum_mean + (1 - alpha) * prior_mean

    if prop_type in COUNT_PROPS:
        projection = round(posterior)
    else:
        projection = round(posterior, 2)

    p_over, p_under = _baye_mc(values[:12], line, prop_type in COUNT_PROPS)

    streak_flag = "NEUTRAL"
    if len(values) >= 3:
        if all(v > line for v in values[:3]):
            streak_flag = "OVER_STREAK"
        elif all(v < line for v in values[:3]):
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
