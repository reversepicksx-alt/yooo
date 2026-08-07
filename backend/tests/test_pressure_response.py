from pressure_response import classify_pressure_response


def _game(possession, passes, day):
    return {
        "minutes": 90,
        "teamPossession": possession,
        "passes_total": passes,
        "date": f"2026-05-{day:02d}",
    }


def test_classifies_player_who_freezes_under_low_possession():
    logs = (
        [_game(42, 35 + i, 1 + i) for i in range(6)]
        + [_game(58, 60 + i, 11 + i) for i in range(6)]
    )
    result = classify_pressure_response(logs, expected_possession=43, possession_is_real=True)
    assert result["status"] == "classified"
    assert result["classification"] == "pressure_sensitive"
    assert result["currentEnvironment"] == "high_pressure"
    assert result["highPressurePassesPer90"] < result["lowPressurePassesPer90"]
    assert result["projectionAdjustmentStatus"] == "shadow_only"


def test_classifies_player_who_increases_passing_under_pressure():
    logs = (
        [_game(41, 75 + i, 1 + i) for i in range(6)]
        + [_game(59, 60 + i, 11 + i) for i in range(6)]
    )
    result = classify_pressure_response(logs, expected_possession=41, possession_is_real=True)
    assert result["status"] == "classified"
    assert result["classification"] == "pressure_resistant"
    assert result["pressureMultiplier"] > 1.10


def test_requires_both_buckets_and_ignores_partial_appearances():
    logs = (
        [_game(42, 35, 1) for _ in range(5)]
        + [_game(58, 60, 11) for _ in range(6)]
        + [{**_game(42, 90, 20), "minutes": 30}]
    )
    result = classify_pressure_response(logs)
    assert result["status"] == "insufficient_evidence"
    assert result["classification"] == "unknown"
    assert result["highPressureSamples"] == 5
