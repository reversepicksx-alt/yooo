"""
Reverse Tactical — AI-powered soccer intelligence chat.
Supports text questions and image uploads (prop screenshots).
Connected to the full system: player cache, API-Sports data.
"""

import json
import uuid
import asyncio as aio
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException
from emergentintegrations.llm.chat import LlmChat, UserMessage, ImageContent
from openai import OpenAI

from config import (
    db, EMERGENT_LLM_KEY, XAI_API_KEY, CURRENT_SEASON,
    SUPPORTED_LEAGUES, PROP_TYPE_ALIASES, INTERNATIONAL_LEAGUES,
)
from models import ChatStartRequest, TacticalMessageRequest
from utils import api_football_request, strip_accents
from cache import get_player_by_name, get_team_by_name, get_national_team_id

router = APIRouter(prefix="/api", tags=["tactical"])

tactical_sessions = {}

GROK_SYSTEM = """You are REVERSE TACTICAL — an elite soccer analytics brain. You combine deep tactical knowledge with real-time data to answer ANY soccer question with authority.

CORE CAPABILITIES:
1. TACTICAL ANALYSIS: Formations, pressing patterns, build-up structures, defensive shapes, transitions, set pieces.
2. PLAYER PROFILING: Role analysis (DLP, B2B, IF, F9, etc.), stat signatures, matchup advantages/disadvantages.
3. PROP PREDICTION REASONING: Apply role analysis + matchup dynamics + game flow scenarios to evaluate any stat prop.
4. WHAT-IF SCENARIOS: "What if Team X plays a high line?", "What if Player Y starts on the bench?" — reason through cascading effects.
5. MULTI-TOPIC THREADING: Handle complex multi-part questions. Track context across follow-ups.
6. COMPARISON ANALYSIS: Player vs player, team vs team, tactical system vs system.

REASONING FRAMEWORK:
- Always ground analysis in ROLE → MATCHUP → GAME STATE → SCENARIO
- Quote specific numbers when data is provided
- Flag uncertainty when sample size is small
- Consider sub risk, injury context, rotation patterns
- Think in distributions, not point estimates

TACTICAL VOCABULARY: PPDA, progressive passes, xT, zone 14, half-spaces, counter-press, deep completions, ball-side overload, inverted fullback, false 9, regista, mezzala.

STYLE: Be direct, sharp, and opinionated. Back every claim with logic. Challenge weak assumptions. This is premium intelligence — make it feel like insider knowledge. NEVER mention the AI models being used."""

SYNTH_SYSTEM = """You are the synthesis layer of REVERSE TACTICAL. Your job:
1. Take the raw tactical analysis and the user's original question
2. Polish it into a clean, structured, authoritative response
3. Integrate any live data provided — quote real numbers, correct any data mismatches
4. Format with clear sections using markdown: **bold** for emphasis, bullet points for lists
5. Add a brief TL;DR at the end for quick scanning
6. Keep the tactical depth — do NOT water it down
7. If the analysis and data disagree, flag it explicitly
8. Be concise but thorough — aim for quality over quantity
9. NEVER mention any AI model names, engines, or technical architecture. You ARE the system."""


INTL_LEAGUE_IDS = {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 15, 29, 30, 31, 32, 33, 34, 115, 960}


def _classify_stat(league_id: int) -> str:
    """Return 'INTERNATIONAL' or 'CLUB' based on league ID."""
    return "INTERNATIONAL" if league_id and league_id in INTL_LEAGUE_IDS else "CLUB"


async def _is_international_context(team_name: str, opponent_name: str) -> bool:
    """Check if teams are national teams (indicates international match)."""
    for name in [team_name, opponent_name]:
        if not name:
            continue
        nat_id, _ = await get_national_team_id(name.lower().strip())
        if nat_id:
            return True
    return False


async def _fetch_player_stats_structured(player_id: int, player_name: str, team_name: str, is_intl: bool) -> str:
    """Fetch a player's stats, separating INTERNATIONAL from CLUB and prioritizing based on context."""
    intl_stats = []
    club_stats = []

    for season in [CURRENT_SEASON + 1, CURRENT_SEASON]:
        try:
            pdata = await api_football_request("players", {"id": player_id, "season": season})
            if not pdata or not pdata[0].get("statistics"):
                continue
            for stat_entry in pdata[0]["statistics"]:
                league = stat_entry.get("league", {})
                lg_name = league.get("name", "")
                lg_id = league.get("id")
                lg_country = league.get("country", "")
                apps = stat_entry.get("games", {}).get("appearences") or 0
                mins = stat_entry.get("games", {}).get("minutes") or 0
                pos = stat_entry.get("games", {}).get("position", "")
                if apps < 1:
                    continue
                raw = {
                    "passes": stat_entry.get("passes", {}).get("total"),
                    "key_passes": stat_entry.get("passes", {}).get("key"),
                    "shots": stat_entry.get("shots", {}).get("total"),
                    "shots_on": stat_entry.get("shots", {}).get("on"),
                    "tackles": stat_entry.get("tackles", {}).get("total"),
                    "interceptions": stat_entry.get("tackles", {}).get("interceptions"),
                    "dribbles": stat_entry.get("dribbles", {}).get("attempts"),
                    "fouls_drawn": stat_entry.get("fouls", {}).get("drawn"),
                    "goals": stat_entry.get("goals", {}).get("total"),
                    "assists": stat_entry.get("goals", {}).get("assists"),
                    "saves": stat_entry.get("goals", {}).get("saves"),
                }
                per_game = {k: round(v / apps, 2) for k, v in raw.items() if v}
                totals = {k: v for k, v in raw.items() if v}
                kind = _classify_stat(lg_id)
                line = f"  [{kind}] {lg_name} ({lg_country}) {season}: {apps} apps, {mins} min, pos={pos}\n    Per game: {per_game}\n    Totals: {totals}"
                if kind == "INTERNATIONAL":
                    intl_stats.append(line)
                else:
                    club_stats.append(line)
            if intl_stats or club_stats:
                break
        except Exception:
            continue

    parts = [f"[PLAYER DATA] {player_name} ({team_name}), id={player_id}"]

    if is_intl:
        # International context — lead with intl stats
        if intl_stats:
            parts.append("  === INTERNATIONAL STATS (PRIMARY — this is an international match) ===")
            parts.extend(intl_stats)
        else:
            parts.append("  === INTERNATIONAL STATS: None found for recent seasons ===")
        if club_stats:
            parts.append("  === CLUB STATS (secondary reference) ===")
            parts.extend(club_stats[:3])  # Limit club context
    else:
        # Club context — lead with club stats
        if club_stats:
            parts.append("  === CLUB STATS (PRIMARY) ===")
            parts.extend(club_stats)
        if intl_stats:
            parts.append("  === INTERNATIONAL STATS (additional context) ===")
            parts.extend(intl_stats)

    return "\n".join(parts)


async def _fetch_national_team_fixtures(team_name: str) -> str:
    """Fetch recent fixtures for a national team."""
    nat_id, nat_name = await get_national_team_id(team_name.lower().strip())
    if not nat_id:
        return ""
    try:
        fixtures = await api_football_request("fixtures", {"team": nat_id, "last": 5})
        if not fixtures:
            return ""
        lines = [f"[DATA] {nat_name} — Last 5 international fixtures:"]
        for f in fixtures:
            home = f.get("teams", {}).get("home", {}).get("name", "")
            away = f.get("teams", {}).get("away", {}).get("name", "")
            hg = f.get("goals", {}).get("home", 0)
            ag = f.get("goals", {}).get("away", 0)
            dt = f.get("fixture", {}).get("date", "")[:10]
            lg = f.get("league", {}).get("name", "")
            lines.append(f"  {dt} ({lg}): {home} {hg}-{ag} {away}")
        return "\n".join(lines)
    except Exception:
        return ""


async def _extract_image_props(image_base64: str) -> dict:
    """Use Vision AI to extract player prop data from a screenshot, then resolve players."""
    chat = LlmChat(
        api_key=EMERGENT_LLM_KEY,
        session_id=f"tac-scan-{uuid.uuid4().hex[:8]}",
        system_message="You are an expert at reading player prop screenshots. Extract structured data precisely."
    ).with_model("openai", "gpt-4o")

    leagues_list = ", ".join([f"{lg['name']} (ID:{lg['id']})" for lg in SUPPORTED_LEAGUES])

    prompt = f"""Analyze this player prop screenshot. Extract ALL prop entries visible.

For EACH entry extract:
- playerName: Full name as displayed
- propType: Map to one of: pass_attempts, shots, shots_on_target, tackles, key_passes, saves, interceptions, blocks, dribbles, fouls_drawn
- line: The numerical line (e.g., 48.5)
- opponentName: The opposing team
- playerTeam: The player's own team
- league: Best guess league name
- leagueId: Match to: {leagues_list}
- venue: "home" (vs) or "away" (@)
- isCombo: true if two players combined (e.g. "Player A + Player B")
- players: only for combos — [{{"name":"A","team":"T1"}},{{"name":"B","team":"T2"}}]

PROP TYPE MAPPING:
- "Passes Attempted" / "Pass Attempts" → pass_attempts
- "Shots" → shots
- "Shots on Target" / "SOT" → shots_on_target
- "Tackles" → tackles
- "Key Passes" / "Assists" → key_passes
- "Saves" / "Goalkeeper Saves" / "Goalie Saves" → saves
- "Interceptions" → interceptions
- "Blocks" → blocks

Return ONLY valid JSON array."""

    image_content = ImageContent(image_base64=image_base64)
    response = await chat.send_message(UserMessage(text=prompt, file_contents=[image_content]))
    response_text = response.strip()

    if response_text.startswith("```"):
        lines = response_text.split("\n")
        response_text = "\n".join(ln for ln in lines if not ln.strip().startswith("```"))

    extracted = json.loads(response_text)
    if not isinstance(extracted, list):
        extracted = [extracted]

    # Resolve players via cache
    resolved_entries = []
    for entry in extracted:
        player_name = entry.get("playerName", "")
        team_hint = (entry.get("playerTeam") or "").lower().strip()
        opponent = entry.get("opponentName", "")
        prop_type = PROP_TYPE_ALIASES.get((entry.get("propType") or "").lower().strip(), entry.get("propType", ""))
        line_val = entry.get("line", 0)
        venue = entry.get("venue", "home")

        # Resolve player
        cache_team_id = None
        if team_hint:
            tid, _ = await get_team_by_name(team_hint)
            if tid:
                cache_team_id = tid

        cached = await get_player_by_name(player_name, cache_team_id)
        player_info = None
        if cached:
            player_info = {"name": cached["name"], "id": cached["playerId"], "team": cached.get("teamName", team_hint), "leagueId": cached.get("leagueId")}

        resolved_entries.append({
            "playerName": player_info["name"] if player_info else player_name,
            "playerId": player_info["id"] if player_info else None,
            "team": player_info["team"] if player_info else (entry.get("playerTeam") or ""),
            "opponent": opponent,
            "propType": prop_type,
            "line": line_val,
            "venue": venue,
            "league": entry.get("league", ""),
            "leagueId": player_info["leagueId"] if player_info and player_info.get("leagueId") else entry.get("leagueId"),
            "resolved": player_info is not None,
            "isCombo": entry.get("isCombo", False),
        })

    return {"entries": resolved_entries}


async def _fetch_data_context(message: str) -> str:
    """Extract player/team entities from the message, fetch real data from cache + API."""
    context_parts = []

    try:
        extractor = LlmChat(
            api_key=EMERGENT_LLM_KEY,
            session_id=f"tac-extract-{uuid.uuid4().hex[:8]}",
            system_message="Extract soccer entities. Return ONLY valid JSON."
        ).with_model("gemini", "gemini-2.5-flash")

        extract_resp = await extractor.send_message(UserMessage(
            text=f"""From this message, extract soccer entities.
Return JSON: {{"players": ["name1", "name2"], "teams": ["team1", "team2"], "needsStats": true/false}}
Set needsStats=true if asking about specific player performance, props, or matchups.
Message: "{message}" """
        ))

        text = extract_resp.strip()
        if text.startswith("```"):
            text = "\n".join(ln for ln in text.split("\n") if not ln.strip().startswith("```"))
        entities = json.loads(text)

        # Detect international context from teams mentioned
        is_intl = False
        for team_name in (entities.get("teams") or []):
            if team_name and await _is_international_context(team_name, ""):
                is_intl = True
                break
        # Also check if player team hints suggest international
        if not is_intl:
            for player_name in (entities.get("players") or []):
                if not player_name:
                    continue
                msg_lower = message.lower()
                # Heuristic: if message mentions country names near player name
                for kw in ["national", "international", "vs ", "qualifier", "nations league", "euro", "world cup"]:
                    if kw in msg_lower:
                        is_intl = True
                        break
                if is_intl:
                    break

        if is_intl:
            context_parts.append("[MATCH CONTEXT: INTERNATIONAL — Prioritize national team stats over club stats.]")

        for player_name in (entities.get("players") or [])[:3]:
            if not player_name:
                continue
            cached = await get_player_by_name(player_name)
            if cached:
                if entities.get("needsStats"):
                    stats_text = await _fetch_player_stats_structured(
                        cached["playerId"], cached["name"], cached.get("teamName", ""), is_intl
                    )
                    if stats_text:
                        context_parts.append(stats_text)
                else:
                    context_parts.append(f"[DATA] {cached['name']}: team={cached.get('teamName')}, id={cached['playerId']}, league={cached.get('leagueId')}")

        for team_name in (entities.get("teams") or [])[:2]:
            if not team_name:
                continue
            # Check national team first
            nat_id, nat_name = await get_national_team_id(team_name.lower().strip())
            if nat_id:
                context_parts.append(f"[DATA] National team: {nat_name} (id={nat_id})")
                fixtures_text = await _fetch_national_team_fixtures(team_name)
                if fixtures_text:
                    context_parts.append(fixtures_text)
                continue

            team_id, canonical = await get_team_by_name(team_name)
            if team_id:
                context_parts.append(f"[DATA] Team: {canonical} (id={team_id})")
                try:
                    fixtures = await api_football_request("fixtures", {"team": team_id, "last": 5})
                    if fixtures:
                        results = []
                        for f in fixtures:
                            home = f.get("teams", {}).get("home", {}).get("name", "")
                            away = f.get("teams", {}).get("away", {}).get("name", "")
                            hg = f.get("goals", {}).get("home", 0)
                            ag = f.get("goals", {}).get("away", 0)
                            dt = f.get("fixture", {}).get("date", "")[:10]
                            results.append(f"    {dt}: {home} {hg}-{ag} {away}")
                        context_parts.append("  Last 5 fixtures:\n" + "\n".join(results))
                except Exception:
                    pass

    except Exception:
        pass

    if context_parts:
        return "\n\n[LIVE SYSTEM DATA]\n" + "\n".join(context_parts) + "\n[END SYSTEM DATA]"
    return ""


async def _fetch_data_for_props(entries: list) -> str:
    """Fetch live stats for extracted prop entries, with international awareness."""
    context_parts = []

    # Detect if this is an international match context
    is_intl = False
    all_teams = set()
    all_opponents = set()
    for e in entries:
        if e.get("team"):
            all_teams.add(e["team"])
        if e.get("opponent"):
            all_opponents.add(e["opponent"])
    for name in list(all_teams) + list(all_opponents):
        if await _is_international_context(name, ""):
            is_intl = True
            break

    if is_intl:
        context_parts.append("[MATCH CONTEXT: INTERNATIONAL — Prioritize national team stats. Club stats are secondary reference only.]")

    for e in entries:
        pid = e.get("playerId")
        if not pid:
            continue
        stats_text = await _fetch_player_stats_structured(pid, e["playerName"], e["team"], is_intl)
        if stats_text:
            context_parts.append(stats_text)

    # Fetch national team fixtures if international
    if is_intl:
        for name in list(all_teams) + list(all_opponents):
            fixtures_text = await _fetch_national_team_fixtures(name)
            if fixtures_text:
                context_parts.append(fixtures_text)

    if context_parts:
        return "\n\n[LIVE SYSTEM DATA]\n" + "\n".join(context_parts) + "\n[END SYSTEM DATA]"
    return ""


@router.post("/tactical/start")
async def tactical_start(req: ChatStartRequest):
    sid = req.session_id or f"tac-{uuid.uuid4().hex[:8]}"
    tactical_sessions[sid] = {
        "history": [],
        "created": datetime.now(timezone.utc).isoformat(),
    }
    return {
        "session_id": sid,
        "message": "**Reverse Tactical online.** Connected to your full player database, live stats, and prediction intelligence.\n\nAsk me anything — or upload a prop screenshot for instant tactical breakdown.",
    }


@router.post("/tactical/message")
async def tactical_message(req: TacticalMessageRequest):
    sid = req.session_id
    session = tactical_sessions.get(sid)
    if not session:
        session = {"history": [], "created": datetime.now(timezone.utc).isoformat()}
        tactical_sessions[sid] = session

    user_msg = (req.message or "").strip()
    has_image = bool(req.image_base64)

    if not user_msg and not has_image:
        raise HTTPException(status_code=400, detail="Empty message")

    # ── IMAGE SCAN MODE ──
    image_context = ""
    scan_entries = None
    if has_image:
        try:
            scan_result = await _extract_image_props(req.image_base64)
            scan_entries = scan_result.get("entries", [])
            if scan_entries:
                # Build readable summary of extracted props
                lines = []
                for e in scan_entries:
                    status = "MATCHED" if e["resolved"] else "UNRESOLVED"
                    lines.append(f"- {e['playerName']} ({e['team']}) vs {e['opponent']}: {e['propType']} line {e['line']} [{e['venue']}] [{status}]")
                image_context = "[EXTRACTED FROM IMAGE]\n" + "\n".join(lines)

                # Fetch live stats for resolved players
                prop_data = await _fetch_data_for_props(scan_entries)
                if prop_data:
                    image_context += prop_data

                if not user_msg:
                    user_msg = "Analyze these props from the screenshot. Give me your tactical take on each one — should I take them or avoid them? Consider matchup, role, game flow, and recent form."
        except Exception as e:
            image_context = f"[Image scan failed: {str(e)[:100]}]"
            if not user_msg:
                user_msg = "I uploaded an image but scanning failed. Please let me know what happened."

    # Fetch text-based data context
    data_context = await _fetch_data_context(user_msg) if not has_image else ""

    # Build conversation history
    history_text = ""
    if session["history"]:
        recent = session["history"][-6:]
        history_lines = []
        for h in recent:
            role = "User" if h["role"] == "user" else "Assistant"
            history_lines.append(f"{role}: {h['content'][:500]}")
        history_text = "\n".join(history_lines)

    # Combine all context
    full_context = ""
    if image_context:
        full_context += f"\n\n{image_context}"
    if data_context:
        full_context += f"\n\n{data_context}"

    # ── GROK: Tactical reasoning ──
    grok_response = ""
    try:
        grok_client = OpenAI(api_key=XAI_API_KEY, base_url="https://api.x.ai/v1")
        grok_messages = [{"role": "system", "content": GROK_SYSTEM}]
        if history_text:
            grok_messages.append({"role": "user", "content": f"[CONVERSATION CONTEXT]\n{history_text}\n[END CONTEXT]"})
            grok_messages.append({"role": "assistant", "content": "Context acknowledged."})

        grok_prompt = user_msg + full_context
        grok_messages.append({"role": "user", "content": grok_prompt})

        loop = aio.get_event_loop()
        def _call_grok():
            return grok_client.chat.completions.create(
                model="grok-3-mini",
                messages=grok_messages,
                max_tokens=2000,
                temperature=0.7,
            )
        grok_result = await aio.wait_for(loop.run_in_executor(None, _call_grok), timeout=45)
        grok_response = grok_result.choices[0].message.content
    except aio.TimeoutError:
        grok_response = "[Primary analysis timed out]"
    except Exception as e:
        grok_response = f"[Primary analysis unavailable: {str(e)[:100]}]"

    # ── Synthesis layer ──
    try:
        gemini = LlmChat(
            api_key=EMERGENT_LLM_KEY,
            session_id=f"tac-synth-{uuid.uuid4().hex[:8]}",
            system_message=SYNTH_SYSTEM,
        ).with_model("gemini", "gemini-2.5-flash")

        synth_prompt = f"""User asked: "{user_msg}"

{full_context if full_context else "[No live data]"}

Tactical analysis:
{grok_response}

Synthesize into a polished response. Format with markdown. NEVER mention any AI model names."""

        if history_text:
            synth_prompt = f"[Conversation context]\n{history_text}\n[End context]\n\n{synth_prompt}"

        final_response = await gemini.send_message(UserMessage(text=synth_prompt))
    except Exception:
        final_response = grok_response if grok_response and not grok_response.startswith("[") else "Analysis failed. Please try again."

    session["history"].append({"role": "user", "content": user_msg})
    session["history"].append({"role": "assistant", "content": final_response})

    if len(session["history"]) > 40:
        session["history"] = session["history"][-20:]

    return {
        "response": final_response,
        "session_id": sid,
        "scanEntries": scan_entries,
    }
