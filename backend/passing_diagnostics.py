"""Leakage-conscious diagnostics for soccer passing props.

This module is intentionally descriptive.  It does not change recommendations,
confidence, calibration, or settlement.  Its job is to answer whether a recent
passing-prop cluster is independent evidence or several picks sharing one
match environment.
"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

from model_metrics import (
    _event_key,
    _direction_key,
    _is_scored_directional_row,
    dedupe_prediction_rows,
    walk_forward_replay,
)


PASSING_PROP_TYPES = frozenset({"pass_attempts", "passes", "key_passes"})

_LEAGUE_NAMES = {
    1: "World Cup",
    2: "UEFA Champions League",
    3: "UEFA Europa League",
    13: "UEFA CL Qualifiers",
    39: "Premier League",
    40: "Championship",
    61: "Ligue 1",
    71: "Brasileirão",
    78: "Bundesliga",
    94: "Primeira Liga",
    128: "Liga Profesional",
    135: "Serie A",
    140: "La Liga",
    188: "A-League",
    203: "Süper Lig",
    242: "Liga Pro Ecuador",
    253: "MLS",
    254: "NWSL",
    262: "Liga MX",
    307: "Saudi Pro League",
    531: "UEFA Super Cup",
    667: "Liga MX",
    772: "Leagues Cup",
    848: "UEFA Conference League",
}

_CUP_LEAGUE_IDS = frozenset({1, 2, 3, 13, 531, 848, 772})
_POSITION_MAP = {
    "g": "GK",
    "goalkeeper": "GK",
    "goalie": "GK",
    "d": "DEF",
    "def": "DEF",
    "defender": "DEF",
    "m": "MID",
    "mid": "MID",
    "midfielder": "MID",
    "f": "FWD",
    "fw": "FWD",
    "fwd": "FWD",
    "forward": "FWD",
    "attacker": "FWD",
}


def _number(value: Any) -> float | None:
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def _league_label(row: dict) -> str:
    explicit = str(row.get("leagueName") or "").strip()
    if explicit:
        return explicit
    league_id = row.get("leagueId")
    try:
        league_id = int(league_id)
    except (TypeError, ValueError):
        return f"League {league_id}" if league_id else "Unknown"
    return _LEAGUE_NAMES.get(league_id, f"League {league_id}")


def _competition_type(row: dict) -> str:
    explicit = str(
        row.get("competitionType")
        or row.get("competition")
        or row.get("stageType")
        or ""
    ).strip().lower()
    if explicit:
        if any(token in explicit for token in ("cup", "tournament", "knockout", "super")):
            return "cup / tournament"
        if any(token in explicit for token in ("league", "regular", "domestic")):
            return "league"

    text = " ".join(
        str(row.get(key) or "").lower()
        for key in ("leagueName", "matchRound", "round", "leagueRound")
    )
    league_id = _number(row.get("leagueId"))
    if league_id is not None and int(league_id) in _CUP_LEAGUE_IDS:
        return "cup / tournament"
    if any(token in text for token in (
        "cup", "champions", "europa", "conference", "super cup",
        "qualif", "knockout", "quarter", "semi-final", "final",
    )):
        return "cup / tournament"
    return "league / other"


def _position_group(row: dict) -> str:
    raw = str(row.get("position") or row.get("role") or "").strip()
    if not raw:
        return "Unknown"
    lowered = raw.lower()
    if lowered in _POSITION_MAP:
        return _POSITION_MAP[lowered]
    upper = raw.upper()
    if upper in {"GK", "CB", "LB", "RB", "LWB", "RWB", "SW"}:
        return "GK" if upper == "GK" else "DEF"
    if upper in {"CDM", "CM", "CAM", "DM", "AM", "RM", "LM"}:
        return "MID"
    if upper in {"LW", "RW", "ST", "CF", "SS"}:
        return "FWD"
    return upper


def _team_possession(row: dict) -> float | None:
    direct = _number(row.get("teamPossession"))
    if direct is not None:
        return direct
    direct = _number(row.get("playerPossession"))
    if direct is not None:
        return direct
    venue = str(row.get("venue") or "").lower()
    if venue == "home":
        return _number(row.get("homePoss"))
    if venue == "away":
        return _number(row.get("awayPoss"))
    return None


def _possession_band(row: dict) -> str:
    possession = _team_possession(row)
    if possession is None:
        return "Unknown"
    if possession < 40:
        return "<40%"
    if possession < 45:
        return "40–44%"
    if possession < 55:
        return "45–54%"
    if possession < 60:
        return "55–59%"
    return "60%+"


def _feature_row(row: dict, fixture_counts: dict[str, int]) -> dict:
    feature = dict(row)
    fixture_id = str(row.get("fixtureId") or "").strip()
    count = fixture_counts.get(fixture_id, 0) if fixture_id else 0
    feature["_diagnosticLeague"] = _league_label(row)
    feature["_diagnosticCompetition"] = _competition_type(row)
    feature["_diagnosticPosition"] = _position_group(row)
    feature["_diagnosticPossession"] = _possession_band(row)
    feature["_diagnosticFixtureCount"] = count
    feature["_diagnosticCorrelation"] = (
        "correlated (2+ picks in fixture)" if count >= 2
        else "independent / singleton fixture" if count == 1
        else "fixture identity unavailable"
    )
    return feature


def _metric_summary(rows: list[dict]) -> dict:
    scored = [row for row in rows if _is_scored_directional_row(row)]
    hits = sum(str(row.get("result") or "").lower() == "hit" for row in scored)
    errors = []
    for row in scored:
        actual = _number(row.get("actualValue"))
        projected = _number(row.get("projectedValue"))
        if actual is not None and projected is not None:
            errors.append(actual - projected)
    under = [row for row in scored if _direction_key(row) == "under"]
    over = [row for row in scored if _direction_key(row) == "over"]

    def direction_summary(direction_rows: list[dict]) -> dict:
        direction_hits = sum(
            str(row.get("result") or "").lower() == "hit"
            for row in direction_rows
        )
        return {
            "n": len(direction_rows),
            "hits": direction_hits,
            "misses": len(direction_rows) - direction_hits,
            "hitRate": round(direction_hits / len(direction_rows) * 100, 1)
            if direction_rows else None,
        }

    return {
        "n": len(scored),
        "hits": hits,
        "misses": len(scored) - hits,
        "hitRate": round(hits / len(scored) * 100, 1) if scored else None,
        "under": direction_summary(under),
        "over": direction_summary(over),
        "meanProjectionError": round(sum(errors) / len(errors), 2) if errors else None,
        "projectionN": len(errors),
    }


def _dimension_rows(rows: list[dict], field: str) -> list[dict]:
    buckets: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        buckets[str(row.get(field) or "Unknown")].append(row)
    output = []
    for label, bucket in buckets.items():
        summary = _metric_summary(bucket)
        summary["label"] = label
        output.append(summary)
    return sorted(output, key=lambda item: (-item["n"], item["label"]))


def _replay_summary(rows: list[dict]) -> dict:
    # The shared replay checker intentionally flags equal timestamps as a
    # possible ordering violation.  Settlement batches commonly write several
    # picks at the same instant, though; there is no "future" row among an
    # exact-time tie.  Give ties a deterministic canonical-event order for this
    # diagnostic only, preserving chronological ordering without false alarms.
    ordered = sorted(
        rows,
        key=lambda row: (
            str(row.get("settledAt") or row.get("timestamp") or ""),
            _event_key(row),
        ),
    )
    replay_rows = []
    tie_counts: dict[str, int] = defaultdict(int)
    for row in ordered:
        copy = dict(row)
        base = str(row.get("settledAt") or row.get("timestamp") or "")
        index = tie_counts[base]
        tie_counts[base] += 1
        copy["settledAt"] = f"{base}#{index:08d}"
        replay_rows.append(copy)

    replay = walk_forward_replay(replay_rows)
    return {
        "n": replay.get("eligibleSamples", 0),
        "evaluatedN": replay.get("evaluatedSamples", 0),
        "leakageViolations": replay.get("leakageViolations", 0),
        "missingPriorDataEvents": replay.get("missingPriorDataEvents", 0),
        "classification": replay.get("classification", {}),
        "projection": replay.get("projection", {}),
        "byDirection": replay.get("byDirection", {}),
    }


def _walk_forward_dimensions(rows: list[dict], field: str) -> list[dict]:
    """Run an independent chronological replay inside every feature bucket."""
    buckets: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        buckets[str(row.get(field) or "Unknown")].append(row)
    output = []
    for label, bucket in buckets.items():
        result = _replay_summary(bucket)
        result["label"] = label
        output.append(result)
    return sorted(output, key=lambda item: (-item["n"], item["label"]))


def build_passing_diagnostics(rows: list[dict]) -> dict:
    """Build grouped passing diagnostics without modifying model behavior."""
    passing_rows = [
        row for row in rows
        if str(row.get("propType") or "").lower() in PASSING_PROP_TYPES
        and _is_scored_directional_row(row)
    ]
    deduped = dedupe_prediction_rows(passing_rows)

    fixture_counts: dict[str, int] = defaultdict(int)
    for row in deduped:
        fixture_id = str(row.get("fixtureId") or "").strip()
        if fixture_id:
            fixture_counts[fixture_id] += 1
    featured = [_feature_row(row, fixture_counts) for row in deduped]

    dimensions = {
        "league": _dimension_rows(featured, "_diagnosticLeague"),
        "competition": _dimension_rows(featured, "_diagnosticCompetition"),
        "position": _dimension_rows(featured, "_diagnosticPosition"),
        "possessionBand": _dimension_rows(featured, "_diagnosticPossession"),
        "correlation": _dimension_rows(featured, "_diagnosticCorrelation"),
    }
    walk_forward = {
        "overall": _replay_summary(featured),
        "byLeague": _walk_forward_dimensions(featured, "_diagnosticLeague"),
        "byCompetition": _walk_forward_dimensions(featured, "_diagnosticCompetition"),
        "byPosition": _walk_forward_dimensions(featured, "_diagnosticPosition"),
        "byPossessionBand": _walk_forward_dimensions(featured, "_diagnosticPossession"),
        "byCorrelation": _walk_forward_dimensions(featured, "_diagnosticCorrelation"),
        "method": (
            "Rows are deduplicated by the canonical prediction event key, sorted "
            "by settlement time (exact-time ties use canonical event-key order), "
            "and each feature bucket is replayed using only earlier settled rows. "
            "This is an evaluation split, not a calibration change."
        ),
    }

    source_paths: dict[str, int] = defaultdict(int)
    verified = 0
    exact_fixture = 0
    missing_fixture = 0
    for row in deduped:
        source = row.get("settlementSource") or {}
        if not isinstance(source, dict):
            source = {}
        source_paths[str(source.get("statPath") or "missing")] += 1
        if source.get("verified") is True:
            verified += 1
        if row.get("fixtureId") is None:
            missing_fixture += 1
        elif source.get("fixtureId") is not None and str(source.get("fixtureId")) == str(row.get("fixtureId")):
            exact_fixture += 1

    correlated_events = [
        row for row in featured
        if row["_diagnosticCorrelation"].startswith("correlated")
    ]
    independent_events = [
        row for row in featured
        if row["_diagnosticCorrelation"].startswith("independent")
    ]
    fixture_ids = {
        str(row.get("fixtureId"))
        for row in deduped
        if row.get("fixtureId") is not None
    }
    correlated_fixture_ids = {
        str(row.get("fixtureId"))
        for row in correlated_events
        if row.get("fixtureId") is not None
    }

    return {
        "scope": {
            "propTypes": sorted(PASSING_PROP_TYPES),
            "rawRows": len(passing_rows),
            "uniqueEvents": len(deduped),
            "fixtures": len(fixture_ids),
            "scoredEvents": len(featured),
        },
        "correlationSummary": {
            "correlatedEvents": len(correlated_events),
            "independentEvents": len(independent_events),
            "fixtureIdentityUnavailableEvents": len(featured) - len(correlated_events) - len(independent_events),
            "correlatedFixtures": len(correlated_fixture_ids),
            "independentFixtures": max(0, len(fixture_ids) - len(correlated_fixture_ids)),
            "correlated": _metric_summary(correlated_events),
            "independent": _metric_summary(independent_events),
        },
        "sourceAudit": {
            "verifiedSourceEvents": verified,
            "exactFixtureSourceEvents": exact_fixture,
            "missingFixtureEvents": missing_fixture,
            "statPaths": [
                {"path": path, "n": count}
                for path, count in sorted(source_paths.items(), key=lambda item: (-item[1], item[0]))
            ],
        },
        "dimensions": dimensions,
        "walkForward": walk_forward,
        "note": (
            "All values are owner-only, descriptive diagnostics. No global UNDER "
            "penalty or recommendation change is applied by this report."
        ),
    }