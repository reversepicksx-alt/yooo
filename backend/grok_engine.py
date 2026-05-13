"""
AI ENGINE — The data backbone powering the ReversePicks prediction system.
Primary AI: Gemini 2.5 Pro/Flash. Fallback: Grok.
"""
import json
import httpx
import asyncio
import traceback
from datetime import datetime, timezone, timedelta
from config import db, XAI_API_KEY, GEMINI_API_KEY

GROK_MODEL = "grok-4-1-fast-non-reasoning"
GROK_REASONING_MODEL = "grok-4-1-fast-non-reasoning"
GROK_SEARCH_MODEL = "grok-3"
GROK_URL = "https://api.x.ai/v1/chat/completions"

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
GEMINI_PRO = "gemini-2.5-pro"
GEMINI_FLASH = "gemini-2.5-flash"


async def _gemini_call(
    prompt: str,
    system: str = "",
    temperature: float = 0.0,
    max_tokens: int = 2000,
    timeout: int = 40,
    model: str = GEMINI_FLASH,
    json_mode: bool = False,
) -> str:
    """Core Gemini API call. Returns raw text (or JSON string if json_mode=True)."""
    if not GEMINI_API_KEY:
        return ""
    url = f"{GEMINI_BASE}/{model}:generateContent?key={GEMINI_API_KEY}"
    payload: dict = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
        },
    }
    if system:
        payload["systemInstruction"] = {"parts": [{"text": system}]}
    if json_mode:
        payload["generationConfig"]["responseMimeType"] = "application/json"

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=10)) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code == 200:
                data = resp.json()
                candidates = data.get("candidates", [])
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [])
                    if parts:
                        return parts[0].get("text", "").strip()
            else:
                print(f"[GEMINI] API error {resp.status_code}: {resp.text[:200]}")
    except httpx.TimeoutException:
        print(f"[GEMINI] Timeout ({model}, {timeout}s)")
    except Exception as e:
        print(f"[GEMINI] Call error: {type(e).__name__}: {e}")
    return ""


async def _grok_call(prompt: str, temperature: float = 0, max_tokens: int = 2000, timeout: int = 30, reasoning: bool = False) -> str:
    """Core Grok API call — kept as fallback when Gemini unavailable."""
    if not XAI_API_KEY:
        return ""
    model = GROK_REASONING_MODEL if reasoning else GROK_MODEL
    models_to_try = [model, GROK_MODEL if reasoning else GROK_REASONING_MODEL]

    for attempt_model in models_to_try:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=10)) as client:
                resp = await client.post(
                    GROK_URL,
                    headers={"Authorization": f"Bearer {XAI_API_KEY}", "Content-Type": "application/json"},
                    json={
                        "model": attempt_model,
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": temperature,
                        "max_tokens": max_tokens,
                    }
                )
                if resp.status_code == 200:
                    return resp.json()["choices"][0]["message"]["content"].strip()
                else:
                    print(f"[GROK] API error {resp.status_code} ({attempt_model}): {resp.text[:200]}")
        except httpx.TimeoutException:
            print(f"[GROK] Timeout ({attempt_model}, {timeout}s)")
        except Exception as e:
            print(f"[GROK] Call error ({attempt_model}): {type(e).__name__}: {e}")
    return ""


async def _ai_call(prompt: str, system: str = "", temperature: float = 0, max_tokens: int = 2000, timeout: int = 35) -> str:
    """Unified AI call: Grok only."""
    return await _grok_call(prompt, temperature=temperature, max_tokens=max_tokens, timeout=timeout)


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
    Primary: Gemini 2.5 Flash with Google Search grounding.
    Fallback: Grok web search → tactical knowledge.
    """
    date_str = match_date[:10] if match_date else ""
    context_str = f"{league} — {match_round}" if (league or match_round) else "upcoming match"

    prompt = (
        f"Give me a concise pre-match intelligence briefing (max 200 words) for: "
        f"{player_team} vs {opponent}{f' ({date_str})' if date_str else ''} [{context_str}].\n\n"
        f"Focus ONLY on: (1) confirmed injuries and suspensions for both teams, "
        f"(2) expected lineup or formation changes, (3) manager tactical comments, "
        f"(4) any relevant match context (must-win, rotation, travel fatigue, etc.).\n"
        f"Be factual and specific. Do not make up information. If nothing significant is confirmed, say so briefly."
    )

    # Strategy 1: Grok web search
    if XAI_API_KEY:
        headers = {"Authorization": f"Bearer {XAI_API_KEY}", "Content-Type": "application/json"}
        for model in [GROK_SEARCH_MODEL, GROK_REASONING_MODEL]:
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=10)) as client:
                    payload = {
                        "model": model,
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0,
                        "max_tokens": 500,
                        "tools": [{"type": "web_search_preview"}],
                        "tool_choice": "auto",
                    }
                    resp = await client.post(GROK_URL, headers=headers, json=payload)
                    if resp.status_code == 200:
                        msg = resp.json()["choices"][0].get("message", {})
                        content = msg.get("content", "")
                        if content:
                            print(f"[WEB INTEL] Grok search ({model}): {content[:120]}...")
                            return content.strip()
                    elif resp.status_code in (400, 404, 422):
                        break
            except Exception as e:
                print(f"[WEB INTEL] Grok error ({model}): {e}")

    # Strategy 3: Tactical knowledge (Gemini or Grok, no live search)
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
    result = await _ai_call(knowledge_prompt, timeout=15, max_tokens=350)
    if result:
        import html as _html
        result = _html.unescape(result)
        print(f"[WEB INTEL] Tactical knowledge fallback: {result[:120]}...")
        return result

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
    Scrape understat.com via Grok's live web search for the opponent's real PPDA
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

    # Prompt Grok to search understat specifically for this team's PPDA
    search_prompt = (
        f"Go to {understat_url} and look at the team statistics table. "
        f"Find {opponent}'s PPDA (Passes Per Defensive Action) for the current 2025/2026 season. "
        f"PPDA is a pressing intensity metric — lower values mean more aggressive pressing "
        f"(e.g. 6.5 = elite press, 9.0 = moderate, 13+ = low press). "
        f"Reply with ONLY the PPDA number as a decimal (e.g. '7.8'). "
        f"If you cannot find {opponent} on that page or cannot confirm the value, reply with exactly 'unknown'."
    )

    headers = {"Authorization": f"Bearer {XAI_API_KEY}", "Content-Type": "application/json"}

    # Strategy 1: Grok web search (live scrape of understat.com)
    for model in [GROK_SEARCH_MODEL]:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=8)) as client:
                payload = {
                    "model": model,
                    "messages": [{"role": "user", "content": search_prompt}],
                    "temperature": 0,
                    "max_tokens": 30,
                    "tools": [{"type": "web_search_preview"}],
                    "tool_choice": "required",
                }
                resp = await client.post(GROK_URL, headers=headers, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    choice = data["choices"][0]
                    msg = choice.get("message", {})
                    content = msg.get("content", "")
                    tool_calls = msg.get("tool_calls", [])
                    if not content and tool_calls:
                        messages = [
                            {"role": "user", "content": search_prompt},
                            {"role": "assistant", "tool_calls": tool_calls, "content": None},
                        ]
                        for tc in tool_calls:
                            messages.append({
                                "role": "tool",
                                "tool_call_id": tc.get("id", ""),
                                "content": "[Search results integrated by model]",
                            })
                        payload2 = {**payload, "messages": messages, "tool_choice": "auto", "max_tokens": 30}
                        resp2 = await client.post(GROK_URL, headers=headers, json=payload2)
                        if resp2.status_code == 200:
                            content = resp2.json()["choices"][0]["message"].get("content", "")
                    if content:
                        text = content.strip()
                        if "unknown" in text.lower():
                            print(f"[PPDA] understat: {opponent} not found ({understat_code})")
                            return None
                        m = _re.search(r'\b(\d{1,2}(?:\.\d{1,2})?)\b', text)
                        if m:
                            val = float(m.group(1))
                            if 3.0 <= val <= 30.0:
                                print(f"[PPDA] understat scrape: {opponent} ({understat_code}) PPDA={val}")
                                return val
                        print(f"[PPDA] understat unparseable: '{text}'")
                        return None
                elif resp.status_code in (400, 404, 422):
                    print(f"[PPDA] Web search not available ({resp.status_code}) — falling through")
                    break
                else:
                    print(f"[PPDA] Web search error {resp.status_code}: {resp.text[:100]}")
        except asyncio.TimeoutError:
            print(f"[PPDA] understat scrape timeout ({model})")
        except Exception as e:
            print(f"[PPDA] understat scrape exception: {e}")

    # Strategy 2: Grok knowledge fallback — only for understat-covered leagues
    # Ask directly about the team's known PPDA from training data
    knowledge_prompt = (
        f"What is {opponent}'s PPDA (Passes Per Defensive Action) in the current or most recent "
        f"{league} season, based on understat.com data? "
        f"PPDA < 6 = elite press, 6-8 = high, 8-11 = moderate, 11+ = low. "
        f"Reply with ONLY a single decimal number. If unsure, reply 'unknown'."
    )
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15, connect=8)) as client:
            resp = await client.post(
                GROK_URL, headers=headers,
                json={
                    "model": GROK_REASONING_MODEL,
                    "messages": [{"role": "user", "content": knowledge_prompt}],
                    "temperature": 0,
                    "max_tokens": 15,
                }
            )
            if resp.status_code == 200:
                text = resp.json()["choices"][0]["message"]["content"].strip()
                if "unknown" in text.lower():
                    print(f"[PPDA] Knowledge fallback: {opponent} unknown")
                    return None
                m = _re.search(r'\b(\d{1,2}(?:\.\d{1,2})?)\b', text)
                if m:
                    val = float(m.group(1))
                    if 3.0 <= val <= 30.0:
                        print(f"[PPDA] Knowledge fallback: {opponent} PPDA={val}")
                        return val
    except Exception as e:
        print(f"[PPDA] Knowledge fallback exception: {e}")

    return None


# ═══════════════════════════════════════════════════════════════
# AI PRESS INTENSITY — Universal opponent press rating (any league)
# ═══════════════════════════════════════════════════════════════
# Replaces the heuristic compute_press_intensity_score for opponents
# whose press style isn't well captured by raw tackles+interceptions.
# Asks Grok to rate opponent on 0–1 press scale using web search +
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
    Ask Grok to rate the opponent's pressing intensity on a 0–1 scale.

    Returns dict with:
      score      : 0.0 – 1.0  (0 = deep block, 1 = elite high press)
      label      : "Low" / "Moderate" / "High" / "Elite"
      ppda       : float | None   (if Grok found/knew it)
      reasoning  : short string from Grok
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

    # In-flight dedupe — concurrent predictions for the same opponent share one Grok call
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
    league_str = league or "their league"
    prompt = (
        f"You are a tactical football analyst. Rate {opponent}'s SEASON-AVERAGE pressing intensity "
        f"in {league_str} for the {season} season on a STRICT 0.0 to 1.0 scale.\n\n"
        f"Anchors (calibrate against these):\n"
        f"  0.00–0.25 LOW     — deep block, drops off (Burnley Dyche, Getafe, Diego Simeone late-era)\n"
        f"  0.25–0.50 MODERATE— mid-block, situational pressing (mid-table sides)\n"
        f"  0.50–0.75 HIGH    — aggressive press, hunts in opponent half (Liverpool Klopp, Brighton, Bilbao)\n"
        f"  0.75–1.00 ELITE   — relentless high press (Bielsa Leeds, peak Rayo Vallecano, Bayer Leverkusen Xabi)\n\n"
        f"PPDA RULES — READ CAREFULLY:\n"
        f"  • Report the team's SEASON-AVERAGE PPDA only (not single-game, not rolling 5-game, not opponent-specific projection).\n"
        f"  • La Liga league average PPDA ≈ 10–12. EPL ≈ 10–12. Bundesliga ≈ 9–11.\n"
        f"  • Elite pressers historically sit 7.5–9.0 over a full season. Examples: Klopp Liverpool '18/19 (7.9), "
        f"Bielsa Leeds '20/21 (7.4), Rayo Vallecano (typically 8–10).\n"
        f"  • PPDA below 7.5 over a full season is HISTORIC territory and requires explicit evidence — "
        f"do NOT guess sub-7.5 values. If unsure between 7.5 and 9, pick the higher end.\n"
        f"  • Score-to-PPDA mapping: 0.85+ ≈ PPDA 7.5–8.5; 0.70 ≈ PPDA 8.5–9.5; 0.50 ≈ PPDA 10–11; 0.25 ≈ PPDA 12+.\n\n"
        f"Reply ONLY with strict JSON, no markdown:\n"
        f'{{"score": <0.0-1.0 float>, "ppda": <season-avg float or null>, "reasoning": "<one short sentence with concrete evidence>"}}\n'
        f"If you cannot make a confident assessment, reply: {{\"score\": null}}"
    )

    headers = {"Authorization": f"Bearer {XAI_API_KEY}", "Content-Type": "application/json"}

    # Strategy 1: Grok web search (live tactical reports + understat)
    parsed = None
    used_source = None
    for model in [GROK_SEARCH_MODEL]:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=8)) as client:
                payload = {
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0,
                    "max_tokens": 200,
                    "tools": [{"type": "web_search_preview"}],
                    "tool_choice": "required",
                }
                resp = await client.post(GROK_URL, headers=headers, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    msg = data["choices"][0].get("message", {})
                    content = msg.get("content", "")
                    tool_calls = msg.get("tool_calls", [])
                    if not content and tool_calls:
                        messages = [
                            {"role": "user", "content": prompt},
                            {"role": "assistant", "tool_calls": tool_calls, "content": None},
                        ]
                        for tc in tool_calls:
                            messages.append({
                                "role": "tool",
                                "tool_call_id": tc.get("id", ""),
                                "content": "[Search results integrated by model]",
                            })
                        payload2 = {**payload, "messages": messages, "tool_choice": "auto", "max_tokens": 200}
                        resp2 = await client.post(GROK_URL, headers=headers, json=payload2)
                        if resp2.status_code == 200:
                            content = resp2.json()["choices"][0]["message"].get("content", "")
                    if content:
                        parsed = _parse_json(content)
                        if parsed and isinstance(parsed, dict) and parsed.get("score") is not None:
                            used_source = "ai_web"
                            break
                elif resp.status_code in (400, 404, 422):
                    print(f"[AI PRESS] Web search not available ({resp.status_code}) — trying knowledge fallback")
                    break
                else:
                    print(f"[AI PRESS] Web search error {resp.status_code}: {resp.text[:120]}")
        except asyncio.TimeoutError:
            print(f"[AI PRESS] Web search timeout ({model})")
        except Exception as e:
            print(f"[AI PRESS] Web search exception: {e}")

    # Strategy 2: Grok knowledge fallback (no web search)
    if not parsed or parsed.get("score") is None:
        try:
            txt = await _grok_call(prompt, temperature=0, max_tokens=200, timeout=15, reasoning=True)
            if txt:
                parsed = _parse_json(txt)
                if parsed and isinstance(parsed, dict) and parsed.get("score") is not None:
                    used_source = "ai_knowledge"
        except Exception as e:
            print(f"[AI PRESS] Knowledge fallback exception: {e}")

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
    # If Grok returns sub-7.5, floor to 7.5 (matches the documented score-to-PPDA mapping at the elite tier)
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
    """Parse JSON from Grok response, stripping markdown wrappers."""
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
# Grok crunches raw API data into a focused, insight-rich brief
# that feeds GPT-5.2 a shorter, smarter prompt
# ═══════════════════════════════════════════════════════════════

async def build_grok_digest(
    player_name: str, team_name: str, opponent_name: str,
    prop_type: str, line: float, venue: str,
    player_stats: dict, team_stats: dict, opponent_stats: dict,
    h2h_data: list, match_odds: dict, standings: list,
    player_game_logs: list, team_fixture_stats: list,
    opponent_fixture_stats: list, match_dominance: dict,
    sport: str = "soccer"
) -> str:
    """Build a Grok-processed data digest. Runs in ~2-3s.
    Returns a compact string of key insights for GPT-5.2."""

    # Build raw data summary for Grok to analyze
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
    await asyncio.sleep(60)  # Initial delay
    print("[GROK ENGINE] Auto-settlement bot started (15 min interval)")

    while True:
        try:
            await _run_auto_settlement()
        except Exception as e:
            print(f"[AUTO-SETTLE] Error: {e}")
        await asyncio.sleep(900)  # Check every 15 minutes


async def _try_settle_mlb(pick: dict) -> bool:
    """
    Settle an MLB pick using BallDontLie game logs.
    Called from _run_auto_settlement() for picks with sport='mlb'.
    Returns True when a settlement was written.
    """
    try:
        import mlb_client
        from mlb_engine import ALL_PROP_FIELDS, PITCHER_PROPS
    except ImportError as _ie:
        print(f"[MLB SETTLE] Import error: {_ie}")
        return False

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

    # MLB settlement REQUIRES the live loop to have already confirmed today's game
    # by writing a gameId onto the pick.  Without it we have no way to know which
    # game to score — grabbing game_logs[0] would silently use a past start.
    expected_game_id = pick.get("gameId")
    if not expected_game_id:
        return False  # Live loop hasn't confirmed today's game yet — wait

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

    season = pick.get("season") or 2025
    try:
        game_logs = await mlb_client.get_player_game_logs(player_id, int(season), limit=3)
    except Exception as _e:
        print(f"[MLB SETTLE] Log fetch failed for player {player_id}: {_e}")
        return False

    if not game_logs:
        return False

    recent = game_logs[0]

    # Verify this log entry is for the game the live loop confirmed — not a prior start
    log_game_id = recent.get("game_id") or recent.get("gameId")
    if log_game_id and int(log_game_id) != int(expected_game_id):
        print(f"[MLB SETTLE] Game ID mismatch: log={log_game_id} vs pick gameId={expected_game_id} — game not finished yet")
        return False

    raw_val = recent.get(field)
    if raw_val is None:
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
    }
    mlb_picks    = [p for p in live_picks if p.get("sport") == "mlb" or p.get("propType", "") in _MLB_PROP_TYPES]
    soccer_picks = [p for p in live_picks if p not in mlb_picks]

    for pick in mlb_picks:
        try:
            settled = await _try_settle_mlb(pick)
            if settled:
                settled_count += 1
        except Exception as _me:
            print(f"[MLB SETTLE] Error: {_me}")
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

                date_fix_calls = []
                for pd in set(pick_dates):
                    if pd not in (today, yesterday):
                        date_fix_calls.append(api_football_request("fixtures", {"team": tid, "date": pd, "season": CURRENT_SEASON}))

                (today_fix, yest_fix, last_fix,
                 today_fix2, yest_fix2, last_fix2, *extra_date_fixes) = await asyncio.gather(
                    api_football_request("fixtures", {"team": tid, "date": today, "season": CURRENT_SEASON}),
                    api_football_request("fixtures", {"team": tid, "date": yesterday, "season": CURRENT_SEASON}),
                    api_football_request("fixtures", {"team": tid, "last": 10, "season": CURRENT_SEASON}),
                    api_football_request("fixtures", {"team": tid, "date": today, "season": next_s}),
                    api_football_request("fixtures", {"team": tid, "date": yesterday, "season": next_s}),
                    api_football_request("fixtures", {"team": tid, "last": 10, "season": next_s}),
                    *date_fix_calls,
                    return_exceptions=True
                )

                all_fixtures = []
                seen = set()
                for batch in [today_fix, yest_fix, last_fix, today_fix2, yest_fix2, last_fix2, *extra_date_fixes]:
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
                    (today_fix, yest_fix, last_fix,
                     today_fix2, yest_fix2, last_fix2) = await asyncio.gather(
                        api_football_request("fixtures", {"team": tid, "date": today, "season": CURRENT_SEASON}),
                        api_football_request("fixtures", {"team": tid, "date": yesterday, "season": CURRENT_SEASON}),
                        api_football_request("fixtures", {"team": tid, "last": 10, "season": CURRENT_SEASON}),
                        api_football_request("fixtures", {"team": tid, "date": today, "season": next_s}),
                        api_football_request("fixtures", {"team": tid, "date": yesterday, "season": next_s}),
                        api_football_request("fixtures", {"team": tid, "last": 10, "season": next_s}),
                        return_exceptions=True
                    )
                    all_fixtures = []
                    seen = set()
                    for batch in [today_fix, yest_fix, last_fix, today_fix2, yest_fix2, last_fix2]:
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

    if settled_count > 0:
        print(f"[AUTO-SETTLE] Settled {settled_count} picks")


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

                    recent = await api_football_request("fixtures", {"team": tid, "last": 10})
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
# Grok analyzes historical picks to find calibration insights
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

    # Ask Grok to find actionable patterns
    prompt = f"""Analyze this sports prediction model's performance data. Find the 5 most actionable calibration rules.

{data_text}

For each pattern, give:
1. The pattern (specific, with numbers)
2. The recommended adjustment (e.g., "increase projection by 5%", "flip under to over when confidence < 50%")
3. Expected impact

Return JSON: [{{"pattern":"...","adjustment":"...","impact":"..."}}]
Only JSON, no markdown."""

    result = await _grok_call(prompt, temperature=0, max_tokens=1000, timeout=15, reasoning=True)
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
        print(f"[PATTERN MINE] Grok returned no parseable insights")


# ═══════════════════════════════════════════════════════════════
# PHASE 5: SMART SCAN (Grok Vision for OCR)
# ═══════════════════════════════════════════════════════════════

async def grok_scan_prop(image_base64: str) -> dict:
    """Extract prop details from a screenshot using AI vision.
    Primary: Gemini 2.5 Flash (vision). Fallback: Grok vision.
    Returns: {"playerName": "...", "propType": "...", "line": 0, "teamName": "...", "opponentName": "...", "leagueName": "..."}"""

    prompt = """Extract the FIRST player prop bet from this image. Focus on the top-left or most prominent player card.

Extract:
- Player name (exact spelling from image)
- Team name (the team shown on the player's card/badge, NOT the opponent)
- Prop type (use EXACTLY one of: pass_attempts, shots, shots_on_target, tackles, key_passes, saves, interceptions, blocks, dribbles, goals, assists, fouls_drawn, crosses, clearances)
- Line/number (the over/under value)
- Opponent name (the "vs" team)
- League name (if visible, e.g., Champions League, La Liga, Premier League)

Return ONLY a valid JSON object (not an array):
{"playerName":"","propType":"","line":0,"teamName":"","opponentName":"","leagueName":""}"""

    def _normalize(result: dict) -> dict:
        if "teamName" in result and "playerTeam" not in result:
            result["playerTeam"] = result.pop("teamName")
        return result

    # Strategy 1: Grok vision
    if XAI_API_KEY:
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.post(
                    GROK_URL,
                    headers={"Authorization": f"Bearer {XAI_API_KEY}", "Content-Type": "application/json"},
                    json={
                        "model": "grok-4-1-fast-non-reasoning",
                        "messages": [{"role": "user", "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_base64}"}}
                        ]}],
                        "temperature": 0,
                        "max_tokens": 500,
                    }
                )
                if resp.status_code == 200:
                    content = resp.json()["choices"][0]["message"]["content"]
                    result = _parse_json(content)
                    if result:
                        if isinstance(result, list) and len(result) > 0:
                            result = result[0]
                        if isinstance(result, dict):
                            result = _normalize(result)
                            print(f"[SCAN] Grok vision fallback: {result.get('playerName','')} {result.get('propType','')} {result.get('line','')}")
                            return result
                else:
                    print(f"[SCAN] Grok vision error: {resp.status_code} — {resp.text[:300]}")
        except Exception as e:
            print(f"[SCAN] Grok vision error: {e}")

    return {}


# ── MLB Live Stat Tracking ────────────────────────────────────────────────────

_MLB_LIVE_PROP_TYPES = {
    "pitcher_strikeouts", "innings_pitched", "hits_allowed", "earned_runs",
    "walks_allowed", "pitches_thrown", "batters_faced",
    "hits", "home_runs", "rbi", "walks", "strikeouts", "runs",
    "total_bases", "stolen_bases", "doubles", "plate_appearances",
    "hitter_fantasy_points",
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
        await asyncio.sleep(110)  # ~2-minute cadence


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
            games = await mlb_client.get_today_and_live_games(team_id, current_year)
            if not games:
                continue

            # Prefer an in-progress game; fall back to any today's game
            live_game  = next((g for g in games if "IN_PROGRESS" in (g.get("status") or "").upper()), None)
            target     = live_game or games[0]
            status_str = (target.get("status") or "").upper()
            is_live    = "IN_PROGRESS" in status_str
            is_final   = "FINAL"       in status_str
            game_id    = target.get("id")
            if not game_id:
                continue

            home_team   = target.get("home_team", {}) or {}
            away_team   = target.get("away_team", {}) or {}
            home_abbrev = home_team.get("abbreviation", "")
            away_abbrev = away_team.get("abbreviation", "")
            home_runs   = (target.get("home_team_data") or {}).get("runs")
            away_runs   = (target.get("away_team_data") or {}).get("runs")

            for pick in picks:
                player_id = pick.get("playerId")
                prop_type = (pick.get("propType") or "").lower()
                field     = ALL_PROP_FIELDS.get(prop_type)
                if not player_id or not field:
                    continue

                # Fetch current game stats — skip cache for live games so every
                # loop iteration gets the freshest values from BDL.
                current_value = None
                try:
                    from mlb_engine import _compute_fantasy_pts as _fp
                    stats = await mlb_client.get_game_player_stats(
                        int(player_id), int(game_id), current_year, live=is_live
                    )
                    if stats:
                        if prop_type == "hitter_fantasy_points":
                            current_value = _fp(stats)
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

                set_fields: dict = {
                    "matchStatus": match_status,
                    "gameId":      game_id,
                    "homeTeam":    home_abbrev,
                    "awayTeam":    away_abbrev,
                }
                if home_runs is not None:
                    set_fields["finalHomeGoals"] = home_runs  # reuse soccer field — pick card renders it already
                if away_runs is not None:
                    set_fields["finalAwayGoals"] = away_runs
                if current_value is not None:
                    set_fields["currentValue"] = current_value

                if is_final and current_value is not None:
                    line_f = line
                    if current_value == line_f:
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
