"""Small, fail-soft recent-turn memory for the owner-only Lissa assistant.

This is intentionally not a general-purpose transcript store. It keeps only
the recent turns for the authenticated owner/session so follow-up questions can
resolve naturally without turning Lissa into a broad data-access surface.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from config import db

_COLLECTION = "lissa_turns"
_STATE_COLLECTION = "lissa_session_state"


async def load_session_state(email: str, session_id: str) -> dict[str, Any]:
    try:
        row = await db[_STATE_COLLECTION].find_one(
            {"email": str(email).lower(), "sessionId": session_id},
            {"_id": 0, "state": 1},
        )
        state = row.get("state") if isinstance(row, dict) else {}
        return state if isinstance(state, dict) else {}
    except Exception as exc:
        print(f"[LISSA MEMORY] state read skipped: {type(exc).__name__}: {exc}")
        return {}


async def save_session_state(email: str, session_id: str, state: dict[str, Any]) -> None:
    try:
        await db[_STATE_COLLECTION].update_one(
            {"email": str(email).lower(), "sessionId": session_id},
            {"$set": {"state": state, "updatedAt": datetime.now(timezone.utc)}},
            upsert=True,
        )
    except Exception as exc:
        print(f"[LISSA MEMORY] state write skipped: {type(exc).__name__}: {exc}")


async def load_recent_turns(email: str, session_id: str, limit: int = 6) -> list[dict[str, Any]]:
    try:
        rows = await (
            db[_COLLECTION]
            .find(
                {"email": str(email).lower(), "sessionId": session_id},
                {"_id": 0, "user": 1, "assistant": 1, "mode": 1, "screen": 1, "createdAt": 1},
            )
            .sort("createdAt", -1)
            .limit(limit)
            .to_list(limit)
        )
        rows.reverse()
        return rows
    except Exception as exc:
        # Memory is an enhancement, never a reason to block a read-only answer.
        print(f"[LISSA MEMORY] read skipped: {type(exc).__name__}: {exc}")
        return []


async def remember_turn(
    email: str,
    session_id: str,
    user_text: str,
    assistant_text: str,
    mode: str,
    screen: str | None,
) -> None:
    try:
        await db[_COLLECTION].insert_one(
            {
                "email": str(email).lower(),
                "sessionId": session_id,
                "user": user_text[:1200],
                "assistant": assistant_text[:4000],
                "mode": mode,
                "screen": screen,
                "createdAt": datetime.now(timezone.utc),
            }
        )
    except Exception as exc:
        # Atlas quota or a transient write failure must not break Lissa.
        print(f"[LISSA MEMORY] write skipped: {type(exc).__name__}: {exc}")