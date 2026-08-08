"""Bounded customer-facing match explanations.

The projection ledger remains deterministic.  Gemini is used only to turn a
small, finalized evidence packet into one short paragraph.  This module has
no background entry point and never changes a prediction value.
"""

from __future__ import annotations

import asyncio as aio
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from typing import Any

from config import db


_MODEL = os.environ.get("GEMINI_EXPLANATION_MODEL", "gemini-2.5-flash")
_MAX_WORDS = 90
_DAILY_LIMIT = max(1, int(os.environ.get("GEMINI_EXPLANATION_DAILY_LIMIT", "200")))
_memory_cache: dict[str, str] = {}
_generation_locks: dict[str, aio.Lock] = {}
_usage_lock = aio.Lock()
_usage_date = ""
_usage_attempts = 0


def _number(value: Any) -> float | None:
    try:
        if value is None or str(value).strip() == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _fmt(value: Any) -> str:
    number = _number(value)
    if number is None:
        return "unavailable"
    return str(int(number)) if number.is_integer() else f"{number:.1f}"


def _clean_text(value: Any) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ")
    text = re.sub(r"\*\*|[`#]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    # Do not allow a provider, model, or error string into the customer card.
    if re.search(r"\b(api[- ]?football|gemini|provider|llm error|model ledger)\b", text, re.I):
        return ""
    words = text.split()
    if len(words) > _MAX_WORDS:
        text = " ".join(words[:_MAX_WORDS]).rstrip(" ,;:") + "."
    return text


def _h2h_packet(prediction: dict[str, Any]) -> dict[str, Any]:
    h2h = prediction.get("h2hPlayerStats") or {}
    meetings = h2h.get("teamMeetingsByVenue") or {}
    compact_meetings: dict[str, list[dict[str, Any]]] = {}
    for venue in ("home", "away"):
        rows = []
        for row in meetings.get(venue, [])[:5]:
            rows.append({
                "date": str(row.get("date") or "")[:10],
                "score": row.get("score"),
                "homeTeam": row.get("homeTeam"),
                "awayTeam": row.get("awayTeam"),
                "homePossession": row.get("homePossession"),
                "awayPossession": row.get("awayPossession"),
            })
        if rows:
            compact_meetings[venue] = rows
    return {
        "playerAppearances": h2h.get("sampleSize") or 0,
        "playerAverage": h2h.get("avgVsOpponent"),
        "playerHitRate": (h2h.get("opponentHitRate") or {}).get("overPct"),
        "teamMeetings": h2h.get("teamMeetings") or 0,
        "teamMeetingsByVenue": compact_meetings,
    }


def build_evidence_packet(prediction: dict[str, Any]) -> dict[str, Any]:
    """Keep the generation input small, final, and auditable."""
    bm = prediction.get("bayesianMetrics") or {}
    ti = prediction.get("tacticalIntelligence") or {}
    player = ti.get("player") or {}
    lineup = ti.get("lineup") or {}
    possession = ti.get("possessionGameScript") or {}
    matchup = prediction.get("matchupOverview") or {}
    expected = matchup.get("expectedPossession") or {}
    if not expected:
        expected = {
            "playerTeam": possession.get("expectedPlayerTeamPossession"),
            "opponent": possession.get("expectedOpponentPossession"),
        }
    limitations = []
    for item in (ti.get("limitations") or []):
        if item and item not in limitations:
            limitations.append(str(item))
    packet = {
        "match": {
            "player": prediction.get("playerName"),
            "team": prediction.get("teamName"),
            "opponent": prediction.get("opponentName"),
            "venue": prediction.get("venue"),
            "homeTeam": matchup.get("homeTeam") or prediction.get("homeTeam"),
            "awayTeam": matchup.get("awayTeam") or prediction.get("awayTeam"),
        },
        "pick": {
            "prop": prediction.get("propType"),
            "line": prediction.get("line"),
            "projection": prediction.get("projectedValue"),
            "recommendation": str(prediction.get("recommendation") or "").upper(),
            "pOver": bm.get("pOver"),
            "pUnder": bm.get("pUnder"),
            "confidence": prediction.get("confidenceScore"),
        },
        "context": {
            "position": player.get("position") or prediction.get("playerPosition"),
            "role": player.get("role") or (ti.get("tacticalContext") or {}).get("role"),
            "homeFormation": lineup.get("homeFormation") or matchup.get("homeFormation"),
            "awayFormation": lineup.get("awayFormation") or matchup.get("awayFormation"),
            "playerTeamPossession": expected.get("playerTeam") or expected.get("team"),
            "opponentPossession": expected.get("opponent"),
            "matchScript": (ti.get("matchScript") or {}).get("label")
                or (ti.get("matchScript") or {}).get("classification"),
        },
        "h2h": _h2h_packet(prediction),
        "limitations": limitations[:3],
    }
    return packet


def _fallback(packet: dict[str, Any]) -> str:
    match = packet["match"]
    pick = packet["pick"]
    context = packet["context"]
    h2h = packet["h2h"]
    player = match.get("player") or "The player"
    matchup = f"{match.get('team') or 'The team'} vs {match.get('opponent') or 'the opponent'}"
    rec = pick.get("recommendation") or "PASS"
    paragraph = (
        f"{player is not None and player or 'The player'} is projected for "
        f"{_fmt(pick.get('projection'))} {str(pick.get('prop') or 'prop').replace('_', ' ')} "
        f"against a {_fmt(pick.get('line'))} line in {matchup}; the model leans {rec}. "
    )
    poss = context.get("playerTeamPossession")
    opp_poss = context.get("opponentPossession")
    if poss is not None and opp_poss is not None:
        paragraph += f"The expected possession split is {_fmt(poss)}%–{_fmt(opp_poss)}%. "
    if context.get("role"):
        paragraph += f"The relevant role is {context['role']}. "
    if h2h.get("playerAppearances"):
        paragraph += (
            f"Player H2H: {h2h['playerAppearances']} appearances, "
            f"{_fmt(h2h.get('playerAverage'))} average. "
        )
    elif h2h.get("teamMeetings"):
        paragraph += f"The teams have met {h2h['teamMeetings']} times, but player H2H is unavailable. "
    else:
        paragraph += "No player H2H sample is available. "
    return _clean_text(paragraph)


async def _cached_text(cache_key: str) -> str:
    if cache_key in _memory_cache:
        return _memory_cache[cache_key]
    try:
        doc = await aio.wait_for(
            db.ai_explanation_cache.find_one({"_k": cache_key}, {"_id": 0, "text": 1}),
            timeout=1.2,
        )
        text = _clean_text((doc or {}).get("text"))
        if text:
            _memory_cache[cache_key] = text
            return text
    except Exception as exc:
        print(f"[COMPACT AI CACHE READ] skipped: {type(exc).__name__}: {exc}")
    return ""


async def _within_daily_limit() -> bool:
    global _usage_date, _usage_attempts
    today = datetime.now(timezone.utc).date().isoformat()
    async with _usage_lock:
        if _usage_date != today:
            _usage_date = today
            _usage_attempts = 0
        if _usage_attempts >= _DAILY_LIMIT:
            return False
        _usage_attempts += 1
        return True


async def _generate(prompt: str) -> str:
    if os.environ.get("GEMINI_COMPACT_EXPLANATIONS", "true").lower() not in {"1", "true", "yes", "on"}:
        return ""
    api_key = os.environ.get("AI_INTEGRATIONS_GEMINI_API_KEY") or os.environ.get("GEMINI_API_KEY")
    base_url = os.environ.get("AI_INTEGRATIONS_GEMINI_BASE_URL")
    if not api_key or not base_url:
        return ""
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(
            api_key=api_key,
            http_options={"api_version": "", "base_url": base_url},
        )
        response = await aio.wait_for(
            aio.to_thread(
                client.models.generate_content,
                model=_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.2,
                    max_output_tokens=140,
                ),
            ),
            timeout=12,
        )
        return _clean_text(getattr(response, "text", ""))
    except Exception as exc:
        print(f"[COMPACT AI] generation skipped: {type(exc).__name__}: {exc}")
        return ""


async def build_compact_explanation(
    prediction: dict[str, Any],
    ledger_payload: dict[str, Any],
    ledger_fingerprint: str,
) -> tuple[str, str, str]:
    """Return ``(text, source, cache_key)`` after the final ledger is locked."""
    packet = build_evidence_packet(prediction)
    raw_identity = {
        "playerId": prediction.get("playerId"),
        "fixtureId": prediction.get("fixtureId"),
        "propType": prediction.get("propType"),
        "line": prediction.get("line"),
        "ledger": ledger_fingerprint,
    }
    cache_key = "compact-v1-" + hashlib.sha256(
        json.dumps(raw_identity, sort_keys=True, default=str).encode()
    ).hexdigest()[:32]
    fallback = _fallback(packet)
    cached = await _cached_text(cache_key)
    if cached:
        return cached, "gemini_cached", cache_key
    if str(prediction.get("sport") or "soccer").lower() != "soccer":
        return fallback, "compact_deterministic", cache_key
    lock = _generation_locks.setdefault(cache_key, aio.Lock())
    async with lock:
        # Re-check after waiting: another concurrent request may have filled
        # the cache while this request was waiting for the lock.
        cached = await _cached_text(cache_key)
        if cached:
            return cached, "gemini_cached", cache_key
        if not await _within_daily_limit():
            return fallback, "compact_budget_fallback", cache_key

        prompt = (
            "Write one concise customer-facing paragraph (55-90 words) explaining this "
            "sports pick. Use only the JSON evidence below. Mention the matchup, line, "
            "projection/recommendation, and the most relevant role, possession, or H2H "
            "context. If H2H is unavailable, say so briefly. Do not invent facts or "
            "numbers. Do not use headings, bullets, markdown, provider names, model "
            "names, or betting guarantees. Do not change the recommendation.\n\n"
            f"EVIDENCE JSON:\n{json.dumps(packet, separators=(',', ':'), default=str)}"
        )
        text = await _generate(prompt)
        if not text:
            return fallback, "compact_deterministic", cache_key
        _memory_cache[cache_key] = text
        try:
            await db.ai_explanation_cache.update_one(
                {"_k": cache_key},
                {"$set": {
                    "_k": cache_key,
                    "text": text,
                    "model": _MODEL,
                    "ledgerFingerprint": ledger_fingerprint,
                    "createdAt": datetime.now(timezone.utc).isoformat(),
                }},
                upsert=True,
            )
        except Exception as exc:
            # A cache write must never make a valid prediction fail, but it must
            # be visible in logs so repeated calls are diagnosable.
            print(f"[COMPACT AI CACHE WRITE] skipped: {type(exc).__name__}: {exc}")
        return text, "gemini", cache_key