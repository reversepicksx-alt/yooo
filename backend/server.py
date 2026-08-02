import os
from datetime import datetime, timezone, timedelta
from fastapi import FastAPI, Query, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
from config import db, LIFETIME_SUB_EMAILS, OWNER_EMAIL, OWNER_EMAILS, COMPLIMENTARY_MEMBERS, init_dynamic_settings, get_dynamic_setting

# ── Create App ──
app = FastAPI(title="Reverse Picks API")

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
from routes.stripe_pay import router as stripe_router
from routes.admin import router as admin_router
from routes.miss_analysis import router as miss_router
from routes.manual_search import router as manual_router
from routes.intel import router as intel_router
from routes.search import router as search_router
from routes.support import router as support_router
from routes.push import router as push_router
from routes.users import router as users_router
from routes.dm import router as dm_router
from routes.mlb_routes import router as mlb_router
from routes.cs2_routes import router as cs2_router
from routes.wta_routes import router as wta_router
from routes.nba_routes import router as nba_router
from routes.nfl_routes import router as nfl_router
from routes.nhl_routes import router as nhl_router
from routes.wnba_routes import router as wnba_router
from routes.ncaab_routes import router as ncaab_router
from routes.ncaaw_routes import router as ncaaw_router
from routes.atp_routes import router as atp_router
from routes.ai_sports_routes import router as ai_sport_router
from routes.ncaaf_routes import router as ncaaf_router
from routes.f1_routes import router as f1_router
from routes.mma_routes import router as mma_router
from routes.pga_routes import router as pga_router
from routes.dota2_routes import router as dota2_router
from routes.lol_routes import router as lol_router
from routes.cbase_routes import router as cbase_router
from routes.notifications import router as notifications_router
from routes.revenuecat_webhook import router as revenuecat_webhook_router
from routes.sports_config import router as sports_config_router
from cache import seed_cache, background_refresh_loop
from model_metrics import build_scorecard, dedupe_prediction_rows

app.include_router(auth_router)
app.include_router(revenuecat_webhook_router)
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
app.include_router(stripe_router)
app.include_router(admin_router)
app.include_router(miss_router)
app.include_router(manual_router)
app.include_router(intel_router)
app.include_router(search_router)
app.include_router(wta_router)
app.include_router(support_router)
app.include_router(push_router)
app.include_router(users_router)
app.include_router(dm_router)
app.include_router(mlb_router)
app.include_router(cs2_router)
app.include_router(nba_router)
app.include_router(nfl_router)
app.include_router(nhl_router)
app.include_router(wnba_router)
app.include_router(ncaab_router)
app.include_router(ncaaw_router)
app.include_router(atp_router)
app.include_router(ai_sport_router)
app.include_router(ncaaf_router)
app.include_router(f1_router)
app.include_router(mma_router)
app.include_router(pga_router)
app.include_router(dota2_router)
app.include_router(lol_router)
app.include_router(cbase_router)
app.include_router(notifications_router)
app.include_router(sports_config_router)


# ── Startup: seed grants for lifetime VIPs ──
@app.on_event("startup")
async def seed_grants():
    """ASGI lifespan startup. This MUST NEVER raise — if it does, uvicorn aborts
    startup, the worker dies, and (in production) there is no supervisor to bring
    it back, so the whole backend goes dark. All real work is delegated to
    _run_startup_tasks(); any failure there is logged but swallowed so the port
    always binds and the API (login, predictions, etc.) stays reachable."""
    try:
        await _run_startup_tasks()
    except Exception as _startup_err:
        import logging, traceback
        logging.getLogger("server").error(
            "[STARTUP] non-fatal error — backend will still serve the API. "
            f"{type(_startup_err).__name__}: {_startup_err}\n{traceback.format_exc()}"
        )


async def _run_startup_tasks():
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
    asyncio.create_task(_overdue_subscription_sweep())
    # Auto-backfill positions for picks missing them (runs once at startup)
    asyncio.create_task(_auto_backfill_positions())
    # Fix MLB picks saved with sport='soccer' before the sport-detection fix
    asyncio.create_task(_backfill_mlb_sport())
    # AI Engine background tasks
    from ai_engine import auto_settlement_loop, auto_scout_loop, pattern_mining_loop, mlb_live_loop, match_review_sweeper_loop
    asyncio.create_task(auto_settlement_loop())
    asyncio.create_task(match_review_sweeper_loop())
    asyncio.create_task(auto_scout_loop())
    asyncio.create_task(pattern_mining_loop())
    asyncio.create_task(mlb_live_loop())

    # Startup AI probe — verifies Replit Gemini integration is reachable
    async def _check_ai_api():
        import os as _os
        _log = __import__("logging").getLogger("server")
        from config import GEMINI_AI_ENABLED
        if not GEMINI_AI_ENABLED:
            _log.warning("[AI] Gemini disabled by emergency credit protection — math-only mode active.")
            return
        _key = _os.environ.get("AI_INTEGRATIONS_GEMINI_API_KEY", "")
        if not _key:
            _log.warning("[AI] AI_INTEGRATIONS_GEMINI_API_KEY not set — predictions will return empty AI narrative.")
            return
        try:
            from ai_engine import _ai_call
            _ping = await _ai_call("Reply with the single word: ready", max_tokens=10, timeout=15)
            if _ping:
                print(f"[AI] Replit Gemini integration active — model gemini-2.5-flash ready.")
            else:
                _log.warning("[AI] Gemini probe returned empty response.")
        except Exception as _e:
            _log.warning(f"[AI] Gemini probe error: {_e}")
    asyncio.create_task(_check_ai_api())

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
                print(f"[CONF CALIB] refreshed: keys={summary['keys']} buckets={summary['totalBuckets']} (min n={summary['minBucketN']})")
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

    # Calibration alerts: scan per-sport/per-prop walk-forward Brier score
    # and calibration gaps every 6h.  Emits AVOID/RISKY suppression signals
    # when confidence is systematically over-stated for a sport or prop type.
    async def _calibration_alerts_loop():
        from calibration_alerts import refresh_calibration_alerts
        import asyncio as _a
        while True:
            try:
                await refresh_calibration_alerts(db)
            except Exception as _e:
                print(f"[CAL ALERTS] refresh failed: {_e}")
            await _a.sleep(6 * 60 * 60)
    asyncio.create_task(_calibration_alerts_loop())

    # Odds-tier empirical priors: auto-learn from settled picks every 6h.
    # Mirrors scenario_priors / league_priors cadence. Min sample n=8.
    async def _odds_tier_loop():
        from odds_tier_priors import ensure_loaded as _ensure_ot
        import asyncio as _a
        while True:
            try:
                await _ensure_ot(db)
            except Exception as _e:
                print(f"[ODDS-TIER PRIORS] refresh failed: {_e}")
            await _a.sleep(6 * 60 * 60)
    asyncio.create_task(_odds_tier_loop())

    # Self-updating cheat sheet — re-renders attached_assets/cheat_sheet_2_1.png
    # from settled picks every few hours so it never goes stale.
    asyncio.create_task(_cheat_sheet_loop())

    # Bulk player-stats prefetch is intentionally opt-in. It can consume
    # hundreds of API-Football calls before a user makes a request; predictions
    # now fill the cache on demand instead.
    from config import API_BULK_PREFETCH_ENABLED
    if API_BULK_PREFETCH_ENABLED:
        from data_prefetch import data_prefetch_loop, backfill_fixture_metadata
        asyncio.create_task(data_prefetch_loop())
        asyncio.create_task(backfill_fixture_metadata(max_fixtures=100))
    else:
        print("[PREFETCH] Disabled by default — using on-demand cache fills")

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
    """Auto-backfill missing positions on startup using cache + Gemini AI fallback."""
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

        # Step 3: Use Gemini to batch-resolve remaining positions
        if unresolved:
            from ai_engine import _ai_call as _gemini_pos
            import json as _json
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
                    raw = await _gemini_pos(prompt, temperature=0, max_tokens=1000, timeout=25, json_mode=True)
                    if raw:
                        content = raw.strip()
                        if content.startswith("```"):
                            content = content.split("\n", 1)[1] if "\n" in content else content[3:]
                            content = content.rsplit("```", 1)[0]
                        resolved = _json.loads(content.strip())
                        grok_updated = 0
                        for r in resolved:
                            rname = r.get("name", "")
                            rpos = r.get("position", "")
                            rrole = r.get("role", "")
                            if rname and rpos:
                                await db.picks.update_many(
                                    {"playerName": rname, "$or": [{"position": {"$exists": False}}, {"position": ""}, {"position": None}]},
                                    {"$set": {"position": rpos, "role": rrole or ""}}
                                )
                                matching = [u for u in batch if u["playerName"] == rname]
                                for m in matching:
                                    if m.get("playerId"):
                                        await db.player_positions.update_one(
                                            {"playerId": m["playerId"]},
                                            {"$set": {"playerId": m["playerId"], "specificPosition": rpos, "role": rrole or ""}},
                                            upsert=True
                                        )
                                grok_updated += 1
                        print(f"[AUTO-BACKFILL] Gemini resolved: {grok_updated} players (batch {i//30+1})")
                except Exception as e:
                    print(f"[AUTO-BACKFILL] Gemini batch error: {e}")

        print(f"[AUTO-BACKFILL] Done. Total cache-resolved: {updated}, AI batches sent: {(len(unresolved)+29)//30 if unresolved else 0}")
    except Exception as e:
        print(f"[AUTO-BACKFILL] Error: {e}")



async def _overdue_subscription_sweep():
    """Expire retired Stripe subscribers whose paid period has ended."""
    import asyncio
    await asyncio.sleep(15)
    await _retire_stripe_subscriptions_once()
    while True:
        try:
            # Keep retrying the live Stripe shutdown until every listed
            # subscription has been processed. A transient Stripe/API error
            # must not leave a recurring subscription untouched forever.
            retirement_marker = await db.settings.find_one(
                {"key": "stripe_retirement_complete", "value": "true"},
                {"_id": 0, "value": 1},
            )
            if not retirement_marker:
                await _retire_stripe_subscriptions_once()

            from datetime import date as date_type, datetime, timezone

            now = datetime.now(timezone.utc)
            now_iso = now.isoformat()
            canceled_count = 0

            # Expire anyone whose currentPeriodEnd has passed
            active_subs = await db.stripe_subscriptions.find(
                {"status": {"$in": ["active", "trialing", "canceled"]}},
                {"_id": 0, "email": 1, "currentPeriodEnd": 1}
            ).to_list(200)

            for sub in active_subs:
                email = sub.get("email", "")
                cpe_raw = sub.get("currentPeriodEnd", "")
                if not cpe_raw:
                    continue
                try:
                    cpe_date = date_type.fromisoformat(str(cpe_raw)[:10])
                except Exception:
                    continue
                # A dashboard "Cancels <date>" date is the final access
                # calendar day; expire beginning the next day.
                if cpe_date >= date_type.today():
                    continue

                days_overdue = (date_type.today() - cpe_date).days
                print(f"[OVERDUE SWEEP] {email}: Stripe period ended {cpe_date} ({days_overdue}d ago) — expiring")
                await db.stripe_subscriptions.update_one(
                    {"email": email},
                    {"$set": {
                        "status": "expired",
                        "expiredReason": "period_ended",
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


async def _retire_stripe_subscriptions_once():
    """Stop all Stripe renewals without removing already-paid access.

    Active/trialing subscriptions are canceled at period end. Failed or
    incomplete subscriptions are canceled immediately so Stripe cannot retry
    charges. The marker makes this safe to run again after a transient error.
    """
    marker = await db.settings.find_one(
        {"key": "stripe_retirement_complete"}, {"_id": 0, "value": 1}
    )
    if marker and marker.get("value") == "true":
        return

    try:
        import stripe
        key = os.environ.get("STRIPE_SECRET_KEY", "")
        if not key:
            print("[STRIPE RETIREMENT] Stripe key unavailable; will retry")
            return
        stripe.api_key = key
        processed = 0
        failed = 0
        for status in ("active", "trialing", "past_due", "unpaid", "incomplete", "paused"):
            for sub in stripe.Subscription.list(
                status=status, limit=100
            ).auto_paging_iter():
                try:
                    if status in ("active", "trialing"):
                        updated = stripe.Subscription.modify(
                            sub.id, cancel_at_period_end=True
                        )
                        period_end = (
                            getattr(updated, "current_period_end", None)
                            or getattr(sub, "current_period_end", None)
                        )
                        fields = {
                            "status": "canceled",
                            "retiredByMigration": True,
                            "updatedAt": datetime.now(timezone.utc).isoformat(),
                        }
                        if period_end:
                            fields["currentPeriodEnd"] = datetime.fromtimestamp(
                                int(period_end), tz=timezone.utc
                            ).isoformat()
                        await db.stripe_subscriptions.update_one(
                            {"stripeSubscriptionId": sub.id}, {"$set": fields}
                        )
                    else:
                        stripe.Subscription.cancel(sub.id)
                        await db.stripe_subscriptions.update_one(
                            {"stripeSubscriptionId": sub.id},
                            {"$set": {
                                "status": "expired",
                                "expiredReason": "stripe_retired_failed_payment",
                                "retiredByMigration": True,
                                "updatedAt": datetime.now(timezone.utc).isoformat(),
                            }},
                        )
                    processed += 1
                except Exception as sub_error:
                    failed += 1
                    print(f"[STRIPE RETIREMENT] Could not process {sub.id}: {sub_error}")

        if failed:
            print(f"[STRIPE RETIREMENT] Processed {processed}; {failed} failed — retrying")
            return
        await db.settings.update_one(
            {"key": "stripe_retirement_complete"},
            {"$set": {
                "key": "stripe_retirement_complete",
                "value": "true",
                "processed": processed,
                "updatedAt": datetime.now(timezone.utc).isoformat(),
            }},
            upsert=True,
        )
        print(f"[STRIPE RETIREMENT] Complete — {processed} subscription(s) stopped")
    except Exception as error:
        print(f"[STRIPE RETIREMENT] Error; will retry: {error}")


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


@app.post("/api/admin/analytics")
async def owner_analytics(payload: dict = Body(...)):
    """ReversePicks system-wide soccer scorecard, owner-authenticated."""
    from config import db
    from collections import defaultdict

    email = str(payload.get("email") or "").lower().strip()
    token = str(payload.get("token") or "")
    period = str(payload.get("period") or "all").lower()
    if period not in {"all", "30d", "7d"}:
        raise HTTPException(status_code=400, detail="Invalid analytics period")
    if email not in OWNER_EMAILS:
        raise HTTPException(status_code=403, detail="Owner access required")
    session = await db.sessions.find_one(
        {"email": email, "session_token": token}, {"_id": 0, "email": 1}
    )
    if not session:
        raise HTTPException(status_code=401, detail="Invalid session")

    # Authentication identifies who may view this private system report.  It
    # must not scope the data to that person's picks: ReversePicks calibration
    # is learned from the entire settled soccer population.
    settled_filter = {
        "sport": "soccer",
        "status": "settled",
    }
    raw_rows = await db.picks.find(
        settled_filter,
        {
            "_id": 0, "trackingId": 1, "playerName": 1, "sport": 1,
            "propType": 1, "line": 1, "recommendation": 1, "passLeaning": 1,
            "venue": 1,
            "position": 1, "leagueId": 1, "leagueName": 1,
            "playerId": 1, "teamId": 1, "opponentId": 1,
            "fixtureId": 1, "fixtureDate": 1, "matchDate": 1,
            "timestamp": 1, "createdAt": 1, "settledAt": 1,
            "result": 1, "confidenceScore": 1, "rawConfidence": 1,
            "confidenceLevel": 1, "projectedValue": 1, "projection": 1,
            "actualValue": 1, "passOutcome": 1, "isCalibrationOnly": 1,
        },
    ).to_list(100000)
    if period != "all":
        days = 30 if period == "30d" else 7
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

        def in_period(row: dict) -> bool:
            raw_date = row.get("settledAt") or row.get("timestamp") or row.get("createdAt")
            if not raw_date:
                return False
            try:
                parsed = raw_date if isinstance(raw_date, datetime) else datetime.fromisoformat(
                    str(raw_date).replace("Z", "+00:00")
                )
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return parsed >= cutoff
            except (TypeError, ValueError):
                return False

        raw_rows = [row for row in raw_rows if in_period(row)]
    deduped_rows = dedupe_prediction_rows(raw_rows)
    actionable_rows = [
        row for row in deduped_rows
        if str(row.get("result") or "").lower() in {"hit", "miss"}
    ]

    def pct(h, t):
        return round(h / t * 100, 1) if t > 0 else 0.0

    async def group_by(field: str):
        buckets: dict = defaultdict(lambda: {"hit": 0, "miss": 0})
        for row in actionable_rows:
            key = row.get(field) or "Unknown"
            result = str(row.get("result") or "").lower()
            buckets[str(key)][result] += 1
        out = []
        for k, v in buckets.items():
            t = v["hit"] + v["miss"]
            out.append({"label": k, "hits": v["hit"], "misses": v["miss"],
                        "total": t, "winPct": pct(v["hit"], t)})
        return sorted(out, key=lambda x: -x["winPct"])

    total_settled = len(deduped_rows)
    total_hits = sum(str(row.get("result") or "").lower() == "hit" for row in deduped_rows)
    total_misses = sum(str(row.get("result") or "").lower() == "miss" for row in deduped_rows)
    total_pushes = sum(str(row.get("result") or "").lower() == "push" for row in deduped_rows)
    total_dnps = sum(str(row.get("result") or "").lower() == "dnp" for row in deduped_rows)
    total_passes = sum(
        str(row.get("recommendation") or "").lower() == "pass"
        or bool(row.get("isCalibrationOnly"))
        for row in deduped_rows
    )
    pass_calibration_counts = {"hit": 0, "miss": 0, "push": 0}
    pass_calibration_by_direction: dict = defaultdict(
        lambda: {"hit": 0, "miss": 0, "push": 0}
    )
    for row in deduped_rows:
        is_pass = (
            str(row.get("recommendation") or "").lower() == "pass"
            or bool(row.get("isCalibrationOnly"))
        )
        outcome = str(row.get("passOutcome") or "").lower()
        if not is_pass or outcome not in pass_calibration_counts:
            continue
        direction = str(row.get("passLeaning") or "").lower()
        if direction not in {"over", "under"}:
            direction = "unknown"
        pass_calibration_counts[outcome] += 1
        pass_calibration_by_direction[direction][outcome] += 1
    pass_calibration_scored = (
        pass_calibration_counts["hit"] + pass_calibration_counts["miss"]
    )

    # Streak: last N settled picks in chronological order
    recent_raw = sorted(
        actionable_rows,
        key=lambda row: str(row.get("settledAt") or row.get("timestamp") or ""),
        reverse=True,
    )[:20]
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
        buckets: dict = defaultdict(lambda: {"hit": 0, "miss": 0})
        for row in actionable_rows:
            key = row.get("leagueId")
            name = LEAGUE_NAMES.get(key, f"League {key}") if key else "Unknown"
            result = str(row.get("result") or "").lower()
            buckets[name][result] += 1
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
    conf_picks = [
        row for row in actionable_rows
        if str(row.get("settledAt") or "") >= "2026-04-30T00:00:00+00:00"
    ]

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

    # ── Model scorecard: probability + numerical projection quality ─────────
    # Keep these metrics separate from the legacy hit-rate breakdown above.
    # Numeric errors are grouped by sport/prop because their units differ.
    scorecard = build_scorecard(deduped_rows)

    def dashboard_groups(field: str, labeler=None):
        buckets: dict = defaultdict(lambda: {
            "hit": 0, "miss": 0, "overHit": 0, "overTotal": 0,
            "underHit": 0, "underTotal": 0,
        })
        for row in actionable_rows:
            raw_key = row.get(field)
            key = labeler(raw_key) if labeler else (raw_key or "Unknown")
            key = str(key)
            result = str(row.get("result") or "").lower()
            direction_value = str(row.get("recommendation") or "").upper()
            buckets[key][result] += 1
            if direction_value == "OVER":
                buckets[key]["overTotal"] += 1
                if result == "hit":
                    buckets[key]["overHit"] += 1
            elif direction_value == "UNDER":
                buckets[key]["underTotal"] += 1
                if result == "hit":
                    buckets[key]["underHit"] += 1
        output = []
        for label, values in buckets.items():
            total = values["hit"] + values["miss"]
            output.append({
                "label": label,
                "total": total,
                "rate": pct(values["hit"], total),
                "overRate": pct(values["overHit"], values["overTotal"]),
                "underRate": pct(values["underHit"], values["underTotal"]),
            })
        return sorted(output, key=lambda item: -item["total"])

    dashboard_sorted = sorted(
        actionable_rows,
        key=lambda row: str(row.get("settledAt") or row.get("timestamp") or ""),
        reverse=True,
    )
    daily: dict = defaultdict(lambda: {"hit": 0, "total": 0})
    for row in actionable_rows:
        date_value = str(row.get("settledAt") or row.get("timestamp") or "")
        day = date_value[:10]
        if len(day) == 10:
            daily[day]["total"] += 1
            if str(row.get("result") or "").lower() == "hit":
                daily[day]["hit"] += 1
    trend = [
        {
            "date": day,
            "rate": pct(values["hit"], values["total"]),
            "total": values["total"],
        }
        for day, values in sorted(daily.items())
    ]
    over_rows = [row for row in actionable_rows if str(row.get("recommendation") or "").upper() == "OVER"]
    under_rows = [row for row in actionable_rows if str(row.get("recommendation") or "").upper() == "UNDER"]
    tier_definitions = (
        ("High (≥70%)", 70, 101),
        ("Medium (60–69%)", 60, 70),
        ("Low (<60%)", 0, 60),
    )
    dashboard_tiers = []
    for label, lower, upper in tier_definitions:
        tier_rows = [
            row for row in actionable_rows
            if lower <= (float(row.get("confidenceScore") or row.get("rawConfidence") or 0)) < upper
        ]
        hits = sum(str(row.get("result") or "").lower() == "hit" for row in tier_rows)
        dashboard_tiers.append({
            "tier": label,
            "hit": hits,
            "total": len(tier_rows),
            "rate": pct(hits, len(tier_rows)),
        })
    dashboard_leagues = dashboard_groups(
        "leagueId",
        lambda value: LEAGUE_NAMES.get(value, f"League {value}") if value else "Unknown",
    )
    dashboard_insights = {
        "total": total_settled,
        "settled": total_settled,
        "hits": total_hits,
        "misses": total_misses,
        "pushes": total_pushes,
        "winRate": pct(total_hits, total_hits + total_misses),
        "currentStreak": streak_count if streak_type == "hit" else 0,
        "overHit": pct(
            sum(str(row.get("result") or "").lower() == "hit" for row in over_rows),
            len(over_rows),
        ),
        "underHit": pct(
            sum(str(row.get("result") or "").lower() == "hit" for row in under_rows),
            len(under_rows),
        ),
        "overTotal": len(over_rows),
        "underTotal": len(under_rows),
        "tiers": dashboard_tiers,
        "trend": trend,
        "byLeague": dashboard_leagues,
        "byProp": dashboard_groups("propType"),
        "bySport": dashboard_groups("sport"),
        "bestLeagues": sorted(
            [row for row in dashboard_leagues if row["total"] >= 5],
            key=lambda row: -row["rate"],
        )[:3],
        "worstLeagues": sorted(
            [row for row in dashboard_leagues if row["total"] >= 5],
            key=lambda row: row["rate"],
        )[:3],
    }

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
            "misses": total_misses,
            "total": total_settled,
            "winPct": pct(total_hits, total_hits + total_misses),
            "pushes": total_pushes,
            "dnps": total_dnps,
            "calibrationOnly": total_passes,
            "actionable": total_hits + total_misses,
            "passCalibration": {
                "n": sum(pass_calibration_counts.values()),
                "hits": pass_calibration_counts["hit"],
                "misses": pass_calibration_counts["miss"],
                "pushes": pass_calibration_counts["push"],
                "winPct": pct(
                    pass_calibration_counts["hit"], pass_calibration_scored
                ),
                "byDirection": dict(pass_calibration_by_direction),
            },
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
        "scorecard": scorecard,
        "insights": dashboard_insights,
        "scope": {
            "access": "owner",
            "dataset": "all_users",
            "sport": "soccer",
            "period": period,
            "rawSettled": len(raw_rows),
            "settled": total_settled,
            "duplicateRowsRemoved": len(raw_rows) - total_settled,
        },
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
    from ai_engine import _run_auto_settlement, _repair_pending_review_soccer_batch
    try:
        await _run_auto_settlement()
        # The normal bot uses one bounded batch per scheduled run. An explicit
        # force action is allowed to drain the legacy review backlog in bounded
        # batches, without requiring the owner to refresh the app 20 times.
        repair_batches = []
        for _ in range(12):
            summary = await _repair_pending_review_soccer_batch(limit=40)
            repair_batches.append(summary)
            if summary["found"] == 0 or summary["repaired"] == 0:
                break
        return {
            "ok": True,
            "message": "Settlement run complete — check picks for updates",
            "reviewRepair": repair_batches,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/admin/repair-pending-review")
async def repair_pending_review(payload: dict = Body(...)):
    """Owner-only bounded repair for legacy soccer pending-review records.

    This intentionally does not run the normal multi-sport settlement sweep.
    The caller can repeat the bounded operation while monitoring provider
    quota, and every repair remains deterministic and provenance-verified.
    """
    from routes.admin import verify_owner
    from ai_engine import _repair_pending_review_soccer_batch

    await verify_owner(
        str(payload.get("email") or ""),
        str(payload.get("token") or ""),
    )
    try:
        limit = max(1, min(int(payload.get("limit") or 40), 40))
        batches = max(1, min(int(payload.get("batches") or 1), 4))
        summaries = []
        for _ in range(batches):
            summary = await _repair_pending_review_soccer_batch(
                limit=limit,
                include_legacy=bool(payload.get("includeLegacy", True)),
                pick_ids=(
                    [str(payload["pickId"])]
                    if payload.get("pickId")
                    else None
                ),
            )
            summaries.append(summary)
            if summary["found"] == 0:
                break
        return {
            "ok": True,
            "reviewRepair": summaries,
            "totals": {
                key: sum(item.get(key, 0) for item in summaries)
                for key in ("found", "repaired", "deferred", "errors")
            },
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.post("/api/admin/pending-review-status")
async def pending_review_status(payload: dict = Body(...)):
    """Owner-only read-only snapshot of the pending-review backlog."""
    from routes.admin import verify_owner

    await verify_owner(
        str(payload.get("email") or ""),
        str(payload.get("token") or ""),
    )
    query = {
        "$and": [
            {
                "$or": [
                    {"status": "pending_review"},
                    {"result": "pending_review"},
                    {"settlementReview": {"$exists": True}},
                ]
            },
            {
                "$or": [
                    {"sport": "soccer"},
                    {"sport": {"$exists": False}},
                ]
            },
        ]
    }
    count = await db.picks.count_documents(query)
    legacy_query = {
        "sport": "soccer",
        "status": "settled",
        "result": {"$in": ["hit", "miss", "push", "dnp", "pass"]},
        "settlementSource.verified": {"$ne": True},
        "correctedManually": {"$ne": True},
    }
    legacy_count = await db.picks.count_documents(legacy_query)
    andy_rows = await db.picks.find(
        {
            "sport": "soccer",
            "$or": [
                {"playerName": {"$regex": "Andy Aryel Najar", "$options": "i"}},
                {"playerName": {"$regex": "Najar", "$options": "i"}},
            ],
        },
        {
            "_id": 0,
            "pickId": 1,
            "playerName": 1,
            "status": 1,
            "result": 1,
            "actualValue": 1,
            "line": 1,
            "recommendation": 1,
            "fixtureId": 1,
            "playerId": 1,
            "teamId": 1,
            "opponentId": 1,
            "settlementReview": 1,
            "settlementSource": 1,
            "settlementRepairAudit": 1,
            "settlementRepairLastAttemptReason": 1,
        },
    ).sort("timestamp", -1).to_list(10)
    return {
        "ok": True,
        "pendingReviewCount": count,
        "legacyUnverifiedCount": legacy_count,
        "totalUnverifiedSoccerCount": count + legacy_count,
        "andyNajar": andy_rows,
    }


@app.post("/api/admin/bulk-resettle-zero-picks")
async def bulk_resettle_zero_picks(payload: dict):
    """
    Find all settled picks where actualValue=0 and propType is a count stat
    (pass_attempts, crosses, tackles, etc.) and re-settle them via API-Football.
    These are almost always wrong settlements from the BDL path returning None/0.

    Body: { "secret": "...", "dryRun": true/false }
    """
    import os
    from datetime import datetime, timezone
    from routes.picks import _settle_soccer_pick

    _admin_secret = os.environ.get("ADMIN_SECRET", "")
    if not _admin_secret or payload.get("secret", "") != _admin_secret:
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Invalid secret")

    dry_run = payload.get("dryRun", True)

    _COUNT_PROPS = {
        "pass_attempts", "passes", "crosses", "tackles", "key_passes",
        "shots", "shots_on_target", "interceptions", "blocks", "dribbles",
        "dribbles_success", "fouls_drawn", "fouls_committed", "clearances",
        "duels_won",
    }

    candidates = await db.picks.find(
        {
            "status": "settled",
            "result": {"$in": ["miss", "hit"]},
            "actualValue": 0,
            "propType": {"$in": list(_COUNT_PROPS)},
            "sport": "soccer",
        },
        {"_id": 0}
    ).to_list(200)

    results = []
    for pick_doc in candidates:
        pick_id    = pick_doc.get("pickId", "")
        player_id  = pick_doc.get("playerId") or 0
        team_id    = pick_doc.get("teamId")   or 0
        opponent   = pick_doc.get("opponentName", "")
        prop_type  = pick_doc.get("propType", "")
        league_id  = pick_doc.get("leagueId") or 0

        pick_for_settle = {**pick_doc, "status": "live", "id": pick_id}

        entry = {
            "pickId":       pick_id,
            "playerName":   pick_doc.get("playerName"),
            "propType":     prop_type,
            "line":         pick_doc.get("line"),
            "recommendation": pick_doc.get("recommendation"),
            "previousResult": pick_doc.get("result"),
        }

        if dry_run:
            entry["action"] = "would-resettle (dryRun=true)"
            results.append(entry)
            continue

        try:
            result = await _settle_soccer_pick(
                pick_for_settle, team_id, player_id, opponent, prop_type, league_id
            )
        except Exception as e:
            entry["action"] = f"error: {e}"
            results.append(entry)
            continue

        if not result:
            entry["action"] = "settlement-returned-none (match not found or stat unavailable)"
            results.append(entry)
            continue

        update_fields = {
            "status":         result.get("status", "settled"),
            "result":         result.get("result"),
            "actualValue":    result.get("actualValue"),
            "minutesPlayed":  result.get("minutesPlayed"),
            "settledAt":      datetime.now(timezone.utc).isoformat(),
            "resettledAt":    datetime.now(timezone.utc).isoformat(),
            "resettleReason": f"Bulk resettle: was {pick_doc.get('result')} with actualValue=0",
        }
        for key in ("matchScore", "homeTeam", "awayTeam", "finalHomeGoals",
                    "finalAwayGoals", "homePoss", "awayPoss", "fixtureDate",
                    "voidReason", "settlementSource"):
            if result.get(key) is not None:
                update_fields[key] = result[key]

        await db.picks.update_one({"pickId": pick_id}, {"$set": update_fields})

        entry["action"]         = "resettled"
        entry["newResult"]      = result.get("result")
        entry["newActualValue"] = result.get("actualValue")
        entry["minutesPlayed"]  = result.get("minutesPlayed")
        results.append(entry)

    return {
        "ok":      True,
        "dryRun":  dry_run,
        "found":   len(candidates),
        "results": results,
    }


@app.post("/api/admin/force-resettle-pick")
async def force_resettle_pick(payload: dict):
    """
    Re-settle a pick that was incorrectly settled (e.g. actualValue=0 from BDL).
    Bypasses the already-settled guard and re-fetches from API-Football.

    Body: { "secret": "...", "pickId": "..." }
    """
    import os
    from datetime import datetime, timezone
    from routes.picks import _settle_soccer_pick

    _admin_secret = os.environ.get("ADMIN_SECRET", "")
    if not _admin_secret or payload.get("secret", "") != _admin_secret:
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Invalid secret")

    pick_id = payload.get("pickId", "").strip()
    if not pick_id:
        return {"ok": False, "error": "pickId is required"}

    pick_doc = await db.picks.find_one({"pickId": pick_id}, {"_id": 0})
    if not pick_doc:
        return {"ok": False, "error": f"Pick {pick_id!r} not found"}

    prev_status = pick_doc.get("status")
    prev_result = pick_doc.get("result")
    prev_actual = pick_doc.get("actualValue")

    # Build the args _settle_soccer_pick expects.
    # DB doc stores these at the top level (saved by save_pick).
    player_id  = pick_doc.get("playerId") or 0
    team_id    = pick_doc.get("teamId")   or 0
    opponent   = pick_doc.get("opponentName", "")
    prop_type  = pick_doc.get("propType", "")
    league_id  = pick_doc.get("leagueId") or 0

    # _settle_soccer_pick reads pick.get("timestamp") — ensure it's present.
    # Force status to "live" so _build_soccer_update's settled-guard doesn't block.
    pick_for_settle = {**pick_doc, "status": "live", "id": pick_id}

    try:
        result = await _settle_soccer_pick(
            pick_for_settle, team_id, player_id, opponent, prop_type, league_id
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"ok": False, "error": f"Settlement raised: {e}"}

    if not result:
        return {
            "ok": False,
            "error": "Settlement returned None — match not found or stat unavailable",
            "previousResult": prev_result,
            "previousActualValue": prev_actual,
        }

    # Apply the corrected settlement to the DB.
    update_fields = {
        "status":         result.get("status", "settled"),
        "result":         result.get("result"),
        "actualValue":    result.get("actualValue"),
        "minutesPlayed":  result.get("minutesPlayed"),
        "settledAt":      datetime.now(timezone.utc).isoformat(),
        "resettledAt":    datetime.now(timezone.utc).isoformat(),
        "resettleReason": f"Admin force-resettle (was {prev_result}, actualValue={prev_actual})",
    }
    if result.get("settlementSource") is not None:
        update_fields["settlementSource"] = result["settlementSource"]
    update_fields["settlementCorrection"] = {
        "previousResult": prev_result,
        "previousActualValue": prev_actual,
        "correctedBy": "admin_force_resettle",
        "correctedAt": datetime.now(timezone.utc).isoformat(),
    }
    for key in ("matchScore", "homeTeam", "awayTeam", "finalHomeGoals",
                "finalAwayGoals", "homePoss", "awayPoss", "fixtureDate",
                "voidReason", "settlementSource"):
        if result.get(key) is not None:
            update_fields[key] = result[key]

    await db.picks.update_one({"pickId": pick_id}, {"$set": update_fields})

    return {
        "ok": True,
        "pickId":       pick_id,
        "playerName":   pick_doc.get("playerName"),
        "propType":     prop_type,
        "line":         pick_doc.get("line"),
        "recommendation": pick_doc.get("recommendation"),
        "previousResult": prev_result,
        "previousActualValue": prev_actual,
        "newResult":    result.get("result"),
        "newActualValue": result.get("actualValue"),
        "minutesPlayed": result.get("minutesPlayed"),
        "matchScore":   result.get("matchScore"),
    }


@app.post("/api/admin/repair-soccer-settlements")
async def repair_soccer_settlements(payload: dict):
    """Audit or repair suspicious positive soccer settlements.

    The endpoint is intentionally dry-run by default.  A write is allowed only
    when the saved pick has an exact fixtureId and the fresh settlement returns
    a verified provider source.  The previous settlement is retained in an
    audit object before the replacement is written.

    Body: {
      "secret": "...",
      "dryRun": true,
      "pickId": "optional-single-id",
      "pickIds": ["optional", "ids"]
    }
    """
    import os
    from datetime import datetime, timezone
    from routes.picks import _settle_soccer_pick

    _admin_secret = os.environ.get("ADMIN_SECRET", "")
    if not _admin_secret or payload.get("secret", "") != _admin_secret:
        raise HTTPException(status_code=403, detail="Invalid secret")

    dry_run = payload.get("dryRun", True) is not False
    requested_ids = set()
    if payload.get("pickId"):
        requested_ids.add(str(payload["pickId"]).strip())
    for value in payload.get("pickIds", []) or []:
        if value:
            requested_ids.add(str(value).strip())

    _COUNT_PROPS = {
        "pass_attempts", "passes", "crosses", "tackles", "key_passes",
        "shots", "shots_on_target", "interceptions", "blocks", "dribbles",
        "dribbles_success", "fouls_drawn", "fouls_committed", "clearances",
        "duels_won",
    }
    query = {
        "sport": "soccer",
        "status": "settled",
        "actualValue": {"$gt": 0},
        "propType": {"$in": list(_COUNT_PROPS)},
    }
    if requested_ids:
        query["pickId"] = {"$in": list(requested_ids)}
    else:
        # Positive legacy values without a verified source are the suspicious
        # population. Explicit pickIds remain auditable even if already marked
        # verified, which is useful for investigating a known bad record.
        query["settlementSource.verified"] = {"$ne": True}

    candidates = await db.picks.find(query, {"_id": 0}).to_list(200)
    results = []
    for pick_doc in candidates:
        pick_id = pick_doc.get("pickId", "")
        entry = {
            "pickId": pick_id,
            "playerName": pick_doc.get("playerName"),
            "propType": pick_doc.get("propType"),
            "fixtureId": pick_doc.get("fixtureId"),
            "previousResult": pick_doc.get("result"),
            "previousActualValue": pick_doc.get("actualValue"),
            "previousSettlementSource": pick_doc.get("settlementSource"),
        }
        if not pick_doc.get("fixtureId"):
            entry["action"] = "skipped: exact fixtureId is required"
            results.append(entry)
            continue
        if dry_run:
            entry["action"] = "would-refetch-exact-fixture-and-player (dryRun=true)"
            results.append(entry)
            continue

        pick_for_settle = {**pick_doc, "status": "live", "id": pick_id}
        try:
            result = await _settle_soccer_pick(
                pick_for_settle,
                pick_doc.get("teamId") or 0,
                pick_doc.get("playerId") or 0,
                pick_doc.get("opponentName", ""),
                pick_doc.get("propType", ""),
                pick_doc.get("leagueId") or 0,
            )
        except Exception as exc:
            entry["action"] = f"error: {exc}"
            results.append(entry)
            continue

        source = (result or {}).get("settlementSource") or {}
        if not result or source.get("verified") is not True:
            entry["action"] = "skipped: fresh exact settlement was not verified"
            entry["freshSettlementSource"] = source or None
            results.append(entry)
            continue

        now = datetime.now(timezone.utc).isoformat()
        new_result = result.get("result")
        update_fields = {
            "status": result.get("status", "settled"),
            "result": new_result,
            "actualValue": result.get("actualValue"),
            "minutesPlayed": result.get("minutesPlayed"),
            "hitPct": 100 if new_result == "hit" else 0 if new_result == "miss" else 50,
            "settledAt": now,
            "resettledAt": now,
            "settlementSource": source,
            "settlementRepairAudit": {
                "previous": {
                    "status": pick_doc.get("status"),
                    "result": pick_doc.get("result"),
                    "actualValue": pick_doc.get("actualValue"),
                    "settlementSource": pick_doc.get("settlementSource"),
                },
                "replacement": {
                    "result": new_result,
                    "actualValue": result.get("actualValue"),
                    "settlementSource": source,
                },
                "correctedBy": "admin_repair_soccer_settlement",
                "correctedAt": now,
            },
            "resettleReason": (
                f"Verified exact-fixture repair "
                f"(was {pick_doc.get('result')}, actualValue={pick_doc.get('actualValue')})"
            ),
        }
        for key in (
            "matchScore", "homeTeam", "awayTeam", "finalHomeGoals",
            "finalAwayGoals", "homePoss", "awayPoss", "fixtureDate", "voidReason",
        ):
            if result.get(key) is not None:
                update_fields[key] = result[key]
        await db.picks.update_one({"pickId": pick_id}, {"$set": update_fields})
        entry.update({
            "action": "repaired",
            "newResult": new_result,
            "newActualValue": result.get("actualValue"),
            "freshSettlementSource": source,
        })
        results.append(entry)

    return {
        "ok": True,
        "dryRun": dry_run,
        "found": len(candidates),
        "results": results,
    }


@app.get("/api/admin/sessions")
async def list_active_sessions(email: str = Query(...)):
    """Owner-only: return all active sessions with last-active info."""
    from config import OWNER_EMAILS
    email_lower = email.lower().strip()
    if email_lower not in OWNER_EMAILS:
        raise HTTPException(status_code=403, detail="Owner access only")

    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    sessions = (
        await db.sessions.find(
            {"last_active": {"$gte": cutoff.isoformat()}},
            {"_id": 0, "email": 1, "access_type": 1, "last_active": 1}
        )
        .sort("last_active", -1)
        .to_list(None)
    )

    results = []
    for s in sessions:
        user = await db.users.find_one(
            {"email": s.get("email")},
            {"_id": 0, "username": 1, "displayName": 1, "profileImage": 1}
        ) or {}
        results.append({
            "email": s.get("email"),
            "name": (
                user.get("username") or user.get("displayName")
                or s.get("email", "").split("@")[0]
            ),
            "profileImage": user.get("profileImage"),
            "accessType": s.get("access_type"),
            "lastActive": s.get("last_active"),
        })
    return results
