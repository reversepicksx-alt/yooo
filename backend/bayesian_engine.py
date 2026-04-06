"""
3-LAYER BAYESIAN ENGINE — Real mathematical computation.
Replaces AI-guessed bayesianMetrics with deterministic math.

Layer 1: PRIOR — Season average (baseline expectation)
Layer 2: MOMENTUM — Recent trend adjustment (last 5 vs season)
Layer 3: COVARIATE — Matchup context (venue, opponent, dominance)

Posterior = Weighted combination → final projected value
"""
import math
import statistics as stats_mod
from typing import Optional


def compute_bayesian_projection(
    game_logs: list,
    prop_type: str,
    line: float,
    venue: str,
    stat_field: str = "targetStat",
    opponent_fixture_stats: list = None,
    match_dominance: dict = None,
) -> dict:
    """
    Compute a 3-layer Bayesian projection from raw game data.
    Returns the full bayesianMetrics dict with real computed values.
    """
    if not game_logs:
        return _empty_metrics(line)

    # Extract stat values and minutes
    all_vals = [g.get(stat_field, g.get("targetStat")) for g in game_logs
                if g.get(stat_field, g.get("targetStat")) is not None]
    if not all_vals:
        return _empty_metrics(line)

    n = len(all_vals)

    # ═══════════════════════════════════════════
    # LAYER 1: PRIOR — Season Average Baseline
    # ═══════════════════════════════════════════
    prior_mean = sum(all_vals) / n
    prior_variance = stats_mod.variance(all_vals) if n >= 3 else (max(all_vals) - min(all_vals)) ** 2 / 4
    prior_std = math.sqrt(prior_variance) if prior_variance > 0 else 1.0
    # Prior precision (inverse variance) — more games = more precise prior
    prior_precision = n / prior_variance if prior_variance > 0 else n

    # ═══════════════════════════════════════════
    # LAYER 2: MOMENTUM — Recent Trend
    # ═══════════════════════════════════════════
    recent_5 = all_vals[:5] if len(all_vals) >= 5 else all_vals[:max(3, len(all_vals))]
    recent_3 = all_vals[:3] if len(all_vals) >= 3 else all_vals

    momentum_mean = sum(recent_5) / len(recent_5)
    momentum_variance = stats_mod.variance(recent_5) if len(recent_5) >= 3 else prior_variance
    # Momentum precision — fewer samples but more recent
    # Weight recent data 2x per sample (recency premium)
    momentum_precision = (len(recent_5) * 2) / momentum_variance if momentum_variance > 0 else len(recent_5) * 2

    momentum_effect = round(momentum_mean - prior_mean, 2)

    # Trend direction detection (linear regression slope on last 5)
    if len(recent_5) >= 3:
        x_vals = list(range(len(recent_5)))
        x_mean = sum(x_vals) / len(x_vals)
        y_mean = sum(recent_5) / len(recent_5)
        numerator = sum((x - x_mean) * (y - y_mean) for x, y in zip(x_vals, recent_5))
        denominator = sum((x - x_mean) ** 2 for x in x_vals)
        slope = numerator / denominator if denominator > 0 else 0
        trend_per_game = round(slope, 2)
    else:
        trend_per_game = 0

    # Momentum label
    if momentum_effect > 2:
        momentum_label = "HOT"
    elif momentum_effect > 0.5:
        momentum_label = "WARMING"
    elif momentum_effect < -2:
        momentum_label = "COLD"
    elif momentum_effect < -0.5:
        momentum_label = "COOLING"
    else:
        momentum_label = "STABLE"

    # ═══════════════════════════════════════════
    # LAYER 3: COVARIATE — Context Adjustments
    # ═══════════════════════════════════════════
    covariate_adjustment = 0.0

    # 3a. Venue split adjustment
    venue_vals = [g.get(stat_field, g.get("targetStat")) for g in game_logs
                  if g.get("venue") == venue and g.get(stat_field, g.get("targetStat")) is not None]
    if venue_vals and len(venue_vals) >= 3:
        venue_mean = sum(venue_vals) / len(venue_vals)
        venue_adj = venue_mean - prior_mean
        # Weight by sample size (min 3 games to matter)
        venue_weight = min(1.0, len(venue_vals) / 10)
        covariate_adjustment += venue_adj * venue_weight

    # 3b. Match dominance adjustment
    if match_dominance and match_dominance.get("multiplier"):
        dom_mult = match_dominance.get("multiplier", 1.0)
        # Possession-based stats (passes, shots) scale with dominance
        poss_sensitive_props = {"pass_attempts", "passes", "shots", "shots_on_target",
                                "key_passes", "crosses", "dribbles", "tackles"}
        if prop_type in poss_sensitive_props and dom_mult != 1.0:
            dom_adj = prior_mean * (dom_mult - 1.0)
            covariate_adjustment += dom_adj

    # 3c. Opponent strength adjustment
    if opponent_fixture_stats:
        # If opponent concedes more/fewer of this stat type, adjust
        opp_conceded = _estimate_opponent_concession(opponent_fixture_stats, prop_type)
        if opp_conceded is not None:
            opp_adj = opp_conceded - prior_mean
            # Light weight — opponent data is noisy
            covariate_adjustment += opp_adj * 0.15

    covariate_adjustment = round(covariate_adjustment, 2)

    # Covariate precision — fixed moderate precision (context is informative but noisy)
    covariate_precision = 3.0

    # ═══════════════════════════════════════════
    # POSTERIOR — Precision-weighted combination
    # ═══════════════════════════════════════════
    # Bayesian posterior mean = sum(precision_i * mean_i) / sum(precision_i)
    total_precision = prior_precision + momentum_precision + covariate_precision

    posterior_mean = (
        prior_precision * prior_mean +
        momentum_precision * momentum_mean +
        covariate_precision * (prior_mean + covariate_adjustment)
    ) / total_precision

    posterior_mean = round(posterior_mean, 1)
    posterior_std = round(math.sqrt(1 / total_precision) if total_precision > 0 else prior_std, 2)

    # ═══════════════════════════════════════════
    # REVERSAL FLAG — Mean Reversion Detection
    # ═══════════════════════════════════════════
    # If recent performance is >1.5 std devs from season mean, flag likely reversion
    if prior_std > 0 and abs(momentum_mean - prior_mean) > 1.5 * prior_std:
        if momentum_mean > prior_mean:
            reversal_flag = "LIKELY REVERSION DOWN"
        else:
            reversal_flag = "LIKELY REVERSION UP"
    elif abs(momentum_effect) > 1.0 * prior_std and abs(momentum_effect) <= 1.5 * prior_std:
        reversal_flag = "WATCH"
    else:
        reversal_flag = "STABLE"

    # ═══════════════════════════════════════════
    # PROBABILITY CURVE — Over/Under probabilities
    # ═══════════════════════════════════════════
    # P(over) using normal CDF with posterior mean and std
    if posterior_std > 0:
        z = (line - posterior_mean) / posterior_std
        p_under = _normal_cdf(z)
        p_over = 1 - p_under
    else:
        p_over = 1.0 if posterior_mean > line else 0.0
        p_under = 1 - p_over

    # Confidence interval (80%)
    ci_low = round(posterior_mean - 1.28 * posterior_std, 1)
    ci_high = round(posterior_mean + 1.28 * posterior_std, 1)

    # Edge = how far posterior is from line as % of std dev
    edge_z = round(abs(posterior_mean - line) / posterior_std, 2) if posterior_std > 0 else 0

    # Layer weights (for transparency)
    w_prior = round(prior_precision / total_precision * 100)
    w_momentum = round(momentum_precision / total_precision * 100)
    w_covariate = round(covariate_precision / total_precision * 100)

    return {
        # Core output
        "posteriorMean": posterior_mean,
        "posteriorStd": posterior_std,
        "recommendation": "over" if posterior_mean > line else "under",
        "pOver": round(p_over * 100, 1),
        "pUnder": round(p_under * 100, 1),
        "confidenceInterval": [ci_low, ci_high],
        "edgeZ": edge_z,

        # 3 Layers (for transparency)
        "priorMean": round(prior_mean, 1),
        "priorStd": round(prior_std, 2),
        "priorWeight": w_prior,
        "priorSamples": n,

        "momentumEffect": momentum_effect,
        "momentumMean": round(momentum_mean, 1),
        "momentumLabel": momentum_label,
        "momentumWeight": w_momentum,
        "trendPerGame": trend_per_game,

        "covariateAdjustment": covariate_adjustment,
        "covariateWeight": w_covariate,
        "venueAvg": round(sum(venue_vals) / len(venue_vals), 1) if venue_vals else None,
        "venueSamples": len(venue_vals) if venue_vals else 0,

        "reversalFlag": reversal_flag,
    }


def _empty_metrics(line: float) -> dict:
    """Return empty Bayesian metrics when no data is available."""
    return {
        "posteriorMean": line,
        "posteriorStd": 0,
        "recommendation": "over",
        "pOver": 50.0,
        "pUnder": 50.0,
        "confidenceInterval": [line, line],
        "edgeZ": 0,
        "priorMean": line,
        "priorStd": 0,
        "priorWeight": 100,
        "priorSamples": 0,
        "momentumEffect": 0,
        "momentumMean": line,
        "momentumLabel": "NO DATA",
        "momentumWeight": 0,
        "trendPerGame": 0,
        "covariateAdjustment": 0,
        "covariateWeight": 0,
        "venueAvg": None,
        "venueSamples": 0,
        "reversalFlag": "NO DATA",
    }


def _normal_cdf(z: float) -> float:
    """Approximate normal CDF using Abramowitz & Stegun formula."""
    # Handle extremes
    if z > 6:
        return 1.0
    if z < -6:
        return 0.0
    sign = 1 if z >= 0 else -1
    z = abs(z)
    t = 1 / (1 + 0.2316419 * z)
    d = 0.3989422804014327  # 1/sqrt(2*pi)
    p = d * math.exp(-z * z / 2) * (
        t * (0.319381530 + t * (-0.356563782 + t * (1.781477937 + t * (-1.821255978 + t * 1.330274429))))
    )
    return 0.5 + sign * (0.5 - p)


def _estimate_opponent_concession(opp_fixture_stats: list, prop_type: str) -> Optional[float]:
    """Estimate how much of a stat type the opponent typically concedes."""
    if not opp_fixture_stats:
        return None

    # Map prop types to team stat fields
    stat_map = {
        "pass_attempts": "totalPasses",
        "passes": "totalPasses",
        "shots": "totalShots",
        "shots_on_target": "shotsOnTarget",
    }
    field = stat_map.get(prop_type)
    if not field:
        return None

    values = [s.get(field) for s in opp_fixture_stats if s.get(field) is not None]
    if not values or len(values) < 2:
        return None

    # These are team-level stats that the opponent CONCEDED
    # A single player typically accounts for ~15-25% of team total
    team_avg = sum(values) / len(values)
    player_share = 0.18  # ~18% average player contribution
    return round(team_avg * player_share, 1)
