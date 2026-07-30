from model_metrics import build_scorecard


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