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
        "status": sub.get("status"),
        "cardLast4": sub.get("cardLast4"),
        "cardBrand": sub.get("cardBrand"),
        "subscribedAt": sub.get("subscribedAt"),
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
