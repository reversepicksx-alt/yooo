"""Optional Bzzoiro Football API enrichment.

Bzzoiro is a secondary evidence source.  API-Football remains authoritative for
fixture identity, primary projection inputs, and settlement.  This module
therefore returns a stable unavailable packet on every integration failure and
never raises into the prediction path.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
import re
import unicodedata
from typing import Any

import httpx


BASE_URL = (os.environ.get("BZZOIRO_API_BASE") or "https://sports.bzzoiro.com/api/v2").rstrip("/")
TOKEN_ENV = "BZZOIRO_API_TOKEN"
TIMEOUT_SECONDS = 8.0
CACHE_TTL_SECONDS = 6 * 60 * 60


def _empty(reason: str) -> dict[str, Any]:
    return {
        "available": False,
        "status": "unavailable",
        "provider": "bzzoiro",
        "source": "Bzzoiro Football API",
        "shadowOnly": True,
        "reason": reason,
        "fixture": None,
        "lineup": None,
        "target": None,
        "pressIntensity": None,
        "limitations": [
            "Bzzoiro is optional enrichment; API-Football remains authoritative.",
            "Missing Bzzoiro coverage is unavailable data, not a measured zero.",
        ],
    }


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _name_match(left: Any, right: Any) -> bool:
    a, b = _norm(left), _norm(right)
    if not a or not b:
        return False
    return a == b or a in b or b in a


def _date_text(value: Any) -> str:
    text = str(value or "").strip()
    return text[:10]


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace("%", "").strip())
    except (TypeError, ValueError):
        return None


def _side_for_team(event: dict[str, Any], team_id: int | None, team_name: str) -> str | None:
    if team_id:
        if event.get("home_team_id") == team_id:
            return "home"
        if event.get("away_team_id") == team_id:
            return "away"
    if _name_match(event.get("home_team"), team_name):
        return "home"
    if _name_match(event.get("away_team"), team_name):
        return "away"
    return None


def _find_event(
    rows: list[dict[str, Any]],
    *,
    team_id: int | None,
    team_name: str,
    opponent_id: int | None,
    opponent_name: str,
    match_date: str,
) -> dict[str, Any] | None:
    target_date = _date_text(match_date)
    candidates = []
    for event in rows:
        if not isinstance(event, dict):
            continue
        side = _side_for_team(event, team_id, team_name)
        if not side:
            continue
        other = "away" if side == "home" else "home"
        opponent_ok = (
            opponent_id
            and event.get(f"{other}_team_id") == opponent_id
        ) or _name_match(event.get(f"{other}_team"), opponent_name)
        if not opponent_ok:
            continue
        event_date = _date_text(event.get("event_date"))
        distance = 0 if target_date and event_date == target_date else 1
        candidates.append((distance, event_date, event))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1]))
    return candidates[0][2]


def _unwrap_results(payload: Any, key: str = "results") -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        rows = payload.get(key, payload.get("players", []))
        return rows if isinstance(rows, list) else []
    return payload if isinstance(payload, list) else []


def compute_press_proxy(team_stats: dict[str, Any] | None) -> dict[str, Any] | None:
    """Calculate a descriptive one-match press proxy from Bzzoiro team stats.

    This intentionally mirrors the existing defensive-actions/PPDA proxy but
    does not invent a historical sample.  A single fixture is evidence for the
    observed match only and cannot change a projection.
    """
    if not isinstance(team_stats, dict):
        return None
    tackles = _number(team_stats.get("total_tackles", team_stats.get("tackles")))
    interceptions = _number(team_stats.get("interceptions"))
    passes = _number(team_stats.get("passes"))
    possession = _number(team_stats.get("ball_possession"))
    if tackles is None and interceptions is None and possession is None:
        return None
    tackles = tackles or 0.0
    interceptions = interceptions or 0.0
    actions = tackles + interceptions
    score = max(0.0, min(1.0, (actions - 22.0) / 20.0)) if actions else 0.0
    if actions < 22:
        label = "Low"
    elif actions < 31:
        label = "Moderate"
    elif actions < 36:
        label = "High"
    else:
        label = "Elite"
    return {
        "label": label,
        "score": round(score, 3),
        "signalUsed": "tackles+interceptions",
        "defensiveActions": round(actions, 1),
        "tackles": round(tackles, 1),
        "interceptions": round(interceptions, 1),
        "possession": round(possession, 1) if possession is not None else None,
        "passes": round(passes, 1) if passes is not None else None,
        "passesPerDefensiveAction": round(passes / actions, 1) if passes is not None and actions > 0 else None,
        "supportingSignals": {
            key: round(value, 1)
            for key, value in {
                "recoveries": _number(team_stats.get("recoveries")),
                "duels": _number(team_stats.get("duels")),
                "fouls": _number(team_stats.get("fouls")),
                "clearances": _number(team_stats.get("clearances")),
                "blockedShots": _number(team_stats.get("blocked_shots")),
                "dispossessed": _number(team_stats.get("dispossessed")),
            }.items()
            if value is not None
        },
        "sampleSize": 1,
        "evidenceStatus": "single_fixture_shadow",
        "projectionAdjustment": 0.0,
        "projectionAdjustmentStatus": "shadow_only",
        "limitations": [
            "This is a one-match defensive-actions/PPDA proxy, not direct pressure-event tracking.",
            "Bzzoiro does not provide a universal passes-under-pressure or PPDA field here.",
            "This is not true PPDA because passes allowed in the defensive third are unavailable.",
            "A single fixture cannot establish a stable team press baseline.",
        ],
    }


def _team_stats_for_side(stats: dict[str, Any], side: str | None) -> dict[str, Any] | None:
    if not side or not isinstance(stats, dict):
        return None
    value = stats.get(side)
    return value if isinstance(value, dict) else None


async def _request(client: httpx.AsyncClient, path: str, params: dict[str, Any] | None = None) -> Any:
    response = await client.get(f"{BASE_URL}{path}", params=params or {})
    response.raise_for_status()
    return response.json()


async def fetch_fixture_enrichment(
    db,
    *,
    fixture_id: int | str | None,
    team_id: int | None,
    team_name: str,
    opponent_id: int | None,
    opponent_name: str,
    match_date: str,
    player_id: int | None,
    player_name: str,
) -> dict[str, Any]:
    """Fetch exact-match enrichment when Bzzoiro covers the fixture."""
    token = os.environ.get(TOKEN_ENV)
    if not token:
        return _empty("BZZOIRO_API_TOKEN is not configured.")
    if not fixture_id or not team_name or not opponent_name or not match_date:
        return _empty("Verified fixture identity is incomplete.")

    cache_key = f"bzzoiro_fixture_{fixture_id}_{team_id}_{opponent_id}"
    try:
        cached = await db.bzzoiro_cache.find_one({"_id": cache_key}, {"_id": 0, "data": 1, "cachedAt": 1})
        if cached and cached.get("data") and cached.get("cachedAt"):
            cached_at = cached["cachedAt"]
            if isinstance(cached_at, datetime):
                age = (datetime.now(timezone.utc) - cached_at.replace(tzinfo=timezone.utc)).total_seconds()
                if age < CACHE_TTL_SECONDS:
                    return cached["data"]
    except Exception as exc:
        print(f"[BZZOIRO] cache read skipped: {type(exc).__name__}: {exc}")

    headers = {"Authorization": f"Token {token}", "Accept": "application/json"}
    try:
        target = datetime.fromisoformat(str(match_date).replace("Z", "+00:00"))
    except ValueError:
        target = datetime.now(timezone.utc)
    date_from = (target - timedelta(days=3)).date().isoformat()
    date_to = (target + timedelta(days=3)).date().isoformat()

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS, headers=headers) as client:
            event_payload = await _request(
                client,
                "/events/",
                {
                    # Bzzoiro and API-Football use different numeric IDs.
                    # Name is the cross-provider identity bridge; numeric IDs
                    # are only useful after Bzzoiro's own record is resolved.
                    "team_name": team_name,
                    "date_from": date_from,
                    "date_to": date_to,
                    "limit": 100,
                },
            )
            event = _find_event(
                _unwrap_results(event_payload),
                team_id=team_id,
                team_name=team_name,
                opponent_id=opponent_id,
                opponent_name=opponent_name,
                match_date=match_date,
            )
            if not event:
                result = _empty("Bzzoiro does not cover this verified fixture.")
                result["fixture"] = {"apiFootballFixtureId": fixture_id, "coverage": "not_found"}
            else:
                bzz_event_id = event.get("id")
                team_side = _side_for_team(event, team_id, team_name)
                opp_side = "away" if team_side == "home" else "home"
                stats_payload, lineups_payload, player_stats_payload = await __import__("asyncio").gather(
                    _request(client, f"/events/{bzz_event_id}/stats/"),
                    _request(client, f"/events/{bzz_event_id}/lineups/"),
                    _request(client, f"/events/{bzz_event_id}/player-stats/"),
                    return_exceptions=True,
                )
                stats = stats_payload if isinstance(stats_payload, dict) else {}
                side_stats = _team_stats_for_side(stats.get("stats") or {}, team_side)
                opp_stats = _team_stats_for_side(stats.get("stats") or {}, opp_side)
                lineups = lineups_payload if isinstance(lineups_payload, dict) else {}
                lineup_groups = lineups.get("lineups") or {}
                own_lineup = lineup_groups.get(team_side) if isinstance(lineup_groups, dict) else {}
                target_lineup = None
                lineup_players = []
                if isinstance(own_lineup, dict):
                    lineup_players = (own_lineup.get("players") or []) + (own_lineup.get("substitutes") or [])
                    target_lineup = next(
                        (
                            item for item in lineup_players
                            if (player_id and item.get("id") == player_id)
                            or _name_match(item.get("name"), player_name)
                        ),
                        None,
                    )
                player_rows = (
                    (player_stats_payload.get("player_stats") or [])
                    if isinstance(player_stats_payload, dict)
                    else []
                )
                target_stats = next(
                    (
                        item for item in player_rows
                        if (player_id and item.get("player_id") == player_id)
                        or _name_match(item.get("name"), player_name)
                    ),
                    None,
                )
                average_positions = (stats.get("average_positions") or {}).get(team_side, [])
                target_position = next(
                    (
                        item for item in average_positions
                        if (player_id and item.get("player_id") == player_id)
                        or _name_match(item.get("name"), player_name)
                    ),
                    None,
                )
                press = compute_press_proxy(opp_stats)
                result = {
                    "available": True,
                    "status": "covered",
                    "provider": "bzzoiro",
                    "source": "Bzzoiro Football API",
                    "shadowOnly": True,
                    "fixture": {
                        "apiFootballFixtureId": fixture_id,
                        "bzzoiroEventId": bzz_event_id,
                        "date": event.get("event_date"),
                        "homeTeam": event.get("home_team"),
                        "awayTeam": event.get("away_team"),
                        "coverage": "exact_date_and_opponent",
                    },
                    "lineup": {
                        "status": lineups.get("lineup_status"),
                        "formation": own_lineup.get("formation") if isinstance(own_lineup, dict) else None,
                        "opponentFormation": (
                            (lineup_groups.get(opp_side) or {}).get("formation")
                            if isinstance(lineup_groups, dict)
                            else None
                        ),
                        "target": target_lineup,
                    },
                    "target": {
                        "lineup": target_lineup,
                        "averagePosition": target_position,
                        "matchStats": target_stats,
                    },
                    "teamStats": {
                        "team": side_stats,
                        "opponent": opp_stats,
                    },
                    "pressIntensity": press,
                    "limitations": [
                        "Bzzoiro is secondary enrichment; API-Football remains authoritative.",
                        "Position coordinates are observed match averages, not tracking-derived role labels.",
                        "The pressure score is descriptive and shadow-only.",
                    ],
                }
    except Exception as exc:
        print(f"[BZZOIRO] enrichment skipped: {type(exc).__name__}: {exc}")
        result = _empty(f"Bzzoiro request failed: {type(exc).__name__}.")

    try:
        await db.bzzoiro_cache.update_one(
            {"_id": cache_key},
            {"$set": {"data": result, "cachedAt": datetime.now(timezone.utc)}},
            upsert=True,
        )
    except Exception as exc:
        print(f"[BZZOIRO] cache write skipped: {type(exc).__name__}: {exc}")
    return result