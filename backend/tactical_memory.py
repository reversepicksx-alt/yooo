"""Owner-only, advisory JARVIS Tactical Memory.

This module is deliberately independent from Reverse Picks math and from the
subscriber knowledge base.  Records are append-only: a new observation creates
another version and older observations remain available as stale audit history.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

MEMORY_TYPES = {
    "team_fingerprint",
    "player_role",
    "matchup_interaction",
    "postmortem",
}
SCHEMA_VERSION = "jarvis-tactical-memory.v1"
MAX_RESULTS = 100


class TacticalMemoryInput(BaseModel):
    memory_type: Literal[
        "team_fingerprint", "player_role", "matchup_interaction", "postmortem"
    ]
    identity: dict[str, Any] = Field(..., min_length=1)
    competition: dict[str, Any] = Field(default_factory=dict)
    context: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(..., ge=0, le=100)
    sample_size: int = Field(..., ge=0, le=1_000_000)
    provenance: list[dict[str, Any]] = Field(..., min_length=1, max_length=50)
    validity: dict[str, Any] = Field(default_factory=dict)
    payload: dict[str, Any] = Field(..., min_length=1)
    schema_version: str = Field(default=SCHEMA_VERSION, max_length=64)

    @field_validator("provenance")
    @classmethod
    def provenance_must_name_source(cls, value):
        if any(not str(item.get("source") or "").strip() for item in value):
            raise ValueError("each provenance item must include a source")
        return value


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _memory_key(item: TacticalMemoryInput) -> str:
    # Context and identity define the observation stream; payload is versioned.
    return hashlib.sha256(
        _canonical({
            "memory_type": item.memory_type,
            "identity": item.identity,
            "competition": item.competition,
            "context": item.context,
        }).encode()
    ).hexdigest()[:32]


def _contains_secret_key(value: Any) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).lower().replace("-", "_")
            if any(token in normalized for token in (
                "api_key", "apikey", "password", "secret", "token",
                "private_key", "credential", "authorization",
            )):
                return True
            if _contains_secret_key(child):
                return True
    elif isinstance(value, list):
        return any(_contains_secret_key(child) for child in value)
    return False


def _clean(doc: dict[str, Any]) -> dict[str, Any]:
    result = dict(doc)
    if "_id" in result:
        result["_id"] = str(result["_id"])
    return result


def _build_filter(
    *,
    memory_type: str | None = None,
    team_id: int | None = None,
    opponent_id: int | None = None,
    player_id: int | None = None,
    role: str | None = None,
    manager_regime: str | None = None,
    venue: str | None = None,
    prop_type: str | None = None,
    since: str | None = None,
    until: str | None = None,
    include_stale: bool = False,
) -> dict[str, Any]:
    query: dict[str, Any] = {}
    if memory_type:
        query["memory_type"] = memory_type
    for field, value in (
        ("identity.team_id", team_id),
        ("identity.opponent_id", opponent_id),
        ("identity.player_id", player_id),
        ("context.venue", venue),
        ("context.prop_type", prop_type),
        ("competition.manager_regime", manager_regime),
    ):
        if value is not None:
            query[field] = value
    if role:
        query["identity.exact_role"] = role
    if not include_stale:
        query["stale"] = {"$ne": True}
    if since or until:
        query["observed_at"] = {}
        if since:
            query["observed_at"]["$gte"] = since
        if until:
            query["observed_at"]["$lte"] = until
    return query


async def ensure_indexes(db: Any) -> None:
    collection = db.jarvis_tactical_memory
    for spec in (
        [("memory_key", 1), ("version", -1)],
        [("memory_type", 1), ("stale", 1), ("observed_at", -1)],
        [("identity.team_id", 1), ("identity.player_id", 1)],
    ):
        try:
            await collection.create_index(spec)
        except Exception:
            # Index setup must not prevent the application from starting.
            pass


async def retrieve_tactical_memory(db: Any, **filters: Any) -> list[dict[str, Any]]:
    limit = min(max(int(filters.pop("limit", 20)), 1), MAX_RESULTS)
    query = _build_filter(**filters)
    rows = await db.jarvis_tactical_memory.find(query, {"_id": 0}).sort(
        [("stale", 1), ("observed_at", -1), ("version", -1)]
    ).limit(limit).to_list(length=limit)
    return [_clean(row) for row in rows]


async def upsert_tactical_memory(db: Any, item: TacticalMemoryInput) -> dict[str, Any]:
    item_data = item.model_dump()
    if _contains_secret_key(item_data):
        raise ValueError("provider credentials and secrets cannot be stored")
    collection = db.jarvis_tactical_memory
    await ensure_indexes(db)
    key = _memory_key(item)
    previous = await collection.find_one(
        {"memory_key": key, "stale": {"$ne": True}},
        sort=[("version", -1)],
    )
    version = int((previous or {}).get("version") or 0) + 1
    if previous:
        await collection.update_many(
            {"memory_key": key, "stale": {"$ne": True}},
            {"$set": {"stale": True, "stale_reason": "superseded", "staled_at": _now()}},
        )
    document = item_data
    document.update({
        "memory_key": key,
        "version": version,
        "observed_at": _now(),
        "created_at": _now(),
        "stale": False,
        "immutable": True,
    })
    await collection.insert_one(document)
    return _clean(document)


async def invalidate_regime(
    db: Any, *, team_id: int | None = None, player_id: int | None = None,
    manager_regime: str | None = None, reason: str = "regime_changed",
) -> int:
    query = {"stale": {"$ne": True}}
    if team_id is not None:
        query["identity.team_id"] = team_id
    if player_id is not None:
        query["identity.player_id"] = player_id
    if manager_regime:
        query["competition.manager_regime"] = {"$ne": manager_regime}
    result = await db.jarvis_tactical_memory.update_many(
        query, {"$set": {"stale": True, "stale_reason": reason, "staled_at": _now()}}
    )
    return int(getattr(result, "modified_count", 0))