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
            batch = messages[i:i + 100]
            try:
                resp = await client.post(EXPO_PUSH_URL, json=batch, headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                })
                result = resp.json()
                errors = [
                    r for r in result.get("data", [])
                    if r.get("status") == "error"
                ]
                if errors:
                    print(f"[PUSH] {len(errors)} delivery error(s): {errors[:3]}")
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
    print(f"[PUSH] @everyone → {len(tokens)} token(s): {title!r}")
    await _fire(tokens, title, body, data)
