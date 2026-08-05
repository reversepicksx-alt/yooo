"""Pure contract tests for the optional TheStatsAPI evidence adapter."""

from thestatsapi_client import (
    _body_data,
    _body_list_or_points,
    _coverage,
    _date_delta_days,
    _find_lineup_player,
    _team_match,
    _trim_points,
    _trim_shots,
)


def test_team_matching_requires_identity_evidence():
    assert _team_match("Minnesota United", "Minnesota United FC")
    assert _team_match("FC Juarez", "Juárez")
    assert not _team_match("Minnesota United", "Minnesota City")


def test_date_delta_handles_provider_iso_dates():
    assert _date_delta_days("2026-08-05T20:00:00Z", "2026-08-05T18:00:00Z") < 0.1
    assert _date_delta_days("not-a-date", "2026-08-05T18:00:00Z") is None


def test_lineup_player_must_belong_to_verified_team():
    lineup = {
        "home": {
            "name": "Minnesota United",
            "starting_xi": [{"id": "pl_1", "name": "Player One"}],
            "substitutes": [],
        },
        "away": {
            "name": "FC Juarez",
            "starting_xi": [{"id": "pl_2", "name": "Player Two"}],
            "substitutes": [],
        },
    }
    player, side = _find_lineup_player(lineup, "Player One", "Minnesota United FC")
    assert player == {"id": "pl_1", "name": "Player One"}
    assert side == "home"
    assert _find_lineup_player(lineup, "Player One", "FC Juarez") == (None, None)


def test_response_normalization_preserves_list_payloads():
    points = {"status": "ok", "body": {"points": [{"x": 12, "y": 42, "count": 3}]}}
    shots = {"status": "ok", "body": {"data": [{"x": 55, "y": 40, "is_goal": True}]}}
    assert _body_data(points)["points"][0]["x"] == 12
    assert _body_list_or_points(shots)[0]["is_goal"] is True
    assert _trim_points(_body_list_or_points(points)) == [{"x": 12.0, "y": 42.0, "count": 3}]
    assert _trim_shots(_body_list_or_points(shots))[0]["isGoal"] is True


def test_coverage_distinguishes_missing_rows_from_measured_data():
    assert _coverage({"status": "ok", "body": {"data": []}}) == "coverage_missing"
    assert _coverage({"status": "ok", "body": {"points": []}}) == "coverage_missing"
    assert _coverage({"status": "ok", "body": {"data": [{"id": 1}]}}) == "measured"
    assert _coverage({"status": "unavailable", "reason": "rate_limited"}) == "rate_limited"