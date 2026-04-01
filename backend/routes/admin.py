import httpx
from fastapi import APIRouter, HTTPException
from config import db, OWNER_EMAIL, get_dynamic_api_key, set_dynamic_api_key
from models import AdminSettingsRequest, AdminTestKeyRequest

router = APIRouter(prefix="/api/admin", tags=["admin"])


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
    """Get current admin settings (owner only). Key value is masked."""
    await verify_owner(req.email, req.token)
    current_key = get_dynamic_api_key() or ""
    masked = current_key[:8] + "..." + current_key[-4:] if len(current_key) > 12 else current_key
    return {
        "settings": {
            "API_FOOTBALL_KEY": {
                "masked_value": masked,
                "is_set": bool(current_key),
            }
        }
    }


@router.post("/settings/update")
async def update_settings(req: AdminSettingsRequest):
    """Update admin settings (owner only)."""
    await verify_owner(req.email, req.token)
    if req.key != "API_FOOTBALL_KEY":
        raise HTTPException(status_code=400, detail="Unsupported setting key.")
    if not req.value or len(req.value.strip()) < 10:
        raise HTTPException(status_code=400, detail="Invalid API key value.")
    await set_dynamic_api_key(req.value.strip())
    return {"success": True, "message": "API key updated successfully. Changes are live immediately."}


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
