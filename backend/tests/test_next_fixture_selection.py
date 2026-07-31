import asyncio
from datetime import datetime, timezone

import routes.misc as misc
from routes.misc import _cached_match_is_active
from utils import select_next_fixture


NOW = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)


def _fixture(fid, date, status="NS", team_id=135, opponent_id=147):
    return {
        "fixture": {
            "id": fid,
            "date": date,
            "status": {"short": status},
        },
        "teams": {
            "home": {"id": team_id, "name": "Team"},
            "away": {"id": opponent_id, "name": "Opponent"},
        },
        "league": {"id": 71, "name": "Brazil"},
    }


def test_finished_fixture_never_wins_over_future_fixture():
    selected = select_next_fixture(
        [
            _fixture(100, "2026-07-31T10:00:00Z", "FT"),
            _fixture(101, "2026-08-02T15:00:00Z", "NS"),
        ],
        team_id=135,
        now=NOW,
    )
    assert selected["fixture"]["id"] == 101


def test_earliest_future_fixture_wins_even_if_response_is_unsorted():
    selected = select_next_fixture(
        [
            _fixture(102, "2026-08-04T15:00:00Z"),
            _fixture(103, "2026-08-01T15:00:00Z"),
        ],
        team_id=135,
        now=NOW,
    )
    assert selected["fixture"]["id"] == 103


def test_live_fixture_wins_over_later_scheduled_fixture():
    selected = select_next_fixture(
        [
            _fixture(104, "2026-07-31T11:00:00Z", "2H"),
            _fixture(105, "2026-08-01T15:00:00Z", "NS"),
        ],
        team_id=135,
        now=NOW,
    )
    assert selected["fixture"]["id"] == 104


def test_past_unknown_status_is_rejected():
    selected = select_next_fixture(
        [_fixture(106, "2026-07-31T09:00:00Z", "NS")],
        team_id=135,
        now=NOW,
    )
    assert selected is None


def test_finished_cached_match_is_rejected_even_with_future_timestamp():
    assert not _cached_match_is_active(
        {
            "found": True,
            "statusShort": "FT",
            "date": "2026-08-01T15:00:00Z",
            "fixtureId": 107,
            "opponent": {"id": 147, "name": "Old Opponent"},
        },
        NOW,
    )


def test_unresolved_result_is_safe_to_cache_and_return():
    assert _cached_match_is_active({"found": False}, NOW)


class _FakeCollection:
    def __init__(self, document):
        self.document = document

    async def find_one(self, *args, **kwargs):
        return self.document

    async def update_one(self, *args, **kwargs):
        return None


class _FakeDb:
    def __init__(self, document):
        self.collection = _FakeCollection(document)

    def __getitem__(self, name):
        return self.collection


def test_provider_failure_does_not_return_old_cached_opponent(monkeypatch):
    old_cached = {
        "teamId": 135,
        "cachedAt": datetime.now(timezone.utc),
        "result": {
            "found": True,
            "statusShort": "FT",
            "date": "2026-07-30T15:00:00Z",
            "fixtureId": 108,
            "opponent": {"id": 999, "name": "Outdated Opponent"},
        },
    }
    monkeypatch.setattr(misc, "db", _FakeDb(old_cached))

    async def provider_failure(endpoint, params=None):
        return []

    monkeypatch.setattr(misc, "priority_api_football_request", provider_failure)

    result = asyncio.run(misc.team_next_match(135))

    assert result["found"] is False
    assert "opponent" not in result
    assert "fixtureId" not in result