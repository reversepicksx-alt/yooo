from pathlib import Path

from tactical_evidence import resolve_observed_role


ROOT = Path(__file__).resolve().parents[1]
PREDICT_SOURCE = (ROOT / "routes" / "predict.py").read_text()
PLAYERS_SOURCE = (ROOT / "routes" / "players.py").read_text()
AI_POSITIONS_SOURCE = (ROOT / "ai_positions.py").read_text()


def test_generic_midfielder_has_no_box_to_box_fallback():
    result = resolve_observed_role("MID", {
        "appearances": 20,
        "shots_total": 40,
        "dribbles_attempts": 50,
        "passes_total": 900,
    })
    assert result["position"] == "MID"
    assert result["role"] is None
    assert result["source"] == "fixture_lineup_category"
    assert "exact CM/CDM/CAM" in " ".join(result["evidence"])
    assert '"Midfielder": ("CM", "Box-to-Box")' not in PREDICT_SOURCE
    assert '"Midfielder": ("CM", "Box-to-Box")' not in PLAYERS_SOURCE


def test_provider_category_fallback_never_returns_exact_midfield_position():
    assert 'return category, "", "provider_category_fallback"' in AI_POSITIONS_SOURCE
    assert '"Midfielder": "CM"' not in AI_POSITIONS_SOURCE
    assert '"specificPosition": ""' in PREDICT_SOURCE


def test_exact_midfield_comparisons_reject_generic_rows_and_role_padding():
    assert "if target_specific_pos not in {" in PREDICT_SOURCE
    assert "observed_exact_target or cached_exact_target" in PREDICT_SOURCE
    assert "_apply_role_match = False" in PREDICT_SOURCE
    assert "exact_opponent_same_position_same_venue" in PREDICT_SOURCE
    assert "roleInferred" in PREDICT_SOURCE


def test_current_generic_lineup_blocks_historical_exact_position_upgrade():
    assert '_current_lineup_observed_position in {"DEF", "MID", "FWD"}' in PREDICT_SOURCE
    assert "fixture_lineup_category" in PREDICT_SOURCE