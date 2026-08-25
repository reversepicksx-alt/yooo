"""Bounded API-Football evidence assembly for the causal recommendation gate.

The prediction route already gathers player logs and position comparison rows.
This module makes that evidence explicit, enriches its fixture identities with
provider event/lineup/stat packets, and never treats a missing response as a
measured zero.
"""
from __future__ import annotations

import asyncio as aio
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from utils import api_football_request, is_quota_exhausted, priority_api_football_request
from tactical_evidence import infer_grid_position, normalize_observed_position

MAX_TARGET_MATCHES = 20
MAX_OPPONENT_MATCHES = 20
_DETAIL_TIMEOUT_SECONDS = 18.0
_DETAIL_CONCURRENCY = 4


def _fixture_id(row: dict[str, Any]) -> int | None:
    for key in ("fixtureId", "fixture_id", "id"):
        try:
            value = int(row.get(key))
            if value:
                return value
        except (TypeError, ValueError):
            continue
    return None


def _row_date(row: dict[str, Any]) -> str:
    return str(row.get("date") or row.get("fixtureDate") or "")[:25]


def _before_cutoff(row: dict[str, Any], cutoff: str | None) -> bool:
    """Do not let a replay read a fixture at/after its target kickoff."""
    if not cutoff:
        return True
    date = _row_date(row)
    return not date or date < cutoff


def _sorted_limited(rows: list[dict], limit: int, cutoff: str | None) -> list[dict]:
    filtered = [
        row for row in rows if isinstance(row, dict) and _before_cutoff(row, cutoff)
    ]
    return sorted(filtered, key=_row_date, reverse=True)[:limit]


def _event_tags(events: list[dict[str, Any]], team_id: int | None) -> dict[str, Any]:
    tags: list[str] = []
    goal_minutes: list[int] = []
    for event in events if isinstance(events, list) else []:
        detail = str(event.get("detail") or "").lower()
        event_type = str(event.get("type") or "").lower()
        minute = ((event.get("time") or {}).get("elapsed"))
        if event_type == "card" and ("red" in detail or "second yellow" in detail):
            tags.append("red_card")
        if event_type == "goal" and isinstance(minute, int):
            goal_minutes.append(minute)
        if event_type == "goal" and "penalty" in detail:
            tags.append("penalty")
    return {
        "eventTags": sorted(set(tags)),
        "earlyGoal": bool(any(minute <= 20 for minute in goal_minutes)),
        "goalMinutes": goal_minutes,
        "eventCoverage": "available" if isinstance(events, list) else "UNKNOWN",
    }


def _lineup_metadata(lineups: list[dict[str, Any]], team_id: int | None) -> dict[str, Any]:
    for item in lineups if isinstance(lineups, list) else []:
        team = item.get("team") or {}
        if team_id and team.get("id") != team_id:
            continue
        return {
            "formation": item.get("formation") or None,
            "coach": ((item.get("coach") or {}).get("name")) or None,
            "lineupCoverage": "available",
        }
    return {"formation": None, "coach": None, "lineupCoverage": "UNKNOWN"}


def _statistics_metadata(stats: list[dict[str, Any]], team_id: int | None) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for item in stats if isinstance(stats, list) else []:
        team = item.get("team") or {}
        if team_id and team.get("id") != team_id:
            continue
        for stat in item.get("statistics") or []:
            name = str(stat.get("type") or "").lower()
            value = stat.get("value")
            if name == "ball possession":
                values["teamPossession"] = value
            elif name == "total passes":
                values["teamPasses"] = value
            elif name == "total shots":
                values["teamShots"] = value
            elif name == "shots on goal":
                values["teamShotsOnTarget"] = value
        break
    values["statisticsCoverage"] = "available" if values else "UNKNOWN"
    return values


async def _fixture_detail(fixture_id: int, team_id: int | None) -> tuple[int, dict[str, Any]]:
    """Fetch a fixture's observed post-match evidence for historical samples."""
    players, lineups, stats, events = await aio.gather(
        api_football_request("fixtures/players", {"fixture": fixture_id}),
        api_football_request("fixtures/lineups", {"fixture": fixture_id}),
        api_football_request("fixtures/statistics", {"fixture": fixture_id}),
        api_football_request("fixtures/events", {"fixture": fixture_id}),
        return_exceptions=True,
    )
    def usable(value: Any) -> list[dict[str, Any]]:
        return value if isinstance(value, list) else []
    return fixture_id, {
        **_lineup_metadata(usable(lineups), team_id),
        **_statistics_metadata(usable(stats), team_id),
        **_event_tags(usable(events), team_id),
        "playerStatisticsCoverage": "available" if isinstance(players, list) and players else "UNKNOWN",
        "_players": usable(players),
        "_lineups": usable(lineups),
    }


def _flat_stats(stats: dict[str, Any]) -> dict[str, Any]:
    return {
        "minutes": ((stats.get("games") or {}).get("minutes")),
        "position": ((stats.get("games") or {}).get("position")),
        "passes_total": ((stats.get("passes") or {}).get("total")),
        "passes_key": ((stats.get("passes") or {}).get("key")),
        "passes_crosses": ((stats.get("passes") or {}).get("cross")),
        "shots_total": ((stats.get("shots") or {}).get("total")),
        "shots_on": ((stats.get("shots") or {}).get("on")),
        "goals_saves": ((stats.get("goals") or {}).get("saves")),
        "goals_total": ((stats.get("goals") or {}).get("total")),
        "goals_assists": ((stats.get("goals") or {}).get("assists")),
        "tackles_total": ((stats.get("tackles") or {}).get("total")),
        "tackles_interceptions": ((stats.get("tackles") or {}).get("interceptions")),
        "tackles_clearances": ((stats.get("tackles") or {}).get("clearances")),
        "dribbles_attempts": ((stats.get("dribbles") or {}).get("attempts")),
        "fouls_committed": ((stats.get("fouls") or {}).get("committed")),
        "cards_yellow": ((stats.get("cards") or {}).get("yellow")),
    }


def _stat_value(flat: dict[str, Any], prop: str) -> Any:
    keys = {
        "passes": "passes_total", "pass_attempts": "passes_total",
        "shots": "shots_total", "shots_on_target": "shots_on",
        "saves": "goals_saves", "clearances": "tackles_clearances",
        "tackles": "tackles_total", "interceptions": "tackles_interceptions",
        "crosses": "passes_crosses", "key_passes": "passes_key",
        "goals": "goals_total", "assists": "goals_assists",
        "fouls_committed": "fouls_committed", "yellow_cards": "cards_yellow",
        "dribbles": "dribbles_attempts",
    }
    return flat.get(keys.get(prop, "passes_total"))


def _lineup_position_map(lineups: list[dict[str, Any]]) -> dict[int, tuple[str, str | None]]:
    positions: dict[int, tuple[str, str | None]] = {}
    for lineup in lineups:
        formation = lineup.get("formation")
        for row in lineup.get("startXI") or []:
            player = row.get("player") or {}
            try:
                player_id = int(player.get("id"))
            except (TypeError, ValueError):
                continue
            positions[player_id] = (
                infer_grid_position(player.get("grid"), formation, player.get("pos")),
                formation,
            )
    return positions


def _raw_history_fixture(raw: dict[str, Any], team_id: int) -> dict[str, Any] | None:
    fixture = raw.get("fixture") or {}
    teams = raw.get("teams") or {}
    home, away = teams.get("home") or {}, teams.get("away") or {}
    if home.get("id") == team_id:
        venue, opponent, score = "home", away, raw.get("goals") or {}
    elif away.get("id") == team_id:
        venue, opponent, score = "away", home, raw.get("goals") or {}
    else:
        return None
    return {
        "fixtureId": fixture.get("id"),
        "date": fixture.get("date"),
        "venue": venue,
        "teamId": team_id,
        "opponentId": opponent.get("id"),
        "opponent": opponent.get("name"),
        "score": f"{score.get('home', '')}-{score.get('away', '')}",
        "isKnockout": "round of" in str((raw.get("league") or {}).get("round") or "").lower()
        or "final" in str((raw.get("league") or {}).get("round") or "").lower(),
    }


async def _historical_fixture_rows(team_id: int | None, cutoff: str | None) -> list[dict[str, Any]]:
    if not team_id:
        return []
    fixtures = await priority_api_football_request("fixtures", {"team": team_id, "last": MAX_TARGET_MATCHES + 1})
    rows = [_raw_history_fixture(row, int(team_id)) for row in fixtures or []]
    return _sorted_limited([row for row in rows if row], MAX_TARGET_MATCHES, cutoff)


async def _hydrate_target_history(
    rows: list[dict[str, Any]], player_id: int | None
) -> tuple[list[dict[str, Any]], dict[int, dict[str, Any]]]:
    details = await _details_for_rows(rows, "teamId")
    hydrated = []
    for row in rows:
        detail = details.get(_fixture_id(row) or 0) or {}
        wanted = None
        for team in detail.get("_players") or []:
            for player in team.get("players") or []:
                if player_id and (player.get("player") or {}).get("id") == player_id:
                    wanted = player
                    break
            if wanted:
                break
        if not wanted:
            continue
        flat = _flat_stats((wanted.get("statistics") or [{}])[0] or {})
        hydrated.append({**row, **flat, "playerId": player_id})
    return hydrated, details


async def _hydrate_opponent_cohort(
    rows: list[dict[str, Any]], target_position: str | None, target_role: str | None, prop: str
) -> tuple[list[dict[str, Any]], dict[int, dict[str, Any]]]:
    details = await _details_for_rows(rows, "teamId")
    expected = str(target_position or "").upper()
    result: list[dict[str, Any]] = []
    for row in rows:
        detail = details.get(_fixture_id(row) or 0) or {}
        lineups = _lineup_position_map(detail.get("_lineups") or [])
        for team in detail.get("_players") or []:
            if team.get("team", {}).get("id") == row.get("teamId"):
                continue
            for player in team.get("players") or []:
                identity = player.get("player") or {}
                stats = (player.get("statistics") or [{}])[0] or {}
                flat = _flat_stats(stats)
                try:
                    minutes = float(flat.get("minutes") or 0)
                except (TypeError, ValueError):
                    minutes = 0
                player_id = identity.get("id")
                observed, formation = lineups.get(
                    player_id, (normalize_observed_position(flat.get("position")), None)
                )
                # Exact role requires a confirmed grid position; broad provider
                # categories remain visible but cannot become causal evidence.
                if expected and observed != expected:
                    continue
                if minutes < 30:
                    continue
                result.append({
                    **row, **flat,
                    "playerId": player_id,
                    "name": identity.get("name"),
                    "position": observed,
                    "role": target_role if observed == expected else None,
                    "formation": formation,
                    "statValue": _stat_value(flat, prop),
                    # A player-specific matching-venue baseline is supplied by
                    # the route's existing comparable-player enrichment when
                    # available. Do not replace a missing baseline with a team
                    # average or a fabricated zero.
                    "normalMatchingVenue": row.get("normalMatchingVenue") or row.get("seasonAvgStat"),
                    "venue": "away" if row.get("venue") == "home" else "home",
                })
    return result, details


async def _details_for_rows(rows: list[dict[str, Any]], team_key: str) -> dict[int, dict[str, Any]]:
    sem = aio.Semaphore(_DETAIL_CONCURRENCY)
    provider_unavailable = aio.Event()
    fixture_team_pairs: dict[int, int | None] = {}
    for row in rows:
        fixture_id = _fixture_id(row)
        if fixture_id and fixture_id not in fixture_team_pairs:
            fixture_team_pairs[fixture_id] = row.get(team_key) or row.get("teamId")

    async def limited(fixture_id: int, team_id: int | None):
        async with sem:
            if provider_unavailable.is_set() or is_quota_exhausted():
                return fixture_id, {
                    "coverage": "UNKNOWN",
                    "reason": "provider budget or quota unavailable",
                }
            try:
                key, detail = await _fixture_detail(fixture_id, team_id)
                # A skipped soft-budget request returns an empty list rather
                # than raising. Stop queued detail fan-out after that first
                # unmistakable all-empty packet instead of hammering the same
                # unavailable provider 20 more times.
                if not (
                    detail.get("_players")
                    or detail.get("_lineups")
                    or detail.get("statisticsCoverage") == "available"
                    or detail.get("eventCoverage") == "available"
                ):
                    provider_unavailable.set()
                    detail["reason"] = "provider detail unavailable"
                return key, detail
            except Exception as error:
                provider_unavailable.set()
                return fixture_id, {"coverage": "UNKNOWN", "error": type(error).__name__}

    tasks = [limited(fid, tid) for fid, tid in fixture_team_pairs.items()]
    try:
        pairs = await aio.wait_for(aio.gather(*tasks), timeout=_DETAIL_TIMEOUT_SECONDS)
    except (TimeoutError, aio.TimeoutError):
        return {}
    return dict(pairs)


def _merge_details(rows: list[dict[str, Any]], details: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    merged = []
    for row in rows:
        detail = details.get(_fixture_id(row) or 0, {})
        event_tags = list(detail.get("eventTags") or [])
        merged.append({
            **row,
            "fixtureEvidence": detail or None,
            "formation": row.get("formation") or row.get("lineupFormation") or detail.get("formation"),
            "coach": row.get("coach") or detail.get("coach"),
            "teamPossession": row.get("teamPossession") or detail.get("teamPossession"),
            "events": row.get("events") or " ".join(event_tags),
            "redCard": row.get("redCard") or ("red_card" in event_tags),
            "penalty": row.get("penalty") or ("penalty" in event_tags),
            "earlyGoal": row.get("earlyGoal") or detail.get("earlyGoal"),
        })
    return merged


async def assemble_causal_evidence(
    prediction: dict[str, Any],
    request: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Return a versioned, bounded evidence packet for one soccer candidate."""
    cutoff = str(
        context.get("fixture_date") or prediction.get("fixtureDate") or
        ((prediction.get("matchContext") or {}).get("date")) or ""
    )
    logs_packet = prediction.get("playerGameLogs") or {}
    target_rows = logs_packet.get("games") if isinstance(logs_packet, dict) else prediction.get("gameLogs")
    target_rows = _sorted_limited(target_rows or [], MAX_TARGET_MATCHES, cutoff)
    comparison = prediction.get("positionComparison") or {}
    cohort_rows = _sorted_limited(
        (comparison.get("players") if isinstance(comparison, dict) else []) or [],
        MAX_OPPONENT_MATCHES,
        cutoff,
    )
    try:
        player_id = int(request.get("player_id") or prediction.get("playerId") or 0) or None
    except (TypeError, ValueError):
        player_id = None
    try:
        team_id = int(context.get("team_id") or prediction.get("fixtureTeamId") or prediction.get("teamId") or 0) or None
    except (TypeError, ValueError):
        team_id = None
    try:
        opponent_id = int(context.get("opponent_id") or prediction.get("fixtureOpponentId") or prediction.get("opponentId") or 0) or None
    except (TypeError, ValueError):
        opponent_id = None
    prop = str(request.get("prop_type") or prediction.get("propType") or "passes").lower()
    target_position = prediction.get("playerPosition") or prediction.get("position")
    target_role = prediction.get("exactTacticalRole") or prediction.get("tacticalRole") or prediction.get("role")
    target_details: dict[int, dict[str, Any]] = {}
    cohort_details: dict[int, dict[str, Any]] = {}
    if not is_quota_exhausted() and not target_rows:
        raw_target_rows = await _historical_fixture_rows(team_id, cutoff)
        target_rows, target_details = await _hydrate_target_history(raw_target_rows, player_id)
    if not is_quota_exhausted() and not cohort_rows:
        raw_opponent_rows = await _historical_fixture_rows(opponent_id, cutoff)
        cohort_rows, cohort_details = await _hydrate_opponent_cohort(
            raw_opponent_rows, target_position, target_role, prop
        )
    live_fixture_id = request.get("fixture_id") or prediction.get("fixtureId")
    live_fixture = []
    live_odds = []
    if live_fixture_id and not is_quota_exhausted():
        try:
            live_fixture, live_odds = await aio.gather(
                priority_api_football_request("fixtures", {"id": live_fixture_id}),
                priority_api_football_request("odds", {"fixture": live_fixture_id}),
                return_exceptions=True,
            )
        except Exception:
            live_fixture, live_odds = [], []

    if is_quota_exhausted():
        provider_state = "quota_exhausted"
    else:
        if not target_details or not cohort_details:
            fetched_target, fetched_cohort = await aio.gather(
                _details_for_rows(target_rows, "teamId") if not target_details else aio.sleep(0, result=target_details),
                _details_for_rows(cohort_rows, "teamId") if not cohort_details else aio.sleep(0, result=cohort_details),
            )
            target_details = target_details or fetched_target
            cohort_details = cohort_details or fetched_cohort
        provider_state = "available"
    target_rows = _merge_details(target_rows, target_details)
    cohort_rows = _merge_details(cohort_rows, cohort_details)
    return {
        "version": "causal-evidence.v1",
        "provider": "api-football",
        "providerState": provider_state,
        "cutoff": cutoff or None,
        "pregameOnly": True,
        "targetHistory": target_rows,
        "opponentRoleCandidates": cohort_rows,
        "targetHistoryRequested": MAX_TARGET_MATCHES,
        "opponentHistoryRequested": MAX_OPPONENT_MATCHES,
        "targetHistoryReturned": len(target_rows),
        "opponentHistoryReturned": len(cohort_rows),
        "currentFixture": live_fixture if isinstance(live_fixture, list) else [],
        "market": live_odds if isinstance(live_odds, list) else [],
        "coverage": {
            "targetHistory": "available" if target_rows else "UNKNOWN",
            "opponentRoleCohort": "available" if cohort_rows else "UNKNOWN",
            "fixture": "available" if isinstance(live_fixture, list) and live_fixture else "UNKNOWN",
            "market": "available" if isinstance(live_odds, list) and live_odds else "UNKNOWN",
            "detailTagCounts": dict(Counter(
                tag for row in [*target_rows, *cohort_rows]
                for tag in ((row.get("fixtureEvidence") or {}).get("eventTags") or [])
            )),
        },
    }