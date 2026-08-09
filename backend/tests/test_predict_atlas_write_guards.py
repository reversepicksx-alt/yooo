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


def test_category_position_cache_write_is_inside_exception_guard():
    """Broad-category cache writes must remain fail-open and non-specific."""
    start = PREDICT_SOURCE.index(
        'try:\n                    await db.player_positions.update_one(',
    )
    end = PREDICT_SOURCE.index(
        'print(\n                    f"[POS RESOLVE] Category fallback:',
        start,
    )
    block = PREDICT_SOURCE[start:end]
    assert "try:" in block
    assert "except Exception as _position_cache_err:" in block
    assert "await db.player_positions.update_one(" in block
