"""
Manager Change Checker
======================
Background job that runs alongside the settlement bot to detect coaching changes
that happened AFTER a pick was saved.  When found, the pick is flagged with
`managerChangedAfterPick: true` and the subscriber gets a push notification.

Throttling: each pick is checked at most once per 24 hours (tracked via
`managerChangeCheckedAt` field on the pick document).

Public API
----------
  run_manager_change_check(db, api_fn) → None
"""

import asyncio
import time
from datetime import datetime, timezone

from manager_tracker import get_team_coach_info

_CHECK_INTERVAL_SECONDS = 24 * 3600  # Re-check each pick at most once every 24 h

# Soccer sport identifiers — limit the check to soccer picks only since
# get_team_coach_info() uses the API-Football coaching endpoint.
_SOCCER_SPORTS = {"soccer", "football", None, ""}  # None / "" = legacy picks with no sport field

# Non-soccer sports that should never be checked
_NON_SOCCER_SPORTS = {"mlb", "nba", "nfl", "nhl", "wnba", "wta", "cs2", "mma", "pga", "nfl", "f1", "dota2", "lol"}


def _pick_created_at(pick: dict) -> str | None:
    """Return the pick creation date as 'YYYY-MM-DD' (ISO) or None."""
    raw = pick.get("timestamp") or pick.get("createdAt") or ""
    if raw:
        return str(raw)[:10]
    return None


def _is_soccer_pick(pick: dict) -> bool:
    sport = (pick.get("sport") or "").lower().strip()
    if sport in _NON_SOCCER_SPORTS:
        return False
    if sport in _SOCCER_SPORTS:
        return True
    return False


async def run_manager_change_check(db, api_fn) -> None:
    """
    Query all active (non-settled) picks, check each soccer pick's team for a
    coaching change that occurred after the pick was saved, and flag it if found.

    Throttled to one check per pick per 24 hours to avoid push-notification spam.
    """
    try:
        now_ts = time.time()
        threshold_ts = now_ts - _CHECK_INTERVAL_SECONDS

        # Only look at live/pending picks — settled picks no longer matter
        cursor = db.picks.find(
            {
                "status": {"$in": ["live", "pending"]},
                "teamId": {"$exists": True, "$ne": None, "$ne": 0},
            },
            {
                "_id": 0,
                "pickId": 1,
                "teamId": 1,
                "playerName": 1,
                "teamName": 1,
                "propType": 1,
                "line": 1,
                "recommendation": 1,
                "sport": 1,
                "email": 1,
                "timestamp": 1,
                "createdAt": 1,
                "managerChangedAfterPick": 1,
                "managerChangeCheckedAt": 1,
                "managerChangeNotifiedAt": 1,
                "managerContext": 1,
            },
        )
        all_pending = await cursor.to_list(500)

        if not all_pending:
            return

        # Filter to soccer picks that are due for a re-check
        due_picks = []
        for p in all_pending:
            if not _is_soccer_pick(p):
                continue
            last_checked = p.get("managerChangeCheckedAt") or 0
            if now_ts - last_checked < _CHECK_INTERVAL_SECONDS:
                continue  # Checked within the last 24 h — skip
            due_picks.append(p)

        if not due_picks:
            return

        print(f"[MGR CHECK] Checking {len(due_picks)} soccer pick(s) for post-save coaching changes")

        # Group by teamId to avoid redundant API calls for the same team
        by_team: dict[int, list[dict]] = {}
        for p in due_picks:
            tid = int(p.get("teamId") or 0)
            if not tid:
                continue
            by_team.setdefault(tid, []).append(p)

        for team_id, team_picks in by_team.items():
            try:
                coach_info = await get_team_coach_info(team_id, db, api_fn)
                if not coach_info:
                    # Mark as checked even on API miss so we don't hammer the endpoint
                    await _mark_checked(db, [p["pickId"] for p in team_picks if p.get("pickId")], now_ts)
                    continue

                coach_start_date = coach_info.get("coachStartDate") or ""
                current_coach = coach_info.get("coachName") or ""

                for pick in team_picks:
                    await _check_one_pick(
                        db, pick, coach_start_date, current_coach, now_ts
                    )

                # Brief pause between teams to be kind to the API-Football quota
                await asyncio.sleep(1.0)

            except Exception as exc:
                print(f"[MGR CHECK] Error checking teamId={team_id}: {exc}")
                continue

    except Exception as exc:
        print(f"[MGR CHECK] run_manager_change_check error: {exc}")


async def _check_one_pick(
    db,
    pick: dict,
    coach_start_date: str,
    current_coach: str,
    now_ts: float,
) -> None:
    """Evaluate one pick and flag it when the coach changed after it was saved."""
    pick_id = pick.get("pickId") or ""
    if not pick_id:
        return

    created_date = _pick_created_at(pick)
    if not created_date or not coach_start_date:
        await _mark_checked(db, [pick_id], now_ts)
        return

    # Did the coaching change happen AFTER the pick was saved?
    changed_after = coach_start_date > created_date

    # Already notified? Avoid duplicate pushes for the same pick
    already_notified = bool(pick.get("managerChangeNotifiedAt"))
    already_flagged = bool(pick.get("managerChangedAfterPick"))

    update_fields: dict = {"managerChangeCheckedAt": now_ts}

    if changed_after:
        update_fields["managerChangedAfterPick"] = True
        update_fields["managerChangeCoachName"] = current_coach
        update_fields["managerChangeDate"] = coach_start_date

        if not already_notified:
            await _send_manager_change_notification(db, pick, current_coach)
            update_fields["managerChangeNotifiedAt"] = now_ts

        if not already_flagged:
            print(
                f"[MGR CHECK] FLAGGED pick={pick_id} player={pick.get('playerName')} "
                f"team={pick.get('teamName')} coach_start={coach_start_date} pick_saved={created_date}"
            )
    else:
        # Coach hasn't changed since the pick was saved — clear any stale flag
        if already_flagged:
            update_fields["managerChangedAfterPick"] = False
            update_fields["managerChangeCoachName"] = None

    try:
        await db.picks.update_one(
            {"pickId": pick_id},
            {"$set": update_fields},
        )
    except Exception as exc:
        print(f"[MGR CHECK] DB update failed for pick={pick_id}: {exc}")


async def _mark_checked(db, pick_ids: list[str], ts: float) -> None:
    """Bulk-mark picks as checked without changing any flag."""
    if not pick_ids:
        return
    try:
        await db.picks.update_many(
            {"pickId": {"$in": pick_ids}},
            {"$set": {"managerChangeCheckedAt": ts}},
        )
    except Exception as exc:
        print(f"[MGR CHECK] Bulk mark-checked failed: {exc}")


async def _send_manager_change_notification(db, pick: dict, new_coach: str) -> None:
    """Fire a push notification + in-app inbox entry for a post-save coaching change."""
    try:
        from routes.push import send_notifications
        from routes.notifications import create_notification

        email = (pick.get("email") or "").lower().strip()
        if not email:
            return

        player_name = pick.get("playerName") or "Player"
        team_name   = pick.get("teamName") or "their team"
        prop        = (pick.get("propType") or "prop").replace("_", " ").title()
        line        = pick.get("line", "")

        title = "⚠️ Coach Change — Re-run suggested"
        body  = (
            f"{team_name} changed coach since your {player_name} pick was saved "
            f"— consider re-running the prediction"
        )

        # Expo push
        asyncio.create_task(send_notifications(
            emails=[email],
            title=title,
            body=body,
            data={
                "screen":     "picks",
                "pickId":     pick.get("pickId", ""),
                "type":       "manager_change",
            },
        ))

        # In-app inbox
        await create_notification(
            email=email,
            ntype="manager_change",
            title=f"⚠️ {team_name} — coach change",
            body=body,
            data={
                "screen":     "picks",
                "pickId":     pick.get("pickId", ""),
                "playerName": player_name,
                "newCoach":   new_coach,
                "type":       "manager_change",
            },
        )

        print(
            f"[MGR CHECK] Notified {email} — {player_name} {prop} {line} | "
            f"new coach: {new_coach}"
        )

    except Exception as exc:
        print(f"[MGR CHECK] Notification failed for pick={pick.get('pickId')}: {exc}")
