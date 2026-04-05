import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from config import db, LIFETIME_SUB_EMAILS, OWNER_EMAIL, init_dynamic_settings

# ── Create App ──
app = FastAPI(title="ReversePicks API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Import and include routers ──
from routes.auth import router as auth_router
from routes.leagues import router as leagues_router
from routes.players import router as players_router
from routes.predict import router as predict_router
from routes.combo import router as combo_router
from routes.scan import router as scan_router
from routes.picks import router as picks_router
from routes.chat import router as chat_router
from routes.misc import router as misc_router
from routes.tactical import router as tactical_router
from routes.basketball_predict import router as basketball_router
from routes.square import router as square_router
from routes.admin import router as admin_router
from routes.miss_analysis import router as miss_router
from routes.manual_search import router as manual_router
from routes.intel import router as intel_router
from cache import seed_cache, background_refresh_loop
from basketball_cache import seed_bball_cache, bball_background_refresh, get_bball_cache_status

app.include_router(auth_router)
app.include_router(leagues_router)
app.include_router(players_router)
app.include_router(predict_router)
app.include_router(combo_router)
app.include_router(scan_router)
app.include_router(picks_router)
app.include_router(chat_router)
app.include_router(misc_router)
app.include_router(tactical_router)
app.include_router(basketball_router)
app.include_router(square_router)
app.include_router(admin_router)
app.include_router(miss_router)
app.include_router(manual_router)
app.include_router(intel_router)


# ── Startup: seed grants for lifetime VIPs ──
@app.on_event("startup")
async def seed_grants():
    # Load dynamic settings (API keys from MongoDB) before anything else
    await init_dynamic_settings()
    for email in LIFETIME_SUB_EMAILS:
        await db.manual_access_grants.update_one(
            {"email": email},
            {"$set": {"email": email, "access_type": "Lifetime"}},
            upsert=True
        )
    await db.manual_access_grants.update_one(
        {"email": OWNER_EMAIL},
        {"$set": {"email": OWNER_EMAIL, "access_type": "Owner"}},
        upsert=True
    )
    # Seed the API-Football lookup cache (non-blocking)
    import asyncio
    asyncio.create_task(seed_cache())
    # Build master team cache for smart opponent resolution
    from team_resolver import build_teams_cache
    asyncio.create_task(build_teams_cache())
    # Start 24h auto-refresh loop for transfers + data freshness
    asyncio.create_task(background_refresh_loop())
    # Seed basketball (NBA + WNBA) cache
    asyncio.create_task(seed_bball_cache())
    asyncio.create_task(bball_background_refresh())
    # Auto-sync Square payments → subscriptions (non-blocking)
    asyncio.create_task(_auto_sync_square_payments())


async def _auto_sync_square_payments():
    """On startup, sync Square subscriptions to local DB for webhook matching."""
    import asyncio
    await asyncio.sleep(5)
    try:
        from routes.square import get_square_client, PLANS
        from datetime import timedelta, timezone, datetime
        import os

        client = get_square_client()
        if not client:
            print("[SQUARE SYNC] No Square client available, skipping")
            return

        location_id = os.environ.get("SQUARE_LOCATION_ID")
        if not location_id:
            print("[SQUARE SYNC] No SQUARE_LOCATION_ID, skipping")
            return

        # Fetch all subscriptions from Square
        result = client.subscriptions.search(
            query={"filter": {"location_ids": [location_id]}}
        )
        sq_subs = result.subscriptions or []
        if not sq_subs:
            print("[SQUARE SYNC] No subscriptions found in Square")
            return

        print(f"[SQUARE SYNC] Found {len(sq_subs)} subscriptions in Square")

        # Sort: ACTIVE subscriptions first so they take priority over CANCELLED
        sq_subs.sort(key=lambda s: 0 if s.status == "ACTIVE" else 1)

        # Track which emails already have ACTIVE subs to prevent overwrite
        active_emails = set()

        synced = 0
        for sub in sq_subs:
            # Resolve customer email
            email = ""
            try:
                cust = client.customers.get(customer_id=sub.customer_id)
                email = (cust.customer.email_address or "").lower().strip()
            except Exception:
                continue

            if not email:
                continue

            sq_status = sub.status  # ACTIVE, CANCELED, PAUSED, etc.

            # Skip if we already synced an ACTIVE sub for this email
            if email in active_emails and sq_status != "ACTIVE":
                continue

            our_status = "ACTIVE" if sq_status == "ACTIVE" else "EXPIRED"
            start_date = str(sub.start_date)[:10] if sub.start_date else ""

            # Determine plan from subscription variation_id
            plan_key = "weekly"  # default
            if sub.plan_variation_id:
                # Match variation_id to our stored plans
                plan_doc = await db.square_plans.find_one(
                    {"variation_id": sub.plan_variation_id}, {"_id": 0, "key": 1}
                )
                if plan_doc and plan_doc.get("key"):
                    plan_key = plan_doc["key"]
                else:
                    # Fallback: use existing DB value or infer from cadence
                    existing = await db.square_subscriptions.find_one(
                        {"email": email}, {"_id": 0, "planKey": 1}
                    )
                    if existing and existing.get("planKey"):
                        plan_key = existing["planKey"]
                    else:
                        try:
                            if sub.charged_through_date and sub.start_date:
                                start = datetime.fromisoformat(str(sub.start_date))
                                charged = datetime.fromisoformat(str(sub.charged_through_date))
                                diff = (charged - start).days
                                if diff <= 10:
                                    plan_key = "weekly"
                                elif diff <= 35:
                                    plan_key = "monthly"
                                else:
                                    plan_key = "quarterly"
                        except Exception:
                            pass

            plan_info = PLANS.get(plan_key, {})

            # Calculate expiration from charged_through_date (Square's next billing date)
            expires_at = ""
            if sub.charged_through_date:
                expires_at = str(sub.charged_through_date) + "T23:59:59+00:00"
            elif start_date:
                cadence_days = {"weekly": 7, "monthly": 30, "quarterly": 90}
                try:
                    start_dt = datetime.fromisoformat(start_date)
                    expires_at = (start_dt + timedelta(days=cadence_days.get(plan_key, 30))).isoformat()
                except Exception:
                    pass

            now = datetime.now(timezone.utc).isoformat()

            await db.square_subscriptions.update_one(
                {"email": email},
                {"$set": {
                    "email": email,
                    "squareSubscriptionId": sub.id,
                    "squareCustomerId": sub.customer_id,
                    "planKey": plan_key,
                    "planName": plan_info.get("name", plan_key),
                    "status": our_status,
                    "subscribedAt": start_date,
                    "expiresAt": expires_at,
                    "updatedAt": now,
                    "source": "square_sync",
                }},
                upsert=True,
            )
            if sq_status == "ACTIVE":
                active_emails.add(email)
            synced += 1

        print(f"[SQUARE SYNC] Synced {synced} subscriptions (IDs stored for webhook matching)")

    except Exception as e:
        print(f"[SQUARE SYNC] Error: {e}")


# ── Legacy alias: /api/search-player ──
@app.get("/api/search-player")
async def search_player_alias(query: str = ""):
    """Legacy compatibility endpoint — redirects to /api/players/search."""
    from routes.players import search_players
    from models import PlayerSearchRequest
    return await search_players(PlayerSearchRequest(query=query))
