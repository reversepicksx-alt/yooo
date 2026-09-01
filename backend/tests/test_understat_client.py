"""Focused contracts for the supplemental Understat pressure layer."""
from __future__ import annotations

import asyncio
import os
import sys
import time
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import understat_client as understat
from tactical_intelligence import build_tactical_explanation


def _match(*, h_a: str, ppda: tuple[int, int] | None, opp: tuple[int, int] | None, date: str):
    return {
        "h_a": h_a,
        "date": date,
        "ppda": {"att": ppda[0], "def": ppda[1]} if ppda else None,
        "ppda_allowed": {"att": opp[0], "def": opp[1]} if opp else None,
        "xG": 1.2,
        "xGA": 1.0,
        "npxG": 1.1,
        "npxGA": 0.9,
    }


def test_understat_summary_uses_ppda_orientation_and_keeps_missing_values_unknown():
    summary = understat._summary([
        _match(h_a="h", ppda=(80, 10), opp=(120, 20), date="2026-05-01"),
        _match(h_a="a", ppda=None, opp=None, date="2026-05-08"),
    ])
    assert summary["ppda"] == 8.0
    assert summary["oppPpda"] == 6.0
    assert summary["sampleSize"] == 2

    empty = understat._summary([_match(h_a="h", ppda=None, opp=None, date="2026-05-01")])
    assert empty["ppda"] is None
    assert empty["oppPpda"] is None


def test_understat_team_matching_handles_verified_name_aliases():
    teams = {
        "1": {"id": "1", "title": "Sevilla", "history": []},
        "2": {"id": "2", "title": "Rayo Vallecano", "history": []},
    }
    assert understat._find_team(teams, "Sevilla FC")["id"] == "1"
    assert understat._find_team(teams, "Rayo Vallecano de Madrid")["id"] == "2"


def test_understat_pressure_packet_inverts_fixture_venue_and_preserves_provenance(monkeypatch):
    payload = {
        "teams": {
            "1": {
                "id": "1",
                "title": "Sevilla",
                "history": [
                    _match(h_a="h", ppda=(80, 10), opp=(120, 20), date="2026-05-01"),
                    _match(h_a="a", ppda=(180, 20), opp=(100, 10), date="2026-05-08"),
                ],
            },
            "2": {
                "id": "2",
                "title": "Rayo Vallecano",
                "history": [
                    _match(h_a="h", ppda=(100, 10), opp=(150, 20), date="2026-05-01"),
                    _match(h_a="a", ppda=(120, 20), opp=(180, 20), date="2026-05-08"),
                ],
            },
        }
    }

    async def fake_load(slug, season):
        assert slug == "La_liga"
        assert season == 2025
        return payload

    monkeypatch.setattr(understat, "_read_cached_league", fake_load)
    packet = asyncio.run(understat.fetch_understat_pressure_context(
        league_id=140,
        season=2025,
        team_name="Rayo Vallecano",
        opponent_name="Sevilla FC",
        venue="away",
        as_of="2026-08-15T00:00:00Z",
    ))

    assert packet["status"] == "verified_team_level"
    assert packet["availability"] == "available"
    assert packet["source"] == "understat"
    assert packet["venue"] == "away"
    assert packet["opponentPress"]["venue"] == "home"
    assert packet["opponentPress"]["ppda"] == 8.0
    assert packet["team"]["venue"]["ppda"] == 6.0
    assert packet["opponent"]["venue"]["sampleSize"] == 1
    assert packet["pressureRouteVerified"] is False
    assert packet["projectionInfluence"] == "explanation_only"


def test_understat_cache_refreshes_after_expiry_and_fails_open(monkeypatch):
    key = "understat:EPL:2025"
    payload = {"teams": {"1": {"id": "1", "title": "Arsenal", "history": []}}}
    understat._MEMORY_CACHE.clear()
    understat._MEMORY_CACHE[key] = (time.time() - understat._CACHE_TTL_SECONDS - 1, payload)

    class Cache:
        async def find_one(self, *args, **kwargs):
            return None

        async def update_one(self, *args, **kwargs):
            return None

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return payload

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, *args, **kwargs):
            return Response()

    monkeypatch.setattr(understat, "db", SimpleNamespace(understat_cache=Cache()))
    monkeypatch.setattr(understat.httpx, "AsyncClient", lambda **kwargs: Client())
    refreshed = asyncio.run(understat._load_league("EPL", 2025))
    assert refreshed["teams"] == payload["teams"]

    class BrokenClient(Client):
        async def get(self, *args, **kwargs):
            raise RuntimeError("provider unavailable")

    understat._MEMORY_CACHE.clear()
    monkeypatch.setattr(understat.httpx, "AsyncClient", lambda **kwargs: BrokenClient())
    assert asyncio.run(understat._load_league("EPL", 2025)) is None


def test_tactical_explanation_labels_press_intensity_without_claiming_a_marker():
    explanation = build_tactical_explanation({
        "playerName": "Florian Lejeune",
        "teamName": "Rayo Vallecano",
        "opponentName": "Sevilla",
        "venue": "away",
        "propType": "pass_attempts",
        "position": "CB",
        "line": 64.5,
        "projectedValue": 54,
        "recommendation": "UNDER",
        "pOver": 20,
        "pUnder": 80,
        "seasonAverage": 56,
        "venueAverage": 52,
        "recentAverage": 53,
        "pressIntensity": {
            "status": "available",
            "score100": 88,
            "label": "Elite",
            "sampleSize": 19,
            "source": "api_football",
            "projectionApplied": True,
        },
    })
    assert "Press Intensity is 88/100" in explanation
    assert "observed aggregate actions and same-fixture opponent passes" in explanation
    assert "individual markers are not claimed" in explanation
    assert "80% projection interval" not in explanation  # no interval supplied