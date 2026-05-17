"""
CS2 Bayesian Projection Engine v3 — Precision Edition

7-layer model for Counter-Strike 2 player props:

  Layer 1: PRIOR          — Career KPR × expected rounds (hyper-prior shrinkage)
  Layer 2: MOMENTUM       — KAST + fatigue + H2H weighted decayed recent form
  Layer 3: OPPONENT TIER  — Full 7-bracket rank adjustment (Top5 → 100+)
  Layer 4: COVARIATES     — Tournament tier, first-duel ratio, entry-fragger variance,
                            overtime propensity, KAST consistency, win-rate context
  Layer 4b: H2H FORM      — Head-to-head win rate vs specific opponent
  Layer 4c: MAP AWARENESS — Map-specific round estimates (Nuke/Vertigo vs Dust2/Anubis)
  Layer 5: MC SIMULATION  — Negative-binomial for discrete counts, Gaussian for continuous
  Layer 5b: KPR SIGNATURE — AWPer/boom-bust vs consistent rifler variance calibration

50,000 Monte Carlo trials. All factors independently sourced from BDL API data.
"""
import math
import random
import statistics as stats_mod
from typing import Optional

# ── Prop definitions ─────────────────────────────────────────────────────────
CS2_PROPS = {
    "maps_1_3_kills":       "maps_1_3_kills",
    "maps_1_3_headshots":   "maps_1_3_headshots",
    # Per-map props
    "kills":                "kills",
    "deaths":               "deaths",
    "assists":              "assists",
    "adr":                  "adr",
    "headshot_pct":         "headshotPct",
    "headshots":            "headshotCount",    # per-map headshot count (kills × hs%)
    "first_kills":          "firstKills",
    "clutches_won":         "clutchesWon",
    "rating":               "rating",
    # Per-match (maps 1-2 aggregate) props — values pulled from match-level logs
    "maps_1_2_kills":       "maps_1_2_kills",
    "maps_1_2_deaths":      "maps_1_2_deaths",
    "maps_1_2_assists":     "maps_1_2_assists",
    "maps_1_2_adr":         "maps_1_2_adr",
    "maps_1_2_headshots":   "maps_1_2_headshots",
    # Map 3 props — from match logs, only valid when map3 was played
    "map3_kills":           "map3_kills",
    "map3_headshots":       "map3_headshots",
    "map3_deaths":          "map3_deaths",
    "map3_assists":         "map3_assists",
    "map3_adr":             "map3_adr",
}

# Props that require per-MATCH data (not per-map)
MATCH_LEVEL_PROPS = {
    "maps_1_2_kills", "maps_1_2_deaths", "maps_1_2_assists",
    "maps_1_2_adr", "maps_1_2_headshots",
    "map3_kills", "map3_headshots", "map3_deaths", "map3_assists", "map3_adr",
    "maps_1_3_kills", "maps_1_3_headshots",
}

# Map-3-specific props (subset of MATCH_LEVEL_PROPS)
MAP3_PROPS = {"map3_kills", "map3_headshots", "map3_deaths", "map3_assists", "map3_adr"}

# Discrete (Negative-Binomial / Poisson) vs continuous (Gaussian)
COUNT_PROPS = {
    "kills", "deaths", "assists", "first_kills", "clutches_won", "headshots",
    "maps_1_2_kills", "maps_1_2_deaths", "maps_1_2_assists", "maps_1_2_headshots",
    "map3_kills", "map3_headshots", "map3_deaths", "map3_assists",
    "maps_1_3_kills", "maps_1_3_headshots",
}

# Props where we normalise by rounds before projecting
KILLS_CLASS_PROPS = {"kills", "maps_1_2_kills", "map3_kills", "maps_1_3_kills"}

# ── League-average hyper-priors ───────────────────────────────────────────────
# Calibrated to realistic T2/T3 competition (not T1 which is only ~20 teams).
# maps_1_2 figures account for blowout maps averaging ~18-20 rounds each.
HYPER_PRIOR = {
    "kills":                16.0,   # per-map: realistic T2 avg
    "deaths":               14.0,
    "assists":               3.5,
    "adr":                  72.0,
    "headshot_pct":         40.0,
    "headshots":             6.5,   # per-map: ~16 kills × 40% hs rate
    "first_kills":           2.0,
    "clutches_won":          0.4,
    "rating":                1.03,
    "maps_1_2_kills":       27.0,
    "maps_1_2_deaths":      26.0,
    "maps_1_2_assists":      7.0,
    "maps_1_2_adr":         72.0,
    "maps_1_2_headshots":   11.0,   # ~27 kills × 40% hs rate
    "map3_kills":           16.0,   # map 3 → competitive map, not a blowout
    "map3_headshots":        6.5,
    "map3_deaths":          14.0,
    "map3_assists":          3.5,
    "map3_adr":             72.0,
    "maps_1_3_kills":       43.0,   # maps 1-2 (~27) + map 3 (~16)
    "maps_1_3_headshots":   17.5,   # maps 1-2 (~11) + map 3 (~6.5)
}

# ── Kills-per-round hyper-prior (used when normalising) ──────────────────────
KPR_HYPER = 0.63   # was 0.70 — calibrated to T2/T3 global average

# Standard expected rounds per map (before OT) — includes blowouts
EXPECTED_ROUNDS_PER_MAP  = 22.0   # was 26 — blowouts (16-26) pull real avg down
EXPECTED_ROUNDS_2MAPS    = 40.0   # was 52 — realistic M1+M2 avg across all tiers

MIN_SAMPLE   = 12      # was 8 — need more data before trusting the sample mean
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
    if prop_type in MAP3_PROPS:
        kast = log_entry.get("map3_kast", 0) or 0
    elif prop_type in MATCH_LEVEL_PROPS:
        kast = log_entry.get("maps_1_2_kast", 0) or 0
    else:
        kast = log_entry.get("kast", 0) or 0
    if kast <= 0:
        return 1.0  # unknown → neutral
    return 0.7 + 0.6 * (kast / 100.0)  # ranges ~0.7 (0% KAST) → 1.3 (100% KAST)


def _opponent_rank_multiplier(rank: Optional[int], prop_type: str) -> float:
    """
    Opponent rank adjustment — deliberately conservative for kills.

    KEY INSIGHT: For raw kill TOTALS (maps_1_2_kills), two effects nearly cancel:
      • Weaker opponent → more kills PER ROUND (easier duels) → pushes UP
      • Weaker opponent → blowout → FEWER ROUNDS in the match → pushes DOWN
    Net effect on raw kill count is small (±5% max). Larger adjustments were
    the primary driver of projection inflation in back-testing.

    Deaths are less affected by round count (you still die even in blowouts),
    so the rank effect on deaths is slightly larger.
    """
    if not rank or rank <= 0:
        return 1.0

    kills_direction_props = {
        "kills", "maps_1_2_kills", "map3_kills", "maps_1_3_kills",
        "adr", "maps_1_2_adr", "map3_adr", "rating",
        "headshots", "maps_1_2_headshots", "map3_headshots", "maps_1_3_headshots",
    }
    deaths_direction_props = {"deaths", "maps_1_2_deaths", "map3_deaths"}

    # kills/adr/rating — capped at ±6% (blowout-round effect partially cancels)
    if prop_type in kills_direction_props:
        if rank <= 5:   return 0.94   # elite — world-class CT setups, long maps
        if rank <= 10:  return 0.96
        if rank <= 20:  return 0.98
        if rank <= 50:  return 1.0
        if rank <= 100: return 1.02
        if rank <= 200: return 1.04
        return 1.06                   # 200+ = weak opponent (net: small boost)

    # deaths — inverse; less round-count cancellation effect
    if prop_type in deaths_direction_props:
        if rank <= 5:   return 1.08
        if rank <= 10:  return 1.05
        if rank <= 20:  return 1.02
        if rank <= 50:  return 1.0
        if rank <= 100: return 0.98
        if rank <= 200: return 0.96
        return 0.94

    return 1.0  # assists, first_kills, clutches — neutral to opponent rank


def _tournament_tier_multiplier(tier: str, prop_type: str) -> float:
    """
    S-tier events are slower and more structured → lower raw kills.
    C-tier events are chaotic → more kills.
    """
    t = (tier or "").lower()
    kills_direction = prop_type in {
        "kills", "maps_1_2_kills", "map3_kills",
        "adr", "maps_1_2_adr", "map3_adr",
        "headshots", "maps_1_2_headshots", "map3_headshots",
    }
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
    if prop_type not in {"kills", "maps_1_2_kills", "map3_kills", "deaths", "maps_1_2_deaths", "map3_deaths"}:
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

    if prop_type in {"kills", "maps_1_2_kills", "map3_kills"}:
        if ratio > 1.3:   return 1.06, 1.15   # aggressive entry fragger
        if ratio > 1.1:   return 1.03, 1.07
        if ratio < 0.75:  return 0.97, 0.88   # pure support
        if ratio < 0.90:  return 0.99, 0.93
    elif prop_type in {"deaths", "maps_1_2_deaths", "map3_deaths"}:
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
    if prop_type not in {"kills", "maps_1_2_kills", "map3_kills", "deaths", "maps_1_2_deaths", "map3_deaths"}:
        return 1.0

    won_field = "wonMatch" if prop_type in MATCH_LEVEL_PROPS else "wonMap"
    won_vals = [m.get(won_field) for m in logs[:10] if m.get(won_field) is not None]
    if not won_vals:
        return 1.0

    win_rate = sum(1 for w in won_vals if w) / len(won_vals)

    if prop_type in {"kills", "maps_1_2_kills", "map3_kills"}:
        return 0.97 + 0.06 * win_rate   # range: 0.97 (0% wr) → 1.03 (100% wr)

    if prop_type in {"deaths", "maps_1_2_deaths", "map3_deaths"}:
        # Losing teams die more
        return 1.03 - 0.06 * win_rate   # range: 1.03 (0% wr) → 0.97 (100% wr)

    return 1.0


def _round_normalized_projection(
    logs: list,
    prop_type: str,
    prior_mean: float,
    n: int,
    map_name: Optional[str] = None,
) -> float:
    """
    For kills props: normalize by rounds played to get kills/round,
    then scale back by expected rounds for tomorrow's match.
    This is the single most important correction for kill props.
    When map_name is known, use its historically-calibrated round count
    instead of the global average (Upgrade 1: map pool awareness).
    """
    if prop_type not in KILLS_CLASS_PROPS:
        return prior_mean

    if prop_type == "map3_kills":
        kpr_field    = "map3_kpr"
        rounds_field = "map3_rounds"
        is_match     = True   # single-map but comes from match data
    elif prop_type in MATCH_LEVEL_PROPS:
        kpr_field    = "killsPerRound_m1m2"
        rounds_field = "maps_1_2_rounds"
        is_match     = True
    else:
        kpr_field    = "killsPerRound"
        rounds_field = "totalRounds"
        is_match     = False

    kpr_vals    = [m.get(kpr_field, 0) for m in logs if m.get(kpr_field, 0) > 0]
    rounds_vals = [m.get(rounds_field, 0) for m in logs if m.get(rounds_field, 0) > 0]

    if not kpr_vals:
        return prior_mean

    # Career avg KPR with hyper-prior shrinkage
    career_kpr = sum(kpr_vals) / len(kpr_vals)
    alpha      = min(len(kpr_vals), MIN_SAMPLE) / MIN_SAMPLE
    kpr        = alpha * career_kpr + (1 - alpha) * KPR_HYPER

    # Expected rounds: map-specific > recent avg > global default
    map_rounds = _get_map_expected_rounds(map_name, is_match)
    if map_rounds is not None:
        expected_rounds = map_rounds
    elif rounds_vals:
        expected_rounds = sum(rounds_vals) / len(rounds_vals)
    else:
        expected_rounds = EXPECTED_ROUNDS_2MAPS if is_match else EXPECTED_ROUNDS_PER_MAP

    return kpr * expected_rounds


# ── Upgrade 1: Map pool awareness ─────────────────────────────────────────────
# Per-map expected round counts based on historical CT-side advantage data.
# Sources: HLTV map statistics. Used to replace the global average when the
# specific map is announced before the match.
_MAP_ROUNDS: dict[str, float] = {
    "nuke":        22.5,   # heavily CT-sided — short, defenders dominate
    "overpass":    23.5,   # CT-favoured, long rotations
    "vertigo":     23.5,   # elevated CT control
    "ancient":     24.5,   # balanced-CT, slower pace
    "train":       24.0,   # CT-favoured, hard T side
    "inferno":     25.5,   # balanced, slightly CT
    "dust2":       25.0,   # most balanced map in the pool
    "mirage":      26.0,   # balanced, high round count
    "anubis":      26.5,   # T-friendly, aggressive pace
    "cache":       25.0,
    "cobblestone": 26.0,
}


def _get_map_expected_rounds(map_name: Optional[str], is_match: bool) -> Optional[float]:
    """Return map-specific expected round count (or None if map unknown)."""
    if not map_name:
        return None
    clean = map_name.lower().replace("de_", "").strip()
    rounds = _MAP_ROUNDS.get(clean)
    if rounds is None:
        return None
    return rounds * 2 if is_match else rounds


# ── Upgrade 2: Multi-match fatigue ────────────────────────────────────────────

def _fatigue_weight(log_entry: dict, all_logs: list) -> float:
    """
    Downweight momentum entries when a player played multiple matches on the
    same day (e.g. 6 matches in a single tournament day).  Playing back-to-back
    matches degrades performance — these entries are less predictive of a
    well-rested future match.
    """
    date = log_entry.get("date", "")
    if not date:
        return 1.0
    same_day = sum(1 for m in all_logs if m.get("date", "") == date)
    if same_day >= 4:
        return 0.40
    if same_day >= 3:
        return 0.55
    if same_day >= 2:
        return 0.75
    return 1.0


# ── Upgrade 3: H2H weighting ──────────────────────────────────────────────────

def _h2h_momentum_boost(log_entry: dict, opponent_name: Optional[str]) -> float:
    """
    Boost the momentum weight for games played specifically vs this opponent.
    Head-to-head performance is more predictive than general form for the
    specific matchup being priced.
    """
    if not opponent_name:
        return 1.0
    opp_in_log = (log_entry.get("opponent") or "").lower()
    target     = opponent_name.lower()
    if target in opp_in_log or opp_in_log in target:
        return 1.60   # H2H games weighted 60% higher in recent-form calc
    return 1.0


def _h2h_form_multiplier(logs: list, opponent_name: Optional[str], prop_type: str) -> float:
    """
    H2H win rate vs this specific opponent → small projection adjustment.
    Dominant H2H history means the player performs well in this matchup.
    Capped at ±4% — H2H record is meaningful signal, not a decisive one.
    """
    if not opponent_name or prop_type not in {"kills", "maps_1_2_kills"}:
        return 1.0
    target   = opponent_name.lower()
    h2h_logs = [
        m for m in logs
        if target in (m.get("opponent") or "").lower()
        or (m.get("opponent") or "").lower() in target
    ]
    if len(h2h_logs) < 2:
        return 1.0
    won_field = "wonMatch" if prop_type in MATCH_LEVEL_PROPS else "wonMap"
    wins      = sum(1 for m in h2h_logs if m.get(won_field))
    win_rate  = wins / len(h2h_logs)
    if win_rate >= 0.70:
        return 1.04
    if win_rate >= 0.55:
        return 1.02
    if win_rate <= 0.30:
        return 0.96
    if win_rate <= 0.45:
        return 0.98
    return 1.0


# ── Upgrade 4: Opponent current form (H2H kill trend) ─────────────────────────

def _h2h_kill_trend(logs: list, opponent_name: Optional[str], prop_type: str) -> float:
    """
    Look at the player's actual kill totals in H2H games vs this opponent.
    If they consistently over/under-perform vs this team vs their global average,
    apply a small correction.  Requires ≥2 H2H entries to trigger.
    """
    if not opponent_name or prop_type not in {"kills", "maps_1_2_kills"}:
        return 1.0
    target   = opponent_name.lower()
    h2h_logs = [
        m for m in logs
        if target in (m.get("opponent") or "").lower()
        or (m.get("opponent") or "").lower() in target
    ]
    if len(h2h_logs) < 2:
        return 1.0
    field     = CS2_PROPS.get(prop_type, prop_type)
    all_vals  = [float(m.get(field, 0)) for m in logs       if m.get(field) is not None and float(m.get(field, 0)) > 0]
    h2h_vals  = [float(m.get(field, 0)) for m in h2h_logs   if m.get(field) is not None and float(m.get(field, 0)) > 0]
    if not all_vals or not h2h_vals:
        return 1.0
    global_avg = sum(all_vals) / len(all_vals)
    h2h_avg    = sum(h2h_vals) / len(h2h_vals)
    if global_avg <= 0:
        return 1.0
    ratio = h2h_avg / global_avg
    # Cap adjustment at ±5%: if player typically gets 20% more kills vs this team, apply 5% boost
    return max(0.95, min(1.05, 0.50 + 0.50 * ratio))


# ── Upgrade 5: KPR signature variance (AWPer vs rifler detection) ──────────────

def _kpr_signature_variance(logs: list, prop_type: str, std_dev: float) -> float:
    """
    Detect player style from the coefficient of variation (std/mean) of KPR.

    AWPers have bimodal KPR: massive rounds when the rifle is working, near-zero
    when opponents buy counters or win the AWP early.  This is captured by high
    CoV and means the variance on their kill total should be wider.

    Consistent riflers (CoV < 0.25) are more predictable → tighten variance.
    """
    if prop_type not in KILLS_CLASS_PROPS or len(logs) < 5:
        return std_dev
    kpr_field = "killsPerRound_m1m2" if prop_type in MATCH_LEVEL_PROPS else "killsPerRound"
    kpr_vals  = [m.get(kpr_field, 0) for m in logs if (m.get(kpr_field) or 0) > 0]
    if len(kpr_vals) < 5:
        return std_dev
    mean_kpr = sum(kpr_vals) / len(kpr_vals)
    if mean_kpr <= 0:
        return std_dev
    std_kpr  = stats_mod.stdev(kpr_vals)
    cov      = std_kpr / mean_kpr
    if cov > 0.50:        # boom-bust / AWPer
        return std_dev * 1.20
    if cov < 0.25:        # laser-consistent rifler
        return std_dev * 0.88
    return std_dev


# ── Main projection function ──────────────────────────────────────────────────

def compute_cs2_projection(
    map_logs: list,
    prop_type: str,
    line: float,
    opponent_rank: Optional[int] = None,
    tournament_tier: Optional[str] = None,
    opponent_name: Optional[str] = None,
    map_name: Optional[str] = None,
) -> dict:
    """
    5-layer Bayesian CS2 projection.
    map_logs — per-map or per-match stat dicts (newest first).
    Returns a dict compatible with the mlb_engine result shape.
    """
    field = CS2_PROPS.get(prop_type)
    if not field:
        return {"error": f"Unknown CS2 prop: {prop_type}"}

    # ── Maps-1-2 sample quality filter ────────────────────────────────────────
    # "Maps 1-2" props require ≥2 maps played; map3 props require ≥3 maps.
    # Single-map results have structurally incomparable totals — filtering them
    # prevents mispriced lines from blowout 1-map scorelines polluting the average.
    if prop_type in MAP3_PROPS and map_logs:
        # Map-3 filter: only use matches where map 3 was actually played
        map3_logs = [m for m in map_logs if m.get("map3_played") or m.get(CS2_PROPS[prop_type]) is not None]
        if len(map3_logs) >= max(MIN_SAMPLE // 2, 4):
            map_logs = map3_logs
    elif prop_type in MATCH_LEVEL_PROPS and map_logs:
        multi_map_logs = [m for m in map_logs if (m.get("mapsPlayed") or 0) >= 2]
        if len(multi_map_logs) >= max(MIN_SAMPLE // 2, 4):
            map_logs = multi_map_logs

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
    # Upgrade 1 (map awareness): pass map_name so map-specific rounds are used
    if prop_type in KILLS_CLASS_PROPS:
        rn_proj    = _round_normalized_projection(map_logs, prop_type, prior_mean, n, map_name)
        prior_mean = 0.70 * rn_proj + 0.30 * prior_mean

    # ── Layer 2: Momentum (KAST + fatigue + H2H weighted) ────────────────────
    # Upgrade 2 (fatigue): downweight same-day multi-game entries
    # Upgrade 3 (H2H):     upweight games vs this specific opponent
    recent  = values[:len(DECAY)]
    w_vals  = []
    weights = []
    for i, v in enumerate(recent):
        if i >= len(map_logs):
            break
        log_entry = map_logs[i]
        tier_w    = _tier_weight(log_entry.get("tier", ""))
        kast_w    = _kast_weight(log_entry, prop_type)
        fatigue_w = _fatigue_weight(log_entry, map_logs)       # Upgrade 2
        h2h_w     = _h2h_momentum_boost(log_entry, opponent_name)  # Upgrade 3
        decay_w   = DECAY[i]
        w = decay_w * tier_w * kast_w * fatigue_w * h2h_w
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

    # 4e. H2H form multiplier — win rate vs this opponent (Upgrade 3/4)
    h2h_form_mult = _h2h_form_multiplier(map_logs, opponent_name, prop_type)
    projection   *= h2h_form_mult

    # 4f. H2H kill trend — actual kills vs this opponent vs global avg (Upgrade 4)
    h2h_trend_mult = _h2h_kill_trend(map_logs, opponent_name, prop_type)
    projection    *= h2h_trend_mult

    projection = max(projection, 0.0)

    # ── Variance estimation ───────────────────────────────────────────────────
    if n >= 2:
        std_dev = stats_mod.stdev(values)
    else:
        std_dev = projection * 0.35

    # Small sample → inflate variance (we're less certain about true mean)
    if n < 8:
        std_dev *= 1.30
    elif n < 12:
        std_dev *= 1.15

    # KAST consistency: high KAST → lower variance (reliable)
    kast_vals = [
        m.get("maps_1_2_kast" if prop_type in MATCH_LEVEL_PROPS else "kast", 0) or 0
        for m in map_logs[:10]
    ]
    avg_kast = sum(kast_vals) / max(len([k for k in kast_vals if k > 0]), 1)
    if avg_kast >= 75:
        std_dev *= 0.88
    elif avg_kast <= 55 and avg_kast > 0:
        std_dev *= 1.25

    # Entry fragger variance adjustment
    std_dev *= fd_var_mult

    # Upgrade 5: KPR signature — AWPer boom-bust vs laser-consistent rifler
    std_dev = _kpr_signature_variance(map_logs, prop_type, std_dev)

    std_dev = max(std_dev, 1.5)

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

    # CS2 confidence levels — deliberately conservative thresholds.
    # CS2 kill totals have very high variance (blowouts, eco rounds, map veto).
    # Require ≥12 samples AND strong MC probability for High confidence.
    # "High" in CS2 is equivalent to "Medium" in soccer/MLB.
    if conf_score >= 73 and n >= 12:
        conf_level = "High"
    elif conf_score >= 63 and n >= 6:
        conf_level = "Medium"
    else:
        conf_level = "Low"

    # Hard cap on displayed confidence — CS2 is too volatile for 80%+ confidence
    conf_score = min(conf_score, 75.0)

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

    # KPR CoV for display
    kpr_cov = None
    if len(kpr_vals) >= 5 and (sum(kpr_vals) / len(kpr_vals)) > 0:
        mean_kpr = sum(kpr_vals) / len(kpr_vals)
        try:
            kpr_cov = round(stats_mod.stdev(kpr_vals) / mean_kpr, 3)
        except Exception:
            pass

    # H2H summary for display
    target_opp  = (opponent_name or "").lower()
    h2h_entries = [m for m in map_logs if target_opp and (
        target_opp in (m.get("opponent") or "").lower() or
        (m.get("opponent") or "").lower() in target_opp
    )]
    h2h_n    = len(h2h_entries)
    h2h_avg  = None
    if h2h_entries:
        field_key = CS2_PROPS.get(prop_type, prop_type)
        h2h_vals  = [float(m.get(field_key, 0)) for m in h2h_entries if m.get(field_key) is not None]
        h2h_avg   = round(sum(h2h_vals) / len(h2h_vals), 1) if h2h_vals else None

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
            # New upgrade signals
            "h2hFormMult":           round(h2h_form_mult, 3),
            "h2hKillTrendMult":      round(h2h_trend_mult, 3),
            "h2hGames":              h2h_n,
            "h2hAvgKills":           h2h_avg,
            "kprCoV":                kpr_cov,
            "mapAwareness":          map_name or None,
            "mapExpectedRounds":     _get_map_expected_rounds(map_name, prop_type in MATCH_LEVEL_PROPS),
            "fatigueActive":         any(_fatigue_weight(m, map_logs) < 1.0 for m in map_logs[:8]),
        },
    }
