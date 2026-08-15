"""
Tests for knowledge_base Stage 2: heuristics seed integrity, fact-bundle
matching, miss-counter, and cache-version invalidation.

All tests that need async functions use asyncio.run() so they work with
standard pytest without the pytest-asyncio plugin.

Async tests that touch MongoDB mock get_player_kb / get_team_kb / db so no
Atlas connection is required.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from knowledge_base import (
    _HEURISTICS_SEED,
    _normalize_prop_for_kb,
    _role_matches,
    assemble_fact_bundle,
)


# ═══════════════════════════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════════════════════════

class _AsyncCursor:
    """Minimal async-iterable that yields a fixed list of dicts, with .limit()."""
    def __init__(self, items):
        self._items = list(items)

    def limit(self, n):
        self._items = self._items[:n]
        return self

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._items:
            raise StopAsyncIteration
        return self._items.pop(0)


def _run(coro):
    """Synchronous helper so plain pytest can run async tests."""
    return asyncio.run(coro)


# ═══════════════════════════════════════════════════════════════════════════════
#  Seed integrity
# ═══════════════════════════════════════════════════════════════════════════════

def test_seed_count_is_thirty():
    assert len(_HEURISTICS_SEED) == 30, (
        f"Expected exactly 30 seed rules, got {len(_HEURISTICS_SEED)}"
    )


def test_no_duplicate_seed_keys():
    keys = [
        f"{r['role']}|{r['opponentStyleTag']}|{r['prop']}|{r['direction']}"
        for r in _HEURISTICS_SEED
    ]
    dups = [k for k in set(keys) if keys.count(k) > 1]
    assert not dups, f"Duplicate seed keys: {dups}"


def test_all_seed_rules_have_required_fields():
    required = {"role", "opponentStyleTag", "prop", "direction", "deltaPercent", "confidence", "note"}
    for i, rule in enumerate(_HEURISTICS_SEED):
        missing = required - set(rule.keys())
        assert not missing, f"Rule #{i} missing fields: {missing} — rule={rule}"


def test_seed_directions_are_valid():
    for r in _HEURISTICS_SEED:
        assert r["direction"] in ("OVER", "UNDER"), (
            f"Invalid direction {r['direction']!r} in rule {r}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
#  Goalkeeper saves — directional consistency
# ═══════════════════════════════════════════════════════════════════════════════

def test_goalkeeper_saves_high_possession_tags_are_under():
    """
    high_line and possession_dominant both indicate the opponent has ≥55%
    possession.  Both must predict UNDER saves for the goalkeeper — otherwise
    the same team would trigger contradictory directions.
    """
    high_poss_tags = {"possession_dominant", "high_line"}
    gk_saves = [
        r for r in _HEURISTICS_SEED
        if r["role"] == "Goalkeeper" and r["prop"] == "saves"
        and r["opponentStyleTag"] in high_poss_tags
    ]
    for rule in gk_saves:
        assert rule["direction"] == "UNDER", (
            f"Goalkeeper × {rule['opponentStyleTag']} → saves must be UNDER "
            f"(high-possession opponent keeps ball away from goal), "
            f"got {rule['direction']!r}"
        )


def test_goalkeeper_saves_low_possession_tags_are_over():
    """
    deep_block and counter_attacking both indicate the opponent has ≤42%
    possession.  Both must predict OVER saves — otherwise the same team
    triggers contradictory directions.
    """
    low_poss_tags = {"counter_attacking", "deep_block"}
    gk_saves = [
        r for r in _HEURISTICS_SEED
        if r["role"] == "Goalkeeper" and r["prop"] == "saves"
        and r["opponentStyleTag"] in low_poss_tags
    ]
    for rule in gk_saves:
        assert rule["direction"] == "OVER", (
            f"Goalkeeper × {rule['opponentStyleTag']} → saves must be OVER "
            f"(low-possession / counter-attacking opponent creates more direct shots), "
            f"got {rule['direction']!r}"
        )


def test_inverted_winger_shots_deep_block_and_counter_consistent():
    """
    deep_block and counter_attacking both apply to ≤42%-possession opponents.
    Inverted Winger × shots must have the same direction for both.
    """
    iw_shots = {
        r["opponentStyleTag"]: r["direction"]
        for r in _HEURISTICS_SEED
        if r["role"] == "Inverted Winger" and r["prop"] == "shots"
    }
    if "deep_block" in iw_shots and "counter_attacking" in iw_shots:
        assert iw_shots["deep_block"] == iw_shots["counter_attacking"], (
            f"Inverted Winger shots: deep_block direction ({iw_shots['deep_block']!r}) "
            f"contradicts counter_attacking ({iw_shots['counter_attacking']!r}). "
            f"Both tags apply to the same ≤42%-possession opponent."
        )


# ═══════════════════════════════════════════════════════════════════════════════
#  _normalize_prop_for_kb
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("raw,expected", [
    ("pass_attempts",   "pass_attempts"),
    ("passes",          "pass_attempts"),
    ("PASS_ATTEMPTS",   "pass_attempts"),
    ("saves",           "saves"),
    ("Saves",           "saves"),
    ("shots_on_target", "shots"),
    ("shots",           "shots"),
    ("key_passes",      "key_passes"),
    ("key passes",      "key_passes"),
    ("tackles",         "tackles"),
    ("clearances",      "clearances"),
])
def test_normalize_prop(raw, expected):
    assert _normalize_prop_for_kb(raw) == expected


# ═══════════════════════════════════════════════════════════════════════════════
#  _role_matches
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("heuristic_role,player_role,should_match", [
    # universal wildcard
    ("any",               "Ball-playing CB",          True),
    ("",                  "Ball-playing CB",          True),
    # exact
    ("Ball-playing CB",   "Ball-playing CB",          True),
    ("Ball-playing CB",   "Ball-Playing CB",          True),   # case insensitive
    # substring
    ("Anchor",            "Anchor/CDM",               True),
    ("Anchor",            "Deep-Lying Anchor",        True),
    ("Box-to-Box",        "Box-to-Box Midfielder",    True),
    ("Pressing Forward",  "Pressing Forward / False 9", True),
    ("False 9",           "False 9",                  True),
    ("Inverted Winger",   "Left Inverted Winger",     True),
    ("Goalkeeper",        "Goalkeeper",               True),
    # non-match
    ("Pressing CB",       "Ball-playing CB",          False),
    ("Ball-playing CB",   "Goalkeeper",               False),
    ("False 9",           "Pressing Forward",         False),
])
def test_role_matches(heuristic_role, player_role, should_match):
    result = _role_matches(heuristic_role, player_role)
    assert result == should_match, (
        f"_role_matches({heuristic_role!r}, {player_role!r}) = {result}, "
        f"expected {should_match}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  assemble_fact_bundle — miss path
# ═══════════════════════════════════════════════════════════════════════════════

def test_assemble_fact_bundle_miss_when_no_data():
    """Returns hit=False and bumps the miss counter when KB has no data."""

    async def _run_test():
        with (
            patch("knowledge_base.get_player_kb", new=AsyncMock(return_value=None)),
            patch("knowledge_base.get_team_kb",   new=AsyncMock(return_value=None)),
            patch("knowledge_base.db") as mock_db,
        ):
            mock_db.knowledge_stats.update_one = AsyncMock()

            result = await assemble_fact_bundle(
                player_id=1, team_id=2, opponent_id=3,
                prop_type="pass_attempts", venue="home", league_id=10,
            )
            return result, mock_db.knowledge_stats.update_one.call_count

    result, miss_calls = _run(_run_test())
    assert result["hit"] is False
    assert result["version"] == "no_bundle"
    assert result["text"] == ""
    assert miss_calls == 1, "Miss counter must be incremented exactly once"


# ═══════════════════════════════════════════════════════════════════════════════
#  assemble_fact_bundle — hit path
# ═══════════════════════════════════════════════════════════════════════════════

_PLAYER_KB = {
    "specificPosition": "CB",
    "role": "Ball-playing CB",
    "passesPer90": 55.0,
    "appearances": 20,
    "homePassAvg": 58.0,
    "awayPassAvg": 48.0,
    "tendencies": {"highVolumePasser": True},
}
_OPPONENT_KB = {
    "buildUpStyle": "counter_attacking",
    "defensiveLineHeight": "deep_block",
    "seasonAvgPoss": 40.0,
}


def test_assemble_fact_bundle_hit():
    """Returns hit=True when player KB is available."""

    async def _run_test():
        with (
            patch("knowledge_base.get_player_kb", new=AsyncMock(return_value=_PLAYER_KB)),
            patch("knowledge_base.get_team_kb",   new=AsyncMock(return_value=_OPPONENT_KB)),
            patch("knowledge_base.db") as mock_db,
        ):
            mock_db.knowledge_heuristics.find.return_value = _AsyncCursor([])
            return await assemble_fact_bundle(
                player_id=1, team_id=2, opponent_id=3,
                prop_type="pass_attempts", venue="home", league_id=10,
            )

    result = _run(_run_test())
    assert result["hit"] is True
    assert result["version"] not in ("no_bundle", "error", "")
    assert "Ball-playing CB" in result["text"]
    assert "counter_attacking" in result["text"]


def test_assemble_fact_bundle_version_deterministic():
    """Same KB data produces the same version hash on repeated calls."""

    async def _run_test():
        with (
            patch("knowledge_base.get_player_kb", new=AsyncMock(return_value=_PLAYER_KB)),
            patch("knowledge_base.get_team_kb",   new=AsyncMock(return_value=_OPPONENT_KB)),
            patch("knowledge_base.db") as mock_db,
        ):
            mock_db.knowledge_heuristics.find.return_value = _AsyncCursor([])
            r1 = await assemble_fact_bundle(
                player_id=1, team_id=2, opponent_id=3,
                prop_type="pass_attempts", venue="home", league_id=10,
            )
            mock_db.knowledge_heuristics.find.return_value = _AsyncCursor([])
            r2 = await assemble_fact_bundle(
                player_id=1, team_id=2, opponent_id=3,
                prop_type="pass_attempts", venue="home", league_id=10,
            )
        return r1["version"], r2["version"]

    v1, v2 = _run(_run_test())
    assert v1 == v2, "Same inputs must produce identical version hash"


def test_version_changes_when_appearances_changes():
    """Appearances is rendered in the bundle text; changing it must invalidate cache."""

    async def _make_result(appearances):
        pkb = {**_PLAYER_KB, "appearances": appearances}
        with (
            patch("knowledge_base.get_player_kb", new=AsyncMock(return_value=pkb)),
            patch("knowledge_base.get_team_kb",   new=AsyncMock(return_value=_OPPONENT_KB)),
            patch("knowledge_base.db") as mock_db,
        ):
            mock_db.knowledge_heuristics.find.return_value = _AsyncCursor([])
            return await assemble_fact_bundle(
                player_id=1, team_id=2, opponent_id=3,
                prop_type="pass_attempts", venue="home", league_id=10,
            )

    r1 = _run(_make_result(10))
    r2 = _run(_make_result(25))
    assert r1["version"] != r2["version"], (
        "Version hash must change when appearances changes "
        f"(got {r1['version']!r} for both)"
    )


def test_version_changes_when_heuristic_note_changes():
    """
    Changing a heuristic's note/version must produce a different version hash
    so stale AI cache entries are not reused with outdated rule content.
    """
    base_heuristic = {
        "_key": "Goalkeeper|possession_dominant|saves|UNDER",
        "role": "Goalkeeper", "opponentStyleTag": "possession_dominant",
        "prop": "saves", "direction": "UNDER", "deltaPercent": -15.0,
        "confidence": "high", "note": "Original note text", "version": 1,
    }
    updated_heuristic = {**base_heuristic, "note": "Updated note v2", "version": 2}

    gk_kb = {
        "specificPosition": "GK", "role": "Goalkeeper",
        "passesPer90": 30.0, "appearances": 15,
        "homePassAvg": None, "awayPassAvg": None, "tendencies": {},
    }
    opp_kb = {
        "buildUpStyle": "possession_dominant",
        "defensiveLineHeight": "high_line",
        "seasonAvgPoss": 60.0,
    }

    async def _make_result(h):
        with (
            patch("knowledge_base.get_player_kb", new=AsyncMock(return_value=gk_kb)),
            patch("knowledge_base.get_team_kb",   new=AsyncMock(return_value=opp_kb)),
            patch("knowledge_base.db") as mock_db,
        ):
            mock_db.knowledge_heuristics.find.return_value = _AsyncCursor([h])
            return await assemble_fact_bundle(
                player_id=1, team_id=2, opponent_id=3,
                prop_type="saves", venue="home", league_id=10,
            )

    r1 = _run(_make_result(base_heuristic))
    r2 = _run(_make_result(updated_heuristic))
    assert r1["version"] != r2["version"], (
        "Version hash must change when heuristic note or version changes"
    )


def test_matched_heuristic_appears_in_bundle_text():
    """A matched heuristic's note and direction must appear in the rendered text."""
    heuristic = {
        "_key": "Ball-playing CB|counter_attacking|pass_attempts|UNDER",
        "role": "Ball-playing CB", "opponentStyleTag": "counter_attacking",
        "prop": "pass_attempts", "direction": "UNDER", "deltaPercent": -12.0,
        "confidence": "medium",
        "note": "Counter-attacks bypass CBs; time-on-ball shrinks",
        "version": 1,
    }

    async def _run_test():
        with (
            patch("knowledge_base.get_player_kb", new=AsyncMock(return_value=_PLAYER_KB)),
            patch("knowledge_base.get_team_kb",   new=AsyncMock(return_value=_OPPONENT_KB)),
            patch("knowledge_base.db") as mock_db,
        ):
            mock_db.knowledge_heuristics.find.return_value = _AsyncCursor([heuristic])
            return await assemble_fact_bundle(
                player_id=1, team_id=2, opponent_id=3,
                prop_type="pass_attempts", venue="home", league_id=10,
            )

    result = _run(_run_test())
    assert "Counter-attacks bypass CBs" in result["text"]
    assert "UNDER" in result["text"]
