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


def _recent_rows(
    rows: list[dict[str, Any]],
    venue: str | None = None,
    *,
    limit: int = 20,
) -> list[dict[str, Any]]:
    output = []
    for row in sorted(
        [
            row for row in rows
            if isinstance(row, dict) and (venue is None or row.get("venue") == venue)
        ],
        key=lambda item: str(item.get("date") or ""),
        reverse=True,
    )[:limit]:
        output.append({
            "fixtureId": row.get("fixtureId"),
            "date": row.get("date"),
            "opponent": row.get("opponent"),
            "venue": row.get("venue") or venue,
            "teamShotsOnTarget": _number(row.get("teamShotsOnTarget")),
            "opponentShotsOnTarget": _number(row.get("opponentShotsOnTarget")),
            "teamPassAttempts": _number(row.get("teamPasses")),
            "opponentPassAttempts": _number(row.get("opponentPasses")),
        })
    return output


def _weighted_ratio_rows(
    rows: list[tuple[float, float, dict[str, Any]]],
    *,
    venue: str,
) -> dict[str, Any]:
    usable = [
        item for item in rows
        if item[2].get("venue") == venue and item[1] > 0
    ]
    usable.sort(key=lambda item: str(item[2].get("date") or ""), reverse=True)
    weights = [1.0 / (1.0 + (index * 0.08)) for index in range(len(usable))]
    denominator = sum(denominator * weight for (_, denominator, _), weight in zip(usable, weights))
    numerator = sum(numerator * weight for (numerator, _, _), weight in zip(usable, weights))
    return {
        "average": round(numerator / denominator * 100, 2) if denominator else None,
        "sampleSize": len(usable),
        "minimumRecommendedSample": MINIMUM_SAMPLE,
        "sampleStatus": (
            "sufficient" if len(usable) >= MINIMUM_SAMPLE
            else "limited" if usable
            else "unavailable"
        ),
        "totalNumerator": round(sum(item[0] for item in usable), 2),
        "totalDenominator": round(sum(item[1] for item in usable), 2),
        "venue": venue,
        "weightMethod": "venue_exact_recency_weighted_ratio",
    }


def _pass_involvement(
    player_logs: list[dict[str, Any]],
    team_rows: list[dict[str, Any]],
    *,
    venue: str,
    expected_team: dict[str, Any],
) -> dict[str, Any]:
    by_fixture = {
        str(row.get("fixtureId")): row
        for row in team_rows
        if isinstance(row, dict) and row.get("fixtureId") is not None
    }
    by_date = {
        str(row.get("date"))[:10]: row
        for row in team_rows
        if isinstance(row, dict) and row.get("date")
    }
    shares: list[tuple[float, float, dict[str, Any]]] = []
    for log in player_logs or []:
        if not isinstance(log, dict) or log.get("venue") != venue:
            continue
        row = by_fixture.get(str(log.get("_fid")))
        if row is None and log.get("date"):
            row = by_date.get(str(log.get("date"))[:10])
        player_passes = _number(log.get("passes_total"))
        team_passes = _number((row or {}).get("teamPasses"))
        if player_passes is None or team_passes is None or team_passes <= 0:
            continue
        shares.append((player_passes, team_passes, log))
    summary = _weighted_ratio_rows(
        [(player / team * 100, 1.0, log) for player, team, log in shares],
        venue=venue,
    )
    # The ratio helper's numerator/denominator fields are not meaningful for
    # percentage shares, so expose the directly weighted player-share average.
    if shares:
        weights = [1.0 / (1.0 + (index * 0.08)) for index in range(len(shares))]
        weight_total = sum(weights)
        share_average = sum((player / team * 100) * weight for (player, team, _), weight in zip(shares, weights)) / weight_total
        summary["average"] = round(share_average, 2)
        summary["totalNumerator"] = None
        summary["totalDenominator"] = None
    summary["expectedPlayerPasses"] = (
        round((_number(expected_team.get("average")) or 0) * (summary["average"] or 0) / 100, 2)
        if summary.get("average") is not None and expected_team.get("average") is not None
        else None
    )
    return summary


def _venue_metrics(
    rows: list[dict[str, Any]],
    *,
    venue: str,
) -> dict[str, Any]:
    team_sot_for = _summary(rows, "teamShotsOnTarget", venue=venue)
    team_sot_allowed = _summary(rows, "opponentShotsOnTarget", venue=venue)
    team_passes_for = _summary(rows, "teamPasses", venue=venue)
    team_passes_allowed = _summary(rows, "opponentPasses", venue=venue)
    return {
        "sotCreated": team_sot_for,
        "sotAllowed": team_sot_allowed,
        "passesCreated": team_passes_for,
        "passesAllowed": team_passes_allowed,
    }


def build_matchup_volume_packet(
    *,
    player_venue: str,
    team_rows: list[dict[str, Any]] | None,
    opponent_rows: list[dict[str, Any]] | None,
    team_name: str | None = None,
    opponent_name: str | None = None,
    player_logs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build comparable SOT/pass volume evidence for one verified matchup."""
    venue = "away" if str(player_venue).lower() == "away" else "home"
    opponent_venue = "home" if venue == "away" else "away"
    team_rows = team_rows or []
    opponent_rows = opponent_rows or []
    player_logs = player_logs or []

    team_by_venue = {
        "home": _venue_metrics(team_rows, venue="home"),
        "away": _venue_metrics(team_rows, venue="away"),
    }
    opponent_by_venue = {
        "home": _venue_metrics(opponent_rows, venue="home"),
        "away": _venue_metrics(opponent_rows, venue="away"),
    }
    team_selected = team_by_venue[venue]
    opponent_selected = opponent_by_venue[opponent_venue]
    team_sot_for = team_selected["sotCreated"]
    team_sot_allowed = team_selected["sotAllowed"]
    opponent_sot_for = opponent_selected["sotCreated"]
    opponent_sot_allowed = opponent_selected["sotAllowed"]
    team_passes_for = team_selected["passesCreated"]
    team_passes_allowed = team_selected["passesAllowed"]
    opponent_passes_for = opponent_selected["passesCreated"]
    opponent_passes_allowed = opponent_selected["passesAllowed"]

    expected_team_sot = _blend(team_sot_for, opponent_sot_allowed)
    expected_opponent_sot = _blend(opponent_sot_for, team_sot_allowed)
    expected_opponent_passes = _blend(opponent_passes_for, team_passes_allowed)
    expected_team_passes = _blend(team_passes_for, opponent_passes_allowed)
    player_pass_involvement = {
        side: _pass_involvement(
            player_logs,
            team_rows,
            venue=side,
            expected_team=team_by_venue[side]["passesCreated"],
        )
        for side in ("home", "away")
    }
    save_rate_rows = []
    for log in player_logs:
        if not isinstance(log, dict):
            continue
        saves = _number(log.get("goals_saves"))
        faced = _number(log.get("opponentShotsOnTarget"))
        if saves is not None and faced is not None and faced > 0:
            save_rate_rows.append((saves, faced, log))
    save_rate_by_venue = {
        side: _weighted_ratio_rows(save_rate_rows, venue=side)
        for side in ("home", "away")
    }
    fixture_home = team_by_venue["home"] if venue == "home" else opponent_by_venue["home"]
    fixture_away = team_by_venue["away"] if venue == "away" else opponent_by_venue["away"]

    return {
        "version": "matchup-volume-v1",
        "available": bool(
            any(
                metric.get("sampleSize")
                for side in (team_by_venue, opponent_by_venue)
                for metrics in side.values()
                for metric in metrics.values()
                if isinstance(metric, dict)
            )
        ),
        "status": "shadow_only",
        "projectionAdjustmentStatus": "shadow_only",
        "projectionAdjustment": 0,
        "venue": venue,
        "opponentVenue": opponent_venue,
        "team": team_name,
        "opponent": opponent_name,
        "homeTeam": team_name if venue == "home" else opponent_name,
        "awayTeam": opponent_name if venue == "home" else team_name,
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
            "byVenue": {
                "home": {
                    "team": team_by_venue["home"]["sotCreated"],
                    "teamAllowed": team_by_venue["home"]["sotAllowed"],
                    "opponent": opponent_by_venue["home"]["sotCreated"],
                    "opponentAllowed": opponent_by_venue["home"]["sotAllowed"],
                },
                "away": {
                    "team": team_by_venue["away"]["sotCreated"],
                    "teamAllowed": team_by_venue["away"]["sotAllowed"],
                    "opponent": opponent_by_venue["away"]["sotCreated"],
                    "opponentAllowed": opponent_by_venue["away"]["sotAllowed"],
                },
            },
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
            "byVenue": {
                "home": {
                    "team": team_by_venue["home"]["passesCreated"],
                    "teamAllowed": team_by_venue["home"]["passesAllowed"],
                    "opponent": opponent_by_venue["home"]["passesCreated"],
                    "opponentAllowed": opponent_by_venue["home"]["passesAllowed"],
                },
                "away": {
                    "team": team_by_venue["away"]["passesCreated"],
                    "teamAllowed": team_by_venue["away"]["passesAllowed"],
                    "opponent": opponent_by_venue["away"]["passesCreated"],
                    "opponentAllowed": opponent_by_venue["away"]["passesAllowed"],
                },
            },
        },
        "playerPassInvolvement": {
            "selectedVenue": player_pass_involvement[venue],
            "byVenue": player_pass_involvement,
        },
        "goalkeeperSaveRate": {
            "selectedVenue": save_rate_by_venue[venue],
            "byVenue": save_rate_by_venue,
        },
        "fixtureSplits": {
            "home": {
                "team": team_name if venue == "home" else opponent_name,
                "sotCreated": fixture_home["sotCreated"],
                "sotAllowed": fixture_home["sotAllowed"],
                "passesCreated": fixture_home["passesCreated"],
                "passesAllowed": fixture_home["passesAllowed"],
            },
            "away": {
                "team": opponent_name if venue == "home" else team_name,
                "sotCreated": fixture_away["sotCreated"],
                "sotAllowed": fixture_away["sotAllowed"],
                "passesCreated": fixture_away["passesCreated"],
                "passesAllowed": fixture_away["passesAllowed"],
            },
        },
        "recentMatchRows": _recent_rows(team_rows, limit=20),
        "opponentRecentMatchRows": _recent_rows(opponent_rows, opponent_venue),
    }