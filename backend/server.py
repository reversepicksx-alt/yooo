import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from config import db, LIFETIME_SUB_EMAILS, OWNER_EMAIL, COMPLIMENTARY_MEMBERS, init_dynamic_settings, get_dynamic_setting

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
from routes.community import router as community_router
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
from routes.search import router as search_router
from routes.support import router as support_router
from routes.push import router as push_router
from routes.mlb_routes import router as mlb_router
from routes.cs2_routes import router as cs2_router
from cache import seed_cache, background_refresh_loop

app.include_router(auth_router)
app.include_router(community_router)
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
app.include_router(search_router)
app.include_router(support_router)
app.include_router(push_router)
app.include_router(mlb_router)
app.include_router(cs2_router)


# ── Startup: seed grants for lifetime VIPs ──
@app.on_event("startup")
async def seed_grants():
    # Load dynamic settings (API keys from MongoDB) before anything else
    await init_dynamic_settings()
    try:
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
        for email, expiry_date in COMPLIMENTARY_MEMBERS.items():
            await db.manual_access_grants.update_one(
                {"email": email},
                {"$set": {"email": email, "access_type": "Complimentary", "expiresAt": expiry_date}},
                upsert=True
            )
    except Exception as _grant_err:
        import logging
        logging.getLogger("server").warning(
            f"seed_grants skipped (Atlas quota or transient error): {_grant_err}"
        )
    # Seed the API-Football lookup cache (non-blocking)
    import asyncio
    # Create index for fixture stat cache (speeds up prediction pipeline)
    try:
        await db.fixture_player_cache.create_index("_k", unique=True)
    except Exception as _idx_err:
        import logging
        logging.getLogger("server").warning(f"create_index skipped (Atlas transient): {_idx_err}")
    # Install TTL indexes on all cache collections to keep Atlas storage under control
    try:
        from ttl_indexes import setup_ttl_indexes
        await setup_ttl_indexes(db)
    except Exception as _ttl_err:
        import logging
        logging.getLogger("server").warning(f"TTL index setup skipped: {_ttl_err}")
    asyncio.create_task(seed_cache())
    # Build master team cache for smart opponent resolution
    # force=True ensures Portugal/Turkey + leaguePriority field are included
    from team_resolver import build_teams_cache
    # force=False so we use cached teams if recent (saves ~26 API calls per startup)
    asyncio.create_task(build_teams_cache(force=False))
    # Start 24h auto-refresh loop for transfers + data freshness
    asyncio.create_task(background_refresh_loop())
    asyncio.create_task(_auto_sync_square_payments())
    asyncio.create_task(_auto_sync_whop_memberships())
    asyncio.create_task(_overdue_subscription_sweep())
    # Auto-backfill positions for picks missing them (runs once at startup)
    asyncio.create_task(_auto_backfill_positions())
    # Fix MLB picks saved with sport='soccer' before the sport-detection fix
    asyncio.create_task(_backfill_mlb_sport())
    # Grok Engine background tasks
    from grok_engine import auto_settlement_loop, auto_scout_loop, pattern_mining_loop, mlb_live_loop
    asyncio.create_task(auto_settlement_loop())
    asyncio.create_task(auto_scout_loop())
    asyncio.create_task(pattern_mining_loop())
    asyncio.create_task(mlb_live_loop())
    # League-aware empirical calibration: load on startup, refresh every 6h
    from league_priors import ensure_loaded as ensure_league_priors_loaded
    asyncio.create_task(ensure_league_priors_loaded(db))

    # Confidence calibration: refresh on startup + every 6h. Maps the engine's
    # raw confidence to empirical hit rate. Until any (propType, bucket) reaches
    # n>=30 settled picks the calibrator passes raw values through untouched.
    async def _conf_calib_loop():
        from confidence_calibration import refresh_calibration
        import asyncio as _a
        while True:
            try:
                summary = await refresh_calibration(db)
                print(f"[CONF CALIB] refreshed: props={summary['props']} buckets={summary['totalBuckets']} (min n={summary['minBucketN']})")
            except Exception as _e:
                print(f"[CONF CALIB] refresh failed: {_e}")
            await _a.sleep(6 * 60 * 60)
    asyncio.create_task(_conf_calib_loop())

    # Prop safety cache: refresh on startup + every 6h. Computes empirical
    # hit rates per (propType, direction) bucket from settled picks so the
    # edge/safety rating on every prediction is always data-driven, never hardcoded.
    async def _prop_safety_loop():
        from prop_safety_cache import refresh_prop_safety
        import asyncio as _a
        while True:
            try:
                await refresh_prop_safety(db)
            except Exception as _e:
                print(f"[PROP SAFETY] refresh failed: {_e}")
            await _a.sleep(6 * 60 * 60)
    asyncio.create_task(_prop_safety_loop())

    # Self-updating cheat sheet — re-renders attached_assets/cheat_sheet_2_1.png
    # from settled picks every few hours so it never goes stale.
    asyncio.create_task(_cheat_sheet_loop())

    # Bulk player-stats prefetch — caches all players from recent fixtures so
    # predictions never hit "no data". Runs on startup then every 24h.
    from data_prefetch import data_prefetch_loop, backfill_fixture_metadata
    asyncio.create_task(data_prefetch_loop())
    # Backfill home/away metadata for any uncovered fixtures (no-op if already done)
    asyncio.create_task(backfill_fixture_metadata(max_fixtures=500))

    # Atlas storage guard: purge stale cached data every 6 hours so the free-tier
    # 512 MB cap is never hit. Predictions are regenerated on demand (7-day TTL).
    # team_fixture_history rows are re-fetched on next predict run.
    asyncio.create_task(_atlas_storage_cleanup_loop())

    # Nightly calibration loop DISABLED — raw Bayesian projections proved more
    # accurate than the learned-offset corrections. Keep import available for
    # admin endpoints but don't auto-run.
    # from calibration import nightly_calibration_loop
    # asyncio.create_task(nightly_calibration_loop())


async def _atlas_storage_cleanup_loop():
    """Prevent Atlas free-tier 512 MB cap from being hit.
    Runs every 6 hours. Deletes predictions older than 7 days (they are
    regenerated on demand) and caps team_fixture_history to 2000 most-recent
    rows (re-fetched live when needed). Logs how much was removed each pass."""
    import asyncio
    from datetime import datetime, timezone, timedelta

    # Create a TTL index on predictions._ts on first run (idempotent)
    try:
        await db.predictions.create_index(
            "_ts",
            expireAfterSeconds=7 * 24 * 3600,  # 7-day TTL
            background=True,
        )
    except Exception:
        pass

    while True:
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(days=7)
            # Delete predictions older than 7 days
            r1 = await db.predictions.delete_many({"_ts": {"$lt": cutoff}})
            # Also delete predictions with no _ts field but older ObjectId (legacy rows)
            # ObjectId embeds creation time — docs older than 7 days have generation
            # time before cutoff.
            from bson import ObjectId
            import struct, time as _time
            _cutoff_ts = int(cutoff.timestamp())
            _old_id = ObjectId(struct.pack(">I", _cutoff_ts) + b"\x00" * 8)
            r2 = await db.predictions.delete_many({"_id": {"$lt": _old_id}, "_ts": {"$exists": False}})
            total_pred = r1.deleted_count + r2.deleted_count

            # Cap team_fixture_history — keep newest 2000 rows only
            th_count = await db.team_fixture_history.count_documents({})
            th_deleted = 0
            if th_count > 2000:
                # Find the _id of the 2000th newest doc and delete everything older
                cursor = db.team_fixture_history.find({}, {"_id": 1}).sort("_id", -1).skip(2000).limit(1)
                pivot = await cursor.to_list(1)
                if pivot:
                    rd = await db.team_fixture_history.delete_many({"_id": {"$lte": pivot[0]["_id"]}})
                    th_deleted = rd.deleted_count

            print(f"[ATLAS CLEANUP] predictions pruned={total_pred} | "
                  f"team_fixture_history pruned={th_deleted} (was {th_count})")
        except Exception as _e:
            print(f"[ATLAS CLEANUP] error: {_e}")
        await asyncio.sleep(6 * 3600)


async def _cheat_sheet_loop():
    """Periodically re-render attached_assets/cheat_sheet_2_1.png from settled
    picks so the marketing/intel asset stays in sync with the live data without
    manual `python scripts/build_cheat_sheet.py` runs."""
    import asyncio
    # Wait a bit so seed_cache / settle loops have a chance to populate first.
    await asyncio.sleep(60)
    INTERVAL_SECS = 6 * 3600  # every 6 hours, matches scenario_priors refresh cadence
    while True:
        try:
            from scripts.build_cheat_sheet import render_cheat_sheet
            result = await render_cheat_sheet(db=db)
            print(f"[CHEAT SHEET] Re-rendered: {result.get('total_picks', 0)} picks "
                  f"→ {result.get('path')}")
        except Exception as e:
            print(f"[CHEAT SHEET] Render failed: {e}")
        await asyncio.sleep(INTERVAL_SECS)


async def _backfill_mlb_sport():
    """
    One-time startup fix: set sport='mlb' on any picks that have an MLB prop type
    but were saved with sport='soccer' (the bug that existed before the sport-detection fix).
    Safe to run repeatedly — only touches picks that need correction.
    """
    import asyncio
    await asyncio.sleep(20)  # Let caches settle first
    try:
        _MLB_PROP_TYPES = [
            "pitcher_strikeouts", "innings_pitched", "hits_allowed", "earned_runs",
            "walks_allowed", "pitches_thrown", "batters_faced",
            "hits", "home_runs", "rbi", "walks", "strikeouts", "runs",
            "total_bases", "stolen_bases", "doubles", "plate_appearances",
        ]
        result = await db.picks.update_many(
            {"propType": {"$in": _MLB_PROP_TYPES}, "sport": {"$ne": "mlb"}},
            {"$set": {"sport": "mlb"}},
        )
        if result.modified_count:
            print(f"[MLB BACKFILL] Fixed sport field on {result.modified_count} MLB picks (were tagged as soccer)")
        else:
            print("[MLB BACKFILL] No picks needed sport correction")
    except Exception as _e:
        print(f"[MLB BACKFILL] Error: {_e}")


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

        # If Square billing is disabled, cancel all active Square subscriptions so Square
        # stops independently retrying charges against customers.
        disabled = (get_dynamic_setting("DISABLE_SQUARE_BILLING") or "").lower() in ("1", "true", "yes", "on")
        if disabled:
            print("[SQUARE SYNC] Square billing is DISABLED — canceling all active Square subscriptions to stop recurring charges")
            try:
                _client = get_square_client()
                _loc = os.environ.get("SQUARE_LOCATION_ID")
                if _client and _loc:
                    _result = _client.subscriptions.search(
                        query={"filter": {"location_ids": [_loc]}}
                    )
                    _all_subs = (_result.subscriptions or []) if _result else []
                    _billable = [s for s in _all_subs if s.status in ("ACTIVE", "PENDING")]
                    _canceled = 0
                    for _sub in _billable:
                        try:
                            _client.subscriptions.cancel(subscription_id=_sub.id)
                            _canceled += 1
                            print(f"[SQUARE SYNC] Canceled {_sub.status} sub {_sub.id} (customer {_sub.customer_id})")
                        except Exception as _ce:
                            _cerr = str(_ce).lower()
                            if "already" not in _cerr and "cancel" not in _cerr:
                                print(f"[SQUARE SYNC] Could not cancel {_sub.id}: {_ce}")
                    print(f"[SQUARE SYNC] Subscriptions — canceled {_canceled} of {len(_billable)} billable")
                    # Also void outstanding invoices so Square stops retrying failed charges.
                    # Only fetch first page (up to 200) to avoid hanging on large accounts.
                    try:
                        _inv_resp = _client.invoices.list(location_id=_loc)
                        _first_page = next(iter(_inv_resp.pages()), None)
                        _invoices = _first_page.items if _first_page else []
                        _voided = 0
                        for _inv in _invoices:
                            _inv_status = getattr(_inv, "status", "")
                            if _inv_status in ("UNPAID", "PARTIALLY_PAID", "SCHEDULED"):
                                try:
                                    _client.invoices.cancel(invoice_id=_inv.id, version=_inv.version or 0)
                                    _voided += 1
                                    print(f"[SQUARE SYNC] Voided invoice {_inv.id} ({_inv_status})")
                                except Exception as _ie:
                                    print(f"[SQUARE SYNC] Could not void invoice {_inv.id}: {_ie}")
                        if _invoices:
                            print(f"[SQUARE SYNC] Invoices — voided {_voided} of {len(_invoices)} checked")
                        else:
                            print("[SQUARE SYNC] Invoices — none found on first page")
                    except Exception as _ie2:
                        print(f"[SQUARE SYNC] Invoice check skipped: {_ie2}")
            except Exception as _e:
                print(f"[SQUARE SYNC] Error canceling active subs: {_e}")
            return

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
        # Detect duplicate ACTIVE subs per customer and cancel extras immediately
        from collections import defaultdict as _dd
        active_by_customer: dict = _dd(list)
        for _s in sq_subs:
            if _s.status == "ACTIVE":
                active_by_customer[_s.customer_id].append(_s.id)
        for _cust_id, _sub_ids in active_by_customer.items():
            if len(_sub_ids) > 1:
                try:
                    _cust = client.customers.get(customer_id=_cust_id)
                    _email = (_cust.customer.email_address or "").lower().strip()
                except Exception:
                    _email = _cust_id
                # Keep first (oldest), cancel the rest
                for _dup_id in _sub_ids[1:]:
                    try:
                        client.subscriptions.cancel(subscription_id=_dup_id)
                        print(f"[SQUARE SYNC] Duplicate ACTIVE sub canceled for {_email}: {_dup_id}")
                    except Exception as _ce:
                        _cerr = str(_ce).lower()
                        if "pending" not in _cerr and "already" not in _cerr:
                            print(f"[SQUARE SYNC] Could not cancel duplicate {_dup_id} for {_email}: {_ce}")

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

            # Skip if we already synced a sub for this email (ACTIVE takes priority,
            # duplicates of any status are skipped — first sub processed wins)
            if email in active_emails:
                continue

            # Never override a manually-blocked user, even if Square shows ACTIVE
            existing_rec = await db.square_subscriptions.find_one({"email": email}, {"_id": 0, "manuallyBlocked": 1})
            if existing_rec and existing_rec.get("manuallyBlocked"):
                print(f"[SQUARE SYNC] Skipping {email} — manually blocked")
                active_emails.add(email)  # Prevent further processing
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
    """
    Honour existing Whop subscribers until their plan expires — no new Whop members added.

    NEW-SIGNUP FREEZE: upsert=False means we ONLY update records already in our DB.
    Any email not already present is ignored, so new Whop signups never gain access.
    Members who cancel/expire on Whop get marked 'expired' here automatically.
    All new subscribers must go through Stripe.
    """
    import asyncio, httpx, os
    await asyncio.sleep(8)
    try:
        api_key = os.environ.get("WHOP_API_KEY", "")
        if not api_key:
            print("[WHOP SYNC] No WHOP_API_KEY, skipping")
            return

        from datetime import datetime, timezone
        still_active_emails: set = set()
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
                    still_active_emails.add(email)
                    # upsert=False: only update EXISTING records — never create new Whop members
                    await db.whop_subscriptions.update_one(
                        {"email": email},
                        {"$set": {
                            "status": "active",
                            "whop_id": m.get("id"),
                            "plan": m.get("plan"),
                            "updatedAt": datetime.now(timezone.utc).isoformat(),
                        }},
                        upsert=False,
                    )

                pagination = data.get("pagination", {})
                if page >= pagination.get("total_page", 1):
                    break
                page += 1

        # Expire any existing Whop records whose plans have since cancelled/lapsed on Whop
        if still_active_emails:
            expired = await db.whop_subscriptions.update_many(
                {"email": {"$nin": list(still_active_emails)}, "status": "active"},
                {"$set": {"status": "expired", "updatedAt": datetime.now(timezone.utc).isoformat()}},
            )
            if expired.modified_count:
                print(f"[WHOP SYNC] Expired {expired.modified_count} lapsed Whop sub(s)")

        print(f"[WHOP SYNC] Honoured {len(still_active_emails)} existing Whop subs. New signups frozen — Stripe only.")

    except Exception as e:
        print(f"[WHOP SYNC] Error: {e}")


async def _overdue_subscription_sweep():
    """Periodically expire Square subscribers whose paid period has ended.
    When Square billing is disabled, uses local DB expiresAt only (no Square API calls).
    When enabled, also checks Square directly for overdue charged_through dates."""
    import asyncio
    await asyncio.sleep(15)
    while True:
        try:
            from datetime import date as date_type, datetime, timezone

            now = datetime.now(timezone.utc)
            now_iso = now.isoformat()
            canceled_count = 0

            billing_disabled = (get_dynamic_setting("DISABLE_SQUARE_BILLING") or "").lower() in ("1", "true", "yes", "on")

            if billing_disabled:
                # ── DB-only sweep: expire anyone whose accessHonoredThrough has passed ──
                # Includes CANCELED subs — they must not get perpetual free access
                active_subs = await db.square_subscriptions.find(
                    {"status": {"$in": ["ACTIVE", "PENDING", "CANCELED"]}}, {"_id": 0, "email": 1, "expiresAt": 1, "accessHonoredThrough": 1}
                ).to_list(200)

                for sub in active_subs:
                    email = sub.get("email", "")
                    # Use accessHonoredThrough if set, else expiresAt
                    through_raw = sub.get("accessHonoredThrough") or sub.get("expiresAt", "")
                    if not through_raw:
                        continue
                    try:
                        through_date = date_type.fromisoformat(str(through_raw)[:10])
                    except Exception:
                        continue
                    if through_date > date_type.today():
                        continue  # Still has valid time

                    days_overdue = (date_type.today() - through_date).days
                    print(f"[OVERDUE SWEEP] {email}: honored period ended {through_date} ({days_overdue}d ago) — expiring")
                    await db.square_subscriptions.update_one(
                        {"email": email},
                        {"$set": {
                            "status": "EXPIRED",
                            "expiredReason": "honored_period_ended",
                            "updatedAt": now_iso,
                        }}
                    )
                    await db.sessions.delete_many({"email": email})
                    canceled_count += 1

            else:
                # ── Square API sweep (original behaviour when billing is active) ──
                import os
                from routes.square import get_square_client

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
                                "updatedAt": now_iso,
                            }}
                        )
                        await db.sessions.delete_many({"email": email})

                    canceled_count += 1

            if canceled_count > 0:
                print(f"[OVERDUE SWEEP] Expired {canceled_count} subscription(s)")

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


# ── Calibration status + manual trigger ──────────────────────────────────────
@app.get("/api/calibration/status")
async def calibration_status(sport: str = "soccer"):
    """Return the last nightly calibration run summary and all stored offsets."""
    from config import db
    run = await db.calibration_runs.find_one({"sport": sport}, {"_id": 0})
    offsets = await db.calibration_offsets.find(
        {"sport": sport}, {"_id": 0}
    ).sort("sampleCount", -1).to_list(200)
    return {
        "lastRun": run or {},
        "offsets": offsets,
        "offsetCount": len(offsets),
    }


@app.post("/api/calibration/run")
async def trigger_calibration(sport: str = "soccer"):
    """Manually trigger a calibration run (owner use only)."""
    from calibration import run_nightly_calibration
    result = await run_nightly_calibration(sport)
    return result


@app.get("/api/admin/analytics")
async def owner_analytics():
    """Owner-only: return bot performance breakdown by direction, venue, position, prop type."""
    from config import db
    from collections import defaultdict

    match_filter = {"status": "settled", "result": {"$in": ["hit", "miss"]}}

    def pct(h, t):
        return round(h / t * 100, 1) if t > 0 else 0.0

    async def group_by(field: str):
        pipeline = [
            {"$match": match_filter},
            {"$group": {"_id": {"key": f"${field}", "result": "$result"}, "count": {"$sum": 1}}},
        ]
        rows = await db.picks.aggregate(pipeline).to_list(200)
        buckets: dict = defaultdict(lambda: {"hit": 0, "miss": 0})
        for r in rows:
            key = r["_id"].get("key") or "Unknown"
            buckets[key][r["_id"]["result"]] += r["count"]
        out = []
        for k, v in buckets.items():
            t = v["hit"] + v["miss"]
            out.append({"label": k, "hits": v["hit"], "misses": v["miss"],
                        "total": t, "winPct": pct(v["hit"], t)})
        return sorted(out, key=lambda x: -x["winPct"])

    total_docs = await db.picks.count_documents(match_filter)
    total_hits = await db.picks.count_documents({"status": "settled", "result": "hit"})

    # Streak: last N settled picks in chronological order
    recent_raw = await db.picks.find(
        match_filter,
        {"_id": 0, "result": 1, "playerName": 1, "timestamp": 1}
    ).sort("timestamp", -1).to_list(20)
    recent_streak = []
    for p in reversed(recent_raw):
        recent_streak.append({"result": p.get("result"), "name": p.get("playerName", "")})

    # Current win/loss streak
    streak_count = 0
    streak_type = None
    for p in recent_raw:
        r = p.get("result")
        if streak_type is None:
            streak_type = r
        if r == streak_type:
            streak_count += 1
        else:
            break

    LEAGUE_NAMES = {
        39: "Premier League", 140: "La Liga", 135: "Serie A",
        78: "Bundesliga", 61: "Ligue 1", 94: "Primeira Liga",
        203: "Süper Lig", 253: "MLS", 262: "Liga MX",
        2: "UEFA Champions League", 3: "UEFA Europa League",
        848: "UEFA Conference League", 40: "Championship",
        307: "Saudi Pro League", 128: "Liga Profesional",
        71: "Brasileirão", 188: "A-League", 13: "UEFA CL Qualifiers",
        254: "NWSL", 242: "Liga Pro Ecuador",
    }

    async def group_by_league():
        pipeline = [
            {"$match": match_filter},
            {"$group": {"_id": {"key": "$leagueId", "result": "$result"}, "count": {"$sum": 1}}},
        ]
        rows = await db.picks.aggregate(pipeline).to_list(200)
        buckets: dict = defaultdict(lambda: {"hit": 0, "miss": 0})
        for r in rows:
            key = r["_id"].get("key")
            name = LEAGUE_NAMES.get(key, f"League {key}") if key else "Unknown"
            buckets[name][r["_id"]["result"]] += r["count"]
        out = []
        for k, v in buckets.items():
            t = v["hit"] + v["miss"]
            out.append({"label": k, "hits": v["hit"], "misses": v["miss"],
                        "total": t, "winPct": pct(v["hit"], t)})
        return sorted(out, key=lambda x: -x["total"])

    direction = await group_by("recommendation")
    venue = await group_by("venue")
    position_raw = await group_by("position")
    position = [p for p in position_raw if p["total"] >= 3]
    prop_type = await group_by("propType")
    league = await group_by_league()

    # ── Brier Score + ROI by confidence tier ─────────────────────────────────
    # Fetch settled picks with confidence info (cutoff = post-placeholder-bug era)
    conf_picks = await db.picks.find(
        {"status": "settled", "result": {"$in": ["hit", "miss"]},
         "settledAt": {"$gte": "2026-04-30T00:00:00+00:00"}},
        {"_id": 0, "result": 1, "confidenceScore": 1, "rawConfidence": 1, "propType": 1}
    ).to_list(5000)

    # Brier Score: mean((prob - outcome)^2), lower = better
    # Use rawConfidence (pre-calibration) so we measure the engine, not the calibrator
    brier_sum, brier_n = 0.0, 0
    tier_buckets = {
        "High (≥70%)":  {"hits": 0, "misses": 0},
        "Medium (60–69%)": {"hits": 0, "misses": 0},
        "Low (<60%)":   {"hits": 0, "misses": 0},
    }
    for p in conf_picks:
        raw = p.get("rawConfidence") or p.get("confidenceScore") or 0
        if not raw or raw <= 0:
            continue
        outcome = 1 if p.get("result") == "hit" else 0
        prob = raw / 100.0
        brier_sum += (prob - outcome) ** 2
        brier_n += 1

        # Tier bucket
        if raw >= 70:
            tier_buckets["High (≥70%)"]["hits" if outcome else "misses"] += 1
        elif raw >= 60:
            tier_buckets["Medium (60–69%)"]["hits" if outcome else "misses"] += 1
        else:
            tier_buckets["Low (<60%)"]["hits" if outcome else "misses"] += 1

    brier_score = round(brier_sum / brier_n, 4) if brier_n >= 10 else None

    # ROI assumes -110 standard American odds: win=+$100, loss=-$110 per $110 wagered
    confidence_tiers = []
    for tier_label, counts in tier_buckets.items():
        h, m = counts["hits"], counts["misses"]
        t = h + m
        if t == 0:
            continue
        hit_rate = pct(h, t)
        roi = round((h * 100 - m * 110) / (t * 110) * 100, 1) if t else 0
        confidence_tiers.append({
            "label": tier_label,
            "hits": h, "misses": m, "total": t,
            "winPct": hit_rate,
            "roi": roi,
        })

    return {
        "overall": {
            "hits": total_hits,
            "misses": total_docs - total_hits,
            "total": total_docs,
            "winPct": pct(total_hits, total_docs),
        },
        "streak": {"type": streak_type, "count": streak_count},
        "recentForm": recent_streak[:10],
        "byDirection": direction,
        "byVenue": venue,
        "byPosition": position,
        "byPropType": prop_type,
        "byLeague": league,
        "brierScore": brier_score,
        "brierN": brier_n,
        "confidenceTiers": confidence_tiers,
    }


@app.get("/api/admin/top-props-table")
async def owner_top_props_table():
    """
    Owner-only: dual-view props intelligence table.
    Returns:
      - bandSummary: aggregated hit rates by deviation band + position + venue + direction
      - playerRows:  individual deduped picks (one per unique prediction event via trackingId)
                     with deviation band computed on-the-fly for older picks
    """
    from config import db

    LEAGUE_NAMES = {
        39: "Premier League", 140: "La Liga", 135: "Serie A",
        78: "Bundesliga", 61: "Ligue 1", 94: "Primeira Liga",
        203: "Süper Lig", 253: "MLS", 262: "Liga MX",
        2: "UEFA CL", 3: "UEFA EL", 848: "UEFA CL Conf",
        40: "Championship", 307: "Saudi Pro", 128: "Liga Prof.",
        71: "Brasileirão", 188: "A-League", 13: "UCL Qual.",
        254: "NWSL", 242: "Liga Pro Ecu",
    }
    CUP_LEAGUE_IDS = {2, 3, 848, 13}

    def _deviation_band(line, proj):
        if not line or not proj or proj <= 0:
            return None, None
        try:
            dev = abs(float(line) - float(proj)) / float(proj)
        except (TypeError, ValueError):
            return None, None
        if dev < 0.05:   return "aligned",  round(dev * 100, 1)
        if dev < 0.10:   return "mild",     round(dev * 100, 1)
        if dev < 0.15:   return "moderate", round(dev * 100, 1)
        if dev < 0.20:   return "elevated", round(dev * 100, 1)
        return "extreme", round(dev * 100, 1)

    BAND_ORDER = {"aligned": 0, "mild": 1, "moderate": 2, "elevated": 3, "extreme": 4}

    # ── Pull all settled picks ────────────────────────────────────────────
    raw_picks = await db.picks.find(
        {"status": "settled", "result": {"$in": ["hit", "miss"]}},
        {
            "_id": 0, "trackingId": 1, "playerName": 1, "position": 1,
            "propType": 1, "recommendation": 1, "result": 1,
            "line": 1, "projectedValue": 1, "actualValue": 1,
            "venue": 1, "leagueId": 1, "teamName": 1, "opponentName": 1,
            "lineDeviationBand": 1, "lineDeviationPct": 1,
            "timestamp": 1, "settledAt": 1, "confidenceScore": 1,
        }
    ).to_list(10000)

    # ── Deduplicate by trackingId (multiple users may save same prediction) ──
    seen_tracking: dict = {}
    for p in raw_picks:
        tid = p.get("trackingId") or f"{p.get('playerName','')}|{p.get('propType','')}|{p.get('line','')}|{p.get('recommendation','')}|{p.get('venue','')}"
        if tid not in seen_tracking:
            seen_tracking[tid] = p

    deduped = list(seen_tracking.values())

    # ── Build player rows (individual pick view) ──────────────────────────
    player_rows = []
    for p in deduped:
        line     = p.get("line")
        proj     = p.get("projectedValue")
        band     = p.get("lineDeviationBand")
        dev_pct  = p.get("lineDeviationPct")

        # Compute band on-the-fly for older picks without it stored
        if not band:
            band, dev_pct = _deviation_band(line, proj)

        rec     = (p.get("recommendation") or "").lower()
        result  = (p.get("result") or "").lower()
        lid     = p.get("leagueId")
        pos_raw = (p.get("position") or "").strip()

        # Normalise position to broad group for display
        pos_group = pos_raw
        if pos_raw in {"CB", "LB", "RB", "LWB", "RWB", "SW"}:
            pos_group = "Defender"
        elif pos_raw in {"CM", "CDM", "CAM", "DM", "AM", "RM", "LM"}:
            pos_group = "Midfielder"
        elif pos_raw in {"LW", "RW", "ST", "CF", "SS", "FW"}:
            pos_group = "Forward"
        elif pos_raw in {"GK"}:
            pos_group = "Goalkeeper"

        # Direction relative to book (line vs projection)
        if line and proj:
            try:
                book_high = float(line) > float(proj)
                against_book = (rec == "under" and book_high) or (rec == "over" and not book_high)
            except (TypeError, ValueError):
                against_book = False
        else:
            against_book = False

        ts = p.get("settledAt") or p.get("timestamp")
        date_str = ""
        if ts:
            try:
                if hasattr(ts, "strftime"):
                    date_str = ts.strftime("%m/%d")
                else:
                    from datetime import datetime, timezone
                    dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                    date_str = dt.strftime("%m/%d")
            except Exception:
                date_str = ""

        player_rows.append({
            "playerName":   p.get("playerName") or "—",
            "position":     pos_group or pos_raw or "—",
            "posRaw":       pos_raw or "—",
            "propType":     p.get("propType") or "—",
            "direction":    rec.upper() if rec else "—",
            "line":         round(float(line), 1) if line is not None else None,
            "projection":   round(float(proj), 1) if proj is not None else None,
            "deviationPct": dev_pct,
            "band":         band or "—",
            "bandOrder":    BAND_ORDER.get(band, 9),
            "venue":        (p.get("venue") or "—").capitalize(),
            "result":       result.upper() if result else "—",
            "actual":       round(float(p["actualValue"]), 1) if p.get("actualValue") is not None else None,
            "opponent":     p.get("opponentName") or "—",
            "teamName":     p.get("teamName") or "—",
            "league":       LEAGUE_NAMES.get(lid, f"Lg {lid}" if lid else "—"),
            "againstBook":  against_book,
            "confidence":   p.get("confidenceScore"),
            "date":         date_str,
        })

    player_rows.sort(key=lambda x: (x["bandOrder"], x["propType"], x["playerName"]))

    # ── Build band summary (aggregated view) ─────────────────────────────
    # Group deduped picks by: band + propType + direction + position + venue
    band_buckets: dict = {}
    for p in player_rows:
        band    = p["band"]
        prop    = p["propType"]
        direc   = p["direction"]
        pos     = p["position"]
        venue   = p["venue"]
        result  = p["result"]
        lid_raw = p["league"]
        key = (band, prop, direc, pos, venue)
        if key not in band_buckets:
            band_buckets[key] = {
                "band": band, "bandOrder": p["bandOrder"],
                "propType": prop, "direction": direc,
                "position": pos, "venue": venue,
                "hits": 0, "misses": 0, "total": 0,
                "lines": [], "players": set(),
                "league": lid_raw,
            }
        b = band_buckets[key]
        b["total"] += 1
        if result == "HIT":
            b["hits"] += 1
        elif result == "MISS":
            b["misses"] += 1
        b["players"].add(p["playerName"])
        if p["line"] is not None:
            b["lines"].append(p["line"])

    band_summary = []
    for key, b in band_buckets.items():
        total = b["total"]
        if total < 2:
            continue
        hits    = b["hits"]
        hit_pct = round(hits / total * 100, 1) if total > 0 else 0.0
        avg_line = round(sum(b["lines"]) / len(b["lines"]), 1) if b["lines"] else None
        band_summary.append({
            "band":         b["band"],
            "bandOrder":    b["bandOrder"],
            "propType":     b["propType"],
            "direction":    b["direction"],
            "position":     b["position"],
            "venue":        b["venue"],
            "hitPct":       hit_pct,
            "hits":         hits,
            "misses":       b["misses"],
            "total":        total,
            "avgLine":      avg_line,
            "uniquePlayers": len(b["players"]),
            "league":       b["league"],
        })

    band_summary.sort(key=lambda x: (x["bandOrder"], -x["hitPct"], -x["total"]))

    # ── Overall band stats (top-level summary cards) ──────────────────────
    overall_bands: dict = {}
    for p in player_rows:
        band   = p["band"]
        direc  = p["direction"]
        result = p["result"]
        k = (band, direc)
        if k not in overall_bands:
            overall_bands[k] = {"hits": 0, "total": 0, "bandOrder": p["bandOrder"]}
        overall_bands[k]["total"] += 1
        if result == "HIT":
            overall_bands[k]["hits"] += 1

    overall_summary = []
    for (band, direc), v in overall_bands.items():
        total    = v["total"]
        hit_pct  = round(v["hits"] / total * 100, 1) if total > 0 else 0.0
        overall_summary.append({
            "band": band, "direction": direc,
            "hitPct": hit_pct, "hits": v["hits"],
            "total": total, "bandOrder": v["bandOrder"],
        })
    overall_summary.sort(key=lambda x: (x["bandOrder"], x["direction"]))

    return {
        "playerRows":    player_rows,
        "bandSummary":   band_summary,
        "overallSummary": overall_summary,
        "totalDeduped":  len(deduped),
        "totalRaw":      len(raw_picks),
    }


@app.post("/api/admin/force-settle")
async def force_settle():
    """Immediately run the auto-settlement bot — use to unblock stuck picks."""
    from grok_engine import _run_auto_settlement
    try:
        await _run_auto_settlement()
        return {"ok": True, "message": "Settlement run complete — check picks for updates"}
    except Exception as e:
        return {"ok": False, "error": str(e)}
