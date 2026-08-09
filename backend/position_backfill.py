"""API-Sports lineup-history position backfill.

This module is deliberately conservative.  API-Sports' ``fixtures/players``
category (G/M/D/F) is not an exact position, so it cannot write a customer
facing position.  Only repeated exact positions derived from a lineup grid are
persisted.  The job is resumable and safe to run repeatedly.
"""

from __future__ import annotations

import asyncio as aio
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from config import db
from tactical_evidence import infer_grid_position
from utils import priority_api_football_request

_EXACT_POSITIONS = {
    "GK", "CB", "LB", "RB", "LWB", "RWB", "CDM", "CM", "CAM",
    "LM", "RM", "LW", "RW", "CF", "ST", "SS",
}
_GENERIC_TO_SPECIFIC = {
    "Goalkeeper": {"GK"},
    "Defender": {"CB", "LB", "RB", "LWB", "RWB"},
    "Midfielder": {"CDM", "CM", "CAM", "LM", "RM", "LW", "RW"},
    "Attacker": {"LW", "RW", "CF", "ST", "SS", "CAM"},
}
_TRUSTED_SOURCES = {"gemini_web_grounded", "manual_override", "api_sports_lineup_history"}


def _pid(value: Any) -> str:
    try:
        return str(int(value))
    except (TypeError, ValueError):
        return str(value or "").strip()


def _category(value: Any) -> str:
    raw = str(value or "").strip().lower()
    return {
        "goalkeeper": "Goalkeeper",
        "gk": "Goalkeeper",
        "defender": "Defender",
        "def": "Defender",
        "d": "Defender",
        "midfielder": "Midfielder",
        "mid": "Midfielder",
        "m": "Midfielder",
        "attacker": "Attacker",
        "forward": "Attacker",
        "fwd": "Attacker",
        "f": "Attacker",
    }.get(raw, "")


def _record_is_trusted(doc: dict) -> bool:
    position = str(doc.get("specificPosition") or "").upper().strip()
    return (
        position in _EXACT_POSITIONS
        and str(doc.get("source") or doc.get("roleSource") or "") in _TRUSTED_SOURCES
    )


def _position_role(position: str) -> str:
    # A role is not needed to validate the position.  Keep it empty rather
    # than inventing one from stats; the normal prediction path can add an
    # observed role separately.
    return ""


async def _team_fixture_ids(team_id: Any, limit: int) -> list[int]:
    """Fetch recent completed fixtures for a team.

    API-Sports has no ``fixtures?player=...`` filter.  The supported identity
    path is team fixtures followed by lineup-player matching.
    """
    try:
        team_id = int(team_id)
    except (TypeError, ValueError):
        return []
    rows = []
    try:
        cached = await db.team_fixture_history.find_one(
            {"teamId": team_id},
            {"_id": 0, "fixtures": 1},
        )
        rows = (cached or {}).get("fixtures") or []
    except Exception:
        rows = []
    if not rows:
        try:
            rows = await priority_api_football_request(
                "fixtures",
                {"team": team_id, "last": min(max(limit * 2, 12), 40), "status": "FT"},
            )
        except Exception as exc:
            print(f"[POSITION BACKFILL] fixture lookup failed team={team_id}: {type(exc).__name__}")
            rows = []
    ids: list[int] = []
    for row in rows or []:
        fixture = row.get("fixture") or {}
        fid = fixture.get("id")
        status = (fixture.get("status") or {}).get("short")
        # Cached team_fixture_history may be normalized and omit status. Its
        # presence in the completed-fixture cache is sufficient; live API rows
        # must still pass the terminal-status guard.
        if fid and (not status or status in {"FT", "AET", "PEN"}):
            try:
                ids.append(int(fid))
            except (TypeError, ValueError):
                continue
    return list(dict.fromkeys(ids))[:limit]


async def _fixture_observation(fixture_id: int, target_id: str) -> tuple[str, str] | None:
    """Return the exact grid position and formation for one appearance."""
    try:
        lineups = await priority_api_football_request(
            "fixtures/lineups", {"fixture": fixture_id}
        )
    except Exception:
        return None
    for team in lineups or []:
        formation = team.get("formation") or ""
        for row in team.get("startXI") or []:
            player = row.get("player") or {}
            if _pid(player.get("id")) != target_id:
                continue
            position = infer_grid_position(
                player.get("grid"), formation, player.get("pos")
            )
            if position in _EXACT_POSITIONS:
                return position, str(formation)
    return None


async def _candidate_records(limit: int) -> list[dict]:
    """Collect unique soccer players that the product already knows."""
    by_id: dict[str, dict] = {}

    async def add(doc: dict, *, source: str = ""):
        team_value = doc.get("team")
        team_object = team_value if isinstance(team_value, dict) else {}
        team_name = (
            doc.get("teamName")
            or (team_value if isinstance(team_value, str) else "")
            or team_object.get("name")
            or ""
        )
        team_id = (
            doc.get("teamId")
            or team_object.get("id")
            or 0
        )
        pid = _pid(doc.get("playerId") or doc.get("player", {}).get("id"))
        name = str(
            doc.get("playerName")
            or doc.get("player", {}).get("name")
            or ""
        ).strip()
        if not pid or not name:
            return
        existing = by_id.setdefault(
            pid,
            {
                "playerId": pid,
                "playerName": name,
                "team": str(team_name),
                "teamId": team_id,
                "teamIds": [],
                "genericPosition": _category(
                    doc.get("genericPosition")
                    or doc.get("position")
                    or doc.get("player", {}).get("position")
                ),
                "sources": [],
            },
        )
        if not existing["team"]:
            existing["team"] = str(team_name)
        candidate_team_id = team_id
        if candidate_team_id:
            try:
                candidate_team_id = int(candidate_team_id)
            except (TypeError, ValueError):
                candidate_team_id = 0
            if candidate_team_id:
                existing["teamId"] = existing.get("teamId") or candidate_team_id
                if candidate_team_id not in existing["teamIds"]:
                    existing["teamIds"].append(candidate_team_id)
        if not existing["genericPosition"]:
            existing["genericPosition"] = _category(
                doc.get("genericPosition")
                or doc.get("position")
                or doc.get("player", {}).get("position")
            )
        if source and source not in existing["sources"]:
            existing["sources"].append(source)

    for doc in await db.player_positions.find(
        {}, {"_id": 0, "playerId": 1, "playerName": 1, "team": 1,
             "teamName": 1, "genericPosition": 1, "specificPosition": 1,
             "source": 1}
    ).to_list(limit):
        await add(doc, source="player_positions")

    # Saved picks are the important customer-facing population.  Include
    # their raw provider category even when the position cache is incomplete.
    for collection in ("picks", "predictions"):
        projection = {
            "_id": 0, "playerId": 1, "playerName": 1, "team": 1,
            "teamName": 1, "position": 1, "sport": 1, "player": 1,
        }
        for doc in await db[collection].find(
            {"sport": {"$in": ["soccer", ""]}},
            projection,
        ).sort("_id", -1).to_list(limit * 4):
            await add(doc, source=collection)

    # Position-cache rows often predate teamId persistence. Recover the current
    # club context from the already-cached API-Sports squad identities before
    # spending any provider calls.
    for pid, record in by_id.items():
        try:
            squad_rows = await db.cache_players.find(
                {"playerId": int(pid)},
                {"_id": 0, "teamId": 1, "teamName": 1, "position": 1, "_cachedAt": 1},
            ).sort("_cachedAt", -1).limit(20).to_list(20)
        except (TypeError, ValueError, Exception):
            squad_rows = []
        preferred_squad = next(
            (squad for squad in squad_rows if squad.get("teamId")),
            None,
        )
        if preferred_squad:
            # Provider squad identity is more reliable than stale pick text
            # (national-team names frequently remain in historical picks after
            # the player returns to a club).
            if preferred_squad.get("teamName"):
                record["team"] = preferred_squad["teamName"]
            try:
                record["teamId"] = int(preferred_squad["teamId"])
            except (TypeError, ValueError):
                pass
        for squad in squad_rows:
            team_id = squad.get("teamId")
            if team_id:
                try:
                    team_id = int(team_id)
                except (TypeError, ValueError):
                    continue
                if team_id not in record["teamIds"]:
                    record["teamIds"].append(team_id)
                if not record.get("teamId"):
                    record["teamId"] = team_id
            if not record.get("team") and squad.get("teamName"):
                record["team"] = squad["teamName"]
            if not record.get("genericPosition"):
                record["genericPosition"] = _category(squad.get("position"))

    return list(by_id.values())[:limit]


async def backfill_api_sports_positions(
    *,
    limit: int = 250,
    fixtures_per_player: int = 12,
    min_observations: int = 2,
    concurrency: int = 3,
) -> dict:
    """Repair weak position profiles from repeated API-Sports lineup grids."""
    records = await _candidate_records(limit)
    summary = {
        "startedAt": datetime.now(timezone.utc).isoformat(),
        "scanned": len(records),
        "updated": 0,
        "alreadyTrusted": 0,
        "noFixtureHistory": 0,
        "noExactLineupEvidence": 0,
        "insufficientRepeatedEvidence": 0,
        "categoryMismatch": 0,
        "errors": 0,
        "results": [],
    }
    sem = aio.Semaphore(max(1, concurrency))

    async def process(record: dict) -> dict:
        pid = record["playerId"]
        current = await db.player_positions.find_one(
            {"$or": [{"playerId": pid}, {"playerId": int(pid)}]},
            {"_id": 0, "specificPosition": 1, "source": 1, "genericPosition": 1},
        )
        if (
            current
            and _record_is_trusted(current)
            and current.get("source") != "api_sports_lineup_history"
        ):
            summary["alreadyTrusted"] += 1
            return {"playerId": pid, "playerName": record["playerName"], "status": "trusted"}

        async with sem:
            team_ids = list(record.get("teamIds") or [])
            if record.get("teamId") and int(record["teamId"]) not in team_ids:
                team_ids.insert(0, int(record["teamId"]))
            fixture_ids: list[int] = []
            for team_id in team_ids[:3]:
                fixture_ids.extend(
                    await _team_fixture_ids(team_id, fixtures_per_player)
                )
                if len(dict.fromkeys(fixture_ids)) >= fixtures_per_player:
                    break
            fixture_ids = list(dict.fromkeys(fixture_ids))[:fixtures_per_player]
            if not fixture_ids:
                summary["noFixtureHistory"] += 1
                return {"playerId": pid, "playerName": record["playerName"], "status": "no_fixture_history"}
            observations: list[tuple[str, str, int]] = []
            for fid in fixture_ids:
                try:
                    observed = await _fixture_observation(fid, pid)
                except Exception:
                    observed = None
                if observed:
                    observations.append((observed[0], observed[1], fid))

        if not observations:
            summary["noExactLineupEvidence"] += 1
            return {"playerId": pid, "playerName": record["playerName"], "status": "no_exact_lineup_evidence"}

        counts = Counter(position for position, _, _ in observations)
        position, count = counts.most_common(1)[0]
        allowed = _GENERIC_TO_SPECIFIC.get(record.get("genericPosition") or "", set())
        if allowed and position not in allowed:
            summary["categoryMismatch"] += 1
            return {
                "playerId": pid, "playerName": record["playerName"],
                "status": "category_mismatch", "position": position,
            }
        if count < min_observations:
            summary["insufficientRepeatedEvidence"] += 1
            return {
                "playerId": pid, "playerName": record["playerName"],
                "status": "insufficient_repeated_evidence",
                "position": position, "observations": count,
            }

        now = datetime.now(timezone.utc).isoformat()
        evidence = [
            {"fixtureId": fid, "position": pos, "formation": formation}
            for pos, formation, fid in observations
        ]
        fields = {
            "playerId": int(pid),
            "playerName": record["playerName"],
            "team": record.get("team") or "",
            "teamId": record.get("teamId") or 0,
            "genericPosition": record.get("genericPosition") or "",
            "specificPosition": position,
            "role": _position_role(position),
            "source": "api_sports_lineup_history",
            "roleSource": "api_sports_lineup_history",
            "positionEvidence": evidence,
            "positionObservationCount": len(observations),
            "positionCounts": dict(counts),
            "updatedAt": now,
        }
        # Never downgrade a trusted profile.  This second guard also handles a
        # profile becoming trusted while this bounded job is in flight.
        latest = await db.player_positions.find_one(
            {"$or": [{"playerId": int(pid)}, {"playerId": pid}]},
            {"_id": 0, "specificPosition": 1, "source": 1, "roleSource": 1},
        )
        if (
            latest
            and _record_is_trusted(latest)
            and latest.get("source") != "api_sports_lineup_history"
        ):
            summary["alreadyTrusted"] += 1
            return {"playerId": pid, "playerName": record["playerName"], "status": "trusted"}
        await db.player_positions.update_one(
            {"$or": [{"playerId": int(pid)}, {"playerId": pid}, {"playerName": record["playerName"]}]},
            {"$set": fields},
            upsert=True,
        )
        summary["updated"] += 1
        return {
            "playerId": pid, "playerName": record["playerName"],
            "status": "updated", "position": position,
            "observations": len(observations), "positionCounts": dict(counts),
        }

    results = await aio.gather(*(process(record) for record in records), return_exceptions=True)
    for result in results:
        if isinstance(result, Exception):
            summary["errors"] += 1
            continue
        summary["results"].append(result)
    summary["finishedAt"] = datetime.now(timezone.utc).isoformat()
    return summary