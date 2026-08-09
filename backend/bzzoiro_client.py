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

# Constraints recorded before production use, as required by the validation gate.
# These must be reviewed and confirmed before Bzzoiro signals influence any
# projection or production decision.
COVERAGE_CONSTRAINTS: dict[str, Any] = {
    "authentication": {
        "required": True,
        "method": "Token header (Authorization: Token <BZZOIRO_API_TOKEN>)",
        "environmentVariable": TOKEN_ENV,
        "consequenceIfMissing": (
            "All requests return an unavailable packet; no silent fallback attempted."
        ),
    },
    "rateLimits": {
        "documented": False,
        "observed": "Unknown — treat as a metered commercial API.",
        "strategy": (
            "6-hour Atlas cache per fixture/team/opponent triple; "
            "optional wave with a 3-second timeout; fail-open on every error."
        ),
    },
    "commercialUse": {
        "verified": False,
        "note": (
            "Bzzoiro commercial-use terms have not been verified for this application. "
            "Data is stored in a 6-hour cache and used only as shadow enrichment. "
            "Confirm terms before using Bzzoiro data in production decisions or "
            "projections that affect subscribers."
        ),
    },
    "competitionCoverage": {
        "confirmed": ["MLS", "Liga MX"],
        "pendingVerification": ["Leagues Cup", "Champions Cup"],
        "bridgeMethod": (
            "Verified team/opponent names and match date.  "
            "Numeric IDs differ across providers; name-based bridging is the "
            "only cross-provider identity anchor available."
        ),
        "coverageGaps": (
            "Missing token or absent coverage returns an unavailable packet, "
            "never a fabricated zero.  Empty arrays are unavailable data, not a "
            "measured absence."
        ),
    },
    "positionData": {
        "source": "Observed match average-position coordinates (x/y on a 0-100 pitch grid) per player",
        "coordinateSystem": (
            "Expected range 0–100 on both axes.  "
            "Values outside this range are rejected by validate_position_data()."
        ),
        "limitation": (
            "Average coordinates are match-level averages, not real-time tracking data.  "
            "A single fixture's coordinates cannot establish a stable positional baseline."
        ),
        "shadowOnly": True,
        "productionReadiness": "not_ready — settled-pick replay required before live influence.",
    },
}


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


def _name_match_quality(left: Any, right: Any) -> str:
    """Return 'exact', 'substring', or 'none' after accent-stripped normalization.

    Used to label the reliability of cross-provider player identity, since
    Bzzoiro and API-Football numeric IDs are incompatible.  Only 'exact' or a
    confirmed numeric ID constitutes a reliable identity match.
    """
    a, b = _norm(left), _norm(right)
    if not a or not b:
        return "none"
    if a == b:
        return "exact"
    if a in b or b in a:
        return "substring"
    return "none"


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
) -> tuple[dict[str, Any], bool] | None:
    """Return ``(event, date_exact)`` for the best-matching event row.

    ``date_exact`` is ``True`` only when the event date string equals the
    requested fixture date (YYYY-MM-DD prefix comparison).  Callers must use
    this flag to set the correct coverage label and validation gate — a
    nearest-date fallback is NOT an exact fixture match and must not be labeled
    or treated as one.

    Returns ``None`` when no team/opponent candidate is found at all.
    """
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
    best_distance, _, best_event = candidates[0]
    return best_event, best_distance == 0


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


def validate_position_data(enrichment: dict[str, Any]) -> dict[str, Any]:
    """Validate Bzzoiro position/lineup data quality before tactical use.

    Checks coordinate ranges, lineup completeness, player identity reliability,
    and fixture date precision.  Returns a validation packet that explicitly
    labels what is usable so callers can make informed decisions rather than
    silently consuming low-quality data.

    Gate rules:
    - ``fixtureDateMatch`` must be "exact" (set by ``_find_event`` distance==0).
    - ``lineupValid`` requires the player to have been matched by numeric ID or
      exact normalized name — substring matches are explicitly rejected because
      cross-provider numeric IDs are incompatible and permissive name matching
      can collide on short or common names.

    This gate must pass before any Bzzoiro position coordinate is forwarded to
    ``tactical_intelligence.build_tactical_intelligence()``.
    """
    if not isinstance(enrichment, dict) or not enrichment.get("available"):
        return {
            "valid": False,
            "reason": "Bzzoiro enrichment is unavailable.",
            "coordinatesValid": False,
            "lineupValid": False,
            "playerIdentityConfidence": "none",
            "fixtureDateMatch": "unknown",
            "issues": [],
            "usableAsPositionSupplement": False,
        }

    issues: list[str] = []

    # ── Fixture date precision ──────────────────────────────────────────────
    # The coverage label is set by fetch_fixture_enrichment() based on the
    # date-distance field returned by _find_event().  Any coverage value other
    # than "exact_date_and_opponent" means the event was a nearest-date fallback
    # and must not be used as position evidence for a different fixture.
    fixture = enrichment.get("fixture") or {}
    coverage = str(fixture.get("coverage") or "")
    if coverage == "exact_date_and_opponent":
        date_match = "exact"
    elif coverage in {"not_found", ""}:
        date_match = "unknown"
        issues.append("Fixture coverage status unknown or not found.")
    else:
        date_match = "fuzzy"
        issues.append(
            f"Fixture date match is '{coverage}', not exact; "
            "nearest-date fallback must not supply position data for a different fixture."
        )

    # ── Lineup validation with reliable identity requirement ────────────────
    # lineupValid requires _matchMethod "exact_name".
    # "numeric_id" is excluded from this gate because it was previously
    # produced by comparing the API-Football player_id against Bzzoiro's ID
    # namespace — two different spaces that can collide accidentally.  The
    # production fetch path now uses only exact normalized name matching for
    # the lineup anchor, so "numeric_id" is never the match method from there.
    # "substring_name" is too permissive for cross-provider identity because
    # short or common names can match multiple players.
    lineup = enrichment.get("lineup") or {}
    target_in_lineup = lineup.get("target")
    match_method = (
        target_in_lineup.get("_matchMethod", "none")
        if isinstance(target_in_lineup, dict)
        else "none"
    )
    RELIABLE_MATCH_METHODS = {"exact_name"}
    lineup_valid = (
        isinstance(target_in_lineup, dict)
        and bool(target_in_lineup)
        and match_method in RELIABLE_MATCH_METHODS
    )
    if not lineup_valid:
        if isinstance(target_in_lineup, dict) and bool(target_in_lineup):
            issues.append(
                f"Player matched via '{match_method}' (not exact normalized name); "
                "identity is ambiguous and cannot be used as position evidence."
            )
        else:
            issues.append("Target player not found in Bzzoiro lineup.")

    # ── Average-position coordinates ────────────────────────────────────────
    target = enrichment.get("target") or {}
    avg_pos = target.get("averagePosition")
    coordinates_valid = False
    if isinstance(avg_pos, dict):
        x = _number(avg_pos.get("x"))
        y = _number(avg_pos.get("y"))
        if x is not None and y is not None:
            if 0.0 <= x <= 100.0 and 0.0 <= y <= 100.0:
                # Ownership check: the average-position record must belong to the
                # same player as the confirmed lineup entry.  A mismatch means the
                # fetch joined a different player's coordinates with this lineup
                # entry (e.g. via substring name match on a common name).
                owner_ok = True
                if lineup_valid and isinstance(target_in_lineup, dict):
                    lineup_name = _norm(target_in_lineup.get("name", ""))
                    avgpos_name = _norm(avg_pos.get("name", ""))
                    if lineup_name and avgpos_name and lineup_name != avgpos_name:
                        owner_ok = False
                        issues.append(
                            "Average-position player name does not match the confirmed "
                            "lineup player; coordinates may belong to a different player."
                        )
                if owner_ok:
                    coordinates_valid = True
            else:
                issues.append(
                    f"Average-position coordinates out of expected pitch range "
                    f"(0–100): x={x}, y={y}."
                )
        else:
            issues.append("Average-position coordinates are missing or non-numeric.")
    else:
        issues.append("No average-position data returned for target player.")

    # ── Player identity confidence ──────────────────────────────────────────
    match_stats = target.get("matchStats")
    player_found_somewhere = lineup_valid or (
        isinstance(match_stats, dict) and bool(match_stats)
    )
    if lineup_valid and coordinates_valid:
        identity_confidence = "high"
    elif player_found_somewhere or match_method == "substring_name":
        identity_confidence = "medium"
    else:
        identity_confidence = "low"

    # ── Overall usability ───────────────────────────────────────────────────
    # Both gates must pass before position data can reach tactical intelligence.
    usable = lineup_valid and date_match == "exact"

    return {
        "valid": usable,
        "reason": None if usable else ("; ".join(issues) or "Validation failed."),
        "coordinatesValid": coordinates_valid,
        "lineupValid": lineup_valid,
        "matchMethod": match_method,
        "playerIdentityConfidence": identity_confidence,
        "fixtureDateMatch": date_match,
        "issues": issues,
        "usableAsPositionSupplement": usable and coordinates_valid,
    }


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
            _find_result = _find_event(
                _unwrap_results(event_payload),
                team_id=team_id,
                team_name=team_name,
                opponent_id=opponent_id,
                opponent_name=opponent_name,
                match_date=match_date,
            )
            if not _find_result:
                result = _empty("Bzzoiro does not cover this verified fixture.")
                result["fixture"] = {"apiFootballFixtureId": fixture_id, "coverage": "not_found"}
            else:
                event, date_exact = _find_result
                # Only label coverage as exact when _find_event confirmed a
                # same-day match (distance == 0).  Nearest-date fallbacks are
                # labeled explicitly so validate_position_data() can reject them.
                coverage_label = (
                    "exact_date_and_opponent" if date_exact else "nearest_date_only"
                )
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

                # ── Player identity resolution ───────────────────────────────
                # API-Football and Bzzoiro use different numeric ID spaces.
                # Cross-provider numeric ID comparisons are explicitly excluded
                # because an accidental collision can attach another player's
                # position data to the prediction with misleading "numeric_id"
                # confidence.  Only exact normalized name matching is reliable
                # for the lineup anchor.  Substring matching is tracked but
                # explicitly rejected by validate_position_data().
                target_lineup = None
                target_match_method = "none"
                bzz_player_id: int | None = None  # Bzzoiro-internal confirmed ID
                lineup_players: list[dict[str, Any]] = []
                if isinstance(own_lineup, dict):
                    lineup_players = (
                        (own_lineup.get("players") or [])
                        + (own_lineup.get("substitutes") or [])
                    )
                    for item in lineup_players:
                        if not isinstance(item, dict):
                            continue
                        quality = _name_match_quality(item.get("name"), player_name)
                        if quality == "exact":
                            target_lineup = dict(item)
                            target_match_method = "exact_name"
                            # After confirming identity by exact name, we can
                            # safely use Bzzoiro's own player ID for intra-
                            # provider lookups (player_stats, avg_pos) without
                            # risking cross-provider ID collisions.
                            bzz_player_id = item.get("id")
                            break
                        elif quality == "substring" and target_match_method == "none":
                            target_lineup = dict(item)
                            target_match_method = "substring_name"
                            # Don't set bzz_player_id — ambiguous identity
                    if target_lineup is not None:
                        target_lineup["_matchMethod"] = target_match_method

                # ── Player stats and average-position ────────────────────────
                # Resolve ONLY to the same confirmed Bzzoiro player.
                # Use the Bzzoiro-internal ID (extracted from the exact lineup
                # match) OR exact name.  Substring matching is excluded because
                # it would allow coordinates/stats from a different player to
                # enter the position supplement despite the lineup gate passing.
                def _bzz_owned(item: dict[str, Any], key: str = "player_id") -> bool:
                    if bzz_player_id is not None and item.get(key) == bzz_player_id:
                        return True
                    return _name_match_quality(item.get("name"), player_name) == "exact"

                player_rows = (
                    (player_stats_payload.get("player_stats") or [])
                    if isinstance(player_stats_payload, dict)
                    else []
                )
                target_stats = next(
                    (item for item in player_rows if isinstance(item, dict) and _bzz_owned(item)),
                    None,
                )
                average_positions = (stats.get("average_positions") or {}).get(team_side, [])
                target_position = next(
                    (
                        item for item in average_positions
                        if isinstance(item, dict) and _bzz_owned(item, key="player_id")
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
                        "coverage": coverage_label,
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
                # Validate the position/lineup data quality before it can be
                # consumed by tactical_intelligence.  The validation packet is
                # attached to the result so callers never need to re-run it.
                result["positionValidation"] = validate_position_data(result)
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