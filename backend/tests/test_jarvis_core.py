from __future__ import annotations

import asyncio

from jarvis_core import (
    JARVIS_CORE_SCHEMA_VERSION,
    build_prediction_result,
    compare_control_to_core,
    persist_shadow_run,
)


def _control():
    return {
        "playerName": "Test Player",
        "teamName": "Home FC",
        "opponentName": "Away FC",
        "projectedValue": 48.2,
        "pOver": 22.0,
        "pUnder": 78.0,
        "confidenceScore": 71,
        "recommendation": "UNDER",
        "propType": "pass_attempts",
        "line": 57.5,
        "gameLogs": [{"value": 48, "venue": "home", "score": "1-0"}],
        "bayesianMetrics": {"priorMean": 49.0},
        "factorLedger": {"final": {"pUnder": 78.0}},
    }


def test_core_result_is_versioned_and_non_influential():
    result = build_prediction_result(
        request={"fixture_id": 10, "player_id": 20, "prop_type": "pass_attempts", "line": 57.5},
        control_result=_control(),
        context={"fixture_id": 10, "player_id": 20, "venue": "home"},
    )
    assert result["schema_version"] == JARVIS_CORE_SCHEMA_VERSION
    assert result["provenance"]["production_influence"] is False
    assert result["identity"]["fixture_id"] == 10
    assert result["decision"]["p_under"] == 78.0
    assert result["evidence"]["tactical_memory"]["projection_influence"] is False
    assert result["post_layers"]["robustness"]["status"] == "UNKNOWN"


def test_comparison_records_numeric_and_unknown_differences():
    result = build_prediction_result(
        request={"fixture_id": 10, "player_id": 20, "prop_type": "shots", "line": 2.5},
        control_result=_control(),
    )
    comparison = compare_control_to_core(_control(), result)
    assert comparison["math_unchanged"] is True
    assert comparison["production_influence"] is False
    assert comparison["decision_differences"]["p_under"]["status"] == "match"
    assert comparison["stage_differences"]["evidence"]["status"] == "UNKNOWN"


def test_shadow_persistence_is_idempotent():
    class Collection:
        def __init__(self):
            self.calls = []

        async def update_one(self, query, update, upsert=False):
            self.calls.append((query, update, upsert))

    class FakeDb:
        def __init__(self):
            self.jarvis_core_shadow_runs = Collection()

    db = FakeDb()
    request = {"fixture_id": 1, "player_id": 2, "prop_type": "shots", "line": 1.5}
    core = build_prediction_result(request=request, control_result=_control())
    comparison = compare_control_to_core(_control(), core)
    first = asyncio.run(persist_shadow_run(
        db, request=request, control_result=_control(),
        core_result=core, comparison=comparison,
    ))
    second = asyncio.run(persist_shadow_run(
        db, request=request, control_result=_control(),
        core_result=core, comparison=comparison,
    ))
    assert first["event_key"] == second["event_key"]
    assert len(db.jarvis_core_shadow_runs.calls) == 2
    assert db.jarvis_core_shadow_runs.calls[0][2] is True