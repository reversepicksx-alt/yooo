"""Deterministic, auditable explanations for Reverse Picks predictions.

This module never calls a language model and never invents evidence. It turns
the final projection ledger and persisted model inputs into concise prose that
can be reproduced from the same prediction inputs.
"""

from __future__ import annotations

from typing import Any


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
        if pos in {"CB", "SW", "DEF"} or "ball-playing defender" in role_norm or "stopper" in role_norm:
            return "As a central defender, the pass volume is primarily circulation behind the attack: it depends on how often the team can retain possession and reset through the first line rather than on final-third touches."
        if pos in {"LB", "RB", "LWB", "RWB", "FB"} or "fullback" in role_norm or "wingback" in role_norm:
            return "As a fullback/wingback, the pass volume is tied to the team's outlet work and width in possession; the role can accumulate passes when the team controls territory, but is less stable when the player is pinned into defensive actions."
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
            lines.append(
                f"Opponent mechanism: {context.get('opponent') or 'Opponent'} allows "
                f"{allowed:.1f} {prop} to this position over {n_allowed} comparable games, "
                f"{comparison} the player's baseline by {abs(diff):.0f}%"
                f"{f' ({tier})' if tier else ''}."
            )
        else:
            lines.append(
                f"Opponent mechanism: {context.get('opponent') or 'Opponent'} allows "
                f"{allowed:.1f} {prop} to this position across {n_allowed} comparable games."
            )

    formation = context.get("lineupFormation")
    opponent_formation = context.get("opponentFormation")
    lineup_status = str(context.get("lineupStatus") or "")
    if formation or opponent_formation:
        shape = f"{formation or 'unknown'} vs {opponent_formation or 'unknown'}"
        lines.append(
            f"Shape evidence: the available {'confirmed' if lineup_status == 'confirmed' else 'predicted'} "
            f"lineup data shows **{shape}**; no additional zone or matchup claim is made without event-level data."
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

    # ── Verdict ───────────────────────────────────────────────────────────────
    if recommendation == "PASS":
        _edge_num = _num(edge)
        if safety in {"AVOID", "AVOID**"}:
            _pass_reason = (
                f"safety is {safety} based on calibrated empirical hit-rate data"
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
        if expected_poss_player and expected_poss_opp:
            poss_phrase = (
                f" Expected possession: **{expected_poss_player:.0f}%** "
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
        tactical_lines.append(
            f"{opponent or 'Opponent'} allows an average of {_fmt(opp_allowed_avg)} {prop} "
            f"to players at this position — used as a defensive-context anchor."
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
    elif safety in {"AVOID", "AVOID**"}:
        confidence_line += (
            " AVOID safety means empirical hit-rate data for this prop/direction "
            "does not support the math edge — treat as informational only."
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
