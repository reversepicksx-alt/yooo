"""
Notifications routes — /api/notifications/*
In-app notifications for:
  - pick_settled  : pick hit / miss / push after match finishes
  - mention       : @mention in Reverse Chat community
"""
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Query
from pydantic import BaseModel
from typing import Optional, List
from config import db

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


# ── Serialiser ────────────────────────────────────────────────────────────────

def _serialize(n: dict) -> dict:
    n = dict(n)
    n.pop("_id", None)
    ts = n.get("createdAt")
    if hasattr(ts, "isoformat"):
        n["createdAt"] = ts.isoformat()
    return n


# ── Internal helper (called from picks.py, community.py) ─────────────────────

async def create_notification(
    email: str,
    ntype: str,
    title: str,
    body: str,
    data: dict = None,
) -> None:
    """Insert a notification for a user. Never raises — fire-and-forget safe."""
    try:
        await db.notifications.insert_one({
            "notificationId": str(uuid.uuid4()),
            "email":          email.lower().strip(),
            "type":           ntype,
            "title":          title,
            "body":           body,
            "data":           data or {},
            "read":           False,
            "createdAt":      datetime.now(timezone.utc),
        })
    except Exception as e:
        print(f"[NOTIF] create failed: {e}")


# ── GET /api/notifications ────────────────────────────────────────────────────

@router.get("")
async def get_notifications(
    email: str = Query(...),
    limit: int = Query(40),
):
    """List notifications for a user, newest first."""
    docs = (
        await db.notifications.find({"email": email.lower().strip()})
        .sort("createdAt", -1)
        .limit(min(limit, 100))
        .to_list(None)
    )
    return [_serialize(d) for d in docs]


# ── GET /api/notifications/unread-count ──────────────────────────────────────

@router.get("/unread-count")
async def get_unread_count(email: str = Query(...)):
    """Return the number of unread notifications (used for tab badge)."""
    count = await db.notifications.count_documents({
        "email": email.lower().strip(),
        "read":  False,
    })
    return {"count": count}


# ── POST /api/notifications/mark-read ────────────────────────────────────────

class MarkReadRequest(BaseModel):
    email:           str
    notificationIds: Optional[List[str]] = None   # None = mark ALL unread


@router.post("/mark-read")
async def mark_read(req: MarkReadRequest):
    """Mark one, several, or all notifications as read."""
    query: dict = {"email": req.email.lower().strip()}
    if req.notificationIds:
        query["notificationId"] = {"$in": req.notificationIds}
    try:
        await db.notifications.update_many(query, {"$set": {"read": True}})
    except Exception as _e:
        print(f"[NOTIFICATIONS WRITE FAIL] mark-read {req.email}: {_e}")
    return {"ok": True}
