"""Verified recent-match opponent pressure and block profiles.

This module deliberately separates observed facts from tactical labels:

* API-Football lineups provide the opponent's formation when the exact fixture
  is covered.
* StatsBomb event data provides exact-match pressure locations and an
  event-derived PPDA for the defined thirds.
* ``HIGH_BLOCK``/``MID_BLOCK``/``LOW_BLOCK`` are classifications of those
  observed event locations, not claims of continuous defensive-line tracking.

The packet is explanation/shadow evidence only.  Missing provider coverage is
returned as unavailable rather than estimated or converted to zero.
"""

from __future__ import annotations

import asyncio as aio
from datetime import datetime, timezone
import re
import unicodedata
from typing import Any

from statsbomb_client import compute_event_metrics, fetch_match_enrichment
from utils import api_football_request


_CACHE_PREFIX = "fx_tactical_profile_v1_"
_CACHE_TTL_SECONDS = 30 * 24 * 3600
_MAX_MATCHES = 40
_NETWORK_CONCURRENCY = 3


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _name_match(left: Any, right: Any) -> bool:
    a, b = _norm(left), _norm(right)
    return bool(a and b and (a == b or a in b or b in a))


def _api_response_list(payload: Any) -> list[dict[str, Any]]:
    """Normalize API-Football response-shaped test doubles and live lists."""
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        response = payload.get("response")
        if isinstance(response, list):
            return [row for row in response if isinstance(row, dict)]
    return []


def _fixture_id(row: dict[str, Any]) -> str | None:
    raw = row.get("_fid") or row.get("fixtureId") or row.get("fixture_id")
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def _formation_profile(
    rows: Any,
    *,
    team_name: str,
    opponent_name: str,
) -> dict[str, Any]:
    team_formation = None
    opponent_formation = None
    team_source = None
    opponent_source = None

    for row in _api_response_list(rows):
        raw_team = row.get("team") or {}
        row_name = (
            raw_team.get("name")
            if isinstance(raw_team, dict)
            else raw_team
        ) or row.get("teamName")
        formation = str(row.get("formation") or "").strip() or None
        if not formation:
            continue
        if _name_match(row_name, team_name):
            team_formation = formation
            team_source = "api_football_fixture_lineup"
        elif _name_match(row_name, opponent_name):
            opponent_formation = formation
            opponent_source = "api_football_fixture_lineup"

    # The user-facing tactical annotation is about the opponent. A missing
    # player-team lineup row must not downgrade an independently verified
    # opponent formation to "unavailable".
    status = (
        "confirmed"
        if opponent_formation
        else "partial"
        if team_formation
        else "unavailable"
    )
    return {
        "status": status,
        "teamFormation": team_formation,
        "opponentFormation": opponent_formation,
        "teamSource": team_source,
        "opponentSource": opponent_source,
        "source": "API-Football fixtures/lineups" if status != "unavailable" else None,
    }


async def _fetch_formation(
    fixture_id: str,
    *,
    team_name: str,
    opponent_name: str,
) -> dict[str, Any]:
    try:
        rows = await aio.wait_for(
            api_football_request("fixtures/lineups", {"fixture": fixture_id}),
            timeout=3.0,
        )
        return _formation_profile(
            rows,
            team_name=team_name,
            opponent_name=opponent_name,
        )
    except Exception as exc:
        return {
            "status": "unavailable",
            "teamFormation": None,
            "opponentFormation": None,
            "source": None,
            "reason": f"lineup_fetch_{type(exc).__name__}",
        }


def _pressure_intensity(ppda: Any) -> str | None:
    try:
        value = float(ppda)
    except (TypeError, ValueError):
        return None
    if value < 10:
        return "very_high"
    if value < 14:
        return "high"
    if value < 20:
        return "moderate"
    return "low"


def classify_block_profile(metrics: dict[str, Any] | None) -> dict[str, Any]:
    """Classify an exact-match pressure profile from StatsBomb events.

    The thresholds are intentionally explicit and conservative.  They are
    used to label the observed distribution of pressure events, not to claim
    a continuously tracked defensive line.
    """
    metrics = metrics if isinstance(metrics, dict) else {}
    by_third = metrics.get("pressureByThird") or {}
    attacking = int(by_third.get("attacking") or 0)
    middle = int(by_third.get("middle") or 0)
    defensive = int(by_third.get("defensive") or 0)
    total = attacking + middle + defensive
    ppda = metrics.get("ppda")
    coordinate_mode = metrics.get("coordinateMode")

    if total < 8 or coordinate_mode != "team_relative":
        return {
            "label": "UNAVAILABLE",
            "status": "insufficient_event_evidence",
            "confidence": "none",
            "pressureEventCount": total,
            "pressureShares": None,
            "ppda": ppda,
            "ppdaStatus": metrics.get("ppdaStatus") or "unavailable",
            "pressureIntensity": _pressure_intensity(ppda),
            "method": "event_pressure_distribution",
            "reason": (
                "At least 8 direction-normalized pressure events are required "
                "before assigning a block profile."
            ),
        }

    shares = {
        "attacking": round(attacking / total, 3),
        "middle": round(middle / total, 3),
        "defensive": round(defensive / total, 3),
    }
    dominant = max(shares, key=shares.get)
    dominant_share = shares[dominant]

    # A 40% dominant-third threshold prevents a noisy event sample from being
    # promoted to a block label. PPDA is a corroborating press-intensity
    # signal; it cannot override the observed pressure-location distribution.
    if dominant == "attacking" and dominant_share >= 0.40:
        label = "HIGH_BLOCK"
    elif dominant == "defensive" and dominant_share >= 0.40:
        label = "LOW_BLOCK"
    elif dominant == "middle" and dominant_share >= 0.40:
        label = "MID_BLOCK"
    else:
        label = "MIXED_BLOCK"

    try:
        ppda_number = float(ppda)
    except (TypeError, ValueError):
        ppda_number = None
    aligned = (
        (label == "HIGH_BLOCK" and ppda_number is not None and ppda_number < 14)
        or (label == "LOW_BLOCK" and ppda_number is not None and ppda_number >= 14)
        or (label == "MID_BLOCK" and ppda_number is not None and 10 <= ppda_number <= 20)
    )
    confidence = "high" if dominant_share >= 0.55 and aligned else "medium"
    return {
        "label": label,
        "status": "event_derived",
        "confidence": confidence,
        "pressureEventCount": total,
        "pressureShares": shares,
        "dominantPressureThird": dominant,
        "dominantPressureShare": dominant_share,
        "ppda": ppda,
        "ppdaStatus": metrics.get("ppdaStatus") or "unavailable",
        "pressureIntensity": _pressure_intensity(ppda),
        "method": "direction_normalized_pressure_distribution_with_event_ppda",
        "thresholds": {
            "minimumPressureEvents": 8,
            "dominantThirdShare": 0.40,
            "ppdaCorroboration": "high<14; low>=14; mid=10..20",
        },
        "limitations": [
            "Block label is derived from event pressure locations, not continuous tracking.",
            "Formation corroborates the shape but does not prove the defensive block height.",
            "A single match is an observed match profile, not a stable team baseline.",
        ],
    }


def _unavailable_profile(row: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "fixtureId": _fixture_id(row),
        "date": row.get("date"),
        "opponent": row.get("opponent"),
        "venue": row.get("venue"),
        "playerValue": row.get("value"),
        "minutes": row.get("minutes"),
        "score": row.get("score"),
        "formation": {
            "status": "unavailable",
            "teamFormation": None,
            "opponentFormation": None,
            "source": None,
        },
        "ppda": None,
        "ppdaStatus": "unavailable",
        "blockProfile": {
            "label": "UNAVAILABLE",
            "status": "unavailable",
            "confidence": "none",
            "method": None,
        },
        "status": "unavailable",
        "verified": False,
        "source": None,
        "reason": reason,
        "projectionInfluence": "explanation_only",
        "shadowWeight": 0.0,
    }


async def _build_one_profile(
    db,
    row: dict[str, Any],
    *,
    league_id: int | None,
    league_name: str,
    team_name: str,
    player_name: str,
) -> dict[str, Any]:
    fixture_id = _fixture_id(row)
    if not fixture_id:
        return _unavailable_profile(row, "missing_verified_fixture_id")

    opponent_name = str(row.get("opponent") or "").strip()
    match_date = str(row.get("date") or "").strip()[:10]
    if not opponent_name or not match_date:
        return _unavailable_profile(row, "incomplete_verified_fixture_identity")

    pressure_task = fetch_match_enrichment(
        db,
        fixture_id=fixture_id,
        league_id=league_id,
        league_name=league_name,
        team_name=team_name,
        opponent_name=opponent_name,
        match_date=match_date,
        player_name=player_name or "",
    )
    formation_task = _fetch_formation(
        fixture_id,
        team_name=team_name,
        opponent_name=opponent_name,
    )
    pressure_result, formation = await aio.gather(
        pressure_task,
        formation_task,
        return_exceptions=True,
    )
    if isinstance(pressure_result, Exception):
        pressure_result = None
    if isinstance(formation, Exception):
        formation = {
            "status": "unavailable",
            "teamFormation": None,
            "opponentFormation": None,
            "source": None,
        }

    metrics = (
        pressure_result.get("eventMetrics")
        if isinstance(pressure_result, dict)
        else None
    )
    block = classify_block_profile(metrics)
    pressure_available = (
        isinstance(pressure_result, dict)
        and pressure_result.get("available") is True
        and isinstance(metrics, dict)
        and metrics.get("ppda") is not None
    )
    verified = pressure_available or formation.get("status") == "confirmed"
    shadow_weight = (
        1.0
        if pressure_available and formation.get("status") == "confirmed"
        else 0.7
        if pressure_available
        else 0.35
        if formation.get("status") == "confirmed"
        else 0.0
    )
    return {
        "fixtureId": fixture_id,
        "date": row.get("date"),
        "opponent": opponent_name,
        "venue": row.get("venue"),
        "playerValue": row.get("value"),
        "minutes": row.get("minutes"),
        "score": row.get("score"),
        "formation": formation,
        "ppda": metrics.get("ppda") if isinstance(metrics, dict) else None,
        "ppdaStatus": (
            metrics.get("ppdaStatus")
            if isinstance(metrics, dict)
            else "unavailable"
        ),
        "ppdaDefinition": (
            metrics.get("ppdaDefinition")
            if isinstance(metrics, dict)
            else None
        ),
        "pressureByThird": (
            metrics.get("pressureByThird")
            if isinstance(metrics, dict)
            else None
        ),
        "blockProfile": block,
        "status": (
            "verified_event_and_formation"
            if pressure_available and formation.get("status") == "confirmed"
            else "verified_event_only"
            if pressure_available
            else "verified_formation_only"
            if formation.get("status") == "confirmed"
            else "unavailable"
        ),
        "verified": verified,
        "source": {
            "pressure": (
                "StatsBomb Open Data"
                if pressure_available
                else None
            ),
            "formation": formation.get("source"),
            "coverage": (
                pressure_result.get("coverage")
                if isinstance(pressure_result, dict)
                else None
            ),
        },
        "limitations": list(dict.fromkeys(
            (metrics.get("limitations") if isinstance(metrics, dict) else [])
            + (block.get("limitations") if isinstance(block, dict) else [])
        )),
        "projectionInfluence": "explanation_only",
        "shadowWeight": shadow_weight,
    }


async def _read_cached_profiles(db, fixture_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not fixture_ids:
        return {}
    try:
        keys = [f"{_CACHE_PREFIX}{fid}" for fid in fixture_ids]
        rows = await db.fixture_player_cache.find(
            {"_k": {"$in": keys}},
            {"_id": 0, "_k": 1, "_ts": 1, "d": 1},
        ).to_list(len(keys))
        now = datetime.now(timezone.utc)
        out: dict[str, dict[str, Any]] = {}
        for row in rows:
            cached_at = row.get("_ts")
            if isinstance(cached_at, datetime):
                age = (now - cached_at.replace(tzinfo=timezone.utc)).total_seconds()
                if age > _CACHE_TTL_SECONDS:
                    continue
            key = str(row.get("_k") or "")
            if key.startswith(_CACHE_PREFIX) and isinstance(row.get("d"), dict):
                out[key[len(_CACHE_PREFIX):]] = row["d"]
        return out
    except Exception:
        return {}


async def _write_cached_profile(db, fixture_id: str, profile: dict[str, Any]) -> None:
    try:
        await db.fixture_player_cache.update_one(
            {"_k": f"{_CACHE_PREFIX}{fixture_id}"},
            {
                "$set": {
                    "_k": f"{_CACHE_PREFIX}{fixture_id}",
                    "_ts": datetime.now(timezone.utc),
                    "d": profile,
                }
            },
            upsert=True,
        )
    except Exception:
        # Analytics cache writes are regenerable and must not break prediction.
        return


async def fetch_recent_opponent_block_profiles(
    db,
    recent_matches: list[dict[str, Any]] | None,
    *,
    league_id: int | None,
    league_name: str,
    team_name: str,
    player_name: str = "",
    limit: int = _MAX_MATCHES,
    max_network_matches: int = 12,
) -> dict[str, Any]:
    """Return one auditable tactical profile for every supplied match row.

    Cached rows are returned immediately.  A bounded number of uncached rows
    are warmed in this request; remaining rows still appear explicitly as
    unavailable and can be warmed by subsequent predictions without blocking
    the player-stat prior.
    """
    rows = sorted([
        row for row in (recent_matches or [])
        if isinstance(row, dict) and _fixture_id(row)
    ], key=lambda row: str(row.get("date") or ""), reverse=True)[
        :max(1, min(int(limit or _MAX_MATCHES), _MAX_MATCHES))
    ]
    fixture_ids = [_fixture_id(row) for row in rows]
    cached = await _read_cached_profiles(db, fixture_ids)
    pending_all = [row for row in rows if _fixture_id(row) not in cached]
    pending = pending_all[:max(0, int(max_network_matches or 0))]
    deferred = pending_all[len(pending):]

    sem = aio.Semaphore(_NETWORK_CONCURRENCY)

    async def bounded(row: dict[str, Any]):
        async with sem:
            try:
                return await aio.wait_for(
                    _build_one_profile(
                        db,
                        row,
                        league_id=league_id,
                        league_name=league_name,
                        team_name=team_name,
                        player_name=player_name,
                    ),
                    timeout=8.0,
                )
            except Exception as exc:
                return _unavailable_profile(
                    row,
                    f"profile_fetch_{type(exc).__name__}",
                )

    async def warm_rows(rows_to_warm: list[dict[str, Any]]) -> None:
        if not rows_to_warm:
            return
        fresh = await aio.gather(
            *(bounded(row) for row in rows_to_warm),
            return_exceptions=True,
        )
        for row, profile in zip(rows_to_warm, fresh):
            if isinstance(profile, dict):
                fid = _fixture_id(row)
                if fid:
                    cached[fid] = profile
                    await _write_cached_profile(db, fid, profile)

    if pending:
        await warm_rows(pending)

    # Do not delay this prediction for the rest of the historical window.
    # The task is deliberately cache-only on completion and will keep filling
    # exact fixture profiles after the response has returned.
    if deferred:
        aio.create_task(warm_rows(deferred))

    profiles = []
    for row in rows:
        fid = _fixture_id(row)
        profile = cached.get(fid) if fid else None
        profiles.append(
            profile
            if isinstance(profile, dict)
            else _unavailable_profile(row, "not_yet_warmed")
        )

    verified_count = sum(1 for profile in profiles if profile.get("verified"))
    ppda_count = sum(1 for profile in profiles if profile.get("ppda") is not None)
    formation_count = sum(
        1 for profile in profiles
        if (profile.get("formation") or {}).get("status") == "confirmed"
    )
    return {
        "status": (
            "verified"
            if verified_count == len(profiles) and profiles
            else "partial"
            if verified_count or formation_count or ppda_count
            else "warming"
            if profiles
            else "unavailable"
        ),
        "available": bool(verified_count or formation_count or ppda_count),
        "profiles": profiles,
        "sampleSize": len(profiles),
        "verifiedMatches": verified_count,
        "ppdaMatches": ppda_count,
        "formationMatches": formation_count,
        "source": "StatsBomb Open Data + API-Football fixture lineups",
        "projectionInfluence": "explanation_only",
        "shadowWeighting": {
            "status": "shadow_only",
            "weights": {
                "event_and_formation": 1.0,
                "event_only": 0.7,
                "formation_only": 0.35,
                "unavailable": 0.0,
            },
            "projectionAdjustment": 0.0,
        },
        "limitations": [
            "StatsBomb Open Data has restricted competition/date coverage.",
            "Block labels are event-derived pressure-location classifications, not tracking-derived defensive-line height.",
            "Missing coverage is unavailable, not a measured zero or a guessed block.",
        ],
    }