"""Evidence-gated soccer tactical intelligence.

This module is intentionally pure and provider-shape tolerant. It converts the
verified fixture, market, lineup, role, and prop context into an auditable
tactical packet. The packet is currently shadow-only for projection changes:
the existing Bayesian engine remains the numeric source of truth until these
signals have enough settled-pick history to calibrate safely.
"""

from __future__ import annotations

from typing import Any

from positional_reality import build_positional_reality


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


def _formal_match_script(
    *,
    market_status: str,
    market_script: str,
    subject_prob: float | None,
    expected_possession: float | None,
    possession_is_real: bool,
    game_script: dict[str, Any] | None,
    is_player_home: bool,
) -> dict[str, Any]:
    """Classify the pre-match environment once, with explicit provenance.

    The moneyline and possession are correlated views of the same fixture
    script. This packet names the combined read so downstream UI/replay code
    cannot accidentally count them as independent adjustments.
    """
    scenario = game_script if isinstance(game_script, dict) else {}
    dominant = _clean(scenario.get("dominant") or scenario.get("key_finding")).lower()
    dominant_side = "home" if "home_dominant" in dominant else "away" if "away_dominant" in dominant else None
    if "low" in dominant:
        classification = "low_event"
        label = "Low-event / compressed"
    elif "high" in dominant or "open" in dominant:
        classification = "open_event"
        label = "Open / high-event"
    elif dominant_side and ((dominant_side == "home") == is_player_home):
        classification = "controlled_dominance"
        label = "Controlled dominance"
    elif dominant_side:
        classification = "opponent_dominance"
        label = "Opponent control / reactive"
    elif market_script == "player_team_favorite":
        classification = "settled_control"
        label = "Settled control"
    elif market_script == "player_team_underdog":
        classification = "counter_defensive"
        label = "Reactive / counter-defensive"
    elif market_script == "balanced_market":
        classification = "balanced"
        label = "Balanced"
    else:
        classification = "unknown"
        label = "Unavailable"

    sources: list[str] = []
    if market_status == "verified_fixture_moneyline":
        sources.append("verified fixture moneyline")
    if expected_possession is not None:
        sources.append("verified possession" if possession_is_real else "fallback possession estimate")
    if scenario.get("available") or scenario.get("dominant"):
        sources.append("score-scenario model")

    # Confidence is intentionally a data-completeness score, not a claim that
    # the match will follow the script.
    confidence = 0.35
    if market_status == "verified_fixture_moneyline":
        confidence += 0.35
    if possession_is_real:
        confidence += 0.15
    if scenario.get("available") or scenario.get("dominant"):
        confidence += 0.10
    if classification == "unknown":
        confidence = min(confidence, 0.35)

    limitations = [
        "pre-match classification; an early goal, red card, or substitution can change the script",
    ]
    if not sources:
        limitations.append("no verified fixture market or possession source")
    if expected_possession is not None and not possession_is_real:
        limitations.append("possession is derived/fallback evidence, not measured fixture possession")

    return {
        "classification": classification,
        "label": label,
        "confidence": round(min(1.0, confidence), 2),
        "confidenceLabel": "high" if confidence >= 0.75 else "medium" if confidence >= 0.55 else "low",
        "sources": sources,
        "subjectTeamImpliedProbability": round(subject_prob, 4) if subject_prob is not None else None,
        "expectedPossession": expected_possession,
        "scenarioDominant": scenario.get("dominant") or None,
        "limitations": limitations,
        "status": "classified" if classification != "unknown" else "unavailable",
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
    history_values: list[Any] | None = None,
    bzzoiro_enrichment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a complete, provenance-tagged tactical evidence packet.

    ``bzzoiro_enrichment`` is optional shadow context.  It is consumed only
    when its ``positionValidation`` gate passes (exact fixture date match and
    confirmed lineup presence) so raw Bzzoiro data can never reach the packet
    without explicit quality gating.  API-Football remains authoritative for
    all projection inputs.
    """
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

    # ── Bzzoiro shadow supplement ─────────────────────────────────────────
    # Use Bzzoiro's confirmed lineup position only when:
    #   1. The positionValidation gate has explicitly passed, AND
    #   2. API-Football did not supply a usable position for this player.
    # This keeps API-Football authoritative and prevents unvalidated coordinates
    # from entering the packet.
    _bzz: dict[str, Any] = bzzoiro_enrichment if isinstance(bzzoiro_enrichment, dict) else {}
    _bzz_validation: dict[str, Any] = _bzz.get("positionValidation") or {}
    _bzz_lineup_target: dict[str, Any] = (_bzz.get("lineup") or {}).get("target") or {}
    _bzz_avg_pos: dict[str, Any] = (_bzz.get("target") or {}).get("averagePosition") or {}

    _bzz_position_raw = (
        _bzz_lineup_target.get("position") or _bzz_lineup_target.get("pos")
        if isinstance(_bzz_lineup_target, dict)
        else None
    )
    _bzz_gate_passed = (
        _bzz_validation.get("lineupValid")
        and _bzz_validation.get("fixtureDateMatch") == "exact"
    )
    _bzz_position: str | None = None
    _bzz_position_source = "unavailable"
    if _bzz_gate_passed and _bzz_position_raw and not target_pos:
        # Normalize through the same alias table as API-Football positions.
        from tactical_evidence import normalize_observed_position
        _bzz_position = normalize_observed_position(_bzz_position_raw) or None
        if _bzz_position:
            _bzz_position_source = "bzzoiro_shadow_confirmed_lineup"

    # Effective position: prefer API-Football, fall back to validated Bzzoiro.
    effective_pos = target_pos or _bzz_position or ""
    effective_pos_source = (
        "lineup-provider" if target_pos
        else _bzz_position_source if _bzz_position
        else "unavailable"
    )

    # Average-position grid coordinates from Bzzoiro (shadow only).
    # Only forwarded when the coordinates passed the 0–100 range check.
    _bzz_grid_x: float | None = None
    _bzz_grid_y: float | None = None
    if _bzz_gate_passed and _bzz_validation.get("coordinatesValid") and isinstance(_bzz_avg_pos, dict):
        try:
            _bzz_grid_x = float(_bzz_avg_pos["x"]) if _bzz_avg_pos.get("x") is not None else None
            _bzz_grid_y = float(_bzz_avg_pos["y"]) if _bzz_avg_pos.get("y") is not None else None
        except (TypeError, ValueError):
            _bzz_grid_x = _bzz_grid_y = None
    target_group = _role_group(effective_pos, target_role)
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

    match_script_packet = _formal_match_script(
        market_status=market_status,
        market_script=market_script,
        subject_prob=subject_prob,
        expected_possession=expected_possession,
        possession_is_real=possession_is_real,
        game_script=game_script,
        is_player_home=is_player_home,
    )
    positional_reality = build_positional_reality(
        player=target,
        position=effective_pos,
        role=target_role,
        prop_type=prop_type,
        is_home=is_player_home,
        match_script=match_script_packet,
        history_values=history_values,
    )

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
    if not effective_pos and not target_role:
        limitations.append("player role and position unavailable")
    if _bzz_position and not target_pos:
        limitations.append(
            "player position sourced from Bzzoiro shadow lineup; "
            "API-Football lineup position unavailable for this fixture."
        )
    if not opponent_players:
        limitations.append("opponent lineup unavailable")
    if not formation or not opponent_formation:
        limitations.append("one or both formations unavailable")
    if market_status == "unavailable":
        limitations.append("verified fixture moneyline unavailable")
    if not possession_is_real:
        limitations.append("possession is fallback or unavailable")
    limitations.extend(match_script_packet.get("limitations") or [])
    limitations.extend(positional_reality.get("limitations") or [])
    limitations.append("direct marking is not verified")

    tactical_status = "strong" if (
        target_group != "unknown"
        and opponent_players
        and (market_status == "verified_fixture_moneyline" or possession_is_real)
    ) else "limited"

    # Bzzoiro average-position coordinates: use provider grid position from the
    # API-Football lineup when available; supplement with validated Bzzoiro
    # coordinates (shadow-only) when the API-Football lineup has no x/y data.
    _api_grid_x = target.get("x") if target else None
    _api_grid_y = target.get("y") if target else None
    _grid_x = _api_grid_x if _api_grid_x is not None else _bzz_grid_x
    _grid_y = _api_grid_y if _api_grid_y is not None else _bzz_grid_y
    _grid_source = (
        "api_football" if _api_grid_x is not None
        else "bzzoiro_shadow" if _bzz_grid_x is not None
        else "unavailable"
    )

    return {
        "version": "tactical-shadow-v2",
        "mode": "shadow",
        "status": tactical_status,
        "sourcePolicy": "verified inputs only; inferred mechanisms are labeled",
        "player": {
            "position": effective_pos or None,
            "role": target_role or None,
            "roleGroup": target_group,
            "providerGridPosition": {
                "x": _grid_x,
                "y": _grid_y,
                "source": _grid_source,
            },
            "positionSource": effective_pos_source,
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
        "matchScript": match_script_packet,
        "positionalReality": positional_reality,
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
            "shadowSignal": positional_reality.get("propSignal"),
        },
        "opponentRoleComparison": opponent_matchup,
        "evidence": {
            "opponentAllowedAverage": opponent_allowed_average,
            "opponentAllowedSamples": opponent_allowed_samples,
            "positionComparableSamples": position_comparable_samples,
            "formationData": shape_status,
            "marketData": market_status,
            "possessionData": "verified" if possession_is_real else "fallback_or_unavailable",
            "matchScript": match_script_packet.get("status"),
            "matchScriptConfidence": match_script_packet.get("confidence"),
            "positionalReality": positional_reality.get("zoneSource"),
        },
        "limitations": list(dict.fromkeys(limitations)),
    }