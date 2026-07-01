"""
Direct Message routes — Reverse Mail (support channel)
- Any user can send a message TO the owner
- Only the owner can send messages to anyone
- Regular users cannot DM each other
"""
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, timezone
import uuid
from config import db, OWNER_EMAILS

router = APIRouter(prefix="/api/dm", tags=["dm"])


class SendDmRequest(BaseModel):
    senderEmail: str
    recipientEmail: str
    text: str


class MarkReadRequest(BaseModel):
    email: str
    otherEmail: str


def _serialize_dm(m: dict) -> dict:
    ts = m.get("createdAt")
    if isinstance(ts, datetime):
        ts_str = ts.isoformat()
    else:
        ts_str = str(ts) if ts else ""
    return {
        "id": m.get("messageId", str(m.get("_id", ""))),
        "senderId": m.get("senderEmail", ""),
        "recipientId": m.get("recipientEmail", ""),
        "text": m.get("text", ""),
        "read": m.get("read", False),
        "createdAt": ts_str,
    }


@router.post("/send")
async def send_dm(req: SendDmRequest):
    sender = req.senderEmail.lower().strip()
    recipient = req.recipientEmail.lower().strip()

    # Security: regular users can ONLY message the owner
    if sender not in OWNER_EMAILS and recipient not in OWNER_EMAILS:
        raise HTTPException(
            status_code=403,
            detail="You can only message the app owner for support."
        )

    if not req.text.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    msg = {
        "messageId": str(uuid.uuid4()),
        "senderEmail": sender,
        "recipientEmail": recipient,
        "text": req.text.strip(),
        "read": False,
        "createdAt": datetime.now(timezone.utc),
    }
    await db.direct_messages.insert_one(msg)
    return {"ok": True, "message": _serialize_dm(msg)}


@router.get("/inbox")
async def get_inbox(email: str = Query(...)):
    email_lower = email.lower().strip()
    pipeline = [
        {"$match": {"$or": [{"senderEmail": email_lower}, {"recipientEmail": email_lower}]}},
        {"$sort": {"createdAt": -1}},
        {"$group": {
            "_id": {"$cond": [{"$eq": ["$senderEmail", email_lower]}, "$recipientEmail", "$senderEmail"]},
            "lastMessage": {"$first": "$text"},
            "lastAt": {"$first": "$createdAt"},
            "unreadCount": {
                "$sum": {
                    "$cond": [
                        {"$and": [{"$eq": ["$recipientEmail", email_lower]}, {"$eq": ["$read", False]}]},
                        1, 0
                    ]
                }
            },
        }},
        {"$sort": {"lastAt": -1}},
    ]
    convos = await db.direct_messages.aggregate(pipeline).to_list(None)
    results = []
    for c in convos:
        other = c["_id"]
        user = await db.users.find_one(
            {"email": other},
            {"_id": 0, "username": 1, "displayName": 1, "profileImage": 1}
        )
        name = (
            user.get("username") or user.get("displayName") or other.split("@")[0]
            if user else other.split("@")[0]
        )
        results.append({
            "otherId": other,
            "otherName": name,
            "otherImage": user.get("profileImage") if user else None,
            "lastMessage": c.get("lastMessage", ""),
            "lastAt": (
                c.get("lastAt").isoformat()
                if isinstance(c.get("lastAt"), datetime)
                else str(c.get("lastAt", ""))
            ),
            "unreadCount": c.get("unreadCount", 0),
        })
    return results


@router.get("/thread")
async def get_thread(
    email: str = Query(...),
    other: str = Query(...),
    limit: int = Query(50, le=100),
):
    email_lower = email.lower().strip()
    other_lower = other.lower().strip()
    msgs = (
        await db.direct_messages.find({
            "$or": [
                {"senderEmail": email_lower, "recipientEmail": other_lower},
                {"senderEmail": other_lower, "recipientEmail": email_lower},
            ]
        })
        .sort("createdAt", 1)
        .limit(limit)
        .to_list(None)
    )
    # Resolve sender usernames so email is never exposed in response
    out = []
    for m in msgs:
        base = _serialize_dm(m)
        s = await db.users.find_one({"email": base["senderId"]}, {"_id": 0, "username": 1, "displayName": 1})
        base["senderName"] = (s.get("username") or s.get("displayName")) if s else base["senderId"].split("@")[0]
        out.append(base)
    return out


@router.patch("/read")
async def mark_read(req: MarkReadRequest):
    email_lower = req.email.lower().strip()
    other_lower = req.otherEmail.lower().strip()
    await db.direct_messages.update_many(
        {"senderEmail": other_lower, "recipientEmail": email_lower, "read": False},
        {"$set": {"read": True}}
    )
    return {"ok": True}
