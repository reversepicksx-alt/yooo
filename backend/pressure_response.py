"""Player-specific passing response to possession pressure.

API-Football does not expose a universal ``passes under pressure`` field.
This module therefore uses the strongest repeatable proxy available from the
provider: the player's team possession in each completed appearance.

The feature is intentionally evidence-gated and projection-neutral.  It
returns a player profile that can be shown in analysis and supplied to a
future validated projection layer, but it never changes a projection itself.
"""

from __future__ import annotations

from datetime import date, datetime
from math import isfinite
from typing import Any


PASS_PROPS = frozenset({"pass_attempts", "passes"})
MIN_BUCKET_SAMPLES = 6
HIGH_PRESSURE_MAX_POSSESSION = 45.0
LOW_PRESSURE_MIN_POSSESSION = 55.0
FREEZER_THRESHOLD = 0.85
THRIVES_THRESHOLD = 1.10
_SHRINK_K = 6.0


def _number(value: Any) -> float | None:
    try:
        if value is None or str(value).strip() == "":
            return None
        number = float(str(value).replace("%", "").strip())
        return number if isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _date_value(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()[:10]
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def empty_pressure_response(reason: str = "No qualifying API-Football possession observations.") -> dict[str, Any]:
    """Return a stable, JSON-safe response when the profile cannot be fitted."""
    return {
        "version": "pressure-response-v1",
        "status": "insufficient_evidence",
        "classification": "unknown",
        "label": "Insufficient evidence",
        "pressureMultiplier": 1.0,
        "rawMultiplier": None,
        "highPressurePassesPer90": None,
        "lowPressurePassesPer90": None,
        "overallPassesPer90": None,
        "highPressureSamples": 0,
        "lowPressureSamples": 0,
        "qualifyingSamples": 0,
        "minimumBucketSamples": MIN_BUCKET_SAMPLES,
        "historicalPressureProxy": "team possession",
        "source": "API-Football fixture statistics + player statistics",
        "projectionAdjustment": 0.0,
        "projectionAdjustmentStatus": "shadow_only",
        "reason": reason,
    }


def _weighted_average(rows: list[tuple[float, date | None]], as_of: date | None) -> float | None:
    if not rows:
        return None
    today = as_of or date.today()
    weighted_sum = 0.0
    weight_sum = 0.0
    for value, observed_on in rows:
        # Recent games matter more, but the half-life is deliberately gentle.
        age_days = max(0, (today - observed_on).days) if observed_on else 180
        weight = 0.5 ** (age_days / 180.0)
        weighted_sum += value * weight
        weight_sum += weight
    return weighted_sum / weight_sum if weight_sum else None


def classify_pressure_response(
    game_logs: list[dict[str, Any]] | None,
    *,
    expected_possession: Any = None,
    possession_is_real: bool = False,
    as_of: date | None = None,
) -> dict[str, Any]:
    """Classify a player's pass-volume response to low team possession.

    A qualifying game must have at least 60 minutes, a real pass total, and
    real team possession from the API-Football fixture statistics endpoint.
    Middle-possession games (46%–54%) are intentionally excluded so the two
    buckets describe meaningfully different match environments.
    """
    high: list[tuple[float, date | None]] = []
    low: list[tuple[float, date | None]] = []
    overall: list[tuple[float, date | None]] = []

    for raw in game_logs or []:
        if not isinstance(raw, dict):
            continue
        minutes = _number(raw.get("minutes"))
        passes = _number(raw.get("passes_total") or raw.get("pass_attempts"))
        possession = _number(raw.get("teamPossession"))
        if minutes is None or minutes < 60 or passes is None or possession is None:
            continue
        if not 0 < possession < 100 or passes < 0:
            continue
        observed_on = _date_value(raw.get("date") or raw.get("fixtureDate"))
        passes_per90 = passes * 90.0 / max(minutes, 1.0)
        overall.append((passes_per90, observed_on))
        if possession <= HIGH_PRESSURE_MAX_POSSESSION:
            high.append((passes_per90, observed_on))
        elif possession >= LOW_PRESSURE_MIN_POSSESSION:
            low.append((passes_per90, observed_on))

    if len(high) < MIN_BUCKET_SAMPLES or len(low) < MIN_BUCKET_SAMPLES:
        result = empty_pressure_response(
            f"Need {MIN_BUCKET_SAMPLES} high-pressure and {MIN_BUCKET_SAMPLES} "
            f"low-pressure appearances; found {len(high)} and {len(low)}."
        )
        result.update({
            "qualifyingSamples": len(overall),
            "highPressureSamples": len(high),
            "lowPressureSamples": len(low),
            "overallPassesPer90": round(_weighted_average(overall, as_of), 2)
            if _weighted_average(overall, as_of) is not None else None,
        })
        return result

    high_avg = _weighted_average(high, as_of)
    low_avg = _weighted_average(low, as_of)
    overall_avg = _weighted_average(overall, as_of)
    if high_avg is None or low_avg is None or low_avg <= 0:
        return empty_pressure_response("Pressure buckets contained no usable pass-volume denominator.")

    raw_multiplier = high_avg / low_avg
    total_n = len(high) + len(low)
    shrink = total_n / (total_n + _SHRINK_K)
    multiplier = 1.0 + (raw_multiplier - 1.0) * shrink
    if multiplier < FREEZER_THRESHOLD:
        classification, label = "pressure_sensitive", "Pressure sensitive / freezer"
    elif multiplier > THRIVES_THRESHOLD:
        classification, label = "pressure_resistant", "Pressure resistant / thrives"
    else:
        classification, label = "pressure_neutral", "Pressure neutral"

    # A fallback/odds-derived possession estimate can help the broader model,
    # but it is not a measured pressure environment for this profile.
    current_possession = _number(expected_possession) if possession_is_real else None
    current_bucket = (
        "high_pressure" if current_possession is not None and current_possession <= HIGH_PRESSURE_MAX_POSSESSION
        else "low_pressure" if current_possession is not None and current_possession >= LOW_PRESSURE_MIN_POSSESSION
        else "normal_or_unknown"
    )
    return {
        "version": "pressure-response-v1",
        "status": "classified",
        "classification": classification,
        "label": label,
        "pressureMultiplier": round(multiplier, 4),
        "rawMultiplier": round(raw_multiplier, 4),
        "shrinkFactor": round(shrink, 4),
        "highPressurePassesPer90": round(high_avg, 2),
        "lowPressurePassesPer90": round(low_avg, 2),
        "overallPassesPer90": round(overall_avg, 2) if overall_avg is not None else None,
        "highPressureSamples": len(high),
        "lowPressureSamples": len(low),
        "qualifyingSamples": len(overall),
        "minimumBucketSamples": MIN_BUCKET_SAMPLES,
        "historicalPressureProxy": "team possession",
        "source": "API-Football fixture statistics + player statistics",
        "currentEnvironment": current_bucket,
        "currentExpectedPossession": current_possession,
        "projectionAdjustment": 0.0,
        "projectionAdjustmentStatus": "shadow_only",
        "reason": (
            f"High-pressure games averaged {high_avg:.1f} passes/90 versus "
            f"{low_avg:.1f} in low-pressure games."
        ),
        "limitations": [
            "API-Football does not provide a universal player passes-under-pressure field.",
            "Low team possession is a pressure proxy and can also reflect a deep-block or counterattacking plan.",
            "The profile is descriptive until walk-forward validation supports a numeric adjustment.",
        ],
    }
