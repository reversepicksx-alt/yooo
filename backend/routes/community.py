from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, timezone
import uuid
import re
from config import db

router = APIRouter()


def _serialize(m: dict) -> dict:
    ts = m.get("createdAt")
    if isinstance(ts, datetime):
        ts_str = ts.isoformat()
    else:
        ts_str = str(ts) if ts else ""
    return {
        "id": m.get("messageId", str(m.get("_id", ""))),
        "email": m.get("email", ""),
        "displayName": m.get("displayName", ""),
        "text": m.get("text", ""),
        "imageData": m.get("imageData"),
        "mentions": m.get("mentions", []),
        "reactions": m.get("reactions", {}),
        "createdAt": ts_str,
    }


class SendMessageRequest(BaseModel):
    email: str
    text: str = ""
    imageData: Optional[str] = None
    mentions: Optional[List[str]] = []


class ReactRequest(BaseModel):
    email: str
    emoji: str


@router.get("/api/community/messages")
async def get_messages(
    since: Optional[str] = Query(None),
    before: Optional[str] = Query(None),
    limit: int = Query(50, le=100),
):
    query: dict = {}
    sort_dir = -1

    if since:
        try:
            since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
            query["createdAt"] = {"$gt": since_dt}
            sort_dir = 1
        except Exception:
            pass
    elif before:
        try:
            before_dt = datetime.fromisoformat(before.replace("Z", "+00:00"))
            query["createdAt"] = {"$lt": before_dt}
        except Exception:
            pass

    msgs = (
        await db.community_messages.find(query)
        .sort("createdAt", sort_dir)
        .limit(limit)
        .to_list(None)
    )

    if sort_dir == -1:
        msgs.reverse()

    return [_serialize(m) for m in msgs]


@router.post("/api/community/messages")
async def send_message(req: SendMessageRequest):
    from routes.auth import check_access

    if not req.text.strip() and not req.imageData:
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    if req.imageData and len(req.imageData) > 5_000_000:
        raise HTTPException(status_code=413, detail="Image too large — compress before sending")

    access = await check_access(req.email.lower().strip())
    if not access:
        raise HTTPException(status_code=403, detail="No active subscription")

    raw_name = req.email.split("@")[0]
    display_name = " ".join(
        w.capitalize() for w in raw_name.replace(".", " ").replace("_", " ").split()
    )

    msg = {
        "messageId": str(uuid.uuid4()),
        "email": req.email.lower().strip(),
        "displayName": display_name,
        "text": req.text.strip(),
        "imageData": req.imageData or None,
        "mentions": [m.lower() for m in (req.mentions or [])],
        "reactions": {},
        "createdAt": datetime.now(timezone.utc),
    }

    await db.community_messages.insert_one(msg)

    try:
        await db.community_messages.create_index([("createdAt", 1)])
    except Exception:
        pass

    # ── Resolve @username mentions to emails ───────────────────────────────
    resolved_mentions: list[str] = []
    if req.mentions:
        for m in req.mentions:
            m_lower = m.lower().strip()
            if not m_lower:
                continue
            if "@" in m_lower and "." in m_lower:
                # already an email
                resolved_mentions.append(m_lower)
                continue
            # try username lookup
            user_doc = await db.users.find_one(
                {"username": {"$regex": f"^{re.escape(m_lower)}$", "$options": "i"}},
                {"email": 1}
            )
            if user_doc and user_doc.get("email"):
                resolved_mentions.append(user_doc["email"].lower())
            else:
                # fallback: email prefix match
                email_doc = await db.users.find_one(
                    {"email": {"$regex": f"^{re.escape(m_lower)}@", "$options": "i"}},
                    {"email": 1}
                )
                if email_doc:
                    resolved_mentions.append(email_doc["email"].lower())
    resolved_mentions = list(dict.fromkeys(resolved_mentions))

    # ── Push notifications ────────────────────────────────────────────────────
    text_body = req.text.strip()
    sender_name = display_name
    notif_title = f"Reverse Chat — {sender_name}"
    is_everyone = "@all" in text_body.lower()

    try:
        import asyncio as _aio
        from routes.push import send_notifications, send_everyone

        # @all rate limit: max once per email per 60 seconds to prevent spam
        _can_everyone = True
        if is_everyone:
            _now = datetime.now(timezone.utc)
            _last = await db.community_messages.find_one(
                {"email": req.email.lower().strip(), "text": {"$regex": "@all", "$options": "i"}},
                sort=[("createdAt", -1)],
                projection={"createdAt": 1},
            )
            if _last and isinstance(_last.get("createdAt"), datetime):
                _delta = (_now - _last["createdAt"]).total_seconds()
                if _delta < 60:
                    _can_everyone = False

        if is_everyone and _can_everyone:
            _aio.create_task(send_everyone(
                sender_email=req.email.lower().strip(),
                title=notif_title,
                body=text_body[:200],
                data={"screen": "community"},
            ))
        elif resolved_mentions:
            _aio.create_task(send_notifications(
                emails=resolved_mentions,
                title=notif_title,
                body=text_body[:200],
                data={"screen": "community"},
            ))
    except Exception as _pe:
        print(f"[PUSH] notification dispatch error: {_pe}")

    # ── In-app mention notifications ──────────────────────────────────────────
    if resolved_mentions and not is_everyone:
        try:
            import asyncio as _aio2
            from routes.notifications import create_notification
            for _m_email in resolved_mentions:
                _aio2.create_task(create_notification(
                    email=_m_email,
                    ntype="mention",
                    title=f"💬 {display_name} mentioned you",
                    body=text_body[:200],
                    data={
                        "messageId":   msg["messageId"],
                        "senderName":  display_name,
                        "senderEmail": req.email.lower().strip(),
                    },
                ))
        except Exception:
            pass

    return _serialize(msg)


@router.post("/api/community/messages/{message_id}/react")
async def react_to_message(message_id: str, req: ReactRequest):
    msg = await db.community_messages.find_one({"messageId": message_id})
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")

    reactions: dict = dict(msg.get("reactions", {}))
    emoji = req.emoji
    email = req.email.lower()

    if emoji not in reactions:
        reactions[emoji] = []

    if email in reactions[emoji]:
        reactions[emoji].remove(email)
        if not reactions[emoji]:
            del reactions[emoji]
    else:
        reactions[emoji].append(email)

    await db.community_messages.update_one(
        {"messageId": message_id}, {"$set": {"reactions": reactions}}
    )
    return {"reactions": reactions}


@router.delete("/api/community/messages/{message_id}")
async def delete_message(message_id: str, email: str = Query(...)):
    from routes.auth import check_access

    msg = await db.community_messages.find_one({"messageId": message_id})
    if not msg:
        raise HTTPException(status_code=404, detail="Not found")

    access = await check_access(email.lower())
    if msg.get("email") != email.lower() and access != "Owner":
        raise HTTPException(status_code=403, detail="Not authorized")

    await db.community_messages.delete_one({"messageId": message_id})
    return {"ok": True}


@router.get("/api/community/participants")
async def get_participants():
    pipeline = [
        {"$sort": {"createdAt": -1}},
        {"$limit": 500},
        {
            "$group": {
                "_id": "$email",
                "displayName": {"$first": "$displayName"},
                "lastSeen": {"$first": "$createdAt"},
            }
        },
        {"$sort": {"lastSeen": -1}},
        {"$limit": 50},
    ]
    parts = await db.community_messages.aggregate(pipeline).to_list(None)
    return [{"email": p["_id"], "displayName": p["displayName"]} for p in parts]
