from model_metrics import (
    build_scorecard,
    dedupe_prediction_rows,
    _event_key,
    validate_weighted_opponent_evidence,
    walk_forward_trends,
)


def _row(i, confidence=70, actual=10, projected=8, result="hit"):
    return {
        "trackingId": f"pick-{i}",
        "playerName": f"Player {i}",
        "sport": "soccer",
        "propType": "passes",
        "confidenceScore": confidence,
        "rawConfidence": confidence,
        "actualValue": actual,
        "projectedValue": projected,
        "result": result,
        "settledAt": f"2026-01-{i:02d}T12:00:00+00:00",
    }


def test_scorecard_calculates_probability_and_projection_metrics():
    rows = [
        _row(1, confidence=50, actual=10, projected=8, result="hit"),
        _row(2, confidence=75, actual=10, projected=14, result="miss"),
    ]
    scorecard = build_scorecard(rows)

    assert scorecard["n"] == 2
    assert scorecard["classification"]["finalConfidence"]["n"] == 2
    assert scorecard["classification"]["finalConfidence"]["brierScore"] == 0.4062
    assert scorecard["classification"]["finalConfidence"]["logLoss"] > 0
    assert scorecard["projection"]["overall"]["n"] == 2
    assert scorecard["projection"]["overall"]["mae"] == 3
    assert scorecard["projection"]["overall"]["rmse"] == 3.1623


def test_scorecard_dedupes_saved_copies_and_reports_calibration_gap():
    first = _row(1, confidence=70, result="hit")
    duplicate = dict(first, settledAt="2026-01-02T12:00:00+00:00", result="miss")
    second = _row(2, confidence=90, result="hit")
    scorecard = build_scorecard([first, duplicate, second])

    assert scorecard["n"] == 2
    assert scorecard["classification"]["calibration"] == [
        {"label": "70–79%", "n": 1, "predictedPct": 70.0, "observedPct": 0.0, "gapPp": -70.0},
        {"label": "90–100%", "n": 1, "predictedPct": 90.0, "observedPct": 100.0, "gapPp": 10.0},
    ]


def test_scorecard_has_chronological_holdout_and_prop_breakdown():
    rows = [_row(i, actual=10 + i, projected=10) for i in range(1, 11)]
    scorecard = build_scorecard(rows)

    assert scorecard["chronologicalHoldout"]["n"] == 2
    assert scorecard["chronologicalHoldout"]["dateRange"]["from"].startswith("2026-01-09")
    assert scorecard["projection"]["byProp"][0]["sport"] == "soccer"
    assert scorecard["projection"]["byProp"][0]["propType"] == "passes"


def test_system_dedupes_same_prediction_across_users_but_not_fixtures():
    base = _row(1, result="hit")
    base.update({
        "trackingId": "TRK-USER-A",
        "fixtureId": 1001,
        "playerId": 44,
        "teamId": 10,
        "opponentId": 20,
    })
    same_prediction_saved_by_another_user = dict(
        base,
        trackingId="TRK-USER-B",
        settledAt="2026-01-02T12:00:00+00:00",
    )
    same_market_different_fixture = dict(
        base,
        trackingId="TRK-USER-C",
        fixtureId=1002,
        settledAt="2026-01-03T12:00:00+00:00",
    )

    deduped = dedupe_prediction_rows([
        base,
        same_prediction_saved_by_another_user,
        same_market_different_fixture,
    ])

    assert len(deduped) == 2
    assert {row["fixtureId"] for row in deduped} == {1001, 1002}


def test_pass_copy_uses_avoided_direction_for_system_deduplication():
    actionable = _row(1, result="hit")
    actionable.update({
        "trackingId": "TRK-ACTIONABLE",
        "fixtureId": 1001,
        "playerId": 44,
        "teamId": 10,
        "opponentId": 20,
        "recommendation": "under",
    })
    calibration_copy = dict(
        actionable,
        trackingId="TRK-PASS",
        recommendation="pass",
        passLeaning="under",
        isCalibrationOnly=True,
        result="pass",
        settledAt="2026-01-02T12:00:00+00:00",
    )

    deduped = dedupe_prediction_rows([actionable, calibration_copy])

    assert len(deduped) == 1
    assert deduped[0]["result"] == "pass"


# ── Canonical event key tests ───────────────────────────────────────────────


def test_repeated_saves_by_same_user_collapse_to_one_event():
    """A user saving the same prediction twice must count as one event."""
    first_save = {
        "trackingId": "TRK-SAVE-1",
        "sport": "soccer",
        "fixtureId": 2001,
        "playerId": 55,
        "playerName": "Jane Doe",
        "teamId": 11,
        "opponentId": 22,
        "propType": "passes",
        "line": 45.5,
        "recommendation": "over",
        "result": "hit",
        "confidenceScore": 72,
        "settledAt": "2026-03-01T10:00:00+00:00",
    }
    second_save = dict(
        first_save,
        trackingId="TRK-SAVE-2",
        settledAt="2026-03-01T11:00:00+00:00",  # saved again one hour later
    )

    deduped = dedupe_prediction_rows([first_save, second_save])

    assert len(deduped) == 1, "Two saves of the same event must be collapsed to one unique event"
    # The newer save (second_save) wins.
    assert deduped[0]["settledAt"] == "2026-03-01T11:00:00+00:00"


def test_older_record_without_fixture_id_uses_fixture_date_bucket():
    """Records missing fixtureId but with fixtureDate deduplicate by date."""
    first = {
        "trackingId": "TRK-A",
        "sport": "soccer",
        "fixtureDate": "2025-11-15",
        "playerId": 88,
        "playerName": "Old Player",
        "teamId": 5,
        "opponentId": 6,
        "propType": "shots",
        "line": 2.5,
        "recommendation": "over",
        "result": "hit",
        "settledAt": "2025-11-15T22:00:00+00:00",
    }
    duplicate_no_fixture_id = dict(
        first,
        trackingId="TRK-B",
        settledAt="2025-11-16T08:00:00+00:00",  # a different user saving the same pick
    )
    different_match = dict(
        first,
        trackingId="TRK-C",
        fixtureDate="2025-11-22",  # genuinely different match day
        settledAt="2025-11-22T22:00:00+00:00",
    )

    deduped = dedupe_prediction_rows([first, duplicate_no_fixture_id, different_match])

    assert len(deduped) == 2, "Same-date duplicate must collapse; different match day must remain separate"
    fixture_dates = {row.get("fixtureDate") for row in deduped}
    assert fixture_dates == {"2025-11-15", "2025-11-22"}


def test_very_old_record_without_any_fixture_date_uses_timestamp_bucket():
    """Records without fixtureId or fixtureDate fall back to a 16-char timestamp prefix (YYYY-MM-DDTHH:MM)."""
    base = {
        "trackingId": "TRK-OLD-1",
        "sport": "soccer",
        "playerId": 99,
        "playerName": "Ancient Player",
        "teamId": 7,
        "opponentId": 8,
        "propType": "passes",
        "line": 30.5,
        "recommendation": "under",
        "result": "miss",
        "timestamp": "2025-06-01T14:05:00+00:00",
        "settledAt": "2025-06-01T14:05:00+00:00",
    }
    duplicate_same_minute = dict(
        base,
        trackingId="TRK-OLD-2",
        # same minute → identical 16-char prefix → same bucket
        timestamp="2025-06-01T14:05:30+00:00",
        settledAt="2025-06-01T14:05:30+00:00",
    )
    different_day = dict(
        base,
        trackingId="TRK-OLD-3",
        # different day → different bucket
        timestamp="2025-06-02T09:00:00+00:00",
        settledAt="2025-06-02T09:00:00+00:00",
    )

    deduped = dedupe_prediction_rows([base, duplicate_same_minute, different_day])

    assert len(deduped) == 2, "Same-minute duplicate must collapse; different-day record must remain separate"


def test_timestamp_bucket_different_players_same_minute_remain_distinct():
    """Two different players saved in the same minute without fixtureId or fixtureDate
    must NOT collapse — player identity (playerId/playerName) keeps them apart."""
    shared_minute = "2025-06-01T14:05:00+00:00"
    player_a = {
        "trackingId": "TRK-PA-1",
        "sport": "soccer",
        # No fixtureId, no fixtureDate — falls through to timestamp bucket
        "playerId": 101,
        "playerName": "Alpha Player",
        "teamId": 7,
        "opponentId": 8,
        "propType": "passes",
        "line": 30.5,
        "recommendation": "over",
        "result": "hit",
        "timestamp": shared_minute,
        "settledAt": shared_minute,
    }
    player_b = {
        "trackingId": "TRK-PB-1",
        "sport": "soccer",
        # Same minute, same prop/team/opponent, but a genuinely different player
        "playerId": 202,
        "playerName": "Beta Player",
        "teamId": 7,
        "opponentId": 8,
        "propType": "passes",
        "line": 30.5,
        "recommendation": "over",
        "result": "hit",
        "timestamp": shared_minute,
        "settledAt": shared_minute,
    }

    deduped = dedupe_prediction_rows([player_a, player_b])

    assert len(deduped) == 2, (
        "Two different players saved in the same minute without fixtureId/fixtureDate "
        "must remain as two distinct events — they should not collapse into one"
    )
    player_ids = {row["playerId"] for row in deduped}
    assert player_ids == {101, 202}


def test_timestamp_bucket_same_player_same_minute_collapses():
    """Two saves of the same player within the same minute without fixtureId or fixtureDate
    must collapse to one event (the later save wins)."""
    base = {
        "trackingId": "TRK-SP-1",
        "sport": "soccer",
        # No fixtureId, no fixtureDate
        "playerId": 55,
        "playerName": "Gamma Player",
        "teamId": 9,
        "opponentId": 10,
        "propType": "shots",
        "line": 2.5,
        "recommendation": "under",
        "result": "miss",
        "timestamp": "2025-07-15T09:22:00+00:00",
        "settledAt": "2025-07-15T09:22:00+00:00",
    }
    second_save_same_minute = dict(
        base,
        trackingId="TRK-SP-2",
        # Different second within the same minute → same 16-char bucket
        timestamp="2025-07-15T09:22:45+00:00",
        settledAt="2025-07-15T09:22:45+00:00",
    )

    deduped = dedupe_prediction_rows([base, second_save_same_minute])

    assert len(deduped) == 1, (
        "Two saves of the same player in the same minute without fixtureId/fixtureDate "
        "must collapse to one unique event"
    )
    # The newer save wins
    assert deduped[0]["settledAt"] == "2025-07-15T09:22:45+00:00"


def test_blank_identity_rows_same_minute_stay_distinct():
    """Two rows with no playerId and no playerName saved in the same minute must
    NOT collapse — they are genuinely different unknown players and their
    trackingIds act as the distinguishing sentinel."""
    shared_minute = "2025-06-01T14:05:00+00:00"
    unknown_a = {
        "trackingId": "TRK-UNKNOWN-A",
        "sport": "soccer",
        # Fully-blank player identity — no playerId, no playerName
        "teamId": 7,
        "opponentId": 8,
        "propType": "passes",
        "line": 30.5,
        "recommendation": "over",
        "result": "hit",
        "timestamp": shared_minute,
        "settledAt": shared_minute,
    }
    unknown_b = {
        "trackingId": "TRK-UNKNOWN-B",
        "sport": "soccer",
        # Same minute, same prop/team/opponent, also fully-blank identity
        "teamId": 7,
        "opponentId": 8,
        "propType": "passes",
        "line": 30.5,
        "recommendation": "over",
        "result": "hit",
        "timestamp": shared_minute,
        "settledAt": shared_minute,
    }

    deduped = dedupe_prediction_rows([unknown_a, unknown_b])

    assert len(deduped) == 2, (
        "Two blank-identity rows saved in the same minute must remain as two "
        "distinct events — they should not silently collapse into one"
    )
    tracking_ids = {row["trackingId"] for row in deduped}
    assert tracking_ids == {"TRK-UNKNOWN-A", "TRK-UNKNOWN-B"}


def test_blank_identity_row_saved_twice_with_same_tracking_id_collapses():
    """A blank-identity row that is genuinely saved twice (same trackingId) must
    still collapse to one event — the sentinel preserves deduplication for
    repeated saves of the exact same row."""
    base = {
        "trackingId": "TRK-SAME-SAVE",
        "sport": "soccer",
        # Fully-blank player identity
        "teamId": 7,
        "opponentId": 8,
        "propType": "passes",
        "line": 30.5,
        "recommendation": "over",
        "result": "hit",
        "timestamp": "2025-06-01T14:05:00+00:00",
        "settledAt": "2025-06-01T14:05:00+00:00",
    }
    second_save = dict(
        base,
        # Same trackingId, slightly different second within the same minute
        timestamp="2025-06-01T14:05:45+00:00",
        settledAt="2025-06-01T14:05:45+00:00",
    )

    deduped = dedupe_prediction_rows([base, second_save])

    assert len(deduped) == 1, (
        "A blank-identity row saved twice with the same trackingId must "
        "collapse to a single unique event"
    )
    # The newer save wins
    assert deduped[0]["settledAt"] == "2025-06-01T14:05:45+00:00"


def test_build_scorecard_labels_raw_unique_and_scored_counts_separately():
    """Scorecard must expose rawN (rows), n (unique events), and scoredN (HIT/MISS events)."""
    rows = [
        {**_row(1, result="hit"), "fixtureId": 100, "playerId": 1, "teamId": 1, "opponentId": 2},
        # duplicate of row 1 (same fixture/player/prop, different trackingId)
        {**_row(1, result="hit"), "fixtureId": 100, "playerId": 1, "teamId": 1, "opponentId": 2,
         "trackingId": "dup-of-1", "settledAt": "2026-01-02T12:00:00+00:00"},
        # genuine second event; push outcome → not scored for classification
        {**_row(2, result="push"), "fixtureId": 200, "playerId": 1, "teamId": 1, "opponentId": 2},
        # third event; miss → scored
        {**_row(3, result="miss"), "fixtureId": 300, "playerId": 1, "teamId": 1, "opponentId": 2},
    ]

    scorecard = build_scorecard(rows)

    assert scorecard["rawN"] == 4, "rawN must count every DB row"
    assert scorecard["n"] == 3, "n must count unique prediction events"
    assert scorecard["scoredN"] == 2, "scoredN must count only HIT or MISS events"
    assert scorecard["duplicateRowsRemoved"] == 1


def test_older_record_with_only_match_date_distinguishes_different_days():
    """Records carrying matchDate (not fixtureDate) must not collapse across different match days."""
    base = {
        "trackingId": "TRK-MD-1",
        "sport": "soccer",
        # No fixtureId, no fixtureDate — only matchDate
        "matchDate": "2025-09-10",
        "playerId": 77,
        "playerName": "Matchdate Player",
        "teamId": 3,
        "opponentId": 4,
        "propType": "shots",
        "line": 1.5,
        "recommendation": "over",
        "result": "hit",
        "settledAt": "2025-09-10T22:00:00+00:00",
    }
    duplicate_same_day = dict(
        base,
        trackingId="TRK-MD-2",
        settledAt="2025-09-11T08:00:00+00:00",  # different user, same match
    )
    different_day = dict(
        base,
        trackingId="TRK-MD-3",
        matchDate="2025-09-17",   # genuinely different match week
        settledAt="2025-09-17T22:00:00+00:00",
    )

    deduped = dedupe_prediction_rows([base, duplicate_same_day, different_day])

    assert len(deduped) == 2, (
        "Same-matchDate duplicate must collapse to one event; "
        "different matchDate must remain a separate event"
    )
    match_dates = {row.get("matchDate") for row in deduped}
    assert match_dates == {"2025-09-10", "2025-09-17"}


def test_scorecard_scored_n_excludes_calibration_only_hits():
    """scoredN must count only genuine HIT/MISS events; PASS/calibration rows are excluded."""
    genuine_hit = {
        **_row(1, result="hit"),
        "fixtureId": 500,
        "playerId": 1,
        "teamId": 1,
        "opponentId": 2,
        "recommendation": "over",
    }
    calibration_hit = dict(
        genuine_hit,
        trackingId="TRK-CALIB",
        fixtureId=501,
        recommendation="pass",
        passLeaning="over",
        isCalibrationOnly=True,
        settledAt="2026-01-02T12:00:00+00:00",
    )
    genuine_miss = {
        **_row(2, result="miss"),
        "fixtureId": 502,
        "playerId": 1,
        "teamId": 1,
        "opponentId": 2,
        "recommendation": "over",
    }

    scorecard = build_scorecard([genuine_hit, calibration_hit, genuine_miss])

    assert scorecard["n"] == 3, "All three are distinct unique events"
    assert scorecard["scoredN"] == 2, (
        "Only the two genuine HIT/MISS rows count; calibration-only row is excluded"
    )
    assert scorecard["calibrationOnlyN"] == 1


def test_event_key_is_stable_regardless_of_tracking_id():
    """_event_key must produce the same key for two saves of the same prediction."""
    base = {
        "sport": "soccer",
        "fixtureId": 9999,
        "playerId": 7,
        "playerName": "Test Player",
        "teamId": 3,
        "opponentId": 4,
        "propType": "shots",
        "line": 2.5,
        "recommendation": "over",
    }
    save_a = dict(base, trackingId="TRK-USER-A")
    save_b = dict(base, trackingId="TRK-USER-B")

    assert _event_key(save_a) == _event_key(save_b), (
        "_event_key must be identical for two saves of the same prediction; "
        "trackingId must not influence the canonical key"
    )


# ─────────────────────────────────────────────────────────────────────────────
# validate_weighted_opponent_evidence
# ─────────────────────────────────────────────────────────────────────────────

def _cohort_row(
    i,
    weighted_avg,
    unweighted_avg,
    actual,
    line=4.0,
    recommendation="over",
    result=None,
):
    """Return a minimal settled pick row with positionComparison cohort evidence."""
    if result is None:
        result = "hit" if (
            (recommendation == "over" and actual > line)
            or (recommendation == "under" and actual < line)
        ) else "miss"
    return {
        "trackingId": f"cohort-{i}",
        "playerName": f"Player {i}",
        "sport": "soccer",
        "propType": "passes",
        "line": line,
        "recommendation": recommendation,
        "actualValue": actual,
        "projectedValue": line,
        "confidenceScore": 70,
        "result": result,
        "settledAt": f"2026-0{(i % 9) + 1}-{(i % 28) + 1:02d}T12:00:00+00:00",
        "positionComparison": {
            "weightedAverage": weighted_avg,
            "unweightedAverage": unweighted_avg,
            "sampleSize": 8,
        },
    }


def test_validate_weighted_returns_caution_when_no_eligible_picks():
    # Rows without positionComparison should yield 0 eligible picks.
    rows = [_row(i) for i in range(1, 6)]
    result = validate_weighted_opponent_evidence(rows)
    assert result["eligibleSamples"] == 0
    assert result["promotionDecision"]["verdict"] == "CAUTION"
    assert result["weighted"] is None
    assert result["unweighted"] is None


def test_validate_weighted_reports_projection_error_per_method():
    # Weighted average is closer to actual than unweighted.
    rows = [
        _cohort_row(i, weighted_avg=5.0, unweighted_avg=8.0, actual=5.2)
        for i in range(1, 11)
    ]
    result = validate_weighted_opponent_evidence(rows)
    assert result["eligibleSamples"] == 10
    w_mae = result["weighted"]["projection"]["mae"]
    u_mae = result["unweighted"]["projection"]["mae"]
    assert w_mae is not None and u_mae is not None
    # weighted is much closer to actual (5.2) than unweighted (8.0)
    assert w_mae < u_mae


def test_validate_weighted_detects_churn_between_methods():
    # Weighted says OVER (avg > line), unweighted says UNDER (avg < line).
    rows = [
        _cohort_row(i, weighted_avg=5.0, unweighted_avg=3.0, actual=5.0, line=4.0)
        for i in range(1, 11)
    ]
    result = validate_weighted_opponent_evidence(rows)
    # Every row has opposite directions → 100 % churn
    assert result["churnPct"] == 100.0
    assert result["churnN"] == 10


def test_validate_weighted_reports_go_when_all_criteria_pass_and_sample_is_sufficient():
    # Need ≥ 30 eligible picks with MAE ≤ unweighted, churn < 15 %, and ≥ 5 agrees per method.
    rows = [
        _cohort_row(i, weighted_avg=5.0, unweighted_avg=5.1, actual=5.0, line=4.0)
        for i in range(1, 41)
    ]
    result = validate_weighted_opponent_evidence(rows)
    assert result["eligibleSamples"] == 40
    verdict = result["promotionDecision"]["verdict"]
    assert verdict == "GO", f"Expected GO but got {verdict}: {result['promotionDecision']['summary']}"


def test_validate_weighted_cannot_return_go_with_insufficient_eligible_picks():
    # Even if all observable criteria pass, fewer than 30 picks must force CAUTION.
    rows = [
        _cohort_row(i, weighted_avg=5.0, unweighted_avg=5.1, actual=5.0, line=4.0)
        for i in range(1, 10)
    ]
    result = validate_weighted_opponent_evidence(rows)
    assert result["eligibleSamples"] < 30
    assert result["promotionDecision"]["verdict"] == "CAUTION"
    assert "minimum" in result["promotionDecision"]["summary"].lower()


def test_validate_weighted_cannot_return_go_when_directional_accuracy_is_insufficient_data():
    # directional_accuracy requires dir_n >= 10 non-tie actuals.
    # If all actualValues equal the line (ties), dir_n == 0 → insufficient_data → CAUTION.
    rows = [
        _cohort_row(
            i,
            weighted_avg=5.0,
            unweighted_avg=5.1,
            actual=4.0,   # == line → tie, excluded from directional count
            line=4.0,
            recommendation="over",
            result="miss",   # explicit result because actual == line is ambiguous
        )
        for i in range(1, 41)
    ]
    result = validate_weighted_opponent_evidence(rows)
    assert result["eligibleSamples"] == 40
    assert result["directionalN"] == 0
    dir_criterion = next(
        c for c in result["promotionDecision"]["criteria"] if c["check"] == "directional_accuracy"
    )
    assert dir_criterion["result"] == "insufficient_data"
    assert result["promotionDecision"]["verdict"] == "CAUTION"


def test_validate_weighted_directional_accuracy_fails_when_weighted_is_much_worse():
    # Reviewer regression: weighted implies wrong direction on 5/40; unweighted correct on all.
    # weighted_avg < line (implies UNDER) but actual > line (OVER) on 5 rows.
    rows = []
    for i in range(1, 36):
        # Both methods correct: weighted and unweighted both imply OVER, actual is OVER.
        rows.append(_cohort_row(i, weighted_avg=5.0, unweighted_avg=5.0, actual=5.0, line=4.0))
    for i in range(36, 41):
        # Weighted wrong (implies UNDER), unweighted correct (implies OVER), actual is OVER.
        rows.append(_cohort_row(
            i,
            weighted_avg=3.0,   # implies UNDER
            unweighted_avg=5.0, # implies OVER
            actual=5.0,         # actual OVER
            line=4.0,
            recommendation="over",
            result="hit",
        ))
    result = validate_weighted_opponent_evidence(rows)
    assert result["eligibleSamples"] == 40
    w_hr = result["weighted"]["directionalHitRate"]
    u_hr = result["unweighted"]["directionalHitRate"]
    assert w_hr is not None and u_hr is not None
    assert u_hr > w_hr, "unweighted should have higher directional hit rate"
    dir_criterion = next(
        c for c in result["promotionDecision"]["criteria"] if c["check"] == "directional_accuracy"
    )
    # 5 rows where weighted is wrong vs 0 for unweighted → gap > 2 pp → fail
    assert dir_criterion["result"] == "fail"
    assert result["promotionDecision"]["verdict"] in {"NO_GO", "CAUTION"}


def test_validate_weighted_reports_nogo_when_projection_is_worse():
    # Weighted average is far from actual; unweighted is much closer.
    rows = [
        _cohort_row(i, weighted_avg=10.0, unweighted_avg=5.0, actual=5.2, line=4.5)
        for i in range(1, 21)
    ]
    result = validate_weighted_opponent_evidence(rows)
    w_mae = result["weighted"]["projection"]["mae"]
    u_mae = result["unweighted"]["projection"]["mae"]
    assert w_mae > u_mae, "weighted should be worse when it is far from actual"
    # The projection_error criterion should fail
    proj_criterion = next(
        c for c in result["promotionDecision"]["criteria"] if c["check"] == "projection_error"
    )
    assert proj_criterion["result"] == "fail"


def test_validate_weighted_leakage_policy_is_documented_in_output():
    rows = [
        _cohort_row(i, weighted_avg=5.0, unweighted_avg=5.0, actual=5.0)
        for i in range(1, 4)
    ]
    result = validate_weighted_opponent_evidence(rows)
    assert "leakagePolicy" in result
    assert "stored at prediction time" in result["leakagePolicy"].lower()


def test_validate_weighted_promotion_env_var_documented_in_decision():
    rows = [
        _cohort_row(i, weighted_avg=5.0, unweighted_avg=5.2, actual=5.0)
        for i in range(1, 6)
    ]
    result = validate_weighted_opponent_evidence(rows)
    decision = result["promotionDecision"]
    assert decision.get("promotionEnvVar") == "WEIGHTED_OPPONENT_EVIDENCE_MODE"
    assert "shadow" in (decision.get("promotionValues") or {})
    assert "live" in (decision.get("promotionValues") or {})


# ─────────────────────────────────────────────────────────────────────────────
# walk_forward_trends tests
# ─────────────────────────────────────────────────────────────────────────────

from datetime import datetime, timezone, timedelta


def _trends_row(idx, sport="soccer", result="hit", confidence=65, days_ago=60):
    """Build a settled row settled `days_ago` days in the past."""
    settled = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    return {
        "trackingId": f"trk-{sport}-{idx}",
        "playerName": f"Player {idx}",
        "sport": sport,
        "propType": "passes",
        "line": 10.5,
        "recommendation": "OVER",
        "fixtureId": 9000 + idx,
        "playerId": idx,
        "teamId": 1,
        "opponentId": 2,
        "confidenceScore": confidence,
        "rawConfidence": confidence,
        "actualValue": 12,
        "projectedValue": 10,
        "result": result,
        "status": "settled",
        "settledAt": settled,
    }


def test_walk_forward_trends_returns_all_three_period_keys():
    rows = [_trends_row(i, days_ago=60 - i) for i in range(1, 20)]
    trends = walk_forward_trends(rows)
    assert "periods" in trends
    assert "all" in trends["periods"]
    assert "30d" in trends["periods"]
    assert "7d" in trends["periods"]
    assert "n" in trends
    assert trends["n"]["all"] >= trends["n"]["30d"] >= trends["n"]["7d"]


def test_walk_forward_trends_per_sport_brier_score():
    """Brier score is computed independently per sport."""
    soccer_rows = [_trends_row(i, sport="soccer", result="hit" if i % 2 == 0 else "miss", days_ago=10) for i in range(1, 8)]
    mlb_rows = [_trends_row(i + 20, sport="mlb", result="hit", days_ago=10) for i in range(1, 5)]
    trends = walk_forward_trends(soccer_rows + mlb_rows)
    all_period = trends["periods"]["all"]
    sports_found = {r["sport"] for r in all_period}
    assert "soccer" in sports_found
    assert "mlb" in sports_found
    for entry in all_period:
        assert entry["brierScore"] is not None or entry["n"] == 0
        assert entry["n"] > 0


def test_walk_forward_trends_deduplicates_before_slicing():
    """Duplicate saves of the same event must not double-count in any period."""
    base = _trends_row(1, days_ago=5)
    duplicate = dict(base, trackingId="trk-soccer-1b", settledAt=(datetime.now(timezone.utc) - timedelta(days=5, hours=1)).isoformat())
    unique = _trends_row(2, days_ago=4)
    trends = walk_forward_trends([base, duplicate, unique])
    # 2 unique events within 7d → 7d slice should have exactly 2
    assert trends["n"]["7d"] == 2
    assert trends["n"]["all"] == 2


def test_walk_forward_trends_periods_filter_correctly_by_date():
    """Rows older than 30d must not appear in 30d or 7d slices."""
    old_rows = [_trends_row(i, days_ago=60) for i in range(1, 6)]
    recent_30d = [_trends_row(i + 10, days_ago=20) for i in range(1, 4)]
    recent_7d = [_trends_row(i + 20, days_ago=3) for i in range(1, 3)]
    trends = walk_forward_trends(old_rows + recent_30d + recent_7d)
    assert trends["n"]["all"] == 10   # 5 + 3 + 2 unique events (no dupes)
    assert trends["n"]["30d"] == len(recent_30d) + len(recent_7d)
    assert trends["n"]["7d"] == len(recent_7d)


def test_walk_forward_trends_independent_of_pre_filter():
    """Trends computed from the full corpus must differ from trends on a pre-filtered subset.

    This verifies that the caller should always pass the unfiltered corpus to
    walk_forward_trends, not the period-display-filtered rows, so that the all/
    30d/7d internal slices are accurate.
    """
    old_rows = [_trends_row(i, days_ago=60) for i in range(1, 6)]
    recent_rows = [_trends_row(i + 10, days_ago=3) for i in range(1, 4)]
    all_rows = old_rows + recent_rows

    trends_from_full = walk_forward_trends(all_rows)
    trends_from_filtered = walk_forward_trends(recent_rows)  # simulates a wrong pre-filter

    # Full corpus "all" slice includes old rows; pre-filtered corpus does not.
    assert trends_from_full["n"]["all"] == len(all_rows)
    assert trends_from_filtered["n"]["all"] == len(recent_rows)
    # They should diverge
    assert trends_from_full["n"]["all"] != trends_from_filtered["n"]["all"]


def test_walk_forward_trends_handles_utc_offset_dates():
    """Date filtering must work on rows with explicit UTC offsets (+00:00 vs Z)."""
    recent_utc = dict(_trends_row(1, days_ago=3))
    recent_utc["settledAt"] = (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    old_z = dict(_trends_row(2, days_ago=40))
    old_z["settledAt"] = (datetime.now(timezone.utc) - timedelta(days=40)).strftime("%Y-%m-%dT%H:%M:%SZ")
    trends = walk_forward_trends([recent_utc, old_z])
    assert trends["n"]["all"] == 2
    assert trends["n"]["7d"] == 1   # only the 3-day-old row
    assert trends["n"]["30d"] == 1  # only the 3-day-old row


def test_walk_forward_trends_excludes_calibration_only_rows_from_brier():
    """Rows with isCalibrationOnly=True must not contribute to the Brier/log-loss computation.

    This mirrors the requirement that the all_sports_raw Mongo projection must include
    isCalibrationOnly so that PASS rows whose recommendation field is not literally 'pass'
    are still correctly filtered out of probability metrics.
    """
    wager_hit = dict(_trends_row(1, sport="soccer", result="hit", days_ago=5))
    wager_miss = dict(_trends_row(2, sport="soccer", result="miss", days_ago=4))
    cal_only = dict(_trends_row(3, sport="soccer", result="hit", days_ago=3))
    cal_only["isCalibrationOnly"] = True  # must be excluded from Brier
    cal_only["recommendation"] = "OVER"   # not literally 'pass', so field check matters

    trends = walk_forward_trends([wager_hit, wager_miss, cal_only])
    all_period = trends["periods"]["all"]
    soccer_entry = next((r for r in all_period if r["sport"] == "soccer"), None)
    assert soccer_entry is not None
    # Only 2 scored events (the calibration-only row must be excluded)
    assert soccer_entry["n"] == 2
    # Brier must not be None — we have 2 scored events with confidenceScore=65
    assert soccer_entry["brierScore"] is not None


def test_walk_forward_trends_mae_is_populated_when_actual_and_projected_present():
    """per-sport MAE must be non-null when actualValue and projectedValue are provided.

    This verifies the all_sports_raw query includes actualValue/projectedValue so that
    the per-sport projection metrics are computed rather than silently set to null.
    """
    rows = []
    for i in range(1, 8):
        r = dict(_trends_row(i, sport="mlb", result="hit" if i % 2 else "miss", days_ago=10))
        r["propType"] = "pitcher_strikeouts"
        r["actualValue"] = 5.0 + i
        r["projectedValue"] = 5.0
        rows.append(r)

    trends = walk_forward_trends(rows)
    all_period = trends["periods"]["all"]
    mlb_entry = next((r for r in all_period if r["sport"] == "mlb"), None)
    assert mlb_entry is not None
    # MAE must be a positive finite number when both values are present
    assert mlb_entry["mae"] is not None
    assert mlb_entry["mae"] > 0


def test_walk_forward_trends_returns_eight_chronological_weekly_brier_buckets():
    rows = [
        _trends_row(1, sport="soccer", result="hit", confidence=70, days_ago=3),
        _trends_row(2, sport="soccer", result="miss", confidence=70, days_ago=10),
        _trends_row(3, sport="mlb", result="hit", confidence=60, days_ago=17),
        _trends_row(4, sport="soccer", result="hit", confidence=70, days_ago=60),
    ]

    trends = walk_forward_trends(rows)
    buckets = trends["weeklyBuckets"]

    assert len(buckets) == 8
    assert [bucket["weekStart"] for bucket in buckets] == sorted(
        bucket["weekStart"] for bucket in buckets
    )
    assert all(bucket["weekEnd"] > bucket["weekStart"] for bucket in buckets)
    assert sum(bucket["n"] for bucket in buckets) == 3

    soccer_points = [
        row
        for bucket in buckets
        for row in bucket["bySport"]
        if row["sport"] == "soccer"
    ]
    mlb_points = [
        row
        for bucket in buckets
        for row in bucket["bySport"]
        if row["sport"] == "mlb"
    ]
    assert len(soccer_points) == 2
    assert len(mlb_points) == 1
    assert all(point["n"] == 1 for point in soccer_points + mlb_points)
    assert soccer_points[0]["brierScore"] == round((0.7 - 0) ** 2, 4)
    assert soccer_points[1]["brierScore"] == round((0.7 - 1) ** 2, 4)


def test_walk_forward_trends_weekly_brier_buckets_deduplicate_saves():
    base = _trends_row(1, sport="soccer", result="hit", confidence=65, days_ago=3)
    duplicate = dict(
        base,
        trackingId="trk-soccer-1b",
        settledAt=(datetime.now(timezone.utc) - timedelta(days=3, hours=1)).isoformat(),
    )

    trends = walk_forward_trends([base, duplicate])
    bucket_rows = [
        row
        for bucket in trends["weeklyBuckets"]
        for row in bucket["bySport"]
        if row["sport"] == "soccer"
    ]

    assert len(bucket_rows) == 1
    assert bucket_rows[0]["n"] == 1