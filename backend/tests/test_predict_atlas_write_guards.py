from pathlib import Path


PREDICT_SOURCE = (
    Path(__file__).resolve().parents[1] / "routes" / "predict.py"
).read_text()


def test_prediction_route_guards_optional_atlas_cache_writes():
    """Cache persistence must never turn a computed prediction into HTTP 500."""
    required_guard_logs = (
        "[FIXTURE CACHE WRITE] skipped",
        "[POSSESSION CACHE WRITE] skipped",
        "[POS ROLE CACHE WRITE] skipped",
        "[POSITION CACHE WRITE] skipped",
        "[ROLE EVIDENCE] persistence skipped",
        "[PREDICTION PERSISTENCE] skipped",
    )
    for log_message in required_guard_logs:
        assert log_message in PREDICT_SOURCE, (
            f"Missing fail-open logging for optional prediction persistence: "
            f"{log_message}"
        )


def test_category_fallback_does_not_write_an_empty_position_profile():
    """A timeout/category fallback must not erase durable position evidence."""
    fallback_start = PREDICT_SOURCE.index(
        "if not specific_position:",
        PREDICT_SOURCE.index("elif player_position in GENERIC_POSITIONS"),
    )
    fallback_end = PREDICT_SOURCE.index(
        'print(\n                    f"[POS RESOLVE] Category fallback:',
        fallback_start,
    )
    fallback_block = PREDICT_SOURCE[fallback_start:fallback_end]
    assert "player_positions.update_one" not in fallback_block
    assert "existing profile preserved" in PREDICT_SOURCE
