from pathlib import Path


PREDICT_SOURCE = (
    Path(__file__).resolve().parents[1] / "routes" / "predict.py"
).read_text()
PICKS_SOURCE = (
    Path(__file__).resolve().parents[1] / "routes" / "picks.py"
).read_text()


def test_position_cohort_requires_same_role_and_has_broad_api_pool():
    assert "target_role=None" in PREDICT_SOURCE
    assert "_apply_role_match and candidate_role != target_role" in PREDICT_SOURCE
    assert '"CB", "LB", "RB", "LWB", "RWB"' in PREDICT_SOURCE
    assert "target_role=display_role or player_role" in PREDICT_SOURCE
    assert '"last": _cohort_fixture_lookback' in PREDICT_SOURCE
    assert "fetch_position_comparison(" in PREDICT_SOURCE
    assert "return unique[:15]" in PREDICT_SOURCE
    assert '"evidenceWeight"' in PREDICT_SOURCE


def test_position_cohort_is_labeled_exact_opponent_same_role_evidence():
    assert '"sourceScope": position_comparison_scope' in PREDICT_SOURCE
    assert '"exact_opponent_same_position_same_venue"' in PREDICT_SOURCE
    assert '"targetRole": display_role or player_role' in PREDICT_SOURCE
    assert '"targetPosition": specific_position or display_position' in PREDICT_SOURCE
    assert '"passAttempts": (pstats.get("passes") or {}).get("total")' in PREDICT_SOURCE
    assert '"matchPosition": observed_normalized or pos or None' in PREDICT_SOURCE
    assert '"weightMethod": _cohort_evidence.get("weightMethod")' in PREDICT_SOURCE
    assert '"avgStatValue": comp_avg' in PREDICT_SOURCE
    assert '"teamPossession": team_poss' in PREDICT_SOURCE
    assert '"oppPossession": opp_poss' in PREDICT_SOURCE
    assert '"avgOpponentPossession": comp_opp_poss_avg' in PREDICT_SOURCE
    assert '"expectedPlayerPossession": current_expected_player_poss' in PREDICT_SOURCE
    assert '"weightedAverage": _cohort_evidence.get("average")' in PREDICT_SOURCE
    assert '"_legacyModelAverage"' in PREDICT_SOURCE
    assert '"positionVerified": position_verified' in PREDICT_SOURCE
    assert '"positionSource": position_source' in PREDICT_SOURCE
    assert "Broad provider categories (DEF/MID/FWD)" in PREDICT_SOURCE
    assert "legacy_unique" in PREDICT_SOURCE


def test_cohort_verdict_is_not_evaluated_before_prediction_exists():
    # The cohort is assembled before deterministic synthesis creates the
    # prediction dict. Verdict reconciliation belongs to the final response
    # stage, after late recommendation guards have completed.
    premature_block = '''"verdict": position_cohort_verdict(
                    _cohort_evidence,
                    prediction.get("recommendation"),
                    req.line,
                ),'''
    assert premature_block not in PREDICT_SOURCE
    assert 'prediction["positionComparison"]' in PREDICT_SOURCE
    assert 'prediction.get("recommendation"),' in PREDICT_SOURCE


def test_required_pick_save_reports_storage_full_explicitly():
    assert "status_code=507" in PICKS_SOURCE
    assert "Pick was not saved because database storage is full." in PICKS_SOURCE
    assert "await db.picks.update_one(" in PICKS_SOURCE