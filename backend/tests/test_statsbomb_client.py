from statsbomb_client import (
    _competition_candidates,
    _find_match,
    _target_lineup,
    compute_event_metrics,
    summarize_freeze_frames,
)


def _event(team, kind, x, *, period=1, minute=1, second=0, **extra):
    event = {
        "period": period,
        "minute": minute,
        "second": second,
        "team": {"id": team, "name": str(team)},
        "type": {"name": kind},
        "location": [x, 40],
    }
    event.update(extra)
    return event


def _pass_event(team, x, end_x, *, period=1, minute=1, second=0, under_pressure=False):
    return _event(
        team,
        "Pass",
        x,
        period=period,
        minute=minute,
        second=second,
        **{
            "pass": {
                "end_location": [end_x, 40],
                "under_pressure": under_pressure,
            }
        },
    )


def test_statsbomb_event_metrics_calculate_pressure_zone_ppda_and_pressure_regain():
    events = [
        _pass_event(10, 20, 25, minute=1),
        _event(10, "Pressure", 20, minute=1, counterpress=True),
        _pass_event(20, 90, 95, minute=1, under_pressure=True),
        _event(10, "Ball Recovery", 20, minute=1, second=3),
        _event(10, "Interception", 25, minute=2),
        _pass_event(20, 90, 95, minute=2),
        _event(10, "Duel", 30, minute=3, duel={"type": {"name": "Tackle"}}),
        _event(10, "Pressure", 60, minute=4),
        _pass_event(10, 60, 65, minute=4),
    ]
    result = compute_event_metrics(events, team_id=10, opponent_id=20)
    assert result["pressureEvents"] == 2
    assert result["counterpressures"] == 1
    assert result["pressureByThird"] == {"defensive": 1, "middle": 1, "attacking": 0}
    assert result["passesUnderPressure"]["opponent"] == 1
    assert result["pressureRegains"] == 1
    assert result["opponentPassesInPressZone"] == 2
    assert result["defensiveActionsInPressZone"] == 3
    assert result["ppda"] == 0.67
    assert result["ppdaStatus"] == "event_derived"


def test_statsbomb_event_metrics_marks_unknown_coordinate_mode_unavailable():
    events = [
        _event(10, "Pass", 20, **{"pass": {}}),
        _event(10, "Pressure", 20),
        _pass_event(20, 90, 95),
    ]
    result = compute_event_metrics(events, team_id=10, opponent_id=20)
    assert result["coordinateMode"] == "unknown"
    assert result["ppda"] is None
    assert result["ppdaStatus"] == "unavailable"


def test_statsbomb_match_matching_requires_exact_date_and_opponent():
    rows = [
        {
            "match_id": 1,
            "match_date": "2015-09-19",
            "home_team": {"home_team_name": "Chelsea"},
            "away_team": {"away_team_name": "Arsenal"},
        },
        {
            "match_id": 2,
            "match_date": "2015-09-20",
            "home_team": {"home_team_name": "Chelsea"},
            "away_team": {"away_team_name": "Arsenal"},
        },
    ]
    result = _find_match(rows, team_name="Arsenal", opponent_name="Chelsea", match_date="2015-09-19")
    assert result["match_id"] == 1


def test_statsbomb_candidates_use_mapped_competition_and_season_date():
    rows = [
        {"competition_id": 2, "competition_name": "Premier League", "season_id": 27, "season_name": "2015/2016"},
        {"competition_id": 2, "competition_name": "Premier League", "season_id": 281, "season_name": "2023/2024"},
    ]
    result = _competition_candidates(rows, league_id=39, league_name="Premier League", match_date="2015-09-19")
    assert len(result) == 1
    assert result[0]["season_id"] == 27


def test_statsbomb_freeze_frame_summary_is_limited_event_snapshot_evidence():
    result = summarize_freeze_frames([
        {"event_uuid": "a", "freeze_frame": [{"location": [10, 10]}]},
        {"event_uuid": "b", "freeze_frame": [{"location": [20, 20]}, {"location": [30, 30]}]},
    ])
    assert result["available"] is True
    assert result["eventCount"] == 2
    assert result["averageVisiblePlayers"] == 1.5
    assert result["status"] == "limited_event_snapshots"


def test_statsbomb_lineup_parser_supports_open_data_lineup_schema_and_name_bridge():
    result = _target_lineup(
        [
            {
                "team_id": 1,
                "team_name": "Arsenal",
                "lineup": [
                    {
                        "player_id": 3385,
                        "player_name": "Alexis Alejandro Sánchez Sánchez",
                        "positions": [
                            {
                                "position": "Left Wing",
                                "start_reason": "Starting XI",
                                "from_period": 1,
                            }
                        ],
                    }
                ],
            }
        ],
        team_name="Arsenal",
        player_name="Alexis Sanchez",
    )
    assert result == {
        "id": 3385,
        "name": "Alexis Alejandro Sánchez Sánchez",
        "position": "Left Wing",
        "starter": True,
    }