"""Owner-only Lissa assistant.

Lissa starts as a read-only, deterministic assistant around the existing
Reverse Picks ledger.  She can summarize owner data and inspect saved picks,
but she cannot create, edit, settle, or publish predictions.
"""

from __future__ import annotations

import re
import uuid
import os
import asyncio as aio
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from config import OWNER_EMAIL, db
from routes.admin import verify_owner
from compact_explanation import _generate as _generate_explanation
from compact_explanation import _within_daily_limit as _within_explanation_budget


router = APIRouter(prefix="/api/lissa", tags=["lissa"])
_LISSA_AI_TIMEOUT_SECONDS = 3.5


class LissaMessageRequest(BaseModel):
    email: str
    token: str
    message: str = Field(min_length=1, max_length=1200)
    session_id: Optional[str] = None
    context: Optional[dict[str, Any]] = None


class LissaOverviewRequest(BaseModel):
    email: str
    token: str


_PICK_PROJECTION = {
    "_id": 0,
    "pickId": 1,
    "playerName": 1,
    "sport": 1,
    "propType": 1,
    "line": 1,
    "recommendation": 1,
    "projectedValue": 1,
    "confidence": 1,
    "confidenceScore": 1,
    "confidenceLevel": 1,
    "result": 1,
    "status": 1,
    "actualValue": 1,
    "createdAt": 1,
    "savedAt": 1,
    "matchDate": 1,
    "teamName": 1,
    "opponentName": 1,
}


def _pick_status(pick: dict[str, Any]) -> str:
    raw = pick.get("result") or pick.get("status") or ""
    value = str(raw).strip().lower()
    if value in {"hit", "won", "win", "winner"}:
        return "HIT"
    if value in {"miss", "lost", "loss"}:
        return "MISS"
    if value in {"push", "void", "dnp", "no_action", "no action"}:
        return "VOID" if value == "void" else value.upper().replace("_", " ")
    if value in {"live", "in_progress", "in progress"}:
        return "LIVE"
    return "PENDING"


def _number(value: Any) -> float | None:
    try:
        if value is None or str(value).strip() == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _format_number(value: Any) -> str:
    number = _number(value)
    if number is None:
        return "unavailable"
    return str(int(number)) if number.is_integer() else f"{number:.1f}"


def _display_prop(value: Any) -> str:
    return str(value or "prop").replace("_", " ").strip()


def _pick_time(pick: dict[str, Any]) -> str:
    return str(pick.get("createdAt") or pick.get("savedAt") or pick.get("matchDate") or "")


async def _load_owner_picks(limit: int = 250) -> list[dict[str, Any]]:
    try:
        return await (
            db.picks.find({"email": OWNER_EMAIL}, _PICK_PROJECTION)
            .sort([("createdAt", -1), ("savedAt", -1)])
            .limit(limit)
            .to_list(limit)
        )
    except Exception as exc:
        print(f"[LISSA] owner ledger read skipped: {type(exc).__name__}: {exc}")
        raise HTTPException(status_code=503, detail="Lissa could not read the owner ledger right now.")


def _ledger_summary(picks: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {"HIT": 0, "MISS": 0, "PENDING": 0, "LIVE": 0, "PUSH": 0, "DNP": 0, "VOID": 0}
    sports: dict[str, int] = {}
    settled = 0

    for pick in picks:
        status = _pick_status(pick)
        counts[status] = counts.get(status, 0) + 1
        sport = str(pick.get("sport") or "soccer").lower()
        sports[sport] = sports.get(sport, 0) + 1
        if status in {"HIT", "MISS"}:
            settled += 1

    hit_rate = round(counts["HIT"] / settled * 100, 1) if settled else None
    return {
        "total": len(picks),
        "counts": counts,
        "settled": settled,
        "hitRate": hit_rate,
        "sports": sports,
    }


def _pick_snapshot(pick: dict[str, Any]) -> dict[str, Any]:
    return {
        "playerName": pick.get("playerName") or "Unknown player",
        "sport": str(pick.get("sport") or "soccer").lower(),
        "propType": _display_prop(pick.get("propType")),
        "line": _number(pick.get("line")),
        "recommendation": str(pick.get("recommendation") or "—").upper(),
        "projection": _number(pick.get("projectedValue")),
        "confidence": _number(pick.get("confidenceScore") or pick.get("confidence")),
        "status": _pick_status(pick),
        "actual": _number(pick.get("actualValue")),
        "teamName": pick.get("teamName"),
        "opponentName": pick.get("opponentName"),
        "date": _pick_time(pick)[:10] or None,
    }


def _summary_text(summary: dict[str, Any]) -> str:
    counts = summary["counts"]
    hit_rate = "not available yet" if summary["hitRate"] is None else f"{summary['hitRate']:.1f}%"
    sports = ", ".join(f"{name.upper()} {count}" for name, count in sorted(summary["sports"].items()))
    return (
        f"I’m Lissa, your owner-only Reverse Picks assistant. I can read the saved ledger, "
        f"but I’m read-only right now: I will not change projections or publish picks.\n\n"
        f"The ledger has {summary['total']} picks: {counts['HIT']} HIT, {counts['MISS']} MISS, "
        f"{counts['LIVE']} LIVE, and {counts['PENDING']} pending. "
        f"Settled HIT/MISS rate: {hit_rate}."
        + (f" Sports in the ledger: {sports}." if sports else "")
        + "\n\nAsk me about recent performance, a player, passing props, or what evidence is unavailable."
    )


def _empty_summary() -> dict[str, Any]:
    return {
        "total": 0,
        "counts": {"HIT": 0, "MISS": 0, "PENDING": 0, "LIVE": 0, "PUSH": 0, "DNP": 0, "VOID": 0},
        "settled": 0,
        "hitRate": None,
        "sports": {},
    }


def _address_owner(response: str) -> str:
    """Keep the assistant's owner-facing voice consistent across all paths."""
    text = str(response or "").strip()
    if not text:
        return "Reverse, I do not have a safe answer for that yet."
    return text if re.search(r"\breverse\b", text, re.IGNORECASE) else f"Reverse, {text}"


def _fast_response(message: str, context: dict[str, Any] | None) -> str | None:
    """Handle identity, presence, app, and screen questions without I/O."""
    lowered = message.lower().strip()
    if (
        re.search(r"\b(can you hear me|do you hear me|are you there|are you listening|can you listen)\b", lowered)
        or re.fullmatch(r"(hello|hi|hey)( lissa| lisa)?[.!? ]*", lowered)
    ):
        return "Yes, I can hear you. I am listening and ready to answer."
    if any(term in lowered for term in ("your name", "who are you", "are you lisa", "are you lissa")):
        return (
            "I am Lissa, Reverse Picks' owner-only intelligence assistant. "
            "You can call me Lissa or Lisa; my name in the app is Lissa."
        )
    if any(term in lowered for term in ("what can you do", "capabilities", "what do you know", "help me")):
        return (
            "I can explain the Reverse Picks screen you are viewing, walk through a prediction's line, "
            "projection, recommendation, confidence, evidence, tactical context, and limitations, "
            "and read your saved ledger for performance, players, results, and missing data. "
            "I am read-only: I do not change projections, settle picks, publish posts, or make decisions for you."
        )
    if any(term in lowered for term in ("what is reverse picks", "what is this app", "what does this app do")):
        return (
            "Reverse Picks is a structured player-props analysis app. "
            "It combines verified matchup context, recent player evidence, historical and tactical signals, "
            "calibration, and a final projection ledger so you can see why a direction was selected."
        )
    if any(term in lowered for term in ("what am i looking at", "what screen", "where am i", "what page", "what can you see")):
        screen = context.get("screen") if isinstance(context, dict) else {}
        if isinstance(screen, dict):
            name = str(screen.get("name") or "the current Reverse Picks screen")
            description = str(screen.get("description") or "").strip()
            return f"You are on the {name} screen. {description}".strip()
        return "You are inside Reverse Picks. I can describe the exact screen once its context is available."
    return None


def _match_player(picks: list[dict[str, Any]], message: str) -> list[dict[str, Any]]:
    normalized = re.sub(r"[^a-z0-9 ]+", " ", message.lower())
    tokens = [token for token in normalized.split() if len(token) >= 3]
    ignored = {
        "what", "were", "with", "about", "show", "tell", "recent", "picks", "pick",
        "player", "passing", "passes", "props", "performance", "history", "this",
        "that", "from", "your", "lissa", "does", "have", "look", "like",
    }
    tokens = [token for token in tokens if token not in ignored]
    if not tokens:
        return []
    matches = []
    for pick in picks:
        name = str(pick.get("playerName") or "").lower()
        if name and all(token in name for token in tokens):
            matches.append(pick)
    return matches


def _player_text(matches: list[dict[str, Any]]) -> str:
    first = matches[0].get("playerName") or "That player"
    lines = []
    for pick in matches[:8]:
        snapshot = _pick_snapshot(pick)
        matchup = ""
        if snapshot.get("teamName") or snapshot.get("opponentName"):
            matchup = f" ({snapshot.get('teamName') or '?'} vs {snapshot.get('opponentName') or '?'})"
        result = snapshot["status"]
        actual = f", actual {_format_number(snapshot['actual'])}" if snapshot["actual"] is not None else ""
        projection = (
            f", projection {_format_number(snapshot['projection'])}"
            if snapshot["projection"] is not None else ""
        )
        lines.append(
            f"• {snapshot['date'] or 'undated'} — {snapshot['recommendation']} "
            f"{snapshot['propType']} { _format_number(snapshot['line']) }"
            f"{projection}{actual} — {result}{matchup}"
        )
    return (
        f"I found {len(matches)} saved pick(s) for {first}. Here is the verified ledger view:\n\n"
        + "\n".join(lines)
        + "\n\nI can summarize these results, but I will not reinterpret missing provider data as a measured zero."
    )


def _analysis_packet(context: dict[str, Any] | None) -> dict[str, Any]:
    """Merge the current screen snapshot with the durable owner pick.

    The client supplies the visible analysis fields so Lissa can answer the
    question the owner is asking right now.  The saved pick is re-read from
    the owner ledger when a pickId is present, so the assistant has a durable
    identity anchor rather than trusting a display-only name.
    """
    if not isinstance(context, dict):
        return {}
    pick = context.get("pick")
    analysis = context.get("analysis")
    if not isinstance(pick, dict):
        pick = {}
    if not isinstance(analysis, dict):
        analysis = {}
    return {
        "pick": {**pick, **analysis},
        "analysis": analysis,
        "factors": context.get("factors") if isinstance(context.get("factors"), list) else [],
        "ledger": context.get("ledger") if isinstance(context.get("ledger"), dict) else {},
    }


def _analysis_fallback(message: str, context: dict[str, Any]) -> str:
    pick = context.get("pick") or {}
    factors = context.get("factors") or pick.get("analysisFactors") or []
    player = pick.get("playerName") or "This player"
    team = pick.get("teamName") or "the player's team"
    opponent = pick.get("opponentName") or "the opponent"
    prop = _display_prop(pick.get("propType"))
    line = _format_number(pick.get("line"))
    projection = _format_number(pick.get("projectedValue") or pick.get("projection"))
    recommendation = str(pick.get("recommendation") or "the current direction").upper()
    venue = str(pick.get("venue") or "the recorded venue").lower()
    confidence = _number(pick.get("confidence") or pick.get("confidenceScore"))

    gap = ""
    line_number = _number(pick.get("line"))
    projection_number = _number(pick.get("projectedValue") or pick.get("projection"))
    if line_number is not None and projection_number is not None:
        difference = projection_number - line_number
        gap = f" That is a {difference:+.1f} projection gap versus the line."

    evidence_lines = []
    for factor in factors[:6]:
        if not isinstance(factor, dict):
            continue
        title = str(factor.get("title") or factor.get("id") or "").strip()
        detail = str(factor.get("detail") or factor.get("summary") or "").strip()
        direction = str(factor.get("direction") or "").strip()
        if title and (detail or direction):
            evidence_lines.append(f"{title}: {detail or direction}")

    tactical = str(pick.get("tacticalBreakdown") or pick.get("reasoning") or "").strip()
    answer = (
        f"{player} is listed for {prop} at {line} against {opponent}. "
        f"The saved projection is {projection}, so the deterministic ledger landed on {recommendation} "
        f"from the {venue} matchup.{gap}"
    )
    if confidence is not None:
        answer += f" Confidence is approximately {_format_number(confidence)}."
    if evidence_lines:
        answer += "\n\nThe strongest captured evidence is:\n" + "\n".join(f"• {line}" for line in evidence_lines[:4])
    if tactical:
        answer += "\n\nThe saved tactical read is: " + tactical[:1400]
    answer += (
        "\n\nThe important limitation is that this explains the finalized snapshot; "
        "it does not invent missing provider data or change the displayed projection."
    )
    return answer


async def _smart_ledger_response(
    message: str,
    picks: list[dict[str, Any]],
    summary: dict[str, Any],
) -> str | None:
    """Answer a general ledger question with the same bounded AI gateway.

    The analysis-modal path already sends a full current-pick packet.  This
    companion path gives the standalone Lissa screen enough durable context
    to answer questions about a player, result, line, or pattern instead of
    falling back to a generic "inspect the ledger" sentence.
    """
    if os.environ.get("LISSA_AI_ENABLED", "true").lower() not in {"1", "true", "yes", "on"}:
        return None
    if not await _within_explanation_budget():
        return None
    import json

    packet = {
        "question": message,
        "summary": summary,
        "savedPicks": [_pick_snapshot(pick) for pick in picks[:160]],
        "rules": [
            "Answer the owner's exact ledger question using only the supplied saved picks.",
            "Your name is Lissa (pronounced Lisa), and address the owner as Reverse.",
            "Speak as a calm, highly intelligent, well-spoken woman inside the app.",
            "Do not claim a prediction exists when no matching saved pick is present.",
            "Distinguish exact line/fixture matches from nearby lines or different matchups.",
            "Name unavailable, pending, duplicate, or thin evidence explicitly.",
            "Do not invent provider statistics, injuries, roles, or future results.",
            "Lissa is read-only and must not suggest that she changed or settled anything.",
            "Speak naturally in 2 to 5 short paragraphs with no markdown headings.",
        ],
    }
    prompt = (
        "You are Lissa (pronounced Lisa), the owner-only intelligence assistant inside Reverse Picks.\n"
        "You are a calm, well-spoken woman. Address the owner as Reverse when speaking directly to him.\n"
        "The owner is asking about the saved prediction ledger, not asking for a new wager.\n"
        "Use the durable records below. Be precise about what is and is not recorded.\n"
        f"{json.dumps(packet, ensure_ascii=False, default=str)[:18000]}\n\n"
        f"Owner question: {message}"
    )
    try:
        text = await aio.wait_for(
            _generate_explanation(prompt),
            timeout=_LISSA_AI_TIMEOUT_SECONDS,
        )
        return text.strip() if text and len(text.strip()) >= 40 else None
    except Exception as exc:
        print(f"[LISSA AI] ledger generation skipped: {type(exc).__name__}: {exc}")
        return None


async def _smart_analysis_response(message: str, context: dict[str, Any]) -> str | None:
    """Use the existing bounded explanation gateway when it is available.

    Lissa must still work when generation is disabled or unavailable, so the
    deterministic explanation remains the fallback and the prediction ledger
    remains authoritative.
    """
    if os.environ.get("LISSA_AI_ENABLED", "true").lower() not in {"1", "true", "yes", "on"}:
        return None
    if not await _within_explanation_budget():
        return None
    packet = {
        "question": message,
        "currentAnalysis": context,
        "rules": [
            "Answer the exact question about the current player analysis.",
            "Your name is Lissa (pronounced Lisa), and address the owner as Reverse.",
            "Speak as a calm, highly intelligent, well-spoken woman; make the first sentence useful when heard aloud.",
            "Use only values present in the current analysis snapshot.",
            "Explain the deterministic projection; do not replace it.",
            "Name the player, matchup, prop, line, projection, and recommendation when available.",
            "Call out unavailable, thin, shadow-only, or fallback evidence explicitly.",
            "Do not invent injuries, line movement, player roles, or statistics.",
            "Do not promise a win and do not give financial advice.",
            "Speak naturally as Lissa: concise first, then the evidence and risk.",
        ],
    }
    import json
    prompt = (
        "You are Lissa (pronounced Lisa), a well-spoken woman and owner-only voice assistant inside Reverse Picks.\n"
        "Address the owner as Reverse. Never call him by another name.\n"
        "The user is looking at one exact analysis screen and asked a question about it.\n"
        "Return a natural spoken answer, 2 to 5 short paragraphs, with no markdown bullets or headings.\n"
        "Here is the structured analysis packet:\n"
        f"{json.dumps(packet, ensure_ascii=False, default=str)[:18000]}\n\n"
        f"User question: {message}"
    )
    try:
        text = await aio.wait_for(
            _generate_explanation(prompt),
            timeout=_LISSA_AI_TIMEOUT_SECONDS,
        )
        return text.strip() if text and len(text.strip()) >= 40 else None
    except Exception as exc:
        print(f"[LISSA AI] generation skipped: {type(exc).__name__}: {exc}")
        return None


async def _authorize(req: LissaMessageRequest | LissaOverviewRequest) -> None:
    await verify_owner(req.email, req.token)


@router.post("/overview")
async def lissa_overview(req: LissaOverviewRequest):
    await _authorize(req)
    picks = await _load_owner_picks()
    summary = _ledger_summary(picks)
    return {
        "assistant": "Lissa",
        "readOnly": True,
        "summary": summary,
        "message": _address_owner(_summary_text(summary)),
        "sessionId": f"lissa-{uuid.uuid4().hex[:12]}",
    }


@router.post("/message")
async def lissa_message(req: LissaMessageRequest):
    await _authorize(req)
    message = req.message.strip()
    fast = _fast_response(message, req.context)
    if fast:
        return {
            "assistant": "Lissa",
            "sessionId": req.session_id or f"lissa-{uuid.uuid4().hex[:12]}",
            "response": _address_owner(fast),
            "readOnly": True,
            "mode": "instant",
            "summary": _empty_summary(),
        }

    packet = _analysis_packet(req.context)
    summary = _empty_summary()

    if packet:
        response = await _smart_analysis_response(message, packet)
        if not response:
            response = _analysis_fallback(message, packet)
    else:
        picks = await _load_owner_picks()
        summary = _ledger_summary(picks)
        lowered = message.lower()
        if any(word in lowered for word in ("hello", "hi ", "hey", "start")):
            response = _summary_text(summary)
        elif any(word in lowered for word in ("recent", "performance", "record", "hit rate", "ledger", "picks")):
            counts = summary["counts"]
            rate = "not available" if summary["hitRate"] is None else f"{summary['hitRate']:.1f}%"
            passing = [
                pick for pick in picks
                if any(term in str(pick.get("propType") or "").lower() for term in ("pass", "key_pass", "cross"))
            ]
            response = (
                f"Your current owner ledger contains {summary['total']} picks. "
                f"Settled record: {counts['HIT']} HIT / {counts['MISS']} MISS "
                f"({rate} HIT rate). There are {counts['LIVE']} live and {counts['PENDING']} pending picks."
                f"\n\nI found {len(passing)} passing-related pick(s). "
                "The next useful step is to inspect them by player, venue, line timestamp, and evidence coverage—"
                "not just by raw average."
            )
        else:
            response = await _smart_ledger_response(message, picks, summary)
            if not response:
                matches = _match_player(picks, message)
                if matches:
                    response = _player_text(matches)
                elif any(term in lowered for term in ("passing", "passes", "pass prop")):
                    passing = [
                        pick for pick in picks
                        if any(term in str(pick.get("propType") or "").lower() for term in ("pass", "cross"))
                    ]
                    response = (
                        f"I found {len(passing)} passing-related saved pick(s). "
                        "I can inspect a specific player next. Include the player’s full name so I do not merge "
                        "same-name identities."
                    )
                else:
                    response = (
                        "I can read your owner ledger, but I do not have enough context to answer that safely yet. "
                        "Try asking for recent performance, passing picks, or a specific player. "
                        "I will say when the saved evidence is unavailable instead of guessing."
                    )

    return {
        "assistant": "Lissa",
        "sessionId": req.session_id or f"lissa-{uuid.uuid4().hex[:12]}",
        "response": _address_owner(response),
        "readOnly": True,
        "mode": "deterministic-ledger",
        "summary": summary,
    }