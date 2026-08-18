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
from fastapi import APIRouter, Body, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

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
            # ── PREDICT ───────────────────────────────────────────────────────
            "/api/jarvis/predict/soccer": {
                "post": {
                    "operationId": "runSoccerPredict",
                    "summary": "Full soccer prediction from fixture + player ID. Auto-resolves team, opponent, venue, league.",
                    "description": "Runs the complete 13-stage pipeline. Returns final recommendation, every Bayesian layer, each covariate, calibration, Monte Carlo, evidence quality, and the full factor ledger. Identical to the subscriber app.",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["fixture_id", "player_id", "prop_type", "line"],
                                    "properties": {
                                        "fixture_id": {"type": "integer", "description": "API-Sports fixture ID — auto-resolves team, opponent, venue, league."},
                                        "player_id":  {"type": "integer", "description": "API-Sports player ID."},
                                        "prop_type":  {"type": "string",  "description": "pass_attempts | passes | key_passes | shots | shots_on_target | tackles | clearances | saves | goals", "default": "pass_attempts"},
                                        "line":       {"type": "number",  "description": "Player prop line to predict against."},
                                        "odds":       {"type": "object",  "description": "Optional moneyline: {home: float, away: float, draw: float}."},
                                        "position_override": {"type": "string", "description": "Override detected position (e.g. CB, CM, ST)."},
                                        "role_override":     {"type": "string", "description": "Override detected role."},
                                    },
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": {"description": "Full diagnostic: final output, pre-calibration Bayesian, prior, momentum, venue, each covariate, posterior, positional squeeze, calibration layers, Monte Carlo, evidence quality, calibration alert, warnings, factor ledger, model fingerprint."},
                        "401": {"description": "Invalid or missing bearer token."},
                        "404": {"description": "Fixture not found."},
                        "422": {"description": "Could not resolve player in fixture, or invalid prop."},
                        "502": {"description": "Prediction engine error."},
                    },
                }
            },
            "/api/jarvis/predict": {
                "post": {
                    "operationId": "runPredict",
                    "summary": "Full Reverse Picks prediction from player + prop inputs",
                    "description": "Runs all 13 pipeline stages: Bayesian projection, situation engine, hierarchical calibration, evidence quality gate, and AI narrative. Returns the same output as the subscriber app.",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["player_id", "player_name", "team_id", "team_name", "opponent_id", "opponent_name", "league_id", "line"],
                                    "properties": {
                                        "player_id":     {"type": "integer", "description": "API-Sports player ID."},
                                        "player_name":   {"type": "string",  "description": "Player display name."},
                                        "team_id":       {"type": "integer", "description": "Player's team API-Sports ID."},
                                        "team_name":     {"type": "string",  "description": "Player's team name."},
                                        "opponent_id":   {"type": "integer", "description": "Opposing team API-Sports ID."},
                                        "opponent_name": {"type": "string",  "description": "Opposing team name."},
                                        "league_id":     {"type": "integer", "description": "League ID (e.g. 39 = Premier League)."},
                                        "line":          {"type": "number",  "description": "The player prop line to predict against."},
                                        "venue":         {"type": "string",  "description": "home or away (relative to the player's team).", "default": "home"},
                                        "prop_type":     {"type": "string",  "description": "pass_attempts | passes | key_passes | shots | shots_on_target | tackles | clearances | saves | goals", "default": "pass_attempts"},
                                        "sport":         {"type": "string",  "description": "Sport name.", "default": "soccer"},
                                        "fixture_id":    {"type": "integer", "description": "Optional verified fixture ID — speeds up identity resolution."},
                                        "odds":          {"type": "object",  "description": "Optional moneyline odds: {home: float, away: float, draw: float}."},
                                        "position_override": {"type": "string", "description": "Override detected position (e.g. CB, CM, ST)."},
                                        "role_override":     {"type": "string", "description": "Override detected role."},
                                    },
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": {"description": "Full prediction with jarvis_brief, bayesian_metrics, calibration, situation, evidence_quality, factors, and full_prediction."},
                        "401": {"description": "Invalid or missing bearer token."},
                        "422": {"description": "Invalid prop type or parameter."},
                        "502": {"description": "Prediction engine error."},
                    },
                }
            },
            # ── AGGREGATOR ────────────────────────────────────────────────────
            "/api/jarvis/match-context": {
                "get": {
                    "operationId": "getMatchContext",
                    "summary": "Full match brief from a single fixture ID",
                    "description": "One fixture ID returns a complete match brief: teams, season stats, H2H, lineups, injuries, odds, and live events bundled for AI analysis. Null sections mean data is unavailable.",
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
            "predict": ["/api/jarvis/predict/soccer", "/api/jarvis/predict"],
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
# PREDICT — full Reverse Picks engine via JARVIS
# ─────────────────────────────────────────────────────────────────────────────

class JarvisPredictBody(BaseModel):
    """Inputs required to run the full Reverse Picks prediction pipeline."""
    player_id:     int
    player_name:   str
    team_id:       int
    team_name:     str
    opponent_id:   int
    opponent_name: str
    league_id:     int
    line:          float
    venue:         str = "home"           # "home" or "away"
    prop_type:     str = "pass_attempts"  # pass_attempts | shots | key_passes | tackles | clearances | saves | goals
    sport:         str = "soccer"
    fixture_id:    Optional[int]  = None
    odds:          Optional[dict] = None  # {"home": float, "away": float, "draw": float} moneyline
    position_override: str = ""
    role_override:     str = ""


@router.post("/api/jarvis/predict")
async def jarvis_predict(
    body: JarvisPredictBody,
    authorization: Optional[str] = Header(default=None),
):
    """
    Run the full Reverse Picks prediction engine.

    Calls the exact same pipeline used by subscribers — all 13 stages including
    Bayesian projection, situation engine, hierarchical calibration, evidence
    quality gate, and AI tactical narrative.  No shortcuts or approximations.
    """
    _require_auth(authorization)

    if not _JARVIS_KEY:
        raise HTTPException(503, detail={"error": "JARVIS_API_KEY not configured."})

    # Lazy import to avoid circular imports at module load time
    from models import PredictionRequest
    from routes.predict import predict as _rp_predict

    req = PredictionRequest(
        email="_jarvis_service_",
        token=_JARVIS_KEY,
        leagueId=body.league_id,
        playerId=body.player_id,
        playerName=body.player_name,
        teamId=body.team_id,
        teamName=body.team_name,
        opponentId=body.opponent_id,
        opponentName=body.opponent_name,
        venue=body.venue,
        propType=body.prop_type,
        line=body.line,
        sport=body.sport,
        fixtureId=body.fixture_id,
        odds=body.odds,
        positionOverride=body.position_override,
        roleOverride=body.role_override,
    )

    try:
        result = await _rp_predict(req)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(502, detail={"error": f"Prediction engine error: {str(exc)}"})

    # predict() returns a dict; handle edge case where it returns a JSONResponse
    if hasattr(result, "body"):
        import json as _json
        result = _json.loads(result.body)

    # ── Extract curated JARVIS brief ──────────────────────────────────────────
    # Field names come from the real predict() response shape — confirmed live.
    bm = result.get("bayesianMetrics") or {}
    eq = result.get("evidenceQuality") or {}
    gs = result.get("gameSituation") or {}

    jarvis_brief = {
        "recommendation":        result.get("recommendation"),
        "confidence_level":      result.get("confidenceLevel"),
        "confidence_score":      result.get("confidenceScore"),
        "raw_confidence":        result.get("rawConfidence"),
        "projected_value":       result.get("projectedValue"),
        "most_likely_value":     result.get("mostLikelyValue"),
        "line":                  result.get("line"),
        # Edge — stored as edgeZ (z-score) and edgeRating (label)
        "edge_z":                result.get("edgeZ"),
        "edge_rating":           result.get("edgeRating"),
        "edge_rating_reason":    result.get("edgeRatingReason"),
        # Direction probability — stored as bayesianComponent (0-100 int)
        "direction_probability_pct": result.get("bayesianComponent"),
        "is_fallback":           result.get("isFallback", False),
        "prediction_status":     result.get("predictionStatus", "ok"),
        "coin_flip":             result.get("coinFlip", False),
        "low_conviction":        result.get("lowConviction", False),
        "sharp_summary":         result.get("sharpSummary"),
        "reasoning":             result.get("reasoning"),
        "tactical_breakdown":    result.get("tacticalBreakdown"),
        "consensus_note":        result.get("consensusNote"),
        "warnings":              result.get("tacticalAlerts", []),
        "data_quality_status":   (result.get("dataQuality") or {}).get("status"),
        "evidence_quality_level": eq.get("level") or eq.get("status"),
        "evidence_quality_score": eq.get("score"),
        "real_log_count":        bm.get("priorSamples"),
        "safety_rating":         result.get("safetyRating"),
        "line_deviation_band":   result.get("lineDeviationBand"),
        "line_deviation_hit_rate": result.get("lineDeviationHitRate"),
    }

    return JSONResponse(content={
        "source":       "jarvis/predict",
        "generated_at": int(time.time()),

        # Curated AI-ready summary
        "jarvis_brief": jarvis_brief,

        # All 3 Bayesian layer outputs + Monte Carlo
        "bayesian_metrics":  bm,
        "probability_curve": result.get("probabilityCurve", []),
        "landing_bands":     bm.get("landingBands") or result.get("landingBands"),
        "range_60":          result.get("range60"),
        "range_80":          result.get("range80"),

        # Calibration — stored as fusionApplied in the real response
        "calibration_applied": result.get("fusionApplied") or result.get("calibrationApplied"),

        # Situational adjustments (knockout, stakes, pressure multipliers)
        "game_situation": gs,

        # Evidence quality gate output
        "evidence_quality": eq,

        # Factor ledger — top-level key in the real response
        "factors": result.get("factorLedger") or result.get("factors") or bm.get("factorLedger"),

        # Model breakdown
        "model_breakdown": result.get("modelBreakdown"),
        "analysis_factors": result.get("analysisFactors"),
        "analysis_summary": result.get("analysisSummary"),

        # Match context
        "match_context":    result.get("matchContext"),
        "game_script":      result.get("gameScript"),
        "match_dominance":  result.get("matchDominance"),
        "match_factors":    result.get("matchFactors"),

        # Identity
        "player":    result.get("player"),
        "opponent":  result.get("opponent"),
        "prop_type": result.get("propType"),
        "venue":     result.get("venue"),
        "is_home":   result.get("isHome"),

        # Full prediction for completeness (all remaining fields)
        "full_prediction": {
            k: v for k, v in result.items()
            if k not in ("probabilityCurve", "factorLedger", "modelBreakdown",
                         "analysisFactors", "analysisSummary", "matchContext",
                         "gameScript", "matchDominance", "matchFactors",
                         "gameSituation", "bayesianMetrics", "evidenceQuality",
                         "fusionApplied", "calibrationApplied")
        },
    })


# ─────────────────────────────────────────────────────────────────────────────
# PREDICT/SOCCER — full pipeline, fixture+player auto-resolution, full diagnostic
# ─────────────────────────────────────────────────────────────────────────────

async def _resolve_soccer_context(fixture_id: int, player_id: int) -> dict:
    """
    Resolves player_name, team_id/name, opponent_id/name, venue, league_id
    from only fixture_id + player_id.

    Strategy:
    1. Use _resolve_fixture for fixture identity (home/away/league/season).
    2. Try fixtures/players (works for finished/in-progress matches).
    3. Fallback to /players?id=&season= (works for future fixtures).
    """
    # ── Step 1: fixture metadata via existing helper ──────────────────────────
    ctx       = await _resolve_fixture(fixture_id)          # raises 404 if not found
    home_id   = ctx["home_team_id"]
    home_name = ctx["home_team"] or "Home"
    away_id   = ctx["away_team_id"]
    away_name = ctx["away_team"] or "Away"
    league_id = ctx["league_id"]
    season    = ctx["season"] or 2026

    if not (home_id and away_id and league_id):
        raise HTTPException(422, detail={
            "error": f"Fixture {fixture_id} has incomplete team/league data."
        })

    # ── Step 2a: fixtures/players (past/in-progress) ──────────────────────────
    player_name      = None
    player_team_id   = None
    player_team_name = None
    resolution_source = "unknown"

    try:
        fp_data = await _sports_get(
            "fixtures/players", {"fixture": fixture_id},
            cache_ttl=_CACHE_TTL_FINISHED,
        )
        for team_entry in fp_data.get("response", []):
            t = team_entry.get("team", {})
            for p in team_entry.get("players", []):
                if p.get("player", {}).get("id") == player_id:
                    player_name      = p["player"]["name"]
                    player_team_id   = t.get("id")
                    player_team_name = t.get("name")
                    resolution_source = "fixture_players"
                    break
            if player_name:
                break
    except Exception:
        pass

    # ── Step 2b: /players?id=&season= (future fixtures) ───────────────────────
    # Players can have multiple entries (club + national team).  Prefer the
    # entry whose team ID matches a team in the fixture; otherwise fall back
    # to the first entry that looks like a club (not an international league).
    if not player_team_id:
        try:
            pl_data = await _sports_get(
                "players", {"id": player_id, "season": season},
                cache_ttl=_CACHE_TTL_FINISHED,
            )
            pl_rows = pl_data.get("response", [])
            if pl_rows:
                pl          = pl_rows[0]
                player_name = (pl.get("player") or {}).get("name") or f"Player {player_id}"
                stats       = pl.get("statistics") or []

                # 1st pass: exact fixture-team match
                for s in stats:
                    tid = (s.get("team") or {}).get("id")
                    if tid in (home_id, away_id):
                        player_team_id   = tid
                        player_team_name = (s.get("team") or {}).get("name")
                        resolution_source = "player_season_stats_fixture_match"
                        break

                # 2nd pass: first non-international entry
                if not player_team_id:
                    _INTL_LEAGUE_IDS = {1, 2, 10, 17, 18, 20, 29, 30, 31, 34}
                    for s in stats:
                        tid = (s.get("team") or {}).get("id")
                        lid = (s.get("league") or {}).get("id")
                        if tid and lid not in _INTL_LEAGUE_IDS:
                            player_team_id   = tid
                            player_team_name = (s.get("team") or {}).get("name")
                            resolution_source = "player_season_stats"
                            break

                # 3rd pass: anything
                if not player_team_id and stats:
                    s = stats[0]
                    player_team_id   = (s.get("team") or {}).get("id")
                    player_team_name = (s.get("team") or {}).get("name")
                    resolution_source = "player_season_stats_fallback"
        except Exception:
            pass

    if not player_team_id:
        raise HTTPException(422, detail={
            "error": (
                f"Could not identify player {player_id} in fixture {fixture_id}. "
                "Ensure the player participated in this match, or provide explicit IDs "
                "via POST /api/jarvis/predict."
            )
        })

    # ── Step 3: derive venue and opponent ─────────────────────────────────────
    if player_team_id == home_id:
        venue         = "home"
        opponent_id   = away_id
        opponent_name = away_name
        team_name     = player_team_name or home_name
    else:
        venue         = "away"
        opponent_id   = home_id
        opponent_name = home_name
        team_name     = player_team_name or away_name

    return {
        "player_name":        player_name,
        "team_id":            player_team_id,
        "team_name":          team_name,
        "opponent_id":        opponent_id,
        "opponent_name":      opponent_name,
        "venue":              venue,
        "league_id":          league_id,
        "season":             season,
        "_resolution_source": resolution_source,
    }


def _build_soccer_diagnostic(result: dict) -> dict:
    """
    Build the comprehensive JARVIS diagnostic from a raw predict() result dict.
    All field names verified against the live predict() output 2026-08-18.
    """
    bm = result.get("bayesianMetrics") or {}
    eq = result.get("evidenceQuality") or {}
    # calibrationApplied = fusionApplied in the real response shape
    ca = result.get("fusionApplied") or result.get("calibrationApplied") or {}
    gs = result.get("gameSituation") or {}

    return {
        # ── Final output (identical to subscriber app) ────────────────────────
        "final": {
            "recommendation":          result.get("recommendation"),
            "projected_value":         result.get("projectedValue"),
            "most_likely_value":       bm.get("mostLikelyValue"),
            "line":                    result.get("line"),
            "confidence_score":        result.get("confidenceScore"),
            "confidence_level":        result.get("confidenceLevel"),
            "raw_confidence":          result.get("rawConfidence"),
            "p_over":                  bm.get("pOver"),
            "p_under":                 bm.get("pUnder"),
            "edge_z":                  bm.get("edgeZ"),
            "edge_gap_abs":            bm.get("edgeGapAbs"),
            "edge_gap_band":           bm.get("edgeGapBand"),
            "edge_gap_pct":            bm.get("edgeGapPct"),
            "edge_rating":             result.get("edgeRating"),
            "edge_rating_reason":      result.get("edgeRatingReason"),
            "safety_rating":           result.get("safetyRating"),
            "coin_flip":               result.get("coinFlip", False),
            "low_conviction":          result.get("lowConviction", False),
            "line_deviation_band":     result.get("lineDeviationBand"),
            "line_deviation_hit_rate": result.get("lineDeviationHitRate"),
            "line_deviation_n":        result.get("lineDeviationHitRateN"),
        },

        # ── Pre-calibration Bayesian state ────────────────────────────────────
        "pre_calibration": {
            "bayesian_posterior":      ca.get("bayesianPosterior"),
            "bayesian_recommendation": ca.get("bayesianRecommendation"),
            "bayesian_confidence":     ca.get("bayesianConfidence"),
            "early_estimate":          ca.get("earlyEstimate"),
            "early_estimate_rec":      ca.get("earlyEstimateRec"),
            "divergence_pct":          ca.get("divergencePct"),
            "agreement":               ca.get("agreement"),
            "fusion_weights":          ca.get("weights"),
            "fusion_note":             ca.get("note"),
        },

        # ── Three-layer model (raw structure) ─────────────────────────────────
        "three_layer_model": bm.get("threeLayerModel"),

        # ── Layer 1: Prior ────────────────────────────────────────────────────
        "prior": {
            "mean":    bm.get("priorMean"),
            "std":     bm.get("priorStd"),
            "weight":  bm.get("priorWeight"),
            "samples": bm.get("priorSamples"),
        },

        # ── Layer 2: Momentum ─────────────────────────────────────────────────
        "momentum": {
            "effect":         bm.get("momentumEffect"),
            "mean":           bm.get("momentumMean"),
            "weight":         bm.get("momentumWeight"),
            "label":          bm.get("momentumLabel"),
            "trend_per_game": bm.get("trendPerGame"),
            "streak_flag":    bm.get("streakFlag"),
        },

        # ── Venue history ─────────────────────────────────────────────────────
        "venue_history": {
            "avg":     bm.get("venueAvg"),
            "samples": bm.get("venueSamples"),
        },

        # ── Layer 3: Covariates (each contribution separately) ────────────────
        "covariates": {
            "total_adjustment":         bm.get("covariateAdjustment"),
            "weight":                   bm.get("covariateWeight"),
            "opponent_allowed_avg":     bm.get("opponentAllowedAvg"),
            "opponent_allowed_samples": bm.get("opponentAllowedSamples"),
            "opponent_allowed_weight":  bm.get("opponentAllowedWeight"),
            "cond_poss_adj":            bm.get("condPossAdj"),
            "press_intensity":          bm.get("pressIntensity"),
            "team_quality_gap":         bm.get("teamQualityGap"),
            "fatigue_layer":            bm.get("fatigueLayer"),
            "match_stakes":             bm.get("matchStakes"),
            "clean_sheet_layer":        bm.get("cleanSheetLayer"),
            "league_style_layer":       bm.get("leagueStyleLayer"),
            "set_piece_layer":          bm.get("setPieceLayer"),
            "altitude_layer":           bm.get("altitudeLayer"),
            "game_script_layer":        bm.get("gameScript"),
            "cdm_inversion":            bm.get("cdmInversion"),
            "dominant_cm_boost":        bm.get("dominantCmBoost"),
            "home_cdm_deep_block":      bm.get("homeCdmDeepBlock"),
            "gk_cross_team":            bm.get("gkCrossTeam"),
        },

        # ── Posterior (post-covariate Gaussian) ───────────────────────────────
        "posterior": {
            "mean":       bm.get("posteriorMean"),
            "std":        bm.get("posteriorStd"),
            "cv":         bm.get("cv"),
            "volatility": bm.get("volatility"),
        },

        # ── Positional squeeze (James-Stein toward position baseline) ─────────
        "positional_squeeze": bm.get("positionalBaseline"),

        # ── Calibration layers ────────────────────────────────────────────────
        "calibration": {
            "league_calibration":    bm.get("leagueCalibration"),
            "scenario_priors":       bm.get("scenarioPriors"),
            "odds_tier_priors":      bm.get("oddsTierPriors"),
            "pass_projection_cal":   bm.get("passProjectionCalibration"),
            "goalkeeper_pool_prior": bm.get("goalkeeperPoolPrior"),
            "pressure_response":     bm.get("pressureResponse"),
            "fusion_applied":        ca,
        },

        # ── Monte Carlo output ────────────────────────────────────────────────
        "monte_carlo": {
            "p_over":              bm.get("pOver"),
            "p_under":             bm.get("pUnder"),
            "landing_bands":       bm.get("landingBands"),
            "range_60":            bm.get("range60"),
            "range_80":            bm.get("range80"),
            "confidence_interval": bm.get("confidenceInterval"),
            "distribution":        bm.get("distribution"),
        },

        # ── Evidence quality gate ─────────────────────────────────────────────
        "evidence_quality": eq,

        # ── Calibration alert: OK / RISKY / AVOID ────────────────────────────
        "calibration_alert": {
            "status":                  result.get("safetyRating", "OK"),
            "line_deviation_band":     result.get("lineDeviationBand"),
            "line_deviation_hit_rate": result.get("lineDeviationHitRate"),
            "line_deviation_n":        result.get("lineDeviationHitRateN"),
            "coin_flip":               result.get("coinFlip", False),
        },

        # ── Warnings and missing-data flags ───────────────────────────────────
        "warnings":      result.get("tacticalAlerts", []),
        "risk_signals":  result.get("riskSignals"),
        "consensus_note": result.get("consensusNote"),
        "data_quality":  result.get("dataQuality"),

        # ── Factor ledger (what raised/lowered the projection) ────────────────
        "factor_ledger":   result.get("factorLedger"),
        "model_breakdown": result.get("modelBreakdown"),

        # ── Model version / fingerprint ───────────────────────────────────────
        "model_version": {
            "factor_ledger_version":     result.get("factorLedgerVersion"),
            "factor_ledger_fingerprint": result.get("factorLedgerFingerprint"),
            "three_layer_version":       (bm.get("threeLayerModel") or {}).get("version"),
            "evidence_quality_version":  eq.get("version"),
        },

        # ── Match situation adjustments ───────────────────────────────────────
        "game_situation":     gs,
        "match_dominance":    result.get("matchDominance"),
        "positional_reality": result.get("positionalReality"),

        # ── Resolved identity ─────────────────────────────────────────────────
        "resolved_identity": {
            "player_name":     result.get("canonicalPlayerName") or result.get("playerName"),
            "player_id":       result.get("playerId"),
            "team":            result.get("teamName"),
            "team_id":         result.get("fixtureTeamId"),
            "opponent":        result.get("opponentName"),
            "opponent_id":     result.get("fixtureOpponentId"),
            "venue":           result.get("resolvedVenue") or result.get("venue"),
            "is_home":         result.get("playerIsHome") or result.get("isHome"),
            "league_id":       result.get("leagueId"),
            "fixture_id":      result.get("fixtureId"),
            "fixture_date":    result.get("fixtureDate"),
            "player_position": result.get("playerPosition"),
        },

        # ── Narrative ─────────────────────────────────────────────────────────
        "sharp_summary":     result.get("sharpSummary"),
        "reasoning":         result.get("reasoning"),
        "tactical_breakdown": result.get("tacticalBreakdown"),
    }


class JarvisSoccerPredictBody(BaseModel):
    """Minimal soccer predict inputs — fixture+player auto-resolve everything else."""
    fixture_id:        int
    player_id:         int
    prop_type:         str   = "pass_attempts"
    line:              float
    odds:              Optional[dict] = None
    position_override: str   = ""
    role_override:     str   = ""


@router.post("/api/jarvis/predict/soccer")
async def jarvis_predict_soccer(
    body: JarvisSoccerPredictBody,
    authorization: Optional[str] = Header(default=None),
):
    """
    Full production soccer prediction. fixture_id + player_id auto-resolve
    all team, opponent, venue, and league context. Returns the exact same
    final projection the subscriber app shows plus every intermediate layer.
    """
    _require_auth(authorization)

    if not _JARVIS_KEY:
        raise HTTPException(503, detail={"error": "JARVIS_API_KEY not configured."})

    # ── 1. Auto-resolve context ───────────────────────────────────────────────
    try:
        ctx = await _resolve_soccer_context(body.fixture_id, body.player_id)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(422, detail={"error": f"Context resolution failed: {exc}"})

    # ── 2. Construct internal PredictionRequest ───────────────────────────────
    from models import PredictionRequest
    from routes.predict import predict as _rp_predict

    req = PredictionRequest(
        email="_jarvis_service_",
        token=_JARVIS_KEY,
        leagueId=ctx["league_id"],
        playerId=body.player_id,
        playerName=ctx["player_name"],
        teamId=ctx["team_id"],
        teamName=ctx["team_name"],
        opponentId=ctx["opponent_id"],
        opponentName=ctx["opponent_name"],
        venue=ctx["venue"],
        propType=body.prop_type,
        line=body.line,
        sport="soccer",
        fixtureId=body.fixture_id,
        odds=body.odds,
        positionOverride=body.position_override,
        roleOverride=body.role_override,
    )

    # ── 3. Run the full production pipeline ───────────────────────────────────
    # The club-transfer guard updates the player cache and then raises HTTP 422
    # with "Current club changed to X." — a single retry uses the fresh cache.
    try:
        result = await _rp_predict(req)
    except HTTPException as exc:
        # Club-transfer guard raises 409 with "Current club changed to X."
        # It updates the player cache then raises — retry once with fresh cache.
        if exc.status_code == 409 and "Current club changed" in str(exc.detail):
            try:
                result = await _rp_predict(req)   # retry with now-warm cache
            except HTTPException:
                raise
            except Exception as exc2:
                raise HTTPException(502, detail={"error": f"Prediction engine error on retry: {exc2}"})
        else:
            raise
    except Exception as exc:
        raise HTTPException(502, detail={"error": f"Prediction engine error: {exc}"})

    if hasattr(result, "body"):
        import json as _json
        result = _json.loads(result.body)

    # ── 4. Return comprehensive diagnostic ────────────────────────────────────
    diagnostic = _build_soccer_diagnostic(result)
    diagnostic["_resolution"] = {
        "source":     ctx.get("_resolution_source"),
        "fixture_id": body.fixture_id,
        "player_id":  body.player_id,
    }

    return JSONResponse(content={
        "source":       "jarvis/predict/soccer",
        "generated_at": int(time.time()),
        "diagnostic":   diagnostic,
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
