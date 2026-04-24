import os
import uuid
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

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
    # Stripe: past_due — still give access while Stripe retries the renewal.
    # Only customer.subscription.deleted should revoke access.
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
        # currentPeriodEnd missing or already passed — give a 10-day grace from
        # when the past_due status was set (covers Stripe's full retry window).
        updated_raw = stripe_past_due.get("updatedAt") or stripe_past_due.get("subscribedAt", "")
        if updated_raw:
            try:
                updated_dt = datetime.fromisoformat(str(updated_raw).replace(" ", "T"))
                if updated_dt.tzinfo is None:
                    updated_dt = updated_dt.replace(tzinfo=timezone.utc)
                grace_end = updated_dt + timedelta(days=1)
                if datetime.now(timezone.utc) < grace_end:
                    print(f"[AUTH] past_due grace window active for {email_lower} (until {grace_end.date()})")
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
    Uses attribute-style access for Stripe SDK v7+.
    """
    try:
        key = os.environ.get("STRIPE_SECRET_KEY", "")
        if not key:
            return None
        _stripe.api_key = key

        customers = _stripe.Customer.list(email=email_lower, limit=10)
        if not customers.data:
            return None

        # Search all customers for this email (handles duplicate customer records)
        best_sub = None
        best_status_priority = {"active": 0, "trialing": 1, "past_due": 2}
        now_ts = datetime.now(timezone.utc).timestamp()

        for cust in customers.data:
            cust_id = cust.id
            for st in ["active", "trialing", "past_due"]:
                subs_result = _stripe.Subscription.list(customer=cust_id, status=st, limit=5)
                for sub in subs_result.data:
                    sub_data = sub._data if hasattr(sub, '_data') else {}
                    cpe = sub_data.get("current_period_end", 0) or 0
                    if st == "past_due" and cpe and cpe <= now_ts:
                        continue  # Period already ended — not valid
                    priority = best_status_priority.get(st, 99)
                    if best_sub is None or priority < best_status_priority.get(best_sub[0], 99):
                        best_sub = (st, sub, sub_data, cust_id)

        if not best_sub:
            return None

        st, sub, sub_data, cust_id = best_sub
        sub_id = sub_data.get("id", "") or sub.id
        status = st

        if st == "past_due":
            cpe = sub_data.get("current_period_end", 0)
            if cpe:
                print(f"[STRIPE LIVE FALLBACK] past_due sub found for {email_lower}, period ends {datetime.fromtimestamp(cpe, tz=timezone.utc).date()}")
            else:
                print(f"[STRIPE LIVE FALLBACK] past_due sub (no period end) for {email_lower} — granting access")

        # Determine plan key from price
        plan_key = "monthly"
        try:
            items_data = sub_data.get("items", {}).get("data", [])
            if items_data:
                price = items_data[0].get("price", {})
                lk = price.get("lookup_key") or ""
                if lk.startswith("reversepicks_"):
                    plan_key = lk.replace("reversepicks_", "")
                else:
                    rec = price.get("recurring") or {}
                    interval = rec.get("interval", "month")
                    interval_count = rec.get("interval_count", 1)
                    if interval == "week":
                        plan_key = "weekly"
                    elif interval == "month" and interval_count >= 3:
                        plan_key = "quarterly"
        except Exception:
            pass

        # Get period end
        end_iso = ""
        try:
            ts = sub_data.get("current_period_end")
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
        # Local DB has no record — webhook may have been missed.
        # Check Stripe live before revoking the session; this preserves access
        # for users whose purchase webhook failed to reach the server.
        access_type = await _check_stripe_live(email_lower)
    if not access_type:
        await db.sessions.delete_one({"email": email_lower, "session_token": req.session_token})
        return {"valid": False}
    return {"valid": True, "access_type": access_type}

@router.post("/logout")
async def logout(req: VerifySessionRequest):
    await db.sessions.delete_one({"email": req.email.lower().strip(), "session_token": req.session_token})
    return {"success": True}


class LinkPaymentRequest(BaseModel):
    login_email: str
    payment_email: str


@router.post("/link-payment")
async def link_payment(req: LinkPaymentRequest):
    """
    Allows a user who paid with a different email to gain access.
    Looks up the payment_email in Stripe (or local DB), and if an active sub
    is found, grants access to login_email by creating a mirrored sub record.
    """
    login_email = req.login_email.lower().strip()
    payment_email = req.payment_email.lower().strip()

    if not login_email or not payment_email:
        raise HTTPException(status_code=400, detail="Both emails are required.")
    if login_email == payment_email:
        # Same email — just do a normal verify
        access_type = await check_access(login_email)
        if not access_type:
            return {"verified": False, "message": "No active membership found for that email."}
        token = await create_session(login_email, access_type)
        return {"verified": True, "email": login_email, "session_token": token, "access_type": access_type}

    # 1. Check if payment_email has an active sub in local DB
    from datetime import datetime, timezone
    payment_access = await _check_access_local(payment_email)

    # 2. If not in local DB, check Stripe live
    stripe_sub_doc = None
    if not payment_access:
        payment_access = await _check_stripe_live(payment_email)

    if not payment_access:
        return {"verified": False, "message": "No active subscription found for the payment email. Please check the email you used at checkout."}

    # 3. Copy the sub record to login_email so they can log in going forward
    existing_sub = await db.stripe_subscriptions.find_one({"email": payment_email}, {"_id": 0})
    now = datetime.now(timezone.utc).isoformat()

    if existing_sub:
        # Mirror the stripe subscription to the login email
        mirrored = {k: v for k, v in existing_sub.items() if k != "_id"}
        mirrored["email"] = login_email
        mirrored["linkedFrom"] = payment_email
        mirrored["linkedAt"] = now
        mirrored["updatedAt"] = now
        await db.stripe_subscriptions.update_one(
            {"email": login_email},
            {"$set": mirrored},
            upsert=True,
        )
    else:
        # No stripe record (Square / Whop / Manual) — create a manual grant
        await db.manual_access_grants.update_one(
            {"email": login_email},
            {"$set": {
                "email": login_email,
                "access_type": "Manual",
                "linkedFrom": payment_email,
                "grantedAt": now,
                "note": f"Payment verified via {payment_email}",
            }},
            upsert=True,
        )

    print(f"[LINK PAYMENT] {payment_email} → {login_email} (access={payment_access})")

    # 4. Create session for the login email
    token = await create_session(login_email, payment_access)
    return {
        "verified": True,
        "email": login_email,
        "session_token": token,
        "access_type": payment_access,
        "message": "Payment verified! Access granted.",
    }
