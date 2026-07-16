"""
Push notification token registry + Expo push sender.

Endpoints:
  POST /api/push/register   — store a device push token for an email
  POST /api/push/unregister — remove token on logout

Internal helper:
  send_notifications(db, emails, title, body, data) — fire Expo push to a list
  send_everyone(db, sender_email, title, body, data) — fire to all registered tokens
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timezone
import httpx
from config import db

router = APIRouter()

EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"


# ─── Models ───────────────────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    email: str
    token: str
    platform: Optional[str] = "unknown"


class UnregisterRequest(BaseModel):
    email: str
    token: Optional[str] = None


# ─── Endpoints ────────────────────────────────────────────────────────────────

@router.post("/api/push/register")
async def register_token(req: RegisterRequest):
    email = req.email.lower().strip()
    token = req.token.strip()
    if not email or not token:
        raise HTTPException(status_code=400, detail="email and token required")

    await db.push_tokens.update_one(
        {"email": email, "token": token},
        {"$set": {
            "email": email,
            "token": token,
            "platform": req.platform or "unknown",
            "updatedAt": datetime.now(timezone.utc),
        }},
        upsert=True,
    )
    return {"ok": True}


@router.post("/api/push/unregister")
async def unregister_token(req: UnregisterRequest):
    email = req.email.lower().strip()
    query: dict = {"email": email}
    if req.token:
        query["token"] = req.token.strip()
    await db.push_tokens.delete_many(query)
    return {"ok": True}


# ─── Internal helpers ─────────────────────────────────────────────────────────

async def _get_tokens_for_emails(emails: List[str]) -> List[str]:
    """Return all valid Expo push tokens for a list of email addresses."""
    lower = [e.lower().strip() for e in emails if e]
    if not lower:
        return []
    docs = await db.push_tokens.find({"email": {"$in": lower}}).to_list(None)
    return [d["token"] for d in docs if d.get("token", "").startswith("ExponentPushToken")]


async def _get_all_tokens(exclude_email: Optional[str] = None) -> List[str]:
    """Return all registered Expo push tokens, optionally excluding the sender."""
    query: dict = {}
    if exclude_email:
        query["email"] = {"$ne": exclude_email.lower().strip()}
    docs = await db.push_tokens.find(query).to_list(None)
    return [d["token"] for d in docs if d.get("token", "").startswith("ExponentPushToken")]


async def _fire(tokens: List[str], title: str, body: str, data: Optional[dict] = None):
    """Send Expo push messages in batches of 100 (Expo limit)."""
    if not tokens:
        return
    unique = list(dict.fromkeys(tokens))
    messages = [
        {"to": t, "title": title, "body": body, "data": data or {}, "sound": "default"}
        for t in unique
    ]
    async with httpx.AsyncClient(timeout=10) as client:
        for i in range(0, len(messages), 100):
            batch        = messages[i:i + 100]
            batch_tokens = unique[i:i + 100]
            try:
                resp = await client.post(EXPO_PUSH_URL, json=batch, headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                })
                result_data = resp.json().get("data", [])
                errors = []
                stale_tokens: List[str] = []
                for j, r in enumerate(result_data):
                    if r.get("status") == "error":
                        errors.append(r)
                        if r.get("details", {}).get("error") == "DeviceNotRegistered":
                            if j < len(batch_tokens):
                                stale_tokens.append(batch_tokens[j])
                if errors:
                    print(f"[PUSH] {len(errors)} delivery error(s): {errors[:3]}")
                if stale_tokens:
                    await db.push_tokens.delete_many({"token": {"$in": stale_tokens}})
                    print(f"[PUSH] Removed {len(stale_tokens)} stale/unregistered token(s)")
            except Exception as e:
                print(f"[PUSH] send error: {e}")


async def send_notifications(
    emails: List[str],
    title: str,
    body: str,
    data: Optional[dict] = None,
):
    """Send a push notification to specific email addresses."""
    tokens = await _get_tokens_for_emails(emails)
    print(f"[PUSH] → {len(tokens)} token(s) for {len(emails)} email(s): {title!r}")
    await _fire(tokens, title, body, data)


async def send_everyone(
    sender_email: str,
    title: str,
    body: str,
    data: Optional[dict] = None,
):
    """Broadcast a push notification to every registered member except the sender."""
    tokens = await _get_all_tokens(exclude_email=sender_email)
    print(f"[PUSH] @all → {len(tokens)} token(s): {title!r}")
    await _fire(tokens, title, body, data)


# ── Pick settlement helper ───────────────────────────────────────────────────────────────────────────────

async def _send_pick_settled_push(pick: dict, result: str):
    """Fire a push notification when a pick settles.
    pick: the dict before update (has email, playerName, propType, line, recommendation).
    result: 'hit' | 'miss' | 'push' | 'dnp'
    """
    import asyncio as _aio

    email = pick.get("email", "").lower().strip()
    player = pick.get("playerName", "Player")
    prop = pick.get("propType", "Prop").replace("_", " ").title()
    line = pick.get("line", "")
    rec = pick.get("recommendation", "over").upper()

    if result == "hit":
        title = "✅ Pick HIT!"
    elif result == "miss":
        title = "❌ Pick Miss"
    elif result == "dnp":
        title = "🔔 Pick DNP"
    else:
        title = "↔️ Pick Push"

    body = f"{player} {prop} {rec} {line} — RESULT: {result.upper()}"

    _aio.create_task(send_notifications(
        emails=[email],
        title=title,
        body=body,
        data={"screen": "picks", "pickId": pick.get("pickId", "")},
    ))


async def _notify_pick_settled(pick: dict, result: str) -> None:
    """Fire BOTH an Expo device push AND write an in-app inbox notification.

    Use this instead of bare asyncio.create_task(_send_pick_settled_push(...))
    everywhere in the auto-settlement background bot so the notification bell
    badge also increments, not just the OS-level push alert.

    pick  — the full pick dict from MongoDB (must have email field).
    result — 'hit' | 'miss' | 'push' | 'dnp'
    """
    import asyncio as _aio

    email  = pick.get("email", "").lower().strip()
    player = pick.get("playerName", "Player")
    prop   = pick.get("propType", "Prop").replace("_", " ").title()
    line   = pick.get("line", "")
    rec    = (pick.get("recommendation") or "over").upper()

    if result == "hit":
        title = "✅ Pick HIT!"
        emoji = "✅"
        label = "HIT"
    elif result == "miss":
        title = "❌ Pick Miss"
        emoji = "❌"
        label = "MISS"
    elif result == "dnp":
        title = "🔔 Pick DNP/Void"
        emoji = "🔔"
        label = "DNP"
    else:
        title = "↔️ Pick Push"
        emoji = "↔️"
        label = "PUSH"

    body = f"{player} · {prop} {rec} {line}"

    # 1. Expo device push (fire-and-forget)
    if email:
        _aio.create_task(send_notifications(
            emails=[email],
            title=title,
            body=f"{body} — {label}",
            data={"screen": "picks", "pickId": pick.get("pickId", "")},
        ))

    # 2. In-app inbox notification (awaited — fast MongoDB insert)
    if email:
        try:
            from routes.notifications import create_notification
            await create_notification(
                email=email,
                ntype="pick_settled",
                title=f"{emoji} {player} {prop} — {label}",
                body=f"{rec} {line} · Result: {label}",
                data={
                    "screen":     "picks",
                    "pickId":     pick.get("pickId", ""),
                    "result":     result,
                    "propType":   pick.get("propType", ""),
                    "playerName": player,
                },
            )
        except Exception as _ne:
            print(f"[PUSH] in-app notif error for {player}: {_ne}")
