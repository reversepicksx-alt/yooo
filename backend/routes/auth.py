import os
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException

import stripe as _stripe

from config import db, OWNER_EMAIL, OWNER_EMAILS, LIFETIME_SUB_EMAILS, WHOP_API_KEY
from models import (
    VerifyAccessRequest, LoginRequest, SetPasswordRequest,
    ResetPasswordRequest, VerifySessionRequest,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])

async def _check_access_local(email_lower: str):
    if email_lower in OWNER_EMAILS:
        return "Owner"
    if email_lower in LIFETIME_SUB_EMAILS:
        return "Lifetime"
    grant = await db.manual_access_grants.find_one({"email": email_lower}, {"_id": 0})
    if grant:
        access_type = grant.get("access_type", "Manual")
        if access_type == "Complimentary":
            expires_raw = grant.get("expiresAt")
            if expires_raw:
                try:
                    exp_dt = datetime.fromisoformat(str(expires_raw))
                    if exp_dt.tzinfo is None:
                        exp_dt = exp_dt.replace(tzinfo=timezone.utc)
                    if datetime.now(timezone.utc) >= exp_dt:
                        return None  # expired — fall through to normal checks
                except Exception:
                    pass
        return access_type
    # Active/pending/canceled subs always have access
    square_sub = await db.square_subscriptions.find_one(
        {"email": email_lower, "status": {"$in": ["ACTIVE", "PENDING", "CANCELED"]}}, {"_id": 0}
    )
    if square_sub:
        return "Premium (Square)"
    # EXPIRED subs: grant access if still within the paid billing window (expiresAt in future)
    # This handles: Square marks sub EXPIRED on renewal day but user already paid for the period
    expired_sub = await db.square_subscriptions.find_one(
        {"email": email_lower, "status": "EXPIRED"}, {"_id": 0}
    )
    if expired_sub:
        expires_at_raw = expired_sub.get("expiresAt")
        if expires_at_raw:
            try:
                exp_str = str(expires_at_raw).replace(" ", "T")
                exp_dt = datetime.fromisoformat(exp_str)
                if exp_dt.tzinfo is None:
                    exp_dt = exp_dt.replace(tzinfo=timezone.utc)
                if datetime.now(timezone.utc) < exp_dt:
                    return "Premium (Square)"  # paid period not yet over
            except Exception:
                pass
    whop_sub = await db.whop_subscriptions.find_one({"email": email_lower, "status": "active"}, {"_id": 0})
    if whop_sub:
        return "Whop Member"
    # Stripe subscriptions (new subscribers)
    stripe_sub = await db.stripe_subscriptions.find_one(
        {"email": email_lower, "status": {"$in": ["active", "trialing"]}}, {"_id": 0}
    )
    if stripe_sub:
        return "Premium (Stripe)"
    # Stripe: past_due — still give a short grace window (webhook may be delayed)
    stripe_past_due = await db.stripe_subscriptions.find_one(
        {"email": email_lower, "status": "past_due"}, {"_id": 0}
    )
    if stripe_past_due:
        end_raw = stripe_past_due.get("currentPeriodEnd")
        if end_raw:
            try:
                end_dt = datetime.fromisoformat(str(end_raw).replace(" ", "T"))
                if end_dt.tzinfo is None:
                    end_dt = end_dt.replace(tzinfo=timezone.utc)
                if datetime.now(timezone.utc) < end_dt:
                    return "Premium (Stripe)"
            except Exception:
                pass
    return None

async def _check_stripe_live(email_lower: str):
    """
    Live fallback: query Stripe directly.
    Called only when the local DB has no record — e.g. webhook was missed.
    If an active subscription is found, we write it to the DB immediately
    so future logins are instant without hitting Stripe again.
    """
    try:
        key = os.environ.get("STRIPE_SECRET_KEY", "")
        if not key:
            return None
        _stripe.api_key = key

        customers = _stripe.Customer.list(email=email_lower, limit=1)
        cust_list = customers["data"]
        if not cust_list:
            return None

        cust_id = cust_list[0]["id"]
        subs = _stripe.Subscription.list(customer=cust_id, status="active", limit=5)
        active_subs = subs["data"]
        if not active_subs:
            # Also check trialing
            subs_trial = _stripe.Subscription.list(customer=cust_id, status="trialing", limit=5)
            active_subs = subs_trial["data"]
        if not active_subs:
            return None

        sub = active_subs[0]
        sub_id = sub["id"]
        status = sub["status"]

        # Determine plan key from price
        plan_key = "monthly"
        try:
            items = sub["items"]["data"]
            if items:
                price = items[0]["price"]
                lk = price.get("lookup_key") or ""
                if lk.startswith("reversepicks_"):
                    plan_key = lk.replace("reversepicks_", "")
                else:
                    interval = (price.get("recurring") or {}).get("interval", "month")
                    interval_count = (price.get("recurring") or {}).get("interval_count", 1)
                    if interval == "week":
                        plan_key = "weekly"
                    elif interval == "month" and interval_count >= 3:
                        plan_key = "quarterly"
        except Exception:
            pass

        # Get period end
        end_iso = ""
        try:
            ts = sub.get("current_period_end")
            if ts:
                end_iso = datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()
        except Exception:
            pass

        # Write to DB so webhook is no longer needed for this user
        now = datetime.now(timezone.utc).isoformat()
        await db.stripe_subscriptions.update_one(
            {"email": email_lower},
            {"$set": {
                "email": email_lower,
                "stripeSubscriptionId": sub_id,
                "planKey": plan_key,
                "status": status,
                "currentPeriodEnd": end_iso,
                "subscribedAt": now,
                "updatedAt": now,
                "source": "stripe",
                "autoRestored": True,
            }},
            upsert=True,
        )
        print(f"[STRIPE LIVE FALLBACK] Restored access for {email_lower}: sub={sub_id} plan={plan_key}")
        return "Premium (Stripe)"
    except Exception as e:
        print(f"[STRIPE LIVE FALLBACK] Error for {email_lower}: {e}")
        return None


async def check_access(email_lower: str):
    result = await _check_access_local(email_lower)
    if result:
        return result
    # Webhook may have failed — check Stripe directly as a safety net
    return await _check_stripe_live(email_lower)

async def create_session(email: str, access_type: str):
    # Reuse existing session token so any device that already has it stays logged in.
    # Only generate a new token if there is no existing session (fresh login or after logout).
    existing = await db.sessions.find_one({"email": email}, {"_id": 0})
    if existing and existing.get("session_token"):
        await db.sessions.update_one(
            {"email": email},
            {"$set": {"access_type": access_type, "last_active": datetime.now(timezone.utc).isoformat()}}
        )
        return existing["session_token"]
    session_token = str(uuid.uuid4())
    await db.sessions.update_one(
        {"email": email},
        {"$set": {"email": email, "session_token": session_token, "access_type": access_type, "last_active": datetime.now(timezone.utc).isoformat()}},
        upsert=True
    )
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
