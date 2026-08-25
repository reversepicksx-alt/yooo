"""Independent, auditable JARVIS evidence layer.

This module deliberately sits beside the Reverse Picks predictor.  It stores
immutable prediction snapshots, computes leakage-conscious calibration summaries,
and records settlement postmortems without feeding audit values back into RP
math.  Optional or unavailable evidence is represented explicitly instead of
being inferred from a nearby metric.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Iterable

from causal_script_engine import build_causal_script_packet


AUDIT_SCHEMA_VERSION = "jarvis-audit.v1"
AUDIT_MODEL_VERSION = "jarvis-shadow-audit.v1"


STAT_DEFINITIONS: dict[str, dict[str, Any]] = {
    "pass_attempts": {
        "market": "Pass Attempts",
        "provider_field": "passes",
        "definition": "All provider-recorded pass attempts used by the RP settlement contract.",
        "verification_status": "repository_contract",
        "source": "Reverse Picks settlement/provider contract",
    },
    "passes": {
        "market": "Completed Passes",
        "provider_field": "passes",
        "definition": "Provider pass total as mapped by the active settlement contract; not silently treated as completed-pass-only.",
        "verification_status": "repository_contract",
        "source": "Reverse Picks settlement/provider contract",
    },
    "key_passes": {
        "market": "Key Passes",
        "provider_field": "key_passes",
        "definition": "Provider-recorded key passes credited to the player.",
        "verification_status": "repository_contract",
        "source": "Reverse Picks settlement/provider contract",
    },
    "shots": {
        "market": "Shots",
        "provider_field": "shots",
        "definition": "Total shots recorded for the player.",
        "verification_status": "repository_contract",
        "source": "Reverse Picks settlement/provider contract",
    },
    "shots_on_target": {
        "market": "Shots on Target",
        "provider_field": "shots_on_target",
        "definition": "Shots on target recorded for the player.",
        "verification_status": "repository_contract",
        "source": "Reverse Picks settlement/provider contract",
    },
    "tackles": {
        "market": "Tackles",
        "provider_field": "tackles",
        "definition": "Tackles recorded by the provider for the player.",
        "verification_status": "repository_contract",
        "source": "Reverse Picks settlement/provider contract",
    },
    "clearances": {
        "market": "Clearances",
        "provider_field": "clearances",
        "definition": "Clearances recorded by the provider for the player.",
        "verification_status": "repository_contract",
        "source": "Reverse Picks settlement/provider contract",
    },
    "saves": {
        "market": "Goalkeeper Saves",
        "provider_field": "saves",
        "definition": "Goalkeeper saves credited by the provider.",
        "verification_status": "repository_contract",
        "source": "Reverse Picks settlement/provider contract",
    },
    "goals": {
        "market": "Goals",
        "provider_field": "goals",
        "definition": "Goals credited to the player.",
        "verification_status": "repository_contract",
        "source": "Reverse Picks settlement/provider contract",
    },
}

# Keep the audit gate aligned with the broader prop vocabulary already
# supported by the RP engine. These are provider-mapped definitions, not new
# production math or a claim that every fixture supplies every field.
for _prop_key, _market, _provider_field in (
    ("dribbles", "Dribbles", "dribbles"),
    ("dribbles_success", "Successful Dribbles", "dribbles_success"),
    ("crosses", "Crosses", "crosses"),
    ("interceptions", "Interceptions", "interceptions"),
    ("blocks", "Blocks", "blocks"),
    ("fouls_drawn", "Fouls Drawn", "fouls_drawn"),
    ("fouls_committed", "Fouls Committed", "fouls_committed"),
    ("duels_won", "Duels Won", "duels_won"),
    ("assists", "Assists", "assists"),
    ("offsides", "Offsides", "offsides"),
    ("yellow_cards", "Yellow Cards", "yellow_cards"),
    ("red_cards", "Red Cards", "red_cards"),
):
    STAT_DEFINITIONS[_prop_key] = {
        "market": _market,
        "provider_field": _provider_field,
        "definition": f"Provider-recorded {_market.lower()} credited to the player.",
        "verification_status": "repository_contract",
        "source": "Reverse Picks settlement/provider contract",
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def audit_mode() -> str:
    """Return the configured audit mode without changing RP behavior."""
    value = (os.environ.get("JARVIS_FULL_AUDIT_MODE") or "shadow").strip().lower()
    if value in {"off", "disabled", "false", "0"}:
        return "off"
    if value in {"live", "enabled", "true", "1"}:
        return "live"
    return "shadow"


def audit_enabled() -> bool:
    return audit_mode() != "off"


def _number(value: Any) -> float | None:
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def _probability(value: Any) -> float | None:
    number = _number(value)
    if number is None:
        return None
    if 0 <= number <= 1:
        number *= 100
    return number if 0 <= number <= 100 else None


def _fingerprint(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]


def _module(
    status: str,
    *,
    source: str,
    values: dict[str, Any] | None = None,
    reason: str | None = None,
    sample_size: int | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": status,
        "source": source,
        "values": values or {},
    }
    if sample_size is not None:
        result["sample_size"] = sample_size
    if reason:
        result["reason"] = reason
    return result


def _rp_snapshot(prediction: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    """Keep the exact quantitative inputs needed to reproduce the RP result."""
    final = prediction.get("final") if isinstance(prediction.get("final"), dict) else {}
    bayesian = prediction.get("bayesianMetrics") if isinstance(prediction.get("bayesianMetrics"), dict) else {}
    ledger = prediction.get("factorLedger") if isinstance(prediction.get("factorLedger"), dict) else {}
    ledger_final = ledger.get("final") if isinstance(ledger.get("final"), dict) else {}
    selected_keys = (
        "recommendation",
        "projectedValue",
        "projection",
        "line",
        "pOver",
        "pUnder",
        "confidenceScore",
        "rawConfidence",
        "confidenceLevel",
        "bayesianMetrics",
        "factorLedger",
        "factorLedgerVersion",
        "factorLedgerFingerprint",
        "modelInputSnapshot",
        "modelBreakdown",
        "evidenceQuality",
        "fusionApplied",
        "calibrationApplied",
    )
    values = {key: prediction.get(key) for key in selected_keys if key in prediction}
    # JARVIS diagnostic responses expose the same RP values under `final`.
    # Normalize those aliases into the immutable snapshot without recalculating
    # or changing the production result.
    aliases = {
        "recommendation": ("recommendation", "recommendation"),
        "projectedValue": ("projectedValue", "projected_value"),
        "pOver": ("pOver", "p_over"),
        "pUnder": ("pUnder", "p_under"),
        "confidenceScore": ("confidenceScore", "confidence_score"),
        "propHistoricalRate": ("propHistoricalRate", "prop_historical_rate"),
        "propHistoricalN": ("propHistoricalN", "prop_historical_n"),
    }
    for target, (top_key, final_key) in aliases.items():
        if values.get(target) is None:
            values[target] = (
                prediction.get(top_key)
                if prediction.get(top_key) is not None
                else final.get(final_key)
            )
    for target, key in (("pOver", "pOver"), ("pUnder", "pUnder")):
        if values.get(target) is None:
            values[target] = bayesian.get(key)
        if values.get(target) is None:
            values[target] = ledger_final.get(key)
    values["request"] = dict(request)
    values["captured_at"] = _now()
    values["model_version"] = (
        prediction.get("factorLedgerVersion")
        or (prediction.get("bayesianMetrics") or {}).get("threeLayerModel", {}).get("version")
        or "rp-version-unspecified"
    )
    fingerprint_values = dict(values)
    fingerprint_values.pop("captured_at", None)
    values["fingerprint"] = _fingerprint(fingerprint_values)
    return values


def _anomalies(prediction: dict[str, Any], eq: dict[str, Any], stat_definition: dict[str, Any]) -> dict[str, Any]:
    flags: list[dict[str, Any]] = []
    p_over = _probability(prediction.get("pOver"))
    p_under = _probability(prediction.get("pUnder"))
    if p_over is not None and p_under is not None and abs((p_over + p_under) - 100) > 3:
        flags.append({
            "code": "PROBABILITY_SUM_MISMATCH",
            "severity": "high",
            "details": {"p_over": p_over, "p_under": p_under},
        })

    samples = _number((prediction.get("bayesianMetrics") or {}).get("priorSamples"))
    score = _number(eq.get("score"))
    if score is not None and score >= 80 and samples is not None and samples < 5:
        flags.append({
            "code": "HIGH_QUALITY_TINY_SAMPLE",
            "severity": "high",
            "details": {"evidence_quality": score, "prior_samples": samples},
        })
    if stat_definition.get("verification_status") not in {"repository_contract", "verified"}:
        flags.append({
            "code": "STAT_DEFINITION_UNVERIFIED",
            "severity": "high",
            "details": {"prop_type": prediction.get("propType")},
        })
    for alert in prediction.get("tacticalAlerts") or []:
        if isinstance(alert, str) and alert.strip():
            flags.append({"code": "RP_TACTICAL_ALERT", "severity": "medium", "details": {"message": alert}})

    severe = any(flag["severity"] == "high" for flag in flags)
    return {
        "status": "available",
        "source": "deterministic_snapshot_checks",
        "flags": flags,
        "severity": "high" if severe else ("medium" if flags else "none"),
        "blocks_elite_grade": severe,
    }


def _prediction_value(prediction: dict[str, Any], *keys: str) -> Any:
    """Read equivalent RP fields from raw and diagnostic response shapes."""
    final = prediction.get("final") if isinstance(prediction.get("final"), dict) else {}
    bayesian = prediction.get("bayesianMetrics") if isinstance(prediction.get("bayesianMetrics"), dict) else {}
    ledger = prediction.get("factorLedger") if isinstance(prediction.get("factorLedger"), dict) else {}
    ledger_final = ledger.get("final") if isinstance(ledger.get("final"), dict) else {}
    for key in keys:
        for source in (prediction, final, bayesian, ledger_final):
            if source.get(key) is not None:
                return source[key]
    return None


def _first_goal_modules(prediction: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Normalize the RP first-goal packet into audit response modules."""
    factors = prediction.get("matchFactors") if isinstance(prediction.get("matchFactors"), dict) else {}
    market = prediction.get("firstGoalMarket") or factors.get("firstGoalMarket") or {}
    regime = prediction.get("firstGoalRegimeChange") or factors.get("firstGoalRegimeChange") or {}
    market_available = bool(isinstance(market, dict) and market.get("available"))
    regime_available = bool(isinstance(regime, dict) and regime.get("available"))
    unavailable_reason = (
        (market.get("reason") if isinstance(market, dict) else None)
        or "No completed-fixture first-goal evidence was available for this fixture."
    )

    market_module = _module(
        "available" if market_available else "unavailable",
        source=str((market or {}).get("source") or "first_goal_engine"),
        values=market if isinstance(market, dict) else {},
        reason=None if market_available else unavailable_reason,
    )
    regime_module = _module(
        "available" if regime_available else "unavailable",
        source=str((regime or {}).get("source") or "first_goal_engine"),
        values=regime if isinstance(regime, dict) else {},
        reason=None if regime_available else (
            (regime.get("reason") if isinstance(regime, dict) else None) or unavailable_reason
        ),
    )
    game_state_module = _module(
        "available" if market_available and regime_available else "partial",
        source="first_goal_engine",
        values={
            "first_goal_market": market_module["values"],
            "first_goal_regime_change": regime_module["values"],
            "projection_influence": "shadow_only",
        },
        reason=None if market_available and regime_available else unavailable_reason,
    )
    return {
        "game_state": game_state_module,
        "first_goal_market": market_module,
        "first_goal_regime_change": regime_module,
    }


def _news_intelligence_module(prediction: dict[str, Any]) -> dict[str, Any]:
    """Normalize current-news evidence into one mandatory shadow audit module."""
    packet = prediction.get("newsIntelligence")
    if not isinstance(packet, dict):
        reason = "Current team news was not attached to this audit snapshot."
        return _module(
            "unavailable",
            source="dynamic_news_research_and_confirmed_lineups",
            values={
                "status": "unavailable",
                "projection_influence": "shadow_only",
                "math_unchanged": True,
                "expected_lineup": "UNKNOWN",
                "target_start_probability": "UNKNOWN",
                "minutes_risk": "UNKNOWN",
                "expected_role": "UNKNOWN",
                "formation": "UNKNOWN",
                "important_teammate_changes": "UNKNOWN",
                "lineup_confidence": "UNKNOWN",
                "regime_changes": "UNKNOWN",
                "news_warnings": [],
                "news_brief": reason,
            },
            reason=reason,
        )
    status = str(packet.get("status") or "unavailable").lower()
    if status not in {"available", "partial", "unavailable"}:
        status = "partial"
    return _module(
        status,
        source=str(packet.get("source") or "dynamic_news_research_and_confirmed_lineups"),
        values=packet,
        reason=packet.get("reason") if status == "unavailable" else None,
    )


def _runtime_module(
    prediction: dict[str, Any],
    *,
    name: str,
    values: dict[str, Any],
    sources: tuple[str, ...],
    reason: str,
) -> dict[str, Any]:
    """Normalize an existing runtime packet without inventing evidence."""
    populated = any(value not in (None, "", [], {}) for value in values.values())
    source = next((source for source in sources if source), "reverse_picks_runtime")
    return _module(
        "available" if populated else "UNKNOWN",
        source=source,
        values=values,
        reason=None if populated else reason,
    )


def _has_direct_packet(value: Any, *, sample_keys: tuple[str, ...] = ()) -> bool:
    """True only when a packet has direct, usable evidence—not just a wrapper."""
    if not isinstance(value, dict):
        return bool(value)
    if value.get("available") is False or str(value.get("status", "")).lower() in {
        "unavailable", "unknown", "not_started",
    }:
        return False
    if sample_keys:
        return any(
            value.get(key) not in (None, "", [], {}, 0)
            for key in sample_keys
        )
    return any(value.get(key) not in (None, "", [], {}, 0) for key in value)


def _unknown_components(modules: dict[str, dict[str, Any]]) -> list[str]:
    """Flatten material gaps so callers cannot mistake a populated packet for complete evidence."""
    gaps: list[str] = []
    for name, module in modules.items():
        status = str(module.get("status", "")).lower() if isinstance(module, dict) else ""
        if status in {"unknown", "unavailable", "partial", "not_started"}:
            gaps.append(name)
    return gaps


def _jarvis_verdict(
    *,
    prediction: dict[str, Any],
    modules: dict[str, dict[str, Any]],
    anomalies: dict[str, Any],
) -> dict[str, Any]:
    """Produce a separate, deterministic tactical conclusion."""
    recommendation = str(_prediction_value(prediction, "recommendation") or "").lower()
    projection = _number(_prediction_value(prediction, "projectedValue", "projection"))
    line = _number(_prediction_value(prediction, "line"))
    active = [
        name for name, module in modules.items()
        if isinstance(module, dict) and module.get("status") in {"available", "partial"}
    ]
    unknown = _unknown_components(modules)
    tactical = modules.get("buildup_interaction", {}).get("values", {})
    press = modules.get("press_block_interaction", {}).get("values", {})
    role = modules.get("exact_role", {}).get("values", {}).get("exact_role")
    direction = recommendation.upper() if recommendation in {"over", "under"} else "PASS"
    # Tactical completeness is capped independently of RP confidence.
    critical_gaps = {
        "buildup_interaction", "press_block_interaction",
        "venue_h2h_possession", "role_opponent_venue_cohort",
        "market_movement", "model_disagreement", "counterfactual_robustness",
    }
    critical_unknowns = len(critical_gaps.intersection(unknown))
    grade = "C"
    if len(active) >= 10 and critical_unknowns <= 2 and not anomalies.get("blocks_elite_grade"):
        grade = "B"
    verdict = (
        f"JARVIS {direction}: {role or 'role evidence is incomplete'}; "
        f"the tactical packet has {len(active)} populated evidence modules"
        + (f" and {len(unknown)} UNKNOWN modules." if unknown else ".")
    )
    if projection is not None and line is not None:
        verdict += f" RP projects {projection:g} against {line:g}; this verdict is observational and does not override RP."
    return {
        "grade": grade,
        "direction": direction,
        "final_verdict": verdict,
        "robustness": modules.get("counterfactual_robustness", {}).get("values", {}),
        "strongest_opposite_case": modules.get("strongest_opposite_case", {}).get("values", {}),
        "active_modules": active,
        "unknown_evidence": unknown,
        "evidence_quality_penalty": critical_unknowns,
        "provenance": "deterministic synthesis of existing Reverse Picks runtime packets; shadow_only",
        "production_influence": False,
        "tactical_summary": {
            "buildup": tactical,
            "press_block": press,
        },
    }


def build_audit_snapshot(
    prediction: dict[str, Any],
    request: dict[str, Any],
    *,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a shadow audit packet from the already-computed RP response.

    No value returned by this function is used to recalculate RP projection,
    probabilities, or recommendation.
    """
    context = context or {}
    causal_script = build_causal_script_packet(
        prediction,
        request=request,
        context=context,
    ) if str(prediction.get("sport") or "soccer").lower() == "soccer" else None
    bm = prediction.get("bayesianMetrics") or {}
    eq = prediction.get("evidenceQuality") or {}
    prop_type = str(request.get("prop_type") or prediction.get("propType") or "").strip().lower()
    stat_definition = STAT_DEFINITIONS.get(prop_type, {
        "market": prop_type or "unknown",
        "provider_field": None,
        "definition": None,
        "verification_status": "unknown",
        "source": "unavailable",
    })
    role = (
        prediction.get("exactTacticalRole")
        or prediction.get("tacticalRole")
        or prediction.get("role")
        or prediction.get("playerRole")
    )
    position = prediction.get("playerPosition") or prediction.get("position")
    role_values = {
        "exact_role": role,
        "position": position,
        "confidence": prediction.get("roleConfidence"),
        "evidence_chain": prediction.get("roleEvidence") or prediction.get("positionalReality"),
    }
    role_status = "available" if role else ("partial" if position else "unknown")
    role_reason = None if role else "The prediction response did not contain an exact observed tactical role."

    venue_values = {
        "rp_venue_avg": bm.get("venueAvg"),
        "rp_venue_samples": bm.get("venueSamples"),
        "opponent_allowed_avg": bm.get("opponentAllowedAvg"),
        "opponent_allowed_samples": bm.get("opponentAllowedSamples"),
        "cond_possession_adjustment": bm.get("condPossAdj"),
    }
    venue_has_data = any(value is not None for value in venue_values.values())
    rp_snapshot = _rp_snapshot(prediction, request)
    p_over = _probability(_prediction_value(prediction, "pOver", "p_over"))
    p_under = _probability(_prediction_value(prediction, "pUnder", "p_under"))
    recommendation = str(_prediction_value(prediction, "recommendation") or "").lower()
    selected_probability = p_over if recommendation == "over" else p_under if recommendation == "under" else None
    anomaly_input = dict(prediction)
    if p_over is not None:
        anomaly_input["pOver"] = p_over
    if p_under is not None:
        anomaly_input["pUnder"] = p_under
    anomalies = _anomalies(anomaly_input, eq, stat_definition)
    evidence_score = _number(eq.get("score"))
    first_goal_modules = _first_goal_modules(prediction)
    news_intelligence_module = _news_intelligence_module(prediction)
    tactical_context = prediction.get("tacticalContext") if isinstance(prediction.get("tacticalContext"), dict) else {}
    tactical_intelligence = prediction.get("tacticalIntelligence") if isinstance(prediction.get("tacticalIntelligence"), dict) else {}
    role_packet = prediction.get("roleEvidencePacket") if isinstance(prediction.get("roleEvidencePacket"), dict) else {}
    matchup_volume = prediction.get("matchupVolume") if isinstance(prediction.get("matchupVolume"), dict) else {}
    position_comparison = prediction.get("positionComparison") or tactical_context.get("positionCohort") or {}
    player_logs = prediction.get("playerGameLogs") if isinstance(prediction.get("playerGameLogs"), dict) else {}
    team_stats = prediction.get("teamMatchStats") if isinstance(prediction.get("teamMatchStats"), dict) else {}
    opponent_stats = prediction.get("opponentMatchStats") if isinstance(prediction.get("opponentMatchStats"), dict) else {}
    role_values = {
        **role_values,
        "exact_role": role_values.get("exact_role") or tactical_context.get("role") or role_packet.get("role"),
        "position": role_values.get("position") or tactical_context.get("position") or role_packet.get("position"),
        "confidence": role_values.get("confidence") or tactical_context.get("roleConfidence"),
        "evidence_chain": role_values.get("evidence_chain") or tactical_context.get("roleEvidence") or role_packet.get("evidence"),
        "formation": tactical_context.get("lineupFormation"),
        "grid_position": tactical_context.get("targetLineupPosition"),
    }
    tactical_context_values = {
        "team_possession": tactical_context.get("expectedPossession") or tactical_context.get("teamSeasonPossession"),
        "opponent_possession": tactical_context.get("opponentExpectedPossession"),
        "venue": context.get("venue") or tactical_context.get("venue"),
        "venue_average": tactical_context.get("venueAverage"),
        "sample_size": tactical_context.get("venueSampleSize"),
        "source": tactical_context.get("possessionSource"),
    }
    buildup_values = {
        "team_playstyle": tactical_intelligence.get("teamPlaystyle") or tactical_context.get("teamPlaystyle"),
        "buildup_route": tactical_intelligence.get("buildup") or tactical_intelligence.get("buildupInteraction") or tactical_context.get("buildup_proxies"),
        "hub_or_connector": tactical_intelligence.get("playerRole") or tactical_context.get("role"),
        "matchup_volume": matchup_volume,
        "team_stats": team_stats,
    }
    press_values = {
        "opponent_playstyle": tactical_intelligence.get("opponentPlaystyle") or tactical_context.get("opponentProfileTier"),
        "press_intensity": tactical_context.get("pressIntensity"),
        "block_profiles": tactical_context.get("recentOpponentBlockProfiles"),
        "pressure_response": tactical_context.get("pressureResponse"),
        "opponent_stats": opponent_stats,
    }
    same_role = role_values.get("evidence_chain") if isinstance(role_values.get("evidence_chain"), dict) else {}
    same_role = same_role.get("sameRoleEvidence") if isinstance(same_role.get("sameRoleEvidence"), dict) else {}
    same_venue = role_values.get("evidence_chain", {})
    same_venue = same_venue.get("sameVenueEvidence") if isinstance(same_venue, dict) else {}
    cohort_direct = _has_direct_packet(
        position_comparison,
        sample_keys=("sampleSize", "avgStatValue", "average", "weightedAverage"),
    ) and bool((position_comparison or {}).get("players"))
    same_role_direct = _has_direct_packet(same_role, sample_keys=("sampleSize",))
    same_venue_direct = _has_direct_packet(same_venue, sample_keys=("sampleSize",))
    direct_playstyle = any(
        tactical_intelligence.get(key) not in (None, "", [], {})
        or tactical_context.get(key) not in (None, "", [], {})
        for key in ("teamPlaystyle", "buildupStyle", "progressionChannel")
    )
    direct_press_style = any(
        tactical_intelligence.get(key) not in (None, "", [], {})
        or tactical_context.get(key) not in (None, "", [], {})
        for key in ("opponentPlaystyle", "pressingTreatment", "blockStyle")
    )
    hub_evidence = any(
        tactical_intelligence.get(key) not in (None, "", [], {})
        or tactical_context.get(key) not in (None, "", [], {})
        for key in ("hubSignal", "volumeShare", "passConcentration", "touchShare")
    ) or bool((prediction.get("teammateRedistribution") or {}).get("hubEvidence"))
    branch_base = {
        "pass_volume_direction": "UNKNOWN",
        "evidence": [],
        "rationale": "No validated score-state-to-pass-volume transport was returned; branch remains descriptive.",
        "projection_influence": "shadow_only",
    }
    first_goal_values = first_goal_modules["first_goal_market"].get("values", {})
    branch_values = {
        "rennes_scores_first": {**branch_base, "scenario_probability": first_goal_values.get("team_scores_first_probability")},
        "psg_scores_first": {**branch_base, "scenario_probability": first_goal_values.get("opponent_scores_first_probability")},
        "level_around_60": {**branch_base, "scenario_probability": first_goal_values.get("no_goal_probability")},
        "early_goal_either_way": {**branch_base, "scenario_probability": first_goal_values.get("team_scores_first_probability")},
    }
    counterfactual_values = {
        "base_projection": _prediction_value(prediction, "projectedValue", "projection"),
        "base_line": request.get("line"),
        "scenarios": {
            "possession_minus_5pp": {"effect": "UNKNOWN", "projection": None},
            "possession_plus_5pp": {"effect": "UNKNOWN", "projection": None},
            "minutes_minus_5": {"effect": "UNKNOWN", "projection": None},
            "minutes_minus_10": {"effect": "UNKNOWN", "projection": None},
            "different_press": {"effect": "UNKNOWN", "projection": None},
            "formation_or_role_shift": {"effect": "UNKNOWN", "projection": None},
            "rennes_scores_first": {"effect": "UNKNOWN", "projection": None},
            "psg_scores_first": {"effect": "UNKNOWN", "projection": None},
            "favorite_trailing": {"effect": "UNKNOWN", "projection": None},
        },
        "summary": "UNKNOWN",
        "flip_condition": "UNKNOWN; no bounded counterfactual rerun was returned.",
        "projection_influence": "shadow_only",
    }
    opposite_values = {
        "opposite_direction": "over" if recommendation == "under" else "under",
        "path_status": "UNKNOWN",
        "football_case": "UNKNOWN: no direct evidence establishes that Rennes would sustain longer harmless possession, funnel buildup through Rongier, or that PSG would relax its press.",
        "supporting_evidence": [],
        "missing_evidence": ["buildup_style", "hub_concentration", "press_relaxation", "verified_90_minute_role"],
        "projection": _prediction_value(prediction, "projectedValue", "projection"),
    }
    modules = {
        "stat_definition": _module(
            "available" if stat_definition.get("verification_status") != "unknown" else "unknown",
            source=str(stat_definition.get("source") or "unavailable"),
            values=stat_definition,
            reason=None if stat_definition.get("verification_status") != "unknown" else "No configured definition for this prop.",
        ),
        "exact_role": _runtime_module(prediction, name="exact_role", values=role_values, sources=("roleEvidencePacket", "tacticalContext"), reason="No exact observed role or lineup evidence was returned."),
        "rp_possession_context": _runtime_module(prediction, name="rp_possession_context", values=venue_values, sources=("rp_prediction",), reason="RP response did not expose venue possession values."),
        "independent_venue_possession": _runtime_module(prediction, name="independent_venue_possession", values=tactical_context_values, sources=("api-football fixture statistics", "tacticalContext"), reason="No verified same-venue team possession packet was returned."),
        "venue_h2h_possession": _module("available" if same_venue_direct else "partial" if tactical_context.get("venueAverage") is not None else "UNKNOWN", source="roleEvidencePacket/tacticalContext", values={"matching_venue_player_evidence": same_venue if same_venue_direct else "UNKNOWN", "team_venue_context": {"average": tactical_context.get("venueAverage"), "sample_size": tactical_context.get("venueSampleSize")}, "player_logs_are_matching_venue": same_venue_direct}, reason=None if same_venue_direct else "Team venue context or generic player logs do not establish matching-venue player comparables."),
        "role_opponent_venue_cohort": _module("available" if cohort_direct or same_role_direct else "UNKNOWN", source="positionComparison/roleEvidencePacket", values={"position_cohort": position_comparison if cohort_direct else "UNKNOWN", "same_role_evidence": same_role if same_role_direct else "UNKNOWN", "opponent": context.get("opponent_name"), "venue": context.get("venue")}, reason=None if cohort_direct or same_role_direct else "Same-role/opponent/venue comparable rows were not returned."),
        "volume_share": _runtime_module(prediction, name="volume_share", values={"matchup_volume": matchup_volume, "player_logs": player_logs, "hub_signal": tactical_intelligence.get("hubSignal") or tactical_context.get("hubSignal")}, sources=("matchupVolume", "tacticalIntelligence"), reason="No player/team volume-share packet was returned."),
        "teammate_redistribution": _runtime_module(prediction, name="teammate_redistribution", values={"redistribution": prediction.get("teammateRedistribution") or tactical_context.get("teammateRedistribution"), "lineup": prediction.get("lineup") or tactical_context.get("lineupStatus")}, sources=("lineup", "tacticalContext"), reason="No verified teammate redistribution packet was returned."),
        "minutes_probability": _runtime_module(prediction, name="minutes_probability", values={"minutes_risk": tactical_context.get("minutesRisk") or (news_intelligence_module.get("values") or {}).get("minutes_risk"), "start_probability": (news_intelligence_module.get("values") or {}).get("target_start_probability"), "lineup_status": tactical_context.get("lineupStatus")}, sources=("newsIntelligence", "tacticalContext"), reason="No lineup/minutes probability packet was returned."),
        "game_state": _module(
            first_goal_modules["game_state"].get("status", "partial"),
            source="first_goal_engine",
            values={
                **first_goal_modules["game_state"].get("values", {}),
                "branch_effects": branch_values,
            },
            reason=first_goal_modules["game_state"].get("reason"),
        ),
        "first_goal_market": first_goal_modules["first_goal_market"],
        "first_goal_regime_change": first_goal_modules["first_goal_regime_change"],
        "news_intelligence": news_intelligence_module,
        "market_movement": _module("unknown", source="saved_prediction_input", reason="Timestamped opening/current/closing line history is unavailable unless separately captured."),
        "anomaly_detection": anomalies,
        "evidence_quality": _module("available" if eq else "unknown", source="rp_evidence_quality" if eq else "unavailable", values=eq, reason=None if eq else "RP did not return an evidence-quality packet."),
        "model_disagreement": _module("unknown", source="independent_layer_registry", reason="Only RP-correlated values are present; independent disagreement cannot be claimed from one prediction run."),
        "counterfactual_robustness": _module("UNKNOWN", source="bounded_counterfactual_runner", values=counterfactual_values, reason="Scenario inputs were specified, but no derived projection/effect was returned."),
        "calibration": _module("partial", source="rp_response_and_settled_pick_ledger", values={"prop_historical_rate": prediction.get("propHistoricalRate"), "prop_historical_n": prediction.get("propHistoricalN"), "line_deviation_hit_rate": prediction.get("lineDeviationHitRate"), "line_deviation_n": prediction.get("lineDeviationHitRateN")}),
        "buildup_interaction": _module("available" if direct_playstyle and hub_evidence else "partial" if matchup_volume else "UNKNOWN", source="tacticalIntelligence/tacticalContext", values={**buildup_values, "team_playstyle": tactical_intelligence.get("teamPlaystyle") or tactical_context.get("teamPlaystyle") or "UNKNOWN", "progression_channel": tactical_intelligence.get("progressionChannel") or tactical_context.get("progressionChannel") or "UNKNOWN", "hub_status": "HUB" if hub_evidence else "UNKNOWN"}, reason=None if direct_playstyle and hub_evidence else "Formation, possession, and role alone do not establish Rennes buildup style or Rongier hub status."),
        "press_block_interaction": _module("available" if direct_press_style else "partial" if tactical_context.get("pressIntensity") or tactical_context.get("recentOpponentBlockProfiles") else "UNKNOWN", source="tacticalContext/opponentMatchStats", values={**press_values, "opponent_playstyle": tactical_intelligence.get("opponentPlaystyle") or tactical_context.get("opponentPlaystyle") or "UNKNOWN", "pressing_treatment": tactical_intelligence.get("pressingTreatment") or tactical_context.get("pressingTreatment") or "UNKNOWN"}, reason=None if direct_press_style else "Press intensity proxies do not establish PSG press/block style or its effect on this prop."),
        "strongest_opposite_case": _module("available" if opposite_values["supporting_evidence"] else "UNKNOWN", source="deterministic_opposite_case", values=opposite_values, reason="No evidence-backed football-specific OVER path was returned."),
    }
    verdict = _jarvis_verdict(prediction=prediction, modules=modules, anomalies=anomalies)
    component_unknowns = []
    buildup_values_final = modules["buildup_interaction"]["values"]
    press_values_final = modules["press_block_interaction"]["values"]
    if buildup_values_final.get("team_playstyle") == "UNKNOWN":
        component_unknowns.extend(["Rennes buildup style", "progression channel"])
    if buildup_values_final.get("hub_status") == "UNKNOWN":
        component_unknowns.append("Rongier hub/connector status")
    if press_values_final.get("opponent_playstyle") == "UNKNOWN":
        component_unknowns.append("PSG press/block style")
    if press_values_final.get("pressing_treatment") == "UNKNOWN":
        component_unknowns.append("PSG pressing treatment")
    verdict["unknown_evidence"] = sorted(set(verdict["unknown_evidence"] + component_unknowns))
    verdict["unavailable_evidence"] = verdict["unknown_evidence"]
    if verdict["unknown_evidence"] and verdict["grade"] == "A":
        verdict["grade"] = "B"
    verdict["model_evidence"] = {
        "rp_snapshot": "rp_snapshot",
        "probability": "rp_probability",
        "recommendation": recommendation,
    }
    verdict["tactical_evidence"] = {
        name: module for name, module in modules.items()
        if name not in {"market_movement", "model_disagreement", "calibration", "evidence_quality"}
    }

    return {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "audit_model_version": AUDIT_MODEL_VERSION,
        "mode": audit_mode(),
        "captured_at": _now(),
        "math_unchanged": True,
        "production_influence": False,
        "identity": {
            "fixture_id": request.get("fixture_id") or prediction.get("fixtureId"),
            "player_id": request.get("player_id") or prediction.get("playerId"),
            "player_name": context.get("player_name") or prediction.get("playerName") or prediction.get("canonicalPlayerName"),
            "team_id": context.get("team_id") or prediction.get("fixtureTeamId") or prediction.get("teamId"),
            "team_name": context.get("team_name") or prediction.get("teamName"),
            "opponent_id": context.get("opponent_id") or prediction.get("fixtureOpponentId") or prediction.get("opponentId"),
            "opponent_name": context.get("opponent_name") or prediction.get("opponentName"),
            "league_id": context.get("league_id") or prediction.get("leagueId"),
            "season": context.get("season") or prediction.get("season"),
            "venue": context.get("venue") or prediction.get("resolvedVenue") or prediction.get("venue"),
            "prop_type": prop_type,
            "line": request.get("line") if request.get("line") is not None else prediction.get("line"),
        },
        "rp_snapshot": rp_snapshot,
        "probability": {
            "p_over": p_over,
            "p_under": p_under,
            "selected_side": recommendation if recommendation in {"over", "under"} else None,
            "selected_probability": selected_probability,
            "source": "rp_prediction",
        },
        "conviction": {
            "probability": selected_probability,
            "evidence_quality_score": evidence_score,
            "status": "available" if evidence_score is not None else "unknown",
            "note": "Probability and evidence conviction are tracked separately.",
        },
        "modules": modules,
        "causal_script": causal_script,
        "jarvis_verdict": verdict,
        "evidence_classes": {
            "MODEL_EVIDENCE": verdict["model_evidence"],
            "TACTICAL_EVIDENCE": verdict["tactical_evidence"],
            "UNAVAILABLE_EVIDENCE": verdict["unavailable_evidence"],
        },
        "verdict": {
            "rp_recommendation": recommendation or prediction.get("recommendation"),
            "audit_decision": "RP_RECOMMENDATION_UNCHANGED",
            "jarvis_is_not_a_probability_override": True,
            "grade": "BLOCKED" if anomalies.get("blocks_elite_grade") else "OBSERVATIONAL",
            "confidence_cap": "NO_ELITE_GRADE" if anomalies.get("blocks_elite_grade") else None,
            "reasons": [
                "Audit values are observational until walk-forward validation promotes them.",
                *[flag["code"] for flag in anomalies.get("flags", [])],
            ],
            **verdict,
        },
        "status": {
            "phase_1_snapshot": "partial",
            "phase_2_calibration": "partial",
            "phase_17_definition_gate": "partial",
            "phase_18_anomaly_gate": "complete",
            "phase_25_evidence_quality": "partial",
            "phase_26_probability_conviction_split": "complete",
            "phase_27_disagreement": "not_started",
            "phase_28_robustness": "not_started",
            "phase_29_confidence_caps": "partial",
            "phase_30_orchestration": "partial",
        },
    }


def prediction_event_key(pick_or_request: dict[str, Any]) -> str:
    """Canonical identity for one prediction event, independent of user saves."""
    parts = [
        pick_or_request.get("sport") or "soccer",
        pick_or_request.get("fixtureId") or pick_or_request.get("fixture_id") or "",
        pick_or_request.get("playerId") or pick_or_request.get("player_id") or "",
        pick_or_request.get("propType") or pick_or_request.get("prop_type") or "",
        pick_or_request.get("line") or "",
        str(pick_or_request.get("recommendation") or "").lower(),
    ]
    return "|".join(str(part) for part in parts)


async def ensure_audit_indexes(db: Any) -> None:
    await db.jarvis_prediction_audits.create_index("event_key", unique=True, name="jarvis_audit_event_key")
    await db.jarvis_prediction_audits.create_index(
        [("identity.fixture_id", 1), ("identity.player_id", 1), ("identity.prop_type", 1), ("captured_at", -1)],
        name="jarvis_audit_identity_time",
    )
    # Calibration reads the durable settled ledger by status and newest
    # settlement. Keep this query bounded without changing the picks schema.
    try:
        await db.picks.create_index(
            [("status", 1), ("settledAt", -1), ("timestamp", -1)],
            name="picks_settled_calibration",
        )
    except Exception:
        # The audit index setup is auxiliary; existing deployments may not
        # permit adding an index while Atlas is under quota pressure.
        pass
    await db.jarvis_settlement_postmortems.create_index(
        [("pick_id", 1), ("settled_at", -1)],
        name="jarvis_postmortem_pick_time",
    )
    await db.jarvis_settlement_postmortems.create_index("settlement_event_key", unique=True, name="jarvis_postmortem_event_key")


async def persist_prediction_audit(
    db: Any,
    *,
    pick: dict[str, Any],
    prediction: dict[str, Any],
    request: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    audit = build_audit_snapshot(prediction, request, context=context)
    event_key = prediction_event_key(pick)
    document = {
        "event_key": event_key,
        "identity": audit["identity"],
        "prediction_snapshot": audit["rp_snapshot"],
        "audit_snapshot": audit,
        "schema_version": AUDIT_SCHEMA_VERSION,
        "audit_model_version": AUDIT_MODEL_VERSION,
        "created_at": audit["captured_at"],
        "immutable": True,
    }
    update = {
        "$setOnInsert": document,
        "$set": {"last_seen_at": _now()},
        "$addToSet": {"pick_ids": str(pick.get("pickId") or pick.get("id") or "")},
    }
    result = await db.jarvis_prediction_audits.update_one({"event_key": event_key}, update, upsert=True)
    return {
        "event_key": event_key,
        "audit_id": event_key,
        "created": bool(result.upserted_id),
        "snapshot": audit,
    }


def _wilson_interval(hits: int, n: int) -> dict[str, float] | None:
    if n <= 0:
        return None
    p = hits / n
    z = 1.96
    denominator = 1 + (z * z / n)
    center = (p + (z * z / (2 * n))) / denominator
    spread = (z / denominator) * math.sqrt((p * (1 - p) / n) + (z * z / (4 * n * n)))
    return {"low": round(max(0, center - spread) * 100, 2), "high": round(min(1, center + spread) * 100, 2)}


def _row_probability(row: dict[str, Any], direction: str) -> float | None:
    direct = row.get("pOver" if direction == "over" else "pUnder")
    if direct is None:
        direct = (row.get("bayesianMetrics") or {}).get("pOver" if direction == "over" else "pUnder")
    return _probability(direct)


def _row_direction(row: dict[str, Any]) -> str | None:
    direction = str(row.get("recommendation") or "").lower()
    if direction in {"over", "under"}:
        return direction
    return None


def _single_calibration(rows: list[dict[str, Any]]) -> dict[str, Any]:
    scored = []
    for row in rows:
        direction = _row_direction(row)
        result = str(row.get("result") or "").lower()
        if direction not in {"over", "under"} or result not in {"hit", "miss"}:
            continue
        probability = _row_probability(row, direction)
        if probability is None:
            continue
        scored.append((row, direction, result, probability / 100))
    n = len(scored)
    hits = sum(1 for _row, _direction, result, _prob in scored if result == "hit")
    if not n:
        return {
            "sample_size": 0,
            "hits": 0,
            "misses": 0,
            "hit_rate": None,
            "confidence_interval": None,
            "brier_score": None,
            "log_loss": None,
            "calibration_error": None,
            "warning": "No verified directional HIT/MISS rows with probabilities.",
        }
    brier = sum((prob - (1 if result == "hit" else 0)) ** 2 for _row, _direction, result, prob in scored) / n
    log_loss = sum(
        -math.log(max(1e-6, min(1 - 1e-6, prob if result == "hit" else 1 - prob)))
        for _row, _direction, result, prob in scored
    ) / n
    mean_prob = sum(prob for _row, _direction, _result, prob in scored) / n
    hit_rate = hits / n
    return {
        "sample_size": n,
        "hits": hits,
        "misses": n - hits,
        "hit_rate": round(hit_rate * 100, 2),
        "confidence_interval": _wilson_interval(hits, n),
        "brier_score": round(brier, 6),
        "log_loss": round(log_loss, 6),
        "calibration_error": round(abs(mean_prob - hit_rate) * 100, 2),
        "mean_predicted_probability": round(mean_prob * 100, 2),
        "warning": "Small sample; interval is wide and should not be treated as precise." if n < 30 else None,
    }


def calibration_summary(
    rows: Iterable[dict[str, Any]],
    *,
    prop_type: str | None = None,
    role: str | None = None,
    position: str | None = None,
    league_id: int | None = None,
    venue: str | None = None,
    side: str | None = None,
    model_version: str | None = None,
) -> dict[str, Any]:
    from model_metrics import dedupe_prediction_rows

    filtered = []
    for row in dedupe_prediction_rows(list(rows)):
        if str(row.get("status") or "").lower() != "settled":
            continue
        if prop_type and str(row.get("propType") or "").lower() != prop_type.lower():
            continue
        if role and str(row.get("role") or row.get("tacticalRole") or "").lower() != role.lower():
            continue
        if position and str(row.get("position") or row.get("playerPosition") or "").lower() != position.lower():
            continue
        if league_id is not None and str(row.get("leagueId") or "") != str(league_id):
            continue
        if venue and str(row.get("venue") or "").lower() != venue.lower():
            continue
        direction = _row_direction(row)
        if side and direction != side.lower():
            continue
        if model_version and str(row.get("modelVersion") or row.get("factorLedgerVersion") or "") != model_version:
            continue
        filtered.append(row)

    ordered = sorted(filtered, key=lambda row: str(row.get("settledAt") or row.get("timestamp") or ""))
    latest = ordered[-1:]
    return {
        "filters": {
            "prop_type": prop_type,
            "role": role,
            "position": position,
            "league_id": league_id,
            "venue": venue,
            "side": side,
            "model_version": model_version,
        },
        "overall": _single_calibration(ordered),
        "rolling": {
            "last_25": _single_calibration(ordered[-25:]),
            "last_50": _single_calibration(ordered[-50:]),
            "last_100": _single_calibration(ordered[-100:]),
            "lifetime": _single_calibration(ordered),
        },
        "latest_settlement": {
            "settled_at": latest[0].get("settledAt") if latest else None,
            "pick_id": latest[0].get("pickId") if latest else None,
        },
        "deduplicated_events": len(ordered),
        "source": "db.picks settled verified ledger",
        "no_fake_precision": True,
    }


def line_deviation_ledger_coverage(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Explain why settled rows do or do not qualify for band calibration.

    This is a diagnostic companion to ``compute_line_deviation_bands``. It
    deliberately reports the exact field gate instead of treating a zero-size
    band as proof that no historical picks exist.
    """
    from calibration import DEVIATION_BANDS
    from model_metrics import dedupe_prediction_rows

    raw_rows = list(rows)
    unique_rows = dedupe_prediction_rows(raw_rows)
    gates = Counter()
    eligible_by_band = Counter()
    eligible_by_position = Counter()
    eligible_by_prop = Counter()

    for row in unique_rows:
        if str(row.get("status") or "").lower() != "settled":
            gates["not_settled"] += 1
            continue
        if str(row.get("result") or "").lower() not in {"hit", "miss"}:
            gates["not_directional_result"] += 1
            continue
        direction = _row_direction(row)
        if direction not in {"over", "under"}:
            gates["not_directional_recommendation"] += 1
            continue

        projected = _number(row.get("projectedValue"))
        if projected is None or projected <= 0:
            gates["missing_projected_value"] += 1
            continue
        if _number(row.get("actualValue")) is None:
            gates["missing_actual_value"] += 1
            continue
        line = _number(row.get("line"))
        if line is None or line <= 0:
            gates["missing_line"] += 1
            continue

        deviation = abs(line - projected) / projected
        band = next(
            (
                name for name, lower, upper in DEVIATION_BANDS
                if lower <= deviation < upper
            ),
            None,
        )
        if not band:
            gates["outside_band_definition"] += 1
            continue

        gates["eligible"] += 1
        position = str(row.get("position") or row.get("playerPosition") or "UNSPECIFIED").strip() or "UNSPECIFIED"
        prop_type = str(row.get("propType") or "UNSPECIFIED").strip() or "UNSPECIFIED"
        eligible_by_band[f"{band}|{direction}"] += 1
        eligible_by_position[position] += 1
        eligible_by_prop[prop_type] += 1

    return {
        "raw_settled_rows": len(raw_rows),
        "deduplicated_events": len(unique_rows),
        "field_gate_counts": dict(gates),
        "eligible_by_band_and_direction": dict(sorted(eligible_by_band.items())),
        "eligible_by_position": dict(eligible_by_position.most_common()),
        "eligible_by_prop": dict(eligible_by_prop.most_common()),
        "note": (
            "Line-band calibration requires a settled HIT/MISS with directional "
            "recommendation plus projectedValue, actualValue, and line."
        ),
    }


def _failure_categories(pick: dict[str, Any], settlement: dict[str, Any]) -> list[dict[str, Any]]:
    categories: list[dict[str, Any]] = []
    result = str(settlement.get("result") or pick.get("result") or "").lower()
    actual = _number(settlement.get("actualValue"))
    projected = _number(pick.get("projectedValue") or pick.get("projection"))
    minutes = _number(settlement.get("minutesPlayed") or pick.get("minutesPlayed"))
    expected_minutes = _number(pick.get("minutesProjection") or pick.get("expectedMinutes"))
    if minutes is not None and expected_minutes is not None and abs(minutes - expected_minutes) > 10:
        categories.append({"code": "MINUTES_MISS", "confidence": "measured", "details": {"expected": expected_minutes, "actual": minutes}})
    expected_poss = _number(pick.get("possessionProjection") or pick.get("expectedPossession"))
    actual_poss = _number(settlement.get("teamPossession") or pick.get("teamPossession"))
    if expected_poss is not None and actual_poss is not None and abs(expected_poss - actual_poss) >= 5:
        categories.append({"code": "POSSESSION_MISS", "confidence": "measured", "details": {"expected": expected_poss, "actual": actual_poss}})
    if result == "miss" and actual is not None and projected is not None:
        categories.append({"code": "VARIANCE", "confidence": "fallback", "details": {"projected": projected, "actual": actual, "error": actual - projected}})
    if not categories and result in {"miss", "push"}:
        categories.append({"code": "UNKNOWN", "confidence": "unresolved", "details": {"reason": "No measured attribution signal was stored."}})
    return categories


async def record_settlement_postmortem(
    db: Any,
    *,
    pick: dict[str, Any],
    settlement: dict[str, Any],
    source: str = "settlement_writer",
) -> dict[str, Any]:
    settlement_source = settlement.get("settlementSource") or pick.get("settlementSource") or {}
    settled_at = (
        settlement.get("settledAt")
        or pick.get("settledAt")
        or settlement_source.get("recordedAt")
        or "unknown"
    )
    event_key = _fingerprint({
        "pick_id": pick.get("pickId"),
        "result": settlement.get("result") or pick.get("result"),
        "actual_value": settlement.get("actualValue"),
        "provider": settlement_source.get("provider"),
        "fixture_id": settlement_source.get("fixtureId"),
        "stat_path": settlement_source.get("statPath"),
    })
    document = {
        "settlement_event_key": event_key,
        "pick_id": str(pick.get("pickId") or ""),
        "tracking_id": pick.get("trackingId"),
        "event_key": prediction_event_key(pick),
        "settled_at": settled_at,
        "source": source,
        "result": settlement.get("result") or pick.get("result"),
        "actual_value": settlement.get("actualValue"),
        "actual_minutes": settlement.get("minutesPlayed") or pick.get("minutesPlayed"),
        "settlement_source": settlement_source,
        "expected": {
            "line": pick.get("line"),
            "recommendation": pick.get("recommendation"),
            "projected_value": pick.get("projectedValue") or pick.get("projection"),
            "confidence": pick.get("confidenceScore"),
            "possession": pick.get("possessionProjection") or pick.get("expectedPossession"),
            "minutes": pick.get("minutesProjection") or pick.get("expectedMinutes"),
            "role": pick.get("role") or pick.get("tacticalRole") or pick.get("playerRole"),
        },
        "actual": {
            "possession": settlement.get("teamPossession") or settlement.get("homePoss"),
            "opponent_possession": settlement.get("opponentPossession") or settlement.get("awayPoss"),
            "role": settlement.get("actualRole"),
            "closing_line": settlement.get("closingLine") or pick.get("closingLine"),
            "clv": settlement.get("clv") or pick.get("clv"),
        },
        "failure_attribution": _failure_categories(pick, settlement),
        "created_at": _now(),
        "immutable": True,
    }
    try:
        await db.jarvis_settlement_postmortems.insert_one(document)
        created = True
    except Exception as exc:
        if "duplicate" not in str(exc).lower():
            raise
        created = False
    return {"event_key": event_key, "created": created, "failure_attribution": document["failure_attribution"]}


def implementation_status() -> list[dict[str, Any]]:
    """Honest status for the 30-phase architecture, exposed to owner tooling."""
    statuses = {
        1: ("PARTIAL", "Immutable RP/JARVIS prediction snapshots and settlement audit collection are available; legacy rows need backfill."),
        2: ("PARTIAL", "Calibration endpoint provides deduplicated hit rate, rolling windows, Brier, log loss, intervals, and warnings."),
        3: ("PARTIAL", "RP possession context is preserved; independent same-venue seven-match replay is not enabled."),
        4: ("NOT_STARTED", "Opponent venue effect replay is not enabled."),
        5: ("PARTIAL", "Existing H2H evidence exists; venue-specific possession aggregation is not enabled in the audit."),
        6: ("PARTIAL", "RP possession is compared as provenance-labeled context; independent range model is not promoted."),
        7: ("PARTIAL", "Existing role evidence is surfaced; exact-role audit snapshot is conservative when absent."),
        8: ("PARTIAL", "Existing role cohort endpoint exists; replay-safe saved cohort snapshots are not yet integrated."),
        9: ("PARTIAL", "Existing player logs support several props; cohort-wide metadata capture is incomplete."),
        10: ("NOT_STARTED", "Independent role-cohort statistics are not yet persisted by the audit."),
        11: ("NOT_STARTED", "Possession-normalized cohort production is not yet enabled."),
        12: ("NOT_STARTED", "Player/team volume-share baselines are not yet enabled."),
        13: ("NOT_STARTED", "Teammate redistribution samples are not yet enabled."),
        14: ("NOT_STARTED", "Independent probabilistic minutes simulation is not yet enabled."),
        15: ("COMPLETE", "Completed-fixture first-goal profiles expose shadow-only pre-match game-state branches; they never alter RP math."),
        16: ("NOT_STARTED", "Independent scenario-weighted projections are not yet enabled."),
        17: ("PARTIAL", "Configured stat-definition registry and confidence gate exist; provider reconciliation is incomplete."),
        18: ("PARTIAL", "Snapshot anomaly checks exist; broad provider anomaly monitoring is not yet complete."),
        19: ("PARTIAL", "Odds can be stored with picks; timestamped opening/current/closing line tracking is incomplete."),
        20: ("PARTIAL", "Settlement postmortem records and measured attribution categories exist; all writer paths still need migration."),
        21: ("PARTIAL", "Machine-readable postmortem storage exists; automated narrative generation is not enabled."),
        22: ("PARTIAL", "Walk-forward replay utilities exist; complete layer-by-layer audit comparison is not yet orchestrated."),
        23: ("PARTIAL", "Hierarchical league calibration exists in RP; independent audit coefficients remain shadow-only."),
        24: ("PARTIAL", "Role/prop calibration exists in RP; independent role models remain disabled."),
        25: ("PARTIAL", "RP evidence-quality packet is captured with provenance; full component scoring is not yet independent."),
        26: ("COMPLETE", "Probability and conviction are explicitly separate in the audit contract."),
        27: ("NOT_STARTED", "Independent multi-layer disagreement index is not yet enabled."),
        28: ("NOT_STARTED", "Counterfactual robustness reruns are not yet enabled."),
        29: ("PARTIAL", "Anomaly/stat-definition caps block elite audit grading; full gate matrix remains shadow-only."),
        30: ("PARTIAL", "Feature-gated full-audit orchestration is available around one RP run; independent modules remain clearly labeled."),
    }
    return [{"phase": phase, "status": status, "summary": summary} for phase, (status, summary) in statuses.items()]