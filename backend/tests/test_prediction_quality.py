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


# ── Cache-format log tests ────────────────────────────────────────────────────
# When the API-Football quota is exhausted, Stage 0 (MongoDB fixture_player_cache)
# serves logs where the target stat field may be None (API returned null = 0 stat,
# not "data unavailable").  Established players must not get a confidence cap
# just because some cached games recorded null shots/passes.


def _cache_logs_shots(n=20, shots_none_count=15):
    """Simulate Stage-0 cache logs: real fixtures, minutes>0, shots_total often null."""
    logs = []
    for i in range(n):
        shots_val = None if i < shots_none_count else (i - shots_none_count + 1)
        logs.append({
            "minutes": 90,
            "passes_total": 40 + i,
            "shots_total": shots_val,
            "shots_on": None if shots_val is None else max(0, shots_val - 1),
            "venue": "home" if i % 2 == 0 else "away",
            # No "synthetic" key — these are real fixture-cache entries
        })
    return logs


def test_cache_logs_with_null_shots_are_counted_as_real():
    """Stage-0 cache returns logs where shots_total=None (player had 0 shots).
    These must be counted as real game logs — not excluded — so an established
    player like Salah does not incorrectly get a realPlayerLogCount=1 confidence cap.
    """
    logs = _cache_logs_shots(n=20, shots_none_count=19)  # 19/20 have shots_total=None
    quality = evaluate_prediction_quality(
        prop_type="shots",
        player_logs=logs,
        fixture_id=999,
    )
    # All 20 logs have minutes=90 and no synthetic flag — all must be counted
    assert quality["realPlayerLogCount"] == 20
    assert quality["groups"]["player_history"]["status"] == "applied"
    assert quality["confidenceCap"] is None


def test_cache_logs_established_player_no_cap():
    """An established player with 20 real cached game logs and a verified fixture
    must not have their confidence capped even when all stat fields are null.
    """
    logs = _cache_logs_shots(n=20, shots_none_count=20)  # all null shots_total
    quality = evaluate_prediction_quality(
        prop_type="shots",
        player_logs=logs,
        fixture_id=123,
    )
    prediction = {
        "projectedValue": 3.5,
        "recommendation": "OVER",
        "confidenceScore": 72,
        "confidenceLevel": "High",
    }
    apply_prediction_quality_controls(prediction, line=2.5, quality=quality)
    assert quality["realPlayerLogCount"] == 20
    assert quality["confidenceCap"] is None
    assert prediction["confidenceScore"] == 72, (
        f"Confidence was incorrectly capped to {prediction['confidenceScore']}"
    )


def test_synthetic_logs_with_minutes_are_not_counted():
    """synthetic=True rows must never count as real, even with minutes>0."""
    logs = [
        {"minutes": 90, "passes_total": 50, "synthetic": True}
        for _ in range(10)
    ]
    quality = evaluate_prediction_quality(
        prop_type="passes",
        player_logs=logs,
        fixture_id=123,
    )
    assert quality["realPlayerLogCount"] == 0
    assert quality["groups"]["player_history"]["status"] == "unavailable"


def test_cache_logs_zero_minutes_are_not_counted():
    """Logs with minutes=0 (player DNP or not in squad) are not real evidence."""
    logs = [
        {"minutes": 0, "passes_total": None, "shots_total": None}
        for _ in range(10)
    ]
    quality = evaluate_prediction_quality(
        prop_type="shots",
        player_logs=logs,
        fixture_id=123,
    )
    assert quality["realPlayerLogCount"] == 0


def test_target_fields_covers_new_prop_types():
    """fouls_drawn, fouls_committed, yellow_cards, shots_assisted must map correctly."""
    from prediction_quality import _TARGET_FIELDS
    assert _TARGET_FIELDS.get("fouls_drawn") == "fouls_drawn"
    assert _TARGET_FIELDS.get("fouls_committed") == "fouls_committed"
    assert _TARGET_FIELDS.get("yellow_cards") == "cards_yellow"
    assert _TARGET_FIELDS.get("shots_assisted") == "passes_key"

    # These logs use the cache field name for yellow_cards
    logs = [
        {"minutes": 85, "cards_yellow": 1}
        for _ in range(8)
    ]
    quality = evaluate_prediction_quality(
        prop_type="yellow_cards",
        player_logs=logs,
        fixture_id=321,
    )
    assert quality["realPlayerLogCount"] == 8