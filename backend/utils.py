import json
import httpx
import asyncio as aio
import unicodedata
from datetime import datetime, timezone
from fastapi import HTTPException
from config import API_FOOTBALL_BASE, api_semaphore, get_dynamic_api_key

# ── Quota circuit breaker ─────────────────────────────────────────────────────
# Once the daily quota is confirmed exhausted, we stop ALL outbound API calls
# immediately instead of letting every background loop hammer the API with
# hundreds of rejected requests. The breaker resets at midnight UTC.
# State is persisted to disk so server restarts on the same day don't re-burn calls.
import os as _os

_BREAKER_FILE = "/tmp/.api_sports_quota_exhausted"
_quota_exhausted_date: str | None = None  # in-memory cache of the breaker date


def _load_breaker_from_disk() -> str | None:
    """Read persisted breaker date from disk (survives process restart)."""
    try:
        if _os.path.exists(_BREAKER_FILE):
            with open(_BREAKER_FILE) as f:
                return f.read().strip() or None
    except Exception:
        pass
    return None


def _quota_tripped() -> bool:
    """Return True if the breaker is active for today (UTC date)."""
    global _quota_exhausted_date
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    # Check in-memory first, then disk (covers restarts)
    date = _quota_exhausted_date or _load_breaker_from_disk()
    if date is None:
        return False
    if date != today:
        # New UTC day — quota has reset, clear everything
        _quota_exhausted_date = None
        try:
            _os.remove(_BREAKER_FILE)
        except Exception:
            pass
        return False
    _quota_exhausted_date = date  # populate in-memory from disk if needed
    return True


def _trip_quota_breaker(error_msg: str):
    """Mark quota as exhausted for today and persist to disk."""
    global _quota_exhausted_date
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if _quota_exhausted_date != today:
        _quota_exhausted_date = today
        try:
            with open(_BREAKER_FILE, "w") as f:
                f.write(today)
        except Exception:
            pass
        print(f"[API-SPORTS] Daily quota exhausted — circuit breaker tripped for {today}. All API calls suspended until midnight UTC.")
    # Only log if this is a fresh trip (not redundant noise)
    else:
        return  # Already tripped and logged — stay silent
    print(f"[API-SPORTS] Quota error detail: {error_msg}")


def is_quota_exhausted() -> bool:
    """Public helper — lets background loops check before attempting calls."""
    return _quota_tripped()


async def api_football_request(endpoint: str, params: dict = None):
    # Short-circuit immediately if today's quota is already known to be gone
    if _quota_tripped():
        return []

    key = get_dynamic_api_key()
    headers = {
        "x-apisports-key": key,
        "x-rapidapi-key": key,
    }
    async with api_semaphore:
        # Re-check inside the semaphore — if a concurrent call just tripped
        # the breaker while we were waiting, bail out immediately.
        if _quota_tripped():
            return []
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
                        if "Too many requests" in error_msg or "rate limit" in error_msg.lower() or "request limit" in error_msg.lower() or "reached the request limit" in error_msg.lower():
                            _trip_quota_breaker(error_msg)
                            return []
                        raise HTTPException(status_code=400, detail=f"API-Sports error: {error_msg}")
                    return data.get("response", [])
            except httpx.TimeoutException:
                if attempt < 2:
                    continue
                raise HTTPException(status_code=504, detail="API-Sports timeout")
        raise HTTPException(status_code=429, detail="API-Sports rate limit — try again in a few seconds")


def _parse_fixtures_to_results(fixtures: list, team_id: int, count: int) -> list:
    """Convert raw API fixture objects into the shape predict.py expects."""
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


async def get_recent_fixtures_fast(team_id: int, count: int = 20):
    """
    Get recent fixtures for a team.
    Checks local DB first (team_fixture_history), falls back to live API.
    """
    try:
        # ── Local cache first ──────────────────────────────────────────
        from config import db
        doc = await db.team_fixture_history.find_one({"teamId": team_id}, {"_id": 0, "fixtures": 1, "_ts": 1})
        if doc and doc.get("fixtures"):
            import time as _t
            age = _t.time() - doc.get("_ts", 0)
            if age < 48 * 3600:  # use local if < 48h old
                return _parse_fixtures_to_results(doc["fixtures"], team_id, count)

        # ── Live API fallback ──────────────────────────────────────────
        fixtures = await api_football_request("fixtures", {"team": team_id, "last": count, "status": "FT"})
        return _parse_fixtures_to_results(fixtures or [], team_id, count)
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
