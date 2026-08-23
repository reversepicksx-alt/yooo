"""Provider boundary for the owner-only JARVIS reasoning loop.

The language model is an orchestrator only.  It may select tools and synthesize
their results, but it never owns Reverse Picks calculations or persistence.
The adapter is intentionally opt-in because no external provider credential is
required for the deterministic fallback to operate.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import httpx


BRAIN_SCHEMA_VERSION = "jarvis-brain.v1"
DEFAULT_MODEL = "gpt-5.6-sol"
MAX_TOOL_ROUNDS = 8


class ResponsesAPIError(RuntimeError):
    """Safe, structured provider failure without credential-bearing details."""

    def __init__(self, *, diagnostics: dict[str, Any]) -> None:
        self.diagnostics = diagnostics
        super().__init__(
            f"Responses API HTTP {diagnostics.get('http_status', 'unknown')}: "
            f"{diagnostics.get('error_type') or 'provider_error'}"
        )


TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {"type": "function", "name": "discover_slate", "description": "Read the bounded upcoming verified soccer slate.", "parameters": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"type": "function", "name": "filter_home_control", "description": "Keep only verified home-side script candidates and record rejections.", "parameters": {"type": "object", "properties": {"fixtures": {"type": "array"}}, "required": ["fixtures"], "additionalProperties": False}},
    {"type": "function", "name": "research_tactical_matchup", "description": "Read tactical matchup evidence for an exact verified fixture.", "parameters": {"type": "object", "properties": {"fixture_id": {"type": "integer"}}, "required": ["fixture_id"], "additionalProperties": False}},
    {"type": "function", "name": "search_market_board", "description": "Read current player markets from the board.", "parameters": {"type": "object", "properties": {"fixture_ids": {"type": "array"}}, "additionalProperties": False}},
    {"type": "function", "name": "match_board_identities", "description": "Match board players to verified provider identities.", "parameters": {"type": "object", "properties": {"markets": {"type": "array"}}, "required": ["markets"], "additionalProperties": False}},
    {"type": "function", "name": "run_reverse_picks_analysis", "description": "Invoke deterministic Reverse Picks math for one exact player, fixture, prop, and line.", "parameters": {"type": "object", "properties": {"fixture_id": {"type": "integer"}, "player_id": {"type": "integer"}, "prop_type": {"type": "string"}, "line": {"type": "number"}}, "required": ["fixture_id", "player_id", "prop_type", "line"], "additionalProperties": False}},
    {"type": "function", "name": "check_line_movement", "description": "Read timestamped line movement; never treat an undated line as history.", "parameters": {"type": "object", "properties": {"market_key": {"type": "string"}}, "required": ["market_key"], "additionalProperties": False}},
    {"type": "function", "name": "stress_test_candidate", "description": "Run a read-only adversarial opposite-case check.", "parameters": {"type": "object", "properties": {"candidate": {"type": "object"}}, "required": ["candidate"], "additionalProperties": False}},
]


@dataclass
class BrainTurn:
    """Small persistent state envelope safe to store with an owner session."""

    response_id: str | None = None
    turns: list[dict[str, Any]] = field(default_factory=list)

    def snapshot(self) -> dict[str, Any]:
        return {
            "schema_version": BRAIN_SCHEMA_VERSION,
            "response_id": self.response_id,
            "turns": self.turns[-12:],
        }


@dataclass
class BrainEvent:
    kind: str
    name: str
    status: str
    detail: str | None = None

    def as_dict(self) -> dict[str, Any]:
        result = {"kind": self.kind, "name": self.name, "status": self.status}
        if self.detail:
            result["detail"] = self.detail
        return result


class ResponsesProvider:
    """Minimal OpenAI Responses API adapter; credentials are never logged."""

    def __init__(self, *, api_key: str, model: str = DEFAULT_MODEL) -> None:
        self.api_key = api_key
        self.model = model

    async def respond(
        self,
        *,
        instructions: str,
        input_items: list[dict[str, Any]],
        reasoning_effort: str,
        previous_response_id: str | None = None,
    ) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "instructions": instructions,
            "input": input_items,
            "tools": TOOL_DEFINITIONS,
            "reasoning": {"effort": reasoning_effort},
            "text": {"format": {"type": "json_object"}},
        }
        if previous_response_id:
            payload["previous_response_id"] = previous_response_id
        async with httpx.AsyncClient(timeout=35.0) as client:
            response = await client.post(
                "https://api.openai.com/v1/responses",
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                json=payload,
            )
            if response.is_error:
                try:
                    body = response.json()
                except (TypeError, ValueError):
                    body = {}
                error = body.get("error") if isinstance(body, dict) else {}
                error = error if isinstance(error, dict) else {}
                raise ResponsesAPIError(diagnostics={
                    "http_status": response.status_code,
                    "error_type": error.get("type"),
                    "error_code": error.get("code"),
                    "error_message": error.get("message"),
                    "request_id": response.headers.get("x-request-id"),
                    "model_sent": self.model,
                    "endpoint": "https://api.openai.com/v1/responses",
                    "authorization_present": bool(self.api_key),
                    "request_shape": {
                        "input_item_roles": [item.get("role") for item in input_items if isinstance(item, dict)],
                        "tool_names": [tool.get("name") for tool in TOOL_DEFINITIONS],
                        "reasoning_effort": reasoning_effort,
                        "text_format": "json_object",
                    },
                })
            return response.json()


def configured_provider() -> ResponsesProvider | None:
    if (os.environ.get("JARVIS_BRAIN_MODE") or "fallback").lower() not in {"openai", "responses", "enabled"}:
        return None
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    return ResponsesProvider(
        api_key=key,
        model=os.environ.get("JARVIS_OPENAI_MODEL", DEFAULT_MODEL),
    ) if key else None


def _response_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    items = payload.get("output")
    return items if isinstance(items, list) else []


async def run_reasoning_turn(
    message: str,
    *,
    state: BrainTurn,
    dispatch_tool: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]],
    on_event: Callable[[BrainEvent], Awaitable[None]] | None = None,
    reasoning_effort: str = "medium",
) -> dict[str, Any]:
    """Run provider tool calls, or return a clearly labeled fallback envelope."""
    provider = configured_provider()
    if provider is None:
        return {
            "schema_version": BRAIN_SCHEMA_VERSION,
            "provider": "deterministic-fallback",
            "status": "fallback",
            "reasoning_effort": reasoning_effort,
            "response_id": state.response_id,
            "events": [],
        }

    events: list[dict[str, Any]] = []

    async def emit(event: BrainEvent) -> None:
        events.append(event.as_dict())
        if on_event:
            await on_event(event)

    # Responses json_object mode requires an explicit JSON instruction in an
    # input message (instructions alone are not sufficient). Preserve the
    # bounded owner conversation state so the provider can orchestrate
    # follow-up turns without redefining verified identity.
    items: list[dict[str, Any]] = [
        item for item in state.turns[-12:]
        if isinstance(item, dict) and item.get("role") in {"user", "assistant"}
    ]
    items.append({
        "role": "user",
        "content": f"Return JSON. Owner request: {message}",
    })
    previous_response_id: str | None = None
    for _ in range(MAX_TOOL_ROUNDS):
        await emit(BrainEvent("progress", "reasoning", "running"))
        payload = await provider.respond(
            instructions=(
                "You are the owner-only JARVIS orchestrator. Select tools, do not "
                "calculate Reverse Picks math yourself, never invent unavailable evidence, "
                "and return JSON with response, verdict, candidates, rejections, and unknowns."
            ),
            input_items=items,
            reasoning_effort=reasoning_effort,
            previous_response_id=previous_response_id,
        )
        state.response_id = payload.get("id") or state.response_id
        previous_response_id = payload.get("id") or previous_response_id
        calls = [item for item in _response_items(payload) if item.get("type") == "function_call"]
        if not calls:
            output_text = payload.get("output_text")
            result = output_text if isinstance(output_text, str) else json.dumps(payload)
            state.turns.append({"role": "assistant", "content": result})
            return {
                "schema_version": BRAIN_SCHEMA_VERSION,
                "provider": "openai-responses",
                "status": "complete",
                "reasoning_effort": reasoning_effort,
                "response_id": state.response_id,
                "events": events,
                "result": result,
            }
        for call in calls:
            name = str(call.get("name") or "")
            try:
                arguments = json.loads(call.get("arguments") or "{}")
            except (TypeError, ValueError):
                arguments = {}
            await emit(BrainEvent("tool", name, "running"))
            tool_result = await dispatch_tool(name, arguments)
            await emit(BrainEvent("tool", name, str(tool_result.get("status") or "UNKNOWN")))
            items.append({"type": "function_call_output", "call_id": call.get("call_id"), "output": json.dumps(tool_result, default=str)})
    return {
        "schema_version": BRAIN_SCHEMA_VERSION,
        "provider": "openai-responses",
        "status": "UNKNOWN",
        "reason": "Maximum bounded tool rounds reached.",
        "response_id": state.response_id,
        "events": events,
    }