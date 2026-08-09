"""Deterministic, auditable explanations for Reverse Picks predictions.

This module never calls a language model and never invents evidence. It turns
the final projection ledger and persisted model inputs into concise prose that
can be reproduced from the same prediction inputs.
"""

from __future__ import annotations

from typing import Any

from tactical_evidence import build_position_cohort_statement


async def unavailable_explanation(*_args: Any, **_kwargs: Any) -> str:
    """Compatibility response for features that require text/image generation."""
    return ""


def _num(value: Any, default: float | None = None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _fmt(value: Any, digits: int = 1) -> str:
    number = _num(value)
    if number is None:
        return "—"
    if number == int(number):
        return str(int(number))
    return f"{number:.{digits}f}"


def _direction(recommendation: Any) -> str:
    value = str(recommendation or "PASS").upper()
    return value if value in {"OVER", "UNDER", "PASS"} else "PASS"


# Props where higher possession meaningfully lifts the stat
_POSSESSION_BOOSTED_PROPS = {
    "pass_attempts", "passes", "key_passes", "dribbles", "crosses",
    "shots", "shots_on_target",
}
# Props where lower possession meaningfully lifts the stat
_POSSESSION_SUPPRESSED_PROPS = {
    "tackles", "interceptions", "clearances", "blocks", "saves",
}


def _prop_label(prop_raw: str) -> str:
    return prop_raw.replace("_", " ")


def _possession_narrative(
    prop: str,
    player_poss: float | None,
    opp_poss: float | None,
    venue: str,
) -> str | None:
    """Return a one-sentence tactical possession note, or None."""
    if player_poss is None:
        return None
    prop_norm = prop.replace(" ", "_").lower()
    if prop_norm in _POSSESSION_BOOSTED_PROPS:
        if player_poss >= 60:
            return (
                f"Ball dominance ({player_poss:.0f}% expected possession) creates "
                f"substantially more opportunities for {prop.replace('_', ' ')} — "
                f"the higher the share, the more passing sequences the player initiates."
            )
        if player_poss >= 52:
            return (
                f"Slight possession edge ({player_poss:.0f}%) provides a modest lift "
                f"to {prop.replace('_', ' ')} volume."
            )
        if player_poss <= 38:
            return (
                f"Defensive shape expected ({player_poss:.0f}% possession): "
                f"low-possession teams produce fewer natural "
                f"{prop.replace('_', ' ')} sequences; counter-attack volume is compressed."
            )
    elif prop_norm in _POSSESSION_SUPPRESSED_PROPS:
        if player_poss <= 42:
            return (
                f"Lower possession ({player_poss:.0f}%) means more defensive action — "
                f"the model expects elevated {prop.replace('_', ' ')} from dealing with "
                f"sustained opposition pressure."
            )
        if player_poss >= 60:
            return (
                f"High possession ({player_poss:.0f}%) limits defensive involvement — "
                f"fewer opposition attacks means fewer natural "
                f"{prop.replace('_', ' ')} opportunities."
            )
    return None


def _role_mechanism(position: str, role: str, prop_raw: str) -> str | None:
    """Describe only the role-to-stat mechanism supported by the role resolver.

    This is intentionally a bounded vocabulary.  It does not claim a player
    occupies a zone, presses a trigger, or receives between lines unless that
    information is actually present in the resolver's position/role output.
    """
    pos = str(position or "").upper().replace(" ", "")
    role_norm = str(role or "").lower()
    prop = prop_raw.lower()

    if prop in {"pass_attempts", "passes", "key_passes", "crosses"}:
        if pos in {"GK", "GOALKEEPER"}:
            return "As the goalkeeper, the pass volume is primarily a build-up and restart signal: it rises when the team recycles possession through the back line and falls when the team plays longer or spends more time defending."
        if pos in {"LB", "RB", "LWB", "RWB", "FB"} or "fullback" in role_norm or "wingback" in role_norm:
            return "As a fullback/wingback, the pass volume is tied to outlet work and width in possession; it can accumulate passes when the team controls territory, but it is less stable when the player is pinned into defensive actions."
        if pos in {"CB", "SW", "DEF"} or "ball-playing defender" in role_norm or "stopper" in role_norm:
            return "As a central defender, the pass volume is primarily circulation behind the attack: it depends on how often the team can retain possession and reset through the first line rather than on final-third touches."
        if pos in {"DM", "CDM"} or "deep" in role_norm or "regista" in role_norm or "ball winner" in role_norm:
            return "As a deeper midfielder, the pass volume is tied to first- and second-phase circulation: possession control creates repeated outlet and recycle actions before the ball reaches the attacking line."
        if pos in {"CM", "MID"} or "box-to-box" in role_norm or "playmaker" in role_norm or "mezzala" in role_norm:
            return "As a central midfielder, the pass volume is tied to linking phases of play; a possession edge gives the player more chances to connect buildup to the attacking unit."
        if pos in {"AM", "CAM"} or "creator" in role_norm or "advanced playmaker" in role_norm:
            return "As an advanced creator, the pass volume depends on how much settled possession reaches the attacking third; the role is more about connecting and progressing attacks than raw defensive circulation."
        if pos in {"LW", "RW", "LM", "RM", "WING"} or "winger" in role_norm or "wide" in role_norm:
            return "As a wide attacker, the pass volume depends on whether the team can establish possession on that side; the role is more variable than a central midfielder because touches are affected by width and direct play."
        if pos in {"ST", "CF", "SS", "FWD", "FW"} or "forward" in role_norm or "striker" in role_norm:
            return "As a forward, the pass volume is naturally more volatile: it depends on link play and how often the team reaches the final third, rather than the deeper circulation that drives defender and midfielder pass counts."

    if prop in {"shots", "shots_on_target", "goals"}:
        if pos in {"ST", "CF", "SS", "FWD", "FW"} or "forward" in role_norm or "striker" in role_norm:
            return "The role is attack-ending rather than possession-recycling, so the relevant tactical path is whether the team can sustain final-third entries and create attempts for the forward."
        if pos in {"LW", "RW", "LM", "RM", "WING", "AM", "CAM"} or "winger" in role_norm or "creator" in role_norm:
            return "The role can generate attempts when the team establishes attacking possession, but shot volume remains sensitive to whether the player is used as a creator or a final action."
        if pos in {"CM", "MID", "DM", "CDM"}:
            return "The role is not primarily attack-ending, so attempts depend on late arrivals and the team's ability to sustain pressure rather than on possession alone."

    if prop in {"tackles", "interceptions", "clearances", "blocks", "fouls_committed"}:
        if pos in {"GK", "GOALKEEPER"}:
            return "For a goalkeeper, defensive volume is driven by opposition entries and shots rather than possession control by the player's own team."
        if pos in {"CB", "SW", "DEF", "LB", "RB", "LWB", "RWB", "FB", "DM", "CDM"} or "def" in role_norm or "ball winner" in role_norm:
            return "The role is defensive, so the prop is driven by time spent without the ball and the number of opposition attacks reaching the player's zone."
        return "The role is not primarily defensive, so this prop depends more on match state and defensive workload than on the player's normal attacking responsibilities."

    if prop in {"dribbles", "dribbles_attempts"}:
        if pos in {"LW", "RW", "LM", "RM", "WING", "AM", "CAM", "ST", "CF", "SS"} or "wide" in role_norm or "creator" in role_norm or "forward" in role_norm:
            return "The role has an attacking ball-carrying pathway, but dribble volume still depends on whether the team reaches the player with space and permission to attack a defender."
        return "The resolved role is not primarily a ball-carrying attacking role, so dribble volume is less directly supported by role identity."

    if prop in {"saves", "goalie_saves"} and pos in {"GK", "GOALKEEPER"}:
        return "For a goalkeeper, save volume is an opponent-activity stat: it rises with shots on target faced and can remain high even when the goalkeeper's team has little possession."
    return None


def _tactical_mechanism_lines(context: dict[str, Any], prop_raw: str) -> list[str]:
    """Build evidence-gated, player-specific tactical sentences."""
    if not isinstance(context, dict):
        return []
    lines: list[str] = []
    position = str(context.get("position") or "")
    role = str(context.get("role") or "")
    prop = _prop_label(prop_raw)

    mechanism = _role_mechanism(position, role, prop_raw)
    if mechanism and (position or role):
        role_display = " · ".join(part for part in (position, role) if part)
        lines.append(f"Role anchor: **{role_display}** — {mechanism}")

    player_poss = _num(context.get("expectedPossession"))
    opp_poss = _num(context.get("opponentExpectedPossession"))
    poss_source = str(context.get("possessionSource") or "")
    if player_poss is not None and opp_poss is not None and poss_source == "verified match dominance":
        if prop_raw in _POSSESSION_BOOSTED_PROPS and player_poss >= 55:
            lines.append(
                f"Match mechanism: verified {player_poss:.0f}% expected possession should increase "
                f"the team's settled sequences, which supports {prop} through the {position or 'player'} role."
            )
        elif prop_raw in _POSSESSION_SUPPRESSED_PROPS and player_poss <= 45:
            lines.append(
                f"Match mechanism: verified {player_poss:.0f}% expected possession leaves the team defending more often, "
                f"which supports defensive {prop} workload."
            )
        elif prop_raw in _POSSESSION_BOOSTED_PROPS and player_poss <= 45:
            lines.append(
                f"Match mechanism: verified {player_poss:.0f}% expected possession limits settled attacking sequences, "
                f"which is a constraint on {prop} volume."
            )

    tier = str(context.get("opponentProfileTier") or "")
    diff = _num(context.get("opponentProfileDiffPct"))
    allowed = _num(context.get("opponentAllowedAverage"))
    n_allowed = int(_num(context.get("opponentAllowedSamples")) or 0)
    if allowed is not None and n_allowed >= 3:
        if diff is not None:
            comparison = "above" if diff > 0 else "below" if diff < 0 else "near"
            cohort_statement = build_position_cohort_statement(
                opponent=context.get("opponent") or "Opponent",
                prop_type=prop_raw,
                position=context.get("position") or context.get("role"),
                average=allowed,
                sample_size=n_allowed,
                venue=context.get("venue"),
            )
            lines.append(
                f"Opponent mechanism: {cohort_statement or 'comparable player sample is available'}, "
                f"{comparison} the player's baseline by {abs(diff):.0f}%"
                f"{f' ({tier})' if tier else ''}."
            )
        else:
            cohort_statement = build_position_cohort_statement(
                opponent=context.get("opponent") or "Opponent",
                prop_type=prop_raw,
                position=context.get("position") or context.get("role"),
                average=allowed,
                sample_size=n_allowed,
                venue=context.get("venue"),
            )
            lines.append(
                f"Opponent mechanism: {cohort_statement or 'Comparable player sample is available.'}"
            )

    formation = context.get("lineupFormation")
    opponent_formation = context.get("opponentFormation")
    lineup_status = str(context.get("lineupStatus") or "")
    if formation and opponent_formation:
        shape = f"{formation or 'unknown'} vs {opponent_formation or 'unknown'}"
        lines.append(
            f"Shape evidence: the available {'confirmed' if lineup_status == 'confirmed' else 'predicted'} "
            f"lineup data shows **{shape}**; no additional zone or matchup claim is made without event-level data."
        )
    elif formation or opponent_formation:
        lines.append(
            "Shape evidence unavailable: only one lineup formation was available, "
            "so no formation matchup is claimed."
        )

    tempo = str(context.get("tempo") or "")
    total_goals = _num(context.get("expectedTotalGoals"))
    if tempo and total_goals is not None:
        lines.append(
            f"Game-state context: the team model classifies the match as **{tempo} tempo** "
            f"({total_goals:.1f} expected total goals), which affects the number of available sequences but "
            f"does not replace the player's role evidence."
        )

    if context.get("favoriteDampeningApplied"):
        note = context.get("favoriteDampeningNote")
        if note:
            lines.append(f"Game-management risk: {note}.")

    return lines


def build_deterministic_explanation(
    prediction: dict[str, Any],
    ledger: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Attach the canonical model explanation to a finalized prediction.

    All numeric claims come from ``ledger`` or the prediction's own recorded
    snapshots. Applied ledger factors are listed in sequence order, while
    unavailable inputs are disclosed as limitations instead of being treated
    as zero evidence.
    """
    result = prediction
    ledger = ledger or result.get("factorLedger") or {}
    final = ledger.get("final") or {}
    factors = ledger.get("factors") or []
    snapshot = result.get("modelInputSnapshot") or {}
    samples = snapshot.get("sampleCounts") or {}

    # ── Core outputs ──────────────────────────────────────────────────────────
    recommendation = _direction(final.get("recommendation") or result.get("recommendation"))
    projection = final.get("projectedValue", result.get("projectedValue"))
    line = final.get("line", result.get("line"))
    p_over = final.get("pOver", (result.get("bayesianMetrics") or {}).get("pOver"))
    p_under = final.get("pUnder", (result.get("bayesianMetrics") or {}).get("pUnder"))
    confidence = final.get("confidenceScore", result.get("confidenceScore"))
    confidence_level = final.get("confidenceLevel", result.get("confidenceLevel", "Medium"))
    edge = final.get("edge")
    edge_rating = final.get("edgeRating", result.get("edgeRating", "NO EDGE"))
    safety = final.get("safetyRating", result.get("safetyRating", "RISKY"))

    player = (result.get("player") or {}).get("name") or result.get("playerName") or "This player"
    prop_raw = str(result.get("propType") or "prop")
    prop = _prop_label(prop_raw)
    opponent = result.get("opponent") or result.get("opponentName")
    team = (result.get("player") or {}).get("team") or result.get("teamName")
    venue = str(result.get("venue") or "").lower()

    # ── Bayesian / matchup context ────────────────────────────────────────────
    bm = result.get("bayesianMetrics") or {}
    match_dominance = result.get("matchDominance") or {}
    match_factors = result.get("matchFactors") or {}
    analysis_summary = result.get("analysisSummary") or {}
    tactical_context = result.get("tacticalContext") or {}
    tactical_intelligence = result.get("tacticalIntelligence") or {}

    prior_mean = bm.get("priorMean")
    momentum_label = str(bm.get("momentumLabel") or "").upper()
    momentum_effect = _num(bm.get("momentumEffect"), 0) or 0.0
    covariate_adj = _num(bm.get("covariateAdjustment"), 0) or 0.0
    prior_samples = _num(bm.get("priorSamples")) or _num(samples.get("playerLogs"))

    venue_avg = _num(analysis_summary.get("venueAverage"))
    opp_allowed_avg = _num(analysis_summary.get("opponentAllowedAverage"))

    # Expected possession — prefer the canonical tactical context packet.
    # Legacy locations remain supported for older cached predictions.
    context_poss_player = _num(tactical_context.get("expectedPossession"))
    context_poss_opp = _num(tactical_context.get("opponentExpectedPossession"))
    if context_poss_player is not None and context_poss_opp is not None:
        expected_poss_player = context_poss_player
        expected_poss_opp = context_poss_opp
    else:
        expected_poss_player = None
        expected_poss_opp = None

    # Expected possession — resolve from legacy locations when necessary.
    home_poss = _num(
        match_dominance.get("homeExpectedPoss")
        or match_dominance.get("home_expected_poss")
    )
    away_poss = _num(
        match_dominance.get("awayExpectedPoss")
        or match_dominance.get("away_expected_poss")
    )
    if expected_poss_player is None and home_poss and away_poss:
        if venue == "home":
            expected_poss_player, expected_poss_opp = home_poss, away_poss
        elif venue == "away":
            expected_poss_player, expected_poss_opp = away_poss, home_poss
    elif expected_poss_player is None and match_factors.get("expectedPossession"):
        expected_poss_player = _num(match_factors["expectedPossession"])
        if expected_poss_player:
            expected_poss_opp = round(100 - expected_poss_player, 1)

    # ── AVOID evidence ────────────────────────────────────────────────────────
    avoid_hist_rate = _num(final.get("propHistoricalRate", result.get("propHistoricalRate")))
    avoid_hist_n = final.get("propHistoricalN") or result.get("propHistoricalN")
    try:
        avoid_hist_n = int(avoid_hist_n) if avoid_hist_n is not None else None
    except (TypeError, ValueError):
        avoid_hist_n = None

    def _avoid_evidence_str(direction: str) -> str:
        """Return an inline evidence string for AVOID ratings."""
        if avoid_hist_rate is not None and avoid_hist_n:
            return (
                f"{avoid_hist_rate:.0f}% historical {direction} hit rate, "
                f"n={avoid_hist_n} settled events"
            )
        if avoid_hist_rate is not None:
            return f"{avoid_hist_rate:.0f}% historical {direction} hit rate"
        return "calibrated empirical hit-rate data"

    # ── Verdict ───────────────────────────────────────────────────────────────
    if recommendation == "PASS":
        _edge_num = _num(edge)
        if safety in {"AVOID", "AVOID**"}:
            _pass_reason = (
                f"safety is {safety} ({_avoid_evidence_str('OVER/UNDER')})"
            )
        elif _edge_num is not None and _edge_num < 2.0:
            _pass_reason = f"the {_fmt(edge)}-unit edge is within the model's noise range"
        else:
            _pass_reason = "there is no actionable OVER/UNDER edge"
        verdict = (
            f"**Verdict** — Reverse Picks projects **{_fmt(projection)}**, "
            f"within noise of the {_fmt(line)} line. PASS issued because {_pass_reason}."
        )
    else:
        relation = "above" if recommendation == "OVER" else "below"
        probability = p_over if recommendation == "OVER" else p_under
        verdict = (
            f"**Verdict** — Reverse Picks projects **{_fmt(projection)}**, "
            f"{relation} the {_fmt(line)} line: **{recommendation}** at "
            f"**{_fmt(probability, 0)}%** modeled probability."
        )

    # ── Matchup ───────────────────────────────────────────────────────────────
    matchup = ""
    if team or opponent or venue:
        location_phrase = f" from the **{venue}** side" if venue in {"home", "away"} else ""
        poss_phrase = ""
        if expected_poss_player is not None and expected_poss_opp is not None:
            poss_source = str(tactical_context.get("possessionSource") or "")
            poss_label = (
                "Verified expected possession"
                if poss_source in {"fixture_stats", "h2h_fixture_stats"}
                else "Model fallback possession estimate"
            )
            poss_phrase = (
                f" {poss_label}: **{expected_poss_player:.0f}%** "
                f"(vs {expected_poss_opp:.0f}% for {opponent or 'opponent'})."
            )
        matchup = (
            f"**Matchup**\n{player} is evaluated for {prop}{location_phrase}"
            f"{f' for {team}' if team else ''}"
            f"{f' against {opponent}' if opponent else ''}."
            f"{poss_phrase}"
        )

    # ── Tactical context ──────────────────────────────────────────────────────
    tactical_lines: list[str] = []

    # Player-specific tactical mechanism. This is evidence-gated: if the role
    # resolver or matchup packet is missing, no invented tactical claim is
    # displayed.
    tactical_lines.extend(_tactical_mechanism_lines(tactical_context, prop_raw))

    # Tactical intelligence packet — cite the same shadow-model evidence that
    # the UI displays. It is explanatory context only until settled-pick
    # calibration proves that a tactical signal should move the projection.
    if isinstance(tactical_intelligence, dict) and tactical_intelligence:
        ti_player = tactical_intelligence.get("player") or {}
        ti_lineup = tactical_intelligence.get("lineup") or {}
        ti_market = tactical_intelligence.get("marketGameScript") or {}
        ti_poss = tactical_intelligence.get("possessionGameScript") or {}
        ti_compare = tactical_intelligence.get("opponentRoleComparison") or {}
        ti_mechanism = tactical_intelligence.get("propMechanism") or {}
        if ti_market.get("classification") and ti_market.get("status") == "verified_fixture_moneyline":
            _market_label = str(ti_market.get("classification")).replace("_", " ")
            tactical_lines.append(
                f"Pre-match game script: **{_market_label}** from the fixture market. "
                f"It is read together with possession and role evidence, not counted twice."
            )
        if ti_poss.get("classification") and ti_poss.get("status") == "verified":
            tactical_lines.append(
                f"Possession game script: **{str(ti_poss.get('classification')).replace('_', ' ')}** "
                f"at {_fmt(ti_poss.get('expectedPlayerTeamPossession'), 0)}% expected player-team possession "
                f"based on the verified match context."
            )
        if ti_mechanism.get("opponentNote"):
            tactical_lines.append(str(ti_mechanism["opponentNote"]))
        if ti_compare.get("comparison"):
            tactical_lines.append(str(ti_compare["comparison"]))
        if ti_compare and not ti_compare.get("directMarkingVerified", False):
            tactical_lines.append(
                "Direct marking, exact operating zones, and average positions are not verified; "
                "the tactical comparison remains nominal."
            )

    # Player-specific pressure response is a descriptive historical signal.
    # It is deliberately not treated as an applied projection factor until it
    # has passed leakage-safe walk-forward validation.
    pressure_response = tactical_context.get("pressureResponse") or result.get("pressureResponse") or {}
    if isinstance(pressure_response, dict) and pressure_response.get("status") == "classified":
        pressure_label = pressure_response.get("label") or "classified"
        high_n = pressure_response.get("highPressureSamples", 0)
        low_n = pressure_response.get("lowPressureSamples", 0)
        high_avg = pressure_response.get("highPressurePassesPer90")
        low_avg = pressure_response.get("lowPressurePassesPer90")
        multiplier = pressure_response.get("pressureMultiplier")
        tactical_lines.append(
            f"Pressure-response profile: **{pressure_label}** — "
            f"{high_n} low-possession games averaged {_fmt(high_avg)} passes/90 versus "
            f"{low_n} higher-possession games at {_fmt(low_avg)} "
            f"(shrunk multiplier {_fmt(multiplier, 2)})."
        )
        tactical_lines.append(
            "This is a team-possession pressure proxy, not event-level pressure data; "
            "it is context-only until walk-forward validation supports using it in the projection."
        )
    elif (
        prop_raw in {"pass_attempts", "passes"}
        and isinstance(pressure_response, dict)
        and pressure_response.get("status") == "insufficient_evidence"
    ):
        tactical_lines.append(
            "Pressure-response profile unavailable: there are not enough qualifying "
            "low- and high-possession appearances to classify this player."
        )

    # StatsBomb is a public, historical event-level ground-truth layer.  It is
    # deliberately rendered as context only: the packet never changes the
    # projection, calibration, or settlement result.
    statsbomb = tactical_context.get("statsbombEnrichment") or result.get("statsbombEnrichment") or {}
    if isinstance(statsbomb, dict) and statsbomb.get("available"):
        sb_metrics = statsbomb.get("eventMetrics") or {}
        sb_match = statsbomb.get("match") or {}
        sb_ppda = sb_metrics.get("ppda")
        sb_pressures = sb_metrics.get("pressureEvents")
        sb_pass_pressure = (sb_metrics.get("passesUnderPressure") or {}).get("opponent")
        sb_bits = []
        if sb_ppda is not None:
            sb_bits.append(f"event-derived PPDA **{_fmt(sb_ppda, 2)}**")
        if sb_pressures is not None:
            sb_bits.append(f"**{sb_pressures}** pressure events")
        if sb_pass_pressure is not None:
            sb_bits.append(f"**{sb_pass_pressure}** opponent passes under pressure")
        if sb_bits:
            tactical_lines.append(
                f"Public historical event evidence for "
                f"{sb_match.get('homeTeam', 'home')} vs {sb_match.get('awayTeam', 'away')}: "
                + ", ".join(sb_bits)
                + ". This is shadow-only context and does not move the projection."
            )
        else:
            tactical_lines.append(
                "Historical event coverage exists for this match, but the requested event metrics are unavailable; "
                "no projection adjustment was applied."
            )

    # Venue average
    if venue_avg is not None and prior_mean is not None:
        _venue_label = venue.capitalize() if venue in {"home", "away"} else "Venue"
        _diff = venue_avg - prior_mean
        _diff_str = f"{_diff:+.1f} vs season average" if abs(_diff) >= 0.5 else "in line with season average"
        tactical_lines.append(
            f"{_venue_label} average: {_fmt(venue_avg)} {prop} "
            f"({_diff_str} of {_fmt(prior_mean)})."
        )
    elif venue_avg is not None:
        _venue_label = venue.capitalize() if venue in {"home", "away"} else "Venue"
        tactical_lines.append(f"{_venue_label} average: {_fmt(venue_avg)} {prop}.")

    # Legacy opponent average fallback for cached predictions that predate the
    # tactical context packet.
    if opp_allowed_avg is not None and not any("Opponent mechanism:" in line_ for line_ in tactical_lines):
        cohort_statement = build_position_cohort_statement(
            opponent=opponent or "Opponent",
            prop_type=prop_raw,
            position=tactical_context.get("position") or tactical_context.get("role"),
            average=opp_allowed_avg,
            sample_size=tactical_context.get("opponentAllowedSamples"),
            venue=venue,
        )
        fallback_opponent = opponent or "Opponent"
        tactical_lines.append(
            f"{cohort_statement or f'{fallback_opponent} matchup evidence is available'} "
            "— used as a matchup-context anchor."
        )

    # Momentum
    if momentum_label and momentum_label not in {"STABLE", ""}:
        _mom_dir = "trending above" if "HOT" in momentum_label else "trending below"
        _mom_val = f" ({momentum_effect:+.1f} per game)" if abs(momentum_effect) >= 0.5 else ""
        tactical_lines.append(
            f"Recent-form momentum: **{momentum_label.title()}**{_mom_val} — "
            f"recent games are {_mom_dir} the seasonal baseline, pulling the projection accordingly."
        )

    # Covariate signal
    if abs(covariate_adj) >= 1.0:
        _cov_dir = "upward" if covariate_adj > 0 else "downward"
        tactical_lines.append(
            f"Match-context covariates (possession, opponent press, game state) "
            f"shift the projection {_cov_dir} by {abs(covariate_adj):.1f}."
        )

    # Sample size disclosure
    if prior_samples is not None:
        n = int(prior_samples)
        if n < 5:
            tactical_lines.append(
                f"Thin evidence base ({n} qualifying logs) limits projection confidence — "
                f"the model relies more on positional and league-level priors."
            )
        elif n <= 10:
            tactical_lines.append(f"Projection draws on {n} qualifying game logs.")

    tactical_block = (
        "**Tactical context**\n" + "\n".join(f"- {line_}" for line_ in tactical_lines)
        if tactical_lines else ""
    )

    # ── Applied model factors ─────────────────────────────────────────────────
    applied = [
        factor for factor in factors
        if str(factor.get("status") or "applied").lower() in {"applied", "measured"}
        and factor.get("reason")
    ]
    applied.sort(key=lambda item: item.get("sequence", 0))
    factor_lines = []
    for factor in applied[:8]:
        label = factor.get("label") or factor.get("name") or "Model factor"
        reason = str(factor.get("reason") or "").strip()
        before = factor.get("before")
        after = factor.get("after")
        movement = ""
        if before is not None and after is not None and _num(before) != _num(after):
            movement = f" ({_fmt(before)} → {_fmt(after)})"
        factor_lines.append(f"- **{label}**{movement}: {reason}")
    factor_block = "**Applied model factors**\n" + "\n".join(factor_lines) if factor_lines else ""

    # ── Limitations ───────────────────────────────────────────────────────────
    limitations = []
    for factor in factors:
        status = str(factor.get("status") or "").lower()
        if status in {"unavailable", "skipped", "warning"}:
            label = factor.get("label") or factor.get("name") or "Input"
            reason = factor.get("reason") or "not available"
            limitations.append(f"- {label}: {reason}")
    if samples:
        for key, label in (
            ("playerLogs", "player game logs"),
            ("h2hPlayerGames", "head-to-head player games"),
            ("comparableGames", "comparable matchups"),
        ):
            if key in samples and samples.get(key) in (None, 0):
                limitations.append(f"- {label}: unavailable")
    limitation_block = (
        "**Limitations**\n" + "\n".join(dict.fromkeys(limitations[:6]))
        if limitations else
        "**Limitations**\nThe result is model-based; late lineup, minutes, or match-state changes can alter the outcome."
    )

    # ── Confidence & risk ─────────────────────────────────────────────────────
    confidence_line = (
        f"**Confidence and risk**\nDisplayed confidence is **{_fmt(confidence, 0)}% "
        f"({confidence_level})**. Edge: **{_fmt(edge)}** ({edge_rating}); safety: **{safety}**."
    )
    if recommendation == "PASS":
        confidence_line += " Confidence does not override the PASS decision."
    if safety in {"AVOID", "AVOID**"}:
        _avoid_dir = recommendation if recommendation in {"OVER", "UNDER"} else "OVER/UNDER"
        _avoid_detail = _avoid_evidence_str(_avoid_dir)
        confidence_line += (
            f" **AVOID**: {_avoid_detail} — empirical data does not support the math edge; "
            f"treat as informational only."
        )

    # ── Summary (sharpSummary) ────────────────────────────────────────────────
    summary = (
        f"Reverse Picks model: {_fmt(projection)} {recommendation} {_fmt(line)} "
        f"with {_fmt(max(_num(p_over, 50) or 50, _num(p_under, 50) or 50), 0)}% "
        f"modeled probability and {_fmt(edge)} edge. "
        f"Confidence is {_fmt(confidence, 0)}% ({confidence_level}); safety is {safety}."
    )

    sections = [verdict, matchup, tactical_block, factor_block, confidence_line, limitation_block]
    result["tacticalBreakdown"] = "\n\n".join(section for section in sections if section)
    result["reasoning"] = result["tacticalBreakdown"]
    result["sharpSummary"] = summary
    result["aiSource"] = "model"
    result["aiPending"] = False
    result["explanationSource"] = "deterministic_model"
    result["explanationVersion"] = "reverse-picks-model-v2"
    return result

def _basketball_tactical_lines(prediction: dict[str, Any], prop: str, sport: str = "nba") -> list[str]:
    """Build evidence-gated tactical context lines for NBA/WNBA picks.

    Thresholds are sport-specific:
    - NBA league average defensive rating ≈ 113 pts/100 possessions
    - WNBA league average defensive rating ≈ 100 pts/100 possessions
    Both engines apply a rest boost at rest_days >= 3.
    """
    lines: list[str] = []
    opp_def = _num(prediction.get("oppDefRating"))
    rest_days = prediction.get("restDays")
    venue = str(prediction.get("venue") or "").lower()
    position = str(prediction.get("position") or "")
    prop_type = str(prediction.get("propType") or "").lower()

    # Opponent defensive rating: thresholds are relative to each league's average
    # NBA avg ~113 pts/100 (engine constant); WNBA avg ~100 pts/100 (engine constant)
    # Higher rating = worse defense (allows more points) = favorable for scorers.
    # Tier boundaries use delta from league_avg so WNBA and NBA scale consistently.
    if opp_def is not None:
        league_avg = 100.0 if str(sport or "").lower() == "wnba" else 113.0
        delta_pct = round((opp_def - league_avg) / league_avg * 100, 1)
        # delta_pct < 0 → defense is better than average (suppresses scoring)
        # delta_pct > 0 → defense is worse than average (lifts scoring)
        if delta_pct <= -4.5:
            tier = "elite"
            direction = "significantly suppresses"
        elif delta_pct <= -1.5:
            tier = "strong"
            direction = "suppresses"
        elif delta_pct < 1.5:
            tier = "average"
            direction = "does not materially adjust"
        elif delta_pct < 4.5:
            tier = "below-average"
            direction = "lifts"
        else:
            tier = "weak"
            direction = "significantly lifts"
        # Which props each engine adjusts with opp_def_mult:
        # NBA: points/scoring combos, assists, rebounds, steals, blocks
        # WNBA: points/scoring combos, assists only (not rebounds — no WNBA engine adjustment)
        is_wnba = str(sport or "").lower() == "wnba"
        _scoring_props = {
            "points", "three_pointers", "field_goals", "free_throws",
            "pts_reb_ast", "pts_reb", "pts_ast", "fantasy_points",
        }
        if prop_type in _scoring_props:
            lines.append(
                f"Opponent defense: **{opp_def:.0f} pts/100 possessions** ({tier}, {delta_pct:+.1f}% vs "
                f"league average) — this defense {direction} scoring opportunities."
            )
        elif prop_type in {"rebounds", "reb_ast"} and not is_wnba:
            # NBA engine adjusts rebounds: weak defense (positive delta) = more pace = more rebounds
            rebound_note = (
                "weaker defenses generate more pace and missed-shot opportunities, lifting rebounding volume"
                if delta_pct >= 1.5 else
                "stronger defenses limit transition pace, moderately reducing rebounding opportunities"
                if delta_pct <= -1.5 else
                "this defense is near league average for rebounding context"
            )
            lines.append(
                f"Opponent defense: **{opp_def:.0f} pts/100** ({tier}) — {rebound_note}."
            )
        elif prop_type in {"assists", "pts_ast"}:
            # Both NBA and WNBA engines adjust assists: tight defense reduces assisted buckets
            assist_note = (
                "tight defenses pressure ball-handlers and reduce clean assisted opportunities"
                if delta_pct <= -1.5 else
                "permissive defenses allow more clean catch-and-shoot and drive-and-kick plays"
                if delta_pct >= 1.5 else
                "average defensive pressure — no material assist adjustment"
            )
            lines.append(
                f"Opponent defense: **{opp_def:.0f} pts/100** ({tier}) — {assist_note}."
            )
        elif prop_type in {"steals", "blocks"} and not is_wnba:
            # NBA engine adjusts steals/blocks; WNBA does not
            lines.append(
                f"Opponent defensive rating: **{opp_def:.0f} pts/100** ({tier}, {delta_pct:+.1f}% vs "
                f"league average) — factored into the engine's {prop_type} adjustment."
            )
        elif not is_wnba or prop_type in _scoring_props | {"assists"}:
            # Show context for NBA props not listed above; skip for WNBA props with no engine adjustment
            lines.append(
                f"Opponent defensive rating: **{opp_def:.0f} pts/100 possessions** "
                f"({tier}, {delta_pct:+.1f}% vs league average)."
            )

    # Rest days — both NBA and WNBA engines apply the boost at rest_days >= 3
    if rest_days is not None:
        try:
            rest_int = int(rest_days)
            if rest_int == 0:
                lines.append(
                    "Rest: **back-to-back game** — the engine applies a fatigue discount "
                    "because same-day fatigue measurably reduces output."
                )
            elif rest_int == 1:
                lines.append("Rest: **1 day** — standard single-day rest, no significant fatigue effect.")
            elif rest_int >= 3:
                lines.append(
                    f"Rest: **{rest_int} days** — well-rested; the engine applies a modest "
                    f"performance boost for most props."
                )
        except (TypeError, ValueError):
            pass

    # Venue context
    if venue in {"home", "away"}:
        lines.append(
            f"Venue: **{venue.capitalize()}** — home court carries a small but consistent "
            f"advantage for scoring and efficiency props."
            if venue == "home" else
            f"Venue: **Away** — road games carry a modest performance discount, particularly "
            f"for scoring props."
        )

    return lines
def build_sport_deterministic_explanation(
    prediction: dict[str, Any],
    sport: str,
) -> dict[str, Any]:
    """Describe non-soccer projections from their recorded model inputs.

    Each sport gets a Tactical Context block drawn from data already computed
    in the prediction pipeline — no new provider calls.  Every sentence is
    derived from the projection, baseline, recent form, matchup fields, or
    explicitly available engine factors.
    """
    sport = str(sport or "").lower()
    prop = _prop_label(str(prediction.get("propType") or "prop"))
    player = prediction.get("playerName") or "This player"
    team = prediction.get("teamName") or "the player's team"
    opponent = prediction.get("opponentName") or "the opponent"
    projection = _num(prediction.get("projection"))
    line = _num(prediction.get("line"))
    p_over = _num(prediction.get("pOver"))
    p_under = _num(prediction.get("pUnder"))
    confidence = _num(prediction.get("confidenceScore"))
    rec = _direction(prediction.get("recommendation"))
    prior = _num(prediction.get("priorMean"))
    momentum = _num(prediction.get("momentum") or prediction.get("momentumMean"))
    logs = prediction.get("gameLogs") or []
    history_count = prediction.get("historyGameCount") or len(logs)
    factors = prediction.get("bayesianMetrics") or {}
    notes: list[str] = []

    # ── Verdict / projection baseline ─────────────────────────────────────────
    if projection is not None and line is not None:
        gap = projection - line
        notes.append(
            f"{player} projects to {projection:g} {prop} against a {line:g} line "
            f"({gap:+.1f} from the market line)."
        )
    if prior is not None:
        notes.append(f"The baseline is {prior:g} {prop} from the available player sample.")
    if momentum is not None and abs(momentum) >= 0.2:
        notes.append(
            f"Recent form shifts the baseline {'up' if momentum > 0 else 'down'} "
            f"by {abs(momentum):.1f} {prop} per game."
        )

    matchup = f"{team} vs {opponent}"

    notes.append(
        f"Evidence: {history_count} game log{'s' if history_count != 1 else ''} "
        f"across the seasons returned by the provider."
    )

    # ── Sport-specific tactical context ───────────────────────────────────────
    tactical_lines: list[str] = []
    if sport in {"nba", "wnba"}:
        tactical_lines = _basketball_tactical_lines(prediction, prop, sport=sport)
    elif sport == "cs2":
        tactical_lines = _cs2_tactical_lines(prediction, prop)
    elif sport == "mlb":
        tactical_lines = _mlb_tactical_lines(prediction, prop)
    else:
        # Generic: game total when available
        total = _num(prediction.get("gameTotal"))
        if total is not None:
            tactical_lines.append(
                f"Game-total input: {total:g}, used as scoring environment context."
            )

    summary = (
        f"{matchup}: {rec} {prop} at {line:g} with "
        f"{max(p_over or 0, p_under or 0):.0f}% modeled probability."
        if line is not None else f"{matchup}: {rec} recommendation for {prop}."
    )

    # ── Safety / AVOID evidence ───────────────────────────────────────────────
    sport_safety = str(prediction.get("safetyRating") or "RISKY").upper()
    sport_hist_rate = _num(prediction.get("propHistoricalRate"))
    sport_hist_n_raw = prediction.get("propHistoricalN")
    try:
        sport_hist_n = int(sport_hist_n_raw) if sport_hist_n_raw is not None else None
    except (TypeError, ValueError):
        sport_hist_n = None

    avoid_note = ""
    if sport_safety in {"AVOID", "AVOID**"}:
        if sport_hist_rate is not None and sport_hist_n:
            avoid_note = (
                f" **AVOID**: {sport_hist_rate:.0f}% historical {rec.upper()} hit rate, "
                f"n={sport_hist_n} settled events — empirical data does not support the math edge; "
                f"treat as informational only."
            )
        elif sport_hist_rate is not None:
            avoid_note = (
                f" **AVOID**: {sport_hist_rate:.0f}% historical {rec.upper()} hit rate — "
                f"empirical data does not support the math edge; treat as informational only."
            )
        else:
            avoid_note = (
                f" **AVOID** safety: calibrated empirical hit-rate data does not support the math edge; "
                f"treat as informational only."
            )

    # ── Assemble sections ─────────────────────────────────────────────────────
    tactical_block = (
        "**Tactical context**\n" + "\n".join(f"- {ln}" for ln in tactical_lines)
        if tactical_lines else ""
    )

    baseline_block = (
        f"**{sport.upper()} matchup context**\n" + "\n".join(f"- {n}" for n in notes)
    )

    decision_block = (
        f"**Decision**\n- {rec} is supported by "
        f"{max(p_over or 0, p_under or 0):.0f}% modeled probability"
        + (f" and {confidence:.0f}% displayed confidence." if confidence is not None else ".")
        + avoid_note
    )

    sections = [baseline_block, tactical_block, decision_block]
    prediction["sharpSummary"] = summary
    prediction["tacticalBreakdown"] = "\n\n".join(s for s in sections if s)
    prediction["reasoning"] = prediction["tacticalBreakdown"]
    prediction["keyFactors"] = notes[:6]
    prediction["aiSource"] = "deterministic_model"
    prediction["explanationSource"] = "deterministic_model"
    prediction["explanationVersion"] = "reverse-picks-sport-v2"
    return prediction

def _mlb_tactical_lines(prediction: dict[str, Any], prop: str) -> list[str]:
    """Build evidence-gated tactical context lines for MLB picks."""
    lines: list[str] = []
    bm = prediction.get("bayesianMetrics") or {}
    prop_type = str(prediction.get("propType") or "").lower()
    player_role = str(prediction.get("playerRole") or "").lower()
    team = str(prediction.get("teamName") or "the player's team")
    opponent = str(prediction.get("opponentName") or "the opponent")
    venue = str(prediction.get("venue") or "").lower()
    game_total = _num(prediction.get("gameTotalUsed") or prediction.get("gameTotal"))
    game_total_source = str(prediction.get("gameTotalSource") or "")

    # Platoon split
    platoon = _num(bm.get("platoonSplitMult"))
    batter_hand = str(bm.get("batterHandedness") or bm.get("batterHand") or "")
    pitcher_hand = str(bm.get("pitcherHandedness") or bm.get("pitcherHand") or "")
    if platoon is not None and abs(platoon - 1) >= 0.03:
        pct = round((platoon - 1) * 100, 1)
        if batter_hand and pitcher_hand:
            matchup_str = f"{batter_hand}HB vs {pitcher_hand}HP"
            hand_note = (
                "opposite-hand advantage" if (
                    (batter_hand.upper() == "L" and pitcher_hand.upper() == "R") or
                    (batter_hand.upper() == "R" and pitcher_hand.upper() == "L")
                ) else "same-hand disadvantage"
            )
            lines.append(
                f"Platoon split ({matchup_str}): **{pct:+.1f}%** — "
                f"{hand_note} applied to the projection."
            )
        else:
            direction = "favourable" if pct > 0 else "unfavourable"
            lines.append(
                f"Platoon split: **{pct:+.1f}%** {direction} handedness matchup."
            )

    # Park factor
    park_pct = _num(bm.get("parkFactorPct"))
    park_name = str(bm.get("parkName") or bm.get("park_name") or "")
    if park_pct is not None and abs(park_pct) >= 1.5:
        park_label = park_name if park_name else (f"{team}'s park" if venue == "home" else f"{opponent}'s park")
        direction_word = "hitter-friendly" if park_pct > 0 else "pitcher-friendly"
        lines.append(
            f"Ballpark factor: **{park_pct:+.1f}%** — {park_label} is {direction_word} "
            f"for {prop.lower()} ({'+' if park_pct > 0 else ''}{park_pct:.1f}% vs neutral)."
        )

    # ERA / opposing pitcher quality
    era = _num(bm.get("eraFactor"))
    opp_era = _num(bm.get("pitcherEra") or prediction.get("pitcherEra"))
    if era is not None and abs(era - 1) >= 0.03:
        pct_era = round((era - 1) * 100, 1)
        if opp_era is not None:
            if player_role == "pitcher":
                # For pitcher props, ERA describes this pitcher's own quality
                quality = "dominant" if opp_era < 2.75 else "solid" if opp_era < 3.75 else "average" if opp_era < 4.75 else "weak"
                lines.append(
                    f"Pitcher quality: ERA {opp_era:.2f} ({quality}) — "
                    f"applies a {pct_era:+.1f}% adjustment to the {prop.lower()} projection."
                )
            else:
                # For batter props, ERA describes the opposing pitcher's quality
                quality = "elite ace" if opp_era < 2.75 else "solid starter" if opp_era < 3.75 else "average" if opp_era < 4.75 else "hittable"
                lines.append(
                    f"Opposing pitcher: ERA {opp_era:.2f} ({quality}) — "
                    f"applies a {pct_era:+.1f}% adjustment to the {prop.lower()} projection."
                )
        else:
            lines.append(
                f"Opposing pitcher quality: **{pct_era:+.1f}%** ERA-based adjustment to the {prop.lower()} projection."
            )

    # Game total — note: the engine applies this as a scoring-environment signal for batter props;
    # pitcher props use it as a run-environment indicator only (not a direct prop multiplier for K/IP).
    if game_total is not None:
        total_context = (
            "high-scoring environment" if game_total >= 10
            else "pitcher's duel" if game_total <= 7
            else "balanced game environment"
        )
        source_note = f" (from {game_total_source})" if game_total_source and game_total_source != "unavailable" else ""
        if player_role == "pitcher":
            lines.append(
                f"Game total: **{game_total:g}** — {total_context}{source_note}. "
                f"Used as a run-environment context; the engine applies it as a scoring-environment signal."
            )
        else:
            lines.append(
                f"Game total: **{game_total:g}** — {total_context}{source_note}. "
                f"Applied as a scoring-environment anchor for the {prop.lower()} projection."
            )

    return lines

def _cs2_tactical_lines(prediction: dict[str, Any], prop: str) -> list[str]:
    """Build evidence-gated tactical context lines for CS2 picks."""
    lines: list[str] = []
    bm = prediction.get("bayesianMetrics") or {}
    tm = bm.get("tacticalMetrics") or {}
    prop_type = str(prediction.get("propType") or "").lower()
    map_name = str(prediction.get("mapName") or tm.get("mapAwareness") or "").strip()
    player_team_rank = _num(prediction.get("playerTeamRank") or tm.get("playerTeamRank"))
    opp_rank = _num(prediction.get("opponentRank") or tm.get("opponentRank"))
    player_starts_ct = prediction.get("playerTeamStartsCt")
    if player_starts_ct is None:
        player_starts_ct = tm.get("playerTeamStartsCt")

    _kills_props = {
        "kills", "map1_kills", "maps_1_2_kills", "map3_kills", "maps_1_3_kills",
    }

    # Map context
    if map_name:
        clean_map = map_name.lower().replace("de_", "").strip()
        map_display = clean_map.capitalize()
        expected_rounds = _num(tm.get("mapExpectedRounds"))
        map_kpr = _num(tm.get("mapKprFactor"))
        ct_win_rate = _num(tm.get("mapCtWinRate"))

        rounds_note = ""
        if expected_rounds is not None:
            rounds_note = f" Expected rounds: ~{expected_rounds:.0f}."
        kpr_note = ""
        if map_kpr is not None and prop_type in _kills_props:
            if map_kpr < 0.97:
                kpr_note = f" This map's structure produces fewer duels per round (KPR factor {map_kpr:.2f})."
            elif map_kpr > 1.03:
                kpr_note = f" This map's open layout creates more duels per round (KPR factor {map_kpr:.2f})."
        lines.append(f"Map: **{map_display}**.{rounds_note}{kpr_note}")

        # CT/T side bias
        if ct_win_rate is not None and prop_type in _kills_props:
            ct_pct = round(ct_win_rate * 100, 1)
            t_pct = round((1 - ct_win_rate) * 100, 1)
            if ct_pct >= 53:
                bias_label = f"CT-favoured ({ct_pct}% CT win rate)"
                bias_note = "CT-side rounds tend to be shorter and more controlled, which can reduce per-round kill opportunities"
            elif ct_pct <= 48:
                bias_label = f"T-favoured ({t_pct}% T win rate)"
                bias_note = "T-side aggression creates more duel opportunities, generally supporting kill volume"
            else:
                bias_label = f"near-balanced ({ct_pct}% CT)"
                bias_note = "balanced side dynamic with no strong kill-volume bias"

            if player_starts_ct is not None:
                starting_side = "CT" if player_starts_ct else "T"
                advantage_half = "CT" if ct_win_rate >= 0.50 else "T"
                aligned = starting_side == advantage_half
                side_note = (
                    f" Player's team **starts {starting_side}** — "
                    f"{'aligned with the map\'s stronger half' if aligned else 'starting on the map\'s weaker half'}."
                )
            else:
                side_note = ""
            lines.append(
                f"Side bias: **{bias_label}** — {bias_note}.{side_note}"
            )

    # Opponent rank
    if opp_rank is not None:
        rank_int = int(opp_rank)
        if rank_int <= 10:
            tier = "world-elite"
            kill_note = "significantly suppresses kill output (structured CT, disciplined utility usage)"
        elif rank_int <= 20:
            tier = "top-20"
            kill_note = "meaningfully suppresses kill totals compared to mid-tier opponents"
        elif rank_int <= 50:
            tier = "top-50"
            kill_note = "applies a moderate kill suppression at baseline range"
        elif rank_int <= 100:
            tier = "ranked 51–100"
            kill_note = "has a slight positive effect on kill volume compared to stronger opponents"
        else:
            tier = f"ranked #{rank_int}"
            kill_note = "generally supports higher kill ceilings due to lower opposition quality"

        if prop_type in _kills_props or prop_type in {"adr", "maps_1_2_adr", "map3_adr"}:
            lines.append(
                f"Opponent: **#{rank_int} ({tier})** — opponent rank {kill_note}."
            )
        else:
            lines.append(f"Opponent: **#{rank_int} ({tier})**.")

    # Player/team rank for relative matchup
    if player_team_rank is not None and opp_rank is not None:
        pt_rank = int(player_team_rank)
        op_rank = int(opp_rank)
        if op_rank > pt_rank * 2 and op_rank > 50:
            lines.append(
                f"Matchup: player's team (#{pt_rank}) faces a notably weaker opponent (#{op_rank}) — "
                f"blowout risk may compress total rounds and cap kill ceiling."
            )
        elif pt_rank > op_rank * 2 and pt_rank > 50:
            lines.append(
                f"Matchup: underdog scenario (#{pt_rank} vs #{op_rank}) — "
                f"underdog compression is applied; elite opponents structurally reduce kill opportunities."
            )

    # Role context
    role_label = str(tm.get("roleClassification") or "")
    if role_label and role_label != "unknown":
        lines.append(f"Player role: **{role_label}** — affects projected kill ceiling and variance.")

    return lines
