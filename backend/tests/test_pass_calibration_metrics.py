from model_metrics import build_scorecard
from routes.picks import _pass_lean, _settle_pick_result


def test_legacy_pass_with_explicit_lean_settles_as_normal_hit():
    result, outcome = _settle_pick_result(
        56, 62.5, {"recommendation": "pass", "passLeaning": "under"}
    )
    assert (result, outcome) == ("hit", None)


def test_pass_recovers_strict_projection_direction():
    assert _pass_lean({"recommendation": "pass", "projection": 49, "line": 62.5}) == "under"
    assert _pass_lean({"recommendation": "pass", "projection": 70, "line": 62.5}) == "over"


def test_pass_tie_remains_ambiguous():
    assert _pass_lean({"recommendation": "pass", "projection": 62.5, "line": 62.5}) is None


def test_pass_calibration_is_separate_from_normal_scorecard_metrics():
    rows = [
        {
            "trackingId": "actionable",
            "sport": "soccer",
            "fixtureId": 1,
            "playerId": 1,
            "playerName": "Player",
            "propType": "passes",
            "line": 50,
            "recommendation": "under",
            "confidenceScore": 70,
            "projectedValue": 45,
            "actualValue": 40,
            "result": "hit",
            "settledAt": "2026-01-01T00:00:00+00:00",
        },
        {
            "trackingId": "pass",
            "sport": "soccer",
            "fixtureId": 2,
            "playerId": 2,
            "playerName": "Pass Player",
            "propType": "passes",
            "line": 62.5,
            "recommendation": "pass",
            "passLeaning": "under",
            "passOutcome": "hit",
            "isCalibrationOnly": True,
            "confidenceScore": 99,
            "projectedValue": 49,
            "actualValue": 56,
            "result": "pass",
            "settledAt": "2026-01-02T00:00:00+00:00",
        },
    ]
    scorecard = build_scorecard(rows)
    assert scorecard["classification"]["finalConfidence"]["n"] == 1
    assert scorecard["passCalibration"] == {
        "n": 1,
        "hits": 1,
        "misses": 0,
        "pushes": 0,
        "winPct": 100.0,
        "byDirection": {"under": {"hit": 1, "miss": 0, "push": 0}},
    }