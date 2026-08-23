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


def _result(action: str, *, status: str, response: str, tools: list[dict[str, Any]],
            data: dict[str, Any] | None = None,
            stage_overrides: dict[str, str] | None = None) -> dict[str, Any]:
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
    stage_status.update(stage_overrides or {})
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
    discover_slate: Callable[[], Awaitable[list[dict[str, Any]]]],
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
        # Script Hunt is intentionally autonomous: the exact command has no
        # required team/player/fixture input. Discovery is the first tool.
        fixture_tool, fixtures = await _safe_tool("discover_slate", discover_slate)
        tools.append(fixture_tool)
        board_tool, board = await _safe_tool("search_market_board", load_board)
        tools.append(board_tool)
        rows = fixtures if isinstance(fixtures, list) else []
        candidates: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        for fixture in rows[:100]:
            fixture_data = fixture.get("fixture") if isinstance(fixture, dict) else {}
            teams = fixture.get("teams") if isinstance(fixture, dict) else {}
            home = teams.get("home") if isinstance(teams, dict) else {}
            away = teams.get("away") if isinstance(teams, dict) else {}
            if not isinstance(home, dict) or not home.get("id"):
                rejected.append({"fixture": fixture, "reason": "missing_verified_home_identity"})
                continue
            item = {
                "fixtureId": (fixture_data or {}).get("id"),
                "homeTeam": {"id": home.get("id"), "name": home.get("name")},
                "awayTeam": {"id": away.get("id"), "name": away.get("name")} if isinstance(away, dict) else {},
                "homeControl": {"status": "UNKNOWN", "reason": "Requires possession, shape, and opponent-control evidence."},
                "scriptStrength": "UNKNOWN",
            }
            # Only home-side candidates enter Script Hunt. Away-control
            # scenarios are retained as transparent rejections, never ranked.
            if away.get("id") if isinstance(away, dict) else False:
                candidates.append(item)
            else:
                rejected.append({"fixture": fixture, "reason": "missing_verified_away_identity"})
        data.update({
            "slate": rows[:100],
            "fixtures": candidates[:30],
            "rejectedFixtures": rejected[:30],
            "home_control_filter": {"status": "partial", "kept": len(candidates), "rejected": len(rejected)},
            "tactical_matchup": {"status": "UNKNOWN", "reason": "Requires exact fixture lineup and tactical evidence."},
            "markets": (board or [])[:50] if isinstance(board, list) else [],
        })
        board_rows = board if isinstance(board, list) else []
        candidate_ids = {item.get("fixtureId") for item in candidates if item.get("fixtureId") is not None}
        candidate_team_ids = {
            team.get("id")
            for item in candidates
            for team in (item.get("homeTeam", {}), item.get("awayTeam", {}))
            if isinstance(team, dict) and team.get("id") is not None
        }
        board_candidates: list[dict[str, Any]] = []
        rejected_markets: list[dict[str, Any]] = []
        for market in board_rows[:100]:
            if not isinstance(market, dict):
                continue
            market_fixture = market.get("fixtureId") or market.get("eventId") or market.get("gameId")
            market_team = market.get("teamId") or market.get("playerTeamId")
            if market_fixture in candidate_ids or market_team in candidate_team_ids:
                board_candidates.append(market)
            else:
                rejected_markets.append({"market": market, "reason": "no_verified_qualifying_fixture_match"})
        data["boardCandidates"] = board_candidates[:30]
        data["rejectedMarkets"] = rejected_markets[:30]
        data["candidateRanking"] = [
            {"fixtureId": item.get("fixtureId"), "marketCount": sum(
                1 for market in board_candidates
                if market.get("fixtureId") == item.get("fixtureId")
            ), "scriptStrength": item.get("scriptStrength", "UNKNOWN")}
            for item in candidates[:30]
        ]
        if candidates:
            response = f"I discovered {len(rows)} upcoming fixtures, kept {len(candidates)} home-team Script Hunt candidates, rejected {len(rejected)} incomplete/away-side cases, and checked the current board. Tactical, role, Bayesian, and adversarial stages remain UNKNOWN until evidence is available."
            status = "partial" if board_tool["status"] != "UNKNOWN" else "partial"
        elif fixture_tool["status"] == "UNKNOWN":
            response = "I started Script Hunt, but the verified fixture provider is temporarily unavailable. I checked the board independently, kept fixture-dependent stages UNKNOWN, and did not guess candidates."
            status = "UNKNOWN"
        else:
            response = "I discovered the current slate and checked the board, but no fixture had both verified home and away identities for a safe Script Hunt candidate. Nothing was guessed."
            status = "UNKNOWN"
        return _result(action, status=status, tools=tools, data=data, response=response,
                       stage_overrides={
                           "fixture_discovery": "available" if fixture_tool["status"] == "available" else "UNKNOWN",
                           "home_control_filter": "partial",
                           "market_board_search": "available" if board_tool["status"] == "available" else "UNKNOWN",
                           "candidate_ranking": "partial" if candidates else "UNKNOWN",
                           "final_verdict": "partial" if candidates else "UNKNOWN",
                       })

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