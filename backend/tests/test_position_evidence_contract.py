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
    assert "existing profile preserved" in PREDICT_SOURCE


def test_exact_midfield_comparisons_reject_generic_rows_and_role_padding():
    assert "if target_specific_pos not in {" in PREDICT_SOURCE
    assert "observed_exact_target or cached_exact_target" in PREDICT_SOURCE
    assert "_apply_role_match = False" in PREDICT_SOURCE
    assert "exact_opponent_same_position_same_venue" in PREDICT_SOURCE
    assert "roleInferred" in PREDICT_SOURCE


def test_current_generic_lineup_blocks_historical_exact_position_upgrade():
    assert '_current_lineup_observed_position in {"DEF", "MID", "FWD"}' in PREDICT_SOURCE
    assert "fixture_lineup_category" in PREDICT_SOURCE


def test_midfield_grid_evidence_can_admit_exact_position_comparisons():
    assert 'shape[:3] == [4, 3, 3]' in (ROOT / "tactical_evidence.py").read_text()
    assert 'shape[:3] == [4, 2, 3]' in (ROOT / "tactical_evidence.py").read_text()
    assert 'shape[:4] == [3, 1, 4, 2]' in (ROOT / "tactical_evidence.py").read_text()
    assert 'return "CM"' in (ROOT / "tactical_evidence.py").read_text()
    assert 'target_specific_pos=specific_position' in PREDICT_SOURCE


def test_api_sports_lineup_identity_is_normalized_before_exact_position_join():
    assert "def _normalize_provider_player_id(value)" in PREDICT_SOURCE
    assert "_normalize_provider_player_id(pl.get(\"id\")) == target_id" in PREDICT_SOURCE
    assert "_normalize_provider_player_id(p_id)" in PREDICT_SOURCE
    assert "_normalize_provider_player_id(item.get(\"player\", {}).get(\"id\"))" in PREDICT_SOURCE


def test_prediction_does_not_erase_grounded_profile_on_category_fallback():
    assert "existing profile preserved" in PREDICT_SOURCE
    assert '"specificPosition": ""' not in PREDICT_SOURCE[
        PREDICT_SOURCE.index('if not specific_position:'):PREDICT_SOURCE.index(
            'print(\n                    f"[POS RESOLVE] Category fallback:',
            PREDICT_SOURCE.index('if not specific_position:'),
        )
    ]


def test_comparison_profiles_accept_string_or_integer_provider_ids():
    comparison_start = PREDICT_SOURCE.index("cached_pr = await db.player_positions.find_one(")
    comparison_block = PREDICT_SOURCE[comparison_start:comparison_start + 900]
    assert '{"playerId": p_id_key}' in comparison_block
    assert '{"playerId": str(p_id)}' in comparison_block


def test_grounded_player_profiles_are_durable_without_prompt_version_or_ttl_gate():
    from ai_positions import _trusted_cached_profile

    profile = _trusted_cached_profile(
        {
            "specificPosition": "CM",
            "role": "Box-to-Box",
            "source": "gemini_web_grounded",
        },
        "Midfielder",
    )
    assert profile == ("CM", "Box-to-Box")


def test_api_sports_lineup_history_profiles_are_trusted():
    from ai_positions import _trusted_cached_profile

    profile = _trusted_cached_profile(
        {
            "specificPosition": "LW",
            "role": "",
            "source": "api_sports_lineup_history",
        },
        "Attacker",
    )
    assert profile == ("LW", "")