from tactical_evidence import (
    infer_grid_position,
    resolve_observed_role,
    summarize_observed_positions,
    summarize_player_opponent_history,
    summarize_position_cohort,
    build_position_cohort_statement,
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


def test_generic_defender_does_not_invent_fullback_or_center_back_role():
    result = resolve_observed_role(
        "D",
        {
            "appearances": 12,
            "passes_total": 900,
            "tackles_total": 45,
            "dribbles_attempts": 14,
            "shots_total": 4,
        },
    )
    assert result["position"] == "DEF"
    assert result["role"] is None
    assert result["confidence"] == "low"
    assert "exact CB/LB/RB role not independently verified" in result["evidence"]


def test_verified_center_back_observation_cannot_become_fullback():
    result = resolve_observed_role(
        "CB",
        {
            "appearances": 12,
            "passes_total": 900,
            "tackles_total": 45,
            "dribbles_attempts": 14,
            "shots_total": 4,
        },
    )
    assert result["position"] == "CB"
    assert result["role"] == "Ball-Playing CB"
    assert result["role"] != "Fullback"


def test_lineup_grid_resolves_four_defender_back_line():
    assert infer_grid_position("2:1", "4-3-3", "D") == "LB"
    assert infer_grid_position("2:2", "4-3-3", "D") == "CB"
    assert infer_grid_position("2:3", "4-3-3", "D") == "CB"
    assert infer_grid_position("2:4", "4-3-3", "D") == "RB"


def test_lineup_grid_stays_conservative_when_shape_is_ambiguous():
    assert infer_grid_position("3:2", "4-3-3", "D") == "DEF"
    assert infer_grid_position(None, "4-3-3", "D") == "DEF"


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


def test_position_cohort_weights_evidence_without_changing_distinct_sample_count():
    result = summarize_position_cohort(
        [
            {"playerId": 1, "minutes": 90, "statValue": 40, "evidenceWeight": 1.5},
            {"playerId": 2, "minutes": 30, "statValue": 20, "evidenceWeight": 0.5},
        ],
        30,
    )
    assert result["sampleSize"] == 2
    assert result["average"] == 35
    assert result["unweightedAverage"] == 30
    assert result["weightMethod"] == "minutes_and_repeat_appearance_evidence_only"


def test_observed_position_summary_exposes_sample_and_dominant_position():
    result = summarize_observed_positions(
        [{"position": "RW"}, {"position": "RW"}, {"position": "ST"}, {"position": None}]
    )
    assert result["sampleSize"] == 3
    assert result["dominantPosition"] == "RW"
    assert result["positionCounts"] == {"RW": 2, "ST": 1}


def test_position_cohort_statement_uses_player_event_language_for_all_prop_families():
    cases = [
        ("saves", "GK", "goalkeepers averaged 3.3 saves"),
        ("pass_attempts", "CB", "centre-backs averaged 42.0 pass attempts"),
        ("shots_on_target", "ST", "strikers averaged 1.5 shots on target"),
        ("tackles", "LB", "left-backs averaged 4.0 tackles"),
        ("clearances", "DEF", "defenders averaged 6.0 clearances"),
    ]
    for prop_type, position, expected in cases:
        statement = build_position_cohort_statement(
            opponent="Vasco DA Gama",
            prop_type=prop_type,
            position=position,
            average={"saves": 3.3, "pass_attempts": 42, "shots_on_target": 1.5,
                     "tackles": 4, "clearances": 6}[prop_type],
            sample_size=8,
            venue="home",
        )
        assert expected in statement
        assert "allows" not in statement
        assert "in matching home fixtures" in statement