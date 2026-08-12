from tactical_evidence import (
    infer_grid_position,
    resolve_observed_role,
    summarize_observed_positions,
    summarize_player_opponent_history,
    summarize_position_cohort,
    build_position_cohort_statement,
    position_cohort_verdict,
)
from model_metrics import validate_weighted_opponent_evidence


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


def test_lineup_grid_resolves_unambiguous_midfield_bands():
    assert infer_grid_position("3:2", "4-3-3", "M") == "CM"
    assert infer_grid_position("3:1", "4-2-3-1", "M") == "CDM"
    assert infer_grid_position("4:1", "4-2-3-1", "M") == "LW"
    assert infer_grid_position("4:2", "4-2-3-1", "M") == "CAM"
    assert infer_grid_position("4:3", "4-2-3-1", "M") == "RW"
    assert infer_grid_position("3:1", "4-1-4-1", "M") == "CDM"
    assert infer_grid_position("4:3", "4-1-4-1", "M") == "CM"
    assert infer_grid_position("3:1", "3-1-4-2", "M") == "CDM"
    assert infer_grid_position("4:1", "3-1-4-2", "M") == "LM"
    assert infer_grid_position("4:2", "3-1-4-2", "M") == "CM"
    assert infer_grid_position("4:3", "3-1-4-2", "M") == "CM"
    assert infer_grid_position("4:4", "3-1-4-2", "M") == "RM"


def test_lineup_grid_resolves_4231_attacking_row_for_forward_category():
    """4-2-3-1 row-four players reported as F/FWD must map to LW/CAM/RW.

    This is the exact formation/category combination that produced the
    overly-central CAM label for wide attackers such as Doku (Man City, col 1)
    before the attacking-grid column mapping was corrected.
    """
    # Column 1 = left winger
    assert infer_grid_position("4:1", "4-2-3-1", "F") == "LW"
    # Column 2 = central attacking midfielder
    assert infer_grid_position("4:2", "4-2-3-1", "F") == "CAM"
    # Column 3 = right winger
    assert infer_grid_position("4:3", "4-2-3-1", "F") == "RW"


def test_lineup_grid_4231_row4_midfield_and_forward_categories_agree():
    """M and F provider categories must resolve identically on 4-2-3-1 row 4.

    API-Football sometimes labels the same player as M in lineup data and F in
    the player-stats payload.  The corrected mapping must be consistent across
    both provider categories.
    """
    for provider_cat in ("M", "F"):
        assert infer_grid_position("4:1", "4-2-3-1", provider_cat) == "LW", provider_cat
        assert infer_grid_position("4:2", "4-2-3-1", provider_cat) == "CAM", provider_cat
        assert infer_grid_position("4:3", "4-2-3-1", provider_cat) == "RW", provider_cat


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


def test_position_cohort_verdict_verifies_when_average_aligns_with_direction():
    cohort = {"sampleSize": 10, "average": 6.0, "avgStatValue": 6.0}
    result = position_cohort_verdict(cohort, "over", 5.0)
    assert result["verdict"] == "verifies"
    assert result["average"] == 6.0
    assert result["sampleSize"] == 10


def test_position_cohort_verdict_contradicts_when_average_opposes_direction():
    cohort = {"sampleSize": 8, "average": 3.5}
    result = position_cohort_verdict(cohort, "over", 5.0)
    assert result["verdict"] == "contradicts"


def test_position_cohort_verdict_is_unavailable_when_sample_is_empty():
    result = position_cohort_verdict({}, "over", 5.0)
    assert result["verdict"] == "unavailable"


def test_validate_weighted_opponent_evidence_returns_caution_with_no_cohort_data():
    """Picks without positionComparison should yield 0 eligible samples."""
    rows = [
        {
            "trackingId": f"p{i}",
            "sport": "soccer",
            "propType": "passes",
            "line": 40.0,
            "recommendation": "over",
            "actualValue": 45.0,
            "projectedValue": 42.0,
            "result": "hit",
            "settledAt": f"2026-01-{i:02d}T12:00:00Z",
        }
        for i in range(1, 6)
    ]
    result = validate_weighted_opponent_evidence(rows)
    assert result["eligibleSamples"] == 0
    assert result["promotionDecision"]["verdict"] == "CAUTION"


def test_validate_weighted_opponent_evidence_compares_mae_per_method():
    """Weighted average closer to actual should produce lower MAE."""
    def _row(i, weighted_avg, unweighted_avg, actual, line=40.0, rec="over"):
        outcome = "hit" if (rec == "over" and actual > line) else "miss"
        return {
            "trackingId": f"te-{i}",
            # Unique player identity per row so _event_key does not collapse all rows.
            "playerName": f"Player {i}",
            "playerId": i,
            "sport": "soccer",
            "propType": "passes",
            "line": line,
            "recommendation": rec,
            "actualValue": actual,
            "projectedValue": line,
            "confidenceScore": 70,
            "result": outcome,
            "settledAt": f"2026-0{(i % 9) + 1}-{(i % 28) + 1:02d}T12:00:00Z",
            "positionComparison": {
                "weightedAverage": weighted_avg,
                "unweightedAverage": unweighted_avg,
                "sampleSize": 10,
            },
        }

    # weighted_avg close to actual (41), unweighted far (55)
    rows = [_row(i, weighted_avg=41.0, unweighted_avg=55.0, actual=41.5) for i in range(1, 16)]
    result = validate_weighted_opponent_evidence(rows)
    assert result["eligibleSamples"] == 15
    w_mae = result["weighted"]["projection"]["mae"]
    u_mae = result["unweighted"]["projection"]["mae"]
    assert w_mae < u_mae, "weighted method should have lower MAE when it's closer to actual"


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