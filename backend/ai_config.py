"""Explicit opt-out switch for all paid AI provider calls.

Deterministic prediction, settlement, and owner analysis continue to work when
the switch is enabled. This module intentionally has no database dependency so
every optional provider boundary can check it safely.
"""

from __future__ import annotations

import os


def paid_ai_disabled() -> bool:
    """Return whether external paid AI requests are globally disabled."""
    return (os.environ.get("REVERSEPICKS_DISABLE_PAID_AI") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }