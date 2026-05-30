import json
import uuid
import unicodedata
import asyncio as aio
import traceback
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, HTTPException

from config import db, CURRENT_SEASON, STAT_LAMBDA_MAP
from models import (
    SavePickRequest, GetPicksRequest, DeletePickRequest,
    CorrectPickRequest, LiveUpdateRequest, SettlePicksRequest,
)
from utils import api_football_request
import cs2_client as _cs2_client
import wta_client as _wta_client
# auto_analyze_miss_background REMOVED — was draining AI tokens on every miss settlement

router = APIRouter(prefix="/api", tags=["picks"])

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
        "teamId": pick.get("_request", {}).get("teamId", 0),
        "opponentId": pick.get("_request", {}).get("opponentId", 0),
        "opponentName": pick.get("opponent") or pick.get("opponentName", ""),
        "leagueId": pick.get("_request", {}).get("leagueId", 0),
        "propType": normalized_prop,
        "line": pick.get("line", 0),
        "recommendation": (pick.get("recommendation") or "over").lower(),
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
        "venue": pick.get("_request", {}).get("venue", "home"),
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

    # Store AI analysis fields directly on CS2 picks (no separate predictions collection)
    if sport == "cs2":
        for field in ("sharpSummary", "reasoning", "tacticalBreakdown"):
            val = pick.get(field)
            if val:
                doc[field] = val
        # Store tactical metrics so the analysis modal can show them
        tm = pick.get("tacticalMetrics")
        if tm:
            doc["tacticalMetrics"] = tm
        # ── CS2 position/role cleanup ──────────────────────────────────────
        # Soccer position fields (e.g. "CM · Box-to-Box", "ST · Poacher")
        # were leaking onto CS2 picks because the player.position/role were
        # being passed through from a previous soccer prediction or the
        # default soccer resolver. CS2 has no tactical role of that kind —
        # we either store the engine's roleClassification (e.g. "entry_fragger")
        # or leave both fields blank.
        engine_role = (tm or {}).get("roleClassification") if tm else None
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
            from grok_positions import resolve_position_ai
            resolved = await resolve_position_ai(doc["playerName"], "soccer")
            if resolved.get("position"):
                doc["position"] = resolved["position"]
                doc["role"] = resolved.get("role", doc["role"])
        except Exception:
            pass

    await db.picks.update_one({"pickId": pick_id, "email": req.email.lower()}, {"$set": doc}, upsert=True)

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

@router.post("/picks/list")
async def list_picks(req: GetPicksRequest):
    session = await db.sessions.find_one({"email": req.email.lower(), "session_token": req.token}, {"_id": 0})
    if not session:
        raise HTTPException(status_code=401, detail="Invalid session")
    picks = await db.picks.find({"email": req.email.lower()}, {"_id": 0}).sort("timestamp", -1).to_list(None)

    for p in picks:
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
                {"pickId": p["pickId"], "email": req.email.lower()},
                {"$set": updates}
            )

    for p in picks:
        if p.get("status") != "settled":
            continue

        # ── DNP / early-sub guard ────────────────────────────────────────────
        # Voided picks (voidReason set OR <30 min played) must always be DNP.
        # This branch ACTIVELY corrects them — not just skips — so that a race
        # condition between a concurrent list_picks response and a DB fix can
        # never leave result=miss permanently stuck in the DB.
        # CRITICAL: only applies to soccer — minutesPlayed is meaningless for
        # CS2/MLB/WTA and would falsely DNP every non-soccer pick.
        _sport = p.get("sport", "soccer")
        _min_played = p.get("minutesPlayed")
        is_dnp = bool(p.get("voidReason")) or (
            _sport == "soccer" and _min_played is not None and _min_played < 30
        )
        if is_dnp:
            if p.get("result") != "dnp":
                p["result"] = "dnp"
                void_label = p.get("voidReason") or (
                    f"<30 min ({p.get('minutesPlayed',0)} min played)"
                    if _sport == "soccer" else f"DNP ({_sport})"
                )
                print(f"[CONSISTENCY] DNP→dnp {p.get('playerName','')} {p.get('propType','')} ({void_label})")
                await db.picks.update_one(
                    {"pickId": p["pickId"], "email": req.email.lower()},
                    {"$set": {"result": "dnp", "hitPct": 0,
                              "voidReason": p.get("voidReason") or void_label}}
                )
            continue

        # ── Normal result consistency check ──────────────────────────────────
        if p.get("actualValue") is not None:
            correct = _settle_result(p["actualValue"], p.get("line", 0), p.get("recommendation", "over"))
            if correct != p.get("result"):
                p["result"] = correct
                print(f"[CONSISTENCY] Correcting {p.get('playerName','')} {p.get('propType','')} → {correct}")
                await db.picks.update_one(
                    {"pickId": p["pickId"], "email": req.email.lower()},
                    {"$set": {"result": correct}}
                )

    # ── CS2 settled-pick data repair ────────────────────────────────────────────
    # Some CS2 picks were settled with wrong actualValue (e.g., 1 or 4 instead
    # of real kills) because the BDL API returned the match as "current" while
    # the old code only scanned "finished" matches.  Re-settle any settled
    # CS2 pick with a suspicious actualValue to fix the data.
    for p in picks:
        if p.get("sport") != "cs2" or p.get("status") != "settled":
            continue
        prop = p.get("propType", "")
        actual = p.get("actualValue")
        if not (prop.startswith(("maps_1_2_", "map1_", "map3_")) and actual is not None):
            continue
        # For kills/deaths/assists, actualValue < 5 is almost always wrong
        # (no pro player has < 5 kills across 2 maps).  For ADR, < 30 is wrong.
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
        try:
            settled = await _settle_cs2_pick({**p, "email": req.email.lower()})
            if settled and settled.get("actualValue") is not None:
                p["actualValue"] = settled["actualValue"]
                p["result"]      = settled["result"]
                p["hitPct"]      = settled["hitPct"]
                if settled.get("matchScore"):
                    p["matchScore"] = settled["matchScore"]
                print(f"[CS2 REPAIR] {p.get('playerName','')} {prop}: actualValue {actual} → {settled['actualValue']}")
                # CRITICAL: persist the fix so the pick stays correct forever
                await db.picks.update_one(
                    {"pickId": p["pickId"], "email": req.email.lower()},
                    {"$set": {
                        "actualValue": settled["actualValue"],
                        "result":      settled["result"],
                        "hitPct":      settled["hitPct"],
                        "matchScore":  settled.get("matchScore"),
                    }}
                )
        except Exception as e:
            print(f"[CS2 REPAIR] error for {p.get('playerName','')}: {e}")

    needs_proj = [p for p in picks if not p.get("projectedValue")]
    if needs_proj:
        for p in needs_proj:
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
                            {"pickId": p["pickId"], "email": req.email.lower()},
                            {"$set": {"projectedValue": pred["projectedValue"]}}
                        )
            except Exception:
                pass

    live_picks = [p for p in picks if p.get("status") in ("live", "pending")]
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

    soccer_live_picks = [
        p for p in live_picks
        if p.get("sport", "soccer") not in ("mlb", "cs2", "wta") and p.get("propType", "") not in _MLB_PROP_SET
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
                    p["fixtureId"] = upd.get("fixtureId")
                    p["minutesPlayed"] = upd.get("minutesPlayed")
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
                    if upd.get("result") and upd["result"] != "pending":
                        p["status"] = "settled"
                        p["result"] = upd["result"]
                        p["actualValue"] = upd.get("actualValue")
        except Exception:
            traceback.print_exc()

    # ── FINAL STAT REFRESH ─────────────────────────────────────────────────
    # For picks settled within the last 8 hours, re-fetch from the fixture API
    # to get the true final value (API data sometimes lags right after FT).
    # Skip picks that were manually corrected.
    try:
        now_utc = datetime.now(timezone.utc)
        recently_settled = [
            p for p in picks
            if p.get("status") == "settled"
            and not p.get("correctedManually")
            and not p.get("voidReason")  # never re-settle voided (DNP/early-sub) picks
            and p.get("settledAt")
            and (now_utc - datetime.fromisoformat(
                    p["settledAt"].replace("Z", "+00:00")
                 )).total_seconds() < 8 * 3600
        ]
        if recently_settled:
            # Limit to 6 picks per refresh to avoid rate-limit hammering
            for p in recently_settled[:6]:
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

                        # DNP void from _settle_soccer_pick: propagate DNP + voidReason
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
                            # Always backfill any newly-available match metadata (score, teams, possession),
                            # even if actualValue did not change.
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
                                {"pickId": p["pickId"], "email": req.email.lower()},
                                {"$set": meta_set}
                            )
                except Exception as _re:
                    print(f"[FINAL REFRESH] Error for {p.get('playerName','?')}: {_re}")
    except Exception as _fe:
        print(f"[FINAL REFRESH] Outer error: {_fe}")
    # ───────────────────────────────────────────────────────────────────────

    return {"picks": picks}


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

    # Strategy 3: CS2 picks store analysis directly in the pick document
    if not prediction and pick.get("sport") == "cs2":
        cs2_analysis = {}
        for field in ("sharpSummary", "reasoning", "tacticalBreakdown", "tacticalMetrics",
                      "projectedValue", "recommendation", "confidenceScore", "confidenceLevel",
                      "pOver", "pUnder", "priorMean", "momentumMean", "sampleSize",
                      "streakFlag", "propType", "line", "playerName", "opponentName"):
            val = pick.get(field)
            if val is not None:
                cs2_analysis[field] = val
        if cs2_analysis.get("reasoning") or cs2_analysis.get("sharpSummary"):
            return {"found": True, "analysis": cs2_analysis}
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
    "shots": lambda s: s.get("shots", {}).get("total"),
    "shots_on_target": lambda s: s.get("shots", {}).get("on"),
    "tackles": lambda s: s.get("tackles", {}).get("total"),
    "key_passes": lambda s: s.get("passes", {}).get("key"),
    "saves": lambda s: (s.get("goals", {}).get("saves") or 0),
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

    updates = await _process_soccer_live(live_picks, req.email.lower())
    return {"updates": updates}


async def _process_soccer_live(picks: list, email: str) -> list:
    """Process soccer picks for live updates."""
    # Group by team
    team_picks = {}
    for pick in picks:
        tid = pick.get("teamId", 0)
        if tid not in team_picks:
            team_picks[tid] = []
        team_picks[tid].append(pick)

    results = []

    async def process_team(team_id, picks_for_team):
        team_results = []
        try:
            # Get team's fixtures: LIVE first, then recent finished, then upcoming
            # "last" only returns FINISHED fixtures — it skips live games!
            # So we must also check "live" fixtures for this team directly
            from datetime import timedelta
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")

            # Fire both calls in parallel: today's fixtures + last 3 finished
            import asyncio as _aio
            live_task = api_football_request("fixtures", {"team": team_id, "date": today})
            yesterday_task = api_football_request("fixtures", {"team": team_id, "date": yesterday})
            last_task = api_football_request("fixtures", {"team": team_id, "last": 3})

            live_fixtures, yesterday_fixtures, last_fixtures = await _aio.gather(
                live_task, yesterday_task, last_task, return_exceptions=True
            )

            # Merge all fixtures, dedup by fixture ID
            all_fixtures = []
            seen_ids = set()
            for batch in [live_fixtures, yesterday_fixtures, last_fixtures]:
                if isinstance(batch, Exception) or not batch:
                    continue
                for f in batch:
                    fid = f.get("fixture", {}).get("id")
                    if fid and fid not in seen_ids:
                        seen_ids.add(fid)
                        all_fixtures.append(f)

            if not all_fixtures:
                for pick in picks_for_team:
                    team_results.append({"pickId": pick["pickId"], "matchStatus": "scheduled"})
                return team_results

            for pick in picks_for_team:
                opponent_name = pick.get("opponentName", "")
                matched_fixture = _match_soccer_fixture(all_fixtures, opponent_name, pick.get("timestamp", ""))

                if not matched_fixture:
                    team_results.append({"pickId": pick["pickId"], "matchStatus": "scheduled"})
                    continue

                update = await _build_soccer_update(pick, matched_fixture, email)
                team_results.append(update)
        except Exception:
            traceback.print_exc()
        return team_results

    tasks = [process_team(tid, picks) for tid, picks in team_picks.items()]
    all_results = await aio.gather(*tasks)
    for r in all_results:
        results.extend(r)

    return results


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
                hours_pick_after_kickoff = (pick_dt - fix_dt).total_seconds() / 3600
                # Pick made more than 3h after kickoff → game was over when pick was created.
                # A typical match (90 min + extra time + result delay) is < 3h from kickoff.
                if hours_pick_after_kickoff > 3:
                    continue
                # Pick made more than 14 days before fixture → wrong direction
                if hours_pick_after_kickoff < -336:
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


async def _build_soccer_update(pick: dict, fixture: dict, email: str) -> dict:
    """Build the live update response for a soccer pick."""
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

    # Fetch player stats + fixture possession in parallel
    player_stats_data, (home_poss, away_poss) = await aio.gather(
        api_football_request("fixtures/players", {"fixture": fixture_id}),
        _fetch_fixture_possession(fixture_id, home_team_id, away_team_id),
    )
    current_value = None
    minutes_played = 0

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
                    pstats = p.get("statistics", [{}])[0] if p.get("statistics") else {}
                    minutes_played = pstats.get("games", {}).get("minutes") or 0
                    getter = SOCCER_STAT_MAP.get(pick.get("propType", ""))
                    if getter:
                        current_value = getter(pstats)
                    break
            if current_value is not None:
                break

    # Keep None distinct from 0: None = stat not in API response, 0 = valid zero value.
    # If stat is truly unavailable, don't settle now — the background loop will retry.
    _stat_available = current_value is not None
    current_value = current_value if current_value is not None else 0
    line = pick.get("line", 0)
    recommendation = pick.get("recommendation", "over")

    # Pace (extrapolate to 90 min)
    effective_elapsed = max(elapsed, 1)
    pace = round((current_value / effective_elapsed) * 90, 1) if effective_elapsed > 0 else 0

    hit_pct = _calc_hit_pct(current_value, line, recommendation, elapsed, 90, is_finished, pace)

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
        "matchScore": match_score,
        "homeTeam": home_team_name,
        "awayTeam": away_team_name,
        "finalHomeGoals": home_goals,
        "finalAwayGoals": away_goals,
        "homePoss": home_poss,
        "awayPoss": away_poss,
    }

    if is_finished:
        # Guard: never re-settle a pick that the background loop already settled
        _current_status = pick.get("status", "live")
        if _current_status == "settled":
            update["matchStatus"] = "final"
            return update

        # If stat came back as None (API didn't return the field), defer to background loop
        if not _stat_available and minutes_played >= 30:
            print(f"[SETTLE-DEFER] {pick.get('playerName','')} {pick.get('propType','')} — stat unavailable despite {minutes_played} min played; deferring to background loop")
            update["matchStatus"] = "final"
            return update

        # DNP / early-sub void guard — industry standard: < 30 min = DNP
        _DNP_THRESHOLD = 30
        if minutes_played < _DNP_THRESHOLD:
            result_str = "dnp"
            update["voidReason"] = f"Player only played {minutes_played} min (min {_DNP_THRESHOLD} required)"
        else:
            result_str = _settle_result(current_value, line, recommendation)
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
        # Persist voidReason to DB so the consistency fixer doesn't re-revert DNP pushes
        if update.get("voidReason"):
            _settle_set["voidReason"] = update["voidReason"]
        if home_poss is not None:
            _settle_set["homePoss"] = home_poss
        if away_poss is not None:
            _settle_set["awayPoss"] = away_poss
        await db.picks.update_one(
            {"pickId": pick["pickId"], "email": email},
            {"$set": _settle_set}
        )
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
    """Settle a soccer pick."""
    if not team_id:
        for s in [CURRENT_SEASON, CURRENT_SEASON + 1]:
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
    for s in [CURRENT_SEASON + 1, CURRENT_SEASON]:
        try:
            data = await api_football_request("fixtures", {"team": team_id, "last": 5, "season": s})
            if data:
                for f in data:
                    home = f.get("teams", {}).get("home", {}).get("name", "")
                    away = f.get("teams", {}).get("away", {}).get("name", "")
                    status = f.get("fixture", {}).get("status", {}).get("short", "")
                    if status not in ("FT", "AET", "PEN"):
                        continue
                    if not (opponent.lower() in home.lower() or opponent.lower() in away.lower()):
                        continue
                    # Time guard: don't settle with a game that was already over when
                    # the pick was made. A pick made 3+ hours after kickoff means the
                    # game finished before the user picked — this is a different fixture.
                    fix_date_str = f.get("fixture", {}).get("date", "")
                    if fix_date_str and pick_created != datetime.min.replace(tzinfo=timezone.utc):
                        try:
                            fix_dt = datetime.fromisoformat(fix_date_str.replace("Z", "+00:00"))
                            hours_after_kickoff = (pick_created - fix_dt).total_seconds() / 3600
                            if hours_after_kickoff > 3:
                                continue  # Game was over before pick was made
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

    # DNP / early-sub void guard — players with < 30 min get DNP, not hit/miss
    _DNP_THRESHOLD = 30
    if minutes_played < _DNP_THRESHOLD and (minutes_played > 0 or actual_value is not None):
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
        }

    if actual_value is not None:
        line = pick.get("line", 0)
        recommendation = pick.get("recommendation", "over")
        result_str = _settle_result(actual_value, line, recommendation)
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
    result_str   = _settle_result(actual_value, line, recommendation)
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

    await db.picks.update_one(
        {"pickId": pick["pickId"], "email": pick.get("email", "")},
        {"$set": settle_set},
    )

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
    result_str   = _settle_result(actual_value, line, recommendation)
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

    await db.picks.update_one(
        {"pickId": pick["pickId"], "email": pick.get("email", "")},
        {"$set": settle_set},
    )

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


# Basketball settlement removed — Soccer only
