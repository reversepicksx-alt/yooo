"""Detailed customer-facing tactical match reports.

The projection ledger remains deterministic. Gemini is used only to turn a
finalized evidence packet into a long, evidence-gated tactical report. This
module has no background entry point and never changes a prediction value.
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
_MIN_WORDS = 800
_MAX_WORDS = 1100
_CACHE_VERSION = "compact-v2-longform"
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
    text = str(value or "").replace("\r", "")
    text = re.sub(r"\*\*|[`#]", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    # Do not allow a provider, model, or error string into the customer card.
    if re.search(r"\b(api[- ]?football|gemini|provider|llm error|model ledger)\b", text, re.I):
        return ""
    words = text.split()
    if len(words) > _MAX_WORDS:
        text = " ".join(words[:_MAX_WORDS]).rstrip(" ,;:") + "."
    return text


def _word_count(value: Any) -> int:
    return len(str(value or "").split())


def _longform_usable(value: Any) -> bool:
    text = str(value or "").strip()
    return (
        _word_count(text) >= _MIN_WORDS
        and not re.match(r"^(for the|structured analysis loading|analysis is pending)\b", text, re.I)
    )


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


def _recent_packet(prediction: dict[str, Any]) -> dict[str, Any]:
    player_logs = prediction.get("playerGameLogs") or {}
    source = prediction.get("gameLogs") or player_logs.get("games") or []
    prop_fields = {
        "pass_attempts": "passes_total", "passes": "passes_total",
        "shots": "shots_total", "shots_on_target": "shots_on",
        "key_passes": "passes_key", "tackles": "tackles_total",
        "clearances": "clearances", "interceptions": "interceptions",
        "dribbles": "dribbles_attempts", "crosses": "crosses",
        "saves": "goals_saves", "goalie_saves": "goals_saves",
    }
    target_field = prop_fields.get(str(prediction.get("propType") or ""))
    rows = []
    for row in source[:20] if isinstance(source, list) else []:
        if not isinstance(row, dict):
            continue
        rows.append({
            "date": str(row.get("date") or row.get("gameDate") or "")[:10],
            "opponent": row.get("opponent") or row.get("opponentName"),
            "venue": row.get("venue") or (
                "home" if row.get("isHome") is True else "away" if row.get("isHome") is False else None
            ),
            "value": (
                row.get("value")
                if row.get("value") is not None
                else row.get("targetStat")
                if row.get("targetStat") is not None
                else row.get(target_field) if target_field else None
            ),
            "minutes": row.get("minutes"),
            "teamPossession": row.get("teamPossession"),
            "opponentPossession": row.get("opponentPossession"),
            "score": row.get("score") or row.get("matchScore"),
        })
    home_count = sum(1 for row in rows if row.get("venue") == "home")
    away_count = sum(1 for row in rows if row.get("venue") == "away")
    return {
        "sampleSize": len(rows),
        "homeCount": home_count,
        "awayCount": away_count,
        "homeAverage": prediction.get("homeAvg", player_logs.get("homeAvg")),
        "awayAverage": prediction.get("awayAvg", player_logs.get("awayAvg")),
        "hitRates": prediction.get("hitRates") or player_logs.get("hitRates") or {},
        "matches": rows,
    }


def build_evidence_packet(prediction: dict[str, Any]) -> dict[str, Any]:
    """Keep the long-form generation input final, rich, and auditable."""
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
    opponent_profile = prediction.get("opponentDefensiveProfile") or prediction.get("opponentProfile") or {}
    quality = prediction.get("evidenceQuality") or {}
    ledger = prediction.get("factorLedger") or {}
    ledger_steps = ledger.get("steps") if isinstance(ledger, dict) else []
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
            "projection": (
                prediction.get("projectedValue")
                if prediction.get("projectedValue") is not None
                else prediction.get("projection")
                if prediction.get("projection") is not None
                else prediction.get("bayesianProjection")
            ),
            "recommendation": str(prediction.get("recommendation") or "").upper(),
            "pOver": bm.get("pOver"),
            "pUnder": bm.get("pUnder"),
            "confidence": prediction.get("confidenceScore"),
            "confidenceLevel": prediction.get("confidenceLevel"),
            "edgeRating": prediction.get("edgeRating"),
            "safetyRating": prediction.get("safetyRating"),
            "historicalRate": prediction.get("propHistoricalRate"),
            "historicalSample": prediction.get("propHistoricalN"),
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
            "lineupStatus": (ti.get("lineup") or {}).get("status") or prediction.get("lineupStatus"),
            "playerPosition": prediction.get("playerPosition"),
            "playerRole": prediction.get("playerRole"),
            "opponentTier": prediction.get("currentOppTier"),
            "moneyline": prediction.get("moneyline"),
            "gameScript": prediction.get("gameScript") or prediction.get("matchScript"),
        },
        "h2h": _h2h_packet(prediction),
        "recentForm": _recent_packet(prediction),
        "opponentProfile": {
            "opponent": opponent_profile.get("opponent"),
            "position": opponent_profile.get("position"),
            "allowedAverage": opponent_profile.get("avgAllowed") or opponent_profile.get("allowedAvg"),
            "sampleSize": opponent_profile.get("sampleSize"),
            "vsPlayerSeasonAvg": opponent_profile.get("vsPlayerSeasonAvg"),
            "isFavorable": opponent_profile.get("isFavorable"),
        },
        "modelSignals": {
            "priorMean": bm.get("priorMean") or prediction.get("priorMean"),
            "momentumMean": bm.get("momentumMean") or prediction.get("momentumMean"),
            "momentumLabel": bm.get("momentumLabel") or prediction.get("momentumLabel"),
            "volatility": bm.get("volatility") or prediction.get("volatility"),
            "priorSamples": bm.get("priorSamples") or prediction.get("priorSamples"),
            "covariateAdjustment": bm.get("covariateAdjustment") or prediction.get("covariateAdjustment"),
            "reversalFlag": bm.get("reversalFlag") or prediction.get("reversalFlag"),
            "ledgerSteps": ledger_steps[-12:] if isinstance(ledger_steps, list) else [],
        },
        "evidenceQuality": {
            "level": quality.get("level") or quality.get("qualityLevel"),
            "score": quality.get("score"),
            "realPlayerLogCount": quality.get("realPlayerLogCount"),
            "capReasons": (quality.get("capReasons") or [])[:5],
        },
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
    recent = packet.get("recentForm") or {}
    opponent = packet.get("opponentProfile") or {}
    signals = packet.get("modelSignals") or {}
    quality = packet.get("evidenceQuality") or {}
    context_bits = packet.get("context") or {}
    prop = str(pick.get("prop") or "prop").replace("_", " ")
    projection = _fmt(pick.get("projection"))
    line = _fmt(pick.get("line"))
    venue = match.get("venue") or "the listed venue"
    paragraphs = [
        (
            f"{player} enters this {prop} matchup on the {venue} side for {matchup}. "
            f"The finalized projection is {projection} against a {line} line, producing a {rec} "
            f"recommendation at {pick.get('confidence') or 'unavailable'}% displayed confidence. "
            f"This is not a recommendation based on one recent box score. The decision is the "
            f"result of the player's baseline, recent form, venue context, opponent profile, "
            f"and the way those signals interact with the posted number. The line is the market "
            f"reference; the projection is the model's estimate of the player's expected workload "
            f"before the match begins."
        ),
        (
            f"The recent sample contains {recent.get('sampleSize') or 0} usable matches, including "
            f"{recent.get('homeCount') or 0} home and {recent.get('awayCount') or 0} away appearances. "
            f"The venue split is important because a player's responsibilities can change when the "
            f"team controls territory, protects a lead, absorbs pressure, or plays through a more "
            f"direct transition plan. The recorded home average is {_fmt(recent.get('homeAverage'))} "
            f"and the away average is {_fmt(recent.get('awayAverage'))}. The recent hit-rate record "
            f"is {json.dumps(recent.get('hitRates') or {}, separators=(',', ':'))}; it is supporting "
            f"context rather than a guarantee. The model also accounts for sample quality, minutes, "
            f"and whether a result represents a normal role rather than a short cameo."
        ),
        (
            f"The tactical environment is anchored by the player's resolved position "
            f"({context_bits.get('playerPosition') or 'unavailable'}) and role "
            f"({context_bits.get('playerRole') or 'unavailable'}). That matters because {prop} "
            f"production is created by repeatable actions: where the player receives the ball, "
            f"whether they are asked to progress or recycle possession, how often they defend in "
            f"their own half, and whether the match script gives them time to complete the relevant "
            f"action. The expected possession split is "
            f"{_fmt(context.get('playerTeamPossession'))}% for the player's team and "
            f"{_fmt(context.get('opponentPossession'))}% for the opponent. The recorded match script "
            f"is {context_bits.get('matchScript') or context_bits.get('gameScript') or 'unavailable'}, "
            f"so no unverified game-state claim is being added."
        ),
        (
            f"The opponent-specific evidence shows an allowed average of {_fmt(opponent.get('allowedAverage'))} "
            f"for the relevant comparison group across {opponent.get('sampleSize') or 'an unavailable'} "
            f"sample. Relative to the player's season baseline, that opponent signal is "
            f"{_fmt(opponent.get('vsPlayerSeasonAvg'))}% different when available, and the model labels "
            f"the matchup as {'favorable' if opponent.get('isFavorable') else 'not favorable' if opponent.get('isFavorable') is False else 'unresolved'}. "
            f"This is the tactical distinction between a generic season average and an opponent-aware "
            f"projection: the same player can have a different workload when the opposing team "
            f"presses aggressively, concedes the relevant zone, closes passing lanes, or forces "
            f"play into a different channel. If the comparison sample is thin, its influence is kept "
            f"small rather than treated as a full scouting truth."
        ),
        (
            f"The direct H2H record contains {h2h.get('playerAppearances') or 0} player appearances "
            f"with an average of {_fmt(h2h.get('playerAverage'))}, while the teams have "
            f"{h2h.get('teamMeetings') or 0} verified meetings in the broader fixture history. "
            f"Player appearances and team-only meetings are kept separate: a match can reveal the "
            f"opponent's possession and tactical shape without proving that this player was on the "
            f"pitch. H2H therefore acts as a matchup-specific adjustment only when the identity, "
            f"fixture, venue, and player participation are verified. Missing H2H is not silently "
            f"converted into a zero or a fabricated neutral average."
        ),
        (
            f"The model's internal signals describe the baseline as {_fmt(signals.get('priorMean'))}, "
            f"recent momentum as {_fmt(signals.get('momentumMean'))} with a "
            f"{signals.get('momentumLabel') or 'unavailable'} label, and volatility as "
            f"{signals.get('volatility') or 'unavailable'}. The evidence uses "
            f"{signals.get('priorSamples') or recent.get('sampleSize') or 0} prior observations, "
            f"with a context adjustment of {_fmt(signals.get('covariateAdjustment'))}. These numbers "
            f"explain why the projection can differ from both the season average and the line. A "
            f"small gap should not be described as a dominant tactical mismatch; conversely, a "
            f"larger gap still needs to survive venue, role, opponent, and evidence-quality checks "
            f"before the recommendation earns strong confidence."
        ),
        (
            f"The evidence-quality status is {quality.get('level') or 'unavailable'} at "
            f"{quality.get('score') or 'unavailable'}/100 with {quality.get('realPlayerLogCount') or recent.get('sampleSize') or 0} "
            f"real player logs. Any confidence cap or limitation is reported as "
            f"{'; '.join(quality.get('capReasons') or packet.get('limitations') or ['no additional limitation recorded'])}. "
            f"The final read is therefore specific but bounded: the model leans {rec} because the "
            f"final projection sits on that side of the line after the deterministic adjustments, "
            f"not because the narrative is trying to manufacture certainty. The most important "
            f"question for this pick is whether the expected venue, role, and match script actually "
            f"appear after kickoff. If they do, the tactical pathway supports the call; if they do "
            f"not, the pre-match projection should be treated as a measured estimate rather than a "
            f"promise."
        ),
        (
            f"There are also practical conditions that would make this read less reliable. A late "
            f"lineup change, an unexpected position, a shortened workload, or a tactical instruction "
            f"that moves {player} away from the relevant zone can reduce the number of opportunities "
            f"available for {prop}. The same is true if the player's team loses the expected "
            f"territorial balance and spends the match defending, or if an early score creates a "
            f"script that is materially different from the pre-match expectation. Those are not "
            f"reasons to rewrite the projection after the fact; they are the boundaries of a "
            f"pre-match estimate. The responsible interpretation is to check whether the observed "
            f"role and venue match the verified inputs, then judge the result against the original "
            f"line rather than against an invented narrative. With that limitation stated clearly, "
            f"the {rec} direction is the deterministic conclusion supported by the available "
            f"evidence, while the confidence level communicates how much of that evidence is "
            f"actually verified."
        ),
    ]
    paragraph = "\n\n".join(paragraphs)
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
        if _longform_usable(text):
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
                    max_output_tokens=1800,
                ),
            ),
            timeout=18,
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
    cache_key = _CACHE_VERSION + "-" + hashlib.sha256(
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
            "Write a detailed customer-facing tactical scouting report of 900-1100 words "
            "about this sports pick. Use only the JSON evidence below. Write 6-9 connected "
            "paragraphs with substantial tactical reasoning, not filler. Explain the exact "
            "matchup, line, projection, recommendation, recent-form pattern, home/away split, "
            "possession environment, resolved position and role, formation or match-script "
            "implications, opponent-specific evidence, H2H evidence, and evidence limitations. "
            "Explain mechanisms: where the player is likely to receive or lose opportunities, "
            "how pressure/territory/game state can change the prop, and why the evidence supports "
            "or limits the final direction. Use exact numbers only when present in the JSON. If a "
            "feed is unavailable, say it is unavailable rather than inventing it. Player H2H and "
            "team-only meetings must remain distinct. Do not use headings, bullets, markdown, "
            "provider names, model names, guarantees, or the word Bayesian. Say Reverse Formula "
            "instead. Do not change the deterministic recommendation.\n\n"
            f"EVIDENCE JSON:\n{json.dumps(packet, separators=(',', ':'), default=str)}"
        )
        text = await _generate(prompt)
        if not _longform_usable(text):
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