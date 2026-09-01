"""
Deterministic policy assertions for the tactical route.
"""
import pytest
import sys
import os

# Add backend root to path so route modules resolve correctly
_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)


class TestDeterministicTacticalPolicy:
    def test_tactical_start_exposes_deterministic_policy(self):
        from routes.tactical import tactical_start
        from models import ChatStartRequest
        import asyncio

        data = asyncio.run(tactical_start(ChatStartRequest()))
        assert "deterministic" in data["message"].lower()

    def test_tactical_message_is_unavailable(self):
        from routes.tactical import tactical_message
        from models import TacticalMessageRequest
        import asyncio

        data = asyncio.run(tactical_message(TacticalMessageRequest(session_id="tac-test", message="Explain this")))
        assert data["available"] is False
        assert data["response"].lower().startswith("tactical generation is unavailable")
        assert data["scanEntries"] == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
