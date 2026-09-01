from matchup_volume import build_matchup_volume_packet


def _row(index, venue="home", **values):
    return {
        "fixtureId": index,
        "date": f"2026-08-{20 - index:02d}",
        "opponent": f"Opponent {index}",
        "venue": venue,
        **values,
    }


def test_matchup_volume_keeps_venue_samples_separate_and_shadow_only():
    home_rows = [
        _row(
            index,
            venue="home",
            teamShotsOnTarget=5,
            opponentShotsOnTarget=4,
            teamPasses=500,
            opponentPasses=440,
        )
        for index in range(10)
    ]
    # These must not leak into the home sample.
    away_rows = [
        _row(
            index + 20,
            venue="away",
            teamShotsOnTarget=99,
            opponentShotsOnTarget=99,
            teamPasses=999,
            opponentPasses=999,
        )
        for index in range(4)
    ]

    packet = build_matchup_volume_packet(
        player_venue="home",
        team_rows=home_rows + away_rows,
        opponent_rows=home_rows + away_rows,
        team_name="Team",
        opponent_name="Opponent",
    )

    assert packet["status"] == "shadow_only"
    assert packet["projectionAdjustment"] == 0
    assert packet["shotsOnTarget"]["teamCreated"]["sampleSize"] == 10
    assert packet["shotsOnTarget"]["teamCreated"]["average"] == 5
    assert packet["passes"]["teamCreated"]["sampleSize"] == 10
    assert len(packet["recentMatchRows"]) == 14


def test_matchup_volume_marks_thin_samples_limited_without_padding():
    rows = [
        _row(
            index,
            venue="away",
            teamShotsOnTarget=2,
            opponentShotsOnTarget=3,
            teamPasses=300,
            opponentPasses=350,
        )
        for index in range(3)
    ]

    packet = build_matchup_volume_packet(
        player_venue="away",
        team_rows=rows,
        opponent_rows=[],
    )

    assert packet["shotsOnTarget"]["teamCreated"]["sampleStatus"] == "limited"
    assert packet["shotsOnTarget"]["teamCreated"]["sampleSize"] == 3
    assert packet["shotsOnTarget"]["opponentCreated"]["sampleStatus"] == "unavailable"
    assert packet["shotsOnTarget"]["expectedTeam"]["average"] == 2


def test_matchup_volume_exposes_fixture_orientation_player_share_and_keeper_rate():
    team_rows = [
        _row(1, "home", teamShotsOnTarget=4, opponentShotsOnTarget=3, teamPasses=500, opponentPasses=420),
        _row(2, "away", teamShotsOnTarget=7, opponentShotsOnTarget=5, teamPasses=550, opponentPasses=460),
    ]
    opponent_rows = [
        _row(11, "home", teamShotsOnTarget=8, opponentShotsOnTarget=4, teamPasses=600, opponentPasses=480),
        _row(12, "away", teamShotsOnTarget=3, opponentShotsOnTarget=6, teamPasses=350, opponentPasses=330),
    ]
    player_logs = [
        {"_fid": "2", "venue": "away", "passes_total": 55, "goals_saves": 4, "opponentShotsOnTarget": 5},
        {"_fid": "1", "venue": "home", "passes_total": 70, "goals_saves": 3, "opponentShotsOnTarget": 4},
    ]

    packet = build_matchup_volume_packet(
        player_venue="away",
        team_rows=team_rows,
        opponent_rows=opponent_rows,
        team_name="Aston Villa",
        opponent_name="PSG",
        player_logs=player_logs,
    )

    assert packet["homeTeam"] == "PSG"
    assert packet["awayTeam"] == "Aston Villa"
    assert packet["fixtureSplits"]["home"]["team"] == "PSG"
    assert packet["fixtureSplits"]["home"]["sotCreated"]["average"] == 8
    assert packet["fixtureSplits"]["away"]["team"] == "Aston Villa"
    assert packet["fixtureSplits"]["away"]["sotCreated"]["average"] == 7
    assert packet["playerPassInvolvement"]["byVenue"]["away"]["sampleSize"] == 1
    assert packet["playerPassInvolvement"]["byVenue"]["away"]["average"] == 10
    assert packet["goalkeeperSaveRate"]["byVenue"]["away"]["sampleSize"] == 1
    assert packet["goalkeeperSaveRate"]["byVenue"]["away"]["average"] == 80