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
  GET /api/jarvis/resolve-soccer-prop

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
import html
import os
import secrets
import shutil
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Body, Header, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from config import OWNER_EMAIL, db
from jarvis_audit import (
    AUDIT_MODEL_VERSION,
    AUDIT_SCHEMA_VERSION,
    STAT_DEFINITIONS,
    audit_enabled,
    audit_mode,
    build_audit_snapshot,
    calibration_summary,
    implementation_status,
    line_deviation_ledger_coverage,
    persist_prediction_audit,
)
from sportsgameodds_client import list_market_board
from prop_safety_cache import (
    canonical_prop_type,
    get_prop_safety,
)
from tactical_memory import (
    MAX_RESULTS as TACTICAL_MEMORY_MAX_RESULTS,
    TacticalMemoryInput,
    retrieve_tactical_memory,
    invalidate_regime,
    upsert_tactical_memory,
)
from jarvis_orchestrator import execute_action

router = APIRouter()


class JarvisConversationBody(BaseModel):
    message: str = Field(..., min_length=1, max_length=1200)
    context: dict[str, Any] | None = None


def _audit_response_contract(audit: dict[str, Any], prediction: dict[str, Any]) -> dict[str, Any]:
    modules = audit.get("modules") if isinstance(audit.get("modules"), dict) else {}
    identity = audit.get("identity") if isinstance(audit.get("identity"), dict) else {}
    probability = audit.get("probability") if isinstance(audit.get("probability"), dict) else {}
    verdict = audit.get("jarvis_verdict") if isinstance(audit.get("jarvis_verdict"), dict) else {}
    values = lambda name: (modules.get(name) or {}).get("values", {})
    return {
        "identity": identity,
        "prop": identity.get("prop_type"),
        "line": identity.get("line"),
        "line_source": (prediction.get("_request") or {}).get("line_source") or "USER_SUPPLIED_LINE",
        "projection": (audit.get("rp_snapshot") or {}).get("projectedValue"),
        "p_over": probability.get("p_over"),
        "p_under": probability.get("p_under"),
        "model_recommendation": probability.get("selected_side"),
        "exact_role": values("exact_role"),
        "venue_analysis": values("independent_venue_possession"),
        "team_playstyle": values("buildup_interaction").get("team_playstyle"),
        "opponent_playstyle": values("press_block_interaction").get("opponent_playstyle"),
        "buildup_interaction": values("buildup_interaction"),
        "press_block_interaction": values("press_block_interaction"),
        "role_cohort": values("role_opponent_venue_cohort"),
        "first_goal": values("first_goal_market"),
        "leading_state": (values("first_goal_regime_change") or {}).get("best_case"),
        "trailing_state": (values("first_goal_regime_change") or {}).get("worst_case"),
        "level_60_state": (values("game_state") or {}).get("level_around_60"),
        "early_goal_states": values("game_state"),
        "minutes_risk": values("minutes_probability"),
        "line_movement": values("market_movement"),
        "strongest_opposite_case": verdict.get("strongest_opposite_case") or values("strongest_opposite_case"),
        "stress_test": values("counterfactual_robustness"),
        "robustness": verdict.get("robustness"),
        "jarvis_grade": verdict.get("grade"),
        "final_verdict": verdict.get("final_verdict"),
        "unknown_evidence": verdict.get("unknown_evidence") or [],
        "provenance": verdict.get("provenance") or audit.get("provenance"),
    }

# ── Config ────────────────────────────────────────────────────────────────────
_API_SPORTS_BASE = "https://v3.football.api-sports.io"
_API_SPORTS_KEY  = os.environ.get("API_SPORTS_KEY", "")
_JARVIS_KEY      = os.environ.get("JARVIS_API_KEY", "")

# Screenshot files are deliberately short-lived and addressed only by an
# opaque random handle. They are never placed in the public app bundle.
_SCREENSHOT_ROOT = Path("/tmp/reversepicks-jarvis-screenshots")
_SCREENSHOT_TTL_SECONDS = 10 * 60
_SCREENSHOTS: dict[str, tuple[Path, float, str]] = {}

# ── Simple TTL cache (in-memory, per-process) ─────────────────────────────────
# Keeps repeated match-context calls from burning quota on the same fixture.
_CACHE: dict[str, tuple[Any, float]] = {}
_CACHE_TTL_LIVE      = 90     # seconds — in-progress match
_CACHE_TTL_SCHEDULED = 300    # seconds — upcoming fixture
_CACHE_TTL_FINISHED  = 1800   # seconds — completed fixture
_RUNTIME_ROW_LIMIT   = 200


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


@router.post("/api/jarvis/conversation")
async def jarvis_conversation(
    body: JarvisConversationBody,
    authorization: Optional[str] = Header(default=None),
):
    """Run one owner-facing JARVIS action through the shared orchestrator."""
    _require_auth(authorization)
    if (os.environ.get("JARVIS_CONVERSATION_MODE") or "enabled").strip().lower() in {
        "off", "disabled", "false", "0",
    }:
        raise HTTPException(404, detail={"error": "feature_disabled", "feature": "jarvis_conversation"})

    from team_resolver import find_team
    from utils import priority_api_football_request

    async def load_picks() -> list[dict[str, Any]]:
        return await db.picks.find(
            {"email": OWNER_EMAIL}, {"_id": 0, "playerName": 1, "propType": 1, "line": 1,
             "recommendation": 1, "projectedValue": 1, "result": 1, "fixtureId": 1,
             "teamName": 1, "opponentName": 1}
        ).sort([("createdAt", -1)]).limit(100).to_list(length=100)

    async def fetch_fixtures(team_id: int) -> list[dict[str, Any]]:
        raw = await priority_api_football_request("fixtures", {"team": team_id, "next": 10})
        if isinstance(raw, list):
            return raw
        if isinstance(raw, dict):
            rows = raw.get("response") or raw.get("data") or []
            return rows if isinstance(rows, list) else []
        return []

    async def discover_slate() -> list[dict[str, Any]]:
        raw = await priority_api_football_request(
            "fixtures",
            {"next": 50},
        )
        if isinstance(raw, list):
            return raw
        if isinstance(raw, dict):
            rows = raw.get("response") or raw.get("data") or []
            return rows if isinstance(rows, list) else []
        return []

    async def resolve_player_fixture(request: dict[str, Any]) -> dict[str, Any]:
        try:
            resolved = await _resolve_soccer_prop_identity(
                player_name=str(request.get("player_name") or ""),
                opponent=request.get("opponent"),
                requested_date=request.get("date"),
                season=request.get("season"),
            )
            requested_venue = request.get("venue")
            if requested_venue and resolved.get("venue") != requested_venue:
                return {
                    "status": "UNKNOWN",
                    "reason": "verified_fixture_venue_conflicts_with_user_request",
                    "requested_venue": requested_venue,
                    "resolved_venue": resolved.get("venue"),
                    "resolution": resolved,
                }
            return resolved
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {"reason": str(exc.detail)}
            return {"status": "UNKNOWN", **detail}
        except Exception as exc:
            return {"status": "UNKNOWN", "reason": f"{type(exc).__name__} during identity resolution"}

    async def run_player_analysis(request: dict[str, Any]) -> dict[str, Any]:
        if request.get("status") != "resolved":
            return {"status": "UNKNOWN", "response": "Verified player and fixture context was unavailable."}
        try:
            body = JarvisSoccerPredictBody(
                fixture_id=int(request["fixture_id"]),
                player_id=int(request["player_id"]),
                prop_type=str(request["prop_type"]),
                line=float(request["line"]),
            )
            _, prediction = await _run_soccer_prediction(body, resolved_context=request)
            if request.get("audit"):
                await asyncio.gather(
                    _ensure_full_audit_first_goal_context(
                        prediction, request, body.prop_type
                    ),
                    _ensure_full_audit_news_context(
                        prediction, request, body.fixture_id
                    ),
                    return_exceptions=True,
                )
                audit = build_audit_snapshot(
                    prediction, _soccer_audit_request(body), context=request
                )
                return {
                    "status": "available",
                    "recommendation": prediction.get("recommendation"),
                    "prediction": prediction,
                    "audit": audit,
                    "jarvis_verdict": audit.get("jarvis_verdict") or audit.get("verdict"),
                    "audit_contract": _audit_response_contract(audit, prediction),
                    "jarvis_grade": audit.get("jarvis_grade") or audit.get("grade"),
                    "response": (
                        f"Verified {request.get('player_name')} at {request.get('venue')} vs "
                        f"{request.get('opponent_name')} and completed the full read-only audit "
                        f"for {body.prop_type} at line {body.line}."
                    ),
                }
            recommendation = prediction.get("recommendation") or prediction.get("rec") or "UNKNOWN"
            return {
                "status": "available",
                "recommendation": recommendation,
                "prediction": prediction,
                "response": (
                    f"Verified {request.get('player_name')} at {request.get('venue')} vs "
                    f"{request.get('opponent_name')} and ran the deterministic {request.get('prop_type')} "
                    f"analysis at line {request.get('line')}. Recommendation: {recommendation}."
                ),
            }
        except HTTPException as exc:
            return {"status": "UNKNOWN", "reason": str(exc.detail)}
        except Exception as exc:
            return {"status": "UNKNOWN", "reason": f"{type(exc).__name__} during prediction analysis"}

    result = await execute_action(
        body.message,
        context=body.context,
        load_picks=load_picks,
        find_team=find_team,
        fetch_fixtures=fetch_fixtures,
        discover_slate=discover_slate,
        load_board=lambda: list_market_board(hours=72, limit=60, sport_id="SOCCER"),
        load_memory=lambda: retrieve_tactical_memory(db, include_stale=False, limit=30),
        prior_prop_type=((body.context or {}).get("propType") if isinstance(body.context, dict) else None),
        resolve_player_fixture=resolve_player_fixture,
        run_player_analysis=run_player_analysis,
    )
    return {
        "source": "jarvis/conversation",
        "assistant": "JARVIS",
        **result,
    }


def _runtime_status(value: Any, *, source: str, reason: str | None = None) -> dict:
    """Common envelope for read-only runtime inputs.

    UNKNOWN is intentional: these routes are an audit surface and must not
    synthesize inputs from the production prediction path.
    """
    out = {
        "status": "available" if value is not None else "UNKNOWN",
        "value": value,
        "provenance": {"source": source, "read_only": True},
    }
    if reason:
        out["reason"] = reason
    return out


def _pick_runtime_packet(doc: dict, *names: str) -> Any:
    for name in names:
        value = doc.get(name)
        if value is not None:
            return value
        snapshot = doc.get("modelInputSnapshot") or {}
        if isinstance(snapshot, dict) and snapshot.get(name) is not None:
            return snapshot[name]
        audit = doc.get("jarvisAudit") or {}
        if isinstance(audit, dict) and audit.get(name) is not None:
            return audit[name]
    return None


# ── READ-ONLY RUNTIME INPUTS ─────────────────────────────────────────────────
# These endpoints deliberately inspect stored snapshots and already-loaded
# calibration caches. They never call predict(), save a pick, or refresh a
# provider/cache. This keeps JARVIS audit/reproduction independent.

@router.get("/api/jarvis/runtime/dominance-inputs")
async def jarvis_runtime_dominance_inputs(
    authorization: Optional[str] = Header(default=None),
    fixture_id: int = Query(..., ge=1),
    team_id: int = Query(..., ge=1),
):
    _require_auth(authorization)
    doc = await db.picks.find_one(
        {"fixtureId": fixture_id, "teamId": team_id},
        {
            "_id": 0, "fixtureId": 1, "teamId": 1, "opponentId": 1,
            "leagueId": 1, "venue": 1, "matchDominance": 1,
            "modelInputSnapshot": 1, "jarvisAudit": 1,
        },
        sort=[("settledAt", -1), ("timestamp", -1)],
    )
    packet = _pick_runtime_packet(doc or {}, "matchDominance", "dominanceInputs")
    if not isinstance(packet, dict):
        return {
            "source": "db.picks persisted runtime snapshot",
            "fixture_id": fixture_id,
            "team_id": team_id,
            "status": "UNKNOWN",
            "inputs": None,
            "provenance": {
                "read_only": True,
                "exact_fixture": True,
                "reason": "No persisted exact-fixture dominance packet is available.",
            },
        }
    return {
        "source": "db.picks persisted runtime snapshot",
        "fixture_id": fixture_id,
        "team_id": team_id,
        "status": "available",
        "inputs": packet,
        "provenance": {
            "read_only": True,
            "exact_fixture": True,
            "stored_snapshot": True,
        },
    }


@router.get("/api/jarvis/runtime/hyperprior")
async def jarvis_runtime_hyperprior(
    authorization: Optional[str] = Header(default=None),
    prop_type: str = Query(..., min_length=1, max_length=64),
    league_id: int = Query(..., ge=1),
    position: Optional[str] = Query(None, max_length=32),
    role: Optional[str] = Query(None, max_length=64),
):
    _require_auth(authorization)
    # The production hyperprior is opponent-fixture-stat dependent and is
    # intentionally not reconstructed here. A saved exact runtime snapshot is
    # the only authoritative caller-supplied value this route may expose.
    query: dict = {
        "leagueId": league_id,
        "modelInputSnapshot.request.propType": prop_type,
    }
    if position:
        query["$or"] = [
            {"position": position},
            {"playerPosition": position},
            {"modelInputSnapshot.position": position},
        ]
    if role:
        query.setdefault("$and", []).append({
            "$or": [
                {"role": role}, {"tacticalRole": role},
                {"playerRole": role},
                {"modelInputSnapshot.role": role},
            ]
        })
    doc = await db.picks.find_one(
        query,
        {"_id": 0, "bayesianMetrics": 1, "modelInputSnapshot": 1,
         "fixtureId": 1, "playerId": 1, "leagueId": 1},
        sort=[("timestamp", -1), ("settledAt", -1)],
    )
    metrics = (doc or {}).get("bayesianMetrics") or {}
    value = metrics.get("hyperpriorMean")
    if value is None:
        value = metrics.get("hyperprior_mean")
    return {
        "source": "db.picks persisted runtime snapshot",
        "query": {
            "prop_type": prop_type, "league_id": league_id,
            "position": position, "role": role,
        },
        **_runtime_status(
            value,
            source="saved exact runtime bayesian metrics",
            reason="No exact caller-supplied hyperprior was persisted for this context."
            if value is None else None,
        ),
        "sample_context": {
            "fixture_id": (doc or {}).get("fixtureId"),
            "player_id": (doc or {}).get("playerId"),
            "league_id": (doc or {}).get("leagueId"),
        },
    }


@router.get("/api/jarvis/runtime/prop-safety")
async def jarvis_runtime_prop_safety(
    authorization: Optional[str] = Header(default=None),
    prop_type: str = Query(..., min_length=1, max_length=64),
    side: str = Query(..., alias="side", pattern="^(OVER|UNDER|over|under)$"),
    line: Optional[float] = Query(None),
    league_id: Optional[int] = Query(None, ge=1),
    position: Optional[str] = Query(None, max_length=32),
    role: Optional[str] = Query(None, max_length=64),
):
    _require_auth(authorization)
    canonical = canonical_prop_type(prop_type)
    bucket = get_prop_safety(canonical, side, league_id, position)
    return {
        "source": "prop_safety_cache loaded snapshot",
        "query": {
            "prop_type": prop_type, "canonical_prop_type": canonical,
            "side": side.lower(), "direction": side.upper(), "line": line,
            "league_id": league_id, "position": position, "role": role,
        },
        **_runtime_status(
            bucket,
            source="derived settled-pick safety cache",
            reason="Safety cache is not loaded or has no eligible bucket."
            if bucket is None else None,
        ),
        "sample": {
            "n": bucket.get("n") if bucket else 0,
            "hit_rate": bucket.get("hitRate") if bucket else None,
            "wins": bucket.get("wins") if bucket else None,
            "losses": bucket.get("losses") if bucket else None,
        },
        "decision": {
            "safety": bucket.get("safety") if bucket else "UNKNOWN",
            "thresholds_are_data_derived": True,
            "thresholds": {
                "safe_hit_rate": 65,
                "safe_min_n": 10,
                "safe_high_hit_rate": 80,
                "safe_high_min_n": 5,
                "moderate_hit_rate": 57,
                "moderate_min_n": 8,
                "avoid_max_hit_rate": 44,
                "avoid_min_n": 5,
            },
            "cap_or_block": (
                "AVOID direction is blocked/suppressed by production safeguards."
                if bucket and bucket.get("safety") == "AVOID"
                else "No AVOID block in this loaded bucket."
                if bucket else "UNKNOWN — no loaded bucket."
            ),
        },
    }


@router.get("/api/jarvis/runtime/calibration-rows")
async def jarvis_runtime_calibration_rows(
    authorization: Optional[str] = Header(default=None),
    prop_type: Optional[str] = Query(None, max_length=64),
    direction: Optional[str] = Query(None, max_length=16),
    line_band: Optional[str] = Query(None, max_length=32),
    league_id: Optional[int] = Query(None, ge=1),
    position: Optional[str] = Query(None, max_length=32),
    role: Optional[str] = Query(None, max_length=64),
    venue: Optional[str] = Query(None, pattern="^(home|away)$"),
    model_version: Optional[str] = Query(None, max_length=64),
    date_from: Optional[str] = Query(None, max_length=10),
    date_to: Optional[str] = Query(None, max_length=10),
    limit: int = Query(50, ge=1, le=_RUNTIME_ROW_LIMIT),
):
    _require_auth(authorization)
    projection = {
        "_id": 0, "pickId": 1, "trackingId": 1, "fixtureId": 1,
        "playerId": 1, "playerName": 1, "propType": 1, "line": 1,
        "projectedValue": 1, "actualValue": 1, "recommendation": 1,
        "result": 1, "status": 1, "leagueId": 1, "position": 1,
        "playerPosition": 1, "role": 1, "tacticalRole": 1,
        "playerRole": 1, "venue": 1, "modelVersion": 1,
        "settledAt": 1, "timestamp": 1,
    }
    # Fetch a bounded superset, then apply aliases/deduplication in Python so
    # the response cannot claim an exact sample from duplicate saves.
    raw = await db.picks.find(
        {"status": "settled", "result": {"$exists": True}},
        projection,
    ).sort([("settledAt", -1), ("timestamp", -1)]).limit(_RUNTIME_ROW_LIMIT).to_list(
        length=_RUNTIME_ROW_LIMIT
    )
    seen: set[str] = set()
    rows: list[dict] = []
    for row in raw:
        result = str(row.get("result") or "").lower()
        if result not in {"hit", "miss", "win", "loss"}:
            continue
        prop = canonical_prop_type(row.get("propType"))
        side = str(row.get("recommendation") or "").upper()
        row_position = row.get("position") or row.get("playerPosition")
        row_role = row.get("role") or row.get("tacticalRole") or row.get("playerRole")
        if prop_type and prop != canonical_prop_type(prop_type):
            continue
        if direction and side != direction.upper():
            continue
        if line_band:
            try:
                parts = [float(p.strip()) for p in line_band.split("-", 1)]
                if len(parts) != 2 or not parts[0] <= float(row.get("line")) < parts[1]:
                    continue
            except (TypeError, ValueError):
                raise HTTPException(400, detail={"error": "line_band must be formatted as lower-upper."})
        if league_id is not None and row.get("leagueId") != league_id:
            continue
        if position and str(row_position or "").lower() != position.lower():
            continue
        if role and str(row_role or "").lower() != role.lower():
            continue
        if venue and row.get("venue") != venue:
            continue
        if model_version and row.get("modelVersion") != model_version:
            continue
        row_date = str(row.get("settledAt") or row.get("timestamp") or "")[:10]
        if date_from and row_date < date_from:
            continue
        if date_to and row_date > date_to:
            continue
        key = str(row.get("pickId") or row.get("trackingId") or (
            row.get("playerId"), prop, row.get("line"), side,
            str(row.get("settledAt") or row.get("timestamp") or "")[:10],
        ))
        if key in seen:
            continue
        seen.add(key)
        row["canonicalPropType"] = prop
        row["direction"] = side
        row["position"] = row_position
        row["role"] = row_role
        rows.append(row)
        if len(rows) >= limit:
            break
    return {
        "source": "db.picks settled ledger",
        "status": "available" if rows else "UNKNOWN",
        "rows": rows,
        "rows_returned": len(rows),
        "requested_limit": limit,
        "bounded_scan": _RUNTIME_ROW_LIMIT,
        "deduplicated": True,
        "settlement_valid": True,
        "provenance": {
            "read_only": True,
            "excluded_results": ["push", "dnp", "void", "pending"],
            "may_be_truncated": len(raw) >= _RUNTIME_ROW_LIMIT or len(rows) >= limit,
        },
    }


async def _resolve_owner_session() -> tuple[str, str]:
    """Resolve the private assistant's owner session without caller credentials.

    The owner email is the server-side account mapping from config. The session
    token stays in MongoDB and never crosses the JARVIS API boundary. If the
    owner has no session yet, create_session provisions one internally.
    """
    owner_email = str(OWNER_EMAIL or "").strip().lower()
    if not owner_email:
        raise HTTPException(503, detail={"error": "Owner account mapping is not configured."})

    try:
        session = await db.sessions.find_one(
            {"email": owner_email},
            {"_id": 0, "session_token": 1},
        )
        owner_token = (session or {}).get("session_token")
        if not owner_token:
            from routes.auth import create_session
            owner_token = await create_session(owner_email, "Owner")
    except Exception as exc:
        # Never echo the database error: provider/session details can include
        # connection metadata. Keep the public response intentionally generic.
        raise HTTPException(503, detail={"error": "Owner session is temporarily unavailable."}) from exc

    return owner_email, str(owner_token)


def _cleanup_screenshot_files() -> None:
    now = time.time()
    for handle, (path, expires_at, _section) in list(_SCREENSHOTS.items()):
        if expires_at <= now:
            _SCREENSHOTS.pop(handle, None)
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass


def _prediction_metrics(result: dict) -> dict[str, Any]:
    """Read final probability/calibration values across engine response shapes."""
    bayesian = result.get("bayesianMetrics") or {}
    ledger = result.get("factorLedger") or {}
    ledger_final = ledger.get("final") if isinstance(ledger, dict) else {}
    ledger_final = ledger_final if isinstance(ledger_final, dict) else {}

    def first(*values: Any) -> Any:
        for value in values:
            if value is not None:
                return value
        return None

    return {
        "pOver": first(
            result.get("pOver"),
            bayesian.get("pOver"),
            ledger_final.get("pOver"),
        ),
        "pUnder": first(
            result.get("pUnder"),
            bayesian.get("pUnder"),
            ledger_final.get("pUnder"),
        ),
        "propHistoricalRate": first(
            result.get("propHistoricalRate"),
            ledger_final.get("propHistoricalRate"),
        ),
        "propHistoricalN": first(
            result.get("propHistoricalN"),
            ledger_final.get("propHistoricalN"),
            0,
        ),
    }


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
# IDENTITY RESOLUTION
# ─────────────────────────────────────────────────────────────────────────────

def _identity_text(value: Any) -> str:
    raw = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(ch for ch in raw if not unicodedata.combining(ch)).casefold().strip()


def _identity_name_matches(query: str, candidate: Any) -> bool:
    wanted = _identity_text(query)
    actual = _identity_text(candidate)
    return bool(wanted and actual and (wanted == actual or wanted in actual))


def _team_name_matches(query: str, candidate: Any) -> bool:
    """Match full provider names plus safe initialisms such as PSG."""
    if _identity_name_matches(query, candidate):
        return True
    wanted = _identity_text(query).replace(" ", "")
    words = [word for word in _identity_text(candidate).split() if word]
    initials = "".join(word[0] for word in words)
    return bool(wanted and len(wanted) >= 2 and wanted == initials)


def _unknown_resolution(reason: str, message: str, **context: Any) -> HTTPException:
    return HTTPException(422, detail={
        "status": "UNKNOWN", "resolution": "unresolved", "reason": reason,
        "message": message, **context,
    })


def _player_search_identity(row: dict[str, Any]) -> dict[str, Any]:
    player = row.get("player") if isinstance(row.get("player"), dict) else row
    stats = row.get("statistics") if isinstance(row.get("statistics"), list) else []
    teams, seen = [], set()
    for stat in stats:
        team = (stat or {}).get("team") or {}
        if team.get("id") is not None and team["id"] not in seen:
            seen.add(team["id"])
            teams.append({"team_id": team["id"], "team_name": team.get("name")})
    return {
        "player_id": player.get("id"),
        "player_name": (
            f"{player.get('firstname')} {player.get('lastname')}".strip()
            if player.get("firstname") and player.get("lastname")
            else player.get("name")
        ),
        "teams": teams,
    }


def _fixture_identity(row: dict[str, Any]) -> dict[str, Any]:
    fixture, teams, league = row.get("fixture") or {}, row.get("teams") or {}, row.get("league") or {}
    home, away = teams.get("home") or {}, teams.get("away") or {}
    return {
        "fixture_id": fixture.get("id"),
        "date": str(fixture.get("date") or "")[:10],
        "status": (fixture.get("status") or {}).get("short") or "UNKNOWN",
        "home_team_id": home.get("id"), "home_team": home.get("name"),
        "away_team_id": away.get("id"), "away_team": away.get("name"),
        "league_id": league.get("id"), "league_name": league.get("name"),
        "season": league.get("season"), "round": league.get("round"),
    }


async def _resolve_soccer_prop_identity(
    *, player_name: str, team: str | None = None, opponent: str | None = None,
    requested_date: str | None = None, season: int | None = None,
) -> dict[str, Any]:
    """Resolve one natural-language soccer prop to one verified fixture."""
    query = str(player_name or "").strip()
    if len(query) < 2:
        raise _unknown_resolution("player_name_required", "Provide a player name.")
    target_date = None
    if requested_date:
        try:
            target_date = datetime.strptime(requested_date, "%Y-%m-%d").date()
        except ValueError as exc:
            raise _unknown_resolution("invalid_date", "date must use YYYY-MM-DD.") from exc

    search_season = int(season or datetime.now(timezone.utc).year)
    try:
        player_data = await _sports_get(
            "players", {"search": query, "season": search_season},
            cache_ttl=_CACHE_TTL_SCHEDULED,
        )
    except HTTPException:
        # API-Football requires team/league context for search. Do not turn
        # that provider constraint into a terminal identity failure; the
        # opponent + venue graph below can derive the missing team context.
        player_data = {"response": []}
    players = [
        _player_search_identity(row) for row in (player_data.get("response") or [])
    ]
    players = [
        row for row in players
        if row.get("player_id") is not None
        and _identity_name_matches(query, row.get("player_name"))
    ]
    graph_fixture: dict[str, Any] | None = None
    rows: list[dict[str, Any]] = []
    if not players and opponent and not team:
        # Prefer the project's existing alias-aware team resolver before
        # asking API-Football to search an abbreviation such as PSG.
        resolved_opponent = None
        try:
            from team_resolver import find_team
            resolved_opponent = await find_team(str(opponent).strip())
        except Exception:
            resolved_opponent = None
        opponent_data = await _sports_get_safe(
            "teams", {"search": str(opponent).strip()},
            cache_ttl=_CACHE_TTL_SCHEDULED,
        )
        opponent_rows = [
            row.get("team") if isinstance(row.get("team"), dict) else row
            for row in ((opponent_data or {}).get("response") or [])
        ]
        opponent_rows = [
            row for row in opponent_rows
            if row.get("id") is not None
            and _team_name_matches(str(opponent), row.get("name"))
        ]
        if resolved_opponent and resolved_opponent.get("teamId") is not None:
            # The alias-aware resolver has league-priority knowledge. Raw
            # provider search for an acronym can return youth/regional clubs;
            # never let those ambiguous rows override the verified result.
            opponent_rows = [{
                "id": resolved_opponent["teamId"],
                "name": resolved_opponent.get("teamName") or str(opponent),
            }]
        opponent_ids = {row.get("id") for row in opponent_rows}
        if len(opponent_ids) == 1:
            opponent_id = next(iter(opponent_ids))
            fixture_params = (
                {"date": target_date.isoformat()}
                if target_date else {"team": opponent_id, "next": 20}
            )
            if target_date:
                fixture_params["team"] = opponent_id
            fixture_data = await _sports_get_safe(
                "fixtures", fixture_params, cache_ttl=_CACHE_TTL_SCHEDULED,
            )
            fixture_rows = (fixture_data or {}).get("response") or []
            terminal = {"FT", "AET", "PEN", "CANC", "PST", "ABD", "AWD", "WO"}
            candidates = []
            for row in fixture_rows:
                identity = _fixture_identity(row)
                if identity["status"] in terminal or identity["fixture_id"] is None:
                    continue
                if identity["away_team_id"] != opponent_id:
                    continue
                candidates.append(identity)
            selected_fixture = None
            if candidates:
                nearest_date = min(item["date"] for item in candidates)
                same_day = [item for item in candidates if item["date"] == nearest_date]
                if len(same_day) == 1:
                    selected_fixture = same_day[0]
            if selected_fixture:
                graph_fixture = selected_fixture
                home_id = graph_fixture["home_team_id"]
                squad_data = await _sports_get_safe(
                    "players/squads", {"team": home_id},
                    cache_ttl=_CACHE_TTL_SCHEDULED,
                )
                squad_rows = (squad_data or {}).get("response") or []
                squad_players = []
                for row in squad_rows:
                    squad_players.extend(row.get("players") or [])
                matched = [
                    player for player in squad_players
                    if _identity_name_matches(
                        query,
                        player.get("name") or " ".join(
                            part for part in (player.get("firstname"), player.get("lastname"))
                            if part
                        ),
                    )
                ]
                players = [{
                    "player_id": player.get("id"),
                    "player_name": player.get("name") or " ".join(
                        part for part in (player.get("firstname"), player.get("lastname"))
                        if part
                    ),
                    "teams": [{"team_id": home_id, "team_name": graph_fixture["home_team"]}],
                } for player in matched if player.get("id") is not None]
                if len(players) == 1:
                    rows = [graph_fixture]
    if not players:
        raise _unknown_resolution("player_not_found", f"No verified player matched {query}.",
                                   query=query, season=search_season,
                                   resolution_path="opponent_fixture_home_team_squad")

    resolved_team = None
    if team:
        team_query = str(team).strip()
        data = await _sports_get(
            "teams",
            {"id": int(team_query)} if team_query.isdigit() else {"search": team_query},
            cache_ttl=_CACHE_TTL_SCHEDULED,
        )
        teams = {}
        for row in data.get("response") or []:
            item = row.get("team") if isinstance(row.get("team"), dict) else row
            if item.get("id") is not None and (
                team_query.isdigit() or _identity_name_matches(team_query, item.get("name"))
            ):
                teams[item["id"]] = {"team_id": item["id"], "team_name": item.get("name")}
        if len(teams) != 1:
            raise _unknown_resolution(
                "team_ambiguous" if teams else "team_not_found",
                f"Team context {team_query} did not resolve to exactly one team.",
                query=team_query, candidates=list(teams.values()),
            )
        resolved_team = next(iter(teams.values()))
        players = [
            row for row in players
            if resolved_team["team_id"] in {item["team_id"] for item in row["teams"]}
        ]

    if len(players) != 1:
        raise _unknown_resolution(
            "player_ambiguous",
            "Player name did not resolve to exactly one verified player.",
            query=query, team=resolved_team, candidates=players[:10],
        )
    player = players[0]
    player_team_ids = {item["team_id"] for item in player["teams"]}
    if resolved_team:
        player_team_ids = {resolved_team["team_id"]}
    if not player_team_ids:
        raise _unknown_resolution(
            "player_team_unavailable",
            "The provider returned no verified team for this player.",
            player_id=player["player_id"],
        )

    if not rows:
        rows = []
        for team_id in sorted(player_team_ids):
            params = {"team": team_id}
            params["date" if target_date else "next"] = (
                target_date.isoformat() if target_date else 20
            )
            data = await _sports_get("fixtures", params, cache_ttl=_CACHE_TTL_SCHEDULED)
            rows.extend(data.get("response") or [])
    normalized_fixtures = (
        [graph_fixture] if graph_fixture else [_fixture_identity(row) for row in rows]
    )
    fixtures = {
        item["fixture_id"]: item for item in normalized_fixtures
        if item.get("fixture_id") is not None
    }
    fixtures = list(fixtures.values())
    if target_date:
        fixtures = [item for item in fixtures if item["date"] == target_date.isoformat()]
    else:
        today = datetime.now(timezone.utc).date().isoformat()
        terminal = {"FT", "AET", "PEN", "CANC", "PST", "ABD", "AWD", "WO"}
        fixtures = [
            item for item in fixtures
            if item["date"] >= today and item["status"] not in terminal
        ]
    if opponent:
        filtered = []
        for item in fixtures:
            opponent_name = (
                item["away_team"] if item["home_team_id"] in player_team_ids
                else item["home_team"] if item["away_team_id"] in player_team_ids
                else None
            )
            if opponent_name and _team_name_matches(opponent, opponent_name):
                filtered.append(item)
        fixtures = filtered
    if len(fixtures) != 1:
        raise _unknown_resolution(
            "fixture_ambiguous" if fixtures else "fixture_not_found",
            "The request did not resolve to exactly one verified fixture.",
            player_id=player["player_id"], player_name=player["player_name"],
            team=resolved_team, opponent=opponent,
            date=target_date.isoformat() if target_date else None,
            candidates=fixtures[:10],
        )

    fixture = fixtures[0]
    if fixture["home_team_id"] in player_team_ids:
        team_id, team_name, opponent_id, opponent_name, venue = (
            fixture["home_team_id"], fixture["home_team"], fixture["away_team_id"],
            fixture["away_team"], "home"
        )
    elif fixture["away_team_id"] in player_team_ids:
        team_id, team_name, opponent_id, opponent_name, venue = (
            fixture["away_team_id"], fixture["away_team"], fixture["home_team_id"],
            fixture["home_team"], "away"
        )
    else:
        raise _unknown_resolution("fixture_team_conflict",
                                  "The verified player team is not one of the fixture teams.",
                                  player_id=player["player_id"], fixture_id=fixture["fixture_id"])
    return {
        "status": "resolved", "resolution": "verified",
        "fixture_id": fixture["fixture_id"], "player_id": player["player_id"],
        "player_name": player["player_name"], "team_id": team_id, "team_name": team_name,
        "opponent_id": opponent_id, "opponent_name": opponent_name,
        "league_id": fixture["league_id"], "league_name": fixture["league_name"],
        "venue": venue, "season": fixture["season"], "date": fixture["date"],
        "fixture_status": fixture["status"],
        "evidence": {
            "source": "api-football", "player_search_season": search_season,
            "fixture_date": fixture["date"],
            "identity_match": "verified_provider_name_and_fixture_team",
        },
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
    # External Actions must call the published app, not the workspace's
    # ephemeral .replit.dev host.
    base = "https://reversepicks.com"

    def _param(name, typ, req, desc):
        p = {"name": name, "in": "query", "schema": {"type": typ}, "description": desc}
        if req:
            p["required"] = True
        return p

    fixture_param   = _param("fixture", "integer", True,  "Fixture ID.")
    fixture_param_o = _param("fixture", "integer", False, "Fixture ID.")

    schema = {
        "openapi": "3.1.0",
        "info": {
            "title": "JARVIS Football API",
            "description": (
                "Secure football data and Reverse Picks analysis API. "
                "All data operations require the JARVIS bearer key. "
                "For player analysis, run the full soccer audit once and use "
                "its RP prediction for the quantitative answer; audit data is "
                "provenance only."
            ),
            "version": "3.1.0",
            "x-jarvis-mandatory-workflow": [
                "JARVIS DIRECT ORCHESTRATION MODE: resolve identity first.",
                "Run immutable Reverse Picks production prediction first.",
                "Then call relevant primitive evidence endpoints directly; do not depend exclusively on full-audit.",
                "Audit role, fixture, lineup, stats, events, injuries, odds, history, venue possession, calibration, current line, and line history.",
                "RP math remains immutable. Use UNKNOWN rather than invented data.",
            ],
        },
        "servers": [{"url": base}],
        "x-direct-api-football-action": {
            "description": (
                "Optional direct Action schema for JARVIS. Use this server for raw "
                "API-Football reads; do not route those calls through Reverse Picks."
            ),
            "servers": [{"url": _API_SPORTS_BASE}],
            "authentication": {
                "type": "apiKey",
                "in": "header",
                "name": "x-apisports-key",
                "description": "Supply the API-Football key through Action authentication. Never place it in prompts or responses.",
            },
            "resources": [
                "fixtures", "fixtures/statistics", "fixtures/players",
                "fixtures/lineups", "fixtures/events", "injuries", "odds",
                "teams", "teams/statistics", "players", "players/squads",
                "standings", "leagues", "fixtures/headtohead",
            ],
        },
        "components": {
            "schemas": {
                "PrizePicksLineHistoryPoint": {
                    "type": "object",
                    "required": ["line", "observed_at"],
                    "properties": {
                        "line": {"type": "number"},
                        "observed_at": {"type": "integer"},
                    },
                },
                "PrizePicksMarket": {
                    "type": "object",
                    "required": [
                        "marketKey", "eventId", "eventStart", "sport", "sportName",
                        "leagueId", "leagueName", "homeTeam", "awayTeam",
                        "playerName", "playerProviderId", "propType", "propLabel",
                        "statId", "marketLine", "current_line", "previous_line",
                        "movement", "first_seen", "last_seen", "line_history",
                        "bookmakers", "providerCoverage", "analysisSupported",
                    ],
                    "properties": {
                        "marketKey": {"type": "string"},
                        "eventId": {"type": ["string", "null"]},
                        "eventStart": {"type": ["string", "null"], "format": "date-time"},
                        "sport": {"type": "string"},
                        "sportName": {"type": "string"},
                        "leagueId": {"type": ["integer", "null"]},
                        "leagueName": {"type": "string"},
                        "homeTeam": {"type": "string"},
                        "awayTeam": {"type": "string"},
                        "playerName": {"type": "string"},
                        "playerProviderId": {"type": ["string", "null"]},
                        "propType": {"type": "string"},
                        "propLabel": {"type": "string"},
                        "statId": {"type": "string"},
                        "marketLine": {"type": ["number", "null"]},
                        "current_line": {"type": ["number", "null"]},
                        "previous_line": {"type": ["number", "null"]},
                        "movement": {"type": ["number", "null"]},
                        "first_seen": {"type": "integer"},
                        "last_seen": {"type": "integer"},
                        "line_history": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/PrizePicksLineHistoryPoint"},
                        },
                        "bookmakers": {
                            "type": "object",
                            "additionalProperties": {"type": "object"},
                        },
                        "providerCoverage": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "analysisSupported": {"type": "boolean"},
                    },
                },
                "PrizePicksBoardResponse": {
                    "type": "object",
                    "required": [
                        "source", "bookmaker", "fetched_at", "market_count",
                        "saved", "save_status", "history_status", "markets",
                    ],
                    "properties": {
                        "source": {"type": "string", "const": "SportsGameOdds"},
                        "bookmaker": {"type": "string", "const": "PrizePicks"},
                        "fetched_at": {"type": "integer"},
                        "market_count": {"type": "integer"},
                        "saved": {"type": "boolean"},
                        "save_status": {"type": "string"},
                        "history_status": {"type": "string"},
                        "markets": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/PrizePicksMarket"},
                        },
                    },
                },
                "PrizePicksLineHistoryResponse": {
                    "type": "object",
                    "required": [
                        "market_key", "event_id", "player_provider_id",
                        "player_name", "prop_type", "first_seen", "last_seen",
                        "previous_line", "current_line", "movement", "line_history",
                    ],
                    "properties": {
                        "market_key": {"type": "string"},
                        "event_id": {"type": ["string", "null"]},
                        "player_provider_id": {"type": ["string", "null"]},
                        "player_name": {"type": "string"},
                        "prop_type": {"type": "string"},
                        "first_seen": {"type": "integer"},
                        "last_seen": {"type": "integer"},
                        "previous_line": {"type": ["number", "null"]},
                        "current_line": {"type": ["number", "null"]},
                        "movement": {"type": ["number", "null"]},
                        "line_history": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/PrizePicksLineHistoryPoint"},
                        },
                    },
                },
            },
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
            # ── PREDICT ───────────────────────────────────────────────────────
            "/api/jarvis/save-pick/soccer": {
                "post": {
                    "operationId": "saveSoccerPick",
                    "summary": "Predict AND save a soccer prop pick to the owner's My Picks ledger.",
                    "description": "Runs the full 13-stage production pipeline and saves the result to the private owner's My Picks ledger. The owner account is resolved server-side; the request contains only prediction inputs. Returns pick_id, tracking_id, warnings, and the model summary.",
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
                                        "prop_type":  {"type": "string",  "description": "pass_attempts | passes | key_passes | shots | shots_on_target | tackles | clearances | saves | goals | dribbles | crosses | interceptions | blocks | fouls_drawn | fouls_committed | duels_won", "default": "pass_attempts"},
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
                        "200": {"description": "saved.pick_id, saved.tracking_id, correlation_warnings, and summary with recommendation, p_over, p_under, prop_historical_rate, prop_historical_n, confidence_score, edge_rating, safety_rating."},
                        "401": {"description": "Invalid or missing JARVIS bearer token."},
                        "404": {"description": "Fixture or player not found."},
                        "409": {"description": "Pick already saved for this player/prop/fixture. Delete it first."},
                        "422": {"description": "Could not resolve player in fixture, or invalid prop."},
                        "502": {"description": "Prediction or save engine error."},
                        "507": {"description": "Database storage full — free Atlas storage and retry."},
                    },
                }
            },
            "/api/jarvis/full-audit/soccer": {
                "post": {
                    "operationId": "runFullSoccerAudit",
                    "summary": "MANDATORY: Run RP once and return a separate JARVIS audit packet.",
                    "description": "Runs Reverse Picks once and returns immutable RP values beside provenance-labeled audit modules. Includes available game-state, first-goal, news, lineup, injury, role, and contradiction evidence. Missing evidence is UNKNOWN; audit evidence never overrides RP math.",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["fixture_id", "player_id", "prop_type", "line"],
                                    "properties": {
                                        "fixture_id": {"type": "integer", "description": "API-Sports fixture ID."},
                                        "player_id": {"type": "integer", "description": "API-Sports player ID."},
                                        "prop_type": {"type": "string", "default": "pass_attempts"},
                                        "line": {"type": "number"},
                                        "odds": {"type": "object"},
                                        "position_override": {"type": "string"},
                                        "role_override": {"type": "string"},
                                    },
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": {"description": "RP diagnostic plus independent audit packet. jarvis_brief includes game_state, first_goal_market, first_goal_regime_change, news_intelligence, news_brief, news_warnings, and lineup_rerun_required."},
                        "401": {"description": "Invalid or missing JARVIS bearer token."},
                        "404": {"description": "Audit feature disabled."},
                        "422": {"description": "Fixture/player could not be resolved."},
                        "502": {"description": "Prediction engine error."},
                    },
                }
            },
            "/api/jarvis/tactical-memory": {
                "get": {
                    "operationId": "getTacticalMemory",
                    "summary": "Retrieve bounded advisory tactical memory",
                    "description": "Owner-only retrieval of team fingerprints, player roles, matchup interactions, and postmortems. Tactical memory never changes Reverse Picks math.",
                    "parameters": [
                        _param("memory_type", "string", False, "team_fingerprint | player_role | matchup_interaction | postmortem."),
                        _param("team_id", "integer", False, "Provider team ID."),
                        _param("opponent_id", "integer", False, "Provider opponent team ID."),
                        _param("player_id", "integer", False, "Provider player ID."),
                        _param("role", "string", False, "Exact tactical role."),
                        _param("manager_regime", "string", False, "Manager or tactical regime."),
                        _param("venue", "string", False, "home or away."),
                        _param("prop_type", "string", False, "Relevant player prop."),
                        _param("since", "string", False, "Earliest observed_at value."),
                        _param("until", "string", False, "Latest observed_at value."),
                        _param("include_stale", "boolean", False, "Include historical superseded records."),
                        _param("limit", "integer", False, "Maximum records; capped at 100."),
                    ],
                    "responses": {"200": {"description": "Bounded tactical memory records"}, "401": {"description": "Unauthorized"}},
                },
                "post": {
                    "operationId": "upsertTacticalMemory",
                    "summary": "Save a versioned tactical memory observation",
                    "description": "Owner-only append-only write. A replacement creates a new version and marks the prior matching observation stale. Do not store credentials or provider secrets.",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["memory_type", "identity", "confidence", "sample_size", "provenance", "payload"],
                                    "properties": {
                                        "memory_type": {"type": "string", "enum": ["team_fingerprint", "player_role", "matchup_interaction", "postmortem"]},
                                        "identity": {"type": "object", "minProperties": 1},
                                        "competition": {"type": "object"},
                                        "context": {"type": "object"},
                                        "confidence": {"type": "number", "minimum": 0, "maximum": 100},
                                        "sample_size": {"type": "integer", "minimum": 0},
                                        "provenance": {"type": "array", "minItems": 1, "items": {"type": "object"}},
                                        "validity": {"type": "object"},
                                        "payload": {"type": "object", "minProperties": 1},
                                        "schema_version": {"type": "string", "default": "jarvis-tactical-memory.v1"},
                                    },
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "Saved versioned tactical memory record"}, "401": {"description": "Unauthorized"}, "422": {"description": "Invalid record"}},
                },
            },
            "/api/jarvis/tactical-memory/team-fingerprint": {
                "get": {
                    "operationId": "getTeamFingerprint",
                    "summary": "Retrieve a team's tactical fingerprint",
                    "parameters": [
                        _param("team_id", "integer", True, "Provider team ID."),
                        _param("opponent_id", "integer", False, "Optional opponent team ID."),
                        _param("venue", "string", False, "home or away."),
                        _param("limit", "integer", False, "Maximum records; capped at 100."),
                    ],
                    "responses": {"200": {"description": "Team fingerprint records"}, "401": {"description": "Unauthorized"}},
                }
            },
            "/api/jarvis/tactical-memory/player-role": {
                "get": {
                    "operationId": "getPlayerRoleMemory",
                    "summary": "Retrieve player-specific role memory",
                    "parameters": [
                        _param("player_id", "integer", True, "Provider player ID."),
                        _param("role", "string", False, "Optional exact tactical role."),
                        _param("limit", "integer", False, "Maximum records; capped at 100."),
                    ],
                    "responses": {"200": {"description": "Player role records"}, "401": {"description": "Unauthorized"}},
                }
            },
            "/api/jarvis/tactical-memory/invalidate": {
                "post": {
                    "operationId": "invalidateTacticalMemory",
                    "summary": "Mark outdated tactical memory stale",
                    "description": "Marks related observations stale after a manager, formation, transfer, injury/return, or tactical-role change without deleting history.",
                    "parameters": [
                        _param("team_id", "integer", False, "Provider team ID."),
                        _param("player_id", "integer", False, "Provider player ID."),
                        _param("manager_regime", "string", False, "Current manager/regime; older regimes become stale."),
                        _param("reason", "string", False, "Reason for invalidation."),
                    ],
                    "responses": {"200": {"description": "Number of records marked stale"}, "401": {"description": "Unauthorized"}, "422": {"description": "team_id or player_id is required"}},
                }
            },
            "/api/jarvis/calibration": {
                "get": {
                    "operationId": "getCalibration",
                    "summary": "MANDATORY before final answer: return settled-pick calibration.",
                    "description": "Returns hit rate, rolling last-25/50/100, lifetime metrics, Brier score, log loss, calibration error, Wilson intervals, and sample-size warnings.",
                    "parameters": [
                        _param("prop_type", "string", False, "Filter by prop type."),
                        _param("role", "string", False, "Filter by exact stored role."),
                        _param("position", "string", False, "Filter by position."),
                        _param("league_id", "integer", False, "Filter by league."),
                        _param("venue", "string", False, "Filter by home/away."),
                        _param("side", "string", False, "Filter by over/under."),
                        _param("model_version", "string", False, "Filter by immutable model version."),
                        _param("limit", "integer", False, "Maximum settled rows to inspect."),
                    ],
                    "responses": {
                        "200": {"description": "Calibration summary with no-fake-precision warnings."},
                        "401": {"description": "Invalid or missing bearer token."},
                    },
                }
            },
            "/api/jarvis/stat-definitions": {
                "get": {
                    "operationId": "getStatDefinitions",
                    "summary": "MANDATORY for unknown props: return stat definitions.",
                    "parameters": [
                        _param("prop_type", "string", False, "Return one prop definition."),
                    ],
                    "responses": {
                        "200": {"description": "Configured definition or explicit UNKNOWN."},
                        "401": {"description": "Invalid or missing bearer token."},
                    },
                }
            },
            "/api/jarvis/prediction-screenshots": {
                "post": {
                    "operationId": "getPredictionScreenshots",
                    "summary": "Render prediction sections and return short-lived screenshot URLs.",
                    "description": "Runs the production soccer prediction, renders a server-side report with the same model values, and captures selected sections with Chromium. Returns opaque temporary image URLs; no app credentials are accepted or returned.",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["fixture_id", "player_id", "prop_type", "line"],
                                    "properties": {
                                        "fixture_id": {"type": "integer", "description": "API-Sports fixture ID."},
                                        "player_id": {"type": "integer", "description": "API-Sports player ID."},
                                        "prop_type": {"type": "string", "description": "Player prop type.", "default": "pass_attempts"},
                                        "line": {"type": "number", "description": "Player prop line."},
                                        "sections": {"type": "array", "items": {"type": "string", "enum": ["read", "form", "matchup", "context", "picks"]}, "description": "Sections to capture. Defaults to read, form, matchup, context."},
                                        "pick_id": {"type": "string", "description": "Optional saved pick ID. When supplied, the owner My Picks card is included in the picks section."},
                                    },
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": {"description": "Opaque temporary image URLs keyed by section, plus summary. URLs expire after 10 minutes and require the JARVIS bearer token."},
                        "401": {"description": "Invalid or missing JARVIS bearer token."},
                        "404": {"description": "Fixture, player, or saved pick not found."},
                        "422": {"description": "Invalid section or prediction input."},
                        "502": {"description": "Prediction or browser rendering error."},
                    },
                }
            },
            "/api/jarvis/prediction-screenshots/{handle}/{section}": {
                "get": {
                    "operationId": "getPredictionScreenshotFile",
                    "summary": "Fetch one authenticated temporary prediction screenshot.",
                    "description": "Downloads one PNG returned by getPredictionScreenshots. The opaque handle expires after 10 minutes and the JARVIS bearer token is required.",
                    "parameters": [
                        {"name": "handle", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "section", "in": "path", "required": True, "schema": {"type": "string", "enum": ["read", "form", "matchup", "context", "picks"]}},
                    ],
                    "responses": {
                        "200": {"description": "PNG image bytes."},
                        "401": {"description": "Invalid or missing JARVIS bearer token."},
                        "404": {"description": "Screenshot expired or not found."},
                    },
                }
            },
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
                                        "prop_type":  {"type": "string",  "description": "pass_attempts | passes | key_passes | shots | shots_on_target | tackles | clearances | saves | goals | dribbles | crosses | interceptions | blocks | fouls_drawn | fouls_committed | duels_won", "default": "pass_attempts"},
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
                        "200": {"description": "Diagnostic: jarvis_brief.p_over/p_under (Bayesian %, 0-100) + jarvis_brief.prop_historical_rate/prop_historical_n (system hit rate). Also in diagnostic.final. p_over+p_under sum to ~100; prop_historical_rate null when <10 settled picks for this bucket."},
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
                                        "prop_type":     {"type": "string",  "description": "pass_attempts | passes | key_passes | shots | shots_on_target | tackles | clearances | saves | goals | dribbles | crosses | interceptions | blocks | fouls_drawn | fouls_committed | duels_won", "default": "pass_attempts"},
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
                        "200": {"description": "jarvis_brief contains p_over, p_under (Bayesian direction probability, 0-100), prop_historical_rate (settled hit rate %, null if no data), prop_historical_n (sample count). Also has confidence_score, edge_rating, recommendation, projection, and tactical summaries."},
                        "401": {"description": "Invalid or missing bearer token."},
                        "422": {"description": "Invalid prop type or parameter."},
                        "502": {"description": "Prediction engine error."},
                    },
                }
            },
            "/api/jarvis/resolve-soccer-prop": {
                "get": {
                    "operationId": "resolveSoccerProp",
                    "summary": "Resolve a soccer prop to verified player and fixture IDs.",
                    "description": "Resolves player name, team, opponent, and date to one verified fixture identity. Ambiguous or unavailable data returns UNKNOWN; no IDs are guessed.",
                    "parameters": [
                        _param("player_name", "string", True, "Player name."),
                        _param("team", "string", False, "Player team name or team ID."),
                        _param("opponent", "string", False, "Opponent name."),
                        _param("date", "string", False, "Fixture date in YYYY-MM-DD format."),
                        _param("season", "integer", False, "API-Sports season year."),
                    ],
                    "security": [{"BearerAuth": []}],
                    "responses": {
                        "200": {"description": "Verified fixture, player, team, opponent, league, venue, season, and evidence."},
                        "401": {"description": "Invalid or missing bearer token."},
                        "422": {"description": "Identity or fixture is UNKNOWN, ambiguous, stale, or unavailable."},
                    },
                }
            },
            # ── ROLE PROFILE ──────────────────────────────────────────────────
            "/api/jarvis/role-profile": {
                "get": {
                    "operationId": "getRoleProfile",
                    "summary": "Granular tactical role for one player in one fixture",
                    "description": (
                        "Returns observed JARVIS role (ball-playing CB, destroyer 6, single pivot, inside forward, etc.), "
                        "confidence + evidence chain, grid slot, formation context, teammate layout by zone, "
                        "buildup/defensive indicators, and recent role history. No AI calls — fully deterministic."
                    ),
                    "parameters": [
                        _param("fixture_id", "integer", True, "Fixture ID — auto-resolves both teams, venue, league, season."),
                        _param("player_id",  "integer", True, "API-Sports player ID."),
                    ],
                    "responses": {
                        "200": {"description": "Role profile: observed_tactical_role, role_confidence, evidence_used, formation_context, teammate_context, buildup_responsibility, defensive_responsibility, recent_role_history, position_group_for_cohort."},
                        "401": {"description": "Invalid or missing bearer token."},
                        "404": {"description": "Fixture not found."},
                        "422": {"description": "Could not resolve player in fixture."},
                    },
                }
            },
            # ── ROLE OPPONENT COHORT ───────────────────────────────────────────
            "/api/jarvis/role-opponent-cohort": {
                "get": {
                    "operationId": "getRoleOpponentCohort",
                    "summary": "Role-matched players who faced this opponent recently",
                    "description": (
                        "Identifies the player's JARVIS role, then finds players in the same position group "
                        "who played ≥45min against the opponent in their last 6 fixtures. "
                        "Returns per-player match stats and a prop aggregate when prop_type is provided."
                    ),
                    "parameters": [
                        _param("fixture_id", "integer", True,  "Fixture ID — auto-resolves opponent."),
                        _param("player_id",  "integer", True,  "API-Sports player ID."),
                        _param("prop_type",  "string",  False, "pass_attempts | shots | shots_on_target | tackles | clearances | key_passes | dribbles | crosses | goals | interceptions | blocks"),
                    ],
                    "responses": {
                        "200": {"description": "Cohort with player_identity, opponent info, cohort_filter, n_cohort_players, cohort_players (per-match stats), and cohort_aggregate (avg/max/min/median/values for prop)."},
                        "401": {"description": "Invalid or missing bearer token."},
                        "404": {"description": "Fixture not found."},
                        "422": {"description": "Could not resolve player in fixture."},
                    },
                }
            },
            # ── TACTICAL EVIDENCE ─────────────────────────────────────────────
            "/api/jarvis/tactical-evidence": {
                "get": {
                    "operationId": "getTacticalEvidence",
                    "summary": "Raw + derived evidence for one player in one fixture",
                    "description": (
                        "Raw evidence for one player/fixture — does NOT run the prediction pipeline. "
                        "Sections: match logs, per-90s, home/away splits, lineup grid, season stats, "
                        "press intensity, concession profile, possession, buildup proxies, rest days, "
                        "injuries, H2H, odds. Every section carries a _source label."
                    ),
                    "parameters": [
                        _param("fixture_id", "integer", True,  "Fixture ID — auto-resolves both teams, venue, league, season."),
                        _param("player_id",  "integer", True,  "API-Sports player ID."),
                        _param("prop_type",  "string",  False, "pass_attempts | shots | shots_on_target | tackles | clearances | saves | goals | key_passes | dribbles | interceptions | blocks | crosses | fouls_drawn | fouls_committed | duels_won"),
                    ],
                    "responses": {
                        "200": {"description": "Tactical evidence bundle with _source labels on every section."},
                        "401": {"description": "Invalid or missing bearer token."},
                        "404": {"description": "Fixture not found."},
                        "422": {"description": "Could not resolve player in fixture."},
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
            "/api/jarvis/prizepicks/board": {
                "post": {
                    "operationId": "refreshPrizePicksBoard",
                    "summary": "Fetch and save the current PrizePicks board",
                    "description": "Queries SportsGameOdds for currently available PrizePicks player markets, saves the latest snapshot for JARVIS, and returns it immediately.",
                    "security": [{"BearerAuth": []}],
                    "parameters": [
                        {
                            "name": "sport",
                            "in": "query",
                            "required": False,
                            "description": "Provider sport filter.",
                            "schema": {
                                "type": "string",
                                "enum": ["SOCCER", "NBA", "MLB", "NFL", "NHL", "WTA", "ALL"],
                                "default": "SOCCER",
                            },
                        },
                        {
                            "name": "hours",
                            "in": "query",
                            "required": False,
                            "description": "Upcoming board window in hours.",
                            "schema": {"type": "integer", "default": 72, "minimum": 6, "maximum": 168},
                        },
                        {
                            "name": "limit",
                            "in": "query",
                            "required": False,
                            "description": "Maximum markets to return.",
                            "schema": {"type": "integer", "default": 100, "minimum": 1, "maximum": 100},
                        },
                    ],
                    "responses": {
                        "200": {
                            "description": "Current PrizePicks market board and save status.",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/PrizePicksBoardResponse"},
                                },
                            },
                        },
                        "400": {"description": "Invalid board query parameters."},
                        "401": {"description": "Invalid or missing bearer token."},
                        "404": {"description": "No matching markets were found."},
                        "503": {"description": "SportsGameOdds is not configured."},
                    },
                },
                "get": {
                    "operationId": "getPrizePicksBoard",
                    "summary": "Read the last saved PrizePicks board",
                    "description": "Returns the most recently saved board snapshot without calling SportsGameOdds.",
                    "security": [{"BearerAuth": []}],
                    "responses": {
                        "200": {
                            "description": "Saved PrizePicks market board.",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/PrizePicksBoardResponse"},
                                },
                            },
                        },
                        "400": {"description": "Invalid request."},
                        "401": {"description": "Invalid or missing bearer token."},
                        "404": {"description": "No PrizePicks board has been saved yet."},
                    },
                },
            },
            "/api/jarvis/prizepicks/markets": {
                "get": {
                    "operationId": "searchPrizePicksMarkets",
                    "summary": "Search the latest saved PrizePicks markets",
                    "description": "Reads only the latest saved PrizePicks snapshot and returns at most 100 matching markets without calling SportsGameOdds.",
                    "parameters": [
                        _param("home_team", "string", False, "Home team name, substring match."),
                        _param("away_team", "string", False, "Away team name, substring match."),
                        _param("team", "string", False, "Either team name, substring match."),
                        _param("player_name", "string", False, "Player name, substring match."),
                        _param("prop_type", "string", False, "Prop type, substring match."),
                        {
                            "name": "limit",
                            "in": "query",
                            "required": False,
                            "description": "Maximum matching markets to return.",
                            "schema": {"type": "integer", "default": 25, "minimum": 1, "maximum": 100},
                        },
                    ],
                    "security": [{"BearerAuth": []}],
                    "responses": {
                        "200": {
                            "description": "Bounded matching markets from the saved snapshot.",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/PrizePicksBoardResponse"},
                                },
                            },
                        },
                        "401": {"description": "Invalid or missing bearer token."},
                        "404": {"description": "No PrizePicks board has been saved yet."},
                        "503": {"description": "Saved PrizePicks board is temporarily unavailable."},
                    },
                }
            },
            "/api/jarvis/prizepicks/line-history": {
                "get": {
                    "operationId": "getPrizePicksLineHistory",
                    "summary": "Get timestamped line movement for one market",
                    "description": "Returns previous_line, current_line, first_seen, last_seen, movement, and up to 50 timestamped observations for a saved market.",
                    "parameters": [
                        _param("market_key", "string", True, "Market key returned in the board market object."),
                    ],
                    "security": [{"BearerAuth": []}],
                    "responses": {
                        "200": {
                            "description": "Complete saved line history.",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/PrizePicksLineHistoryResponse"},
                                },
                            },
                        },
                        "400": {"description": "Missing or invalid market_key."},
                        "401": {"description": "Invalid or missing bearer token."},
                        "404": {"description": "No history exists for this market."},
                    },
                },
            },
            "/api/jarvis/runtime/dominance-inputs": {
                "get": {
                    "operationId": "getRuntimeDominanceInputs",
                    "summary": "Read persisted exact-fixture dominance inputs",
                    "description": "Read-only audit lookup. Returns the stored match-dominance packet for an exact fixture/team pair, or UNKNOWN when no exact snapshot exists. Never runs prediction.",
                    "parameters": [
                        _param("fixture_id", "integer", True, "Exact API-Sports fixture ID."),
                        _param("team_id", "integer", True, "Player team API-Sports ID."),
                    ],
                    "security": [{"BearerAuth": []}],
                    "responses": {"200": {"description": "Dominance inputs or explicit UNKNOWN."}, "401": {"description": "Unauthorized"}},
                },
            },
            "/api/jarvis/runtime/hyperprior": {
                "get": {
                    "operationId": "getRuntimeHyperprior",
                    "summary": "Read persisted runtime hyperprior",
                    "description": "Read-only lookup of an exact caller-supplied hyperprior already persisted with runtime Bayesian metrics. It does not recreate or apply a prior.",
                    "parameters": [
                        _param("prop_type", "string", True, "Canonical prop type."),
                        _param("league_id", "integer", True, "League ID."),
                        _param("position", "string", False, "Player position."),
                        _param("role", "string", False, "Tactical role."),
                    ],
                    "security": [{"BearerAuth": []}],
                    "responses": {"200": {"description": "Hyperprior value or explicit UNKNOWN."}, "401": {"description": "Unauthorized"}},
                },
            },
            "/api/jarvis/runtime/prop-safety": {
                "get": {
                    "operationId": "getRuntimePropSafety",
                    "summary": "Read current empirical prop-safety bucket",
                    "description": "Read-only loaded safety-cache lookup with sample, hit rate, thresholds, and safety state. Missing buckets are UNKNOWN, never estimated.",
                    "parameters": [
                        _param("prop_type", "string", True, "Prop type."),
                        _param("side", "string", True, "over or under."),
                        _param("line", "number", False, "Requested line, retained as query context."),
                        _param("league_id", "integer", False, "League ID."),
                        _param("position", "string", False, "Player position."),
                        _param("role", "string", False, "Tactical role context."),
                    ],
                    "security": [{"BearerAuth": []}],
                    "responses": {"200": {"description": "Safety bucket or explicit UNKNOWN."}, "401": {"description": "Unauthorized"}},
                },
            },
            "/api/jarvis/runtime/calibration-rows": {
                "get": {
                    "operationId": "getRuntimeCalibrationRows",
                    "summary": "Read bounded deduplicated settled calibration rows",
                    "description": "Read-only settled ledger sample filtered by prop, direction, league, position, role, venue, and model version. Push, DNP, void, and pending rows are excluded.",
                    "parameters": [
                        _param("prop_type", "string", False, "Prop type."),
                        _param("direction", "string", False, "OVER or UNDER."),
                        _param("line_band", "string", False, "Half-open line range formatted lower-upper, e.g. 55-70."),
                        _param("league_id", "integer", False, "League ID."),
                        _param("position", "string", False, "Player position."),
                        _param("role", "string", False, "Tactical role."),
                        _param("venue", "string", False, "home or away."),
                        _param("model_version", "string", False, "Exact model version."),
                        _param("date_from", "string", False, "Inclusive settlement date YYYY-MM-DD."),
                        _param("date_to", "string", False, "Inclusive settlement date YYYY-MM-DD."),
                        {"name": "limit", "in": "query", "schema": {"type": "integer", "default": 50, "minimum": 1, "maximum": _RUNTIME_ROW_LIMIT}, "description": "Maximum returned rows."},
                    ],
                    "security": [{"BearerAuth": []}],
                    "responses": {"200": {"description": "Bounded settled calibration rows with provenance."}, "401": {"description": "Unauthorized"}},
                },
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
    }
    # ChatGPT Actions reject schemas over 30 operations. These remain live
    # routes, but are diagnostic/download helpers rather than orchestration
    # primitives and are intentionally omitted from the importer schema.
    for diagnostic_path in (
        "/api/jarvis/prizepicks/board",
        "/api/jarvis/prizepicks/markets",
        "/api/jarvis/prizepicks/line-history",
        "/api/jarvis/standings",
        "/api/jarvis/stat-definitions",
        "/api/jarvis/prediction-screenshots",
        "/api/jarvis/prediction-screenshots/{handle}/{section}",
        "/api/jarvis/full-audit/soccer",
    ):
        schema["paths"].pop(diagnostic_path, None)
    return JSONResponse(content=schema)


@router.get("/api/jarvis/docs")
async def jarvis_docs():
    """Full API reference — no authentication required."""
    base = "https://reversepicks.com"
    return JSONResponse(content={
        "service": "JARVIS Football API",
        "version": "3.1.0",
        "base_url": base,
        "openapi_schema": f"{base}/api/jarvis/openapi.json",
        "direct_api_football_schema": f"{base}/api/jarvis/api-football/openapi.json",
        "authentication": {
            "type": "Bearer token",
            "header": "Authorization",
            "format": "Authorization: Bearer <JARVIS_API_KEY>",
            "note": "health, docs, and openapi.json endpoints do not require authentication.",
        },
        "mandatory_workflow": {
            "per_analysis": [
                {
                    "step": 1,
                    "call": "GET /api/jarvis/resolve-soccer-prop",
                    "rule": "Resolve player, fixture, teams, venue, league, and season before prediction.",
                },
                {
                    "step": 2,
                    "call": "POST /api/jarvis/predict/soccer",
                    "rule": "Run immutable Reverse Picks production prediction first.",
                },
                {
                    "step": 3,
                    "call": "Primitive evidence endpoints",
                    "rule": "Build the independent audit directly; do not depend exclusively on full-audit.",
                },
                {
                    "step": 4,
                    "call": "GET /api/jarvis/calibration",
                    "rule": "Call before the final answer and use UNKNOWN rather than invented data.",
                },
            ],
            "interpretation": [
                "Audit role, fixture, lineup, stats, events, injuries, odds, history, venue possession, current line, and line history.",
                "RP math remains immutable; direct audit evidence never changes the production prediction.",
            ],
        },
        "endpoint_groups": {
            "public": ["/api/jarvis/health", "/api/jarvis/docs", "/api/jarvis/openapi.json"],
            "predict": ["/api/jarvis/predict/soccer", "/api/jarvis/predict"],
            "save": ["/api/jarvis/save-pick/soccer"],
            "prizepicks": [
                "/api/jarvis/prizepicks/board",
                "/api/jarvis/prizepicks/markets",
                "/api/jarvis/prizepicks/line-history",
            ],
            "audit": [
                "/api/jarvis/full-audit/soccer",
                "/api/jarvis/calibration",
                "/api/jarvis/stat-definitions",
                "/api/jarvis/audit-status",
            ],
            "screenshots": ["/api/jarvis/prediction-screenshots"],
            "tactical_evidence": ["/api/jarvis/tactical-evidence"],
            "role_analysis": ["/api/jarvis/role-profile", "/api/jarvis/role-opponent-cohort"],
            "aggregator": ["/api/jarvis/match-context"],
            "market_board": [
                "/api/jarvis/prizepicks/board",
                "/api/jarvis/prizepicks/line-history",
            ],
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
            "identity_resolution": ["/api/jarvis/resolve-soccer-prop"],
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


@router.get("/api/jarvis/api-football/openapi.json", include_in_schema=False)
async def jarvis_direct_api_football_openapi():
    """Importable raw API-Football Action schema.

    This is intentionally a separate schema from the 30-operation JARVIS
    orchestration schema. The credential is supplied by the Action caller via
    the x-apisports-key security scheme and is never held in this response.
    """
    resources = [
        "fixtures", "fixtures/statistics", "fixtures/players",
        "fixtures/lineups", "fixtures/events", "injuries", "odds",
        "teams", "teams/statistics", "players", "players/squads",
        "standings", "leagues", "fixtures/headtohead",
    ]
    paths = {}
    for resource in resources:
        paths[f"/{resource}"] = {
            "get": {
                "operationId": "apiFootball_" + resource.replace("/", "_"),
                "summary": f"Raw API-Football {resource} data",
                "description": (
                    "Direct read-only API-Football resource. Query parameters "
                    "are provider-defined; use the provider's documented "
                    "parameters for this resource."
                ),
                "parameters": [
                    {
                        "name": name,
                        "in": "query",
                        "required": False,
                        "schema": {"type": "string" if name in {"date", "search", "live", "h2h"} else "integer"},
                        "description": f"API-Football {name} filter.",
                    }
                    for name in (
                        "id", "fixture", "team", "player", "league", "season",
                        "date", "last", "h2h", "search", "current", "live",
                        "page", "per_page",
                    )
                ],
                "security": [{"ApiSportsKey": []}],
                "responses": {
                    "200": {"description": "Raw API-Football response."},
                    "401": {"description": "Invalid or missing x-apisports-key."},
                    "429": {"description": "Provider quota exceeded."},
                },
            }
        }
    return JSONResponse(content={
        "openapi": "3.1.0",
        "info": {
            "title": "API-Football Direct Read API",
            "version": "1.0.0",
            "description": (
                "Raw read-only API-Football access for JARVIS evidence "
                "reproduction. Credentials belong in Action authentication."
            ),
        },
        "servers": [{"url": _API_SPORTS_BASE}],
        "components": {
            "schemas": {},
            "securitySchemes": {
                "ApiSportsKey": {
                    "type": "apiKey",
                    "in": "header",
                    "name": "x-apisports-key",
                    "description": "API-Football credential supplied by Action authentication.",
                }
            }
        },
        "paths": paths,
    })


# ─────────────────────────────────────────────────────────────────────────────
# PRIZEPICKS BOARD — live SportsGameOdds board for JARVIS
# ─────────────────────────────────────────────────────────────────────────────

def _saved_market_board_response(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": snapshot.get("source", "SportsGameOdds"),
        "bookmaker": "PrizePicks",
        "fetched_at": snapshot.get("fetched_at"),
        "market_count": len(snapshot.get("markets") or []),
        "markets": snapshot.get("markets") or [],
        "saved": True,
    }


def _market_history_key(market: dict[str, Any]) -> str:
    """Stable identity for one provider event/player/stat market."""
    return "|".join(
        str(market.get(key) or "")
        for key in ("eventId", "playerProviderId", "statId", "propType")
    )


async def _save_market_history(market: dict[str, Any], observed_at: int) -> dict[str, Any]:
    """Append one market observation and return its complete line history."""
    history_key = _market_history_key(market)
    prizepicks = (market.get("bookmakers") or {}).get("prizepicks") or {}
    current_line = prizepicks.get("line")
    if current_line is None:
        current_line = market.get("marketLine")

    collection = db.jarvis_prizepicks_market_history
    existing = await collection.find_one({"_id": history_key}, {"_id": 0})
    previous_line = (existing or {}).get("current_line")
    first_seen = (existing or {}).get("first_seen") or observed_at
    line_history = list((existing or {}).get("line_history") or [])
    observation = {
        "line": current_line,
        "observed_at": observed_at,
    }
    # Do not create repeated observations when JARVIS refreshes the same
    # provider board multiple times during one second.
    if not line_history or line_history[-1] != observation:
        line_history.append(observation)
    line_history = line_history[-50:]
    await collection.replace_one(
        {"_id": history_key},
        {
            "_id": history_key,
            "market_key": history_key,
            "event_id": market.get("eventId"),
            "player_provider_id": market.get("playerProviderId"),
            "player_name": market.get("playerName"),
            "stat_id": market.get("statId"),
            "prop_type": market.get("propType"),
            "market_name": market.get("marketName"),
            "first_seen": first_seen,
            "last_seen": observed_at,
            "previous_line": previous_line,
            "current_line": current_line,
            "line_history": line_history,
        },
        upsert=True,
    )
    movement = None
    if previous_line is not None and current_line is not None:
        movement = round(float(current_line) - float(previous_line), 2)
    return {
        "marketKey": history_key,
        "previous_line": previous_line,
        "current_line": current_line,
        "first_seen": first_seen,
        "last_seen": observed_at,
        "movement": movement,
        "line_history": line_history,
    }


async def _attach_market_history(
    markets: list[dict[str, Any]],
    observed_at: int,
) -> tuple[list[dict[str, Any]], str]:
    """Persist and attach line movement without making board fetch fragile."""
    semaphore = asyncio.Semaphore(10)

    async def save_one(market: dict[str, Any]):
        async with semaphore:
            return market, await _save_market_history(market, observed_at)

    try:
        results = await asyncio.gather(*(save_one(market) for market in markets))
    except Exception as exc:
        import logging
        logging.getLogger("jarvis").warning(
            "PrizePicks line-history write skipped: %s", type(exc).__name__
        )
        return markets, "history_save_unavailable"

    for market, history in results:
        market["marketKey"] = history["marketKey"]
        market["previous_line"] = history["previous_line"]
        market["current_line"] = history["current_line"]
        market["first_seen"] = history["first_seen"]
        market["last_seen"] = history["last_seen"]
        market["movement"] = history["movement"]
        market["line_history"] = history["line_history"]
    return markets, "saved"


@router.post("/api/jarvis/prizepicks/board")
async def jarvis_refresh_prizepicks_board(
    authorization: Optional[str] = Header(default=None),
    sport: str = Query(default="SOCCER", description="SportsGameOdds sport ID, or ALL."),
    hours: int = Query(default=72, ge=6, le=168),
    limit: int = Query(default=100, ge=1, le=100),
):
    """Fetch the live PrizePicks board and save its latest snapshot for JARVIS."""
    _require_auth(authorization)
    if not os.environ.get("SPORTSGAMEODDS_API_KEY", "").strip():
        raise HTTPException(
            503,
            detail={"error": "SportsGameOdds is not configured on the server."},
        )

    markets = await list_market_board(
        hours=hours,
        limit=limit,
        sport_id=sport.strip().upper() or "SOCCER",
    )
    observed_at = int(time.time())
    markets, history_status = await _attach_market_history(markets, observed_at)
    snapshot = {
        "_id": "latest",
        "source": "SportsGameOdds",
        "bookmaker": "PrizePicks",
        "fetched_at": observed_at,
        "sport": sport.strip().upper() or "SOCCER",
        "hours": hours,
        "markets": markets,
    }
    save_status = "saved"
    try:
        await db.jarvis_prizepicks_board.replace_one(
            {"_id": "latest"},
            snapshot,
            upsert=True,
        )
    except Exception as exc:
        # A provider response is still useful during a transient Atlas write
        # outage, but JARVIS must be told that persistence did not complete.
        save_status = "save_unavailable"
        import logging
        logging.getLogger("jarvis").warning(
            "PrizePicks board snapshot write skipped: %s", type(exc).__name__
        )

    response = _saved_market_board_response(snapshot)
    response["saved"] = save_status == "saved"
    response["save_status"] = save_status
    response["history_status"] = history_status
    return JSONResponse(content=response)


@router.get("/api/jarvis/prizepicks/board")
async def jarvis_get_saved_prizepicks_board(
    authorization: Optional[str] = Header(default=None),
):
    """Return the latest saved PrizePicks board without a provider request."""
    _require_auth(authorization)
    try:
        snapshot = await db.jarvis_prizepicks_board.find_one(
            {"_id": "latest"},
            {"_id": 0},
        )
    except Exception as exc:
        raise HTTPException(
            503,
            detail={"error": "Saved PrizePicks board is temporarily unavailable."},
        ) from exc
    if not snapshot:
        raise HTTPException(
            404,
            detail={"error": "No PrizePicks board has been saved yet."},
        )
    response = _saved_market_board_response(snapshot)
    response["save_status"] = "saved"
    return JSONResponse(content=response)


def _filter_saved_prizepicks_markets(
    markets: list[dict[str, Any]],
    *,
    home_team: str | None = None,
    away_team: str | None = None,
    team: str | None = None,
    player_name: str | None = None,
    prop_type: str | None = None,
    limit: int = 25,
) -> list[dict[str, Any]]:
    """Filter one already-saved board without contacting SportsGameOdds."""
    def contains(value: Any, query: str | None) -> bool:
        return not query or str(query).casefold().strip() in str(value or "").casefold()

    filtered = []
    for market in markets:
        if not contains(market.get("homeTeam"), home_team):
            continue
        if not contains(market.get("awayTeam"), away_team):
            continue
        if team and not (
            contains(market.get("homeTeam"), team)
            or contains(market.get("awayTeam"), team)
        ):
            continue
        if not contains(market.get("playerName"), player_name):
            continue
        if not contains(market.get("propType"), prop_type):
            continue
        filtered.append(market)
        if len(filtered) >= limit:
            break
    return filtered


@router.get("/api/jarvis/prizepicks/markets")
async def jarvis_search_saved_prizepicks_markets(
    home_team: Optional[str] = Query(default=None),
    away_team: Optional[str] = Query(default=None),
    team: Optional[str] = Query(default=None),
    player_name: Optional[str] = Query(default=None),
    prop_type: Optional[str] = Query(default=None),
    limit: int = Query(default=25, ge=1, le=100),
    authorization: Optional[str] = Header(default=None),
):
    """Return a bounded subset of the latest saved PrizePicks board."""
    _require_auth(authorization)
    try:
        snapshot = await db.jarvis_prizepicks_board.find_one(
            {"_id": "latest"},
            {"_id": 0},
        )
    except Exception as exc:
        raise HTTPException(
            503,
            detail={"error": "Saved PrizePicks board is temporarily unavailable."},
        ) from exc
    if not snapshot:
        raise HTTPException(
            404,
            detail={"error": "No PrizePicks board has been saved yet."},
        )

    markets = _filter_saved_prizepicks_markets(
        snapshot.get("markets") or [],
        home_team=home_team,
        away_team=away_team,
        team=team,
        player_name=player_name,
        prop_type=prop_type,
        limit=limit,
    )
    response = _saved_market_board_response({**snapshot, "markets": markets})
    response["save_status"] = "saved"
    response["filters"] = {
        "home_team": home_team,
        "away_team": away_team,
        "team": team,
        "player_name": player_name,
        "prop_type": prop_type,
        "limit": limit,
    }
    return JSONResponse(content=response)


@router.get("/api/jarvis/prizepicks/line-history")
async def jarvis_get_prizepicks_line_history(
    market_key: str = Query(..., min_length=3),
    authorization: Optional[str] = Header(default=None),
):
    """Return the complete timestamped line history for one saved market."""
    _require_auth(authorization)
    try:
        history = await db.jarvis_prizepicks_market_history.find_one(
            {"_id": market_key},
            {"_id": 0},
        )
    except Exception as exc:
        raise HTTPException(
            503,
            detail={"error": "PrizePicks line history is temporarily unavailable."},
        ) from exc
    if not history:
        raise HTTPException(
            404,
            detail={"error": "No saved line history exists for that market."},
        )
    return JSONResponse(content=history)


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
    model_metrics = _prediction_metrics(result)

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
        # ── The two numbers that must always be surfaced together ─────────────
        # p_over / p_under: Bayesian probability for each direction (0-100 float)
        "p_over":                model_metrics["pOver"],
        "p_under":               model_metrics["pUnder"],
        # prop_historical_rate: system-wide settled-pick hit rate for this
        # prop+direction (e.g. 62 means 62% of all UNDER pass_attempts picks hit).
        # None when fewer than ~10 settled picks exist for this bucket.
        "prop_historical_rate":  model_metrics["propHistoricalRate"],
        "prop_historical_n":     model_metrics["propHistoricalN"],
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

    # ── Step 2b: fixture-team roster fallback for future fixtures ─────────────
    # API-Football may reject bare /players?id searches. Since the fixture
    # already gives us both verified teams, resolve the player through each
    # team's season roster before trying the legacy player lookup below.
    if not player_team_id:
        try:
            for candidate_team_id, candidate_team_name in (
                (home_id, home_name), (away_id, away_name)
            ):
                roster = await _sports_get(
                    "players",
                    {"team": candidate_team_id, "season": season},
                    cache_ttl=_CACHE_TTL_SCHEDULED,
                )
                for row in roster.get("response", []):
                    candidate = row.get("player") or {}
                    if candidate.get("id") == player_id:
                        player_name = candidate.get("name") or player_name
                        player_team_id = candidate_team_id
                        player_team_name = candidate_team_name
                        resolution_source = "fixture_team_season_roster"
                        break
                if player_team_id:
                    break
        except Exception:
            pass

    # Some competitions expose a roster only through /players/squads rather
    # than the season-filtered /players endpoint.
    if not player_team_id:
        try:
            for candidate_team_id, candidate_team_name in (
                (home_id, home_name), (away_id, away_name)
            ):
                squad = await _sports_get(
                    "players/squads",
                    {"team": candidate_team_id},
                    cache_ttl=_CACHE_TTL_SCHEDULED,
                )
                for squad_row in squad.get("response", []):
                    for candidate in squad_row.get("players") or []:
                        if candidate.get("id") == player_id:
                            player_name = candidate.get("name") or player_name
                            player_team_id = candidate_team_id
                            player_team_name = candidate_team_name
                            resolution_source = "fixture_team_squad"
                            break
                    if player_team_id:
                        break
                if player_team_id:
                    break
        except Exception:
            pass

    # ── Step 2c: /players?id=&season= (future fixtures) ───────────────────────
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
        "player_id":          player_id,
        "team_id":            player_team_id,
        "team_name":          team_name,
        "opponent_id":        opponent_id,
        "opponent_name":      opponent_name,
        "venue":              venue,
        "league_id":          league_id,
        "league_name":        ctx.get("league_name"),
        "competition_country": ctx.get("country"),
        "season":             season,
        "fixture_id":         fixture_id,
        "fixture_date":       ctx.get("date"),
        "fixture_status":     ctx.get("status_short"),
        "fixture_round":      ctx.get("round"),
        "fixture_venue":      ctx.get("venue"),
        "fixture_city":       ctx.get("city"),
        "home_team_id":       home_id,
        "home_team_name":     home_name,
        "away_team_id":       away_id,
        "away_team_name":     away_name,
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
    model_metrics = _prediction_metrics(result)

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
            # pOver/pUnder live at top-level result, not inside bayesianMetrics
            "p_over":                  model_metrics["pOver"],
            "p_under":                 model_metrics["pUnder"],
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
            # System-wide settled-pick hit rate for this prop+direction
            "prop_historical_rate":    model_metrics["propHistoricalRate"],
            "prop_historical_n":       model_metrics["propHistoricalN"],
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
        "first_goal_market": (
            result.get("firstGoalMarket")
            or (result.get("matchFactors") or {}).get("firstGoalMarket")
        ),
        "first_goal_regime_change": (
            result.get("firstGoalRegimeChange")
            or (result.get("matchFactors") or {}).get("firstGoalRegimeChange")
        ),
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


def _audit_first_goal_brief(audit: dict) -> dict:
    """Expose first-goal audit modules in the compact JARVIS brief."""
    modules = audit.get("modules") if isinstance(audit.get("modules"), dict) else {}
    game_state = modules.get("game_state") if isinstance(modules.get("game_state"), dict) else {}
    market = modules.get("first_goal_market") if isinstance(modules.get("first_goal_market"), dict) else {}
    regime = (
        modules.get("first_goal_regime_change")
        if isinstance(modules.get("first_goal_regime_change"), dict)
        else {}
    )
    return {
        "game_state": game_state,
        "first_goal_market": market.get("values") or {},
        "first_goal_regime_change": regime.get("values") or {},
    }


def _audit_news_brief(audit: dict) -> dict:
    """Expose the mandatory shadow news packet in the compact JARVIS brief."""
    modules = audit.get("modules") if isinstance(audit.get("modules"), dict) else {}
    module = modules.get("news_intelligence") if isinstance(modules.get("news_intelligence"), dict) else {}
    values = module.get("values") if isinstance(module.get("values"), dict) else {}
    return {
        "news_intelligence": values,
        "news_brief": values.get("news_brief"),
        "news_warnings": values.get("news_warnings") or [],
        "lineup_rerun_required": bool(
            (values.get("confirmed_lineup_comparison") or {}).get("rerun_required")
        ),
    }


async def _ensure_full_audit_first_goal_context(
    result: dict,
    ctx: dict,
    prop_type: str,
) -> None:
    """Fill the shadow-only audit packet if RP's response budget skipped it."""
    existing = result.get("firstGoalMarket") or (result.get("matchFactors") or {}).get("firstGoalMarket")
    if isinstance(existing, dict) and existing.get("available"):
        return

    from first_goal_engine import build_first_goal_market, get_first_goal_profile

    try:
        profiles = await asyncio.wait_for(
            asyncio.gather(
                get_first_goal_profile(
                    int(ctx.get("team_id") or 0),
                    int(ctx.get("season") or 0),
                    _sports_get,
                    db,
                ),
                get_first_goal_profile(
                    int(ctx.get("opponent_id") or 0),
                    int(ctx.get("season") or 0),
                    _sports_get,
                    db,
                ),
                return_exceptions=True,
            ),
            timeout=28.0,
        )
    except Exception as exc:
        market, regime = build_first_goal_market({}, {}, prop_type)
        market["reason"] = f"First-goal audit retrieval was unavailable: {type(exc).__name__}."
        regime["reason"] = market["reason"]
    else:
        team_profile = profiles[0] if not isinstance(profiles[0], Exception) else {}
        opponent_profile = profiles[1] if not isinstance(profiles[1], Exception) else {}
        market, regime = build_first_goal_market(team_profile, opponent_profile, prop_type)

    # The audit-only fallback executes after the immutable RP prediction. These
    # values are intentionally response metadata, never model inputs.
    result["firstGoalMarket"] = market
    result["firstGoalRegimeChange"] = regime
    factors = result.setdefault("matchFactors", {})
    if isinstance(factors, dict):
        factors["firstGoalMarket"] = market
        factors["firstGoalRegimeChange"] = regime


async def _ensure_full_audit_news_context(
    result: dict,
    ctx: dict,
    fixture_id: int,
) -> None:
    """Research current team news after the immutable RP prediction is complete."""
    from news_intelligence import run_news_intelligence, unknown_news_intelligence

    async def _research_within_budget() -> dict:
        try:
            lineups_payload, injuries_payload = await asyncio.wait_for(
                asyncio.gather(
                    _sports_get_safe(
                        "fixtures/lineups",
                        {"fixture": fixture_id},
                        cache_ttl=60,
                    ),
                    _sports_get_safe(
                        "injuries",
                        {"fixture": fixture_id},
                        cache_ttl=60,
                    ),
                ),
                timeout=4.0,
            )
        except Exception:
            lineups_payload, injuries_payload = None, None
        return await run_news_intelligence(
            context=ctx,
            prediction=result,
            db=db,
            lineups_payload=lineups_payload,
            injuries_payload=injuries_payload,
        )

    try:
        packet = await asyncio.wait_for(
            _research_within_budget(),
            timeout=18.0,
        )
    except Exception as exc:
        packet = unknown_news_intelligence(
            f"Current news research was unavailable: {type(exc).__name__}."
        )

    # This assignment happens only after _rp_predict returned. The packet is
    # response metadata for the audit and is never read by prediction math.
    result["newsIntelligence"] = packet


class JarvisSoccerPredictBody(BaseModel):
    """Minimal soccer predict inputs — fixture+player auto-resolve everything else."""
    fixture_id:        int
    player_id:         int
    prop_type:         str   = "pass_attempts"
    line:              float
    odds:              Optional[dict] = None
    position_override: str   = ""
    role_override:     str   = ""


async def _run_soccer_prediction(
    body: JarvisSoccerPredictBody,
    resolved_context: dict[str, Any] | None = None,
) -> tuple[dict, dict]:
    """Run exactly one untouched RP soccer prediction for JARVIS callers."""
    if not _JARVIS_KEY:
        raise HTTPException(503, detail={"error": "JARVIS_API_KEY not configured."})

    if resolved_context:
        ctx = resolved_context
    else:
        try:
            ctx = await _resolve_soccer_context(body.fixture_id, body.player_id)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(422, detail={"error": f"Context resolution failed: {exc}"})

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

    try:
        result = await _rp_predict(req)
    except HTTPException as exc:
        # Preserve the existing one-retry club-transfer behavior.
        if exc.status_code == 409 and "Current club changed" in str(exc.detail):
            try:
                result = await _rp_predict(req)
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
    if not isinstance(result, dict):
        raise HTTPException(502, detail={"error": "Prediction engine returned an invalid response."})
    return ctx, result


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

    ctx, result = await _run_soccer_prediction(body)

    # ── Return comprehensive diagnostic ───────────────────────────────────────
    diagnostic = _build_soccer_diagnostic(result)
    diagnostic["_resolution"] = {
        "source":     ctx.get("_resolution_source"),
        "fixture_id": body.fixture_id,
        "player_id":  body.player_id,
    }

    # ── 5. Build curated top-level brief (same shape as /api/jarvis/predict) ──
    # Pull from diagnostic.final which is already correctly populated above.
    _df = diagnostic.get("final", {})
    jarvis_brief = {
        "recommendation":          _df.get("recommendation"),
        "confidence_score":        _df.get("confidence_score"),
        "confidence_level":        _df.get("confidence_level"),
        "projected_value":         result.get("projectedValue"),
        "line":                    _df.get("line"),
        "edge_rating":             _df.get("edge_rating"),
        "edge_rating_reason":      _df.get("edge_rating_reason"),
        "safety_rating":           _df.get("safety_rating"),
        "coin_flip":               _df.get("coin_flip", False),
        "low_conviction":          _df.get("low_conviction", False),
        "sharp_summary":           result.get("sharpSummary"),
        "reasoning":               result.get("reasoning"),
        "tactical_breakdown":      result.get("tacticalBreakdown"),
        # ── The two numbers that must always travel together ─────────────────
        # p_over/p_under: Bayesian direction probability (0-100 float, sum ~100)
        "p_over":                  _df.get("p_over"),
        "p_under":                 _df.get("p_under"),
        # prop_historical_rate: settled-pick hit rate for this prop+direction.
        # null when fewer than ~10 settled picks exist for this bucket.
        "prop_historical_rate":    _df.get("prop_historical_rate"),
        "prop_historical_n":       _df.get("prop_historical_n"),
        "line_deviation_hit_rate": _df.get("line_deviation_hit_rate"),
        "line_deviation_n":        _df.get("line_deviation_n"),
        # Shadow-only first-goal context from the production RP run.
        "game_state": {
            "status": (
                "available"
                if (diagnostic.get("first_goal_market") or {}).get("available")
                and (diagnostic.get("first_goal_regime_change") or {}).get("available")
                else "partial"
            ),
            "source": "first_goal_engine",
            "projection_influence": "shadow_only",
        },
        "first_goal_market":       diagnostic.get("first_goal_market") or {},
        "first_goal_regime_change": diagnostic.get("first_goal_regime_change") or {},
    }

    return JSONResponse(content={
        "source":       "jarvis/predict/soccer",
        "generated_at": int(time.time()),
        # Curated AI-ready summary — read this first
        "jarvis_brief": jarvis_brief,
        "diagnostic":   diagnostic,
    })


# ─────────────────────────────────────────────────────────────────────────────
# JARVIS SAVE PICK — predict + save in one call
# ─────────────────────────────────────────────────────────────────────────────

class JarvisSavePickBody(BaseModel):
    """Predict then save a soccer prop pick to the private owner's ledger."""
    fixture_id:        int
    player_id:         int
    prop_type:         str   = "pass_attempts"
    line:              float
    odds:              Optional[dict] = None
    position_override: str   = ""
    role_override:     str   = ""


@router.post("/api/jarvis/save-pick/soccer")
async def jarvis_save_pick_soccer(
    body: JarvisSavePickBody,
    authorization: Optional[str] = Header(default=None),
):
    """
    Run the full soccer prediction pipeline then immediately save the pick
    to the private owner's ledger. The owner account is resolved entirely
    server-side from the configured owner mapping and MongoDB session.

    Returns: pick_id, tracking_id, correlation_warnings, and the model
    summary (recommendation, p_over, p_under, prop_historical_rate, …).
    """
    _require_auth(authorization)

    ctx, result = await _run_soccer_prediction(body)

    model_metrics = _prediction_metrics(result)

    # ── 3. Build pick dict from prediction result ─────────────────────────────
    # Mirrors the fields the mobile app sends to /picks/save.
    pick_dict = {
        "sport":           "soccer",
        "playerId":        body.player_id,
        "playerName":      ctx["player_name"],
        "teamId":          ctx["team_id"],
        "teamName":        ctx["team_name"],
        "opponentId":      ctx["opponent_id"],
        "opponentName":    ctx["opponent_name"],
        "leagueId":        ctx["league_id"],
        "fixtureId":       body.fixture_id,
        "venue":           ctx["venue"],
        "playerIsHome":    ctx["venue"] == "home",
        "propType":        body.prop_type,
        "line":            body.line,
        "recommendation":  result.get("recommendation", "pass"),
        "projectedValue":  result.get("projectedValue"),
        "projection":      result.get("projection") or result.get("projectedValue"),
        "confidenceScore": result.get("confidenceScore", 50),
        "confidenceLevel": result.get("confidenceLevel", "Low"),
        "rawConfidence":   result.get("rawConfidence") or result.get("confidenceScore", 50),
        # Both numbers always together
        "pOver":           model_metrics["pOver"],
        "pUnder":          model_metrics["pUnder"],
        "propHistoricalRate": model_metrics["propHistoricalRate"],
        "propHistoricalN":   model_metrics["propHistoricalN"],
        "bayesianMetrics": result.get("bayesianMetrics") or {},
        "factorLedger":    result.get("factorLedger") or {},
        "factorLedgerVersion": result.get("factorLedgerVersion"),
        "factorLedgerFingerprint": result.get("factorLedgerFingerprint"),
        "edgeRating":      result.get("edgeRating"),
        "edgeRatingReason": result.get("edgeRatingReason"),
        "safetyRating":    result.get("safetyRating"),
        "lineDeviationBand": result.get("lineDeviationBand"),
        "lineDeviationHitRate": result.get("lineDeviationHitRate"),
        "coinFlip":        result.get("coinFlip", False),
        "lowConviction":   result.get("lowConviction", False),
        "moneyline":       result.get("moneyline"),
        "matchupOverview": result.get("matchupOverview"),
        "sharpSummary":    result.get("sharpSummary"),
        "reasoning":       result.get("reasoning"),
        "tacticalBreakdown": result.get("tacticalBreakdown"),
        "tacticalContext": result.get("tacticalContext"),
        "playerGameLogs":  result.get("playerGameLogs") or {},
        "analysisFactors": result.get("analysisFactors") or [],
        "modelInputSnapshot": result.get("modelInputSnapshot") or {},
        # _request block used by save_pick for venue / IDs fallback
        "_request": {
            "teamId":     ctx["team_id"],
            "opponentId": ctx["opponent_id"],
            "leagueId":   ctx["league_id"],
            "venue":      ctx["venue"],
        },
    }

    audit_request = {
        "fixture_id": body.fixture_id,
        "player_id": body.player_id,
        "prop_type": body.prop_type,
        "line": body.line,
        "odds": body.odds,
        "position_override": body.position_override,
        "role_override": body.role_override,
    }
    audit_snapshot = build_audit_snapshot(result, audit_request, context=ctx)
    pick_dict.update({
        # Immutable quantitative snapshot plus an observational audit packet.
        "modelVersion": audit_snapshot["rp_snapshot"].get("model_version"),
        "modelFingerprint": audit_snapshot["rp_snapshot"].get("fingerprint"),
        "jarvisAuditSchemaVersion": AUDIT_SCHEMA_VERSION,
        "jarvisAuditModelVersion": AUDIT_MODEL_VERSION,
        "jarvisAudit": audit_snapshot,
    })

    # ── 4. Save with the server-side owner session ────────────────────────────
    from models import SavePickRequest
    from routes.picks import save_pick as _rp_save_pick

    owner_email, owner_token = await _resolve_owner_session()
    save_req = SavePickRequest(
        email=owner_email,
        token=owner_token,
        pick=pick_dict,
    )

    try:
        save_result = await _rp_save_pick(save_req)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(502, detail={"error": f"Save failed: {exc}"})

    audit_persistence = {
        "status": "disabled" if not audit_enabled() else "not_written",
        "schema_version": AUDIT_SCHEMA_VERSION,
    }
    if audit_enabled():
        try:
            audit_persistence = await persist_prediction_audit(
                db,
                pick={
                    **pick_dict,
                    "pickId": save_result.get("pickId"),
                    "trackingId": save_result.get("trackingId"),
                },
                prediction=result,
                request=audit_request,
                context=ctx,
            )
            audit_persistence["status"] = "written"
        except Exception as exc:
            # The audit is auxiliary; an Atlas quota/transient failure must not
            # turn a successful owner save into a failed pick save.
            import logging
            logging.getLogger("jarvis").warning("prediction audit write skipped: %s", exc)
            audit_persistence = {
                "status": "write_skipped",
                "schema_version": AUDIT_SCHEMA_VERSION,
                "reason": "optional audit persistence failed",
            }

    # ── 5. Return compact summary ──────────────────────────────────────────────
    return JSONResponse(content={
        "source":       "jarvis/save-pick/soccer",
        "generated_at": int(time.time()),
        "saved": {
            "pick_id":     save_result.get("pickId"),
            "tracking_id": save_result.get("trackingId"),
        },
        "audit": {
            "status": audit_persistence.get("status"),
            "schema_version": AUDIT_SCHEMA_VERSION,
            "audit_model_version": AUDIT_MODEL_VERSION,
            "event_key": audit_persistence.get("event_key"),
        },
        # Correlation risk warnings (zero-sum pass props, all-under slip, etc.)
        "correlation_warnings": save_result.get("correlationWarnings", []),
        # Full model summary — both numbers always present
        "summary": {
            "player_name":          ctx["player_name"],
            "team":                 ctx["team_name"],
            "opponent":             ctx["opponent_name"],
            "venue":                ctx["venue"],
            "prop_type":            body.prop_type,
            "line":                 body.line,
            "recommendation":       result.get("recommendation"),
            "projected_value":      result.get("projectedValue"),
            "confidence_score":     result.get("confidenceScore"),
            "edge_rating":          result.get("edgeRating"),
            "safety_rating":        result.get("safetyRating"),
            "coin_flip":            result.get("coinFlip", False),
            "low_conviction":       result.get("lowConviction", False),
            # ── The two numbers that always travel together ───────────────────
            "p_over":               model_metrics["pOver"],
            "p_under":              model_metrics["pUnder"],
            "prop_historical_rate": model_metrics["propHistoricalRate"],
            "prop_historical_n":    model_metrics["propHistoricalN"],
            "line_deviation_hit_rate": result.get("lineDeviationHitRate"),
        },
    })


# ─────────────────────────────────────────────────────────────────────────────
# JARVIS AUDIT / CALIBRATION — observational layers around immutable RP output
# ─────────────────────────────────────────────────────────────────────────────

def _soccer_audit_request(body: JarvisSoccerPredictBody | JarvisSavePickBody) -> dict[str, Any]:
    return {
        "fixture_id": body.fixture_id,
        "player_id": body.player_id,
        "prop_type": body.prop_type,
        "line": body.line,
        "odds": body.odds,
        "position_override": body.position_override,
        "role_override": body.role_override,
    }


@router.post("/api/jarvis/full-audit/soccer")
async def jarvis_full_audit_soccer(
    body: JarvisSoccerPredictBody,
    authorization: Optional[str] = Header(default=None),
):
    """Run RP once, then return a provenance-labeled independent audit packet.

    Audit modules are observational and cannot alter the RP projection,
    probabilities, recommendation, or saved-pick behavior.
    """
    _require_auth(authorization)
    if not audit_enabled():
        raise HTTPException(
            status_code=404,
            detail={"error": "feature_disabled", "feature": "jarvis_full_audit"},
        )

    # Advisory only. Retrieve before the existing audit work, but never pass
    # this packet into RP prediction or provider refresh code.
    try:
        tactical_memory = await retrieve_tactical_memory(
            db,
            player_id=body.player_id,
            prop_type=body.prop_type,
            role=body.role_override,
            limit=20,
        )
        tactical_memory_status = "available"
        tactical_memory_reason = None
    except Exception as exc:
        # Tactical memory is auxiliary. Preserve the pre-existing full-audit
        # contract if its isolated store is unavailable.
        tactical_memory = []
        tactical_memory_status = "UNKNOWN"
        tactical_memory_reason = f"Advisory memory unavailable: {type(exc).__name__}."
    ctx, result = await _run_soccer_prediction(body)
    await asyncio.gather(
        _ensure_full_audit_first_goal_context(result, ctx, body.prop_type),
        _ensure_full_audit_news_context(result, ctx, body.fixture_id),
        return_exceptions=True,
    )
    audit = build_audit_snapshot(result, _soccer_audit_request(body), context=ctx)
    audit_contract = _audit_response_contract(audit, result)
    rp_diagnostic = _build_soccer_diagnostic(result)
    jarvis_brief = dict(rp_diagnostic.get("final") or {})
    jarvis_brief.update(_audit_first_goal_brief(audit))
    news_brief = _audit_news_brief(audit)
    jarvis_brief.update(news_brief)
    news_values = news_brief.get("news_intelligence") or {}
    # Keep the compact brief easy for callers that do not traverse the module
    # envelope. The canonical copy remains audit.modules.news_intelligence.
    for field in (
        "expected_lineup",
        "target_start_probability",
        "minutes_risk",
        "expected_role",
        "formation",
        "important_teammate_changes",
        "lineup_confidence",
        "regime_changes",
    ):
        jarvis_brief[field] = news_values.get(field)
    return {
        "source": "jarvis/full-audit/soccer",
        "generated_at": int(time.time()),
        "audit_mode": audit_mode(),
        "math_unchanged": True,
        "production_influence": False,
        "jarvis_brief": jarvis_brief,
        "news_intelligence": news_values,
        "news_brief": news_brief.get("news_brief"),
        "news_warnings": news_brief.get("news_warnings") or [],
        "rp_prediction": rp_diagnostic,
        "audit": audit,
        "jarvis_verdict": audit.get("jarvis_verdict") or audit.get("verdict"),
        "audit_contract": audit_contract,
        "tactical_memory": {
            "status": tactical_memory_status,
            "records": tactical_memory,
            "production_influence": False,
            "precedence": "current verified fixture, lineup, news, and provider evidence wins",
            "provenance": "owner-authenticated tactical memory; advisory historical context",
            **({"reason": tactical_memory_reason} if tactical_memory_reason else {}),
        },
    }


def _jarvis_core_shadow_enabled() -> bool:
    """Feature flag for the parallel migration surface; disabled by default."""
    return (os.environ.get("JARVIS_CORE_SHADOW_MODE") or "").strip().lower() in {
        "on", "enabled", "true", "1", "shadow",
    }


@router.post("/api/jarvis/shadow/soccer")
async def jarvis_core_shadow_soccer(
    body: JarvisSoccerPredictBody,
    authorization: Optional[str] = Header(default=None),
):
    """Compare the versioned JarvisCore contract with the RP control run."""
    _require_auth(authorization)
    if not _jarvis_core_shadow_enabled():
        raise HTTPException(
            status_code=404,
            detail={"error": "feature_disabled", "feature": "jarvis_core_shadow"},
        )

    from jarvis_core import (
        build_prediction_result,
        canonical_request,
        compare_control_to_core,
        persist_shadow_run,
    )
    ctx, control_result = await _run_soccer_prediction(body)
    shadow_error = None
    memory_records: list[dict[str, Any]] = []
    memory_status = "UNKNOWN"
    memory_reason = None
    try:
        memory_records = await retrieve_tactical_memory(
            db,
            player_id=body.player_id,
            prop_type=body.prop_type,
            role=body.role_override,
            limit=20,
        )
        memory_status = "available"
    except Exception as exc:
        memory_reason = f"Advisory memory unavailable: {type(exc).__name__}."

    request = canonical_request(body)
    try:
        core_result = build_prediction_result(
            request=request,
            control_result=control_result,
            context=ctx,
            tactical_memory=memory_records,
            tactical_memory_status=memory_status,
            tactical_memory_reason=memory_reason,
        )
        comparison = compare_control_to_core(control_result, core_result)
        persistence = await persist_shadow_run(
            db,
            request=request,
            control_result=control_result,
            core_result=core_result,
            comparison=comparison,
        )
    except Exception as exc:
        shadow_error = f"Shadow assembly unavailable: {type(exc).__name__}."
        core_result = {
            "schema_version": "jarvis-core.v1",
            "model_version": "jarvis-core-shadow.v1",
            "status": "UNKNOWN",
            "production_influence": False,
            "reason": shadow_error,
        }
        comparison = {
            "schema_version": "jarvis-core.v1",
            "math_unchanged": True,
            "production_influence": False,
            "status": "UNKNOWN",
            "reason": shadow_error,
        }
        persistence = {"persisted": False, "status": "UNKNOWN", "reason": shadow_error}

    return {
        "source": "jarvis/core-shadow/soccer",
        "schema_version": core_result["schema_version"],
        "feature": "jarvis_core_shadow",
        "math_unchanged": True,
        "production_influence": False,
        "control_prediction": control_result,
        "jarvis_core": core_result,
        "comparison": comparison,
        "persistence": persistence,
        **({"shadow_error": shadow_error} if shadow_error else {}),
    }


@router.get("/api/jarvis/tactical-memory")
async def get_tactical_memory(
    authorization: Optional[str] = Header(default=None),
    memory_type: Optional[str] = Query(None),
    team_id: Optional[int] = Query(None, ge=1),
    opponent_id: Optional[int] = Query(None, ge=1),
    player_id: Optional[int] = Query(None, ge=1),
    role: Optional[str] = Query(None, max_length=80),
    manager_regime: Optional[str] = Query(None, max_length=120),
    venue: Optional[str] = Query(None, max_length=20),
    prop_type: Optional[str] = Query(None, max_length=80),
    since: Optional[str] = Query(None, max_length=40),
    until: Optional[str] = Query(None, max_length=40),
    include_stale: bool = Query(False),
    limit: int = Query(20, ge=1, le=TACTICAL_MEMORY_MAX_RESULTS),
):
    """Bounded owner-only retrieval of advisory tactical memory."""
    _require_auth(authorization)
    if memory_type and memory_type not in {"team_fingerprint", "player_role", "matchup_interaction", "postmortem"}:
        raise HTTPException(422, detail={"error": "invalid_memory_type"})
    return {
        "source": "jarvis tactical memory",
        "read_only": True,
        "records": await retrieve_tactical_memory(
            db, memory_type=memory_type, team_id=team_id, opponent_id=opponent_id,
            player_id=player_id, role=role, manager_regime=manager_regime,
            venue=venue, prop_type=prop_type, since=since, until=until,
            include_stale=include_stale, limit=limit,
        ),
    }


@router.get("/api/jarvis/tactical-memory/team-fingerprint")
async def get_team_fingerprint(
    authorization: Optional[str] = Header(default=None),
    team_id: int = Query(..., ge=1),
    opponent_id: Optional[int] = Query(None, ge=1),
    venue: Optional[str] = Query(None, max_length=20),
    limit: int = Query(20, ge=1, le=TACTICAL_MEMORY_MAX_RESULTS),
):
    _require_auth(authorization)
    return {"records": await retrieve_tactical_memory(
        db, memory_type="team_fingerprint", team_id=team_id,
        opponent_id=opponent_id, venue=venue, limit=limit,
    )}


@router.get("/api/jarvis/tactical-memory/player-role")
async def get_player_role_memory(
    authorization: Optional[str] = Header(default=None),
    player_id: int = Query(..., ge=1),
    role: Optional[str] = Query(None, max_length=80),
    limit: int = Query(20, ge=1, le=TACTICAL_MEMORY_MAX_RESULTS),
):
    _require_auth(authorization)
    return {"records": await retrieve_tactical_memory(
        db, memory_type="player_role", player_id=player_id, role=role, limit=limit,
    )}


@router.get("/api/jarvis/tactical-memory/postmortem")
async def get_postmortem_memory(
    authorization: Optional[str] = Header(default=None),
    team_id: Optional[int] = Query(None, ge=1),
    player_id: Optional[int] = Query(None, ge=1),
    prop_type: Optional[str] = Query(None, max_length=80),
    limit: int = Query(20, ge=1, le=TACTICAL_MEMORY_MAX_RESULTS),
):
    _require_auth(authorization)
    return {"records": await retrieve_tactical_memory(
        db, memory_type="postmortem", team_id=team_id, player_id=player_id,
        prop_type=prop_type, limit=limit,
    )}


@router.post("/api/jarvis/tactical-memory")
async def upsert_tactical_memory_route(
    body: TacticalMemoryInput,
    authorization: Optional[str] = Header(default=None),
):
    _require_auth(authorization)
    try:
        record = await upsert_tactical_memory(db, body)
    except ValueError as exc:
        raise HTTPException(422, detail={"error": str(exc)})
    return {"record": record, "production_influence": False}


@router.post("/api/jarvis/tactical-memory/postmortem")
async def save_postmortem_memory(
    body: TacticalMemoryInput,
    authorization: Optional[str] = Header(default=None),
):
    _require_auth(authorization)
    if body.memory_type != "postmortem":
        raise HTTPException(422, detail={"error": "memory_type_must_be_postmortem"})
    try:
        record = await upsert_tactical_memory(db, body)
    except ValueError as exc:
        raise HTTPException(422, detail={"error": str(exc)})
    return {"record": record, "production_influence": False}


@router.post("/api/jarvis/tactical-memory/invalidate")
async def invalidate_tactical_memory(
    authorization: Optional[str] = Header(default=None),
    team_id: Optional[int] = Query(None, ge=1),
    player_id: Optional[int] = Query(None, ge=1),
    manager_regime: Optional[str] = Query(None, max_length=120),
    reason: str = Query("regime_changed", max_length=120),
):
    """Stale-mark related observations without deleting their audit history."""
    _require_auth(authorization)
    if team_id is None and player_id is None:
        raise HTTPException(422, detail={"error": "team_id_or_player_id_required"})
    return {
        "staled": await invalidate_regime(
            db, team_id=team_id, player_id=player_id,
            manager_regime=manager_regime, reason=reason,
        ),
        "deleted": 0,
        "production_influence": False,
    }


@router.get("/api/jarvis/calibration")
async def jarvis_calibration(
    authorization: Optional[str] = Header(default=None),
    prop_type: Optional[str] = Query(default=None),
    role: Optional[str] = Query(default=None),
    position: Optional[str] = Query(default=None),
    league_id: Optional[int] = Query(default=None),
    venue: Optional[str] = Query(default=None),
    side: Optional[str] = Query(default=None),
    model_version: Optional[str] = Query(default=None),
    limit: int = Query(default=1000, ge=1, le=20000),
):
    """Return leakage-conscious settled-pick calibration with sample warnings."""
    _require_auth(authorization)
    rows = await db.picks.find(
        {"status": "settled"},
        {
            "_id": 0,
            "playerName": 1,
            "playerId": 1,
            "teamId": 1,
            "opponentId": 1,
            "fixtureId": 1,
            "propType": 1,
            "sport": 1,
            "line": 1,
            "projectedValue": 1,
            "actualValue": 1,
            "recommendation": 1,
            "passLeaning": 1,
            "isCalibrationOnly": 1,
            "result": 1,
            "status": 1,
            "pOver": 1,
            "pUnder": 1,
            "bayesianMetrics": 1,
            "role": 1,
            "tacticalRole": 1,
            "playerRole": 1,
            "position": 1,
            "playerPosition": 1,
            "leagueId": 1,
            "venue": 1,
            "modelVersion": 1,
            "factorLedgerVersion": 1,
            "settlementSource": 1,
            "settledAt": 1,
            "timestamp": 1,
            "trackingId": 1,
            "pickId": 1,
        },
    ).sort([("settledAt", -1), ("timestamp", -1)]).limit(limit).to_list(length=limit)
    summary = calibration_summary(
        rows,
        prop_type=prop_type,
        role=role,
        position=position,
        league_id=league_id,
        venue=venue,
        side=side,
        model_version=model_version,
    )
    return {
        "source": "db.picks settled ledger",
        "generated_at": int(time.time()),
        "calibration": summary,
        "line_deviation_coverage": line_deviation_ledger_coverage(rows),
        "rows_returned": len(rows),
        "requested_limit": limit,
        "may_be_truncated": len(rows) >= limit,
        "note": "Lifetime means the deduplicated settled rows inspected under requested_limit; raise limit for a wider ledger window.",
    }


@router.get("/api/jarvis/stat-definitions")
async def jarvis_stat_definitions(
    authorization: Optional[str] = Header(default=None),
    prop_type: Optional[str] = Query(default=None),
):
    """Return the explicit market/provider definition registry."""
    _require_auth(authorization)
    if prop_type:
        key = prop_type.strip().lower()
        definition = STAT_DEFINITIONS.get(key)
        if not definition:
            return {
                "source": "stat_definition_registry",
                "status": "unknown",
                "prop_type": key,
                "reason": "No configured market/provider definition.",
            }
        return {"source": "stat_definition_registry", "status": "configured", "prop_type": key, "definition": definition}
    return {
        "source": "stat_definition_registry",
        "status": "configured",
        "definitions": STAT_DEFINITIONS,
    }


@router.get("/api/jarvis/audit-status")
async def jarvis_audit_status(authorization: Optional[str] = Header(default=None)):
    """Return feature flags and honest status for all 30 architecture phases."""
    _require_auth(authorization)
    return {
        "source": "jarvis/audit-status",
        "generated_at": int(time.time()),
        "audit_mode": audit_mode(),
        "audit_enabled": audit_enabled(),
        "schema_version": AUDIT_SCHEMA_VERSION,
        "audit_model_version": AUDIT_MODEL_VERSION,
        "math_unchanged": True,
        "phases": implementation_status(),
    }


# ─────────────────────────────────────────────────────────────────────────────
# JARVIS PREDICTION SCREENSHOTS — server-side report capture
# ─────────────────────────────────────────────────────────────────────────────

_SCREENSHOT_SECTIONS = ("read", "form", "matchup", "context", "picks")


class JarvisScreenshotBody(BaseModel):
    """Inputs for a server-side prediction report capture."""
    fixture_id: int
    player_id: int
    prop_type: str = "pass_attempts"
    line: float
    sections: list[str] = Field(
        default_factory=lambda: ["read", "form", "matchup", "context"]
    )
    pick_id: Optional[str] = None


def _report_value(value: Any, fallback: str = "—") -> str:
    if value is None or value == "":
        return fallback
    if isinstance(value, float):
        return f"{value:.1f}"
    return str(value)


def _report_html(prediction_payload: dict, saved_pick: Optional[dict]) -> str:
    """Build a self-contained, credential-free report for Chromium capture."""
    brief = prediction_payload.get("jarvis_brief") or {}
    diagnostic = prediction_payload.get("diagnostic") or {}
    final = diagnostic.get("final") or {}
    identity = diagnostic.get("resolved_identity") or {}
    evidence = diagnostic.get("evidence_quality") or {}
    venue = diagnostic.get("venue_history") or {}
    situation = diagnostic.get("game_situation") or {}
    warnings = diagnostic.get("warnings") or []

    def esc(value: Any, fallback: str = "—") -> str:
        return html.escape(_report_value(value, fallback))

    p_over = final.get("p_over")
    p_under = final.get("p_under")
    hist_rate = final.get("prop_historical_rate")
    hist_n = final.get("prop_historical_n")
    direction = str(final.get("recommendation") or brief.get("recommendation") or "PASS").upper()
    hist_label = (
        f"HIST {direction} {esc(hist_rate)}% · n={esc(hist_n)}"
        if hist_rate is not None
        else "HIST unavailable for this prop bucket"
    )
    player = identity.get("player_name") or "Player"
    matchup = (
        f"{identity.get('team') or 'Team'} vs {identity.get('opponent') or 'Opponent'}"
    )
    title = f"{player} · {esc(final.get('line'))} {esc(prediction_payload.get('prop_type'), 'prop')}"

    def metric(label: str, value: Any, sub: str = "") -> str:
        return (
            f'<div class="metric"><div class="metric-label">{html.escape(label)}</div>'
            f'<div class="metric-value">{esc(value)}</div>'
            f'<div class="metric-sub">{html.escape(sub)}</div></div>'
        )

    warning_text = "; ".join(
        _report_value(w.get("message") if isinstance(w, dict) else w)
        for w in warnings[:3]
    ) or "No active warnings."
    tactical = (
        brief.get("tactical_breakdown")
        or diagnostic.get("tactical_breakdown")
        or brief.get("reasoning")
        or "Verified model read is available from the production prediction."
    )
    saved = saved_pick or {}
    saved_card = (
        f'<div class="saved-card"><div class="eyebrow">MY PICKS · SAVED</div>'
        f'<h2>{esc(saved.get("playerName") or player)}</h2>'
        f'<div class="muted">{esc(saved.get("teamName") or identity.get("team"))} vs '
        f'{esc(saved.get("opponentName") or identity.get("opponent"))}</div>'
        f'<div class="metrics">'
        f'{metric("LINE", saved.get("line"))}'
        f'{metric("PROJECTION", saved.get("projection") or saved.get("projectedValue"))}'
        f'{metric("DIRECTION", str(saved.get("recommendation") or direction).upper())}'
        f'</div><div class="probability"><b>P(OVER) {esc(saved.get("pOver") if saved.get("pOver") is not None else p_over)}%</b>'
        f'<b>P(UNDER) {esc(saved.get("pUnder") if saved.get("pUnder") is not None else p_under)}%</b>'
        f'<span>{hist_label}</span></div></div>'
        if saved
        else '<div class="empty">No saved pick was supplied for this capture.</div>'
    )

    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Reverse Picks report</title>
<style>
*{{box-sizing:border-box}} body{{margin:0;background:#070909;color:#f4f7f2;font-family:Arial,Helvetica,sans-serif}}
.report{{width:980px;margin:0 auto;padding:30px 34px 42px;background:#0b1110}}
.header{{border-bottom:1px solid #25362d;padding-bottom:22px;margin-bottom:18px}}
.brand{{color:#39ff14;font-size:12px;font-weight:800;letter-spacing:3px}}
h1{{font-size:30px;margin:8px 0 5px}} h2{{font-size:21px;margin:7px 0}}
.muted,.detail,.metric-sub{{color:#9daaa1}} .muted{{font-size:14px}}
.eyebrow{{font-size:10px;letter-spacing:2px;color:#39ff14;font-weight:800;margin-bottom:7px}}
.section{{background:#111a17;border:1px solid #284034;border-radius:14px;padding:20px 22px;margin:16px 0}}
.section-title{{font-size:11px;color:#b9c4bb;letter-spacing:2px;font-weight:800;margin-bottom:14px}}
.metrics{{display:flex;gap:12px;margin:14px 0}} .metric{{flex:1;background:#0b1110;border:1px solid #22352a;border-radius:9px;padding:12px}}
.metric-label{{font-size:10px;color:#95a299;letter-spacing:1.4px}} .metric-value{{font-size:24px;font-weight:800;margin-top:6px}}
.metric-sub{{font-size:10px;margin-top:3px;letter-spacing:.7px}}
.probability{{display:flex;align-items:center;gap:18px;padding:12px 14px;background:#0a100d;border-left:3px solid #39ff14;border-radius:8px;font-size:14px}}
.probability b:first-child{{color:#39ff14}} .probability b:nth-child(2){{color:#60a5fa}} .probability span{{color:#ffc857;margin-left:auto;font-size:12px}}
.detail{{font-size:14px;line-height:1.55}} .grid{{display:grid;grid-template-columns:1fr 1fr;gap:10px}}
.kv{{background:#0b1110;border-radius:8px;padding:11px}} .kv-label{{font-size:10px;color:#819087;letter-spacing:1px}} .kv-value{{font-size:14px;margin-top:4px}}
.callout{{padding:13px 15px;background:#0d1712;border-left:3px solid #39ff14;border-radius:7px;line-height:1.55;font-size:14px}}
.warning{{padding:12px 14px;background:#1a1710;border-left:3px solid #ffc857;color:#f5d98d;border-radius:7px;font-size:13px;line-height:1.45}}
.saved-card{{border:1px solid #39ff14;border-radius:12px;padding:18px;background:#0d1710}} .empty{{color:#79867d;padding:18px 0}}
</style></head><body><main class="report">
<header class="header"><div class="brand">REVERSE PICKS · JARVIS REPORT</div>
<h1>{html.escape(title)}</h1><div class="muted">{html.escape(matchup)} · {esc(identity.get("venue"))}</div></header>

<section class="section" data-jarvis-section="read">
<div class="section-title">READ · FINAL MODEL VIEW</div>
<div class="metrics">
{metric("RECOMMENDATION", direction, esc(brief.get("edge_rating"), "NO EDGE"))}
{metric("PROJECTION", brief.get("projected_value"), "production model")}
{metric("CONFIDENCE", brief.get("confidence_score"), esc(brief.get("confidence_level"), "SCORE"))}
</div>
<div class="probability"><b>P(OVER) {esc(p_over)}%</b><b>P(UNDER) {esc(p_under)}%</b><span>{hist_label}</span></div>
<p class="detail">{html.escape(_report_value(tactical))}</p></section>

<section class="section" data-jarvis-section="form">
<div class="section-title">FORM · VERIFIED EVIDENCE</div>
<div class="grid">
<div class="kv"><div class="kv-label">EVIDENCE QUALITY</div><div class="kv-value">{esc(evidence.get("level") or evidence.get("status"))}</div></div>
<div class="kv"><div class="kv-label">PLAYER LOG SAMPLE</div><div class="kv-value">{esc(evidence.get("realPlayerLogCount") or evidence.get("sampleSize"))}</div></div>
<div class="kv"><div class="kv-label">VENUE HISTORY</div><div class="kv-value">{esc(venue.get("modelScope") or venue.get("scope"))}</div></div>
<div class="kv"><div class="kv-label">VENUE SAMPLE</div><div class="kv-value">{esc(venue.get("sampleSize") or venue.get("n"))}</div></div>
</div><p class="detail">Historical rate is system calibration evidence, not a claim that this individual player has the same hit rate.</p></section>

<section class="section" data-jarvis-section="matchup">
<div class="section-title">MATCHUP · MODEL CONTEXT</div>
<div class="grid">
<div class="kv"><div class="kv-label">VENUE</div><div class="kv-value">{esc(identity.get("venue"))}</div></div>
<div class="kv"><div class="kv-label">OPPONENT</div><div class="kv-value">{esc(identity.get("opponent"))}</div></div>
<div class="kv"><div class="kv-label">MATCH SCRIPT</div><div class="kv-value">{esc(situation.get("label") or situation.get("summary") or situation.get("status"))}</div></div>
<div class="kv"><div class="kv-label">LINE DEVIATION HIT RATE</div><div class="kv-value">{esc(final.get("line_deviation_hit_rate"))}%</div></div>
</div><div class="callout">The two directional probabilities and the system hit rate are displayed together so the math signal and calibration evidence cannot be confused.</div></section>

<section class="section" data-jarvis-section="context">
<div class="section-title">MATCH CONTEXT · IDENTITY & RISKS</div>
<div class="grid">
<div class="kv"><div class="kv-label">PLAYER</div><div class="kv-value">{esc(identity.get("player_name"))}</div></div>
<div class="kv"><div class="kv-label">TEAM</div><div class="kv-value">{esc(identity.get("team"))}</div></div>
<div class="kv"><div class="kv-label">FIXTURE</div><div class="kv-value">{esc(identity.get("fixture_id"))}</div></div>
<div class="kv"><div class="kv-label">POSITION</div><div class="kv-value">{esc(identity.get("player_position"))}</div></div>
</div><div class="warning">{html.escape(warning_text)}</div></section>

<section class="section" data-jarvis-section="picks">
<div class="section-title">MY PICKS · SAVED CARD</div>{saved_card}</section>
</main></body></html>"""


def _capture_report_sections(report_html: str, sections: list[str]) -> dict[str, Path]:
    """Render the report in headless Chromium and capture exact section nodes."""
    binary = shutil.which("chromium") or shutil.which("chromium-browser")
    if not binary:
        raise RuntimeError("Chromium runtime is unavailable")

    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait

    _SCREENSHOT_ROOT.mkdir(parents=True, exist_ok=True)
    source_path = _SCREENSHOT_ROOT / f"report-{secrets.token_hex(12)}.html"
    source_path.write_text(report_html, encoding="utf-8")
    output: dict[str, Path] = {}
    driver = None
    try:
        options = Options()
        options.binary_location = binary
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--hide-scrollbars")
        options.add_argument("--window-size=1100,1200")
        driver = webdriver.Chrome(options=options)
        driver.get(source_path.as_uri())
        wait = WebDriverWait(driver, 15)
        for section in sections:
            selector = f'[data-jarvis-section="{section}"]'
            element = wait.until(lambda current, s=selector: current.find_element(By.CSS_SELECTOR, s))
            path = _SCREENSHOT_ROOT / f"capture-{secrets.token_hex(12)}.png"
            element.screenshot(str(path))
            output[section] = path
        return output
    finally:
        if driver is not None:
            driver.quit()
        try:
            source_path.unlink(missing_ok=True)
        except OSError:
            pass


@router.post("/api/jarvis/prediction-screenshots")
async def jarvis_prediction_screenshots(
    body: JarvisScreenshotBody,
    authorization: Optional[str] = Header(default=None),
):
    """Run a prediction and return short-lived section screenshot URLs."""
    _require_auth(authorization)
    sections = list(dict.fromkeys(body.sections or ["read", "form", "matchup", "context"]))
    invalid = [section for section in sections if section not in _SCREENSHOT_SECTIONS]
    if invalid or len(sections) > len(_SCREENSHOT_SECTIONS):
        raise HTTPException(422, detail={"error": "Invalid screenshot section."})

    owner_email, _owner_token = await _resolve_owner_session()
    saved_pick = None
    if body.pick_id:
        saved_pick = await db.picks.find_one(
            {"email": owner_email, "pickId": body.pick_id},
            {
                "_id": 0,
                "playerName": 1,
                "teamName": 1,
                "opponentName": 1,
                "line": 1,
                "projection": 1,
                "projectedValue": 1,
                "recommendation": 1,
                "pOver": 1,
                "pUnder": 1,
                "propHistoricalRate": 1,
                "propHistoricalN": 1,
            },
        )
        if not saved_pick:
            raise HTTPException(404, detail={"error": "Saved pick not found."})
        if "picks" not in sections:
            sections.append("picks")

    try:
        prediction_response = await jarvis_predict_soccer(
            JarvisSoccerPredictBody(
                fixture_id=body.fixture_id,
                player_id=body.player_id,
                prop_type=body.prop_type,
                line=body.line,
            ),
            authorization=f"Bearer {_JARVIS_KEY}",
        )
        import json as _json
        prediction_payload = _json.loads(prediction_response.body)
        prediction_payload["prop_type"] = body.prop_type
        report_html = _report_html(prediction_payload, saved_pick)
        captures = await asyncio.to_thread(_capture_report_sections, report_html, sections)
    except HTTPException:
        raise
    except Exception as exc:
        # Keep browser/provider details out of both the response and logs.
        print(f"[JARVIS SCREENSHOT] capture failed: {type(exc).__name__}")
        raise HTTPException(502, detail={"error": "Prediction screenshot rendering failed."}) from exc

    _cleanup_screenshot_files()
    expires_at = time.time() + _SCREENSHOT_TTL_SECONDS
    urls: dict[str, str] = {}
    for section, path in captures.items():
        handle = secrets.token_urlsafe(24)
        _SCREENSHOTS[handle] = (path, expires_at, section)
        urls[section] = f"/api/jarvis/prediction-screenshots/{handle}/{section}"

    brief = prediction_payload.get("jarvis_brief") or {}
    return JSONResponse(content={
        "source": "jarvis/prediction-screenshots",
        "generated_at": int(time.time()),
        "expires_in_seconds": _SCREENSHOT_TTL_SECONDS,
        "sections": urls,
        "summary": {
            "recommendation": brief.get("recommendation"),
            "p_over": brief.get("p_over"),
            "p_under": brief.get("p_under"),
            "prop_historical_rate": brief.get("prop_historical_rate"),
            "prop_historical_n": brief.get("prop_historical_n"),
            "saved_pick_card_included": bool(saved_pick),
        },
    })


@router.get("/api/jarvis/prediction-screenshots/{handle}/{section}")
async def jarvis_prediction_screenshot_file(
    handle: str,
    section: str,
    authorization: Optional[str] = Header(default=None),
):
    """Serve one opaque, authenticated, short-lived screenshot file."""
    _require_auth(authorization)
    _cleanup_screenshot_files()
    entry = _SCREENSHOTS.get(handle)
    if not entry or entry[2] != section:
        raise HTTPException(404, detail={"error": "Screenshot expired or not found."})
    path, _expires_at, _stored_section = entry
    if not path.exists():
        _SCREENSHOTS.pop(handle, None)
        raise HTTPException(404, detail={"error": "Screenshot expired or not found."})
    return FileResponse(
        path,
        media_type="image/png",
        filename=f"reverse-picks-{section}.png",
        headers={"Cache-Control": "private, max-age=60"},
    )


# ─────────────────────────────────────────────────────────────────────────────
# JARVIS ROLE CLASSIFICATION — granular tactical-role layer (no AI calls)
# ─────────────────────────────────────────────────────────────────────────────

# Map existing ai_positions system roles → JARVIS granular role names
_EXISTING_TO_JARVIS: dict[str, str] = {
    "Shot-Stopper":        "Shot-Stopper",
    "Sweeper Keeper":      "Sweeper Keeper",
    "Ball-Playing CB":     "Ball-Playing CB",
    "Stopper":             "Stopper CB",
    "Fullback":            "Overlapping FB",
    "Wing-Back":           "Overlapping FB",
    "Inverted Fullback":   "Inverted FB",
    "Anchor":              "Single Pivot",
    "Ball Winner":         "Destroyer 6",
    "Deep-Lying Playmaker":"Deep-Lying Playmaker",
    "Box-to-Box":          "Box-to-Box 8",
    "Mezzala":             "Advanced 8",
    "Advanced Playmaker":  "Attacking Midfielder",
    "Wide Playmaker":      "Wide Playmaker",
    "Traditional Winger":  "Touchline Winger",
    "Inverted Winger":     "Inside Forward",
    "Progressive Carrier": "Inside Forward",
    "Inside Forward":      "Inside Forward",
    "Target Man":          "Target Striker",
    "Poacher":             "Pressing Striker",
    "Complete Forward":    "Target Striker",
    "Pressing Forward":    "Pressing Striker",
    "False 9":             "False 9",
    "Shadow Striker":      "Second Striker",
}

_JARVIS_ROLE_DESC: dict[str, str] = {
    "Ball-Playing CB":         "Central defender initiating buildup with high-volume passing; comfortable under pressure.",
    "Stopper CB":              "Dominant CB prioritising aerial duels, clearances, and blocking passing lanes.",
    "Wide CB":                 "Outer CB in a back-three/five who carries forward and provides width like a fullback.",
    "Overlapping FB":          "Fullback making attacking runs into the final third and delivering crosses from deep.",
    "Inverted FB":             "Fullback cutting inside into halfspace to create overloads and progress centrally.",
    "Defensive FB":            "Disciplined fullback whose primary duty is defensive cover with minimal attacking runs.",
    "Destroyer 6":             "Aggressive CDM winning duels and breaking up play; limited creative output.",
    "Deep-Lying Playmaker":    "Deepest midfielder dictating tempo through high-volume short-to-medium passing.",
    "Single Pivot":            "Lone CDM providing defensive screening as the sole shield in front of the backline.",
    "Double-Pivot Distributor":"One of two CDMs recycling possession and protecting width in a pair.",
    "Box-to-Box 8":            "Central midfielder contributing in both phases: pressing, carrying, and supporting attack.",
    "Advanced 8":              "Technically gifted CM driving into dangerous areas and creating from central positions.",
    "Attacking Midfielder":    "Central no.10 connecting midfield to attack through key passes and movement.",
    "Wide Playmaker":          "Wide or halfspace midfielder creating rather than crossing — inverted creative focus.",
    "Touchline Winger":        "Traditional wide forward hugging the touchline and delivering crosses into the box.",
    "Inside Forward":          "Winger cutting inside to shoot or create from central positions.",
    "Second Striker":          "Shadow striker pressing high and exploiting pockets behind the central striker.",
    "False 9":                 "Deep-dropping centre forward creating space and threading key passes through lines.",
    "Target Striker":          "Focal-point striker holding up play, winning aerials, and finishing from range.",
    "Pressing Striker":        "High-energy centre forward whose primary role is pressing triggers and winning the ball high.",
    "Shot-Stopper":            "Traditional goalkeeper prioritising shot-stopping and aerial command.",
    "Sweeper Keeper":          "Ball-playing goalkeeper comfortable distributing with feet and sweeping behind the line.",
}

_JARVIS_ROLE_POSITION_GROUP: dict[str, str] = {
    "Ball-Playing CB": "CB",  "Stopper CB": "CB",   "Wide CB": "CB",
    "Overlapping FB":  "FB",  "Inverted FB": "FB",  "Defensive FB": "FB",
    "Destroyer 6":     "CDM", "Deep-Lying Playmaker": "CDM",
    "Single Pivot":    "CDM", "Double-Pivot Distributor": "CDM",
    "Box-to-Box 8":    "CM",  "Advanced 8": "CM",
    "Attacking Midfielder": "CAM", "Wide Playmaker": "W",
    "Touchline Winger": "W", "Inside Forward": "W",
    "Second Striker": "SS",  "False 9": "CF",
    "Target Striker":  "ST", "Pressing Striker": "ST",
    "Shot-Stopper":    "GK", "Sweeper Keeper": "GK",
}

# Provider position code → position groups used for cohort filtering
_PROVIDER_POS_TO_GROUPS: dict[str, set[str]] = {
    "D": {"CB", "FB"},
    "M": {"CDM", "CM", "CAM", "W"},
    "F": {"W", "SS", "CF", "ST"},
    "G": {"GK"},
}

# Prop type → API-Football (category, sub-key) for fixtures/players stat extraction
_PROP_TO_API_PATH: dict[str, tuple[str, str]] = {
    "pass_attempts":   ("passes",   "total"),
    "passes":          ("passes",   "total"),
    "key_passes":      ("passes",   "key"),
    "crosses":         ("passes",   "cross"),
    "shots":           ("shots",    "total"),
    "shots_on_target": ("shots",    "on"),
    "goals":           ("goals",    "total"),
    "assists":         ("goals",    "assists"),
    "saves":           ("goals",    "saves"),
    "tackles":         ("tackles",  "total"),
    "clearances":      ("tackles",  "clearances"),
    "interceptions":   ("tackles",  "interceptions"),
    "blocks":          ("tackles",  "blocks"),
    "dribbles":        ("dribbles", "attempts"),
    "duels_won":       ("duels",    "won"),
    "fouls_drawn":     ("fouls",    "drawn"),
    "fouls_committed": ("fouls",    "committed"),
}

# Position group → provider position codes for cohort matching
_POS_GROUP_TO_PROVIDER: dict[str, set[str]] = {
    "CB":  {"D"}, "FB": {"D"},
    "CDM": {"M"}, "CM": {"M"}, "CAM": {"M"},
    "W":   {"M", "F"}, "SS": {"F"}, "CF": {"F"}, "ST": {"F"},
    "GK":  {"G"},
}


def _parse_formation_rows(formation: str | None) -> list[int]:
    """Parse '4-3-3' → [4, 3, 3].  Returns [] if unparseable."""
    if not formation:
        return []
    try:
        parts = [int(x) for x in formation.replace(" ", "").split("-") if x.isdigit()]
        return parts if 0 < sum(parts) <= 11 else []
    except Exception:
        return []


def _classify_grid_slot(
    grid: str | None,
    formation: str | None,
    start_xi_teammates: list[dict],
) -> dict:
    """
    Infer tactical zone from a player's API-Football grid string and formation.

    grid format: "row:col"  (row 1 = GK, higher rows = further forward)
    Returns: row, col, total_in_row, zone, is_wide, side, formation_rows.
    """
    base: dict[str, Any] = {
        "grid": grid, "formation": formation,
        "row": None, "col": None, "total_in_row": None,
        "zone": None, "is_wide": None, "side": None,
        "formation_rows": None,
    }
    if not grid:
        return base
    try:
        row, col = int(grid.split(":")[0]), int(grid.split(":")[1])
    except Exception:
        return base

    base["row"] = row
    base["col"] = col
    form_rows = _parse_formation_rows(formation)
    base["formation_rows"] = form_rows

    if row == 1:
        base["zone"] = "GK"
        return base

    form_idx = row - 2            # 0-based outfield row index (deepest first)
    total = form_rows[form_idx] if form_rows and 0 <= form_idx < len(form_rows) else None
    base["total_in_row"] = total

    if not total or total < 1:
        return base

    is_wide = total > 2 and col in (1, total)
    base["is_wide"] = is_wide
    base["side"]    = "left" if col <= total / 2 else "right"

    n_rows = len(form_rows)

    if form_idx == 0:
        # ── Defensive row ──────────────────────────────────────────────────
        if total == 3:
            base["zone"] = "Wide_CB" if is_wide else "Central_CB"
        elif total == 4:
            base["zone"] = "FB" if is_wide else "CB"
        elif total == 5:
            base["zone"] = "WB" if col in (1, 5) else "CB"
        else:
            base["zone"] = "Wide_CB" if is_wide else "CB"

    elif form_idx == n_rows - 1:
        # ── Forward row ────────────────────────────────────────────────────
        if total == 1:
            base["zone"] = "CF"
        elif total == 2:
            base["zone"] = "ST"
        elif total == 3:
            base["zone"] = "W" if is_wide else "CF"
        else:
            base["zone"] = "W" if is_wide else "SS"

    else:
        # ── Midfield row(s) ────────────────────────────────────────────────
        n_mid  = n_rows - 2        # total midfield rows
        mid_idx = form_idx - 1    # 0 = deepest mid row

        if n_mid == 1:
            if   total == 1: base["zone"] = "CDM_Pivot"
            elif total == 2: base["zone"] = "CDM_Pair"
            elif is_wide and total >= 4: base["zone"] = "WM"
            else: base["zone"] = "CM"
        else:
            if mid_idx == 0:
                if   total == 1: base["zone"] = "CDM_Pivot"
                elif total == 2: base["zone"] = "CDM_Pair"
                elif is_wide and total >= 4: base["zone"] = "WB_Mid"
                else: base["zone"] = "CDM"
            elif mid_idx == n_mid - 1:
                if   total == 1: base["zone"] = "CAM"
                elif is_wide:    base["zone"] = "W_AM"
                else:            base["zone"] = "CAM"
            else:
                base["zone"] = "WM" if is_wide else "CM"

    return base


def _stat_fingerprint_jarvis(specific_position: str, stats: dict) -> str | None:
    """
    Derive a JARVIS-taxonomy role from per-game stat ratios.
    Requires a specific position string (CB, CDM, CM, LB, LW, ST, …).
    Returns a JARVIS role string or None.
    """
    if not stats or not specific_position:
        return None
    apps          = max(1, (stats.get("appearances") or 0) or 1)
    passes_pg     = ((stats.get("passes")     or 0) / apps)
    key_passes_pg = ((stats.get("key_passes") or 0) / apps)
    tackles_pg    = ((stats.get("tackles")    or 0) / apps)
    clearances_pg = ((stats.get("clearances") or 0) / apps)
    dribbles_pg   = ((stats.get("dribbles")   or 0) / apps)
    shots_pg      = ((stats.get("shots")      or 0) / apps)
    crosses_pg    = ((stats.get("crosses")    or 0) / apps)
    pos = specific_position.upper()

    if pos == "CB":
        if clearances_pg >= 3.0 or passes_pg >= 55: return "Ball-Playing CB" if passes_pg >= 50 else "Stopper CB"
        if clearances_pg >= 1.5: return "Stopper CB"
        if dribbles_pg   >= 1.0: return "Ball-Playing CB"
        return "Stopper CB"
    if pos in ("LB", "RB"):
        if dribbles_pg >= 1.5 and shots_pg >= 0.4:  return "Inverted FB"
        if crosses_pg  < 0.5  and tackles_pg >= 2.5: return "Defensive FB"
        return "Overlapping FB"
    if pos in ("LWB", "RWB"): return "Overlapping FB"
    if pos == "CDM":
        if tackles_pg >= 5.5 and passes_pg < 40: return "Destroyer 6"
        if passes_pg  >= 55  and tackles_pg < 4: return "Deep-Lying Playmaker"
        if tackles_pg >= 4.0: return "Destroyer 6"
        return "Single Pivot"
    if pos == "CM":
        if passes_pg >= 65 and tackles_pg < 3: return "Deep-Lying Playmaker"
        if key_passes_pg >= 1.5 and (shots_pg >= 1.2 or dribbles_pg >= 1.2): return "Advanced 8"
        return "Box-to-Box 8"
    if pos == "CAM": return "Attacking Midfielder"
    if pos in ("LM", "RM"):
        return "Wide Playmaker" if key_passes_pg >= 1.0 else "Touchline Winger"
    if pos in ("LW", "RW"):
        if dribbles_pg >= 2.5 and shots_pg >= 1.5: return "Inside Forward"
        if key_passes_pg >= 1.5:                   return "Wide Playmaker"
        return "Touchline Winger"
    if pos in ("CF", "SS"):
        return "False 9" if (key_passes_pg >= 1.5 and dribbles_pg >= 1.5) else "Second Striker"
    if pos == "ST":
        if shots_pg < 1.5 and tackles_pg >= 1.5: return "Pressing Striker"
        if shots_pg >= 2.5 and dribbles_pg < 1.5: return "Target Striker"
        return "Pressing Striker"
    return None


def _classify_jarvis_role(
    base_position: str,
    base_role: str,
    role_source: str,
    grid_info: dict,
    season_stats: dict,
    provider_pos: str,
) -> dict:
    """
    Combine cached/grounded role + grid slot + stat fingerprint into a
    JARVIS granular role with confidence score and evidence chain.
    """
    evidence: list[str] = []
    score = 0
    jarvis_role: str | None = None

    # ── Step 1: map existing cached/grounded role ──────────────────────────
    if base_role and base_role in _EXISTING_TO_JARVIS:
        jarvis_role = _EXISTING_TO_JARVIS[base_role]
        if role_source == "gemini_web_grounded":
            score += 55
            evidence.append(f"Gemini web-grounded: '{base_role}' → {jarvis_role}")
        elif role_source in ("cache", "manual", "api_sports_lineup_history"):
            score += 40
            evidence.append(f"Cached role: '{base_role}' → {jarvis_role}")
        else:
            score += 20
            evidence.append(f"System role: '{base_role}' → {jarvis_role}")

    # ── Step 2: grid-slot refinement ──────────────────────────────────────
    zone = grid_info.get("zone")
    if zone:
        evidence.append(f"Grid {grid_info.get('grid')} in {grid_info.get('formation')} → zone={zone}")
        score += 12

        if zone == "Wide_CB":
            jarvis_role = "Wide CB"; evidence.append("Outer slot in 3-man backline → Wide CB")
        elif zone in ("WB", "WB_Mid"):
            jarvis_role = "Overlapping FB"; evidence.append(f"{zone} → Wingback (Overlapping FB)")
        elif zone == "CDM_Pivot":
            if jarvis_role not in ("Destroyer 6", "Deep-Lying Playmaker"):
                jarvis_role = "Single Pivot"; evidence.append("Solo CDM slot → Single Pivot")
            else:
                evidence.append("Solo CDM slot confirms CDM role")
        elif zone == "CDM_Pair":
            if jarvis_role == "Deep-Lying Playmaker":
                jarvis_role = "Double-Pivot Distributor"; evidence.append("CDM pair + DLP → Double-Pivot Distributor")
            elif jarvis_role == "Single Pivot":
                jarvis_role = "Double-Pivot Distributor"; evidence.append("CDM pair slot → Double-Pivot Distributor")
            elif jarvis_role not in ("Destroyer 6", "Double-Pivot Distributor"):
                jarvis_role = "Double-Pivot Distributor"; evidence.append("CDM pair slot → Double-Pivot Distributor by grid")
        elif zone == "CAM" and jarvis_role not in ("Attacking Midfielder", "False 9", "Second Striker"):
            jarvis_role = "Attacking Midfielder"; evidence.append("CAM slot → Attacking Midfielder by grid")
        elif zone in ("W", "W_AM", "WM") and jarvis_role not in ("Touchline Winger", "Inside Forward", "Wide Playmaker"):
            jarvis_role = "Touchline Winger"; evidence.append(f"{zone} slot → Touchline Winger by grid")

    # ── Step 3: stat-based sub-classification ─────────────────────────────
    if season_stats:
        apps = max(1, (season_stats.get("appearances") or 0) or 1)
        crosses_pg = ((season_stats.get("crosses") or 0) / apps)
        tackles_pg = ((season_stats.get("tackles") or 0) / apps)
        passes_pg  = ((season_stats.get("passes")  or 0) / apps)
        if jarvis_role == "Overlapping FB" and zone not in ("WB", "Wide_CB", "WB_Mid"):
            if crosses_pg < 0.5 and tackles_pg >= 2.5:
                jarvis_role = "Defensive FB"
                evidence.append(f"Low crosses ({crosses_pg:.1f}/g) + high tackles ({tackles_pg:.1f}/g) → Defensive FB")
                score = max(score, 30)
        if jarvis_role == "Deep-Lying Playmaker" and tackles_pg >= 5.5 and passes_pg < 38:
            jarvis_role = "Destroyer 6"
            evidence.append(f"Exceptional tackles ({tackles_pg:.1f}/g) + low passes → Destroyer 6 over DLP")

    # ── Step 4: stat fingerprint fallback ─────────────────────────────────
    if not jarvis_role:
        # Normalise long-form provider position ("Defender" → "D") or keep short code
        _prov_long = {
            "goalkeeper": "G", "defender": "D", "midfielder": "M",
            "attacker": "F", "forward": "F",
        }
        prov_short = _prov_long.get((provider_pos or "").lower(), (provider_pos or "")[:1].upper())

        # Map API-Football grid zone → specific position for fingerprint
        _zone_to_pos = {
            "GK": "GK", "CB": "CB", "Central_CB": "CB", "Wide_CB": "CB",
            "FB": "LB", "WB": "LWB", "WB_Mid": "LWB",
            "CDM_Pivot": "CDM", "CDM_Pair": "CDM", "CDM": "CDM",
            "CM": "CM", "WM": "LM", "CAM": "CAM",
            "W": "LW", "W_AM": "LW", "CF": "CF", "ST": "ST", "SS": "SS",
        }
        inferred_pos = _zone_to_pos.get(zone or "")

        if not inferred_pos:
            # No grid zone — infer from provider position + stats
            if prov_short == "D":
                # Distinguish CB from LB: CBs clear more and pass more
                _apps = max(1, (season_stats.get("appearances") or 0) or 1)
                _clr  = ((season_stats.get("clearances") or 0) / _apps)
                _pas  = ((season_stats.get("passes")     or 0) / _apps)
                inferred_pos = "CB" if (_clr >= 1.0 or _pas >= 40) else "LB"
                evidence.append(f"Generic 'Defender' → inferred {inferred_pos} (clr/g={_clr:.1f} pass/g={_pas:.1f})")
            elif prov_short == "M":
                _apps = max(1, (season_stats.get("appearances") or 0) or 1)
                _pas  = ((season_stats.get("passes") or 0) / _apps)
                _tk   = ((season_stats.get("tackles") or 0) / _apps)
                inferred_pos = "CDM" if _tk >= 3.5 else ("CAM" if _pas >= 55 else "CM")
                evidence.append(f"Generic 'Midfielder' → inferred {inferred_pos}")
            elif prov_short == "F":
                inferred_pos = "ST"
                evidence.append("Generic 'Forward/Attacker' → inferred ST")
            elif prov_short == "G":
                inferred_pos = "GK"

        fp = _stat_fingerprint_jarvis(inferred_pos or "", season_stats)
        if fp:
            jarvis_role = fp; score = max(score, 18)
            evidence.append(f"Stat fingerprint ({inferred_pos}) → {fp}")

    # ── Step 5: provider-position last resort ──────────────────────────────
    if not jarvis_role:
        _prov_long2 = {"goalkeeper": "G", "defender": "D", "midfielder": "M", "attacker": "F", "forward": "F"}
        prov_short2 = _prov_long2.get((provider_pos or "").lower(), (provider_pos or "")[:1].upper())
        jarvis_role = {"D": "Stopper CB", "M": "Box-to-Box 8", "F": "Pressing Striker", "G": "Shot-Stopper"}.get(
            prov_short2, "Role unavailable"
        )
        if jarvis_role != "Role unavailable":
            score = max(score, 8); evidence.append(f"Provider position fallback ({provider_pos}) → {jarvis_role}")
        else:
            evidence.append("Insufficient evidence for role classification.")

    conf = "high" if score >= 55 else "medium" if score >= 30 else "low" if score >= 12 else "speculative"
    return {
        "jarvis_role":           jarvis_role,
        "description":           _JARVIS_ROLE_DESC.get(jarvis_role or "", ""),
        "position_group":        _JARVIS_ROLE_POSITION_GROUP.get(jarvis_role or ""),
        "confidence_score":      min(100, score),
        "confidence_label":      conf,
        "evidence":              evidence,
        "base_role_from_system": base_role  or None,
        "base_role_source":      role_source or None,
        "base_position":         base_position or None,
    }


def _build_teammate_context(teammates: list[dict], formation: str | None) -> dict:
    """Group starting XI teammates by grid row for formation readability."""
    if not teammates:
        return {"_source": "unavailable", "n_teammates": 0}
    rows: dict[int, list] = {}
    no_grid: list = []
    for t in teammates:
        g = t.get("grid")
        if not g:
            no_grid.append({"name": t.get("name"), "pos": t.get("pos")}); continue
        try:
            r = int(g.split(":")[0])
        except Exception:
            no_grid.append({"name": t.get("name"), "pos": t.get("pos")}); continue
        rows.setdefault(r, []).append(t)

    all_row_nums = sorted(rows.keys())
    outfield_nums = [r for r in all_row_nums if r != 1]
    labels: dict[int, str] = {1: "GK"}
    for i, rn in enumerate(outfield_nums):
        if i == 0:               labels[rn] = "Defensive"
        elif i == len(outfield_nums) - 1: labels[rn] = "Forward"
        else:                    labels[rn] = f"Midfield_Row{i}"

    by_zone: dict[str, list] = {}
    for rn, pls in sorted(rows.items()):
        lbl = labels.get(rn, f"Row{rn}")
        by_zone[lbl] = sorted(
            [{"id": p.get("id"), "name": p.get("name"), "pos": p.get("pos"), "grid": p.get("grid")} for p in pls],
            key=lambda x: int(x.get("grid", "0:0").split(":")[1]) if ":" in (x.get("grid") or "") else 0,
        )
    return {"_source": "raw_api_data", "formation": formation, "n_teammates": len(teammates), "rows_by_zone": by_zone, "no_grid": no_grid}


# ─────────────────────────────────────────────────────────────────────────────
# ROLE PROFILE — granular tactical identity for one player in one fixture
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/api/jarvis/resolve-soccer-prop")
async def jarvis_resolve_soccer_prop(
    player_name: str = Query(..., min_length=2, description="Player name as entered by the user."),
    team: Optional[str] = Query(default=None, description="Player team name or API-Sports team ID."),
    opponent: Optional[str] = Query(default=None, description="Opponent name to disambiguate the fixture."),
    date: Optional[str] = Query(default=None, description="Fixture date in YYYY-MM-DD format."),
    season: Optional[int] = Query(default=None, description="Optional API-Sports season year."),
    authorization: Optional[str] = Header(default=None),
):
    """Resolve a player prop to one verified fixture and player identity."""
    _require_auth(authorization)
    return JSONResponse(content=await _resolve_soccer_prop_identity(
        player_name=player_name,
        team=team,
        opponent=opponent,
        requested_date=date,
        season=season,
    ))


@router.get("/api/jarvis/role-profile")
async def jarvis_role_profile(
    authorization: Optional[str] = Header(default=None),
    fixture_id: int = Query(..., description="API-Sports fixture ID."),
    player_id:  int = Query(..., description="API-Sports player ID."),
):
    """
    Granular tactical role for one player in one fixture.

    Returns observed JARVIS role (ball-playing CB, destroyer 6, single pivot,
    inside forward, etc.), confidence + evidence chain, formation context,
    grid slot classification, teammate layout by zone, buildup + defensive
    responsibility indicators, and recent role history from match logs.
    No AI calls — fully deterministic.
    """
    _require_auth(authorization)

    ctx = await _resolve_soccer_context(fixture_id, player_id)
    fix = await _resolve_fixture(fixture_id)

    team_id      = ctx["team_id"]
    league_id    = ctx["league_id"]
    season       = ctx["season"]
    player_name  = ctx["player_name"]
    team_name    = ctx["team_name"]

    status_short = fix["status_short"]
    finished = status_short in ("FT", "AET", "PEN")
    is_live  = status_short in ("1H", "HT", "2H", "ET", "BT", "P", "INT", "LIVE")
    ttl = _CACHE_TTL_LIVE if is_live else (_CACHE_TTL_FINISHED if finished else _CACHE_TTL_SCHEDULED)

    player_season_raw, lineups_raw, team_fix_raw = await asyncio.gather(
        _sports_get_safe("players",          {"id": player_id, "season": season}, cache_ttl=_CACHE_TTL_FINISHED),
        _sports_get_safe("fixtures/lineups", {"fixture": fixture_id},             cache_ttl=ttl),
        _sports_get_safe("fixtures",         {"team": team_id, "last": 10},       cache_ttl=_CACHE_TTL_SCHEDULED),
    )

    # ── Season stats ───────────────────────────────────────────────────────
    season_rows  = (player_season_raw or {}).get("response", [])
    provider_pos = ""
    sfp: dict    = {}   # stats for fingerprint
    all_entries: list = []

    if season_rows:
        sr    = season_rows[0]
        stats = sr.get("statistics") or []
        cs    = next((s for s in stats if (s.get("league") or {}).get("id") == league_id), stats[0] if stats else {})
        gs    = cs.get("games", {})
        provider_pos = gs.get("position", "")
        sfp = {
            "appearances": gs.get("appearences"),
            "passes":      (cs.get("passes")   or {}).get("total"),
            "key_passes":  (cs.get("passes")   or {}).get("key"),
            "crosses":     (cs.get("passes")   or {}).get("cross"),
            "tackles":     (cs.get("tackles")  or {}).get("total"),
            "clearances":  (cs.get("tackles")  or {}).get("clearances"),
            "shots":       (cs.get("shots")    or {}).get("total"),
            "dribbles":    (cs.get("dribbles") or {}).get("attempts"),
            "goals":       (cs.get("goals")    or {}).get("total"),
        }
        all_entries = [
            {
                "league": (s.get("league") or {}).get("name"),
                "league_id": (s.get("league") or {}).get("id"),
                "team": (s.get("team") or {}).get("name"),
                "position": (s.get("games") or {}).get("position"),
                "appearances": (s.get("games") or {}).get("appearences"),
                "minutes": (s.get("games") or {}).get("minutes"),
            }
            for s in stats
        ]

    # ── Cached role (DB only, no AI call) ─────────────────────────────────
    base_position = ""; base_role = ""; role_source = ""
    try:
        from backend.ai_positions import resolve_position_deterministic as _rpd
        cached = await _rpd(player_name) if player_name else {}
        base_position = cached.get("position", "")
        base_role     = cached.get("role", "")
        role_source   = "cache" if (base_position or base_role) else ""
    except Exception:
        pass

    # ── Lineup: grid + formation + teammates ──────────────────────────────
    lineup_rows       = (lineups_raw or {}).get("response", [])
    formation         = None
    player_grid       = None
    player_lineup_pos = None
    start_xi_teammates: list[dict] = []
    substitutes: list[dict] = []
    team_coach = None

    for t in lineup_rows:
        if (t.get("team") or {}).get("id") == team_id:
            formation  = t.get("formation")
            team_coach = (t.get("coach") or {}).get("name")
            for p in t.get("startXI", []):
                pl = p.get("player", {})
                if pl.get("id") == player_id:
                    player_grid = pl.get("grid"); player_lineup_pos = pl.get("pos")
                else:
                    start_xi_teammates.append({"name": pl.get("name"), "id": pl.get("id"), "pos": pl.get("pos"), "grid": pl.get("grid")})
            for p in t.get("substitutes", []):
                pl = p.get("player", {})
                substitutes.append({"name": pl.get("name"), "pos": pl.get("pos")})
            break

    grid_info   = _classify_grid_slot(player_grid, formation, start_xi_teammates)
    role_result = _classify_jarvis_role(
        base_position=base_position, base_role=base_role, role_source=role_source,
        grid_info=grid_info, season_stats=sfp,
        provider_pos=player_lineup_pos or provider_pos,
    )

    # ── Recent role history from match logs ────────────────────────────────
    _DONE_SET = {"FT", "AET", "PEN"}
    team_done = [
        f for f in (team_fix_raw or {}).get("response", [])
        if (f.get("fixture") or {}).get("status", {}).get("short") in _DONE_SET
    ][:8]
    team_fids = [(f["fixture"]["id"]) for f in team_done if (f.get("fixture") or {}).get("id")]

    log_raws: list = []
    if team_fids:
        log_raws = list(await asyncio.gather(*[
            _sports_get_safe("fixtures/players", {"fixture": fid}, cache_ttl=_CACHE_TTL_FINISHED)
            for fid in team_fids
        ]))

    role_history: list[dict] = []
    for i, raw in enumerate(log_raws):
        if not raw or i >= len(team_done):
            continue
        fix_row  = team_done[i]
        fdate    = ((fix_row.get("fixture") or {}).get("date") or "")[:10]
        ft       = fix_row.get("teams") or {}
        home_id  = (ft.get("home") or {}).get("id")
        opp_side = "away" if home_id == team_id else "home"
        opp_nm   = ((ft.get(opp_side)) or {}).get("name", "")
        lname    = ((fix_row.get("league") or {}).get("name") or "")
        for te in (raw or {}).get("response", []):
            if (te.get("team") or {}).get("id") != team_id:
                continue
            for pl_e in te.get("players", []):
                if (pl_e.get("player") or {}).get("id") != player_id:
                    continue
                s   = ((pl_e.get("statistics") or [{}])[0])
                gs_ = s.get("games") or {}
                mins = gs_.get("minutes")
                if not mins:
                    continue
                role_history.append({
                    "date": fdate, "opponent": opp_nm, "competition": lname,
                    "venue": "home" if home_id == team_id else "away",
                    "minutes": mins, "position_played": gs_.get("position"),
                    "rating": gs_.get("rating"),
                })

    # ── Buildup + defensive responsibility indicators ─────────────────────
    def _safe_pg(key):
        v = sfp.get(key)
        apps_ = max(1, (sfp.get("appearances") or 0) or 1)
        try:   return round((v or 0) / apps_, 2)
        except: return None

    passes_pg    = _safe_pg("passes");    kp_pg     = _safe_pg("key_passes")
    crosses_pg   = _safe_pg("crosses");   drib_pg   = _safe_pg("dribbles")
    tackles_pg   = _safe_pg("tackles");   clr_pg    = _safe_pg("clearances")

    buildup = {
        "_source":             "raw_api_data" if passes_pg is not None else "unavailable",
        "passes_per_game":     passes_pg, "key_passes_per_game": kp_pg,
        "crosses_per_game":    crosses_pg, "dribbles_per_game":  drib_pg,
        "high_pass_volume":    (passes_pg or 0) >= 50,
        "creative_output":     (kp_pg    or 0) >= 1.0,
        "crossing_threat":     (crosses_pg or 0) >= 1.0,
    }
    defensive = {
        "_source":              "raw_api_data" if tackles_pg is not None else "unavailable",
        "tackles_per_game":     tackles_pg, "clearances_per_game": clr_pg,
        "high_defensive_duty":  (tackles_pg or 0) >= 3.0 or (clr_pg or 0) >= 2.5,
    }

    return JSONResponse(content={
        "source": "jarvis_role_profile",
        "generated_at": int(time.time()),
        "player_identity": {
            "player_id": player_id, "player_name": player_name, "team": team_name,
            "match": f"{fix['home_team']} vs {fix['away_team']}",
            "date":  (fix.get("date") or "")[:10], "player_venue": ctx.get("player_venue"),
            "fixture_id": fixture_id,
        },
        "base_position":           base_position or provider_pos or None,
        "provider_position":        provider_pos or player_lineup_pos or None,
        "all_competition_entries":  all_entries,
        "observed_tactical_role":   role_result["jarvis_role"],
        "role_description":         role_result["description"],
        "role_confidence": {
            "label": role_result["confidence_label"],
            "score": role_result["confidence_score"],
            "note":  "≥55=high (grounded), ≥30=medium, ≥12=low, <12=speculative",
        },
        "evidence_used":            role_result["evidence"],
        "base_role_from_system":    role_result["base_role_from_system"],
        "base_role_source":         role_result["base_role_source"],
        "position_group_for_cohort":role_result["position_group"],
        "recent_role_history": {
            "_source": "raw_api_data" if role_history else "unavailable",
            "n": len(role_history),
            "note": "position_played is provider category (D/M/F/G); use observed_tactical_role for granular role",
            "matches": role_history,
        },
        "formation_context": {
            "_source":              "raw_api_data" if formation else "unavailable",
            "team_formation":       formation, "team_coach": team_coach,
            "player_grid":          player_grid, "lineup_position_tag": player_lineup_pos,
            **{k: v for k, v in grid_info.items() if k not in ("grid", "formation")},
        },
        "teammate_context":         _build_teammate_context(start_xi_teammates, formation),
        "substitutes_available":    substitutes,
        "buildup_responsibility":   buildup,
        "defensive_responsibility": defensive,
    })


# ─────────────────────────────────────────────────────────────────────────────
# ROLE OPPONENT COHORT — role-matched players who faced this opponent
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/api/jarvis/role-opponent-cohort")
async def jarvis_role_opponent_cohort(
    authorization: Optional[str] = Header(default=None),
    fixture_id: int = Query(..., description="API-Sports fixture ID."),
    player_id:  int = Query(..., description="API-Sports player ID."),
    prop_type:  Optional[str] = Query(None, description="Optional prop for stat aggregation: pass_attempts | shots | tackles | clearances | key_passes | dribbles | crosses | goals"),
):
    """
    Role-matched opponent cohort for one player in one fixture.

    Identifies the player's JARVIS role, then collects players in the same
    position group who played against the opponent in their last 6 completed
    fixtures. Returns per-player match stats and a prop aggregate (when
    prop_type is provided). Use to benchmark role-similar players against
    this opponent's current defensive structure.
    """
    _require_auth(authorization)

    ctx = await _resolve_soccer_context(fixture_id, player_id)
    fix = await _resolve_fixture(fixture_id)

    team_id       = ctx["team_id"]
    opponent_id   = ctx["opponent_id"]
    league_id     = ctx["league_id"]
    season        = ctx["season"]
    player_name   = ctx["player_name"]
    team_name     = ctx["team_name"]
    opponent_name = ctx.get("opponent_name", "")

    status_short = fix["status_short"]
    finished = status_short in ("FT", "AET", "PEN")
    is_live  = status_short in ("1H", "HT", "2H", "ET", "BT", "P", "INT", "LIVE")
    ttl = _CACHE_TTL_LIVE if is_live else (_CACHE_TTL_FINISHED if finished else _CACHE_TTL_SCHEDULED)

    player_season_raw, lineups_raw, opp_fix_raw = await asyncio.gather(
        _sports_get_safe("players",          {"id": player_id, "season": season}, cache_ttl=_CACHE_TTL_FINISHED),
        _sports_get_safe("fixtures/lineups", {"fixture": fixture_id},             cache_ttl=ttl),
        _sports_get_safe("fixtures",         {"team": opponent_id, "last": 8},    cache_ttl=_CACHE_TTL_SCHEDULED),
    )

    # ── Season stats ───────────────────────────────────────────────────────
    season_rows  = (player_season_raw or {}).get("response", [])
    provider_pos = ""
    sfp2: dict   = {}

    if season_rows:
        sr    = season_rows[0]
        stats = sr.get("statistics") or []
        cs    = next((s for s in stats if (s.get("league") or {}).get("id") == league_id), stats[0] if stats else {})
        gs    = cs.get("games", {})
        provider_pos = gs.get("position", "")
        sfp2 = {
            "appearances": gs.get("appearences"),
            "passes":      (cs.get("passes")   or {}).get("total"),
            "key_passes":  (cs.get("passes")   or {}).get("key"),
            "crosses":     (cs.get("passes")   or {}).get("cross"),
            "tackles":     (cs.get("tackles")  or {}).get("total"),
            "clearances":  (cs.get("tackles")  or {}).get("clearances"),
            "shots":       (cs.get("shots")    or {}).get("total"),
            "dribbles":    (cs.get("dribbles") or {}).get("attempts"),
        }

    # ── Cached role ────────────────────────────────────────────────────────
    base_position2 = ""; base_role2 = ""; role_source2 = ""
    try:
        from backend.ai_positions import resolve_position_deterministic as _rpd2
        c2 = await _rpd2(player_name) if player_name else {}
        base_position2 = c2.get("position", ""); base_role2 = c2.get("role", "")
        role_source2   = "cache" if (base_position2 or base_role2) else ""
    except Exception:
        pass

    # ── Grid + role ────────────────────────────────────────────────────────
    lineup_rows       = (lineups_raw or {}).get("response", [])
    formation2        = None; player_grid2 = None; player_lineup_pos2 = None
    teammates2: list[dict] = []

    for t in lineup_rows:
        if (t.get("team") or {}).get("id") == team_id:
            formation2 = t.get("formation")
            for p in t.get("startXI", []):
                pl = p.get("player", {})
                if pl.get("id") == player_id:
                    player_grid2 = pl.get("grid"); player_lineup_pos2 = pl.get("pos")
                else:
                    teammates2.append({"id": pl.get("id"), "pos": pl.get("pos"), "grid": pl.get("grid"), "name": pl.get("name")})
            break

    grid_info2   = _classify_grid_slot(player_grid2, formation2, teammates2)
    role_result2 = _classify_jarvis_role(
        base_position=base_position2, base_role=base_role2, role_source=role_source2,
        grid_info=grid_info2, season_stats=sfp2,
        provider_pos=player_lineup_pos2 or provider_pos,
    )
    jarvis_role2   = role_result2["jarvis_role"]
    position_group = role_result2["position_group"] or "M"

    # ── Opponent completed fixtures ────────────────────────────────────────
    _DONE2 = {"FT", "AET", "PEN"}
    opp_done = [
        f for f in (opp_fix_raw or {}).get("response", [])
        if (f.get("fixture") or {}).get("status", {}).get("short") in _DONE2
    ][:6]
    opp_fids = [f["fixture"]["id"] for f in opp_done if (f.get("fixture") or {}).get("id")]
    opp_meta = {f["fixture"]["id"]: f for f in opp_done if (f.get("fixture") or {}).get("id")}

    if not opp_fids:
        return JSONResponse(content={
            "source": "jarvis_role_opponent_cohort", "generated_at": int(time.time()),
            "player_identity": {"player_id": player_id, "player_name": player_name, "team": team_name,
                                 "jarvis_role": jarvis_role2, "position_group": position_group},
            "opponent": {"opponent_id": opponent_id, "opponent_name": opponent_name, "fixtures_analyzed": 0},
            "cohort_players": [], "n_cohort_players": 0,
            "cohort_aggregate": {"_source": "unavailable", "note": "No completed opponent fixtures found."},
        })

    cohort_raws = list(await asyncio.gather(*[
        _sports_get_safe("fixtures/players", {"fixture": fid}, cache_ttl=_CACHE_TTL_FINISHED)
        for fid in opp_fids
    ]))

    target_codes = _POS_GROUP_TO_PROVIDER.get(position_group, {"M", "D", "F"})
    prop_path    = _PROP_TO_API_PATH.get(prop_type or "") if prop_type else None

    # ── Build cohort ──────────────────────────────────────────────────────
    cohort_players: list[dict] = []

    for i, raw in enumerate(cohort_raws):
        if not raw or i >= len(opp_fids):
            continue
        fid         = opp_fids[i]
        fix_meta_r  = opp_meta.get(fid, {})
        fdate       = ((fix_meta_r.get("fixture") or {}).get("date") or "")[:10]
        ft2         = fix_meta_r.get("teams") or {}
        home_id_f   = (ft2.get("home") or {}).get("id")
        home_nm_f   = (ft2.get("home") or {}).get("name", "")
        away_nm_f   = (ft2.get("away") or {}).get("name", "")

        for team_entry in (raw or {}).get("response", []):
            tid_e  = (team_entry.get("team") or {}).get("id")
            tnm_e  = (team_entry.get("team") or {}).get("name", "")
            if tid_e == opponent_id:
                continue   # want players who FACED the opponent, not the opponent's own players

            for pl_e in team_entry.get("players", []):
                pl_info = pl_e.get("player") or {}
                s       = ((pl_e.get("statistics") or [{}])[0])
                gs_e    = s.get("games") or {}
                mins    = gs_e.get("minutes") or 0
                if mins < 45:
                    continue
                pl_pos  = gs_e.get("position", "")
                if pl_pos not in target_codes:
                    continue

                prop_val = None
                if prop_path:
                    cat, sub = prop_path
                    try:
                        prop_val = float((s.get(cat) or {}).get(sub)) if (s.get(cat) or {}).get(sub) is not None else None
                    except (TypeError, ValueError):
                        prop_val = None

                opp_they_faced = away_nm_f if tid_e == home_id_f else home_nm_f

                cohort_players.append({
                    "player_id": pl_info.get("id"), "player_name": pl_info.get("name"), "team": tnm_e,
                    "fixture_id": fid, "date": fdate, "opponent_faced": opp_they_faced,
                    "minutes": mins, "position_played": pl_pos, "rating": gs_e.get("rating"),
                    "passes_total":  (s.get("passes")   or {}).get("total"),
                    "key_passes":    (s.get("passes")   or {}).get("key"),
                    "crosses":       (s.get("passes")   or {}).get("cross"),
                    "shots_total":   (s.get("shots")    or {}).get("total"),
                    "shots_on":      (s.get("shots")    or {}).get("on"),
                    "tackles_total": (s.get("tackles")  or {}).get("total"),
                    "clearances":    (s.get("tackles")  or {}).get("clearances"),
                    "dribbles":      (s.get("dribbles") or {}).get("attempts"),
                    "duels_won":     (s.get("duels")    or {}).get("won"),
                    "goals":         (s.get("goals")    or {}).get("total"),
                    "prop_stat_value": prop_val,
                })

    # ── Aggregate ─────────────────────────────────────────────────────────
    stat_vals = [p["prop_stat_value"] for p in cohort_players if p.get("prop_stat_value") is not None]

    if stat_vals and prop_type:
        sv_sorted = sorted(stat_vals)
        cohort_agg = {
            "_source": "raw_api_data", "prop_type": prop_type,
            "n": len(stat_vals), "avg": round(sum(stat_vals) / len(stat_vals), 2),
            "max": max(stat_vals), "min": min(stat_vals),
            "median": sv_sorted[len(sv_sorted) // 2],
            "values": sorted(stat_vals, reverse=True),
        }
    elif prop_type:
        cohort_agg = {"_source": "unavailable", "note": f"No {prop_type} data in cohort for position group '{position_group}'."}
    else:
        cohort_agg = {"_source": "unavailable", "note": "Provide prop_type for aggregate stats."}

    return JSONResponse(content={
        "source": "jarvis_role_opponent_cohort",
        "generated_at": int(time.time()),
        "player_identity": {
            "player_id": player_id, "player_name": player_name, "team": team_name,
            "jarvis_role": jarvis_role2, "role_description": role_result2["description"],
            "role_confidence": role_result2["confidence_label"], "position_group": position_group,
        },
        "opponent": {
            "opponent_id": opponent_id, "opponent_name": opponent_name,
            "fixtures_analyzed": len(opp_fids), "fixture_ids": opp_fids,
            "fixture_dates": [(opp_meta.get(fid, {}).get("fixture") or {}).get("date", "")[:10] for fid in opp_fids],
        },
        "cohort_filter": {
            "position_group": position_group,
            "provider_positions_matched": sorted(target_codes),
            "min_minutes_threshold": 45,
            "note": f"Players in '{position_group}' group who played ≥45 min against {opponent_name} in last {len(opp_fids)} fixtures",
        },
        "cohort_aggregate": cohort_agg,
        "n_cohort_players": len(cohort_players),
        "cohort_players":   cohort_players,
    })


# ─────────────────────────────────────────────────────────────────────────────
# TACTICAL EVIDENCE — raw + minimally-derived evidence for JARVIS/ChatGPT
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/api/jarvis/tactical-evidence")
async def jarvis_tactical_evidence(
    authorization: Optional[str] = Header(default=None),
    fixture_id: int = Query(..., description="API-Sports fixture ID."),
    player_id:  int = Query(..., description="API-Sports player ID."),
    prop_type:  Optional[str] = Query(
        None,
        description=(
            "Optional prop context for opponent concession estimate: "
            "pass_attempts | shots | shots_on_target | tackles | clearances | "
            "saves | goals | key_passes | dribbles | interceptions | blocks | "
            "crosses | fouls_drawn | fouls_committed | duels_won"
        ),
    ),
):
    """
    Raw + minimally-derived tactical evidence for one player in one fixture.

    Returns: fixture/player identity, season profile, confirmed position +
    lineup grid, last 8 match logs (all raw API values), per-90 values,
    home/away splits, team/opponent season stats, recent form, possession
    history, press intensity index, opponent concession profile (prop_type
    required), buildup proxies, fatigue/rest days, injuries, H2H, odds.

    Each section carries _source: raw_api_data | reverse_picks_metric | unavailable.
    Does NOT run the prediction pipeline and cannot be used to infer model output.
    """
    _require_auth(authorization)

    # ── 1. resolve identity ───────────────────────────────────────────────────
    ctx = await _resolve_soccer_context(fixture_id, player_id)
    fix = await _resolve_fixture(fixture_id)

    team_id     = ctx["team_id"]
    opponent_id = ctx["opponent_id"]
    league_id   = ctx["league_id"]
    season      = ctx["season"]

    status_short = fix["status_short"]
    is_live  = status_short in ("1H", "HT", "2H", "ET", "BT", "P", "INT", "LIVE")
    finished = status_short in ("FT", "AET", "PEN")
    ttl      = _CACHE_TTL_LIVE if is_live else (_CACHE_TTL_FINISHED if finished else _CACHE_TTL_SCHEDULED)

    # ── 2. wave-1: static parallel fetches (12 calls) ────────────────────────
    (
        player_season_raw,
        lineups_raw,
        injuries_raw,
        odds_raw,
        team_szn_raw,
        opp_szn_raw,
        h2h_raw,
        standings_raw,
        team_fix_raw,
        opp_fix_raw,
        fix_stats_raw,
        fix_players_raw,
    ) = await asyncio.gather(
        _sports_get_safe("players",             {"id": player_id, "season": season},                                     cache_ttl=_CACHE_TTL_FINISHED),
        _sports_get_safe("fixtures/lineups",    {"fixture": fixture_id},                                                  cache_ttl=ttl),
        _sports_get_safe("injuries",            {"fixture": fixture_id},                                                  cache_ttl=_CACHE_TTL_SCHEDULED),
        _sports_get_safe("odds",                {"fixture": fixture_id},                                                  cache_ttl=_CACHE_TTL_SCHEDULED),
        _sports_get_safe("teams/statistics",    {"team": team_id,     "league": league_id, "season": season},            cache_ttl=_CACHE_TTL_FINISHED),
        _sports_get_safe("teams/statistics",    {"team": opponent_id, "league": league_id, "season": season},            cache_ttl=_CACHE_TTL_FINISHED),
        _sports_get_safe("fixtures/headtohead", {"h2h": f"{team_id}-{opponent_id}", "last": 10},                         cache_ttl=_CACHE_TTL_FINISHED),
        _sports_get_safe("standings",           {"league": league_id, "season": season},                                 cache_ttl=_CACHE_TTL_FINISHED),
        _sports_get_safe("fixtures",            {"team": team_id,     "last": 15}, cache_ttl=_CACHE_TTL_SCHEDULED),
        _sports_get_safe("fixtures",            {"team": opponent_id, "last": 10}, cache_ttl=_CACHE_TTL_SCHEDULED),
        _sports_get_safe("fixtures/statistics", {"fixture": fixture_id},                                                  cache_ttl=ttl),
        _sports_get_safe("fixtures/players",    {"fixture": fixture_id},                                                  cache_ttl=ttl),
    )

    # ── 3. pick completed fixtures for per-match fetches ──────────────────────
    _DONE = {"FT", "AET", "PEN"}

    def _completed(raw, limit):
        rows = (raw or {}).get("response", [])
        return [f for f in rows if f.get("fixture", {}).get("status", {}).get("short") in _DONE][:limit]

    team_done = _completed(team_fix_raw, 8)
    opp_done  = _completed(opp_fix_raw,  6)
    team_fids = [f["fixture"]["id"] for f in team_done if f.get("fixture", {}).get("id")]
    opp_fids  = [f["fixture"]["id"] for f in opp_done  if f.get("fixture", {}).get("id")]

    # ── 4. wave-2: per-fixture fetches ────────────────────────────────────────
    #   fixtures/players per team fixture → player match logs
    #   fixtures/statistics per opp fixture → press intensity + concession
    player_log_tasks = [
        _sports_get_safe("fixtures/players",    {"fixture": fid}, cache_ttl=_CACHE_TTL_FINISHED)
        for fid in team_fids
    ]
    opp_stat_tasks = [
        _sports_get_safe("fixtures/statistics", {"fixture": fid}, cache_ttl=_CACHE_TTL_FINISHED)
        for fid in opp_fids
    ]

    wave2 = await asyncio.gather(*player_log_tasks, *opp_stat_tasks)
    n_pl  = len(player_log_tasks)
    player_log_raws = wave2[:n_pl]
    opp_stat_raws   = wave2[n_pl:]

    # ── helpers ───────────────────────────────────────────────────────────────
    def _rnd(v):
        try:    return round(float(v), 2) if v is not None else None
        except: return None

    def _num(v):
        if v is None:
            return None
        try:    return float(str(v).replace("%", "").strip())
        except: return None

    def _avg(vals):
        c = [v for v in vals if v is not None]
        return _rnd(sum(c) / len(c)) if c else None

    # ── 5. player season profile ──────────────────────────────────────────────
    def _season_profile():
        rows = (player_season_raw or {}).get("response", [])
        if not rows:
            return {"_source": "unavailable"}
        pl    = rows[0].get("player", {})
        stats = rows[0].get("statistics", [])
        cs    = next((s for s in stats if (s.get("league") or {}).get("id") == league_id), stats[0] if stats else {})
        gs    = cs.get("games", {})
        p     = cs.get("passes", {})
        sh    = cs.get("shots", {})
        tk    = cs.get("tackles", {})
        dr    = cs.get("dribbles", {})
        du    = cs.get("duels", {})
        gl    = cs.get("goals", {})
        cr    = cs.get("cards", {})
        return {
            "_source": "raw_api_data",
            "name": pl.get("name"), "age": pl.get("age"),
            "height": pl.get("height"), "weight": pl.get("weight"),
            "nationality": pl.get("nationality"),
            "position": gs.get("position"),
            "appearances": gs.get("appearences"), "starts": gs.get("lineups"),
            "minutes": gs.get("minutes"), "rating": gs.get("rating"),
            "season_passes_total":    p.get("total"),
            "season_passes_key":      p.get("key"),
            "season_passes_accuracy": p.get("accuracy"),
            "season_shots_total":     sh.get("total"),
            "season_shots_on":        sh.get("on"),
            "season_tackles":         tk.get("total"),
            "season_interceptions":   tk.get("interceptions"),
            "season_blocks":          tk.get("blocks"),
            "season_clearances":      tk.get("clearances"),
            "season_dribbles":        dr.get("attempts"),
            "season_duels_total":     du.get("total"),
            "season_duels_won":       du.get("won"),
            "season_goals":           gl.get("total"),
            "season_assists":         gl.get("assists"),
            "season_saves":           gl.get("saves"),
            "season_crosses":         p.get("cross"),
            "season_yellow_cards":    cr.get("yellow"),
            "season_red_cards":       cr.get("red"),
            "all_competition_entries": [
                {
                    "league": (s.get("league") or {}).get("name"),
                    "league_id": (s.get("league") or {}).get("id"),
                    "team": (s.get("team") or {}).get("name"),
                    "team_id": (s.get("team") or {}).get("id"),
                    "apps": (s.get("games") or {}).get("appearences"),
                    "minutes": (s.get("games") or {}).get("minutes"),
                    "position": (s.get("games") or {}).get("position"),
                }
                for s in stats
            ],
        }

    # ── 6. this-fixture lineup ────────────────────────────────────────────────
    def _lineup():
        rows = (lineups_raw or {}).get("response", [])
        if not rows:
            return {"_source": "unavailable", "note": "Lineup not yet released."}
        out = {"_source": "raw_api_data", "teams": {}}
        target_found = None
        for t in rows:
            tname  = (t.get("team") or {}).get("name", "unknown")
            tid_lu = (t.get("team") or {}).get("id")
            xi = []
            for p in t.get("startXI", []):
                pl = p.get("player", {})
                row = {
                    "name":   pl.get("name"),
                    "id":     pl.get("id"),
                    "number": pl.get("number"),
                    "pos":    pl.get("pos"),
                    "grid":   pl.get("grid"),
                }
                if pl.get("id") == player_id:
                    row["_is_target_player"] = True
                    target_found = {"status": "starter", "pos": pl.get("pos"), "grid": pl.get("grid"), "team": tname}
                xi.append(row)
            subs = []
            for p in t.get("substitutes", []):
                pl = p.get("player", {})
                sr = {"name": pl.get("name"), "id": pl.get("id"), "number": pl.get("number"), "pos": pl.get("pos")}
                if pl.get("id") == player_id:
                    target_found = {"status": "substitute", "pos": pl.get("pos"), "grid": None, "team": tname}
                subs.append(sr)
            out["teams"][tname] = {
                "team_id": tid_lu, "formation": t.get("formation"),
                "coach": (t.get("coach") or {}).get("name"),
                "start_xi": xi, "substitutes": subs,
            }
        out["target_player"] = target_found or {"status": "not_in_confirmed_lineup"}
        return out

    # ── 7. player match logs ──────────────────────────────────────────────────
    _STAT_FIELDS = [
        "passes_total", "passes_key", "passes_accuracy", "passes_cross",
        "shots_total", "shots_on", "tackles_total", "tackles_interceptions",
        "tackles_blocks", "tackles_clearances", "dribbles_attempts",
        "duels_total", "duels_won", "fouls_drawn", "fouls_committed",
        "goals_total", "goals_assists", "goals_saves",
    ]

    # Also look for player in the current fixture's players response
    all_logs_raw = list(player_log_raws)
    all_done     = list(team_done)
    if (is_live or finished) and fix_players_raw:
        all_done.insert(0, {"fixture": {"id": fixture_id, "date": fix.get("date", ""), "status": {"short": status_short}},
                             "teams": {"home": {"id": fix["home_team_id"]}, "away": {"id": fix["away_team_id"]}},
                             "goals": {"home": None, "away": None},
                             "league": {"name": fix.get("league_name", "")}})
        all_logs_raw.insert(0, fix_players_raw)

    player_logs = []
    for i, raw in enumerate(all_logs_raw):
        if not raw or i >= len(all_done):
            continue
        fix_row  = all_done[i]
        fid      = (fix_row.get("fixture") or {}).get("id")
        fdate    = ((fix_row.get("fixture") or {}).get("date") or "")[:10]
        home_id  = (((fix_row.get("teams") or {}).get("home")) or {}).get("id")
        mv       = "home" if home_id == team_id else "away"
        opp_side = "away" if mv == "home" else "home"
        opp_name = (((fix_row.get("teams") or {}).get(opp_side)) or {}).get("name", "")
        gh       = (fix_row.get("goals") or {}).get("home")
        ga       = (fix_row.get("goals") or {}).get("away")
        lname    = ((fix_row.get("league") or {}).get("name") or "")

        for te in (raw or {}).get("response", []):
            for p in te.get("players", []):
                if (p.get("player") or {}).get("id") != player_id:
                    continue
                s    = ((p.get("statistics") or [{}])[0])
                mins = _num((s.get("games") or {}).get("minutes")) or 0
                log  = {
                    "_source":         "raw_api_data",
                    "fixture_id":      fid,
                    "date":            fdate,
                    "league":          lname,
                    "opponent":        opp_name,
                    "venue":           mv,
                    "score":           f"{gh}-{ga}",
                    "minutes":         _num((s.get("games") or {}).get("minutes")),
                    "position_played": (s.get("games") or {}).get("position"),
                    "rating":          _rnd((s.get("games") or {}).get("rating")),
                    "passes_total":    _num((s.get("passes") or {}).get("total")),
                    "passes_key":      _num((s.get("passes") or {}).get("key")),
                    "passes_accuracy": _num((s.get("passes") or {}).get("accuracy")),
                    "passes_cross":    _num((s.get("passes") or {}).get("cross")),
                    "shots_total":     _num((s.get("shots") or {}).get("total")),
                    "shots_on":        _num((s.get("shots") or {}).get("on")),
                    "tackles_total":   _num((s.get("tackles") or {}).get("total")),
                    "tackles_interceptions": _num((s.get("tackles") or {}).get("interceptions")),
                    "tackles_blocks":  _num((s.get("tackles") or {}).get("blocks")),
                    "tackles_clearances": _num((s.get("tackles") or {}).get("clearances")),
                    "dribbles_attempts": _num((s.get("dribbles") or {}).get("attempts")),
                    "duels_total":     _num((s.get("duels") or {}).get("total")),
                    "duels_won":       _num((s.get("duels") or {}).get("won")),
                    "fouls_drawn":     _num((s.get("fouls") or {}).get("drawn")),
                    "fouls_committed": _num((s.get("fouls") or {}).get("committed")),
                    "goals_total":     _num((s.get("goals") or {}).get("total")),
                    "goals_assists":   _num((s.get("goals") or {}).get("assists")),
                    "goals_saves":     _num((s.get("goals") or {}).get("saves")),
                    "offsides":        _num(s.get("offsides")),
                    "yellow_cards":    _num((s.get("cards") or {}).get("yellow")),
                    "red_cards":       _num((s.get("cards") or {}).get("red")),
                    "_dnp":            mins == 0,
                }
                player_logs.append(log)
                break

    # active logs (minutes > 0) for derived metrics
    active_logs = [l for l in player_logs if not l.get("_dnp")]

    def _per90(field):
        vals = []
        for l in active_logs:
            v = l.get(field); m = l.get("minutes") or 0
            if v is not None and m > 0:
                vals.append(v * 90 / m)
        return {"avg_per90": _avg(vals), "n": len(vals), "_source": "reverse_picks_metric" if vals else "unavailable"}

    def _split(field, sv):
        vals = [l[field] for l in active_logs if l.get("venue") == sv and l.get(field) is not None]
        return {"avg": _avg(vals), "n": len(vals), "_source": "reverse_picks_metric" if vals else "unavailable"}

    per90       = {f: _per90(f) for f in _STAT_FIELDS}
    home_splits = {f: _split(f, "home") for f in _STAT_FIELDS}
    away_splits = {f: _split(f, "away") for f in _STAT_FIELDS}

    # prop-specific convenience summary
    _FIELD_MAP = {
        "pass_attempts": "passes_total", "passes": "passes_total",
        "key_passes": "passes_key", "shots": "shots_total",
        "shots_on_target": "shots_on", "tackles": "tackles_total",
        "clearances": "tackles_clearances", "saves": "goals_saves",
        "goals": "goals_total", "assists": "goals_assists",
        "blocks": "tackles_blocks", "interceptions": "tackles_interceptions",
        "dribbles": "dribbles_attempts", "crosses": "passes_cross",
        "fouls_drawn": "fouls_drawn", "fouls_committed": "fouls_committed",
        "duels_won": "duels_won",
    }
    prop_field = _FIELD_MAP.get(prop_type or "") if prop_type else None
    if prop_field and active_logs:
        _pv  = [l[prop_field] for l in active_logs if l.get(prop_field) is not None]
        _phv = [l[prop_field] for l in active_logs if l.get("venue") == "home" and l.get(prop_field) is not None]
        _pav = [l[prop_field] for l in active_logs if l.get("venue") == "away" and l.get(prop_field) is not None]
        prop_summary = {
            "_source": "reverse_picks_metric",
            "prop_type": prop_type, "stat_field": prop_field,
            "avg": _avg(_pv), "n": len(_pv),
            "home_avg": _avg(_phv), "home_n": len(_phv),
            "away_avg": _avg(_pav), "away_n": len(_pav),
            "values": _pv,
            "min": _rnd(min(_pv)) if _pv else None,
            "max": _rnd(max(_pv)) if _pv else None,
        }
    else:
        prop_summary = {"_source": "unavailable", "note": "Provide prop_type param for prop-specific summary."}

    # ── 8. opponent fixture stats → press + concession ────────────────────────
    def _num_rs(v):
        try:    return float(str(v or "").replace("%", "").strip()) if v is not None else None
        except: return None

    opp_fixture_stats = []   # shape expected by bayesian_engine helpers
    opp_match_rows    = []   # compact display rows

    for i, raw in enumerate(opp_stat_raws):
        if not raw or i >= len(opp_done):
            continue
        fix_row  = opp_done[i]
        fdate    = ((fix_row.get("fixture") or {}).get("date") or "")[:10]
        home_id  = (((fix_row.get("teams") or {}).get("home")) or {}).get("id")
        ov       = "home" if home_id == opponent_id else "away"
        opp_opp_side = "away" if ov == "home" else "home"
        opp_opp_name = (((fix_row.get("teams") or {}).get(opp_opp_side)) or {}).get("name", "")
        gh = (fix_row.get("goals") or {}).get("home")
        ga = (fix_row.get("goals") or {}).get("away")

        by_tid = {}
        for tr in (raw or {}).get("response", []):
            tid = (tr.get("team") or {}).get("id")
            if tid:
                by_tid[tid] = {str(s.get("type") or ""): s.get("value") for s in tr.get("statistics", [])}

        opp_rs   = by_tid.get(opponent_id, {})
        other_rs = next((v for tid, v in by_tid.items() if tid != opponent_id), {})
        if not opp_rs:
            continue

        engine_row = {
            "date":                fdate,
            "venue":               ov,
            "possession":          opp_rs.get("Ball Possession"),
            "totalPasses":         _num_rs(opp_rs.get("Total passes")),
            "accuratePasses":      _num_rs(opp_rs.get("Passes accurate")),
            "shotsOnTarget":       _num_rs(opp_rs.get("Shots on Goal")),
            "totalShots":          _num_rs(opp_rs.get("Total Shots")),
            "fouls":               _num_rs(opp_rs.get("Fouls")),
            "fouls_committed_agg": _num_rs(opp_rs.get("Fouls")),
            "corners":             _num_rs(opp_rs.get("Corner Kicks")),
            "opponentTotalPasses": _num_rs(other_rs.get("Total passes")),
            "opponentTotalShots":  _num_rs(other_rs.get("Total Shots")),
        }
        opp_fixture_stats.append(engine_row)
        opp_match_rows.append({
            "_source": "raw_api_data",
            "date": fdate, "opponent": opp_opp_name, "venue": ov,
            "score": f"{gh}-{ga}",
            "possession":    opp_rs.get("Ball Possession"),
            "total_passes":  _num_rs(opp_rs.get("Total passes")),
            "pass_accuracy": opp_rs.get("Passes %"),
            "total_shots":   _num_rs(opp_rs.get("Total Shots")),
            "shots_on_target": _num_rs(opp_rs.get("Shots on Goal")),
            "xg":            _num_rs(opp_rs.get("expected_goals")),
            "fouls":         _num_rs(opp_rs.get("Fouls")),
            "corners":       _num_rs(opp_rs.get("Corner Kicks")),
            "opp_total_passes": _num_rs(other_rs.get("Total passes")),
        })

    # bayesian_engine press + concession (no prediction algo changes)
    press_packet = {"_source": "unavailable", "note": "Insufficient opponent fixture data (need ≥1 completed fixture)."}
    concession   = {"_source": "unavailable", "note": "Provide prop_type and sufficient opponent data."}

    if opp_fixture_stats:
        try:
            from bayesian_engine import compute_press_intensity_score as _cpi
            press_packet = _cpi(opp_fixture_stats)
            press_packet["_source"] = "reverse_picks_metric"
            press_packet["_note"] = (
                "Reverse Picks Pressure Index — synthetic PPDA proxy from API-Football team aggregates. "
                "Not a raw PPDA count. 0-100 where higher = stronger press."
            )
            press_packet["raw_opp_fixture_stats_n"] = len(opp_fixture_stats)
        except Exception as _e:
            press_packet = {"_source": "unavailable", "note": f"Press computation error: {_e}"}

        if prop_type:
            try:
                from bayesian_engine import _estimate_opponent_concession as _eoc
                est = _eoc(opp_fixture_stats, prop_type)
                if est is not None:
                    concession = {
                        "_source": "reverse_picks_metric",
                        "prop_type": prop_type,
                        "estimated_player_share_conceded": est,
                        "based_on_n_fixtures": len(opp_fixture_stats),
                        "_note": (
                            "Estimated prop units the opponent concedes to a player of this position per game, "
                            "derived from opponent team-level fixture aggregates using a position-specific share."
                        ),
                    }
                else:
                    concession = {
                        "_source": "unavailable",
                        "note": f"prop_type={prop_type!r} not supported in opponent concession model.",
                    }
            except Exception as _e2:
                concession = {"_source": "unavailable", "note": f"Concession computation error: {_e2}"}

    # ── 9. possession + buildup proxies ───────────────────────────────────────
    def _poss_avg(rows):
        vals = []
        for r in rows:
            p = r.get("possession")
            if p is None:
                continue
            try:
                vals.append(float(str(p).replace("%", "")))
            except (TypeError, ValueError):
                pass
        return {"avg_pct": _avg(vals), "n": len(vals), "_source": "reverse_picks_metric" if vals else "unavailable"}

    team_poss_avg = _poss_avg(opp_match_rows)   # team's possession = opponent's opponent context
    opp_poss_avg  = _poss_avg(opp_match_rows)   # raw opp possession

    # build possession history from opp fixture stats directly
    team_passes_vals = [_num_rs(r.get("opp_total_passes")) for r in opp_match_rows if _num_rs(r.get("opp_total_passes")) is not None]
    opp_passes_vals  = [_num_rs(r.get("total_passes"))     for r in opp_match_rows if _num_rs(r.get("total_passes"))     is not None]

    buildup_proxies = {
        "_source": "reverse_picks_metric" if (opp_passes_vals or team_passes_vals) else "unavailable",
        "opponent_avg_passes_per_game":   _avg(opp_passes_vals),
        "opponent_avg_passes_n":          len(opp_passes_vals),
        "conceding_team_avg_passes_per_game": _avg(team_passes_vals),  # what teams playing against opp average
        "conceding_team_avg_passes_n":    len(team_passes_vals),
        "opponent_avg_shots_per_game":    _avg([r.get("total_shots") for r in opp_match_rows if r.get("total_shots") is not None]),
        "opponent_avg_xg_per_game":       _avg([r.get("xg") for r in opp_match_rows if r.get("xg") is not None]),
        "_note": "Derived from opponent's recent completed fixtures via API-Football team statistics.",
    }

    # ── 10. season stats + standings ─────────────────────────────────────────
    def _team_season(raw):
        r = (raw or {}).get("response", {})
        if not r:
            return {"_source": "unavailable"}
        return {
            "_source": "raw_api_data",
            "team":   (r.get("team") or {}).get("name"),
            "form":    r.get("form"),
            "played":  (r.get("fixtures") or {}).get("played", {}),
            "wins":    (r.get("fixtures") or {}).get("wins", {}),
            "draws":   (r.get("fixtures") or {}).get("draws", {}),
            "losses":  (r.get("fixtures") or {}).get("loses", {}),
            "goals_for":     (r.get("goals") or {}).get("for", {}),
            "goals_against": (r.get("goals") or {}).get("against", {}),
            "clean_sheet":   r.get("clean_sheet", {}),
            "failed_to_score": r.get("failed_to_score", {}),
            "biggest":       r.get("biggest", {}),
            "penalty":       r.get("penalty", {}),
        }

    def _standings_row(tid):
        if not standings_raw:
            return {"_source": "unavailable"}
        all_rows = []
        for entry in (standings_raw or {}).get("response", []):
            for grp in (entry.get("league") or {}).get("standings", []):
                all_rows.extend(grp)
        row = next((r for r in all_rows if (r.get("team") or {}).get("id") == tid), None)
        if not row:
            return {"_source": "unavailable"}
        return {
            "_source": "raw_api_data",
            "rank": row.get("rank"), "points": row.get("points"), "form": row.get("form"),
            "played": (row.get("all") or {}).get("played"),
            "won":    (row.get("all") or {}).get("win"),
            "drawn":  (row.get("all") or {}).get("draw"),
            "lost":   (row.get("all") or {}).get("lose"),
            "goals_for":     (row.get("all") or {}).get("goals", {}).get("for"),
            "goals_against": (row.get("all") or {}).get("goals", {}).get("against"),
            "goal_diff": row.get("goalsDiff"),
        }

    # ── 11. fatigue / rest inputs ─────────────────────────────────────────────
    def _rest(done_list, fixture_date_str):
        if not done_list or not fixture_date_str:
            return {"_source": "unavailable"}
        try:
            from datetime import date as _dt
            md = _dt.fromisoformat(fixture_date_str[:10])
            latest = max(
                _dt.fromisoformat(((f.get("fixture") or {}).get("date") or "")[:10])
                for f in done_list
                if ((f.get("fixture") or {}).get("date") or "")[:10]
            )
            return {
                "_source": "reverse_picks_metric",
                "last_match_date": str(latest),
                "days_rest": (md - latest).days,
                "fixture_date": fixture_date_str[:10],
            }
        except Exception:
            return {"_source": "unavailable"}

    fix_date_str = (fix.get("date") or "")[:10]
    team_rest = _rest(team_done, fix_date_str)
    opp_rest  = _rest(opp_done,  fix_date_str)

    # ── 12. team recent form (raw fixture summary) ────────────────────────────
    def _form(raw):
        rows = (raw or {}).get("response", [])
        done = [f for f in rows if (f.get("fixture") or {}).get("status", {}).get("short") in _DONE][:8]
        if not done:
            return {"_source": "unavailable"}
        out = []
        for f in done:
            gh = (f.get("goals") or {}).get("home")
            ga = (f.get("goals") or {}).get("away")
            out.append({
                "date":   ((f.get("fixture") or {}).get("date") or "")[:10],
                "home":   ((f.get("teams") or {}).get("home") or {}).get("name"),
                "away":   ((f.get("teams") or {}).get("away") or {}).get("name"),
                "score":  f"{gh}-{ga}",
                "league": ((f.get("league") or {}).get("name") or ""),
                "round":  ((f.get("league") or {}).get("round") or ""),
            })
        return {"_source": "raw_api_data", "n": len(out), "matches": out}

    # ── 13. current fixture stats ─────────────────────────────────────────────
    def _fix_stats():
        rows = (fix_stats_raw or {}).get("response", [])
        if not rows:
            return {"_source": "unavailable"}
        out = {}
        for tb in rows:
            nm = (tb.get("team") or {}).get("name", "unknown")
            out[nm] = {s["type"]: s["value"] for s in tb.get("statistics", [])}
        return {"_source": "raw_api_data", "by_team": out} if out else {"_source": "unavailable"}

    # ── 14. injuries ──────────────────────────────────────────────────────────
    injuries_out = [
        {
            "player": (r.get("player") or {}).get("name"),
            "team":   (r.get("team") or {}).get("name"),
            "type":   (r.get("player") or {}).get("type"),
            "reason": (r.get("player") or {}).get("reason"),
        }
        for r in (injuries_raw or {}).get("response", [])
    ]

    # ── 15. H2H ───────────────────────────────────────────────────────────────
    def _h2h():
        rows = (h2h_raw or {}).get("response", [])
        out  = []
        for f in rows:
            fx = f.get("fixture", {}); ts = f.get("teams", {}); gl = f.get("goals", {})
            out.append({
                "date":    fx.get("date", "")[:10],
                "venue":   (fx.get("venue") or {}).get("name"),
                "home":    (ts.get("home") or {}).get("name"),
                "away":    (ts.get("away") or {}).get("name"),
                "score":   f"{gl.get('home')}-{gl.get('away')}",
                "winner":  (
                    (ts.get("home") or {}).get("name") if (ts.get("home") or {}).get("winner")
                    else (ts.get("away") or {}).get("name") if (ts.get("away") or {}).get("winner")
                    else "Draw"
                ),
                "league": (f.get("league") or {}).get("name"),
            })
        return out or None

    # ── 16. odds ─────────────────────────────────────────────────────────────
    def _odds():
        rows = (odds_raw or {}).get("response", [])
        if not rows:
            return {"_source": "unavailable"}
        mkts = []
        for bm in rows[0].get("bookmakers", [])[:3]:
            for m in bm.get("bets", []):
                if m["name"] in ("Match Winner", "Goals Over/Under", "Asian Handicap"):
                    mkts.append({"bookmaker": bm["name"], "market": m["name"], "values": m.get("values", [])})
        return {"_source": "raw_api_data", "markets": mkts} if mkts else {"_source": "unavailable"}

    # ── assemble ─────────────────────────────────────────────────────────────
    return JSONResponse(content={
        "source":        "jarvis/tactical-evidence",
        "generated_at":  int(time.time()),
        "prop_type":     prop_type,
        "_field_labels": {
            "raw_api_data":         "Direct observation from API-Sports provider. Not processed by Reverse Picks.",
            "reverse_picks_metric": "Derived by Reverse Picks from raw API data. Not a raw provider measurement.",
            "unavailable":          "Data not available for this player/fixture combination.",
        },

        # ── identity ──────────────────────────────────────────────────────────
        "fixture_identity": {
            "_source":    "raw_api_data",
            "fixture_id": fixture_id,
            "date":       fix_date_str or None,
            "status":     status_short,
            "home_team":  {"name": fix["home_team"],   "id": fix["home_team_id"]},
            "away_team":  {"name": fix["away_team"],   "id": fix["away_team_id"]},
            "league":     {"name": fix["league_name"], "id": fix["league_id"], "country": fix.get("country")},
            "venue_name": fix.get("venue"),
            "city":       fix.get("city"),
            "round":      fix.get("round"),
            "season":     fix.get("season"),
        },

        "player_identity": {
            "_source":            "raw_api_data",
            "player_id":          player_id,
            "player_name":        ctx["player_name"],
            "team":               ctx["team_name"],
            "team_id":            ctx["team_id"],
            "opponent":           ctx["opponent_name"],
            "opponent_id":        ctx["opponent_id"],
            "player_venue":       ctx["venue"],
            "league_id":          ctx["league_id"],
            "season":             ctx["season"],
            "_resolution_source": ctx["_resolution_source"],
        },

        # ── player profile ────────────────────────────────────────────────────
        "player_season_profile": _season_profile(),

        # ── lineup ───────────────────────────────────────────────────────────
        "this_fixture_lineup": _lineup(),

        # ── match logs ───────────────────────────────────────────────────────
        "player_recent_logs": {
            "_source":       "raw_api_data",
            "n_with_minutes": len(active_logs),
            "n_dnp":          len(player_logs) - len(active_logs),
            "fixtures_checked": len(team_fids),
            "matches":        player_logs,
        },

        # ── derived metrics ───────────────────────────────────────────────────
        "player_per_90": {
            "_source": "reverse_picks_metric",
            "_note":   "Computed from recent match logs where minutes > 0.",
            **per90,
        },
        "player_home_splits": {"_source": "reverse_picks_metric", **home_splits},
        "player_away_splits": {"_source": "reverse_picks_metric", **away_splits},

        # ── prop-specific ─────────────────────────────────────────────────────
        "prop_specific_evidence": prop_summary,

        # ── current fixture live stats ────────────────────────────────────────
        "this_fixture_stats": _fix_stats(),

        # ── team context ─────────────────────────────────────────────────────
        "team_season_stats":  _team_season(team_szn_raw),
        "team_standings":     _standings_row(team_id),
        "team_recent_form":   _form(team_fix_raw),

        # ── opponent context ──────────────────────────────────────────────────
        "opponent_season_stats": _team_season(opp_szn_raw),
        "opponent_standings":    _standings_row(opponent_id),
        "opponent_recent_form":  _form(opp_fix_raw),

        # ── opponent match stats (raw) ─────────────────────────────────────────
        "opponent_recent_match_stats": {
            "_source": "raw_api_data",
            "n":       len(opp_match_rows),
            "matches": opp_match_rows,
            "_note":   "Raw per-fixture team statistics for opponent's last N completed matches.",
        },

        # ── press intensity ───────────────────────────────────────────────────
        "opponent_press_intensity": press_packet,

        # ── opponent concession profile ───────────────────────────────────────
        "opponent_concession_profile": concession,

        # ── possession + buildup ──────────────────────────────────────────────
        "possession_context": {
            "_source":                    "reverse_picks_metric",
            "opponent_avg_possession":    _poss_avg(opp_match_rows),
            "opponent_match_stats_n":     len(opp_match_rows),
            "_note": "Derived from opponent's recent completed fixtures.",
        },
        "buildup_proxies": buildup_proxies,

        # ── team quality inputs ───────────────────────────────────────────────
        "team_quality_inputs": {
            "_source":            "raw_api_data",
            "team_standings":     _standings_row(team_id),
            "opponent_standings": _standings_row(opponent_id),
        },

        # ── fatigue / rest ────────────────────────────────────────────────────
        "fatigue_rest_inputs": {
            "_source":       "reverse_picks_metric",
            "team_rest":     team_rest,
            "opponent_rest": opp_rest,
        },

        # ── injuries ──────────────────────────────────────────────────────────
        "injuries": {
            "_source": "raw_api_data",
            "n":       len(injuries_out),
            "players": injuries_out,
        },

        # ── H2H + odds ────────────────────────────────────────────────────────
        "h2h_team_meetings": {
            "_source":  "raw_api_data",
            "meetings": _h2h(),
        },
        "odds_context": _odds(),
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
