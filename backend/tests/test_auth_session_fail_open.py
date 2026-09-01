import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from routes import auth


def test_verify_session_stays_valid_when_last_active_write_is_blocked():
    session = {
        "email": "owner@example.com",
        "session_token": "session-token",
        "access_type": "Owner",
    }

    sessions = SimpleNamespace(
        find_one=AsyncMock(return_value=session),
        update_one=AsyncMock(side_effect=RuntimeError("Atlas write blocked")),
    )
    with patch.object(auth, "db", SimpleNamespace(sessions=sessions)):
        result = asyncio.run(
            auth.verify_session(
                {"email": "owner@example.com", "token": "session-token"}
            )
        )

    assert result == {"valid": True, "access_type": "Owner"}