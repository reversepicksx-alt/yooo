import asyncio
import json

import pytest
from fastapi import HTTPException

from routes import jarvis


class _Cursor:
    def __init__(self, rows):
        self.rows = rows

    def sort(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    async def to_list(self, length=None):
        return self.rows[:length] if length else self.rows


class _Picks:
    def __init__(self, one=None, rows=None):
        self.one = one
        self.rows = rows or []

    async def find_one(self, *_args, **_kwargs):
        return self.one

    def find(self, *_args, **_kwargs):
        return _Cursor(self.rows)


class _DB:
    def __init__(self, picks):
        self.picks = picks


def _allow(monkeypatch):
    monkeypatch.setattr(jarvis, "_require_auth", lambda _auth: None)


def test_runtime_routes_require_auth(monkeypatch):
    monkeypatch.setattr(jarvis, "_JARVIS_KEY", "test-key")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            jarvis.jarvis_runtime_dominance_inputs(
                authorization=None, fixture_id=1, team_id=2
            )
        )
    assert exc.value.status_code == 401


def test_dominance_is_read_only_and_explicitly_unknown(monkeypatch):
    _allow(monkeypatch)
    monkeypatch.setattr(jarvis, "db", _DB(_Picks(one=None)))
    result = asyncio.run(
        jarvis.jarvis_runtime_dominance_inputs(
            authorization="Bearer test", fixture_id=10, team_id=20
        )
    )
    assert result["status"] == "UNKNOWN"
    assert result["inputs"] is None
    assert result["provenance"]["read_only"] is True


def test_prop_safety_exposes_loaded_bucket_without_refresh(monkeypatch):
    _allow(monkeypatch)
    monkeypatch.setattr(
        jarvis,
        "get_prop_safety",
        lambda *_args: {
            "hitRate": 82.0,
            "n": 11,
            "wins": 9,
            "losses": 2,
            "safety": "SAFE",
        },
    )
    result = asyncio.run(
        jarvis.jarvis_runtime_prop_safety(
            authorization="Bearer test",
            prop_type="passes",
            side="under",
            line=55.5,
            league_id=39,
            position="CM",
            role="single pivot",
        )
    )
    assert result["status"] == "available"
    assert result["query"]["canonical_prop_type"] == "pass_attempts"
    assert result["sample"]["n"] == 11
    assert result["decision"]["thresholds_are_data_derived"] is True


def test_calibration_rows_are_filtered_and_deduplicated(monkeypatch):
    _allow(monkeypatch)
    rows = [
        {
            "pickId": "same",
            "fixtureId": 1,
            "playerId": 2,
            "propType": "passes",
            "line": 55.5,
            "recommendation": "under",
            "result": "miss",
            "status": "settled",
            "leagueId": 39,
            "position": "CM",
            "role": "single pivot",
            "venue": "home",
            "modelVersion": "v1",
            "settledAt": "2026-08-20T00:00:00Z",
        },
        {
            "pickId": "same",
            "fixtureId": 1,
            "playerId": 2,
            "propType": "passes",
            "line": 55.5,
            "recommendation": "under",
            "result": "miss",
            "status": "settled",
            "leagueId": 39,
            "position": "CM",
            "role": "single pivot",
            "venue": "home",
            "modelVersion": "v1",
            "settledAt": "2026-08-20T00:00:00Z",
        },
        {
            "pickId": "excluded-push",
            "propType": "passes",
            "line": 55.5,
            "recommendation": "under",
            "result": "push",
            "status": "settled",
        },
    ]
    monkeypatch.setattr(jarvis, "db", _DB(_Picks(rows=rows)))
    result = asyncio.run(
        jarvis.jarvis_runtime_calibration_rows(
            authorization="Bearer test",
            prop_type="pass_attempts",
            direction="UNDER",
            line_band="55-70",
            league_id=39,
            position="CM",
            role="single pivot",
            venue="home",
            model_version="v1",
            date_from="2026-08-01",
            date_to="2026-08-31",
            limit=10,
        )
    )
    assert result["rows_returned"] == 1
    assert result["rows"][0]["canonicalPropType"] == "pass_attempts"
    assert result["rows"][0]["direction"] == "UNDER"
    assert result["provenance"]["read_only"] is True
    json.dumps(result)