import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException

from config import db, OWNER_EMAIL, LIFETIME_SUB_EMAILS, WHOP_API_KEY
from models import (
    VerifyAccessRequest, LoginRequest, SetPasswordRequest,
    ResetPasswordRequest, VerifySessionRequest,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])

async def _check_access_local(email_lower: str):
    if email_lower == OWNER_EMAIL:
        return "Owner"
    if email_lower in LIFETIME_SUB_EMAILS:
        return "Lifetime"
    grant = await db.manual_access_grants.find_one({"email": email_lower}, {"_id": 0})
    if grant:
        return grant.get("access_type", "Manual")
    square_sub = await db.square_subscriptions.find_one({"email": email_lower, "status": {"$in": ["ACTIVE", "PENDING", "CANCELED"]}}, {"_id": 0})
    if square_sub:
        if square_sub.get("expiredReason") == "payment_overdue":
            return None
        return "Premium (Square)"
    whop_sub = await db.whop_subscriptions.find_one({"email": email_lower, "status": "active"}, {"_id": 0})
    if whop_sub:
        return "Whop Member"
    return None

async def check_access(email_lower: str):
    return await _check_access_local(email_lower)

async def create_session(email: str, access_type: str):
    session_token = str(uuid.uuid4())
    await db.sessions.update_one({"email": email}, {"$set": {"email": email, "session_token": session_token, "access_type": access_type, "last_active": datetime.now(timezone.utc).isoformat()}}, upsert=True)
    return session_token

@router.post("/verify-access")
@router.post("/verify-whop")
async def verify_access(req: VerifyAccessRequest):
    email_lower = req.email.lower().strip()
    access_type = await check_access(email_lower)
    if not access_type:
        return {"verified": False, "email": email_lower, "message": "No active membership found."}
    token = await create_session(email_lower, access_type)
    return {"verified": True, "email": email_lower, "session_token": token, "access_type": access_type, "message": "Access granted"}

@router.post("/login")
async def login(req: LoginRequest):
    email_lower = req.email.lower().strip()
    access_type = await check_access(email_lower)
    if not access_type:
        raise HTTPException(status_code=403, detail="Your subscription has expired. Please resubscribe to regain access.")
    token = await create_session(email_lower, access_type)
    return {"verified": True, "email": email_lower, "session_token": token, "access_type": access_type, "message": "Login successful"}

@router.post("/set-password")
async def set_password(req: SetPasswordRequest):
    email_lower = req.email.lower().strip()
    access_type = await check_access(email_lower)
    if not access_type:
        raise HTTPException(status_code=401, detail="No active subscription found.")
    token = await create_session(email_lower, access_type)
    return {"verified": True, "email": email_lower, "session_token": token, "access_type": access_type, "message": "Access granted"}

@router.post("/reset-password")
async def reset_password(req: ResetPasswordRequest):
    email_lower = req.email.lower().strip()
    access_type = await check_access(email_lower)
    if not access_type:
        raise HTTPException(status_code=401, detail="No active subscription found.")
    token = await create_session(email_lower, access_type)
    return {"verified": True, "email": email_lower, "session_token": token, "access_type": access_type, "message": "Access granted"}

@router.post("/verify-session")
async def verify_session(req: VerifySessionRequest):
    email_lower = req.email.lower().strip()
    session = await db.sessions.find_one({"email": email_lower, "session_token": req.session_token}, {"_id": 0})
    if not session:
        return {"valid": False}
    access_type = await _check_access_local(email_lower)
    if not access_type:
        await db.sessions.delete_one({"email": email_lower, "session_token": req.session_token})
        return {"valid": False}
    return {"valid": True, "access_type": access_type}

@router.post("/logout")
async def logout(req: VerifySessionRequest):
    await db.sessions.delete_one({"email": req.email.lower().strip(), "session_token": req.session_token})
    return {"success": True}
