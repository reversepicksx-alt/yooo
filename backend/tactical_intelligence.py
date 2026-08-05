"""Evidence-gated soccer tactical intelligence.

This module is intentionally pure and provider-shape tolerant. It converts the
verified fixture, market, lineup, role, and prop context into an auditable
tactical packet. The packet is currently shadow-only for projection changes:
the existing Bayesian engine remains the numeric source of truth until these
signals have enough settled-pick history to calibrate safely.
"""

from __future__ import annotations

from typing import Any


PASS_PROPS = {"pass_attempts", "passes", "key_passes", "crosses"}
ATTACK_PROPS = {"shots", "shots_on_target", "goals", "assists", "shots_assisted"}
DEFENSIVE_PROPS = {
    "tackles", "interceptions", "clearances", "blocks",
    "fouls_committed", "duels_won",
}
GK_PROPS = {"saves", "goalie_saves"}
BALL_CARRY_PROPS = {"dribbles"}


def _num(value: Any) -> float | None:
    try:
        if value is None or str(value).strip() == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _side_players(side: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(side, dict):
        return []
    result = []
    for raw in side.get("players") or []:
        if not isinstance(raw, dict):
            continue
        result.append(raw)
    return result


def _player_position(player: dict[str, Any]) -> str:
    return _clean(
        player.get("position")
        or player.get("pos")
        or player.get("specificPosition")
    ).upper().replace(" ", "")


def _role_group(position: str, role: str = "") -> str:
    pos = _clean(position).upper().replace(" ", "")
    role_low = _clean(role).lower()
    if pos in {"GK", "G", "GOALKEEPER"}:
        return "goalkeeper"
    if pos in {"CB", "LCB", "RCB", "SW", "LIB", "DEF", "LB", "RB", "LWB", "RWB", "FB"}:
        return "defender"
    if pos in {"DM", "CDM", "CM", "LCM", "RCM", "MID", "AM", "CAM", "LM", "RM"}:
        return "midfielder"
    if pos in {"LW", "RW", "WING", "WF"} or "wing" in role_low:
        return "wide_attacker"
    if pos in {"ST", "CF", "SS", "FWD", "FW"} or "forward" in role_low or "striker" in role_low:
        return "forward"
    if "def" in role_low:
        return "defender"
    if "keeper" in role_low:
        return "goalkeeper"
    return "unknown"


def _parse_moneyline(value: Any) -> float | None:
    """Return a decimal implied probability from American/decimal odds."""
    raw = _clean(value).replace("−", "-").replace("–", "-")
    if not raw or raw.upper() in {"N/A", "NA", "NONE", "NULL"}:
        return None
    try:
        number = float(raw.replace("+", ""))
    except (TypeError, ValueError):
        return None
    # American odds are normally displayed as +/-100 or larger. Handle the
    # sign before the decimal-odds branch so -278 and +550 both work.
    if number <= -100:
        return abs(number) / (abs(number) + 100.0)
    if number >= 100:
        return 100.0 / (number + 100.0)
    if number > 0:
        return 1.0 / number
    return None


def _find_target(
    players: list[dict[str, Any]],
    player_id: Any,
    player_name: str,
) -> dict[str, Any] | None:
    pid = str(player_id or "")
    name = _clean(player_name).lower()
    for player in players:
        if pid and str(player.get("id") or "") == pid:
            return player
    if name:
        for player in players:
            candidate = _clean(player.get("name")).lower()
            if candidate and (candidate == name or name in candidate or candidate in name):
                return player
    return None


def _formation_rows(players: list[dict[str, Any]]) -> dict[str, int]:
    rows: dict[str, int] = {}
    for player in players:
        pos = _player_position(player)
        group = _role_group(pos)
        rows[group] = rows.get(group, 0) + 1
    return rows


def _opponent_role_matchup(
    target: dict[str, Any] | None,
    opponent_players: list[dict[str, Any]],
    prop_type: str,
) -> dict[str, Any]:
    target_pos = _player_position(target or {})
    target_group = _role_group(target_pos)
    opponent_groups = _formation_rows(opponent_players)
    opponent_defenders = sum(
        opponent_groups.get(group, 0)
        for group in ("defender", "midfielder")
    )
    if target_group == "goalkeeper":
        relevant = "opponent attack volume"
        comparison = "Goalkeeper workload is compared with the opponent's attacking personnel, not a direct marker."
    elif prop_type in PASS_PROPS:
        relevant = "opponent pressure and available passing outlets"
        comparison = "Passing opportunity is compared with the opponent's nominal pressure structure; no direct press assignment is assumed."
    elif prop_type in ATTACK_PROPS or prop_type in BALL_CARRY_PROPS:
        relevant = "opponent defensive density"
        comparison = "Attacking opportunity is compared with nominal defensive density; no one-to-one marking assignment is assumed."
    elif prop_type in DEFENSIVE_PROPS:
        relevant = "opponent attacking personnel"
        comparison = "Defensive workload is compared with nominal opponent attacking personnel; event-level pressure is not available pre-match."
    else:
        relevant = "nominal opponent shape"
        comparison = "The comparison is nominal only because event-level matchup data is unavailable."

    return {
        "targetPosition": target_pos or None,
        "targetRoleGroup": target_group,
        "opponentRoleCounts": opponent_groups,
        "opponentDefensiveCount": opponent_defenders,
        "relevantMechanism": relevant,
        "comparison": comparison,
        "directMarkingVerified": False,
        "sampleStatus": "nominal_lineup_comparison" if opponent_players else "unavailable",
    }


def build_tactical_intelligence(
    *,
    prediction: dict[str, Any],
    prop_type: str,
    player_position: str | None = None,
    player_role: str | None = None,
    expected_possession: float | None = None,
    possession_is_real: bool = False,
    possession_source: str | None = None,
    opponent_allowed_average: float | None = None,
    opponent_allowed_samples: int = 0,
    position_comparable_samples: int = 0,
    game_script: dict[str, Any] | None = None,
    lineup: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a complete, provenance-tagged tactical evidence packet."""
    lineup = lineup if isinstance(lineup, dict) else {}
    home = lineup.get("home") if isinstance(lineup.get("home"), dict) else {}
    away = lineup.get("away") if isinstance(lineup.get("away"), dict) else {}
    is_player_home = bool(prediction.get("isHome", True))
    player_side = home if is_player_home else away
    opponent_side = away if is_player_home else home
    player_players = _side_players(player_side)
    opponent_players = _side_players(opponent_side)

    target = _find_target(
        player_players,
        (prediction.get("player") or {}).get("id"),
        (prediction.get("player") or {}).get("name") or prediction.get("playerName"),
    )
    target_pos = _clean(player_position) or _player_position(target or {})
    target_role = _clean(player_role) or _clean((prediction.get("player") or {}).get("role"))
    target_group = _role_group(target_pos, target_role)
    opponent_matchup = _opponent_role_matchup(target, opponent_players, prop_type)

    market = prediction.get("moneyline") if isinstance(prediction.get("moneyline"), dict) else {}
    home_prob = _parse_moneyline(market.get("home"))
    away_prob = _parse_moneyline(market.get("away"))
    subject_prob = home_prob if is_player_home else away_prob
    opponent_prob = away_prob if is_player_home else home_prob
    market_status = "unavailable"
    market_script = "unknown"
    market_direction = "neutral"
    if subject_prob is not None and opponent_prob is not None:
        market_status = "verified_fixture_moneyline"
        if subject_prob >= 0.65:
            market_script = "player_team_favorite"
        elif subject_prob <= 0.35:
            market_script = "player_team_underdog"
        else:
            market_script = "balanced_market"
        if market_script == "player_team_favorite":
            market_direction = "more_settled_possession"
        elif market_script == "player_team_underdog":
            market_direction = "more_defensive_workload"

    # One combined game-script interpretation prevents the moneyline and
    # possession estimate from being counted as two independent adjustments.
    script_support: list[str] = []
    if market_script == "player_team_favorite":
        if prop_type in PASS_PROPS or prop_type in ATTACK_PROPS:
            script_support.append("market supports more player-team attacking sequences")
        elif prop_type in DEFENSIVE_PROPS or prop_type in GK_PROPS:
            script_support.append("market may reduce player-team defensive workload")
    elif market_script == "player_team_underdog":
        if prop_type in DEFENSIVE_PROPS or prop_type in GK_PROPS:
            script_support.append("market supports more player-team defensive workload")
        elif prop_type in PASS_PROPS or prop_type in ATTACK_PROPS:
            script_support.append("market may reduce settled player-team attacking sequences")

    if expected_possession is not None and possession_is_real:
        if expected_possession >= 55:
            poss_script = "player_team_possession_edge"
        elif expected_possession <= 45:
            poss_script = "player_team_possession_deficit"
        else:
            poss_script = "possession_balanced"
        if poss_script == "player_team_possession_edge" and prop_type in PASS_PROPS:
            script_support.append("verified possession supports circulation volume")
        elif poss_script == "player_team_possession_deficit" and prop_type in DEFENSIVE_PROPS | GK_PROPS:
            script_support.append("verified possession deficit supports defensive-event volume")
    else:
        poss_script = "unavailable"

    if opponent_allowed_average is not None and opponent_allowed_samples >= 3:
        opponent_evidence = "comparable_opponent_sample"
        opponent_note = (
            f"Opponent allows {opponent_allowed_average:.1f} {prop_type.replace('_', ' ')} "
            f"across {opponent_allowed_samples} comparable observations."
        )
    elif position_comparable_samples:
        opponent_evidence = "position_comparison_sample"
        opponent_note = f"Position comparison has {position_comparable_samples} comparable observations."
    else:
        opponent_evidence = "unavailable"
        opponent_note = None

    lineup_status = _clean(lineup.get("status")) or "unavailable"
    formation = _clean(player_side.get("formation")) or None
    opponent_formation = _clean(opponent_side.get("formation")) or None
    shape_status = "confirmed" if lineup_status == "confirmed" else "projected" if lineup_status == "predicted" else "unavailable"

    limitations = []
    if not target_pos and not target_role:
        limitations.append("player role and position unavailable")
    if not opponent_players:
        limitations.append("opponent lineup unavailable")
    if not formation or not opponent_formation:
        limitations.append("one or both formations unavailable")
    if market_status == "unavailable":
        limitations.append("verified fixture moneyline unavailable")
    if not possession_is_real:
        limitations.append("possession is fallback or unavailable")
    limitations.append("direct marking and average-position data unavailable")

    tactical_status = "strong" if (
        target_group != "unknown"
        and opponent_players
        and (market_status == "verified_fixture_moneyline" or possession_is_real)
    ) else "limited"

    return {
        "version": "tactical-shadow-v1",
        "mode": "shadow",
        "status": tactical_status,
        "sourcePolicy": "verified inputs only; inferred mechanisms are labeled",
        "player": {
            "position": target_pos or None,
            "role": target_role or None,
            "roleGroup": target_group,
            "providerGridPosition": {
                "x": target.get("x") if target else None,
                "y": target.get("y") if target else None,
            },
            "positionSource": "lineup-provider" if target_pos else "unavailable",
            "roleSource": "position-role-resolver" if target_role else "unavailable",
        },
        "lineup": {
            "status": lineup_status,
            "shapeStatus": shape_status,
            "formation": formation,
            "opponentFormation": opponent_formation,
            "playerTeam": player_side.get("teamName"),
            "opponent": opponent_side.get("teamName"),
            "playerCount": len(player_players),
            "opponentPlayerCount": len(opponent_players),
        },
        "marketGameScript": {
            "status": market_status,
            "homeImpliedProbability": round(home_prob, 4) if home_prob is not None else None,
            "awayImpliedProbability": round(away_prob, 4) if away_prob is not None else None,
            "playerTeamImpliedProbability": round(subject_prob, 4) if subject_prob is not None else None,
            "opponentImpliedProbability": round(opponent_prob, 4) if opponent_prob is not None else None,
            "classification": market_script,
            "direction": market_direction,
            "source": "verified fixture moneyline" if market_status != "unavailable" else None,
        },
        "possessionGameScript": {
            "status": "verified" if possession_is_real else "fallback_or_unavailable",
            "expectedPlayerTeamPossession": expected_possession,
            "classification": poss_script,
            "source": (
                "verified fixture statistics"
                if possession_is_real
                else possession_source
                if expected_possession is not None
                else None
            ),
        },
        "propMechanism": {
            "propType": prop_type,
            "roleGroup": target_group,
            "marketSupport": script_support,
            "gameScriptEvidence": script_support,
            "opponentEvidence": opponent_evidence,
            "opponentNote": opponent_note,
            "gameScript": game_script or None,
            "projectionAdjustment": 0.0,
            "projectionAdjustmentStatus": "shadow_only_until_calibrated",
        },
        "opponentRoleComparison": opponent_matchup,
        "evidence": {
            "opponentAllowedAverage": opponent_allowed_average,
            "opponentAllowedSamples": opponent_allowed_samples,
            "positionComparableSamples": position_comparable_samples,
            "formationData": shape_status,
            "marketData": market_status,
            "possessionData": "verified" if possession_is_real else "fallback_or_unavailable",
        },
        "limitations": limitations,
    }