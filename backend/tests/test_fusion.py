"""
Legacy tactical fusion test file kept only for deterministic policy assertions.
"""
import pytest
import sys

sys.path.insert(0, '/app/backend')


class TestDeterministicTacticalPolicy:
    def test_tactical_start_exposes_deterministic_policy(self):
        from backend.routes.tactical import tactical_start
        from models import ChatStartRequest
        import asyncio

        data = asyncio.run(tactical_start(ChatStartRequest()))
        assert "deterministic" in data["message"].lower()
        assert "unavailable" in data["message"].lower()

    def test_tactical_message_is_unavailable(self):
        from backend.routes.tactical import tactical_message
        from models import TacticalMessageRequest
        import asyncio

        data = asyncio.run(tactical_message(TacticalMessageRequest(session_id="tac-test", message="Explain this")))
        assert data["available"] is False
        assert data["response"].lower().startswith("tactical generation is unavailable")
        assert data["scanEntries"] == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
