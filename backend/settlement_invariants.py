"""Canonical numeric settlement rules shared by every player-prop writer."""

from __future__ import annotations

from typing import Any


def line_allows_push(line: Any) -> bool:
    """Only whole-number player-prop lines can push."""
    try:
        return float(line).is_integer()
    except (TypeError, ValueError):
        return False


def settle_numeric_result(actual: Any, line: Any, recommendation: Any) -> str:
    """Return the only valid terminal numeric outcome for a saved pick.

    Half-lines can never push. Invalid numeric inputs raise rather than
    allowing a caller to persist a guessed terminal result.
    """
    actual_f = float(actual)
    line_f = float(line)
    rec = str(recommendation or "").strip().lower()
    if rec not in {"over", "under"}:
        raise ValueError("numeric settlement requires OVER or UNDER")
    if line_allows_push(line_f) and actual_f == line_f:
        return "push"
    if rec == "over":
        return "hit" if actual_f > line_f else "miss"
    return "hit" if actual_f < line_f else "miss"


def validate_numeric_outcome(
    actual: Any,
    line: Any,
    recommendation: Any,
    stored_result: Any,
) -> bool:
    """Check that a persisted terminal label matches its immutable inputs."""
    normalized = str(stored_result or "").strip().lower()
    if normalized in {"won", "lost"}:
        normalized = {"won": "hit", "lost": "miss"}[normalized]
    if normalized not in {"hit", "miss", "push"}:
        return False
    try:
        return settle_numeric_result(actual, line, recommendation) == normalized
    except (TypeError, ValueError):
        return False