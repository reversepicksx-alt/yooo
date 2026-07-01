"""
User profile routes — /api/users/*
Handles username management, profile lookup, and user search for mentions.
"""
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, timezone
import re
from config import db

router = APIRouter(prefix="/api/users", tags=["users"])

_USERNAME_RE = re.compile(r"^[a-zA-Z0-9_]{3,20}$")


# ─────────────────────────────────────────────────────────────────────────────────────────────

class SetUsernameRequest(BaseModel):
    email: str
    username: str


class UsernameResponse(BaseModel):
    ok: bool
    username: Optional[str] = None
    message: str = ""


# ───────────────────────────────────────────────────────────────────────────────────────────────────

@router.get("/me")
async def get_profile(email: str = Query(...)):
    """Return user profile including username."""
    email_lower = email.lower().strip()
    doc = await db.users.find_one({"email": email_lower}, {"_id": 0})
    if not doc:
        # Create bare user record on first lookup if needed
        return {"email": email_lower, "username": None, "displayName": None}
    return {
        "email": doc.get("email"),
        "username": doc.get("username"),
        "displayName": doc.get("displayName"),
        "createdAt": doc.get("createdAt"),
    }


@router.post("/username", response_model=UsernameResponse)
async def set_username(req: SetUsernameRequest):
    """Set or change a username. Must be unique and 3–20 alphanumeric+underscore."""
    email_lower = req.email.lower().strip()
    username_raw = req.username.strip()

    if not username_raw:
        raise HTTPException(status_code=400, detail="Username cannot be empty")

    if not _USERNAME_RE.match(username_raw):
        raise HTTPException(
            status_code=400,
            detail="Username must be 3–20 characters: letters, numbers, and underscores only"
        )

    username = username_raw.lower()

    # Check uniqueness (case-insensitive)
    existing = await db.users.find_one(
        {"username": {"$regex": f"^{re.escape(username)}$", "$options": "i"}},
        {"email": 1}
    )
    if existing and existing.get("email", "").lower() != email_lower:
        raise HTTPException(status_code=409, detail="Username is already taken")

    await db.users.update_one(
        {"email": email_lower},
        {
            "$set": {
                "username": username,
                "updatedAt": datetime.now(timezone.utc),
            },
            "$setOnInsert": {
                "email": email_lower,
                "createdAt": datetime.now(timezone.utc),
            },
        },
        upsert=True,
    )

    return UsernameResponse(ok=True, username=username, message="Username saved")


@router.get("/search")
async def search_users(q: str = Query(..., min_length=1), limit: int = Query(10, le=20)):
    """Prefix search for username / displayName / email — used by @mention autocomplete."""
    q_lower = q.lower().strip()
    if not q_lower:
        return []

    # Compound query: prefix match on username, displayName, or email prefix
    query = {
        "$or": [
            {"username": {"$regex": f"^{re.escape(q_lower)}", "$options": "i"}},
            {"displayName": {"$regex": f"^{re.escape(q_lower)}", "$options": "i"}},
            {"email": {"$regex": f"^{re.escape(q_lower)}", "$options": "i"}},
        ]
    }

    docs = (
        await db.users.find(query, {"_id": 0, "email": 1, "username": 1, "displayName": 1})
        .limit(limit)
        .to_list(None)
    )

    results = []
    for d in docs:
        results.append({
            "email": d.get("email", ""),
            "username": d.get("username"),
            "displayName": d.get("displayName"),
            "label": d.get("username") or d.get("displayName") or d.get("email", "").split("@")[0],
        })
    return results


@router.get("/by-username/{username}")
async def get_by_username(username: str):
    """Resolve a username to email (used by backend when processing @mentions)."""
    doc = await db.users.find_one(
        {"username": {"$regex": f"^{re.escape(username.strip())}$", "$options": "i"}},
        {"_id": 0, "email": 1, "username": 1, "displayName": 1}
    )
    if not doc:
        raise HTTPException(status_code=404, detail="User not found")
    return {
        "email": doc.get("email"),
        "username": doc.get("username"),
        "displayName": doc.get("displayName"),
    }
