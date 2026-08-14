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
        or re.fullmatch(r"(hello|hi|hey)( lissa| lisa)?[.!? ]*", lowered)
    ):
        return "Yes, I can hear you. Ready when you are."

    # Identity
    if any(term in lowered for term in ("your name", "who are you", "are you lisa", "are you lissa")):
        return "I'm Lissa — Reverse Picks' owner-only assistant. You can call me Lissa or Lisa."

    # Capabilities
    if any(term in lowered for term in ("what can you do", "capabilities", "what do you know", "help me", "how do i use")):
        return (
            "When you have a pick analysis open, ask me things like 'why the over,' 'explain this pick,' "
            "or 'what's the projection.' I can also answer questions about your saved ledger — "
            "hit rate, specific players, patterns. And I can look up upcoming fixtures if you name a team. "
            "I'm read-only; I can't change picks or settle results."
        )

    # App description
    if any(term in lowered for term in ("what is reverse picks", "what is this app", "what does this app do")):
        return (
            "Reverse Picks is a structured player-props analysis app. "
            "It builds a verified matchup picture — player form, opponent data, odds, tactical role — "
            "and uses that to project a direction with an explicit evidence trail."
        )

    # Screen / page questions — broad pattern match
    screen_match = (
        _SCREEN_QUESTION_RE.search(lowered)
        or any(term in lowered for term in ("what page", "what screen", "where am i", "what tab"))
    )
    if screen_match:
        ctx = context if isinstance(context, dict) else {}
        screen = ctx.get("screen") if isinstance(ctx, dict) else {}
        name = str(screen.get("name") or "Reverse Picks") if isinstance(screen, dict) else "Reverse Picks"

        # If a pick card is open in My Picks, mention it briefly — don't launch into analysis
        pick = ctx.get("pick") if isinstance(ctx, dict) else None
        if isinstance(pick, dict) and pick.get("playerName") and name == "My Picks":
            player = str(pick.get("playerName") or "a player")
            prop = _display_prop(pick.get("propType"))
            rec = str(pick.get("recommendation") or "").upper()
            line = _format_number(pick.get("line"))
            line_str = f" {line}" if line else ""
            rec_str = f", leaning {rec}" if rec else ""
            return (
                f"You're on My Picks. {player}'s {prop}{line_str} analysis is open{rec_str}. "
                f"Ask me to explain the pick or walk through the evidence."
            )

        description = str(screen.get("description") or "").strip() if isinstance(screen, dict) else ""
        if description:
            return f"You're on {name}. {description}"
        return f"You're on {name}."

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
    return {
        "assistant": "Lissa",
        "sessionId": session_id,
        "response": text,
        "readOnly": True,
        "mode": mode,
        "summary": summary,
    }


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
            lines.append(f"Reverse: {t.get('user', '')}")
            lines.append(f"Lissa: {t.get('assistant', '')[:300]}")
        convo_block = "\n--- RECENT CONVERSATION ---\n" + "\n".join(lines) + "\n--- END ---"

    return (
        "Your name is Lissa. You're the AI voice inside Reverse Picks, talking directly to the owner.\n"
        "The owner's name is Reverse. Use it naturally — not at the start of every single sentence, just when it fits.\n\n"
        "HOW YOU TALK:\n"
        "- Sound like a smart friend who actually knows the numbers, not a customer support bot.\n"
        "- Short sentences. Contractions. Real words. Never say 'Certainly', 'Of course', 'I understand that', or 'Great question'.\n"
        "- Don't acknowledge the question — just answer it. Lead with the actual answer in the first sentence.\n"
        "- Use the player's real name. Use the actual numbers. Be specific.\n"
        "- Two or three short paragraphs max. Never use bullet points or headings.\n"
        "- If evidence is thin or missing, say that plainly in one sentence and move on.\n"
        "- Never promise a result. Never give financial advice. You can't change picks or run new predictions.\n\n"
        f"Reverse is currently on: {screen_name}\n"
        f"His ledger: {ledger_line}\n"
        f"{pick_block}"
        f"{settled_block}"
        f"{convo_block}\n\n"
        f"Reverse says: {message}"
    )


async def _smart_primary_response(
    message: str,
    packet: dict[str, Any],
    picks: list[dict[str, Any]],
    summary: dict[str, Any],
    recent_turns: list[dict[str, Any]],
    context: dict[str, Any] | None,
) -> str | None:
    """Gemini-powered primary response — handles all non-trivial questions."""
    if os.environ.get("LISSA_AI_ENABLED", "true").lower() not in {"1", "true", "yes", "on"}:
        return None
    if not await _within_explanation_budget():
        return None
    prompt = await _build_gemini_prompt(message, packet, picks, summary, recent_turns, context)
    try:
        text = await aio.wait_for(
            _generate_explanation(prompt),
            timeout=14.0,
        )
        cleaned = text.strip() if text else ""
        return cleaned if len(cleaned) >= 20 else None
    except Exception as exc:
        print(f"[LISSA AI] primary response failed: {type(exc).__name__}: {exc}")
        return None


@router.post("/message")
async def lissa_message(req: LissaMessageRequest):
    await _authorize(req)
    message = req.message.strip()
    session_id = _session_id(req)

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

    # 4. Load durable context (picks + memory) in parallel
    try:
        picks, recent_turns = await aio.gather(
            _load_owner_picks(),
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