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
    assert len(packet["recentMatchRows"]) == 10


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