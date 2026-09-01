"""H2H lineup grids must upgrade broad player-stat categories when possible."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PREDICT_SOURCE = (ROOT / "routes" / "predict.py").read_text()


def test_h2h_player_rows_fetch_fixture_lineups_and_preserve_position_source():
    assert '"fixtures/lineups", {"fixture": fixture_id}' in PREDICT_SOURCE
    assert "exact_position_from_lineup_payload" in PREDICT_SOURCE
    assert '"positionSource": "fixture_lineup_grid"' in PREDICT_SOURCE


def test_h2h_exact_position_history_can_unlock_target_comparison():
    assert "_historical_exact_position" in PREDICT_SOURCE
    assert '"h2h_fixture_lineup_history"' in PREDICT_SOURCE
    assert "target_specific_pos=specific_position" in PREDICT_SOURCE
    assert '"h2h_fixture_lineup_history"' in PREDICT_SOURCE