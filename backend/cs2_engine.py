"""
CS2 Bayesian Projection Engine v2 — World-Class Edition

5-layer model for Counter-Strike 2 player props:

  Layer 1: PRIOR          — Career kills-per-round × expected rounds (hyper-prior shrinkage)
  Layer 2: MOMENTUM       — KAST-weighted decayed recent form (last 8 entries, newest first)
  Layer 3: OPPONENT TIER  — Full 7-bracket rank adjustment (Top5 → 100+)
  Layer 4: COVARIATES     — Tournament tier, first-duel ratio, entry-fragger variance,
                            overtime propensity, KAST consistency, win-rate context
  Layer 5: MC SIMULATION  — Negative-binomial for discrete counts, Gaussian for continuous

50,000 Monte Carlo trials. All factors independently sourced from BDL API data.
"""
import math
import random
import statistics as stats_mod
from typing import Optional

# ── Prop definitions ─────────────────────────────────────────────────────────
CS2_PROPS = {
    # Per-map props
    "kills":            "kills",
    "deaths":           "deaths",
    "assists":          "assists",
    "adr":              "adr",
    "headshot_pct":     "headshotPct",
    "first_kills":      "firstKills",
    "clutches_won":     "clutchesWon",
    "rating":           "rating",
    # Per-match (maps 1-2 aggregate) props — values pulled from match-level logs
    "maps_1_2_kills":   "maps_1_2_kills",
    "maps_1_2_deaths":  "maps_1_2_deaths",
    "maps_1_2_assists": "maps_1_2_assists",
    "maps_1_2_adr":     "maps_1_2_adr",
}

# Props that require per-MATCH data (not per-map)
MATCH_LEVEL_PROPS = {"maps_1_2_kills", "maps_1_2_deaths", "maps_1_2_assists", "maps_1_2_adr"}

# Discrete (Negative-Binomial / Poisson) vs continuous (Gaussian)
COUNT_PROPS = {
    "kills", "deaths", "assists", "first_kills", "clutches_won",
    "maps_1_2_kills", "maps_1_2_deaths", "maps_1_2_assists",
}

# Props where we normalise by rounds before projecting
KILLS_CLASS_PROPS = {"kills", "maps_1_2_kills"}

# ── League-average hyper-priors ───────────────────────────────────────────────
# T1/T2 pro averages. maps_1_2 ≈ 2× per-map.
HYPER_PRIOR = {
    "kills":            18.0,
    "deaths":           15.0,
    "assists":           4.0,
    "adr":              75.0,
    "headshot_pct":     40.0,
    "first_kills":       2.5,
    "clutches_won":      0.5,
    "rating":            1.05,
    "maps_1_2_kills":   34.0,
    "maps_1_2_deaths":  28.0,
    "maps_1_2_assists":  8.0,
    "maps_1_2_adr":     75.0,
}

# ── Kills-per-round hyper-prior (used when normalising) ──────────────────────
KPR_HYPER = 0.70   # ~0.70 kills/round is strong T1/T2 average

# Standard expected rounds per map (before OT) — competitive CS2
EXPECTED_ROUNDS_PER_MAP  = 26.0
EXPECTED_ROUNDS_2MAPS    = 52.0   # Maps 1+2 combined

MIN_SAMPLE   = 8       # below this, blend with hyper-prior
MC_TRIALS    = 50_000

# ── Momentum decay weights (index 0 = most recent) ───────────────────────────
DECAY = [1.0, 0.85, 0.72, 0.60, 0.50, 0.42, 0.35, 0.29]


# ── Utility helpers ───────────────────────────────────────────────────────────

def _tier_weight(tier: str) -> float:
    """Tournament quality weight for momentum entries."""
    return {"s": 1.20, "a": 1.10, "b": 1.0}.get((tier or "").lower(), 0.90)


def _extract_values(logs: list, prop_type: str) -> list:
    """Pull raw numeric values for prop_type from logs (per-map or per-match)."""
    field = CS2_PROPS.get(prop_type)
    if not field:
        return []
    vals = []
    for m in logs:
        v = m.get(field)
        if v is not None and v != "" and float(v) >= 0:
            vals.append(float(v))
    return vals


def _kast_weight(log_entry: dict, prop_type: str) -> float:
    """
    KAST (Kill/Assist/Survive/Trade) efficiency multiplier for momentum weighting.
    Higher KAST = more reliable game → upweight that entry.
    League avg ~65-70%. Use 67.5% as baseline.
    """
    if prop_type in MATCH_LEVEL_PROPS:
        kast = log_entry.get("maps_1_2_kast", 0) or 0
    else:
        kast = log_entry.get("kast", 0) or 0
    if kast <= 0:
        return 1.0  # unknown → neutral
    return 0.7 + 0.6 * (kast / 100.0)  # ranges ~0.7 (0% KAST) → 1.3 (100% KAST)


def _opponent_rank_multiplier(rank: Optional[int], prop_type: str) -> float:
    """
    Full 7-bracket opponent rank adjustment.
    Stronger opponents → fewer kills (but more deaths).
    ADR and rating follow kills directionally.
    """
    if not rank or rank <= 0:
        return 1.0

    kills_direction_props = {"kills", "maps_1_2_kills", "adr", "maps_1_2_adr", "rating"}
    deaths_direction_props = {"deaths", "maps_1_2_deaths"}

    # kills/adr/rating adjustment
    if prop_type in kills_direction_props:
        if rank <= 5:   return 0.88   # elite — world-class CT setups
        if rank <= 10:  return 0.92
        if rank <= 20:  return 0.96
        if rank <= 50:  return 1.0
        if rank <= 100: return 1.04
        if rank <= 200: return 1.08
        return 1.12                   # 200+ = very weak opposition

    # deaths — inverse (facing better opponents → more deaths)
    if prop_type in deaths_direction_props:
        if rank <= 5:   return 1.12
        if rank <= 10:  return 1.07
        if rank <= 20:  return 1.03
        if rank <= 50:  return 1.0
        if rank <= 100: return 0.97
        if rank <= 200: return 0.94
        return 0.91

    return 1.0  # assists, first_kills, clutches — neutral to opponent rank


def _tournament_tier_multiplier(tier: str, prop_type: str) -> float:
    """
    S-tier events are slower and more structured → lower raw kills.
    C-tier events are chaotic → more kills.
    """
    t = (tier or "").lower()
    kills_direction = prop_type in {"kills", "maps_1_2_kills", "adr", "maps_1_2_adr"}
    if t == "s":
        return 0.97 if kills_direction else 1.0
    if t in ("b", "c", "d"):
        return 1.04 if kills_direction else 1.0
    return 1.0  # a-tier = baseline


def _first_duel_ratio(logs: list, prop_type: str):
    """
    First duel ratio = firstKills / max(firstDeaths, 1).
    > 1.2 → entry fragger: higher kills ceiling, more variance
    < 0.8 → support role: capped but consistent
    Returns (projection_multiplier, variance_multiplier).
    """
    if prop_type not in {"kills", "maps_1_2_kills", "deaths", "maps_1_2_deaths"}:
        return 1.0, 1.0

    fk_field = "maps_1_2_firstKills" if prop_type in MATCH_LEVEL_PROPS else "firstKills"
    fd_field = "maps_1_2_firstDeaths" if prop_type in MATCH_LEVEL_PROPS else "firstDeaths"

    fk_vals = [m.get(fk_field, 0) or 0 for m in logs[:10]]
    fd_vals = [m.get(fd_field, 0) or 0 for m in logs[:10]]

    if not any(fk_vals) and not any(fd_vals):
        return 1.0, 1.0

    avg_fk = sum(fk_vals) / max(len(fk_vals), 1)
    avg_fd = sum(fd_vals) / max(len(fd_vals), 1)
    ratio  = avg_fk / max(avg_fd, 0.5)

    if prop_type in {"kills", "maps_1_2_kills"}:
        if ratio > 1.3:   return 1.06, 1.15   # aggressive entry fragger
        if ratio > 1.1:   return 1.03, 1.07
        if ratio < 0.75:  return 0.97, 0.88   # pure support
        if ratio < 0.90:  return 0.99, 0.93
    elif prop_type in {"deaths", "maps_1_2_deaths"}:
        if ratio > 1.3:   return 0.96, 1.10   # entry fraggers die less (win duels)
        if ratio < 0.75:  return 1.04, 0.92   # support players die more

    return 1.0, 1.0


def _overtime_boost(logs: list, prop_type: str) -> float:
    """
    If team frequently goes to OT, rounds run long → more kill opportunities.
    Only applies to kills/maps_1_2_kills.
    """
    if prop_type not in KILLS_CLASS_PROPS:
        return 0.0

    ot_field = "overtimeRounds" if prop_type == "kills" else None
    if ot_field is None:
        # For match-level, check maps within the match
        ot_rounds = []
        for m in logs[:8]:
            for mp in (m.get("maps") or []):
                ot = mp.get("overtimeRounds") or 0
                ot_rounds.append(ot)
        avg_ot = sum(ot_rounds) / max(len(ot_rounds), 1) if ot_rounds else 0
    else:
        ot_vals = [m.get(ot_field, 0) or 0 for m in logs[:8]]
        avg_ot = sum(ot_vals) / max(len(ot_vals), 1) if ot_vals else 0

    # Each OT round ≈ ~0.7 kill per round for an average fragger
    # Maps that go to OT add ~3-6 extra rounds. Boost is the expected extra kills.
    return round(avg_ot * 0.7, 2)


def _win_rate_adjustment(logs: list, prop_type: str) -> float:
    """
    Winning teams kill more (CT holds, successful T attacks).
    Win rate → projection modifier for kills/deaths.
    """
    if prop_type not in {"kills", "maps_1_2_kills", "deaths", "maps_1_2_deaths"}:
        return 1.0

    won_field = "wonMatch" if prop_type in MATCH_LEVEL_PROPS else "wonMap"
    won_vals = [m.get(won_field) for m in logs[:10] if m.get(won_field) is not None]
    if not won_vals:
        return 1.0

    win_rate = sum(1 for w in won_vals if w) / len(won_vals)

    if prop_type in {"kills", "maps_1_2_kills"}:
        # Win rate 70%+: slightly more kills (organized winning plays)
        # Win rate <40%: slightly fewer kills (being outclassed)
        return 0.95 + 0.10 * win_rate   # range: 0.95 (0% wr) → 1.05 (100% wr)

    if prop_type in {"deaths", "maps_1_2_deaths"}:
        # Losing teams die more
        return 1.05 - 0.10 * win_rate   # range: 1.05 (0% wr) → 0.95 (100% wr)

    return 1.0


def _round_normalized_projection(
    logs: list,
    prop_type: str,
    prior_mean: float,
    n: int,
) -> float:
    """
    For kills props: normalize by rounds played to get kills/round,
    then scale back by expected rounds for tomorrow's match.
    This is the single most important correction for kill props.
    """
    if prop_type not in KILLS_CLASS_PROPS:
        return prior_mean

    is_match = prop_type in MATCH_LEVEL_PROPS
    kpr_field   = "killsPerRound_m1m2" if is_match else "killsPerRound"
    rounds_field = "maps_1_2_rounds"   if is_match else "totalRounds"

    kpr_vals     = [m.get(kpr_field, 0) for m in logs if m.get(kpr_field, 0) > 0]
    rounds_vals  = [m.get(rounds_field, 0) for m in logs if m.get(rounds_field, 0) > 0]

    if not kpr_vals:
        return prior_mean

    # Career avg KPR with hyper-prior shrinkage
    career_kpr = sum(kpr_vals) / len(kpr_vals)
    alpha      = min(len(kpr_vals), MIN_SAMPLE) / MIN_SAMPLE
    kpr        = alpha * career_kpr + (1 - alpha) * KPR_HYPER

    # Expected rounds tomorrow (use recent avg rounds as baseline)
    if rounds_vals:
        expected_rounds = sum(rounds_vals) / len(rounds_vals)
    else:
        expected_rounds = EXPECTED_ROUNDS_2MAPS if is_match else EXPECTED_ROUNDS_PER_MAP

    return kpr * expected_rounds


# ── Main projection function ──────────────────────────────────────────────────

def compute_cs2_projection(
    map_logs: list,
    prop_type: str,
    line: float,
    opponent_rank: Optional[int] = None,
    tournament_tier: Optional[str] = None,
) -> dict:
    """
    5-layer Bayesian CS2 projection.
    map_logs — per-map or per-match stat dicts (newest first).
    Returns a dict compatible with the mlb_engine result shape.
    """
    field = CS2_PROPS.get(prop_type)
    if not field:
        return {"error": f"Unknown CS2 prop: {prop_type}"}

    values = _extract_values(map_logs, prop_type)

    if not values:
        return {
            "error":      "insufficient_data",
            "projection": round(HYPER_PRIOR.get(prop_type, line), 2),
        }

    n = len(values)

    # ── Layer 1: Prior ────────────────────────────────────────────────────────
    season_mean = stats_mod.mean(values)
    hyper       = HYPER_PRIOR.get(prop_type, season_mean)
    alpha       = min(n, MIN_SAMPLE) / MIN_SAMPLE
    prior_mean  = alpha * season_mean + (1 - alpha) * hyper

    # Round-normalize kills props (most important tactical correction)
    if prop_type in KILLS_CLASS_PROPS:
        rn_proj   = _round_normalized_projection(map_logs, prop_type, prior_mean, n)
        # Blend: 60% round-normalised (tactical), 40% raw (preserves style)
        prior_mean = 0.60 * rn_proj + 0.40 * prior_mean

    # ── Layer 2: KAST-weighted momentum ──────────────────────────────────────
    recent   = values[:len(DECAY)]
    w_vals   = []
    weights  = []
    for i, v in enumerate(recent):
        if i >= len(map_logs):
            break
        log_entry = map_logs[i]
        tier_w  = _tier_weight(log_entry.get("tier", ""))
        kast_w  = _kast_weight(log_entry, prop_type)
        decay_w = DECAY[i]
        w = decay_w * tier_w * kast_w
        w_vals.append(v)
        weights.append(w)

    if weights and sum(weights) > 0:
        momentum_mean = sum(v * w for v, w in zip(w_vals, weights)) / sum(weights)
    else:
        momentum_mean = prior_mean

    # Blend: more data → trust momentum more (cap at 65%)
    blend      = min(n / 12.0, 0.65)
    projection = (1 - blend) * prior_mean + blend * momentum_mean

    # ── Layer 3: Opponent rank adjustment ────────────────────────────────────
    opp_multiplier = _opponent_rank_multiplier(opponent_rank, prop_type)
    projection    *= opp_multiplier

    # ── Layer 4: Additional covariates ───────────────────────────────────────
    # 4a. Tournament tier
    t_tier = tournament_tier or (map_logs[0].get("tier", "") if map_logs else "")
    projection *= _tournament_tier_multiplier(t_tier, prop_type)

    # 4b. Win rate context
    projection *= _win_rate_adjustment(map_logs, prop_type)

    # 4c. First duel ratio (entry fragger / support detection)
    fd_proj_mult, fd_var_mult = _first_duel_ratio(map_logs, prop_type)
    projection *= fd_proj_mult

    # 4d. Overtime bonus (for kills: extra rounds = extra kills)
    ot_bonus = _overtime_boost(map_logs, prop_type)
    projection += ot_bonus

    projection = max(projection, 0.0)

    # ── Variance estimation ───────────────────────────────────────────────────
    if n >= 2:
        std_dev = stats_mod.stdev(values)
    else:
        std_dev = projection * 0.30

    # KAST consistency: high KAST → lower variance (reliable)
    kast_vals = [
        m.get("maps_1_2_kast" if prop_type in MATCH_LEVEL_PROPS else "kast", 0) or 0
        for m in map_logs[:10]
    ]
    avg_kast = sum(kast_vals) / max(len([k for k in kast_vals if k > 0]), 1)
    if avg_kast >= 75:
        std_dev *= 0.85   # consistent player
    elif avg_kast <= 55 and avg_kast > 0:
        std_dev *= 1.20   # boom-bust player

    # Entry fragger variance adjustment
    std_dev *= fd_var_mult
    std_dev  = max(std_dev, 0.5)

    # ── Layer 5: Monte Carlo (Negative-Binomial for counts) ───────────────────
    is_count  = prop_type in COUNT_PROPS
    over_count = 0

    for _ in range(MC_TRIALS):
        # Add model uncertainty (projection itself has variance)
        proj_sample = random.gauss(projection, std_dev * 0.25)
        proj_sample = max(proj_sample, 0.0)

        if is_count:
            # Negative-Binomial via Gamma-Poisson compound:
            # captures overdispersion vs pure Poisson (real CS2 kills are overdispersed)
            lam = max(proj_sample, 0.01)
            # Dispersion: ~0.4 for kills (typical CS2 variance)
            r   = max(lam / 0.40, 1.0)   # overdispersion parameter
            p   = r / (r + lam)
            gamma_sample = random.gammavariate(r, (1 - p) / p) if r > 0 else lam
            val = max(round(random.gauss(gamma_sample, math.sqrt(max(gamma_sample, 0.01)))), 0)
        else:
            val = random.gauss(proj_sample, std_dev)

        if val > line:
            over_count += 1

    p_over  = round(over_count / MC_TRIALS * 100, 1)
    p_under = round(100 - p_over, 1)

    recommendation = "over" if p_over >= p_under else "under"
    conf_score     = max(p_over, p_under)

    if conf_score >= 70:
        conf_level = "High"
    elif conf_score >= 60:
        conf_level = "Medium"
    else:
        conf_level = "Low"

    # Round for count stats
    display_proj = round(projection) if is_count else round(projection, 1)

    # ── Streak detection ──────────────────────────────────────────────────────
    streak_flag = ""
    if len(values) >= 5:
        last5 = values[:5]
        over5 = sum(1 for v in last5 if v > line)
        if over5 >= 4:
            streak_flag = "🔥 OVER streak (4+ of last 5)"
        elif over5 <= 1:
            streak_flag = "❄️ UNDER streak (4+ of last 5)"

    # ── Tactical metrics (exposed to AI analysis) ─────────────────────────────
    kpr_vals = [m.get("killsPerRound_m1m2" if prop_type in MATCH_LEVEL_PROPS else "killsPerRound", 0)
                for m in map_logs if m.get("killsPerRound_m1m2" if prop_type in MATCH_LEVEL_PROPS else "killsPerRound", 0) > 0]

    fk_field  = "maps_1_2_firstKills" if prop_type in MATCH_LEVEL_PROPS else "firstKills"
    fd_field  = "maps_1_2_firstDeaths" if prop_type in MATCH_LEVEL_PROPS else "firstDeaths"
    avg_fk    = sum(m.get(fk_field, 0) or 0 for m in map_logs[:10]) / max(len(map_logs[:10]), 1)
    avg_fd    = sum(m.get(fd_field, 0) or 0 for m in map_logs[:10]) / max(len(map_logs[:10]), 1)

    return {
        "projection":    display_proj,
        "pOver":         p_over,
        "pUnder":        p_under,
        "recommendation": recommendation,
        "confidenceScore": round(conf_score),
        "confidenceLevel": conf_level,
        "priorMean":     round(prior_mean, 2),
        "momentumMean":  round(momentum_mean, 2),
        "sampleSize":    n,
        "streakFlag":    streak_flag,
        "tacticalMetrics": {
            "oppRankMultiplier":     round(opp_multiplier, 3),
            "tournamentTierAdj":     round(_tournament_tier_multiplier(t_tier, prop_type), 3),
            "winRateAdj":            round(_win_rate_adjustment(map_logs, prop_type), 3),
            "entryFraggerRatio":     round(avg_fk / max(avg_fd, 0.5), 2),
            "firstDuelProjMult":     round(fd_proj_mult, 3),
            "firstDuelVarMult":      round(fd_var_mult, 3),
            "avgKast":               round(avg_kast, 1),
            "overtimeBonus":         ot_bonus,
            "avgKillsPerRound":      round(sum(kpr_vals) / len(kpr_vals), 3) if kpr_vals else None,
        },
    }
