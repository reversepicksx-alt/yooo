import os
import uuid
import random
import string
from datetime import datetime, timezone, timedelta
from typing import Union
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import stripe as _stripe

from config import db, OWNER_EMAILS, LIFETIME_SUB_EMAILS, BETA_TEST_EMAILS
from models import (
    VerifySessionRequest, VerifyAccessRequest, LoginRequest,
    SetPasswordRequest, ResetPasswordRequest, AppleAuthRequest,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])

# ── Owner passphrase (stored as secret, never in code) ────────────────────────
OWNER_CODE = os.environ.get("OWNER_ACCESS_CODE", "").strip()

# ── OTP helpers ───────────────────────────────────────────────────────────────
def _gen_code() -> str:
    return "".join(random.choices(string.digits, k=6))

async def _send_otp_email(email: str, code: str):
    """Send OTP via Gmail SMTP (same credentials used elsewhere in the project)."""
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart

    gmail_user = "reversepicksx@gmail.com"
    gmail_pass = os.environ.get("GMAIL_APP_PASSWORD", "").replace(" ", "").strip()
    if not gmail_pass:
        print(f"[OTP] GMAIL_APP_PASSWORD not set — code for {email}: {code}")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"{code} — Your Reverse Picks Login Code"
    msg["From"]    = f"Reverse Picks <{gmail_user}>"
    msg["To"]      = email

    html = f"""
    <div style="background:#050505;padding:40px 0;font-family:sans-serif;">
      <div style="max-width:420px;margin:0 auto;background:#111;border-radius:16px;
                  border:1px solid #222;padding:36px 32px;text-align:center;">
        <img src="https://reversepicks.com/logo.png" width="64" style="margin-bottom:20px;" />
        <h2 style="color:#39FF14;font-size:22px;margin:0 0 8px;">Your Login Code</h2>
        <p style="color:#aaa;font-size:14px;margin:0 0 28px;">
          Use this code to sign in to Reverse Picks. It expires in 10 minutes.
        </p>
        <div style="background:#050505;border-radius:12px;border:1.5px solid #39FF14;
                    padding:20px 0;margin-bottom:28px;">
          <span style="font-size:44px;font-weight:900;letter-spacing:12px;color:#39FF14;">
            {code}
          </span>
        </div>
        <p style="color:#555;font-size:12px;margin:0;">
          If you didn't request this, ignore this email.
        </p>
      </div>
    </div>
    """
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=10) as s:
            s.login(gmail_user, gmail_pass)
            s.sendmail(gmail_user, email, msg.as_string())
        print(f"[OTP] Code sent to {email}")
    except Exception as e:
        print(f"[OTP] Email failed for {email}: {e}")
        raise

# ── Access check (Apple IAP only for App Store build) ─────────────────────────
async def _check_apple_access(email_lower: str) -> str | None:
    """Returns access_type string or None. Only Apple IAP counts here."""
    apple_iap = await db.apple_iap_subscriptions.find_one({"email": email_lower}, {"_id": 0})
    if apple_iap:
        iap_status = apple_iap.get("status", "")
        if iap_status == "active":
            return "Premium (Apple)"
        if iap_status in ("canceled", "billing_issue"):
            expires_raw = apple_iap.get("expiresAt")
            if expires_raw:
                try:
                    exp_dt = datetime.fromisoformat(str(expires_raw).replace("Z", "+00:00"))
                    if exp_dt.tzinfo is None:
                        exp_dt = exp_dt.replace(tzinfo=timezone.utc)
                    if datetime.now(timezone.utc) < exp_dt:
                        return "Premium (Apple)"
                except Exception:
                    pass
    return None

_ANIMALS = [
    "lion", "tiger", "wolf", "bear", "hawk", "eagle", "fox", "shark", "cobra",
    "panther", "jaguar", "cheetah", "falcon", "dragon", "viper", "rhino",
    "bull", "stallion", "orca", "raptor", "lynx", "puma", "bison", "leopard",
]

_COLORS = [
    "neon", "dark", "green", "blue", "red", "gold", "silver", "black",
    "white", "crimson", "azure", "emerald", "ruby", "sapphire", "jade",
    "amber", "obsidian", "steel", "midnight", "shadow",
]

def _random_username() -> str:
    return f"{_random.choice(_COLORS)}_{_random.choice(_ANIMALS)}_{_random.randint(1,9999)}"

async def _ensure_username(email: str) -> str:
    doc = await db.users.find_one({"email": email}, {"_id": 0, "username": 1})
    if doc and doc.get("username"):
        return doc["username"]
    for _ in range(5):
        name = _random_username()
        taken = await db.users.find_one({"username": name}, {"_id": 0})
        if not taken:
            await db.users.update_one(
                {"email": email},
                {"$set": {"username": name, "updatedAt": datetime.now(timezone.utc)},
                 "$setOnInsert": {"email": email, "createdAt": datetime.now(timezone.utc)}},
                upsert=True,
            )
            return name
    return email.split("@")[0]

async def create_session(email: str, access_type: str) -> str:
    try:
        existing = await db.sessions.find_one({"email": email}, {"_id": 0})
    except Exception:
        existing = None
    # Auto-generate random username for every new session
    await _ensure_username(email)
    if existing and existing.get("session_token"):
        try:
            await db.sessions.update_one(
                {"email": email},
                {"$set": {"access_type": access_type, "last_active": datetime.now(timezone.utc).isoformat()}}
            )
        except Exception:
            pass
        return existing["session_token"]
    session_token = str(uuid.uuid4())
    await db.sessions.update_one(
        {"email": email},
        {"$set": {
            "email": email,
            "session_token": session_token,
            "access_type": access_type,
            "last_active": datetime.now(timezone.utc).isoformat(),
        }},
        upsert=True,
    )
    return session_token

# ── Web access check (Stripe / manual grants) ────────────────────────
async def _check_access_local(email_lower: str):
    if email_lower in OWNER_EMAILS:
        return "Owner"
    if email_lower in LIFETIME_SUB_EMAILS:
        return "Lifetime"
    if email_lower in BETA_TEST_EMAILS:
        return "Beta"
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
                        return None
                except Exception:
                    pass
            stripe_record = await db.stripe_subscriptions.find_one({"email": email_lower}, {"_id": 0, "status": 1, "currentPeriodEnd": 1})
            if stripe_record:
                _st = stripe_record.get("status", "")
                if _st == "past_due":
                    return None
                if _st == "canceled":
                    _cpe_raw = stripe_record.get("currentPeriodEnd")
                    _blocked = True
                    if _cpe_raw:
                        try:
                            _cpe_dt = datetime.fromisoformat(str(_cpe_raw).replace(" ", "T"))
                            if _cpe_dt.tzinfo is None:
                                _cpe_dt = _cpe_dt.replace(tzinfo=timezone.utc)
                            if datetime.now(timezone.utc) < _cpe_dt:
                                _blocked = False
                        except Exception:
                            pass
                    if _blocked:
                        return None
        return access_type
    stripe_sub = await db.stripe_subscriptions.find_one({"email": email_lower, "status": {"$in": ["active", "trialing"]}}, {"_id": 0})
    if stripe_sub:
        return "Premium (Stripe)"
    stripe_canceled = await db.stripe_subscriptions.find_one({"email": email_lower, "status": "canceled"}, {"_id": 0})
    if stripe_canceled:
        if stripe_canceled.get("canceledReason") == "payment_failed":
            pass
        else:
            cpe_raw = stripe_canceled.get("currentPeriodEnd")
            if cpe_raw:
                try:
                    cpe_dt = datetime.fromisoformat(str(cpe_raw).replace(" ", "T"))
                    if cpe_dt.tzinfo is None:
                        cpe_dt = cpe_dt.replace(tzinfo=timezone.utc)
                    if datetime.now(timezone.utc) < cpe_dt:
                        return "Premium (Stripe)"
                except Exception:
                    pass
    return None

def _sub_period_end_ts(sub_data: dict):
    try:
        items = sub_data.get("items", {}).get("data", [])
        if items:
            ts = items[0].get("current_period_end")
            if ts:
                return int(ts)
    except Exception:
        pass
    try:
        return int(sub_data.get("current_period_end", 0)) or None
    except Exception:
        return None

async def _check_stripe_live(email_lower: str):
    try:
        key = os.environ.get("STRIPE_SECRET_KEY", "")
        if not key:
            return None
        _stripe.api_key = key
        customers = _stripe.Customer.list(email=email_lower, limit=10)
        if not customers.data:
            return None
        best_sub = None
        priority_map = {"active": 0, "trialing": 1}
        now_ts = datetime.now(timezone.utc).timestamp()
        for cust in customers.data:
            for st in ["active", "trialing"]:
                subs_result = _stripe.Subscription.list(customer=cust.id, status=st, limit=5)
                for sub in subs_result.data:
                    sub_data = sub._data if hasattr(sub, '_data') else {}
                    priority = priority_map.get(st, 99)
                    if best_sub is None or priority < priority_map.get(best_sub[0], 99):
                        best_sub = (st, sub, sub_data, cust.id)
        if not best_sub:
            return None
        st, sub, sub_data, cust_id = best_sub
        sub_id = (sub_data.get("id") or "") if sub_data else sub.id
        if sub_data.get("cancel_at_period_end"):
            cpe = _sub_period_end_ts(sub_data)
            if cpe and cpe > now_ts:
                end_iso = datetime.fromtimestamp(int(cpe), tz=timezone.utc).isoformat()
                now_str = datetime.now(timezone.utc).isoformat()
                await db.stripe_subscriptions.update_one(
                    {"email": email_lower},
                    {"$set": {"email": email_lower, "stripeSubscriptionId": sub_id, "status": "canceled",
                              "canceledAt": now_str, "currentPeriodEnd": end_iso, "updatedAt": now_str,
                              "source": "stripe", "autoRestored": True}},
                    upsert=True,
                )
                return "Premium (Stripe)"
            return None
        plan_key = "monthly"
        try:
            items_data = sub_data.get("items", {}).get("data", []) if sub_data else []
            if items_data:
                price = items_data[0].get("price", {})
                lk = price.get("lookup_key") or ""
                if lk.startswith("reversepicks_"):
                    plan_key = lk.replace("reversepicks_", "")
                else:
                    rec = price.get("recurring") or {}
                    if rec.get("interval") == "week":
                        plan_key = "weekly"
                    elif rec.get("interval") == "month" and rec.get("interval_count", 1) >= 3:
                        plan_key = "quarterly"
        except Exception:
            pass
        end_iso = ""
        try:
            ts = _sub_period_end_ts(sub_data)
            if ts:
                end_iso = datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()
        except Exception:
            pass
        now = datetime.now(timezone.utc).isoformat()
        await db.stripe_subscriptions.update_one(
            {"email": email_lower},
            {"$set": {"email": email_lower, "stripeSubscriptionId": sub_id, "planKey": plan_key,
                      "status": st, "currentPeriodEnd": end_iso, "subscribedAt": now,
                      "updatedAt": now, "source": "stripe", "autoRestored": True},
             "$unset": {"canceledAt": ""}},
            upsert=True,
        )
        return "Premium (Stripe)"
    except Exception as e:
        print(f"[STRIPE LIVE FALLBACK] Error for {email_lower}: {e}")
        return None

async def check_access(email_lower: str) -> str | None:
    """Unified access check for web and Apple IAP users.

    Used by community.py, picks.py, and other protected routes that need to
    verify a subscription regardless of whether the user paid via Stripe,
    Apple IAP, or has a manual grant.
    """
    if not email_lower:
        return None
    # 1) Local grants (owner, lifetime, beta, manual, stripe local)
    result = await _check_access_local(email_lower)
    if result:
        return result
    # 2) Apple IAP (RevenueCat / App Store)
    apple = await _check_apple_access(email_lower)
    if apple:
        return apple
    # 3) Live Stripe fallback
    return await _check_stripe_live(email_lower)


async def check_web_access(email_lower: str):
    """Full web access check: local DB + live Stripe fallback."""
    if not email_lower:
        return None
    result = await _check_access_local(email_lower)
    if result:
        return result
    return await _check_stripe_live(email_lower)

# ── Web endpoints (Stripe / website login) ───────────────────────────
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
    access_type = await check_web_access(email_lower)
    if not access_type:
        raise HTTPException(status_code=403, detail="Your subscription has expired. Please resubscribe to regain access.")
    token = await create_session(email_lower, access_type)
    return {"verified": True, "email": email_lower, "session_token": token, "access_type": access_type, "message": "Login successful"}

@router.post("/set-password")
async def set_password(req: SetPasswordRequest):
    email_lower = req.email.lower().strip()
    access_type = await _check_access_local(email_lower)
    if not access_type:
        raise HTTPException(status_code=401, detail="No active membership found.")
    token = await create_session(email_lower, access_type)
    return {"verified": True, "email": email_lower, "session_token": token, "access_type": access_type, "message": "Access granted"}

@router.post("/reset-password")
async def reset_password(req: ResetPasswordRequest):
    email_lower = req.email.lower().strip()
    access_type = await _check_access_local(email_lower)
    if not access_type:
        raise HTTPException(status_code=401, detail="No active membership found.")
    token = await create_session(email_lower, access_type)
    return {"verified": True, "email": email_lower, "session_token": token, "access_type": access_type, "message": "Access granted"}

class LinkPaymentRequest(BaseModel):
    login_email: str
    payment_email: str

@router.post("/link-payment")
async def link_payment(req: LinkPaymentRequest):
    login_email   = req.login_email.lower().strip()
    payment_email = req.payment_email.lower().strip()
    if not login_email or not payment_email:
        raise HTTPException(status_code=400, detail="Both emails are required.")
    if login_email == payment_email:
        access_type = await check_web_access(login_email)
        if not access_type:
            return {"verified": False, "message": "No active membership found for that email."}
        token = await create_session(login_email, access_type)
        return {"verified": True, "email": login_email, "session_token": token, "access_type": access_type}
    payment_access = await _check_access_local(payment_email)
    if not payment_access:
        payment_access = await _check_stripe_live(payment_email)
    if not payment_access:
        return {"verified": False, "message": "No active subscription found for the payment email."}
    existing_sub = await db.stripe_subscriptions.find_one({"email": payment_email}, {"_id": 0})
    now = datetime.now(timezone.utc).isoformat()
    if existing_sub:
        mirrored = {k: v for k, v in existing_sub.items() if k != "_id"}
        mirrored.update({"email": login_email, "linkedFrom": payment_email, "linkedAt": now, "updatedAt": now})
        await db.stripe_subscriptions.update_one({"email": login_email}, {"$set": mirrored}, upsert=True)
    else:
        await db.manual_access_grants.update_one(
            {"email": login_email},
            {"$set": {"email": login_email, "access_type": "Manual", "linkedFrom": payment_email,
                      "grantedAt": now, "note": f"Payment verified via {payment_email}"}},
            upsert=True,
        )
    token = await create_session(login_email, payment_access)
    return {"verified": True, "email": login_email, "session_token": token,
            "access_type": payment_access, "message": "Payment verified! Access granted."}

# ── OTP Models ────────────────────────────────────────────────────────────────
class SendCodeRequest(BaseModel):
    email: str

class VerifyCodeRequest(BaseModel):
    email: str
    code: str

class OwnerLoginRequest(BaseModel):
    code: str

class IAPGrantRequest(BaseModel):
    email: str
    session_token: str
    product_id: str
    expires_at_ms: int | None = None

# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/send-code")
async def send_code(req: SendCodeRequest):
    """Step 1: email user a 6-digit OTP."""
    email_lower = req.email.lower().strip()
    if not email_lower or "@" not in email_lower:
        raise HTTPException(status_code=400, detail="Invalid email address.")

    code = _gen_code()
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)

    await db.auth_codes.update_one(
        {"email": email_lower},
        {"$set": {
            "email":      email_lower,
            "code":       code,
            "expiresAt":  expires_at.isoformat(),
            "used":       False,
            "createdAt":  datetime.now(timezone.utc).isoformat(),
        }},
        upsert=True,
    )

    email_sent = False
    try:
        await _send_otp_email(email_lower, code)
        email_sent = True
    except Exception as e:
        print(f"[OTP] Email send failed for {email_lower}: {e}")
        # Log the code so the user can still sign in if email is down
        # (e.g., Gmail app password expired, SMTP blocked, etc.)
        print(f"[OTP] FALLBACK code for {email_lower}: {code}")

    return {
        "sent": email_sent,
        "message": "Code sent — check your email." if email_sent else "We couldn't send the email right now. Please try again in a moment.",
    }


@router.post("/verify-code")
async def verify_code(req: VerifyCodeRequest):
    """Step 2: verify OTP, grant access if Apple IAP active."""
    email_lower = req.email.lower().strip()
    code_input  = req.code.strip()

    record = await db.auth_codes.find_one({"email": email_lower}, {"_id": 0})
    if not record:
        raise HTTPException(status_code=400, detail="No code found. Please request a new one.")
    if record.get("used"):
        raise HTTPException(status_code=400, detail="Code already used. Please request a new one.")

    expires_raw = record.get("expiresAt", "")
    try:
        exp_dt = datetime.fromisoformat(str(expires_raw))
        if exp_dt.tzinfo is None:
            exp_dt = exp_dt.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) > exp_dt:
            raise HTTPException(status_code=400, detail="Code expired. Please request a new one.")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid code record. Please request a new one.")

    if record.get("code") != code_input:
        raise HTTPException(status_code=400, detail="Incorrect code. Try again.")

    # Mark used (single-use)
    await db.auth_codes.update_one({"email": email_lower}, {"$set": {"used": True}})

    # Check ALL subscription sources (local grants, Apple IAP, Stripe live)
    access_type = await check_access(email_lower)
    if not access_type:
        # No active subscription anywhere — still log them in so the app can show the paywall
        token = await create_session(email_lower, "NoSubscription")
        return {
            "verified": True,
            "email": email_lower,
            "session_token": token,
            "access_type": "NoSubscription",
            "has_access": False,
            "message": "Email verified. Subscribe to get access.",
        }

    token = await create_session(email_lower, access_type)
    return {
        "verified": True,
        "email": email_lower,
        "session_token": token,
        "access_type": access_type,
        "has_access": True,
        "message": "Access granted.",
    }


def _is_reviewer_login_enabled() -> bool:
    """Secure-by-default: reviewer-login is ONLY enabled when
    REVIEWER_LOGIN_ENABLED=1 is explicitly set. Never infer from PRODUCTION."""
    return os.environ.get("REVIEWER_LOGIN_ENABLED") == "1"

_REVIEWER_EMAIL = "reversepicksx@gmail.com"

@router.post("/reviewer-login")
async def reviewer_login():
    """No-code login for the Apple App Store reviewer demo account.
    Creates a real MongoDB session so no credentials are hardcoded in source.
    DISABLED unless REVIEWER_LOGIN_ENABLED=1 is explicitly set.
    """
    if not _is_reviewer_login_enabled():
        raise HTTPException(status_code=404, detail="Not found")
    token = await create_session(_REVIEWER_EMAIL, "Owner")
    return {
        "verified": True,
        "email": _REVIEWER_EMAIL,
        "session_token": token,
        "access_type": "Owner",
        "has_access": True,
        "message": "Reviewer access granted.",
    }


@router.post("/owner-login")
async def owner_login(req: OwnerLoginRequest):
    """Owner passphrase login — full access, no email needed."""
    if not OWNER_CODE:
        raise HTTPException(status_code=503, detail="Owner access not configured.")
    if req.code.strip() != OWNER_CODE:
        raise HTTPException(status_code=401, detail="Invalid code.")

    owner_email = "reversepicksx@gmail.com"
    token = await create_session(owner_email, "Owner")
    return {
        "verified": True,
        "email": owner_email,
        "session_token": token,
        "access_type": "Owner",
        "has_access": True,
        "message": "Owner access granted.",
    }


async def verify_session(req_or_email_token: Union[VerifySessionRequest, dict]) -> dict:
    """Unified session verification: accepts VerifySessionRequest (session_token)
    or a plain dict with {email, token}. Returns {valid, access_type?}."""
    if isinstance(req_or_email_token, VerifySessionRequest):
        email_lower = req_or_email_token.email.lower().strip()
        token = req_or_email_token.session_token
    elif hasattr(req_or_email_token, "email"):
        email_lower = req_or_email_token.email.lower().strip()
        token = getattr(req_or_email_token, "token", None) or getattr(req_or_email_token, "session_token", "")
    else:
        email_lower = req_or_email_token["email"].lower().strip()
        token = req_or_email_token.get("token") or req_or_email_token.get("session_token", "")

    session = await db.sessions.find_one(
        {"email": email_lower, "session_token": token}, {"_id": 0}
    )
    if not session:
        return {"valid": False}

    access_type = session.get("access_type", "")

    # Owner always valid
    if access_type == "Owner":
        return {"valid": True, "access_type": "Owner"}

    # Apple IAP sessions — re-check Apple only
    if "Apple" in access_type or access_type == "NoSubscription":
        current = await _check_apple_access(email_lower)
        if not current:
            if access_type == "NoSubscription":
                return {"valid": True, "access_type": "NoSubscription"}
            await db.sessions.delete_one({"email": email_lower, "session_token": token})
            return {"valid": False}
        return {"valid": True, "access_type": current}

    # Web sessions (Stripe / manual) — re-check web access
    current = await check_web_access(email_lower)
    if not current:
        await db.sessions.delete_one({"email": email_lower, "session_token": token})
        return {"valid": False}
    return {"valid": True, "access_type": current}


@router.post("/verify-session")
async def verify_session_endpoint(req: VerifySessionRequest):
    return await verify_session(req)


@router.post("/logout")
async def logout(req: VerifySessionRequest):
    await db.sessions.delete_one(
        {"email": req.email.lower().strip(), "session_token": req.session_token}
    )
    return {"success": True}


@router.post("/apple-auth")
async def apple_auth(req: AppleAuthRequest):
    """Sign in with Apple — verify identity token, create/grant session."""
    import jwt
    import requests

    # 1. Fetch Apple's public keys
    try:
        keys_resp = requests.get("https://appleid.apple.com/auth/keys", timeout=10)
        keys = keys_resp.json().get("keys", [])
    except Exception:
        raise HTTPException(status_code=502, detail="Could not reach Apple auth servers.")

    # 2. Decode header to find key ID
    try:
        unverified = jwt.decode(req.identity_token, options={"verify_signature": False})
        kid = jwt.get_unverified_header(req.identity_token).get("kid")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid identity token.")

    # 3. Find matching public key
    apple_key = next((k for k in keys if k.get("kid") == kid), None)
    if not apple_key:
        raise HTTPException(status_code=400, detail="Apple signing key not found.")

    # 4. Convert JWK to PEM
    from cryptography.hazmat.primitives.asymmetric.ec import EllipticCurvePublicNumbers, SECP256R1
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.backends import default_backend
    import base64

    def b64_to_int(s: str) -> int:
        return int.from_bytes(base64.urlsafe_b64decode(s + "=="), "big")

    x = b64_to_int(apple_key["x"])
    y = b64_to_int(apple_key["y"])
    pub = EllipticCurvePublicNumbers(x, y, SECP256R1()).public_key(default_backend())
    pem = pub.public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    # 5. Verify token
    try:
        payload = jwt.decode(
            req.identity_token, pem, algorithms=["ES256"],
            audience="com.reversepicks.app",
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Apple token expired.")
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Apple token verification failed: {e}")

    apple_user_id = payload.get("sub")
    apple_email = req.email or payload.get("email", "")
    if not apple_email or "@" not in apple_email:
        # Fallback: derive email from Apple user ID if private email relay
        apple_email = f"{apple_user_id}@apple.private"

    email_lower = apple_email.lower().strip()

    # 6. Check actual Apple IAP entitlement — do NOT blindly grant premium
    access_type = await _check_apple_access(email_lower)
    if not access_type:
        # No active RevenueCat subscription — log them in as NoSubscription so app shows paywall
        token = await create_session(email_lower, "NoSubscription")
        print(f"[APPLE AUTH] {email_lower} signed in via Apple — NO active IAP, NoSubscription")
        return {
            "verified": True,
            "email": email_lower,
            "session_token": token,
            "access_type": "NoSubscription",
            "has_access": False,
            "message": "Apple sign-in successful. Subscribe via App Store to unlock predictions.",
        }

    token = await create_session(email_lower, access_type)

    # Persist Apple ID mapping
    await db.apple_auth_ids.update_one(
        {"appleUserId": apple_user_id},
        {"$set": {"appleUserId": apple_user_id, "email": email_lower, "updatedAt": datetime.now(timezone.utc).isoformat()}},
        upsert=True,
    )

    print(f"[APPLE AUTH] {email_lower} signed in via Apple | access={access_type} | uid={apple_user_id[:20]}...")
    return {
        "verified": True,
        "email": email_lower,
        "session_token": token,
        "access_type": access_type,
        "has_access": True,
        "message": "Access granted.",
    }


@router.post("/iap-grant")
async def iap_grant(req: IAPGrantRequest):
    """Fast-path: immediately grant Apple IAP access after purchase (before webhook arrives)."""
    email_lower = req.email.lower().strip()
    session = await db.sessions.find_one(
        {"email": email_lower, "session_token": req.session_token}, {"_id": 0}
    )
    if not session:
        raise HTTPException(status_code=401, detail="Invalid session")

    now_iso = datetime.now(timezone.utc).isoformat()
    expires_iso: str | None = None
    if req.expires_at_ms:
        try:
            expires_iso = datetime.fromtimestamp(req.expires_at_ms / 1000, tz=timezone.utc).isoformat()
        except Exception:
            pass

    await db.apple_iap_subscriptions.update_one(
        {"email": email_lower},
        {"$set": {
            "email":     email_lower,
            "status":    "active",
            "productId": req.product_id,
            "expiresAt": expires_iso,
            "updatedAt": now_iso,
        }},
        upsert=True,
    )
    await db.sessions.update_one(
        {"email": email_lower},
        {"$set": {"access_type": "Premium (Apple)", "last_active": now_iso}},
    )
    print(f"[IAP GRANT] {email_lower} → Premium (Apple) | product={req.product_id} | expires={expires_iso}")
    return {"ok": True, "access_type": "Premium (Apple)"}


# ── Pydantic models for new endpoints ─────────────────────────────────────────
class DeleteAccountRequest(BaseModel):
    email: str
    session_token: str

class IAPSignupRequest(BaseModel):
    email: str
    product_id: str
    expires_at_ms: int | None = None


@router.post("/delete-account")
async def delete_account(req: DeleteAccountRequest):
    """Permanently delete a user account and all associated data (Apple Guideline 5.1.1(v))."""
    email_lower = req.email.lower().strip()

    session_doc = await db.sessions.find_one(
        {"email": email_lower, "session_token": req.session_token}, {"_id": 0}
    )
    if not session_doc:
        raise HTTPException(status_code=401, detail="Invalid session. Please sign in again.")

    # Cancel active Stripe subscription if one exists
    try:
        stripe_sub = await db.stripe_subscriptions.find_one(
            {"email": email_lower, "status": {"$in": ["active", "trialing"]}}, {"_id": 0}
        )
        if stripe_sub and stripe_sub.get("subscriptionId"):
            try:
                _stripe.Subscription.cancel(stripe_sub["subscriptionId"])
            except Exception as se:
                print(f"[DELETE ACCOUNT] Stripe cancel skipped for {email_lower}: {se}")
    except Exception:
        pass

    # Wipe all user data
    await db.sessions.delete_many({"email": email_lower})
    await db.apple_iap_subscriptions.delete_many({"email": email_lower})
    await db.stripe_subscriptions.delete_many({"email": email_lower})
    await db.picks.delete_many({"email": email_lower})
    await db.manual_access_grants.delete_many({"email": email_lower})

    print(f"[DELETE ACCOUNT] {email_lower} — account and all data permanently deleted")
    return {"ok": True, "message": "Account deleted successfully."}


@router.post("/iap-signup")
async def iap_signup(req: IAPSignupRequest):
    """Create a new account from a completed Apple IAP purchase (no prior session required)."""
    email_lower = req.email.lower().strip()
    if not email_lower or "@" not in email_lower:
        raise HTTPException(status_code=400, detail="A valid email address is required.")

    # Honour owner / lifetime overrides
    if email_lower in OWNER_EMAILS:
        access_type = "Owner"
    elif email_lower in LIFETIME_SUB_EMAILS:
        access_type = "Lifetime"
    else:
        access_type = "Premium (Apple)"

    now_iso = datetime.now(timezone.utc).isoformat()
    expires_iso: str | None = None
    if req.expires_at_ms:
        try:
            expires_iso = datetime.fromtimestamp(req.expires_at_ms / 1000, tz=timezone.utc).isoformat()
        except Exception:
            pass

    await db.apple_iap_subscriptions.update_one(
        {"email": email_lower},
        {"$set": {
            "email":     email_lower,
            "status":    "active",
            "productId": req.product_id,
            "expiresAt": expires_iso,
            "updatedAt": now_iso,
        }},
        upsert=True,
    )

    token = await create_session(email_lower, access_type)
    print(f"[IAP SIGNUP] {email_lower} | product={req.product_id} | expires={expires_iso}")
    return {
        "verified": True,
        "email": email_lower,
        "session_token": token,
        "access_type": access_type,
        "has_access": True,
        "message": "Account created and access granted.",
    }
