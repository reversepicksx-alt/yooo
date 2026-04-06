"""
3-LAYER BAYESIAN ENGINE v2 — Elite Mathematical Computation

v1 had a critical flaw: the Covariate layer (match context) drowned out
Prior and Momentum for high-variance players because precision = n/variance
collapsed near zero while Covariate stayed fixed at 3.0.

v2 fixes:
- Floor precisions: Prior and Momentum can never be zeroed out
- Covariate hard-capped at 25% of total weight (informs, never overrides)
- Exponential decay weighting in Momentum (most recent game = highest weight)
- Streak detection vs the line (consecutive over/under pattern)
- Volatility scoring via coefficient of variation
- Adaptive precision that respects sample size even for chaotic players

Layer 1: PRIOR — Season average (baseline expectation + mean reversion anchor)
Layer 2: MOMENTUM — Exponentially-weighted recent form (last 5 games, recency premium)
Layer 3: COVARIATE — Match context adjustments (venue, opponent, dominance) — CAPPED

Posterior = Precision-weighted combination with adaptive floors
"""
import math
import statistics as stats_mod
from typing import Optional


# Hard cap: Covariate can be at most this ratio of (Prior + Momentum)
# ratio=0.33 guarantees Covariate weight <= 25% of total
MAX_COVARIATE_RATIO = 0.33

# Exponential decay weights for recency (most recent game = index 0)
DECAY_WEIGHTS = [1.0, 0.82, 0.67, 0.55, 0.45]


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

    # Extract stat values
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

    # Coefficient of variation — volatility indicator
    cv = prior_std / prior_mean if prior_mean > 0 else 0

    # Prior precision: Bayesian base (n/variance) with sample-size floor
    # The floor ensures high-variance players still carry meaningful weight
    # n^0.6 scales sub-linearly with sample size: 5→2.6, 10→4.0, 20→6.3, 30→8.0
    raw_prior_prec = n / prior_variance if prior_variance > 0 else n * 2
    prior_precision = max(raw_prior_prec, n ** 0.6, 2.0)

    # ═══════════════════════════════════════════
    # LAYER 2: MOMENTUM — Exponentially-Weighted Recent Form
    # ═══════════════════════════════════════════
    recent_5 = all_vals[:5] if len(all_vals) >= 5 else all_vals[:max(3, len(all_vals))]
    recent_3 = all_vals[:3] if len(all_vals) >= 3 else all_vals

    # Apply exponential decay: most recent game gets highest weight
    weights = DECAY_WEIGHTS[:len(recent_5)]
    total_w = sum(weights)
    momentum_mean = sum(w * v for w, v in zip(weights, recent_5)) / total_w

    # Weighted variance (Bessel-corrected for weighted samples)
    if len(recent_5) >= 3:
        wm = momentum_mean
        weighted_ss = sum(w * (v - wm) ** 2 for w, v in zip(weights, recent_5))
        # Reliability weights correction factor
        v1 = total_w
        v2 = sum(w ** 2 for w in weights)
        denom = v1 - (v2 / v1)
        momentum_variance = weighted_ss / denom if denom > 0 else prior_variance
    else:
        momentum_variance = prior_variance

    momentum_effect = round(momentum_mean - prior_mean, 2)

    # Momentum precision: recency premium (3x per effective sample) with floor
    # The *3 multiplier reflects that recent data is 3x more predictive than old data
    raw_mom_prec = (total_w * 3) / momentum_variance if momentum_variance > 0 else total_w * 6
    momentum_precision = max(raw_mom_prec, 2.5)

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

    # Trend consistency bonus: if trend is strong AND consistent, boost momentum
    if len(recent_5) >= 3 and trend_per_game != 0:
        # Check if all recent games follow the trend direction
        if trend_per_game > 0:
            trend_consistent = sum(1 for i in range(len(recent_5) - 1) if recent_5[i] >= recent_5[i + 1])
        else:
            trend_consistent = sum(1 for i in range(len(recent_5) - 1) if recent_5[i] <= recent_5[i + 1])
        consistency_ratio = trend_consistent / (len(recent_5) - 1)
        if consistency_ratio >= 0.75:
            # Strong consistent trend — boost momentum precision by up to 30%
            momentum_precision *= (1 + consistency_ratio * 0.3)

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

    # Streak detection vs the line
    streak_flag = "NONE"
    if len(recent_5) >= 3:
        over_count = sum(1 for v in recent_5 if v > line)
        under_count = sum(1 for v in recent_5 if v < line)
        if over_count == len(recent_5):
            streak_flag = f"OVER_{len(recent_5)}"
        elif under_count == len(recent_5):
            streak_flag = f"UNDER_{len(recent_5)}"
        elif len(recent_3) >= 3:
            o3 = sum(1 for v in recent_3 if v > line)
            u3 = sum(1 for v in recent_3 if v < line)
            if o3 == 3:
                streak_flag = "OVER_3"
            elif u3 == 3:
                streak_flag = "UNDER_3"

    # ═══════════════════════════════════════════
    # LAYER 3: COVARIATE — Context Adjustments (CAPPED)
    # ═══════════════════════════════════════════
    covariate_adjustment = 0.0

    # 3a. Venue split adjustment
    venue_vals = [g.get(stat_field, g.get("targetStat")) for g in game_logs
                  if g.get("venue") == venue and g.get(stat_field, g.get("targetStat")) is not None]
    if venue_vals and len(venue_vals) >= 3:
        venue_mean = sum(venue_vals) / len(venue_vals)
        venue_adj = venue_mean - prior_mean
        venue_weight = min(1.0, len(venue_vals) / 10)
        covariate_adjustment += venue_adj * venue_weight

    # 3b. Match dominance adjustment
    if match_dominance and match_dominance.get("multiplier"):
        dom_mult = match_dominance.get("multiplier", 1.0)
        poss_sensitive_props = {"pass_attempts", "passes", "shots", "shots_on_target",
                                "key_passes", "crosses", "dribbles", "tackles"}
        if prop_type in poss_sensitive_props and dom_mult != 1.0:
            dom_adj = prior_mean * (dom_mult - 1.0)
            covariate_adjustment += dom_adj

    # 3c. Opponent strength adjustment
    if opponent_fixture_stats:
        opp_conceded = _estimate_opponent_concession(opponent_fixture_stats, prop_type)
        if opp_conceded is not None:
            opp_adj = opp_conceded - prior_mean
            covariate_adjustment += opp_adj * 0.15

    covariate_adjustment = round(covariate_adjustment, 2)

    # Covariate precision: base contextual precision, boosted by venue data quality
    raw_cov_prec = 3.0
    if venue_vals and len(venue_vals) >= 5:
        raw_cov_prec += 1.0

    # HARD CAP: Covariate can never exceed 25% of total weight
    # Math: if cov = (P+M)*0.33, then cov/(P+M+cov) = 0.33/1.33 = 24.8%
    covariate_precision = min(raw_cov_prec, (prior_precision + momentum_precision) * MAX_COVARIATE_RATIO)

    # ═══════════════════════════════════════════
    # POSTERIOR — Precision-weighted combination
    # ═══════════════════════════════════════════
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
    # Use a blended std that accounts for both posterior precision AND player volatility
    # This prevents overconfident probabilities for volatile players
    effective_std = max(posterior_std, prior_std * 0.4)

    if effective_std > 0:
        z = (line - posterior_mean) / effective_std
        p_under = _normal_cdf(z)
        p_over = 1 - p_under
    else:
        p_over = 1.0 if posterior_mean > line else 0.0
        p_under = 1 - p_over

    # Confidence interval (80%) — uses effective_std for realistic bands
    ci_low = round(posterior_mean - 1.28 * effective_std, 1)
    ci_high = round(posterior_mean + 1.28 * effective_std, 1)

    # Edge = how far posterior is from line as % of std dev
    edge_z = round(abs(posterior_mean - line) / effective_std, 2) if effective_std > 0 else 0

    # Layer weights (for transparency)
    w_prior = round(prior_precision / total_precision * 100)
    w_momentum = round(momentum_precision / total_precision * 100)
    w_covariate = round(covariate_precision / total_precision * 100)

    # Volatility classification
    if cv < 0.15:
        volatility_label = "LOW"
    elif cv < 0.30:
        volatility_label = "NORMAL"
    elif cv < 0.50:
        volatility_label = "HIGH"
    else:
        volatility_label = "EXTREME"

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
        "streakFlag": streak_flag,
        "volatility": volatility_label,
        "cv": round(cv, 3),
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
        "streakFlag": "NONE",
        "volatility": "UNKNOWN",
        "cv": 0,
    }


def _normal_cdf(z: float) -> float:
    """Approximate normal CDF using Abramowitz & Stegun formula."""
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

    team_avg = sum(values) / len(values)
    player_share = 0.18
    return round(team_avg * player_share, 1)
