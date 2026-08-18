"""
JARVIS Integration Layer — secure proxy to API-Sports football data.

Authentication
--------------
Every request (except GET /api/jarvis/health and GET /api/jarvis/docs)
requires the header:

    Authorization: Bearer <JARVIS_API_KEY>

The secret JARVIS_API_KEY lives in Replit Secrets and is never returned,
logged, or echoed in any response.  API_SPORTS_KEY is also secret and
never leaves the server.

Available endpoints
-------------------
GET /api/jarvis/health          – server + auth status (no key needed)
GET /api/jarvis/docs            – machine-readable API reference (no key needed)
GET /api/jarvis/fixtures        – match fixtures
GET /api/jarvis/leagues         – league catalogue
GET /api/jarvis/teams           – team catalogue
GET /api/jarvis/standings       – league standings table
GET /api/jarvis/players         – player season statistics
GET /api/jarvis/player/fixtures – a player's recent match history
"""
from __future__ import annotations

import os
import time
from typing import Optional

import httpx
from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import JSONResponse

router = APIRouter()

# ── Config (read once at import time) ────────────────────────────────────────
_API_SPORTS_BASE = "https://v3.football.api-sports.io"
_API_SPORTS_KEY  = os.environ.get("API_SPORTS_KEY", "")
_JARVIS_KEY      = os.environ.get("JARVIS_API_KEY", "")


# ── Auth ─────────────────────────────────────────────────────────────────────

def _require_auth(authorization: Optional[str]) -> None:
    """Raise 401 unless the bearer token matches JARVIS_API_KEY."""
    if not _JARVIS_KEY:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "JARVIS_API_KEY secret is not configured on the server.",
                "action": "Set JARVIS_API_KEY in Replit Secrets and restart the backend.",
            },
        )
    if not authorization:
        raise HTTPException(
            status_code=401,
            detail={
                "error": "Missing Authorization header.",
                "format": "Authorization: Bearer <JARVIS_API_KEY>",
            },
        )
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or token.strip() != _JARVIS_KEY:
        raise HTTPException(
            status_code=401,
            detail={"error": "Invalid JARVIS API key."},
        )


# ── Internal API-Sports helper ────────────────────────────────────────────────

async def _sports_get(endpoint: str, params: dict) -> dict:
    """Call API-Sports; return parsed JSON.  Raises 502/503 on upstream errors."""
    if not _API_SPORTS_KEY:
        raise HTTPException(
            status_code=503,
            detail={"error": "API_SPORTS_KEY not configured on the server."},
        )
    url = f"{_API_SPORTS_BASE}/{endpoint}"
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            url,
            headers={"x-apisports-key": _API_SPORTS_KEY},
            params=params,
        )
    if resp.status_code == 429:
        raise HTTPException(
            status_code=429,
            detail={"error": "API-Sports daily quota exhausted. Try again after midnight UTC."},
        )
    if resp.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail={"error": f"API-Sports returned HTTP {resp.status_code}."},
        )
    data = resp.json()
    errors = data.get("errors", {})
    if errors:
        raise HTTPException(
            status_code=422,
            detail={"error": "API-Sports reported parameter errors.", "details": errors},
        )
    return data


# ─────────────────────────────────────────────────────────────────────────────
# Public endpoints (no auth required)
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/api/jarvis/openapi.json", include_in_schema=False)
async def jarvis_openapi():
    """
    OpenAPI 3.1 schema — import this URL directly into a ChatGPT Custom GPT Action.
    No authentication required.
    """
    base = "https://7a030359-7bf3-4fa1-8914-cbee61d63eb2-00-1w1w9xi7usfsw.picard.replit.dev"
    return JSONResponse(content={
        "openapi": "3.0.3",
        "info": {
            "title": "JARVIS Football API",
            "description": (
                "Secure proxy to real-time football data via API-Sports. "
                "Provides fixtures, league tables, team info, player stats, "
                "and recent match history. All data endpoints require a "
                "Bearer token in the Authorization header."
            ),
            "version": "2.0.0",
        },
        "servers": [{"url": base}],
        "paths": {
            "/api/jarvis/health": {
                "get": {
                    "operationId": "getHealth",
                    "summary": "Server health check",
                    "description": "Returns server status and whether both API keys are configured. No authentication required.",
                    "security": [],
                    "responses": {
                        "200": {
                            "description": "Health status",
                            "content": {"application/json": {"schema": {
                                "type": "object",
                                "properties": {
                                    "status": {"type": "string"},
                                    "service": {"type": "string"},
                                    "timestamp": {"type": "integer"},
                                    "auth": {"type": "object"},
                                }
                            }}},
                        }
                    },
                }
            },
            "/api/jarvis/fixtures": {
                "get": {
                    "operationId": "getFixtures",
                    "summary": "Get football fixtures",
                    "description": (
                        "Retrieve football match fixtures. Filter by date, league, team, or get live matches. "
                        "At least one parameter is required. Use live='all' for currently in-progress matches."
                    ),
                    "parameters": [
                        {"name": "league",  "in": "query", "schema": {"type": "integer"}, "description": "League ID (use /api/jarvis/leagues to look up IDs). Common: 39=Premier League, 140=La Liga, 2=Champions League."},
                        {"name": "season",  "in": "query", "schema": {"type": "integer"}, "description": "Season year, e.g. 2025 or 2026."},
                        {"name": "date",    "in": "query", "schema": {"type": "string"},  "description": "Date in YYYY-MM-DD format."},
                        {"name": "team",    "in": "query", "schema": {"type": "integer"}, "description": "Team ID (use /api/jarvis/teams to look up IDs)."},
                        {"name": "fixture", "in": "query", "schema": {"type": "integer"}, "description": "Specific fixture ID for detailed lookup."},
                        {"name": "next",    "in": "query", "schema": {"type": "integer"}, "description": "Fetch the next N upcoming fixtures (max 20)."},
                        {"name": "last",    "in": "query", "schema": {"type": "integer"}, "description": "Fetch the last N completed fixtures (max 20)."},
                        {"name": "live",    "in": "query", "schema": {"type": "string"},  "description": "Pass 'all' for all live matches, or a league ID string for live matches in that league."},
                    ],
                    "responses": {
                        "200": {"description": "List of fixtures", "content": {"application/json": {"schema": {"type": "object", "properties": {"source": {"type": "string"}, "results": {"type": "integer"}, "fixtures": {"type": "array", "items": {"type": "object"}}}}}}},
                        "400": {"description": "No query parameters provided"},
                        "401": {"description": "Invalid or missing bearer token"},
                    },
                }
            },
            "/api/jarvis/leagues": {
                "get": {
                    "operationId": "getLeagues",
                    "summary": "Look up league IDs",
                    "description": (
                        "Search for football leagues by name or country to find their numeric IDs. "
                        "Use `search` OR `country`, not both at once."
                    ),
                    "parameters": [
                        {"name": "search",  "in": "query", "schema": {"type": "string"},  "description": "Partial league name, e.g. 'premier' or 'champions'."},
                        {"name": "country", "in": "query", "schema": {"type": "string"},  "description": "Country name, e.g. 'England' or 'Spain'. Use search OR country, not both."},
                        {"name": "league",  "in": "query", "schema": {"type": "integer"}, "description": "Specific league ID to look up details for."},
                        {"name": "current", "in": "query", "schema": {"type": "boolean"}, "description": "Set true to return only currently active league seasons."},
                    ],
                    "responses": {
                        "200": {"description": "List of leagues", "content": {"application/json": {"schema": {"type": "object", "properties": {"results": {"type": "integer"}, "leagues": {"type": "array", "items": {"type": "object"}}}}}}},
                        "401": {"description": "Invalid or missing bearer token"},
                    },
                }
            },
            "/api/jarvis/teams": {
                "get": {
                    "operationId": "getTeams",
                    "summary": "Look up team IDs",
                    "description": "Search for football teams by name or within a specific league to find their numeric IDs for use in other endpoints.",
                    "parameters": [
                        {"name": "search", "in": "query", "schema": {"type": "string"},  "description": "Partial team name, e.g. 'Arsenal' or 'Real Madrid'."},
                        {"name": "league", "in": "query", "schema": {"type": "integer"}, "description": "Filter results to teams that compete in this league."},
                        {"name": "season", "in": "query", "schema": {"type": "integer"}, "description": "Season year to use when filtering by league."},
                        {"name": "team",   "in": "query", "schema": {"type": "integer"}, "description": "Specific team ID to look up."},
                    ],
                    "responses": {
                        "200": {"description": "List of teams", "content": {"application/json": {"schema": {"type": "object", "properties": {"results": {"type": "integer"}, "teams": {"type": "array", "items": {"type": "object"}}}}}}},
                        "401": {"description": "Invalid or missing bearer token"},
                    },
                }
            },
            "/api/jarvis/standings": {
                "get": {
                    "operationId": "getStandings",
                    "summary": "Get league standings table",
                    "description": "Returns the current standings/table for a league and season. League ID and season are required.",
                    "parameters": [
                        {"name": "league", "in": "query", "required": True, "schema": {"type": "integer"}, "description": "League ID (e.g. 39 for Premier League)."},
                        {"name": "season", "in": "query", "required": True, "schema": {"type": "integer"}, "description": "Season year (e.g. 2025)."},
                        {"name": "team",   "in": "query", "schema": {"type": "integer"}, "description": "Optional: filter to a single team's row in the table."},
                    ],
                    "responses": {
                        "200": {"description": "Standings table", "content": {"application/json": {"schema": {"type": "object", "properties": {"league": {"type": "integer"}, "season": {"type": "integer"}, "standings": {"type": "array", "items": {"type": "object"}}}}}}},
                        "401": {"description": "Invalid or missing bearer token"},
                    },
                }
            },
            "/api/jarvis/players": {
                "get": {
                    "operationId": "getPlayerStats",
                    "summary": "Get player season statistics",
                    "description": "Returns season statistics for a specific player. Player ID and season year are required.",
                    "parameters": [
                        {"name": "player", "in": "query", "required": True, "schema": {"type": "integer"}, "description": "Player ID."},
                        {"name": "season", "in": "query", "required": True, "schema": {"type": "integer"}, "description": "Season year (e.g. 2025)."},
                        {"name": "league", "in": "query", "schema": {"type": "integer"}, "description": "Optional: filter stats to a specific league."},
                    ],
                    "responses": {
                        "200": {"description": "Player statistics", "content": {"application/json": {"schema": {"type": "object", "properties": {"results": {"type": "integer"}, "players": {"type": "array", "items": {"type": "object"}}}}}}},
                        "401": {"description": "Invalid or missing bearer token"},
                    },
                }
            },
            "/api/jarvis/player/fixtures": {
                "get": {
                    "operationId": "getPlayerFixtures",
                    "summary": "Get a player's recent match history",
                    "description": (
                        "Returns the player's last 10 team fixtures in a given league and season. "
                        "Automatically resolves the player's team — no team ID needed. "
                        "Player ID, league ID, and season are all required."
                    ),
                    "parameters": [
                        {"name": "player", "in": "query", "required": True, "schema": {"type": "integer"}, "description": "Player ID."},
                        {"name": "league", "in": "query", "required": True, "schema": {"type": "integer"}, "description": "League ID."},
                        {"name": "season", "in": "query", "required": True, "schema": {"type": "integer"}, "description": "Season year."},
                    ],
                    "responses": {
                        "200": {"description": "Player's recent fixtures", "content": {"application/json": {"schema": {"type": "object", "properties": {"player_name": {"type": "string"}, "team_id": {"type": "integer"}, "results": {"type": "integer"}, "fixtures": {"type": "array", "items": {"type": "object"}}}}}}},
                        "401": {"description": "Invalid or missing bearer token"},
                    },
                }
            },
        },
        "components": {
            "schemas": {},
            "securitySchemes": {
                "BearerAuth": {
                    "type": "http",
                    "scheme": "bearer",
                    "description": "Enter your JARVIS_API_KEY as the bearer token.",
                }
            }
        },
        "security": [{"BearerAuth": []}],
    })


@router.get("/api/jarvis/health")
async def jarvis_health():
    """
    Health check — no authentication required.

    Returns server status and whether the JARVIS key and API-Sports key are
    configured.  Never reveals the actual key values.
    """
    return JSONResponse(content={
        "status": "ok",
        "service": "jarvis",
        "timestamp": int(time.time()),
        "auth": {
            "jarvis_key_configured": bool(_JARVIS_KEY),
            "api_sports_configured": bool(_API_SPORTS_KEY),
        },
        "note": (
            "All data endpoints require: Authorization: Bearer <JARVIS_API_KEY>"
        ),
    })


@router.get("/api/jarvis/docs")
async def jarvis_docs():
    """
    Machine-readable API reference — no authentication required.

    Returns a structured JSON description of every JARVIS endpoint so an AI
    assistant can understand how to call the API without human documentation.
    """
    base = "https://7a030359-7bf3-4fa1-8914-cbee61d63eb2-00-1w1w9xi7usfsw.picard.replit.dev"
    return JSONResponse(content={
        "service": "JARVIS Football API",
        "version": "2.0",
        "base_url": base,
        "authentication": {
            "type": "Bearer token",
            "header": "Authorization",
            "format": "Authorization: Bearer <JARVIS_API_KEY>",
            "obtain": "The JARVIS_API_KEY is provided by the server owner.",
            "note": "health and docs endpoints do not require authentication.",
        },
        "endpoints": [
            {
                "method": "GET",
                "path": "/api/jarvis/health",
                "auth_required": False,
                "description": "Server health and key-configuration status.",
                "params": [],
                "example": f"GET {base}/api/jarvis/health",
            },
            {
                "method": "GET",
                "path": "/api/jarvis/docs",
                "auth_required": False,
                "description": "This document — full API reference.",
                "params": [],
                "example": f"GET {base}/api/jarvis/docs",
            },
            {
                "method": "GET",
                "path": "/api/jarvis/fixtures",
                "auth_required": True,
                "description": (
                    "Football match fixtures. Filter by date, league, team, or get "
                    "live matches. At least one query parameter is required."
                ),
                "params": [
                    {"name": "league",  "type": "integer", "required": False, "description": "League ID (see /api/jarvis/leagues)"},
                    {"name": "season",  "type": "integer", "required": False, "description": "Season year, e.g. 2025 or 2026"},
                    {"name": "date",    "type": "string",  "required": False, "description": "Date in YYYY-MM-DD format"},
                    {"name": "team",    "type": "integer", "required": False, "description": "Team ID (see /api/jarvis/teams)"},
                    {"name": "fixture", "type": "integer", "required": False, "description": "Specific fixture ID"},
                    {"name": "next",    "type": "integer", "required": False, "description": "Next N upcoming fixtures (max 20)"},
                    {"name": "last",    "type": "integer", "required": False, "description": "Last N completed fixtures (max 20)"},
                    {"name": "live",    "type": "string",  "required": False, "description": "'all' for all live matches, or a league ID"},
                ],
                "response_fields": ["source", "results", "fixtures"],
                "example": f"GET {base}/api/jarvis/fixtures?live=all",
                "examples": [
                    f"{base}/api/jarvis/fixtures?live=all",
                    f"{base}/api/jarvis/fixtures?league=39&season=2025&date=2026-08-18",
                    f"{base}/api/jarvis/fixtures?team=33&next=5",
                    f"{base}/api/jarvis/fixtures?fixture=1035039",
                ],
            },
            {
                "method": "GET",
                "path": "/api/jarvis/leagues",
                "auth_required": True,
                "description": "Search for leagues by name or country to get their IDs for use in other endpoints.",
                "params": [
                    {"name": "search",  "type": "string",  "required": False, "description": "Partial league name, e.g. 'premier'"},
                    {"name": "country", "type": "string",  "required": False, "description": "Country name, e.g. 'England'"},
                    {"name": "league",  "type": "integer", "required": False, "description": "Specific league ID to look up"},
                    {"name": "current", "type": "boolean", "required": False, "description": "true = only currently active seasons"},
                ],
                "response_fields": ["source", "results", "leagues"],
                "example": f"GET {base}/api/jarvis/leagues?search=premier&country=England",
            },
            {
                "method": "GET",
                "path": "/api/jarvis/teams",
                "auth_required": True,
                "description": "Search for teams by name or within a league to get their IDs.",
                "params": [
                    {"name": "search", "type": "string",  "required": False, "description": "Partial team name, e.g. 'Arsenal'"},
                    {"name": "league", "type": "integer", "required": False, "description": "Filter to teams in a specific league"},
                    {"name": "season", "type": "integer", "required": False, "description": "Season year"},
                    {"name": "team",   "type": "integer", "required": False, "description": "Specific team ID to look up"},
                ],
                "response_fields": ["source", "results", "teams"],
                "example": f"GET {base}/api/jarvis/teams?search=Arsenal",
            },
            {
                "method": "GET",
                "path": "/api/jarvis/standings",
                "auth_required": True,
                "description": "Current league standings / table.",
                "params": [
                    {"name": "league", "type": "integer", "required": True,  "description": "League ID"},
                    {"name": "season", "type": "integer", "required": True,  "description": "Season year"},
                    {"name": "team",   "type": "integer", "required": False, "description": "Filter to a specific team's standing"},
                ],
                "response_fields": ["source", "league", "season", "standings"],
                "example": f"GET {base}/api/jarvis/standings?league=39&season=2025",
            },
            {
                "method": "GET",
                "path": "/api/jarvis/players",
                "auth_required": True,
                "description": "Season statistics for a player. Requires both player ID and season.",
                "params": [
                    {"name": "player", "type": "integer", "required": True,  "description": "Player ID"},
                    {"name": "season", "type": "integer", "required": True,  "description": "Season year"},
                    {"name": "league", "type": "integer", "required": False, "description": "Filter stats to a specific league"},
                ],
                "response_fields": ["source", "results", "players"],
                "example": f"GET {base}/api/jarvis/players?player=276&season=2025",
            },
            {
                "method": "GET",
                "path": "/api/jarvis/player/fixtures",
                "auth_required": True,
                "description": "Recent match history for a player in a specific league and season.",
                "params": [
                    {"name": "player", "type": "integer", "required": True,  "description": "Player ID"},
                    {"name": "league", "type": "integer", "required": True,  "description": "League ID"},
                    {"name": "season", "type": "integer", "required": True,  "description": "Season year"},
                ],
                "response_fields": ["source", "results", "fixtures"],
                "example": f"GET {base}/api/jarvis/player/fixtures?player=276&league=39&season=2025",
            },
        ],
        "common_league_ids": {
            "Premier League (England)": 39,
            "La Liga (Spain)": 140,
            "Serie A (Italy)": 135,
            "Bundesliga (Germany)": 78,
            "Ligue 1 (France)": 61,
            "Champions League": 2,
            "Europa League": 3,
            "MLS (USA)": 253,
            "FIFA World Cup": 1,
        },
    })


# ─────────────────────────────────────────────────────────────────────────────
# Authenticated data endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/api/jarvis/fixtures")
async def jarvis_fixtures(
    authorization: Optional[str] = Header(default=None),
    league:  Optional[int] = Query(None),
    season:  Optional[int] = Query(None),
    date:    Optional[str] = Query(None),
    team:    Optional[int] = Query(None),
    fixture: Optional[int] = Query(None),
    next:    Optional[int] = Query(None),
    last:    Optional[int] = Query(None),
    live:    Optional[str] = Query(None),
):
    """Return football fixtures. See /api/jarvis/docs for full parameter reference."""
    _require_auth(authorization)

    params: dict = {}
    if league  is not None: params["league"] = league
    if season  is not None: params["season"] = season
    if date    is not None: params["date"]   = date
    if team    is not None: params["team"]   = team
    if fixture is not None: params["id"]     = fixture
    if next    is not None: params["next"]   = next
    if last    is not None: params["last"]   = last
    if live    is not None: params["live"]   = live

    if not params:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "At least one query param is required.",
                "valid_params": ["league", "season", "date", "team", "fixture", "next", "last", "live"],
                "docs": "/api/jarvis/docs",
            },
        )

    data = await _sports_get("fixtures", params)
    return JSONResponse(content={
        "source": "api-sports/fixtures",
        "results": data.get("results", 0),
        "fixtures": data.get("response", []),
    })


@router.get("/api/jarvis/leagues")
async def jarvis_leagues(
    authorization: Optional[str] = Header(default=None),
    search:  Optional[str]  = Query(None),
    country: Optional[str]  = Query(None),
    league:  Optional[int]  = Query(None),
    current: Optional[bool] = Query(None),
):
    """
    Return league catalogue. See /api/jarvis/docs for full parameter reference.

    NOTE: `search` and `country` are mutually exclusive on the upstream API —
    use only one at a time.  `search` is preferred for name lookups.
    """
    _require_auth(authorization)

    # API-Sports ignores `country` when `search` is also provided — prefer
    # search so the caller always gets results.
    params: dict = {}
    if search  is not None:
        params["search"] = search
    elif country is not None:
        params["country"] = country
    if league  is not None: params["id"]      = league
    if current is not None: params["current"] = "true" if current else "false"

    data = await _sports_get("leagues", params)
    leagues = data.get("response", [])
    # If a country filter was requested but search took priority, post-filter
    if search and country:
        country_lower = country.lower()
        leagues = [
            l for l in leagues
            if country_lower in (l.get("country", {}).get("name") or "").lower()
        ]
    return JSONResponse(content={
        "source": "api-sports/leagues",
        "results": len(leagues),
        "leagues": leagues,
        "note": (
            "Use `search` for name lookup, `country` for country filter. "
            "Combining both applies country as a post-filter on the search results."
        ),
    })


@router.get("/api/jarvis/teams")
async def jarvis_teams(
    authorization: Optional[str] = Header(default=None),
    search: Optional[str] = Query(None),
    league: Optional[int] = Query(None),
    season: Optional[int] = Query(None),
    team:   Optional[int] = Query(None),
):
    """Return team catalogue. See /api/jarvis/docs for full parameter reference."""
    _require_auth(authorization)

    params: dict = {}
    if search is not None: params["search"] = search
    if league is not None: params["league"] = league
    if season is not None: params["season"] = season
    if team   is not None: params["id"]     = team

    data = await _sports_get("teams", params)
    return JSONResponse(content={
        "source": "api-sports/teams",
        "results": data.get("results", 0),
        "teams": data.get("response", []),
    })


@router.get("/api/jarvis/standings")
async def jarvis_standings(
    authorization: Optional[str] = Header(default=None),
    league: int = Query(..., description="League ID (required)"),
    season: int = Query(..., description="Season year (required)"),
    team:   Optional[int] = Query(None, description="Filter to a specific team"),
):
    """Return league standings table. See /api/jarvis/docs for full parameter reference."""
    _require_auth(authorization)

    params: dict = {"league": league, "season": season}
    if team is not None:
        params["team"] = team

    data = await _sports_get("standings", params)
    raw = data.get("response", [])

    # Flatten the nested standings structure for easier AI consumption
    standings_out = []
    for entry in raw:
        league_info = entry.get("league", {})
        for group in league_info.get("standings", []):
            standings_out.extend(group)

    return JSONResponse(content={
        "source": "api-sports/standings",
        "league": league,
        "season": season,
        "standings": standings_out,
    })


@router.get("/api/jarvis/players")
async def jarvis_players(
    authorization: Optional[str] = Header(default=None),
    player: int = Query(..., description="Player ID (required)"),
    season: int = Query(..., description="Season year (required)"),
    league: Optional[int] = Query(None, description="Filter stats to a specific league"),
):
    """Return season statistics for a player. See /api/jarvis/docs for full parameter reference."""
    _require_auth(authorization)

    params: dict = {"id": player, "season": season}
    if league is not None:
        params["league"] = league

    data = await _sports_get("players", params)
    return JSONResponse(content={
        "source": "api-sports/players",
        "results": data.get("results", 0),
        "players": data.get("response", []),
    })


@router.get("/api/jarvis/player/fixtures")
async def jarvis_player_fixtures(
    authorization: Optional[str] = Header(default=None),
    player: int = Query(..., description="Player ID (required)"),
    league: int = Query(..., description="League ID (required)"),
    season: int = Query(..., description="Season year (required)"),
):
    """
    Return a player's recent match history.

    Uses a 2-step lookup: fetches the player's team from their season stats,
    then returns that team's last 10 fixtures in the given league/season.
    API-Sports does not support filtering /fixtures directly by player ID.
    """
    _require_auth(authorization)

    # Step 1 — resolve the player's team ID for this league/season
    player_data = await _sports_get("players", {
        "id":     player,
        "season": season,
        "league": league,
    })
    player_rows = player_data.get("response", [])
    team_id: Optional[int] = None
    player_name: Optional[str] = None
    if player_rows:
        first = player_rows[0]
        player_name = first.get("player", {}).get("name")
        stats = first.get("statistics", [])
        if stats:
            team_id = stats[0].get("team", {}).get("id")

    if not team_id:
        return JSONResponse(content={
            "source": "api-sports/player-fixtures",
            "player": player,
            "league": league,
            "season": season,
            "results": 0,
            "fixtures": [],
            "note": (
                "Could not resolve this player's team for the given league/season. "
                "Verify the player ID, league ID, and season year."
            ),
        })

    # Step 2 — fetch the team's last 10 fixtures in this league/season
    fix_data = await _sports_get("fixtures", {
        "team":   team_id,
        "league": league,
        "season": season,
        "last":   10,
    })
    fixtures = fix_data.get("response", [])

    return JSONResponse(content={
        "source": "api-sports/player-fixtures",
        "player": player,
        "player_name": player_name,
        "team_id": team_id,
        "league": league,
        "season": season,
        "results": len(fixtures),
        "fixtures": fixtures,
        "note": (
            "Fixtures are for the player's team — individual player stats "
            "per fixture are not included in this endpoint."
        ),
    })
