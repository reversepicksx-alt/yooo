"""Tests for walk_forward_replay() and the /api/admin/model-replay endpoint.

These tests verify:
- walk_forward_replay returns all required keys with correct types
- chronological ordering and leakage detection work correctly
- empty inputs are handled gracefully
- bySport and byProp breakdowns are produced per the schema
- prospective calibration bins are populated correctly
- build_scorecard and walk_forward_replay results are distinct (replay is not
  the same as the descriptive scorecard)
"""
import sys
sys.path.insert(0, '/app/backend')

import math
import pytest
from model_metrics import walk_forward_replay, build_scorecard, dedupe_prediction_rows


def _make_row(i: int, sport: str = "soccer", prop: str = "passes",
              hit: bool = True, confidence: float = 70.0) -> dict:
    """Return a minimal settled pick row."""
    return {
        "trackingId": f"t{i:04d}",
        "settledAt": f"2026-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}T12:00:00Z",
        "result": "hit" if hit else "miss",
        "confidenceScore": confidence,
        "projectedValue": 10.0 + i * 0.2,
        "actualValue": 10.5 + i * 0.2,
        "sport": sport,
        "propType": prop,
        "line": 9.5,
        "playerName": f"Player{i}",
        "recommendation": "over",
        "venue": "home",
        "fixtureId": f"fx{i:04d}",
        "timestamp": f"2026-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}T10:00:00Z",
    }


class TestWalkForwardReplayStructure:
    """Verify the return schema is complete and well-typed."""

    def test_top_level_keys_present(self):
        rows = [_make_row(i) for i in range(10)]
        result = walk_forward_replay(rows)
        for key in (
            "description", "eligibleSamples", "evaluatedSamples",
            "missingPriorDataEvents", "leakageViolations",
            "dateRange", "classification", "prospectiveCalibration",
            "projection", "bySport", "byProp",
            "byDirection",
        ):
            assert key in result, f"Missing key: {key}"

    def test_classification_keys(self):
        rows = [_make_row(i) for i in range(10)]
        result = walk_forward_replay(rows)
        cl = result["classification"]
        assert "n" in cl
        assert "logLoss" in cl
        assert "brierScore" in cl
        assert cl["n"] == 10
        assert isinstance(cl["logLoss"], float)
        assert isinstance(cl["brierScore"], float)

    def test_direction_breakdown_counts_only_scored_market_events(self):
        rows = [
            _make_row(i, hit=i < 3, confidence=70.0)
            for i in range(5)
        ]
        rows[1]["recommendation"] = "under"
        rows[2]["recommendation"] = "under"
        rows[3]["result"] = "push"
        result = walk_forward_replay(rows)

        assert result["byDirection"]["over"]["n"] == 2
        assert result["byDirection"]["over"]["hits"] == 1
        assert result["byDirection"]["under"]["n"] == 2
        assert result["byDirection"]["under"]["hits"] == 2
        assert "push" not in result["byDirection"]

    def test_projection_keys(self):
        rows = [_make_row(i) for i in range(10)]
        result = walk_forward_replay(rows)
        proj = result["projection"]
        assert proj["n"] == 10
        assert isinstance(proj["mae"], float)
        assert isinstance(proj["rmse"], float)
        assert isinstance(proj["meanError"], float)

    def test_by_sport_structure(self):
        rows = [_make_row(i, sport="soccer" if i % 2 == 0 else "mlb") for i in range(10)]
        result = walk_forward_replay(rows)
        assert len(result["bySport"]) == 2
        sports = {e["sport"] for e in result["bySport"]}
        assert sports == {"soccer", "mlb"}
        for entry in result["bySport"]:
            assert "classification" in entry
            assert "projection" in entry

    def test_by_prop_structure(self):
        rows = [_make_row(i, prop="passes" if i % 2 == 0 else "shots") for i in range(10)]
        result = walk_forward_replay(rows)
        assert len(result["byProp"]) >= 1
        for entry in result["byProp"]:
            assert "sport" in entry
            assert "propType" in entry
            assert "n" in entry
            assert "mae" in entry

    def test_description_is_non_empty_string(self):
        rows = [_make_row(i) for i in range(5)]
        result = walk_forward_replay(rows)
        assert isinstance(result["description"], str)
        assert len(result["description"]) > 20


class TestWalkForwardReplayCorrectness:
    """Verify numerical correctness and leakage guards."""

    def test_zero_leakage_on_strictly_ordered_input(self):
        rows = [_make_row(i) for i in range(20)]
        result = walk_forward_replay(rows)
        assert result["leakageViolations"] == 0

    def test_missing_prior_data_exactly_one_for_nonempty_input(self):
        """The very first pick has no prior data — exactly one missing-prior event."""
        rows = [_make_row(i) for i in range(15)]
        result = walk_forward_replay(rows)
        assert result["missingPriorDataEvents"] == 1

    def test_eligible_samples_equals_deduped_count(self):
        rows = [_make_row(i) for i in range(12)]
        deduped = dedupe_prediction_rows(rows)
        result = walk_forward_replay(rows)
        assert result["eligibleSamples"] == len(deduped)

    def test_log_loss_is_finite_positive(self):
        rows = [_make_row(i, hit=(i % 3 != 2)) for i in range(20)]
        result = walk_forward_replay(rows)
        ll = result["classification"]["logLoss"]
        assert ll is not None
        assert math.isfinite(ll)
        assert ll > 0

    def test_brier_score_in_0_1(self):
        rows = [_make_row(i, hit=(i % 2 == 0)) for i in range(20)]
        result = walk_forward_replay(rows)
        bs = result["classification"]["brierScore"]
        assert bs is not None
        assert 0.0 <= bs <= 1.0

    def test_mae_is_nonnegative(self):
        rows = [_make_row(i) for i in range(20)]
        result = walk_forward_replay(rows)
        assert result["projection"]["mae"] >= 0.0

    def test_rmse_ge_mae(self):
        """RMSE is always ≥ MAE."""
        rows = [_make_row(i) for i in range(20)]
        result = walk_forward_replay(rows)
        assert result["projection"]["rmse"] >= result["projection"]["mae"]

    def test_prospective_calibration_bins_have_required_keys(self):
        rows = [_make_row(i, confidence=60 + (i % 30)) for i in range(30)]
        result = walk_forward_replay(rows)
        for b in result["prospectiveCalibration"]:
            for key in ("label", "n", "prospectiveN", "finalObservedPct"):
                assert key in b, f"Missing key {key} in calibration bin {b}"

    def test_date_range_populated(self):
        rows = [_make_row(i) for i in range(10)]
        result = walk_forward_replay(rows)
        assert result["dateRange"]["from"] is not None
        assert result["dateRange"]["to"] is not None


class TestWalkForwardReplayEdgeCases:
    """Edge cases: empty input, single row, missing fields."""

    def test_empty_input_returns_zero_counts(self):
        result = walk_forward_replay([])
        assert result["eligibleSamples"] == 0
        assert result["evaluatedSamples"] == 0
        assert result["leakageViolations"] == 0
        assert result["classification"]["n"] == 0
        assert result["classification"]["logLoss"] is None
        assert result["projection"]["n"] == 0
        assert result["projection"]["mae"] is None

    def test_single_row_returns_valid_result(self):
        result = walk_forward_replay([_make_row(0)])
        assert result["eligibleSamples"] == 1
        assert result["classification"]["n"] == 1
        assert result["projection"]["n"] == 1
        assert result["leakageViolations"] == 0
        assert result["missingPriorDataEvents"] == 1

    def test_rows_without_confidence_score_still_compute_regression(self):
        rows = []
        for i in range(5):
            r = _make_row(i)
            del r["confidenceScore"]
            rows.append(r)
        result = walk_forward_replay(rows)
        assert result["classification"]["n"] == 0
        assert result["projection"]["n"] == 5

    def test_rows_without_actual_value_still_compute_classification(self):
        rows = []
        for i in range(5):
            r = _make_row(i)
            del r["actualValue"]
            rows.append(r)
        result = walk_forward_replay(rows)
        assert result["projection"]["n"] == 0
        assert result["classification"]["n"] == 5

    def test_deduplication_applied(self):
        """Duplicate trackingIds are collapsed before replay."""
        row = _make_row(0)
        rows = [row, row, row]  # same trackingId three times
        result = walk_forward_replay(rows)
        assert result["eligibleSamples"] == 1


class TestReplayVsScorecard:
    """walk_forward_replay and build_scorecard must be clearly distinct."""

    def test_replay_has_keys_absent_from_scorecard(self):
        rows = [_make_row(i) for i in range(20)]
        replay = walk_forward_replay(rows)
        scorecard = build_scorecard(rows)
        # Replay-only keys
        assert "leakageViolations" in replay
        assert "missingPriorDataEvents" in replay
        assert "prospectiveCalibration" in replay
        assert "bySport" in replay
        assert "goNoGo" in replay
        # Scorecard-only keys
        assert "chronologicalHoldout" in scorecard
        assert "n" in scorecard

    def test_replay_description_mentions_walk_forward(self):
        rows = [_make_row(i) for i in range(5)]
        replay = walk_forward_replay(rows)
        assert "walk" in replay["description"].lower() or "prospective" in replay["description"].lower()


class TestGoNoGoRecommendation:
    """The goNoGo field must carry a structured verdict with required keys."""

    def test_go_no_go_keys_present(self):
        rows = [_make_row(i) for i in range(20)]
        result = walk_forward_replay(rows)
        gng = result["goNoGo"]
        for key in ("verdict", "summary", "issues", "positives", "basisN", "note"):
            assert key in gng, f"Missing goNoGo key: {key}"

    def test_verdict_is_one_of_three_values(self):
        rows = [_make_row(i) for i in range(20)]
        result = walk_forward_replay(rows)
        assert result["goNoGo"]["verdict"] in {"GO", "CAUTION", "NO_GO"}

    def test_summary_is_non_empty_string(self):
        rows = [_make_row(i) for i in range(20)]
        result = walk_forward_replay(rows)
        assert isinstance(result["goNoGo"]["summary"], str)
        assert len(result["goNoGo"]["summary"]) > 10

    def test_issues_and_positives_are_lists(self):
        rows = [_make_row(i) for i in range(20)]
        result = walk_forward_replay(rows)
        assert isinstance(result["goNoGo"]["issues"], list)
        assert isinstance(result["goNoGo"]["positives"], list)

    def test_basis_n_equals_eligible_samples(self):
        rows = [_make_row(i) for i in range(15)]
        result = walk_forward_replay(rows)
        assert result["goNoGo"]["basisN"] == result["eligibleSamples"]

    def test_empty_input_produces_caution_not_go(self):
        """Empty corpus must return CAUTION — no checks ran, so GO is unsafe."""
        result = walk_forward_replay([])
        gng = result["goNoGo"]
        assert "verdict" in gng
        assert gng["verdict"] == "CAUTION", (
            f"Empty corpus must return CAUTION (no evidence), got {gng['verdict']!r}. "
            f"Summary: {gng.get('summary')}"
        )
        # Summary must mention the evidence gap, not claim all checks passed
        assert "insufficient" in gng["summary"].lower() or "missing" in gng["summary"].lower(), (
            f"Empty-corpus summary must describe missing evidence, got: {gng['summary']!r}"
        )

    def test_under_threshold_corpus_produces_caution_not_go(self):
        """A corpus with only 5 rows is too thin for most checks — must not be GO."""
        rows = [_make_row(i) for i in range(5)]
        result = walk_forward_replay(rows)
        gng = result["goNoGo"]
        assert gng["verdict"] in {"CAUTION", "NO_GO"}, (
            f"Under-threshold corpus (n=5) must not receive GO verdict, got {gng['verdict']!r}"
        )

    def test_unscored_rows_only_produces_caution_not_go(self):
        """Rows with result=push (no scored directional outcome) leave classification empty."""
        rows = []
        for i in range(20):
            r = _make_row(i, hit=True, confidence=70.0)
            r["result"] = "push"
            rows.append(r)
        result = walk_forward_replay(rows)
        gng = result["goNoGo"]
        # push rows produce no scored classification events → check cannot run
        assert gng["verdict"] in {"CAUTION", "NO_GO"}, (
            f"Push-only corpus must not receive GO verdict (no scored events), "
            f"got {gng['verdict']!r}"
        )

    def test_coin_flip_confidence_produces_caution_or_no_go(self):
        """Rows with 50% confidence alternating hit/miss should not get GO.

        A model at exactly coin-flip calibration (50% confidence, ~50% hit rate)
        cannot beat the log-loss baseline and must not receive a GO verdict.
        """
        rows = []
        for i in range(30):
            r = _make_row(i, hit=(i % 2 == 0), confidence=50.0)
            rows.append(r)
        result = walk_forward_replay(rows)
        assert result["goNoGo"]["verdict"] in {"CAUTION", "NO_GO"}, (
            "50%-confidence alternating hit/miss model must not receive GO verdict"
        )

    def test_well_calibrated_model_gets_go_or_caution(self):
        """Rows with high confidence and consistent hits should get GO or CAUTION."""
        # 75% confidence, 80% hit rate — well calibrated, should not be NO_GO
        rows = []
        for i in range(40):
            rows.append(_make_row(i, hit=(i % 5 != 0), confidence=75.0))
        result = walk_forward_replay(rows)
        assert result["goNoGo"]["verdict"] in {"GO", "CAUTION"}, (
            "Well-calibrated model (75% confidence, 80% hit rate) must not be NO_GO"
        )

    def test_severe_direction_asymmetry_flagged_in_issues(self):
        """A large OVER/UNDER hit-rate gap must appear in goNoGo issues."""
        rows = []
        # OVER picks: 10 rows, all miss (0% hit rate)
        for i in range(10):
            r = _make_row(i, hit=False, confidence=70.0)
            r["recommendation"] = "over"
            rows.append(r)
        # UNDER picks: 10 rows, all hit (100% hit rate)
        for i in range(10, 20):
            r = _make_row(i, hit=True, confidence=70.0)
            r["recommendation"] = "under"
            rows.append(r)
        result = walk_forward_replay(rows)
        issues_text = " ".join(result["goNoGo"]["issues"]).lower()
        assert "direction" in issues_text or "asymmetr" in issues_text, (
            "Severe OVER/UNDER asymmetry (0% vs 100% hit rate) must appear in goNoGo issues"
        )

    def test_systematic_projection_bias_flagged_in_issues(self):
        """Consistent over-projection should surface in goNoGo issues."""
        rows = []
        for i in range(20):
            r = _make_row(i, hit=True, confidence=70.0)
            # Projected is always 5 units above actual → systematic over-projection
            r["projectedValue"] = 20.0
            r["actualValue"] = 5.0
            rows.append(r)
        result = walk_forward_replay(rows)
        issues_text = " ".join(result["goNoGo"]["issues"]).lower()
        assert "bias" in issues_text or "project" in issues_text, (
            "Systematic projection bias (meanError far from zero) must appear in goNoGo issues"
        )


class TestDirectionSplitCalibration:
    """prospectiveCalibration bins must include a byDirection OVER/UNDER breakdown."""

    def test_calibration_bins_have_by_direction_key(self):
        rows = [_make_row(i, confidence=70.0) for i in range(30)]
        result = walk_forward_replay(rows)
        for b in result["prospectiveCalibration"]:
            assert "byDirection" in b, (
                f"Calibration bin {b.get('label')} missing byDirection key"
            )

    def test_by_direction_has_over_and_under_keys(self):
        rows = [_make_row(i, confidence=70.0) for i in range(30)]
        result = walk_forward_replay(rows)
        for b in result["prospectiveCalibration"]:
            assert "over" in b["byDirection"], "byDirection must contain 'over'"
            assert "under" in b["byDirection"], "byDirection must contain 'under'"

    def test_by_direction_over_populated_for_over_picks(self):
        """Bins for rows with recommendation=over must populate the over bucket."""
        rows = []
        for i in range(40):
            r = _make_row(i, confidence=70.0, hit=(i % 2 == 0))
            r["recommendation"] = "over"
            rows.append(r)
        result = walk_forward_replay(rows)
        bins_with_over = [
            b for b in result["prospectiveCalibration"]
            if b["byDirection"].get("over") is not None
        ]
        assert bins_with_over, "At least one calibration bin should have over data"
        for b in bins_with_over:
            over = b["byDirection"]["over"]
            assert "n" in over
            assert "priorPredictedPct" in over
            assert "observedPct" in over
            assert "gapPp" in over

    def test_by_direction_under_populated_for_under_picks(self):
        """Bins for rows with recommendation=under must populate the under bucket."""
        rows = []
        for i in range(40):
            r = _make_row(i, confidence=75.0, hit=(i % 3 != 2))
            r["recommendation"] = "under"
            rows.append(r)
        result = walk_forward_replay(rows)
        bins_with_under = [
            b for b in result["prospectiveCalibration"]
            if b["byDirection"].get("under") is not None
        ]
        assert bins_with_under, "At least one calibration bin should have under data"

    def test_direction_split_counts_sum_to_at_most_overall(self):
        """Sum of OVER + UNDER prospective n must not exceed overall prospective n."""
        rows = []
        for i in range(40):
            r = _make_row(i, confidence=70.0, hit=(i % 2 == 0))
            r["recommendation"] = "over" if i % 3 != 0 else "under"
            rows.append(r)
        result = walk_forward_replay(rows)
        for b in result["prospectiveCalibration"]:
            over_n = (b["byDirection"].get("over") or {}).get("n", 0)
            under_n = (b["byDirection"].get("under") or {}).get("n", 0)
            total_prosp = b["prospectiveN"]
            assert over_n + under_n <= total_prosp, (
                f"Bin {b['label']}: over ({over_n}) + under ({under_n}) "
                f"> prospectiveN ({total_prosp})"
            )
