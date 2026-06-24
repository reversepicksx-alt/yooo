import os
import uuid
import random
import string
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config import db, OWNER_EMAILS
from models import VerifySessionRequest

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
    msg["Subject"] = f"{code} — Your ReversePicks Login Code"
    msg["From"]    = f"ReversePicks <{gmail_user}>"
    msg["To"]      = email

    html = f"""
    <div style="background:#050505;padding:40px 0;font-family:sans-serif;">
      <div style="max-width:420px;margin:0 auto;background:#111;border-radius:16px;
                  border:1px solid #222;padding:36px 32px;text-align:center;">
        <img src="https://reversepicks.com/logo.png" width="64" style="margin-bottom:20px;" />
        <h2 style="color:#39FF14;font-size:22px;margin:0 0 8px;">Your Login Code</h2>
        <p style="color:#aaa;font-size:14px;margin:0 0 28px;">
          Use this code to sign in to ReversePicks. It expires in 10 minutes.
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

async def create_session(email: str, access_type: str) -> str:
    try:
        existing = await db.sessions.find_one({"email": email}, {"_id": 0})
    except Exception:
        existing = None
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
    try:
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
    except Exception:
        pass
    return session_token

# ── Models ────────────────────────────────────────────────────────────────────
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

    try:
        await _send_otp_email(email_lower, code)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not send email. Please try again. ({e})")

    return {"sent": True, "message": "Code sent — check your email."}


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

    # Check Apple IAP access
    access_type = await _check_apple_access(email_lower)
    if not access_type:
        # No active subscription — still log them in but mark as no access
        # so the app can show the paywall
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


@router.post("/verify-session")
async def verify_session(req: VerifySessionRequest):
    email_lower = req.email.lower().strip()
    session = await db.sessions.find_one(
        {"email": email_lower, "session_token": req.session_token}, {"_id": 0}
    )
    if not session:
        return {"valid": False}

    access_type = session.get("access_type", "")

    # Owner always valid
    if access_type == "Owner":
        return {"valid": True, "access_type": "Owner"}

    # Re-check Apple IAP
    current = await _check_apple_access(email_lower)
    if not current:
        # If they have NoSubscription session (just verified email, no sub yet),
        # keep them valid so they can see the paywall
        if access_type == "NoSubscription":
            return {"valid": True, "access_type": "NoSubscription"}
        await db.sessions.delete_one({"email": email_lower, "session_token": req.session_token})
        return {"valid": False}

    return {"valid": True, "access_type": current}


@router.post("/logout")
async def logout(req: VerifySessionRequest):
    await db.sessions.delete_one(
        {"email": req.email.lower().strip(), "session_token": req.session_token}
    )
    return {"success": True}


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
