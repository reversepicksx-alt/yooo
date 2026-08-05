"""Optional, evidence-only integration with TheStatsAPI.

API-Football remains authoritative for fixture identity, predictions, live
settlement, and final player stats.  This module only returns enrichment that
has passed an identity join against the already verified API-Football fixture.
Every response carries coverage/status metadata so an absent provider record is
never mistaken for a zero or an observed tactical fact.
"""

from __future__ import annotations

import asyncio as aio
import hashlib
import json
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from config import THESTATSAPI_API_KEY, THESTATSAPI_BASE, db


_inflight: dict[str, aio.Task] = {}
_inflight_lock = aio.Lock()
_DEFAULT_TTL = 60 * 60 * 24 * 14
_MATCH_TTL = 60 * 60 * 6
_SHORT_TTL = 60 * 10
_REQUEST_TIMEOUT = 8.0


def _norm(value: Any) -> str:
    value = str(value or "").lower()
    value = unicodedata.normalize("NFKD", value)
    value = "".join(char for char in value if not unicodedata.combining(char))
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _tokens(value: Any) -> set[str]:
    return {t for t in _norm(value).split() if len(t) > 2}


def _team_match(actual: Any, wanted: Any) -> bool:
    a, w = _norm(actual), _norm(wanted)
    if not a or not w:
        return False
    if a == w or a in w or w in a:
        return True
    overlap = len(_tokens(a) & _tokens(w))
    return overlap >= 2 or (overlap == 1 and len(_tokens(a)) == 1 and len(_tokens(w)) == 1)


def _date_delta_days(left: Any, right: Any) -> float | None:
    try:
        l = datetime.fromisoformat(str(left).replace("Z", "+00:00"))
        r = datetime.fromisoformat(str(right).replace("Z", "+00:00"))
        if l.tzinfo is None:
            l = l.replace(tzinfo=timezone.utc)
        if r.tzinfo is None:
            r = r.replace(tzinfo=timezone.utc)
        return abs((l - r).total_seconds()) / 86400
    except (TypeError, ValueError, OverflowError):
        return None


def _cache_key(path: str, params: dict[str, Any] | None) -> str:
    raw = json.dumps({"path": path, "params": params or {}}, sort_keys=True, separators=(",", ":"))
    return "tsa:" + hashlib.sha256(raw.encode()).hexdigest()


async def _request(path: str, params: dict[str, Any] | None = None, *, ttl: int = _DEFAULT_TTL) -> dict:
    """Read a provider endpoint with Mongo caching and request coalescing."""
    if not THESTATSAPI_API_KEY:
        return {"status": "unavailable", "reason": "provider_not_configured"}

    key = _cache_key(path, params)
    try:
        cached = await db.thestatsapi_cache.find_one({"_k": key}, {"_id": 0, "v": 1, "expiresAt": 1})
        if cached and cached.get("v") is not None:
            expiry = cached.get("expiresAt")
            if not expiry or expiry > datetime.now(timezone.utc):
                return cached["v"]
    except Exception as exc:
        print(f"[THESTATSAPI CACHE READ] skipped: {type(exc).__name__}: {exc}")

    async with _inflight_lock:
        task = _inflight.get(key)
        if task is None:
            task = aio.create_task(_request_uncached(path, params, key, ttl))
            _inflight[key] = task
    try:
        return await task
    finally:
        async with _inflight_lock:
            if _inflight.get(key) is task:
                _inflight.pop(key, None)


async def _request_uncached(path: str, params: dict[str, Any] | None, key: str, ttl: int) -> dict:
    headers = {
        "Authorization": f"Bearer {THESTATSAPI_API_KEY}",
        "Accept": "application/json",
    }
    url = f"{THESTATSAPI_BASE.rstrip('/')}/{path.lstrip('/')}"
    last_reason = "request_failed"
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
                response = await client.get(url, headers=headers, params=params or {})
            if response.status_code == 404:
                value = {"status": "unavailable", "reason": "coverage_missing", "httpStatus": 404}
                await _cache_result(key, value, _SHORT_TTL)
                return value
            if response.status_code == 429:
                last_reason = "rate_limited"
                if attempt == 0:
                    retry_after = response.headers.get("Retry-After")
                    try:
                        delay = max(1.0, min(3.0, float(retry_after)))
                    except (TypeError, ValueError):
                        delay = 1.5
                    await aio.sleep(delay)
                    continue
                break
            if response.status_code >= 400:
                last_reason = f"http_{response.status_code}"
                break
            body = response.json()
            value = {"status": "ok", "body": body}
            await _cache_result(key, value, ttl)
            return value
        except (httpx.TimeoutException, httpx.RequestError, ValueError) as exc:
            last_reason = type(exc).__name__.lower()
            if attempt == 0:
                await aio.sleep(0.25)
    value = {"status": "unavailable", "reason": last_reason}
    # Do not cache rate-limit failures for long: a later prediction should get
    # a chance to use coverage after the provider window resets.
    await _cache_result(key, value, _SHORT_TTL)
    return value


async def _cache_result(key: str, value: dict, ttl: int) -> None:
    try:
        await db.thestatsapi_cache.replace_one(
            {"_k": key},
            {
                "_k": key,
                "v": value,
                "createdAt": datetime.now(timezone.utc),
                "expiresAt": datetime.now(timezone.utc) + timedelta(seconds=ttl),
            },
            upsert=True,
        )
    except Exception as exc:
        print(f"[THESTATSAPI CACHE WRITE] skipped: {type(exc).__name__}: {exc}")


def _body_rows(result: dict) -> list[dict]:
    body = result.get("body") if isinstance(result, dict) else None
    if isinstance(body, list):
        return [x for x in body if isinstance(x, dict)]
    if isinstance(body, dict):
        data = body.get("data")
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
        if isinstance(data, dict):
            return [data]
    return []


def _body_data(result: dict) -> dict:
    body = result.get("body") if isinstance(result, dict) else None
    if isinstance(body, dict) and isinstance(body.get("data"), dict):
        return body["data"]
    return body if isinstance(body, dict) else {}


def _body_list_or_points(result: dict) -> list[dict]:
    """Read list-shaped `data` payloads without treating them as missing."""
    rows = _body_rows(result)
    if rows:
        return rows
    body = result.get("body") if isinstance(result, dict) else None
    if isinstance(body, dict):
        if isinstance(body.get("points"), list):
            return [x for x in body["points"] if isinstance(x, dict)]
        data = body.get("data")
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
    return []


def _coverage(result: dict, *, empty_reason: str = "coverage_missing") -> str:
    if not isinstance(result, dict) or result.get("status") != "ok":
        return result.get("reason", "unavailable") if isinstance(result, dict) else "unavailable"
    body = result.get("body")
    if isinstance(body, list):
        return "measured" if body else empty_reason
    if not isinstance(body, dict) or not body:
        return empty_reason
    if "data" in body:
        data = body.get("data")
        if isinstance(data, (list, dict)):
            return "measured" if data else empty_reason
    if "points" in body:
        points = body.get("points")
        return "measured" if isinstance(points, list) and points else empty_reason
    return "measured"


def _event_entity_name(value: Any) -> str | None:
    if isinstance(value, dict):
        name = value.get("name")
        return str(name) if name else None
    return str(value) if value else None


async def _find_match(
    fixture: dict,
    team_name: str,
    opponent_name: str,
) -> dict:
    fixture_date = fixture.get("matchDate") or fixture.get("fixtureDate") or ""
    try:
        dt = datetime.fromisoformat(str(fixture_date).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        dt = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    date_value = dt.strftime("%Y-%m-%d")
    result = await _request(
        "football/matches",
        {
            "date_from": (dt - timedelta(days=2)).strftime("%Y-%m-%d"),
            "date_to": (dt + timedelta(days=2)).strftime("%Y-%m-%d"),
            "per_page": 100,
        },
        ttl=_MATCH_TTL,
    )
    if result.get("status") != "ok":
        return {"status": "unavailable", "reason": result.get("reason", "match_lookup_failed")}

    candidates: list[tuple[float, dict]] = []
    for match in _body_rows(result):
        home = match.get("home_team") or {}
        away = match.get("away_team") or {}
        if not (
            (_team_match(home.get("name"), team_name) and _team_match(away.get("name"), opponent_name))
            or (_team_match(home.get("name"), opponent_name) and _team_match(away.get("name"), team_name))
        ):
            continue
        delta = _date_delta_days(match.get("utc_date"), fixture_date)
        if delta is None or delta > 2:
            continue
        # Exact team names are stronger than token/substring matches; date is
        # the tie-breaker when a provider returns duplicate competition rows.
        exact_bonus = sum(
            1 for actual, wanted in (
                (home.get("name"), team_name), (away.get("name"), opponent_name),
                (home.get("name"), opponent_name), (away.get("name"), team_name),
            ) if _norm(actual) == _norm(wanted)
        )
        candidates.append((delta - exact_bonus * 0.1, match))
    if not candidates:
        return {"status": "unavailable", "reason": "fixture_not_found"}
    _, selected = sorted(candidates, key=lambda item: item[0])[0]
    return {
        "status": "matched",
        "matchId": selected.get("id"),
        "competitionId": selected.get("competition_id"),
        "seasonId": selected.get("season_id"),
        "utcDate": selected.get("utc_date"),
        "home": selected.get("home_team") or {},
        "away": selected.get("away_team") or {},
        "providerDate": date_value,
        "verified": True,
    }


def _find_lineup_player(lineup: dict, player_name: str, team_name: str) -> tuple[dict | None, str | None]:
    for side in ("home", "away"):
        block = lineup.get(side) or {}
        if not _team_match(block.get("name"), team_name):
            continue
        for group in ("starting_xi", "substitutes"):
            for player in block.get(group) or []:
                if _team_match(player.get("name"), player_name):
                    return player, side
    return None, None


def _trim_points(value: Any, limit: int = 320) -> list[dict]:
    points = value if isinstance(value, list) else []
    output = []
    for point in points[:limit]:
        if not isinstance(point, dict):
            continue
        try:
            x, y = float(point.get("x")), float(point.get("y"))
        except (TypeError, ValueError):
            continue
        if 0 <= x <= 100 and 0 <= y <= 100:
            output.append({"x": round(x, 2), "y": round(y, 2), "count": point.get("count")})
    return output


def _trim_shots(value: Any, limit: int = 60) -> list[dict]:
    shots = value if isinstance(value, list) else []
    output = []
    for shot in shots[:limit]:
        if not isinstance(shot, dict):
            continue
        output.append({
            "x": shot.get("x"),
            "y": shot.get("y"),
            "minute": shot.get("minute"),
            "result": shot.get("result"),
            "xg": shot.get("expected_goals"),
            "onTarget": shot.get("is_on_target"),
            "isGoal": shot.get("is_goal"),
            "situation": shot.get("situation"),
        })
    return output


async def get_soccer_enrichment(
    *,
    fixture: dict,
    player_name: str,
    team_name: str,
    opponent_name: str,
) -> dict:
    """Return identity-verified, analysis-only TheStatsAPI enrichment."""
    base = {
        "provider": "TheStatsAPI",
        "status": "unavailable",
        "coverage": {},
        "analysisOnly": True,
        "settlementAuthority": "API-Football",
    }
    if not THESTATSAPI_API_KEY:
        base["reason"] = "provider_not_configured"
        return base

    match = await _find_match(fixture, team_name, opponent_name)
    if match.get("status") != "matched" or not match.get("matchId"):
        base["reason"] = match.get("reason", "fixture_not_found")
        base["fixtureVerification"] = {"status": "unavailable", "reason": base["reason"]}
        return base

    match_id = match["matchId"]
    lineup_result = await _request(f"football/matches/{match_id}/lineups", ttl=_SHORT_TTL)
    lineup_data = _body_data(lineup_result)
    player, side = _find_lineup_player(lineup_data, player_name, team_name)
    base.update({
        "status": "matched",
        "match": match,
        "fixtureVerification": {
            "status": "verified",
            "apiFootballTeam": team_name,
            "apiFootballOpponent": opponent_name,
            "dateDeltaDays": _date_delta_days(match.get("utcDate"), fixture.get("matchDate") or fixture.get("fixtureDate")),
        },
        "coverage": {"lineup": _coverage(lineup_result)},
    })

    home = lineup_data.get("home") or {}
    away = lineup_data.get("away") or {}
    base["opponentTactics"] = {
        "status": "measured" if (away if side == "home" else home).get("formation") else "coverage_missing",
        "formation": (away if side == "home" else home).get("formation"),
        "confirmed": lineup_data.get("confirmed") is True,
        "starterCount": len((away if side == "home" else home).get("starting_xi") or []),
        "observedAt": "confirmed_lineup" if lineup_data.get("confirmed") else "provider_lineup",
    }
    if not player or not player.get("id"):
        base["player"] = {"status": "unavailable", "reason": "player_not_found_in_verified_lineup"}
        base["coverage"]["player"] = "identity_missing"
        return base

    player_id = player["id"]
    base["player"] = {
        "status": "matched",
        "id": player_id,
        "name": player.get("name"),
        "side": side,
        "position": player.get("position"),
        "jerseyNumber": player.get("jersey_number"),
    }

    # These requests are deliberately independent and are coalesced/cacheable.
    match_heatmap_task = _request(
        f"football/matches/{match_id}/players/{player_id}/heatmap", ttl=_MATCH_TTL
    )
    player_stats_task = _request(
        f"football/matches/{match_id}/player-stats",
        {"player_ids": player_id},
        ttl=_MATCH_TTL,
    )
    shotmap_task = _request(
        f"football/matches/{match_id}/shotmap",
        {"player_id": player_id},
        ttl=_MATCH_TTL,
    )
    season_heatmap_task = None
    if match.get("competitionId") and match.get("seasonId"):
        season_heatmap_task = _request(
            f"football/players/{player_id}/competitions/{match['competitionId']}/seasons/{match['seasonId']}/heatmap",
            ttl=_DEFAULT_TTL,
        )
    tasks = [match_heatmap_task, player_stats_task, shotmap_task, season_heatmap_task or aio.sleep(0, result=None)]
    match_heatmap, player_stats, shotmap, season_heatmap = await aio.gather(*tasks, return_exceptions=True)
    if not isinstance(match_heatmap, dict):
        match_heatmap = {"status": "unavailable", "reason": "request_failed"}
    if not isinstance(player_stats, dict):
        player_stats = {"status": "unavailable", "reason": "request_failed"}
    if not isinstance(shotmap, dict):
        shotmap = {"status": "unavailable", "reason": "request_failed"}
    if not isinstance(season_heatmap, dict):
        season_heatmap = {"status": "unavailable", "reason": "request_failed"}

    match_heatmap_data = _body_data(match_heatmap)
    season_heatmap_data = _body_data(season_heatmap)
    heatmap_points = _trim_points(match_heatmap_data.get("points"))
    if not heatmap_points:
        heatmap_points = _trim_points(_body_list_or_points(match_heatmap))
    heatmap_source = "match"
    if not heatmap_points:
        heatmap_points = _trim_points(season_heatmap_data.get("points"))
    if not heatmap_points:
        heatmap_points = _trim_points(_body_list_or_points(season_heatmap))
        heatmap_source = "season"
    base["heatmap"] = {
        "status": "measured" if heatmap_points else _coverage(match_heatmap),
        "source": heatmap_source if heatmap_points else None,
        "points": heatmap_points,
        "sampleSize": len(heatmap_points),
        "observedTouchLocations": bool(heatmap_points),
    }
    base["coverage"]["matchHeatmap"] = _coverage(match_heatmap)
    base["coverage"]["seasonHeatmap"] = _coverage(season_heatmap)
    stats_rows = _body_rows(player_stats)
    base["matchStats"] = {
        "status": "measured" if stats_rows else _coverage(player_stats),
        "data": stats_rows[0] if stats_rows else None,
    }
    shots_data = _body_data(shotmap)
    shots = _trim_shots(shots_data.get("data"))
    if not shots:
        shots = _trim_shots(_body_list_or_points(shotmap))
    base["shotmap"] = {
        "status": "measured" if shots else _coverage(shotmap),
        "shots": shots,
        "sampleSize": len(shots),
        "npXgSummary": shots_data.get("np_xg_summary"),
    }
    base["coverage"]["shotmap"] = _coverage(shotmap)
    base["coverage"]["playerStats"] = _coverage(player_stats)

    # Live-only context is deliberately separate from historical player
    # evidence. It is sampled only for a fixture that API-Football already
    # marked live; missing live coverage is not a pre-match zero.
    fixture_status = str(fixture.get("matchStatus") or "").upper()
    if fixture_status in {"1H", "HT", "2H", "ET", "BT", "P", "LIVE"}:
        live_stats, timeline = await aio.gather(
            _request(f"football/matches/{match_id}/live-stats", ttl=60),
            _request(f"football/matches/{match_id}/timeline", ttl=60),
            return_exceptions=True,
        )
        if not isinstance(live_stats, dict):
            live_stats = {"status": "unavailable", "reason": "request_failed"}
        if not isinstance(timeline, dict):
            timeline = {"status": "unavailable", "reason": "request_failed"}
        live_data = _body_data(live_stats)
        timeline_data = _body_data(timeline)
        events = timeline_data.get("events") if isinstance(timeline_data, dict) else []
        base["currentMatch"] = {
            "status": "measured" if live_data else _coverage(live_stats),
            "liveStats": live_data or None,
            "timeline": [
                {
                    "minute": event.get("minute"),
                    "period": event.get("period"),
                    "type": event.get("type"),
                    "team": _event_entity_name(event.get("team")),
                    "player": _event_entity_name(event.get("player")),
                }
                for event in (events[-40:] if isinstance(events, list) else [])
                if isinstance(event, dict)
            ],
            "timelineCoverage": _coverage(timeline),
        }
        base["coverage"]["liveStats"] = _coverage(live_stats)
        base["coverage"]["timeline"] = _coverage(timeline)
    else:
        base["currentMatch"] = {
            "status": "not_live",
            "liveStats": None,
            "timeline": [],
            "timelineCoverage": "not_live",
        }
    return base