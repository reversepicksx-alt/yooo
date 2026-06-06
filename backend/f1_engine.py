"""
Formula 1 Bayesian Projection Engine.
Finish position: lower = better (handle inverted Bayesian direction).
"""
from typing import Optional
from bayesian_engine import _monte_carlo_probability as _baye_mc

F1_PROPS = {
    "finish_position": "finish_pos",
    "grid_position":   "grid_pos",
    "points":          "points",
    "fastest_lap":     "fastest_lap",
    "laps_led":        "laps_led",
    "pit_stops":       "pit_stops",
}

# For finish_position & grid_position: lower is better, so p_over means worse finish
# We track this and invert the recommendation for position-based props.
INVERTED_PROPS = {"finish_position", "grid_position"}
COUNT_PROPS    = {"finish_position", "grid_position", "laps_led", "pit_stops"}

HYPER_PRIOR = {
    "finish_position": 8.0,   # avg expected finishing position
    "grid_position":   7.0,
    "points":          8.0,
    "fastest_lap":     0.15,  # ~15% chance per race
    "laps_led":        5.0,
    "pit_stops":       2.0,
}

DECAY_WEIGHTS = [1.0, 0.92, 0.85, 0.78, 0.72, 0.66, 0.60, 0.55]

PROP_LABELS = {
    "finish_position": "Finish Position",
    "grid_position":   "Grid/Qualifying Position",
    "points":          "Championship Points",
    "fastest_lap":     "Fastest Lap (0/1)",
    "laps_led":        "Laps Led",
    "pit_stops":       "Pit Stops",
}


def _weighted_mean(values: list, weights: list) -> float:
    total_w = sum(weights[:len(values)])
    if total_w == 0:
        return 0.0
    return sum(v * w for v, w in zip(values, weights[:len(values)])) / total_w


def compute_f1_projection(
    race_logs: list,
    prop_type: str,
    line: float,
) -> dict:
    field = F1_PROPS.get(prop_type)
    if not field:
        return {"error": f"Unknown F1 prop: {prop_type}"}

    values = []
    for g in race_logs:
        v = g.get(field)
        if v is not None:
            try:
                fv = float(v)
                # Exclude DNF (position=99) from finish_position averages
                if prop_type == "finish_position" and fv >= 50:
                    continue
                values.append(fv)
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

    if prop_type in COUNT_PROPS and prop_type not in INVERTED_PROPS:
        projection = round(posterior)
    elif prop_type in INVERTED_PROPS:
        projection = round(posterior)
    else:
        projection = round(posterior, 2)

    p_over, p_under = _baye_mc(values[:12], line, prop_type in COUNT_PROPS)

    # For position props: OVER means higher number (worse finish) = typically the "bad" outcome
    # Leave recommendation as-is since bettors think over/under on position number
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
