from datetime import datetime, timezone
from fastapi import APIRouter

from config import SUPPORTED_LEAGUES, CURRENT_SEASON, db
from utils import api_football_request
from cache import (
    full_sync, detect_transfers, get_cache_status,
    get_national_team_id, get_team_by_name, get_player_by_name,
    COL_LEAGUES, COL_TEAMS, COL_PLAYERS, COL_NATIONAL, COL_TRANSFERS,
)

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


# ══════════════════════════════════════════════
#  CACHE MANAGEMENT ENDPOINTS
# ══════════════════════════════════════════════

@router.get("/cache/status")
async def cache_status():
    """Full overview of what's cached."""
    return await get_cache_status()


@router.post("/cache/refresh")
async def refresh_cache():
    """Force a full re-sync of all data (leagues, teams, players, national teams)."""
    import asyncio
    asyncio.create_task(full_sync(force=True))
    return {"status": "ok", "message": "Full sync started in background. Check /api/cache/status for progress."}


@router.post("/cache/detect-transfers")
async def run_transfer_detection():
    """Compare current squads with cached data to find transfers."""
    import asyncio
    asyncio.create_task(detect_transfers())
    return {"status": "ok", "message": "Transfer detection started. Check /api/cache/transfers for results."}


@router.get("/cache/national-teams")
async def get_cached_national_teams():
    """All cached national team IDs."""
    teams = []
    seen = set()
    async for doc in db[COL_NATIONAL].find({}, {"_id": 0}):
        tid = doc["teamId"]
        if tid not in seen:
            teams.append({"id": tid, "name": doc["name"]})
            seen.add(tid)
    teams.sort(key=lambda x: x["name"])
    return {"count": len(teams), "teams": teams}


@router.get("/cache/leagues")
async def get_cached_leagues():
    """All cached leagues from API-Football."""
    leagues = []
    async for doc in db[COL_LEAGUES].find({}, {"_id": 0}).sort("name", 1):
        leagues.append({
            "id": doc.get("leagueId"),
            "name": doc.get("name"),
            "country": doc.get("country"),
            "type": doc.get("type"),
            "currentSeason": doc.get("currentSeason"),
        })
    return {"count": len(leagues), "leagues": leagues}


@router.get("/cache/teams")
async def get_cached_teams(league_id: int = None):
    """All cached teams, optionally filtered by league."""
    query = {}
    if league_id:
        query["leagueId"] = league_id
    teams = []
    async for doc in db[COL_TEAMS].find(query, {"_id": 0}).sort("name", 1):
        teams.append({
            "id": doc.get("teamId"),
            "name": doc.get("name"),
            "country": doc.get("country"),
            "leagueId": doc.get("leagueId"),
            "national": doc.get("national", False),
        })
    return {"count": len(teams), "teams": teams}


@router.get("/cache/players")
async def get_cached_players(team_id: int = None, search: str = None):
    """Cached players, filtered by team or name search."""
    query = {}
    if team_id:
        query["teamId"] = team_id
    if search:
        query["nameLower"] = {"$regex": search.lower().strip()}

    players = []
    async for doc in db[COL_PLAYERS].find(query, {"_id": 0}).sort("name", 1).limit(100):
        players.append({
            "id": doc.get("playerId"),
            "name": doc.get("name"),
            "position": doc.get("position"),
            "teamId": doc.get("teamId"),
            "teamName": doc.get("teamName"),
            "number": doc.get("number"),
        })
    return {"count": len(players), "players": players}


@router.get("/cache/transfers")
async def get_cached_transfers(limit: int = 50):
    """Recently detected transfers."""
    transfers = []
    async for doc in db[COL_TRANSFERS].find({}, {"_id": 0}).sort("detectedAt", -1).limit(limit):
        transfers.append(doc)
    return {"count": len(transfers), "transfers": transfers}


@router.get("/cache/lookup/team")
async def lookup_team(name: str, league_id: int = None):
    """Look up any team by name (club or national)."""
    # Try national first
    nat_id, nat_name = await get_national_team_id(name)
    if nat_id:
        return {"found": True, "type": "national", "teamId": nat_id, "name": nat_name}

    # Try club
    club_id, club_name = await get_team_by_name(name, league_id)
    if club_id:
        return {"found": True, "type": "club", "teamId": club_id, "name": club_name}

    return {"found": False, "query": name}


@router.get("/cache/lookup/player")
async def lookup_player(name: str, team_id: int = None):
    """Look up a player by name, optionally filtered by team."""
    player = await get_player_by_name(name, team_id)
    if player:
        return {"found": True, "player": player}
    return {"found": False, "query": name}
