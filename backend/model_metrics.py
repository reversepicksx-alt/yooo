"""Leakage-conscious evaluation metrics for settled prediction records.

These functions are deliberately pure so the same definitions can be used by
the API, tests, and offline reports.

Terminology used throughout this module
---------------------------------------
raw records      — every row returned from the database (one per save action);
                   a single prediction event may produce many raw records when
                   users save the same pick or a user saves it more than once.
unique events    — raw records collapsed to one row per prediction event using
                   the canonical event key (_event_key).  This is the unit all
                   accuracy metrics use.
scored events    — unique events whose result is a verified directional HIT or
                   MISS; PUSH, DNP, and legacy PASS rows are NOT scored because
                   they carry no binary classification signal.

Deduplication key
-----------------
Each unique prediction event is identified by _event_key(), which combines
sport, fixture identity (fixtureId when present, otherwise fixtureDate, then a
16-minute timestamp bucket), player identity, team/opponent identity, prop
type, line, and resolved direction.  trackingId is a per-user, per-save handle
and must NOT be used as the deduplication key.

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
    """Return the canonical event key for a single prediction row.

    This is the single authoritative deduplication identifier for the system.
    Two rows with the same key represent the same prediction event (e.g. the
    same pick saved by two different users, or saved twice by the same user).

    Key components (in order):
      sport | fixture-identity | playerId | playerName | teamId | opponentId
      | propType | line | resolved-direction

    Fixture identity falls back through three tiers:
      1. fixtureId           — exact match; preferred for all modern records
      2. fixtureDate/matchDate — date bucket; used when fixtureId is absent
      3. 16-minute timestamp bucket — last resort for very old records
    """
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
        # Use the resolved fixture_date value directly, not row.get("fixtureDate"),
        # so records that carry only matchDate (not fixtureDate) still contribute
        # the correct date to the key and remain distinguishable from other days.
        parts = [str(row.get(key, "")) for key in (
            "sport", "playerId", "playerName", "teamId",
            "opponentId", "propType", "line",
        )]
        return "|".join(["", str(fixture_date), *parts, recommendation])
    timestamp = str(row.get("timestamp") or row.get("createdAt") or "")
    # Use the first 16 characters (YYYY-MM-DDTHH:MM) as a per-minute bucket
    # so two saves of the same pick within the same minute collapse together,
    # while picks on different days remain distinct events.
    timestamp_bucket = timestamp[:16] if timestamp else ""
    parts = [str(row.get(key, "")) for key in (
        "sport", "playerId", "playerName", "teamId", "opponentId",
        "propType", "line", "venue",
    )]
    # Append the bucket directly as a value — do NOT use it as a dict key.
    return "|".join([*parts, timestamp_bucket, recommendation])


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
    scored_n = 0
    for row in deduped:
        result_counts[str(row.get("result") or "unknown").lower()] += 1
        if _is_pass_row(row):
            calibration_only += 1
        if _is_scored_directional_row(row):
            scored_n += 1

    return {
        # rawN  — total DB rows fetched (one per save action; includes duplicates)
        # n     — unique prediction events after deduplication via _event_key()
        # scoredN — unique events with a verified directional HIT or MISS outcome;
        #           this is the denominator for all binary classification metrics
        "rawN": len(rows),
        "n": len(deduped),
        "scoredN": scored_n,
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

def _go_no_go_recommendation(
    classification: dict,
    prospective_calibration: list[dict],
    projection: dict,
    by_direction: dict,
    eligible_samples: int,
) -> dict:
    """Synthesise a go/no-go verdict from walk-forward replay results.

    Criteria evaluated (each flags an issue or positive):

    1. Classification quality — log-loss vs the coin-flip baseline (ln 2 ≈ 0.693).
       A model that cannot beat a coin flip on confidence-weighted scoring
       should not be trusted for high-stakes picks.

    2. Calibration quality — mean absolute gap between the prior-predicted hit
       rate and the observed hit rate across prospective calibration bins.
       Bins with fewer than 5 prospective observations are skipped.

    3. Direction asymmetry — absolute gap between OVER and UNDER hit rates.
       A gap > 20 pp with at least 5 samples in each direction is a concern.

    4. Projection bias — signed meanError relative to MAE.  A ratio > 0.4
       indicates the model systematically over- or under-projects.

    Verdict:
      GO      — no issues detected; model passes all key checks.
      CAUTION — one or more moderate concerns that should be monitored.
      NO_GO   — fundamental classification or calibration failure.
    """
    issues: list[str] = []
    positives: list[str] = []

    # Minimum-evidence thresholds for each check.  If fewer samples exist than
    # each threshold the check is skipped, which is correct — but if EVERY
    # check is skipped the function has no basis for any verdict.  Track how
    # many checks actually ran so the verdict correctly reflects the gap.
    _MIN_CLS = 10     # minimum scored events for classification check
    _MIN_CAL = 5      # minimum prospective observations per calibration bin
    _MIN_DIR = 5      # minimum samples in each direction for asymmetry check
    _MIN_PROJ = 10    # minimum regression samples for bias check
    checks_run = 0    # incremented once per check that fires

    # 1. Classification quality
    log_loss = classification.get("logLoss")
    n_cls = classification.get("n", 0)
    _COIN_FLIP_LL = 0.6931  # ln(2)
    if log_loss is not None and n_cls >= _MIN_CLS:
        checks_run += 1
        if log_loss < _COIN_FLIP_LL * 0.95:
            positives.append(
                f"Log-loss {log_loss:.4f} beats coin-flip baseline ({_COIN_FLIP_LL:.4f})"
            )
        elif log_loss >= _COIN_FLIP_LL:
            issues.append(
                f"Log-loss {log_loss:.4f} is at or above coin-flip baseline "
                f"({_COIN_FLIP_LL:.4f}) — model is not beating random on classification"
            )
        else:
            issues.append(
                f"Log-loss {log_loss:.4f} only marginally below coin-flip baseline "
                f"({_COIN_FLIP_LL:.4f}) — gains are weak"
            )

    # 2. Prospective calibration quality
    calibration_gaps = [
        abs(b["gapPp"])
        for b in prospective_calibration
        if b.get("gapPp") is not None and (b.get("prospectiveN") or 0) >= _MIN_CAL
    ]
    if calibration_gaps:
        checks_run += 1
        mean_gap = sum(calibration_gaps) / len(calibration_gaps)
        if mean_gap > 15:
            issues.append(
                f"Mean prospective calibration gap {mean_gap:.1f} pp — "
                f"confidence scores are poorly calibrated out-of-sample"
            )
        elif mean_gap > 7:
            issues.append(
                f"Mean prospective calibration gap {mean_gap:.1f} pp — "
                f"moderate miscalibration; monitor for overconfidence"
            )
        else:
            positives.append(
                f"Mean prospective calibration gap {mean_gap:.1f} pp — "
                f"confidence scores are well calibrated"
            )

    # 3. OVER / UNDER direction asymmetry
    over_data = by_direction.get("over", {})
    under_data = by_direction.get("under", {})
    over_hr = over_data.get("hitRate")
    under_hr = under_data.get("hitRate")
    over_n = over_data.get("n", 0)
    under_n = under_data.get("n", 0)
    if over_hr is not None and under_hr is not None and over_n >= _MIN_DIR and under_n >= _MIN_DIR:
        checks_run += 1
        gap = abs(over_hr - under_hr)
        if gap > 20:
            worse = "UNDER" if over_hr > under_hr else "OVER"
            issues.append(
                f"Direction asymmetry {gap:.1f} pp: "
                f"OVER {over_hr:.1f}% (n={over_n}) vs UNDER {under_hr:.1f}% (n={under_n}) — "
                f"{worse} picks significantly underperform"
            )
        elif gap <= 8:
            positives.append(
                f"OVER/UNDER hit rates balanced: "
                f"OVER {over_hr:.1f}% (n={over_n}) vs UNDER {under_hr:.1f}% (n={under_n})"
            )
        else:
            issues.append(
                f"Moderate direction asymmetry {gap:.1f} pp: "
                f"OVER {over_hr:.1f}% (n={over_n}) vs UNDER {under_hr:.1f}% (n={under_n})"
            )

    # 4. Systematic projection bias
    mean_error = projection.get("meanError")
    mae = projection.get("mae")
    proj_n = projection.get("n", 0)
    if mean_error is not None and mae is not None and proj_n >= _MIN_PROJ and mae > 0:
        checks_run += 1
        bias_ratio = abs(mean_error) / mae
        direction_label = "over-projecting" if mean_error < 0 else "under-projecting"
        if bias_ratio > 0.4:
            issues.append(
                f"Systematic projection bias: meanError={mean_error:.3f}, MAE={mae:.3f} "
                f"(bias ratio {bias_ratio:.2f}) — model is {direction_label}"
            )
        else:
            positives.append(
                f"Projection bias acceptable: meanError={mean_error:.3f}, MAE={mae:.3f} "
                f"(bias ratio {bias_ratio:.2f})"
            )

    # Determine verdict
    # Guard: if no check ran there is no evidence basis for any verdict.
    # This happens for empty corpora or corpora that fall below every
    # minimum-sample threshold.  A spurious GO is actively misleading, so
    # force CAUTION and explain which evidence is missing.
    if checks_run == 0:
        missing = []
        if n_cls < _MIN_CLS:
            missing.append(f"classification (need ≥{_MIN_CLS} scored events, have {n_cls})")
        cal_prosp = sum(
            1 for b in prospective_calibration
            if (b.get("prospectiveN") or 0) >= _MIN_CAL
        )
        if not cal_prosp:
            missing.append(f"prospective calibration (need ≥{_MIN_CAL} observations per bin)")
        if over_n < _MIN_DIR or under_n < _MIN_DIR:
            missing.append(
                f"direction analysis (need ≥{_MIN_DIR} OVER and ≥{_MIN_DIR} UNDER events; "
                f"have {over_n} OVER, {under_n} UNDER)"
            )
        if proj_n < _MIN_PROJ:
            missing.append(f"projection bias (need ≥{_MIN_PROJ} regression events, have {proj_n})")
        verdict = "CAUTION"
        summary = (
            "Insufficient evidence to evaluate the model. "
            + ("Missing: " + "; ".join(missing) + "." if missing else "No settled picks found.")
        )
        return {
            "verdict": verdict,
            "summary": summary,
            "issues": [],
            "positives": [],
            "basisN": eligible_samples,
            "note": (
                "GO = model beats baseline on all key metrics; "
                "CAUTION = one or more moderate concerns or insufficient evidence; "
                "NO_GO = fundamental classification or calibration failure."
            ),
        }

    critical = any(
        "not beating random" in i or "poorly calibrated" in i
        for i in issues
    )
    if critical:
        verdict = "NO_GO"
        summary = (
            "Model has a fundamental classification or calibration failure. "
            "Confidence scores cannot be trusted as probability estimates."
        )
    elif issues:
        verdict = "CAUTION"
        summary = (
            "Model shows mixed signals. Investigate the flagged concerns "
            "before expanding to new sports or props."
        )
    else:
        verdict = "GO"
        summary = (
            "Model passes all key out-of-sample checks. "
            "No significant calibration, classification, or projection concerns detected."
        )

    return {
        "verdict": verdict,
        "summary": summary,
        "issues": issues,
        "positives": positives,
        "basisN": eligible_samples,
        "note": (
            "GO = model beats baseline on all key metrics; "
            "CAUTION = one or more moderate concerns; "
            "NO_GO = fundamental classification or calibration failure."
        ),
    }


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
                              the current pick; each bin also includes a
                              byDirection breakdown separating OVER and UNDER
      projection            — MAE, RMSE, meanError over all picks with
                              both actualValue and projectedValue
      bySport               — per-sport classification + projection metrics
      byProp                — per prop-type projection metrics (sport × propType)
      byDirection           — hit-rate + log-loss breakdown by OVER vs UNDER
      dateRange             — first and last settledAt in the corpus
      goNoGo                — synthesised go/no-go recommendation with verdict,
                              summary, issues, and positives
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
    # Direction-split prospective calibration: track OVER and UNDER separately
    # within each confidence bin so over- vs under-calibration is visible.
    prosp_bins_dir: dict[str, dict[str, dict]] = {
        d: {label: {"n": 0, "priorHitRateSum": 0.0, "hits": 0} for label, _, _ in _BIN_DEFS}
        for d in ("over", "under")
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
                # Direction-split accumulation
                if direction in prosp_bins_dir:
                    prosp_bins_dir[direction][label]["n"] += 1
                    prosp_bins_dir[direction][label]["priorHitRateSum"] += prior_rate * 100.0
                    prosp_bins_dir[direction][label]["hits"] += outcome

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
    def _prosp_bin_summary(pb: dict) -> dict | None:
        """Return {n, priorPredictedPct, observedPct, gapPp} or None."""
        if not pb["n"]:
            return None
        prior_predicted = round(pb["priorHitRateSum"] / pb["n"], 1)
        observed = round(pb["hits"] / pb["n"] * 100, 1)
        return {
            "n": pb["n"],
            "priorPredictedPct": prior_predicted,
            "observedPct": observed,
            "gapPp": round(observed - prior_predicted, 1),
        }

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
        # Direction-split summaries (None when no data for that direction)
        by_direction_bin: dict[str, dict | None] = {}
        for d in ("over", "under"):
            by_direction_bin[d] = _prosp_bin_summary(prosp_bins_dir[d][label])
        calibration_output.append({
            "label": label,
            "n": rb["n"],
            "prospectiveN": pb["n"],
            "priorPredictedPct": prior_predicted,
            "observedPct": observed,
            "gapPp": round(observed - prior_predicted, 1) if (prior_predicted is not None and observed is not None) else None,
            "finalObservedPct": final_observed,
            "byDirection": by_direction_bin,
            "note": ("prospective: priorPredictedPct uses only picks settled before each row; "
                     "finalObservedPct is the overall hit rate for the bin; "
                     "byDirection separates OVER and UNDER calibration within the bin"),
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
        "goNoGo": _go_no_go_recommendation(
            classification=(
                {
                    "n": prob_n,
                    "logLoss": round(log_loss_sum / prob_n, 4),
                    "brierScore": round(brier_sum / prob_n, 4),
                }
                if prob_n else {"n": 0, "logLoss": None, "brierScore": None}
            ),
            prospective_calibration=calibration_output,
            projection=(
                {
                    "n": error_n,
                    "mae": round(error_abs_sum / error_n, 4),
                    "rmse": round(math.sqrt(error_sq_sum / error_n), 4),
                    "meanError": round(error_signed_sum / error_n, 4),
                }
                if error_n else {"n": 0, "mae": None, "rmse": None, "meanError": None}
            ),
            by_direction=by_direction_output,
            eligible_samples=len(ordered),
        ),
    }

def validate_weighted_opponent_evidence(rows: list[dict]) -> dict:
    """Leakage-safe replay comparing weighted vs unweighted opponent cohort evidence.

    Processes settled picks in strict chronological order. For each row, ONLY
    the cohort evidence stored at prediction time is used — the weighted and
    unweighted averages are read from ``positionComparison`` on the row itself,
    never recomputed. This guarantees no future information leaks into the
    evaluation.

    Eligible rows must carry all of:
      - positionComparison.weightedAverage  (from summarize_position_cohort)
      - positionComparison.unweightedAverage or positionComparison.avgStatValue
      - line                                (the saved prop line)
      - actualValue                         (the post-settlement stat)
      - result in {"hit", "miss"}           (scored directional events only)

    Metrics returned per method (weighted / unweighted):
      - projection     — MAE, RMSE, meanError vs actualValue
      - agreesWithRecommendationN — picks where the method's implied direction
                                    matches the saved recommendation
      - hitRateWhenAgrees         — empirical hit rate for those picks

    Plus:
      - churnN / churnPct — how often the two methods imply a different direction
      - promotionDecision — GO / CAUTION / NO_GO with per-criterion pass/fail

    Promotion criteria (all must pass for GO):
      1. Weighted MAE ≤ Unweighted MAE  (projection is not worse)
      2. Weighted hit-rate ≥ Unweighted hit-rate − 2 pp  (calibration not regressed)
      3. Recommendation churn < 15 %  (changes are bounded)
    """
    deduped = dedupe_prediction_rows(rows)
    ordered = _sorted_rows(deduped)

    eligible: list[dict] = []
    for row in ordered:
        if not _is_scored_directional_row(row):
            continue
        pc = row.get("positionComparison") or {}
        weighted_avg = _number(pc.get("weightedAverage") or pc.get("average"))
        unweighted_avg = _number(
            pc.get("unweightedAverage") or pc.get("avgStatValue")
        )
        actual = _number(row.get("actualValue"))
        line = _number(row.get("line"))
        if weighted_avg is None or unweighted_avg is None or actual is None or line is None:
            continue
        eligible.append({
            "row": row,
            "weightedAvg": weighted_avg,
            "unweightedAvg": unweighted_avg,
            "actual": actual,
            "line": line,
            "outcome": 1 if row.get("result") == "hit" else 0,
            "recommendation": str(row.get("recommendation") or "").lower(),
        })

    if not eligible:
        return {
            "eligibleSamples": 0,
            "leakagePolicy": (
                "Each pick is evaluated using ONLY the weighted/unweighted averages "
                "stored at prediction time. No recomputation of cohort evidence is performed."
            ),
            "message": (
                "No eligible picks found. Picks need positionComparison.weightedAverage, "
                "positionComparison.unweightedAverage (or avgStatValue), actualValue, "
                "line, and a scored directional result."
            ),
            "weighted": None,
            "unweighted": None,
            "churnN": 0,
            "churnPct": None,
            "promotionDecision": {
                "verdict": "CAUTION",
                "summary": "Insufficient data — no eligible picks with cohort evidence found.",
                "issues": [],
                "passed": [],
                "criteria": [],
            },
        }

    n = len(eligible)

    # ── Projection error (MAE/RMSE/meanError vs actualValue) ─────────────────
    def _proj_summary(errors: list[float]) -> dict:
        if not errors:
            return {"n": 0, "mae": None, "rmse": None, "meanError": None}
        mae = sum(abs(e) for e in errors) / len(errors)
        rmse = math.sqrt(sum(e * e for e in errors) / len(errors))
        return {
            "n": len(errors),
            "mae": round(mae, 4),
            "rmse": round(rmse, 4),
            "meanError": round(sum(errors) / len(errors), 4),
        }

    w_errors = [e["actual"] - e["weightedAvg"] for e in eligible]
    u_errors = [e["actual"] - e["unweightedAvg"] for e in eligible]
    w_proj = _proj_summary(w_errors)
    u_proj = _proj_summary(u_errors)

    # ── Counterfactual directional accuracy (same cohort for both methods) ──────
    # For each eligible pick, determine the direction each method implies and
    # compare it against the actual outcome direction.  Both methods are scored
    # on the *same* set of picks so the comparison is apples-to-apples.
    # Ties (actualValue == line) carry no directional signal and are excluded.
    def _implied_dir(avg: float, line: float) -> str:
        if avg > line:
            return "over"
        if avg < line:
            return "under"
        return "tie"

    w_correct = 0
    u_correct = 0
    dir_n = 0        # picks where actual direction is non-tie
    churn_count = 0  # picks where the two methods imply different directions

    for e in eligible:
        w_dir = _implied_dir(e["weightedAvg"], e["line"])
        u_dir = _implied_dir(e["unweightedAvg"], e["line"])
        actual_dir = _implied_dir(e["actual"], e["line"])

        if w_dir != u_dir:
            churn_count += 1

        if actual_dir == "tie":
            continue  # push — no directional signal
        dir_n += 1
        if w_dir == actual_dir:
            w_correct += 1
        if u_dir == actual_dir:
            u_correct += 1

    w_dir_hit_rate = round(w_correct / dir_n * 100, 1) if dir_n else None
    u_dir_hit_rate = round(u_correct / dir_n * 100, 1) if dir_n else None
    churn_pct = round(churn_count / n * 100, 1) if n else None

    # ── Promotion criteria ─────────────────────────────────────────────────────
    criteria: list[dict] = []
    issues: list[str] = []
    passed: list[str] = []

    # 1. Projection error
    w_mae = w_proj.get("mae")
    u_mae = u_proj.get("mae")
    if w_mae is not None and u_mae is not None:
        if w_mae <= u_mae:
            passed.append(
                f"Weighted MAE ({w_mae}) ≤ Unweighted MAE ({u_mae}) — projection not degraded"
            )
            criteria.append({"check": "projection_error", "result": "pass",
                              "weighted": w_mae, "unweighted": u_mae})
        else:
            issues.append(
                f"Weighted MAE ({w_mae}) > Unweighted MAE ({u_mae}) — weighting worsens projection error"
            )
            criteria.append({"check": "projection_error", "result": "fail",
                              "weighted": w_mae, "unweighted": u_mae})
    else:
        criteria.append({"check": "projection_error", "result": "insufficient_data",
                          "weighted": w_mae, "unweighted": u_mae})

    # 2. Counterfactual directional accuracy: weighted not materially worse than unweighted.
    # Both rates are computed on the same cohort (full eligible set, excluding pushes).
    _MIN_DIR_N = 10
    if w_dir_hit_rate is not None and u_dir_hit_rate is not None and dir_n >= _MIN_DIR_N:
        gap = round(w_dir_hit_rate - u_dir_hit_rate, 1)
        if gap >= -2.0:
            passed.append(
                f"Weighted directional hit rate ({w_dir_hit_rate}%) within 2 pp of "
                f"unweighted ({u_dir_hit_rate}%) over {dir_n} picks — accuracy not regressed"
            )
            criteria.append({"check": "directional_accuracy", "result": "pass",
                              "weightedHitRate": w_dir_hit_rate, "unweightedHitRate": u_dir_hit_rate,
                              "directionalN": dir_n, "gapPp": gap,
                              "note": "Rates compare implied direction to actual outcome, same cohort"})
        else:
            issues.append(
                f"Weighted directional hit rate ({w_dir_hit_rate}%) is {abs(gap):.1f} pp below "
                f"unweighted ({u_dir_hit_rate}%) over {dir_n} picks — accuracy regressed"
            )
            criteria.append({"check": "directional_accuracy", "result": "fail",
                              "weightedHitRate": w_dir_hit_rate, "unweightedHitRate": u_dir_hit_rate,
                              "directionalN": dir_n, "gapPp": gap,
                              "note": "Rates compare implied direction to actual outcome, same cohort"})
    else:
        note = (
            f"Need ≥{_MIN_DIR_N} picks with a non-tie actual outcome; have {dir_n}"
        )
        criteria.append({"check": "directional_accuracy", "result": "insufficient_data",
                          "weightedHitRate": w_dir_hit_rate, "unweightedHitRate": u_dir_hit_rate,
                          "directionalN": dir_n, "note": note})

    # 3. Recommendation churn is bounded
    _CHURN_THRESHOLD = 15.0
    if churn_pct is not None:
        if churn_pct < _CHURN_THRESHOLD:
            passed.append(
                f"Recommendation churn {churn_pct}% is below {_CHURN_THRESHOLD}% threshold"
            )
            criteria.append({"check": "churn", "result": "pass",
                              "churnPct": churn_pct, "threshold": _CHURN_THRESHOLD})
        else:
            issues.append(
                f"Recommendation churn {churn_pct}% ({churn_count}/{n} picks) exceeds "
                f"{_CHURN_THRESHOLD}% — weighted method changes too many saved picks"
            )
            criteria.append({"check": "churn", "result": "fail",
                              "churnPct": churn_pct, "threshold": _CHURN_THRESHOLD})
    else:
        criteria.append({"check": "churn", "result": "insufficient_data",
                          "churnPct": None, "threshold": _CHURN_THRESHOLD})

    # ── Verdict ────────────────────────────────────────────────────────────────
    # Promotion to GO requires:
    #   a) at least _MIN_ELIGIBLE_FOR_PROMOTION settled picks with cohort evidence
    #   b) every criterion must be "pass" — insufficient_data is treated as CAUTION
    #   c) no criterion can be "fail"
    _MIN_ELIGIBLE_FOR_PROMOTION = 30
    has_insufficient = any(c["result"] == "insufficient_data" for c in criteria)
    has_failures = any(c["result"] == "fail" for c in criteria)
    all_pass = (
        not has_failures
        and not has_insufficient
        and all(c["result"] == "pass" for c in criteria)
        and bool(criteria)
    )

    if n < _MIN_ELIGIBLE_FOR_PROMOTION:
        verdict = "CAUTION"
        summary = (
            f"Insufficient data — only {n} eligible pick(s) carry cohort evidence "
            f"(minimum {_MIN_ELIGIBLE_FOR_PROMOTION} required for a GO verdict). "
            "Accumulate more settled soccer picks with same-role opponent cohort data before promoting."
        )
    elif has_insufficient:
        verdict = "CAUTION"
        summary = (
            "One or more promotion criteria could not be evaluated (insufficient agreeing picks "
            "in at least one method). All criteria must fully pass before promoting."
        )
    elif has_failures:
        verdict = "NO_GO"
        summary = (
            "Weighted opponent evidence fails key promotion criteria. "
            "Keep shadow-only and investigate the flagged issues before promoting."
        )
    elif all_pass:
        verdict = "GO"
        summary = (
            "Weighted opponent evidence passes all out-of-sample promotion criteria. "
            "Safe to promote from shadow-only to live mode by setting "
            "WEIGHTED_OPPONENT_EVIDENCE_MODE=live in the backend environment."
        )
    else:
        verdict = "CAUTION"
        summary = (
            "Mixed results — some criteria pass, others do not. "
            "Keep shadow-only; investigate flagged issues before promoting."
        )

    dates = _date_range(ordered)
    return {
        "eligibleSamples": n,
        "totalRows": len(ordered),
        "dateRange": dates,
        "leakagePolicy": (
            "Each pick is evaluated using ONLY the weighted/unweighted averages "
            "stored at prediction time. No recomputation of cohort evidence is performed."
        ),
        "directionalN": dir_n,
        "directionalNote": (
            "Directional hit rates are computed on the same cohort for both methods. "
            "Each method's implied direction (avg vs line) is compared against the actual "
            "outcome direction (actualValue vs line). Ties are excluded."
        ),
        "weighted": {
            "projection": w_proj,
            "directionalHitRate": w_dir_hit_rate,
            "directionalCorrect": w_correct,
        },
        "unweighted": {
            "projection": u_proj,
            "directionalHitRate": u_dir_hit_rate,
            "directionalCorrect": u_correct,
        },
        "churnN": churn_count,
        "churnPct": churn_pct,
        "promotionDecision": {
            "verdict": verdict,
            "summary": summary,
            "issues": issues,
            "passed": passed,
            "criteria": criteria,
            "promotionEnvVar": "WEIGHTED_OPPONENT_EVIDENCE_MODE",
            "promotionValues": {"shadow": "shadow_only_evidence_card_only", "live": "apply_weighted_average_to_projection"},
            "promotionCommand": "Set WEIGHTED_OPPONENT_EVIDENCE_MODE=live in the backend environment, then redeploy.",
            "note": (
                "GO = all criteria pass; "
                "CAUTION = mixed or insufficient evidence; "
                "NO_GO = one or more criteria fail"
            ),
        },
    }


def validate_bzzoiro_position_replay(rows: list[dict]) -> dict:
    """Compare settled soccer picks where Bzzoiro position data was valid vs unavailable.

    This is a group-comparison replay, not a walk-forward simulation:

    - Group A (bzzoiro_valid): settled picks where
      ``tacticalContext.bzzoiroEnrichment.positionValidation.valid == True``
      and the fix-date match was "exact".
    - Group B (bzzoiro_absent): all other settled soccer picks in the corpus
      (bzzoiro unavailable, failed validation, or non-exact fixture match).

    For each group we compute:
      - Hit rate (HIT / (HIT + MISS))
      - Directional accuracy (implied projection direction vs actual outcome)
      - Projection MAE vs actualValue when projectedValue is present

    The comparison answers: "do picks where Bzzoiro confirmed the player's
    position hit more often than picks where we relied on API-Football alone?"

    Promotion criteria (all must pass for a GO verdict):
      1. Bzzoiro group hit rate ≥ baseline group hit rate − 2 pp  (not harmful)
      2. ≥ _MIN_ELIGIBLE_FOR_PROMOTION scored bzzoiro-valid picks
      3. Bzzoiro projection MAE ≤ baseline MAE + 0.1  (within tolerance)

    Note: because we cannot control which picks received bzzoiro enrichment,
    this is an observational comparison, not a controlled experiment. Confounders
    (e.g. richer data available for top-league matches) may inflate group A's
    apparent accuracy. The verdict is therefore an indicator, not a definitive
    causal proof.

    Leakage policy: all metrics read the fields stored at prediction time
    (projectedValue, confidenceScore, recommendation, result). No post-hoc
    recomputation of projections is performed.
    """
    deduped = dedupe_prediction_rows(rows)
    ordered = _sorted_rows(deduped)

    group_a: list[dict] = []  # bzzoiro positionValidation.valid=True, exact fixture match
    group_b: list[dict] = []  # everything else
    # Voided picks (DNP, insufficient minutes, etc.) with a valid bzzoiroEnrichment
    # snapshot are real Bzzoiro-covered fixtures.  They cannot contribute to
    # hit-rate or MAE metrics — no direction outcome is known — but they are
    # counted separately so the promotion corpus size is correctly reported.
    n_voided_covered: int = 0

    for row in ordered:
        tc = row.get("tacticalContext") or {}
        bzz = tc.get("bzzoiroEnrichment") or {}
        pv = bzz.get("positionValidation") or {}
        bzz_valid = (
            bool(pv.get("valid"))
            and pv.get("fixtureDateMatch") == "exact"
        )
        if not _is_scored_directional_row(row):
            # Count voided picks that carry a valid Bzzoiro snapshot separately
            # so the coverage corpus size is not under-reported.
            if bzz_valid and bool(row.get("voidReason")):
                n_voided_covered += 1
            continue
        if bzz_valid:
            group_a.append(row)
        else:
            group_b.append(row)

    def _group_stats(group: list[dict], label: str) -> dict:
        n = len(group)
        if n == 0:
            return {
                "n": 0,
                "hitRate": None,
                "directionalN": None,
                "directionalHitRate": None,
                "projection": {"n": 0, "mae": None, "rmse": None, "meanError": None},
                "label": label,
            }
        hits = sum(1 for r in group if r.get("result") == "hit")
        hit_rate = round(hits / n * 100, 1)

        # Directional accuracy: implied direction (projectedValue vs line) vs actual
        dir_correct = 0
        dir_n = 0
        proj_errors: list[float] = []
        for row in group:
            actual = _number(row.get("actualValue"))
            projected = _number(row.get("projectedValue"))
            line = _number(row.get("line"))
            if actual is not None and projected is not None:
                proj_errors.append(actual - projected)
            if actual is not None and line is not None:
                actual_dir = "over" if actual > line else "under" if actual < line else "tie"
                if actual_dir == "tie":
                    continue
                dir_n += 1
                if projected is not None:
                    proj_dir = "over" if projected > line else "under" if projected < line else "tie"
                    if proj_dir == actual_dir:
                        dir_correct += 1

        dir_hit_rate = round(dir_correct / dir_n * 100, 1) if dir_n else None
        mae = round(sum(abs(e) for e in proj_errors) / len(proj_errors), 4) if proj_errors else None
        rmse_val = (
            round(math.sqrt(sum(e * e for e in proj_errors) / len(proj_errors)), 4)
            if proj_errors else None
        )
        mean_err = round(sum(proj_errors) / len(proj_errors), 4) if proj_errors else None
        return {
            "n": n,
            "hits": hits,
            "hitRate": hit_rate,
            "directionalN": dir_n,
            "directionalHitRate": dir_hit_rate,
            "directionalCorrect": dir_correct,
            "projection": {
                "n": len(proj_errors),
                "mae": mae,
                "rmse": rmse_val,
                "meanError": mean_err,
            },
            "label": label,
        }

    stats_a = _group_stats(group_a, "bzzoiro_valid")
    stats_b = _group_stats(group_b, "bzzoiro_absent")

    _MIN_ELIGIBLE_FOR_PROMOTION = 20

    # ── Promotion criteria ────────────────────────────────────────────────────
    criteria: list[dict] = []
    issues: list[str] = []
    passed: list[str] = []

    # 1. Sample size gate
    n_a = stats_a["n"]
    if n_a >= _MIN_ELIGIBLE_FOR_PROMOTION:
        passed.append(
            f"Bzzoiro-valid group has {n_a} scored picks "
            f"(≥ {_MIN_ELIGIBLE_FOR_PROMOTION} required)"
        )
        criteria.append({"check": "sample_size", "result": "pass",
                          "bzzoiroValidN": n_a, "minimum": _MIN_ELIGIBLE_FOR_PROMOTION})
    else:
        issues.append(
            f"Only {n_a} bzzoiro-valid scored picks "
            f"(need ≥ {_MIN_ELIGIBLE_FOR_PROMOTION})"
        )
        criteria.append({"check": "sample_size", "result": "insufficient_data",
                          "bzzoiroValidN": n_a, "minimum": _MIN_ELIGIBLE_FOR_PROMOTION})

    # 2. Hit rate not materially worse than baseline
    hr_a = stats_a["hitRate"]
    hr_b = stats_b["hitRate"]
    _MIN_HIT_N = 10
    if hr_a is not None and hr_b is not None and n_a >= _MIN_HIT_N:
        gap = round(hr_a - hr_b, 1)
        if gap >= -2.0:
            passed.append(
                f"Bzzoiro-valid hit rate ({hr_a}%) within 2 pp of "
                f"absent group ({hr_b}%) — not harmful"
            )
            criteria.append({"check": "hit_rate", "result": "pass",
                              "bzzoiroHitRate": hr_a, "baselineHitRate": hr_b,
                              "gapPp": gap})
        else:
            issues.append(
                f"Bzzoiro-valid hit rate ({hr_a}%) is {abs(gap):.1f} pp below "
                f"absent group ({hr_b}%) — position enrichment may degrade calibration"
            )
            criteria.append({"check": "hit_rate", "result": "fail",
                              "bzzoiroHitRate": hr_a, "baselineHitRate": hr_b,
                              "gapPp": gap})
    else:
        note = f"Need ≥{_MIN_HIT_N} picks in bzzoiro-valid group; have {n_a}"
        criteria.append({"check": "hit_rate", "result": "insufficient_data",
                          "bzzoiroHitRate": hr_a, "baselineHitRate": hr_b,
                          "note": note})

    # 3. Projection MAE not materially worse than baseline (within 0.1 tolerance)
    mae_a = (stats_a["projection"] or {}).get("mae")
    mae_b = (stats_b["projection"] or {}).get("mae")
    _MAE_TOLERANCE = 0.1
    if mae_a is not None and mae_b is not None:
        if mae_a <= mae_b + _MAE_TOLERANCE:
            passed.append(
                f"Bzzoiro-valid MAE ({mae_a}) ≤ absent-group MAE ({mae_b}) + {_MAE_TOLERANCE} tolerance"
            )
            criteria.append({"check": "projection_mae", "result": "pass",
                              "bzzoiroMAE": mae_a, "baselineMAE": mae_b,
                              "tolerance": _MAE_TOLERANCE})
        else:
            issues.append(
                f"Bzzoiro-valid MAE ({mae_a}) exceeds absent-group MAE ({mae_b}) "
                f"by more than {_MAE_TOLERANCE} — projection accuracy degraded"
            )
            criteria.append({"check": "projection_mae", "result": "fail",
                              "bzzoiroMAE": mae_a, "baselineMAE": mae_b,
                              "tolerance": _MAE_TOLERANCE})
    else:
        criteria.append({"check": "projection_mae", "result": "insufficient_data",
                          "bzzoiroMAE": mae_a, "baselineMAE": mae_b,
                          "tolerance": _MAE_TOLERANCE})

    # ── Verdict ───────────────────────────────────────────────────────────────
    has_insufficient = any(c["result"] == "insufficient_data" for c in criteria)
    has_failures = any(c["result"] == "fail" for c in criteria)
    all_pass = (
        not has_failures
        and not has_insufficient
        and all(c["result"] == "pass" for c in criteria)
        and bool(criteria)
    )

    if n_a < _MIN_ELIGIBLE_FOR_PROMOTION:
        verdict = "CAUTION"
        summary = (
            f"Insufficient data — only {n_a} bzzoiro-valid scored pick(s) "
            f"(minimum {_MIN_ELIGIBLE_FOR_PROMOTION} required for a GO verdict). "
            "Accumulate more settled soccer picks with exact Bzzoiro fixture coverage "
            "before promoting."
        )
    elif has_failures:
        verdict = "NO_GO"
        summary = (
            "Bzzoiro position enrichment fails key promotion criteria. "
            "Keep shadow-only and investigate the flagged issues before promoting."
        )
    elif has_insufficient:
        verdict = "CAUTION"
        summary = (
            "One or more promotion criteria could not be evaluated (insufficient data). "
            "All criteria must fully pass before promoting."
        )
    elif all_pass:
        verdict = "GO"
        summary = (
            "Bzzoiro position enrichment passes all out-of-sample promotion criteria. "
            "Safe to promote from shadow-only to live mode by setting "
            "BZZOIRO_POSITION_LIVE=live in the backend environment."
        )
    else:
        verdict = "CAUTION"
        summary = (
            "Mixed results — some criteria pass, others do not. "
            "Keep shadow-only; investigate flagged issues before promoting."
        )

    dates = _date_range(ordered)
    return {
        "totalRows": len(ordered),
        "bzzoiroValidN": n_a,
        "bzzoiroAbsentN": stats_b["n"],
        # Voided picks (DNP, etc.) with a valid Bzzoiro snapshot: counted for
        # corpus-size purposes only.  Not included in hit-rate/MAE groups.
        "nVoidedCovered": n_voided_covered,
        "dateRange": dates,
        "leakagePolicy": (
            "Metrics read fields stored at prediction time "
            "(result, projectedValue, line, actualValue). "
            "No post-hoc recomputation of projections is performed."
        ),
        "observationalCaveat": (
            "This is an observational group comparison, not a controlled experiment. "
            "Picks receiving Bzzoiro enrichment may systematically differ from those "
            "that do not (e.g. top-league coverage bias). Treat verdict as an indicator."
        ),
        "bzzoiroValid": stats_a,
        "bzzoiroAbsent": stats_b,
        "criteria": criteria,
        "promotionDecision": {
            "verdict": verdict,
            "summary": summary,
            "issues": issues,
            "passed": passed,
            "criteria": criteria,
            "promotionEnvVar": "BZZOIRO_POSITION_LIVE",
            "promotionValues": {
                "shadow": "position_supplement_explanation_only",
                "live": "position_supplement_also_overrides_generic_api_football_labels",
            },
            "promotionCommand": (
                "Set BZZOIRO_POSITION_LIVE=live in the backend environment, "
                "then redeploy."
            ),
            "note": (
                "GO = all criteria pass with sufficient data; "
                "CAUTION = mixed or insufficient evidence; "
                "NO_GO = one or more criteria fail"
            ),
        },
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
