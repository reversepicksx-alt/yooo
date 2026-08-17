import json
import os
import re
import uuid
import hashlib
import math
import asyncio as aio
import statistics as stats_mod
import traceback
from datetime import datetime, timezone, timedelta
from typing import Any
from fastapi import APIRouter, HTTPException
from config import (
    db, CURRENT_SEASON, WOMENS_LEAGUE_IDS, STAT_FIELD_MAP, STAT_LAMBDA_MAP,
    INTERNATIONAL_LEAGUES, NATIONAL_TEAM_TIER,
)
from models import PredictionRequest
from utils import (
    api_football_request, get_recent_fixtures_fast, strip_accents, get_soccer_odds,
    decimal_to_american, priority_api_football_request,
    set_api_request_priority, reset_api_request_priority,
    resolve_verified_fixture,
)
from sportsgameodds_client import lookup_soccer_market_context
from prop_safety_cache import (
    get_prop_safety as _get_prop_safety,
    get_recent_prop_safety as _get_recent_prop_safety,
)
import soccer_bdl_client as _bdl_soc
from tactical_evidence import (
    build_tactical_conclusion,
    infer_grid_position,
    exact_position_from_lineup_payload,
    normalize_observed_position,
    position_cohort_verdict,
    preserve_selection_role,
    resolve_observed_role,
    summarize_observed_positions,
    summarize_player_opponent_history,
    summarize_position_cohort,
    build_position_cohort_statement,
)
from role_evidence import build_role_evidence_packet
from matchup_volume import build_matchup_volume_packet
from possession_context import (
    POSSESSION_MIN_VERIFIED_SAMPLE,
    POSSESSION_RECENCY_HALF_LIFE,
    moneyline_possession_signal,
    possession_sample_status,
    recency_weighted_average,
)
from statsbomb_client import fetch_match_enrichment as _fetch_statsbomb_enrichment
from opponent_block_profile import (
    fetch_recent_opponent_block_profiles as _fetch_recent_opponent_block_profiles,
)
# compact_explanation (Gemini AI) removed — hit rates shown on frontend instead
# game script intelligence removed — it distorted confidence scores for GK pass picks

router = APIRouter(prefix="/api", tags=["predict"])

# A venue-specific player prior is only activated after this many verified
# appearances. The history loader may search older seasons to reach it.
_VENUE_HISTORY_TARGET = 30
# Customer-facing Recent Matches is a complete archive, not the model's
# venue-scoped prior. Keep enough rows to make the archive useful on mobile;
# provider gaps may make 35 the honest floor, but never intentionally stop at
# the old 10/15/25-row samples.
_RECENT_ARCHIVE_TARGET = 50
_RECENT_ARCHIVE_MIN = 45
# A verified cache sample at this size is sufficient for the deterministic
# projection. Larger archive/venue targets remain useful context, but must not
# force a provider fan-out before the core prediction can return.
_PREDICTION_CACHE_MIN = 15
# Press Intensity is intentionally not called "stable" until it has at least
# seven recent, valid fixture packets. The provider can still return fewer
# rows; that is surfaced as a limited sample instead of being padded.
_PRESSURE_SAMPLE_TARGET = 7
# Customer-facing opponent pressure is a recent-team profile, not a single
# historical fixture row. Five valid completed matches is the minimum profile
# contract; extra candidate fixtures cover provider gaps without inventing data.
_OPPONENT_PRESSURE_MATCH_TARGET = 5
_OPPONENT_PRESSURE_CANDIDATE_LIMIT = 8
# A possession calculation is only called verified when BOTH clubs have this
# many exact fixture-statistics rows at the relevant venue.  A shorter sample
# remains visible as limited context but cannot drive the precise model signal.
_POSSESSION_SAMPLE_TARGET = POSSESSION_MIN_VERIFIED_SAMPLE


def _newest_first_rows(rows: list | None, limit: int | None = None) -> list:
    """Return fixture/history rows newest-first before any caller slices them.

    API-Football and the local fixture cache do not guarantee the same order.
    Sorting at the boundary keeps recent-form, pressure, matchup, and cohort
    evidence consistent regardless of which source won the race.
    """
    if not isinstance(rows, list):
        return []

    def _row_date(row):
        if not isinstance(row, dict):
            return ""
        fixture = row.get("fixture") or {}
        return str(
            row.get("date")
            or fixture.get("date")
            or row.get("matchDate")
            or ""
        )

    ordered = sorted(rows, key=_row_date, reverse=True)
    return ordered[:limit] if limit is not None else ordered


def _apply_optional_soccer_possession(
    game_log: dict,
    venue: str | None,
    home_possession: float | int | None,
    away_possession: float | int | None,
) -> dict:
    """Attach exact fixture possession without making it a history gate.

    Player minutes and the requested stat are required for a history row.
    Fixture possession is corroborating context and may be absent when the
    provider omits fixture statistics or only returns one side. Never infer a
    missing side from 100 minus the other side.
    """
    if (
        home_possession is not None
        and away_possession is not None
        and venue in {"home", "away"}
    ):
        team_possession = (
            home_possession if venue == "home" else away_possession
        )
        opponent_possession = (
            away_possession if venue == "home" else home_possession
        )
        game_log["teamPossession"] = team_possession
        game_log["opponentPossession"] = opponent_possession
        game_log["tp"] = team_possession
        game_log["possessionStatus"] = "verified"
        game_log["possessionSource"] = "fixture_statistics"
    else:
        game_log["teamPossession"] = None
        game_log["opponentPossession"] = None
        game_log.pop("tp", None)
        game_log["possessionStatus"] = "unavailable"
        game_log["possessionSource"] = None
    return game_log


def _filter_usable_soccer_history_logs(
    logs: list[dict] | None,
    prop_type: str,
) -> list[dict]:
    """Keep real appearances with usable evidence for the requested prop.

    Historical possession is intentionally not part of this admission rule.
    This is shared by the route's final quality gate and regression tests so
    a provider outage cannot turn an otherwise valid player history into a
    false verified-data error.
    """
    target_field = STAT_FIELD_MAP.get(prop_type, "passes_total")
    usable = []
    for game in logs or []:
        if not isinstance(game, dict) or game.get("synthetic"):
            continue
        if (game.get("minutes") or 0) <= 0:
            continue
        has_target_stat = (
            game.get(target_field) is not None
            or game.get("targetStat") is not None
        )
        # Stage-0 fixture-player cache rows are still real appearances even
        # when API-Football returned null for the prop (null means zero).
        # Their fixture metadata may be unavailable during provider quota
        # exhaustion; that optional context must not erase the appearance.
        cache_appearance = game.get("historySource") == "fixture_player_cache"
        if has_target_stat or cache_appearance:
            usable.append(game)
    return usable


def _json_safe_prediction(value, *, _active=None, _depth=0):
    """Detach a prediction graph before MongoDB/HTTP serialization.

    Diagnostic packets are assembled from several shared evidence objects. A
    late snapshot must never be able to create a circular response graph and
    turn an already-computed prediction into a 500. Repeated references are
    copied normally; only an object encountered again on the current traversal
    path is replaced with None.
    """
    if _depth > 80:
        return None
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (datetime,)):
        return value.isoformat()
    if isinstance(value, dict):
        active = _active if _active is not None else set()
        marker = id(value)
        if marker in active:
            return None
        active.add(marker)
        try:
            return {
                str(key): _json_safe_prediction(item, _active=active, _depth=_depth + 1)
                for key, item in value.items()
            }
        finally:
            active.remove(marker)
    if isinstance(value, (list, tuple, set)):
        active = _active if _active is not None else set()
        marker = id(value)
        if marker in active:
            return None
        active.add(marker)
        try:
            return [
                _json_safe_prediction(item, _active=active, _depth=_depth + 1)
                for item in value
            ]
        finally:
            active.remove(marker)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _reconcile_deterministic_confidence(
    text: str,
    confidence_score: float,
    confidence_level: str,
) -> str:
    """Replace stale confidence phrases without touching probability claims."""
    if not isinstance(text, str) or not text:
        return text
    final_label = f"{float(confidence_score):.0f}% ({confidence_level or 'Medium'})"
    reconciled = re.sub(
        r"Confidence:\s*\d+(?:\.\d+)?%\s*\([^)]*\)",
        f"Confidence: {final_label}",
        text,
        flags=re.IGNORECASE,
    )
    return re.sub(
        r"\b\d+(?:\.\d+)?%\s+confidence\s*\([^)]*\)",
        f"{final_label} confidence",
        reconciled,
        flags=re.IGNORECASE,
    )


def _recompute_landing_bands(
    bands: list,
    final_mean: float,
    final_line: float,
    final_std: float,
    source_center: float | None = None,
) -> list:
    """Rebuild landing probabilities from the final Gaussian snapshot.

    The terminal band is the OVER region and its lower boundary is forced to
    the exact line used by P(OVER)/P(UNDER). This prevents a stale integer
    display label or an earlier projection's band boundary from disagreeing
    with the displayed probabilities.
    """
    if not isinstance(bands, list) or not bands or final_std <= 0:
        return bands

    try:
        shift = (
            final_mean - float(source_center)
            if source_center is not None
            else 0.0
        )
    except (TypeError, ValueError):
        shift = 0.0

    normal_scale = final_std * math.sqrt(2.0)

    def cdf(value):
        if value is None:
            return 0.0
        return 0.5 * (1.0 + math.erf((value - final_mean) / normal_scale))

    rebuilt: list[dict] = []
    for index, source in enumerate(bands):
        if not isinstance(source, dict):
            continue
        lower = source.get("lower")
        upper = source.get("upper")
        try:
            lower = float(lower) + shift if lower is not None else None
        except (TypeError, ValueError):
            lower = None
        try:
            upper = float(upper) + shift if upper is not None else None
        except (TypeError, ValueError):
            upper = None

        # The final band is the displayed OVER region.  Anchoring it to the
        # request line makes its probability equal to P(OVER) by construction.
        if index == len(bands) - 1:
            lower = final_line
            upper = None
        elif index == len(bands) - 2:
            upper = final_line

        probability = (
            (cdf(upper) if upper is not None else 1.0)
            - (cdf(lower) if lower is not None else 0.0)
        ) * 100.0
        rebuilt.append({
            **source,
            "lower": round(lower, 1) if lower is not None else None,
            "upper": round(upper, 1) if upper is not None else None,
            "probability": round(max(0.0, probability), 1),
        })

    # Rounding should not make a packet fail the visible 100% invariant.
    if rebuilt:
        rounded_total = round(sum(float(item["probability"]) for item in rebuilt), 1)
        if abs(rounded_total - 100.0) > 0.05:
            rebuilt[-1]["probability"] = round(
                max(0.0, float(rebuilt[-1]["probability"]) + (100.0 - rounded_total)),
                1,
            )
    return rebuilt
def _age_from_birth_date(value: Any) -> int | None:
    """Calculate current age instead of trusting a season-snapshot age."""
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        birth_date = datetime.fromisoformat(raw[:10]).date()
    except (TypeError, ValueError):
        return None
    today = datetime.now(timezone.utc).date()
    return today.year - birth_date.year - (
        (today.month, today.day) < (birth_date.month, birth_date.day)
    )


def _provider_full_player_name(player: Any) -> str:
    """Return the verified provider name when first/last fields are present.

    API-Football's short ``name`` can be an initial or a display alias.  The
    separate first/last fields are the better identity source for cards,
    saved-pick rows, and share images.
    """
    if not isinstance(player, dict):
        return ""
    firstname = str(player.get("firstname") or "").strip()
    lastname = str(player.get("lastname") or "").strip()
    if firstname and lastname:
        return f"{firstname} {lastname}".strip()
    return ""


def _normalize_prediction_identity(prediction: dict, req: PredictionRequest) -> dict:
    """Keep the public prediction identity contract complete.

    The soccer engine resolves fixture identity in several stages and can
    legitimately leave request-shaped fields absent or explicitly null. The
    mobile card and saved-pick flow consume the top-level contract, so fill
    only identity fallbacks here; never replace a verified canonical fixture
    value with stale request data.
    """
    player = prediction.get("player") or {}
    provider_name = _provider_full_player_name(player)
    prediction["playerName"] = (
        prediction.get("canonicalPlayerName")
        or provider_name
        or prediction.get("playerName")
        or player.get("name")
        or req.playerName
        or ""
    )
    prediction["playerId"] = prediction.get("playerId") or player.get("id") or req.playerId
    prediction["teamName"] = prediction.get("teamName") or player.get("team") or req.teamName or ""
    prediction["opponentName"] = prediction.get("opponentName") or prediction.get("opponent") or req.opponentName or ""
    prediction["playerPosition"] = (
        prediction.get("playerPosition")
        or player.get("position")
        or prediction.get("position")
        or ""
    )
    # Promote profile facts into the compact response contract as well as the
    # nested identity packet. Saved-pick analysis reads this boundary directly.
    _calculated_age = _age_from_birth_date(
        (player.get("birth") or {}).get("date")
        if isinstance(player.get("birth"), dict)
        else None
    )
    _player_age = (
        _calculated_age
        if _calculated_age is not None
        else prediction.get("playerAge")
        if prediction.get("playerAge") is not None
        else player.get("age")
        if player.get("age") is not None
        else prediction.get("age")
    )
    if _player_age is not None:
        prediction["playerAge"] = _player_age
        prediction["age"] = _player_age
    _player_logs = prediction.get("playerGameLogs") or {}
    _avg_minutes = (
        prediction.get("averageMinutesPerMatch")
        if prediction.get("averageMinutesPerMatch") is not None
        else prediction.get("averageMinutesPerGame")
        if prediction.get("averageMinutesPerGame") is not None
        else _player_logs.get("avgMinutes")
        if isinstance(_player_logs, dict)
        else None
    )
    if _avg_minutes is not None:
        prediction["averageMinutesPerMatch"] = _avg_minutes
        prediction["averageMinutesPerGame"] = _avg_minutes
    prediction["leagueId"] = prediction.get("leagueId") or req.leagueId or None

    is_home = prediction.get("isHome")
    if not isinstance(is_home, bool):
        matchup = prediction.get("matchupOverview") or {}
        matchup_home = matchup.get("playerIsHome")
        is_home = matchup_home if isinstance(matchup_home, bool) else None
    if isinstance(is_home, bool):
        prediction["isHome"] = is_home
        prediction["playerIsHome"] = (
            prediction.get("playerIsHome")
            if isinstance(prediction.get("playerIsHome"), bool)
            else is_home
        )
        _canonical_venue = "home" if is_home else "away"
        _existing_venue = prediction.get("venue")
        if _existing_venue and _existing_venue != _canonical_venue:
            # venue field disagrees with playerIsHome — fixture team IDs are the
            # single source of truth for which side the player occupies.  Record
            # the contradiction so the saved snapshot is auditable; then override.
            if not prediction.get("venueWasRepaired"):
                prediction["venueWasRepaired"] = True
                prediction["originalRequestVenue"] = _existing_venue
            prediction["venue"] = _canonical_venue
        else:
            prediction["venue"] = _existing_venue or _canonical_venue
    else:
        prediction["venue"] = prediction.get("venue") or req.venue or "home"

    match_context = prediction.get("matchContext") or {}
    if not prediction.get("leagueName") and match_context.get("league"):
        prediction["leagueName"] = match_context["league"]
    return prediction


async def _attach_owner_prediction_media(prediction: dict, requester_email: str) -> None:
    """Attach cached player/team media to the owner response only."""
    try:
        from config import OWNER_EMAILS
        from owner_media import select_player_photo
        if (requester_email or "").lower().strip() not in OWNER_EMAILS:
            return
        player_id = prediction.get("playerId") or (prediction.get("player") or {}).get("id")
        team_id = prediction.get("fixtureTeamId") or prediction.get("teamId")
        opponent_id = prediction.get("fixtureOpponentId") or prediction.get("opponentId")
        if player_id:
            player_rows = await db["cache_players"].find(
                {"playerId": player_id},
                {"_id": 0, "playerId": 1, "teamId": 1, "photo": 1, "_cachedAt": 1},
            ).to_list(50)
            photo = select_player_photo(
                player_rows,
                player_id=player_id,
                team_id=team_id,
            )
            if photo:
                prediction["ownerPlayerPhoto"] = photo
        team_ids = [tid for tid in (team_id, opponent_id) if tid]
        if team_ids:
            logos = {}
            async for team in db["cache_teams"].find(
                {"teamId": {"$in": team_ids}}, {"_id": 0, "teamId": 1, "logo": 1}
            ):
                if team.get("logo"):
                    logos[team.get("teamId")] = team["logo"]
            if logos.get(team_id):
                prediction["ownerTeamLogo"] = logos[team_id]
            if logos.get(opponent_id):
                prediction["ownerOpponentLogo"] = logos[opponent_id]
    except Exception as exc:
        print(f"[PREDICTION] owner media skipped: {exc}")

# H2H history is intentionally broader than the current-season prediction
# window. The player-specific pass still caps the displayed sample so older
# meetings cannot dominate the model, but it must inspect enough real fixtures
# to find 4-5+ appearances when they exist.
H2H_HISTORY_SEASONS = 6
# API-Football's `last` parameter is applied per season. Keep enough merged
# meetings to find actual player appearances rather than stopping at the first
# dozen team fixtures from whichever season responded first.
H2H_FIXTURE_LIMIT = 48
H2H_PLAYER_SCAN_LIMIT = 24
H2H_PLAYER_RESULT_LIMIT = 20
_H2H_FINISHED_STATUSES = {"FT", "AET", "PEN", "AWD", "WO"}


def _api_response_list(payload) -> list:
    """Normalize list responses from the API-Football helper."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        response = payload.get("response", [])
        return response if isinstance(response, list) else []
    return []


def _legacy_h2h_display_date(value: Any, venue: Any) -> str:
    """Return a plain provider date for H2H rows.

    Older native bundles once needed H/A encoded into the date string, but the
    current client renders the venue marker separately. Keeping the API value
    as a normal ISO date prevents the legacy workaround from leaking into
    current cards and into saved analysis payloads.
    """
    date_text = str(value or "").strip()
    # ``venue`` remains in the signature for compatibility with callers that
    # already pass it; it must never be embedded into the date.
    _ = venue
    return date_text


def _normalize_provider_player_id(value):
    """Normalize provider IDs before joining separate API-Sports responses."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return str(value).strip() or None


def _lineup_player_status(payload, player_id: int | str | None) -> str:
    """Return starting/substitute/not_in_squad/unknown from lineup payload."""
    if player_id is None:
        return "unknown"
    target_id = _normalize_provider_player_id(player_id)
    if target_id is None:
        return "unknown"
    responses = _api_response_list(payload)
    if not responses:
        return "unknown"
    for team in responses:
        starters = team.get("startXI", []) if isinstance(team, dict) else []
        substitutes = team.get("substitutes", []) if isinstance(team, dict) else []
        substitute_ids = {
            _normalize_provider_player_id(item.get("player", {}).get("id"))
            for item in substitutes
            if isinstance(item, dict) and item.get("player", {}).get("id") is not None
        }
        starter_ids = {
            _normalize_provider_player_id(item.get("player", {}).get("id"))
            for item in starters
            if isinstance(item, dict) and item.get("player", {}).get("id") is not None
        }
        if target_id in starter_ids:
            return "starting"
        if target_id in substitute_ids:
            return "substitute"
    return "not_in_squad"


def compute_team_quality_gap(
    *,
    match_odds: dict | None,
    standing_data: dict | None,
    match_dominance: dict | None,
    requested_league_id: int | None = None,
    prop_type: str = "",
    position: str = "",
) -> dict:
    """Return one bounded, auditable team-quality adjustment for pass volume.

    The possession engine already owns possession multipliers and the Bayesian
    engine already owns its possession squeeze.  This helper therefore uses
    possession only as corroborating evidence; it never adds a second
    possession-derived multiplier.  Numeric quality strength comes from the
    verified fixture context, standings gap, and fixture market probability.
    """
    result = {
        "eligible": prop_type in {"pass_attempts", "passes", "key_passes", "crosses"},
        "applied": False,
        "multiplier": 1.0,
        "deltaPct": 0.0,
        "score": 0.0,
        "direction": "neutral",
        "competition": {
            "verified": bool((match_odds or {}).get("matchLeagueId") or (match_odds or {}).get("matchLeague")),
            "leagueId": (match_odds or {}).get("matchLeagueId"),
            "league": (match_odds or {}).get("matchLeague"),
            "requestedLeagueId": requested_league_id,
            "crossCompetition": bool(
                requested_league_id
                and (match_odds or {}).get("matchLeagueId")
                and int(requested_league_id) != int((match_odds or {}).get("matchLeagueId"))
            ),
        },
        "signals": [],
        "possessionCorroborates": False,
        "reason": "Not eligible for the bounded outfield passing quality signal.",
    }
    if not result["eligible"] or (position or "").upper() in {"GK", "GOALKEEPER"}:
        return result

    odds = match_odds or {}
    player_is_home = odds.get("playerIsHome")
    if player_is_home is None:
        player_is_home = True

    # API-Football odds are fixture-home/fixture-away. Normalize to the
    # player's team before using them as a quality signal.
    player_prob = None
    try:
        bookmaker = odds.get("bookmakerOdds") or {}
        if bookmaker.get("homeWin") and bookmaker.get("awayWin"):
            home_p = 1.0 / max(float(bookmaker["homeWin"]), 1.01)
            away_p = 1.0 / max(float(bookmaker["awayWin"]), 1.01)
            total = home_p + away_p
            if total > 0:
                player_prob = (home_p if player_is_home else away_p) / total
        elif (odds.get("americanOdds") or {}).get("home") is not None:
            def _american_prob(value):
                value = float(value)
                return (-value) / (-value + 100.0) if value < 0 else 100.0 / (value + 100.0)
            home_p = _american_prob(odds["americanOdds"]["home"])
            away_p = _american_prob(odds["americanOdds"]["away"])
            total = home_p + away_p
            if total > 0:
                player_prob = (home_p if player_is_home else away_p) / total
    except (TypeError, ValueError, ZeroDivisionError):
        player_prob = None
    if player_prob is not None:
        # ±1 means the market sees a roughly 90%/10% team.  The market is
        # deliberately capped and weighted below so it cannot dominate history.
        odds_score = max(-1.0, min(1.0, (player_prob - 0.5) / 0.40))
        result["signals"].append({
            "source": "market_implied_probability",
            "playerTeamProbability": round(player_prob, 3),
            "signedScore": round(odds_score, 3),
        })
    else:
        odds_score = None

    team_rank = (standing_data or {}).get("teamRank")
    opp_rank = (standing_data or {}).get("oppRank")
    rank_score = None
    try:
        if team_rank and opp_rank:
            # Positive means the player's team has the better rank (smaller number).
            rank_score = max(-1.0, min(1.0, (float(opp_rank) - float(team_rank)) / 20.0))
            result["signals"].append({
                "source": "verified_standings_gap",
                "teamRank": int(team_rank),
                "opponentRank": int(opp_rank),
                "signedScore": round(rank_score, 3),
            })
    except (TypeError, ValueError):
        rank_score = None

    expected_poss = (match_dominance or {}).get("expectedPoss")
    if (match_dominance or {}).get("hasRealPossData") and expected_poss is not None:
        try:
            poss_score = max(-1.0, min(1.0, (float(expected_poss) - 50.0) / 18.0))
            result["possessionCorroborates"] = (
                (odds_score is not None and poss_score * odds_score > 0.10)
                or (rank_score is not None and poss_score * rank_score > 0.10)
            )
            result["signals"].append({
                "source": "verified_possession",
                "expectedPossession": round(float(expected_poss), 1),
                "corroborates": result["possessionCorroborates"],
                "usedForNumericAdjustment": False,
            })
        except (TypeError, ValueError):
            pass

    # Require two independent quality sources, or a very strong market signal
    # corroborated by verified possession. This avoids turning a generic home
    # advantage into a quality-gap boost.
    sources = [s for s in (odds_score, rank_score) if s is not None]
    if len(sources) < 2 and not (
        odds_score is not None
        and abs(odds_score) >= 0.72
        and result["possessionCorroborates"]
    ):
        result["reason"] = "Insufficient independent quality evidence; possession was not double-counted."
        return result

    signed_score = (
        (odds_score * 0.60 if odds_score is not None else 0.0)
        + (rank_score * 0.40 if rank_score is not None else 0.0)
    )
    # If only the exceptional odds+possession path qualified, use the market
    # alone but keep the same cap.
    if odds_score is not None and rank_score is None:
        signed_score = odds_score
    signed_score = max(-1.0, min(1.0, signed_score))
    # At full strength this is ±12%, bounded independently of possession.
    delta_pct = round(signed_score * 12.0, 2)
    result.update({
        "applied": abs(delta_pct) >= 1.0,
        "multiplier": round(1.0 + delta_pct / 100.0, 4),
        "deltaPct": delta_pct,
        "score": round(signed_score, 3),
        "direction": "up" if delta_pct > 0 else "down" if delta_pct < 0 else "neutral",
        "reason": (
            "Independent team-quality evidence supports higher pass volume."
            if delta_pct > 0 else
            "Independent team-quality evidence supports lower pass volume."
            if delta_pct < 0 else
            "Independent team-quality evidence is neutral."
        ),
    })
    return result


def _merge_h2h_fixtures(*responses: list, limit: int = H2H_FIXTURE_LIMIT) -> list:
    """Merge real API-Football H2H responses into newest-first finished games."""
    by_id = {}
    for response in responses:
        if not isinstance(response, list):
            continue
        for fixture in response:
            if not isinstance(fixture, dict):
                continue
            fixture_id = (fixture.get("fixture") or {}).get("id")
            status = ((fixture.get("fixture") or {}).get("status") or {}).get("short")
            if not fixture_id or status not in _H2H_FINISHED_STATUSES:
                continue
            by_id[str(fixture_id)] = fixture

    return sorted(
        by_id.values(),
        key=lambda item: (item.get("fixture") or {}).get("date", ""),
        reverse=True,
    )[:limit]

# ── CALIBRATION TOGGLE ────────────────────────────────────────────────────────
# Nightly-learned bias offsets from historical pick outcomes.
# Priority: prop_rec (direction) > prop_league > prop_venue > prop (general).
# Direction offsets are the strongest signal — applied first.
# Each offset is dampened to 40% of raw mean error and capped at ±20% of posterior.
CALIBRATION_ENABLED = False  # Disabled — raw Bayesian projections proved more accurate than the learned-offset corrections
# ─────────────────────────────────────────────────────────────────────────────

# Match dominance cache: keyed by (home_team_id, away_team_id)
# Ensures the SAME game always returns identical possession numbers regardless of which player is scanned.
import time as _time
_match_dom_cache: dict = {}
_MATCH_DOM_TTL = 3600 * 6  # 6 hours

def _fixture_matchup(fixture: dict, team_id: int) -> dict | None:
    """Return the canonical matchup for team_id from an API-Football fixture."""
    home = fixture.get("teams", {}).get("home", {}) or {}
    away = fixture.get("teams", {}).get("away", {}) or {}
    if home.get("id") == team_id:
        player_team, opponent = home, away
        player_is_home = True
    elif away.get("id") == team_id:
        player_team, opponent = away, home
        player_is_home = False
    else:
        return None
    if not player_team.get("id") or not opponent.get("id"):
        return None
    return {
        "fixtureTeamId": player_team.get("id"),
        "fixtureTeamName": player_team.get("name", ""),
        "fixtureOpponentId": opponent.get("id"),
        "fixtureOpponentName": opponent.get("name", ""),
        "playerIsHome": player_is_home,
        "fixtureHomeId": home.get("id"),
        "fixtureHomeName": home.get("name", ""),
        "fixtureAwayId": away.get("id"),
        "fixtureAwayName": away.get("name", ""),
        "venue": "home" if player_is_home else "away",
    }


def _validate_fixture_identity(matchup: dict | None, *, team_id: int, opponent_id: int | None = None) -> tuple[bool, str]:
    """Reject contradictory team/venue identity before calculations begin."""
    if not isinstance(matchup, dict):
        return False, "fixture matchup missing"
    player_id = matchup.get("fixtureTeamId")
    fixture_opp_id = matchup.get("fixtureOpponentId")
    player_is_home = matchup.get("playerIsHome")
    if not player_id or not fixture_opp_id or player_id == fixture_opp_id:
        return False, "fixture team IDs are incomplete or identical"
    if team_id and player_id != team_id:
        return False, "fixture team does not match requested player team"
    if opponent_id and fixture_opp_id != opponent_id:
        # Opponent is intentionally allowed to be repaired from a stale request.
        # The canonical fixture still has to be internally consistent.
        pass
    if not isinstance(player_is_home, bool):
        return False, "fixture home/away assignment is missing"
    expected_venue = "home" if player_is_home else "away"
    if matchup.get("venue") not in {None, expected_venue}:
        return False, "fixture venue disagrees with playerIsHome"
    if player_is_home and matchup.get("fixtureHomeId") != player_id:
        return False, "home team ID disagrees with playerIsHome"
    if not player_is_home and matchup.get("fixtureAwayId") != player_id:
        return False, "away team ID disagrees with playerIsHome"
    return True, ""


def _select_player_context_for_league(
    docs: list[dict],
    league_id: int,
    requested_team_id: int = 0,
) -> dict | None:
    """Choose the player's club context for the selected competition.

    A single player ID can have both a national-team cache record and a club
    record.  The request's selected league is the authoritative context for
    fixture resolution; a national-team row must not make a Liga MX request
    resolve fixtures for Mexico instead of the player's Liga MX club.
    """
    if not league_id or league_id in INTERNATIONAL_LEAGUES:
        return None
    candidates = [
        d for d in docs
        if d.get("teamId") and d.get("leagueId") == league_id
    ]
    if not candidates:
        return None
    if requested_team_id:
        for doc in candidates:
            if doc.get("teamId") == requested_team_id:
                return doc
    # Prefer a real club row when a league has multiple cache contexts.
    return next(
        (d for d in candidates if d.get("leagueId") not in INTERNATIONAL_LEAGUES),
        candidates[0],
    )


@router.post("/predict")
async def predict(req: PredictionRequest):
    # Keep the display name defined before any optional enrichment or
    # fail-open branch can run. Older deployed builds referenced this local
    # before the later canonical-name assignment, turning a recoverable
    # provider refresh into the generic prediction error banner.
    player_team_display = req.teamName or ""
    from routes.auth import verify_session
    sess = await verify_session(req)
    if not sess.get("valid"):
        raise HTTPException(status_code=401, detail="Invalid or expired session. Please sign in again.")
    access = sess.get("access_type", "")
    if not access or access == "NoSubscription":
        raise HTTPException(status_code=403, detail="Active subscription required")
    # API-Football's fixture-player payload exposes passes.total, passes.key,
    # and passes.accuracy, but not a per-player cross count.  Keep the legacy
    # prop name recognizable for old saved rows and OCR payloads, but do not
    # manufacture a projection from a missing provider field.
    if req.sport == "soccer" and req.propType == "crosses":
        raise HTTPException(
            status_code=422,
            detail=(
                "Crosses are not available in the verified soccer player-stat "
                "feed yet. Choose Pass Attempts, Passes, or Key Passes instead."
            ),
        )
    # A prediction submitted by an older client must not bypass current-club
    # verification.  This protects against stale cache rows such as a player
    # remaining at Liverpool after moving to another club. Explicit national
    # team contexts remain allowed.
    if (
        req.sport == "soccer"
        and req.playerId
        and req.teamId
        and req.leagueId not in INTERNATIONAL_LEAGUES
    ):
        try:
            from routes.misc import _resolve_verified_club
            from cache import COL_NATIONAL
            is_national_context = await db[COL_NATIONAL].find_one(
                {"teamId": req.teamId}, {"_id": 1}
            )
            if not is_national_context:
                verified_club = await _resolve_verified_club(req.playerId)
                if not verified_club:
                    raise HTTPException(
                        status_code=409,
                        detail="The player's current club could not be verified. Please retry before predicting.",
                    )
                if int(req.teamId) != int(verified_club["teamId"]):
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            f"Current club changed to {verified_club['teamName']}. "
                            "Please reselect the player before predicting."
                        ),
                    )
        except HTTPException:
            raise
        except Exception as exc:
            print(f"[CLUB GUARD] player={req.playerId} verification failed: {exc}")
            raise HTTPException(
                status_code=409,
                detail="The player's current club could not be verified. Please retry before predicting.",
            )
        # User-triggered predictions must not be starved by the shared background
        # soft budget. The HTTP 429/daily-quota response still trips the real
        # circuit breaker in utils.py.
    _priority_token = set_api_request_priority(True)
    try:
        _prediction_started = aio.get_running_loop().time()

        def _prediction_elapsed() -> float:
            return aio.get_running_loop().time() - _prediction_started

        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        # Ordered numeric audit trail for the explanation layer.  This is
        # intentionally separate from analysisFactors: analysisFactors describe
        # evidence quality, while this ledger describes how the displayed
        # projection was actually transformed.
        _factor_ledger: list[dict] = []

        def _ledger_num(value):
            try:
                return round(float(value), 4) if value is not None else None
            except (TypeError, ValueError):
                return None

        def _record_projection_factor(
            factor_id: str,
            title: str,
            before,
            after,
            *,
            status: str = "applied",
            reason: str = "",
            inputs: dict | None = None,
            sample_size=None,
            multiplier=None,
        ):
            b = _ledger_num(before)
            a = _ledger_num(after)
            _factor_ledger.append({
                "id": factor_id,
                "title": title,
                "status": status,
                "before": b,
                "after": a,
                "delta": _ledger_num(a - b) if a is not None and b is not None else None,
                "direction": (
                    "up" if a is not None and b is not None and a > b
                    else "down" if a is not None and b is not None and a < b
                    else "neutral"
                ),
                "multiplier": _ledger_num(multiplier),
                "sampleSize": sample_size,
                "inputs": inputs or {},
                "reason": reason,
            })

        def _record_confidence_control(control_id: str, title: str, before, after, reason: str):
            _factor_ledger.append({
                "id": control_id,
                "title": title,
                "status": "applied" if before != after else "measured",
                "before": _ledger_num(before),
                "after": _ledger_num(after),
                "delta": _ledger_num(after - before) if before is not None and after is not None else None,
                "direction": "down" if after is not None and before is not None and after < before else "neutral",
                "multiplier": None,
                "sampleSize": None,
                "inputs": {},
                "reason": reason,
                "kind": "confidence",
            })
        # Prediction cache REMOVED: returning stale cached predictions caused
        # contradictions (e.g., wrong possession narrative when match data changed)
        # and undermined user trust. Every request now runs full fresh analysis.
        # Results are still stored in db.predictions for analytics/top-props.

        async def safe_fetch(endpoint, params, fallback=None):
            try:
                return await api_football_request(endpoint, params)
            except Exception:
                return fallback

        async def get_h2h_history(team_id: int, opponent_id: int, league_id: int):
            """Fetch a deep, deduplicated H2H history across recent seasons.

            API-Football's headtohead endpoint is season-scoped when `season`
            is supplied. A single current-season request silently omits older
            meetings, so search the recent six seasons and merge them.
            """
            if not team_id or not opponent_id:
                return []

            # Current-season config is 2025 for European competitions, while
            # calendar-year leagues (and the current date) are already in 2026.
            # Starting at 2026 covers both without changing the global season
            # constant used by the rest of the prediction pipeline.
            start_season = 2026 if league_id == 254 else max(CURRENT_SEASON + 1, 2026)
            seasons = list(range(start_season, start_season - H2H_HISTORY_SEASONS, -1))
            responses = await aio.gather(
                *[
                    safe_fetch(
                        "fixtures/headtohead",
                        {
                            "h2h": f"{team_id}-{opponent_id}",
                            "season": season,
                            "last": min(H2H_FIXTURE_LIMIT, 20),
                        },
                        [],
                    )
                    for season in seasons
                ],
                return_exceptions=True,
            )
            merged = _merge_h2h_fixtures(*responses)
            print(
                f"[H2H HISTORY] {team_id} vs {opponent_id}: "
                f"{len(merged)} finished meetings across seasons {seasons[0]}-{seasons[-1]}"
            )
            return merged

        async def get_player_data():
            if not req.playerId:
                return None
            # ── Local DB first (no API call if cached) ────────────────────
            try:
                from cache import get_cached_player_season_stats
                seasons_to_check = [CURRENT_SEASON + 1, CURRENT_SEASON, CURRENT_SEASON - 1, CURRENT_SEASON - 2]
                local_records = await get_cached_player_season_stats(req.playerId, seasons_to_check)
                if local_records:
                    all_data = local_records[0]
                    for rec in local_records[1:]:
                        all_data.setdefault("statistics", []).extend(rec.get("statistics", []))
                    return all_data
            except Exception:
                pass
            # ── Live API fallback (only when not yet cached) ──────────────
            # Skip for all soccer predictions — BDL is the sole data source.
            try:
                if _is_bdl_league:
                    return None
            except NameError:
                pass
            all_data = None
            for s in [CURRENT_SEASON + 1, CURRENT_SEASON, CURRENT_SEASON - 1, CURRENT_SEASON - 2]:
                try:
                    data = await api_football_request("players", {"id": req.playerId, "season": s})
                    if data:
                        entry = data[0]
                        if all_data is None:
                            all_data = entry
                        else:
                            all_data.setdefault("statistics", []).extend(entry.get("statistics", []))
                        # Write back to player_season_stats cache so future predictions
                        # survive quota exhaustion without hitting the API again
                        try:
                            _pid = entry.get("player", {}).get("id") or req.playerId
                            _doc = {
                                "_id_key": f"{_pid}_{s}",
                                "playerId": _pid,
                                "season": s,
                                "teamId": actual_team_id or 0,
                                "leagueId": league_id or 0,
                                "player": entry.get("player", {}),
                                "statistics": entry.get("statistics", []),
                                "_ts": __import__("time").time(),
                                "_dt": datetime.now(timezone.utc),
                            }
                            await db.player_season_stats.update_one(
                                {"_id_key": _doc["_id_key"]},
                                {"$set": _doc},
                                upsert=True
                            )
                        except Exception:
                            pass
                except Exception:
                    continue
            return all_data

        actual_team_id = req.teamId
        league_id = req.leagueId or 39
        # ── World Cup / International tournament mode ──────────────────────────
        # leagueId=1 = FIFA World Cup. Stats not available in API-Football for WC
        # (statistics_players=False), so we use club stats as the prior and apply
        # a neutral-venue + high-stakes treatment throughout the pipeline.
        _is_wc = False

        # ── AUTO-RESOLVE missing IDs from team/player names using local cache ──
        # This runs BEFORE ai_only_mode is decided, so predictions always have
        # real fixture data even when the scan didn't return numeric IDs.
        _resolved_opp_id = req.opponentId or 0
        _resolved_player_id = req.playerId or 0
        _resolved_team_name = req.teamName or ""
        _player_candidates: list = []  # populated when name-based resolution finds multiple matches

        try:
            from team_resolver import find_team as _find_team
            from cache import get_player_by_name as _get_player_by_name

            # 1. Resolve team ID from team name — always verify, never blindly trust req.teamId
            if req.teamName:
                try:
                    _t = await _find_team(req.teamName, league_id=league_id if league_id and league_id != 39 else None)
                    if _t and _t.get("teamId"):
                        _resolved_tid = _t["teamId"]
                        if _resolved_tid != actual_team_id:
                            print(f"[ID RESOLVE] '{req.teamName}' teamId corrected: {actual_team_id} → {_resolved_tid}")
                            actual_team_id = _resolved_tid
                        else:
                            print(f"[ID RESOLVE] '{req.teamName}' → teamId={actual_team_id} (confirmed)")
                    elif not actual_team_id or actual_team_id == 0:
                        print(f"[ID RESOLVE] '{req.teamName}' not found in local cache, keeping req.teamId={actual_team_id}")
                except Exception as _re:
                    print(f"[ID RESOLVE] team lookup failed: {_re}")

            # 2. Resolve opponent ID from opponent name — always verify.
            # Guard: if the frontend already supplied a national-team opponentId
            # (leagueId=0 from /api/search/teams) don't clobber it with a clubs hit.
            if req.opponentName:
                try:
                    from cache import COL_NATIONAL as _COL_NAT
                    _opp_is_national = req.opponentId and await db[_COL_NAT].count_documents(
                        {"teamId": req.opponentId}, limit=1
                    ) > 0
                    if _opp_is_national:
                        _resolved_opp_id = req.opponentId
                        print(f"[ID RESOLVE] '{req.opponentName}' opponentId={_resolved_opp_id} (national team — kept)")
                    else:
                        _o = await _find_team(req.opponentName)
                        if _o and _o.get("teamId"):
                            _resolved_opp_id = _o["teamId"]
                            print(f"[ID RESOLVE] '{req.opponentName}' → opponentId={_resolved_opp_id}")
                except Exception as _re:
                    print(f"[ID RESOLVE] opponent lookup failed: {_re}")

            # 3. Resolve player ID from player name
            if (not _resolved_player_id or _resolved_player_id == 0) and req.playerName:
                try:
                    # If the supplied team is a national-team context but the
                    # selected competition is domestic, do not constrain the
                    # player lookup to that national team. The player may be
                    # shown as "Mexico" in an older search result while the
                    # requested fixture is Liga MX.
                    _lookup_team_id = actual_team_id if actual_team_id and actual_team_id != 0 else None
                    _lookup_team_hint = req.teamName or None
                    if league_id not in INTERNATIONAL_LEAGUES and _lookup_team_id:
                        try:
                            from cache import COL_NATIONAL as _COL_NAT_PLAYER
                            if await db[_COL_NAT_PLAYER].count_documents(
                                {"teamId": _lookup_team_id}, limit=1
                            ) > 0:
                                _lookup_team_id = None
                                _lookup_team_hint = None
                        except Exception:
                            pass
                    _p = await _get_player_by_name(
                        req.playerName,
                        _lookup_team_id,
                        league_id=league_id if league_id and league_id != 39 else None,
                        team_name_hint=_lookup_team_hint,
                        prop_type=req.propType or None,
                    )
                    if _p and _p.get("playerId"):
                        _resolved_player_id = _p["playerId"]
                        if not actual_team_id or actual_team_id == 0:
                            actual_team_id = _p.get("teamId") or actual_team_id
                        if _p.get("teamName") and (
                            not actual_team_id or actual_team_id == _p.get("teamId")
                        ):
                            _resolved_team_name = _p.get("teamName") or _resolved_team_name
                        print(f"[ID RESOLVE] '{req.playerName}' → playerId={_resolved_player_id}, teamId={actual_team_id}")

                        # [PLAYER AMBIGUITY] If the player was resolved by name (no playerId supplied),
                        # check whether the cache holds multiple players with the same abbreviated nameClean.
                        # If so, surface all candidates in the response so the frontend can warn the user.
                        try:
                            _nc = (_p.get("nameClean") or "").strip()
                            if _nc:
                                from cache import COL_PLAYERS
                                _all_nc = await db[COL_PLAYERS].find(
                                    {"nameClean": _nc},
                                    {"playerId": 1, "name": 1, "teamName": 1, "position": 1, "leagueId": 1, "_id": 0}
                                ).to_list(15)
                                if len(_all_nc) > 1:
                                    _player_candidates = [
                                        {
                                            "playerId": m["playerId"],
                                            "playerName": m.get("name", ""),
                                            "teamName":   m.get("teamName", ""),
                                            "position":   m.get("position", ""),
                                            "leagueId":   m.get("leagueId"),
                                        }
                                        for m in _all_nc
                                    ]
                                    print(f"[PLAYER AMBIGUITY] '{_nc}' — {len(_all_nc)} candidates: "
                                          f"{[m.get('teamName','?') for m in _all_nc]}")
                        except Exception as _ae:
                            print(f"[PLAYER AMBIGUITY] check failed: {_ae}")
                except Exception as _re:
                    print(f"[ID RESOLVE] player lookup failed: {_re}")

            # A supplied playerId is still not enough to identify the team:
            # player IDs legitimately have both club and national-team cache
            # rows. For domestic requests, select the row belonging to the
            # requested league before resolving the fixture.
            if _resolved_player_id and league_id not in INTERNATIONAL_LEAGUES:
                try:
                    from cache import COL_PLAYERS as _COL_PLAYER_CONTEXT
                    _context_docs = await db[_COL_PLAYER_CONTEXT].find(
                        {"playerId": _resolved_player_id},
                        {"_id": 0, "playerId": 1, "teamId": 1, "teamName": 1, "leagueId": 1},
                    ).to_list(30)
                    _league_context = _select_player_context_for_league(
                        _context_docs, league_id, actual_team_id
                    )
                    if _league_context and _league_context.get("teamId") != actual_team_id:
                        print(
                            f"[PLAYER CONTEXT ALIGN] playerId={_resolved_player_id} "
                            f"league={league_id}: team {actual_team_id}/{req.teamName} "
                            f"→ {_league_context.get('teamId')}/{_league_context.get('teamName')}"
                        )
                        actual_team_id = _league_context["teamId"]
                        _resolved_team_name = _league_context.get("teamName") or _resolved_team_name
                except Exception as _context_err:
                    print(f"[PLAYER CONTEXT ALIGN] lookup failed: {_context_err}")

            # Bake resolved IDs back into req so all downstream references see them
            if (
                _resolved_opp_id != req.opponentId
                or _resolved_player_id != req.playerId
                or actual_team_id != req.teamId
                or _resolved_team_name != req.teamName
            ):
                req = req.model_copy(update={
                    "teamId": actual_team_id or 0,
                    "teamName": _resolved_team_name or req.teamName,
                    "opponentId": _resolved_opp_id,
                    "playerId": _resolved_player_id,
                })
        except Exception as _global_resolve_err:
            print(f"[ID RESOLVE] Global error: {_global_resolve_err}")

        ai_only_mode = (not actual_team_id or actual_team_id == 0 or not req.opponentId or req.opponentId == 0)
        if ai_only_mode:
            print(f"[ID RESOLVE] After resolution: teamId={actual_team_id}, opponentId={req.opponentId}, playerId={req.playerId}")

        # Guard: skip team/opponent API calls when IDs are missing
        safe_team_id = actual_team_id if actual_team_id and actual_team_id != 0 else None
        safe_opp_id = req.opponentId if req.opponentId and req.opponentId != 0 else None

        # Fire ALL API calls at once (optimized — kept odds for game context)
        async def get_team_stats_multi_season(team_id, lid):
            # ── Local DB first ─────────────────────────────────────────────
            try:
                from cache import get_cached_team_season_stats
                cached = await get_cached_team_season_stats(team_id, lid)
                if cached:
                    return cached
            except Exception:
                pass
            # ── Live API fallback ──────────────────────────────────────────
            for s in [CURRENT_SEASON + 1, CURRENT_SEASON, CURRENT_SEASON - 1]:
                result = await safe_fetch("teams/statistics", {"team": team_id, "league": lid, "season": s})
                if result:
                    return result
            return None

        async def get_match_odds():
            """Get bookmaker odds for the specific upcoming fixture between team and opponent.
            Uses team's next fixtures (across ALL competitions) to find the correct match."""
            try:
                fixture_match = None
                # The scan screen already resolved the active fixture for
                # soccer. Reuse that identity when available; the fallback
                # remains for older/manual clients that do not send it.
                if req.fixtureId:
                    direct_rows = await priority_api_football_request(
                        "fixtures", {"id": req.fixtureId}
                    )
                    if isinstance(direct_rows, list):
                        fixture_match = next(
                            (
                                row for row in direct_rows
                                if isinstance(row, dict)
                                and _fixture_matchup(row, actual_team_id)
                            ),
                            None,
                        )
                if not fixture_match:
                    canonical = await resolve_verified_fixture(
                        actual_team_id,
                        opponent_id=req.opponentId,
                        opponent_name=req.opponentName,
                        league_id=(
                            league_id
                            if league_id and league_id not in {39, 667, 666}
                            else None
                        ),
                    )
                    fixture_match = canonical["fixture"] if canonical else None

                if not fixture_match:
                    return None

                fid = fixture_match.get("fixture", {}).get("id")
                result = {}
                canonical_matchup = _fixture_matchup(fixture_match, actual_team_id)
                if not canonical_matchup:
                    # Never attach odds/context from a fixture that does not
                    # actually contain the requested player's team.
                    return None
                _fixture_ok, _fixture_reason = _validate_fixture_identity(
                    canonical_matchup,
                    team_id=actual_team_id,
                    opponent_id=req.opponentId,
                )
                if not _fixture_ok:
                    print(f"[FIXTURE INTEGRITY] rejected odds fixture: {_fixture_reason}")
                    return None
                result.update(canonical_matchup)
                if fid:
                    result["fixtureId"] = fid
                # Tag whether the player's team is the API-Football fixture's home team.
                # Used later to normalise moneyline home/away keys so they always
                # correspond to real_matchup.homeTeam / awayTeam regardless of
                # how API-Football labels the fixture.
                # Extract competition context (league/cup name + round)
                match_round = fixture_match.get("league", {}).get("round", "")
                match_league = fixture_match.get("league", {}).get("name", "")
                match_league_id = fixture_match.get("league", {}).get("id")
                match_date = fixture_match.get("fixture", {}).get("date", "")
                match_status = fixture_match.get("fixture", {}).get("status", {}).get("short", "")
                if match_round:
                    result["matchRound"] = match_round
                if match_league:
                    result["matchLeague"] = match_league
                if match_league_id:
                    result["matchLeagueId"] = match_league_id  # actual competition (e.g. Europa League = 3)
                if match_date:
                    result["matchDate"] = match_date
                if match_status:
                    result["matchStatus"] = match_status
                try:
                    odds = await api_football_request("odds", {"fixture": fid})
                    if odds:
                        for bk in odds[0].get("bookmakers", [])[:1]:
                            for bet in bk.get("bets", []):
                                if bet.get("name") == "Match Winner":
                                    vals = {v["value"]: v["odd"] for v in bet.get("values", [])}
                                    result["bookmakerOdds"] = {
                                        "source": bk.get("name", ""),
                                        "homeWin": vals.get("Home", ""),
                                        "draw": vals.get("Draw", ""),
                                        "awayWin": vals.get("Away", ""),
                                    }
                                    # Convert to American odds
                                    try:
                                        home_dec = float(vals.get("Home", 0))
                                        away_dec = float(vals.get("Away", 0))
                                        draw_dec = float(vals.get("Draw", 0))
                                        result["americanOdds"] = {
                                            "home": decimal_to_american(home_dec) if home_dec else "",
                                            "away": decimal_to_american(away_dec) if away_dec else "",
                                            "draw": decimal_to_american(draw_dec) if draw_dec else "",
                                        }
                                        result["favorite"] = "home" if home_dec < away_dec else "away"
                                        # Game type from odds spread
                                        fav_odds = min(home_dec, away_dec)
                                        if fav_odds < 1.3:
                                            result["gameType"] = "HEAVY FAVORITE — expect dominant performance, possible early subs"
                                        elif fav_odds < 1.7:
                                            result["gameType"] = "CLEAR FAVORITE — should control the game"
                                        elif fav_odds < 2.2:
                                            result["gameType"] = "SLIGHT FAVORITE — competitive match expected"
                                        else:
                                            result["gameType"] = "PICK'EM — very close, could go either way"
                                    except Exception:
                                        result["favorite"] = "home" if float(vals.get("Home", 99)) < float(vals.get("Away", 99)) else "away"
                except Exception:
                    pass
                return result if result else None
            except Exception:
                return None

        # When in AI-only mode (missing IDs), skip API calls that would waste quota
        async def noop_none(): return None
        async def noop_list(): return []

        _is_bdl_league = False  # API-Football is the primary soccer data source

        # Resolve the actual fixture before launching opponent-dependent
        # requests. Previously get_match_odds() could fall back to the team's
        # next fixture while leaving the stale requested opponent in req. That
        # produced contradictory cards such as Corinthians vs Bahia when the
        # actual fixture was Corinthians vs Athletico.
        # Capture the user-supplied venue BEFORE any fixture alignment rewrites req.venue.
        # This is the sole way to detect a home/away contradiction after model_copy
        # silently corrects req.venue at the prefetch boundary.
        _raw_request_venue = req.venue  # user-supplied value; never mutated after this line
        _venue_contradiction_detected = False  # set to True if fixture disagrees with user input

        match_odds_prefetched = None
        sgo_market_task = None
        if not ai_only_mode and actual_team_id and not _is_bdl_league:
            match_odds_prefetched = await get_match_odds()
            if not match_odds_prefetched:
                # Do not analyze a stale OCR/manual opponent when the current
                # fixture cannot be verified.  A clear retry is safer than a
                # polished prediction for the wrong game.
                raise HTTPException(
                    status_code=409,
                    detail="Could not verify the player's current or next fixture. Please retry shortly.",
                )
            _fixture_opp_id = (match_odds_prefetched or {}).get("fixtureOpponentId")
            _fixture_opp_name = (match_odds_prefetched or {}).get("fixtureOpponentName")
            _fixture_team_name = (match_odds_prefetched or {}).get("fixtureTeamName")
            if _fixture_opp_id and _fixture_opp_name:
                if (
                    _fixture_opp_id != req.opponentId
                    or _fixture_opp_name.strip().lower() != (req.opponentName or "").strip().lower()
                ):
                    print(
                        f"[FIXTURE CONTEXT ALIGN] requested={req.opponentName}({req.opponentId}) "
                        f"→ actual={_fixture_opp_name}({_fixture_opp_id}) "
                        f"fixture={(match_odds_prefetched or {}).get('fixtureId')}"
                    )
                _fixture_pih = (match_odds_prefetched or {}).get("playerIsHome")
                _fixture_venue_str = "home" if _fixture_pih else "away"
                if (
                    _fixture_pih is not None
                    and _raw_request_venue.lower() not in ("neutral",)
                    and _raw_request_venue.lower() != _fixture_venue_str
                ):
                    _venue_contradiction_detected = True
                    print(
                        f"[VENUE CONTRADICTION] user='{_raw_request_venue}' "
                        f"fixture='{_fixture_venue_str}' player={req.playerName} "
                        f"— repairing from verified fixture data"
                    )
                req = req.model_copy(update={
                    "opponentId": _fixture_opp_id,
                    "opponentName": _fixture_opp_name,
                    "teamName": _fixture_team_name or req.teamName,
                    "venue": _fixture_venue_str,
                })
                actual_team_id = (match_odds_prefetched or {}).get("fixtureTeamId") or actual_team_id
            _fixture_ok, _fixture_reason = _validate_fixture_identity(
                match_odds_prefetched,
                team_id=actual_team_id,
                opponent_id=req.opponentId,
            )
            if not _fixture_ok:
                raise HTTPException(
                    status_code=409,
                    detail=f"Verified fixture identity was inconsistent: {_fixture_reason}",
                )
            if req.sport == "soccer":
                _sgo_fixture = {
                    **match_odds_prefetched,
                    "fixtureHomeName": (
                        (match_odds_prefetched or {}).get("fixtureTeamName", "")
                        if (match_odds_prefetched or {}).get("playerIsHome")
                        else (match_odds_prefetched or {}).get("fixtureOpponentName", "")
                    ),
                    "fixtureAwayName": (
                        (match_odds_prefetched or {}).get("fixtureOpponentName", "")
                        if (match_odds_prefetched or {}).get("playerIsHome")
                        else (match_odds_prefetched or {}).get("fixtureTeamName", "")
                    ),
                }
                sgo_market_task = aio.create_task(
                    lookup_soccer_market_context(
                        player_name=req.playerName,
                        prop_type=req.propType,
                        entered_line=req.line,
                        fixture=_sgo_fixture,
                    )
                )

        # Recompute after canonical fixture alignment.
        safe_team_id = actual_team_id if actual_team_id and actual_team_id != 0 else None
        safe_opp_id = req.opponentId if req.opponentId and req.opponentId != 0 else None
        _manager_task = None   # set below in the API-Football branch

        if ai_only_mode:
            print(f"[AI-ONLY] Running in AI-only mode for {req.playerName} — teamId={actual_team_id}, opponentId={req.opponentId}")

            player_data_task = get_player_data() if req.playerId and req.playerId != 0 else noop_none()
            team_stats_task = noop_none()
            opponent_stats_task = noop_none()
            h2h_task = noop_list()
            standings_task = noop_none()
            fixtures_task = noop_list()
            odds_task = noop_none()
        elif _is_bdl_league:
            # BDL leagues: skip all API-Football enrichment — no H2H, odds, or fixture cache
            print(f"[BDL-GATE] Skipping API-Football Wave 1 tasks for BDL league {league_id}")
            player_data_task = get_player_data() if req.playerId and req.playerId != 0 else noop_none()
            team_stats_task = noop_none()
            opponent_stats_task = noop_none()
            h2h_task = noop_list()
            standings_task = noop_none()
            fixtures_task = noop_list()
            odds_task = noop_none()
        else:
            player_data_task = get_player_data()
            team_stats_task = get_team_stats_multi_season(actual_team_id, league_id)
            opponent_stats_task = get_team_stats_multi_season(req.opponentId, league_id)
            h2h_task = get_h2h_history(actual_team_id, req.opponentId, league_id)

            async def get_standings_multi_season():
                for s in [CURRENT_SEASON + 1, CURRENT_SEASON, CURRENT_SEASON - 1]:
                    result = await safe_fetch("standings", {"league": league_id, "season": s})
                    if result:
                        return result
                return None

            standings_task = get_standings_multi_season()
            fixtures_task = get_recent_fixtures_fast(actual_team_id, 40)
            odds_task = aio.sleep(0, result=match_odds_prefetched)

            # ── MANAGER CHANGE DETECTION (async, 7-day cached) ─────────────────
            # Runs concurrently with Wave-1 so it adds ~0 latency on cache hits.
            try:
                from manager_tracker import get_team_coach_info as _get_coach_info
                _manager_task = aio.ensure_future(
                    _get_coach_info(actual_team_id, db, api_football_request)
                )
            except Exception as _mgt_init_err:
                print(f"[MANAGER] task init error: {_mgt_init_err}")

        import time as _t
        _t0 = _t.time()
        player_stats, team_stats, opponent_stats, h2h_data, standings_raw, recent_fixtures, match_odds = await aio.gather(
            player_data_task, team_stats_task, opponent_stats_task, h2h_task, standings_task, fixtures_task, odds_task,
        )
        sgo_market_context = None
        if sgo_market_task is not None:
            try:
                sgo_market_context = await sgo_market_task
            except Exception as _sgo_err:
                print(f"[SGO] prediction context skipped: {type(_sgo_err).__name__}: {_sgo_err}")
        print(f"[TIMING] Wave 1: {_t.time()-_t0:.1f}s")

        if actual_team_id == 0 and player_stats:
            _pl_nat = (player_stats.get("player") or {}).get("nationality", "")
            for _st in (player_stats.get("statistics") or []):
                _t_name = (_st.get("team") or {}).get("name", "")
                if _pl_nat and _t_name and _t_name.strip().lower() == _pl_nat.strip().lower():
                    continue
                _t_id = (_st.get("team") or {}).get("id", 0)
                if _t_id:
                    actual_team_id = _t_id
                    break

        if not league_id and player_stats:
            _pl_nat = (player_stats.get("player") or {}).get("nationality", "")
            for _st in (player_stats.get("statistics") or []):
                _t_name = (_st.get("team") or {}).get("name", "")
                if _pl_nat and _t_name and _t_name.strip().lower() == _pl_nat.strip().lower():
                    continue
                _l_id = (_st.get("league") or {}).get("id", 0)
                if _l_id:
                    league_id = _l_id
                    break
            if not league_id:
                league_id = 39

        # Recovery: if ai_only_mode skipped fixture fetching but we now have a real team ID,
        # fetch recent fixtures retroactively so the Reverse Formula has game log data.
        # Skipped for BDL leagues — BDL game logs are fetched separately.
        if actual_team_id and actual_team_id != 0 and not recent_fixtures and not _is_bdl_league:
            try:
                print(f"[FIXTURE RECOVERY] Fetching fixtures for recovered teamId={actual_team_id}")
                recent_fixtures = await get_recent_fixtures_fast(actual_team_id, 40)
            except Exception as _fre:
                print(f"[FIXTURE RECOVERY] Error: {_fre}")

        # Cache and provider fixture order is not contractual. Normalize it
        # before venue filters and all later [:N] slices so every downstream
        # history sample starts with the newest verified match.
        recent_fixtures = _newest_first_rows(recent_fixtures)

        # ── SINGLE SOURCE OF TRUTH: correct club team name ──────────────────────
        # Trust req.teamName (what the user explicitly scanned) as primary.
        # Only use API-Football stats to SUPPLEMENT when req.teamName is empty.
        # Never let a national-team or historical-club entry override the user's input.
        corrected_team_name = req.teamName or ""
        if player_stats and not corrected_team_name:
            _pl_nat2 = (player_stats.get("player") or {}).get("nationality", "")
            for _st2 in (player_stats.get("statistics") or []):
                _t2_name = (_st2.get("team") or {}).get("name", "")
                if _pl_nat2 and _t2_name and _t2_name.strip().lower() == _pl_nat2.strip().lower():
                    continue  # skip national team entries
                if _t2_name:
                    corrected_team_name = _t2_name
                    break
        print(f"[TEAM] corrected_team_name={corrected_team_name!r} (req.teamName={req.teamName!r})")

        standings = []
        if standings_raw:
            try:
                standings = standings_raw[0].get("league", {}).get("standings", [[]])[0]
            except (IndexError, AttributeError):
                pass

        # =============================================
        # WAVE 2: Deep per-fixture data (uses fixture IDs from Wave 1)
        # =============================================

        # 1. Per-fixture team stats (possession, shots, passes per match)
        async def fetch_fixture_team_stats(
            fixture_list,
            team_id,
            limit=_PRESSURE_SAMPLE_TARGET,
            *,
            include_player_actions=True,
        ):
            """Fetch per-match team stats — cached in MongoDB for finished fixtures.

            Fetches two data sources per fixture:
              1. /fixtures/statistics  → possession, passes, shots, fouls (team-level)
              2. /fixtures/players     → player-level data aggregated for tackles +
                                         interceptions (not available at team level in
                                         /fixtures/statistics)

            Cached together under fxt_{fid}_{team_id}. Existing cache entries missing
            tackles data are enriched incrementally (one extra API call, then re-cached).
            """
            async def fetch_one(fix):
                fid = fix.get("fixtureId")
                if not fid:
                    return None
                try:
                    cache_key = f"fxt_{fid}_{team_id}"
                    cached = await db.fixture_player_cache.find_one({"_k": cache_key}, {"_id": 0, "d": 1})

                    # Full cache hit — has the API-Football pressure inputs cached.
                    # Older rows are deliberately enriched below instead of
                    # silently reusing the pre-press schema.
                    if (
                        cached
                        and cached.get("d")
                        and "fouls_committed_agg" in cached["d"]
                        and "opponentTotalPasses" in cached["d"]
                        and "duels_won_agg" in cached["d"]
                        and (
                            not include_player_actions
                            or cached["d"].get("pressureActionSource")
                            == "api_football_fixture_players"
                        )
                    ):
                        r = cached["d"]
                        r["date"] = fix.get("date", "")[:10]
                        r["opponent"] = fix.get("opponent", "")
                        r["venue"] = fix.get("venue", "")
                        r["score"] = f"{fix.get('homeGoals',0)}-{fix.get('awayGoals',0)}"
                        # goals_conceded: goals scored AGAINST the opponent in this fixture
                        _fv = fix.get("venue", "home")
                        r["goals_conceded"] = (fix.get("awayGoals", 0)
                                               if _fv == "home"
                                               else fix.get("homeGoals", 0))
                        return r

                    # Partial cache hit — preserve what is known, then enrich
                    # missing pressure fields from the same fixture.  The
                    # opponent's pass total is the numerator for synthetic
                    # pressing; the defending team's own passes are not.
                    result = dict(cached["d"]) if cached and cached.get("d") else {}
                    if "opponentTotalPasses" not in result:
                        data = await api_football_request("fixtures/statistics", {"fixture": fid})
                        if data:
                            stats_by_team = {}
                            for team_data in data:
                                raw_stats = {}
                                for stat in team_data.get("statistics", []):
                                    raw_stats[stat.get("type", "")] = stat.get("value")
                                stats_by_team[(team_data.get("team") or {}).get("id")] = raw_stats

                            raw_stats = stats_by_team.get(team_id) or {}
                            opponent_stats = next(
                                (stats for tid, stats in stats_by_team.items() if tid and tid != team_id),
                                {},
                            )
                            if raw_stats:
                                result.update({
                                    "possession": raw_stats.get("Ball Possession", result.get("possession", "")),
                                    "totalShots": raw_stats.get("Total Shots", result.get("totalShots")),
                                    "shotsOnTarget": raw_stats.get("Shots on Goal", result.get("shotsOnTarget")),
                                    "shotsOffTarget": raw_stats.get("Shots off Goal", result.get("shotsOffTarget")),
                                    "blockedShots": raw_stats.get("Blocked Shots", result.get("blockedShots")),
                                    "shotsInsideBox": raw_stats.get("Shots insidebox", result.get("shotsInsideBox")),
                                    "shotsOutsideBox": raw_stats.get("Shots outsidebox", result.get("shotsOutsideBox")),
                                    "totalPasses": raw_stats.get("Total passes", result.get("totalPasses")),
                                    "passAccuracy": raw_stats.get("Passes %", result.get("passAccuracy")),
                                    "accuratePasses": raw_stats.get("Passes accurate", result.get("accuratePasses")),
                                    "fouls": raw_stats.get("Fouls", result.get("fouls")),
                                    "corners": raw_stats.get("Corner Kicks", result.get("corners")),
                                    "expectedGoals": raw_stats.get("expected_goals", result.get("expectedGoals")),
                                    "opponentTotalPasses": opponent_stats.get("Total passes"),
                                    "opponentPossession": opponent_stats.get("Ball Possession"),
                                    "opponentTotalShots": opponent_stats.get("Total Shots"),
                                })

                    if not result:
                        return None

                    # Fetch player-level data to aggregate tackles + interceptions
                    # (these are not available from /fixtures/statistics at team level)
                    # Player-level rows add tackles/interceptions/duels, but
                    # API-Football can stall on this endpoint while the
                    # fixture-level statistics endpoint already has a real
                    # defensive input (team fouls). Bound the enrichment so a
                    # slow optional endpoint cannot hide an exact fixture.
                    try:
                        player_data = []
                        if include_player_actions:
                            player_data = await aio.wait_for(
                                api_football_request(
                                    "fixtures/players", {"fixture": fid, "team": team_id}
                                ),
                                timeout=3.0,
                            )
                        tkl_total  = 0
                        tkl_int    = 0
                        tkl_blocks = 0
                        fls_committed = 0
                        duels_won = 0
                        duels_total = 0
                        cards_yellow = 0
                        cards_red = 0
                        got_tkl = False
                        if player_data:
                            for team_block in player_data:
                                if team_block.get("team", {}).get("id") == team_id:
                                    for p in team_block.get("players", []):
                                        st  = (p.get("statistics") or [{}])[0]
                                        tkl = st.get("tackles") or {}
                                        fls = st.get("fouls")   or {}
                                        crd = st.get("cards")   or {}
                                        tkl_total     += (tkl.get("total")          or 0)
                                        tkl_int       += (tkl.get("interceptions")  or 0)
                                        tkl_blocks    += (tkl.get("blocks")         or 0)
                                        fls_committed += (fls.get("committed")      or 0)
                                        duel = st.get("duels") or {}
                                        duels_won     += (duel.get("won")           or 0)
                                        duels_total   += (duel.get("total")         or 0)
                                        cards_yellow  += (crd.get("yellow")         or 0)
                                        cards_red     += (crd.get("red")            or 0)
                                    got_tkl = True
                                    break
                        # API-Football's player rows provide the defensive
                        # actions used by the synthetic Press Intensity
                        # denominator.  Recoveries and blocked-pass locations
                        # are not available, so they are never invented.
                        result["tackles_total"]         = tkl_total     if got_tkl else None
                        result["tackles_interceptions"] = tkl_int       if got_tkl else None
                        result["tackles_blocks"]        = tkl_blocks    if got_tkl else None
                        result["fouls_committed_agg"]   = (
                            fls_committed
                            if got_tkl
                            else result.get("fouls")
                        )
                        result["duels_won_agg"]          = duels_won     if got_tkl else None
                        result["duels_total_agg"]        = duels_total   if got_tkl else None
                        result["cards_yellow_agg"]      = cards_yellow  if got_tkl else None
                        result["cards_red_agg"]         = cards_red     if got_tkl else None
                        result["pressureActionSource"] = (
                            "api_football_fixture_players"
                            if got_tkl
                            else "api_football_fixture_statistics_fouls"
                            if result.get("fouls") is not None
                            else None
                        )
                    except Exception:
                        result["tackles_total"]         = None
                        result["tackles_interceptions"] = None
                        result["tackles_blocks"]        = None
                        result["fouls_committed_agg"]   = result.get("fouls")
                        result["duels_won_agg"]          = None
                        result["duels_total_agg"]        = None
                        result["cards_yellow_agg"]      = None
                        result["cards_red_agg"]         = None
                        result["pressureActionSource"] = (
                            "api_football_fixture_statistics_fouls"
                            if result.get("fouls") is not None
                            else None
                        )

                    # Cache the enriched result when storage permits. Atlas
                    # quota exhaustion must never discard an otherwise valid
                    # fixture/player result.
                    try:
                        await db.fixture_player_cache.update_one(
                            {"_k": cache_key}, {"$set": {"_k": cache_key, "_ts": datetime.now(timezone.utc), "d": result}}, upsert=True
                        )
                    except Exception as _cache_write_err:
                        print(f"[FIXTURE CACHE WRITE] skipped: {_cache_write_err}")
                    result["date"]     = fix.get("date", "")[:10]
                    result["opponent"] = fix.get("opponent", "")
                    result["venue"]    = fix.get("venue", "")
                    result["score"]    = f"{fix.get('homeGoals',0)}-{fix.get('awayGoals',0)}"
                    # goals_conceded: goals scored AGAINST the opponent in this fixture
                    _fv2 = fix.get("venue", "home")
                    result["goals_conceded"] = (fix.get("awayGoals", 0)
                                                if _fv2 == "home"
                                                else fix.get("homeGoals", 0))
                    return result
                except Exception:
                    return None

            tasks = [
                fetch_one(fix)
                for fix in _newest_first_rows(fixture_list, limit)
            ]
            results_raw = await aio.gather(*tasks, return_exceptions=True)
            return _newest_first_rows(
                [r for r in results_raw if r and not isinstance(r, Exception)]
            )

        async def fetch_recent_opponent_press_intensity(
            history_rows,
            *,
            limit=_RECENT_ARCHIVE_TARGET,
            max_network_matches=50,
        ):
            """Attach a recent opponent pressure profile to every history row.

            The old implementation scored the opponent from the same single
            fixture as the player's history row. That produced misleading
            ``N=1`` cards and made the value look like a direct measurement.
            This explanation-only packet now builds one cached profile per
            opponent from at least five of that team's recent completed
            fixtures, then reuses that profile for each matching player row.
            """
            from bayesian_engine import compute_press_intensity_score

            profile_version = "opponent-pressure-v5"
            profile_cache_prefix = "opp_press_profile_v5_"
            target_matches = _OPPONENT_PRESSURE_MATCH_TARGET
            candidate_limit = _OPPONENT_PRESSURE_CANDIDATE_LIMIT

            def unavailable_packet(reason, *, status="unavailable"):
                return {
                    "available": False,
                    "status": status,
                    "score": None,
                    "score100": None,
                    "multiplier": 1.0,
                    "label": "Unavailable",
                    "signal_used": None,
                    "source": "api_football",
                    "metric": "reverse_picks_pressure_index",
                    "scoreInterpretation": (
                        "Reverse Picks Pressure Index is a custom bounded 0-100 product rating; "
                        "it is not PPDA, a count of pressure events, or a raw provider statistic."
                    ),
                    "sampleSize": 0,
                    "sampleTarget": target_matches,
                    "sampleStatus": "unavailable",
                    "sampleUnit": "opponent_recent_fixture",
                    "profileScope": "opponent_recent_matchups",
                    "reason": reason,
                    "projectionApplied": False,
                    "projectionMultiplier": 1.0,
                }

            rows = []
            seen = set()
            for row in _newest_first_rows(history_rows, limit):
                if not isinstance(row, dict):
                    continue
                fid = str(row.get("_fid") or row.get("fixtureId") or "").strip()
                if not fid or fid in seen:
                    continue
                seen.add(fid)
                rows.append(row)

            # Group by verified opponent team ID. A single opponent profile is
            # intentionally shared across its player-history rows so one club
            # cannot consume the provider quota once per appearance.
            opponents = {}
            for row in rows:
                opponent_id = row.get("fixtureOpponentId") or row.get("opponentId")
                if not opponent_id:
                    continue
                try:
                    opponent_id = int(opponent_id)
                except (TypeError, ValueError):
                    continue
                key = str(opponent_id)
                if key not in opponents:
                    opponents[key] = {
                        "teamId": opponent_id,
                        "name": row.get("opponent") or "Opponent",
                    }

            def _fixture_date(raw):
                fixture = raw.get("fixture") or {}
                return str(
                    raw.get("date")
                    or fixture.get("date")
                    or raw.get("matchDate")
                    or "",
                )[:10]

            def _normalize_team_fixture(raw, team_id):
                """Normalize API-Football or cached team fixture rows."""
                if not isinstance(raw, dict):
                    return None
                fixture = raw.get("fixture") or {}
                teams = raw.get("teams") or {}
                home = teams.get("home") or {}
                away = teams.get("away") or {}
                fixture_id = fixture.get("id") or raw.get("fixtureId")
                home_id = home.get("id") or raw.get("homeTeamId")
                away_id = away.get("id") or raw.get("awayTeamId")
                if not fixture_id or home_id is None or away_id is None:
                    return None
                if team_id not in {home_id, away_id}:
                    return None
                date_value = _fixture_date(raw)
                if not date_value:
                    return None
                status_short = str(
                    (fixture.get("status") or {}).get("short")
                    or raw.get("statusShort")
                    or "",
                ).upper()
                if status_short and status_short not in {"FT", "AET", "PEN", "AWD", "WO"}:
                    return None
                try:
                    if datetime.strptime(date_value, "%Y-%m-%d").date() > datetime.now(
                        timezone.utc
                    ).date():
                        return None
                except ValueError:
                    pass
                team_is_home = home_id == team_id
                return {
                    "fixtureId": fixture_id,
                    "date": date_value,
                    "opponent": (
                        (away.get("name") if team_is_home else home.get("name"))
                        or raw.get("opponent")
                        or "Opponent",
                    ),
                    "venue": "home" if team_is_home else "away",
                    "homeGoals": (raw.get("goals") or {}).get("home", 0)
                    if raw.get("goals") is not None
                    else raw.get("homeGoals", 0),
                    "awayGoals": (raw.get("goals") or {}).get("away", 0)
                    if raw.get("goals") is not None
                    else raw.get("awayGoals", 0),
                }

            team_fixture_pools = {}

            async def load_opponent_fixture_pool(team_id):
                key = str(team_id)
                if key in team_fixture_pools:
                    return team_fixture_pools[key]
                raw_fixtures = []
                try:
                    from cache import get_cached_team_fixtures
                    raw_fixtures = await get_cached_team_fixtures(team_id)
                except Exception:
                    raw_fixtures = []

                normalized = []
                seen_fixture_ids = set()

                def add_fixtures(candidate_rows):
                    for raw in candidate_rows or []:
                        normalized_row = _normalize_team_fixture(raw, team_id)
                        if not normalized_row:
                            continue
                        fixture_key = str(normalized_row["fixtureId"])
                        if fixture_key in seen_fixture_ids:
                            continue
                        seen_fixture_ids.add(fixture_key)
                        normalized.append(normalized_row)

                add_fixtures(raw_fixtures)
                if len(normalized) < target_matches:
                    try:
                        live_fixtures = await aio.wait_for(
                            api_football_request(
                                "fixtures",
                                {
                                    "team": team_id,
                                    "last": candidate_limit * 2,
                                    "status": "FT",
                                },
                            ),
                            timeout=6.0,
                        )
                        add_fixtures(live_fixtures)
                    except Exception as fixture_err:
                        print(
                            f"[OPP PRESS FIXTURES] team={team_id} "
                            f"unavailable={type(fixture_err).__name__}"
                        )
                normalized = _newest_first_rows(normalized, candidate_limit)
                team_fixture_pools[key] = normalized
                return normalized

            async def read_cached_profile(opponent):
                team_id = opponent["teamId"]
                try:
                    doc = await db.fixture_player_cache.find_one(
                        {"_k": f"{profile_cache_prefix}{team_id}"},
                        {"_id": 0, "d": 1},
                    )
                    profile = (doc or {}).get("d")
                    packet = (profile or {}).get("pressIntensity") if isinstance(profile, dict) else None
                    if (
                        isinstance(profile, dict)
                        and profile.get("version") == profile_version
                        and isinstance(packet, dict)
                        and packet.get("available") is True
                        and int(packet.get("sampleSize") or 0) >= target_matches
                    ):
                        return profile
                except Exception:
                    pass
                return None

            cached_profiles = {}
            cached_results = await aio.gather(
                *(read_cached_profile(opponent) for opponent in opponents.values()),
                return_exceptions=True,
            )
            for opponent, profile in zip(opponents.values(), cached_results):
                if isinstance(profile, dict):
                    cached_profiles[str(opponent["teamId"])] = profile

            def _has_pressure_action(row):
                return isinstance(row, dict) and any(
                    row.get(field) is not None
                    for field in (
                        "tackles_total",
                        "tackles_interceptions",
                        "tackles_blocks",
                        "duels_won_agg",
                        "fouls_committed_agg",
                    )
                )

            def _profile_match_rows(stats_rows):
                matches = []
                for stats_row in _newest_first_rows(stats_rows):
                    if not _has_pressure_action(stats_row):
                        continue
                    single = compute_press_intensity_score([stats_row])
                    matches.append({
                        "fixtureId": stats_row.get("fixtureId"),
                        "date": stats_row.get("date"),
                        "opponent": stats_row.get("opponent"),
                        "venue": stats_row.get("venue"),
                        "score": stats_row.get("score"),
                        "pressureLabel": (
                            single.get("label")
                            if single.get("available") is True
                            else None
                        ),
                        "pressureIndex": (
                            single.get("score100")
                            if single.get("available") is True
                            else None
                        ),
                    })
                return matches

            async def build_opponent_profile(opponent):
                team_id = opponent["teamId"]
                team_name = opponent["name"]
                try:
                    fixture_pool = await load_opponent_fixture_pool(team_id)
                    if len(fixture_pool) < target_matches:
                        packet = unavailable_packet(
                            f"Only {len(fixture_pool)} recent completed opponent matches "
                            f"were found; {target_matches} are required.",
                            status="insufficient_sample",
                        )
                        stats_rows = []
                    else:
                        # Player-level defensive actions are requested when
                        # possible. fetch_fixture_team_stats falls back to
                        # exact team fouls when the optional player endpoint is
                        # unavailable, so a provider gap never becomes zero.
                        stats_rows = await fetch_fixture_team_stats(
                            fixture_pool,
                            team_id,
                            limit=min(len(fixture_pool), candidate_limit),
                            include_player_actions=True,
                        )
                        packet = compute_press_intensity_score(stats_rows or [])
                        valid_count = int(packet.get("sampleSize") or 0)
                        if packet.get("available") is not True or valid_count < target_matches:
                            packet = unavailable_packet(
                                f"Only {valid_count} valid pressure matches were returned "
                                f"from the opponent's recent {target_matches}-match target.",
                                status="insufficient_sample",
                            )
                    sample_matches = _profile_match_rows(stats_rows)
                    if packet.get("available") is True:
                        packet.update({
                            "sampleTarget": target_matches,
                            "sampleUnit": "opponent_recent_fixture",
                            "profileScope": "opponent_recent_matchups",
                            "recentMatchupsUsed": len(sample_matches),
                        })
                    profile = {
                        "version": profile_version,
                        "opponentId": team_id,
                        "opponent": team_name,
                        "status": (
                            "available"
                            if packet.get("available") is True
                            else packet.get("status") or "unavailable"
                        ),
                        "verified": packet.get("available") is True,
                        "source": (
                            "API-Football opponent fixtures + fixture statistics "
                            "+ fixture player defensive actions"
                        ),
                        "sampleTarget": target_matches,
                        "sampleMatches": sample_matches,
                        "pressIntensity": packet,
                        "reason": packet.get("reason"),
                    }
                    try:
                        await db.fixture_player_cache.update_one(
                            {"_k": f"{profile_cache_prefix}{team_id}"},
                            {
                                "$set": {
                                    "_k": f"{profile_cache_prefix}{team_id}",
                                    "_ts": datetime.now(timezone.utc),
                                    "d": profile,
                                }
                            },
                            upsert=True,
                        )
                    except Exception as cache_err:
                        print(
                            f"[OPP PRESS CACHE] team={team_id} skipped="
                            f"{type(cache_err).__name__}"
                        )
                    return profile
                except Exception as exc:
                    packet = unavailable_packet(
                        f"opponent_recent_pressure_{type(exc).__name__}"
                    )
                    return {
                        "version": profile_version,
                        "opponentId": team_id,
                        "opponent": team_name,
                        "status": "unavailable",
                        "verified": False,
                        "source": None,
                        "sampleTarget": target_matches,
                        "sampleMatches": [],
                        "pressIntensity": packet,
                        "reason": packet.get("reason"),
                    }

            pending_all = [
                opponent
                for key, opponent in opponents.items()
                if key not in cached_profiles
            ]
            pending = pending_all[:max(0, int(max_network_matches or 0))]
            deferred = pending_all[len(pending):]
            profile_sem = aio.Semaphore(4)

            async def bounded_profile(opponent):
                async with profile_sem:
                    try:
                        return await aio.wait_for(
                            build_opponent_profile(opponent),
                            timeout=30.0,
                        )
                    except Exception as exc:
                        return {
                            "version": profile_version,
                            "opponentId": opponent["teamId"],
                            "opponent": opponent["name"],
                            "status": "unavailable",
                            "verified": False,
                            "source": None,
                            "sampleTarget": target_matches,
                            "sampleMatches": [],
                            "pressIntensity": unavailable_packet(
                                f"opponent_recent_pressure_{type(exc).__name__}"
                            ),
                            "reason": f"opponent_recent_pressure_{type(exc).__name__}",
                        }

            async def warm_profiles(opponents_to_warm, *, response_budget=16.0):
                if not opponents_to_warm:
                    return

                task_map = {
                    aio.create_task(bounded_profile(opponent)): opponent
                    for opponent in opponents_to_warm
                }

                def store_completed(done_tasks):
                    for task in done_tasks:
                        try:
                            profile = task.result()
                        except Exception:
                            continue
                        if isinstance(profile, dict):
                            opponent = task_map.get(task)
                            if opponent:
                                cached_profiles[str(opponent["teamId"])] = profile

                # Do not wait for every opponent before returning. The old
                # gather() made a slow 18-opponent batch discard profiles that
                # had already completed, which rendered 0/N in the first
                # response even though the provider had returned usable data.
                done, pending_tasks = await aio.wait(
                    set(task_map),
                    timeout=response_budget,
                )
                store_completed(done)

                if pending_tasks:
                    async def finish_remaining():
                        # Keep warming the cache after the core response is
                        # assembled. Each completed profile is still persisted
                        # by build_opponent_profile for the next render.
                        await aio.gather(*pending_tasks, return_exceptions=True)

                    aio.create_task(finish_remaining())

            await warm_profiles(pending)
            if deferred:
                aio.create_task(warm_profiles(deferred))

            profiles = []
            for row in rows:
                fid = str(row.get("_fid") or row.get("fixtureId") or "")
                opponent_id = row.get("fixtureOpponentId") or row.get("opponentId")
                try:
                    opponent_key = str(int(opponent_id)) if opponent_id else ""
                except (TypeError, ValueError):
                    opponent_key = ""
                baseline = cached_profiles.get(opponent_key)
                if baseline:
                    packet = dict(baseline.get("pressIntensity") or {})
                    profile_status = baseline.get("status") or packet.get("status")
                    profile = {
                        "fixtureId": row.get("_fid") or row.get("fixtureId"),
                        "date": row.get("date"),
                        "opponent": row.get("opponent"),
                        "venue": row.get("venue"),
                        "opponentId": opponent_id,
                        "status": profile_status,
                        "verified": baseline.get("verified") is True,
                        "source": baseline.get("source"),
                        "pressureScope": "opponent_recent_matchups",
                        "sampleTarget": target_matches,
                        "sampleMatches": baseline.get("sampleMatches") or [],
                        "pressIntensity": packet,
                        "reason": baseline.get("reason") or packet.get("reason"),
                    }
                else:
                    profile = {
                        "fixtureId": row.get("_fid") or row.get("fixtureId"),
                        "date": row.get("date"),
                        "opponent": row.get("opponent"),
                        "venue": row.get("venue"),
                        "opponentId": opponent_id,
                        "status": "warming" if opponent_key else "unavailable",
                        "verified": False,
                        "source": None,
                        "pressureScope": "opponent_recent_matchups",
                        "sampleTarget": target_matches,
                        "sampleMatches": [],
                        "pressIntensity": unavailable_packet(
                            "missing_opponent_team_id"
                            if not opponent_key
                            else "opponent_recent_pressure_not_yet_warmed",
                            status="warming" if opponent_key else "unavailable",
                        ),
                        "reason": (
                            "missing_opponent_team_id"
                            if not opponent_key
                            else "opponent_recent_pressure_not_yet_warmed"
                        ),
                    }
                profiles.append(
                    profile
                )
            available_opponents = sum(
                1
                for opponent in opponents.values()
                if (
                    cached_profiles.get(str(opponent["teamId"]), {})
                    .get("pressIntensity", {})
                    .get("available")
                    is True
                )
            )
            opponent_count = len(opponents)
            verified_matches = sum(
                int(
                    (cached_profiles.get(str(opponent["teamId"]), {})
                     .get("pressIntensity", {})
                     .get("sampleSize") or 0)
                )
                for opponent in opponents.values()
            )
            return {
                "status": (
                    "verified"
                    if opponent_count and available_opponents == opponent_count
                    else "partial"
                    if available_opponents
                    else "warming"
                    if opponent_count
                    else "unavailable"
                ),
                "available": bool(available_opponents),
                "sampleSize": len(profiles),
                "verifiedMatches": verified_matches,
                "opponentCount": opponent_count,
                "opponentsWithPressure": available_opponents,
                "matchupsPerOpponentTarget": target_matches,
                "sampleUnit": "opponent_recent_matchups",
                "source": (
                    "API-Football opponent fixtures + fixture statistics "
                    "+ fixture player defensive actions"
                ),
                "projectionInfluence": "explanation_only",
                "profiles": profiles,
                "limitations": [
                    (
                        f"Each opponent profile uses at least {target_matches} "
                        "recent completed team matches when provider coverage permits."
                    ),
                    "The profile is descriptive and is not used to change the deterministic projection.",
                    "Missing provider fields are unavailable, never a guessed zero.",
                ],
            }

        async def fetch_fixture_matchup_volume(fixture_list, team_id, limit=10):
            """Fetch exact team/opponent SOT and pass totals for venue samples.

            Unlike fetch_fixture_team_stats, this deliberately makes one
            team-level request per fixture and returns both sides. It avoids
            player-level enrichment because this packet is descriptive
            matchup evidence, not a new projection input.
            """
            def _num(value):
                try:
                    parsed = float(str(value).replace("%", "").strip())
                    return parsed if math.isfinite(parsed) else None
                except (TypeError, ValueError):
                    return None

            async def fetch_one(fix):
                fid = fix.get("fixtureId")
                if not fid or not team_id:
                    return None
                # v2 invalidates the original packet, which could contain
                # incomplete/empty side metrics from the first implementation.
                cache_key = f"fxt_matchup_volume_v2_{fid}"
                cached = None
                try:
                    cached_doc = await db.fixture_player_cache.find_one(
                        {"_k": cache_key}, {"_id": 0, "d": 1}
                    )
                    cached = (cached_doc or {}).get("d") or {}
                except Exception:
                    cached = None

                sides = cached.get("sides") if isinstance(cached, dict) else None

                def _side_has_volume(side):
                    return isinstance(side, dict) and any(
                        side.get(field) is not None
                        for field in ("shotsOnTarget", "passes")
                    )

                if (
                    not isinstance(sides, dict)
                    or not _side_has_volume(sides.get("home"))
                    or not _side_has_volume(sides.get("away"))
                ):
                    try:
                        stats_rows = await api_football_request(
                            "fixtures/statistics", {"fixture": fid}
                        )
                        sides = {}
                        for team_stats in stats_rows or []:
                            side_id = (team_stats.get("team") or {}).get("id")
                            if side_id is None:
                                continue
                            raw_stats = {
                                str(item.get("type") or ""): item.get("value")
                                for item in (team_stats.get("statistics") or [])
                            }
                            side = {
                                "teamId": side_id,
                                "shotsOnTarget": _num(raw_stats.get("Shots on Goal")),
                                "passes": _num(raw_stats.get("Total passes")),
                            }
                            # The normalized fixture rows carry the
                            # perspective team's venue, not always both raw
                            # team IDs. Assign the provider rows relative to
                            # that verified perspective.
                            perspective_venue = (
                                "home" if fix.get("venue") == "home" else "away"
                            )
                            side_key = (
                                perspective_venue
                                if side_id == team_id
                                else ("away" if perspective_venue == "home" else "home")
                            )
                            sides[side_key] = side
                        if not sides.get("home") or not sides.get("away"):
                            return None
                        try:
                            await db.fixture_player_cache.update_one(
                                {"_k": cache_key},
                                {"$set": {"_k": cache_key, "d": {"sides": sides}}},
                                upsert=True,
                            )
                        except Exception as cache_err:
                            print(f"[MATCHUP VOLUME CACHE] skipped: {cache_err}")
                    except Exception as volume_err:
                        print(
                            f"[MATCHUP VOLUME] fixture={fid} unavailable: "
                            f"{type(volume_err).__name__}"
                        )
                        return None

                is_home = fix.get("venue") == "home" or (
                    sides.get("home", {}).get("teamId") == team_id
                )
                team_side = sides.get("home" if is_home else "away") or {}
                opponent_side = sides.get("away" if is_home else "home") or {}
                return {
                    "fixtureId": fid,
                    "date": str(fix.get("date") or "")[:10],
                    "opponent": fix.get("opponent", ""),
                    "venue": "home" if is_home else "away",
                    "teamShotsOnTarget": team_side.get("shotsOnTarget"),
                    "opponentShotsOnTarget": opponent_side.get("shotsOnTarget"),
                    "teamPasses": team_side.get("passes"),
                    "opponentPasses": opponent_side.get("passes"),
                }

            results = await aio.gather(
                *(
                    fetch_one(fix)
                    for fix in _newest_first_rows(fixture_list, limit)
                ),
                return_exceptions=True,
            )
            return _newest_first_rows([
                row for row in results
                if isinstance(row, dict) and row.get("fixtureId")
            ])

        async def fetch_team_possession_average(
            fixture_list,
            team_id,
            limit=20,
            *,
            venue_filter=None,
            required_sample=_POSSESSION_SAMPLE_TARGET,
        ):
            """Build independent, venue-specific team possession evidence.

            A player who did not appear in a fixture must not remove that
            match from the club's sample.  Conversely, a fixture at the wrong
            venue must not pad the requested home/away sample.  The returned
            average is recency-weighted, but is only ``verified`` once the
            required number of exact fixture-statistics rows is present.
            """
            empty_status, _ = possession_sample_status(0, required=required_sample)
            if not team_id or not fixture_list:
                return {
                    "average": None,
                    "sampleSize": 0,
                    "requiredSample": required_sample,
                    "verified": False,
                    "status": empty_status,
                    "venue": venue_filter or "all",
                    "fixtureIds": [],
                    "rows": [],
                    "source": None,
                    "recencyWeighting": f"half_life_{POSSESSION_RECENCY_HALF_LIFE:g}_matches",
                }

            async def fetch_one(fix):
                if venue_filter and fix.get("venue") != venue_filter:
                    return None
                fid = fix.get("fixtureId")
                if not fid:
                    return None
                row_meta = {
                    "fixtureId": fid,
                    "date": str(fix.get("date") or "")[:10],
                    "opponent": fix.get("opponent") or "Unknown",
                    "venue": fix.get("venue") or "unknown",
                    "teamId": team_id,
                }
                cache_key = f"fxt_team_poss_{fid}_{team_id}"
                try:
                    cached = await db.fixture_player_cache.find_one(
                        {"_k": cache_key}, {"_id": 0, "d": 1}
                    )
                    cached_data = (cached or {}).get("d") or {}
                    cached_value = cached_data.get("teamPossession")
                    if isinstance(cached_value, (int, float)):
                        return {
                            **row_meta,
                            "value": float(cached_value),
                            "source": "fixture_statistics_cache",
                        }
                except Exception:
                    pass

                # The player-history path stores the exact two-sided fixture
                # possession packet under fxt_poss_{fixture}. Reuse that
                # verified fixture statistic before spending another provider
                # request on the team-level key. The schedule row already
                # carries the club's verified home/away orientation.
                try:
                    fixture_poss = await db.fixture_player_cache.find_one(
                        {"_k": f"fxt_poss_{fid}"}, {"_id": 0, "d": 1}
                    )
                    fixture_poss_data = (fixture_poss or {}).get("d") or {}
                    home_value = fixture_poss_data.get("home_poss")
                    away_value = fixture_poss_data.get("away_poss")
                    if (
                        isinstance(home_value, (int, float))
                        and isinstance(away_value, (int, float))
                    ):
                        value = (
                            float(home_value)
                            if fix.get("venue") == "home"
                            else float(away_value)
                        )
                        try:
                            await db.fixture_player_cache.update_one(
                                {"_k": cache_key},
                                {"$set": {
                                    "_k": cache_key,
                                    "d": {
                                        "teamId": team_id,
                                        "teamPossession": value,
                                        "fixtureId": fid,
                                    },
                                }},
                                upsert=True,
                            )
                        except Exception:
                            pass
                        return {
                            **row_meta,
                            "value": value,
                            "source": "fixture_statistics_cache",
                        }
                except Exception:
                    pass

                try:
                    stats_rows = await api_football_request(
                        "fixtures/statistics", {"fixture": fid}
                    )
                    value = None
                    for team_stats in stats_rows or []:
                        if (team_stats.get("team") or {}).get("id") != team_id:
                            continue
                        for stat in team_stats.get("statistics") or []:
                            if stat.get("type") != "Ball Possession":
                                continue
                            raw = str(stat.get("value") or "").replace("%", "").strip()
                            try:
                                value = float(raw)
                            except (TypeError, ValueError):
                                value = None
                            break
                        break
                    if value is None:
                        return None
                    try:
                        await db.fixture_player_cache.update_one(
                            {"_k": cache_key},
                            {"$set": {
                                "_k": cache_key,
                                "d": {
                                    "teamId": team_id,
                                    "teamPossession": value,
                                    "fixtureId": fid,
                                },
                            }},
                            upsert=True,
                        )
                    except Exception as cache_err:
                        print(f"[TEAM POSS CACHE] skipped: {cache_err}")
                    return {
                        **row_meta,
                        "value": value,
                        "source": "fixture_statistics",
                    }
                except Exception:
                    return None

            sem = aio.Semaphore(6)

            async def bounded(fix):
                async with sem:
                    return await fetch_one(fix)

            results = await aio.gather(
                *(
                    bounded(fix)
                    for fix in _newest_first_rows(fixture_list, limit)
                ),
                return_exceptions=True,
            )
            valid = [
                row for row in results
                if isinstance(row, dict) and isinstance(row.get("value"), (int, float))
            ]
            # The candidate window may include rows whose fixture statistics
            # are unavailable. Keep searching that window for valid evidence,
            # then use only the newest verified sample requested by the
            # contract. Older verified rows must not displace more recent ones
            # just because they were easier to load from cache.
            valid = _newest_first_rows(valid)
            valid = valid[:required_sample]
            status, verified = possession_sample_status(
                len(valid),
                required=required_sample,
            )
            unweighted_average = (
                round(sum(row["value"] for row in valid) / len(valid), 1)
                if valid
                else None
            )
            return {
                "average": recency_weighted_average(valid),
                "unweightedAverage": unweighted_average,
                "sampleSize": len(valid),
                "requiredSample": required_sample,
                "verified": verified,
                "status": status,
                "venue": venue_filter or "all",
                "fixtureIds": [row["fixtureId"] for row in valid],
                "rows": valid,
                "source": "fixture_statistics_team_schedule" if valid else None,
                "recencyWeighting": f"half_life_{POSSESSION_RECENCY_HALF_LIFE:g}_matches",
            }

        # 2. Player game-by-game box scores from recent fixtures
        async def fetch_player_game_logs(
            fixture_list,
            player_id,
            limit=100,
            extra_fixture_list=None,
        ):
            """Fetch player's individual stats — always live from API, all competitions."""

            def _build_game_log(stats: dict) -> dict:
                minutes = stats.get("games", {}).get("minutes") or 0
                rating = stats.get("games", {}).get("rating")
                gl = {
                    "minutes": minutes,
                    "rating": float(rating) if rating else None,
                    "passes_total": stats.get("passes", {}).get("total"),
                    "passes_key": stats.get("passes", {}).get("key"),
                    "passes_accuracy": stats.get("passes", {}).get("accuracy"),
                    "shots_total": stats.get("shots", {}).get("total"),
                    "shots_on": stats.get("shots", {}).get("on"),
                    "tackles_total": stats.get("tackles", {}).get("total"),
                    "tackles_interceptions": stats.get("tackles", {}).get("interceptions"),
                    "tackles_blocks": stats.get("tackles", {}).get("blocks"),
                    "dribbles_attempts": stats.get("dribbles", {}).get("attempts"),
                    "dribbles_success": stats.get("dribbles", {}).get("success"),
                    "fouls_drawn": stats.get("fouls", {}).get("drawn"),
                    "fouls_committed": stats.get("fouls", {}).get("committed"),
                    "duels_total": stats.get("duels", {}).get("total"),
                    "duels_won": stats.get("duels", {}).get("won"),
                    "goals_saves": stats.get("goals", {}).get("saves"),
                    "goals_total": stats.get("goals", {}).get("total"),
                    "goals_assists": stats.get("goals", {}).get("assists"),
                    "passes_crosses": stats.get("passes", {}).get("cross"),
                    "tackles_clearances": stats.get("tackles", {}).get("clearances"),
                    "cards_yellow": stats.get("cards", {}).get("yellow"),
                    # Fields needed for PrizePicks soccer fantasy scoring
                    "goals_conceded": stats.get("goals", {}).get("conceded"),
                    "penalty_saved": stats.get("penalty", {}).get("saved"),
                    "penalty_missed": stats.get("penalty", {}).get("missed"),
                    "offsides": stats.get("offsides"),
                    "cards_red": stats.get("cards", {}).get("red"),
                    "providerPosition": stats.get("games", {}).get("position"),
                }
                return gl

            stat_field_map = {
                "goals": "goals_total", "assists": "goals_assists",
                "shots_assisted": "passes_key",
                "pass_attempts": "passes_total", "passes": "passes_total",
                "shots": "shots_total", "shots_on_target": "shots_on",
                "tackles": "tackles_total", "key_passes": "passes_key",
                "saves": "goals_saves", "goalie_saves": "goals_saves",
                "interceptions": "tackles_interceptions",
                "blocks": "tackles_blocks", "dribbles": "dribbles_attempts",
                "fouls_drawn": "fouls_drawn", "fouls_committed": "fouls_committed",
                "crosses": "passes_crosses", "clearances": "tackles_clearances",
                "duels_won": "duels_won", "yellow_cards": "cards_yellow",
            }
            # Venue history is only trustworthy as a primary sample when it
            # has real player appearances, not merely team fixtures. The
            # provider's recent team feed can contain fewer than 30 selected
            # venue appearances even when it returns 100 fixtures, so the
            # loader extends through older seasons before falling back.
            _VENUE_HISTORY_MAX_OLDER_SEASONS = 7

            def _venue_history_count(logs):
                if req.sport != "soccer" or not player_venue:
                    return 0
                return sum(
                    1
                    for log in logs
                    if log.get("venue") == player_venue
                    and log.get(stat_field_map.get(req.propType, ""))
                    is not None
                )

            async def _fetch_fixture_possession(fid, home_id, away_id):
                """Return both exact fetched fixture possession values.

                Possession is optional historical context. Never infer the
                opponent share from 100 minus one side because provider
                possession can be rounded independently or be unavailable for
                one team. The caller may still keep a valid player appearance
                when this returns a partial/unavailable pair.
                """
                cache_key = f"fxt_poss_{fid}"
                try:
                    cached = await db.fixture_player_cache.find_one(
                        {"_k": cache_key}, {"_id": 0, "d": 1}
                    )
                    cached_data = (cached or {}).get("d") or {}
                    if cached_data.get("home_poss") is not None and cached_data.get("away_poss") is not None:
                        return float(cached_data["home_poss"]), float(cached_data["away_poss"])
                except Exception:
                    pass
                try:
                    stats_rows = await api_football_request(
                        "fixtures/statistics", {"fixture": fid}
                    )
                    home_poss = away_poss = None
                    for team_stats in stats_rows or []:
                        team_id = (team_stats.get("team") or {}).get("id")
                        for stat in team_stats.get("statistics") or []:
                            if stat.get("type") != "Ball Possession":
                                continue
                            raw = str(stat.get("value") or "").replace("%", "").strip()
                            try:
                                value = float(raw)
                            except (TypeError, ValueError):
                                continue
                            if team_id == home_id:
                                home_poss = value
                            elif team_id == away_id:
                                away_poss = value
                    if home_poss is None or away_poss is None:
                        return None, None
                    try:
                        await db.fixture_player_cache.update_one(
                            {"_k": cache_key},
                            {"$set": {"_k": cache_key, "d": {
                                "home_poss": home_poss,
                                "away_poss": away_poss,
                            }}},
                            upsert=True,
                        )
                    except Exception as cache_err:
                        print(f"[PLAYER TP CACHE] skipped: {cache_err}")
                    return home_poss, away_poss
                except Exception as poss_err:
                    print(f"[PLAYER TP] fixture={fid} unavailable: {type(poss_err).__name__}")
                    return None, None

            async def _fetch_fixture_opponent_sot(
                fid,
                home_id,
                away_id,
                player_team_id,
            ):
                """Return exact-fixture opponent shots on target for a player log.

                This is intentionally separate from the player's save total:
                the opponent's team SOT is the match context that generated the
                save opportunity. Missing provider data remains unavailable.
                """
                cache_key = f"fxt_sot_{fid}"
                try:
                    cached = await db.fixture_player_cache.find_one(
                        {"_k": cache_key}, {"_id": 0, "d": 1}
                    )
                    cached_data = (cached or {}).get("d") or {}
                    if (
                        cached_data.get("home_sot") is not None
                        and cached_data.get("away_sot") is not None
                    ):
                        opponent_id = (
                            away_id if player_team_id == home_id else home_id
                        )
                        return (
                            float(cached_data["away_sot"])
                            if opponent_id == away_id
                            else float(cached_data["home_sot"])
                        )
                except Exception:
                    pass
                try:
                    stats_rows = await api_football_request(
                        "fixtures/statistics", {"fixture": fid}
                    )
                    home_sot = away_sot = None
                    for team_stats in stats_rows or []:
                        team_id = (team_stats.get("team") or {}).get("id")
                        for stat in team_stats.get("statistics") or []:
                            if stat.get("type") != "Shots on Goal":
                                continue
                            raw = stat.get("value")
                            try:
                                value = float(str(raw).replace("%", "").strip())
                            except (TypeError, ValueError):
                                continue
                            if team_id == home_id:
                                home_sot = value
                            elif team_id == away_id:
                                away_sot = value
                    if home_sot is None or away_sot is None:
                        return None
                    try:
                        await db.fixture_player_cache.update_one(
                            {"_k": cache_key},
                            {"$set": {
                                "_k": cache_key,
                                "d": {
                                    "home_sot": home_sot,
                                    "away_sot": away_sot,
                                },
                            }},
                            upsert=True,
                        )
                    except Exception as cache_err:
                        print(f"[PLAYER OPP SOT CACHE] skipped: {cache_err}")
                    opponent_id = away_id if player_team_id == home_id else home_id
                    return away_sot if opponent_id == away_id else home_sot
                except Exception as sot_err:
                    print(
                        f"[PLAYER OPP SOT] fixture={fid} unavailable: "
                        f"{type(sot_err).__name__}"
                    )
                    return None

            async def _fetch_fixture_opponent_context(
                fid,
                home_id,
                away_id,
                player_team_id,
            ):
                """Return exact-fixture opponent SOT and total pass attempts.

                The same context belongs beside every soccer player log, not
                only goalkeeper saves. It is fetched from the two-team
                fixture-statistics response and cached as a fixture-level
                artifact.
                """
                cache_key = f"fxt_opponent_context_{fid}"
                try:
                    cached = await db.fixture_player_cache.find_one(
                        {"_k": cache_key}, {"_id": 0, "d": 1}
                    )
                    cached_data = (cached or {}).get("d") or {}
                    if cached_data.get("home") and cached_data.get("away"):
                        team_side = (
                            cached_data["home"]
                            if player_team_id == home_id
                            else cached_data["away"]
                        )
                        opponent_id = away_id if player_team_id == home_id else home_id
                        opponent_side = (
                            cached_data["away"] if opponent_id == away_id
                            else cached_data["home"]
                        )
                        return {
                            "teamShotsOnTarget": team_side.get("shotsOnTarget"),
                            "teamPasses": team_side.get("passes"),
                            "shotsOnTarget": opponent_side.get("shotsOnTarget"),
                            "passes": opponent_side.get("passes"),
                        }
                except Exception:
                    pass
                try:
                    stats_rows = await api_football_request(
                        "fixtures/statistics", {"fixture": fid}
                    )
                    sides = {}
                    for team_stats in stats_rows or []:
                        team_id = (team_stats.get("team") or {}).get("id")
                        if team_id not in {home_id, away_id}:
                            continue
                        raw_stats = {
                            str(item.get("type") or ""): item.get("value")
                            for item in (team_stats.get("statistics") or [])
                        }

                        def _safe_stat(value):
                            try:
                                parsed = float(str(value).replace("%", "").strip())
                                return parsed if math.isfinite(parsed) else None
                            except (TypeError, ValueError):
                                return None

                        sides["home" if team_id == home_id else "away"] = {
                            "shotsOnTarget": _safe_stat(raw_stats.get("Shots on Goal")),
                            "passes": _safe_stat(raw_stats.get("Total passes")),
                        }
                    if not sides.get("home") or not sides.get("away"):
                        return {}
                    try:
                        await db.fixture_player_cache.update_one(
                            {"_k": cache_key},
                            {"$set": {"_k": cache_key, "d": sides}},
                            upsert=True,
                        )
                    except Exception as cache_err:
                        print(f"[PLAYER OPP CONTEXT CACHE] skipped: {cache_err}")
                    team_side = sides["home"] if player_team_id == home_id else sides["away"]
                    opponent_id = away_id if player_team_id == home_id else home_id
                    opponent_side = sides["away"] if opponent_id == away_id else sides["home"]
                    return {
                        "teamShotsOnTarget": team_side.get("shotsOnTarget"),
                        "teamPasses": team_side.get("passes"),
                        "shotsOnTarget": opponent_side.get("shotsOnTarget"),
                        "passes": opponent_side.get("passes"),
                    }
                except Exception as context_err:
                    print(
                        f"[PLAYER OPP CONTEXT] fixture={fid} unavailable: "
                        f"{type(context_err).__name__}"
                    )
                    return {}

            collected = []
            if not player_id or not actual_team_id:
                return collected

            # ── STAGE 0: Read per-game stats directly from MongoDB cache ──────────
            # This fires first and avoids ANY API call. Key pattern: fxp_{fid}_{player_id}
            try:
                cached_games = await db.fixture_player_cache.find(
                    {"_k": {"$regex": f"_{player_id}$"}}
                ).sort("_k", -1).limit(60).to_list(60)
                if cached_games:
                    print(f"[CACHE-STAGE0] {req.playerName}: {len(cached_games)} cached game logs from MongoDB")
                    target_field = stat_field_map.get(req.propType, "")

                    # Extract fixture IDs from keys (fxp_{fid}_{player_id})
                    fid_map: dict = {}  # fid_str -> entry
                    for entry in cached_games:
                        key = entry.get("_k", "")
                        parts = key.split("_")
                        # key format: fxp_{fid}_{pid} — parts[0]="fxp", parts[1]=fid, parts[2]=pid
                        if len(parts) >= 3:
                            fid_map[parts[1]] = entry

                    # Track which fixture IDs come from Stage 0 so Stage 1 can dedup
                    _stage0_fids: set = set(fid_map.keys())

                    # Batch-fetch fixture metadata (home/away team IDs) stored by prefetch
                    fxm_docs: dict = {}
                    if fid_map:
                        meta_keys = [f"fxm_{fid}" for fid in fid_map]
                        meta_results = await db.fixture_player_cache.find(
                            {"_k": {"$in": meta_keys}}, {"_id": 0}
                        ).to_list(len(meta_keys))
                        for meta in meta_results:
                            fid_str = meta.get("_k", "")[4:]  # strip "fxm_"
                            fxm_docs[fid_str] = meta.get("d", {})

                    # Player-stat cache rows can outlive their fxm companion.
                    # Rejoin the durable team fixture schedule before exposing
                    # the history so cached appearances retain the verified
                    # date, opponent, venue, score, and competition context.
                    # This is a read-only recovery path; it never invents
                    # metadata when the fixture cannot be found.
                    if fid_map:
                        try:
                            _history_meta_doc = await db.team_fixture_history.find_one(
                                {"teamId": actual_team_id},
                                {"_id": 0, "fixtures": 1},
                            )
                            for _history_fixture in (
                                (_history_meta_doc or {}).get("fixtures") or []
                            ):
                                _fixture_info = _history_fixture.get("fixture") or {}
                                _fixture_teams = _history_fixture.get("teams") or {}
                                _fixture_home = _fixture_teams.get("home") or {}
                                _fixture_away = _fixture_teams.get("away") or {}
                                _history_fid = (
                                    _fixture_info.get("id")
                                    or _history_fixture.get("fixtureId")
                                )
                                if not _history_fid:
                                    continue
                                _history_fid = str(_history_fid)
                                if _history_fid not in fid_map:
                                    continue
                                if not _fixture_home.get("id") or not _fixture_away.get("id"):
                                    continue
                                _fixture_date = (
                                    _fixture_info.get("date")
                                    or _history_fixture.get("date")
                                    or ""
                                )
                                _fixture_league = _history_fixture.get("league") or {}
                                _fixture_goals = _history_fixture.get("goals") or {}
                                _history_meta = {
                                    "home_id": _fixture_home.get("id"),
                                    "away_id": _fixture_away.get("id"),
                                    "home_name": _fixture_home.get("name") or "",
                                    "away_name": _fixture_away.get("name") or "",
                                    "date": str(_fixture_date)[:10],
                                    "score": (
                                        f"{_fixture_goals.get('home')}-"
                                        f"{_fixture_goals.get('away')}"
                                        if _fixture_goals.get("home") is not None
                                        and _fixture_goals.get("away") is not None
                                        else ""
                                    ),
                                    "league_id": _fixture_league.get("id"),
                                    "league_name": _fixture_league.get("name") or "",
                                    "round": _fixture_league.get("round") or "",
                                    "metadataSource": "team_fixture_history",
                                }
                                _merged_meta = dict(fxm_docs.get(_history_fid) or {})
                                for _meta_key, _meta_value in _history_meta.items():
                                    if (
                                        _meta_value not in (None, "")
                                        and not _merged_meta.get(_meta_key)
                                    ):
                                        _merged_meta[_meta_key] = _meta_value
                                fxm_docs[_history_fid] = _merged_meta
                        except Exception as _history_meta_err:
                            print(
                                f"[CACHE-STAGE0] fixture metadata recovery skipped: "
                                f"{type(_history_meta_err).__name__}"
                            )

                    # Older stat rows can refer to fixtures outside the current
                    # team schedule window. Recover only the missing fixture
                    # metadata by exact fixture ID, with a hard bounded fan-out.
                    # Successful responses are persisted as permanent fxm_ rows
                    # so quota exhaustion on a later request cannot erase the
                    # verified date/venue/opponent context again.
                    def _fixture_meta_complete(_meta: dict) -> bool:
                        return bool(
                            _meta.get("home_id") is not None
                            and _meta.get("away_id") is not None
                            and _meta.get("date")
                        )

                    def _cached_row_meta_complete(_entry: dict) -> bool:
                        _cached_data = _entry.get("d") or {}
                        return bool(
                            _cached_data.get("date")
                            and _cached_data.get("venue") in {"home", "away"}
                            and _cached_data.get("opponent")
                        )

                    _missing_fixture_meta_ids = [
                        _fid
                        for _fid in fid_map
                        if not _fixture_meta_complete(fxm_docs.get(_fid) or {})
                        and not _cached_row_meta_complete(fid_map[_fid])
                    ][:30]
                    if _missing_fixture_meta_ids:
                        async def _recover_fixture_meta(_fid: str):
                            try:
                                _payload = await aio.wait_for(
                                    api_football_request(
                                        "fixtures",
                                        {"id": int(_fid)},
                                    ),
                                    timeout=2.5,
                                )
                                for _fixture_row in _api_response_list(_payload):
                                    _fixture_info = _fixture_row.get("fixture") or {}
                                    if str(_fixture_info.get("id")) != str(_fid):
                                        continue
                                    _fixture_teams = _fixture_row.get("teams") or {}
                                    _fixture_home = _fixture_teams.get("home") or {}
                                    _fixture_away = _fixture_teams.get("away") or {}
                                    if not _fixture_home.get("id") or not _fixture_away.get("id"):
                                        continue
                                    _fixture_league = _fixture_row.get("league") or {}
                                    _fixture_goals = _fixture_row.get("goals") or {}
                                    return str(_fid), {
                                        "home_id": _fixture_home.get("id"),
                                        "away_id": _fixture_away.get("id"),
                                        "home_name": _fixture_home.get("name") or "",
                                        "away_name": _fixture_away.get("name") or "",
                                        "date": str(_fixture_info.get("date") or "")[:10],
                                        "score": (
                                            f"{_fixture_goals.get('home')}-"
                                            f"{_fixture_goals.get('away')}"
                                            if _fixture_goals.get("home") is not None
                                            and _fixture_goals.get("away") is not None
                                            else ""
                                        ),
                                        "league_id": _fixture_league.get("id"),
                                        "league_name": _fixture_league.get("name") or "",
                                        "round": _fixture_league.get("round") or "",
                                        "metadataSource": "provider_fixture_metadata",
                                    }
                            except Exception:
                                return None
                            return None

                        try:
                            _recovered_meta = await aio.wait_for(
                                aio.gather(*[
                                    _recover_fixture_meta(_fid)
                                    for _fid in _missing_fixture_meta_ids
                                ], return_exceptions=True),
                                timeout=6.0,
                            )
                        except Exception as _provider_meta_err:
                            print(
                                f"[CACHE-STAGE0] exact fixture metadata recovery "
                                f"bounded: {type(_provider_meta_err).__name__}"
                            )
                            _recovered_meta = []

                        _metadata_write_tasks = []
                        for _recovered in _recovered_meta:
                            if (
                                not isinstance(_recovered, tuple)
                                or len(_recovered) != 2
                                or not isinstance(_recovered[1], dict)
                            ):
                                continue
                            _recovered_fid, _recovered_doc = _recovered
                            _merged_meta = dict(fxm_docs.get(_recovered_fid) or {})
                            for _meta_key, _meta_value in _recovered_doc.items():
                                if _meta_value not in (None, ""):
                                    _merged_meta[_meta_key] = _meta_value
                            fxm_docs[_recovered_fid] = _merged_meta
                            _metadata_write_tasks.append(
                                db.fixture_player_cache.update_one(
                                    {"_k": f"fxm_{_recovered_fid}"},
                                    {
                                        "$set": {
                                            "_k": f"fxm_{_recovered_fid}",
                                            "d": _merged_meta,
                                        }
                                    },
                                    upsert=True,
                                )
                            )
                        if _metadata_write_tasks:
                            await aio.gather(
                                *_metadata_write_tasks,
                                return_exceptions=True,
                            )

                    # Join verified fixture possession onto every cached soccer
                    # game log so the compact history bars can label all props,
                    # not just pass-volume props. A missing optional possession
                    # feed must not discard a valid minutes/stat appearance.
                    poss_docs: dict[str, dict] = {}
                    if fid_map and req.sport == "soccer":
                        poss_keys = [f"fxt_poss_{fid}" for fid in fid_map]
                        poss_results = await db.fixture_player_cache.find(
                            {"_k": {"$in": poss_keys}}, {"_id": 0, "d": 1}
                        ).to_list(len(poss_keys))
                        for poss_doc in poss_results:
                            fid_str = poss_doc.get("_k", "")[9:]  # strip "fxt_poss_"
                            poss_docs[fid_str] = poss_doc.get("d") or {}
                        # A cache miss or partial possession cache is worth
                        # rehydrating, but failure to hydrate remains an
                        # unavailable optional signal rather than a reason to
                        # discard the player appearance.
                        _tp_tasks = []
                        _tp_fids = []
                        for _fid, _meta in fxm_docs.items():
                            _poss = poss_docs.get(_fid) or {}
                            if (
                                _meta.get("home_id") is not None
                                and _meta.get("away_id") is not None
                                and (
                                    _poss.get("home_poss") is None
                                    or _poss.get("away_poss") is None
                                )
                            ):
                                _tp_fids.append(_fid)
                                _tp_tasks.append(_fetch_fixture_possession(
                                    _fid, _meta["home_id"], _meta["away_id"]
                                ))
                        if _tp_tasks:
                            # Historical possession is required before a cached
                            # soccer row can be used, but hydrating every cached
                            # fixture here made the critical path wait on a
                            # provider-wide burst. Prefer the newest bounded
                            # sample and let Stage 1/direct history fill any
                            # remaining gaps.
                            for _unused_tp_task in _tp_tasks[16:]:
                                try:
                                    _unused_tp_task.close()
                                except AttributeError:
                                    pass
                            _tp_tasks = _tp_tasks[:16]
                            _tp_fids = _tp_fids[:16]
                            try:
                                _tp_results = await aio.wait_for(
                                    aio.gather(*_tp_tasks, return_exceptions=True),
                                    # Possession is optional context. Never
                                    # consume the entire player-history
                                    # response window trying to hydrate it.
                                    timeout=1.5,
                                )
                            except Exception as _tp_err:
                                print(
                                    f"[CACHE-STAGE0] possession hydration bounded: "
                                    f"{type(_tp_err).__name__}"
                                )
                                _tp_results = []
                            for _fid, _result in zip(_tp_fids, _tp_results):
                                if (
                                    isinstance(_result, tuple)
                                    and len(_result) == 2
                                    and _result[0] is not None
                                    and _result[1] is not None
                                ):
                                    poss_docs[_fid] = {
                                        "home_poss": _result[0],
                                        "away_poss": _result[1],
                                    }

                    # Every soccer player row gets the opponent's exact
                    # fixture SOT and total passes. Goalkeeper saves need SOT
                    # faced, but the same observed opponent volume is also
                    # required for SOT and pass-prop context.
                    context_docs: dict[str, dict] = {}
                    if req.sport == "soccer":
                        _context_tasks = []
                        _context_fids = []
                        for _fid, _meta in fxm_docs.items():
                            if (
                                _meta.get("home_id") is not None
                                and _meta.get("away_id") is not None
                            ):
                                _context_fids.append(_fid)
                                _context_tasks.append(
                                    _fetch_fixture_opponent_context(
                                        _fid,
                                        _meta["home_id"],
                                        _meta["away_id"],
                                        actual_team_id,
                                    )
                                )
                        if _context_tasks:
                            # Keep the optional enrichment bounded without
                            # creating coroutine objects that are then discarded
                            # by the 16-fixture cap. Discarded coroutine objects
                            # emit "was never awaited" warnings and make it
                            # impossible to tell whether the useful rows survived.
                            for _unused_context_task in _context_tasks[16:]:
                                try:
                                    _unused_context_task.close()
                                except AttributeError:
                                    pass
                            _context_tasks = _context_tasks[:16]
                            _context_fids = _context_fids[:16]
                            try:
                                _context_results = await aio.wait_for(
                                    aio.gather(*_context_tasks, return_exceptions=True),
                                    # Opponent context enriches the card but
                                    # is not required to return real player
                                    # appearances from Stage 0.
                                    timeout=1.5,
                                )
                            except Exception as _context_err:
                                print(
                                    f"[CACHE-STAGE0] opponent-context hydration bounded: "
                                    f"{type(_context_err).__name__}"
                                )
                                _context_results = []
                            for _fid, _result in zip(_context_fids, _context_results):
                                if isinstance(_result, dict):
                                    context_docs[_fid] = _result

                    for fid_str, entry in fid_map.items():
                        d = entry.get("d", {})
                        if not d:
                            continue
                        minutes = d.get("minutes") or 0
                        if not minutes:
                            continue
                        gl = dict(d)
                        gl["historySource"] = "fixture_player_cache"
                        gl["fixtureId"] = str(fid_str)
                        gl["_fid"] = str(fid_str)
                        # Older exact-fixture fetches persisted their verified
                        # context directly on the player row. Preserve that
                        # context even when the companion fxm document was
                        # missing; this is the durable last cache tier before
                        # an exact provider lookup.
                        gl["date"] = str(d.get("date") or "")[:10]
                        gl["score"] = d.get("score") or ""
                        gl["league"] = d.get("league") or ""
                        gl["leagueId"] = d.get("leagueId")
                        gl["competitionId"] = d.get("competitionId") or gl["leagueId"]
                        gl["competitionName"] = d.get("competitionName") or gl["league"]
                        gl["round"] = d.get("round") or ""
                        if (
                            gl.get("date")
                            and gl.get("venue") in {"home", "away"}
                            and gl.get("opponent")
                        ):
                            gl["fixtureContextStatus"] = "verified"
                            gl["fixtureContextSource"] = "fixture_player_cache_row"

                        # Populate venue and opponent from fixture metadata if available
                        meta = fxm_docs.get(fid_str, {})
                        if meta:
                            home_id_meta = meta.get("home_id")
                            away_id_meta = meta.get("away_id")
                            # Club filter: if BOTH team IDs are known and NEITHER matches
                            # the player's current team, this is a fixture from a previous
                            # club — drop it so stale old-club stats don't corrupt the prior.
                            if (home_id_meta is not None and away_id_meta is not None
                                    and home_id_meta != actual_team_id
                                    and away_id_meta != actual_team_id):
                                print(f"[STAGE0 CLUB FILTER] fid={fid_str} "
                                      f"home={home_id_meta} away={away_id_meta} "
                                      f"≠ current team {actual_team_id} — dropped (old-club fixture)")
                                continue
                            if home_id_meta == actual_team_id:
                                is_home = True
                            elif away_id_meta == actual_team_id:
                                is_home = False
                            else:
                                is_home = gl.get("venue") == "home"
                            gl["fixtureTeamId"] = actual_team_id
                            gl["fixtureOpponentId"] = (
                                away_id_meta if is_home else home_id_meta
                            )
                            gl["homeTeamId"] = home_id_meta
                            gl["awayTeamId"] = away_id_meta
                            gl["venue"] = (
                                "home"
                                if is_home
                                else "away"
                                if away_id_meta == actual_team_id
                                else gl.get("venue", "")
                            )
                            gl["opponent"] = (
                                (
                                    meta.get("away_name", "")
                                    if is_home
                                    else meta.get("home_name", "")
                                )
                                or gl.get("opponent", "")
                            )
                            gl["date"] = str(
                                meta.get("date")
                                or meta.get("fixture_date")
                                or gl.get("date")
                                or "",
                            )[:10]
                            gl["score"] = meta.get("score") or gl.get("score") or ""
                            gl["leagueId"] = (
                                meta.get("league_id")
                                or meta.get("competition_id")
                                or gl.get("leagueId")
                            )
                            gl["competitionId"] = gl["leagueId"]
                            gl["competitionName"] = (
                                meta.get("league_name")
                                or meta.get("competition_name")
                                or gl.get("competitionName")
                            )
                            gl["round"] = (
                                meta.get("round")
                                or meta.get("stage")
                                or gl.get("round")
                                or ""
                            )
                            gl["fixtureContextStatus"] = "verified"
                            gl["fixtureContextSource"] = (
                                meta.get("metadataSource")
                                or "fixture_metadata_cache"
                            )
                            if req.sport == "soccer":
                                poss = poss_docs.get(fid_str, {})
                                try:
                                    home_poss = float(str(poss.get("home_poss")).replace("%", "").strip())
                                except (TypeError, ValueError):
                                    home_poss = None
                                try:
                                    away_poss = float(str(poss.get("away_poss")).replace("%", "").strip())
                                except (TypeError, ValueError):
                                    away_poss = None
                                if is_home and home_poss is not None:
                                    gl["teamPossession"] = home_poss
                                    gl["opponentPossession"] = away_poss
                                elif not is_home and away_poss is not None:
                                    gl["teamPossession"] = away_poss
                                    gl["opponentPossession"] = home_poss
                                if (
                                    gl.get("teamPossession") is not None
                                    and gl.get("opponentPossession") is not None
                                ):
                                    gl["tp"] = gl["teamPossession"]
                                    gl["possessionStatus"] = "verified"
                                    gl["possessionSource"] = "fixture_statistics"
                                else:
                                    # Preserve the stat-bearing appearance and
                                    # make the missing optional evidence
                                    # explicit. Never retain a stale/partial
                                    # tp value or synthesize 100 - one side.
                                    gl["teamPossession"] = None
                                    gl["opponentPossession"] = None
                                    gl.pop("tp", None)
                                    gl["possessionStatus"] = "unavailable"
                                    gl["possessionSource"] = None
                                if fid_str in context_docs:
                                    _context = context_docs[fid_str]
                                    if _context.get("teamShotsOnTarget") is not None:
                                        gl["teamShotsOnTarget"] = _context["teamShotsOnTarget"]
                                    if _context.get("teamPasses") is not None:
                                        gl["teamPassAttempts"] = _context["teamPasses"]
                                    if _context.get("shotsOnTarget") is not None:
                                        gl["opponentShotsOnTarget"] = _context["shotsOnTarget"]
                                    if _context.get("passes") is not None:
                                        gl["opponentPassAttempts"] = _context["passes"]
                        else:
                            if req.sport == "soccer":
                                # The player-stat cache can outlive its
                                # companion fixture metadata, especially when
                                # the provider quota is exhausted. Keep the
                                # real appearance, but make the missing
                                # fixture context explicit instead of
                                # guessing its venue/opponent or erasing the
                                # player's usable history.
                                if gl.get("venue") not in {"home", "away"}:
                                    gl["venue"] = ""
                                if not gl.get("opponent"):
                                    gl["opponent"] = ""
                                if not (
                                    gl.get("date")
                                    and gl.get("venue") in {"home", "away"}
                                    and gl.get("opponent")
                                ):
                                    gl["fixtureContextStatus"] = "unavailable"
                                    gl["fixtureContextSource"] = None
                                gl["historySource"] = "fixture_player_cache"
                            else:
                                gl["venue"] = ""
                                gl["opponent"] = ""

                        raw_val = gl.get(target_field) if target_field else None
                        if raw_val is not None and minutes > 0:
                            gl["targetStatPer90"] = round((raw_val / minutes) * 90, 2)
                        # Mark with fixture ID so Stage 1 can dedup
                        gl["_fid"] = fid_str
                        collected.append(gl)

                    # Only short-circuit if we have enough games with venue data.
                    # Minimum 15 games required — a proper Bayesian prior needs enough
                    # samples to split home/away and compute stable rolling averages.
                    # Below 15 we always fall through to Stage 1 so the live API fetches
                    # all 40 team fixtures and fills the gaps (Stage 1 still uses cache
                    # hits for any fixture already stored, so no wasted API calls).
                    good = [g for g in collected if g.get("venue")]
                    # For saves prop: also require that at least SOME cached logs actually
                    # have goals_saves data. The prefetch cache often stores a game log
                    # entry with goals_saves=None (the stat was null at cache time).
                    # If Stage 0 returns early with 17 logs all having goals_saves=None,
                    # the Bayesian engine gets an empty series, falls back to _empty_metrics
                    # (posteriorMean=line, P=50/50), and the coin-flip guard pins the
                    # result to UNDER — exactly the Oblak bug.
                    _saves_ok = True
                    if req.propType in {"saves", "goalie_saves"}:
                        target_f = stat_field_map.get(req.propType, "")
                        _saves_ok = any(g.get(target_f) is not None for g in collected)
                        if not _saves_ok:
                            print(f"[CACHE-STAGE0] {req.playerName}/saves: 0 of {len(collected)} cached logs have goals_saves — falling through to Stage 1")
                    _pressure_possession_count = sum(
                        1 for g in collected if g.get("teamPossession") is not None
                    )
                    # Competition/stage evidence needs permanent fixture
                    # metadata. Older prefetched rows may have player stats
                    # but only IDs/venue, so let Stage 1 rejoin them to the
                    # team's verified fixture schedule before short-circuiting.
                    _competition_meta_complete = (
                        req.sport != "soccer"
                        or all(
                            g.get("leagueId") is not None
                            or g.get("competitionName")
                            for g in collected
                        )
                    )
                    # Historical possession is optional context, not a
                    # prerequisite for the player's stat prior. Requiring 12
                    # possession rows here made an otherwise complete cache
                    # fall through to 40 fixture/player API calls and then
                    # time out, replacing real logs with synthetic season
                    # averages. Passing safeguards already keep absent
                    # possession neutral and expose its provenance.
                    _home_count = sum(
                        1 for _g in collected
                        if _g.get("venue") == "home"
                        and _g.get(target_field) is not None
                    )
                    _away_count = sum(
                        1 for _g in collected
                        if _g.get("venue") == "away"
                        and _g.get(target_field) is not None
                    )
                    _selected_venue_count = _venue_history_count(collected)
                    _venue_history_complete = (
                        req.sport != "soccer"
                        or not player_venue
                        or _selected_venue_count >= _VENUE_HISTORY_TARGET
                    )
                    if (
                        len(collected) >= _PREDICTION_CACHE_MIN
                        and len(good) >= len(collected) // 2
                        and _saves_ok
                        and _competition_meta_complete
                        and not extra_fixture_list
                    ):
                        _poss_note = (
                            f"; historical possession={_pressure_possession_count}"
                            if req.sport == "soccer" else ""
                        )
                        _coverage_note = (
                            " with explicit full-history fallback"
                            if not _venue_history_complete or len(collected) < _RECENT_ARCHIVE_MIN
                            else ""
                        )
                        print(
                            f"[CACHE-STAGE0] Returning {len(collected)} real (cached) "
                            f"game logs — skipping API{_poss_note}{_coverage_note}"
                        )
                        return _newest_first_rows(collected)
                    elif collected:
                        print(
                            f"[CACHE-STAGE0] Only {len(collected)} games "
                            f"(venue ok: {len(good)}, saves_ok={_saves_ok}, "
                            f"competition_meta={_competition_meta_complete}, "
                            f"historical_poss={_pressure_possession_count}, "
                            f"home={_home_count}, away={_away_count}, "
                            f"selectedVenue={_selected_venue_count}/{_VENUE_HISTORY_TARGET}) — "
                            "falling through to Stage 1 for more data"
                        )
            except Exception as _ce:
                print(f"[CACHE-STAGE0] Error: {_ce}")

            try:
                # Fetch a deep finished-fixture pool across ALL competitions.
                # API-Sports rejects the old `last` parameter shape used here
                # in production, so use a bounded date window and then expand
                # by season below when the selected venue is still thin.
                _player_history_fixture_lookback = 100

                # ── On-demand cache: check team_fixture_history before calling API ──
                team_fixtures_raw = None
                _tfh_cache_ttl = 6 * 3600  # 6 hours — refresh often for accurate rest-day calculation
                try:
                    _tfh_doc = await db.team_fixture_history.find_one(
                        {"teamId": actual_team_id}, {"_id": 0, "fixtures": 1, "_ts": 1}
                    )
                    if _tfh_doc and _tfh_doc.get("fixtures"):
                        import time as _t2
                        _age = _t2.time() - _tfh_doc.get("_ts", 0)
                        if (
                            _age < _tfh_cache_ttl
                            and len(_tfh_doc.get("fixtures") or []) >= _player_history_fixture_lookback
                        ):
                            team_fixtures_raw = _tfh_doc["fixtures"]
                            print(f"[API-DIRECT] {req.playerName}: {len(team_fixtures_raw)} team fixtures from CACHE (age {int(_age/3600)}h)")
                except Exception:
                    pass

                if team_fixtures_raw is None and not _is_bdl_league:
                    _history_to = datetime.now(timezone.utc).date()
                    _history_from = _history_to - timedelta(days=365 * 3)
                    try:
                        team_fixtures_raw = await api_football_request(
                            "fixtures",
                            {
                                "team": actual_team_id,
                                "from": _history_from.isoformat(),
                                "to": _history_to.isoformat(),
                                "status": "FT",
                            },
                        )
                    except Exception as _fixture_pool_err:
                        print(
                            f"[API-DIRECT] {req.playerName}: current fixture "
                            f"pool failed: {type(_fixture_pool_err).__name__}"
                        )
                        team_fixtures_raw = []
                    if not team_fixtures_raw:
                        print(f"[API-DIRECT] No fixtures found for teamId={actual_team_id}")
                    else:
                        print(
                            f"[API-DIRECT] {req.playerName}: "
                            f"{len(team_fixtures_raw)} team fixtures from API "
                            f"(date window={_history_from.isoformat()}.."
                            f"{_history_to.isoformat()}, target="
                            f"{_player_history_fixture_lookback})"
                        )
                        # Write-back: cache for next prediction on same team
                        import time as _t3
                        try:
                            await db.team_fixture_history.update_one(
                                {"teamId": actual_team_id},
                                {"$set": {
                                    "teamId": actual_team_id,
                                    "fixtures": team_fixtures_raw,
                                    "_ts": _t3.time(),
                                    "_dt": datetime.now(timezone.utc),
                                }},
                                upsert=True
                            )
                        except Exception as _ce:
                            pass  # non-fatal — prediction continues

                # A knockout prediction needs enough equivalent knockout history
                # to be useful across seasons. The normal "last 40" team feed is
                # often dominated by one current season, so append the explicitly
                # selected historical fixtures while keeping the current feed.
                if extra_fixture_list:
                    _fixture_by_id = {}
                    for _fixture in (team_fixtures_raw or []) + list(extra_fixture_list):
                        _fid = (_fixture.get("fixture") or {}).get("id")
                        if _fid:
                            _fixture_by_id[_fid] = _fixture
                    team_fixtures_raw = list(_fixture_by_id.values())
                    print(
                        f"[API-DIRECT] {req.playerName}: expanded player-history pool "
                        f"to {len(team_fixtures_raw)} fixtures with "
                        f"{len(extra_fixture_list)} historical knockout candidates"
                    )

                async def _fetch_one(fix_raw):
                    try:
                        fid = fix_raw.get("fixture", {}).get("id")
                        if not fid:
                            return None
                        home_id = fix_raw.get("teams", {}).get("home", {}).get("id")
                        fix_venue = "home" if home_id == actual_team_id else "away"
                        fix_date = fix_raw.get("fixture", {}).get("date", "")[:10]
                        fix_league = fix_raw.get("league", {}).get("name", "")
                        fix_league_id = fix_raw.get("league", {}).get("id")
                        fix_round = fix_raw.get("league", {}).get("round", "")
                        opp_key = "away" if home_id == actual_team_id else "home"
                        fix_opponent = fix_raw.get("teams", {}).get(opp_key, {}).get("name", "")
                        home_goals = fix_raw.get("goals", {}).get("home", 0) or 0
                        away_goals = fix_raw.get("goals", {}).get("away", 0) or 0

                        # Helper: enrich game log with exact team possession from
                        # fixtures/statistics. Never derive the other side.
                        async def _enrich_possession(gl_dict: dict) -> dict:
                            home_id = fix_raw.get("teams", {}).get("home", {}).get("id")
                            away_id = fix_raw.get("teams", {}).get("away", {}).get("id")
                            home_poss, away_poss = await _fetch_fixture_possession(
                                fid, home_id, away_id
                            )
                            return _apply_optional_soccer_possession(
                                gl_dict,
                                fix_venue,
                                home_poss,
                                away_poss,
                            )

                        async def _enrich_opponent_sot(gl_dict: dict) -> dict:
                            if req.sport == "soccer":
                                home_id = fix_raw.get("teams", {}).get("home", {}).get("id")
                                away_id = fix_raw.get("teams", {}).get("away", {}).get("id")
                                opponent_context = await _fetch_fixture_opponent_context(
                                    fid, home_id, away_id, actual_team_id
                                )
                                if opponent_context.get("teamShotsOnTarget") is not None:
                                    gl_dict["teamShotsOnTarget"] = opponent_context["teamShotsOnTarget"]
                                if opponent_context.get("teamPasses") is not None:
                                    gl_dict["teamPassAttempts"] = opponent_context["teamPasses"]
                                if opponent_context.get("shotsOnTarget") is not None:
                                    gl_dict["opponentShotsOnTarget"] = opponent_context["shotsOnTarget"]
                                if opponent_context.get("passes") is not None:
                                    gl_dict["opponentPassAttempts"] = opponent_context["passes"]
                            return gl_dict

                        # Check prefetch cache first — avoids extra API call if already cached
                        cache_key = f"fxp_{fid}_{player_id}"
                        cached_doc = await db.fixture_player_cache.find_one({"_k": cache_key}, {"_id": 0, "d": 1, "_ts": 1})
                        if cached_doc and cached_doc.get("d"):
                            # Freshness guard: API-Football can take 2-4h to finalize player
                            # stats after FT. If the entry was cached < 4h ago it may reflect
                            # mid-match or early-post-FT data (e.g. 3 shots at HT vs 6 final).
                            # Re-fetch live so the cache gets overwritten with final values.
                            _doc_ts = cached_doc.get("_ts")
                            _doc_age_h = ((datetime.now(timezone.utc) - (
                                _doc_ts if _doc_ts and _doc_ts.tzinfo else
                                (_doc_ts.replace(tzinfo=timezone.utc) if _doc_ts else datetime.now(timezone.utc))
                            )).total_seconds() / 3600) if _doc_ts else 999
                            _cache_stale = _doc_age_h < 4.0
                            if _cache_stale:
                                pass  # fall through to live API fetch + overwrite
                            else:
                                gl = dict(cached_doc["d"])
                                minutes = gl.get("minutes", 0)
                                if not minutes or minutes == 0:
                                    return None
                                # For saves prop: bypass cache if saves value is None
                                # (pre-fetch cache often misses saves for GKs — always fetch fresh)
                                saves_cache_miss = (
                                    req.propType in {"saves", "goalie_saves"}
                                    and gl.get("goals_saves") is None
                                )
                                if not saves_cache_miss:
                                    gl["date"] = fix_date
                                    gl["opponent"] = fix_opponent
                                    gl["venue"] = fix_venue
                                    gl["score"] = f"{home_goals}-{away_goals}"
                                    gl["league"] = fix_league
                                    gl["leagueId"] = fix_league_id
                                    gl["competitionId"] = fix_league_id
                                    gl["competitionName"] = fix_league
                                    gl["round"] = fix_round
                                    gl["fixtureId"] = str(fid)
                                    gl["fixtureTeamId"] = actual_team_id
                                    gl["fixtureOpponentId"] = (
                                        fix_raw.get("teams", {})
                                        .get("away" if fix_venue == "home" else "home", {})
                                        .get("id")
                                    )
                                    gl["homeTeamId"] = (
                                        fix_raw.get("teams", {}).get("home", {}).get("id")
                                    )
                                    gl["awayTeamId"] = (
                                        fix_raw.get("teams", {}).get("away", {}).get("id")
                                    )
                                    raw_val = gl.get(stat_field_map.get(req.propType, ""), None)
                                    if raw_val is not None and minutes > 0:
                                        gl["targetStatPer90"] = round((raw_val / minutes) * 90, 2)
                                    gl["_fid"] = str(fid)
                                    gl = await _enrich_possession(gl)
                                    gl = await _enrich_opponent_sot(gl)
                                    return gl
                                # Fall through to live API fetch for saves

                        fix_data = await api_football_request("fixtures/players", {"fixture": fid})
                        if not fix_data:
                            return None

                        matched_stats = None
                        all_player_logs = {}
                        # Build a name→stats map for fallback matching
                        name_stats_map: dict = {}
                        _target_name_norm = req.playerName.lower().strip() if req.playerName else ""
                        for team_data in fix_data:
                            for p in team_data.get("players", []):
                                pid = p.get("player", {}).get("id")
                                pname = (p.get("player", {}).get("name") or "").lower().strip()
                                stats = p.get("statistics", [{}])[0] if p.get("statistics") else {}
                                mins = stats.get("games", {}).get("minutes") or 0
                                if pid:
                                    all_player_logs[pid] = _build_game_log(stats)
                                    if pid == player_id and mins > 0:
                                        matched_stats = stats
                                if pname and mins > 0:
                                    name_stats_map[pname] = stats

                        # Fallback: name-based match when ID lookup misses
                        if not matched_stats and _target_name_norm and name_stats_map:
                            # Try exact name match first
                            if _target_name_norm in name_stats_map:
                                matched_stats = name_stats_map[_target_name_norm]
                                print(f"[NAME-MATCH] fid={fid}: matched '{req.playerName}' by exact name")
                            else:
                                # Try partial match: target surname in API name or vice versa
                                target_parts = set(_target_name_norm.split())
                                for api_name, s in name_stats_map.items():
                                    api_parts = set(api_name.split())
                                    # At least one word must match and names share >50% of tokens
                                    common = target_parts & api_parts
                                    if common and len(common) / max(len(target_parts), len(api_parts)) >= 0.5:
                                        matched_stats = s
                                        print(f"[NAME-MATCH] fid={fid}: matched '{req.playerName}' → '{api_name}' (partial)")
                                        break

                        # Cache all players from this fixture (fire-and-forget, for position comparisons)
                        # Also write/refresh fxm_ doc (no _ts so it never expires via TTL)
                        _fix_home_id = fix_raw.get("teams", {}).get("home", {}).get("id")
                        _fix_away_id = fix_raw.get("teams", {}).get("away", {}).get("id")
                        _fix_home_name = fix_raw.get("teams", {}).get("home", {}).get("name", "")
                        _fix_away_name = fix_raw.get("teams", {}).get("away", {}).get("name", "")
                        async def _cache_fix(
                            fid_c,
                            logs_c,
                            fhid=_fix_home_id,
                            faid=_fix_away_id,
                            fhn=_fix_home_name,
                            fan=_fix_away_name,
                            fli=fix_league_id,
                            fln=fix_league,
                            fr=fix_round,
                        ):
                            ops = []
                            for pk, lv in logs_c.items():
                                _cached_player_log = dict(lv or {})
                                _cached_player_log.update({
                                    "date": fix_date,
                                    "opponent": fix_opponent,
                                    "venue": fix_venue,
                                    "score": f"{home_goals}-{away_goals}",
                                    "league": fix_league,
                                    "leagueId": fix_league_id,
                                    "competitionId": fix_league_id,
                                    "competitionName": fix_league,
                                    "round": fix_round,
                                    "fixtureId": str(fid_c),
                                    "fixtureContextStatus": "verified",
                                    "fixtureContextSource": "fixture_player_cache_row",
                                })
                                ops.append(
                                    db.fixture_player_cache.update_one(
                                        {"_k": f"fxp_{fid_c}_{pk}"},
                                        {
                                            "$set": {
                                                "_k": f"fxp_{fid_c}_{pk}",
                                                "_ts": datetime.now(timezone.utc),
                                                "d": _cached_player_log,
                                            }
                                        },
                                        upsert=True,
                                    )
                                )
                            # Refresh fxm_ without _ts so venue metadata is permanent (not TTL-expired)
                            if fhid and faid:
                                fxm_k = f"fxm_{fid_c}"
                                ops.append(db.fixture_player_cache.update_one(
                                    {"_k": fxm_k},
                                    {"$set": {"_k": fxm_k, "d": {
                                        "home_id": fhid, "away_id": faid,
                                        "home_name": fhn, "away_name": fan,
                                        "league_id": fli,
                                        "competition_id": fli,
                                        "league_name": fln,
                                        "competition_name": fln,
                                        "round": fr,
                                    }}},
                                    upsert=True
                                ))
                            if ops:
                                await aio.gather(*ops, return_exceptions=True)
                        aio.ensure_future(_cache_fix(fid, all_player_logs))

                        if not matched_stats:
                            return None

                        gl = _build_game_log(matched_stats)
                        gl["date"] = fix_date
                        gl["opponent"] = fix_opponent
                        gl["venue"] = fix_venue
                        gl["score"] = f"{home_goals}-{away_goals}"
                        gl["league"] = fix_league
                        gl["leagueId"] = fix_league_id
                        gl["competitionId"] = fix_league_id
                        gl["competitionName"] = fix_league
                        gl["round"] = fix_round
                        minutes = gl.get("minutes", 0)
                        raw_val = gl.get(stat_field_map.get(req.propType, ""), None)
                        if raw_val is not None and minutes > 0:
                            gl["targetStatPer90"] = round((raw_val / minutes) * 90, 2)
                        gl["_fid"] = str(fid)
                        gl["fixtureId"] = str(fid)
                        gl = await _enrich_possession(gl)
                        gl = await _enrich_opponent_sot(gl)
                        return gl
                    except Exception:
                        return None

                async def _fetch_fixture_batch(fixture_rows):
                    if not fixture_rows:
                        return []
                    fixture_rows = _newest_first_rows(fixture_rows)
                    sem = aio.Semaphore(10)

                    async def _sem_fetch(fix_raw):
                        async with sem:
                            return await _fetch_one(fix_raw)

                    results = await aio.gather(
                        *[_sem_fetch(fx) for fx in fixture_rows],
                        return_exceptions=True,
                    )
                    return [
                        result for result in results
                        if result and not isinstance(result, Exception)
                    ]

                current_fixture_count = len(team_fixtures_raw or [])
                collected.extend(
                    await _fetch_fixture_batch(team_fixtures_raw or [])
                )

                # A 100-fixture feed can still contain only a handful of
                # selected-venue appearances. Search older seasons, across all
                # competitions, until 30 verified player appearances exist.
                # Older rows are fetched venue-first to control API cost. If
                # the target remains unavailable after the full search, fetch
                # the remaining scanned rows so the all-history fallback is
                # still a genuine full-history sample.
                _older_fixture_pool = []
                _older_fetched_ids = set()
                if (
                    req.sport == "soccer"
                    and player_venue
                    and (
                        _venue_history_count(collected) < _VENUE_HISTORY_TARGET
                        or len(collected) < _RECENT_ARCHIVE_TARGET
                    )
                ):
                    _known_fixture_ids = {
                        str((fixture or {}).get("fixture", {}).get("id"))
                        for fixture in (team_fixtures_raw or [])
                        if (fixture or {}).get("fixture", {}).get("id")
                    }
                    for _older_season in range(
                        CURRENT_SEASON - 1,
                        CURRENT_SEASON - 1 - _VENUE_HISTORY_MAX_OLDER_SEASONS,
                        -1,
                    ):
                        try:
                            _season_rows = await api_football_request(
                                "fixtures",
                                {
                                    "team": actual_team_id,
                                    "season": _older_season,
                                    "status": "FT",
                                },
                            ) or []
                        except Exception as _season_err:
                            print(
                                f"[PLAYER HISTORY] {req.playerName}: season "
                                f"{_older_season} lookup failed: "
                                f"{type(_season_err).__name__}"
                            )
                            continue

                        _new_season_rows = []
                        for _season_fixture in _season_rows:
                            _season_fid = (
                                (_season_fixture.get("fixture") or {}).get("id")
                            )
                            if not _season_fid:
                                continue
                            _season_fid_key = str(_season_fid)
                            if _season_fid_key in _known_fixture_ids:
                                continue
                            _home_id = (
                                (_season_fixture.get("teams") or {})
                                .get("home", {})
                                .get("id")
                            )
                            _away_id = (
                                (_season_fixture.get("teams") or {})
                                .get("away", {})
                                .get("id")
                            )
                            if actual_team_id not in {_home_id, _away_id}:
                                continue
                            _known_fixture_ids.add(_season_fid_key)
                            _new_season_rows.append(_season_fixture)

                        if not _new_season_rows:
                            continue

                        _older_fixture_pool.extend(_new_season_rows)
                        _selected_rows = [
                            fixture for fixture in _new_season_rows
                            if (
                                "home"
                                if (
                                    ((fixture.get("teams") or {})
                                     .get("home", {}).get("id"))
                                    == actual_team_id
                                )
                                else "away"
                            ) == player_venue
                        ]
                        _selected_rows = sorted(
                            _selected_rows,
                            key=lambda fixture: str(
                                (fixture.get("fixture") or {}).get("date") or ""
                            ),
                            reverse=True,
                        )
                        # Fill the venue-prior target first, then continue
                        # across both venues until the customer archive reaches
                        # its independent target.
                        _priority_rows = (
                            _selected_rows
                            if _venue_history_count(collected) < _VENUE_HISTORY_TARGET
                            else []
                        )
                        _priority_logs = await _fetch_fixture_batch(_priority_rows)
                        _selected_logs = _priority_logs
                        collected.extend(_selected_logs)
                        _older_fetched_ids.update(
                            str((fixture.get("fixture") or {}).get("id"))
                            for fixture in _priority_rows
                            if (fixture.get("fixture") or {}).get("id")
                        )
                        print(
                            f"[PLAYER HISTORY] {req.playerName}: season "
                            f"{_older_season} added "
                            f"{len(_selected_logs)} {player_venue} appearances; "
                            f"venue={_venue_history_count(collected)}/"
                            f"{_VENUE_HISTORY_TARGET}"
                        )
                        if (
                            _venue_history_count(collected) >= _VENUE_HISTORY_TARGET
                            and len(collected) >= _RECENT_ARCHIVE_TARGET
                        ):
                            break

                    # If either the venue prior or the customer archive is
                    # still short, complete the scanned historical seasons.
                    # Rows remain exact-fixture, positive-minute, provider-
                    # verified data; this is not a synthetic filler path.
                    if (
                        _venue_history_count(collected) < _VENUE_HISTORY_TARGET
                        or len(collected) < _RECENT_ARCHIVE_MIN
                    ):
                        _remaining_rows = [
                            fixture for fixture in _older_fixture_pool
                            if str((fixture.get("fixture") or {}).get("id"))
                            not in _older_fetched_ids
                        ]
                        _remaining_rows = sorted(
                            _remaining_rows,
                            key=lambda fixture: str(
                                (fixture.get("fixture") or {}).get("date") or ""
                            ),
                            reverse=True,
                        )[:max(_RECENT_ARCHIVE_TARGET - len(collected), 0)]
                        if _remaining_rows:
                            _remaining_logs = await _fetch_fixture_batch(
                                _remaining_rows
                            )
                            collected.extend(_remaining_logs)
                            print(
                                f"[PLAYER HISTORY] {req.playerName}: venue target "
                                f"unavailable after older-season search; added "
                                f"{len(_remaining_logs)} remaining rows for "
                                "full-history fallback"
                            )

                    if _older_fixture_pool:
                        try:
                            _cache_rows = {}
                            for _fixture in (
                                list(team_fixtures_raw or [])
                                + _older_fixture_pool
                            ):
                                _fid = (_fixture.get("fixture") or {}).get("id")
                                if _fid:
                                    _cache_rows[str(_fid)] = _fixture
                            _merged_fixture_history = sorted(
                                _cache_rows.values(),
                                key=lambda fixture: str(
                                    (fixture.get("fixture") or {}).get("date") or ""
                                ),
                                reverse=True,
                            )[:400]
                            await db.team_fixture_history.update_one(
                                {"teamId": actual_team_id},
                                {"$set": {
                                    "teamId": actual_team_id,
                                    "fixtures": _merged_fixture_history,
                                    "historyLookback": "multi-season",
                                    "_ts": __import__("time").time(),
                                    "_dt": datetime.now(timezone.utc),
                                }},
                                upsert=True,
                            )
                        except Exception as _history_cache_err:
                            print(
                                f"[PLAYER HISTORY CACHE] skipped: "
                                f"{type(_history_cache_err).__name__}"
                            )

                print(
                    f"[API-DIRECT] {req.playerName}/{req.propType}: "
                    f"{len(collected)} real game logs from "
                    f"{current_fixture_count + len(_older_fixture_pool)} fixtures"
                )
            except aio.CancelledError:
                # The required-wave wrapper has a hard latency budget. Stage
                # 0 may already contain a large, real player archive when the
                # provider fallback is slow or quota-blocked; cancellation
                # must return that snapshot instead of replacing it with the
                # later 10-row direct fallback.
                print(
                    f"[API-DIRECT] {req.playerName}: provider history cancelled "
                    f"after preserving {len(collected)} cached rows"
                )
            except Exception as _e:
                print(f"[API-DIRECT] Error: {_e}")

            # ── Dedup by fixture ID ────────────────────────────────────────────
            # Stage 0 (MongoDB cache) and Stage 1 (team fixture loop) both read
            # from fixture_player_cache for the same fixture IDs — the same game
            # can appear twice: once without date/score (Stage 0) and once with
            # date/score/possession (Stage 1). Keep Stage 1's richer entry.
            if collected:
                _fid_index: dict = {}   # fid_str -> index in _deduped
                _deduped: list = []
                for _g in collected:
                    _g_fid = _g.get("_fid")
                    if not _g_fid:
                        _deduped.append(_g)
                    elif _g_fid not in _fid_index:
                        _fid_index[_g_fid] = len(_deduped)
                        _deduped.append(_g)
                    else:
                        # If the new entry has a real date and the existing one doesn't,
                        # replace — Stage 1 (with date) beats Stage 0 (empty date).
                        _existing = _deduped[_fid_index[_g_fid]]
                        if _g.get("date") and not _existing.get("date"):
                            _deduped[_fid_index[_g_fid]] = _g
                if len(_deduped) < len(collected):
                    print(f"[DEDUP] {req.playerName}: removed {len(collected) - len(_deduped)} duplicate fixture(s)")
                collected = _deduped
                # Strip internal marker before handing off
                for _g in collected:
                    _g.pop("_fid", None)

            return _newest_first_rows(collected)

        # =============================================
        # POSITION COMPARISON: Same-position players vs opponent
        # =============================================
        FIXTURE_POS_MAP = {
            "Goalkeeper": "G", "Defender": "D", "Midfielder": "M", "Attacker": "F",
            "GK": "G",
            "CB": "D", "LB": "D", "RB": "D", "LWB": "D", "RWB": "D",
            "CDM": "M", "CM": "M", "CAM": "M", "LM": "M", "RM": "M",
            "LW": "F", "RW": "F", "CF": "F", "ST": "F", "SS": "F",
        }
        PROP_STAT_KEYS = {
            "pass_attempts": ("passes", "total"), "shots": ("shots", "total"),
            "shots_on_target": ("shots", "on"), "tackles": ("tackles", "total"),
            "key_passes": ("passes", "key"), "shots_assisted": ("passes", "key"),
            "saves": ("goals", "saves"),
            "interceptions": ("tackles", "interceptions"), "blocks": ("tackles", "blocks"),
            "dribbles": ("dribbles", "attempts"), "fouls_drawn": ("fouls", "drawn"),
            "goals": ("goals", "total"), "assists": ("goals", "assists"),
            "crosses": ("passes", "cross"), "clearances": ("tackles", "clearances"),
            "duels_won": ("duels", "won"), "yellow_cards": ("cards", "yellow"),
        }

        def _cohort_team_key(row):
            """Use one evidence row per opposing team, not one per player."""
            team_id = row.get("teamId")
            if team_id not in (None, "", 0, "0"):
                return f"id:{team_id}"
            team_name = str(row.get("team") or "").strip().lower()
            if team_name:
                return f"name:{team_name}"
            player_id = row.get("playerId")
            return f"player:{player_id or str(row.get('name') or '').strip().lower()}"

        async def fetch_position_comparison(
            opp_fixtures,
            target_pos,
            prop_type,
            opponent_id,
            player_venue_filter,
            limit=20,
            target_specific_pos=None,
            target_role=None,
            allow_broad_category=False,
            allow_exact_fallback=False,
        ):
            """Fetch exact-position comparison players who played against the opponent.
            Filters by venue: if target player is AWAY, only show comparison players' AWAY performances.
            Also fetches possession data for each match.
            If target_specific_pos is set, the candidate must match that verified
            specific position. Tactical role matching is intentionally limited to
            forwards and midfielders; defender roles such as Stopper and
            Ball-Playing CB are not reliable enough to filter out an otherwise
            exact CB/LB/RB appearance.

            When the selected player only has a provider-level D/M/F label,
            ``allow_broad_category`` admits verified category rows for display
            only. Those rows remain explicitly broad-category evidence and are
            never eligible to change the deterministic projection."""
            fixture_pos = FIXTURE_POS_MAP.get(target_pos, "")
            if not fixture_pos or not opp_fixtures:
                return []
            _exact_positions = {
                "GK", "CB", "LB", "RB", "LWB", "RWB", "CDM", "CM",
                "CAM", "LM", "RM", "LW", "RW", "CF", "ST", "SS",
            }
            # Exact cohorts require an exact target. A broad-category fallback
            # is separately opt-in so generic D/M/F observations can be shown
            # as context without being mislabeled as CB/CM/ST evidence.
            if target_specific_pos not in _exact_positions and not allow_broad_category:
                return []
            stat_cat, stat_sub = PROP_STAT_KEYS.get(prop_type, ("passes", "total"))
            # The comparison players' venue should match the TARGET player's venue
            # If target is AWAY, we want other players who also played AWAY against this opponent
            comp_venue = player_venue_filter  # "home" or "away"
            # API-Football often exposes only F/FWD for wide attackers when a
            # lineup grid is absent or rate-limited. Those rows are useful
            # opponent context for LW/RW/LM/RM/WB targets, but they must be
            # explicitly labeled broad and can never become projection input.
            _wide_position_fallback = bool(
                allow_exact_fallback
                and target_specific_pos in {"LW", "RW", "LM", "RM", "LWB", "RWB"}
            )

            async def fetch_pos_from_fixture(fix):
                fid = fix.get("fixtureId")
                if not fid:
                    return []
                try:
                    # Fetch players, fixture statistics, and the lineup grid
                    # together.  The player-stat endpoint often reports only
                    # D/M/F; the confirmed lineup grid is what identifies
                    # CB/LB/RB in that specific appearance.
                    players_task = api_football_request("fixtures/players", {"fixture": fid})
                    stats_task = api_football_request("fixtures/statistics", {"fixture": fid})
                    # Start lineup enrichment at the same time, but do not
                    # make player/stat rows wait for it. The source-player
                    # evidence must survive a slow or rate-limited lineup
                    # endpoint.
                    lineups_task = aio.create_task(
                        api_football_request("fixtures/lineups", {"fixture": fid})
                    )
                    try:
                        players_data, fixture_stats_data = await aio.wait_for(
                            aio.gather(players_task, stats_task, return_exceptions=True),
                            timeout=3.0,
                        )
                    except Exception:
                        if not lineups_task.done():
                            lineups_task.cancel()
                        return []
                    try:
                        # Give the optional exact-position enrichment a brief
                        # independent window after player/stat data is safe.
                        # A slow lineup response may be skipped, but it can no
                        # longer cancel or erase the usable historical rows.
                        lineups_data = await aio.wait_for(lineups_task, timeout=1.0)
                    except Exception:
                        if not lineups_task.done():
                            lineups_task.cancel()
                        lineups_data = []
                    # Lineup grids are valuable exact-position enrichment, but
                    # they are optional for the historical evidence row. A
                    # lineup 429/timeout must never discard usable player
                    # stats and possession response.
                    if isinstance(players_data, Exception):
                        players_data = []
                    if isinstance(fixture_stats_data, Exception):
                        fixture_stats_data = []
                    if isinstance(lineups_data, Exception):
                        lineups_data = []

                    if not players_data:
                        return []

                    # Parse possession from fixture stats
                    possession_map = {}  # team_id -> possession %
                    if fixture_stats_data:
                        for team_stats in fixture_stats_data:
                            tid = team_stats.get("team", {}).get("id")
                            for stat in team_stats.get("statistics", []):
                                if stat.get("type") == "Ball Possession":
                                    poss_str = str(stat.get("value", "0")).replace("%", "")
                                    try:
                                        possession_map[tid] = int(poss_str)
                                    except (ValueError, TypeError):
                                        pass

                    # Map player ID → exact observed position from the
                    # fixture lineup grid.  Keep the provider category as a
                    # fallback when a lineup is missing or the formation
                    # cannot support an unambiguous inference.
                    lineup_position_map = {}
                    lineup_formation_map = {}
                    for lineup_team in _api_response_list(lineups_data):
                        formation = lineup_team.get("formation")
                        for lineup_row in lineup_team.get("startXI", []):
                            lineup_player = lineup_row.get("player") or {}
                            lineup_player_id = lineup_player.get("id")
                            if lineup_player_id is not None:
                                lineup_player_key = _normalize_provider_player_id(
                                    lineup_player_id
                                )
                                lineup_position_map[lineup_player_key] = infer_grid_position(
                                    lineup_player.get("grid"),
                                    formation,
                                    lineup_player.get("pos"),
                                )
                                lineup_formation_map[lineup_player_key] = formation

                    results = []
                    for team_data in players_data:
                        tid = team_data.get("team", {}).get("id")
                        team_name = team_data.get("team", {}).get("name", "")
                        if tid == opponent_id:
                            continue  # Skip opponent — we want teams who PLAYED AGAINST them

                        # Venue filter: determine if this team was home or away in this fixture
                        # The opponent's fixture list has opp_venue (opponent's venue)
                        # If opponent was HOME, the comparison team was AWAY, and vice versa
                        opp_fixture_venue = fix.get("venue", "")  # opponent's venue in this fixture
                        comp_team_venue = "away" if opp_fixture_venue == "home" else "home"
                        if comp_venue != "any" and comp_team_venue != comp_venue:
                            continue  # Skip — wrong venue for comparison

                        team_poss = possession_map.get(tid, None)
                        opp_poss = possession_map.get(opponent_id, None)
                        # Comparison evidence has the same customer-facing
                        # contract as the selected player's history: both
                        # fixture-side possession values must be verified.
                        if team_poss is None or opp_poss is None:
                            continue

                        for p in team_data.get("players", []):
                            pstats = p.get("statistics", [{}])[0] if p.get("statistics") else {}
                            pos = pstats.get("games", {}).get("position", "")
                            minutes = pstats.get("games", {}).get("minutes") or 0
                            provider_position = normalize_observed_position(pos)
                            # Keep the broad provider observation available
                            # for the response even when the fixture lineup
                            # does not return an exact grid position.
                            observed_normalized = provider_position
                            _target_generic_categories = {
                                "GK": {"G", "GK"},
                                "CB": {"D", "DEF"},
                                "LB": {"D", "DEF"},
                                "RB": {"D", "DEF"},
                                "LWB": {"D", "DEF", "M", "MID"},
                                "RWB": {"D", "DEF", "M", "MID"},
                                "CDM": {"M", "MID"},
                                "CM": {"M", "MID"},
                                "CAM": {"M", "MID"},
                                "LM": {"M", "MID"},
                                "RM": {"M", "MID"},
                                "LW": {"F", "FWD", "M", "MID"},
                                "RW": {"F", "FWD", "M", "MID"},
                                "CF": {"F", "FWD"},
                                "ST": {"F", "FWD"},
                                "SS": {"F", "FWD", "M", "MID"},
                            }
                            _provider_category_allowed = (
                                provider_position == normalize_observed_position(fixture_pos)
                                or (
                                    target_specific_pos in _target_generic_categories
                                    and provider_position in _target_generic_categories[target_specific_pos]
                                )
                            )
                            if not _provider_category_allowed or minutes < 30:
                                continue
                            stat_val = pstats.get(stat_cat, {}).get(stat_sub)
                            if stat_val is None:
                                continue
                            cross_prop_stats = {}
                            for _cross_prop, (_cross_cat, _cross_sub) in PROP_STAT_KEYS.items():
                                _cross_value = (pstats.get(_cross_cat) or {}).get(_cross_sub)
                                if _cross_value is not None:
                                    try:
                                        cross_prop_stats[_cross_prop] = float(_cross_value)
                                    except (TypeError, ValueError):
                                        pass
                            rating = pstats.get("games", {}).get("rating")
                            p_id = p.get("player", {}).get("id")
                            p_id_key = _normalize_provider_player_id(p_id)
                            p_name = p.get("player", {}).get("name", "")
                            grid_position = lineup_position_map.get(p_id_key)
                            observed_fixture_position = (
                                grid_position
                                if grid_position in {
                                    "GK", "CB", "LB", "RB", "LWB", "RWB",
                                    "CDM", "CM", "CAM", "LM", "RM", "LW",
                                    "RW", "CF", "ST", "SS",
                                }
                                else provider_position
                            )

                            # Look up cached specific position + role. Atlas may be
                            # write-blocked, so a missing cache row is expected; in
                            # that case infer the role from this API fixture row.
                            cached_pr = await db.player_positions.find_one(
                                {
                                    "$or": [
                                        {"playerId": p_id},
                                        {"playerId": p_id_key},
                                        {"playerId": str(p_id)}
                                        if p_id is not None
                                        else {"playerId": None},
                                    ]
                                },
                                {
                                    "_id": 0,
                                    "specificPosition": 1,
                                    "role": 1,
                                    "source": 1,
                                    "roleSource": 1,
                                    "confidence": 1,
                                },
                            ) if p_id else None
                            spec_pos = (cached_pr or {}).get("specificPosition", "")
                            spec_role = (cached_pr or {}).get("role", "")
                            # A cache row is only an exact-position claim when it
                            # came from grounded/manual evidence. Category
                            # fallbacks can contain a made-up CB/CM/etc. and must
                            # never turn a broad fixture label (D/M/F) into LB,
                            # CM, or another customer-facing natural position.
                            cached_position_source = (cached_pr or {}).get("source") or (
                                cached_pr or {}
                            ).get("roleSource")
                            trusted_cached_position = (
                                str(cached_position_source or "") in {
                                    "gemini_web_grounded",
                                    "manual_override",
                                    "api_sports_lineup_history",
                                }
                                and bool(spec_pos)
                            )
                            # Generic D/M/F observations are category evidence,
                            # not exact-position evidence.  They may support a
                            # broad cohort, but can never satisfy an exact CB,
                            # LB, or role-specific target.
                            _exact_target_requested = target_specific_pos in {
                                "GK", "CB", "LB", "RB", "LWB", "RWB", "CDM",
                                "CM", "CAM", "LM", "RM", "LW", "RW", "CF",
                                "ST", "SS",
                            }
                            observed_exact_target = bool(
                                _exact_target_requested
                                and observed_fixture_position == target_specific_pos
                            )
                            cached_exact_target = bool(
                                target_specific_pos
                                and trusted_cached_position
                                and spec_pos == target_specific_pos
                            )
                            role_stats = {
                                "appearances": 1,
                                "passes_total": (pstats.get("passes") or {}).get("total"),
                                "key_passes": (pstats.get("passes") or {}).get("key"),
                                "tackles_total": (pstats.get("tackles") or {}).get("total"),
                                "dribbles_attempts": (pstats.get("dribbles") or {}).get("attempts"),
                                "shots_total": (pstats.get("shots") or {}).get("total"),
                                "clearances": (pstats.get("tackles") or {}).get("clearances"),
                            }
                            observed_role = resolve_observed_role(
                                observed_fixture_position,
                                role_stats,
                            )
                            # A match row is the strongest evidence for the
                            # player's actual position in that appearance.
                            # Never let a stale grounded profile replace an
                            # observed CB/LB/RB role, and never attach a
                            # cached exact role to a broad D/DEF row.
                            if observed_fixture_position == "DEF":
                                candidate_role = ""
                            elif observed_fixture_position in {
                                "GK", "CB", "LB", "RB", "LWB", "RWB", "CDM",
                                "CM", "CAM", "LM", "RM", "LW", "RW", "CF",
                                "ST", "SS",
                            }:
                                candidate_role = observed_role.get("role") or ""
                            else:
                                candidate_role = (
                                    spec_role if trusted_cached_position
                                    else observed_role.get("role") or ""
                                )

                            # Prefer the actual position recorded in this match
                            # over a stale cache row. If neither exists, retain
                            # the broad provider category rather than inventing
                            # an exact position.
                            observed_pos = observed_normalized
                            target_generic_category = {
                                "GK": "GK",
                                "CB": "DEF", "LB": "DEF", "RB": "DEF",
                                "LWB": "DEF", "RWB": "DEF",
                                "CDM": "MID", "CM": "MID", "CAM": "MID",
                                "LM": "MID", "RM": "MID",
                                "LW": "FWD", "RW": "FWD", "CF": "FWD",
                                "ST": "FWD", "SS": "FWD",
                            }.get(target_specific_pos, fixture_pos)
                            if _exact_target_requested and not (
                                observed_exact_target
                                or cached_exact_target
                                or _wide_position_fallback
                            ):
                                # Broad provider categories (DEF/MID/FWD) are
                                # useful for broad cohorts, but are not proof of
                                # the target's natural side-specific position.
                                continue
                            # Exact defender position is the complete comparison
                            # key. API-Football/Gemini role labels frequently
                            # disagree between otherwise equivalent centre-backs
                            # (for example Stopper vs Ball-Playing CB), so a role
                            # gate here incorrectly emptied Carlos's cohort.
                            _apply_role_match = False

                            position_verified = bool(
                                observed_exact_target or cached_exact_target
                            )
                            if grid_position in {
                                "GK", "CB", "LB", "RB", "LWB", "RWB",
                                "CDM", "CM", "CAM", "LM", "RM", "LW",
                                "RW", "CF", "ST", "SS",
                            }:
                                position_value = grid_position
                                position_source = "fixture_lineup_grid"
                            elif observed_exact_target:
                                position_value = target_specific_pos
                                position_source = "fixture_observed"
                            elif cached_exact_target:
                                position_value = spec_pos
                                position_source = "grounded_profile"
                            else:
                                position_value = observed_fixture_position or fixture_pos
                                position_source = (
                                    "fixture_lineup_grid"
                                    if grid_position in {
                                        "GK", "CB", "LB", "RB", "LWB", "RWB",
                                        "CDM", "CM", "CAM", "LM", "RM", "LW",
                                        "RW", "CF", "ST", "SS",
                                    }
                                    else "provider_category"
                                )

                            # GK-specific: capture goals conceded for per-game save rate.
                            # For saves prop: stat_cat="goals", stat_sub="saves" per PROP_STAT_KEYS.
                            # Conceded is at the same "goals" block in the fixture player API.
                            _gk_conceded = None
                            if prop_type == "saves":
                                _raw_conceded = pstats.get("goals", {}).get("conceded")
                                if _raw_conceded is not None:
                                    try:
                                        _gk_conceded = int(_raw_conceded)
                                    except (TypeError, ValueError):
                                        pass

                            _candidate_role_source = (
                                "cached_role"
                                if spec_role and trusted_cached_position
                                else observed_role.get("source")
                            )
                            results.append({
                                "name": p_name,
                                "playerId": p_id,
                                "teamId": tid,
                                "team": team_name,
                                "minutes": minutes,
                                "statValue": stat_val,
                                "passAttempts": (pstats.get("passes") or {}).get("total"),
                                "crossPropStats": cross_prop_stats,
                                "rating": float(rating) if rating else None,
                                "date": fix.get("date", "")[:10],
                                "per90": round((stat_val / minutes) * 90, 2) if minutes > 0 else 0,
                                "venue": comp_team_venue,
                                "position": position_value or None,
                                "matchPosition": observed_normalized or pos or None,
                                "exactPosition": (
                                    observed_fixture_position
                                    if observed_fixture_position in {
                                        "GK", "CB", "LB", "RB", "LWB", "RWB",
                                        "CDM", "CM", "CAM", "LM", "RM", "LW",
                                        "RW", "CF", "ST", "SS",
                                    }
                                    else None
                                ),
                                "gridPosition": grid_position,
                                "lineupFormation": lineup_formation_map.get(p_id_key),
                                "positionMatch": "specific" if position_verified else "provider_category",
                                "positionVerified": position_verified,
                                "positionSource": position_source,
                                "observedPosition": observed_fixture_position or observed_normalized or pos or None,
                                "role": candidate_role or None,
                                "roleMatchApplied": _apply_role_match,
                                "roleSource": _candidate_role_source,
                                "roleInferred": bool(
                                    candidate_role
                                    and str(_candidate_role_source or "").endswith("_inferred")
                                ),
                                "teamPossession": team_poss,
                                "oppPossession": opp_poss,
                                "tp": team_poss,
                                "minutesPlayed": minutes,
                                "goalsConceded": _gk_conceded,
                            })
                    ordered_results = _newest_first_rows(results)
                    if target_specific_pos in _exact_positions and _wide_position_fallback:
                        exact_rows = [
                            row for row in ordered_results
                            if row.get("positionVerified") is True
                        ]
                        # Prefer exact lineup/profile evidence whenever it exists. The
                        # broad fallback is only a recovery path for a genuinely sparse
                        # exact cohort, never a mixture that could dilute exact rows.
                        if exact_rows:
                            return exact_rows
                        return [
                            row for row in ordered_results
                            if row.get("positionVerified") is not True
                        ]
                    return ordered_results
                except Exception:
                    return []

            async def _bounded_fixture(fix):
                try:
                    # A single rate-limited fixture must not make the entire
                    # opponent cohort wait. Keep every fast, verified fixture
                    # result and omit only the one that exceeds this bound.
                    return await aio.wait_for(fetch_pos_from_fixture(fix), timeout=4.5)
                except Exception as _fixture_err:
                    return []

            tasks = [
                _bounded_fixture(f)
                for f in _newest_first_rows(opp_fixtures, limit)
            ]
            raw_results = await aio.gather(*tasks, return_exceptions=True)
            all_players = []
            for r in raw_results:
                if isinstance(r, list):
                    all_players.extend(r)
            # Preserve the exact pre-expansion average for the existing
            # deterministic opponent-profile adjustment. The larger cohort
            # below is evidence/shadow-only until replay validates it.
            legacy_unique = []
            legacy_seen_names = set()
            # Dedup by most-recent fixture first so the selected appearance
            # per player reflects current form, not a historical outlier.
            for row in sorted(all_players, key=lambda x: str(x.get("date") or ""), reverse=True):
                name_key = str(row.get("name") or "").strip().lower()
                if not name_key or name_key in legacy_seen_names:
                    continue
                legacy_seen_names.add(name_key)
                legacy_unique.append(row)
                if len(legacy_unique) >= 10:
                    break
            legacy_values = [
                float(row["statValue"])
                for row in legacy_unique
                if isinstance(row.get("statValue"), (int, float))
            ]
            legacy_model_average = (
                round(sum(legacy_values) / len(legacy_values), 2)
                if legacy_values else None
            )
            # Collapse repeat opponent appearances into one row per player.
            # The old implementation kept the highest raw game for each name,
            # which biased the cohort toward outliers. Repeated appearances now
            # contribute through a capped reliability weight, while each player
            # remains one distinct cohort observation.
            by_player = {}
            for row in all_players:
                key = row.get("playerId") or str(row.get("name") or "").strip().lower()
                if not key:
                    continue
                by_player.setdefault(key, []).append(row)

            unique = []
            for rows in by_player.values():
                if not rows:
                    continue
                newest = max(rows, key=lambda x: str(x.get("date") or ""))
                stat_rows = [
                    row for row in rows
                    if isinstance(row.get("statValue"), (int, float))
                ]
                if not stat_rows:
                    continue
                row_weights = [
                    max(0.25, min(1.0, (float(row.get("minutes") or 0) / 90.0)))
                    for row in stat_rows
                ]
                weight_total = sum(row_weights)

                def _weighted_value(field):
                    pairs = [
                        (float(row[field]), weight)
                        for row, weight in zip(stat_rows, row_weights)
                        if isinstance(row.get(field), (int, float))
                    ]
                    total = sum(weight for _, weight in pairs)
                    return round(sum(value * weight for value, weight in pairs) / total, 2) if total else None

                collapsed = dict(newest)
                collapsed["statValue"] = _weighted_value("statValue")
                collapsed["passAttempts"] = _weighted_value("passAttempts")
                collapsed["per90"] = _weighted_value("per90")
                collapsed["minutes"] = round(
                    sum(float(row.get("minutes") or 0) for row in stat_rows) / len(stat_rows), 1
                )
                collapsed["appearanceCount"] = len(stat_rows)
                collapsed["evidenceWeight"] = round(
                    min(1.75, (weight_total ** 0.5) * (0.75 + 0.25 * min(1.0, collapsed["minutes"] / 90.0))),
                    3,
                )
                collapsed["crossPropStats"] = {}
                cross_keys = {
                    key
                    for row in stat_rows
                    for key in (row.get("crossPropStats") or {}).keys()
                }
                for cross_key in cross_keys:
                    cross_pairs = [
                        (float(row["crossPropStats"][cross_key]), weight)
                        for row, weight in zip(stat_rows, row_weights)
                        if isinstance((row.get("crossPropStats") or {}).get(cross_key), (int, float))
                    ]
                    cross_total = sum(weight for _, weight in cross_pairs)
                    if cross_total:
                        collapsed["crossPropStats"][cross_key] = round(
                            sum(value * weight for value, weight in cross_pairs) / cross_total,
                            2,
                        )
                unique.append(collapsed)

            # Prefer recent verified players, then stronger observation
            # reliability. Never pad the result with unverified rows.
            unique.sort(
                key=lambda x: (
                    str(x.get("date") or ""),
                    float(x.get("evidenceWeight") or 0),
                ),
                reverse=True,
            )
            # A team may field two centre-backs (or two players at another
            # exact position), but those are not independent opponent-team
            # observations. Keep the strongest, most recently observed
            # representative so one club cannot visually or statistically
            # overweight the cohort.
            def _cohort_row_rank(row):
                try:
                    evidence_weight = float(row.get("evidenceWeight") or 0)
                except (TypeError, ValueError):
                    evidence_weight = 0.0
                try:
                    minutes = float(row.get("minutes") or 0)
                except (TypeError, ValueError):
                    minutes = 0.0
                return (
                    1 if row.get("positionVerified") is True else 0,
                    evidence_weight,
                    minutes,
                    str(row.get("date") or ""),
                )

            one_per_team = {}
            for row in unique:
                team_key = _cohort_team_key(row)
                current = one_per_team.get(team_key)
                if current is None or _cohort_row_rank(row) > _cohort_row_rank(current):
                    one_per_team[team_key] = row
            unique = sorted(
                one_per_team.values(),
                key=lambda x: (
                    str(x.get("date") or ""),
                    float(x.get("evidenceWeight") or 0),
                ),
                reverse=True,
            )
            if unique and legacy_model_average is not None:
                unique[0]["_legacyModelAverage"] = legacy_model_average
            return unique[:15]

        # =============================================
        # VENUE-FILTERED DATA: Everything is venue-based
        # =============================================
        # If player is HOME → team's HOME games + opponent's AWAY games
        # If player is AWAY → team's AWAY games + opponent's HOME games
        player_venue = req.venue.lower()  # "home" or "away" (legacy clients may still send "neutral")
        # "Neutral" venue is a fiction — even at a neutral tournament site, one team
        # effectively plays like the home side (bigger following in the crowd, more
        # expected support) and the other like the away side. There is no real
        # in-between, so we always resolve a definite home/away here rather than
        # letting "neutral" skip venue-aware logic downstream. Priority of signals:
        #   1. Betting-market favorite (proxy for which team the world is backing)
        #   2. The fixture's own home/away designation from API-Football
        #   3. A deterministic team-ID tiebreaker (last resort, no data available)
        if player_venue == "neutral":
            _fav = (match_odds or {}).get("favorite")       # "home"/"away", relative to FIXTURE home/away
            _pih = (match_odds or {}).get("playerIsHome")
            if _fav is not None and _pih is not None:
                _player_is_favorite = (_fav == "home") == bool(_pih)
                player_venue = "home" if _player_is_favorite else "away"
                _ev_source = "odds"
            elif _pih is not None:
                player_venue = "home" if _pih else "away"
                _ev_source = "fixture"
            else:
                player_venue = "home" if (actual_team_id or 0) < (req.opponentId or 0) else "away"
                _ev_source = "tiebreaker"
            print(f"[EFFECTIVE VENUE] neutral→{player_venue} source={_ev_source} player={req.playerName}")
        # API-Football always designates one team as home (1) and one as away (2) for
        # every fixture — including World Cup matches. We trust that designation and the
        # playerIsHome flag from get_match_odds().
        _is_neutral = False  # normalized above — nothing downstream should treat a match as neutral anymore
        # ── VENUE ALIGNMENT: override user-selected venue with fixture reality ──
        # If the user typed a venue that contradicts the actual fixture assignment
        # (e.g. selected HOME for a team API-Football designated as AWAY), the entire
        # pipeline — game log filtering, possession calculation, and structured evidence — must
        # use a SINGLE consistent venue. We trust the fixture data because it determines
        # the actual match context (home/away possession, opponent venue, etc.).
        #
        # Track provenance so the final snapshot records whether a repair happened:
        #   _venue_source = "fixture"  → playerIsHome from verified fixture data
        #   _venue_source = "request"  → no fixture confirmation; user input used as-is
        #
        # _venue_contradiction_detected was set at the prefetch boundary (before model_copy
        # silently corrected req.venue).  _raw_request_venue is the user-supplied value.
        # Both are captured earlier in the function — we read them here so the alignment
        # block has the original intent available regardless of what req.venue is now.
        _venue_was_repaired = locals().get("_venue_contradiction_detected", False)
        _original_request_venue = locals().get("_raw_request_venue", player_venue)
        _venue_source = "request"  # upgraded to "fixture" when odds/fixture confirms
        _pih_after_odds = match_odds.get("playerIsHome") if match_odds else None
        if _pih_after_odds is not None:
            _fixture_venue = "home" if _pih_after_odds else "away"
            _venue_source = "fixture"
            if player_venue != _fixture_venue:
                # Defensive double-check: model_copy should have aligned player_venue
                # already, but log and correct here for any path that bypassed prefetch.
                print(f"[VENUE ALIGN] user={player_venue} → fixture={_fixture_venue} "
                      f"player={req.playerName} team={corrected_team_name}")
                player_venue = _fixture_venue
                _venue_was_repaired = True
        opponent_venue = "away" if player_venue == "home" else "home"
        is_womens = req.leagueId in WOMENS_LEAGUE_IDS
        pronoun_note = "IMPORTANT: This is a WOMEN'S league. Use she/her/her pronouns for all players. Never use he/him/his." if is_womens else ""

        # Filter team's recent fixtures by venue (skipped for neutral — no venue preference)
        venue_filtered_team_fixtures = (
            [] if _is_neutral else [f for f in recent_fixtures if f.get("venue") == player_venue]
        )
        # Also keep all fixtures for general context
        all_team_fixtures = recent_fixtures

        # Get opponent's recent fixtures — local DB first, API fallback.
        # Keep a broad enough API-backed pool to reach the 15-player cohort
        # target; the cohort itself still refuses to pad missing evidence.
        _cohort_fixture_lookback = 40
        opponent_recent_raw = []
        if safe_opp_id:
            try:
                from cache import get_cached_team_fixtures as _get_opp_fixtures
                _opp_local = await _get_opp_fixtures(safe_opp_id)
                if _opp_local:
                    opponent_recent_raw = _newest_first_rows(
                        _opp_local,
                        _cohort_fixture_lookback,
                    )
                    print(f"[LOCAL] Opponent fixtures from DB: {len(opponent_recent_raw)} games")
            except Exception:
                pass
            # A cache generated with the older 20-fixture sync must not make
            # the newer 40-fixture cohort lookback silently behave like 20.
            # Fill only the missing tail from the provider, then deduplicate by
            # fixture ID so cached rows remain the primary source.
            if (
                len(opponent_recent_raw) < _cohort_fixture_lookback
                and not _is_bdl_league
            ):
                try:
                    _opp_live = await api_football_request(
                        "fixtures",
                        {"team": safe_opp_id, "last": _cohort_fixture_lookback},
                    )
                    _seen_opp_fixture_ids = {
                        row.get("fixture", {}).get("id")
                        for row in (_opp_live or [])
                        if row.get("fixture", {}).get("id")
                    }
                    _cached_opp_ids = {
                        row.get("fixtureId")
                        or (row.get("fixture", {}) or {}).get("id")
                        for row in (opponent_recent_raw or [])
                        if (
                            row.get("fixtureId")
                            or (row.get("fixture", {}) or {}).get("id")
                        )
                    }
                    _live_only = [
                        row for row in (_opp_live or [])
                        if row.get("fixture", {}).get("id") not in _cached_opp_ids
                    ]
                    if _live_only:
                        opponent_recent_raw = (
                            list(opponent_recent_raw) + list(_live_only)
                        )
                        opponent_recent_raw = _newest_first_rows(
                            opponent_recent_raw,
                            _cohort_fixture_lookback,
                        )
                    print(
                        f"[COHORT FIXTURES] opponent={safe_opp_id} "
                        f"cache={len(_cached_opp_ids)} live={len(_seen_opp_fixture_ids)} "
                        f"merged={len(opponent_recent_raw)}"
                    )
                except Exception as _opp_live_err:
                    print(
                        f"[COHORT FIXTURES] live fill skipped: "
                        f"{type(_opp_live_err).__name__}"
                    )
        def _normalize_opponent_fixtures(raw_fixtures):
            normalized = []
            for f in raw_fixtures or []:
                fixture = f.get("fixture", {}) or {}
                teams = f.get("teams", {}) or {}
                home = teams.get("home", {}) or {}
                away = teams.get("away", {}) or {}
                fixture_id = fixture.get("id")
                opp_home_id = home.get("id")
                if not fixture_id or not opp_home_id:
                    continue
                opp_venue = "home" if opp_home_id == req.opponentId else "away"
                normalized.append({
                    "fixtureId": fixture_id,
                    "date": fixture.get("date", ""),
                    "opponent": (away if opp_venue == "home" else home).get("name", "Unknown"),
                    "venue": opp_venue,
                    "homeGoals": (f.get("goals", {}) or {}).get("home", 0) or 0,
                    "awayGoals": (f.get("goals", {}) or {}).get("away", 0) or 0,
                })
            return _newest_first_rows(normalized)

        opponent_fixture_list = _normalize_opponent_fixtures(opponent_recent_raw)

        async def _prepare_possession_schedule_pool(
            base_rows,
            team_id,
            venue_filter,
        ):
            """Build a real club schedule pool for possession evidence.

            The normal recent-fixture feed is intentionally shallow and can
            contain only the current competition season. Possession evidence
            needs the club schedule, so use the durable multi-season fixture
            archive and, when the verified possession sample is still short,
            fetch prior completed seasons before declaring a venue unavailable.
            """
            rows_by_id = {}

            def _add_row(row):
                if not isinstance(row, dict):
                    return
                fixture_id = row.get("fixtureId") or (
                    row.get("fixture") or {}
                ).get("id")
                if not fixture_id:
                    return
                teams = row.get("teams") or {}
                home = teams.get("home") or {}
                away = teams.get("away") or {}
                home_id = home.get("id")
                away_id = away.get("id")
                if home_id is not None and away_id is not None and team_id:
                    if team_id not in {home_id, away_id}:
                        return
                    row_venue = "home" if home_id == team_id else "away"
                    opponent = (
                        away.get("name") if row_venue == "home"
                        else home.get("name")
                    )
                    date = (row.get("fixture") or {}).get("date") or row.get("date")
                else:
                    row_venue = row.get("venue")
                    opponent = row.get("opponent") or ""
                    date = row.get("date") or ""
                if row_venue not in {"home", "away"}:
                    return
                if venue_filter and row_venue != venue_filter:
                    return
                rows_by_id[str(fixture_id)] = {
                    "fixtureId": fixture_id,
                    "date": str(date or "")[:10],
                    "opponent": opponent or "Unknown",
                    "venue": row_venue,
                }

            for base_row in base_rows or []:
                _add_row(base_row)

            async def _add_history_cache():
                try:
                    history_doc = await db.team_fixture_history.find_one(
                        {"teamId": team_id},
                        {"_id": 0, "fixtures": 1},
                    )
                    for history_row in (history_doc or {}).get("fixtures") or []:
                        _add_row(history_row)
                except Exception:
                    pass

            await _add_history_cache()

            async def _cached_possession_count():
                fixture_ids = list(rows_by_id.keys())
                if not fixture_ids:
                    return 0
                try:
                    cached_rows = await db.fixture_player_cache.find(
                        {
                            "_k": {
                                "$in": [f"fxt_poss_{fixture_id}" for fixture_id in fixture_ids]
                            }
                        },
                        {"_id": 0, "_k": 1, "d": 1},
                    ).to_list(len(fixture_ids))
                    return sum(
                        1
                        for cached_row in cached_rows
                        if isinstance((cached_row.get("d") or {}).get("home_poss"), (int, float))
                        and isinstance((cached_row.get("d") or {}).get("away_poss"), (int, float))
                    )
                except Exception:
                    return 0

            venue_fixture_count = sum(
                1 for row in rows_by_id.values()
                if not venue_filter or row.get("venue") == venue_filter
            )
            cached_possession_count = await _cached_possession_count()
            if (
                venue_filter
                and (
                    venue_fixture_count < _POSSESSION_SAMPLE_TARGET
                    or cached_possession_count < _POSSESSION_SAMPLE_TARGET
                )
            ):
                seasons = list(
                    range(CURRENT_SEASON - 1, CURRENT_SEASON - 5, -1)
                )

                async def _fetch_prior_season(season):
                    try:
                        return await api_football_request(
                            "fixtures",
                            {
                                "team": team_id,
                                "season": season,
                                "status": "FT",
                            },
                        ) or []
                    except Exception:
                        return []

                season_batches = await aio.gather(
                    *[_fetch_prior_season(season) for season in seasons],
                    return_exceptions=True,
                )
                for season_rows in season_batches:
                    if isinstance(season_rows, Exception):
                        continue
                    for season_row in season_rows:
                        _add_row(season_row)

            return _newest_first_rows(list(rows_by_id.values()), 100)

        async def _fetch_schedule_possession(
            base_rows,
            team_id,
            venue_filter,
        ):
            prepared_rows = await _prepare_possession_schedule_pool(
                base_rows,
                team_id,
                venue_filter,
            )
            return await fetch_team_possession_average(
                prepared_rows,
                team_id,
                20,
                venue_filter=venue_filter,
                required_sample=_POSSESSION_SAMPLE_TARGET,
            )

        # Filter opponent fixtures by their venue in THIS matchup (skipped for neutral)
        venue_filtered_opp_fixtures = (
            [] if _is_neutral else [f for f in opponent_fixture_list if f.get("venue") == opponent_venue]
        )

        # Wave 2: Use VENUE-FILTERED fixtures for possession evidence.
        # For neutral venue: use all fixtures (no venue preference).  Do not
        # silently fall back to the opposite venue when the requested sample
        # is short; the shortfall must remain visible to the caller.
        # Possession context is a team-level sample, not a player-history
        # sample. Use each club's completed schedule independently so a
        # player's minutes or non-appearance cannot change the club average.
        _team_possession_fixtures = (
            all_team_fixtures if _is_neutral else venue_filtered_team_fixtures
        )
        _opponent_possession_fixtures = (
            opponent_fixture_list if _is_neutral else venue_filtered_opp_fixtures
        )
        # Keep the explicit venue contract visible here: venue_filter=None if _is_neutral else player_venue.
        # The wrapper below expands the club schedule before applying that same filter.
        team_schedule_possession_task = _fetch_schedule_possession(
            _team_possession_fixtures,
            actual_team_id or 40,
            None if _is_neutral else player_venue,
        )
        # Keep the explicit venue contract visible here: venue_filter=None if _is_neutral else opponent_venue.
        opponent_schedule_possession_task = _fetch_schedule_possession(
            _opponent_possession_fixtures,
            req.opponentId,
            None if _is_neutral else opponent_venue,
        )

        _pressure_team_fixtures = (
            _newest_first_rows(venue_filtered_team_fixtures, _PRESSURE_SAMPLE_TARGET)
            if len(venue_filtered_team_fixtures) >= _PRESSURE_SAMPLE_TARGET
            else _newest_first_rows(all_team_fixtures, _PRESSURE_SAMPLE_TARGET)
        )
        _pressure_opponent_fixtures = (
            _newest_first_rows(venue_filtered_opp_fixtures, _PRESSURE_SAMPLE_TARGET)
            if len(venue_filtered_opp_fixtures) >= _PRESSURE_SAMPLE_TARGET
            else _newest_first_rows(opponent_fixture_list, _PRESSURE_SAMPLE_TARGET)
        )
        team_fixture_stats_task = fetch_fixture_team_stats(
            _pressure_team_fixtures,
            actual_team_id or 40,
            _PRESSURE_SAMPLE_TARGET,
        )
        opponent_fixture_stats_task = fetch_fixture_team_stats(
            _pressure_opponent_fixtures,
            req.opponentId,
            _PRESSURE_SAMPLE_TARGET,
        )
        # Matchup-volume evidence uses exact venue samples. Fetch both home
        # and away pools for both clubs so the UI can show, for example,
        # "PSG home SOT" and "Aston Villa away SOT" with no label inversion.
        # A venue is never padded with the opposite venue.
        team_home_volume_task = fetch_fixture_matchup_volume(
            [f for f in all_team_fixtures if f.get("venue") == "home"][:10],
            actual_team_id or 40,
            10,
        )
        team_away_volume_task = fetch_fixture_matchup_volume(
            [f for f in all_team_fixtures if f.get("venue") == "away"][:10],
            actual_team_id or 40,
            10,
        )
        opponent_home_volume_task = fetch_fixture_matchup_volume(
            [f for f in opponent_fixture_list if f.get("venue") == "home"][:10],
            req.opponentId,
            10,
        )
        opponent_away_volume_task = fetch_fixture_matchup_volume(
            [f for f in opponent_fixture_list if f.get("venue") == "away"][:10],
            req.opponentId,
            10,
        )
        async def fetch_historical_knockout_fixtures(team_id: int, venue: str | None) -> list[dict]:
            """Find verified same-venue elite knockout fixtures across seasons.

            The normal team feed is intentionally shallow (the latest 40
            fixtures). That is enough for current form, but not for a player
            such as Vitinha whose comparable Champions League knockout history
            spans several PSG seasons. Only finished, provider-labelled
            knockout fixtures are returned; player participation is still
            verified by fetch_player_game_logs.
            """
            if not team_id or req.sport != "soccer" or _is_neutral:
                return []
            try:
                from competition_context import (
                    ELITE_KNOCKOUT_COMPETITIONS,
                    KNOCKOUT_STAGES,
                    normalize_stage,
                )

                target_stage = normalize_stage((match_odds or {}).get("matchRound"))
                if target_stage not in KNOCKOUT_STAGES:
                    return []

                current_year = datetime.now(timezone.utc).year
                season_start = max(CURRENT_SEASON, current_year)
                seasons = list(range(season_start, season_start - 5, -1))

                async def _fetch_season(season: int):
                    try:
                        return await api_football_request(
                            "fixtures",
                            {"team": team_id, "season": season, "status": "FT"},
                        )
                    except Exception as _season_err:
                        print(
                            f"[HISTORY FIXTURES] {team_id}/{season} unavailable: "
                            f"{type(_season_err).__name__}"
                        )
                        return []

                season_batches = await aio.gather(
                    *[_fetch_season(season) for season in seasons],
                    return_exceptions=True,
                )
                candidates = []
                seen_ids = set()
                for batch in season_batches:
                    if isinstance(batch, Exception):
                        continue
                    for fixture in batch or []:
                        fixture_meta = fixture.get("fixture") or {}
                        teams = fixture.get("teams") or {}
                        league = fixture.get("league") or {}
                        fixture_id = fixture_meta.get("id")
                        league_id = league.get("id")
                        round_value = league.get("round") or ""
                        if (
                            not fixture_id
                            or fixture_id in seen_ids
                            or league_id not in ELITE_KNOCKOUT_COMPETITIONS
                            or normalize_stage(round_value) not in KNOCKOUT_STAGES
                        ):
                            continue
                        home_id = (teams.get("home") or {}).get("id")
                        fixture_venue = "home" if home_id == team_id else "away"
                        if venue and fixture_venue != venue:
                            continue
                        seen_ids.add(fixture_id)
                        candidates.append(fixture)

                candidates.sort(
                    key=lambda fixture: (fixture.get("fixture") or {}).get("date", ""),
                    reverse=True,
                )
                # Keep the request bounded while leaving enough room to
                # produce a 15+ player-appearance sample after DNPs.
                return candidates[:28]
            except Exception as _history_fixture_err:
                print(
                    f"[HISTORY FIXTURES] {team_id} lookup failed: "
                    f"{type(_history_fixture_err).__name__}: {_history_fixture_err}"
                )
                return []

        # The customer-facing archive must use the same effective venue as the
        # prediction. This is especially important for a market-labelled HOME
        # fixture whose provider metadata may describe a neutral/host venue
        # differently. The fixture's playerIsHome normalization above is the
        # single source of truth for this effective venue.
        historical_knockout_fixtures_task = fetch_historical_knockout_fixtures(
            actual_team_id,
            player_venue,
        )
        # Player game logs: VENUE-PRIORITIZED ordering
        # For neutral: use all fixtures equally (no venue priority — WC/tournament game)
        # For home/away: search venue-matching fixtures first. The deep player
        # history fetch targets 50 real appearances per venue before returning
        # a cache-only result.
        venue_first_fixtures = (
            all_team_fixtures if _is_neutral
            else venue_filtered_team_fixtures + [f for f in all_team_fixtures if f.get("venue") != player_venue]
        )
        async def _fetch_player_logs_with_history():
            # Start the optional multi-season knockout search in parallel, but
            # let the cache-first player loader finish without waiting for it.
            # If the cache already contains a usable core sample, cancel the
            # optional search and return the verified rows immediately.
            _history_task = aio.create_task(historical_knockout_fixtures_task)
            _initial_logs = await fetch_player_game_logs(
                venue_first_fixtures,
                req.playerId,
                100,
                extra_fixture_list=None,
            )
            if len(_initial_logs or []) >= _PREDICTION_CACHE_MIN:
                if not _history_task.done():
                    _history_task.cancel()
                try:
                    await _history_task
                except aio.CancelledError:
                    pass
                return _initial_logs

            try:
                historical_knockout_fixtures = await _history_task
            except Exception as _history_err:
                print(
                    f"[HISTORY FIXTURES] optional enrichment unavailable: "
                    f"{type(_history_err).__name__}"
                )
                historical_knockout_fixtures = []
            if not historical_knockout_fixtures:
                return _initial_logs
            return await fetch_player_game_logs(
                venue_first_fixtures,
                req.playerId,
                100,
                extra_fixture_list=historical_knockout_fixtures,
            )

        player_game_logs_task = _fetch_player_logs_with_history()

        # Position comparison task — same-position players vs this opponent
        # (started later after player_position is resolved)
        async def _empty_list():
            return []
        # =============================================
        # BUILD STRUCTURED DATA DIGEST (no AI needed — pure code extraction)
        # =============================================
        def build_data_digest():
            """Build a compact data digest directly from raw API data — no AI summarization needed."""
            parts = []

            # 1. Player basics
            if player_stats:
                pstats = player_stats.get("statistics", [{}])[0] if player_stats.get("statistics") else {}
                games_data = pstats.get("games", {})
                passes = pstats.get("passes", {})
                shots = pstats.get("shots", {})
                tackles = pstats.get("tackles", {})
                goals = pstats.get("goals", {})
                dribbles = pstats.get("dribbles", {})
                fouls = pstats.get("fouls", {})
                parts.append(f"""[PLAYER PROFILE]
- Position: {games_data.get('position', 'Unknown')} | Apps: {games_data.get('appearences', 'N/A')} | Avg Rating: {games_data.get('rating', 'N/A')}
- Avg Minutes: {(games_data.get('minutes') or 0) / max((games_data.get('appearences') or 1), 1):.0f} per game
- Passes: total={passes.get('total','N/A')}, key={passes.get('key','N/A')}, accuracy={passes.get('accuracy','N/A')}%
- Shots: total={shots.get('total','N/A')}, on_target={shots.get('on','N/A')}
- Tackles: total={tackles.get('total','N/A')}, interceptions={tackles.get('interceptions','N/A')}, blocks={tackles.get('blocks','N/A')}
- Saves: {goals.get('saves','N/A')} | Dribbles: attempts={dribbles.get('attempts','N/A')}, success={dribbles.get('success','N/A')}
- Fouls drawn: {fouls.get('drawn','N/A')}""")

            # 2. Team stats (venue-specific; for neutral use overall totals)
            if team_stats:
                fixtures = team_stats.get("fixtures", {})
                goals_for = team_stats.get("goals", {}).get("for", {}).get("total", {})
                goals_against = team_stats.get("goals", {}).get("against", {}).get("total", {})
                _pv_label = "OVERALL" if _is_neutral else player_venue.upper()
                _pv_key   = None if _is_neutral else player_venue  # None → fall back gracefully
                _gf_val   = sum(goals_for.values()) if _is_neutral else goals_for.get(player_venue, "N/A")
                _ga_val   = sum(goals_against.values()) if _is_neutral else goals_against.get(player_venue, "N/A")
                _w = sum(fixtures.get("wins", {}).values()) if _is_neutral else fixtures.get("wins", {}).get(player_venue, "N/A")
                _d = sum(fixtures.get("draws", {}).values()) if _is_neutral else fixtures.get("draws", {}).get(player_venue, "N/A")
                _l = sum(fixtures.get("loses", {}).values()) if _is_neutral else fixtures.get("loses", {}).get(player_venue, "N/A")
                parts.append(f"""[TEAM {_pv_label} PROFILE]
- Record: W{_w} D{_d} L{_l}
- Goals For: {_gf_val} | Against: {_ga_val}""")

            # 3. Opponent stats (opposite venue; for neutral use overall totals)
            if opponent_stats:
                opp_fix = opponent_stats.get("fixtures", {})
                opp_gf = opponent_stats.get("goals", {}).get("for", {}).get("total", {})
                opp_ga = opponent_stats.get("goals", {}).get("against", {}).get("total", {})
                _ov_label = "OVERALL" if _is_neutral else opponent_venue.upper()
                _ogf_val  = sum(opp_gf.values()) if _is_neutral else opp_gf.get(opponent_venue, "N/A")
                _oga_val  = sum(opp_ga.values()) if _is_neutral else opp_ga.get(opponent_venue, "N/A")
                _ow = sum(opp_fix.get("wins", {}).values()) if _is_neutral else opp_fix.get("wins", {}).get(opponent_venue, "N/A")
                _od = sum(opp_fix.get("draws", {}).values()) if _is_neutral else opp_fix.get("draws", {}).get(opponent_venue, "N/A")
                _ol = sum(opp_fix.get("loses", {}).values()) if _is_neutral else opp_fix.get("loses", {}).get(opponent_venue, "N/A")
                parts.append(f"""[OPPONENT {_ov_label} PROFILE]
- Record: W{_ow} D{_od} L{_ol}
- Goals For: {_ogf_val} | Against: {_oga_val}""")

            # 4. H2H
            if h2h_data:
                h2h_lines = []
                for h in h2h_data[:5]:
                    h2h_lines.append(f"  {h.get('date', '')[:10]}: {h.get('homeTeam', '')} {h.get('homeGoals', 0)}-{h.get('awayGoals', 0)} {h.get('awayTeam', '')}")
                parts.append(f"[H2H ({len(h2h_data)} matches)]\n" + "\n".join(h2h_lines))

            # 5. Standings
            if standings:
                standing_lines = [f"  {s.get('rank','')}. {s.get('team','')} — {s.get('points','')}pts (GD: {s.get('goalsDiff','')})" for s in standings[:8]]
                parts.append("[STANDINGS]\n" + "\n".join(standing_lines))

            # 6. Odds & Game Type
            if match_odds and match_odds.get("bookmakerOdds"):
                bo = match_odds["bookmakerOdds"]
                ao = match_odds.get("americanOdds", {})
                gt = match_odds.get("gameType", "")
                if ao:
                    parts.append(f"""[MONEYLINE & GAME TYPE]
- Home ({ao.get('home', '')}) | Draw ({ao.get('draw', '')}) | Away ({ao.get('away', '')})
- Favorite: {match_odds.get('favorite', 'Unknown').upper()}
- Game Type: {gt}
>>> Moneyline tells you expected game flow. Heavy favorites control possession and tempo. Underdogs may sit deep (deflating pass/shot stats for attacker props). CRITICAL FOR GOALKEEPERS: GK pass volume is INVERTED — a team sitting deep and defending (low possession) produces MORE back-passes to the GK, not fewer. An away GK protecting a lead is the highest-volume scenario for GK passes. A GK on a dominant possession team sees FEWER back-passes. <<<""")
                else:
                    parts.append(f"""[ODDS]
- Home: {bo.get('homeWin', 'N/A')} | Draw: {bo.get('draw', 'N/A')} | Away: {bo.get('awayWin', 'N/A')}
- Favorite: {match_odds.get('favorite', 'Unknown').upper()}""")

            return "\n\n".join(parts)

        data_digest = build_data_digest()

        # =============================================
        # MATCH DOMINANCE ENGINE: Calculate expected possession & context multiplier
        # Uses opponent-aware formula + odds adjustment for accurate matchup prediction
        # =============================================
        match_dominance = {
            "expectedPoss": 50.0,
            "oppExpectedPoss": 50.0,
            "multiplier": 1.0,
            "notes": [],
            "seasonAvgIsReal": False,
            "hasRealPossData": False,
        }

        # Wave 2: Fetch deep fixture data + Situation Engine in parallel.
        from situation_engine import build_game_situation

        # Situation engine inputs
        # Use the fixture's canonical home/away assignment (from match_odds) when available,
        # just like we do for possession/moneyline/team labels. This ensures the situation
        # engine (knockout aggregate, home/away multipliers) also sees correct orientation.
        _sit_pih = (match_odds or {}).get("playerIsHome")
        _canonical_team_id = (match_odds or {}).get("fixtureTeamId") or actual_team_id
        _canonical_opponent_id = (match_odds or {}).get("fixtureOpponentId") or req.opponentId
        _canonical_team_name = (
            (match_odds or {}).get("fixtureTeamName")
            or corrected_team_name
            or req.teamName
        )
        _canonical_opponent_name = (
            (match_odds or {}).get("fixtureOpponentName")
            or req.opponentName
        )
        _sit_is_home = bool(_sit_pih) if _sit_pih is not None else (player_venue == "home")
        _sit_home_id = _canonical_team_id if _sit_is_home else _canonical_opponent_id
        _sit_away_id = _canonical_opponent_id if _sit_is_home else _canonical_team_id
        _sit_match_round = (match_odds or {}).get("matchRound", "")
        _sit_match_league = (match_odds or {}).get("matchLeague", "")
        _sit_match_date = (match_odds or {}).get("matchDate", "")
        _sit_fixture_id = (match_odds or {}).get("fixtureId")

        # Use the fixture's actual competition league_id (e.g. Europa League = 3),
        # not the player's domestic league. Domestic league_id breaks H2H lookup
        # for European ties (e.g. Braga in Europa League vs Primeira Liga = 94).
        _sit_fixture_league_id = (match_odds or {}).get("matchLeagueId") or league_id or 39
        situation_task = build_game_situation(
            home_team_id=_sit_home_id,
            away_team_id=_sit_away_id,
            is_player_home=_sit_is_home,
            league_id=_sit_fixture_league_id,
            match_round=_sit_match_round,
            fixture_id=_sit_fixture_id,
            player_team_name=_canonical_team_name or "",
            opponent_name=_canonical_opponent_name or "",
            prop_type=req.propType,
            standings=standings,
            player_team_id=_canonical_team_id or req.teamId,
            opponent_id=_canonical_opponent_id,
        )
        statsbomb_task = (
            _fetch_statsbomb_enrichment(
                db,
                fixture_id=_sit_fixture_id,
                league_id=_sit_fixture_league_id,
                league_name=_sit_match_league or "",
                team_name=_canonical_team_name or "",
                opponent_name=_canonical_opponent_name or "",
                match_date=_sit_match_date,
                player_name=req.playerName,
            )
            if req.sport == "soccer"
            else aio.sleep(
                0,
                result={
                    "available": False,
                    "status": "not_applicable",
                    "provider": "statsbomb_open_data",
                    "shadowOnly": True,
                    "reason": "StatsBomb Open Data is football-only.",
                },
            )
        )

        # Required projection inputs and optional shadow providers have
        # different latency contracts. Keep API-Football player logs, team
        # stats, and the situation engine together so the deterministic
        # projection sees the same evidence as before. StatsBomb is optional
        # explanation-only enrichment and must not hold the result hostage.
        async def _bounded_required(coro, label: str, timeout: float):
            try:
                return await aio.wait_for(coro, timeout=timeout)
            except Exception as exc:
                print(
                    f"[WAVE2 SOURCE] {label} unavailable after {timeout:.0f}s: "
                    f"{type(exc).__name__}"
                )
                return None

        # Bound sources independently. A slow team-level enrichment must not
        # cancel the player's game logs, which are the primary Bayesian prior.
        required_wave2 = aio.gather(
            _bounded_required(team_fixture_stats_task, "team fixture stats", 10),
            _bounded_required(opponent_fixture_stats_task, "opponent fixture stats", 10),
            _bounded_required(player_game_logs_task, "player game logs", 12),
            _bounded_required(situation_task, "match situation", 8),
            _bounded_required(
                team_schedule_possession_task,
                "team schedule possession",
                12,
            ),
            _bounded_required(
                opponent_schedule_possession_task,
                "opponent schedule possession",
                12,
            ),
            return_exceptions=True,
        )
        matchup_volume_wave = aio.gather(
            _bounded_required(team_home_volume_task, "team home volume", 6),
            _bounded_required(team_away_volume_task, "team away volume", 6),
            _bounded_required(opponent_home_volume_task, "opponent home volume", 6),
            _bounded_required(opponent_away_volume_task, "opponent away volume", 6),
            return_exceptions=True,
        )
        optional_wave2 = aio.gather(statsbomb_task, return_exceptions=True)
        # Each required source already has its own timeout above. Do not wrap
        # the gather in a second outer timeout: cancelling the gather here
        # discards completed player-history rows when one sibling provider
        # request is slow. The projection can use the partial result set and
        # represent any missing source as unavailable.
        try:
            required_results = await required_wave2
        except Exception as _required_wave_err:
            required_results = [None] * 6
            print(
                f"[WAVE2 TIMEOUT] required API-Football sources failed for "
                f"{req.playerName}: {type(_required_wave_err).__name__}"
            )

        try:
            matchup_volume_results = await aio.wait_for(matchup_volume_wave, timeout=6)
        except aio.TimeoutError:
            matchup_volume_results = [None] * 4
            print(f"[MATCHUP VOLUME TIMEOUT] venue evidence exceeded 6s for {req.playerName}")

        try:
            optional_results = await aio.wait_for(optional_wave2, timeout=3)
        except aio.TimeoutError:
            optional_results = [None]
            print(f"[WAVE2 OPTIONAL TIMEOUT] shadow enrichment exceeded 3s for {req.playerName}")

        required_results = required_results + matchup_volume_results
        team_fixture_stats = required_results[0] if not isinstance(required_results[0], (Exception, type(None))) else []
        opponent_fixture_stats = required_results[1] if not isinstance(required_results[1], (Exception, type(None))) else []
        player_game_logs = required_results[2] if not isinstance(required_results[2], (Exception, type(None))) else []
        game_situation = required_results[3] if len(required_results) > 3 and not isinstance(required_results[3], (Exception, type(None))) else {}
        team_schedule_possession = (
            required_results[4]
            if len(required_results) > 4
            and not isinstance(required_results[4], (Exception, type(None)))
            else {"average": None, "sampleSize": 0, "fixtureIds": [], "source": None}
        )
        opponent_schedule_possession = (
            required_results[5]
            if len(required_results) > 5
            and not isinstance(required_results[5], (Exception, type(None)))
            else {"average": None, "sampleSize": 0, "fixtureIds": [], "source": None}
        )
        def _wave_rows(index):
            value = required_results[index] if len(required_results) > index else None
            return value if not isinstance(value, (Exception, type(None))) else []

        team_matchup_volume_rows = _wave_rows(6) + _wave_rows(7)
        opponent_matchup_volume_rows = _wave_rows(8) + _wave_rows(9)
        # If the dedicated venue-sample wave returned no rows, reuse the
        # exact-fixture side totals already hydrated onto player logs. This
        # keeps pass/SOT evidence visible during provider throttling without
        # inventing team totals or changing the projection.
        _fallback_team_rows = []
        _fallback_opponent_rows = []
        for _gl in player_game_logs or []:
            if not isinstance(_gl, dict) or not (_gl.get("_fid") or _gl.get("fixtureId")):
                continue
            _base = {
                "fixtureId": _gl.get("_fid") or _gl.get("fixtureId"),
                "date": _gl.get("date", ""),
                "opponent": _gl.get("opponent", ""),
                "venue": _gl.get("venue"),
                "teamShotsOnTarget": _gl.get("teamShotsOnTarget"),
                "opponentShotsOnTarget": _gl.get("opponentShotsOnTarget"),
                "teamPasses": _gl.get("teamPassAttempts"),
                "opponentPasses": _gl.get("opponentPassAttempts"),
                "source": "exact_player_fixture_context",
            }
            if any(
                _base.get(field) is not None
                for field in ("teamShotsOnTarget", "teamPasses")
            ):
                _fallback_team_rows.append(_base)
            if any(
                _base.get(field) is not None
                for field in ("opponentShotsOnTarget", "opponentPasses")
            ):
                _fallback_opponent_rows.append({
                    **_base,
                    "venue": "away" if _gl.get("venue") == "home" else "home",
                    "teamShotsOnTarget": _base.get("opponentShotsOnTarget"),
                    "opponentShotsOnTarget": _base.get("teamShotsOnTarget"),
                    "teamPasses": _base.get("opponentPasses"),
                    "opponentPasses": _base.get("teamPasses"),
                })
        if not team_matchup_volume_rows:
            team_matchup_volume_rows = _fallback_team_rows
        if not opponent_matchup_volume_rows:
            opponent_matchup_volume_rows = _fallback_opponent_rows
        matchup_volume = build_matchup_volume_packet(
            player_venue=player_venue,
            team_rows=team_matchup_volume_rows,
            opponent_rows=opponent_matchup_volume_rows,
            team_name=corrected_team_name or req.teamName,
            opponent_name=_canonical_opponent_name or req.opponentName,
            player_logs=player_game_logs,
        )
        print(
            f"[MATCHUP VOLUME RESULT] {req.playerName}/{req.propType}: "
            f"available={matchup_volume.get('available')} "
            f"team_rows={len(team_matchup_volume_rows)} "
            f"opponent_rows={len(opponent_matchup_volume_rows)} "
            f"home_sot_n={matchup_volume['fixtureSplits']['home']['sotCreated'].get('sampleSize', 0)} "
            f"away_sot_n={matchup_volume['fixtureSplits']['away']['sotCreated'].get('sampleSize', 0)} "
            f"home_pass_n={matchup_volume['fixtureSplits']['home']['passesCreated'].get('sampleSize', 0)} "
            f"away_pass_n={matchup_volume['fixtureSplits']['away']['passesCreated'].get('sampleSize', 0)}"
        )
        # Carry the exact opponent team totals onto the player-history rows
        # when the fixture identity/date joins. This keeps the recent-match
        # chart auditable without making the player stat equal to the team
        # stat, and leaves missing provider values unavailable.
        _volume_by_fixture = {
            str(row.get("fixtureId")): row
            for row in team_matchup_volume_rows
            if isinstance(row, dict) and row.get("fixtureId") is not None
        }
        _volume_by_date = {
            str(row.get("date"))[:10]: row
            for row in team_matchup_volume_rows
            if isinstance(row, dict) and row.get("date")
        }
        for _gl in player_game_logs or []:
            _volume_row = _volume_by_fixture.get(
                str(_gl.get("_fid") or _gl.get("fixtureId"))
            )
            if _volume_row is None and _gl.get("date"):
                _volume_row = _volume_by_date.get(str(_gl.get("date"))[:10])
            if _volume_row:
                if _volume_row.get("opponentShotsOnTarget") is not None:
                    _gl["opponentShotsOnTarget"] = _volume_row["opponentShotsOnTarget"]
                if _volume_row.get("opponentPasses") is not None:
                    _gl["opponentPassAttempts"] = _volume_row["opponentPasses"]
        statsbomb_enrichment = (
            optional_results[0]
            if len(optional_results) > 0 and not isinstance(optional_results[0], (Exception, type(None)))
            else {
                "available": False,
                "status": "unavailable",
                "provider": "statsbomb_open_data",
                "shadowOnly": True,
                "reason": "StatsBomb enrichment did not complete.",
            }
        )
        if statsbomb_enrichment.get("available"):
            _sb_metrics = statsbomb_enrichment.get("eventMetrics") or {}
            print(
                f"[STATSBOMB] covered match="
                f"{(statsbomb_enrichment.get('match') or {}).get('statsBombMatchId')} "
                f"ppda={_sb_metrics.get('ppda')} "
                f"pressures={_sb_metrics.get('pressureEvents')}"
            )

        # Recent-match opponent pressure/block profiles are optional and
        # cache-first. They never hold the player-stat prior hostage and they
        # never change the deterministic projection. The shield lets any
        # uncached warming continue after the bounded response window so later
        # predictions can return more verified rows.
        _recent_profile_stat_fields = {
            "goals": "goals_total", "assists": "goals_assists",
            "shots_assisted": "passes_key", "pass_attempts": "passes_total",
            "passes": "passes_total", "shots": "shots_total",
            "shots_on_target": "shots_on", "tackles": "tackles_total",
            "key_passes": "passes_key", "saves": "goals_saves",
            "goalie_saves": "goals_saves", "interceptions": "tackles_interceptions",
            "blocks": "tackles_blocks", "dribbles": "dribbles_attempts",
            "fouls_drawn": "fouls_drawn", "fouls_committed": "fouls_committed",
            "crosses": "passes_crosses", "clearances": "tackles_clearances",
            "duels_won": "duels_won",
        }
        _recent_profile_rows = []
        _recent_profile_field = _recent_profile_stat_fields.get(req.propType)
        for _recent_log in player_game_logs or []:
            if not isinstance(_recent_log, dict):
                continue
            if not (_recent_log.get("_fid") or _recent_log.get("fixtureId")):
                continue
            _recent_row = dict(_recent_log)
            _recent_row["value"] = (
                _recent_log.get(_recent_profile_field)
                if _recent_profile_field
                else None
            )
            _recent_profile_rows.append(_recent_row)
        _recent_block_profile_task = aio.create_task(
            _fetch_recent_opponent_block_profiles(
                db,
                _recent_profile_rows,
                league_id=req.leagueId,
                league_name=_sit_match_league or "",
                team_name=corrected_team_name or req.teamName,
                player_name=req.playerName,
                limit=_RECENT_ARCHIVE_TARGET,
                max_network_matches=12,
            )
        )
        _recent_opponent_press_task = aio.create_task(
            fetch_recent_opponent_press_intensity(
                _recent_profile_rows,
                limit=_RECENT_ARCHIVE_TARGET,
                max_network_matches=50,
            )
        )
        if _prediction_elapsed() < 20.0:
            try:
                recent_block_profiles = await aio.wait_for(
                    aio.shield(_recent_block_profile_task),
                    timeout=1.5,
                )
            except Exception as _recent_profile_err:
                print(
                    f"[RECENT BLOCK PROFILE] bounded response window: "
                    f"{type(_recent_profile_err).__name__}"
                )
                recent_block_profiles = {
                    "status": "warming",
                    "available": False,
                    "profiles": [],
                    "sampleSize": len(_recent_profile_rows),
                    "verifiedMatches": 0,
                    "ppdaMatches": 0,
                    "formationMatches": 0,
                    "source": "StatsBomb Open Data + API-Football fixture lineups",
                    "projectionInfluence": "explanation_only",
                    "shadowWeighting": {
                        "status": "shadow_only",
                        "projectionAdjustment": 0.0,
                    },
                    "limitations": [
                        "Recent opponent profiles are still warming from cache/provider.",
                        "Missing coverage is unavailable, not a guessed block.",
                    ],
                }
        else:
            _recent_block_profile_task.cancel()
            print(
                f"[RECENT BLOCK PROFILE] skipped after {_prediction_elapsed():.1f}s "
                "to protect the core prediction response window"
            )
            recent_block_profiles = {
                "status": "skipped",
                "available": False,
                "profiles": [],
                "sampleSize": len(_recent_profile_rows),
                "verifiedMatches": 0,
                "ppdaMatches": 0,
                "formationMatches": 0,
                "source": "StatsBomb Open Data + API-Football fixture lineups",
                "projectionInfluence": "explanation_only",
                "shadowWeighting": {
                    "status": "shadow_only",
                    "projectionAdjustment": 0.0,
                },
                "limitations": [
                    "Recent opponent profiles were skipped to keep the core prediction responsive.",
                    "Missing coverage is unavailable, not a guessed block.",
                ],
            }

        try:
            # Do not serialize the new packet as "not yet warmed" after a
            # 1.5-second placeholder window. The caller needs the actual
            # exact-fixture classification or a real provider limitation.
            recent_opponent_press_intensity = await aio.wait_for(
                aio.shield(_recent_opponent_press_task),
                timeout=20.0,
            )
        except Exception as _recent_press_err:
            print(
                f"[RECENT PRESS INTENSITY] bounded response window: "
                f"{type(_recent_press_err).__name__}"
            )
            recent_opponent_press_intensity = {
                "status": "limited",
                "available": False,
                "sampleSize": len(_recent_profile_rows),
                "verifiedMatches": 0,
                "source": "API-Football fixture statistics + fixture player defensive actions",
                "projectionInfluence": "explanation_only",
                "profiles": [
                    {
                        "fixtureId": row.get("_fid") or row.get("fixtureId"),
                        "date": row.get("date"),
                        "opponent": row.get("opponent"),
                        "venue": row.get("venue"),
                        "pressIntensity": {
                            "available": False,
                            "status": "limited",
                            "score": None,
                            "score100": None,
                            "label": "Limited",
                            "source": "api_football",
                            "sampleSize": 0,
                            "sampleStatus": "unavailable",
                            "reason": f"pressure_fetch_{type(_recent_press_err).__name__}",
                        },
                        "status": "limited",
                        "verified": False,
                        "reason": f"pressure_fetch_{type(_recent_press_err).__name__}",
                    }
                    for row in _recent_profile_rows
                ],
                "limitations": [
                    "Exact-fixture defensive-action enrichment exceeded the bounded provider window.",
                    "No pressure score was guessed from possession, odds, or the aggregate matchup packet.",
                ],
            }

        # Keep the existing block-profile packet backward compatible while
        # making the exact API-Football pressure packet available to the same
        # fixture row in older saved-pick renderers.
        _press_by_fixture = {
            str(profile.get("fixtureId")): profile
            for profile in (recent_opponent_press_intensity.get("profiles") or [])
            if isinstance(profile, dict) and profile.get("fixtureId") is not None
        }
        for _block_profile in recent_block_profiles.get("profiles") or []:
            if not isinstance(_block_profile, dict):
                continue
            _press_profile = _press_by_fixture.get(str(_block_profile.get("fixtureId")))
            if _press_profile:
                _block_profile["pressIntensity"] = _press_profile.get("pressIntensity")
                _block_profile["pressIntensityStatus"] = _press_profile.get("status")
        for _history_log in player_game_logs or []:
            if not isinstance(_history_log, dict):
                continue
            _history_fid = str(
                _history_log.get("_fid") or _history_log.get("fixtureId") or ""
            )
            _history_press = _press_by_fixture.get(_history_fid)
            if _history_press:
                _history_log["pressIntensity"] = _history_press.get("pressIntensity")
                _history_log["pressIntensityStatus"] = _history_press.get("status")

        # ── Await manager task (nearly instant on cache hit, <1 API call/7 days) ───
        _manager_ctx = {}
        _manager_possession_drift = {}
        if _manager_task is not None:
            try:
                _manager_ctx = await _manager_task or {}
                if _manager_ctx.get("isRecent"):
                    print(
                        f"[MANAGER] ⚠ Recent change: {_manager_ctx.get('prevCoachName','?')} → "
                        f"{_manager_ctx.get('coachName','?')} "
                        f"({_manager_ctx.get('daysElapsed')}d ago, start={_manager_ctx.get('coachStartDate')})"
                    )
                else:
                    print(
                        f"[MANAGER] {_manager_ctx.get('coachName', 'unknown')} "
                        f"(stable, {_manager_ctx.get('daysElapsed','?')}d)"
                    )
            except Exception as _mgr_err:
                print(f"[MANAGER] await error: {_mgr_err}")

        # ── Possession drift: last-5 vs season average for tactical-shift detection ──
        if team_fixture_stats:
            try:
                from manager_tracker import compute_possession_drift as _cpd
                _manager_possession_drift = _cpd(team_fixture_stats) or {}
                if _manager_possession_drift.get("isShift"):
                    print(
                        f"[MANAGER POSS DRIFT] {req.teamName}: "
                        f"season={_manager_possession_drift['seasonAvg']}% → "
                        f"last5={_manager_possession_drift['last5Avg']}% "
                        f"({_manager_possession_drift['drift']:+.1f}pp) ⚠ TACTICAL SHIFT"
                    )
            except Exception as _pd_err:
                print(f"[MANAGER POSS DRIFT] error: {_pd_err}")
        if not game_situation:
            game_situation = {"isKnockout": False, "isSecondLeg": False, "aggregate": {}, "multipliers": {}, "injuries": {}, "contextBlock": ""}

        # =============================================
        # BDL SOCCER STAGE: For BDL-covered leagues (EPL, La Liga, Serie A, Bundesliga,
        # Ligue 1, UCL, MLS, World Cup) try BDL as the PRIMARY source — no daily quota.
        # Runs even when the fixture cache already has API-Football logs: if the BDL
        # quality gate passes (≥3 games with the target stat populated), BDL overrides
        # the fixture cache and the PLAYER-DIRECT stage below is skipped.  When the
        # quality gate fails (Tier-2 stat like passes_total not yet available in BDL),
        # player_game_logs retains fixture-cache data; PLAYER-DIRECT still runs if empty.
        # =============================================
        if _is_bdl_league and _bdl_soc.is_bdl_league(league_id) and req.playerName:
            _bdl_stat_field_map = {
                "goals": "goals_total", "assists": "goals_assists",
                "shots_assisted": "passes_key", "pass_attempts": "passes_total",
                "passes": "passes_total", "shots": "shots_total",
                "shots_on_target": "shots_on", "tackles": "tackles_total",
                 "key_passes": "passes_key", "saves": "goals_saves",
                 "goalie_saves": "goals_saves",
                "interceptions": "tackles_interceptions", "blocks": "tackles_blocks",
                "dribbles": "dribbles_attempts", "fouls_drawn": "fouls_drawn",
                "fouls_committed": "fouls_committed", "crosses": "passes_crosses",
                "clearances": "tackles_clearances", "duels_won": "duels_won",
                "yellow_cards": "cards_yellow",
            }
            _bdl_gl_key = _bdl_stat_field_map.get(req.propType, "passes_total")
            try:
                _bdl_logs, _bdl_pid = await _bdl_soc.get_game_logs(
                    league_id, req.playerName, last_n=_RECENT_ARCHIVE_TARGET
                )
                if _bdl_logs:
                    # Quality gate: only adopt BDL logs when the target stat
                    # field is actually populated (BDL tier-2 stats like
                    # passes_total / tackles are often None for new seasons).
                    # If fewer than 3 logs have data for this prop, fall
                    # through to the API-Football PLAYER-DIRECT stage instead.
                    _useful = sum(
                        1 for _g in _bdl_logs if _g.get(_bdl_gl_key) is not None
                    )
                    if _useful >= 3:
                        # Add per-90 for the target stat where possible
                        for _g in _bdl_logs:
                            _mins = _g.get("minutes") or 0
                            _sval = _g.get(_bdl_gl_key)
                            if _sval is not None and _mins > 0:
                                _g["targetStatPer90"] = round((_sval / _mins) * 90, 2)
                        player_game_logs = _bdl_logs
                        print(f"[BDL-SOCCER] {req.playerName}/{req.propType}: "
                              f"{len(_bdl_logs)} logs, {_useful} with {_bdl_gl_key} "
                              f"(league {league_id})")
                    else:
                        print(f"[BDL-SOCCER] {req.playerName}/{req.propType}: "
                              f"only {_useful}/3 logs have '{_bdl_gl_key}' data — "
                              f"using cached game logs (no API-Football fallback)")
            except Exception as _bdl_err:
                print(f"[BDL-SOCCER] Error for {req.playerName}: {_bdl_err}")


        # =============================================
        # PLAYER-DIRECT API FALLBACK: When fixture cache misses, fetch the player's
        # recent fixtures directly from the API by player ID — no team cache needed.
        # Skipped for BDL leagues — BDL is the sole source, no API-Football fallback.
        # =============================================
        if not player_game_logs and req.playerId and not _is_bdl_league:
            _gl_field_map2 = {
                "goals": "goals_total", "assists": "goals_assists",
                "shots_assisted": "passes_key", "pass_attempts": "passes_total",
                "passes": "passes_total", "shots": "shots_total",
                "shots_on_target": "shots_on", "tackles": "tackles_total",
                 "key_passes": "passes_key", "saves": "goals_saves",
                 "goalie_saves": "goals_saves",
                "interceptions": "tackles_interceptions", "blocks": "tackles_blocks",
                "dribbles": "dribbles_attempts", "fouls_drawn": "fouls_drawn",
                "fouls_committed": "fouls_committed", "crosses": "passes_crosses",
                "clearances": "tackles_clearances", "duels_won": "duels_won",
                "yellow_cards": "cards_yellow",
            }
            _stat_key_map2 = {
                "goals": ("goals", "total"), "assists": ("goals", "assists"),
                "shots_assisted": ("passes", "key"), "pass_attempts": ("passes", "total"),
                "passes": ("passes", "total"), "shots": ("shots", "total"),
                "shots_on_target": ("shots", "on"), "tackles": ("tackles", "total"),
                "key_passes": ("passes", "key"), "saves": ("goals", "saves"),
                "interceptions": ("tackles", "interceptions"), "blocks": ("tackles", "blocks"),
                "dribbles": ("dribbles", "attempts"), "fouls_drawn": ("fouls", "drawn"),
                "fouls_committed": ("fouls", "committed"), "crosses": ("passes", "cross"),
                "clearances": ("tackles", "clearances"), "duels_won": ("duels", "won"),
                "yellow_cards": ("cards", "yellow"),
            }
            _gl_key2 = _gl_field_map2.get(req.propType, "passes_total")

            # Stage 1: Pull the player's recent club fixtures from API-Football.
            # API-Football's /fixtures endpoint does not support a `player`
            # parameter (it returns "The Player field do not exist"). Use the
            # verified club team instead, then keep the exact player-ID match
            # inside each fixture below. This is the same identity-safe route
            # used by the primary team-history loader, but remains a bounded
            # recovery path when its cache is unavailable.
            try:
                if not actual_team_id:
                    print(
                        f"[PLAYER-DIRECT] {req.playerName}: missing verified teamId; "
                        "cannot fetch club fixtures"
                    )
                    _player_fixtures_raw = []
                else:
                    print(
                        f"[PLAYER-DIRECT] {req.playerName}: fetching recent club "
                        f"fixtures by verified teamId={actual_team_id} "
                        f"(playerId={req.playerId})"
                    )
                    try:
                        _player_fixtures_raw = await aio.wait_for(
                            api_football_request(
                                "fixtures",
                        {"team": actual_team_id, "last": 40, "status": "FT"},
                            ),
                            timeout=5,
                        )
                    except aio.TimeoutError:
                        print(f"[PLAYER-DIRECT] {req.playerName}: club fixtures timed out after 5s")
                        _player_fixtures_raw = []
                if _player_fixtures_raw and actual_team_id:
                    # Keep the explicit team-ID guard even though the provider
                    # was queried by team. It protects against stale or
                    # malformed cached responses being reused here.
                    _player_fixtures_raw = [
                        fx for fx in _player_fixtures_raw
                        if (
                            (fx.get("teams", {}).get("home", {}).get("id") == actual_team_id)
                            or (fx.get("teams", {}).get("away", {}).get("id") == actual_team_id)
                        )
                    ]
                if _player_fixtures_raw and _is_wc:
                    print(
                        f"[WC MODE] {req.playerName}: using {len(_player_fixtures_raw)} "
                        "verified club fixtures as the player-stat prior"
                    )

                if _player_fixtures_raw:
                    # For each fixture, fetch per-game stats
                    _sem2 = aio.Semaphore(10)

                    async def _direct_fixture_possession(fid, home_id, away_id):
                        """Fetch exact home/away possession for a direct-player fixture."""
                        cache_key = f"fxt_poss_{fid}"
                        try:
                            cached = await db.fixture_player_cache.find_one(
                                {"_k": cache_key}, {"_id": 0, "d": 1}
                            )
                            cached_data = (cached or {}).get("d") or {}
                            if (
                                cached_data.get("home_poss") is not None
                                and cached_data.get("away_poss") is not None
                            ):
                                return (
                                    float(cached_data["home_poss"]),
                                    float(cached_data["away_poss"]),
                                )
                        except Exception:
                            pass
                        try:
                            stats_rows = await api_football_request(
                                "fixtures/statistics", {"fixture": fid}
                            )
                            home_poss = away_poss = None
                            for team_stats in stats_rows or []:
                                team_id = (team_stats.get("team") or {}).get("id")
                                for stat in team_stats.get("statistics") or []:
                                    if stat.get("type") != "Ball Possession":
                                        continue
                                    raw = str(stat.get("value") or "").replace("%", "").strip()
                                    try:
                                        value = float(raw)
                                    except (TypeError, ValueError):
                                        continue
                                    if team_id == home_id:
                                        home_poss = value
                                    elif team_id == away_id:
                                        away_poss = value
                            if home_poss is None or away_poss is None:
                                return None, None
                            try:
                                await db.fixture_player_cache.update_one(
                                    {"_k": cache_key},
                                    {"$set": {"_k": cache_key, "d": {
                                        "home_poss": home_poss,
                                        "away_poss": away_poss,
                                    }}},
                                    upsert=True,
                                )
                            except Exception as cache_err:
                                print(f"[PLAYER-DIRECT TP CACHE] skipped: {cache_err}")
                            return home_poss, away_poss
                        except Exception as poss_err:
                            print(
                                f"[PLAYER-DIRECT TP] fixture={fid} unavailable: "
                                f"{type(poss_err).__name__}"
                            )
                            return None, None

                    async def _fetch_player_fix_stats(fix_raw):
                        try:
                            fid = fix_raw.get("fixture", {}).get("id")
                            if not fid:
                                return None
                            home_team_id = fix_raw.get("teams", {}).get("home", {}).get("id")
                            player_fix_venue = "home" if home_team_id == actual_team_id else "away"
                            fix_date = fix_raw.get("fixture", {}).get("date", "")[:10]
                            fix_league = fix_raw.get("league", {}).get("name", "")
                            fix_round = fix_raw.get("league", {}).get("round", "")
                            fix_opp_key = "away" if home_team_id == actual_team_id else "home"
                            fix_opponent = fix_raw.get("teams", {}).get(fix_opp_key, {}).get("name", "")
                            home_goals = fix_raw.get("goals", {}).get("home", 0) or 0
                            away_goals = fix_raw.get("goals", {}).get("away", 0) or 0

                            # Check cache first
                            ck = f"fxp_{fid}_{req.playerId}"
                            cached_doc = await db.fixture_player_cache.find_one({"_k": ck}, {"_id": 0, "d": 1})
                            if cached_doc and cached_doc.get("d"):
                                gl = cached_doc["d"]
                            else:
                                # Hit the API
                                async with _sem2:
                                    fix_data = await api_football_request("fixtures/players", {"fixture": fid})
                                if not fix_data:
                                    return None
                                gl = None
                                all_player_logs_inner = {}
                                for team_data in fix_data:
                                    for p in team_data.get("players", []):
                                        pid = p.get("player", {}).get("id")
                                        stats = p.get("statistics", [{}])[0] if p.get("statistics") else {}
                                        mins = stats.get("games", {}).get("minutes") or 0
                                        if pid:
                                            built = {
                                                "minutes": mins,
                                                "passes_total": stats.get("passes", {}).get("total"),
                                                "passes_key": stats.get("passes", {}).get("key"),
                                                "passes_crosses": stats.get("passes", {}).get("cross"),
                                                "shots_total": stats.get("shots", {}).get("total"),
                                                "shots_on": stats.get("shots", {}).get("on"),
                                                "tackles_total": stats.get("tackles", {}).get("total"),
                                                "tackles_interceptions": stats.get("tackles", {}).get("interceptions"),
                                                "tackles_blocks": stats.get("tackles", {}).get("blocks"),
                                                "tackles_clearances": stats.get("tackles", {}).get("clearances"),
                                                "dribbles_attempts": stats.get("dribbles", {}).get("attempts"),
                                                "fouls_drawn": stats.get("fouls", {}).get("drawn"),
                                                "fouls_committed": stats.get("fouls", {}).get("committed"),
                                                "duels_won": stats.get("duels", {}).get("won"),
                                                "goals_total": stats.get("goals", {}).get("total"),
                                                "goals_assists": stats.get("goals", {}).get("assists"),
                                                "goals_saves": stats.get("goals", {}).get("saves"),
                                                "cards_yellow": stats.get("cards", {}).get("yellow"),
                                            }
                                            all_player_logs_inner[pid] = built
                                            if pid == req.playerId and mins > 0:
                                                gl = built
                                # Name-based fallback for Stage 2
                                if gl is None and req.playerName:
                                    _tname = req.playerName.lower().strip()
                                    _tparts = set(_tname.split())
                                    for team_data2 in fix_data:
                                        for p2 in team_data2.get("players", []):
                                            p2name = (p2.get("player", {}).get("name") or "").lower().strip()
                                            p2stats = p2.get("statistics", [{}])[0] if p2.get("statistics") else {}
                                            p2mins = p2stats.get("games", {}).get("minutes") or 0
                                            if not p2name or not p2mins:
                                                continue
                                            p2parts = set(p2name.split())
                                            common2 = _tparts & p2parts
                                            if common2 and len(common2) / max(len(_tparts), len(p2parts)) >= 0.5:
                                                gl = all_player_logs_inner.get(p2.get("player", {}).get("id"))
                                                if gl:
                                                    print(f"[NAME-MATCH-S2] fid={fid}: matched '{req.playerName}' → '{p2name}' (partial)")
                                                    break
                                        if gl:
                                            break
                                # Cache all players from this fixture
                                async def _cache_all_inner(fid_inner, logs_inner):
                                    ops = [
                                        db.fixture_player_cache.update_one(
                                            {"_k": f"fxp_{fid_inner}_{pid_k}"},
                                            {"$set": {"_k": f"fxp_{fid_inner}_{pid_k}", "_ts": datetime.now(timezone.utc), "d": gl_v}},
                                            upsert=True
                                        ) for pid_k, gl_v in logs_inner.items()
                                    ]
                                    if ops:
                                        await aio.gather(*ops, return_exceptions=True)
                                aio.ensure_future(_cache_all_inner(fid, all_player_logs_inner))
                                if gl is None:
                                    return None

                            minutes = gl.get("minutes", 0)
                            if not minutes or minutes == 0:
                                return None
                            gl["date"] = fix_date
                            gl["opponent"] = fix_opponent
                            gl["venue"] = player_fix_venue
                            gl["score"] = f"{home_goals}-{away_goals}"
                            gl["league"] = fix_league
                            gl["round"] = fix_round
                            if req.sport == "soccer":
                                _home_tp, _away_tp = await _direct_fixture_possession(
                                    fid, home_team_id,
                                    fix_raw.get("teams", {}).get("away", {}).get("id"),
                                )
                                _apply_optional_soccer_possession(
                                    gl,
                                    player_fix_venue,
                                    _home_tp,
                                    _away_tp,
                                )
                            stat_val = gl.get(_gl_key2)
                            if stat_val is not None and minutes > 0:
                                gl["targetStatPer90"] = round((stat_val / minutes) * 90, 2)
                            return gl
                        except Exception:
                            return None

                    # This is a recovery path after the primary history loader
                    # missed. Keep it bounded so provider throttling cannot
                    # turn one prediction into 40 player/stat/possession calls.
                    _pf_rows = list(_player_fixtures_raw)[:16]
                    _pf_tasks = [_fetch_player_fix_stats(fx) for fx in _pf_rows]
                    try:
                        _pf_results = await aio.wait_for(
                            aio.gather(*_pf_tasks, return_exceptions=True),
                            timeout=8,
                        )
                    except aio.TimeoutError:
                        print(
                            f"[PLAYER-DIRECT] {req.playerName}: "
                            "fixture-stat recovery timed out after 8s"
                        )
                        _pf_results = []
                    for r in _pf_results:
                        if r and not isinstance(r, Exception):
                            player_game_logs.append(r)

                    if player_game_logs:
                        print(f"[PLAYER-DIRECT] {req.playerName}/{req.propType}: fetched {len(player_game_logs)} real game logs via player API")
            except Exception as _pde:
                print(f"[PLAYER-DIRECT] Error: {_pde}")

        # Stage 2: Season aggregate fallback — only if API direct also returned nothing
        if req.sport == "soccer":
            # A provider/cache response can contain a mixture of complete
            # appearances and rows missing minutes, target stats, or optional
            # fixture possession, especially when a new competition season has
            # just started. Keep real appearances with usable target-stat
            # evidence; possession remains explicitly unavailable when absent.
            _verified_player_logs = _filter_usable_soccer_history_logs(
                player_game_logs,
                req.propType,
            )
            _dropped_incomplete_logs = len(player_game_logs or []) - len(_verified_player_logs)
            if _dropped_incomplete_logs:
                print(
                    f"[PLAYER HISTORY QUALITY] {req.playerName}: dropped "
                    f"{_dropped_incomplete_logs} incomplete appearance(s); "
                    f"retained {len(_verified_player_logs)} stat-bearing rows "
                    f"(historical possession is optional)"
                )
            player_game_logs = _verified_player_logs
            if not player_game_logs:
                raise HTTPException(
                    status_code=424,
                    detail=(
                        "Verified player game data is unavailable: no soccer "
                        "appearance currently has positive minutes and usable "
                        "target-stat evidence. Please retry shortly."
                    ),
                )

            for _game in player_game_logs:
                _game["tp"] = _game.get("teamPossession")
                if (
                    _game.get("teamPossession") is not None
                    and _game.get("opponentPossession") is not None
                ):
                    _game.setdefault("possessionStatus", "verified")
                    _game.setdefault("possessionSource", "fixture_statistics")
                else:
                    _game["teamPossession"] = None
                    _game["opponentPossession"] = None
                    _game.pop("tp", None)
                    _game["possessionStatus"] = "unavailable"
                    _game["possessionSource"] = None

        _history_target_field = STAT_FIELD_MAP.get(req.propType, "passes_total")
        _has_observed_history_target = any(
            isinstance(_game, dict)
            and (
                _game.get(_history_target_field) is not None
                or _game.get("targetStat") is not None
            )
            for _game in (player_game_logs or [])
        )
        # If the cache proves the player appeared but the provider returned
        # null for every requested stat, use the verified season aggregate as
        # a transparent prior. Keep the real cache rows for provenance; only
        # add synthetic rows when there is no observed target value to model.
        if (not player_game_logs or not _has_observed_history_target) and player_stats:
            _sfm_fallback = {
                "goals": ("goals", "total"), "assists": ("goals", "assists"),
                "shots_assisted": ("passes", "key"), "pass_attempts": ("passes", "total"),
                "passes": ("passes", "total"), "shots": ("shots", "total"),
                "shots_on_target": ("shots", "on"), "tackles": ("tackles", "total"),
                "key_passes": ("passes", "key"), "saves": ("goals", "saves"),
                "interceptions": ("tackles", "interceptions"), "blocks": ("tackles", "blocks"),
                "dribbles": ("dribbles", "attempts"), "fouls_drawn": ("fouls", "drawn"),
                "fouls_committed": ("fouls", "committed"), "crosses": ("passes", "cross"),
                "clearances": ("tackles", "clearances"), "duels_won": ("duels", "won"),
                "yellow_cards": ("cards", "yellow"),
            }
            _gl_field_map3 = {
                "goals": "goals_total", "assists": "goals_assists",
                "shots_assisted": "passes_key", "pass_attempts": "passes_total",
                "passes": "passes_total", "shots": "shots_total",
                "shots_on_target": "shots_on", "tackles": "tackles_total",
                 "key_passes": "passes_key", "saves": "goals_saves",
                 "goalie_saves": "goals_saves",
                "interceptions": "tackles_interceptions", "blocks": "tackles_blocks",
                "dribbles": "dribbles_attempts", "fouls_drawn": "fouls_drawn",
                "fouls_committed": "fouls_committed", "crosses": "passes_crosses",
                "clearances": "tackles_clearances", "duels_won": "duels_won",
                "yellow_cards": "cards_yellow",
            }
            _best_stat = None
            _best_appearances = 0
            _best_minutes = 0
            for _stat_entry in (player_stats.get("statistics") or []):
                _apps = _stat_entry.get("games", {}).get("appearences") or 0
                _mins = _stat_entry.get("games", {}).get("minutes") or 0
                if _apps >= 3 and _mins >= 270 and _apps > _best_appearances:
                    _cat, _sub = _sfm_fallback.get(req.propType, ("passes", "total"))
                    _raw = _stat_entry.get(_cat, {}).get(_sub)
                    if _raw is not None:
                        _best_stat = _stat_entry
                        _best_appearances = _apps
                        _best_minutes = _mins

            if _best_stat:
                _cat, _sub = _sfm_fallback.get(req.propType, ("passes", "total"))
                _raw_total = _best_stat.get(_cat, {}).get(_sub) or 0
                _avg_per_game = round(_raw_total / _best_appearances, 2) if _best_appearances else 0
                _avg_minutes = round(_best_minutes / _best_appearances, 1) if _best_appearances else 90
                _gl_key3 = _gl_field_map3.get(req.propType, "passes_total")
                _n_synthetic = min(_best_appearances, 20)
                for _i in range(_n_synthetic):
                    _syn_log = {
                        _gl_key3: _avg_per_game,
                        "minutes": _avg_minutes,
                        "date": "", "opponent": "",
                        "venue": "home" if _i % 2 == 0 else "away",
                        "score": "",
                        "league": (_best_stat.get("league") or {}).get("name", ""),
                        "round": "", "synthetic": True,
                    }
                    if _avg_per_game and _avg_minutes > 0:
                        _syn_log["targetStatPer90"] = round((_avg_per_game / _avg_minutes) * 90, 2)
                    player_game_logs.append(_syn_log)
                print(f"[SEASON FALLBACK] {req.playerName}/{req.propType}: built {_n_synthetic} synthetic logs from season avg={_avg_per_game}/game")
            else:
                print(f"[NO GAME LOGS] {req.playerName}/{req.propType}: no game logs anywhere. Using line as prior.")

        # =============================================
        # MATCH DOMINANCE: Opponent-aware possession + context multiplier
        # =============================================
        def compute_match_dominance(
            team_stats_list,
            opp_stats_list,
            odds,
            is_home,
            standing_data,
            is_neutral=False,
            team_possession_packet=None,
            opponent_possession_packet=None,
        ):
            """Compute expected possession using opponent-aware model + odds adjustment.
            SYMMETRIC: Always computes from HOME team perspective first, then maps back.
            This ensures the SAME match always produces identical possession numbers
            regardless of which player (home or away) triggers the analysis.

            Uses venue-split averages: home team's HOME-game possession avg vs
            away team's AWAY-game possession avg. Overall averages inflate expected
            possession for away teams (e.g. Braga 54% overall but ~48% away).

            For is_neutral=True: uses overall averages for both teams and skips the
            home-venue possession boost (+1.5pp). Used for World Cup / tournament
            games where neither team has a real home-ground advantage."""
            dom = {
                "expectedPoss": 50.0,
                "oppExpectedPoss": 50.0,
                "multiplier": 1.0,
                "notes": [],
                "seasonAvgIsReal": False,
                "hasRealPossData": False,
                "possessionSource": "unavailable",
                "possessionVerificationStatus": "insufficient_sample",
                "possessionSampleRequired": _POSSESSION_SAMPLE_TARGET,
                "teamPossessionSampleSize": 0,
                "opponentPossessionSampleSize": 0,
                "teamPossessionVenue": "all" if is_neutral else ("home" if is_home else "away"),
                "opponentPossessionVenue": "all" if is_neutral else ("away" if is_home else "home"),
                "moneylineWeight": 0.0,
                "moneylineExpectedHomePoss": None,
                "recencyWeighting": f"half_life_{POSSESSION_RECENCY_HALF_LIFE:g}_matches",
            }

            def avg_poss(sl, venue_filter=None):
                vals = []
                for s in (sl or []):
                    if venue_filter and s.get("venue") != venue_filter:
                        continue
                    p = s.get("possession")
                    if p is not None:
                        try:
                            vals.append(float(str(p).replace("%", "")))
                        except (ValueError, TypeError):
                            pass
                return round(sum(vals) / len(vals), 1) if vals else None

            _team_packet = (
                team_possession_packet
                if isinstance(team_possession_packet, dict)
                else {}
            )
            _opp_packet = (
                opponent_possession_packet
                if isinstance(opponent_possession_packet, dict)
                else {}
            )
            _team_packet_avg = _team_packet.get("average")
            _opp_packet_avg = _opp_packet.get("average")
            _team_packet_n = int(_team_packet.get("sampleSize") or 0)
            _opp_packet_n = int(_opp_packet.get("sampleSize") or 0)
            _team_packet_verified = bool(
                _team_packet.get("verified")
                and _team_packet_n >= _POSSESSION_SAMPLE_TARGET
                and isinstance(_team_packet_avg, (int, float))
            )
            _opp_packet_verified = bool(
                _opp_packet.get("verified")
                and _opp_packet_n >= _POSSESSION_SAMPLE_TARGET
                and isinstance(_opp_packet_avg, (int, float))
            )
            _both_schedule_samples_verified = (
                _team_packet_verified and _opp_packet_verified
            )
            dom["teamPossessionSampleSize"] = _team_packet_n
            dom["opponentPossessionSampleSize"] = _opp_packet_n
            dom["teamPossessionObservedAvg"] = _team_packet_avg
            dom["opponentPossessionObservedAvg"] = _opp_packet_avg
            dom["teamPossessionRows"] = list(_team_packet.get("rows") or [])
            dom["opponentPossessionRows"] = list(_opp_packet.get("rows") or [])
            dom["teamPossessionUsedCount"] = _team_packet_n
            dom["opponentPossessionUsedCount"] = _opp_packet_n
            dom["teamPossessionVenue"] = _team_packet.get(
                "venue",
                dom["teamPossessionVenue"],
            )
            dom["opponentPossessionVenue"] = _opp_packet.get(
                "venue",
                dom["opponentPossessionVenue"],
            )
            if _both_schedule_samples_verified:
                dom["possessionVerificationStatus"] = "verified"
            elif _team_packet_n or _opp_packet_n:
                dom["possessionVerificationStatus"] = "insufficient_sample"
            else:
                dom["possessionVerificationStatus"] = "unavailable"

            def avg_passes(sl):
                """Average total passes per game from fixture stats."""
                vals = []
                for s in (sl or []):
                    v = s.get("totalPasses")
                    if v is not None:
                        try:
                            vals.append(int(v))
                        except (ValueError, TypeError):
                            pass
                return round(sum(vals) / len(vals), 1) if vals else None

            # Only the independent schedule packets can establish a verified
            # season average.  The pressure-stat packets below are intentionally
            # limited to seven rows and must never silently become a possession
            # substitute when the ten-match venue gate is not met.
            _team_verified_avg = _team_packet_avg if _team_packet_verified else None
            _opp_verified_avg = _opp_packet_avg if _opp_packet_verified else None

            if is_neutral:
                # Neutral venue: no home/away split — use overall averages for both teams.
                # Home/away splits inflate numbers from qualifier mismatches (e.g. a team
                # that averaged 67% possession at home against weak qualifiers). Using
                # overall averages is more honest for a neutral-venue tournament match.
                if is_home:
                    home_avg = _team_verified_avg
                    away_avg = _opp_verified_avg
                    home_rank = standing_data.get("teamRank") if standing_data else None
                    away_rank = standing_data.get("oppRank") if standing_data else None
                else:
                    home_avg = _opp_verified_avg
                    away_avg = _team_verified_avg
                    home_rank = standing_data.get("oppRank") if standing_data else None
                    away_rank = standing_data.get("teamRank") if standing_data else None
            elif is_home:
                # Player's team is HOME → use their home game avg; opponent uses away game avg
                home_avg = _team_verified_avg
                away_avg = _opp_verified_avg
                home_rank = standing_data.get("teamRank") if standing_data else None
                away_rank = standing_data.get("oppRank") if standing_data else None
            else:
                # Player's team is AWAY → use their away game avg; opponent (home) uses home game avg
                home_avg = _opp_verified_avg
                away_avg = _team_verified_avg
                home_rank = standing_data.get("oppRank") if standing_data else None
                away_rank = standing_data.get("teamRank") if standing_data else None

            # For the possession squeeze engine, also compute overall season averages
            # from the same verified venue-specific schedule packets.  Do not
            # use the seven-row pressure sample as a hidden fallback.
            team_avg = _team_verified_avg
            opp_avg = _opp_verified_avg

            # Fallback: when possession data is unavailable, estimate from standings
            # gap only. Each rank position ≈ 0.8% possession difference.
            if (home_avg is None or away_avg is None) and home_rank and away_rank:
                gap = away_rank - home_rank  # positive = home team stronger
                raw_poss = 50.0 + 2.5 + min(8.0, max(-8.0, gap * 0.8))
                home_poss_fallback = min(72.0, max(28.0, round(raw_poss, 1)))
                away_poss_fallback = round(100.0 - home_poss_fallback, 1)
                # Use 50% as season avg so the squeeze can activate on big gaps
                fallback_home_avg = 50.0
                fallback_away_avg = 50.0
                # Neutral: formula maps player_team→"away", opponent→"home".
                # Use away_poss_fallback for player regardless of user-entered venue.
                if is_home:
                    dom["expectedPoss"] = home_poss_fallback
                    dom["oppExpectedPoss"] = away_poss_fallback
                    dom["teamSeasonAvg"] = fallback_home_avg
                    dom["oppSeasonAvg"] = fallback_away_avg
                else:
                    dom["expectedPoss"] = away_poss_fallback
                    dom["oppExpectedPoss"] = home_poss_fallback
                    dom["teamSeasonAvg"] = fallback_away_avg
                    dom["oppSeasonAvg"] = fallback_home_avg
                dom["homePoss"] = home_poss_fallback
                dom["awayPoss"] = away_poss_fallback
                dom["notes"].append(f"Rank-gap fallback (no poss data): #{home_rank} vs #{away_rank} → {home_poss_fallback:.0f}% home / {away_poss_fallback:.0f}% away")
                dom["possessionSource"] = "standings_fallback"
                player_team_poss = dom["expectedPoss"]
                poss_ratio = player_team_poss / 50.0
                PASS_PROPS = {"pass_attempts", "key_passes", "crosses", "passes"}
                DEF_PROPS = {"tackles", "interceptions", "blocks", "clearances"}
                if req.propType in PASS_PROPS:
                    raw_adj = poss_ratio - 1.0
                    capped_adj = max(-0.35, min(0.35, raw_adj))
                    dom["multiplier"] = round(1.0 + capped_adj, 3)
                elif req.propType in DEF_PROPS:
                    inverse_ratio = (100.0 - player_team_poss) / 50.0
                    raw_adj = inverse_ratio - 1.0
                    capped_adj = max(-0.25, min(0.25, raw_adj))
                    dom["multiplier"] = round(1.0 + capped_adj, 3)

            elif (home_avg is None or away_avg is None):
                # No possession stats AND no standings rankings.
                # Common for WC/Euro/Copa tournament group stage where API-Football
                # doesn't return possession averages for the tournament league.
                # Last-resort: derive expected possession from match odds probability.
                # A 70% win-prob favourite is realistically ~55% possession territory.
                _moneyline_signal = moneyline_possession_signal(odds)
                if _moneyline_signal:
                    try:
                        _fx_home_poss = _moneyline_signal["expectedHomePossession"]
                        _fx_away_poss = round(100.0 - _fx_home_poss, 1)
                        dom["homePoss"] = _fx_home_poss
                        dom["awayPoss"] = _fx_away_poss
                        dom["moneylineWeight"] = _moneyline_signal["weight"]
                        dom["moneylineExpectedHomePoss"] = _fx_home_poss
                        if is_home:
                            dom["expectedPoss"] = _fx_home_poss
                            dom["oppExpectedPoss"] = _fx_away_poss
                        else:
                            dom["expectedPoss"] = _fx_away_poss
                            dom["oppExpectedPoss"] = _fx_home_poss
                        dom["teamSeasonAvg"] = 50.0
                        dom["oppSeasonAvg"] = 50.0
                        dom["notes"].append(
                            f"Moneyline-only estimate (insufficient verified venue samples): "
                            f"{_fx_home_poss:.0f}%/{_fx_away_poss:.0f}%"
                        )
                        dom["possessionSource"] = "odds_fallback"
                        _otp = dom["expectedPoss"]
                        _otr = _otp / 50.0
                        _PASS_P = {"pass_attempts", "key_passes", "crosses", "passes"}
                        _DEF_P = {"tackles", "interceptions", "blocks", "clearances"}
                        _SHT_P = {"shots", "shots_on_target"}
                        if req.propType in _PASS_P:
                            dom["multiplier"] = round(
                                1.0 + max(-0.35, min(0.35, _otr - 1.0)),
                                3,
                            )
                        elif req.propType in _DEF_P:
                            _inv = (100.0 - _otp) / 50.0
                            dom["multiplier"] = round(
                                1.0 + max(-0.25, min(0.25, _inv - 1.0)),
                                3,
                            )
                        elif req.propType in _SHT_P:
                            dom["multiplier"] = round(
                                1.0 + max(-0.20, min(0.20, (_otr - 1.0) * 0.6)),
                                3,
                            )
                    except Exception as _oe:
                        dom["notes"].append(f"Odds-only possession fallback failed: {_oe}")

            if home_avg is not None and away_avg is not None:

                # ── Qualifying/weak-opponent contamination guard ───────────────
                # National teams in WC/AFCON/CONCACAF qualifying often average
                # 60-70% possession against weak sides (e.g. SA vs Lesotho).
                # These stats contaminate the possession monster when the same
                # team travels to play a much stronger opponent (e.g. Mexico at
                # Azteca). Caps raised (68/72) so elite away teams like France
                # (~63-65% away avg) aren't artificially cut to 58%.
                home_avg = min(home_avg, 72.0)
                away_avg = min(away_avg, 68.0)

                away_concedes = 100.0 - away_avg

                # FIX 3 — Lower monster threshold from 57 → 53.
                # Teams like PSG, Atlético, Inter Miami average 53-57% away
                # possession and consistently suppress opponents more than the
                # old neutral blend captured. Activating the weighted blend
                # earlier gives their possession dominance proper weight.
                if away_avg > 53:
                    extremity = min((away_avg - 53) / 9.0, 1.0)
                    away_weight = 0.60 + extremity * 0.30
                    home_weight = 1.0 - away_weight
                    home_poss = home_weight * home_avg + away_weight * away_concedes
                    dom["notes"].append(f"Possession monster: away avg {away_avg:.0f}% → weight {away_weight*100:.0f}% away-driven (raw base {home_poss:.1f}%)")
                elif home_avg > 57:
                    extremity = min((home_avg - 57) / 11.0, 1.0)
                    home_weight = 0.60 + extremity * 0.30
                    away_weight_blend = 1.0 - home_weight
                    home_concedes = 100.0 - home_avg
                    away_poss_raw = away_weight_blend * away_avg + home_weight * home_concedes
                    home_poss = 100.0 - away_poss_raw
                    dom["notes"].append(f"Possession monster: home avg {home_avg:.0f}% → weight {home_weight*100:.0f}% home-driven (raw base {home_poss:.1f}%)")
                else:
                    home_poss = (home_avg + away_concedes) / 2.0

                # FIX 3 — Home-field possession advantage trimmed 2.5 → 1.5.
                # Data shows home teams don't gain 2.5% possession from venue alone;
                # 1.5% is calibrated from settled pick residuals.
                # Neutral venues (World Cup, etc.) get NO home-field boost.
                if is_neutral:
                    home_boost = 0.0
                else:
                    home_boost = 1.5
                    higher_avg = max(home_avg, away_avg)
                    if higher_avg > 60:
                        dampen = min((higher_avg - 60) / 10.0, 0.7)
                        home_boost *= (1.0 - dampen)
                        dom["notes"].append(f"Home poss boost dampened: {home_boost:.1f}% (dominant team avg {higher_avg:.0f}%)")
                home_poss += home_boost

                if home_rank and away_rank:
                    gap = away_rank - home_rank
                    quality_adj = min(4.0, max(-4.0, gap * 0.4))
                    home_poss += quality_adj
                    if abs(quality_adj) > 1:
                        dom["notes"].append(f"Standings gap (#{home_rank} vs #{away_rank}): {quality_adj:+.1f}% poss adj")

                # Current moneyline is a bounded contextual blend.  It can
                # refine the verified schedule calculation, but it cannot
                # replace the ten-match venue-specific evidence.
                _moneyline_signal = moneyline_possession_signal(odds)
                if _moneyline_signal:
                    _moneyline_weight = _moneyline_signal["weight"]
                    _moneyline_target = _moneyline_signal["expectedHomePossession"]
                    _pre_moneyline_home = home_poss
                    home_poss = round(
                        (1.0 - _moneyline_weight) * home_poss
                        + _moneyline_weight * _moneyline_target,
                        1,
                    )
                    dom["moneylineWeight"] = _moneyline_weight
                    dom["moneylineExpectedHomePoss"] = _moneyline_target
                    dom["notes"].append(
                        f"Moneyline blend: {_moneyline_weight:.0%} weight toward "
                        f"{_moneyline_target:.0f}% fixture-home possession "
                        f"({_pre_moneyline_home:.0f}% schedule base)"
                    )

                # FIX 2 — Regression to mean (22% shrink toward 50%).
                home_poss = round(50.0 + (home_poss - 50.0) * 0.78, 1)

                # Ceiling raised 67% → 73%. 67% made France vs Iraq
                # (72-76% realistic) physically impossible.
                home_poss = min(73.0, max(28.0, round(home_poss, 1)))

                # ── EXTREME MISMATCH POST-CORRECTION ─────────────────────────
                # When odds show one team is a massive favourite (≥ 85% implied
                # win prob), the season-avg monster formula can land on the wrong
                # value — e.g. Iraq averages 59% possession at home against weak
                # Asian sides but is +2200 against France.
                #
                # The correction is applied AFTER all formula steps so it can't
                # get confused by home/away/neutral direction logic.
                #
                # We work purely in "formula-home" space: home_poss is always the
                # formula-home team's possession, and the formula-home team is the
                # OPPONENT when the player is away/neutral (see home_avg assignment
                # above). So we need the formula-home team's WIN PROBABILITY.
                #
                # Formula-home win prob:
                #   player is home (non-neutral) → formula-home = player team
                #   player is away or neutral    → formula-home = opponent team
                try:
                    _ep_fh_prob = None   # formula-home team's win prob (0-1)
                    # Determine which odds key maps to the FORMULA-HOME team.
                    #
                    # formula-home team is defined by the code above:
                    #   if is_home and not is_neutral → formula-home = player team
                    #   else (away OR neutral)        → formula-home = opponent team
                    #
                    # odds.home / bookmakerOdds.homeWin always = FIXTURE-HOME team.
                    #
                    # Non-neutral games:
                    #   player home  → formula-home = player = fixture-home  → use home odds
                    #   player away  → formula-home = opp   = fixture-home   → use home odds
                    #   Either way: use home odds. ✓
                    #
                    # Neutral games (is_neutral=True) → ELSE branch, formula-home = opponent:
                    #   playerIsHome=True  → player = fixture-home, opp = fixture-AWAY
                    #                       → formula-home = opp = fixture-away → use AWAY odds
                    #   playerIsHome=False → player = fixture-away, opp = fixture-home
                    #                       → formula-home = opp = fixture-home → use HOME odds
                    #
                    # So: use AWAY odds only when (is_neutral AND playerIsHome).
                    _ep_pih = odds.get("playerIsHome")
                    if _ep_pih is None:
                        _ep_pih = bool(is_home)
                    _ep_use_away = False

                    if odds and odds.get("bookmakerOdds"):
                        _bh = float(odds["bookmakerOdds"].get("homeWin", 3.0))
                        _ba = float(odds["bookmakerOdds"].get("awayWin", 3.0))
                        _bkh = 1.0 / max(_bh, 1.01)
                        _bka = 1.0 / max(_ba, 1.01)
                        _bkt = _bkh + _bka
                        if _bkt > 0:
                            _ep_fh_prob = (_bka if _ep_use_away else _bkh) / _bkt
                    elif odds and odds.get("americanOdds"):
                        def _ml2p_ep(ml):
                            ml = float(ml)
                            return (-ml) / (-ml + 100.0) if ml < 0 else 100.0 / (ml + 100.0)
                        _eaod = odds["americanOdds"]
                        _eih  = _ml2p_ep(_eaod.get("home", 0))  # fixture-home team prob
                        _eia  = _ml2p_ep(_eaod.get("away", 0))  # fixture-away team prob
                        _eit  = _eih + _eia
                        if _eit > 0:
                            _ep_fh_prob = (_eia if _ep_use_away else _eih) / _eit

                    if _ep_fh_prob is not None:
                        _ep_fav_prob = max(_ep_fh_prob, 1.0 - _ep_fh_prob)
                        if _ep_fav_prob >= 0.82:
                            # Odds-only expected possession for formula-home:
                            # calibrated so 95% fav → ~75%, 85% fav → ~61%
                            _ep_odds_hp = max(25.0, min(75.0,
                                50.0 + (_ep_fh_prob - 0.5) * 55.0))
                            # Blend weight: 82% → 0%, 90% → 80%, 95% → 100%
                            _ep_w = min(1.0, (_ep_fav_prob - 0.82) / 0.08)
                            _old_hp = home_poss
                            home_poss = round(
                                _ep_w * _ep_odds_hp + (1.0 - _ep_w) * home_poss, 1)
                            home_poss = min(73.0, max(28.0, home_poss))
                            dom["notes"].append(
                                f"Extreme mismatch corr (w={_ep_w:.0%}, "
                                f"fh_prob={_ep_fh_prob:.1%}): "
                                f"{_old_hp:.0f}%→{home_poss:.0f}%")
                except Exception:
                    pass
                away_poss = round(100.0 - home_poss, 1)

                if is_home:
                    dom["expectedPoss"] = home_poss
                    dom["oppExpectedPoss"] = away_poss
                    dom["teamSeasonAvg"] = home_avg
                    dom["oppSeasonAvg"] = away_avg
                else:
                    dom["expectedPoss"] = away_poss
                    dom["oppExpectedPoss"] = home_poss
                    dom["teamSeasonAvg"] = away_avg
                    dom["oppSeasonAvg"] = home_avg

                dom["homePoss"] = home_poss
                dom["awayPoss"] = away_poss
                # This branch can only be reached when both teams have
                # possession observations.  Keep this separate from the
                # expected possession itself: rank-gap and odds-only fallbacks
                # also produce a number, but their synthetic 50% season
                # baselines must not activate possession-dependent layers.
                dom["hasRealPossData"] = bool(team_avg is not None and opp_avg is not None)
                dom["seasonAvgIsReal"] = dom["hasRealPossData"]
                if dom["hasRealPossData"]:
                    dom["possessionSource"] = "fixture_stats"

                player_team_poss = dom["expectedPoss"]
                poss_ratio = player_team_poss / team_avg if team_avg > 0 else 1.0
                PASS_PROPS = {"pass_attempts", "key_passes", "crosses", "passes"}
                DEF_PROPS = {"tackles", "interceptions", "blocks", "clearances"}

                if req.propType in PASS_PROPS:
                    raw_adj = poss_ratio - 1.0
                    capped_adj = max(-0.35, min(0.35, raw_adj))
                    dom["multiplier"] = round(1.0 + capped_adj, 3)
                    if abs(capped_adj) > 0.03:
                        direction = "boost" if capped_adj > 0 else "drop"
                        dom["notes"].append(f"Pass volume {direction}: expected {player_team_poss:.0f}% poss vs {team_avg:.0f}% avg (ratio={poss_ratio:.2f}) → {capped_adj*100:+.0f}%")
                elif req.propType in DEF_PROPS:
                    inverse_ratio = (100.0 - player_team_poss) / (100.0 - team_avg) if team_avg < 100 else 1.0
                    raw_adj = inverse_ratio - 1.0
                    capped_adj = max(-0.25, min(0.25, raw_adj))
                    dom["multiplier"] = round(1.0 + capped_adj, 3)
                    if abs(capped_adj) > 0.03:
                        direction = "boost" if capped_adj > 0 else "drop"
                        dom["notes"].append(f"Def action {direction}: expected {100-player_team_poss:.0f}% without ball vs {100-team_avg:.0f}% avg → {capped_adj*100:+.0f}%")
                elif req.propType in {"shots", "shots_on_target"}:
                    raw_adj = (poss_ratio - 1.0) * 0.6
                    capped_adj = max(-0.20, min(0.20, raw_adj))
                    dom["multiplier"] = round(1.0 + capped_adj, 3)
                    if abs(capped_adj) > 0.03:
                        dom["notes"].append(f"Shot volume adj from possession ratio → {capped_adj*100:+.0f}%")

            # Team pass-rate for positional baseline (Layer 2)
            _tap = avg_passes(team_stats_list)
            if _tap is not None:
                dom["teamAvgPasses"] = _tap

            return dom

        # Compute standings data for match dominance
        standing_data = {}
        if standings:
            for s in standings:
                s_team = s.get("team", "")
                s_team_name = s_team.get("name", "") if isinstance(s_team, dict) else str(s_team)
                s_team_id = s_team.get("id", "") if isinstance(s_team, dict) else s.get("team_id", "")
                if s_team_name.lower() == req.teamName.lower() or str(s_team_id) == str(req.teamId):
                    standing_data["teamRank"] = s.get("rank")
                if s_team_name.lower() == req.opponentName.lower() or str(s_team_id) == str(req.opponentId):
                    standing_data["oppRank"] = s.get("rank")

        # Determine canonical (home_team_id, away_team_id) for cache key.
        # Use the fixture's playerIsHome flag when available — this tells us
        # which team API-Football actually designated as "home" in the fixture,
        # regardless of what the user typed in the venue field. This is the
        # ONLY reliable source of truth for home/away orientation.
        _pih_flag = match_odds.get("playerIsHome") if match_odds else None
        if _pih_flag is not None:
            _is_home = bool(_pih_flag)
        elif _is_neutral:
            # No odds data available — use team ID as a deterministic tiebreaker
            # so BOTH player scans always produce the same fixture-perspective
            # homePoss/awayPoss values. Without this, both teams are assigned
            # is_home=False (formula-away), the formula is symmetric, and teams
            # with similar qualifier stats produce identical possession numbers.
            _is_home = (actual_team_id or 0) < (req.opponentId or 0)
        else:
            _is_home = player_venue == "home"
        _home_id = _canonical_team_id if _is_home else _canonical_opponent_id
        _away_id = _canonical_opponent_id if _is_home else _canonical_team_id
        _dom_cache_key = (_home_id, _away_id) if (_home_id and _away_id) else None

        # Check cache first — same game always returns same possession
        _cached_dom = None
        if _dom_cache_key:
            _entry = _match_dom_cache.get(_dom_cache_key)
            if _entry and (_time.time() - _entry["ts"]) < _MATCH_DOM_TTL:
                _cached_dom = _entry["dom"]

        _schedule_poss_evidence_available = bool(
            (team_schedule_possession or {}).get("rows")
            or (opponent_schedule_possession or {}).get("rows")
        )
        if _cached_dom is not None and not _schedule_poss_evidence_available:
            # Remap expectedPoss/oppExpectedPoss for this player's perspective
            match_dominance = dict(_cached_dom)
            if _is_home:
                match_dominance["expectedPoss"] = _cached_dom["homePoss"]
                match_dominance["oppExpectedPoss"] = _cached_dom["awayPoss"]
                match_dominance["teamSeasonAvg"] = _cached_dom.get("homeSeasonAvg", _cached_dom.get("teamSeasonAvg"))
                match_dominance["oppSeasonAvg"] = _cached_dom.get("awaySeasonAvg", _cached_dom.get("oppSeasonAvg"))
            else:
                match_dominance["expectedPoss"] = _cached_dom["awayPoss"]
                match_dominance["oppExpectedPoss"] = _cached_dom["homePoss"]
                match_dominance["teamSeasonAvg"] = _cached_dom.get("awaySeasonAvg", _cached_dom.get("oppSeasonAvg"))
                match_dominance["oppSeasonAvg"] = _cached_dom.get("homeSeasonAvg", _cached_dom.get("teamSeasonAvg"))

            # CRITICAL: multiplier is prop-type-specific — MUST be recomputed from
            # cached possession data for the CURRENT prop type.  The cached value was
            # set by whichever prop type hit this match first (e.g. clearances → +17%
            # defensive boost) and is WRONG for a different prop type (e.g. pass_attempts).
            _cp = match_dominance["expectedPoss"]
            _ca = match_dominance.get("teamSeasonAvg") or 50.0
            _PASS_PROPS_C  = {"pass_attempts", "key_passes", "crosses", "passes"}
            _DEF_PROPS_C   = {"tackles", "interceptions", "blocks", "clearances"}
            _SHOT_PROPS_C  = {"shots", "shots_on_target"}
            if req.propType in _PASS_PROPS_C:
                _poss_ratio_c = _cp / _ca if _ca > 0 else 1.0
                _capped_c = max(-0.35, min(0.35, _poss_ratio_c - 1.0))
                match_dominance["multiplier"] = round(1.0 + _capped_c, 3)
            elif req.propType in _DEF_PROPS_C:
                _inv_ratio_c = (100.0 - _cp) / (100.0 - _ca) if _ca < 100 else 1.0
                _capped_c = max(-0.25, min(0.25, _inv_ratio_c - 1.0))
                match_dominance["multiplier"] = round(1.0 + _capped_c, 3)
            elif req.propType in _SHOT_PROPS_C:
                _poss_ratio_c = _cp / _ca if _ca > 0 else 1.0
                _capped_c = max(-0.20, min(0.20, (_poss_ratio_c - 1.0) * 0.6))
                match_dominance["multiplier"] = round(1.0 + _capped_c, 3)
            else:
                match_dominance["multiplier"] = 1.0

            print(f"[MATCH DOMINANCE CACHE HIT] {req.playerName}: home={_cached_dom['homePoss']}% away={_cached_dom['awayPoss']}% mult_recalc={match_dominance['multiplier']} for {req.propType}")
        else:
            # Build effective odds: API-fetched match_odds is preferred, but for
            # WC/tournament games the odds API often returns nothing. Fall back to
            # req.odds (user-supplied from the mobile app) so the possession
            # extreme-mismatch correction fires correctly for e.g. Portugal -1111
            # vs Uzbekistan +2200 WC group-stage predictions.
            _eff_odds = match_odds or {}
            if req.odds and not _eff_odds.get("bookmakerOdds") and not _eff_odds.get("americanOdds"):
                _req_o = req.odds if isinstance(req.odds, dict) else (req.odds.dict() if hasattr(req.odds, "dict") else {})
                if _req_o.get("bookmakerOdds") or _req_o.get("americanOdds"):
                    _eff_odds = dict(_eff_odds)
                    _eff_odds.update(_req_o)
            match_dominance = compute_match_dominance(
                team_fixture_stats, opponent_fixture_stats, _eff_odds,
                _is_home,
                standing_data,
                is_neutral=_is_neutral,
                team_possession_packet=team_schedule_possession,
                opponent_possession_packet=opponent_schedule_possession,
            )
            # Store in cache with home/away season avgs for perspective remapping
            if _dom_cache_key and match_dominance.get("homePoss") is not None:
                _cache_entry = dict(match_dominance)
                if _is_home:
                    _cache_entry["homeSeasonAvg"] = match_dominance.get("teamSeasonAvg")
                    _cache_entry["awaySeasonAvg"] = match_dominance.get("oppSeasonAvg")
                else:
                    _cache_entry["homeSeasonAvg"] = match_dominance.get("oppSeasonAvg")
                    _cache_entry["awaySeasonAvg"] = match_dominance.get("teamSeasonAvg")
                _match_dom_cache[_dom_cache_key] = {"ts": _time.time(), "dom": _cache_entry}

        # A numeric fallback is not verified possession.  The exact model
        # signal requires both independent venue-specific schedule packets to
        # pass the ten-match gate; standings and moneyline estimates remain
        # visible but must not masquerade as fixture-statistics evidence.
        match_dominance["hasRealPossData"] = (
            match_dominance.get("seasonAvgIsReal") is True
            and match_dominance.get("possessionVerificationStatus") == "verified"
        )
        if match_dominance.get("notes"):
            print(f"[MATCH DOMINANCE] {req.playerName}: poss={match_dominance['expectedPoss']}%, mult={match_dominance['multiplier']}, {' | '.join(match_dominance['notes'])}")
        else:
            print(f"[MATCH DOMINANCE] {req.playerName}: NO real data available (poss/standings/odds all missing) — 50/50 default is uninformative")

        # ─────────────────────────────────────────────────────────────────────
        # H2H POSSESSION OVERRIDE
        # The season-average model can't know that Damac dominates 63%
        # possession specifically against Al-Fayha even if their overall home
        # average is lower. When we have ≥2 H2H fixtures with possession data,
        # we override expectedPoss with a weighted blend:
        #   H2H avg × (50-70%) + season avg × (30-50%)
        # Weight grows with sample count: 2 games=50%, 3=56%, 4=62%, 5+=68%.
        # This is the single biggest source of missed high-pass CB/CDM props.
        #
        # Source priority: DB cache (instant) → /fixtures/statistics API call
        # ─────────────────────────────────────────────────────────────────────
        async def _get_h2h_fixture_poss(fid: int, team_id: int) -> float | None:
            """Return team's possession % in a fixture. Tries cache first, then API."""
            # 1. Try fixture_player_cache (populated from previous predictions)
            try:
                _doc = await db.fixture_player_cache.find_one(
                    {"_k": f"fxt_{fid}_{team_id}"}, {"_id": 0, "d.possession": 1}
                )
                if _doc and _doc.get("d"):
                    _raw = str(_doc["d"].get("possession", "")).replace("%", "").strip()
                    if _raw:
                        return float(_raw)
            except Exception:
                pass
            # 2. Fallback: fetch /fixtures/statistics directly from the API
            try:
                _stats = await api_football_request("fixtures/statistics", {"fixture": fid})
                for _s in (_stats or []):
                    if _s.get("team", {}).get("id") == team_id:
                        for _st in _s.get("statistics", []):
                            if _st.get("type") == "Ball Possession":
                                _val = str(_st.get("value", "")).replace("%", "").strip()
                                if _val:
                                    return float(_val)
            except Exception:
                pass
            return None

        _h2h_team_poss_vals: list[float] = []
        if h2h_data and actual_team_id:
            _h2h_poss_tasks = []
            _h2h_fxt_ids_used = []
            for _hf in h2h_data[:8]:
                _hf_fid  = _hf.get("fixture", {}).get("id")
                _hf_home = _hf.get("teams", {}).get("home", {}).get("id")
                if not _hf_fid:
                    continue
                # CRITICAL: venue-match — only include H2H fixtures where the
                # player's team had the SAME venue as the current prediction.
                # Mixing home and away possession averages to ~50% and wipes out
                # the opponent-specific possession advantage (e.g. Damac 63% HOME
                # vs Fayha but only 38% AWAY → naive avg = 50.5%, useless).
                _player_is_home_in_h2h = (_hf_home == actual_team_id)
                if _player_is_home_in_h2h != _is_home:
                    continue  # skip wrong-venue fixture
                _h2h_poss_tasks.append(_get_h2h_fixture_poss(_hf_fid, actual_team_id))
                _h2h_fxt_ids_used.append(_hf_fid)
            try:
                _h2h_poss_results = await aio.wait_for(
                    aio.gather(*_h2h_poss_tasks), timeout=8
                )
                _h2h_team_poss_vals = [r for r in _h2h_poss_results if r is not None]
                print(f"[H2H POSS FETCH] {req.playerName}: venue={'home' if _is_home else 'away'} "
                      f"venue-matched fixtures={len(_h2h_poss_tasks)}/{len(h2h_data[:8])}, "
                      f"got possession for {len(_h2h_team_poss_vals)}: {_h2h_team_poss_vals}")
            except aio.TimeoutError:
                print(f"[H2H POSS FETCH] timeout for {req.playerName}")
                _h2h_team_poss_vals = []

        _h2h_poss_avg: float | None = None
        if _h2h_team_poss_vals:
            _h2h_n = len(_h2h_team_poss_vals)
            _h2h_poss_avg = round(
                sum(_h2h_team_poss_vals) / _h2h_n,
                1,
            )
            # Direct meetings are useful context, but they are not an
            # independent ten-match venue schedule and must not replace the
            # schedule-gated calculation above.
            match_dominance["h2hPossAvg"] = _h2h_poss_avg
            match_dominance["h2hPossCount"] = _h2h_n
            match_dominance["h2hPossRole"] = "context_only"
            match_dominance["notes"].append(
                f"H2H possession context ({_h2h_n} venue-matched matches): "
                f"{_h2h_poss_avg:.0f}% — not used as the exact possession input"
            )
            print(
                f"[H2H POSS CONTEXT] {req.playerName}: avg={_h2h_poss_avg}% "
                f"(n={_h2h_n}); schedule/moneyline calculation unchanged"
            )

        # =============================================
        # SITUATION ENGINE: Apply possession boost from knockout/2nd-leg context
        # Overrides the season-average-based possession model when game state demands it
        # =============================================
        _sit_mults = game_situation.get("multipliers", {})
        _sit_poss_boost = _sit_mults.get("possessionBoostHome", 0.0)
        if _sit_poss_boost != 0.0 and match_dominance.get("homePoss") is not None:
            # Apply boost to home team's raw possession, recalculate both sides
            old_home_poss = match_dominance["homePoss"]
            new_home_poss = min(80.0, max(30.0, old_home_poss + _sit_poss_boost))
            new_away_poss = round(100.0 - new_home_poss, 1)
            print(f"[SITUATION BOOST] Possession: home {old_home_poss:.1f}% → {new_home_poss:.1f}% (boost={_sit_poss_boost:+.1f}%)")
            match_dominance["homePoss"] = new_home_poss
            match_dominance["awayPoss"] = new_away_poss
            # Remap player perspective
            if _sit_is_home:
                match_dominance["expectedPoss"] = new_home_poss
                match_dominance["oppExpectedPoss"] = new_away_poss
            else:
                match_dominance["expectedPoss"] = new_away_poss
                match_dominance["oppExpectedPoss"] = new_home_poss
            match_dominance["notes"].extend(_sit_mults.get("notes", []))
            # NOTE: do NOT write boosted values back into _match_dom_cache.
            # The cache holds the clean season-stats-derived possession.
            # The situation boost is applied fresh each call from that clean base.
            # Writing boosted values into the cache causes a compounding spiral:
            # each subsequent call for the same fixture reads the already-boosted
            # value as its new baseline and adds the boost again, e.g.
            # 63% → 72% → 81% → capped 80% across 3 requests.

        # =============================================
        # GAME TEMPO ESTIMATION — Expected match intensity
        # A 2-2 draw = high tempo → both teams pass MORE.
        # A 0-0 grind = low tempo → both teams pass LESS.
        # This adjusts the dominance multiplier based on expected total game activity.
        # =============================================
        game_tempo = {"expectedTempo": "normal", "tempoMultiplier": 1.0, "notes": []}
        try:
            # Signal 1: Both teams' goals-per-game from team stats
            team_gpg = 0.0
            opp_gpg = 0.0
            team_ga_pg = 0.0
            opp_ga_pg = 0.0
            if team_stats:
                fixtures_played = team_stats.get("fixtures", {})
                total_played = (fixtures_played.get("played", {}).get("total") or 0)
                goals_for = team_stats.get("goals", {}).get("for", {}).get("total", {}).get("total", 0) or 0
                goals_against = team_stats.get("goals", {}).get("against", {}).get("total", {}).get("total", 0) or 0
                if total_played > 0:
                    team_gpg = goals_for / total_played
                    team_ga_pg = goals_against / total_played
            if opponent_stats:
                opp_played = (opponent_stats.get("fixtures", {}).get("played", {}).get("total") or 0)
                opp_gf = opponent_stats.get("goals", {}).get("for", {}).get("total", {}).get("total", 0) or 0
                opp_ga = opponent_stats.get("goals", {}).get("against", {}).get("total", {}).get("total", 0) or 0
                if opp_played > 0:
                    opp_gpg = opp_gf / opp_played
                    opp_ga_pg = opp_ga / opp_played

            # Expected total goals in match = (team_gpg + opp_ga_pg)/2 + (opp_gpg + team_ga_pg)/2
            if team_gpg > 0 or opp_gpg > 0:
                expected_team_goals = (team_gpg + opp_ga_pg) / 2.0
                expected_opp_goals = (opp_gpg + team_ga_pg) / 2.0
                expected_total = expected_team_goals + expected_opp_goals

                # Signal 2: Odds-implied over/under (if available)
                if match_odds and match_odds.get("bookmakerOdds"):
                    try:
                        home_odds = float(match_odds["bookmakerOdds"].get("homeWin", 3.0))
                        away_odds = float(match_odds["bookmakerOdds"].get("awayWin", 3.0))
                        # Low home+away odds = both teams expected to score
                        total_implied = 1.0/max(home_odds, 1.01) + 1.0/max(away_odds, 1.01)
                        if total_implied > 0.65:  # Both teams strong favorites to score
                            expected_total += 0.3
                            game_tempo["notes"].append("Odds suggest competitive match")
                    except Exception:
                        pass

                # Classify tempo
                if expected_total >= 3.2:
                    game_tempo["expectedTempo"] = "high"
                    # High-tempo: scale up pass volume by 4-8%
                    tempo_boost = min(0.08, (expected_total - 2.5) * 0.04)
                    game_tempo["tempoMultiplier"] = round(1.0 + tempo_boost, 3)
                    game_tempo["notes"].append(f"High-tempo expected ({expected_total:.1f} total goals) → +{tempo_boost*100:.0f}% pass boost")
                elif expected_total <= 1.8:
                    game_tempo["expectedTempo"] = "low"
                    # Low-tempo: dampen pass volume by 3-6%
                    tempo_drop = max(-0.06, -(2.5 - expected_total) * 0.03)
                    game_tempo["tempoMultiplier"] = round(1.0 + tempo_drop, 3)
                    game_tempo["notes"].append(f"Low-tempo expected ({expected_total:.1f} total goals) → {tempo_drop*100:.0f}% pass reduction")
                else:
                    game_tempo["expectedTempo"] = "normal"
                    game_tempo["tempoMultiplier"] = 1.0

                game_tempo["expectedTotalGoals"] = round(expected_total, 2)
                game_tempo["teamGPG"] = round(team_gpg, 2)
                game_tempo["oppGPG"] = round(opp_gpg, 2)

            if game_tempo["notes"]:
                print(f"[GAME TEMPO] {req.playerName}: tempo={game_tempo['expectedTempo']}, mult={game_tempo['tempoMultiplier']}, goals={game_tempo.get('expectedTotalGoals', '?')}")
        except Exception as e:
            print(f"[GAME TEMPO] Error: {e}")

        # =============================================
        # HEAVY FAVORITE DAMPENING — for OVER pass props
        # When a team is a heavy favorite (odds < 1.6), they're likely
        # to score early and then reduce passing tempo (game management).
        # This creates a "leading-team tempo drop" effect.
        # =============================================
        favorite_dampening = {"applied": False}
        try:
            poss_sensitive_for_fav = {"pass_attempts", "passes", "key_passes", "crosses"}
            if req.propType in poss_sensitive_for_fav and match_odds and match_odds.get("bookmakerOdds"):
                home_odds = float(match_odds["bookmakerOdds"].get("homeWin", 3.0))
                away_odds = float(match_odds["bookmakerOdds"].get("awayWin", 3.0))
                # Use fixture's playerIsHome tag so we pick the right odds regardless
                # of whether player_venue matches the API-Football fixture designation.
                _pifh_damp = match_odds.get("playerIsHome", player_venue == "home")
                team_odds = home_odds if _pifh_damp else away_odds

                if team_odds < 1.60:
                    # Heavy favorite — game management likely in 2nd half
                    # The heavier the favorite, the stronger the dampening
                    fav_dampen = round(min(0.06, (1.60 - team_odds) * 0.10), 3)
                    favorite_dampening = {
                        "applied": True,
                        "teamOdds": team_odds,
                        "dampeningFactor": fav_dampen,
                        "note": f"Heavy favorite ({team_odds:.2f}): leading teams reduce tempo → -{fav_dampen*100:.0f}% pass dampening"
                    }
                    print(f"[FAVORITE DAMPENING] {req.playerName}: odds={team_odds:.2f}, dampen={fav_dampen*100:.0f}%")
        except Exception as e:
            print(f"[FAVORITE DAMPENING] Error: {e}")

        print(f"[TIMING] Wave 2: {_t.time()-_t0:.1f}s total")

        historical_data = {
            "playerStats": player_stats,
            "teamStats": team_stats,
            "opponentStats": opponent_stats,
            "h2hData": h2h_data,
            "standings": standings,
            "recentFixtures": recent_fixtures,
            "matchOdds": match_odds,
        }

        # =============================================
        # Per-fixture deep data (Wave 2 results)
        # =============================================
        if team_fixture_stats:
            historical_data["teamMatchStats"] = team_fixture_stats
        if opponent_fixture_stats:
            historical_data["opponentMatchStats"] = opponent_fixture_stats
        if matchup_volume.get("available"):
            historical_data["matchupVolume"] = matchup_volume
        if player_game_logs:
            # Customer-visible recent history must match the current fixture
            # context. Venue is always required; knockout fixtures additionally
            # use equivalent knockout-stage history (for example, a Super Cup
            # final with Champions League knockout matches). The full log set
            # remains available to deterministic math and shadow evidence.
            _history_view_logs = player_game_logs
            _history_view_context = {
                "mode": "unfiltered",
                "candidateCount": len(player_game_logs),
                "includedCount": len(player_game_logs),
                "excludedCount": 0,
                "label": "ALL HISTORY",
            }
            try:
                from competition_context import select_contextual_history

                _history_view_logs, _history_view_context = select_contextual_history(
                    player_game_logs,
                    competition_id=(
                        (match_odds or {}).get("matchLeagueId")
                        or league_id
                    ),
                    competition_name=(match_odds or {}).get("matchLeague") or "",
                    round_value=(match_odds or {}).get("matchRound") or "",
                    venue=player_venue,
                    include_all_venues=False,
                )
                if not _history_view_logs:
                    print(
                        f"[HISTORY CONTEXT] {req.playerName}: no matching "
                        f"{_history_view_context.get('label')} rows; display sample is unavailable"
                    )
                else:
                    print(
                        f"[HISTORY CONTEXT] {req.playerName}: "
                        f"{_history_view_context.get('label')} "
                        f"n={len(_history_view_logs)}/{len(player_game_logs)}"
                    )
            except Exception as _history_context_err:
                print(f"[HISTORY CONTEXT] unavailable: {_history_context_err}")

            # Add summary stats for the game logs
            target_field_map = {
                "pass_attempts":          "passes_total",
                "shots":                  "shots_total",
                "shots_on_target":        "shots_on",
                "tackles":                "tackles_total",
                "key_passes":             "passes_key",
                "shots_assisted":         "passes_key",
                "saves":                  "goals_saves",
                "interceptions":          "tackles_interceptions",
                "clearances":             "tackles_clearances",
                "blocks":                 "tackles_blocks",
                "dribbles":               "dribbles_attempts",
                "fouls_drawn":            "fouls_drawn",
                "fouls_committed":        "fouls_committed",
                "crosses":                "passes_crosses",
                "duels_won":              "duels_won",
                "yellow_cards":           "cards_yellow",
            }
            target_field = target_field_map.get(req.propType, "passes_total")
            values = [g.get(target_field) for g in _history_view_logs if g.get(target_field) is not None]
            minutes_list = [g.get("minutes", 0) for g in _history_view_logs if g.get("minutes")]
            per90_values = [g.get("targetStatPer90") for g in _history_view_logs if g.get("targetStatPer90") is not None]
            _last10_logs = sorted(
                _history_view_logs,
                key=lambda g: g.get("date", ""),
                reverse=True,
            )[:10]
            _tp_home = [
                float(g["teamPossession"]) for g in _last10_logs
                if g.get("venue") == "home" and g.get("teamPossession") is not None
            ]
            _tp_away = [
                float(g["teamPossession"]) for g in _last10_logs
                if g.get("venue") == "away" and g.get("teamPossession") is not None
            ]
            _history_possession_count = sum(
                1
                for _log in _history_view_logs
                if _log.get("teamPossession") is not None
                and _log.get("opponentPossession") is not None
            )
            _history_possession_status = (
                "verified"
                if _history_view_logs
                and _history_possession_count == len(_history_view_logs)
                else "partial"
                if _history_possession_count > 0
                else "unavailable"
            )
            _venue_history_sample = (
                sum(
                    1
                    for _log in player_game_logs
                    if _log.get("venue") == player_venue
                    and _log.get(target_field) is not None
                )
                if req.sport == "soccer" and player_venue
                else None
            )
            _venue_history_fallback = (
                req.sport == "soccer"
                and bool(player_venue)
                and (_venue_history_sample or 0) < _VENUE_HISTORY_TARGET
            )
            _model_history_logs = (
                [
                    _log for _log in player_game_logs
                    if _log.get("venue") == player_venue
                    and _log.get(target_field) is not None
                ]
                if req.sport == "soccer"
                and player_venue
                and not _venue_history_fallback
                else [
                    _log for _log in player_game_logs
                    if _log.get(target_field) is not None
                ]
            )
            _model_history_values = [
                _log.get(target_field) for _log in _model_history_logs
            ]
            _history_view_context["metadataCoverage"] = {
                "total": len(player_game_logs),
                "dated": sum(1 for _log in player_game_logs if _log.get("date")),
                "withVenue": sum(
                    1 for _log in player_game_logs if _log.get("venue") in {"home", "away"}
                ),
                "withOpponent": sum(1 for _log in player_game_logs if _log.get("opponent")),
            }

            def _hit_rate_packet(_values):
                if not _values or not req.line:
                    return None
                _over_hits = sum(1 for _value in _values if _value > req.line)
                _under_hits = sum(1 for _value in _values if _value < req.line)
                _push_hits = len(_values) - _over_hits - _under_hits
                return {
                    "overHits": _over_hits,
                    "underHits": _under_hits,
                    "pushHits": _push_hits,
                    "overPct": round(_over_hits / len(_values) * 100, 1),
                    "underPct": round(_under_hits / len(_values) * 100, 1),
                    "total": len(_values),
                }

            game_log_summary = {
                    "games": _history_view_logs,
                # Keep the venue-scoped view for model/context calculations,
                # but expose the complete verified archive for the customer
                # Recent Matches card. The UI can then show both venues
                # without relabeling mixed rows as the selected venue.
                    "allGames": sorted(
                    player_game_logs,
                    key=lambda g: str(g.get("date") or ""),
                    reverse=True,
                    )[:_RECENT_ARCHIVE_TARGET],
                "targetProp": req.propType,
                "sampleSize": len(values),
                "last10Count": len(_last10_logs),
                "tpHomeAvg": round(sum(_tp_home) / len(_tp_home), 1) if _tp_home else None,
                "tpAwayAvg": round(sum(_tp_away) / len(_tp_away), 1) if _tp_away else None,
                "tpHomeCount": len(_tp_home),
                "tpAwayCount": len(_tp_away),
                "possessionStatus": _history_possession_status,
                "possessionSource": (
                    "fixture_statistics"
                    if _history_possession_count > 0
                    else None
                ),
                "possessionAvailableGames": _history_possession_count,
                "possessionUnavailableGames": max(
                    0,
                    len(_history_view_logs) - _history_possession_count,
                ),
                "venueHistory": {
                    "selectedVenue": player_venue,
                    "target": _VENUE_HISTORY_TARGET,
                    "verifiedSampleSize": _venue_history_sample,
                    "status": (
                        "sufficient"
                        if not _venue_history_fallback
                        else "full_history_fallback"
                    ),
                    "fallback": "full_verified_history" if _venue_history_fallback else None,
                    "modelScope": (
                        "full_verified_history"
                        if _venue_history_fallback
                        else "selected_venue"
                    ),
                    "modelSampleSize": len(_model_history_values),
                },
            }
            _model_hit_rates = _hit_rate_packet(_model_history_values)
            if _model_hit_rates:
                game_log_summary["modelHitRates"] = _model_hit_rates
            if values:
                game_log_summary["rawAvg"] = round(sum(values) / len(values), 2)
                game_log_summary["rawMin"] = min(values)
                game_log_summary["rawMax"] = max(values)
                if len(values) >= 3:
                    game_log_summary["stdDev"] = round(stats_mod.stdev(values), 2)
                # Home/away splits
                home_vals = [g.get(target_field) for g in _history_view_logs if g.get("venue") == "home" and g.get(target_field) is not None]
                away_vals = [g.get(target_field) for g in _history_view_logs if g.get("venue") == "away" and g.get(target_field) is not None]
                if home_vals:
                    game_log_summary["homeAvg"] = round(sum(home_vals) / len(home_vals), 2)
                if away_vals:
                    game_log_summary["awayAvg"] = round(sum(away_vals) / len(away_vals), 2)
            # The split values shown in Recent Matches must describe the full
            # verified archive, not only the selected prediction venue.
            _all_home_values = [
                g.get(target_field)
                for g in player_game_logs
                if g.get("venue") == "home" and g.get(target_field) is not None
            ]
            _all_away_values = [
                g.get(target_field)
                for g in player_game_logs
                if g.get("venue") == "away" and g.get(target_field) is not None
            ]
            _all_history_values = [
                g.get(target_field)
                for g in player_game_logs
                if g.get(target_field) is not None
            ]
            if _all_home_values:
                game_log_summary["homeAvg"] = round(
                    sum(_all_home_values) / len(_all_home_values), 2
                )
            if _all_away_values:
                game_log_summary["awayAvg"] = round(
                    sum(_all_away_values) / len(_all_away_values), 2
                )
            game_log_summary["venueSampleSizes"] = {
                "home": len(_all_home_values),
                "away": len(_all_away_values),
            }
            if per90_values:
                game_log_summary["per90Avg"] = round(sum(per90_values) / len(per90_values), 2)
            if minutes_list:
                game_log_summary["avgMinutes"] = round(sum(minutes_list) / len(minutes_list), 1)
                game_log_summary["avgMinutesPerMatch"] = game_log_summary["avgMinutes"]
            if _all_history_values and req.line:
                over_hits = sum(1 for v in _all_history_values if v > req.line)
                under_hits = sum(1 for v in _all_history_values if v < req.line)
                push_hits = len(_all_history_values) - over_hits - under_hits
                game_log_summary["hitRates"] = {
                    "overHits": over_hits,
                    "underHits": under_hits,
                    "pushHits": push_hits,
                    "overPct": round(over_hits / len(_all_history_values) * 100, 1),
                    "underPct": round(under_hits / len(_all_history_values) * 100, 1),
                    "total": len(_all_history_values),
                }
                game_log_summary["archiveHitRates"] = game_log_summary["hitRates"]

            # ── Annotate each game log with opponent league rank ────────────────
            # Build a quick lookup: lowercased team name → rank from standings.
            # This lets the tile UI show "#7" without extra API calls.
            if standings:
                _rank_map: dict = {}
                for _s in standings:
                    _tname = (_s.get("team") or {}).get("name", "") if isinstance(_s.get("team"), dict) else str(_s.get("team", ""))
                    _rank = _s.get("rank")
                    if _tname and _rank:
                        _rank_map[_tname.lower().strip()] = _rank
                for _gl in game_log_summary["games"]:
                    _opp = (_gl.get("opponent") or "").lower().strip()
                    if _opp and _rank_map:
                        # Try exact match first, then fuzzy prefix match
                        _gl["oppRank"] = _rank_map.get(_opp) or next(
                            (v for k, v in _rank_map.items() if _opp in k or k in _opp), None
                        )

            # ── Quality flag + opponent tier per game log ──────────────────────
            # Standings-based rank only covers opponents that share the SAME
            # standings table as the current prediction's league_id. A
            # national team's game log frequently spans multiple confederations/
            # competitions (qualifying groups, playoffs, friendlies) that never
            # share one table — so most historical opponents would otherwise
            # get no tier at all. Fall back to the curated NATIONAL_TEAM_TIER
            # map (by opponent name) whenever a real rank isn't available.
            for _gl in game_log_summary["games"]:
                _mins = _gl.get("minutes", 0) or 0
                _gl["quality"] = _mins >= 60
                _opp_rank = _gl.get("oppRank")
                if _opp_rank is not None:
                    if _opp_rank <= 6:
                        _gl["oppTier"] = "ELITE"
                    elif _opp_rank <= 15:
                        _gl["oppTier"] = "STRONG"
                    elif _opp_rank <= 30:
                        _gl["oppTier"] = "MID"
                    else:
                        _gl["oppTier"] = "WEAK"
                else:
                    _opp_name = (_gl.get("opponent") or "").lower().strip()
                    _gl["oppTier"] = NATIONAL_TEAM_TIER.get(_opp_name)
                    if _gl["oppTier"] is None and _opp_name:
                        _match = next((v for k, v in NATIONAL_TEAM_TIER.items() if _opp_name in k or k in _opp_name), None)
                        _gl["oppTier"] = _match

            # ── Quality-filtered hit rates (≥60 min games only) ───────────────
            if req.line and "hitRates" in game_log_summary:
                _qual_vals = [
                    g.get(target_field) for g in game_log_summary["games"]
                    if g.get(target_field) is not None and (g.get("minutes", 0) or 0) >= 60
                ]
                if _qual_vals:
                    _q_over = sum(1 for v in _qual_vals if v > req.line)
                    _q_under = sum(1 for v in _qual_vals if v < req.line)
                    game_log_summary["hitRates"]["qualityTotal"] = len(_qual_vals)
                    game_log_summary["hitRates"]["qualityOverHits"] = _q_over
                    game_log_summary["hitRates"]["qualityOverPct"] = round(_q_over / len(_qual_vals) * 100, 1)

            historical_data["playerGameLogs"] = game_log_summary
            historical_data["playerGameLogs"]["historyContext"] = _history_view_context

        # ── COMPETITION-AWARE HISTORICAL EVIDENCE ─────────────────────────────
        # This packet is built for every supported soccer prop type from the
        # same verified player-game logs used by the Reverse Formula.  It is
        # intentionally shadow-only: competition/stage evidence is auditable
        # now, but cannot alter the projection before leakage-safe replay.
        competition_context = {
            "version": "competition-context-v1",
            "available": False,
            "shadowOnly": True,
            "projectionAdjustmentStatus": "shadow_only",
            "projectionAdjustment": 0.0,
            "reason": "No verified player history was available.",
        }
        try:
            from competition_context import build_competition_context

            _cc_match = match_odds or {}
            competition_context = build_competition_context(
                player_game_logs,
                prop_type=req.propType,
                competition_id=(
                    _cc_match.get("matchLeagueId")
                    or league_id
                ),
                competition_name=(
                    _cc_match.get("matchLeague")
                    or ""
                ),
                round_value=_cc_match.get("matchRound") or "",
                venue=player_venue,
                position=locals().get("specific_position") or "",
                role=locals().get("player_role") or "",
                line=req.line,
            )
            historical_data["competitionContext"] = competition_context
            _cc_selected = competition_context.get("selected") or {}
            print(
                f"[COMPETITION CONTEXT] {req.playerName}/{req.propType}: "
                f"target={(_cc_match.get('matchLeague') or league_id)!r} "
                f"stage={_cc_match.get('matchRound') or 'unknown'!r} "
                f"source={_cc_selected.get('sourceLevel')} "
                f"n={_cc_selected.get('sampleSize', 0)}"
            )
        except Exception as _cc_err:
            # Evidence enrichment must never block a deterministic prediction.
            print(f"[COMPETITION CONTEXT] unavailable: {_cc_err}")

        # =============================================
        # EARLY BAYESIAN — Compute math BEFORE structured evidence assembly
        # This anchors the AI's reasoning so it doesn't
        # contradict the mathematical evidence.
        # =============================================
        early_bayes = None
        bayesian_prompt_anchor = ""
        _pressure_response = {
            "version": "pressure-response-v1",
            "status": "not_applicable",
            "classification": "unknown",
            "label": "Not applicable",
            "pressureMultiplier": 1.0,
            "projectionAdjustment": 0.0,
            "projectionAdjustmentStatus": "shadow_only",
        }
        _gk_pool_prior = {
            "version": "gk-pool-prior-v1",
            "status": "not_applicable",
            "mode": os.environ.get("GK_POOL_PRIOR_MODE", "shadow"),
            "requestedMode": os.environ.get("GK_POOL_PRIOR_MODE", "shadow"),
            "livePromotionRequested": False,
            "applied": False,
            "projectionAdjustmentStatus": "shadow_only",
            "poolMean": None,
            "poolRows": 0,
            "poolPlayers": 0,
            "reason": "Not a goalkeeper pass-attempt prediction.",
        }
        # Safety defaults for T003/T004 — always defined even if exception occurs
        _redist_alerts: list = []
        _redist_multiplier: float = 1.0
        _lineup_alert: str | None = None
        _lineup_status: str = "unknown"
        _lineup_confidence_floor: float | None = None
        _lineup_raw_preflight = None
        _quality_prior_applied: bool = False
        _quality_prior_dropped: int = 0
        _opp_tier_filter_applied: bool = False
        _opp_tier_filter_dropped: int = 0
        _opp_tier_filter_kept_tiers: list = []
        try:
            from bayesian_engine import compute_bayesian_projection, gaussian_likelihood_update
            if req.sport == "soccer" and req.propType in {"pass_attempts", "passes"}:
                try:
                    from pressure_response import classify_pressure_response
                    _pressure_response = classify_pressure_response(
                        player_game_logs,
                        expected_possession=(match_dominance or {}).get("expectedPoss"),
                        possession_is_real=bool((match_dominance or {}).get("seasonAvgIsReal")),
                    )
                    print(
                        f"[PRESSURE RESPONSE] {req.playerName}: "
                        f"{_pressure_response.get('label')} "
                        f"mult={_pressure_response.get('pressureMultiplier')} "
                        f"high_n={_pressure_response.get('highPressureSamples', 0)} "
                        f"low_n={_pressure_response.get('lowPressureSamples', 0)}"
                    )
                except Exception as _pressure_err:
                    print(f"[PRESSURE RESPONSE] non-fatal: {_pressure_err}")

            # ── Quick position cache lookup (fast indexed read) ──────────────
            # We look up the cached position so the engine can apply the correct
            # momentum decay table AND the position-aware press multiplier
            # (attackers decay faster, GKs decay slower; defenders get press boost).
            #
            # The cache is written by the [POS RESOLVE] block keyed on playerId,
            # Prefer playerId-keyed entries (written by the stats-aware resolver
            # with a versioned prompt). Fall back to playerName only when there
            # is no playerId entry — avoids stale batch-resolver entries that are
            # stored by name only and may have wrong positions (e.g. Vitinha=CB).
            _bayes_position = ""
            _bayes_role     = ""
            try:
                _pos_doc = await db.player_positions.find_one(
                    {"playerId": req.playerId}
                ) if req.playerId else None
                if not _pos_doc:
                    _pos_doc = await db.player_positions.find_one(
                        {"playerName": req.playerName, "playerId": {"$exists": True}}
                    )
                if _pos_doc:
                    _bayes_position = _pos_doc.get("specificPosition", "")
                    _bayes_role     = _pos_doc.get("role", "")
            except Exception:
                pass

            # The selection-time position is the trusted identity context for
            # this request. Do not let a stale cache row silently change the
            # role-aware Press Intensity multiplier before the later display
            # resolver gets a chance to restore the verified position.
            if req.positionOverride:
                _bayes_position = req.positionOverride
            if req.roleOverride:
                _bayes_role = req.roleOverride

            # ── GK detection — always override for saves prop ────────────────
            # "saves" is an exclusively GK stat. If the position cache has a
            # stale/wrong outfield entry (e.g. Oblak cached as "RB"), every
            # downstream GK-specific branch misfires: opponent-concession cap,
            # press boost, venue-split threshold, inverted possession model.
            # Guard: always force GK when propType is saves, regardless of cache.
            if req.propType in {"saves", "goalie_saves"}:
                _bayes_position = "GK"
            elif not _bayes_position:
                if req.propType in {"pass_attempts", "passes"}:
                    # Any saves value in logs = goalkeeper
                    if any(g.get("goals_saves") is not None and g.get("goals_saves", -1) >= 0
                           for g in player_game_logs):
                        _bayes_position = "GK"

            # ── Hyperprior for low-sample players (n < 6) ───────────────────
            # Derive a league-context anchor from opponent fixture stats.
            # Same field map as _estimate_opponent_concession in bayesian_engine.
            # If a player has very few logs this pulls the prior toward the
            # "typical output for this prop type in this match context."
            _bayes_hyperprior = None
            _hp_map = {
                "shots":           ("totalShots",     0.18),
                "shots_on_target": ("shotsOnTarget",  0.18),
                "goals":           ("goals",           0.40),
                "assists":         ("goals",           0.25),
                "saves":           ("shotsOnTarget",   0.70),
                "goalie_saves":    ("shotsOnTarget",   0.70),
                "tackles":         ("totalPasses",     0.015),
                "key_passes":      ("keyPasses",       0.28),
                "crosses":         ("totalCrosses",    0.35),
                "interceptions":   ("totalInterceptions", 0.22),
                "clearances":      ("totalClearances", 0.18),
                "dribbles":        ("dribbleAttempts", 0.30),
                "fouls_drawn":     ("foulsDrawn",      0.25),
                "fouls_committed": ("foulsCommitted",  0.22),
                "duels_won":       ("totalDuels",      0.22),
            }
            if opponent_fixture_stats and len(player_game_logs) < 6:
                _hp_entry = _hp_map.get(req.propType)
                if _hp_entry:
                    _hp_field, _hp_share = _hp_entry
                    _hp_vals = [
                        s.get(_hp_field) for s in opponent_fixture_stats
                        if s.get(_hp_field) is not None
                    ]
                    if len(_hp_vals) >= 3:
                        _bayes_hyperprior = (sum(_hp_vals) / len(_hp_vals)) * _hp_share

            # ── Expected minutes for this match ─────────────────────────────
            # Use the MEDIAN of the player's recent minutes to estimate playing
            # time. Median is more robust than mean — one 120-min ET game won't
            # inflate the expectation. Clamp to [30, 90].
            _all_mins = sorted([
                g.get("minutes", 90) for g in player_game_logs
                if (g.get("minutes") or 0) > 0
            ])
            if _all_mins:
                _mid = len(_all_mins) // 2
                _exp_mins = (_all_mins[_mid] if len(_all_mins) % 2 == 1
                             else (_all_mins[_mid - 1] + _all_mins[_mid]) / 2)
                _exp_mins = max(30.0, min(90.0, _exp_mins))
            else:
                _exp_mins = 90.0

            # Fetch confirmed lineup status before the minutes model. The
            # API helper returns the response list directly; the old code
            # expected the raw provider envelope and silently missed starters.
            # A confirmed starter should not receive a rotation haircut merely
            # because recent appearances included managed minutes.
            if _sit_fixture_id and req.playerId:
                try:
                    _lineup_raw_preflight = await api_football_request(
                        "fixtures/lineups", {"fixture": _sit_fixture_id}
                    )
                    _lineup_status = _lineup_player_status(
                        _lineup_raw_preflight, req.playerId
                    )
                    if _lineup_status == "starting":
                        _lineup_alert = "✓ Confirmed in starting XI"
                        _exp_mins = max(_exp_mins, 90.0)
                        print(
                            f"[LINEUP PREFLIGHT] {req.playerName}: confirmed STARTING "
                            f"→ expected minutes={_exp_mins:.1f}"
                        )
                    elif _lineup_status == "substitute":
                        _lineup_alert = "⚠ Listed as substitute — reduced involvement expected"
                        _lineup_confidence_floor = 0.45
                    elif _lineup_status == "not_in_squad":
                        _lineup_alert = "⚠ Player not found in confirmed lineup"
                        _lineup_confidence_floor = 0.45
                except Exception as _lineup_preflight_err:
                    print(
                        f"[LINEUP PREFLIGHT] fetch error for fixture {_sit_fixture_id}: "
                        f"{_lineup_preflight_err}"
                    )

            _sfm = {
                "goals": "goals_total", "assists": "goals_assists",
                "shots_assisted": "passes_key",
                "pass_attempts": "passes_total", "passes_attempted": "passes_total",
                "shots": "shots_total",
                "shots_on_target": "shots_on", "tackles": "tackles_total",
                "key_passes": "passes_key", "saves": "goals_saves",
                "goalie_saves": "goals_saves",
                "interceptions": "tackles_interceptions", "blocks": "tackles_blocks",
                "dribbles": "dribbles_attempts", "dribbles_success": "dribbles_success",
                "fouls_drawn": "fouls_drawn", "fouls_committed": "fouls_committed",
                "crosses": "passes_crosses", "clearances": "tackles_clearances",
                "duels_won": "duels_won", "yellow_cards": "cards_yellow",
            }
            # VENUE-SPLIT PRIOR for possession-sensitive props
            # Pass attempts/passes vary by 10-15 for GKs and 5-10 for outfield players
            # between home and away games. Using combined logs biases the prior toward
            # whichever venue had more recent games and systematically over/under-projects.
            # Fix: use only venue-matching logs as the primary sample when ≥5 are available.
            # Saves also differ by venue (away GKs face more shots) so apply the same logic.
            # Sort game logs newest-first so the Bayesian engine's momentum layer
            # (recent_5 = all_vals[:5]) correctly captures the most recent games.
            # The API returns fixtures in ascending date order; without this sort the
            # engine would apply the highest decay weight to the OLDEST game — completely
            # reversing the momentum signal (e.g. Cáceres showed COLD -11.5 momentum
            # when his true recent form was HOT +8.8, causing a wrong UNDER call).
            player_game_logs = sorted(
                player_game_logs,
                key=lambda g: g.get("date", ""),
                reverse=True,
            )

            # ── ROTATION RISK — minutes trend detection ───────────────────────
            # Detects whether a player is being rotated out (declining minutes)
            # or returning to full duty (increasing minutes) by comparing their
            # average minutes in the last 3 games vs games 4-8.
            #
            # Why median alone misses this: a player who played 90, 90, 90, 65,
            # 60, 58 has a median of ~77, completely hiding the clear trend.
            # The trend layer adjusts _exp_mins proportionally, capped at ±15%.
            _rotation_risk   = "stable"
            _rotation_adj_pct = 0.0
            try:
                _ordered_mins = [
                    float(g.get("minutes") or 0)
                    for g in player_game_logs
                    if (g.get("minutes") or 0) >= 20
                ]
                if len(_ordered_mins) >= 5:
                    _recent_3   = _ordered_mins[:3]
                    _prior_pool = _ordered_mins[3:min(8, len(_ordered_mins))]
                    _recent_avg = sum(_recent_3) / len(_recent_3)
                    _prior_avg  = sum(_prior_pool) / len(_prior_pool)
                    _delta      = _recent_avg - _prior_avg
                    # Require a meaningful absolute shift (≥8 min) to avoid
                    # noise from minor fixture-length variance (e.g. 88 vs 90).
                    if _prior_avg > 0 and abs(_delta) >= 8:
                        # Scale proportional to the magnitude of the drop/rise,
                        # but cap at ±15% so one anomalous sample can't swing
                        # the projection by an absurd amount.
                        _raw_adj = (_delta / _prior_avg)
                        if _delta < 0:
                            _rotation_risk    = "declining"
                            _rotation_adj_pct = max(-0.15, _raw_adj * 0.6)
                        else:
                            _rotation_risk    = "returning"
                            _rotation_adj_pct = min(0.10, _raw_adj * 0.4)
                        print(
                            f"[ROTATION] {req.playerName}: recent3={_recent_avg:.1f}min "
                            f"prior={_prior_avg:.1f}min delta={_delta:+.1f} "
                            f"→ {_rotation_risk} adj={_rotation_adj_pct:+.1%}"
                        )
            except Exception as _rot_err:
                print(f"[ROTATION] detection error: {_rot_err}")

            # A confirmed starter is expected to receive the full-match role
            # the market priced. Do not apply a recent-minutes haircut merely
            # because prior appearances included managed minutes. Keep the
            # observed trend in the audit fields/logs, but let confirmed
            # availability take precedence over that retrospective signal.
            if _lineup_status == "starting" and _rotation_adj_pct < 0:
                print(
                    f"[ROTATION] {req.playerName}: confirmed starter overrides "
                    f"negative minutes trend ({_rotation_adj_pct:+.1%})"
                )
                _rotation_adj_pct = 0.0
                _rotation_risk = "starting_full_minutes"

            # Apply rotation multiplier to the median-based expected minutes
            if _rotation_adj_pct != 0.0:
                _exp_mins = max(30.0, min(90.0, _exp_mins * (1.0 + _rotation_adj_pct)))
                print(f"[ROTATION] adjusted _exp_mins → {_exp_mins:.1f}min")

            _VENUE_SPLIT_PROPS = {"pass_attempts", "passes", "saves", "goalie_saves"}
            _bayes_logs = player_game_logs
            if league_id in INTERNATIONAL_LEAGUES:
                # International tournament (WC, Euros, Copa America, qualifiers, etc.):
                # player_game_logs are CLUB matches, unrelated to whether the player's
                # NATIONAL team is the effective home/away side in this fixture — pool
                # the full club log set as the prior instead of splitting by club venue.
                print(f"[INTL PRIOR] Skipping club venue split — using all {len(player_game_logs)} club logs")
            elif req.propType in _VENUE_SPLIT_PROPS and player_venue:
                _venue_logs = [
                    g for g in player_game_logs
                    if g.get("venue") == player_venue
                    and g.get(target_field) is not None
                ]
                # GK saves are HIGHLY venue-dependent (away GKs face far more shots
                # than home GKs — e.g. Oblak home avg 2.3 vs away avg 5.8). Using
                # combined logs when away samples exist biases the prior toward home
                # game values and systematically under-projects away GK saves.
                # Require the same 30-appearance venue target for goalkeeper
                # saves. A small venue slice can be directionally interesting,
                # but it must not replace the broad prior.
                _is_gk_saves = (
                    req.propType in {"saves", "goalie_saves"}
                    and _bayes_position.upper() in {"GK", "GOALKEEPER"}
                )
                _venue_min = _VENUE_HISTORY_TARGET
                if len(_venue_logs) >= _venue_min:
                    _bayes_logs = _venue_logs
                    print(
                        f"[VENUE PRIOR] {req.playerName}/{req.propType}: "
                        f"using {len(_venue_logs)} {player_venue} logs "
                        f"(dropped {len(player_game_logs) - len(_venue_logs)} opposite-venue logs, "
                        f"threshold={_venue_min})"
                    )
                else:
                    print(
                        f"[VENUE PRIOR] {req.playerName}/{req.propType}: "
                        f"only {len(_venue_logs)} {player_venue} logs — keeping combined {len(player_game_logs)}"
                    )

            # ── MANAGER CHANGE LOG SPLIT ──────────────────────────────────────────
            # When a recent coaching change is detected, pre-change game logs reflect
            # a completely different tactical system. Split at the change date and
            # use ONLY post-change logs as the Bayesian prior so the model prices
            # the new system — not a blended history from two different managers.
            #
            # Threshold: ≥ 3 post-change logs → use them exclusively.
            # < 3 post-change logs → keep combined (flag thin sample for AI + UI).
            _manager_split_info = {}
            if _manager_ctx.get("isRecent") and _manager_ctx.get("coachStartDate"):
                try:
                    from manager_tracker import detect_log_split as _dls
                    _post_logs, _pre_logs, _post_n, _pre_n = _dls(
                        _bayes_logs, _manager_ctx["coachStartDate"]
                    )
                    _sfm_field = _sfm.get(req.propType, "passes_total")
                    _pre_vals_ms  = [g.get(_sfm_field) for g in _pre_logs  if g.get(_sfm_field) is not None]
                    _post_vals_ms = [g.get(_sfm_field) for g in _post_logs if g.get(_sfm_field) is not None]
                    _pre_avg_ms   = round(sum(_pre_vals_ms)  / len(_pre_vals_ms),  1) if _pre_vals_ms  else None
                    _post_avg_ms  = round(sum(_post_vals_ms) / len(_post_vals_ms), 1) if _post_vals_ms else None
                    _manager_split_info = {
                        "postCount": _post_n, "preCount": _pre_n,
                        "preAvg":    _pre_avg_ms, "postAvg": _post_avg_ms,
                        "thinSample": _post_n < 5,
                    }
                    if _post_n >= 3:
                        _bayes_logs = _post_logs
                        print(
                            f"[MANAGER SPLIT] {req.playerName}: using {_post_n} post-"
                            f"{_manager_ctx.get('coachName','new manager')!r} logs "
                            f"(dropped {_pre_n} pre-change) | avg "
                            f"{_pre_avg_ms} → {_post_avg_ms} ({req.propType})"
                        )
                    else:
                        print(
                            f"[MANAGER SPLIT] {req.playerName}: only {_post_n} post-change "
                            f"logs — keeping combined {len(_bayes_logs)} (THIN SAMPLE)"
                        )
                except Exception as _msp_err:
                    print(f"[MANAGER SPLIT] error: {_msp_err}")

            # ── SAMPLE-QUALITY FILTER (luck strip) ───────────────────────
            # Drop garbage-time cameos and severe blowouts when we have
            # abundance — these samples are distorted by game state, not
            # representative of the player's normal output. Conservative:
            # never reduces sample size below 6.
            #
            # Gated behind env flag LUCK_STRIP_ENABLED=1 because we don't yet
            # have an empirical backtest proving it improves hit rate on this
            # specific dataset. When enabled, every filter event is logged so
            # the impact can be measured against settled outcomes over time.
            if os.environ.get("LUCK_STRIP_ENABLED") == "1":
                try:
                    from sample_quality import filter_low_quality_samples
                    _pre_n = len(_bayes_logs)
                    _bayes_logs, _drop_reasons = filter_low_quality_samples(_bayes_logs)
                    if _drop_reasons:
                        print(
                            f"[LUCK STRIP] {req.playerName}/{req.propType}: "
                            f"dropped {len(_drop_reasons)}/{_pre_n} samples "
                            f"({'; '.join(_drop_reasons[:3])}{'...' if len(_drop_reasons) > 3 else ''})"
                        )
                except Exception as _e:
                    print(f"[LUCK STRIP] skipped due to error: {_e}")

            # ── QUALITY PRIOR FILTER — exclude sub-60-min games from Bayesian prior ──
            # Cameos, cup rotations, and partial appearances produce stat lines that
            # are NOT representative of a player's full-game output. A player averaging
            # 36.7 passes in full games but only 31.1 across all games (including 19-min
            # substitute appearances) must have their prior anchored to the 36.7, not 31.1.
            # Only filters when enough full-game samples exist to maintain Bayesian stability.
            _MIN_QUALITY_BAYES = 6
            _quality_bayes_pool = [g for g in _bayes_logs if (g.get("minutes", 0) or 0) >= 60]
            if len(_quality_bayes_pool) >= _MIN_QUALITY_BAYES and len(_quality_bayes_pool) < len(_bayes_logs):
                _quality_prior_dropped = len(_bayes_logs) - len(_quality_bayes_pool)
                _bayes_logs = _quality_bayes_pool
                _quality_prior_applied = True
                print(
                    f"[QUALITY PRIOR] {req.playerName}/{req.propType}: "
                    f"dropped {_quality_prior_dropped} sub-60-min game{'s' if _quality_prior_dropped != 1 else ''} from prior, "
                    f"using {len(_quality_bayes_pool)} full-game logs"
                )

            # ── OPPONENT TIER AUTO-FILTER ─────────────────────────────────
            # If the current opponent is ELITE/STRONG, the prior should only
            # draw from games where the player faced comparably tough sides.
            # Games vs weak opposition skew the prior optimistically for an
            # ELITE opponent (opponent parks less, presses more, concedes
            # fewer touches). Filter stacks on top of the 60-min filter.
            _cur_opp_rank_for_tier = (standing_data or {}).get("oppRank")
            if _cur_opp_rank_for_tier is not None:
                if _cur_opp_rank_for_tier <= 15:
                    _keep_tiers = {"ELITE", "STRONG"}          # facing top-15: only top-15 history
                elif _cur_opp_rank_for_tier <= 30:
                    _keep_tiers = {"ELITE", "STRONG", "MID"}   # facing mid: exclude weak history
                else:
                    _keep_tiers = None                          # facing weak: no tier filter needed
                if _keep_tiers:
                    # Keep games vs matching tiers; keep unknowns (oppTier=None) conservatively
                    _tier_pool = [
                        g for g in _bayes_logs
                        if g.get("oppTier") in _keep_tiers or g.get("oppTier") is None
                    ]
                    if len(_tier_pool) >= _MIN_QUALITY_BAYES and len(_tier_pool) < len(_bayes_logs):
                        _opp_tier_filter_dropped = len(_bayes_logs) - len(_tier_pool)
                        _opp_tier_filter_kept_tiers = sorted(
                            _keep_tiers, key=lambda t: {"ELITE": 0, "STRONG": 1, "MID": 2, "WEAK": 3}.get(t, 4)
                        )
                        _bayes_logs = _tier_pool
                        _opp_tier_filter_applied = True
                        print(
                            f"[OPP TIER FILTER] {req.playerName}/{req.propType}: "
                            f"opp_rank={_cur_opp_rank_for_tier}, kept={_opp_tier_filter_kept_tiers}, "
                            f"dropped {_opp_tier_filter_dropped} games, using {len(_tier_pool)} remaining"
                        )

            # ── LEAGUE-EMPIRICAL CALIBRATION lookup ──────────────────────
            # Returns a small, well-shrunken multiplicative nudge on the
            # posterior, derived from settled-pick history of this exact
            # (league, position, prop, side) bucket.
            _league_calib = None
            try:
                from league_priors import lookup as _league_lookup, ensure_loaded as _ensure_lp
                # Make sure the cache is warm (no-op if already loaded recently)
                await _ensure_lp(db)
                # Pass BOTH sides of the bucket — over/under are independently
                # estimated populations, so we let the engine pick the bucket
                # that matches the side we end up recommending.
                _league_calib = {
                    "over":  _league_lookup(
                        league_id=req.leagueId or league_id,
                        position=_bayes_position,
                        prop_type=req.propType,
                        recommendation="over",
                        posterior_mean=req.line,
                    ),
                    "under": _league_lookup(
                        league_id=req.leagueId or league_id,
                        position=_bayes_position,
                        prop_type=req.propType,
                        recommendation="under",
                        posterior_mean=req.line,
                    ),
                }
            except Exception as _lc_err:
                print(f"[LEAGUE CALIB] lookup failed: {_lc_err}")

            # ── GAME-SCRIPT extraction from Vegas odds (already fetched) ─
            # We derive expected_total_goals + expected_goal_diff so the engine
            # can apply chase-mode / nailbiter nudges (cheat-sheet patterns).
            # ALSO produce a scenario probability vector (P_draw, P_low_scoring,
            # ...) used by the new scenario_priors layer.
            _game_script = None
            _scenario_probs = None
            try:
                from game_script_engine import compute_scenario_probs, expected_total_from_game_tempo
                _bo = (match_odds or {}).get("bookmakerOdds") if match_odds else None
                _gt_local = locals().get("game_tempo") or {}
                _expected_total = expected_total_from_game_tempo(_gt_local) or 2.6
                _scenario_probs = compute_scenario_probs(_bo, _expected_total)
                if _bo and _scenario_probs.get("available"):
                    _expected_diff = (_scenario_probs["impliedHome"]
                                      - _scenario_probs["impliedAway"]) * 2.5
                    # NEUTRAL-VENUE FIX: `player_venue` is forced to "neutral" for
                    # non-host World Cup / tournament fixtures (see venueOverride
                    # logic client-side), which previously caused every venue-gated
                    # game-script boost below (CB managing-lead, CDM chase-mode, GK
                    # high-scoring) to silently never fire — even for a huge favourite
                    # like Argentina vs Cape Verde. `playerIsHome` reflects the
                    # fixture's true home/away slot (from the odds/fixture data)
                    # regardless of the neutral-venue display label, so the engine can
                    # still tell which side is favoured. Falls back to the real venue
                    # when not neutral, and to None (skip) when truly unknown.
                    _pih_for_script = (match_odds or {}).get("playerIsHome")
                    if _pih_for_script is None and not _is_neutral:
                        _pih_for_script = (player_venue == "home")
                    _game_script = {
                        "expected_total_goals": _scenario_probs["expectedTotal"],
                        "expected_goal_diff":   round(_expected_diff, 2),
                        "implied_home":         _scenario_probs["impliedHome"],
                        "implied_away":         _scenario_probs["impliedAway"],
                        "player_is_home":       _pih_for_script,
                    }
            except Exception as _gs_err:
                print(f"[GAME SCRIPT] extraction failed: {_gs_err}")

            # ── CONDITIONAL POSSESSION ADJUSTMENT ────────────────────────────
            # Adjusts expectedPoss for game-state-conditional team style before
            # the Bayesian engine runs. France cedes possession when leading 1-0;
            # Morocco's CDM pass volume follows that shift upward. Spain doesn't
            # cede — their CDM numbers hold regardless of score.
            # Controlled by COND_POSS_MODE env var: off | shadow | live (default: live)
            _cond_poss_result = None
            _cond_poss_mode = os.environ.get("COND_POSS_MODE", "live").lower()
            try:
                from game_state_possession import (
                    PASS_ADJACENT_PROPS as _PASS_ADJ_PROPS,
                    compute_conditional_possession as _compute_cond_poss,
                )
                _cond_poss_eligible = (
                    _cond_poss_mode != "off"
                    and req.sport == "soccer"
                    and req.propType in _PASS_ADJ_PROPS
                    and match_dominance.get("seasonAvgIsReal", False)
                )
                if _cond_poss_eligible:
                    # Determine player_is_home: prefer game_script (fixture-derived),
                    # fall back to req.venue
                    _pih_cp = (player_venue == "home")
                    if _game_script is not None:
                        _gs_pih = _game_script.get("player_is_home")
                        if _gs_pih is not None:
                            _pih_cp = _gs_pih

                    # Derive implied win/loss probs: game_script > req.odds > balanced default
                    if _game_script is not None and _game_script.get("implied_home") is not None:
                        # p_trail = probability player's team loses this match
                        _cp_p_trail = (
                            float(_game_script["implied_away"]) if _pih_cp
                            else float(_game_script["implied_home"])
                        )
                        _cp_p_lead = (
                            float(_game_script["implied_home"]) if _pih_cp
                            else float(_game_script["implied_away"])
                        )
                    elif req.odds:
                        # Convert req.odds American lines → implied probs
                        _ro = req.odds if isinstance(req.odds, dict) else (req.odds.dict() if hasattr(req.odds, "dict") else {})
                        _h_ml = _ro.get("home") or _ro.get("homeOdds") or _ro.get("americanHome")
                        _a_ml = _ro.get("away") or _ro.get("awayOdds") or _ro.get("americanAway")
                        def _ml_to_prob(ml):
                            if ml is None: return 0.5
                            ml = float(ml)
                            return abs(ml) / (abs(ml) + 100) if ml < 0 else 100 / (ml + 100)
                        _h_raw = _ml_to_prob(_h_ml)
                        _a_raw = _ml_to_prob(_a_ml)
                        _tot_raw = _h_raw + _a_raw
                        _h_imp = _h_raw / _tot_raw if _tot_raw > 0 else 0.50
                        _a_imp = _a_raw / _tot_raw if _tot_raw > 0 else 0.50
                        _cp_p_trail = _a_imp if _pih_cp else _h_imp
                        _cp_p_lead  = _h_imp if _pih_cp else _a_imp
                    else:
                        # No odds signal — use balanced defaults (style still fires if opp_cede is strong)
                        _cp_p_trail = 0.33
                        _cp_p_lead  = 0.33
                    _cond_poss_result = await _compute_cond_poss(
                        base_poss=match_dominance["expectedPoss"],
                        p_trail=_cp_p_trail,
                        p_lead=_cp_p_lead,
                        player_team_name=(
                            locals().get("corrected_team_name") or req.teamName or ""
                        ),
                        opp_team_name=req.opponentName or "",
                        db=db,
                        team_fixture_stats=team_fixture_stats,
                        opp_fixture_stats=opponent_fixture_stats,
                    )
                    if _cond_poss_result and _cond_poss_result.get("adjusted_poss"):
                        if _cond_poss_mode == "live":
                            _cp_old = match_dominance["expectedPoss"]
                            match_dominance["expectedPoss"] = _cond_poss_result["adjusted_poss"]
                            match_dominance["notes"].append(
                                f"Conditional poss: {_cp_old:.0f}%→{_cond_poss_result['adjusted_poss']:.1f}% "
                                f"(Δ{_cond_poss_result['delta_pp']:+.1f}pp, "
                                f"p_trail={_cp_p_trail:.2f}, "
                                f"opp_cede={_cond_poss_result['opp_style'].get('possession_cede_when_leading', 0):.2f})"
                            )
                        else:
                            print(
                                f"[COND POSS SHADOW] {req.playerName}: "
                                f"would adjust {match_dominance['expectedPoss']:.0f}% → "
                                f"{_cond_poss_result['adjusted_poss']:.1f}%"
                            )
            except Exception as _cp_err:
                print(f"[COND POSS] Error: {_cp_err}")

            # ── SCENARIO PRIORS lookup (cheat-sheet conditional layer) ────
            # Mode controlled by env var SCENARIO_PRIORS_MODE: off|shadow|live
            # Default = shadow (compute & log, do NOT change projection).
            _scenario_priors_result = None
            _scen_mode = os.environ.get("SCENARIO_PRIORS_MODE", "live").lower()
            if _scen_mode not in {"off", "shadow", "live"}:
                _scen_mode = "shadow"
            if _scen_mode != "off" and _scenario_probs and _scenario_probs.get("available"):
                try:
                    from scenario_priors import (lookup_weighted as _scen_lookup,
                                                 ensure_loaded as _ensure_scen)
                    await _ensure_scen(db)
                    # Look up BOTH sides; the engine has already chosen which
                    # to apply by the time scenario_priors runs in shadow/live.
                    # We emit both so the inspector and downstream consumers
                    # can see what each side would have done.
                    _scen_over = _scen_lookup(_scenario_probs, _bayes_position,
                                              req.propType, "over",
                                              posterior_mean=req.line)
                    _scen_under = _scen_lookup(_scenario_probs, _bayes_position,
                                               req.propType, "under",
                                               posterior_mean=req.line)
                    # Pick the bucket that matches the side we'll likely
                    # recommend (compare line vs. baseline). The engine itself
                    # will not re-choose — it consumes whatever we hand it.
                    _scenario_priors_result = (_scen_over if _scen_over.get("found")
                                               else _scen_under)
                    if _scenario_priors_result and _scenario_priors_result.get("found"):
                        _scenario_priors_result["sideOver"]  = _scen_over
                        _scenario_priors_result["sideUnder"] = _scen_under
                except Exception as _sp_err:
                    print(f"[SCENARIO PRIORS] lookup failed: {_sp_err}")

            # ── ODDS-TIER PRIORS lookup ("alive" self-learning layer) ──────
            # Mode controlled by env var ODDS_TIER_PRIORS_MODE: off|shadow|live
            # Default = shadow (compute & log, do NOT change projection yet).
            _odds_tier_priors_result = None
            _ot_mode = os.environ.get("ODDS_TIER_PRIORS_MODE", "shadow").lower()
            if _ot_mode not in {"off", "shadow", "live"}:
                _ot_mode = "shadow"
            _odds_tier = "unknown"
            try:
                if _ot_mode != "off":
                    from odds_tier_priors import (lookup_single as _ot_lookup,
                                                 odds_tier_from_moneyline as _ot_from_ml,
                                                 odds_tier_from_possession as _ot_from_poss,
                                                 ensure_loaded as _ensure_ot)
                    await _ensure_ot(db)
                    # Resolve odds tier deterministically: moneyline first, then
                    # projected possession (already computed by match_dominance).
                    if match_odds and match_odds.get("americanOdds"):
                        _odds_tier = _ot_from_ml(match_odds["americanOdds"], player_venue)
                    elif not match_dominance.get("hasRealPossData"):
                        # No moneyline AND no real possession/standings/odds signal
                        # (compute_match_dominance left pure 50/50 defaults with no
                        # notes) — e.g. an international friendly vs a minnow with
                        # sparse pre-match data. Do NOT let a fake "close" tier feed
                        # the odds-tier-priors nudge; "unknown" finds no bucket and
                        # lookup_single() correctly applies zero adjustment instead.
                        _odds_tier = "unknown"
                    else:
                        # match_dominance["expectedPoss"]/["oppExpectedPoss"] are already
                        # remapped to the player's own team vs opponent (see remap logic
                        # above) — NOT a {"home":.., "away":..} dict. Pass them straight
                        # through as (team_poss, opp_poss) using the player's own venue.
                        _team_poss = match_dominance.get("expectedPoss")
                        _opp_poss = match_dominance.get("oppExpectedPoss")
                        if player_venue == "home":
                            _odds_tier = _ot_from_poss(_team_poss, _opp_poss, "home")
                        else:
                            _odds_tier = _ot_from_poss(_opp_poss, _team_poss, "away")
                    print(f"[ODDS TIER] {req.playerName} ({player_venue}): {_odds_tier} "
                          f"(from={'moneyline' if (match_odds and match_odds.get('americanOdds')) else ('projPoss' if match_dominance.get('hasRealPossData') else 'no-data')})")
                    # Look up BOTH sides; engine applies the one matching recommendation.
                    # Pass player_venue so the lookup can try the fine-grained
                    # (tier x pos x prop x side x venue) bucket first and fall
                    # back to the venue-agnostic bucket automatically.
                    _ot_over = _ot_lookup(_odds_tier, _bayes_position,
                                         req.propType, "over",
                                         posterior_mean=req.line,
                                         venue=player_venue)
                    _ot_under = _ot_lookup(_odds_tier, _bayes_position,
                                          req.propType, "under",
                                          posterior_mean=req.line,
                                          venue=player_venue)
                    _odds_tier_priors_result = (_ot_over if _ot_over.get("found")
                                                 else _ot_under)
                    if _odds_tier_priors_result and _odds_tier_priors_result.get("found"):
                        _odds_tier_priors_result["sideOver"]  = _ot_over
                        _odds_tier_priors_result["sideUnder"] = _ot_under
                        _odds_tier_priors_result["resolvedTier"] = _odds_tier
            except Exception as _ot_err:
                print(f"[ODDS-TIER PRIORS] lookup failed: {_ot_err}")

            # ── Ultra v4: compute 4 new Bayesian inputs ──────────────────────
            # 1. REST DAYS — days since player's team last played
            _rest_days_v4: int | None = None
            try:
                _match_date_str_v4 = (match_odds or {}).get("matchDate", "") or ""
                if _match_date_str_v4 and player_game_logs:
                    from datetime import date as _dt_v4
                    _md_obj = _dt_v4.fromisoformat(_match_date_str_v4[:10])
                    _last_dates = [
                        g.get("date", "")[:10] for g in player_game_logs
                        if g.get("date", "")[:10]
                    ]
                    if _last_dates:
                        _ld_obj = _dt_v4.fromisoformat(max(_last_dates))
                        _rest_days_v4 = max(0, (_md_obj - _ld_obj).days)
                        print(f"[REST DAYS] {req.playerName}: last={max(_last_dates)} "
                              f"match={_match_date_str_v4[:10]} → {_rest_days_v4}d rest")
            except Exception as _rd_err:
                print(f"[REST DAYS] err: {_rd_err}")

            # 2. OPPONENT CLEAN SHEET RATE — fraction of recent games opp kept CS
            _opp_cs_rate_v4: float | None = None
            try:
                _cs_vals = [
                    s.get("goals_conceded")
                    for s in (opponent_fixture_stats or [])
                    if s.get("goals_conceded") is not None
                ]
                if len(_cs_vals) >= 3:
                    _opp_cs_rate_v4 = round(
                        sum(1 for v in _cs_vals if v == 0) / len(_cs_vals), 3
                    )
                    print(f"[CS RATE] {req.opponentName}: "
                          f"cs={sum(1 for v in _cs_vals if v==0)}/{len(_cs_vals)} "
                          f"= {_opp_cs_rate_v4:.0%}")
            except Exception as _cs_err:
                print(f"[CS RATE] err: {_cs_err}")

            # 2b. TOURNAMENT GAME INDEX — derive from round string for compounding fatigue
            _tourn_game_idx = None
            _raw_round = (match_odds or {}).get("matchRound", "")
            if _raw_round:
                _round_digits = re.findall(r'\d+', _raw_round)
                if _round_digits:
                    _tourn_game_idx = int(_round_digits[0])
                elif "group" in _raw_round.lower():
                    _tourn_game_idx = 1
                elif any(k in _raw_round.lower() for k in ("round of", "16", "eighth")):
                    _tourn_game_idx = 4
                elif any(k in _raw_round.lower() for k in ("quarter", "qf")):
                    _tourn_game_idx = 5
                elif any(k in _raw_round.lower() for k in ("semi", "sf")):
                    _tourn_game_idx = 6
                elif any(k in _raw_round.lower() for k in ("final", "3rd", "third")):
                    _tourn_game_idx = 7

            # 3. ALTITUDE — high-altitude league mapping (away teams only)
            _HIGH_ALTITUDE_LEAGUES_V4 = {
                270: 3640,   # Bolivia (La Paz, Sucre) — Liga Profesional
                285: 2850,   # Ecuador (Quito) — Liga Pro
                239: 2640,   # Colombia (Bogotá) — Primera A
                262: 2240,   # Mexico (Mexico City) — Liga MX (moderate)
                300: 2800,   # Peru (Lima is sea-level but Cusco/Arequipa) — rough avg
            }
            _altitude_m_v4: int | None = None
            _lid_v4 = req.leagueId or locals().get("league_id")
            if _lid_v4 and _lid_v4 in _HIGH_ALTITUDE_LEAGUES_V4:
                # Only pass altitude for AWAY team (home teams are acclimatised)
                if player_venue == "away":
                    _altitude_m_v4 = _HIGH_ALTITUDE_LEAGUES_V4[_lid_v4]
                    print(f"[ALTITUDE] {req.opponentName} league={_lid_v4} "
                          f"altitude={_altitude_m_v4}m (away penalty active)")

            # 4. OPPONENT FOUL RATE — avg fouls/game from opponent's recent fixtures
            _opp_foul_rate_v4: float | None = None
            try:
                _foul_vals = [
                    s.get("fouls_committed_agg")
                    for s in (opponent_fixture_stats or [])
                    if s.get("fouls_committed_agg") is not None
                ]
                if len(_foul_vals) >= 2:
                    _opp_foul_rate_v4 = round(sum(_foul_vals) / len(_foul_vals), 1)
                    print(f"[FOUL RATE] {req.opponentName}: "
                          f"avg={_opp_foul_rate_v4:.1f} fouls/game "
                          f"(n={len(_foul_vals)})")
            except Exception as _fr_err:
                print(f"[FOUL RATE] err: {_fr_err}")

            # 5. DISMISSAL / RED-CARD RISK — combined card volatility for both teams.
            # Not a stat prediction; a volatility flag so users know a 10-man scenario
            # is a live possibility that can swing the whole match (and the prop).
            _risk_signals: dict = {"level": "normal", "note": None, "teamCardsAvg": None, "oppCardsAvg": None}
            try:
                def _avg_cards(fixture_stats):
                    yv = [s.get("cards_yellow_agg") for s in (fixture_stats or []) if s.get("cards_yellow_agg") is not None]
                    rv = [s.get("cards_red_agg") for s in (fixture_stats or []) if s.get("cards_red_agg") is not None]
                    if len(yv) < 2:
                        return None, None
                    y_avg = round(sum(yv) / len(yv), 2)
                    r_avg = round(sum(rv) / len(rv), 2) if rv else 0.0
                    return y_avg, r_avg

                _team_y, _team_r = _avg_cards(team_fixture_stats)
                _opp_y, _opp_r = _avg_cards(opponent_fixture_stats)
                _risk_signals["teamCardsAvg"] = _team_y
                _risk_signals["oppCardsAvg"] = _opp_y
                _combined_y = (_team_y or 0) + (_opp_y or 0)
                _combined_r = (_team_r or 0) + (_opp_r or 0)
                if _team_y is not None and _opp_y is not None:
                    if _combined_r >= 0.25 or _combined_y >= 5.0:
                        _risk_signals["level"] = "elevated"
                        _risk_signals["note"] = (
                            f"Elevated dismissal risk — combined card rate {_combined_y:.1f} yellow"
                            f"{f' / {_combined_r:.2f} red' if _combined_r else ''} per game across both sides. "
                            "A red card can flip possession/tempo and swing this prop either way."
                        )
                        print(f"[RISK] elevated dismissal risk: team={_team_y}/{_team_r} opp={_opp_y}/{_opp_r}")
                    elif _combined_y >= 3.8:
                        _risk_signals["level"] = "moderate"
                        _risk_signals["note"] = f"Moderate card volatility ({_combined_y:.1f} combined yellows/game)."
            except Exception as _risk_err:
                print(f"[RISK] err: {_risk_err}")
            # ─────────────────────────────────────────────────────────────────

            early_bayes = compute_bayesian_projection(
                game_logs=_bayes_logs,
                prop_type=req.propType,
                line=req.line,
                venue=player_venue,
                stat_field=_sfm.get(req.propType, "passes_total"),
                opponent_fixture_stats=opponent_fixture_stats,
                match_dominance=match_dominance,
                position=_bayes_position,
                hyperprior_mean=_bayes_hyperprior,
                expected_minutes=_exp_mins,
                league_calibration=_league_calib,
                game_script=_game_script,
                scenario_priors_result=_scenario_priors_result,
                scenario_priors_mode=_scen_mode,
                odds_tier_priors_result=_odds_tier_priors_result,
                odds_tier_priors_mode=_ot_mode,
                role=locals().get("player_role", ""),
                match_stakes={
                    **(game_situation.get("matchStakes") or {}),
                    # Inject live expectedPoss so Bayesian can gate the
                    # direct-play debuff when possession shows dominance
                    "teamExpectedPoss": match_dominance.get("expectedPoss", 50.0),
                    # H2H possession is displayed as context only. It does
                    # not bypass the independent ten-match venue gate.
                    "h2hPossAvg": None,
                    # World Cup: every match is max-stakes elimination pressure
                    "isWorldCup": _is_wc,
                },
                league_id=req.leagueId,
                # ── Ultra v4 new layers ────────────────────────────────────
                rest_days=_rest_days_v4,
                opponent_clean_sheet_rate=_opp_cs_rate_v4,
                altitude_m=_altitude_m_v4,
                opponent_foul_rate=_opp_foul_rate_v4,
                tournament_game_index=_tourn_game_idx,
                player_stats=player_stats,
            )
            if isinstance(early_bayes, dict):
                early_bayes["pressureResponse"] = _pressure_response
                early_bayes["goalkeeperPoolPrior"] = _gk_pool_prior
            _eb_samples = early_bayes.get("priorSamples", 0) if early_bayes else 0
            print(f"[BAYESIAN] {req.playerName}/{req.propType}: samples={_eb_samples}, logs={len(_bayes_logs)} (venue={player_venue})")

            # ── POSITIONAL ROLE BASELINE ──────────────────────────────────────
            # Reality-check: does the projection make sense for this position
            # in this possession context? A CDM who played for a high-possession
            # club (80-pass history) but is now at a low-possession club should
            # NOT be projected at 80 passes. The baseline knows what CDMs at
            # low-possession teams actually produce (median ~50) and squeezes
            # the projection back toward the realistic ceiling when sample count
            # is low enough that the player's personal history is still "tainted"
            # by a very different team context.
            # No squeeze at 8+ game logs — by then the player's own data is law.
            try:
                from positional_baseline import get_positional_baseline, apply_positional_squeeze
                _pos_for_baseline = (
                    _bayes_position
                    or locals().get("display_position", "")
                    or ""
                )
                _poss_for_baseline = match_dominance.get("expectedPoss", 50.0) if match_dominance else 50.0
                _team_avg_passes   = match_dominance.get("teamAvgPasses") if match_dominance else None
                _press_label       = None
                _pos_baseline = get_positional_baseline(
                    position=_pos_for_baseline,
                    expected_poss=_poss_for_baseline,
                    prop_type=req.propType,
                    role=_bayes_role,
                    team_avg_passes=_team_avg_passes,
                    press_intensity_label=_press_label,
                )
                if early_bayes and _pos_baseline:
                    _raw_pm = early_bayes.get("posteriorMean", req.line)
                    _adj_pm, _pos_note = apply_positional_squeeze(
                        posterior_mean=_raw_pm,
                        baseline=_pos_baseline,
                        n_samples=early_bayes.get("priorSamples", len(_bayes_logs)),
                    )
                    if _pos_note:
                        print(_pos_note)
                        early_bayes["posteriorMean"] = _adj_pm
                        # Recalculate recommendation direction from adjusted projection
                        early_bayes["recommendation"] = "over" if _adj_pm > req.line else "under"
                        _pos_baseline["squeezedFrom"] = _raw_pm
                        _pos_baseline["squeezedTo"]   = _adj_pm
                        _pos_baseline["note"] = _pos_note
                        # ── Recompute P(over)/P(under) from the adjusted mean ──────────
                        # When squeeze fires from n=0 centering, pOver/pUnder are still
                        # 50/50 from _empty_metrics.  Recompute from a normal distribution
                        # centered at _adj_pm with σ = IQR/1.35 (empirical normal approx).
                        try:
                            import math as _math
                            _bl_iqr = (_pos_baseline.get("p75", req.line) -
                                       _pos_baseline.get("p25", req.line))
                            _bl_std = _bl_iqr / 1.35 if _bl_iqr > 0 else max(req.line * 0.25, 1.0)
                            _z      = (_adj_pm - req.line) / max(_bl_std, 0.01)
                            _po_raw = 50.0 + 50.0 * _math.erf(_z / _math.sqrt(2))
                            _po     = round(max(1.0, min(99.0, _po_raw)), 1)
                            early_bayes["pOver"]  = _po
                            early_bayes["pUnder"] = round(100.0 - _po, 1)
                            print(f"[POS BASELINE] pOver recalc: adj_pm={_adj_pm:.2f} "
                                  f"line={req.line} std={_bl_std:.2f} → P(over)={_po:.1f}%")
                        except Exception as _po_err:
                            print(f"[POS BASELINE] pOver recalc failed (non-fatal): {_po_err}")
                    else:
                        _pos_baseline["note"] = "within realistic range — no adjustment"
                    early_bayes["positionalBaseline"] = _pos_baseline
            except Exception as _pb_err:
                print(f"[POS BASELINE] error (non-fatal): {_pb_err}")
            # ─────────────────────────────────────────────────────────────────

            # ── LOW-SAMPLE MID/CAM UNDER GUARD ───────────────────────────────
            # Evidence: CM/DLP UNDER picks have 0% win rate (4 picks, avg_err=+27.5).
            # CM/Mezzala UNDER: 33% win rate. CDM/Ball Winner UNDER: 54% (borderline).
            # When the engine has < 4 game logs AND projects significantly below the
            # line for a midfielder/attacker, the UNDER recommendation is unreliable —
            # the model is mostly anchored to the hyperprior, which is often too low.
            # Guard: cap pUnder at 65 in this scenario so the UI shows "Medium" not "High".
            _guard_positions = {"CM", "CDM", "CAM", "DM", "AM", "MF", "DMF", "OMF"}
            if (early_bayes
                    and req.propType in {"pass_attempts", "passes"}
                    and _bayes_position.upper() in _guard_positions
                    and early_bayes.get("recommendation") == "under"
                    and _eb_samples < 4):
                _proj = early_bayes.get("posteriorMean", req.line)
                _proj_ratio = _proj / req.line if req.line > 0 else 1.0
                if _proj_ratio < 0.88:
                    _old_pu = early_bayes.get("pUnder", 50)
                    if _old_pu > 65:
                        early_bayes["pUnder"] = 65.0
                        early_bayes["pOver"]  = 35.0
                        print(f"[LOW-SAMPLE UNDER GUARD] {req.playerName}/{req.propType}: "
                              f"samples={_eb_samples}, proj/line={_proj_ratio:.2f} "
                              f"pUnder {_old_pu:.1f}→65.0 (low data, mid UNDER unreliable)")

            # ── T003: Redistribution model ───────────────────────────────────
            # When a teammate of the same position is absent, the subject player
            # absorbs a portion of their typical contribution. We detect absences
            # from the situation-engine injury data and apply a per-prop-type
            # multiplier to the Bayesian posteriorMean.
            #
            # Position groups: A/F → attacker, M → midfielder, D → defender.
            # Redistribution only applies when >= 1 same-position teammate absent.
            # Cap: total boost ≤ 25%, never applied to goalkeepers (G).
            _player_team_absences = game_situation.get("injuries", {}).get("playerTeamAbsences", [])
            _redist_multiplier = 1.0

            # Map raw API-Football position codes → canonical group
            def _pos_group(pos_code: str) -> str:
                p = (pos_code or "").upper().strip()
                if p in ("A", "F", "ST", "CF", "LW", "RW", "LF", "RF", "SS"):
                    return "attacker"
                if p in ("M", "AM", "CM", "DM", "CAM", "CDM", "LM", "RM", "MF", "W"):
                    return "midfielder"
                if p in ("D", "CB", "LB", "RB", "LWB", "RWB", "SW", "DF"):
                    return "defender"
                return "other"

            # Determine subject player's position group
            _subject_pos_group = _pos_group(_bayes_position)

            # Redistribution table: (prop_type → boost per absent same-position teammate)
            # Boosts are fractional multipliers above 1.0; typical squad size per position:
            # attacker ~2, midfielder ~4, defender ~4 — so 1 absence = bigger impact for attacker
            _REDIST_TABLE = {
                "attacker": {
                    "goals": 0.12, "shots": 0.12, "shots_on_target": 0.10,
                    "key_passes": 0.07, "dribbles": 0.08, "dribbles_success": 0.07,
                    "assists": 0.06, "fouls_drawn": 0.05,
                },
                "midfielder": {
                    "pass_attempts": 0.08, "key_passes": 0.10, "assists": 0.08,
                    "tackles": 0.06, "interceptions": 0.06, "fouls_committed": 0.05,
                    "dribbles": 0.06, "crosses": 0.07,
                },
                "defender": {
                    "tackles": 0.10, "clearances": 0.12, "interceptions": 0.09,
                    "blocks": 0.08, "fouls_committed": 0.06, "duels_won": 0.07,
                    # Pass redistribution: when a fellow defender is absent, the remaining
                    # defenders take on more build-up passing — especially CBs in possession systems
                    "pass_attempts": 0.07, "passes": 0.07, "key_passes": 0.06, "crosses": 0.04,
                },
            }

            _redist_alerts = []
            if _subject_pos_group in _REDIST_TABLE and _player_team_absences:
                _prop_boosts = _REDIST_TABLE[_subject_pos_group]
                _per_absence_boost = _prop_boosts.get(req.propType, 0.0)
                if _per_absence_boost > 0:
                    _absent_same_pos = [
                        a for a in _player_team_absences
                        if _pos_group(a.get("position", "")) == _subject_pos_group
                    ]
                    if _absent_same_pos:
                        _raw_boost = len(_absent_same_pos) * _per_absence_boost
                        _capped_boost = min(_raw_boost, 0.25)
                        _redist_multiplier = 1.0 + _capped_boost
                        _absent_names = ", ".join(a["name"] for a in _absent_same_pos[:3])
                        _redist_alerts.append(
                            f"Redistribution: {len(_absent_same_pos)} same-position teammate(s) absent "
                            f"({_absent_names}) → +{round(_capped_boost*100)}% {req.propType} boost applied"
                        )
                        print(f"[REDIST] {req.playerName}/{req.propType}: "
                              f"×{_redist_multiplier:.3f} from {len(_absent_same_pos)} absence(s)")

            # Apply redistribution to early_bayes posteriorMean
            if early_bayes and _redist_multiplier != 1.0:
                _orig_pm = early_bayes["posteriorMean"]
                _new_pm  = round(_orig_pm * _redist_multiplier, 1)
                early_bayes["posteriorMean"] = _new_pm
                early_bayes["recommendation"] = "over" if _new_pm > req.line else "under"
                early_bayes["redistribution"] = {
                    "multiplier": round(_redist_multiplier, 3),
                    "originalMean": _orig_pm,
                    "adjustedMean": _new_pm,
                    "absentCount": len([a for a in _player_team_absences
                                        if _pos_group(a.get("position", "")) == _subject_pos_group]),
                }

            # ── Pitch diagram data — grid "row:col" (API-Football) -> normalized x,y ──
            def _grid_to_xy(grid: str, is_home: bool) -> tuple:
                try:
                    row, col = grid.split(":")
                    row, col = int(row), int(col)
                except Exception:
                    return (0.5, 0.5)
                # y: 0 = own goal line, 1 = opponent goal line. Home attacks "up" (y grows),
                # away is mirrored so both teams render facing each other on one pitch.
                y = min(0.92, 0.08 + (row - 1) * 0.20)
                if not is_home:
                    y = 1.0 - y
                # x spread within the row (col starts at 1)
                row_counts = {1: 1, 2: 5, 3: 5, 4: 5, 5: 3}
                n = max(row_counts.get(row, 4), col)
                x = (col) / (n + 1)
                return (round(x, 3), round(y, 3))

            def _build_pitch_team(team_lineup: dict, is_home: bool, target_id: int | None) -> dict:
                players = []
                for p in team_lineup.get("startXI", []):
                    pl = p.get("player", {})
                    x, y = _grid_to_xy(pl.get("grid") or "", is_home)
                    players.append({
                        "id": pl.get("id"),
                        "name": pl.get("name"),
                        "pos": pl.get("pos"),
                        "grid": pl.get("grid"),
                        "number": pl.get("number"),
                        "x": x, "y": y,
                        "isTarget": (
                            target_id is not None
                            and _normalize_provider_player_id(pl.get("id")) == target_id
                        ),
                    })
                return {
                    "formation": team_lineup.get("formation"),
                    "coach": (team_lineup.get("coach") or {}).get("name"),
                    "players": players,
                }

            _pitch_lineup: dict = {
                "status": "unavailable", "formation": None, "players": [],
                "opponentFormation": None, "opponentPlayers": [], "coach": None, "opponentCoach": None,
            }

            # ── T004: Lineup confirmation gate ───────────────────────────────
            # Fetch the confirmed starting XI for the upcoming fixture.
            # If available and the subject player is NOT in the XI → confidence floor.
            # If confirmed starting → positive tactical signal.
            if _sit_fixture_id and req.playerId:
                try:
                    _lineup_raw = (
                        _lineup_raw_preflight
                        if _lineup_raw_preflight is not None
                        else await api_football_request(
                            "fixtures/lineups", {"fixture": _sit_fixture_id}
                        )
                    )
                    _lineup_responses = _api_response_list(_lineup_raw)
                    _player_id_int = int(req.playerId) if str(req.playerId).isdigit() else None

                    if _lineup_responses:
                        # Build pitch data for both teams (confirmed)
                        try:
                            for _tl in _lineup_responses:
                                _tl_id = (_tl.get("team") or {}).get("id")
                                _is_home_tl = (_tl_id == _sit_home_id)
                                _team_pitch = _build_pitch_team(_tl, _is_home_tl, _player_id_int)
                                if _tl_id == _canonical_team_id:
                                    _pitch_lineup["formation"] = _team_pitch["formation"]
                                    _pitch_lineup["players"] = _team_pitch["players"]
                                    _pitch_lineup["coach"] = _team_pitch["coach"]
                                elif _tl_id == _canonical_opponent_id:
                                    _pitch_lineup["opponentFormation"] = _team_pitch["formation"]
                                    _pitch_lineup["opponentPlayers"] = _team_pitch["players"]
                                    _pitch_lineup["opponentCoach"] = _team_pitch["coach"]
                            if _pitch_lineup["players"] or _pitch_lineup["opponentPlayers"]:
                                _pitch_lineup["status"] = "confirmed"
                        except Exception as _pitch_err:
                            print(f"[PITCH] build error: {_pitch_err}")
                    else:
                        # Not posted yet — build a "predicted" XI from each team's most
                        # recent fixture lineup as a reasonable proxy (last-used shape/personnel).
                        try:
                            async def _last_lineup(team_id):
                                if not team_id:
                                    return None
                                _lf = await api_football_request(
                                    "fixtures", {"team": team_id, "last": 5}
                                )
                                _fx = _api_response_list(_lf)
                                for _fixture in _fx:
                                    _fid = (_fixture.get("fixture") or {}).get("id")
                                    if not _fid:
                                        continue
                                    _lu = await api_football_request("fixtures/lineups", {"fixture": _fid})
                                    for _tl in _api_response_list(_lu):
                                        if (
                                            (_tl.get("team") or {}).get("id") == team_id
                                            and _tl.get("formation")
                                            and len(_tl.get("startXI") or []) >= 8
                                        ):
                                            return _tl
                                return None

                            _own_last, _opp_last = await aio.gather(
                                _last_lineup(_canonical_team_id), _last_lineup(_canonical_opponent_id),
                                return_exceptions=True
                            )
                            if _own_last and not isinstance(_own_last, Exception):
                                _tp = _build_pitch_team(_own_last, _sit_is_home, _player_id_int)
                                _pitch_lineup["formation"] = _tp["formation"]
                                _pitch_lineup["players"] = _tp["players"]
                                _pitch_lineup["coach"] = _tp["coach"]
                            if _opp_last and not isinstance(_opp_last, Exception):
                                _tp = _build_pitch_team(_opp_last, not _sit_is_home, None)
                                _pitch_lineup["opponentFormation"] = _tp["formation"]
                                _pitch_lineup["opponentPlayers"] = _tp["players"]
                                _pitch_lineup["opponentCoach"] = _tp["coach"]
                            if _pitch_lineup["players"] or _pitch_lineup["opponentPlayers"]:
                                _pitch_lineup["status"] = "predicted"
                                print(f"[PITCH] predicted XI built from last-match lineups for {req.playerName}'s fixture")
                        except Exception as _pred_pitch_err:
                            print(f"[PITCH] predicted build error: {_pred_pitch_err}")
                    if _lineup_responses and _player_id_int and _lineup_status == "unknown":
                        # Determine which team the subject player belongs to by scanning both
                        for _team_lineup in _lineup_responses:
                            _starters = _team_lineup.get("startXI", [])
                            _subs     = _team_lineup.get("substitutes", [])
                            _starter_ids = {
                                p.get("player", {}).get("id")
                                for p in _starters
                                if p.get("player", {}).get("id") is not None
                            }
                            _sub_ids = {
                                p.get("player", {}).get("id")
                                for p in _subs
                                if p.get("player", {}).get("id") is not None
                            }
                            if _player_id_int in _starter_ids:
                                _lineup_status = "starting"
                                _lineup_alert = "✓ Confirmed in starting XI"
                                print(f"[LINEUP] {req.playerName}: confirmed STARTING in fixture {_sit_fixture_id}")
                                break
                            elif _player_id_int in _sub_ids:
                                _lineup_status = "substitute"
                                _lineup_alert = "⚠ Listed as substitute — reduced involvement expected"
                                _lineup_confidence_floor = 0.45
                                print(f"[LINEUP] {req.playerName}: confirmed SUBSTITUTE in fixture {_sit_fixture_id}")
                                break
                        else:
                            # Lineups posted but player found in neither — possibly not in squad
                            if _lineup_responses:
                                _lineup_status = "not_in_squad"
                                _lineup_alert = "⚠ Player not found in confirmed lineup"
                                _lineup_confidence_floor = 0.45
                                print(f"[LINEUP] {req.playerName}: NOT in lineup for fixture {_sit_fixture_id}")
                except Exception as _lineup_err:
                    print(f"[LINEUP] fetch error for fixture {_sit_fixture_id}: {_lineup_err}")

            # Apply confidence floor — cap pOver / pUnder at 45% if substitute / not in squad
            if early_bayes and _lineup_confidence_floor is not None:
                _dir = early_bayes["recommendation"]
                if _dir == "over" and early_bayes["pOver"] > _lineup_confidence_floor * 100:
                    early_bayes["pOver"]  = round(_lineup_confidence_floor * 100, 1)
                    early_bayes["pUnder"] = round((1 - _lineup_confidence_floor) * 100, 1)
                elif _dir == "under" and early_bayes["pUnder"] > _lineup_confidence_floor * 100:
                    early_bayes["pUnder"] = round(_lineup_confidence_floor * 100, 1)
                    early_bayes["pOver"]  = round((1 - _lineup_confidence_floor) * 100, 1)
                early_bayes["lineupStatus"] = _lineup_status

            if early_bayes and early_bayes.get("priorSamples", 0) >= 3:
                # ── PREFLIGHT PROJECTION: apply major downstream adjustments now ──
                # early_bayes.posteriorMean is the raw Bayesian estimate BEFORE
                # H2H, OPP-profile, and dominance adjustments that happen later.
                # If the dominance boost (Ball-Playing CB, GK inverted etc.) will
                # significantly move the final projection, we must tell AI the
                # RIGHT direction now — not the pre-adjustment direction.
                # Without this, AI writes "57.8 under" and the badge shows 66 OVER,
                # which is the exact contradiction the user is complaining about.
                _pf_proj = early_bayes["posteriorMean"]
                _pf_poss_props = {"pass_attempts", "passes", "key_passes", "crosses", "dribbles"}
                _pf_is_gk = _bayes_position.upper() in {"GK", "GOALKEEPER"}
                if match_dominance and req.propType in _pf_poss_props and not _pf_is_gk:
                    _pf_dom   = match_dominance.get("multiplier", 1.0)
                    _pf_avg   = match_dominance.get("teamSeasonAvg", 50)
                    _pf_exp   = match_dominance.get("expectedPoss", 50)
                    if _pf_avg < 52 and _pf_dom < 0.92:
                        # Pinned-back team — squeeze applies
                        _pf_proj = round(_pf_proj * _pf_dom, 1)
                    elif _pf_dom > 1.08 and _pf_exp > _pf_avg + 8:
                        # Positive dominance surge — apply damped boost (same logic as main pipeline)
                        _pf_damp = 0.65 if _pf_avg < 42 else (0.50 if _pf_avg < 48 else 0.35)
                        _pf_mult = 1.0 + (_pf_dom - 1.0) * _pf_damp
                        _pf_proj = round(_pf_proj * _pf_mult, 1)
                # Apply redistribution if it was calculated (already applied to early_bayes in some paths)
                # Note: early_bayes['posteriorMean'] may already include _redist_multiplier if it was applied above.
                # _pf_proj uses early_bayes['posteriorMean'] which is the post-redist value.

                # Use Monte Carlo P values for direction — not just mean vs line.
                # When P(UNDER) > P(OVER), recommend UNDER even if posteriorMean > line.
                _pf_p_over  = early_bayes.get("pOver", 50)
                _pf_p_under = early_bayes.get("pUnder", 50)
                _pf_rec_by_mean = "OVER" if _pf_proj > req.line else "UNDER"
                _pf_rec_by_prob = "OVER" if _pf_p_over >= _pf_p_under else "UNDER"
                _pf_rec = _pf_rec_by_prob
                if _pf_rec != _pf_rec_by_mean:
                    print(f"[PROB DIRECTION] {req.playerName}: mean→{_pf_rec_by_mean} but P(OVER)={_pf_p_over}%/P(UNDER)={_pf_p_under}% → using {_pf_rec}")
                _pf_bprob = early_bayes['pOver'] if _pf_rec == 'OVER' else early_bayes['pUnder']
                bdir = _pf_rec  # Use preflight direction as the anchor direction
                bprob = _pf_bprob
                if _pf_proj != early_bayes["posteriorMean"]:
                    print(f"[ANCHOR PREFLIGHT] {req.playerName}: raw={early_bayes['posteriorMean']} → preflight={_pf_proj} ({_pf_rec}) after dominance adjustment")

                bayesian_prompt_anchor = f"""
[MATHEMATICAL ENGINE — FINAL VERDICT — DO NOT CONTRADICT]
3-Layer Reverse Formula analysis ({early_bayes['priorSamples']} games): projects {_pf_proj} — VERDICT: {bdir} {req.line} (P={bprob}%).
Season avg: {early_bayes['priorMean']} | Recent form (decay-weighted): {early_bayes['momentumMean']} ({early_bayes['momentumLabel']}) | Context adj: {early_bayes['covariateAdjustment']:+.1f}
Streak: {early_bayes['streakFlag']} | Volatility: {early_bayes['volatility']} (CV={early_bayes['cv']}) | Reversal: {early_bayes['reversalFlag']}
IMPORTANT: Never use the word "Bayesian" in your response. Always say "Reverse Formula" instead.
>>> DIRECTION LOCK: The model's verdict is {bdir} {req.line} with projection {_pf_proj}. This is FINAL. Your ENTIRE analysis — every section, every sentence — must explain and support the {bdir} verdict. Do NOT argue for {'OVER' if bdir == 'UNDER' else 'UNDER'}. Do NOT present "tension" or "balanced" views. The math has already weighed all factors; your job is to narrate WHY the {bdir} verdict is tactically correct. Set aiProjection to a number on the {bdir} side of {req.line} (i.e. {'below' if bdir == 'UNDER' else 'above'} {req.line}). <<<"""
                # ── Inject quality-filtered hit rate into structured evidence ─────
                _ql_hr   = (historical_data.get("playerGameLogs") or {}).get("hitRates", {})
                _ql_tot  = _ql_hr.get("qualityTotal", 0)
                if _ql_tot >= 3 and req.line:
                    _ql_ov   = _ql_hr.get("qualityOverHits", 0)
                    _ql_pct  = _ql_hr.get("qualityOverPct", 0.0)
                    _ql_un   = _ql_tot - _ql_ov
                    _ql_un_pct = round(100 - _ql_pct, 1)
                    _ql_raw_tot = len((historical_data.get("playerGameLogs") or {}).get("games", []))
                    _ql_excl = _ql_raw_tot - _ql_tot
                    _ql_excl_note = (
                        f"{_ql_excl} sub-60-min game{'s' if _ql_excl != 1 else ''} excluded — partial-minute appearances distort the raw rate."
                        if _ql_excl > 0 else "All logged games were 60+ minutes (full sample)."
                    )
                    _ql_dir = "OVER" if _ql_pct >= 50 else "UNDER"
                    _ql_excl_suffix = f" — {_ql_excl} sub-60-min game{'s' if _ql_excl != 1 else ''} excluded" if _ql_excl > 0 else ""
                    bayesian_prompt_anchor += f"""
[QUALITY-FILTERED HIT RATE — 60+ MINUTE GAMES ONLY — USE AS PRIMARY SIGNAL]
Full-game appearances: {_ql_ov}/{_ql_tot} ({_ql_pct}%) OVER {req.line} | {_ql_un}/{_ql_tot} ({_ql_un_pct}%) UNDER {req.line}
{_ql_excl_note}
This quality-filtered rate is the TRUE historical signal. Include it in qualitySignal as: '{_ql_ov} of {_ql_tot} full-game appearances ({_ql_pct}%) went {_ql_dir} {req.line}{_ql_excl_suffix}.'"""
                # Inject quality prior note when Bayesian prior was quality-filtered
                if _quality_prior_applied and early_bayes:
                    bayesian_prompt_anchor += f"""
[QUALITY PRIOR — CRITICAL: WHY THE PRIOR IS {early_bayes.get('priorMean', '?')}]
The Reverse Formula EXCLUDED {_quality_prior_dropped} sub-60-min game{'s' if _quality_prior_dropped != 1 else ''} from the prior calculation. These were partial appearances (cameos, rotations, injury-limited games) — NOT representative of this player's full-game output.
Prior mean {early_bayes.get('priorMean', '?')} is based on {early_bayes.get('priorSamples', '?')} FULL GAMES (60+ minutes) only.
IMPORTANT: When narrating the projection, reference {early_bayes.get('priorMean', '?')} as the player's full-game average. Do NOT use a lower number — the lower raw average includes games where the player barely featured."""
                # Inject opponent tier filter note
                if _opp_tier_filter_applied and early_bayes:
                    _kept_str = " + ".join(_opp_tier_filter_kept_tiers)
                    bayesian_prompt_anchor += f"""
[OPPONENT QUALITY FILTER — CRITICAL]
The Reverse Formula also EXCLUDED {_opp_tier_filter_dropped} game{'s' if _opp_tier_filter_dropped != 1 else ''} vs lower-ranked opponents from the prior.
Current opponent rank: {_cur_opp_rank_for_tier}. Only kept games vs {_kept_str} opposition (comparable difficulty).
This ensures the prior reflects performance against teams of similar calibre, not inflated by results against easier sides.
Prior mean {early_bayes.get('priorMean', '?')} is drawn exclusively from {_kept_str} matchups. Reference this as the player's quality-opposition average."""
                # Inject redistribution context into prompt
                if _redist_alerts:
                    _redist_mult_pct = round((_redist_multiplier - 1) * 100)
                    bayesian_prompt_anchor += f"""
[TEAMMATE ABSENCE REDISTRIBUTION]
{" | ".join(_redist_alerts)}
The Reverse Formula has already boosted the projected {req.propType} by {_redist_mult_pct}% to account for this vacancy. Acknowledge this in your analysis."""
                # Inject lineup status context into prompt
                if _lineup_alert:
                    if _lineup_status == "starting":
                        bayesian_prompt_anchor += f"""
[LINEUP CONFIRMATION — POSITIVE SIGNAL]
{_lineup_alert}. Full minute involvement expected — no playing-time uncertainty for this projection."""
                    elif _lineup_status in ("substitute", "not_in_squad"):
                        bayesian_prompt_anchor += f"""
[LINEUP WARNING — REDUCED INVOLVEMENT]
{_lineup_alert}. Confidence capped at 45%. Flag this clearly in your analysis as a significant risk factor."""
                # Inject press intensity context into structured evidence
                _pi = early_bayes.get("pressIntensity", {})
                if _pi.get("label") not in (None, "Unknown", "Low") and req.propType in {"pass_attempts", "passes"}:
                    _pi_label = _pi["label"]
                    _pi_mult  = _pi.get("multiplier", 1.0)
                    _pi_sig   = _pi.get("signal_used", "possession")
                    if _pi_sig == "tackles":
                        _pi_da  = _pi.get("avg_defensive_actions", "?")
                        _pi_tkl = _pi.get("avg_tackles", "?")
                        _pi_int = _pi.get("avg_interceptions", "?")
                        bayesian_prompt_anchor += f"""
[OPPONENT PRESS INTENSITY — {_pi_label.upper()} (PPDA Proxy)]
PPDA Proxy (tackles + interceptions + fouls + blocks/game): {_pi_label} | Opponent avg {_pi_da} defensive actions/game ({_pi_tkl} tackles + {_pi_int} interceptions).
High defensive actions = opponent aggressively hunts the ball → subject player has less time/space with the ball, disrupted in possession.
Mathematical press penalty already applied: ×{_pi_mult} reduction to pass projection.
CRITICAL: This opponent actively disrupts passing lanes. Account for the subject player being pressured even when their team has the ball."""
                    else:
                        _pi_poss   = _pi.get("avg_poss", "?")
                        _pi_passes = _pi.get("avg_passes", "?")
                        bayesian_prompt_anchor += f"""
[OPPONENT POSSESSION PRESSURE — {_pi_label.upper()}]
Possession Pressure Index: {_pi_label} | Opponent avg {_pi_poss}% ball possession per game ({_pi_passes} total passes/game).
High opponent possession = the subject player's team has less time on the ball → subject player makes fewer pass attempts.
Mathematical possession penalty already applied: ×{_pi_mult} reduction to pass projection.
CRITICAL: This opponent dominates ball possession. Do NOT project pass totals near season average — the subject player's team will have significantly reduced time with the ball."""
                if (
                    req.propType in {"pass_attempts", "passes"}
                    and _pressure_response.get("status") == "classified"
                ):
                    bayesian_prompt_anchor += f"""
[PLAYER PRESSURE RESPONSE — SHADOW EVIDENCE ONLY]
This player's historical profile is {_pressure_response.get('label', 'unknown')}:
{_pressure_response.get('reason', '')}
High-pressure sample: {_pressure_response.get('highPressureSamples', 0)} games at {_pressure_response.get('highPressurePassesPer90')} passes/90.
Low-pressure sample: {_pressure_response.get('lowPressureSamples', 0)} games at {_pressure_response.get('lowPressurePassesPer90')} passes/90.
Shrunk pressure multiplier: {_pressure_response.get('pressureMultiplier')}.
This is a possession-based pressure proxy, not a direct passes-under-pressure measurement.
Do not change the mathematical projection for this signal; explain it as shadow evidence only."""

                # Inject positional baseline context into structured evidence
                _pb = (early_bayes or {}).get("positionalBaseline")
                if _pb and _pb.get("note") and "within realistic range" not in _pb.get("note", ""):
                    _pb_group = _pb.get("posGroup", "")
                    _pb_tier  = _pb.get("possessionTier", "")
                    _pb_p25   = _pb.get("p25")
                    _pb_p50   = _pb.get("p50")
                    _pb_p75   = _pb.get("p75")
                    _pb_from  = _pb.get("squeezedFrom")
                    _pb_to    = _pb.get("squeezedTo")
                    if _pb_from and _pb_to:
                        bayesian_prompt_anchor += f"""
[POSITIONAL ROLE BASELINE — CONTEXT CORRECTION APPLIED]
Position group: {_pb_group} | Team possession tier: {_pb_tier} (expected {_poss_for_baseline:.0f}%)
Realistic range for {_pb_group} in {_pb_tier}-possession team: p25={_pb_p25} / p50={_pb_p50} / p75={_pb_p75} per 90 min.
The raw projection ({_pb_from:.1f}) was outside this range and has been corrected to {_pb_to:.1f}.
IMPORTANT: In your analysis, explain WHY this player's current team context limits their output relative to their historical numbers. Do NOT cite the player's stats from a previous higher-possession club as evidence the OVER is likely."""

                # Inject game tempo context into structured evidence
                if game_tempo.get("expectedTempo") != "normal" and req.propType in {"pass_attempts", "passes", "key_passes", "crosses", "dribbles"}:
                    tempo_label = game_tempo["expectedTempo"].upper()
                    exp_goals = game_tempo.get("expectedTotalGoals", "?")
                    bayesian_prompt_anchor += f"""
[GAME TEMPO WARNING]
Expected match tempo: {tempo_label} ({exp_goals} expected total goals).
{"HIGH tempo = more open play, more touches, higher pass volumes for ALL players." if tempo_label == "HIGH" else "LOW tempo = defensive, fewer passes, compressed stat lines."}
Factor this into your projection — do NOT ignore game flow."""
                # Inject favorite dampening context
                if favorite_dampening.get("applied") and req.propType in {"pass_attempts", "passes", "key_passes", "crosses"}:
                    bayesian_prompt_anchor += f"""
[HEAVY FAVORITE ALERT]
This player's team is a heavy favorite (odds: {favorite_dampening['teamOdds']:.2f}).
CRITICAL: Teams leading early often shift to game management mode — fewer passes, direct play, time-wasting.
If recommending OVER on passes, account for potential 2nd-half tempo drop."""
                print(f"[BAYESIAN ANCHOR] {req.playerName}: math={early_bayes['posteriorMean']} {bdir} ({bprob}%), momentum={early_bayes['momentumLabel']}, streak={early_bayes['streakFlag']}")
        except Exception as e:
            print(f"[BAYESIAN ANCHOR] Error: {e}")

        # =============================================
        # BUILD REAL RECENT SAMPLES FROM GAME LOGS
        # =============================================
        # These replace generated samples with actual API-Sports data
        real_recent_samples = []
        if player_game_logs:
            gl_target_field_map = {
                "pass_attempts":   "passes_total",
                "shots":           "shots_total",
                "shots_on_target": "shots_on",
                "tackles":         "tackles_total",
                "key_passes":      "passes_key",
                "shots_assisted":  "passes_key",
                "saves":           "goals_saves",
                "interceptions":   "tackles_interceptions",
                "clearances":      "tackles_clearances",
                "blocks":          "tackles_blocks",
                "dribbles":        "dribbles_attempts",
                "fouls_drawn":     "fouls_drawn",
                "fouls_committed": "fouls_committed",
                "crosses":         "passes_crosses",
                "duels_won":       "duels_won",
                "yellow_cards":    "cards_yellow",
            }
            gl_target = gl_target_field_map.get(req.propType, "passes_total")
            for g in player_game_logs:
                stat_val = g.get(gl_target)
                if stat_val is not None and (g.get("minutes") or 0) > 0:
                    real_recent_samples.append({
                        "date": g.get("date", ""),
                        "opponent": g.get("opponent", ""),
                        "value": stat_val,
                        "minutesPlayed": g.get("minutes", 0),
                        "matchDifficulty": "medium",
                        "venue": g.get("venue", ""),
                    })

        # =============================================
        # UPGRADE #4: Per-90 minute normalization
        # =============================================
        # Extract per-90 rates from player's season stats so the structured model sees
        # normalized numbers, not raw totals skewed by minutes played
        per90_stats = {}
        if player_stats:
            stat_key_map = {
                "pass_attempts": ("passes", "total"),
                "shots": ("shots", "total"),
                "shots_on_target": ("shots", "on"),
                "tackles": ("tackles", "total"),
                "key_passes": ("passes", "key"),
                "shots_assisted": ("passes", "key"),
                "saves": ("goals", "saves"),
                "interceptions": ("tackles", "interceptions"),
                "blocks": ("tackles", "blocks"),
                "dribbles": ("dribbles", "attempts"),
                "fouls_drawn": ("fouls", "drawn"),
                "crosses": ("passes", "cross"),
                "clearances": ("tackles", "clearances"),
                "goals": ("goals", "total"),
                "assists": ("goals", "assists"),
                "duels_won": ("duels", "won"),
                "yellow_cards": ("cards", "yellow"),
                "fouls_committed": ("fouls", "committed"),
            }
            for stat_entry in player_stats.get("statistics", []):
                league_name = stat_entry.get("league", {}).get("name", "Unknown")
                season = stat_entry.get("league", {}).get("season", "")
                games = stat_entry.get("games", {})
                minutes = games.get("minutes") or 0
                appearances = games.get("appearences") or 0
                if minutes < 90 or appearances < 2:
                    continue  # Skip tiny samples

                entry = {
                    "league": league_name,
                    "season": season,
                    "appearances": appearances,
                    "totalMinutes": minutes,
                    "avgMinutesPerGame": round(minutes / appearances, 1) if appearances else 0,
                    "per90": {},
                    "rawPerGame": {},
                }

                for prop_key, (cat, sub) in stat_key_map.items():
                    raw_val = stat_entry.get(cat, {}).get(sub)
                    if raw_val is not None and raw_val > 0:
                        per_90 = round((raw_val / minutes) * 90, 2)
                        per_game = round(raw_val / appearances, 2) if appearances else 0
                        entry["per90"][prop_key] = per_90
                        entry["rawPerGame"][prop_key] = per_game

                if entry["per90"]:
                    per90_stats[f"{league_name}_{season}"] = entry

        if per90_stats:
            historical_data["per90Analysis"] = per90_stats

        # =============================================
        # UPGRADE #3: H2H player-specific stat extraction
        # =============================================
        # For each H2H fixture, fetch the player's individual stats in THAT match
        h2h_player_stats = []
        h2h_summary = {}
        # H2H is explanatory evidence, not required for the deterministic
        # projection. Wave 2 can already consume most of the response budget
        # when provider history is slow, so do not start another fan-out once
        # the core prediction is late.
        if h2h_data:
            h2h_fixture_ids = []
            for h in h2h_data[:H2H_PLAYER_SCAN_LIMIT]:
                fid = h.get("fixture", {}).get("id")
                if fid:
                    h2h_fixture_ids.append((fid, h))

            async def fetch_h2h_player_stat(fid, fixture_info):
                """Fetch the target player's stats from a specific H2H fixture"""
                try:
                    pstats, lineup_payload = await aio.gather(
                        api_football_request("fixtures/players", {"fixture": fid}),
                        api_football_request("fixtures/lineups", {"fixture": fid}),
                        return_exceptions=True,
                    )
                    if isinstance(pstats, Exception):
                        pstats = []
                    if isinstance(lineup_payload, Exception):
                        lineup_payload = []
                    if not pstats:
                        return None

                    # Determine which team is the player's team in this fixture
                    home_id = fixture_info.get("teams", {}).get("home", {}).get("id")
                    away_id = fixture_info.get("teams", {}).get("away", {}).get("id")
                    home_name = fixture_info.get("teams", {}).get("home", {}).get("name", "")
                    away_name = fixture_info.get("teams", {}).get("away", {}).get("name", "")
                    home_goals = fixture_info.get("goals", {}).get("home", 0)
                    away_goals = fixture_info.get("goals", {}).get("away", 0)

                    # Player's team is home → opponent is away, and vice versa
                    player_is_home = (home_id == actual_team_id)
                    opponent_name = away_name if player_is_home else home_name
                    venue_in_match = "home" if player_is_home else "away"

                    # Find our player in the fixture stats
                    for team_data in pstats:
                        for p in team_data.get("players", []):
                            if (
                                _normalize_provider_player_id(
                                    p.get("player", {}).get("id")
                                )
                                == _normalize_provider_player_id(req.playerId)
                            ):
                                stats = p.get("statistics", [{}])[0] if p.get("statistics") else {}
                                minutes_played = stats.get("games", {}).get("minutes") or 0
                                # A team meeting is not a player H2H appearance.
                                # API-Football can return bench/DNP rows with
                                # zero minutes; those must not inflate the H2H
                                # sample or trigger the model's H2H adjustment.
                                if minutes_played <= 0:
                                    return None
                                stat_key_map_h2h = {
                                    "pass_attempts": stats.get("passes", {}).get("total"),
                                    "shots": stats.get("shots", {}).get("total"),
                                    "shots_on_target": stats.get("shots", {}).get("on"),
                                    "tackles": stats.get("tackles", {}).get("total"),
                                    "key_passes": stats.get("passes", {}).get("key"),
                                    "shots_assisted": stats.get("passes", {}).get("key"),
                                    "saves": stats.get("goals", {}).get("saves"),
                                    "interceptions": stats.get("tackles", {}).get("interceptions"),
                                    "blocks": stats.get("tackles", {}).get("blocks"),
                                    "dribbles": stats.get("dribbles", {}).get("attempts"),
                                    "fouls_drawn": stats.get("fouls", {}).get("drawn"),
                                    "crosses": stats.get("passes", {}).get("cross"),
                                    "clearances": stats.get("tackles", {}).get("clearances"),
                                    "goals": stats.get("goals", {}).get("total"),
                                    "assists": stats.get("goals", {}).get("assists"),
                                    "duels_won": stats.get("duels", {}).get("won"),
                                    "yellow_cards": stats.get("cards", {}).get("yellow"),
                                    "fouls_committed": stats.get("fouls", {}).get("committed"),
                                }
                                # Enrich with possession from team fixture cache
                                _h2h_poss_team = None
                                _h2h_poss_opp  = None
                                try:
                                    _h2h_ck = f"fxt_{fid}_{actual_team_id}"
                                    _h2h_poss_doc = await db.fixture_player_cache.find_one(
                                        {"_k": _h2h_ck}, {"_id": 0, "d.possession": 1}
                                    )
                                    if _h2h_poss_doc and _h2h_poss_doc.get("d"):
                                        _raw = str(_h2h_poss_doc["d"].get("possession", "")).replace("%", "").strip()
                                        if _raw:
                                            _h2h_poss_team = int(_raw)
                                            _h2h_poss_opp  = 100 - _h2h_poss_team
                                except Exception:
                                    pass
                                exact_h2h_position = exact_position_from_lineup_payload(
                                    _api_response_list(lineup_payload),
                                    req.playerId,
                                )
                                observed_h2h_position = (
                                    exact_h2h_position
                                    or (stats.get("games") or {}).get("position")
                                )
                                return {
                                     "fixtureId": fid,
                                    "date": _legacy_h2h_display_date(
                                        fixture_info.get("fixture", {}).get("date", ""),
                                        venue_in_match,
                                    ),
                                    "opponent": opponent_name,
                                    "venue": venue_in_match,
                                    # Keep minutes beside every player-vs-opponent
                                    # row. A target-stat value without minutes is
                                    # not a verified appearance.
                                    "minutes": minutes_played,
                                    "minutesPlayed": minutes_played,
                                    "observedPosition": observed_h2h_position,
                                    "positionSource": (
                                        "fixture_lineup_grid"
                                        if exact_h2h_position
                                        else "fixture_player_stats"
                                    ),
                                    "statValues": {k: v for k, v in stat_key_map_h2h.items() if v is not None},
                                    "targetStat": stat_key_map_h2h.get(req.propType),
                                    "targetStatPer90": round((stat_key_map_h2h.get(req.propType, 0) or 0) / minutes_played * 90, 2) if minutes_played > 0 and stat_key_map_h2h.get(req.propType) else None,
                                    "matchScore": f"{home_goals}-{away_goals}",
                                    "teamPossession": _h2h_poss_team,
                                    "opponentPossession": _h2h_poss_opp,
                                }
                    return None
                except Exception:
                    return None

            if h2h_fixture_ids:
                # Do not put one slow lineup/stat request in charge of the
                # entire H2H result. The old outer gather timeout cancelled all
                # siblings and returned zero rows, even when several fixtures
                # had already produced verified player appearances.
                async def _bounded_h2h_player_stat(fid, fixture_info):
                    try:
                        return await aio.wait_for(
                            fetch_h2h_player_stat(fid, fixture_info),
                            timeout=5.0,
                        )
                    except Exception:
                        return None

                h2h_results = await aio.gather(*[
                    _bounded_h2h_player_stat(fid, fi)
                    for fid, fi in h2h_fixture_ids
                ], return_exceptions=True)
                h2h_player_stats = [
                    r for r in h2h_results
                    if isinstance(r, dict) and r
                ][:H2H_PLAYER_RESULT_LIMIT]
        print(f"[TIMING] H2H+prep: {_t.time()-_t0:.1f}s total")

        # Team meetings are useful even when the player did not appear in any
        # of them. Keep them separate from player H2H and group them by the
        # player's venue. Possession is only shown when a verified fixture
        # stat cache contains it; missing possession is not converted to 50/50.
        if h2h_data:
            async def _read_h2h_possession(fid: int, player_home: bool) -> tuple[int | None, int | None]:
                home_poss = away_poss = None
                try:
                    cached = await db.fixture_player_cache.find_one(
                        {"_k": f"fxt_poss_{fid}"}, {"_id": 0, "d": 1}
                    )
                    raw = (cached or {}).get("d") or {}
                    home_poss = raw.get("home_poss")
                    away_poss = raw.get("away_poss")

                    # Older fixture caches may only contain the player's
                    # team's possession under fxt_{fixture}_{team}.
                    if home_poss is None or away_poss is None:
                        team_cached = await db.fixture_player_cache.find_one(
                            {"_k": f"fxt_{fid}_{actual_team_id}"}, {"_id": 0, "d.possession": 1}
                        )
                        team_raw = str(((team_cached or {}).get("d") or {}).get("possession") or "")
                        team_raw = team_raw.replace("%", "").strip()
                        if team_raw:
                            team_poss = int(float(team_raw))
                            if player_home:
                                home_poss = team_poss
                                away_poss = 100 - team_poss
                            else:
                                away_poss = team_poss
                                home_poss = 100 - team_poss

                    # Historical team meetings are optional context. Keep this
                    # fallback independently bounded so a missing cache entry
                    # cannot hold the prediction open indefinitely.
                    if home_poss is None or away_poss is None:
                        try:
                            fixture_stats = await aio.wait_for(
                                api_football_request(
                                    "fixtures/statistics", {"fixture": fid}
                                ),
                                timeout=2.5,
                            )
                            for team_stats in fixture_stats or []:
                                team_id = (team_stats.get("team") or {}).get("id")
                                for stat in team_stats.get("statistics") or []:
                                    if stat.get("type") != "Ball Possession":
                                        continue
                                    raw_value = str(stat.get("value") or "").replace("%", "").strip()
                                    try:
                                        value = int(float(raw_value))
                                    except (TypeError, ValueError):
                                        continue
                                    if team_id == actual_team_id:
                                        if player_home:
                                            home_poss = value
                                        else:
                                            away_poss = value
                                    else:
                                        if player_home:
                                            away_poss = value
                                        else:
                                            home_poss = value
                            if home_poss is not None and away_poss is not None:
                                try:
                                    await db.fixture_player_cache.update_one(
                                        {"_k": f"fxt_poss_{fid}"},
                                        {"$set": {"_k": f"fxt_poss_{fid}", "d": {
                                            "home_poss": home_poss,
                                            "away_poss": away_poss,
                                        }}},
                                        upsert=True,
                                    )
                                except Exception as _poss_cache_err:
                                    print(f"[POSSESSION CACHE WRITE] skipped: {_poss_cache_err}")
                        except Exception:
                            # Missing optional historical possession is honest
                            # unavailable context, not a fabricated 50/50 value.
                            pass
                except (TypeError, ValueError):
                    pass
                except Exception:
                    pass

                def _int_poss(value):
                    try:
                        return int(float(str(value).replace("%", "").strip()))
                    except (TypeError, ValueError):
                        return None

                return _int_poss(home_poss), _int_poss(away_poss)

            async def _build_team_meeting_row(fixture_info: dict) -> tuple[str, dict] | None:
                try:
                    teams = fixture_info.get("teams") or {}
                    home = teams.get("home") or {}
                    away = teams.get("away") or {}
                    home_id = home.get("id")
                    away_id = away.get("id")
                    if home_id != actual_team_id and away_id != actual_team_id:
                        return None
                    player_home = home_id == actual_team_id
                    fid = (fixture_info.get("fixture") or {}).get("id")
                    home_poss, away_poss = await _read_h2h_possession(fid, player_home) if fid else (None, None)
                    row = {
                         "fixtureId": fid,
                        "date": _legacy_h2h_display_date(
                            (fixture_info.get("fixture") or {}).get("date", ""),
                            "home" if player_home else "away",
                        ),
                        "score": f"{fixture_info.get('goals', {}).get('home', '—')}-"
                                 f"{fixture_info.get('goals', {}).get('away', '—')}",
                        "homeTeam": home.get("name", ""),
                        "awayTeam": away.get("name", ""),
                        "homePossession": home_poss,
                        "awayPossession": away_poss,
                        "possessionAvailable": home_poss is not None and away_poss is not None,
                        "venue": "home" if player_home else "away",
                    }
                    return row["venue"], row
                except Exception:
                    return None

            try:
                _meeting_rows = await aio.gather(*[
                    _build_team_meeting_row(item) for item in h2h_data[:H2H_FIXTURE_LIMIT]
                ])
                _meetings_by_venue = {"home": [], "away": []}
                for _meeting in _meeting_rows:
                    if _meeting:
                        _venue_key, _row = _meeting
                        _meetings_by_venue[_venue_key].append(_row)
            except Exception:
                _meetings_by_venue = {"home": [], "away": []}
        else:
            _meetings_by_venue = {"home": [], "away": []}

        if h2h_player_stats:
            # The player-appearance rows and team-meeting rows are both tied to
            # the exact fixture. Reuse the verified possession pair discovered
            # by the meeting enrichment instead of leaving player rows blank.
            _meeting_by_fixture = {}
            for _venue_rows in (_meetings_by_venue or {}).values():
                for _meeting in _venue_rows or []:
                    if _meeting.get("fixtureId") is not None:
                        _meeting_by_fixture[_meeting["fixtureId"]] = _meeting
            for _appearance in h2h_player_stats:
                _meeting = _meeting_by_fixture.get(_appearance.get("fixtureId"))
                if _meeting:
                    _appearance["teamPossession"] = (
                        _meeting.get("homePossession")
                        if _appearance.get("venue") == "home"
                        else _meeting.get("awayPossession")
                    )
                    _appearance["opponentPossession"] = (
                        _meeting.get("awayPossession")
                        if _appearance.get("venue") == "home"
                        else _meeting.get("homePossession")
                    )
            # Calculate H2H averages for the target stat
            h2h_values = [s["targetStat"] for s in h2h_player_stats if s.get("targetStat") is not None]
            h2h_summary = {
                "matches": h2h_player_stats,
                "targetProp": req.propType,
                "sampleSize": len(h2h_values),
                "searchedFixtureCount": min(len(h2h_fixture_ids), H2H_PLAYER_SCAN_LIMIT),
                "historySeasons": H2H_HISTORY_SEASONS,
                "historyDepth": "six seasons",
            }
            if h2h_values:
                h2h_summary["avgVsOpponent"] = round(sum(h2h_values) / len(h2h_values), 2)
                h2h_summary["minVsOpponent"] = min(h2h_values)
                h2h_summary["maxVsOpponent"] = max(h2h_values)
                _h2h_evidence = summarize_player_opponent_history(h2h_values, req.line)
                h2h_summary["opponentHitRate"] = {
                    "overHits": _h2h_evidence["overHits"],
                    "underHits": _h2h_evidence["underHits"],
                    "overPct": _h2h_evidence["overHitRate"],
                    "underPct": _h2h_evidence["underHitRate"],
                    "sampleSize": _h2h_evidence["sampleSize"],
                    "evidenceStatus": _h2h_evidence["evidenceStatus"],
                    "opponent": req.opponentName,
                }

            # Keep the direct-player H2H evidence split by the player's actual
            # venue in each historical fixture. The overall H2H average is
            # useful context, but it must not hide a home/away disagreement.
            _h2h_venue_splits = {}
            for _venue in ("home", "away"):
                _venue_rows = [
                    row for row in h2h_player_stats
                    if row.get("venue") == _venue
                    and row.get("targetStat") is not None
                    and (row.get("minutesPlayed") or row.get("minutes") or 0) > 0
                ]
                _venue_values = [row["targetStat"] for row in _venue_rows]
                if not _venue_values:
                    continue
                _venue_over = sum(1 for value in _venue_values if value > req.line)
                _venue_under = sum(1 for value in _venue_values if value < req.line)
                _venue_push = len(_venue_values) - _venue_over - _venue_under
                _h2h_venue_splits[_venue] = {
                    "sampleSize": len(_venue_values),
                    "average": round(sum(_venue_values) / len(_venue_values), 2),
                    "overHits": _venue_over,
                    "underHits": _venue_under,
                    "pushHits": _venue_push,
                    "overPct": round(_venue_over / len(_venue_values) * 100, 1),
                    "underPct": round(_venue_under / len(_venue_values) * 100, 1),
                    "minutesAverage": round(
                        sum((row.get("minutesPlayed") or row.get("minutes") or 0) for row in _venue_rows)
                        / len(_venue_rows),
                        1,
                    ),
                }
            h2h_summary["venueSplits"] = _h2h_venue_splits

            # ── Enriched H2H metadata for the pro analysis display ──────────
            # Total team meetings found (not just ones the player appeared in)
            h2h_summary["teamMeetings"] = len(h2h_data) if h2h_data else 0
            h2h_summary["teamMeetingsByVenue"] = _meetings_by_venue

            # Season span from team H2H fixture dates
            try:
                _h2h_years = []
                for _hd in (h2h_data or []):
                    _hd_date = (_hd.get("fixture") or {}).get("date", "")
                    if _hd_date and len(_hd_date) >= 4:
                        try:
                            _h2h_years.append(int(_hd_date[:4]))
                        except (ValueError, TypeError):
                            pass
                if _h2h_years:
                    h2h_summary["seasonsCovered"] = {
                        "min": min(_h2h_years), "max": max(_h2h_years),
                        "range": f"{min(_h2h_years)}–{max(_h2h_years)}",
                    }
            except Exception:
                pass

            # Trend: recent 3 appearances vs prior (positive = improving)
            if len(h2h_values) >= 4:
                try:
                    _recent_3_avg = sum(h2h_values[:3]) / 3
                    _prior_avg = sum(h2h_values[3:]) / len(h2h_values[3:])
                    _trend_delta = _recent_3_avg - _prior_avg
                    h2h_summary["trendDirection"] = (
                        "improving" if _trend_delta > 3
                        else "declining" if _trend_delta < -3
                        else "stable"
                    )
                    h2h_summary["trendDelta"] = round(_trend_delta, 2)
                except Exception:
                    h2h_summary["trendDirection"] = "stable"
            else:
                h2h_summary["trendDirection"] = "stable"

            # Venue hit rate at the player's current venue
            try:
                _vh_hits = 0
                _vh_total = 0
                for _hs in h2h_player_stats:
                    if _hs.get("venue") == player_venue and _hs.get("targetStat") is not None:
                        _vh_total += 1
                        if _hs["targetStat"] > req.line:
                            _vh_hits += 1
                if _vh_total > 0:
                    h2h_summary["venueHitRate"] = {
                        "hits": _vh_hits, "total": _vh_total,
                        "pct": round(_vh_hits / _vh_total * 100),
                        "venue": player_venue,
                    }
            except Exception:
                pass

            historical_data["h2hPlayerStats"] = h2h_summary
        elif h2h_data:
            # Preserve team-meeting context for players with no verified
            # player-specific appearances in the historical fixture set.
            historical_data["h2hPlayerStats"] = {
                "matches": [],
                "targetProp": req.propType,
                "sampleSize": 0,
                "searchedFixtureCount": min(len(h2h_data), H2H_FIXTURE_LIMIT),
                "historySeasons": H2H_HISTORY_SEASONS,
                "historyDepth": "six seasons",
                "teamMeetings": len(h2h_data),
                "teamMeetingsByVenue": _meetings_by_venue,
                "trendDirection": "stable",
            }

        # Extract player's ACTUAL position from API-Sports data
        player_position = ""
        best_entry = None
        if player_stats:
            stats_list = player_stats.get("statistics", [])
            # Find the stat entry with most appearances (most relevant)
            best_apps = 0
            for s in stats_list:
                apps = s.get("games", {}).get("appearences") or 0
                pos = s.get("games", {}).get("position", "")
                if apps > best_apps and pos:
                    best_apps = apps
                    best_entry = s
                    player_position = pos
            # If we found a better entry, also try to get stats from multiple seasons
            if not player_position:
                for s in stats_list:
                    pos = s.get("games", {}).get("position", "")
                    if pos:
                        player_position = pos
                        break

        # API-Football uses compact category codes in some statistics payloads
        # (DEF/MID/FWD/G). Normalize them before the grounded verifier and all
        # downstream position gates so the provider category stays authoritative.
        player_position = {
            "G": "Goalkeeper",
            "GK": "Goalkeeper",
            "DEF": "Defender",
            "D": "Defender",
            "MID": "Midfielder",
            "M": "Midfielder",
            "FWD": "Attacker",
            "F": "Attacker",
        }.get(str(player_position or "").strip().upper(), player_position)

        # =============================================
        # GROUNDED POSITION RESOLVER: confirm identity only
        # Gemini is used here solely for web-grounded position/role verification.
        # It never writes narrative and never changes projection math directly.
        # =============================================
        specific_position = ""
        player_role = ""
        cached_pos = None
        _position_resolution_source = "fallback"
        _selection_role_source = str(req.positionSourceOverride or req.roleSourceOverride or "").strip()
        _selection_role_is_trusted = _selection_role_source in {
            "gemini_web_grounded",
            "cache",
            "manual_override",
            "api_sports_lineup_history",
        }
        # An inferred fixture-history role may be shown at selection time, but
        # it must not suppress a confirmed current lineup or be treated as a
        # user/manual override. Older clients that omit provenance retain the
        # historical manual-override behavior.
        _role_override_active = bool(req.positionOverride) and (
            not _selection_role_source or _selection_role_is_trusted
        )
        _selection_role_evidence = list(req.roleEvidenceOverride or [])
        GENERIC_POSITIONS = {"Goalkeeper", "Defender", "Midfielder", "Attacker", ""}

        # Position-to-role compatibility: ensures roles match positions
        POSITION_ROLE_MAP = {
            "GK": {"Shot-Stopper", "Sweeper Keeper"},
            "CB": {"Ball-Playing CB", "Stopper"},
            "LB": {"Fullback", "Wing-Back", "Inverted Fullback"},
            "RB": {"Fullback", "Wing-Back", "Inverted Fullback"},
            "LWB": {"Wing-Back", "Fullback"},
            "RWB": {"Wing-Back", "Fullback"},
            "CDM": {"Anchor", "Ball Winner", "Deep-Lying Playmaker"},
            "CM": {"Box-to-Box", "Mezzala", "Deep-Lying Playmaker", "Ball Winner"},
            "CAM": {"Advanced Playmaker", "Wide Playmaker", "Shadow Striker"},
            "LM": {"Wide Playmaker", "Traditional Winger"},
            "RM": {"Wide Playmaker", "Traditional Winger"},
            "LW": {"Traditional Winger", "Inverted Winger", "Inside Forward", "Progressive Carrier"},
            "RW": {"Traditional Winger", "Inverted Winger", "Inside Forward", "Progressive Carrier"},
            "CF": {"Complete Forward", "False 9", "Target Man", "Pressing Forward"},
            "ST": {"Poacher", "Target Man", "Complete Forward", "Pressing Forward"},
            "SS": {"Shadow Striker", "False 9"},
            "FWD": {"False 9", "Creative Forward", "Complete Forward", "Pressing Forward"},
        }

        # Constrain valid positions by API-Sports generic category
        GENERIC_TO_SPECIFIC = {
            "Goalkeeper": {"GK"},
            "Defender": {"CB", "LB", "RB", "LWB", "RWB"},
            "Midfielder": {"CDM", "CM", "CAM", "LM", "RM", "LW", "RW"},
            "Attacker": {"LW", "RW", "CF", "ST", "SS", "CAM"},
        }
        # Conservative fallback used only when the grounded resolver cannot
        # return a specific position. Keep the provider category broad:
        # generic M/MID is not proof of CM, CDM, or CAM.
        if req.positionOverride and _role_override_active:
            specific_position = req.positionOverride
            player_role = req.roleOverride or ""
            _position_resolution_source = "manual_override"
            print(f"[POS RESOLVE] User override: {req.playerName} → {specific_position} ({player_role})")
        elif req.positionOverride:
            specific_position = req.positionOverride
            player_role = req.roleOverride or ""
            _position_resolution_source = _selection_role_source or "selection_inferred"
            print(
                f"[POS RESOLVE] Inferred selection role: {req.playerName} → "
                f"{specific_position} ({player_role or 'role unavailable'})"
            )
        elif player_position in GENERIC_POSITIONS or not player_position:
            from ai_positions import resolve_player_role
            cached_pos = await db.player_positions.find_one(
                {"playerId": req.playerId},
                {"_id": 0, "source": 1, "roleSource": 1, "specificPosition": 1, "role": 1},
            )
            cached_specific = str((cached_pos or {}).get("specificPosition") or "").strip().upper()
            cached_source = str(
                (cached_pos or {}).get("source")
                or (cached_pos or {}).get("roleSource")
                or ""
            )
            cached_role = str((cached_pos or {}).get("role") or "").strip()
            cached_profile_is_trusted = bool(
                cached_specific
                and cached_specific in GENERIC_TO_SPECIFIC.get(player_position, set())
                and cached_source in {
                    "gemini_web_grounded",
                    "manual_override",
                    "api_sports_lineup_history",
                }
            )
            if cached_profile_is_trusted and cached_role not in POSITION_ROLE_MAP.get(
                cached_specific, set()
            ):
                cached_role = ""
            try:
                # Grounded position is explanation enrichment only. Keep the
                # provider category fallback available when the Gemini proxy
                # is slow or unavailable; it must not delay the deterministic
                # projection.
                specific_position, player_role, _position_resolution_source = await aio.wait_for(
                    resolve_player_role(
                        player_name=req.playerName,
                        team_name=corrected_team_name or req.teamName or "",
                        generic_position=player_position,
                        player_id=req.playerId or 0,
                        stats=_role_stats if "_role_stats" in locals() else None,
                    ),
                    timeout=1.5,
                )
                # ai_positions returns the provider category on its
                # fail-open path so callers can display it honestly. It is
                # not an exact position for projection, role matching, or
                # comparison admission.
                if _position_resolution_source == "provider_category_fallback":
                    specific_position = ""
                    player_role = ""
            except Exception as _position_resolve_err:
                print(
                    f"[POSITION RESOLVE] bounded fallback for {req.playerName}: "
                    f"{type(_position_resolve_err).__name__}"
                )
                specific_position, player_role = "", ""
                _position_resolution_source = "category_fallback"
            # The resolver may time out while reading the same Atlas record
            # that was already fetched above. Use that trusted player-ID
            # profile directly rather than throwing away exact evidence and
            # disabling same-position comparisons for this request.
            if not specific_position and cached_profile_is_trusted:
                specific_position = cached_specific
                player_role = cached_role
                _position_resolution_source = "cache"
                print(
                    f"[POS RESOLVE] Durable profile fallback: "
                    f"{req.playerName} → {specific_position}"
                    f"{' / ' + player_role if player_role else ''}"
                )
            if not specific_position:
                # Keep the broad provider category in player_position/display
                # only. Most importantly, do not overwrite an already-grounded
                # player-ID profile with an empty timeout/category fallback.
                # Position identity is durable enrichment, not per-request
                # ephemeral state.
                specific_position, player_role = "", ""
                _position_resolution_source = "category_fallback"
                print(
                    f"[POS RESOLVE] Category fallback: "
                    f"{req.playerName} → {player_position} "
                    "(exact position unavailable; existing profile preserved)"
                )
        else:
            specific_position = player_position
            _position_resolution_source = "provider_specific"

        # Use specific position if available, otherwise fall back to generic
        display_position = specific_position or player_position
        display_role = player_role

        # ── OBSERVED ROLE EVIDENCE ─────────────────────────────────────────
        # A current confirmed lineup is the strongest provider observation.
        # H2H fixture player rows provide a multi-match fallback when today's
        # lineup is projected or unavailable. This is explanation context only.
        _role_stats = {}
        if best_entry:
            _role_stats = {
                "appearances": best_entry.get("games", {}).get("appearences"),
                "passes_total": best_entry.get("passes", {}).get("total"),
                "key_passes": best_entry.get("passes", {}).get("key"),
                "tackles_total": best_entry.get("tackles", {}).get("total"),
                "dribbles_attempts": best_entry.get("dribbles", {}).get("attempts"),
                "shots_total": best_entry.get("shots", {}).get("total"),
                "goals_total": best_entry.get("goals", {}).get("total"),
            }
        # Prefer the same verified player-game logs used by the projection
        # when resolving a generic provider position.  A single aggregate
        # provider row can be stale or omit the creative profile that is
        # visible across the current season's fixture logs.
        def _role_number(value):
            try:
                if value is None or str(value).strip() == "":
                    return None
                return float(value)
            except (TypeError, ValueError):
                return None

        _role_logs = [
            game for game in (player_game_logs or [])
            if isinstance(game, dict) and (_role_number(game.get("minutes")) or 0) > 0
        ]
        if _role_logs:
            def _role_total(key):
                values = [_role_number(game.get(key)) for game in _role_logs]
                values = [value for value in values if value is not None]
                return sum(values) if values else None

            _log_profile = {
                "appearances": len(_role_logs),
                "passes_total": _role_total("passes_total"),
                "key_passes": _role_total("passes_key"),
                "tackles_total": _role_total("tackles_total"),
                "dribbles_attempts": _role_total("dribbles_attempts"),
                "shots_total": _role_total("shots_total"),
                "goals_total": _role_total("goals_total"),
            }
            _role_stats = {
                key: value for key, value in _log_profile.items()
                if value is not None
            }
        _observed_target = next(
            (item for item in ((_pitch_lineup or {}).get("players") or []) if item.get("isTarget")),
            None,
        )
        _observed_role = None
        _lineup_status = (_pitch_lineup or {}).get("status")
        if _lineup_status in {"confirmed", "predicted"} and _observed_target:
            _observed_position = infer_grid_position(
                _observed_target.get("grid"),
                (_pitch_lineup or {}).get("formation"),
                _observed_target.get("pos"),
            )
            _observed_role = resolve_observed_role(_observed_position, _role_stats)
            if _observed_position == "DEF":
                # A confirmed generic D row is stronger than a stale cached
                # exact position, but it is still only broad defender evidence.
                # Preserve that uncertainty instead of displaying a guessed
                # CB/fullback role.
                specific_position = "DEF"
                player_role = ""
                display_position = "DEF"
                display_role = ""
                _position_resolution_source = "fixture_lineup_category"
                _observed_role["position"] = "DEF"
                _observed_role["role"] = None
                _observed_role["source"] = "fixture_lineup_category"
                _observed_role["confidence"] = "low"
            elif _observed_position in {
                "GK", "CB", "LB", "RB", "LWB", "RWB", "CDM", "CM", "CAM",
                "LM", "RM", "LW", "RW", "CF", "ST", "SS",
            }:
                # A confirmed, exact current lineup is stronger than a
                # selection-time profile. A predicted lineup grid is not:
                # provider projections and stat fingerprints used to turn a
                # grounded midfielder/winger into an invented striker role.
                # Preserve the grounded identity until the fixture is
                # actually confirmed.
                if (
                    (
                        _selection_role_is_trusted
                        or _position_resolution_source in {
                            "manual_override",
                            "gemini_web_grounded",
                        }
                    )
                    and _lineup_status != "confirmed"
                ):
                    _observed_role = {
                        "position": specific_position,
                        "role": player_role or None,
                        "source": _selection_role_source or _position_resolution_source,
                        "confidence": req.roleConfidenceOverride or "medium",
                        "evidence": _selection_role_evidence + [
                            f"predicted lineup reported {_observed_position}; selection-time verified identity retained",
                        ],
                    }
                else:
                    # Exact current-fixture evidence outranks grounded profile
                    # evidence only when the lineup is confirmed. This prevents
                    # a stale profile from surviving an actual CB/LB/RB/ST
                    # observation while avoiding false certainty from a
                    # predicted lineup.
                    specific_position = _observed_position
                    player_role = _observed_role.get("role") or ""
                    display_position = specific_position
                    display_role = player_role
                    _position_resolution_source = (
                        "fixture_lineup_observation"
                        if _lineup_status == "confirmed"
                        else "predicted_lineup_grid"
                    )
        _historical_position_summary = summarize_observed_positions(
            [{"position": item.get("observedPosition")} for item in h2h_player_stats]
        )
        # A generic current D/M/F row is incomplete detail, not contradictory
        # evidence. Exact positions from verified player-ID history or lineup
        # grids remain usable for the target's natural comparison cohort.
        _historical_exact_positions = {
            "GK", "CB", "LB", "RB", "LWB", "RWB", "CDM", "CM", "CAM",
            "LM", "RM", "LW", "RW", "CF", "ST", "SS",
        }
        _historical_exact_position = next(
            (
                position
                for position, count in sorted(
                    (_historical_position_summary.get("positionCounts") or {}).items(),
                    key=lambda item: (-item[1], item[0]),
                )
                if position in _historical_exact_positions
            ),
            None,
        )
        _current_fixture_has_exact_position = (
            _observed_target is not None
            and infer_grid_position(
                _observed_target.get("grid"),
                (_pitch_lineup or {}).get("formation"),
                _observed_target.get("pos"),
            ) in _historical_exact_positions
        )
        _historical_exact_position_is_compatible = (
            _historical_exact_position
            and _historical_exact_position in GENERIC_TO_SPECIFIC.get(
                player_position,
                set(),
            )
        )
        # Provider lineup history is stronger than a durable profile cache:
        # caches can retain a former fullback role after a player has settled
        # into a centre-back role. Current exact grid evidence still wins, but
        # when today's lineup is generic/missing, the verified player-ID H2H
        # rows must be allowed to replace stale cached LB/RB/Fullback data.
        if (
            _historical_exact_position_is_compatible
            and not _current_fixture_has_exact_position
            and (
                not specific_position
                or _position_resolution_source in {
                    "cache",
                    "gemini_web_grounded",
                    "grounded_profile",
                    "category_fallback",
                }
            )
        ):
            specific_position = _historical_exact_position
            player_role = resolve_observed_role(
                specific_position,
                _role_stats,
            ).get("role") or ""
            display_position = specific_position
            display_role = player_role
            _position_resolution_source = "h2h_fixture_lineup_history"
            print(
                f"[POS RESOLVE] H2H lineup history: {req.playerName} → "
                f"{specific_position}{' / ' + player_role if player_role else ''}"
            )

        _current_lineup_observed_position = (
            infer_grid_position(
                _observed_target.get("grid"),
                (_pitch_lineup or {}).get("formation"),
                _observed_target.get("pos"),
            )
            if _observed_target is not None
            else ""
        )
        _current_lineup_position_is_generic = (
            (_pitch_lineup or {}).get("status") == "confirmed"
            and _observed_target is not None
            and _current_lineup_observed_position in {"DEF", "MID", "FWD"}
        )
        # A generic current fixture category is incomplete detail. It must not
        # erase an exact position already verified from player-ID history or a
        # lineup grid; it only prevents a role from being inferred when no
        # exact position exists yet.
        if _current_lineup_position_is_generic and not _role_override_active:
            if specific_position:
                _observed_role = {
                    "position": specific_position,
                    "role": player_role or None,
                    "source": _position_resolution_source,
                    "confidence": "medium",
                    "evidence": [
                        f"current fixture reports generic {_current_lineup_observed_position}",
                        f"exact {specific_position} retained from verified lineup history",
                    ],
                }
            else:
                _observed_role = {
                    "position": _current_lineup_observed_position,
                    "role": None,
                    "source": "fixture_lineup_category",
                    "confidence": "low",
                    "evidence": [
                        f"current fixture reports generic {_current_lineup_observed_position}",
                        "exact position still requires lineup/profile evidence",
                    ],
                }
        if (
            (not _observed_role or not _observed_role.get("role"))
            and not _current_lineup_position_is_generic
        ):
            _historical_position = _historical_position_summary.get("dominantPosition")
            if _historical_position:
                _observed_role = resolve_observed_role(_historical_position, _role_stats)
                _observed_role["sampleSize"] = _historical_position_summary.get("sampleSize", 0)
                _observed_role["positionCounts"] = _historical_position_summary.get("positionCounts", {})
                _observed_role["source"] = (
                    "h2h_fixture_role_inferred"
                    if _observed_role.get("role")
                    else "h2h_fixture_position_history"
                )
        if _observed_role and (
            _observed_role.get("role")
            or _observed_role.get("position") == "DEF"
        ):
            if (
                not _role_override_active
                and _position_resolution_source not in {
                    "gemini_web_grounded",
                    "cache",
                    "manual_override",
                    "fixture_lineup_observation",
                    "api_sports_lineup_history",
                    "h2h_fixture_lineup_history",
                }
            ):
                specific_position = _observed_role.get("position") or specific_position
                player_role = _observed_role.get("role") or ""
                display_position = specific_position or player_position
                display_role = player_role
                try:
                    await db.player_positions.update_one(
                        {"playerId": req.playerId},
                        {"$set": {
                            "playerId": req.playerId,
                            "playerName": req.playerName,
                            "team": corrected_team_name,
                            "genericPosition": player_position,
                            "specificPosition": specific_position,
                            "role": player_role,
                            "roleSource": _observed_role.get("source"),
                            "roleEvidence": _observed_role.get("evidence", []),
                            "roleSampleSize": _observed_role.get("sampleSize", 1),
                            "observedPositionCounts": _observed_role.get("positionCounts", {}),
                            "updatedAt": datetime.now(timezone.utc).isoformat(),
                        }},
                        upsert=True,
                    )
                except Exception as _role_persist_err:
                    print(f"[ROLE EVIDENCE] persistence skipped: {_role_persist_err}")
                    print(f"[POS ROLE CACHE WRITE] skipped: {_role_persist_err}")
        else:
            _observed_role = {
                "position": display_position or None,
                "role": display_role or None,
                "source": "cached_role_resolver" if display_role else "unavailable",
                "confidence": "low",
                "evidence": ["no confirmed lineup or positive-minutes position history"],
            }

        # Keep the selection-time grounded identity as the final customer
        # identity whenever the only competing observation is predicted,
        # generic, or stat-inferred. This final boundary is deliberately after
        # all history/role fallbacks so none of them can clobber the card.
        _selection_role_packet = preserve_selection_role(
            {
                "position": req.positionOverride,
                "role": req.roleOverride,
                "source": _selection_role_source,
                "confidence": req.roleConfidenceOverride,
                "evidence": _selection_role_evidence,
            },
            _observed_role,
            _lineup_status,
        )
        if _selection_role_packet:
            specific_position = _selection_role_packet["position"]
            player_role = _selection_role_packet.get("role") or ""
            display_position = specific_position
            display_role = player_role
            _position_resolution_source = _selection_role_packet["source"]
            _observed_role = _selection_role_packet

        # ── POSITION-CORRECTED BASELINE RE-SQUEEZE ────────────────────────────
        # The positional baseline ran at line ~2866 using _bayes_position from
        # the early cache lookup (which may have been empty or wrong on first run).
        # Now that specific_position is resolved via the stats-aware AI resolver,
        # re-run the baseline + squeeze if the position changed — so the CURRENT
        # prediction benefits from the correct position, not just the next one.
        try:
            if (
                specific_position
                and early_bayes
                and specific_position != _bayes_position
                and req.propType not in {"saves", "goalie_saves"}
            ):
                from positional_baseline import get_positional_baseline, apply_positional_squeeze
                _poss_rb = match_dominance.get("expectedPoss", 50.0) if match_dominance else 50.0
                _tavg_rb = match_dominance.get("teamAvgPasses") if match_dominance else None
                _plab_rb = None
                _pos_baseline_new = get_positional_baseline(
                    position=specific_position,
                    expected_poss=_poss_rb,
                    prop_type=req.propType,
                    role=player_role,
                    team_avg_passes=_tavg_rb,
                    press_intensity_label=_plab_rb,
                )
                if _pos_baseline_new:
                    # Re-squeeze from original pre-squeeze posteriorMean.
                    # _pos_baseline["squeezedFrom"] holds the pre-squeeze value when
                    # the first (wrong-position) squeeze fired; fall back to current pm.
                    _origin_pm = _pos_baseline.get("squeezedFrom") if _pos_baseline else None
                    _resqueeze_pm = _origin_pm if _origin_pm is not None else early_bayes.get("posteriorMean", req.line)
                    _adj_pm2, _pos_note2 = apply_positional_squeeze(
                        posterior_mean=_resqueeze_pm,
                        baseline=_pos_baseline_new,
                        n_samples=early_bayes.get("priorSamples", 0),
                    )
                    # ALWAYS apply the result — even when no squeeze fires we must
                    # restore posteriorMean to the pre-wrong-squeeze value.
                    import math as _math2
                    early_bayes["posteriorMean"] = _adj_pm2
                    early_bayes["recommendation"] = "over" if _adj_pm2 > req.line else "under"
                    _pos_baseline_new["squeezedFrom"] = _resqueeze_pm
                    _pos_baseline_new["squeezedTo"]   = _adj_pm2
                    if _pos_note2:
                        _pos_baseline_new["note"] = f"[RERESOLVED] {_pos_note2}"
                    else:
                        _pos_baseline_new["note"] = f"[RERESOLVED {specific_position}] within realistic range — no squeeze"
                    _bl_iqr2 = _pos_baseline_new.get("p75", req.line) - _pos_baseline_new.get("p25", req.line)
                    _bl_std2 = _bl_iqr2 / 1.35 if _bl_iqr2 > 0 else max(req.line * 0.25, 1.0)
                    _z2 = (_adj_pm2 - req.line) / max(_bl_std2, 0.01)
                    _po2 = round(max(1.0, min(99.0, 50.0 + 50.0 * _math2.erf(_z2 / _math2.sqrt(2)))), 1)
                    early_bayes["pOver"]  = _po2
                    early_bayes["pUnder"] = round(100.0 - _po2, 1)
                    early_bayes["positionalBaseline"] = _pos_baseline_new
                    print(f"[POS RE-RESOLVE] {req.playerName}: {_bayes_position or 'none'}→{specific_position} "
                          f"role={player_role} pm={_resqueeze_pm:.2f}→{_adj_pm2:.2f} P(over)={_po2}%")
        except Exception as _rrb_err:
            print(f"[POS RE-RESOLVE] non-fatal: {_rrb_err}")

        # ── DEFENDER POSSESSION MULTIPLIER OVERRIDE ──────────────────────────
        # The match-dominance possession multiplier uses poss_ratio = expected/season_avg.
        # For defenders on pass_attempts, this formula can PENALIZE slightly-below-average
        # expected possession even when the team is still a neutral-to-dominant possession side.
        # Root cause: if Huracan avg away = 52% and expected = 50.9%, ratio = 0.979 → multiplier
        # reduces passes by 2%. But 50.9% is basically neutral, not a deficit.
        #
        # Fix: recompute the possession multiplier for defenders using an ABSOLUTE 50% neutral
        # baseline so that any possession above 50% gives a positive (not relative-neutral) boost.
        # Also widen the cap to 0.55 (vs 0.35) since defender passes scale tightly with possession.
        _is_def_pass = (
            req.propType in {"pass_attempts", "passes"}
            and player_position in {"Defender"}
            and match_dominance is not None
        )
        if _is_def_pass:
            _def_exp_poss = match_dominance.get("expectedPoss", 50.0)
            _def_raw_adj  = (_def_exp_poss - 50.0) / 50.0  # +0.30 at 65%, +0.018 at 50.9%
            _def_capped   = max(-0.40, min(0.55, _def_raw_adj))
            _def_new_mult = round(1.0 + _def_capped, 3)
            _def_old_mult = match_dominance.get("multiplier", 1.0)
            if abs(_def_new_mult - _def_old_mult) > 0.02:
                match_dominance["multiplier"] = _def_new_mult
                match_dominance["notes"].append(
                    f"Defender pass override: absolute baseline → ×{_def_new_mult} "
                    f"(was ×{_def_old_mult}, exp poss {_def_exp_poss:.1f}%)"
                )
                print(f"[DEF PASS MULT] {req.playerName}: poss={_def_exp_poss:.1f}% → ×{_def_old_mult}→×{_def_new_mult}")

        # =============================================
        # Deterministic projection ledger
        # =============================================
        # Build structured evidence from the fetched data.

        # Build the data payload — use GPT summary as primary + Wave 2 deep data as supplement
        wave2_supplement = {}
        if player_game_logs:
            target_field_map = {
                "pass_attempts": "passes_total", "shots": "shots_total", "shots_on_target": "shots_on",
                "tackles": "tackles_total", "key_passes": "passes_key", "shots_assisted": "passes_key",
                "saves": "goals_saves", "goalie_saves": "goals_saves",
                "interceptions": "tackles_interceptions", "blocks": "tackles_blocks",
                "dribbles": "dribbles_attempts", "fouls_drawn": "fouls_drawn",
                "crosses": "passes_crosses", "clearances": "tackles_clearances",
                "goals": "goals_total", "assists": "goals_assists",
                "duels_won": "duels_won", "yellow_cards": "cards_yellow",
                "fouls_committed": "fouls_committed",
            }
            target_field = target_field_map.get(req.propType, "passes_total")
            values = [g.get(target_field) for g in player_game_logs if g.get(target_field) is not None]
            game_log_brief = []
            for g in player_game_logs:
                val = g.get(target_field)
                _form_str = f", {g['formation']}" if g.get("formation") else ""
                game_log_brief.append(f"{g.get('date','')[:10]} vs {g.get('opponent','')} ({g.get('venue','')}, {g.get('minutes',0)}min{_form_str}): {val}")
            wave2_supplement["playerGameLogs"] = {
                "games": game_log_brief,
                "rawAvg": round(sum(values) / len(values), 2) if values else 0,
                "homeAvg": round(sum(v for g, v in zip(player_game_logs, [g.get(target_field) for g in player_game_logs]) if g.get("venue") == "home" and v) / max(1, sum(1 for g in player_game_logs if g.get("venue") == "home" and g.get(target_field))), 2) if values else 0,
                "awayAvg": round(sum(v for g, v in zip(player_game_logs, [g.get(target_field) for g in player_game_logs]) if g.get("venue") == "away" and v) / max(1, sum(1 for g in player_game_logs if g.get("venue") == "away" and g.get(target_field))), 2) if values else 0,
                "sampleSize": len(values),
            }
            _wave2_last10 = sorted(
                player_game_logs,
                key=lambda g: g.get("date", ""),
                reverse=True,
            )[:10]
            _wave2_tp_home = [
                float(g["teamPossession"]) for g in _wave2_last10
                if g.get("venue") == "home" and g.get("teamPossession") is not None
            ]
            _wave2_tp_away = [
                float(g["teamPossession"]) for g in _wave2_last10
                if g.get("venue") == "away" and g.get("teamPossession") is not None
            ]
            _wave2_possession_count = sum(
                1
                for _log in player_game_logs
                if _log.get("teamPossession") is not None
                and _log.get("opponentPossession") is not None
            )
            wave2_supplement["playerGameLogs"].update({
                "last10Count": len(_wave2_last10),
                "tpHomeAvg": round(sum(_wave2_tp_home) / len(_wave2_tp_home), 1) if _wave2_tp_home else None,
                "tpAwayAvg": round(sum(_wave2_tp_away) / len(_wave2_tp_away), 1) if _wave2_tp_away else None,
                "tpHomeCount": len(_wave2_tp_home),
                "tpAwayCount": len(_wave2_tp_away),
                "possessionStatus": (
                    "verified"
                    if _wave2_possession_count == len(player_game_logs)
                    else "partial"
                    if _wave2_possession_count > 0
                    else "unavailable"
                ),
                "possessionAvailableGames": _wave2_possession_count,
            })
            # Pre-compute OVER/UNDER hit rates from actual game logs
            if values and req.line:
                over_hits = sum(1 for v in values if v > req.line)
                under_hits = sum(1 for v in values if v < req.line)
                push_hits = len(values) - over_hits - under_hits
                over_pct = round(over_hits / len(values) * 100, 1)
                under_pct = round(under_hits / len(values) * 100, 1)
                wave2_supplement["playerGameLogs"]["hitRates"] = {
                    "overHits": over_hits, "underHits": under_hits, "pushHits": push_hits,
                    "overPct": over_pct, "underPct": under_pct, "total": len(values),
                    "summary": f"OVER {req.line} in {over_hits}/{len(values)} games ({over_pct}%), UNDER in {under_hits}/{len(values)} ({under_pct}%)"
                }
        if team_fixture_stats:
            wave2_supplement["teamMatchStats"] = team_fixture_stats
        if opponent_fixture_stats:
            wave2_supplement["opponentMatchStats"] = opponent_fixture_stats
        if statsbomb_enrichment:
            wave2_supplement["statsbombEnrichment"] = statsbomb_enrichment

        # GK PASS CONTEXT — injected for GK pass_attempts props
        gk_pass_context = ""
        _is_gk_for_passes = (
            req.propType in {"pass_attempts", "passes"}
            and (
                (specific_position or "").upper() in {"GK", "GOALKEEPER"}
                or (player_position or "").lower() in {"goalkeeper", "gk"}
            )
        )
        if _is_gk_for_passes and match_dominance:
            _gk_exp_poss  = match_dominance.get("expectedPoss", 50)
            _gk_team_avg  = match_dominance.get("teamSeasonAvg", 50)
            _gk_opp_poss  = match_dominance.get("oppExpectedPoss", 50)
            _gk_venue_lbl = "AWAY" if player_venue == "away" else "HOME"
            _gk_poss_gap  = round(_gk_exp_poss - _gk_team_avg, 1)
            if _gk_exp_poss < 45:
                _gk_scenario = "LOW POSSESSION — HIGH GK VOLUME RISK: Team expected to defend deep. Defenders will constantly recycle to the GK under pressure. Model RAISES projection for this scenario. Do NOT underestimate."
            elif _gk_exp_poss < 50:
                _gk_scenario = "SLIGHTLY LOW POSSESSION — moderate back-pass volume expected."
            elif _gk_exp_poss > 58:
                _gk_scenario = "HIGH POSSESSION — LOW GK VOLUME: Team controls the ball through midfield. Fewer back-passes to the GK. Model LOWERS projection for this scenario."
            else:
                _gk_scenario = "BALANCED POSSESSION — normal GK pass volume expected."
            # Blowout risk: if the GK's team is a heavy favourite, flag the
            # game-script risk that a large winning margin suppresses second-half
            # GK distribution. Defenders stop recycling and just clear it long
            # to kill the clock when up 3+. This is irreducible variance that the
            # model cannot project in advance — user must be aware of the risk.
            _gk_blowout_warning = ""
            try:
                _bk_odds = (odds or {}).get("bookmakerOdds", {})
                _pifh_gk = (odds or {}).get("playerIsHome", player_venue == "home")
                _team_win_odds = float(_bk_odds.get("homeWin" if _pifh_gk else "awayWin", 99))
                _opp_win_odds  = float(_bk_odds.get("awayWin" if _pifh_gk else "homeWin", 99))
                if _team_win_odds <= 1.50:
                    _gk_blowout_warning = (
                        f"\n⚠️ BLOWOUT RISK: {req.teamName} are heavy favourites ({_team_win_odds:.2f}). "
                        f"If they lead by 3+ goals, defenders stop recycling and the GK's second-half "
                        f"distribution collapses — actual passes can finish 30-40% below first-half pace. "
                        f"This is irreducible game-script variance. Flag this in your analysis."
                    )
                elif _opp_win_odds <= 1.50:
                    _gk_blowout_warning = (
                        f"\n⚠️ COMEBACK PRESSURE RISK: {req.opponentName} are heavy favourites ({_opp_win_odds:.2f}). "
                        f"If the opponent leads big, the GK's team may chase the game — more open play, "
                        f"fewer back-passes as defenders push forward. GK distribution can drop late."
                    )
            except Exception:
                print("[POSITION CACHE WRITE] skipped: safety-valve cache update failed")

            # Determine cross-team correlation note for dominant-possession opponent
            _gk_cross_team_note = ""
            if _gk_opp_poss >= 62.0 and _gk_exp_poss < 40.0:
                _ct_severity_pct = round(min(100.0, (_gk_opp_poss - 62.0) / 15.0 * 100))
                _gk_cross_team_note = (
                    f"\n\n⚡ CROSS-TEAM CORRELATION ACTIVE — CORRELATED (NOT INVERSE):\n"
                    f"Opponent expected possession: {_gk_opp_poss}% — this is the 'Rodri Effect' scenario.\n"
                    f"When a dominant possession team (like Spain with Rodri) controls {_gk_opp_poss:.0f}% of the ball:\n"
                    f"  1. LOW-BLOCK PASS-BACK LOOP: {req.teamName} defenders are compressed deep → every ball won "
                    f"is recycled BACK to {req.playerName} under press (safe release = GK pass).\n"
                    f"  2. OVER-HIT CROSSES: {req.opponentName}'s high crossing volume leads to GK collections "
                    f"→ {req.playerName} must immediately distribute (= pass attempt).\n"
                    f"  3. GOAL KICKS: More opponent possession sequences = more shots/crosses = more goal kicks "
                    f"(each counts as a pass attempt).\n"
                    f"CRITICAL: {req.playerName}'s and {req.opponentName}'s ball-playing midfielder's pass totals "
                    f"RISE TOGETHER (correlated ↑↑), NOT inversely. Do not penalise {req.playerName}'s projection "
                    f"just because the opponent's midfielders have high pass volumes — that IS the mechanism driving "
                    f"this GK's volume up. Cross-team correlation severity: {_ct_severity_pct}%."
                )

            gk_pass_context = f"""
[GK PASS VOLUME CONTEXT — INVERTED POSSESSION MODEL]
{req.playerName} is a GOALKEEPER. Pass volume rules are INVERTED vs outfield players.
Venue: {_gk_venue_lbl} | Expected possession: {_gk_exp_poss}% (team season avg: {_gk_team_avg}%, gap: {_gk_poss_gap:+.1f}pp)
Opponent expected possession: {_gk_opp_poss}%
Scenario: {_gk_scenario}
KEY PRINCIPLE: A GK defending deep = maximum back-pass recycling. A GK on a dominant team = barely touched. This is the single most important factor for GK pass props.{_gk_cross_team_note}{_gk_blowout_warning}"""

            # ── Inject GK possession logic DIRECTLY into bayesian_prompt_anchor ──
            # The anchor sits immediately before the main prompt and is the AI's
            # primary reference for WHY the direction is what it is. Without this,
            # the AI applies outfield logic (high poss → more passes) to GK props.
            if bayesian_prompt_anchor:
                _gk_anchor_team = corrected_team_name or req.teamName
                if _gk_exp_poss > 55:
                    _gk_anchor_reason = (
                        f"{_gk_anchor_team} are the DOMINANT team at {_gk_exp_poss:.0f}% possession. "
                        f"BECAUSE they dominate, {req.playerName} barely receives back-passes — "
                        f"teammates circulate through midfield, rarely returning to the keeper. "
                        f"HIGH team possession = SUPPRESSED GK pass volume. This is why the verdict is UNDER."
                    )
                    _gk_forbidden = f"Do NOT say {_gk_anchor_team} struggle/fight for possession — they control {_gk_exp_poss:.0f}%."
                elif _gk_exp_poss < 45:
                    _gk_anchor_reason = (
                        f"{_gk_anchor_team} have only {_gk_exp_poss:.0f}% possession — they sit deep and defend. "
                        f"LOW team possession = constant back-pass recycling to the GK under pressure. "
                        f"Defenders use the keeper as a safe release repeatedly. "
                        f"LOW team possession = RAISED GK pass volume. This is why the verdict is OVER."
                    )
                    _gk_forbidden = f"Do NOT say {_gk_anchor_team} dominate — they have only {_gk_exp_poss:.0f}% possession."
                else:
                    _gk_anchor_reason = (
                        f"{_gk_anchor_team} have {_gk_exp_poss:.0f}% possession — balanced match. "
                        f"GK inverted rule: moderate volume, close to season average expected."
                    )
                    _gk_forbidden = f"Do not exaggerate possession imbalance."
                bayesian_prompt_anchor += f"""
[GK PASS PROP — POSSESSION NARRATIVE RULE — MANDATORY — READ BEFORE WRITING]
GOALKEEPER PROP. Standard possession → pass-volume logic is INVERTED for keepers.
Possession: {_gk_anchor_team} = {_gk_exp_poss:.0f}% | {req.opponentName} = {_gk_opp_poss:.0f}%
{_gk_anchor_reason}
⛔ {_gk_forbidden}
⛔ Do NOT apply outfield logic ("high possession = more passes") to this GK prop.
⛔ Do NOT flip or swap the possession numbers. {_gk_anchor_team} = {_gk_exp_poss:.0f}%. {req.opponentName} = {_gk_opp_poss:.0f}%. <<<"""

        # SAVES-SPECIFIC: Elite GK Formula
        # Projected Saves = Opponent Avg SoT × GK Save% × Match Context Multiplier
        saves_context = ""
        gk_formula_data = None
        if req.propType == "saves":
            # 1. Opponent SoT per game (venue-filtered from fixture stats)
            opp_shots_list = []
            if opponent_fixture_stats:
                for mf in opponent_fixture_stats:
                    shots = mf.get("totalShots")
                    shots_on = mf.get("shotsOnTarget")
                    if shots is not None:
                        opp_shots_list.append({"total": shots, "on_target": shots_on or 0, "date": mf.get("date", ""), "venue": mf.get("venue", "")})
            opp_avg_shots = round(sum(s["total"] for s in opp_shots_list) / len(opp_shots_list), 1) if opp_shots_list else 0
            opp_avg_sot = round(sum(s["on_target"] for s in opp_shots_list) / len(opp_shots_list), 1) if opp_shots_list else 0

            # 2. GK save rate — prefer venue-specific logs (away GKs face more shots,
            # mixing home/away inflates the save-rate baseline in the wrong direction).
            gk_saves_list = []
            gk_ga_from_logs = []
            _saves_venue_logs = [g for g in player_game_logs if g.get("venue") == player_venue and g.get("goals_saves") is not None and (g.get("minutes") or 0) > 0]
            # Lower threshold to 3 for GK saves (same as Bayesian venue-split fix):
            # away GK save averages are radically different from home averages.
            # 3 venue-specific samples are enough to anchor the gk_avg_saves here.
            _saves_pool = _saves_venue_logs if len(_saves_venue_logs) >= 3 else player_game_logs
            recent_gk_logs = [g for g in _saves_pool if g.get("goals_saves") is not None and (g.get("minutes") or 0) > 0][:7]
            for g in recent_gk_logs:
                gk_saves_list.append(g.get("goals_saves"))
                # Compute GA directly from game score + venue (most reliable source)
                score = g.get("score", "")
                venue = g.get("venue", "")
                try:
                    parts = score.split("-")
                    home_goals = int(parts[0].strip())
                    away_goals = int(parts[1].strip())
                    ga_this_game = away_goals if venue == "home" else home_goals
                    gk_ga_from_logs.append(ga_this_game)
                except Exception:
                    pass
            gk_avg_saves = round(sum(gk_saves_list) / len(gk_saves_list), 2) if gk_saves_list else 0
            gk_saves_per90 = round(sum(gk_saves_list) / max(1, sum((g.get("minutes") or 0) for g in recent_gk_logs)) * 90, 2) if gk_saves_list else 0

            # Goals against: prefer game-log-derived, fallback to team stats
            total_saves = sum(gk_saves_list) if gk_saves_list else 0
            games_with_saves = len(gk_saves_list)
            total_ga_from_logs = sum(gk_ga_from_logs) if gk_ga_from_logs else 0
            goals_against = round(total_ga_from_logs / len(gk_ga_from_logs), 2) if gk_ga_from_logs else None

            # Fallback to team stats if game logs didn't yield GA
            if goals_against is None and team_stats:
                ga = team_stats.get("goals", {}).get("against", {})
                if ga:
                    ga_total = ga.get("total", {})
                    if isinstance(ga_total, dict):
                        total_ga = ga_total.get(player_venue) or ga_total.get("total") or 0
                    else:
                        total_ga = ga_total or 0
                    played_data = team_stats.get("fixtures", {}).get("played", {})
                    if isinstance(played_data, dict):
                        played = played_data.get(player_venue) or played_data.get("total") or 1
                    else:
                        played = played_data or 1
                    goals_against = round(total_ga / max(played, 1), 2) if total_ga else None

            # Save % = saves / (saves + goals conceded)
            if total_saves > 0 and total_ga_from_logs > 0:
                est_sot_faced = total_saves + total_ga_from_logs
                gk_save_pct = round((total_saves / max(est_sot_faced, 1)) * 100, 1)
            elif total_saves > 0 and goals_against is not None and games_with_saves > 0:
                est_sot_faced = total_saves + (goals_against * games_with_saves)
                gk_save_pct = round((total_saves / max(est_sot_faced, 1)) * 100, 1)
            elif total_saves > 0:
                # Fallback: assume 1.3 GA/game (league average)
                gk_save_pct = round(min(80, (total_saves / max(total_saves + games_with_saves * 1.3, 1)) * 100), 1)
            else:
                gk_save_pct = 65.0  # Conservative league average fallback
            # Cap save rate at realistic bounds
            gk_save_pct = min(80.0, max(50.0, gk_save_pct))

            # 3. Match context multiplier (symmetric adjustments)
            context_multiplier = 1.0
            context_factors = []
            if match_odds and match_odds.get("favorite"):
                fav = match_odds["favorite"]
                if fav == player_venue:
                    context_multiplier -= 0.10
                    context_factors.append(f"Team favored ({fav}) → -10% (fewer opponent shots)")
                else:
                    context_multiplier += 0.07
                    context_factors.append("Team underdog → +7% (more opponent shots)")

            # POSSESSION DOMINANCE PENALTY for saves
            # When the GK's team dominates possession, opponents have less ball
            # → fewer shots on target → fewer saves. Ann-Katrin Berger (62% poss,
            # won 1-0) projected OVER 2 saves but actual was 1 — classic dominance miss.
            if match_dominance and isinstance(match_dominance, dict):
                _saves_exp_poss = match_dominance.get("expectedPoss")
                _saves_avg_poss = match_dominance.get("teamSeasonAvg")
                if (_saves_exp_poss and _saves_avg_poss and _saves_avg_poss > 0):
                    _saves_poss_ratio = _saves_exp_poss / _saves_avg_poss
                    if _saves_poss_ratio > 1.08:
                        # Team significantly more dominant than usual → opponent barely touches ball
                        _poss_save_penalty = min(0.20, (_saves_poss_ratio - 1.0) * 1.0)
                        context_multiplier = round(context_multiplier * (1.0 - _poss_save_penalty), 2)
                        context_factors.append(
                            f"Possession dominance ({_saves_exp_poss:.0f}% vs {_saves_avg_poss:.0f}% avg) "
                            f"→ -{_poss_save_penalty*100:.0f}% saves (opponent less ball)"
                        )

            context_multiplier = round(context_multiplier, 2)

            # 4. THE FORMULA: Projected Saves = Opp Avg SoT × GK Save% × Context
            # Weighted blend: 40% formula (match-specific) + 60% GK average (form).
            # Saves is a high-variance stat — individual-game SOT fluctuates sharply
            # even when a team's season average looks high. Anchoring more heavily to
            # the GK's own recent save average reduces formula-driven over-projection
            # in cagey or low-tempo matchups.
            raw_formula = round(opp_avg_sot * (gk_save_pct / 100) * context_multiplier, 1) if opp_avg_sot > 0 else gk_avg_saves
            if gk_avg_saves > 0 and raw_formula > 0:
                projected_saves = round(raw_formula * 0.4 + gk_avg_saves * 0.6, 1)
            else:
                projected_saves = raw_formula if raw_formula > 0 else gk_avg_saves

            gk_formula_data = {
                "opponentAvgShots": opp_avg_shots,
                "opponentAvgSOT": opp_avg_sot,
                "opponentVenue": opponent_venue.upper(),
                "opponentShotsSample": len(opp_shots_list),
                "gkSaveRate": gk_save_pct,
                "gkAvgSaves": gk_avg_saves,
                "gkSavesPer90": gk_saves_per90,
                "gkSampleSize": games_with_saves,
                "goalsAgainstPerGame": goals_against,
                "contextMultiplier": context_multiplier,
                "contextFactors": context_factors,
                "formulaProjection": projected_saves,
                "formula": f"{opp_avg_sot} SoT × {gk_save_pct}% save rate × {context_multiplier} context → {raw_formula} formula (40%) + {gk_avg_saves} avg (60%) = {projected_saves}",
            }
            wave2_supplement["savesAnalysis"] = gk_formula_data

            saves_context = f"""
[ELITE GK SAVES FORMULA]
FORMULA: Projected Saves = Opponent Avg SoT × GK Save% × Match Context Multiplier

1. OPPONENT SHOTS ON TARGET ({opponent_venue.upper()} venue, last {len(opp_shots_list)} games):
   - Avg total shots/game: {opp_avg_shots}
   - Avg shots on TARGET/game: {opp_avg_sot}

2. GK SAVE RATE (last {games_with_saves} games):
   - Avg saves/game: {gk_avg_saves}
   - Saves per 90: {gk_saves_per90}
   - Estimated save %: {gk_save_pct}%
   - Team goals against/game ({player_venue}): {goals_against or 'N/A'}

3. MATCH CONTEXT MULTIPLIER: {context_multiplier}
   {chr(10).join('   - ' + f for f in context_factors) if context_factors else '   - Neutral'}

4. FORMULA RESULT: {opp_avg_sot} × {gk_save_pct}% × {context_multiplier} = {raw_formula} (blended with {gk_avg_saves} avg → {projected_saves})

COMPARE TO LINE: Line is {req.line}. Formula projects {projected_saves}.
{'LEAN OVER' if projected_saves > req.line else 'LEAN UNDER' if projected_saves < req.line else 'PUSH ZONE'} — but weight scenarios (blowout, cagey game, etc.)
"""

        # POSITION COMPARISON: Fetch exact-position players vs opponent (run
        # after player_position resolved). Tactical role is explanatory
        # metadata only; it never admits or excludes a comparison row.
        position_comparison = []
        position_comparison_meta = {
            "attempted": req.sport == "soccer",
            "status": "pending" if req.sport == "soccer" else "not_applicable",
            "unavailableReason": None,
        }
        _exact_comparison_positions = {
            "GK", "CB", "LB", "RB", "LWB", "RWB", "CDM", "CM", "CAM",
            "LM", "RM", "LW", "RW", "CF", "ST", "SS",
        }
        _exact_target_for_comparison = specific_position in _exact_comparison_positions
        _defender_positions = {"CB", "LB", "RB", "LWB", "RWB"}
        _defender_position_cohort = specific_position in _defender_positions
        position_comparison_scope = (
            "exact_opponent_same_position_same_venue"
            if _exact_target_for_comparison
            else "opponent_broad_category_same_venue"
        )
        if req.sport == "soccer":
            if not player_position:
                position_comparison_meta["status"] = "unavailable"
                position_comparison_meta["unavailableReason"] = "player_position_unavailable"
            elif not opponent_fixture_list:
                position_comparison_meta["status"] = "unavailable"
                position_comparison_meta["unavailableReason"] = "opponent_fixture_history_unavailable"
            try:
                position_comparison = await aio.wait_for(
                    fetch_position_comparison(
                        opponent_fixture_list,
                        player_position,
                        req.propType,
                        req.opponentId,
                        player_venue,
                        min(_cohort_fixture_lookback, 20),
                        target_specific_pos=specific_position,
                        target_role=display_role or player_role,
                        allow_broad_category=not _exact_target_for_comparison,
                        allow_exact_fallback=True,
                    ) if player_position else _empty_list(),
                    # This is required evidence, not optional late enrichment.
                    # Keep it independently bounded, but do not drop the attempt
                    # merely because the deterministic pass took longer than the
                    # old 17-second response heuristic.
                    timeout=10,
                )
                if position_comparison:
                    position_comparison_meta["status"] = "available"
                else:
                    position_comparison_meta["status"] = "unavailable"
                    position_comparison_meta["unavailableReason"] = (
                        "broad_category_unavailable"
                        if not _exact_target_for_comparison
                        else (
                            "opponent_fixture_history_unavailable"
                            if not opponent_fixture_list
                            else "no_verified_exact_position_rows"
                        )
                    )
            except aio.TimeoutError:
                position_comparison_meta["status"] = "unavailable"
                position_comparison_meta["unavailableReason"] = "provider_timeout"
                print("[POS COMP] required comparison attempt timed out")
            except Exception as e:
                position_comparison_meta["status"] = "unavailable"
                position_comparison_meta["unavailableReason"] = "provider_unavailable"
                print(f"[POS COMP] Error/timeout: {e}")

        # A current-season pool can be very small even when the opponent has a
        # deep, useful history. Go back through prior seasons before showing a
        # three-player "opponent" sample as if it were complete. The same exact
        # opponent, role, position, and venue filters remain active; only the
        # season window broadens.
        if (
            len(position_comparison) < 15
            and safe_opp_id
            and not _is_bdl_league
        ):
            _prior_season_rows = []
            # Fetch up to 4 prior seasons in parallel — sequential season fetches
            # were a major prediction latency source (7 × ~1s = 7s). Capped at 4
            # seasons which covers ~95% of position-cohort cases.
            async def _fetch_prior_season(season: int):
                try:
                    _raw = await aio.wait_for(
                        api_football_request(
                            "fixtures",
                            {"team": safe_opp_id, "season": season},
                        ),
                        timeout=3,
                    )
                    rows = _normalize_opponent_fixtures(_raw)
                    if rows:
                        print(
                            f"[POS COMP] Prior-season fallback: opponent={safe_opp_id} "
                            f"season={season} fixtures={len(rows)}"
                        )
                    return rows
                except Exception as _prior_err:
                    print(
                        f"[POS COMP] Prior-season fallback failed for "
                        f"{safe_opp_id}/{season}: {type(_prior_err).__name__}"
                    )
                    return []

            _prior_seasons = list(range(CURRENT_SEASON - 1, CURRENT_SEASON - 5, -1))
            try:
                _prior_results = await aio.wait_for(
                    aio.gather(
                        *[_fetch_prior_season(s) for s in _prior_seasons],
                        return_exceptions=True,
                    ),
                    timeout=3.5,
                )
            except Exception as _prior_fetch_err:
                print(
                    f"[POS COMP] Prior-season fixture window bounded: "
                    f"{type(_prior_fetch_err).__name__}"
                )
                _prior_results = []
            for _res in _prior_results:
                if isinstance(_res, list):
                    _prior_season_rows.extend(_res)

            if _prior_season_rows:
                _existing_fixture_ids = {
                    row.get("fixtureId") for row in opponent_fixture_list
                }
                opponent_fixture_list.extend(
                    row for row in _prior_season_rows
                    if row.get("fixtureId") not in _existing_fixture_ids
                )
                try:
                    _prior_comparison = await aio.wait_for(
                        fetch_position_comparison(
                            _prior_season_rows,
                            player_position,
                            req.propType,
                            req.opponentId,
                            player_venue,
                            len(_prior_season_rows),
                            target_specific_pos=specific_position,
                            target_role=display_role or player_role,
                            allow_broad_category=not _exact_target_for_comparison,
                            allow_exact_fallback=True,
                        ) if player_position else _empty_list(),
                        timeout=10,
                    )
                except Exception as _prior_comp_err:
                    print(f"[POS COMP] Prior-season comparison failed: {_prior_comp_err}")
                    _prior_comparison = []

                if _prior_comparison:
                    _current_has_exact_rows = any(
                        row.get("positionVerified") is True
                        for row in position_comparison
                    )
                    by_team = {
                        _cohort_team_key(row): row
                        for row in position_comparison
                    }
                    for row in _prior_comparison:
                        # Do not mix broad winger fallback rows into an
                        # otherwise exact cohort. Exact evidence remains the
                        # stronger contract whenever either window has it.
                        if (
                            _current_has_exact_rows
                            and row.get("positionVerified") is not True
                        ):
                            continue
                        key = _cohort_team_key(row)
                        if key and key not in by_team:
                            by_team[key] = row
                    position_comparison = list(by_team.values())
                    if len(position_comparison) > 15:
                        position_comparison = position_comparison[:15]
                    if len(position_comparison) > 3:
                        position_comparison_scope = (
                            (
                                "exact_opponent_same_position_same_venue_plus_prior_seasons"
                                if _exact_target_for_comparison
                                else "opponent_broad_category_same_venue_plus_prior_seasons"
                            )
                        )
                    if position_comparison:
                        position_comparison_meta["status"] = "available"
                        position_comparison_meta["unavailableReason"] = None

        # Always show most-recent appearances first so subscribers see
        # current-form evidence before older historical data.
        if position_comparison:
            position_comparison = sorted(
                position_comparison,
                key=lambda x: str(x.get("date") or ""),
                reverse=True,
            )

        _has_exact_position_rows = any(
            row.get("positionVerified") is True
            for row in position_comparison
        )
        _broad_position_fallback = bool(
            _exact_target_for_comparison
            and position_comparison
            and not _has_exact_position_rows
        )
        if _broad_position_fallback:
            position_comparison_scope = (
                "opponent_broad_category_same_venue"
                if "prior_seasons" not in position_comparison_scope
                else "opponent_broad_category_same_venue_plus_prior_seasons"
            )

        print(
            f"[POS COMP] target={req.playerName} position={specific_position or display_position} "
            f"mode={'exact-position' if _exact_target_for_comparison else 'broad-category'} "
            f"rows={len(position_comparison)}"
        )

        # ── COMPARISON ENRICHMENT: Add season save rate (GK) or venue pass avg to each player ──
        if position_comparison:
            _enrich_prop = req.propType

            async def _fetch_comp_player_stats(p_entry):
                """Enrich one comparison player with save rate (GK) or season avg passes."""
                _pid = p_entry.get("playerId")

                # ── SAVES: compute per-game save rate from fixture data — no API call needed.
                # API-Football does NOT return goalkeeper.saves in season stats for many leagues.
                # Per-game rate (saves vs this opponent) is directly available and highly relevant.
                if _enrich_prop == "saves":
                    _gc = p_entry.get("goalsConceded")
                    _sv = p_entry.get("statValue", 0)
                    if _gc is not None and (_sv + _gc) > 0:
                        p_entry["saveRate"] = round(_sv / (_sv + _gc) * 100, 1)
                    return  # no API call needed for saves

                # ── PASSES: fetch season stats for avg passes per game
                if _enrich_prop not in {"pass_attempts", "passes", "key_passes", "crosses"}:
                    return
                if not _pid:
                    return
                _enrich_lid = req.leagueId or league_id or 39

                # The cache refresher already stores API-Football season
                # records in the same API-shaped form used by get_player_data.
                # Read those records first; the old path made two live
                # provider calls for every comparison player, even when the
                # values were already available locally.
                try:
                    _cached_seasons = await db.player_season_stats.find(
                        {
                            "playerId": _pid,
                            "season": {"$in": [CURRENT_SEASON, CURRENT_SEASON - 1]},
                        },
                        {"_id": 0, "statistics": 1},
                    ).to_list(4)
                    _cached_stats = [
                        stat
                        for doc in _cached_seasons
                        for stat in (doc.get("statistics") or [])
                        if isinstance(stat, dict)
                    ]
                    if _cached_stats:
                        _apps = sum(
                            (stat.get("games") or {}).get("appearences") or 0
                            for stat in _cached_stats
                        )
                        _pass_total = sum(
                            (stat.get("passes") or {}).get("total") or 0
                            for stat in _cached_stats
                        )
                        if _apps > 0 and _pass_total > 0:
                            p_entry["seasonAvgStat"] = round(_pass_total / _apps, 1)
                            return
                except Exception:
                    pass

                # Do not make a live provider call here. A mixed sample of
                # cached and newly-fetched season averages can change the
                # pair-calibration uplift based on whichever requests happen
                # to beat the timeout. The comparison row remains valid
                # opponent evidence without this optional season baseline;
                # background cache refresh will make it available next time.
                return

            # Run enrichment for all comparison players in parallel
            _enrich_tasks = [_fetch_comp_player_stats(p) for p in position_comparison]
            try:
                await aio.wait_for(aio.gather(*_enrich_tasks, return_exceptions=True), timeout=8)
                _enriched = sum(1 for p in position_comparison if p.get("saveRate") or p.get("seasonAvgStat"))
                if _enriched:
                    print(f"[POS ENRICH] Enriched {_enriched}/{len(position_comparison)} comparison players for {req.propType}")
            except Exception as _ee:
                print(f"[POS ENRICH] Batch timeout/error: {_ee}")

        # POSITION CONTEXT: Aggregate same-position comparison rows for the
        # deterministic opponent profile, math adjustments, and factor ledger.
        position_context = ""
        position_comp_data = None
        if display_position:
            pos_map = {"Goalkeeper": "GK", "Defender": "DEF", "Midfielder": "MID", "Attacker": "FWD"}
            pos_short = specific_position if specific_position else pos_map.get(player_position, player_position)
            position_context = f"\n[PLAYER POSITION] {req.playerName} plays as {pos_short}"
            if player_role:
                position_context += f" — Role: {player_role}"
            if specific_position and player_position:
                position_context += f" (API category: {player_position})"
            _cohort_evidence = summarize_position_cohort(position_comparison, req.line)
            comp_values = [
                p.get("statValue") for p in position_comparison
                if p.get("statValue") is not None
            ]
            comp_per90 = [
                p.get("per90") for p in position_comparison
                if p.get("per90") is not None
            ]
            # These are deliberately NOT derived from position_comparison.
            # Comparable-player rows are appearance/minutes-filtered; team
            # possession must represent the club schedule, including matches
            # where the selected player did not play.
            team_schedule_poss_avg = (team_schedule_possession or {}).get("average")
            opponent_schedule_poss_avg = (opponent_schedule_possession or {}).get("average")
            team_schedule_poss_n = int(
                (team_schedule_possession or {}).get("sampleSize") or 0
            )
            opponent_schedule_poss_n = int(
                (opponent_schedule_possession or {}).get("sampleSize") or 0
            )
            # Keep the existing deterministic model-facing opponent average
            # unchanged until the weighted evidence has passed settled-pick
            # replay. The new weighted value is exposed separately for the
            # evidence card and final evidence verdict.
            legacy_model_average = next(
                (
                    p.pop("_legacyModelAverage")
                    for p in position_comparison
                    if p.get("_legacyModelAverage") is not None
                ),
                None,
            )
            comp_avg = (
                legacy_model_average
                if legacy_model_average is not None
                else round(sum(comp_values) / len(comp_values), 2) if comp_values else None
            )
            comp_per90_avg = round(sum(comp_per90) / len(comp_per90), 2) if comp_per90 else None
            comp_poss_avg = team_schedule_poss_avg
            comp_opp_poss_avg = opponent_schedule_poss_avg
            _team_schedule_poss_verified = (
                isinstance(comp_poss_avg, (int, float))
                and isinstance(comp_opp_poss_avg, (int, float))
                and (team_schedule_possession or {}).get("verified") is True
                and (opponent_schedule_possession or {}).get("verified") is True
                and team_schedule_poss_n >= _POSSESSION_SAMPLE_TARGET
                and opponent_schedule_poss_n >= _POSSESSION_SAMPLE_TARGET
            )
            _team_schedule_poss_status = (
                (team_schedule_possession or {}).get("status")
                or "unavailable"
            )
            _opponent_schedule_poss_status = (
                (opponent_schedule_possession or {}).get("status")
                or "unavailable"
            )
            # This is the possession expectation for the selected player's
            # team in the current fixture. It is comparison context only:
            # same-role evidence must never alter the deterministic projection.
            try:
                current_expected_player_poss = round(
                    float((match_dominance or {}).get("expectedPoss")),
                    1,
                )
            except (TypeError, ValueError):
                current_expected_player_poss = None
            cross_prop_values = {}
            cross_prop_samples = {}
            for _row in position_comparison:
                for _cross_prop, _cross_value in (_row.get("crossPropStats") or {}).items():
                    cross_prop_values.setdefault(_cross_prop, []).append(_cross_value)
            cross_prop_averages = {}
            for _cross_prop, _values in cross_prop_values.items():
                if _values:
                    cross_prop_averages[_cross_prop] = round(sum(_values) / len(_values), 2)
                    cross_prop_samples[_cross_prop] = len(_values)
            position_comp_data = {
                "position": display_position,
                "positionShort": pos_short,
                "players": position_comparison,
                "avgStatValue": comp_avg,
                "average": _cohort_evidence.get("average"),
                "weightedAverage": _cohort_evidence.get("average"),
                "avgPer90": comp_per90_avg,
                "avgPossession": comp_poss_avg,
                "avgOpponentPossession": comp_opp_poss_avg,
                "expectedPlayerPossession": current_expected_player_poss,
                "possessionSampleSize": min(
                    team_schedule_poss_n,
                    opponent_schedule_poss_n,
                ),
                "teamPossessionSampleSize": team_schedule_poss_n,
                "opponentPossessionSampleSize": opponent_schedule_poss_n,
                "possessionSampleRequired": _POSSESSION_SAMPLE_TARGET,
                "teamPossessionVenue": (team_schedule_possession or {}).get("venue"),
                "opponentPossessionVenue": (opponent_schedule_possession or {}).get("venue"),
                "teamPossessionRows": list(
                    (team_schedule_possession or {}).get("rows") or []
                ),
                "opponentPossessionRows": list(
                    (opponent_schedule_possession or {}).get("rows") or []
                ),
                "teamPossessionStatus": _team_schedule_poss_status,
                "opponentPossessionStatus": _opponent_schedule_poss_status,
                "possessionRecencyWeighting": (
                    team_schedule_possession or {}
                ).get("recencyWeighting"),
                "possessionStatus": (
                    "verified" if _team_schedule_poss_verified else "unavailable"
                ),
                "possessionSource": (
                    "fixture_statistics_team_schedule"
                    if _team_schedule_poss_verified
                    else None
                ),
                "possessionComparison": (
                    "team schedules averaged "
                    f"{comp_poss_avg:.1f}% possession vs {comp_opp_poss_avg:.1f}% for "
                    f"the opponent"
                    if _team_schedule_poss_verified
                    else (
                        "verified team-schedule possession unavailable: "
                        f"team {team_schedule_poss_n}/{_POSSESSION_SAMPLE_TARGET} "
                        f"({_team_schedule_poss_status}), opponent "
                        f"{opponent_schedule_poss_n}/{_POSSESSION_SAMPLE_TARGET} "
                        f"({_opponent_schedule_poss_status})"
                    )
                ),
                "sampleSize": _cohort_evidence["sampleSize"],
                "minimumRecommendedSample": _cohort_evidence["minimumRecommendedSample"],
                "sampleStatus": _cohort_evidence["sampleStatus"],
                "overHits": _cohort_evidence["overHits"],
                "underHits": _cohort_evidence["underHits"],
                "overHitRate": _cohort_evidence["overHitRate"],
                "underHitRate": _cohort_evidence["underHitRate"],
                "unweightedAverage": _cohort_evidence.get("unweightedAverage"),
                "effectiveSampleSize": _cohort_evidence.get("effectiveSampleSize"),
                "weightMethod": _cohort_evidence.get("weightMethod"),
                "sampleUnit": _cohort_evidence.get("sampleUnit") or "team",
                "crossPropAverages": cross_prop_averages,
                "crossPropSampleSizes": cross_prop_samples,
                "propType": req.propType,
                "opponent": req.opponentName,
                "venue": player_venue,
                "targetPosition": specific_position or display_position,
                "targetRole": display_role or player_role,
                # Every admitted row has already passed exact-position
                # compatibility when projectionEligible is true. Broad
                # category rows are context only and do not broaden the
                # deterministic cohort adjustment.
                "comparisonMode": (
                    "broad-category"
                    if _broad_position_fallback or not _exact_target_for_comparison
                    else "same-position"
                ),
                "positionEvidenceType": (
                    "broad_category"
                    if _broad_position_fallback or not _exact_target_for_comparison
                    else "exact_position"
                ),
                # Compatibility wording retained for older source-contract
                # checks; the runtime value above is now explicit for exact
                # versus broad fallback evidence.
                # "broad_category" if player_position else "unavailable"
                "positionEvidenceNote": (
                    (
                        (
                            f"Exact {specific_position} rows were not returned for "
                            "the verified venue-matched fixtures; showing provider "
                            f"{player_position} rows as broad opponent context only. "
                            "These rows cannot change the deterministic projection."
                        )
                        if _broad_position_fallback
                        else (
                            f"Exact {specific_position} identity is verified from "
                            f"{str(_position_resolution_source or 'verified evidence').replace('_', ' ')}; "
                            "comparison rows are separate opponent-context evidence."
                        )
                    )
                    if specific_position in {
                        "GK", "CB", "LB", "RB", "LWB", "RWB", "CDM",
                        "CM", "CAM", "LM", "RM", "LW", "RW", "CF", "ST", "SS",
                    }
                    else (
                        f"Provider verifies the broad {player_position} category; "
                        "similar-player rows are broad-category context only; "
                        "no exact flank/central position was available, so it is not relabeled "
                        "or used to change the projection."
                        if player_position
                        else "No provider or lineup position evidence was available."
                    )
                ),
                # "projectionEligible": _exact_target_for_comparison
                "projectionEligible": (
                    _exact_target_for_comparison and not _broad_position_fallback
                ),
                "sourceScope": position_comparison_scope,
                "source": "api_football_fixture_player_stats",
                "comparisonAttempted": position_comparison_meta["attempted"],
                "comparisonStatus": position_comparison_meta["status"],
                "comparisonUnavailableReason": position_comparison_meta["unavailableReason"],
            }

        # The comparison attempt is part of every soccer prediction's response
        # contract, even when strict evidence filters produce zero rows. This
        # keeps "unavailable" distinct from a missing/failed payload and lets
        # the client explain exactly why no comparable players were shown.
        if req.sport == "soccer" and position_comp_data is None:
            _fallback_position = specific_position or display_position or player_position or None
            _fallback_exact = specific_position in {
                "GK", "CB", "LB", "RB", "LWB", "RWB", "CDM", "CM", "CAM",
                "LM", "RM", "LW", "RW", "CF", "ST", "SS",
            }
            _fallback_team_packet = team_schedule_possession or {}
            _fallback_opponent_packet = opponent_schedule_possession or {}
            _fallback_team_n = int(_fallback_team_packet.get("sampleSize") or 0)
            _fallback_opponent_n = int(
                _fallback_opponent_packet.get("sampleSize") or 0
            )
            _fallback_poss_verified = (
                _fallback_team_packet.get("verified") is True
                and _fallback_opponent_packet.get("verified") is True
                and _fallback_team_n >= _POSSESSION_SAMPLE_TARGET
                and _fallback_opponent_n >= _POSSESSION_SAMPLE_TARGET
            )
            position_comp_data = {
                "position": _fallback_position,
                "positionShort": _fallback_position,
                "players": [],
                "avgStatValue": None,
                "average": None,
                "weightedAverage": None,
                "avgPer90": None,
                "avgPossession": _fallback_team_packet.get("average"),
                "avgOpponentPossession": _fallback_opponent_packet.get("average"),
                "expectedPlayerPossession": None,
                "possessionSampleSize": min(_fallback_team_n, _fallback_opponent_n),
                "teamPossessionSampleSize": _fallback_team_n,
                "opponentPossessionSampleSize": _fallback_opponent_n,
                "possessionSampleRequired": _POSSESSION_SAMPLE_TARGET,
                "teamPossessionVenue": _fallback_team_packet.get("venue"),
                "opponentPossessionVenue": _fallback_opponent_packet.get("venue"),
                "teamPossessionRows": list(_fallback_team_packet.get("rows") or []),
                "opponentPossessionRows": list(
                    _fallback_opponent_packet.get("rows") or []
                ),
                "possessionStatus": (
                    "verified" if _fallback_poss_verified else "unavailable"
                ),
                "possessionSource": (
                    "fixture_statistics_team_schedule"
                    if _fallback_poss_verified
                    else None
                ),
                "possessionComparison": (
                    "team schedules averaged "
                    f"{float(_fallback_team_packet.get('average')):.1f}% possession vs "
                    f"{float(_fallback_opponent_packet.get('average')):.1f}% for the opponent"
                    if _fallback_poss_verified
                    else "verified team-schedule possession unavailable"
                ),
                "sampleSize": 0,
                "minimumRecommendedSample": 15,
                "sampleStatus": "unavailable",
                "overHits": 0,
                "underHits": 0,
                "overHitRate": None,
                "underHitRate": None,
                "unweightedAverage": None,
                "effectiveSampleSize": 0,
                "weightMethod": None,
                "crossPropAverages": {},
                "crossPropSampleSizes": {},
                "propType": req.propType,
                "opponent": req.opponentName,
                "venue": player_venue,
                "targetPosition": _fallback_position,
                "targetRole": display_role or player_role,
                "comparisonMode": "same-position" if _fallback_exact else "unavailable",
                "positionEvidenceType": "unavailable",
                "projectionEligible": False,
                "positionEvidenceNote": (
                    f"No verified comparable rows were returned: "
                    f"{position_comparison_meta['unavailableReason'] or 'unavailable'}."
                ),
                "sourceScope": position_comparison_scope,
                "source": "api_football_fixture_player_stats",
                "comparisonAttempted": position_comparison_meta["attempted"],
                "comparisonStatus": position_comparison_meta["status"],
                "comparisonUnavailableReason": position_comparison_meta["unavailableReason"],
            }

        # The exact-opponent comparison pool is assembled after the initial
        # Bayesian pass. For goalkeeper pass props, calculate its diagnostic
        # prior here and attach it to the final ledger. It remains shadow-only
        # until a walk-forward validation explicitly promotes the mode.
        if (
            req.sport == "soccer"
            and req.propType in {"pass_attempts", "passes"}
            and (
                str(_bayes_position or "").upper() in {"GK", "G", "GOALKEEPER"}
                or str(specific_position or "").upper() in {"GK", "G", "GOALKEEPER"}
            )
        ):
            try:
                from gk_pool_prior import build_gk_pool_prior
                _gk_pool_prior = build_gk_pool_prior(
                    position_comparison,
                    player_prior_mean=(early_bayes or {}).get("priorMean"),
                    mode=os.environ.get("GK_POOL_PRIOR_MODE", "shadow"),
                )
                _gk_pool_prior["sourceScope"] = position_comparison_scope
                print(
                    f"[GK POOL PRIOR] {req.playerName}: "
                    f"status={_gk_pool_prior.get('status')} "
                    f"mode={_gk_pool_prior.get('mode')} "
                    f"pool={_gk_pool_prior.get('poolMean')} "
                    f"n={_gk_pool_prior.get('poolRows')}"
                )
            except Exception as _gk_pool_err:
                print(f"[GK POOL PRIOR] non-fatal: {_gk_pool_err}")
            if isinstance(early_bayes, dict):
                early_bayes["goalkeeperPoolPrior"] = _gk_pool_prior

        # ── CATEGORY SAFETY VALVE ──────────────────────────────────────────────
        # Hard override: API-Football generic category is the ground truth.  If a
        # stale/wrong position cache resolved an attacking role for a player the
        # API categorises as "Defender", silently correct it here so the AI
        # narrative NEVER says "playing as a Poacher" for a centre-back.
        _ATTACKING_ROLES = {
            "Poacher", "Target Man", "False 9", "Shadow Striker",
            "Complete Forward", "Creative Forward", "Pressing Forward",
        }
        _ATTACKER_POSITIONS = {"ST", "CF", "SS"}
        if player_position == "Defender" and (
            player_role in _ATTACKING_ROLES or specific_position in _ATTACKER_POSITIONS
        ):
            print(
                f"[SAFETY VALVE] Defender {req.playerName} had attacking "
                f"pos={specific_position}/role={player_role} — correcting to CB/Stopper"
            )
            specific_position = specific_position if specific_position not in _ATTACKER_POSITIONS else "CB"
            player_role = "Stopper"
            display_position = specific_position
            display_role = player_role
            # Also correct the cached entry so this doesn't repeat
            try:
                await db.player_positions.update_one(
                    {"playerId": req.playerId},
                    {"$set": {"specificPosition": specific_position, "role": player_role}},
                )
            except Exception:
                pass

        # ── ROLE-FIRST EVIDENCE CONTRACT ────────────────────────────────────
        # This packet is built after the category safety valve so the persisted
        # role cannot disagree with the provider category.  It is descriptive
        # and auditable; projection math remains Bayesian and deterministic.
        role_evidence_packet = build_role_evidence_packet(
            position=specific_position or display_position or player_position,
            role=display_role or player_role,
            source=(_observed_role or {}).get("source") or _position_resolution_source,
            confidence=(_observed_role or {}).get("confidence"),
            lineup_status=_lineup_status,
            fixture_id=(match_odds or {}).get("fixtureId"),
            venue=player_venue,
            role_stats=_role_stats,
            player_logs=player_game_logs,
            comparable_players=position_comparison,
            prop_type=req.propType,
        )
        print(
            f"[ROLE EVIDENCE] {req.playerName}: "
            f"status={role_evidence_packet.get('status')} "
            f"position={role_evidence_packet.get('position')} "
            f"role={role_evidence_packet.get('role') or 'unknown'} "
            f"fixture={role_evidence_packet.get('fixtureId')}"
        )

        # ── First-Goal Profile (both teams, concurrent) ──────────────────────────
        _fg_team: dict = {}
        _fg_opp:  dict = {}
        _fg_scenario_weights: dict = {}
        if not ai_only_mode and actual_team_id and req.opponentId and not _is_bdl_league:
            try:
                from first_goal_engine import get_first_goal_profile, compute_scenario_weights as _fg_sw
                _fg_season = 2025
                _fg_results = await aio.gather(
                    get_first_goal_profile(actual_team_id, _fg_season, api_football_request, db),
                    get_first_goal_profile(req.opponentId,  _fg_season, api_football_request, db),
                    return_exceptions=True,
                )
                _fg_team = _fg_results[0] if not isinstance(_fg_results[0], Exception) else {}
                _fg_opp  = _fg_results[1] if not isinstance(_fg_results[1], Exception) else {}
                if _fg_team.get("available"):
                    _fg_scenario_weights = _fg_sw(_fg_team, req.propType)
                    print(f"[FIRST GOAL] {req.playerName}: teamFirst={_fg_team.get('teamScoredFirstPct'):.0%} oppFirst={_fg_team.get('opponentScoredFirstPct'):.0%} n={_fg_team.get('dataPoints')}")
            except Exception as _fge:
                print(f"[FIRST GOAL] engine failed: {_fge}")

        # Build structured evidence from the fetched data.
        # External narrative generation is retired; keep this compatibility
        # slot empty while the deterministic data digest and ledger remain the
        # authoritative evidence sources.
        ai_digest = ""
        evidence_blocks = []
        if ai_digest:
            evidence_blocks.append(f"[STRUCTURED EVIDENCE — MODEL DIGEST]\n{ai_digest}")
        if data_digest:
            evidence_blocks.append(f"[STRUCTURED EVIDENCE — DATA DIGEST]\n{data_digest}")
        if wave2_supplement:
            evidence_blocks.append(f"[STRUCTURED EVIDENCE — WAVE2 SUPPLEMENT]\n{json.dumps(wave2_supplement, default=str)[:5000]}")

        if _fg_team.get("available"):
            _fg_evidence_block = (
                f"[FIRST-GOAL EVIDENCE — last {_fg_team.get('dataPoints', 0)} matches]\n"
                f"Team ({corrected_team_name}) scored first: {round(_fg_team.get('teamScoredFirstPct', 0) * 100)}% of games\n"
                f"Opponent ({req.opponentName}) scored first: {round(_fg_team.get('opponentScoredFirstPct', 0) * 100)}% of games\n"
                f"No goal / goalless half: {round(_fg_team.get('noGoalPct', 0) * 100)}% of games\n"
                f"Avg first-goal minute: {_fg_team.get('avgFirstGoalMin', 35)}\n"
                f"Math-derived scenario weights → best: {round(_fg_scenario_weights.get('best', 0.40) * 100)}% / "
                f"base: {round(_fg_scenario_weights.get('base', 0.35) * 100)}% / "
                f"worst: {round(_fg_scenario_weights.get('worst', 0.25) * 100)}%\n"
                f">>> Use these rates to anchor scenarioProbabilities in your JSON. They are real data, not estimates. <<<"
            )
            if _fg_opp.get("available"):
                _fg_evidence_block += (
                    f"\nOpponent ({req.opponentName}) first-goal profile (their own recent matches): "
                    f"scored first {round(_fg_opp.get('teamScoredFirstPct', 0) * 100)}% / "
                    f"conceded first {round(_fg_opp.get('opponentScoredFirstPct', 0) * 100)}%"
                )
            evidence_blocks.append(_fg_evidence_block)

        # =============================================
        # Deterministic synthesis: projection comes ONLY from the math engine.
        # =============================================
        pv = early_bayes["posteriorMean"] if early_bayes and early_bayes.get("posteriorMean") else req.line
        _raw_model_conf = max(early_bayes.get("pOver", 50), early_bayes.get("pUnder", 50)) if early_bayes else 50
        prediction = {"projectedValue": pv, "recommendation": "over" if pv > req.line else "under", "confidenceScore": min(_raw_model_conf, 72), "reasoning": "", "sport": req.sport}
        # Expose current opponent quality tier so the frontend can display it.
        # Standings-based rank only exists when the CURRENT prediction's league_id
        # has a domestic/qualifying-group table — this silently fails for
        # friendlies, intercontinental playoffs, and any match without a table
        # (the exact case that was hiding the "vs {opponent} [TIER]" badge).
        # Fall back, in order: (1) curated national-team tier by name,
        # (2) odds-implied opponent win probability — always available for any
        # match with a betting market, regardless of competition.
        _cur_opp_rank = (standing_data or {}).get("oppRank")
        if _cur_opp_rank is not None:
            prediction["currentOppRank"] = _cur_opp_rank
            if _cur_opp_rank <= 6:
                prediction["currentOppTier"] = "ELITE"
            elif _cur_opp_rank <= 15:
                prediction["currentOppTier"] = "STRONG"
            elif _cur_opp_rank <= 30:
                prediction["currentOppTier"] = "MID"
            else:
                prediction["currentOppTier"] = "WEAK"
        else:
            _opp_name_l = (req.opponentName or "").lower().strip()
            _nat_tier = NATIONAL_TEAM_TIER.get(_opp_name_l)
            if _nat_tier is None and _opp_name_l:
                _nat_tier = next(
                    (v for k, v in NATIONAL_TEAM_TIER.items() if _opp_name_l in k or k in _opp_name_l), None
                )
            if _nat_tier is not None:
                prediction["currentOppTier"] = _nat_tier
                prediction["currentOppTierSource"] = "nationalTeamTable"
            elif match_odds and match_odds.get("bookmakerOdds"):
                try:
                    _hw = float(match_odds["bookmakerOdds"].get("homeWin") or 0)
                    _aw = float(match_odds["bookmakerOdds"].get("awayWin") or 0)
                    if _hw > 1.0 and _aw > 1.0:
                        _p_home = 1.0 / _hw
                        _p_away = 1.0 / _aw
                        _total = _p_home + _p_away
                        _p_home_norm = _p_home / _total if _total > 0 else 0.5
                        _player_is_home = match_odds.get("playerIsHome")
                        _opp_win_prob = (1.0 - _p_home_norm) if _player_is_home else _p_home_norm
                        if _opp_win_prob >= 0.55:
                            prediction["currentOppTier"] = "ELITE"
                        elif _opp_win_prob >= 0.40:
                            prediction["currentOppTier"] = "STRONG"
                        elif _opp_win_prob >= 0.25:
                            prediction["currentOppTier"] = "MID"
                        else:
                            prediction["currentOppTier"] = "WEAK"
                        prediction["currentOppTierSource"] = "oddsImplied"
                except (TypeError, ValueError):
                    pass
        # The deterministic explanation is complete in this response.
        prediction["aiPending"] = False

        # scenarioProbabilities: prefer AI-assigned values; fall back to first-goal math
        _sp = prediction.get("scenarioProbabilities")
        if (not isinstance(_sp, dict) or
                not all(isinstance(_sp.get(k), (int, float)) for k in ("best", "base", "worst")) or
                sum(_sp.get(k, 0) for k in ("best", "base", "worst")) < 0.5):
            if _fg_scenario_weights:
                prediction["scenarioProbabilities"] = _fg_scenario_weights
        else:
            # Normalise AI's values (they may not sum to 1.0 exactly)
            _sp_total = sum(_sp[k] for k in ("best", "base", "worst"))
            if _sp_total > 0:
                prediction["scenarioProbabilities"] = {
                    k: round(_sp[k] / _sp_total, 3) for k in ("best", "base", "worst")
                }

        # Confidence normalization
        cs = prediction.get("confidenceScore", 50)
        if isinstance(cs, (int, float)):
            prediction["confidenceScore"] = round(cs * 100 if cs <= 1 else cs)
        else:
            prediction["confidenceScore"] = 50

        prediction["consensusNote"] = f"Reverse Formula projection. Tactical analysis powered by ReverseScan."
        prediction["modelBreakdown"] = [{
            "model": "ReverseScan",
            "recommendation": prediction["recommendation"],
            "projectedValue": pv,
            "confidenceScore": prediction["confidenceScore"],
        }]

        # Set confidence level
        cs = prediction.get("confidenceScore", 50)
        prediction["confidenceLevel"] = "Very High" if cs >= 80 else "High" if cs >= 70 else "Medium" if cs >= 55 else "Low"

        # Store dominance info — will be applied POST-FUSION to the final number
        _dom_avg_is_real = bool(match_dominance.get("seasonAvgIsReal"))
        prediction["matchDominance"] = {
            "applied": match_dominance["multiplier"] != 1.0,
            "multiplier": match_dominance["multiplier"],
            "expectedPoss": match_dominance["expectedPoss"],
            "oppExpectedPoss": match_dominance.get("oppExpectedPoss"),
            # Only expose teamSeasonAvg/oppSeasonAvg when they're real season
            # averages — the odds-only fallback hardcodes 50.0/50.0 with zero
            # real signal behind it, and showing that to the UI as a static
            # "season avg" badge is misleading (see possession-fallback-unknown-tier.md).
            "teamSeasonAvg": match_dominance.get("teamSeasonAvg") if _dom_avg_is_real else None,
            "oppSeasonAvg": match_dominance.get("oppSeasonAvg") if _dom_avg_is_real else None,
            "seasonAvgIsReal": _dom_avg_is_real,
            "hasRealPossData": bool(match_dominance.get("hasRealPossData")),
            "possessionSource": match_dominance.get("possessionSource"),
            "possessionVerificationStatus": match_dominance.get(
                "possessionVerificationStatus"
            ),
            "possessionSampleRequired": _POSSESSION_SAMPLE_TARGET,
            "teamPossessionSampleSize": match_dominance.get(
                "teamPossessionSampleSize",
                0,
            ),
            "opponentPossessionSampleSize": match_dominance.get(
                "opponentPossessionSampleSize",
                0,
            ),
            "teamPossessionVenue": match_dominance.get("teamPossessionVenue"),
            "opponentPossessionVenue": match_dominance.get("opponentPossessionVenue"),
            "teamPossessionObservedAvg": match_dominance.get(
                "teamPossessionObservedAvg"
            ),
            "opponentPossessionObservedAvg": match_dominance.get(
                "opponentPossessionObservedAvg"
            ),
            "moneylineWeight": match_dominance.get("moneylineWeight", 0.0),
            "moneylineExpectedHomePoss": match_dominance.get(
                "moneylineExpectedHomePoss"
            ),
            "recencyWeighting": match_dominance.get("recencyWeighting"),
            "h2hPossAvg": match_dominance.get("h2hPossAvg"),
            "h2hPossCount": match_dominance.get("h2hPossCount"),
            "h2hPossRole": match_dominance.get("h2hPossRole"),
            "notes": match_dominance["notes"],
            "qualityGap": match_dominance.get("qualityGap"),
        }

        # =============================================
        # BAYESIAN — Reuse early computation (already done before structured evidence assembly)
        # =============================================
        real_bayes = early_bayes
        if real_bayes:
            prediction["bayesianMetrics"] = real_bayes
            prediction["confidenceInterval"] = real_bayes.get("confidenceInterval", prediction.get("confidenceInterval"))
            prediction["distribution"] = real_bayes.get("distribution") or {}
            prediction["mostLikelyValue"] = real_bayes.get("mostLikelyValue")
            prediction["range60"] = real_bayes.get("range60")
            prediction["range80"] = real_bayes.get("range80")

        # Expose the key engine inputs the UI needs to show "Model Factors"
        prediction["matchFactors"] = {
            "expectedPoss":   match_dominance.get("expectedPoss"),
            "oppExpectedPoss":match_dominance.get("oppExpectedPoss"),
            "possessionSource": match_dominance.get("possessionSource"),
            "possessionVerificationStatus": match_dominance.get(
                "possessionVerificationStatus"
            ),
            "possessionSampleRequired": _POSSESSION_SAMPLE_TARGET,
            "teamPossessionSampleSize": match_dominance.get(
                "teamPossessionSampleSize",
                0,
            ),
            "opponentPossessionSampleSize": match_dominance.get(
                "opponentPossessionSampleSize",
                0,
            ),
            "teamPossessionVenue": match_dominance.get("teamPossessionVenue"),
            "opponentPossessionVenue": match_dominance.get("opponentPossessionVenue"),
            "teamPossessionObservedAvg": match_dominance.get(
                "teamPossessionObservedAvg"
            ),
            "opponentPossessionObservedAvg": match_dominance.get(
                "opponentPossessionObservedAvg"
            ),
            "moneylineWeight": match_dominance.get("moneylineWeight", 0.0),
            "moneylineExpectedHomePoss": match_dominance.get(
                "moneylineExpectedHomePoss"
            ),
            "recencyWeighting": match_dominance.get("recencyWeighting"),
            "firstGoalProfile":     _fg_team if _fg_team.get("available") else None,
            "firstGoalOppProfile":  _fg_opp  if _fg_opp.get("available")  else None,
            "scenarioProbabilities": prediction.get("scenarioProbabilities") or _fg_scenario_weights or None,
            "h2hPossAvg":     match_dominance.get("h2hPossAvg"),
            "h2hPossCount":   match_dominance.get("h2hPossCount"),
            "possMultiplier": match_dominance.get("multiplier"),
            "matchStakes":    game_situation.get("matchStakes"),
            "bayesian": {
                "priorMean":     (real_bayes or {}).get("priorMean"),
                "posteriorMean": (real_bayes or {}).get("posteriorMean"),
                "priorSamples":  (real_bayes or {}).get("priorSamples"),
                "pOver":         (real_bayes or {}).get("pOver"),
                "pUnder":        (real_bayes or {}).get("pUnder"),
                "matchStakes":   (real_bayes or {}).get("matchStakes"),
                "cdmInversion":  (real_bayes or {}).get("cdmInversion"),
                "homeCdmDeepBlock": (real_bayes or {}).get("homeCdmDeepBlock"),
                "condPossAdj": locals().get("_cond_poss_result") and {
                    "basePoss":      locals()["_cond_poss_result"].get("base_poss"),
                    "adjustedPoss":  locals()["_cond_poss_result"].get("adjusted_poss"),
                    "deltaPP":       locals()["_cond_poss_result"].get("delta_pp"),
                    "trailScenario": locals()["_cond_poss_result"].get("trailing_scenario_poss"),
                    "leadScenario":  locals()["_cond_poss_result"].get("leading_scenario_poss"),
                    "pTrail":        locals()["_cond_poss_result"].get("p_trail"),
                    "pLead":         locals()["_cond_poss_result"].get("p_lead"),
                    "playerCede":    (locals()["_cond_poss_result"].get("player_style") or {}).get("possession_cede_when_leading"),
                    "playerChase":   (locals()["_cond_poss_result"].get("player_style") or {}).get("possession_chase_when_trailing"),
                    "oppCede":       (locals()["_cond_poss_result"].get("opp_style") or {}).get("possession_cede_when_leading"),
                    "oppStyleNotes": (locals()["_cond_poss_result"].get("opp_style") or {}).get("style_notes"),
                    "settledWinPoss": (locals()["_cond_poss_result"].get("player_settled") or {}).get("winning_poss"),
                    "settledLosePoss": (locals()["_cond_poss_result"].get("player_settled") or {}).get("losing_poss"),
                    "method":        locals()["_cond_poss_result"].get("method"),
                } or None,
                "leagueCalib":   (real_bayes or {}).get("leagueCalib"),
                "scenarioPriors":(real_bayes or {}).get("scenarioPriors"),
                "oppAllowedAvg": (real_bayes or {}).get("opponentAllowedAvg"),
                "oppAllowedN":   (real_bayes or {}).get("opponentAllowedSamples"),
                "oppAllowedWeight": (real_bayes or {}).get("opponentAllowedWeight"),
                "goalkeeperPoolPrior": (real_bayes or {}).get("goalkeeperPoolPrior"),
                "momentumLabel": (real_bayes or {}).get("momentumLabel"),
                "momentumEffect":(real_bayes or {}).get("momentumEffect"),
                "priorStd":      (real_bayes or {}).get("priorStd"),
                "pairShare":     (real_bayes or {}).get("pairShare"),
                "compSeasonAvg": (real_bayes or {}).get("compSeasonAvg"),
                "rawOppAllowedAvg": (real_bayes or {}).get("rawOppAllowedAvg"),
                "rotationRisk":  locals().get("_rotation_risk", "stable"),
                "rotationAdjPct": round(locals().get("_rotation_adj_pct", 0.0) * 100, 1),
                "expectedMinutes": round(locals().get("_exp_mins", 90.0), 1),
                "teamQualityGap": (real_bayes or {}).get("teamQualityGap"),
                "line": req.line,
            },
            "pressureResponse": _pressure_response,
            "statsbombEnrichment": statsbomb_enrichment,
            "positionPassesReceived": (
                (statsbomb_enrichment.get("eventMetrics") or {})
                .get("positionPassesReceived")
            ),
        }

        # Mirror condPossAdj into bayesianMetrics so the mobile structured-evidence
        # view can find it at pred.bayesianMetrics.condPossAdj
        _cp_res = locals().get("_cond_poss_result")
        if _cp_res and prediction.get("bayesianMetrics") is not None:
            prediction["bayesianMetrics"]["condPossAdj"] = {
                "basePoss":      _cp_res.get("base_poss"),
                "adjustedPoss":  _cp_res.get("adjusted_poss"),
                "deltaPP":       _cp_res.get("delta_pp"),
                "trailingPoss":  _cp_res.get("trailing_scenario_poss"),
                "leadingPoss":   _cp_res.get("leading_scenario_poss"),
                "pTrail":        _cp_res.get("p_trail"),
                "pLead":         _cp_res.get("p_lead"),
                "playerCede":    (_cp_res.get("player_style") or {}).get("possession_cede_when_leading"),
                "playerChase":   (_cp_res.get("player_style") or {}).get("possession_chase_when_trailing"),
                "oppCede":       (_cp_res.get("opp_style") or {}).get("possession_cede_when_leading"),
                "oppCedeSrc":    _cp_res.get("method"),
                "oppStyleNotes": (_cp_res.get("opp_style") or {}).get("style_notes"),
                "signals":       _cp_res.get("signals"),
                "settledWinPoss":  (_cp_res.get("player_settled") or {}).get("winning_poss"),
                "settledLosePoss": (_cp_res.get("player_settled") or {}).get("losing_poss"),
            }

        # =============================================
        # =============================================
        # BAYESIAN-ONLY PROJECTION
        #
        # The math OWNS the number. Period.
        # Structured evidence provides tactical reasoning text only — no numeric influence.
        # The Bayesian posterior IS the projected value.
        # =============================================
        if real_bayes and real_bayes.get("priorSamples", 0) >= 3:
            bayesian_posterior = real_bayes["posteriorMean"]
            _record_projection_factor(
                "bayesian_engine",
                "Three-layer Bayesian engine",
                real_bayes.get("priorMean"),
                bayesian_posterior,
                inputs={
                    "priorMean": real_bayes.get("priorMean"),
                    "momentumMean": real_bayes.get("momentumMean"),
                    "momentumEffect": real_bayes.get("momentumEffect"),
                    "covariateAdjustment": real_bayes.get("covariateAdjustment"),
                    "priorSamples": real_bayes.get("priorSamples"),
                    "priorWeight": real_bayes.get("priorWeight"),
                    "momentumWeight": real_bayes.get("momentumWeight"),
                    "covariateWeight": real_bayes.get("covariateWeight"),
                },
                sample_size=real_bayes.get("priorSamples"),
                reason=(
                    lambda _rb=real_bayes: (
                        lambda _pm=_rb.get("priorMean"), _n=_rb.get("priorSamples", 0),
                               _ml=str(_rb.get("momentumLabel") or "STABLE").upper(),
                               _me=float(_rb.get("momentumEffect") or 0),
                               _ca=float(_rb.get("covariateAdjustment") or 0): (
                            f"Season prior: {round(_pm, 1)} across {_n} logs"
                            if _pm else f"{_n} qualifying logs"
                        ) + (
                            f"; momentum {_ml.title()} ({_me:+.1f})"
                            if _ml not in {"STABLE", ""} and abs(_me) >= 0.3 else ""
                        ) + (
                            f"; match-context covariate {_ca:+.1f}"
                            if abs(_ca) >= 0.5 else ""
                        ) + "."
                    )()
                )(),
            )

            # ─── OPPONENT H2H PRIOR ADJUSTMENT ────────────────────────────────────
            # Blend player's historical stats vs THIS specific opponent into the prior.
            # Captures opponent-specific patterns season averages can't see:
            # e.g., a player who averages 70 passes/game but only 55 vs this opponent.
            # Weight is proportional to H2H sample size, capped at 25% max influence —
            # season average always holds at least 75% authority.
            # Venue-filtered when enough same-venue H2H games exist (home vs home, away vs away).
            _h2h_summary = historical_data.get("h2hPlayerStats", {})
            _h2h_avg = _h2h_summary.get("avgVsOpponent")
            _h2h_n = _h2h_summary.get("sampleSize", 0)

            if _h2h_avg is not None and _h2h_n >= 2:
                # Prefer same-venue H2H data when available (>= 2 games at same venue)
                _venue_vals = [
                    s["targetStat"] for s in h2h_player_stats
                    if s.get("venue") == req.venue and s.get("targetStat") is not None
                ]
                if len(_venue_vals) >= 2:
                    _h2h_avg_use = round(sum(_venue_vals) / len(_venue_vals), 2)
                    _h2h_n_use = len(_venue_vals)
                    _venue_note = f"venue-filtered ({req.venue})"
                else:
                    _h2h_avg_use = _h2h_avg
                    _h2h_n_use = _h2h_n
                    _venue_note = "all venues"

                # Weight: 5% per H2H game, max 25% — season data always dominates
                _h2h_weight = min(_h2h_n_use * 0.05, 0.25)
                # HIGH-TRUST H2H WEIGHT (13% per game, cap 40%):
                # Opponent-specific history dominates over season baseline for
                # defensive volume props where press shape is highly predictive.
                # GK pass_attempts: opponent pressing style is the single most predictive
                # factor after venue. CB/CDM pass props: specific opponent's press
                # intensity and block depth are highly repeatable patterns.
                _is_gk_h2h = (specific_position or "").upper() in {"GK", "GOALKEEPER"} or \
                              (player_position or "").lower() == "goalkeeper"
                _DEF_VOL_ROLES = {"CB", "CDM", "DM", "LB", "RB", "LWB", "RWB", "SW"}
                _DEF_VOL_PROPS = {"pass_attempts", "passes", "tackles", "interceptions", "blocks", "clearances"}
                _is_def_vol_h2h = (
                    req.propType in _DEF_VOL_PROPS and
                    ((specific_position or "").upper() in _DEF_VOL_ROLES or
                     (player_role or "").upper() in _DEF_VOL_ROLES)
                )
                if (_is_gk_h2h and req.propType in {"pass_attempts", "passes"}) or _is_def_vol_h2h:
                    _h2h_weight = min(_h2h_n_use * 0.13, 0.40)  # 13% per game, cap 40%
                _old_bp = bayesian_posterior
                bayesian_posterior = round(
                    _old_bp * (1 - _h2h_weight) + _h2h_avg_use * _h2h_weight, 1
                )
                _record_projection_factor(
                    "opponent_h2h_blend",
                    "Direct player H2H blend",
                    _old_bp,
                    bayesian_posterior,
                    inputs={"h2hAverage": _h2h_avg_use, "weightPct": round(_h2h_weight * 100), "venue": _venue_note},
                    sample_size=_h2h_n_use,
                    multiplier=1 - _h2h_weight,
                    reason="Blended the player's verified appearances against this opponent into the posterior.",
                )
                real_bayes["opponentH2HAvg"] = _h2h_avg_use
                real_bayes["opponentH2HSamples"] = _h2h_n_use
                real_bayes["opponentH2HWeight"] = round(_h2h_weight * 100)
                real_bayes["posteriorMean"] = bayesian_posterior

                if abs(bayesian_posterior - _old_bp) >= 0.3:
                    direction = "▲" if bayesian_posterior > _old_bp else "▼"
                    print(
                        f"[H2H ADJ] {req.playerName} vs {req.opponentName}: "
                        f"H2H avg={_h2h_avg_use} ({_h2h_n_use} games, {_venue_note}, "
                        f"weight={_h2h_weight:.0%}) {direction} {_old_bp:.1f} → {bayesian_posterior:.1f}"
                    )

                # ── H2H LINE HIT RATE — UNANIMOUS SIGNAL ─────────────────────────
                # Separate from the avg-blend above. When ALL same-venue H2H games
                # cleared the line the same way (e.g., 2/2 OVER 38.5), the
                # weighted-average approach will always land between the season avg
                # and the H2H avg — which may never cross the line when the two
                # anchors straddle it. This block treats unanimous line-crossing as
                # independent hard evidence and applies an ADDITIONAL pull toward the
                # H2H avg, strong enough to cross the line.
                #
                # Weight: 20% per same-venue game, capped at 55%.
                # Fires when: ≥2 same-venue H2H games AND ≥75% went same direction.
                # Guard: "all venues" fallback does NOT trigger this — only
                # venue-filtered data (we need location-specific evidence).
                # ─────────────────────────────────────────────────────────────────
                if req.line and len(_venue_vals) >= 2:
                    _h2h_over_n   = sum(1 for v in _venue_vals if v > req.line)
                    _h2h_under_n  = len(_venue_vals) - _h2h_over_n
                    _h2h_line_n   = len(_venue_vals)
                    _h2h_over_pct = _h2h_over_n / _h2h_line_n

                    if _h2h_over_pct >= 0.75 or _h2h_over_pct <= 0.25:
                        # Pull toward a target that is definitively on the dominant side
                        if _h2h_over_pct >= 0.75:
                            # ≥75% of same-venue H2H went OVER → target above the line
                            _h2h_line_target = max(_h2h_avg_use, req.line + 1.5)
                        else:
                            # ≥75% went UNDER → target below the line
                            _h2h_line_target = min(_h2h_avg_use, req.line - 1.5)

                        _h2h_line_weight = min(_h2h_line_n * 0.20, 0.55)
                        _old_bp2 = bayesian_posterior
                        bayesian_posterior = round(
                            _old_bp2 * (1 - _h2h_line_weight) + _h2h_line_target * _h2h_line_weight, 1
                        )
                        _record_projection_factor(
                            "h2h_line_signal",
                            "Unanimous same-venue H2H line signal",
                            _old_bp2,
                            bayesian_posterior,
                            inputs={
                                "target": _h2h_line_target,
                                "overPct": round(_h2h_over_pct * 100),
                                "line": req.line,
                                "weightPct": round(_h2h_line_weight * 100),
                            },
                            sample_size=_h2h_line_n,
                            multiplier=1 - _h2h_line_weight,
                            reason="Same-venue H2H appearances consistently cleared one side of the line.",
                        )
                        real_bayes["h2hLineHitRate"]   = round(_h2h_over_pct * 100)
                        real_bayes["h2hLineSampleN"]   = _h2h_line_n
                        real_bayes["posteriorMean"]    = bayesian_posterior

                        if abs(bayesian_posterior - _old_bp2) >= 0.3:
                            _ldir = "▲" if bayesian_posterior > _old_bp2 else "▼"
                            _ldir_word = "OVER" if _h2h_over_pct >= 0.75 else "UNDER"
                            print(
                                f"[H2H LINE SIGNAL] {req.playerName} vs {req.opponentName}: "
                                f"{_h2h_over_n}/{_h2h_line_n} same-venue H2H {_ldir_word} {req.line} "
                                f"({_h2h_over_pct:.0%}) → target={_h2h_line_target:.1f} "
                                f"weight={_h2h_line_weight:.0%} {_ldir} {_old_bp2:.1f} → {bayesian_posterior:.1f}"
                            )
                # ─────────────────────────────────────────────────────────────────

            # ─────────────────────────────────────────────────────────────────────

            # ─── OPPONENT DEFENSIVE PROFILE ADJUSTMENT ────────────────────────────
            # Blend in what same-position players produce against THIS opponent.
            # Captures opponent-style effects that season averages can't see:
            # e.g., PSG's press suppresses opposing CB pass volume league-wide,
            # or a low-block team inflates opposition shot attempts.
            # Data source: fetch_position_comparison — same position, same venue,
            # opponent's recent fixture-player sample (already computed above
            # for the evidence/model context).
            # Weight: 2.5% per comparison player, max 15%.
            # Requires at least 3 sampled players to fire (noise guard).
            # Applied AFTER personal H2H blend, BEFORE situational multiplier.
            # ──────────────────────────────────────────────────────────────────────
            if position_comp_data and position_comp_data.get("projectionEligible"):
                _opp_allowed_avg = position_comp_data.get("avgStatValue", 0)
                _opp_allowed_n   = position_comp_data.get("sampleSize", 0)
                _opp_pos_label   = position_comp_data.get("positionShort", "?")
                if _opp_allowed_avg and _opp_allowed_n >= 3:
                    _opp_weight = min(_opp_allowed_n * 0.025, 0.15)  # base: 2.5% per player, max 15%
                    _old_bp = bayesian_posterior

                    # ── PAIR CALIBRATION ──────────────────────────────────────────────
                    # Comparison players' raw stat vs this opponent reflects their actual
                    # output — but these players may be dominant role players (e.g. primary
                    # CB averaging 55+), while the target is secondary (averaging 38-42).
                    # Blending toward the raw comparison avg over-projects the secondary
                    # player. Fix: compute the opponent's RELATIVE uplift vs those same
                    # players' normal season averages, then apply that same uplift ratio
                    # to THIS player's own baseline level.
                    #
                    #   uplift         = opp_allowed_avg / comp_players_season_avg
                    #   calibrated_opp = player_posterior × uplift
                    #
                    # Only fires for pass-sensitive props when ≥2 comparison players have
                    # a known season average (populated by _fetch_comp_player_stats).
                    # Capped at ±50% of raw opp avg to prevent runaway adjustments.
                    # ──────────────────────────────────────────────────────────────────
                    _pair_calib_props = {"pass_attempts", "passes", "key_passes", "crosses"}
                    if req.propType in _pair_calib_props and position_comparison:
                        _comp_seas = [
                            p["seasonAvgStat"] for p in position_comparison
                            if p.get("seasonAvgStat") and p["seasonAvgStat"] > 0
                        ]
                        if len(_comp_seas) >= 2:
                            _comp_seas_avg = sum(_comp_seas) / len(_comp_seas)
                            if _comp_seas_avg > 0:
                                _opp_uplift = _opp_allowed_avg / _comp_seas_avg
                                _cal_opp    = round(_old_bp * _opp_uplift, 1)
                                # Cap: calibrated must stay within [50%, 150%] of raw opp avg
                                _cal_opp = max(
                                    round(_opp_allowed_avg * 0.50, 1),
                                    min(round(_opp_allowed_avg * 1.50, 1), _cal_opp)
                                )
                                _pair_share = round(_old_bp / _comp_seas_avg, 3)
                                real_bayes["pairShare"]        = _pair_share
                                real_bayes["compSeasonAvg"]    = round(_comp_seas_avg, 1)
                                real_bayes["rawOppAllowedAvg"] = _opp_allowed_avg
                                if abs(_cal_opp - _opp_allowed_avg) >= 0.5:
                                    print(
                                        f"[PAIR CAL] {req.propType} {_opp_pos_label}: "
                                        f"player={_old_bp:.1f} comp_seas={_comp_seas_avg:.1f} "
                                        f"share={_pair_share:.2f} uplift={_opp_uplift:.2f}× "
                                        f"opp {_opp_allowed_avg:.1f}→{_cal_opp:.1f}"
                                    )
                                _opp_allowed_avg = _cal_opp

                    # ── CONVERGENCE BOOST ────────────────────────────────────────────────
                    # When possession dominance AND opponent profile BOTH point the same
                    # direction with meaningful magnitude for pass-sensitive props,
                    # they are measuring the same underlying truth (this matchup inflates/
                    # suppresses pass volume). Compound them by increasing opp_weight.
                    # Without this boost the 15% cap keeps the signal too weak vs the
                    # Bayesian season-average anchor — e.g. a dominant home CB vs a
                    # low-block side where opp avg=85 and poss=63% still lands <line.
                    # ────────────────────────────────────────────────────────────────────
                    _poss_sens = {"pass_attempts", "passes", "key_passes", "crosses", "dribbles"}
                    _is_gk_conv = (specific_position or "").upper() in {"GK", "GOALKEEPER"} or (player_position or "").lower() == "goalkeeper"
                    # The audit record below is shared by all opponent-profile
                    # predictions, but the possession comparison only runs for
                    # possession-sensitive outfield props. Initialize the
                    # convergence inputs before that narrower branch so GK and
                    # unrelated props cannot raise UnboundLocalError.
                    _has_poss_data = False
                    _poss_diff = 0.0
                    _opp_diff = 0.0
                    if req.propType in _poss_sens and not _is_gk_conv:
                        _exp_poss  = match_dominance.get("expectedPoss")
                        _avg_poss  = match_dominance.get("teamSeasonAvg")
                        _opp_diff  = _opp_allowed_avg - _old_bp # +ve = opp allows more than proj
                        # expectedPoss/teamSeasonAvg are ALWAYS floats (default 50.0),
                        # never None — checking hasRealPossData (set only when
                        # compute_match_dominance found a genuine signal) is the only
                        # reliable way to know whether poss_diff below is real or a
                        # meaningless 0.0-vs-0.0 default comparison.
                        _has_poss_data = (
                            match_dominance.get("hasRealPossData")
                            and _exp_poss is not None and _avg_poss is not None
                        )
                        if _has_poss_data:
                            _poss_diff = _exp_poss - _avg_poss      # +ve = more poss than usual
                        else:
                            _poss_diff = 0.0
                        # Same-direction AND both material (≥5pp poss gap, ≥5 stat gap)
                        if (_has_poss_data
                                and _poss_diff * _opp_diff > 0
                                and abs(_poss_diff) >= 5
                                and abs(_opp_diff) >= 5):
                            # Boost scales with possession gap: 5pp→0.05 extra, 10pp→0.10, cap 0.15
                            _conv_boost = min(abs(_poss_diff) / 100.0, 0.15)
                            _opp_weight = min(_opp_weight + _conv_boost, 0.30)  # hard cap 30%
                            print(
                                f"[OPP CONVERGENCE] {req.propType}: poss_diff={_poss_diff:+.1f}pp "
                                f"opp_diff={_opp_diff:+.1f} → weight {_opp_weight:.0%} "
                                f"(+{_conv_boost:.0%} alignment boost)"
                            )
                        elif not _has_poss_data and _old_bp:
                            # ── INDEPENDENT-SIGNAL BOOST ────────────────────────────
                            # No possession projection exists for this fixture (common
                            # for international friendlies vs minnows with sparse
                            # pre-match data), so the convergence check above can never
                            # fire. But the opponent-allowed-avg signal is itself real
                            # and independently measured (recent games vs this opponent
                            # at this position) — it shouldn't be strangled to an 8-15%
                            # weight just because a SEPARATE data source is missing.
                            # Only fires for a strong signal (≥30% relative gap, i.e.
                            # "elite leak"/"elite suppressor" tier) with a decent sample,
                            # and the boost is smaller than full convergence (cap 22%
                            # vs 30%) since it isn't cross-confirmed by possession data.
                            _opp_rel_pct = abs(_opp_diff) / max(abs(_old_bp), 1e-6)
                            if _opp_rel_pct >= 0.30 and _opp_allowed_n >= 3:
                                _indep_boost = min(_opp_rel_pct * 0.25, 0.10)
                                _opp_weight = min(_opp_weight + _indep_boost, 0.22)
                                print(
                                    f"[OPP INDEPENDENT SIGNAL] {req.propType}: "
                                    f"opp_diff={_opp_diff:+.1f} ({_opp_rel_pct:.0%} of prior, "
                                    f"no possession data available) → weight {_opp_weight:.0%} "
                                    f"(+{_indep_boost:.0%} boost, n={_opp_allowed_n})"
                                )

                    bayesian_posterior = round(
                        _old_bp * (1 - _opp_weight) + _opp_allowed_avg * _opp_weight, 1
                    )
                    _record_projection_factor(
                        "opponent_profile",
                        "Same-position opponent profile",
                        _old_bp,
                        bayesian_posterior,
                        inputs={
                            "allowedAverage": _opp_allowed_avg,
                            "sampleSize": _opp_allowed_n,
                            "weightPct": round(_opp_weight * 100),
                            "pairShare": real_bayes.get("pairShare"),
                            "comparisonSeasonAverage": real_bayes.get("compSeasonAvg"),
                            "rawAllowedAverage": real_bayes.get("rawOppAllowedAvg"),
                            "convergence": bool(_has_poss_data and _poss_diff * _opp_diff > 0
                                                and abs(_poss_diff) >= 5 and abs(_opp_diff) >= 5),
                        },
                        sample_size=_opp_allowed_n,
                        multiplier=1 - _opp_weight,
                        reason="Compared the opponent's recent output allowed to same-position players, pair-calibrated to this player's baseline.",
                    )
                    real_bayes["opponentAllowedAvg"]     = round(_opp_allowed_avg, 1)
                    real_bayes["opponentAllowedSamples"] = _opp_allowed_n
                    real_bayes["opponentAllowedWeight"]  = round(_opp_weight * 100)
                    real_bayes["posteriorMean"] = bayesian_posterior
                    # Explicit Layer 2: turn the exact same-position opponent
                    # evidence into a Gaussian likelihood update.  The
                    # likelihood standard deviation is chosen to preserve the
                    # established evidence-weight cap, while making the
                    # calculation auditable as prior × likelihood rather than
                    # an unexplained arithmetic blend.
                    try:
                        _layer1_std = max(
                            float(real_bayes.get("posteriorStd") or 0),
                            float(real_bayes.get("priorStd") or 0),
                            abs(float(_old_bp)) * 0.05,
                            0.1,
                        )
                        _layer2_weight = max(0.01, min(0.30, float(_opp_weight)))
                        _layer2_std = _layer1_std * math.sqrt(
                            (1.0 - _layer2_weight) / _layer2_weight
                        )
                        real_bayes["threeLayerModel"] = {
                            "version": "three-layer-gaussian-v1",
                            "layer1": {
                                "name": "player_baseline",
                                "mean": round(float(_old_bp), 1),
                                "std": round(_layer1_std, 2),
                                "source": "player_history_and_role_baseline",
                            },
                            "layer2": {
                                **gaussian_likelihood_update(
                                    prior_mean=_old_bp,
                                    prior_std=_layer1_std,
                                    likelihood_mean=_opp_allowed_avg,
                                    likelihood_std=_layer2_std,
                                ),
                                "name": "opponent_same_position_likelihood",
                                "sampleSize": _opp_allowed_n,
                                "weightPct": round(_layer2_weight * 100, 1),
                                "source": "exact_opponent_same_position_same_venue",
                            },
                            "layer3": {
                                "name": "live_gaussian_remaining_total",
                                "status": "available_when_match_is_live",
                                "source": "saved_pre_match_distribution_plus_observed_drift",
                            },
                        }
                    except (TypeError, ValueError, ZeroDivisionError) as _layer_err:
                        print(f"[3-LAYER] opponent likelihood metadata failed: {_layer_err}")
                    if abs(bayesian_posterior - _old_bp) >= 0.2:
                        _dir = "▲" if bayesian_posterior > _old_bp else "▼"
                        print(
                            f"[OPP PROFILE] {_opp_pos_label}s vs {req.opponentName} "
                            f"({player_venue.upper()}): allowed avg={_opp_allowed_avg:.1f} "
                            f"({_opp_allowed_n} players, weight={_opp_weight:.0%}) "
                            f"{_dir} {_old_bp:.1f} → {bayesian_posterior:.1f}"
                        )
            # ─────────────────────────────────────────────────────────────────────

            # Keep an explicit three-layer packet even when the opponent cohort
            # is unavailable. Missing evidence is unavailable, never fabricated.
            if real_bayes and not real_bayes.get("threeLayerModel"):
                real_bayes["threeLayerModel"] = {
                    "version": "three-layer-gaussian-v1",
                    "layer1": {
                        "name": "player_baseline",
                        "mean": real_bayes.get("posteriorMean"),
                        "std": real_bayes.get("posteriorStd"),
                        "source": "player_history_and_role_baseline",
                    },
                    "layer2": {
                        "name": "opponent_same_position_likelihood",
                        "status": "unavailable",
                        "reason": "No verified exact-position opponent cohort",
                    },
                    "layer3": {
                        "name": "live_gaussian_remaining_total",
                        "status": "available_when_match_is_live",
                        "source": "saved_pre_match_distribution_plus_observed_drift",
                    },
                }

            # ─── SITUATIONAL MULTIPLIER — applied BEFORE final number is locked ───
            # When game state demands different output than seasonal avg, scale the projection.
            _sit_m = game_situation.get("multipliers", {})
            _sit_bayes_mult = _sit_m.get("bayesianMultiplierHome", 1.0) if _sit_is_home else _sit_m.get("bayesianMultiplierAway", 1.0)
            if _sit_bayes_mult != 1.0:
                _old_bp = bayesian_posterior
                bayesian_posterior = round(bayesian_posterior * _sit_bayes_mult, 1)
                _record_projection_factor(
                    "situational_multiplier",
                    "Match situation multiplier",
                    _old_bp,
                    bayesian_posterior,
                    inputs={"multiplier": _sit_bayes_mult, "matchStakes": game_situation.get("matchStakes")},
                    multiplier=_sit_bayes_mult,
                    reason="Adjusted the posterior for the match-state and tactical situation.",
                )
                print(f"[SITUATION MULT] Bayesian {_old_bp:.1f} × {_sit_bayes_mult:.3f} = {bayesian_posterior:.1f} ({req.propType})")
                real_bayes["posteriorMean"] = bayesian_posterior
                real_bayes["situationalMultiplier"] = _sit_bayes_mult
            # ─────────────────────────────────────────────────────────────────────

            # ── KNOCKOUT EXTRA-TIME (ET) ADJUSTMENT ──────────────────────────────
            # Knockout games go to ET (2×15 additional minutes) ~30% of the time.
            # Count stats (pass_attempts, shots, saves…) scale linearly with
            # minutes played. Without this adjustment the engine chronically
            # under-projects for UNDER bets → actual >>> projected when ET fires.
            # Settled WC knockout data: 50% hit rate vs 64% group stage.
            # Multiplier = 1 + P(ET) × (30 extra min / 90 base min) ≈ 1.100
            #
            # DESIGN: applied BEFORE P-REFRESH so the normal-distribution CDF
            # that recomputes p_over/p_under already sees the ET-inflated mean.
            # Consequently UNDER edges shrink (correct) and OVER edges grow.
            # Separate confidence penalty blocks UNDER confidence further.
            # ──────────────────────────────────────────────────────────────────────
            # Safe defaults — must be initialized here so async code paths
            # that skip the main bayesian block still have these defined when
            # the KNOCKOUT UNDER CONFIDENCE PENALTY check fires at line ~7159.
            _final_is_knockout = False
            _KO_COUNT_PROPS = {
                "pass_attempts", "passes", "shots", "shots_on_target",
                "saves", "key_passes", "crosses", "dribbles", "tackles", "clearances",
            }
            # Resolve is_knockout: prefer situation engine flag (always defined),
            # fall back to the match_context local var which is only set when
            # match_odds is present.
            _final_is_knockout = game_situation.get("isKnockout", False)
            if not _final_is_knockout:
                _ko_kws = ("final", "quarter", "semi", "round of", "knockout", "elimination", "playoff")
                _raw_round_ko = (match_odds or {}).get("matchRound", "") if match_odds else ""
                if _raw_round_ko:
                    _final_is_knockout = any(kw in _raw_round_ko.lower() for kw in _ko_kws)

            if _final_is_knockout and req.propType in _KO_COUNT_PROPS:
                _KO_ET_PROB  = 0.30           # 30 % of knockout games go to ET historically
                _KO_ET_MULT  = round(1.0 + _KO_ET_PROB * (30.0 / 90.0), 4)  # ≈ 1.1000
                _ko_old_bp   = bayesian_posterior
                bayesian_posterior = round(bayesian_posterior * _KO_ET_MULT, 1)
                _record_projection_factor(
                    "knockout_extra_time",
                    "Knockout extra-time exposure",
                    _ko_old_bp,
                    bayesian_posterior,
                    inputs={"extraTimeProbability": _KO_ET_PROB, "extraMinutes": 30, "knockout": True},
                    multiplier=_KO_ET_MULT,
                    reason="Added expected count volume from the possibility of 30 minutes of extra time.",
                )
                real_bayes["posteriorMean"]    = bayesian_posterior
                real_bayes["koExtraTimeAdj"]   = _KO_ET_MULT
                real_bayes["koExtraTimeProb"]  = _KO_ET_PROB
                print(
                    f"[KNOCKOUT ET ADJ] {req.playerName}/{req.propType}: "
                    f"{_ko_old_bp:.1f} × {_KO_ET_MULT:.4f} → {bayesian_posterior:.1f} "
                    f"(P(ET)={_KO_ET_PROB:.0%})"
                )
            # ─────────────────────────────────────────────────────────────────────

            # ── TEAM QUALITY / GAME-CONTROL GAP ─────────────────────────────────
            # Possession already has a Bayesian squeeze and a match-dominance
            # multiplier. This independent factor uses standings and normalized
            # fixture odds for its numeric signal; verified possession can only
            # corroborate it, never add another possession multiplier.
            _quality_gap = compute_team_quality_gap(
                match_odds=match_odds,
                standing_data=standing_data,
                match_dominance=match_dominance,
                requested_league_id=req.leagueId,
                prop_type=req.propType,
                position=(
                    specific_position
                    or locals().get("_bayes_position")
                    or player_position
                    or ""
                ),
            )
            match_dominance["qualityGap"] = _quality_gap
            if _quality_gap.get("applied") and abs(_quality_gap.get("multiplier", 1.0) - 1.0) > 0.0001:
                _quality_old_bp = bayesian_posterior
                bayesian_posterior = round(
                    bayesian_posterior * _quality_gap["multiplier"], 1
                )
                _record_projection_factor(
                    "team_quality_gap",
                    "Team quality and game-control gap",
                    _quality_old_bp,
                    bayesian_posterior,
                    inputs={
                        "score": _quality_gap.get("score"),
                        "deltaPct": _quality_gap.get("deltaPct"),
                        "direction": _quality_gap.get("direction"),
                        "competition": _quality_gap.get("competition"),
                        "signals": _quality_gap.get("signals"),
                        "possessionCorroborates": _quality_gap.get("possessionCorroborates"),
                        "possessionUsedForNumericAdjustment": False,
                    },
                    multiplier=_quality_gap.get("multiplier"),
                    reason=_quality_gap.get("reason", ""),
                )
                real_bayes["posteriorMean"] = bayesian_posterior
                print(
                    f"[QUALITY GAP] {req.playerName}/{req.propType}: "
                    f"{_quality_old_bp:.1f} × {_quality_gap['multiplier']:.4f} "
                    f"→ {bayesian_posterior:.1f}"
                )
            if real_bayes is not None:
                real_bayes["teamQualityGap"] = _quality_gap
            if isinstance(prediction.get("matchDominance"), dict):
                prediction["matchDominance"]["qualityGap"] = _quality_gap
            if isinstance(prediction.get("matchFactors"), dict):
                prediction["matchFactors"]["teamQualityGap"] = _quality_gap

            # ── RECOMPUTE P(over)/P(under) AFTER OPP-PROFILE + SITUATION MULT ──
            # The opponent profile (and situational multiplier) can shift bayesian_posterior
            # significantly — e.g. 39.1 → 43.0 — AFTER pOver/pUnder were frozen by the
            # Bayesian engine. If we don't refresh the probabilities here, BAYESIAN TRUTH
            # reads the stale pOver=35.6% and locks in UNDER even though the final
            # projection is clearly in OVER territory.
            # Use the predictive std (game-to-game variability), not posteriorStd
            # which is the credible interval for the mean (often ~0.3) and far too
            # tight for P(over a line). Mirror the engine's effective_std formula:
            # max(posterior_std, prior_std*0.55, posterior_mean*0.17)
            _rb_prior_std    = real_bayes.get("priorStd") or 0.0
            _rb_post_std_raw = real_bayes.get("posteriorStd") or 0.0
            _rb_eff_std = max(
                _rb_post_std_raw,
                _rb_prior_std * 0.55,
                bayesian_posterior * 0.17,
            )
            if _rb_eff_std > 0 and req.line:
                try:
                    import math as _math
                    def _norm_cdf(x):
                        return 0.5 * (1 + _math.erf(x / _math.sqrt(2)))
                    _z = (req.line - bayesian_posterior) / _rb_eff_std
                    _new_p_under = round(100 * _norm_cdf(_z), 1)
                    _new_p_over  = round(100 - _new_p_under, 1)
                    _old_p_over  = real_bayes.get("pOver", 50)
                    if abs(_new_p_over - _old_p_over) >= 2.0:
                        real_bayes["pOver"]  = _new_p_over
                        real_bayes["pUnder"] = _new_p_under
                        _new_rec = "over" if _new_p_over >= _new_p_under else "under"
                        real_bayes["recommendation"] = _new_rec
                        print(
                            f"[P-REFRESH] {req.playerName}/{req.propType}: "
                            f"posterior={bayesian_posterior} eff_std={_rb_eff_std:.2f} "
                            f"→ P(over) {_old_p_over}% → {_new_p_over}% rec={_new_rec.upper()}"
                        )
                except Exception as _pr_err:
                    print(f"[P-REFRESH-ERR] {_pr_err}")
            # ─────────────────────────────────────────────────────────────────────

            bayesian_prob = max(real_bayes.get("pOver", 50), real_bayes.get("pUnder", 50)) / 100
            bayesian_rec = real_bayes.get("recommendation", "over")
            # early_proj = early_bayes estimate before full multi-factor Bayesian run
            early_proj = prediction.get("projectedValue", req.line)
            early_rec  = prediction.get("recommendation", "over")

            divergence_pct = abs(early_proj - bayesian_posterior) / max(bayesian_posterior, 1) * 100

            # Log when early estimate and full Bayesian differ noticeably (adjustment audit trail)
            if divergence_pct > 10 and bayesian_rec != early_rec:
                print(f"[BAYES ADJUST] Early={early_proj}({early_rec}) → Full Bayes={bayesian_posterior}({bayesian_rec}) — {divergence_pct:.0f}% shift after all adjustments.")

            print(f"[PROJECTION] Bayesian={bayesian_posterior}({bayesian_rec}, {bayesian_prob:.0%}) | Early estimate={early_proj}({early_rec}) — ledger math is final. Structured evidence is explanation only.")

            # ── Apply nightly-learned bias offsets ──────────────────────────
            # GK pass_attempts UNDER: the GK inverted possession model already achieves
            # 70% UNDER hit rate through position-specific logic. The general UNDER offset
            # (+1.94) is driven by MID UNDER failures and must NOT be applied to GKs —
            # it would push correct GK UNDER projections above the line and flip them to OVER.
            # GK pass_attempts OVER still benefits from the direction correction (-1.14).
            _is_gk_pass_under = (
                req.propType == "pass_attempts"
                and bayesian_rec == "under"
                and (specific_position or "").upper() in {"GK", "GOALKEEPER"}
            )
            if CALIBRATION_ENABLED:
                try:
                    from calibration import apply_learned_offsets
                    _offset_venue = player_venue or req.venue or "home"
                    # For GK UNDER: skip direction offset, fall through to venue/league
                    _cal_rec = None if _is_gk_pass_under else bayesian_rec
                    bayesian_posterior, _offset_note = await apply_learned_offsets(
                        posterior=bayesian_posterior,
                        prop_type=req.propType,
                        venue=_offset_venue,
                        recommendation=_cal_rec,
                        league_id=req.leagueId,
                        sport="soccer",
                    )
                    if _offset_note:
                        # Recalculate direction from calibrated posterior, then apply
                        # probability override: when P(UNDER) > P(OVER), prefer UNDER
                        # even if the calibrated mean is slightly above the line.
                        _cal_rec_by_mean = "over" if bayesian_posterior > req.line else "under"
                        _rb_p_over  = real_bayes.get("pOver", 50)
                        _rb_p_under = real_bayes.get("pUnder", 50)
                        if _cal_rec_by_mean == "over" and _rb_p_under > _rb_p_over:
                            bayesian_rec = "under"
                            print(f"[PROB DIRECTION] {req.playerName}: post-cal mean={bayesian_posterior} (OVER) "
                                  f"but P(UNDER)={_rb_p_under}%>P(OVER)={_rb_p_over}% → UNDER")
                        elif _cal_rec_by_mean == "under" and _rb_p_over > _rb_p_under:
                            bayesian_rec = "over"
                            print(f"[PROB DIRECTION] {req.playerName}: post-cal mean={bayesian_posterior} (UNDER) "
                                  f"but P(OVER)={_rb_p_over}%>P(UNDER)={_rb_p_under}% → OVER")
                        else:
                            bayesian_rec = _cal_rec_by_mean
                        real_bayes["posteriorMean"] = bayesian_posterior
                except Exception as _oe:
                    print(f"[NIGHTLY CAL APPLY] Error applying offsets: {_oe}")
            else:
                print("[NIGHTLY CAL] Calibration disabled — raw Bayesian posterior used.")
            # ───────────────────────────────────────────────────────────────

            prediction["projectedValue"] = bayesian_posterior
            prediction["recommendation"] = bayesian_rec
            prediction["fusionApplied"] = {
                "earlyEstimate": early_proj,        # math's early_bayes estimate before all adjustments
                "earlyEstimateRec": early_rec,
                "bayesianPosterior": bayesian_posterior,
                "bayesianRecommendation": bayesian_rec,
                "bayesianConfidence": round(bayesian_prob * 100, 1),
                "fusedProjection": bayesian_posterior,
                "fusedRecommendation": bayesian_rec,
                "weights": {"math": 1.0, "structuredEvidence": 0},  # Structured evidence = explanation only, zero weight in projection
                "agreement": bayesian_rec == early_rec,
                "divergencePct": round(divergence_pct, 1),
                "note": "projectedValue is determined entirely by the Reverse Formula math engine. Structured evidence writes explanation text only.",
            }

            pass  # Math Lock runs after PASS GATE below — see [MATH LOCK] block

        # =============================================
        # POST-PROJECTION DOMINANCE SCALING — NON-PASS PROPS ONLY
        #
        # The Bayesian engine owns possession-sensitive pass volume. Applying
        # match_dominance["multiplier"] again here double-counts possession and
        # was the source of clustered passing-prop projection errors. Keep this
        # route-level adjustment disabled for every prop in this set; the
        # variable remains available for the non-pass tempo/favorite audit below.
        # =============================================
        poss_sensitive = {"pass_attempts", "passes", "key_passes", "crosses", "dribbles"}
        _post_dom_props = set()

        _is_gk_dom = (specific_position or "").upper() in {"GK", "GOALKEEPER"} or (player_position or "").lower() == "goalkeeper"
        if req.propType in _post_dom_props and not _is_gk_dom and match_dominance.get("multiplier", 1.0) != 1.0:
            dom_mult = match_dominance["multiplier"]
            team_avg_poss = match_dominance.get("teamSeasonAvg", 50)
            exp_poss      = match_dominance.get("expectedPoss", 50)
            current = prediction.get("projectedValue", req.line)

            if team_avg_poss < 52 and dom_mult < 0.92:
                # Low-possession team facing a dominant opponent — scale down
                post_dom = round(current * dom_mult, 1)
                prediction["projectedValue"] = post_dom
                prediction["recommendation"] = "over" if post_dom > req.line else "under"
                print(f"[DOMINANCE] APPLIED: {current} × {dom_mult:.3f} → {post_dom} (team avg {team_avg_poss:.0f}% < 52% threshold)")
            elif dom_mult > 1.08 and exp_poss > team_avg_poss + 8 and team_avg_poss < 52:
                # Team expected to significantly exceed their own season-average possession.
                # ONLY applies to LOW-possession teams (avg < 52%). High-possession teams
                # already have their Bayesian calibrated to their possession style.
                #
                # COLD-STREAK GATE: If the player's recent form (momentumMean) is already
                # running >4 passes below their season average, the form is the dominant
                # signal — it likely reflects WHY possession isn't translating to more volume
                # for this specific player (tactical role, fatigue, manager decisions).
                # Applying a possession boost on top fights this signal and over-inflates.
                _eb_momentum = (early_bayes or {}).get("momentumMean")
                _eb_prior    = (early_bayes or {}).get("priorMean")
                _cold_streak = (
                    _eb_momentum is not None and _eb_prior is not None
                    and _eb_momentum < _eb_prior - 4
                )
                if _cold_streak:
                    print(
                        f"[DOMINANCE] SKIP positive boost — cold streak: "
                        f"form={_eb_momentum:.1f} vs season_avg={_eb_prior:.1f} "
                        f"(gap={_eb_prior - _eb_momentum:.1f}). Form is the lead signal."
                    )
                else:
                    # Damping schedule (fraction of raw mult excess applied):
                    #   team_avg < 42% → 55% (rarely in possession — surge is highly anomalous)
                    #   team_avg < 48% → 40% (below-average — meaningful departure from norm)
                    #   team_avg 48-52% → 20% (approaching normal — Bayesian covers most of it)
                    if team_avg_poss < 42:
                        _damp_frac = 0.55
                    elif team_avg_poss < 48:
                        _damp_frac = 0.40
                    else:
                        _damp_frac = 0.20
                    _damped_mult = 1.0 + (dom_mult - 1.0) * _damp_frac
                    post_dom = round(current * _damped_mult, 1)
                    _old_rec = prediction.get("recommendation", "over")
                    prediction["projectedValue"] = post_dom
                    prediction["recommendation"] = "over" if post_dom > req.line else "under"
                    print(
                        f"[DOMINANCE] POSITIVE: {current} × {_damped_mult:.3f} → {post_dom} "
                        f"(exp {exp_poss:.0f}% vs avg {team_avg_poss:.0f}%, raw mult={dom_mult:.3f})"
                    )
                    # If the positive boost flipped the recommendation, the AI confidence was
                    # calibrated for the opposite direction — reset it based on the new edge.
                    _new_rec = prediction["recommendation"]
                    if _new_rec != _old_rec or True:  # always recalibrate after DOMINANCE
                        _dom_edge = abs(post_dom - req.line)
                        # Base: 55% + 1.5% per pass over the line, capped at 68%
                        _base_conf = min(68, round(55 + _dom_edge * 1.5))
                    prediction["confidenceScore"] = _base_conf
                    print(f"[DOMINANCE] Confidence recalibrated: {_base_conf}% (edge={_dom_edge:.1f})")
                    # Recalibrate edgeZ so downstream guards use the final edge
                    if real_bayes:
                        _bstd = real_bayes.get("posteriorStd", 10) or 10
                        real_bayes["edgeZ"] = round(abs(post_dom - req.line) / max(_bstd, 5), 2)
            else:
                would_be = round(current * dom_mult, 1)
                print(f"[DOMINANCE] SKIPPED: {current} × {dom_mult:.3f} would be {would_be} (team avg {team_avg_poss:.0f}% — Bayesian covers this)")

        if req.propType in poss_sensitive and game_tempo.get("tempoMultiplier", 1.0) != 1.0:
            tempo_mult = game_tempo["tempoMultiplier"]
            current = prediction.get("projectedValue", req.line)
            print(f"[TEMPO] LOGGED ONLY: {current} × {tempo_mult:.3f} (NOT applied)")

        if favorite_dampening.get("applied") and req.propType in poss_sensitive:
            fav_factor = favorite_dampening["dampeningFactor"]
            current = prediction.get("projectedValue", req.line)
            print(f"[FAV DAMPEN] LOGGED ONLY: {current} × {1.0-fav_factor:.3f} (NOT applied)")

        # HARD GUARD: recommendation MUST match the FINAL projected value vs line
        final_proj = prediction.get("projectedValue", req.line)
        prediction["recommendation"] = "over" if final_proj > req.line else "under"

        # ── Inject redistribution + lineup alerts into tacticalAlerts ────────
        if _redist_alerts:
            prediction["tacticalAlerts"] = prediction.get("tacticalAlerts", []) + _redist_alerts
        if _lineup_alert:
            prediction["tacticalAlerts"] = prediction.get("tacticalAlerts", []) + [_lineup_alert]
        if _lineup_status == "starting":
            prediction["lineupConfirmed"] = True
        elif _lineup_status in ("substitute", "not_in_squad"):
            prediction["lineupWarning"] = True

        # ── Risk signals (red-card/dismissal volatility) + fixture congestion ──
        # NOTE: mobile/lib/api.ts#PredictionResult['riskSignals'] expects
        # {yellowCardAvg, redCardRisk: 'low'|'elevated'|'high', opponentYellowCardAvg, note}
        # — keep this mapping in sync with that interface, not the internal _risk_signals shape.
        try:
            _rs = _risk_signals
            _level_map = {"normal": "low", "moderate": "elevated", "elevated": "high"}
            prediction["riskSignals"] = {
                "yellowCardAvg": _rs.get("teamCardsAvg"),
                "opponentYellowCardAvg": _rs.get("oppCardsAvg"),
                "redCardRisk": _level_map.get(_rs.get("level"), "low"),
                "note": _rs.get("note"),
            }
            if _rs.get("note"):
                prediction["tacticalAlerts"] = prediction.get("tacticalAlerts", []) + [_rs["note"]]
        except NameError:
            prediction["riskSignals"] = {"yellowCardAvg": None, "opponentYellowCardAvg": None, "redCardRisk": "low", "note": None}

        # NOTE: mobile/lib/api.ts#PredictionResult['congestion'] expects
        # {teamRestDays, opponentRestDays, teamGamesIn14d, opponentGamesIn14d, fatigueFlag: 'low'|'moderate'|'high'}
        try:
            _fatigue_layer = (early_bayes or {}).get("fatigueLayer", {}) or {}
            # NOTE: congestion_games is None when there wasn't enough dated
            # game-log history to compute a real games-in-14d count (common
            # for national-team/tournament contexts with sparse logs) — keep
            # it None rather than coercing to 0, which would misleadingly
            # read as "confirmed zero games" instead of "not enough data".
            _cong_games = _fatigue_layer.get("congestion_games")
            _fatigue_flag = "high" if (_cong_games or 0) >= 4 else ("moderate" if (_cong_games or 0) >= 3 else "low")
            prediction["congestion"] = {
                "teamRestDays": _fatigue_layer.get("rest_days"),
                "opponentRestDays": _fatigue_layer.get("opponent_rest_days"),
                "teamGamesIn14d": _cong_games,
                "opponentGamesIn14d": _fatigue_layer.get("opponent_congestion_games"),
                "fatigueFlag": _fatigue_flag,
            }
        except Exception:
            prediction["congestion"] = {
                "teamRestDays": None, "opponentRestDays": None,
                "teamGamesIn14d": None, "opponentGamesIn14d": None, "fatigueFlag": "low",
            }

        # ── Lineup pitch data (predicted or confirmed XI + formation) ──
        # NOTE: mobile/components/PitchDiagram.tsx expects {status, home:{teamName,formation,coach,players[]}, away:{...}}
        _raw_lineup = locals().get("_pitch_lineup") or {}
        _is_player_home = bool(_is_home)
        _team_side = {
            "teamName": _canonical_team_name or None,
            "formation": _raw_lineup.get("formation"),
            "coach": _raw_lineup.get("coach"),
            "players": _raw_lineup.get("players") or [],
        }
        _opp_side = {
            "teamName": _canonical_opponent_name or None,
            "formation": _raw_lineup.get("opponentFormation"),
            "coach": _raw_lineup.get("opponentCoach"),
            "players": _raw_lineup.get("opponentPlayers") or [],
        }
        # A pitch is only useful when both sides are present and both nominal
        # shapes are known. Do not render one team's lineup as a complete
        # matchup or let a missing provider formation become tactical evidence.
        _has_lineup_data = bool(
            _team_side["players"]
            and _opp_side["players"]
            and _team_side["formation"]
            and _opp_side["formation"]
        )
        prediction["lineup"] = {
            "status": _raw_lineup.get("status") or "unavailable",
            "home": _team_side if _is_player_home else _opp_side,
            "away": _opp_side if _is_player_home else _team_side,
        } if _has_lineup_data else None

        # =============================================
        # POST-CONSENSUS CONFIDENCE GUARDS
        # =============================================
        conf = prediction.get("confidenceScore", 50)
        proj_val = prediction.get("projectedValue", req.line)
        edge = abs(proj_val - req.line)
        rec = prediction.get("recommendation", "over")

        # Guard 0: Direction-specific blocked prop types
        # clearances OVER: 0% hit rate (0W 5L) — all picks had margin ≤ 0.5 above line.
        # Clearances UNDER hits at 100% (4W 0L) and is NOT penalized.
        # shots OVER at thin margins (margin < 1.0): 10% hit rate — discrete count means
        # proj=2 vs line=1.5 is a 50/50 coin flip that the model systematically over-calls.
        if req.propType == "clearances" and rec == "over":
            prediction["confidenceScore"] = 45
            prediction["coinFlip"] = True
            prediction["tacticalAlerts"] = prediction.get("tacticalAlerts", []) + [
                "CLEARANCES OVER blocked: 0% historical hit rate — bookmakers set these lines precisely. Clearances UNDER remains viable."
            ]
            print(f"[GUARD 0] clearances OVER → forced to 45% coin-flip (0% hit rate, data n=5)")

        if req.propType in {"shots", "shots_on_target"} and rec == "over" and edge < 1.0:
            prediction["confidenceScore"] = 45
            prediction["coinFlip"] = True
            prediction["tacticalAlerts"] = prediction.get("tacticalAlerts", []) + [
                f"SHOTS OVER blocked: proj={proj_val} only +{edge:.1f} above line {req.line}. "
                "For discrete shot counts a margin < 1 is a coin flip — model shows 10% hit rate here."
            ]
            print(f"[GUARD 0b] shots OVER margin={edge:.1f} < 1.0 → forced to 45% coin-flip")

        # Guard 1: Binary line (0.5) — UNDER means zero, very risky
        if req.line <= 0.5 and rec == "under" and conf > 55:
            prediction["confidenceScore"] = 55
            prediction["tacticalAlerts"] = prediction.get("tacticalAlerts", []) + [
                "Binary line (0.5): UNDER requires ZERO of this stat — high-risk"
            ]
            print(f"[GUARD] Binary line 0.5 UNDER: confidence capped at 55% (was {conf})")

        # Guard 2: Tight edge — projected value within ±1 of line
        if edge < 1.0 and conf > 58:
            prediction["confidenceScore"] = 58
            prediction["tacticalAlerts"] = prediction.get("tacticalAlerts", []) + [
                f"Tight edge: projection {proj_val} is within 1.0 of line {req.line} — marginal"
            ]
            print(f"[GUARD] Tight edge ({edge:.1f}): confidence capped at 58% (was {conf})")

        # Guard 3: Coin-flip zone
        # Hard threshold: any pick with edge < 2.0 is a coin flip regardless of
        # Bayesian probability — the projected value is so close to the line that
        # market noise dominates. Previously gated by bayes_conf < 60% which
        # allowed near-zero edge picks (e.g. proj=66 vs line=65.5) to slip through
        # as full-confidence picks.
        _bayes_conf_g3 = 50
        if real_bayes:
            _bayes_conf_g3 = max(real_bayes.get("pOver", 50), real_bayes.get("pUnder", 50))
        if edge < 2.0 or (edge < 3.0 and _bayes_conf_g3 < 60):
            old_conf = prediction.get("confidenceScore", 50)
            prediction["confidenceScore"] = min(old_conf, 52)
            prediction["coinFlip"] = True
            prediction["tacticalAlerts"] = prediction.get("tacticalAlerts", []) + [
                f"COIN FLIP: Edge only {edge:.1f} vs line {req.line} (proj={prediction.get('projectedValue','?')}). Bayesian P={_bayes_conf_g3}%. Near-line picks are variance-driven."
            ]
            print(f"[GUARD] Coin-flip zone: edge={edge:.1f}, Bayesian P={_bayes_conf_g3}% → capped at 52% (was {old_conf})")

        # Guard 3-PASS: pass_attempts OVER thin-margin extension.
        # Backtest (n=479 OVER pass_attempts picks):
        #   edge < 3 → 45% hit rate regardless of Bayesian confidence.
        #   edge 3-10 → 56% hit rate.
        #   edge 10+ → 56% hit rate.
        # Guard 3 only catches edge < 2.0 unconditionally. Picks with edge 2-3 and
        # bayes_conf ≥ 60% slip through and hit at 45% — worse than random.
        # Fix: extend coin-flip zone to edge < 3.0 for pass_attempts OVER.
        # UNDER is NOT penalized (UNDER hits at 65% across all margin buckets).
        if req.propType in {"pass_attempts", "passes"} and rec == "over" and edge < 3.0:
            old_conf = prediction.get("confidenceScore", 50)
            if old_conf > 52:
                prediction["confidenceScore"] = 52
                prediction["coinFlip"] = True
                prediction["tacticalAlerts"] = prediction.get("tacticalAlerts", []) + [
                    f"PASS OVER thin edge: proj {proj_val} is only +{edge:.1f} above line {req.line}. "
                    "Backtest shows <3 edge OVER picks hit at 45% — coin flip territory."
                ]
                print(f"[GUARD 3-PASS] pass_attempts OVER edge={edge:.1f} < 3.0 → capped at 52% (was {old_conf})")

        # Guard 3a: High-confidence OVER coin-flip flag.
        # 30-day backtest: OVER ≥70% confidence hits at only 45.7% (32/70) — WORSE
        # than random. Breakdown by prop: pass_attempts OVER ≥70% = 22/55 = 40%.
        # The model's upward projection bias is most extreme at high confidence.
        # When the model is very "certain" about an OVER, the upward bias has pulled
        # the projection far above the line — exactly where the model is most wrong.
        #
        # Fix: flag all OVER picks at ≥70% confidence as coin flips (capped at 55%).
        # They remain visible in the app but are clearly marked as uncertain.
        # UNDER picks are NOT penalized — UNDER 50-59% hits at 65.8% (better than
        # high-confidence OVER), so the confidence score for UNDER is already
        # mis-calibrated low and should not be penalised further.
        if rec == "over":
            _over_conf = prediction.get("confidenceScore", 50)
            if _over_conf >= 70:
                prediction["confidenceScore"] = min(_over_conf, 55)
                prediction["coinFlip"] = True
                prediction["tacticalAlerts"] = prediction.get("tacticalAlerts", []) + [
                    f"OVER BIAS: High-confidence OVER picks hit at only 45.7% in 30-day data — model's upward projection bias is strongest here. Treat as coin flip."
                ]
                print(f"[GUARD 3a] High-conf OVER ({_over_conf}%) flagged as coin flip → 55%")

        # Guard 3b: High-scoring game CB pass volatility
        # CBs in high expected-total games (Vegas line ≥ 4.0 goals) show extreme
        # pass variance — goals create chaos, shape changes kill steady build-up.
        # Moussa Niakhaté (Lyon 4-2 Rennes): two OVER picks both missed badly.
        # Reduce confidence so users aren't overexposed to volatile defender props
        # in goal-fests.
        _cb_volatile_pos = {"CB", "LB", "RB", "LCB", "RCB", "WB", "WBL", "WBR"}
        _pos_upper_g3b = (player_position or "").upper()
        if (_pos_upper_g3b in _cb_volatile_pos
                and req.propType in {"pass_attempts", "passes"}
                and _game_script and isinstance(_game_script, dict)):
            _gs_total_g3b = _game_script.get("expected_total_goals", 0) or 0
            if _gs_total_g3b >= 4.0:
                _hs_penalty = min(14, round((_gs_total_g3b - 3.5) * 5))
                _pre_hs = prediction.get("confidenceScore", 50)
                prediction["confidenceScore"] = max(47, _pre_hs - _hs_penalty)
                prediction["tacticalAlerts"] = prediction.get("tacticalAlerts", []) + [
                    f"HIGH-SCORING GAME: Defender pass volume highly volatile in {_gs_total_g3b}-goal expected games — confidence reduced"
                ]
                if _pre_hs != prediction["confidenceScore"]:
                    print(f"[GUARD] High-scoring CB volatility: total={_gs_total_g3b} -{_hs_penalty}% ({_pre_hs}→{prediction['confidenceScore']})")

        # Guard 3c: open_close scenario — high confidence picks in close-game scenarios
        # hit at only 31% (8/26) in 30-day backtest. When the pre-game model assigns
        # >35% probability to an open/close (1-goal game) result, the outcome is
        # too random for high-confidence calls. Cap these at 62%.
        _p_open_close = (_scenario_probs or {}).get("P_open_close", 0)
        if _p_open_close > 0.35:
            _oc_conf = prediction.get("confidenceScore", 50)
            if _oc_conf >= 70:
                _oc_penalty = min(22, round(_p_open_close * 35))
                prediction["confidenceScore"] = max(52, _oc_conf - _oc_penalty)
                prediction["tacticalAlerts"] = prediction.get("tacticalAlerts", []) + [
                    f"OPEN/CLOSE SCENARIO ({_p_open_close*100:.0f}% game probability): High-confidence picks in close-game scenarios hit at only 31% — confidence reduced"
                ]
                if _oc_conf != prediction["confidenceScore"]:
                    print(f"[GUARD 3c] open_close scenario: P={_p_open_close:.2f}, -{_oc_penalty}% ({_oc_conf}→{prediction['confidenceScore']})")

        # Guard 3d: draw scenario — low confidence picks in draw-probability games
        # hit at only 50% (75/149) — indistinguishable from random.
        # When draw probability > 30% and the model has low confidence anyway (<60%),
        # the pick has no edge. Cap at 50%.
        _p_draw = (_scenario_probs or {}).get("P_draw", 0)
        if _p_draw > 0.30:
            _draw_conf = prediction.get("confidenceScore", 50)
            if _draw_conf < 60:
                _draw_penalty = min(10, round(_p_draw * 20))
                prediction["confidenceScore"] = max(45, _draw_conf - _draw_penalty)
                prediction["tacticalAlerts"] = prediction.get("tacticalAlerts", []) + [
                    f"DRAW SCENARIO ({_p_draw*100:.0f}% probability): Low-confidence picks in draw-likely games are near-random — confidence reduced"
                ]
                if _draw_conf != prediction["confidenceScore"]:
                    print(f"[GUARD 3d] draw scenario: P={_p_draw:.2f}, -{_draw_penalty}% ({_draw_conf}→{prediction['confidenceScore']})")

        # Guard 3d-ii: draw scenario + OVER + CB/CM/CAM pass_attempts = catastrophic
        # Empirical: owner DRAW OVER pass_attempts hits only 25.9% (7/27).
        # CB in draws: 33.3%, CM in draws: 0%, CAM in draws: 0%.
        # The model applies CB lead-manage boosts and CDM chase-mode boosts which
        # OVERFIRE in draw scenarios — predicting OVER when possession stays even
        # and no lead needs managing. Hard cap confidence at 52% for these combos.
        _draw_over_pos_set = {"CB", "LCB", "RCB", "CM", "MC", "CAM", "AM", "LM", "RM"}
        if (_p_draw > 0.25
                and req.propType in {"pass_attempts", "passes"}
                and str(prediction.get("recommendation", "")).lower() == "over"
                and str(_bayes_position or "").upper() in _draw_over_pos_set):
            _d2_pre = prediction.get("confidenceScore", 50)
            if _d2_pre > 52:
                prediction["confidenceScore"] = 52
                prediction["confidenceLevel"] = "Low"
                prediction["tacticalAlerts"] = prediction.get("tacticalAlerts", []) + [
                    f"DRAW + OVER WARNING ({_p_draw*100:.0f}% draw probability): "
                    f"{_bayes_position} pass OVER picks in draw scenarios hit only 26% historically — confidence capped"
                ]
                print(f"[GUARD 3d-ii] draw+OVER+{_bayes_position} pass_attempts: P_draw={_p_draw:.2f} "
                      f"conf {_d2_pre}→52 (empirical 26% hit rate)")

        # Guard 3e: home_blowout + away + OVER pass_attempts
        # Empirical: owner OVER in home_blowout scenarios hits only 25% (3/12).
        # Away players in blowouts park the bus / defend deep → minimal passing,
        # long clearances replace build-up sequences. Model over-projects away
        # pass volume because it expects normal game-state possession fractions.
        _p_home_blowout = (_scenario_probs or {}).get("P_home_blowout", 0)
        if (_p_home_blowout > 0.25
                and req.propType in {"pass_attempts", "passes"}
                and str(prediction.get("recommendation", "")).lower() == "over"
                and str(player_venue or "").lower() == "away"):
            _hb_pre = prediction.get("confidenceScore", 50)
            if _hb_pre > 52:
                prediction["confidenceScore"] = 52
                prediction["confidenceLevel"] = "Low"
                prediction["tacticalAlerts"] = prediction.get("tacticalAlerts", []) + [
                    f"HOME BLOWOUT + AWAY OVER WARNING ({_p_home_blowout*100:.0f}% blowout probability): "
                    f"Away pass OVER picks in blowout scenarios hit only 25% — away team parks bus and passes fall"
                ]
                print(f"[GUARD 3e] home_blowout+away OVER pass_attempts: P={_p_home_blowout:.2f} "
                      f"conf {_hb_pre}→52 (empirical 25% hit rate)")

        # Guard 3f: Bundesliga home OVER pass_attempts confidence cap
        # Empirical: Bundesliga (ID 78) home OVER hits only 30.8% (4/13).
        # High-press vertical style — GKs/CBs pass count runs 13% below model's
        # cross-league prior. Bundesliga deflation already applied in the Bayesian
        # engine (×0.87), but if projection still lands OVER after deflation
        # we add a visible warning and cap confidence at 58%.
        if (req.leagueId == 78
                and req.propType in {"pass_attempts", "passes"}
                and str(prediction.get("recommendation", "")).lower() == "over"
                and str(player_venue or "").lower() == "home"):
            _bf_pre = prediction.get("confidenceScore", 50)
            if _bf_pre > 58:
                prediction["confidenceScore"] = 58
                prediction["tacticalAlerts"] = prediction.get("tacticalAlerts", []) + [
                    "BUNDESLIGA HOME OVER: High-press league — pass counts run 13% below model prior. "
                    "Historical hit rate 31% on home OVER pass picks. Confidence capped."
                ]
                print(f"[GUARD 3f] Bundesliga home OVER pass_attempts: conf {_bf_pre}→58")

        # Guard 4: Base-rate conflict — model recommendation fights the player's own season average.
        # When the season average sits on the OPPOSITE side of the line from the recommendation,
        # an external factor (possession squeeze, opponent matchup) is overriding the base rate.
        # These picks historically have lower accuracy because the base rate is a very strong prior.
        # Apply a confidence penalty proportional to how far the average is on the wrong side.
        _prior_m = (real_bayes or {}).get("priorMean")
        if _prior_m is not None and req.line > 0:
            _base_says_over = _prior_m > req.line
            _model_says_over = rec == "over"
            if _base_says_over != _model_says_over:
                _conflict_gap = abs(_prior_m - req.line)
                # Penalty: 15% flat minimum, +3% per pass of conflict gap beyond 2, capped at 25%
                _conflict_penalty = min(25, max(15, round(15 + (_conflict_gap - 2) * 3)))
                _pre_conflict = prediction.get("confidenceScore", 50)
                prediction["confidenceScore"] = max(45, _pre_conflict - _conflict_penalty)
                _conflict_dir = "OVER" if _base_says_over else "UNDER"
                prediction["tacticalAlerts"] = prediction.get("tacticalAlerts", []) + [
                    f"BASE-RATE CONFLICT: Season avg {_prior_m} is on the {_conflict_dir} side of line {req.line} — contextual model fights historical norm"
                ]
                print(
                    f"[GUARD] Base-rate conflict: season avg {_prior_m} is {_conflict_dir} of line {req.line}, "
                    f"rec={rec.upper()}, gap={_conflict_gap:.1f} → -{_conflict_penalty}% conf "
                    f"({_pre_conflict} → {prediction['confidenceScore']})"
                )

        # Guard 4b: Unverified venue — no fixture data was available to confirm the
        # user-supplied home/away assignment.  The pipeline may have processed game
        # logs, possession, and conditional-possession under an incorrect venue.
        # Very high confidence would be misleading; cap at 65 so the pick can still
        # be surfaced but is not presented as a strong recommendation.
        if locals().get("_venue_source") == "request":
            _vu_conf = prediction.get("confidenceScore", 50)
            if _vu_conf > 65:
                prediction["confidenceScore"] = 65
                prediction["tacticalAlerts"] = prediction.get("tacticalAlerts", []) + [
                    "UNVERIFIED VENUE: Fixture data unavailable to confirm home/away assignment — confidence capped"
                ]
                print(f"[VENUE GUARD] No fixture data; confidence {_vu_conf}→65 (unverified venue)")

        # Guard 5: Line-Deviation Intelligence — data-driven market asymmetry guard.
        # Uses the deviation band system (calibration.py) to adjust confidence
        # based on how far the book's line is from our model's projection.
        # The further our rec disagrees with where the book set the line, the more
        # we trust the book's information over our model's historical baseline.
        #
        # Hit rates by band are LEARNED from settled picks (self-improving).
        # When insufficient settled data exists, empirically-researched defaults apply.
        try:
            from calibration import get_line_deviation_intel
            _dev_proj = prediction.get("projectedValue", req.line)
            if _dev_proj and req.line > 0 and rec in ("over", "under"):
                _dev_intel = await aio.wait_for(
                    get_line_deviation_intel(
                        line=req.line,
                        projected_value=_dev_proj,
                        recommendation=rec,
                        prop_type=req.propType,
                    ),
                    timeout=1.0,
                )
                _dev_band       = _dev_intel.get("band", "aligned")
                _dev_pct        = _dev_intel.get("deviationPct", 0)
                _dev_against    = _dev_intel.get("againstBook", False)
                _dev_hit_rate   = _dev_intel.get("hitRate", 55)
                _dev_delta      = _dev_intel.get("confidenceDelta", 0)
                _dev_note       = _dev_intel.get("note", "")
                _dev_n          = _dev_intel.get("hitRateN", 0)
                _dev_src        = _dev_intel.get("hitRateSource", "default")

                # Always expose band + deviation for frontend display (regardless of conf adjustment)
                prediction["lineDeviationBand"]    = _dev_band
                prediction["lineDeviationPct"]     = _dev_pct
                prediction["lineDeviationHitRate"] = _dev_hit_rate
                prediction["lineDeviationHitRateN"] = _dev_n

                # Apply confidence adjustment for non-aligned, against-book bands
                if _dev_against and _dev_band not in ("aligned",) and abs(_dev_delta) >= 2:
                    _is_def_dev = player_position in {"Defender"}
                    # Extra damping for defenders on pass props (extra possession-sensitive)
                    _dev_extra = 0
                    if _is_def_dev and req.propType in {"pass_attempts", "passes"} and _dev_band in ("elevated", "extreme"):
                        _dev_extra = -5  # additional caution for defenders
                    _pre_dev = prediction.get("confidenceScore", 50)
                    _adj_dev = max(45, _pre_dev + _dev_delta + _dev_extra)
                    prediction["confidenceScore"] = _adj_dev

                    _src_note = f"{_dev_n} settled picks" if _dev_src == "learned" else f"default/{_dev_n} picks"
                    _def_note = " Defender pass extra-sensitive to possession." if _is_def_dev and req.propType in {"pass_attempts", "passes"} else ""
                    _alert = (
                        f"LINE DEVIATION [{_dev_band.upper()}]: Line {req.line} is {_dev_pct}% "
                        f"{'above' if _dev_intel.get('direction') == 'above' else 'below'} model projection {_dev_proj} — "
                        f"historical {rec.upper()} hit rate in this band: {_dev_hit_rate}% ({_src_note}).{_def_note}"
                    )
                    prediction["tacticalAlerts"] = prediction.get("tacticalAlerts", []) + [_alert]
                    prediction["lineDeviationBand"] = _dev_band
                    prediction["lineDeviationPct"]  = _dev_pct
                    prediction["lineDeviationHitRate"] = _dev_hit_rate
                    prediction["lineDeviationHitRateN"] = _dev_n

                    if abs(_adj_dev - _pre_dev) >= 1:
                        print(f"[DEV GUARD] {req.playerName} {rec.upper()} {req.propType}: "
                              f"band={_dev_band} dev={_dev_pct}% hit_rate={_dev_hit_rate}% ({_src_note}) "
                              f"delta={_dev_delta} → conf {_pre_dev}→{_adj_dev}")
                elif _dev_band == "aligned":
                    # Line is near our projection — apply historical hit rate nudge
                    _pre_dev = prediction.get("confidenceScore", 50)
                    if _dev_delta > 0:
                        # Book agrees with direction — slight boost
                        _boost = min(5, _dev_delta)
                        prediction["confidenceScore"] = min(85, _pre_dev + _boost)
                        prediction["lineDeviationBand"] = "aligned"
                        if _boost > 0:
                            print(f"[DEV GUARD] {req.playerName}: aligned band +{_boost}% ({_pre_dev}→{prediction['confidenceScore']})")
                    elif _dev_delta <= -5:
                        # Historical hit rate below 50% — warn and penalize
                        _penalty = min(10, abs(_dev_delta))
                        _adj = max(48, _pre_dev - _penalty)
                        prediction["confidenceScore"] = _adj
                        prediction["lineDeviationBand"] = "aligned_warn"
                        _alert_w = (
                            f"LINE DEVIATION [ALIGNED CAUTION]: Historically this {rec.upper()} "
                            f"direction hits only {_dev_hit_rate}% ({_dev_n} settled picks) "
                            f"when line is near model projection."
                        )
                        prediction["tacticalAlerts"] = prediction.get("tacticalAlerts", []) + [_alert_w]
                        print(f"[DEV GUARD] {req.playerName}: aligned CAUTION {rec.upper()} "
                              f"hit_rate={_dev_hit_rate}% → -{_penalty}% ({_pre_dev}→{_adj})")

        except Exception as _dev_e:
            print(f"[DEV GUARD] Error: {_dev_e}")

        # ── Market Edge Calibration ───────────────────────────────────────────
        # edgeZ = (|posteriorMean - line|) / effective_std.
        # It measures how many standard deviations our projection sits away from
        # the prop line — a true measure of edge sharpness vs the market price.
        #
        # A fair prop line implies ~50% probability either side. Any deviation
        # from 50% must be justified by the magnitude of our edge relative to
        # our own uncertainty.  We apply a final calibration nudge:
        #   edgeZ ≥ 2.0 → very sharp → +7% confidence
        #   edgeZ ≥ 1.5 → sharp      → +4% confidence
        #   edgeZ ≥ 1.0 → moderate   → +2% confidence
        #   edgeZ < 0.5 → weak       → -4% confidence (marginal edge)
        #   edgeZ < 0.3 → razor thin → -7% confidence (near-random)
        # Cap: confidence stays in [45, 85] regardless.
        if real_bayes:
            _ez = real_bayes.get("edgeZ", 0)
            if _ez >= 2.0:
                _edge_nudge = 7
            elif _ez >= 1.5:
                _edge_nudge = 4
            elif _ez >= 1.0:
                _edge_nudge = 2
            elif _ez >= 0.5:
                _edge_nudge = 0
            elif _ez >= 0.3:
                _edge_nudge = -4
            else:
                _edge_nudge = -7
            if _edge_nudge != 0:
                _pre_edge_conf = prediction.get("confidenceScore", 50)
                prediction["confidenceScore"] = max(45, min(85, _pre_edge_conf + _edge_nudge))
                if prediction["confidenceScore"] != _pre_edge_conf:
                    print(f"[EDGE CAL] edgeZ={_ez:.2f} nudge={_edge_nudge:+d}% "
                          f"({_pre_edge_conf} → {prediction['confidenceScore']})")
            prediction["edgeZ"] = round(_ez, 2)

        # ── UNDERDOG GK SCORE-EFFECT RISK ────────────────────────────────────
        # When a GK belongs to a HEAVY underdog team, losing badly forces constant
        # ball recycling through the GK: defenders back-pass under pressure, team
        # chases the game → GK volume EXPLODES above model estimates.
        # Only fires for true heavy underdogs (< 25% implied win probability,
        # i.e. decimal odds ≥ 4.0). The 25-35% "clear underdog" tier was removed
        # because it produced false positives (e.g. Borgognono actual=17 vs boost→OVER).
        # ─────────────────────────────────────────────────────────────────────
        if _is_gk_dom and req.propType in {"pass_attempts", "passes"} and match_odds:
            _bo = (match_odds or {}).get("bookmakerOdds", {})
            _home_dec = _bo.get("homeWin") or _bo.get("home")
            _away_dec = _bo.get("awayWin") or _bo.get("away")
            _gk_venue = (player_venue or req.venue or "home").lower()
            _team_dec = _home_dec if _gk_venue == "home" else _away_dec
            if _team_dec:
                try:
                    _team_dec_f = float(_team_dec)
                    _implied_prob = 1.0 / _team_dec_f if _team_dec_f > 0 else None
                    if _implied_prob is not None:
                        _current_proj = prediction.get("projectedValue", req.line)
                        _rec_now = prediction.get("recommendation", "under")
                        if _implied_prob < 0.25:
                            # Heavy underdog (≥ 4.0 decimal odds) — GK blow-up risk HIGH
                            _gk_boost = 1.20
                            _conf_cap = 50
                            _risk_label = "HEAVY UNDERDOG"
                        else:
                            _gk_boost = None
                            _conf_cap = None
                            _risk_label = None
                        if _gk_boost:
                            _boosted_proj = round(_current_proj * _gk_boost, 1)
                            prediction["projectedValue"] = _boosted_proj
                            prediction["recommendation"] = "over" if _boosted_proj > req.line else "under"
                            if _rec_now == "under" and prediction.get("confidenceScore", 50) > _conf_cap:
                                prediction["confidenceScore"] = _conf_cap
                            prediction["tacticalAlerts"] = prediction.get("tacticalAlerts", []) + [
                                f"GK SCORE-EFFECT RISK: Team is a {_risk_label} (implied {_implied_prob:.0%} win prob) — GK volume tends to spike in heavy losses via back-pass recycling"
                            ]
                            print(f"[UNDERDOG GK] {_risk_label}: implied_prob={_implied_prob:.2f}, "
                                  f"boost={_gk_boost}× {_current_proj} → {_boosted_proj} "
                                  f"(line={req.line}, conf cap={_conf_cap}%)")
                except (ValueError, TypeError, ZeroDivisionError):
                    pass
        # ─────────────────────────────────────────────────────────────────────

        # Recalculate confidence level after guards
        cs = prediction.get("confidenceScore", 50)
        prediction["confidenceLevel"] = "Very High" if cs >= 80 else "High" if cs >= 70 else "Medium" if cs >= 55 else "Low"

        # HARD GUARD: recommendation MUST match the FINAL projected value vs line
        final_proj_cal = prediction.get("projectedValue", req.line)
        prediction["recommendation"] = "over" if final_proj_cal > req.line else "under"

        # Use the single corrected team name resolved early (trusts req.teamName from scan)
        player_team_display = corrected_team_name
        _exact_position_values = {
            "GK", "CB", "LB", "RB", "LWB", "RWB", "CDM", "CM", "CAM",
            "LM", "RM", "LW", "RW", "CF", "ST", "SS",
        }
        _position_display_value = display_position or player_position or ""
        _position_is_exact = _position_display_value.upper() in _exact_position_values
        _observed_source = str((_observed_role or {}).get("source") or "").strip()
        _position_evidence_source = (
            _position_resolution_source
            if _position_resolution_source in {
                "manual_override",
                "gemini_web_grounded",
                "api_sports_lineup_history",
                "cache",
            }
            else _observed_source or _position_resolution_source
        )
        _position_evidence_rows = (
            [
                "Transfermarkt lists Right Winger as the main position",
                "Austin FC describes Facundo Torres as a winger",
            ]
            if _position_evidence_source == "manual_override"
            else (
                _observed_role.get("evidence", [])
                if _observed_role
                else ["provider category only; exact lineup/profile position was not available"]
            )
        )
        _position_evidence = {
            "genericPosition": player_position or None,
            "specificPosition": _position_display_value if _position_is_exact else None,
            "displayPosition": _position_display_value or None,
            "role": display_role or player_role or None,
            "source": _position_evidence_source or "unavailable",
            "status": (
                "verified_exact"
                if _position_is_exact
                else "verified_broad"
                if player_position
                else "unavailable"
            ),
            "confidence": (
                _observed_role.get("confidence")
                if _observed_role and _observed_role.get("confidence")
                else "medium" if _position_is_exact else "low"
            ),
            "evidence": _position_evidence_rows,
            "decisionRule": "exact fixture/history/profile position outranks broad provider category; calibration cannot relabel identity",
        }
        _league_label = str(
            (prediction.get("matchContext") or {}).get("league")
            or prediction.get("leagueName")
            or f"League {req.leagueId}"
        ).strip()
        _role_bucket_label = (
            _position_display_value
            if _position_is_exact
            else player_position or "UNSPECIFIED"
        )
        _league_role_bucket = f"{_league_label} · {_role_bucket_label}"
        _player_profile = (
            player_stats.get("player")
            if isinstance(player_stats, dict)
            and isinstance(player_stats.get("player"), dict)
            else {}
        )
        prediction["player"] = {
            "id": req.playerId,
            "name": req.playerName,
            "team": player_team_display,
            "birth": _player_profile.get("birth"),
            "age": (
                _age_from_birth_date(
                    (_player_profile.get("birth") or {}).get("date")
                    if isinstance(_player_profile.get("birth"), dict)
                    else None
                )
                or _player_profile.get("age")
            ),
            "position": _position_display_value or "Unknown",
            "role": display_role or "",
            "positionSource": _position_resolution_source,
            "roleSource": _observed_role.get("source") if _observed_role else None,
            "roleConfidence": _observed_role.get("confidence") if _observed_role else None,
            "roleEvidence": _observed_role.get("evidence", []) if _observed_role else [],
            "roleEvidencePacket": role_evidence_packet,
            "roleIsInferred": bool(
                display_role
                and _observed_role
                and str(_observed_role.get("source", "")).endswith("_inferred")
            ),
        }
        prediction["positionEvidence"] = _position_evidence
        prediction["leagueRoleBucket"] = _league_role_bucket
        prediction["opponent"] = req.opponentName
        prediction["propType"] = req.propType
        prediction["line"] = req.line
        prediction["roleEvidence"] = role_evidence_packet
        # Optional market reference only. This never feeds projection,
        # recommendation, confidence, calibration, or settlement.
        if sgo_market_context:
            prediction["sportsGameOddsContext"] = sgo_market_context
        # Tag WC predictions so the mobile UI / settlement loop can handle them correctly
        if _is_wc:
            prediction["wcMode"] = True
        prediction.setdefault("projectedValue", req.line)
        prediction.setdefault("recommendation", "over")
        prediction.setdefault("confidenceScore", 50)
        prediction.setdefault("confidenceLevel", "Medium")
        prediction.setdefault("confidenceInterval", None)
        prediction.setdefault("recentSamples", [])
        if real_recent_samples:
            prediction["recentSamples"] = real_recent_samples
        prediction.setdefault("bayesianMetrics", {"priorMean": req.line, "momentumEffect": 0, "covariateAdjustment": 0, "reversalFlag": "stable"})

        _COUNT_STATS = {
            "pass_attempts", "passes", "shots", "shots_on_target", "tackles",
            "key_passes", "shots_assisted", "saves", "interceptions", "blocks",
            "dribbles", "dribbles_success", "fouls_drawn", "fouls_committed",
            "crosses", "clearances", "duels_won", "yellow_cards", "goals", "assists",
        }
        if req.propType in _COUNT_STATS:
            pv = prediction.get("projectedValue")
            if pv is not None:
                rounded_pv = round(pv)
                prediction["projectedValue"] = rounded_pv
                # Re-sync recommendation after rounding — round() can change the
                # integer value relative to the line (e.g. pv=1.5 line=1.5 rounds
                # to 2 via banker's rounding, but guard set "under" since 1.5 ≯ 1.5).
                prediction["recommendation"] = "over" if rounded_pv > req.line else "under"
            ci = prediction.get("confidenceInterval")
            if ci and len(ci) >= 2:
                lo = round(float(ci[0]), 1)
                hi = round(float(ci[1]), 1)
                prediction["confidenceInterval"] = [lo, hi] if hi > lo else None
            for s in prediction.get("recentSamples", []):
                if not isinstance(s, dict):
                    continue
                v = s.get("value")
                if v is not None:
                    s["value"] = int(round(v))

        # ── BAYESIAN IS FINAL ────────────────────────────────────────────────
        # The Bayesian math projection is the sole source of truth for both the
        # projectedValue and the OVER/UNDER recommendation. Structured
        # explanation text is display context only and never moves the number.
        # Rationale: the 85/15 blend was causing the final projected value to cross
        # the line when the AI disagreed, silently flipping the recommendation
        # against the math. The user's money follows the math — the math decides.
        _bayes_final = prediction.get("projectedValue", req.line)
        prediction["bayesianComponent"] = _bayes_final

        # ═══════════════════════════════════════════════════════════════════
        # NARROW EDGE — GK PASS_ATTEMPTS ONLY
        # Fades the model's lean when the projection is close to the line.
        # SCOPE: ONLY fires for goalkeepers on pass_attempts props.
        # The fade pattern (tight lean lands opposite direction) was empirically
        # validated exclusively on GK pass picks. It must NEVER fire on outfield
        # players — for them a tight edge just means a close call, not a fade signal.
        #
        # HOME GK OVER threshold is widened to 12%:
        # Historical data shows home GK OVER recs on pass_attempts hit only
        # 37.5% (3/8 picks). Home teams hold more possession → fewer back-passes
        # to GK → actual runs UNDER the line. All other GKs: 8% threshold.
        #
        # SEASON AVG ANCHOR GUARD: even within the GK scope, never flip when
        # the season average independently confirms the lean by >5% beyond the line.
        # ═══════════════════════════════════════════════════════════════════
        _pass_proj = prediction.get("projectedValue", req.line)
        # Both position systems must agree on GK — _bayes_position is the early
        # DB-lookup estimate (can misfire for outfield players who have goals_saves=0
        # in logs); specific_position is the authoritative POS RESOLVE result.
        # Requiring both to agree prevents the narrow-edge flip from ever touching
        # outfield players like Victor Braga who are RBs, not GKs.
        _is_gk_pass = (
            req.propType == "pass_attempts"
            and _bayes_position.upper() in {"GK", "GOALKEEPER"}
            and specific_position.upper() in {"GK", "GOALKEEPER", "G"}
        )
        if _is_gk_pass and req.line > 0 and _pass_proj is not None:
            _edge_pct = abs(_pass_proj - req.line) / req.line * 100

            _is_home_gk_over = (
                req.venue == "home"
                and _pass_proj > req.line
            )
            _narrow_threshold = 12.0 if _is_home_gk_over else 8.0

            # Season avg anchor — block flip if season avg clearly confirms lean
            _season_avg = early_bayes.get("priorMean") if early_bayes else None
            _avg_anchor_blocks_flip = False
            _model_lean_over = _pass_proj > req.line
            if _season_avg and req.line > 0:
                _avg_edge_pct = (_season_avg - req.line) / req.line * 100
                if _model_lean_over and _avg_edge_pct > 5.0:
                    _avg_anchor_blocks_flip = True
                    print(f"[GK NARROW EDGE BLOCKED] {req.playerName}: season_avg={_season_avg} "
                          f"{_avg_edge_pct:.1f}% above line — anchor confirms OVER, no flip")
                elif not _model_lean_over and _avg_edge_pct < -5.0:
                    _avg_anchor_blocks_flip = True
                    print(f"[GK NARROW EDGE BLOCKED] {req.playerName}: season_avg={_season_avg} "
                          f"{abs(_avg_edge_pct):.1f}% below line — anchor confirms UNDER, no flip")

            # Possession context anchor: if the model is leaning UNDER because the team
            # has above-average expected possession (GK DOM POSS PENALTY fired), block the
            # narrow edge from flipping UNDER → OVER even when the season avg is over the line.
            # Example: Escandell (Oviedo HOME, 55.7% poss / 52.4% avg = ratio 1.063) → UNDER.
            # The season avg (35.3) is above the 33.5 line, but the possession context is real.
            if not _model_lean_over and match_dominance:
                _ne_exp_poss  = match_dominance.get("expectedPoss")
                _ne_team_avg  = match_dominance.get("teamSeasonAvg")
                if _ne_exp_poss and _ne_team_avg and _ne_team_avg > 0:
                    _ne_poss_ratio = _ne_exp_poss / _ne_team_avg
                    if _ne_poss_ratio > 1.05:
                        _avg_anchor_blocks_flip = True
                        print(f"[GK POSS ANCHOR] {req.playerName}: possession context "
                              f"({_ne_exp_poss:.1f}% > avg {_ne_team_avg:.1f}%, ratio={_ne_poss_ratio:.2f}) "
                              f"confirms UNDER lean — blocking flip to OVER")

            if _edge_pct < _narrow_threshold and not _avg_anchor_blocks_flip:
                _leaning = "over" if _pass_proj > req.line else "under"
                _flipped = "UNDER" if _leaning == "over" else "OVER"
                prediction["recommendation"] = _flipped
                prediction["passLeaning"] = _leaning.upper()
                _reason_tag = "[HOME GK OVER FADE]" if _is_home_gk_over else "[GK NARROW EDGE]"
                prediction["passReason"] = (
                    f"Edge only {_edge_pct:.1f}% — fading model's {_leaning.upper()} lean → {_flipped}"
                )
                print(
                    f"{_reason_tag} {req.playerName} {req.propType}: "
                    f"proj={_pass_proj}, line={req.line}, gap={_edge_pct:.1f}% → fading to {_flipped}"
                )

        # ── PROJECTION CONSISTENCY GUARD ─────────────────────────────────────────────────
        # Ensure projectedValue and recommendation can never contradict each other.
        # Any gate (GK narrow edge, home-GK fade, etc.) may flip the recommendation
        # without touching projectedValue — this guard realigns the number so the UI
        # never shows "Projection: 30, Line: 29.5 — UNDER" or vice-versa.
        #
        # FIX: use the actual Bayesian posterior mean (real_bayes["posteriorMean"])
        # instead of the hardcoded "line ± 0.5" anchor.  The old anchor caused the
        # displayed projection to track the sportsbook line perfectly (proj = line - 0.5
        # regardless of player stats) whenever a gate flipped the recommendation.
        # The posterior mean is computed independently from game logs and only lightly
        # fused with the line (20%), so it reflects the player's actual statistical level.
        _cg_rec  = str(prediction.get("recommendation", "")).lower()
        _cg_proj = prediction.get("projectedValue")
        _cg_bayes_mean = (real_bayes or {}).get("posteriorMean")
        if _cg_proj is not None and req.line and req.line > 0:
            if _cg_rec == "under" and _cg_proj > req.line:
                # Prefer the real posterior mean; fall back to line-0.5 only if unavailable
                _cg_fixed = round(_cg_bayes_mean, 1) if _cg_bayes_mean is not None else round((req.line - 0.5) * 2) / 2
                prediction["projectedValue"] = _cg_fixed
                print(f"[CONSISTENCY GUARD] {req.playerName}: projectedValue {_cg_proj} → {_cg_fixed} "
                      f"(rec=UNDER, was above line {req.line}; using posterior={'real' if _cg_bayes_mean is not None else 'line-anchor'})")
            elif _cg_rec == "over" and _cg_proj < req.line:
                _cg_fixed = round(_cg_bayes_mean, 1) if _cg_bayes_mean is not None else round((req.line + 0.5) * 2) / 2
                prediction["projectedValue"] = _cg_fixed
                print(f"[CONSISTENCY GUARD] {req.playerName}: projectedValue {_cg_proj} → {_cg_fixed} "
                      f"(rec=OVER, was below line {req.line}; using posterior={'real' if _cg_bayes_mean is not None else 'line-anchor'})")

        # ── BAYESIAN TRUTH OVERRIDE ──────────────────────────────────────────
        # By user directive: the Bayesian Monte-Carlo probability is the
        # source of truth for both direction AND displayed confidence.
        # Eight upstream branches set `recommendation` from `projection > line`,
        # which ignores the posterior distribution's variance/skew. Result: the
        # badge can say OVER while P(UNDER) > 50% (real example: Tielemans
        # 51.0 vs 50.5 line, P(UNDER)=59.4%, badge said OVER, actual landed 40).
        #
        # This block runs AFTER all upstream adjustments and BEFORE the MATH
        # LOCK + calibration so that downstream consumers (lock text, calibrator,
        # mobile UI) all see the corrected values.
        #
        # Safe-defaults for knockout variables: must be at this outer 8-space
        # scope so ALL code paths (including the async/no-logs path that skips
        # the inner `if real_bayes:` block above) reach the KNOCKOUT UNDER
        # CONFIDENCE PENALTY check below with these variables defined.
        # The inner `if real_bayes:` block may later override _final_is_knockout
        # to the correct game_situation value; these are just safe fallbacks.
        if "_final_is_knockout" not in locals():
            _final_is_knockout = False
        if "_KO_COUNT_PROPS" not in locals():
            _KO_COUNT_PROPS = {
                "pass_attempts", "passes", "shots", "shots_on_target",
                "saves", "key_passes", "crosses", "dribbles", "tackles", "clearances",
            }
        _bt_src = real_bayes if isinstance(real_bayes, dict) else (early_bayes if isinstance(early_bayes, dict) else None)
        if prediction.get("recommendation", "").upper() != "PASS" and _bt_src is not None and "pOver" in _bt_src and "pUnder" in _bt_src:
            _bt_p_over  = _bt_src["pOver"]
            _bt_p_under = _bt_src["pUnder"]
            _bt_max_pct = max(_bt_p_over, _bt_p_under)
            _bt_dir     = "over" if _bt_p_over >= _bt_p_under else "under"
            _bt_old_rec  = str(prediction.get("recommendation", "")).lower()
            _bt_old_conf = prediction.get("confidenceScore")
            _bt_new_conf = int(round(_bt_max_pct))
            _bt_new_lvl  = (
                "Very High" if _bt_max_pct >= 80
                else "High"   if _bt_max_pct >= 70
                else "Medium" if _bt_max_pct >= 55
                else "Low"
            )

            prediction["recommendation"] = _bt_dir
            prediction["confidenceScore"] = _bt_new_conf
            prediction["rawConfidence"] = _bt_new_conf
            prediction["confidenceLevel"] = _bt_new_lvl

            # Clear stale coinFlip flags from upstream guards (e.g. Guard 3a).
            # Guard 3a fires before BAYESIAN TRUTH and can set coinFlip=True on
            # any high-conf OVER. If Bayesian genuinely confirms ≥70% probability,
            # the pick is not a coin flip — clear the flag so the UI doesn't
            # show a contradictory warning.
            if _bt_max_pct >= 70.0 and prediction.get("coinFlip"):
                prediction["coinFlip"] = False
                print(
                    f"[BAYESIAN TRUTH] Cleared coinFlip — P={_bt_max_pct:.0f}% "
                    f"confirms genuine {_bt_dir.upper()} signal, not a coin flip"
                )

            # If direction flipped, align projectedValue with the new direction.
            # Use the actual Bayesian posterior mean — NOT "line ± 0.5" — so the
            # projection reflects the player's real statistical level independent
            # of what the sportsbook set the line to.
            if _bt_old_rec != _bt_dir:
                _bt_proj = prediction.get("projectedValue", req.line)
                _bt_posterior = (real_bayes or {}).get("posteriorMean")
                if _bt_dir == "under" and _bt_proj > req.line:
                    _bt_fixed = round(_bt_posterior, 1) if _bt_posterior is not None else round((req.line - 0.5) * 2) / 2
                    prediction["projectedValue"] = _bt_fixed
                    print(f"[BAYESIAN TRUTH] projectedValue flip UNDER: {_bt_proj} → {_bt_fixed} "
                          f"({'posterior' if _bt_posterior is not None else 'line-anchor'})")
                elif _bt_dir == "over" and _bt_proj < req.line:
                    _bt_fixed = round(_bt_posterior, 1) if _bt_posterior is not None else round((req.line + 0.5) * 2) / 2
                    prediction["projectedValue"] = _bt_fixed
                    print(f"[BAYESIAN TRUTH] projectedValue flip OVER: {_bt_proj} → {_bt_fixed} "
                          f"({'posterior' if _bt_posterior is not None else 'line-anchor'})")

            # ── KNOCKOUT UNDER CONFIDENCE PENALTY ────────────────────────────
            # Even after the ET projection uplift, UNDER bets in knockout games
            # carry residual extra-time risk that the normal distribution doesn't
            # fully capture (the distribution is symmetric; ET is asymmetric —
            # it only ADDS minutes, never subtracts).  Settled data: WC knockout
            # UNDER 50% hit rate.  Apply a -8pt confidence cap for UNDER bets
            # on count stats in knockout games, floor 52 so we never suppress to
            # noise levels when the edge is genuinely strong.
            if _final_is_knockout and req.propType in _KO_COUNT_PROPS and _bt_dir == "under":
                _ko_under_pre = prediction["confidenceScore"]
                _ko_under_cap = max(52, _ko_under_pre - 8)
                if _ko_under_cap < _ko_under_pre:
                    prediction["confidenceScore"] = _ko_under_cap
                    prediction["rawConfidence"]   = _ko_under_cap
                    if _ko_under_cap < 70:
                        prediction["confidenceLevel"] = "High" if _ko_under_cap >= 65 else "Medium" if _ko_under_cap >= 55 else "Low"
                    print(
                        f"[KNOCKOUT UNDER PENALTY] {req.playerName}/{req.propType}: "
                        f"conf {_ko_under_pre}% → {_ko_under_cap}% (ET risk on UNDER bets)"
                    )
            # ─────────────────────────────────────────────────────────────────

            print(
                f"[BAYESIAN TRUTH] {req.playerName}/{req.propType}: "
                f"P(OVER)={_bt_p_over}% P(UNDER)={_bt_p_under}% → "
                f"{_bt_dir.upper()} {_bt_new_conf}% ({_bt_new_lvl})"
                + (f" [FLIPPED from {_bt_old_rec.upper()} {_bt_old_conf}%]" if _bt_old_rec != _bt_dir else f" [confidence {_bt_old_conf}→{_bt_new_conf}]")
            )

            # ── SHARP SUMMARY DIRECTION GUARD ─────────────────────────────────
            # The prediction cache stores deterministic narrative. When BAYESIAN TRUTH pins
            # a different direction than what the AI wrote (common when the AI
            # explains OVER but Bayesian says UNDER), the sharpSummary displayed
            # to users flatly contradicts the recommendation badge.
            # Detect the conflict and replace sharpSummary with a math-based one.
            # Also purge the prediction cache so the next request regenerates
            # fresh AI text with the correct direction anchor.
            _ss_text = prediction.get("sharpSummary", "") or ""
            if _ss_text:
                _ss_lo = _ss_text.lower()
                _over_markers = ("exceed", " over ", "above the line", "more than",
                                 "surpass", "push past", "eclips", "over 46", "over 47",
                                 "over 48", "over 49", "over 50", "strong over",
                                 "projects to exceed", "will exceed")
                _under_markers = (" under ", "going under", "is under ", "stays under",
                                  "come under ", "fall under ", "land under",
                                  "below", "fewer than", "less than",
                                  "suppress", "fall short", "won't reach", "won't hit",
                                  "short of the", "not reach", "miss the line")
                _ss_has_over  = any(m in _ss_lo for m in _over_markers)
                _ss_has_under = any(m in _ss_lo for m in _under_markers)
                _ss_conflicts = (
                    (_bt_dir == "under" and _ss_has_over and not _ss_has_under) or
                    (_bt_dir == "over"  and _ss_has_under and not _ss_has_over)
                )
                if _ss_conflicts:
                    _dir_word = "UNDER" if _bt_dir == "under" else "OVER"
                    _alt_dir  = "OVER"  if _bt_dir == "under" else "UNDER"
                    _bt_proj  = prediction.get("projectedValue", req.line)
                    _p_dir    = _bt_p_under if _bt_dir == "under" else _bt_p_over
                    _proj_disp = f"{_bt_proj:.1f}" if isinstance(_bt_proj, (int, float)) else str(_bt_proj)
                    _replacement_summary = (
                        f"The Reverse Formula projects {req.playerName} to finish at "
                        f"{_proj_disp} — {_dir_word} {req.line}. The 3-layer statistical "
                        f"model gives {_p_dir:.0f}% probability the {_dir_word} lands; "
                        f"structural matchup and possession factors suppress the stat "
                        f"below the line despite the {_alt_dir.lower()} narrative in "
                        f"market commentary."
                        if _bt_dir == "under" else
                        f"The Reverse Formula projects {req.playerName} to finish at "
                        f"{_proj_disp} — {_dir_word} {req.line}. The 3-layer statistical "
                        f"model gives {_p_dir:.0f}% probability the {_dir_word} lands; "
                        f"volume and possession factors push the stat above the line "
                        f"despite the cautious market pricing."
                    )
                    prediction["sharpSummary"] = _replacement_summary
                    # Do not discard a substantive structured explanation when the
                    # final Bayesian pass changes direction. The deterministic model is called
                    # before the full posterior is available, so this can happen
                    # even though the explanation contains valuable matchup,
                    # role, manager, and game-flow evidence. Replace only the
                    # direction-bearing sections and add an authoritative
                    # reconciliation note; retain the evidence and its source.
                    _existing_td = prediction.get("tacticalBreakdown", "") or ""
                    if isinstance(_existing_td, str) and len(_existing_td.strip()) > 100:
                        _final_note = (
                            f"**Final Model Reconciliation**\n"
                            f"The completed Reverse Formula posterior is authoritative: "
                            f"{_proj_disp} {_dir_word} the {req.line} line with "
                            f"{_p_dir:.0f}% probability. The tactical evidence below is "
                            f"retained as matchup context; the final Bayesian direction "
                            f"overrides any earlier {_alt_dir} lean.\n\n"
                        )
                        # Replace a generated Verdict section when present so the
                        # first section can never contradict the recommendation.
                        _existing_td = re.sub(
                            r"\*\*Verdict\*\*.*?(?=\n\s*\*\*[A-Za-z][^*]*\*\*|\Z)",
                            (
                                f"**Verdict**\n"
                                f"The completed Reverse Formula projects {_proj_disp} "
                                f"— {_dir_word} {req.line} ({_p_dir:.0f}% probability)."
                            ),
                            _existing_td,
                            count=1,
                            flags=re.IGNORECASE | re.DOTALL,
                        )
                        # Likewise replace a generated TL;DR so the visible close
                        # of the analysis agrees with the final badge.
                        _existing_td = re.sub(
                            r"\*\*TL;DR\*\*.*?(?=\n\s*\*\*[A-Za-z][^*]*\*\*|\Z)",
                            (
                                f"**TL;DR**\n"
                                f"{_dir_word} at {_proj_disp} is the final model call "
                                f"against the {req.line} line ({_p_dir:.0f}% probability)."
                            ),
                            _existing_td,
                            count=1,
                            flags=re.IGNORECASE | re.DOTALL,
                        )
                        prediction["tacticalBreakdown"] = _final_note + _existing_td.strip()
                        prediction["aiSource"] = "model"
                        print(
                            f"[DIRECTION GUARD] {req.playerName}/{req.propType}: "
                            f"final rec={_bt_dir.upper()} — reconciled deterministic narrative "
                            f"without discarding tactical evidence"
                        )
                    else:
                        # No substantive AI text exists, so the normal math
                        # fallback below remains the correct source marker.
                        prediction["aiSource"] = "model"
                    # Keep the daily AI cache. The final direction is computed
                    # fresh on every request, and this same reconciliation is
                    # applied to cached prose when necessary.

            # ── LOW CONVICTION FILTER ─────────────────────────────────────────
            # When Bayesian max(P(OVER), P(UNDER)) < 57%, the model has weak
            # signal — the line is close to the projection mean and the
            # distribution straddles both sides. Cap confidence at 58% and
            # expose lowConviction=True so the UI can surface a warning.
            # Fires inside the _bt_src guard so it only runs when Bayesian
            # data is available.
            # Note: threshold raised from 57% (was 60%) so only genuinely weak
            # signals are penalised — WC/tournament props with limited history
            # were hitting this too aggressively at 60%.
            _bt_conv = max(_bt_p_over, _bt_p_under)
            if _bt_conv < 57.0 and prediction.get("recommendation", "").upper() != "PASS":
                prediction["lowConviction"] = True
                if (prediction.get("confidenceScore") or 0) > 58:
                    prediction["confidenceScore"] = 58
                    prediction["confidenceLevel"] = "Medium"
                print(f"[LOW CONV] {req.playerName}/{req.propType}: P(max)={_bt_conv:.1f}% < 57% → capped 58% Medium")
            else:
                prediction.setdefault("lowConviction", False)

            # ── SMALL SAMPLE CONFIDENCE DECAY ─────────────────────────────────
            # With n<10 game logs the Bayesian prior is unreliable — small samples
            # produce artificially tight distributions. Decay confidence toward a
            # safe floor: n<6 → cap 57%, n<10 → cap 63%, n<15 → cap 68%.
            # Runs inside the _bt_src guard so it only fires with real Bayesian data.
            _bt_n = (_bt_src or {}).get("sampleSize", 20)
            if _bt_n is not None:
                _ss_cap = 57 if _bt_n < 6 else (63 if _bt_n < 10 else (68 if _bt_n < 15 else 100))
                if _ss_cap < 100 and (prediction.get("confidenceScore") or 0) > _ss_cap:
                    prediction["confidenceScore"] = _ss_cap
                    prediction["confidenceLevel"] = "High" if _ss_cap >= 70 else "Medium" if _ss_cap >= 55 else "Low"
                    print(f"[SMALL SAMPLE] {req.playerName}/{req.propType}: n={_bt_n} → cap {_ss_cap}%")

        # ── HARD BLOCK: clearances OVER (0% hit rate, runs AFTER Bayesian Truth) ──
        # Bayesian Truth may still output OVER because the prior over-projects
        # clearances for forwards/midfielders who rarely block crosses.
        # 0W/11L empirical record → hard-flip to UNDER and set 60% Medium.
        if req.propType == "clearances" and prediction.get("recommendation", "").lower() == "over":
            prediction["recommendation"]  = "under"
            prediction["confidenceScore"] = 60
            prediction["confidenceLevel"] = "Medium"
            prediction["coinFlip"]        = False
            prediction["tacticalAlerts"]  = prediction.get("tacticalAlerts", []) + [
                "CLEARANCES OVER → UNDER (data override): 0% hit rate on 11 settled clearances OVER picks. "
                "Books set these lines precisely; clearances are volatile and hard to project. "
                "Clearances UNDER is viable."
            ]
            if prediction.get("projectedValue") is not None and prediction["projectedValue"] > req.line:
                prediction["projectedValue"] = round((req.line - 0.5) * 2) / 2
            print(f"[HARD BLOCK] clearances OVER → forced UNDER 60% for {req.playerName}")

        # ── RECENT PASS-PROP CALIBRATION CONTEXT ─────────────────────────────
        # All-time safety is useful for context, but it can hide a short-lived
        # league/role regime change.  For soccer passing props only, suppress
        # a direction when the most-specific rolling bucket has at least ten
        # deduplicated settled events and is at or below a 50% hit rate.
        # This can cap confidence, but never creates a third customer-facing
        # direction. The final projection ledger owns OVER/UNDER.
        if (
            str(req.sport or "").lower() == "soccer"
            and req.propType in {"pass_attempts", "passes"}
            and prediction.get("recommendation", "").upper() in {"OVER", "UNDER"}
        ):
            _pass_dir = prediction["recommendation"].upper()
            _pass_position = (
                prediction.get("player", {}).get("position")
                or prediction.get("position")
                or req.positionOverride
                or ""
            )
            _recent_pass = _get_recent_prop_safety(
                req.propType,
                _pass_dir,
                league_id=req.leagueId,
                position=_pass_position,
            )
            if (
                _recent_pass
                and _recent_pass.get("hitRate") is not None
                and _recent_pass.get("hitRate") <= 50
            ):
                _pass_rate = _recent_pass["hitRate"]
                _pass_n = _recent_pass["n"]
                prediction["recentPropSafety"] = {
                    "direction": _pass_dir,
                    "hitRate": _pass_rate,
                    "sampleSize": _pass_n,
                    "windowDays": 45,
                    "minSampleSize": 10,
                    "action": "confidence_cap",
                }
                prediction["confidenceScore"] = min(
                    float(prediction.get("confidenceScore") or 50),
                    60,
                )
                prediction["rawConfidence"] = min(
                    float(prediction.get("rawConfidence") or prediction["confidenceScore"]),
                    60,
                )
                prediction["confidenceLevel"] = (
                    "Medium" if prediction["confidenceScore"] >= 55 else "Low"
                )
                prediction["tacticalAlerts"] = prediction.get("tacticalAlerts", []) + [
                    f"LOW {_pass_dir} CALIBRATION: league/role bucket is "
                    f"{_pass_rate:.0f}% across {_pass_n} settled events; "
                    "direction remains ledger-based."
                ]
                print(
                    f"[PASS PROP CALIBRATION] {req.playerName}/{req.propType}: "
                    f"{_pass_dir} {_pass_rate:.1f}% ({_pass_n}n, rolling 45d)"
                )

        # ── MARKET DISTANCE GUARD ────────────────────────────────────────────
        # When our projection is ≥35% away from the market line, the prior is
        # likely contaminated (stale seasons, old-club era, position mismatch).
        # Normally caps confidence at 55% and surfaces a caution alert.
        #
        # BAYESIAN TRUTH exception: if the Bayesian Monte-Carlo gives P ≥ 80%
        # in the winning direction, the posterior distribution already accounts
        # for data quality — its mass is solidly on one side for structural
        # reasons (e.g., a 65% possession team vs a 35% expected opponent).
        # In that case, cap confidence only to 72% (not 55%) and still show
        # the caution alert, but don't override a strong Bayesian signal.
        _mg_proj = prediction.get("projectedValue", req.line)
        _market_distance_fired = False
        if req.line > 0 and _mg_proj is not None:
            _mg_gap_pct = abs(_mg_proj - req.line) / req.line * 100
            if _mg_gap_pct >= 35:
                _market_distance_fired = True
                _mg_pre = prediction.get("confidenceScore", 50)
                # Check how strong the Bayesian posterior is
                _mg_bt_p = max(
                    (real_bayes or {}).get("pOver", 0),
                    (real_bayes or {}).get("pUnder", 0)
                )
                _mg_bt_strong = _mg_bt_p >= 80.0   # posterior is genuinely confident
                _mg_cap = 72 if _mg_bt_strong else 55
                if _mg_pre > _mg_cap:
                    prediction["confidenceScore"] = _mg_cap
                    prediction["confidenceLevel"] = (
                        "High" if _mg_cap >= 70 else "Medium"
                    )
                    prediction["tacticalAlerts"] = prediction.get("tacticalAlerts", []) + [
                        f"MARKET DISTANCE: Model projects {_mg_proj} but line is {req.line} "
                        f"({_mg_gap_pct:.0f}% gap) — prior may be from wrong club era or "
                        f"stale season. Treat with caution."
                    ]
                    _bt_note = f" (Bayesian P={_mg_bt_p:.0f}% — soft cap {_mg_cap}%)" if _mg_bt_strong else ""
                    print(f"[MARKET DIST] {req.playerName}: proj={_mg_proj} line={req.line} "
                          f"gap={_mg_gap_pct:.0f}% → confidence capped {_mg_pre}→{_mg_cap}%{_bt_note}")
                else:
                    prediction["tacticalAlerts"] = prediction.get("tacticalAlerts", []) + [
                        f"MARKET DISTANCE: Model projects {_mg_proj} but line is {req.line} "
                        f"({_mg_gap_pct:.0f}% gap) — verify this is the right era/club data."
                    ]
                    print(f"[MARKET DIST] {req.playerName}: gap={_mg_gap_pct:.0f}% — alert only "
                          f"(conf={_mg_pre}% already ≤ cap={_mg_cap}%)")
                # When the gap is extreme (≥60%), direction is structural not a coin-flip.
                if _mg_gap_pct >= 60 and prediction.get("coinFlip"):
                    prediction["coinFlip"] = False
                    print(f"[MARKET DIST] gap={_mg_gap_pct:.0f}% ≥ 60% — coinFlip cleared; "
                          f"direction is structural, not a near-line coin-flip")

        # ── POSITION-SPECIFIC CONFIDENCE CAP ─────────────────────────────────────
        # Empirical data (1291 settled picks) shows certain position+prop+direction
        # combos are systematically overconfident at 90-99%. Hard caps prevent the
        # model from displaying confidence the data does not support.
        #   GK  OVER pass_attempts: 41.4% actual hit rate → cap 72%
        #   CAM OVER pass_attempts:  0.0% actual hit rate → cap 62%
        #   CM  OVER pass_attempts: 41.7% actual hit rate → cap 68%
        #   CDM OVER pass_attempts: 33.3% actual hit rate → cap 68%
        _POS_CONF_CAPS = {
            ("GK",  "pass_attempts", "over"): 72,
            ("CAM", "pass_attempts", "over"): 62,
            ("CM",  "pass_attempts", "over"): 68,
            ("CDM", "pass_attempts", "over"): 68,
        }
        _cap_key = (
            str(prediction.get("position") or "").upper(),
            req.propType,
            str(prediction.get("recommendation") or "").lower(),
        )
        _pos_cap = _POS_CONF_CAPS.get(_cap_key)
        if _pos_cap is not None and (prediction.get("confidenceScore") or 0) > _pos_cap:
            _pre_pos_cap = prediction["confidenceScore"]
            prediction["confidenceScore"] = _pos_cap
            prediction["rawConfidence"]   = _pos_cap
            prediction["confidenceLevel"] = "High" if _pos_cap >= 70 else "Medium"
            print(
                f"[POS CAP] {prediction.get('position')} {req.propType} "
                f"{prediction.get('recommendation')}: {_pre_pos_cap}% → {_pos_cap}%"
            )

        # MATH LOCK removed — pure math analysis is built below, no AI text to patch.
        _lock_final_rec = str(prediction.get("recommendation", "")).upper()  # PASS, OVER, or UNDER
        _lock_proj_raw  = prediction.get("projectedValue", req.line)
        _lock_proj_str  = str(int(_lock_proj_raw)) if _lock_proj_raw == int(_lock_proj_raw) else f"{_lock_proj_raw:.1f}"
        # ── EDGE & SAFETY RATING (DATA-DRIVEN) ───────────────────────────────────
        # Computed AFTER BAYESIAN TRUTH + MATH LOCK — all values are final here.
        # edgeRating  : SHARP EDGE | EDGE | MARGINAL | NO EDGE
        # safetyRating: SAFE | MODERATE | RISKY | AVOID
        #
        # Safety comes from the LIVE prop_safety_cache which queries all settled
        # picks in MongoDB, computing empirical hit rates per (propType, direction).
        # Cache refreshes every 6h — always reflects the latest real data.
        # Edge is projection-margin-based, gated by the historical safety.
        _er_rec   = prediction.get("recommendation", "").upper()
        _er_prop  = req.propType or ""
        _er_conf  = prediction.get("confidenceScore", 50)
        _er_proj  = prediction.get("projectedValue", req.line)
        _er_line  = req.line or 0
        _er_coin  = prediction.get("coinFlip", False)

        try:
            _er_margin = abs(float(_er_proj) - float(_er_line)) if _er_line > 0 else 0
        except (TypeError, ValueError):
            _er_margin = 0

        # ── Safety: pull from live DB-derived cache ───────────────────────────
        # Hierarchical v2: league-aware + position-aware. Falls back to global
        # prop+direction when the child bucket is too thin.
        _er_position = prediction.get("player", {}).get("position") or prediction.get("position") or ""
        if _er_rec == "PASS":
            _safety_rating = "AVOID"
            _er_hit_rate   = None
            _er_n          = 0
        elif _er_coin:
            _safety_rating = "RISKY"
            _er_hit_rate   = None
            _er_n          = 0
        else:
            _ps = _get_prop_safety(_er_prop, _er_rec, league_id=req.leagueId, position=_er_position)
            if _ps:
                _safety_rating = _ps["safety"]
                _er_hit_rate   = _ps["hitRate"]
                _er_n          = _ps["n"]
            else:
                # No historical data for this prop+direction — treat as unknown risk
                _safety_rating = "RISKY"
                _er_hit_rate   = None
                _er_n          = 0

        # ── Edge: projection margin, gated by safety ──────────────────────────
        # SHARP EDGE requires both a meaningful margin AND a historically SAFE prop.
        # AVOID/RISKY props are capped at MARGINAL even with large projection margins.
        # MARKET DISTANCE override: when the line is structurally far from projection
        # (gap ≥ 60%), even a RISKY prop gets at least MARGINAL if margin is large —
        # the line itself is the anomaly, not the model.
        _er_market_dist = _market_distance_fired and _er_margin >= 10
        if _er_rec == "PASS" or _er_coin:
            _edge_rating = "NO EDGE"
        elif _safety_rating == "AVOID":
            # Historically proven loser — never call it an edge
            _edge_rating = "NO EDGE"
        elif _safety_rating == "SAFE":
            if _er_margin >= 5 and _er_conf >= 60:
                _edge_rating = "SHARP EDGE"
            elif _er_margin >= 3 and _er_conf >= 55:
                _edge_rating = "EDGE"
            elif _er_margin >= 2:
                _edge_rating = "MARGINAL"
            else:
                _edge_rating = "NO EDGE"
        elif _safety_rating == "MODERATE":
            if _er_margin >= 8 and _er_conf >= 65:
                _edge_rating = "SHARP EDGE"
            elif _er_margin >= 5 and _er_conf >= 58:
                _edge_rating = "EDGE"
            elif _er_margin >= 3:
                _edge_rating = "MARGINAL"
            else:
                _edge_rating = "NO EDGE"
        else:  # RISKY
            # Even with a big margin, a historically unreliable prop can't be SHARP EDGE
            if _er_margin >= 10 and _er_conf >= 70:
                _edge_rating = "MARGINAL"
            elif _er_market_dist:
                # Market distance override: the line itself is the anomaly, margin is real
                _edge_rating = "MARGINAL"
            else:
                _edge_rating = "NO EDGE"

        # Market distance structural override: when the projection gap is extreme (≥60%
        # from line) AND the model has a clear direction, floor the edge at MARGINAL
        # regardless of safety rating. The line is the outlier — not the model.
        if _er_market_dist and _edge_rating == "NO EDGE":
            _edge_rating = "MARGINAL"
            print(f"[MARKET DIST EDGE] margin={_er_margin:.1f} gap≥60% → floor to MARGINAL")

        prediction["edgeRating"]        = _edge_rating
        prediction["safetyRating"]      = _safety_rating
        prediction["propHistoricalRate"] = _er_hit_rate  # expose to frontend
        prediction["propHistoricalN"]   = _er_n
        print(
            f"[EDGE/SAFETY] {_er_rec} {_er_prop}: margin={_er_margin:.1f} conf={_er_conf} "
            f"hist={_er_hit_rate}% (n={_er_n}) → {_edge_rating} / {_safety_rating}"
        )

        # ── AVOID / RISKY CONFIDENCE SUPPRESSION ─────────────────────────────────
        # The Bayesian engine computes P(OVER)/P(UNDER) from the prior + momentum,
        # but has no knowledge of the prop+direction's historical hit rate.
        # When prop safety has enough evidence that a direction is a loser, we
        # suppress the Bayesian confidence to match the empirical reality.
        #
        # AVOID (≤44% hit rate, n≥5): cap confidence at the empirical rate (floor 50)
        # RISKY (45–57%, n≥8):        soft −5 pp reduction when confidence > 65
        #
        # This runs AFTER edgeRating is computed (which used the pre-suppression
        # confidence) so the NO EDGE label is already correct for AVOID props.
        if prediction.get("recommendation", "").upper() not in ("PASS",):
            _sup_conf = prediction.get("confidenceScore", 50)
            if _safety_rating == "AVOID" and _er_hit_rate is not None:
                _avoid_cap = max(50, round(_er_hit_rate))
                if _sup_conf > _avoid_cap:
                    prediction["confidenceScore"] = _avoid_cap
                    prediction["confidenceLevel"] = (
                        "Medium" if _avoid_cap >= 55 else "Low"
                    )
                    print(
                        f"[AVOID CAP] {_er_prop} {_er_rec}: bayesian={_sup_conf}% "
                        f"→ capped at empirical {_avoid_cap}% (n={_er_n})"
                    )
            elif _safety_rating == "RISKY" and _er_hit_rate is not None and _sup_conf > 65:
                _risky_adj = max(55, _sup_conf - 5)
                if _risky_adj != _sup_conf:
                    prediction["confidenceScore"] = _risky_adj
                    prediction["confidenceLevel"] = (
                        "High" if _risky_adj >= 70 else "Medium"
                    )
                    print(
                        f"[RISKY ADJ] {_er_prop} {_er_rec}: {_sup_conf}% → {_risky_adj}% "
                        f"(RISKY hist={_er_hit_rate:.1f}%)"
                    )

            # ── LINE-DEVIATION HARD CAP ────────────────────────────────────────
            # Independent of the prop-safety cache above (which is keyed on
            # propType+direction across ALL deviation levels and can have no
            # data for a specific combo, silently skipping the cap). The
            # line-deviation band hit rate measures a different, always-
            # available signal: how this exact "book strongly disagrees with
            # our projection" scenario has historically resolved. Guard 5
            # only applies a damped proportional nudge (e.g. 44% hit rate →
            # ~-3 to -8 pts), which can leave confidence sitting in "High"
            # territory (e.g. 72%) for a bet that has historically LOST more
            # than it won. Never show High/Strong confidence on a sub-50%
            # empirical hit rate — cap it the same way AVOID does above.
            _dev_band_final = prediction.get("lineDeviationBand")
            _dev_hit_final  = prediction.get("lineDeviationHitRate")
            if _dev_band_final in ("elevated", "extreme") and _dev_hit_final is not None:
                _post_conf = prediction.get("confidenceScore", 50)
                if _dev_hit_final <= 44:
                    _dev_cap = max(50, round(_dev_hit_final))
                    if _post_conf > _dev_cap:
                        prediction["confidenceScore"] = _dev_cap
                        prediction["confidenceLevel"] = "Medium" if _dev_cap >= 55 else "Low"
                        print(
                            f"[DEV CAP] {_er_prop} {_er_rec}: {_post_conf}% → {_dev_cap}% "
                            f"({_dev_band_final} band hist={_dev_hit_final}%)"
                        )
                elif _dev_hit_final < 50 and _post_conf > 65:
                    _dev_adj = max(55, _post_conf - 5)
                    if _dev_adj != _post_conf:
                        prediction["confidenceScore"] = _dev_adj
                        prediction["confidenceLevel"] = "High" if _dev_adj >= 70 else "Medium"
                        print(
                            f"[DEV ADJ] {_er_prop} {_er_rec}: {_post_conf}% → {_dev_adj}% "
                            f"({_dev_band_final} band hist={_dev_hit_final}%)"
                        )
        # ── CALIBRATION ALERT SUPPRESSION ────────────────────────────────────────
        # Walk-forward Brier score and calibration gap scans run every 6h and
        # flag sports/props where the model systematically over-states confidence.
        # When a sport or prop is flagged AVOID/RISKY at the walk-forward level,
        # apply the same cap logic as prop_safety above so users never see
        # "High" confidence from a statistically over-confident sport.
        if prediction.get("recommendation", "").upper() not in ("PASS",):
            try:
                from calibration_alerts import get_calibration_alert as _get_cal_alert
                _cal_alert = _get_cal_alert(
                    str(getattr(req, "sport", "") or ""),
                    str(getattr(req, "propType", "") or ""),
                )
                if _cal_alert and _cal_alert.get("alertLevel") in ("AVOID", "RISKY"):
                    _cal_level  = _cal_alert["alertLevel"]
                    _cal_brier  = _cal_alert.get("brierScore")
                    _cal_gap    = _cal_alert.get("maxOverGapPp")
                    _cal_src    = _cal_alert.get("source", "sport")
                    _post_conf  = prediction.get("confidenceScore", 50)
                    if _cal_level == "AVOID":
                        # Cap at 60 — systematic over-confidence should never show as High/Strong
                        _cal_cap = 60
                        if _post_conf > _cal_cap:
                            prediction["confidenceScore"] = _cal_cap
                            prediction["confidenceLevel"] = "Medium"
                            prediction["calibrationAlertApplied"] = {
                                "level": _cal_level, "source": _cal_src,
                                "brierScore": _cal_brier, "maxOverGapPp": _cal_gap,
                                "capApplied": _cal_cap, "from": _post_conf,
                            }
                            print(
                                f"[CAL AVOID] {_cal_src} alert: {_post_conf}% → capped {_cal_cap}% "
                                f"(Brier={_cal_brier}, gap={_cal_gap}pp)"
                            )
                    elif _cal_level == "RISKY" and _post_conf > 70:
                        # Soft −5pp reduction when walk-forward shows mild over-confidence
                        _cal_adj = max(60, _post_conf - 5)
                        if _cal_adj != _post_conf:
                            prediction["confidenceScore"] = _cal_adj
                            prediction["confidenceLevel"] = "High" if _cal_adj >= 70 else "Medium"
                            prediction["calibrationAlertApplied"] = {
                                "level": _cal_level, "source": _cal_src,
                                "brierScore": _cal_brier, "maxOverGapPp": _cal_gap,
                                "capApplied": _cal_adj, "from": _post_conf,
                            }
                            print(
                                f"[CAL RISKY] {_cal_src} alert: {_post_conf}% → {_cal_adj}% "
                                f"(Brier={_cal_brier}, gap={_cal_gap}pp)"
                            )
            except Exception as _cal_err:
                print(f"[CAL ALERT SUP] error: {_cal_err}")
        # ─────────────────────────────────────────────────────────────────────────────
        prediction.setdefault("probabilityCurve", [])
        prediction.setdefault("reasoning", "Analysis based on available data.")
        prediction.setdefault("tacticalInsights", "")

        # OVERRIDE: Lock matchupOverview to REAL DATA so it never fluctuates between predictions
        real_matchup = prediction.get("matchupOverview", {})
        # 1. Possession: Use MATCH DOMINANCE model (symmetric — always computed from HOME perspective)
        if match_dominance.get("homePoss") is not None:
            real_matchup["expectedPossession"] = {
                "home": match_dominance["homePoss"],
                "away": match_dominance["awayPoss"]
            }
        elif team_fixture_stats or opponent_fixture_stats:
            def avg_possession(stats_list):
                vals = []
                for s in (stats_list or []):
                    p = s.get("possession")
                    if p is not None:
                        try:
                            vals.append(float(str(p).replace("%", "")))
                        except (ValueError, TypeError):
                            pass
                return round(sum(vals) / len(vals), 0) if vals else None
            team_poss = avg_possession(team_fixture_stats)
            opp_poss = avg_possession(opponent_fixture_stats)
            if player_venue == "home":
                fb_home_avg = team_poss
                fb_away_avg = opp_poss
            elif player_venue == "away":
                fb_home_avg = opp_poss
                fb_away_avg = team_poss
            else:
                # Neutral venue: use _is_home (tiebreaker already applied above)
                # so the home/away orientation is consistent between both team scans.
                fb_home_avg = team_poss if _is_home else opp_poss
                fb_away_avg = opp_poss if _is_home else team_poss
            if fb_home_avg is not None and fb_away_avg is not None:
                fb_away_concedes = 100 - fb_away_avg
                fb_home_poss = round((fb_home_avg + fb_away_concedes) / 2.0 + 2.5)
                fb_home_poss = min(75, max(30, fb_home_poss))
                fb_away_poss = 100 - fb_home_poss
                real_matchup["expectedPossession"] = {"home": fb_home_poss, "away": fb_away_poss}
            elif fb_home_avg is not None:
                fb_home_poss = round(min(75, max(30, fb_home_avg + 2.5)))
                real_matchup["expectedPossession"] = {"home": fb_home_poss, "away": 100 - fb_home_poss}
            elif fb_away_avg is not None:
                fb_away_poss = round(min(75, max(30, fb_away_avg - 2.5)))
                real_matchup["expectedPossession"] = {"home": 100 - fb_away_poss, "away": fb_away_poss}
        _poss_source = match_dominance.get("possessionSource") or "unavailable"
        real_matchup["possessionSource"] = _poss_source
        real_matchup["possessionStatus"] = (
            "verified"
            if (
                match_dominance.get("seasonAvgIsReal") is True
                and match_dominance.get("possessionVerificationStatus") == "verified"
            )
            else "estimated"
            if _poss_source != "unavailable"
            else "unavailable"
        )
        real_matchup["possessionVerificationStatus"] = match_dominance.get(
            "possessionVerificationStatus"
        )
        real_matchup["possessionSampleRequired"] = _POSSESSION_SAMPLE_TARGET
        real_matchup["teamPossessionSampleSize"] = match_dominance.get(
            "teamPossessionSampleSize",
            0,
        )
        real_matchup["opponentPossessionSampleSize"] = match_dominance.get(
            "opponentPossessionSampleSize",
            0,
        )
        real_matchup["teamPossessionVenue"] = match_dominance.get(
            "teamPossessionVenue"
        )
        real_matchup["opponentPossessionVenue"] = match_dominance.get(
            "opponentPossessionVenue"
        )
        real_matchup["teamPossessionObservedAvg"] = match_dominance.get(
            "teamPossessionObservedAvg"
        )
        real_matchup["opponentPossessionObservedAvg"] = match_dominance.get(
            "opponentPossessionObservedAvg"
        )
        real_matchup["teamPossessionRows"] = match_dominance.get(
            "teamPossessionRows",
            [],
        )
        real_matchup["opponentPossessionRows"] = match_dominance.get(
            "opponentPossessionRows",
            [],
        )
        real_matchup["teamPossessionUsedCount"] = match_dominance.get(
            "teamPossessionUsedCount",
            0,
        )
        real_matchup["opponentPossessionUsedCount"] = match_dominance.get(
            "opponentPossessionUsedCount",
            0,
        )
        real_matchup["moneylineWeight"] = match_dominance.get(
            "moneylineWeight",
            0.0,
        )
        real_matchup["moneylineExpectedHomePoss"] = match_dominance.get(
            "moneylineExpectedHomePoss"
        )
        real_matchup["recencyWeighting"] = match_dominance.get(
            "recencyWeighting"
        )
        # 2. Moneyline + favorite from real odds data.
        # API-Football's home/away odds and the verified matchup team labels
        # are both fixture-oriented. Never swap these based on the player's
        # venue; doing so reverses a player's favorite/underdog context when
        # the player is the fixture away team.
        if match_odds:
            if match_odds.get("americanOdds"):
                ao = match_odds["americanOdds"]
                if ao.get("home") and ao.get("away") and ao.get("draw"):
                    real_matchup["moneyline"] = {
                        "home": str(ao["home"]),
                        "draw": str(ao["draw"]),
                        "away": str(ao["away"]),
                    }
            elif match_odds.get("bookmakerOdds"):
                bo = match_odds["bookmakerOdds"]
                h, d, a = bo.get("homeWin", ""), bo.get("draw", ""), bo.get("awayWin", "")
                if h and d and a and h != "N/A" and d != "N/A" and a != "N/A":
                    real_matchup["moneyline"] = {"home": h, "draw": d, "away": a}
            # Favorite uses the same fixture orientation as moneyline.home/away.
            if match_odds.get("favorite"):
                real_matchup["favorite"] = match_odds["favorite"]
        # 3. Game type from real stats — deterministic classification
        # ALWAYS override AI's expectedGameType. AI invents values like
        # "KNOCKOUT (HIGH-PRESSURE, END-TO-END)" for group stage matches.
        # Valid labels: open | cagey | one-sided | high-tempo only.
        _poss_diff = abs((real_matchup.get("expectedPossession", {}).get("home", 50)) - 50)
        if team_fixture_stats and opponent_fixture_stats:
            def avg_stat(stats_list, key):
                vals = [s.get(key) for s in stats_list if s.get(key) is not None]
                return sum(vals) / len(vals) if vals else 0
            team_avg_shots = avg_stat(team_fixture_stats, "totalShots")
            opp_avg_shots = avg_stat(opponent_fixture_stats, "totalShots")
            combined_shots = team_avg_shots + opp_avg_shots
            if combined_shots >= 28:
                real_matchup["expectedGameType"] = "open"
            elif combined_shots <= 18:
                real_matchup["expectedGameType"] = "cagey"
            elif _poss_diff >= 12:
                real_matchup["expectedGameType"] = "one-sided"
            else:
                real_matchup["expectedGameType"] = "high-tempo" if combined_shots >= 23 else "cagey"
        else:
            # No shot data — classify purely from possession imbalance
            if _poss_diff >= 14:
                real_matchup["expectedGameType"] = "one-sided"
            elif _poss_diff >= 6:
                real_matchup["expectedGameType"] = "open"
            else:
                real_matchup["expectedGameType"] = "open"

        # Final sanitisation — reject any value AI invented that isn't in the approved set
        _valid_game_types = {"open", "cagey", "one-sided", "high-tempo"}
        if real_matchup.get("expectedGameType", "open").lower().strip() not in _valid_game_types:
            real_matchup["expectedGameType"] = "one-sided" if _poss_diff >= 12 else "open"

        # 4. Always set team names from request data (deterministic)
        # Use _is_home (which is now based on playerIsHome from the fixture) to
        # determine which team is the home team, NOT the user's venue input.
        real_matchup["homeTeam"] = (
            _canonical_team_name if _is_home else _canonical_opponent_name
        )
        real_matchup["awayTeam"] = (
            _canonical_opponent_name if _is_home else _canonical_team_name
        )
        # Keep one canonical odds contract for every consumer.  The mobile
        # prediction card reads the top-level field, while saved-pick analysis
        # may read matchupOverview.  Both must use the same fixture-oriented
        # homeTeam/awayTeam pairing.
        if real_matchup.get("moneyline"):
            prediction["moneyline"] = dict(real_matchup["moneyline"])

        # Expose team/opponent names at the TOP LEVEL of the response so the
        # frontend can use them directly without digging into matchupOverview.
        # The frontend checks prediction.opponentName, prediction.teamName,
        # prediction.homeTeam, and prediction.awayTeam — these were missing,
        # causing "HOME" / "AWAY" fallback labels in the possession bar.
        prediction["opponentName"] = _canonical_opponent_name or ""
        prediction["teamName"]     = _canonical_team_name or ""
        prediction["homeTeam"]     = real_matchup["homeTeam"]
        prediction["awayTeam"]     = real_matchup["awayTeam"]
        prediction["isHome"]       = _is_home
        prediction["possessionSource"] = _poss_source
        prediction["possessionStatus"] = real_matchup["possessionStatus"]
        prediction["possessionVerificationStatus"] = real_matchup.get(
            "possessionVerificationStatus"
        )
        prediction["possessionSampleRequired"] = _POSSESSION_SAMPLE_TARGET
        prediction["teamPossessionSampleSize"] = real_matchup.get(
            "teamPossessionSampleSize",
            0,
        )
        prediction["opponentPossessionSampleSize"] = real_matchup.get(
            "opponentPossessionSampleSize",
            0,
        )
        if match_odds and match_odds.get("fixtureId"):
            prediction["fixtureId"] = match_odds["fixtureId"]
            prediction["fixtureDate"] = match_odds.get("matchDate", "")
            prediction["fixtureTeamId"] = match_odds.get("fixtureTeamId")
            prediction["fixtureOpponentId"] = match_odds.get("fixtureOpponentId")
        # 5. Deterministic keyMatchupFactor — MUST align with computed possession numbers.
        # Overrides AI-generated text to prevent contradictions like "Liverpool dominates
        # possession" when the model computed PSG at 62% and Liverpool at 38%.
        _ep = real_matchup.get("expectedPossession", {})
        _home_p = _ep.get("home", 50)
        _away_p = _ep.get("away", 50)
        _home_team = real_matchup.get("homeTeam", "Home")
        _away_team = real_matchup.get("awayTeam", "Away")
        _game_type = real_matchup.get("expectedGameType", "open")
        _game_type_label = {"open": "open", "cagey": "cagey", "one-sided": "one-sided", "high-tempo": "high-tempo"}.get(_game_type, _game_type)
        if _home_p >= 58:
            _kmf = f"{_home_team}'s possession dominance ({_home_p:.0f}%) expected to control tempo at home"
        elif _away_p >= 58:
            _kmf = f"{_away_team}'s possession superiority ({_away_p:.0f}%) expected to control the ball despite playing away"
        elif _home_p >= 53:
            _kmf = f"{_home_team} holds home possession edge ({_home_p:.0f}% vs {_away_p:.0f}%) in an {_game_type_label} game"
        elif _away_p >= 53:
            _kmf = f"{_away_team} holds possession edge ({_away_p:.0f}% vs {_home_p:.0f}%) in an {_game_type_label} game despite being away"
        else:
            _kmf = f"Balanced possession expected ({_home_p:.0f}% vs {_away_p:.0f}%) — {_game_type_label} game"
        real_matchup["keyMatchupFactor"] = _kmf

        prediction["matchupOverview"] = real_matchup

        # ── OPPONENT DEFENSIVE PROFILE ────────────────────────────────────────
        # How does the opponent's recent defending compare to what this player
        # typically produces?  Uses the opponentAllowedAvg already computed by
        # the Bayesian engine (weighted average of what this position/prop gets
        # vs this opponent in recent matches) vs. the player's prior mean.
        # Only attach when we have ≥3 opponent samples so the signal is reliable.
        if real_bayes and req.propType not in {"goals", "assists"}:
            _op_allowed  = real_bayes.get("opponentAllowedAvg")
            _op_n        = int(real_bayes.get("opponentAllowedSamples") or 0)
            _op_baseline = real_bayes.get("priorMean")
            if _op_allowed is not None and _op_baseline and _op_baseline > 0 and _op_n >= 3:
                _op_diff_pct = round((_op_allowed - _op_baseline) / _op_baseline * 100, 1)
                _op_tier = (
                    "elite suppressor" if _op_diff_pct <= -30 else
                    "strong suppressor" if _op_diff_pct <= -15 else
                    "slight suppressor" if _op_diff_pct <= -5  else
                    "elite leak"        if _op_diff_pct >= 30  else
                    "notable leak"      if _op_diff_pct >= 15  else
                    "slight lean"       if _op_diff_pct >= 5   else
                    "neutral"
                )
                _op_is_neg = _op_diff_pct < 0
                prediction["opponentProfile"] = {
                    "allowedAvg":    round(_op_allowed, 1),
                    "playerBaseline": round(_op_baseline, 1),
                    "diffPct":       _op_diff_pct,
                    "tier":          _op_tier,
                    "sampleSize":    _op_n,
                    "propType":      req.propType,
                    "description": (
                        f"Comparable {req.propType.replace('_', ' ')} observations against "
                        f"{req.opponentName} averaged {abs(_op_diff_pct):.0f}% "
                        f"{'below' if _op_is_neg else 'above'} the player's baseline "
                        f"for this position ({_op_n} games)"
                    ),
                }
                print(
                    f"[OPP PROFILE] {req.playerName}/{req.propType}: "
                    f"allowed={_op_allowed:.1f} baseline={_op_baseline:.1f} "
                    f"diff={_op_diff_pct:+.1f}% → {_op_tier} (n={_op_n})"
                )

        # Add match context (competition name, round) for frontend display
        if match_odds:
            mc = {}
            if match_odds.get("matchLeague"):
                mc["league"] = match_odds["matchLeague"]
            if match_odds.get("matchRound"):
                mc["round"] = match_odds["matchRound"]
            if match_odds.get("matchDate"):
                mc["date"] = match_odds["matchDate"][:10]
            if mc:
                prediction["matchContext"] = mc

        # Expose situation engine result to frontend (second leg, aggregate, injuries)
        if game_situation:
            _agg = game_situation.get("aggregate", {})
            prediction["gameSituation"] = {
                "isKnockout": game_situation.get("isKnockout", False),
                "isSecondLeg": game_situation.get("isSecondLeg", False),
                "aggregate": {
                    "firstLegFound": _agg.get("firstLegFound", False),
                    "firstLegScore": _agg.get("firstLegScore", ""),
                    "homeTeamAggregate": _agg.get("homeTeamAggregate", 0),
                    "awayTeamAggregate": _agg.get("awayTeamAggregate", 0),
                    "goalDeficit": _agg.get("goalDeficit", 0),
                    "homeTeamTrailing": _agg.get("homeTeamTrailing", False),
                    "mustWinByGoals": _agg.get("mustWinByGoals", 0),
                },
                "injuries": game_situation.get("injuries", {}).get("summaryText", ""),
                "matchStakes": game_situation.get("matchStakes"),
            }

        # DATA QUALITY INDICATOR — flag when API data might be unreliable
        total_game_logs = len(player_game_logs)
        _is_synthetic = total_game_logs > 0 and all(g.get("synthetic") for g in player_game_logs)
        gl_target_field_map_check = {
            "pass_attempts": "passes_total", "shots": "shots_total", "shots_on_target": "shots_on",
            "tackles": "tackles_total", "key_passes": "passes_key", "shots_assisted": "passes_key",
            "saves": "goals_saves", "goalie_saves": "goals_saves",
            "interceptions": "tackles_interceptions", "blocks": "tackles_blocks",
            "dribbles": "dribbles_attempts", "fouls_drawn": "fouls_drawn",
            "crosses": "passes_crosses", "clearances": "tackles_clearances",
            "goals": "goals_total", "assists": "goals_assists",
            "duels_won": "duels_won", "yellow_cards": "cards_yellow",
            "fouls_committed": "fouls_committed",
        }
        target_check = gl_target_field_map_check.get(req.propType, "passes_total")
        games_with_data = sum(1 for g in player_game_logs if g.get(target_check) is not None)
        games_with_none = total_game_logs - games_with_data
        if _is_synthetic:
            prediction["dataQuality"] = {
                "level": "medium",
                "message": f"No recent match logs cached. Analysis based on season averages ({total_game_logs} appearances).",
                "gamesWithData": games_with_data,
                "totalGames": total_game_logs,
            }
        elif total_game_logs > 0 and games_with_none / total_game_logs >= 0.3:
            prediction["dataQuality"] = {
                "level": "limited",
                "message": f"API data incomplete — {games_with_none} of {total_game_logs} recent games missing {req.propType} stats. Cross-referenced sources used for analysis.",
                "gamesWithData": games_with_data,
                "totalGames": total_game_logs,
            }
        elif total_game_logs < 3:
            prediction["dataQuality"] = {
                "level": "low",
                "message": f"Only {total_game_logs} game logs available. Limited sample size for accurate projection.",
                "gamesWithData": games_with_data,
                "totalGames": total_game_logs,
            }
        else:
            prediction["dataQuality"] = {
                "level": "good",
                "message": "",
                "gamesWithData": games_with_data,
                "totalGames": total_game_logs,
            }

        # Compact analysis summary for the UI
        prop_key = req.propType or ""
        if prop_key == "shots_on_target":
            stat_label = "Shots on Target"
        elif prop_key == "saves":
            stat_label = "Goalkeeper Saves"
        else:
            stat_label = {
                "pass_attempts": "Pass Attempts",
                "shots": "Shots",
                "tackles": "Tackles",
                "key_passes": "Key Passes",
                "saves": "Saves",
                "interceptions": "Interceptions",
                "blocks": "Blocks",
                "dribbles": "Dribbles",
                "fouls_drawn": "Fouls Drawn",
            }.get(prop_key, prop_key.replace("_", " ").title())

        venue_samples = [g for g in player_game_logs if g.get("venue") == player_venue and g.get(target_check) is not None]
        venue_avg = round(sum((g.get(target_check) or 0) for g in venue_samples) / len(venue_samples), 2) if venue_samples else None
        opp_allowed_avg = None
        opp_stat_field_map = {
            # ONLY include props where team-level opponent stats are actually meaningful.
            # Pass-volume props (pass_attempts, passes, key_passes, crosses, dribbles) are
            # possession-dependent: opponent's totalPasses tells us nothing about what
            # they concede to individual players in those categories — removed.
            "shots": "totalShots",
            "shots_on_target": "shotsOnTarget",
            "saves": "shotsOnTarget",
            "tackles": "totalShots",
            "interceptions": "totalShots",
            "blocks": "totalShots",
            "fouls_drawn": "fouls",
            "clearances": "totalShots",
        }
        opp_stat_key = opp_stat_field_map.get(req.propType)
        if opp_stat_key and opponent_fixture_stats:
            opp_vals = [g.get(opp_stat_key) for g in opponent_fixture_stats if g.get(opp_stat_key) is not None]
            if opp_vals:
                try:
                    opp_vals_num = [float(str(v).replace("%", "")) for v in opp_vals]
                    opp_allowed_avg = round(sum(opp_vals_num) / len(opp_vals_num), 1)
                except (ValueError, TypeError):
                    pass

        # A position comparison is a player-event cohort, not a team-level
        # "allowed" total. Prefer it for every prop when available so the
        # analysis describes exactly what comparable players recorded against
        # this opponent at the matching venue.
        if (
            position_comp_data
            and position_comp_data.get("projectionEligible")
            and position_comp_data.get("avgStatValue") is not None
        ):
            opp_allowed_avg = round(float(position_comp_data["avgStatValue"]), 1)

        prediction["analysisSummary"] = {
            "statLabel": stat_label,
            "venue": player_venue,
            "venueSampleSize": len(venue_samples),
            "venueAverage": venue_avg,
            "opponentAllowedAverage": opp_allowed_avg,
            "opponentEvidenceSource": (
                "same_position_player_cohort"
                if (
                    position_comp_data
                    and position_comp_data.get("projectionEligible")
                    and position_comp_data.get("avgStatValue") is not None
                )
                else "team_match_stats"
                if opp_allowed_avg is not None
                else None
            ),
            "goalkeeperSaveRate": gk_formula_data.get("gkSaveRate") if gk_formula_data else None,
            "goalkeeperSaveSample": gk_formula_data.get("gkSampleSize") if gk_formula_data else None,
            "opponentShotsOnTarget": gk_formula_data.get("opponentAvgSOT") if gk_formula_data else None,
        }

        # Grounded tactical context.  This is deliberately a structured
        # evidence packet, not an LLM-generated claim set.  The explanation
        # renderer uses only these values to describe the player's likely
        # role-to-prop mechanism and explicitly omits unsupported mechanisms.
        _tc_bm = real_bayes or {}
        _tc_poss_player = match_dominance.get("expectedPoss")
        _tc_poss_opp = match_dominance.get("oppExpectedPoss")
        _tc_role = display_role or player_role or ""
        _tc_position = specific_position or player_position or ""
        _tc_opp_profile = prediction.get("opponentProfile") or {}
        _tc_team_form = match_dominance.get("teamSeasonAvg") if match_dominance.get("seasonAvgIsReal") else None
        _tc_lineup = locals().get("_pitch_lineup") or {}
        _tc_target_lineup = [
            item for item in (_tc_lineup.get("players") or [])
            if item.get("isTarget")
        ]
        _fbref_player_kb = None
        _fbref_opponent_kb = None
        try:
            from knowledge_base import get_player_kb, get_team_kb
            _fbref_player_kb, _fbref_opponent_kb = await aio.gather(
                get_player_kb(req.playerId or 0, req.leagueId or 0),
                get_team_kb(
                    req.opponentId or prediction.get("fixtureOpponentId") or 0,
                    req.leagueId or 0,
                    getattr(req, "season", None) or CURRENT_SEASON,
                ),
                return_exceptions=True,
            )
            if not isinstance(_fbref_player_kb, dict):
                _fbref_player_kb = None
            if not isinstance(_fbref_opponent_kb, dict):
                _fbref_opponent_kb = None
        except Exception as _fbref_read_err:
            print(f"[FBREF CONTEXT] read skipped: {_fbref_read_err}")

        _fbref_pressure = {}
        if _fbref_opponent_kb and _fbref_opponent_kb.get("fbrefStatus") in {"available", "partial"}:
            _fbref_pressure = {
                "status": _fbref_opponent_kb.get("fbrefStatus"),
                "label": _fbref_opponent_kb.get("pressIntensityLabel"),
                "ppda": _fbref_opponent_kb.get("ppda"),
                "source": _fbref_opponent_kb.get("pressIntensitySource"),
                "method": _fbref_opponent_kb.get("pressIntensityMethod"),
                "pressures": _fbref_opponent_kb.get("fbrefPressures"),
                "pressureSuccessPct": _fbref_opponent_kb.get("fbrefPressureSuccessPct"),
            }
        _fbref_zones = {}
        if _fbref_player_kb and _fbref_player_kb.get("fbrefStatus") in {"available", "partial"}:
            _fbref_zones = {
                "status": _fbref_player_kb.get("fbrefStatus"),
                "dominance": _fbref_player_kb.get("zoneDominance"),
                "defThirdSharePct": _fbref_player_kb.get("defThirdSharePct"),
                "midThirdSharePct": _fbref_player_kb.get("midThirdSharePct"),
                "attThirdSharePct": _fbref_player_kb.get("attThirdSharePct"),
                "progressivePasses": _fbref_player_kb.get("progressivePasses"),
                "progressiveCarries": _fbref_player_kb.get("progressiveCarries"),
            }
        _fbref_available = bool(_fbref_pressure or _fbref_zones)
        _press_intensity = (
            (early_bayes or {}).get("pressIntensity")
            if isinstance(early_bayes, dict)
            else None
        )
        if not isinstance(_press_intensity, dict):
            _press_intensity = {
                "available": False,
                "status": "unavailable",
                 "score": None,
                 "score100": None,
                "label": "Unavailable",
                "source": "api_football",
                 "metric": "reverse_picks_pressure_index",
                "reasoning": "Press Intensity was not returned by the Bayesian layer.",
                "sampleSize": 0,
                "sampleStatus": "unavailable",
                "projectionApplied": False,
                "projectionMultiplier": 1.0,
            }
        prediction["tacticalContext"] = {
            "available": bool(
                _tc_position
                or _tc_role
                or _tc_poss_player is not None
                or _fbref_available
                or isinstance(_press_intensity, dict)
                or recent_block_profiles.get("available")
            ),
            "position": _tc_position or None,
            "role": _tc_role or None,
            "roleSource": _observed_role.get("source") if _observed_role else (
                "position-and-role-resolver" if (_tc_position or _tc_role) else None
            ),
            "roleConfidence": _observed_role.get("confidence") if _observed_role else None,
            "roleEvidence": _observed_role.get("evidence", []) if _observed_role else [],
            "roleSampleSize": _observed_role.get("sampleSize", 0) if _observed_role else 0,
            "observedPositionHistory": _historical_position_summary,
            "propType": req.propType,
            "playerTeam": player_team_display,
            "opponent": req.opponentName,
            "venue": player_venue,
            "expectedPossession": _tc_poss_player,
            "opponentExpectedPossession": _tc_poss_opp,
            "possessionSource": (
                match_dominance.get("possessionSource")
                if _tc_poss_player is not None
                else None
            ),
            "teamSeasonPossession": _tc_team_form,
            "opponentAllowedAverage": opp_allowed_avg,
            "opponentAllowedSamples": (
                _tc_opp_profile.get("sampleSize")
                or _tc_bm.get("opponentAllowedSamples")
                or 0
            ),
            "pressureResponse": _pressure_response,
            "pressIntensity": _press_intensity,
            "recentOpponentBlockProfiles": recent_block_profiles,
            "recentOpponentPressIntensity": recent_opponent_press_intensity,
            "opponentProfileTier": _tc_opp_profile.get("tier"),
            "opponentProfileDiffPct": _tc_opp_profile.get("diffPct"),
            "venueAverage": venue_avg,
            "venueSampleSize": len(venue_samples),
            "seasonAverage": _tc_bm.get("priorMean"),
            "recentMomentum": _tc_bm.get("momentumLabel"),
            "recentMomentumEffect": _tc_bm.get("momentumEffect"),
            "tempo": game_tempo.get("expectedTempo"),
            "expectedTotalGoals": game_tempo.get("expectedTotalGoals"),
            "favoriteDampeningApplied": bool(favorite_dampening.get("applied")),
            "favoriteDampeningNote": favorite_dampening.get("note"),
            "lineupStatus": _lineup_status,
            "lineupFormation": _tc_lineup.get("formation"),
            "opponentFormation": _tc_lineup.get("opponentFormation"),
            "targetLineupPosition": (_tc_target_lineup[0] or {}).get("pos") if _tc_target_lineup else None,
            "playerOpponentHistory": (historical_data.get("h2hPlayerStats") or {}).get("opponentHitRate"),
            "positionCohort": position_comp_data,
            "statsbombEnrichment": statsbomb_enrichment,
            "fbrefEnrichment": {
                "available": _fbref_available,
                "pressure": _fbref_pressure or None,
                "zones": _fbref_zones or None,
                "status": "available" if _fbref_available else "warming",
                "projectionInfluence": "explanation_only",
            },
        }

        # ── PURE MATH ANALYSIS — no AI paragraphs ────────────────────────────────
        # Tactical intelligence is assembled from the same verified fixture,
        # lineup, odds, role, and opponent evidence as the projection. It is
        # shadow-only for numeric changes until settled-pick calibration proves
        # a signal is safe to activate.
        _tactical_stat_fields = {
            "pass_attempts": "passes_total", "passes": "passes_total",
            "shots": "shots_total", "shots_on_target": "shots_on",
            "goals": "goals_total", "assists": "goals_assists",
            "key_passes": "passes_key", "shots_assisted": "passes_key",
            "tackles": "tackles_total", "saves": "goals_saves",
            "goalie_saves": "goals_saves", "interceptions": "tackles_interceptions",
            "blocks": "tackles_blocks", "dribbles": "dribbles_attempts",
            "crosses": "passes_crosses", "clearances": "tackles_clearances",
            "fouls_drawn": "fouls_drawn", "fouls_committed": "fouls_committed",
            "duels_won": "duels_won",
        }
        _tactical_history_values = [
            (game or {}).get(_tactical_stat_fields.get(req.propType, "passes_total"))
            for game in (player_game_logs or [])
            if isinstance(game, dict)
        ]
        try:
            from tactical_intelligence import (
                build_tactical_explanation,
                build_tactical_intelligence,
            )
            prediction["tacticalIntelligence"] = build_tactical_intelligence(
                prediction=prediction,
                prop_type=req.propType,
                player_position=specific_position or player_position,
                player_role=display_role or player_role,
                expected_possession=match_dominance.get("expectedPoss"),
                possession_is_real=match_dominance.get("possessionSource")
                in {"fixture_stats", "h2h_fixture_stats"},
                possession_source=match_dominance.get("possessionSource"),
                opponent_allowed_average=opp_allowed_avg,
                opponent_allowed_samples=int(
                    (position_comp_data or {}).get("sampleSize")
                    or (real_bayes or {}).get("opponentAllowedSamples")
                    or 0
                ),
                position_comparable_samples=int(
                    (position_comp_data or {}).get("sampleSize") or 0
                )
                if (position_comp_data or {}).get("projectionEligible")
                else 0,
                game_script=prediction.get("gameScript"),
                lineup=prediction.get("lineup"),
                history_values=_tactical_history_values,
                press_intensity=_press_intensity,
            )
        except Exception as _tactical_intel_err:
            print(f"[TACTICAL INTELLIGENCE] failed: {_tactical_intel_err}")
            prediction["tacticalIntelligence"] = {
                "version": "tactical-shadow-v2",
                "mode": "shadow",
                "status": "unavailable",
                "limitations": ["tactical intelligence assembly failed"],
            }
        # Stable top-level aliases make the structured signals easy to consume
        # in replay/backtest jobs without requiring consumers to understand the
        # entire tactical packet shape. The nested packet remains canonical for
        # the UI and saved-pick audit trail.
        _ti_packet = prediction.get("tacticalIntelligence") or {}
        if isinstance(_ti_packet, dict):
            prediction["matchScript"] = _ti_packet.get("matchScript") or None
            prediction["positionalReality"] = _ti_packet.get("positionalReality") or None

        _m_rec    = prediction.get("recommendation", "over").upper()
        _m_proj   = prediction.get("projectedValue", req.line)
        _m_conf   = prediction.get("confidenceScore", 50)
        _m_lvl    = prediction.get("confidenceLevel", "Medium")
        _m_proj_s = str(int(_m_proj)) if _m_proj == int(_m_proj) else f"{_m_proj:.1f}"
        _m_line_s = str(int(req.line)) if req.line == int(req.line) else f"{req.line:.1f}"
        _m_edge   = round(abs(_m_proj - req.line), 1)

        _m_rb     = real_bayes or {}
        _m_pover  = _m_rb.get("pOver", 50)
        _m_punder = _m_rb.get("pUnder", 50)
        _m_pwin   = max(_m_pover, _m_punder)
        _m_mom    = _m_rb.get("momentumLabel", "STABLE")
        _m_rev    = _m_rb.get("reversalFlag", "stable").upper()
        _m_cov    = _m_rb.get("covariateAdjustment", 0) or 0
        _m_prior  = _m_rb.get("priorMean") or (early_bayes.get("priorMean") if early_bayes else None) or "—"

        # Verdict line
        _m_dir_lbl = "clearing" if _m_rec == "OVER" else ("within noise of" if _m_rec == "PASS" else "falling short of")
        _m_verdict = (
            f"**Verdict** — Reverse Formula projects **{_m_proj_s}**, {_m_dir_lbl} the {_m_line_s} line "
            f"({_m_rec} | {_m_pwin:.0f}% | edge: {_m_edge})."
        )

        # Math Engine numbers block
        _m_math = (
            f"**Math Engine**\n"
            f"Projection: {_m_proj_s}  |  Line: {_m_line_s}  |  Edge: {_m_edge}\n"
            f"P(OVER): {_m_pover:.0f}%  |  P(UNDER): {_m_punder:.0f}%\n"
            f"Season avg: {_m_prior}  |  Covariate adj: {_m_cov:+.1f}\n"
            f"Momentum: {_m_mom}  |  Reversal flag: {_m_rev}  |  Confidence: {_m_conf}% ({_m_lvl})"
        )

        # Game Log section (reuse pre-parsed data from wave2_supplement)
        _m_log_str = ""
        _gl_d2 = wave2_supplement.get("playerGameLogs", {}) if wave2_supplement else {}
        _gl_g2 = _gl_d2.get("games", [])
        if _gl_g2:
            import re as _re_ml2
            _gl_fmt2 = []
            for _gs2 in _gl_g2[-8:]:
                _mm2 = _re_ml2.match(r"(\d{4}-(\d{2})-(\d{2})) vs (.+?) \((.+?), (\d+)min\): (.+)", _gs2)
                if _mm2:
                    _gl_fmt2.append(f"{_mm2.group(7)} vs {_mm2.group(4)} ({_mm2.group(5)}, {_mm2.group(6)}min)")
                else:
                    _gl_fmt2.append(_gs2)
            _gl_avg2   = _gl_d2.get("rawAvg", "—")
            _gl_h_avg2 = _gl_d2.get("homeAvg", "—")
            _gl_a_avg2 = _gl_d2.get("awayAvg", "—")
            _gl_n2     = _gl_d2.get("sampleSize", len(_gl_fmt2))
            _m_log_str = (
                f"**Game Log** ({req.propType}, last {len(_gl_fmt2)} games)\n"
                + " | ".join(_gl_fmt2) + "\n"
                + f"Season avg: {_gl_avg2}  |  Home avg: {_gl_h_avg2}  |  Away avg: {_gl_a_avg2}  |  n={_gl_n2}"
            )

        # Hit Rate section
        _m_hr_str = ""
        _hr2 = _gl_d2.get("hitRates") if _gl_d2 else None
        if _hr2:
            _m_hr_str = f"**Hit Rate**\n{_hr2.get('summary', '')}"

        # Opponent Profile
        _m_opp_parts = []
        if position_comp_data and position_comp_data.get("avgStatValue"):
            _opp_avg2 = position_comp_data["avgStatValue"]
            _opp_n2   = position_comp_data.get("sampleSize", 0)
            _opp_pos2 = position_comp_data.get("positionShort", "position")
            _m_opp_parts.append(
                build_position_cohort_statement(
                    opponent=req.opponentName,
                    prop_type=req.propType,
                    position=_opp_pos2,
                    average=_opp_avg2,
                    sample_size=_opp_n2,
                    venue=position_comp_data.get("venue") or player_venue,
                )
                or f"{req.opponentName} matchup sample: {_opp_avg2:.1f} "
                f"{req.propType.replace('_', ' ')} across {_opp_n2} comparable observations"
            )
        if h2h_data:
            _h2h_v2 = [g.get("stat_value") or g.get("statValue") for g in h2h_data
                       if g.get("stat_value") or g.get("statValue")]
            if _h2h_v2:
                _h2h_avg2 = round(sum(_h2h_v2) / len(_h2h_v2), 1)
                _m_opp_parts.append(f"H2H avg: {_h2h_avg2} ({len(_h2h_v2)} games vs {req.opponentName})")
        if _m_opp_parts:
            _m_opp_str = "**Opponent Profile**\n" + "  |  ".join(_m_opp_parts)
        else:
            _m_opp_str = ""

        # Scenarios block
        _m_sp2 = prediction.get("scenarioProbabilities", {}) or {}
        _m_scen_str = ""
        if _m_sp2 and any(_m_sp2.get(k) is not None for k in ("best", "base", "worst")):
            _s_best = round((_m_sp2.get("best") or 0) * 100)
            _s_base = round((_m_sp2.get("base") or 0) * 100)
            _s_wrst = round((_m_sp2.get("worst") or 0) * 100)
            _m_scen_str = f"**Scenarios**\nBest: {_s_best}%  |  Base: {_s_base}%  |  Worst: {_s_wrst}%"

        # TL;DR
        _m_tldr = (
            f"**TL;DR** — {_m_proj_s} {_m_rec} {_m_line_s}  |  "
            f"P({_m_rec}): {_m_pwin:.0f}%  |  Edge: {_m_edge}  |  "
            f"{_m_conf}% confidence ({_m_lvl})"
        )

        # ── Assemble the math engine block (always computed — used as footer
        #    when AI succeeded, or as full breakdown when AI failed).
        _m_sections = [_m_verdict, _m_math]
        if _m_log_str:  _m_sections.append(_m_log_str)
        if _m_hr_str:   _m_sections.append(_m_hr_str)
        if _m_opp_str:  _m_sections.append(_m_opp_str)
        if _m_scen_str: _m_sections.append(_m_scen_str)
        _m_sections.append(_m_tldr)
        _m_full_block = "\n\n".join(_m_sections)

        _m_ev_note = ""
        if position_comp_data and position_comp_data.get("avgStatValue"):
            _m_ev_note = (
                " "
                + (
                    build_position_cohort_statement(
                        opponent=req.opponentName,
                        prop_type=req.propType,
                        position=position_comp_data.get("positionShort"),
                        average=position_comp_data["avgStatValue"],
                        sample_size=position_comp_data.get("sampleSize", 0),
                        venue=position_comp_data.get("venue") or player_venue,
                    )
                    or ""
                )
            )
        _m_sharp_summary = (
            f"Reverse Formula: {_m_proj_s} {_m_rec} {_m_line_s} "
            f"(P({_m_rec}): {_m_pwin:.0f}%, edge: {_m_edge})."
            f"{_m_ev_note}"
        )

        _analysis_text = prediction.get("tacticalBreakdown", "")
        if not isinstance(_analysis_text, str):
            _analysis_text = json.dumps(_analysis_text) if _analysis_text else ""
        _summary_text = prediction.get("sharpSummary", "")
        if not isinstance(_summary_text, str):
            _summary_text = json.dumps(_summary_text) if _summary_text else ""

        if _analysis_text and len(_analysis_text.strip()) > 100:
            # Keep the deterministic explanation and append the final math footer.
            prediction["tacticalBreakdown"] = _analysis_text.strip() + "\n\n---\n" + _m_math + "\n" + _m_tldr
            prediction["aiSource"] = "model"
            if not (_summary_text and len(_summary_text.strip()) > 20):
                prediction["sharpSummary"] = _m_sharp_summary
            print(f"[MODEL SUMMARY] Using deterministic tacticalBreakdown ({len(_analysis_text)} chars) + math footer appended")
        else:
            # ── Missing explanation — use the reproducible math breakdown ──
            prediction["tacticalBreakdown"] = _m_full_block
            prediction["sharpSummary"] = _m_sharp_summary
            prediction["aiSource"] = "model"
            print(f"[PURE MODEL] Explanation absent — using math-only tacticalBreakdown ({len(_m_full_block)} chars)")

        # ── Game Script — attach computed scenario probabilities + script analysis
        # The gameScript engine uses Poisson(λ_h) × Poisson(λ_a) to forecast likely
        # match scenarios (draw, low_scoring, high_scoring, open_close, blowouts).
        # Settled data revealed: draw/blowout predictions are unreliable (0%/44% hit).
        # We apply a "smart remap" that spreads draw prob into low_scoring/open_close
        # and blowout prob into high_scoring, so the engine surfaces the macro
        # buckets we actually nail (high / low / open = 100% accuracy).
        if _scenario_probs and _scenario_probs.get("available"):
            _raw_probs = {k: v for k, v in _scenario_probs.items() if k.startswith("P_")}
            # Smart remap: collapse unreliable micro-buckets into reliable macro ones
            _smart = {
                "P_low_scoring": (
                    _raw_probs.get("P_low_scoring", 0)
                    + _raw_probs.get("P_draw", 0) * 0.83   # 82.7% of draws are low-scoring
                ),
                "P_open_close": (
                    _raw_probs.get("P_open_close", 0)
                    + _raw_probs.get("P_draw", 0) * 0.17   # 17.2% of draws are high-scoring
                ),
                "P_high_scoring": (
                    _raw_probs.get("P_high_scoring", 0)
                    + _raw_probs.get("P_home_blowout", 0) * 0.53  # 53.2% of home_blowouts are high-scoring
                    + _raw_probs.get("P_away_blowout", 0) * 0.50   # similar pattern for away
                ),
                "P_home_blowout": _raw_probs.get("P_home_blowout", 0) * 0.47,
                "P_away_blowout": _raw_probs.get("P_away_blowout", 0) * 0.50,
                "P_draw": 0.0,  # draw probability fully absorbed into low/open
            }
            # Renormalise
            _total = sum(_smart.values())
            if _total > 0:
                for k in _smart:
                    _smart[k] /= _total
            # Pick dominant macro script
            _macro = {k[2:]: v for k, v in _smart.items() if not k.startswith("P_draw")}
            _dominant = max(_macro, key=_macro.get)
            _dom_prob = round(_macro[_dominant], 3)

            _script_labels = {
                "low_scoring":   "LOW-SCORING MATCH",
                "high_scoring":  "HIGH-SCORING MATCH",
                "open_close":    "OPEN MATCH",
                "home_blowout":  "HOME DOMINANT",
                "away_blowout":  "AWAY DOMINANT",
            }
            _script_colors = {
                "low_scoring":   "#6B7280",
                "high_scoring":  "#39FF14",
                "open_close":    "#60A5FA",
                "home_blowout":  "#FBBF24",
                "away_blowout":  "#FBBF24",
            }

            prediction["gameScript"] = {
                "key_finding": _script_labels.get(_dominant, "OPEN MATCH"),
                "scenarios": [
                    {"name": k.replace("_", " ").title(), "probability": round(v, 3)}
                    for k, v in sorted(_macro.items(), key=lambda x: -x[1])
                    if v > 0.01
                ],
                "dominant": _dominant,
                "dominant_probability": _dom_prob,
                "color": _script_colors.get(_dominant, "#60A5FA"),
                "expected_total_goals": _scenario_probs.get("expectedTotal"),
                "implied_home": _scenario_probs.get("impliedHome"),
                "implied_away": _scenario_probs.get("impliedAway"),
                "implied_draw": _scenario_probs.get("impliedDraw"),
                "raw_scenarios": [
                    {"name": k[2:].replace("_", " ").title(), "probability": round(v, 3)}
                    for k, v in sorted(_raw_probs.items(), key=lambda x: -x[1])
                    if v > 0.01
                ],
                "smart_remap": _scenario_priors_result is not None,
            }
        else:
            prediction["gameScript"] = {"key_finding": "Game script unavailable", "scenarios": []}

        # The scenario model is assembled after the first tactical packet. Rebuild
        # the pure packet once so the formal match-script classifier can use the
        # final dominant scenario as well as the verified market/possession inputs.
        try:
            from tactical_intelligence import build_tactical_intelligence
            prediction["tacticalIntelligence"] = build_tactical_intelligence(
                prediction=prediction,
                prop_type=req.propType,
                player_position=specific_position or player_position,
                player_role=display_role or player_role,
                expected_possession=match_dominance.get("expectedPoss"),
                possession_is_real=match_dominance.get("possessionSource")
                in {"fixture_stats", "h2h_fixture_stats"},
                possession_source=match_dominance.get("possessionSource"),
                opponent_allowed_average=opp_allowed_avg,
                opponent_allowed_samples=int(
                    (position_comp_data or {}).get("sampleSize")
                    or (real_bayes or {}).get("opponentAllowedSamples")
                    or 0
                ),
                position_comparable_samples=int(
                    (position_comp_data or {}).get("sampleSize") or 0
                )
                if (position_comp_data or {}).get("projectionEligible")
                else 0,
                game_script=prediction.get("gameScript"),
                lineup=prediction.get("lineup"),
                history_values=_tactical_history_values,
                press_intensity=_press_intensity,
            )
            prediction["matchScript"] = prediction["tacticalIntelligence"].get("matchScript")
            prediction["positionalReality"] = prediction["tacticalIntelligence"].get("positionalReality")
            prediction["tacticalIntelligence"]["tacticalConclusion"] = build_tactical_conclusion(
                player_name=req.playerName,
                role=display_role or player_role,
                prop_type=req.propType,
                opponent=req.opponentName,
                player_history=(historical_data.get("h2hPlayerStats") or {}).get("opponentHitRate") or {},
                cohort=position_comp_data or {},
            )
            prediction["tacticalIntelligence"]["playerOpponentHistory"] = (
                historical_data.get("h2hPlayerStats") or {}
            ).get("opponentHitRate")
            prediction["tacticalIntelligence"]["positionCohort"] = position_comp_data
            _ti_player = prediction["tacticalIntelligence"].setdefault("player", {})
            # Role metadata always comes from the final observed-role resolver
            # and should be updated unconditionally.
            _ti_player.update({
                "role": display_role or player_role or None,
                "roleSource": (
                    _selection_role_source
                    if _selection_role_is_trusted and _lineup_status != "confirmed"
                    else _observed_role.get("source") if _observed_role else None
                ),
                "roleConfidence": (
                    req.roleConfidenceOverride
                    if _selection_role_is_trusted and _lineup_status != "confirmed"
                    else _observed_role.get("confidence") if _observed_role else None
                ),
                "roleEvidence": (
                    _selection_role_evidence
                    if _selection_role_is_trusted and _lineup_status != "confirmed"
                    else _observed_role.get("evidence", []) if _observed_role else []
                ),
                "roleSampleSize": _observed_role.get("sampleSize", 0) if _observed_role else 0,
            })
            # Position and its provenance are only overwritten when the
            # API-Football observation actually supplied a value.
            if display_position:
                _ti_player["position"] = display_position
                _ti_player["positionSource"] = (
                    _observed_role.get("source") if _observed_role else None
                )

            _tactical_h2h = historical_data.get("h2hPlayerStats") or {}
            _team_pass_values = [
                float(row.get("totalPasses"))
                for row in (team_fixture_stats or [])
                if isinstance(row, dict)
                and row.get("totalPasses") is not None
            ]
            _team_pass_average = (
                sum(_team_pass_values) / len(_team_pass_values)
                if _team_pass_values
                else None
            )
            _tactical_player_history = prediction.get("playerGameLogs") or historical_data.get("playerGameLogs") or {}
            prediction["tacticalBreakdown"] = build_tactical_explanation({
                "playerName": req.playerName,
                "teamName": player_team_display,
                "opponentName": req.opponentName,
                "venue": player_venue,
                "propType": req.propType,
                "position": specific_position or player_position,
                "role": display_role or player_role,
                "line": req.line,
                "projectedValue": prediction.get("projectedValue"),
                "recommendation": prediction.get("recommendation"),
                "pOver": (real_bayes or {}).get("pOver"),
                "pUnder": (real_bayes or {}).get("pUnder"),
                "seasonAverage": (real_bayes or {}).get("priorMean"),
                "venueAverage": venue_avg,
                "recentAverage": _tactical_player_history.get("rawAvg"),
                "expectedPossession": match_dominance.get("expectedPoss"),
                "opponentExpectedPossession": match_dominance.get("oppExpectedPoss"),
                "possessionSource": match_dominance.get("possessionSource"),
                "teamPassAverage": _team_pass_average,
                "pressureResponse": _pressure_response,
                "pressIntensity": _press_intensity,
                "gameScript": prediction.get("gameScript"),
                "positionCohort": position_comp_data,
                "h2h": _tactical_h2h,
                "uncertaintyBand": prediction.get("range80") or prediction.get("confidenceInterval"),
                "limitations": (
                    (prediction.get("tacticalIntelligence") or {}).get("limitations")
                    if isinstance(prediction.get("tacticalIntelligence"), dict)
                    else None
                ),
            })
        except Exception as _tactical_refresh_err:
            print(f"[TACTICAL INTELLIGENCE] scenario refresh failed: {_tactical_refresh_err}")

        # Attach player disambiguation candidates when the name was ambiguous
        if _player_candidates:
            prediction["playerCandidates"] = _player_candidates

        # Save to MongoDB
        prediction["_created"] = datetime.now(timezone.utc).isoformat()
        prediction["_request"] = req.model_dump()
        if match_odds and match_odds.get("fixtureId"):
            prediction["_request"]["fixtureId"] = match_odds["fixtureId"]

        # Attach match stat data for frontend heat maps/visualizations
        if team_fixture_stats:
            prediction["teamMatchStats"] = team_fixture_stats
        if opponent_fixture_stats:
            prediction["opponentMatchStats"] = opponent_fixture_stats
        if req.sport == "soccer" and matchup_volume:
            prediction["matchupVolume"] = matchup_volume
        if historical_data.get("h2hPlayerStats"):
            prediction["h2hPlayerStats"] = historical_data["h2hPlayerStats"]
        if position_comp_data:
            prediction["positionComparison"] = position_comp_data
        if historical_data.get("playerGameLogs"):
            prediction["playerGameLogs"] = historical_data["playerGameLogs"]
        elif player_game_logs:
            # Safety net: historical_data path missed — rebuild from final player_game_logs
            _pgl_target_map = {
                "pass_attempts": "passes_total", "shots": "shots_total", "shots_on_target": "shots_on",
                "tackles": "tackles_total", "key_passes": "passes_key",
                "saves": "goals_saves", "goalie_saves": "goals_saves",
                "interceptions": "tackles_interceptions", "blocks": "tackles_blocks",
                "dribbles": "dribbles_attempts", "fouls_drawn": "fouls_drawn",
                "crosses": "passes_crosses", "clearances": "tackles_clearances",
                "goals": "goals_total", "assists": "goals_assists",
            }
            _pgl_tf = _pgl_target_map.get(req.propType, "passes_total")
            _pgl_vals = [g.get(_pgl_tf) for g in player_game_logs if g.get(_pgl_tf) is not None]
            _pgl_home = [v for g, v in zip(player_game_logs, _pgl_vals) if g.get("venue") == "home" and g.get(_pgl_tf) is not None]
            _pgl_away = [v for g, v in zip(player_game_logs, _pgl_vals) if g.get("venue") == "away" and g.get(_pgl_tf) is not None]
            _pgl_last10 = sorted(
                player_game_logs,
                key=lambda g: g.get("date", ""),
                reverse=True,
            )[:10]
            _pgl_tp_home = [
                float(g["teamPossession"]) for g in _pgl_last10
                if g.get("venue") == "home" and g.get("teamPossession") is not None
            ]
            _pgl_tp_away = [
                float(g["teamPossession"]) for g in _pgl_last10
                if g.get("venue") == "away" and g.get("teamPossession") is not None
            ]
            _pgl_summary = {
                "games": player_game_logs,
                "allGames": player_game_logs,
                "targetProp": req.propType,
                "sampleSize": len(_pgl_vals),
                "last10Count": len(_pgl_last10),
                "tpHomeAvg": round(sum(_pgl_tp_home) / len(_pgl_tp_home), 1) if _pgl_tp_home else None,
                "tpAwayAvg": round(sum(_pgl_tp_away) / len(_pgl_tp_away), 1) if _pgl_tp_away else None,
                "tpHomeCount": len(_pgl_tp_home),
                "tpAwayCount": len(_pgl_tp_away),
                "possessionStatus": (
                    "verified"
                    if player_game_logs
                    and len(_pgl_tp_home) + len(_pgl_tp_away) == len(player_game_logs)
                    else "partial"
                    if _pgl_tp_home or _pgl_tp_away
                    else "unavailable"
                ),
                "possessionAvailableGames": sum(
                    1
                    for _pgl_log in player_game_logs
                    if _pgl_log.get("teamPossession") is not None
                    and _pgl_log.get("opponentPossession") is not None
                ),
            }
            _pgl_minutes = [
                g.get("minutes")
                for g in player_game_logs
                if isinstance(g.get("minutes"), (int, float))
                and g.get("minutes") > 0
            ]
            if _pgl_minutes:
                _pgl_summary["avgMinutes"] = round(
                    sum(_pgl_minutes) / len(_pgl_minutes), 1
                )
                _pgl_summary["avgMinutesPerMatch"] = _pgl_summary["avgMinutes"]
            if _pgl_vals:
                _pgl_summary["rawAvg"] = round(sum(_pgl_vals) / len(_pgl_vals), 2)
            if _pgl_home:
                _pgl_summary["homeAvg"] = round(sum(_pgl_home) / len(_pgl_home), 2)
            if _pgl_away:
                _pgl_summary["awayAvg"] = round(sum(_pgl_away) / len(_pgl_away), 2)
            _pgl_venue_sample = (
                sum(
                    1
                    for _pgl_log in player_game_logs
                    if _pgl_log.get("venue") == player_venue
                    and _pgl_log.get(_pgl_tf) is not None
                )
                if req.sport == "soccer" and player_venue
                else None
            )
            _pgl_summary["venueHistory"] = {
                "selectedVenue": player_venue,
                "target": _VENUE_HISTORY_TARGET,
                "verifiedSampleSize": _pgl_venue_sample,
                "status": (
                    "sufficient"
                    if req.sport != "soccer"
                    or not player_venue
                    or (_pgl_venue_sample or 0) >= _VENUE_HISTORY_TARGET
                    else "full_history_fallback"
                ),
                "fallback": (
                    "full_verified_history"
                    if req.sport == "soccer"
                    and player_venue
                    and (_pgl_venue_sample or 0) < _VENUE_HISTORY_TARGET
                    else None
                ),
            }
            if _pgl_vals and req.line:
                _pgl_over = sum(1 for v in _pgl_vals if v > req.line)
                _pgl_under = sum(1 for v in _pgl_vals if v < req.line)
                _pgl_push = len(_pgl_vals) - _pgl_over - _pgl_under
                _pgl_summary["hitRates"] = {
                    "overHits": _pgl_over, "underHits": _pgl_under,
                    "pushHits": _pgl_push,
                    "overPct": round(_pgl_over / len(_pgl_vals) * 100, 1),
                    "underPct": round(_pgl_under / len(_pgl_vals) * 100, 1),
                    "total": len(_pgl_vals),
                }
            prediction["playerGameLogs"] = _pgl_summary
        if historical_data.get("competitionContext"):
            prediction["competitionContext"] = historical_data["competitionContext"]
            print(f"[SAFETY NET] playerGameLogs rebuilt from {len(player_game_logs)} logs for {req.playerName}")
        if gk_formula_data:
            prediction["gkFormula"] = gk_formula_data
        # positionComparison stored but not surfaced directly; used for opponent profile below

        # ── OPPONENT DEFENSIVE PROFILE ───────────────────────────────────────
        # Derived from position-comparison data already fetched above.
        # Quantifies how many of this stat the opponent allows per game to
        # same-position players, versus the player's own season average.
        try:
            _pcd = position_comp_data if isinstance(position_comp_data, dict) else {}
            _pcd_players = _pcd.get("players") or []
            _pcd_n = int(_pcd.get("sampleSize") or len(_pcd_players) or 0)
            _pcd_avg = None
            if _pcd.get("avgStatValue") is not None:
                _pcd_avg = float(_pcd["avgStatValue"])
            elif _pcd_players:
                _pcd_vals = [p.get("statValue") for p in _pcd_players if p.get("statValue") is not None]
                if _pcd_vals:
                    _pcd_avg = round(sum(_pcd_vals) / len(_pcd_vals), 1)
            if _pcd_avg is not None and _pcd_n >= 2:
                _player_s_avg = wave2_supplement.get("playerGameLogs", {}).get("rawAvg")
                _odf_delta_pct = None
                _odf_favorable = None
                if _player_s_avg and float(_player_s_avg) > 0:
                    _odf_delta_pct = round((float(_pcd_avg) / float(_player_s_avg) - 1) * 100, 1)
                    _odf_favorable = _odf_delta_pct > 0
                prediction["opponentDefensiveProfile"] = {
                    "opponent": req.opponentName,
                    "propType": req.propType,
                    "position": (prediction.get("player") or {}).get("position") or player_position or "",
                    "avgAllowed": _pcd_avg,
                    "sampleSize": _pcd_n,
                    "vsPlayerSeasonAvg": _odf_delta_pct,
                    "isFavorable": _odf_favorable,
                    "playerSeasonAvg": float(_player_s_avg) if _player_s_avg else None,
                }
        except Exception as _odf_err:
            print(f"[OPP DEF PROFILE] failed: {_odf_err}")

        # ── MANAGER CONTEXT ──────────────────────────────────────────────────────
        try:
            if _manager_ctx:
                prediction["managerContext"] = {
                    **_manager_ctx,
                    "logSplitInfo": _manager_split_info if "_manager_split_info" in vars() else {},
                    "possessionDrift": _manager_possession_drift if "_manager_possession_drift" in vars() else {},
                }
        except Exception as _mc_err:
            print(f"[MANAGER CONTEXT] failed: {_mc_err}")

        # ── FINAL PASS-PROJECTION CALIBRATION (SHADOW BY DEFAULT) ───────────
        # This is deliberately the only projection-calibration boundary.  It
        # runs after Bayesian Truth, H2H, scenario, odds, and route-level
        # guards, so the extractor measures the projection users actually saw.
        # PASS suppression and confidence calibration remain separate concerns.
        if (
            str(req.sport or "").lower() == "soccer"
            and req.propType in {"pass_attempts", "passes"}
            and str(prediction.get("recommendation") or "").lower() in {"over", "under"}
        ):
            try:
                from pass_projection_calibration import ensure_loaded, lookup

                await ensure_loaded(db, datetime.now(timezone.utc))
                _cal_position = (
                    prediction.get("player", {}).get("position")
                    or prediction.get("position")
                    or specific_position
                    or req.positionOverride
                    or ""
                )
                _cal_role = (
                    prediction.get("player", {}).get("role")
                    or prediction.get("role")
                    or player_role
                    or req.roleOverride
                    or ""
                )
                _cal_mean = prediction.get("projectedValue")
                _pass_calibration = lookup(
                    req.leagueId,
                    _cal_position,
                    _cal_role,
                    str(prediction.get("recommendation") or "").lower(),
                    float(_cal_mean) if _cal_mean is not None else None,
                )
                _cal_metrics = prediction.setdefault("bayesianMetrics", {})
                _cal_metrics["passProjectionCalibration"] = _pass_calibration

                if _pass_calibration.get("applied"):
                    _corrected_mean = round(
                        float(_cal_mean) * _pass_calibration["multiplier"], 1
                    )
                    _record_projection_factor(
                        "pass_projection_calibration",
                        "Learned pass-projection calibration",
                        _cal_mean,
                        _corrected_mean,
                        inputs={
                            "multiplier": _pass_calibration.get("multiplier"),
                            "bucket": _pass_calibration.get("bucket"),
                            "mode": _pass_calibration.get("mode"),
                        },
                        sample_size=_pass_calibration.get("n"),
                        multiplier=_pass_calibration.get("multiplier"),
                        reason="Applied only when the learned walk-forward calibration bucket is live.",
                    )
                    prediction["projectedValue"] = _corrected_mean
                    prediction["recommendation"] = (
                        "over" if _corrected_mean > req.line else "under"
                    )
                    _pass_calibration["appliedValue"] = _corrected_mean
                    _cal_metrics["passProjectionCalibration"] = _pass_calibration
                    print(
                        f"[PASS PROJECTION CAL] applied {_cal_mean} → {_corrected_mean} "
                        f"bucket={_pass_calibration.get('bucket')} "
                        f"n={_pass_calibration.get('n')}"
                    )
            except Exception as _pass_cal_err:
                prediction.setdefault("bayesianMetrics", {})[
                    "passProjectionCalibration"
                ] = {
                    "found": False,
                    "mode": os.environ.get("PASS_PROJECTION_CALIBRATION_MODE", "shadow"),
                    "applied": False,
                    "error": str(_pass_cal_err)[:240],
                }
                print(f"[PASS PROJECTION CAL] application failed: {_pass_cal_err}")

        # ── FINAL EDGE-GAP RECOMPUTE ─────────────────────────────────────
        # The engine computes edgeGap from its own posterior, but several
        # post-engine guards (dominance, consistency, GK risk) can still
        # mutate `prediction["projectedValue"]`. Refresh the surfaced
        # gap/band so the UI pills always reflect the FINAL projection.
        try:
            _final_pv = prediction.get("projectedValue")
            _final_line = prediction.get("line") or req.line
            if _final_pv is not None and _final_line and _final_line > 0:
                _gap_abs = round(float(_final_pv) - float(_final_line), 2)
                _gap_pct = round((_gap_abs / float(_final_line)) * 100, 1)
                if abs(_gap_pct) >= 20:
                    _band = "DEEP"
                elif abs(_gap_pct) >= 10:
                    _band = "STRONG"
                elif abs(_gap_pct) >= 5:
                    _band = "MODERATE"
                else:
                    _band = "THIN"
                bm = prediction.setdefault("bayesianMetrics", {})
                bm["edgeGapAbs"]  = _gap_abs
                bm["edgeGapPct"]  = _gap_pct
                bm["edgeGapBand"] = _band
        except Exception as _eg_err:
            print(f"[EDGE GAP RECOMPUTE] failed: {_eg_err}")

        # ── EMPIRICAL CONFIDENCE CALIBRATION ──────────────────────────
        # When the calibration table has enough data (n≥30 per bucket), replace
        # the displayed confidenceScore with the empirical hit rate — but ONLY
        # downward. We never boost confidence via calibration; we only correct
        # overconfidence. This preserves the Bayesian direction (over/under) while
        # making the displayed number match what the data actually shows.
        try:
            from confidence_calibration import calibrate as _calibrate
            _raw_conf = prediction.get("confidenceScore")
            if _raw_conf is not None:
                prediction.setdefault("rawConfidence", _raw_conf)
                _calibrated = _calibrate(
                    req.propType,
                    float(_raw_conf),
                    prediction.get("recommendation", "").upper() or None,
                    line=req.line,
                    league_id=req.leagueId,
                    position=prediction.get("player", {}).get("position") or prediction.get("position") or None,
                    role=prediction.get("player", {}).get("role") or prediction.get("role") or None,
                )
                if _calibrated is not None:
                    _calibrated_rounded = round(_calibrated)
                    prediction["calibratedConfidence"] = _calibrated_rounded
                    if _calibrated < float(_raw_conf):
                        # Empirical rate is lower than Bayesian → system is
                        # overconfident for this bucket. Correct the display.
                        prediction["confidenceScore"] = _calibrated_rounded
                        prediction["confidenceLevel"] = (
                            "Very High" if _calibrated_rounded >= 80
                            else "High"   if _calibrated_rounded >= 70
                            else "Medium" if _calibrated_rounded >= 55
                            else "Low"
                        )
                        print(
                            f"[CONF CALIB] {req.propType}: bayesian={_raw_conf}% "
                            f"→ empirical={_calibrated_rounded}% (overconfidence corrected)"
                        )
                    else:
                        print(
                            f"[CONF CALIB] {req.propType}: bayesian={_raw_conf}% "
                            f"empirical={_calibrated_rounded}% (no correction needed)"
                            + (f" [line={req.line}]" if req.line else "")
                        )
        except Exception as _calib_err:
            print(f"[CONF CALIB] application failed: {_calib_err}")

        # Opponent-specific samples are useful context, but 1–2 meetings are
        # not enough to justify Very High confidence. Keep the broad empirical
        # calibration intact and only cap the display when this unusually thin
        # matchup signal is present.
        try:
            _bm_final = prediction.get("bayesianMetrics") or {}
            _opp_sample_final = int(_bm_final.get("opponentAllowedSamples") or 0)
            _final_conf = float(prediction.get("confidenceScore") or 0)
            if (
                req.propType not in {"goals", "assists"}
                and 0 < _opp_sample_final < 3
                and _final_conf > 72
            ):
                prediction["confidenceScore"] = 72
                prediction["confidenceLevel"] = "High"
                prediction["tacticalAlerts"] = prediction.get("tacticalAlerts", []) + [
                    f"THIN OPPONENT SAMPLE: opponent-specific evidence uses only "
                    f"{_opp_sample_final} matchup(s); confidence capped at 72%."
                ]
                print(
                    f"[THIN OPP SAMPLE] {req.playerName}/{req.propType}: "
                    f"n={_opp_sample_final}, confidence {_final_conf:.0f}%→72%"
                )
        except Exception as _thin_sample_err:
            print(f"[THIN OPP SAMPLE] application failed: {_thin_sample_err}")

        # ── WORLD CUP CALIBRATION TRACKING ──────────────────────────────
        # The World Cup happens once every 4 years, so there's almost no settled-pick
        # history for "World Cup knockout" specifically — the calibration table above
        # is trained overwhelmingly on domestic-league picks. Flag it honestly rather
        # than let a WC pick display the same false precision as a league pick, and
        # keep it isolated (isWorldCup on the saved doc) so its own sample can build.
        try:
            if (req.leagueId or 0) == 1:
                prediction["isWorldCup"] = True
                _wc_conf = prediction.get("confidenceScore")
                if _wc_conf is not None and _wc_conf >= 75:
                    prediction["confidenceScore"] = 75
                    prediction["confidenceLevel"] = "High"
                prediction["tacticalAlerts"] = prediction.get("tacticalAlerts", []) + [
                    "World Cup pick: confidence is capped conservatively — there isn't enough "
                    "settled World Cup history yet to fully trust the model's calibration here."
                ]
        except Exception as _wc_err:
            print(f"[WC CALIB] err: {_wc_err}")

        # ── AUDITABLE MODEL FACTORS ─────────────────────────────────────────
        # Keep the explanation attached to the exact prediction that produced
        # the number.  This is deliberately built at the end of the pipeline,
        # after Bayesian Truth, calibration, thin-sample guards, and the final
        # matchup override have all run.  The mobile Analysis page renders all
        # ten factors, including unavailable inputs, so "not enough data" is
        # visible instead of being silently treated as neutral evidence.
        try:
            def _af_num(value):
                try:
                    return float(value) if value is not None and str(value).strip() != "" else None
                except (TypeError, ValueError):
                    return None

            def _af_avg(values):
                nums = [_af_num(v) for v in values]
                nums = [v for v in nums if v is not None]
                return round(sum(nums) / len(nums), 2) if nums else None

            def _af_factor(fid, title, status, summary, value=None, sample_size=None,
                           impact="context", direction="neutral", detail=""):
                return {
                    "id": fid,
                    "title": title,
                    "status": status,
                    "summary": summary,
                    "value": value,
                    "sampleSize": sample_size,
                    "impact": impact,
                    "direction": direction,
                    "detail": detail,
                }

            _af_logs = [g for g in (player_game_logs or []) if isinstance(g, dict)]
            _af_team_stats = [g for g in (team_fixture_stats or []) if isinstance(g, dict)]
            _af_opp_stats = [g for g in (opponent_fixture_stats or []) if isinstance(g, dict)]
            _af_h2h = [g for g in (h2h_player_stats or []) if isinstance(g, dict)]
            _af_target_map = {
                "pass_attempts": "passes_total", "passes": "passes_total",
                "shots": "shots_total", "shots_on_target": "shots_on",
                "tackles": "tackles_total", "key_passes": "passes_key",
                "shots_assisted": "passes_key", "saves": "goals_saves",
                "goalie_saves": "goals_saves", "interceptions": "tackles_interceptions",
                "blocks": "tackles_blocks", "dribbles": "dribbles_attempts",
                "crosses": "passes_crosses", "clearances": "tackles_clearances",
                "fouls_drawn": "fouls_drawn", "fouls_committed": "fouls_committed",
                "duels_won": "duels_won", "goals": "goals_total", "assists": "goals_assists",
            }
            _af_target = _af_target_map.get(req.propType, "passes_total")
            _af_values = [_af_num(g.get(_af_target)) for g in _af_logs]
            _af_values = [v for v in _af_values if v is not None]
            _af_h2h_values = [_af_num(g.get("targetStat")) for g in _af_h2h]
            _af_h2h_values = [v for v in _af_h2h_values if v is not None]

            # Team pass-volume history.  This is separate from the player's
            # own logs: a 65-pass player on a 500-pass team is a different
            # opportunity profile from a 65-pass player on a 300-pass team.
            _af_team_passes = [
                _af_num(g.get("totalPasses")) for g in _af_team_stats
                if _af_num(g.get("totalPasses")) is not None
            ]
            _af_team_pass_avg = _af_avg(_af_team_passes)
            _af_pass_prop = req.propType in {"pass_attempts", "passes", "key_passes", "crosses"}

            # Join player logs to team fixture totals by date to estimate the
            # player's share of team passes when both sides expose the field.
            _af_team_pass_by_date = {}
            for g in _af_team_stats:
                _d = str(g.get("date") or "")[:10]
                _p = _af_num(g.get("totalPasses"))
                if _d and _p and _p > 0:
                    _af_team_pass_by_date[_d] = _p
            _af_shares = []
            for g in _af_logs:
                _d = str(g.get("date") or "")[:10]
                _p = _af_num(g.get("passes_total"))
                _tp = _af_team_pass_by_date.get(_d)
                if _p is not None and _tp and _tp > 0:
                    _af_shares.append((_p / _tp) * 100)
            _af_share_avg = _af_avg(_af_shares)

            # Possession is represented as a range, not a falsely precise
            # single point.  Use observed team possession volatility when
            # available; otherwise expose a conservative uncertainty band.
            _af_poss_obs = []
            for g in _af_team_stats:
                raw_poss = g.get("possession")
                if isinstance(raw_poss, str):
                    raw_poss = raw_poss.replace("%", "").strip()
                val = _af_num(raw_poss)
                if val is not None and 0 < val < 100:
                    _af_poss_obs.append(val)
            _af_expected_poss = _af_num((match_dominance or {}).get("expectedPoss"))
            if _af_expected_poss is None:
                _af_expected_poss = _af_num(
                    ((prediction.get("matchupOverview") or {}).get("expectedPossession") or {}).get(
                        "home" if prediction.get("isHome") else "away"
                    )
                )
            _af_poss_std = None
            if len(_af_poss_obs) >= 3:
                try:
                    _af_poss_std = float(stats_mod.stdev(_af_poss_obs))
                except Exception:
                    _af_poss_std = None
            _af_range_width = max(4.0, min(12.0, (_af_poss_std or 6.0)))
            _af_poss_range = (
                [round(max(0, _af_expected_poss - _af_range_width), 1),
                 round(min(100, _af_expected_poss + _af_range_width), 1)]
                if _af_expected_poss is not None else None
            )
            _af_real_poss = bool((match_dominance or {}).get("hasRealPossData"))

            _af_lineup = prediction.get("lineup")
            _af_lineup_status = (_af_lineup or {}).get("status") if isinstance(_af_lineup, dict) else None
            _af_role = (
                (prediction.get("player") or {}).get("role")
                or prediction.get("role")
                or locals().get("player_role")
                or ""
            )
            _af_position = (
                (prediction.get("player") or {}).get("position")
                or prediction.get("position")
                or locals().get("player_position")
                or ""
            )

            _af_game_situation = game_situation if isinstance(game_situation, dict) else {}
            _af_game_script = prediction.get("gameScript") or {}
            _af_event_warning = (
                "Pre-match estimate only: an early goal, red card, or substitution can change the pace and role."
            )
            _af_comp = {
                "leagueId": req.leagueId,
                "league": (prediction.get("matchContext") or {}).get("league") or None,
                "venue": prediction.get("venue") or req.venue,
                "opponentTier": prediction.get("currentOppTier"),
                "opponentRank": prediction.get("currentOppRank"),
                "fixtureId": prediction.get("fixtureId") or (match_odds or {}).get("fixtureId"),
            }
            _af_missing = []
            for _k, _v in {
                "fixture": _af_comp.get("fixtureId"),
                "possession": _af_expected_poss if _af_real_poss else None,
                "playerHistory": len(_af_values),
                "opponentHistory": len(_af_h2h_values),
                "lineup": _af_lineup_status,
                "teamPassVolume": _af_team_pass_avg if _af_pass_prop else True,
            }.items():
                if _v is None or _v == 0 or _v == "":
                    _af_missing.append(_k)

            _af_opponent_n = len(_af_h2h_values)
            _af_comparable_n = (
                int((position_comp_data or {}).get("sampleSize") or 0)
                if (position_comp_data or {}).get("projectionEligible")
                else 0
            )
            _af_history_status = "applied" if len(_af_values) >= 3 else ("warning" if _af_values else "unavailable")
            _af_opp_status = "applied" if (_af_opponent_n >= 3 or _af_comparable_n >= 3) else (
                "warning" if (_af_opponent_n or _af_comparable_n) else "unavailable"
            )
            _af_poss_status = "applied" if _af_real_poss and _af_expected_poss is not None else (
                "warning" if _af_expected_poss is not None else "unavailable"
            )
            _af_team_pass_status = "applied" if _af_team_pass_avg is not None else (
                "measured" if not _af_pass_prop else "unavailable"
            )
            _af_share_status = "applied" if _af_share_avg is not None else (
                "warning" if _af_pass_prop else "measured"
            )
            _af_lineup_status_label = "applied" if _af_lineup_status in {"confirmed", "predicted"} else (
                "warning" if _af_role or _af_position else "unavailable"
            )
            _af_script_status = "applied" if _af_game_script or _af_game_situation else "warning"
            _af_comp_status = "applied" if _af_comp.get("fixtureId") and _af_comp.get("leagueId") else "warning"
            _af_tactical_status = "applied" if _af_comparable_n >= 3 else (
                "warning" if _af_comparable_n else "unavailable"
            )
            _af_quality_gap = (match_dominance or {}).get("qualityGap") or {}
            _af_quality_status = (
                "applied" if _af_quality_gap.get("applied")
                else "warning" if _af_quality_gap.get("eligible")
                else "unavailable"
            )
            _af_quality_direction = _af_quality_gap.get("direction") or "neutral"
            _af_quality_delta = _af_quality_gap.get("deltaPct")
            _af_quality_summary = (
                f"{_af_quality_direction.title()} {_af_quality_delta:+.1f}% team-quality adjustment"
                if isinstance(_af_quality_delta, (int, float))
                else "Team-quality gap not applied"
            )

            # Evidence-quality is intentionally descriptive.  It never boosts
            # the model; it explains why confidence was capped or left alone.
            _af_applied_count = sum(
                1 for s in (
                    _af_history_status, _af_opp_status, _af_poss_status,
                    _af_team_pass_status, _af_share_status, _af_lineup_status_label,
                    _af_script_status, _af_comp_status, _af_tactical_status,
                ) if s == "applied"
            )
            _af_warning_count = sum(
                1 for s in (
                    _af_history_status, _af_opp_status, _af_poss_status,
                    _af_team_pass_status, _af_share_status, _af_lineup_status_label,
                    _af_script_status, _af_comp_status, _af_tactical_status,
                ) if s == "warning"
            )
            _af_quality_score = round(min(100, max(20, 45 + _af_applied_count * 5 - _af_warning_count * 3)))
            _af_quality_level = "high" if _af_quality_score >= 78 else ("medium" if _af_quality_score >= 58 else "low")
            _af_conf = _af_num(prediction.get("confidenceScore")) or 50
            _af_conf_cap = 72 if _af_opponent_n < 3 and _af_comparable_n < 3 else None
            _af_evidence_detail = (
                f"{_af_applied_count} of 9 evidence groups applied; "
                f"{_af_warning_count} need caution. Displayed confidence is {_af_conf:.0f}%."
            )
            if _af_conf_cap:
                _af_evidence_detail += " Opponent-specific evidence is thin, so confidence is capped conservatively."

            prediction["analysisFactors"] = [
                _af_factor(
                    "historical_depth", "Multi-season player history", _af_history_status,
                    f"{len(_af_values)} usable {_af_target.replace('_', ' ')} game logs",
                    {"games": len(_af_values), "avg": _af_avg(_af_values), "seasonsSearched": H2H_HISTORY_SEASONS},
                    len(_af_values), "projection", "neutral",
                    "Logs are filtered for usable stat evidence and minutes before entering the prior."
                ),
                _af_factor(
                    "opponent_history", "Opponent and comparable-player history", _af_opp_status,
                    f"{_af_opponent_n} direct H2H games · {_af_comparable_n} comparable matchups",
                    {"h2hAverage": _af_avg(_af_h2h_values), "h2hGames": _af_opponent_n, "comparableGames": _af_comparable_n},
                    _af_opponent_n + _af_comparable_n, "projection", "neutral",
                    "Direct H2H is weighted only when it has enough appearances; comparable position history is a fallback."
                ),
                _af_factor(
                    "possession_range", "Possession range and upside", _af_poss_status,
                    (f"Expected {_af_expected_poss:.1f}% possession; likely range "
                     f"{_af_poss_range[0]:.1f}–{_af_poss_range[1]:.1f}%") if _af_poss_range else "No verified possession range",
                    {"expected": _af_expected_poss, "range": _af_poss_range, "observations": len(_af_poss_obs),
                     "realData": _af_real_poss, "multiplier": (match_dominance or {}).get("multiplier")},
                    len(_af_poss_obs), "projection", "up" if (_af_expected_poss or 50) > 52 else ("down" if (_af_expected_poss or 50) < 48 else "neutral"),
                    "The range exposes uncertainty around the point estimate; it is not a guarantee of possession."
                ),
                _af_factor(
                    "team_pass_volume", "Team pass-volume environment", _af_team_pass_status,
                    f"Team averaged {_af_team_pass_avg:.1f} passes per match" if _af_team_pass_avg is not None else (
                        "Measured but not needed for this prop" if not _af_pass_prop else "Team pass totals unavailable"
                    ),
                    {"average": _af_team_pass_avg, "observations": len(_af_team_passes), "propSensitive": _af_pass_prop},
                    len(_af_team_passes), "projection" if _af_pass_prop else "context", "up" if _af_pass_prop and (_af_team_pass_avg or 0) >= 450 else "neutral",
                    "Team opportunity is separated from the player's own recent production."
                ),
                _af_factor(
                    "player_share", "Player share of team passes", _af_share_status,
                    f"Player averaged {_af_share_avg:.1f}% of team passes" if _af_share_avg is not None else (
                        "Player share unavailable from matching fixture totals" if _af_pass_prop else "Measured only for passing props"
                    ),
                    {"averagePct": _af_share_avg, "gamesJoined": len(_af_shares)},
                    len(_af_shares), "projection" if _af_pass_prop else "context", "up" if _af_share_avg is not None and _af_share_avg >= 8 else "neutral",
                    "Share is joined by fixture date; it is unavailable when provider data lacks team totals for the same match."
                ),
                _af_factor(
                    "availability_role", "Availability, lineup, and role", _af_lineup_status_label,
                    f"{_af_lineup_status or 'Lineup unavailable'} · {_af_position or 'position unknown'}"
                    f"{' · ' + _af_role if _af_role else ''}",
                    {"lineupStatus": _af_lineup_status, "position": _af_position, "role": _af_role,
                     "teamPlayers": len((((_af_lineup or {}).get("home") or {}).get("players") or [])) if isinstance(_af_lineup, dict) else 0},
                    None, "projection", "neutral",
                    "Confirmed or predicted lineup data can change expected minutes; role is kept separate from raw position."
                ),
                _af_factor(
                    "game_state", "Game-state and event scenarios", _af_script_status,
                    (_af_game_script.get("key_finding") or "Scenario model available; live match events are not known pre-match."),
                    {"gameScript": _af_game_script or None, "situation": _af_game_situation or None,
                     "earlyGoalProfile": locals().get("_fg_scenario_weights") or None,
                     "liveEventsAvailable": False},
                    None, "projection", "neutral", _af_event_warning
                ),
                _af_factor(
                    "competition_context", "Competition, venue, and opponent strength", _af_comp_status,
                    f"{_af_comp.get('league') or 'League ' + str(_af_comp.get('leagueId') or '?')} · "
                    f"{_af_comp.get('venue') or 'venue unknown'} · "
                    f"opponent {_af_comp.get('opponentTier') or 'tier unknown'}",
                    _af_comp, None, "context", "neutral",
                    "Fixture identity, venue, odds, and opponent tier are kept together to avoid mixing matches."
                ),
                _af_factor(
                    "team_quality_gap", "Team quality and game-control gap", _af_quality_status,
                    _af_quality_summary,
                    _af_quality_gap,
                    len(_af_quality_gap.get("signals") or []),
                    "projection",
                    _af_quality_direction,
                    _af_quality_gap.get("reason") or (
                        "Verified possession is corroboration only; it is not applied as a second multiplier."
                    ),
                ),
                _af_factor(
                    "tactical_similarity", "Tactical and role similarity", _af_tactical_status,
                    f"{_af_comparable_n} same-position opponent matchups" if _af_comparable_n else "No comparable tactical sample",
                    {"sampleSize": _af_comparable_n, "position": _af_position,
                     "role": _af_role, "formation": ((_af_lineup or {}).get("home") or {}).get("formation") if isinstance(_af_lineup, dict) else None},
                    _af_comparable_n, "projection", "neutral",
                    "Comparable history is weighted by position, venue, opponent, and available possession context."
                ),
                _af_factor(
                    "evidence_quality", "Evidence quality and confidence controls", "applied" if _af_quality_level != "low" else "warning",
                    f"{_af_quality_level.title()} evidence quality · {_af_conf:.0f}% displayed confidence",
                    {"score": _af_quality_score, "level": _af_quality_level, "appliedGroups": _af_applied_count,
                     "warningGroups": _af_warning_count, "confidence": _af_conf, "confidenceCap": _af_conf_cap,
                     "missingInputs": _af_missing},
                    _af_applied_count, "confidence", "down" if _af_conf_cap else "neutral", _af_evidence_detail
                ),
            ]
            # Keep the tactical packet in the auditable snapshot. It is
            # intentionally descriptive until its settled-pick performance is
            # validated out of sample.
            if prediction.get("tacticalIntelligence"):
                prediction["analysisFactors"].append(
                    _af_factor(
                        "tactical_intelligence",
                        "Tactical role, formation, and market context",
                        "applied" if prediction["tacticalIntelligence"].get("status") == "strong" else "warning",
                        (
                            f"{prediction['tacticalIntelligence'].get('player', {}).get('roleGroup') or 'role unknown'} · "
                            f"{prediction['tacticalIntelligence'].get('lineup', {}).get('formation') or 'formation unavailable'} "
                            f"vs {prediction['tacticalIntelligence'].get('lineup', {}).get('opponentFormation') or 'formation unavailable'}"
                        ),
                        prediction["tacticalIntelligence"],
                        prediction["tacticalIntelligence"].get("evidence", {}).get("positionComparableSamples"),
                        "context",
                        "neutral",
                        "Shadow tactical signals are visible for audit and explanation but do not move the projection until calibrated.",
                    )
                )
            _pressure_factor = prediction.get("pressureResponse") or {}
            if req.sport == "soccer" and req.propType in {"pass_attempts", "passes"}:
                _pressure_factor_status = (
                    "applied"
                    if _pressure_factor.get("status") == "classified"
                    else "warning"
                )
                _pressure_factor_label = _pressure_factor.get("label") or "Insufficient evidence"
                _pressure_factor_detail = (
                    "Shadow-only player response profile. It does not move the projection until "
                    "walk-forward validation supports a live adjustment."
                )
                prediction["analysisFactors"].append(
                    _af_factor(
                        "player_pressure_response",
                        "Player pressure-response profile",
                        _pressure_factor_status,
                        _pressure_factor_label,
                        _pressure_factor,
                        (
                            (_pressure_factor.get("highPressureSamples") or 0)
                            + (_pressure_factor.get("lowPressureSamples") or 0)
                        ),
                        "context",
                        "neutral",
                        _pressure_factor_detail,
                    )
                )
            prediction["modelInputSnapshot"] = {
                "capturedAt": datetime.now(timezone.utc).isoformat(),
                "fixture": {
                    "fixtureId": prediction.get("fixtureId") or (match_odds or {}).get("fixtureId"),
                    "teamId": prediction.get("fixtureTeamId") or actual_team_id,
                    "opponentId": prediction.get("fixtureOpponentId") or req.opponentId,
                    "venue": prediction.get("venue") or req.venue,
                    "leagueId": req.leagueId,
                },
                "request": {"playerId": req.playerId, "playerName": req.playerName,
                            "propType": req.propType, "line": req.line},
                "sampleCounts": {
                    "playerLogs": len(_af_logs), "teamFixtures": len(_af_team_stats),
                    "opponentFixtures": len(_af_opp_stats), "h2hPlayerGames": _af_opponent_n,
                    "comparableGames": _af_comparable_n, "possessionObservations": len(_af_poss_obs),
                    "teamPassObservations": len(_af_team_passes), "shareJoins": len(_af_shares),
                    "goalkeeperPoolRows": int((_gk_pool_prior or {}).get("poolRows") or 0),
                    "goalkeeperPoolPlayers": int((_gk_pool_prior or {}).get("poolPlayers") or 0),
                    "statsbombFixtureCovered": bool((statsbomb_enrichment or {}).get("available")),
                },
                "final": {
                    "projectedValue": prediction.get("projectedValue"),
                    "line": req.line,
                    "recommendation": prediction.get("recommendation"),
                    "confidenceScore": prediction.get("confidenceScore"),
                    "confidenceLevel": prediction.get("confidenceLevel"),
                    "expectedPossession": _af_expected_poss,
                    "lineupStatus": _af_lineup_status,
                    "gameScript": _af_game_script or None,
                    "teamQualityGap": (real_bayes or {}).get("teamQualityGap"),
                    "pressureResponse": prediction.get("pressureResponse"),
                    "goalkeeperPoolPrior": (real_bayes or {}).get("goalkeeperPoolPrior"),
                    "statsbombEnrichment": prediction.get("tacticalContext", {}).get("statsbombEnrichment"),
                    "tacticalIntelligence": prediction.get("tacticalIntelligence"),
                },
            }

            # ── DETERMINISTIC EVIDENCE-QUALITY GATE ─────────────────────────
            # The projection has already been calculated.  This final control
            # stream checks whether the confidence attached to it is supported
            # by independent, fixture-specific evidence.  It can only cap
            # confidence or suppress a thin unsupported edge; it never boosts
            # the projection and never treats missing data as zero evidence.
            try:
                from prediction_quality import (
                    evaluate_prediction_quality,
                    apply_prediction_quality_controls,
                )

                _quality = evaluate_prediction_quality(
                    prop_type=req.propType,
                    player_logs=player_game_logs,
                    h2h_logs=h2h_player_stats,
                    comparable_sample=(
                        int((position_comp_data or {}).get("sampleSize") or 0)
                        if (position_comp_data or {}).get("projectionEligible")
                        else 0
                    ),
                    team_fixture_stats=team_fixture_stats,
                    opponent_fixture_stats=opponent_fixture_stats,
                    match_dominance=match_dominance,
                    lineup_status=_af_lineup_status,
                    fixture_id=prediction.get("fixtureId") or (match_odds or {}).get("fixtureId"),
                    match_odds=match_odds,
                    position=_af_position,
                    role=_af_role,
                    role_evidence=role_evidence_packet,
                )
                _quality_before_conf = prediction.get("confidenceScore")
                _quality_before_rec = prediction.get("recommendation")
                apply_prediction_quality_controls(
                    prediction,
                    line=req.line,
                    quality=_quality,
                )
                _quality_after_conf = prediction.get("confidenceScore")
                _quality_after_rec = prediction.get("recommendation")
                if _quality_before_conf != _quality_after_conf:
                    _record_confidence_control(
                        "evidence_quality_gate",
                        "Evidence-quality confidence control",
                        _quality_before_conf,
                        _quality_after_conf,
                        "; ".join(_quality.get("capReasons") or [])
                        or "Confidence was limited by evidence quality.",
                    )
                if _quality_before_rec != _quality_after_rec:
                    _factor_ledger.append({
                        "id": "evidence_quality_decision",
                        "title": "Evidence-quality decision control",
                        "status": "applied",
                        "before": _quality_before_conf,
                        "after": _quality_after_conf,
                        "delta": None,
                        "direction": "neutral",
                        "multiplier": None,
                        "sampleSize": _quality.get("realPlayerLogCount"),
                        "inputs": {
                            "qualityScore": _quality.get("score"),
                            "edgePercent": _quality.get("edgePercent"),
                        },
                        "reason": prediction.get("passReason")
                        or "Thin edge suppressed because independent evidence was limited.",
                        "kind": "decision",
                    })
                _analysis_quality = next(
                    (item for item in prediction.get("analysisFactors", [])
                     if item.get("id") == "evidence_quality"),
                    None,
                )
                if _analysis_quality is not None:
                    _analysis_quality["status"] = (
                        "applied" if _quality.get("level") != "low" else "warning"
                    )
                    _analysis_quality["summary"] = (
                        f"{_quality.get('level', 'low').title()} evidence quality · "
                        f"{_quality.get('score', 0)}/100 · "
                        f"{_quality.get('realPlayerLogCount', 0)} real player logs"
                    )
                    _analysis_quality["value"] = _quality
                    _analysis_quality["sampleSize"] = _quality.get("realPlayerLogCount")
                    _analysis_quality["detail"] = (
                        "; ".join(_quality.get("capReasons") or [])
                        or "Evidence quality did not require a confidence cap."
                    )
                _snapshot_final = prediction.get("modelInputSnapshot", {}).get("final")
                if isinstance(_snapshot_final, dict):
                    _snapshot_final.update({
                        "recommendation": prediction.get("recommendation"),
                        "confidenceScore": prediction.get("confidenceScore"),
                        "confidenceLevel": prediction.get("confidenceLevel"),
                        "evidenceQuality": _quality,
                    })
            except Exception as _quality_err:
                # Quality controls are protective metadata and must never turn
                # a valid deterministic projection into a failed request.
                print(f"[EVIDENCE QUALITY] failed: {_quality_err}")
        except Exception as _af_err:
            # A diagnostic explanation must never make a valid prediction fail.
            print(f"[MODEL FACTORS] snapshot failed: {_af_err}")
            prediction["analysisFactors"] = []

        # ── FINAL PROJECTION LEDGER + DETERMINISTIC EXPLANATION ───────────────
        # This is intentionally the last model boundary.  The earlier
        # analysisFactors snapshot is evidence-oriented; factorLedger is the
        # ordered numeric audit trail used by the explanation model.
        try:
            _ledger_projection = next(
                (
                    item.get("after")
                    for item in reversed(_factor_ledger)
                    if item.get("after") is not None and item.get("kind") != "confidence"
                ),
                None,
            )
            _final_projection = prediction.get("projectedValue", req.line)
            if _ledger_projection != _ledger_num(_final_projection):
                _record_projection_factor(
                    "final_projection_lock",
                    "Final displayed projection",
                    _ledger_projection,
                    _final_projection,
                    inputs={"line": req.line},
                    reason="Captures any late guard or calibration change before the result is returned.",
                )
            else:
                _record_projection_factor(
                    "final_projection_lock",
                    "Final displayed projection",
                    _ledger_projection,
                    _final_projection,
                    status="measured",
                    inputs={"line": req.line},
                    reason="Final projection is locked for display and explanation.",
                )

            # A late pass calibration or hard guard can move the displayed
            # projection after the Bayesian Truth block refreshed pOver/pUnder.
            # Recompute the probabilities from the value the user actually
            # sees, using the same predictive standard deviation used earlier.
            # This keeps the ledger, bayesianMetrics, badge, and structured evidence on
            # one final numeric snapshot.
            _final_bm = prediction.setdefault("bayesianMetrics", {})
            _final_line_num = float(req.line) if req.line is not None else 0.0
            _final_pv_num = float(_final_projection) if _final_projection is not None else _final_line_num
            _final_std = max(
                float(_final_bm.get("posteriorStd") or 0),
                float(_final_bm.get("priorStd") or 0) * 0.55,
                abs(_final_pv_num) * 0.17,
            )
            if _final_std > 0 and _final_line_num:
                try:
                    import math as _final_math
                    _final_z = (_final_line_num - _final_pv_num) / _final_std
                    _final_p_under = round(
                        100 * (0.5 * (1 + _final_math.erf(_final_z / _final_math.sqrt(2)))),
                        1,
                    )
                    _final_p_over = round(100 - _final_p_under, 1)
                    _final_bm["pOver"] = _final_p_over
                    _final_bm["pUnder"] = _final_p_under
                    if str(prediction.get("recommendation") or "").upper() != "PASS":
                        _final_rec = "over" if _final_p_over >= _final_p_under else "under"
                        prediction["recommendation"] = _final_rec
                        _final_bm["recommendation"] = _final_rec
                except (TypeError, ValueError, ZeroDivisionError):
                    pass

            # Keep the distribution packet aligned with the final displayed
            # projection after every late guard/calibration stage. Preserve
            # the model's original band widths (important for count props)
            # and translate the bands to the final mean rather than exposing
            # stale bands around an earlier Bayesian snapshot.
            try:
                _old_r60 = _final_bm.get("range60")
                _old_r80 = _final_bm.get("range80") or _final_bm.get("confidenceInterval")
                def _translated_band(_old_band, _fallback_z):
                    if isinstance(_old_band, (list, tuple)) and len(_old_band) >= 2:
                        _lo, _hi = float(_old_band[0]), float(_old_band[1])
                        _center = (_lo + _hi) / 2.0
                        return [
                            round(max(0.0, _final_pv_num + _lo - _center), 1),
                            round(max(0.0, _final_pv_num + _hi - _center), 1),
                        ]
                    return [
                        round(max(0.0, _final_pv_num - _fallback_z * _final_std), 1),
                        round(max(0.0, _final_pv_num + _fallback_z * _final_std), 1),
                    ]
                _final_r60 = _translated_band(_old_r60, 0.841621)
                _final_r80 = _translated_band(_old_r80, 1.281552)
                _final_bm["mostLikelyValue"] = round(_final_pv_num, 1)
                _final_bm["range60"] = _final_r60
                _final_bm["range80"] = _final_r80
                _final_bm["confidenceInterval"] = _final_r80
                _final_bm.setdefault("distribution", {})
                _final_bm["distribution"].update({
                    "mostLikelyValue": round(_final_pv_num, 1),
                    "range60": _final_r60,
                    "range80": _final_r80,
                })
                prediction["mostLikelyValue"] = round(_final_pv_num, 1)
                prediction["range60"] = _final_r60
                prediction["range80"] = _final_r80
                prediction["confidenceInterval"] = _final_r80
                prediction["distribution"] = _final_bm["distribution"]
            except (TypeError, ValueError, ZeroDivisionError):
                pass

            # Reassert the display invariant after every late projection stage.
            # PASS is an intentional suppression state; OVER/UNDER must agree
            # with the final displayed projection relative to the line.
            if str(prediction.get("recommendation") or "").upper() != "PASS":
                prediction["recommendation"] = (
                    "over" if _final_pv_num > _final_line_num else "under"
                )
                _final_bm["recommendation"] = prediction["recommendation"]

            # Recompute edge and safety after all late projection stages. The
            # normal edge/safety block runs before pass-projection calibration,
            # so using its values here could describe an earlier projection or
            # earlier direction in the final ledger.
            _final_rec_upper = str(prediction.get("recommendation") or "").upper()
            _final_position = (
                prediction.get("player", {}).get("position")
                or prediction.get("position")
                or specific_position
                or ""
            )
            _final_conf_pre_safety = float(prediction.get("confidenceScore") or 50)
            if _final_rec_upper == "PASS":
                _final_safety = "AVOID"
                _final_hist_rate = None
                _final_hist_n = 0
            elif prediction.get("coinFlip"):
                _final_safety = "RISKY"
                _final_hist_rate = None
                _final_hist_n = 0
            else:
                _final_safety_data = _get_prop_safety(
                    req.propType,
                    _final_rec_upper,
                    league_id=req.leagueId,
                    position=_final_position,
                )
                _final_safety = (_final_safety_data or {}).get("safety", "RISKY")
                _final_hist_rate = (_final_safety_data or {}).get("hitRate")
                _final_hist_n = (_final_safety_data or {}).get("n", 0)

            _final_margin = abs(_final_pv_num - _final_line_num) if _final_line_num > 0 else 0
            _final_gap_pct = (
                abs(_final_pv_num - _final_line_num) / _final_line_num * 100
                if _final_line_num > 0 else 0
            )
            _final_market_dist = _final_gap_pct >= 35
            if _final_rec_upper == "PASS" or prediction.get("coinFlip") or _final_safety == "AVOID":
                _final_edge_rating = "NO EDGE"
                if _final_rec_upper == "PASS":
                    _final_edge_reason = "Evidence-quality control converted a thin or unsupported edge to PASS."
                elif prediction.get("coinFlip"):
                    _final_edge_reason = "Projection probabilities are too close to call."
                else:
                    _final_edge_reason = (
                        f"Historical {_final_rec_upper} safety is AVOID"
                        + (f" ({_final_hist_rate:.0f}% over {_final_hist_n} settled events)." if _final_hist_rate is not None else ".")
                    )
            elif _final_safety == "SAFE":
                _final_edge_rating = (
                    "SHARP EDGE" if _final_margin >= 5 and _final_conf_pre_safety >= 60
                    else "EDGE" if _final_margin >= 3 and _final_conf_pre_safety >= 55
                    else "MARGINAL" if _final_margin >= 2 else "NO EDGE"
                )
                _final_edge_reason = "Final projection gap and confidence clear the SAFE threshold." if _final_edge_rating != "NO EDGE" else "Final projection gap is below the SAFE threshold."
            elif _final_safety == "MODERATE":
                _final_edge_rating = (
                    "SHARP EDGE" if _final_margin >= 8 and _final_conf_pre_safety >= 65
                    else "EDGE" if _final_margin >= 5 and _final_conf_pre_safety >= 58
                    else "MARGINAL" if _final_margin >= 3 else "NO EDGE"
                )
                _final_edge_reason = "Final projection gap and confidence clear the MODERATE threshold." if _final_edge_rating != "NO EDGE" else "Final projection gap is below the MODERATE threshold."
            else:
                _final_edge_rating = (
                    "MARGINAL"
                    if (_final_margin >= 10 and _final_conf_pre_safety >= 70) or _final_market_dist
                    else "NO EDGE"
                )
                _final_edge_reason = "Large mathematical gap retained as a marginal read, but safety is not strong enough for an actionable edge." if _final_edge_rating == "MARGINAL" else "Risk and confidence controls do not support an actionable edge."
            if _final_market_dist and _final_edge_rating == "NO EDGE":
                _final_edge_rating = "MARGINAL"
                _final_edge_reason = "Large mathematical gap is retained as MARGINAL because the safety profile is not actionable."

            prediction["edgeRating"] = _final_edge_rating
            prediction["edgeRatingReason"] = _final_edge_reason
            prediction["safetyRating"] = _final_safety
            prediction["propHistoricalRate"] = _final_hist_rate
            prediction["propHistoricalN"] = _final_hist_n

            # Preserve the existing suppression policy, but apply it against
            # the final direction/rating if late calibration changed either.
            if _final_rec_upper != "PASS":
                if _final_safety == "AVOID" and _final_hist_rate is not None:
                    _final_cap = max(50, round(_final_hist_rate))
                    if float(prediction.get("confidenceScore") or 50) > _final_cap:
                        _record_confidence_control(
                            "final_safety_cap",
                            "Final safety confidence cap",
                            prediction.get("confidenceScore"),
                            _final_cap,
                            f"Final {_final_rec_upper} safety is AVOID at {_final_hist_rate:.1f}% "
                            f"over {_final_hist_n} settled events.",
                        )
                        prediction["confidenceScore"] = _final_cap
                        prediction["confidenceLevel"] = "Medium" if _final_cap >= 55 else "Low"
                elif _final_safety == "RISKY" and _final_hist_rate is not None:
                    _final_risky_conf = float(prediction.get("confidenceScore") or 50)
                    if _final_hist_rate < 50 and _final_risky_conf > 65:
                        _final_adj = max(55, _final_risky_conf - 5)
                        _record_confidence_control(
                            "final_risky_adjustment",
                            "Final risky-prop confidence adjustment",
                            _final_risky_conf,
                            _final_adj,
                            f"Final {_final_rec_upper} safety is RISKY at {_final_hist_rate:.1f}% "
                            f"over {_final_hist_n} settled events.",
                        )
                        prediction["confidenceScore"] = _final_adj
                        prediction["confidenceLevel"] = "High" if _final_adj >= 70 else "Medium"

            # Directional walk-forward guard must run after the final
            # recommendation reassertion and final safety suppression above.
            # The aggregate sport/prop Brier score hides the persistent
            # OVER/UNDER split found in the production replay. This caps only
            # weak OVER confidence; it never flips a side or changes UNDER.
            if _final_rec_upper == "OVER":
                try:
                    from calibration_alerts import get_directional_calibration_alert
                    _dir_alert = get_directional_calibration_alert(
                        str(getattr(req, "sport", "") or ""),
                        str(getattr(req, "propType", "") or ""),
                        "OVER",
                    )
                    if _dir_alert:
                        _dir_level = _dir_alert.get("alertLevel")
                        _dir_conf = float(prediction.get("confidenceScore") or 50)
                        _dir_rate = _dir_alert.get("hitRate")
                        _dir_n = int(_dir_alert.get("n") or 0)
                        if _dir_level == "AVOID" and _dir_conf > 60:
                            prediction["confidenceScore"] = 60
                            prediction["confidenceLevel"] = "Medium"
                            prediction["directionalCalibrationApplied"] = {
                                "direction": "OVER",
                                "level": _dir_level,
                                "sampleSize": _dir_n,
                                "hitRate": _dir_rate,
                                "capApplied": 60,
                                "from": _dir_conf,
                            }
                            print(
                                f"[DIRECTIONAL OVER AVOID] {_dir_rate:.1f}% ({_dir_n}n): "
                                f"{_dir_conf:.0f}% → 60%"
                            )
                        elif _dir_level == "RISKY" and _dir_conf > 70:
                            _dir_adj = max(60, _dir_conf - 5)
                            if _dir_adj != _dir_conf:
                                prediction["confidenceScore"] = _dir_adj
                                prediction["confidenceLevel"] = (
                                    "High" if _dir_adj >= 70 else "Medium"
                                )
                                prediction["directionalCalibrationApplied"] = {
                                    "direction": "OVER",
                                    "level": _dir_level,
                                    "sampleSize": _dir_n,
                                    "hitRate": _dir_rate,
                                    "reduction": 5,
                                    "from": _dir_conf,
                                }
                                print(
                                    f"[DIRECTIONAL OVER RISKY] {_dir_rate:.1f}% ({_dir_n}n): "
                                    f"{_dir_conf:.0f}% → {_dir_adj}%"
                                )
                except Exception as _dir_err:
                    # Calibration is advisory; an unavailable refresh must
                    # never make an otherwise valid prediction fail.
                    print(f"[DIRECTIONAL CAL SUP] error: {_dir_err}")

            # Confidence is a separate control stream from projection. Keep it
            # explicit so the deterministic explanation can explain a PASS/RISKY/capped result without
            # implying the cap changed the math projection.
            _raw_conf_final = prediction.get("rawConfidence")
            _display_conf_final = prediction.get("confidenceScore", 50)
            if _raw_conf_final is not None and _ledger_num(_raw_conf_final) != _ledger_num(_display_conf_final):
                _record_confidence_control(
                    "final_confidence_control",
                    "Final confidence controls",
                    _raw_conf_final,
                    _display_conf_final,
                    "Displayed confidence includes empirical, sample-size, market-distance, and safety controls.",
                )

            _ledger_final = {
                "projectedValue": _ledger_num(_final_projection),
                "line": _ledger_num(req.line),
                "recommendation": str(prediction.get("recommendation") or "").upper(),
                "pOver": _ledger_num(_final_bm.get("pOver")),
                "pUnder": _ledger_num(_final_bm.get("pUnder")),
                "confidenceScore": _ledger_num(_display_conf_final),
                "confidenceLevel": prediction.get("confidenceLevel"),
                "edge": _ledger_num(abs(float(_final_projection) - float(req.line)))
                if _final_projection is not None and req.line is not None else None,
                "edgeRating": prediction.get("edgeRating"),
                "edgeRatingReason": prediction.get("edgeRatingReason"),
                "safetyRating": prediction.get("safetyRating"),
                "propHistoricalRate": _final_hist_rate,
                "propHistoricalN": _final_hist_n if _final_hist_n else None,
            }
            for _idx, _factor in enumerate(_factor_ledger, start=1):
                _factor["sequence"] = _idx
            _ledger_payload = {
                "version": "projection-ledger-v1",
                "factors": _factor_ledger,
                "final": _ledger_final,
            }
            _ledger_fingerprint = hashlib.sha256(
                json.dumps(_ledger_payload, sort_keys=True, separators=(",", ":"), default=str).encode()
            ).hexdigest()[:20]
            prediction["factorLedger"] = _ledger_payload
            prediction["factorLedgerVersion"] = "projection-ledger-v1"
            prediction["factorLedgerFingerprint"] = _ledger_fingerprint

            # The ledger is now complete. Gemini may only write the long-form
            # AI tactical explanation removed — prop hit rates shown on frontend

            # Rebuild the authoritative math footer after all late calibration
            # and guard stages. This prevents a correct structured narrative from being
            # followed by stale pre-calibration numbers.
            _final_bm = prediction.get("bayesianMetrics") or {}
            _fpv = prediction.get("projectedValue", req.line)
            _frec = str(prediction.get("recommendation") or "PASS").upper()
            _fline = req.line
            _fpover = _final_bm.get("pOver", 50) or 50
            _fpunder = _final_bm.get("pUnder", 50) or 50
            _fpwin = max(_fpover, _fpunder)
            _fedge = abs(float(_fpv) - float(_fline)) if _fpv is not None and _fline is not None else 0
            _fp_s = str(int(_fpv)) if isinstance(_fpv, (int, float)) and _fpv == int(_fpv) else f"{_fpv}"
            _fl_s = str(int(_fline)) if isinstance(_fline, (int, float)) and _fline == int(_fline) else f"{_fline}"
            _final_math_footer = (
                f"**Final Math Ledger**\n"
                f"Projection: {_fp_s} | Line: {_fl_s} | Recommendation: {_frec} | Edge: {_fedge:.1f}\n"
                f"P(OVER): {_fpover:.1f}% | P(UNDER): {_fpunder:.1f}% | "
                f"Confidence: {_display_conf_final:.0f}% ({prediction.get('confidenceLevel', 'Medium')})\n"
                f"Ledger: {_ledger_fingerprint} | Factors recorded: {len(_factor_ledger)}"
            )
            # Keep the math ledger structured for the UI/owner audit trail.
            # Do not append it to the customer paragraph; that was the source
            # of the oversized Turner-style explanation.
            prediction["finalMathLedgerText"] = _final_math_footer

            # The deterministic narrative is assembled before the evidence
            # quality and safety gates finish.  Rewrite only confidence labels
            # here so the customer-facing prose cannot disagree with the
            # authoritative final ledger.  Probability statements such as
            # "P(UNDER): 87%" are intentionally left alone.
            _td_final = prediction.get("tacticalBreakdown")
            if isinstance(_td_final, str) and _td_final:
                prediction["tacticalBreakdown"] = _reconcile_deterministic_confidence(
                    _td_final,
                    float(_display_conf_final),
                    str(prediction.get("confidenceLevel") or "Medium"),
                )

            # Recompute landing-band probabilities from the same final mean
            # and standard deviation used for P(OVER)/P(UNDER).  Earlier
            # packets could carry stale probabilities after a late projection
            # shift, producing impossible output such as P(OVER)=13.6% while
            # the 66+ landing band said 53.2%.
            _dist_final = _final_bm.get("distribution")
            _landing_source_center = (
                _dist_final.get("mostLikelyValue")
                if isinstance(_dist_final, dict)
                else None
            )
            _old_bands = (
                _dist_final.get("landingBands")
                if isinstance(_dist_final, dict)
                else None
            )
            if isinstance(_old_bands, list) and _old_bands and _final_std > 0:
                _new_bands = _recompute_landing_bands(
                    _old_bands,
                    _final_pv_num,
                    _final_line_num,
                    _final_std,
                    _landing_source_center,
                )
                if _new_bands:
                    _dist_final["landingBands"] = _new_bands
                    _final_bm["distribution"] = _dist_final
                    prediction["distribution"] = _dist_final
        except Exception as _ledger_err:
            # The ledger is diagnostic/explanatory and must never take down a
            # valid math prediction. Keep the explicit math source marker.
            print(f"[FINAL LEDGER] failed: {_ledger_err}")
            prediction["aiSource"] = "model"
            prediction["aiPending"] = False

        # Venue provenance — write to the prediction dict before normalization so
        # the saved snapshot carries a complete audit trail regardless of whether
        # the caller consumes the top-level field or the matchupOverview sub-doc.
        #   resolvedVenue:        final venue used by the entire pipeline ("home"/"away")
        #   venueSource:          "fixture" if playerIsHome was confirmed by API-Football;
        #                         "request" if no fixture was available (unverified)
        #   venueWasRepaired:     True only when user input contradicted the fixture
        #   originalRequestVenue: the user-supplied value that was overridden
        _pv_resolved = locals().get("player_venue") or prediction.get("venue") or req.venue
        if _pv_resolved:
            prediction["resolvedVenue"] = _pv_resolved
        prediction["venueSource"] = locals().get("_venue_source", "request")
        if locals().get("_venue_was_repaired"):
            prediction["venueWasRepaired"] = True
            prediction["originalRequestVenue"] = (
                locals().get("_original_request_venue") or req.venue
            )

        # The player stats packet is keyed by the verified player ID.  Prefer
        # its provider first/last name over a stale OCR/search label before
        # the response crosses into saved picks and share-card rendering.
        _verified_player = (
            player_stats.get("player")
            if isinstance(player_stats, dict)
            else None
        )
        _verified_player_name = _provider_full_player_name(_verified_player)
        if _verified_player_name:
            prediction["canonicalPlayerName"] = _verified_player_name
            prediction["playerName"] = _verified_player_name
            _prediction_player = prediction.get("player")
            if isinstance(_prediction_player, dict):
                prediction["player"] = {
                    **_prediction_player,
                    "id": _prediction_player.get("id") or req.playerId,
                    "name": _verified_player_name,
                    "firstname": _verified_player.get("firstname"),
                    "lastname": _verified_player.get("lastname"),
                }

        prediction = _normalize_prediction_identity(prediction, req)
        # Reconcile the evidence verdict with the final displayed direction.
        # Late safety/calibration gates can change OVER/UNDER; the cohort must
        # describe that final saved recommendation, without changing it.
        if isinstance(prediction.get("positionComparison"), dict):
            _final_cohort = prediction["positionComparison"]
            _final_cohort["verdict"] = position_cohort_verdict(
                _final_cohort,
                prediction.get("recommendation"),
                req.line,
            )
        prediction["_ts"] = datetime.now(timezone.utc).isoformat()
        safe_prediction = _json_safe_prediction(prediction)
        try:
            # Persistence is analytics-only. Atlas quota/network stalls must
            # never consume the user-facing prediction response budget.
            await aio.wait_for(
                db.predictions.insert_one(safe_prediction),
                timeout=1.5,
            )
        except Exception as _persist_err:
            # Atlas can hard-block writes when the free-tier cluster reaches
            # its storage limit. Persistence is useful for analytics, but it
            # must not turn an already-computed prediction into a 500.
            # The background cleanup loop prunes stale cache data every 6 hours;
            # the owner can also trigger a manual cleanup via /api/admin/trigger-cleanup.
            print(
                f"[PREDICTION PERSISTENCE] skipped; returning computed prediction: "
                f"{type(_persist_err).__name__}: {_persist_err}"
            )
        safe_prediction.pop("_id", None)
        if access == "Owner":
            try:
                await aio.wait_for(
                    _attach_owner_prediction_media(safe_prediction, req.email),
                    timeout=1.0,
                )
            except Exception as _media_err:
                print(
                    f"[PREDICTION] owner media skipped within response budget: "
                    f"{type(_media_err).__name__}"
                )

        # ── Knowledge Base: fire-and-forget compilation ───────────────────────
        # Compile team style + player profile from this prediction's data so the
        # KB stays warm for Stage 2 prompt injection.  Any failure is logged and
        # swallowed — it must never affect the prediction response.
        try:
            from knowledge_base import fire_and_forget_compile
            _kb_opp_id = (
                safe_prediction.get("opponentId")
                or safe_prediction.get("fixtureOpponentId")
                or 0
            )
            await fire_and_forget_compile(
                player_id=req.playerId or 0,
                team_id=req.teamId or 0,
                league_id=req.leagueId or 0,
                opponent_id=int(_kb_opp_id) if _kb_opp_id else 0,
                season=req.season if hasattr(req, "season") and req.season else __import__("config").CURRENT_SEASON,
            )
        except Exception as _kb_err:
            print(f"[KB fire-and-forget] scheduling error: {_kb_err}")

        return safe_prediction
    except (json.JSONDecodeError, aio.TimeoutError):
        # Return a safe fallback deterministic model prediction
        return {
            "player": {"id": req.playerId, "name": req.playerName, "team": req.teamName, "position": "Unknown"},
            "opponent": req.opponentName,
            "propType": req.propType,
            "line": req.line,
            "projectedValue": req.line,
            "recommendation": "over",
            "confidenceScore": 50,
            "confidenceLevel": "Medium",
            "confidenceInterval": None,
            "recentSamples": [],
            "bayesianMetrics": {"priorMean": req.line, "momentumEffect": 0, "covariateAdjustment": 0, "reversalFlag": "stable"},
            "probabilityCurve": [],
            "reasoning": "Deterministic model returned an invalid format. Displaying fallback prediction.",
            "tacticalInsights": "",
            "explanation": "Fallback prediction due to deterministic model parsing error."
        }
    except HTTPException:
        raise  # Re-raise HTTPException directly (e.g., 400 for teamId=0)
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Prediction failed: {str(e)}")
    finally:
        reset_api_request_priority(_priority_token)


# ─────────────────────────────────────────────────────────────────────────


# ── Match Script endpoint ───────────────────────────────────────────────────
# Fires right after a player/match is identified, BEFORE the user enters a
# stat line. Fast, moneyline + odds-derived-possession classification — see
# match_script.py for the tier table and cross-check logic.
@router.get("/match-script")
async def match_script(teamId: int, opponentId: int, leagueId: int, isHome: bool,
                        teamName: str = "This team", opponentName: str = "Opponent",
                        leagueName: str = ""):
    from match_script import get_match_script
    try:
        if not teamId or not opponentId:
            return {"available": False, "noCleanScript": True, "primaryScript": None,
                    "isFavorable": False, "explanation": "Missing team data.",
                    "tacticalModifier": None, "expectedEffects": []}
        result = await get_match_script(
            team_id=teamId, opponent_id=opponentId, league_id=leagueId, is_home=isHome,
            team_name=teamName, opponent_name=opponentName, league_name=leagueName,
        )
        return result
    except Exception as e:
        print(f"[MATCH SCRIPT] error: {e}")
        return {"available": False, "noCleanScript": True, "primaryScript": None,
                "isFavorable": False, "explanation": "Could not classify this match right now.",
                "tacticalModifier": None, "expectedEffects": []}
