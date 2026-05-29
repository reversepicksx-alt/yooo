"""
WTA Tennis projection engine — Bayesian, surface-aware.
Mirrors cs2_engine / mlb_engine structure: prior mean × momentum mean → posterior,
opponent-rank multiplier, surface multiplier, round multiplier → final projection.
Output: projection, pOver, pUnder, recommendation, confidence, tactical metrics.
"""
import math
import statistics
from typing import Optional
from bayesian_engine import _monte_carlo_probability as _baye_mc

# Supported prop types
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

# Surface league averages (approx total games / match for women)
SURFACE_BASELINE_GAMES = {"Hard": 20.5, "Clay": 21.0, "Grass": 19.5}

# Round multipliers — deep rounds → tighter matches → slightly more games
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


def _surface_mult(this_surface: str, recent_logs: list, prop_type: str) -> float:
    """
    Adjust for surface mismatch — if recent matches are mostly Clay but this
    one's Hard, expect totals to shift toward the Hard baseline.
    Only meaningful for game/total props.
    """
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
    return this_base / avg_recent


def _opp_rank_mult(opp_rank: Optional[int], subject_rank: Optional[int], prop_type: str) -> float:
    """
    Bigger ranking gap → quicker matches (heavy favorite breaks more).
    For player_games_won + match_winner: favored player gets a boost,
    underdog gets compressed.
    """
    if not opp_rank or opp_rank <= 0:
        return 1.0
    if prop_type in BINARY_PROPS:
        if not subject_rank:
            return 1.0
        gap = opp_rank - subject_rank  # +ve = subject is favorite
        # logistic-ish: cap at ±0.30
        return max(0.70, min(1.30, 1.0 + gap * 0.005))
    # game-count props: very large ranking gap → fewer total games
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
    # Map win_rate 0.0-1.0 → multiplier 0.92-1.08
    return 0.92 + win_rate * 0.16


def _bayesian_posterior(prior_mean: float, momentum_mean: float, n_recent: int, prior_n: float = 8.0) -> float:
    """Posterior mean = prior-weighted blend of long-term and recent form."""
    if n_recent <= 0:
        return prior_mean
    w_recent = n_recent / (n_recent + prior_n)
    return prior_mean * (1 - w_recent) + momentum_mean * w_recent


def _prob_over_normal(projection: float, line: float, std: float) -> float:
    if std <= 0.01:
        return 1.0 if projection > line else 0.0
    # Continuity-corrected normal approx — line+0.5 because lines are .5
    z = (projection - line) / std
    # Standard normal CDF via erf
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def compute_wta_projection(
    match_logs: list,
    prop_type: str,
    line: float,
    surface: Optional[str]   = None,
    round_name: Optional[str] = None,
    opp_rank: Optional[int]   = None,
    subject_rank: Optional[int] = None,
    h2h: Optional[dict]       = None,
    subject_is_p1: bool       = True,
) -> dict:
    if prop_type not in WTA_PROPS:
        return {"error": "unknown_prop"}
    field = WTA_PROPS[prop_type]

    # Extract numeric series (filter None / non-numeric)
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

    # ── Binary props: hit-rate based ───────────────────────────────────────
    if prop_type in BINARY_PROPS:
        hits         = sum(1 for v in series if v >= 0.5)
        n            = len(series)
        prior        = 0.5
        prior_n      = 6
        win_rate     = (hits + prior * prior_n) / (n + prior_n)
        # Apply opponent rank + H2H multipliers
        win_rate *= _opp_rank_mult(opp_rank, subject_rank, prop_type)
        win_rate *= _h2h_mult(h2h, subject_is_p1, prop_type)
        win_rate  = max(0.05, min(0.95, win_rate))
        # Line is typically 0.5 for binary; "OVER" = subject wins
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
            "momentumMean":    round(sum(series[:5]) / min(5, n), 3) if n else 0.0,
            "sampleSize":      n,
            "streakFlag":      "",
            "tacticalMetrics": {
                "h2hWins":      (h2h or {}).get("p1Wins" if subject_is_p1 else "p2Wins", 0),
                "h2hLosses":    (h2h or {}).get("p2Wins" if subject_is_p1 else "p1Wins", 0),
                "surfaceMult":  1.0,
                "oppRankMult":  round(_opp_rank_mult(opp_rank, subject_rank, prop_type), 3),
                "roundMult":    1.0,
            },
        }

    # ── Continuous (game / set counts) ─────────────────────────────────────
    prior_mean    = sum(series) / len(series)
    recent        = series[:5]
    momentum_mean = sum(recent) / len(recent)
    posterior     = _bayesian_posterior(prior_mean, momentum_mean, n_recent=len(recent))

    surface_mult = _surface_mult(surface or "", match_logs, prop_type)
    round_mult   = _round_mult(round_name or "")
    opp_mult     = _opp_rank_mult(opp_rank, subject_rank, prop_type)
    h2h_mult     = _h2h_mult(h2h, subject_is_p1, prop_type)

    projection = posterior * surface_mult * round_mult * opp_mult * h2h_mult

    # Whole-number rounding for count props (games, sets)
    is_count = prop_type in ("total_games", "player_games_won", "opponent_games_won",
                             "total_sets", "player_sets_won",
                             "set_1_total_games", "set_1_player_games")
    if is_count:
        projection = round(projection)

    # Stdev for over/under probability
    std = statistics.pstdev(series) if len(series) >= 2 else 1.0
    std = max(std, 1.0)
    mc_variance   = std ** 2
    _po, _pu, _, _ = _baye_mc(
        mean=projection, std=std, line=line,
        n_sims=10_000, is_count_stat=is_count, variance=mc_variance,
    )
    p_over  = round(_po * 100, 1)
    p_under = round(_pu * 100, 1)
    rec        = "over" if p_over >= p_under else "under"
    conf       = round(max(p_over, p_under), 0)
    level      = "High" if conf >= 70 else ("Medium" if conf >= 60 else "Low")

    # Hot/cold streak flag
    streak_flag = ""
    if len(recent) >= 3:
        last3_avg = sum(recent[:3]) / 3
        if last3_avg > prior_mean * 1.10:
            streak_flag = "hot"
        elif last3_avg < prior_mean * 0.90:
            streak_flag = "cold"

    return {
        "projection":      float(projection),
        "pOver":           p_over,
        "pUnder":          p_under,
        "recommendation":  rec,
        "confidenceScore": int(conf),
        "confidenceLevel": level,
        "priorMean":       round(prior_mean, 2),
        "momentumMean":    round(momentum_mean, 2),
        "sampleSize":      len(series),
        "streakFlag":      streak_flag,
        "tacticalMetrics": {
            "surfaceMult":   round(surface_mult, 3),
            "roundMult":     round(round_mult, 3),
            "oppRankMult":   round(opp_mult, 3),
            "h2hMult":       round(h2h_mult, 3),
            "surface":       surface,
            "round":         round_name,
            "h2hWins":       (h2h or {}).get("p1Wins" if subject_is_p1 else "p2Wins", 0),
            "h2hLosses":     (h2h or {}).get("p2Wins" if subject_is_p1 else "p1Wins", 0),
        },
    }
