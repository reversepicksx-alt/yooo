"""Cached Understat team-pressure enrichment.

Understat is a supplemental, team-level source.  It is intentionally kept
outside the numeric projection path: the prediction remains API-Football and
Bayesian-ledger authoritative until this signal has settled-pick validation.

The public Understat league page loads JSON from ``/getLeagueData/{slug}/{season}``.
There is no documented public API contract, so requests are bounded, cached, and
fail open.  Only the six leagues that Understat currently exposes are mapped.
"""

from __future__ import annotations

import re
import time
import asyncio
from datetime import datetime, timezone
from typing import Any
import unicodedata

import httpx

from config import db


UNDERSTAT_LEAGUES: dict[int, dict[str, Any]] = {
    39: {"slug": "EPL", "name": "Premier League"},
    140: {"slug": "La_liga", "name": "La Liga"},
    78: {"slug": "Bundesliga", "name": "Bundesliga"},
    135: {"slug": "Serie_A", "name": "Serie A"},
    61: {"slug": "Ligue_1", "name": "Ligue 1"},
    235: {"slug": "RFPL", "name": "Russian Premier League"},
}

_CACHE_TTL_SECONDS = 12 * 60 * 60
_HTTP_TIMEOUT_SECONDS = 8.0
_MEMORY_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}

_TEAM_ALIASES = {
    "sevilla fc": "sevilla",
    "sevilla futbol club": "sevilla",
    "rayo vallecano de madrid": "rayo vallecano",
    "athletic bilbao": "athletic club",
    "athletic club bilbao": "athletic club",
    "atletico madrid": "atletico de madrid",
    "inter milan": "internazionale",
    "internazionale milano": "internazionale",
    "paris saint germain": "paris saint-germain",
    "psg": "paris saint-germain",
    "borussia monchengladbach": "borussia m'gladbach",
    "bayern munich": "bayern munich",
    "tottenham hotspur": "tottenham",
    "manchester united": "manchester united",
}


def _clean_name(value: Any) -> str:
    raw = unicodedata.normalize("NFKD", str(value or ""))
    raw = "".join(ch for ch in raw if not unicodedata.combining(ch))
    raw = raw.lower().replace("&", " and ")
    raw = re.sub(r"[^a-z0-9]+", " ", raw).strip()
    return _TEAM_ALIASES.get(raw, raw)


def _parse_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        dt = value
    else:
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
                try:
                    dt = datetime.strptime(raw, fmt)
                    break
                except ValueError:
                    continue
            else:
                return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _number(value: Any) -> float | None:
    try:
        if value is None or str(value).strip() == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _ratio(raw: Any) -> float | None:
    if not isinstance(raw, dict):
        return None
    attacking = _number(raw.get("att"))
    defensive = _number(raw.get("def"))
    if attacking is None or defensive is None or defensive <= 0:
        return None
    return attacking / defensive


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def aggregate(field: str) -> float | None:
        numer = 0.0
        denom = 0.0
        seen = False
        for row in rows:
            raw = row.get(field)
            if not isinstance(raw, dict):
                continue
            att = _number(raw.get("att"))
            defensive = _number(raw.get("def"))
            if att is None or defensive is None or defensive <= 0:
                continue
            numer += att
            denom += defensive
            seen = True
        return round(numer / denom, 2) if seen and denom > 0 else None

    def average(field: str) -> float | None:
        values = [_number(row.get(field)) for row in rows]
        values = [value for value in values if value is not None]
        return round(sum(values) / len(values), 3) if values else None

    return {
        "sampleSize": len(rows),
        "ppda": aggregate("ppda"),
        "oppPpda": aggregate("ppda_allowed"),
        "xG": average("xG"),
        "xGA": average("xGA"),
        "npxG": average("npxG"),
        "npxGA": average("npxGA"),
        "deep": average("deep"),
        "deepAllowed": average("deep_allowed"),
        "dateStart": rows[0].get("date") if rows else None,
        "dateEnd": rows[-1].get("date") if rows else None,
    }


def _rows_until(history: Any, as_of: datetime) -> list[dict[str, Any]]:
    rows: list[tuple[datetime, dict[str, Any]]] = []
    for raw in history or []:
        if not isinstance(raw, dict):
            continue
        dt = _parse_dt(raw.get("date"))
        if dt is None or dt > as_of:
            continue
        rows.append((dt, raw))
    rows.sort(key=lambda item: item[0])
    return [row for _, row in rows]


def _team_packet(team: dict[str, Any], *, venue: str, as_of: datetime) -> dict[str, Any]:
    history = _rows_until(team.get("history"), as_of)
    venue_rows = [row for row in history if row.get("h_a") == ("h" if venue == "home" else "a")]
    return {
        "understatTeamId": str(team.get("id") or ""),
        "name": team.get("title"),
        "overall": _summary(history),
        "venue": _summary(venue_rows),
        "recent5": _summary(history[-5:]),
        "recent10": _summary(history[-10:]),
    }


def _empty_team_packet(team_name: str) -> dict[str, Any]:
    """Keep opponent-only Understat coverage explicit when target is absent."""
    return {
        "understatTeamId": None,
        "name": team_name,
        "overall": _summary([]),
        "venue": _summary([]),
        "recent5": _summary([]),
        "recent10": _summary([]),
        "status": "unavailable",
        "reason": "team_not_in_understat_season",
    }


def _find_team(teams: Any, requested_name: str) -> dict[str, Any] | None:
    if not isinstance(teams, dict):
        return None
    wanted = _clean_name(requested_name)
    if not wanted:
        return None
    exact: list[dict[str, Any]] = []
    candidates: list[tuple[int, dict[str, Any]]] = []
    for raw in teams.values():
        if not isinstance(raw, dict):
            continue
        title = _clean_name(raw.get("title"))
        if title == wanted:
            exact.append(raw)
            continue
        wanted_tokens = set(wanted.split())
        title_tokens = set(title.split())
        overlap = len(wanted_tokens & title_tokens)
        if overlap and (wanted in title or title in wanted):
            candidates.append((overlap, raw))
    return exact[0] if exact else max(candidates, key=lambda item: item[0])[1] if candidates else None


def _press_percentile(value: float | None, values: list[float]) -> float | None:
    if value is None or not values:
        return None
    # Lower PPDA means a stronger press.  Percentile therefore measures the
    # share of league teams with a weaker/higher PPDA value.
    return round(100.0 * sum(other >= value for other in values) / len(values), 1)


def _press_label(percentile: float | None) -> str:
    if percentile is None:
        return "unavailable"
    if percentile >= 75:
        return "high"
    if percentile >= 55:
        return "above average"
    if percentile <= 25:
        return "low"
    return "average"


async def _read_cached_league(slug: str, season: int) -> dict[str, Any] | None:
    key = f"understat:{slug}:{season}"
    memory = _MEMORY_CACHE.get(key)
    if memory:
        return memory[1]

    try:
        cached = await db.understat_cache.find_one({"_key": key}, {"_id": 0})
        if cached:
            payload = cached.get("payload")
            if isinstance(payload, dict) and isinstance(payload.get("teams"), dict):
                _MEMORY_CACHE[key] = (float(cached.get("_ts") or time.time()), payload)
                return payload
    except Exception as exc:
        print(f"[UNDERSTAT] cache read skipped: {type(exc).__name__}")

    return None


async def _refresh_league(slug: str, season: int) -> dict[str, Any] | None:
    """Refresh one league payload from Understat for the background worker."""
    key = f"understat:{slug}:{season}"
    now = time.time()
    try:
        url = f"https://understat.com/getLeagueData/{slug}/{season}"
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; ReversePicks/1.0)",
            "Accept": "application/json,text/javascript,*/*;q=0.01",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"https://understat.com/league/{slug}",
        }
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SECONDS, follow_redirects=True) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            raw = response.json()
        payload = {
            "teams": raw.get("teams") if isinstance(raw, dict) else {},
            "fetchedAt": datetime.now(timezone.utc).isoformat(),
        }
        if not isinstance(payload["teams"], dict) or not payload["teams"]:
            return None
        _MEMORY_CACHE[key] = (now, payload)
        try:
            await db.understat_cache.update_one(
                {"_key": key},
                {
                    "$set": {
                        "_key": key,
                        "_ts": now,
                        "source": "understat",
                        "leagueSlug": slug,
                        "season": season,
                        "payload": payload,
                    }
                },
                upsert=True,
            )
        except Exception as exc:
            # Atlas quota or a transient write failure must never remove the
            # live prediction path; the in-process cache is still usable.
            print(f"[UNDERSTAT] cache write skipped: {type(exc).__name__}")
        return payload
    except Exception as exc:
        print(f"[UNDERSTAT] fetch failed {slug}/{season}: {type(exc).__name__}")
        return None


async def _load_league(slug: str, season: int) -> dict[str, Any] | None:
    """Load a payload for a refresh job, using the provider only when stale."""
    cached = await _read_cached_league(slug, season)
    if cached:
        fetched_at = _parse_dt(cached.get("fetchedAt"))
        cache_stamp = _MEMORY_CACHE.get(f"understat:{slug}:{season}", (0, None))[0]
        age = (
            (datetime.now(timezone.utc) - fetched_at).total_seconds()
            if fetched_at is not None
            else time.time() - float(cache_stamp or 0)
        )
        if age < _CACHE_TTL_SECONDS:
            return cached
    return await _refresh_league(slug, season)


async def refresh_understat_cache(
    *,
    seasons: tuple[int, ...] = (2025, 2024),
) -> dict[str, int]:
    """Refresh covered league snapshots outside the prediction request path."""
    refreshed = 0
    failed = 0
    for league in UNDERSTAT_LEAGUES.values():
        for season in seasons:
            payload = await _refresh_league(league["slug"], season)
            if payload:
                refreshed += 1
            else:
                failed += 1
            await asyncio.sleep(0.25)
    return {"refreshed": refreshed, "failed": failed}


def _unavailable(league_id: int | None, reason: str) -> dict[str, Any]:
    return {
        "status": "unavailable",
        "source": None,
        "leagueId": league_id,
        "reason": reason,
        "projectionInfluence": "explanation_only",
    }


async def fetch_understat_pressure_context(
    *,
    league_id: int | None,
    season: int | None,
    team_name: str,
    opponent_name: str,
    venue: str,
    as_of: Any = None,
) -> dict[str, Any]:
    """Return team-level pressure context for one verified fixture.

    The response is explicitly not player-level marking evidence.  ``team`` is
    the player's team and ``opponent`` is the fixture opponent.  The opponent's
    venue is automatically inverted for the same fixture.
    """
    try:
        lid = int(league_id or 0)
    except (TypeError, ValueError):
        lid = 0
    league = UNDERSTAT_LEAGUES.get(lid)
    if not league:
        return _unavailable(lid or None, "league_not_covered_by_understat")

    target_venue = "away" if str(venue or "").lower() == "away" else "home"
    opponent_venue = "away" if target_venue == "home" else "home"
    cutoff = _parse_dt(as_of) or datetime.now(timezone.utc)

    try:
        requested_season = int(season or 0)
    except (TypeError, ValueError):
        requested_season = 0
    if requested_season <= 0:
        requested_season = datetime.now(timezone.utc).year - 1

    payload = None
    used_season = None
    target_raw = None
    opponent_raw = None
    # A prediction can arrive in the first weeks of a new season.  Try the
    # requested season, then the latest two completed seasons.
    for candidate in dict.fromkeys((requested_season, requested_season - 1, requested_season - 2)):
        if candidate <= 0:
            continue
        # Predictions are strictly cache-only.  The background refresh loop
        # owns all provider requests so a user request can never scrape
        # Understat or wait on its response.
        candidate_payload = await _read_cached_league(league["slug"], candidate)
        if not candidate_payload:
            continue
        candidate_opponent = _find_team(candidate_payload.get("teams"), opponent_name)
        candidate_target = _find_team(candidate_payload.get("teams"), team_name)
        # The requested club can be outside the Understat league while the
        # opponent is covered (e.g. a Segunda club facing a former La Liga
        # side). Opponent PPDA does not require the target club's own history.
        if candidate_opponent:
            payload = candidate_payload
            used_season = candidate
            target_raw = candidate_target
            opponent_raw = candidate_opponent
            if _rows_until(candidate_opponent.get("history"), cutoff):
                break

    if not payload or not opponent_raw or used_season is None:
        return _unavailable(lid, "team_not_found_or_season_unavailable")

    opponent_history = _rows_until(opponent_raw.get("history"), cutoff)
    target_history = (
        _rows_until(target_raw.get("history"), cutoff)
        if target_raw
        else []
    )
    if not opponent_history:
        return _unavailable(lid, "no_completed_matches_before_cutoff")

    target_packet = (
        _team_packet(target_raw, venue=target_venue, as_of=cutoff)
        if target_raw
        else _empty_team_packet(team_name)
    )
    opponent_packet = _team_packet(opponent_raw, venue=opponent_venue, as_of=cutoff)

    league_ppda: list[float] = []
    for raw_team in (payload.get("teams") or {}).values():
        if not isinstance(raw_team, dict):
            continue
        value = _summary(_rows_until(raw_team.get("history"), cutoff)).get("ppda")
        if value is not None:
            league_ppda.append(value)
    opponent_press = opponent_packet["venue"].get("ppda")
    percentile = _press_percentile(opponent_press, league_ppda)
    latest_dates = [
        row.get("date") or ""
        for row in (target_history[-1:] + opponent_history[-1:])
    ]
    result = {
        "status": "verified_team_level",
        "availability": "available",
        "source": "understat",
        "sourceUrl": f"https://understat.com/league/{league['slug']}/{used_season}",
        "leagueId": lid,
        "league": league["name"],
        "leagueSlug": league["slug"],
        "season": used_season,
        "asOf": None,
        "cutoff": cutoff.isoformat(),
        "venue": target_venue,
        "projectionInfluence": "explanation_only",
        "team": target_packet,
        "opponent": opponent_packet,
        "opponentPress": {
            "ppda": opponent_press,
            "label": _press_label(percentile),
            "leaguePercentile": percentile,
            "sampleSize": opponent_packet["venue"].get("sampleSize", 0),
            "venue": opponent_venue,
        },
        "targetTeamOppPpda": target_packet["venue"].get("oppPpda"),
        "pressureRouteVerified": False,
        "coverage": {
            "targetTeamMatches": len(target_history),
            "opponentMatches": len(opponent_history),
            "leagueTeamCount": len(league_ppda),
        },
    }
    fetched_at = _parse_dt(payload.get("fetchedAt"))
    result["freshness"] = (
        "fresh"
        if fetched_at is not None
        and (datetime.now(timezone.utc) - fetched_at).total_seconds() < _CACHE_TTL_SECONDS
        else "stale"
    )
    result["asOf"] = max(latest_dates) if latest_dates else None
    try:
        await db.understat_pressure_cache.update_one(
            {
                "_key": (
                    f"understat-pressure:{lid}:{used_season}:"
                    f"{opponent_raw.get('id')}:{opponent_venue}"
                )
            },
            {
                "$set": {
                    "_key": (
                        f"understat-pressure:{lid}:{used_season}:"
                        f"{opponent_raw.get('id')}:{opponent_venue}"
                    ),
                    "source": "understat",
                    "leagueId": lid,
                    "league": league["name"],
                    "season": used_season,
                    "teamName": team_name,
                    "opponentName": opponent_name,
                    "venue": opponent_venue,
                    "ppda": opponent_press,
                    "sampleSize": opponent_packet["venue"].get("sampleSize", 0),
                    "asOf": result["asOf"],
                    "updatedAt": datetime.now(timezone.utc).isoformat(),
                    "packet": result,
                }
            },
            upsert=True,
        )
    except Exception as exc:
        # Pressure evidence is regenerable; Atlas/cache write failures must
        # never remove a verified response from this prediction.
        print(f"[UNDERSTAT] pressure cache write skipped: {type(exc).__name__}")
    return result