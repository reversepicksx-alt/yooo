"""Priority, cache-first completion of the three causal regression replays."""
from __future__ import annotations

import asyncio as aio
from datetime import datetime, timezone
from typing import Any

from causal_evidence_assembly import assemble_causal_evidence
from causal_script_engine import build_causal_script_packet
from config import db
from utils import api_sports_soft_budget_available


REPLAY_CASES: tuple[dict[str, Any], ...] = (
    {
        "key": "petrovic-1557374",
        "playerName": "Djordje Petrovic",
        "fixtureId": 1557374,
        "playerId": 118307,
        "teamId": 35,
        "opponentId": 50,
        "venue": "away",
        "position": "GK",
        "propType": "passes",
        "recommendation": "under",
        "line": 24.5,
        "projection": 23.5,
        "cutoff": "2026-08-23T13:00:00+00:00",
    },
    {
        "key": "ferraresi-1492340",
        "playerName": "Nahuel Ferraresi",
        "fixtureId": 1492340,
        "playerId": 2440,
        "teamId": 120,
        "opponentId": 134,
        "venue": "home",
        "position": "CB",
        "propType": "passes",
        "recommendation": "under",
        "line": 50.5,
        "projection": 46.0,
        "cutoff": "2026-08-24T23:00:00+00:00",
    },
    {
        "key": "moncayola-1570350",
        "playerName": "Jon Moncayola",
        "fixtureId": 1570350,
        "playerId": 46662,
        "teamId": 727,
        "opponentId": 539,
        "venue": "home",
        "position": "CM",
        "propType": "passes",
        "recommendation": "over",
        "line": 39.5,
        "projection": 40.0,
        "cutoff": "2026-08-24T17:30:00+00:00",
    },
)


def _replay_prediction(case: dict[str, Any], saved: dict[str, Any] | None) -> dict[str, Any]:
    """Use saved pre-match fields when available; never read target result data."""
    saved = saved or {}
    allowed = {
        key: saved.get(key)
        for key in (
            "tacticalContext", "moneyline", "gameScript", "managerContext",
            "playerGameLogs", "positionComparison", "roleEvidencePacket",
            "exactTacticalRole", "tacticalRole", "formation", "teamFormation",
        )
        if saved.get(key) is not None
    }
    return {
        **allowed,
        "sport": "soccer",
        "fixtureId": case["fixtureId"],
        "playerId": case["playerId"],
        "fixtureTeamId": case["teamId"],
        "fixtureOpponentId": case["opponentId"],
        "teamId": case["teamId"],
        "opponentId": case["opponentId"],
        "playerName": case["playerName"],
        "playerPosition": case["position"],
        "propType": case["propType"],
        "venue": case["venue"],
        "recommendation": case["recommendation"],
        "line": case["line"],
        "projection": case["projection"],
    }


async def _saved_pregame_snapshot(case: dict[str, Any]) -> dict[str, Any] | None:
    """Find a saved prediction by immutable fixture/player identity only."""
    try:
        return await db.picks.find_one(
            {
                "fixtureId": case["fixtureId"],
                "$or": [{"playerId": case["playerId"]}, {"player_id": case["playerId"]}],
            },
            {"_id": 0},
            sort=[("timestamp", -1)],
        )
    except Exception:
        return None


def _evidence_used(packet: dict[str, Any]) -> dict[str, Any]:
    evidence = packet.get("evidence") or {}
    cohort = packet.get("opponentRoleCohort") or {}
    history = packet.get("history") or {}
    return {
        "targetFixtureIds": [
            row.get("fixtureId") for row in evidence.get("targetHistory") or []
            if row.get("fixtureId")
        ],
        "cohortFixtureIds": [
            row.get("fixtureId") for row in evidence.get("opponentRoleCandidates") or []
            if row.get("fixtureId")
        ],
        "matchingVenueCleanSample": history.get("matchingVenueCleanSample"),
        "matchingVenueTaggedSample": history.get("matchingVenueTaggedSample"),
        "distortionCounts": history.get("distortionCounts"),
        "cohortWeightedSample": cohort.get("weightedSampleSize"),
        "cohortWorkloadAverage": cohort.get("workloadAverage"),
        "cohortNormalVenueAverage": cohort.get("normalMatchingVenueAverage"),
        "cohortEffect": cohort.get("effect"),
        "script": (packet.get("script") or {}).get("classification"),
        "gateReason": (packet.get("recommendationGate") or {}).get("reason"),
    }


async def run_pending_causal_replays() -> dict[str, Any]:
    """Complete pending replays in declared order, stopping at each gate verdict."""
    summary = {"completed": [], "deferred": [], "skipped": []}
    for case in REPLAY_CASES:
        try:
            prior = await db.causal_replay_packets.find_one(
                {"_id": case["key"]}, {"_id": 0, "status": 1}
            )
            if (prior or {}).get("status") == "complete":
                summary["skipped"].append(case["key"])
                continue
        except Exception:
            # Keep a transient storage issue from blocking a cache-only replay.
            pass
        if not api_sports_soft_budget_available():
            summary["deferred"].append(case["key"])
            break

        saved = await _saved_pregame_snapshot(case)
        prediction = _replay_prediction(case, saved)
        request = {
            "fixture_id": case["fixtureId"], "player_id": case["playerId"],
            "prop_type": case["propType"], "line": case["line"],
        }
        context = {
            "team_id": case["teamId"], "opponent_id": case["opponentId"],
            "venue": case["venue"], "fixture_date": case["cutoff"],
            "replay_mode": True, "replay_priority": True,
        }
        evidence = await assemble_causal_evidence(prediction, request, context)
        packet = build_causal_script_packet(prediction, request, context, evidence)
        decision = (packet.get("recommendationGate") or {}).get("decision")
        complete = decision in {"PASS", "REJECT", "CONFIRM"}
        record = {
            "_id": case["key"],
            "case": {key: value for key, value in case.items() if key != "cutoff"},
            "status": "complete" if complete else "pending_evidence",
            "completedAt": datetime.now(timezone.utc) if complete else None,
            "packet": packet,
            "evidenceUsed": _evidence_used(packet),
            "targetResultFieldsRead": False,
            "proof": (
                "Replay mode never requests the target fixture endpoint; all "
                "provider detail fixture IDs are strictly before cutoff."
            ),
        }
        try:
            await db.causal_replay_packets.replace_one({"_id": case["key"]}, record, upsert=True)
        except Exception as error:
            print(f"[CAUSAL REPLAY] cache write failed for {case['key']}: {type(error).__name__}")
        (summary["completed"] if complete else summary["deferred"]).append(case["key"])
        if not complete:
            # Do not consume the reset budget on later cases when the highest
            # priority replay still needs evidence.
            break
    return summary


async def causal_replay_priority_loop() -> None:
    """Run pending replays once per UTC day, immediately after a reset."""
    last_attempted_day: str | None = None
    while True:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != last_attempted_day and api_sports_soft_budget_available():
            try:
                summary = await run_pending_causal_replays()
                print(f"[CAUSAL REPLAY] priority run: {summary}")
                last_attempted_day = today
            except Exception as error:
                print(f"[CAUSAL REPLAY] priority run failed: {type(error).__name__}: {error}")
                last_attempted_day = today
        await aio.sleep(60)