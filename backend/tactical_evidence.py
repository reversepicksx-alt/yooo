"""Small, deterministic helpers for auditable tactical evidence.

These helpers intentionally do not change a projection.  They turn provider
observations into compact evidence objects that can be persisted with a pick
and rendered without asking an LLM to invent tactical facts.
"""

from __future__ import annotations

from typing import Any


_POSITION_ALIASES = {
    "G": "GK",
    "GOALKEEPER": "GK",
    "D": "DEF",
    "DEFENDER": "DEF",
    "CENTREBACK": "CB",
    "CENTERBACK": "CB",
    "LEFTBACK": "LB",
    "RIGHTBACK": "RB",
    "LEFTWINGBACK": "LWB",
    "RIGHTWINGBACK": "RWB",
    "M": "MID",
    "MIDFIELDER": "MID",
    "DEFENSIVEMIDFIELDER": "CDM",
    "CENTRALMIDFIELDER": "CM",
    "ATTACKINGMIDFIELDER": "CAM",
    "LEFTMIDFIELDER": "LM",
    "RIGHTMIDFIELDER": "RM",
    "F": "FWD",
    "FORWARD": "FWD",
    "ATTACKER": "FWD",
    "LEFTWINGER": "LW",
    "RIGHTWINGER": "RW",
    "CENTREFORWARD": "CF",
    "CENTERFORWARD": "CF",
    "STRIKER": "ST",
    "SECONDSTRIKER": "SS",
}

_WIDE_POSITIONS = {"LW", "RW", "LM", "RM", "LWB", "RWB"}
_CREATIVE_PROPS = {"pass_attempts", "passes", "key_passes", "shots_assisted", "dribbles"}
_EXACT_POSITIONS = {
    "GK", "CB", "LB", "RB", "LWB", "RWB", "CDM", "CM", "CAM",
    "LM", "RM", "LW", "RW", "CF", "ST", "SS",
}

_TRUSTED_SELECTION_ROLE_SOURCES = {
    "gemini_web_grounded",
    "cache",
    "manual_override",
    "api_sports_lineup_history",
}


def preserve_selection_role(
    selection: dict[str, Any] | None,
    observed: dict[str, Any] | None,
    lineup_status: Any,
) -> dict[str, Any] | None:
    """Keep a trusted selection-time identity over inferred lineup output.

    A confirmed exact current lineup is handled by the caller before this
    boundary and intentionally wins. A confirmed broad category is still
    incomplete evidence. Predicted grids, generic provider categories, and
    stat fingerprints do not have enough certainty to turn a grounded
    winger/midfielder into a different customer-facing role.
    """
    selection = selection or {}
    observed = observed or {}
    position = str(selection.get("position") or "").strip()
    source = str(selection.get("source") or "").strip()
    observed_position = str(observed.get("position") or "").strip().upper()
    confirmed_exact_observation = (
        str(lineup_status or "").strip().lower() == "confirmed"
        and observed_position in _EXACT_POSITIONS
    )
    if not position or source not in _TRUSTED_SELECTION_ROLE_SOURCES or confirmed_exact_observation:
        return None
    evidence = list(selection.get("evidence") or [])
    if observed.get("position") and observed.get("position") != position:
        evidence.append(
            f"non-confirmed lineup reported {observed['position']}; "
            "selection-time verified identity retained"
        )
    return {
        "position": position,
        "role": str(selection.get("role") or "").strip() or None,
        "source": source,
        "confidence": selection.get("confidence") or "medium",
        "evidence": evidence,
    }


def infer_grid_position(
    grid: Any,
    formation: Any,
    provider_position: Any = None,
) -> str:
    """Infer a conservative exact position from a lineup grid.

    API-Football's ``grid`` is ``row:column`` from the team's defensive end.
    The grid is more informative than the broad D/M/F category, but exact
    positions are returned only when the formation makes the tactical band
    unambiguous.
    """
    raw = str(grid or "").strip()
    try:
        row, column = (int(part) for part in raw.split(":", 1))
    except (TypeError, ValueError):
        return normalize_observed_position(provider_position)

    shape = [int(part) for part in str(formation or "").split("-") if part.isdigit()]
    if row == 1:
        return "GK"
    if not shape:
        return normalize_observed_position(provider_position)

    defenders = shape[0]
    if row == 2:
        if defenders == 4 and column in {1, 2, 3, 4}:
            return {1: "LB", 2: "CB", 3: "CB", 4: "RB"}[column]
        if defenders == 3 and column in {1, 2, 3}:
            return "CB"
        if defenders == 5 and column in {1, 2, 3, 4, 5}:
            return {
                1: "LWB", 2: "CB", 3: "CB", 4: "CB", 5: "RWB",
            }[column]

    # API-Football uses the same row/column grid for midfielders. These
    # mappings are intentionally limited to formations where the row's
    # tactical band is unambiguous; otherwise retain M/MID rather than
    # manufacturing CM/CDM/CAM evidence.
    provider_category = normalize_observed_position(provider_position)
    if provider_category in {"M", "MID"} and row == 3:
        if shape[:3] == [3, 1, 4] and column == 1:
            return "CDM"
        if shape[:3] == [4, 3, 3] and column in {1, 2, 3}:
            return "CM"
        if shape[:3] == [4, 3, 1] and column in {1, 2, 3}:
            return "CM"
        if shape[:3] == [4, 2, 3] and column in {1, 2}:
            return "CDM"
        if shape[:3] == [4, 1, 4] and column == 1:
            return "CDM"
        if shape[:3] == [4, 4, 2] and column in {2, 3}:
            return "CM"

    if provider_category in {"M", "MID"} and row == 4:
        if shape[:4] == [3, 1, 4, 2] and column in {1, 2, 3, 4}:
            return {1: "LM", 2: "CM", 3: "CM", 4: "RM"}[column]
        if shape[:4] == [4, 2, 3, 1] and column in {1, 2, 3}:
            # The three players on row four are the left attacking midfielder,
            # central attacking midfielder, and right attacking midfielder.
            # Treating every column as CAM incorrectly turns wide wingers such
            # as Doku into central players.
            return {1: "LW", 2: "CAM", 3: "RW"}[column]
        if shape[:4] == [4, 1, 4, 1] and column in {1, 2, 3, 4}:
            return "CM"
        if shape[:4] == [4, 3, 1, 2] and column == 1:
            return "CAM"

    # Forward rows in common formations are exact enough to distinguish the
    # central striker from wide forwards. API-Football still reports these as
    # F/FWD in fixture player statistics, so use the formation/grid pair.
    if provider_category in {"F", "FWD"}:
        if row == 5 and shape in ([4, 2, 3, 1], [4, 3, 3, 1], [3, 4, 3, 1]):
            return "ST"
        if row == 4 and shape[:4] == [4, 2, 3, 1] and column in {1, 2, 3}:
            return {1: "LW", 2: "CAM", 3: "RW"}[column]
        if row == 4 and shape[:3] == [4, 3, 3] and column in {1, 2, 3}:
            return {1: "LW", 2: "ST", 3: "RW"}[column]
        if row == 4 and shape[:3] == [3, 4, 3] and column in {1, 2, 3}:
            return {1: "LW", 2: "ST", 3: "RW"}[column]
        if row == 4 and shape[:3] == [4, 4, 2] and column in {1, 2}:
            return "ST"

    return normalize_observed_position(provider_position)


def _number(value: Any) -> float | None:
    try:
        if value is None or str(value).strip() == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _per_game(stats: dict[str, Any] | None, key: str) -> float:
    stats = stats or {}
    appearances = max(1.0, _number(stats.get("appearances")) or 1.0)
    return (_number(stats.get(key)) or 0.0) / appearances


def normalize_observed_position(value: Any) -> str:
    """Normalize API-Football lineup/stat positions without guessing a side."""
    raw = str(value or "").strip().upper().replace(" ", "")
    return _POSITION_ALIASES.get(raw, raw)


def exact_position_from_lineup_payload(payload: Any, player_id: Any) -> str | None:
    """Extract one player's exact position from API-Football lineup grids.

    The player-stat endpoint often returns only F/M/D. A lineup grid plus the
    team's formation contains the exact tactical band needed for CF/ST/LW/RW
    and the other comparison positions. This helper is deliberately
    fixture-scoped so it cannot merge same-name players across clubs.
    """
    try:
        target_id = str(int(player_id))
    except (TypeError, ValueError):
        target_id = str(player_id or "").strip()
    if not target_id:
        return None
    teams = payload if isinstance(payload, list) else []
    for team in teams:
        if not isinstance(team, dict):
            continue
        formation = team.get("formation") or ""
        for row in team.get("startXI") or []:
            player = row.get("player") or {}
            try:
                row_id = str(int(player.get("id")))
            except (TypeError, ValueError):
                row_id = str(player.get("id") or "").strip()
            if row_id != target_id:
                continue
            position = infer_grid_position(
                player.get("grid"),
                formation,
                player.get("pos"),
            )
            if position in _EXACT_POSITIONS:
                return position
    return None


def resolve_observed_role(
    position: Any,
    stats: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve a conservative role from an observed lineup position.

    A generic ``ST``/``F`` label alone is never enough to call somebody a
    pressing forward.  Wide creators are explicitly protected from that
    fallback, which is important for players such as Messi.
    """
    observed = normalize_observed_position(position)
    stats = stats or {}
    dribbles = _per_game(stats, "dribbles_attempts")
    key_passes = _per_game(stats, "key_passes")
    shots = _per_game(stats, "shots_total")
    passes = _per_game(stats, "passes_total")
    tackles = _per_game(stats, "tackles_total")

    role = None
    evidence: list[str] = []
    if observed in _WIDE_POSITIONS:
        if dribbles >= 2.0 and key_passes >= 1.5:
            role = "Inverted Winger"
            evidence = [
                f"observed {observed} lineup position",
                f"{dribbles:.1f} dribbles/game",
                f"{key_passes:.1f} key passes/game",
                "creative wide-output fingerprint",
            ]
        elif key_passes >= 2.0:
            role = "Wide Playmaker"
            evidence = [f"observed {observed} lineup position", f"{key_passes:.1f} key passes/game"]
        else:
            role = "Traditional Winger"
            evidence = [f"observed {observed} lineup position"]
    elif observed == "CAM":
        role = "Advanced Playmaker" if key_passes >= 1.5 else "Shadow Striker"
        evidence = [f"observed {observed} lineup position"]
    elif observed == "FWD":
        # FWD/F is a provider category, not an exact position. A generic label
        # alone cannot distinguish CF/ST/SS. However, a strong multi-match stat
        # fingerprint — high key passes and dribbles — is sufficient to infer a
        # creative attacking profile even without an exact lineup position.
        if key_passes >= 2.0 and dribbles >= 2.0 and shots < 2.5:
            role = "False 9"
            evidence = [
                "generic forward category",
                "creator-over-finisher fingerprint",
                f"{key_passes:.1f} key passes/game",
                f"{dribbles:.1f} dribbles/game",
            ]
        elif key_passes >= 3.0 and dribbles >= 3.0:
            role = "Creative Forward"
            evidence = [
                "generic forward category",
                "creative link-play and carry fingerprint",
                f"{key_passes:.1f} key passes/game",
                f"{dribbles:.1f} dribbles/game",
            ]
        else:
            role = None
            evidence = [
                "generic forward category only",
                "exact wide/central/striker position not independently verified",
            ]
    elif observed in {"CF", "SS"}:
        if key_passes >= 2.0 and dribbles >= 2.0 and shots < 2.5:
            role = "False 9"
            evidence = [f"observed {observed} lineup position", "creator-over-finisher fingerprint"]
        else:
            role = "Complete Forward"
            evidence = [f"observed {observed} lineup position"]
    elif observed == "ST":
        if shots >= 2.5 and dribbles < 1.5:
            role = "Poacher"
            evidence = [f"observed {observed} lineup position", "high-shot, low-carry fingerprint"]
        else:
            role = "Complete Forward"
            evidence = [f"observed {observed} lineup position", "pressing role not independently verified"]
    elif observed == "CM":
        # CM is an exact observed position, but the lineup category alone
        # does not prove a tactical role. A role is only an inference when a
        # multi-match output fingerprint supports it.
        if shots >= 1.5 and dribbles >= 1.5:
            role = "Box-to-Box"
            evidence = [
                f"observed {observed} lineup position",
                f"{shots:.1f} shots/game",
                f"{dribbles:.1f} dribbles/game",
                "role inferred from multi-match output fingerprint",
            ]
        elif passes >= 50 and tackles < 4.0:
            role = "Deep-Lying Playmaker"
            evidence = [
                f"observed {observed} lineup position",
                f"{passes:.1f} passes/game",
                "role inferred from pass-volume fingerprint",
            ]
        elif tackles >= 4.5:
            role = "Ball Winner"
            evidence = [
                f"observed {observed} lineup position",
                f"{tackles:.1f} tackles/game",
                "role inferred from defensive-output fingerprint",
            ]
        else:
            evidence = [
                f"observed {observed} lineup position",
                "exact tactical role unavailable",
            ]
    elif observed == "MID":
        # Generic M/MID is category evidence only. Never turn it into a
        # Box-to-Box or other exact midfield role.
        role = None
        evidence = [
            "observed generic midfielder lineup category",
            "exact CM/CDM/CAM position and tactical role not independently verified",
        ]
    elif observed in {"CDM", "DM"}:
        if passes >= 50 and tackles < 4.5:
            role = "Deep-Lying Playmaker"
            evidence = [
                f"observed {observed} lineup position",
                f"{passes:.1f} passes/game",
                "role inferred from pass-volume fingerprint",
            ]
        elif tackles >= 4.5:
            role = "Ball Winner"
            evidence = [
                f"observed {observed} lineup position",
                f"{tackles:.1f} tackles/game",
                "role inferred from defensive-output fingerprint",
            ]
        else:
            evidence = [
                f"observed {observed} lineup position",
                "exact tactical role unavailable",
            ]
    elif observed == "CB":
        role = "Ball-Playing CB" if passes >= 50 else "Stopper"
        evidence = [f"observed {observed} lineup position"]
    elif observed in {"LB", "RB", "LWB", "RWB"}:
        role = "Fullback" if observed in {"LB", "RB"} else "Wing-Back"
        evidence = [f"observed {observed} lineup position"]
    elif observed == "DEF":
        # API-Football's broad D/DEF label does not identify centre-back,
        # fullback, or wing-back.  Do not let aggregate stats manufacture an
        # exact side-specific role from a generic fixture observation.
        role = None
        evidence = [
            "observed generic defender lineup category",
            "exact CB/LB/RB role not independently verified",
        ]
    elif observed == "GK":
        role = "Shot-Stopper"
        evidence = ["observed goalkeeper lineup position"]

    return {
        "position": observed or None,
        "role": role,
        "source": (
            "fixture_lineup_role_inferred"
            if observed and role
            else "fixture_lineup_position_observation"
            if observed in _EXACT_POSITIONS
            else "fixture_lineup_category"
            if observed in {"DEF", "MID", "FWD"}
            else "unavailable"
        ),
        "confidence": (
            "high"
            if observed and role and (
                observed in _EXACT_POSITIONS
                or observed in {"FWD", "MID", "DEF"}
            )
            else "low"
        ),
        "roleIsInferred": bool(role),
        "evidence": evidence,
    }


def summarize_observed_positions(observations: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Return a dominant observed position and transparent sample metadata."""
    counts: dict[str, int] = {}
    for item in observations or []:
        pos = normalize_observed_position((item or {}).get("position"))
        if pos:
            counts[pos] = counts.get(pos, 0) + 1
    total = sum(counts.values())
    dominant = max(counts, key=counts.get) if counts else None
    return {
        "sampleSize": total,
        "positionCounts": counts,
        "dominantPosition": dominant,
        "status": "observed" if total else "unavailable",
    }


def summarize_player_opponent_history(
    values: list[Any] | None,
    line: Any,
) -> dict[str, Any]:
    """Summarize valid player appearances against the exact opponent."""
    numeric = [v for v in (_number(value) for value in values or []) if v is not None]
    threshold = _number(line)
    over_hits = sum(1 for value in numeric if threshold is not None and value > threshold)
    under_hits = sum(1 for value in numeric if threshold is not None and value < threshold)
    return {
        "sampleSize": len(numeric),
        "average": round(sum(numeric) / len(numeric), 2) if numeric else None,
        "overHits": over_hits,
        "underHits": under_hits,
        "overHitRate": round(over_hits / len(numeric) * 100) if numeric else None,
        "underHitRate": round(under_hits / len(numeric) * 100) if numeric else None,
        "evidenceStatus": "thin" if 0 < len(numeric) < 4 else "usable" if numeric else "unavailable",
    }


def summarize_position_cohort(
    players: list[dict[str, Any]] | None,
    line: Any,
    minimum_sample: int = 15,
) -> dict[str, Any]:
    """Aggregate a same-position opponent cohort without padding its sample.

    The weight is scoped to this evidence packet. It rewards a meaningful
    number of minutes and repeat verified appearances, while the square-root
    cap prevents one repeatedly observed player from dominating the cohort.
    It must not be reused as a projection or calibration weight.
    """
    rows = [
        row for row in (players or [])
        if _number((row or {}).get("statValue")) is not None
        and (_number((row or {}).get("minutes")) or 0) > 0
    ]
    values = [_number(row.get("statValue")) for row in rows]
    values = [value for value in values if value is not None]
    weights = [
        max(
            0.25,
            _number(row.get("evidenceWeight"))
            or min(1.0, max(30.0, _number(row.get("minutes")) or 0.0) / 90.0),
        )
        for row in rows
    ]
    weight_total = sum(weights)
    weighted_average = (
        sum(value * weight for value, weight in zip(values, weights)) / weight_total
        if values and weight_total > 0
        else None
    )
    threshold = _number(line)
    over_hits = sum(
        weight for value, weight in zip(values, weights)
        if threshold is not None and value > threshold
    )
    under_hits = sum(
        weight for value, weight in zip(values, weights)
        if threshold is not None and value < threshold
    )
    size = len(values)
    effective_sample_size = (
        round((weight_total * weight_total) / sum(weight * weight for weight in weights), 2)
        if weights and sum(weight * weight for weight in weights) > 0
        else 0
    )
    return {
        "sampleSize": size,
        "minimumRecommendedSample": minimum_sample,
        "sampleStatus": (
            "sufficient" if size >= minimum_sample
            else "limited" if size
            else "unavailable"
        ),
        "average": round(weighted_average, 2) if weighted_average is not None else None,
        "unweightedAverage": round(sum(values) / size, 2) if values else None,
        "overHits": round(over_hits, 2),
        "underHits": round(under_hits, 2),
        "overHitRate": round(over_hits / weight_total * 100) if values and weight_total else None,
        "underHitRate": round(under_hits / weight_total * 100) if values and weight_total else None,
        "effectiveSampleSize": effective_sample_size,
        "weightMethod": "minutes_and_repeat_appearance_evidence_only",
        "sampleUnit": "team",
        "position": next((row.get("position") for row in rows if row.get("position")), None),
        "venue": next((row.get("venue") for row in rows if row.get("venue")), None),
    }


_COHORT_PROP_LABELS = {
    "pass_attempts": "pass attempts",
    "passes": "passes",
    "shots": "shots",
    "shots_on_target": "shots on target",
    "goals": "goals",
    "assists": "assists",
    "shots_assisted": "shot assists",
    "key_passes": "key passes",
    "tackles": "tackles",
    "saves": "saves",
    "goalie_saves": "saves",
    "interceptions": "interceptions",
    "blocks": "blocks",
    "dribbles": "dribbles",
    "dribbles_success": "successful dribbles",
    "fouls_drawn": "fouls drawn",
    "fouls_committed": "fouls committed",
    "crosses": "crosses",
    "clearances": "clearances",
    "duels_won": "duels won",
    "yellow_cards": "yellow cards",
}

_COHORT_POSITION_LABELS = {
    "GK": "goalkeepers",
    "G": "goalkeepers",
    "GOALKEEPER": "goalkeepers",
    "CB": "centre-backs",
    "LCB": "centre-backs",
    "RCB": "centre-backs",
    "LB": "left-backs",
    "LWB": "left wing-backs",
    "RB": "right-backs",
    "RWB": "right wing-backs",
    "D": "defenders",
    "DEF": "defenders",
    "CDM": "defensive midfielders",
    "DM": "defensive midfielders",
    "CM": "central midfielders",
    "M": "midfielders",
    "MID": "midfielders",
    "CAM": "attacking midfielders",
    "AM": "attacking midfielders",
    "LM": "left midfielders",
    "RM": "right midfielders",
    "LW": "left wingers",
    "RW": "right wingers",
    "WING": "wide attackers",
    "CF": "forwards",
    "ST": "strikers",
    "SS": "second strikers",
    "F": "forwards",
    "FWD": "forwards",
}


def position_cohort_stat_label(prop_type: Any) -> str:
    """Return the customer-facing name for a player event count."""
    raw = str(prop_type or "prop").strip().lower()
    return _COHORT_PROP_LABELS.get(raw, raw.replace("_", " "))


def position_cohort_subject(position: Any) -> str:
    """Return the observed position as a plural cohort subject."""
    raw = str(position or "").strip().upper().replace(" ", "")
    if raw in _COHORT_POSITION_LABELS:
        return _COHORT_POSITION_LABELS[raw]
    readable = str(position or "same-role players").strip().lower()
    return readable if readable.endswith("s") else f"{readable}s"


def build_position_cohort_statement(
    *,
    opponent: Any,
    prop_type: Any,
    position: Any,
    average: Any,
    sample_size: Any,
    venue: Any = None,
) -> str | None:
    """Describe a player cohort without claiming the opponent directly records it.

    The comparison is an observed sample of players who played this stat against
    the opponent. It is not automatically a team-level "allowed" metric.
    """
    try:
        avg = float(average)
        count = int(sample_size)
    except (TypeError, ValueError):
        return None
    if count <= 0:
        return None
    opponent_text = str(opponent or "the opponent")
    venue_text = str(venue or "").strip().lower()
    venue_phrase = (
        f" in matching {venue_text} fixtures"
        if venue_text in {"home", "away"}
        else ""
    )
    return (
        f"{opponent_text} matchup sample: "
        f"{position_cohort_subject(position)} averaged "
        f"{avg:.1f} {position_cohort_stat_label(prop_type)}"
        f"{venue_phrase} (n={count})."
    )


def position_cohort_verdict(
    cohort: dict[str, Any] | None,
    recommendation: Any,
    line: Any,
) -> dict[str, Any]:
    """Compare exact-role opponent evidence with the selected direction.

    This is descriptive evidence only. It never changes the projection or
    recommendation, and unavailable evidence is not represented as a zero.
    """
    cohort = cohort or {}
    sample_size = int(cohort.get("sampleSize") or 0)
    average = _number(cohort.get("average") or cohort.get("avgStatValue"))
    threshold = _number(line)
    direction = str(recommendation or "").strip().lower()
    if sample_size <= 0 or average is None or threshold is None:
        verdict = "unavailable"
        reason = "No valid same-role opponent sample is available."
    elif direction == "over" and average > threshold:
        verdict = "verifies"
        reason = "The same-role opponent average is above the saved line."
    elif direction == "under" and average < threshold:
        verdict = "verifies"
        reason = "The same-role opponent average is below the saved line."
    elif direction in {"over", "under"}:
        verdict = "contradicts"
        reason = "The same-role opponent average points to the opposite side of the saved line."
    else:
        verdict = "neutral"
        reason = "No OVER/UNDER direction was selected for this evidence."
    return {
        "verdict": verdict,
        "reason": reason,
        "average": average,
        "line": threshold,
        "sampleSize": sample_size,
        "recommendation": direction.upper() if direction else None,
    }


def build_tactical_conclusion(
    *,
    player_name: str,
    role: str | None,
    prop_type: str,
    opponent: str,
    player_history: dict[str, Any],
    cohort: dict[str, Any],
) -> str:
    """Build a concise human-readable conclusion from already verified facts."""
    role_text = role or "an unverified role"
    prop_text = prop_type.replace("_", " ")
    conclusion = f"{player_name} is treated as {role_text}; the {prop_text} pathway is role-specific, not a generic forward assumption."
    if player_history.get("sampleSize"):
        conclusion += (
            f" Against {opponent}, the player has {player_history['sampleSize']} "
            f"verified appearance{'' if player_history['sampleSize'] == 1 else 's'}"
        )
        if player_history.get("average") is not None:
            conclusion += f" averaging {player_history['average']}"
        conclusion += "."
    else:
        conclusion += f" There is no verified player-level history against {opponent} in the searched fixtures."
    if cohort.get("sampleSize"):
        conclusion += (
            f" The same-position opponent cohort is n={cohort['sampleSize']}"
            f" with a {cohort.get('average')} average"
        )
        if cohort.get("sampleStatus") != "sufficient":
            conclusion += " (limited sample)"
        conclusion += "."
    else:
        conclusion += " No valid same-position opponent cohort was available."
    return conclusion