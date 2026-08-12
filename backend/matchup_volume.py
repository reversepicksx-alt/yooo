"""Venue-aware team-volume evidence for soccer player props.

This module only summarizes verified fixture-level team statistics.  It is
intentionally projection-neutral: the packet explains whether the matchup
supports or conflicts with a player prop, but promotion into Bayesian math
requires settled-pick validation.
"""

from __future__ import annotations

import math
from typing import Any


MINIMUM_SAMPLE = 10


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(str(value).replace("%", "").strip())
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _summary(
    rows: list[dict[str, Any]],
    field: str,
    *,
    venue: str,
    limit: int = MINIMUM_SAMPLE,
) -> dict[str, Any]:
    usable = []
    for row in rows:
        if not isinstance(row, dict) or row.get("venue") != venue:
            continue
        value = _number(row.get(field))
        if value is None:
            continue
        usable.append((row, value))

    usable.sort(key=lambda item: str(item[0].get("date") or ""), reverse=True)
    usable = usable[:limit]
    values = [value for _, value in usable]
    # A modest recency tilt is useful for current team identity without
    # allowing one recent match to overwhelm a thin venue sample.
    weights = [1.0 / (1.0 + (index * 0.08)) for index in range(len(values))]
    weight_total = sum(weights)
    weighted_average = (
        sum(value * weight for value, weight in zip(values, weights)) / weight_total
        if values and weight_total
        else None
    )
    effective_sample = (
        (weight_total * weight_total) / sum(weight * weight for weight in weights)
        if weights
        else 0.0
    )
    return {
        "average": round(weighted_average, 2) if weighted_average is not None else None,
        "unweightedAverage": round(sum(values) / len(values), 2) if values else None,
        "sampleSize": len(values),
        "effectiveSampleSize": round(effective_sample, 2),
        "minimumRecommendedSample": MINIMUM_SAMPLE,
        "sampleStatus": (
            "sufficient" if len(values) >= MINIMUM_SAMPLE
            else "limited" if values
            else "unavailable"
        ),
        "venue": venue,
        "weightMethod": "venue_exact_recency_weighted",
        "fixtureIds": [
            row.get("fixtureId") for row, _ in usable if row.get("fixtureId") is not None
        ],
    }


def _blend(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    values = []
    for item in (left, right):
        value = _number(item.get("average"))
        if value is not None:
            # Effective sample size gives a thin source less influence without
            # discarding it completely.
            values.append((value, max(1.0, _number(item.get("effectiveSampleSize")) or 1.0)))
    if not values:
        return {
            "average": None,
            "sampleSize": 0,
            "sampleStatus": "unavailable",
            "weightMethod": "effective_sample_size_blend",
        }
    total_weight = sum(weight for _, weight in values)
    return {
        "average": round(sum(value * weight for value, weight in values) / total_weight, 2),
        "sampleSize": sum(int(item.get("sampleSize") or 0) for item in (left, right)),
        "effectiveSampleSize": round(sum(weight for _, weight in values), 2),
        "sampleStatus": "sufficient" if all(
            item.get("sampleStatus") == "sufficient" for item in (left, right)
            if item.get("average") is not None
        ) else "limited",
        "weightMethod": "effective_sample_size_blend",
    }


def _comparison(
    label: str,
    left: dict[str, Any],
    right: dict[str, Any],
) -> dict[str, Any]:
    left_value = _number(left.get("average"))
    right_value = _number(right.get("average"))
    delta = round(left_value - right_value, 2) if left_value is not None and right_value is not None else None
    return {
        "label": label,
        "left": left,
        "right": right,
        "delta": delta,
        "status": "aligned" if delta is not None and abs(delta) < max(1.0, abs(right_value or 0) * 0.08)
        else "different" if delta is not None
        else "unavailable",
    }


def _recent_rows(rows: list[dict[str, Any]], venue: str) -> list[dict[str, Any]]:
    output = []
    for row in sorted(
        [row for row in rows if isinstance(row, dict) and row.get("venue") == venue],
        key=lambda item: str(item.get("date") or ""),
        reverse=True,
    )[:MINIMUM_SAMPLE]:
        output.append({
            "fixtureId": row.get("fixtureId"),
            "date": row.get("date"),
            "opponent": row.get("opponent"),
            "venue": venue,
            "teamShotsOnTarget": _number(row.get("teamShotsOnTarget")),
            "opponentShotsOnTarget": _number(row.get("opponentShotsOnTarget")),
            "teamPassAttempts": _number(row.get("teamPasses")),
            "opponentPassAttempts": _number(row.get("opponentPasses")),
        })
    return output


def build_matchup_volume_packet(
    *,
    player_venue: str,
    team_rows: list[dict[str, Any]] | None,
    opponent_rows: list[dict[str, Any]] | None,
    team_name: str | None = None,
    opponent_name: str | None = None,
) -> dict[str, Any]:
    """Build comparable SOT/pass volume evidence for one verified matchup."""
    venue = "away" if str(player_venue).lower() == "away" else "home"
    opponent_venue = "home" if venue == "away" else "away"
    team_rows = team_rows or []
    opponent_rows = opponent_rows or []

    team_sot_for = _summary(team_rows, "teamShotsOnTarget", venue=venue)
    team_sot_allowed = _summary(team_rows, "opponentShotsOnTarget", venue=venue)
    opponent_sot_for = _summary(opponent_rows, "teamShotsOnTarget", venue=opponent_venue)
    opponent_sot_allowed = _summary(opponent_rows, "opponentShotsOnTarget", venue=opponent_venue)

    team_passes_for = _summary(team_rows, "teamPasses", venue=venue)
    team_passes_allowed = _summary(team_rows, "opponentPasses", venue=venue)
    opponent_passes_for = _summary(opponent_rows, "teamPasses", venue=opponent_venue)
    opponent_passes_allowed = _summary(opponent_rows, "opponentPasses", venue=opponent_venue)

    expected_team_sot = _blend(team_sot_for, opponent_sot_allowed)
    expected_opponent_sot = _blend(opponent_sot_for, team_sot_allowed)
    expected_opponent_passes = _blend(opponent_passes_for, team_passes_allowed)
    expected_team_passes = _blend(team_passes_for, opponent_passes_allowed)

    return {
        "version": "matchup-volume-v1",
        "available": bool(
            team_sot_for["sampleSize"]
            or team_passes_for["sampleSize"]
            or opponent_sot_for["sampleSize"]
            or opponent_passes_for["sampleSize"]
        ),
        "status": "shadow_only",
        "projectionAdjustmentStatus": "shadow_only",
        "projectionAdjustment": 0,
        "venue": venue,
        "opponentVenue": opponent_venue,
        "team": team_name,
        "opponent": opponent_name,
        "minimumRecommendedSample": MINIMUM_SAMPLE,
        "weightMethod": "venue_exact_recency_weighted_plus_effective_sample_blend",
        "shotsOnTarget": {
            "teamCreated": team_sot_for,
            "teamAllowed": team_sot_allowed,
            "opponentCreated": opponent_sot_for,
            "opponentAllowed": opponent_sot_allowed,
            "expectedTeam": expected_team_sot,
            "expectedOpponent": expected_opponent_sot,
            "comparison": _comparison("team-created vs opponent-allowed", team_sot_for, opponent_sot_allowed),
            "opponentPressure": _comparison("opponent-created vs team-allowed", opponent_sot_for, team_sot_allowed),
        },
        "passes": {
            "teamCreated": team_passes_for,
            "teamAllowed": team_passes_allowed,
            "opponentCreated": opponent_passes_for,
            "opponentAllowed": opponent_passes_allowed,
            "expectedTeam": expected_team_passes,
            "expectedOpponent": expected_opponent_passes,
            "comparison": _comparison("team-pass-volume vs opponent-allowed", team_passes_for, opponent_passes_allowed),
            "opponentPressure": _comparison("opponent-pass-volume vs team-allowed", opponent_passes_for, team_passes_allowed),
        },
        "recentMatchRows": _recent_rows(team_rows, venue),
        "opponentRecentMatchRows": _recent_rows(opponent_rows, opponent_venue),
    }