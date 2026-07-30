import os
import httpx
import stripe
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from config import (
    db, OWNER_EMAIL, DYNAMIC_KEYS,
    get_dynamic_setting, set_dynamic_setting,
)
from models import AdminSettingsRequest, AdminTestKeyRequest
from pass_projection_calibration import walk_forward_validate

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _mask(val: str) -> str:
    if not val or len(val) < 8:
        return val or ""
    return val[:6] + "..." + val[-4:]


async def verify_owner(email: str, token: str):
    """Verify the request is from the owner with a valid session."""
    email_lower = email.lower().strip()
    if email_lower != OWNER_EMAIL:
        raise HTTPException(status_code=403, detail="Owner access required.")
    session = await db.sessions.find_one(
        {"email": email_lower, "session_token": token}, {"_id": 0}
    )
    if not session:
        raise HTTPException(status_code=401, detail="Invalid session.")
    return email_lower


@router.post("/settings")
async def get_settings(req: AdminSettingsRequest):
    """Get current admin settings (owner only). Values are masked."""
    await verify_owner(req.email, req.token)
    settings = {}
    for key in DYNAMIC_KEYS:
        val = get_dynamic_setting(key) or ""
        settings[key] = {
            "masked_value": _mask(val),
            "is_set": bool(val),
        }
    return {"settings": settings}


@router.post("/settings/update")
async def update_settings(req: AdminSettingsRequest):
    """Update admin settings (owner only)."""
    await verify_owner(req.email, req.token)
    if req.key not in DYNAMIC_KEYS:
        raise HTTPException(status_code=400, detail=f"Unsupported setting: {req.key}")
    val = req.value.strip()
    if not val or len(val) < 5:
        raise HTTPException(status_code=400, detail="Value too short.")
    await set_dynamic_setting(req.key, val)
    return {"success": True, "message": f"{req.key} updated. Changes are live immediately."}


@router.post("/test-key")
async def test_api_key(req: AdminTestKeyRequest):
    """Test if an API-Football key is valid (owner only)."""
    await verify_owner(req.email, req.token)
    key = req.api_key.strip()
    if not key or len(key) < 10:
        raise HTTPException(status_code=400, detail="Invalid key format.")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://v3.football.api-sports.io/status",
                headers={"x-apisports-key": key}
            )
            data = resp.json()
            account = data.get("response", {}).get("account", {})
            sub = data.get("response", {}).get("subscription", {})
            if account and sub:
                return {
                    "valid": True,
                    "account": account.get("firstname", "") + " " + account.get("lastname", ""),
                    "plan": sub.get("plan", "Unknown"),
                    "active": sub.get("active", False),
                }
            errors = data.get("errors", {})
            return {"valid": False, "error": str(errors) if errors else "Unknown error"}
    except Exception as e:
        return {"valid": False, "error": str(e)}


@router.get("/calibration")
async def get_calibration(email: str, token: str):
    """Owner-only: Get calibration stats from settled picks."""
    await verify_owner(email, token)

    from calibration import get_calibration_stats
    soccer_stats = await get_calibration_stats("soccer", force_refresh=True)

    def summarize(stats):
        if not stats:
            return None
        result = {
            "total": stats.get("total", 0),
            "overallHitRate": stats.get("overall_hit_rate", 0),
            "overHitRate": stats.get("over_hit_rate", 0),
            "underHitRate": stats.get("under_hit_rate", 0),
            "byProp": {},
            "byVenue": {},
            "byLeague": {},
            "byPosition": {},
            "byGameContext": {},
            "byPropPosition": {},
            "byPropContext": {},
            "byConfidence": {},
            "byLineRange": {},
            "byPropVenue": {},
            "blowoutMisses": len(stats.get("blowout_misses", [])),
            "blowoutDetails": stats.get("blowout_misses", [])[:10],
            "closeGameHitRate": 0,
        }
        for k, v in stats.get("by_prop", {}).items():
            h, m = v.get("hit", 0), v.get("miss", 0)
            t = h + m
            errs = v.get("errors", [])
            result["byProp"][k] = {
                "hits": h, "misses": m, "total": t,
                "rate": round(h/t*100, 1) if t else 0,
                "avgError": round(sum(errs)/len(errs), 1) if errs else 0,
            }
        for section, src_key in [
            ("byVenue", "by_venue"), ("byLeague", "by_league"),
            ("byPosition", "by_position"), ("byGameContext", "by_game_context"),
            ("byPropPosition", "by_prop_position"), ("byPropContext", "by_prop_context"),
            ("byConfidence", "by_confidence_band"), ("byLineRange", "by_line_range"),
            ("byPropVenue", "by_prop_venue"),
        ]:
            for k, v in stats.get(src_key, {}).items():
                h, m = v.get("hit", 0), v.get("miss", 0)
                t = h + m
                errs = v.get("errors", [])
                entry = {"hits": h, "misses": m, "total": t, "rate": round(h/t*100, 1) if t else 0}
                if errs:
                    entry["avgError"] = round(sum(errs)/len(errs), 1)
                result[section][k] = entry
        cg = stats.get("close_game_results", {})
        cg_h, cg_m = cg.get("hit", 0), cg.get("miss", 0)
        cg_t = cg_h + cg_m
        result["closeGameHitRate"] = round(cg_h/cg_t*100, 1) if cg_t else 0
        return result

    return {
        "soccer": summarize(soccer_stats),
    }


# ─── ADMIN: Generate a direct Stripe checkout link for any client ───────────

STRIPE_PLANS = {
    "weekly":    {"name": "Weekly",    "amount": 1100,  "interval": "week",  "interval_count": 1},
    "monthly":   {"name": "Monthly",   "amount": 3999,  "interval": "month", "interval_count": 1},
    "quarterly": {"name": "Quarterly", "amount": 9999,  "interval": "month", "interval_count": 3},
}


class CheckoutLinkRequest(BaseModel):
    adminEmail: str
    sessionToken: str
    clientEmail: str
    planKey: str = "monthly"


@router.post("/generate-checkout-link")
async def generate_checkout_link(req: CheckoutLinkRequest):
    """Generate a direct Stripe checkout URL for any client email. Owner-only."""
    await verify_owner(req.adminEmail, req.sessionToken)
    raise HTTPException(
        status_code=410,
        detail="Stripe is retired. New subscriptions must use Apple through the Reverse Picks app.",
    )

    plan_key = req.planKey.lower()
    if plan_key not in STRIPE_PLANS:
        raise HTTPException(status_code=400, detail=f"Unknown plan: {plan_key}")

    stripe_key = os.environ.get("STRIPE_SECRET_KEY", "")
    if not stripe_key:
        raise HTTPException(status_code=500, detail="Stripe not configured.")
    stripe.api_key = stripe_key

    plan = STRIPE_PLANS[plan_key]
    client_email = req.clientEmail.lower().strip()

    try:
        prices = stripe.Price.list(
            lookup_keys=[f"reversepicks_{plan_key}"],
            expand=["data.product"],
        )
        if prices.data:
            price_id = prices.data[0].id
        else:
            price = stripe.Price.create(
                unit_amount=plan["amount"],
                currency="usd",
                recurring={"interval": plan["interval"], "interval_count": plan["interval_count"]},
                product_data={"name": f"Reverse Picks {plan['name']}"},
                lookup_key=f"reversepicks_{plan_key}",
            )
            price_id = price.id

        session = stripe.checkout.Session.create(
            mode="subscription",
            customer_email=client_email,
            line_items=[{"price": price_id, "quantity": 1}],
            success_url="https://reversepicks.com/auth?stripe_success=1",
            cancel_url="https://reversepicks.com/auth",
            subscription_data={
                "metadata": {"email": client_email, "plan_key": plan_key}
            },
            metadata={"email": client_email, "plan_key": plan_key},
            allow_promotion_codes=True,
        )
        return {
            "checkoutUrl": session.url,
            "clientEmail": client_email,
            "planKey": plan_key,
            "expiresIn": "24 hours",
        }
    except stripe.StripeError as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─── ADMIN: Manually grant / revoke access for any email ────────────────────

class GrantAccessRequest(BaseModel):
    adminEmail: str
    sessionToken: str
    targetEmail: str
    accessType: str = "Manual"
    note: str = ""
    durationDays: int = 0  # 0 = unlimited


class RevokeAccessRequest(BaseModel):
    adminEmail: str
    sessionToken: str
    targetEmail: str


@router.post("/grant-access")
async def grant_access(req: GrantAccessRequest):
    """
    Instantly grant access to any email.
    Writes a manual_access_grants record so the user can log in immediately.
    """
    await verify_owner(req.adminEmail, req.sessionToken)
    target = req.targetEmail.lower().strip()
    if not target:
        raise HTTPException(status_code=400, detail="targetEmail is required.")

    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    doc = {
        "email": target,
        "access_type": req.accessType or "Manual",
        "grantedAt": now.isoformat(),
        "grantedBy": req.adminEmail.lower().strip(),
        "note": req.note or "",
    }
    if req.durationDays and req.durationDays > 0:
        doc["expiresAt"] = (now + timedelta(days=req.durationDays)).isoformat()

    await db.manual_access_grants.update_one(
        {"email": target},
        {"$set": doc},
        upsert=True,
    )
    return {"success": True, "email": target, "accessType": doc["access_type"], "message": f"Access granted to {target}."}


@router.post("/revoke-access")
async def revoke_access(req: RevokeAccessRequest):
    """Remove manual access grant for a user (owner only)."""
    await verify_owner(req.adminEmail, req.sessionToken)
    target = req.targetEmail.lower().strip()
    await db.manual_access_grants.delete_one({"email": target})
    await db.sessions.delete_many({"email": target})
    return {"success": True, "email": target, "message": f"Access revoked for {target}."}


@router.post("/list-grants")
async def list_grants(req: AdminSettingsRequest):
    """List all manual access grants (owner only)."""
    await verify_owner(req.email, req.token)
    grants = await db.manual_access_grants.find({}, {"_id": 0}).sort("grantedAt", -1).to_list(None)
    return {"grants": grants}


class _ScenarioPriorsRequest(BaseModel):
    email: str
    token: str


@router.post("/scenario-priors")
async def scenario_priors_inspector(req: _ScenarioPriorsRequest):
    """Inspect the scenario_priors cache (owner only).

    Returns the loaded buckets, sample sizes, hit rates, and bias for
    every (scenario × position × prop × side) cell that has crossed the
    minimum sample threshold.
    """
    await verify_owner(req.email, req.token)
    from scenario_priors import ensure_loaded as _ensure_scen, stats as _scen_stats
    await _ensure_scen(db)
    return {"mode": os.environ.get("SCENARIO_PRIORS_MODE", "shadow"),
            **_scen_stats()}


@router.post("/scenario-priors/refresh")
async def scenario_priors_refresh(req: _ScenarioPriorsRequest):
    """Force-refresh the scenario_priors cache (owner only)."""
    await verify_owner(req.email, req.token)
    from scenario_priors import _refresh as _refresh_scen, stats as _scen_stats
    await _refresh_scen(db)
    return {"success": True, "stats": _scen_stats()}


@router.post("/odds-tier-priors")
async def odds_tier_priors_inspector(req: _ScenarioPriorsRequest):
    """Inspect the odds-tier priors cache (owner only)."""
    await verify_owner(req.email, req.token)
    from odds_tier_priors import ensure_loaded as _ensure_ot, stats as _ot_stats
    await _ensure_ot(db)
    return {"mode": os.environ.get("ODDS_TIER_PRIORS_MODE", "shadow"),
            **_ot_stats()}


@router.post("/odds-tier-priors/refresh")
async def odds_tier_priors_refresh(req: _ScenarioPriorsRequest):
    """Force-refresh the odds-tier priors cache (owner only)."""
    await verify_owner(req.email, req.token)
    from odds_tier_priors import _refresh as _refresh_ot, stats as _ot_stats
    await _refresh_ot(db)
    return {"success": True, "stats": _ot_stats()}


# ── Picks Audit ────────────────────────────────────────────────────────────────

class _PicksAuditRequest(BaseModel):
    email: str
    token: str


@router.post("/picks-audit")
async def picks_audit(req: _PicksAuditRequest):
    """Full diagnostic snapshot of every pick in the DB (owner only).

    Returns:
      • matrix     — counts by sport × status × result
      • wrong_push — picks settled as push but actualValue ≠ line (engine bug detector)
      • stale      — pending/live picks by age bucket (>8 h, >24 h, >7 d)
      • prop_rates — hit rate per propType+direction from settled picks (n≥5)
      • recent_voids — last 20 stale-void picks
      • totals     — aggregate hit/miss/push/pending counts
    """
    from datetime import datetime, timezone, timedelta
    await verify_owner(req.email, req.token)

    now = datetime.now(timezone.utc)

    # ── 1. Full matrix: sport × status × result ────────────────────────────
    matrix_raw = await db.picks.aggregate([
        {"$group": {
            "_id": {
                "sport":  {"$ifNull": ["$sport",  "unknown"]},
                "status": {"$ifNull": ["$status", "unknown"]},
                "result": {"$ifNull": ["$result", "—"]},
            },
            "count": {"$sum": 1},
        }},
        {"$sort": {"_id.sport": 1, "_id.status": 1, "_id.result": 1}},
    ]).to_list(None)

    matrix = {}
    for row in matrix_raw:
        sport  = row["_id"]["sport"]
        status = row["_id"]["status"]
        result = row["_id"]["result"]
        matrix.setdefault(sport, {}).setdefault(status, {})[result] = row["count"]

    # ── 2. Wrong-push: settled=push but actualValue present and ≠ line ─────
    # Only flag as wrong-push if there is NO voidReason — picks voided for DNP,
    # <30 min played, no-data sentinels, etc. are legitimate pushes.
    wrong_pushes_raw = await db.picks.find(
        {"status": "settled", "result": "push",
         "actualValue": {"$exists": True, "$ne": None},
         "line":        {"$exists": True, "$nin": [None, 0]},
         "voidReason":  {"$exists": False}},
        {"_id": 0, "pickId": 1, "playerName": 1, "propType": 1, "sport": 1,
         "line": 1, "actualValue": 1, "recommendation": 1,
         "settledAt": 1, "settledBy": 1}
    ).sort("settledAt", -1).to_list(200)

    wrong_pushes = []
    for p in wrong_pushes_raw:
        try:
            diff = abs(float(p.get("actualValue", 0)) - float(p.get("line", 0)))
            if diff >= 0.01:
                wrong_pushes.append({
                    "pickId":      p.get("pickId"),
                    "player":      p.get("playerName"),
                    "prop":        p.get("propType"),
                    "sport":       p.get("sport"),
                    "line":        p.get("line"),
                    "actual":      p.get("actualValue"),
                    "diff":        round(diff, 3),
                    "rec":         p.get("recommendation"),
                    "settledAt":   p.get("settledAt"),
                    "settledBy":   p.get("settledBy"),
                })
        except (TypeError, ValueError):
            pass

    # ── 3. Stale pending/live picks ────────────────────────────────────────
    cutoff_8h  = (now - timedelta(hours=8)).isoformat()
    cutoff_24h = (now - timedelta(hours=24)).isoformat()
    cutoff_7d  = (now - timedelta(days=7)).isoformat()

    stale_pipeline = [
        {"$match": {"status": {"$in": ["pending", "live"]}}},
        {"$addFields": {"ts": {"$ifNull": ["$timestamp", "$createdAt"]}}},
        {"$match": {"ts": {"$lt": cutoff_8h}}},
        {"$group": {
            "_id": {"$ifNull": ["$sport", "unknown"]},
            "over_8h":  {"$sum": 1},
            "over_24h": {"$sum": {"$cond": [{"$lt": ["$ts", cutoff_24h]}, 1, 0]}},
            "over_7d":  {"$sum": {"$cond": [{"$lt": ["$ts", cutoff_7d]},  1, 0]}},
            "oldest":   {"$min": "$ts"},
        }},
        {"$sort": {"_id": 1}},
    ]
    stale_raw  = await db.picks.aggregate(stale_pipeline).to_list(None)
    stale_by_sport = {
        r["_id"]: {
            "over_8h":  r["over_8h"],
            "over_24h": r["over_24h"],
            "over_7d":  r["over_7d"],
            "oldest":   r.get("oldest", ""),
        }
        for r in stale_raw
    }

    # ── 4. Prop hit rates (settled picks, n≥5, exclude push) ──────────────
    prop_pipeline = [
        {"$match": {"status": "settled", "result": {"$in": ["hit", "miss"]}}},
        {"$group": {
            "_id": {
                "prop": "$propType",
                "dir":  {"$toUpper": "$recommendation"},
            },
            "hits":  {"$sum": {"$cond": [{"$eq": ["$result", "hit"]}, 1, 0]}},
            "total": {"$sum": 1},
        }},
        {"$match": {"total": {"$gte": 5}}},
        {"$addFields": {"hitRate": {"$round": [
            {"$multiply": [{"$divide": ["$hits", "$total"]}, 100]}, 1
        ]}}},
        {"$sort": {"hitRate": 1}},
    ]
    prop_raw   = await db.picks.aggregate(prop_pipeline).to_list(None)
    prop_rates = [
        {
            "prop":    r["_id"]["prop"],
            "dir":     r["_id"]["dir"],
            "hitRate": r["hitRate"],
            "hits":    r["hits"],
            "total":   r["total"],
            "flag":    ("🚫 AVOID" if r["hitRate"] < 30 else
                        "⚠️ RISKY" if r["hitRate"] < 45 else
                        "✅ GOOD"  if r["hitRate"] >= 55 else ""),
        }
        for r in prop_raw
    ]

    # ── 5. Recent stale-voids ──────────────────────────────────────────────
    recent_voids = await db.picks.find(
        {"settledBy": "stale_void"},
        {"_id": 0, "playerName": 1, "propType": 1, "sport": 1,
         "settledAt": 1, "voidReason": 1}
    ).sort("settledAt", -1).limit(20).to_list(20)

    # ── 6. Totals ──────────────────────────────────────────────────────────
    totals = {
        "hit":     await db.picks.count_documents({"status": "settled", "result": "hit"}),
        "miss":    await db.picks.count_documents({"status": "settled", "result": "miss"}),
        "push":    await db.picks.count_documents({"status": "settled", "result": "push"}),
        "pending": await db.picks.count_documents({"status": {"$in": ["pending", "live"]}}),
        "total":   await db.picks.count_documents({}),
    }
    settled = totals["hit"] + totals["miss"]
    totals["overall_hit_rate"] = (
        round(totals["hit"] / settled * 100, 1) if settled > 0 else None
    )
    totals["wrong_push_count"] = len(wrong_pushes)

    return {
        "generatedAt":  now.isoformat(),
        "totals":       totals,
        "matrix":       matrix,
        "wrong_pushes": wrong_pushes,
        "stale":        stale_by_sport,
        "prop_rates":   prop_rates,
        "recent_voids": recent_voids,
    }


# ── Position Cache Invalidation ────────────────────────────────────────────────

class _PositionInvalidateRequest(BaseModel):
    email: str
    token: str
    playerIds: list[int] = []


@router.post("/positions/invalidate")
async def invalidate_position_cache(req: _PositionInvalidateRequest):
    """Force re-resolution of position cache for specific players (or all).

    Sets promptVersion=0 on targeted entries so the next predict call
    re-resolves their position via Gemini.

    - Pass a non-empty `playerIds` list to target specific players.
    - Pass an empty list (or omit the field) to reset ALL cached positions.

    Owner-only.
    """
    await verify_owner(req.email, req.token)

    if req.playerIds:
        filter_query = {"playerId": {"$in": req.playerIds}}
        result = await db.player_positions.update_many(
            filter_query,
            {"$set": {"promptVersion": 0}},
        )
        return {
            "success": True,
            "scope": "targeted",
            "playerIds": req.playerIds,
            "matched": result.matched_count,
            "modified": result.modified_count,
        }
    else:
        result = await db.player_positions.update_many(
            {},
            {"$set": {"promptVersion": 0}},
        )
        return {
            "success": True,
            "scope": "all",
            "matched": result.matched_count,
            "modified": result.modified_count,
        }


class _PositionClearRequest(BaseModel):
    email: str
    token: str
    playerName: str = ""
    playerId: int | None = None


@router.post("/positions/clear-player")
async def clear_player_position(req: _PositionClearRequest):
    """Delete position cache for a specific player by name and/or playerId.

    Use this to fix a known-wrong position (e.g. Vitinha cached as CB).
    On next predict the engine will re-resolve with the full stats-aware prompt.
    Owner-only.
    """
    await verify_owner(req.email, req.token)
    if not req.playerName and not req.playerId:
        raise HTTPException(status_code=400, detail="Provide playerName or playerId")

    deleted = 0
    if req.playerId:
        r = await db.player_positions.delete_many({"playerId": req.playerId})
        deleted += r.deleted_count
    if req.playerName:
        r = await db.player_positions.delete_many({"playerName": req.playerName})
        deleted += r.deleted_count

    return {
        "success": True,
        "deleted": deleted,
        "playerName": req.playerName,
        "playerId": req.playerId,
        "note": "Position will be re-resolved with stats-aware AI on next predict call.",
    }


_OWNER_ACCESS_CODE = os.environ.get("OWNER_ACCESS_CODE", "").strip()

async def _verify_owner_or_code(email: str, token: str):
    """Accept either a live session token OR the OWNER_ACCESS_CODE env secret."""
    email_lower = email.lower().strip()
    if email_lower not in (OWNER_EMAIL, *[e for e in [os.environ.get("OWNER_EMAIL2","")]  if e]):
        if email_lower != OWNER_EMAIL:
            raise HTTPException(status_code=403, detail="Owner access required.")
    # Fast path: direct access code match (no DB lookup needed)
    if _OWNER_ACCESS_CODE and token == _OWNER_ACCESS_CODE:
        return email_lower
    # Slow path: session token
    session = await db.sessions.find_one(
        {"email": email_lower, "session_token": token}, {"_id": 0}
    )
    if not session:
        raise HTTPException(status_code=401, detail="Invalid session or access code.")
    return email_lower


class UnsettlePickRequest(BaseModel):
    email: str
    token: str
    pickIds: list = []             # reset by exact pickId
    playerNames: list = []         # reset by player name (partial match, last 48h)


@router.post("/unsettle-picks")
async def admin_unsettle_picks(req: UnsettlePickRequest):
    """Owner only: reset wrongly-settled picks back to 'live' so auto-settlement
    can re-settle them correctly once the match finishes (FT).
    Accepts either a session token or the OWNER_ACCESS_CODE directly."""
    await _verify_owner_or_code(req.email, req.token)
    from datetime import datetime, timezone, timedelta
    updated = []
    _unset_fields = {"$set": {"status": "live", "result": None, "actualValue": None, "hitPct": None},
                     "$unset": {"settledAt": "", "settledBy": "", "voidReason": ""}}

    # By pickId
    for pid in (req.pickIds or []):
        res = await db.picks.update_one({"pickId": pid}, _unset_fields)
        if res.matched_count:
            updated.append(pid)

    # By player name (partial, case-insensitive, settled in last 48h)
    if req.playerNames:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        for name in req.playerNames:
            import re as _re
            pat = _re.compile(_re.escape(name.strip()), _re.IGNORECASE)
            picks = await db.picks.find(
                {"playerName": {"$regex": pat}, "status": "settled",
                 "settledAt": {"$gt": cutoff}},
                {"_id": 0, "pickId": 1, "playerName": 1}
            ).to_list(20)
            for p in picks:
                res = await db.picks.update_one({"pickId": p["pickId"]}, _unset_fields)
                if res.matched_count:
                    updated.append(f"{p['pickId']}({p['playerName']})")

    return {"success": True, "reset": updated, "count": len(updated)}


class RefreshPlayerRequest(BaseModel):
    email: str
    token: str
    player_id: int


@router.post("/refresh-player")
async def admin_refresh_player(req: RefreshPlayerRequest):
    """Force re-sync a player's cache_players entry from API-Football (owner only).

    Useful for recently transferred players whose cache entry still shows the old team.
    Fetches the player's latest season stats and updates teamId/teamName/leagueId in-place.
    """
    await verify_owner(req.email, req.token)
    from cache import refresh_player_cache
    result = await refresh_player_cache(req.player_id)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return {"success": True, **result}


class ClearSportCacheRequest(BaseModel):
    email: str
    token: str
    sport: str = "all"   # "wta" | "cs2" | "all"


class RegradeDnpRequest(BaseModel):
    email: str
    token: str
    dry_run: bool = True  # safe default: preview without writing


@router.post("/regrade-dnp-picks")
async def admin_regrade_dnp_picks(req: RegradeDnpRequest):
    """Owner only: find picks that were incorrectly settled as DNP/push because
    the API reported minutes=None/0 (common in NWSL and some other leagues) even
    though the player clearly played (actualValue > 0).  Re-computes the correct
    result from actualValue vs line and writes it back unless dry_run=True.

    Safe to run multiple times — only touches picks with voidReason containing
    'min (min' (our DNP pattern) AND actualValue > 0.
    """
    await verify_owner(req.email, req.token)
    from datetime import datetime, timezone

    def _settle_result(actual, line, rec):
        rec = (rec or "").lower()
        if actual == line:
            return "push"
        elif (actual > line and rec == "over") or (actual < line and rec == "under"):
            return "hit"
        else:
            return "miss"

    candidates = await db.picks.find(
        {
            "status": "settled",
            "voidReason": {"$regex": "min \\(min"},  # matches our "X min (min 30 required)" pattern
            "actualValue": {"$gt": 0},               # player had real stats
        },
        {"_id": 0}
    ).to_list(500)

    regraded = []
    for pick in candidates:
        actual   = pick.get("actualValue", 0)
        line     = pick.get("line", 0)
        rec      = pick.get("recommendation", "over")
        result   = _settle_result(actual, line, rec)
        hit_pct  = 100 if result == "hit" else (0 if result == "miss" else 50)
        regraded.append({
            "pickId":      pick.get("pickId"),
            "playerName":  pick.get("playerName"),
            "propType":    pick.get("propType"),
            "line":        line,
            "actual":      actual,
            "rec":         rec,
            "old_result":  pick.get("result"),
            "new_result":  result,
        })
        if not req.dry_run:
            await db.picks.update_one(
                {"pickId": pick["pickId"]},
                {"$set": {
                    "result":    result,
                    "hitPct":    hit_pct,
                    "settledBy": "admin_regrade_dnp",
                    "regradedAt": datetime.now(timezone.utc).isoformat(),
                },
                 "$unset": {"voidReason": ""}},
            )

    return {
        "success":   True,
        "dry_run":   req.dry_run,
        "count":     len(regraded),
        "regraded":  regraded,
        "message":   ("DRY RUN — no changes written. Set dry_run=false to apply."
                      if req.dry_run else f"Re-graded {len(regraded)} picks."),
    }


@router.post("/clear-sport-cache")
async def admin_clear_sport_cache(req: ClearSportCacheRequest):
    """
    Drop all cached BDL API responses for WTA and/or CS2 so the next
    prediction or search fetches fresh GOAT-tier data from BallDontLie.
    Use after upgrading the BDL subscription plan.
    """
    await verify_owner(req.email, req.token)
    sport = req.sport.lower().strip()
    results = {}

    if sport in ("wta", "all"):
        r = await db.wta_cache.delete_many({})
        results["wta_cache"] = r.deleted_count

    if sport in ("cs2", "all"):
        r = await db.cs2_cache.delete_many({})
        results["cs2_cache"] = r.deleted_count

    total = sum(results.values())
    return {
        "success": True,
        "sport": sport,
        "deleted": results,
        "total": total,
        "message": f"Cleared {total} cached docs — next request will fetch fresh GOAT-tier data.",
    }


class PassCalValidateRequest(BaseModel):
    email: str
    token: str


@router.post("/pass-calibration-validate")
async def admin_pass_calibration_validate(req: PassCalValidateRequest):
    """
    Run the walk-forward pass-projection calibration evaluator against all
    eligible settled soccer pass picks in the production database.

    Returns raw vs calibrated MAE, signed bias, direction hit rate, sample
    counts, and a leakage-violation count.  The mode field confirms whether
    live calibration is active.  PASS_PROJECTION_CALIBRATION_MODE must be
    'live' in the environment for corrections to be applied to user-facing
    projections; it remains 'shadow' by default.

    Eligible picks: soccer, propType in {pass_attempts, passes}, result in
    {hit, miss}, recommendation in {over, under}, actualValue and
    projectedValue both set, no voidReason, correctedManually != true.
    """
    await verify_owner(req.email, req.token)

    cursor = db.picks.find(
        {
            "sport": "soccer",
            "propType": {"$in": ["pass_attempts", "passes"]},
            "result": {"$in": ["hit", "miss"]},
            "recommendation": {"$in": ["over", "under"]},
            "actualValue": {"$ne": None},
            "projectedValue": {"$ne": None},
            "settledAt": {"$ne": None},
            "voidReason": {"$exists": False},
            "correctedManually": {"$ne": True},
        },
        {
            "_id": 0,
            "playerName": 1, "playerNameKey": 1, "fixtureId": 1,
            "fixtureDate": 1, "matchDate": 1, "opponentName": 1, "opponent": 1,
            "propType": 1, "line": 1, "recommendation": 1, "leagueId": 1,
            "position": 1, "role": 1, "actualValue": 1, "projectedValue": 1,
            "settledAt": 1, "sport": 1, "result": 1,
        },
    )
    rows = await cursor.to_list(length=50000)

    report = walk_forward_validate(rows)

    mode = os.environ.get("PASS_PROJECTION_CALIBRATION_MODE", "shadow").lower()
    if mode not in {"off", "shadow", "live"}:
        mode = "shadow"

    # Derive a plain-English recommendation.
    calibrated_mae = report["calibrated"]["mae"]
    raw_mae = report["raw"]["mae"]
    raw_bias = report["raw"]["signedBias"]
    calibrated_bias = report["calibrated"]["signedBias"]
    raw_dhr = report["raw"]["directionHitRate"]
    cal_dhr = report["calibrated"]["directionHitRate"]
    leakage = report["leakageViolations"]
    n = report["evaluatedSamples"]

    observations = []
    if leakage > 0:
        observations.append(f"⚠️  {leakage} leakage violation(s) — investigate before trusting these results.")
    else:
        observations.append("✅ Zero leakage violations.")

    if n < 30:
        observations.append(f"⚠️  Only {n} evaluated samples — too few for reliable out-of-sample conclusions.")
    else:
        observations.append(f"ℹ️  {n} evaluated samples ({report['calibratedSamples']} received a calibration correction).")

    if calibrated_mae is not None and raw_mae is not None:
        if calibrated_mae < raw_mae:
            pct = round((raw_mae - calibrated_mae) / raw_mae * 100, 1)
            observations.append(f"✅ Calibrated MAE ({calibrated_mae}) is {pct}% lower than raw MAE ({raw_mae}).")
        else:
            observations.append(f"⚠️  Calibrated MAE ({calibrated_mae}) is NOT lower than raw MAE ({raw_mae}).")

    if calibrated_bias is not None and raw_bias is not None:
        if abs(calibrated_bias) < abs(raw_bias):
            observations.append(f"✅ Calibrated signed bias ({calibrated_bias}) is closer to zero than raw ({raw_bias}).")
        else:
            observations.append(f"⚠️  Calibrated signed bias ({calibrated_bias}) is NOT closer to zero than raw ({raw_bias}).")

    if raw_dhr is not None and cal_dhr is not None:
        dhr_drop = round((raw_dhr - cal_dhr) * 100, 1) if cal_dhr < raw_dhr else 0
        if dhr_drop > 3:
            observations.append(
                f"⚠️  Direction hit rate dropped by {dhr_drop}pp after calibration ({raw_dhr} → {cal_dhr}). "
                "Material deterioration — do NOT enable live mode."
            )
        elif cal_dhr >= raw_dhr:
            observations.append(f"✅ Direction hit rate held or improved ({raw_dhr} → {cal_dhr}).")
        else:
            observations.append(f"ℹ️  Direction hit rate change: {raw_dhr} → {cal_dhr} ({dhr_drop}pp drop — within tolerance).")

    safe_to_enable = (
        leakage == 0
        and n >= 30
        and calibrated_mae is not None
        and raw_mae is not None
        and calibrated_mae < raw_mae
        and (raw_dhr is None or cal_dhr is None or (raw_dhr - cal_dhr) <= 0.03)
    )
    if safe_to_enable:
        observations.append(
            "✅ All criteria met — calibration may be promoted to live mode by setting "
            "PASS_PROJECTION_CALIBRATION_MODE=live in the environment."
        )
    else:
        observations.append(
            f"🔒 PASS_PROJECTION_CALIBRATION_MODE remains '{mode}' — criteria not yet met for live promotion."
        )

    return {
        "success": True,
        "mode": mode,
        "report": report,
        "observations": observations,
        "safeToEnable": safe_to_enable,
    }
