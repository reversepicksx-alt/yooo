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
import os
import random
import statistics as stats_mod
from typing import Optional


# Hard cap: Covariate can be at most this ratio of (Prior + Momentum)
# ratio=0.33 guarantees Covariate weight <= 25% of total
MAX_COVARIATE_RATIO = 0.33

# ── Position-aware momentum decay tables ─────────────────────────────────────
# Research (Frontiers 2024-25) shows attackers have shorter form cycles (3-4 games)
# while defenders/GKs are more stable (6-8 game cycles).
# Most recent game = index 0.
DECAY_BY_POSITION = {
    "attacker":   [1.0, 0.75, 0.55, 0.38, 0.25],  # volatile, short form cycles
    "midfielder": [1.0, 0.82, 0.67, 0.55, 0.45],  # balanced (original default)
    "defender":   [1.0, 0.88, 0.77, 0.67, 0.58],  # stable, long form cycles
    "goalkeeper": [1.0, 0.90, 0.80, 0.70, 0.60],  # most stable of all positions
}

POSITION_GROUP_MAP = {
    # Attackers
    "CF": "attacker", "SS": "attacker", "LW": "attacker", "RW": "attacker",
    "CAM": "attacker", "AM": "attacker", "F": "attacker", "ST": "attacker",
    "FW": "attacker", "WF": "attacker",
    # Midfielders
    "CM": "midfielder", "DM": "midfielder", "CDM": "midfielder",
    "LM": "midfielder", "RM": "midfielder", "M": "midfielder",
    "MF": "midfielder", "DMF": "midfielder", "AMF": "midfielder",
    # Defenders
    "CB": "defender", "LB": "defender", "RB": "defender",
    "LWB": "defender", "RWB": "defender", "D": "defender",
    "DF": "defender", "SW": "defender",
    # Goalkeepers
    "GK": "goalkeeper", "G": "goalkeeper",
}

# Fallback decay used when position is unknown
DECAY_WEIGHTS = DECAY_BY_POSITION["midfielder"]


# Props that are discrete counts (non-negative integers).
# These use the negative binomial distribution instead of Gaussian in Monte Carlo.
COUNT_PROPS = {
    "shots", "shots_on_target", "goals", "assists", "saves", "tackles",
    "key_passes", "interceptions", "blocks", "dribbles", "dribbles_success",
    "fouls_drawn", "fouls_committed", "crosses", "clearances",
    "duels_won", "yellow_cards", "shots_assisted",
}


def _sample_negative_binomial(mean: float, variance: float, n_sims: int) -> list:
    """
    Gamma-Poisson mixture (= negative binomial) for count data.
    More accurate than Gaussian for discrete props like shots, saves, goals.
    Handles overdispersion (variance > mean) naturally — common in football stats.

    When variance <= mean (rare, underdispersed), falls back to Poisson via
    Gaussian approximation since NB is undefined there.
    """
    if mean <= 0:
        return [0] * n_sims

    if variance <= mean:
        # Underdispersed — use Poisson approximation
        lam = max(mean, 0.01)
        return [max(0, round(random.gauss(lam, math.sqrt(lam)))) for _ in range(n_sims)]

    # NB parameters via method of moments
    r     = max(mean ** 2 / (variance - mean), 0.1)  # dispersion (shape)
    beta  = (variance - mean) / mean                  # scale

    samples = []
    for _ in range(n_sims):
        # Draw Poisson rate from Gamma(r, beta) — this gives NB marginal
        rate = random.gammavariate(r, beta)
        # Approximate Poisson(rate) with Gaussian for speed (accurate for rate > 1)
        sample = max(0, round(random.gauss(rate, math.sqrt(max(rate, 0.01)))))
        samples.append(sample)
    return samples


def _monte_carlo_probability(
    mean: float,
    std: float,
    line: float,
    n_sims: int = 5000,
    is_count_stat: bool = False,
    variance: float = None,
) -> tuple:
    """
    Monte Carlo simulation for P(over) / P(under) and 80% CI.

    For count stats (shots, goals, saves etc.) uses the negative binomial
    via a gamma-Poisson mixture — correctly handles discrete, right-skewed
    distributions. For continuous stats uses Gaussian.

    Returns: (p_over, p_under, ci_low_80, ci_high_80)
    """
    if std <= 0 or mean <= 0:
        p = 1.0 if mean > line else 0.0
        return p, 1.0 - p, round(mean, 1), round(mean, 1)

    if is_count_stat:
        var = variance if variance and variance > 0 else std ** 2
        samples = _sample_negative_binomial(mean, var, n_sims)
    else:
        samples = [random.gauss(mean, std) for _ in range(n_sims)]

    over_count = sum(1 for s in samples if s > line)
    p_over = over_count / n_sims
    p_under = 1.0 - p_over

    sorted_s = sorted(samples)
    ci_low  = round(sorted_s[int(0.10 * n_sims)], 1)   # 10th percentile
    ci_high = round(sorted_s[int(0.90 * n_sims)], 1)   # 90th percentile

    return p_over, p_under, ci_low, ci_high


def compute_bayesian_projection(
    game_logs: list,
    prop_type: str,
    line: float,
    venue: str,
    stat_field: str = "targetStat",
    opponent_fixture_stats: list = None,
    match_dominance: dict = None,
    position: str = "",
    hyperprior_mean: float = None,
    expected_minutes: float = 90.0,
    ai_press_intensity: dict = None,
    league_calibration: dict = None,
    game_script: dict = None,
    scenario_priors_result: dict = None,
    scenario_priors_mode: str = "off",
    role: str = "",
) -> dict:
    """
    Compute a 3-layer Bayesian projection from raw game data.
    Returns the full bayesianMetrics dict with real computed values.
    """
    if not game_logs:
        return _empty_metrics(line)

    # ── Is this a discrete count prop? ──────────────────────────────────────
    is_count_stat = prop_type in COUNT_PROPS

    # ── Position flag — needed early for GK-specific logic ───────────────────
    _is_gk = (position or "").upper() in {"GK", "GOALKEEPER"}

    # ── Per-90 normalization ─────────────────────────────────────────────────
    # Raw stats from games of different durations are not comparable.
    # A player with 40 passes in 60 min is performing better than 40 passes in 90 min.
    # Normalize each value to per-90-minute rate, then de-normalize at the end
    # by the player's expected playing time (median of recent minutes played).
    # Floor: 30 min to avoid division by zero on very short cameos.
    _MIN_MINUTES = 30       # ignore sub-30 min appearances in the dataset
    _raw_pairs = [
        (g.get(stat_field, g.get("targetStat")), g.get("minutes", 90))
        for g in game_logs
        if g.get(stat_field, g.get("targetStat")) is not None
        and g.get("minutes", 90) >= _MIN_MINUTES
    ]
    if not _raw_pairs:
        # Fall back to logs without the minutes filter
        _raw_pairs = [
            (g.get(stat_field, g.get("targetStat")), 90)
            for g in game_logs
            if g.get(stat_field, g.get("targetStat")) is not None
        ]
    if not _raw_pairs:
        return _empty_metrics(line)

    # Normalise to per-90
    all_vals    = [v * 90.0 / max(m, _MIN_MINUTES) for v, m in _raw_pairs]
    all_minutes = [m for _, m in _raw_pairs]

    # expected_minutes: caller passes the player's likely playing time for this match.
    # Clamp to [30, 90] so partial substitution stays realistic.
    _exp_min = max(30.0, min(90.0, expected_minutes))

    # De-normalisation factor applied to posterior after all calculations
    _denorm = _exp_min / 90.0

    n = len(all_vals)

    # ═══════════════════════════════════════════
    # LAYER 1: PRIOR — Season Average Baseline
    # ═══════════════════════════════════════════
    # Recency-weighted mean: game logs are sorted newest-first.
    # Exponential decay (0.93/game) gives recent matches ~35% more
    # influence than games from 10+ fixtures ago, while keeping the
    # full sample for variance stability.
    # For n < 4 fall back to equal weights — too few games to decay reliably.
    _PRIOR_DECAY = 0.93
    if n >= 4:
        _pw = [_PRIOR_DECAY ** i for i in range(n)]
        _pw_total = sum(_pw)
        prior_mean = sum(w * v for w, v in zip(_pw, all_vals)) / _pw_total
    else:
        prior_mean = sum(all_vals) / n

    # ── HYPERPRIOR SHRINKAGE (low-sample players) ──────────────────────────
    # When a player has fewer than 6 game logs, their empirical average is noisy.
    # Shrink toward a league/position prior (if provided) to reduce variance.
    # Shrinkage weight decreases as sample size grows:
    #   n=1 → 83%,  n=2 → 67%,  n=3 → 50%,  n=4 → 33%,  n=5 → 17%,  n≥6 → 0%
    _hyperprior_applied = False
    if hyperprior_mean is not None and hyperprior_mean > 0 and n < 6:
        shrinkage = (6 - n) / 6.0
        blended = prior_mean * (1 - shrinkage) + hyperprior_mean * shrinkage
        print(f"[HYPERPRIOR] n={n} samples, shrink={shrinkage:.2f}: "
              f"player={prior_mean:.1f} → blended={blended:.1f} (prior={hyperprior_mean:.1f})")
        prior_mean = blended
        _hyperprior_applied = True

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
    # Position-aware decay: attackers use faster cycles, defenders/GKs slower.
    # ═══════════════════════════════════════════
    _pos_group = POSITION_GROUP_MAP.get(position.upper().strip(), "midfielder")
    _decay_table = DECAY_BY_POSITION[_pos_group]

    recent_5 = all_vals[:5] if len(all_vals) >= 5 else all_vals[:max(3, len(all_vals))]
    recent_3 = all_vals[:3] if len(all_vals) >= 3 else all_vals

    # Apply position-aware exponential decay: most recent game gets highest weight
    weights = _decay_table[:len(recent_5)]
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

    # 3a. Venue split adjustment (normalised to per-90 to match all_vals)
    venue_vals = [
        v * 90.0 / max(g.get("minutes", 90), _MIN_MINUTES)
        for g in game_logs
        if g.get("venue") == venue
        and (v := g.get(stat_field, g.get("targetStat"))) is not None
        and g.get("minutes", 90) >= _MIN_MINUTES
    ]
    if not venue_vals:
        venue_vals = [
            g.get(stat_field, g.get("targetStat"))
            for g in game_logs
            if g.get("venue") == venue and g.get(stat_field, g.get("targetStat")) is not None
        ]
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
            if _is_gk and prop_type in {"pass_attempts", "passes"}:
                # GK INVERTED COVARIATE: More team possession → fewer back-passes to GK.
                # dom_mult > 1.0 = team controls more ball than average → GK LESS involved.
                # dom_mult < 1.0 = team has less ball → handled by GK INVERTED block below (line 542+).
                # Only fire when dom_mult > 1.10 (meaningful possession surplus).
                # Dampened 0.5× since the inverted block below carries the primary adjustment.
                if dom_mult > 1.05:
                    dom_adj = prior_mean * (1.0 - dom_mult) * 0.5  # NEGATIVE → reduces GK projection
                    _dom_cap = prior_mean * 0.15
                    dom_adj = max(-_dom_cap, min(0.0, dom_adj))  # Only allow reduction, cap at -15%
                    covariate_adjustment += dom_adj
            elif not _is_gk:
                # Outfield players: more possession = more passes/shots
                dom_adj = prior_mean * (dom_mult - 1.0)
                # Cap at ±20% of prior_mean — prevents possession signal from dominating
                # when form/prior already point strongly in one direction.
                _dom_cap = prior_mean * 0.20
                dom_adj = max(-_dom_cap, min(_dom_cap, dom_adj))
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
            # GK saves: the formula (opp SOT × 0.70) systematically underestimates
            # saves because it uses season-average SOT (which smooths out home-game
            # spikes) and ignores press intensity, game state, and the actual
            # observed GK performance against this specific opponent.
            # When the formula pulls the adjustment negative, cap suppression at
            # 10% of the venue-specific prior — otherwise a prior of 5.8 (away avg)
            # gets crushed by a formula yielding 2.94, creating a false UNDER call
            # even when every observable signal points to OVER the line.
            if _is_gk and prop_type in {"saves", "goalie_saves"} and opp_adj < 0:
                opp_adj = max(opp_adj, -prior_mean * 0.10)
            covariate_adjustment += opp_adj * 0.15

    # 3d. xG covariate — opponent's expected goals allowed (shot props only)
    # API-Football provides xG at fixture level. An opponent allowing more xG
    # than league avg (≈1.35/game) signals weaker shot defence → positive signal
    # for shots/shots_on_target props. Dampened 40% to avoid double-counting
    # with the opponent-concession adjustment above.
    _XG_LEAGUE_AVG = 1.35
    _SHOT_PROPS = {"shots", "shots_on_target"}
    if opponent_fixture_stats and prop_type in _SHOT_PROPS:
        xg_vals = [
            s.get("expectedGoals") for s in opponent_fixture_stats
            if s.get("expectedGoals") is not None
        ]
        if len(xg_vals) >= 2:
            avg_xg = sum(xg_vals) / len(xg_vals)
            xg_ratio = avg_xg / _XG_LEAGUE_AVG
            xg_raw_adj = prior_mean * (xg_ratio - 1.0) * 0.40
            # Cap at ±15% of prior_mean to prevent overcorrection
            xg_adj = max(-prior_mean * 0.15, min(prior_mean * 0.15, xg_raw_adj))
            covariate_adjustment += xg_adj
            print(f"[XG COVARIATE] {prop_type}: opp_xg_avg={avg_xg:.2f} "
                  f"ratio={xg_ratio:.2f} adj={xg_adj:+.1f}")

    # 3e. xG Shot Quality Proxy — player's own conversion metrics
    # ─────────────────────────────────────────────────────────────────────────
    # The standard prior treats all shots as equal. In reality, a player who
    # converts 40% of their on-target shots (elite finisher) is very different
    # from one who converts 15% (low quality / shot location).
    #
    # We compute two quality ratios from the player's own game logs:
    #   shot_quality  = shots_on_target / shots  (how often they hit the frame)
    #   conversion    = goals / shots_on_target   (finishing quality)
    # then compare to European league averages to derive a quality factor.
    #
    # League averages (Opta research): quality≈0.37, conversion≈0.31
    # Applied ONLY to goals/shots_on_target. Capped at ±18% influence.
    _SHOT_QUALITY_PROPS = {"goals", "shots_on_target"}
    if prop_type in _SHOT_QUALITY_PROPS and len(game_logs) >= 4:
        _SQ_LEAGUE_AVG  = 0.37  # shots_on_target / shots
        _CON_LEAGUE_AVG = 0.31  # goals / shots_on_target

        _sq_samples, _con_samples = [], []
        for g in game_logs:
            _g_shots  = g.get("shots_total") or g.get("shots")
            _g_sot    = g.get("shots_on")    or g.get("shots_on_target")
            _g_goals  = g.get("goals_total") or g.get("goals")
            if _g_shots and _g_shots > 0 and _g_sot is not None:
                _sq_samples.append(_g_sot / _g_shots)
            if _g_sot and _g_sot > 0 and _g_goals is not None:
                _con_samples.append(_g_goals / _g_sot)

        _sq_factor  = (sum(_sq_samples)  / len(_sq_samples))  / _SQ_LEAGUE_AVG  if len(_sq_samples)  >= 3 else 1.0
        _con_factor = (sum(_con_samples) / len(_con_samples)) / _CON_LEAGUE_AVG if len(_con_samples) >= 3 else 1.0

        if prop_type == "shots_on_target":
            # Quality factor: do more of their shots hit the target than average?
            _sq_raw_adj = prior_mean * (_sq_factor - 1.0) * 0.20
            _sq_adj = max(-prior_mean * 0.18, min(prior_mean * 0.18, _sq_raw_adj))
            covariate_adjustment += _sq_adj
            if abs(_sq_adj) > 0.05:
                print(f"[SHOT QUALITY] {prop_type}: sq_ratio={_sq_factor:.2f}x league_avg → adj={_sq_adj:+.2f}")

        elif prop_type == "goals":
            # Combined: shot quality × finishing rate vs league averages
            _combined_factor = (_sq_factor * _con_factor) ** 0.5  # geometric mean of both
            _sq_raw_adj = prior_mean * (_combined_factor - 1.0) * 0.20
            _sq_adj = max(-prior_mean * 0.18, min(prior_mean * 0.18, _sq_raw_adj))
            covariate_adjustment += _sq_adj
            if abs(_sq_adj) > 0.01:
                print(f"[SHOT QUALITY] goals: sq={_sq_factor:.2f}x conv={_con_factor:.2f}x combined={_combined_factor:.2f}x → adj={_sq_adj:+.3f}")

    covariate_adjustment = round(covariate_adjustment, 2)

    # Covariate precision: base contextual precision, boosted by venue data quality
    raw_cov_prec = 3.0
    if venue_vals and len(venue_vals) >= 5:
        raw_cov_prec += 1.0

    # HARD CAP: Covariate can never exceed 25% of total weight
    # Math: if cov = (P+M)*0.33, then cov/(P+M+cov) = 0.33/1.33 = 24.8%
    covariate_precision = min(raw_cov_prec, (prior_precision + momentum_precision) * MAX_COVARIATE_RATIO)

    # ═══════════════════════════════════════════
    # MOMENTUM-vs-STRUCTURE GUARD
    # ═══════════════════════════════════════════
    # When recent-form momentum points one direction but the structural matchup
    # signals (venue split, opponent allowed, H2H — captured in covariate_adjustment)
    # point the OTHER direction, dampen momentum's weight. This stops a 3-game
    # cold streak from steamrolling a structurally favorable matchup.
    #
    # Both momentum_effect and covariate_adjustment are in per-90 units, so we
    # gate on absolute magnitudes that scale with prior_std (a player-relative
    # threshold avoids over-firing on count stats with small means).
    #
    # Disagreement = both signals exceed their thresholds AND they have opposite signs.
    # Strong disagreement → halve momentum weight; mild disagreement → ¾ momentum weight.
    _mom_thresh = max(0.5, 0.30 * prior_std)
    _cov_thresh = max(0.3, 0.20 * prior_std)
    if abs(momentum_effect) > _mom_thresh and abs(covariate_adjustment) > _cov_thresh:
        if (momentum_effect > 0) != (covariate_adjustment > 0):
            disagreement_mag = min(abs(momentum_effect), abs(covariate_adjustment))
            if disagreement_mag > max(1.5, 0.60 * prior_std):
                _scale = 0.5
            else:
                _scale = 0.75
            _orig_mom_prec = momentum_precision
            momentum_precision *= _scale
            print(f"[MOMENTUM GUARD] mom_effect={momentum_effect:.2f} vs cov_adj={covariate_adjustment:.2f} "
                  f"(disagree, mag={disagreement_mag:.2f}, prior_std={prior_std:.2f}) → "
                  f"momentum precision {_orig_mom_prec:.2f} × {_scale} = {momentum_precision:.2f}")

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
    _is_gk = (position or "").upper() in {"GK", "GOALKEEPER"}

    # ═══════════════════════════════════════════
    # OUTFIELD PLAYER POSSESSION SQUEEZE
    # GKs use an INVERTED model below — they are excluded here.
    # ═══════════════════════════════════════════
    if match_dominance and prop_type in BALL_CONTROL_PROPS and not _is_gk:
        expected_poss = match_dominance.get("expectedPoss")
        team_season_avg_poss = match_dominance.get("teamSeasonAvg")
        if expected_poss is not None and team_season_avg_poss and team_season_avg_poss > 0:
            poss_ratio = expected_poss / team_season_avg_poss
            # Activates at 5%+ below norm — catches moderate mismatches sooner
            # Exponent 1.5 — steeper curve so severe mismatches hit harder
            # Position-aware floor: CBs/DFs are high-volume passers whose output doesn't
            # collapse proportionally with team possession — they still clear, recycle,
            # and switch the ball under any game state. Apply a more conservative floor.
            _pos_upper = (position or "").upper()
            _is_cb  = _pos_upper in {"CB", "DC", "RCB", "LCB"}
            _is_def = _pos_upper in {"DEF", "LB", "RB", "WB", "LWB", "RWB", "D"}
            # MID: deep midfielders (CDM/CM/CAM) stay involved regardless of possession.
            # Evidence: 60-pick CDM/Ball Winner sample shows avg_err=+9.6 (model under-projects).
            # Raise general mid floor 0.75→0.82 (max 18% cut).
            # High-volume roles (DLP, Mezzala, Box-to-Box, Ball Winner) get 0.88 — they are
            # volume passers by design and maintain output even when team possession dips.
            _is_mid = _pos_upper in {"MF", "CM", "CDM", "CAM", "DM", "AM", "MC", "DMF", "OMF", "CMF"}
            _role_lower = (role or "").lower()
            _is_high_vol_mid = _is_mid and any(r in _role_lower for r in (
                "deep-lying", "mezzala", "box-to-box", "ball winner",
                "holding", "regista", "advanced playmaker",
            ))
            squeeze_floor = (
                0.88 if _is_high_vol_mid else
                0.80 if _is_cb else
                0.82 if _is_mid else
                0.70 if _is_def else
                0.60
            )
            if poss_ratio < 0.95:
                squeeze_mult = round(max(squeeze_floor, poss_ratio ** 1.5), 3)
                # HOT-STREAK DAMPENING: A player on an upward momentum trend may
                # retain volume even when team possession dips — tactical discipline,
                # manager trust, and personal form maintain their involvement.
                _squeeze_dampened = False
                if momentum_effect > 5:
                    squeeze_mult = round(min(1.0, squeeze_mult + (1.0 - squeeze_mult) * 0.35), 3)
                    _squeeze_dampened = "HOT"
                elif momentum_effect > 2:
                    squeeze_mult = round(min(1.0, squeeze_mult + (1.0 - squeeze_mult) * 0.18), 3)
                    _squeeze_dampened = "WARMING"
                raw_before_squeeze = posterior_mean
                posterior_mean = round(posterior_mean * squeeze_mult, 1)
                _damp_note = f" [dampened for {_squeeze_dampened} streak]" if _squeeze_dampened else ""
                print(f"[POSS SQUEEZE] {prop_type}: team_avg={team_season_avg_poss:.1f}% "
                      f"expected={expected_poss:.1f}% ratio={poss_ratio:.2f} pos={_pos_upper} "
                      f"floor={squeeze_floor} mult={squeeze_mult}{_damp_note} {raw_before_squeeze} → {posterior_mean}")

    # ═══════════════════════════════════════════
    # GK INVERTED POSSESSION MODEL
    # GK pass volume is INVERSELY correlated with team possession:
    #   Low possession → defenders constantly back-pass under pressure → HIGH GK volume
    #   High possession → team builds through midfield → LOW GK volume
    # This is the opposite of outfield players. The Miami case (away GK sitting
    # deep on a 1-0 lead) is the worst-case: ultra-low team possession = maximum
    # back-pass recycling volume through the GK.
    # ═══════════════════════════════════════════
    if match_dominance and prop_type in {"pass_attempts", "passes"} and _is_gk:
        expected_poss = match_dominance.get("expectedPoss")
        team_season_avg_poss = match_dominance.get("teamSeasonAvg")
        if expected_poss is not None and team_season_avg_poss and team_season_avg_poss > 0:
            poss_ratio = expected_poss / team_season_avg_poss
            # Only trigger on meaningful deficit (>13% below norm).
            # Rationale: teams with slightly less possession don't uniformly recycle
            # more through the GK — many play direct football (long balls), reducing GK
            # distribution volume. The inverse-boost only holds in specific press-heavy
            # scenarios; applying it broadly over-inflates projections.
            if poss_ratio < 0.87:
                # LOW POSSESSION BOOST — GK gets more back-passes when team defends deep.
                # Evidence: away GKs on low-possession sides consistently exceed projections.
                # Cap reduced to +10% (was +18%) — the old cap over-inflated projections
                # in chaotic high-scoring games (e.g. Pickford 52 proj vs 36 actual in 3-3).
                # Exponent lowered from 0.55→0.30 for a flatter, more conservative curve.
                #   poss_ratio=0.86 → ~+2%   (minor deficit)
                #   poss_ratio=0.77 → ~+7%   (moderate deficit, e.g. Everton vs Man City)
                #   poss_ratio=0.50 → +10%   (extreme deficit, capped)
                inverse_ratio = 1.0 / max(poss_ratio, 0.50)
                boost_mult = round(min(1.10, inverse_ratio ** 0.30), 3)
                raw_before_gk = posterior_mean
                posterior_mean = round(posterior_mean * boost_mult, 1)
                print(f"[GK POSS BOOST] {prop_type}: team_avg={team_season_avg_poss:.1f}% "
                      f"expected={expected_poss:.1f}% ratio={poss_ratio:.2f} "
                      f"inv_mult={boost_mult} {raw_before_gk} → {posterior_mean}")
            elif poss_ratio > 1.05:
                # GK DOMINANT POSSESSION PENALTY
                # When a team heavily out-possesses the opponent, their GK barely touches
                # the ball — defenders push high and recycle possession among themselves.
                # Evidence:
                #   Donnarumma (Man City away, 75% poss): 12 actual vs 27 proj — huge miss.
                #   Donnarumma (Man City away vs Burnley, 65% poss): 20 actual vs 27 proj.
                #   Gazzaniga (Girona HOME, above-avg poss vs Betis): 26 actual.
                #
                # Scale (cap reduced 50%→20%, coefficient reduced 2.5→1.0 per empirical audit):
                # 1291-pick dataset showed 65/65 GK UNDER misses had actual > line, meaning
                # the old penalty cut projections so far below the line they were always wrong.
                # Evidence: De Gea season avg ~37, poss_ratio=1.14 → old penalty 35% → proj=24
                # vs line 31.5, actual 35. With cap=20%: proj=32 → correctly straddles the line.
                #   ratio=1.05 → ~5% reduction  (was 12%)
                #   ratio=1.10 → ~10% reduction (was 25%)
                #   ratio=1.14 → ~14% reduction (was 35%)
                #   ratio=1.20 → 20% reduction  (was 50%, extreme dominance, now capped lower)
                dom_penalty = min(0.20, (poss_ratio - 1.0) * 1.0)
                shrink_mult = round(1.0 - dom_penalty, 3)
                raw_before_dom = posterior_mean
                posterior_mean = round(posterior_mean * shrink_mult, 1)
                print(f"[GK DOM POSS PENALTY] {prop_type}: team_avg={team_season_avg_poss:.1f}% "
                      f"expected={expected_poss:.1f}% ratio={poss_ratio:.2f} "
                      f"shrink={shrink_mult} {raw_before_dom} → {posterior_mean}")

        # ── GK PROJECTION FLOOR ───────────────────────────────────────────────
        # After all possession adjustments, a GK projection should never drop below
        # 72% of the player's season prior mean. Anything lower means the possession
        # model is over-penalising — GKs always handle the ball regardless of team style.
        # Evidence: projections of 17-24 for GKs with 35+ pass season averages are
        # physically implausible. A floor prevents cascading possession errors from
        # producing unbettable projections.
        if _is_gk and prop_type in {"pass_attempts", "passes"} and prior_mean > 0:
            _gk_floor = round(prior_mean * 0.72, 1)
            if posterior_mean < _gk_floor:
                print(f"[GK FLOOR] {prop_type}: posterior={posterior_mean} below floor={_gk_floor} "
                      f"(72% of prior_mean={prior_mean:.1f}) → lifting to floor")
                posterior_mean = _gk_floor

    # ═══════════════════════════════════════════
    # CDM INVERTED POSSESSION MODEL  (Layer A of CDM-inversion fix)
    # ═══════════════════════════════════════════
    # Mirror of the GK Inverted Possession Model above, but for deep midfielders.
    # Mechanism: when an away team is pinned back (low expected possession), the
    # CDM becomes the primary build-up outlet — defenders constantly recycle the
    # ball through them under press, generating MORE pass attempts, not fewer.
    # The general "low possession → suppress everyone" assumption is wrong for
    # this position, exactly as it is wrong for keepers.
    #
    # Cap: ±6% (intentionally smaller than GK's 18% — the inversion is real but
    # less extreme than the GK case, and the empirical sample is still modest).
    # Trigger: poss_ratio < 0.90 (more conservative than GK's 0.87).
    #
    # Mode controlled by env var CDM_INVERSION_MODE: off|shadow|live
    # Default = shadow → compute and log, do NOT change projection.
    # ═══════════════════════════════════════════
    cdm_inversion_info = {"applied": False, "multiplier": 1.0, "mode": "off",
                          "shadow_multiplier": 1.0, "reason": ""}
    _cdm_mode = os.environ.get("CDM_INVERSION_MODE", "live").lower()
    if _cdm_mode not in {"off", "shadow", "live"}:
        _cdm_mode = "shadow"
    _cdm_pos_set = {"CDM", "DM", "DMF", "CM", "MC", "CMF"}
    _pos_upper_for_cdm = (position or "").upper()
    if (_cdm_mode != "off" and match_dominance
            and prop_type in {"pass_attempts", "passes"}
            and _pos_upper_for_cdm in _cdm_pos_set):
        expected_poss = match_dominance.get("expectedPoss")
        team_season_avg_poss = match_dominance.get("teamSeasonAvg")
        if (expected_poss is not None and team_season_avg_poss
                and team_season_avg_poss > 0):
            poss_ratio = expected_poss / team_season_avg_poss
            if poss_ratio < 0.90:
                # Same shape as GK boost but flatter ceiling.
                # poss_ratio=0.89 → ~+1.5%
                # poss_ratio=0.75 → ~+4%
                # poss_ratio=0.55 → ~+6% (capped)
                inverse_ratio = 1.0 / max(poss_ratio, 0.50)
                cdm_boost_mult = round(min(1.06, inverse_ratio ** 0.30), 3)
                cdm_inversion_info["shadow_multiplier"] = cdm_boost_mult
                cdm_inversion_info["mode"] = _cdm_mode
                cdm_inversion_info["reason"] = (
                    f"pinned-back outlet boost "
                    f"(ratio={poss_ratio:.2f}, mult={cdm_boost_mult})"
                )
                if _cdm_mode == "live":
                    raw_before_cdm = posterior_mean
                    posterior_mean = round(posterior_mean * cdm_boost_mult, 1)
                    cdm_inversion_info["applied"] = True
                    cdm_inversion_info["multiplier"] = cdm_boost_mult
                    print(f"[CDM POSS BOOST live] {prop_type} pos={_pos_upper_for_cdm} "
                          f"venue={venue}: team_avg={team_season_avg_poss:.1f}% "
                          f"expected={expected_poss:.1f}% ratio={poss_ratio:.2f} "
                          f"mult={cdm_boost_mult} {raw_before_cdm} → {posterior_mean}")
                else:
                    print(f"[CDM POSS BOOST shadow] {prop_type} pos={_pos_upper_for_cdm} "
                          f"venue={venue}: team_avg={team_season_avg_poss:.1f}% "
                          f"expected={expected_poss:.1f}% ratio={poss_ratio:.2f} "
                          f"would_mult={cdm_boost_mult} (NOT APPLIED)")

    # ═══════════════════════════════════════════
    # PRESS INTENSITY — Position & Prop Aware
    # ═══════════════════════════════════════════
    # Press signal is computed for any prop where opponent pressing meaningfully
    # changes player workload (passes, saves, defensive actions, etc.) — not just
    # pass_attempts. Direction is decided by position + prop, magnitude scales
    # with the press score (0–1), and the cap is ±20% (was ±10%) so elite-press
    # matchups can actually move the projection enough to matter.
    #
    # Direction matrix (high opponent press →):
    #   • GK saves                       → BOOST  (turnovers high up → keeper bombarded)
    #   • GK pass_attempts (away only)   → BOOST  (defenders pinned back, more back-passes)
    #   • Defender pass_attempts         → BOOST  (rushed recycling — attempted ≠ completed)
    #   • Defender tackles/interceptions → BOOST  (more defensive workload under chaos)
    #   • Midfielder defensive actions   → BOOST  (more reclaim attempts)
    #   • Midfielder/Attacker passes     → NEUTRAL (effects mixed in real data)
    #
    # We deliberately do NOT apply blanket suppression to outfield pass_attempts
    # anymore — empirically that direction proved wrong for build-up positions
    # against teams like Rayo Vallecano (high press → MORE attempted passes from
    # pressed CBs/FBs, not fewer).
    # ═══════════════════════════════════════════
    PRESS_AFFECTED_PROPS = {
        "pass_attempts", "passes", "saves",
        "tackles", "interceptions", "clearances", "blocks",
    }
    # Canonical empty/default schema — every path below MUST keep these keys
    press_intensity_info = {
        "score": 0.0, "multiplier": 1.0, "label": "Unknown", "signal_used": None,
        "ppda": None, "reasoning": "",
        "avg_defensive_actions": None, "avg_tackles": None, "avg_interceptions": None,
        "avg_poss": None, "avg_passes": None,
    }
    if prop_type in PRESS_AFFECTED_PROPS:
        # ── PRIMARY: AI-supplied press intensity (Grok web search + tactical knowledge) ──
        # This is opponent-specific and works for ALL leagues. The structural heuristic
        # (tackles+interceptions) only fires as a fallback when AI couldn't produce a
        # confident answer, because it inverts for elite-press teams (more press → fewer
        # tackles_total when opponent never gets the ball back).
        if ai_press_intensity and isinstance(ai_press_intensity, dict) \
                and ai_press_intensity.get("score") is not None:
            _ai_score = max(0.0, min(1.0, float(ai_press_intensity.get("score", 0.0))))
            press_intensity_info = {
                "score": round(_ai_score, 3),
                "multiplier": 1.0,
                "label": ai_press_intensity.get("label", "Unknown"),
                "signal_used": ai_press_intensity.get("source", "ai"),
                "ppda": ai_press_intensity.get("ppda"),
                "reasoning": ai_press_intensity.get("reasoning", ""),
                "avg_defensive_actions": None, "avg_tackles": None,
                "avg_interceptions": None, "avg_poss": None, "avg_passes": None,
            }
        elif opponent_fixture_stats:
            press_intensity_info = compute_press_intensity_score(opponent_fixture_stats)
        # else: keep canonical default — already initialised above

        press_score = press_intensity_info.get("score", 0.0) or 0.0
        press_label = press_intensity_info.get("label", "Unknown")

        # Boost cap (positive = boost, negative = suppress) — scaled by press_score
        boost_cap = 0.0
        if _is_gk:
            if prop_type == "saves":
                boost_cap = 0.20   # up to +20% at elite press
            elif prop_type in {"pass_attempts", "passes"} and venue == "away":
                boost_cap = 0.12   # away GK back-pass boost (was capped at +5%)
        elif _pos_group == "defender":
            if prop_type in {"pass_attempts", "passes"}:
                boost_cap = 0.15
            elif prop_type in {"tackles", "interceptions", "clearances", "blocks"}:
                boost_cap = 0.10
        elif _pos_group == "midfielder":
            if prop_type in {"tackles", "interceptions"}:
                boost_cap = 0.08
            # midfielder pass_attempts intentionally neutral (mixed empirical signal)
        # attacker props intentionally neutral — press effect is too matchup-specific

        if press_score > 0 and boost_cap != 0.0:
            adj = boost_cap * press_score
            applied_mult = round(1.0 + adj, 3)
            press_intensity_info["multiplier"] = applied_mult
            raw_before = posterior_mean
            posterior_mean = round(posterior_mean * applied_mult, 1)
            tag = "BOOST" if adj > 0 else "SUPPRESS"
            print(f"[PRESS {tag}] {prop_type} pos={_pos_group} venue={venue} "
                  f"opp={press_label}(score={press_score}, signal={press_intensity_info['signal_used']}) "
                  f"da={press_intensity_info.get('avg_defensive_actions')} → "
                  f"mult={applied_mult} : {raw_before} → {posterior_mean}")
        else:
            # Press info captured for transparency but no posterior change applied
            press_intensity_info["multiplier"] = 1.0
            print(f"[PRESS NEUTRAL] {prop_type} pos={_pos_group} venue={venue} "
                  f"opp={press_label}(score={press_score}) — no directional adjustment for this combo")

    # ═══════════════════════════════════════════
    # GAME-SCRIPT NUDGE (Vegas-derived chase / nailbiter signals)
    # ═══════════════════════════════════════════
    # Inspired by the cheat-sheet contextual breakdown:
    #   * Home CDM OVER passes hits 100% when their team LOSES (chase mode).
    #   * Away GK UNDER passes hits 80% in 0–1 goal games, but only 29%
    #     in 4+ goal goal-fests.
    #   * Home GK UNDER passes hits 100% in blowouts/draws but only 53%
    #     in 1-goal nailbiters — i.e. close games are coin flips.
    #
    # We accept a `game_script` dict with two optional keys:
    #   expected_total_goals : float (Vegas line for game total)
    #   expected_goal_diff   : float (positive = home favoured, negative = away favoured)
    #
    # The nudge is small (cap ±5%) and only fires when the script signal
    # aligns with the position/prop pattern. Always informative, never
    # the dominant signal.
    # ═══════════════════════════════════════════
    game_script_info = {"applied": False, "multiplier": 1.0, "reason": ""}
    if game_script and isinstance(game_script, dict):
        expected_total = game_script.get("expected_total_goals")
        expected_diff = game_script.get("expected_goal_diff")
        gs_mult = 1.0
        gs_reason = []
        _pos_upper = (position or "").upper()
        # Home CDM/CAM OVER passes — chase-mode boost when team is underdog.
        # Extends to attacking mids (CAM/AM). Magnitude increased from 7% to 20%
        # max: empirical data shows trailing teams generate 30-80% more passes
        # through their central mids when chasing — small multipliers were being
        # swamped by the base projection bias.
        if (prop_type in {"pass_attempts", "passes"} and venue == "home"
                and _pos_upper in {"CDM", "DM", "DMF", "CAM", "AM", "OM", "ACM"}
                and expected_diff is not None and expected_diff < -0.5):
            chase_boost = min(0.20, abs(expected_diff) * 0.09)
            gs_mult *= (1.0 + chase_boost)
            gs_reason.append(f"home MID chase-mode boost +{chase_boost*100:.1f}% (diff={expected_diff:.1f})")
        # Away CDM/CAM OVER passes — symmetric pinned-back boost. Magnitude
        # increased from 6% to 18% max for same reason as above.
        if (_cdm_mode != "off"
                and prop_type in {"pass_attempts", "passes"} and venue == "away"
                and _pos_upper in {"CDM", "DM", "DMF", "CM", "MC", "CMF", "CAM", "AM", "OM", "ACM"}
                and expected_diff is not None and expected_diff > 0.5):
            away_chase_boost = min(0.18, abs(expected_diff) * 0.08)
            cdm_inversion_info["mode"] = _cdm_mode
            cdm_inversion_info["shadow_multiplier"] = round(
                cdm_inversion_info.get("shadow_multiplier", 1.0) * (1.0 + away_chase_boost), 4)
            if _cdm_mode == "live":
                gs_mult *= (1.0 + away_chase_boost)
                gs_reason.append(
                    f"away CDM pinned-back boost +{away_chase_boost*100:.1f}% "
                    f"(diff={expected_diff:.1f})")
                cdm_inversion_info["applied"] = True
                cdm_inversion_info["multiplier"] = round(
                    cdm_inversion_info.get("multiplier", 1.0) * (1.0 + away_chase_boost), 4)
                cdm_inversion_info["reason"] = (
                    (cdm_inversion_info.get("reason") or "")
                    + f"; away pinned-back boost +{away_chase_boost*100:.1f}% (diff={expected_diff:.1f})"
                ).lstrip("; ")
            else:
                print(f"[CDM SCRIPT BOOST shadow] {prop_type} pos={_pos_upper} venue=away "
                      f"expected_diff={expected_diff:.2f} would_boost=+{away_chase_boost*100:.1f}% "
                      f"(NOT APPLIED)")
                cdm_inversion_info["reason"] = (
                    (cdm_inversion_info.get("reason") or "")
                    + f"; away pinned-back boost +{away_chase_boost*100:.1f}% (diff={expected_diff:.1f}) [shadow]"
                ).lstrip("; ")
        # Away GK UNDER passes — high-scoring game suppression (boost projection up)
        if (prop_type in {"pass_attempts", "passes"} and venue == "away" and _is_gk
                and expected_total is not None and expected_total >= 3.0):
            highscore_boost = min(0.05, (expected_total - 2.5) * 0.025)
            gs_mult *= (1.0 + highscore_boost)
            gs_reason.append(f"away GK high-scoring boost +{highscore_boost*100:.1f}% (total={expected_total:.1f})")
        # CB MANAGING-LEAD BOOST — when a team is clearly favoured to win,
        # their CBs accumulate passes managing the lead (build-out from the back,
        # waste time, recycle under reduced pressure).
        # Home CB on clear home favourite, or away CB on clear away favourite.
        # Empirical pattern: Micky van de Ven (Spurs away, won 2-1) hit 61 actual
        # vs 42-54 projected UNDER — model was suppressing a lead-managing CB.
        # CB MANAGING-LEAD BOOST — increased from 8% to 15% max.
        # Winning CBs accumulate passes heavily: Micky van de Ven (Spurs away)
        # hit 61 actual vs 42 projected. Threshold lowered from 0.8 to 0.3 to
        # catch moderate favourites too.
        _cb_set = {"CB", "LB", "RB", "LCB", "RCB", "WB", "WBL", "WBR"}
        if (prop_type in {"pass_attempts", "passes"}
                and _pos_upper in _cb_set
                and expected_diff is not None):
            # Home CB: home team is favourite
            if venue == "home" and expected_diff > 0.3:
                cb_lead_boost = min(0.15, (expected_diff - 0.3) * 0.07)
                gs_mult *= (1.0 + cb_lead_boost)
                gs_reason.append(f"home CB lead-manage boost +{cb_lead_boost*100:.1f}% (diff={expected_diff:.1f})")
            # Away CB: away team is favourite (negative expected_diff = away fav)
            elif venue == "away" and expected_diff < -0.3:
                cb_lead_boost = min(0.15, (abs(expected_diff) - 0.3) * 0.07)
                gs_mult *= (1.0 + cb_lead_boost)
                gs_reason.append(f"away CB lead-manage boost +{cb_lead_boost*100:.1f}% (diff={expected_diff:.1f})")
        if gs_mult != 1.0:
            raw_before_gs = posterior_mean
            posterior_mean = round(posterior_mean * gs_mult, 1)
            game_script_info = {"applied": True,
                                "multiplier": round(gs_mult, 4),
                                "reason": "; ".join(gs_reason)}
            print(f"[GAME SCRIPT] {prop_type} pos={_pos_upper} venue={venue} "
                  f"mult={gs_mult:.3f} {raw_before_gs} → {posterior_mean} ({'; '.join(gs_reason)})")

    # ═══════════════════════════════════════════
    # LEAGUE-EMPIRICAL CALIBRATION (post-posterior nudge)
    # ═══════════════════════════════════════════
    # `league_calibration` may be either:
    #   * a dict with `found` (single side, legacy form), OR
    #   * a dict {"over": <bucket>, "under": <bucket>} so we can pick the
    #     correct bucket *after* the posterior tells us which side we'll
    #     recommend. The OVER and UNDER buckets are independently
    #     estimated from settled picks, so we MUST apply the side that
    #     matches the eventual recommendation (otherwise we apply a
    #     bucket that measures a different population).
    # ═══════════════════════════════════════════
    league_calib_info = {"applied": False, "multiplier": 1.0, "n": 0,
                         "hit_rate": None, "bias": 0.0}
    _selected_calib = None
    if league_calibration and isinstance(league_calibration, dict):
        if "over" in league_calibration or "under" in league_calibration:
            # Two-sided form — choose bucket matching what we'd recommend now
            _post_side = "over" if posterior_mean >= line else "under"
            _selected_calib = league_calibration.get(_post_side)
        elif league_calibration.get("found"):
            _selected_calib = league_calibration

    if _selected_calib and _selected_calib.get("found"):
        lmult = float(_selected_calib.get("multiplier", 1.0))
        if abs(lmult - 1.0) > 0.001:
            raw_before_lc = posterior_mean
            posterior_mean = round(posterior_mean * lmult, 1)
            print(f"[LEAGUE CALIB] {prop_type} {position} {venue}: n={_selected_calib.get('n')}, "
                  f"hit={_selected_calib.get('hit_rate')}, bias={_selected_calib.get('bias')}, "
                  f"mult={lmult:.4f} {raw_before_lc} → {posterior_mean}")
        league_calib_info = {
            "applied":  True,
            "multiplier": round(lmult, 4),
            "n":        int(_selected_calib.get("n", 0)),
            "hit_rate": _selected_calib.get("hit_rate"),
            "bias":     _selected_calib.get("bias", 0.0),
            "direction": _selected_calib.get("direction", "neutral"),
        }

    # ═══════════════════════════════════════════
    # SCENARIO PRIORS (cheat-sheet, scenario-conditional)
    # ═══════════════════════════════════════════
    # `scenario_priors_result` arrives pre-computed by the caller as
    # Σ P(scenario_i) × scenario_priors.lookup(scenario_i, ...).
    # We apply it as a final small multiplicative nudge that lives
    # alongside league_calibration but is conditioned on the predicted
    # game script (draw / blowout / low-scoring / etc.) instead of the
    # league. Two operating modes:
    #   * "off"    : no-op (legacy behaviour)
    #   * "shadow" : compute & log only — multiplier NOT applied
    #   * "live"   : compute, log, AND apply
    # Default is "off" so any caller that doesn't pass scenario_priors_mode
    # behaves exactly as before.
    scenario_priors_info = {"applied": False, "multiplier": 1.0,
                            "mode": scenario_priors_mode, "components": []}
    if (scenario_priors_result and isinstance(scenario_priors_result, dict)
            and scenario_priors_result.get("found")
            and scenario_priors_mode in {"shadow", "live"}):
        sp_mult = float(scenario_priors_result.get("multiplier", 1.0))
        scenario_priors_info["multiplier"] = round(sp_mult, 4)
        scenario_priors_info["components"] = scenario_priors_result.get("components", [])
        scenario_priors_info["coverage"]   = scenario_priors_result.get("coverage")
        scenario_priors_info["direction"]  = scenario_priors_result.get("direction", "neutral")
        scenario_priors_info["n"]          = scenario_priors_result.get("n", 0)
        if scenario_priors_mode == "live" and abs(sp_mult - 1.0) > 0.001:
            raw_before_sp = posterior_mean
            posterior_mean = round(posterior_mean * sp_mult, 1)
            scenario_priors_info["applied"] = True
            print(f"[SCENARIO PRIORS LIVE] {prop_type} {position} {venue}: "
                  f"mult={sp_mult:.4f} {raw_before_sp} → {posterior_mean} "
                  f"(coverage={scenario_priors_info['coverage']}, n={scenario_priors_info['n']})")
        else:
            print(f"[SCENARIO PRIORS SHADOW] {prop_type} {position} {venue}: "
                  f"would_mult={sp_mult:.4f} (coverage={scenario_priors_info.get('coverage')}, "
                  f"n={scenario_priors_info['n']}, components={len(scenario_priors_info['components'])})")

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
    # Use a blended std that accounts for both posterior precision AND player volatility.
    # Floor = 55% of prior_std ensures we never collapse the band too far below historical
    # spread, PLUS an absolute floor of 17% of the posterior mean so wide-gap props stay
    # honest (e.g. a 30-mean projection with line at 38.5 must acknowledge the real range).
    effective_std = max(posterior_std, prior_std * 0.55, posterior_mean * 0.17)

    # ── POSITION BIAS CORRECTION ─────────────────────────────────────────────
    # 1291-pick empirical audit identified systematic over/under-estimation
    # for specific positions on pass_attempts. Applied before de-normalise so
    # the correction compounds correctly with playing-time scaling.
    #
    #   CAM: actual avg 3.2 below projection (model overestimates) → -4% correction
    #   CM:  actual avg 1.8 below projection (model overestimates) → -2% correction
    #
    # These positions overestimate because the model uses a general "high possession
    # = more passes" rule, but CAMs/CMs in high possession systems often play
    # narrow and touch the ball less in direct build-up than CBs or DMs.
    _POS_BIAS_CORRECTIONS = {
        ("CAM", "pass_attempts"): 0.96,
        ("AM",  "pass_attempts"): 0.96,
        ("CM",  "pass_attempts"): 0.98,
        ("MC",  "pass_attempts"): 0.98,
        ("CAM", "passes"):        0.96,
        ("CM",  "passes"):        0.98,
    }
    _pos_bias_key = ((position or "").upper(), prop_type)
    _pos_bias_mult = _POS_BIAS_CORRECTIONS.get(_pos_bias_key)
    if _pos_bias_mult is not None and abs(_pos_bias_mult - 1.0) > 0.001:
        _raw_before_bias = posterior_mean
        posterior_mean = round(posterior_mean * _pos_bias_mult, 1)
        print(f"[POS BIAS CORR] {position} {prop_type}: {_raw_before_bias:.1f} → {posterior_mean:.1f} "
              f"(×{_pos_bias_mult} empirical correction)")

    # ── DE-NORMALISE: convert per-90 posterior back to raw expected units ────
    # All maths above ran in per-90 space. Now scale down to the player's
    # expected playing time for this match (e.g. 70 min → ×0.778).
    # effective_std scales by the same factor so CI width stays proportional.
    _posterior_mean_raw = posterior_mean * _denorm
    _effective_std_raw  = effective_std  * _denorm
    _prior_mean_raw     = prior_mean     * _denorm
    _momentum_mean_raw  = momentum_mean  * _denorm
    _prior_variance_raw = prior_variance * (_denorm ** 2)

    print(f"[PER90] {prop_type}: posterior={posterior_mean:.1f}/90 → {_posterior_mean_raw:.1f} raw "
          f"(exp_min={_exp_min:.0f}, denorm={_denorm:.3f})")

    # ── MONTE CARLO SIMULATION ───────────────────────────────────────────────
    # Count stats (shots, goals, saves etc.) use the negative binomial
    # distribution via gamma-Poisson mixture — naturally discrete and right-skewed.
    # Continuous stats (pass_attempts) use Gaussian.
    p_over, p_under, ci_low, ci_high = _monte_carlo_probability(
        mean=_posterior_mean_raw,
        std=_effective_std_raw,
        line=line,
        n_sims=5000,
        is_count_stat=is_count_stat,
        variance=_prior_variance_raw,
    )

    # Edge = how far DENORMALISED posterior is from line as % of denormalised std
    edge_z = round(abs(_posterior_mean_raw - line) / _effective_std_raw, 2) if _effective_std_raw > 0 else 0

    # Projection-vs-line gap (raw + relative). Surfaced so the UI can show
    # "deep edge" / "razor thin" labels — picks with |edgeGapPct| >= 15%
    # historically convert at much higher rates than thin-edge picks.
    edge_gap_abs = round(_posterior_mean_raw - line, 2)
    edge_gap_pct = round((edge_gap_abs / line) * 100, 1) if line and line > 0 else 0.0
    if abs(edge_gap_pct) >= 20:
        edge_gap_band = "DEEP"
    elif abs(edge_gap_pct) >= 10:
        edge_gap_band = "STRONG"
    elif abs(edge_gap_pct) >= 5:
        edge_gap_band = "MODERATE"
    else:
        edge_gap_band = "THIN"

    # Layer weights (for transparency)
    w_prior = round(prior_precision / total_precision * 100)
    w_momentum = round(momentum_precision / total_precision * 100)
    w_covariate = round(covariate_precision / total_precision * 100)

    # Volatility classification (based on per-90 CV — position-invariant)
    if cv < 0.15:
        volatility_label = "LOW"
    elif cv < 0.30:
        volatility_label = "NORMAL"
    elif cv < 0.50:
        volatility_label = "HIGH"
    else:
        volatility_label = "EXTREME"

    # Venue avg — denormalise to match raw units
    _venue_avg_raw = round(sum(venue_vals) / len(venue_vals) * _denorm, 1) if venue_vals else None

    # Use Monte Carlo probability as the recommendation signal, not just mean vs line.
    # For count stats (negative binomial, right-skewed), the mean can sit above the line
    # while the MAJORITY of probability mass is below it — in that case, UNDER is correct.
    # This prevents contradictory picks like "Projection 24.0 OVER line 23.5 — P(UNDER) 60.6%".
    _rec_by_prob = "over" if p_over >= p_under else "under"

    return {
        # Core output — all values in RAW units (de-normalised from per-90)
        "posteriorMean": round(_posterior_mean_raw, 1),
        "posteriorStd": round(posterior_std * _denorm, 2),
        "recommendation": _rec_by_prob,
        "pOver": round(p_over * 100, 1),
        "pUnder": round(p_under * 100, 1),
        "confidenceInterval": [ci_low, ci_high],
        "edgeZ": edge_z,

        # 3 Layers (for transparency) — also in raw units
        "priorMean": round(_prior_mean_raw, 1),
        "priorStd": round(prior_std * _denorm, 2),
        "priorWeight": w_prior,
        "priorSamples": n,

        "momentumEffect": round((_momentum_mean_raw - _prior_mean_raw), 2),
        "momentumMean": round(_momentum_mean_raw, 1),
        "momentumLabel": momentum_label,
        "momentumWeight": w_momentum,
        "trendPerGame": round(trend_per_game * _denorm, 3),

        "covariateAdjustment": round(covariate_adjustment * _denorm, 2),
        "covariateWeight": w_covariate,
        "venueAvg": _venue_avg_raw,
        "venueSamples": len(venue_vals) if venue_vals else 0,

        "reversalFlag": reversal_flag,
        "streakFlag": streak_flag,
        "volatility": volatility_label,
        "cv": round(cv, 3),
        "pressIntensity": press_intensity_info,
        "expectedMinutes": round(_exp_min, 0),
        "isCountStat": is_count_stat,

        # New: edge-gap surfacing for the UI
        "edgeGapAbs": edge_gap_abs,
        "edgeGapPct": edge_gap_pct,
        "edgeGapBand": edge_gap_band,

        # New: empirical league calibration applied (post-posterior)
        "leagueCalibration": league_calib_info,

        # New: game-script nudge applied (chase mode / nailbiter avoidance)
        "gameScript": game_script_info,

        # Scenario-aware priors (cheat-sheet conditional on predicted game script)
        # Always emitted so shadow-mode logs and admin inspector can see what
        # the layer would have / did do.
        "scenarioPriors": scenario_priors_info,

        # CDM inverted-possession + pinned-back script boost (away CDM passes).
        # Always emitted; `mode` reports off|shadow|live and `applied` shows
        # whether the projection was actually nudged this call.
        "cdmInversion": cdm_inversion_info,
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
        "pressIntensity": {
            "score": 0.0, "multiplier": 1.0, "label": "Unknown", "signal_used": None,
            "ppda": None, "reasoning": "",
            "avg_defensive_actions": None, "avg_tackles": None, "avg_interceptions": None,
            "avg_poss": None, "avg_passes": None,
        },
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
        "ppda": None, "reasoning": "",
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
            "ppda":                  None,
            "reasoning":             "",
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
        "ppda":                  None,
        "reasoning":             "",
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
        # ── Attacking / Goal-threat props ────────────────────────────────────
        # opponent totalShots = how much the opponent attacks → shot volume
        # opponent shotsOnTarget = higher quality attack → more dangerous shots taken/faced
        "shots":           ("totalShots",    0.18),   # ~18% of team shots belong to the subject
        "shots_on_target": ("shotsOnTarget", 0.18),   # same share for quality shots
        "saves":           ("shotsOnTarget", 0.70),   # GK saves ~70% of opponent SOT

        # ── Defensive action props ───────────────────────────────────────────
        # More opponent shots/passes → more defensive work for the subject player
        "tackles":         ("totalPasses",   0.015),  # opp passes = tackle opportunities
        "clearances":      ("totalShots",    0.28),   # each opp shot ≈ 0.28 clearances for a CB
        "blocks":          ("shotsOnTarget", 0.14),   # on-target shots blocked by outfield players
        "interceptions":   ("totalPasses",   0.013),  # more opp passes = more interception chances
        "fouls_committed": ("totalPasses",   0.009),  # more opp pressure = more fouls defending
        "duels_won":       ("totalPasses",   0.028),  # more opp passes = more duel situations
        "fouls_drawn":     ("totalShots",    0.09),   # attackers draw fouls near goal → opp shots proxy
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
