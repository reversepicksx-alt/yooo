"""
NBA Bayesian Projection Engine

Multi-layer model for basketball player props:
  Layer 1: PRIOR          — Season average + hyper-prior shrinkage
  Layer 2: MOMENTUM       — Exponential decay over last 8 games (newest-first)
  Layer 3: VENUE          — Home/away multiplier
  Layer 4: OPPONENT DEF   — Opponent defensive rating tier adjustment
  Layer 5: REST DAYS      — Back-to-back penalty / extra-rest boost
  Layer 6: USAGE TREND    — Minutes trend signal for volume props
  Monte Carlo: Negative-Binomial for discrete counts, Gaussian for rates
"""
import math
import random
import statistics as stats_mod
from typing import Optional
from bayesian_engine import _monte_carlo_probability as _baye_mc

# ── Prop definitions ─────────────────────────────────────────────────────────
NBA_PROPS = {
    "points":              "pts",
    "rebounds":            "reb",
    "assists":             "ast",
    "steals":              "stl",
    "blocks":              "blk",
    "turnovers":           "tov",
    "three_pointers":      "fg3m",
    "fantasy_points":      "fantasy_pts",
    "pts_reb_ast":         "pts_reb_ast",
    "pts_reb":             "pts_reb",
    "pts_ast":             "pts_ast",
    "reb_ast":             "reb_ast",
    "stl_blk":             "stl_blk",
    "free_throws":         "ftm",
    "field_goals":         "fgm",
}

COUNT_PROPS = {
    "points", "rebounds", "assists", "steals", "blocks", "turnovers",
    "three_pointers", "pts_reb_ast", "pts_reb", "pts_ast", "reb_ast",
    "stl_blk", "free_throws", "field_goals",
}

# League-average hyper-priors (NBA per-game, all positions blended)
HYPER_PRIOR = {
    "points":         12.5,
    "rebounds":        5.0,
    "assists":         3.0,
    "steals":          0.8,
    "blocks":          0.5,
    "turnovers":       1.8,
    "three_pointers":  1.2,
    "fantasy_points": 25.0,
    "pts_reb_ast":    20.5,
    "pts_reb":        17.5,
    "pts_ast":        15.5,
    "reb_ast":         8.0,
    "stl_blk":         1.3,
    "free_throws":     2.5,
    "field_goals":     4.5,
}

DECAY = [1.0, 0.82, 0.67, 0.55, 0.45, 0.37, 0.30, 0.24]

MIN_SAMPLE    = 8   # shrinkage kicks in below 8 games
MIN_VALID_LOG = 3   # need at least 3 games to run


def _extract_series(logs: list, field: str) -> list:
    """Pull numeric series for a field, newest-first, skipping DNP (< 5 min)."""
    vals = []
    for g in logs:
        if (g.get("minutes") or 0) < 5:
            continue
        v = g.get(field)
        if v is not None:
            try:
                vals.append(float(v))
            except Exception:
                pass
    return vals


def _momentum(vals: list) -> float:
    """Exponential-decay weighted mean over recent games."""
    if not vals:
        return 0.0
    weights, total = [], 0.0
    for i, v in enumerate(vals[:len(DECAY)]):
        w = DECAY[i]
        weights.append(v * w)
        total += w
    return sum(weights) / total if total else 0.0


def _venue_mult(venue: str, prop_type: str) -> float:
    """Home/away split. Basketball home court = ~4% pts advantage."""
    base = {"home": 1.03, "away": 0.97}.get(venue, 1.0)
    # Assists amplified at home (comfort), steals/blocks neutral
    if prop_type in ("assists", "points"):
        return base
    if prop_type in ("steals", "blocks"):
        return 1.0  # Defensive stats relatively venue-neutral
    return base


def _opp_def_mult(opp_def_rating: Optional[float], prop_type: str) -> float:
    """
    Opponent defensive rating adjustment.
    opp_def_rating: points allowed per 100 possessions.
    League avg ~113. Higher = worse defense (more generous for scorers).
    """
    if not opp_def_rating:
        return 1.0
    league_avg = 113.0
    delta = (opp_def_rating - league_avg) / league_avg
    # Scoring props are more sensitive; defensive stats inverse
    if prop_type in ("points", "pts_reb_ast", "pts_reb", "pts_ast", "fantasy_points", "three_pointers", "free_throws", "field_goals"):
        return max(0.82, min(1.18, 1.0 + delta * 0.85))
    if prop_type in ("assists",):
        return max(0.88, min(1.12, 1.0 + delta * 0.40))
    return 1.0  # rebounds/steals/blocks independent of offensive quality


def _rest_mult(rest_days: Optional[int], prop_type: str) -> float:
    """Back-to-back = fatigue penalty; extra rest = boost."""
    if rest_days is None:
        return 1.0
    if rest_days == 0:  # Back-to-back
        return 0.94
    if rest_days >= 3:  # Well-rested
        return 1.03
    return 1.0  # 1-2 days = normal


def _minutes_trend_mult(logs: list) -> float:
    """If recent minutes are trending higher/lower vs season average, adjust."""
    if len(logs) < 4:
        return 1.0
    recent_min = [g.get("minutes") or 0 for g in logs[:4]]
    old_min    = [g.get("minutes") or 0 for g in logs[4:min(12, len(logs))]]
    if not old_min or not recent_min:
        return 1.0
    r_avg = sum(recent_min) / len(recent_min)
    o_avg = sum(old_min) / len(old_min)
    if o_avg < 5:
        return 1.0
    delta = (r_avg - o_avg) / o_avg
    return max(0.88, min(1.12, 1.0 + delta * 0.60))


def compute_nba_projection(
    game_logs: list,
    prop_type: str,
    line: float,
    venue: str = "home",
    opp_def_rating: Optional[float] = None,
    rest_days: Optional[int] = None,
    season_avg: Optional[dict] = None,
) -> dict:
    prop_type = prop_type.lower()
    field = NBA_PROPS.get(prop_type)
    if not field:
        return {"error": f"Unknown NBA prop: {prop_type}"}

    vals = _extract_series(game_logs, field)
    if len(vals) < MIN_VALID_LOG:
        return {"error": f"Insufficient game log data (n={len(vals)}, need {MIN_VALID_LOG})"}

    n = len(vals)
    raw_mean = sum(vals) / n

    # ── Layer 1: Prior with hyper-prior shrinkage ──────────────────────────────
    hyper = HYPER_PRIOR.get(prop_type, raw_mean)
    alpha = min(n, MIN_SAMPLE) / MIN_SAMPLE
    prior = alpha * raw_mean + (1 - alpha) * hyper

    # Override prior with season averages if available
    if season_avg:
        sa_map = {
            "points": "pts", "rebounds": "reb", "assists": "ast",
            "steals": "stl", "blocks": "blk", "turnovers": "turnover",
            "three_pointers": "fg3m",
        }
        sa_field = sa_map.get(prop_type)
        if sa_field and season_avg.get(sa_field) is not None:
            sa_val = float(season_avg[sa_field] or 0)
            prior = alpha * sa_val + (1 - alpha) * hyper

    # ── Layer 2: Momentum ──────────────────────────────────────────────────────
    momentum = _momentum(vals)
    # Blend prior 40% + momentum 60% (recent form weights heavily in NBA)
    projection = 0.40 * prior + 0.60 * momentum

    # ── Layer 3: Venue ─────────────────────────────────────────────────────────
    projection *= _venue_mult(venue, prop_type)

    # ── Layer 4: Opponent defense ──────────────────────────────────────────────
    projection *= _opp_def_mult(opp_def_rating, prop_type)

    # ── Layer 5: Rest days ─────────────────────────────────────────────────────
    projection *= _rest_mult(rest_days, prop_type)

    # ── Layer 6: Minutes trend ─────────────────────────────────────────────────
    projection *= _minutes_trend_mult(game_logs)

    # Round count stats
    if prop_type in COUNT_PROPS:
        projection = round(projection)

    # ── Monte Carlo ────────────────────────────────────────────────────────────
    variance = stats_mod.variance(vals) if len(vals) > 1 else max(projection * 0.30, 1.5)
    is_discrete = prop_type in COUNT_PROPS
    _po, _pu, *_ = _baye_mc(projection, variance, line, is_count_stat=is_discrete)
    # _monte_carlo_probability returns fractions (0–1); convert to percentages
    p_over  = round(_po * 100, 2)
    p_under = round(_pu * 100, 2)

    recommendation = "over" if p_over >= p_under else "under"
    confidence = round(max(p_over, p_under))
    confidence_level = "High" if confidence >= 70 else "Medium" if confidence >= 60 else "Low"

    # Streak detection
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
        "recentValues":     vals[:8],
    }
