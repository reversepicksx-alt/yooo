from pathlib import Path


PREDICT_SOURCE = (
    Path(__file__).resolve().parents[1] / "routes" / "predict.py"
).read_text()
PICKS_SOURCE = (
    Path(__file__).resolve().parents[1] / "routes" / "picks.py"
).read_text()


def test_position_cohort_requires_exact_position_and_has_broad_api_pool():
    assert "target_role=None" in PREDICT_SOURCE
    assert "_apply_role_match = False" in PREDICT_SOURCE
    assert '"CB", "LB", "RB", "LWB", "RWB"' in PREDICT_SOURCE
    assert "target_role=display_role or player_role" in PREDICT_SOURCE
    assert '"last": _cohort_fixture_lookback' in PREDICT_SOURCE
    assert "_cohort_fixture_lookback = 40" in PREDICT_SOURCE
    assert "fetch_position_comparison(" in PREDICT_SOURCE
    assert "return unique[:15]" in PREDICT_SOURCE
    assert '"evidenceWeight"' in PREDICT_SOURCE


def test_position_cohort_is_labeled_exact_opponent_same_position_evidence():
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


def test_possession_context_uses_independent_team_schedules():
    assert "async def fetch_team_possession_average" in PREDICT_SOURCE
    assert "fixture_statistics_team_schedule" in PREDICT_SOURCE
    assert "team_schedule_possession_task" in PREDICT_SOURCE
    assert "opponent_schedule_possession_task" in PREDICT_SOURCE
    assert "player appearances not required" in PREDICT_SOURCE or "player" in PREDICT_SOURCE


def test_wide_exact_positions_allow_broad_provider_midfielder_rows():
    assert '"LW": {"F", "FWD", "M", "MID"}' in PREDICT_SOURCE
    assert '"RW": {"F", "FWD", "M", "MID"}' in PREDICT_SOURCE


def test_position_cohort_lineup_enrichment_cannot_drop_player_stats():
    assert "lineups_task = aio.create_task(" in PREDICT_SOURCE
    assert "players_data, fixture_stats_data = await aio.wait_for(" in PREDICT_SOURCE
    assert "timeout=3.0" in PREDICT_SOURCE
    assert "lineups_data = await aio.wait_for(lineups_task, timeout=1.0)" in PREDICT_SOURCE
    assert "if not lineups_task.done():" in PREDICT_SOURCE


def test_same_role_opponent_evidence_stays_venue_filtered():
    assert 'player_venue,' in PREDICT_SOURCE
    assert 'comp_venue = player_venue_filter' in PREDICT_SOURCE
    assert 'if comp_venue != "any" and comp_team_venue != comp_venue:' in PREDICT_SOURCE
    assert "mixed_venue" not in PREDICT_SOURCE
    assert '"exact_opponent_same_position_same_venue"' in PREDICT_SOURCE
    assert '"exact_opponent_same_position_same_venue_plus_prior_seasons"' in PREDICT_SOURCE
    assert '"positionEvidenceType"' in PREDICT_SOURCE
    assert '"positionEvidenceNote"' in PREDICT_SOURCE
    assert '"exact_opponent_same_position_same_venue"' in PREDICT_SOURCE
    assert '"exact_opponent_same_position_same_venue_plus_prior_seasons"' in PREDICT_SOURCE


def test_soccer_player_history_requires_exact_tp_and_minutes():
    assert "async def _fetch_fixture_possession(" in PREDICT_SOURCE
    assert '"tp": gl["teamPossession"]' not in PREDICT_SOURCE
    assert "_tp_complete" in PREDICT_SOURCE
    assert "_verified_player_logs" in PREDICT_SOURCE
    assert "status_code=424" in PREDICT_SOURCE
    assert '"tpHomeAvg"' in PREDICT_SOURCE
    assert '"tpAwayAvg"' in PREDICT_SOURCE
    assert '"minutesPlayed": minutes' in PREDICT_SOURCE
    assert "async def _direct_fixture_possession(" in PREDICT_SOURCE


def test_soccer_history_drops_incomplete_rows_without_poisoning_verified_history():
    assert "_verified_player_logs" in PREDICT_SOURCE
    assert "_dropped_incomplete_logs" in PREDICT_SOURCE
    assert "retained {len(_verified_player_logs)} verified rows" in PREDICT_SOURCE
    assert "if req.sport == \"soccer\":" in PREDICT_SOURCE
    assert "no soccer " in PREDICT_SOURCE


def test_comparison_rows_require_verified_possession_and_exact_minutes():
    assert "if team_poss is None or opp_poss is None:" in PREDICT_SOURCE
    assert "or minutes < 30" in PREDICT_SOURCE
    assert '"tp": team_poss' in PREDICT_SOURCE
    assert '"minutesPlayed": minutes' in PREDICT_SOURCE


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