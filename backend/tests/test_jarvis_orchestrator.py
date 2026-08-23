from __future__ import annotations

import asyncio

from jarvis_orchestrator import classify_action, execute_action


def test_classifies_named_commands():
    assert classify_action("Script Hunt for Arsenal")[0] == "script_hunt"
    assert classify_action("show me the Board")[0] == "board"
    assert classify_action("Run St. Clair")[0] == "run_player"
    assert classify_action("Opposite Case")[0] == "opposite_case"
    assert classify_action("Refresh Lines")[0] == "refresh_lines"
    assert classify_action("Postmortem")[0] == "postmortem"


def test_run_player_parses_plays_as_venue_not_prop():
    action, args = classify_action("Run Rongier plays at home vs PSG line 52.5")
    assert action == "run_player"
    assert args["player_query"] == "Rongier"
    assert args["opponent_query"] == "PSG"
    assert args["venue"] == "home"
    assert args["line"] == 52.5
    assert args["prop_type"] is None


def test_run_player_infers_prop_from_one_matching_board_market():
    async def empty():
        return []

    async def board():
        return [{
            "playerName": "Valentin Rongier",
            "line": 52.5,
            "propType": "pass_attempts",
        }]

    result = asyncio.run(execute_action(
        "Run Rongier plays at home vs PSG line 52.5",
        context=None,
        load_picks=empty,
        find_team=lambda _: empty(),
        fetch_fixtures=lambda _: empty(),
        discover_slate=empty,
        load_board=board,
        load_memory=empty,
    ))
    assert result["data"]["player_query"] == "Rongier"
    assert result["data"]["venue"] == "home"
    assert result["data"]["opponent_query"] == "PSG"
    assert result["data"]["inferred_prop_type"] == "pass_attempts"


def test_script_hunt_dispatches_fixture_and_board_tools():
    calls = []

    async def slate():
        calls.append(("slate",))
        return [{"fixture": {"id": 99}, "teams": {
            "home": {"id": 7, "name": "Arsenal"},
            "away": {"id": 8, "name": "Chelsea"},
        }}]

    async def board():
        calls.append(("board",))
        return [{"playerName": "A", "marketLine": 2.5}]

    async def empty():
        return []

    result = asyncio.run(execute_action(
        "Script hunt",
        context=None,
        load_picks=empty,
        find_team=lambda _: empty(),
        fetch_fixtures=lambda _: empty(),
        discover_slate=slate,
        load_board=board,
        load_memory=empty,
    ))
    assert result["action"] == "script_hunt"
    assert result["status"] == "partial"
    assert [call[0] for call in calls] == ["slate", "board"]
    assert result["data"]["home_control_filter"]["status"] == "partial"
    assert result["data"]["fixtures"][0]["homeTeam"]["name"] == "Arsenal"
    assert result["provenance"]["tool_results_are_read_only"] is True


def test_tool_failures_are_unknown_and_do_not_raise():
    async def fail():
        raise RuntimeError("provider down")

    async def empty():
        return []

    result = asyncio.run(execute_action(
        "Board",
        context=None,
        load_picks=empty,
        find_team=lambda _: empty(),
        fetch_fixtures=lambda _: empty(),
        discover_slate=empty,
        load_board=fail,
        load_memory=empty,
    ))
    assert result["action"] == "board"
    assert result["status"] == "UNKNOWN"
    assert result["tools"][0]["status"] == "UNKNOWN"
    assert "won't invent" in result["response"]