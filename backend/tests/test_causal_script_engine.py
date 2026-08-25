from causal_script_engine import (
    build_causal_script_packet,
    distortion_tags,
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
    assert packet["recommendationGate"]["productionInfluence"] == "active_pass_guard"


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


def test_thin_edge_without_exact_role_is_pass():
    packet = build_causal_script_packet({
        "sport": "soccer",
        "playerName": "Moncayola",
        "propType": "passes",
        "playerPosition": "CM",
        "recommendation": "over",
        "projection": 40,
        "line": 39.5,
    })
    assert packet["recommendationGate"]["decision"] == "PASS"
    assert "CAUSAL EVIDENCE INCOMPLETE" in packet["recommendationGate"]["reason"]
    assert packet["causalVerdict"] == "EVIDENCE INCOMPLETE"


def test_reference_miss_replay_is_pregame_only_and_conservative():
    replay = replay_reference_misses()
    assert [item["decision"] for item in replay] == ["REJECT", "REJECT", "PASS"]
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