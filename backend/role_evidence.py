"""Role-first evidence contract for soccer player props.

This module is deliberately descriptive.  It makes the questions that must be
answered before a projection is trusted explicit, without changing the
Bayesian projection until the packet has enough settled-pick validation.
"""

from __future__ import annotations

from typing import Any


ROLE_QUESTIONS: dict[str, tuple[str, ...]] = {
    "Goalkeeper": (
        "Will the goalkeeper face enough shots on target for save volume?",
        "Is the team likely to defend deep or control possession?",
        "Is distribution/back-pass volume relevant to this prop?",
    ),
    "Shot-Stopper": (
        "Will the goalkeeper face enough shots on target for save volume?",
        "Does the opponent's shot quality create save opportunity?",
    ),
    "Sweeper Keeper": (
        "Will a high defensive line create sweeper actions?",
        "Does build-up responsibility create distribution opportunity?",
    ),
    "CB": (
        "Will the centre-back defend enough box and aerial actions?",
        "Will build-up pressure create passing opportunity?",
    ),
    "Ball-Playing CB": (
        "Will the centre-back be the first progression outlet?",
        "Will the opponent press high enough to change passing volume?",
    ),
    "Stopper": (
        "Will the centre-back step into duels and defensive actions?",
        "Does the opponent's striker profile create the relevant opportunity?",
    ),
    "Fullback": (
        "Will the fullback advance or remain in the defensive line?",
        "Will the opponent's wing threat create defensive-action opportunity?",
    ),
    "Inverted Fullback": (
        "Will the fullback move inside during build-up?",
        "Does the midfield structure create central passing opportunity?",
    ),
    "Wing-Back": (
        "Will the wing-back hold width and make repeated forward actions?",
        "Will the opponent pin the wing-back deep or allow progression?",
    ),
    "Anchor": (
        "Will the midfielder screen the defence and receive first phase?",
        "Will match state increase defensive or circulation actions?",
    ),
    "Deep-Lying Playmaker": (
        "Will the midfielder be the primary first-phase distributor?",
        "Will opponent pressure and possession create passing volume?",
    ),
    "Ball Winner": (
        "Will the midfielder contest enough duels and defensive transitions?",
        "Does the opponent's progression create tackle/interception opportunity?",
    ),
    "Box-to-Box": (
        "Will the midfielder cover both defensive and attacking phases?",
        "Will expected minutes and transition frequency support the prop?",
    ),
    "Mezzala": (
        "Will the midfielder attack half-spaces and combine near the box?",
        "Does the formation provide enough wide/advanced involvement?",
    ),
    "Advanced Playmaker": (
        "Will the player receive between the lines?",
        "Will the opponent block central access or allow chance creation?",
    ),
    "Wide Playmaker": (
        "Will the player receive wide and progress inside?",
        "Does the opponent's shape allow crossing or key-pass opportunity?",
    ),
    "Traditional Winger": (
        "Will the winger hold width and receive 1v1 opportunities?",
        "Will the fullback matchup create crossing, shot, or dribble volume?",
    ),
    "Inverted Winger": (
        "Will the winger cut inside onto the stronger foot?",
        "Does the opponent protect the half-space or concede shots?",
    ),
    "Inside Forward": (
        "Will the attacker occupy scoring zones rather than stay wide?",
        "Does the opponent's defensive block concede box entries?",
    ),
    "Progressive Carrier": (
        "Will the player have space to carry through pressure?",
        "Does the opponent's rest defence create transition lanes?",
    ),
    "Shadow Striker": (
        "Will the player arrive beyond the striker in scoring areas?",
        "Does the opponent leave space between midfield and defence?",
    ),
    "False 9": (
        "Will the forward drop to connect play or stay high?",
        "Does the opponent follow the drop and open runs beyond?",
    ),
    "Target Man": (
        "Will the team use direct service into the forward?",
        "Does the opponent's centre-back matchup create aerial opportunity?",
    ),
    "Poacher": (
        "Will the player receive enough box entries and final actions?",
        "Does the opponent concede central chances?",
    ),
    "Complete Forward": (
        "Will the forward both link play and attack the box?",
        "Does the match script support the required mix of actions?",
    ),
    "Pressing Forward": (
        "Will the forward lead the press for enough minutes?",
        "Will opponent build-up create pressure and transition opportunity?",
    ),
}


POSITION_QUESTIONS: dict[str, tuple[str, ...]] = {
    "GK": ROLE_QUESTIONS["Goalkeeper"],
    "CB": ROLE_QUESTIONS["CB"],
    "LB": ROLE_QUESTIONS["Fullback"],
    "RB": ROLE_QUESTIONS["Fullback"],
    "LWB": ROLE_QUESTIONS["Wing-Back"],
    "RWB": ROLE_QUESTIONS["Wing-Back"],
    "CDM": ROLE_QUESTIONS["Anchor"],
    "CM": ROLE_QUESTIONS["Box-to-Box"],
    "CAM": ROLE_QUESTIONS["Advanced Playmaker"],
    "LM": ROLE_QUESTIONS["Wide Playmaker"],
    "RM": ROLE_QUESTIONS["Wide Playmaker"],
    "LW": ROLE_QUESTIONS["Traditional Winger"],
    "RW": ROLE_QUESTIONS["Traditional Winger"],
    "CF": ROLE_QUESTIONS["Complete Forward"],
    "ST": ROLE_QUESTIONS["Complete Forward"],
    "SS": ROLE_QUESTIONS["Shadow Striker"],
}


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def build_role_evidence_packet(
    *,
    position: str | None,
    role: str | None,
    source: str | None,
    confidence: str | None,
    lineup_status: str | None,
    fixture_id: Any,
    venue: str | None,
    role_stats: dict[str, Any] | None = None,
    player_logs: list[dict[str, Any]] | None = None,
    comparable_players: list[dict[str, Any]] | None = None,
    prop_type: str | None = None,
) -> dict[str, Any]:
    """Create an auditable, role-first packet for the final response."""
    normalized_position = str(position or "").strip().upper()
    normalized_role = str(role or "").strip()
    questions = list(
        ROLE_QUESTIONS.get(normalized_role)
        or POSITION_QUESTIONS.get(normalized_position)
        or (
            "Is the player's exact field role verified for this fixture?",
            "Does the role create opportunity for the requested prop?",
        )
    )
    stats = role_stats or {}
    logs = [
        row for row in (player_logs or [])
        if isinstance(row, dict) and (_num(row.get("minutes")) or 0) > 0
    ]
    rows = [row for row in (comparable_players or []) if isinstance(row, dict)]
    same_role = [
        row for row in rows
        if normalized_role and str(row.get("role") or "").strip().lower() == normalized_role.lower()
    ]
    same_venue = [
        row for row in rows
        if venue and str(row.get("venue") or "").strip().lower() == str(venue).lower()
    ]
    role_known = bool(normalized_role and normalized_position not in {"", "DEF", "MID", "FWD"})
    exact_fixture_observation = source in {
        "fixture_lineup_observation",
        "predicted_lineup_grid",
        "api_sports_lineup_history",
    }
    status = "verified" if role_known and exact_fixture_observation else (
        "partial" if role_known or normalized_position else "unavailable"
    )
    if normalized_position in {"DEF", "MID", "FWD", "Goalkeeper", "Defender", "Midfielder", "Attacker"}:
        status = "partial"
    if confidence == "low" and status == "verified":
        status = "partial"

    opportunity = {
        "expectedMinutes": round(
            sum(_num(row.get("minutes")) or 0 for row in logs) / len(logs), 1
        ) if logs else None,
        "playerLogCount": len(logs),
        "passes": stats.get("passes_total"),
        "tackles": stats.get("tackles_total"),
        "shots": stats.get("shots_total"),
        "propType": prop_type,
    }
    evidence_counts = {
        "fixtureIdentity": 1 if fixture_id is not None else 0,
        "exactRole": 1 if role_known else 0,
        "lineupObservation": 1 if exact_fixture_observation else 0,
        "playerOpportunity": len(logs),
        "sameRoleComparables": len(same_role),
        "sameVenueComparables": len(same_venue),
    }
    return {
        "version": "role-evidence-v1",
        "status": status,
        "position": position or None,
        "role": normalized_role or None,
        "source": source or "unavailable",
        "confidence": confidence or "low",
        "fixtureId": fixture_id,
        "venue": venue or None,
        "questions": questions,
        "opportunity": opportunity,
        "evidenceCounts": evidence_counts,
        "sameRoleEvidence": {
            "sampleSize": len(same_role),
            "status": "available" if same_role else "unavailable",
        },
        "sameVenueEvidence": {
            "sampleSize": len(same_venue),
            "status": "available" if same_venue else "unavailable",
        },
        "projectionInfluence": "shadow_only",
        "confidenceControl": (
            "Role and prop opportunity are verified for this fixture."
            if status == "verified"
            else "Role or prop opportunity is incomplete; confidence must remain conservative."
        ),
    }