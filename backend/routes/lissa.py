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
from lissa_memory import load_recent_turns, remember_turn
from team_resolver import find_team
from utils import priority_api_football_request
from jarvis_orchestrator import execute_action


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


import time as _time
_picks_cache: dict[str, tuple[list, float]] = {}
_PICKS_CACHE_TTL = 30.0  # seconds


async def _load_owner_picks_cached() -> list[dict[str, Any]]:
    """Load picks with a 30-second in-memory cache so repeated Lissa questions don't hammer Atlas."""
    cached = _picks_cache.get(OWNER_EMAIL)
    if cached and (_time.monotonic() - cached[1]) < _PICKS_CACHE_TTL:
        return cached[0]
    picks = await _load_owner_picks()
    _picks_cache[OWNER_EMAIL] = (picks, _time.monotonic())
    return picks


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
        f"Jossel, I'm point two — your personal Reverse Picks analyst. I can read the full ledger, "
        f"but I'm read-only: I won't change projections or publish picks.\n\n"
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
        return "Jossel, I don't have a safe answer for that yet."
    return text if re.search(r"\bjossel\b", text, re.IGNORECASE) else f"Jossel, {text}"


_SCREEN_QUESTION_RE = re.compile(
    r"\b("
    r"what (page|screen|tab) (is this|am i on|are (we|you) on)"
    r"|what (is|'s|are) (this|here|open|on screen)"
    r"|what am i (looking at|seeing|on|viewing)"
    r"|what are (we|you) (looking at|seeing|on)"
    r"|where am i"
    r"|which (page|screen|tab)"
    r"|what can you see"
    r")\b",
    re.IGNORECASE,
)


def _fast_response(message: str, context: dict[str, Any] | None) -> str | None:
    """Handle identity, presence, app, and screen questions without I/O."""
    lowered = message.lower().strip()

    # Presence / greeting
    if (
        re.search(r"\b(can you hear me|do you hear me|are you there|are you listening|can you listen)\b", lowered)
        or re.fullmatch(r"(hello|hi|hey)( reverse| lissa| lisa)?[.!? ]*", lowered)
    ):
        return "Yeah, I'm here, Jossel."

    # Identity
    if any(term in lowered for term in ("your name", "who are you", "are you lisa", "are you lissa", "what are you", "point two")):
        return "I'm point two — Jossel's personal analyst inside Reverse Picks. Ask me anything."

    # Screen / page questions — broad pattern match
    screen_match = (
        _SCREEN_QUESTION_RE.search(lowered)
        or any(term in lowered for term in ("what page", "what screen", "where am i", "what tab",
                                            "what can you see", "what do you see", "can you see"))
    )
    if screen_match:
        ctx = context if isinstance(context, dict) else {}
        screen = ctx.get("screen") if isinstance(ctx, dict) else {}
        name = str(screen.get("name") or "Reverse Picks") if isinstance(screen, dict) else "Reverse Picks"

        # If a pick is open on any screen, lead with the player — don't just name the tab
        pick = ctx.get("pick") if isinstance(ctx, dict) else None
        if isinstance(pick, dict) and pick.get("playerName"):
            player = str(pick.get("playerName") or "a player")
            prop = _display_prop(pick.get("propType"))
            rec = str(pick.get("recommendation") or "").upper()
            line = _format_number(pick.get("line"))
            proj = _format_number(pick.get("projectedValue") or pick.get("projection"))
            line_str = f" {line}" if line else ""
            proj_str = f", projection {proj}" if proj else ""
            rec_str = f" — {rec}" if rec else ""
            return (
                f"Jossel, you're on {name}. I can see {player}'s {prop}{line_str} analysis{proj_str}{rec_str}. "
                f"Ask me anything about it."
            )

        return f"Jossel, you're on {name}. I can see whatever's on your screen — just ask."

    return None


def _match_search_parts(message: str) -> tuple[str, str | None] | None:
    """Extract a human team-search request without guessing from unrelated text."""
    lowered = re.sub(r"[?!,.:;]+", " ", message.lower())
    lowered = re.sub(r"\s+", " ", lowered).strip()
    has_schedule_intent = any(term in lowered for term in (
        "upcoming", "next match", "next game", "next fixture", "fixtures",
        "schedule", "playing next", "plays next",
    ))
    has_specific_match = bool(re.search(r"\s+(?:vs?\.?|against)\s+", lowered))
    if not has_schedule_intent and not has_specific_match:
        return None

    opponent = None
    versus = re.search(r"\s+(?:vs?\.?|against)\s+(.+?)\s*$", lowered)
    if versus:
        opponent = versus.group(1).strip()
        lowered = lowered[:versus.start()].strip()
        opponent = re.sub(r"\s+(?:upcoming|next|match|game|fixture)$", "", opponent).strip()

    direct = re.match(r"^what(?: is|'s) (.+?)'s next (?:match|game|fixture)$", lowered)
    if direct:
        return direct.group(1).strip(), opponent
    direct = re.match(r"^who is (.+?) playing next$", lowered)
    if direct:
        return direct.group(1).strip(), opponent

    prefixes = (
        r"^what upcoming matches are there for\s+",
        r"^what are the upcoming matches for\s+",
        r"^show me the upcoming matches for\s+",
        r"^show me the upcoming fixtures for\s+",
        r"^tell me about the upcoming matches for\s+",
        r"^tell me about upcoming matches for\s+",
        r"^what are the upcoming fixtures for\s+",
        r"^upcoming matches for\s+",
        r"^upcoming fixtures for\s+",
        r"^matches for\s+",
        r"^fixtures for\s+",
        r"^schedule for\s+",
        r"^what is the next match for\s+",
        r"^what.s the next match for\s+",
        r"^when does\s+",
        r"^who does\s+",
        r"^next match for\s+",
        r"^next game for\s+",
    )
    team_query = lowered
    for prefix in prefixes:
        stripped = re.sub(prefix, "", team_query).strip()
        if stripped != team_query:
            team_query = stripped
            break
    team_query = re.sub(r"^(?:show me|find|search for|look up)\s+", "", team_query).strip()
    team_query = re.sub(r"\s+(?:play|plays|playing|next|match|game|fixture|fixtures|schedule)\s*$", "", team_query).strip()
    team_query = re.sub(r"'s$", "", team_query).strip()
    if not team_query or team_query in {"there", "they", "the team", "a team"}:
        return None
    return team_query, opponent


def _fixture_kickoff_text(fixture: dict[str, Any]) -> str:
    raw = str((fixture.get("fixture") or {}).get("date") or "")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        # Keep the user's familiar Central time while labeling it clearly.
        from zoneinfo import ZoneInfo
        local = parsed.astimezone(ZoneInfo("America/Chicago"))
        return local.strftime("%a, %b %-d at %-I:%M %p CT")
    except (TypeError, ValueError):
        return raw or "time unavailable"


async def _upcoming_match_response(message: str) -> str | None:
    parts = _match_search_parts(message)
    if not parts:
        return None
    team_query, opponent_query = parts
    try:
        team = await find_team(team_query)
    except Exception as exc:
        print(f"[LISSA MATCH] team resolution skipped: {type(exc).__name__}: {exc}")
        return f"I couldn't match “{team_query}” to a team yet."
    if not team:
        return f"I couldn't match “{team_query}” to a team yet. Try the full club or country name."

    opponent = None
    if opponent_query:
        try:
            opponent = await find_team(opponent_query)
        except Exception:
            opponent = None

    try:
        fixtures = await priority_api_football_request(
            "fixtures", {"team": int(team["teamId"]), "next": 10},
        )
    except Exception as exc:
        print(f"[LISSA MATCH] fixture lookup skipped: {type(exc).__name__}: {exc}")
        return f"I found {team['teamName']}, but the fixture feed is unavailable right now."

    now = datetime.now(timezone.utc)
    upcoming: list[dict[str, Any]] = []
    for fixture in fixtures or []:
        if not isinstance(fixture, dict):
            continue
        meta = fixture.get("fixture") or {}
        status = str((meta.get("status") or {}).get("short") or "").upper()
        if status in {"FT", "AET", "PEN", "ABD", "AWD", "WO", "CANC", "PST"}:
            continue
        raw_date = str(meta.get("date") or "")
        try:
            kickoff = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
            if kickoff.tzinfo is None:
                kickoff = kickoff.replace(tzinfo=timezone.utc)
            if kickoff < now and status not in {"1H", "HT", "2H", "ET", "BT", "P", "LIVE"}:
                continue
        except (TypeError, ValueError):
            continue
        teams = fixture.get("teams") or {}
        home = teams.get("home") or {}
        away = teams.get("away") or {}
        if opponent:
            if int(home.get("id") or 0) != int(opponent.get("teamId") or 0) and int(away.get("id") or 0) != int(opponent.get("teamId") or 0):
                continue
        upcoming.append(fixture)

    if not upcoming:
        if opponent_query:
            return f"I couldn't find an upcoming {team['teamName']} versus {opponent_query} fixture."
        return f"I couldn't find an upcoming fixture for {team['teamName']} in the current schedule."

    lines = []
    for fixture in upcoming[:3]:
        teams = fixture.get("teams") or {}
        home = teams.get("home") or {}
        away = teams.get("away") or {}
        league = fixture.get("league") or {}
        lines.append(
            f"{home.get('name', 'Home')} vs {away.get('name', 'Away')} — "
            f"{_fixture_kickoff_text(fixture)}"
            + (f" ({league.get('name')})" if league.get("name") else "")
        )
    if len(lines) == 1:
        return f"{team['teamName']}'s next match is {lines[0]}."
    return f"Here are the next matches I found for {team['teamName']}:\n" + "\n".join(lines)


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
            f"{snapshot['date'] or 'undated'}: {snapshot['recommendation']} "
            f"{snapshot['propType']} { _format_number(snapshot['line']) }"
            f"{projection}{actual} — {result}{matchup}"
        )
    return (
        f"I found {len(matches)} saved pick(s) for {first}:\n\n"
        + "\n".join(lines)
        + "\n\nIf you want, ask me about one of these matches or why a specific pick landed where it did."
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
    # The client normally scopes this packet to My Picks, but the backend must
    # enforce the boundary too. A stale mobile tab, old bundle, or malformed
    # caller must not make Predict answer as if an old player modal is open.
    screen = context.get("screen")
    screen_name = str(screen.get("name") or "").strip().lower() if isinstance(screen, dict) else ""
    if screen_name and screen_name not in {"my picks", "analysis", "pick analysis", "predict"}:
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
        # Live evidence panels sent from the prediction screen
        "h2hPlayerStats": context.get("h2hPlayerStats") if isinstance(context.get("h2hPlayerStats"), dict) else {},
        "positionComparison": context.get("positionComparison") if isinstance(context.get("positionComparison"), dict) else {},
        "hitRates": context.get("hitRates") if isinstance(context.get("hitRates"), dict) else {},
    }


def _analysis_fallback(message: str, context: dict[str, Any]) -> str:
    """Natural spoken analysis using only values present in the saved pick."""
    pick = context.get("pick") or {}
    factors = context.get("factors") or pick.get("analysisFactors") or []
    player = str(pick.get("playerName") or "the player")
    opponent = str(pick.get("opponentName") or "the opponent")
    prop = _display_prop(pick.get("propType"))
    line = _format_number(pick.get("line"))
    recommendation = str(pick.get("recommendation") or "").upper()
    venue = str(pick.get("venue") or "").lower()
    confidence = _number(pick.get("confidence") or pick.get("confidenceScore"))

    line_number = _number(pick.get("line"))
    projection_number = _number(pick.get("projectedValue") or pick.get("projection"))
    projection = _format_number(projection_number)

    sentences: list[str] = []

    # Opening — establish the pick
    venue_phrase = f" at {venue}" if venue in ("home", "away") else ""
    rec_phrase = f" The model leans {recommendation}." if recommendation else ""
    if player and prop and line:
        sentences.append(
            f"{player} is on {prop} {line} against {opponent}{venue_phrase}.{rec_phrase}"
        )

    # Projection gap
    if line_number is not None and projection_number is not None:
        diff = projection_number - line_number
        direction = "above" if diff > 0 else "below"
        conf_str = f" with {_format_number(confidence)} confidence" if confidence is not None else ""
        sentences.append(
            f"The projection is {projection} — {abs(diff):.1f} points {direction} the line{conf_str}."
        )
    elif projection:
        sentences.append(f"The projection is {projection}.")

    # Evidence factors — plain language, not a list
    good_factors: list[str] = []
    for factor in factors[:6]:
        if not isinstance(factor, dict):
            continue
        title = str(factor.get("title") or factor.get("id") or "").strip()
        detail = str(factor.get("detail") or factor.get("summary") or "").strip()
        fdir = str(factor.get("direction") or "").strip()
        value = detail or fdir
        if title and value:
            good_factors.append(f"{title}: {value}")

    if good_factors:
        joined = "; ".join(good_factors[:3])
        sentences.append(f"The main signals behind it — {joined}.")

    # Tactical read — first two sentences only, keep it brief
    tactical = str(pick.get("tacticalBreakdown") or pick.get("reasoning") or "").strip()
    if tactical:
        tac_sentences = re.split(r"(?<=[.!?])\s+", tactical)
        brief = " ".join(tac_sentences[:2]).strip()
        if brief:
            sentences.append(f"The tactical read: {brief[:400]}")

    if not sentences:
        return (
            f"I can see {player}'s analysis is open, but the key fields are empty. "
            "The pick may not have a completed analysis saved."
        )

    return " ".join(sentences)


async def _smart_ledger_response(
    message: str,
    picks: list[dict[str, Any]],
    summary: dict[str, Any],
    recent_turns: list[dict[str, Any]] | None = None,
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
        "recentConversation": (recent_turns or [])[-6:],
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
            "Sound like a smart friend, not a report. Use contractions and plain language.",
            "Answer the question directly in one to three short spoken paragraphs. No headings, bullets, disclaimers, or robotic phrases.",
            "Use recent conversation only to understand follow-ups; never treat a prior assistant statement as new evidence.",
        ],
    }
    prompt = (
        "You are Lissa (pronounced Lisa), the owner-only intelligence assistant inside Reverse Picks.\n"
        "You are a calm, well-spoken woman. Address the owner as Reverse when speaking directly to him.\n"
        "The owner is asking about the saved prediction ledger, not asking for a new wager.\n"
        "Use the durable records below. Be precise, but do not dump the whole record when one sentence answers the question.\n"
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


async def _smart_analysis_response(
    message: str,
    context: dict[str, Any],
    recent_turns: list[dict[str, Any]] | None = None,
) -> str | None:
    """Use the existing bounded explanation gateway when it is available.

    Lissa must still work when generation is disabled or unavailable, so the
    deterministic explanation remains the fallback and the prediction ledger
    remains authoritative.
    """
    if os.environ.get("LISSA_AI_ENABLED", "true").lower() not in {"1", "true", "yes", "on"}:
        return None
    if not await _within_explanation_budget():
        return None
    import json
    # Pull the key pick fields to the top of the prompt so Gemini sees them
    # first instead of having to parse the full nested packet.
    pick = context.get("pick") or {}
    player_name = pick.get("playerName") or "the player"
    prop_label = _display_prop(pick.get("propType"))
    line_val = _format_number(pick.get("line"))
    proj_val = _format_number(pick.get("projectedValue") or pick.get("projection"))
    rec_val = str(pick.get("recommendation") or "").upper()
    conf_val = _format_number(pick.get("confidence") or pick.get("confidenceScore"))
    opp_val = pick.get("opponentName") or "the opponent"
    venue_val = str(pick.get("venue") or "").lower()

    pick_summary = (
        f"Player: {player_name} | Prop: {prop_label} {line_val} | "
        f"Projection: {proj_val} | Direction: {rec_val} | "
        f"Confidence: {conf_val} | Opponent: {opp_val} | Venue: {venue_val}"
    )
    packet = {
        "pickSummary": pick_summary,
        "question": message,
        "fullAnalysis": context,
        "recentConversation": (recent_turns or [])[-4:],
    }
    prompt = (
        "You are Lissa (pronounced Lisa), the owner-only assistant inside Reverse Picks.\n"
        "You are a calm, intelligent woman. Speak like a smart friend who knows the data — "
        "direct, no fluff, no bullet points, no headings, no markdown.\n"
        "Address the owner as 'Reverse' when speaking to him directly.\n\n"
        f"The pick currently open: {pick_summary}\n\n"
        "Here is the full analysis data:\n"
        f"{json.dumps(packet, ensure_ascii=False, default=str)[:16000]}\n\n"
        f"Owner's question: {message}\n\n"
        "Answer in one to three short spoken paragraphs. "
        "Start with the most direct answer to the question. "
        "Do not repeat the pick summary unless it directly answers the question. "
        "Call out thin or missing evidence honestly — don't gloss over gaps. "
        "Never promise a win or give financial advice."
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


def _session_id(req: LissaMessageRequest) -> str:
    return req.session_id or f"lissa-{uuid.uuid4().hex[:12]}"


def _screen_name(context: dict[str, Any] | None) -> str | None:
    if not isinstance(context, dict) or not isinstance(context.get("screen"), dict):
        return None
    value = str(context["screen"].get("name") or "").strip()
    return value or None


async def _finish_turn(
    req: LissaMessageRequest,
    session_id: str,
    response: str,
    mode: str,
    summary: dict[str, Any],
    orchestration: dict[str, Any] | None = None,
) -> dict[str, Any]:
    text = _address_owner(response)
    try:
        await aio.wait_for(
            remember_turn(
                req.email,
                session_id,
                req.message.strip(),
                text,
                mode,
                _screen_name(req.context),
            ),
            timeout=0.4,
        )
    except Exception as exc:
        print(f"[LISSA MEMORY] turn save skipped: {type(exc).__name__}: {exc}")
    response = {
        "assistant": ".2",
        "sessionId": session_id,
        "response": text,
        "readOnly": True,
        "mode": mode,
        "summary": summary,
    }
    if orchestration:
        response["orchestration"] = orchestration
        response["action"] = orchestration.get("action")
        response["tools"] = orchestration.get("tools", [])
    return response


@router.post("/overview")
async def lissa_overview(req: LissaOverviewRequest):
    await _authorize(req)
    picks = await _load_owner_picks()
    summary = _ledger_summary(picks)
    return {
        "assistant": ".2",
        "readOnly": True,
        "summary": summary,
        "message": _address_owner(
            "I’m JARVIS. Tell me what to run — Script Hunt, Board, Run a player, "
            "Opposite Case, Refresh Lines, or Postmortem. I’m read-only and will "
            "show what each workflow can verify."
        ),
        "sessionId": f"lissa-{uuid.uuid4().hex[:12]}",
    }


_THIS_RE = re.compile(
    r"\b(this pick|this analysis|this one|this prediction|explain this|"
    r"what.s this|what is this pick|why this|why the (over|under)|"
    r"break this|walk me through this|tell me about this)\b",
    re.IGNORECASE,
)


async def _build_gemini_prompt(
    message: str,
    packet: dict[str, Any],
    picks: list[dict[str, Any]],
    summary: dict[str, Any],
    recent_turns: list[dict[str, Any]],
    context: dict[str, Any] | None,
) -> str:
    """Build a single self-contained prompt for Gemini covering all Lissa context."""
    import json

    screen_name = _screen_name(context) or "Reverse Picks"

    # Ledger summary
    counts = summary.get("counts", {})
    hit_rate = summary.get("hitRate")
    rate_str = f"{hit_rate:.1f}% HIT rate" if hit_rate else "HIT rate unavailable"
    ledger_line = (
        f"{summary.get('total', 0)} picks total — "
        f"{counts.get('HIT', 0)} HIT / {counts.get('MISS', 0)} MISS ({rate_str}), "
        f"{counts.get('LIVE', 0)} live, {counts.get('PENDING', 0)} pending"
    )

    # Active pick context (only when the screen is My Picks)
    pick_block = ""
    if packet:
        pick = packet.get("pick") or {}
        player = str(pick.get("playerName") or "unknown player")
        prop = _display_prop(pick.get("propType"))
        line = _format_number(pick.get("line"))
        rec = str(pick.get("recommendation") or "").upper()
        proj = _format_number(pick.get("projectedValue") or pick.get("projection"))
        conf = _format_number(pick.get("confidence") or pick.get("confidenceScore"))
        opp = str(pick.get("opponentName") or "unknown opponent")
        venue = str(pick.get("venue") or "").lower()
        line_n = _number(pick.get("line"))
        proj_n = _number(pick.get("projectedValue") or pick.get("projection"))
        gap_line = ""
        if line_n is not None and proj_n is not None:
            diff = proj_n - line_n
            gap_line = f" — projection is {abs(diff):.1f} pts {'above' if diff > 0 else 'below'} the line"

        factors = packet.get("factors") or []
        factor_lines = []
        for f in factors[:5]:
            if isinstance(f, dict):
                t = str(f.get("title") or "").strip()
                d = str(f.get("detail") or f.get("summary") or "").strip()
                if t and d:
                    factor_lines.append(f"  • {t}: {d}")
        tactical = str(pick.get("tacticalBreakdown") or "").strip()

        pick_block = (
            f"\n--- OPEN PICK ANALYSIS ---\n"
            f"Player: {player} | Prop: {prop} {line} | Rec: {rec} | "
            f"Projection: {proj}{gap_line} | Confidence: {conf} | vs {opp} ({venue})\n"
        )
        if factor_lines:
            pick_block += "Key model factors:\n" + "\n".join(factor_lines) + "\n"
        if tactical:
            pick_block += f"Tactical read: {tactical[:600]}\n"

        # ── Bayesian engine internals ──────────────────────────────────────────
        bm = pick.get("matchFactors", {}).get("bayesian") or pick.get("bayesianMetrics") or {}
        if not bm:
            bm = {}
        def _fp(k): v = bm.get(k); return float(v) if v is not None else None  # noqa
        pm = _fp("priorMean") or _number(pick.get("priorMean"))
        posterior = _fp("posteriorMean")
        p_over = _fp("pOver") or _fp("pOverLine")
        p_under = _fp("pUnder") or _fp("pUnderLine")
        n_games = bm.get("priorSamples") or bm.get("sampleSize") or pick.get("priorSamples")
        mom = _fp("momentumEffect")
        mom_label = str(bm.get("momentumLabel") or "")
        eff_std = _fp("effStd") or _fp("eff_std")
        opp_allowed_avg = _fp("oppAllowedAvg") or _fp("rawOppAllowedAvg")
        pair_share = _fp("pairShare")
        comp_season = _fp("compSeasonAvg")
        bm_parts = []
        if pm is not None: bm_parts.append(f"prior mean={pm:.1f}")
        if posterior is not None: bm_parts.append(f"posterior={posterior:.1f}")
        if p_over is not None: bm_parts.append(f"P(OVER)={p_over:.0f}%")
        if p_under is not None: bm_parts.append(f"P(UNDER)={p_under:.0f}%")
        if n_games is not None: bm_parts.append(f"n={int(n_games)} log games")
        if mom is not None and abs(mom) > 0.05: bm_parts.append(f"momentum={mom:+.2f} ({mom_label})" if mom_label else f"momentum={mom:+.2f}")
        if eff_std is not None: bm_parts.append(f"eff_std={eff_std:.2f}")
        if opp_allowed_avg is not None: bm_parts.append(f"opp_allowed_avg={opp_allowed_avg:.1f}")
        if pair_share is not None: bm_parts.append(f"pair_share={pair_share:.2f}")
        if comp_season is not None: bm_parts.append(f"comp_season_avg={comp_season:.1f}")
        if bm_parts:
            pick_block += f"Bayesian engine: {', '.join(bm_parts)}\n"

        # Calibration layers (scenario priors, odds-tier priors, league calib)
        lc = bm.get("leagueCalib") or {}
        sp = bm.get("scenarioPriors") or {}
        calib_parts = []
        if lc.get("multiplier") is not None: calib_parts.append(f"league_calib={float(lc['multiplier']):.4f} (n={lc.get('n',0)})")
        if sp.get("multiplier") is not None: calib_parts.append(f"scenario_priors={float(sp['multiplier']):.4f} (n={sp.get('n',0)})")
        if calib_parts:
            pick_block += f"Calibration: {', '.join(calib_parts)}\n"

        # Analysis summary (venue avg, opp-allowed)
        as_ = pick.get("analysisSummary") or {}
        venue_avg = _number(as_.get("venueAverage"))
        venue_n = as_.get("venueSampleSize")
        opp_avg_allowed = _number(as_.get("opponentAllowedAverage"))
        gk_sot = _number(as_.get("opponentShotsOnTarget"))
        if venue_avg is not None:
            v_n_str = f" over {int(venue_n)} games" if venue_n else ""
            pick_block += f"Player {venue} avg: {venue_avg:.1f}{v_n_str}\n"
        if opp_avg_allowed is not None:
            pick_block += f"Opponent-allowed avg (same position): {opp_avg_allowed:.1f}\n"
        if gk_sot is not None:
            pick_block += f"Opponent shots-on-target (GK context): {gk_sot:.1f} per game\n"

        # Recent game logs — raw values so .2 can reference specific games
        logs_raw = pick.get("gameLogs") or []
        if isinstance(logs_raw, dict):
            logs_raw = logs_raw.get("games") or []
        if logs_raw and isinstance(logs_raw, list):
            log_entries = []
            for g in logs_raw[-12:]:
                if not isinstance(g, dict): continue
                v = g.get("value")
                if v is None: continue
                v_str = f"{int(v)}" if isinstance(v, float) and v == int(v) else f"{round(float(v), 1)}"
                opp_g = str(g.get("opponent") or g.get("opponentName") or "")
                venue_g = str(g.get("venue") or ("home" if g.get("isHome") else "away" if g.get("isHome") is False else ""))
                mins = g.get("minutes")
                entry = v_str
                if opp_g: entry += f"(vs {opp_g[:12]}"
                if venue_g: entry += f" {venue_g}"
                if mins: entry += f" {int(mins)}min"
                if opp_g or venue_g or mins: entry += ")"
                log_entries.append(entry)
            if log_entries:
                pick_block += f"Recent game logs (oldest→newest): {', '.join(log_entries)}\n"

        # Line deviation hit rate
        ldhr = _number(pick.get("lineDeviationHitRate"))
        if ldhr is not None:
            pick_block += f"Line deviation hit rate: {ldhr:.0f}% (historical hit rate when line is set this far from avg)\n"

        # Game script + possession
        gs = pick.get("gameScript") or {}
        gs_finding = str(gs.get("key_finding") or "").strip()
        if gs_finding:
            pick_block += f"Game script: {gs_finding[:300]}\n"
        ep = pick.get("expectedPossession") or pick.get("matchFactors", {}).get("expectedPoss")
        if isinstance(ep, dict):
            hp, ap = ep.get("home"), ep.get("away")
            if hp is not None: pick_block += f"Expected possession: home {hp}% / away {ap}%\n"
        elif isinstance(ep, (int, float)):
            pick_block += f"Expected possession: {ep}%\n"

        # H2H player vs this opponent
        h2h = packet.get("h2hPlayerStats") or {}
        if h2h and h2h.get("avgVsOpponent") is not None:
            h2h_avg = h2h.get("avgVsOpponent")
            h2h_n = h2h.get("sampleSize") or 0
            matches = h2h.get("matches") or []
            vals = []
            for m in matches[:5]:
                v = m.get("value") or m.get("statValue")
                if v is not None:
                    vals.append(str(round(float(v), 0) if isinstance(v, float) else v))
            venue_meetings = h2h.get("teamMeetingsByVenue") or {}
            home_avg = venue_meetings.get("home", {}).get("average") if isinstance(venue_meetings.get("home"), dict) else None
            away_avg = venue_meetings.get("away", {}).get("average") if isinstance(venue_meetings.get("away"), dict) else None
            h2h_str = f"H2H vs {opp}: avg {float(h2h_avg):.1f} over {h2h_n} apps"
            if home_avg is not None:
                h2h_str += f" (home avg {float(home_avg):.1f}"
                if away_avg is not None:
                    h2h_str += f", away avg {float(away_avg):.1f}"
                h2h_str += ")"
            if vals:
                h2h_str += f" | Recent games: {', '.join(vals)}"
            pick_block += f"H2H data: {h2h_str}\n"

        # Exact position evidence (comparable players vs same opponent)
        pc = packet.get("positionComparison") or {}
        if pc:
            pc_avg = pc.get("avgAllowed") or pc.get("average") or pc.get("avgVsOpponent")
            pc_n = pc.get("distinctPlayers") or pc.get("sampleSize") or pc.get("n")
            pc_over = pc.get("overPercent") or pc.get("overPct") or pc.get("pctOver")
            pc_pos = pc.get("positionLabel") or pc.get("position") or pc.get("positionGroup")
            pc_status = pc.get("status") or ""
            comp_parts = []
            if pc_avg is not None:
                comp_parts.append(f"comparable {pc_pos or 'position'} avg {float(pc_avg):.1f}")
            if pc_n:
                comp_parts.append(f"{pc_n} players")
            if pc_over is not None:
                comp_parts.append(f"{float(pc_over):.0f}% OVER / {100 - float(pc_over):.0f}% UNDER this line")
            if pc_status:
                comp_parts.append(f"({pc_status})")
            if comp_parts:
                pick_block += f"Exact position evidence: {', '.join(comp_parts)}\n"

        # Historical hit rates for this prop/direction
        hr = packet.get("hitRates") or {}
        if hr and hr.get("overPct") is not None:
            over_pct = float(hr.get("overPct") or 0)
            over_hits = hr.get("overHits") or 0
            total = hr.get("total") or 0
            under_hits = total - over_hits if total else 0
            pick_block += (
                f"Hit rates (this prop history): OVER {over_pct:.1f}% ({int(over_hits)}/{int(total)} hits) "
                f"| UNDER {100 - over_pct:.1f}% ({int(under_hits)}/{int(total)} hits)\n"
            )

        pick_block += "--- END PICK ---"

    # Recent settled picks (last 12) for ledger questions
    settled = [p for p in picks if str(p.get("result") or "") in ("HIT", "MISS")][-12:]
    settled_lines = []
    for p in settled:
        name = str(p.get("playerName") or "?")
        prop = _display_prop(p.get("propType"))
        res = str(p.get("result") or "?")
        line = _format_number(p.get("line"))
        opp = str(p.get("opponentName") or "")
        opp_str = f" vs {opp}" if opp else ""
        settled_lines.append(f"{name} — {prop} {line}{opp_str} → {res}")
    settled_block = ""
    if settled_lines:
        settled_block = "\n--- RECENT SETTLED PICKS ---\n" + "\n".join(settled_lines) + "\n--- END ---"

    # Recent conversation
    convo_block = ""
    if recent_turns:
        lines = []
        for t in recent_turns[-5:]:
            lines.append(f"Jossel: {t.get('user', '')}")
            lines.append(f".2: {t.get('assistant', '')[:300]}")
        convo_block = "\n--- RECENT CONVERSATION ---\n" + "\n".join(lines) + "\n--- END ---"

    # Screen-specific context blocks
    screen_lower = screen_name.lower()
    extra_block = ""

    # Community feed context
    feed = context.get("feed") if isinstance(context, dict) else None
    if isinstance(feed, list) and feed:
        feed_lines = "\n".join(str(f) for f in feed[:6])
        extra_block += f"\n--- COMMUNITY FEED (recent messages) ---\n{feed_lines}\n--- END ---"
        online = context.get("onlineCount")
        if online:
            extra_block += f"\n{online} members currently online."

    # Account context
    acc_type = context.get("accountType") if isinstance(context, dict) else None
    if acc_type:
        is_owner = context.get("isOwner")
        is_lifetime = context.get("isLifetime")
        sub_status = context.get("subscriptionStatus")
        acc_summary = f"Account type: {acc_type}"
        if is_owner:
            acc_summary += " (Owner — full access)"
        elif is_lifetime:
            acc_summary += " (Lifetime subscriber)"
        if sub_status:
            acc_summary += f" | Subscription detail: {str(sub_status)[:200]}"
        extra_block += f"\n--- ACCOUNT ---\n{acc_summary}\n--- END ---"

    return (
        "You are .2 (pronounced 'point two') — Jossel's personal AI inside Reverse Picks.\n"
        "The owner's name is Jossel (pronounced 'joe-cel'). Use it naturally in every response, like a close friend would.\n\n"
        "YOU SEE EVERYTHING on Jossel's screen right now. The data below is exactly what he's looking at.\n"
        "Never say your access is limited. If data exists below, use it. If something genuinely isn't there, say so in one sentence and keep going.\n\n"
        "HOW YOU TALK:\n"
        "— Direct. Sharp. Like a top sports analyst who knows the game cold. Think Jarvis — concise but complete.\n"
        "— Short sentences. Contractions. No filler words, no padding.\n"
        "— Never open with 'Certainly', 'Of course', 'Great question', or any acknowledgement. Just answer.\n"
        "— First sentence = the actual answer or main verdict with a real number. Then back it up with evidence.\n"
        "— Use real player names and real numbers from the data. Be specific — if the H2H avg is 82.5, say 82.5.\n"
        "— No bullets. No headings. No markdown. Flowing prose like a real analyst on a call.\n"
        "— DEPTH RULE: For quick factual questions, 2-3 sentences is enough. For 'explain this', 'walk me through', 'why', or 'full analysis' — go as deep as the question demands. Never cut off mid-analysis.\n"
        "— When asked to explain a full page/pick: cover ALL sections in order — projection vs line, Bayesian layers, evidence labels, H2H avg, position avg, hit rates, guards/caps that fired, tactical factors. Don't stop after one section.\n"
        "— You have Google Search available — use it for current injury news, tactical form guides, transfer updates, opponent press style, and any real-world context the app data doesn't cover.\n"
        "— Always address Jossel by name at least once.\n\n"
        "ENGINE KNOWLEDGE — you built this model, you know it cold:\n\n"
        "THE 3-LAYER SYSTEM:\n"
        "Layer 1 — BAYESIAN PROJECTION: Takes last N game logs, computes a prior mean (season avg), applies updates via Bayesian inference. Key adjustments: venue split (home vs away avg), momentum (last 3-5 games vs prior), covariate adj (possession context, game script), opponent quality, rest/fatigue, match stakes (Europa vs league final pressure), lineup rotation risk, CDM inversion (deep-block teams suppress possession for the player's team). Outputs a posteriorMean and eff_std (effective standard deviation). priorMean = raw season average before any context. posteriorMean = after all Bayesian updates. projectedValue = the final number shown to the user after calibration.\n"
        "Layer 2 — EMPIRICAL CALIBRATION: Uses settled pick history to learn bias. Three sub-layers stack multiplicatively: (a) league calibration (how accurate the model is for this league/prop, n= the settled pick count, mult= the correction factor), (b) scenario priors (matches the exact prop/direction/position/venue bucket with James-Stein shrinkage so thin buckets borrow from parent), (c) odds-tier priors (adjusts based on whether the team is a favourite, close, or heavy underdog per moneyline). A multiplier > 1.0 means the model historically underestimated; < 1.0 means overestimated. You can see leagueCalib.multiplier and scenarioPriors.multiplier in the data.\n"
        "Layer 3 — AI TACTICAL SYNTHESIS: Gemini analyses opponent shape (formation, pressing style, how they defend in possession), player role (CDM shield, pressing forward, ball-playing CB), home/away game script, weather, and produces a prose tactical read that either confirms or challenges the math.\n\n"
        "GUARDS AND CAPS (why confidence can drop below the raw Bayesian level):\n"
        "— COIN-FLIP GUARD: if P(max) < 55%, confidence is capped at 52% regardless.\n"
        "— TIGHT EDGE GUARD: if projection gap is less than 0.5 units above the line, capped at 58%.\n"
        "— BASE-RATE CONFLICT: if season avg is on the opposite side of the line from the recommendation, -25% conf penalty.\n"
        "— CONVICTION FILTER: P(max) < 60% → cap at 54%.\n"
        "— EDGE CAL: edgeZ measures the projected edge relative to historical variance; negative edgeZ = additional confidence cut.\n"
        "— LOW CONV: P(max) < 57% with a medium Bayesian confidence → capped at 52%.\n\n"
        "EVIDENCE LABELS:\n"
        "— VERIFIES: the evidence actively confirms the pick direction.\n"
        "— WEAKENS: the evidence contradicts or undermines the pick.\n"
        "— NEUTRAL: evidence available but not clearly directional.\n"
        "— STRONG EDGE / SAFE: projection gap is large, historical hit rate is above 60%, both aligned.\n"
        "— NO EDGE / RISKY: projection gap is within noise, or historical hit rate is flat.\n"
        "— HIST X%(N): X% of previously saved picks for this player/prop/direction HIT. N = sample count. N < 10 = thin — treat cautiously.\n"
        "— P(OVER) / P(UNDER): Monte Carlo probability from sampling the posterior distribution 5000+ times.\n\n"
        "CONFIDENCE LEVELS: 48–54% = Low (borderline), 55–60% = Medium (lean), 61–70% = High (solid edge), 71–80% = Very High, 81%+ = Extreme (rare). Calibrated probability is NOT the same as confidence — calibrated prob is the raw Bayesian/Monte Carlo output; confidence is the post-guard, post-calibration actionability score.\n\n"
        "KEY METRICS TO EXPLAIN ON DEMAND:\n"
        "— priorMean: season average before context. posteriorMean: after Bayesian updates.\n"
        "— eff_std: effective standard deviation of the posterior — wider = more uncertain.\n"
        "— momentumEffect: positive = player is trending above their average recently.\n"
        "— covariateAdj: possession/game-script adjustment. Positive = model expects more of the prop.\n"
        "— oppAllowedAvg: how much the opponent typically allows for this position/prop.\n"
        "— pairShare: how the player's production compares to their team's overall output in this prop.\n"
        "— compSeasonAvg: comparable players' season average — context for whether the prior is high or low.\n"
        "— lineDeviationHitRate: when a line is set X% above/below the player's avg, how often does the pick hit historically.\n"
        "— venue avg: the player's specific average in home or away games (not overall season avg).\n"
        "— H2H avg: how this player has specifically performed against this exact opponent historically.\n"
        "— exact position evidence: what comparable players (same position, same venue) averaged vs this same opponent — the strongest directional signal.\n\n"
        "SOCCER PROPS BENCHMARKS:\n"
        "— Pass attempts: GKs 20–35, CBs 50–90, LB/RB 40–70, CDMs 60–100, CMs 50–80, CAMs 40–70, Wingers 30–55, Forwards 20–45. Possession teams inflate everyone. High press from opponent compresses.\n"
        "— Shots on target: Forward 1.5–3.0, Midfielders 0.5–1.5, Defenders 0.2–0.5 per game.\n"
        "— Goals: 0.5 line = will this player score at all. Top forwards ~30–40% per game, mid 10–20%, defenders 3–8%.\n"
        "— Key passes: CAM/CM 1–3 per game. Full-backs with offensive license 0.5–1.5.\n"
        "— Saves: GK lines driven by opponent xSOT. High-press teams force more saves.\n"
        "— Cards: aggressive CDMs in derby or 50/50 matches = elevated risk.\n"
        "— xG (expected goals), xA (expected assists), PPDA (lower = more pressing intensity = opponent fewer passes).\n"
        "— Formations: 4-3-3 (wide press), 4-2-3-1 (controlled), 3-5-2 (wing-heavy), 4-4-2 (direct), 5-3-2 (park the bus).\n"
        "— H2H player-specific history is the strongest signal — beats generic form when sample ≥ 3.\n"
        "— Home/away splits flip entire game styles for some teams. Always consider venue.\n\n"
        f"OWNER: Jossel (pronounced 'joe-cel') — address him by name naturally in every response.\n"
        f"SCREEN: {screen_name}\n"
        f"LEDGER: {ledger_line}\n"
        f"{pick_block}"
        f"{settled_block}"
        f"{extra_block}"
        f"{convo_block}\n\n"
        f"Jossel: {message}"
    )


_LISSA_PRO_MODEL = os.environ.get("LISSA_PRO_MODEL", "gemini-2.5-pro")


async def _lissa_pro_ai(prompt: str) -> str | None:
    """
    Dedicated .2 AI call — Gemini 2.5 Pro with Google Search grounding.
    Separate from the pick-card explanation pipeline so it has no word caps,
    no caching, and full tactical search capability.
    """
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        return None
    payload: dict[str, Any] = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "tools": [{"google_search": {}}],
        "generationConfig": {
            "temperature": 0.75,
            "maxOutputTokens": 2048,
        },
    }
    url = (
        "https://generativelanguage.googleapis.com/v1beta/"
        f"models/{_LISSA_PRO_MODEL}:generateContent?key={api_key}"
    )
    try:
        import httpx
        async with httpx.AsyncClient(timeout=26.0) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
        candidates = data.get("candidates", [])
        if not candidates:
            print(f"[LISSA PRO] no candidates in response")
            return None
        parts = candidates[0].get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts if "text" in p).strip()
        # Strip any markdown formatting — this is spoken audio
        text = re.sub(r"\*\*|[*_`#>]", "", text)
        text = re.sub(r"\n{2,}", " ", text).strip()
        print(f"[LISSA PRO] {_LISSA_PRO_MODEL} responded ({len(text)} chars)")
        return text if len(text) >= 15 else None
    except Exception as exc:
        print(f"[LISSA PRO] failed: {type(exc).__name__}: {exc}")
        return None


async def _smart_primary_response(
    message: str,
    packet: dict[str, Any],
    picks: list[dict[str, Any]],
    summary: dict[str, Any],
    recent_turns: list[dict[str, Any]],
    context: dict[str, Any] | None,
) -> str | None:
    """Gemini 2.5 Pro primary response with Google Search grounding."""
    if os.environ.get("LISSA_AI_ENABLED", "true").lower() not in {"1", "true", "yes", "on"}:
        return None
    prompt = await _build_gemini_prompt(message, packet, picks, summary, recent_turns, context)
    try:
        text = await aio.wait_for(
            _lissa_pro_ai(prompt),
            timeout=25.0,
        )
        cleaned = (text or "").strip()
        return cleaned if len(cleaned) >= 15 else None
    except aio.TimeoutError:
        print("[LISSA PRO] timed out after 25s — falling back")
        # Fast fallback using Flash if Pro times out
        try:
            text = await aio.wait_for(
                _generate_explanation(prompt),
                timeout=10.0,
            )
            cleaned = (text or "").strip()
            return cleaned if len(cleaned) >= 15 else None
        except Exception:
            return None
    except Exception as exc:
        print(f"[LISSA AI] primary response failed: {type(exc).__name__}: {exc}")
        return None


@router.post("/message")
async def lissa_message(req: LissaMessageRequest):
    await _authorize(req)
    message = req.message.strip()
    session_id = _session_id(req)

    # Named commands use the shared action orchestrator instead of the retired
    # ledger-only chat path. Each tool is bounded, read-only, and provenance
    # labeled; failures become UNKNOWN inside the response.
    async def _fixtures_for_team(team_id: int) -> list[dict[str, Any]]:
        raw = await priority_api_football_request("fixtures", {"team": team_id, "next": 10})
        if isinstance(raw, list):
            return raw
        if isinstance(raw, dict):
            rows = raw.get("response") or raw.get("data") or []
            return rows if isinstance(rows, list) else []
        return []

    async def _discover_slate() -> list[dict[str, Any]]:
        # API-Football's global upcoming-fixtures query is the portable
        # discovery primitive. Date/status-only requests are rejected by
        # some API-Sports plans, so narrow the returned window locally.
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

    action_result = await execute_action(
        message,
        context=req.context,
        load_picks=_load_owner_picks_cached,
        find_team=find_team,
        fetch_fixtures=_fixtures_for_team,
        discover_slate=_discover_slate,
        load_board=lambda: list_market_board(hours=72, limit=60, sport_id="SOCCER"),
        load_memory=lambda: retrieve_tactical_memory(db, include_stale=False, limit=30),
    )
    if action_result.get("action") != "general":
        try:
            picks_for_summary = await _load_owner_picks_cached()
        except Exception as exc:
            print(f"[LISSA] action summary load skipped: {type(exc).__name__}: {exc}")
            picks_for_summary = []
        return await _finish_turn(
            req,
            session_id,
            action_result["response"],
            f"action:{action_result['action']}",
            _ledger_summary(picks_for_summary),
            orchestration=action_result,
        )

    # 1. Instant screen/identity responses — no I/O needed
    fast = _fast_response(message, req.context)
    if fast:
        return await _finish_turn(req, session_id, fast, "instant", _empty_summary())

    # 2. Fixture schedule lookup
    try:
        match_response = await aio.wait_for(_upcoming_match_response(message), timeout=5.5)
    except aio.TimeoutError:
        match_response = "The fixture search timed out. Try a more specific team name."
    except Exception as exc:
        print(f"[LISSA MATCH] failed: {type(exc).__name__}: {exc}")
        match_response = None
    if match_response:
        return await _finish_turn(req, session_id, match_response, "match-search", _empty_summary())

    # 3. Check if a pick card is open (My Picks screen only)
    packet = _analysis_packet(req.context)

    # Redirect "this pick" when no card is open
    if _THIS_RE.search(message) and not packet:
        active_screen = _screen_name(req.context) or "the current screen"
        response = (
            f"I don't have a pick open right now on {active_screen}. "
            "Open a pick card in My Picks and ask me again."
        )
        return await _finish_turn(req, session_id, response, "instant", _empty_summary())

    # 4. Load durable context (picks cached 30s + memory) in parallel
    try:
        picks, recent_turns = await aio.gather(
            _load_owner_picks_cached(),
            aio.wait_for(load_recent_turns(req.email, session_id), timeout=0.5),
            return_exceptions=True,
        )
        if isinstance(picks, Exception):
            print(f"[LISSA] picks load failed: {picks}")
            picks = []
        if isinstance(recent_turns, Exception):
            recent_turns = []
    except Exception as exc:
        print(f"[LISSA] context load failed: {type(exc).__name__}: {exc}")
        picks, recent_turns = [], []

    summary = _ledger_summary(picks)

    # 5. Gemini is the primary path — it handles analysis, ledger, and general questions
    response = await _smart_primary_response(
        message, packet, picks, summary, recent_turns, req.context,
    )

    # 6. Deterministic fallback when AI is unavailable
    if not response:
        if packet:
            response = _analysis_fallback(message, packet)
        else:
            matches = _match_player(picks, message)
            if matches:
                response = _player_text(matches)
            else:
                response = _summary_text(summary) if summary.get("total") else (
                    "I'm having trouble reaching my AI right now. "
                    "Try again in a moment, or ask me something specific like your hit rate or a player's name."
                )

    return await _finish_turn(req, session_id, response, "ai-primary", summary)