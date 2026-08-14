"""Owner-only Lissa assistant.

Lissa starts as a read-only, deterministic assistant around the existing
Reverse Picks ledger.  She can summarize owner data and inspect saved picks,
but she cannot create, edit, settle, or publish predictions.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from config import OWNER_EMAIL, db
from routes.admin import verify_owner


router = APIRouter(prefix="/api/lissa", tags=["lissa"])


class LissaMessageRequest(BaseModel):
    email: str
    token: str
    message: str = Field(min_length=1, max_length=1200)
    session_id: Optional[str] = None


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
        "message": _summary_text(summary),
        "sessionId": f"lissa-{uuid.uuid4().hex[:12]}",
    }


@router.post("/message")
async def lissa_message(req: LissaMessageRequest):
    await _authorize(req)
    picks = await _load_owner_picks()
    summary = _ledger_summary(picks)
    message = req.message.strip()
    lowered = message.lower()

    if any(word in lowered for word in ("hello", "hi ", "hey", "who are you", "start")):
        response = _summary_text(summary)
    elif any(word in lowered for word in ("what can you", "capabilities", "help", "do for me")):
        response = (
            "I’m Lissa. In this first owner-only release I can read your saved pick ledger, "
            "summarize settled performance, find a player’s saved picks, and highlight whether "
            "a pick is missing a projection or result.\n\n"
            "I am intentionally read-only. I cannot create, edit, settle, or publish a pick yet. "
            "Ask “show my recent performance,” “find my passing picks,” or name a player."
        )
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
        "response": response,
        "readOnly": True,
        "mode": "deterministic-ledger",
        "summary": summary,
    }