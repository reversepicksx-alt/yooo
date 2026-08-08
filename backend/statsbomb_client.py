"""StatsBomb Open Data event enrichment.

StatsBomb Open Data is a static, research-oriented dataset.  When an exact
competition/date/opponent match is publicly available, this module derives
event-level pressure evidence from the published event stream:

* pressure events and counterpress actions by pitch third
* passes under pressure
* defensive-third passes allowed
* event-derived PPDA for a defined zone
* pressure-linked ball recoveries
* optional 360 freeze-frame coverage

This is evidence-only.  API-Football remains authoritative for live fixture
identity, projections, and settlement.  Missing public coverage is unavailable
data, never a measured zero.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import os
import re
import unicodedata
from typing import Any

import httpx


RAW_BASE_URL = (
    os.environ.get("STATSBOMB_OPEN_DATA_BASE")
    or "https://raw.githubusercontent.com/statsbomb/open-data/master/data"
).rstrip("/")
META_TTL_SECONDS = 7 * 24 * 3600
MATCH_TTL_SECONDS = 30 * 24 * 3600
EVENT_TTL_SECONDS = 180 * 24 * 3600
TIMEOUT_SECONDS = 12.0

# API-Football league IDs that have a reasonably direct StatsBomb Open Data
# competition counterpart.  The mapping is intentionally conservative; a
# league with no public counterpart must return unavailable rather than being
# matched to a similarly named competition.
API_LEAGUE_TO_STATSBOMB = {
    39: (2,),       # Premier League
    140: (11,),     # La Liga
    78: (9,),       # Bundesliga
    61: (7,),       # Ligue 1
    135: (12,),     # Serie A
    253: (44,),     # MLS
    254: (49,),     # NWSL
    1: (43,),       # FIFA World Cup
    2: (16,),       # UEFA Champions League
    3: (35,),       # UEFA Europa League
    55: (55,),      # UEFA Euro
    72: (72,),      # Women's World Cup
    49: (37,),      # FA Women's Super League
}


def _empty(reason: str) -> dict[str, Any]:
    return {
        "available": False,
        "status": "unavailable",
        "provider": "statsbomb_open_data",
        "source": "StatsBomb Open Data",
        "shadowOnly": True,
        "reason": reason,
        "coverage": None,
        "match": None,
        "eventMetrics": None,
        "target": None,
        "freezeFrame": None,
        "limitations": [
            "StatsBomb Open Data is static and has restricted competition coverage.",
            "API-Football remains authoritative for fixture identity, projections, and settlement.",
            "Missing StatsBomb coverage is unavailable data, not a measured zero.",
        ],
    }


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _name_match(left: Any, right: Any) -> bool:
    a, b = _norm(left), _norm(right)
    return bool(a and b and (a == b or a in b or b in a))


def _person_name_match(left: Any, right: Any) -> bool:
    """Match provider full names to abbreviated/user-entered player names."""
    if _name_match(left, right):
        return True
    left_tokens = set(_norm(left).split())
    right_tokens = set(_norm(right).split())
    if not left_tokens or not right_tokens:
        return False
    if left_tokens <= right_tokens or right_tokens <= left_tokens:
        return True
    return bool(left_tokens & right_tokens) and (
        next(iter(left_tokens)) in right_tokens or next(iter(right_tokens)) in left_tokens
    )


def _date_text(value: Any) -> str:
    return str(value or "").strip()[:10]


def _parse_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(_date_text(value))
    except (TypeError, ValueError):
        return None


def _season_contains_date(season_name: Any, match_date: str) -> bool:
    """Return whether a published season could contain the requested date."""
    target = _parse_date(match_date)
    text = str(season_name or "").strip()
    if target is None:
        return True
    years = re.findall(r"\d{4}", text)
    if not years:
        return True
    start_year = int(years[0])
    if len(years) > 1:
        return date(start_year, 7, 1) <= target <= date(int(years[1]), 6, 30)
    return target.year == start_year


def _competition_candidates(
    competitions: list[dict[str, Any]],
    *,
    league_id: int | None,
    league_name: str,
    match_date: str,
) -> list[dict[str, Any]]:
    """Select only public competition seasons that can contain the date."""
    mapped_ids = set(API_LEAGUE_TO_STATSBOMB.get(int(league_id or 0), ()))
    name = _norm(league_name)
    rows: list[dict[str, Any]] = []
    for row in competitions:
        if not isinstance(row, dict):
            continue
        comp_id = row.get("competition_id")
        try:
            comp_id_int = int(comp_id)
        except (TypeError, ValueError):
            continue
        mapped = comp_id_int in mapped_ids
        named = bool(
            name
            and (
                _name_match(row.get("competition_name"), league_name)
                or _name_match(row.get("country_name"), league_name)
            )
        )
        if (mapped or named) and _season_contains_date(row.get("season_name"), match_date):
            rows.append(row)
    rows.sort(key=lambda item: (0 if int(item.get("competition_id", 0)) in mapped_ids else 1,
                                str(item.get("season_name") or "")))
    return rows


def _find_match(
    rows: list[dict[str, Any]],
    *,
    team_name: str,
    opponent_name: str,
    match_date: str,
) -> dict[str, Any] | None:
    """Match only by verified names and exact published match date."""
    target_date = _date_text(match_date)
    candidates: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        home = row.get("home_team") or {}
        away = row.get("away_team") or {}
        home_name = home.get("home_team_name") if isinstance(home, dict) else home
        away_name = away.get("away_team_name") if isinstance(away, dict) else away
        same_orientation = _name_match(home_name, team_name) and _name_match(away_name, opponent_name)
        reverse_orientation = _name_match(away_name, team_name) and _name_match(home_name, opponent_name)
        if not (same_orientation or reverse_orientation):
            continue
        if target_date and _date_text(row.get("match_date")) != target_date:
            continue
        candidates.append(row)
    return candidates[0] if candidates else None


def _event_type(event: dict[str, Any]) -> str:
    raw = event.get("type")
    return str(raw.get("name") if isinstance(raw, dict) else raw or "")


def _team_id(event: dict[str, Any]) -> int | None:
    raw = event.get("team")
    value = raw.get("id") if isinstance(raw, dict) else raw
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coord(event: dict[str, Any]) -> tuple[float, float] | None:
    raw = event.get("location")
    if not isinstance(raw, (list, tuple)) or len(raw) < 2:
        return None
    try:
        return float(raw[0]), float(raw[1])
    except (TypeError, ValueError):
        return None


def _pass_start(event: dict[str, Any]) -> tuple[float, float] | None:
    return _coord(event)


def _event_seconds(event: dict[str, Any]) -> float:
    try:
        return float(event.get("period", 0)) * 45 * 60 + float(event.get("minute", 0)) * 60 + float(event.get("second", 0))
    except (TypeError, ValueError):
        return 0.0


def _is_tackle_duel(event: dict[str, Any]) -> bool:
    duel = event.get("duel")
    duel_type = duel.get("type") if isinstance(duel, dict) else {}
    name = duel_type.get("name") if isinstance(duel_type, dict) else duel_type
    return str(name or "").lower() == "tackle"


def _defensive_action(event: dict[str, Any]) -> bool:
    return _event_type(event) in {
        "Pressure", "Interception", "Block", "Foul Committed",
    } or (_event_type(event) == "Duel" and _is_tackle_duel(event))


def _third(x: float | None) -> str:
    if x is None:
        return "unavailable"
    if x < 40:
        return "defensive"
    if x < 80:
        return "middle"
    return "attacking"


def _mean_pass_progression(events: list[dict[str, Any]], team_id: int) -> float | None:
    changes: list[float] = []
    for event in events:
        if _team_id(event) != team_id or _event_type(event) != "Pass":
            continue
        start = _pass_start(event)
        raw_pass = event.get("pass") or {}
        end = raw_pass.get("end_location")
        if not start or not isinstance(end, (list, tuple)) or not end:
            continue
        try:
            changes.append(float(end[0]) - start[0])
        except (TypeError, ValueError):
            continue
    return sum(changes) / len(changes) if changes else None


def _period_direction(
    events: list[dict[str, Any]],
    team_id: int,
    period: int,
) -> tuple[float | None, str]:
    """Infer the team's attacking direction for one half.

    StatsBomb locations use a shared 120x80 pitch.  Shots provide the strongest
    signal: their origin is normally in the attacking half.  Goalkeeper
    locations are the next-best fallback because they identify the team's own
    goal.  Pass progression is only a last resort because buildup passes can
    run opposite to the team's attacking direction.
    """
    period_rows = [
        event for event in events
        if int(event.get("period", 0) or 0) == period
    ]
    shots = [
        _coord(event)[0]
        for event in period_rows
        if _team_id(event) == team_id
        and _event_type(event) == "Shot"
        and _coord(event) is not None
    ]
    if shots:
        return (1.0 if sum(shots) / len(shots) >= 60 else -1.0, "shots")

    keepers = [
        _coord(event)[0]
        for event in period_rows
        if _team_id(event) == team_id
        and _event_type(event) == "Goal Keeper"
        and _coord(event) is not None
    ]
    if keepers:
        # Keeper near x=0 means the team attacks toward x=120, and vice versa.
        return (-1.0 if sum(keepers) / len(keepers) >= 60 else 1.0, "goalkeeper")

    progression = _mean_pass_progression(period_rows, team_id)
    if progression is not None and abs(progression) > 0.5:
        return (1.0 if progression > 0 else -1.0, "pass_progression_fallback")
    return None, "unavailable"


def _coordinate_mode(events: list[dict[str, Any]], team_ids: list[int]) -> str:
    # Do not call a one-sided stream "normalized": PPDA compares two teams,
    # so both teams need at least one direction anchor.
    has_team_direction = [
        any(
            _period_direction(events, team_id, period)[0] is not None
            for period in (1, 2)
        )
        for team_id in team_ids
    ]
    return "team_relative" if all(has_team_direction) else "unknown"


def _zone_match(x: float | None, zone: str, *, direction: float = 1.0) -> bool:
    if x is None:
        return False
    if zone == "defensive":
        return x < 40 if direction >= 0 else x >= 80
    if zone == "attacking":
        return x >= 80 if direction >= 0 else x < 40
    return 40 <= x < 80


def _team_direction(events: list[dict[str, Any]], team_id: int, period: int) -> float | None:
    direction = _period_direction(events, team_id, period)[0]
    if direction is not None:
        return direction
    # A rare half with no shots/keeper events can inherit the other half's
    # direction; this is still event-grounded and preferable to assuming x=0.
    other_period = 2 if period == 1 else 1
    return _period_direction(events, team_id, other_period)[0]


def _normalized_x(
    x: float | None,
    *,
    direction: float | None,
) -> float | None:
    if x is None or direction is None:
        return None
    return x if direction >= 0 else 120.0 - x


def compute_event_metrics(
    events: list[dict[str, Any]],
    *,
    team_id: int,
    opponent_id: int,
) -> dict[str, Any]:
    """Derive transparent team pressure metrics from StatsBomb events."""
    rows = [event for event in events if isinstance(event, dict)]
    mode = _coordinate_mode(rows, [team_id, opponent_id])
    target_pressures = [event for event in rows if _team_id(event) == team_id and _event_type(event) == "Pressure"]
    target_actions = [event for event in rows if _team_id(event) == team_id and _defensive_action(event)]
    opponent_passes = [event for event in rows if _team_id(event) == opponent_id and _event_type(event) == "Pass"]
    target_passes = [event for event in rows if _team_id(event) == team_id and _event_type(event) == "Pass"]

    pressure_by_third = {key: 0 for key in ("defensive", "middle", "attacking")}
    counterpressures = 0
    for event in target_pressures:
        location = _coord(event)
        direction = _team_direction(rows, team_id, int(event.get("period", 1) or 1))
        normalized_x = _normalized_x(location[0] if location else None, direction=direction)
        pressure_by_third[_third(normalized_x)] = pressure_by_third.get(
            _third(normalized_x), 0
        ) + 1
        if (event.get("counterpress") is True):
            counterpressures += 1

    team_pressure_actions = 0
    opponent_passes_in_press_zone = 0
    if mode != "unknown":
        for event in target_actions:
            location = _coord(event)
            if not location:
                continue
            period = int(event.get("period", 1) or 1)
            direction = _team_direction(rows, team_id, period)
            if _zone_match(location[0], "defensive", direction=direction or 1.0):
                team_pressure_actions += 1
        for event in opponent_passes:
            location = _pass_start(event)
            if not location:
                continue
            period = int(event.get("period", 1) or 1)
            opponent_direction = _team_direction(rows, opponent_id, period)
            if _zone_match(location[0], "attacking", direction=opponent_direction or 1.0):
                opponent_passes_in_press_zone += 1

    pressure_regains = 0
    for index, event in enumerate(rows):
        if _team_id(event) != team_id or _event_type(event) != "Ball Recovery":
            continue
        recovery_time = _event_seconds(event)
        for previous in reversed(rows[max(0, index - 12):index]):
            if _team_id(previous) == team_id and _event_type(previous) == "Pressure":
                if 0 <= recovery_time - _event_seconds(previous) <= 5:
                    pressure_regains += 1
                break

    opponent_under = sum(bool((event.get("pass") or {}).get("under_pressure")) for event in opponent_passes)
    team_under = sum(bool((event.get("pass") or {}).get("under_pressure")) for event in target_passes)
    ppda = (
        round(opponent_passes_in_press_zone / team_pressure_actions, 2)
        if mode != "unknown" and team_pressure_actions > 0
        else None
    )
    return {
        "coordinateMode": mode,
        "coordinateScale": "120x80",
        "coordinateNormalizationStatus": (
            "period_direction_inferred"
            if mode == "team_relative"
            else "unavailable"
        ),
        "pressureEvents": len(target_pressures),
        "pressureByThird": pressure_by_third,
        "counterpressures": counterpressures,
        "defensiveActionsInPressZone": team_pressure_actions,
        "opponentPassesInPressZone": opponent_passes_in_press_zone,
        "passesUnderPressure": {
            "team": team_under,
            "opponent": opponent_under,
            "teamRate": round(team_under / len(target_passes), 4) if target_passes else None,
            "opponentRate": round(opponent_under / len(opponent_passes), 4) if opponent_passes else None,
        },
        "pressureRegains": pressure_regains,
        "ppda": ppda,
        "ppdaStatus": "event_derived" if ppda is not None else "unavailable",
        "ppdaDefinition": "opponent passes beginning in the opponent attacking third ÷ team pressure/tackle/interception/block/foul actions in the team's defensive third",
        "sampleSize": 1,
        "evidenceStatus": "single_match_event_stream",
        "projectionAdjustment": 0.0,
        "projectionAdjustmentStatus": "shadow_only",
        "limitations": [
            "StatsBomb Open Data provides event locations, not continuous tracking.",
            "PPDA is calculated for the explicit thirds and action set above.",
            "A single match describes the observed match and is not a stable team baseline.",
        ],
    }


def summarize_freeze_frames(frames: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    if not isinstance(frames, list):
        return None
    valid = [row for row in frames if isinstance(row, dict) and isinstance(row.get("freeze_frame"), list)]
    if not valid:
        return None
    player_counts = [len(row["freeze_frame"]) for row in valid]
    return {
        "available": True,
        "eventCount": len(valid),
        "averageVisiblePlayers": round(sum(player_counts) / len(player_counts), 1),
        "source": "StatsBomb 360 freeze frames",
        "status": "limited_event_snapshots",
        "limitations": [
            "360 frames are available only for selected matches and selected events.",
            "Freeze frames are not continuous player tracking.",
        ],
    }


async def _request(client: httpx.AsyncClient, path: str) -> Any:
    response = await client.get(f"{RAW_BASE_URL}/{path.lstrip('/')}")
    response.raise_for_status()
    return response.json()


async def _cached_json(db, key: str, path: str, ttl: int, client: httpx.AsyncClient) -> Any:
    try:
        cached = await db.statsbomb_cache.find_one({"_id": key}, {"_id": 0, "data": 1, "cachedAt": 1})
        cached_at = cached.get("cachedAt") if cached else None
        if cached and cached.get("data") is not None and isinstance(cached_at, datetime):
            age = (datetime.now(timezone.utc) - cached_at.replace(tzinfo=timezone.utc)).total_seconds()
            if age < ttl:
                return cached["data"]
    except Exception as exc:
        print(f"[STATSBOMB] cache read skipped: {type(exc).__name__}: {exc}")
    data = await _request(client, path)
    try:
        await db.statsbomb_cache.update_one(
            {"_id": key},
            {"$set": {"_id": key, "data": data, "cachedAt": datetime.now(timezone.utc)}},
            upsert=True,
        )
    except Exception as exc:
        print(f"[STATSBOMB] cache write skipped: {type(exc).__name__}: {exc}")
    return data


def _target_lineup(lineups: Any, *, team_name: str, player_name: str) -> dict[str, Any] | None:
    rows = lineups if isinstance(lineups, list) else []
    for row in rows:
        if not isinstance(row, dict):
            continue
        raw_team = row.get("team") or {}
        name = (
            row.get("team_name")
            or raw_team.get("name") if isinstance(raw_team, dict) else raw_team
        )
        if not _name_match(name, team_name):
            continue
        for item in row.get("lineup") or []:
            player = item.get("player") or {}
            provider_name = (
                item.get("player_name")
                or player.get("name") if isinstance(player, dict) else player
            )
            positions = item.get("positions") or []
            position = positions[0] if positions else (item.get("position") or {})
            if _person_name_match(provider_name, player_name):
                return {
                    "id": item.get("player_id") or (
                        player.get("id") if isinstance(player, dict) else None
                    ),
                    "name": provider_name,
                    "position": (
                        position.get("position")
                        or position.get("name")
                        if isinstance(position, dict) else position
                    ),
                    "starter": bool(
                        position.get("start_reason") == "Starting XI"
                        if isinstance(position, dict) else item.get("starter")
                    ),
                }
    return None


def _position_group(value: Any) -> str | None:
    """Normalize StatsBomb lineup positions into stable analysis groups."""
    text = _norm(value)
    if not text:
        return None
    if "goalkeeper" in text or text in {"keeper", "gk"}:
        return "GK"
    if "center back" in text or "centre back" in text or text in {"cb", "defender"}:
        return "CB"
    if "left back" in text or text in {"lb", "left wing back"}:
        return "LB"
    if "right back" in text or text in {"rb", "right wing back"}:
        return "RB"
    if "wing back" in text:
        return "WB"
    if "defensive midfield" in text or text in {"dm", "cdm"}:
        return "DM"
    if "center midfield" in text or "centre midfield" in text or text in {"cm", "midfielder"}:
        return "CM"
    if "left midfield" in text or text in {"lm", "left wing"}:
        return "LM/LW" if "wing" in text else "LM"
    if "right midfield" in text or text in {"rm", "right wing"}:
        return "RM/RW" if "wing" in text else "RM"
    if "attacking midfield" in text or text in {"am", "cam"}:
        return "AM"
    if "left wing" in text:
        return "LW"
    if "right wing" in text:
        return "RW"
    if "center forward" in text or "centre forward" in text or text in {"cf", "forward"}:
        return "CF"
    if "striker" in text or text in {"st", "ss"}:
        return "ST"
    return None


def _lineup_position_map(lineups: Any) -> dict[int, str]:
    """Return provider player ID → normalized position group."""
    positions: dict[int, str] = {}
    for row in lineups if isinstance(lineups, list) else []:
        if not isinstance(row, dict):
            continue
        for item in row.get("lineup") or []:
            if not isinstance(item, dict):
                continue
            player = item.get("player") or {}
            raw_id = item.get("player_id") or (
                player.get("id") if isinstance(player, dict) else None
            )
            try:
                player_id = int(raw_id)
            except (TypeError, ValueError):
                continue
            raw_positions = item.get("positions") or []
            position = raw_positions[0] if raw_positions else item.get("position")
            position_name = (
                position.get("position") or position.get("name")
                if isinstance(position, dict) else position
            )
            group = _position_group(position_name)
            if group:
                positions[player_id] = group
    return positions


def compute_position_pass_metrics(
    events: list[dict[str, Any]],
    *,
    team_id: int,
    opponent_id: int,
    lineups: Any,
) -> dict[str, Any]:
    """Count completed passes received by each lineup position in this match.

    The metric is deliberately match-level and evidence-only.  It does not claim
    a league baseline or infer a missing recipient position.
    """
    position_map = _lineup_position_map(lineups)
    if not position_map:
        return {
            "status": "unavailable",
            "reason": "No verified StatsBomb lineup positions were available.",
            "provider": "statsbomb_open_data",
        }

    profiles: dict[str, dict[str, int]] = {}
    identified_passes = 0
    for event in events if isinstance(events, list) else []:
        if not isinstance(event, dict) or _event_type(event) != "Pass":
            continue
        passer_team = _team_id(event)
        if passer_team not in {team_id, opponent_id}:
            continue
        raw_pass = event.get("pass") or {}
        recipient = raw_pass.get("recipient") or {}
        raw_recipient_id = recipient.get("id") if isinstance(recipient, dict) else recipient
        try:
            recipient_id = int(raw_recipient_id)
        except (TypeError, ValueError):
            continue
        position = position_map.get(recipient_id)
        if not position:
            continue
        identified_passes += 1
        profile = profiles.setdefault(position, {"attempted": 0, "completed": 0})
        profile["attempted"] += 1
        outcome = raw_pass.get("outcome")
        outcome_name = outcome.get("name") if isinstance(outcome, dict) else outcome
        if not outcome_name:
            profile["completed"] += 1
        elif str(outcome_name).strip().lower() in {"complete", "successful"}:
            profile["completed"] += 1

    if identified_passes == 0:
        return {
            "status": "unavailable",
            "reason": "The exact event stream did not identify pass recipients and positions.",
            "provider": "statsbomb_open_data",
        }

    by_team: dict[str, dict[str, dict[str, float | int]]] = {
        "targetTeam": {},
        "opponent": {},
    }
    for event in events:
        if not isinstance(event, dict) or _event_type(event) != "Pass":
            continue
        passer_team = _team_id(event)
        if passer_team not in {team_id, opponent_id}:
            continue
        raw_pass = event.get("pass") or {}
        recipient = raw_pass.get("recipient") or {}
        raw_recipient_id = recipient.get("id") if isinstance(recipient, dict) else recipient
        try:
            recipient_id = int(raw_recipient_id)
        except (TypeError, ValueError):
            continue
        position = position_map.get(recipient_id)
        if not position:
            continue
        outcome = raw_pass.get("outcome")
        outcome_name = outcome.get("name") if isinstance(outcome, dict) else outcome
        completed = not outcome_name or str(outcome_name).strip().lower() in {"complete", "successful"}
        team_key = "targetTeam" if passer_team == team_id else "opponent"
        row = by_team[team_key].setdefault(position, {"attempted": 0, "completed": 0, "per90": 0.0})
        row["attempted"] += 1
        if completed:
            row["completed"] += 1

    for rows in by_team.values():
        for row in rows.values():
            row["per90"] = round(float(row["completed"]), 1)

    return {
        "status": "event_derived",
        "provider": "statsbomb_open_data",
        "sampleMatches": 1,
        "normalization": "completed passes received per 90 match minutes",
        "targetTeam": by_team["targetTeam"],
        "opponent": by_team["opponent"],
        "opponentAllowedToTargetPositions": by_team["targetTeam"],
        "limitations": [
            "This is an exact-match event metric, not a league baseline.",
            "Only recipients with a verified lineup position are counted.",
            "The metric is shadow-only and does not change the projection.",
        ],
    }


async def fetch_match_enrichment(
    db,
    *,
    fixture_id: int | str | None,
    league_id: int | None,
    league_name: str,
    team_name: str,
    opponent_name: str,
    match_date: str,
    player_name: str,
) -> dict[str, Any]:
    """Fetch and calculate StatsBomb evidence for an exact public match."""
    if not team_name or not opponent_name or not match_date:
        return _empty("Verified fixture identity is incomplete.")
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            competitions = await _cached_json(db, "statsbomb_competitions_v1", "competitions.json", META_TTL_SECONDS, client)
            candidates = _competition_candidates(
                competitions if isinstance(competitions, list) else [],
                league_id=league_id,
                league_name=league_name,
                match_date=match_date,
            )
            if not candidates:
                return _empty("StatsBomb Open Data has no mapped competition season for this fixture.")
            match = None
            selected = None
            for candidate in candidates[:12]:
                comp_id = int(candidate["competition_id"])
                season_id = int(candidate["season_id"])
                rows = await _cached_json(
                    db,
                    f"statsbomb_matches_{comp_id}_{season_id}",
                    f"matches/{comp_id}/{season_id}.json",
                    MATCH_TTL_SECONDS,
                    client,
                )
                match = _find_match(
                    rows if isinstance(rows, list) else [],
                    team_name=team_name,
                    opponent_name=opponent_name,
                    match_date=match_date,
                )
                if match:
                    selected = candidate
                    break
            if not match or not selected:
                return _empty("StatsBomb Open Data does not cover this exact date and opponent.")

            match_id = match.get("match_id")
            events = await _cached_json(
                db,
                f"statsbomb_events_{match_id}",
                f"events/{match_id}.json",
                EVENT_TTL_SECONDS,
                client,
            )
            if not isinstance(events, list):
                return _empty("StatsBomb event stream was unavailable for this match.")

            home = match.get("home_team") or {}
            away = match.get("away_team") or {}
            home_name = home.get("home_team_name") if isinstance(home, dict) else home
            away_name = away.get("away_team_name") if isinstance(away, dict) else away
            if _name_match(home_name, team_name):
                sb_team_id, sb_opponent_id = home.get("home_team_id"), away.get("away_team_id")
            else:
                sb_team_id, sb_opponent_id = away.get("away_team_id"), home.get("home_team_id")
            lineups = await _cached_json(
                db,
                f"statsbomb_lineups_{match_id}",
                f"lineups/{match_id}.json",
                EVENT_TTL_SECONDS,
                client,
            )
            metrics = compute_event_metrics(events, team_id=int(sb_team_id), opponent_id=int(sb_opponent_id))
            metrics["positionPassesReceived"] = compute_position_pass_metrics(
                events,
                team_id=int(sb_team_id),
                opponent_id=int(sb_opponent_id),
                lineups=lineups,
            )
            target = _target_lineup(lineups, team_name=team_name, player_name=player_name)
            freeze = None
            try:
                frames = await _cached_json(
                    db,
                    f"statsbomb_360_{match_id}",
                    f"three-sixty/{match_id}.json",
                    EVENT_TTL_SECONDS,
                    client,
                )
                freeze = summarize_freeze_frames(frames)
            except Exception:
                freeze = None

            return {
                "available": True,
                "status": "covered",
                "provider": "statsbomb_open_data",
                "source": "StatsBomb Open Data",
                "shadowOnly": True,
                "coverage": "exact_date_and_opponent",
                "competition": {
                    "competitionId": selected.get("competition_id"),
                    "competitionName": selected.get("competition_name"),
                    "seasonId": selected.get("season_id"),
                    "seasonName": selected.get("season_name"),
                },
                "match": {
                    "statsBombMatchId": match_id,
                    "apiFootballFixtureId": fixture_id,
                    "date": match.get("match_date"),
                    "homeTeam": home_name,
                    "awayTeam": away_name,
                },
                "eventMetrics": metrics,
                "target": target,
                "freezeFrame": freeze,
                "limitations": metrics.get("limitations", []),
            }
    except Exception as exc:
        print(f"[STATSBOMB] enrichment unavailable: {type(exc).__name__}: {exc}")
        return _empty(f"StatsBomb Open Data request failed: {type(exc).__name__}.")