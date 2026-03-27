import os
import json
import httpx
import uuid
import asyncio as aio
import time
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
    {"id": 32, "name": "World Cup Qualifiers (UEFA)", "type": "International Team"},
    {"id": 34, "name": "World Cup Qualifiers (CONMEBOL)", "type": "International Team"},
    {"id": 31, "name": "World Cup Qualifiers (CONCACAF)", "type": "International Team"},
    {"id": 29, "name": "World Cup Qualifiers (CAF)", "type": "International Team"},
    {"id": 30, "name": "World Cup Qualifiers (AFC)", "type": "International Team"},
    {"id": 33, "name": "World Cup Qualifiers (OFC)", "type": "International Team"},
    {"id": 4, "name": "Euro Championship", "type": "International Team"},
    {"id": 960, "name": "Euro Qualifiers", "type": "International Team"},
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
        return {"requires_password": True, "email": email_lower, "access_type": access_type}

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


class ResetPasswordRequest(BaseModel):
    email: str
    new_password: str


@app.post("/api/auth/reset-password")
async def reset_password(req: ResetPasswordRequest):
    email_lower = req.email.lower().strip()
    if len(req.new_password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters.")

    access_type = await check_access(email_lower)
    if not access_type:
        raise HTTPException(status_code=401, detail="No active subscription found. Cannot reset password.")

    user_record = await db.users.find_one({"email": email_lower}, {"_id": 0})
    if not user_record:
        raise HTTPException(status_code=404, detail="No account found for this email. Please sign up first.")

    salt = bcrypt.gensalt()
    password_hash = bcrypt.hashpw(req.new_password.encode("utf-8"), salt).decode("utf-8")

    await db.users.update_one(
        {"email": email_lower},
        {"$set": {"passwordHash": password_hash, "password_reset_at": datetime.now(timezone.utc).isoformat()}}
    )

    token = await create_session(email_lower, access_type)
    return {"verified": True, "email": email_lower, "session_token": token, "access_type": access_type, "message": "Password reset successfully"}


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
    # Try multiple seasons - international comps often use future seasons
    seasons_to_try = [season + 1, season, season - 1, season - 2, season - 3]
    for s in seasons_to_try:
        try:
            data = await api_football_request("teams", {"league": league_id, "season": s})
            if data:
                teams = [{"id": item["team"]["id"], "name": item["team"]["name"], "logo": item["team"].get("logo", "")} for item in data]
                return {"teams": teams}
        except Exception:
            continue
    return {"teams": []}


class PlayerSearchRequest(BaseModel):
    query: str
    league_id: Optional[int] = None
    season: Optional[int] = None


@app.post("/api/players/search")
async def search_players(req: PlayerSearchRequest):
    if len(req.query) < 3:
        return {"players": []}
    season = req.season or CURRENT_SEASON
    query_lower = req.query.lower().strip()

    def extract_player(item):
        p = item.get("player", {})
        stats = item.get("statistics", [])
        # Use LAST statistics entry (most recent team after transfers)
        team_id = stats[-1]["team"]["id"] if stats else 0
        team_name = stats[-1]["team"]["name"] if stats else ""
        firstname = p.get("firstname", "") or ""
        lastname = p.get("lastname", "") or ""
        display_name = f"{firstname} {lastname}".strip() if firstname and lastname else p.get("name", "")
        return {
            "id": p.get("id", 0),
            "name": display_name,
            "firstname": firstname,
            "lastname": lastname,
            "age": p.get("age", 0),
            "nationality": p.get("nationality", ""),
            "photo": p.get("photo", ""),
            "teamId": team_id,
            "teamName": team_name,
        }

    all_players = []

    # Strategy 1: Search within specified league
    if req.league_id:
        for s in [season, season - 1, season + 1, season - 2]:
            try:
                data = await api_football_request("players", {"search": req.query, "league": req.league_id, "season": s})
                if data:
                    all_players.extend([extract_player(item) for item in data])
                    break
            except Exception:
                continue

    # Strategy 2: If no results, try major domestic leagues in parallel for better team info
    if not all_players:
        major_leagues = [39, 140, 135, 78, 61, 253, 71, 307]
        async def try_league(lid):
            try:
                data = await api_football_request("players", {"search": req.query, "league": lid, "season": season})
                return [extract_player(item) for item in (data or [])]
            except Exception:
                return []
        results = await aio.gather(*[try_league(lid) for lid in major_leagues])
        for r in results:
            all_players.extend(r)

    # Strategy 3: If still nothing, use profiles as last resort
    if not all_players:
        try:
            data = await api_football_request("players/profiles", {"search": req.query})
            if data:
                all_players.extend([extract_player(item) for item in data])
        except Exception:
            pass

    # Strategy 4: If full name returned nothing from profiles, try last name
    if not all_players and " " in req.query:
        last_name = req.query.strip().split()[-1]
        try:
            data = await api_football_request("players/profiles", {"search": last_name})
            if data:
                all_players.extend([extract_player(item) for item in data])
        except Exception:
            pass

    # De-duplicate by player ID, prefer entries with team info
    seen_ids = {}
    for p in all_players:
        pid = p["id"]
        if pid not in seen_ids:
            seen_ids[pid] = p
        elif p["teamName"] and not seen_ids[pid]["teamName"]:
            seen_ids[pid] = p

    players = list(seen_ids.values())

    # Sort: players with team info first, then relevance (name match)
    def sort_key(p):
        has_team = 0 if p["teamName"] else 1
        name_lower = p["name"].lower()
        lastname_lower = (p["lastname"] or "").lower()
        # Exact lastname match gets priority
        exact = 0 if lastname_lower == query_lower or query_lower in name_lower.split() else 1
        return (has_team, exact, p["name"])

    players.sort(key=sort_key)
    return {"players": players[:15]}


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


async def get_recent_fixtures_fast(team_id: int, count: int = 20):
    """Get recent fixtures WITHOUT individual player stats (fast - 1 API call)."""
    try:
        fixtures = await api_football_request("fixtures", {"team": team_id, "last": count})
        results = []
        for f in fixtures[:count]:
            home_team_id = f.get("teams", {}).get("home", {}).get("id")
            venue = "home" if home_team_id == team_id else "away"
            home_goals = f.get("goals", {}).get("home", 0) or 0
            away_goals = f.get("goals", {}).get("away", 0) or 0
            opponent_name = f.get("teams", {}).get("away" if venue == "home" else "home", {}).get("name", "Unknown")
            results.append({
                "date": f.get("fixture", {}).get("date", ""),
                "opponent": opponent_name,
                "venue": venue,
                "homeGoals": home_goals,
                "awayGoals": away_goals,
                "result": f.get("teams", {}).get("home" if venue == "home" else "away", {}).get("winner"),
                "league": f.get("league", {}).get("name", ""),
                "round": f.get("league", {}).get("round", ""),
            })
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
        # Fetch player stats + recent fixtures + supplementary data ALL IN PARALLEL
        async def safe_fetch(endpoint, params, fallback=None):
            try:
                return await api_football_request(endpoint, params)
            except Exception:
                return fallback

        async def get_player_data():
            for s in [CURRENT_SEASON, CURRENT_SEASON - 1, CURRENT_SEASON - 2]:
                try:
                    data = await api_football_request("players", {"id": req.playerId, "season": s})
                    if data:
                        return data[0]
                except Exception:
                    continue
            return None

        actual_team_id = req.teamId
        league_id = req.leagueId or 39

        # Fire ALL API calls at once
        player_data_task = get_player_data()
        team_stats_task = safe_fetch("teams/statistics", {"team": actual_team_id or 40, "league": league_id, "season": CURRENT_SEASON})
        opponent_stats_task = safe_fetch("teams/statistics", {"team": req.opponentId, "league": league_id, "season": CURRENT_SEASON})
        h2h_task = safe_fetch("fixtures/headtohead", {"h2h": f"{actual_team_id or 40}-{req.opponentId}", "last": 5}, [])
        standings_task = safe_fetch("standings", {"league": league_id, "season": CURRENT_SEASON})
        fixtures_task = get_recent_fixtures_fast(actual_team_id or 40, 20)

        player_stats, team_stats, opponent_stats, h2h_data, standings_raw, recent_fixtures = await aio.gather(
            player_data_task, team_stats_task, opponent_stats_task, h2h_task, standings_task, fixtures_task
        )

        if actual_team_id == 0 and player_stats:
            stats_list = player_stats.get("statistics", [])
            if stats_list:
                actual_team_id = stats_list[0].get("team", {}).get("id", 0)

        if not league_id and player_stats:
            stats_list = player_stats.get("statistics", [])
            if stats_list:
                league_id = stats_list[0].get("league", {}).get("id", 39)

        standings = []
        if standings_raw:
            try:
                standings = standings_raw[0].get("league", {}).get("standings", [[]])[0]
            except (IndexError, AttributeError):
                pass

        historical_data = {
            "playerStats": player_stats,
            "teamStats": team_stats,
            "opponentStats": opponent_stats,
            "h2hData": h2h_data,
            "standings": standings,
            "recentFixtures": recent_fixtures,
        }

        # 2. Send to Gemini — enhanced with role + opponent analysis
        chat = LlmChat(
            api_key=EMERGENT_LLM_KEY,
            session_id=f"predict-{uuid.uuid4().hex[:8]}",
            system_message="""You are an elite soccer prop analyst with deep tactical knowledge. Return ONLY valid JSON (no markdown).

CRITICAL ANALYSIS RULES:
1. PLAYER ROLE MULTIPLIERS: A player's position/role is the #1 predictor of stat volume.
   - Deep-lying playmaker (CDM/CM holding): Pass attempts are EXTREMELY HIGH (80-110+ per 90). They recycle possession constantly.
   - Box-to-box midfielder: Moderate-high passes (50-80), moderate tackles/interceptions
   - Attacking midfielder / #10: High key passes, moderate total passes, low tackles
   - Centre-back: High passes in own half, low key passes, high blocks/interceptions
   - Goalkeeper: Saves only, extremely low passes
   - Winger: High dribbles, shots, low pass attempts
   - Striker: High shots, low passes
   ALWAYS identify the player's ACTUAL role and adjust projections accordingly. A CDM/holding midfielder will ALWAYS have inflated pass numbers.

2. OPPONENT DEFENSIVE STYLE ANALYSIS:
   - Low-block teams (sit deep, defend, counter): Opponents will have MORE possession, MORE passes, FEWER shots
   - High-press teams: More turnovers, fewer passes, more tackles/interceptions
   - Teams that concede possession: Check their avg. opposition possession% — if they give up 55%+ possession, the opposing team's passers will have inflated numbers
   - Weaker teams in the league: Often sit back → more passes for dominant team's midfielders

3. POSSESSION CONTEXT:
   - If the player's team is projected to dominate possession (55%+), ALL passing stats get a 15-25% boost
   - If the team is the underdog/away and will press high, expect fewer passes, more tackles

4. RECENT FORM vs SEASON AVERAGES:
   - Rolling 5-game average is MORE predictive than season average for streaky players
   - Look for FLOOR games (injury return, substituted early, tactical changes) and adjust

JSON structure:
{"player":{"id":int,"name":"","team":"","role":"","position":""},"opponent":"","league":"","propType":"","line":0,"projectedValue":0,"recommendation":"over|under","confidenceScore":0-100,"confidenceLevel":"Low|Medium|High|Very High","confidenceInterval":[lo,hi],"recentSamples":[{"date":"","opponent":"","value":0,"minutesPlayed":0,"matchDifficulty":"low|medium|high","venue":"home|away"}],"bayesianMetrics":{"priorMean":0,"momentumEffect":0,"covariateAdjustment":0,"reversalFlag":"stable|upward_reversal_likely|downward_reversal_likely"},"probabilityCurve":[{"value":0,"probability":0}],"tacticalAlerts":[{"type":"injury|lineup|tactical","message":"","severity":"low|medium|high"}],"sharpSummary":"","reasoning":""}

Field requirements:
- role: MUST be specific (e.g., "Deep-Lying Playmaker", "CDM Holding", "Box-to-Box CM", "Inside Forward", "Target Striker", "Sweeper Keeper")
- sharpSummary: 2-3 sharp sentences. The CORE edge including role context and opponent defensive matchup.
- reasoning: 2-3 rich paragraphs covering: (1) PLAYER ROLE — what position they play, how that role inflates/deflates the specific stat, with per-90 context (2) OPPONENT DEFENSIVE PROFILE — do they press high or sit deep? What possession% do they concede? Where do they allow the ball? (3) venue splits + game script + expected possession + floor/ceiling. Write like an elite sharp analyst.
- recentSamples: 15-20 entries with venue tags, sorted date descending
- probabilityCurve: 10 data points
- covariateAdjustment: Factor in role + opponent style adjustment (positive = stat boost, negative = stat suppression)"""
        )
        chat.with_model("gemini", "gemini-2.5-flash")

        trimmed_data = json.dumps(historical_data, default=str)[:10000]

        prompt = f"""Player: {req.playerName} | Opponent: {req.opponentName} | Venue: {req.venue} | Prop: {req.propType} | Line: {req.line}

Stat mapping: pass_attempts=passes.total, shots=shots.total, shots_on_target=shots.on, tackles=tackles.total, key_passes=passes.key, saves=goals.saves, interceptions=tackles.interceptions, blocks=tackles.blocks, dribbles=dribbles.attempts, fouls_drawn=fouls.drawn

IMPORTANT: First determine the player's EXACT role/position from the data. Then analyze the opponent's defensive style (high press vs low block, possession conceded %). Use role-based multipliers to adjust your projection. A deep-lying playmaker/CDM will have 80-110+ pass attempts. Do NOT under-project high-volume passers.

Data:
{trimmed_data}

Return ONLY valid JSON. 15+ recentSamples with venue. Rich role+opponent reasoning (2-3 paragraphs). 10pt probabilityCurve."""

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


class SettlePicksRequest(BaseModel):
    picks: list


@app.post("/api/settle-picks")
async def settle_picks(req: SettlePicksRequest):
    """Check match results and settle picks that have finished."""
    settled = []
    for pick in req.picks:
        if pick.get("status") != "live":
            continue

        player_id = pick.get("player", {}).get("id", 0)
        team_name = pick.get("player", {}).get("team", "")
        prop_type = pick.get("propType", "")
        opponent = pick.get("opponent", "")
        league_id = pick.get("_request", {}).get("leagueId", 39)

        stat_map = {
            "pass_attempts": lambda s: s.get("passes", {}).get("total"),
            "shots": lambda s: s.get("shots", {}).get("total"),
            "shots_on_target": lambda s: s.get("shots", {}).get("on"),
            "tackles": lambda s: s.get("tackles", {}).get("total"),
            "key_passes": lambda s: s.get("passes", {}).get("key"),
            "saves": lambda s: s.get("goals", {}).get("saves"),
            "interceptions": lambda s: s.get("tackles", {}).get("interceptions"),
            "blocks": lambda s: s.get("tackles", {}).get("blocks"),
            "dribbles": lambda s: s.get("dribbles", {}).get("attempts"),
            "fouls_drawn": lambda s: s.get("fouls", {}).get("drawn"),
        }

        try:
            # Find the player's team ID from recent data
            team_id = pick.get("_request", {}).get("teamId", 0)
            if not team_id:
                # Try to get from player search
                for s in [CURRENT_SEASON, CURRENT_SEASON + 1]:
                    try:
                        pdata = await api_football_request("players", {"id": player_id, "season": s, "league": league_id})
                        if pdata:
                            stats_list = pdata[0].get("statistics", [])
                            if stats_list:
                                team_id = stats_list[-1]["team"]["id"]
                                break
                    except Exception:
                        continue

            if not team_id:
                continue

            # Find the relevant finished fixture (try current and next season)
            recent = None
            for s in [CURRENT_SEASON + 1, CURRENT_SEASON]:
                try:
                    data = await api_football_request("fixtures", {"team": team_id, "last": 3, "season": s})
                    if data:
                        # Find fixture matching opponent
                        for f in data:
                            home = f.get("teams", {}).get("home", {}).get("name", "")
                            away = f.get("teams", {}).get("away", {}).get("name", "")
                            status = f.get("fixture", {}).get("status", {}).get("short", "")
                            if status in ("FT", "AET", "PEN") and \
                               (opponent.lower() in home.lower() or opponent.lower() in away.lower()):
                                recent = f
                                break
                        if recent:
                            break
                except Exception:
                    continue

            if not recent:
                continue

            fixture_id = recent.get("fixture", {}).get("id")
            fixture_date = recent.get("fixture", {}).get("date", "")

            # Get player stats from fixtures/players endpoint
            fixture_players = await api_football_request("fixtures/players", {"fixture": fixture_id})
            actual_value = None

            if fixture_players:
                for team_data in fixture_players:
                    for p in team_data.get("players", []):
                        if p.get("player", {}).get("id") == player_id:
                            pstats = p.get("statistics", [{}])[0]
                            getter = stat_map.get(prop_type)
                            if getter:
                                actual_value = getter(pstats)
                            break
                    if actual_value is not None:
                        break

            if actual_value is not None:
                line = pick.get("line", 0)
                recommendation = pick.get("recommendation", "over")
                hit = (actual_value > line and recommendation == "over") or \
                      (actual_value < line and recommendation == "under")

                settled.append({
                    "pickId": pick.get("id"),
                    "status": "settled",
                    "result": "hit" if hit else "miss",
                    "actualValue": actual_value,
                    "fixtureDate": fixture_date,
                    "matchScore": f"{recent.get('goals',{}).get('home',0)}-{recent.get('goals',{}).get('away',0)}",
                })

        except Exception:
            continue

    return {"settled": settled}


@app.get("/api/pick-of-the-day")
async def pick_of_the_day():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Check cache first
    cached = await db.potd.find_one({"date": today}, {"_id": 0})
    if cached:
        return cached

    # Fetch today's fixtures to find live games
    try:
        fixtures = await api_football_request("fixtures", {"date": today, "status": "NS"})
        if not fixtures:
            # Try tomorrow
            from datetime import timedelta
            tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d")
            fixtures = await api_football_request("fixtures", {"date": tomorrow, "status": "NS"})

        if not fixtures:
            # Fallback: get next fixtures from top leagues
            fixtures = []
            for lid in [39, 140, 135, 78, 61]:
                try:
                    f = await api_football_request("fixtures", {"league": lid, "next": 3, "season": CURRENT_SEASON})
                    fixtures.extend(f or [])
                except Exception:
                    continue
                if len(fixtures) >= 5:
                    break
    except Exception:
        fixtures = []

    if not fixtures:
        result = {
            "date": today,
            "available": False,
            "message": "No fixtures found for today. Check back later."
        }
        await db.potd.update_one({"date": today}, {"$set": result}, upsert=True)
        return result

    # Prepare fixture summaries for Gemini
    fixture_summaries = []
    for f in fixtures[:10]:
        home = f.get("teams", {}).get("home", {})
        away = f.get("teams", {}).get("away", {})
        league = f.get("league", {})
        fixture_summaries.append({
            "home": home.get("name", ""),
            "away": away.get("name", ""),
            "league": league.get("name", ""),
            "leagueId": league.get("id", 0),
            "date": f.get("fixture", {}).get("date", ""),
        })

    chat = LlmChat(
        api_key=EMERGENT_LLM_KEY,
        session_id=f"potd-{uuid.uuid4().hex[:8]}",
        system_message="You are an elite soccer prop analyst. Return ONLY valid JSON."
    )
    chat.with_model("gemini", "gemini-2.5-flash")

    prompt = f"""Today's fixtures:
{json.dumps(fixture_summaries, default=str)}

Pick the SINGLE best player prop bet of the day. Choose a real star player from one of these matchups who has a strong statistical edge. Return ONLY this JSON:
{{"playerName":"","teamName":"","opponentName":"","league":"","leagueId":0,"propType":"pass_attempts|shots|shots_on_target|tackles|key_passes|saves|interceptions|blocks|dribbles|fouls_drawn","suggestedLine":0,"recommendation":"over|under","confidenceScore":0-100,"confidenceLevel":"Low|Medium|High|Very High","sharpSummary":"2-3 sentence sharp analysis of WHY this is the pick","reasoning":"1 paragraph explaining the matchup edge, recent form, and statistical backing"}}

Pick a REAL player from these actual fixtures. Be specific and data-driven."""

    try:
        response = await chat.send_message(UserMessage(text=prompt))
        text = response.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines)
        pick_data = json.loads(text)
    except Exception:
        pick_data = {
            "playerName": "Unable to generate",
            "teamName": "",
            "opponentName": "",
            "league": "",
            "propType": "shots",
            "suggestedLine": 0,
            "recommendation": "over",
            "confidenceScore": 0,
            "confidenceLevel": "Low",
            "sharpSummary": "Pick generation failed. Try refreshing.",
            "reasoning": ""
        }

    result = {
        "date": today,
        "available": True,
        "pick": pick_data,
        "generatedAt": datetime.now(timezone.utc).isoformat()
    }

    await db.potd.update_one({"date": today}, {"$set": result}, upsert=True)
    return result
