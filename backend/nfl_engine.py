"""
NFL Bayesian Projection Engine

Multi-layer model for football player props:
  Layer 1: PRIOR          — Season average + hyper-prior shrinkage
  Layer 2: MOMENTUM       — Exponential decay over last 6 games (newest-first)
  Layer 3: VENUE          — Home/away multiplier
  Layer 4: OPPONENT DEF   — Position-group defensive rank adjustment
  Layer 5: GAME SCRIPT    — O/U total line encodes pace/shootout vs grind
  Layer 6: ROLE DETECTION — Position derived from stat profile (QB/RB/WR/TE)
  Monte Carlo: Negative-Binomial for counts, Gaussian for continuous
"""
import math
import statistics as stats_mod
from typing import Optional
from bayesian_engine import _monte_carlo_probability as _baye_mc

# ── Prop definitions ─────────────────────────────────────────────────────────
NFL_PROPS = {
    # Passing
    "passing_yards":       "passing_yards",
    "passing_tds":         "passing_tds",
    "completions":         "passing_completions",
    "pass_attempts":       "passing_attempts",
    "interceptions":       "interceptions",
    "passing_rushing_yards": "passing_rushing_yards",
    # Rushing
    "rushing_yards":       "rushing_yards",
    "rushing_tds":         "rushing_tds",
    "carries":             "carries",
    # Receiving
    "receiving_yards":     "receiving_yards",
    "receiving_tds":       "receiving_tds",
    "receptions":          "receptions",
    "targets":             "targets",
    # Fantasy / combos
    "fantasy_points":      "fantasy_pts",
    "anytime_td":          "anytime_td",
}

COUNT_PROPS = {
    "passing_tds", "completions", "pass_attempts", "interceptions",
    "rushing_tds", "carries", "receptions", "targets",
    "receiving_tds", "anytime_td",
}

# League-average hyper-priors
HYPER_PRIOR = {
    "passing_yards":         220.0,
    "passing_tds":             1.5,
    "completions":            20.0,
    "pass_attempts":          32.0,
    "interceptions":           0.9,
    "passing_rushing_yards": 235.0,
    "rushing_yards":          55.0,
    "rushing_tds":             0.45,
    "carries":                13.0,
    "receiving_yards":        45.0,
    "receiving_tds":           0.30,
    "receptions":              4.5,
    "targets":                 6.0,
    "fantasy_points":         14.0,
    "anytime_td":              0.55,
}

DECAY = [1.0, 0.80, 0.64, 0.51, 0.41, 0.33]

MIN_SAMPLE    = 6
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
    """NFL home advantage is ~3 pts per game → translates to ~4% yardage."""
    base = {"home": 1.04, "away": 0.96}.get(venue, 1.0)
    # Turnover-prone props neutral for venue
    if prop_type == "interceptions":
        return 1.0
    return base


def _game_script_mult(game_total: Optional[float], prop_type: str) -> float:
    """
    High game total (shootout) = more passing volume.
    Low total (defensive game) = more rushing / conservative play.
    League avg O/U ~44.5 points.
    """
    if not game_total:
        return 1.0
    league_avg = 44.5
    delta = (game_total - league_avg) / league_avg
    if prop_type in ("passing_yards", "completions", "pass_attempts", "passing_tds",
                     "receiving_yards", "receptions", "targets"):
        return max(0.85, min(1.15, 1.0 + delta * 0.70))
    if prop_type in ("rushing_yards", "carries"):
        return max(0.88, min(1.12, 1.0 - delta * 0.40))
    return 1.0


def _opp_def_mult(opp_rank_percentile: Optional[float], prop_type: str) -> float:
    """
    opp_rank_percentile: 0.0 (best defense) to 1.0 (worst defense).
    Better defense = lower projection.
    """
    if opp_rank_percentile is None:
        return 1.0
    # Map to multiplier: best D → 0.88, worst D → 1.12
    return max(0.88, min(1.12, 0.88 + opp_rank_percentile * 0.24))


def compute_nfl_projection(
    game_logs: list,
    prop_type: str,
    line: float,
    venue: str = "home",
    game_total: Optional[float] = None,
    opp_rank_percentile: Optional[float] = None,
    rest_days: Optional[int] = None,
    position: str = "",
) -> dict:
    prop_type = prop_type.lower()
    field = NFL_PROPS.get(prop_type)
    if not field:
        return {"error": f"Unknown NFL prop: {prop_type}"}

    vals = _extract_series(game_logs, field)
    if len(vals) < MIN_VALID_LOG:
        return {"error": f"Insufficient game log data (n={len(vals)}, need {MIN_VALID_LOG})"}

    n = len(vals)
    raw_mean = sum(vals) / n

    # ── Layer 1: Prior + hyper-prior shrinkage ─────────────────────────────────
    hyper = HYPER_PRIOR.get(prop_type, raw_mean)
    alpha = min(n, MIN_SAMPLE) / MIN_SAMPLE
    prior = alpha * raw_mean + (1 - alpha) * hyper

    # ── Layer 2: Momentum ──────────────────────────────────────────────────────
    momentum = _momentum(vals)
    projection = 0.45 * prior + 0.55 * momentum

    # ── Layer 3: Venue ─────────────────────────────────────────────────────────
    projection *= _venue_mult(venue, prop_type)

    # ── Layer 4: Game script ───────────────────────────────────────────────────
    projection *= _game_script_mult(game_total, prop_type)

    # ── Layer 5: Opponent defense ──────────────────────────────────────────────
    projection *= _opp_def_mult(opp_rank_percentile, prop_type)

    # ── Layer 6: Rest ──────────────────────────────────────────────────────────
    if rest_days is not None:
        if rest_days <= 4:  # Short week (Thursday game)
            projection *= 0.95
        elif rest_days >= 10:  # Bye week rest
            projection *= 1.04

    # Round count stats
    if prop_type in COUNT_PROPS:
        projection = round(projection)

    # ── Monte Carlo ────────────────────────────────────────────────────────────
    variance = stats_mod.variance(vals) if len(vals) > 1 else max(projection * 0.35, 2.0)
    is_discrete = prop_type in COUNT_PROPS
    p_over, p_under = _baye_mc(projection, variance, line, discrete=is_discrete)

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
        "projection":       projection,
        "pOver":            p_over,
        "pUnder":           p_under,
        "recommendation":   recommendation,
        "confidenceScore":  confidence,
        "confidenceLevel":  confidence_level,
        "priorMean":        round(prior, 2),
        "momentum":         round(momentum, 2),
        "sampleSize":       n,
        "streakFlag":       streak_flag,
        "recentValues":     vals[:6],
    }
