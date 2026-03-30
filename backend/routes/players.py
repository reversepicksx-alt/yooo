import asyncio as aio
import unicodedata
from fastapi import APIRouter

from config import CURRENT_SEASON
from models import PlayerSearchRequest
from utils import api_football_request

router = APIRouter(prefix="/api", tags=["players"])


@router.post("/players/search")
async def search_players(req: PlayerSearchRequest):
    if len(req.query) < 3:
        return {"players": []}
    season = req.season or CURRENT_SEASON
    query_lower = req.query.lower().strip()

    def extract_player(item):
        p = item.get("player", {})
        stats = item.get("statistics", [])
        team_id = stats[-1]["team"]["id"] if stats else 0
        team_name = stats[-1]["team"]["name"] if stats else ""
        firstname = p.get("firstname", "") or ""
        lastname = p.get("lastname", "") or ""
        display_name = f"{firstname} {lastname}".strip() if firstname and lastname else p.get("name", "")
        return {
            "id": p.get("id", 0),
            "name": display_name,
            "firstname": firstname,
            "lastname": lastname,
            "age": p.get("age", 0),
            "nationality": p.get("nationality", ""),
            "photo": "",
            "teamId": team_id,
            "teamName": team_name,
        }

    all_players = []

    # Strategy 1: Search within specified league
    if req.league_id:
        async def search_season(s):
            try:
                data = await api_football_request("players", {"search": req.query, "league": req.league_id, "season": s})
                return [(extract_player(item), s) for item in (data or [])]
            except Exception:
                return []

        results_by_season = await aio.gather(search_season(season + 1), search_season(season))
        season_data = {}
        for season_results in results_by_season:
            for player_data, found_season in season_results:
                pid = player_data["id"]
                if pid not in season_data or found_season > season_data[pid][1]:
                    season_data[pid] = (player_data, found_season)
        all_players = [v[0] for v in season_data.values()]

        if not all_players:
            for s in [season - 1, season - 2]:
                try:
                    data = await api_football_request("players", {"search": req.query, "league": req.league_id, "season": s})
                    if data:
                        all_players.extend([extract_player(item) for item in data])
                        break
                except Exception:
                    continue

        # Strategy 1b: last name fallback
        if not all_players and " " in req.query:
            last_name = req.query.strip().split()[-1]
            async def search_season_lastname(s):
                try:
                    data = await api_football_request("players", {"search": last_name, "league": req.league_id, "season": s})
                    return [(extract_player(item), s) for item in (data or [])]
                except Exception:
                    return []
            results_by_season = await aio.gather(search_season_lastname(season + 1), search_season_lastname(season))
            season_data = {}
            for season_results in results_by_season:
                for player_data, found_season in season_results:
                    pid = player_data["id"]
                    if pid not in season_data or found_season > season_data[pid][1]:
                        season_data[pid] = (player_data, found_season)
            all_players = [v[0] for v in season_data.values()]
            if not all_players:
                for s in [season - 1]:
                    try:
                        data = await api_football_request("players", {"search": last_name, "league": req.league_id, "season": s})
                        if data:
                            all_players.extend([extract_player(item) for item in data])
                            break
                    except Exception:
                        continue

    # Strategy 2: major domestic leagues
    if not all_players:
        major_leagues = [39, 140, 135, 78, 61, 253, 71, 307]
        async def try_league(lid):
            for s in [season + 1, season]:
                try:
                    data = await api_football_request("players", {"search": req.query, "league": lid, "season": s})
                    if data:
                        return [extract_player(item) for item in data]
                except Exception:
                    continue
            return []
        results = await aio.gather(*[try_league(lid) for lid in major_leagues])
        for r in results:
            all_players.extend(r)

    # Strategy 3: profiles
    if not all_players:
        try:
            data = await api_football_request("players/profiles", {"search": req.query})
            if data:
                all_players.extend([extract_player(item) for item in data])
        except Exception:
            pass

    # Strategy 4: last name from profiles
    if not all_players and " " in req.query:
        last_name = req.query.strip().split()[-1]
        try:
            data = await api_football_request("players/profiles", {"search": last_name})
            if data:
                all_players.extend([extract_player(item) for item in data])
        except Exception:
            pass

    # De-duplicate
    seen_ids = {}
    for p in all_players:
        pid = p["id"]
        if pid not in seen_ids:
            seen_ids[pid] = p
        elif p["teamName"] and not seen_ids[pid]["teamName"]:
            seen_ids[pid] = p
    players = list(seen_ids.values())

    # Sort
    def _strip(s):
        return ''.join(c for c in unicodedata.normalize('NFD', s) if unicodedata.category(c) != 'Mn')
    query_parts = [_strip(w.lower()) for w in req.query.strip().split()]
    def sort_key(p):
        has_team = 0 if p["teamName"] else 1
        name_norm = _strip(p["name"].lower())
        firstname_norm = _strip((p["firstname"] or "").lower())
        all_match = 0 if all(w in name_norm for w in query_parts) else 1
        first_match = 0 if query_parts and firstname_norm.startswith(query_parts[0]) else 1
        return (has_team, all_match, first_match, p["name"])
    players.sort(key=sort_key)
    return {"players": players[:15]}


@router.get("/player/{player_id}/stats")
async def get_player_stats(player_id: int, season: int = CURRENT_SEASON):
    for s in [season + 1, season, season - 1, season - 2]:
        try:
            data = await api_football_request("players", {"id": player_id, "season": s})
            if data:
                return {"stats": data[0]}
        except Exception:
            continue
    return {"stats": None}
