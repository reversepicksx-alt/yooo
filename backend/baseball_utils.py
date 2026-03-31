"""
Baseball API utilities — completely separate from soccer.
Base URL: https://v1.baseball.api-sports.io/
Same API key, different sport endpoints.
"""
import aiohttp
import asyncio as aio
from config import API_FOOTBALL_KEY

BASEBALL_API_BASE = "https://v1.baseball.api-sports.io"
baseball_semaphore = aio.Semaphore(10)

MLB_LEAGUE_ID = 1
BASEBALL_CURRENT_SEASON = 2025


async def baseball_api_request(endpoint: str, params: dict = None):
    """Make a request to the baseball API-Sports."""
    async with baseball_semaphore:
        try:
            url = f"{BASEBALL_API_BASE}/{endpoint}"
            headers = {"x-apisports-key": API_FOOTBALL_KEY}
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, params=params or {}, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data.get("response", [])
                    return []
        except Exception as e:
            print(f"[BASEBALL API] {endpoint} error: {e}")
            return []


async def get_baseball_team_games(team_id: int, season: int = None, last: int = 20):
    """Get recent games for a team."""
    params = {"team": str(team_id)}
    if season:
        params["season"] = str(season)
        params["league"] = str(MLB_LEAGUE_ID)
    else:
        # Use league + current season for recent games
        params["league"] = str(MLB_LEAGUE_ID)
        params["season"] = str(BASEBALL_CURRENT_SEASON)
    data = await baseball_api_request("games", params)
    # Filter to finished games and sort by date desc
    finished = [g for g in data if g.get("status", {}).get("short") == "FT"]
    finished.sort(key=lambda g: g.get("date", ""), reverse=True)
    return finished[:last]


async def get_baseball_team_stats(team_id: int, season: int = None):
    """Get team season statistics."""
    s = season or BASEBALL_CURRENT_SEASON
    for try_season in [s, s - 1]:
        data = await baseball_api_request("teams/statistics", {
            "team": str(team_id),
            "league": str(MLB_LEAGUE_ID),
            "season": str(try_season),
        })
        if data:
            return data
    return None


async def get_baseball_standings(season: int = None):
    """Get MLB standings."""
    s = season or BASEBALL_CURRENT_SEASON
    for try_season in [s, s - 1]:
        data = await baseball_api_request("standings", {
            "league": str(MLB_LEAGUE_ID),
            "season": str(try_season),
        })
        if data:
            return data
    return None


async def get_baseball_h2h(team1_id: int, team2_id: int, last: int = 10):
    """Get head-to-head games between two teams."""
    data = await baseball_api_request("games/h2h", {
        "h2h": f"{team1_id}-{team2_id}",
    })
    if data:
        finished = [g for g in data if g.get("status", {}).get("short") == "FT"]
        finished.sort(key=lambda g: g.get("date", ""), reverse=True)
        return finished[:last]
    return []


async def get_baseball_odds(game_id: int):
    """Get odds for a specific game."""
    data = await baseball_api_request("odds", {"game": str(game_id)})
    return data[0] if data else None


async def search_baseball_teams(query: str):
    """Search for baseball teams by name."""
    data = await baseball_api_request("teams", {"search": query})
    return data


def parse_game_for_team(game: dict, team_id: int) -> dict:
    """Parse a game response into a structured format relative to a team."""
    home_id = game.get("teams", {}).get("home", {}).get("id")
    is_home = home_id == team_id
    venue = "home" if is_home else "away"
    opponent = game.get("teams", {}).get("away" if is_home else "home", {})
    scores = game.get("scores", {})
    team_score = scores.get("home" if is_home else "away", {})
    opp_score = scores.get("away" if is_home else "home", {})

    return {
        "gameId": game.get("id"),
        "date": game.get("date", "")[:10],
        "venue": venue,
        "opponent": opponent.get("name", "Unknown"),
        "opponentId": opponent.get("id"),
        "teamRuns": team_score.get("total", 0) or 0,
        "oppRuns": opp_score.get("total", 0) or 0,
        "teamHits": team_score.get("hits", 0) or 0,
        "oppHits": opp_score.get("hits", 0) or 0,
        "teamErrors": team_score.get("errors", 0) or 0,
        "oppErrors": opp_score.get("errors", 0) or 0,
        "innings": team_score.get("innings", {}),
        "result": "W" if (team_score.get("total", 0) or 0) > (opp_score.get("total", 0) or 0) else "L",
        "league": game.get("league", {}).get("name", ""),
        "round": game.get("league", {}).get("round", ""),
    }
