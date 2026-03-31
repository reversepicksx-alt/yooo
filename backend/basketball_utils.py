"""
Basketball API utilities — completely separate from soccer.
Base URL: https://v1.basketball.api-sports.io/
Individual player stats per game available via games/statistics/players
"""
import aiohttp
import asyncio as aio
from config import API_FOOTBALL_KEY

BASKETBALL_API_BASE = "https://v1.basketball.api-sports.io"
bball_semaphore = aio.Semaphore(10)

NBA_LEAGUE_ID = 12
BBALL_CURRENT_SEASON = "2024-2025"


async def bball_api_request(endpoint: str, params: dict = None):
    """Make a request to the basketball API-Sports."""
    async with bball_semaphore:
        try:
            url = f"{BASKETBALL_API_BASE}/{endpoint}"
            headers = {"x-apisports-key": API_FOOTBALL_KEY}
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, params=params or {}, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data.get("response", [])
                    return []
        except Exception as e:
            print(f"[BBALL API] {endpoint} error: {e}")
            return []


async def get_team_games(team_id: int, season: str = None, league: int = None):
    """Get recent finished games for a team."""
    s = season or BBALL_CURRENT_SEASON
    lg = league or NBA_LEAGUE_ID
    data = await bball_api_request("games", {
        "team": str(team_id),
        "league": str(lg),
        "season": s,
    })
    finished = [g for g in data if g.get("status", {}).get("short") == "FT"]
    finished.sort(key=lambda g: g.get("date", ""), reverse=True)
    return finished


async def get_team_stats(team_id: int, season: str = None):
    """Get team season statistics."""
    s = season or BBALL_CURRENT_SEASON
    return await bball_api_request("statistics", {
        "team": str(team_id),
        "league": str(NBA_LEAGUE_ID),
        "season": s,
    })


async def get_standings(season: str = None):
    """Get NBA standings."""
    s = season or BBALL_CURRENT_SEASON
    return await bball_api_request("standings", {
        "league": str(NBA_LEAGUE_ID),
        "season": s,
    })


async def get_h2h(team1_id: int, team2_id: int):
    """Get H2H games between two teams."""
    data = await bball_api_request("games/h2h", {
        "h2h": f"{team1_id}-{team2_id}",
    })
    finished = [g for g in data if g.get("status", {}).get("short") == "FT"]
    finished.sort(key=lambda g: g.get("date", ""), reverse=True)
    return finished[:10]


async def get_player_game_stats(game_id: int):
    """Get individual player stats from a specific game. Returns all players."""
    return await bball_api_request("games/statistics/players", {"id": str(game_id)})


async def search_players(query: str, team_id: int = None, season: str = None):
    """Search for players by name."""
    params = {"search": query}
    if team_id:
        params["team"] = str(team_id)
    if season:
        params["season"] = season
    return await bball_api_request("players", params)


async def search_teams(query: str):
    """Search for basketball teams."""
    return await bball_api_request("teams", {"search": query})


async def get_team_players(team_id: int, season: str = None):
    """Get all players on a team roster."""
    s = season or BBALL_CURRENT_SEASON
    return await bball_api_request("players", {
        "team": str(team_id),
        "season": s,
    })


def parse_game_for_team(game: dict, team_id: int) -> dict:
    """Parse a game into a structured format relative to a team."""
    home_id = game.get("teams", {}).get("home", {}).get("id")
    is_home = home_id == team_id
    venue = "home" if is_home else "away"
    opponent = game.get("teams", {}).get("away" if is_home else "home", {})
    scores = game.get("scores", {})
    team_score = scores.get("home" if is_home else "away", {}).get("total", 0) or 0
    opp_score = scores.get("away" if is_home else "home", {}).get("total", 0) or 0

    return {
        "gameId": game.get("id"),
        "date": game.get("date", "")[:10],
        "venue": venue,
        "opponent": opponent.get("name", "Unknown"),
        "opponentId": opponent.get("id"),
        "teamScore": team_score,
        "oppScore": opp_score,
        "result": "W" if team_score > opp_score else "L",
        "stage": game.get("stage", ""),
        "league": game.get("league", {}).get("name", ""),
    }


def parse_player_game_stat(stat: dict) -> dict:
    """Parse individual player stat entry from games/statistics/players."""
    fg = stat.get("field_goals", {})
    tp = stat.get("threepoint_goals", {})
    ft = stat.get("freethrows_goals", {})
    reb = stat.get("rebounds", {})

    return {
        "points": stat.get("points", 0) or 0,
        "rebounds": reb.get("total", 0) or 0,
        "assists": stat.get("assists", 0) or 0,
        "steals": stat.get("steals", 0) or 0,
        "blocks": stat.get("blocks", 0) or 0,
        "turnovers": stat.get("turnovers", 0) or 0,
        "minutes": stat.get("minutes", "0"),
        "fgm": fg.get("total", 0) or 0,
        "fga": fg.get("attempts", 0) or 0,
        "tpm": tp.get("total", 0) or 0,
        "tpa": tp.get("attempts", 0) or 0,
        "ftm": ft.get("total", 0) or 0,
        "fta": ft.get("attempts", 0) or 0,
        "type": stat.get("type", ""),  # "starters" or "bench"
        "playerName": stat.get("player", {}).get("name", ""),
        "playerId": stat.get("player", {}).get("id", 0),
        "teamId": stat.get("team", {}).get("id", 0),
    }
