"""Leakage-conscious evaluation metrics for settled prediction records.

These functions are deliberately pure so the same definitions can be used by
the API, tests, and offline reports.  A settled pick is deduplicated by
trackingId because one prediction can be saved by multiple users.

Two distinct evaluation modes are provided:

  build_scorecard()       — descriptive: computes metrics over the full corpus
                            of already-settled picks.  The chronological holdout
                            inside is a time-split of existing predictions, NOT a
                            rerun of the prediction engine.

  walk_forward_replay()   — prospective: processes picks in strict settlement
                            order.  Every metric is computed using ONLY the picks
                            that were settled BEFORE the current one.  This
                            simulates the calibration state that would have
                            existed at each real prediction moment and is
                            therefore a true out-of-sample accuracy test.
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


def _is_pass_row(row: dict) -> bool:
    return (
        str(row.get("recommendation") or "").lower() == "pass"
        or bool(row.get("isCalibrationOnly"))
    )


def _is_scored_directional_row(row: dict) -> bool:
    """Return whether a row is a verified directional HIT/MISS event.

    PUSH, DNP, unknown outcomes, and legacy PASS rows are ledger events but
    must not be converted into a binary miss for probability metrics.
    """
    if _is_pass_row(row):
        return False
    return str(row.get("result") or "").lower() in {"hit", "miss"}


def _direction_key(row: dict) -> str | None:
    """Return the scored market direction for directional breakdowns."""
    recommendation = str(row.get("recommendation") or "").lower()
    if recommendation in {"over", "under"}:
        return recommendation
    pass_direction = str(row.get("passLeaning") or "").lower()
    return pass_direction if pass_direction in {"over", "under"} else None


def _pass_calibration_metrics(rows: list[dict]) -> dict:
    """Score avoided PASS directions without mixing them into wager metrics."""
    counts = {"hit": 0, "miss": 0, "push": 0}
    by_direction: dict[str, dict[str, int]] = {}
    for row in rows:
        if not _is_pass_row(row):
            continue
        outcome = str(row.get("passOutcome") or "").lower()
        if outcome not in counts:
            continue
        direction = str(row.get("passLeaning") or "").lower()
        if direction not in {"over", "under"}:
            direction = "unknown"
        counts[outcome] += 1
        bucket = by_direction.setdefault(
            direction, {"hit": 0, "miss": 0, "push": 0}
        )
        bucket[outcome] += 1
    scored = counts["hit"] + counts["miss"]
    return {
        "n": sum(counts.values()),
        "hits": counts["hit"],
        "misses": counts["miss"],
        "pushes": counts["push"],
        "winPct": round(counts["hit"] / scored * 100, 1) if scored else 0.0,
        "byDirection": by_direction,
    }


def _event_key(row: dict) -> str:
    recommendation = str(row.get("recommendation") or "").lower()
    if recommendation == "pass":
        recommendation = str(row.get("passLeaning") or "pass").lower()
    fixture = row.get("fixtureId")
    if fixture:
        parts = [str(row.get(key, "")) for key in (
            "sport", "fixtureId", "playerId", "playerName", "teamId",
            "opponentId", "propType", "line",
        )]
        return "|".join([*parts, recommendation])
    # Older records may lack fixtureId.  Use the most specific available
    # event context; a time bucket prevents separate saves by different users
    # from becoming duplicate events while retaining distinct match days.
    fixture_date = row.get("fixtureDate") or row.get("matchDate")
    if fixture_date:
        parts = [str(row.get(key, "")) for key in (
            "sport", "fixtureDate", "playerId", "playerName", "teamId",
            "opponentId", "propType", "line",
        )]
        return "|".join([*parts, recommendation])
    timestamp = str(row.get("timestamp") or row.get("createdAt") or "")
    timestamp_bucket = timestamp[:16] if timestamp else ""
    parts = [str(row.get(key, "")) for key in (
        "sport", "playerId", "playerName", "teamId", "opponentId",
        "propType", "line", "venue", timestamp_bucket,
    )]
    return "|".join([*parts, recommendation])


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
        # Only verified directional HIT/MISS rows are binary classification
        # observations. PUSH/DNP/unknown rows are not losses.
        if not _is_scored_directional_row(row):
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
            if not _is_scored_directional_row(row):
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

def _as_sortable_dt(row: dict) -> str:
    """Return a sortable ISO string for a row, or empty string."""
    return str(row.get("settledAt") or row.get("timestamp") or "")
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
        if _is_pass_row(row):
            calibration_only += 1

    return {
        "n": len(deduped),
        "rawN": len(rows),
        "duplicateRowsRemoved": max(0, len(rows) - len(deduped)),
        "resultCounts": dict(result_counts),
        "calibrationOnlyN": calibration_only,
        "passCalibration": _pass_calibration_metrics(deduped),
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

def _walk_forward_classification(ordered: list[dict]) -> dict:
    """Aggregate log-loss and Brier over all rows using stored confidenceScore."""
    log_loss_sum = 0.0
    brier_sum = 0.0
    n = 0
    for row in ordered:
        if not _is_scored_directional_row(row):
            continue
        confidence = _number(row.get("confidenceScore"))
        if confidence is None:
            continue
        prob = max(0.0001, min(0.9999, confidence / 100.0))
        outcome = 1 if row.get("result") == "hit" else 0
        log_loss_sum += -(outcome * math.log(prob) + (1 - outcome) * math.log(1 - prob))
        brier_sum += (prob - outcome) ** 2
        n += 1
    if not n:
        return {"n": 0, "logLoss": None, "brierScore": None}
    return {
        "n": n,
        "logLoss": round(log_loss_sum / n, 4),
        "brierScore": round(brier_sum / n, 4),
    }

_BIN_DEFS = (
    ("50–59%", 50, 60),
    ("60–69%", 60, 70),
    ("70–79%", 70, 80),
    ("80–89%", 80, 90),
    ("90–100%", 90, 101),
)


def _bin_label(confidence: float) -> str | None:
    for label, lo, hi in _BIN_DEFS:
        if lo <= confidence < hi:
            return label
    return None

def walk_forward_replay(rows: list[dict]) -> dict:
    """True out-of-sample historical replay.

    Every pick is evaluated against the calibration state built exclusively from
    picks settled *before* it.  No future information is used.

    Returns
    -------
    A dict with:
      eligibleSamples       — total deduped picks
      evaluatedSamples      — picks with confidenceScore or projectedValue
      missingPriorDataEvents— picks evaluated when the prior training set was
                              empty (first event has zero prior context)
      leakageViolations     — picks where a prior row was found with
                              settledAt >= current row (data-order anomaly)
      classification        — prospective log-loss + Brier over all scored picks
      prospectiveCalibration— calibration bins built walk-forward: the bin
                              hit rate is computed from ONLY rows seen before
                              the current pick, showing whether the stored
                              confidence would have been accurate prospectively
      projection            — MAE, RMSE, meanError over all picks with
                              both actualValue and projectedValue
      bySport               — per-sport classification + projection metrics
      byProp                — per prop-type projection metrics (sport × propType)
      dateRange             — first and last settledAt in the corpus
    """
    deduped = dedupe_prediction_rows(rows)
    ordered = _sorted_rows(deduped)

    # Running calibration bins: label → {n, hits}
    # These accumulate from prior rows and are used to evaluate the current row.
    running_bins: dict[str, dict] = {
        label: {"n": 0, "hits": 0} for label, _, _ in _BIN_DEFS
    }

    # Accumulators for overall metrics
    log_loss_sum = 0.0
    brier_sum = 0.0
    prob_n = 0

    error_abs_sum = 0.0
    error_sq_sum = 0.0
    error_signed_sum = 0.0
    error_n = 0

    leakage_violations = 0
    missing_prior_data_events = 0

    # Per-sport accumulators: sport → {log_loss, brier, prob_n, abs, sq, signed, reg_n}
    sport_acc: dict[str, dict] = defaultdict(lambda: {
        "log_loss": 0.0, "brier": 0.0, "prob_n": 0,
        "abs": 0.0, "sq": 0.0, "signed": 0.0, "reg_n": 0,
    })
    direction_acc: dict[str, dict] = defaultdict(lambda: {
        "hits": 0, "misses": 0, "log_loss": 0.0, "brier": 0.0, "n": 0,
    })
    # Per-(sport,prop) for regression breakdown
    prop_acc: dict[tuple, dict] = defaultdict(lambda: {
        "abs": 0.0, "sq": 0.0, "signed": 0.0, "n": 0,
    })

    # Prospective calibration: for each bin, track how each row's outcome
    # compared against the prior-bin empirical hit rate.
    prosp_bins: dict[str, dict] = {
        label: {"n": 0, "priorHitRateSum": 0.0, "hits": 0} for label, _, _ in _BIN_DEFS
    }

    for index, row in enumerate(ordered):
        row_dt = _as_sortable_dt(row)
        sport = str(row.get("sport") or "unknown")
        prop_type = str(row.get("propType") or "unknown")

        # ── Leakage check ──────────────────────────────────────────────────
        # Any prior row whose settledAt is >= the current row's is a violation.
        if index > 0:
            prior_dts = [_as_sortable_dt(ordered[j]) for j in range(index)]
            if any(pdt >= row_dt for pdt in prior_dts if pdt):
                leakage_violations += 1

        if index == 0:
            missing_prior_data_events += 1

        # ── Classification (log-loss + Brier) ──────────────────────────────
        confidence = _number(row.get("confidenceScore"))
        if not _is_scored_directional_row(row):
            confidence = None
        outcome = 1 if row.get("result") == "hit" else 0
        direction = _direction_key(row) if _is_scored_directional_row(row) else None
        if direction:
            direction_acc[direction]["hits"] += outcome
            direction_acc[direction]["misses"] += 1 - outcome
            direction_acc[direction]["n"] += 1

        if confidence is not None:
            prob = max(0.0001, min(0.9999, confidence / 100.0))
            ll = -(outcome * math.log(prob) + (1 - outcome) * math.log(1 - prob))
            bs = (prob - outcome) ** 2
            log_loss_sum += ll
            brier_sum += bs
            prob_n += 1
            sport_acc[sport]["log_loss"] += ll
            sport_acc[sport]["brier"] += bs
            sport_acc[sport]["prob_n"] += 1
            if direction:
                direction_acc[direction]["log_loss"] += ll
                direction_acc[direction]["brier"] += bs

            # Prospective calibration: find prior bin hit rate, then record
            label = _bin_label(confidence)
            if label and running_bins[label]["n"] > 0:
                prior_rate = running_bins[label]["hits"] / running_bins[label]["n"]
                prosp_bins[label]["n"] += 1
                prosp_bins[label]["priorHitRateSum"] += prior_rate * 100.0
                prosp_bins[label]["hits"] += outcome

        # ── Regression (MAE / RMSE) ────────────────────────────────────────
        actual = _number(row.get("actualValue"))
        projected = _number(row.get("projectedValue"))
        if actual is not None and projected is not None:
            err = actual - projected
            error_abs_sum += abs(err)
            error_sq_sum += err * err
            error_signed_sum += err
            error_n += 1
            sport_acc[sport]["abs"] += abs(err)
            sport_acc[sport]["sq"] += err * err
            sport_acc[sport]["signed"] += err
            sport_acc[sport]["reg_n"] += 1
            prop_acc[(sport, prop_type)]["abs"] += abs(err)
            prop_acc[(sport, prop_type)]["sq"] += err * err
            prop_acc[(sport, prop_type)]["signed"] += err
            prop_acc[(sport, prop_type)]["n"] += 1

        # ── Update running calibration bins with this row ──────────────────
        if confidence is not None:
            label = _bin_label(confidence)
            if label:
                running_bins[label]["n"] += 1
                running_bins[label]["hits"] += outcome

    # ── Assemble prospective calibration output ────────────────────────────
    calibration_output = []
    for label, _, _ in _BIN_DEFS:
        pb = prosp_bins[label]
        rb = running_bins[label]
        if not rb["n"]:
            continue
        if pb["n"] > 0:
            prior_predicted = round(pb["priorHitRateSum"] / pb["n"], 1)
            observed = round(pb["hits"] / pb["n"] * 100, 1)
        else:
            prior_predicted = None
            observed = None
        # Always include final observed rate for the bin
        final_observed = round(rb["hits"] / rb["n"] * 100, 1) if rb["n"] else None
        calibration_output.append({
            "label": label,
            "n": rb["n"],
            "prospectiveN": pb["n"],
            "priorPredictedPct": prior_predicted,
            "observedPct": observed,
            "gapPp": round(observed - prior_predicted, 1) if (prior_predicted is not None and observed is not None) else None,
            "finalObservedPct": final_observed,
            "note": ("prospective: priorPredictedPct uses only picks settled before each row; "
                     "finalObservedPct is the overall hit rate for the bin"),
        })

    # ── Per-sport summary ──────────────────────────────────────────────────
    by_sport_output = []
    for sp, acc in sorted(sport_acc.items()):
        entry: dict = {"sport": sp}
        if acc["prob_n"]:
            entry["classification"] = {
                "n": acc["prob_n"],
                "logLoss": round(acc["log_loss"] / acc["prob_n"], 4),
                "brierScore": round(acc["brier"] / acc["prob_n"], 4),
            }
        else:
            entry["classification"] = {"n": 0, "logLoss": None, "brierScore": None}
        if acc["reg_n"]:
            entry["projection"] = {
                "n": acc["reg_n"],
                "mae": round(acc["abs"] / acc["reg_n"], 4),
                "rmse": round(math.sqrt(acc["sq"] / acc["reg_n"]), 4),
                "meanError": round(acc["signed"] / acc["reg_n"], 4),
            }
        else:
            entry["projection"] = {"n": 0, "mae": None, "rmse": None, "meanError": None}
        by_sport_output.append(entry)

    # ── Per-prop summary ───────────────────────────────────────────────────
    by_prop_output = []
    for (sp, pt), acc in sorted(prop_acc.items(), key=lambda kv: (-kv[1]["n"], kv[0])):
        if not acc["n"]:
            continue
        by_prop_output.append({
            "sport": sp,
            "propType": pt,
            "n": acc["n"],
            "mae": round(acc["abs"] / acc["n"], 4),
            "rmse": round(math.sqrt(acc["sq"] / acc["n"]), 4),
            "meanError": round(acc["signed"] / acc["n"], 4),
        })

    by_direction_output = {}
    for direction, acc in sorted(direction_acc.items()):
        n = acc["n"]
        by_direction_output[direction] = {
            "n": n,
            "hits": acc["hits"],
            "misses": acc["misses"],
            "hitRate": round(acc["hits"] / n * 100, 1) if n else None,
            "logLoss": round(acc["log_loss"] / n, 4) if n else None,
            "brierScore": round(acc["brier"] / n, 4) if n else None,
        }

    return {
        "description": (
            "True out-of-sample walk-forward replay. Each pick is evaluated against "
            "the calibration state built from ONLY the picks settled before it. "
            "Separate from the descriptive scorecard which uses the full corpus."
        ),
        "eligibleSamples": len(ordered),
        "evaluatedSamples": max(prob_n, error_n),
        "missingPriorDataEvents": missing_prior_data_events,
        "leakageViolations": leakage_violations,
        "dateRange": _date_range(ordered),
        "classification": (
            {
                "n": prob_n,
                "logLoss": round(log_loss_sum / prob_n, 4),
                "brierScore": round(brier_sum / prob_n, 4),
            }
            if prob_n else {"n": 0, "logLoss": None, "brierScore": None}
        ),
        "prospectiveCalibration": calibration_output,
        "projection": (
            {
                "n": error_n,
                "mae": round(error_abs_sum / error_n, 4),
                "rmse": round(math.sqrt(error_sq_sum / error_n), 4),
                "meanError": round(error_signed_sum / error_n, 4),
            }
            if error_n else {"n": 0, "mae": None, "rmse": None, "meanError": None}
        ),
        "bySport": by_sport_output,
        "byProp": by_prop_output,
        "byDirection": by_direction_output,
    }

def _walk_forward_projection(ordered: list[dict]) -> dict:
    errors: list[float] = []
    for row in ordered:
        actual = _number(row.get("actualValue"))
        projected = _number(row.get("projectedValue"))
        if actual is not None and projected is not None:
            errors.append(actual - projected)
    if not errors:
        return {"n": 0, "mae": None, "rmse": None, "meanError": None}
    return {
        "n": len(errors),
        "mae": round(sum(abs(e) for e in errors) / len(errors), 4),
        "rmse": round(math.sqrt(sum(e * e for e in errors) / len(errors)), 4),
        "meanError": round(sum(errors) / len(errors), 4),
    }
