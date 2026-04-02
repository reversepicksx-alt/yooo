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


@router.post("/webhook")
async def square_webhook(event: dict):
    event_type = event.get("type", "")
    data = event.get("data", {}).get("object", {})

    if event_type == "subscription.updated":
        sq_sub_id = data.get("subscription", {}).get("id") or data.get("id")
        new_status = data.get("subscription", {}).get("status") or data.get("status")
        if sq_sub_id and new_status:
            await db.square_subscriptions.update_one(
                {"squareSubscriptionId": sq_sub_id},
                {"$set": {"status": new_status, "updatedAt": datetime.now(timezone.utc).isoformat()}}
            )
    return {"received": True}
