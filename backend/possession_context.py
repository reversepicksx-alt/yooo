"""Pure helpers for auditable soccer possession context.

These helpers intentionally contain no provider or database access.  The
prediction route supplies independently fetched fixture-statistics rows and
current-fixture odds, while this module keeps the sampling and weighting
contract easy to test.
"""

from __future__ import annotations

import math
from typing import Any


POSSESSION_MIN_VERIFIED_SAMPLE = 10
POSSESSION_RECENCY_HALF_LIFE = 10.0


def recency_weighted_average(
    rows: list[dict[str, Any]] | None,
    *,
    value_key: str = "value",
    half_life: float = POSSESSION_RECENCY_HALF_LIFE,
) -> float | None:
    """Return a newest-first, recency-weighted average.

    The caller must provide rows newest-first.  A ten-match half-life gives
    current form more influence without allowing one fixture to dominate the
    club-level sample.
    """
    if not rows or half_life <= 0:
        return None

    weighted_total = 0.0
    weight_total = 0.0
    for index, row in enumerate(rows):
        try:
            value = float(row.get(value_key))
        except (AttributeError, TypeError, ValueError):
            continue
        if not math.isfinite(value):
            continue
        weight = 2.0 ** (-index / half_life)
        weighted_total += value * weight
        weight_total += weight

    if weight_total <= 0:
        return None
    return round(weighted_total / weight_total, 1)


def possession_sample_status(
    sample_size: int,
    *,
    required: int = POSSESSION_MIN_VERIFIED_SAMPLE,
) -> tuple[str, bool]:
    """Classify a schedule sample without treating a partial sample as exact."""
    if sample_size >= required:
        return "verified", True
    if sample_size > 0:
        return "insufficient_sample", False
    return "unavailable", False


def moneyline_possession_signal(odds: dict[str, Any] | None) -> dict[str, float] | None:
    """Convert the current fixture moneyline into a bounded possession signal.

    This is a contextual blend, never a substitute for fixture-statistics
    possession.  The returned weight is deliberately capped at 18%, so a
    strong market cannot erase verified schedule evidence.
    """
    if not isinstance(odds, dict):
        return None

    def _american_to_probability(value: Any) -> float | None:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if number == 0:
            return None
        if number < 0:
            return (-number) / (-number + 100.0)
        return 100.0 / (number + 100.0)

    home_probability: float | None = None
    away_probability: float | None = None
    bookmaker = odds.get("bookmakerOdds")
    if isinstance(bookmaker, dict):
        try:
            home_decimal = float(bookmaker.get("homeWin"))
            away_decimal = float(bookmaker.get("awayWin"))
            if home_decimal > 0 and away_decimal > 0:
                home_probability = 1.0 / max(home_decimal, 1.01)
                away_probability = 1.0 / max(away_decimal, 1.01)
        except (TypeError, ValueError):
            pass

    if home_probability is None or away_probability is None:
        american = odds.get("americanOdds")
        if isinstance(american, dict):
            home_probability = _american_to_probability(american.get("home"))
            away_probability = _american_to_probability(american.get("away"))

    if home_probability is None or away_probability is None:
        return None

    total = home_probability + away_probability
    if total <= 0:
        return None

    normalized_home = home_probability / total
    favorite_probability = max(normalized_home, 1.0 - normalized_home)
    edge = min(1.0, max(0.0, (favorite_probability - 0.5) * 2.0))
    weight = round(min(0.18, 0.06 + edge * 0.12), 3)
    expected_home = round(
        min(75.0, max(25.0, 50.0 + (normalized_home - 0.5) * 55.0)),
        1,
    )
    return {
        "normalizedHomeProbability": round(normalized_home, 4),
        "favoriteProbability": round(favorite_probability, 4),
        "expectedHomePossession": expected_home,
        "weight": weight,
    }