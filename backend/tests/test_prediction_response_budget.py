"""Response-budget contract tests for the user-triggered prediction path."""

from __future__ import annotations

import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from routes.predict import _run_bounded_prediction_source


def test_hanging_provider_returns_fallback_within_deadline():
    async def hanging_provider():
        await asyncio.sleep(30)
        return {"unexpected": True}

    async def exercise():
        started = time.monotonic()
        result = await _run_bounded_prediction_source(
            hanging_provider(),
            started=started,
            budget=0.08,
            timeout=30.0,
            fallback={"status": "limited"},
            label="hanging test provider",
        )
        return result, time.monotonic() - started

    result, elapsed = asyncio.run(exercise())
    assert result == {"status": "limited"}
    assert elapsed < 0.5


def test_expired_budget_does_not_start_a_cache_operation():
    started_operation = False

    async def hanging_cache():
        nonlocal started_operation
        started_operation = True
        await asyncio.sleep(30)
        return {"unexpected": True}

    async def exercise():
        return await _run_bounded_prediction_source(
            hanging_cache(),
            started=time.monotonic() - 1.0,
            budget=0.1,
            timeout=30.0,
            fallback=[],
            label="expired test cache",
        )

    assert asyncio.run(exercise()) == []
    assert started_operation is False


def test_late_wave_two_source_uses_only_remaining_budget():
    async def hanging_wave_two_source():
        await asyncio.sleep(30)
        return {"unexpected": True}

    async def exercise():
        started = time.monotonic() - 0.07
        return await _run_bounded_prediction_source(
            hanging_wave_two_source(),
            started=started,
            budget=0.1,
            timeout=8.0,
            fallback=None,
            label="late Wave 2 source",
        )

    assert asyncio.run(exercise()) is None