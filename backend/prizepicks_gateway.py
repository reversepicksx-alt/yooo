"""Private, read-only SportsGameOdds PrizePicks board gateway."""
from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin

import httpx

BASE_URL = "https://api.sportsgameodds.com/v2"
_cache: tuple[float, list[dict[str, Any]], dict[str, Any]] | None = None
_lock = asyncio.Lock()
_ttl = 30


class GatewayError(RuntimeError):
    def __init__(self, kind: str, message: str, status: int = 502):
        super().__init__(message)
        self.kind, self.status = kind, status


def api_key() -> str:
    return (os.getenv("SGO_API_KEY") or os.getenv("SPORTSGAMEODDS_API_KEY") or "").strip()


def _value(obj: Any, *keys: str) -> Any:
    if not isinstance(obj, dict):
        return None
    for key in keys:
        if obj.get(key) is not None:
            return obj[key]
    return None


def _first_id(obj: Any, *keys: str) -> Any:
    value = _value(obj, *keys)
    if isinstance(value, dict):
        return _value(value, "id", "ID", "value")
    return value


def _open_event(event: dict[str, Any], now: datetime) -> bool:
    status = event.get("status") or {}
    if any(status.get(k) is True for k in ("ended", "completed", "cancelled", "unavailable")):
        return False
    starts = _value(status, "startsAt", "startTime", "starts_at") or _value(event, "startTime", "startsAt")
    if not starts:
        return True
    try:
        dt = datetime.fromisoformat(str(starts).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt >= now
    except ValueError:
        return True


def _normalize_event(event: dict[str, Any], now: datetime) -> list[dict[str, Any]]:
    if not _open_event(event, now):
        return []
    teams = event.get("teams") or {}
    home, away = teams.get("home") or {}, teams.get("away") or {}
    home_name = _value(home.get("names") or {}, "long", "short", "name") or _value(home, "name")
    away_name = _value(away.get("names") or {}, "long", "short", "name") or _value(away, "name")
    event_id = _value(event, "eventID", "eventId", "id")
    league_id = _value(event, "leagueID", "leagueId", "league_id")
    start = _value(event.get("status") or {}, "startsAt", "startTime", "starts_at") or _value(event, "startTime", "startsAt")
    rows = []
    for odd in (event.get("odds") or {}).values() if isinstance(event.get("odds"), dict) else (event.get("odds") or []):
        if not isinstance(odd, dict):
            continue
        books = odd.get("byBookmaker") or odd.get("bookmakers") or {}
        pp = books.get("prizepicks") or books.get("PrizePicks") or {}
        if not isinstance(pp, dict):
            continue
        available = pp.get("available")
        if available is False or (pp.get("status") or "").lower() in {"ended", "unavailable", "closed"}:
            continue
        stat_name = _value(odd, "marketName", "market", "statName", "stat_type")
        player_id = _first_id(odd, "playerID", "playerId", "statEntityID")
        # PLAYER_ID is a marker, not a usable player identifier.
        if player_id == "PLAYER_ID":
            player_id = None
        rows.append({
            "eventId": event_id, "leagueId": league_id,
            "fixtureStartTime": start,
            "homeTeam": home_name, "awayTeam": away_name,
            "homeTeamId": _first_id(home, "teamID", "teamId", "id"),
            "awayTeamId": _first_id(away, "teamID", "teamId", "id"),
            "playerName": _value(odd, "playerName", "player", "statEntityName") or stat_name,
            "playerId": player_id, "statEntityID": _value(odd, "statEntityID"),
            "marketName": stat_name, "statId": _value(odd, "statID", "statId"),
            "period": _value(odd, "period", "periodID", "periodId"),
            "betType": _value(odd, "betType", "type"),
            "side": _value(odd, "sideID", "side", "selection"),
            "currentLine": _value(pp, "overUnder", "line", "odds"),
            "availability": available if available is not None else True,
            "status": _value(pp, "status") or _value(event.get("status") or {}, "status") or "available",
            "openingLine": _value(pp, "openingLine", "open", "openLine"),
            "closingLine": _value(pp, "closingLine", "close", "closeLine"),
            "historicalLines": _value(pp, "historicalLines", "lineHistory", "history"),
            "provider": {"eventId": event_id, "leagueId": league_id, "statId": _value(odd, "statID", "statId")},
        })
    return rows


def _page(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], Any]:
    data = payload.get("data") or payload.get("events") or payload.get("results") or []
    if isinstance(data, dict):
        data = data.get("events") or data.get("items") or []
    pagination = payload.get("pagination") or payload.get("page") or {}
    cursor = (
        _value(payload, "nextCursor", "next_cursor", "cursorNext")
        or _value(pagination, "nextCursor", "next_cursor", "next")
        or _value(payload.get("meta") or {}, "nextCursor", "next_cursor")
    )
    return (data if isinstance(data, list) else []), cursor


async def fetch_board(date: str | None = None, force: bool = False) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    global _cache
    key = api_key()
    if not key:
        raise GatewayError("configuration", "SGO_API_KEY is not configured on the server.", 503)
    now_mono = time.monotonic()
    async with _lock:
        if not force and _cache and now_mono - _cache[0] < _ttl:
            rows = _cache[1]
            return _filter_date(rows, date), {**_cache[2], "cache": "hit"}
    params: dict[str, Any] = {"bookmakerID": "prizepicks", "sportID": "SOCCER", "limit": 100, "apiKey": key}
    if date:
        params["startsAfter"], params["startsBefore"] = f"{date}T00:00:00Z", f"{date}T23:59:59Z"
    rows: list[dict[str, Any]] = []
    cursor = None
    pages = 0
    deadline = time.monotonic() + 55
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(20, connect=5)) as client:
            while pages < 100:
                if time.monotonic() >= deadline:
                    raise GatewayError("upstream", "SportsGameOdds pagination exceeded the gateway time budget.", 504)
                request_params = dict(params)
                if cursor:
                    request_params["cursor"] = cursor
                response = None
                for attempt in range(2):
                    try:
                        response = await client.get(f"{BASE_URL}/events/", params=request_params)
                        if response.status_code not in {408, 429, 500, 502, 503, 504}:
                            break
                    except httpx.TimeoutException:
                        if attempt == 1:
                            raise
                    if attempt == 0:
                        await asyncio.sleep(0.2)
                assert response is not None
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise GatewayError("parse", "SportsGameOdds returned a non-object response.", 502)
                events, cursor = _page(payload)
                for event in events:
                    if isinstance(event, dict):
                        rows.extend(_normalize_event(event, datetime.now(timezone.utc)))
                pages += 1
                if not cursor:
                    break
    except GatewayError:
        raise
    except httpx.HTTPStatusError as exc:
        raise GatewayError("upstream", f"SportsGameOdds returned HTTP {exc.response.status_code}.", 502) from exc
    except httpx.TimeoutException as exc:
        raise GatewayError("upstream", "SportsGameOdds timed out.", 504) from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise GatewayError("upstream", "SportsGameOdds could not be reached or decoded.", 502) from exc
    if pages >= 100 and cursor:
        raise GatewayError("parse", "SportsGameOdds pagination exceeded the safety limit.", 502)
    # De-duplicate provider rows while preserving exact IDs.
    unique = {f"{r.get('eventId')}|{r.get('playerId')}|{r.get('statId')}|{r.get('side')}": r for r in rows}
    rows = list(unique.values())
    meta = {"pages": pages, "events": len({r.get("eventId") for r in rows}), "props": len(rows), "cache": "miss", "source": "SportsGameOdds"}
    async with _lock:
        _cache = (time.monotonic(), rows, meta)
    return _filter_date(rows, date), meta


def _filter_date(rows: list[dict[str, Any]], date: str | None) -> list[dict[str, Any]]:
    if not date:
        return rows
    return [r for r in rows if str(r.get("fixtureStartTime") or "").startswith(date)]