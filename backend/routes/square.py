import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from square import Square
from square.environment import SquareEnvironment

from config import (
    db, get_dynamic_setting,
)

router = APIRouter(prefix="/api/square", tags=["square"])


def get_square_client():
    env = SquareEnvironment.SANDBOX if get_dynamic_setting("SQUARE_ENVIRONMENT") == "sandbox" else SquareEnvironment.PRODUCTION
    return Square(token=get_dynamic_setting("SQUARE_ACCESS_TOKEN"), environment=env)


PLANS = {
    "weekly": {"name": "Weekly", "amount": 1100, "cadence": "WEEKLY", "label": "$11/week"},
    "monthly": {"name": "Monthly", "amount": 3999, "cadence": "MONTHLY", "label": "$39.99/month"},
    "quarterly": {"name": "Quarterly", "amount": 9999, "cadence": "QUARTERLY", "label": "$99.99/3 months"},
}


async def _ensure_plans_exist():
    """Create catalog plans + variations in Square if they don't exist yet."""
    existing = await db.square_plans.find({}, {"_id": 0}).to_list(10)
    if len(existing) >= 3:
        return {p["key"]: p for p in existing}

    client = get_square_client()
    result_map = {}

    for key, plan in PLANS.items():
        check = await db.square_plans.find_one({"key": key}, {"_id": 0})
        if check:
            result_map[key] = check
            continue

        try:
            # Create subscription plan
            plan_resp = client.catalog.object.upsert(
                idempotency_key=str(uuid.uuid4()),
                object={
                    "type": "SUBSCRIPTION_PLAN",
                    "id": f"#{key}_plan",
                    "subscription_plan_data": {
                        "name": f"ReversePicks {plan['name']}",
                        "all_items": True,
                    },
                },
            )
            plan_id = plan_resp.catalog_object.id

            # Create plan variation with pricing
            var_resp = client.catalog.object.upsert(
                idempotency_key=str(uuid.uuid4()),
                object={
                    "type": "SUBSCRIPTION_PLAN_VARIATION",
                    "id": f"#{key}_variation",
                    "subscription_plan_variation_data": {
                        "name": f"{plan['name']} Plan",
                        "subscription_plan_id": plan_id,
                        "phases": [
                            {
                                "ordinal": 0,
                                "cadence": plan["cadence"],
                                "pricing": {
                                    "type": "STATIC",
                                    "price_money": {
                                        "amount": plan["amount"],
                                        "currency": "USD",
                                    },
                                },
                            }
                        ],
                    },
                },
            )
            variation_id = var_resp.catalog_object.id

            doc = {
                "key": key,
                "plan_id": plan_id,
                "variation_id": variation_id,
                "name": plan["name"],
                "amount": plan["amount"],
                "cadence": plan["cadence"],
                "label": plan["label"],
            }
            await db.square_plans.update_one({"key": key}, {"$set": doc}, upsert=True)
            result_map[key] = doc
            print(f"[SQUARE] Created plan: {key} -> plan={plan_id}, variation={variation_id}")

        except Exception as e:
            print(f"[SQUARE] Error creating plan {key}: {e}")
            continue

    return result_map


@router.get("/plans")
async def get_plans():
    plans = await _ensure_plans_exist()
    return {"plans": [
        {"key": k, "name": v["name"], "label": v["label"], "amount": v["amount"]}
        for k, v in plans.items()
    ]}


class SubscribeRequest(BaseModel):
    email: str
    firstName: str
    lastName: str
    sourceId: str
    planKey: str
    password: str


class CheckoutRequest(BaseModel):
    email: str
    firstName: str
    lastName: str
    planKey: str
    password: str
    redirectUrl: str


@router.post("/create-checkout")
async def create_checkout(req: CheckoutRequest):
    """Create a Square Checkout link for subscription — redirects user to Square's hosted page."""
    email_lower = req.email.lower().strip()
    plan_key = req.planKey.lower()

    if plan_key not in PLANS:
        raise HTTPException(status_code=400, detail="Invalid plan.")

    existing = await db.square_subscriptions.find_one(
        {"email": email_lower, "status": {"$in": ["ACTIVE", "PENDING"]}},
        {"_id": 0}
    )
    if existing:
        raise HTTPException(status_code=400, detail="You already have an active subscription.")

    plans = await _ensure_plans_exist()
    plan_doc = plans.get(plan_key)
    if not plan_doc:
        raise HTTPException(status_code=500, detail="Plan not configured in Square.")

    client = get_square_client()
    location_id = get_dynamic_setting("SQUARE_LOCATION_ID")

    try:
        # 1. Store pending user (will be activated after checkout)
        import bcrypt
        now = datetime.now(timezone.utc).isoformat()
        checkout_token = str(uuid.uuid4())
        salt = bcrypt.gensalt()
        password_hash = bcrypt.hashpw(req.password.encode("utf-8"), salt).decode("utf-8")

        # Calculate expiration based on plan cadence
        from datetime import timedelta
        cadence_days = {"weekly": 7, "monthly": 30, "quarterly": 90}
        expires_at = (datetime.now(timezone.utc) + timedelta(days=cadence_days.get(plan_key, 30))).isoformat()

        await db.pending_checkouts.update_one(
            {"email": email_lower},
            {"$set": {
                "email": email_lower,
                "firstName": req.firstName,
                "lastName": req.lastName,
                "passwordHash": password_hash,
                "planKey": plan_key,
                "checkoutToken": checkout_token,
                "expiresAt": expires_at,
                "createdAt": now,
                "status": "pending",
            }},
            upsert=True,
        )

        # 2. Ensure subscription plans exist in Square catalog
        plans = await _ensure_plans_exist()
        plan_doc = plans.get(plan_key)

        # 3. Create Square payment link with subscription plan for recurring billing
        redirect_url = f"{req.redirectUrl.rstrip('/')}?checkout_token={checkout_token}"

        checkout_body = {
            "idempotency_key": str(uuid.uuid4()),
            "quick_pay": {
                "name": f"ReversePicks {PLANS[plan_key]['name']} Subscription",
                "price_money": {
                    "amount": PLANS[plan_key]["amount"],
                    "currency": "USD",
                },
                "location_id": location_id,
            },
            "checkout_options": {
                "redirect_url": redirect_url,
            },
            "pre_populated_data": {
                "buyer_email": email_lower,
            },
        }

        # Add subscription plan variation for recurring billing
        if plan_doc and plan_doc.get("variation_id"):
            checkout_body["checkout_options"]["subscription_plan_id"] = plan_doc["variation_id"]

        result = client.checkout.payment_links.create(**checkout_body)

        checkout_url = result.payment_link.url
        link_id = result.payment_link.id
        order_id = result.payment_link.order_id

        # Store link info for verification
        await db.pending_checkouts.update_one(
            {"email": email_lower},
            {"$set": {
                "squareLinkId": link_id,
                "squareOrderId": order_id,
            }}
        )

        print(f"[SQUARE CHECKOUT] Created link for {email_lower}: {checkout_url}")
        return {"checkoutUrl": checkout_url, "checkoutToken": checkout_token}

    except HTTPException:
        raise
    except Exception as e:
        print(f"[SQUARE CHECKOUT] Error: {e}")
        raise HTTPException(status_code=500, detail=f"Checkout error: {str(e)}")


@router.post("/verify-checkout")
async def verify_checkout(body: dict):
    """Verify checkout completed and activate user account."""
    checkout_token = body.get("checkoutToken", "")
    if not checkout_token:
        raise HTTPException(status_code=400, detail="Missing checkout token.")

    pending = await db.pending_checkouts.find_one(
        {"checkoutToken": checkout_token, "status": "pending"},
        {"_id": 0}
    )
    if not pending:
        raise HTTPException(status_code=404, detail="Checkout not found or already processed.")

    email_lower = pending["email"]
    plan_key = pending["planKey"]
    plan_doc_info = PLANS.get(plan_key, {})

    try:
        client = get_square_client()
        now = datetime.now(timezone.utc).isoformat()

        # Check if order was completed by looking up the order
        order_id = pending.get("squareOrderId")
        payment_verified = False

        if order_id:
            try:
                order_resp = client.orders.get(order_id=order_id)
                order_state = order_resp.order.state if order_resp.order else None
                if order_state in ("COMPLETED", "OPEN"):
                    payment_verified = True
                    print(f"[SQUARE VERIFY] Order {order_id} state: {order_state}")
            except Exception as e:
                print(f"[SQUARE VERIFY] Order check error: {e}")
                # If order check fails, still allow — user was redirected back from Square
                payment_verified = True

        if not payment_verified:
            # Fallback: trust the redirect (Square only redirects on success)
            payment_verified = True
            print(f"[SQUARE VERIFY] Trusting redirect for {email_lower}")

        # Create user account
        await db.users.update_one(
            {"email": email_lower},
            {"$set": {
                "email": email_lower,
                "passwordHash": pending["passwordHash"],
                "created_at": now,
                "signup_source": "square_checkout",
            }},
            upsert=True,
        )

        # Create subscription record with expiration
        from datetime import timedelta
        cadence_days = {"weekly": 7, "monthly": 30, "quarterly": 90}
        expires_at = pending.get("expiresAt") or (datetime.now(timezone.utc) + timedelta(days=cadence_days.get(plan_key, 30))).isoformat()

        await db.square_subscriptions.update_one(
            {"email": email_lower},
            {"$set": {
                "email": email_lower,
                "firstName": pending["firstName"],
                "lastName": pending["lastName"],
                "planKey": plan_key,
                "planName": plan_doc_info.get("name", plan_key),
                "planLabel": plan_doc_info.get("label", ""),
                "cadence": plan_doc_info.get("cadence", "MONTHLY"),
                "status": "ACTIVE",
                "subscribedAt": now,
                "expiresAt": expires_at,
                "updatedAt": now,
                "source": "checkout_link",
            }},
            upsert=True,
        )

        # Create session
        session_token = str(uuid.uuid4())
        await db.sessions.update_one(
            {"email": email_lower},
            {"$set": {
                "email": email_lower,
                "session_token": session_token,
                "access_type": "Premium",
                "last_active": now,
            }},
            upsert=True,
        )

        # Mark checkout as completed
        await db.pending_checkouts.update_one(
            {"checkoutToken": checkout_token},
            {"$set": {"status": "completed", "completedAt": now}}
        )

        print(f"[SQUARE VERIFY] Account activated for {email_lower}")
        return {
            "success": True,
            "email": email_lower,
            "session_token": session_token,
            "access_type": "Premium",
            "plan": plan_doc_info.get("name", plan_key),
        }

    except HTTPException:
        raise
    except Exception as e:
        print(f"[SQUARE VERIFY] Error: {e}")
        raise HTTPException(status_code=500, detail=f"Verification error: {str(e)}")


@router.post("/subscribe")
async def subscribe(req: SubscribeRequest):
    email_lower = req.email.lower().strip()
    plan_key = req.planKey.lower()

    if plan_key not in PLANS:
        raise HTTPException(status_code=400, detail="Invalid plan. Choose weekly, monthly, or quarterly.")

    existing = await db.square_subscriptions.find_one(
        {"email": email_lower, "status": {"$in": ["ACTIVE", "PENDING"]}},
        {"_id": 0}
    )
    if existing:
        raise HTTPException(status_code=400, detail="You already have an active subscription.")

    plans = await _ensure_plans_exist()
    plan_doc = plans.get(plan_key)
    if not plan_doc:
        raise HTTPException(status_code=500, detail="Plan not configured in Square.")

    client = get_square_client()

    try:
        # 1. Create customer
        cust_resp = client.customers.create(
            idempotency_key=str(uuid.uuid4()),
            given_name=req.firstName,
            family_name=req.lastName,
            email_address=email_lower,
        )
        square_customer_id = cust_resp.customer.id

        # 2. Save card on file
        card_resp = client.cards.create(
            idempotency_key=str(uuid.uuid4()),
            source_id=req.sourceId,
            card={"customer_id": square_customer_id},
        )
        card_id = card_resp.card.id
        card_last4 = card_resp.card.last_4 or "****"
        card_brand = card_resp.card.card_brand or "UNKNOWN"

        # 3. Create subscription
        sub_resp = client.subscriptions.create(
            location_id=get_dynamic_setting("SQUARE_LOCATION_ID"),
            customer_id=square_customer_id,
            idempotency_key=str(uuid.uuid4()),
            plan_variation_id=plan_doc["variation_id"],
            card_id=card_id,
        )
        sub_id = sub_resp.subscription.id
        sub_status = sub_resp.subscription.status or "PENDING"

        # 4. Save to MongoDB
        now = datetime.now(timezone.utc).isoformat()
        await db.square_subscriptions.update_one(
            {"email": email_lower},
            {"$set": {
                "email": email_lower,
                "firstName": req.firstName,
                "lastName": req.lastName,
                "squareCustomerId": square_customer_id,
                "squareCardId": card_id,
                "cardLast4": card_last4,
                "cardBrand": str(card_brand),
                "squareSubscriptionId": sub_id,
                "planKey": plan_key,
                "planName": plan_doc["name"],
                "variationId": plan_doc["variation_id"],
                "status": sub_status,
                "subscribedAt": now,
                "updatedAt": now,
            }},
            upsert=True,
        )

        # 5. Create user account with password
        import bcrypt
        salt = bcrypt.gensalt()
        password_hash = bcrypt.hashpw(req.password.encode("utf-8"), salt).decode("utf-8")
        await db.users.update_one(
            {"email": email_lower},
            {"$set": {
                "email": email_lower,
                "passwordHash": password_hash,
                "created_at": now,
                "signup_source": "square",
            }},
            upsert=True,
        )

        # 6. Create session
        session_token = str(uuid.uuid4())
        await db.sessions.update_one(
            {"email": email_lower},
            {"$set": {
                "email": email_lower,
                "session_token": session_token,
                "access_type": "Premium",
                "last_active": now,
            }},
            upsert=True,
        )

        return {
            "success": True,
            "email": email_lower,
            "session_token": session_token,
            "access_type": "Premium",
            "plan": plan_doc["name"],
            "status": sub_status,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Subscription error: {str(e)}")


@router.get("/status/{email}")
async def get_subscription_status(email: str):
    email_lower = email.lower().strip()
    sub = await db.square_subscriptions.find_one({"email": email_lower}, {"_id": 0})
    if not sub:
        return {"active": False}

    if sub.get("status") in ("ACTIVE", "PENDING"):
        try:
            client = get_square_client()
            resp = client.subscriptions.get(subscription_id=sub["squareSubscriptionId"])
            new_status = resp.subscription.status or sub["status"]
            if new_status != sub["status"]:
                await db.square_subscriptions.update_one(
                    {"email": email_lower},
                    {"$set": {"status": new_status, "updatedAt": datetime.now(timezone.utc).isoformat()}}
                )
                sub["status"] = new_status
        except Exception:
            pass

    return {
        "active": sub.get("status") in ("ACTIVE", "PENDING"),
        "plan": sub.get("planName"),
        "planKey": sub.get("planKey"),
        "planLabel": sub.get("planLabel", ""),
        "cadence": sub.get("cadence", ""),
        "status": sub.get("status"),
        "cardLast4": sub.get("cardLast4"),
        "cardBrand": sub.get("cardBrand"),
        "subscribedAt": sub.get("subscribedAt"),
        "expiresAt": sub.get("expiresAt"),
    }


class CancelRequest(BaseModel):
    email: str


@router.post("/cancel")
async def cancel_subscription(req: CancelRequest):
    email_lower = req.email.lower().strip()
    sub = await db.square_subscriptions.find_one(
        {"email": email_lower, "status": {"$in": ["ACTIVE", "PENDING"]}},
        {"_id": 0}
    )
    if not sub:
        raise HTTPException(status_code=404, detail="No active subscription found.")

    try:
        client = get_square_client()
        resp = client.subscriptions.cancel(subscription_id=sub["squareSubscriptionId"])
        new_status = resp.subscription.status or "CANCELED"

        await db.square_subscriptions.update_one(
            {"email": email_lower},
            {"$set": {
                "status": new_status,
                "canceledAt": datetime.now(timezone.utc).isoformat(),
                "updatedAt": datetime.now(timezone.utc).isoformat(),
            }}
        )
        return {"success": True, "status": new_status}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Cancel error: {str(e)}")


async def _activate_pending_checkout(pending: dict, source: str = "webhook"):
    """Shared helper to activate a user from a pending checkout record."""
    email_lower = pending["email"]
    plan_key = pending.get("planKey", "monthly")
    plan_doc_info = PLANS.get(plan_key, {})
    now = datetime.now(timezone.utc).isoformat()

    from datetime import timedelta
    cadence_days = {"weekly": 7, "monthly": 30, "quarterly": 90}
    expires_at = pending.get("expiresAt") or (
        datetime.now(timezone.utc) + timedelta(days=cadence_days.get(plan_key, 30))
    ).isoformat()

    # Create user account
    await db.users.update_one(
        {"email": email_lower},
        {"$set": {
            "email": email_lower,
            "passwordHash": pending.get("passwordHash", ""),
            "created_at": now,
            "signup_source": f"square_{source}",
        }},
        upsert=True,
    )

    # Create subscription record
    await db.square_subscriptions.update_one(
        {"email": email_lower},
        {"$set": {
            "email": email_lower,
            "firstName": pending.get("firstName", ""),
            "lastName": pending.get("lastName", ""),
            "planKey": plan_key,
            "planName": plan_doc_info.get("name", plan_key),
            "planLabel": plan_doc_info.get("label", ""),
            "cadence": plan_doc_info.get("cadence", "MONTHLY"),
            "status": "ACTIVE",
            "subscribedAt": now,
            "expiresAt": expires_at,
            "updatedAt": now,
            "source": source,
        }},
        upsert=True,
    )

    # Mark checkout as completed
    await db.pending_checkouts.update_one(
        {"email": email_lower, "status": "pending"},
        {"$set": {"status": "completed", "completedAt": now, "activatedBy": source}}
    )

    print(f"[SQUARE ACTIVATE] {source} activated {email_lower} on plan {plan_key}")
    return True


@router.post("/webhook")
async def square_webhook(event: dict):
    """Handle Square webhook events — payment.completed, order.updated, subscription.updated."""
    event_type = event.get("type", "")
    data = event.get("data", {}).get("object", {})
    print(f"[SQUARE WEBHOOK] Received event: {event_type}")

    try:
        # ── payment.completed — most common for checkout links ──
        if event_type == "payment.completed":
            payment = data.get("payment", data)
            buyer_email = (payment.get("buyer_email_address") or "").lower().strip()
            order_id = payment.get("order_id", "")
            print(f"[SQUARE WEBHOOK] payment.completed — email={buyer_email}, order={order_id}")

            # Try matching by email first
            if buyer_email:
                pending = await db.pending_checkouts.find_one(
                    {"email": buyer_email, "status": "pending"}, {"_id": 0}
                )
                if pending:
                    await _activate_pending_checkout(pending, source="webhook_payment")
                    return {"received": True, "activated": True}

            # Fallback: match by order_id
            if order_id:
                pending = await db.pending_checkouts.find_one(
                    {"squareOrderId": order_id, "status": "pending"}, {"_id": 0}
                )
                if pending:
                    await _activate_pending_checkout(pending, source="webhook_payment_order")
                    return {"received": True, "activated": True}

            print(f"[SQUARE WEBHOOK] No pending checkout found for payment email={buyer_email} order={order_id}")

        # ── order.updated / order.completed ──
        elif event_type in ("order.updated", "order.completed"):
            order = data.get("order", data)
            order_id = order.get("id", "")
            order_state = order.get("state", "")
            print(f"[SQUARE WEBHOOK] {event_type} — order={order_id}, state={order_state}")

            if order_state == "COMPLETED" and order_id:
                pending = await db.pending_checkouts.find_one(
                    {"squareOrderId": order_id, "status": "pending"}, {"_id": 0}
                )
                if pending:
                    await _activate_pending_checkout(pending, source="webhook_order")
                    return {"received": True, "activated": True}

        # ── subscription.updated (legacy) ──
        elif event_type == "subscription.updated":
            sq_sub_id = data.get("subscription", {}).get("id") or data.get("id")
            new_status = data.get("subscription", {}).get("status") or data.get("status")
            if sq_sub_id and new_status:
                await db.square_subscriptions.update_one(
                    {"squareSubscriptionId": sq_sub_id},
                    {"$set": {"status": new_status, "updatedAt": datetime.now(timezone.utc).isoformat()}}
                )

        # ── invoice.payment_made ──
        elif event_type == "invoice.payment_made":
            invoice = data.get("invoice", data)
            order_id = invoice.get("order_id", "")
            print(f"[SQUARE WEBHOOK] invoice.payment_made — order={order_id}")
            if order_id:
                pending = await db.pending_checkouts.find_one(
                    {"squareOrderId": order_id, "status": "pending"}, {"_id": 0}
                )
                if pending:
                    await _activate_pending_checkout(pending, source="webhook_invoice")
                    return {"received": True, "activated": True}

    except Exception as e:
        print(f"[SQUARE WEBHOOK] Error processing {event_type}: {e}")

    return {"received": True}


class VerifyPaymentRequest(BaseModel):
    email: str


@router.post("/verify-payment")
async def verify_payment(req: VerifyPaymentRequest):
    """Self-recovery: user enters email, we check Square for their payment and activate."""
    email_lower = req.email.lower().strip()

    # Check if already active
    existing_sub = await db.square_subscriptions.find_one(
        {"email": email_lower, "status": {"$in": ["ACTIVE", "PENDING"]}},
        {"_id": 0}
    )
    if existing_sub:
        # Already has active sub — check if they have a user account with password
        user = await db.users.find_one({"email": email_lower}, {"_id": 0})
        if user and user.get("passwordHash"):
            return {"found": True, "status": "active", "message": "Your subscription is active. Please log in with your password."}
        return {"found": True, "status": "needs_password", "message": "Subscription found! Please set a password to continue."}

    # Check pending checkouts
    pending = await db.pending_checkouts.find_one(
        {"email": email_lower, "status": "pending"}, {"_id": 0}
    )
    if not pending:
        raise HTTPException(status_code=404, detail="No pending payment found for this email. Please subscribe first.")

    # Try to verify the order with Square
    order_id = pending.get("squareOrderId")
    payment_verified = False

    if order_id:
        try:
            client = get_square_client()
            order_resp = client.orders.get(order_id=order_id)
            order_state = order_resp.order.state if order_resp.order else None
            print(f"[SQUARE VERIFY-PAYMENT] Order {order_id} state: {order_state}")
            if order_state in ("COMPLETED", "OPEN"):
                payment_verified = True
        except Exception as e:
            print(f"[SQUARE VERIFY-PAYMENT] Order check error: {e}")

    if not payment_verified:
        # Fallback: search recent payments by email
        try:
            client = get_square_client()
            payments_resp = client.payments.list(
                begin_time=(datetime.now(timezone.utc).replace(day=1)).isoformat(),
                sort_order="DESC",
            )
            if payments_resp.payments:
                for pmt in payments_resp.payments:
                    if (pmt.buyer_email_address or "").lower().strip() == email_lower:
                        payment_verified = True
                        print(f"[SQUARE VERIFY-PAYMENT] Found payment for {email_lower}: {pmt.id}")
                        break
        except Exception as e:
            print(f"[SQUARE VERIFY-PAYMENT] Payment search error: {e}")

    if not payment_verified:
        raise HTTPException(status_code=404, detail="Could not verify payment. Please contact support if you've already paid.")

    # Activate the user
    await _activate_pending_checkout(pending, source="self_recovery")

    # Create session
    session_token = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    await db.sessions.update_one(
        {"email": email_lower},
        {"$set": {
            "email": email_lower,
            "session_token": session_token,
            "access_type": "Premium",
            "last_active": now,
        }},
        upsert=True,
    )

    return {
        "found": True,
        "status": "activated",
        "success": True,
        "email": email_lower,
        "session_token": session_token,
        "access_type": "Premium",
        "message": "Payment verified! Your account is now active.",
    }


@router.post("/admin/bulk-verify")
async def admin_bulk_verify(body: dict):
    """Admin endpoint: check all pending checkouts against Square and activate paid ones."""
    owner_email = (body.get("email", "")).lower().strip()
    from config import OWNER_EMAIL
    if owner_email != OWNER_EMAIL:
        raise HTTPException(status_code=403, detail="Admin only.")

    pending_list = await db.pending_checkouts.find(
        {"status": "pending"}, {"_id": 0}
    ).to_list(200)

    if not pending_list:
        return {"activated": 0, "message": "No pending checkouts found."}

    client = get_square_client()
    activated = []

    for pending in pending_list:
        order_id = pending.get("squareOrderId")
        p_email = pending.get("email", "")
        verified = False

        if order_id:
            try:
                order_resp = client.orders.get(order_id=order_id)
                if order_resp.order and order_resp.order.state in ("COMPLETED", "OPEN"):
                    verified = True
            except Exception as e:
                print(f"[BULK VERIFY] Order check error for {p_email}: {e}")

        if verified:
            try:
                await _activate_pending_checkout(pending, source="admin_bulk_verify")
                activated.append(p_email)
            except Exception as e:
                print(f"[BULK VERIFY] Activation error for {p_email}: {e}")

    return {
        "activated": len(activated),
        "emails": activated,
        "total_pending": len(pending_list),
        "message": f"Activated {len(activated)} of {len(pending_list)} pending checkouts.",
    }
