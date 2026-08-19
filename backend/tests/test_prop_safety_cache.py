"""Regression tests for empirical prop-history aliasing."""

import asyncio

import prop_safety_cache
from prop_safety_cache import (
    canonical_prop_type,
    get_prop_safety,
    get_recent_prop_safety,
    refresh_prop_safety,
)


def test_pass_and_pass_attempts_share_the_settlement_history_key():
    assert canonical_prop_type("passes") == "pass_attempts"
    assert canonical_prop_type("PASS_ATTEMPTS") == "pass_attempts"
    assert canonical_prop_type("goalie_saves") == "saves"


def test_alias_lookup_uses_canonical_global_bucket(monkeypatch):
    bucket = {
        "hitRate": 62.4,
        "n": 1801,
        "wins": 1124,
        "losses": 677,
        "safety": "MODERATE",
        "recentHitRate": 62.5,
        "recentN": 759,
        "recentWins": 474,
        "recentLosses": 285,
    }
    monkeypatch.setattr(
        prop_safety_cache,
        "_CACHE",
        {"pass_attempts|UNDER": bucket},
    )

    assert get_prop_safety("passes", "under") is bucket
    assert get_recent_prop_safety("passes", "under")["n"] == 759


def test_refresh_merges_pass_aliases_before_building_buckets():
    class FakeAggregate:
        async def to_list(self, _limit):
            return [
                {
                    "_id": {
                        "propType": "pass_attempts",
                        "recommendation": "UNDER",
                        "date": "2026-08-01",
                    },
                    "propType": "pass_attempts",
                    "recommendation": "UNDER",
                    "win": 1,
                    "leagueId": 1,
                    "position": "midfielder",
                },
                {
                    "_id": {
                        "propType": "passes",
                        "recommendation": "UNDER",
                        "date": "2026-08-02",
                    },
                    "propType": "passes",
                    "recommendation": "UNDER",
                    "win": 0,
                    "leagueId": 1,
                    "position": "midfielder",
                },
            ]

    class FakePicks:
        def aggregate(self, _pipeline):
            return FakeAggregate()

    class FakeDb:
        picks = FakePicks()

    try:
        asyncio.run(refresh_prop_safety(FakeDb()))
        bucket = prop_safety_cache.get_all()["pass_attempts|UNDER"]
        assert bucket["n"] == 2
        assert bucket["wins"] == 1
        assert "passes|UNDER" not in prop_safety_cache.get_all()
    finally:
        prop_safety_cache._CACHE.clear()