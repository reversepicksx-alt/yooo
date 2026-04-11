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
from routes.square import router as square_router
from routes.stripe_pay import router as stripe_router
from routes.admin import router as admin_router
from routes.miss_analysis import router as miss_router
from routes.manual_search import router as manual_router
from routes.intel import router as intel_router
from cache import seed_cache, background_refresh_loop

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
app.include_router(square_router)
app.include_router(stripe_router)
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
    # Create index for fixture stat cache (speeds up prediction pipeline)
    await db.fixture_player_cache.create_index("_k", unique=True)
    asyncio.create_task(seed_cache())
    # Build master team cache for smart opponent resolution
    from team_resolver import build_teams_cache
    asyncio.create_task(build_teams_cache())
    # Start 24h auto-refresh loop for transfers + data freshness
    asyncio.create_task(background_refresh_loop())
    asyncio.create_task(_auto_sync_square_payments())
    asyncio.create_task(_auto_sync_whop_memberships())
    asyncio.create_task(_overdue_subscription_sweep())
    # Auto-backfill positions for picks missing them (runs once at startup)
    asyncio.create_task(_auto_backfill_positions())
    # Grok Engine background tasks
    from grok_engine import auto_settlement_loop, auto_scout_loop, pattern_mining_loop
    asyncio.create_task(auto_settlement_loop())
    asyncio.create_task(auto_scout_loop())
    asyncio.create_task(pattern_mining_loop())


async def _auto_backfill_positions():
    """Auto-backfill missing positions on startup using cache + Grok AI fallback."""
    import asyncio
    await asyncio.sleep(15)  # Wait for caches to load first
    try:
        from calibration import LEAGUE_NAMES
        from config import XAI_API_KEY
        import httpx
        all_league_names = set(LEAGUE_NAMES.values())

        # Step 1: Clean invalid positions (league IDs/names, cross-sport contamination)
        from routes.intel import SOCCER_POSITIONS
        bad_picks = await db.picks.find(
            {"position": {"$exists": True, "$ne": "", "$ne": None}},
            {"_id": 0, "pickId": 1, "position": 1, "sport": 1}
        ).to_list(5000)
        cleaned = 0
        for p in bad_picks:
            pos = (p.get("position") or "").strip()
            valid_set = SOCCER_POSITIONS
            if pos.isdigit() or pos in all_league_names or pos not in valid_set:
                await db.picks.update_one(
                    {"pickId": p["pickId"]},
                    {"$set": {"position": "", "role": ""}}
                )
                cleaned += 1
        if cleaned:
            print(f"[AUTO-BACKFILL] Cleaned {cleaned} picks with invalid/cross-sport positions")

        # Step 2: Find picks still missing positions
        picks = await db.picks.find(
            {"$or": [{"position": {"$exists": False}}, {"position": ""}, {"position": None}]},
            {"_id": 0, "pickId": 1, "playerId": 1, "playerName": 1, "sport": 1}
        ).to_list(5000)

        if not picks:
            print("[AUTO-BACKFILL] No picks need position backfill")
            return

        # Step 2a: Try cache first
        unresolved = []
        updated = 0
        for p in picks:
            pid = p.get("playerId")
            pname = p.get("playerName", "")
            pos_found, role_found = "", ""

            if pid:
                cached = await db.player_positions.find_one(
                    {"playerId": pid}, {"_id": 0, "specificPosition": 1, "role": 1}
                )
                if cached and cached.get("specificPosition"):
                    pos_found = cached["specificPosition"]
                    role_found = cached.get("role", "")

            if not pos_found and pid:
                pred = await db.predictions.find_one(
                    {"player.id": pid, "player.position": {"$nin": ["Unknown", "", None]}},
                    {"_id": 0, "player.position": 1, "player.role": 1}
                )
                if pred:
                    pos_found = pred.get("player", {}).get("position", "")
                    role_found = pred.get("player", {}).get("role", "")

            if pos_found:
                await db.picks.update_many(
                    {"playerId": pid, "$or": [{"position": {"$exists": False}}, {"position": ""}, {"position": None}]},
                    {"$set": {"position": pos_found, "role": role_found or ""}}
                )
                updated += 1
            else:
                unresolved.append({"pickId": p["pickId"], "playerId": pid, "playerName": pname, "sport": p.get("sport", "soccer")})

        print(f"[AUTO-BACKFILL] Cache resolved: {updated}/{len(picks)}. Unresolved: {len(unresolved)}")

        # Step 3: Use Grok to batch-resolve remaining positions
        if unresolved and XAI_API_KEY:
            # Deduplicate by player name+sport
            unique_players = {}
            for u in unresolved:
                key = f"{u['playerName']}|{u['sport']}"
                if key not in unique_players:
                    unique_players[key] = u

            # Batch into chunks of 30
            player_list = list(unique_players.values())
            for i in range(0, len(player_list), 30):
                batch = player_list[i:i+30]
                player_lines = []
                for idx, pl in enumerate(batch):
                    player_lines.append(f"{idx+1}. {pl['playerName']} ({pl['sport']})")

                prompt = f"""For each player below, return ONLY their primary position abbreviation.

Soccer positions: GK, CB, LB, RB, LWB, RWB, CDM, CM, CAM, LM, RM, LW, RW, CF, ST

Also return a short role description (e.g., "Inverted Winger", "Deep-Lying Playmaker", "Box-to-Box").

Players:
{chr(10).join(player_lines)}

Return JSON array: [{{"name":"...","position":"XX","role":"..."}}]
Only the JSON array, no markdown."""

                try:
                    async with httpx.AsyncClient(timeout=20) as client:
                        resp = await client.post(
                            "https://api.x.ai/v1/chat/completions",
                            headers={"Authorization": f"Bearer {XAI_API_KEY}", "Content-Type": "application/json"},
                            json={
                                "model": "grok-4-1-fast-non-reasoning",
                                "messages": [{"role": "user", "content": prompt}],
                                "temperature": 0,
                            }
                        )
                        if resp.status_code == 200:
                            import json
                            content = resp.json()["choices"][0]["message"]["content"]
                            # Clean markdown wrapping if present
                            content = content.strip()
                            if content.startswith("```"):
                                content = content.split("\n", 1)[1] if "\n" in content else content[3:]
                                content = content.rsplit("```", 1)[0]
                            resolved = json.loads(content.strip())
                            grok_updated = 0
                            for r in resolved:
                                rname = r.get("name", "")
                                rpos = r.get("position", "")
                                rrole = r.get("role", "")
                                if rname and rpos:
                                    # Update all picks for this player
                                    await db.picks.update_many(
                                        {"playerName": rname, "$or": [{"position": {"$exists": False}}, {"position": ""}, {"position": None}]},
                                        {"$set": {"position": rpos, "role": rrole or ""}}
                                    )
                                    # Cache for future lookups
                                    matching = [u for u in batch if u["playerName"] == rname]
                                    for m in matching:
                                        if m.get("playerId"):
                                            await db.player_positions.update_one(
                                                {"playerId": m["playerId"]},
                                                {"$set": {"playerId": m["playerId"], "specificPosition": rpos, "role": rrole or ""}},
                                                upsert=True
                                            )
                                    grok_updated += 1
                            print(f"[AUTO-BACKFILL] Grok resolved: {grok_updated} players (batch {i//30+1})")
                        else:
                            print(f"[AUTO-BACKFILL] Grok API error: {resp.status_code}")
                except Exception as e:
                    print(f"[AUTO-BACKFILL] Grok batch error: {e}")

        print(f"[AUTO-BACKFILL] Done. Total cache-resolved: {updated}, Grok batches sent: {(len(unresolved)+29)//30 if unresolved else 0}")
    except Exception as e:
        print(f"[AUTO-BACKFILL] Error: {e}")



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

            # Calculate expiration
            # CANCELED subs: expire on the cancel date (revoke access immediately, not at charged_through)
            # ACTIVE subs: expire at charged_through_date (next billing date)
            expires_at = ""
            if sq_status == "CANCELED":
                # Revoke access on cancel date — NEVER give future access for a canceled sub
                from datetime import date as _date
                today_str = _date.today().isoformat()
                cancel_date = str(getattr(sub, 'canceled_date', '') or '').strip()[:10]
                charged_str = str(sub.charged_through_date or '').strip()[:10] if sub.charged_through_date else ''

                # Use the EARLIEST of: cancel_date, charged_through_date, today
                candidates = [d for d in [cancel_date, charged_str, today_str] if d]
                expires_day = min(candidates) if candidates else today_str
                expires_at = expires_day + "T23:59:59+00:00"
                print(f"[SQUARE SYNC] CANCELED sub {email}: access expires {expires_at} (cancel={cancel_date or 'N/A'}, charged={charged_str or 'N/A'})")
            elif sub.charged_through_date:
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


async def _auto_sync_whop_memberships():
    """On startup, sync all active Whop memberships to local DB for fast email lookups."""
    import asyncio, httpx, os
    await asyncio.sleep(8)
    try:
        api_key = os.environ.get("WHOP_API_KEY", "")
        if not api_key:
            print("[WHOP SYNC] No WHOP_API_KEY, skipping")
            return

        from datetime import datetime, timezone
        synced = 0
        page = 1

        async with httpx.AsyncClient(timeout=15.0) as client:
            while page <= 20:
                resp = await client.get(
                    "https://api.whop.com/api/v2/memberships",
                    params={"valid": "true", "per_page": 50, "page": page},
                    headers={"Authorization": f"Bearer {api_key}"},
                )
                if resp.status_code != 200:
                    print(f"[WHOP SYNC] API error {resp.status_code}: {resp.text[:100]}")
                    break

                data = resp.json()
                memberships = data.get("data", [])
                if not memberships:
                    break

                for m in memberships:
                    email = (m.get("email") or "").lower().strip()
                    if not email:
                        continue
                    is_active = m.get("valid") or m.get("status") == "active"
                    if not is_active:
                        continue
                    await db.whop_subscriptions.update_one(
                        {"email": email},
                        {"$set": {
                            "email": email,
                            "status": "active",
                            "whop_id": m.get("id"),
                            "plan": m.get("plan"),
                            "updatedAt": datetime.now(timezone.utc).isoformat(),
                        }},
                        upsert=True,
                    )
                    synced += 1

                pagination = data.get("pagination", {})
                if page >= pagination.get("total_page", 1):
                    break
                page += 1

        print(f"[WHOP SYNC] Synced {synced} active memberships to local DB")

    except Exception as e:
        print(f"[WHOP SYNC] Error: {e}")


async def _overdue_subscription_sweep():
    """Periodically check all ACTIVE Square subscriptions for overdue payments.
    If charged_through_date has passed, auto-cancel and expire them."""
    import asyncio
    await asyncio.sleep(15)
    while True:
        try:
            from routes.square import get_square_client
            from datetime import date as date_type, datetime, timezone
            import os

            client = get_square_client()
            location_id = os.environ.get("SQUARE_LOCATION_ID")
            if not client or not location_id:
                await asyncio.sleep(3600)
                continue

            result = client.subscriptions.search(
                query={"filter": {"location_ids": [location_id]}}
            )
            sq_subs = result.subscriptions or []

            today = date_type.today()
            canceled_count = 0

            for sub in sq_subs:
                if sub.status not in ("ACTIVE", "PENDING"):
                    continue

                charged_through = getattr(sub, 'charged_through_date', None)
                if not charged_through:
                    continue

                try:
                    ct_date = date_type.fromisoformat(str(charged_through)[:10])
                except Exception:
                    continue

                if ct_date > today:
                    continue

                email = ""
                try:
                    cust = client.customers.get(customer_id=sub.customer_id)
                    email = (cust.customer.email_address or "").lower().strip()
                except Exception:
                    pass

                days_overdue = (today - ct_date).days
                print(f"[OVERDUE SWEEP] {email or sub.id}: overdue by {days_overdue} day(s), charged_through={ct_date}")

                try:
                    client.subscriptions.cancel(subscription_id=sub.id)
                    print(f"[OVERDUE SWEEP] Auto-canceled {email or sub.id}")
                except Exception as ce:
                    err_msg = str(ce).lower()
                    if "pending cancel" not in err_msg and "already" not in err_msg:
                        print(f"[OVERDUE SWEEP] Cancel failed for {email or sub.id}: {ce}")
                        continue

                if email:
                    await db.square_subscriptions.update_one(
                        {"email": email},
                        {"$set": {
                            "status": "EXPIRED",
                            "expiredReason": "payment_overdue",
                            "updatedAt": datetime.now(timezone.utc).isoformat(),
                        }}
                    )
                    await db.sessions.delete_many({"email": email})

                canceled_count += 1

            if canceled_count > 0:
                print(f"[OVERDUE SWEEP] Canceled {canceled_count} overdue subscription(s)")

        except Exception as e:
            print(f"[OVERDUE SWEEP] Error: {e}")

        await asyncio.sleep(900)


# ── Legacy alias: /api/search-player ──
@app.get("/api/search-player")
async def search_player_alias(query: str = ""):
    """Legacy compatibility endpoint — redirects to /api/players/search."""
    from routes.players import search_players
    from models import PlayerSearchRequest
    return await search_players(PlayerSearchRequest(query=query))
