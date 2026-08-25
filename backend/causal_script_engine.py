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
    "pass_attempts": ("value", "targetStat", "passes_total", "passes"),
    "passes": ("value", "targetStat", "passes_total", "passes"),
    "shots": ("value", "targetStat", "shots_total", "shots"),
    "shots_on_target": ("value", "targetStat", "shots_on", "shots_on_target"),
    "saves": ("value", "targetStat", "goals_saves", "saves"),
    "clearances": ("value", "targetStat", "clearances"),
    "crosses": ("value", "targetStat", "crosses"),
    "tackles": ("value", "targetStat", "tackles_total", "tackles"),
    "interceptions": ("value", "targetStat", "interceptions"),
    "dribbles": ("value", "targetStat", "dribbles_attempts", "dribbles"),
    "key_passes": ("value", "targetStat", "passes_key", "key_passes"),
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
    return tags


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
    if any(token in raw for token in ("goalkeeper", "keeper", "gk")):
        return "GK"
    if any(token in raw for token in ("center-back", "centre-back", "cb", "defender")):
        return "CB"
    if any(token in raw for token in ("pivot", "anchor", "dm", "cm", "midfielder")):
        return "PIVOT"
    return str(role or position or "UNKNOWN").upper() or "UNKNOWN"


def _cohort_packet(prediction: dict, prop: str, venue: str | None, opponent: str | None, role: Any, position: Any) -> dict:
    comparison = prediction.get("positionComparison") or {}
    role_packet = prediction.get("roleEvidencePacket") or {}
    rows = comparison.get("players") if isinstance(comparison, dict) else []
    if not isinstance(rows, list):
        rows = []
    if not rows:
        rows = role_packet.get("comparablePlayers") or role_packet.get("players") or []
    target_bucket = _role_bucket(role, position)
    target_formation = str(
        prediction.get("formation") or prediction.get("teamFormation") or
        (role_packet.get("formation") if isinstance(role_packet, dict) else "") or ""
    ).strip().lower()
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
        cohort.append({**row, "distortionTags": tags})
    clean = [row for row in cohort if not {"red_card", "early_injury", "extreme_score_state", "formation_anomaly"}.intersection(row["distortionTags"])]
    values = [_value(row, prop) for row in clean]
    values = [value for value in values if value is not None]
    baseline_values = [_num(row.get("normalMatchingVenue")) or _num(row.get("baseline")) for row in clean]
    baseline_values = [value for value in baseline_values if value is not None]
    average = sum(values) / len(values) if values else None
    baseline = sum(baseline_values) / len(baseline_values) if baseline_values else None
    uplift = average / baseline if average is not None and baseline and baseline > 0 else None
    status = "available" if len(values) >= 3 and uplift is not None else "partial" if values else "UNKNOWN"
    return {
        "status": status,
        "roleBucket": target_bucket,
        "formation": target_formation or "UNKNOWN",
        "sampleSize": len(values),
        "cleanSampleSize": len(values),
        "distortedSampleSize": len(cohort) - len(clean),
        "workloadAverage": round(average, 2) if average is not None else None,
        "normalMatchingVenueAverage": round(baseline, 2) if baseline is not None else None,
        "opponentRoleEffect": round(uplift, 3) if uplift is not None else None,
        "effect": "uplift" if uplift is not None and uplift > 1.08 else "suppression" if uplift is not None and uplift < 0.92 else "neutral" if uplift is not None else "UNKNOWN",
        "provenance": "positionComparison/roleEvidencePacket exact-role rows",
        "limitation": None if status == "available" else "Fewer than three clean exact-role matching-venue opponent rows with a baseline.",
    }


def _script(prediction: dict, venue: str | None) -> dict:
    context = prediction.get("tacticalContext") or {}
    moneyline = prediction.get("moneyline") or {}
    team_poss = _num(context.get("expectedPossession") or context.get("teamPossession"))
    opp_poss = _num(context.get("opponentExpectedPossession") or context.get("opponentPossession"))
    favorite = str(moneyline.get("favorite") or "").lower()
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
        label, status = "UNKNOWN", "UNKNOWN"
    return {"status": status, "classification": label, "halfLife": "UNKNOWN", "source": "observed possession/moneyline proxies"}


def _branches(prop: str, role_bucket: str, script: str) -> dict:
    pass_like = prop in {"pass_attempts", "passes"} and role_bucket in {"GK", "CB", "PIVOT"}
    base = "UNKNOWN"
    if script == "CONTROL":
        base = "UP" if pass_like else "NEUTRAL"
    elif script in {"SUPPRESSION", "SIEGE"}:
        base = "UP" if prop in {"saves", "clearances", "tackles", "interceptions"} else "DOWN" if pass_like else "NEUTRAL"
    return {
        "target_scores_first": {"workload": base, "regime": "SURVIVES" if base != "UNKNOWN" else "UNKNOWN"},
        "opponent_scores_first": {"workload": "UP" if prop in {"shots", "saves", "crosses", "key_passes"} else "DOWN" if pass_like else base, "regime": "EXPLODES" if prop in {"shots", "saves"} else "SURVIVES" if base != "UNKNOWN" else "UNKNOWN"},
        "level_around_60": {"workload": base, "regime": "FREEZES" if script == "CONTROL" else "SURVIVES" if base != "UNKNOWN" else "UNKNOWN"},
        "early_goal_either_way": {"workload": "UNKNOWN" if script == "UNKNOWN" else base, "regime": "EXPLODES" if script in {"SUPPRESSION", "SIEGE"} else "SURVIVES" if base != "UNKNOWN" else "UNKNOWN"},
    }


def build_causal_script_packet(prediction: dict[str, Any], request: dict[str, Any] | None = None, context: dict[str, Any] | None = None) -> dict[str, Any]:
    request = request or {}
    context = context or {}
    prop = str(prediction.get("propType") or request.get("prop_type") or "").lower()
    venue = str(prediction.get("resolvedVenue") or prediction.get("venue") or context.get("venue") or "").lower() or None
    opponent = context.get("opponent_name") or prediction.get("opponentName")
    role = prediction.get("exactTacticalRole") or prediction.get("tacticalRole") or prediction.get("role") or prediction.get("playerRole")
    position = prediction.get("playerPosition") or prediction.get("position")
    logs = (prediction.get("playerGameLogs") or {}).get("games") if isinstance(prediction.get("playerGameLogs"), dict) else prediction.get("gameLogs")
    clean_logs, tagged_logs = _clean_rows(logs or [], venue, prop)
    cohort = _cohort_packet(prediction, prop, venue, opponent, role, position)
    script = _script(prediction, venue)
    role_bucket = cohort["roleBucket"]
    chain = PROP_CHAINS.get(prop, f"role/zone → opportunity creation → {prop}")
    projection = _num(prediction.get("projectedValue") if prediction.get("projectedValue") is not None else prediction.get("projection"))
    line = _num(prediction.get("line") if prediction.get("line") is not None else request.get("line"))
    gap = abs(projection - line) if projection is not None and line is not None else None
    recommendation = str(prediction.get("recommendation") or "").lower()
    # A verified position bucket (GK/CB/PIVOT) is usable role evidence even
    # when an exact lineup role string was not attached. Truly unknown position
    # remains conservative.
    uncertain_role = role_bucket == "UNKNOWN"
    thin_edge = gap is not None and gap < 1.0
    mechanism_edge = cohort["effect"] in {"uplift", "suppression"} and cohort["status"] == "available"
    if uncertain_role or thin_edge:
        decision, reason = "PASS", "MODEL EDGE REJECTED: exact-role concentration or numerical edge is too uncertain."
    elif mechanism_edge and ((recommendation == "under" and cohort["effect"] == "uplift") or (recommendation == "over" and cohort["effect"] == "suppression")):
        decision, reason = "REJECT", "CAUSAL CONTRADICTION: today's opponent-created exact-role workload conflicts with the RP direction."
    elif mechanism_edge:
        decision, reason = "CONFIRM", "MECHANISM EDGE: clean exact-role opponent workload supports the RP direction."
    else:
        decision, reason = "UNKNOWN", "UNKNOWN: no validated exact-role opponent workload effect is available."
    return {
        "schemaVersion": CAUSAL_SCHEMA_VERSION,
        "modelVersion": CAUSAL_MODEL_VERSION,
        "status": "available" if prop in PROP_CHAINS else "partial",
        "statProductionChain": chain,
        "identity": {
            "fixtureId": prediction.get("fixtureId") or request.get("fixture_id"),
            "playerId": prediction.get("playerId") or request.get("player_id"),
            "playerName": prediction.get("playerName"),
            "teamName": prediction.get("teamName"),
            "opponentName": opponent,
            "venue": venue,
            "role": role or "UNKNOWN",
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
        },
        "opponentRoleCohort": cohort,
        "scoreStateBranches": _branches(prop, role_bucket, script["classification"]),
        "recommendationGate": {
            "decision": decision,
            "reason": reason,
            "rpRecommendation": recommendation or "UNKNOWN",
            "wouldRecommendation": recommendation if decision not in {"PASS", "REJECT"} else "PASS",
            "productionInfluence": "shadow_only",
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
        })
        results.append({
            "case": name,
            "decision": packet["recommendationGate"]["decision"],
            "pregameOnly": True,
            "resultDataUsed": False,
        })
    return results