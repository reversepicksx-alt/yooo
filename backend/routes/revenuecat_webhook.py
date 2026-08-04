import os
import hmac
import hashlib
from datetime import datetime, timezone
from fastapi import APIRouter, Request, HTTPException

from config import db

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])

WEBHOOK_SECRET = os.environ.get("REVENUECAT_WEBHOOK_SECRET", "")

ACTIVE_EVENTS   = {"INITIAL_PURCHASE", "RENEWAL", "UNCANCELLATION", "PRODUCT_CHANGE", "TRANSFER"}
CANCELED_EVENTS = {"CANCELLATION"}
EXPIRED_EVENTS  = {"EXPIRATION"}
BILLING_EVENTS  = {"BILLING_ISSUE"}


def _ms_to_iso(ms: int | None) -> str | None:
    if not ms:
        return None
    try:
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()
    except Exception:
        return None


@router.post("/revenuecat")
async def revenuecat_webhook(request: Request):
    auth_header = request.headers.get("Authorization", "")
    if WEBHOOK_SECRET and auth_header != WEBHOOK_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized")

    body = await request.json()
    event = body.get("event", {})

    event_type      = event.get("type", "")
    app_user_id     = (event.get("app_user_id") or "").strip().lower()
    product_id      = event.get("product_id", "")
    expiration_ms   = event.get("expiration_at_ms")
    purchased_ms    = event.get("purchased_at_ms")
    environment     = event.get("environment", "PRODUCTION")
    original_app_user_id = (event.get("original_app_user_id") or app_user_id).strip()
    transaction_id = event.get("transaction_id")
    original_transaction_id = event.get("original_transaction_id") or transaction_id
    period_type = event.get("period_type")
    is_trial_period = bool(event.get("is_trial_period")) or period_type in {"TRIAL", "INTRO"}

    if not app_user_id:
        return {"ok": True, "note": "no app_user_id — skipped"}

    now_iso     = datetime.now(timezone.utc).isoformat()
    expires_iso = _ms_to_iso(expiration_ms)
    bought_iso  = _ms_to_iso(purchased_ms)

    print(f"[RC WEBHOOK] {event_type} | user={app_user_id} | product={product_id} | env={environment} | expires={expires_iso}")

    if event_type in ACTIVE_EVENTS:
        await db.apple_iap_subscriptions.update_one(
            {"email": app_user_id},
            {"$set": {
                "email":       app_user_id,
                "status":      "active",
                "productId":   product_id,
                "expiresAt":   expires_iso,
                "purchasedAt": bought_iso,
                "updatedAt":   now_iso,
                "environment": environment,
                "revenueCatCustomerId": event.get("app_user_id"),
                "originalRevenueCatCustomerId": original_app_user_id,
                "storeTransactionId": transaction_id,
                "originalTransactionId": original_transaction_id,
                "periodType": period_type,
                "trialing": is_trial_period,
            }},
            upsert=True,
        )
        await db.apple_iap_customer_history.update_one(
            {"revenueCatCustomerId": event.get("app_user_id")},
            {
                "$set": {
                    "originalRevenueCatCustomerId": original_app_user_id,
                    "originalTransactionId": original_transaction_id,
                    "latestTransactionId": transaction_id,
                    "environment": environment.lower(),
                    "trialUsed": is_trial_period,
                    "lastWebhookAt": now_iso,
                },
                "$setOnInsert": {"firstSeenAt": now_iso},
                "$addToSet": {"transactionIds": transaction_id},
            },
            upsert=True,
        )

    elif event_type in CANCELED_EVENTS:
        await db.apple_iap_subscriptions.update_one(
            {"email": app_user_id},
            {"$set": {
                "status":    "canceled",
                "expiresAt": expires_iso,
                "updatedAt": now_iso,
            }},
        )

    elif event_type in EXPIRED_EVENTS:
        await db.apple_iap_subscriptions.update_one(
            {"email": app_user_id},
            {"$set": {
                "status":    "expired",
                "expiresAt": expires_iso,
                "updatedAt": now_iso,
            }},
        )

    elif event_type in BILLING_EVENTS:
        await db.apple_iap_subscriptions.update_one(
            {"email": app_user_id},
            {"$set": {
                "status":    "billing_issue",
                "updatedAt": now_iso,
            }},
        )

    else:
        print(f"[RC WEBHOOK] Unhandled event type: {event_type} — no DB change")

    return {"ok": True}
