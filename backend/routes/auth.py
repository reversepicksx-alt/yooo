import uuid
import bcrypt
import httpx
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException

from config import db, OWNER_EMAIL, LIFETIME_SUB_EMAILS, WHOP_API_KEY
from models import (
    VerifyAccessRequest, LoginRequest, SetPasswordRequest,
    ResetPasswordRequest, VerifySessionRequest,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


async def _verify_square_live(square_sub: dict, email_lower: str) -> str | None:
    """Verify a Square subscription against the live Square API.
    Returns access label string if valid, None if expired/invalid."""
    sub_id = square_sub.get("squareSubscriptionId")
    if not sub_id:
        return None

    try:
        from routes.square import get_square_client
        client = get_square_client()
        resp = client.subscriptions.get(subscription_id=sub_id)
        sq_sub = resp.subscription
        live_status = sq_sub.status if sq_sub.status else square_sub.get("status", "")

        canceled_date = getattr(sq_sub, 'canceled_date', None)
        charged_through = getattr(sq_sub, 'charged_through_date', None)
        updates = {}

        if live_status in ("DEACTIVATED",):
            updates["status"] = "EXPIRED"
            updates["updatedAt"] = datetime.now(timezone.utc).isoformat()
            await db.square_subscriptions.update_one(
                {"email": email_lower}, {"$set": updates}
            )
            print(f"[AUTH] Square live check: {email_lower} is DEACTIVATED → access denied")
            return None

        if live_status in ("ACTIVE", "PENDING") and charged_through:
            try:
                ct_str = str(charged_through)[:10]
                from datetime import date as date_type
                ct_date = date_type.fromisoformat(ct_str)
                today = date_type.today()
                if ct_date < today:
                    print(f"[AUTH] Square OVERDUE: {email_lower} charged_through={ct_str} < today={today} → auto-canceling")
                    try:
                        client.subscriptions.cancel(subscription_id=sub_id)
                        print(f"[AUTH] Square auto-canceled overdue subscription for {email_lower}")
                    except Exception as cancel_err:
                        print(f"[AUTH] Square auto-cancel failed for {email_lower}: {cancel_err}")
                    await db.square_subscriptions.update_one(
                        {"email": email_lower},
                        {"$set": {
                            "status": "EXPIRED",
                            "updatedAt": datetime.now(timezone.utc).isoformat(),
                            "expiredReason": "payment_overdue",
                        }}
                    )
                    return None
            except Exception as ct_err:
                print(f"[AUTH] charged_through check error for {email_lower}: {ct_err}")

        if canceled_date:
            updates["status"] = "CANCELED"
            updates["canceledAt"] = canceled_date
            live_status = "CANCELED"

        if updates:
            updates["updatedAt"] = datetime.now(timezone.utc).isoformat()
            await db.square_subscriptions.update_one(
                {"email": email_lower}, {"$set": updates}
            )

        if live_status in ("ACTIVE", "PENDING"):
            return "Premium (Square)"

        if live_status == "CANCELED":
            expires_at = square_sub.get("expiresAt")
            if expires_at:
                try:
                    exp_dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
                    if datetime.now(timezone.utc) < exp_dt:
                        return "Premium (Square) — Cancels soon"
                except Exception:
                    pass
            return None

        return None
    except Exception as e:
        print(f"[AUTH] Square live check failed for {email_lower}: {e}")
        return _check_square_local(square_sub, email_lower)


async def _verify_whop_live(whop_id: str, email_lower: str) -> bool | None:
    """Verify a Whop membership is still active via live API call.
    Returns True if valid, False if expired/invalid, None if API unreachable."""
    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            resp = await client.get(
                f"https://api.whop.com/api/v2/memberships/{whop_id}",
                headers={"Authorization": f"Bearer {WHOP_API_KEY}"},
            )
            if resp.status_code == 200:
                data = resp.json()
                is_valid = data.get("valid", False) or data.get("status") == "active"
                if is_valid:
                    print(f"[AUTH] Whop live check: {email_lower} membership {whop_id} is valid")
                    return True
                else:
                    await db.whop_subscriptions.update_one(
                        {"email": email_lower},
                        {"$set": {"status": "expired", "updatedAt": datetime.now(timezone.utc).isoformat()}}
                    )
                    print(f"[AUTH] Whop live check: {email_lower} membership {whop_id} is no longer valid → access denied")
                    return False
            elif resp.status_code == 404:
                await db.whop_subscriptions.update_one(
                    {"email": email_lower},
                    {"$set": {"status": "expired", "updatedAt": datetime.now(timezone.utc).isoformat()}}
                )
                print(f"[AUTH] Whop live check: {email_lower} membership {whop_id} not found → access denied")
                return False
            else:
                print(f"[AUTH] Whop live check: unexpected status {resp.status_code} for {email_lower}")
                return None
    except Exception as e:
        print(f"[AUTH] Whop live check failed for {email_lower}: {e}")
        return None


async def _check_square_local(square_sub: dict, email_lower: str) -> str | None:
    """Fallback: check Square subscription using only local DB data."""
    expires_at = square_sub.get("expiresAt")
    if expires_at:
        try:
            exp_dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            if datetime.now(timezone.utc) > exp_dt:
                await db.square_subscriptions.update_one(
                    {"email": email_lower},
                    {"$set": {"status": "EXPIRED", "updatedAt": datetime.now(timezone.utc).isoformat()}}
                )
                print(f"[AUTH] Subscription expired for {email_lower} (expired {expires_at})")
                return None
            else:
                sub_status = square_sub.get("status", "ACTIVE")
                return "Premium (Square)" if sub_status != "CANCELED" else "Premium (Square) — Cancels soon"
        except Exception:
            return "Premium (Square)"
    else:
        return "Premium (Square)"


async def check_access(email_lower: str):
    if email_lower == OWNER_EMAIL:
        return "Owner"
    if email_lower in LIFETIME_SUB_EMAILS:
        return "Lifetime"
    grant = await db.manual_access_grants.find_one({"email": email_lower}, {"_id": 0})
    if grant:
        return grant.get("access_type", "Manual")

    square_sub = await db.square_subscriptions.find_one(
        {"email": email_lower, "status": {"$in": ["ACTIVE", "PENDING", "CANCELED"]}},
        {"_id": 0}
    )
    if square_sub:
        return await _verify_square_live(square_sub, email_lower)

    if WHOP_API_KEY:
        try:
            cached = await db.whop_subscriptions.find_one(
                {"email": email_lower, "status": "active"},
                {"_id": 0}
            )

            if cached and cached.get("whop_id"):
                live_valid = await _verify_whop_live(cached["whop_id"], email_lower)
                if live_valid:
                    return "Whop Member"
                elif live_valid is False:
                    pass
                else:
                    return "Whop Member"

            async with httpx.AsyncClient(timeout=8.0) as client:
                page = 1
                while page <= 5:
                    resp = await client.get(
                        "https://api.whop.com/api/v2/memberships",
                        params={"valid": "true", "per_page": 50, "page": page},
                        headers={"Authorization": f"Bearer {WHOP_API_KEY}"},
                    )
                    if resp.status_code != 200:
                        break
                    data = resp.json()
                    memberships = data.get("data", [])
                    for m in memberships:
                        m_email = (m.get("email") or "").lower()
                        if m_email == email_lower and (m.get("valid") or m.get("status") == "active"):
                            await db.whop_subscriptions.update_one(
                                {"email": email_lower},
                                {"$set": {
                                    "email": email_lower,
                                    "status": "active",
                                    "whop_id": m.get("id"),
                                    "updatedAt": datetime.now(timezone.utc).isoformat(),
                                }},
                                upsert=True,
                            )
                            return "Whop Member"
                    pagination = data.get("pagination", {})
                    if page >= pagination.get("total_page", 1):
                        break
                    page += 1
        except Exception as exc:
            print(f"[WHOP] Check failed for {email_lower}: {exc}")

    return None


async def create_session(email: str, access_type: str):
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
        return {"verified": False, "email": email_lower, "message": "No active membership found. Contact your administrator to get access."}
    if email_lower == OWNER_EMAIL:
        token = await create_session(email_lower, "Owner")
        return {"verified": True, "email": email_lower, "session_token": token, "access_type": "Owner", "message": "Access granted"}
    user_record = await db.users.find_one({"email": email_lower}, {"_id": 0})
    if user_record and user_record.get("passwordHash"):
        return {"requires_password": True, "email": email_lower, "access_type": access_type}
    return {"requires_password_setup": True, "email": email_lower, "access_type": access_type}


@router.post("/login")
async def login(req: LoginRequest):
    email_lower = req.email.lower().strip()
    user_record = await db.users.find_one({"email": email_lower}, {"_id": 0, "passwordHash": 1, "email": 1})
    if not user_record or not user_record.get("passwordHash"):
        raise HTTPException(status_code=401, detail="Invalid credentials or password not set.")
    if not bcrypt.checkpw(req.password.encode("utf-8"), user_record["passwordHash"].encode("utf-8")):
        raise HTTPException(status_code=401, detail="Invalid password.")
    access_type = await check_access(email_lower)
    if not access_type:
        raise HTTPException(status_code=401, detail="Your subscription has expired or been revoked.")
    token = await create_session(email_lower, access_type)
    return {"verified": True, "email": email_lower, "session_token": token, "access_type": access_type, "message": "Login successful"}


@router.post("/set-password")
async def set_password(req: SetPasswordRequest):
    email_lower = req.email.lower().strip()
    if len(req.password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters.")
    access_type = await check_access(email_lower)
    if not access_type:
        raise HTTPException(status_code=401, detail="No active subscription found.")
    salt = bcrypt.gensalt()
    password_hash = bcrypt.hashpw(req.password.encode("utf-8"), salt).decode("utf-8")
    await db.users.update_one(
        {"email": email_lower},
        {"$set": {"email": email_lower, "passwordHash": password_hash, "created_at": datetime.now(timezone.utc).isoformat()}},
        upsert=True
    )
    token = await create_session(email_lower, access_type)
    return {"verified": True, "email": email_lower, "session_token": token, "access_type": access_type, "message": "Password set successfully"}


@router.post("/reset-password")
async def reset_password(req: ResetPasswordRequest):
    email_lower = req.email.lower().strip()
    if len(req.new_password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters.")
    access_type = await check_access(email_lower)
    if not access_type:
        raise HTTPException(status_code=401, detail="No active subscription found. Cannot reset password.")
    user_record = await db.users.find_one({"email": email_lower}, {"_id": 0})
    if not user_record:
        raise HTTPException(status_code=404, detail="No account found for this email.")
    salt = bcrypt.gensalt()
    password_hash = bcrypt.hashpw(req.new_password.encode("utf-8"), salt).decode("utf-8")
    await db.users.update_one(
        {"email": email_lower},
        {"$set": {"passwordHash": password_hash, "password_reset_at": datetime.now(timezone.utc).isoformat()}}
    )
    token = await create_session(email_lower, access_type)
    return {"verified": True, "email": email_lower, "session_token": token, "access_type": access_type, "message": "Password reset successfully"}


@router.post("/verify-session")
async def verify_session(req: VerifySessionRequest):
    email_lower = req.email.lower().strip()
    session = await db.sessions.find_one({"email": email_lower, "session_token": req.session_token}, {"_id": 0})
    if not session:
        return {"valid": False}
    access_type = await check_access(email_lower)
    if not access_type:
        await db.sessions.delete_one({"email": email_lower, "session_token": req.session_token})
        return {"valid": False}
    return {"valid": True, "access_type": access_type}


@router.post("/logout")
async def logout(req: VerifySessionRequest):
    await db.sessions.delete_one({"email": req.email.lower().strip(), "session_token": req.session_token})
    return {"success": True}
