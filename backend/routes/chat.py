import json
import uuid
from fastapi import APIRouter, HTTPException
from emergentintegrations.llm.chat import LlmChat, UserMessage

from config import EMERGENT_LLM_KEY, CURRENT_SEASON, chat_sessions
from models import ChatStartRequest, ChatMessageRequest, NaturalQueryRequest
from utils import api_football_request

router = APIRouter(prefix="/api", tags=["chat"])

TACTICAL_SEARCH_SYSTEM = """You are an elite soccer tactical analyst and prop betting strategist. You think step-by-step, quote evidence, and never make vague claims.

CORE REASONING FRAMEWORK — Apply this to EVERY question:

1. ROLE-BASED ANALYSIS: Always identify a player's specific role (Deep-Lying Playmaker, Box-to-Box CM, Inside Forward, Target Striker, etc.) and explain how that role drives their stat profile. A CDM will ALWAYS have more passes than a winger. A striker will ALWAYS have more shots than a centre-back. Start here.

2. MATCHUP INTELLIGENCE:
- Opponent pressing intensity (PPDA concept): Aggressive press (PPDA 6-9) = less time on ball = fewer passes, more turnovers. Passive/low-block (PPDA 13+) = more possession = inflated pass/touch numbers.
- Formation matchups: 5-back = ultra defensive = massive pass boost for dominant team. 4-3-3 high press = open game = more dribble space but fewer safe passes.
- Opponent defensive shape: Do they double the wide areas (kills dribbles)? Do they sit narrow (opens wing play)?

3. SUBSTITUTION RISK: Always consider minutes risk. A player subbed at 60' loses ~33% of their stat volume. Check if the team is heavy favorites (blowout sub risk) or if the player has a recent pattern of early subs.

4. GAME FLOW DYNAMICS:
- First-to-score impact: Some teams sit back after scoring (fewer passes for everyone), others keep pressing.
- Trailing dynamics: Teams chasing often go more direct (bypass midfield = fewer CM passes, more crosses/shots).
- Score state changes EVERYTHING about individual stat distributions.

5. SCENARIO THINKING: For any prediction question, consider:
- Base case (most likely game flow)
- Blowout scenario (team dominates → subs come on)
- Trailing scenario (team falls behind → tactical shift)
- Cagey game (tight, low-possession → suppressed stats)
- Sensitivity: Would the pick survive a red card, early sub, or parking the bus?

6. EVIDENCE-BASED: When discussing player stats, reference specific numbers, averages, splits (home/away), and trends. Never say "he's been good recently" — say "he's averaged 4.2 shots over his last 5 home games."

7. UNCERTAINTY: When data is limited or the matchup is ambiguous, say so. "Small sample — only 3 games in this league so far" is more useful than false confidence.

TACTICAL VOCABULARY: Use real concepts — low blocks, half-spaces, pressing triggers, progressive passes, build-up structure, defensive transition, positive transition, counter-press, deep completions, xT (expected threat), and zone 14 activity.

Be concise but substantive. Every answer should teach the user something they couldn't figure out from basic stats alone."""


@router.post("/chat/start")
async def chat_start(req: ChatStartRequest):
    sid = req.session_id or str(uuid.uuid4())
    chat = LlmChat(
        api_key=EMERGENT_LLM_KEY,
        session_id=sid,
        system_message=TACTICAL_SEARCH_SYSTEM
    )
    chat.with_model("gemini", "gemini-2.5-flash")
    chat_sessions[sid] = chat
    return {
        "session_id": sid,
        "message": "Tactical Search online. I run the same reasoning engine as the prediction system — role analysis, PPDA matchups, sub risk, game flow dynamics, and scenario testing. What do you want to break down?"
    }


@router.post("/chat/message")
async def chat_message(req: ChatMessageRequest):
    chat = chat_sessions.get(req.session_id)
    if not chat:
        chat = LlmChat(
            api_key=EMERGENT_LLM_KEY,
            session_id=req.session_id,
            system_message=TACTICAL_SEARCH_SYSTEM
        )
        chat.with_model("gemini", "gemini-2.5-flash")
        chat_sessions[req.session_id] = chat

    # =============================================
    # UPGRADE #2: Data-aware chat — fetch live data
    # =============================================
    # Use a quick LLM call to extract player/team names, then fetch real API-Sports data
    live_context = ""
    try:
        # Step 1: Extract entities from user message
        extractor = LlmChat(
            api_key=EMERGENT_LLM_KEY,
            session_id=f"extract-{uuid.uuid4().hex[:8]}",
            system_message="Extract soccer entities from the user message. Return ONLY valid JSON."
        )
        extractor.with_model("gemini", "gemini-2.5-flash")
        extract_prompt = f"""From this message, extract any soccer player names, team names, or league references.
Return JSON: {{"playerName": "name or null", "teamName": "name or null", "leagueName": "name or null", "needsData": true/false}}
Set needsData=true if the user is asking about a specific player's stats, matchup, or performance.
Message: "{req.message}" """
        extract_resp = await extractor.send_message(UserMessage(text=extract_prompt))
        extract_text = extract_resp.strip()
        if extract_text.startswith("```"):
            lines = extract_text.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            extract_text = "\n".join(lines)
        entities = json.loads(extract_text)

        if entities.get("needsData") and entities.get("playerName"):
            player_name = entities["playerName"]
            # Step 2: Search for player in API-Sports
            search_data = await api_football_request("players", {"search": player_name, "league": 39})
            if not search_data:
                # Try broader search without league filter
                for lid in [140, 135, 78, 61, 253, 262]:
                    search_data = await api_football_request("players", {"search": player_name, "league": lid})
                    if search_data:
                        break
            if not search_data:
                # Last resort: search by last name
                last_name = player_name.split()[-1] if " " in player_name else player_name
                search_data = await api_football_request("players", {"search": last_name})

            if search_data:
                player = search_data[0]
                p_id = player.get("player", {}).get("id")
                p_name = player.get("player", {}).get("name", player_name)

                # Step 3: Fetch real stats from API-Sports
                stats_parts = []

                # Get player season stats (try multiple seasons)
                for s in [CURRENT_SEASON + 1, CURRENT_SEASON, CURRENT_SEASON - 1]:
                    try:
                        pdata = await api_football_request("players", {"id": p_id, "season": s})
                        if pdata and pdata[0].get("statistics"):
                            for stat_entry in pdata[0]["statistics"]:
                                league_name = stat_entry.get("league", {}).get("name", "")
                                games = stat_entry.get("games", {})
                                minutes = games.get("minutes") or 0
                                appearances = games.get("appearences") or 0
                                position = games.get("position", "")
                                if appearances < 1:
                                    continue

                                # Extract key stats with per-90 normalization
                                raw_stats = {}
                                per90 = {}
                                stat_cats = {
                                    "passes.total": stat_entry.get("passes", {}).get("total"),
                                    "shots.total": stat_entry.get("shots", {}).get("total"),
                                    "shots.on": stat_entry.get("shots", {}).get("on"),
                                    "tackles.total": stat_entry.get("tackles", {}).get("total"),
                                    "passes.key": stat_entry.get("passes", {}).get("key"),
                                    "dribbles.attempts": stat_entry.get("dribbles", {}).get("attempts"),
                                    "fouls.drawn": stat_entry.get("fouls", {}).get("drawn"),
                                    "tackles.interceptions": stat_entry.get("tackles", {}).get("interceptions"),
                                }
                                for k, v in stat_cats.items():
                                    if v is not None and v > 0:
                                        raw_stats[k] = v
                                        per_game = round(v / appearances, 2)
                                        p90 = round((v / minutes) * 90, 2) if minutes > 0 else 0
                                        per90[k] = f"{per_game}/game ({p90}/90)"

                                if raw_stats:
                                    stats_parts.append(f"  {league_name} {s}: {appearances} apps, {minutes} min, position: {position}")
                                    for k, v in per90.items():
                                        stats_parts.append(f"    {k}: {v}")
                    except Exception:
                        continue

                # Get recent fixtures for the player's team
                if player.get("statistics"):
                    team_id = player["statistics"][-1].get("team", {}).get("id")
                    if team_id:
                        try:
                            fixtures = await api_football_request("fixtures", {"team": team_id, "last": 5})
                            if fixtures:
                                stats_parts.append(f"\n  Last 5 team results:")
                                for f in fixtures:
                                    home = f.get("teams", {}).get("home", {}).get("name", "")
                                    away = f.get("teams", {}).get("away", {}).get("name", "")
                                    hg = f.get("goals", {}).get("home", 0)
                                    ag = f.get("goals", {}).get("away", 0)
                                    date = f.get("fixture", {}).get("date", "")[:10]
                                    stats_parts.append(f"    {date}: {home} {hg}-{ag} {away}")
                        except Exception:
                            pass

                if stats_parts:
                    live_context = f"\n\n[LIVE API-SPORTS DATA for {p_name}]\n" + "\n".join(stats_parts) + "\n[END LIVE DATA]\n\nUse this REAL data in your analysis. Quote specific numbers from above."

    except Exception:
        pass  # If data fetch fails, proceed without context — don't break the chat

    try:
        augmented_message = req.message + live_context if live_context else req.message
        response = await chat.send_message(UserMessage(text=augmented_message))
        return {"response": response, "session_id": req.session_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/parse-query")
async def parse_natural_query(req: NaturalQueryRequest):
    chat = LlmChat(
        api_key=EMERGENT_LLM_KEY,
        session_id=f"parse-{uuid.uuid4().hex[:8]}",
        system_message="You are an expert at parsing soccer prop betting queries. Return ONLY valid JSON."
    )
    chat.with_model("gemini", "gemini-2.5-flash")
    prompt = f"""Parse this soccer prop query into a structured object: "{req.query}"
Extract: playerName, opponentName, venue (home/away), propType, line (number).
Valid propType values: pass_attempts, shots, shots_on_target, tackles, key_passes, saves, interceptions, blocks, dribbles, fouls_drawn.
Return ONLY valid JSON like: {{"playerName": "...", "opponentName": "...", "venue": "home", "propType": "pass_attempts", "line": 0}}"""
    try:
        response = await chat.send_message(UserMessage(text=prompt))
        text = response.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines)
        return json.loads(text)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
