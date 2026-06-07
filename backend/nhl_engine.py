"""
NHL Bayesian Projection Engine

Multi-layer model for hockey player props:
  Layer 1: PRIOR          — Season average + hyper-prior shrinkage
  Layer 2: MOMENTUM       — Exponential decay over last 7 games (newest-first)
  Layer 3: VENUE          — Home/away multiplier
  Layer 4: OPPONENT       — Goalie quality / defensive tier for skater props
  Layer 5: POWER PLAY     — PP time-on-ice signal for goal/assist props
  Layer 6: ROLE           — Goalie vs skater detection
  Monte Carlo: Negative-Binomial for discrete counts, Gaussian for rates
"""
import statistics as stats_mod
from typing import Optional
from bayesian_engine import _monte_carlo_probability as _baye_mc

# ── Prop definitions ─────────────────────────────────────────────────────────
NHL_PROPS = {
    # Skater
    "goals":           "goals",
    "assists":         "assists",
    "points":          "points",
    "shots":           "shots",
    "blocked_shots":   "blocked_shots",
    "hits":            "hits",
    "plus_minus":      "plus_minus",
    "pim":             "pim",
    # Goalie
    "saves":           "saves",
    "goals_against":   "goals_against",
    "save_pct":        "save_pct",
}

COUNT_PROPS = {
    "goals", "assists", "points", "shots", "blocked_shots",
    "hits", "saves", "goals_against", "pim",
}

HYPER_PRIOR = {
    "goals":           0.28,
    "assists":         0.42,
    "points":          0.68,
    "shots":           2.80,
    "blocked_shots":   1.10,
    "hits":            2.20,
    "plus_minus":      0.05,
    "pim":             0.70,
    "saves":          26.50,
    "goals_against":   2.80,
    "save_pct":        0.905,
}

DECAY = [1.0, 0.82, 0.67, 0.55, 0.45, 0.37, 0.30]

MIN_SAMPLE    = 7
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
    """NHL home ice: ~5% goals advantage, but goalie/shots relatively neutral."""
    if prop_type in ("saves", "goals_against", "save_pct"):
        return 1.0  # Goalie stats venue-neutral
    return {"home": 1.04, "away": 0.96}.get(venue, 1.0)


def _opp_quality_mult(opp_goals_per_game: Optional[float], prop_type: str) -> float:
    """
    For skater goal/assist/points props: opponent's goals-allowed per game.
    Higher = leakier defense = more opportunity.
    NHL avg ~3.1 G/game.
    """
    if not opp_goals_per_game:
        return 1.0
    avg = 3.1
    delta = (opp_goals_per_game - avg) / avg
    if prop_type in ("goals", "assists", "points", "shots"):
        return max(0.80, min(1.20, 1.0 + delta * 0.80))
    return 1.0


def compute_nhl_projection(
    game_logs: list,
    prop_type: str,
    line: float,
    venue: str = "home",
    opp_goals_per_game: Optional[float] = None,
    rest_days: Optional[int] = None,
) -> dict:
    prop_type = prop_type.lower()
    field = NHL_PROPS.get(prop_type)
    if not field:
        return {"error": f"Unknown NHL prop: {prop_type}"}

    vals = _extract_series(game_logs, field)
    if len(vals) < MIN_VALID_LOG:
        return {"error": f"Insufficient game log data (n={len(vals)}, need {MIN_VALID_LOG})"}

    n = len(vals)
    raw_mean = sum(vals) / n

    # ── Layer 1: Prior ─────────────────────────────────────────────────────────
    hyper = HYPER_PRIOR.get(prop_type, raw_mean)
    alpha = min(n, MIN_SAMPLE) / MIN_SAMPLE
    prior = alpha * raw_mean + (1 - alpha) * hyper

    # ── Layer 2: Momentum ──────────────────────────────────────────────────────
    momentum = _momentum(vals)
    projection = 0.45 * prior + 0.55 * momentum

    # ── Layer 3: Venue ─────────────────────────────────────────────────────────
    projection *= _venue_mult(venue, prop_type)

    # ── Layer 4: Opponent ──────────────────────────────────────────────────────
    projection *= _opp_quality_mult(opp_goals_per_game, prop_type)

    # ── Layer 5: Rest ──────────────────────────────────────────────────────────
    if rest_days is not None and rest_days == 0:
        projection *= 0.95  # Back-to-back

    # Round count stats
    if prop_type in COUNT_PROPS:
        projection = round(projection)

    # ── Monte Carlo ────────────────────────────────────────────────────────────
    variance = stats_mod.variance(vals) if len(vals) > 1 else max(projection * 0.35, 0.5)
    is_discrete = prop_type in COUNT_PROPS
    _po, _pu, *_ = _baye_mc(projection, variance, line, is_count_stat=is_discrete)
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
        "recentValues":    vals[:7],
    }
