"""
AI ENGINE — The data backbone powering the ReversePicks prediction system.
Primary AI: Grok-3 (prediction synthesis).
Secondary: Gemini 2.5 Pro/Flash (explanations, web search, tactical chat, OCR).
"""
import json
import hashlib
import httpx
import asyncio
import traceback
from datetime import datetime, timezone, timedelta
from config import db, XAI_API_KEY, GROK_MODEL, GROK_REASONING_MODEL

AI_MODEL = GROK_MODEL
AI_REASONING_MODEL = GROK_REASONING_MODEL
GEMINI_SEARCH_MODEL = "gemini-3"
AI_URL = "https://api.x.ai/v1/chat/completions"

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
GEMINI_PRO = "gemini-2.5-pro"
GEMINI_FLASH = "gemini-2.5-flash"


_GROK_HEADERS = {"Content-Type": "application/json"}


async def _grok_call(
    prompt: str,
    system: str = "",
    temperature: float = 0.0,
    max_tokens: int = 2000,
    timeout: int = 40,
    model: str | None = None,
    json_mode: bool = False,
) -> str:
    """Core Grok (xAI) API call — OpenAI-compatible endpoint."""
    if not XAI_API_KEY:
        return ""
    _model = model or GROK_MODEL

    # ── Daily response cache — same prompt on same day returns instantly ──────
    _today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    _hash_src = (system[:500] + prompt[:1800] + _model + _today).encode()
    _ck = "gk|" + hashlib.md5(_hash_src).hexdigest()
    try:
        _hit = await db.grok_response_cache.find_one({"_k": _ck}, {"_id": 0, "v": 1})
        if _hit and _hit.get("v"):
            print(f"[GROK CACHE HIT] {_model} key={_ck[:20]}")
            return _hit["v"]
    except Exception:
        pass
    # ─────────────────────────────────────────────────────────────────────────

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    payload: dict = {
        "model": _model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    headers = {"Authorization": f"Bearer {XAI_API_KEY}", **_GROK_HEADERS}

    for _attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=10)) as client:
                resp = await client.post(AI_URL, json=payload, headers=headers)
                if resp.status_code == 200:
                    _result = resp.json()["choices"][0]["message"]["content"].strip()
                    if _result:
                        try:
                            await db.grok_response_cache.replace_one(
                                {"_k": _ck},
                                {"_k": _ck, "v": _result, "ts": datetime.now(timezone.utc)},
                                upsert=True,
                            )
                        except Exception:
                            pass
                    return _result
                elif resp.status_code == 429:
                    _wait = 2 ** _attempt
                    print(f"[GROK] Rate-limited (429) — retry {_attempt+1}/3 in {_wait}s")
                    await asyncio.sleep(_wait)
                    continue
                else:
                    print(f"[GROK] API error {resp.status_code}: {resp.text[:200]}")
                    return ""
        except httpx.TimeoutException:
            print(f"[GROK] Timeout ({_model}, {timeout}s)")
            return ""
        except Exception as e:
            print(f"[GROK] Call error: {type(e).__name__}: {e}")
            return ""
    print(f"[GROK] All 3 retry attempts exhausted (rate limit)")
    return ""


async def _grok_search_call(
    prompt: str,
    max_tokens: int = 500,
    timeout: int = 25,
    model: str | None = None,
) -> str:
    """Grok call with live web search grounding (xAI search_parameters)."""
    if not XAI_API_KEY:
        return ""
    _model = model or GROK_MODEL
    payload: dict = {
        "model": _model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": max_tokens,
        "search_parameters": {"mode": "auto"},
    }
    headers = {"Authorization": f"Bearer {XAI_API_KEY}", **_GROK_HEADERS}

    for _attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=10)) as client:
                resp = await client.post(AI_URL, json=payload, headers=headers)
                if resp.status_code == 200:
                    text = resp.json()["choices"][0]["message"]["content"].strip()
                    if text:
                        return text
                    return ""
                elif resp.status_code == 429:
                    _wait = 2 ** _attempt
                    print(f"[GROK SEARCH] Rate-limited (429) — retry {_attempt+1}/3 in {_wait}s")
                    await asyncio.sleep(_wait)
                    continue
                else:
                    print(f"[GROK SEARCH] API error {resp.status_code}: {resp.text[:200]}")
                    return ""
        except httpx.TimeoutException:
            print(f"[GROK SEARCH] Timeout ({timeout}s)")
            return ""
        except Exception as e:
            print(f"[GROK SEARCH] Error: {type(e).__name__}: {e}")
            return ""
    print(f"[GROK SEARCH] All 3 retry attempts exhausted (rate limit)")
    return ""


# Backward-compat aliases — all Gemini calls now route through Grok
async def _gemini_call(
    prompt: str,
    system: str = "",
    temperature: float = 0.0,
    max_tokens: int = 2000,
    timeout: int = 40,
    model: str | None = None,
    json_mode: bool = False,
    thinking_budget: int = 0,  # ignored — kept for call-site compatibility
) -> str:
    return await _grok_call(prompt, system=system, temperature=temperature,
                            max_tokens=max_tokens, timeout=timeout, json_mode=json_mode)


async def _gemini_search_call(
    prompt: str,
    max_tokens: int = 500,
    timeout: int = 25,
    model: str | None = None,
) -> str:
    return await _grok_search_call(prompt, max_tokens=max_tokens, timeout=timeout)


async def _ai_call(prompt: str, system: str = "", temperature: float = 0, max_tokens: int = 2000, timeout: int = 35) -> str:
    """Unified AI call — Grok."""
    return await _grok_call(prompt, system=system, temperature=temperature, max_tokens=max_tokens, timeout=timeout)


_web_intel_cache: dict = {}
_WEB_INTEL_TTL = 600  # 10 minutes — same match context reused across concurrent users


async def fetch_web_intel(
    player_team: str,
    opponent: str,
    match_date: str,
    match_round: str = "",
    league: str = "",
    timeout: int = 20,
) -> str:
    """
    WEB INTELLIGENCE: Fetches real-time match preview data — injuries, suspensions,
    lineup news, tactical shifts, manager quotes.
    Primary: Grok with live web search (xAI search_parameters).
    Fallback: Grok tactical knowledge (no search).
    Results cached 10 min per matchup to avoid redundant calls under concurrent load.
    """
    import time, html as _html
    date_str = match_date[:10] if match_date else ""
    cache_key = f"{player_team}|{opponent}|{date_str}"
    now = time.time()
    cached = _web_intel_cache.get(cache_key)
    if cached:
        ts, result = cached
        if now - ts < _WEB_INTEL_TTL:
            print(f"[WEB INTEL] Cache hit: {player_team} vs {opponent}")
            return result

    context_str = f"{league} — {match_round}" if (league or match_round) else "upcoming match"
    prompt = (
        f"Give me a concise pre-match intelligence briefing (max 200 words) for: "
        f"{player_team} vs {opponent}{f' ({date_str})' if date_str else ''} [{context_str}].\n\n"
        f"Focus ONLY on: (1) confirmed injuries and suspensions for both teams, "
        f"(2) expected lineup or formation changes, (3) manager tactical comments, "
        f"(4) any relevant match context (must-win, rotation, travel fatigue, etc.).\n"
        f"Be factual and specific. Do not make up information. If nothing significant is confirmed, say so briefly."
    )

    # Strategy 1: Grok with live web search
    search_result = await _grok_search_call(prompt, max_tokens=500, timeout=timeout)
    if search_result:
        search_result = _html.unescape(search_result)
        print(f"[WEB INTEL] Grok search: {search_result[:120]}...")
        _web_intel_cache[cache_key] = (now, search_result)
        return search_result

    # Strategy 2: Grok knowledge fallback (no live search)
    knowledge_prompt = (
        f"You are a professional soccer analyst. Provide a concise tactical briefing (max 180 words) for "
        f"{player_team} vs {opponent}"
        f"{f' in the {league}' if league else ''}"
        f"{f' ({match_round})' if match_round else ''}.\n\n"
        f"Cover: (1) each team's typical tactical shape and possession style, "
        f"(2) expected game tempo, (3) which team dominates the ball and through what channels, "
        f"(4) historical head-to-head tendencies. "
        f"Focus on known tactical identities. Be specific and analytical."
    )
    result = await _grok_call(knowledge_prompt, timeout=15, max_tokens=350)
    if result:
        result = _html.unescape(result)
        print(f"[WEB INTEL] Grok knowledge fallback: {result[:120]}...")
        _web_intel_cache[cache_key] = (now, result)
        return result

    _web_intel_cache[cache_key] = (now, "")
    return ""


# Understat covers these 5 major leagues with full PPDA data
_UNDERSTAT_LEAGUE_MAP = {
    "premier league": "EPL",
    "epl": "EPL",
    "english premier league": "EPL",
    "la liga": "La_liga",
    "laliga": "La_liga",
    "spain": "La_liga",
    "bundesliga": "Bundesliga",
    "german bundesliga": "Bundesliga",
    "serie a": "Serie_A",
    "italian serie a": "Serie_A",
    "ligue 1": "Ligue_1",
    "french ligue 1": "Ligue_1",
    "ligue1": "Ligue_1",
}


async def fetch_opponent_ppda(opponent: str, league: str = "", timeout: int = 20) -> float | None:
    """
    Scrape understat.com via Gemini's live web search for the opponent's real PPDA
    (Passes Per Defensive Action) for the current season.

    Understat covers: EPL, La Liga, Bundesliga, Serie A, Ligue 1.
    For all other leagues this returns None immediately (proxy handles them).

    PPDA scale:
      < 6    : Elite press
      6 – 8  : High press
      8 – 11 : Moderate
      11+    : Low press / deep block
    """
    import re as _re
    if not XAI_API_KEY or not opponent:
        return None

    # Only fire for understat-covered major leagues
    league_lower = (league or "").lower()
    understat_code = None
    for key, code in _UNDERSTAT_LEAGUE_MAP.items():
        if key in league_lower:
            understat_code = code
            break

    if not understat_code:
        print(f"[PPDA] League '{league}' not on understat — skipping")
        return None

    understat_url = f"https://understat.com/league/{understat_code}"

    # Strategy 1: Grok with live web search (understat.com)
    search_prompt = (
        f"Go to {understat_url} and look at the team statistics table. "
        f"Find {opponent}'s PPDA (Passes Per Defensive Action) for the current 2025/2026 season. "
        f"PPDA is a pressing intensity metric — lower values mean more aggressive pressing "
        f"(e.g. 6.5 = elite press, 9.0 = moderate, 13+ = low press). "
        f"Reply with ONLY the PPDA number as a decimal (e.g. '7.8'). "
        f"If you cannot find {opponent} on that page or cannot confirm the value, reply with exactly 'unknown'."
    )
    try:
        text = await _gemini_search_call(search_prompt, max_tokens=30, timeout=timeout)
        if text:
            if "unknown" in text.lower():
                print(f"[PPDA] understat: {opponent} not found ({understat_code})")
                return None
            m = _re.search(r'\b(\d{1,2}(?:\.\d{1,2})?)\b', text)
            if m:
                val = float(m.group(1))
                if 3.0 <= val <= 30.0:
                    print(f"[PPDA] Grok search: {opponent} ({understat_code}) PPDA={val}")
                    return val
            print(f"[PPDA] Grok search unparseable: '{text[:60]}'")
    except Exception as e:
        print(f"[PPDA] Grok search exception: {e}")

    # Strategy 2: Grok knowledge fallback
    knowledge_prompt = (
        f"What is {opponent}'s PPDA (Passes Per Defensive Action) in the current or most recent "
        f"{league} season, based on understat.com data? "
        f"PPDA < 6 = elite press, 6-8 = high, 8-11 = moderate, 11+ = low. "
        f"Reply with ONLY a single decimal number. If unsure, reply 'unknown'."
    )
    try:
        text = await _gemini_call(knowledge_prompt, max_tokens=15, timeout=15)
        if text:
            if "unknown" in text.lower():
                print(f"[PPDA] Grok knowledge fallback: {opponent} unknown")
                return None
            m = _re.search(r'\b(\d{1,2}(?:\.\d{1,2})?)\b', text)
            if m:
                val = float(m.group(1))
                if 3.0 <= val <= 30.0:
                    print(f"[PPDA] Grok knowledge fallback: {opponent} PPDA={val}")
                    return val
    except Exception as e:
        print(f"[PPDA] Knowledge fallback exception: {e}")

    return None


# ═══════════════════════════════════════════════════════════════
# AI PRESS INTENSITY — Universal opponent press rating (any league)
# ═══════════════════════════════════════════════════════════════
# Replaces the heuristic compute_press_intensity_score for opponents
# whose press style isn't well captured by raw tackles+interceptions.
# Asks Gemini to rate opponent on 0–1 press scale using web search +
# tactical knowledge, returns a structured score the Bayesian engine
# uses directly (same direction matrix and ±20% caps still apply).
# ═══════════════════════════════════════════════════════════════

# Simple in-memory TTL cache: { (opponent_lower, league_lower): (expires_at, result) }
_PRESS_INTENSITY_CACHE: dict = {}
_PRESS_INTENSITY_LOCKS: dict = {}  # in-flight dedupe: { key: asyncio.Lock }
_PRESS_TTL_SECONDS = 6 * 3600  # 6 hours — press style doesn't change game-to-game


def _label_from_score(s: float) -> str:
    if s >= 0.75:
        return "Elite"
    if s >= 0.50:
        return "High"
    if s >= 0.25:
        return "Moderate"
    return "Low"


async def fetch_ai_press_intensity(
    opponent: str,
    league: str = "",
    season: str = "2025/2026",
    timeout: int = 18,
) -> dict | None:
    """
    Ask Gemini to rate the opponent's pressing intensity on a 0–1 scale.

    Returns dict with:
      score      : 0.0 – 1.0  (0 = deep block, 1 = elite high press)
      label      : "Low" / "Moderate" / "High" / "Elite"
      ppda       : float | None   (if Gemini found/knew it)
      reasoning  : short string from Gemini
      source     : "ai_web" or "ai_knowledge"
    Returns None if AI couldn't produce a confident answer.

    Works for ALL leagues (not just understat-covered five). The heuristic
    `compute_press_intensity_score` stays as a structural fallback.
    """
    import re as _re
    if not XAI_API_KEY or not opponent:
        return None

    cache_key = (opponent.lower().strip(), (league or "").lower().strip(), (season or "").strip())
    now = datetime.now(timezone.utc).timestamp()
    cached = _PRESS_INTENSITY_CACHE.get(cache_key)
    if cached and cached[0] > now:
        return cached[1]

    # In-flight dedupe — concurrent predictions for the same opponent share one Gemini call
    lock = _PRESS_INTENSITY_LOCKS.get(cache_key)
    if lock is None:
        lock = asyncio.Lock()
        _PRESS_INTENSITY_LOCKS[cache_key] = lock

    async with lock:
        # Re-check cache after acquiring the lock — winner of the race populated it
        cached = _PRESS_INTENSITY_CACHE.get(cache_key)
        if cached and cached[0] > now:
            return cached[1]
        return await _fetch_ai_press_intensity_inner(opponent, league, season, timeout, cache_key, now)


async def _fetch_ai_press_intensity_inner(
    opponent: str, league: str, season: str, timeout: int,
    cache_key: tuple, now: float,
) -> dict | None:
    """Inner fetch routine — caller holds the per-key lock."""
    import re as _re
    league_str = league or "football"
    # Reframed as tactical identity (year-stable) — removes the season-specific
    # escape hatch that caused the model to return {"score": null} for all teams.
    prompt = (
        f"You are a tactical football analyst. Based on {opponent}'s well-known tactical identity "
        f"and pressing style in {league_str}, rate their pressing intensity on a STRICT 0.0–1.0 scale.\n\n"
        f"Scale anchors:\n"
        f"  0.00–0.25 LOW     — deep block, passive, drops off (Getafe, Simeone Atletico, Burnley)\n"
        f"  0.25–0.50 MODERATE— mid-block, situational pressing (most mid-table sides)\n"
        f"  0.50–0.75 HIGH    — aggressive, hunts in opponent half (Liverpool, Brighton, Athletic Bilbao)\n"
        f"  0.75–1.00 ELITE   — relentless high press (Bielsa Leeds, Bayer Leverkusen, peak Rayo)\n\n"
        f"Known reference points: Liverpool≈0.72, Manchester City≈0.55, Arsenal≈0.60, "
        f"Chelsea≈0.50, Tottenham≈0.45, Real Madrid≈0.40, Barcelona≈0.50, "
        f"Atletico Madrid≈0.30, Dortmund≈0.60, Bayern Munich≈0.55, "
        f"PSG≈0.45, Marseille≈0.60, Inter Milan≈0.45, Napoli≈0.55.\n\n"
        f"You MUST give a numeric score — do NOT return null. Use the reference points above "
        f"to calibrate. If {opponent} is not listed, pick the score of the most tactically similar team.\n\n"
        f"Reply ONLY with strict JSON, no markdown:\n"
        f'{{"score": <0.0-1.0 float>, "ppda": <float or null>, "reasoning": "<one sentence>"}}'
    )

    # Strategy 1: knowledge call (search deprecated by xAI as of 2026-06)
    parsed = None
    used_source = None
    try:
        txt = await _grok_call(prompt, temperature=0, max_tokens=200, timeout=timeout)
        if txt:
            parsed = _parse_json(txt)
            if parsed and isinstance(parsed, dict) and parsed.get("score") is not None:
                used_source = "ai_knowledge"
    except Exception as e:
        print(f"[AI PRESS] knowledge call exception: {e}")

    # Strategy 2: retry with slightly higher temperature if score came back null
    if not parsed or parsed.get("score") is None:
        try:
            txt = await _grok_call(prompt, temperature=0.3, max_tokens=200, timeout=15)
            if txt:
                parsed = _parse_json(txt)
                if parsed and isinstance(parsed, dict) and parsed.get("score") is not None:
                    used_source = "ai_knowledge_retry"
        except Exception as e:
            print(f"[AI PRESS] retry exception: {e}")

    if not parsed or parsed.get("score") is None:
        print(f"[AI PRESS] No confident assessment for {opponent} ({league})")
        # Cache the negative result briefly so we don't hammer the API on retries
        _PRESS_INTENSITY_CACHE[cache_key] = (now + 600, None)
        return None

    try:
        score = float(parsed.get("score"))
    except (TypeError, ValueError):
        return None
    score = max(0.0, min(1.0, score))
    ppda_raw = parsed.get("ppda")
    try:
        ppda = float(ppda_raw) if ppda_raw is not None else None
    except (TypeError, ValueError):
        ppda = None
    if ppda is not None and not (3.0 <= ppda <= 30.0):
        ppda = None

    # Sanity guard: season-average PPDA below 7.5 is implausible.
    # Real-world historic-elite pressers (Klopp '18/19=7.9, Bielsa Leeds '20/21=7.4) bottom out near 7.5.
    # If Gemini returns sub-7.5, floor to 7.5 (matches the documented score-to-PPDA mapping at the elite tier)
    # and log so we know the model is over-stating.
    ppda_warning = None
    if ppda is not None and ppda < 7.5:
        ppda_warning = f"PPDA {ppda} implausibly low — floored to 7.5"
        print(f"[AI PRESS] {opponent}: {ppda_warning}")
        ppda = 7.5

    result = {
        "score": round(score, 3),
        "label": _label_from_score(score),
        "ppda": ppda,
        "reasoning": str(parsed.get("reasoning", ""))[:300],
        "source": used_source or "ai_knowledge",
    }
    if ppda_warning:
        result["ppda_note"] = ppda_warning
    _PRESS_INTENSITY_CACHE[cache_key] = (now + _PRESS_TTL_SECONDS, result)
    print(f"[AI PRESS] {opponent} ({league}): score={result['score']} label={result['label']} "
          f"ppda={result['ppda']} source={result['source']} — {result['reasoning'][:100]}")
    return result


def _parse_json(raw: str) -> dict | list | None:
    """Parse JSON from Gemini response, stripping markdown wrappers."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
        raw = raw.rsplit("```", 1)[0]
    try:
        return json.loads(raw.strip())
    except (json.JSONDecodeError, ValueError):
        return None


# ═══════════════════════════════════════════════════════════════
# PHASE 1: PRE-PREDICTION DATA DIGEST
# Gemini crunches raw API data into a focused, insight-rich brief
# that feeds GPT-5.2 a shorter, smarter prompt
# ═══════════════════════════════════════════════════════════════

async def build_gemini_digest(
    player_name: str, team_name: str, opponent_name: str,
    prop_type: str, line: float, venue: str,
    player_stats: dict, team_stats: dict, opponent_stats: dict,
    h2h_data: list, match_odds: dict, standings: list,
    player_game_logs: list, team_fixture_stats: list,
    opponent_fixture_stats: list, match_dominance: dict,
    sport: str = "soccer"
) -> str:
    """Build a Gemini-processed data digest. Runs in ~2-3s.
    Returns a compact string of key insights for GPT-5.2."""

    # Build raw data summary for Gemini to analyze
    parts = []

    # Player season stats
    if player_stats:
        pstats = player_stats.get("statistics", [{}])[0] if player_stats.get("statistics") else {}
        games = pstats.get("games", {})
        passes = pstats.get("passes", {})
        shots = pstats.get("shots", {})
        tackles = pstats.get("tackles", {})
        goals_data = pstats.get("goals", {})
        apps = games.get("appearences") or games.get("appearances") or 0
        parts.append(f"SEASON: {apps} apps, rating {games.get('rating','?')}, "
                     f"passes {passes.get('total','?')}/{passes.get('key','?')} key, "
                     f"shots {shots.get('total','?')}/{shots.get('on','?')} on target, "
                     f"tackles {tackles.get('total','?')}, saves {goals_data.get('saves','?')}")

    # Game logs (last N games with the target stat)
    if player_game_logs:
        stat_map = {
            "pass_attempts": "passes_total", "shots": "shots_total",
            "shots_on_target": "shots_on", "tackles": "tackles_total",
            "key_passes": "passes_key", "saves": "goals_saves",
            "interceptions": "tackles_interceptions", "blocks": "tackles_blocks",
            "dribbles": "dribbles_attempts", "goals": "goals_total",
            "assists": "goals_assists", "crosses": "passes_crosses",
            "clearances": "tackles_clearances", "fouls_drawn": "fouls_drawn",
            "shots_assisted": "passes_key", "points": "points",
            "rebounds": "rebounds", "three_pointers_made": "three_pointers_made",
        }
        target_field = stat_map.get(prop_type, "passes_total")
        log_lines = []
        for g in player_game_logs[:12]:
            val = g.get(target_field, g.get("targetStat", "?"))
            log_lines.append(f"{g.get('date','?')[:10]} vs {g.get('opponent','?')} ({g.get('venue','?')}): {val} in {g.get('minutes','?')}min")
        parts.append(f"GAME LOGS ({prop_type}): " + " | ".join(log_lines))

    # Team form
    if team_fixture_stats:
        possessions = [s.get("possession", "").replace("%", "") for s in team_fixture_stats if s.get("possession")]
        avg_poss = sum(float(p) for p in possessions if p) / max(len(possessions), 1) if possessions else 0
        parts.append(f"TEAM FORM ({venue}): avg poss {avg_poss:.0f}%, "
                     f"recent: {', '.join(s.get('score','?') + ' vs ' + s.get('opponent','?') for s in team_fixture_stats[:3])}")

    # Opponent form
    if opponent_fixture_stats:
        opp_venue = "away" if venue == "home" else "home"
        parts.append(f"OPPONENT FORM ({opp_venue}): "
                     f"recent: {', '.join(s.get('score','?') + ' vs ' + s.get('opponent','?') for s in opponent_fixture_stats[:3])}")

    # Odds & dominance
    if match_odds:
        ao = match_odds.get("americanOdds", {})
        if ao:
            parts.append(f"ODDS: Home {ao.get('home','?')} | Draw {ao.get('draw','?')} | Away {ao.get('away','?')} | Fav: {match_odds.get('favorite','?')}")

    if match_dominance.get("notes"):
        parts.append(f"DOMINANCE: poss={match_dominance.get('expectedPoss',50):.0f}%, mult={match_dominance.get('multiplier',1.0)}")

    raw_data = "\n".join(parts)

    prompt = f"""You are a sports analytics data processor. Analyze this raw data and produce a FOCUSED brief.

MATCHUP: {player_name} ({team_name}) {venue.upper()} vs {opponent_name}
PROP: {prop_type} line {line}
SPORT: {sport}

RAW DATA:
{raw_data}

Produce a brief with EXACTLY these sections (keep each to 1-2 sentences max):
1. TREND: Is the player trending up/down/stable for {prop_type}? Cite specific recent numbers.
2. MATCHUP EDGE: How does this specific opponent affect {prop_type}? Do they concede more/fewer?
3. VENUE FACTOR: Any home/away split for this stat?
4. RED FLAGS: Injuries, rotation risk, minute restrictions, or data gaps.
5. KEY NUMBER: The single most important stat for this projection.

Be direct. No hedging. Use numbers, not words like "good" or "bad"."""

    result = await _ai_call(prompt, temperature=0, max_tokens=600, timeout=18)
    return result if result else raw_data  # Fallback to raw data if AI fails


# ═══════════════════════════════════════════════════════════════
# PHASE 2: AUTO-SETTLEMENT BOT
# Background task that checks live scores and auto-settles picks
# ═══════════════════════════════════════════════════════════════

async def auto_settlement_loop():
    """Background loop: check and settle finished games every 15 minutes.
    Each run fires 6+ API calls per unique team in pending picks, so frequent
    runs burn quota fast. 15 min is plenty since picks resolve after the match.
    """
    await asyncio.sleep(5)   # Short delay then run immediately on startup
    print("[GROK ENGINE] Auto-settlement bot started (15 min interval)")

    while True:
        try:
            await _run_auto_settlement()
        except Exception as e:
            print(f"[AUTO-SETTLE] Error: {e}")
        await asyncio.sleep(900)  # Check every 15 minutes — shared BDL key needs breathing room


async def _try_settle_mlb(pick: dict) -> bool:
    """
    Settle an MLB pick using game log data.

    Handles both ID spaces:
      • Stats API IDs (≥ _STATSAPI_ID_THRESHOLD, ~100k) → mlb_client fetches
        from statsapi.mlb.com directly with proper field names.
      • BDL IDs (< _STATSAPI_ID_THRESHOLD) → mlb_client fetches from
        BallDontLie and _transform_bdl_log normalises the field schema to
        Stats-API shape (p_k, hits, rbi, ip, etc.) before caching/returning.
      • Composite props (hitter_fantasy_points, hits_runs_rbis, etc.) are
        detected via _COMPOSITE_HANDLERS and computed from sub-fields.

    Game matching is done by date proximity to pick creation; BDL game IDs
    are unreliable on picks so we never filter by ID.

    Called from _run_auto_settlement() for picks with sport='mlb'.
    Returns True when a settlement was written.
    """
    try:
        import mlb_client
        from mlb_engine import (
            ALL_PROP_FIELDS, PITCHER_PROPS,
            _compute_fantasy_pts, _compute_hits_runs_rbis,
            _compute_pitcher_fantasy, _compute_pitching_outs,
        )
    except ImportError as _ie:
        print(f"[MLB SETTLE] Import error: {_ie}")
        return False

    # Composite props are stored in ALL_PROP_FIELDS as placeholder strings like
    # "__fantasy_pts__".  We detect those and call the real compute function.
    _COMPOSITE_HANDLERS = {
        "__fantasy_pts__":      _compute_fantasy_pts,
        "__hits_runs_rbis__":   _compute_hits_runs_rbis,
        "__pitcher_fantasy__":  _compute_pitcher_fantasy,
        "__pitching_outs__":    _compute_pitching_outs,
    }

    player_id = pick.get("playerId")
    prop_type  = (pick.get("propType") or "").lower()
    line       = pick.get("line")
    rec        = (pick.get("recommendation") or "over").upper()

    if not player_id or not prop_type or line is None:
        return False

    field = ALL_PROP_FIELDS.get(prop_type)
    if not field:
        print(f"[MLB SETTLE] Unknown prop_type={prop_type}, skipping")
        return False

    # Only settle picks that are 4+ hours old (baseball games ~3–4 h)
    pick_created = None
    for ts_key in ("timestamp", "createdAt"):
        raw = pick.get(ts_key)
        if raw:
            try:
                pick_created = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                break
            except Exception:
                pass

    if pick_created:
        hours_old = (datetime.now(timezone.utc) - pick_created).total_seconds() / 3600
        if hours_old < 4:
            return False  # Too early — game might still be in progress

    # Always resolve against the current calendar year
    current_year = datetime.now(timezone.utc).year

    # ── Fetch game logs via Stats API (correct ID space) ──────────────────────
    try:
        logs = await mlb_client.get_player_game_logs(int(player_id), current_year)
    except Exception as _e:
        print(f"[MLB SETTLE] Stats API log fetch failed player={player_id}: {_e}")
        return False

    if not logs:
        print(f"[MLB SETTLE] No game logs for player {player_id} season {current_year}")
        return False

    # ── Match game log by date proximity to pick creation ─────────────────────
    # Pick is created before the game. Find the first game played on pick_date
    # or within 2 days after (handles late-night / next-day situations).
    target_log = None
    if pick_created:
        from datetime import date as _date, timedelta as _td
        target_date = pick_created.date()
        window_end  = target_date + _td(days=2)
        # logs are newest-first — iterate reversed (oldest-first) to find earliest match
        for log in reversed(logs):
            log_date_str = (log.get("date") or "")[:10]
            if not log_date_str:
                continue
            try:
                log_date = _date.fromisoformat(log_date_str)
                if target_date <= log_date <= window_end:
                    target_log = log
                    break
            except Exception:
                pass
        if not target_log:
            # No game found in window — pick might be old; use most recent completed game
            target_log = logs[0]
    else:
        target_log = logs[0]

    # ── Composite props: compute from multiple fields ─────────────────────────
    _composite_fn = _COMPOSITE_HANDLERS.get(field)
    if _composite_fn:
        raw_val = _composite_fn(target_log)
        if raw_val is None:
            print(f"[MLB SETTLE] Composite '{field}' returned None for player {player_id} "
                  f"date={target_log.get('date','?')} — missing sub-fields")
            return False
    else:
        raw_val = target_log.get(field)
        if raw_val is None:
            print(f"[MLB SETTLE] Field '{field}' not in log for player {player_id} "
                  f"date={target_log.get('date','?')} — may be wrong group (hit vs pitch)")
            return False

    try:
        if prop_type == "innings_pitched":
            # Convert "5.2" BDL fractional IP → float outs representation
            parts = str(raw_val).split(".")
            whole = int(parts[0])
            frac  = int(parts[1]) if len(parts) > 1 else 0
            actual: float = whole + frac / 3.0
        else:
            actual = float(raw_val)
    except Exception:
        return False

    # ── DNP guard for pitcher props ───────────────────────────────────────────
    # If a pitcher was scratched / did not appear, BDL returns ip=0 and all
    # counting stats as 0.  Settling an UNDER with actual=0 in that case is a
    # false hit — the player never took the mound.  Detect by checking IP: if
    # IP == 0 and the prop value is also 0, void the pick (push) so it doesn't
    # inflate the hit-rate ledger.
    _PITCHER_PROP_SET = {
        "pitcher_strikeouts", "hits_allowed", "earned_runs",
        "walks_allowed", "pitches_thrown", "batters_faced",
        "pitcher_fantasy_score", "pitching_outs",
    }
    if prop_type in _PITCHER_PROP_SET and actual == 0.0:
        ip_raw = target_log.get("ip")
        if ip_raw is not None:
            try:
                ip_parts = str(ip_raw).split(".")
                ip_float = int(ip_parts[0]) + (int(ip_parts[1]) / 3.0 if len(ip_parts) > 1 else 0)
                if ip_float == 0.0:
                    print(f"[MLB SETTLE] DNP detected for {pick.get('playerName')} {prop_type} "
                          f"(IP=0, stat=0) — voiding as DNP")
                    await db.picks.update_one(
                        {"pickId": pick["pickId"]},
                        {"$set": {
                            "actualValue":  0.0,
                            "result":       "dnp",
                            "hitPct":       0,
                            "status":       "settled",
                            "matchStatus":  "final",
                            "settledAt":    datetime.now(timezone.utc).isoformat(),
                            "settledBy":    "mlb_auto_dnp",
                            "voidReason":   "Player did not pitch (IP=0, stat=0)",
                        }},
                    )
                    return True
            except Exception:
                pass

    line_f = float(line)
    if actual == line_f:
        result = "push"
    elif rec == "OVER":
        result = "hit" if actual > line_f else "miss"
    else:
        result = "hit" if actual < line_f else "miss"

    await db.picks.update_one(
        {"pickId": pick["pickId"]},
        {"$set": {
            "actualValue":  round(actual, 1),
            "result":       result,
            "status":       "settled",
            "matchStatus":  "final",
            "settledAt":    datetime.now(timezone.utc).isoformat(),
            "settledBy":    "mlb_auto",
        }},
    )
    print(f"[MLB SETTLE] ✓ {pick.get('playerName')} {prop_type} actual={actual:.2f} line={line_f} rec={rec} → {result}")
    return True


async def _try_settle_bdl(pick: dict, sport: str) -> bool:
    """
    Generic BDL settler for NBA / NFL / NHL / WNBA.
    Fetches game logs from the matching client, finds the game by date,
    reads the prop field, and writes the result to MongoDB.
    """
    try:
        if sport == "nba":
            import nba_client as bdl_client
            import nba_engine as bdl_engine
            PROP_MAP = bdl_engine.NBA_PROPS
            min_hours = 3
        elif sport == "nfl":
            import nfl_client as bdl_client
            import nfl_engine as bdl_engine
            PROP_MAP = bdl_engine.NFL_PROPS
            min_hours = 5
        elif sport == "nhl":
            import nhl_client as bdl_client
            import nhl_engine as bdl_engine
            PROP_MAP = bdl_engine.NHL_PROPS
            min_hours = 3
        elif sport == "wnba":
            import wnba_client as bdl_client
            import wnba_engine as bdl_engine
            PROP_MAP = bdl_engine.WNBA_PROPS
            min_hours = 3
        else:
            return False
    except ImportError as e:
        print(f"[{sport.upper()} SETTLE] Import error: {e}")
        return False

    player_id = pick.get("playerId")
    prop_type = (pick.get("propType") or "").lower()
    line      = pick.get("line")
    rec       = (pick.get("recommendation") or "over").upper()

    if not player_id or not prop_type or line is None:
        return False

    field = PROP_MAP.get(prop_type)
    if not field:
        return False

    # Age gate
    pick_created = None
    for ts_key in ("timestamp", "createdAt"):
        raw = pick.get(ts_key)
        if raw:
            try:
                pick_created = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                break
            except Exception:
                pass

    if pick_created:
        hours_old = (datetime.now(timezone.utc) - pick_created).total_seconds() / 3600
        if hours_old < min_hours:
            return False

    # Fetch game logs
    current_year = datetime.now(timezone.utc).year
    try:
        if sport == "nhl":
            season = f"{current_year - 1}{current_year}"
            logs = await bdl_client.get_player_game_logs(int(player_id), season)
        elif sport == "wnba":
            logs = await bdl_client.get_player_game_logs(int(player_id), current_year)
        else:
            logs = await bdl_client.get_player_game_logs(int(player_id), current_year)
    except Exception as e:
        print(f"[{sport.upper()} SETTLE] Log fetch failed player={player_id}: {e}")
        return False

    if not logs:
        return False

    # Match game by date
    target_log = None
    if pick_created:
        from datetime import date as _date, timedelta as _td
        target_date = pick_created.date()
        window_end  = target_date + _td(days=2)
        for log in reversed(logs):
            log_date_str = (log.get("date") or "")[:10]
            if not log_date_str:
                continue
            try:
                log_date = _date.fromisoformat(log_date_str)
                if target_date <= log_date <= window_end:
                    target_log = log
                    break
            except Exception:
                pass
        if not target_log:
            target_log = logs[0]
    else:
        target_log = logs[0]

    raw_val = target_log.get(field)
    if raw_val is None:
        return False

    try:
        actual = float(raw_val)
    except Exception:
        return False

    line_f = float(line)
    if actual == line_f:
        result = "push"
    elif rec == "OVER":
        result = "hit" if actual > line_f else "miss"
    else:
        result = "hit" if actual < line_f else "miss"

    await db.picks.update_one(
        {"pickId": pick["pickId"]},
        {"$set": {
            "actualValue":  round(actual, 2),
            "result":       result,
            "status":       "settled",
            "matchStatus":  "final",
            "settledAt":    datetime.now(timezone.utc).isoformat(),
            "settledBy":    f"{sport}_auto",
        }},
    )
    print(f"[{sport.upper()} SETTLE] ✓ {pick.get('playerName')} {prop_type} actual={actual:.2f} line={line_f} rec={rec} → {result}")
    return True


async def _run_auto_settlement():
    """Check all live picks and settle any finished games."""
    from utils import api_football_request, is_quota_exhausted
    from config import CURRENT_SEASON

    if is_quota_exhausted():
        return  # Don't burn quota on settlement checks when there's nothing left

    # Settle "live" picks AND soccer "pending" picks older than 90 min (match duration).
    # MLB pending picks are intentionally excluded from the timestamp-cutoff path —
    # an MLB game can be scheduled 8+ hours after the pick is saved, so 90 min would
    # fire settlement long before the first pitch.  MLB picks only enter here once the
    # live loop promotes them to "live" status (gameId confirmed, in-progress or final).
    _MLB_PENDING_PROPS = {
        "pitcher_strikeouts", "innings_pitched", "hits_allowed", "earned_runs",
        "walks_allowed", "pitches_thrown", "batters_faced",
        "hits", "home_runs", "rbi", "walks", "strikeouts", "runs",
        "total_bases", "stolen_bases", "doubles", "plate_appearances",
        "hitter_fantasy_points", "hits_runs_rbis",
        "pitcher_fantasy_score", "pitching_outs",
    }
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=90)).isoformat()
    live_picks = await db.picks.find(
        {"$or": [
            {"status": "live"},
            # Soccer pending: 90-min cutoff is appropriate (match is over)
            {"status": "pending", "sport": {"$ne": "mlb"},
             "propType": {"$nin": list(_MLB_PENDING_PROPS)}, "timestamp": {"$lt": cutoff}},
            {"status": "pending", "sport": {"$ne": "mlb"},
             "propType": {"$nin": list(_MLB_PENDING_PROPS)}, "createdAt": {"$lt": cutoff}},
        ]},
        {"_id": 0}
    ).to_list(300)
    if not live_picks:
        return

    settled_count = 0

    # ── MLB settlement ────────────────────────────────────────────────────────
    # Detect by sport field OR by prop type (catches picks saved before sport-fix)
    _MLB_PROP_TYPES = {
        "pitcher_strikeouts", "innings_pitched", "hits_allowed", "earned_runs",
        "walks_allowed", "pitches_thrown", "batters_faced",
        "hits", "home_runs", "rbi", "walks", "strikeouts", "runs",
        "total_bases", "stolen_bases", "doubles", "plate_appearances",
        "hitter_fantasy_points", "hits_runs_rbis",
        "pitcher_fantasy_score", "pitching_outs",
    }
    mlb_picks    = [p for p in live_picks if p.get("sport") == "mlb" or p.get("propType", "") in _MLB_PROP_TYPES]
    # Detect CS2 picks by sport field OR propType (catches picks saved before
    # the sport-field repair was deployed — same logic as picks.py repair block).
    _CS2_PROP_PREFIXES = ("map1_", "maps_1_2_", "map3_")
    cs2_picks = [
        p for p in live_picks
        if p.get("sport") == "cs2"
        or str(p.get("propType", "")).startswith(_CS2_PROP_PREFIXES)
    ]
    # WTA picks by sport field
    wta_picks  = [p for p in live_picks if p.get("sport") == "wta"]
    # BDL sports
    nba_picks  = [p for p in live_picks if p.get("sport") == "nba"]
    nfl_picks  = [p for p in live_picks if p.get("sport") == "nfl"]
    nhl_picks  = [p for p in live_picks if p.get("sport") == "nhl"]
    wnba_picks = [p for p in live_picks if p.get("sport") == "wnba"]
    _bdl_picks = set(id(p) for p in nba_picks + nfl_picks + nhl_picks + wnba_picks)
    # Re-partition: remove cs2/wta/bdl from mlb and soccer pools
    mlb_picks    = [p for p in mlb_picks if p not in cs2_picks and p not in wta_picks and id(p) not in _bdl_picks]
    soccer_picks = [p for p in live_picks if p not in mlb_picks and p not in cs2_picks and p not in wta_picks and id(p) not in _bdl_picks]

    for pick in mlb_picks:
        try:
            settled = await _try_settle_mlb(pick)
            if settled:
                settled_count += 1
        except Exception as _me:
            print(f"[MLB SETTLE] Error: {_me}")
            continue
        await asyncio.sleep(2.0)  # pace BDL calls — shared key across all sport clients

    # ── NBA / NFL / NHL / WNBA settlement ────────────────────────────────────
    for _sport, _picks in [("nba", nba_picks), ("nfl", nfl_picks), ("nhl", nhl_picks), ("wnba", wnba_picks)]:
        for pick in _picks:
            try:
                settled = await _try_settle_bdl(pick, _sport)
                if settled:
                    settled_count += 1
            except Exception as _be:
                print(f"[{_sport.upper()} SETTLE] Error: {_be}")
                continue

    # ── Soccer settlement ─────────────────────────────────────────────────────
    if soccer_picks:
        team_ids = list(set(p.get("teamId", 0) for p in soccer_picks if p.get("teamId")))
        for tid in team_ids:
            try:
                today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
                next_s = CURRENT_SEASON + 1  # MLS and other calendar-year leagues use next year label

                # Also cover dates of the oldest pending pick for this team
                # so picks from 3+ days ago don't fall out of the "last 3" window
                oldest_pick = min(
                    (p for p in soccer_picks if p.get("teamId") == tid),
                    key=lambda p: p.get("timestamp") or p.get("createdAt") or "",
                    default=None
                )
                pick_dates = []
                if oldest_pick:
                    for tf in ("timestamp", "createdAt"):
                        raw = oldest_pick.get(tf)
                        if raw:
                            try:
                                pd = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                                pick_dates.append(pd.strftime("%Y-%m-%d"))
                                pick_dates.append((pd - timedelta(days=1)).strftime("%Y-%m-%d"))
                            except Exception:
                                pass
                            break

                # Use last:5 for each season (2 calls instead of 6).
                # last:5 covers ~2-3 weeks of matches which is enough to settle any pending pick.
                # Date-specific calls only added for picks older than yesterday to handle edge cases.
                date_fix_calls = []
                for pd in set(pick_dates):
                    if pd not in (today, yesterday):
                        date_fix_calls.append(api_football_request("fixtures", {"team": tid, "date": pd, "season": CURRENT_SEASON}))

                _fx_from = (datetime.now(timezone.utc) - timedelta(days=90)).strftime("%Y-%m-%d")
                _fx_to   = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                settle_batches = await asyncio.gather(
                    api_football_request("fixtures", {"team": tid, "from": _fx_from, "to": _fx_to, "season": CURRENT_SEASON}),
                    api_football_request("fixtures", {"team": tid, "from": _fx_from, "to": _fx_to, "season": next_s}),
                    *date_fix_calls,
                    return_exceptions=True
                )

                all_fixtures = []
                seen = set()
                for batch in settle_batches:
                    if isinstance(batch, Exception) or not batch:
                        continue
                    for f in batch:
                        fid = f.get("fixture", {}).get("id")
                        if fid and fid not in seen:
                            seen.add(fid)
                            all_fixtures.append(f)

                team_picks = [p for p in soccer_picks if p.get("teamId") == tid]
                for pick in team_picks:
                    result = await _try_settle_soccer(pick, all_fixtures)
                    if result:
                        settled_count += 1
                    else:
                        # Inline orphan-void: pick >48h, no opponent info → will never settle
                        _pick_ts_str = pick.get("timestamp") or pick.get("createdAt") or ""
                        try:
                            _pick_ts_dt = datetime.fromisoformat(str(_pick_ts_str).replace("Z", "+00:00"))
                            _pick_age_h = (datetime.now(timezone.utc) - _pick_ts_dt).total_seconds() / 3600
                        except Exception:
                            _pick_age_h = 0
                        _has_opp = bool(pick.get("opponentId") or pick.get("opponentName"))
                        if _pick_age_h >= 48 and not _has_opp:
                            _now_iso_sv = datetime.now(timezone.utc).isoformat()
                            await db.picks.update_one(
                                {"pickId": pick["pickId"]},
                                {"$set": {"status":"settled","result":"push","hitPct":50,
                                          "settledAt":_now_iso_sv,"settledBy":"stale_void_orphan",
                                          "voidReason":"No opponent info on pick — cannot match fixture, voided as push"}},
                            )
                            settled_count += 1
                            print(f"[ORPHAN-VOID] soccer {pick.get('playerName','?')} {pick.get('propType','?')} (no opponent)")
                            continue
                    if not result and (pick.get("leagueId") == 1 or pick.get("wcMode")):
                        # World Cup picks: API has no per-player stats → fall back to Gemini web search
                        wc_result = await _try_settle_wc_via_gemini(pick)
                        if wc_result:
                            settled_count += 1
            except Exception:
                continue

        # Also handle picks saved without teamId — look up team by name
        orphan_picks = [p for p in soccer_picks if not p.get("teamId") and p.get("teamName")]
        if orphan_picks:
            unique_team_names = list(set(p.get("teamName", "") for p in orphan_picks))
            for team_name in unique_team_names:
                if not team_name:
                    continue
                try:
                    teams_resp = await api_football_request("teams", {"search": team_name[:30]})
                    if not teams_resp:
                        continue
                    tid = teams_resp[0].get("team", {}).get("id")
                    if not tid:
                        continue

                    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
                    next_s = CURRENT_SEASON + 1
                    _ofx_from = (datetime.now(timezone.utc) - timedelta(days=90)).strftime("%Y-%m-%d")
                    _ofx_to   = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                    orphan_batches = await asyncio.gather(
                        api_football_request("fixtures", {"team": tid, "from": _ofx_from, "to": _ofx_to, "season": CURRENT_SEASON}),
                        api_football_request("fixtures", {"team": tid, "from": _ofx_from, "to": _ofx_to, "season": next_s}),
                        return_exceptions=True
                    )
                    all_fixtures = []
                    seen = set()
                    for batch in orphan_batches:
                        if isinstance(batch, Exception) or not batch:
                            continue
                        for f in batch:
                            fid = f.get("fixture", {}).get("id")
                            if fid and fid not in seen:
                                seen.add(fid)
                                all_fixtures.append(f)

                    picks_for_team = [p for p in orphan_picks if p.get("teamName") == team_name]
                    for pick in picks_for_team:
                        await db.picks.update_one(
                            {"pickId": pick["pickId"]},
                            {"$set": {"teamId": tid}}
                        )
                        pick["teamId"] = tid
                        result = await _try_settle_soccer(pick, all_fixtures)
                        if result:
                            settled_count += 1
                except Exception:
                    continue

    # ── CS2 background settlement ──────────────────────────────────────────────
    # Settle CS2 picks that have been pending/live for > 30 min.  Uses the same
    # BDL cache layer as the on-demand path so the 15-min cron run only costs
    # a handful of API calls (all subsequent hits are served from cache).
    if cs2_picks:
        import cs2_client as _cs2_client_ge
        cs2_settle_cutoff = datetime.now(timezone.utc) - timedelta(minutes=30)

        for pick in cs2_picks:
            team_id   = pick.get("teamId")
            player_id = pick.get("playerId")
            opp_name  = pick.get("opponentName", "")
            prop_type = pick.get("propType", "maps_1_2_kills")
            line      = pick.get("line", 0)
            rec       = pick.get("recommendation", "over")
            pick_id   = pick.get("pickId", "")
            email     = pick.get("email", "")

            if not team_id or not player_id:
                continue
            # opp_name may be empty — cs2_client will fall back to the most
            # recent finished match for the team when opponent_name is blank.

            # Skip picks saved in the last 30 min — match can't be over yet
            # Parse pick timestamp (may be Unix-ms int OR ISO string)
            pick_ts = None
            for tf in ("timestamp", "createdAt"):
                raw_ts = pick.get(tf)
                if not raw_ts:
                    continue
                try:
                    if isinstance(raw_ts, (int, float)) and raw_ts > 1_000_000_000:
                        # Unix milliseconds (common for CS2 picks saved from mobile)
                        pick_ts = datetime.fromtimestamp(raw_ts / 1000, tz=timezone.utc)
                    elif isinstance(raw_ts, datetime):
                        pick_ts = raw_ts if raw_ts.tzinfo else raw_ts.replace(tzinfo=timezone.utc)
                    else:
                        pick_ts = datetime.fromisoformat(str(raw_ts).replace("Z", "+00:00"))
                        if pick_ts.tzinfo is None:
                            pick_ts = pick_ts.replace(tzinfo=timezone.utc)
                    break
                except Exception:
                    continue

            # Skip picks saved in the last 30 min — match can't be over yet
            if pick_ts and pick_ts > cs2_settle_cutoff:
                continue

            ts_iso = pick.get("timestamp") or pick.get("createdAt", "")
            if isinstance(ts_iso, (int, float)) and ts_iso > 0:
                ts_iso = datetime.fromtimestamp(ts_iso / 1000, tz=timezone.utc).isoformat()

            try:
                result = await _cs2_client_ge.get_cs2_completed_match_result(
                    team_id=int(team_id),
                    player_id=int(player_id),
                    opponent_name=opp_name,
                    prop_type=prop_type,
                    after_iso=str(ts_iso),
                )
            except Exception as _ce:
                print(f"[CS2 AUTO-SETTLE] error for {pick.get('playerName','?')}: {_ce}")
                continue

            if not result or result.get("actualValue") is None:
                now_iso = datetime.now(timezone.utc).isoformat()
                pname = pick.get("playerName", "?")

                # Player DNP — finished match but player not in any map stats
                if result and result.get("playerDNP"):
                    void_reason = "Player did not appear in match stats (DNP)"
                    await db.picks.update_one(
                        {"pickId": pick_id, "email": email},
                        {"$set": {"status": "settled", "result": "dnp", "hitPct": 0,
                                  "settledAt": now_iso, "sport": "cs2",
                                  "matchScore": result.get("matchScore"),
                                  "voidReason": void_reason}},
                    )
                    settled_count += 1
                    print(f"[CS2 AUTO-SETTLE] DNP push: {pname} — {void_reason}")
                    continue

                # Map 3 wasn't played (match went 2-0 or 0-2)
                if result and result.get("noMap3"):
                    void_reason = f"Map 3 not played ({result.get('mapsPlayed', '?')} maps total) — voided as DNP"
                    await db.picks.update_one(
                        {"pickId": pick_id, "email": email},
                        {"$set": {"status": "settled", "result": "dnp", "hitPct": 0,
                                  "settledAt": now_iso, "sport": "cs2",
                                  "matchScore": result.get("matchScore"),
                                  "voidReason": void_reason}},
                    )
                    settled_count += 1
                    print(f"[CS2 AUTO-SETTLE] No-map3 push: {pname} — {void_reason}")
                    continue

                # Stale-void: if pick is > 7 days old with no data, DNP it so it never hangs forever
                if pick_ts and (datetime.now(timezone.utc) - pick_ts).days >= 7:
                    await db.picks.update_one(
                        {"pickId": pick_id, "email": email},
                        {"$set": {"status": "settled", "result": "dnp", "hitPct": 0,
                                  "settledAt": now_iso, "sport": "cs2",
                                  "voidReason": "No match data found after 7 days — voided as DNP"}},
                    )
                    settled_count += 1
                    print(f"[CS2 AUTO-SETTLE] Stale-void push: {pname} (7d+ no data)")
                continue

            actual_value = result["actualValue"]
            # Determine hit/miss/push
            _diff = actual_value - float(line)
            if abs(_diff) < 0.001:
                result_str = "push"
            elif rec == "over":
                result_str = "hit" if actual_value > float(line) else "miss"
            else:
                result_str = "hit" if actual_value < float(line) else "miss"

            hit_pct   = 100 if result_str == "hit" else (0 if result_str == "miss" else 50)
            now_iso   = datetime.now(timezone.utc).isoformat()
            settle_set = {
                "status":      "settled",
                "result":      result_str,
                "actualValue": actual_value,
                "hitPct":      hit_pct,
                "matchScore":  result.get("matchScore"),
                "settledAt":   now_iso,
                "settledBy":   "auto_cs2",
                "sport":       "cs2",
            }
            try:
                current = await db.picks.find_one({"pickId": pick_id, "email": email}, {"_id": 0, "status": 1, "sport": 1})
                if current and current.get("status") == "settled" and current.get("sport") == "cs2":
                    continue
                await db.picks.update_one(
                    {"pickId": pick_id, "email": email},
                    {"$set": settle_set},
                )
                settled_count += 1
                print(
                    f"[CS2 AUTO-SETTLE] {pick.get('playerName','?')} {prop_type} "
                    f"actual={actual_value} line={line} → {result_str}"
                )
                # ── In-app notification ──────────────────────────────────────
                try:
                    from routes.notifications import create_notification
                    _emoji = "✅" if result_str == "hit" else ("❌" if result_str == "miss" else "↔️")
                    _prop  = prop_type.replace("_", " ").title()
                    _label = "HIT" if result_str == "hit" else ("MISSED" if result_str == "miss" else "PUSH")
                    await create_notification(
                        email=email,
                        ntype="pick_settled",
                        title=f"{_emoji} {pick.get('playerName','?')} {_prop} — {_label}",
                        body=f"Actual: {actual_value} · Line: {line} · {rec.upper()}",
                        data={
                            "pickId":         pick_id,
                            "playerName":     pick.get("playerName"),
                            "propType":       prop_type,
                            "result":         result_str,
                            "actualValue":    actual_value,
                            "line":           line,
                            "recommendation": rec,
                            "sport":          "cs2",
                        },
                    )
                except Exception as _ne:
                    print(f"[CS2 AUTO-SETTLE] notification error: {_ne}")
            except Exception as _ue:
                print(f"[CS2 AUTO-SETTLE] DB write error: {_ue}")

    # ── WTA background settlement ───────────────────────────────────────────────
    # Settle WTA picks that have been live/pending for > 90 min (match duration).
    if wta_picks:
        import wta_client as _wta_client_ge
        wta_settle_cutoff = datetime.now(timezone.utc) - timedelta(minutes=90)

        for pick in wta_picks:
            player_id     = pick.get("playerId")
            opponent_id   = pick.get("opponentId")
            opponent_name = pick.get("opponentName", "")
            prop_type     = pick.get("propType", "total_games")
            line          = pick.get("line", 0)
            rec           = pick.get("recommendation", "over")
            pick_id       = pick.get("pickId", "")
            email         = pick.get("email", "")

            if not player_id or (not opponent_id and not opponent_name):
                # If pick is >48h old with no opponent info it will never settle → void now
                _orphan_ts = None
                for _tf in ("timestamp", "createdAt"):
                    _raw = pick.get(_tf)
                    if not _raw:
                        continue
                    try:
                        if isinstance(_raw, (int, float)) and _raw > 1_000_000_000:
                            _orphan_ts = datetime.fromtimestamp(_raw / 1000, tz=timezone.utc)
                        else:
                            _orphan_ts = datetime.fromisoformat(str(_raw).replace("Z", "+00:00"))
                            if _orphan_ts.tzinfo is None:
                                _orphan_ts = _orphan_ts.replace(tzinfo=timezone.utc)
                        break
                    except Exception:
                        pass
                if _orphan_ts and (datetime.now(timezone.utc) - _orphan_ts).total_seconds() >= 172800:
                    _now_iso_wta_sv = datetime.now(timezone.utc).isoformat()
                    await db.picks.update_one(
                        {"pickId": pick_id, "email": email},
                        {"$set": {"status":"settled","result":"push","hitPct":50,
                                  "settledAt":_now_iso_wta_sv,"settledBy":"stale_void_orphan",
                                  "voidReason":"No opponent info stored — WTA pick cannot be settled, voided as push"}},
                    )
                    settled_count += 1
                    print(f"[WTA ORPHAN-VOID] {pick.get('playerName','?')} — no opponent info")
                continue

            # Skip picks saved in the last 90 min — match can't be over yet
            pick_ts = None
            for tf in ("timestamp", "createdAt"):
                raw_ts = pick.get(tf)
                if not raw_ts:
                    continue
                try:
                    if isinstance(raw_ts, (int, float)) and raw_ts > 1_000_000_000:
                        pick_ts = datetime.fromtimestamp(raw_ts / 1000, tz=timezone.utc)
                    else:
                        pick_ts = datetime.fromisoformat(str(raw_ts).replace("Z", "+00:00"))
                        if pick_ts.tzinfo is None:
                            pick_ts = pick_ts.replace(tzinfo=timezone.utc)
                    break
                except Exception:
                    continue

            if pick_ts and pick_ts > wta_settle_cutoff:
                continue

            ts_iso = pick.get("timestamp") or pick.get("createdAt", "")
            if isinstance(ts_iso, (int, float)) and ts_iso > 0:
                ts_iso = datetime.fromtimestamp(ts_iso / 1000, tz=timezone.utc).isoformat()

            try:
                result = await _wta_client_ge.get_wta_completed_match_result(
                    player_id=int(player_id),
                    opponent_id=int(opponent_id) if opponent_id else None,
                    opponent_name=opponent_name,
                    prop_type=prop_type,
                    after_iso=str(ts_iso),
                )
            except Exception as _we:
                print(f"[WTA AUTO-SETTLE] error for {pick.get('playerName','?')}: {_we}")
                continue

            if not result or result.get("actualValue") is None:
                # Stale-void: WTA matches are weekly so allow 14 days
                if pick_ts and (datetime.now(timezone.utc) - pick_ts).days >= 14:
                    now_iso = datetime.now(timezone.utc).isoformat()
                    await db.picks.update_one(
                        {"pickId": pick_id, "email": email},
                        {"$set": {"status": "settled", "result": "push", "hitPct": 50,
                                  "settledAt": now_iso, "sport": "wta",
                                  "voidReason": "No match data found after 14 days — voided as push"}},
                    )
                    settled_count += 1
                    print(f"[WTA AUTO-SETTLE] Stale-void push: {pick.get('playerName','?')} (14d+ no data)")
                continue

            actual_value = result["actualValue"]
            _diff = actual_value - float(line)
            if abs(_diff) < 0.001:
                result_str = "push"
            elif rec.lower() == "over":
                result_str = "hit" if actual_value > float(line) else "miss"
            else:
                result_str = "hit" if actual_value < float(line) else "miss"

            hit_pct  = 100 if result_str == "hit" else (0 if result_str == "miss" else 50)
            now_iso  = datetime.now(timezone.utc).isoformat()
            settle_set = {
                "status":      "settled",
                "result":      result_str,
                "actualValue": actual_value,
                "hitPct":      hit_pct,
                "matchScore":  result.get("matchScore"),
                "settledAt":   now_iso,
                "sport":       "wta",
            }
            try:
                current = await db.picks.find_one({"pickId": pick_id, "email": email}, {"_id": 0, "status": 1})
                if current and current.get("status") == "settled":
                    continue
                await db.picks.update_one(
                    {"pickId": pick_id, "email": email},
                    {"$set": settle_set},
                )
                settled_count += 1
                print(
                    f"[WTA AUTO-SETTLE] {pick.get('playerName','?')} {prop_type} "
                    f"actual={actual_value} line={line} → {result_str}"
                )
                try:
                    from routes.notifications import create_notification
                    _emoji = "✅" if result_str == "hit" else ("❌" if result_str == "miss" else "↔️")
                    _prop  = prop_type.replace("_", " ").title()
                    _label = "HIT" if result_str == "hit" else ("MISSED" if result_str == "miss" else "PUSH")
                    await create_notification(
                        email=email,
                        ntype="pick_settled",
                        title=f"{_emoji} {pick.get('playerName','?')} {_prop} — {_label}",
                        body=f"Actual: {actual_value} · Line: {line} · {rec.upper()}",
                        data={
                            "pickId":         pick_id,
                            "playerName":     pick.get("playerName"),
                            "propType":       prop_type,
                            "result":         result_str,
                            "actualValue":    actual_value,
                            "line":           line,
                            "recommendation": rec,
                            "sport":          "wta",
                        },
                    )
                except Exception as _ne:
                    print(f"[WTA AUTO-SETTLE] notification error: {_ne}")
            except Exception as _ue:
                print(f"[WTA AUTO-SETTLE] DB write error: {_ue}")

    # ── Global stale-void: void picks that can never settle ────────────────────
    # Soccer: matches end in 90 min; any pick >4d old that hasn't settled is
    #         orphaned (opponent matched wrong window, league not supported, etc.)
    # WTA:    14-day per-pick limit in loop above; 4d global backstop here catches
    #         any that slipped through opponentId=None guard.
    # CS2:    7-day per-pick limit in loop above; 4d global backstop here too.
    # MLB:    Excluded — the live-loop's stale-final escape handles those.
    # A pick with no sport field is assumed soccer.
    try:
        _now_sv = datetime.now(timezone.utc)
        _cutoff_4d = (_now_sv - timedelta(days=4)).isoformat()
        _stale_candidates = await db.picks.find(
            {"status": {"$in": ["pending", "live"]},
             "sport": {"$nin": ["mlb"]},
             "$or": [
                 {"timestamp": {"$lt": _cutoff_4d}},
                 {"createdAt":  {"$lt": _cutoff_4d}},
             ]},
            {"_id": 0, "pickId": 1, "playerName": 1, "propType": 1,
             "sport": 1, "timestamp": 1, "createdAt": 1}
        ).to_list(500)

        _sv_count = 0
        for _sp in _stale_candidates:
            try:
                _sport = _sp.get("sport") or "soccer"
                await db.picks.update_one(
                    {"pickId": _sp["pickId"]},
                    {"$set": {
                        "result":      "push",
                        "status":      "settled",
                        "matchStatus": "final",
                        "settledAt":   _now_sv.isoformat(),
                        "settledBy":   "stale_void",
                        "voidReason":  f"No data found after 7+ days ({_sport}) — voided as push",
                    }},
                )
                _sv_count += 1
                print(f"[STALE-VOID] {_sp.get('playerName','?')} {_sp.get('propType','?')} ({_sport}) → push")
            except Exception:
                pass
        if _sv_count:
            settled_count += _sv_count
            print(f"[STALE-VOID] Voided {_sv_count} stale picks as push")
    except Exception as _sve:
        print(f"[STALE-VOID] Error: {_sve}")

    if settled_count > 0:
        print(f"[AUTO-SETTLE] Settled {settled_count} picks")


async def _try_settle_wc_via_gemini(pick: dict) -> bool:
    """
    Settle a World Cup pick.

    Strategy:
      1. Fetch all finished WC 2026 fixtures directly (league=1, season=2026).
         The primary settlement path fails because WC picks store the player's
         CLUB teamId, so `fixtures?team={club_id}&season=2026` returns club
         matches, not WC fixtures. We bypass that by querying the WC league
         directly and matching by opponent name + pick date.
      2. Call fixtures/players for the matched WC fixture to get real stats.
      3. Fall back to a knowledge-only Grok call (no live search — xAI live
         search was deprecated and returns HTTP 410).
    """
    from utils import api_football_request, strip_accents
    import re as _re

    player_name = pick.get("playerName", "")
    prop_type   = pick.get("propType", "")
    line        = pick.get("line", 0)
    pick_id     = pick.get("pickId", "")
    team_name   = pick.get("teamName", "")
    opp_name    = pick.get("opponentName", "")
    player_id   = pick.get("playerId", 0)
    rec         = pick.get("recommendation", "over")

    if not player_name or not prop_type or not pick_id:
        return False

    _PROP_LABELS = {
        "pass_attempts": "passes attempted",
        "passes": "passes completed",
        "shots": "shots",
        "shots_on_target": "shots on target",
        "saves": "saves",
        "goalie_saves": "goalkeeper saves",
        "tackles": "tackles",
        "key_passes": "key passes",
        "goals": "goals",
        "assists": "assists",
        "crosses": "crosses",
        "interceptions": "interceptions",
        "clearances": "clearances",
        "yellow_cards": "yellow cards",
        "minutes": "minutes played",
    }
    prop_label = _PROP_LABELS.get(prop_type, prop_type.replace("_", " "))
    match_desc = f"{team_name} vs {opp_name}" if opp_name else f"{team_name} match"

    # ── Stage 1: API-Football WC 2026 fixtures ────────────────────────────────
    try:
        from config import STAT_LAMBDA_MAP
        stat_fn = STAT_LAMBDA_MAP.get(prop_type)

        # Parse pick creation time for date-window matching
        pick_created_at = None
        for ts_field in ("timestamp", "createdAt"):
            raw_ts = pick.get(ts_field)
            if raw_ts:
                try:
                    pick_created_at = datetime.fromisoformat(str(raw_ts).replace("Z", "+00:00"))
                    break
                except Exception:
                    pass

        # Fetch all finished WC 2026 fixtures (league_id=1 in API-Football = World Cup)
        wc_fixtures = await api_football_request("fixtures", {
            "league": 1, "season": 2026, "status": "FT"
        }) or []

        # Match fixture by opponent name + created-after guard
        opp_lower = strip_accents((opp_name or "").lower().strip())
        matched_fid = None
        for fx in wc_fixtures:
            fix_date = fx.get("fixture", {}).get("date", "")
            if pick_created_at and fix_date:
                try:
                    fix_dt = datetime.fromisoformat(fix_date.replace("Z", "+00:00"))
                    if fix_dt < (pick_created_at - timedelta(hours=3)):
                        continue
                except Exception:
                    pass
            if not opp_lower:
                continue
            home_n = strip_accents(fx.get("teams", {}).get("home", {}).get("name", "").lower())
            away_n = strip_accents(fx.get("teams", {}).get("away", {}).get("name", "").lower())
            if any([
                opp_lower in home_n, opp_lower in away_n,
                home_n in opp_lower, away_n in opp_lower,
            ]):
                matched_fid = fx.get("fixture", {}).get("id")
                break

        if matched_fid and stat_fn:
            players_data = await api_football_request("fixtures/players", {"fixture": matched_fid}) or []
            player_name_key = player_name.lower().strip()
            actual_value = None
            minutes_played = None
            for team_data in players_data:
                for p in team_data.get("players", []):
                    pid = p.get("player", {}).get("id")
                    api_nm = strip_accents((p.get("player", {}).get("name") or "").lower())
                    name_hit = player_name_key in api_nm or api_nm in player_name_key
                    if pid == player_id or (not player_id and name_hit):
                        stats = p.get("statistics", [{}])[0]
                        minutes_played = stats.get("games", {}).get("minutes") or 0
                        actual_value = stat_fn(stats)
                        break
                if actual_value is not None:
                    break

            if actual_value is not None:
                if minutes_played is not None and minutes_played < 30:
                    void_set = {
                        "status": "settled", "result": "push",
                        "actualValue": actual_value, "minutesPlayed": minutes_played,
                        "settledAt": datetime.now(timezone.utc).isoformat(),
                        "settledBy": "wc_api", "wcSettled": True,
                        "voidReason": f"Player only played {minutes_played} min (min 30 required)",
                    }
                    await db.picks.update_one({"pickId": pick_id}, {"$set": void_set})
                    print(f"[WC SETTLE] {player_name}/{prop_type} → VOID/PUSH ({minutes_played} min)")
                    return True
                result = "win" if (
                    (rec == "over" and actual_value > line) or
                    (rec == "under" and actual_value < line)
                ) else "loss"
                await db.picks.update_one(
                    {"pickId": pick_id},
                    {"$set": {
                        "status": "settled", "result": result,
                        "actualValue": actual_value,
                        "settledAt": datetime.now(timezone.utc).isoformat(),
                        "settledBy": "wc_api", "wcSettled": True,
                    }}
                )
                print(f"[WC SETTLE API] {player_name}/{prop_type} fid={matched_fid} actual={actual_value} → {result.upper()}")
                return True
            else:
                print(f"[WC SETTLE] {player_name}/{prop_type}: fixture {matched_fid} found but no player stats in API-Football")
        else:
            if not matched_fid:
                print(f"[WC SETTLE] {player_name}/{prop_type}: no finished WC fixture matched opponent='{opp_name}'")
    except Exception as _api_err:
        print(f"[WC SETTLE] API-Football stage error: {_api_err}")

    # ── Stage 2: knowledge-only Grok (no live search — xAI search deprecated) ─
    try:
        full_prompt = (
            "You are a sports stats lookup assistant. "
            "Answer ONLY with a single number (the stat value) and nothing else. "
            "Do not include any explanation, units, or words.\n\n"
            f"What were {player_name}'s exact {prop_label} stats in the World Cup 2026 "
            f"{match_desc} match? Reply with only the integer or decimal number."
        )
        raw = (await _grok_call(full_prompt, max_tokens=20, timeout=20) or "").strip()
        nums = _re.findall(r"\d+(?:\.\d+)?", raw)
        if not nums:
            print(f"[WC SETTLE] {player_name}/{prop_type}: AI returned no number — '{raw[:80]}'")
            return False

        actual_value = float(nums[0])
        result = "win" if (
            (rec == "over" and actual_value > line) or
            (rec == "under" and actual_value < line)
        ) else "loss"
        await db.picks.update_one(
            {"pickId": pick_id},
            {"$set": {
                "status": "settled", "result": result,
                "actualValue": actual_value,
                "settledAt": datetime.now(timezone.utc).isoformat(),
                "settledBy": "wc_ai", "wcSettled": True,
            }}
        )
        print(f"[WC SETTLE AI] {player_name}/{prop_type} line={line} actual={actual_value} → {result.upper()}")
        return True

    except Exception as e:
        print(f"[WC SETTLE] {player_name}/{prop_type} error: {e}")
        return False


async def _try_settle_soccer(pick: dict, fixtures: list) -> bool:
    """Try to settle a single soccer pick from available fixtures."""
    from utils import api_football_request, strip_accents

    opponent = pick.get("opponentName", "")
    prop_type = pick.get("propType", "")
    player_id = pick.get("playerId", 0)
    player_name_key = pick.get("playerName", "").lower().strip()

    # Parse pick creation time for timestamp guard
    pick_created_at = None
    for ts_field in ("timestamp", "createdAt", "settledAt"):
        raw_ts = pick.get(ts_field)
        if raw_ts:
            try:
                pick_created_at = datetime.fromisoformat(str(raw_ts).replace("Z", "+00:00"))
                break
            except Exception:
                pass

    opponent_id = pick.get("opponentId", 0)

    # Find matching finished fixture — prefer opponentId match, fall back to name match
    matched = None
    for f in fixtures:
        status = f.get("fixture", {}).get("status", {}).get("short", "")
        if status not in ("FT", "AET", "PEN"):
            continue
        # Timestamp guard: fixture must have ended after the pick was saved.
        # Allow picks saved up to 3 hours after kickoff (user may save mid-match).
        # Only skip fixtures that kicked off MORE than 3 hours before the pick.
        fix_date = f.get("fixture", {}).get("date", "")
        if fix_date and pick_created_at:
            try:
                fix_dt = datetime.fromisoformat(fix_date.replace("Z", "+00:00"))
                if fix_dt < (pick_created_at - timedelta(hours=3)):
                    continue  # This game kicked off well before pick was made — skip
            except Exception:
                pass

        home_id = f.get("teams", {}).get("home", {}).get("id", 0)
        away_id = f.get("teams", {}).get("away", {}).get("id", 0)
        home_name = f.get("teams", {}).get("home", {}).get("name", "")
        away_name = f.get("teams", {}).get("away", {}).get("name", "")

        # Primary: match by opponentId (most reliable — immune to name abbreviations)
        if opponent_id and (home_id == opponent_id or away_id == opponent_id):
            matched = f
            break

        # Fallback: fuzzy name match (handles partial names like "Sporting KC" vs "Sporting Kansas City")
        if opponent:
            # Resolve common team abbreviations to canonical names
            _TEAM_ALIASES = {
                "lafc": "los angeles fc",
                "la galaxy": "los angeles galaxy",
                "nycfc": "new york city fc",
                "nyrb": "new york red bulls",
                "red bulls": "new york red bulls",
                "sporting kc": "sporting kansas city",
                "inter miami": "inter miami cf",
                "atl utd": "atlanta united",
                "dc united": "d.c. united",
                "cf montreal": "cf montreal",
                "ne revolution": "new england revolution",
                "psg": "paris saint-germain",
                "man city": "manchester city",
                "man utd": "manchester united",
                "spurs": "tottenham hotspur",
                "bvb": "borussia dortmund",
                "mgladbach": "borussia monchengladbach",
                "m'gladbach": "borussia monchengladbach",
                "hertha": "hertha berlin",
                "sociedad": "real sociedad",
                "betis": "real betis",
            }
            opp_raw = strip_accents(opponent.lower().strip())
            opp_lower = _TEAM_ALIASES.get(opp_raw, opp_raw)
            home_lower = strip_accents(home_name.lower())
            away_lower = strip_accents(away_name.lower())
            # Also resolve home/away canonical names through alias map (reverse lookup)
            home_resolved = _TEAM_ALIASES.get(home_lower, home_lower)
            away_resolved = _TEAM_ALIASES.get(away_lower, away_lower)
            # Substring both ways (try both raw and resolved)
            name_hit = any([
                opp_lower in home_lower, opp_lower in away_lower,
                home_lower in opp_lower, away_lower in opp_lower,
                opp_raw in home_lower, opp_raw in away_lower,
                home_lower in opp_raw, away_lower in opp_raw,
            ])
            # Also check first word match (e.g. "Sporting" in "Sporting Kansas City")
            if not name_hit:
                opp_words = set(opp_lower.split())
                home_words = set(home_lower.split())
                away_words = set(away_lower.split())
                stopwords = {"fc", "cf", "sc", "ac", "united", "city", "the", "de", "1.", "sv", "vfb"}
                home_shared = (opp_words & home_words) - stopwords
                away_shared = (opp_words & away_words) - stopwords
                name_hit = len(home_shared) >= 2 or len(away_shared) >= 2
            if name_hit:
                matched = f
                break

    if not matched:
        return False

    fid = matched.get("fixture", {}).get("id")
    if not fid:
        return False

    # Get player stats from the fixture
    try:
        players_data = await api_football_request("fixtures/players", {"fixture": fid})
        if not players_data:
            return False

        actual_value = None
        minutes_played = None
        from config import STAT_LAMBDA_MAP
        stat_fn = STAT_LAMBDA_MAP.get(prop_type)

        for team_data in players_data:
            for p in team_data.get("players", []):
                pid = p.get("player", {}).get("id")
                api_name = strip_accents((p.get("player", {}).get("name") or "").lower())
                name_match = player_name_key and (
                    player_name_key in api_name or api_name in player_name_key
                )
                if pid == player_id or (not player_id and name_match):
                    stats = p.get("statistics", [{}])[0]
                    minutes_played = stats.get("games", {}).get("minutes") or 0
                    if stat_fn:
                        actual_value = stat_fn(stats)
                    if actual_value is not None and not player_id and pid:
                        await db.picks.update_one(
                            {"pickId": pick["pickId"]},
                            {"$set": {"playerId": pid}}
                        )
                    break
            if actual_value is not None or minutes_played is not None:
                break

        if actual_value is None:
            return False

        # Minimum minutes threshold — if player played < 30 min, void as push
        # (benched, injured off, or DNP effectively — not enough data to fairly grade)
        MIN_MINUTES = 30
        if minutes_played is not None and minutes_played < MIN_MINUTES:
            home_goals = matched.get("goals", {}).get("home", 0) or 0
            away_goals = matched.get("goals", {}).get("away", 0) or 0
            _venue = (pick.get("venue") or "home").lower()
            _player_goals = home_goals if _venue == "home" else away_goals
            _opp_goals    = away_goals if _venue == "home" else home_goals
            home_team_name = matched.get("teams", {}).get("home", {}).get("name", "") or ""
            away_team_name = matched.get("teams", {}).get("away", {}).get("name", "") or ""
            home_team_id   = matched.get("teams", {}).get("home", {}).get("id")
            away_team_id   = matched.get("teams", {}).get("away", {}).get("id")
            try:
                from routes.picks import _fetch_fixture_possession
                home_poss, away_poss = await _fetch_fixture_possession(fid, home_team_id, away_team_id)
            except Exception:
                home_poss, away_poss = None, None
            try:
                from game_script_engine import bucket_from_final_score
                _scen_bucket = bucket_from_final_score(home_goals, away_goals)
            except Exception:
                _scen_bucket = None
            _push_set = {
                "status": "settled",
                "result": "push",
                "actualValue": actual_value,
                "minutesPlayed": minutes_played,
                "matchScore": f"{_player_goals}-{_opp_goals}",
                "finalHomeGoals": home_goals,
                "finalAwayGoals": away_goals,
                "homeTeam": home_team_name,
                "awayTeam": away_team_name,
                "scenarioBucket": _scen_bucket,
                "settledAt": datetime.now(timezone.utc).isoformat(),
                "settledBy": "auto_soccer",
                "voidReason": f"Player only played {minutes_played} min (min {MIN_MINUTES} required)",
            }
            if home_poss is not None:
                _push_set["homePoss"] = home_poss
            if away_poss is not None:
                _push_set["awayPoss"] = away_poss
            await db.picks.update_one(
                {"pickId": pick["pickId"]},
                {"$set": _push_set}
            )
            print(f"[AUTO-SETTLE] {pick.get('playerName','')} {prop_type} → VOID/PUSH (only {minutes_played} min played)")
            return True

        # Determine result
        line = pick.get("line", 0)
        rec = pick.get("recommendation", "over")
        if actual_value > line:
            result = "hit" if rec == "over" else "miss"
        elif actual_value < line:
            result = "hit" if rec == "under" else "miss"
        else:
            result = "push"

        home_goals = matched.get("goals", {}).get("home", 0) or 0
        away_goals = matched.get("goals", {}).get("away", 0) or 0
        _venue = (pick.get("venue") or "home").lower()
        _player_goals = home_goals if _venue == "home" else away_goals
        _opp_goals    = away_goals if _venue == "home" else home_goals
        home_team_name = matched.get("teams", {}).get("home", {}).get("name", "") or ""
        away_team_name = matched.get("teams", {}).get("away", {}).get("name", "") or ""
        home_team_id   = matched.get("teams", {}).get("home", {}).get("id")
        away_team_id   = matched.get("teams", {}).get("away", {}).get("id")
        try:
            from routes.picks import _fetch_fixture_possession
            home_poss, away_poss = await _fetch_fixture_possession(fid, home_team_id, away_team_id)
        except Exception:
            home_poss, away_poss = None, None

        try:
            from game_script_engine import bucket_from_final_score
            _scen_bucket = bucket_from_final_score(home_goals, away_goals)
        except Exception:
            _scen_bucket = None
        _settle_set = {
            "status": "settled",
            "result": result,
            "actualValue": actual_value,
            "minutesPlayed": minutes_played,
            "matchScore": f"{_player_goals}-{_opp_goals}",
            "finalHomeGoals": home_goals,
            "finalAwayGoals": away_goals,
            "homeTeam": home_team_name,
            "awayTeam": away_team_name,
            "scenarioBucket": _scen_bucket,
            "settledAt": datetime.now(timezone.utc).isoformat(),
            "settledBy": "auto_soccer",
        }
        if home_poss is not None:
            _settle_set["homePoss"] = home_poss
        if away_poss is not None:
            _settle_set["awayPoss"] = away_poss
        await db.picks.update_one(
            {"pickId": pick["pickId"]},
            {"$set": _settle_set}
        )
        print(f"[AUTO-SETTLE] {pick.get('playerName','')} {prop_type} {line} → actual {actual_value} ({minutes_played}min) = {result}")
        return True
    except Exception as e:
        print(f"[AUTO-SETTLE] Error settling {pick.get('playerName','')}: {e}")
        return False


# ═══════════════════════════════════════════════════════════════
# PHASE 3: PRE-GAME AUTO-SCOUT
# Pre-fetches tomorrow's matchup data into cache
# ═══════════════════════════════════════════════════════════════

async def auto_scout_loop():
    """Background loop: pre-fetch data for upcoming games every 6 hours."""
    await asyncio.sleep(60)  # Wait for caches
    print("[GROK ENGINE] Auto-scout started")

    while True:
        try:
            from utils import is_quota_exhausted
            if is_quota_exhausted():
                print("[AUTO-SCOUT] Quota exhausted — skipping run, will retry in 6h")
            else:
                await _run_auto_scout()
        except Exception as e:
            print(f"[AUTO-SCOUT] Error: {e}")
        await asyncio.sleep(43200)  # Every 12 hours


async def _run_auto_scout():
    """Pre-fetch fixture data for the next 24 hours."""
    from utils import api_football_request

    tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Fetch fixtures for key leagues
    key_leagues = [39, 140, 135, 78, 61, 71, 253, 2, 3]  # PL, La Liga, Serie A, Bundesliga, Ligue 1, etc.
    total_cached = 0

    for league_id in key_leagues:
        try:
            fixtures = await api_football_request("fixtures", {"league": league_id, "date": today})
            if not fixtures:
                fixtures = await api_football_request("fixtures", {"league": league_id, "date": tomorrow})
            if not fixtures:
                continue

            for f in fixtures[:10]:
                fid = f.get("fixture", {}).get("id")
                status = f.get("fixture", {}).get("status", {}).get("short", "")
                if not fid or status in ("FT", "AET", "PEN"):
                    continue

                home_id = f.get("teams", {}).get("home", {}).get("id")
                away_id = f.get("teams", {}).get("away", {}).get("id")

                # Pre-cache team recent fixtures
                for tid in [home_id, away_id]:
                    if not tid:
                        continue
                    cache_key = f"scout_team_{tid}"
                    cached = await db.fixture_player_cache.find_one({"_k": cache_key}, {"_id": 0})
                    if cached:
                        continue  # Already scouted

                    _sc_from = (datetime.now(timezone.utc) - timedelta(days=180)).strftime("%Y-%m-%d")
                    _sc_to   = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                    recent = await api_football_request("fixtures", {"team": tid, "from": _sc_from, "to": _sc_to})
                    if recent:
                        recent = sorted(recent, key=lambda f: f.get("fixture", {}).get("date", ""), reverse=True)[:10]
                    if recent:
                        await db.fixture_player_cache.update_one(
                            {"_k": cache_key},
                            {"$set": {"_k": cache_key, "_ts": datetime.now(timezone.utc), "d": [r.get("fixture", {}).get("id") for r in recent]}},
                            upsert=True
                        )
                        total_cached += 1
                        await asyncio.sleep(0.5)  # Rate limit

        except Exception:
            continue

    if total_cached > 0:
        print(f"[AUTO-SCOUT] Pre-cached data for {total_cached} teams")


# ═══════════════════════════════════════════════════════════════
# PHASE 4: INTEL PATTERN MINING
# Gemini analyzes historical picks to find calibration insights
# ═══════════════════════════════════════════════════════════════

async def pattern_mining_loop():
    """Background loop: analyze settled picks for patterns daily."""
    await asyncio.sleep(300)  # Wait 5 min for startup
    print("[GROK ENGINE] Pattern mining started")

    while True:
        try:
            await _run_pattern_mining()
        except Exception as e:
            print(f"[PATTERN MINE] Error: {e}")
        await asyncio.sleep(86400)  # Daily


async def _run_pattern_mining():
    """Analyze all settled picks and extract calibration patterns."""
    # Get all settled picks
    picks = await db.picks.find(
        {"status": "settled", "result": {"$in": ["hit", "miss"]}},
        {"_id": 0, "propType": 1, "position": 1, "venue": 1, "result": 1,
         "recommendation": 1, "confidenceScore": 1, "line": 1,
         "projectedValue": 1, "actualValue": 1, "sport": 1, "leagueId": 1}
    ).to_list(5000)

    if len(picks) < 20:
        print("[PATTERN MINE] Not enough data (<20 picks), skipping")
        return

    # Build summary stats
    by_prop = {}
    by_venue = {"home": {"hit": 0, "miss": 0}, "away": {"hit": 0, "miss": 0}}
    by_rec = {"over": {"hit": 0, "miss": 0}, "under": {"hit": 0, "miss": 0}}
    by_conf = {"high": {"hit": 0, "miss": 0}, "medium": {"hit": 0, "miss": 0}, "low": {"hit": 0, "miss": 0}}
    errors = []

    for p in picks:
        pt = p.get("propType", "unknown")
        res = p.get("result")
        venue = p.get("venue", "unknown")
        rec = p.get("recommendation", "unknown")
        conf = p.get("confidenceScore", 50)

        if pt not in by_prop:
            by_prop[pt] = {"hit": 0, "miss": 0}
        by_prop[pt][res] += 1

        if venue in by_venue:
            by_venue[venue][res] += 1

        if rec in by_rec:
            by_rec[rec][res] += 1

        conf_level = "high" if conf >= 65 else "low" if conf < 50 else "medium"
        by_conf[conf_level][res] += 1

        proj = p.get("projectedValue")
        actual = p.get("actualValue")
        if proj and actual:
            errors.append(round(actual - proj, 1))

    # Build analysis summary
    summary_lines = ["PICK ANALYSIS SUMMARY:"]
    summary_lines.append(f"Total: {len(picks)} picks")

    summary_lines.append("\nBY PROP TYPE:")
    for pt, counts in sorted(by_prop.items(), key=lambda x: x[1]["hit"] + x[1]["miss"], reverse=True):
        total = counts["hit"] + counts["miss"]
        rate = counts["hit"] / total * 100 if total > 0 else 0
        summary_lines.append(f"  {pt}: {rate:.0f}% ({counts['hit']}/{total})")

    summary_lines.append("\nBY VENUE:")
    for v, counts in by_venue.items():
        total = counts["hit"] + counts["miss"]
        rate = counts["hit"] / total * 100 if total > 0 else 0
        summary_lines.append(f"  {v}: {rate:.0f}% ({counts['hit']}/{total})")

    summary_lines.append("\nBY DIRECTION:")
    for r, counts in by_rec.items():
        total = counts["hit"] + counts["miss"]
        rate = counts["hit"] / total * 100 if total > 0 else 0
        summary_lines.append(f"  {r}: {rate:.0f}% ({counts['hit']}/{total})")

    summary_lines.append("\nBY CONFIDENCE:")
    for c, counts in by_conf.items():
        total = counts["hit"] + counts["miss"]
        rate = counts["hit"] / total * 100 if total > 0 else 0
        summary_lines.append(f"  {c} (n={total}): {rate:.0f}%")

    if errors:
        avg_err = sum(errors) / len(errors)
        summary_lines.append(f"\nAVG ERROR: {avg_err:+.1f} (positive = model under-projects)")

    data_text = "\n".join(summary_lines)

    # Ask Gemini to find actionable patterns
    prompt = f"""Analyze this sports prediction model's performance data. Find the 5 most actionable calibration rules.

{data_text}

For each pattern, give:
1. The pattern (specific, with numbers)
2. The recommended adjustment (e.g., "increase projection by 5%", "flip under to over when confidence < 50%")
3. Expected impact

Return JSON: [{{"pattern":"...","adjustment":"...","impact":"..."}}]
Only JSON, no markdown."""

    result = await _gemini_call(prompt, temperature=0, max_tokens=1000, timeout=15)
    insights = _parse_json(result)

    if insights:
        await db.calibration_insights.update_one(
            {"type": "pattern_mining"},
            {"$set": {
                "type": "pattern_mining",
                "insights": insights,
                "raw_stats": {"by_prop": by_prop, "by_venue": by_venue, "by_rec": by_rec, "by_conf": by_conf},
                "pick_count": len(picks),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }},
            upsert=True
        )
        print(f"[PATTERN MINE] Stored {len(insights)} insights from {len(picks)} picks")
    else:
        print(f"[PATTERN MINE] Gemini returned no parseable insights")


# ═══════════════════════════════════════════════════════════════
# PHASE 5: SMART SCAN (Gemini Vision for OCR)
# ═══════════════════════════════════════════════════════════════

_SCAN_PROMPTS = {
    "soccer": """Extract the FIRST player prop bet from this soccer/football image. Focus on the top-left or most prominent player card.

Extract:
- Player name (exact spelling from image)
- Team name (the team shown on the player's card/badge, NOT the opponent)
- Prop type (use EXACTLY one of: pass_attempts, shots, shots_on_target, tackles, key_passes, saves, interceptions, blocks, dribbles, goals, assists, fouls_drawn, crosses, clearances)
- Line/number (the over/under value)
- Opponent name (the "vs" team)
- League name (if visible, e.g., Champions League, La Liga, Premier League)

Return ONLY a valid JSON object (not an array):
{"playerName":"","propType":"","line":0,"teamName":"","opponentName":"","leagueName":""}""",

    "mlb": """Extract the FIRST player prop bet from this MLB baseball image. Focus on the most prominent player card.

Extract:
- Player name (exact spelling from image — batter or pitcher)
- Team name (the player's team, NOT the opponent)
- Prop type (use EXACTLY one of: hits, home_runs, rbi, runs, walks, strikeouts, total_bases, stolen_bases, hits_runs_rbis, hitter_fantasy_points, pitcher_strikeouts, innings_pitched, earned_runs, hits_allowed, walks_allowed, pitches_thrown, pitcher_fantasy_score, plate_appearances)
- Line/number (the over/under value, e.g., 1.5, 6.5, 0.5)
- Opponent team name (the opposing team)

Return ONLY a valid JSON object (not an array):
{"playerName":"","propType":"","line":0,"teamName":"","opponentName":""}""",

    "cs2": """Extract the FIRST player prop bet from this CS2/Counter-Strike esports image. Focus on the most prominent player card.

Extract:
- Player nickname/handle (exact spelling — e.g., "s1mple", "ZywOo", "NiKo")
- Team name (the player's team/org, NOT the opponent — e.g., "NAVI", "Vitality", "FaZe")
- Prop type (use EXACTLY one of: maps_1_2_kills, maps_1_2_headshots, map1_kills, map3_kills)
  - If the image shows "kills" over multiple maps → maps_1_2_kills
  - If it shows "headshots" → maps_1_2_headshots
  - If it shows kills for a single map → map1_kills
- Line/number (the over/under value, e.g., 32.5, 45.5)
- Opponent team name

Return ONLY a valid JSON object (not an array):
{"playerName":"","propType":"","line":0,"teamName":"","opponentName":""}""",

    "wta": """Extract the FIRST player prop bet from this WTA tennis image. Focus on the most prominent player card.

Extract:
- Player name (exact spelling from image)
- Prop type (use EXACTLY one of: total_games, player_games_won, games_won_by_player)
  - If the image shows total games in the match → total_games
  - If it shows games won by a specific player → player_games_won
- Line/number (the over/under value, e.g., 20.5, 8.5)
- Opponent player name (who they are playing against)

Return ONLY a valid JSON object (not an array):
{"playerName":"","propType":"","line":0,"teamName":"","opponentName":""}""",
}


async def gemini_scan_prop(image_base64: str, sport: str = "soccer") -> dict:
    """Extract prop details from a screenshot using Grok vision (grok-2-vision-1212).
    Returns: {"playerName": "...", "propType": "...", "line": 0, "teamName": "...", "opponentName": "...", "leagueName": "..."}"""

    prompt = _SCAN_PROMPTS.get(sport.lower(), _SCAN_PROMPTS["soccer"])

    def _normalize(result: dict) -> dict:
        if "teamName" in result and "playerTeam" not in result:
            result["playerTeam"] = result.pop("teamName")
        return result

    if XAI_API_KEY:
        try:
            payload = {
                "model": "grok-2-vision-1212",
                "messages": [{"role": "user", "content": [
                    {"type": "image_url", "image_url": {
                        "url": f"data:image/png;base64,{image_base64}",
                        "detail": "high",
                    }},
                    {"type": "text", "text": prompt},
                ]}],
                "temperature": 0,
                "max_tokens": 256,
            }
            headers = {"Authorization": f"Bearer {XAI_API_KEY}", **_GROK_HEADERS}
            async with httpx.AsyncClient(timeout=25) as client:
                resp = await client.post(AI_URL, json=payload, headers=headers)
                if resp.status_code == 200:
                    content = resp.json()["choices"][0]["message"]["content"].strip()
                    result = _parse_json(content)
                    if result:
                        if isinstance(result, list) and len(result) > 0:
                            result = result[0]
                        if isinstance(result, dict):
                            result = _normalize(result)
                            print(f"[SCAN:{sport}] Grok vision: {result.get('playerName','')} {result.get('propType','')} {result.get('line','')}")
                            return result
                else:
                    print(f"[SCAN:{sport}] Grok vision error: {resp.status_code} — {resp.text[:300]}")
        except Exception as e:
            print(f"[SCAN:{sport}] Grok vision error: {e}")

    return {}


# ── MLB Live Stat Tracking ────────────────────────────────────────────────────

_MLB_LIVE_PROP_TYPES = {
    "pitcher_strikeouts", "innings_pitched", "hits_allowed", "earned_runs",
    "walks_allowed", "pitches_thrown", "batters_faced",
    "hits", "home_runs", "rbi", "walks", "strikeouts", "runs",
    "total_bases", "stolen_bases", "doubles", "plate_appearances",
    "hitter_fantasy_points", "hits_runs_rbis",
    "pitcher_fantasy_score", "pitching_outs",
}


async def mlb_live_loop():
    """Background task: poll BDL every ~2 minutes for live/today MLB games
    and update currentValue on pending/live MLB picks so the pick card shows
    a live stat counter exactly like soccer shows live passes/shots."""
    await asyncio.sleep(20)  # Brief startup delay so the rest of the app is ready
    while True:
        try:
            await _update_mlb_live_picks()
        except Exception as e:
            print(f"[MLB LIVE] Loop error: {e}")
        await asyncio.sleep(180)  # 3-minute cadence — shared BDL key


async def _update_mlb_live_picks():
    """Core of the MLB live loop: find in-progress or today's games,
    fetch each player's current game stats, and write them to the picks collection."""
    try:
        import mlb_client
        from mlb_engine import ALL_PROP_FIELDS
    except ImportError as _ie:
        print(f"[MLB LIVE] Import error: {_ie}")
        return

    # Grab all live/pending MLB picks (detect by sport field OR prop type)
    live_picks = await db.picks.find(
        {"$or": [
            {"status": "live",    "sport": "mlb"},
            {"status": "pending", "sport": "mlb"},
            {"status": "live",    "propType": {"$in": list(_MLB_LIVE_PROP_TYPES)}},
            {"status": "pending", "propType": {"$in": list(_MLB_LIVE_PROP_TYPES)}},
        ]},
        {"_id": 0}
    ).to_list(200)

    if not live_picks:
        return

    # Always use the current calendar year for live game lookups — a pick saved
    # in "season 2025" won't find a game running in the 2026 season otherwise.
    current_year = datetime.now(timezone.utc).year

    # Group by team_id only (not season — we use current_year for all live queries)
    team_groups: dict = {}
    for pick in live_picks:
        tid = pick.get("teamId") or 0
        team_groups.setdefault(tid, []).append(pick)

    for team_id, picks in team_groups.items():
        if not team_id:
            continue
        try:
            # ── Resolve BDL team ID (picks store Stats API IDs; BDL uses 1-30) ─
            bdl_team_id = await mlb_client.get_bdl_team_id_for_statsapi(team_id, current_year)
            effective_team_id = bdl_team_id or team_id  # fallback to original if lookup fails
            if bdl_team_id and bdl_team_id != team_id:
                print(f"[MLB LIVE] Resolved BDL team id: statsapi={team_id} → bdl={bdl_team_id}")

            # ── Fetch today's game for this team ─────────────────────────────
            games = await mlb_client.get_today_and_live_games(effective_team_id, current_year)
            live_game   = next((g for g in games if "IN_PROGRESS" in (g.get("status") or "").upper()), None)
            today_game  = live_game or (games[0] if games else None)
            today_game_id = today_game.get("id") if today_game else None

            for pick in picks:
                player_id = pick.get("playerId")
                prop_type = (pick.get("propType") or "").lower()
                field     = ALL_PROP_FIELDS.get(prop_type)
                if not player_id or not field:
                    continue

                # ── Determine which game to use for this pick ─────────────────
                # CRITICAL: never overwrite a confirmed gameId with today's game.
                # Old picks had their gameId silently replaced each loop cycle,
                # meaning a 5-day-old pick would try to settle against today's
                # scheduled game — which has no stats — and loop forever.
                stored_game_id = pick.get("gameId")

                if stored_game_id and stored_game_id != today_game_id:
                    # Pick has stats from a PREVIOUS game — use that game's data.
                    # If it differs from today's it's already completed.
                    game_id     = stored_game_id
                    is_live     = False   # past game is never live
                    is_final    = True    # past game is always final
                    home_abbrev = pick.get("homeTeam", "")
                    away_abbrev = pick.get("awayTeam", "")
                    home_runs   = pick.get("finalHomeGoals")
                    away_runs   = pick.get("finalAwayGoals")
                elif today_game_id:
                    # Either no stored gameId, or stored matches today → use today's game
                    game_id     = today_game_id
                    status_str  = (today_game.get("status") or "").upper()
                    is_live     = "IN_PROGRESS" in status_str
                    is_final    = "FINAL"       in status_str
                    home_team   = today_game.get("home_team", {}) or {}
                    away_team   = today_game.get("away_team", {}) or {}
                    home_abbrev = home_team.get("abbreviation", "")
                    away_abbrev = away_team.get("abbreviation", "")
                    home_runs   = (today_game.get("home_team_data") or {}).get("runs")
                    away_runs   = (today_game.get("away_team_data") or {}).get("runs")
                else:
                    # No today game found for this team — skip
                    continue

                # ── Stale-final escape hatch ──────────────────────────────────
                # If matchStatus is already "final" AND the pick is >48h old with
                # no currentValue, stats are never coming — void as push so it
                # doesn't stay live indefinitely.
                if is_final and pick.get("currentValue") is None:
                    pick_ts = None
                    for _tf in ("timestamp", "createdAt"):
                        _raw = pick.get(_tf)
                        if _raw:
                            try:
                                pick_ts = datetime.fromisoformat(str(_raw).replace("Z", "+00:00"))
                                break
                            except Exception:
                                pass
                    if pick_ts:
                        age_h = (datetime.now(timezone.utc) - pick_ts).total_seconds() / 3600
                        if age_h > 48:
                            print(f"[MLB LIVE] Stale-final void: {pick.get('playerName')} "
                                  f"{prop_type} age={age_h:.0f}h game={game_id} — push")
                            await db.picks.update_one(
                                {"pickId": pick["pickId"]},
                                {"$set": {
                                    "result":      "push",
                                    "status":      "settled",
                                    "matchStatus": "final",
                                    "settledAt":   datetime.now(timezone.utc).isoformat(),
                                    "settledBy":   "mlb_stale_void",
                                }},
                            )
                            continue

                # Fetch current game stats — skip cache for live games so every
                # loop iteration gets the freshest values from BDL.
                current_value = None
                stats = None
                try:
                    from mlb_engine import _compute_fantasy_pts as _fp
                    stats = await mlb_client.get_game_player_stats(
                        int(player_id), int(game_id), current_year, live=is_live
                    )
                    if stats:
                        if prop_type == "hitter_fantasy_points":
                            current_value = _fp(stats)
                        elif prop_type == "hits_runs_rbis":
                            from mlb_engine import _compute_hits_runs_rbis as _hrr
                            current_value = _hrr(stats)
                        elif prop_type == "pitcher_fantasy_score":
                            from mlb_engine import _compute_pitcher_fantasy as _pf
                            current_value = _pf(stats)
                        elif prop_type == "pitching_outs":
                            from mlb_engine import _compute_pitching_outs as _po
                            current_value = _po(stats)
                        else:
                            raw = stats.get(field)
                            if raw is not None:
                                if prop_type == "innings_pitched":
                                    parts = str(raw).split(".")
                                    whole = int(parts[0])
                                    frac  = int(parts[1]) if len(parts) > 1 else 0
                                    current_value = round(whole + frac / 3.0, 1)
                                else:
                                    current_value = float(raw)
                except Exception as _se:
                    print(f"[MLB LIVE] Stats fetch failed player={player_id} game={game_id}: {_se}")
                    continue

                # Skip if no data at all and game hasn't started
                if current_value is None and not (is_live or is_final):
                    continue

                line = float(pick.get("line") or 0)
                rec  = (pick.get("recommendation") or "over").upper()
                match_status = "final" if is_final else ("live" if is_live else "scheduled")

                set_fields: dict = {"matchStatus": match_status}
                # Only write gameId/score fields when using TODAY's game (don't
                # overwrite historical data on picks with a prior game's gameId).
                if not stored_game_id or stored_game_id == today_game_id:
                    set_fields["gameId"] = game_id
                    set_fields["homeTeam"] = home_abbrev
                    set_fields["awayTeam"] = away_abbrev
                if home_runs is not None:
                    set_fields["finalHomeGoals"] = home_runs
                if away_runs is not None:
                    set_fields["finalAwayGoals"] = away_runs
                if current_value is not None:
                    set_fields["currentValue"] = current_value

                if is_final and current_value is not None:
                    line_f = line
                    # ── DNP guard: pitcher got 0 K/outs but also 0 IP ─────────
                    _PITCHER_COUNT_PROPS = {
                        "pitcher_strikeouts", "hits_allowed", "earned_runs",
                        "walks_allowed", "pitches_thrown", "batters_faced",
                        "pitcher_fantasy_score", "pitching_outs",
                    }
                    result_str: str
                    if prop_type in _PITCHER_COUNT_PROPS and current_value == 0.0 and stats:
                        ip_raw = stats.get("ip")
                        if ip_raw is not None:
                            try:
                                ip_parts = str(ip_raw).split(".")
                                ip_float = int(ip_parts[0]) + (int(ip_parts[1]) / 3.0 if len(ip_parts) > 1 else 0)
                                if ip_float == 0.0:
                                    result_str = "push"
                                    print(f"[MLB LIVE] DNP {pick.get('playerName')} {prop_type} IP=0 → push")
                                else:
                                    result_str = "push" if current_value == line_f else ("hit" if (rec == "OVER" and current_value > line_f) or (rec != "OVER" and current_value < line_f) else "miss")
                            except Exception:
                                result_str = "push" if current_value == line_f else ("hit" if (rec == "OVER" and current_value > line_f) or (rec != "OVER" and current_value < line_f) else "miss")
                        else:
                            result_str = "push" if current_value == line_f else ("hit" if (rec == "OVER" and current_value > line_f) or (rec != "OVER" and current_value < line_f) else "miss")
                    elif current_value == line_f:
                        result_str = "push"
                    elif rec == "OVER":
                        result_str = "hit" if current_value > line_f else "miss"
                    else:
                        result_str = "hit" if current_value < line_f else "miss"
                    set_fields.update({
                        "actualValue": round(current_value, 1),
                        "result":      result_str,
                        "status":      "settled",
                        "settledAt":   datetime.now(timezone.utc).isoformat(),
                        "settledBy":   "mlb_live_loop",
                    })
                    print(f"[MLB LIVE] ✓ Settled {pick.get('playerName')} {prop_type} "
                          f"actual={current_value} line={line_f} rec={rec} → {result_str}")
                elif is_live:
                    set_fields["status"] = "live"
                    if current_value is not None:
                        print(f"[MLB LIVE] {pick.get('playerName')} {prop_type} = {current_value} (live)")

                await db.picks.update_one(
                    {"pickId": pick["pickId"]},
                    {"$set": set_fields}
                )

        except Exception as _te:
            print(f"[MLB LIVE] Team {team_id}/{current_year} error: {_te}")
        # Pace BDL calls between teams — shared API key across all sport clients
        await asyncio.sleep(1.5)
