import os
import json
import httpx
import uuid
import asyncio as aio
import bcrypt
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
from dotenv import load_dotenv
from emergentintegrations.llm.chat import LlmChat, UserMessage
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

MONGO_URL = os.environ.get("MONGO_URL")
DB_NAME = os.environ.get("DB_NAME")
EMERGENT_LLM_KEY = os.environ.get("EMERGENT_LLM_KEY")
API_FOOTBALL_KEY = os.environ.get("API_FOOTBALL_KEY")
API_FOOTBALL_BASE = "https://v3.football.api-sports.io"
WHOP_API_KEY = os.environ.get("WHOP_API_KEY")
WHOP_COMPANY_ID = os.environ.get("WHOP_COMPANY_ID")
OWNER_EMAIL = (os.environ.get("OWNER_EMAIL") or "josselj001@gmail.com").lower().strip()

LIFETIME_SUB_EMAILS = [
    "faron2allen@gmail.com", "jossel0701@gmail.com", "josselj001@gmail.com",
    "brayanfgaleas@icloud.com", "odr310@gmail.com",
    "joseharo197@gmail.com", "rijulgauchan1@gmail.com", "gordo0210@icloud.com"
]
LIFETIME_SUB_EMAILS = [e.lower() for e in LIFETIME_SUB_EMAILS]

whop_cache = None
whop_cache_time = 0

mongo_client = AsyncIOMotorClient(MONGO_URL)
db = mongo_client[DB_NAME]


@app.on_event("startup")
async def seed_grants():
    seed_data = [{"email": OWNER_EMAIL, "access_type": "Owner"}]
    seed_data.extend([{"email": e, "access_type": "Lifetime"} for e in LIFETIME_SUB_EMAILS if e != OWNER_EMAIL])
    for item in seed_data:
        await db.manual_access_grants.update_one(
            {"email": item["email"]},
            {"$set": item},
            upsert=True
        )

SUPPORTED_LEAGUES = [
    {"id": 39, "name": "Premier League", "type": "Domestic"},
    {"id": 140, "name": "La Liga", "type": "Domestic"},
    {"id": 135, "name": "Serie A", "type": "Domestic"},
    {"id": 78, "name": "Bundesliga", "type": "Domestic"},
    {"id": 61, "name": "Ligue 1", "type": "Domestic"},
    {"id": 40, "name": "Championship", "type": "Domestic"},
    {"id": 188, "name": "A-League", "type": "Domestic"},
    {"id": 253, "name": "MLS", "type": "Domestic"},
    {"id": 262, "name": "Liga MX", "type": "Domestic"},
    {"id": 128, "name": "Liga Profesional Argentina", "type": "Domestic"},
    {"id": 71, "name": "Brasileirao", "type": "Domestic"},
    {"id": 307, "name": "Saudi Pro League", "type": "Domestic"},
    {"id": 254, "name": "NWSL", "type": "Domestic"},
    {"id": 2, "name": "Champions League", "type": "International Club"},
    {"id": 3, "name": "Europa League", "type": "International Club"},
    {"id": 1, "name": "World Cup", "type": "International Team"},
    {"id": 34, "name": "World Cup Qualifiers (UEFA)", "type": "International Team"},
    {"id": 30, "name": "World Cup Qualifiers (CONMEBOL)", "type": "International Team"},
    {"id": 32, "name": "World Cup Qualifiers (CONCACAF)", "type": "International Team"},
    {"id": 31, "name": "World Cup Qualifiers (CAF)", "type": "International Team"},
    {"id": 33, "name": "World Cup Qualifiers (AFC)", "type": "International Team"},
    {"id": 4, "name": "Euro Championship", "type": "International Team"},
    {"id": 96, "name": "Euro Qualifiers", "type": "International Team"},
    {"id": 9, "name": "Copa America", "type": "International Team"},
    {"id": 5, "name": "UEFA Nations League", "type": "International Team"},
    {"id": 13, "name": "CONCACAF Nations League", "type": "International Team"},
    {"id": 6, "name": "Africa Cup of Nations", "type": "International Team"},
    {"id": 115, "name": "AFCON Qualifiers", "type": "International Team"},
    {"id": 7, "name": "Asian Cup", "type": "International Team"},
    {"id": 10, "name": "International Friendlies", "type": "International Team"},
]

CURRENT_SEASON = 2025

# Chat sessions stored in memory
chat_sessions: dict = {}


# ======= WHOP AUTH SYSTEM =======

async def fetch_whop_memberships():
    global whop_cache, whop_cache_time
    now = time.time()
    if whop_cache is not None and (now - whop_cache_time < 60):
        return whop_cache

    all_memberships = []
    page = 1
    async with httpx.AsyncClient(timeout=15.0) as client:
        while True:
            url = f"https://api.whop.com/api/v2/memberships?company_id={WHOP_COMPANY_ID}&per_page=50&page={page}"
            resp = await client.get(url, headers={"Authorization": f"Bearer {WHOP_API_KEY}", "Accept": "application/json"})
            if resp.status_code != 200:
                break
            data = resp.json()
            memberships = data.get("data", [])
            all_memberships.extend(memberships)
            total_pages = data.get("pagination", {}).get("total_page", 1)
            if page >= total_pages:
                break
            page += 1

    whop_cache = all_memberships
    whop_cache_time = now
    return all_memberships


async def check_access(email_lower: str):
    if email_lower == OWNER_EMAIL:
        return "Owner"

    # Check lifetime subs
    if email_lower in LIFETIME_SUB_EMAILS:
        return "Lifetime"

    # Check manual grants in MongoDB
    grant = await db.manual_access_grants.find_one({"email": email_lower}, {"_id": 0})
    if grant:
        return grant.get("access_type", "Manual")

    # Check Whop memberships
    try:
        all_memberships = await fetch_whop_memberships()
        user_memberships = [m for m in all_memberships if (m.get("email") or "").lower() == email_lower]
        for m in user_memberships:
            company_match = m.get("company_id") == WHOP_COMPANY_ID or m.get("page_id") == WHOP_COMPANY_ID
            if not company_match:
                continue
            status = (m.get("status") or "").lower()
            if status in ["active", "trialing", "completed"] or m.get("valid") is True:
                return "Premium"
    except Exception:
        pass

    return None


async def create_session(email: str, access_type: str):
    session_token = str(uuid.uuid4())
    await db.sessions.update_one(
        {"email": email},
        {"$set": {"email": email, "session_token": session_token, "access_type": access_type, "last_active": datetime.now(timezone.utc).isoformat()}},
        upsert=True
    )
    return session_token


class VerifyWhopRequest(BaseModel):
    email: str


@app.post("/api/auth/verify-whop")
async def verify_whop(req: VerifyWhopRequest):
    email_lower = req.email.lower().strip()
    access_type = await check_access(email_lower)

    if not access_type:
        return {"verified": False, "email": email_lower, "message": "No active membership found. Please subscribe via Whop to gain access."}

    # Owner bypasses password
    if email_lower == OWNER_EMAIL:
        token = await create_session(email_lower, "Owner")
        return {"verified": True, "email": email_lower, "session_token": token, "access_type": "Owner", "message": "Premium access granted"}

    # Check if user has password set
    user_record = await db.users.find_one({"email": email_lower}, {"_id": 0})
    if user_record and user_record.get("passwordHash"):
        return {"requires_password": True, "email": email_lower}

    return {"requires_password_setup": True, "email": email_lower, "access_type": access_type}


class LoginRequest(BaseModel):
    email: str
    password: str


@app.post("/api/auth/login")
async def login(req: LoginRequest):
    email_lower = req.email.lower().strip()
    user_record = await db.users.find_one({"email": email_lower}, {"_id": 0, "passwordHash": 1, "email": 1})

    if not user_record or not user_record.get("passwordHash"):
        raise HTTPException(status_code=401, detail="Invalid credentials or password not set.")

    if not bcrypt.checkpw(req.password.encode("utf-8"), user_record["passwordHash"].encode("utf-8")):
        raise HTTPException(status_code=401, detail="Invalid password.")

    access_type = await check_access(email_lower)
    if not access_type:
        raise HTTPException(status_code=401, detail="Your subscription has expired or been revoked.")

    token = await create_session(email_lower, access_type)
    return {"verified": True, "email": email_lower, "session_token": token, "access_type": access_type, "message": "Login successful"}


class SetPasswordRequest(BaseModel):
    email: str
    password: str


@app.post("/api/auth/set-password")
async def set_password(req: SetPasswordRequest):
    email_lower = req.email.lower().strip()
    if len(req.password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters.")

    access_type = await check_access(email_lower)
    if not access_type:
        raise HTTPException(status_code=401, detail="No active subscription found.")

    salt = bcrypt.gensalt()
    password_hash = bcrypt.hashpw(req.password.encode("utf-8"), salt).decode("utf-8")

    await db.users.update_one(
        {"email": email_lower},
        {"$set": {"email": email_lower, "passwordHash": password_hash, "created_at": datetime.now(timezone.utc).isoformat()}},
        upsert=True
    )

    token = await create_session(email_lower, access_type)
    return {"verified": True, "email": email_lower, "session_token": token, "access_type": access_type, "message": "Password set successfully"}


class VerifySessionRequest(BaseModel):
    email: str
    session_token: str


@app.post("/api/auth/verify-session")
async def verify_session(req: VerifySessionRequest):
    email_lower = req.email.lower().strip()
    session = await db.sessions.find_one({"email": email_lower, "session_token": req.session_token}, {"_id": 0})
    if not session:
        return {"valid": False}

    access_type = await check_access(email_lower)
    if not access_type:
        await db.sessions.delete_one({"email": email_lower, "session_token": req.session_token})
        return {"valid": False}

    return {"valid": True, "access_type": access_type}


@app.post("/api/auth/logout")
async def logout(req: VerifySessionRequest):
    await db.sessions.delete_one({"email": req.email.lower().strip(), "session_token": req.session_token})
    return {"success": True}


async def api_football_request(endpoint: str, params: dict = None):
    headers = {
        "x-apisports-key": API_FOOTBALL_KEY,
        "x-rapidapi-key": API_FOOTBALL_KEY,
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(f"{API_FOOTBALL_BASE}/{endpoint}", headers=headers, params=params or {})
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail=f"API-Sports error: {resp.text}")
        data = resp.json()
        if data.get("errors") and len(data["errors"]) > 0:
            raise HTTPException(status_code=400, detail=f"API-Sports error: {json.dumps(data['errors'])}")
        return data.get("response", [])


@app.get("/api/health")
async def health():
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/api/leagues")
async def get_leagues():
    return {"leagues": SUPPORTED_LEAGUES}


@app.get("/api/leagues/{league_id}/teams")
async def get_teams_by_league(league_id: int, season: int = CURRENT_SEASON):
    try:
        data = await api_football_request("teams", {"league": league_id, "season": season})
        teams = [{"id": item["team"]["id"], "name": item["team"]["name"], "logo": item["team"].get("logo", "")} for item in data]
        return {"teams": teams}
    except Exception as e:
        # Try previous season
        try:
            data = await api_football_request("teams", {"league": league_id, "season": season - 1})
            teams = [{"id": item["team"]["id"], "name": item["team"]["name"], "logo": item["team"].get("logo", "")} for item in data]
            return {"teams": teams}
        except Exception:
            raise HTTPException(status_code=500, detail=str(e))


class PlayerSearchRequest(BaseModel):
    query: str
    league_id: Optional[int] = None
    season: Optional[int] = None


@app.post("/api/players/search")
async def search_players(req: PlayerSearchRequest):
    if len(req.query) < 3:
        return {"players": []}
    season = req.season or CURRENT_SEASON
    params = {"search": req.query}
    if req.league_id:
        params["league"] = req.league_id
        params["season"] = season
        endpoint = "players"
    else:
        endpoint = "players/profiles"
    try:
        data = await api_football_request(endpoint, params)
        if not data and req.league_id:
            # Try previous season
            params["season"] = season - 1
            data = await api_football_request(endpoint, params)
        if not data and req.league_id:
            params["season"] = season - 2
            data = await api_football_request(endpoint, params)
        if not data and req.league_id:
            # Fallback to global search
            data = await api_football_request("players/profiles", {"search": req.query})
        players = []
        for item in (data or []):
            p = item.get("player", {})
            stats = item.get("statistics", [])
            team_id = stats[0]["team"]["id"] if stats else 0
            team_name = stats[0]["team"]["name"] if stats else "Unknown"
            players.append({
                "id": p.get("id", 0),
                "name": p.get("name", ""),
                "firstname": p.get("firstname", ""),
                "lastname": p.get("lastname", ""),
                "age": p.get("age", 0),
                "nationality": p.get("nationality", ""),
                "photo": p.get("photo", ""),
                "teamId": team_id,
                "teamName": team_name,
            })
        return {"players": players}
    except HTTPException:
        raise
    except Exception as e:
        return {"players": [], "error": str(e)}


@app.get("/api/player/{player_id}/stats")
async def get_player_stats(player_id: int, season: int = CURRENT_SEASON):
    for s in [season, season - 1, season - 2]:
        try:
            data = await api_football_request("players", {"id": player_id, "season": s})
            if data:
                return {"stats": data[0]}
        except Exception:
            continue
    return {"stats": None}


async def fetch_match_player_stats(fixture_id: int, player_id: int):
    try:
        data = await api_football_request("fixtures/players", {"fixture": fixture_id})
        for team_data in (data or []):
            for p in team_data.get("players", []):
                if p.get("player", {}).get("id") == player_id:
                    return p
    except Exception:
        pass
    return None


async def get_recent_match_history(player_id: int, team_id: int, count: int = 20):
    try:
        fixtures = await api_football_request("fixtures", {"team": team_id, "last": count})

        async def process_fixture(f):
            fid = f["fixture"]["id"]
            home_team_id = f.get("teams", {}).get("home", {}).get("id")
            venue = "home" if home_team_id == team_id else "away"
            ps = await fetch_match_player_stats(fid, player_id)
            if ps:
                return {
                    "fixture": f["fixture"],
                    "league": f.get("league"),
                    "teams": f.get("teams"),
                    "goals": f.get("goals"),
                    "playerStats": ps,
                    "venue": venue
                }
            return None

        # Fetch all match stats in parallel (batches of 5 to avoid rate limits)
        results = []
        for i in range(0, len(fixtures[:count]), 5):
            batch = fixtures[i:i+5]
            batch_results = await aio.gather(*[process_fixture(f) for f in batch], return_exceptions=True)
            results.extend([r for r in batch_results if r and not isinstance(r, Exception)])
        return results
    except Exception:
        return []


class PredictionRequest(BaseModel):
    leagueId: int
    playerId: int
    playerName: str
    teamId: int
    opponentId: int
    opponentName: str
    venue: str = "home"
    propType: str = "pass_attempts"
    line: float = 0


@app.post("/api/predict")
async def predict(req: PredictionRequest):
    try:
        # 1. Gather data from API-Sports
        player_stats = None
        for s in [CURRENT_SEASON, CURRENT_SEASON - 1, CURRENT_SEASON - 2]:
            try:
                data = await api_football_request("players", {"id": req.playerId, "season": s})
                if data:
                    player_stats = data[0]
                    break
            except Exception:
                continue

        actual_team_id = req.teamId
        if actual_team_id == 0 and player_stats:
            stats_list = player_stats.get("statistics", [])
            if stats_list:
                actual_team_id = stats_list[0].get("team", {}).get("id", 0)

        match_history = await get_recent_match_history(req.playerId, actual_team_id, 20)

        league_id = req.leagueId
        if not league_id and player_stats:
            stats_list = player_stats.get("statistics", [])
            if stats_list:
                league_id = stats_list[0].get("league", {}).get("id", 39)

        # Fetch supplementary data in PARALLEL for speed
        async def safe_fetch(endpoint, params, fallback=None):
            try:
                return await api_football_request(endpoint, params)
            except Exception:
                return fallback

        team_stats_task = safe_fetch("teams/statistics", {"team": actual_team_id, "league": league_id, "season": CURRENT_SEASON})
        opponent_stats_task = safe_fetch("teams/statistics", {"team": req.opponentId, "league": league_id, "season": CURRENT_SEASON})
        h2h_task = safe_fetch("fixtures/headtohead", {"h2h": f"{actual_team_id}-{req.opponentId}", "last": 5}, [])
        standings_task = safe_fetch("standings", {"league": league_id, "season": CURRENT_SEASON})
        fixtures_task = safe_fetch("fixtures", {"team": actual_team_id, "last": 20}, [])

        team_stats, opponent_stats, h2h_data, standings_raw, team_fixtures = await aio.gather(
            team_stats_task, opponent_stats_task, h2h_task, standings_task, fixtures_task
        )

        standings = []
        if standings_raw:
            try:
                standings = standings_raw[0].get("league", {}).get("standings", [[]])[0]
            except (IndexError, AttributeError):
                pass

        odds = None
        fixture_metadata = None
        upcoming = [f for f in (team_fixtures or []) if f.get("fixture", {}).get("status", {}).get("short") == "NS"]
        if upcoming:
            uf = upcoming[0]
            fixture_metadata = {
                "round": uf.get("league", {}).get("round", ""),
                "venue": uf.get("fixture", {}).get("venue", {}).get("name", ""),
                "city": uf.get("fixture", {}).get("venue", {}).get("city", ""),
            }
            try:
                odds_data = await api_football_request("odds", {"fixture": uf["fixture"]["id"]})
                if odds_data:
                    bookmakers = odds_data[0].get("bookmakers", [])
                    if bookmakers:
                        bets = bookmakers[0].get("bets", [])
                        odds = next((b for b in bets if b.get("name") == "Match Winner"), None)
            except Exception:
                pass

        historical_data = {
            "playerStats": player_stats,
            "teamStats": team_stats,
            "opponentStats": opponent_stats,
            "h2hData": h2h_data,
            "standings": standings,
            "teamFixtures": team_fixtures,
            "matchHistory": match_history,
            "odds": odds,
            "fixtureMetadata": fixture_metadata,
        }

        # 2. Send to Gemini for AI analysis
        chat = LlmChat(
            api_key=EMERGENT_LLM_KEY,
            session_id=f"predict-{uuid.uuid4().hex[:8]}",
            system_message="""You are an elite soccer prop betting analyst used by sharp bettors. You produce structured JSON predictions with deep, convincing research that explains WHY a prop should hit or miss — not just surface-level stats.

Your analysis MUST cover these layers (the kind of breakdown that makes people trust the pick even if it misses):

1. MATCHUP EDGE: How the opponent's defensive weaknesses/strengths directly impact THIS specific stat. What does the opponent concede in this category? Are they a high-press team that forces turnovers (fewer passes) or a low-block team that invites possession (more passes)? Cite actual opponent defensive data.

2. PLAYER ROLE & USAGE: Is the player the primary creator, set-piece taker, focal point of attacks? How does their tactical role guarantee volume? What percentage of team actions flow through them? What's their per-90 average and consistency?

3. PACE & GAME SCRIPT: What's the expected tempo? If one team is a heavy favorite, does that change possession split? Does a blowout risk reduce minutes? Will the game be open (more chances) or cagey (fewer touches)?

4. VENUE SPLITS: How does this player perform at home vs away for THIS specific stat? Is there a significant venue differential? Show the numbers.

5. FORM & MOMENTUM: Recent 5-game rolling average vs season average. Is the player trending up or down? Any tactical changes, new teammates, or system shifts that explain the trend?

6. HEAD-TO-HEAD HISTORY: What has this player done against this specific opponent historically? Any patterns?

7. FLOOR/CEILING ANALYSIS: What's the realistic worst case (floor) and best case (ceiling) for this stat? What scenarios lead to each?

ALWAYS return valid JSON matching this exact structure:
{
  "player": { "id": number, "name": string, "team": string, "role": string, "position": string },
  "opponent": string,
  "league": string,
  "propType": string,
  "line": number,
  "projectedValue": number,
  "recommendation": "over" or "under",
  "confidenceScore": number (0-100),
  "confidenceLevel": "Low" | "Medium" | "High" | "Very High",
  "confidenceInterval": [number, number],
  "explanation": string (2-3 sentence executive summary),
  "recentSamples": [{ "date": string, "opponent": string, "value": number, "minutesPlayed": number, "matchDifficulty": "low"|"medium"|"high", "venue": "home"|"away" }],
  "tacticalAnalysis": { "pressingStyle": string, "possessionImpact": string, "spaceAndTime": string },
  "bayesianMetrics": { "priorMean": number, "momentumEffect": number, "covariateAdjustment": number, "reversalFlag": "stable"|"upward_reversal_likely"|"downward_reversal_likely" },
  "probabilityCurve": [{ "value": number, "probability": number }],
  "tacticalAlerts": [{ "type": "injury"|"lineup"|"tactical", "message": string, "severity": "low"|"medium"|"high" }],
  "matchupBreakdown": string (3-4 paragraphs covering opponent defensive profile, how they concede this specific stat, and what creates the edge or risk),
  "venueAnalysis": string (home vs away splits for this stat with numbers),
  "formTrend": string (recent form analysis — rolling average, trend direction, explaining WHY the trend exists),
  "floorCeiling": string (worst/best case scenarios with reasoning),
  "sharpSummary": string (the key 2-3 sentences a sharp bettor would focus on — the CORE reason this pick has or lacks value),
  "reasoning": string (comprehensive 4-6 paragraph analysis tying everything together — matchup, role, game script, venue, form, H2H, floor/ceiling. This should read like a professional analyst breakdown that makes the reader trust the research)
}

CRITICAL RULES for recentSamples:
- You MUST include AT LEAST 15 entries (up to 20 if data available)
- Each entry MUST have a "venue" field set to "home" or "away"
- Sort by date descending (most recent first)"""
        )
        chat.with_model("gemini", "gemini-2.5-flash")

        prompt = f"""Analyze this soccer player prop bet using ONLY the provided API data:

Player: {req.playerName}
Team ID: {req.teamId}
Opponent: {req.opponentName}
Venue: {req.venue}
Prop Type: {req.propType}
Line: {req.line}

Historical Data (from API-Sports):
{json.dumps(historical_data, default=str)[:18000]}

CRITICAL INSTRUCTIONS:
1. Use ONLY the provided data. Extract actual stat values from match history for recentSamples.
2. For propType '{req.propType}': map to the CORRECT API-Sports stat field:
   - pass_attempts → statistics[].passes.total
   - shots → statistics[].shots.total
   - shots_on_target → statistics[].shots.on
   - tackles → statistics[].tackles.total
   - key_passes → statistics[].passes.key
   - saves → statistics[].goals.saves (goalkeeper stat)
   - interceptions → statistics[].tackles.interceptions
   - blocks → statistics[].tackles.blocks
   - dribbles → statistics[].dribbles.attempts
   - fouls_drawn → statistics[].fouls.drawn
3. Generate a probability curve with 10-15 data points
4. Write the "reasoning" field as a DEEP 4-6 paragraph professional analysis covering: matchup edge, player usage, game script, venue splits, form trend, H2H, and floor/ceiling. This should convince a sharp bettor.
5. Write "matchupBreakdown" as 3-4 paragraphs about the opponent's defensive profile and how they concede this specific stat.
6. Include venue home/away stat splits in "venueAnalysis" with actual numbers.
7. Include "formTrend" with rolling 5-game average vs season average.
8. Include "floorCeiling" with worst/best case reasoning.
9. Write "sharpSummary" as the 2-3 sentence KEY takeaway a sharp bettor cares about.
10. Include AT LEAST 15 recentSamples with venue tags.
11. Return ONLY valid JSON, no markdown or extra text."""

        response = await chat.send_message(UserMessage(text=prompt))
        response_text = response.strip()

        # Clean up response - remove markdown code fences if present
        if response_text.startswith("```"):
            lines = response_text.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            response_text = "\n".join(lines)

        prediction = json.loads(response_text)

        # Ensure all required fields have fallback values
        prediction.setdefault("player", {"id": req.playerId, "name": req.playerName, "team": str(req.teamId), "role": "Unknown", "position": "Unknown"})
        prediction.setdefault("opponent", req.opponentName)
        prediction.setdefault("propType", req.propType)
        prediction.setdefault("line", req.line)
        prediction.setdefault("projectedValue", req.line)
        prediction.setdefault("recommendation", "over")
        prediction.setdefault("confidenceScore", 50)
        prediction.setdefault("confidenceLevel", "Medium")
        prediction.setdefault("confidenceInterval", [req.line * 0.8, req.line * 1.2])
        prediction.setdefault("recentSamples", [])
        prediction.setdefault("bayesianMetrics", {"priorMean": req.line, "momentumEffect": 0, "covariateAdjustment": 0, "reversalFlag": "stable"})
        prediction.setdefault("probabilityCurve", [])
        prediction.setdefault("reasoning", "Analysis based on available data.")
        prediction.setdefault("tacticalInsights", "")

        # Save to MongoDB
        prediction["_created"] = datetime.now(timezone.utc).isoformat()
        prediction["_request"] = req.model_dump()
        await db.predictions.insert_one(prediction)
        prediction.pop("_id", None)

        return prediction

    except json.JSONDecodeError as e:
        # Return a safe fallback prediction
        return {
            "player": {"id": req.playerId, "name": req.playerName, "team": str(req.teamId), "role": "Unknown", "position": "Unknown"},
            "opponent": req.opponentName,
            "propType": req.propType,
            "line": req.line,
            "projectedValue": req.line,
            "recommendation": "over",
            "confidenceScore": 50,
            "confidenceLevel": "Medium",
            "confidenceInterval": [req.line * 0.8, req.line * 1.2],
            "recentSamples": [],
            "bayesianMetrics": {"priorMean": req.line, "momentumEffect": 0, "covariateAdjustment": 0, "reversalFlag": "stable"},
            "probabilityCurve": [],
            "reasoning": "AI analysis returned an invalid format. Displaying fallback prediction.",
            "tacticalInsights": "",
            "explanation": "Fallback prediction due to AI parsing error."
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Prediction failed: {str(e)}")


class ChatStartRequest(BaseModel):
    session_id: Optional[str] = None


@app.post("/api/chat/start")
async def chat_start(req: ChatStartRequest):
    sid = req.session_id or str(uuid.uuid4())
    chat = LlmChat(
        api_key=EMERGENT_LLM_KEY,
        session_id=sid,
        system_message="You are an elite soccer tactical analyst and prop betting expert. You help users understand the deep tactical nuances of player performances and match dynamics. Use data-driven reasoning and mention specific tactical concepts like 'low blocks', 'half-spaces', 'pressing triggers', and 'progressive passes'. Be concise but insightful."
    )
    chat.with_model("gemini", "gemini-2.5-flash")
    chat_sessions[sid] = chat
    return {
        "session_id": sid,
        "message": "Welcome to the Tactical Command Center. I am your elite analyst. How can I help you dominate the props market today?"
    }


class ChatMessageRequest(BaseModel):
    session_id: str
    message: str


@app.post("/api/chat/message")
async def chat_message(req: ChatMessageRequest):
    chat = chat_sessions.get(req.session_id)
    if not chat:
        chat = LlmChat(
            api_key=EMERGENT_LLM_KEY,
            session_id=req.session_id,
            system_message="You are an elite soccer tactical analyst and prop betting expert. You help users understand deep tactical nuances of player performances. Be concise but insightful."
        )
        chat.with_model("gemini", "gemini-2.5-flash")
        chat_sessions[req.session_id] = chat
    try:
        response = await chat.send_message(UserMessage(text=req.message))
        return {"response": response, "session_id": req.session_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class NaturalQueryRequest(BaseModel):
    query: str


@app.post("/api/parse-query")
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


@app.get("/api/football/status")
async def football_status():
    try:
        data = await api_football_request("status")
        return {"status": "online", "data": data}
    except Exception:
        return {"status": "offline"}
