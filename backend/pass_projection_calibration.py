"""
Walk-forward residual calibration for soccer passing projections.

This module intentionally defaults to shadow mode.  It mines only settled,
non-voided soccer pass picks, deduplicates them by player/event/market, and
uses a small hierarchical residual correction with recency and shrinkage.

The correction is kept separate from confidence calibration: this module
changes a projection mean, while confidence_calibration.py changes display
confidence.  Keeping those concerns separate makes the audit trail explicit.
"""
from __future__ import annotations

import math
import os
import re
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any


PASS_PROPS = frozenset({"pass_attempts", "passes"})
_REFRESH_SECONDS = 6 * 3600
_RECENT_DAYS = 45
_MIN_SAMPLE = 10
_SHRINK_K = 30.0
_MAX_CORRECTION = 0.05

_cache: dict[str, Any] = {
    "loaded": False,
    "loaded_at": 0.0,
    "snapshot_at": None,
    "rows": 0,
    "deduped": 0,
    "buckets": {},
}


def _as_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _norm_name(value: Any) -> str:
    return " ".join(str(value or "").lower().strip().split())


def _role_bucket(position: Any, role: Any) -> str:
    text = f"{position or ''} {role or ''}".lower()
    tokens = set(re.findall(r"[a-z]+", text))
    if tokens & {"goalkeeper", "keeper", "gk"}:
        return "GK"
    if tokens & {"cb", "fullback", "lb", "rb", "wingback", "wingbacks"} or any(
        phrase in text for phrase in ("center back", "centre back", "full back", "wing-back")
    ):
        return "CB_FB"
    if tokens & {"cdm", "dm", "regista", "anchor", "holding"} or any(
        phrase in text for phrase in ("defensive midfielder", "deep-lying", "deep lying", "ball winner")
    ):
        return "DM"
    if tokens & {"cm", "mc", "midfielder", "mezzala", "playmaker"} or any(
        phrase in text for phrase in ("box-to-box", "box to box")
    ):
        return "CM"
    if tokens & {"cam", "am", "winger", "wide", "forward", "striker", "st", "cf"} or "inside forward" in text:
        return "AM_WIDE_ST"
    return "UNKNOWN"


def _event_key(row: dict) -> tuple:
    player = row.get("playerNameKey") or _norm_name(row.get("playerName"))
    fixture = row.get("fixtureId")
    if not fixture:
        fixture = (
            row.get("fixtureDate")
            or row.get("matchDate")
            or row.get("opponentName")
            or row.get("opponent")
            or "unknown-event"
        )
    return (
        player,
        str(fixture),
        str(row.get("propType") or "").lower().strip(),
        str(row.get("line") if row.get("line") is not None else ""),
        str(row.get("recommendation") or "").lower().strip(),
    )


def _bucket_keys(row: dict) -> tuple[tuple, ...]:
    league = int(row.get("leagueId") or 0)
    role = _role_bucket(row.get("position"), row.get("role"))
    direction = str(row.get("recommendation") or "").lower().strip()
    # Most specific first, then safe parent buckets.
    return (
        ("league_role_direction", league, role, direction),
        ("league_direction", league, direction),
        ("role_direction", role, direction),
        ("global_direction", direction),
    )


def _empty_bucket() -> dict:
    return {"n": 0, "sum": 0.0, "recent_n": 0, "recent_sum": 0.0}


def _finalize_bucket(raw: dict) -> dict:
    n = raw["n"]
    recent_n = raw["recent_n"]
    if not n:
        return {"n": 0, "recentN": 0}
    # Recent data gets more influence, but long-term data remains a stabilizer.
    long_mean = raw["sum"] / n
    recent_mean = raw["recent_sum"] / recent_n if recent_n else long_mean
    recent_weight = min(0.60, recent_n / 20.0) if recent_n else 0.0
    residual = (recent_mean * recent_weight) + (long_mean * (1.0 - recent_weight))
    effective_n = n + min(recent_n, 20) * 0.5
    shrink = effective_n / (effective_n + _SHRINK_K)
    return {
        "n": n,
        "recentN": recent_n,
        "residual": round(residual, 4),
        "shrink": round(shrink, 4),
    }


def _eligible_row(row: dict, snapshot_at: datetime | None = None) -> bool:
    """Return whether a settled row is safe training/evaluation data."""
    result = str(row.get("result") or "").lower()
    direction = str(row.get("recommendation") or "").lower().strip()
    if (
        str(row.get("sport") or "soccer").lower() != "soccer"
        or str(row.get("propType") or "").lower() not in PASS_PROPS
        or result not in {"hit", "miss"}
        or direction not in {"over", "under"}
        or row.get("actualValue") is None
        or row.get("projectedValue") is None
        or row.get("voidReason")
        or row.get("correctedManually") is True
    ):
        return False
    settled_at = _as_dt(row.get("settledAt"))
    if not settled_at:
        return False
    return snapshot_at is None or settled_at < snapshot_at


def _dedupe_rows(rows: list[dict], snapshot_at: datetime | None = None) -> list[dict]:
    deduped: dict[tuple, dict] = {}
    for row in rows:
        if not _eligible_row(row, snapshot_at):
            continue
        key = _event_key(row)
        current_dt = _as_dt(row.get("settledAt"))
        previous_dt = _as_dt(deduped.get(key, {}).get("settledAt"))
        if current_dt and (key not in deduped or current_dt >= (previous_dt or datetime.min.replace(tzinfo=timezone.utc))):
            deduped[key] = row
    return sorted(
        deduped.values(),
        key=lambda row: _as_dt(row.get("settledAt")) or datetime.min.replace(tzinfo=timezone.utc),
    )


def _build_buckets(rows: list[dict], snapshot_at: datetime) -> dict[tuple, dict]:
    """Build buckets using only rows settled before snapshot_at."""
    buckets: dict[tuple, dict] = defaultdict(_empty_bucket)
    recent_cutoff = snapshot_at - timedelta(days=_RECENT_DAYS)
    for row in _dedupe_rows(rows, snapshot_at):
        try:
            projected = float(row["projectedValue"])
            actual = float(row["actualValue"])
            if projected <= 0 or not math.isfinite(projected) or not math.isfinite(actual):
                continue
            residual = (actual - projected) / max(abs(projected), 1.0)
        except (TypeError, ValueError):
            continue
        settled_at = _as_dt(row.get("settledAt"))
        for key in _bucket_keys(row):
            bucket = buckets[key]
            bucket["n"] += 1
            bucket["sum"] += residual
            if settled_at and settled_at >= recent_cutoff:
                bucket["recent_n"] += 1
                bucket["recent_sum"] += residual
    return {key: _finalize_bucket(value) for key, value in buckets.items()}


def _lookup_buckets(buckets: dict[tuple, dict], row: dict) -> dict:
    direction = str(row.get("recommendation") or "").lower().strip()
    candidates = _bucket_keys(row)
    for key in candidates:
        candidate = buckets.get(key)
        if candidate and candidate.get("n", 0) >= _MIN_SAMPLE:
            correction = max(
                -_MAX_CORRECTION,
                min(_MAX_CORRECTION, candidate["residual"] * candidate["shrink"]),
            )
            return {
                "found": True,
                "bucket": list(key),
                "n": int(candidate["n"]),
                "recentN": int(candidate.get("recentN", 0)),
                "correction": correction,
                "residual": candidate["residual"],
            }
    return {
        "found": False,
        "bucket": None,
        "n": 0,
        "recentN": 0,
        "correction": 0.0,
        "residual": 0.0,
    }


def _direction_hit(actual: float, line: float, projection: float) -> bool | None:
    if actual == line or projection == line:
        return None
    actual_direction = "over" if actual > line else "under"
    predicted_direction = "over" if projection > line else "under"
    return actual_direction == predicted_direction


def walk_forward_validate(rows: list[dict]) -> dict:
    """
    Evaluate raw versus calibrated projections using strict walk-forward splits.

    Every row is evaluated against buckets built only from earlier settled rows.
    The return value is JSON-serializable and suitable for an admin/offline
    report.  Live mode is never enabled by this function.
    """
    prepared = _dedupe_rows(rows)
    raw_abs = calibrated_abs = 0.0
    raw_signed = calibrated_signed = 0.0
    raw_direction_hits = calibrated_direction_hits = 0
    direction_samples = 0
    calibrated_samples = 0
    leakage_violations = 0
    evaluated = 0

    for index, row in enumerate(prepared):
        settled_at = _as_dt(row.get("settledAt"))
        if not settled_at:
            continue
        prior = prepared[:index]
        # Explicit invariant: no training row may be at or after the test row.
        if any(
            (_as_dt(previous.get("settledAt")) or settled_at) >= settled_at
            for previous in prior
        ):
            leakage_violations += 1
        buckets = _build_buckets(prior, settled_at)
        try:
            raw_projection = float(row["projectedValue"])
            actual = float(row["actualValue"])
            line = float(row.get("line"))
            if not all(math.isfinite(value) for value in (raw_projection, actual, line)):
                continue
        except (TypeError, ValueError):
            continue

        calibration = _lookup_buckets(buckets, row)
        calibrated_projection = raw_projection * (1.0 + calibration["correction"])
        raw_error = actual - raw_projection
        calibrated_error = actual - calibrated_projection
        raw_abs += abs(raw_error)
        calibrated_abs += abs(calibrated_error)
        raw_signed += raw_error
        calibrated_signed += calibrated_error
        evaluated += 1
        if calibration["found"]:
            calibrated_samples += 1

        raw_hit = _direction_hit(actual, line, raw_projection)
        calibrated_hit = _direction_hit(actual, line, calibrated_projection)
        if raw_hit is not None:
            direction_samples += 1
            raw_direction_hits += int(raw_hit)
            calibrated_direction_hits += int(calibrated_hit is True)

    def _metric(total: float, count: int) -> float | None:
        return round(total / count, 4) if count else None

    return {
        "eligibleSamples": len(prepared),
        "evaluatedSamples": evaluated,
        "calibratedSamples": calibrated_samples,
        "raw": {
            "mae": _metric(raw_abs, evaluated),
            "signedBias": _metric(raw_signed, evaluated),
            "directionHitRate": round(raw_direction_hits / direction_samples, 4)
            if direction_samples else None,
        },
        "calibrated": {
            "mae": _metric(calibrated_abs, evaluated),
            "signedBias": _metric(calibrated_signed, evaluated),
            "directionHitRate": round(calibrated_direction_hits / direction_samples, 4)
            if direction_samples else None,
        },
        "directionSamples": direction_samples,
        "leakageViolations": leakage_violations,
        "windowDays": _RECENT_DAYS,
        "maxCorrection": _MAX_CORRECTION,
        "minBucketSample": _MIN_SAMPLE,
    }


async def _refresh(db, snapshot_at: datetime) -> None:
    cursor = db.picks.find(
        {
            "sport": "soccer",
            "propType": {"$in": list(PASS_PROPS)},
            "result": {"$in": ["hit", "miss"]},
            "recommendation": {"$in": ["over", "under"]},
            "actualValue": {"$ne": None},
            "projectedValue": {"$ne": None},
            "settledAt": {"$ne": None},
            "voidReason": {"$exists": False},
            "correctedManually": {"$ne": True},
        },
        {
            "_id": 0, "playerName": 1, "playerNameKey": 1, "fixtureId": 1,
            "fixtureDate": 1, "matchDate": 1, "opponentName": 1, "opponent": 1,
            "propType": 1, "line": 1, "recommendation": 1, "leagueId": 1,
            "position": 1, "role": 1, "actualValue": 1, "projectedValue": 1,
            "settledAt": 1,
        },
    )
    rows = await cursor.to_list(length=50000)
    deduped: dict[tuple, dict] = {}
    eligible_rows = []
    for row in rows:
        settled_at = _as_dt(row.get("settledAt"))
        # Filter in Python because older settlement records contain ISO
        # strings while some Mongo writes contain BSON datetimes.  A Mongo
        # string-only $lt would silently mix those types and risk leakage.
        if not settled_at or settled_at >= snapshot_at:
            continue
        eligible_rows.append(row)

    for row in eligible_rows:
        key = _event_key(row)
        # Keep the latest record for a duplicate event.  This prevents repeated
        # settlement writes from making one fixture count multiple times.
        current_dt = _as_dt(row.get("settledAt")) or datetime.min.replace(tzinfo=timezone.utc)
        previous_dt = _as_dt(deduped.get(key, {}).get("settledAt")) if key in deduped else None
        if key not in deduped or current_dt >= (previous_dt or datetime.min.replace(tzinfo=timezone.utc)):
            deduped[key] = row

    buckets: dict[tuple, dict] = defaultdict(_empty_bucket)
    recent_cutoff = snapshot_at - timedelta(days=_RECENT_DAYS)
    for row in deduped.values():
        try:
            projected = float(row["projectedValue"])
            actual = float(row["actualValue"])
            if projected <= 0 or not math.isfinite(projected) or not math.isfinite(actual):
                continue
            residual = (actual - projected) / max(abs(projected), 1.0)
        except (TypeError, ValueError):
            continue
        settled_at = _as_dt(row.get("settledAt"))
        for key in _bucket_keys(row):
            bucket = buckets[key]
            bucket["n"] += 1
            bucket["sum"] += residual
            if settled_at and settled_at >= recent_cutoff:
                bucket["recent_n"] += 1
                bucket["recent_sum"] += residual

    _cache.update({
        "loaded": True,
        "loaded_at": time.time(),
        "snapshot_at": snapshot_at,
        "rows": len(eligible_rows),
        "deduped": len(deduped),
        "buckets": {key: _finalize_bucket(value) for key, value in buckets.items()},
    })
    print(
        f"[PASS PROJECTION CAL] refreshed: rows={len(rows)} deduped={len(deduped)} "
        f"buckets={len(buckets)} snapshot={snapshot_at.isoformat()}"
    )


async def ensure_loaded(db, snapshot_at: datetime | None = None) -> None:
    snapshot = snapshot_at or datetime.now(timezone.utc)
    if (
        not _cache["loaded"]
        or time.time() - float(_cache["loaded_at"]) > _REFRESH_SECONDS
    ):
        try:
            await _refresh(db, snapshot)
        except Exception as exc:
            print(f"[PASS PROJECTION CAL] refresh failed: {exc}")


def lookup(
    league_id: int | None,
    position: str,
    role: str,
    direction: str,
    posterior_mean: float,
) -> dict:
    """Return a shadow/live correction for one projected pass direction."""
    inert = {
        "found": False, "mode": os.environ.get("PASS_PROJECTION_CALIBRATION_MODE", "shadow"),
        "multiplier": 1.0, "correction": 0.0, "n": 0, "recentN": 0,
        "bucket": None, "residual": 0.0, "shrink": 0.0, "applied": False,
    }
    if (
        not _cache["loaded"]
        or direction not in {"over", "under"}
        or posterior_mean is None
        or posterior_mean <= 0
    ):
        return inert

    league = int(league_id or 0)
    role_bucket = _role_bucket(position, role)
    candidates = (
        ("league_role_direction", league, role_bucket, direction),
        ("league_direction", league, direction),
        ("role_direction", role_bucket, direction),
        ("global_direction", direction),
    )
    selected_key = None
    selected = None
    for key in candidates:
        candidate = _cache["buckets"].get(key)
        if candidate and candidate.get("n", 0) >= _MIN_SAMPLE:
            selected_key, selected = key, candidate
            break
    if not selected:
        return inert

    correction = max(
        -_MAX_CORRECTION,
        min(_MAX_CORRECTION, selected["residual"] * selected["shrink"]),
    )
    mode = os.environ.get("PASS_PROJECTION_CALIBRATION_MODE", "shadow").lower()
    if mode not in {"off", "shadow", "live"}:
        mode = "shadow"
    return {
        "found": True,
        "mode": mode,
        "multiplier": round(1.0 + correction, 4),
        "correction": round(correction, 4),
        "n": int(selected["n"]),
        "recentN": int(selected.get("recentN", 0)),
        "bucket": list(selected_key),
        "residual": round(selected["residual"], 4),
        "shrink": round(selected["shrink"], 4),
        "applied": mode == "live" and abs(correction) > 0.0005,
    }


def stats() -> dict:
    return {
        "loaded": _cache["loaded"],
        "rows": _cache["rows"],
        "deduped": _cache["deduped"],
        "buckets": len(_cache["buckets"]),
        "snapshotAt": _cache["snapshot_at"].isoformat() if _cache["snapshot_at"] else None,
    }