import json
import uuid
import unicodedata
import asyncio as aio
import traceback
from datetime import datetime, timezone, timedelta
from typing import Optional
from fastapi import APIRouter, HTTPException, Request

from config import db, CURRENT_SEASON, STAT_LAMBDA_MAP, NWSL_LEAGUE_ID, NWSL_SEASON
from models import (
    SavePickRequest, GetPicksRequest, DeletePickRequest,
    CorrectPickRequest, LiveUpdateRequest, SettlePicksRequest,
)
from utils import api_football_request
import cs2_client as _cs2_client
import wta_client as _wta_client
import httpx as _httpx
import os as _os
import time as _time_mod

# ── BDL live cooldown (shared across NBA/NHL/WNBA/NFL) ────────────────────
_bdl_live_last_attempt: dict[str, float] = {}
_bdl_live_locks:        dict[str, aio.Lock] = {}
BDL_LIVE_COOLDOWN_SEC = 120   # re-fetch every 2 min per pick

# Soccer count props where a positive final stat is conclusive evidence that
# the player participated.  API-Football's minutes field is occasionally
# stale/incorrect even when the fixture player row has the real stat.
_SOCCER_STAT_EVIDENCE_PROPS = frozenset({
    "pass_attempts", "passes", "crosses", "tackles", "key_passes",
    "shots", "shots_on_target", "interceptions", "blocks", "dribbles",
    "dribbles_success", "fouls_drawn", "fouls_committed", "clearances",
    "duels_won", "saves", "goals", "assists", "yellow_cards", "red_cards",
    "offsides",
})


def _has_soccer_stat_evidence(pick: dict) -> bool:
    """Return true when a settled soccer count stat proves participation."""
    if pick.get("sport", "soccer") != "soccer":
        return False
    if pick.get("propType") not in _SOCCER_STAT_EVIDENCE_PROPS:
        return False
    value = pick.get("actualValue")
    return isinstance(value, (int, float)) and value > 0

def _bdl_live_lock(pick_id: str) -> aio.Lock:
    if pick_id not in _bdl_live_locks:
        _bdl_live_locks[pick_id] = aio.Lock()
    return _bdl_live_locks[pick_id]

# auto_analyze_miss_background REMOVED — was draining AI tokens on every miss settlement

router = APIRouter(prefix="/api", tags=["picks"])


def _fixture_contains_teams(fixture: dict, team_id: int, opponent_id: int) -> bool:
    home_id = (fixture.get("teams", {}).get("home", {}) or {}).get("id")
    away_id = (fixture.get("teams", {}).get("away", {}) or {}).get("id")
    return (
        home_id and away_id
        and {int(home_id), int(away_id)} == {int(team_id), int(opponent_id)}
    )


async def _verify_soccer_fixture_context(
    fixture_id: int,
    team_id: int,
    opponent_id: int,
) -> None:
    """Warn (but never block) when the stored fixture doesn't match the requested matchup.

    The upstream predict pipeline already aligns the fixture before the result is
    returned, so a mismatch here is either a lookup/quota failure or an edge case
    (tournament players, re-used IDs).  Hard-rejecting causes more harm than good;
    log the anomaly for auditing and let the save proceed.
    """
    if not fixture_id or not team_id or not opponent_id:
        return
    try:
        fixtures = await api_football_request("fixtures", {"id": int(fixture_id)})
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning(
            "fixture_context_check: could not fetch fixture %s – %s", fixture_id, exc
        )
        return
    fixture = fixtures[0] if isinstance(fixtures, list) and fixtures else None
    if not fixture or not _fixture_contains_teams(fixture, team_id, opponent_id):
        import logging
        home_id = (fixture or {}).get("teams", {}).get("home", {}).get("id") if fixture else None
        away_id = (fixture or {}).get("teams", {}).get("away", {}).get("id") if fixture else None
        logging.getLogger(__name__).warning(
            "fixture_context_check: fixture %s teams (%s/%s) don't match pick teams (%s/%s) – saving anyway",
            fixture_id, home_id, away_id, team_id, opponent_id,
        )


# ── CS2 settle cooldown ────────────────────────────────────────────────────
# Tracks the last time we ATTEMPTED to settle each CS2 pick (by pickId).
# If the match wasn't ready yet, we wait CS2_SETTLE_COOLDOWN_SEC before
# trying again.  This prevents hammering the PandaScore API with hundreds
# of requests per minute when multiple users poll /api/picks/list.
#
# Key: pickId (str)  →  Value: last-attempt monotonic timestamp (float)
_cs2_settle_last_attempt: dict[str, float] = {}
_cs2_settle_locks:        dict[str, aio.Lock] = {}
CS2_SETTLE_COOLDOWN_SEC = 300   # 5 minutes between settle retries per pick

# ── WTA settle cooldown (mirrors CS2 pattern) ─────────────────────────────
_wta_settle_last_attempt: dict[str, float] = {}
_wta_settle_locks:        dict[str, aio.Lock] = {}
WTA_SETTLE_COOLDOWN_SEC = 300

def _wta_settle_lock(pick_id: str) -> aio.Lock:
    if pick_id not in _wta_settle_locks:
        _wta_settle_locks[pick_id] = aio.Lock()
    return _wta_settle_locks[pick_id]


@router.post("/picks/cs2/admin-manual-settle")
async def cs2_admin_manual_settle(payload: dict):
    """
    Admin endpoint: manually settle a CS2 pick by providing the actual value directly.
    Use when BDL hasn't ingested player stats yet but real-world results are known.
    Body: { "secret": "...", "pickId": "...", "email": "...", "actualValue": 12 }
    """
    import os as _os
    admin_secret = _os.environ.get("ADMIN_SECRET", "")
    if not admin_secret or payload.get("secret") != admin_secret:
        raise HTTPException(status_code=403, detail="forbidden")

    pick_id      = payload.get("pickId") or ""
    email        = (payload.get("email") or "").lower().strip()
    actual_value = payload.get("actualValue")

    if not pick_id or not email or actual_value is None:
        raise HTTPException(status_code=400, detail="pickId, email, actualValue required")

    pick = await db.picks.find_one({"pickId": pick_id, "email": email}, {"_id": 0})
    if not pick:
        raise HTTPException(status_code=404, detail="pick not found")
    if pick.get("status") == "settled":
        return {"ok": True, "alreadySettled": True}

    line        = float(pick.get("line", 0))
    rec         = pick.get("recommendation", "over")
    actual_value = float(actual_value)
    diff = actual_value - line
    if abs(diff) < 0.001:
        result_str = "push"
    elif rec == "over":
        result_str = "hit" if actual_value > line else "miss"
    else:
        result_str = "hit" if actual_value < line else "miss"

    hit_pct  = 100 if result_str == "hit" else (0 if result_str == "miss" else 50)
    now_iso  = datetime.now(timezone.utc).isoformat()
    settle_set = {
        "status":      "settled",
        "result":      result_str,
        "actualValue": actual_value,
        "hitPct":      hit_pct,
        "settledAt":   now_iso,
        "sport":       "cs2",
    }
    await db.picks.update_one(
        {"pickId": pick_id, "email": email},
        {"$set": settle_set}
    )
    return {
        "ok":          True,
        "settled":     True,
        "result":      result_str,
        "actualValue": actual_value,
        "line":        line,
        "player":      pick.get("playerName"),
    }


@router.post("/picks/cs2/admin-force-settle-all")
async def cs2_admin_force_settle_all(payload: dict):
    """
    Admin endpoint: force-settle ALL pending/live CS2 picks right now.
    Auth: requires ADMIN_SECRET env var match (no user session needed).
    Used for immediate post-deployment recovery.
    Body: { "secret": "..." }
    """
    import os as _os
    admin_secret = _os.environ.get("ADMIN_SECRET", "")
    if not admin_secret or payload.get("secret") != admin_secret:
        raise HTTPException(status_code=403, detail="forbidden")

    # Find all unsettled CS2 picks by sport OR by prop type prefix
    all_picks = await db.picks.find(
        {"$or": [
            {"status": {"$in": ["pending", "live"]}, "sport": "cs2"},
            {"status": {"$in": ["pending", "live"]},
             "propType": {"$regex": "^(map1_|maps_1_2_|map3_)"}},
        ]},
        {"_id": 0}
    ).to_list(500)

    results = []
    for pick in all_picks:
        pick_id = pick.get("pickId", "")
        email   = pick.get("email", "")
        team_id   = pick.get("teamId")
        player_id = pick.get("playerId")
        opp_name  = pick.get("opponentName", "")
        if not team_id or not player_id or not opp_name:
            results.append({"pickId": pick_id, "player": pick.get("playerName"), "status": "skip_missing_fields"})
            continue

        # Repair sport field in-memory so _settle_cs2_pick works
        pick = {**pick, "sport": "cs2", "email": email}

        lock = _cs2_settle_lock(pick_id)
        async with lock:
            fresh = await db.picks.find_one({"pickId": pick_id, "email": email}, {"_id": 0})
            if fresh and fresh.get("status") == "settled":
                results.append({"pickId": pick_id, "player": pick.get("playerName"), "status": "already_settled"})
                continue
            try:
                settled = await _settle_cs2_pick(pick)
            except Exception as e:
                results.append({"pickId": pick_id, "player": pick.get("playerName"), "status": f"error: {e}"})
                continue

        if settled:
            results.append({
                "pickId":      pick_id,
                "player":      pick.get("playerName"),
                "status":      "settled",
                "result":      settled.get("result"),
                "actualValue": settled.get("actualValue"),
                "line":        pick.get("line"),
                "matchScore":  settled.get("matchScore"),
            })
        else:
            results.append({"pickId": pick_id, "player": pick.get("playerName"),
                            "opp": opp_name, "status": "no_match_found"})

    settled_count = sum(1 for r in results if r.get("status") == "settled")
    return {"ok": True, "total": len(all_picks), "settled": settled_count, "picks": results}


@router.post("/picks/cs2/force-settle")
async def cs2_force_settle(payload: dict):
    """
    Diagnostic + manual settle path. Bypasses the 5-min cooldown for a single
    CS2 pick so the user can ask the system to look right now. Returns a
    structured payload explaining what happened (settled, not yet, or why
    the lookup failed) so the frontend can show a useful message.

    Body: { "email": "...", "token": "...", "pickId": "..." }
    Requires a valid session token AND the pick must belong to that user.
    Uses the per-pick lock so we can never double-settle concurrently with
    the pull-based settler on /picks/list.
    """
    email   = (payload.get("email") or "").lower().strip()
    token   = payload.get("token") or ""
    pick_id = payload.get("pickId") or ""
    if not email or not pick_id or not token:
        raise HTTPException(status_code=400, detail="email, token and pickId required")

    session = await db.sessions.find_one(
        {"email": email, "session_token": token}, {"_id": 0}
    )
    if not session:
        raise HTTPException(status_code=401, detail="Invalid session")

    pick = await db.picks.find_one({"email": email, "pickId": pick_id}, {"_id": 0})
    if not pick:
        raise HTTPException(status_code=404, detail="pick not found")
    if pick.get("sport") != "cs2":
        return {"ok": False, "reason": "not a CS2 pick"}
    if pick.get("status") == "settled":
        return {"ok": True, "alreadySettled": True, "result": pick.get("result")}

    # Reset cooldown so the next settle attempt runs immediately, then take
    # the per-pick lock so a concurrent /picks/list settler can't race us.
    lock = _cs2_settle_lock(pick_id)
    async with lock:
        # Re-check after acquiring the lock — another caller may have just
        # settled this pick while we were waiting on the lock.
        fresh = await db.picks.find_one({"email": email, "pickId": pick_id}, {"_id": 0})
        if fresh and fresh.get("status") == "settled":
            return {"ok": True, "alreadySettled": True, "result": fresh.get("result")}

        _cs2_settle_last_attempt[pick_id] = _time.monotonic()
        try:
            settled = await _settle_cs2_pick({**pick, "email": email})
        except Exception as e:
            traceback.print_exc()
            return {"ok": False, "reason": f"settle exception: {e}"}

    if not settled:
        return {
            "ok": False,
            "settled": False,
            "reason": "match not yet finished or opponent name didn't match any recent finished match — see backend logs ([CS2 SETTLE])",
            "teamId":       pick.get("teamId"),
            "playerId":     pick.get("playerId"),
            "opponentName": pick.get("opponentName"),
        }
    return {"ok": True, "settled": True, **settled}


def _cs2_settle_lock(pick_id: str) -> aio.Lock:
    """Return (or create) a per-pick asyncio.Lock — prevents concurrent settle calls."""
    if pick_id not in _cs2_settle_locks:
        _cs2_settle_locks[pick_id] = aio.Lock()
    return _cs2_settle_locks[pick_id]


import time as _time


def generate_tracking_id():
    """Generate a unique tracking ID for every pick."""
    return f"TRK-{uuid.uuid4().hex[:8].upper()}"


def normalize_player_name(name: str) -> str:
    """
    Canonical key for player name deduplication.
    Lowercases, strips accents/diacritics, trims whitespace.
    'Daley Blind' and 'D. Blind' still differ (we can't expand abbreviations
    without a DB lookup), but 'Adrian Semper' and 'A. Šemper' will both
    produce 'a. semper' / 'adrian semper' sharing the same last-name token
    used in dedup logic. Stored as playerNameKey alongside the original name.
    """
    if not name:
        return ""
    name = name.strip()
    name = unicodedata.normalize("NFD", name)
    name = "".join(c for c in name if unicodedata.category(c) != "Mn")
    return name.lower()


@router.post("/picks/save")
async def save_pick(req: SavePickRequest):
    session = await db.sessions.find_one({"email": req.email.lower(), "session_token": req.token}, {"_id": 0})
    if not session:
        raise HTTPException(status_code=401, detail="Invalid session")
    pick = req.pick
    pick_id = pick.get("id") or str(uuid.uuid4())[:8]
    tracking_id = generate_tracking_id()

    # Normalize propType for consistent storage
    raw_prop = pick.get("propType", "")
    normalized_prop = raw_prop.lower().replace("+", "_").replace(" ", "_").replace("-", "_")
    prop_label_map = {
        "pts_reb_ast": "pts_reb_ast",
        "3_pointers_made": "three_pointers",
        "3_point_fg_made": "three_pointers",
        "fg_made": "fgm", "ft_made": "ftm",
        "fg_attempted": "fga", "ft_attempted": "fta",
        "3pt_attempted": "tpa",
    }
    normalized_prop = prop_label_map.get(normalized_prop, normalized_prop)

    # Detect sport from pick payload
    _sport_raw = str(pick.get("sport", "soccer")).lower()
    if _sport_raw == "mlb":
        sport = "mlb"
    elif _sport_raw == "cs2":
        sport = "cs2"
    elif _sport_raw == "wta":
        sport = "wta"
    else:
        sport = "soccer"

    if sport == "soccer":
        _save_request = pick.get("_request") or {}
        _save_team_id = pick.get("teamId") or _save_request.get("teamId") or 0
        _save_opp_id = pick.get("opponentId") or _save_request.get("opponentId") or 0
        _save_fixture_id = pick.get("fixtureId") or _save_request.get("fixtureId") or 0
        await _verify_soccer_fixture_context(
            int(_save_fixture_id or 0),
            int(_save_team_id or 0),
            int(_save_opp_id or 0),
        )

    doc = {
        "pickId": pick_id,
        "trackingId": tracking_id,
        "email": req.email.lower(),
        "sport": sport,
        "playerId": pick.get("player", {}).get("id"),
        "playerName": pick.get("player", {}).get("name") or pick.get("playerName", ""),
        "playerNameKey": normalize_player_name(
            pick.get("player", {}).get("name") or pick.get("playerName", "")
        ),
        "teamName": pick.get("player", {}).get("team") or pick.get("teamName", ""),
        # New clients send the verified IDs at the top level; retain the
        # request-context fallback for legacy clients.
        "teamId": pick.get("teamId") or pick.get("_request", {}).get("teamId", 0),
        "opponentId": pick.get("opponentId") or pick.get("_request", {}).get("opponentId", 0),
        "opponentName": pick.get("opponent") or pick.get("opponentName", ""),
        # PASS is saved as a calibration observation.  passLeaning stores the
        # model's original direction so settlement can measure the avoided
        # outcome without presenting PASS as an actionable wager.
        "passLeaning": str(pick.get("passLeaning") or "").lower() or None,
        "passReason": pick.get("passReason") or None,
        "isCalibrationOnly": str(pick.get("recommendation") or "").upper() == "PASS",
        "leagueId": pick.get("leagueId") or pick.get("_request", {}).get("leagueId", 0),
        # World Cup picks are tracked separately for calibration — the tournament only
        # happens every 4 years so there's little settled-pick history to trust confidence
        # scores against; keep them isolated until their own sample builds up.
        "isWorldCup": pick.get("_request", {}).get("leagueId", 0) == 1,
        # Permanent fix: store the exact fixtureId so settlement never needs
        # fuzzy fixture matching again.  If the client didn't send one, the
        # live-tracking / settlement paths will still attempt to resolve it
        # by team name, but any future pick with this field is bulletproof.
        "fixtureId": pick.get("fixtureId") or None,
        "fixtureDate": pick.get("fixtureDate") or pick.get("matchDate") or None,
        "propType": normalized_prop,
        "line": pick.get("line", 0),
        "recommendation": (pick.get("recommendation") or "over").lower(),
        "playerIsHome": pick.get("playerIsHome") if pick.get("playerIsHome") is not None else (pick.get("venue") == "home"),
        "projectedValue": pick.get("projectedValue") or pick.get("projection") or 0,
        "projection":     pick.get("projection") or pick.get("projectedValue") or 0,
        "confidenceScore": pick.get("confidenceScore") or pick.get("confidence") or 50,
        # rawConfidence preserves the engine's pre-calibration confidence so the
        # calibrator can train against raw values, not its own (calibrated) output.
        # Falls back to the displayed score when raw isn't sent (legacy clients).
        "rawConfidence": pick.get("rawConfidence") or pick.get("confidenceScore") or pick.get("confidence") or 50,
        "confidenceLevel": pick.get("confidenceLevel", "Medium"),
        "confidenceInterval": pick.get("confidenceInterval", []),
        # Persist Bayesian engine metrics for post-game auditing + model improvement.
        # These are essential for projection accuracy analysis (was model's direction
        # correct? how far was the projection from actual?).
        "bayesianMetrics": pick.get("bayesianMetrics") or {},
        "pOver":           pick.get("pOver") or (pick.get("bayesianMetrics") or {}).get("pOver"),
        "pUnder":          pick.get("pUnder") or (pick.get("bayesianMetrics") or {}).get("pUnder"),
        "priorMean":       pick.get("priorMean") or (pick.get("bayesianMetrics") or {}).get("priorMean"),
        "momentumMean":    pick.get("momentumMean") or (pick.get("bayesianMetrics") or {}).get("momentumMean"),
        "venue": pick.get("venue") or pick.get("_request", {}).get("venue", "home"),
        "position": pick.get("player", {}).get("position", ""),
        "role": pick.get("player", {}).get("role", ""),
        "status": "live",
        "result": "pending",
        "actualValue": None,
        "matchScore": None,
        "coinFlip": pick.get("coinFlip", False),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "settledAt": None,
        "gameScript": pick.get("gameScript") or {},
        "moneyline": pick.get("moneyline") or None,
        "oddsTier": pick.get("oddsTier") or pick.get("bayesianMetrics", {}).get("oddsTierPriors", {}).get("oddsTier") or None,
    }

    # Persist the model's projected ball-possession split so we can compare
    # projected vs actual once the match settles. Comes from the predict
    # response's `matchupOverview.expectedPossession` (mobile passes it through
    # as projHomePoss / projAwayPoss). Stored as numeric percentages 0-100.
    proj_home = pick.get("projHomePoss")
    proj_away = pick.get("projAwayPoss")
    try:
        if proj_home is not None:
            doc["projHomePoss"] = round(float(proj_home), 1)
        if proj_away is not None:
            doc["projAwayPoss"] = round(float(proj_away), 1)
    except (TypeError, ValueError):
        pass

    # Store AI analysis fields directly on sport picks (no separate predictions collection)
    # Soccer, CS2, and WTA all persist AI analysis on the pick for offline analysis modal access.
    if sport in ("cs2", "soccer", "wta"):
        for field in ("sharpSummary", "reasoning", "tacticalBreakdown", "tacticalAlerts"):
            val = pick.get(field)
            if val:
                doc[field] = val
        # Store tactical metrics so the analysis modal can show them
        for field in ("projectedValue", "recommendation", "confidenceScore", "confidenceLevel", "pOver", "pUnder"):
            val = pick.get(field)
            if val is not None:
                doc[field] = val
    if sport == "cs2":
        # ── CS2 position/role cleanup ──────────────────────────────────────
        # Soccer position fields (e.g. "CM · Box-to-Box", "ST · Poacher")
        # were leaking onto CS2 picks because the player.position/role were
        # being passed through from a previous soccer prediction or the
        # default soccer resolver. CS2 has no tactical role of that kind —
        # we either store the engine's roleClassification (e.g. "entry_fragger")
        # or leave both fields blank.
        engine_role = (pick.get("tacticalMetrics") or {}).get("roleClassification")
        if engine_role:
            doc["position"] = ""               # CS2 doesn't have a position label
            doc["role"]     = str(engine_role).replace("_", " ").title()
        else:
            doc["position"] = ""
            doc["role"]     = ""

    # ── MLB / CS2 position fix ─────────────────────────────────────────────────
    # MLB picks: always set baseball-appropriate position/role.  Old picks were
    # saved without a sport field so picks.py defaulted to "soccer" and called
    # resolve_position_ai — giving pitchers labels like "GK · Shot-Stopper".
    _MLB_PROP_TYPES_SET = {
        "pitcher_strikeouts", "innings_pitched", "hits_allowed", "earned_runs",
        "walks_allowed", "pitches_thrown", "batters_faced",
        "hits", "home_runs", "rbi", "walks", "strikeouts", "runs",
        "total_bases", "stolen_bases", "doubles", "plate_appearances",
        "hitter_fantasy_points", "hits_runs_rbis", "pitcher_fantasy_score", "pitching_outs",
    }
    _PITCHER_PROP_TYPES_SET = {
        "pitcher_strikeouts", "innings_pitched", "hits_allowed", "earned_runs",
        "walks_allowed", "pitches_thrown", "batters_faced",
        "pitcher_fantasy_score", "pitching_outs",
    }
    _SOCCER_POS_LABELS = {
        "GK", "CB", "RB", "LB", "RWB", "LWB", "CDM", "CM",
        "CAM", "RW", "LW", "ST", "CF", "SS", "AM", "DM",
    }
    _SOCCER_ROLE_LABELS = {
        "Shot-Stopper", "Sweeper Keeper", "Ball-Playing CB", "Stopper",
        "Fullback", "Wing-Back", "Inverted Fullback", "Anchor",
        "Box-to-Box", "Deep-Lying Playmaker", "Ball Winner", "Mezzala",
        "Advanced Playmaker", "Wide Playmaker", "Traditional Winger",
        "Inverted Winger", "Progressive Carrier", "Inside Forward",
        "Target Man", "Poacher", "False 9", "Shadow Striker",
        "Complete Forward", "Pressing Forward",
    }
    if sport == "mlb" or doc["propType"] in _MLB_PROP_TYPES_SET:
        # Overwrite any soccer-contaminated position/role with correct MLB labels
        if (not doc["position"]
                or doc["position"] in _SOCCER_POS_LABELS
                or doc["role"] in _SOCCER_ROLE_LABELS):
            if doc["propType"] in _PITCHER_PROP_TYPES_SET:
                doc["position"] = "P"
                doc["role"] = "Pitcher"
            else:
                doc["position"] = "Batter"
                doc["role"] = "Batter"

    # AI-powered position resolution if position is missing (soccer only,
    # never for MLB/CS2 prop types).
    elif sport == "soccer" and doc["propType"] not in _MLB_PROP_TYPES_SET and (not doc["position"] or doc["position"] in ("Unknown", "unknown", "")):
        try:
            from ai_positions import resolve_position_ai
            resolved = await resolve_position_ai(doc["playerName"], "soccer")
            if resolved.get("position"):
                doc["position"] = resolved["position"]
                doc["role"] = resolved.get("role", doc["role"])
        except Exception:
            pass

    # ── Duplicate-pick guard ─────────────────────────────────────────────────
    # Reject only the same prediction event.  The same player/prop/line can be
    # a valid new record in a later fixture, so fixtureId is the identity when
    # present.  Legacy records without a fixture use the timestamp window.
    dup_query = {
        "email": req.email.lower(),
        "playerName": doc["playerName"],
        "opponentName": doc["opponentName"],
        "propType": doc["propType"],
        "line": doc["line"],
    }
    if doc.get("fixtureId"):
        dup_query["fixtureId"] = doc["fixtureId"]
    else:
        try:
            ts = datetime.fromisoformat(doc["timestamp"].replace("Z", "+00:00"))
            from_d = (ts - timedelta(days=7)).isoformat()
            to_d = (ts + timedelta(days=1)).isoformat()
            dup_query["timestamp"] = {"$gte": from_d, "$lte": to_d}
        except Exception:
            pass
    dup_query["pickId"] = {"$ne": pick_id}
    existing_dup = await db.picks.find_one(
        dup_query, {"_id": 0, "pickId": 1, "timestamp": 1}
    )
    if existing_dup:
        raise HTTPException(
            status_code=409,
            detail=(
                f"You already saved a {doc['playerName']} {doc['propType']} ({doc['line']}) pick "
                f"against {doc['opponentName']}. Delete it first if you want to re-pick."
            )
        )

    await db.picks.update_one({"pickId": pick_id, "email": req.email.lower()}, {"$set": doc}, upsert=True)

    # Automatic Community posting happens from the Picks screen after the
    # highest-confidence card has been rendered and captured. Do not create a
    # text-only post here: that could publish a lower pick before the UI has
    # selected and captured the actual top card image.

    # =============================================
    # SLIP CORRELATION ANALYSIS — Same-game risk detection
    # =============================================
    correlation_warnings = []
    try:
        # Find other active picks from the same game (by team/opponent overlap)
        same_game_picks = await db.picks.find({
            "email": req.email.lower(),
            "pickId": {"$ne": pick_id},
            "status": {"$in": ["live", "pending"]},
            "$or": [
                # Same team's player
                {"teamName": doc["teamName"], "opponentName": doc["opponentName"]},
                # Opposing team's player
                {"teamName": doc["opponentName"], "opponentName": doc["teamName"]},
            ]
        }, {"_id": 0, "playerName": 1, "teamName": 1, "recommendation": 1, "propType": 1, "line": 1}).to_list(20)

        if same_game_picks:
            same_team = [p for p in same_game_picks if p.get("teamName") == doc["teamName"]]
            opp_team = [p for p in same_game_picks if p.get("teamName") == doc["opponentName"]]
            total_in_game = len(same_game_picks) + 1  # +1 for this pick

            # Check directional correlation
            all_recs = [p.get("recommendation") for p in same_game_picks] + [doc["recommendation"]]
            all_under = all(r == "under" for r in all_recs)
            all_over = all(r == "over" for r in all_recs)

            pass_props = {"pass_attempts", "passes", "key_passes", "crosses"}
            is_pass_prop = doc["propType"] in pass_props

            if total_in_game >= 3 and (all_under or all_over):
                direction = "UNDER" if all_under else "OVER"
                correlation_warnings.append({
                    "type": "CORRELATED_RISK",
                    "severity": "HIGH",
                    "message": f"You have {total_in_game} picks ALL {direction} in the same game. If game flow goes against you, ALL picks lose together.",
                })

            if same_team:
                same_dir = [p for p in same_team if p.get("recommendation") == doc["recommendation"]]
                opp_dir = [p for p in same_team if p.get("recommendation") != doc["recommendation"]]
                if same_dir:
                    names = ", ".join(p["playerName"] for p in same_dir)
                    correlation_warnings.append({
                        "type": "BOOSTING",
                        "severity": "INFO",
                        "message": f"Same team, same direction as {names}. These picks are positively correlated.",
                    })
                if opp_dir:
                    names = ", ".join(p["playerName"] for p in opp_dir)
                    correlation_warnings.append({
                        "type": "CONFLICTING",
                        "severity": "MEDIUM",
                        "message": f"Same team but OPPOSITE direction to {names}. These picks may conflict.",
                    })

            if opp_team and is_pass_prop:
                opp_pass = [p for p in opp_team if p.get("propType") in pass_props]
                if opp_pass:
                    same_dir_opp = [p for p in opp_pass if p.get("recommendation") == doc["recommendation"]]
                    if same_dir_opp:
                        names = ", ".join(p["playerName"] for p in same_dir_opp)
                        dir_label = doc["recommendation"].upper()
                        correlation_warnings.append({
                            "type": "POSSESSION_CONTRADICTION",
                            "severity": "CRITICAL",
                            "message": f"ZERO-SUM ALERT: You have {dir_label} on passes for BOTH teams ({names} + {doc['playerName']}). "
                                       f"Possession is zero-sum — if one team passes less, the other passes MORE. "
                                       f"These picks CANNOT both hit unless the game is extremely low-tempo. "
                                       f"Consider flipping the direction on one team's player.",
                        })
                        correlation_warnings.append({
                            "type": "OPPOSING_TEAMS_SAME_DIR",
                            "severity": "HIGH",
                            "message": f"Both teams' players {dir_label} on passes ({names}). In open games, one team's passes rise as the other's falls. High correlation risk.",
                        })
    except Exception as e:
        print(f"[CORRELATION] Error: {e}")

    return {
        "success": True,
        "pickId": pick_id,
        "trackingId": tracking_id,
        "correlationWarnings": correlation_warnings,
    }

# ── Picks list in-memory cache ────────────────────────────────────────────
# Caches the PROCESSED picks list per email for 20s.  This prevents the
# owner account (which loads 5 000 docs from Atlas) from re-running the
# entire pipeline on every poll cycle.
_picks_list_cache: dict[str, dict] = {}   # email → {ts, picks}
PICKS_LIST_CACHE_TTL = 20                 # seconds


# ── Owner-only media enrichment (player photos + team crests) ───────────
async def _enrich_owner_media(picks: list[dict], requester_email: str) -> None:
    """Attach player photo and team crest URLs for the owner account only."""
    from config import OWNER_EMAILS
    if requester_email not in OWNER_EMAILS:
        return
    player_ids = {p.get("playerId") for p in picks if p.get("playerId")}
    team_ids = {p.get("teamId") for p in picks if p.get("teamId")}
    opponent_ids = {p.get("opponentId") for p in picks if p.get("opponentId")}
    opponent_names = {p.get("opponentName") for p in picks if p.get("opponentName")}

    player_map: dict[int, str] = {}
    team_map: dict[int, str] = {}
    opp_map: dict[str, str] = {}

    if player_ids:
        async for doc in db["cache_players"].find(
            {"playerId": {"$in": list(player_ids)}},
            {"_id": 0, "playerId": 1, "photo": 1},
        ):
            player_map.setdefault(doc.get("playerId"), doc.get("photo", "") or "")

    if team_ids:
        async for doc in db["cache_teams"].find(
            {"teamId": {"$in": list(team_ids)}},
            {"_id": 0, "teamId": 1, "logo": 1},
        ):
            team_map.setdefault(doc.get("teamId"), doc.get("logo", "") or "")

    # Opponent logos by name AND by opponentId (abbreviated names like "Gimnasia M."
    # often miss cache_teams name matching, but the fixture opponentId is reliable).
    if opponent_ids:
        async for doc in db["cache_teams"].find(
            {"teamId": {"$in": list(opponent_ids)}},
            {"_id": 0, "teamId": 1, "logo": 1},
        ):
            team_map.setdefault(doc.get("teamId"), doc.get("logo", "") or "")

    if opponent_names:
        names = list(opponent_names)
        async for doc in db["cache_teams"].find(
            {"name": {"$in": names}},
            {"_id": 0, "name": 1, "logo": 1},
        ):
            opp_map.setdefault(doc.get("name", "").lower(), doc.get("logo", "") or "")
        missing = {n.lower() for n in names} - set(opp_map.keys())
        if missing:
            async for doc in db["cache_teams"].find(
                {"nameLower": {"$in": list(missing)}},
                {"_id": 0, "nameLower": 1, "logo": 1},
            ):
                opp_map.setdefault(doc.get("nameLower", "").lower(), doc.get("logo", "") or "")

    for p in picks:
        p["ownerPlayerPhoto"] = player_map.get(p.get("playerId"), "")
        p["ownerTeamLogo"] = team_map.get(p.get("teamId"), "")
        p["ownerOpponentLogo"] = (
            team_map.get(p.get("opponentId"), "")
            or opp_map.get((p.get("opponentName") or "").lower(), "")
        )

    # Fixture-date backfill for owner cards (kickoff time display).
    fid_to_picks: dict[int, list] = {}
    for p in picks:
        if p.get("fixtureId") and not p.get("fixtureDate"):
            fid_to_picks.setdefault(p["fixtureId"], []).append(p)
    if fid_to_picks:
        async def _backfill_fixture_dates(fids: list[int], pmap: dict[int, list]):
            from utils import api_football_request
            for fid in fids[:20]:
                try:
                    res = await api_football_request("fixtures", {"id": fid})
                    if res:
                        fd = res[0].get("fixture", {}).get("date", "")
                        if fd:
                            await db.picks.update_many(
                                {"fixtureId": fid, "fixtureDate": {"$in": [None, ""]}},
                                {"$set": {"fixtureDate": fd}},
                            )
                            for pp in pmap.get(fid, []):
                                pp["fixtureDate"] = fd
                except Exception:
                    pass
        aio.ensure_future(_backfill_fixture_dates(list(fid_to_picks.keys()), fid_to_picks))

    # Backfill missing player photos and team crests in the background (owner-only, capped).
    missing_photo_pids = [pid for pid, url in player_map.items() if not url]
    missing_team_ids = {tid for tid in (team_ids | opponent_ids) if tid and not team_map.get(tid)}
    if missing_photo_pids or missing_team_ids:
        async def _backfill_media(pids: list[int], team_ids_missing: set[int], teams: list[tuple[int, str, int]]):
            from cache import refresh_player_cache, sync_squad
            from utils import api_football_request, strip_accents

            # Fetch missing team crests first.
            for tid in list(team_ids_missing)[:15]:
                try:
                    data = await api_football_request("teams", {"id": tid})
                    if data and data[0].get("team"):
                        t = data[0]["team"]
                        await db["cache_teams"].update_one(
                            {"teamId": tid},
                            {
                                "$set": {
                                    "teamId": tid,
                                    "name": t.get("name", ""),
                                    "nameLower": (t.get("name") or "").lower(),
                                    "nameClean": strip_accents((t.get("name") or "").lower()),
                                    "logo": t.get("logo", "") or "",
                                    "country": t.get("country", ""),
                                    "_dt": datetime.now(timezone.utc),
                                }
                            },
                            upsert=True,
                        )
                except Exception:
                    pass

            # Refresh squads — populates photos for the whole team in one call.
            seen_teams = set()
            for team_id, team_name, league_id in teams:
                if team_id and team_id not in seen_teams:
                    seen_teams.add(team_id)
                    try:
                        await sync_squad(team_id, team_name or "", league_id or 0)
                    except Exception:
                        pass

            # Individual fallback for any players still missing.
            for pid in pids[:10]:
                try:
                    await refresh_player_cache(pid)
                except Exception:
                    pass

        backfill_teams = [(p.get("teamId"), p.get("teamName"), p.get("leagueId")) for p in picks if p.get("teamId")]
        aio.ensure_future(_backfill_media(missing_photo_pids, missing_team_ids, backfill_teams))


@router.post("/picks/list")
async def list_picks(req: GetPicksRequest):
    from config import OWNER_EMAIL, OWNER_EMAILS
    requester_email = req.email.lower()
    session = await db.sessions.find_one({"email": requester_email, "session_token": req.token}, {"_id": 0})
    if not session:
        raise HTTPException(status_code=401, detail="Invalid session")

    # ── Short-circuit: serve from cache if fresh enough ───────────────────
    _now_mono = _time_mod.monotonic()
    _cached = _picks_list_cache.get(requester_email)
    if _cached and (_now_mono - _cached["ts"]) < PICKS_LIST_CACHE_TTL:
        return {"picks": _cached["picks"]}

    # Always fetch only the requester's own picks for the My Picks / Live / History UI.
    # The owner's "see all users" calibration view is available in /picks/matchups.
    # Fetching 5,000 large documents from Atlas takes 120+ seconds — unacceptable.
    is_owner_view = requester_email in OWNER_EMAILS
    picks = await db.picks.find({"email": requester_email}, {"_id": 0}).sort("timestamp", -1).to_list(None)

    # Budget cut: NFL/NBA/MLB are no longer offered; hide them from settled picks too.
    _HIDDEN_SPORTS = {"mlb", "nba", "nfl"}
    picks = [p for p in picks if p.get("sport", "soccer") not in _HIDDEN_SPORTS]

    def _pick_email(p: dict) -> str:
        return requester_email

    def _should_process(_p: dict) -> bool:
        return True

    for p in picks:
        if not _should_process(p):
            continue
        pick_email = _pick_email(p)
        updates = {}
        if not p.get("trackingId"):
            tid = generate_tracking_id()
            p["trackingId"] = tid
            updates["trackingId"] = tid
        if not p.get("sport") or (
            # Repair picks already wrongly stamped as soccer: if the pick has
            # a CS2 propType it must be cs2 regardless of what sport says.
            p.get("sport") == "soccer"
            and str(p.get("propType", "")).startswith(("map1_", "maps_1_2_", "map3_"))
        ):
            # Detect sport from propType so old picks saved before the sport
            # field was added are not permanently mis-labelled as soccer.
            _CS2_PROPS = {
                "map1_kills", "map1_deaths", "map1_assists", "map1_adr",
                "map1_rating", "map1_first_kills", "map1_clutches_won",
                "map1_headshot_pct", "maps_1_2_kills", "maps_1_2_deaths",
                "maps_1_2_assists", "maps_1_2_headshots",
                "map3_kills", "map3_deaths", "map3_assists", "map3_headshots",
                "map3_adr",
            }
            _MLB_PROPS = {
                "hits", "home_runs", "rbi", "walks", "strikeouts", "runs",
                "total_bases", "stolen_bases", "doubles", "plate_appearances",
            }
            _pt = p.get("propType", "")
            if _pt in _CS2_PROPS or str(_pt).startswith(("map1_", "maps_1_2_", "map3_")):
                _detected = "cs2"
            elif _pt in _MLB_PROPS:
                _detected = "mlb"
            else:
                _detected = "soccer"
            p["sport"] = _detected
            updates["sport"] = _detected
        rec_raw = p.get("recommendation", "")
        if rec_raw and rec_raw != rec_raw.lower():
            p["recommendation"] = rec_raw.lower()
            updates["recommendation"] = rec_raw.lower()
        if updates:
            await db.picks.update_one(
                {"pickId": p["pickId"], "email": pick_email},
                {"$set": updates}
            )

    for p in picks:
        if p.get("status") != "settled":
            continue
        if not _should_process(p):
            # Still apply in-memory DNP coercion for display correctness
            _sport = p.get("sport", "soccer")
            if (
                not _has_soccer_stat_evidence(p)
                and (bool(p.get("voidReason")) or (_sport == "soccer" and (p.get("minutesPlayed") or 90) < 30))
            ):
                p["result"] = "dnp"
            continue
        pick_email = _pick_email(p)

        # ── DNP / early-sub guard ────────────────────────────────────────────
        # Voided picks (voidReason set OR <30 min played) must always be DNP,
        # except when the final provider stat proves the player participated.
        # This exception is essential because API-Football can report a stale
        # low minutes value alongside a real positive fixture stat.
        # This branch ACTIVELY corrects them — not just skips — so that a race
        # condition between a concurrent list_picks response and a DB fix can
        # never leave result=miss permanently stuck in the DB.
        # CRITICAL: only applies to soccer — minutesPlayed is meaningless for
        # CS2/MLB/WTA and would falsely DNP every non-soccer pick.
        _sport = p.get("sport", "soccer")
        _min_played = p.get("minutesPlayed")
        has_stat_evidence = _has_soccer_stat_evidence(p)
        is_dnp = not has_stat_evidence and (bool(p.get("voidReason")) or (
            _sport == "soccer" and _min_played is not None and _min_played < 30
        ))
        if is_dnp:
            if p.get("result") != "dnp":
                p["result"] = "dnp"
                void_label = p.get("voidReason") or (
                    f"<30 min ({p.get('minutesPlayed',0)} min played)"
                    if _sport == "soccer" else f"DNP ({_sport})"
                )
                print(f"[CONSISTENCY] DNP→dnp {p.get('playerName','')} {p.get('propType','')} ({void_label})")
                await db.picks.update_one(
                    {"pickId": p["pickId"], "email": pick_email},
                    {"$set": {"result": "dnp", "hitPct": 0,
                              "voidReason": p.get("voidReason") or void_label}}
                )
            continue

        # Repair an already-settled false DNP in-place the next time the
        # owner's picks are loaded. Do not manually edit production records:
        # this deterministic repair is safe, idempotent, and based only on
        # the stored final stat and original line/direction.
        if has_stat_evidence and (p.get("voidReason") or p.get("result") == "dnp"):
            previous_result = p.get("result")
            previous_void_reason = p.get("voidReason")
            correct, pass_outcome = _settle_pick_result(
                p["actualValue"], p.get("line", 0), p
            )
            p["result"] = correct
            p.pop("voidReason", None)
            repair_set = {
                "result": correct,
                "hitPct": 100 if correct == "hit" else 50 if correct == "push" else 0,
                "settlementCorrection": {
                    "reason": "positive provider stat overrides stale minutes/DNP classification",
                    "previousResult": previous_result,
                    "previousVoidReason": previous_void_reason,
                    "actualValue": p["actualValue"],
                    "minutesPlayed": _min_played,
                    "correctedBy": "picks_consistency_guard",
                    "correctedAt": datetime.now(timezone.utc).isoformat(),
                },
            }
            if pass_outcome:
                repair_set["passOutcome"] = pass_outcome
            await db.picks.update_one(
                {"pickId": p["pickId"], "email": pick_email},
                {"$set": repair_set, "$unset": {"voidReason": ""}},
            )
            print(
                f"[CONSISTENCY] Repaired false DNP {p.get('playerName','')} "
                f"{p.get('propType','')} stat={p['actualValue']} min={_min_played} → {correct}"
            )

        # ── Normal result consistency check ──────────────────────────────────
        if p.get("actualValue") is not None:
            correct, pass_outcome = _settle_pick_result(p["actualValue"], p.get("line", 0), p)
            if correct != p.get("result"):
                p["result"] = correct
                print(f"[CONSISTENCY] Correcting {p.get('playerName','')} {p.get('propType','')} → {correct}")
                updates = {"result": correct}
                if pass_outcome:
                    updates["passOutcome"] = pass_outcome
                await db.picks.update_one(
                    {"pickId": p["pickId"], "email": pick_email},
                    {"$set": updates}
                )

    # ── CS2 settled-pick data repair ─────────────────────────────────────────
    # Only run for the requester's OWN picks (owner-view must not repair other
    # users' picks — it causes 429 floods and multi-minute hangs).
    for p in picks:
        if not _should_process(p):
            continue
        if p.get("sport") != "cs2" or p.get("status") != "settled":
            continue
        prop = p.get("propType", "")
        actual = p.get("actualValue")
        if not (prop.startswith(("maps_1_2_", "map1_", "map3_")) and actual is not None):
            continue
        if prop in ("maps_1_2_kills", "maps_1_3_kills", "map1_kills", "map3_kills") and actual < 5:
            pass
        elif prop in ("maps_1_2_deaths", "maps_1_3_deaths", "map1_deaths", "map3_deaths") and actual < 5:
            pass
        elif prop in ("maps_1_2_assists", "maps_1_3_assists", "map1_assists", "map3_assists") and actual < 2:
            pass
        elif prop in ("maps_1_2_adr", "map1_adr", "map3_adr") and actual < 30:
            pass
        else:
            continue
        pick_email = _pick_email(p)
        try:
            settled = await _settle_cs2_pick({**p, "email": pick_email})
            if settled and settled.get("actualValue") is not None:
                p["actualValue"] = settled["actualValue"]
                p["result"]      = settled["result"]
                p["hitPct"]      = settled["hitPct"]
                if settled.get("matchScore"):
                    p["matchScore"] = settled["matchScore"]
                print(f"[CS2 REPAIR] {p.get('playerName','')} {prop}: actualValue {actual} → {settled['actualValue']}")
                await db.picks.update_one(
                    {"pickId": p["pickId"], "email": pick_email},
                    {"$set": {
                        "actualValue": settled["actualValue"],
                        "result":      settled["result"],
                        "hitPct":      settled["hitPct"],
                        "matchScore":  settled.get("matchScore"),
                    }}
                )
        except Exception as e:
            print(f"[CS2 REPAIR] error for {p.get('playerName','')}: {e}")

    # Projection backfill — only for own picks to avoid 5000-query cascade
    needs_proj = [p for p in picks if not p.get("projectedValue") and _should_process(p)]
    if needs_proj:
        for p in needs_proj:
            pick_email = _pick_email(p)
            try:
                pid = p.get("playerId")
                pt = p.get("propType", "")
                opp = p.get("opponentName", "")
                if pid:
                    query = {"player.id": pid, "propType": pt}
                    if opp:
                        query["opponent"] = opp
                    pred = await db.predictions.find_one(
                        query,
                        {"projectedValue": 1, "_id": 0},
                        sort=[("_created", -1)]
                    )
                    if not pred and opp:
                        pred = await db.predictions.find_one(
                            {"player.id": pid, "propType": pt},
                            {"projectedValue": 1, "_id": 0},
                            sort=[("_created", -1)]
                        )
                    if pred and pred.get("projectedValue"):
                        p["projectedValue"] = pred["projectedValue"]
                        await db.picks.update_one(
                            {"pickId": p["pickId"], "email": pick_email},
                            {"$set": {"projectedValue": pred["projectedValue"]}}
                        )
            except Exception:
                pass

    # Live settle: ONLY process picks belonging to the requester's own email
    live_picks = [p for p in picks if p.get("status") in ("live", "pending") and _should_process(p)]
    # MLB picks are handled by the mlb_live_loop background task which writes
    # currentValue / matchStatus directly to MongoDB — don't pass them to the
    # soccer pipeline or it will overwrite their matchStatus with "scheduled".
    _MLB_PROP_SET = {
        "pitcher_strikeouts", "innings_pitched", "hits_allowed", "earned_runs",
        "walks_allowed", "pitches_thrown", "batters_faced",
        "hits", "home_runs", "rbi", "walks", "strikeouts", "runs",
        "total_bases", "stolen_bases", "doubles", "plate_appearances",
    }
    # ── CS2 auto-settle ────────────────────────────────────────────────────────
    # When a CS2 match finishes, settle the pick by fetching the player's real
    # final stats from the BDL CS2 API. No live tracking — just a post-match
    # check each time the user opens their picks screen.
    #
    # RATE LIMIT GUARD: only attempt to settle a pick once every
    # CS2_SETTLE_COOLDOWN_SEC (5 min).  Concurrent list calls for the same
    # pick are serialised via per-pick asyncio.Locks so only ONE request
    # fires at a time.  This prevents the 429 cascade that blocked settling.
    cs2_live_picks = [p for p in live_picks if p.get("sport") == "cs2"]
    if cs2_live_picks:
        async def _settle_with_cooldown(pick: dict) -> Optional[dict]:
            pick_id = pick.get("pickId", "")
            now = _time.monotonic()
            last = _cs2_settle_last_attempt.get(pick_id, 0.0)
            if now - last < CS2_SETTLE_COOLDOWN_SEC:
                # Still within cooldown window — skip API call
                return None
            lock = _cs2_settle_lock(pick_id)
            if lock.locked():
                # Another concurrent request is already settling this pick
                return None
            async with lock:
                # Re-check cooldown inside the lock (double-checked locking)
                now2 = _time.monotonic()
                last2 = _cs2_settle_last_attempt.get(pick_id, 0.0)
                if now2 - last2 < CS2_SETTLE_COOLDOWN_SEC:
                    return None
                _cs2_settle_last_attempt[pick_id] = now2
                return await _settle_cs2_pick({**pick, "email": req.email.lower()})

        try:
            cs2_settle_tasks = [_settle_with_cooldown(p) for p in cs2_live_picks]
            cs2_results = await aio.gather(*cs2_settle_tasks, return_exceptions=True)
            for p, settled in zip(cs2_live_picks, cs2_results):
                if isinstance(settled, Exception) or not settled:
                    continue
                p["status"]      = "settled"
                p["result"]      = settled.get("result", "pending")
                p["actualValue"] = settled.get("actualValue")
                p["hitPct"]      = settled.get("hitPct")
                p["matchScore"]  = settled.get("matchScore")
                p["settledAt"]   = settled.get("settledAt")
        except Exception:
            traceback.print_exc()

    # ── WTA settle dispatch ────────────────────────────────────────────────
    wta_live_picks = [p for p in live_picks if p.get("sport") == "wta"]
    if wta_live_picks:
        async def _settle_wta_with_cooldown(pick: dict) -> Optional[dict]:
            pick_id = pick.get("pickId", "")
            now  = _time.monotonic()
            last = _wta_settle_last_attempt.get(pick_id, 0.0)
            if now - last < WTA_SETTLE_COOLDOWN_SEC:
                return None
            lock = _wta_settle_lock(pick_id)
            if lock.locked():
                return None
            async with lock:
                now2  = _time.monotonic()
                last2 = _wta_settle_last_attempt.get(pick_id, 0.0)
                if now2 - last2 < WTA_SETTLE_COOLDOWN_SEC:
                    return None
                _wta_settle_last_attempt[pick_id] = now2
                return await _settle_wta_pick({**pick, "email": req.email.lower()})

        try:
            wta_settle_tasks = [_settle_wta_with_cooldown(p) for p in wta_live_picks]
            wta_results = await aio.gather(*wta_settle_tasks, return_exceptions=True)
            for p, settled in zip(wta_live_picks, wta_results):
                if isinstance(settled, Exception) or not settled:
                    continue
                p["status"]      = "settled"
                p["result"]      = settled.get("result", "pending")
                p["actualValue"] = settled.get("actualValue")
                p["hitPct"]      = settled.get("hitPct")
                p["matchScore"]  = settled.get("matchScore")
                p["settledAt"]   = settled.get("settledAt")
        except Exception:
            traceback.print_exc()

    _BDL_SPORTS = {"nba", "wnba", "nhl", "nfl"}
    bdl_live_picks = [p for p in live_picks if p.get("sport") in _BDL_SPORTS]
    if bdl_live_picks:
        try:
            bdl_updates = await _process_bdl_live(bdl_live_picks, req.email.lower())
            bdl_update_map = {u["pickId"]: u for u in bdl_updates if u.get("pickId")}
            for p in picks:
                upd = bdl_update_map.get(p.get("pickId"))
                if upd:
                    p["currentValue"] = upd.get("currentValue")
                    p["pace"]         = upd.get("pace")
                    p["hitPct"]       = upd.get("hitPct")
                    p["elapsed"]      = upd.get("elapsed")
                    p["period"]       = upd.get("period")
                    p["matchStatus"]  = upd.get("matchStatus")
                    p["matchScore"]   = upd.get("matchScore")
                    if upd.get("homeTeam"):
                        p["homeTeam"] = upd.get("homeTeam")
                    if upd.get("awayTeam"):
                        p["awayTeam"] = upd.get("awayTeam")
                    if upd.get("finalHomeGoals") is not None:
                        p["finalHomeGoals"] = upd.get("finalHomeGoals")
                    if upd.get("finalAwayGoals") is not None:
                        p["finalAwayGoals"] = upd.get("finalAwayGoals")
                    if upd.get("result") and upd["result"] != "pending":
                        p["status"]      = "settled"
                        p["result"]      = upd["result"]
                        p["actualValue"] = upd.get("actualValue")
        except Exception:
            traceback.print_exc()

    soccer_live_picks = [
        p for p in live_picks
        if p.get("sport", "soccer") not in {"mlb", "cs2", "wta", "nba", "wnba", "nhl", "nfl"}
        and p.get("propType", "") not in _MLB_PROP_SET
    ]
    if soccer_live_picks:
        try:
            live_updates = await _process_soccer_live(soccer_live_picks, req.email.lower())
            update_map = {u["pickId"]: u for u in live_updates if u.get("pickId")}
            for p in picks:
                upd = update_map.get(p.get("pickId"))
                if upd:
                    p["currentValue"] = upd.get("currentValue")
                    p["pace"] = upd.get("pace")
                    p["hitPct"] = upd.get("hitPct")
                    p["elapsed"] = upd.get("elapsed")
                    p["period"] = upd.get("period")
                    p["matchStatus"] = upd.get("matchStatus")
                    p["matchScore"] = upd.get("matchScore")
                    _old_fid = p.get("fixtureId")
                    new_fid = upd.get("fixtureId")
                    p["fixtureId"] = new_fid
                    p["minutesPlayed"] = upd.get("minutesPlayed")
                    p["paceMismatch"] = upd.get("paceMismatch")
                    p["paceWarning"] = upd.get("paceWarning")
                    if upd.get("homeTeam"):
                        p["homeTeam"] = upd.get("homeTeam")
                    if upd.get("awayTeam"):
                        p["awayTeam"] = upd.get("awayTeam")
                    if upd.get("finalHomeGoals") is not None:
                        p["finalHomeGoals"] = upd.get("finalHomeGoals")
                    if upd.get("finalAwayGoals") is not None:
                        p["finalAwayGoals"] = upd.get("finalAwayGoals")
                    if upd.get("homePoss") is not None:
                        p["homePoss"] = upd.get("homePoss")
                    if upd.get("awayPoss") is not None:
                        p["awayPoss"] = upd.get("awayPoss")
                    if upd.get("oppAvgPoss") is not None:
                        p["oppAvgPoss"] = upd.get("oppAvgPoss")
                    if upd.get("result") and upd["result"] != "pending":
                        p["status"] = "settled"
                        p["result"] = upd["result"]
                        p["actualValue"] = upd.get("actualValue")
                    # Persist fixtureId to DB so future calls can use T0 lookup
                    # (direct by ID) instead of T1/T2/T3, reducing rate-limit risk.
                    if new_fid and not _old_fid:
                        try:
                            await db.picks.update_one(
                                {"pickId": p["pickId"], "email": req.email.lower()},
                                {"$set": {"fixtureId": new_fid}}
                            )
                        except Exception:
                            pass
        except Exception:
            traceback.print_exc()

    # ── FINAL STAT REFRESH ─────────────────────────────────────────────────
    # For own settled picks within the last 8 hours, re-fetch from the fixture
    # API to get the true final value. Never run against other users' picks.
    try:
        now_utc = datetime.now(timezone.utc)
        recently_settled = [
            p for p in picks
            if _should_process(p)
            and p.get("status") == "settled"
            and not p.get("correctedManually")
            and not p.get("voidReason")
            and p.get("settledAt")
            and (now_utc - datetime.fromisoformat(
                    p["settledAt"].replace("Z", "+00:00")
                 )).total_seconds() < 8 * 3600
        ]
        if recently_settled:
            for p in recently_settled[:6]:
                pick_email = _pick_email(p)
                try:
                    team_id   = p.get("teamId") or 0
                    player_id = p.get("playerId") or 0
                    opponent  = p.get("opponentName") or ""
                    prop_type = p.get("propType") or ""
                    league_id = p.get("leagueId") or 39
                    if not player_id:
                        continue
                    refreshed = await _settle_soccer_pick(
                        p, team_id, player_id, opponent, prop_type, league_id
                    )
                    if refreshed and refreshed.get("actualValue") is not None:
                        new_val = refreshed["actualValue"]
                        old_val = p.get("actualValue")
                        meta_set = {}

                        if refreshed.get("voidReason"):
                            new_res = "dnp"
                            p["result"] = new_res
                            p["actualValue"] = new_val
                            p["voidReason"] = refreshed["voidReason"]
                            meta_set = {
                                "result": new_res,
                                "actualValue": new_val,
                                "hitPct": 50,
                                "voidReason": refreshed["voidReason"],
                            }
                            for fld in ("homeTeam", "awayTeam", "finalHomeGoals", "finalAwayGoals",
                                         "homePoss", "awayPoss", "minutesPlayed"):
                                v = refreshed.get(fld)
                                if v is not None and v != "":
                                    meta_set[fld] = v
                                    p[fld] = v
                            print(f"[FINAL REFRESH] {p.get('playerName')} {prop_type}: VOID/PUSH ({refreshed['voidReason']})")
                        else:
                            for fld in ("homeTeam", "awayTeam", "finalHomeGoals", "finalAwayGoals",
                                         "homePoss", "awayPoss"):
                                v = refreshed.get(fld)
                                if v is not None and v != "" and p.get(fld) != v:
                                    meta_set[fld] = v
                                    p[fld] = v
                            if new_val != old_val:
                                line    = p.get("line", 0)
                                rec     = p.get("recommendation", "over")
                                new_res = _settle_result(new_val, line, rec)
                                p["actualValue"] = new_val
                                p["result"]      = new_res
                                meta_set["actualValue"] = new_val
                                meta_set["result"] = new_res
                                print(f"[FINAL REFRESH] {p.get('playerName')} {prop_type}: "
                                      f"actualValue {old_val} → {new_val}, result → {new_res}")

                        if meta_set:
                            await db.picks.update_one(
                                {"pickId": p["pickId"], "email": pick_email},
                                {"$set": meta_set}
                            )
                except Exception as _re:
                    print(f"[FINAL REFRESH] Error for {p.get('playerName','?')}: {_re}")
    except Exception as _fe:
        print(f"[FINAL REFRESH] Outer error: {_fe}")
    # ───────────────────────────────────────────────────────────────────────

    # Owner-only: attach player photos and team crests from API-Football cache.
    await _enrich_owner_media(picks, requester_email)

    # Cache the processed result so rapid re-polls skip the full pipeline
    _picks_list_cache[requester_email] = {"ts": _time_mod.monotonic(), "picks": picks}
    return {"picks": picks}


# In-memory cache for /picks/matchups: email -> {ts, result}. TTL 60s.
_matchups_cache: dict[str, dict] = {}
MATCHUPS_CACHE_TTL = 60


async def _fetch_matchups_with_retry(email: str, token: str) -> dict:
    """
    Soccer-only matchups endpoint.
    Returns one row per unique player+opponent+prop combo (no duplicate picks
    from multiple users). Aggregates hit/miss/push/dnp counts, average line,
    average actual, and most common venue.
    """
    from config import OWNER_EMAIL
    session = await db.sessions.find_one({"email": email.lower(), "session_token": token}, {"_id": 0})
    if not session:
        raise HTTPException(status_code=401, detail="Invalid session")

    # ── SOCCER ONLY. This tab is for soccer matchups, period. ───────────────
    # All subscribers can see the aggregated matchup history from the full
    # settled-picks dataset so the tab is actually useful.
    query: dict = {"status": "settled", "sport": "soccer"}

    projection = {
        "_id": 0,
        "pickId": 1,
        "playerName": 1,
        "teamName": 1,
        "opponentName": 1,
        "position": 1,
        "role": 1,
        "propType": 1,
        "line": 1,
        "recommendation": 1,
        "result": 1,
        "actualValue": 1,
        "projectedValue": 1,
        "sport": 1,
        "leagueId": 1,
        "leagueName": 1,
        "matchScore": 1,
        "playerIsHome": 1,
        "homeTeam": 1,
        "awayTeam": 1,
        "settledAt": 1,
        "timestamp": 1,
    }

    last_error = None
    for attempt in range(3):
        try:
            picks = await db.picks.find(query, projection).sort("timestamp", -1).to_list(None)
            break
        except Exception as e:
            last_error = e
            print(f"[MATCHUPS] DB attempt {attempt + 1} failed for {email.lower()}: {e}")
            await aio.sleep(0.5 * (attempt + 1))
    else:
        raise HTTPException(status_code=503, detail=f"Database temporarily unavailable: {last_error}")

    def _norm_result(res: str) -> str:
        r = (res or "").lower()
        if r in ("hit", "won"): return "Hit"
        if r in ("miss", "lost"): return "Miss"
        if r == "push": return "Push"
        if r == "dnp": return "DNP"
        return "Pending"

    def _league_label(p: dict) -> str:
        return p.get("leagueName") or (f"League {p.get('leagueId')}" if p.get("leagueId") else "Unknown")

    # ── Aggregate by unique matchup: player + opponent + prop ────────────────
    groups: dict[tuple, dict] = {}
    for p in picks:
        player = (p.get("playerName") or "").strip()
        opponent = (p.get("opponentName") or "").strip()
        prop = (p.get("propType") or "").strip()
        if not player or not opponent or not prop:
            continue
        key = (player, opponent, prop)
        g = groups.setdefault(key, {
            "playerName": player,
            "opponentName": opponent,
            "propType": prop,
            "position": "",
            "leagueName": "",
            "leagueId": 0,
            "matchScore": "",
            "lines": [],
            "actuals": [],
            "projecteds": [],
            "results": [],
            "recommendations": [],
            "venues": [],  # True=Home, False=Away
            "count": 0,
            "lastTs": None,
        })
        g["count"] += 1
        g["lines"].append(p.get("line"))
        if isinstance(p.get("actualValue"), (int, float)):
            g["actuals"].append(p["actualValue"])
        if isinstance(p.get("projectedValue"), (int, float)):
            g["projecteds"].append(p["projectedValue"])
        g["results"].append(_norm_result(p.get("result")))
        rec = (p.get("recommendation") or "").upper()
        if rec in ("OVER", "UNDER"):
            g["recommendations"].append(rec)
        if p.get("position"):
            g["position"] = p["position"]
        if _league_label(p) != "Unknown":
            g["leagueName"] = _league_label(p)
            g["leagueId"] = p.get("leagueId") or 0
        if p.get("matchScore") and not g["matchScore"]:
            g["matchScore"] = p["matchScore"]
        if p.get("playerIsHome") is not None:
            g["venues"].append(p["playerIsHome"])
        ts = p.get("timestamp") or p.get("settledAt")
        if ts and (g["lastTs"] is None or ts > g["lastTs"]):
            g["lastTs"] = ts

    matchups = []
    for key, g in groups.items():
        hits = sum(1 for r in g["results"] if r == "Hit")
        misses = sum(1 for r in g["results"] if r == "Miss")
        pushes = sum(1 for r in g["results"] if r == "Push")
        dnps = sum(1 for r in g["results"] if r == "DNP")
        settled = hits + misses

        lines = [x for x in g["lines"] if isinstance(x, (int, float))]
        actuals = [x for x in g["actuals"] if isinstance(x, (int, float))]
        projecteds = [x for x in g["projecteds"] if isinstance(x, (int, float))]

        avg_line = sum(lines) / len(lines) if lines else 0
        avg_actual = sum(actuals) / len(actuals) if actuals else None
        avg_projected = sum(projecteds) / len(projecteds) if projecteds else None

        win_rate = round((hits / settled) * 100) if settled > 0 else 0
        rec_mode = max(set(g["recommendations"]), key=g["recommendations"].count) if g["recommendations"] else ""
        venue_mode = True if g["venues"].count(True) >= g["venues"].count(False) else False if g["venues"] else None

        matchup = {
            "pickId": f"mu-{'-'.join(str(k).replace(' ', '_') for k in key)}",
            "playerName": g["playerName"],
            "opponentName": g["opponentName"],
            "propType": g["propType"],
            "line": round(avg_line, 1),
            "actualValue": round(avg_actual, 1) if avg_actual is not None else None,
            "projectedValue": round(avg_projected, 1) if avg_projected is not None else None,
            "recommendation": rec_mode,
            "position": g["position"],
            "leagueName": g["leagueName"],
            "leagueId": g["leagueId"],
            "matchScore": g["matchScore"],
            "playerIsHome": venue_mode,
            "sport": "soccer",
            "result": "Hit" if win_rate > 50 else "Miss" if settled > 0 else "Pending",
            "hits": hits,
            "misses": misses,
            "pushes": pushes,
            "dnps": dnps,
            "winRate": win_rate,
            "count": g["count"],
            "settledAt": g["lastTs"],
        }
        matchups.append(matchup)

    matchups.sort(key=lambda x: x["playerName"].lower())

    # Owner-only: attach player photos and team crests from API-Football cache.
    await _enrich_owner_media(matchups, email.lower())

    players = sorted({m["playerName"] for m in matchups})
    opponents = sorted({m["opponentName"] for m in matchups})
    positions = sorted({m["position"] for m in matchups if m["position"]})
    prop_types = sorted({m["propType"] for m in matchups})
    leagues = sorted({m["leagueName"] for m in matchups if m["leagueName"]})
    venues = sorted({"Home" if m["playerIsHome"] else "Away" for m in matchups if m["playerIsHome"] is not None})

    return {
        "picks": matchups,
        "options": {
            "players": players,
            "opponents": opponents,
            "venues": venues if venues else ["Home", "Away"],
            "positions": positions,
            "propTypes": prop_types,
            "leagues": leagues,
            "results": ["Hit", "Miss", "Push", "DNP"],
        },
    }


@router.post("/picks/matchups")
async def get_matchups(req: GetPicksRequest):
    """
    Soccer-only matchups tab endpoint.
    Returns unique player+opponent+prop matchups (no per-user duplicate picks).
    Cached in-memory for 60s.
    """
    cache_key = req.email.lower()
    now = _time_mod.monotonic()
    cached = _matchups_cache.get(cache_key)
    if cached and (now - cached["ts"]) < MATCHUPS_CACHE_TTL:
        return cached["result"]

    result = await _fetch_matchups_with_retry(req.email, req.token)
    _matchups_cache[cache_key] = {"ts": now, "result": result}
    return result


@router.post("/picks/matchups/backfill-venues")
async def backfill_venues(req: GetPicksRequest):
    """
    Backfill playerIsHome for all settled soccer picks that are missing it.
    Uses stored fixtureId when available, otherwise falls back to team-fixture
    matching. Runs in the background; returns immediately.
    """
    from config import OWNER_EMAIL
    session = await db.sessions.find_one({"email": req.email.lower(), "session_token": req.token}, {"_id": 0})
    if not session:
        raise HTTPException(status_code=401, detail="Invalid session")
    if req.email.lower() != OWNER_EMAIL:
        raise HTTPException(status_code=403, detail="Owner only")

    async def _run():
        query = {"status": "settled", "sport": "soccer", "playerIsHome": {"$exists": False}}
        cursor = db.picks.find(query, {"_id": 0, "pickId": 1, "email": 1, "fixtureId": 1, "teamId": 1,
                                        "teamName": 1, "opponentName": 1, "leagueId": 1,
                                        "timestamp": 1, "settledAt": 1})
        picks_missing = await cursor.to_list(None)
        if not picks_missing:
            print("[VENUE BACKFILL] no picks need backfill")
            return

        print(f"[VENUE BACKFILL] starting: {len(picks_missing)} picks missing venue")

        # Build lookup maps
        by_fixture: dict[int, list] = {}
        by_team: dict[int, list] = {}
        for p in picks_missing:
            fid = p.get("fixtureId")
            if fid:
                by_fixture.setdefault(fid, []).append(p)
            elif p.get("teamId"):
                by_team.setdefault(p["teamId"], []).append(p)

        fixture_cache: dict[int, dict] = {}

        async def _fetch_fixture(fid: int) -> dict | None:
            if fid in fixture_cache:
                return fixture_cache[fid]
            res = await api_football_request("fixtures", {"id": fid}) or []
            f = res[0] if res else None
            fixture_cache[fid] = f
            return f

        updated = 0
        skipped = 0

        # Pass 1: fixtureId direct lookups
        for fid, plist in by_fixture.items():
            fixture = await _fetch_fixture(fid)
            if not fixture:
                skipped += len(plist)
                continue
            home_name = fixture.get("teams", {}).get("home", {}).get("name", "")
            away_name = fixture.get("teams", {}).get("away", {}).get("name", "")
            for p in plist:
                team_name = p.get("teamName", "")
                player_is_home = bool(team_name and team_name in home_name)
                await db.picks.update_one(
                    {"pickId": p["pickId"], "email": p.get("email", "")},
                    {"$set": {"playerIsHome": player_is_home, "homeTeam": home_name, "awayTeam": away_name}}
                )
                updated += 1

        # Pass 2: team window lookups — one wide fetch per unique team
        for tid, plist in by_team.items():
            try:
                dates = []
                for p in plist:
                    ts = p.get("settledAt") or p.get("timestamp") or datetime.now(timezone.utc).isoformat()
                    try:
                        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                    except Exception:
                        d = datetime.now(timezone.utc)
                    dates.append(d)
                min_d = min(dates) - timedelta(days=3)
                max_d = max(dates) + timedelta(days=1)
                from_d = min_d.strftime("%Y-%m-%d")
                to_d = max_d.strftime("%Y-%m-%d")

                fixtures = []
                for season in (CURRENT_SEASON, 2026, 2025):
                    res = await api_football_request(
                        "fixtures", {"team": tid, "from": from_d, "to": to_d, "season": season}
                    ) or []
                    fixtures.extend(res)
                    if fixtures:
                        break
                if not fixtures:
                    skipped += len(plist)
                    continue

                # Build a lookup dict by team name substring for fast matching
                fixture_map = {}
                for f in fixtures:
                    home = f.get("teams", {}).get("home", {}).get("name", "")
                    away = f.get("teams", {}).get("away", {}).get("name", "")
                    fixture_map[home.lower()] = f
                    fixture_map[away.lower()] = f

                for p in plist:
                    team_name = (p.get("teamName") or "").lower()
                    opp_name = (p.get("opponentName") or "").lower()
                    fixture = None
                    # Match by team name or opponent name
                    for key in fixture_map:
                        if team_name and team_name in key:
                            fixture = fixture_map[key]
                            break
                        if opp_name and opp_name in key:
                            fixture = fixture_map[key]
                            break
                    if not fixture:
                        skipped += 1
                        continue
                    home_name = fixture.get("teams", {}).get("home", {}).get("name", "")
                    away_name = fixture.get("teams", {}).get("away", {}).get("name", "")
                    player_is_home = bool(p.get("teamName") and p.get("teamName") in home_name)
                    await db.picks.update_one(
                        {"pickId": p["pickId"], "email": p.get("email", "")},
                        {"$set": {"playerIsHome": player_is_home, "homeTeam": home_name, "awayTeam": away_name}}
                    )
                    updated += 1

                print(f"[VENUE BACKFILL] team {tid}: updated batch, total updated so far {updated}")
            except Exception as e:
                print(f"[VENUE BACKFILL] team {tid} failed: {e}")
                skipped += len(plist)

        print(f"[VENUE BACKFILL] done: updated={updated} skipped={skipped}")

    aio.create_task(_run())
    return {"started": True, "message": "Venue backfill running in background"}


@router.get("/picks/analysis")
async def get_pick_analysis(email: str, token: str, pickId: str):
    """Fetch the original prediction analysis for a saved pick."""
    session = await db.sessions.find_one({"email": email.lower(), "session_token": token}, {"_id": 0})
    if not session:
        raise HTTPException(status_code=401, detail="Invalid session")

    pick = await db.picks.find_one({"pickId": pickId, "email": email.lower()}, {"_id": 0})
    if not pick:
        raise HTTPException(status_code=404, detail="Pick not found")

    player_id = pick.get("playerId")
    prop_type = pick.get("propType", "")

    # Fields to return from the prediction
    proj_fields = {
        "_id": 0, "reasoning": 1, "tacticalBreakdown": 1, "explanation": 1,
        "sharpSummary": 1, "scenarioAnalysis": 1, "keyEvidence": 1,
        "matchupOverview": 1, "gameFlowDynamics": 1, "sensitivityTests": 1,
        "subRisk": 1, "uncertaintyNote": 1, "consensusNote": 1,
        "projectedValue": 1, "recommendation": 1, "confidenceScore": 1,
        "confidenceLevel": 1, "confidenceInterval": 1,
        "player": 1, "opponent": 1, "propType": 1, "line": 1,
        "recentSamples": 1, "bayesianMetrics": 1,
        "playerGameLogs": 1, "tacticalAlerts": 1,
        "positionComparison": 1, "h2hPlayerStats": 1,
        "gameScript": 1, "matchFactors": 1,
        "_created": 1,
    }

    prediction = None
    collection = db.predictions

    # Strategy 0: Check ai_pending_jobs for same-day cached AI result.
    # This handles picks saved while AI was still pending — the job finishes
    # after the pick is saved, so the pick doc has no AI text yet.
    try:
        from datetime import timezone as _tz
        _today = datetime.now(_tz.utc).strftime("%Y-%m-%d")
        _pid_or_name = pick.get("playerId") or pick.get("playerName", "")
        _line_val = pick.get("line", "")
        _opp_name = pick.get("opponentName", "")
        _job_ck = f"soc|{_pid_or_name}|{prop_type}|{_line_val}|{_opp_name}|{_today}"
        _job_hit = await db.ai_pending_jobs.find_one(
            {"_k": _job_ck, "done": True, "failed": {"$ne": True}},
            {"_id": 0, "v": 1}
        )
        if _job_hit and _job_hit.get("v"):
            _ai_v = _job_hit["v"]
            if _ai_v.get("tacticalBreakdown") or _ai_v.get("sharpSummary") or _ai_v.get("reasoning"):
                _merged = {**_ai_v}
                for _mf in ("projectedValue", "bayesianMetrics", "gameScript", "moneyline",
                            "tacticalAlerts", "pOver", "pUnder", "confidenceScore", "confidenceLevel"):
                    _mv = pick.get(_mf)
                    if _mv is not None and not _merged.get(_mf):
                        _merged[_mf] = _mv
                return {"found": True, "analysis": _merged}
    except Exception:
        pass

    # Strategy 1: Match by player ID + prop type (most recent)
    if player_id and player_id != 0:
        prediction = await collection.find_one(
            {"player.id": player_id, "propType": prop_type},
            proj_fields,
            sort=[("_created", -1)]
        )

    # Strategy 2: Match by player name + prop type
    if not prediction:
        player_name = pick.get("playerName", "")
        if player_name:
            prediction = await collection.find_one(
                {"player.name": player_name, "propType": prop_type},
                proj_fields,
                sort=[("_created", -1)]
            )

    # Strategy 3: CS2 / Soccer / WTA picks store analysis directly in the pick document
    # because the predictions collection may have been rotated or the lookup missed.
    pick_sport = pick.get("sport", "soccer")
    if not prediction and pick_sport in ("cs2", "soccer", "wta"):
        inline_analysis = {}
        for field in ("sharpSummary", "reasoning", "tacticalBreakdown", "tacticalAlerts",
                      "projectedValue", "recommendation", "confidenceScore", "confidenceLevel",
                      "pOver", "pUnder", "priorMean", "momentumMean", "sampleSize",
                      "streakFlag", "propType", "line", "playerName", "opponentName",
                      "tacticalMetrics", "gameScript", "moneyline"):
            val = pick.get(field)
            if val is not None:
                inline_analysis[field] = val
        if inline_analysis.get("reasoning") or inline_analysis.get("sharpSummary") or inline_analysis.get("tacticalBreakdown"):
            return {"found": True, "analysis": inline_analysis}
        return {"found": False}

    # Strategy 4: check MLB predictions collection when soccer lookup missed
    if not prediction:
        pick_sport = pick.get("sport", "soccer")
        _MLB_PROPS = {
            "pitcher_strikeouts", "innings_pitched", "hits_allowed", "earned_runs",
            "walks_allowed", "pitches_thrown", "batters_faced",
            "hits", "home_runs", "rbi", "walks", "strikeouts", "runs",
            "total_bases", "stolen_bases", "doubles", "plate_appearances",
        }
        if pick_sport == "mlb" or prop_type in _MLB_PROPS:
            mlb_proj_fields = {
                "_id": 0, "reasoning": 1, "sharpSummary": 1,
                "projectedValue": 1, "projection": 1,
                "recommendation": 1, "confidenceScore": 1, "confidenceLevel": 1,
                "confidenceInterval": 1, "pOver": 1, "pUnder": 1,
                "bayesianMetrics": 1, "gameLogs": 1, "hitRates": 1,
                "priorMean": 1, "momentumMean": 1, "momentumLabel": 1,
                "streakFlag": 1, "volatility": 1, "sampleSize": 1,
                "playerName": 1, "propType": 1, "line": 1,
                "generatedAt": 1, "sport": 1,
            }
            if player_id and player_id != 0:
                prediction = await db.mlb_predictions.find_one(
                    {"playerId": player_id, "propType": prop_type},
                    mlb_proj_fields,
                    sort=[("generatedAt", -1)],
                )
            if not prediction and pick.get("playerName"):
                prediction = await db.mlb_predictions.find_one(
                    {
                        "playerName": {"$regex": pick.get("playerName", ""), "$options": "i"},
                        "propType": prop_type,
                    },
                    mlb_proj_fields,
                    sort=[("generatedAt", -1)],
                )

    if not prediction:
        return {"found": False}

    prediction.pop("_id", None)
    return {"found": True, "analysis": prediction}



@router.post("/picks/delete")
async def delete_pick(req: DeletePickRequest):
    session = await db.sessions.find_one({"email": req.email.lower(), "session_token": req.token}, {"_id": 0})
    if not session:
        raise HTTPException(status_code=401, detail="Invalid session")
    await db.picks.delete_one({"pickId": req.pickId, "email": req.email.lower()})
    return {"success": True}


@router.post("/picks/correct")
async def correct_pick(req: CorrectPickRequest):
    """Manual correction for settled picks when API data was wrong."""
    session = await db.sessions.find_one({"email": req.email.lower(), "session_token": req.token}, {"_id": 0})
    if not session:
        raise HTTPException(status_code=401, detail="Invalid session")
    pick = await db.picks.find_one({"pickId": req.pickId, "email": req.email.lower()}, {"_id": 0})
    if not pick:
        raise HTTPException(status_code=404, detail="Pick not found")
    line = pick.get("line", 0)
    rec = pick.get("recommendation", "over")
    if req.actualValue == line:
        result_str = "push"
    elif (rec == "over" and req.actualValue > line) or (rec == "under" and req.actualValue < line):
        result_str = "hit"
    else:
        result_str = "miss"
    await db.picks.update_one(
        {"pickId": req.pickId, "email": req.email.lower()},
        {"$set": {"actualValue": req.actualValue, "result": result_str, "correctedManually": True}}
    )
    return {"success": True, "result": result_str, "actualValue": req.actualValue}



# =============================================
# LIVE TRACKING — Real-time in-game stats
# =============================================

# Soccer stat extraction map
SOCCER_STAT_MAP = {
    "goals": lambda s: s.get("goals", {}).get("total"),
    "assists": lambda s: s.get("goals", {}).get("assists"),
    "shots_assisted": lambda s: s.get("passes", {}).get("key"),
    "pass_attempts": lambda s: s.get("passes", {}).get("total"),
    "passes": lambda s: s.get("passes", {}).get("total"),
    "shots": lambda s: s.get("shots", {}).get("total"),
    "shots_on_target": lambda s: s.get("shots", {}).get("on"),
    "tackles": lambda s: s.get("tackles", {}).get("total"),
    "key_passes": lambda s: s.get("passes", {}).get("key"),
    "saves": lambda s: (s.get("goals", {}).get("saves") or 0),
    "goalie_saves": lambda s: (s.get("goals", {}).get("saves") or 0),
    "interceptions": lambda s: s.get("tackles", {}).get("interceptions"),
    "blocks": lambda s: s.get("tackles", {}).get("blocks"),
    "dribbles": lambda s: s.get("dribbles", {}).get("attempts"),
    "dribbles_success": lambda s: s.get("dribbles", {}).get("success"),
    "fouls_drawn": lambda s: s.get("fouls", {}).get("drawn"),
    "fouls_committed": lambda s: s.get("fouls", {}).get("committed"),
    "crosses": lambda s: s.get("passes", {}).get("crosses"),
    "clearances": lambda s: s.get("tackles", {}).get("clearances"),
    "duels_won": lambda s: s.get("duels", {}).get("won"),
    "yellow_cards": lambda s: s.get("cards", {}).get("yellow"),
}

@router.post("/picks/live-update")
async def live_update_picks(req: LiveUpdateRequest):
    """For each live pick, check if match is live or finished. Return current stats.
    Handles soccer picks only."""
    session = await db.sessions.find_one({"email": req.email.lower(), "session_token": req.token}, {"_id": 0})
    if not session:
        raise HTTPException(status_code=401, detail="Invalid session")

    live_picks = await db.picks.find({"email": req.email.lower(), "status": "live"}, {"_id": 0}).to_list(50)
    if not live_picks:
        return {"updates": []}

    _BDL_SP = {"nba", "wnba", "nhl", "nfl"}
    bdl_picks    = [p for p in live_picks if p.get("sport") in _BDL_SP]
    soccer_picks = [p for p in live_picks if p.get("sport") not in _BDL_SP]

    async def _empty(): return []
    bdl_upd, soccer_upd = await aio.gather(
        _process_bdl_live(bdl_picks, req.email.lower())       if bdl_picks    else _empty(),
        _process_soccer_live(soccer_picks, req.email.lower()) if soccer_picks else _empty(),
        return_exceptions=True,
    )
    updates = []
    if not isinstance(bdl_upd, Exception):
        updates.extend(bdl_upd)
    if not isinstance(soccer_upd, Exception):
        updates.extend(soccer_upd)
    return {"updates": updates}


async def _process_bdl_live(picks: list, email: str) -> list:
    """Live tracking for NBA / WNBA / NHL / NFL picks via BDL APIs.

    Strategy per sport:
      NBA/WNBA : fetch today's games → match by team name → get player stats
      NHL      : fetch today's games → match by team name → get box_score row
      NFL      : fetch today's games → score-only (no live player stats endpoint)

    Returns a list of update dicts with the same schema as _process_soccer_live:
      { pickId, matchStatus, currentValue, pace, hitPct, elapsed,
        period, matchScore, homeTeam, awayTeam, result?, actualValue? }
    """
    BDL_KEY = _os.environ.get("MLB_BDL_API_KEY", "")
    if not BDL_KEY:
        return []

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    results: list = []

    # ── BDL GET helper (shared) ───────────────────────────────────────────
    async def _bdl_get(url: str, params=None) -> dict:
        headers = {"Authorization": BDL_KEY}
        try:
            async with _httpx.AsyncClient(timeout=15) as c:
                r = await c.get(url, headers=headers, params=params or {})
                if r.status_code == 200:
                    return r.json()
                return {}
        except Exception:
            return {}

    # ── Stat prop maps ────────────────────────────────────────────────────
    # NFL  (BDL /nfl/v1/stats — all flat fields on the row itself)
    _NFL_STAT = {
        "passing_yards":        lambda s: s.get("passing_yards"),
        "passing_touchdowns":   lambda s: s.get("passing_touchdowns"),
        "passing_attempts":     lambda s: s.get("passing_attempts"),
        "passing_completions":  lambda s: s.get("passing_completions"),
        "passing_interceptions":lambda s: s.get("passing_interceptions"),
        "rushing_yards":        lambda s: s.get("rushing_yards"),
        "rushing_attempts":     lambda s: s.get("rushing_attempts"),
        "rushing_touchdowns":   lambda s: s.get("rushing_touchdowns"),
        "receptions":           lambda s: s.get("receptions"),
        "receiving_yards":      lambda s: s.get("receiving_yards"),
        "receiving_touchdowns": lambda s: s.get("receiving_touchdowns"),
        "receiving_targets":    lambda s: s.get("receiving_targets"),
        "targets":              lambda s: s.get("receiving_targets"),
        "tackles":              lambda s: s.get("total_tackles"),
        "total_tackles":        lambda s: s.get("total_tackles"),
        "sacks":                lambda s: s.get("defensive_sacks"),
        "defensive_sacks":      lambda s: s.get("defensive_sacks"),
        "interceptions":        lambda s: s.get("defensive_interceptions"),
        "rush_rec_yards":       lambda s: (s.get("rushing_yards") or 0) + (s.get("receiving_yards") or 0),
        "pass_rush_yards":      lambda s: (s.get("passing_yards") or 0) + (s.get("rushing_yards") or 0),
        "anytime_td":           lambda s: (
            (s.get("passing_touchdowns") or 0) +
            (s.get("rushing_touchdowns") or 0) +
            (s.get("receiving_touchdowns") or 0)
        ),
        "kicking_points":       lambda s: s.get("total_points"),
    }
    # NBA / WNBA  (BDL v1 /stats  and  /wnba/v1/player_stats)
    _NBA_STAT = {
        "points":    lambda s: s.get("pts"),
        "rebounds":  lambda s: s.get("reb"),
        "assists":   lambda s: s.get("ast"),
        "steals":    lambda s: s.get("stl"),
        "blocks":    lambda s: s.get("blk"),
        "turnovers": lambda s: s.get("turnover"),
        "threes":    lambda s: s.get("fg3m"),
        "pts_reb_ast": lambda s: (s.get("pts") or 0) + (s.get("reb") or 0) + (s.get("ast") or 0),
        "pts_reb":     lambda s: (s.get("pts") or 0) + (s.get("reb") or 0),
        "pts_ast":     lambda s: (s.get("pts") or 0) + (s.get("ast") or 0),
        "reb_ast":     lambda s: (s.get("reb") or 0) + (s.get("ast") or 0),
    }
    # NHL  (BDL /nhl/v1/box_scores)
    _NHL_STAT = {
        "goals":         lambda s: s.get("goals"),
        "assists":       lambda s: s.get("assists"),
        "points":        lambda s: (s.get("goals") or 0) + (s.get("assists") or 0),
        "shots":         lambda s: s.get("shots_on_goal"),
        "blocked_shots": lambda s: s.get("blocked_shots"),
        "hits":          lambda s: s.get("hits"),
        "saves":         lambda s: s.get("saves"),
        "goals_against": lambda s: s.get("goals_against"),
    }

    # ── Generic live period / regulation duration ─────────────────────────
    # Used for PACE extrapolation.  Soccer-style: extrapolate to 90 min,
    # here we use the regulation-period count in the same way.
    def _regulation(sport: str) -> int:
        return {"nba": 48, "wnba": 40, "nhl": 60, "nfl": 60}.get(sport, 48)

    def _calc_elapsed_pct(sport: str, period: int, time_str: str) -> float:
        """Return elapsed fraction 0-1 through regulation."""
        reg = _regulation(sport)
        if sport in ("nba", "wnba"):
            q_len = 12 if sport == "nba" else 10
            quarters = 4
            mins_per_q = q_len
            if period <= 0:
                return 0.0
            elapsed_full_q = min(period - 1, quarters) * mins_per_q
            # Parse time remaining in current quarter "MM:SS"
            try:
                parts = str(time_str or "").split(":")
                rem = int(parts[0]) + (int(parts[1]) / 60 if len(parts) > 1 else 0)
            except Exception:
                rem = 0.0
            elapsed = elapsed_full_q + (mins_per_q - rem)
            return min(elapsed / reg, 1.0)
        elif sport == "nhl":
            # period 1/2/3, time_remaining "MM:SS"
            if period <= 0:
                return 0.0
            elapsed_full_p = min(period - 1, 3) * 20
            try:
                parts = str(time_str or "20:00").split(":")
                rem = int(parts[0]) + (int(parts[1]) / 60 if len(parts) > 1 else 0)
            except Exception:
                rem = 20.0
            elapsed = elapsed_full_p + (20.0 - rem)
            return min(elapsed / reg, 1.0)
        elif sport == "nfl":
            if period <= 0:
                return 0.0
            elapsed_full_q = min(period - 1, 4) * 15
            return min(elapsed_full_q / reg, 1.0)
        return 0.0

    # ── Name matching helper ──────────────────────────────────────────────
    def _name_matches(query: str, candidate: str) -> bool:
        q = query.lower().strip()
        c = candidate.lower().strip()
        if not q or not c:
            return False
        if q in c or c in q:
            return True
        # Last-name match (min 4 chars)
        q_last = q.rsplit(" ", 1)[-1]
        if len(q_last) >= 4 and q_last in c:
            return True
        return False

    # ── Group picks by sport ──────────────────────────────────────────────
    nba_picks   = [p for p in picks if p.get("sport") == "nba"]
    wnba_picks  = [p for p in picks if p.get("sport") == "wnba"]
    nhl_picks   = [p for p in picks if p.get("sport") == "nhl"]
    nfl_picks   = [p for p in picks if p.get("sport") == "nfl"]

    # ── Fetch today's games for each sport in parallel ────────────────────
    nba_games_task  = _bdl_get("https://api.balldontlie.io/v1/games",
                                [("dates[]", today), ("per_page", 25)]) if nba_picks else aio.sleep(0)
    wnba_games_task = _bdl_get("https://api.balldontlie.io/wnba/v1/games",
                                [("dates[]", today), ("per_page", 25)]) if wnba_picks else aio.sleep(0)
    nhl_games_task  = _bdl_get("https://api.balldontlie.io/nhl/v1/games",
                                [("dates[]", today), ("per_page", 25)]) if nhl_picks else aio.sleep(0)
    nfl_games_task  = _bdl_get("https://api.balldontlie.io/nfl/v1/games",
                                [("dates[]", today), ("per_page", 25)]) if nfl_picks else aio.sleep(0)

    nba_resp, wnba_resp, nhl_resp, nfl_resp = await aio.gather(
        nba_games_task, wnba_games_task, nhl_games_task, nfl_games_task,
        return_exceptions=True,
    )

    def _safe_games(resp) -> list:
        if isinstance(resp, Exception) or not isinstance(resp, dict):
            return []
        return resp.get("data", [])

    nba_games  = _safe_games(nba_resp)
    wnba_games = _safe_games(wnba_resp)
    nhl_games  = _safe_games(nhl_resp)
    nfl_games  = _safe_games(nfl_resp)

    # ── NBA live status helpers ───────────────────────────────────────────
    # BDL NBA: status is either an ISO timestamp (scheduled), "Final", or
    # a live string like "Q2 3:24".
    def _nba_is_live(g: dict) -> bool:
        s = str(g.get("status", ""))
        return bool(s) and s not in ("Final",) and not s.startswith("20")
    def _nba_is_final(g: dict) -> bool:
        return g.get("status") == "Final" or g.get("period", 0) >= 4 and g.get("time") == "Final"
    def _nba_period_time(g: dict):
        # status = "Q3 4:12" → period=3, time="4:12"
        # period field is also set by BDL
        period = g.get("period", 0)
        time_str = g.get("time") or ""
        return period, time_str

    # ── WNBA live status helpers ──────────────────────────────────────────
    # BDL WNBA: status = "pre" | "in" | "final"
    def _wnba_is_live(g: dict) -> bool:
        return g.get("status") == "in"
    def _wnba_is_final(g: dict) -> bool:
        return g.get("status") == "final"

    # ── NHL live status helpers ───────────────────────────────────────────
    # BDL NHL: game_state = "PRE" | "LIVE" | "CRIT" | "OFF"
    def _nhl_is_live(g: dict) -> bool:
        return g.get("game_state") in ("LIVE", "CRIT")
    def _nhl_is_final(g: dict) -> bool:
        return g.get("game_state") == "OFF"

    # ── NFL live status helpers ───────────────────────────────────────────
    def _nfl_is_live(g: dict) -> bool:
        return g.get("status") == "in_progress"
    def _nfl_is_final(g: dict) -> bool:
        return g.get("status") == "Final"

    # ── Generic: find matching game for a pick ────────────────────────────
    def _find_game(games: list, opp_name: str, team_name: str,
                   home_key: str, away_key: str,
                   is_live_fn, is_final_fn) -> dict:
        """Find today's game for a pick by matching team/opponent names."""
        opp_l  = (opp_name  or "").lower()
        team_l = (team_name or "").lower()
        # Prefer live games first
        for g in games:
            if not is_live_fn(g):
                continue
            h = g.get(home_key, {}).get("full_name", "")
            a = g.get(away_key, {}).get("full_name", "")
            if (_name_matches(team_l, h) or _name_matches(team_l, a)
                    or (opp_l and (_name_matches(opp_l, h) or _name_matches(opp_l, a)))):
                return g
        # Fall back to final games
        for g in games:
            if not is_final_fn(g):
                continue
            h = g.get(home_key, {}).get("full_name", "")
            a = g.get(away_key, {}).get("full_name", "")
            if (_name_matches(team_l, h) or _name_matches(team_l, a)
                    or (opp_l and (_name_matches(opp_l, h) or _name_matches(opp_l, a)))):
                return g
        return {}

    # ── Settle helper ─────────────────────────────────────────────────────
    async def _settle_bdl_pick(pick: dict, current_value: float, sport: str,
                               home_score: int, away_score: int,
                               home_name: str, away_name: str,
                               period_label: str) -> dict:
        """Settle a BDL pick and persist to DB."""
        line = pick.get("line", 0)
        rec  = pick.get("recommendation", "over")
        if current_value > line:
            result_str = "hit" if rec == "over" else "miss"
        elif current_value < line:
            result_str = "miss" if rec == "over" else "hit"
        else:
            result_str = "push"

        venue = (pick.get("venue") or "home").lower()
        p_score = home_score if venue == "home" else away_score
        o_score = away_score if venue == "home" else home_score
        match_score = f"{p_score}-{o_score}"

        settled_hit_pct = 100 if result_str == "hit" else (0 if result_str == "miss" else 50)
        settle_set = {
            "status": "settled",
            "result": result_str,
            "actualValue": current_value,
            "hitPct": settled_hit_pct,
            "matchScore": match_score,
            "homeTeam": home_name,
            "awayTeam": away_name,
            "finalHomeGoals": home_score,
            "finalAwayGoals": away_score,
            "settledAt": datetime.now(timezone.utc).isoformat(),
        }
        try:
            await db.picks.update_one(
                {"pickId": pick["pickId"], "email": email},
                {"$set": settle_set}
            )
        except Exception:
            pass

        # Push notification
        try:
            from routes.push import _send_pick_settled_push
            import asyncio as _aio
            _aio.create_task(_send_pick_settled_push(pick, result_str))
        except Exception:
            pass

        # In-app notification
        try:
            from routes.notifications import create_notification
            _emoji = "✅" if result_str == "hit" else ("❌" if result_str == "miss" else "↔️")
            _prop  = pick.get("propType", "").replace("_", " ").title()
            _label = "HIT" if result_str == "hit" else ("MISSED" if result_str == "miss" else "PUSH")
            await create_notification(
                email=email,
                ntype="pick_settled",
                title=f"{_emoji} {pick.get('playerName','?')} {_prop} — {_label}",
                body=f"Actual: {current_value} · Line: {line} · {rec.upper()}",
                data={"pickId": pick.get("pickId"), "sport": sport},
            )
        except Exception:
            pass

        return {
            "pickId": pick["pickId"],
            "matchStatus": "final",
            "currentValue": current_value,
            "actualValue": current_value,
            "pace": current_value,
            "hitPct": settled_hit_pct,
            "period": period_label,
            "matchScore": match_score,
            "homeTeam": home_name,
            "awayTeam": away_name,
            "finalHomeGoals": home_score,
            "finalAwayGoals": away_score,
            "result": result_str,
        }

    # ─────────────────────────────────────────────────────────────────────
    # NBA
    # ─────────────────────────────────────────────────────────────────────
    for pick in nba_picks:
        try:
            game = _find_game(nba_games, pick.get("opponentName",""),
                              pick.get("teamName",""),
                              "home_team", "visitor_team",
                              _nba_is_live, _nba_is_final)
            if not game:
                results.append({"pickId": pick["pickId"], "matchStatus": "scheduled"})
                continue

            game_id   = game["id"]
            home_name = game["home_team"]["full_name"]
            away_name = game["visitor_team"]["full_name"]
            home_score = game.get("home_team_score") or 0
            away_score = game.get("visitor_team_score") or 0
            period, time_str = _nba_period_time(game)
            is_final = _nba_is_final(game)
            is_live  = _nba_is_live(game)
            period_label = "Final" if is_final else (f"Q{period}" if period > 0 else "Pre")

            # Fetch player stats for this game
            p_id = pick.get("playerId")
            player_name = pick.get("playerName", "")
            stats_resp = await _bdl_get(
                "https://api.balldontlie.io/v1/stats",
                [("game_ids[]", game_id), ("per_page", 100)],
            )
            stat_rows = stats_resp.get("data", []) if isinstance(stats_resp, dict) else []

            current_value = None
            for row in stat_rows:
                r_player = row.get("player", {})
                if (p_id and r_player.get("id") == p_id) or \
                        _name_matches(player_name, f"{r_player.get('first_name','')} {r_player.get('last_name','')}"):
                    getter = _NBA_STAT.get(pick.get("propType", ""))
                    if getter:
                        current_value = getter(row)
                    break

            if current_value is None:
                results.append({"pickId": pick["pickId"], "matchStatus": "live" if is_live else "scheduled"})
                continue

            elapsed_frac = _calc_elapsed_pct("nba", period, time_str)
            reg = _regulation("nba")
            elapsed_mins = round(elapsed_frac * reg, 1)
            pace = round((current_value / max(elapsed_frac, 0.01)) * 1.0, 1) if elapsed_frac > 0 else current_value
            hit_pct = _calc_hit_pct(current_value, pick.get("line", 0), pick.get("recommendation","over"),
                                     int(elapsed_mins), reg, is_final, pace)

            if is_final and pick.get("status") != "settled":
                r = await _settle_bdl_pick(pick, current_value, "nba",
                                           home_score, away_score, home_name, away_name, period_label)
                results.append(r)
            else:
                venue = (pick.get("venue") or "home").lower()
                p_score = home_score if venue == "home" else away_score
                o_score = away_score if venue == "home" else home_score
                results.append({
                    "pickId": pick["pickId"],
                    "matchStatus": "final" if is_final else "live",
                    "currentValue": current_value,
                    "pace": pace,
                    "hitPct": hit_pct,
                    "elapsed": elapsed_mins,
                    "period": period_label,
                    "matchScore": f"{p_score}-{o_score}",
                    "homeTeam": home_name,
                    "awayTeam": away_name,
                    "finalHomeGoals": home_score,
                    "finalAwayGoals": away_score,
                })
        except Exception:
            traceback.print_exc()
            results.append({"pickId": pick.get("pickId",""), "matchStatus": "scheduled"})

    # ─────────────────────────────────────────────────────────────────────
    # WNBA  (same structure as NBA, different base URL + endpoints)
    # ─────────────────────────────────────────────────────────────────────
    for pick in wnba_picks:
        try:
            game = _find_game(wnba_games, pick.get("opponentName",""),
                              pick.get("teamName",""),
                              "home_team", "visitor_team",
                              _wnba_is_live, _wnba_is_final)
            if not game:
                results.append({"pickId": pick["pickId"], "matchStatus": "scheduled"})
                continue

            game_id   = game["id"]
            home_name = game["home_team"]["full_name"]
            away_name = game["visitor_team"]["full_name"]
            home_score = game.get("home_score") or 0
            away_score = game.get("away_score") or 0
            period    = game.get("period", 0)
            time_str  = str(game.get("time") or "")
            is_final  = _wnba_is_final(game)
            is_live   = _wnba_is_live(game)
            period_label = "Final" if is_final else (f"Q{period}" if period > 0 else "Pre")

            p_id = pick.get("playerId")
            player_name = pick.get("playerName", "")
            stats_resp = await _bdl_get(
                "https://api.balldontlie.io/wnba/v1/player_stats",
                [("game_ids[]", game_id), ("per_page", 100)],
            )
            stat_rows = stats_resp.get("data", []) if isinstance(stats_resp, dict) else []

            current_value = None
            for row in stat_rows:
                r_player = row.get("player", {})
                if (p_id and r_player.get("id") == p_id) or \
                        _name_matches(player_name, f"{r_player.get('first_name','')} {r_player.get('last_name','')}"):
                    getter = _NBA_STAT.get(pick.get("propType", ""))
                    if getter:
                        current_value = getter(row)
                    break

            if current_value is None:
                results.append({"pickId": pick["pickId"], "matchStatus": "live" if is_live else "scheduled"})
                continue

            elapsed_frac = _calc_elapsed_pct("wnba", period, time_str)
            reg = _regulation("wnba")
            elapsed_mins = round(elapsed_frac * reg, 1)
            pace = round(current_value / max(elapsed_frac, 0.01), 1) if elapsed_frac > 0 else current_value
            hit_pct = _calc_hit_pct(current_value, pick.get("line", 0), pick.get("recommendation","over"),
                                     int(elapsed_mins), reg, is_final, pace)

            if is_final and pick.get("status") != "settled":
                r = await _settle_bdl_pick(pick, current_value, "wnba",
                                           home_score, away_score, home_name, away_name, period_label)
                results.append(r)
            else:
                venue = (pick.get("venue") or "home").lower()
                p_score = home_score if venue == "home" else away_score
                o_score = away_score if venue == "home" else home_score
                results.append({
                    "pickId": pick["pickId"],
                    "matchStatus": "final" if is_final else "live",
                    "currentValue": current_value,
                    "pace": pace,
                    "hitPct": hit_pct,
                    "elapsed": elapsed_mins,
                    "period": period_label,
                    "matchScore": f"{p_score}-{o_score}",
                    "homeTeam": home_name,
                    "awayTeam": away_name,
                    "finalHomeGoals": home_score,
                    "finalAwayGoals": away_score,
                })
        except Exception:
            traceback.print_exc()
            results.append({"pickId": pick.get("pickId",""), "matchStatus": "scheduled"})

    # ─────────────────────────────────────────────────────────────────────
    # NHL
    # ─────────────────────────────────────────────────────────────────────
    for pick in nhl_picks:
        try:
            game = _find_game(nhl_games, pick.get("opponentName",""),
                              pick.get("teamName",""),
                              "home_team", "away_team",
                              _nhl_is_live, _nhl_is_final)
            if not game:
                results.append({"pickId": pick["pickId"], "matchStatus": "scheduled"})
                continue

            game_id    = game["id"]
            home_name  = game["home_team"]["full_name"]
            away_name  = game["away_team"]["full_name"]
            home_score = game.get("home_score") or 0
            away_score = game.get("away_score") or 0
            period     = game.get("period", 0)
            time_rem   = str(game.get("time_remaining") or "20:00")
            is_final   = _nhl_is_final(game)
            is_live    = _nhl_is_live(game)
            period_label = "Final" if is_final else (f"P{period}" if period > 0 else "Pre")

            p_id = pick.get("playerId")
            player_name = pick.get("playerName", "")
            box_resp = await _bdl_get(
                "https://api.balldontlie.io/nhl/v1/box_scores",
                [("game_ids[]", game_id), ("per_page", 100)],
            )
            box_rows = box_resp.get("data", []) if isinstance(box_resp, dict) else []

            current_value = None
            for row in box_rows:
                r_player = row.get("player", {})
                if (p_id and r_player.get("id") == p_id) or \
                        _name_matches(player_name, r_player.get("full_name", "")):
                    getter = _NHL_STAT.get(pick.get("propType", ""))
                    if getter:
                        current_value = getter(row)
                    break

            if current_value is None:
                results.append({"pickId": pick["pickId"], "matchStatus": "live" if is_live else "scheduled"})
                continue

            elapsed_frac = _calc_elapsed_pct("nhl", period, time_rem)
            reg = _regulation("nhl")
            elapsed_mins = round(elapsed_frac * reg, 1)
            pace = round(current_value / max(elapsed_frac, 0.01), 2) if elapsed_frac > 0 else current_value
            hit_pct = _calc_hit_pct(current_value, pick.get("line", 0), pick.get("recommendation","over"),
                                     int(elapsed_mins), reg, is_final, pace)

            if is_final and pick.get("status") != "settled":
                r = await _settle_bdl_pick(pick, current_value, "nhl",
                                           home_score, away_score, home_name, away_name, period_label)
                results.append(r)
            else:
                venue = (pick.get("venue") or "home").lower()
                p_score = home_score if venue == "home" else away_score
                o_score = away_score if venue == "home" else home_score
                results.append({
                    "pickId": pick["pickId"],
                    "matchStatus": "final" if is_final else "live",
                    "currentValue": current_value,
                    "pace": pace,
                    "hitPct": hit_pct,
                    "elapsed": elapsed_mins,
                    "period": period_label,
                    "matchScore": f"{p_score}-{o_score}",
                    "homeTeam": home_name,
                    "awayTeam": away_name,
                    "finalHomeGoals": home_score,
                    "finalAwayGoals": away_score,
                })
        except Exception:
            traceback.print_exc()
            results.append({"pickId": pick.get("pickId",""), "matchStatus": "scheduled"})

    # ─────────────────────────────────────────────────────────────────────
    # NFL  (/nfl/v1/stats?game_ids[]= — full player stat tracking)
    # ─────────────────────────────────────────────────────────────────────
    for pick in nfl_picks:
        try:
            game = _find_game(nfl_games, pick.get("opponentName",""),
                              pick.get("teamName",""),
                              "home_team", "visitor_team",
                              _nfl_is_live, _nfl_is_final)
            if not game:
                results.append({"pickId": pick["pickId"], "matchStatus": "scheduled"})
                continue

            game_id    = game["id"]
            home_name  = game["home_team"]["full_name"]
            away_name  = game["visitor_team"]["full_name"]
            home_score = game.get("home_team_score") or 0
            away_score = game.get("visitor_team_score") or 0
            is_final   = _nfl_is_final(game)
            is_live    = _nfl_is_live(game)

            # Infer current quarter from partial Q scores
            def _nfl_current_quarter(g: dict) -> int:
                for q in range(4, 0, -1):
                    h_key = f"home_team_q{q}"
                    v_key = f"visitor_team_q{q}"
                    if g.get(h_key) is not None or g.get(v_key) is not None:
                        return q
                return 0

            period = _nfl_current_quarter(game) if is_live else (4 if is_final else 0)
            period_label = "Final" if is_final else (f"Q{period}" if period > 0 else "Pre")

            # Fetch player stats for this game
            p_id = pick.get("playerId")
            player_name = pick.get("playerName", "")
            stats_resp = await _bdl_get(
                "https://api.balldontlie.io/nfl/v1/stats",
                [("game_ids[]", game_id), ("per_page", 100)],
            )
            stat_rows = stats_resp.get("data", []) if isinstance(stats_resp, dict) else []

            current_value = None
            for row in stat_rows:
                r_player = row.get("player", {})
                full_name = f"{r_player.get('first_name','')} {r_player.get('last_name','')}".strip()
                if (p_id and r_player.get("id") == p_id) or _name_matches(player_name, full_name):
                    getter = _NFL_STAT.get(pick.get("propType", ""))
                    if getter:
                        current_value = getter(row)
                    break

            venue = (pick.get("venue") or "home").lower()
            p_score = home_score if venue == "home" else away_score
            o_score = away_score if venue == "home" else home_score

            if current_value is None:
                # Game found but no stat row yet — show score context
                results.append({
                    "pickId": pick["pickId"],
                    "matchStatus": "final" if is_final else ("live" if is_live else "scheduled"),
                    "period": period_label,
                    "matchScore": f"{p_score}-{o_score}",
                    "homeTeam": home_name,
                    "awayTeam": away_name,
                    "finalHomeGoals": home_score,
                    "finalAwayGoals": away_score,
                })
                continue

            # NFL elapsed: 15 min per quarter, no live clock from BDL
            elapsed_frac = _calc_elapsed_pct("nfl", period, "")
            reg = _regulation("nfl")
            elapsed_mins = round(elapsed_frac * reg, 1)
            pace = round(current_value / max(elapsed_frac, 0.01), 1) if elapsed_frac > 0 else current_value
            hit_pct = _calc_hit_pct(current_value, pick.get("line", 0), pick.get("recommendation","over"),
                                     int(elapsed_mins), reg, is_final, pace)

            if is_final and pick.get("status") != "settled":
                r = await _settle_bdl_pick(pick, current_value, "nfl",
                                           home_score, away_score, home_name, away_name, period_label)
                results.append(r)
            else:
                results.append({
                    "pickId": pick["pickId"],
                    "matchStatus": "final" if is_final else ("live" if is_live else "scheduled"),
                    "currentValue": current_value,
                    "pace": pace,
                    "hitPct": hit_pct,
                    "elapsed": elapsed_mins,
                    "period": period_label,
                    "matchScore": f"{p_score}-{o_score}",
                    "homeTeam": home_name,
                    "awayTeam": away_name,
                    "finalHomeGoals": home_score,
                    "finalAwayGoals": away_score,
                })
        except Exception:
            traceback.print_exc()
            results.append({"pickId": pick.get("pickId",""), "matchStatus": "scheduled"})

    return results


async def _process_soccer_live(picks: list, email: str) -> list:
    """Route soccer live-update picks: BDL for supported leagues, API-Football otherwise."""
    import soccer_bdl_client as _sbc
    bdl_picks   = [p for p in picks if _sbc.is_bdl_league(p.get("leagueId", 0))]
    other_picks = [p for p in picks if not _sbc.is_bdl_league(p.get("leagueId", 0))]

    results = []
    if bdl_picks:
        bdl_results = await _process_soccer_bdl_live(bdl_picks, email)
        results.extend(bdl_results)
    if other_picks:
        api_results = await _process_api_football_live(other_picks, email)
        results.extend(api_results)
    return results


async def _process_api_football_live(picks: list, email: str) -> list:
    """Live tracking for API-Football leagues (WC, UCL, top European leagues, etc.).

    Three-tier fixture lookup (most specific → broadest):
      T1. teamId available  → fixtures?team={teamId}&live=all  (single team, fastest)
      T2. leagueId available → fixtures?league={leagueId}&live=all + team-name match
      T3. fallback           → fixtures?live=all (all live games) + team-name match

    For each tier, if no live match is found, also checks today's fixtures so picks
    transition to "final" immediately when the match ends.
    """
    today = datetime.now(timezone.utc)
    today_str = today.strftime("%Y-%m-%d")
    yesterday_str = (today - timedelta(days=1)).strftime("%Y-%m-%d")
    tomorrow_str = (today + timedelta(days=1)).strftime("%Y-%m-%d")

    # ── T0: direct lookup by stored fixtureId (most targeted, avoids T1/T2/T3) ──
    # Picks that already have a fixtureId skip team/league/all-live searches
    # entirely — one `fixtures?id=X` call per unique fixture is enough.
    picks_with_fid = [p for p in picks if p.get("fixtureId")]
    picks_no_fid   = [p for p in picks if not p.get("fixtureId")]
    known_fids     = list({p["fixtureId"] for p in picks_with_fid})

    async def _by_fid(fid: int) -> dict | None:
        res = await api_football_request("fixtures", {"id": fid}) or []
        return res[0] if res else None

    # ── Pre-fetch shared caches for picks WITHOUT a stored fixtureId ─────
    unique_team_ids   = {p.get("teamId") or 0 for p in picks_no_fid} - {0}
    unique_league_ids = {p.get("leagueId") or 0 for p in picks_no_fid} - {0}

    async def _team_window(tid: int, from_d: str, to_d: str) -> list:
        """Fetch fixtures for a team across a date window, trying both seasons."""
        _team_leagues = {p.get("leagueId") for p in picks_no_fid if p.get("teamId") == tid}
        _is_nwsl = NWSL_LEAGUE_ID in _team_leagues
        _seasons = [NWSL_SEASON] if _is_nwsl else [2025, 2026]
        _league = NWSL_LEAGUE_ID if _is_nwsl else None
        window_results = await aio.gather(
            *[
                api_football_request(
                    "fixtures",
                    {
                        "team": tid,
                        "from": from_d,
                        "to": to_d,
                        "season": season,
                        **({"league": _league} if _league else {}),
                    },
                )
                for season in _seasons
            ],
            return_exceptions=True,
        )
        _seen: set = set()
        out = []
        for batch in window_results:
            for f in (batch if isinstance(batch, list) else []):
                _fid = f.get("fixture", {}).get("id")
                if _fid and _fid not in _seen:
                    _seen.add(_fid)
                    out.append(f)
        return out

    async def _league_window(lid: int, from_d: str, to_d: str) -> list:
        """Fetch fixtures for a league across a date window, trying both seasons."""
        _seasons = [NWSL_SEASON] if lid == NWSL_LEAGUE_ID else [2025, 2026]
        window_results = await aio.gather(
            *[
                api_football_request(
                    "fixtures",
                    {"league": lid, "from": from_d, "to": to_d, "season": season},
                )
                for season in _seasons
            ],
            return_exceptions=True,
        )
        _seen: set = set()
        out = []
        for batch in window_results:
            for f in (batch if isinstance(batch, list) else []):
                _fid = f.get("fixture", {}).get("id")
                if _fid and _fid not in _seen:
                    _seen.add(_fid)
                    out.append(f)
        return out

    async def _by_team(tid: int) -> list:
        # T1: live team lookup is most specific.
        live = await api_football_request("fixtures", {"team": tid, "live": "all"}) or []
        if live:
            return live
        # Fallback: 3-day window. South American / Mexican kickoffs often land
        # on a different UTC date than the user's local calendar, and the exact
        # "today" date in the API is unreliable. from/to covers yesterday/today/tomorrow.
        return await _team_window(tid, yesterday_str, tomorrow_str)

    async def _by_league(lid: int) -> list:
        # T2: live league lookup.
        live = await api_football_request("fixtures", {"league": lid, "live": "all"}) or []
        if live:
            return live
        # Fallback: 3-day window for the same UTC-date reason.
        return await _league_window(lid, yesterday_str, tomorrow_str)

    async def _all_live() -> list:
        return await api_football_request("fixtures", {"live": "all"}) or []

    async def _empty_list() -> list:
        return []

    # Fetch T0 (by known fid) in parallel with T1/T2/T3 (only for no-fid picks).
    # T3 (all-live) fires only when there are picks without a stored fixtureId.
    fid_fixtures_list, team_lists, league_lists, all_live_fixtures = await aio.gather(
        aio.gather(*[_by_fid(fid) for fid in known_fids]) if known_fids else _empty_list(),
        aio.gather(*[_by_team(tid) for tid in unique_team_ids]) if unique_team_ids else _empty_list(),
        aio.gather(*[_by_league(lid) for lid in unique_league_ids]) if unique_league_ids else _empty_list(),
        _all_live() if picks_no_fid else _empty_list(),
    )
    fid_fix_map:    dict[int, dict | None] = dict(zip(known_fids, fid_fixtures_list or []))
    team_fix_map:   dict[int, list] = dict(zip(unique_team_ids, team_lists or []))
    league_fix_map: dict[int, list] = dict(zip(unique_league_ids, league_lists or []))

    # Youth/reserve league and team-name markers — used to reject youth fixtures
    # when the pick is for a senior player.
    _YOUTH_LEAGUE_MARKERS = {
        "u21", "u20", "u19", "u18", "u17", "u16", "u15",
        "sub-21", "sub-20", "sub-19", "sub-18",
        "youth", "reserve", "reserves", "b team", "segunda b",
        "under-21", "under-20", "under-19", "under-18",
        "under 21", "under 20", "under 19", "under 18",
        "juvenile", "juvenil",
    }
    _YOUTH_TEAM_SUFFIXES = {
        "u21", "u20", "u19", "u18", "u17", "u16",
        "sub 21", "sub 20", "sub 19", "sub 18",
        "sub-21", "sub-20", "sub-19", "sub-18",
        "reserve", "reserves", "youth", "b",
        "under 21", "under-21",
    }

    def _is_youth_fixture(fixture: dict) -> bool:
        """Return True if this fixture belongs to a youth/reserve competition.
        Checks both the league name and both team names for youth markers."""
        league_name = (fixture.get("league", {}).get("name") or "").lower()
        if any(m in league_name for m in _YOUTH_LEAGUE_MARKERS):
            return True
        for side in ("home", "away"):
            tname = (fixture.get("teams", {}).get(side, {}).get("name") or "").lower().strip()
            # Check if the team name ENDS with a youth suffix (e.g. "Cruz Azul U21")
            for suffix in _YOUTH_TEAM_SUFFIXES:
                if tname.endswith(" " + suffix) or tname == suffix:
                    return True
        return False

    def _team_name_in_fixture(fixture: dict, team_name: str) -> bool:
        """True if the pick's (senior) team name matches either side of the fixture.
        Rejects youth/reserve fixtures — a senior player can never play in a U21 match."""
        if _is_youth_fixture(fixture):
            return False
        tn = (team_name or "").lower().strip()
        if not tn:
            return False
        home = (fixture.get("teams", {}).get("home", {}).get("name") or "").lower()
        away = (fixture.get("teams", {}).get("away", {}).get("name") or "").lower()
        return tn in home or home in tn or tn in away or away in tn

    def _strip_youth(fixtures: list) -> list:
        """Remove any youth/reserve fixtures from a list before matching."""
        return [f for f in (fixtures or []) if not _is_youth_fixture(f)]

    # Session-level cache of fixture IDs already identified as youth by T0.
    # Prevents T3 from re-matching the same wrong fixture even when the
    # fixtures?live=all response omits the "U21" suffix from team names.
    _known_youth_fids: set = set()

    def _fix_team_ids(fx: dict) -> tuple[int, int]:
        """Return (home_team_id, away_team_id) for a fixture dict."""
        t = fx.get("teams", {})
        return (t.get("home", {}).get("id", 0), t.get("away", {}).get("id", 0))

    def _team_id_matches(fx: dict, pick_team_id: int) -> bool:
        """True when the pick's teamId is one of the two sides in this fixture.
        Filters out youth/reserve fixtures whose API team IDs differ from the
        senior team — even when the team name looks identical in abbreviated
        live-fixture responses."""
        if not pick_team_id:
            return True  # no teamId stored — allow any match (legacy picks)
        h, a = _fix_team_ids(fx)
        return pick_team_id in (h, a)

    # ── Pass 1: resolve fixture for every pick ───────────────────────────
    pick_fixtures: list[tuple[dict, dict | None]] = []  # (pick, fixture|None)
    for pick in picks:
        team_id   = pick.get("teamId") or 0
        league_id = pick.get("leagueId") or 0
        team_name = pick.get("teamName") or ""
        opp_name  = pick.get("opponentName") or ""
        pick_ts   = pick.get("timestamp")

        fixture = None

        # T0: stored fixtureId → single direct fixture lookup (fastest, no team/league search)
        stored_fid = pick.get("fixtureId")
        if stored_fid and stored_fid in fid_fix_map:
            fixture = fid_fix_map.get(stored_fid)
            # Even T0 must be youth-clean — the stored fixtureId could have been
            # pre-stored against a youth fixture in a previous polling cycle.
            if fixture and _is_youth_fixture(fixture):
                print(f"[YOUTH FILTER T0] Rejected youth fixture fid={stored_fid} for pick {pick.get('playerName')}")
                _known_youth_fids.add(stored_fid)  # block this fid in T3 this cycle
                fixture = None
            elif fixture and not _team_id_matches(fixture, team_id):
                # T0 fixture belongs to a different team entirely (e.g. youth squad
                # that shares the club name but has a different teamId).
                fid_mismatch = stored_fid
                print(f"[TEAMID FILTER T0] fid={fid_mismatch} team IDs {_fix_team_ids(fixture)} ≠ pick teamId={team_id} for {pick.get('playerName')} — rejected")
                _known_youth_fids.add(fid_mismatch)
                fixture = None

        # T1: teamId → direct team lookup (most specific).
        # Also enforce teamId match so the API-Football ?team= result can't bleed
        # in fixtures for related teams (e.g. Cruz Azul U21 showing alongside senior).
        if not fixture and team_id and team_id in team_fix_map:
            t1_fxts = [f for f in _strip_youth(team_fix_map[team_id])
                       if _team_id_matches(f, team_id)
                       and f.get("fixture", {}).get("id") not in _known_youth_fids]
            fixture = _match_soccer_fixture(t1_fxts, opp_name, pick_ts)

        # T2: leagueId → league-wide lookup filtered by team name + teamId
        if not fixture and league_id and league_id in league_fix_map:
            lg_fxts = [f for f in _strip_youth(league_fix_map[league_id])
                       if _team_name_in_fixture(f, team_name)
                       and _team_id_matches(f, team_id)
                       and f.get("fixture", {}).get("id") not in _known_youth_fids]
            fixture = _match_soccer_fixture(lg_fxts, opp_name, pick_ts)
            if not fixture and lg_fxts:
                fixture = _match_soccer_fixture(lg_fxts, "", pick_ts)

        # T3: all live fixtures filtered by team name + teamId (broadest fallback).
        # The teamId check is critical here — fixtures?live=all sometimes returns
        # abbreviated team names (e.g. "Cruz Azul" instead of "Cruz Azul U21"),
        # making _strip_youth() blind to youth fixtures in this tier.
        if not fixture and all_live_fixtures and team_name:
            all_fxts = [f for f in _strip_youth(all_live_fixtures)
                        if _team_name_in_fixture(f, team_name)
                        and _team_id_matches(f, team_id)
                        and f.get("fixture", {}).get("id") not in _known_youth_fids]
            fixture = _match_soccer_fixture(all_fxts, opp_name, pick_ts)

        pick_fixtures.append((pick, fixture))

    # ── Pass 1.5: fixtureId pre-store ─────────────────────────────────────
    # Persist the exact fixtureId for any pick that doesn't have one yet.
    # This is the #1 permanent fix for tracking flakiness: once a fixtureId is
    # stored, future calls use T0 (fixtures?id=X) instead of fuzzy team/league
    # matching, which is fragile across UTC date boundaries and API rate-limits.
    # We store fixtureIds for live/finished matches too, not just NS — the next
    # poll will then settle or track via the bulletproof ID path.
    async def _persist_fid(pick: dict, fid: int) -> None:
        try:
            await db.picks.update_one(
                {"pickId": pick["pickId"], "email": email.lower()},
                {"$set": {"fixtureId": fid}}
            )
            pick["fixtureId"] = fid  # reflect in-memory for MATCH badge
            print(f"[FID-PRESTORE] pickId={pick.get('pickId')} fid={fid}")
        except Exception:
            pass

    _prestore_tasks: list[tuple[dict, int]] = []
    for _pick, _fix in pick_fixtures:
        if _fix is not None or _pick.get("fixtureId"):
            continue
        _tid   = _pick.get("teamId") or 0
        _lid   = _pick.get("leagueId") or 0
        _tname = (_pick.get("teamName") or "").lower().strip()
        _opp   = (_pick.get("opponentName") or "").lower().strip()

        # Try team_fix_map first (most specific), then league_fix_map filtered by team.
        # Apply youth + teamId filters here too — pre-store must never latch onto
        # a youth fixture just because the senior match is hard to find.
        _candidates = [f for f in _strip_youth(team_fix_map.get(_tid, []))
                       if _team_id_matches(f, _tid)
                       and f.get("fixture", {}).get("id") not in _known_youth_fids]
        if not _candidates and _lid:
            _candidates = [f for f in _strip_youth(league_fix_map.get(_lid, []))
                           if _team_name_in_fixture(f, _tname)
                           and _team_id_matches(f, _tid)
                           and f.get("fixture", {}).get("id") not in _known_youth_fids]

        for _cand in _candidates:
            _ss = _cand.get("fixture", {}).get("status", {}).get("short", "")
            # Reject cancelled/postponed fixtures that will never produce stats.
            if _ss in {"CANC", "PST", "ABD", "AWD"}:
                continue
            if _opp and _opp not in ("unknown", "tbd"):
                _h = (_cand.get("teams", {}).get("home", {}).get("name") or "").lower()
                _a = (_cand.get("teams", {}).get("away", {}).get("name") or "").lower()
                if not (_opp in _h or _h in _opp or _opp in _a or _a in _opp):
                    continue
            _fid = _cand.get("fixture", {}).get("id")
            if _fid:
                _prestore_tasks.append((_pick, _fid))
                break  # one fixture per pick

    if _prestore_tasks:
        await aio.gather(*[_persist_fid(p, fid) for p, fid in _prestore_tasks])

    # ── Pre-fetch fixtures/players once per unique fixture ID ─────────────
    # This avoids N separate API calls (one per pick) for the same match
    # when multiple picks (e.g. C. Arcus + Robertson in Haiti vs Scotland)
    # share the same fixture. Rate-limiting is the #1 cause of currentValue=0.
    unique_fixture_ids = list({
        f.get("fixture", {}).get("id")
        for _, f in pick_fixtures
        if f is not None and f.get("fixture", {}).get("id")
    })

    async def _fetch_players(fid: int) -> list:
        try:
            data = await api_football_request("fixtures/players", {"fixture": fid})
            if data:
                print(f"[LIVE] fixtures/players fixture={fid} → {len(data)} teams")
            else:
                print(f"[LIVE] fixtures/players fixture={fid} → empty (rate-limit?)")
            return data or []
        except Exception:
            return []

    player_data_lists = await aio.gather(*[_fetch_players(fid) for fid in unique_fixture_ids])
    players_by_fixture: dict[int, list] = dict(zip(unique_fixture_ids, player_data_lists))

    # ── Pass 2: build update for each pick using pre-fetched player data ──
    results = []
    for pick, fixture in pick_fixtures:
        pick_id = pick.get("pickId", "")
        _db_status = pick.get("status", "pending")
        # When the fixture lookup fails (transient API error/rate-limit), preserve
        # the pick's existing "live" state rather than reverting to "scheduled".
        # A "live" pick that momentarily can't be reached by the API is still live —
        # showing PENDING on a live card confuses the user and defeats tracking.
        _live_fallback = "live" if _db_status == "live" else "scheduled"
        if not fixture:
            # Log the miss so we can see which picks/paths are still failing.
            print(f"[LIVE-MISS] {pick.get('playerName','?')} teamId={pick.get('teamId')} leagueId={pick.get('leagueId')} "
                  f"team='{pick.get('teamName','')}' opp='{pick.get('opponentName','')}' — no fixture found")
            results.append({"pickId": pick_id, "matchStatus": _live_fallback})
            continue

        fid = fixture.get("fixture", {}).get("id")
        prefetched = players_by_fixture.get(fid)  # list or None

        try:
            update = await _build_soccer_update(pick, fixture, email, prefetched_players=prefetched)
            results.append(update)
        except Exception:
            traceback.print_exc()
            results.append({"pickId": pick_id, "matchStatus": _live_fallback})

    return results


async def _process_soccer_bdl_live(picks: list, email: str) -> list:
    """Live tracking for BDL-covered soccer leagues (WC, MLS, EPL, etc.)."""
    import soccer_bdl_client as _sbc

    league_picks: dict = {}
    for pick in picks:
        lid = pick.get("leagueId", 0)
        if lid not in league_picks:
            league_picks[lid] = []
        league_picks[lid].append(pick)

    results = []
    for league_id, picks_for_league in league_picks.items():
        try:
            matches = await _sbc.get_live_and_recent_matches(league_id)
        except Exception:
            matches = []

        for pick in picks_for_league:
            _pick_id = pick.get("pickId", "")
            _db_status = pick.get("status", "pending")
            _live_fallback = "live" if _db_status == "live" else "scheduled"
            try:
                match = _sbc.find_match_for_pick(matches, pick)
                if not match:
                    results.append({"pickId": _pick_id, "matchStatus": _live_fallback})
                    continue
                update = await _build_bdl_soccer_update(pick, match, email, league_id)
                results.append(update)
            except Exception:
                traceback.print_exc()
                results.append({"pickId": _pick_id, "matchStatus": _live_fallback})
    return results


async def _build_bdl_soccer_update(
    pick: dict, match: dict, email: str, league_id: int
) -> dict:
    """Build a live-update / settlement dict for a soccer pick using BDL match data."""
    import soccer_bdl_client as _sbc

    is_live     = match.get("is_live", False)
    is_finished = match.get("is_finished", False)

    home_score     = match.get("home_score", 0) or 0
    away_score     = match.get("away_score", 0) or 0
    home_team_name = match.get("home_team_name", "")
    away_team_name = match.get("away_team_name", "")
    venue          = (pick.get("venue") or "home").lower()
    p_score        = home_score if venue == "home" else away_score
    o_score        = away_score if venue == "home" else home_score
    match_score    = f"{p_score}-{o_score}"

    if not is_live and not is_finished:
        return {
            "pickId": pick.get("pickId", ""),
            "matchStatus": "scheduled",
            "bdlMatchId":  match.get("id"),
            "homeTeam":    home_team_name,
            "awayTeam":    away_team_name,
        }

    elapsed        = 0 if is_live else 90
    current_value  = None
    minutes_played = 90 if is_finished else 0
    minutes_confirmed = not is_finished

    if is_finished:
        prop_type  = pick.get("propType", "")
        stat_field = _sbc.BDL_SOCCER_STAT_MAP.get(prop_type)
        if stat_field:
            current_value, minutes_played, minutes_confirmed = await _sbc.get_player_settled_stat_details(
                league_id, pick.get("playerName", ""), stat_field
            )

    line           = pick.get("line", 0)
    recommendation = pick.get("recommendation", "over")
    pace           = (current_value or 0) if is_finished else 0
    hit_pct        = _calc_hit_pct(
        current_value or 0, line, recommendation,
        elapsed, 90, is_finished, pace
    )

    update = {
        "pickId":        pick.get("pickId", ""),
        "matchStatus":   "final" if is_finished else "live",
        "bdlMatchId":    match.get("id"),
        "elapsed":       elapsed,
        "period":        "FT" if is_finished else "LIVE",
        "currentValue":  current_value if current_value is not None else 0,
        "minutesPlayed": minutes_played,
        "pace":          pace,
        "hitPct":        hit_pct,
        "matchScore":    match_score,
        "homeTeam":      home_team_name,
        "awayTeam":      away_team_name,
        "finalHomeGoals": home_score,
        "finalAwayGoals": away_score,
        "homePoss": None,
        "awayPoss": None,
    }

    if not is_finished:
        return update

    _current_status = pick.get("status", "live")
    if _current_status == "settled":
        update["matchStatus"] = "final"
        return update

    _stat_available    = current_value is not None
    current_value_safe = current_value if current_value is not None else 0

    if not _stat_available and (minutes_confirmed and minutes_played >= 30):
        print(f"[BDL-SETTLE-DEFER] {pick.get('playerName','')} {pick.get('propType','')} — stat unavailable; deferring")
        update["matchStatus"] = "final"
        return update

    # Zero-value guard: BDL Tier-2 stats (passes_total, tackles, etc.) can be
    # None which resolves to 0 above. Never settle a count prop at 0 for a
    # player who played 30+ min — it almost certainly means the stat is missing.
    _BDL_COUNT_PROPS = {
        "pass_attempts", "passes", "crosses", "tackles", "key_passes",
        "shots", "shots_on_target", "interceptions", "blocks", "dribbles",
        "dribbles_success", "fouls_drawn", "fouls_committed", "clearances",
        "duels_won",
    }
    if current_value_safe == 0 and pick.get("propType", "") in _BDL_COUNT_PROPS and (
        not minutes_confirmed or minutes_played >= 30
    ):
        print(f"[BDL-SETTLE-DEFER] {pick.get('playerName','')} {pick.get('propType','')} — BDL Tier-2 stat=0 with {minutes_played} min; likely None/missing, deferring")
        update["matchStatus"] = "final"
        return update

    _DNP_THRESHOLD = 30
    pass_outcome = None
    if minutes_confirmed and minutes_played < _DNP_THRESHOLD:
        result_str        = "dnp"
        update["voidReason"] = f"Player only played {minutes_played} min (min {_DNP_THRESHOLD} required)"
    elif current_value is not None and current_value > 0:
        # Stat evidence is stronger than a missing/estimated minutes field.
        result_str, pass_outcome = _settle_pick_result(current_value, line, pick)
    elif not minutes_confirmed:
        # An estimated 90 or missing minutes field cannot prove participation.
        update["matchStatus"] = "final"
        return update
    else:
        result_str, pass_outcome = _settle_pick_result(current_value_safe, line, pick)

    settled_hit_pct = 100 if result_str == "hit" else (0 if result_str == "miss" else 50)
    if result_str == "dnp":
        settled_hit_pct = 0

    update["result"]      = result_str
    update["actualValue"] = current_value_safe
    update["hitPct"]      = settled_hit_pct
    if pass_outcome:
        update["passOutcome"] = pass_outcome

    _settle_set = {
        "status":         "settled",
        "result":         result_str,
        "actualValue":    current_value_safe,
        "hitPct":         settled_hit_pct,
        "matchScore":     match_score,
        "minutesPlayed":  minutes_played,
        "finalHomeGoals": home_score,
        "finalAwayGoals": away_score,
        "homeTeam":       home_team_name,
        "awayTeam":       away_team_name,
        "settledAt":      datetime.now(timezone.utc).isoformat(),
    }
    if pass_outcome:
        _settle_set["passOutcome"] = pass_outcome
    if update.get("voidReason"):
        _settle_set["voidReason"] = update["voidReason"]

    await db.picks.update_one(
        {"pickId": pick["pickId"], "email": email},
        {"$set": _settle_set}
    )

    try:
        from routes.push import _send_pick_settled_push
        import asyncio as _aio
        _aio.create_task(_send_pick_settled_push(pick, result_str))
    except Exception:
        pass

    try:
        from routes.notifications import create_notification
        _emoji = "✅" if result_str == "hit" else ("❌" if result_str == "miss" else "↔️")
        _prop  = pick.get("propType", "").replace("_", " ").title()
        _label = "HIT" if result_str == "hit" else ("MISSED" if result_str == "miss" else "PUSH")
        await create_notification(
            email=email,
            ntype="pick_settled",
            title=f"{_emoji} {pick.get('playerName','?')} {_prop} — {_label}",
            body=f"Actual: {current_value_safe} · Line: {line} · {recommendation.upper()}",
            data={"pickId": pick.get("pickId"), "playerName": pick.get("playerName"),
                  "propType": pick.get("propType"), "result": result_str,
                  "actualValue": current_value_safe, "line": line,
                  "recommendation": recommendation, "sport": "soccer"},
        )
    except Exception:
        pass

    return update


def _match_soccer_fixture(fixtures: list, opponent_name: str, pick_ts) -> dict:
    """Find the matching fixture for a soccer pick.
    A team can only play ONE game at a time, so:
    - If there's a LIVE game for this team, ALWAYS match it (no opponent check needed)
    - For FINISHED games, use opponent name + time proximity for accuracy."""
    live_statuses = {"1H", "2H", "ET", "BT", "P", "LIVE", "HT"}
    finished_statuses = {"FT", "AET", "PEN"}

    # First pass: find ANY live game (a team can only be in one live match)
    for f in fixtures:
        status_short = f.get("fixture", {}).get("status", {}).get("short", "")
        if status_short in live_statuses:
            return f

    # Second pass: finished games — match by opponent name + time proximity
    opp_lower = (opponent_name or "").lower().strip()
    for f in fixtures:
        status_short = f.get("fixture", {}).get("status", {}).get("short", "")
        if status_short not in finished_statuses:
            continue

        # Try opponent name match if we have one
        if opp_lower and opp_lower != "unknown" and opp_lower != "tbd":
            home_name = f.get("teams", {}).get("home", {}).get("name", "")
            away_name = f.get("teams", {}).get("away", {}).get("name", "")
            if not (opp_lower in home_name.lower() or opp_lower in away_name.lower()):
                continue

        # Check time proximity — DIRECTIONAL (not abs) to prevent settling picks
        # with results from a game that was already OVER when the pick was made.
        if pick_ts:
            try:
                if isinstance(pick_ts, str):
                    pick_dt = datetime.fromisoformat(pick_ts.replace("Z", "+00:00"))
                else:
                    pick_dt = datetime.fromtimestamp(pick_ts / 1000, tz=timezone.utc)
                fix_dt = datetime.fromisoformat(f.get("fixture", {}).get("date", "").replace("Z", "+00:00"))
                # Match end ≈ kickoff + 2h.  If match ended before pick was saved, skip.
                fix_end = fix_dt + timedelta(hours=2)
                if fix_end < pick_dt:
                    continue
                # Pick made more than 14 days before fixture → wrong direction
                if (pick_dt - fix_dt).total_seconds() / 3600 < -336:
                    continue
            except Exception:
                pass
        return f

    return None


async def _fetch_fixture_possession(fixture_id: int, home_id: int, away_id: int) -> tuple:
    """Return (home_poss, away_poss) from fixtures/statistics, or (None, None) on failure."""
    try:
        stats_data = await api_football_request("fixtures/statistics", {"fixture": fixture_id})
        if not stats_data:
            return (None, None)
        h_poss, a_poss = None, None
        for team_stats in stats_data:
            tid = team_stats.get("team", {}).get("id")
            for stat in team_stats.get("statistics", []):
                if stat.get("type") == "Ball Possession":
                    raw = str(stat.get("value", "")).replace("%", "").strip()
                    try:
                        val = int(raw)
                    except (ValueError, TypeError):
                        val = None
                    if val is not None:
                        if tid == home_id:
                            h_poss = val
                        elif tid == away_id:
                            a_poss = val
        return (h_poss, a_poss)
    except Exception:
        return (None, None)


async def _get_team_avg_possession(team_id: int, league_id: int, season: int = CURRENT_SEASON) -> Optional[float]:
    """Return the team's season-average possession % from API-Football fixtures.

    Uses a short-lived cache (6 h) so repeated live-updates of the same opponent
    do not hammer the API. Returns None when the team/fixtures/stats are unavailable.
    """
    if not team_id or not league_id:
        return None

    cache_key = f"team_avg_poss_{team_id}_{league_id}_{season}"
    try:
        cached = await db.team_avg_poss.find_one({"_key": cache_key}, {"_id": 0, "value": 1, "_ts": 1})
        if cached and (datetime.now(timezone.utc).timestamp() - (cached.get("_ts") or 0)) < 6 * 3600:
            return cached.get("value")
    except Exception:
        pass

    try:
        fixtures = await api_football_request("fixtures", {
            "team": team_id,
            "league": league_id,
            "season": season,
            "status": "FT",
        })
        if not fixtures:
            return None

        values = []
        for fx in fixtures[:15]:  # last 15 finished fixtures
            fid = fx.get("fixture", {}).get("id")
            home_id = fx.get("teams", {}).get("home", {}).get("id")
            away_id = fx.get("teams", {}).get("away", {}).get("id")
            if not fid:
                continue
            h_poss, a_poss = await _fetch_fixture_possession(fid, home_id, away_id)
            if team_id == home_id and h_poss is not None:
                values.append(h_poss)
            elif team_id == away_id and a_poss is not None:
                values.append(a_poss)

        if not values:
            return None
        avg = round(sum(values) / len(values), 1)
        try:
            await db.team_avg_poss.update_one(
                {"_key": cache_key},
                {"$set": {"_key": cache_key, "value": avg, "teamId": team_id, "leagueId": league_id,
                          "season": season, "_ts": datetime.now(timezone.utc).timestamp()}},
                upsert=True
            )
        except Exception:
            pass
        return avg
    except Exception:
        return None


async def _build_soccer_update(pick: dict, fixture: dict, email: str, prefetched_players: list | None = None) -> dict:
    """Build the live update response for a soccer pick.

    Args:
        prefetched_players: If provided, skip the fixtures/players API call and use
            this data directly (list of team-player objects from fixtures/players response).
            Pass this when multiple picks share the same fixture to avoid redundant calls.
    """
    fixture_id = fixture.get("fixture", {}).get("id")
    status_short = fixture.get("fixture", {}).get("status", {}).get("short", "")
    elapsed = fixture.get("fixture", {}).get("status", {}).get("elapsed") or 0
    home_goals = fixture.get("goals", {}).get("home", 0) or 0
    away_goals = fixture.get("goals", {}).get("away", 0) or 0
    home_team_name = fixture.get("teams", {}).get("home", {}).get("name", "") or ""
    away_team_name = fixture.get("teams", {}).get("away", {}).get("name", "") or ""
    home_team_id = fixture.get("teams", {}).get("home", {}).get("id")
    away_team_id = fixture.get("teams", {}).get("away", {}).get("id")
    # Store from player's team perspective: player_goals-opponent_goals
    # so "Rennes 3-0 Strasbourg" shows correctly even when Rennes is away
    _pick_venue = (pick.get("venue") or "home").lower()
    _player_goals = home_goals if _pick_venue == "home" else away_goals
    _opp_goals    = away_goals if _pick_venue == "home" else home_goals
    match_score = f"{_player_goals}-{_opp_goals}"

    live_statuses = {"1H", "2H", "ET", "BT", "P", "LIVE", "HT"}
    finished_statuses = {"FT", "AET", "PEN"}
    is_live = status_short in live_statuses
    is_finished = status_short in finished_statuses

    if not is_live and not is_finished:
        return {"pickId": pick["pickId"], "matchStatus": "scheduled", "fixtureId": fixture_id}

    # Fetch player stats + fixture possession in parallel.
    # If prefetched_players was supplied by the caller (e.g. from _process_api_football_live
    # which batches one fixtures/players call per unique fixture), skip the API round-trip.
    if prefetched_players is not None:
        player_stats_data = prefetched_players
        (home_poss, away_poss) = await _fetch_fixture_possession(fixture_id, home_team_id, away_team_id)
    else:
        player_stats_data, (home_poss, away_poss) = await aio.gather(
            api_football_request("fixtures/players", {"fixture": fixture_id}),
            _fetch_fixture_possession(fixture_id, home_team_id, away_team_id),
        )
    # Opponent season-average possession for richer post-match context
    _pick_venue = (pick.get("venue") or "home").lower()
    _opp_team_id = away_team_id if _pick_venue == "home" else home_team_id
    opp_avg_poss = await _get_team_avg_possession(_opp_team_id, pick.get("leagueId"), CURRENT_SEASON)
    current_value = None
    minutes_played = 0
    _player_found_in_api = False  # True only when this player appears in fixtures/players response

    if player_stats_data:
        player_id = pick.get("playerId")
        player_name = (pick.get("playerName") or "").lower().strip()
        # Pre-compute name parts for flexible matching
        pname_parts = player_name.split()
        pname_last = pname_parts[-1] if pname_parts else player_name
        pname_initial = (pname_parts[0][0] + ".") if pname_parts else ""  # "J."

        def _player_name_matches(api_name: str) -> bool:
            """Match player names flexibly: full name, initial+last, last name only."""
            n = api_name.lower().strip()
            # Full name match (either direction)
            if player_name and (player_name in n or n in player_name):
                return True
            # Last name match: "tverskov" in "j. tverskov"
            if pname_last and len(pname_last) >= 4 and pname_last in n:
                return True
            # Initial + last match: "j. tverskov" matches "jeppe tverskov"
            if pname_initial and pname_last:
                if f"{pname_initial} {pname_last}" in n or n.startswith(pname_initial) and pname_last in n:
                    return True
            return False

        for team_data in player_stats_data:
            for p in team_data.get("players", []):
                p_id = p.get("player", {}).get("id")
                p_name = (p.get("player", {}).get("name") or "")
                # Match by ID first, fallback to flexible name match
                if p_id == player_id or (player_name and _player_name_matches(p_name)):
                    _player_found_in_api = True
                    pstats = p.get("statistics", [{}])[0] if p.get("statistics") else {}
                    minutes_played = pstats.get("games", {}).get("minutes") or 0
                    getter = SOCCER_STAT_MAP.get(pick.get("propType", ""))
                    if getter:
                        current_value = getter(pstats)
                    break
            if _player_found_in_api:  # stop once we've located the player (even if stat is None)
                break

    # Keep None distinct from 0: None = stat not in API response, 0 = valid zero value.
    # If stat is truly unavailable, don't settle now — the background loop will retry.
    # Do NOT force None→0 here: the frontend uses null to suppress the "NOW: 0" display
    # (which would be misleading when the stat simply isn't available in this polling cycle).
    # Pace and hitPct are only computed when we have a real number.
    _stat_available = current_value is not None
    line = pick.get("line", 0)
    recommendation = pick.get("recommendation", "over")
    pass_outcome = None

    # Pace (extrapolate to 90 min) — only meaningful when we have a real stat value
    effective_elapsed = max(elapsed, 1)
    if current_value is not None:
        pace = round((current_value / effective_elapsed) * 90, 1) if effective_elapsed > 0 else 0
        hit_pct = _calc_hit_pct(current_value, line, recommendation, elapsed, 90, is_finished, pace)
    else:
        pace = None
        hit_pct = None

    # Live pace-divergence warning — surfaces when the in-match trend is
    # running strongly against the pre-match recommendation (e.g. a fullback
    # forced into a possession-dominant role after the opponent sits back on
    # an early lead). hitPct already encodes "probability the recommendation
    # still hits" adjusted for live pace; a low value here means the pre-match
    # projection is on track to flip. Gated on elapsed>=15 to avoid noise from
    # tiny early-game samples, and only fires while the pick is still live.
    pace_mismatch = False
    pace_warning = None
    if is_live and not is_finished and elapsed >= 15 and hit_pct is not None and hit_pct <= 25:
        opposite = "UNDER" if (recommendation or "").lower() == "over" else "OVER"
        pace_mismatch = True
        pace_warning = f"Pace trending {opposite} — on pace for {pace:.0f} ({hit_pct}% chance {(recommendation or '').upper()} still hits)"

    update = {
        "pickId": pick["pickId"],
        "matchStatus": "final" if is_finished else "live",
        "fixtureId": fixture_id,
        "elapsed": elapsed,
        "period": status_short,
        "currentValue": current_value,
        "minutesPlayed": minutes_played,
        "pace": pace,
        "hitPct": hit_pct,
        "paceMismatch": pace_mismatch,
        "paceWarning": pace_warning,
        "matchScore": match_score,
        "homeTeam": home_team_name,
        "awayTeam": away_team_name,
        "finalHomeGoals": home_goals,
        "finalAwayGoals": away_goals,
        "homePoss": home_poss,
        "awayPoss": away_poss,
        "oppAvgPoss": opp_avg_poss,
    }

    if is_finished:
        # Guard: never re-settle a pick that the background loop already settled
        _current_status = pick.get("status", "live")
        if _current_status == "settled":
            update["matchStatus"] = "final"
            return update

        # DNP / not-in-squad settlement: the match is finished and the player
        # does not appear in the fixtures/players response. They did not make the
        # matchday squad — settle as push/DNP immediately instead of leaving
        # the pick stuck in "live".
        if not _player_found_in_api:
            print(f"[SETTLE-DNP] {pick.get('playerName','')} {pick.get('propType','')} — not in finished fixture squad")
            update["result"] = "dnp"
            update["actualValue"] = None
            update["minutesPlayed"] = 0
            update["hitPct"] = 0
            update["voidReason"] = "Player not in matchday squad"
            # Persist the settlement so the card moves out of the Live tab
            _persist_settlement = {
                "status": "settled",
                "result": "dnp",
                "actualValue": None,
                "hitPct": 0,
                "matchScore": match_score,
                "minutesPlayed": 0,
                "finalHomeGoals": home_goals,
                "finalAwayGoals": away_goals,
                "homeTeam": home_team_name,
                "awayTeam": away_team_name,
                "settledAt": datetime.now(timezone.utc).isoformat(),
                "settledBy": "live_dnp",
                "voidReason": "Player not in matchday squad",
            }
            if home_poss is not None:
                _persist_settlement["homePoss"] = home_poss
            if away_poss is not None:
                _persist_settlement["awayPoss"] = away_poss
            if opp_avg_poss is not None:
                _persist_settlement["oppAvgPoss"] = opp_avg_poss
            await db.picks.update_one(
                {"pickId": pick["pickId"], "status": {"$ne": "settled"}},
                {"$set": _persist_settlement}
            )
            try:
                from routes.push import _notify_pick_settled
                await _notify_pick_settled(pick, "dnp")
            except Exception as _pe:
                print(f"[SETTLE-DNP] push error: {_pe}")
            return update

        # If stat came back as None (API didn't return the field), defer to background loop
        if not _stat_available and minutes_played >= 30:
            print(f"[SETTLE-DEFER] {pick.get('playerName','')} {pick.get('propType','')} — stat unavailable despite {minutes_played} min played; deferring to background loop")
            update["matchStatus"] = "final"
            return update

        # Zero-value guard for count props: a field player who played 30+ min
        # will never have 0 pass attempts, crosses, tackles, etc. A zero return
        # almost always means the stat hasn't been populated yet by the API.
        # Defer settlement and let the background loop retry with fresh data.
        _COUNT_PROPS = {
            "pass_attempts", "passes", "crosses", "tackles", "key_passes",
            "shots", "shots_on_target", "interceptions", "blocks", "dribbles",
            "dribbles_success", "fouls_drawn", "fouls_committed", "clearances",
            "duels_won",
        }
        if current_value == 0 and pick.get("propType", "") in _COUNT_PROPS and minutes_played >= 30:
            print(f"[SETTLE-DEFER] {pick.get('playerName','')} {pick.get('propType','')} — stat=0 with {minutes_played} min played; likely unpopulated, deferring")
            update["matchStatus"] = "final"
            return update

        # DNP / early-sub void guard — industry standard: < 30 min = DNP
        _DNP_THRESHOLD = 30
        if minutes_played < _DNP_THRESHOLD:
            # Some leagues (e.g. NWSL) return minutes=None for players who played
            # the full game.  The `or 0` above converts that to 0, which would
            # incorrectly trip this DNP guard.  A non-zero stat value is definitive
            # proof the player participated — settle as hit/miss, not DNP.
            if current_value is not None and current_value > 0:
                print(f"[SETTLE] {pick.get('playerName','')} {pick.get('propType','')} "
                      f"— minutes={minutes_played} but stat={current_value} (API minutes unreliable); settling normally")
                result_str, pass_outcome = _settle_pick_result(current_value, line, pick)
            else:
                result_str = "dnp"
                update["voidReason"] = f"Player only played {minutes_played} min (min {_DNP_THRESHOLD} required)"
        else:
            if current_value is None:
                # Stat not available yet — defer to the background auto-settle loop
                print(f"[SETTLE-DEFER] {pick.get('playerName','')} {pick.get('propType','')} "
                      f"— stat=None at FT; deferring to background loop")
                update["matchStatus"] = "final"
                return update
            result_str, pass_outcome = _settle_pick_result(current_value, line, pick)
        update["result"] = result_str
        update["actualValue"] = current_value
        # Store hitPct so settled pick cards show 100%/0%/50% instead of "—"
        settled_hit_pct = 100 if result_str == "hit" else (0 if result_str == "miss" else 50)
        if result_str == "dnp":
            settled_hit_pct = 0
        update["hitPct"] = settled_hit_pct
        # Capture final score + scenario bucket for scenario_priors mining
        try:
            from game_script_engine import bucket_from_final_score
            _scen_bucket = bucket_from_final_score(home_goals, away_goals)
        except Exception:
            _scen_bucket = None
        _settle_set = {"status": "settled", "result": result_str, "actualValue": current_value,
                      "hitPct": settled_hit_pct, "matchScore": match_score,
                      "minutesPlayed": minutes_played,
                      "finalHomeGoals": home_goals,
                      "finalAwayGoals": away_goals,
                      "homeTeam": home_team_name,
                      "awayTeam": away_team_name,
                      "scenarioBucket": _scen_bucket,
                      "settledAt": datetime.now(timezone.utc).isoformat()}
        if pass_outcome:
            _settle_set["passOutcome"] = pass_outcome
        # Persist voidReason to DB so the consistency fixer doesn't re-revert DNP pushes
        if update.get("voidReason"):
            _settle_set["voidReason"] = update["voidReason"]
        if home_poss is not None:
            _settle_set["homePoss"] = home_poss
        if away_poss is not None:
            _settle_set["awayPoss"] = away_poss
        if opp_avg_poss is not None:
            _settle_set["oppAvgPoss"] = opp_avg_poss
        await db.picks.update_one(
            {"pickId": pick["pickId"], "email": email},
            {"$set": _settle_set}
        )

        # Push notification
        try:
            from routes.push import _send_pick_settled_push
            import asyncio as _aio
            _aio.create_task(_send_pick_settled_push(pick, result_str))
        except Exception:
            pass
        # ── In-app notification ──────────────────────────────────────────────
        try:
            from routes.notifications import create_notification
            _emoji = "✅" if result_str == "hit" else ("❌" if result_str == "miss" else "↔️")
            _prop  = pick.get("propType", "").replace("_", " ").title()
            _label = "HIT" if result_str == "hit" else ("MISSED" if result_str == "miss" else "PUSH")
            await create_notification(
                email=email,
                ntype="pick_settled",
                title=f"{_emoji} {pick.get('playerName','?')} {_prop} — {_label}",
                body=f"Actual: {current_value} · Line: {line} · {recommendation.upper()}",
                data={
                    "pickId":         pick.get("pickId"),
                    "playerName":     pick.get("playerName"),
                    "propType":       pick.get("propType"),
                    "result":         result_str,
                    "actualValue":    current_value,
                    "line":           line,
                    "recommendation": recommendation,
                    "sport":          "soccer",
                },
            )
        except Exception:
            pass
    return update


# Basketball functions removed — Soccer only


# =============================================
# SHARED HELPERS
# =============================================

def _calc_hit_pct(current_value, line, recommendation, elapsed, total_minutes, is_finished, pace):
    """Calculate hit probability percentage."""
    rec = (recommendation or "").lower()
    if is_finished:
        if current_value == line:
            return 50
        return 100 if ((rec == "over" and current_value > line) or
                       (rec == "under" and current_value < line)) else 0

    progress = elapsed / max(total_minutes, 1)
    if rec == "over":
        if pace > line * 1.3:
            return min(95, round(60 + progress * 35))
        elif pace > line:
            return min(85, round(50 + progress * 30))
        elif pace > line * 0.7:
            return max(15, round(40 - (line - pace) / max(line, 1) * 30))
        else:
            return max(5, round(20 - progress * 15))
    else:
        if pace < line * 0.7:
            return min(95, round(60 + progress * 35))
        elif pace < line:
            return min(85, round(50 + progress * 30))
        elif pace < line * 1.3:
            return max(15, round(40 - (pace - line) / max(line, 1) * 30))
        else:
            return max(5, round(20 - progress * 15))


def _settle_result(current_value, line, recommendation):
    """Determine if a pick hit, missed, or pushed."""
    rec = (recommendation or "").lower()
    if current_value == line:
        return "push"
    elif (current_value > line and rec == "over") or \
         (current_value < line and rec == "under"):
        return "hit"
    else:
        return "miss"


def _settle_pick_result(current_value, line, pick):
    """Return (stored_result, avoided_side_result) for actionable/PASS picks.

    PASS records remain calibration-only in the ledger.  Their original lean
    is evaluated and stored separately so they can train avoidance analysis
    without being counted as normal wager wins or losses.
    """
    recommendation = str(pick.get("recommendation") or "over").lower()
    if recommendation == "pass":
        lean = str(
            pick.get("passLeaning")
            or (pick.get("skipDetails") or {}).get("direction")
            or ""
        ).lower()
        if lean in {"over", "under"} and current_value is not None:
            return "pass", _settle_result(current_value, line, lean)
        return "pass", None
    return _settle_result(current_value, line, recommendation), None


@router.post("/settle-picks")
async def settle_picks(req: SettlePicksRequest):
    """Check match results and settle picks that have finished."""
    settled = []
    for pick in req.picks:
        if pick.get("status") != "live":
            continue

        sport = pick.get("sport", "soccer")
        player_id = pick.get("player", {}).get("id", 0)
        team_name = pick.get("player", {}).get("team", "")
        prop_type = pick.get("propType", "")
        opponent = pick.get("opponent", "")
        league_id = pick.get("_request", {}).get("leagueId", 39)

        try:
            team_id = pick.get("_request", {}).get("teamId", 0)
            settled_result = await _settle_soccer_pick(pick, team_id, player_id, opponent, prop_type, league_id)

            if settled_result:
                settled.append(settled_result)
        except Exception:
            continue

    return {"settled": settled}


async def _settle_soccer_pick(pick, team_id, player_id, opponent, prop_type, league_id):
    """Settle a soccer pick — BDL path for BDL leagues, legacy path otherwise."""
    import soccer_bdl_client as _sbc
    if _sbc.is_bdl_league(league_id):
        matches = await _sbc.get_live_and_recent_matches(league_id)
        match   = _sbc.find_match_for_pick(matches, pick)
        if not match or not match.get("is_finished"):
            return None
        stat_field = _sbc.BDL_SOCCER_STAT_MAP.get(prop_type)
        if not stat_field:
            return None
        actual_value, minutes_played, minutes_confirmed = await _sbc.get_player_settled_stat_details(
            league_id, pick.get("playerName", ""), stat_field
        )
        if actual_value is None and not (minutes_confirmed and minutes_played < 30):
            return None
        home_goals = match.get("home_score", 0) or 0
        away_goals = match.get("away_score", 0) or 0
        venue      = (pick.get("venue") or "home").lower()
        p_goals    = home_goals if venue == "home" else away_goals
        o_goals    = away_goals if venue == "home" else home_goals
        _DNP_THRESHOLD = 30
        if minutes_confirmed and minutes_played < _DNP_THRESHOLD and not (
            actual_value is not None and actual_value > 0
        ):
            return {
                "pickId": pick.get("id"), "status": "settled", "result": "dnp",
                "actualValue": actual_value, "minutesPlayed": minutes_played,
                "voidReason": f"Player only played {minutes_played} min (min {_DNP_THRESHOLD} required)",
                "matchScore": f"{p_goals}-{o_goals}",
                "homeTeam": match.get("home_team_name", ""),
                "awayTeam": match.get("away_team_name", ""),
                "finalHomeGoals": home_goals, "finalAwayGoals": away_goals,
            }
        result_str, pass_outcome = _settle_pick_result(actual_value, pick.get("line", 0), pick)
        return {
            "pickId": pick.get("id"), "status": "settled", "result": result_str,
            "actualValue": actual_value, "minutesPlayed": minutes_played,
            "matchScore": f"{p_goals}-{o_goals}",
            "homeTeam": match.get("home_team_name", ""),
            "awayTeam": match.get("away_team_name", ""),
            "finalHomeGoals": home_goals, "finalAwayGoals": away_goals,
            **({"passOutcome": pass_outcome} if pass_outcome else {}),
        }

    # ── Legacy API-Football path for non-BDL leagues ──────────────────────────
    if not team_id:
        _player_seasons = [NWSL_SEASON] if league_id == NWSL_LEAGUE_ID else [CURRENT_SEASON, CURRENT_SEASON + 1]
        for s in _player_seasons:
            try:
                pdata = await api_football_request("players", {"id": player_id, "season": s, "league": league_id})
                if pdata:
                    stats_list = pdata[0].get("statistics", [])
                    if stats_list:
                        team_id = stats_list[-1]["team"]["id"]
                        break
            except Exception:
                continue

    if not team_id:
        return None

    pick_timestamp = pick.get("timestamp", 0)
    pick_created = datetime.fromtimestamp(pick_timestamp / 1000, tz=timezone.utc) if isinstance(pick_timestamp, (int, float)) and pick_timestamp else datetime.min.replace(tzinfo=timezone.utc)

    recent = None
    _fixture_seasons = [NWSL_SEASON] if league_id == NWSL_LEAGUE_ID else [CURRENT_SEASON + 1, CURRENT_SEASON]
    for s in _fixture_seasons:
        try:
            _p_from = (datetime.now(timezone.utc) - timedelta(days=90)).strftime("%Y-%m-%d")
            _p_to   = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            _fix_params = {"team": team_id, "from": _p_from, "to": _p_to, "season": s}
            if league_id:
                _fix_params["league"] = league_id
            data = await api_football_request("fixtures", _fix_params)
            if data:
                for f in data:
                    home = f.get("teams", {}).get("home", {}).get("name", "")
                    away = f.get("teams", {}).get("away", {}).get("name", "")
                    status = f.get("fixture", {}).get("status", {}).get("short", "")
                    if status not in ("FT", "AET", "PEN"):
                        continue
                    if not (opponent.lower() in home.lower() or opponent.lower() in away.lower()):
                        continue
                    # Time guard: finished fixture must have ended after the pick was
                    # saved.  kickoff+3h is not enough — a Germany pick at 14:00 can
                    # match a Germany game that kicked off at 11:00 and finished at 13:00.
                    fix_date_str = f.get("fixture", {}).get("date", "")
                    if fix_date_str and pick_created != datetime.min.replace(tzinfo=timezone.utc):
                        try:
                            fix_dt = datetime.fromisoformat(fix_date_str.replace("Z", "+00:00"))
                            fix_end = fix_dt + timedelta(hours=2)  # match end ≈ kickoff + 2h
                            if fix_end < pick_created:
                                continue  # Match was over before pick was saved
                        except Exception:
                            pass
                    recent = f
                    break
                if recent:
                    break
        except Exception:
            continue

    if not recent:
        return None

    fixture_id = recent.get("fixture", {}).get("id")
    fixture_date = recent.get("fixture", {}).get("date", "")
    fixture_players = await api_football_request("fixtures/players", {"fixture": fixture_id})
    actual_value = None

    minutes_played = 0
    if fixture_players:
        for team_data in fixture_players:
            for p in team_data.get("players", []):
                if p.get("player", {}).get("id") == player_id:
                    pstats = p.get("statistics", [{}])[0]
                    minutes_played = pstats.get("games", {}).get("minutes") or 0
                    getter = SOCCER_STAT_MAP.get(prop_type)
                    if getter:
                        actual_value = getter(pstats)
                    break
            if actual_value is not None or minutes_played:
                break

    home_goals = recent.get("goals", {}).get("home", 0) or 0
    away_goals = recent.get("goals", {}).get("away", 0) or 0
    home_team_name = recent.get("teams", {}).get("home", {}).get("name", "") or ""
    away_team_name = recent.get("teams", {}).get("away", {}).get("name", "") or ""
    home_team_id = recent.get("teams", {}).get("home", {}).get("id")
    away_team_id = recent.get("teams", {}).get("away", {}).get("id")
    home_poss, away_poss = await _fetch_fixture_possession(fixture_id, home_team_id, away_team_id)
    _settle_venue = (pick.get("venue") or "home").lower()
    _settle_opp_id = away_team_id if _settle_venue == "home" else home_team_id
    settle_opp_avg_poss = await _get_team_avg_possession(_settle_opp_id, pick.get("leagueId"), CURRENT_SEASON)

    # DNP / early-sub void guard — players with < 30 min get DNP, not hit/miss
    _DNP_THRESHOLD = 30
    if minutes_played < _DNP_THRESHOLD and actual_value is not None and actual_value > 0:
        # A populated positive stat proves the player participated even if the
        # provider omitted minutes.
        pass
    elif minutes_played < _DNP_THRESHOLD and (minutes_played > 0 or actual_value is not None):
        return {
            "pickId": pick.get("id"),
            "status": "settled",
            "result": "dnp",
            "actualValue": actual_value,
            "minutesPlayed": minutes_played,
            "voidReason": f"Player only played {minutes_played} min (min {_DNP_THRESHOLD} required)",
            "fixtureDate": fixture_date,
            "matchScore": f"{home_goals}-{away_goals}",
            "homeTeam": home_team_name,
            "awayTeam": away_team_name,
            "finalHomeGoals": home_goals,
            "finalAwayGoals": away_goals,
            "homePoss": home_poss,
            "awayPoss": away_poss,
            "oppAvgPoss": settle_opp_avg_poss,
        }

    if actual_value is not None:
        # Zero-value guard: count stats should never be 0 for a player who
        # played 30+ minutes. Zero means the API hasn't populated the stat yet.
        # Return None to defer so the background loop retries with fresh data.
        _COUNT_PROPS_SETTLE = {
            "pass_attempts", "passes", "crosses", "tackles", "key_passes",
            "shots", "shots_on_target", "interceptions", "blocks", "dribbles",
            "dribbles_success", "fouls_drawn", "fouls_committed", "clearances",
            "duels_won",
        }
        if actual_value == 0 and prop_type in _COUNT_PROPS_SETTLE and minutes_played >= 30:
            print(f"[SETTLE-DEFER] {pick.get('playerName','')} {prop_type} — stat=0 with {minutes_played} min; likely unpopulated, deferring")
            return None

        line = pick.get("line", 0)
        recommendation = pick.get("recommendation", "over")
        result_str, pass_outcome = _settle_pick_result(actual_value, line, pick)
        return {
            "pickId": pick.get("id"),
            "status": "settled",
            "result": result_str,
            "actualValue": actual_value,
            "minutesPlayed": minutes_played,
            "fixtureDate": fixture_date,
            "matchScore": f"{home_goals}-{away_goals}",
            "homeTeam": home_team_name,
            "awayTeam": away_team_name,
            "finalHomeGoals": home_goals,
            "finalAwayGoals": away_goals,
            "homePoss": home_poss,
            "awayPoss": away_poss,
            "oppAvgPoss": settle_opp_avg_poss,
            **({"passOutcome": pass_outcome} if pass_outcome else {}),
        }

    return None


async def _settle_cs2_pick(pick: dict) -> Optional[dict]:
    """
    Auto-settle a CS2 pick once the match is finished.
    Fetches the player's actual maps 1-2 kills (or other stat) from the
    BDL CS2 API and compares against the saved line.
    Returns a settlement dict or None if the match isn't finished yet.
    """
    team_id       = pick.get("teamId")
    player_id     = pick.get("playerId")
    opponent_name = pick.get("opponentName", "")
    prop_type     = pick.get("propType", "maps_1_2_kills")
    line          = pick.get("line", 0)
    recommendation = pick.get("recommendation", "over")
    timestamp     = pick.get("timestamp", "")

    if not team_id or not player_id or not opponent_name:
        return None

    # Normalise timestamp to ISO string
    if isinstance(timestamp, (int, float)) and timestamp > 0:
        ts_iso = datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc).isoformat()
    elif isinstance(timestamp, str) and timestamp:
        ts_iso = timestamp
    else:
        ts_iso = datetime.now(timezone.utc).isoformat()

    try:
        result = await _cs2_client.get_cs2_completed_match_result(
            team_id=int(team_id),
            player_id=int(player_id),
            opponent_name=opponent_name,
            prop_type=prop_type,
            after_iso=ts_iso,
        )
    except Exception as e:
        print(f"[CS2 SETTLE] error for {pick.get('playerName','?')}: {e}")
        return None

    if not result or result.get("actualValue") is None:
        return None

    actual_value = result["actualValue"]
    result_str, pass_outcome = _settle_pick_result(actual_value, line, pick)
    hit_pct      = 100 if result_str == "hit" else (0 if result_str == "miss" else 50)
    now_iso      = datetime.now(timezone.utc).isoformat()

    settle_set = {
        "status":      "settled",
        "result":      result_str,
        "actualValue": actual_value,
        "hitPct":      hit_pct,
        "matchScore":  result.get("matchScore"),
        "settledAt":   now_iso,
    }
    if pass_outcome:
        settle_set["passOutcome"] = pass_outcome

    await db.picks.update_one(
        {"pickId": pick["pickId"], "email": pick.get("email", "")},
        {"$set": settle_set},
    )

    # Push notification
    try:
        from routes.push import _send_pick_settled_push
        import asyncio as _aio
        _aio.create_task(_send_pick_settled_push(pick, result_str))
    except Exception:
        pass

    # ── In-app notification ──────────────────────────────────────────────────
    try:
        from routes.notifications import create_notification
        _emoji = "✅" if result_str == "hit" else ("❌" if result_str == "miss" else "↔️")
        _prop  = prop_type.replace("_", " ").title()
        _label = "HIT" if result_str == "hit" else ("MISSED" if result_str == "miss" else "PUSH")
        await create_notification(
            email=pick.get("email", ""),
            ntype="pick_settled",
            title=f"{_emoji} {pick.get('playerName','?')} {_prop} — {_label}",
            body=f"Actual: {actual_value} · Line: {line} · {recommendation.upper()}",
            data={
                "pickId":         pick.get("pickId"),
                "playerName":     pick.get("playerName"),
                "propType":       prop_type,
                "result":         result_str,
                "actualValue":    actual_value,
                "line":           line,
                "recommendation": recommendation,
                "sport":          "cs2",
            },
        )
    except Exception:
        pass

    print(
        f"[CS2 SETTLE] {pick.get('playerName','?')} {prop_type} "
        f"actual={actual_value} line={line} → {result_str.upper()} "
        f"(match: {result.get('matchScore','')})"
    )

    return {
        "pickId":      pick["pickId"],
        "status":      "settled",
        "result":      result_str,
        "actualValue": actual_value,
        "hitPct":      hit_pct,
        "matchScore":  result.get("matchScore"),
        "settledAt":   now_iso,
    }


async def _settle_wta_pick(pick: dict) -> Optional[dict]:
    """
    Auto-settle a WTA tennis pick once the match is finished.
    Mirrors _settle_cs2_pick — finds the player's finished match vs the
    opponent and resolves the prop's actual value from BDL WTA data.
    """
    player_id     = pick.get("playerId")
    opponent_id   = pick.get("opponentId")
    opponent_name = pick.get("opponentName") or ""
    prop_type     = pick.get("propType", "total_games")
    line          = pick.get("line", 0)
    recommendation = pick.get("recommendation", "over")
    timestamp     = pick.get("timestamp", "")

    if not player_id or (not opponent_id and not opponent_name):
        return None

    if isinstance(timestamp, (int, float)) and timestamp > 0:
        ts_iso = datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc).isoformat()
    elif isinstance(timestamp, str) and timestamp:
        ts_iso = timestamp
    else:
        ts_iso = datetime.now(timezone.utc).isoformat()

    try:
        result = await _wta_client.get_wta_completed_match_result(
            player_id=int(player_id),
            opponent_id=int(opponent_id) if opponent_id else None,
            opponent_name=opponent_name,
            prop_type=prop_type,
            after_iso=ts_iso,
        )
    except Exception as e:
        print(f"[WTA SETTLE] error for {pick.get('playerName','?')}: {e}")
        return None

    if not result or result.get("actualValue") is None:
        return None

    actual_value = result["actualValue"]
    result_str, pass_outcome = _settle_pick_result(actual_value, line, pick)
    hit_pct      = 100 if result_str == "hit" else (0 if result_str == "miss" else 50)
    now_iso      = datetime.now(timezone.utc).isoformat()

    settle_set = {
        "status":      "settled",
        "result":      result_str,
        "actualValue": actual_value,
        "hitPct":      hit_pct,
        "matchScore":  result.get("matchScore"),
        "settledAt":   now_iso,
    }
    if pass_outcome:
        settle_set["passOutcome"] = pass_outcome

    await db.picks.update_one(
        {"pickId": pick["pickId"], "email": pick.get("email", "")},
        {"$set": settle_set},
    )

    try:
        from routes.push import _send_pick_settled_push
        import asyncio as _aio
        _aio.create_task(_send_pick_settled_push(pick, result_str))
    except Exception as _pe:
        print(f"[WTA SETTLE] push error: {_pe}")

    try:
        from routes.notifications import create_notification
        _emoji = "✅" if result_str == "hit" else ("❌" if result_str == "miss" else "↔️")
        _prop  = prop_type.replace("_", " ").title()
        _label = "HIT" if result_str == "hit" else ("MISSED" if result_str == "miss" else "PUSH")
        await create_notification(
            email=pick.get("email", ""),
            ntype="pick_settled",
            title=f"{_emoji} {pick.get('playerName','?')} {_prop} — {_label}",
            body=f"Actual: {actual_value} · Line: {line} · {recommendation.upper()}",
            data={
                "pickId":         pick.get("pickId"),
                "playerName":     pick.get("playerName"),
                "propType":       prop_type,
                "result":         result_str,
                "actualValue":    actual_value,
                "line":           line,
                "recommendation": recommendation,
                "sport":          "wta",
            },
        )
    except Exception:
        pass

    print(
        f"[WTA SETTLE] {pick.get('playerName','?')} {prop_type} "
        f"actual={actual_value} line={line} → {result_str.upper()} "
        f"(score: {result.get('matchScore','')})"
    )

    return {
        "pickId":      pick["pickId"],
        "status":      "settled",
        "result":      result_str,
        "actualValue": actual_value,
        "hitPct":      hit_pct,
        "matchScore":  result.get("matchScore"),
        "settledAt":   now_iso,
    }


@router.post("/picks/{pick_id}/review")
async def on_demand_match_review(pick_id: str):
    """Trigger on-demand post-match AI review for a settled pick.
    Idempotent — returns existing review if already generated."""
    from ai_engine import generate_match_review
    pick = await db.picks.find_one({"pickId": pick_id})
    if not pick:
        raise HTTPException(status_code=404, detail="Pick not found")
    if pick.get("matchReview"):
        return {"ok": True, "matchReview": pick["matchReview"]}
    await generate_match_review(pick_id)
    updated = await db.picks.find_one({"pickId": pick_id}, {"matchReview": 1})
    return {
        "ok": bool(updated and updated.get("matchReview")),
        "matchReview": updated.get("matchReview") if updated else None,
    }


@router.post("/picks/{pick_id}/review/regenerate")
async def force_regenerate_match_review(pick_id: str, request: Request):
    """Force-regenerate post-match AI review — clears any stale/incomplete
    review (e.g. generated before red card data was available) and rebuilds
    from scratch using the latest fixture events from API-Football.
    Admin-only endpoint."""
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    if body.get("adminSecret") != _os.environ.get("ADMIN_SECRET", ""):
        raise HTTPException(status_code=403, detail="Forbidden")
    from ai_engine import generate_match_review
    pick = await db.picks.find_one({"pickId": pick_id})
    if not pick:
        raise HTTPException(status_code=404, detail="Pick not found")
    # Clear existing review so generate_match_review can claim it
    await db.picks.update_one(
        {"pickId": pick_id},
        {"$unset": {"matchReview": "", "matchReviewStatus": "", "matchReviewAt": ""}},
    )
    ok = await generate_match_review(pick_id)
    updated = await db.picks.find_one({"pickId": pick_id}, {"matchReview": 1})
    return {
        "ok": ok,
        "matchReview": updated.get("matchReview") if updated else None,
    }


# Basketball settlement removed — Soccer only
