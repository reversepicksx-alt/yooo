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

        # Props where MORE possession = MORE stats
        positive_poss_props = {"pass_attempts", "passes", "shots", "shots_on_target",
                               "key_passes", "crosses", "dribbles"}
        # Props where LESS possession = MORE stats (defensive actions)
        inverse_poss_props = {"tackles", "interceptions", "blocks", "clearances"}

        if prop_type in positive_poss_props and dom_mult != 1.0:
            dom_adj = prior_mean * (dom_mult - 1.0)
            covariate_adjustment += dom_adj
        elif prop_type in inverse_poss_props and dom_mult != 1.0:
            # INVERTED: less possession → more defensive actions
            # dom_mult=0.76 means 24% less possession → ~12% more tackles (dampened 0.5×)
            dom_adj = prior_mean * (1.0 - dom_mult) * 0.5
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
    # POSSESSION SQUEEZE — Direct multiplier for ball-control props
    # Applied AFTER posterior when the player's team faces a meaningful
    # possession disadvantage vs their season norm.
    #
    # WHY: The covariate pathway (≤25% weight) dilutes the possession signal
    # to ~4% effect even for severe imbalances (e.g. Real Madrid at Bayern).
    # When a team loses 10+ poss points vs their average, individual pass
    # volume drops near-linearly. This step captures that directly.
    #
    # Activates when: expected_poss < team_season_avg × 0.92 (8%+ below norm)
    # Multiplier: poss_ratio^1.3 — slightly convex so extremes hit harder
    # Floor: 0.60 — prevents overcorrection (max 40% reduction)
    # ═══════════════════════════════════════════
    BALL_CONTROL_PROPS = {"pass_attempts", "passes", "key_passes", "crosses", "dribbles"}
    if match_dominance and prop_type in BALL_CONTROL_PROPS:
        expected_poss = match_dominance.get("expectedPoss")
        team_season_avg_poss = match_dominance.get("teamSeasonAvg")
        if expected_poss is not None and team_season_avg_poss and team_season_avg_poss > 0:
            poss_ratio = expected_poss / team_season_avg_poss
            if poss_ratio < 0.92:
                squeeze_mult = round(max(0.60, poss_ratio ** 1.3), 3)
                raw_before_squeeze = posterior_mean
                posterior_mean = round(posterior_mean * squeeze_mult, 1)
                print(f"[POSS SQUEEZE] {prop_type}: team_avg={team_season_avg_poss:.1f}% "
                      f"expected={expected_poss:.1f}% ratio={poss_ratio:.2f} "
                      f"mult={squeeze_mult} {raw_before_squeeze} → {posterior_mean}")

    # ═══════════════════════════════════════════
    # PRESS INTENSITY — PPDA Proxy (independent of match dominance)
    # Applied AFTER posterior for pass_attempts/passes props only.
    #
    # PRIMARY: opponent tackles + interceptions/game (aggregated from /fixtures/players)
    # FALLBACK: opponent possession % + passes/game
    #
    # Multiplier capped at 10% max — this is ADDITIVE to match dominance, not
    # duplicating it. Match dominance = possession/ball-time. Pressing = active
    # defensive disruption. These are correlated but independently causal.
    # Max 10% ensures no overcorrection when both signals are present.
    # ═══════════════════════════════════════════
    press_intensity_info = {
        "score": 0.0, "multiplier": 1.0, "label": "Unknown", "signal_used": None,
        "avg_defensive_actions": None, "avg_tackles": None, "avg_interceptions": None,
        "avg_poss": None, "avg_passes": None,
    }
    if opponent_fixture_stats and prop_type in {"pass_attempts", "passes"}:
        press_intensity_info = compute_press_intensity_score(opponent_fixture_stats)
        if press_intensity_info["multiplier"] < 1.0:
            raw_before = posterior_mean
            posterior_mean = round(posterior_mean * press_intensity_info["multiplier"], 1)
            print(f"[PRESS] {prop_type}: signal={press_intensity_info['signal_used']} label={press_intensity_info['label']} "
                  f"(score={press_intensity_info['score']}, mult={press_intensity_info['multiplier']}) "
                  f"da={press_intensity_info.get('avg_defensive_actions')} tkl={press_intensity_info.get('avg_tackles')} "
                  f"int={press_intensity_info.get('avg_interceptions')} → {raw_before} → {posterior_mean}")

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
        "pressIntensity": press_intensity_info,
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


def compute_press_intensity_score(opp_fixture_stats: list) -> dict:
    """
    Press Intensity Score — PPDA Proxy for pass_attempts/passes props.

    ─── PRIMARY SIGNAL (when available) ───────────────────────────────────────
    Opponent's avg tackles + interceptions per game, aggregated from player-level
    data via /fixtures/players (fetched and cached in fetch_fixture_team_stats).

    This is the correct PPDA-proxy signal: defensive actions directly measure
    how aggressively a team hunts the ball when not in possession.
      Low-press team  : ~14 tackles + 8  interceptions = ~22 def actions/game
      Average team    : ~18 tackles + 11 interceptions = ~29 def actions/game
      High-press team : ~22 tackles + 14 interceptions = ~36 def actions/game
      Elite press     : ~26 tackles + 16 interceptions = ~42+ def actions/game

    ─── FALLBACK SIGNAL (when tackles data not yet cached) ─────────────────────
    Opponent's avg possession % and total passes per game (possession-based signal).
    Possession thresholds: 50% = neutral, 70% = dominant.

    ─── MULTIPLIER CAP ─────────────────────────────────────────────────────────
    Capped at 10% max reduction (multiplier floor = 0.90), NOT 20%.
    This is independent of match dominance (which handles possession imbalance).
    Pressing = disruption/turnovers. Dominance = ball-time. They are additive
    but the pressing effect is smaller and more marginal — hence the 10% cap.

    Returns:
      score                 : 0.0 → 1.0
      multiplier            : 1.0 → 0.90 (max 10% reduction)
      label                 : "Low" / "Moderate" / "High" / "Elite"
      signal_used           : "tackles" or "possession"
      avg_defensive_actions : tackles + interceptions per game (if tackles used)
      avg_tackles           : opponent avg tackles / game
      avg_interceptions     : opponent avg interceptions / game
      avg_poss              : opponent avg possession % (if possession used)
      avg_passes            : opponent avg total passes / game
    """
    unknown = {
        "score": 0.0, "multiplier": 1.0, "label": "Unknown",
        "signal_used": None,
        "avg_defensive_actions": None, "avg_tackles": None, "avg_interceptions": None,
        "avg_poss": None, "avg_passes": None,
    }
    if not opp_fixture_stats or len(opp_fixture_stats) < 2:
        return unknown

    # ── PRIMARY: tackles + interceptions from /fixtures/players aggregation ──
    tackles       = [s.get("tackles_total")         for s in opp_fixture_stats if s.get("tackles_total")         is not None]
    interceptions = [s.get("tackles_interceptions")  for s in opp_fixture_stats if s.get("tackles_interceptions") is not None]

    if len(tackles) >= 2:
        avg_tkl = sum(tackles) / len(tackles)
        avg_int = sum(interceptions) / len(interceptions) if len(interceptions) >= 2 else 10.0
        avg_da  = avg_tkl + avg_int
        # Baseline 22 def-actions/game (low press) → 42+ = elite (score=1.0)
        score = round(max(0.0, min(1.0, (avg_da - 22) / 20)), 3)
        multiplier = round(max(0.90, 1.0 - score * 0.10), 3)
        if score < 0.20:
            label = "Low"
        elif score < 0.45:
            label = "Moderate"
        elif score < 0.70:
            label = "High"
        else:
            label = "Elite"
        return {
            "score":                 score,
            "multiplier":            multiplier,
            "label":                 label,
            "signal_used":           "tackles",
            "avg_defensive_actions": round(avg_da, 1),
            "avg_tackles":           round(avg_tkl, 1),
            "avg_interceptions":     round(avg_int, 1),
            "avg_poss":              None,
            "avg_passes":            None,
        }

    # ── FALLBACK: possession % + total passes ───────────────────────────────
    raw_poss = []
    for s in opp_fixture_stats:
        p = s.get("possession")
        if p is None:
            continue
        if isinstance(p, str):
            p = p.replace("%", "").strip()
            try:
                p = float(p)
            except ValueError:
                continue
        try:
            raw_poss.append(float(p))
        except (TypeError, ValueError):
            continue

    passes = [s.get("totalPasses") for s in opp_fixture_stats if s.get("totalPasses") is not None]

    if len(raw_poss) < 2 and len(passes) < 2:
        return unknown

    avg_poss   = sum(raw_poss) / len(raw_poss) if len(raw_poss) >= 2 else None
    avg_passes = sum(passes)   / len(passes)   if len(passes)   >= 2 else None

    poss_score = max(0.0, min(1.0, (avg_poss - 50) / 20))   if avg_poss   is not None else None
    pass_score = max(0.0, min(1.0, (avg_passes - 450) / 200)) if avg_passes is not None else None

    if poss_score is not None and pass_score is not None:
        score = poss_score * 0.70 + pass_score * 0.30
    elif poss_score is not None:
        score = poss_score
    else:
        score = pass_score

    score = round(max(0.0, min(1.0, score)), 3)
    multiplier = round(max(0.90, 1.0 - score * 0.10), 3)

    if score < 0.20:
        label = "Low"
    elif score < 0.45:
        label = "Moderate"
    elif score < 0.70:
        label = "High"
    else:
        label = "Elite"

    return {
        "score":                 score,
        "multiplier":            multiplier,
        "label":                 label,
        "signal_used":           "possession",
        "avg_defensive_actions": None,
        "avg_tackles":           None,
        "avg_interceptions":     None,
        "avg_poss":              round(avg_poss, 1)   if avg_poss   is not None else None,
        "avg_passes":            round(avg_passes, 1) if avg_passes is not None else None,
    }


def _estimate_opponent_concession(opp_fixture_stats: list, prop_type: str) -> Optional[float]:
    """Estimate how much of a stat type the opponent typically concedes.
    
    Each prop has a specific opponent stat and player share:
    - pass_attempts: uses opponent's total passes → player gets ~18% of team total
    - shots: uses opponent's total shots → player gets ~18% of team total
    - shots_on_target: uses opponent's SOT → player gets ~18% of team total
    - saves: uses opponent's SOT → GK faces ~100% of SOT, saves ~70%
    - tackles: uses opponent's total passes → more opp passes = more tackles needed → player ~15%
    - key_passes: uses opponent's total passes → player gets ~12% of team key passes
    """
    if not opp_fixture_stats:
        return None

    # (stat_field_from_opponent_data, player_share_of_that_stat)
    #
    # IMPORTANT: pass_attempts, passes, key_passes, crosses, dribbles are intentionally
    # EXCLUDED. Their "opponent concession" relies on totalPasses — but opponent totalPasses
    # measures THEIR possession volume, which is INVERSELY correlated with how many passes
    # the attacking player gets (more opp passes = opp has ball = fewer passes for attacker).
    # Including these was boosting projections against Arsenal/Bayern/City by +5-10 — wrong.
    # Possession-based penalties for these props are handled by the POSSESSION SQUEEZE step.
    prop_config = {
        "shots":           ("totalShots",    0.18),
        "shots_on_target": ("shotsOnTarget", 0.18),
        "saves":           ("shotsOnTarget", 0.70),   # GK saves ~70% of opponent SOT
        "tackles":         ("totalPasses",   0.015),  # more opp passes = more tackle opportunities
    }

    config = prop_config.get(prop_type)
    if not config:
        return None

    field, share = config
    values = [s.get(field) for s in opp_fixture_stats if s.get(field) is not None]
    if not values or len(values) < 2:
        return None

    avg = sum(values) / len(values)
    return round(avg * share, 1)
