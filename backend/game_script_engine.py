"""
GAME SCRIPT ENGINE

Pure function: bookmaker odds → probability vector over likely game scripts.

Outputs probabilities for the scenarios the cheat-sheet conditions on:
  draw, home_blowout, away_blowout, low_scoring, high_scoring, open_close

Math:
  1. Convert 1×2 odds → overround-normalised win probabilities
  2. Derive expected total goals from totals odds if available, else use
     the supplied fallback (typically game_tempo.expectedTotalGoals or
     league avg 2.6)
  3. Solve for individual team goal expectancies (lambda_home, lambda_away)
     from (P_home, P_away, P_draw) and expected_total via a simple
     bivariate-Poisson approximation
  4. Enumerate (h, a) ∈ {0..6}² with independent Poisson(λ_h) × Poisson(λ_a)
     and bucket into scenarios

This module has NO I/O. No DB, no API, no LLM. Pure deterministic math so
it can be unit-tested and called from the request hot-path safely.
"""
from __future__ import annotations
from math import exp, factorial
from typing import Optional


SCENARIOS = (
    "draw",
    "home_blowout",
    "away_blowout",
    "low_scoring",
    "high_scoring",
    "open_close",
)


def _poisson(k: int, lam: float) -> float:
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return (lam ** k) * exp(-lam) / factorial(k)


def _normalise_1x2(home_odds: float, draw_odds: float, away_odds: float) -> tuple:
    """Decimal odds → overround-normalised win probabilities."""
    try:
        ph = 1.0 / max(float(home_odds), 1.01)
        pd = 1.0 / max(float(draw_odds), 1.01)
        pa = 1.0 / max(float(away_odds), 1.01)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    z = ph + pd + pa
    if z <= 0:
        return None
    return ph / z, pd / z, pa / z


def _solve_lambdas(p_home: float, p_away: float, expected_total: float) -> tuple:
    """
    Estimate (lambda_home, lambda_away) such that:
      lambda_home + lambda_away ≈ expected_total
      lambda_home / lambda_away ≈ implied team strength ratio

    Uses a closed-form approximation that's good enough for scenario
    bucketing (it does not need to be a true MLE).
    """
    expected_total = max(0.4, min(6.0, float(expected_total)))
    # Strength ratio: more home win prob (vs away) → larger λ_home share
    # Cap at 0.85/0.15 so we don't produce degenerate scripts on extreme odds
    home_share = p_home / max(p_home + p_away, 1e-6)
    home_share = max(0.20, min(0.80, home_share))
    lam_h = expected_total * home_share
    lam_a = expected_total * (1.0 - home_share)
    return round(lam_h, 3), round(lam_a, 3)


def _bucket(h: int, a: int) -> str:
    """Classify a final score (h, a) into a scenario bucket."""
    diff = h - a
    total = h + a
    if diff == 0:
        return "draw"
    if diff >= 3:
        return "home_blowout"
    if diff <= -3:
        return "away_blowout"
    if total <= 2:
        return "low_scoring"
    if total >= 4:
        return "high_scoring"
    return "open_close"


def compute_scenario_probs(
    bookmaker_odds: Optional[dict],
    expected_total_fallback: Optional[float] = None,
) -> dict:
    """
    Main entry point. Returns:
      {
        "available": bool,
        "P_draw": float, "P_home_blowout": float, ...,
        "expectedTotal": float,
        "lambdaHome": float, "lambdaAway": float,
        "impliedHome": float, "impliedAway": float, "impliedDraw": float,
      }

    If odds are missing/malformed, returns {"available": False, ...} with
    inert (uniform) probabilities so callers can still consume safely.
    """
    inert = {"available": False}
    for s in SCENARIOS:
        inert[f"P_{s}"] = 1.0 / len(SCENARIOS)
    inert.update({
        "expectedTotal": float(expected_total_fallback or 2.6),
        "lambdaHome": 0.0, "lambdaAway": 0.0,
        "impliedHome": 0.0, "impliedAway": 0.0, "impliedDraw": 0.0,
    })

    if not bookmaker_odds:
        return inert
    home_odds = bookmaker_odds.get("homeWin")
    away_odds = bookmaker_odds.get("awayWin")
    draw_odds = bookmaker_odds.get("draw")
    if home_odds is None or away_odds is None:
        return inert
    if draw_odds is None:
        draw_odds = 3.5  # safe fallback

    probs = _normalise_1x2(home_odds, draw_odds, away_odds)
    if probs is None:
        return inert
    p_home, p_draw, p_away = probs

    expected_total = float(expected_total_fallback or 2.6)

    lam_h, lam_a = _solve_lambdas(p_home, p_away, expected_total)

    # Enumerate score grid 0..6 × 0..6 (covers >99.9% of mass for λ < 4)
    GRID = 7
    bucket_mass = {s: 0.0 for s in SCENARIOS}
    total_mass = 0.0
    for h in range(GRID):
        ph = _poisson(h, lam_h)
        for a in range(GRID):
            pa = _poisson(a, lam_a)
            mass = ph * pa
            bucket_mass[_bucket(h, a)] += mass
            total_mass += mass

    # Renormalise to handle the small grid-truncation error
    if total_mass > 0:
        for s in SCENARIOS:
            bucket_mass[s] /= total_mass

    out = {"available": True}
    for s in SCENARIOS:
        out[f"P_{s}"] = round(bucket_mass[s], 4)
    out.update({
        "expectedTotal": round(expected_total, 2),
        "lambdaHome":    lam_h,
        "lambdaAway":    lam_a,
        "impliedHome":   round(p_home, 3),
        "impliedAway":   round(p_away, 3),
        "impliedDraw":   round(p_draw, 3),
    })
    return out


def bucket_from_final_score(home_goals: int, away_goals: int) -> str:
    """Public helper for backfill / settle code: classify a known final score."""
    try:
        return _bucket(int(home_goals), int(away_goals))
    except (TypeError, ValueError):
        return "open_close"


def expected_total_from_game_tempo(game_tempo: Optional[dict]) -> Optional[float]:
    """Convenience: pull expectedTotalGoals out of an existing game_tempo dict."""
    if not isinstance(game_tempo, dict):
        return None
    v = game_tempo.get("expectedTotalGoals")
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None
