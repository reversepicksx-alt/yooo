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
                if dom_mult > 1.10:
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
            squeeze_floor = 0.80 if _is_cb else (0.70 if _is_def else 0.55)
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
                # TWO REGIMES for GK possession deficit:
                # EXTREME deficit (expected < 40%): GK starved of touches. Squeeze.
                # MODERATE deficit (40–87% of avg): Some back-pass recycling. Mild boost.
                if expected_poss < 40.0:
                    squeeze_mult = round(max(0.70, poss_ratio ** 0.8), 3)
                    raw_before_gk = posterior_mean
                    posterior_mean = round(posterior_mean * squeeze_mult, 1)
                    print(f"[GK EXTREME SQUEEZE] {prop_type}: team expected only {expected_poss:.1f}% "
                          f"possession — GK ball-touch volume drops sharply. "
                          f"mult={squeeze_mult} {raw_before_gk} → {posterior_mean}")
                else:
                    # Capped at +8% (was +15%). Exponent 0.35 = very gentle curve.
                    inverse_ratio = 1.0 / max(poss_ratio, 0.60)
                    boost_mult = round(min(1.08, inverse_ratio ** 0.35), 3)
                    raw_before_gk = posterior_mean
                    posterior_mean = round(posterior_mean * boost_mult, 1)
                    print(f"[GK POSS BOOST] {prop_type}: team_avg={team_season_avg_poss:.1f}% "
                          f"expected={expected_poss:.1f}% ratio={poss_ratio:.2f} "
                          f"inv_mult={boost_mult} {raw_before_gk} → {posterior_mean}")
            # GK BUILDUP BOOST REMOVED.
            # Removed because: when a home team has above-average possession,
            # their GK is LESS involved (team builds in the opponent's half,
            # defenders push forward, GK rarely gets back-passes).
            # Evidence: Gazzaniga (Girona HOME, above-avg possession vs Betis) → 26 passes.
            # Simón (Athletic Club HOME, high possession vs Osasuna) → 26 passes.
            # The buildup boost was systematically over-projecting home GKs.

    # ═══════════════════════════════════════════
    # PRESS INTENSITY — PPDA Proxy (independent of match dominance)
    # Applied AFTER posterior for pass_attempts/passes props only.
    #
    # For OUTFIELD players: heavy opponent pressing → fewer passes (dispossessed)
    # For GKs: heavy opponent pressing → MORE back-passes (defenders under pressure
    #   play it safe back to the GK constantly) → INVERTED multiplier for GKs.
    # ═══════════════════════════════════════════
    press_intensity_info = {
        "score": 0.0, "multiplier": 1.0, "label": "Unknown", "signal_used": None,
        "avg_defensive_actions": None, "avg_tackles": None, "avg_interceptions": None,
        "avg_poss": None, "avg_passes": None,
    }
    if opponent_fixture_stats and prop_type in {"pass_attempts", "passes"}:
        press_intensity_info = compute_press_intensity_score(opponent_fixture_stats)
        if _is_gk:
            # GK press BOOST — but only for AWAY GKs.
            #
            # When a GK is AWAY and the home team presses hard, defenders get
            # pinned back and play it safe to the GK repeatedly, generating
            # high back-pass volume → GK distributes more.
            # Evidence: Valles (Real Betis AWAY at pressing Girona) got 35 passes (OVER 30.5 ✓)
            #
            # For HOME GKs: the pressing effect is murkier and does not reliably
            # produce more passes (home team has possession, presses don't force
            # as many back-passes when you control the game).
            # No boost applied for home GKs — let the baseline and possession model decide.
            raw_mult = press_intensity_info["multiplier"]
            if raw_mult < 1.0 and venue == "away":
                gk_press_mult = round(min(1.05, 1.0 + (1.0 - raw_mult) * 0.35), 3)
                raw_before = posterior_mean
                posterior_mean = round(posterior_mean * gk_press_mult, 1)
                print(f"[GK AWAY PRESS BOOST] {prop_type}: opp_press={press_intensity_info['label']} "
                      f"(score={press_intensity_info['score']}, mult={raw_mult}) "
                      f"→ boost {gk_press_mult} {raw_before} → {posterior_mean}")
        elif press_intensity_info["multiplier"] < 1.0:
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
    # Use a blended std that accounts for both posterior precision AND player volatility.
    # Floor = 55% of prior_std ensures we never collapse the band too far below historical
    # spread, PLUS an absolute floor of 17% of the posterior mean so wide-gap props stay
    # honest (e.g. a 30-mean projection with line at 38.5 must acknowledge the real range).
    effective_std = max(posterior_std, prior_std * 0.55, posterior_mean * 0.17)

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

    return {
        # Core output — all values in RAW units (de-normalised from per-90)
        "posteriorMean": round(_posterior_mean_raw, 1),
        "posteriorStd": round(posterior_std * _denorm, 2),
        "recommendation": "over" if _posterior_mean_raw > line else "under",
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
