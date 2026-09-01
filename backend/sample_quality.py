"""
Sample-quality filtering for Bayesian priors.

Two independent layers:

1. CONTEXTUAL FILTER ("luck strip") — drops games where the game-state itself
   was distorted (blowouts, garbage-time cameos).  Binary: keep or drop.

2. MAGNITUDE FILTER ("outlier weights") — bidirectional continuous weights that
   downweight any game whose stat is a statistical outlier relative to the
   player's own distribution, regardless of whether it was an upside surprise
   (freakish game vs weak opponent) or a downside collapse (horrible game vs
   elite press).

   A player who averaged 55 passes but had a 12-pass nightmare vs Atletico's
   press, or a 107-pass carnival vs a relegated side, should not pull the prior
   equally with his 50-60 normal games.  MAD-based z-scores are used because
   MAD itself is resistant to the outliers being measured.

Both layers are conservative: we never reduce the effective sample below the
minimum floor, and borderline values are only softly discounted, not dropped.
"""
from typing import List, Tuple
import statistics as _stats


_MIN_RETAINED = 6  # never drop a sample if it would leave fewer than this


_MAD_SCALE = 1.4826   # makes MAD comparable to std-dev for normal distributions


def magnitude_outlier_weights(values: List[float], min_samples: int = 4) -> List[float]:
    """
    Bidirectional outlier weights using MAD (Median Absolute Deviation).

    For each value in the list, return a weight in (0.25, 1.0] reflecting how
    representative that game is of the player's true level.  Values far from
    the group median — in EITHER direction — receive a reduced weight.

    Uses MAD rather than std-dev because MAD is itself resistant to the outliers
    we are trying to detect (std-dev gets inflated by the very values we want to
    downweight, masking other anomalies).

    Weight table (z_mad = |value − median| / (1.4826 × MAD)):
      z_mad < 1.5  → 1.00  (normal game — full weight)
      1.5 ≤ z_mad < 2.0 → 0.70  (mild outlier)
      2.0 ≤ z_mad < 2.5 → 0.45  (moderate outlier)
      z_mad ≥ 2.5       → 0.25  (strong outlier — barely influences prior)

    Returns all 1.0 weights when:
      - Fewer than min_samples values (not enough data to judge)
      - MAD = 0 (all values identical — nothing to flag)
    """
    n = len(values)
    if n < min_samples:
        return [1.0] * n

    median = _stats.median(values)
    deviations = [abs(v - median) for v in values]
    mad = _stats.median(deviations)

    if mad == 0:
        return [1.0] * n

    robust_std = _MAD_SCALE * mad
    weights = []
    for v in values:
        z = abs(v - median) / robust_std
        if z < 1.5:
            weights.append(1.00)
        elif z < 2.0:
            weights.append(0.70)
        elif z < 2.5:
            weights.append(0.45)
        else:
            weights.append(0.25)

    return weights


def magnitude_outlier_notes(values: List[float], min_samples: int = 4) -> List[str]:
    """
    Returns a human-readable note for each game log value (parallel to values list).
    Empty string means no flag; non-empty strings explain why the game was discounted.
    Useful for log output / debugging.
    """
    n = len(values)
    if n < min_samples:
        return [""] * n

    median = _stats.median(values)
    deviations = [abs(v - median) for v in values]
    mad = _stats.median(deviations)
    if mad == 0:
        return [""] * n

    robust_std = _MAD_SCALE * mad
    notes = []
    for v in values:
        z = abs(v - median) / robust_std
        direction = "HIGH" if v > median else "LOW"
        if z < 1.5:
            notes.append("")
        elif z < 2.0:
            notes.append(f"mild-outlier({direction},z={z:.1f})")
        elif z < 2.5:
            notes.append(f"outlier({direction},z={z:.1f})")
        else:
            notes.append(f"strong-outlier({direction},z={z:.1f})")
    return notes


def _parse_score_margin(score: str) -> int:
    """Parse 'H-A' format into absolute goal margin. Returns 0 if unparseable."""
    if not score or not isinstance(score, str):
        return 0
    parts = score.replace("–", "-").split("-")
    if len(parts) != 2:
        return 0
    try:
        return abs(int(parts[0].strip()) - int(parts[1].strip()))
    except (ValueError, AttributeError):
        return 0


def _sample_quality(g: dict) -> Tuple[float, str]:
    """
    Return (weight_in_[0,1], reason).
    weight=1.0 is a "normal" sample. Lower weights mean less informative.

    A sample becomes lower quality if:
      - The final margin was a blowout (>=4 goals) — game state distorted
      - Player came on as a sub late in a blowout — garbage time minutes
      - Player got <40 minutes (very brief cameo, low signal)
    """
    minutes = g.get("minutes", 90) or 0
    margin = _parse_score_margin(g.get("score", ""))

    # Garbage-time cameo: short minutes in a blowout
    if minutes > 0 and minutes < 50 and margin >= 4:
        return 0.3, f"garbage-time cameo ({minutes}min in {margin}-goal blowout)"

    # Pure blowout — full game but result distorted style
    if margin >= 5:
        return 0.5, f"severe blowout ({margin}-goal margin)"

    # Moderate blowout
    if margin >= 4:
        return 0.7, f"blowout ({margin}-goal margin)"

    # Very brief cameo (signal too thin even normalized)
    if minutes > 0 and minutes < 40:
        return 0.5, f"brief cameo ({minutes}min)"

    return 1.0, ""


def filter_low_quality_samples(
    game_logs: List[dict],
    min_retained: int = _MIN_RETAINED,
) -> Tuple[List[dict], List[str]]:
    """
    Drop only the lowest-quality samples (weight <= 0.4) and only when
    abundance allows it.

    Returns (filtered_logs, dropped_reasons).
    Conservative by design: we never reduce sample size below `min_retained`.
    """
    if not game_logs or len(game_logs) <= min_retained:
        return game_logs, []

    # Score every log
    scored = [(g, *_sample_quality(g)) for g in game_logs]

    # Drop the most distorted samples (weight <= 0.5 covers garbage-time
    # cameos AND severe blowouts AND brief cameos). Moderate blowouts
    # (weight 0.7) are kept — borderline distortion is not worth dropping.
    # Always respect the min_retained floor so low-data players keep all data.
    keep: List[dict] = []
    dropped_reasons: List[str] = []
    drop_budget = len(game_logs) - min_retained

    # Sort scored entries by weight ascending so we drop the worst first when
    # the budget is limited (otherwise we'd drop in iteration order).
    sortable = list(enumerate(scored))
    sortable.sort(key=lambda x: x[1][1])  # sort by weight asc

    drop_indices = set()
    for orig_idx, (_g, weight, reason) in sortable:
        if drop_budget <= 0:
            break
        if weight <= 0.5:
            drop_indices.add(orig_idx)
            dropped_reasons.append(reason)
            drop_budget -= 1

    for idx, (g, _w, _r) in enumerate(scored):
        if idx not in drop_indices:
            keep.append(g)

    return keep, dropped_reasons
