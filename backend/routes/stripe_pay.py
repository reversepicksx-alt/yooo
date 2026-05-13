import os
import stripe
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from config import db

router = APIRouter(prefix="/api/stripe", tags=["stripe"])

STRIPE_PLANS = {
    "weekly":    {"name": "Weekly",    "amount": 1500,  "interval": "week",  "interval_count": 1, "label": "$15/week",        "price_id": "price_1TTPgOE5jSGb860HYBTZ6emm"},
    "monthly":   {"name": "Monthly",   "amount": 4999,  "interval": "month", "interval_count": 1, "label": "$49.99/month",    "price_id": "price_1TTPgOE5jSGb860Hco39c7bc"},
    "quarterly": {"name": "Quarterly", "amount": 9999,  "interval": "month", "interval_count": 3, "label": "$99.99/3 months", "price_id": None},
}

# Versioned lookup keys — used as fallback when no price_id is hardcoded.
STRIPE_LOOKUP_KEY_VERSION = {
    "weekly":    "reversepicks_weekly_v2",
    "monthly":   "reversepicks_monthly_v2",
    "quarterly": "reversepicks_quarterly",
}


def get_stripe():
    key = os.environ.get("STRIPE_SECRET_KEY", "")
    if not key:
        raise HTTPException(status_code=500, detail="Stripe is not configured.")
    stripe.api_key = key
    return stripe


def _get_or_create_price(plan_key: str) -> str:
    """Return Stripe Price ID for plan_key, creating it if needed."""
    plan = STRIPE_PLANS[plan_key]
    # Use hardcoded price ID when available — avoids any lookup key ambiguity.
    if plan.get("price_id"):
        return plan["price_id"]
    # Fallback: find or create via versioned lookup key (used for quarterly).
    lookup_key = STRIPE_LOOKUP_KEY_VERSION.get(plan_key, f"reversepicks_{plan_key}")
    prices = stripe.Price.list(
        lookup_keys=[lookup_key],
        expand=["data.product"],
    )
    if prices.data:
        return prices.data[0].id

    price = stripe.Price.create(
        unit_amount=plan["amount"],
        currency="usd",
        recurring={
            "interval": plan["interval"],
            "interval_count": plan["interval_count"],
        },
        product_data={"name": f"ReversePicks {plan['name']}"},
        lookup_key=lookup_key,
    )
    return price.id


class CheckoutRequest(BaseModel):
    email: str
    planKey: str
    redirectUrl: str = ""


def _get_or_create_stripe_customer(email: str) -> str:
    """
    Return the existing Stripe customer ID for this email, or create a new one.
    Prevents duplicate customer records when a user subscribes more than once.
    Uses the customer with the most recent active/paid subscription to avoid
    returning a stale/incomplete duplicate.
    """
    email_lower = email.lower().strip()
    customers = stripe.Customer.list(email=email_lower, limit=10)
    best_cust_id = None
    best_ts = 0
    for cust in customers.data:
        # Prefer customers that have at least one paid subscription
        subs = stripe.Subscription.list(customer=cust.id, status="active", limit=1)
        if subs.data:
            if cust.created > best_ts:
                best_ts = cust.created
                best_cust_id = cust.id
    if not best_cust_id and customers.data:
        # Fall back to most recently created customer
        best_cust_id = sorted(customers.data, key=lambda c: c.created, reverse=True)[0].id
    if best_cust_id:
        print(f"[STRIPE] Reusing existing customer {best_cust_id} for {email_lower}")
        return best_cust_id
    # No existing customer — create one
    new_cust = stripe.Customer.create(email=email_lower, metadata={"email": email_lower})
    print(f"[STRIPE] Created new customer {new_cust.id} for {email_lower}")
    return new_cust.id


@router.post("/create-checkout")
async def create_checkout(req: CheckoutRequest):
    plan_key = req.planKey.lower()
    if plan_key not in STRIPE_PLANS:
        raise HTTPException(status_code=400, detail=f"Unknown plan: {plan_key}")

    get_stripe()

    success_url = req.redirectUrl or "https://reversepicks.com/auth"
    cancel_url = req.redirectUrl or "https://reversepicks.com/auth"

    def _build_session(payment_method_types: list[str]) -> stripe.checkout.Session:
        return stripe.checkout.Session.create(
            mode="subscription",
            customer=customer_id,
            line_items=[{"price": price_id, "quantity": 1}],
            success_url=success_url + "?stripe_success=1",
            cancel_url=cancel_url,
            payment_method_types=payment_method_types,
            subscription_data={
                "metadata": {
                    "email": email_lower,
                    "plan_key": plan_key,
                }
            },
            metadata={
                "email": email_lower,
                "plan_key": plan_key,
            },
            allow_promotion_codes=True,
        )

    try:
        price_id = _get_or_create_price(plan_key)
        email_lower = req.email.lower().strip()

        # Reuse existing Stripe customer to prevent duplicate accounts
        customer_id = _get_or_create_stripe_customer(email_lower)

        # Try with expanded payment methods first (Cash App Pay + Stripe Link).
        # Cash App Pay / Link let users pay even if their bank blocks card subscriptions.
        # Fall back to card-only if the Stripe account doesn't have those methods enabled.
        try:
            session = _build_session(["card", "cashapp", "link"])
            print(f"[STRIPE] Checkout created with card+cashapp+link for {email_lower}")
        except stripe.InvalidRequestError as _pmt_err:
            print(f"[STRIPE] Extended payment methods unavailable ({_pmt_err}), falling back to card-only")
            session = _build_session(["card"])

        return {"checkoutUrl": session.url}
    except stripe.StripeError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/resubscribe-checkout")
async def resubscribe_checkout(req: CheckoutRequest):
    return await create_checkout(req)


class StatusRequest(BaseModel):
    email: str


@router.get("/status/{email}")
async def get_status(email: str):
    email_lower = email.lower().strip()
    sub = await db.stripe_subscriptions.find_one({"email": email_lower}, {"_id": 0})
    if not sub:
        return {"active": False}

    plan_key = sub.get("planKey", "monthly")
    plan_info = STRIPE_PLANS.get(plan_key, {})

    return {
        "active": sub.get("status") in ("active", "trialing"),
        "plan": plan_info.get("name", plan_key),
        "planKey": plan_key,
        "planLabel": plan_info.get("label", ""),
        "status": sub.get("status", ""),
        "subscribedAt": sub.get("subscribedAt", ""),
        "expiresAt": sub.get("currentPeriodEnd", ""),
        "canceledAt": sub.get("canceledAt", ""),
        "source": "stripe",
    }


class CancelRequest(BaseModel):
    email: str


@router.post("/cancel")
async def cancel_subscription(req: CancelRequest):
    email_lower = req.email.lower().strip()
    sub = await db.stripe_subscriptions.find_one({"email": email_lower}, {"_id": 0})
    if not sub or not sub.get("stripeSubscriptionId"):
        raise HTTPException(status_code=404, detail="No active Stripe subscription found.")

    get_stripe()
    try:
        stripe.Subscription.modify(
            sub["stripeSubscriptionId"],
            cancel_at_period_end=True,
        )
        await db.stripe_subscriptions.update_one(
            {"email": email_lower},
            {"$set": {"status": "canceled", "canceledAt": datetime.now(timezone.utc).isoformat()}}
        )
        return {"success": True, "message": "Subscription will cancel at end of billing period."}
    except stripe.StripeError as e:
        raise HTTPException(status_code=500, detail=str(e))


class ChangePlanRequest(BaseModel):
    email: str
    new_plan_key: str


@router.post("/change-plan")
async def change_plan(req: ChangePlanRequest):
    plan_key = req.new_plan_key.lower()
    if plan_key not in STRIPE_PLANS:
        raise HTTPException(status_code=400, detail=f"Unknown plan: {plan_key}")

    email_lower = req.email.lower().strip()
    sub = await db.stripe_subscriptions.find_one({"email": email_lower}, {"_id": 0})
    if not sub or not sub.get("stripeSubscriptionId"):
        raise HTTPException(status_code=404, detail="No active Stripe subscription found.")

    old_key = sub.get("planKey", "")

    # Guard: don't charge if already on this plan
    if old_key == plan_key:
        return {
            "success": True,
            "previous_plan": old_key,
            "new_plan": plan_key,
            "new_label": STRIPE_PLANS[plan_key]["label"],
            "message": "Already on this plan — no change made.",
        }

    get_stripe()
    try:
        price_id = _get_or_create_price(plan_key)
        stripe_sub = stripe.Subscription.retrieve(sub["stripeSubscriptionId"])
        stripe.Subscription.modify(
            sub["stripeSubscriptionId"],
            items=[{"id": stripe_sub["items"]["data"][0]["id"], "price": price_id}],
            # "none" = no immediate prorated invoice; new price takes effect at next renewal.
            # "always_invoice" was causing immediate charges (the $28.99 double-charge bug).
            proration_behavior="none",
        )
        await db.stripe_subscriptions.update_one(
            {"email": email_lower},
            {"$set": {"planKey": plan_key, "updatedAt": datetime.now(timezone.utc).isoformat()}}
        )
        return {
            "success": True,
            "previous_plan": old_key,
            "new_plan": plan_key,
            "new_label": STRIPE_PLANS[plan_key]["label"],
            "message": "Plan updated — new rate applies at your next renewal.",
        }
    except stripe.StripeError as e:
        raise HTTPException(status_code=500, detail=str(e))


def _stripe_to_dict(obj) -> dict:
    """Safely convert a Stripe SDK object (or plain dict) to a plain Python dict."""
    import json
    try:
        if hasattr(obj, "to_dict"):
            return obj.to_dict()
        if hasattr(obj, "to_json"):
            return json.loads(obj.to_json())
        return dict(obj)
    except Exception:
        return {}


@router.post("/webhook")
async def stripe_webhook(request: Request):
    import json as _json
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")
    webhook_secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "")

    get_stripe()

    if webhook_secret:
        try:
            raw_event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
        except stripe.SignatureVerificationError:
            raise HTTPException(status_code=400, detail="Invalid signature")
        # Convert to plain dict so .get() works everywhere
        try:
            event = _json.loads(raw_event.to_json())
        except Exception:
            event = _stripe_to_dict(raw_event)
    else:
        event = _json.loads(payload)

    etype = event.get("type", "")

    if etype == "checkout.session.completed":
        session = event.get("data", {}).get("object", {})
        meta = session.get("metadata") or {}
        email = (session.get("customer_email") or meta.get("email", "")).lower().strip()
        # Fallback: look up email from Stripe customer record (handles re-subscriptions
        # made outside our checkout flow where customer_email may be absent)
        if not email:
            email = await _email_from_customer(session.get("customer", ""))
        plan_key = meta.get("plan_key", "monthly")
        stripe_sub_id = session.get("subscription", "")
        if email and stripe_sub_id:
            await _upsert_stripe_sub(email, stripe_sub_id, plan_key, "active")

    elif etype in ("customer.subscription.updated", "customer.subscription.created"):
        sub_obj = event.get("data", {}).get("object", {})
        meta = sub_obj.get("metadata") or {}
        email = meta.get("email", "").lower().strip()
        if not email:
            email = await _email_from_customer(sub_obj.get("customer", ""))
        plan_key = meta.get("plan_key", "")
        if not plan_key:
            plan_key = await _plan_key_from_sub(sub_obj)
        status = sub_obj.get("status", "active")
        current_period_end = sub_obj.get("current_period_end")
        # Some Stripe plan types (e.g. weekly) store current_period_end only inside
        # items.data[0], not at the subscription top level. Fall back there.
        if not current_period_end:
            items_data = (sub_obj.get("items") or {})
            items_list = items_data.get("data", []) if isinstance(items_data, dict) else []
            if items_list:
                current_period_end = items_list[0].get("current_period_end")
        # Also check cancel_at as a period-end fallback (used when Stripe schedules
        # a hard cancellation date instead of end-of-period).
        if not current_period_end:
            current_period_end = sub_obj.get("cancel_at")
        end_iso = datetime.fromtimestamp(current_period_end, tz=timezone.utc).isoformat() if current_period_end else ""
        sub_id = sub_obj.get("id", "")
        cancel_at_period_end = sub_obj.get("cancel_at_period_end", False)
        # If cancel_at_period_end is true, the user has already canceled.
        # Stripe keeps status="active" until the period ends, but we track it as "canceled"
        # so our auth check can enforce expiry via currentPeriodEnd.
        # Guard: only mark canceled if we have a period-end date to enforce — otherwise
        # keep the Stripe status so the user isn't wrongly locked out.
        if cancel_at_period_end:
            if end_iso:
                status = "canceled"
            else:
                # No period end available — don't mark canceled; let next webhook or
                # live Stripe check sort it out when a valid end date is present.
                print(f"[STRIPE WEBHOOK] cancel_at_period_end=True for {email} but no period end found — keeping status={status}")
        if email and sub_id:
            await _upsert_stripe_sub(email, sub_id, plan_key, status, end_iso)

    elif etype == "customer.subscription.deleted":
        sub_obj = event.get("data", {}).get("object", {})
        meta = sub_obj.get("metadata") or {}
        email = meta.get("email", "").lower().strip()
        if not email:
            email = await _email_from_customer(sub_obj.get("customer", ""))
        if email:
            await db.stripe_subscriptions.update_one(
                {"email": email},
                {"$set": {
                    "status": "canceled",
                    "canceledAt": datetime.now(timezone.utc).isoformat(),
                    "updatedAt": datetime.now(timezone.utc).isoformat(),
                }}
            )
            await db.sessions.delete_many({"email": email})

    elif etype == "invoice.payment_failed":
        invoice = event.get("data", {}).get("object", {})
        email = await _email_from_customer(invoice.get("customer", ""))
        if email:
            await db.stripe_subscriptions.update_one(
                {"email": email},
                {"$set": {"status": "past_due", "updatedAt": datetime.now(timezone.utc).isoformat()}}
            )

    return {"received": True}


async def _upsert_stripe_sub(email: str, stripe_sub_id: str, plan_key: str, status: str, current_period_end: str = ""):
    plan_info = STRIPE_PLANS.get(plan_key, {})
    now = datetime.now(timezone.utc).isoformat()
    # Core fields always written
    set_fields: dict = {
        "email": email,
        "stripeSubscriptionId": stripe_sub_id,
        "planKey": plan_key,
        "planName": plan_info.get("name", plan_key),
        "status": status,
        "updatedAt": now,
        "source": "stripe",
    }
    # Only update currentPeriodEnd when we have a real value — never blank it out
    if current_period_end:
        set_fields["currentPeriodEnd"] = current_period_end
    # Track when a subscription was first marked canceled (don't overwrite if already set)
    if status == "canceled":
        set_fields.setdefault("canceledAt", now)

    update_op: dict = {
        "$set": set_fields,
        # subscribedAt is only written on insert (first time this email appears)
        "$setOnInsert": {"subscribedAt": now},
    }
    # When a subscription becomes active/trialing again, clear any stale canceledAt
    # so the auth hard-gate doesn't block users who have since resubscribed.
    if status in ("active", "trialing"):
        update_op["$unset"] = {"canceledAt": ""}

    await db.stripe_subscriptions.update_one(
        {"email": email},
        update_op,
        upsert=True,
    )


async def _email_from_customer(cust_id: str) -> str:
    if not cust_id:
        return ""
    try:
        import json as _json
        cust_raw = stripe.Customer.retrieve(cust_id)
        try:
            cust = _json.loads(cust_raw.to_json())
        except Exception:
            cust = _stripe_to_dict(cust_raw)
        return (cust.get("email") or "").lower().strip()
    except Exception:
        return ""


async def _plan_key_from_sub(sub_obj: dict) -> str:
    try:
        items_data = (sub_obj.get("items") or {})
        if hasattr(items_data, "get"):
            items = items_data.get("data", [])
        else:
            items = []
        if items:
            price = items[0].get("price") or {}
            lookup_key = price.get("lookup_key", "") or ""
            if lookup_key.startswith("reversepicks_"):
                return lookup_key.replace("reversepicks_", "")
            recurring = price.get("recurring") or {}
            interval = recurring.get("interval", "")
            interval_count = recurring.get("interval_count", 1)
            if interval == "week":
                return "weekly"
            elif interval == "month" and interval_count >= 3:
                return "quarterly"
            else:
                return "monthly"
    except Exception:
        pass
    return "monthly"
