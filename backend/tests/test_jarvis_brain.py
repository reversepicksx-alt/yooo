from __future__ import annotations

import asyncio
import json

from jarvis_brain import (
    BRAIN_SCHEMA_VERSION,
    BrainTurn,
    TOOL_DEFINITIONS,
    configured_provider,
    run_reasoning_turn,
)


def test_tool_catalog_is_typed_and_deterministic_math_is_a_tool():
    names = {item["name"] for item in TOOL_DEFINITIONS}
    assert "discover_slate" in names
    assert "run_reverse_picks_analysis" in names
    assert all(item["parameters"]["type"] == "object" for item in TOOL_DEFINITIONS)


def test_provider_absence_returns_safe_fallback(monkeypatch):
    monkeypatch.delenv("JARVIS_BRAIN_MODE", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    state = BrainTurn()
    result = asyncio.run(run_reasoning_turn(
        "Script hunt", state=state, dispatch_tool=lambda *_: None,
    ))
    assert result["schema_version"] == BRAIN_SCHEMA_VERSION
    assert result["provider"] == "deterministic-fallback"
    assert result["status"] == "fallback"


def test_global_paid_ai_opt_out_blocks_openai_provider(monkeypatch):
    monkeypatch.setenv("JARVIS_BRAIN_MODE", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    monkeypatch.setenv("REVERSEPICKS_DISABLE_PAID_AI", "true")
    assert configured_provider() is None


def test_provider_tool_calls_continue_and_emit_events(monkeypatch):
    class FakeProvider:
        model = "test"
        calls = 0

        async def respond(self, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                return {
                    "id": "resp_1",
                    "output": [{
                        "type": "function_call",
                        "name": "discover_slate",
                        "call_id": "call_1",
                        "arguments": "{}",
                    }],
                }
            return {"id": "resp_2", "output_text": json.dumps({
                "response": "Slate reviewed", "verdict": "partial",
            })}

    fake = FakeProvider()
    monkeypatch.setenv("JARVIS_BRAIN_MODE", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    monkeypatch.setattr("jarvis_brain.configured_provider", lambda: fake)
    seen = []

    async def dispatch(name, arguments):
        seen.append((name, arguments))
        return {"status": "available", "fixtures": []}

    async def on_event(event):
        seen.append(event.as_dict())

    state = BrainTurn()
    result = asyncio.run(run_reasoning_turn(
        "Script hunt", state=state, dispatch_tool=dispatch, on_event=on_event,
        reasoning_effort="high",
    ))
    assert result["provider"] == "openai-responses"
    assert result["status"] == "complete"
    assert state.response_id == "resp_2"
    assert ("discover_slate", {}) in seen
    assert any(item.get("kind") == "tool" and item.get("status") == "available" for item in seen if isinstance(item, dict))