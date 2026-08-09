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


def infer_grid_position(
    grid: Any,
    formation: Any,
    provider_position: Any = None,
) -> str:
    """Infer a conservative exact position from API-Football's lineup grid.

    API-Football's ``grid`` is ``row:column`` from the team's defensive end.
    The grid is more informative than the broad D/M/F category, but we only
    infer a side when the formation gives us an unambiguous back line.
    """
    raw = str(grid or "").strip()
    try:
        row, column = (int(part) for part in raw.split(":", 1))
    except (TypeError, ValueError):
        return normalize_observed_position(provider_position)

    shape = [int(part) for part in str(formation or "").split("-") if part.isdigit()]
    if row == 1:
        return "GK"
    if not shape or row != 2:
        return normalize_observed_position(provider_position)

    defenders = shape[0]
    if defenders == 4 and column in {1, 2, 3, 4}:
        return {1: "LB", 2: "CB", 3: "CB", 4: "RB"}[column]
    if defenders == 3 and column in {1, 2, 3}:
        return "CB"
    if defenders == 5 and column in {1, 2, 3, 4, 5}:
        return {
            1: "LWB", 2: "CB", 3: "CB", 4: "CB", 5: "RWB",
        }[column]

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
        # API-Football often reports historical attacker rows as generic F.
        # Apply the same creator-over-finisher fingerprint here instead of
        # allowing a stale cached "Pressing Forward" label to win.
        if key_passes >= 2.0 and dribbles >= 2.0 and shots < 2.5:
            role = "False 9"
            evidence = ["observed generic forward position", "creator-over-finisher fingerprint"]
        elif passes >= 35 and key_passes >= 2.5 and dribbles >= 3.0:
            role = "Creative Forward"
            evidence = [
                "observed generic forward position",
                f"{passes:.1f} passes/game",
                f"{key_passes:.1f} key passes/game",
                f"{dribbles:.1f} dribbles/game",
                "creative link-play and carry fingerprint",
                "exact wide/central zone not independently verified",
            ]
        elif key_passes >= 1.5 and dribbles >= 1.5 and shots < 2.5:
            role = "Complete Forward"
            evidence = [
                "observed generic forward position",
                "creative link-play fingerprint",
                "pressing role not independently verified",
            ]
        else:
            role = "Complete Forward"
            evidence = [
                "observed generic forward position",
                "pressing role not independently verified",
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
    elif observed in {"CM", "MID"}:
        role = "Advanced Playmaker" if key_passes >= 1.5 else "Box-to-Box"
        evidence = [f"observed {observed} lineup position"]
    elif observed in {"CDM", "DM"}:
        role = "Deep-Lying Playmaker" if passes >= 50 and tackles < 4.5 else "Ball Winner"
        evidence = [f"observed {observed} lineup position"]
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
        "source": "fixture_lineup_observation" if observed else "unavailable",
        "confidence": (
            "high"
            if observed and role and (
                observed in _EXACT_POSITIONS
                or observed in {"FWD", "MID", "DEF"}
            )
            else "low"
        ),
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