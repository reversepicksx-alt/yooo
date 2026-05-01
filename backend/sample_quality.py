"""
Sample-quality filtering ("luck strip") for Bayesian priors.

Walters-inspired idea: not every historical game is equally informative.
Games where game state ran away (blowouts) or where the player only made a
short cameo in a distorted match should not weight equally with normal games.

The engine already normalizes to per-90, which handles minutes per se.
What it does NOT handle is *game-state distortion* — when the team's playing
style changed because of the score line.

We are conservative on purpose: we only filter samples when we have plenty
to spare. Below the abundance threshold every sample is preserved.
"""
from typing import List, Tuple


_MIN_RETAINED = 6  # never drop a sample if it would leave fewer than this


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
