"""
WTA Tennis projection engine v2 — Elite Edition.
Mirrors soccer/cs2/mlb engine structure with full Bayesian layers.

  Layer 1:  PRIOR           — Career mean + hyper-prior shrinkage (n<10 protection)
  Layer 2:  MOMENTUM        — Exponential decay over recent matches (newest first)
  Layer 3:  PER-SET NORM    — Normalize game counts by sets played (like per-90 in soccer)
  Layer 4:  MATCHUP         — Surface, round, opponent rank, H2H multipliers
  Layer 5:  FATIGUE         — Rest days penalty/boost
  Layer 6:  TOURNAMENT TIER — Grand Slam vs WTA 1000/500/250/ITF context
  Layer 7:  MC SIMULATION   — Shared Bayesian NB / Gaussian engine
"""
import math
import statistics
from typing import Optional
from bayesian_engine import _monte_carlo_probability as _baye_mc

# ── Supported prop types ──────────────────────────────────────────────────────
WTA_PROPS = {
    "total_games":        "totalGames",
    "player_games_won":   "playerGamesWon",
    "opponent_games_won": "opponentGamesWon",
    "total_sets":         "setsPlayed",
    "player_sets_won":    "setsWon",
    "set_1_total_games":  "set1Total",
    "set_1_player_games": "set1PlayerGames",
    "match_winner":       "wonMatch",
    "first_set_winner":   "set1WinnerSubject",
}

BINARY_PROPS = {"match_winner", "first_set_winner"}

# Props where per-set normalization makes sense
PER_SET_PROPS = {
    "total_games", "player_games_won", "opponent_games_won",
}

# Discrete count props (use NB in MC)
COUNT_PROPS = {
    "total_games", "player_games_won", "opponent_games_won",
    "total_sets", "player_sets_won",
    "set_1_total_games", "set_1_player_games",
}

PROP_LABELS = {
    "total_games":        "Total Games (Match)",
    "player_games_won":   "Player Games Won",
    "opponent_games_won": "Opponent Games Won",
    "total_sets":         "Total Sets",
    "player_sets_won":    "Player Sets Won",
    "set_1_total_games":  "Set 1 Total Games",
    "set_1_player_games": "Set 1 Player Games",
    "match_winner":       "Match Winner",
    "first_set_winner":   "First Set Winner",
}

# ── Hyper-priors (WTA tour averages) ─────────────────────────────────────────
# Games per set averages derived from WTA stats (2022-2024)
HYPER_PRIOR = {
    "total_games":        20.8,   # avg total games per match
    "player_games_won":   10.2,
    "opponent_games_won": 10.2,
    "total_sets":          2.3,
    "player_sets_won":     1.2,
    "set_1_total_games":  10.1,
    "set_1_player_games":  5.0,
    # Per-set rates (used internally for normalization)
    "_games_per_set":      9.0,   # typical games per set in WTA
    "_player_gps":         4.4,
    "_opp_gps":            4.4,
}

WTA_AVG_SETS = 2.32   # WTA tour average sets per match (source: Tennis Abstract 2022-24)

# ── Momentum decay weights (newest first) ────────────────────────────────────
# More aggressive recency weighting than a flat mean — recent form matters in tennis
DECAY = [1.0, 0.82, 0.67, 0.55, 0.45, 0.36, 0.29, 0.23]

# ── Surface league averages ───────────────────────────────────────────────────
SURFACE_BASELINE_GAMES = {"Hard": 20.5, "Clay": 21.0, "Grass": 19.5, "Indoor Hard": 20.3}
SURFACE_BASELINE_GPS   = {k: v / WTA_AVG_SETS for k, v in SURFACE_BASELINE_GAMES.items()}

# ── Round multipliers ─────────────────────────────────────────────────────────
ROUND_MULT = {
    "F":   1.06, "Final": 1.06,
    "SF":  1.05, "Semifinal": 1.05, "Semi-Final": 1.05,
    "QF":  1.04, "Quarterfinal": 1.04, "Quarter-Final": 1.04,
    "R16": 1.02, "Round of 16": 1.02,
    "R32": 1.00,
    "R64": 0.99,
    "R128": 0.97,
    "Qualifying": 0.95,
}

# ── Tournament tier multipliers ───────────────────────────────────────────────
# Grand Slams (5-set capable on men's side, best-of-3 women's) = more intense, 
# higher calibre opponents, slower courts (Roland Garros clay, Wimbledon grass)
TOURNAMENT_MULT = {
    "grand slam": 1.04,
    "wta 1000":   1.02,
    "wta 500":    1.01,
    "wta 250":    1.00,
    "itf":        0.97,
    "challenger": 0.98,
}


def _round_mult(r: str) -> float:
    if not r:
        return 1.0
    r = r.strip()
    if r in ROUND_MULT:
        return ROUND_MULT[r]
    for k, v in ROUND_MULT.items():
        if k.lower() in r.lower():
            return v
    return 1.0


def _tournament_mult(tier: Optional[str], prop_type: str) -> float:
    if not tier or prop_type in BINARY_PROPS:
        return 1.0
    key = tier.strip().lower()
    for k, v in TOURNAMENT_MULT.items():
        if k in key:
            return v
    return 1.0


def _surface_mult(this_surface: str, recent_logs: list, prop_type: str) -> float:
    if not this_surface or prop_type in BINARY_PROPS:
        return 1.0
    this_base = SURFACE_BASELINE_GAMES.get(this_surface)
    if not this_base:
        return 1.0
    recent_bases = [SURFACE_BASELINE_GAMES.get(m.get("surface", ""))
                    for m in recent_logs if m.get("surface")]
    recent_bases = [b for b in recent_bases if b]
    if not recent_bases:
        return 1.0
    avg_recent = sum(recent_bases) / len(recent_bases)
    if avg_recent <= 0:
        return 1.0
    mult = this_base / avg_recent
    return max(0.90, min(1.10, mult))


def _opp_rank_mult(opp_rank: Optional[int], subject_rank: Optional[int], prop_type: str) -> float:
    if not opp_rank or opp_rank <= 0:
        return 1.0
    if prop_type in BINARY_PROPS:
        if not subject_rank:
            return 1.0
        gap = opp_rank - subject_rank
        return max(0.70, min(1.30, 1.0 + gap * 0.005))
    if prop_type in ("total_games", "set_1_total_games") and subject_rank:
        gap = abs(opp_rank - subject_rank)
        if gap >= 50:
            return 0.93
        if gap >= 25:
            return 0.96
    return 1.0


def _h2h_mult(h2h: Optional[dict], subject_is_p1: bool, prop_type: str) -> float:
    if not h2h or prop_type not in ("player_games_won", "match_winner", "first_set_winner"):
        return 1.0
    p1w = h2h.get("p1Wins", 0)
    p2w = h2h.get("p2Wins", 0)
    total = p1w + p2w
    if total < 2:
        return 1.0
    subj_w = p1w if subject_is_p1 else p2w
    win_rate = subj_w / total
    return 0.92 + win_rate * 0.16


def _fatigue_mult(rest_days: Optional[int], prop_type: str) -> float:
    """
    Days since player's last match → performance multiplier.
    Back-to-back in tennis is a documented performance reducer.
    Source: Tennis Abstract fatigue analysis (Kovalchik, 2019).
    """
    if rest_days is None or prop_type in BINARY_PROPS:
        return 1.0
    if rest_days <= 1:
        return 0.94   # back-to-back: physically depleted
    if rest_days == 2:
        return 0.97   # 1 clear day: still tired
    if rest_days >= 6:
        return 1.02   # well rested: slight boost
    return 1.0        # 3-5 days: normal


def _decay_weighted_mean(values: list, weights: list) -> float:
    """Weighted mean using decay weights, trimmed to available data."""
    n = min(len(values), len(weights))
    if n == 0:
        return 0.0
    w_vals   = [values[i] * weights[i] for i in range(n)]
    total_w  = sum(weights[:n])
    return sum(w_vals) / total_w if total_w > 0 else sum(values[:n]) / n


def _extract_per_set_series(match_logs: list, field: str, count_field: str) -> tuple:
    """
    For game-count props, normalize each match value by sets played.
    Returns (per_set_series, sets_list) where per_set_series[i] = games/set that match.
    This mirrors soccer's per-90 normalization.
    """
    per_set = []
    raw_vals = []
    sets_list = []
    for m in match_logs:
        v = m.get(field)
        s = m.get("setsPlayed") or m.get("totalSets")
        if v is None or s is None:
            continue
        try:
            v = float(v)
            s = float(s)
        except (TypeError, ValueError):
            continue
        if s < 1:
            continue
        per_set.append(v / s)
        raw_vals.append(v)
        sets_list.append(s)
    return per_set, raw_vals, sets_list


def compute_wta_projection(
    match_logs: list,
    prop_type: str,
    line: float,
    surface: Optional[str]        = None,
    round_name: Optional[str]     = None,
    opp_rank: Optional[int]       = None,
    subject_rank: Optional[int]   = None,
    h2h: Optional[dict]           = None,
    subject_is_p1: bool           = True,
    rest_days: Optional[int]      = None,
    tournament_tier: Optional[str] = None,
    expected_sets: Optional[float] = None,
) -> dict:
    if prop_type not in WTA_PROPS:
        return {"error": "unknown_prop"}
    field = WTA_PROPS[prop_type]

    # ── Extract numeric series ────────────────────────────────────────────────
    series = []
    for m in match_logs:
        v = m.get(field)
        if v is None:
            continue
        try:
            series.append(float(v))
        except (TypeError, ValueError):
            continue

    if len(series) < 3:
        return {"error": "insufficient_data", "sampleSize": len(series)}

    n = len(series)

    # ── Binary props: hit-rate based ──────────────────────────────────────────
    if prop_type in BINARY_PROPS:
        hits     = sum(1 for v in series if v >= 0.5)
        prior    = 0.5
        prior_n  = 8
        win_rate = (hits + prior * prior_n) / (n + prior_n)
        win_rate *= _opp_rank_mult(opp_rank, subject_rank, prop_type)
        win_rate *= _h2h_mult(h2h, subject_is_p1, prop_type)
        win_rate  = max(0.05, min(0.95, win_rate))
        p_over    = round(win_rate * 100, 1)
        p_under   = round((1 - win_rate) * 100, 1)
        rec       = "over" if p_over >= p_under else "under"
        conf      = round(max(p_over, p_under), 0)
        level     = "High" if conf >= 70 else ("Medium" if conf >= 60 else "Low")
        return {
            "projection":      round(win_rate, 3),
            "pOver":           p_over,
            "pUnder":          p_under,
            "recommendation":  rec,
            "confidenceScore": int(conf),
            "confidenceLevel": level,
            "priorMean":       round(hits / n, 3),
            "momentumMean":    round(sum(series[:5]) / min(5, n), 3),
            "sampleSize":      n,
            "streakFlag":      "",
            "tacticalMetrics": {
                "h2hWins":      (h2h or {}).get("p1Wins" if subject_is_p1 else "p2Wins", 0),
                "h2hLosses":    (h2h or {}).get("p2Wins" if subject_is_p1 else "p1Wins", 0),
                "surfaceMult":  1.0,
                "oppRankMult":  round(_opp_rank_mult(opp_rank, subject_rank, prop_type), 3),
                "roundMult":    1.0,
                "fatigueMult":  1.0,
                "tournamentMult": 1.0,
            },
        }

    # ── Continuous / count props ──────────────────────────────────────────────

    # ── LAYER 1: PRIOR with hyper-prior shrinkage ─────────────────────────────
    # Small sample players are shrunk toward tour average
    raw_mean   = sum(series) / n
    hyper      = HYPER_PRIOR.get(prop_type, raw_mean)
    # Shrinkage weight: 0 games = 100% hyper, 10+ games = 0% hyper
    alpha      = min(n, 10) / 10.0
    prior_mean = alpha * raw_mean + (1 - alpha) * hyper

    # ── LAYER 2: MOMENTUM with exponential decay ──────────────────────────────
    recent = series[:len(DECAY)]
    momentum_mean = _decay_weighted_mean(recent, DECAY[:len(recent)])

    # Blend prior and momentum — more data → trust momentum more (cap 60%)
    blend      = min(n / 10.0, 0.60)
    blended    = (1 - blend) * prior_mean + blend * momentum_mean

    # ── LAYER 3: PER-SET NORMALIZATION ───────────────────────────────────────
    # For game-count props (total_games, player_games_won, opponent_games_won),
    # normalize by sets played so a 2-set match and 3-set match are comparable.
    exp_sets = expected_sets or WTA_AVG_SETS
    if prop_type in PER_SET_PROPS:
        per_set, raw_vals, sets_list = _extract_per_set_series(match_logs, field, "setsPlayed")
        if len(per_set) >= 3:
            ps_raw      = sum(per_set) / len(per_set)
            ps_hyper    = HYPER_PRIOR.get(f"_{field.replace('Games','').replace('Won','')}_gps",
                                           HYPER_PRIOR.get("_games_per_set", 9.0))
            ps_alpha    = min(len(per_set), 10) / 10.0
            ps_prior    = ps_alpha * ps_raw + (1 - ps_alpha) * ps_hyper
            ps_recent   = per_set[:len(DECAY)]
            ps_momentum = _decay_weighted_mean(ps_recent, DECAY[:len(ps_recent)])
            ps_blend    = min(len(per_set) / 10.0, 0.60)
            ps_blended  = (1 - ps_blend) * ps_prior + ps_blend * ps_momentum
            # Denormalize by expected sets for this match
            blended     = ps_blended * exp_sets

    projection = blended

    # ── LAYER 4: MATCHUP MULTIPLIERS ─────────────────────────────────────────
    surface_mult  = _surface_mult(surface or "", match_logs, prop_type)
    round_mult_v  = _round_mult(round_name or "")
    opp_mult      = _opp_rank_mult(opp_rank, subject_rank, prop_type)
    h2h_mult      = _h2h_mult(h2h, subject_is_p1, prop_type)
    projection   *= surface_mult * round_mult_v * opp_mult * h2h_mult

    # ── LAYER 5: FATIGUE ─────────────────────────────────────────────────────
    fatigue_mult  = _fatigue_mult(rest_days, prop_type)
    projection   *= fatigue_mult

    # ── LAYER 6: TOURNAMENT TIER ─────────────────────────────────────────────
    tournament_mult = _tournament_mult(tournament_tier, prop_type)
    projection     *= tournament_mult

    # ── Rounding for count props ──────────────────────────────────────────────
    is_count = prop_type in COUNT_PROPS
    if is_count:
        projection = round(projection)
    else:
        projection = round(projection, 2)

    # ── LAYER 7: MONTE CARLO — shared Bayesian engine ────────────────────────
    # Standard deviation from historical series
    std = statistics.pstdev(series) if n >= 2 else 1.0
    std = max(std, 1.0)
    # Per-set normalization also refines std
    if prop_type in PER_SET_PROPS and len(per_set if 'per_set' in dir() else []) >= 3:
        ps_std = statistics.pstdev(per_set) if len(per_set) >= 2 else 1.0
        # Denormalize per-set std to match-level
        std = max(ps_std * exp_sets, 1.0)

    mc_variance   = std ** 2
    _po, _pu, _, _ = _baye_mc(
        mean=float(projection), std=std, line=line,
        n_sims=10_000, is_count_stat=is_count, variance=mc_variance,
    )
    p_over  = round(_po * 100, 1)
    p_under = round(_pu * 100, 1)
    rec     = "over" if p_over >= p_under else "under"
    conf    = round(max(p_over, p_under), 0)
    level   = "High" if conf >= 70 else ("Medium" if conf >= 60 else "Low")

    # ── LOW CONVICTION FILTER ─────────────────────────────────────────────────
    low_conviction = False
    if max(p_over, p_under) < 60.0:
        low_conviction = True
        conf           = min(conf, 54.0)
        level          = "Low"

    # ── Streak detection ──────────────────────────────────────────────────────
    streak_flag = ""
    if n >= 3:
        last3_avg = sum(series[:3]) / 3
        if last3_avg > prior_mean * 1.12:
            streak_flag = "hot"
        elif last3_avg < prior_mean * 0.88:
            streak_flag = "cold"

    return {
        "projection":      float(projection),
        "pOver":           p_over,
        "pUnder":          p_under,
        "recommendation":  rec,
        "confidenceScore": int(conf),
        "confidenceLevel": level,
        "lowConviction":   low_conviction,
        "priorMean":       round(prior_mean, 2),
        "momentumMean":    round(momentum_mean, 2),
        "sampleSize":      n,
        "streakFlag":      streak_flag,
        "tacticalMetrics": {
            "surfaceMult":    round(surface_mult, 3),
            "roundMult":      round(round_mult_v, 3),
            "oppRankMult":    round(opp_mult, 3),
            "h2hMult":        round(h2h_mult, 3),
            "fatigueMult":    round(fatigue_mult, 3),
            "tournamentMult": round(tournament_mult, 3),
            "surface":        surface,
            "round":          round_name,
            "tournamentTier": tournament_tier,
            "expectedSets":   round(exp_sets, 2),
            "h2hWins":        (h2h or {}).get("p1Wins" if subject_is_p1 else "p2Wins", 0),
            "h2hLosses":      (h2h or {}).get("p2Wins" if subject_is_p1 else "p1Wins", 0),
        },
    }
