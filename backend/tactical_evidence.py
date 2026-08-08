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
    "M": "MID",
    "MIDFIELDER": "MID",
    "F": "FWD",
    "FORWARD": "FWD",
    "ATTACKER": "FWD",
}

_WIDE_POSITIONS = {"LW", "RW", "LM", "RM", "LWB", "RWB"}
_CREATIVE_PROPS = {"pass_attempts", "passes", "key_passes", "shots_assisted", "dribbles"}


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
    elif observed in {"CB", "DEF", "LB", "RB", "LWB", "RWB"}:
        role = "Ball-Playing CB" if observed in {"CB", "DEF"} and passes >= 50 else "Fullback"
        evidence = [f"observed {observed} lineup position"]
    elif observed == "GK":
        role = "Shot-Stopper"
        evidence = ["observed goalkeeper lineup position"]

    return {
        "position": observed or None,
        "role": role,
        "source": "fixture_lineup_observation" if observed else "unavailable",
        "confidence": "high" if observed and role else "low",
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
    minimum_sample: int = 10,
) -> dict[str, Any]:
    """Aggregate a same-position opponent cohort without padding its sample."""
    rows = [
        row for row in (players or [])
        if _number((row or {}).get("statValue")) is not None
        and (_number((row or {}).get("minutes")) or 0) > 0
    ]
    values = [_number(row.get("statValue")) for row in rows]
    values = [value for value in values if value is not None]
    threshold = _number(line)
    over_hits = sum(1 for value in values if threshold is not None and value > threshold)
    under_hits = sum(1 for value in values if threshold is not None and value < threshold)
    size = len(values)
    return {
        "sampleSize": size,
        "minimumRecommendedSample": minimum_sample,
        "sampleStatus": (
            "sufficient" if size >= minimum_sample
            else "limited" if size
            else "unavailable"
        ),
        "average": round(sum(values) / size, 2) if values else None,
        "overHits": over_hits,
        "underHits": under_hits,
        "overHitRate": round(over_hits / size * 100) if values else None,
        "underHitRate": round(under_hits / size * 100) if values else None,
        "position": next((row.get("position") for row in rows if row.get("position")), None),
        "venue": next((row.get("venue") for row in rows if row.get("venue")), None),
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