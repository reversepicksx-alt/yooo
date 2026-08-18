from pathlib import Path

from routes.predict import (
    _apply_optional_soccer_possession,
    _coerce_history_fixture_row,
    _filter_usable_soccer_history_logs,
    _newest_first_rows,
)


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


def test_position_cohort_attempt_is_not_dropped_by_elapsed_time_gates():
    # Comparison evidence is required context. It remains independently
    # bounded, but must not disappear just because the deterministic pass ran
    # longer than the old late-enrichment cutoff.
    assert "if _prediction_elapsed() < 17.0" not in PREDICT_SOURCE
    assert "_prediction_elapsed() < 18.0" not in PREDICT_SOURCE
    assert '"comparisonAttempted": position_comparison_meta["attempted"]' in PREDICT_SOURCE
    assert '"comparisonStatus": position_comparison_meta["status"]' in PREDICT_SOURCE
    assert '"comparisonUnavailableReason": position_comparison_meta["unavailableReason"]' in PREDICT_SOURCE


def test_history_boundaries_sort_newest_before_truncating():
    rows = [
        {"date": "2025-01-02"},
        {"date": "2025-03-20"},
        {"date": "2025-02-11"},
    ]

    assert [row["date"] for row in _newest_first_rows(rows, 2)] == [
        "2025-03-20",
        "2025-02-11",
    ]


def test_compact_recent_fixture_rows_can_hydrate_a_stale_archive():
    row = _coerce_history_fixture_row(
        {
            "fixtureId": 9001,
            "date": "2026-05-17T19:00:00+00:00",
            "venue": "away",
            "opponent": "Angers",
            "opponentId": 77,
            "homeTeamId": 77,
            "awayTeamId": 80,
            "homeGoals": 0,
            "awayGoals": 2,
            "league": "Ligue 1",
        }
    )

    assert row["fixture"]["id"] == 9001
    assert row["fixture"]["date"].startswith("2026-05-17")
    assert row["teams"]["home"]["id"] == 77
    assert row["teams"]["away"]["id"] == 80
    assert row["league"]["name"] == "Ligue 1"


def test_nested_h2h_fixture_rows_sort_newest_before_limiting():
    rows = [
        {"fixture": {"id": 1, "date": "2022-02-01T20:00:00+00:00"}},
        {"fixture": {"id": 3, "date": "2025-02-01T20:00:00+00:00"}},
        {"fixture": {"id": 2, "date": "2024-02-01T20:00:00+00:00"}},
    ]

    assert [row["fixture"]["id"] for row in _newest_first_rows(rows, 2)] == [3, 2]


def test_history_archive_and_h2h_use_complete_bounded_windows_before_slicing():
    assert "_coerce_history_fixture_row(_fixture)" in PREDICT_SOURCE
    assert "recent schedule" in PREDICT_SOURCE
    assert "player-history pool" in PREDICT_SOURCE
    assert "for h in h2h_data[:H2H_PLAYER_SCAN_LIMIT]" in PREDICT_SOURCE
    assert "for item in h2h_data[:H2H_FIXTURE_LIMIT]" in PREDICT_SOURCE
    assert "h2h_player_stats = _newest_first_rows(" in PREDICT_SOURCE
    assert "_meetings_by_venue[_venue_key] = _newest_first_rows(" in PREDICT_SOURCE


def test_broad_provider_category_can_show_similar_players_without_projection_influence():
    assert "allow_broad_category=False" in PREDICT_SOURCE
    assert "allow_broad_category=not _exact_target_for_comparison" in PREDICT_SOURCE
    assert "allow_exact_fallback=True" in PREDICT_SOURCE
    assert "positionVerified" in PREDICT_SOURCE
    assert '"positionEvidenceType": (' in PREDICT_SOURCE
    assert '"broad_category" if player_position else "unavailable"' in PREDICT_SOURCE
    assert '"projectionEligible": _exact_target_for_comparison' in PREDICT_SOURCE
    assert 'if position_comp_data and position_comp_data.get("projectionEligible")' in PREDICT_SOURCE


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


def test_possession_context_requires_verified_venue_samples_and_exposes_contract():
    assert "_POSSESSION_SAMPLE_TARGET = POSSESSION_MIN_VERIFIED_SAMPLE" in PREDICT_SOURCE
    assert "venue_filter=None if _is_neutral else player_venue" in PREDICT_SOURCE
    assert "venue_filter=None if _is_neutral else opponent_venue" in PREDICT_SOURCE
    assert '"requiredSample": required_sample' in PREDICT_SOURCE
    assert '"verified": verified' in PREDICT_SOURCE
    assert '"status": status' in PREDICT_SOURCE
    assert '"recencyWeighting"' in PREDICT_SOURCE
    assert '"possessionSampleRequired": _POSSESSION_SAMPLE_TARGET' in PREDICT_SOURCE
    assert '"moneylineWeight": match_dominance.get("moneylineWeight", 0.0)' in PREDICT_SOURCE


def test_partial_or_odds_only_possession_cannot_be_marked_verified():
    assert 'match_dominance.get("seasonAvgIsReal") is True' in PREDICT_SOURCE
    assert 'match_dominance.get("possessionVerificationStatus") == "verified"' in PREDICT_SOURCE
    assert "_team_schedule_poss_n" not in PREDICT_SOURCE
    assert 'if len(_h2h_team_poss_vals) >= 2:' not in PREDICT_SOURCE
    assert '"h2hPossRole" = "context_only"' not in PREDICT_SOURCE
    assert '"h2hPossRole"] = "context_only"' in PREDICT_SOURCE


def test_opponent_cohort_live_fill_handles_empty_fixture_cache():
    assert "opponent_recent_raw = []" in PREDICT_SOURCE
    assert "len(opponent_recent_raw) < _cohort_fixture_lookback" in PREDICT_SOURCE


def test_exact_position_cohort_searches_four_prior_seasons_when_thin():
    assert "list(range(CURRENT_SEASON - 1, CURRENT_SEASON - 5, -1))" in PREDICT_SOURCE
    assert "prior seasons before showing" in PREDICT_SOURCE


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


def test_soccer_player_history_requires_minutes_and_target_stat_but_not_tp():
    assert "async def _fetch_fixture_possession(" in PREDICT_SOURCE
    assert '"tp": gl["teamPossession"]' not in PREDICT_SOURCE
    assert "_filter_usable_soccer_history_logs" in PREDICT_SOURCE
    assert 'game_log["possessionStatus"] = "unavailable"' in PREDICT_SOURCE
    assert "_verified_player_logs" in PREDICT_SOURCE
    assert "status_code=424" in PREDICT_SOURCE
    assert '"tpHomeAvg"' in PREDICT_SOURCE
    assert '"tpAwayAvg"' in PREDICT_SOURCE
    assert '"minutesPlayed": minutes' in PREDICT_SOURCE
    assert "async def _direct_fixture_possession(" in PREDICT_SOURCE


def test_soccer_history_drops_incomplete_rows_without_poisoning_verified_history():
    assert "_verified_player_logs" in PREDICT_SOURCE
    assert "_dropped_incomplete_logs" in PREDICT_SOURCE
    assert "retained {len(_verified_player_logs)} stat-bearing rows" in PREDICT_SOURCE
    assert "if req.sport == \"soccer\":" in PREDICT_SOURCE
    assert "no soccer " in PREDICT_SOURCE


def test_history_filter_keeps_stat_bearing_appearances_without_possession():
    logs = [
        {"minutes": 90, "passes_total": 64, "teamPossession": None, "opponentPossession": None},
        {"minutes": 78, "passes_total": 58, "teamPossession": 52, "opponentPossession": 48},
        {"minutes": 90, "passes_total": None},
        {"minutes": 0, "passes_total": 70},
        {"minutes": 90, "passes_total": 60, "synthetic": True},
    ]

    retained = _filter_usable_soccer_history_logs(logs, "pass_attempts")

    assert len(retained) == 2
    assert retained[0]["passes_total"] == 64
    assert retained[0]["teamPossession"] is None


def test_cached_appearance_without_fixture_metadata_survives_history_gate():
    """A real Stage-0 cache row remains usable when its fxm companion is missing."""
    logs = [
        {
            "minutes": 90,
            "passes_total": None,
            "historySource": "fixture_player_cache",
            "fixtureContextStatus": "unavailable",
        },
    ]

    retained = _filter_usable_soccer_history_logs(logs, "pass_attempts")

    assert len(retained) == 1
    assert retained[0]["fixtureContextStatus"] == "unavailable"


def test_stage0_history_recovers_fixture_metadata_before_sorting():
    assert "team_fixture_history" in PREDICT_SOURCE
    assert "fixture metadata recovery skipped" in PREDICT_SOURCE
    assert '"metadataSource": "team_fixture_history"' in PREDICT_SOURCE
    assert "_cached_row_meta_complete" in PREDICT_SOURCE
    assert '"fixtureContextSource": "fixture_player_cache_row"' in PREDICT_SOURCE
    assert '"fixtureContextStatus": "verified"' in PREDICT_SOURCE
    assert "exact fixture metadata recovery" in PREDICT_SOURCE
    assert 'gl["date"] = str(' in PREDICT_SOURCE
    assert 'gl["fixtureContextStatus"] = "verified"' in PREDICT_SOURCE
    assert '"metadataCoverage"' in PREDICT_SOURCE
    assert '"dated": sum(1 for _log in player_game_logs if _log.get("date"))' in PREDICT_SOURCE


def test_history_and_comparable_rows_are_newest_first():
    assert 'key=lambda g: g.get("date", ""),\n                reverse=True' in PREDICT_SOURCE
    assert 'key=lambda x: str(x.get("date") or ""),\n                reverse=True' in PREDICT_SOURCE


def test_direct_fixture_possession_fallback_preserves_appearance_and_never_fabricates_tp():
    game = {"minutes": 90, "passes_total": 63, "tp": 71}

    result = _apply_optional_soccer_possession(game, "away", 71, None)

    assert result["passes_total"] == 63
    assert result["minutes"] == 90
    assert result["teamPossession"] is None
    assert result["opponentPossession"] is None
    assert result["possessionStatus"] == "unavailable"
    assert "tp" not in result


def test_optional_possession_helper_keeps_verified_fixture_orientation():
    result = _apply_optional_soccer_possession(
        {"minutes": 90, "passes_total": 63},
        "away",
        42,
        58,
    )

    assert result["teamPossession"] == 58
    assert result["opponentPossession"] == 42
    assert result["tp"] == 58
    assert result["possessionStatus"] == "verified"


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


def test_required_pick_save_reclaims_only_regenerable_cache_before_retry():
    assert "await _emergency_cache_cleanup_for_save()" in PICKS_SOURCE
    assert "await _purge_regenerable_cache_collections_for_save()" in PICKS_SOURCE
    assert "Continue through the normal post-save correlation response." in PICKS_SOURCE
    assert '"picks"' not in PICKS_SOURCE[
        PICKS_SOURCE.index("async def _purge_regenerable_cache_collections_for_save")
        : PICKS_SOURCE.index("def _has_soccer_stat_evidence")
    ]