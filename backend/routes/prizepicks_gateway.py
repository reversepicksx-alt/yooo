from __future__ import annotations

import os
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Query

from prizepicks_gateway import GatewayError, fetch_board

router = APIRouter()


def _auth(value: Optional[str]) -> None:
    expected = (os.getenv("JARVIS_API_TOKEN") or "").strip()
    if not expected:
        raise HTTPException(503, detail={"error": "configuration", "message": "JARVIS_API_TOKEN is not configured."})
    scheme, _, token = (value or "").partition(" ")
    if scheme.lower() != "bearer" or token.strip() != expected:
        raise HTTPException(401, detail={"error": "authentication", "message": "A valid bearer token is required."})


def _error(exc: GatewayError) -> HTTPException:
    return HTTPException(exc.status, detail={"error": exc.kind, "message": str(exc)})


async def _rows(date: str | None = None) -> tuple[list[dict], dict]:
    try:
        return await fetch_board(date)
    except GatewayError as exc:
        raise _error(exc) from exc


def _result(rows: list[dict], meta: dict, filters: dict | None = None) -> dict:
    return {"source": "SportsGameOdds", "bookmaker": "prizepicks", "markets": rows, "meta": meta, "filters": filters or {}}


@router.get("/health", tags=["gateway"])
async def health():
    return {"status": "ok", "service": "private-prizepicks-gateway", "upstream_configured": bool(os.getenv("SGO_API_KEY") or os.getenv("SPORTSGAMEODDS_API_KEY"))}


@router.get("/pp/board", tags=["prizepicks"])
async def board(date: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"), authorization: Optional[str] = Header(None)):
    _auth(authorization)
    rows, meta = await _rows(date)
    return _result(rows, meta, {"date": date})


@router.get("/pp/match/{event_id}", tags=["prizepicks"])
async def match(event_id: str, authorization: Optional[str] = Header(None)):
    _auth(authorization)
    rows, meta = await _rows()
    found = [row for row in rows if str(row.get("eventId")) == event_id]
    if not found:
        raise HTTPException(404, detail={"error": "no_markets", "message": "No current PrizePicks markets found for that event ID."})
    return _result(found, meta, {"eventId": event_id})


@router.get("/pp/player/{player_id}", tags=["prizepicks"])
async def player(player_id: str, authorization: Optional[str] = Header(None)):
    _auth(authorization)
    rows, meta = await _rows()
    found = [row for row in rows if str(row.get("playerId")) == player_id or str(row.get("statEntityID")) == player_id]
    if not found:
        raise HTTPException(404, detail={"error": "no_markets", "message": "No current PrizePicks markets found for that player ID."})
    return _result(found, meta, {"playerId": player_id})


@router.get("/pp/search", tags=["prizepicks"])
async def search(
    team: Optional[str] = Query(None, max_length=100),
    player: Optional[str] = Query(None, max_length=100),
    league: Optional[str] = Query(None, max_length=100),
    event: Optional[str] = Query(None, max_length=100),
    stat: Optional[str] = Query(None, max_length=100),
    date: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    authorization: Optional[str] = Header(None),
):
    _auth(authorization)
    rows, meta = await _rows(date)
    def has(value: object, query: str | None) -> bool:
        return not query or query.casefold() in str(value or "").casefold()
    found = [
        row for row in rows
        if (not team or has(row.get("homeTeam"), team) or has(row.get("awayTeam"), team))
        and has(row.get("playerName"), player)
        and has(row.get("leagueId"), league)
        and has(row.get("eventId"), event)
        and (has(row.get("marketName"), stat) or has(row.get("statId"), stat))
    ]
    if not found:
        raise HTTPException(404, detail={"error": "no_markets", "message": "No current PrizePicks markets matched the supplied filters."})
    return _result(found, meta, {"team": team, "player": player, "league": league, "event": event, "stat": stat, "date": date})


@router.get("/pp/openapi.json", include_in_schema=False)
async def gateway_openapi():
    return {
        "openapi": "3.1.0",
        "info": {"title": "Private JARVIS PrizePicks Gateway", "version": "1.0.0"},
        "servers": [{"url": "https://YOUR-DEPLOYED-DOMAIN"}],
        "security": [{"bearerAuth": []}],
        "paths": {
            "/pp/board": {"get": {"operationId": "getCurrentSoccerPrizePicksBoard", "parameters": [{"name": "date", "in": "query", "schema": {"type": "string", "format": "date"}}], "responses": {"200": {"description": "Complete normalized current soccer board"}}}},
            "/pp/match/{event_id}": {"get": {"operationId": "getPrizePicksMatchMarkets", "parameters": [{"name": "event_id", "in": "path", "required": True, "schema": {"type": "string"}}], "responses": {"200": {"description": "All markets for one event"}}}},
            "/pp/player/{player_id}": {"get": {"operationId": "getPrizePicksPlayerMarkets", "parameters": [{"name": "player_id", "in": "path", "required": True, "schema": {"type": "string"}}], "responses": {"200": {"description": "All current markets for one player"}}}},
            "/pp/search": {"get": {"operationId": "searchPrizePicksMarkets", "parameters": [{"name": n, "in": "query", "schema": {"type": "string"}} for n in ("team", "player", "league", "event", "stat", "date")], "responses": {"200": {"description": "Filtered current markets"}}}},
        },
        "components": {"securitySchemes": {"bearerAuth": {"type": "http", "scheme": "bearer"}}},
    }