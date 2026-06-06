"""
College Baseball Bayesian Projection Engine.
Mirrors MLB engine with college-calibrated hyper-priors.
"""
from typing import Optional
from bayesian_engine import _monte_carlo_probability as _baye_mc

CBASE_PROPS = {
    "hits":                  "hits",
    "at_bats":               "at_bats",
    "runs":                  "runs",
    "rbi":                   "rbi",
    "home_runs":             "home_runs",
    "walks":                 "walks",
    "strikeouts":            "strikeouts",
    "stolen_bases":          "stolen_bases",
    "total_bases":           "total_bases",
    "strikeouts_pitching":   "strikeouts_pitching",
    "earned_runs":           "earned_runs",
    "innings_pitched":       "innings_pitched",
}

COUNT_PROPS = {
    "hits", "at_bats", "runs", "rbi", "home_runs", "walks",
    "strikeouts", "stolen_bases", "total_bases",
    "strikeouts_pitching", "earned_runs",
}

# College baseball hyper-priors (lower power numbers than MLB)
HYPER_PRIOR = {
    "hits":                  1.2,
    "at_bats":               3.8,
    "runs":                  0.7,
    "rbi":                   0.8,
    "home_runs":             0.2,
    "walks":                 0.6,
    "strikeouts":            1.0,
    "stolen_bases":          0.3,
    "total_bases":           1.6,
    "strikeouts_pitching":   5.5,
    "earned_runs":           2.5,
    "innings_pitched":       5.0,
}

VENUE_MULT = {"home": 1.04, "away": 0.96, "neutral": 1.0}
DECAY_WEIGHTS = [1.0, 0.93, 0.86, 0.79, 0.72, 0.65, 0.58, 0.51]

PROP_LABELS = {
    "hits":                  "Hits",
    "at_bats":               "At Bats",
    "runs":                  "Runs",
    "rbi":                   "RBI",
    "home_runs":             "Home Runs",
    "walks":                 "Walks",
    "strikeouts":            "Strikeouts (Batter)",
    "stolen_bases":          "Stolen Bases",
    "total_bases":           "Total Bases",
    "strikeouts_pitching":   "Strikeouts (Pitcher)",
    "earned_runs":           "Earned Runs Allowed",
    "innings_pitched":       "Innings Pitched",
}


def _weighted_mean(values: list, weights: list) -> float:
    total_w = sum(weights[:len(values)])
    if total_w == 0:
        return 0.0
    return sum(v * w for v, w in zip(values, weights[:len(values)])) / total_w


def compute_cbase_projection(
    game_logs: list,
    prop_type: str,
    line: float,
    venue: str = "home",
    rest_days: Optional[int] = None,
) -> dict:
    field = CBASE_PROPS.get(prop_type)
    if not field:
        return {"error": f"Unknown college baseball prop: {prop_type}"}

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
            rest_m = 0.95
        elif rest_days >= 4:
            rest_m = 1.02
    posterior *= rest_m

    if prop_type in COUNT_PROPS:
        projection = round(posterior)
    else:
        projection = round(posterior, 3)

    p_over, p_under = _baye_mc(values[:12], line, prop_type in COUNT_PROPS)

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
