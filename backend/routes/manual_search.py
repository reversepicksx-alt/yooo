"""Manual search routes — fallback when scan doesn't work."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from config import db, CURRENT_SEASON
from utils import api_football_request, strip_accents

router = APIRouter(prefix="/api/manual", tags=["manual"])

# Leagues the user wants in manual search (ordered)
MANUAL_LEAGUES = [
    {"id": 254, "name": "NWSL"},
    {"id": 253, "name": "MLS"},
    {"id": 307, "name": "Saudi Pro League"},
    {"id": 128, "name": "Argentine Liga"},
    {"id": 40, "name": "Championship"},
    {"id": 140, "name": "La Liga"},
    {"id": 135, "name": "Serie A"},
    {"id": 61, "name": "Ligue 1"},
    {"id": 78, "name": "Bundesliga"},
    {"id": 39, "name": "Premier League"},
    {"id": 2, "name": "Champions League"},
    {"id": 3, "name": "Europa League"},
]


@router.get("/leagues")
async def get_leagues():
    return {"leagues": MANUAL_LEAGUES}


@router.get("/teams/{league_id}")
async def get_teams(league_id: int):
    """Return cached teams for a league, sorted alphabetically."""
    teams = await db.cache_teams.find(
        {"leagueId": league_id},
        {"_id": 0, "teamId": 1, "name": 1, "code": 1, "logo": 1}
    ).sort("name", 1).to_list(100)
    if not teams:
        return {"teams": [], "message": "No teams cached for this league."}
    return {"teams": teams}


class PlayerSearchRequest(BaseModel):
    team_id: int
    league_id: int
    player_name: str = ""


@router.post("/search-player")
async def search_player(req: PlayerSearchRequest):
    """Search for players on a specific team. If player_name given, filter by name."""
    season = CURRENT_SEASON
    try:
        # Get squad for this team
        data = await api_football_request("players/squads", {"team": req.team_id})
        if not data or not data[0].get("players"):
            raise ValueError("No squad data")

        squad = data[0]["players"]
        results = []
        search_lower = strip_accents(req.player_name.strip().lower()) if req.player_name else ""

        for p in squad:
            name = p.get("name", "")
            name_lower = strip_accents(name.lower())
            if search_lower and search_lower not in name_lower:
                continue
            results.append({
                "id": p.get("id"),
                "name": name,
                "position": p.get("position", ""),
                "number": p.get("number"),
                "photo": p.get("photo", ""),
            })

        # Sort: exact matches first, then alphabetically
        results.sort(key=lambda x: (0 if search_lower and strip_accents(x["name"].lower()).startswith(search_lower) else 1, x["name"]))
        return {"players": results[:30]}

    except Exception as e:
        print(f"[MANUAL SEARCH] Squad fetch failed: {e}, falling back to player search API")

    # Fallback: search API-Sports player search endpoint
    if not req.player_name:
        return {"players": [], "message": "Enter a player name to search."}

    try:
        search_clean = strip_accents(req.player_name.strip())
        data = await api_football_request("players", {
            "search": search_clean,
            "team": req.team_id,
            "season": season
        })
        results = []
        for item in (data or []):
            player = item.get("player", {})
            stats = item.get("statistics", [{}])
            pos = stats[0].get("games", {}).get("position", "") if stats else ""
            results.append({
                "id": player.get("id"),
                "name": player.get("name", ""),
                "position": pos,
                "number": stats[0].get("games", {}).get("number") if stats else None,
                "photo": player.get("photo", ""),
            })
        return {"players": results[:30]}
    except Exception as e:
        print(f"[MANUAL SEARCH] Player search failed: {e}")
        return {"players": [], "message": str(e)}
