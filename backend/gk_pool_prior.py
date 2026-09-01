"""Goalkeeper passing-pool evidence.

The keeper pool is useful context for pass-attempt props because opponent
pressure can change goalkeeper volume without improving completion quality.
This module deliberately keeps the pool separate from the player's own logs
and returns a shadow-only result unless an explicit validation mode promotes
it.
"""

from __future__ import annotations

from math import isfinite
from typing import Any


PASS_PROPS = frozenset({"pass_attempts", "passes"})
MIN_POOL_ROWS = 5
MAX_POOL_WEIGHT = 0.25
_SHRINK_K = 8.0


def _number(value: Any) -> float | None:
    try:
        if value is None or str(value).strip() == "":
            return None
        number = float(value)
        return number if isfinite(number) else None
    except (TypeError, ValueError):
        return None


def empty_gk_pool_prior(reason: str = "No qualifying goalkeeper pool rows.") -> dict[str, Any]:
    return {
        "version": "gk-pool-prior-v1",
        "status": "insufficient_evidence",
        "mode": "shadow",
        "requestedMode": "shadow",
        "applied": False,
        "projectionAdjustmentStatus": "shadow_only",
        "poolMean": None,
        "poolPer90Mean": None,
        "poolRows": 0,
        "poolPlayers": 0,
        "effectiveSampleSize": 0.0,
        "shrinkFactor": 0.0,
        "playerPriorMean": None,
        "blendedPriorMean": None,
        "poolWeight": 0.0,
        "pressureProxy": "opponent-specific goalkeeper pass-attempt pool",
        "reason": reason,
    }


def build_gk_pool_prior(
    rows: list[dict[str, Any]] | None,
    *,
    player_prior_mean: Any = None,
    mode: str = "shadow",
) -> dict[str, Any]:
    """Build a conservative keeper-pool prior from already verified rows.

    Rows must already be scoped by the caller to the same fixture context
    (opponent, venue, and competition/role). This function additionally
    requires an explicit goalkeeper marker so outfield pass rows cannot leak
    into the pool.
    """
    requested_mode = str(mode or "shadow").strip().lower()
    if requested_mode not in {"off", "shadow", "live"}:
        requested_mode = "shadow"

    qualifying: list[tuple[str, float, float]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        position = str(
            row.get("position")
            or row.get("matchPosition")
            or row.get("observedPosition")
            or ""
        ).upper().replace(" ", "")
        if position not in {"GK", "G", "GOALKEEPER"}:
            continue
        value = _number(row.get("statValue"))
        if value is None:
            value = _number(row.get("passAttempts"))
        if value is None or value < 0:
            continue
        minutes = _number(row.get("minutes"))
        if minutes is None or minutes < 30:
            continue
        player_key = str(row.get("playerId") or row.get("name") or "").strip().lower()
        if not player_key:
            continue
        per90 = value * 90.0 / max(minutes, 30.0)
        reliability = max(0.25, min(1.0, minutes / 90.0))
        qualifying.append((player_key, per90, reliability))

    if len(qualifying) < MIN_POOL_ROWS:
        result = empty_gk_pool_prior(
            f"Need {MIN_POOL_ROWS} qualifying goalkeeper rows; found {len(qualifying)}."
        )
        result["mode"] = requested_mode
        result["requestedMode"] = requested_mode
        result["livePromotionRequested"] = requested_mode == "live"
        result["poolRows"] = len(qualifying)
        result["poolPlayers"] = len({key for key, _, _ in qualifying})
        return result

    # A player can appear more than once against the opponent. Keep every
    # verified appearance, but cap each appearance's contribution so one
    # keeper with repeated fixtures cannot dominate the pool.
    weights = [weight for _, _, weight in qualifying]
    values = [value for _, value, _ in qualifying]
    weight_total = sum(weights)
    pool_mean_per90 = sum(value * weight for value, weight in zip(values, weights)) / weight_total
    pool_mean = pool_mean_per90 * 90.0 / 90.0
    effective_n = (weight_total * weight_total) / sum(weight * weight for weight in weights)

    player_mean = _number(player_prior_mean)
    if player_mean is None or player_mean <= 0:
        player_mean = pool_mean
    shrink = effective_n / (effective_n + _SHRINK_K)
    blended = player_mean * (1.0 - shrink) + pool_mean * shrink

    # This is a diagnostic weight, not a final projection weight. It is capped
    # so a future live promotion cannot let a matchup pool override the
    # player's verified history.
    pool_weight = min(MAX_POOL_WEIGHT, shrink * MAX_POOL_WEIGHT)
    return {
        "version": "gk-pool-prior-v1",
        "status": "classified",
        "mode": requested_mode,
        "requestedMode": requested_mode,
        "livePromotionRequested": requested_mode == "live",
        "applied": False,
        "projectionAdjustmentStatus": "shadow_only",
        "poolMean": round(pool_mean, 2),
        "poolPer90Mean": round(pool_mean_per90, 2),
        "poolRows": len(qualifying),
        "poolPlayers": len({key for key, _, _ in qualifying}),
        "effectiveSampleSize": round(effective_n, 2),
        "shrinkFactor": round(shrink, 4),
        "playerPriorMean": round(player_mean, 2),
        "blendedPriorMean": round(blended, 2),
        "poolWeight": round(pool_weight, 4),
        "pressureProxy": "opponent-specific goalkeeper pass-attempt pool",
        "reason": (
            f"Goalkeeper pool averaged {pool_mean:.1f} pass attempts/90 across "
            f"{len(qualifying)} verified rows and {len({key for key, _, _ in qualifying})} keepers."
        ),
    }
