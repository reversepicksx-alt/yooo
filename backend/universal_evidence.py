"""Sport-neutral evidence packets for the shared prediction UI.

This module deliberately does not change projections.  It gives every sport
the same response shape while keeping labels, position vocabularies, and
evidence language appropriate to that sport.  A comparable-player sample is
only marked available when a sport adapter has supplied real rows.
"""

from __future__ import annotations

from typing import Any


SPORT_POSITION_GROUPS: dict[str, dict[str, str]] = {
    "nfl": {
        "QB": "quarterback", "RB": "running back", "FB": "fullback",
        "WR": "wide receiver", "TE": "tight end",
        "OL": "offensive lineman", "OT": "offensive tackle", "OG": "offensive guard",
        "C": "center", "DL": "defensive lineman", "DE": "defensive end",
        "DT": "defensive tackle", "LB": "linebacker", "MLB": "middle linebacker",
        "OLB": "outside linebacker", "ILB": "inside linebacker",
        "CB": "cornerback", "S": "safety", "DB": "defensive back",
        "K": "kicker", "P": "punter", "LS": "long snapper",
    },
    "mlb": {
        "P": "pitcher", "SP": "starting pitcher", "RP": "relief pitcher",
        "CP": "closing pitcher", "C": "catcher", "1B": "first baseman",
        "2B": "second baseman", "3B": "third baseman", "SS": "shortstop",
        "IF": "infielder", "OF": "outfielder", "LF": "left fielder",
        "CF": "center fielder", "RF": "right fielder", "DH": "designated hitter",
    },
    "nba": {
        "G": "guard", "PG": "point guard", "SG": "shooting guard",
        "F": "forward", "SF": "small forward", "PF": "power forward",
        "C": "center", "GF": "guard-forward", "FC": "forward-center",
    },
    "wnba": {
        "G": "guard", "PG": "point guard", "SG": "shooting guard",
        "F": "forward", "SF": "small forward", "PF": "power forward",
        "C": "center",
    },
    "nhl": {
        "C": "center", "LW": "left wing", "RW": "right wing",
        "W": "winger", "D": "defenseman", "LD": "left defenseman",
        "RD": "right defenseman", "G": "goaltender", "GK": "goaltender",
    },
    "ncaaf": {
        "QB": "quarterback", "RB": "running back", "FB": "fullback",
        "WR": "wide receiver", "TE": "tight end", "OL": "offensive lineman",
        "OT": "offensive tackle", "OG": "offensive guard", "C": "center",
        "DL": "defensive lineman", "DE": "defensive end", "DT": "defensive tackle",
        "LB": "linebacker", "CB": "cornerback", "S": "safety", "DB": "defensive back",
        "K": "kicker", "P": "punter",
    },
    "ncaab": {"G": "guard", "F": "forward", "C": "center"},
    "ncaaw": {"G": "guard", "F": "forward", "C": "center"},
    "cbase": {
        "P": "pitcher", "SP": "starting pitcher", "RP": "relief pitcher",
        "C": "catcher", "1B": "first baseman", "2B": "second baseman",
        "3B": "third baseman", "SS": "shortstop", "IF": "infielder",
        "OF": "outfielder", "LF": "left fielder", "CF": "center fielder",
        "RF": "right fielder", "DH": "designated hitter",
    },
}

SPORT_ROLE_QUESTIONS: dict[str, tuple[str, ...]] = {
    "nfl": (
        "Does the player's listed position create the requested production?",
        "Will matchup, workload, and game script support this role's opportunity?",
    ),
    "mlb": (
        "Does the player's field or pitcher role create the requested production?",
        "Will the opposing pitcher, lineup context, and park support the opportunity?",
    ),
    "nba": (
        "Does the player's court role create the requested production?",
        "Will minutes, usage, and opponent matchup support this role's opportunity?",
    ),
    "wnba": (
        "Does the player's court role create the requested production?",
        "Will minutes, usage, and opponent matchup support this role's opportunity?",
    ),
    "nhl": (
        "Does the player's on-ice role create the requested production?",
        "Will line usage, ice time, and opponent matchup support the opportunity?",
    ),
    "ncaaf": (
        "Does the player's football position create the requested production?",
        "Will workload, matchup, and game script support this role's opportunity?",
    ),
    "ncaab": (
        "Does the player's court role create the requested production?",
        "Will minutes, usage, and opponent matchup support this role's opportunity?",
    ),
    "ncaaw": (
        "Does the player's court role create the requested production?",
        "Will minutes, usage, and opponent matchup support this role's opportunity?",
    ),
    "cbase": (
        "Does the player's baseball role create the requested production?",
        "Will lineup, pitching, park, and matchup context support the opportunity?",
    ),
    "atp": (
        "Does the player's serve/return profile create the requested production?",
        "Will surface, opponent, and match format support the opportunity?",
    ),
    "wta": (
        "Does the player's serve/return profile create the requested production?",
        "Will surface, opponent, and match format support the opportunity?",
    ),
    "pga": (
        "Does the player's course and round role create the requested production?",
        "Will course conditions and tournament context support the opportunity?",
    ),
    "mma": (
        "Does the fighter's style and phase profile create the requested production?",
        "Will the opponent matchup and expected fight script support the opportunity?",
    ),
    "f1": (
        "Does the driver's race role create the requested production?",
        "Will qualifying, strategy, circuit, and race conditions support the opportunity?",
    ),
    "lol": (
        "Does the player's lane or team role create the requested production?",
        "Will matchup, champion context, and game script support the opportunity?",
    ),
    "dota2": (
        "Does the player's lane or team role create the requested production?",
        "Will matchup, draft, and game script support the opportunity?",
    ),
    "cs2": (
        "Does the player's map role create the requested production?",
        "Will map, opponent, and round context support the opportunity?",
    ),
}

SPORT_DEFAULT_ROLES: dict[str, str] = {
    "atp": "singles player",
    "wta": "singles player",
    "pga": "tour golfer",
    "mma": "fighter",
    "f1": "driver",
    "lol": "esports player",
    "dota2": "esports player",
    "cs2": "esports player",
}

SPORT_ROLE_GROUPS: dict[str, dict[str, str]] = {
    "nfl": {
        "QB": "quarterback", "RB": "backfield", "FB": "backfield",
        "WR": "pass catcher", "TE": "pass catcher", "OL": "offensive line",
        "OT": "offensive line", "OG": "offensive line", "C": "offensive line",
        "DL": "defensive line", "DE": "defensive line", "DT": "defensive line",
        "LB": "linebacker", "MLB": "linebacker", "OLB": "linebacker", "ILB": "linebacker",
        "CB": "coverage defender", "S": "coverage defender", "DB": "coverage defender",
        "K": "special teams", "P": "special teams", "LS": "special teams",
    },
    "ncaaf": {
        "QB": "quarterback", "RB": "backfield", "FB": "backfield",
        "WR": "pass catcher", "TE": "pass catcher", "OL": "offensive line",
        "OT": "offensive line", "OG": "offensive line", "C": "offensive line",
        "DL": "defensive line", "DE": "defensive line", "DT": "defensive line",
        "LB": "linebacker", "CB": "coverage defender", "S": "coverage defender",
        "DB": "coverage defender", "K": "special teams", "P": "special teams",
    },
    "mlb": {
        "P": "pitcher", "SP": "pitcher", "RP": "pitcher", "CP": "pitcher",
        "C": "infield defense", "1B": "infield defense", "2B": "infield defense",
        "3B": "infield defense", "SS": "infield defense", "IF": "infield defense",
        "OF": "outfield defense", "LF": "outfield defense", "CF": "outfield defense",
        "RF": "outfield defense", "DH": "designated hitter",
    },
    "cbase": {
        "P": "pitcher", "SP": "pitcher", "RP": "pitcher", "C": "infield defense",
        "1B": "infield defense", "2B": "infield defense", "3B": "infield defense",
        "SS": "infield defense", "IF": "infield defense", "OF": "outfield defense",
        "LF": "outfield defense", "CF": "outfield defense", "RF": "outfield defense",
        "DH": "designated hitter",
    },
    "nba": {"G": "guard", "PG": "guard", "SG": "guard", "F": "forward",
            "SF": "forward", "PF": "forward", "C": "center", "GF": "guard-forward",
            "FC": "forward-center"},
    "wnba": {"G": "guard", "PG": "guard", "SG": "guard", "F": "forward",
             "SF": "forward", "PF": "forward", "C": "center"},
    "ncaab": {"G": "guard", "F": "forward", "C": "center"},
    "ncaaw": {"G": "guard", "F": "forward", "C": "center"},
    "nhl": {"C": "forward", "LW": "forward", "RW": "forward", "W": "forward",
            "D": "defense", "LD": "defense", "RD": "defense",
            "G": "goaltender", "GK": "goaltender"},
}


def _sport(response: dict[str, Any]) -> str:
    return str(response.get("sport") or "").strip().lower()


def _position(response: dict[str, Any]) -> str:
    player = response.get("player")
    player_position = player.get("position") if isinstance(player, dict) else None
    return str(
        response.get("playerPosition")
        or response.get("position")
        or player_position
        or ""
    ).strip()


def _role(response: dict[str, Any]) -> str:
    player = response.get("player")
    player_role = player.get("role") if isinstance(player, dict) else None
    explicit = str(
        response.get("playerRole")
        or response.get("role")
        or player_role
        or ""
    ).strip()
    if explicit:
        return explicit
    sport = _sport(response)
    code = _position_code(sport, _position(response))
    return SPORT_ROLE_GROUPS.get(sport, {}).get(code) or SPORT_DEFAULT_ROLES.get(sport, "")


def _position_code(sport: str, position: str) -> str:
    raw = position.upper().strip().replace("-", "").replace("_", "").replace(" ", "")
    aliases = {
        "QUARTERBACK": "QB", "RUNNINGBACK": "RB", "WIDERECEIVER": "WR",
        "TIGHTEND": "TE", "LINEBACKER": "LB", "CORNERBACK": "CB",
        "SAFETY": "S", "PITCHER": "P", "CATCHER": "C",
        "FIRSTBASEMAN": "1B", "SECONDBASEMAN": "2B", "THIRDBASEMAN": "3B",
        "SHORTSTOP": "SS", "OUTFIELDER": "OF", "GUARD": "G",
        "FORWARD": "F", "CENTER": "C", "DEFENSEMAN": "D",
        "GOALTENDER": "G", "LEFTWING": "LW", "RIGHTWING": "RW",
    }
    return aliases.get(raw, raw)


def _role_questions(sport: str) -> list[str]:
    return list(SPORT_ROLE_QUESTIONS.get(sport, (
        "Does the player's verified role create the requested production?",
        "Will the event, matchup, and workload support this role's opportunity?",
    )))


def _unavailable_comparison(
    response: dict[str, Any],
    sport: str,
    position: str,
    role: str,
) -> dict[str, Any]:
    position_code = _position_code(sport, position)
    position_label = SPORT_POSITION_GROUPS.get(sport, {}).get(position_code, position)
    has_exact_position = bool(position_code and position_code in SPORT_POSITION_GROUPS.get(sport, {}))
    note = (
        f"{position_label.title()} is the verified sport position, but no "
        f"sport-specific comparable-player sample was returned."
        if has_exact_position
        else "A verified sport-specific position or comparable-player sample was not returned."
    )
    return {
        "version": "universal-evidence-v1",
        "sport": sport,
        "targetPosition": position or None,
        "targetRole": role or None,
        "positionShort": position or None,
        "opponent": response.get("opponentName") or None,
        "venue": response.get("venue") or None,
        "propType": response.get("propType") or None,
        "sampleSize": 0,
        "minimumRecommendedSample": 10,
        "sampleStatus": "unavailable",
        "players": [],
        "comparisonMode": "unavailable",
        "positionEvidenceType": "exact_position" if has_exact_position else "unavailable",
        "positionEvidenceNote": note,
        "comparisonUnavailableReason": "no_sport_specific_comparison_sample",
        "sourceScope": f"{sport}_provider_unavailable",
        "verdict": {
            "verdict": "unavailable",
            "reason": note,
            "average": None,
            "line": response.get("line"),
            "sampleSize": 0,
            "recommendation": None,
        },
    }


def ensure_universal_evidence(response: dict[str, Any]) -> dict[str, Any]:
    """Attach shared role/comparison evidence without changing model output."""
    sport = _sport(response)
    position = _position(response)
    role = _role(response)
    game_logs = response.get("gameLogs")
    game_log_count = len(game_logs) if isinstance(game_logs, list) else 0

    # Existing soccer packets and future sport adapters are authoritative.
    if not isinstance(response.get("positionComparison"), dict):
        response["positionComparison"] = _unavailable_comparison(
            response, sport, position, role
        )

    if not isinstance(response.get("roleEvidencePacket"), dict):
        packet_status = "partial" if position or role else "unavailable"
        response["roleEvidencePacket"] = {
            "version": "universal-evidence-v1",
            "sport": sport,
            "status": packet_status,
            "position": position or None,
            "role": role or None,
            "source": "provider_position" if position else "unavailable",
            "confidence": "low",
            "fixtureId": response.get("fixtureId") or response.get("gameId"),
            "venue": response.get("venue") or None,
            "questions": _role_questions(sport),
            "opportunity": {
                "expectedMinutes": response.get("avgMinutes")
                or response.get("minutes"),
                "playerLogCount": game_log_count,
                "propType": response.get("propType") or None,
            },
            "evidenceCounts": {
                "fixtureIdentity": int(bool(response.get("fixtureId") or response.get("gameId"))),
                "exactRole": int(bool(role)),
                "position": int(bool(position)),
                "playerOpportunity": game_log_count,
                "sameRoleComparables": 0,
                "sameVenueComparables": 0,
            },
            "sameRoleEvidence": {
                "sampleSize": 0,
                "status": "unavailable",
                "reason": "no_sport_specific_comparison_sample",
            },
            "sameVenueEvidence": {
                "sampleSize": 0,
                "status": "unavailable",
                "reason": "no_sport_specific_comparison_sample",
            },
            "projectionInfluence": "shadow_only",
            "confidenceControl": (
                "Sport position is present, but comparable role evidence is unavailable; "
                "the deterministic projection is unchanged."
                if position
                else "Sport position and comparable role evidence are unavailable; "
                     "the deterministic projection is unchanged."
            ),
        }

    # Always expose the canonical aliases consumed by shared clients.
    if not response.get("playerPosition") and position:
        response["playerPosition"] = position
    if not response.get("playerRole") and role:
        response["playerRole"] = role
    return response