import asyncio
from pathlib import Path

import pytest

import soccer_bdl_client
from routes import picks


def _finished_fixture():
    return {
        "fixture": {
            "id": 12345,
            "date": "2026-08-05T01:00:00+00:00",
            "status": {"short": "FT"},
        },
        "teams": {
            "home": {"id": 10, "name": "Pachuca"},
            "away": {"id": 20, "name": "Puebla"},
        },
        "goals": {"home": 2, "away": 3},
    }


def _player_fixture_stats():
    return [
        {
            "team": {"id": 10},
            "players": [
                {
                    "player": {"id": 99, "name": "Sergio Barreto"},
                    "statistics": [
                        {
                            "games": {"minutes": 90},
                            "passes": {"total": 53, "accuracy": 85},
                        }
                    ],
                }
            ],
        }
    ]


def test_forced_soccer_settlement_uses_fresh_exact_fixture_and_player_calls(monkeypatch):
    calls = []

    async def fake_priority_request(endpoint, params=None, *, force_refresh=False):
        calls.append((endpoint, params, force_refresh))
        if endpoint == "fixtures":
            return [_finished_fixture()]
        if endpoint == "fixtures/players":
            return _player_fixture_stats()
        raise AssertionError(f"unexpected provider endpoint: {endpoint}")

    monkeypatch.setattr(soccer_bdl_client, "is_bdl_league", lambda _league: False)
    monkeypatch.setattr(picks, "priority_api_football_request", fake_priority_request)

    async def run():
        return await picks._settle_soccer_pick(
            {
                "id": "pick-1",
                "playerName": "Sergio Barreto",
                "playerId": 99,
                "fixtureId": 12345,
                "line": 48.5,
                "recommendation": "OVER",
            },
            10,
            99,
            "Puebla",
            "passes",
            262,
            force_refresh=True,
        )

    result = asyncio.run(run())

    assert result["actualValue"] == 53
    assert result["result"] == "hit"
    assert result["settlementSource"]["verified"] is True
    assert calls[:2] == [
        ("fixtures", {"id": 12345}, True),
        ("fixtures/players", {"fixture": 12345}, True),
    ]
    assert all(force_refresh for _endpoint, _params, force_refresh in calls)


class _FakeCollection:
    def __init__(self, document=None):
        self.document = document
        self.updated = None

    async def find_one(self, _query, _projection=None):
        return dict(self.document) if self.document else None

    async def update_one(self, _query, update):
        self.updated = update


class _FakeDb:
    def __init__(self, pick):
        self.sessions = _FakeCollection({"email": "subscriber@example.com"})
        self.picks = _FakeCollection(pick)


def test_refresh_endpoint_persists_corrected_result_and_provenance(monkeypatch):
    saved_pick = {
        "pickId": "pick-1",
        "email": "subscriber@example.com",
        "sport": "soccer",
        "playerName": "Sergio Barreto",
        "playerId": 99,
        "teamId": 10,
        "fixtureId": 12345,
        "leagueId": 262,
        "propType": "passes",
        "line": 48.5,
        "recommendation": "OVER",
        "status": "settled",
        "result": "miss",
        "actualValue": 24,
        "settlementReview": {"reason": "suspect"},
    }
    fake_db = _FakeDb(saved_pick)
    monkeypatch.setattr(picks, "db", fake_db)

    async def fake_settle(*_args, force_refresh=False, **_kwargs):
        assert force_refresh is True
        return {
            "pickId": "pick-1",
            "fixtureId": 12345,
            "status": "settled",
            "result": "hit",
            "actualValue": 53,
            "minutesPlayed": 90,
            "settlementSource": {
                "provider": "api-football",
                "fixtureId": 12345,
                "playerId": 99,
                "propType": "passes",
                "statPath": "statistics.passes.total",
                "fixtureStatus": "FT",
                "verified": True,
                "verificationMethod": "fixture_id",
                "recordedAt": "2026-08-18T12:00:00+00:00",
            },
        }

    monkeypatch.setattr(picks, "_settle_soccer_pick", fake_settle)

    async def run():
        return await picks.refresh_pick_settlement(
            "pick-1",
            {
                "email": "subscriber@example.com",
                "token": "session-token",
                "pickId": "pick-1",
            },
        )

    response = asyncio.run(run())

    assert response["confirmed"] is True
    assert response["changed"] is True
    assert response["actualValue"] == 53
    assert response["result"] == "hit"
    assert fake_db.picks.updated["$set"]["actualValue"] == 53
    assert fake_db.picks.updated["$set"]["result"] == "hit"
    assert fake_db.picks.updated["$set"]["settlementSource"]["verified"] is True
    assert "settlementReview" in fake_db.picks.updated["$unset"]
    history = fake_db.picks.updated["$push"]["settlementRefreshHistory"]["$each"]
    assert history[0]["before"]["actualValue"] == 24
    assert history[0]["after"]["actualValue"] == 53


def test_customer_archive_exposes_a_real_35_game_floor_without_padding():
    source = (Path(__file__).resolve().parents[1] / "routes" / "predict.py").read_text()

    assert "_RECENT_ARCHIVE_MIN = 35" in source
    assert "len(player_game_logs or []) < _RECENT_ARCHIVE_MIN" in source
    assert '"archiveMinimum": _RECENT_ARCHIVE_MIN' in source
    assert '"archiveStatus": (' in source
    assert '"insufficient_provider_data"' in source
    assert "no rows were fabricated" in source