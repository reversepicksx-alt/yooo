from routes.players import _confirmed_search_rows


def _row(**overrides):
    row = {
        "id": 101,
        "name": "Reinaldo Example",
        "teamId": 77,
        "teamName": "Example FC",
        "leagueId": 39,
        "position": "Defender",
    }
    row.update(overrides)
    return row


def test_generic_provider_category_is_not_displayed_as_confirmed_position():
    assert _confirmed_search_rows([_row()]) == []


def test_provider_exact_position_keeps_team_and_marks_identity_confirmed():
    result = _confirmed_search_rows([_row(position="Left Back")])

    assert len(result) == 1
    assert result[0]["teamName"] == "Example FC"
    assert result[0]["position"] == "LB"
    assert result[0]["positionVerified"] is True
    assert result[0]["teamConfirmed"] is True
    assert result[0]["positionSource"] == "provider_exact"


def test_grounded_position_is_allowed_only_for_the_same_returned_team():
    grounded = {
        101: {
            "specificPosition": "CB",
            "source": "gemini_web_grounded",
            "promptVersion": 8,
            "team": "Example FC",
        }
    }

    result = _confirmed_search_rows([_row()], grounded)

    assert len(result) == 1
    assert result[0]["position"] == "CB"
    assert result[0]["positionSource"] == "gemini_web_grounded"


def test_grounded_position_for_another_team_is_not_used_for_search_identity():
    grounded = {
        101: {
            "specificPosition": "CB",
            "source": "gemini_web_grounded",
            "promptVersion": 8,
            "team": "Former FC",
        }
    }

    assert _confirmed_search_rows([_row()], grounded) == []


def test_same_name_players_remain_separate_by_player_and_team_identity():
    rows = [
        _row(id=101, teamId=77, teamName="Example FC", position="CB"),
        _row(id=202, teamId=88, teamName="Other FC", position="GK"),
    ]

    result = _confirmed_search_rows(rows)

    assert [(p["id"], p["teamName"], p["position"]) for p in result] == [
        (101, "Example FC", "CB"),
        (202, "Other FC", "GK"),
    ]