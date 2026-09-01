import sys
sys.path.insert(0, "/app/backend")

from passing_diagnostics import build_passing_diagnostics


def _row(i, *, fixture="fx-1", venue="home", league=128, position="M",
         result="hit", recommendation="under", actual=20, projection=18,
         home_poss=50, away_poss=50):
    return {
        "sport": "soccer",
        "trackingId": f"save-{i}",
        "fixtureId": fixture,
        "fixtureDate": f"2026-08-{10 + i:02d}T20:00:00Z",
        "settledAt": f"2026-08-{10 + i:02d}T22:00:00Z",
        "playerId": 1000 + i,
        "playerName": f"Player {i}",
        "teamId": 10,
        "opponentId": 20,
        "teamName": "Team",
        "opponentName": "Opponent",
        "propType": "pass_attempts",
        "line": 25.5,
        "recommendation": recommendation,
        "result": result,
        "actualValue": actual,
        "projectedValue": projection,
        "confidenceScore": 65,
        "leagueId": league,
        "position": position,
        "venue": venue,
        "homePoss": home_poss,
        "awayPoss": away_poss,
        "settlementSource": {
            "verified": True,
            "fixtureId": fixture,
            "statPath": "statistics.passes.total",
        },
    }


def test_correlated_and_independent_events_are_separated():
    rows = [
        _row(0, fixture="fx-a", result="hit"),
        _row(1, fixture="fx-a", result="miss", actual=40),
        _row(2, fixture="fx-b", result="hit"),
    ]
    result = build_passing_diagnostics(rows)

    corr = result["correlationSummary"]
    assert corr["correlatedEvents"] == 2
    assert corr["independentEvents"] == 1
    assert corr["correlated"]["n"] == 2
    assert corr["correlated"]["hitRate"] == 50.0
    assert corr["independent"]["hitRate"] == 100.0


def test_leakage_safe_replay_has_no_future_rows():
    rows = [
        _row(0, fixture="fx-a", result="hit"),
        _row(1, fixture="fx-b", result="miss", actual=40),
        _row(2, fixture="fx-c", result="hit"),
    ]
    result = build_passing_diagnostics(rows)

    replay = result["walkForward"]["overall"]
    assert replay["n"] == 3
    assert replay["leakageViolations"] == 0
    assert replay["missingPriorDataEvents"] == 1
    assert result["walkForward"]["byLeague"][0]["leakageViolations"] == 0


def test_same_settlement_time_ties_are_not_reported_as_leakage():
    first = _row(0, fixture="fx-a")
    second = _row(1, fixture="fx-b", result="miss", actual=40)
    second["settledAt"] = first["settledAt"]
    result = build_passing_diagnostics([first, second])

    assert result["walkForward"]["overall"]["leakageViolations"] == 0


def test_away_possession_uses_away_team_value():
    result = build_passing_diagnostics([
        _row(0, fixture="fx-a", venue="away", away_poss=62, home_poss=38),
    ])
    labels = {row["label"] for row in result["dimensions"]["possessionBand"]}
    assert "60%+" in labels


def test_duplicate_saves_collapse_but_distinct_lines_remain():
    duplicate = _row(0, fixture="fx-a")
    duplicate["trackingId"] = "another-save"
    distinct_line = _row(0, fixture="fx-a")
    distinct_line["trackingId"] = "different-line"
    distinct_line["line"] = 30.5
    result = build_passing_diagnostics([duplicate, duplicate.copy(), distinct_line])

    assert result["scope"]["rawRows"] == 3
    assert result["scope"]["uniqueEvents"] == 2
    assert result["correlationSummary"]["correlatedEvents"] == 2


def test_missing_fixture_identity_is_not_called_independent():
    row = _row(0, fixture=None)
    row.pop("fixtureId", None)
    result = build_passing_diagnostics([row])

    corr = result["correlationSummary"]
    assert corr["fixtureIdentityUnavailableEvents"] == 1
    assert corr["independentEvents"] == 0
    assert result["sourceAudit"]["missingFixtureEvents"] == 1


def test_argentina_league_128_has_its_own_dimension_bucket():
    result = build_passing_diagnostics([
        _row(0, fixture="arg-1", league=128),
        _row(1, fixture="mls-1", league=772, result="miss", actual=40),
    ])

    labels = {row["label"] for row in result["dimensions"]["league"]}
    assert "Liga Profesional" in labels
    assert "Leagues Cup" in labels