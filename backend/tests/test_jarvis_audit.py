from __future__ import annotations

from jarvis_audit import (
    STAT_DEFINITIONS,
    audit_enabled,
    audit_mode,
    build_audit_snapshot,
    calibration_summary,
    implementation_status,
    prediction_event_key,
    record_settlement_postmortem,
)


def _prediction(**overrides):
    result = {
        "fixtureId": 123,
        "playerId": 456,
        "playerName": "Audit Player",
        "teamName": "Home FC",
        "opponentName": "Away FC",
        "leagueId": 253,
        "resolvedVenue": "home",
        "propType": "pass_attempts",
        "line": 57.5,
        "recommendation": "under",
        "projectedValue": 48.2,
        "pOver": 22.0,
        "pUnder": 78.0,
        "confidenceScore": 71,
        "bayesianMetrics": {
            "priorSamples": 18,
            "venueAvg": 51.0,
            "venueSamples": 8,
            "opponentAllowedAvg": 59.0,
            "opponentAllowedSamples": 12,
        },
        "evidenceQuality": {"score": 64, "level": "MEDIUM", "version": "eq-v1"},
        "factorLedger": {"final": {"pUnder": 78.0}},
        "factorLedgerVersion": "rp-v1",
        "factorLedgerFingerprint": "rp-fingerprint",
    }
    result.update(overrides)
    return result


def test_audit_preserves_rp_math_and_separates_conviction():
    snapshot = build_audit_snapshot(
        _prediction(),
        {"fixture_id": 123, "player_id": 456, "prop_type": "pass_attempts", "line": 57.5},
        context={"venue": "home"},
    )

    assert snapshot["math_unchanged"] is True
    assert snapshot["production_influence"] is False
    assert snapshot["probability"]["p_over"] == 22.0
    assert snapshot["probability"]["p_under"] == 78.0
    assert snapshot["probability"]["selected_probability"] == 78.0
    assert snapshot["conviction"]["evidence_quality_score"] == 64.0
    assert snapshot["modules"]["independent_venue_possession"]["status"] == "not_started"
    assert snapshot["modules"]["stat_definition"]["status"] == "available"
    assert snapshot["rp_snapshot"]["fingerprint"]


def test_audit_fingerprint_is_stable_when_capture_time_changes():
    request = {"fixture_id": 123, "player_id": 456, "prop_type": "shots", "line": 2.5}
    first = build_audit_snapshot(_prediction(propType="shots"), request)
    second = build_audit_snapshot(_prediction(propType="shots"), request)
    assert first["rp_snapshot"]["fingerprint"] == second["rp_snapshot"]["fingerprint"]


def test_audit_reads_probabilities_from_jarvis_diagnostic_final_shape():
    diagnostic = {
        "final": {
            "recommendation": "under",
            "projected_value": 48.2,
            "p_over": 22.0,
            "p_under": 78.0,
            "confidence_score": 71,
        },
        "propType": "pass_attempts",
    }
    snapshot = build_audit_snapshot(
        diagnostic,
        {"fixture_id": 123, "player_id": 456, "prop_type": "pass_attempts", "line": 57.5},
    )
    assert snapshot["probability"]["p_over"] == 22.0
    assert snapshot["probability"]["p_under"] == 78.0
    assert snapshot["probability"]["selected_probability"] == 78.0
    assert snapshot["verdict"]["rp_recommendation"] == "under"


def test_audit_mode_can_be_disabled_without_touching_rp(monkeypatch):
    monkeypatch.setenv("JARVIS_FULL_AUDIT_MODE", "off")
    assert audit_mode() == "off"
    assert audit_enabled() is False


def test_unknown_prop_does_not_get_a_fake_stat_definition():
    snapshot = build_audit_snapshot(
        _prediction(propType="unknown_stat"),
        {"fixture_id": 123, "player_id": 456, "prop_type": "unknown_stat", "line": 1},
    )

    assert snapshot["modules"]["stat_definition"]["status"] == "unknown"
    assert snapshot["modules"]["stat_definition"]["reason"]
    assert snapshot["modules"]["anomaly_detection"]["blocks_elite_grade"] is True


def test_calibration_deduplicates_prediction_events_and_reports_metrics():
    rows = [
        {
            "pickId": "one",
            "fixtureId": 10,
            "playerId": 1,
            "propType": "pass_attempts",
            "line": 50,
            "recommendation": "under",
            "pUnder": 80,
            "result": "hit",
            "status": "settled",
            "settledAt": "2026-01-02T00:00:00+00:00",
        },
        {
            "pickId": "duplicate-save",
            "fixtureId": 10,
            "playerId": 1,
            "propType": "pass_attempts",
            "line": 50,
            "recommendation": "under",
            "pUnder": 80,
            "result": "hit",
            "status": "settled",
            "settledAt": "2026-01-03T00:00:00+00:00",
        },
        {
            "pickId": "two",
            "fixtureId": 11,
            "playerId": 2,
            "propType": "pass_attempts",
            "line": 50,
            "recommendation": "under",
            "pUnder": 70,
            "result": "miss",
            "status": "settled",
            "settledAt": "2026-01-04T00:00:00+00:00",
        },
    ]

    summary = calibration_summary(rows, prop_type="pass_attempts", side="under")
    assert summary["deduplicated_events"] == 2
    assert summary["overall"]["sample_size"] == 2
    assert summary["overall"]["hits"] == 1
    assert summary["overall"]["misses"] == 1
    assert summary["overall"]["hit_rate"] == 50.0
    assert summary["overall"]["brier_score"] is not None
    assert summary["overall"]["log_loss"] is not None
    assert summary["no_fake_precision"] is True


def test_event_key_is_independent_of_tracking_id():
    base = {
        "sport": "soccer",
        "fixtureId": 99,
        "playerId": 12,
        "propType": "shots",
        "line": 2.5,
        "recommendation": "over",
    }
    assert prediction_event_key({**base, "trackingId": "TRK-one"}) == prediction_event_key(
        {**base, "trackingId": "TRK-two"}
    )


def test_phase_status_report_is_complete_and_defines_all_phases():
    statuses = implementation_status()
    assert len(statuses) == 30
    assert {entry["phase"] for entry in statuses} == set(range(1, 31))
    assert all(entry["status"] in {"COMPLETE", "PARTIAL", "BLOCKED", "NOT_STARTED"} for entry in statuses)
    assert "pass_attempts" in STAT_DEFINITIONS


def test_settlement_postmortem_is_idempotent():
    class DuplicateCollection:
        def __init__(self):
            self.keys = set()

        async def insert_one(self, document):
            key = document["settlement_event_key"]
            if key in self.keys:
                raise RuntimeError("duplicate key")
            self.keys.add(key)

    class FakeDb:
        def __init__(self):
            self.jarvis_settlement_postmortems = DuplicateCollection()

    db = FakeDb()
    pick = {
        "pickId": "pick-1",
        "trackingId": "tracking-1",
        "fixtureId": 99,
        "playerId": 12,
        "propType": "shots",
        "line": 2.5,
        "recommendation": "over",
        "projectedValue": 3.1,
    }
    settlement = {
        "result": "miss",
        "actualValue": 1,
        "settlementSource": {
            "verified": True,
            "provider": "api-football",
            "fixtureId": 99,
            "statPath": "fixtures.players.shots",
        },
    }

    import asyncio

    first = asyncio.run(record_settlement_postmortem(db, pick=pick, settlement=settlement))
    second = asyncio.run(record_settlement_postmortem(db, pick=pick, settlement=settlement))
    assert first["created"] is True
    assert second["created"] is False
    assert first["event_key"] == second["event_key"]