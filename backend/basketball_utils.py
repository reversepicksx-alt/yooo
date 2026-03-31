"""
Basketball (NBA) API Utilities
Uses API-Sports Basketball v1 (v1.basketball.api-sports.io)
All calls use GET with x-apisports-key header.

Key endpoints used:
- /teams?search=X&league=12&season=SEASON  → NBA team search
- /players?search=X&team=T&season=SEASON   → Player lookup
- /games/statistics/players?player=P&season=SEASON → All player game stats (single call!)
- /games?team=T&league=12&season=SEASON    → Team's recent games
- /games?h2h=T1-T2                         → Head-to-head
- /statistics?league=12&season=S&team=T    → Team season stats
- /standings?league=12&season=S            → NBA standings
"""
import os
import asyncio
import httpx
from datetime import datetime

API_KEY = os.environ.get("API_FOOTBALL_KEY", "")
BASE_URL = "https://v1.basketball.api-sports.io"
NBA_LEAGUE_ID = 12

# Auto-detect current NBA season (season runs Oct-Jun, format "YYYY-YYYY")
def get_current_nba_season() -> str:
    now = datetime.utcnow()
    # NBA season starts in October, so if we're Oct-Dec, season is YYYY-(YYYY+1)
    # If Jan-Sep, season is (YYYY-1)-YYYY
    if now.month >= 10:
        return f"{now.year}-{now.year + 1}"
    else:
        return f"{now.year - 1}-{now.year}"

BBALL_CURRENT_SEASON = get_current_nba_season()


async def _api_get(endpoint: str, params: dict) -> list:
    """Make a GET request to the basketball API."""
    url = f"{BASE_URL}/{endpoint}"
    headers = {"x-apisports-key": API_KEY}
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            resp = await client.get(url, headers=headers, params=params)
            data = resp.json()
            return data.get("response", [])
    except Exception as e:
        print(f"[BBALL API] {endpoint} error: {e}")
        return []


# ════════════════════════════════════════════
# TEAM OPERATIONS (NBA-filtered)
# ════════════════════════════════════════════

async def search_nba_teams(query: str) -> list:
    """Search for NBA teams only (league=12)."""
    results = await _api_get("teams", {
        "search": query,
        "league": NBA_LEAGUE_ID,
        "season": BBALL_CURRENT_SEASON,
    })
    if not results:
        # Fallback: try previous season
        prev_season = f"{int(BBALL_CURRENT_SEASON[:4])-1}-{BBALL_CURRENT_SEASON[:4]}"
        results = await _api_get("teams", {
            "search": query,
            "league": NBA_LEAGUE_ID,
            "season": prev_season,
        })
    return results


async def get_team_games(team_id: int, season: str = None) -> list:
    """Get finished games for a team in the current NBA season."""
    s = season or BBALL_CURRENT_SEASON
    games = await _api_get("games", {
        "team": team_id,
        "league": NBA_LEAGUE_ID,
        "season": s,
    })
    # Filter to finished games only, sort by date descending
    finished = [g for g in games if g.get("status", {}).get("short") in ("FT", "AOT")]
    finished.sort(key=lambda g: g.get("date", ""), reverse=True)
    return finished


async def get_h2h(team1_id: int, team2_id: int) -> list:
    """Get head-to-head games between two teams."""
    return await _api_get("games", {"h2h": f"{team1_id}-{team2_id}"})


async def get_team_stats(team_id: int, season: str = None) -> dict:
    """Get team season statistics."""
    s = season or BBALL_CURRENT_SEASON
    results = await _api_get("statistics", {
        "team": team_id,
        "league": NBA_LEAGUE_ID,
        "season": s,
    })
    return results if isinstance(results, dict) else (results[0] if results else {})


async def get_standings(season: str = None) -> list:
    """Get NBA standings."""
    s = season or BBALL_CURRENT_SEASON
    return await _api_get("standings", {
        "league": NBA_LEAGUE_ID,
        "season": s,
    })


# ════════════════════════════════════════════
# PLAYER OPERATIONS
# ════════════════════════════════════════════

async def search_player(name: str, team_id: int = None, season: str = None) -> dict:
    """
    Find a player by name. Tries multiple strategies:
    1. Search by last name + team + season
    2. Search by last name only
    3. Search by first name
    Returns player dict {id, name, position, ...} or None.
    """
    s = season or BBALL_CURRENT_SEASON
    name_parts = name.strip().split()
    last_name = name_parts[-1] if name_parts else name
    first_name = name_parts[0] if len(name_parts) > 1 else ""

    # Strategy 1: last name + team + season
    if team_id:
        results = await _api_get("players", {
            "search": last_name,
            "team": team_id,
            "season": s,
        })
        match = _best_player_match(results, name)
        if match:
            return match

    # Strategy 2: last name only (across all teams)
    results = await _api_get("players", {"search": last_name})
    match = _best_player_match(results, name)
    if match:
        return match

    # Strategy 3: first name
    if first_name and len(first_name) >= 3:
        results = await _api_get("players", {"search": first_name})
        match = _best_player_match(results, name)
        if match:
            return match

    return None


def _best_player_match(players: list, search_name: str) -> dict:
    """Find the best matching player from a list."""
    if not players:
        return None

    search_lower = search_name.lower().strip()
    search_parts = search_lower.split()
    last_name = search_parts[-1] if search_parts else ""
    first_name = search_parts[0] if len(search_parts) > 1 else ""

    # Score each player
    scored = []
    for p in players:
        api_name = p.get("name", "").lower()
        # API format is "LastName FirstName" (e.g., "Green Jalen")
        api_parts = api_name.split()

        score = 0
        # Exact full match
        if api_name == search_lower or api_name == f"{last_name} {first_name}":
            score = 100
        # Last name match
        elif last_name and last_name in api_parts:
            score = 50
            # First name partial match
            if first_name:
                for ap in api_parts:
                    if ap.startswith(first_name[:3]) or first_name.startswith(ap[:3]):
                        score += 30
                        break
        # Initial match (e.g., "J. Green")
        elif len(search_parts) >= 2 and len(search_parts[0]) <= 2:
            initial = search_parts[0].rstrip(".")
            if api_parts and api_parts[0] == last_name:
                score = 40
                if len(api_parts) > 1 and api_parts[1].startswith(initial):
                    score += 30

        if score > 0:
            scored.append((score, p))

    if scored:
        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[0][1]
    return None


async def get_player_season_stats(player_id: int, season: str = None) -> list:
    """
    Get ALL of a player's game stats for a season in a SINGLE API call.
    This is the KEY optimization — one call instead of 20+ per-game calls.
    Returns list of stat entries with parsed fields.
    """
    s = season or BBALL_CURRENT_SEASON
    raw = await _api_get("games/statistics/players", {
        "player": player_id,
        "season": s,
    })
    return raw


# ════════════════════════════════════════════
# PARSING HELPERS
# ════════════════════════════════════════════

def parse_player_stat(stat: dict) -> dict:
    """Parse a single player game stat entry from the API response."""
    fg = stat.get("field_goals", {}) or {}
    tp = stat.get("threepoint_goals", {}) or {}
    ft = stat.get("freethrows_goals", {}) or {}
    reb = stat.get("rebounds", {})

    return {
        "gameId": stat.get("game", {}).get("id"),
        "teamId": stat.get("team", {}).get("id"),
        "playerId": stat.get("player", {}).get("id"),
        "playerName": stat.get("player", {}).get("name", ""),
        "type": stat.get("type", ""),  # starters/bench
        "minutes": stat.get("minutes", "0:00"),
        "points": stat.get("points", 0) or 0,
        "rebounds": reb.get("total", 0) if isinstance(reb, dict) else (reb or 0),
        "assists": stat.get("assists", 0) or 0,
        "fgm": fg.get("total", 0) or 0,
        "fga": fg.get("attempts", 0) or 0,
        "tpm": tp.get("total", 0) or 0,
        "tpa": tp.get("attempts", 0) or 0,
        "ftm": ft.get("total", 0) or 0,
        "fta": ft.get("attempts", 0) or 0,
    }


def parse_game_for_team(game: dict, team_id: int) -> dict:
    """Parse a game entry from the perspective of a specific team."""
    teams = game.get("teams", {})
    scores = game.get("scores", {})
    home = teams.get("home", {})
    away = teams.get("away", {})

    is_home = home.get("id") == team_id
    my_team = home if is_home else away
    opp_team = away if is_home else home
    my_scores = scores.get("home", {}) if is_home else scores.get("away", {})
    opp_scores = scores.get("away", {}) if is_home else scores.get("home", {})

    my_total = my_scores.get("total", 0) or 0
    opp_total = opp_scores.get("total", 0) or 0
    result = "W" if my_total > opp_total else ("L" if opp_total > my_total else "D")

    return {
        "gameId": game.get("id"),
        "date": (game.get("date") or "")[:10],
        "venue": "home" if is_home else "away",
        "opponent": opp_team.get("name", ""),
        "opponentId": opp_team.get("id"),
        "teamScore": my_total,
        "oppScore": opp_total,
        "result": result,
    }


# Legacy aliases for backward compatibility
async def search_teams(query: str) -> list:
    return await search_nba_teams(query)
