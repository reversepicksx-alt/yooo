import httpx
from fastapi import APIRouter, HTTPException
from config import (
    db, OWNER_EMAIL, DYNAMIC_KEYS,
    get_dynamic_setting, set_dynamic_setting,
)
from models import AdminSettingsRequest, AdminTestKeyRequest

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
    if req.key == "SQUARE_ENVIRONMENT":
        if val not in ("sandbox", "production"):
            raise HTTPException(status_code=400, detail="Must be 'sandbox' or 'production'.")
    elif not val or len(val) < 5:
        raise HTTPException(status_code=400, detail="Value too short.")
    await set_dynamic_setting(req.key, val)
    # Clear cached Square plans when Square keys change so they get recreated
    if req.key.startswith("SQUARE_"):
        await db.square_plans.delete_many({})
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


@router.get("/square-config")
async def get_square_config():
    """Public endpoint: returns Square App ID + Location ID for the payment form."""
    return {
        "appId": get_dynamic_setting("SQUARE_APPLICATION_ID"),
        "locationId": get_dynamic_setting("SQUARE_LOCATION_ID"),
    }


@router.get("/calibration")
async def get_calibration(email: str, token: str):
    """Owner-only: Get calibration stats from settled picks."""
    await verify_owner(email, token)

    from calibration import get_calibration_stats
    soccer_stats = await get_calibration_stats("soccer", force_refresh=True)
    basketball_stats = await get_calibration_stats("basketball", force_refresh=True)

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
        "basketball": summarize(basketball_stats),
    }
