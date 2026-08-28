from causal_script_engine import (
    build_causal_script_packet,
    distortion_tags,
    finalize_opponent_cohort,
    replay_reference_misses,
)


def test_pass_chain_and_distortion_tags_are_explicit():
    packet = build_causal_script_packet({
        "sport": "soccer",
        "playerName": "Keeper",
        "propType": "pass_attempts",
        "playerPosition": "GK",
        "recommendation": "under",
        "projection": 23.5,
        "line": 24.5,
        "gameLogs": [{"value": 18, "venue": "away", "minutes": 90, "redCard": True}],
    })
    assert "pressure/progression geometry" in packet["statProductionChain"]
    assert packet["history"]["distortionCounts"]["red_card"] == 1
    assert packet["provenance"]["pregameOnly"] is True
    assert packet["recommendationGate"]["productionInfluence"] == "active_invalid_or_contradiction_guard"


def test_clean_exact_role_uplift_rejects_conflicting_under():
    rows = [
        {"role": "GK", "position": "GK", "venue": "away", "value": 35,
         "normalMatchingVenue": 20, "minutes": 90}
        for _ in range(3)
    ]
    packet = build_causal_script_packet({
        "sport": "soccer",
        "playerName": "Petrovic",
        "propType": "pass_attempts",
        "playerPosition": "GK",
        "recommendation": "under",
        "projection": 23.5,
        "line": 24.5,
        "opponentName": "Manchester City",
        "venue": "away",
        "positionComparison": {"players": rows},
        "tacticalContext": {"expectedPossession": 42, "opponentExpectedPossession": 58},
    })
    assert packet["opponentRoleCohort"]["opponentRoleEffect"] == 1.75
    assert packet["recommendationGate"]["decision"] == "REJECT"
    assert packet["modelDirection"] == "UNDER"
    assert packet["causalDirection"] == "MORE"
    assert packet["causalVerdict"] == "CAUSAL CONTRADICTION"
    assert packet["modelProjection"] == 23.5


def test_thin_edge_without_exact_role_does_not_override_deterministic_model():
    packet = build_causal_script_packet({
        "sport": "soccer",
        "playerName": "Moncayola",
        "propType": "passes",
        "playerPosition": "CM",
        "recommendation": "over",
        "projection": 40,
        "line": 39.5,
    })
    assert packet["recommendationGate"]["decision"] == "NO_OVERRIDE"
    assert "CAUSAL EVIDENCE INCOMPLETE" in packet["recommendationGate"]["reason"]
    assert "deterministic recommendation retained" in packet["recommendationGate"]["reason"]
    assert packet["causalVerdict"] == "EVIDENCE INCOMPLETE"


def test_reference_miss_replay_is_pregame_only_and_conservative():
    replay = replay_reference_misses()
    assert [item["decision"] for item in replay] == ["REJECT", "REJECT", "NO_OVERRIDE"]
    assert all(item["pregameOnly"] and not item["resultDataUsed"] for item in replay)


def test_three_samples_are_provisional_without_aligned_corroboration():
    rows = [
        {"role": "GK", "position": "GK", "venue": "away", "value": 35,
         "normalMatchingVenue": 20, "minutes": 90}
        for _ in range(3)
    ]
    packet = build_causal_script_packet({
        "sport": "soccer", "playerName": "Petrovic",
        "propType": "pass_attempts", "playerPosition": "GK",
        "recommendation": "under", "projection": 23.5, "line": 24.5,
        "venue": "away", "positionComparison": {"players": rows},
        "tacticalContext": {"expectedPossession": 50, "opponentExpectedPossession": 50},
    })
    assert packet["causalVerdict"] == "CAUSAL CONTRADICTION"
    assert packet["corroboration"]["cleanExactRoleSamples"] == 3
    assert packet["corroboration"]["productionFlipEligible"] is False
    assert packet["corroboration"]["sampleStrength"] == "provisional"
    assert packet["corroboration"]["strongConfidenceAllowed"] is False


def test_three_samples_plus_aligned_regime_can_be_provisional_flip():
    rows = [
        {"role": "GK", "position": "GK", "venue": "home", "value": 35,
         "normalMatchingVenue": 20, "minutes": 90}
        for _ in range(3)
    ]
    packet = build_causal_script_packet({
        "sport": "soccer", "playerName": "Keeper",
        "propType": "pass_attempts", "playerPosition": "GK",
        "recommendation": "over", "projection": 35, "line": 24.5,
        "venue": "home", "positionComparison": {"players": rows},
        "tacticalContext": {"expectedPossession": 60, "opponentExpectedPossession": 40},
    })
    assert packet["recommendationGate"]["decision"] == "CONFIRM"
    assert packet["corroboration"]["alignedEvidence"] == ["current_regime"]
    assert packet["corroboration"]["productionFlipEligible"] is True
    assert packet["corroboration"]["sampleStrength"] == "provisional"
    assert packet["corroboration"]["strongConfidenceAllowed"] is False


def test_five_clean_exact_role_samples_allow_strong_sample_status():
    rows = [
        {"role": "GK", "position": "GK", "venue": "home", "value": 35,
         "normalMatchingVenue": 20, "minutes": 90}
        for _ in range(5)
    ]
    packet = build_causal_script_packet({
        "sport": "soccer", "playerName": "Keeper",
        "propType": "pass_attempts", "playerPosition": "GK",
        "recommendation": "over", "projection": 35, "line": 24.5,
        "venue": "home", "positionComparison": {"players": rows},
        "tacticalContext": {"expectedPossession": 60, "opponentExpectedPossession": 40},
    })
    assert packet["corroboration"]["productionFlipEligible"] is True
    assert packet["corroboration"]["sampleStrength"] == "strong"
    assert packet["corroboration"]["strongConfidenceAllowed"] is True


def test_goalie_saves_uses_api_football_goal_saves_values():
    rows = [
        {
            "role": "GK", "position": "GK", "venue": "home",
            "goals_saves": 4, "normalMatchingVenue": 2, "minutes": 90,
        }
        for _ in range(3)
    ]
    packet = build_causal_script_packet({
        "sport": "soccer", "playerName": "Mailson",
        "propType": "goalie_saves", "playerPosition": "GK",
        "recommendation": "over", "projection": 4.0, "line": 2.5,
        "venue": "home", "positionComparison": {"players": rows},
        "tacticalContext": {"expectedPossession": 40, "opponentExpectedPossession": 60},
    })
    cohort = packet["opponentRoleCohort"]
    assert cohort["sampleSize"] == 3
    assert cohort["workloadAverage"] == 4.0
    assert cohort["opponentRoleEffect"] == 2.0
    assert packet["causalVerdict"] == "CAUSAL CONFIRM"


def test_shot_stopper_role_is_admitted_to_goalkeeper_cohort():
    rows = [
        {
            "role": "Shot-Stopper", "position": "GK", "venue": "home",
            "goals_saves": 4, "normalMatchingVenue": 2, "minutes": 90,
        }
        for _ in range(3)
    ]
    packet = build_causal_script_packet({
        "sport": "soccer", "playerName": "Mailson",
        "propType": "goalie_saves", "playerPosition": "GK",
        "recommendation": "over", "projection": 4.0, "line": 2.5,
        "venue": "home", "positionComparison": {"players": rows},
        "tacticalContext": {"expectedPossession": 40, "opponentExpectedPossession": 60},
    })
    assert packet["opponentRoleCohort"]["sampleSize"] == 3
    assert packet["opponentRoleCohort"]["roleBucket"] == "GK"
    assert packet["causalVerdict"] == "CAUSAL CONFIRM"


def test_incomplete_goalkeeper_saves_does_not_invent_causal_direction():
    rows = [
        {"role": "Shot-Stopper", "position": "GK", "venue": "home",
         "goals_saves": 3, "minutes": 90}
        for _ in range(3)
    ]
    packet = build_causal_script_packet({
        "sport": "soccer", "playerName": "Mailson",
        "propType": "goalie_saves", "playerPosition": "GK",
        "recommendation": "over", "projection": 4.0, "line": 2.5,
        "venue": "home", "positionComparison": {"players": rows},
        "tacticalContext": {"expectedPossession": 40, "opponentExpectedPossession": 60},
    })
    assert packet["opponentRoleCohort"]["workloadAverage"] == 3.0
    assert packet["causalDirection"] == "EVIDENCE INCOMPLETE"
    assert packet["causalVerdict"] == "EVIDENCE INCOMPLETE"


def test_mailson_cohort_is_pure_and_reproducible_from_same_snapshot():
    source_rows = [
        {
            "fixtureId": 1000 + index,
            "playerId": 2000 + index,
            "teamId": 3000 + index,
            "opponentId": 2944,
            "venue": "home",
            "role": "Shot-Stopper",
            "position": "GK",
            "minutes": 90,
            "statValue": value,
            "normalMatchingVenue": baseline,
            "sourcePath": "positionComparison",
        }
        for index, (value, baseline) in enumerate(
            zip(
                [20, 28, 35, 36, 26, 25, 30, 22, 28, 26, 16, 21, 26, 35, 27],
                [26.7, 27.6, 25.4, None, 28.0, 26.8, None, None, 26.4, 28.4, None, 21.0, None, 28.3, 24.1],
            )
        )
    ]
    first = finalize_opponent_cohort(
        source_rows,
        prop="pass_attempts",
        venue="home",
        target_player_id=9557,
        target_team_id=2936,
        target_fixture_id=1602993,
        target_opponent_id=2944,
        target_bucket="GK",
        strict_identity=True,
    )
    second = finalize_opponent_cohort(
        source_rows,
        prop="pass_attempts",
        venue="home",
        target_player_id=9557,
        target_team_id=2936,
        target_fixture_id=1602993,
        target_opponent_id=2944,
        target_bucket="GK",
        strict_identity=True,
    )
    admitted_a, rejected_a, validation_a = first
    admitted_b, rejected_b, validation_b = second
    assert validation_a["valid"] is True
    assert validation_b["valid"] is True
    assert len(admitted_a) == len(admitted_b) == 15
    assert [(r["fixtureId"], r["playerId"]) for r in admitted_a] == [
        (r["fixtureId"], r["playerId"]) for r in admitted_b
    ]
    assert rejected_a == rejected_b == []
    values = [row["statValue"] for row in admitted_a]
    baselines = [row["normalMatchingVenue"] for row in admitted_a if row["normalMatchingVenue"] is not None]
    workload = sum(values) / len(values)
    baseline = sum(baselines) / len(baselines)
    assert round(workload, 2) == 26.73
    assert round(baseline, 2) == 26.27
    assert round(workload / baseline, 3) == 1.018


def test_persisted_cohort_snapshot_is_the_only_calculation_input_on_replay():
    rows = [
        {
            "fixtureId": 7000 + index,
            "playerId": 8000 + index,
            "teamId": 9000 + index,
            "opponentId": 2944,
            "venue": "home",
            "position": "GK",
            "role": "Shot-Stopper",
            "minutes": 90,
            "value": 30 + index,
            "normalMatchingVenue": 25,
        }
        for index in range(5)
    ]
    snapshot = {
        "status": "available",
        "snapshotKey": "mailson-repeatable",
        "snapshotVersion": "causal-cohort.v2",
        "admittedRows": rows,
        "rejectedRows": [],
        "validation": {"valid": True},
    }
    base = {
        "playerId": 9557,
        "fixtureId": 1602993,
        "playerName": "Mailson",
        "propType": "pass_attempts",
        "playerPosition": "GK",
        "projection": 23,
        "line": 27.5,
        "recommendation": "under",
    }
    packet_a = build_causal_script_packet(
        {**base, "positionComparison": {"players": [{"value": 999, "venue": "away"}]}},
        evidence={"cohortSnapshot": snapshot, "targetHistory": []},
    )
    packet_b = build_causal_script_packet(
        {**base, "positionComparison": {"players": []}},
        evidence={"cohortSnapshot": snapshot, "targetHistory": []},
    )
    for packet in (packet_a, packet_b):
        cohort = packet["opponentRoleCohort"]
        assert cohort["sampleSize"] == 5
        assert cohort["workloadAverage"] == 32.0
        assert cohort["normalMatchingVenueAverage"] == 25.0
        assert cohort["opponentRoleEffect"] == 1.28
        assert packet["corroboration"]["sampleStrength"] == "strong"
    assert [
        (row["fixtureId"], row["playerId"]) for row in packet_a["evidence"]["cohortSnapshot"]["admittedRows"]
    ] == [
        (row["fixtureId"], row["playerId"]) for row in packet_b["evidence"]["cohortSnapshot"]["admittedRows"]
    ]


def test_opponent_cohort_persists_rejections_for_target_identity_and_wrong_venue():
    rows = [
        {
            "fixtureId": 1602993,
            "playerId": 9557,
            "teamId": 2936,
            "opponentId": 2944,
            "venue": "home",
            "position": "GK",
            "minutes": 90,
            "value": 27,
        },
        {
            "fixtureId": 9002,
            "playerId": 9900,
            "teamId": 3000,
            "opponentId": 2944,
            "venue": "away",
            "position": "GK",
            "minutes": 90,
            "value": 30,
        },
    ]
    admitted, rejected, validation = finalize_opponent_cohort(
        rows,
        prop="pass_attempts",
        venue="home",
        target_player_id=9557,
        target_team_id=2936,
        target_fixture_id=1602993,
        target_opponent_id=2944,
        target_bucket="GK",
        strict_identity=True,
    )
    assert admitted == []
    assert validation["valid"] is True
    assert [row["rejectionReason"] for row in rejected] == [
        "target_player_id",
        "venue_mismatch",
    ]
    assert all(row["admissionStatus"] == "rejected" for row in rejected)


def test_invalid_snapshot_with_purity_failure_returns_evidence_invalid_pass():
    packet = build_causal_script_packet(
        {
            "playerId": 9557,
            "fixtureId": 1602993,
            "playerName": "Mailson",
            "propType": "pass_attempts",
            "playerPosition": "GK",
            "projection": 23,
            "line": 27.5,
            "recommendation": "under",
        },
        evidence={
            "cohortSnapshot": {
                "status": "invalid",
                "snapshotKey": "mailson-invalid",
                "validation": {"valid": False, "purityFailures": ["target_team_id_in_admitted_cohort"]},
                "admittedRows": [],
            },
            "targetHistory": [],
        },
    )
    assert packet["causalDirection"] == "EVIDENCE INVALID"
    assert packet["causalVerdict"] == "EVIDENCE INVALID"
    assert packet["recommendationGate"]["decision"] == "PASS"