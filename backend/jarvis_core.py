"""Versioned JARVIS Core shadow contract.

Phase 1 intentionally wraps the existing Reverse Picks control result rather
than replacing its math.  This gives the migration a stable, provenance-rich
boundary while disagreements and missing modules are measured explicitly.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from engine_base import normalize_response

JARVIS_CORE_SCHEMA_VERSION = "jarvis-core.v1"
JARVIS_CORE_MODEL_VERSION = "jarvis-core-shadow.v1"
SHADOW_COLLECTION = "jarvis_core_shadow_runs"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fingerprint(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()[:24]


def _unknown(reason: str) -> dict[str, Any]:
    return {"status": "UNKNOWN", "value": None, "reason": reason}


def _value(result: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = result.get(key)
        if value is not None:
            return value
    return None


def canonical_request(body: Any) -> dict[str, Any]:
    return {
        "sport": "soccer",
        "fixture_id": body.fixture_id,
        "player_id": body.player_id,
        "prop_type": body.prop_type,
        "line": body.line,
        "odds": body.odds,
        "position_override": body.position_override,
        "role_override": body.role_override,
    }


def build_prediction_result(
    *,
    request: dict[str, Any],
    control_result: dict[str, Any],
    context: dict[str, Any] | None = None,
    tactical_memory: list[dict[str, Any]] | None = None,
    tactical_memory_status: str = "UNKNOWN",
    tactical_memory_reason: str | None = None,
) -> dict[str, Any]:
    """Create the shared result shape without recalculating production values."""
    context = context or {}
    result = normalize_response(dict(control_result))
    memory = {
        "status": tactical_memory_status,
        "records": tactical_memory or [],
        "projection_influence": False,
        "precedence": (
            "current verified fixture, lineup, role, manager/regime, injury, "
            "and tactical evidence supersedes historical memory"
        ),
        "provenance": "jarvis_tactical_memory",
    }
    if tactical_memory_reason:
        memory["reason"] = tactical_memory_reason

    evidence = {
        "fixture": {"status": "available", "source": "verified_prediction_context"},
        "player": {"status": "available", "source": "verified_prediction_context"},
        "tactical_memory": memory,
        "role": (
            {"status": "available", "source": "control_prediction"}
            if _value(result, "exactTacticalRole", "tacticalRole", "role", "playerRole")
            else _unknown("No exact tactical role was exposed by the control result.")
        ),
        "venue": (
            {"status": "available", "source": "verified_prediction_context", "value": context.get("venue")}
            if context.get("venue")
            else _unknown("Verified venue was not available.")
        ),
        "possession": (
            {"status": "available", "source": "control_prediction"}
            if any(_value(result, key) is not None for key in ("teamPossession", "possessionProjection", "expectedPossession"))
            else _unknown("Possession evidence was not exposed by the control result.")
        ),
        "market": (
            {"status": "available", "source": "control_prediction"}
            if result.get("marketContext") is not None
            else _unknown("Market context was not available in this run.")
        ),
    }
    quantitative = {
        "prior": (result.get("priorMean") if result.get("priorMean") is not None else
                  (result.get("bayesianMetrics") or {}).get("priorMean")),
        "momentum": (result.get("momentumMean") if result.get("momentumMean") is not None else
                     (result.get("bayesianMetrics") or {}).get("momentumMean")),
        "covariates": (result.get("matchFactors") or result.get("covariates") or {}),
        "posterior": (result.get("projectedValue") if result.get("projectedValue") is not None else result.get("projection")),
        "standard_deviation": _value(result, "effectiveStd", "std", "standardDeviation"),
        "simulation": result.get("simulation") or result.get("monteCarlo"),
        "p_over": result.get("pOver"),
        "p_under": result.get("pUnder"),
        "calibration": result.get("calibration") or {
            "historical_rate": result.get("propHistoricalRate"),
            "sample_size": result.get("propHistoricalN"),
        },
    }
    canonical = {
        "schema_version": JARVIS_CORE_SCHEMA_VERSION,
        "model_version": JARVIS_CORE_MODEL_VERSION,
        "captured_at": _now(),
        "request": request,
        "identity": {
            "fixture_id": context.get("fixture_id") or request.get("fixture_id"),
            "player_id": context.get("player_id") or request.get("player_id"),
            "player_name": context.get("player_name") or result.get("playerName"),
            "team_id": context.get("team_id") or result.get("teamId"),
            "team_name": context.get("team_name") or result.get("teamName"),
            "opponent_id": context.get("opponent_id") or result.get("opponentId"),
            "opponent_name": context.get("opponent_name") or result.get("opponentName"),
            "league_id": context.get("league_id") or result.get("leagueId"),
            "venue": context.get("venue") or result.get("venue"),
            "prop_type": request.get("prop_type"),
            "line": request.get("line"),
        },
        "line_context": {
            "saved_line": request.get("line"),
            "current_line": result.get("currentLine"),
            "movement": result.get("lineMovement"),
            "status": "available" if request.get("line") is not None else "UNKNOWN",
        },
        "game_logs": result.get("gameLogs") or _unknown("Game logs were not exposed by the control result."),
        "exact_role": _value(result, "exactTacticalRole", "tacticalRole", "role", "playerRole") or _unknown("Exact role unavailable."),
        "tactical_fingerprint": _unknown("JarvisCore tactical synthesis is shadow-only in Phase 1."),
        "evidence": evidence,
        "quantitative": quantitative,
        "post_layers": {
            "factor_ledger": result.get("factorLedger") or _unknown("Factor ledger unavailable."),
            "safety": result.get("safetyRating") or result.get("safetyState") or _unknown("Safety state unavailable."),
            "opposite_case": result.get("strongestOppositeCase") or _unknown("Opposite case unavailable."),
            "robustness": result.get("robustness") or _unknown("Robustness test unavailable."),
        },
        "decision": {
            "projection": _value(result, "projectedValue", "projection"),
            "p_over": result.get("pOver"),
            "p_under": result.get("pUnder"),
            "confidence": result.get("confidenceScore"),
            "recommendation": result.get("recommendation"),
            "tactical_verdict": result.get("tacticalVerdict") or _unknown("Tactical verdict unavailable."),
        },
        "provenance": {
            "control_source": "Reverse Picks production prediction",
            "raw_provider_data_duplicated": False,
            "production_influence": False,
            "unavailable_evidence_is_unknown": True,
        },
    }
    canonical["fingerprints"] = {
        "request": _fingerprint(request),
        "control_result": _fingerprint({
            key: result.get(key)
            for key in ("projectedValue", "projection", "pOver", "pUnder", "confidenceScore", "recommendation")
        }),
        "canonical_result": _fingerprint(canonical),
    }
    return canonical


def compare_control_to_core(control_result: dict[str, Any], core_result: dict[str, Any]) -> dict[str, Any]:
    fields = (
        ("projection", control_result.get("projectedValue", control_result.get("projection")),
         core_result["decision"].get("projection")),
        ("p_over", control_result.get("pOver"), core_result["decision"].get("p_over")),
        ("p_under", control_result.get("pUnder"), core_result["decision"].get("p_under")),
        ("confidence", control_result.get("confidenceScore"), core_result["decision"].get("confidence")),
        ("recommendation", control_result.get("recommendation"), core_result["decision"].get("recommendation")),
    )
    differences = {}
    for name, control, core in fields:
        if control is None or core is None:
            differences[name] = {"status": "UNKNOWN", "control": control, "jarvis_core": core}
        else:
            try:
                delta = float(core) - float(control)
            except (TypeError, ValueError):
                delta = None
            differences[name] = {
                "status": "match" if control == core else "difference",
                "control": control,
                "jarvis_core": core,
                "delta": delta,
            }
    return {
        "schema_version": JARVIS_CORE_SCHEMA_VERSION,
        "control_model": "reverse-picks-production",
        "jarvis_core_model": JARVIS_CORE_MODEL_VERSION,
        "math_unchanged": True,
        "production_influence": False,
        "stage_differences": {
            "identity": {"status": "available", "difference": False},
            "evidence": {"status": "UNKNOWN", "difference": None},
            "quantitative": {"status": "control_wrapped_in_phase_1", "difference": False},
            "post_layers": {"status": "UNKNOWN", "difference": None},
        },
        "decision_differences": differences,
        "provenance_differences": [],
    }


async def persist_shadow_run(db: Any, *, request: dict[str, Any], control_result: dict[str, Any],
                             core_result: dict[str, Any], comparison: dict[str, Any]) -> dict[str, Any]:
    event_key = _fingerprint({
        "request": request,
        "control": core_result["fingerprints"]["control_result"],
    })
    document = {
        "event_key": event_key,
        "schema_version": JARVIS_CORE_SCHEMA_VERSION,
        "model_version": JARVIS_CORE_MODEL_VERSION,
        "request": request,
        "production": {
            "projection": control_result.get("projectedValue", control_result.get("projection")),
            "p_over": control_result.get("pOver"),
            "p_under": control_result.get("pUnder"),
            "confidence": control_result.get("confidenceScore"),
            "recommendation": control_result.get("recommendation"),
        },
        "jarvis_core": core_result,
        "comparison": comparison,
        "created_at": _now(),
        "immutable": True,
    }
    await db.jarvis_core_shadow_runs.update_one(
        {"event_key": event_key},
        {"$setOnInsert": document},
        upsert=True,
    )
    return {"event_key": event_key, "persisted": True}


async def ensure_shadow_indexes(db: Any) -> None:
    try:
        await db.jarvis_core_shadow_runs.create_index(
            "event_key", unique=True, name="jarvis_core_shadow_event_key"
        )
    except Exception:
        pass