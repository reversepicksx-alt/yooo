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
    assert "MODEL EDGE REJECTED" in packet["recommendationGate"]["reason"]


def test_reference_miss_replay_is_pregame_only_and_conservative():
    replay = replay_reference_misses()
    assert [item["decision"] for item in replay] == ["REJECT", "REJECT", "PASS"]
    assert all(item["pregameOnly"] and not item["resultDataUsed"] for item in replay)