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
from tactical_evidence import build_position_cohort_statement


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
        comparison = "Passing opportunity is compared with the opponent's team-level pressure structure; no player-level press route or trigger is assumed."
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


def build_tactical_explanation(context: dict[str, Any]) -> str:
    """Render a grounded, role-specific explanation from the tactical packet.

    This is intentionally deterministic. It is the user-facing explanation
    fallback when generation is unavailable, so a valid prediction still
    explains the exact role, venue, same-role opponent cohort, H2H split, and
    game environment that shaped the read.
    """
    context = context if isinstance(context, dict) else {}
    player = _clean(context.get("playerName")) or "The player"
    team = _clean(context.get("teamName")) or "the player's team"
    opponent = _clean(context.get("opponentName")) or "the opponent"
    venue = _clean(context.get("venue")).lower() or "unknown"
    venue_label = venue.upper() if venue in {"home", "away"} else "UNKNOWN VENUE"
    position = _clean(context.get("position")) or "unspecified position"
    role = _clean(context.get("role")) or position
    prop_type = _clean(context.get("propType"))
    prop_label = prop_type.replace("_", " ") or "the selected prop"
    recommendation = _clean(context.get("recommendation")).upper() or "PASS"
    line = _num(context.get("line"))
    projection = _num(context.get("projectedValue"))
    p_over = _num(context.get("pOver"))
    p_under = _num(context.get("pUnder"))

    def fmt(value: Any, digits: int = 1) -> str:
        number = _num(value)
        if number is None:
            return "unavailable"
        return f"{number:.{digits}f}".rstrip("0").rstrip(".")

    paragraphs: list[str] = []

    if prop_type in PASS_PROPS:
        mechanism = (
            f"{player} is being evaluated as a {position} / {role}. "
            f"For a {prop_label} prop, that role creates volume through build-out "
            "circulation, back-pass recycling, and the number of settled possessions "
            "the team can sustain—not through attacking touches."
        )
    elif prop_type in DEFENSIVE_PROPS:
        mechanism = (
            f"{player} is being evaluated as a {position} / {role}. "
            f"The {prop_label} mechanism is defensive workload against the opponent's "
            "attacking phases, with role and match script separated from generic form."
        )
    elif prop_type in ATTACK_PROPS:
        mechanism = (
            f"{player} is being evaluated as a {position} / {role}. "
            f"The {prop_label} mechanism depends on his role receiving or creating "
            "attacking sequences against the opponent's defensive shape."
        )
    else:
        mechanism = (
            f"{player} is being evaluated as a {position} / {role}; the explanation "
            f"keeps the {prop_label} mechanism tied to that role rather than using a generic player trend."
        )
    paragraphs.append(
        f"**Tactical role read** — {mechanism} The fixture is {team} at {opponent}, "
        f"with {player} on the {venue_label} side."
    )

    cohort = context.get("positionCohort") or {}
    cohort_avg = _num(cohort.get("avgStatValue") or cohort.get("average"))
    cohort_n = cohort.get("sampleSize") or 0
    cohort_position = _clean(cohort.get("positionShort") or cohort.get("position") or position)
    cohort_venue = _clean(cohort.get("venue")).lower() or venue
    cohort_venue_label = cohort_venue.upper() if cohort_venue in {"home", "away"} else venue_label
    cohort_sentence = ""
    if cohort_avg is not None and cohort_n:
        cohort_position_label = (
            "centre-back (CB)" if cohort_position.upper() in {"CB", "LCB", "RCB"}
            else cohort_position
        )
        cohort_sentence = (
            f"The same-role opponent cohort is the key matchup anchor: {cohort_n} "
            f"comparable {cohort_position_label} players produced {fmt(cohort_avg)} "
            f"{prop_label} against {opponent} in matching {cohort_venue_label.lower()} fixtures. "
            "That is opponent-specific role evidence, not a generic league average."
        )
    else:
        cohort_position_label = (
            "centre-back (CB)" if cohort_position.upper() in {"CB", "LCB", "RCB"}
            else cohort_position
        )
        cohort_sentence = (
            f"No verified same-role {cohort_position_label} cohort is available against {opponent}; "
            "the matchup conclusion therefore stays conservative."
        )

    h2h = context.get("h2h") or {}
    venue_splits = h2h.get("venueSplits") if isinstance(h2h, dict) else {}
    venue_split = (venue_splits or {}).get(venue) if isinstance(venue_splits, dict) else None
    h2h_sentence = ""
    if isinstance(venue_split, dict) and (venue_split.get("sampleSize") or 0) > 0:
        h2h_n = int(venue_split.get("sampleSize") or 0)
        h2h_avg = fmt(venue_split.get("average"))
        h2h_over = fmt(venue_split.get("overPct"))
        h2h_under = fmt(venue_split.get("underPct"))
        h2h_sentence = (
            f"Direct player H2H is split by venue: {player} has {h2h_n} verified "
            f"{venue_label.lower()} appearance{'s' if h2h_n != 1 else ''} against {opponent}, "
            f"averaging {h2h_avg} {prop_label} ({h2h_over}% OVER / {h2h_under}% UNDER). "
            "The all-venue H2H average is not allowed to hide this location-specific split."
        )
    else:
        h2h_n = int(h2h.get("sampleSize") or 0) if isinstance(h2h, dict) else 0
        h2h_avg = fmt(h2h.get("avgVsOpponent")) if isinstance(h2h, dict) else "unavailable"
        if h2h_n:
            h2h_sentence = (
                f"Direct player H2H has {h2h_n} verified appearances against {opponent} "
                f"with an all-venue average of {h2h_avg} {prop_label}, but no usable "
                f"{venue_label.lower()} split was available, so it is not treated as venue-specific evidence."
            )
        else:
            h2h_sentence = (
                f"No verified player H2H appearance is available at this {venue_label.lower()} venue "
                f"against {opponent}."
            )

    paragraphs.append(f"**Same-role opponent evidence** — {cohort_sentence} {h2h_sentence}")

    environment: list[str] = []
    expected_possession = _num(context.get("expectedPossession"))
    opponent_possession = _num(context.get("opponentExpectedPossession"))
    if expected_possession is not None:
        opponent_possession_text = (
            f" versus {fmt(opponent_possession)}% for {opponent}"
            if opponent_possession is not None else ""
        )
        source = _clean(context.get("possessionSource"))
        environment.append(
            f"{team} is projected at {fmt(expected_possession)}% possession"
            f"{opponent_possession_text}"
            f" ({'verified fixture data' if source in {'fixture_stats', 'h2h_fixture_stats'} else 'bounded matchup estimate'})."
        )
    team_pass_average = _num(context.get("teamPassAverage"))
    if team_pass_average is not None and prop_type in PASS_PROPS:
        environment.append(
            f"The team opportunity baseline is {fmt(team_pass_average)} total passes per match."
        )

    understat_pressure = context.get("understatPressure") or {}
    if isinstance(understat_pressure, dict) and understat_pressure.get("status") in {"available", "verified_team_level"}:
        understat_press = understat_pressure.get("opponentPress") or {}
        opponent_press_ppda = _num(understat_press.get("ppda"))
        press_label = _clean(understat_press.get("label")) or "classified"
        press_percentile = _num(understat_press.get("leaguePercentile"))
        press_sample = understat_press.get("sampleSize") or 0
        target_opp_ppda = _num(understat_pressure.get("targetTeamOppPpda"))
        opponent_packet = understat_pressure.get("opponent") or {}
        opponent_name = _clean(opponent_packet.get("name")) or opponent
        press_percentile_text = (
            f", {fmt(press_percentile, 0)}th league percentile"
            if press_percentile is not None else ""
        )
        target_opp_text = (
            f" {team}'s venue-specific OPPDA context is {fmt(target_opp_ppda)}."
            if target_opp_ppda is not None else ""
        )
        if prop_type in PASS_PROPS and press_label in {"high", "above average"}:
            press_effect = (
                "That team-level pressure environment supports the UNDER risk direction "
                "for a pressure-sensitive passing profile."
                if recommendation == "UNDER"
                else "That team-level pressure environment raises pressure-related risk against the OVER."
            )
        elif prop_type in PASS_PROPS:
            press_effect = (
                "That team-level pressure environment does not independently support a strong "
                "UNDER pressure case."
            )
        else:
            press_effect = "This is matchup context, not a direct player assignment."
        environment.append(
            f"Understat's {opponent_name} venue record shows a {press_label} team press: "
            f"PPDA {fmt(opponent_press_ppda)}{press_percentile_text} across {press_sample} matches."
            f"{target_opp_text} {press_effect} This is team-level pressure evidence; "
            "it does not identify a one-to-one marker or exact pressing trigger."
        )

    pressure = context.get("pressureResponse") or {}
    if isinstance(pressure, dict) and pressure.get("status") == "classified":
        high = fmt(pressure.get("highPressurePassesPer90"))
        low = fmt(pressure.get("lowPressurePassesPer90"))
        high_n = pressure.get("highPressureSamples") or 0
        low_n = pressure.get("lowPressureSamples") or 0
        label = _clean(pressure.get("label")) or "classified pressure response"
        environment.append(
            f"{player}'s pressure profile is {label.lower()}: {high} passes/90 in "
            f"high-pressure samples (n={high_n}) versus {low} in lower-pressure samples "
            f"(n={low_n}), which supports the {recommendation} risk direction for this prop. "
            "This is a player-response proxy, separate from the team-level PPDA evidence above."
        )

    script = context.get("gameScript") or {}
    if isinstance(script, dict):
        script_label = _clean(script.get("key_finding") or script.get("dominant"))
        script_probability = _num(script.get("dominant_probability"))
        if script_label:
            probability_text = (
                f" ({fmt(script_probability * 100, 0)}% model probability)"
                if script_probability is not None and script_probability <= 1
                else ""
            )
            environment.append(
                f"The dominant pre-match script is {script_label.lower()}{probability_text}; "
                "an early goal, red card, or substitution can still change the role."
            )
    if environment:
        paragraphs.append("**Match mechanism** — " + " ".join(environment))

    season_average = _num(context.get("seasonAverage"))
    venue_average = _num(context.get("venueAverage"))
    recent_average = _num(context.get("recentAverage"))
    history_parts = []
    if season_average is not None:
        history_parts.append(f"season average {fmt(season_average)}")
    if venue_average is not None:
        history_parts.append(f"{venue_label.lower()} average {fmt(venue_average)}")
    if recent_average is not None:
        history_parts.append(f"recent sample average {fmt(recent_average)}")
    if history_parts:
        uncertainty_band = context.get("uncertaintyBand") or context.get("confidenceInterval")
        if isinstance(uncertainty_band, (list, tuple)) and len(uncertainty_band) >= 2:
            uncertainty_read = (
                f"The 80% projection interval is {fmt(uncertainty_band[0])}–{fmt(uncertainty_band[1])}; "
                "the remaining uncertainty is driven by the pre-match script and the lack of a verified player-level pressure route."
            )
        else:
            uncertainty_read = (
                "The read is probabilistic, not a certainty; the remaining uncertainty is driven by "
                "the pre-match script and the lack of a verified player-level pressure route."
            )
        paragraphs.append(
            "**Decision synthesis** — "
            + "; ".join(history_parts)
            + ". "
            + (
                f"The final projection is {fmt(projection)} against {fmt(line)} with "
                f"P(OVER) {fmt(p_over, 1)}% and P(UNDER) {fmt(p_under, 1)}%, so the "
                f"model calls {recommendation}."
                if projection is not None and line is not None
                else f"The model calls {recommendation} from the evidence above."
            )
            + " "
            + uncertainty_read
        )

    return "\n\n".join(paragraphs)


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
    understat_pressure: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a complete, provenance-tagged tactical evidence packet.

    API-Football remains authoritative for lineup and position evidence.
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

    effective_pos = target_pos or ""
    effective_pos_source = "lineup-provider" if target_pos else "unavailable"
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
        opponent_note = build_position_cohort_statement(
            opponent=prediction.get("opponentName") or prediction.get("opponent"),
            prop_type=prop_type,
            position=effective_pos or target_group,
            average=opponent_allowed_average,
            sample_size=opponent_allowed_samples,
            venue=prediction.get("venue"),
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
    if not isinstance(understat_pressure, dict) or understat_pressure.get("status") not in {"available", "verified_team_level"}:
        limitations.append("team-level opponent PPDA is unavailable")
    limitations.append("player-level pressure route or pressing trigger is not verified")

    tactical_status = "strong" if (
        target_group != "unknown"
        and opponent_players
        and (market_status == "verified_fixture_moneyline" or possession_is_real)
    ) else "limited"

    _api_grid_x = target.get("x") if target else None
    _api_grid_y = target.get("y") if target else None
    _grid_x = _api_grid_x
    _grid_y = _api_grid_y
    _grid_source = (
        "api_football" if _api_grid_x is not None
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
        "understatPressure": understat_pressure or {
            "status": "unavailable",
            "projectionInfluence": "explanation_only",
        },
        "recentOpponentBlockProfiles": (
            (prediction.get("tacticalContext") or {}).get("recentOpponentBlockProfiles")
            if isinstance(prediction.get("tacticalContext"), dict)
            else None
        ),
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
            "understatPressure": (
                "available"
                if isinstance(understat_pressure, dict)
                and understat_pressure.get("status") in {"available", "verified_team_level"}
                else "unavailable"
            ),
        },
        "limitations": list(dict.fromkeys(limitations)),
    }