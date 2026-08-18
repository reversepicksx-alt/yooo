"""
JARVIS Integration Layer — secure proxy to API-Sports football data.

Authentication
--------------
Every request (except GET /api/jarvis/health, /api/jarvis/docs, and
/api/jarvis/openapi.json) requires:

    Authorization: Bearer <JARVIS_API_KEY>

API_SPORTS_KEY and JARVIS_API_KEY live in Replit Secrets and are never
returned, logged, or echoed in any response.

Endpoints
---------
Public (no auth):
  GET /api/jarvis/health
  GET /api/jarvis/docs
  GET /api/jarvis/openapi.json

Catalogue / search:
  GET /api/jarvis/fixtures
  GET /api/jarvis/leagues
  GET /api/jarvis/teams
  GET /api/jarvis/standings
  GET /api/jarvis/players
  GET /api/jarvis/player/fixtures

Per-fixture detail:
  GET /api/jarvis/fixture/stats
  GET /api/jarvis/fixture/events
  GET /api/jarvis/fixture/lineups
  GET /api/jarvis/injuries
  GET /api/jarvis/team/stats
  GET /api/jarvis/h2h
  GET /api/jarvis/odds

Aggregator:
  GET /api/jarvis/match-context   ← single fixture ID → full AI brief
"""
from __future__ import annotations

import asyncio
import os
import time
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import JSONResponse

router = APIRouter()

# ── Config ────────────────────────────────────────────────────────────────────
_API_SPORTS_BASE = "https://v3.football.api-sports.io"
_API_SPORTS_KEY  = os.environ.get("API_SPORTS_KEY", "")
_JARVIS_KEY      = os.environ.get("JARVIS_API_KEY", "")

# ── Simple TTL cache (in-memory, per-process) ─────────────────────────────────
# Keeps repeated match-context calls from burning quota on the same fixture.
_CACHE: dict[str, tuple[Any, float]] = {}
_CACHE_TTL_LIVE      = 90     # seconds — in-progress match
_CACHE_TTL_SCHEDULED = 300    # seconds — upcoming fixture
_CACHE_TTL_FINISHED  = 1800   # seconds — completed fixture


def _cache_get(key: str) -> Any | None:
    entry = _CACHE.get(key)
    if entry and time.time() < entry[1]:
        return entry[0]
    return None


def _cache_set(key: str, value: Any, ttl: int) -> None:
    _CACHE[key] = (value, time.time() + ttl)


# ── Auth ──────────────────────────────────────────────────────────────────────

def _require_auth(authorization: Optional[str]) -> None:
    if not _JARVIS_KEY:
        raise HTTPException(503, detail={"error": "JARVIS_API_KEY not configured on server."})
    if not authorization:
        raise HTTPException(401, detail={"error": "Missing Authorization header.", "format": "Authorization: Bearer <JARVIS_API_KEY>"})
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or token.strip() != _JARVIS_KEY:
        raise HTTPException(401, detail={"error": "Invalid JARVIS API key."})


# ── API-Sports helper ─────────────────────────────────────────────────────────

async def _sports_get(endpoint: str, params: dict, *, cache_ttl: int = 0) -> dict:
    """Call API-Sports and return parsed JSON. Raises on upstream errors."""
    if not _API_SPORTS_KEY:
        raise HTTPException(503, detail={"error": "API_SPORTS_KEY not configured on server."})

    cache_key = f"{endpoint}:{sorted(params.items())}"
    if cache_ttl:
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached

    url = f"{_API_SPORTS_BASE}/{endpoint}"
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, headers={"x-apisports-key": _API_SPORTS_KEY}, params=params)

    if resp.status_code == 429:
        raise HTTPException(429, detail={"error": "API-Sports daily quota exhausted. Resets at midnight UTC."})
    if resp.status_code != 200:
        raise HTTPException(502, detail={"error": f"API-Sports returned HTTP {resp.status_code}."})

    data = resp.json()
    errors = data.get("errors", {})
    if errors and errors != [] and errors != {}:
        raise HTTPException(422, detail={"error": "API-Sports parameter error.", "details": errors})

    if cache_ttl:
        _cache_set(cache_key, data, cache_ttl)
    return data


async def _sports_get_safe(endpoint: str, params: dict, *, cache_ttl: int = 0) -> dict | None:
    """Like _sports_get but returns None instead of raising — for aggregator sub-fetches."""
    try:
        return await _sports_get(endpoint, params, cache_ttl=cache_ttl)
    except Exception:
        return None


# ── Helper: resolve a fixture to its core identity ───────────────────────────

async def _resolve_fixture(fixture_id: int) -> dict:
    """
    Return {fixture, home_team, away_team, league_id, season, status_short}
    from a fixture ID. Raises 404 if not found.
    """
    data = await _sports_get("fixtures", {"id": fixture_id}, cache_ttl=_CACHE_TTL_SCHEDULED)
    rows = data.get("response", [])
    if not rows:
        raise HTTPException(404, detail={"error": f"Fixture {fixture_id} not found."})
    f = rows[0]
    fix   = f.get("fixture", {})
    teams = f.get("teams", {})
    league = f.get("league", {})
    return {
        "fixture_id":    fix.get("id"),
        "date":          fix.get("date"),
        "status_short":  fix.get("status", {}).get("short", "NS"),
        "venue":         fix.get("venue", {}).get("name"),
        "city":          fix.get("venue", {}).get("city"),
        "home_team_id":  teams.get("home", {}).get("id"),
        "home_team":     teams.get("home", {}).get("name"),
        "away_team_id":  teams.get("away", {}).get("id"),
        "away_team":     teams.get("away", {}).get("name"),
        "league_id":     league.get("id"),
        "league_name":   league.get("name"),
        "country":       league.get("country"),
        "season":        league.get("season"),
        "round":         league.get("round"),
    }


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC endpoints (no auth)
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/api/jarvis/health")
async def jarvis_health():
    """Health check — no authentication required."""
    return JSONResponse(content={
        "status": "ok",
        "service": "jarvis",
        "timestamp": int(time.time()),
        "auth": {
            "jarvis_key_configured": bool(_JARVIS_KEY),
            "api_sports_configured": bool(_API_SPORTS_KEY),
        },
        "note": "All data endpoints require: Authorization: Bearer <JARVIS_API_KEY>",
    })


@router.get("/api/jarvis/openapi.json", include_in_schema=False)
async def jarvis_openapi():
    """OpenAPI 3.1.0 schema — import this URL directly into a ChatGPT Custom GPT Action."""
    base = "https://7a030359-7bf3-4fa1-8914-cbee61d63eb2-00-1w1w9xi7usfsw.picard.replit.dev"

    def _param(name, typ, req, desc):
        p = {"name": name, "in": "query", "schema": {"type": typ}, "description": desc}
        if req:
            p["required"] = True
        return p

    fixture_param   = _param("fixture", "integer", True,  "Fixture ID.")
    fixture_param_o = _param("fixture", "integer", False, "Fixture ID.")

    return JSONResponse(content={
        "openapi": "3.1.0",
        "info": {
            "title": "JARVIS Football API",
            "description": (
                "Secure proxy to real-time football data via API-Sports. "
                "Provides fixtures, league tables, team info, player stats, "
                "match events, lineups, H2H, odds, injuries, and a one-call "
                "match-context aggregator for AI analysis. "
                "All data endpoints require Authorization: Bearer <JARVIS_API_KEY>."
            ),
            "version": "3.0.0",
        },
        "servers": [{"url": base}],
        "components": {
            "schemas": {},
            "securitySchemes": {
                "BearerAuth": {
                    "type": "http",
                    "scheme": "bearer",
                    "description": "Enter your JARVIS_API_KEY as the bearer token.",
                }
            },
        },
        "security": [{"BearerAuth": []}],
        "paths": {
            "/api/jarvis/health": {
                "get": {
                    "operationId": "getHealth",
                    "summary": "Server health check (no auth)",
                    "security": [],
                    "responses": {"200": {"description": "Health status"}},
                }
            },
            # ── AGGREGATOR ────────────────────────────────────────────────────
            "/api/jarvis/match-context": {
                "get": {
                    "operationId": "getMatchContext",
                    "summary": "Full match brief from a single fixture ID",
                    "description": (
                        "The primary JARVIS tool. From one fixture ID it automatically "
                        "resolves teams, league, and season, then gathers fixture details, "
                        "team season stats (both sides), head-to-head history, starting "
                        "lineups, injuries, pre-match odds, live match statistics, and "
                        "match events — all in one clean JSON package designed for AI "
                        "analysis. Unavailable data sections are null rather than errors."
                    ),
                    "parameters": [fixture_param],
                    "responses": {
                        "200": {"description": "Full match context bundle"},
                        "401": {"description": "Invalid or missing bearer token"},
                        "404": {"description": "Fixture not found"},
                    },
                }
            },
            # ── FIXTURE DETAIL ────────────────────────────────────────────────
            "/api/jarvis/fixture/stats": {
                "get": {
                    "operationId": "getFixtureStats",
                    "summary": "Team statistics for a specific match",
                    "description": "Returns possession, shots, passes, cards, xG and other match stats for both teams.",
                    "parameters": [fixture_param],
                    "responses": {"200": {"description": "Match statistics"}, "401": {"description": "Unauthorized"}},
                }
            },
            "/api/jarvis/fixture/events": {
                "get": {
                    "operationId": "getFixtureEvents",
                    "summary": "Match events (goals, cards, substitutions)",
                    "description": "Returns all in-match events with minute, team, player, and event type.",
                    "parameters": [fixture_param],
                    "responses": {"200": {"description": "Match events"}, "401": {"description": "Unauthorized"}},
                }
            },
            "/api/jarvis/fixture/lineups": {
                "get": {
                    "operationId": "getFixtureLineups",
                    "summary": "Starting lineups, formations, and substitutes",
                    "description": "Returns confirmed starting XI, formation, bench, and coach for both teams.",
                    "parameters": [fixture_param],
                    "responses": {"200": {"description": "Lineups"}, "401": {"description": "Unauthorized"}},
                }
            },
            "/api/jarvis/injuries": {
                "get": {
                    "operationId": "getInjuries",
                    "summary": "Injury and absence report for a fixture",
                    "description": "Returns players marked as injured or suspended ahead of the fixture.",
                    "parameters": [fixture_param_o,
                                   _param("team",   "integer", False, "Filter to a specific team."),
                                   _param("league", "integer", False, "Filter by league ID."),
                                   _param("season", "integer", False, "Season year.")],
                    "responses": {"200": {"description": "Injury list"}, "401": {"description": "Unauthorized"}},
                }
            },
            "/api/jarvis/odds": {
                "get": {
                    "operationId": "getOdds",
                    "summary": "Pre-match odds for a fixture",
                    "description": "Returns available bookmaker odds including 1X2, Asian handicap, and over/under markets.",
                    "parameters": [fixture_param],
                    "responses": {"200": {"description": "Odds data"}, "401": {"description": "Unauthorized"}},
                }
            },
            # ── TEAM / HISTORY ────────────────────────────────────────────────
            "/api/jarvis/team/stats": {
                "get": {
                    "operationId": "getTeamStats",
                    "summary": "Season-level team statistics",
                    "description": "Returns a team's season stats: matches played, goals, form, home/away splits, clean sheets, average goals, and more.",
                    "parameters": [
                        _param("team",   "integer", True, "Team ID."),
                        _param("league", "integer", True, "League ID."),
                        _param("season", "integer", True, "Season year."),
                    ],
                    "responses": {"200": {"description": "Team season stats"}, "401": {"description": "Unauthorized"}},
                }
            },
            "/api/jarvis/h2h": {
                "get": {
                    "operationId": "getH2H",
                    "summary": "Head-to-head fixture history between two teams",
                    "description": "Returns recent meetings between two teams including scores, venues, and dates.",
                    "parameters": [
                        _param("team1", "integer", True,  "First team ID."),
                        _param("team2", "integer", True,  "Second team ID."),
                        _param("last",  "integer", False, "Number of most recent meetings to return (default 10)."),
                    ],
                    "responses": {"200": {"description": "H2H history"}, "401": {"description": "Unauthorized"}},
                }
            },
            # ── CATALOGUE / SEARCH ────────────────────────────────────────────
            "/api/jarvis/fixtures": {
                "get": {
                    "operationId": "getFixtures",
                    "summary": "Search / filter football fixtures",
                    "description": "Retrieve fixtures by date, league, team, live status, or fixture ID. At least one parameter required.",
                    "parameters": [
                        _param("league",  "integer", False, "League ID."),
                        _param("season",  "integer", False, "Season year, e.g. 2025."),
                        _param("date",    "string",  False, "Date in YYYY-MM-DD format."),
                        _param("team",    "integer", False, "Team ID."),
                        _param("fixture", "integer", False, "Specific fixture ID."),
                        _param("next",    "integer", False, "Next N upcoming fixtures (max 20)."),
                        _param("last",    "integer", False, "Last N completed fixtures (max 20)."),
                        _param("live",    "string",  False, "'all' or a league ID for live fixtures."),
                    ],
                    "responses": {"200": {"description": "Fixture list"}, "401": {"description": "Unauthorized"}},
                }
            },
            "/api/jarvis/leagues": {
                "get": {
                    "operationId": "getLeagues",
                    "summary": "Look up league IDs",
                    "description": "Search leagues by name or country. Use search OR country, not both.",
                    "parameters": [
                        _param("search",  "string",  False, "Partial league name."),
                        _param("country", "string",  False, "Country name (use search OR country)."),
                        _param("league",  "integer", False, "Specific league ID."),
                        _param("current", "boolean", False, "true = active seasons only."),
                    ],
                    "responses": {"200": {"description": "League list"}, "401": {"description": "Unauthorized"}},
                }
            },
            "/api/jarvis/teams": {
                "get": {
                    "operationId": "getTeams",
                    "summary": "Look up team IDs",
                    "description": "Search for teams by name or within a league.",
                    "parameters": [
                        _param("search", "string",  False, "Partial team name."),
                        _param("league", "integer", False, "League ID."),
                        _param("season", "integer", False, "Season year."),
                        _param("team",   "integer", False, "Specific team ID."),
                    ],
                    "responses": {"200": {"description": "Team list"}, "401": {"description": "Unauthorized"}},
                }
            },
            "/api/jarvis/standings": {
                "get": {
                    "operationId": "getStandings",
                    "summary": "League standings table",
                    "parameters": [
                        _param("league", "integer", True,  "League ID."),
                        _param("season", "integer", True,  "Season year."),
                        _param("team",   "integer", False, "Filter to one team's row."),
                    ],
                    "responses": {"200": {"description": "Standings"}, "401": {"description": "Unauthorized"}},
                }
            },
            "/api/jarvis/players": {
                "get": {
                    "operationId": "getPlayerStats",
                    "summary": "Player season statistics",
                    "parameters": [
                        _param("player", "integer", True,  "Player ID."),
                        _param("season", "integer", True,  "Season year."),
                        _param("league", "integer", False, "Filter to a league."),
                    ],
                    "responses": {"200": {"description": "Player stats"}, "401": {"description": "Unauthorized"}},
                }
            },
            "/api/jarvis/player/fixtures": {
                "get": {
                    "operationId": "getPlayerFixtures",
                    "summary": "Player's recent match history",
                    "description": "Resolves the player's team automatically, then returns last 10 fixtures.",
                    "parameters": [
                        _param("player", "integer", True, "Player ID."),
                        _param("league", "integer", True, "League ID."),
                        _param("season", "integer", True, "Season year."),
                    ],
                    "responses": {"200": {"description": "Recent fixtures"}, "401": {"description": "Unauthorized"}},
                }
            },
        },
    })


@router.get("/api/jarvis/docs")
async def jarvis_docs():
    """Full API reference — no authentication required."""
    base = "https://7a030359-7bf3-4fa1-8914-cbee61d63eb2-00-1w1w9xi7usfsw.picard.replit.dev"
    return JSONResponse(content={
        "service": "JARVIS Football API",
        "version": "3.0.0",
        "base_url": base,
        "openapi_schema": f"{base}/api/jarvis/openapi.json",
        "authentication": {
            "type": "Bearer token",
            "header": "Authorization",
            "format": "Authorization: Bearer <JARVIS_API_KEY>",
            "note": "health, docs, and openapi.json endpoints do not require authentication.",
        },
        "endpoint_groups": {
            "public": ["/api/jarvis/health", "/api/jarvis/docs", "/api/jarvis/openapi.json"],
            "aggregator": ["/api/jarvis/match-context"],
            "fixture_detail": [
                "/api/jarvis/fixture/stats",
                "/api/jarvis/fixture/events",
                "/api/jarvis/fixture/lineups",
                "/api/jarvis/injuries",
                "/api/jarvis/odds",
            ],
            "team_history": ["/api/jarvis/team/stats", "/api/jarvis/h2h"],
            "catalogue": [
                "/api/jarvis/fixtures",
                "/api/jarvis/leagues",
                "/api/jarvis/teams",
                "/api/jarvis/standings",
                "/api/jarvis/players",
                "/api/jarvis/player/fixtures",
            ],
        },
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
# AGGREGATOR — match-context
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/api/jarvis/match-context")
async def jarvis_match_context(
    authorization: Optional[str] = Header(default=None),
    fixture: int = Query(..., description="Fixture ID — everything is resolved from this."),
):
    """
    Primary JARVIS tool.  One fixture ID → full AI analysis brief.

    Resolves teams/league/season automatically then fetches in parallel:
    team season stats (both sides), H2H, lineups, injuries, odds,
    match statistics, and match events.  Each section is null if unavailable
    rather than failing the whole response.
    """
    _require_auth(authorization)

    # Step 1 — resolve fixture identity
    ctx = await _resolve_fixture(fixture)

    status   = ctx["status_short"]
    is_live  = status in ("1H", "HT", "2H", "ET", "BT", "P", "INT", "LIVE")
    finished = status in ("FT", "AET", "PEN")
    ttl      = _CACHE_TTL_LIVE if is_live else (_CACHE_TTL_FINISHED if finished else _CACHE_TTL_SCHEDULED)

    home_id  = ctx["home_team_id"]
    away_id  = ctx["away_team_id"]
    league   = ctx["league_id"]
    season   = ctx["season"]

    # Step 2 — parallel fetch of all sub-sections
    (
        stats_raw,
        events_raw,
        lineups_raw,
        injuries_raw,
        odds_raw,
        home_stats_raw,
        away_stats_raw,
        h2h_raw,
    ) = await asyncio.gather(
        _sports_get_safe("fixtures/statistics", {"fixture": fixture}, cache_ttl=ttl),
        _sports_get_safe("fixtures/events",     {"fixture": fixture}, cache_ttl=ttl),
        _sports_get_safe("fixtures/lineups",    {"fixture": fixture}, cache_ttl=ttl),
        _sports_get_safe("injuries",            {"fixture": fixture}, cache_ttl=ttl),
        _sports_get_safe("odds",                {"fixture": fixture}, cache_ttl=_CACHE_TTL_SCHEDULED),
        _sports_get_safe("teams/statistics",    {"team": home_id, "league": league, "season": season}, cache_ttl=_CACHE_TTL_FINISHED),
        _sports_get_safe("teams/statistics",    {"team": away_id, "league": league, "season": season}, cache_ttl=_CACHE_TTL_FINISHED),
        _sports_get_safe("fixtures/headtohead", {"h2h": f"{home_id}-{away_id}", "last": 10},           cache_ttl=_CACHE_TTL_FINISHED),
    )

    # Step 3 — clean and shape each section
    def _stats(raw):
        if not raw:
            return None
        rows = raw.get("response", [])
        out = {}
        for team_block in rows:
            name = team_block.get("team", {}).get("name", "unknown")
            out[name] = {s["type"]: s["value"] for s in team_block.get("statistics", [])}
        return out or None

    def _events(raw):
        if not raw:
            return None
        return raw.get("response") or None

    def _lineups(raw):
        if not raw:
            return None
        rows = raw.get("response", [])
        out = {}
        for t in rows:
            name = t.get("team", {}).get("name", "unknown")
            out[name] = {
                "formation":   t.get("formation"),
                "coach":       t.get("coach", {}).get("name"),
                "start_xi":    [{"name": p["player"]["name"], "number": p["player"]["number"], "pos": p["player"]["pos"], "grid": p["player"]["grid"]} for p in t.get("startXI", [])],
                "substitutes": [{"name": p["player"]["name"], "number": p["player"]["number"], "pos": p["player"]["pos"]} for p in t.get("substitutes", [])],
            }
        return out or None

    def _injuries(raw):
        if not raw:
            return None
        rows = raw.get("response", [])
        return [
            {
                "player": r.get("player", {}).get("name"),
                "team":   r.get("team",   {}).get("name"),
                "type":   r.get("player", {}).get("type"),
                "reason": r.get("player", {}).get("reason"),
            }
            for r in rows
        ] or None

    def _odds(raw):
        if not raw:
            return None
        rows = raw.get("response", [])
        if not rows:
            return None
        out = []
        for bm in rows[0].get("bookmakers", [])[:3]:  # top 3 bookmakers
            for mkt in bm.get("bets", []):
                if mkt["name"] in ("Match Winner", "Goals Over/Under", "Asian Handicap"):
                    out.append({
                        "bookmaker": bm["name"],
                        "market":    mkt["name"],
                        "values":    mkt.get("values", []),
                    })
        return out or None

    def _team_stats(raw):
        if not raw:
            return None
        r = raw.get("response", {})
        if not r:
            return None
        return {
            "team":    r.get("team", {}).get("name"),
            "form":    r.get("form"),
            "fixtures": r.get("fixtures", {}),
            "goals":   r.get("goals", {}),
            "biggest": r.get("biggest", {}),
            "clean_sheet": r.get("clean_sheet", {}),
            "failed_to_score": r.get("failed_to_score", {}),
            "average_goals": r.get("goals", {}).get("for", {}).get("average", {}),
        }

    def _h2h(raw):
        if not raw:
            return None
        rows = raw.get("response", [])
        meetings = []
        for f in rows:
            fix    = f.get("fixture", {})
            teams  = f.get("teams", {})
            goals  = f.get("goals", {})
            score  = f.get("score", {})
            meetings.append({
                "date":      fix.get("date", "")[:10],
                "venue":     fix.get("venue", {}).get("name"),
                "home":      teams.get("home", {}).get("name"),
                "away":      teams.get("away", {}).get("name"),
                "score":     f"{goals.get('home')}-{goals.get('away')}",
                "winner":    teams.get("home", {}).get("name") if teams.get("home", {}).get("winner") else (teams.get("away", {}).get("name") if teams.get("away", {}).get("winner") else "Draw"),
                "halftime":  f"{score.get('halftime', {}).get('home')}-{score.get('halftime', {}).get('away')}",
            })
        return meetings or None

    return JSONResponse(content={
        "source": "jarvis/match-context",
        "generated_at": int(time.time()),
        "fixture": ctx,
        "match_statistics":   _stats(stats_raw),
        "match_events":       _events(events_raw),
        "lineups":            _lineups(lineups_raw),
        "injuries":           _injuries(injuries_raw),
        "odds":               _odds(odds_raw),
        "home_season_stats":  _team_stats(home_stats_raw),
        "away_season_stats":  _team_stats(away_stats_raw),
        "head_to_head":       _h2h(h2h_raw),
    })


# ─────────────────────────────────────────────────────────────────────────────
# FIXTURE DETAIL — individual endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/api/jarvis/fixture/stats")
async def jarvis_fixture_stats(
    authorization: Optional[str] = Header(default=None),
    fixture: int = Query(...),
):
    """Team match statistics for a specific fixture (possession, shots, passes, xG, cards…)."""
    _require_auth(authorization)
    data = await _sports_get("fixtures/statistics", {"fixture": fixture}, cache_ttl=_CACHE_TTL_LIVE)
    rows = data.get("response", [])
    out  = {}
    for team_block in rows:
        name = team_block.get("team", {}).get("name", "unknown")
        out[name] = {s["type"]: s["value"] for s in team_block.get("statistics", [])}
    return JSONResponse(content={"source": "api-sports/fixture-stats", "fixture": fixture, "statistics": out})


@router.get("/api/jarvis/fixture/events")
async def jarvis_fixture_events(
    authorization: Optional[str] = Header(default=None),
    fixture: int = Query(...),
):
    """All match events: goals, cards, substitutions, VAR decisions."""
    _require_auth(authorization)
    data = await _sports_get("fixtures/events", {"fixture": fixture}, cache_ttl=_CACHE_TTL_LIVE)
    return JSONResponse(content={"source": "api-sports/fixture-events", "fixture": fixture, "events": data.get("response", [])})


@router.get("/api/jarvis/fixture/lineups")
async def jarvis_fixture_lineups(
    authorization: Optional[str] = Header(default=None),
    fixture: int = Query(...),
):
    """Starting lineups, formations, substitutes, and coaches for both teams."""
    _require_auth(authorization)
    data = await _sports_get("fixtures/lineups", {"fixture": fixture}, cache_ttl=_CACHE_TTL_SCHEDULED)
    rows = data.get("response", [])
    out  = {}
    for t in rows:
        name = t.get("team", {}).get("name", "unknown")
        out[name] = {
            "formation":   t.get("formation"),
            "coach":       t.get("coach", {}).get("name"),
            "start_xi":    [{"name": p["player"]["name"], "number": p["player"]["number"], "pos": p["player"]["pos"], "grid": p["player"]["grid"]} for p in t.get("startXI", [])],
            "substitutes": [{"name": p["player"]["name"], "number": p["player"]["number"], "pos": p["player"]["pos"]} for p in t.get("substitutes", [])],
        }
    return JSONResponse(content={"source": "api-sports/fixture-lineups", "fixture": fixture, "lineups": out})


@router.get("/api/jarvis/injuries")
async def jarvis_injuries(
    authorization: Optional[str] = Header(default=None),
    fixture: Optional[int] = Query(None),
    team:    Optional[int] = Query(None),
    league:  Optional[int] = Query(None),
    season:  Optional[int] = Query(None),
):
    """Injury and suspension report. Provide fixture ID for match-specific injuries."""
    _require_auth(authorization)
    if not any([fixture, team, league]):
        raise HTTPException(400, detail={"error": "Provide at least one of: fixture, team, or league+season."})
    params: dict = {}
    if fixture is not None: params["fixture"] = fixture
    if team    is not None: params["team"]    = team
    if league  is not None: params["league"]  = league
    if season  is not None: params["season"]  = season
    data = await _sports_get("injuries", params, cache_ttl=_CACHE_TTL_SCHEDULED)
    rows = data.get("response", [])
    out  = [
        {
            "player": r.get("player", {}).get("name"),
            "team":   r.get("team",   {}).get("name"),
            "type":   r.get("player", {}).get("type"),
            "reason": r.get("player", {}).get("reason"),
        }
        for r in rows
    ]
    return JSONResponse(content={"source": "api-sports/injuries", "results": len(out), "injuries": out})


@router.get("/api/jarvis/odds")
async def jarvis_odds(
    authorization: Optional[str] = Header(default=None),
    fixture: int = Query(...),
):
    """Pre-match bookmaker odds for a fixture (1X2, over/under, Asian handicap)."""
    _require_auth(authorization)
    data = await _sports_get("odds", {"fixture": fixture}, cache_ttl=_CACHE_TTL_SCHEDULED)
    rows = data.get("response", [])
    if not rows:
        return JSONResponse(content={"source": "api-sports/odds", "fixture": fixture, "results": 0, "odds": []})
    bookmakers = []
    for bm in rows[0].get("bookmakers", []):
        bookmakers.append({"bookmaker": bm["name"], "markets": bm.get("bets", [])})
    return JSONResponse(content={"source": "api-sports/odds", "fixture": fixture, "results": len(bookmakers), "odds": bookmakers})


# ─────────────────────────────────────────────────────────────────────────────
# TEAM / HISTORY
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/api/jarvis/team/stats")
async def jarvis_team_stats(
    authorization: Optional[str] = Header(default=None),
    team:   int = Query(...),
    league: int = Query(...),
    season: int = Query(...),
):
    """Season-level team statistics: matches, goals, form, home/away splits, clean sheets."""
    _require_auth(authorization)
    data = await _sports_get("teams/statistics", {"team": team, "league": league, "season": season}, cache_ttl=_CACHE_TTL_FINISHED)
    r = data.get("response", {})
    return JSONResponse(content={
        "source": "api-sports/team-stats",
        "team":   r.get("team", {}).get("name"),
        "league": r.get("league", {}).get("name"),
        "season": season,
        "form":   r.get("form"),
        "fixtures":         r.get("fixtures", {}),
        "goals":            r.get("goals", {}),
        "biggest":          r.get("biggest", {}),
        "clean_sheet":      r.get("clean_sheet", {}),
        "failed_to_score":  r.get("failed_to_score", {}),
        "penalty":          r.get("penalty", {}),
    })


@router.get("/api/jarvis/h2h")
async def jarvis_h2h(
    authorization: Optional[str] = Header(default=None),
    team1: int = Query(...),
    team2: int = Query(...),
    last:  int = Query(10, ge=1, le=20),
):
    """Head-to-head fixture history between two teams."""
    _require_auth(authorization)
    data = await _sports_get("fixtures/headtohead", {"h2h": f"{team1}-{team2}", "last": last}, cache_ttl=_CACHE_TTL_FINISHED)
    rows = data.get("response", [])
    meetings = []
    for f in rows:
        fix   = f.get("fixture", {})
        teams = f.get("teams", {})
        goals = f.get("goals", {})
        score = f.get("score", {})
        meetings.append({
            "date":     fix.get("date", "")[:10],
            "venue":    fix.get("venue", {}).get("name"),
            "home":     teams.get("home", {}).get("name"),
            "away":     teams.get("away", {}).get("name"),
            "score":    f"{goals.get('home')}-{goals.get('away')}",
            "winner":   (
                teams.get("home", {}).get("name") if teams.get("home", {}).get("winner")
                else teams.get("away", {}).get("name") if teams.get("away", {}).get("winner")
                else "Draw"
            ),
            "halftime": f"{score.get('halftime', {}).get('home')}-{score.get('halftime', {}).get('away')}",
            "league":   f.get("league", {}).get("name"),
        })
    return JSONResponse(content={"source": "api-sports/h2h", "team1": team1, "team2": team2, "results": len(meetings), "meetings": meetings})


# ─────────────────────────────────────────────────────────────────────────────
# CATALOGUE / SEARCH (existing endpoints — unchanged behaviour)
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
        raise HTTPException(400, detail={"error": "At least one query param required.", "docs": "/api/jarvis/docs"})
    data = await _sports_get("fixtures", params)
    return JSONResponse(content={"source": "api-sports/fixtures", "results": data.get("results", 0), "fixtures": data.get("response", [])})


@router.get("/api/jarvis/leagues")
async def jarvis_leagues(
    authorization: Optional[str] = Header(default=None),
    search:  Optional[str]  = Query(None),
    country: Optional[str]  = Query(None),
    league:  Optional[int]  = Query(None),
    current: Optional[bool] = Query(None),
):
    _require_auth(authorization)
    params: dict = {}
    if search  is not None:
        params["search"] = search
    elif country is not None:
        params["country"] = country
    if league  is not None: params["id"]      = league
    if current is not None: params["current"] = "true" if current else "false"
    data = await _sports_get("leagues", params)
    leagues = data.get("response", [])
    if search and country:
        country_lower = country.lower()
        leagues = [l for l in leagues if country_lower in (l.get("country", {}).get("name") or "").lower()]
    return JSONResponse(content={"source": "api-sports/leagues", "results": len(leagues), "leagues": leagues})


@router.get("/api/jarvis/teams")
async def jarvis_teams(
    authorization: Optional[str] = Header(default=None),
    search: Optional[str] = Query(None),
    league: Optional[int] = Query(None),
    season: Optional[int] = Query(None),
    team:   Optional[int] = Query(None),
):
    _require_auth(authorization)
    params: dict = {}
    if search is not None: params["search"] = search
    if league is not None: params["league"] = league
    if season is not None: params["season"] = season
    if team   is not None: params["id"]     = team
    data = await _sports_get("teams", params)
    return JSONResponse(content={"source": "api-sports/teams", "results": data.get("results", 0), "teams": data.get("response", [])})


@router.get("/api/jarvis/standings")
async def jarvis_standings(
    authorization: Optional[str] = Header(default=None),
    league: int = Query(...),
    season: int = Query(...),
    team:   Optional[int] = Query(None),
):
    _require_auth(authorization)
    params: dict = {"league": league, "season": season}
    if team is not None: params["team"] = team
    data = await _sports_get("standings", params, cache_ttl=_CACHE_TTL_FINISHED)
    standings_out = []
    for entry in data.get("response", []):
        for group in entry.get("league", {}).get("standings", []):
            standings_out.extend(group)
    return JSONResponse(content={"source": "api-sports/standings", "league": league, "season": season, "standings": standings_out})


@router.get("/api/jarvis/players")
async def jarvis_players(
    authorization: Optional[str] = Header(default=None),
    player: int = Query(...),
    season: int = Query(...),
    league: Optional[int] = Query(None),
):
    _require_auth(authorization)
    params: dict = {"id": player, "season": season}
    if league is not None: params["league"] = league
    data = await _sports_get("players", params, cache_ttl=_CACHE_TTL_FINISHED)
    return JSONResponse(content={"source": "api-sports/players", "results": data.get("results", 0), "players": data.get("response", [])})


@router.get("/api/jarvis/player/fixtures")
async def jarvis_player_fixtures(
    authorization: Optional[str] = Header(default=None),
    player: int = Query(...),
    league: int = Query(...),
    season: int = Query(...),
):
    _require_auth(authorization)
    player_data = await _sports_get("players", {"id": player, "season": season, "league": league})
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
        return JSONResponse(content={"source": "api-sports/player-fixtures", "player": player, "league": league, "season": season, "results": 0, "fixtures": [], "note": "Could not resolve player's team for this league/season."})
    fix_data = await _sports_get("fixtures", {"team": team_id, "league": league, "season": season, "last": 10})
    fixtures = fix_data.get("response", [])
    return JSONResponse(content={"source": "api-sports/player-fixtures", "player": player, "player_name": player_name, "team_id": team_id, "league": league, "season": season, "results": len(fixtures), "fixtures": fixtures})
