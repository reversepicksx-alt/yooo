from datetime import datetime, timezone
from fastapi import APIRouter

from config import SUPPORTED_LEAGUES, CURRENT_SEASON, db
from utils import api_football_request
from cache import seed_cache, fetch_national_teams

router = APIRouter(prefix="/api", tags=["leagues"])


@router.get("/health")
async def health():
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


@router.get("/leagues")
async def get_leagues():
    return {"leagues": SUPPORTED_LEAGUES}


@router.get("/leagues/{league_id}/teams")
async def get_teams_by_league(league_id: int, season: int = CURRENT_SEASON):
    seasons_to_try = [season + 1, season, season - 1, season - 2, season - 3]
    for s in seasons_to_try:
        try:
            data = await api_football_request("teams", {"league": league_id, "season": s})
            if data:
                teams = [{"id": item["team"]["id"], "name": item["team"]["name"], "logo": item["team"].get("logo", "")} for item in data]
                return {"teams": teams}
        except Exception:
            continue
    return {"teams": []}


@router.get("/football/status")
async def football_status():
    try:
        data = await api_football_request("status")
        return {"status": "online", "data": data}
    except Exception:
        return {"status": "offline"}


@router.post("/cache/refresh")
async def refresh_cache():
    """Force refresh the API-Football lookup cache."""
    await db["api_cache"].delete_many({})
    import asyncio
    asyncio.create_task(seed_cache())
    return {"status": "ok", "message": "Cache refresh started"}


@router.get("/cache/national-teams")
async def get_cached_national_teams():
    """View all cached national team IDs."""
    lookup = await fetch_national_teams()
    teams = sorted(
        [{"name": v["name"], "id": v["id"]} for v in {v["id"]: v for v in lookup.values()}.values()],
        key=lambda x: x["name"]
    )
    return {"count": len(teams), "teams": teams}
