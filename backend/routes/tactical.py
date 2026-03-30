"""
Reverse Tactical — Dual AI Chat Engine (Grok + Gemini)
Grok-4.20-reasoning provides deep tactical reasoning.
Gemini 2.5 Flash synthesizes, fact-checks with live data, and formats.
Connected to the full system: player cache, API-Sports, saved picks.
"""

import json
import uuid
import asyncio as aio
import traceback
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException
from emergentintegrations.llm.chat import LlmChat, UserMessage
from openai import OpenAI

from config import db, EMERGENT_LLM_KEY, XAI_API_KEY, CURRENT_SEASON
from models import ChatStartRequest, ChatMessageRequest
from utils import api_football_request, strip_accents
from cache import get_player_by_name, get_team_by_name, get_national_team_id

router = APIRouter(prefix="/api", tags=["tactical"])

# Session storage for multi-turn conversations
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

STYLE: Be direct, sharp, and opinionated. Back every claim with logic. Challenge weak assumptions. This is premium intelligence — make it feel like insider knowledge."""

GEMINI_SYNTH_SYSTEM = """You are the synthesis layer of REVERSE TACTICAL. Your job:
1. Take Grok's raw tactical analysis and the user's original question
2. Polish it into a clean, structured, authoritative response
3. Integrate any live data provided — quote real numbers, correct any data mismatches
4. Format with clear sections using markdown: **bold** for emphasis, bullet points for lists
5. Add a brief TL;DR at the end for quick scanning
6. Keep Grok's tactical depth — do NOT water it down
7. If Grok and the data disagree, flag it explicitly
8. Be concise but thorough — aim for quality over quantity"""


async def _fetch_data_context(message: str) -> str:
    """Extract player/team entities from the message, fetch real data from cache + API."""
    context_parts = []

    try:
        # Use Gemini to extract entities
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
            lines = text.split("\n")
            text = "\n".join(ln for ln in lines if not ln.strip().startswith("```"))
        entities = json.loads(text)

        # Fetch player data from cache + API
        for player_name in (entities.get("players") or [])[:3]:
            if not player_name:
                continue
            cached = await get_player_by_name(player_name)
            if cached:
                context_parts.append(f"[CACHE] {cached['name']}: team={cached.get('teamName')}, id={cached['playerId']}, league={cached.get('leagueId')}")

                # If stats needed, fetch from API
                if entities.get("needsStats"):
                    for season in [CURRENT_SEASON + 1, CURRENT_SEASON]:
                        try:
                            pdata = await api_football_request("players", {"id": cached["playerId"], "season": season})
                            if pdata and pdata[0].get("statistics"):
                                for stat_entry in pdata[0]["statistics"]:
                                    lg = stat_entry.get("league", {}).get("name", "")
                                    games = stat_entry.get("games", {})
                                    apps = games.get("appearences") or 0
                                    mins = games.get("minutes") or 0
                                    pos = games.get("position", "")
                                    if apps < 1:
                                        continue
                                    stats_line = f"  {lg} {season}: {apps} apps, {mins} min, pos={pos}"
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
                                    }
                                    per_game = {k: round(v / apps, 2) for k, v in raw.items() if v}
                                    stats_line += f"\n    Per game: {per_game}"
                                    context_parts.append(stats_line)
                                break
                        except Exception:
                            continue

        # Fetch team data
        for team_name in (entities.get("teams") or [])[:2]:
            if not team_name:
                continue
            team_id, canonical = await get_team_by_name(team_name)
            if team_id:
                context_parts.append(f"[CACHE] Team: {canonical} (id={team_id})")
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
                        context_parts.append(f"  Last 5 fixtures:\n" + "\n".join(results))
                except Exception:
                    pass
            else:
                nat_id, nat_name = await get_national_team_id(team_name.lower())
                if nat_id:
                    context_parts.append(f"[CACHE] National team: {nat_name} (id={nat_id})")

    except Exception:
        pass

    if context_parts:
        return "\n\n[LIVE SYSTEM DATA]\n" + "\n".join(context_parts) + "\n[END SYSTEM DATA]"
    return ""


@router.post("/tactical/start")
async def tactical_start(req: ChatStartRequest):
    """Initialize a new Reverse Tactical session."""
    sid = req.session_id or f"tac-{uuid.uuid4().hex[:8]}"
    tactical_sessions[sid] = {
        "history": [],
        "created": datetime.now(timezone.utc).isoformat(),
    }
    return {
        "session_id": sid,
        "message": "**REVERSE TACTICAL online.** Dual AI engine active — Grok for tactical depth, Gemini for data synthesis. I'm connected to your full player database, live stats, and prediction system.\n\nAsk me anything: player matchups, prop analysis, what-if scenarios, tactical breakdowns, or multi-player comparisons. I keep full context across follow-ups.",
    }


@router.post("/tactical/message")
async def tactical_message(req: ChatMessageRequest):
    """Process a message through the dual AI pipeline: Grok reasons, Gemini synthesizes."""
    sid = req.session_id
    session = tactical_sessions.get(sid)
    if not session:
        session = {"history": [], "created": datetime.now(timezone.utc).isoformat()}
        tactical_sessions[sid] = session

    user_msg = req.message.strip()
    if not user_msg:
        raise HTTPException(status_code=400, detail="Empty message")

    # 1. Fetch live data context
    data_context = await _fetch_data_context(user_msg)

    # Build conversation history for context
    history_text = ""
    if session["history"]:
        recent = session["history"][-6:]  # Last 3 exchanges
        history_lines = []
        for h in recent:
            role = "User" if h["role"] == "user" else "Assistant"
            history_lines.append(f"{role}: {h['content'][:500]}")
        history_text = "\n".join(history_lines)

    # 2. Grok: Deep tactical reasoning
    grok_response = ""
    try:
        grok_client = OpenAI(api_key=XAI_API_KEY, base_url="https://api.x.ai/v1")

        grok_messages = [{"role": "system", "content": GROK_SYSTEM}]
        if history_text:
            grok_messages.append({"role": "user", "content": f"[CONVERSATION CONTEXT]\n{history_text}\n[END CONTEXT]"})
            grok_messages.append({"role": "assistant", "content": "Context acknowledged. Ready for the next question."})

        grok_prompt = user_msg
        if data_context:
            grok_prompt += f"\n\n{data_context}"

        grok_messages.append({"role": "user", "content": grok_prompt})

        loop = aio.get_event_loop()

        def _call_grok():
            return grok_client.chat.completions.create(
                model="grok-3-mini",
                messages=grok_messages,
                max_tokens=2000,
                temperature=0.7,
            )

        grok_result = await aio.wait_for(
            loop.run_in_executor(None, _call_grok),
            timeout=45
        )
        grok_response = grok_result.choices[0].message.content
    except aio.TimeoutError:
        grok_response = "[Grok timed out — proceeding with Gemini analysis only]"
    except Exception as e:
        grok_response = f"[Grok unavailable: {str(e)[:100]}]"

    # 3. Gemini: Synthesize Grok's analysis with data
    try:
        gemini = LlmChat(
            api_key=EMERGENT_LLM_KEY,
            session_id=f"tac-synth-{uuid.uuid4().hex[:8]}",
            system_message=GEMINI_SYNTH_SYSTEM,
        ).with_model("gemini", "gemini-2.5-flash")

        synth_prompt = f"""User asked: "{user_msg}"

{data_context if data_context else "[No live data available for this query]"}

Grok's tactical analysis:
{grok_response}

Synthesize this into a polished, authoritative response. Keep Grok's tactical insights but ensure data accuracy. Format cleanly with markdown."""

        if history_text:
            synth_prompt = f"[Conversation context]\n{history_text}\n[End context]\n\n{synth_prompt}"

        final_response = await gemini.send_message(UserMessage(text=synth_prompt))
    except Exception as e:
        # If Gemini fails, return Grok's raw analysis
        final_response = grok_response if grok_response and not grok_response.startswith("[") else f"Error: {str(e)}"

    # 4. Store in session history
    session["history"].append({"role": "user", "content": user_msg})
    session["history"].append({"role": "assistant", "content": final_response})

    # Keep history manageable (last 20 exchanges)
    if len(session["history"]) > 40:
        session["history"] = session["history"][-20:]

    return {
        "response": final_response,
        "session_id": sid,
        "sources": {
            "grok": bool(grok_response and not grok_response.startswith("[")),
            "gemini": True,
            "liveData": bool(data_context),
        },
    }
