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
        "senderId": m.get("email", ""),
        "name": m.get("displayName", ""),
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


class PickShareRequest(BaseModel):
    email: str
    pick: dict


def _pick_share_text(pick: dict, automatic: bool = False) -> str:
    player = pick.get("playerName") or pick.get("player", {}).get("name") or "Unknown player"
    prop = str(pick.get("propType") or "prop").replace("_", " ").title()
    rec = str(pick.get("recommendation") or "").upper()
    line = pick.get("line", "—")
    confidence = pick.get("confidenceScore", pick.get("confidence", "—"))
    matchup = pick.get("opponentName") or pick.get("opponent") or ""
    prefix = "AI TOP PICK" if automatic else "COMMUNITY PICK"
    parts = [f"{prefix}: {player}", f"{rec} {prop} {line}"]
    if matchup:
        parts.append(f"vs {matchup}")
    if confidence != "—":
        try:
            confidence = f"{float(confidence):.0f}%"
        except (TypeError, ValueError):
            pass
        parts.append(f"Confidence {confidence}")
    return " · ".join(parts)


async def create_pick_community_post(email: str, pick: dict, automatic: bool = False) -> dict:
    """Create an idempotent text post for a saved pick."""
    email_lower = email.lower().strip()
    from routes.auth import check_access
    if not await check_access(email_lower):
        raise HTTPException(status_code=403, detail="No active subscription")
    pick_id = str(pick.get("pickId") or pick.get("id") or "")
    if automatic and pick_id:
        existing = await db.community_messages.find_one(
            {"postType": "auto_pick", "pickId": pick_id},
            {"_id": 1},
        )
        if existing:
            return _serialize(await db.community_messages.find_one({"postType": "auto_pick", "pickId": pick_id}))
    user = await db.users.find_one({"email": email_lower}, {"_id": 0, "username": 1, "displayName": 1})
    display_name = (
        (user or {}).get("username") or (user or {}).get("displayName")
        or email_lower.split("@")[0].replace(".", " ").replace("_", " ").title()
    )
    msg = {
        "messageId": str(uuid.uuid4()),
        "email": email_lower,
        "displayName": display_name,
        "text": _pick_share_text(pick, automatic),
        "imageData": None,
        "mentions": [],
        "reactions": {},
        "createdAt": datetime.now(timezone.utc),
        "postType": "auto_pick" if automatic else "shared_pick",
        "pickId": pick_id or None,
        "pickData": {
            "playerName": pick.get("playerName") or pick.get("player", {}).get("name"),
            "propType": pick.get("propType"),
            "line": pick.get("line"),
            "recommendation": pick.get("recommendation"),
            "confidence": pick.get("confidenceScore", pick.get("confidence")),
            "opponentName": pick.get("opponentName") or pick.get("opponent"),
        },
    }
    await db.community_messages.insert_one(msg)
    return _serialize(msg)


@router.post("/api/community/share-pick")
async def share_pick_to_community(req: PickShareRequest):
    return await create_pick_community_post(req.email, req.pick, automatic=False)


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

    email_lower = req.email.lower().strip()
    user = await db.users.find_one({"email": email_lower}, {"_id": 0, "username": 1, "displayName": 1})
    display_name = (
        user.get("username") or user.get("displayName")
        or " ".join(w.capitalize() for w in email_lower.split("@")[0].replace(".", " ").replace("_", " ").split())
    )

    msg = {
        "messageId": str(uuid.uuid4()),
        "email": email_lower,
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
                _last_created = _last["createdAt"]
                if _last_created.tzinfo is None:
                    _last_created = _last_created.replace(tzinfo=timezone.utc)
                _delta = (_now - _last_created).total_seconds()
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
    results = []
    for p in parts:
        user = await db.users.find_one({"email": p["_id"]}, {"_id": 0, "username": 1, "displayName": 1})
        name = (user.get("username") or user.get("displayName") or p["displayName"]) if user else p["displayName"]
        results.append({"id": p["_id"], "name": name})
    return results
