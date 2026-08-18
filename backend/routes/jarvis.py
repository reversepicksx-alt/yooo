"""
Jarvis assistant API — secure proxy to API-Sports football data.

Authentication: `Authorization: Bearer <OWNER_PIN>`
The API_SPORTS_KEY is never returned in any response.
"""
import os
import httpx
from fastapi import APIRouter, Query, HTTPException, Header
from fastapi.responses import JSONResponse
from typing import Optional

router = APIRouter()

_API_SPORTS_BASE = "https://v3.football.api-sports.io"
_API_SPORTS_KEY  = os.environ.get("API_SPORTS_KEY", "")
_OWNER_PIN       = os.environ.get("OWNER_PIN", "")


def _check_auth(authorization: Optional[str]) -> None:
    """Raise 401 if the bearer token doesn't match OWNER_PIN."""
    if not _OWNER_PIN:
        raise HTTPException(status_code=503, detail="Jarvis auth not configured on server.")
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header.")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or token.strip() != _OWNER_PIN:
        raise HTTPException(status_code=401, detail="Invalid Jarvis API key.")


async def _api_sports_get(endpoint: str, params: dict) -> dict:
    """Internal helper: call API-Sports and return the parsed JSON."""
    if not _API_SPORTS_KEY:
        raise HTTPException(status_code=503, detail="API_SPORTS_KEY not configured on server.")
    url = f"{_API_SPORTS_BASE}/{endpoint}"
    headers = {"x-apisports-key": _API_SPORTS_KEY}
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, headers=headers, params=params)
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"API-Sports returned {resp.status_code}.")
    return resp.json()


# ── Fixtures ─────────────────────────────────────────────────────────────────

@router.get("/api/jarvis/fixtures")
async def jarvis_fixtures(
    authorization: Optional[str] = Header(default=None),
    league:  Optional[int] = Query(None, description="League ID (e.g. 39 = Premier League)"),
    season:  Optional[int] = Query(None, description="Season year (e.g. 2025)"),
    date:    Optional[str] = Query(None, description="Date YYYY-MM-DD"),
    team:    Optional[int] = Query(None, description="Team ID"),
    fixture: Optional[int] = Query(None, description="Specific fixture ID"),
    next:    Optional[int] = Query(None, description="Next N fixtures"),
    last:    Optional[int] = Query(None, description="Last N fixtures"),
    live:    Optional[str] = Query(None, description="'all' or a league ID for live fixtures"),
):
    """
    Retrieve football fixtures from API-Sports.

    Examples:
      - Today's Premier League fixtures: ?league=39&season=2025&date=2026-08-18
      - Next 5 fixtures for a team: ?team=33&next=5
      - Live fixtures: ?live=all
      - Specific fixture: ?fixture=1035039
    """
    _check_auth(authorization)

    params = {}
    if league  is not None: params["league"]  = league
    if season  is not None: params["season"]  = season
    if date    is not None: params["date"]    = date
    if team    is not None: params["team"]    = team
    if fixture is not None: params["id"]      = fixture
    if next    is not None: params["next"]    = next
    if last    is not None: params["last"]    = last
    if live    is not None: params["live"]    = live

    if not params:
        raise HTTPException(
            status_code=400,
            detail="Provide at least one query param: league, season, date, team, fixture, next, last, or live."
        )

    data = await _api_sports_get("fixtures", params)

    # Strip the key from any potential echo in the response (safety measure)
    return JSONResponse(content={
        "source": "api-sports/fixtures",
        "results": data.get("results", 0),
        "fixtures": data.get("response", []),
        "errors": data.get("errors", {}),
    })


# ── Leagues ───────────────────────────────────────────────────────────────────

@router.get("/api/jarvis/leagues")
async def jarvis_leagues(
    authorization: Optional[str] = Header(default=None),
    search: Optional[str] = Query(None, description="League name search term"),
    country: Optional[str] = Query(None, description="Country name"),
    league: Optional[int] = Query(None, description="Specific league ID"),
    current: Optional[bool] = Query(None, description="Only currently active leagues"),
):
    """Look up league IDs by name or country — useful before querying fixtures."""
    _check_auth(authorization)

    params = {}
    if search  is not None: params["search"]  = search
    if country is not None: params["country"] = country
    if league  is not None: params["id"]      = league
    if current is not None: params["current"] = "true" if current else "false"

    data = await _api_sports_get("leagues", params)
    return JSONResponse(content={
        "source": "api-sports/leagues",
        "results": data.get("results", 0),
        "leagues": data.get("response", []),
    })


# ── Teams ─────────────────────────────────────────────────────────────────────

@router.get("/api/jarvis/teams")
async def jarvis_teams(
    authorization: Optional[str] = Header(default=None),
    search: Optional[str] = Query(None, description="Team name search"),
    league: Optional[int] = Query(None, description="League ID"),
    season: Optional[int] = Query(None, description="Season year"),
    team:   Optional[int] = Query(None, description="Specific team ID"),
):
    """Look up team IDs — useful before querying fixtures by team."""
    _check_auth(authorization)

    params = {}
    if search is not None: params["search"] = search
    if league is not None: params["league"] = league
    if season is not None: params["season"] = season
    if team   is not None: params["id"]     = team

    data = await _api_sports_get("teams", params)
    return JSONResponse(content={
        "source": "api-sports/teams",
        "results": data.get("results", 0),
        "teams": data.get("response", []),
    })
