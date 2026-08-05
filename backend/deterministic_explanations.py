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
    prop = str(result.get("propType") or "prop").replace("_", " ")
    opponent = result.get("opponent") or result.get("opponentName")
    team = (result.get("player") or {}).get("team") or result.get("teamName")
    venue = str(result.get("venue") or "").lower()

    if recommendation == "PASS":
        verdict = (
            f"**Verdict** — Reverse Picks projects **{_fmt(projection)}**, "
            f"which is within noise of the {_fmt(line)} line. PASS means there is no "
            f"actionable OVER/UNDER edge."
        )
    else:
        relation = "above" if recommendation == "OVER" else "below"
        probability = p_over if recommendation == "OVER" else p_under
        verdict = (
            f"**Verdict** — Reverse Picks projects **{_fmt(projection)}**, "
            f"{relation} the {_fmt(line)} line: **{recommendation}** at "
            f"**{_fmt(probability, 0)}%** modeled probability."
        )

    matchup = ""
    if team or opponent or venue:
        location = f" from the {venue} side" if venue in {"home", "away"} else ""
        matchup = (
            f"**Matchup**\n{player} is evaluated for {prop}{location}"
            f"{f' for {team}' if team else ''}"
            f"{f' against {opponent}' if opponent else ''}."
        )

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

    confidence_line = (
        f"**Confidence and risk**\nDisplayed confidence is **{_fmt(confidence, 0)}% "
        f"({confidence_level}). Edge: **{_fmt(edge)}** ({edge_rating}); safety: **{safety}**."
    )
    if recommendation == "PASS":
        confidence_line += " Confidence does not override the PASS decision."

    summary = (
        f"Reverse Picks model: {_fmt(projection)} {recommendation} {_fmt(line)} "
        f"with {_fmt(max(_num(p_over, 50) or 50, _num(p_under, 50) or 50), 0)}% "
        f"modeled probability and {_fmt(edge)} edge. "
        f"Confidence is {_fmt(confidence, 0)}% ({confidence_level}); safety is {safety}."
    )
    sections = [verdict, matchup, factor_block, confidence_line, limitation_block]
    result["tacticalBreakdown"] = "\n\n".join(section for section in sections if section)
    result["reasoning"] = result["tacticalBreakdown"]
    result["sharpSummary"] = summary
    result["aiSource"] = "model"
    result["aiPending"] = False
    result["explanationSource"] = "deterministic_model"
    result["explanationVersion"] = "reverse-picks-model-v1"
    return result