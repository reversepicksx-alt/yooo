import asyncio

import pytest
from fastapi import HTTPException

from routes import jarvis
from tactical_memory import TacticalMemoryInput, retrieve_tactical_memory, upsert_tactical_memory


class Cursor:
    def __init__(self, rows):
        self.rows = rows

    def sort(self, *_args):
        return self

    def limit(self, value):
        self.rows = self.rows[:value]
        return self

    async def to_list(self, length=None):
        return self.rows[:length] if length else self.rows


class Collection:
    def __init__(self):
        self.rows = []

    async def create_index(self, *_args, **_kwargs):
        return "idx"

    async def find_one(self, query, sort=None):
        candidates = [
            row for row in self.rows
            if row["memory_key"] == query["memory_key"] and not row["stale"]
        ]
        return sorted(candidates, key=lambda row: row["version"], reverse=True)[0] if candidates else None

    async def update_many(self, query, update):
        changed = 0
        for row in self.rows:
            if row["memory_key"] == query["memory_key"] and not row["stale"]:
                row.update(update["$set"])
                changed += 1
        return type("Result", (), {"modified_count": changed})()

    async def insert_one(self, document):
        self.rows.append(dict(document))

    def find(self, query, projection=None):
        def matches(row):
            if query.get("memory_type") and row["memory_type"] != query["memory_type"]:
                return False
            if query.get("identity.team_id") and row["identity"].get("team_id") != query["identity.team_id"]:
                return False
            if query.get("stale") == {"$ne": True} and row["stale"]:
                return False
            return True
        return Cursor([row for row in self.rows if matches(row)])


class DB:
    def __init__(self):
        self.jarvis_tactical_memory = Collection()


def item(**overrides):
    values = dict(
        memory_type="team_fingerprint",
        identity={"team_id": 10, "team_name": "Example FC"},
        competition={"league_id": 39, "manager_regime": "coach-a"},
        context={"venue": "home", "prop_type": "shots"},
        confidence=72,
        sample_size=12,
        provenance=[{"source": "api-football", "fixture_ids": [1, 2]}],
        payload={"pressing": "high"},
    )
    values.update(overrides)
    return TacticalMemoryInput(**values)


def test_upsert_is_append_only_and_stales_previous_version():
    db = DB()
    first = asyncio.run(upsert_tactical_memory(db, item()))
    second = asyncio.run(upsert_tactical_memory(db, item(payload={"pressing": "mid"})))
    assert first["version"] == 1
    assert second["version"] == 2
    assert len(db.jarvis_tactical_memory.rows) == 2
    assert db.jarvis_tactical_memory.rows[0]["stale"] is True
    assert db.jarvis_tactical_memory.rows[1]["stale"] is False


def test_retrieval_is_bounded_and_filters_by_team():
    db = DB()
    for team_id in (10, 20):
        asyncio.run(upsert_tactical_memory(db, item(identity={"team_id": team_id})))
    rows = asyncio.run(retrieve_tactical_memory(
        db, memory_type="team_fingerprint", team_id=10, limit=1000,
    ))
    assert len(rows) == 1
    assert rows[0]["identity"]["team_id"] == 10


def test_schema_rejects_credentials():
    with pytest.raises(ValueError, match="credentials"):
        asyncio.run(upsert_tactical_memory(
            DB(), item(payload={"api_key": "should-never-be-stored"})
        ))


def test_tactical_memory_route_requires_jarvis_auth(monkeypatch):
    monkeypatch.setattr(jarvis, "_JARVIS_KEY", "test-key")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(jarvis.get_tactical_memory(
            authorization=None, memory_type=None, team_id=None,
            opponent_id=None, player_id=None, role=None,
            manager_regime=None, venue=None, prop_type=None,
            since=None, until=None, include_stale=False, limit=20,
        ))
    assert exc.value.status_code == 401