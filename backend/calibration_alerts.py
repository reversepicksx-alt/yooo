"""
Calibration Alerts — detects systematic confidence mis-calibration by sport/prop.

Scans walk-forward replay metrics (Brier score + calibration gap) against
configurable thresholds and emits suppression signals that mirror the
prop_safety_cache AVOID/RISKY flags.

Alert levels (parallel to prop_safety_cache semantics):
  AVOID   — heavily over-stated confidence (Brier >= BRIER_ALERT OR max gap >= GAP_PP_ALERT)
  RISKY   — moderately over-stated       (Brier >= BRIER_WARN  OR max gap >= GAP_PP_WARN)
  OK      — within acceptable calibration bounds

A "gap" here is gapPp = observedPct - predictedPct (negative = model over-predicts).
We flag when the model is systematically OVER-CONFIDENT, i.e. gapPp is strongly negative.

Refresh cycle: every 6h (same cadence as prop_safety_cache).
Min samples: BRIER_MIN_N (30) for sport-level, PROP_MIN_N (30) for prop-level.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

from model_metrics import walk_forward_replay

# ── Thresholds (configurable) ──────────────────────────────────────────────────
# Brier score: perfect calibration at 50% base rate ≈ 0.25.
# Values materially above 0.25 indicate systematic over-confidence.
BRIER_ALERT = 0.28   # alert:  heavily over-confident
BRIER_WARN  = 0.26   # risky:  mildly over-confident

# Max calibration over-prediction in any single confidence bin (abs pp).
# Negative gapPp (observed < predicted) means model overstates confidence.
GAP_PP_ALERT = 15    # alert: ≥15pp over-confidence gap in some bin
GAP_PP_WARN  = 10    # risky: ≥10pp over-confidence gap in some bin

# Minimum sample sizes before flagging anything (avoid noise from thin data)
BRIER_MIN_N = 30     # sport-level
GAP_MIN_BIN_N = 15   # per-bin minimum before its gap is counted
PROP_MIN_N  = 30     # prop-level (same as sport-level for consistency)
DIRECTION_MIN_N = 100
OVER_DIRECTION_AVOID_RATE = 55.0
OVER_DIRECTION_RISKY_RATE = 60.0

# ── In-memory alert stores ─────────────────────────────────────────────────────
# sport → { alertLevel, brierScore, maxOverGapPp, n, worstBin, updatedAt, calibrationBins }
_SPORT_ALERTS: Dict[str, dict] = {}
# (sport, propType) → same shape + sport/propType fields
_PROP_ALERTS: Dict[Tuple[str, str], dict] = {}
# Direction alerts are intentionally separate from generic confidence alerts:
# the production audit found a strong OVER/UNDER split, so an aggregate Brier
# score must not make a weak OVER bucket look healthy because UNDER is strong.
_DIRECTION_ALERTS: Dict[Tuple[str, Optional[str], str], dict] = {}

_ALERTS_LOCK = asyncio.Lock()
_LAST_REFRESH: Optional[datetime] = None


def _alert_level(
    brier: Optional[float],
    max_over_gap_pp: Optional[float],
    n: int,
    min_n: int = BRIER_MIN_N,
) -> str:
    """Derive alert level from Brier score and worst over-confidence gap.

    max_over_gap_pp is the largest *positive* over-prediction magnitude:
    i.e. abs(gapPp) where gapPp < 0 (observed < predicted).
    """
    if n < min_n:
        return "OK"  # insufficient data — silence the alert
    if brier is not None and brier >= BRIER_ALERT:
        return "AVOID"
    if max_over_gap_pp is not None and max_over_gap_pp >= GAP_PP_ALERT:
        return "AVOID"
    if brier is not None and brier >= BRIER_WARN:
        return "RISKY"
    if max_over_gap_pp is not None and max_over_gap_pp >= GAP_PP_WARN:
        return "RISKY"
    return "OK"


def _worst_overconfidence_gap(calibration_bins: list[dict]) -> Tuple[Optional[float], Optional[str]]:
    """Return (max_over_gap_pp, worst_bin_label) from prospectiveCalibration output.

    gapPp < 0 means model predicted higher confidence than observed hit rate
    (systematic over-confidence).  We return the magnitude of the worst such gap.
    """
    worst_gap: Optional[float] = None
    worst_label: Optional[str] = None
    for b in calibration_bins:
        if (b.get("n") or 0) < GAP_MIN_BIN_N:
            continue
        g = b.get("gapPp")
        if g is None:
            continue
        # Negative gapPp = over-confident. We track the most negative (largest magnitude).
        over_gap = -g  # positive = over-confidence magnitude
        if over_gap > 0 and (worst_gap is None or over_gap > worst_gap):
            worst_gap = over_gap
            worst_label = b.get("label")
    return worst_gap, worst_label


def _build_alert(
    *,
    sport: str,
    prop_type: Optional[str],
    replay: dict,
    min_n: int,
) -> dict:
    """Build a single alert record from a walk_forward_replay result."""
    classification = replay.get("classification") or {}
    brier = classification.get("brierScore")
    n     = classification.get("n", 0)

    calib_bins = replay.get("prospectiveCalibration") or []
    max_over_gap, worst_bin = _worst_overconfidence_gap(calib_bins)

    level = _alert_level(brier, max_over_gap, n, min_n)

    record: dict = {
        "alertLevel":     level,
        "sport":          sport,
        "brierScore":     brier,
        "n":              n,
        "maxOverGapPp":   round(max_over_gap, 1) if max_over_gap is not None else None,
        "worstBin":       worst_bin,
        "calibrationBins": calib_bins,
        "updatedAt":      datetime.now(timezone.utc).isoformat(),
    }
    if prop_type is not None:
        record["propType"] = prop_type
    return record


def _build_direction_alert(
    *,
    sport: str,
    prop_type: Optional[str],
    direction: str,
    replay: dict,
) -> dict:
    """Build a conservative direction-specific alert from replay evidence."""
    stats = (replay.get("byDirection") or {}).get(direction) or {}
    n = int(stats.get("n") or 0)
    hit_rate = stats.get("hitRate")
    if n < DIRECTION_MIN_N or hit_rate is None:
        level = "OK"
    elif direction == "over" and hit_rate < OVER_DIRECTION_AVOID_RATE:
        level = "AVOID"
    elif direction == "over" and hit_rate < OVER_DIRECTION_RISKY_RATE:
        level = "RISKY"
    else:
        level = "OK"
    return {
        "alertLevel": level,
        "sport": sport,
        "propType": prop_type,
        "direction": direction,
        "n": n,
        "hits": stats.get("hits", 0),
        "misses": stats.get("misses", 0),
        "hitRate": hit_rate,
        "brierScore": stats.get("brierScore"),
        "updatedAt": datetime.now(timezone.utc).isoformat(),
    }


async def refresh_calibration_alerts(db) -> dict:
    """Recompute calibration alerts from all settled picks.

    Fetches settled picks, runs walk_forward_replay per sport (and per sport+prop
    for buckets with enough data), then updates the in-memory alert caches.

    Called every 6h from server.py startup tasks.
    """
    global _LAST_REFRESH

    query = {
        "status": "settled",
        "result": {"$in": ["hit", "miss"]},
        "settledAt": {"$ne": None},
        "voidReason": {"$exists": False},
    }
    projection = {
        "_id": 0,
        "sport": 1, "propType": 1, "recommendation": 1, "line": 1,
        "fixtureId": 1, "fixtureDate": 1, "matchDate": 1,
        "playerId": 1, "playerName": 1, "teamId": 1, "opponentId": 1,
        "timestamp": 1, "settledAt": 1,
        "result": 1, "confidenceScore": 1, "rawConfidence": 1,
        "actualValue": 1, "projectedValue": 1,
    }
    rows = await db.picks.find(query, projection).sort("settledAt", 1).to_list(50000)

    if not rows:
        print("[CAL ALERTS] No settled picks — skipping alert computation")
        return {"sports": 0, "propAlerts": 0, "nonOkAlerts": 0}

    # Split by sport
    by_sport: Dict[str, list] = defaultdict(list)
    for row in rows:
        sport = str(row.get("sport") or "unknown")
        by_sport[sport].append(row)

    new_sport_alerts: Dict[str, dict] = {}
    new_prop_alerts: Dict[Tuple[str, str], dict] = {}
    new_direction_alerts: Dict[Tuple[str, Optional[str], str], dict] = {}

    for sport, sport_rows in by_sport.items():
        # ── Sport-level alert ──────────────────────────────────────────────
        sport_replay = walk_forward_replay(sport_rows)
        sport_alert  = _build_alert(sport=sport, prop_type=None, replay=sport_replay, min_n=BRIER_MIN_N)
        new_sport_alerts[sport] = sport_alert
        for direction in ("over", "under"):
            direction_alert = _build_direction_alert(
                sport=sport, prop_type=None, direction=direction,
                replay=walk_forward_replay([
                    row for row in sport_rows
                    if str(row.get("recommendation") or "").lower() == direction
                ]),
            )
            if direction_alert["alertLevel"] != "OK":
                new_direction_alerts[(sport, None, direction)] = direction_alert

        # ── Per-prop alerts within this sport ──────────────────────────────
        by_prop: Dict[str, list] = defaultdict(list)
        for row in sport_rows:
            prop_type = str(row.get("propType") or "unknown")
            by_prop[prop_type].append(row)

        for prop_type, prop_rows in by_prop.items():
            # Only compute for props with enough data
            if len(prop_rows) < PROP_MIN_N:
                continue
            prop_replay = walk_forward_replay(prop_rows)
            prop_alert  = _build_alert(sport=sport, prop_type=prop_type, replay=prop_replay, min_n=PROP_MIN_N)
            # Only store non-OK alerts for props (keep the dict lean)
            if prop_alert["alertLevel"] != "OK":
                new_prop_alerts[(sport, prop_type)] = prop_alert
            for direction in ("over", "under"):
                direction_rows = [
                    row for row in prop_rows
                    if str(row.get("recommendation") or "").lower() == direction
                ]
                if len(direction_rows) < DIRECTION_MIN_N:
                    continue
                direction_alert = _build_direction_alert(
                    sport=sport, prop_type=prop_type, direction=direction,
                    replay=walk_forward_replay(direction_rows),
                )
                if direction_alert["alertLevel"] != "OK":
                    new_direction_alerts[(sport, prop_type, direction)] = direction_alert

    async with _ALERTS_LOCK:
        _SPORT_ALERTS.clear()
        _SPORT_ALERTS.update(new_sport_alerts)
        _PROP_ALERTS.clear()
        _PROP_ALERTS.update(new_prop_alerts)
        _DIRECTION_ALERTS.clear()
        _DIRECTION_ALERTS.update(new_direction_alerts)
        _LAST_REFRESH = datetime.now(timezone.utc)

    non_ok = sum(1 for v in new_sport_alerts.values() if v["alertLevel"] != "OK")
    non_ok += len(new_prop_alerts)
    non_ok += len(new_direction_alerts)

    sport_summary = {
        s: f"Brier={v['brierScore']} gap={v['maxOverGapPp']}pp n={v['n']} → {v['alertLevel']}"
        for s, v in sorted(new_sport_alerts.items())
    }
    print(f"[CAL ALERTS] {len(new_sport_alerts)} sports, {len(new_prop_alerts)} prop alerts, "
          f"{len(new_direction_alerts)} direction alerts, "
          f"{non_ok} non-OK: {sport_summary}")
    return {
        "sports":       len(new_sport_alerts),
        "propAlerts":   len(new_prop_alerts),
        "directionAlerts": len(new_direction_alerts),
        "nonOkAlerts":  non_ok,
    }


def get_calibration_alert(
    sport: str,
    prop_type: Optional[str] = None,
) -> Optional[dict]:
    """Return the most specific calibration alert for the given sport/prop.

    Tries sport+prop first (if prop_type provided), then falls back to sport-level.
    Returns None when no non-OK alert exists.

    The returned dict includes:
      alertLevel    — "AVOID" | "RISKY"
      brierScore    — walk-forward Brier score for the bucket
      n             — sample count
      maxOverGapPp  — worst over-confidence gap in pp (positive = over-confident)
      worstBin      — confidence bin label with the largest over-confidence gap
      source        — "prop" or "sport"
    """
    sport_key = (sport or "").lower()

    # Most specific: sport + prop
    if prop_type:
        prop_key = prop_type.lower()
        for (s, p), alert in _PROP_ALERTS.items():
            if s.lower() == sport_key and p.lower() == prop_key:
                return {**alert, "source": "prop"}

    # Fall back: sport-level
    for s, alert in _SPORT_ALERTS.items():
        if s.lower() == sport_key and alert["alertLevel"] != "OK":
            return {**alert, "source": "sport"}

    return None


def get_directional_calibration_alert(
    sport: str,
    prop_type: Optional[str],
    direction: str,
) -> Optional[dict]:
    """Return a non-OK replay alert for a specific market direction."""
    sport_key = (sport or "").lower()
    prop_key = prop_type.lower() if prop_type else None
    direction_key = (direction or "").lower()
    if direction_key not in {"over", "under"}:
        return None
    for key in (
        (sport_key, prop_key, direction_key),
        (sport_key, None, direction_key),
    ):
        alert = _DIRECTION_ALERTS.get(key)
        if alert and alert.get("alertLevel") in {"AVOID", "RISKY"}:
            return {**alert, "source": "direction"}
    return None


def get_all_alerts() -> dict:
    """Return a full snapshot of all calibration alerts (for admin endpoints)."""
    return {
        "lastRefresh": _LAST_REFRESH.isoformat() if _LAST_REFRESH else None,
        "thresholds": {
            "brierAlert":   BRIER_ALERT,
            "brierWarn":    BRIER_WARN,
            "gapPpAlert":   GAP_PP_ALERT,
            "gapPpWarn":    GAP_PP_WARN,
            "brierMinN":    BRIER_MIN_N,
            "propMinN":     PROP_MIN_N,
            "gapMinBinN":   GAP_MIN_BIN_N,
        },
        "sports": {s: v for s, v in sorted(_SPORT_ALERTS.items())},
        "props":  {f"{s}|{p}": v for (s, p), v in sorted(_PROP_ALERTS.items())},
        "directions": {
            f"{s}|{p or '*'}|{d}": v
            for (s, p, d), v in sorted(
                _DIRECTION_ALERTS.items(),
                key=lambda item: (item[0][0], item[0][1] or "", item[0][2]),
            )
        },
    }
