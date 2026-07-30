"""Leakage-conscious evaluation metrics for settled prediction records.

These functions are deliberately pure so the same definitions can be used by
the API, tests, and offline reports.  A settled pick is deduplicated by
trackingId because one prediction can be saved by multiple users.
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime
from typing import Any


def _number(value: Any) -> float | None:
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def _event_key(row: dict) -> str:
    tracking = row.get("trackingId")
    if tracking:
        return str(tracking)
    fixture = row.get("fixtureId")
    # Legacy rows without tracking IDs can still be deduplicated when the
    # fixture is known.  Do not merge separate fixtures just because the same
    # player and market were used again.
    if fixture:
        return "|".join(str(row.get(key, "")) for key in (
            "playerName", "sport", "propType", "line", "recommendation",
            "fixtureId",
        ))
    return "|".join(str(row.get(key, "")) for key in (
        "playerName", "sport", "propType", "line", "recommendation",
        "venue", "timestamp",
    ))


def dedupe_prediction_rows(rows: list[dict]) -> list[dict]:
    """Keep one row per prediction event, preferring the newest duplicate."""
    chosen: dict[str, dict] = {}
    for row in rows:
        key = _event_key(row)
        current = chosen.get(key)
        if current is None:
            chosen[key] = row
            continue
        current_date = str(current.get("settledAt") or current.get("timestamp") or "")
        new_date = str(row.get("settledAt") or row.get("timestamp") or "")
        if new_date > current_date:
            chosen[key] = row
    return list(chosen.values())


def _sorted_rows(rows: list[dict]) -> list[dict]:
    return sorted(
        rows,
        key=lambda row: str(row.get("settledAt") or row.get("timestamp") or ""),
    )


def _date_range(rows: list[dict]) -> dict:
    dates = [
        str(row.get("settledAt") or row.get("timestamp") or "")
        for row in rows
        if row.get("settledAt") or row.get("timestamp")
    ]
    return {
        "from": min(dates) if dates else None,
        "to": max(dates) if dates else None,
    }


def _error_metrics(rows: list[dict]) -> dict:
    errors: list[float] = []
    for row in rows:
        actual = _number(row.get("actualValue"))
        projected = _number(row.get("projectedValue"))
        if actual is not None and projected is not None:
            errors.append(actual - projected)
    if not errors:
        return {"n": 0, "mae": None, "rmse": None, "meanError": None}
    return {
        "n": len(errors),
        "mae": round(sum(abs(error) for error in errors) / len(errors), 4),
        "rmse": round(math.sqrt(sum(error * error for error in errors) / len(errors)), 4),
        "meanError": round(sum(errors) / len(errors), 4),
    }


def _probability_metrics(rows: list[dict], field: str) -> dict:
    scored: list[tuple[float, int]] = []
    for row in rows:
        # A PASS is a calibration observation, not an actionable directional
        # prediction.  It must remain in the ledger, but cannot be scored as a
        # hit or miss without a direction.
        if str(row.get("recommendation") or "").lower() == "pass":
            continue
        confidence = _number(row.get(field))
        if confidence is None:
            continue
        # Confidence is stored as a percentage throughout the prediction APIs.
        probability = max(0.0001, min(0.9999, confidence / 100.0))
        outcome = 1 if row.get("result") == "hit" else 0
        scored.append((probability, outcome))
    if not scored:
        return {"n": 0, "logLoss": None, "brierScore": None}
    log_loss = sum(
        -(outcome * math.log(probability) + (1 - outcome) * math.log(1 - probability))
        for probability, outcome in scored
    ) / len(scored)
    brier = sum((probability - outcome) ** 2 for probability, outcome in scored) / len(scored)
    return {
        "n": len(scored),
        "logLoss": round(log_loss, 4),
        "brierScore": round(brier, 4),
    }


def _calibration_bins(rows: list[dict], field: str = "confidenceScore") -> list[dict]:
    definitions = (
        ("50–59%", 50, 60),
        ("60–69%", 60, 70),
        ("70–79%", 70, 80),
        ("80–89%", 80, 90),
        ("90–100%", 90, 101),
    )
    output = []
    for label, lower, upper in definitions:
        bucket = []
        for row in rows:
            if str(row.get("recommendation") or "").lower() == "pass":
                continue
            confidence = _number(row.get(field))
            if confidence is not None and lower <= confidence < upper:
                bucket.append(row)
        if not bucket:
            continue
        predicted = sum(_number(row.get(field)) or 0 for row in bucket) / len(bucket)
        observed = sum(row.get("result") == "hit" for row in bucket) / len(bucket) * 100
        output.append({
            "label": label,
            "n": len(bucket),
            "predictedPct": round(predicted, 1),
            "observedPct": round(observed, 1),
            "gapPp": round(observed - predicted, 1),
        })
    return output


def _projection_groups(rows: list[dict]) -> list[dict]:
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in rows:
        if _number(row.get("actualValue")) is not None and _number(row.get("projectedValue")) is not None:
            groups[(str(row.get("sport") or "unknown"), str(row.get("propType") or "unknown"))].append(row)
    output = []
    for (sport, prop_type), bucket in groups.items():
        output.append({
            "sport": sport,
            "propType": prop_type,
            **_error_metrics(bucket),
        })
    return sorted(output, key=lambda item: (-item["n"], item["sport"], item["propType"]))


def build_scorecard(rows: list[dict]) -> dict:
    """Build the model scorecard from settled hit/miss rows.

    The final-confidence metrics describe what users see.  Raw-confidence
    metrics are included separately so calibration improvements are auditable.
    MAE/RMSE are reported overall and by sport/prop because their units differ.
    The chronological holdout is the final 20% of settled events and is
    descriptive; it does not claim that a retrained model was run.
    """
    deduped = dedupe_prediction_rows(rows)
    ordered = _sorted_rows(deduped)
    numeric = [row for row in ordered if _number(row.get("actualValue")) is not None and _number(row.get("projectedValue")) is not None]
    split = max(1, int(len(ordered) * 0.8)) if ordered else 0
    holdout = ordered[split:] if split < len(ordered) else []

    result_counts = defaultdict(int)
    calibration_only = 0
    for row in deduped:
        result_counts[str(row.get("result") or "unknown").lower()] += 1
        if str(row.get("recommendation") or "").lower() == "pass":
            calibration_only += 1

    return {
        "n": len(deduped),
        "rawN": len(rows),
        "duplicateRowsRemoved": max(0, len(rows) - len(deduped)),
        "resultCounts": dict(result_counts),
        "calibrationOnlyN": calibration_only,
        "dateRange": _date_range(ordered),
        "classification": {
            "finalConfidence": _probability_metrics(deduped, "confidenceScore"),
            "rawConfidence": _probability_metrics(deduped, "rawConfidence"),
            "calibration": _calibration_bins(deduped),
        },
        "projection": {
            "overall": _error_metrics(numeric),
            "byProp": _projection_groups(numeric),
            "unitsNote": "Overall MAE/RMSE combines unlike stat units and is directional only; use byProp for comparable error measurement.",
        },
        "chronologicalHoldout": {
            "description": "Final 20% of settled prediction events, ordered by settlement time. This is a descriptive time split of already-produced predictions, not a rerun of the prediction engine.",
            "n": len(holdout),
            "dateRange": _date_range(holdout),
            "classification": _probability_metrics(holdout, "confidenceScore"),
            "projection": _error_metrics(holdout),
        },
    }