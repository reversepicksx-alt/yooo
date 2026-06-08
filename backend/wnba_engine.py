"""
WNBA Bayesian Projection Engine

Multi-layer model for women's basketball player props:
  Layer 1: PRIOR          — Season average + hyper-prior shrinkage
  Layer 2: MOMENTUM       — Exponential decay over last 8 games (newest-first)
  Layer 3: VENUE          — Home/away multiplier
  Layer 4: OPPONENT DEF   — Opponent defensive rating adjustment
  Layer 5: REST DAYS      — Back-to-back penalty / extra-rest boost
  Monte Carlo: Negative-Binomial for discrete counts
"""
import math
import statistics as stats_mod
from typing import Optional
from bayesian_engine import _monte_carlo_probability as _baye_mc

def _mc(mean, var, line, is_count):
    std = math.sqrt(max(var, 0.01))
    return _baye_mc(mean, std, line, n_sims=5000, is_count_stat=is_count, variance=var)

# ── Prop definitions ─────────────────────────────────────────────────────────
WNBA_PROPS = {
    "points":         "pts",
    "rebounds":       "reb",
    "assists":        "ast",
    "steals":         "stl",
    "blocks":         "blk",
    "turnovers":      "tov",
    "three_pointers": "fg3m",
    "fantasy_points": "fantasy_pts",
    "pts_reb_ast":    "pts_reb_ast",
    "pts_reb":        "pts_reb",
    "pts_ast":        "pts_ast",
    "reb_ast":        "reb_ast",
    "free_throws":    "ftm",
    "field_goals":    "fgm",
}

COUNT_PROPS = {
    "points", "rebounds", "assists", "steals", "blocks", "turnovers",
    "three_pointers", "pts_reb_ast", "pts_reb", "pts_ast", "reb_ast",
    "free_throws", "field_goals",
}

HYPER_PRIOR = {
    "points":          8.5,
    "rebounds":        4.2,
    "assists":         2.2,
    "steals":          1.0,
    "blocks":          0.5,
    "turnovers":       1.5,
    "three_pointers":  0.9,
    "fantasy_points": 18.0,
    "pts_reb_ast":    14.9,
    "pts_reb":        12.7,
    "pts_ast":        10.7,
    "reb_ast":         6.4,
    "free_throws":     1.8,
    "field_goals":     3.2,
}

DECAY = [1.0, 0.82, 0.67, 0.55, 0.45, 0.37, 0.30, 0.24]

MIN_SAMPLE    = 8
MIN_VALID_LOG = 3


def _extract_series(logs: list, field: str) -> list:
    vals = []
    for g in logs:
        v = g.get(field)
        if v is not None:
            try:
                vals.append(float(v))
            except Exception:
                pass
    return vals


def _momentum(vals: list) -> float:
    if not vals:
        return 0.0
    weights, total = [], 0.0
    for i, v in enumerate(vals[:len(DECAY)]):
        w = DECAY[i]
        weights.append(v * w)
        total += w
    return sum(weights) / total if total else 0.0


def _venue_mult(venue: str, prop_type: str) -> float:
    return {"home": 1.03, "away": 0.97}.get(venue, 1.0)


def _opp_def_mult(opp_def_rating: Optional[float], prop_type: str) -> float:
    if not opp_def_rating:
        return 1.0
    league_avg = 100.0  # WNBA pts per 100 possessions avg
    delta = (opp_def_rating - league_avg) / league_avg
    if prop_type in ("points", "pts_reb_ast", "pts_reb", "pts_ast", "fantasy_points",
                     "three_pointers", "free_throws", "field_goals"):
        return max(0.82, min(1.18, 1.0 + delta * 0.85))
    if prop_type == "assists":
        return max(0.88, min(1.12, 1.0 + delta * 0.40))
    return 1.0


def compute_wnba_projection(
    game_logs: list,
    prop_type: str,
    line: float,
    venue: str = "home",
    opp_def_rating: Optional[float] = None,
    rest_days: Optional[int] = None,
    season_avg: Optional[dict] = None,
) -> dict:
    prop_type = prop_type.lower()
    field = WNBA_PROPS.get(prop_type)
    if not field:
        return {"error": f"Unknown WNBA prop: {prop_type}"}

    vals = _extract_series(game_logs, field)
    if len(vals) < MIN_VALID_LOG:
        return {"error": f"Insufficient game log data (n={len(vals)}, need {MIN_VALID_LOG})"}

    n = len(vals)
    raw_mean = sum(vals) / n

    hyper = HYPER_PRIOR.get(prop_type, raw_mean)
    alpha = min(n, MIN_SAMPLE) / MIN_SAMPLE
    prior = alpha * raw_mean + (1 - alpha) * hyper

    momentum  = _momentum(vals)
    projection = 0.40 * prior + 0.60 * momentum

    projection *= _venue_mult(venue, prop_type)
    projection *= _opp_def_mult(opp_def_rating, prop_type)

    if rest_days is not None:
        if rest_days == 0:
            projection *= 0.94
        elif rest_days >= 3:
            projection *= 1.03

    if prop_type in COUNT_PROPS:
        projection = round(projection)

    variance = stats_mod.variance(vals) if len(vals) > 1 else max(projection * 0.30, 1.0)
    is_discrete = prop_type in COUNT_PROPS
    _po, _pu, *_ = _mc(projection, variance, line, is_discrete)
    # _monte_carlo_probability returns fractions (0–1); convert to percentages
    p_over  = round(_po * 100, 2)
    p_under = round(_pu * 100, 2)

    recommendation = "over" if p_over >= p_under else "under"
    confidence = round(max(p_over, p_under))
    confidence_level = "High" if confidence >= 70 else "Medium" if confidence >= 60 else "Low"

    recent_5 = vals[:5]
    streak_flag = ""
    if len(recent_5) >= 4:
        if sum(1 for v in recent_5 if v > line) >= 4:
            streak_flag = "OVER_STREAK"
        elif sum(1 for v in recent_5 if v < line) >= 4:
            streak_flag = "UNDER_STREAK"

    return {
        "projection":      projection,
        "pOver":           p_over,
        "pUnder":          p_under,
        "recommendation":  recommendation,
        "confidenceScore": confidence,
        "confidenceLevel": confidence_level,
        "priorMean":       round(prior, 2),
        "momentum":        round(momentum, 2),
        "sampleSize":      n,
        "streakFlag":      streak_flag,
        "recentValues":    vals[:8],
    }
