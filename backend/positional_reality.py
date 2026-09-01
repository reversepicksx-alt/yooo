"""Deterministic positional-reality signals for soccer prop analysis.

This module is deliberately provider-shape tolerant and projection-neutral.
Lineup coordinates are normalized into the player's attacking direction, then
combined with the resolved position/role and the already-classified match
script. The returned multiplier is a *shadow* value for audit and replay; it
must not be applied to the live Bayesian projection until out-of-sample
validation promotes it.
"""

from __future__ import annotations

from statistics import median
from typing import Any


PASS_PROPS = {"pass_attempts", "passes", "key_passes", "crosses"}
ATTACK_PROPS = {"shots", "shots_on_target", "goals", "assists", "shots_assisted"}
DEFENSIVE_PROPS = {
    "tackles", "interceptions", "clearances", "blocks",
    "fouls_committed", "duels_won",
}
GK_PROPS = {"saves", "goalie_saves"}
CARRY_PROPS = {"dribbles"}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _num(value: Any) -> float | None:
    try:
        if value is None or _text(value) == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _group(position: str, role: str) -> str:
    pos = _text(position).upper().replace(" ", "")
    role_low = _text(role).lower()
    if pos in {"GK", "G", "GOALKEEPER"} or "keeper" in role_low:
        return "goalkeeper"
    if pos in {"CB", "LCB", "RCB", "SW", "LIB", "DEF", "LB", "RB", "LWB", "RWB", "FB"}:
        return "defender"
    if pos in {"DM", "CDM", "CM", "LCM", "RCM", "MID", "AM", "CAM", "LM", "RM"}:
        return "midfielder"
    if pos in {"LW", "RW", "WING", "WF"} or "wing" in role_low or "wide" in role_low:
        return "wide_attacker"
    if pos in {"ST", "CF", "SS", "FWD", "FW"} or "forward" in role_low or "striker" in role_low:
        return "forward"
    return "unknown"


def _zone_from_position(position: str, role: str, group: str) -> tuple[str, str]:
    pos = _text(position).upper().replace(" ", "")
    role_low = _text(role).lower()
    if group == "goalkeeper":
        return "own_third_central", "position_role_inference"
    if pos in {"CB", "LCB", "RCB", "SW", "LIB", "DEF"} or "centre-back" in role_low:
        return "own_third_central", "position_role_inference"
    if pos in {"LB", "LWB"} or "left back" in role_low or "left wing-back" in role_low:
        return "own_third_left", "position_role_inference"
    if pos in {"RB", "RWB"} or "right back" in role_low or "right wing-back" in role_low:
        return "own_third_right", "position_role_inference"
    if pos in {"DM", "CDM"} or "deep" in role_low or "regista" in role_low:
        return "middle_third_central", "position_role_inference"
    if pos in {"CM", "LCM", "RCM", "MID"} or "box-to-box" in role_low or "mezzala" in role_low:
        return "middle_third_central", "position_role_inference"
    if pos in {"AM", "CAM"} or "creator" in role_low or "playmaker" in role_low:
        return "attacking_third_central", "position_role_inference"
    if pos in {"LW", "LM"}:
        return "attacking_third_left", "position_role_inference"
    if pos in {"RW", "RM"}:
        return "attacking_third_right", "position_role_inference"
    if group == "wide_attacker":
        return "attacking_third_wide", "position_role_inference"
    if group == "forward":
        return "attacking_third_central", "position_role_inference"
    return "zone_unavailable", "unavailable"


def _coordinate_zone(x: float, attacking_y: float) -> str:
    vertical = "own_third" if attacking_y < 0.34 else (
        "middle_third" if attacking_y < 0.67 else "attacking_third"
    )
    horizontal = "left" if x < 0.34 else "right" if x > 0.66 else "central"
    return f"{vertical}_{horizontal}"


def _script_bucket(match_script: dict[str, Any]) -> str:
    if not isinstance(match_script, dict):
        return "unknown"
    value = _text(match_script.get("classification") or match_script.get("primaryScript")).lower()
    if any(token in value for token in ("high_event", "high scoring", "open", "dominant")):
        return "open_or_controlled"
    if any(token in value for token in ("low_event", "low scoring", "low-scoring")):
        return "low_event"
    if any(token in value for token in ("counter", "underdog", "defensive", "opponent_dominance")):
        return "counter_defensive"
    if any(token in value for token in ("control", "favorite", "possession", "controlled_dominance")):
        return "settled_control"
    return "balanced"


def _prop_signal(
    prop_type: str,
    role_group: str,
    zone: str,
    script_bucket: str,
) -> dict[str, Any]:
    prop = _text(prop_type).lower()
    attacking_zone = zone.startswith("attacking_third")
    own_zone = zone.startswith("own_third")
    direction = "neutral"
    strength = 0.0
    rationale = "No bounded zone-to-prop direction is supported by the available inputs."

    if prop in PASS_PROPS:
        if script_bucket == "settled_control" or (attacking_zone and role_group in {"midfielder", "wide_attacker"}):
            direction, strength = "higher_volume", 0.58
            rationale = "Settled possession and the player's circulation zone create more repeatable passing sequences."
        elif script_bucket == "counter_defensive" or own_zone:
            direction, strength = "lower_volume", 0.42
            rationale = "A deeper or reactive script can replace short settled circulation with longer, lower-frequency actions."
    elif prop in ATTACK_PROPS or prop in CARRY_PROPS:
        if script_bucket == "open_or_controlled" and (attacking_zone or role_group in {"forward", "wide_attacker"}):
            direction, strength = "higher_volume", 0.62
            rationale = "The player's attacking zone and an open/control script support more final-third attempts or carries."
        elif script_bucket == "low_event":
            direction, strength = "lower_volume", 0.48
            rationale = "A low-event script reduces the number of attacking sequences available to finish or carry."
        elif script_bucket == "counter_defensive" and role_group in {"forward", "wide_attacker"}:
            direction, strength = "neutral", 0.24
            rationale = "A reactive side may create counter opportunities, but their timing is too variable for a directional claim."
    elif prop in DEFENSIVE_PROPS or prop in GK_PROPS:
        if script_bucket == "counter_defensive" or own_zone:
            direction, strength = "higher_volume", 0.64
            rationale = "Reactive possession and a defensive zone increase exposure to opposition attacks."
        elif script_bucket == "settled_control" and role_group != "goalkeeper":
            direction, strength = "lower_volume", 0.40
            rationale = "Team control can reduce the defensive workload reaching an outfield player's zone."

    return {
        "propType": prop_type,
        "shadowDirection": direction,
        "shadowStrength": round(strength, 2),
        "shadowMultiplier": round(1.0 + (strength * 0.06 if direction == "higher_volume" else -strength * 0.06 if direction == "lower_volume" else 0.0), 3),
        "rationale": rationale,
        "activationStatus": "shadow_only_until_calibrated",
    }


def _robust_history(values: list[Any] | None) -> dict[str, Any]:
    clean = [v for v in (_num(item) for item in (values or [])) if v is not None]
    if not clean:
        return {
            "status": "unavailable", "sampleSize": 0, "median": None,
            "weightedMean": None, "outlierCount": 0, "method": "median_absolute_deviation",
        }
    med = median(clean)
    deviations = [abs(v - med) for v in clean]
    mad = median(deviations)
    # MAD=0 is common for count props. In that case only exact deviations
    # greater than one count are down-weighted; no observation is deleted.
    cutoff = max(2.5 * mad, 1.0)
    weights = [1.0 if abs(v - med) <= cutoff else cutoff / abs(v - med) for v in clean]
    weighted_mean = sum(v * w for v, w in zip(clean, weights)) / sum(weights)
    outliers = sum(1 for v in clean if abs(v - med) > cutoff)
    return {
        "status": "applied",
        "sampleSize": len(clean),
        "median": round(med, 2),
        "mad": round(mad, 2),
        "weightedMean": round(weighted_mean, 2),
        "outlierCount": outliers,
        "outlierRate": round(outliers / len(clean), 3),
        "method": "median_absolute_deviation",
        "policy": "outliers are down-weighted, never blanket-deleted",
    }


def build_positional_reality(
    *,
    player: dict[str, Any] | None,
    position: str | None,
    role: str | None,
    prop_type: str,
    is_home: bool,
    match_script: dict[str, Any] | None,
    history_values: list[Any] | None = None,
) -> dict[str, Any]:
    player = player if isinstance(player, dict) else {}
    target_position = _text(position) or _text(player.get("pos") or player.get("position"))
    target_role = _text(role) or _text(player.get("role"))
    group = _group(target_position, target_role)
    x = _num(player.get("x"))
    y = _num(player.get("y"))
    limitations: list[str] = []
    normalized_position = target_position.upper().replace(" ", "")
    broad_category_only = (
        normalized_position in {"D", "DEF", "DEFENDER", "M", "MID", "MIDFIELDER", "F", "FW", "FWD", "FORWARD", "ATTACKER"}
        and not target_role
    )
    has_provider_coordinates = (
        x is not None and y is not None and 0 <= x <= 1 and 0 <= y <= 1
    )

    # A provider-level D/M/F category cannot locate a player centrally, on a
    # flank, or in an attacking/defensive third. Previously DEF defaulted to
    # own_third_central, which made unrelated generic defenders produce the
    # same "lower volume" positional card. Preserve only genuinely observed
    # provider coordinates; otherwise report positional evidence unavailable.
    if broad_category_only and not has_provider_coordinates:
        return {
            "version": "positional-reality-shadow-v1",
            "status": "unavailable",
            "zone": "zone_unavailable",
            "zoneSource": "unavailable",
            "zoneConfidence": 0.0,
            "coordinates": {"x": None, "y": None, "attackingDirectionY": None},
            "roleGroup": group,
            "role": None,
            "scriptBucket": _script_bucket(
                match_script if isinstance(match_script, dict) else {}
            ),
            "roleMechanism": None,
            "propSignal": {
                "propType": prop_type,
                "shadowDirection": "neutral",
                "shadowStrength": 0.0,
                "shadowMultiplier": 1.0,
                "rationale": "Exact position or provider coordinates are required for a positional direction.",
                "activationStatus": "unavailable",
            },
            "playerStyle": {
                "profile": group,
                "evidence": "broad_provider_category_only",
                "sampleSize": 0,
            },
            "robustEvidence": {
                "status": "unavailable",
                "sampleSize": 0,
                "median": None,
                "weightedMean": None,
                "outlierCount": 0,
                "method": "median_absolute_deviation",
            },
            "limitations": [
                "provider supplied only a broad position category",
                "no exact position or provider coordinates were verified",
            ],
        }

    if has_provider_coordinates:
        attacking_y = y if is_home else 1.0 - y
        zone = _coordinate_zone(x, attacking_y)
        zone_source = "lineup_provider_coordinates"
        zone_confidence = 0.88
        coordinates = {"x": round(x, 3), "y": round(y, 3), "attackingDirectionY": round(attacking_y, 3)}
    else:
        zone, zone_source = _zone_from_position(target_position, target_role, group)
        zone_confidence = 0.58 if zone != "zone_unavailable" else 0.0
        coordinates = {"x": None, "y": None, "attackingDirectionY": None}
        limitations.append("provider average-position coordinates unavailable; zone is inferred from position and role")

    script = match_script if isinstance(match_script, dict) else {}
    script_bucket = _script_bucket(script)
    signal = _prop_signal(prop_type, group, zone, script_bucket)
    if script_bucket == "unknown":
        limitations.append("match script could not be classified from the available fixture evidence")
    if group == "unknown":
        limitations.append("player role group unavailable")
    limitations.append("nominal lineup coordinates are not a verified in-possession heat map")

    return {
        "version": "positional-reality-shadow-v1",
        "zone": zone,
        "zoneSource": zone_source,
        "zoneConfidence": zone_confidence,
        "coordinates": coordinates,
        "roleGroup": group,
        "role": target_role or None,
        "scriptBucket": script_bucket,
        "roleMechanism": signal["rationale"],
        "propSignal": signal,
        "playerStyle": {
            "profile": group,
            "evidence": "resolved_position_and_role" if group != "unknown" else "unavailable",
            "sampleSize": len([v for v in (history_values or []) if _num(v) is not None]),
        },
        "robustEvidence": _robust_history(history_values),
        "limitations": limitations,
        "mode": "shadow",
        "activationStatus": "display_only_until_leakage_safe_replay",
    }