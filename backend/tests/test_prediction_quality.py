"""Pure tests for deterministic evidence-quality controls."""

from prediction_quality import (
    apply_prediction_quality_controls,
    evaluate_prediction_quality,
)


def _logs(n=6, synthetic=False):
    return [
        {"passes_total": 40 + i, "minutes": 80, "synthetic": synthetic}
        for i in range(n)
    ]


def test_real_logs_are_counted_but_synthetic_rows_are_not():
    quality = evaluate_prediction_quality(
        prop_type="pass_attempts",
        player_logs=_logs(5) + _logs(8, synthetic=True),
        fixture_id=123,
    )
    assert quality["realPlayerLogCount"] == 5
    assert quality["groups"]["player_history"]["status"] == "warning"


def test_strong_fixture_context_produces_high_quality_without_confidence_boost():
    quality = evaluate_prediction_quality(
        prop_type="pass_attempts",
        player_logs=_logs(8),
        h2h_logs=[{"targetStat": 40}] * 3,
        comparable_sample=4,
        team_fixture_stats=[{"totalPasses": 500}] * 4,
        opponent_fixture_stats=[{"totalPasses": 450}] * 4,
        match_dominance={"hasRealPossData": True, "expectedPoss": 57.0},
        lineup_status="confirmed",
        fixture_id=123,
        match_odds={"favorite": "home"},
        position="CM",
        role="deep_playmaker",
    )
    assert quality["score"] >= 78
    assert quality["confidenceCap"] is None


def test_missing_fixture_and_history_cap_confidence_downward():
    quality = evaluate_prediction_quality(
        prop_type="shots",
        player_logs=[],
        lineup_status=None,
        fixture_id=None,
    )
    prediction = {
        "projectedValue": 8.0,
        "recommendation": "OVER",
        "confidenceScore": 82,
        "confidenceLevel": "Very High",
    }
    apply_prediction_quality_controls(prediction, line=7.5, quality=quality)
    assert prediction["confidenceScore"] <= 60
    assert prediction["recommendation"] == "OVER"
    assert prediction["evidenceQuality"]["thinEvidence"] is True


def test_missing_optional_feeds_are_neutral_when_player_history_is_strong():
    quality = evaluate_prediction_quality(
        prop_type="passes",
        player_logs=_logs(8),
        fixture_id=123,
        lineup_status="confirmed",
    )
    prediction = {
        "projectedValue": 70.0,
        "recommendation": "OVER",
        "confidenceScore": 78,
        "confidenceLevel": "Very High",
    }
    apply_prediction_quality_controls(prediction, line=55.5, quality=quality)
    assert quality["score"] >= 58
    assert quality["confidenceCap"] is None
    assert prediction["confidenceScore"] == 78


def test_thin_edge_with_low_evidence_becomes_pass_not_opposite_side():
    quality = evaluate_prediction_quality(
        prop_type="shots",
        player_logs=[],
        fixture_id=None,
    )
    prediction = {
        "projectedValue": 7.6,
        "recommendation": "OVER",
        "confidenceScore": 55,
    }
    apply_prediction_quality_controls(prediction, line=7.5, quality=quality)
    assert prediction["recommendation"] == "PASS"
    assert prediction["passLeaning"] == "OVER"
    assert prediction["skipReason"] == "THIN_EDGE_LOW_EVIDENCE"
    assert prediction["evidenceQuality"]["edgePercent"] == 1.33


def test_quality_controls_never_boost_confidence():
    quality = evaluate_prediction_quality(
        prop_type="passes",
        player_logs=_logs(8),
        fixture_id=123,
    )
    prediction = {"projectedValue": 50, "recommendation": "OVER", "confidenceScore": 45}
    apply_prediction_quality_controls(prediction, line=40, quality=quality)
    assert prediction["confidenceScore"] == 45