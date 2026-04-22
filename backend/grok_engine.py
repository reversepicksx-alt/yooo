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
    """Unified AI call: Gemini Flash primary, Grok fallback."""
    result = await _gemini_call(prompt, system=system, temperature=temperature,
                                 max_tokens=max_tokens, timeout=timeout, model=GEMINI_FLASH)
    if result:
        return result
    print("[AI] Gemini failed — falling back to Grok")
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

    # Strategy 1: Gemini with Google Search grounding (live web data)
    if GEMINI_API_KEY:
        try:
            url = f"{GEMINI_BASE}/{GEMINI_FLASH}:generateContent?key={GEMINI_API_KEY}"
            payload = {
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "tools": [{"googleSearch": {}}],
                "generationConfig": {"temperature": 0, "maxOutputTokens": 600},
            }
            async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=10)) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    candidates = data.get("candidates", [])
                    if candidates:
                        parts = candidates[0].get("content", {}).get("parts", [])
                        text = " ".join(p.get("text", "") for p in parts if p.get("text")).strip()
                        if text:
                            print(f"[WEB INTEL] Gemini Search success: {text[:120]}...")
                            return text
                else:
                    print(f"[WEB INTEL] Gemini Search error {resp.status_code}: {resp.text[:150]}")
        except httpx.TimeoutException:
            print("[WEB INTEL] Gemini Search timeout")
        except Exception as e:
            print(f"[WEB INTEL] Gemini Search error: {type(e).__name__}: {e}")

    # Strategy 2: Grok web search fallback
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
    """Background loop: check and settle finished games every 2 minutes."""
    await asyncio.sleep(30)  # Initial delay
    print("[GROK ENGINE] Auto-settlement bot started")

    while True:
        try:
            await _run_auto_settlement()
        except Exception as e:
            print(f"[AUTO-SETTLE] Error: {e}")
        await asyncio.sleep(120)  # Check every 2 minutes


async def _run_auto_settlement():
    """Check all live picks and settle any finished games."""
    from utils import api_football_request
    from config import CURRENT_SEASON

    # Settle both "live" picks AND "pending" picks — pending picks can miss the live
    # promotion step if the user doesn't refresh during the match, leaving them stuck.
    # Include pending picks created more than 90 minutes ago (match should be over).
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=90)).isoformat()
    live_picks = await db.picks.find(
        {"$or": [
            {"status": "live"},
            {"status": "pending", "timestamp": {"$lt": cutoff}},
            {"status": "pending", "createdAt": {"$lt": cutoff}},
        ]},
        {"_id": 0}
    ).to_list(300)
    if not live_picks:
        return

    settled_count = 0

    # Process soccer picks
    soccer_picks = live_picks
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
            await db.picks.update_one(
                {"pickId": pick["pickId"]},
                {"$set": {
                    "status": "settled",
                    "result": "push",
                    "actualValue": actual_value,
                    "minutesPlayed": minutes_played,
                    "matchScore": f"{_player_goals}-{_opp_goals}",
                    "settledAt": datetime.now(timezone.utc).isoformat(),
                    "voidReason": f"Player only played {minutes_played} min (min {MIN_MINUTES} required)",
                }}
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

        await db.picks.update_one(
            {"pickId": pick["pickId"]},
            {"$set": {
                "status": "settled",
                "result": result,
                "actualValue": actual_value,
                "minutesPlayed": minutes_played,
                "matchScore": f"{_player_goals}-{_opp_goals}",
                "settledAt": datetime.now(timezone.utc).isoformat(),
            }}
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
            await _run_auto_scout()
        except Exception as e:
            print(f"[AUTO-SCOUT] Error: {e}")
        await asyncio.sleep(21600)  # Every 6 hours


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
                            {"$set": {"_k": cache_key, "d": [r.get("fixture", {}).get("id") for r in recent], "ts": datetime.now(timezone.utc).isoformat()}},
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

    # Strategy 1: Gemini vision (primary)
    if GEMINI_API_KEY:
        try:
            url = f"{GEMINI_BASE}/{GEMINI_FLASH}:generateContent?key={GEMINI_API_KEY}"
            payload = {
                "contents": [{"role": "user", "parts": [
                    {"text": prompt},
                    {"inlineData": {"mimeType": "image/png", "data": image_base64}}
                ]}],
                "generationConfig": {"temperature": 0, "maxOutputTokens": 500, "responseMimeType": "application/json"},
            }
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    candidates = data.get("candidates", [])
                    if candidates:
                        parts = candidates[0].get("content", {}).get("parts", [])
                        text = "".join(p.get("text", "") for p in parts).strip()
                        result = _parse_json(text)
                        if result:
                            if isinstance(result, list) and len(result) > 0:
                                result = result[0]
                            if isinstance(result, dict):
                                result = _normalize(result)
                                print(f"[SCAN] Gemini vision: {result.get('playerName','')} {result.get('propType','')} {result.get('line','')}")
                                return result
                else:
                    print(f"[SCAN] Gemini vision error: {resp.status_code} — {resp.text[:200]}")
        except Exception as e:
            print(f"[SCAN] Gemini vision error: {e}")

    # Strategy 2: Grok vision fallback
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
