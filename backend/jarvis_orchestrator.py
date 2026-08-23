"""Owner-only, action-driven JARVIS conversation orchestration.

This module is deliberately read-only.  It turns natural language into named
actions, calls existing bounded Reverse Picks data capabilities, and returns a
structured result that can be rendered by any JARVIS client.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable


ACTION_SPECS = {
    "script_hunt": "fixture discovery, home-control filtering, tactical matchup, and board candidates",
    "board": "current player-prop board search",
    "run_player": "exact player and matchup analysis",
    "opposite_case": "strongest opposite-case stress test",
    "refresh_lines": "current line and movement check",
    "postmortem": "settled-pick postmortem and lessons",
    "general": "read-only owner intelligence question",
}


def classify_action(message: str) -> tuple[str, dict[str, Any]]:
    text = str(message or "").strip()
    lowered = text.lower()
    if re.search(r"\b(script\s+hunt|hunt\s+the\s+slate|find\s+the\s+best\s+script)\b", lowered):
        return "script_hunt", {}
    if re.search(r"\b(board|show\s+(me\s+)?lines|available\s+props|prop\s+board)\b", lowered):
        return "board", {}
    if re.search(r"\b(opposite\s+case|stress\s+test|bear\s+case|what\s+could\s+go\s+wrong)\b", lowered):
        return "opposite_case", {}
    if re.search(r"\b(refresh\s+lines?|line\s+movement|moved\s+the\s+line)\b", lowered):
        return "refresh_lines", {}
    if re.search(r"\b(postmortem|post\s*mortem|why\s+did\s+(this|that)\s+(miss|lose|hit))\b", lowered):
        return "postmortem", {}
    run = re.search(r"\b(?:run|analyze|analyse|check)\s+(.+?)(?:\s+for\s+.+)?$", text, re.IGNORECASE)
    if run and len(run.group(1).strip()) >= 3:
        return "run_player", {"player_query": run.group(1).strip()}
    return "general", {}


def _result(action: str, *, status: str, response: str, tools: list[dict[str, Any]], data: dict[str, Any] | None = None) -> dict[str, Any]:
    stage_names = [
        "fixture_discovery",
        "home_control_filter",
        "tactical_matchup_classification",
        "market_board_search",
        "candidate_ranking",
        "exact_role_venue_analysis",
        "bayesian_pipeline",
        "opposite_case_stress_test",
        "line_movement_check",
        "final_verdict",
    ]
    stage_status = {name: "UNKNOWN" for name in stage_names}
    for tool in tools:
        tool_name = str(tool.get("name") or "")
        if tool.get("status") == "available":
            if "fixture" in tool_name:
                stage_status["fixture_discovery"] = "available"
            if "board" in tool_name or "market" in tool_name:
                stage_status["market_board_search"] = "available"
    if action == "opposite_case":
        stage_status["opposite_case_stress_test"] = "partial"
    if action == "refresh_lines":
        stage_status["line_movement_check"] = "available" if stage_status["market_board_search"] == "available" else "UNKNOWN"
    if action == "postmortem":
        stage_status["final_verdict"] = "partial"
    return {
        "schema_version": "jarvis-conversation.v1",
        "action": action,
        "status": status,
        "read_only": True,
        "production_influence": False,
        "tools": tools,
        "data": data or {},
        "pipeline": [{"name": name, "status": stage_status[name]} for name in stage_names],
        "response": response,
        "provenance": {
            "orchestrator": "jarvis_orchestrator",
            "tool_results_are_read_only": True,
            "unavailable_evidence_is_unknown": True,
        },
    }


async def _safe_tool(name: str, call: Callable[[], Awaitable[Any]]) -> tuple[dict[str, Any], Any]:
    started = datetime.now(timezone.utc).isoformat()
    try:
        value = await call()
        status = "available" if value is not None else "UNKNOWN"
        return {"name": name, "status": status, "started_at": started}, value
    except Exception as exc:
        return {
            "name": name,
            "status": "UNKNOWN",
            "started_at": started,
            "reason": f"{type(exc).__name__} while running read-only tool",
        }, None


async def execute_action(
    message: str,
    *,
    context: dict[str, Any] | None,
    load_picks: Callable[[], Awaitable[list[dict[str, Any]]]],
    find_team: Callable[[str], Awaitable[dict[str, Any] | None]],
    fetch_fixtures: Callable[[int], Awaitable[list[dict[str, Any]]]],
    load_board: Callable[[], Awaitable[list[dict[str, Any]]]],
    load_memory: Callable[[], Awaitable[list[dict[str, Any]]]],
) -> dict[str, Any]:
    action, args = classify_action(message)
    tools: list[dict[str, Any]] = []
    data: dict[str, Any] = {"action_spec": ACTION_SPECS[action]}

    if action == "board":
        tool, board = await _safe_tool("search_market_board", load_board)
        tools.append(tool)
        rows = board if isinstance(board, list) else []
        data["markets"] = rows[:50]
        if rows:
            return _result(action, status="available", tools=tools,
                           data=data, response=f"I found {len(rows)} current player markets on the board. I can rank them against a specific fixture or player next.")
        return _result(action, status="UNKNOWN", tools=tools, data=data,
                       response="The market board is unavailable or returned no verified player lines right now. I won't invent a board entry.")

    if action == "script_hunt":
        query = re.sub(r"\b(script\s+hunt|hunt\s+the\s+slate)\b", "", message, flags=re.IGNORECASE).strip(" :,-")
        query = re.sub(r"^(?:for|against|on)\s+", "", query, flags=re.IGNORECASE).strip()
        team = None
        if query:
            team_tool, team = await _safe_tool("resolve_team", lambda: find_team(query))
            tools.append(team_tool)
        if team:
            fixture_tool, fixtures = await _safe_tool("discover_fixtures", lambda: fetch_fixtures(int(team.get("teamId"))))
            tools.append(fixture_tool)
        else:
            fixtures = []
        board_tool, board = await _safe_tool("search_market_board", load_board)
        tools.append(board_tool)
        data.update({
            "team": team,
            "fixtures": (fixtures or [])[:10],
            "home_control_filter": {"status": "UNKNOWN", "reason": "Requires a verified fixture and opponent context."},
            "tactical_matchup": {"status": "UNKNOWN", "reason": "Requires exact fixture lineup and tactical evidence."},
            "markets": (board or [])[:50] if isinstance(board, list) else [],
        })
        if team and fixtures:
            response = f"I found {len(fixtures[:10])} upcoming fixtures for {team.get('teamName', query)} and checked the available board. Home-control and tactical ranking still need an exact fixture selection before I issue a verdict."
            status = "partial"
        else:
            response = "Script Hunt is wired to fixture discovery and board search, but I need a recognizable team or a verified fixture to rank candidates. Nothing was guessed."
            status = "UNKNOWN"
        return _result(action, status=status, tools=tools, data=data, response=response)

    if action in {"opposite_case", "refresh_lines", "run_player", "postmortem"}:
        picks_tool, picks = await _safe_tool("read_owner_ledger", load_picks)
        tools.append(picks_tool)
        data["matching_picks"] = (picks or [])[:30] if isinstance(picks, list) else []
        if action == "opposite_case":
            packet = (context or {}).get("analysis") if isinstance(context, dict) else None
            data["opposite_case"] = (packet or {}).get("strongestOppositeCase") if isinstance(packet, dict) else None
            response = "I ran the read-only opposite-case stage against the available analysis. The strongest counterargument is shown in the structured result; missing stress-test evidence remains UNKNOWN."
        elif action == "refresh_lines":
            board_tool, board = await _safe_tool("refresh_market_lines", load_board)
            tools.append(board_tool)
            data["markets"] = (board or [])[:50] if isinstance(board, list) else []
            response = f"I refreshed the read-only market board and found {len(data['markets'])} current rows. I did not change a pick or treat a late line as pre-match evidence."
        elif action == "postmortem":
            memory_tool, memory = await _safe_tool("read_tactical_memory", load_memory)
            tools.append(memory_tool)
            data["postmortem_memory"] = (memory or [])[:30] if isinstance(memory, list) else []
            response = "I loaded the settled-pick context and advisory Tactical Memory for a postmortem. I will separate verified settlement facts from UNKNOWN causes."
        else:
            data["player_query"] = args.get("player_query")
            response = f"I started the read-only analysis workflow for {args.get('player_query')}. I need a verified player identity and fixture before producing a projection or verdict."
        return _result(action, status="partial", tools=tools, data=data, response=response)

    return _result(
        action,
        status="available",
        tools=[],
        data=data,
        response="I’m ready. Ask for Script Hunt, Board, Run a player, Opposite Case, Refresh Lines, or Postmortem, and I’ll run that named read-only workflow.",
    )