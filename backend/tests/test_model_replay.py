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
        # Scorecard-only keys
        assert "chronologicalHoldout" in scorecard
        assert "n" in scorecard

    def test_replay_description_mentions_walk_forward(self):
        rows = [_make_row(i) for i in range(5)]
        replay = walk_forward_replay(rows)
        assert "walk" in replay["description"].lower() or "prospective" in replay["description"].lower()
