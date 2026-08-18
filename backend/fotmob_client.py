"""Small, bounded FotMob reader for exact finished-match player stats.

FotMob's public match page includes the same player-level fraction that users
see in its match statistics UI (for example, accurate passes 45/53).  This is
used as an independent corroboration source for an authenticated settlement
confirmation, not as a replacement for the canonical API-Football fixture ID.

The page is intentionally parsed from ``__NEXT_DATA__`` rather than from CSS
markup.  The latter is presentation-only and has changed several times.
"""

from __future__ import annotations

import html
import json
import re
import unicodedata
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

import httpx


_BASE_URL = "https://www.fotmob.com"
_USER_AGENT = (
    "ReversePicks settlement verifier/1.0 "
    "(exact finished-match stat corroboration)"
)
_HTTP_TIMEOUT = httpx.Timeout(connect=4.0, read=12.0, write=4.0, pool=4.0)
_CACHE_TTL_SECONDS = 300.0
_cache: dict[str, tuple[float, Any]] = {}


def _now_ts() -> float:
    return datetime.now(timezone.utc).timestamp()


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.casefold()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _team_matches(left: Any, right: Any) -> bool:
    a = _norm(left)
    b = _norm(right)
    if not a or not b:
        return False
    if a == b:
        return True
    # Provider labels sometimes add a club prefix/suffix. Do not use this
    # relaxed comparison for short names, where false matches are common.
    return len(a) >= 5 and len(b) >= 5 and (a in b or b in a)


def _player_match_score(query: Any, candidate: Any) -> int:
    q = _norm(query)
    c = _norm(candidate)
    if not q or not c:
        return -1
    if q == c:
        return 100
    q_tokens = set(q.split())
    c_tokens = set(c.split())
    if not q_tokens or not c_tokens or q.split()[-1] != c.split()[-1]:
        return -1
    # This accepts Sergio Damián Barreto ↔ Sergio Barreto while still
    # requiring the surname and all shorter-name tokens to agree.
    if c_tokens <= q_tokens or q_tokens <= c_tokens:
        return 80 + len(q_tokens & c_tokens)
    return -1


def _candidate_dates(fixture_date: Any) -> list[date]:
    raw = str(fixture_date or "").strip()
    if not raw:
        return []
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        parsed = parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        base = parsed.astimezone(timezone.utc).date()
    except (TypeError, ValueError):
        return []
    return [base, base + timedelta(days=1), base - timedelta(days=1)]


def _cache_get(key: str) -> Any:
    item = _cache.get(key)
    if not item:
        return None
    created, value = item
    if _now_ts() - created >= _CACHE_TTL_SECONDS:
        _cache.pop(key, None)
        return None
    return value


def _cache_put(key: str, value: Any) -> Any:
    _cache[key] = (_now_ts(), value)
    return value


async def _get_json(path: str, params: Optional[dict[str, Any]] = None) -> Any:
    query = ""
    if params:
        query = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    key = f"json:{path}?{query}"
    cached = _cache_get(key)
    if cached is not None:
        return cached
    url = f"{_BASE_URL}{path}"
    try:
        async with httpx.AsyncClient(
            timeout=_HTTP_TIMEOUT,
            follow_redirects=True,
            headers={
                "User-Agent": _USER_AGENT,
                "Accept": "application/json",
            },
        ) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            return _cache_put(key, response.json())
    except Exception as exc:
        print(f"[FOTMOB] JSON request failed ({path}): {exc}")
        return None


async def _get_html(path: str) -> Optional[str]:
    key = f"html:{path}"
    cached = _cache_get(key)
    if cached is not None:
        return cached
    url = f"{_BASE_URL}{path}"
    try:
        async with httpx.AsyncClient(
            timeout=_HTTP_TIMEOUT,
            follow_redirects=True,
            headers={
                "User-Agent": _USER_AGENT,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "en-US,en;q=0.9",
            },
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
            return _cache_put(key, response.text)
    except Exception as exc:
        print(f"[FOTMOB] match page request failed ({path}): {exc}")
        return None


def _matches_from_day(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    matches: list[dict[str, Any]] = []
    for league in payload.get("leagues") or []:
        if not isinstance(league, dict):
            continue
        for match in league.get("matches") or []:
            if isinstance(match, dict):
                matches.append(match)
    return matches


async def _find_match_id(
    fixture_date: Any,
    home_name: str,
    away_name: str,
) -> Optional[int]:
    for candidate_date in _candidate_dates(fixture_date):
        payload = await _get_json(
            "/api/data/matches",
            {"date": candidate_date.strftime("%Y%m%d")},
        )
        for match in _matches_from_day(payload):
            home = match.get("home") or {}
            away = match.get("away") or {}
            if (
                _team_matches(home.get("name") or home.get("longName"), home_name)
                and _team_matches(away.get("name") or away.get("longName"), away_name)
            ):
                try:
                    return int(match["id"])
                except (KeyError, TypeError, ValueError):
                    continue
    return None


def _next_data_from_html(page_html: str) -> Optional[dict[str, Any]]:
    match = re.search(
        r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
        page_html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return None
    try:
        data = json.loads(html.unescape(match.group(1)))
        return data if isinstance(data, dict) else None
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"[FOTMOB] __NEXT_DATA__ parse failed: {exc}")
        return None


def _player_stats_from_page(
    page_html: str,
    player_name: str,
    team_name: str,
) -> Optional[dict[str, Any]]:
    data = _next_data_from_html(page_html)
    page_props = ((data or {}).get("props") or {}).get("pageProps") or {}
    content = page_props.get("content") or {}
    players = content.get("playerStats") or {}
    if not isinstance(players, dict):
        return None

    candidates: list[tuple[int, int, dict[str, Any]]] = []
    for raw_player_id, player in players.items():
        if not isinstance(player, dict):
            continue
        name_score = _player_match_score(player_name, player.get("name"))
        if name_score < 0 or not _team_matches(player.get("teamName"), team_name):
            continue
        try:
            provider_player_id = int(player.get("id") or raw_player_id)
        except (TypeError, ValueError):
            continue
        candidates.append((name_score, provider_player_id, player))
    if not candidates:
        return None

    _, provider_player_id, player = max(candidates, key=lambda row: row[0])
    return {
        "providerPlayerId": provider_player_id,
        "player": player,
    }


def _flatten_player_stats(player: dict[str, Any]) -> dict[str, tuple[str, dict[str, Any]]]:
    flattened: dict[str, tuple[str, dict[str, Any]]] = {}
    for section in player.get("stats") or []:
        if not isinstance(section, dict):
            continue
        for label, entry in (section.get("stats") or {}).items():
            if not isinstance(entry, dict):
                continue
            stat = entry.get("stat")
            if not isinstance(stat, dict):
                continue
            key = str(entry.get("key") or "")
            flattened[_norm(label)] = (str(label), stat)
            if key:
                flattened[_norm(key)] = (str(label), stat)
    return flattened


_STAT_LOOKUP: dict[str, tuple[str, str]] = {
    # The app's soccer pass props are attempts, so use FotMob's fraction
    # denominator (53), not its accurate/completed numerator (45).
    "pass_attempts": ("accurate passes", "total"),
    "passes": ("accurate passes", "total"),
    "shots": ("total shots", "value"),
    "shots_on_target": ("shots on target", "value"),
    "key_passes": ("chances created", "value"),
    "tackles": ("tackles", "value"),
    "clearances": ("clearances", "value"),
    "interceptions": ("interceptions", "value"),
    "blocks": ("blocks", "value"),
    "dribbles": ("successful dribbles", "value"),
    "dribbles_success": ("successful dribbles", "value"),
    "fouls_committed": ("fouls committed", "value"),
    "fouls_drawn": ("was fouled", "value"),
    "duels_won": ("duels won", "value"),
    "goals": ("goals", "value"),
    "assists": ("assists", "value"),
}


def _read_stat_value(
    player: dict[str, Any],
    prop_type: str,
) -> Optional[tuple[int | float, str]]:
    lookup = _STAT_LOOKUP.get(prop_type)
    if not lookup:
        return None
    label, field = lookup
    flattened = _flatten_player_stats(player)
    entry = flattened.get(_norm(label))
    if not entry:
        return None
    actual_label, stat = entry
    value = stat.get(field)
    if value is None:
        return None
    try:
        numeric = float(value)
        normalized: int | float = int(numeric) if numeric.is_integer() else numeric
    except (TypeError, ValueError):
        return None
    return normalized, actual_label


async def fetch_exact_player_stat(
    *,
    fixture_id: int,
    fixture_date: str,
    home_name: str,
    away_name: str,
    player_id: int,
    player_name: str,
    team_name: str,
    prop_type: str,
) -> Optional[dict[str, Any]]:
    """Return an exact FotMob value, or ``None`` when the source is incomplete.

    The caller must still apply its normal DNP/incomplete-stat guards.  A
    missing FotMob row is deliberately not converted into zero.
    """
    provider_match_id = await _find_match_id(fixture_date, home_name, away_name)
    if provider_match_id is None:
        print(
            f"[FOTMOB] no exact match for {home_name} v {away_name} "
            f"(fixture={fixture_id})"
        )
        return None

    page_html = await _get_html(f"/match/{provider_match_id}")
    if not page_html:
        return None
    player_row = _player_stats_from_page(page_html, player_name, team_name)
    if not player_row:
        print(
            f"[FOTMOB] no exact player row for {player_name} "
            f"in match={provider_match_id}"
        )
        return None

    player = player_row["player"]
    stat_result = _read_stat_value(player, prop_type)
    if not stat_result:
        print(
            f"[FOTMOB] no {prop_type} stat for {player_name} "
            f"in match={provider_match_id}"
        )
        return None
    actual_value, stat_label = stat_result

    flattened = _flatten_player_stats(player)
    minutes_entry = flattened.get(_norm("Minutes played"))
    minutes_played = 0
    if minutes_entry:
        try:
            minutes_played = int(float(minutes_entry[1].get("value") or 0))
        except (TypeError, ValueError):
            minutes_played = 0

    provider_player_id = player_row["providerPlayerId"]
    return {
        "actualValue": actual_value,
        "minutesPlayed": minutes_played,
        "providerMatchId": provider_match_id,
        "providerPlayerId": provider_player_id,
        "settlementSource": {
            "provider": "fotmob",
            "fixtureId": fixture_id,
            "providerFixtureId": provider_match_id,
            "playerId": player_id,
            "providerPlayerId": provider_player_id,
            "propType": prop_type,
            "statPath": (
                f"content.playerStats.{provider_player_id}."
                f"stats['{stat_label}'].stat."
                f"{'total' if prop_type in ('pass_attempts', 'passes') else 'value'}"
            ),
            "fixtureStatus": "FT",
            "verified": True,
            "verificationMethod": "exact_team_date_player_match",
            "recordedAt": datetime.now(timezone.utc).isoformat(),
        },
    }