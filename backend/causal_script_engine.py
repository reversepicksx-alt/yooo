"""Deterministic, pre-match causal football audit.

This layer does not ask an LLM to invent a style label.  It translates the
provider observations already attached to a prediction into a bounded
mechanism packet and a conservative gate.  Numeric RP math remains the source
of truth until a separately validated promotion flag is enabled.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any


CAUSAL_SCHEMA_VERSION = "causal-script.v1"
CAUSAL_MODEL_VERSION = "deterministic-observed-mechanism.v1"
CAUSAL_COHORT_SNAPSHOT_VERSION = "causal-cohort.v2"

PROP_CHAINS = {
    "pass_attempts": "team possession → pressure/progression geometry → first-line recycling → exact-role pass attempts",
    "passes": "team possession → pressure/progression geometry → first-line recycling → exact-role pass attempts",
    "clearances": "opponent territory → crosses/direct entries → target defensive zone → forced clearances",
    "saves": "opponent attacks → shots → shots on target → shot quality/conversion → goalkeeper saves",
    "shots": "role/zone → progression into zone → receptions/touches → shot attempts",
    "shots_on_target": "shot volume → shot location/orientation → accuracy → shots on target",
    "crosses": "team width → wide progression → crossing-zone receptions → deliveries",
    "dribbles": "isolation/transition space → facing-goal receptions → take-on attempts",
    "tackles": "opponent progression through zone → role assignment → duel opportunities → tackles",
    "interceptions": "passing lanes → opponent progression → screening zone → interceptions",
    "fouls_committed": "defensive matchup → contact/recovery situations → foul opportunities",
    "yellow_cards": "duel/foul exposure → transition danger → tactical-foul opportunities → cards",
    "key_passes": "possession/progression → creation-zone receptions → final-ball attempts → key passes",
    "assists": "progression → creation-zone final ball → teammate finish → assists",
    "goals": "box entries → shot quality → finishing event → goals",
    "fouls": "defensive matchup → contact/recovery situations → foul opportunities",
    "fouls_committed": "defensive matchup → contact/recovery situations → foul opportunities",
    "cards": "duel/foul exposure → transition danger → tactical-foul opportunities → cards",
    "yellow_cards": "duel/foul exposure → transition danger → tactical-foul opportunities → cards",
    "fantasy_score": "minutes/role → tracked actions → event weighting → fantasy score",
}

_VALUE_KEYS = {
    "pass_attempts": ("value", "statValue", "targetStat", "passes_total", "passes"),
    "passes": ("value", "statValue", "targetStat", "passes_total", "passes"),
    "shots": ("value", "statValue", "targetStat", "shots_total", "shots"),
    "shots_on_target": ("value", "statValue", "targetStat", "shots_on", "shots_on_target"),
    "saves": ("value", "statValue", "targetStat", "goals_saves", "saves"),
    "goalie_saves": ("value", "statValue", "targetStat", "goals_saves", "saves"),
    "clearances": ("value", "statValue", "targetStat", "clearances"),
    "crosses": ("value", "statValue", "targetStat", "crosses"),
    "tackles": ("value", "statValue", "targetStat", "tackles_total", "tackles"),
    "interceptions": ("value", "statValue", "targetStat", "interceptions"),
    "dribbles": ("value", "statValue", "targetStat", "dribbles_attempts", "dribbles"),
    "key_passes": ("value", "statValue", "targetStat", "passes_key", "key_passes"),
}


def _num(value: Any) -> float | None:
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def _value(row: dict, prop: str) -> float | None:
    for key in _VALUE_KEYS.get(prop, ("value", "targetStat", prop)):
        value = _num(row.get(key))
        if value is not None:
            return value
    return None


def _venue(row: dict) -> str | None:
    venue = str(row.get("venue") or "").strip().lower()
    if venue in {"home", "away"}:
        return venue
    if row.get("isHome") is True:
        return "home"
    if row.get("isHome") is False:
        return "away"
    return None


def _score_margin(row: dict) -> int | None:
    score = str(row.get("score") or row.get("matchScore") or "")
    match = re.search(r"(\d+)\s*[-–]\s*(\d+)", score)
    if not match:
        return None
    return abs(int(match.group(1)) - int(match.group(2)))


def distortion_tags(row: dict) -> list[str]:
    """Tag known workload regime changes without guessing missing facts."""
    tags: list[str] = []
    events = " ".join(str(row.get(k) or "") for k in ("events", "eventSummary", "matchEvents")).lower()
    if row.get("redCard") or row.get("redCards") or "red card" in events or "second yellow" in events:
        tags.append("red_card")
    if row.get("earlyInjury") or row.get("injuryMinute") is not None:
        tags.append("early_injury")
    if row.get("penalty") or row.get("penalties"):
        tags.append("penalty")
    minutes = _num(row.get("minutes"))
    if minutes is not None and minutes < 40:
        tags.append("short_minutes")
    margin = _score_margin(row)
    if margin is not None and margin >= 2:
        tags.append("extreme_score_state" if margin >= 3 else "large_score_state")
    if row.get("formationAnomaly") or row.get("formationChanged"):
        tags.append("formation_anomaly")
    if row.get("rotation") or row.get("opponentRotation") or row.get("rotated"):
        tags.append("rotation")
    if row.get("substitution") or row.get("wasSubstitute") or row.get("subbedOn"):
        tags.append("substitution")
    if row.get("knockoutUrgency") or row.get("isKnockout") or row.get("mustWinByGoals"):
        tags.append("knockout_urgency")
    if row.get("refereeCardEnvironment") or row.get("refereeCardsPerGame"):
        tags.append("referee_context")
    return tags


def _distortion_weight(tags: list[str]) -> float:
    """Keep known regime changes visible while reducing their numeric leverage."""
    hard_exclude = {"red_card", "early_injury", "extreme_score_state", "formation_anomaly"}
    if hard_exclude.intersection(tags):
        return 0.0
    if {"short_minutes", "substitution", "rotation", "penalty", "knockout_urgency"}.intersection(tags):
        return 0.4
    if "large_score_state" in tags:
        return 0.6
    return 1.0


def _clean_rows(rows: list[dict], venue: str | None, prop: str) -> tuple[list[dict], list[dict]]:
    usable: list[dict] = []
    tagged: list[dict] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or _value(row, prop) is None:
            continue
        tags = distortion_tags(row)
        enriched = {**row, "distortionTags": tags}
        tagged.append(enriched)
        if (_venue(row) == venue if venue else True) and (_num(row.get("minutes")) or 90) >= 30 and not {
            "red_card", "early_injury", "extreme_score_state", "formation_anomaly"
        }.intersection(tags):
            usable.append(enriched)
    return usable, tagged


def _role_bucket(role: Any, position: Any) -> str:
    raw = str(role or position or "").strip().lower()
    if any(token in raw for token in (
        "goalkeeper", "keeper", "gk", "shot-stopper", "shot stopper",
    )):
        return "GK"
    if any(token in raw for token in ("center-back", "centre-back", "cb", "defender")):
        return "CB"
    if any(token in raw for token in ("pivot", "anchor", "dm", "cm", "midfielder")):
        return "PIVOT"
    return str(role or position or "UNVERIFIED").upper() or "UNVERIFIED"


def _identity_int(row: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        try:
            value = int(row.get(key))
            if value:
                return value
        except (TypeError, ValueError):
            continue
    return None


def _row_side_team_ids(row: dict[str, Any]) -> tuple[int | None, int | None]:
    fixture = row.get("fixture") if isinstance(row.get("fixture"), dict) else {}
    teams = row.get("teams") if isinstance(row.get("teams"), dict) else {}
    home = teams.get("home") if isinstance(teams.get("home"), dict) else {}
    away = teams.get("away") if isinstance(teams.get("away"), dict) else {}
    home_id = _identity_int(row, "homeTeamId", "home_id") or _identity_int(fixture, "homeTeamId")
    away_id = _identity_int(row, "awayTeamId", "away_id") or _identity_int(fixture, "awayTeamId")
    home_id = home_id or _identity_int(home, "id")
    away_id = away_id or _identity_int(away, "id")
    return home_id, away_id


def canonical_venue(row: dict[str, Any], *, target_team_id: int | None = None) -> str | None:
    """Return venue from one perspective: the requested player's club.

    Hydrated opponent rows are stored with ``venuePerspective=target`` after
    assembly. Already-hydrated comparison rows are tagged the same way at the
    boundary. Fixture side IDs are used only when no canonical venue has been
    assigned yet, so venue is never flipped twice.
    """
    raw = str(row.get("venue") or "").strip().lower()
    if row.get("venuePerspective") == "target":
        return raw if raw in {"home", "away"} else None
    team_id = _identity_int(row, "teamId", "fixtureTeamId", "team_id")
    home_id, away_id = _row_side_team_ids(row)
    if team_id and home_id and away_id:
        if team_id == home_id:
            observed = "home"
        elif team_id == away_id:
            observed = "away"
        else:
            observed = None
        if observed:
            return observed if team_id == target_team_id else (
                "away" if observed == "home" else "home"
            )
    if raw in {"home", "away"}:
        return raw
    if row.get("isHome") is True:
        return "home"
    if row.get("isHome") is False:
        return "away"
    return None


def _row_source_path(row: dict[str, Any]) -> str:
    return str(
        row.get("sourcePath")
        or row.get("historySource")
        or row.get("source")
        or "unknown"
    )


def finalize_opponent_cohort(
    rows: list[dict[str, Any]],
    *,
    prop: str,
    venue: str | None,
    target_player_id: int | None = None,
    target_team_id: int | None = None,
    target_fixture_id: int | None = None,
    target_opponent_id: int | None = None,
    target_bucket: str = "UNVERIFIED",
    target_formation: str | None = None,
    strict_identity: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Create the deterministic admitted/rejected cohort ledger.

    ``strict_identity`` is enabled for persisted prediction evidence. The
    legacy direct engine tests intentionally omit fixture identity and continue
    to exercise the calculation contract without pretending to be persisted
    provider evidence.
    """
    admitted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for index, source in enumerate(rows if isinstance(rows, list) else []):
        row = dict(source) if isinstance(source, dict) else {}
        reason = None
        fixture_id = _identity_int(row, "fixtureId", "fixture_id", "id")
        player_id = _identity_int(row, "playerId", "player_id")
        team_id = _identity_int(row, "teamId", "fixtureTeamId", "team_id")
        opponent_id = _identity_int(row, "opponentId", "fixtureOpponentId", "opponent_id")
        home_id, away_id = _row_side_team_ids(row)
        if opponent_id is None and target_opponent_id is not None and target_opponent_id in {home_id, away_id}:
            opponent_id = target_opponent_id
            row["opponentId"] = target_opponent_id
        row_venue = canonical_venue(row, target_team_id=target_team_id)
        row["venue"] = row_venue
        row["venuePerspective"] = "target"
        row["sourcePath"] = _row_source_path(row)
        row["exactRoleBucket"] = _role_bucket(row.get("role"), row.get("position"))
        row["providerRole"] = row.get("role")
        row["statValue"] = _value(row, prop)
        row["baselineValue"] = (
            _num(row.get("normalMatchingVenue"))
            or _num(row.get("baseline"))
            or _num(row.get("seasonAvgStat"))
        )
        tags = distortion_tags(row)
        row["distortionTags"] = tags
        row["distortionWeight"] = _distortion_weight(tags)
        if strict_identity and fixture_id is None:
            reason = "missing_fixture_id"
        elif strict_identity and player_id is None:
            reason = "missing_player_id"
        elif strict_identity and team_id is None:
            reason = "missing_team_id"
        elif target_player_id is not None and player_id == target_player_id:
            reason = "target_player_id"
        elif target_team_id is not None and team_id == target_team_id:
            reason = "target_team_id"
        elif target_fixture_id is not None and fixture_id == target_fixture_id:
            reason = "target_fixture_id"
        elif strict_identity and opponent_id is not None and target_opponent_id is not None and opponent_id != target_opponent_id:
            reason = "fixture_opponent_not_requested_opponent"
        elif strict_identity and home_id and away_id and target_opponent_id is not None and (
            target_opponent_id not in {home_id, away_id} or team_id not in {home_id, away_id}
        ):
            reason = "fixture_sides_do_not_contain_opponent_and_comparator"
        elif strict_identity and target_opponent_id is not None and opponent_id != target_opponent_id:
            reason = "missing_or_mismatched_opponent_identity"
        elif row["exactRoleBucket"] != target_bucket:
            reason = "role_bucket_mismatch"
        elif venue and row_venue != venue:
            reason = "venue_mismatch"
        elif target_formation and row.get("formation") and str(row.get("formation")).strip().lower() != target_formation:
            reason = "formation_mismatch"
        elif (_num(row.get("minutes")) or 0) < 30:
            reason = "minutes_below_30"
        elif row["statValue"] is None:
            reason = "missing_stat_value"
        if reason:
            row["admissionStatus"] = "rejected"
            row["rejectionReason"] = reason
            row["admissionReason"] = None
            rejected.append(row)
            continue
        row["admissionStatus"] = "admitted"
        row["admissionReason"] = "exact_role_opponent_fixture_matching_venue"
        row["rejectionReason"] = None
        admitted.append(row)

    admitted_ids = {
        (_identity_int(row, "fixtureId", "fixture_id", "id"),
         _identity_int(row, "playerId", "player_id"),
         _identity_int(row, "teamId", "fixtureTeamId", "team_id"))
        for row in admitted
    }
    purity_failures = []
    for row in admitted:
        if target_player_id is not None and _identity_int(row, "playerId", "player_id") == target_player_id:
            purity_failures.append("target_player_id_in_admitted_cohort")
        if target_team_id is not None and _identity_int(row, "teamId", "fixtureTeamId", "team_id") == target_team_id:
            purity_failures.append("target_team_id_in_admitted_cohort")
        if target_fixture_id is not None and _identity_int(row, "fixtureId", "fixture_id", "id") == target_fixture_id:
            purity_failures.append("target_fixture_id_in_admitted_cohort")
        if venue and canonical_venue(row, target_team_id=target_team_id) != venue:
            purity_failures.append("admitted_row_venue_mismatch")
        if strict_identity and target_opponent_id is not None and _identity_int(row, "opponentId", "fixtureOpponentId", "opponent_id") != target_opponent_id:
            purity_failures.append("admitted_row_opponent_identity_mismatch")
    valid = not purity_failures and (not strict_identity or bool(admitted) or bool(rejected))
    assertions = {
        "targetPlayerIdAbsent": not any(
            target_player_id is not None and _identity_int(row, "playerId", "player_id") == target_player_id
            for row in admitted
        ),
        "targetTeamIdAbsent": not any(
            target_team_id is not None and _identity_int(row, "teamId", "fixtureTeamId", "team_id") == target_team_id
            for row in admitted
        ),
        "targetFixtureIdAbsent": not any(
            target_fixture_id is not None and _identity_int(row, "fixtureId", "fixture_id", "id") == target_fixture_id
            for row in admitted
        ),
        "opponentSideOnly": not any(
            strict_identity and target_opponent_id is not None
            and _identity_int(row, "opponentId", "fixtureOpponentId", "opponent_id") != target_opponent_id
            for row in admitted
        ),
        "venueMatches": not any(
            venue and canonical_venue(row, target_team_id=target_team_id) != venue
            for row in admitted
        ),
    }
    valid = valid and all(assertions.values())
    return admitted, rejected, {
        "valid": valid,
        "purityFailures": sorted(set(purity_failures)),
        "assertions": assertions,
        "candidateCount": len(rows) if isinstance(rows, list) else 0,
        "admittedCount": len(admitted),
        "rejectedCount": len(rejected),
    }


def _cohort_packet(prediction: dict, prop: str, venue: str | None, opponent: str | None, role: Any, position: Any, evidence: dict | None = None) -> dict:
    comparison = prediction.get("positionComparison") or {}
    role_packet = prediction.get("roleEvidencePacket") or {}
    rows = comparison.get("players") if isinstance(comparison, dict) else []
    if not isinstance(rows, list):
        rows = []
    if not rows:
        rows = role_packet.get("comparablePlayers") or role_packet.get("players") or []
    if evidence and isinstance(evidence.get("opponentRoleCandidates"), list):
        # The evidence assembler is authoritative because it records the
        # historical cutoff and provider detail coverage for each row.
        rows = evidence["opponentRoleCandidates"]
    target_bucket = _role_bucket(role, position)
    target_formation = str(
        prediction.get("formation") or prediction.get("teamFormation") or
        (role_packet.get("formation") if isinstance(role_packet, dict) else "") or ""
    ).strip().lower()
    snapshot = (evidence or {}).get("cohortSnapshot") if isinstance(evidence, dict) else None
    if isinstance(snapshot, dict):
        if snapshot.get("status") == "invalid":
            return {
                "status": "invalid",
                "roleBucket": target_bucket,
                "formation": target_formation or "unverified",
                "sampleSize": 0,
                "weightedSampleSize": 0,
                "cleanSampleSize": 0,
                "distortedSampleSize": 0,
                "workloadAverage": None,
                "normalMatchingVenueAverage": None,
                "opponentRoleEffect": None,
                "effect": "not_established",
                "provenance": "immutable causal cohort snapshot",
                "snapshotKey": snapshot.get("snapshotKey"),
                "snapshotVersion": snapshot.get("snapshotVersion"),
                "validation": snapshot.get("validation") or {},
                "limitation": "EVIDENCE INVALID: cohort purity or opponent-side assertion failed.",
            }
        rows = snapshot.get("admittedRows") or []
    cohort: list[dict] = []
    for row in rows:
        if not isinstance(row, dict) or _value(row, prop) is None:
            continue
        row_bucket = _role_bucket(row.get("role"), row.get("position"))
        row_venue = _venue(row)
        row_formation = str(row.get("formation") or row.get("teamFormation") or "").strip().lower()
        row_opp = str(row.get("opponent") or row.get("opponentName") or "").casefold()
        if row_bucket != target_bucket or (venue and row_venue and row_venue != venue):
            continue
        # Formation is a matching constraint only when both observations
        # actually carry it; missing formation remains UNKNOWN, not mismatch.
        if target_formation and row_formation and target_formation != row_formation:
            continue
        if opponent and row_opp and str(opponent).casefold() not in row_opp and row.get("opponentId") != prediction.get("opponentId"):
            continue
        minutes = _num(row.get("minutes")) or 0
        if minutes < 30:
            continue
        tags = distortion_tags(row)
        cohort.append({**row, "distortionTags": tags, "distortionWeight": _distortion_weight(tags)})
    weighted = [row for row in cohort if row["distortionWeight"] > 0]
    values = [(_value(row, prop), row["distortionWeight"]) for row in weighted]
    values = [(value, weight) for value, weight in values if value is not None]
    baseline_values = [
        (
            (
                _num(row.get("normalMatchingVenue"))
                or _num(row.get("baseline"))
                or _num(row.get("seasonAvgStat"))
            ),
            row["distortionWeight"],
        )
        for row in weighted
    ]
    baseline_values = [(value, weight) for value, weight in baseline_values if value is not None]
    value_weight = sum(weight for _value_item, weight in values)
    baseline_weight = sum(weight for _value_item, weight in baseline_values)
    average = sum(value * weight for value, weight in values) / value_weight if value_weight else None
    baseline = sum(value * weight for value, weight in baseline_values) / baseline_weight if baseline_weight else None
    uplift = average / baseline if average is not None and baseline and baseline > 0 else None
    status = "available" if value_weight >= 3 and uplift is not None else "partial" if values else "not_established"
    return {
        "status": status,
        "roleBucket": target_bucket,
        "formation": target_formation or "unverified",
        "sampleSize": len(values),
        "weightedSampleSize": round(value_weight, 2),
        "cleanSampleSize": sum(1 for row in weighted if row["distortionWeight"] == 1),
        "distortedSampleSize": len(cohort) - sum(1 for row in weighted if row["distortionWeight"] == 1),
        "workloadAverage": round(average, 2) if average is not None else None,
        "normalMatchingVenueAverage": round(baseline, 2) if baseline is not None else None,
        "opponentRoleEffect": round(uplift, 3) if uplift is not None else None,
        "effect": "uplift" if uplift is not None and uplift > 1.08 else "suppression" if uplift is not None and uplift < 0.92 else "neutral" if uplift is not None else "not_established",
        "provenance": "positionComparison/roleEvidencePacket exact-role rows",
        "limitation": None if status == "available" else "Fewer than three distortion-weighted exact-role matching-venue opponent rows with a baseline.",
    }


def _script(prediction: dict, venue: str | None, evidence: dict | None = None) -> dict:
    context = prediction.get("tacticalContext") or {}
    moneyline = prediction.get("moneyline") or {}
    game_script = prediction.get("gameScript") or {}
    manager = prediction.get("managerContext") or {}
    team_poss = _num(context.get("expectedPossession") or context.get("teamPossession"))
    opp_poss = _num(context.get("opponentExpectedPossession") or context.get("opponentPossession"))
    favorite = str(moneyline.get("favorite") or "").lower()
    dominant = str(game_script.get("dominant") or "").lower()
    if team_poss is not None and opp_poss is not None:
        if team_poss >= opp_poss + 7:
            label = "CONTROL"
        elif opp_poss >= team_poss + 7:
            label = "SUPPRESSION" if venue == "away" else "SIEGE"
        else:
            label = "TRANSITION"
        status = "available"
    elif favorite:
        label, status = "CONTROL" if favorite in {"team", "home"} else "SUPPRESSION", "partial"
    else:
        label, status = "NOT ESTABLISHED", "not_established"
    if dominant in {"home_blowout", "away_blowout"}:
        label = f"{label}→LOCK" if label != "NOT ESTABLISHED" else "LOCK"
    elif dominant == "open_close":
        label = f"{label}→CHASE" if label != "NOT ESTABLISHED" else "CHASE"
    elif dominant == "low_scoring" and label == "CONTROL":
        label = "CONTROL→LOCK"
    recent_manager = bool(manager.get("isRecent"))
    half_life = (
        "short" if recent_manager or "→CHASE" in label else
        "long" if "LOCK" in label and status == "available" else
        "medium" if status == "available" else "not_established"
    )
    return {
        "status": status,
        "classification": label,
        "halfLife": half_life,
        "source": "observed possession, odds, scenario and manager context",
        "currentManagerStable": None if not manager else not recent_manager,
        "evidenceCoverage": (evidence or {}).get("coverage") or {},
    }


def _branches(prop: str, role_bucket: str, script: str) -> dict:
    pass_like = prop in {"pass_attempts", "passes"} and role_bucket in {"GK", "CB", "PIVOT"}
    base = "NOT ESTABLISHED"
    if script.startswith("CONTROL"):
        base = "UP" if pass_like else "NEUTRAL"
    elif script.startswith(("SUPPRESSION", "SIEGE")):
        base = "UP" if prop in {"saves", "clearances", "tackles", "interceptions"} else "DOWN" if pass_like else "NEUTRAL"
    return {
        "target_scores_first": {"workload": base, "regime": "SURVIVES" if base != "NOT ESTABLISHED" else "NOT ESTABLISHED"},
        "opponent_scores_first": {"workload": "UP" if prop in {"shots", "saves", "crosses", "key_passes"} else "DOWN" if pass_like else base, "regime": "EXPLODES" if prop in {"shots", "saves"} else "SURVIVES" if base != "NOT ESTABLISHED" else "NOT ESTABLISHED"},
        "level_around_60": {"workload": base, "regime": "FREEZES" if script.startswith("CONTROL") else "SURVIVES" if base != "NOT ESTABLISHED" else "NOT ESTABLISHED"},
        "target_scores_before_20": {"workload": "DOWN" if pass_like else base, "regime": "FREEZES" if script.startswith("CONTROL") else "SURVIVES" if base != "NOT ESTABLISHED" else "NOT ESTABLISHED"},
        "opponent_scores_before_20": {"workload": "UP" if prop in {"shots", "saves", "crosses", "key_passes"} else "DOWN" if pass_like else base, "regime": "EXPLODES" if prop in {"shots", "saves"} else "SURVIVES" if base != "NOT ESTABLISHED" else "NOT ESTABLISHED"},
        "early_goal_either_way": {"workload": "NOT ESTABLISHED" if script == "NOT ESTABLISHED" else base, "regime": "EXPLODES" if script.startswith(("SUPPRESSION", "SIEGE")) else "SURVIVES" if base != "NOT ESTABLISHED" else "NOT ESTABLISHED"},
    }


def _script_direction(prop: str, script: str) -> str | None:
    """Translate a script into a workload direction for the selected prop."""
    if prop in {"passes", "pass_attempts", "key_passes"}:
        if script.startswith("CONTROL"):
            return "over"
        if script.startswith(("SUPPRESSION", "SIEGE")):
            return "under"
    if prop in {"saves", "clearances", "tackles", "interceptions"}:
        if script.startswith(("SUPPRESSION", "SIEGE")):
            return "over"
        if script.startswith("CONTROL"):
            return "under"
    return None


def _sample_direction(rows: list[dict], prop: str, line: float | None) -> str | None:
    if line is None or len(rows) < 3:
        return None
    values = [_value(row, prop) for row in rows]
    values = [value for value in values if value is not None]
    if len(values) < 3:
        return None
    average = sum(values) / len(values)
    # A half-point cushion prevents a line-adjacent sample from pretending to
    # be corroboration for a production flip.
    if average >= line + 0.5:
        return "over"
    if average <= line - 0.5:
        return "under"
    return None


def _h2h_direction(prediction: dict, line: float | None, prop: str) -> str | None:
    h2h = prediction.get("h2hPlayerStats") or {}
    try:
        sample_size = int(h2h.get("sampleSize") or 0)
    except (TypeError, ValueError):
        sample_size = 0
    if sample_size < 3 or line is None:
        return None
    average = _num(h2h.get("avgVsOpponent"))
    if average is None:
        return None
    if average >= line + 0.5:
        return "over"
    if average <= line - 0.5:
        return "under"
    return None


def build_causal_script_packet(prediction: dict[str, Any], request: dict[str, Any] | None = None, context: dict[str, Any] | None = None, evidence: dict[str, Any] | None = None) -> dict[str, Any]:
    request = request or {}
    context = context or {}
    prop = str(prediction.get("propType") or request.get("prop_type") or "").lower()
    venue = str(prediction.get("resolvedVenue") or prediction.get("venue") or context.get("venue") or "").lower() or None
    opponent = context.get("opponent_name") or prediction.get("opponentName")
    role = prediction.get("exactTacticalRole") or prediction.get("tacticalRole") or prediction.get("role") or prediction.get("playerRole")
    position = prediction.get("playerPosition") or prediction.get("position")
    logs = (
        (evidence or {}).get("targetHistory")
        or ((prediction.get("playerGameLogs") or {}).get("games") if isinstance(prediction.get("playerGameLogs"), dict) else prediction.get("gameLogs"))
    )
    clean_logs, tagged_logs = _clean_rows(logs or [], venue, prop)
    cohort = _cohort_packet(prediction, prop, venue, opponent, role, position, evidence)
    script = _script(prediction, venue, evidence)
    role_bucket = cohort["roleBucket"]
    chain = PROP_CHAINS.get(prop, f"role/zone → opportunity creation → {prop}")
    projection = _num(
        prediction.get("deterministicProjection")
        if prediction.get("deterministicProjection") is not None
        else prediction.get("projectedValue")
        if prediction.get("projectedValue") is not None
        else prediction.get("projection")
    )
    line = _num(prediction.get("line") if prediction.get("line") is not None else request.get("line"))
    gap = abs(projection - line) if projection is not None and line is not None else None
    model_direction = str(
        prediction.get("modelDirection")
        or prediction.get("preCausalRecommendation")
        or prediction.get("recommendation")
        or ""
    ).lower()
    if model_direction not in {"over", "under"} and projection is not None and line is not None:
        model_direction = "over" if projection > line else "under" if projection < line else ""
    # A verified position bucket (GK/CB/PIVOT) is usable role evidence even
    # when an exact lineup role string was not attached. Truly unknown position
    # remains conservative.
    uncertain_role = role_bucket == "UNVERIFIED"
    current_regime_unknown = script["status"] not in {"available", "partial"}
    thin_edge = gap is not None and gap < 1.0
    mechanism_edge = cohort["effect"] in {"uplift", "suppression"} and cohort["status"] == "available"
    invalid_evidence = cohort.get("status") == "invalid"
    mechanism_direction = (
        "over" if cohort["effect"] == "uplift" else "under"
        if cohort["effect"] == "suppression" else None
    )
    causal_direction = (
        "EVIDENCE INVALID" if invalid_evidence else
        "MORE" if mechanism_direction == "over" else
        "LESS" if mechanism_direction == "under" else
        "EVIDENCE INCOMPLETE" if cohort["status"] != "available" else
        "NEUTRAL"
    )
    clean_exact_role_n = int(cohort.get("cleanSampleSize") or 0)
    script_direction = _script_direction(prop, script["classification"])
    venue_direction = _sample_direction(clean_logs, prop, line)
    h2h_direction = _h2h_direction(prediction, line, prop)
    corroboration = [
        label for label, direction in (
            ("current_regime", script_direction),
            ("matching_venue_history", venue_direction),
            ("player_h2h", h2h_direction),
        )
        if mechanism_direction and direction == mechanism_direction
    ]
    strong_corroboration = (
        clean_exact_role_n >= 5
        or clean_exact_role_n >= 3 and bool(corroboration)
    )
    # Three rows plus aligned context can authorize a production flip, but it
    # is still provisional confidence. Only five clean exact-role rows may
    # promote the sample itself to strong.
    sample_strength = (
        "invalid" if invalid_evidence else
        "strong" if clean_exact_role_n >= 5 else
        "provisional" if clean_exact_role_n >= 3 else "insufficient"
    )
    distorted_dominant = bool(tagged_logs) and len(clean_logs) < max(2, len(tagged_logs) / 2)
    cohort_incomplete = cohort["status"] != "available"
    if invalid_evidence:
        decision = "PASS"
        reason = "EVIDENCE INVALID: opponent cohort purity or fixture-side assertion failed; causal direction withheld."
        verdict = "EVIDENCE INVALID"
    elif uncertain_role or current_regime_unknown or thin_edge or cohort_incomplete:
        if uncertain_role:
            missing_reason = "the player's exact role is not verified"
        elif current_regime_unknown:
            missing_reason = "the current match regime is not verified"
        elif cohort_incomplete:
            missing_reason = "the opponent-created exact-role workload baseline is not verified"
        else:
            missing_reason = "the deterministic edge is too close to the line"
        decision = "PASS"
        reason = f"CAUSAL EVIDENCE INCOMPLETE: {missing_reason}; recommendation withheld."
        verdict = "DISTORTED SAMPLE" if distorted_dominant else "EVIDENCE INCOMPLETE"
    elif mechanism_edge and (
        (model_direction == "under" and cohort["effect"] == "uplift")
        or (model_direction == "over" and cohort["effect"] == "suppression")
    ):
        decision, reason = "REJECT", (
            f"MODEL/CAUSAL DISAGREEMENT: model {model_direction.upper()} "
            f"conflicts with causal {causal_direction} workload evidence."
        )
        verdict = "CAUSAL CONTRADICTION"
    elif mechanism_edge and model_direction == mechanism_direction:
        decision, reason = "CONFIRM", (
            f"MECHANISM EDGE: clean exact-role opponent workload supports "
            f"the model {model_direction.upper()} direction."
        )
        verdict = "CAUSAL CONFIRM" if cohort["effect"] != "neutral" else "MECHANISM EDGE"
    elif mechanism_edge:
        decision, reason = "PASS", (
            "CAUSAL EVIDENCE INCOMPLETE: a deterministic model direction "
            "was not available for comparison; recommendation withheld."
        )
        verdict = "EVIDENCE INCOMPLETE"
    else:
        decision = "PASS"
        reason = "NO MECHANISM EDGE: validated exact-role workload is neutral for this line."
        verdict = "DISTORTED SAMPLE" if distorted_dominant else "NO MECHANISM EDGE"
    return {
        "schemaVersion": CAUSAL_SCHEMA_VERSION,
        "modelVersion": CAUSAL_MODEL_VERSION,
        "status": "available" if prop in PROP_CHAINS and evidence else "partial",
        "statProductionChain": chain,
        "identity": {
            "fixtureId": prediction.get("fixtureId") or request.get("fixture_id"),
            "playerId": prediction.get("playerId") or request.get("player_id"),
            "playerName": prediction.get("playerName"),
            "teamName": prediction.get("teamName"),
            "opponentName": opponent,
            "venue": venue,
            "role": role or "unverified",
            "roleBucket": role_bucket,
            "propType": prop,
            "line": line,
        },
        "script": script,
        "history": {
            "matchingVenueCleanSample": len(clean_logs),
            "matchingVenueTaggedSample": len(tagged_logs),
            "distortionCounts": dict(Counter(tag for row in tagged_logs for tag in row.get("distortionTags", []))),
            "excludedRows": max(0, len(tagged_logs) - len(clean_logs)),
            "source": "prediction playerGameLogs pre-match snapshot",
            "weighted": True,
        },
        "opponentRoleCohort": cohort,
        "modelProjection": projection,
        "modelDirection": model_direction.upper() or None,
        "causalDirection": causal_direction,
        "scoreStateBranches": _branches(prop, role_bucket, script["classification"]),
        "causalVerdict": verdict,
        "corroboration": {
            "cleanExactRoleSamples": clean_exact_role_n,
            "requiredForProductionFlip": "5 clean exact-role samples, or 3 plus aligned current-regime, matching-venue, or H2H evidence",
            "alignedEvidence": corroboration,
            "currentRegimeDirection": script_direction,
            "matchingVenueDirection": venue_direction,
            "h2hDirection": h2h_direction,
            "sampleStrength": sample_strength,
            "productionFlipEligible": bool(mechanism_edge and strong_corroboration),
            "strongConfidenceAllowed": bool(clean_exact_role_n >= 5),
        },
        "evidence": evidence or {"status": "incomplete", "reason": "Evidence assembly did not run."},
        "recommendationGate": {
            "decision": decision,
            "reason": reason,
            "rpRecommendation": model_direction or "not_set",
            "wouldRecommendation": model_direction if decision not in {"PASS", "REJECT"} else "PASS",
            "productionInfluence": "active_pass_guard",
            "replayValidated": False,
            "strongestOppositeCase": (
                "Opponent-created role workload defeats the selected direction."
                if decision == "REJECT" else
                "The selected direction may be right only if the observed role/possession mechanism persists."
            ),
            "killCondition": "Early goal, red card, substitution, or formation change can end the pre-match mechanism.",
        },
        "provenance": {
            "source": "deterministic provider-observation synthesis",
            "pregameOnly": True,
            "resultLeakage": False,
            "optionalEvidenceFailOpen": True,
        },
    }


def replay_reference_misses() -> list[dict[str, Any]]:
    """Return pregame-only decisions for the three supplied regression cases."""
    cases = [
        ("Petrovic", "pass_attempts", "under", 23.5, 24.5, "GK", 35, 20),
        ("Ferraresi", "passes", "under", 46.0, 50.5, "CB", 65, 48),
        # No exact role is supplied: the 0.5 edge must be rejected rather than
        # forcing a direction from a borderline projection.
        ("Moncayola", "passes", "over", 40.0, 39.5, "", None, None),
    ]
    results = []
    for name, prop, rec, projection, line, role, cohort_value, cohort_baseline in cases:
        cohort_rows = []
        if cohort_value is not None:
            for _ in range(3):
                cohort_rows.append({
                    "role": role, "position": role, "venue": "home",
                    "value": cohort_value, "normalMatchingVenue": cohort_baseline,
                    "minutes": 90,
                })
        packet = build_causal_script_packet({
            "playerName": name, "propType": prop, "recommendation": rec,
            "projection": projection, "line": line, "playerPosition": role,
            "positionComparison": {"players": cohort_rows},
            "tacticalContext": {"expectedPossession": 55, "opponentExpectedPossession": 45},
        })
        results.append({
            "case": name,
            "decision": packet["recommendationGate"]["decision"],
            "pregameOnly": True,
            "resultDataUsed": False,
        })
    return results