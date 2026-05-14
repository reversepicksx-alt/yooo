"""
CS2 Bayesian Projection Engine

3-layer Bayesian model for Counter-Strike 2 per-map player props:
  Layer 1: PRIOR    — Career/recent average (hyper-prior shrinkage for small samples)
  Layer 2: MOMENTUM — Exponentially-decayed recent form (last 7 maps, newest first)
  Layer 3: COVARIATE— Tier adjustment (S/A tier gets +5% weight vs lower tiers)

Discrete props (kills, deaths, assists, first_kills, clutches_won) → Poisson MC
Continuous props (adr, rating, headshot_pct) → Gaussian MC
50,000 Monte Carlo trials for all props.
"""
import math
import random
import statistics as stats_mod
from typing import Optional

# ── Prop definitions ─────────────────────────────────────────────────────────
CS2_PROPS = {
    "kills":           "kills",
    "deaths":          "deaths",
    "assists":         "assists",
    "adr":             "adr",
    "headshot_pct":    "headshotPct",
    "first_kills":     "firstKills",
    "clutches_won":    "clutchesWon",
    "rating":          "rating",
}

# Discrete (Poisson) vs continuous (Gaussian)
COUNT_PROPS = {"kills", "deaths", "assists", "first_kills", "clutches_won"}

# ── League-average hyper-priors (used when sample < MIN_SAMPLE) ─────────────
# Approximate pro-player averages per map in T1/T2 CS2
HYPER_PRIOR = {
    "kills":        18.0,
    "deaths":       15.0,
    "assists":       4.0,
    "adr":          75.0,
    "headshot_pct": 40.0,
    "first_kills":   2.5,
    "clutches_won":  0.5,
    "rating":        1.05,
}

MIN_SAMPLE   = 8   # below this, blend with hyper-prior
MC_TRIALS    = 50_000

# Momentum decay weights (index 0 = most recent map)
DECAY = [1.0, 0.82, 0.68, 0.56, 0.46, 0.38, 0.31]


def _extract_values(map_logs: list, prop_type: str) -> list:
    """Pull the raw numeric values for prop_type from map_logs."""
    field = CS2_PROPS.get(prop_type)
    if not field:
        return []
    vals = []
    for m in map_logs:
        v = m.get(field)
        if v is not None and v != "" and float(v) >= 0:
            vals.append(float(v))
    return vals


def _tier_weight(tier: str) -> float:
    """Higher weight for top-tier tournament maps."""
    tier = (tier or "").lower()
    return {"s": 1.2, "a": 1.1, "b": 1.0}.get(tier, 0.9)


def compute_cs2_projection(
    map_logs: list,
    prop_type: str,
    line: float,
    opponent_rank: Optional[int] = None,
) -> dict:
    """
    Main projection function.
    map_logs — list of per-map stat dicts from cs2_client, newest first.
    Returns same shape as mlb_engine result dict.
    """
    field  = CS2_PROPS.get(prop_type)
    if not field:
        return {"error": f"Unknown CS2 prop: {prop_type}"}

    values = _extract_values(map_logs, prop_type)

    if not values:
        return {
            "error":      "insufficient_data",
            "projection": round(HYPER_PRIOR.get(prop_type, line), 2),
        }

    n = len(values)

    # ── Layer 1: Prior (season average with hyper-prior shrinkage) ────────────
    season_mean = stats_mod.mean(values)
    hyper       = HYPER_PRIOR.get(prop_type, season_mean)
    # Shrink toward hyper-prior when sample is small
    alpha       = min(n, MIN_SAMPLE) / MIN_SAMPLE    # 0→1 as n grows to MIN_SAMPLE
    prior_mean  = alpha * season_mean + (1 - alpha) * hyper

    # ── Layer 2: Momentum (decayed recent form, last 7 maps) ─────────────────
    recent   = values[:len(DECAY)]
    weights  = []
    w_values = []
    for i, v in enumerate(recent):
        tier = map_logs[i].get("tier", "") if i < len(map_logs) else ""
        w = DECAY[i] * _tier_weight(tier)
        w_values.append(v)
        weights.append(w)

    if weights and sum(weights) > 0:
        momentum_mean = sum(v * w for v, w in zip(w_values, weights)) / sum(weights)
    else:
        momentum_mean = prior_mean

    # ── Blend prior + momentum ────────────────────────────────────────────────
    # More maps → trust momentum more
    blend      = min(n / 15.0, 0.6)    # cap at 60% momentum weight
    projection = (1 - blend) * prior_mean + blend * momentum_mean

    # ── Layer 3: Opponent rank adjustment (optional) ─────────────────────────
    if opponent_rank and opponent_rank <= 5:
        if prop_type in ("kills", "adr", "rating"):
            projection *= 0.97  # top-5 teams are harder to frag against
        elif prop_type == "deaths":
            projection *= 1.03
    elif opponent_rank and opponent_rank > 20:
        if prop_type in ("kills", "adr", "rating"):
            projection *= 1.03
        elif prop_type == "deaths":
            projection *= 0.97

    projection = max(projection, 0.0)

    # ── Variance estimate ─────────────────────────────────────────────────────
    if n >= 2:
        std_dev = stats_mod.stdev(values)
    else:
        std_dev = projection * 0.30

    std_dev = max(std_dev, 0.5)

    # ── Monte Carlo simulation ────────────────────────────────────────────────
    is_count = prop_type in COUNT_PROPS
    over_count = 0

    for _ in range(MC_TRIALS):
        # Sample projection with noise
        proj_sample = random.gauss(projection, std_dev * 0.3)
        proj_sample = max(proj_sample, 0.0)

        if is_count:
            lam = max(proj_sample, 0.01)
            simulated = random.random()
            # Approximate Poisson via Gaussian for speed
            val = max(round(random.gauss(lam, math.sqrt(lam))), 0)
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

    # Round projection for count stats
    display_proj = round(projection) if is_count else round(projection, 1)

    # Streak detection (last 5 maps vs line)
    streak_flag = ""
    if len(values) >= 5:
        last5 = values[:5]
        over5 = sum(1 for v in last5 if v > line)
        if over5 >= 4:
            streak_flag = "🔥 OVER streak (4+ of last 5)"
        elif over5 <= 1:
            streak_flag = "❄️ UNDER streak (4+ of last 5)"

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
        "gameLogs":      map_logs,
    }
