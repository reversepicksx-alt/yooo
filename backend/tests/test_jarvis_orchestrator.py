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


def test_script_hunt_dispatches_fixture_and_board_tools():
    calls = []

    async def team(query):
        calls.append(("team", query))
        return {"teamId": 7, "teamName": "Arsenal"}

    async def fixtures(team_id):
        calls.append(("fixtures", team_id))
        return [{"fixture": {"id": 99}}]

    async def board():
        calls.append(("board",))
        return [{"playerName": "A", "marketLine": 2.5}]

    async def empty():
        return []

    result = asyncio.run(execute_action(
        "Script Hunt for Arsenal",
        context=None,
        load_picks=empty,
        find_team=team,
        fetch_fixtures=fixtures,
        load_board=board,
        load_memory=empty,
    ))
    assert result["action"] == "script_hunt"
    assert result["status"] == "partial"
    assert [call[0] for call in calls] == ["team", "fixtures", "board"]
    assert result["data"]["home_control_filter"]["status"] == "UNKNOWN"
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
        load_board=fail,
        load_memory=empty,
    ))
    assert result["action"] == "board"
    assert result["status"] == "UNKNOWN"
    assert result["tools"][0]["status"] == "UNKNOWN"
    assert "won't invent" in result["response"]