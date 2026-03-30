import json
import httpx
import asyncio as aio
import unicodedata
from fastapi import HTTPException
from config import API_FOOTBALL_KEY, API_FOOTBALL_BASE, api_semaphore


async def api_football_request(endpoint: str, params: dict = None):
    headers = {
        "x-apisports-key": API_FOOTBALL_KEY,
        "x-rapidapi-key": API_FOOTBALL_KEY,
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
