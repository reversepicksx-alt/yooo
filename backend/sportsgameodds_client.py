"""SportsGameOdds market-reference client.

This module is deliberately separate from the prediction and settlement
engines. SportsGameOdds supplies optional PrizePicks/Underdog market context;
API-Football remains the source of truth for fixtures, game logs, and results.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
import unicodedata
from difflib import SequenceMatcher
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx


_BASE_URL = "https://api.sportsgameodds.com/v2"
_BOOKMAKERS = ("prizepicks", "underdog")
_CACHE_TTL_SECONDS = 45
_EVENT_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_BOARD_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_CACHE_LOCK = asyncio.Lock()

# API-Football numeric league IDs → SportsGameOdds league IDs.
# Unknown competitions intentionally fall back to SOCCER rather than being
# guessed into the wrong competition.
_LEAGUE_MAP: dict[int, str] = {
    2: "UEFA_CHAMPIONS_LEAGUE",
    3: "UEFA_EUROPA_LEAGUE",
    39: "EPL",
    61: "FR_LIGUE_1",
    71: "BR_SERIE_A",
    78: "BUNDESLIGA",
    128: "ARGENTINA_PRIMERA_DIVISION",
    135: "IT_SERIE_A",
    140: "LA_LIGA",
    253: "MLS",
    262: "LIGA_MX",
}

_STAT_MAP: dict[str, str] = {
    "pass_attempts": "passes_attempted",
    "passes": "passes_attempted",
    "shots": "shots",
    "shots_total": "shots",
    "shots_on_target": "shots_onGoal",
    "tackles": "tackles",
    "clearances": "clearances",
    "interceptions": "interceptions",
    "crosses": "crosses",
    "assists": "assists",
    "goals": "goals",
    "saves": "goalie_saves",
    "goalie_saves": "goalie_saves",
}

_BOARD_STAT_TYPES: dict[str, str] = {
    "passes_attempted": "pass_attempts",
    "shots": "shots",
    "shots_onGoal": "shots_on_target",
    "tackles": "tackles",
    "clearances": "clearances",
    "interceptions": "interceptions",
    "crosses": "crosses",
    "assists": "assists",
    "goals": "goals",
    "goalie_saves": "saves",
}

_BOARD_STAT_LABELS: dict[str, str] = {
    "passes_attempted": "Passes Attempted",
    "shots": "Shots",
    "shots_onGoal": "Shots On Goal",
    "tackles": "Tackles",
    "clearances": "Clearances",
    "interceptions": "Interceptions",
    "crosses": "Crosses",
    "assists": "Assists",
    "goals": "Goals",
    "goalie_saves": "Saves",
}


def _normalise(value: Any) -> str:
    text = unicodedata.normalize("NFD", str(value or ""))
    text = "".join(char for char in text if unicodedata.category(char) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _tokens(value: Any) -> set[str]:
    return {token for token in _normalise(value).split() if len(token) > 1}


def _team_score(left: str, right: str) -> float:
    a, b = _normalise(left), _normalise(right)
    if not a or not b:
        return 0.0
    if a == b or a in b or b in a:
        return 1.0
    ta, tb = _tokens(a), _tokens(b)
    overlap = len(ta & tb) / max(1, min(len(ta), len(tb)))
    return max(overlap, SequenceMatcher(None, a, b).ratio())


def _teams_match(event: dict[str, Any], home: str, away: str) -> bool:
    teams = event.get("teams") or {}
    event_home = ((teams.get("home") or {}).get("names") or {}).get("long", "")
    event_away = ((teams.get("away") or {}).get("names") or {}).get("long", "")
    direct = _team_score(home, event_home) >= 0.55 and _team_score(away, event_away) >= 0.55
    swapped = _team_score(home, event_away) >= 0.55 and _team_score(away, event_home) >= 0.55
    return direct or swapped


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _player_score(player_name: str, odd: dict[str, Any]) -> float:
    query = _normalise(player_name)
    stat_entity = _normalise(odd.get("statEntityID", "").replace("_", " "))
    market = _normalise(odd.get("marketName", ""))
    if query and query == stat_entity:
        return 1.0
    if query and query in stat_entity:
        return 0.95
    score = SequenceMatcher(None, query, stat_entity).ratio() if query and stat_entity else 0.0
    # Market names contain the display name and are a useful fallback for
    # punctuation/diacritic differences in provider player IDs.
    if query and query in market:
        score = max(score, 0.9)
    query_last = query.split()[-1] if query else ""
    entity_last = stat_entity.split()[-1] if stat_entity else ""
    if query_last and query_last == entity_last:
        score = min(1.0, score + 0.12)
    return score


def _event_date_score(event: dict[str, Any], fixture_date: datetime) -> float:
    event_date = _parse_datetime(((event.get("status") or {}).get("startsAt")))
    if not event_date:
        return 0.0
    hours = abs((event_date - fixture_date).total_seconds()) / 3600
    return max(0.0, 1.0 - min(hours, 72) / 72)


async def _fetch_events(
    *,
    league_id: int,
    league_name: str,
    fixture_date: datetime,
    home_team: str,
    away_team: str,
) -> list[dict[str, Any]]:
    api_key = os.environ.get("SPORTSGAMEODDS_API_KEY", "").strip()
    if not api_key:
        return []

    sgo_league = _LEAGUE_MAP.get(league_id)
    if not sgo_league:
        name = _normalise(league_name)
        if "champions league" in name:
            sgo_league = "UEFA_CHAMPIONS_LEAGUE"
        elif "europa league" in name:
            sgo_league = "UEFA_EUROPA_LEAGUE"
        elif "premier league" in name:
            sgo_league = "EPL"

    cache_key = "|".join(
        [
            sgo_league or "SOCCER",
            fixture_date.strftime("%Y-%m-%d"),
            _normalise(home_team),
            _normalise(away_team),
        ]
    )
    now = time.monotonic()
    async with _CACHE_LOCK:
        cached = _EVENT_CACHE.get(cache_key)
        if cached and now - cached[0] < _CACHE_TTL_SECONDS:
            return cached[1]

    # SGO accepts bookmakerID with sport/league filters on /events. Keep the
    # window broad enough for provider timezone shifts, but never search an
    # unbounded historical board.
    params: dict[str, Any] = {
        "bookmakerID": ",".join(_BOOKMAKERS),
        "limit": 100,
        "startsAfter": (fixture_date - timedelta(days=3)).isoformat().replace("+00:00", "Z"),
        "startsBefore": (fixture_date + timedelta(days=3)).isoformat().replace("+00:00", "Z"),
        "apiKey": api_key,
    }
    if sgo_league:
        params["leagueID"] = sgo_league
    else:
        params["sportID"] = "SOCCER"

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=4.0)) as client:
            response = await client.get(f"{_BASE_URL}/events/", params=params)
            response.raise_for_status()
            payload = response.json()
        events = payload.get("data") or []
        if not isinstance(events, list):
            events = []
    except Exception as exc:
        print(f"[SGO] market lookup unavailable: {type(exc).__name__}: {exc}")
        return []

    matching = [
        event for event in events
        if isinstance(event, dict)
        and _teams_match(event, home_team, away_team)
        and _event_date_score(event, fixture_date) > 0
    ]
    matching.sort(
        key=lambda event: _event_date_score(event, fixture_date),
        reverse=True,
    )
    async with _CACHE_LOCK:
        _EVENT_CACHE[cache_key] = (time.monotonic(), matching[:5])
    return matching[:5]


def _extract_bookmaker_market(
    event: dict[str, Any],
    *,
    player_name: str,
    stat_id: str,
    prop_type: str,
    entered_line: float,
    bookmaker: str,
) -> dict[str, Any] | None:
    candidates: list[tuple[float, dict[str, Any], dict[str, Any]]] = []
    for odd in (event.get("odds") or {}).values():
        if not isinstance(odd, dict) or odd.get("statID") != stat_id:
            continue
        if odd.get("statEntityID") != "PLAYER_ID" and not odd.get("playerID"):
            # Player-specific SGO events use a provider player ID in
            # statEntityID/playerID; exclude team/game totals.
            continue
        book = (odd.get("byBookmaker") or {}).get(bookmaker)
        if not isinstance(book, dict):
            continue
        line_raw = book.get("overUnder")
        try:
            market_line = float(line_raw)
        except (TypeError, ValueError):
            continue
        score = _player_score(player_name, odd)
        if score >= 0.62:
            candidates.append((score, odd, book | {"marketLine": market_line}))

    if not candidates:
        return None

    candidates.sort(key=lambda item: item[0], reverse=True)
    best_score, best_odd, best_book = candidates[0]
    side_lines: dict[str, Any] = {}
    side_available: dict[str, bool] = {}
    updated_at = best_book.get("lastUpdatedAt")
    for _, odd, book in candidates:
        side = str(odd.get("sideID") or "").lower()
        if side in {"over", "under"}:
            side_lines[side] = book.get("odds")
            side_available[side] = bool(book.get("available"))
            updated_at = updated_at or book.get("lastUpdatedAt")

    teams = event.get("teams") or {}
    home_name = ((teams.get("home") or {}).get("names") or {}).get("long", "")
    away_name = ((teams.get("away") or {}).get("names") or {}).get("long", "")
    return {
        "source": "SportsGameOdds",
        "bookmaker": "PrizePicks" if bookmaker == "prizepicks" else "Underdog",
        "providerBookmaker": bookmaker,
        "eventId": event.get("eventID"),
        "leagueId": event.get("leagueID"),
        "eventStart": ((event.get("status") or {}).get("startsAt")),
        "homeTeam": home_name,
        "awayTeam": away_name,
        "playerName": player_name,
        "matchedPlayer": best_odd.get("marketName", ""),
        "statId": stat_id,
        "propType": prop_type,
        "marketName": best_odd.get("marketName", ""),
        "marketLine": best_book.get("marketLine"),
        "lineDifference": round(best_book.get("marketLine", 0) - float(entered_line), 2),
        "overOdds": side_lines.get("over"),
        "underOdds": side_lines.get("under"),
        "available": bool(any(side_available.values())),
        "lastUpdatedAt": updated_at,
        "matchScore": round(best_score, 3),
    }


async def lookup_soccer_market_context(
    *,
    player_name: str,
    prop_type: str,
    entered_line: float,
    fixture: dict[str, Any],
) -> dict[str, Any] | None:
    """Return optional SGO market context for the already-verified fixture."""
    if not os.environ.get("SPORTSGAMEODDS_API_KEY", "").strip():
        return None
    stat_id = _STAT_MAP.get(prop_type)
    if not stat_id or not fixture:
        return None

    fixture_date = _parse_datetime(fixture.get("matchDate"))
    if not fixture_date:
        return None
    events = await _fetch_events(
        league_id=int(fixture.get("matchLeagueId") or 0),
        league_name=str(fixture.get("matchLeague") or ""),
        fixture_date=fixture_date,
        home_team=str(fixture.get("fixtureHomeName") or ""),
        away_team=str(fixture.get("fixtureAwayName") or ""),
    )
    if not events:
        return None

    for event in events:
        markets = {
            bookmaker: _extract_bookmaker_market(
                event,
                player_name=player_name,
                stat_id=stat_id,
                prop_type=prop_type,
                entered_line=entered_line,
                bookmaker=bookmaker,
            )
            for bookmaker in _BOOKMAKERS
        }
        markets = {key: value for key, value in markets.items() if value}
        if markets:
            primary = markets.get("prizepicks") or markets.get("underdog")
            if primary:
                primary["bookmakers"] = markets
                primary["providerCoverage"] = list(markets.keys())
                return primary
    return None


def _board_player_name(market_name: str, stat_id: str) -> str:
    """Recover the provider display name from a player market label."""
    label = _BOARD_STAT_LABELS.get(stat_id, "")
    text = str(market_name or "").strip()
    if label:
        suffix = re.compile(
            rf"\s+{re.escape(label)}\s+Over/Under(?:\s+\([^)]*\))?$",
            flags=re.IGNORECASE,
        )
        cleaned = suffix.sub("", text).strip()
        if cleaned and cleaned != text:
            return cleaned
    return text


def _board_event_is_open(event: dict[str, Any], now: datetime) -> bool:
    status = event.get("status") or {}
    if status.get("ended") or status.get("completed") or status.get("cancelled"):
        return False
    starts_at = _parse_datetime(status.get("startsAt"))
    if not starts_at:
        return False
    # Keep live events and upcoming events, but never let an old provider
    # record enter the discovery board because its market is still cached.
    return starts_at >= now - timedelta(hours=4)


def _extract_board_markets(
    event: dict[str, Any],
    *,
    now: datetime,
    api_league_id: int | None,
    league_name: str,
) -> list[dict[str, Any]]:
    if not _board_event_is_open(event, now):
        return []

    teams = event.get("teams") or {}
    home_name = ((teams.get("home") or {}).get("names") or {}).get("long", "")
    away_name = ((teams.get("away") or {}).get("names") or {}).get("long", "")
    status = event.get("status") or {}
    grouped: dict[str, dict[str, Any]] = {}

    for odd in (event.get("odds") or {}).values():
        if not isinstance(odd, dict):
            continue
        stat_id = str(odd.get("statID") or "")
        prop_type = _BOARD_STAT_TYPES.get(stat_id)
        if not prop_type or not odd.get("playerID"):
            continue
        if odd.get("cancelled") or not odd.get("started") and odd.get("ended"):
            continue

        player_name = _board_player_name(str(odd.get("marketName") or ""), stat_id)
        if not player_name:
            continue

        bookmaker_data: dict[str, dict[str, Any]] = {}
        for bookmaker in _BOOKMAKERS:
            book = (odd.get("byBookmaker") or {}).get(bookmaker)
            if not isinstance(book, dict):
                continue
            try:
                market_line = float(book.get("overUnder"))
            except (TypeError, ValueError):
                continue
            # The board is intentionally an availability surface, not an
            # archive. A provider market must be live at one supported book.
            if not book.get("available"):
                continue
            bookmaker_data[bookmaker] = {
                "line": market_line,
                "odds": book.get("odds"),
                "lastUpdatedAt": book.get("lastUpdatedAt"),
            }
        if not bookmaker_data:
            continue

        key = "|".join(
            [
                str(event.get("eventID") or ""),
                str(odd.get("playerID") or odd.get("statEntityID") or ""),
                stat_id,
            ]
        )
        side = str(odd.get("sideID") or "").lower()
        item = grouped.setdefault(
            key,
            {
                "eventId": event.get("eventID"),
                "leagueId": api_league_id,
                "leagueName": league_name or event.get("leagueID") or "Soccer",
                "eventStart": status.get("startsAt"),
                "homeTeam": home_name,
                "awayTeam": away_name,
                "playerName": player_name,
                "playerProviderId": odd.get("playerID") or odd.get("statEntityID"),
                "propType": prop_type,
                "marketName": odd.get("marketName") or "",
                "statId": stat_id,
                "bookmakers": {},
                "providerCoverage": [],
                "overOdds": None,
                "underOdds": None,
            },
        )
        for bookmaker, book in bookmaker_data.items():
            item["bookmakers"][bookmaker] = book
            if bookmaker not in item["providerCoverage"]:
                item["providerCoverage"].append(bookmaker)
            item["marketLine"] = book["line"]
        if side in {"over", "under"}:
            item[f"{side}Odds"] = bookmaker_data.get("prizepicks", next(iter(bookmaker_data.values())))["odds"]

    return list(grouped.values())


async def list_soccer_market_board(
    *,
    hours: int = 72,
    league_id: int | None = None,
    limit: int = 60,
) -> list[dict[str, Any]]:
    """Return currently available soccer player markets for discovery."""
    api_key = os.environ.get("SPORTSGAMEODDS_API_KEY", "").strip()
    if not api_key:
        return []

    now = datetime.now(timezone.utc)
    safe_hours = max(6, min(int(hours or 72), 168))
    safe_limit = max(1, min(int(limit or 60), 100))
    cache_key = f"{league_id or 'all'}|{safe_hours}"
    async with _CACHE_LOCK:
        cached = _BOARD_CACHE.get(cache_key)
        if cached and time.monotonic() - cached[0] < _CACHE_TTL_SECONDS:
            return cached[1][:safe_limit]

    params: dict[str, Any] = {
        "bookmakerID": ",".join(_BOOKMAKERS),
        "limit": 100,
        "startsAfter": now.isoformat().replace("+00:00", "Z"),
        "startsBefore": (now + timedelta(hours=safe_hours)).isoformat().replace("+00:00", "Z"),
        "sportID": "SOCCER",
        "apiKey": api_key,
    }
    if league_id:
        params["leagueID"] = _LEAGUE_MAP.get(int(league_id), str(league_id))

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(12.0, connect=4.0)) as client:
            response = await client.get(f"{_BASE_URL}/events/", params=params)
            response.raise_for_status()
            payload = response.json()
        events = payload.get("data") or []
        if not isinstance(events, list):
            events = []
    except Exception as exc:
        print(f"[SGO BOARD] unavailable: {type(exc).__name__}: {exc}")
        return []

    inverse_leagues = {value: key for key, value in _LEAGUE_MAP.items()}
    board: list[dict[str, Any]] = []
    for event in events:
        provider_league = str(event.get("leagueID") or "")
        event_api_league = int(league_id) if league_id else inverse_leagues.get(provider_league)
        board.extend(
            _extract_board_markets(
                event,
                now=now,
                api_league_id=event_api_league,
                league_name=provider_league.replace("_", " ").title(),
            )
        )

    board.sort(key=lambda item: _parse_datetime(item.get("eventStart")) or now)
    board = board[:safe_limit]
    async with _CACHE_LOCK:
        _BOARD_CACHE[cache_key] = (time.monotonic(), board)
    return board