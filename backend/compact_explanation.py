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
from knowledge_base import assemble_fact_bundle


_MODEL = os.environ.get("GEMINI_EXPLANATION_MODEL", "gemini-2.5-flash")
_MIN_WORDS = 380
_MAX_WORDS = 600
_CACHE_VERSION = "compact-v3-tactical"
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
    tactical_context = prediction.get("tacticalContext") or {}
    understat = tactical_context.get("understatPressure") or {}
    recent_blocks = tactical_context.get("recentOpponentBlockProfiles") or {}
    recent_block_rows = recent_blocks.get("profiles") if isinstance(recent_blocks, dict) else []
    if not isinstance(recent_block_rows, list):
        recent_block_rows = []
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
            "age": prediction.get("playerAge")
            if prediction.get("playerAge") is not None
            else (prediction.get("player") or {}).get("age"),
            "averageMinutesPerMatch": prediction.get("averageMinutesPerMatch")
            if prediction.get("averageMinutesPerMatch") is not None
            else (prediction.get("playerGameLogs") or {}).get("avgMinutes"),
            "position": player.get("position") or prediction.get("playerPosition"),
            "role": player.get("role") or (ti.get("tacticalContext") or {}).get("role"),
            "roleSource": prediction.get("playerRoleSource")
            or player.get("roleSource")
            or tactical_context.get("roleSource"),
            "roleConfidence": prediction.get("playerRoleConfidence")
            or player.get("roleConfidence")
            or tactical_context.get("roleConfidence"),
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
            "opponentPressure": {
                "status": understat.get("status") or "unavailable",
                "ppda": (understat.get("opponentPress") or {}).get("ppda")
                if isinstance(understat, dict)
                else None,
                "label": (understat.get("opponentPress") or {}).get("label")
                if isinstance(understat, dict)
                else None,
                "source": understat.get("source") if isinstance(understat, dict) else None,
                "reason": understat.get("reason") if isinstance(understat, dict) else None,
            },
            "recentOpponentBlockProfiles": recent_block_rows[:5],
        },
        "h2h": _h2h_packet(prediction),
        "recentForm": _recent_packet(prediction),
        "opponentProfile": {
            "opponent": opponent_profile.get("opponent"),
            "position": opponent_profile.get("position"),
            "allowedAverage": (
                opponent_profile.get("avgAllowed")
                or opponent_profile.get("allowedAvg")
                or opponent_profile.get("allowedAverage")
            ),
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
        "roleEvidence": prediction.get("positionEvidence")
        or prediction.get("roleEvidence")
        or player.get("roleEvidence"),
    }
    return packet


def _fallback(packet: dict[str, Any]) -> str:
    """Deterministic explanation used when AI generation is unavailable or too short."""
    match = packet["match"]
    pick = packet["pick"]
    h2h = packet["h2h"]
    player = match.get("player") or "The player"
    team = match.get("team") or "the team"
    opponent = match.get("opponent") or "the opponent"
    rec = pick.get("recommendation") or "PASS"
    recent = packet.get("recentForm") or {}
    opp_profile = packet.get("opponentProfile") or {}
    signals = packet.get("modelSignals") or {}
    quality = packet.get("evidenceQuality") or {}
    prop = str(pick.get("prop") or "prop").replace("_", " ")
    projection = _fmt(pick.get("projection"))
    line = _fmt(pick.get("line"))
    venue = match.get("venue") or "home"
    conf = pick.get("confidence")
    home_avg = _fmt(recent.get("homeAverage"))
    away_avg = _fmt(recent.get("awayAverage"))
    baseline = _fmt(signals.get("priorMean"))
    momentum = _fmt(signals.get("momentumMean"))
    momentum_label = (signals.get("momentumLabel") or "").lower()
    opp_allowed = _fmt(opp_profile.get("allowedAverage"))
    opp_n = opp_profile.get("sampleSize") or 0
    lims = quality.get("capReasons") or packet.get("limitations") or []

    # Para 1: the pick itself
    p1 = (
        f"The Reverse Formula projects {player} for {projection} {prop} in "
        f"{team} vs {opponent} — {venue} side — against a posted line of {line}. "
        f"That gap is enough to lean {rec}"
        + (f" at {conf}% confidence" if conf else "")
        + ". The projection is built from the player's baseline, recent form weighted "
        "toward the last ten appearances, a venue adjustment, and an opponent-specific "
        "comparison where the sample exists."
    )

    # Para 2: form and venue split
    split_note = ""
    home_n = recent.get("homeCount") or 0
    away_n = recent.get("awayCount") or 0
    if home_avg != "unavailable" and away_avg != "unavailable":
        dominant = "home" if float(home_avg) > float(away_avg) else "away"
        gap = abs(float(home_avg) - float(away_avg))
        split_note = (
            f"{player}'s home average is {home_avg} against an away average of {away_avg} "
            f"across the recent {recent.get('sampleSize') or home_n + away_n}-match sample — "
            f"a {gap:.1f}-point venue gap that favours the {dominant} environment. "
        )
    p2 = (
        split_note
        + f"The baseline across the full season is {baseline}. "
        + (
            f"Recent momentum reads {momentum} ({momentum_label}) — "
            "suggesting the current trajectory is pulling the projection away from that baseline. "
            if momentum != "unavailable" and momentum_label else
            "The momentum and baseline are aligned within normal variance. "
        )
        + f"The model is running on {recent.get('sampleSize') or 0} real game logs with full-minute filtering, "
        "so cameo appearances that would inflate or deflate the average are excluded."
    )

    # Para 3: opponent context
    if opp_allowed != "unavailable" and opp_n:
        opp_direction = "above" if float(opp_allowed) > float(baseline if baseline != "unavailable" else line) else "below"
        p3 = (
            f"Against {opponent} specifically, the comparison group has averaged {opp_allowed} "
            f"{prop} across {opp_n} measured match{'es' if opp_n != 1 else ''} — "
            f"{opp_direction} the player's season baseline. "
            f"That opponent signal {'pulls the projection toward the ' + rec + ' side' if opp_n >= 5 else 'carries limited weight given the thin sample size'}."
        )
    else:
        p3 = (
            f"Opponent-specific evidence for {opponent} against this prop is not available in the "
            "verified sample, so no opponent-allowed adjustment was applied. The projection rests "
            "on the player's own form and venue history."
        )

    # Para 4: H2H and limitations
    h2h_note = ""
    if h2h.get("playerAppearances"):
        h2h_note = (
            f"{player} has {h2h['playerAppearances']} verified appearances against {opponent} "
            f"with an average of {_fmt(h2h.get('playerAverage'))} {prop}. "
        )
    elif h2h.get("teamMeetings"):
        h2h_note = (
            f"The teams have {h2h['teamMeetings']} prior meetings in the record, but no verified "
            f"player appearances for {player}, so the H2H component stays neutral. "
        )
    lim_note = ""
    if lims:
        lim_note = f"Flagged limitations: {'; '.join(str(l) for l in lims[:2])}. "
    p4 = (
        h2h_note
        + lim_note
        + f"The {rec} direction stands as the deterministic conclusion from the Reverse Formula. "
        "It stays valid as long as the pre-match conditions — lineup, role, and game state — "
        "match what was expected when the projection was built."
    )

    return _clean_text("\n\n".join([p1, p2, p3, p4]))


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
                    temperature=0.35,
                    max_output_tokens=900,
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
    force: bool = False,
) -> tuple[str, str, str]:
    """Return ``(text, source, cache_key)`` after the final ledger is locked.

    When ``force=True`` the cache is bypassed and a fresh AI generation is
    attempted regardless of what is already stored.  The new text is written
    back to the cache (overwriting the old entry) so future reads are fast.
    """
    packet = build_evidence_packet(prediction)
    # ── Stage 2: fact-bundle lookup (fast indexed MongoDB reads, no API calls) ─
    _bundle_result: dict = {}
    if str(prediction.get("sport") or "soccer").lower() == "soccer":
        try:
            _bundle_result = await assemble_fact_bundle(
                player_id=int(prediction.get("playerId") or 0),
                team_id=int(
                    prediction.get("teamId") or prediction.get("fixtureTeamId") or 0
                ),
                opponent_id=int(
                    prediction.get("opponentId") or prediction.get("fixtureOpponentId") or 0
                ),
                prop_type=str(prediction.get("propType") or ""),
                venue=str(prediction.get("venue") or "home"),
                league_id=int(prediction.get("leagueId") or 0),
            )
        except Exception as _kb_exc:
            print(f"[COMPACT KB] assemble_fact_bundle failed: {_kb_exc}")
    _fact_bundle_version = _bundle_result.get("version") or "no_bundle"
    raw_identity = {
        "playerId": prediction.get("playerId"),
        "fixtureId": prediction.get("fixtureId"),
        "propType": prediction.get("propType"),
        "line": prediction.get("line"),
        "ledger": ledger_fingerprint,
        "factBundleVersion": _fact_bundle_version,
    }
    cache_key = _CACHE_VERSION + "-" + hashlib.sha256(
        json.dumps(raw_identity, sort_keys=True, default=str).encode()
    ).hexdigest()[:32]
    fallback = _fallback(packet)
    if not force:
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

        match = packet["match"]
        pick = packet["pick"]
        recent = packet.get("recentForm") or {}
        signals = packet.get("modelSignals") or {}
        player_name = match.get("player") or "the player"
        team = match.get("team") or "the team"
        opponent = match.get("opponent") or "the opponent"
        venue = match.get("venue") or "home"
        prop_label = str(pick.get("prop") or "prop").replace("_", " ")
        rec = str(pick.get("recommendation") or "OVER").upper()
        line = pick.get("line")
        proj = pick.get("projection")
        conf = pick.get("confidence")
        home_avg = recent.get("homeAverage")
        away_avg = recent.get("awayAverage")
        baseline = signals.get("priorMean")
        momentum = signals.get("momentumMean")
        momentum_label = signals.get("momentumLabel") or ""
        opp_profile = packet.get("opponentProfile") or {}

        # Build a tight evidence block — anchors Gemini's numbers without
        # dumping the full JSON.  Gemini must use its own tactical knowledge;
        # these are the only specific figures it may cite.
        evidence_lines = [
            f"Player: {player_name}",
            f"Team: {team} ({venue} side) vs Opponent: {opponent}",
            f"Prop: {prop_label} | Line: {_fmt(line)} | Projection: {_fmt(proj)} | Verdict: {rec} ({_fmt(conf)}% confidence)",
            f"Season baseline: {_fmt(baseline)} | Recent momentum: {_fmt(momentum)} ({momentum_label})",
            f"Home average: {_fmt(home_avg)} | Away average: {_fmt(away_avg)} | Sample: {recent.get('sampleSize') or 0} matches",
        ]
        context = packet.get("context") or {}
        evidence_lines.append(
            f"Player profile: age {_fmt(context.get('age'))} | average minutes per match "
            f"{_fmt(context.get('averageMinutesPerMatch'))} | position {context.get('position') or 'unavailable'} "
            f"| role {context.get('role') or 'unavailable'} "
            f"| role source {context.get('roleSource') or 'unavailable'}"
        )
        expected_team_poss = context.get("playerTeamPossession")
        expected_opp_poss = context.get("opponentPossession")
        evidence_lines.append(
            f"Fixture context: expected possession {team} {_fmt(expected_team_poss)}% / "
            f"{opponent} {_fmt(expected_opp_poss)}% | match script {context.get('matchScript') or 'unavailable'}"
        )
        pressure_context = context.get("opponentPressure") or {}
        evidence_lines.append(
            f"Opponent pressure: status {pressure_context.get('status') or 'unavailable'} | "
            f"PPDA {_fmt(pressure_context.get('ppda'))} | "
            f"source {pressure_context.get('source') or 'unavailable'} | "
            f"reason {pressure_context.get('reason') or 'none supplied'}"
        )
        recent_opponent_blocks = context.get("recentOpponentBlockProfiles") or []
        if recent_opponent_blocks:
            evidence_lines.append(
                "Most recent opponent block evidence: "
                + "; ".join(
                    f"{row.get('date') or 'undated'} vs {row.get('opponent') or opponent}: "
                    f"{row.get('blockProfile', {}).get('label') if isinstance(row.get('blockProfile'), dict) else row.get('blockProfile') or 'unavailable'}"
                    for row in recent_opponent_blocks[:5]
                    if isinstance(row, dict)
                )
            )
        if opp_profile.get("allowedAverage") is not None:
            evidence_lines.append(
                f"Opponent allowed average for this prop: {_fmt(opp_profile.get('allowedAverage'))} "
                f"(n={opp_profile.get('sampleSize') or 'thin'})"
            )
        h2h_block = packet.get("h2h") or {}
        if h2h_block.get("playerAppearances"):
            evidence_lines.append(
                f"H2H player appearances vs this opponent: {h2h_block['playerAppearances']}"
                f" (avg {_fmt(h2h_block.get('playerAverage'))})"
            )
        lims = packet.get("limitations") or []
        if lims:
            evidence_lines.append(f"Model limitations noted: {'; '.join(lims)}")
        evidence_block = "\n".join(evidence_lines)

        _bundle_text = _bundle_result.get("text") or ""
        _bundle_hit  = _bundle_result.get("hit", False)
        # Prepend the verified fact bundle when KB had data for this matchup.
        _bundle_section = (
            f"FACT BUNDLE\n{_bundle_text}\n\n" if _bundle_hit and _bundle_text else ""
        )
        # Instruction changes when a fact bundle is present: ground claims in
        # verified facts rather than asking Gemini to use its own knowledge.
        if _bundle_hit:
            _tactic_instruction = (
                f"The FACT BUNDLE above contains verified facts about {player_name}'s "
                f"role and {opponent}'s tactical style. Use these as your primary foundation. "
                f"Combine it with the most recent fixture/opponent evidence in THE PICK block. "
                f"Prefer the most recent dated evidence when sources disagree. You may explain "
                f"broader soccer mechanisms, but all specific claims about this player's "
                f"position/role or the opponent's tactical identity must come from supplied "
                f"evidence — do not invent facts beyond it."
            )
        else:
            _tactic_instruction = (
                f"Use the supplied dated opponent and fixture evidence first, then your "
                f"general soccer knowledge to explain the mechanism. Never turn general "
                f"knowledge into a confirmed current fact about {team} or {opponent}. The "
                f"numbers above are anchors — reference them when they support the argument, "
                f"but build the reasoning from the role and latest available matchup context, "
                f"not from re-reading the stats back to the subscriber."
            )
        prompt = (
            f"You are a soccer prop analyst writing for subscribers of a player prop analytics platform.\n\n"
            f"{_bundle_section}"
            f"THE PICK\n"
            f"{evidence_block}\n\n"
            f"TASK\n"
            f"Write a focused ~500-word tactical explanation (4-5 paragraphs, no headings, no bullets, no markdown). "
            f"{_tactic_instruction}\n\n"
            f"Cover these four things:\n"
            f"1. {player_name}'s actual position and role, and why that role specifically does or does not generate "
            f"high {prop_label} volume — describe the real on-pitch mechanism.\n"
            f"2. How {team}'s tactical shape and {opponent}'s defensive/pressing style interact to affect {player_name}'s "
            f"expected {prop_label} count today ({venue} side).\n"
            f"3. Why the Reverse Formula lands at {_fmt(proj)} against a {_fmt(line)} line and leans {rec}: "
            f"explain the home/away split difference, the {momentum_label.lower() or 'current'} momentum reading, "
            f"and any opponent-allowed evidence — in plain English that connects the numbers to the tactical picture.\n"
            f"4. What would have to happen in-game for this read to be wrong. Be specific to this matchup, "
            f"not generic (don't just list 'red card or substitution').\n\n"
            f"RULES\n"
            f"- Call the player by name ({player_name}) throughout.\n"
            f"- Do NOT say 'Bayesian' — say 'Reverse Formula' instead.\n"
            f"- Do NOT invent statistics. Use only the numbers in THE PICK block for specific figures.\n"
            f"- Treat PPDA as unavailable when its status is unavailable; never infer a number from a pressure label.\n"
            f"- Treat role/source, dated opponent rows, and fixture identity as provenance; do not upgrade inferred evidence to confirmed fact.\n"
            f"- The {rec} recommendation is final — do not argue the other side or hedge the direction.\n"
            f"- No headings, no bullets, no markdown, no provider names, no betting guarantees.\n"
            f"- Write as if talking to a subscriber who follows soccer — skip the basics, go tactical."
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