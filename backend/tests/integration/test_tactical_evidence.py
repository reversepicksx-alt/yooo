from tactical_evidence import (
    resolve_observed_role,
    summarize_observed_positions,
    summarize_player_opponent_history,
    summarize_position_cohort,
)


def test_wide_creator_is_not_classified_as_pressing_forward():
    result = resolve_observed_role(
        "RW",
        {
            "appearances": 20,
            "dribbles_attempts": 60,
            "key_passes": 50,
            "shots_total": 35,
        },
    )
    assert result["position"] == "RW"
    assert result["role"] == "Inverted Winger"
    assert "pressing" not in " ".join(result["evidence"]).lower()


def test_generic_forward_creator_is_not_classified_as_pressing_forward():
    result = resolve_observed_role(
        "F",
        {
            "appearances": 10,
            "passes_total": 430,
            "key_passes": 28,
            "dribbles_attempts": 24,
            "shots_total": 18,
        },
    )
    assert result["role"] == "False 9"
    assert result["confidence"] == "high"
    assert "creator-over-finisher fingerprint" in result["evidence"]


def test_generic_forward_link_play_profile_is_creative_forward():
    result = resolve_observed_role(
        "F",
        {
            "appearances": 24,
            "passes_total": 1071,
            "key_passes": 77,
            "dribbles_attempts": 118,
            "shots_total": 92,
        },
    )
    assert result["role"] == "Creative Forward"
    assert "creative link-play and carry fingerprint" in result["evidence"]


def test_player_opponent_history_reports_hit_rate_from_valid_values():
    result = summarize_player_opponent_history([4, 7, 2, None], 3.5)
    assert result == {
        "sampleSize": 3,
        "average": 4.33,
        "overHits": 2,
        "underHits": 1,
        "overHitRate": 67,
        "underHitRate": 33,
        "evidenceStatus": "thin",
    }


def test_position_cohort_does_not_pad_thin_sample():
    result = summarize_position_cohort(
        [
            {"playerId": 1, "position": "RW", "minutes": 90, "statValue": 5},
            {"playerId": 2, "position": "RW", "minutes": 0, "statValue": 10},
            {"playerId": 3, "position": "RW", "minutes": 90, "statValue": None},
            {"playerId": 4, "position": "RW", "minutes": 90, "statValue": 3},
        ],
        4.5,
    )
    assert result["sampleSize"] == 2
    assert result["sampleStatus"] == "limited"
    assert result["average"] == 4
    assert result["overHitRate"] == 50


def test_observed_position_summary_exposes_sample_and_dominant_position():
    result = summarize_observed_positions(
        [{"position": "RW"}, {"position": "RW"}, {"position": "ST"}, {"position": None}]
    )
    assert result["sampleSize"] == 3
    assert result["dominantPosition"] == "RW"
    assert result["positionCounts"] == {"RW": 2, "ST": 1}