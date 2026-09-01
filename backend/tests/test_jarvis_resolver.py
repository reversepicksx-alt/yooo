from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

import routes.jarvis as jarvis


def _player_row(player_id=7, name=("Dominik", "Sallói"), team_id=100):
    return {
        "player": {
            "id": player_id,
            "name": f"{name[0]} {name[1]}",
            "firstname": name[0],
            "lastname": name[1],
        },
        "statistics": [{"team": {"id": team_id, "name": "Sporting KC"}}],
    }


def _fixture_row(status="NS", fixture_date="2026-08-23"):
    return {
        "fixture": {"id": 9001, "date": f"{fixture_date}T19:00:00+00:00", "status": {"short": status}},
        "teams": {
            "home": {"id": 200, "name": "Inter Miami"},
            "away": {"id": 100, "name": "Sporting KC"},
        },
        "league": {"id": 253, "name": "MLS", "season": 2026, "round": "Regular Season"},
    }


def test_resolver_returns_verified_ids_and_away_venue(monkeypatch):
    async def fake_get(endpoint, params, *, cache_ttl=0):
        if endpoint == "players":
            return {"response": [_player_row()]}
        if endpoint == "teams":
            return {"response": [{"team": {"id": 100, "name": "Sporting Kansas City"}}]}
        if endpoint == "fixtures":
            assert params == {"team": 100, "date": "2026-08-23"}
            return {"response": [_fixture_row()]}
        raise AssertionError(endpoint)

    monkeypatch.setattr(jarvis, "_sports_get", fake_get)
    result = asyncio.run(jarvis._resolve_soccer_prop_identity(
        player_name="Sallói", team="Sporting", opponent="Miami",
        requested_date="2026-08-23", season=2026,
    ))

    assert result["resolution"] == "verified"
    assert result["fixture_id"] == 9001
    assert result["player_id"] == 7
    assert result["team_id"] == 100
    assert result["opponent_id"] == 200
    assert result["league_id"] == 253
    assert result["venue"] == "away"
    assert result["season"] == 2026


def test_resolver_rejects_ambiguous_player(monkeypatch):
    async def fake_get(endpoint, params, *, cache_ttl=0):
        if endpoint == "players":
            return {"response": [_player_row(7), _player_row(8)]}
        if endpoint == "teams":
            return {"response": [{"team": {"id": 100, "name": "Sporting KC"}}]}
        raise AssertionError(endpoint)

    monkeypatch.setattr(jarvis, "_sports_get", fake_get)
    with pytest.raises(HTTPException) as error:
        asyncio.run(jarvis._resolve_soccer_prop_identity(
            player_name="Sallói",
            team="Sporting",
            requested_date="2026-08-23",
            season=2026,
        ))
    assert error.value.status_code == 422
    assert error.value.detail["status"] == "UNKNOWN"
    assert error.value.detail["reason"] == "player_ambiguous"


def test_resolver_never_uses_finished_fixture_without_date(monkeypatch):
    async def fake_get(endpoint, params, *, cache_ttl=0):
        if endpoint == "players":
            return {"response": [_player_row()]}
        if endpoint == "fixtures":
            assert params == {"team": 100, "next": 20}
            return {"response": [_fixture_row(status="FT", fixture_date="2026-08-01")]}
        raise AssertionError(endpoint)

    monkeypatch.setattr(jarvis, "_sports_get", fake_get)
    with pytest.raises(HTTPException) as error:
        asyncio.run(jarvis._resolve_soccer_prop_identity(
            player_name="Sallói", season=2026
        ))
    assert error.value.detail["status"] == "UNKNOWN"
    assert error.value.detail["reason"] == "fixture_not_found"