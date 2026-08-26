import os
import uuid
import random
import string
from datetime import datetime, timezone, timedelta
from typing import Union
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import stripe as _stripe
import httpx

from config import db, OWNER_EMAILS, LIFETIME_SUB_EMAILS, BETA_TEST_EMAILS
from models import (
    VerifySessionRequest, VerifyAccessRequest, LoginRequest,
    SetPasswordRequest, ResetPasswordRequest, AppleAuthRequest,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])

# Stripe Dashboard screenshots supplied on 2026-07-29. These are explicit
# last-access dates for the visible accounts. Two of the 16 dashboard rows
# were clipped in the screenshots, so they remain governed by their stored
# Stripe period end instead of being guessed here.
STRIPE_SCREENSHOT_CUTOFFS = {
    "tuckersneakerbot@gmail.com": "2026-08-03",
    "jahiemcooper1516@gmail.com": "2026-08-02",
    "eddiecane372@gmail.com": "2026-08-01",
    "jujumobley@icloud.com": "2026-07-31",
    "isaiahmccowan@gmail.com": "2026-08-23",
    "miguel7893@icloud.com": "2026-08-05",
    "justinrsanders9107@gmail.com": "2026-07-31",
    "potabil50@gmail.com": "2026-08-01",
    "maldonadoivan209@gmail.com": "2026-07-29",
    "gerald.alfonseca03@gmail.com": "2026-07-31",
    "alphonsobruton@gmail.com": "2026-07-31",
    "ryanamosun26@gmail.com": "2026-07-31",
    "tristanobannon21@gmail.com": "2026-07-29",
    "yahirpalacios45@gmail.com": "2026-08-02",
    "sylvester.jared@gmail.com": "2026-08-01",
    "caloc01.ch@gmail.com": "2026-07-31",
}


def _screenshot_stripe_access_expired(email_lower: str) -> bool:
    """Return true only after the account's listed final calendar day."""
    cutoff_raw = STRIPE_SCREENSHOT_CUTOFFS.get(email_lower)
    if not cutoff_raw:
        return False
    try:
        from datetime import date
        return date.today() > date.fromisoformat(cutoff_raw)
    except (TypeError, ValueError):
        return True


# ── Owner passphrase (stored as secret, never in code) ────────────────────────
OWNER_CODE = os.environ.get("OWNER_ACCESS_CODE", "").strip()
OWNER_PIN  = os.environ.get("OWNER_PIN", "").strip()
# Gate code: prefer OWNER_PIN; fall back to OWNER_ACCESS_CODE.
# Either secret being set is enough to activate the owner sign-in gate.
_OWNER_GATE_CODE = OWNER_PIN or OWNER_CODE
print(f"[AUTH BOOT] OWNER_PIN set={bool(OWNER_PIN)} OWNER_CODE set={bool(OWNER_CODE)} gate_active={bool(_OWNER_GATE_CODE)}")

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

REVENUECAT_PROJECT_ID = os.environ.get("REVENUECAT_PROJECT_ID", "proj3a3fd517")
# RevenueCat's mobile SDK exposes the entitlement's public identifier
# ("pro"), but the V2 server API returns the entitlement resource ID. Using
# "pro" here made valid Apple subscribers look unsubscribed to prediction
# routes even though Account correctly showed an active entitlement.
REVENUECAT_PRO_ENTITLEMENT = os.environ.get(
    "REVENUECAT_PRO_ENTITLEMENT",
    "entl9515aab63f",
)
# Keep the project entitlement resource ID as a hard safety net if an older
# deployment still has REVENUECAT_PRO_ENTITLEMENT=pro configured. RevenueCat's
# V2 API returns resource IDs in active_entitlements, not the SDK identifier.
REVENUECAT_PRO_ENTITLEMENT_IDS = {
    REVENUECAT_PRO_ENTITLEMENT,
    "entl9515aab63f",
}

# Sentinel returned by _check_revenuecat_live when the call failed due to a
# network / server error (as opposed to "customer has no active entitlement").
# verify_session uses this to distinguish the two cases so it never deletes a
# paying subscriber's session just because RevenueCat was unreachable.
_RC_NETWORK_ERROR = "__RC_NETWORK_ERROR__"

async def _check_revenuecat_live(email_lower: str) -> str | None:
    """Live fallback: ask RevenueCat's server API directly whether this
    email (used as the app_user_id / customer_id) has an active Apple
    entitlement.

    Returns:
      "Premium (Apple)"   — active entitlement confirmed
      None                — customer exists but no active entitlement (404 or empty)
      _RC_NETWORK_ERROR   — API call failed (network/timeout); caller should
                            NOT downgrade the user based on this result.

    Timeout is intentionally kept to 2.5 s so the call completes within the
    mobile client's 4 s verify-session race window (preventing stale cached
    NoSubscription from winning the race on slow networks).
    """
    key = os.environ.get("REVENUECAT_SECRET_API_KEY", "")
    if not key or not email_lower:
        return None
    try:
        async with httpx.AsyncClient(timeout=2.5) as client:
            resp = await client.get(
                f"https://api.revenuecat.com/v2/projects/{REVENUECAT_PROJECT_ID}/customers/{email_lower}",
                headers={"Authorization": f"Bearer {key}"},
            )
            if resp.status_code == 404:
                return None
            if resp.status_code >= 500:
                print(f"[REVENUECAT LIVE FALLBACK] RC server error {resp.status_code} for {email_lower}")
                return _RC_NETWORK_ERROR
            resp.raise_for_status()
            data = resp.json()
        active_items = ((data.get("active_entitlements") or {}).get("items")) or []
        active_items = [
            item for item in active_items
            if item.get("entitlement_id") in REVENUECAT_PRO_ENTITLEMENT_IDS
        ]
        if not active_items:
            return None
        # Pick the entitlement with the furthest-out (or no) expiration.
        best = None
        best_exp_ms = None
        for ent in active_items:
            exp_ms = ent.get("expires_at")
            if exp_ms is None:
                best = ent
                best_exp_ms = None
                break
            if best_exp_ms is None or exp_ms > best_exp_ms:
                best = ent
                best_exp_ms = exp_ms
        if not best:
            return None
        # Sync the DB so future checks hit the fast local path, and the
        # webhook (whenever it does arrive) just confirms what we already know.
        expires_iso = None
        if best_exp_ms is not None:
            try:
                expires_iso = datetime.fromtimestamp(best_exp_ms / 1000, tz=timezone.utc).isoformat()
            except Exception:
                expires_iso = None
        now_iso = datetime.now(timezone.utc).isoformat()
        try:
            await db.apple_iap_subscriptions.update_one(
                {"email": email_lower},
                {"$set": {
                    "email":       email_lower,
                    "status":      "active",
                    "productId":   best.get("entitlement_id", ""),
                    "expiresAt":   expires_iso,
                    "updatedAt":   now_iso,
                    "source":      "revenuecat_live_fallback",
                }},
                upsert=True,
            )
        except Exception as persistence_error:
            # RevenueCat's live response is the access decision. Atlas/cache
            # storage is only a speed-up and may be temporarily read-only
            # (for example when the cluster quota is full).
            print(f"[REVENUECAT LIVE FALLBACK] cache write skipped: {type(persistence_error).__name__}")
        return "Premium (Apple)"
    except Exception as e:
        print(f"[REVENUECAT LIVE FALLBACK] Network/timeout error for {email_lower}: {e}")
        return _RC_NETWORK_ERROR


def _rc_customer_id_is_allowed_for_email(
    customer_id: str,
    email_lower: str,
    *,
    allow_anonymous: bool = False,
) -> bool:
    """Prevent one signed-in account from claiming another named RC customer.

    Guest purchases are represented by RevenueCat's anonymous IDs. Once the
    account exists, the app uses the normalized email as its RC app user ID.
    The server never accepts an arbitrary named customer ID supplied by a
    modified client.
    """
    customer_id = customer_id.strip()
    return customer_id == email_lower or (
        allow_anonymous and customer_id.startswith("$RCAnonymousID:")
    )


async def _verify_revenuecat_purchase(
    customer_id: str,
    email_lower: str,
    *,
    allow_anonymous: bool = False,
) -> dict | None:
    """Verify an Apple entitlement directly with RevenueCat V2.

    The request body is intentionally not allowed to provide a product or
    expiry. Those values are derived here from RevenueCat's server response.
    The returned record is also written to an identity-only history collection
    so account deletion cannot reset Apple trial history.
    """
    customer_id = (customer_id or "").strip()
    if not customer_id or not email_lower or not _rc_customer_id_is_allowed_for_email(
        customer_id, email_lower, allow_anonymous=allow_anonymous
    ):
        raise HTTPException(status_code=403, detail="RevenueCat customer identity does not match this account.")

    key = os.environ.get("REVENUECAT_SECRET_API_KEY", "")
    if not key:
        print("[REVENUECAT VERIFY] secret API key is not configured")
        raise HTTPException(status_code=503, detail="Subscription verification is temporarily unavailable.")

    base = f"https://api.revenuecat.com/v2/projects/{REVENUECAT_PROJECT_ID}"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            customer_resp = await client.get(
                f"{base}/customers/{customer_id}",
                headers={"Authorization": f"Bearer {key}"},
            )
            if customer_resp.status_code == 404:
                raise HTTPException(status_code=402, detail="No active Apple subscription was found.")
            if customer_resp.status_code >= 500:
                raise RuntimeError(f"RevenueCat customer verification returned {customer_resp.status_code}")
            customer_resp.raise_for_status()
            customer = customer_resp.json()

            subs_resp = await client.get(
                f"{base}/customers/{customer_id}/subscriptions",
                headers={"Authorization": f"Bearer {key}"},
            )
            if subs_resp.status_code == 404:
                subscriptions = []
            elif subs_resp.status_code >= 500:
                raise RuntimeError(f"RevenueCat subscription verification returned {subs_resp.status_code}")
            else:
                subs_resp.raise_for_status()
                subscriptions = (subs_resp.json().get("items") or [])
    except HTTPException:
        raise
    except Exception as exc:
        print(f"[REVENUECAT VERIFY] network/API error for {customer_id[:32]}: {exc}")
        raise HTTPException(status_code=503, detail="Subscription verification is temporarily unavailable.")

    active_entitlements = (customer.get("active_entitlements") or {}).get("items") or []
    if not any(
        item.get("entitlement_id") in REVENUECAT_PRO_ENTITLEMENT_IDS
        for item in active_entitlements
    ):
        raise HTTPException(status_code=402, detail="No active Pro subscription was found.")
    if not active_entitlements:
        raise HTTPException(status_code=402, detail="No active Apple subscription was found.")

    # Only an Apple subscription that RevenueCat says currently gives access
    # may activate the account. Do not trust the mobile entitlement object.
    apple_subs = [
        sub for sub in subscriptions
        if sub.get("store") == "app_store" and sub.get("gives_access") is True
    ]
    if not apple_subs:
        raise HTTPException(status_code=402, detail="No active Apple subscription was found.")

    best_sub = max(
        apple_subs,
        key=lambda sub: sub.get("current_period_ends_at") or sub.get("ends_at") or 0,
    )
    entitlement_expiries = [
        item.get("expires_at") for item in active_entitlements
        if item.get("expires_at") is not None
    ]
    expiry_ms = max(
        [value for value in entitlement_expiries if isinstance(value, (int, float))]
        + [value for value in [
            best_sub.get("current_period_ends_at"),
            best_sub.get("ends_at"),
        ] if isinstance(value, (int, float))]
        or [None]
    )
    expires_iso = None
    if expiry_ms is not None:
        expires_iso = datetime.fromtimestamp(expiry_ms / 1000, tz=timezone.utc).isoformat()

    now_iso = datetime.now(timezone.utc).isoformat()
    trialing = best_sub.get("status") == "trialing"
    identity_doc = {
        "revenueCatCustomerId": customer_id,
        "originalRevenueCatCustomerId": best_sub.get("original_customer_id") or customer_id,
        "originalTransactionId": best_sub.get("store_subscription_identifier"),
        "latestTransactionId": best_sub.get("store_subscription_identifier"),
        "environment": best_sub.get("environment", "production"),
        "store": best_sub.get("store"),
        "trialUsed": trialing,
        "lastVerifiedAt": now_iso,
    }
    # This collection intentionally contains no email. It survives account
    # deletion and prevents local account state from becoming a trial reset.
    try:
        await db.apple_iap_customer_history.update_one(
            {"revenueCatCustomerId": customer_id},
            {
                "$set": identity_doc,
                "$setOnInsert": {"firstSeenAt": now_iso},
                "$addToSet": {"transactionIds": best_sub.get("store_subscription_identifier")},
            },
            upsert=True,
        )
    except Exception as persistence_error:
        # The entitlement was already verified directly with RevenueCat.
        # Identity history is durable bookkeeping, not an access prerequisite.
        print(f"[REVENUECAT VERIFY] identity history write skipped: {type(persistence_error).__name__}")

    return {
        "customer_id": customer_id,
        "original_customer_id": identity_doc["originalRevenueCatCustomerId"],
        "product_id": best_sub.get("product_id"),
        "store_transaction_id": best_sub.get("store_subscription_identifier"),
        "status": "trialing" if trialing else "active",
        "expires_at": expires_iso,
        "expires_at_ms": expiry_ms,
        "environment": best_sub.get("environment", "production"),
        "trialing": trialing,
        "verified_at": now_iso,
    }

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
    return f"{random.choice(_COLORS)}_{random.choice(_ANIMALS)}_{random.randint(1,9999)}"

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

async def _best_effort_session_update(query: dict, update: dict) -> None:
    """Keep authenticated reads working when Atlas blocks noncritical writes."""
    try:
        await db.sessions.update_one(query, update)
    except Exception as exc:
        print(
            f"[AUTH SESSION WRITE] skipped; keeping session usable: "
            f"{type(exc).__name__}: {exc}"
        )

# ── Web access check (Stripe / manual grants) ────────────────────────
async def _check_access_local(email_lower: str):
    # A new active website subscription must take precedence over the
    # historical migration screenshot cutoff for returning customers.
    active_stripe = await db.stripe_subscriptions.find_one(
        {
            "email": email_lower,
            "status": {"$in": ["active", "trialing"]},
            "retiredByMigration": {"$ne": True},
        },
        {"_id": 0},
    )
    if active_stripe:
        return "Premium (Stripe)"
    if _screenshot_stripe_access_expired(email_lower):
        return None
    # The retirement marker only prevents renewal/restoration. It must not
    # remove access that Stripe's dashboard confirms is already paid through
    # the listed cancellation date.
    if email_lower in STRIPE_SCREENSHOT_CUTOFFS:
        return "Premium (Stripe)"
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
    """Confirm an active website Stripe subscription when the webhook is late."""
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
                try:
                    await db.stripe_subscriptions.update_one(
                        {"email": email_lower},
                        {"$set": {"email": email_lower, "stripeSubscriptionId": sub_id, "status": "canceled",
                                  "canceledAt": now_str, "currentPeriodEnd": end_iso, "updatedAt": now_str,
                                  "source": "stripe", "autoRestored": True}},
                        upsert=True,
                    )
                except Exception as _db_err:
                    print(f"[STRIPE LIVE FALLBACK] canceled-state sync skipped: {_db_err}")
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
        try:
            await db.stripe_subscriptions.update_one(
                {"email": email_lower},
                {"$set": {"email": email_lower, "stripeSubscriptionId": sub_id, "planKey": plan_key,
                          "status": st, "currentPeriodEnd": end_iso, "subscribedAt": now,
                          "updatedAt": now, "source": "stripe", "autoRestored": True},
                 "$unset": {"canceledAt": "", "retiredByMigration": ""}},
                upsert=True,
            )
        except Exception as _db_err:
            print(f"[STRIPE LIVE FALLBACK] active-state sync skipped: {_db_err}")
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
    # 2) Apple IAP (RevenueCat / App Store) — local DB first
    apple = await _check_apple_access(email_lower)
    if apple:
        return apple
    # 3) Live RevenueCat fallback (covers missed/delayed webhooks)
    apple_live = await _check_revenuecat_live(email_lower)
    if apple_live and apple_live != _RC_NETWORK_ERROR:
        return apple_live
    # 4) Live Stripe fallback
    return await _check_stripe_live(email_lower)


async def check_web_access(email_lower: str):
    """Full web access check: local grants and existing Stripe periods only."""
    if not email_lower:
        return None
    result = await _check_access_local(email_lower)
    if result:
        return result
    return None

# ── Web endpoints (Stripe / website login) ───────────────────────────
@router.post("/verify-access")
@router.post("/verify-whop")
async def verify_access(req: VerifyAccessRequest):
    email_lower = req.email.lower().strip()

    # ── Owner PIN gate (web only) ─────────────────────────────────────────────
    # The pin field is Optional[str]:
    #   - None  → request came from the native app (old binary sends no pin field) → bypass gate, auto-login
    #   - ""    → web app opened PIN screen but user hasn't typed yet → demand code
    #   - "..." → web app submitted a code → validate it
    # This lets the current App Store build auto-login while the web always requires the code.
    if _OWNER_GATE_CODE and email_lower in OWNER_EMAILS and req.pin is not None:
        supplied = req.pin.strip()
        print(f"[AUTH] owner gate (web) email={email_lower} supplied_len={len(supplied)} match={supplied == _OWNER_GATE_CODE}")
        if supplied != _OWNER_GATE_CODE:
            return {
                "verified": False,
                "owner_pin_required": True,
                "email": email_lower,
                "message": "Incorrect code. Try again." if supplied else "Enter your access code.",
            }

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
    revenuecat_customer_id: str

# Keep a short rolling window because email delivery can arrive out of order.
# Every entry still expires after ten minutes and is marked used individually.
OTP_HISTORY_LIMIT = 10

def _parse_otp_expiry(raw):
    try:
        value = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
    except (TypeError, ValueError):
        return None

def _otp_entries(record: dict | None) -> list[dict]:
    """Normalize rolling OTP records and the legacy single-code shape."""
    if not record:
        return []
    raw_entries = record.get("codes")
    if not isinstance(raw_entries, list):
        raw_entries = [{
            "code": record.get("code"),
            "expiresAt": record.get("expiresAt"),
            "createdAt": record.get("createdAt"),
            "used": bool(record.get("used")),
        }]
    return [
        entry for entry in raw_entries
        if isinstance(entry, dict) and str(entry.get("code") or "").strip()
    ]

# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/send-code")
async def send_code(req: SendCodeRequest):
    """Step 1: email user a 6-digit OTP."""
    email_lower = req.email.lower().strip()
    if not email_lower or "@" not in email_lower:
        raise HTTPException(status_code=400, detail="Invalid email address.")

    code = _gen_code()
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)

    otp_doc = {
        "email":      email_lower,
        "code":       code,
        "expiresAt":  expires_at.isoformat(),
        "used":       False,
        "createdAt":  datetime.now(timezone.utc).isoformat(),
    }

    async def _store_code() -> None:
        existing = await db.auth_codes.find_one(
            {"email": email_lower},
            {"_id": 0, "codes": 1, "code": 1, "expiresAt": 1, "createdAt": 1, "used": 1},
        )
        now = datetime.now(timezone.utc)
        retained = []
        for entry in _otp_entries(existing):
            expires = _parse_otp_expiry(entry.get("expiresAt"))
            # Expired entries cannot authenticate and only make it harder to
            # reason about which emailed code is still usable.
            if expires and expires > now:
                retained.append(entry)
        entries = (retained + [{
            "code": code,
            "expiresAt": expires_at.isoformat(),
            "createdAt": now.isoformat(),
            "used": False,
        }])[-OTP_HISTORY_LIMIT:]
        await db.auth_codes.update_one(
            {"email": email_lower},
            {
                "$set": {
                    **otp_doc,
                    "codes": entries,
                },
            },
            upsert=True,
        )

    try:
        await _store_code()
    except Exception as exc:
        # Login must not surface an opaque 500 when Atlas is full. Reclaim
        # disposable caches and retry the durable OTP write once.
        if "space quota" in str(exc).lower() or "storage" in str(exc).lower():
            try:
                from routes.picks import (
                    _emergency_cache_cleanup_for_save,
                    _purge_regenerable_cache_collections_for_save,
                )
                await _emergency_cache_cleanup_for_save()
                await _purge_regenerable_cache_collections_for_save()
                await _store_code()
            except Exception as retry_exc:
                if "space quota" in str(retry_exc).lower() or "storage" in str(retry_exc).lower():
                    raise HTTPException(
                        status_code=503,
                        detail=(
                            "Login is temporarily unavailable because the database "
                            "storage is full. Please try again shortly."
                        ),
                    ) from retry_exc
                raise
        else:
            raise

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
    entries = _otp_entries(record)
    now = datetime.now(timezone.utc)
    matched_index = None
    matched_used = False
    matched_expired = False
    has_active_entry = False

    for index, entry in enumerate(entries):
        expires = _parse_otp_expiry(entry.get("expiresAt"))
        is_expired = expires is None or now > expires
        if not entry.get("used") and not is_expired:
            has_active_entry = True
        if str(entry.get("code") or "").strip() != code_input:
            continue
        if entry.get("used"):
            matched_used = True
        elif is_expired:
            matched_expired = True
        else:
            matched_index = index
            break

    if matched_index is None:
        if matched_used:
            raise HTTPException(status_code=400, detail="Code already used. Please request a new one.")
        if matched_expired or not has_active_entry:
            raise HTTPException(status_code=400, detail="Code expired. Please request a new one.")
        raise HTTPException(status_code=400, detail="Incorrect code. Try again.")

    # Mark only the accepted code used. A newer or delayed email may still
    # contain another valid entry in the rolling window.
    entries[matched_index] = {
        **entries[matched_index],
        "used": True,
        "usedAt": now.isoformat(),
    }
    latest_used = bool(entries[-1].get("used")) if entries else True
    await db.auth_codes.update_one(
        {"email": email_lower},
        {"$set": {"codes": entries, "used": latest_used}},
    )

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
    # Presence is based on authenticated app activity, not message history.
    # Sessions are stored with ISO strings, so keep this write in the same
    # representation used by create_session and the presence queries.
    await _best_effort_session_update(
        {"email": email_lower, "session_token": token},
        {"$set": {"last_active": datetime.now(timezone.utc).isoformat()}},
    )

    # Owner always valid
    if access_type == "Owner":
        return {"valid": True, "access_type": "Owner"}

    # A NoSubscription session may be stale: the device can complete an Apple
    # restore/purchase after the session was created, or a Stripe webhook can
    # arrive after the session was cached. Use the unified server-side access
    # check here so every protected route sees the same current truth.
    if access_type == "NoSubscription":
        current = await check_access(email_lower)
        # _RC_NETWORK_ERROR means verification was unavailable, not that the
        # customer is unsubscribed. Keep the session conservative in that case.
        if current and current != _RC_NETWORK_ERROR:
            await _best_effort_session_update(
                {"email": email_lower, "session_token": token},
                {"$set": {"access_type": current, "last_active": datetime.now(timezone.utc).isoformat()}},
            )
            return {"valid": True, "access_type": current}
        return {"valid": True, "access_type": "NoSubscription"}

    # Apple IAP sessions — re-check Apple (local DB, then live RevenueCat fallback)
    if "Apple" in access_type:
        current = await _check_apple_access(email_lower)
        if not current:
            current = await _check_revenuecat_live(email_lower)
        # _RC_NETWORK_ERROR means RevenueCat was unreachable / timed out.
        # Never delete a paying subscriber's session because of a network hiccup
        # — keep the existing access_type so the user stays logged in.
        if current == _RC_NETWORK_ERROR:
            print(f"[VERIFY SESSION] RC unreachable for {email_lower} — keeping current access_type={access_type}")
            return {"valid": True, "access_type": access_type}
        if not current:
            if access_type == "NoSubscription":
                return {"valid": True, "access_type": "NoSubscription"}
            # Apple user with definitively no entitlement (RC said 404/empty).
            # Don't outright delete — downgrade to NoSubscription so the paywall
            # can re-check on the device side (RC SDK) and recover automatically.
            print(f"[VERIFY SESSION] Apple user {email_lower} — no RC entitlement found, downgrading to NoSubscription")
            await _best_effort_session_update(
                {"email": email_lower, "session_token": token},
                {"$set": {"access_type": "NoSubscription", "last_active": datetime.now(timezone.utc).isoformat()}},
            )
            return {"valid": True, "access_type": "NoSubscription"}
        if current != access_type:
            await _best_effort_session_update(
                {"email": email_lower, "session_token": token},
                {"$set": {"access_type": current, "last_active": datetime.now(timezone.utc).isoformat()}},
            )
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


@router.post("/heartbeat")
async def heartbeat(req: VerifySessionRequest):
    """Refresh authenticated presence without re-checking subscription state."""
    email_lower = req.email.lower().strip()
    session = await db.sessions.find_one(
        {"email": email_lower, "session_token": req.session_token},
        {"_id": 1},
    )
    if not session:
        raise HTTPException(status_code=401, detail="Invalid session")
    await _best_effort_session_update(
        {"email": email_lower, "session_token": req.session_token},
        {"$set": {"last_active": datetime.now(timezone.utc).isoformat()}},
    )
    return {"ok": True}


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
        access_type = await _check_revenuecat_live(email_lower)
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
    """Fast-path after purchase, backed by a server-side RevenueCat check."""
    email_lower = req.email.lower().strip()
    session = await db.sessions.find_one(
        {"email": email_lower, "session_token": req.session_token}, {"_id": 0}
    )
    if not session:
        raise HTTPException(status_code=401, detail="Invalid session")

    # Restore can legitimately return RevenueCat's anonymous StoreKit
    # customer ID on an older app build or after reinstall. RevenueCat still
    # verifies the active Apple entitlement server-side; allowing that
    # identity here lets the authenticated account be linked during recovery.
    verified = await _verify_revenuecat_purchase(
        req.revenuecat_customer_id,
        email_lower,
        allow_anonymous=True,
    )
    now_iso = verified["verified_at"]

    try:
        await db.apple_iap_subscriptions.update_one(
            {"email": email_lower},
            {"$set": {
                "email":     email_lower,
                "status":    "active",
                "productId": verified.get("product_id"),
                "expiresAt": verified.get("expires_at"),
                "revenueCatCustomerId": verified["customer_id"],
                "originalRevenueCatCustomerId": verified["original_customer_id"],
                "storeTransactionId": verified.get("store_transaction_id"),
                "environment": verified.get("environment"),
                "trialing": verified.get("trialing", False),
                "lastVerifiedAt": now_iso,
                "updatedAt": now_iso,
            }},
            upsert=True,
        )
    except Exception as persistence_error:
        print(f"[IAP GRANT] subscription cache write skipped: {type(persistence_error).__name__}")
    try:
        await db.sessions.update_one(
            {"email": email_lower},
            {"$set": {"access_type": "Premium (Apple)", "last_active": now_iso}},
        )
    except Exception as persistence_error:
        print(f"[IAP GRANT] session access write skipped: {type(persistence_error).__name__}")
    print(f"[IAP GRANT] {email_lower} → Premium (Apple) | customer={verified['customer_id'][:32]} | expires={verified.get('expires_at')}")
    return {"ok": True, "access_type": "Premium (Apple)"}


# ── Pydantic models for new endpoints ─────────────────────────────────────────
class DeleteAccountRequest(BaseModel):
    email: str
    session_token: str

class IAPSignupRequest(BaseModel):
    email: str
    revenuecat_customer_id: str


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

    # Wipe all user data. RevenueCat identity/trial history is deliberately
    # kept in the identity-only collection, without email, so deletion cannot
    # be used to reset Apple's one-introductory-offer-per-group rule.
    await db.sessions.delete_many({"email": email_lower})
    await db.apple_iap_subscriptions.delete_many({"email": email_lower})
    await db.stripe_subscriptions.delete_many({"email": email_lower})
    await db.picks.delete_many({"email": email_lower})
    await db.manual_access_grants.delete_many({"email": email_lower})
    await db.users.delete_many({"email": email_lower})

    print(f"[DELETE ACCOUNT] {email_lower} — account and all data permanently deleted")
    return {"ok": True, "message": "Account deleted successfully."}


@router.post("/iap-signup")
async def iap_signup(req: IAPSignupRequest):
    """Create a new account from a completed Apple IAP purchase (no prior session required)."""
    email_lower = req.email.lower().strip()
    if not email_lower or "@" not in email_lower:
        raise HTTPException(status_code=400, detail="A valid email address is required.")

    verified = await _verify_revenuecat_purchase(
        req.revenuecat_customer_id,
        email_lower,
        allow_anonymous=True,
    )

    # Honour owner / lifetime overrides
    if email_lower in OWNER_EMAILS:
        access_type = "Owner"
    elif email_lower in LIFETIME_SUB_EMAILS:
        access_type = "Lifetime"
    else:
        access_type = "Premium (Apple)"

    now_iso = verified["verified_at"]

    await db.apple_iap_subscriptions.update_one(
        {"email": email_lower},
        {"$set": {
            "email":     email_lower,
            "status":    "active",
            "productId": verified.get("product_id"),
            "expiresAt": verified.get("expires_at"),
            "revenueCatCustomerId": verified["customer_id"],
            "originalRevenueCatCustomerId": verified["original_customer_id"],
            "storeTransactionId": verified.get("store_transaction_id"),
            "environment": verified.get("environment"),
            "trialing": verified.get("trialing", False),
            "lastVerifiedAt": now_iso,
            "updatedAt": now_iso,
        }},
        upsert=True,
    )

    token = await create_session(email_lower, access_type)
    print(f"[IAP SIGNUP] {email_lower} | customer={verified['customer_id'][:32]} | expires={verified.get('expires_at')}")
    return {
        "verified": True,
        "email": email_lower,
        "session_token": token,
        "access_type": access_type,
        "has_access": True,
        "message": "Account created and access granted.",
    }
