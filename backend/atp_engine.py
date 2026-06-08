"""
ATP Tennis Projection Engine — mirrors WTA engine for men's tour.
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

ATP_PROPS = {
    "total_games":        "totalGames",
    "player_games_won":   "playerGamesWon",
    "opponent_games_won": "opponentGamesWon",
    "total_sets":         "setsPlayed",
    "player_sets_won":    "setsWon",
    "set_1_total_games":  "set1Total",
    "set_1_player_games": "set1PlayerGames",
    "match_winner":       "wonMatch",
    "first_set_winner":   "set1WinnerSubject",
}

BINARY_PROPS  = {"match_winner", "first_set_winner"}
PER_SET_PROPS = {"total_games", "player_games_won", "opponent_games_won"}
COUNT_PROPS   = {"total_games", "player_games_won", "opponent_games_won", "total_sets", "player_sets_won"}

PROP_LABELS = {
    "total_games":        "Total Games",
    "player_games_won":   "Player Games Won",
    "opponent_games_won": "Opponent Games Won",
    "total_sets":         "Total Sets",
    "player_sets_won":    "Sets Won",
    "set_1_total_games":  "Set 1 Total Games",
    "set_1_player_games": "Set 1 Player Games",
    "match_winner":       "Match Winner",
    "first_set_winner":   "First Set Winner",
}

# ATP hyper-priors (men's tennis games per match slightly higher than WTA)
HYPER_PRIOR = {
    "total_games":        23.5,
    "player_games_won":   11.2,
    "opponent_games_won": 12.3,
    "total_sets":          3.4,
    "player_sets_won":     1.6,
    "set_1_total_games":   9.8,
    "set_1_player_games":  4.7,
    "match_winner":        0.5,
    "first_set_winner":    0.5,
}

SURFACE_MULT = {
    "Hard":  1.0,
    "Clay":  0.97,
    "Grass": 1.01,
    "Indoor Hard": 1.0,
}

ROUND_MULT = {
    "F":   0.97,
    "SF":  0.98,
    "QF":  1.0,
    "R16": 1.01,
    "R32": 1.02,
    "R64": 1.03,
    "R128": 1.03,
    "RR":  1.0,
}

DECAY_WEIGHTS = [1.0, 0.88, 0.77, 0.68, 0.59, 0.52, 0.45, 0.40]


def _weighted_mean(values: list, weights: list) -> float:
    total_w = sum(weights[:len(values)])
    if total_w == 0:
        return 0.0
    return sum(v * w for v, w in zip(values, weights[:len(values)])) / total_w


def compute_atp_projection(
    match_logs: list,
    prop_type: str,
    line: float,
    surface: Optional[str] = "Hard",
    round_name: Optional[str] = None,
    opp_rank: Optional[int] = None,
    subject_rank: Optional[int] = None,
    h2h: Optional[dict] = None,
    rest_days: Optional[int] = None,
) -> dict:
    field = ATP_PROPS.get(prop_type)
    if not field:
        return {"error": f"Unknown ATP prop: {prop_type}"}

    is_binary = prop_type in BINARY_PROPS
    is_per_set = prop_type in PER_SET_PROPS

    raw_values = []
    per_set_vals = []
    for m in match_logs:
        v = m.get(field)
        if v is None:
            continue
        try:
            raw_values.append(float(v))
            if is_per_set:
                sets = m.get("setsPlayed", 1) or 1
                per_set_vals.append(float(v) / sets)
        except Exception:
            pass

    if is_binary and len(raw_values) < 3:
        raw_values = [0.5] * 5

    if len(raw_values) < 2:
        return {"error": f"Insufficient ATP match data for {prop_type} (n={len(raw_values)})"}

    values = raw_values
    n = len(values)
    hyper = HYPER_PRIOR.get(prop_type, line)
    k0 = max(3, 10 - n)
    prior_mean = (sum(values) + k0 * hyper) / (n + k0)

    recent = values[:8]
    weights = DECAY_WEIGHTS[:len(recent)]
    momentum_mean = _weighted_mean(recent, weights)

    alpha = min(n / (n + k0), 0.78)
    posterior = alpha * momentum_mean + (1 - alpha) * prior_mean

    # Surface adjustment
    surface_mult = SURFACE_MULT.get(surface or "Hard", 1.0)
    if is_per_set:
        surface_std = SURFACE_MULT.get(surface or "Hard", 1.0)
        posterior *= surface_std

    # Round adjustment
    round_mult = ROUND_MULT.get(round_name or "", 1.0)
    posterior *= round_mult

    # Opponent rank adjustment (better opponent = longer matches = more games)
    opp_adj = 1.0
    if opp_rank and subject_rank:
        rank_diff = subject_rank - opp_rank
        if rank_diff > 30:
            opp_adj = 0.97
        elif rank_diff < -30:
            opp_adj = 1.02
    posterior *= opp_adj

    # Rest days
    if rest_days is not None and not is_binary:
        if rest_days == 0:
            posterior *= 0.96
        elif rest_days >= 5:
            posterior *= 1.01

    if is_binary:
        projection = round(min(max(posterior, 0.0), 1.0), 3)
    elif prop_type in COUNT_PROPS:
        projection = round(posterior)
    else:
        projection = round(posterior, 1)

    _po, _pu, *_ = _mc(projection, values, line, prop_type in COUNT_PROPS and not is_binary)
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
        "surfaceMult":    surface_mult,
        "roundMult":      round_mult,
    }
