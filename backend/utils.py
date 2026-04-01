import json
import httpx
import asyncio as aio
import unicodedata
from fastapi import HTTPException
from config import API_FOOTBALL_BASE, api_semaphore, get_dynamic_api_key


async def api_football_request(endpoint: str, params: dict = None):
    key = get_dynamic_api_key()
    headers = {
        "x-apisports-key": key,
        "x-rapidapi-key": key,
    }
    async with api_semaphore:
        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    resp = await client.get(f"{API_FOOTBALL_BASE}/{endpoint}", headers=headers, params=params or {})
                    if resp.status_code == 429:
                        await aio.sleep(1.5 * (attempt + 1))
                        continue
                    if resp.status_code != 200:
                        raise HTTPException(status_code=resp.status_code, detail=f"API-Sports error: {resp.text}")
                    data = resp.json()
                    if data.get("errors") and len(data["errors"]) > 0:
                        error_msg = json.dumps(data["errors"])
                        if "Too many requests" in error_msg or "rate limit" in error_msg.lower():
                            await aio.sleep(1.5 * (attempt + 1))
                            continue
                        raise HTTPException(status_code=400, detail=f"API-Sports error: {error_msg}")
                    return data.get("response", [])
            except httpx.TimeoutException:
                if attempt < 2:
                    continue
                raise HTTPException(status_code=504, detail="API-Sports timeout")
        raise HTTPException(status_code=429, detail="API-Sports rate limit — try again in a few seconds")


async def get_recent_fixtures_fast(team_id: int, count: int = 20):
    """Get recent fixtures with fixture IDs for deeper stat lookups."""
    try:
        fixtures = await api_football_request("fixtures", {"team": team_id, "last": count})
        results = []
        for f in fixtures[:count]:
            home_team_id = f.get("teams", {}).get("home", {}).get("id")
            venue = "home" if home_team_id == team_id else "away"
            home_goals = f.get("goals", {}).get("home", 0) or 0
            away_goals = f.get("goals", {}).get("away", 0) or 0
            opponent_name = f.get("teams", {}).get("away" if venue == "home" else "home", {}).get("name", "Unknown")
            results.append({
                "fixtureId": f.get("fixture", {}).get("id"),
                "date": f.get("fixture", {}).get("date", ""),
                "opponent": opponent_name,
                "venue": venue,
                "homeGoals": home_goals,
                "awayGoals": away_goals,
                "result": f.get("teams", {}).get("home" if venue == "home" else "away", {}).get("winner"),
                "league": f.get("league", {}).get("name", ""),
                "round": f.get("league", {}).get("round", ""),
            })
        return results
    except Exception:
        return []


def strip_accents(text):
    """Remove diacritics and normalize Nordic/special chars for API search."""
    CHAR_MAP = {
        'ø': 'o', 'Ø': 'O', 'æ': 'ae', 'Æ': 'AE', 'å': 'a', 'Å': 'A',
        'ð': 'd', 'Ð': 'D', 'þ': 'th', 'Þ': 'Th', 'ß': 'ss',
        'ł': 'l', 'Ł': 'L', 'đ': 'd', 'Đ': 'D',
    }
    text = ''.join(CHAR_MAP.get(c, c) for c in text)
    nfkd = unicodedata.normalize('NFKD', text)
    return ''.join(c for c in nfkd if not unicodedata.category(c).startswith('M'))



def decimal_to_american(decimal_odds: float) -> str:
    """Convert decimal odds to American odds string."""
    if decimal_odds >= 2.0:
        american = round((decimal_odds - 1) * 100)
        return f"+{american}"
    elif decimal_odds > 1.0:
        american = round(-100 / (decimal_odds - 1))
        return str(american)
    return "+100"


async def get_soccer_odds(team_id: int, opponent_id: int, league_id: int) -> dict:
    """Fetch moneyline odds for the next fixture between two teams."""
    try:
        # Find the next fixture between these teams
        fixtures = await api_football_request("fixtures", {
            "h2h": f"{team_id}-{opponent_id}",
            "next": 1,
        })
        if not fixtures:
            # Try season-based search
            fixtures = await api_football_request("fixtures", {
                "h2h": f"{team_id}-{opponent_id}",
                "season": "2025",
                "status": "NS",
            })

        if not fixtures:
            return {}

        fixture_id = fixtures[0].get("fixture", {}).get("id")
        if not fixture_id:
            return {}

        # Fetch odds
        odds_data = await api_football_request("odds", {"fixture": fixture_id})
        if not odds_data:
            return {}

        bookmakers = odds_data[0].get("bookmakers", []) if odds_data else []
        if not bookmakers:
            return {}

        for bet in bookmakers[0].get("bets", []):
            if bet.get("name") == "Match Winner":
                values = bet.get("values", [])
                home_odds = None
                away_odds = None
                draw_odds = None
                for v in values:
                    dec = float(v.get("odd", 0))
                    if v.get("value") == "Home":
                        home_odds = dec
                    elif v.get("value") == "Away":
                        away_odds = dec
                    elif v.get("value") == "Draw":
                        draw_odds = dec

                if home_odds and away_odds:
                    home_team = fixtures[0].get("teams", {}).get("home", {})
                    away_team = fixtures[0].get("teams", {}).get("away", {})
                    home_american = decimal_to_american(home_odds)
                    away_american = decimal_to_american(away_odds)
                    draw_american = decimal_to_american(draw_odds) if draw_odds else "+100"
                    favorite = home_team.get("name", "") if home_odds < away_odds else away_team.get("name", "")

                    return {
                        "homeName": home_team.get("name", ""),
                        "awayName": away_team.get("name", ""),
                        "homeOdds": home_american,
                        "awayOdds": away_american,
                        "drawOdds": draw_american,
                        "favorite": favorite,
                        "fixtureId": fixture_id,
                    }
        return {}
    except Exception as e:
        print(f"[SOCCER ODDS] Error: {e}")
        return {}
