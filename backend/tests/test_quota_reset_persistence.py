"""Tests for quota reset timestamp persistence across server restarts.

Covers:
  - admin_quota_reset writes the reset timestamp to the database
  - admin_quota_status recovers the timestamp from the database when
    in-memory state (_last_quota_reset_at) is None (simulates a restart)
  - A DB write failure is surfaced in the response (not silently swallowed)
"""
import types
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone


# ── helpers ─────────────────────────────────────────────────────────────────

def _make_utils_stub(reset_at=None):
    """Return a minimal utils stub with just the attributes admin.py touches."""
    stub = types.SimpleNamespace(
        _quota_exhausted_date=None,
        _daily_call_count=0,
        _last_quota_reset_at=reset_at,
        _RESET_TIMESTAMP_FILE="/tmp/.api_sports_quota_last_reset_test",
    )
    stub.is_quota_exhausted = lambda: False
    stub._load_reset_timestamp_from_disk = lambda: None
    return stub


def _make_db_stub(stored_value=None):
    """Return a minimal db stub whose settings collection behaves like Atlas."""
    settings = MagicMock()
    settings.find_one = AsyncMock(
        return_value={"key": "QUOTA_LAST_RESET_AT", "value": stored_value}
        if stored_value else None
    )
    settings.update_one = AsyncMock(return_value=MagicMock(upserted_id="fake_id"))
    db = MagicMock()
    db.settings = settings
    return db


# ── quota-reset: DB write is called ─────────────────────────────────────────

def test_quota_reset_writes_timestamp_to_db():
    """admin_quota_reset must upsert the reset timestamp into db.settings."""
    import importlib, sys, os
    db_stub = _make_db_stub()
    utils_stub = _make_utils_stub()

    # Patch the /tmp breaker so 'existed' is False (no file to remove)
    with patch("os.path.exists", return_value=False), \
         patch("routes.admin.db", db_stub), \
         patch.dict(sys.modules, {"utils": utils_stub}):
        from routes.admin import admin_quota_reset
        req = types.SimpleNamespace(email="owner@example.com", token="tok")

        async def run():
            with patch("routes.admin.verify_owner", new=AsyncMock()):
                return await admin_quota_reset(req)

        result = asyncio.get_event_loop().run_until_complete(run())

    # DB upsert must have been called
    db_stub.settings.update_one.assert_awaited_once()
    call_kwargs = db_stub.settings.update_one.call_args
    # Second positional arg is the $set payload
    set_doc = call_kwargs[0][1]["$set"]
    assert set_doc["key"] == "QUOTA_LAST_RESET_AT"
    assert set_doc["value"] == result["resetAt"]
    assert "persistenceWarning" not in result


def test_quota_reset_surfaces_db_write_failure():
    """A failed DB write must produce a 'persistenceWarning' in the response."""
    import sys
    db_stub = _make_db_stub()
    db_stub.settings.update_one = AsyncMock(side_effect=Exception("Atlas write blocked"))
    utils_stub = _make_utils_stub()

    with patch("os.path.exists", return_value=False), \
         patch("routes.admin.db", db_stub), \
         patch.dict(sys.modules, {"utils": utils_stub}):
        from routes.admin import admin_quota_reset
        req = types.SimpleNamespace(email="owner@example.com", token="tok")

        async def run():
            with patch("routes.admin.verify_owner", new=AsyncMock()):
                return await admin_quota_reset(req)

        result = asyncio.get_event_loop().run_until_complete(run())

    assert "persistenceWarning" in result, (
        "A DB write failure must produce a 'persistenceWarning' key in the reset response"
    )
    assert "Atlas write blocked" in result["persistenceWarning"]
    # The reset timestamp is still returned (in-memory path worked)
    assert result["resetAt"]


# ── quota-status: DB fallback when in-memory is None ────────────────────────

def test_quota_status_recovers_timestamp_from_db_after_restart():
    """After a restart _last_quota_reset_at is None; status must read from DB."""
    import sys
    stored_ts = "2026-08-09T10:00:00+00:00"
    db_stub = _make_db_stub(stored_value=stored_ts)
    utils_stub = _make_utils_stub(reset_at=None)  # simulates cleared in-memory state

    with patch("routes.admin.db", db_stub), \
         patch.dict(sys.modules, {"utils": utils_stub}):
        from routes.admin import admin_quota_status

        async def run():
            with patch("routes.admin.verify_owner", new=AsyncMock()):
                return await admin_quota_status(
                    email="owner@example.com", token="tok"
                )

        result = asyncio.get_event_loop().run_until_complete(run())

    assert result["lastResetAt"] == stored_ts, (
        "quota-status must fall back to the DB value when in-memory is None"
    )
    # In-memory should now be populated to avoid future DB reads
    assert utils_stub._last_quota_reset_at == stored_ts


def test_quota_status_prefers_in_memory_over_db():
    """If _last_quota_reset_at is set in memory the DB must not be queried."""
    import sys
    mem_ts = "2026-08-09T12:00:00+00:00"
    db_stub = _make_db_stub(stored_value="2026-08-01T00:00:00+00:00")
    utils_stub = _make_utils_stub(reset_at=mem_ts)

    with patch("routes.admin.db", db_stub), \
         patch.dict(sys.modules, {"utils": utils_stub}):
        from routes.admin import admin_quota_status

        async def run():
            with patch("routes.admin.verify_owner", new=AsyncMock()):
                return await admin_quota_status(
                    email="owner@example.com", token="tok"
                )

        result = asyncio.get_event_loop().run_until_complete(run())

    assert result["lastResetAt"] == mem_ts
    db_stub.settings.find_one.assert_not_awaited()
