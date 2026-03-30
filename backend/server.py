import os
import json
import httpx
import uuid
import asyncio as aio
import time
import bcrypt
import statistics as stats_mod
import traceback
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
from dotenv import load_dotenv
from emergentintegrations.llm.chat import LlmChat, UserMessage, ImageContent
from motor.motor_asyncio import AsyncIOMotorClient
from openai import OpenAI

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
XAI_API_KEY = os.environ.get("XAI_API_KEY")

LIFETIME_SUB_EMAILS = [
    "faron2allen@gmail.com", "jossel0701@gmail.com", "josselj001@gmail.com",
    "brayanfgaleas@icloud.com", "odr310@gmail.com",
    "joseharo197@gmail.com", "rijulgauchan1@gmail.com", "gordo0210@icloud.com",
    "brianavina23@gmail.com", "andrewfitz97@yahoo.com",
    "jose108798@gmail.com", "letwins04@gmail.com",
    "quon.qg@gmail.com", "jesselopezj@hotmail.com",
    "jaredlee0414@gmail.com"
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

# Women's league IDs — for correct pronoun usage in AI analysis
WOMENS_LEAGUE_IDS = {254}  # NWSL. Add more women's leagues as needed.

# Rate limit: max 5 concurrent API-Sports requests
_api_semaphore = aio.Semaphore(5)

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
    async with _api_semaphore:
        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    resp = await client.get(f"{API_FOOTBALL_BASE}/{endpoint}", headers=headers, params=params or {})
                    if resp.status_code == 429:
                        await aio.sleep(1.5 * (attempt + 1))
                        continue
                    if resp.status_code != 200:
                        raise HTTPException(status_code=resp.status_code, detail=f"API-Sports error: {resp.text}")
                    data = resp.json()
                    if data.get("errors") and len(data["errors"]) > 0:
                        error_msg = json.dumps(data["errors"])
                        if "Too many requests" in error_msg or "rate limit" in error_msg.lower():
                            await aio.sleep(1.5 * (attempt + 1))
                            continue
                        raise HTTPException(status_code=400, detail=f"API-Sports error: {error_msg}")
                    return data.get("response", [])
            except httpx.TimeoutException:
                if attempt < 2:
                    continue
                raise HTTPException(status_code=504, detail="API-Sports timeout")
        raise HTTPException(status_code=429, detail="API-Sports rate limit — try again in a few seconds")


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
            "photo": "",
            "teamId": team_id,
            "teamName": team_name,
        }

    all_players = []

    # Strategy 1: Search within specified league — search MULTIPLE seasons in parallel and merge
    # This catches both transferred players (in newest season) AND players not yet registered (in current season)
    if req.league_id:
        async def search_season(s):
            try:
                data = await api_football_request("players", {"search": req.query, "league": req.league_id, "season": s})
                return [(extract_player(item), s) for item in (data or [])]
            except Exception:
                return []

        # Search newest + current season in parallel
        results_by_season = await aio.gather(
            search_season(season + 1),
            search_season(season),
        )
        # Merge: newer season data wins for team assignment
        season_data = {}
        for season_results in results_by_season:
            for player_data, found_season in season_results:
                pid = player_data["id"]
                if pid not in season_data or found_season > season_data[pid][1]:
                    season_data[pid] = (player_data, found_season)
                elif found_season == season_data[pid][1]:
                    pass  # Same season, keep first
                else:
                    # Older season — only add if we don't have this player yet, keep newer team
                    if pid not in season_data:
                        season_data[pid] = (player_data, found_season)
        all_players = [v[0] for v in season_data.values()]

        # If still nothing, try older seasons
        if not all_players:
            for s in [season - 1, season - 2]:
                try:
                    data = await api_football_request("players", {"search": req.query, "league": req.league_id, "season": s})
                    if data:
                        all_players.extend([extract_player(item) for item in data])
                        break
                except Exception:
                    continue

        # Strategy 1b: If full name returned nothing in league, try last name within SAME league
        if not all_players and " " in req.query:
            last_name = req.query.strip().split()[-1]
            async def search_season_lastname(s):
                try:
                    data = await api_football_request("players", {"search": last_name, "league": req.league_id, "season": s})
                    return [(extract_player(item), s) for item in (data or [])]
                except Exception:
                    return []
            results_by_season = await aio.gather(
                search_season_lastname(season + 1),
                search_season_lastname(season),
            )
            season_data = {}
            for season_results in results_by_season:
                for player_data, found_season in season_results:
                    pid = player_data["id"]
                    if pid not in season_data or found_season > season_data[pid][1]:
                        season_data[pid] = (player_data, found_season)
            all_players = [v[0] for v in season_data.values()]
            if not all_players:
                for s in [season - 1]:
                    try:
                        data = await api_football_request("players", {"search": last_name, "league": req.league_id, "season": s})
                        if data:
                            all_players.extend([extract_player(item) for item in data])
                            break
                    except Exception:
                        continue

    # Strategy 2: If no results, try major domestic leagues — newest season first
    if not all_players:
        major_leagues = [39, 140, 135, 78, 61, 253, 71, 307]
        async def try_league(lid):
            for s in [season + 1, season]:
                try:
                    data = await api_football_request("players", {"search": req.query, "league": lid, "season": s})
                    if data:
                        return [extract_player(item) for item in data]
                except Exception:
                    continue
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

    # De-duplicate by player ID, prefer entries with team info from newest season
    seen_ids = {}
    for p in all_players:
        pid = p["id"]
        if pid not in seen_ids:
            seen_ids[pid] = p
        elif p["teamName"] and not seen_ids[pid]["teamName"]:
            seen_ids[pid] = p

    players = list(seen_ids.values())

    # Sort: players with team info first, then relevance (name match)
    import unicodedata
    def strip_accents(s):
        return ''.join(c for c in unicodedata.normalize('NFD', s) if unicodedata.category(c) != 'Mn')

    query_parts = [strip_accents(w.lower()) for w in req.query.strip().split()]
    def sort_key(p):
        has_team = 0 if p["teamName"] else 1
        name_norm = strip_accents(p["name"].lower())
        firstname_norm = strip_accents((p["firstname"] or "").lower())
        # Best: all query words match (e.g., "hernan" + "lopez" both in name)
        all_match = 0 if all(w in name_norm for w in query_parts) else 1
        # Good: first name matches first query word
        first_match = 0 if query_parts and firstname_norm.startswith(query_parts[0]) else 1
        return (has_team, all_match, first_match, p["name"])

    players.sort(key=sort_key)
    return {"players": players[:15]}


@app.get("/api/player/{player_id}/stats")
async def get_player_stats(player_id: int, season: int = CURRENT_SEASON):
    for s in [season + 1, season, season - 1, season - 2]:
        try:
            data = await api_football_request("players", {"id": player_id, "season": s})
            if data:
                return {"stats": data[0]}
        except Exception:
            continue
    return {"stats": None}


async def get_recent_fixtures_fast(team_id: int, count: int = 20):
    """Get recent fixtures with fixture IDs for deeper stat lookups."""
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
                "fixtureId": f.get("fixture", {}).get("id"),
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
    teamName: str = ""
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
            # Try to get data from multiple seasons for richer context
            all_data = None
            for s in [CURRENT_SEASON + 1, CURRENT_SEASON, CURRENT_SEASON - 1, CURRENT_SEASON - 2]:
                try:
                    data = await api_football_request("players", {"id": req.playerId, "season": s})
                    if data:
                        if all_data is None:
                            all_data = data[0]
                        else:
                            # Merge additional season stats
                            all_data.setdefault("statistics", []).extend(data[0].get("statistics", []))
                except Exception:
                    continue
            return all_data

        actual_team_id = req.teamId
        league_id = req.leagueId or 39

        # Fire ALL API calls at once (optimized — kept odds for game context)
        player_data_task = get_player_data()
        async def get_team_stats_multi_season(team_id, lid):
            for s in [CURRENT_SEASON + 1, CURRENT_SEASON, CURRENT_SEASON - 1]:
                result = await safe_fetch("teams/statistics", {"team": team_id, "league": lid, "season": s})
                if result:
                    return result
            return None

        async def get_match_odds():
            """Get bookmaker odds for the specific upcoming fixture between team and opponent"""
            try:
                fixtures = []
                # First try: h2h next fixture between the two specific teams
                for s in [CURRENT_SEASON + 1, CURRENT_SEASON]:
                    try:
                        h2h_fixtures = await api_football_request("fixtures/headtohead", {
                            "h2h": f"{actual_team_id or 40}-{req.opponentId}",
                            "next": 5,
                            "season": s
                        })
                        if h2h_fixtures:
                            fixtures = h2h_fixtures
                            break
                    except Exception:
                        continue

                # Fallback: get team's next match if h2h didn't find upcoming fixture
                if not fixtures:
                    for s in [CURRENT_SEASON + 1, CURRENT_SEASON]:
                        try:
                            next_fixtures = await api_football_request("fixtures", {"team": actual_team_id or 40, "next": 5, "season": s})
                            if next_fixtures:
                                # Try to find the specific opponent match
                                for nf in next_fixtures:
                                    home_id = nf.get("teams", {}).get("home", {}).get("id")
                                    away_id = nf.get("teams", {}).get("away", {}).get("id")
                                    if req.opponentId in (home_id, away_id):
                                        fixtures = [nf]
                                        break
                                if not fixtures:
                                    fixtures = next_fixtures[:1]
                                break
                        except Exception:
                            continue
                if not fixtures:
                    return None
                fid = fixtures[0].get("fixture", {}).get("id")
                result = {}
                try:
                    odds = await api_football_request("odds", {"fixture": fid})
                    if odds:
                        for bk in odds[0].get("bookmakers", [])[:1]:
                            for bet in bk.get("bets", []):
                                if bet.get("name") == "Match Winner":
                                    vals = {v["value"]: v["odd"] for v in bet.get("values", [])}
                                    result["bookmakerOdds"] = {
                                        "source": bk.get("name", ""),
                                        "homeWin": vals.get("Home", ""),
                                        "draw": vals.get("Draw", ""),
                                        "awayWin": vals.get("Away", ""),
                                    }
                                    try:
                                        home_odd = float(vals.get("Home", 99))
                                        away_odd = float(vals.get("Away", 99))
                                        result["favorite"] = "home" if home_odd < away_odd else "away"
                                    except Exception:
                                        pass
                except Exception:
                    pass
                return result if result else None
            except Exception:
                return None

        team_stats_task = get_team_stats_multi_season(actual_team_id or 40, league_id)
        opponent_stats_task = get_team_stats_multi_season(req.opponentId, league_id)
        h2h_task = safe_fetch("fixtures/headtohead", {"h2h": f"{actual_team_id or 40}-{req.opponentId}", "last": 10}, [])

        async def get_standings_multi_season():
            for s in [CURRENT_SEASON + 1, CURRENT_SEASON, CURRENT_SEASON - 1]:
                result = await safe_fetch("standings", {"league": league_id, "season": s})
                if result:
                    return result
            return None

        standings_task = get_standings_multi_season()
        fixtures_task = get_recent_fixtures_fast(actual_team_id or 40, 30)
        odds_task = get_match_odds()

        player_stats, team_stats, opponent_stats, h2h_data, standings_raw, recent_fixtures, match_odds = await aio.gather(
            player_data_task, team_stats_task, opponent_stats_task, h2h_task, standings_task, fixtures_task, odds_task
        )

        if actual_team_id == 0 and player_stats:
            stats_list = player_stats.get("statistics", [])
            if stats_list:
                actual_team_id = stats_list[-1].get("team", {}).get("id", 0)

        if not league_id and player_stats:
            stats_list = player_stats.get("statistics", [])
            if stats_list:
                league_id = stats_list[-1].get("league", {}).get("id", 39)

        standings = []
        if standings_raw:
            try:
                standings = standings_raw[0].get("league", {}).get("standings", [[]])[0]
            except (IndexError, AttributeError):
                pass

        # =============================================
        # WAVE 2: Deep per-fixture data (uses fixture IDs from Wave 1)
        # =============================================

        # 1. Per-fixture team stats (possession, shots, passes per match)
        async def fetch_fixture_team_stats(fixture_list, team_id, limit=5):
            """Fetch per-match team stats (possession, shots, passes) from fixtures/statistics"""
            results = []
            for fix in fixture_list[:limit]:
                fid = fix.get("fixtureId")
                if not fid:
                    continue
                try:
                    data = await api_football_request("fixtures/statistics", {"fixture": fid})
                    if not data:
                        continue
                    # Find the stats for our team
                    for team_data in data:
                        if team_data.get("team", {}).get("id") == team_id:
                            raw_stats = {}
                            for s in team_data.get("statistics", []):
                                raw_stats[s.get("type", "")] = s.get("value")
                            results.append({
                                "date": fix.get("date", "")[:10],
                                "opponent": fix.get("opponent", ""),
                                "venue": fix.get("venue", ""),
                                "score": f"{fix.get('homeGoals',0)}-{fix.get('awayGoals',0)}",
                                "possession": raw_stats.get("Ball Possession", ""),
                                "totalShots": raw_stats.get("Total Shots"),
                                "shotsOnTarget": raw_stats.get("Shots on Goal"),
                                "shotsOffTarget": raw_stats.get("Shots off Goal"),
                                "blockedShots": raw_stats.get("Blocked Shots"),
                                "shotsInsideBox": raw_stats.get("Shots insidebox"),
                                "shotsOutsideBox": raw_stats.get("Shots outsidebox"),
                                "totalPasses": raw_stats.get("Total passes"),
                                "passAccuracy": raw_stats.get("Passes %"),
                                "accuratePasses": raw_stats.get("Passes accurate"),
                                "fouls": raw_stats.get("Fouls"),
                                "corners": raw_stats.get("Corner Kicks"),
                                "expectedGoals": raw_stats.get("expected_goals"),
                            })
                            break
                except Exception:
                    continue
            return results

        # 2. Player game-by-game box scores from recent fixtures
        async def fetch_player_game_logs(fixture_list, player_id, limit=8):
            """Fetch player's individual stats from each recent fixture. Falls back to name match for duplicate IDs."""
            results = []
            player_name_lower = req.playerName.lower().split()[-1] if req.playerName else ""
            for fix in fixture_list[:limit]:
                fid = fix.get("fixtureId")
                if not fid:
                    continue
                try:
                    data = await api_football_request("fixtures/players", {"fixture": fid})
                    if not data:
                        continue
                    # Find our player — try ID first, then name fallback for duplicate ID cases
                    matched_stats = None
                    for team_data in data:
                        for p in team_data.get("players", []):
                            pid = p.get("player", {}).get("id")
                            pname = (p.get("player", {}).get("name") or "").lower()
                            if pid == player_id or (player_name_lower and player_name_lower in pname):
                                stats = p.get("statistics", [{}])[0] if p.get("statistics") else {}
                                minutes = stats.get("games", {}).get("minutes") or 0
                                if minutes > 0:
                                    matched_stats = stats
                                    break
                        if matched_stats:
                            break
                    if not matched_stats:
                        continue
                    stats = matched_stats
                    minutes = stats.get("games", {}).get("minutes") or 0
                    rating = stats.get("games", {}).get("rating")
                    game_log = {
                        "date": fix.get("date", "")[:10],
                        "opponent": fix.get("opponent", ""),
                        "venue": fix.get("venue", ""),
                        "score": f"{fix.get('homeGoals',0)}-{fix.get('awayGoals',0)}",
                        "minutes": minutes,
                        "rating": float(rating) if rating else None,
                        "passes_total": stats.get("passes", {}).get("total"),
                        "passes_key": stats.get("passes", {}).get("key"),
                        "passes_accuracy": stats.get("passes", {}).get("accuracy"),
                        "shots_total": stats.get("shots", {}).get("total"),
                        "shots_on": stats.get("shots", {}).get("on"),
                        "tackles_total": stats.get("tackles", {}).get("total"),
                        "tackles_interceptions": stats.get("tackles", {}).get("interceptions"),
                        "tackles_blocks": stats.get("tackles", {}).get("blocks"),
                        "dribbles_attempts": stats.get("dribbles", {}).get("attempts"),
                        "dribbles_success": stats.get("dribbles", {}).get("success"),
                        "fouls_drawn": stats.get("fouls", {}).get("drawn"),
                        "fouls_committed": stats.get("fouls", {}).get("committed"),
                        "duels_total": stats.get("duels", {}).get("total"),
                        "duels_won": stats.get("duels", {}).get("won"),
                        "goals_saves": stats.get("goals", {}).get("saves"),
                    }
                    stat_field_map = {
                        "pass_attempts": "passes_total",
                        "shots": "shots_total",
                        "shots_on_target": "shots_on",
                        "tackles": "tackles_total",
                        "key_passes": "passes_key",
                        "saves": "goals_saves",
                        "interceptions": "tackles_interceptions",
                        "blocks": "tackles_blocks",
                        "dribbles": "dribbles_attempts",
                        "fouls_drawn": "fouls_drawn",
                    }
                    raw_val = game_log.get(stat_field_map.get(req.propType, ""), None)
                    if raw_val is not None and minutes > 0:
                        game_log["targetStatPer90"] = round((raw_val / minutes) * 90, 2)
                    results.append(game_log)
                except Exception:
                    continue
            return results

        # =============================================
        # VENUE-FILTERED DATA: Everything is venue-based
        # =============================================
        # If player is HOME → team's HOME games + opponent's AWAY games
        # If player is AWAY → team's AWAY games + opponent's HOME games
        player_venue = req.venue.lower()  # "home" or "away"
        opponent_venue = "away" if player_venue == "home" else "home"
        is_womens = req.leagueId in WOMENS_LEAGUE_IDS
        pronoun_note = "IMPORTANT: This is a WOMEN'S league. Use she/her/her pronouns for all players. Never use he/him/his." if is_womens else ""

        # Filter team's recent fixtures by venue
        venue_filtered_team_fixtures = [f for f in recent_fixtures if f.get("venue") == player_venue]
        # Also keep all fixtures for general context
        all_team_fixtures = recent_fixtures

        # Get opponent's recent fixtures (need fixture IDs)
        opponent_recent_raw = await api_football_request("fixtures", {"team": req.opponentId, "last": 15})
        opponent_fixture_list = []
        if opponent_recent_raw:
            for f in opponent_recent_raw[:15]:
                opp_home_id = f.get("teams", {}).get("home", {}).get("id")
                opp_venue = "home" if opp_home_id == req.opponentId else "away"
                opponent_fixture_list.append({
                    "fixtureId": f.get("fixture", {}).get("id"),
                    "date": f.get("fixture", {}).get("date", ""),
                    "opponent": f.get("teams", {}).get("away" if opp_venue == "home" else "home", {}).get("name", "Unknown"),
                    "venue": opp_venue,
                    "homeGoals": f.get("goals", {}).get("home", 0) or 0,
                    "awayGoals": f.get("goals", {}).get("away", 0) or 0,
                })

        # Filter opponent fixtures by their venue in THIS matchup
        venue_filtered_opp_fixtures = [f for f in opponent_fixture_list if f.get("venue") == opponent_venue]

        # Wave 2: Use VENUE-FILTERED fixtures for deep stats
        # Team's last 5 HOME/AWAY games (matching this match's venue)
        team_fixture_stats_task = fetch_fixture_team_stats(
            venue_filtered_team_fixtures[:5] if len(venue_filtered_team_fixtures) >= 3 else all_team_fixtures[:5],
            actual_team_id or 40, 5
        )
        # Opponent's last 5 AWAY/HOME games (opposite venue — how they perform when visiting/hosting)
        opponent_fixture_stats_task = fetch_fixture_team_stats(
            venue_filtered_opp_fixtures[:5] if len(venue_filtered_opp_fixtures) >= 3 else opponent_fixture_list[:5],
            req.opponentId, 5
        )
        # Player game logs: use ALL recent fixtures for maximum sample size
        # AI will weight venue-matching games higher in analysis
        player_game_logs_task = fetch_player_game_logs(all_team_fixtures[:20], req.playerId, 15)

        # =============================================
        # BUILD STRUCTURED DATA DIGEST (no AI needed — pure code extraction)
        # =============================================
        def build_data_digest():
            """Build a compact data digest directly from raw API data — no AI summarization needed."""
            parts = []

            # 1. Player basics
            if player_stats:
                pstats = player_stats.get("statistics", [{}])[0] if player_stats.get("statistics") else {}
                games_data = pstats.get("games", {})
                passes = pstats.get("passes", {})
                shots = pstats.get("shots", {})
                tackles = pstats.get("tackles", {})
                goals = pstats.get("goals", {})
                dribbles = pstats.get("dribbles", {})
                fouls = pstats.get("fouls", {})
                parts.append(f"""[PLAYER PROFILE]
- Position: {games_data.get('position', 'Unknown')} | Apps: {games_data.get('appearences', 'N/A')} | Avg Rating: {games_data.get('rating', 'N/A')}
- Avg Minutes: {(games_data.get('minutes') or 0) / max((games_data.get('appearences') or 1), 1):.0f} per game
- Passes: total={passes.get('total','N/A')}, key={passes.get('key','N/A')}, accuracy={passes.get('accuracy','N/A')}%
- Shots: total={shots.get('total','N/A')}, on_target={shots.get('on','N/A')}
- Tackles: total={tackles.get('total','N/A')}, interceptions={tackles.get('interceptions','N/A')}, blocks={tackles.get('blocks','N/A')}
- Saves: {goals.get('saves','N/A')} | Dribbles: attempts={dribbles.get('attempts','N/A')}, success={dribbles.get('success','N/A')}
- Fouls drawn: {fouls.get('drawn','N/A')}""")

            # 2. Team stats (venue-specific)
            if team_stats:
                fixtures = team_stats.get("fixtures", {})
                goals_for = team_stats.get("goals", {}).get("for", {}).get("total", {})
                goals_against = team_stats.get("goals", {}).get("against", {}).get("total", {})
                parts.append(f"""[TEAM {player_venue.upper()} PROFILE]
- Record: W{fixtures.get('wins', {}).get(player_venue, 'N/A')} D{fixtures.get('draws', {}).get(player_venue, 'N/A')} L{fixtures.get('loses', {}).get(player_venue, 'N/A')}
- Goals For ({player_venue}): {goals_for.get(player_venue, 'N/A')} | Against ({player_venue}): {goals_against.get(player_venue, 'N/A')}""")

            # 3. Opponent stats (opposite venue)
            if opponent_stats:
                opp_fix = opponent_stats.get("fixtures", {})
                opp_gf = opponent_stats.get("goals", {}).get("for", {}).get("total", {})
                opp_ga = opponent_stats.get("goals", {}).get("against", {}).get("total", {})
                parts.append(f"""[OPPONENT {opponent_venue.upper()} PROFILE]
- Record: W{opp_fix.get('wins', {}).get(opponent_venue, 'N/A')} D{opp_fix.get('draws', {}).get(opponent_venue, 'N/A')} L{opp_fix.get('loses', {}).get(opponent_venue, 'N/A')}
- Goals For ({opponent_venue}): {opp_gf.get(opponent_venue, 'N/A')} | Against ({opponent_venue}): {opp_ga.get(opponent_venue, 'N/A')}""")

            # 4. H2H
            if h2h_data:
                h2h_lines = []
                for h in h2h_data[:5]:
                    h2h_lines.append(f"  {h.get('date', '')[:10]}: {h.get('homeTeam', '')} {h.get('homeGoals', 0)}-{h.get('awayGoals', 0)} {h.get('awayTeam', '')}")
                parts.append(f"[H2H ({len(h2h_data)} matches)]\n" + "\n".join(h2h_lines))

            # 5. Standings
            if standings:
                standing_lines = [f"  {s.get('rank','')}. {s.get('team','')} — {s.get('points','')}pts (GD: {s.get('goalsDiff','')})" for s in standings[:8]]
                parts.append("[STANDINGS]\n" + "\n".join(standing_lines))

            # 6. Odds
            if match_odds and match_odds.get("bookmakerOdds"):
                bo = match_odds["bookmakerOdds"]
                parts.append(f"""[ODDS]
- Home: {bo.get('homeWin', 'N/A')} | Draw: {bo.get('draw', 'N/A')} | Away: {bo.get('awayWin', 'N/A')}
- Favorite: {match_odds.get('favorite', 'Unknown').upper()}""")

            return "\n\n".join(parts)

        data_digest = build_data_digest()

        # =============================================
        # DUAL AI: Grok runs tactical analysis with LIVE WEB SEARCH
        # =============================================
        async def grok_tactical_analysis():
            """Grok-4 with web search for real-time injury/lineup intel + stat verification + tactical analysis"""
            try:
                grok_client = OpenAI(api_key=XAI_API_KEY, base_url="https://api.x.ai/v1")
                player_team = player_stats.get("statistics", [{}])[0].get("team", {}).get("name", "") if player_stats else ""

                tactical_brief = {
                    "opponent_stats": opponent_stats,
                    "team_stats": team_stats,
                    "h2h": h2h_data[:3] if h2h_data else [],
                    "standings": standings[:6] if standings else [],
                    "odds": match_odds,
                }
                tactical_json = json.dumps(tactical_brief, default=str)[:5000]

                odds_context = ""
                if match_odds and match_odds.get("bookmakerOdds"):
                    bo = match_odds["bookmakerOdds"]
                    fav = match_odds.get("favorite", "unknown")
                    odds_context = f"ODDS: Home={bo.get('homeWin','')} Draw={bo.get('draw','')} Away={bo.get('awayWin','')} FAV={fav.upper()}"

                loop = aio.get_event_loop()
                def _call_grok():
                    return grok_client.responses.create(
                        model="grok-4.20-reasoning",
                        tools=[{"type": "web_search"}],
                        input=[
                            {"role": "system", "content": "Elite soccer analyst with deep web research. You MUST use web search to find ACTUAL per-game player statistics. Search SofaScore, FotMob, and WhoScored specifically. Do NOT guess or estimate — search and report exact numbers you find. Be concise."},
                            {"role": "user", "content": f"""Analyze {req.propType} prop on {req.playerName} ({player_position or 'Unknown'}) — {player_team} vs {req.opponentName} ({player_venue.upper()}). Line: {req.line}. {odds_context}
{pronoun_note}

TASK 1 — FIND REAL STATS (MOST IMPORTANT):
Search specifically for:
- "site:sofascore.com {req.playerName}" to find the player's SofaScore profile
- "{req.playerName} {player_team} player statistics {req.propType}"
- "{req.playerName} recent matches stats"
Go to SofaScore.com or FotMob and find this player's ACTUAL per-game {req.propType} from their last 5 matches.
Report per game: date, opponent, exact {req.propType} count, minutes played.
Example format: "Mar 14 vs Kansas City: 2 shots (90 min) — source: SofaScore"
DO NOT use any numbers I gave you in the DATA section below — those may be wrong. Only report what you find from your web search.

TASK 2 — LIVE NEWS:
Search for injuries, confirmed lineups, key absences for {player_team} and {req.opponentName}.

TASK 3 — TACTICAL ANALYSIS:
- Matchup: favorite (from odds), expected possession %, game type (open/cagey/one-sided)
- Position baseline: {player_position} expected range for {req.propType}
- If saves prop: Opponent SOT avg → saves ceiling. Favored GK = fewer saves.
- Scenarios: Base/Blowout/Trailing/Cagey — probability % and expected {req.propType} value
- Sensitivity: Sub risk, red card impact — ROBUST/MODERATE/FRAGILE
- Verdict: Over or under {req.line}? Confidence 0-100?

Format your response:
[VERIFIED STATS] — exact per-game {req.propType} numbers from web sources (cite the source)
[LIVE NEWS] — injuries, lineups
[ANALYSIS] — scenarios, verdict

DATA: {tactical_json}"""}
                        ],
                        max_output_tokens=2000,
                    )
                result = await loop.run_in_executor(None, _call_grok)
                return result.output_text if result else None
            except Exception:
                return None

        grok_tactical_task = grok_tactical_analysis()

        try:
            team_fixture_stats, opponent_fixture_stats, player_game_logs, grok_analysis = await aio.wait_for(
                aio.gather(team_fixture_stats_task, opponent_fixture_stats_task, player_game_logs_task, grok_tactical_task),
                timeout=90
            )
        except aio.TimeoutError:
            team_fixture_stats, opponent_fixture_stats, player_game_logs, grok_analysis = [], [], [], None

        historical_data = {
            "playerStats": player_stats,
            "teamStats": team_stats,
            "opponentStats": opponent_stats,
            "h2hData": h2h_data,
            "standings": standings,
            "recentFixtures": recent_fixtures,
            "matchOdds": match_odds,
        }

        # =============================================
        # Per-fixture deep data (Wave 2 results)
        # =============================================
        if team_fixture_stats:
            historical_data["teamMatchStats"] = team_fixture_stats
        if opponent_fixture_stats:
            historical_data["opponentMatchStats"] = opponent_fixture_stats
        if player_game_logs:
            # Add summary stats for the game logs
            target_field_map = {
                "pass_attempts": "passes_total",
                "shots": "shots_total",
                "shots_on_target": "shots_on",
                "tackles": "tackles_total",
                "key_passes": "passes_key",
                "saves": "goals_saves",
                "interceptions": "tackles_interceptions",
                "blocks": "tackles_blocks",
                "dribbles": "dribbles_attempts",
                "fouls_drawn": "fouls_drawn",
            }
            target_field = target_field_map.get(req.propType, "passes_total")
            values = [g.get(target_field) for g in player_game_logs if g.get(target_field) is not None]
            minutes_list = [g.get("minutes", 0) for g in player_game_logs if g.get("minutes")]
            per90_values = [g.get("targetStatPer90") for g in player_game_logs if g.get("targetStatPer90") is not None]

            game_log_summary = {
                "games": player_game_logs,
                "targetProp": req.propType,
                "sampleSize": len(values),
            }
            if values:
                game_log_summary["rawAvg"] = round(sum(values) / len(values), 2)
                game_log_summary["rawMin"] = min(values)
                game_log_summary["rawMax"] = max(values)
                if len(values) >= 3:
                    game_log_summary["stdDev"] = round(stats_mod.stdev(values), 2)
                # Home/away splits
                home_vals = [g.get(target_field) for g in player_game_logs if g.get("venue") == "home" and g.get(target_field) is not None]
                away_vals = [g.get(target_field) for g in player_game_logs if g.get("venue") == "away" and g.get(target_field) is not None]
                if home_vals:
                    game_log_summary["homeAvg"] = round(sum(home_vals) / len(home_vals), 2)
                if away_vals:
                    game_log_summary["awayAvg"] = round(sum(away_vals) / len(away_vals), 2)
            if per90_values:
                game_log_summary["per90Avg"] = round(sum(per90_values) / len(per90_values), 2)
            if minutes_list:
                game_log_summary["avgMinutes"] = round(sum(minutes_list) / len(minutes_list), 1)

            historical_data["playerGameLogs"] = game_log_summary

        # =============================================
        # BUILD REAL RECENT SAMPLES FROM GAME LOGS
        # =============================================
        # These replace Gemini-generated samples with actual API-Sports data
        real_recent_samples = []
        if player_game_logs:
            gl_target_field_map = {
                "pass_attempts": "passes_total", "shots": "shots_total", "shots_on_target": "shots_on",
                "tackles": "tackles_total", "key_passes": "passes_key", "saves": "goals_saves",
                "interceptions": "tackles_interceptions", "blocks": "tackles_blocks",
                "dribbles": "dribbles_attempts", "fouls_drawn": "fouls_drawn",
            }
            gl_target = gl_target_field_map.get(req.propType, "passes_total")
            for g in player_game_logs:
                stat_val = g.get(gl_target)
                if stat_val is not None and (g.get("minutes") or 0) > 0:
                    real_recent_samples.append({
                        "date": g.get("date", ""),
                        "opponent": g.get("opponent", ""),
                        "value": stat_val,
                        "minutesPlayed": g.get("minutes", 0),
                        "matchDifficulty": "medium",
                        "venue": g.get("venue", ""),
                    })

        # =============================================
        # UPGRADE #4: Per-90 minute normalization
        # =============================================
        # Extract per-90 rates from player's season stats so Gemini sees
        # normalized numbers, not raw totals skewed by minutes played
        per90_stats = {}
        if player_stats:
            stat_key_map = {
                "pass_attempts": ("passes", "total"),
                "shots": ("shots", "total"),
                "shots_on_target": ("shots", "on"),
                "tackles": ("tackles", "total"),
                "key_passes": ("passes", "key"),
                "saves": ("goals", "saves"),
                "interceptions": ("tackles", "interceptions"),
                "blocks": ("tackles", "blocks"),
                "dribbles": ("dribbles", "attempts"),
                "fouls_drawn": ("fouls", "drawn"),
            }
            for stat_entry in player_stats.get("statistics", []):
                league_name = stat_entry.get("league", {}).get("name", "Unknown")
                season = stat_entry.get("league", {}).get("season", "")
                games = stat_entry.get("games", {})
                minutes = games.get("minutes") or 0
                appearances = games.get("appearences") or 0
                if minutes < 90 or appearances < 2:
                    continue  # Skip tiny samples

                entry = {
                    "league": league_name,
                    "season": season,
                    "appearances": appearances,
                    "totalMinutes": minutes,
                    "avgMinutesPerGame": round(minutes / appearances, 1) if appearances else 0,
                    "per90": {},
                    "rawPerGame": {},
                }

                for prop_key, (cat, sub) in stat_key_map.items():
                    raw_val = stat_entry.get(cat, {}).get(sub)
                    if raw_val is not None and raw_val > 0:
                        per_90 = round((raw_val / minutes) * 90, 2)
                        per_game = round(raw_val / appearances, 2) if appearances else 0
                        entry["per90"][prop_key] = per_90
                        entry["rawPerGame"][prop_key] = per_game

                if entry["per90"]:
                    per90_stats[f"{league_name}_{season}"] = entry

        if per90_stats:
            historical_data["per90Analysis"] = per90_stats

        # =============================================
        # UPGRADE #3: H2H player-specific stat extraction
        # =============================================
        # For each H2H fixture, fetch the player's individual stats in THAT match
        h2h_player_stats = []
        if h2h_data:
            h2h_fixture_ids = []
            for h in h2h_data[:10]:
                fid = h.get("fixture", {}).get("id")
                if fid:
                    h2h_fixture_ids.append((fid, h))

            async def fetch_h2h_player_stat(fid, fixture_info):
                """Fetch the target player's stats from a specific H2H fixture"""
                try:
                    pstats = await api_football_request("fixtures/players", {"fixture": fid})
                    if not pstats:
                        return None

                    # Determine which team is the player's team in this fixture
                    home_id = fixture_info.get("teams", {}).get("home", {}).get("id")
                    away_id = fixture_info.get("teams", {}).get("away", {}).get("id")
                    home_name = fixture_info.get("teams", {}).get("home", {}).get("name", "")
                    away_name = fixture_info.get("teams", {}).get("away", {}).get("name", "")
                    home_goals = fixture_info.get("goals", {}).get("home", 0)
                    away_goals = fixture_info.get("goals", {}).get("away", 0)

                    # Player's team is home → opponent is away, and vice versa
                    player_is_home = (home_id == actual_team_id)
                    opponent_name = away_name if player_is_home else home_name
                    venue_in_match = "home" if player_is_home else "away"

                    # Find our player in the fixture stats
                    for team_data in pstats:
                        for p in team_data.get("players", []):
                            if p.get("player", {}).get("id") == req.playerId:
                                stats = p.get("statistics", [{}])[0] if p.get("statistics") else {}
                                minutes_played = stats.get("games", {}).get("minutes") or 0
                                stat_key_map_h2h = {
                                    "pass_attempts": stats.get("passes", {}).get("total"),
                                    "shots": stats.get("shots", {}).get("total"),
                                    "shots_on_target": stats.get("shots", {}).get("on"),
                                    "tackles": stats.get("tackles", {}).get("total"),
                                    "key_passes": stats.get("passes", {}).get("key"),
                                    "saves": stats.get("goals", {}).get("saves"),
                                    "interceptions": stats.get("tackles", {}).get("interceptions"),
                                    "blocks": stats.get("tackles", {}).get("blocks"),
                                    "dribbles": stats.get("dribbles", {}).get("attempts"),
                                    "fouls_drawn": stats.get("fouls", {}).get("drawn"),
                                }
                                return {
                                    "date": fixture_info.get("fixture", {}).get("date", ""),
                                    "opponent": opponent_name,
                                    "venue": venue_in_match,
                                    "minutesPlayed": minutes_played,
                                    "statValues": {k: v for k, v in stat_key_map_h2h.items() if v is not None},
                                    "targetStat": stat_key_map_h2h.get(req.propType),
                                    "targetStatPer90": round((stat_key_map_h2h.get(req.propType, 0) or 0) / minutes_played * 90, 2) if minutes_played > 0 and stat_key_map_h2h.get(req.propType) else None,
                                    "matchScore": f"{home_goals}-{away_goals}",
                                }
                    return None
                except Exception:
                    return None

            if h2h_fixture_ids:
                h2h_results = await aio.gather(*[fetch_h2h_player_stat(fid, fi) for fid, fi in h2h_fixture_ids])
                h2h_player_stats = [r for r in h2h_results if r]

        if h2h_player_stats:
            # Calculate H2H averages for the target stat
            h2h_values = [s["targetStat"] for s in h2h_player_stats if s.get("targetStat") is not None]
            h2h_summary = {
                "matches": h2h_player_stats,
                "targetProp": req.propType,
                "sampleSize": len(h2h_values),
            }
            if h2h_values:
                h2h_summary["avgVsOpponent"] = round(sum(h2h_values) / len(h2h_values), 2)
                h2h_summary["minVsOpponent"] = min(h2h_values)
                h2h_summary["maxVsOpponent"] = max(h2h_values)
            historical_data["h2hPlayerStats"] = h2h_summary

        # Extract player's ACTUAL position from API-Sports data
        player_position = ""
        if player_stats:
            stats_list = player_stats.get("statistics", [])
            # Find the stat entry with most appearances (most relevant)
            best_entry = None
            best_apps = 0
            for s in stats_list:
                apps = s.get("games", {}).get("appearences") or 0
                pos = s.get("games", {}).get("position", "")
                if apps > best_apps and pos:
                    best_apps = apps
                    best_entry = s
                    player_position = pos
            # If we found a better entry, also try to get stats from multiple seasons
            if not player_position:
                for s in stats_list:
                    pos = s.get("games", {}).get("position", "")
                    if pos:
                        player_position = pos
                        break

        # 2. Send to Gemini — FINAL PREDICTOR (receives Grok analysis + structured data)
        chat = LlmChat(
            api_key=EMERGENT_LLM_KEY,
            session_id=f"predict-{uuid.uuid4().hex[:8]}",
            system_message="""You are the final-stage predictor in a DUAL AI pipeline. You receive:
1. A STRUCTURED DATA DIGEST (real API stats, compiled by code — 100% accurate numbers)
2. A TACTICAL ANALYSIS from Grok (matchup analysis, live web search intel, scenarios, sensitivity tests, sub risk)
3. DEEP MATCH DATA (per-fixture stats and player game logs from the real API)

Your job: SYNTHESIZE all inputs into a single, calibrated prediction JSON. Do NOT re-derive what Grok already analyzed — USE their analysis directly. Focus on:

STEP 1: VALIDATE the player's role and POSITION from the data digest. Apply position baselines:
  - GK saves: Capped by opponent shots on target. If opponent avg 10 shots/game with 35% on target = 3.5 SOT. GK saves ~70% = ~2.5 saves. NEVER project above opponent SOT avg.
  - GK saves INVERSE: Favored team's GK = FEWER saves. Underdog GK = MORE saves.
  - Defender: tackles 2-4, interceptions 1-3, blocks 0-2, shots 0-1
  - Midfielder: shots 1-2 (AM 2-3), passes 30-50, key passes 1-3
  - Forward: shots 2-4 (elite 3-5), key passes 0-2

STEP 2: CROSS-REFERENCE Grok's scenario analysis with the game log data — does the evidence support Grok's assessment?
  - For SAVES: Check if Grok's saves projection exceeds opponent's shots-on-target average. If yes, CAP IT.
  - For SHOTS: Check if projection exceeds what's normal for this position. Midfielders rarely exceed 3 shots/game.

STEP 3: CALIBRATE the final projected value using:
- Grok's VERIFIED STATS section as ground truth (web-searched actual game numbers)
- Grok's weighted scenario projection as the starting point
- Game log data as secondary reference (API data may be incomplete — Grok's web-verified stats take priority)
- Data digest numbers for team/opponent context
- If Grok's verified stats and API game logs disagree, USE GROK'S NUMBERS and note the discrepancy
STEP 4: SET CONFIDENCE based on:
- Data quality (sample size, recency)
- Grok's sensitivity test results (ROBUST = 75+, MODERATE = 55-74, FRAGILE = 40-54)
- How close the projection is to the line (within 0.5 = coin flip = max 55 confidence)

DRIBBLE-SPECIFIC: Most volatile stat. AWAY + low-block = default UNDER when line is close to average.

Return ONLY valid JSON (no markdown).

JSON structure:
{"player":{"id":int,"name":"","team":"","role":"","position":""},"opponent":"","league":"","propType":"","line":0,"projectedValue":0,"recommendation":"over|under","confidenceScore":0-100,"confidenceLevel":"Low|Medium|High|Very High","confidenceInterval":[lo,hi],"matchupOverview":{"homeTeam":"","awayTeam":"","favorite":"home|away|even","moneyline":{"home":"","draw":"","away":""},"expectedPossession":{"home":0,"away":0},"expectedGameType":"open|cagey|one-sided|high-tempo","keyMatchupFactor":""},"recentSamples":[],"bayesianMetrics":{"priorMean":0,"momentumEffect":0,"covariateAdjustment":0,"reversalFlag":"stable|upward_reversal_likely|downward_reversal_likely"},"probabilityCurve":[{"value":0,"probability":0}],"tacticalAlerts":[{"type":"injury|lineup|tactical|sub_risk","message":"","severity":"low|medium|high"}],"sharpSummary":"","reasoning":"","scenarioAnalysis":"","keyEvidence":"","uncertaintyNote":"","sensitivityTests":"","subRisk":"","gameFlowDynamics":""}

Field requirements:
- matchupOverview: Home/away teams, moneyline odds, expected possession split, expected game type, key matchup factor. Use Grok's web intel + odds data.
- sharpSummary: 2-3 sentences. Core edge. Be direct.
- reasoning: 2-3 paragraphs synthesizing Grok's analysis + data digest numbers. Quote specific stats.
- scenarioAnalysis: Take Grok's scenario breakdown directly — refine if game log data contradicts it.
- keyEvidence: Quote 5-8 specific values from the data. Format: "Last 5: X (vs Team, venue), Y (vs Team, venue)..."
- sensitivityTests: Take Grok's sensitivity results directly.
- subRisk: Take Grok's sub risk quantification directly.
- gameFlowDynamics: Take Grok's game flow analysis directly.
- uncertaintyNote: Key risk factor + data limitations.
- recentSamples: MUST BE EMPTY ARRAY []. DO NOT generate any. Backend injects real data.
- probabilityCurve: 10 data points centered on projection."""
        )
        chat.with_model("gemini", "gemini-2.5-flash")

        # Build the data payload — use GPT summary as primary + Wave 2 deep data as supplement
        wave2_supplement = {}
        if player_game_logs:
            target_field_map = {
                "pass_attempts": "passes_total", "shots": "shots_total", "shots_on_target": "shots_on",
                "tackles": "tackles_total", "key_passes": "passes_key", "saves": "goals_saves",
                "interceptions": "tackles_interceptions", "blocks": "tackles_blocks",
                "dribbles": "dribbles_attempts", "fouls_drawn": "fouls_drawn",
            }
            target_field = target_field_map.get(req.propType, "passes_total")
            values = [g.get(target_field) for g in player_game_logs if g.get(target_field) is not None]
            game_log_brief = []
            for g in player_game_logs:
                val = g.get(target_field)
                game_log_brief.append(f"{g.get('date','')[:10]} vs {g.get('opponent','')} ({g.get('venue','')}, {g.get('minutes',0)}min): {val}")
            wave2_supplement["playerGameLogs"] = {
                "games": game_log_brief,
                "rawAvg": round(sum(values) / len(values), 2) if values else 0,
                "homeAvg": round(sum(v for g, v in zip(player_game_logs, [g.get(target_field) for g in player_game_logs]) if g.get("venue") == "home" and v) / max(1, sum(1 for g in player_game_logs if g.get("venue") == "home" and g.get(target_field))), 2) if values else 0,
                "awayAvg": round(sum(v for g, v in zip(player_game_logs, [g.get(target_field) for g in player_game_logs]) if g.get("venue") == "away" and v) / max(1, sum(1 for g in player_game_logs if g.get("venue") == "away" and g.get(target_field))), 2) if values else 0,
                "sampleSize": len(values),
            }
        if team_fixture_stats:
            wave2_supplement["teamMatchStats"] = team_fixture_stats
        if opponent_fixture_stats:
            wave2_supplement["opponentMatchStats"] = opponent_fixture_stats

        # SAVES-SPECIFIC: Elite GK Formula
        # Projected Saves = Opponent Avg SoT × GK Save% × Match Context Multiplier
        saves_context = ""
        gk_formula_data = None
        if req.propType == "saves":
            # 1. Opponent SoT per game (venue-filtered from fixture stats)
            opp_shots_list = []
            if opponent_fixture_stats:
                for mf in opponent_fixture_stats:
                    shots = mf.get("totalShots")
                    shots_on = mf.get("shotsOnTarget")
                    if shots is not None:
                        opp_shots_list.append({"total": shots, "on_target": shots_on or 0, "date": mf.get("date", ""), "venue": mf.get("venue", "")})
            opp_avg_shots = round(sum(s["total"] for s in opp_shots_list) / len(opp_shots_list), 1) if opp_shots_list else 0
            opp_avg_sot = round(sum(s["on_target"] for s in opp_shots_list) / len(opp_shots_list), 1) if opp_shots_list else 0

            # 2. GK save rate from LAST 5-7 game logs only (recent form)
            gk_saves_list = []
            gk_sot_faced_list = []
            recent_gk_logs = [g for g in player_game_logs if g.get("goals_saves") is not None and g.get("minutes", 0) > 0][:7]
            for g in recent_gk_logs:
                gk_saves_list.append(g.get("goals_saves"))
            gk_avg_saves = round(sum(gk_saves_list) / len(gk_saves_list), 2) if gk_saves_list else 0
            gk_saves_per90 = round(sum(gk_saves_list) / max(1, sum((g.get("minutes") or 0) for g in recent_gk_logs)) * 90, 2) if gk_saves_list else 0

            # Calculate save % from LAST 5-7 games only
            total_saves = sum(gk_saves_list) if gk_saves_list else 0
            games_with_saves = len(gk_saves_list)
            # Estimate goals conceded per game from team stats
            goals_against = None
            if team_stats:
                ga = team_stats.get("goals", {}).get("against", {})
                if ga:
                    total_ga = ga.get("total", {}).get(player_venue) or ga.get("total", {}).get("total") or 0
                    played = team_stats.get("fixtures", {}).get("played", {}).get(player_venue) or team_stats.get("fixtures", {}).get("played", {}).get("total") or 1
                    goals_against = round(total_ga / max(played, 1), 2) if total_ga else None

            # Save % calculation
            if total_saves > 0 and goals_against is not None and games_with_saves > 0:
                est_sot_faced = total_saves + (goals_against * games_with_saves)
                gk_save_pct = round((total_saves / max(est_sot_faced, 1)) * 100, 1)
            elif total_saves > 0:
                gk_save_pct = round(min(85, (total_saves / max(total_saves + games_with_saves * 0.8, 1)) * 100), 1)
            else:
                gk_save_pct = 70.0  # League average fallback

            # 3. Match context multiplier
            # Base = 1.0, adjust: underdog at home +0.10, favorite at home -0.10, etc.
            context_multiplier = 1.0
            context_factors = []
            if match_odds and match_odds.get("favorite"):
                fav = match_odds["favorite"]
                if fav == player_venue:
                    # GK's team is favored → fewer shots faced → fewer saves
                    context_multiplier -= 0.10
                    context_factors.append(f"Team favored ({fav}) → -10% (fewer opponent shots)")
                else:
                    # GK's team is underdog → more shots faced → more saves
                    context_multiplier += 0.10
                    context_factors.append(f"Team underdog → +10% (more opponent shots)")
            if player_venue == "away":
                context_multiplier += 0.05
                context_factors.append("Away GK → +5% (typically face more pressure)")
            context_multiplier = round(context_multiplier, 2)

            # 4. THE FORMULA: Projected Saves = Opp Avg SoT × GK Save% × Context Multiplier
            projected_saves = round(opp_avg_sot * (gk_save_pct / 100) * context_multiplier, 1) if opp_avg_sot > 0 else gk_avg_saves

            gk_formula_data = {
                "opponentAvgShots": opp_avg_shots,
                "opponentAvgSOT": opp_avg_sot,
                "opponentVenue": opponent_venue.upper(),
                "opponentShotsSample": len(opp_shots_list),
                "gkSaveRate": gk_save_pct,
                "gkAvgSaves": gk_avg_saves,
                "gkSavesPer90": gk_saves_per90,
                "gkSampleSize": games_with_saves,
                "goalsAgainstPerGame": goals_against,
                "contextMultiplier": context_multiplier,
                "contextFactors": context_factors,
                "formulaProjection": projected_saves,
                "formula": f"{opp_avg_sot} SoT × {gk_save_pct}% save rate (last {games_with_saves} games) × {context_multiplier} context = {projected_saves}",
            }
            wave2_supplement["savesAnalysis"] = gk_formula_data

            saves_context = f"""
[ELITE GK SAVES FORMULA]
FORMULA: Projected Saves = Opponent Avg SoT × GK Save% × Match Context Multiplier

1. OPPONENT SHOTS ON TARGET ({opponent_venue.upper()} venue, last {len(opp_shots_list)} games):
   - Avg total shots/game: {opp_avg_shots}
   - Avg shots on TARGET/game: {opp_avg_sot}

2. GK SAVE RATE (last {games_with_saves} games):
   - Avg saves/game: {gk_avg_saves}
   - Saves per 90: {gk_saves_per90}
   - Estimated save %: {gk_save_pct}%
   - Team goals against/game ({player_venue}): {goals_against or 'N/A'}

3. MATCH CONTEXT MULTIPLIER: {context_multiplier}
   {chr(10).join('   - ' + f for f in context_factors) if context_factors else '   - Neutral'}

4. FORMULA RESULT: {opp_avg_sot} × {gk_save_pct}% × {context_multiplier} = {projected_saves} projected saves

COMPARE TO LINE: Line is {req.line}. Formula projects {projected_saves}.
{'LEAN OVER' if projected_saves > req.line else 'LEAN UNDER' if projected_saves < req.line else 'PUSH ZONE'} — but weight scenarios (blowout, cagey game, etc.)
"""

        # POSITION CONTEXT: Compute position-specific baseline from game logs
        position_context = ""
        if player_position and player_game_logs:
            pos_map = {"Goalkeeper": "GK", "Defender": "DEF", "Midfielder": "MID", "Attacker": "FWD"}
            pos_short = pos_map.get(player_position, player_position)
            position_context = f"\n[POSITION BASELINE] Player position: {player_position} ({pos_short}). Calibrate expectations for this position — {pos_short}s have different stat ceilings than other positions."

        # Compose final data: Structured data digest (code-built) + Grok tactical analysis + Wave 2 deep data
        final_data_parts = []
        if data_digest:
            final_data_parts.append(f"[DATA DIGEST — REAL API STATS]\n{data_digest}")
        if grok_analysis:
            final_data_parts.append(f"[GROK TACTICAL ANALYSIS — LIVE WEB SEARCH]\n{grok_analysis}")
        if wave2_supplement:
            final_data_parts.append(f"[DEEP MATCH DATA]\n{json.dumps(wave2_supplement, default=str)[:5000]}")

        if final_data_parts:
            final_data = "\n\n".join(final_data_parts)
            if saves_context:
                final_data += f"\n\n{saves_context}"
            if position_context:
                final_data += f"\n{position_context}"
        else:
            # Fallback: raw data if both AIs failed
            final_data = json.dumps(historical_data, default=str)[:18000]

        prompt = f"""DUAL AI PREDICTION — FINAL STAGE
{pronoun_note}

Player: {req.playerName} (ID: {req.playerId}) | Position: {player_position or 'Unknown'} | Opponent: {req.opponentName} | Venue: {req.venue.upper()} | Prop: {req.propType} | Line: {req.line}

CRITICAL VENUE CONTEXT: Player is {player_venue.upper()}. ALL data below is venue-filtered:
- Team stats = their {player_venue.upper()} performance
- Opponent stats = their {opponent_venue.upper()} performance
- Player game logs = prioritized {player_venue.upper()} games
- The {player_venue.upper()} average is MORE predictive than the overall average. Weight it 60-70%.

Stat mapping: pass_attempts=passes.total, shots=shots.total, shots_on_target=shots.on, tackles=tackles.total, key_passes=passes.key, saves=goals.saves, interceptions=tackles.interceptions, blocks=tackles.blocks, dribbles=dribbles.attempts, fouls_drawn=fouls.drawn

You have received pre-analyzed intel from Grok (live web search + tactical analysis):
1. DATA DIGEST — 100% real API numbers compiled by code (no AI distortion)
2. Grok's TACTICAL ANALYSIS — live injury/lineup news, scenario analysis, sensitivity tests, sub risk

YOUR JOB: Synthesize both into the final calibrated prediction JSON.
- Use Grok's scenario weights as your starting framework
- Cross-check against the data digest's real numbers
- If they disagree, explain why in your reasoning and go with the more evidence-based number
- DO NOT generate recentSamples — return empty array []. Backend injects real API data.
- Take Grok's sensitivityTests, subRisk, and gameFlowDynamics assessments — refine only if game log data contradicts them

TACTICAL INTEL:
- See Grok's tactical analysis above for live web intel (injuries, lineups, news) and matchup context
- Bookmaker odds: {json.dumps(match_odds, default=str) if match_odds else 'Not available — use Grok analysis and standings'}

CRITICAL ODDS RULE: If bookmaker odds present, lower odds = favored team. Trust bookmaker odds over all other signals.

ALL DATA:
{final_data}

Return ONLY valid JSON. recentSamples MUST be []. 10pt probabilityCurve."""

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
        # OVERRIDE: Always use real game log data instead of AI-generated samples
        if real_recent_samples:
            prediction["recentSamples"] = real_recent_samples
        prediction.setdefault("bayesianMetrics", {"priorMean": req.line, "momentumEffect": 0, "covariateAdjustment": 0, "reversalFlag": "stable"})
        prediction.setdefault("probabilityCurve", [])
        prediction.setdefault("reasoning", "Analysis based on available data.")
        prediction.setdefault("tacticalInsights", "")

        # OVERRIDE: Lock matchupOverview to REAL DATA so it never fluctuates between predictions
        real_matchup = prediction.get("matchupOverview", {})
        # 1. Possession from real fixture stats (venue-filtered averages)
        if team_fixture_stats or opponent_fixture_stats:
            def avg_possession(stats_list):
                vals = []
                for s in (stats_list or []):
                    p = s.get("possession")
                    if p is not None:
                        try:
                            vals.append(float(str(p).replace("%", "")))
                        except (ValueError, TypeError):
                            pass
                return round(sum(vals) / len(vals), 0) if vals else None
            team_poss = avg_possession(team_fixture_stats)
            opp_poss = avg_possession(opponent_fixture_stats)
            if team_poss is not None and opp_poss is not None:
                # Normalize so they add to 100
                total = team_poss + opp_poss
                if total > 0:
                    team_poss = round(team_poss / total * 100)
                    opp_poss = 100 - team_poss
                real_matchup["expectedPossession"] = {
                    "home": team_poss if player_venue == "home" else opp_poss,
                    "away": opp_poss if player_venue == "home" else team_poss
                }
            elif team_poss is not None:
                real_matchup["expectedPossession"] = {
                    "home": team_poss if player_venue == "home" else (100 - team_poss),
                    "away": (100 - team_poss) if player_venue == "home" else team_poss
                }
        # 2. Moneyline + favorite from real odds data
        if match_odds:
            if match_odds.get("bookmakerOdds"):
                bo = match_odds["bookmakerOdds"]
                real_matchup["moneyline"] = {
                    "home": bo.get("homeWin", "N/A"),
                    "draw": bo.get("draw", "N/A"),
                    "away": bo.get("awayWin", "N/A")
                }
            if match_odds.get("favorite"):
                real_matchup["favorite"] = match_odds["favorite"]
        # 3. Game type from real stats — deterministic classification
        if team_fixture_stats and opponent_fixture_stats:
            def avg_stat(stats_list, key):
                vals = [s.get(key) for s in stats_list if s.get(key) is not None]
                return sum(vals) / len(vals) if vals else 0
            team_avg_shots = avg_stat(team_fixture_stats, "totalShots")
            opp_avg_shots = avg_stat(opponent_fixture_stats, "totalShots")
            combined_shots = team_avg_shots + opp_avg_shots
            poss_diff = abs((real_matchup.get("expectedPossession", {}).get("home", 50)) - 50)
            if combined_shots >= 28:
                real_matchup["expectedGameType"] = "open"
            elif combined_shots <= 18:
                real_matchup["expectedGameType"] = "cagey"
            elif poss_diff >= 12:
                real_matchup["expectedGameType"] = "one-sided"
            else:
                real_matchup["expectedGameType"] = "high-tempo" if combined_shots >= 23 else "cagey"
        # 4. Always set team names from request data (deterministic)
        player_team = req.teamName or (player_stats.get("statistics", [{}])[0].get("team", {}).get("name", "") if player_stats else "")
        real_matchup["homeTeam"] = player_team if player_venue == "home" else req.opponentName
        real_matchup["awayTeam"] = req.opponentName if player_venue == "home" else player_team
        prediction["matchupOverview"] = real_matchup

        # DATA QUALITY INDICATOR — flag when API data might be unreliable
        total_game_logs = len(player_game_logs)
        gl_target_field_map_check = {
            "pass_attempts": "passes_total", "shots": "shots_total", "shots_on_target": "shots_on",
            "tackles": "tackles_total", "key_passes": "passes_key", "saves": "goals_saves",
            "interceptions": "tackles_interceptions", "blocks": "tackles_blocks",
            "dribbles": "dribbles_attempts", "fouls_drawn": "fouls_drawn",
        }
        target_check = gl_target_field_map_check.get(req.propType, "passes_total")
        games_with_data = sum(1 for g in player_game_logs if g.get(target_check) is not None)
        games_with_none = total_game_logs - games_with_data
        if total_game_logs > 0 and games_with_none / total_game_logs >= 0.3:
            prediction["dataQuality"] = {
                "level": "limited",
                "message": f"API data incomplete — {games_with_none} of {total_game_logs} recent games missing {req.propType} stats. Web-verified stats from Grok used for analysis.",
                "gamesWithData": games_with_data,
                "totalGames": total_game_logs,
            }
        elif total_game_logs < 3:
            prediction["dataQuality"] = {
                "level": "low",
                "message": f"Only {total_game_logs} game logs available. Limited sample size for accurate projection.",
                "gamesWithData": games_with_data,
                "totalGames": total_game_logs,
            }
        else:
            prediction["dataQuality"] = {
                "level": "good",
                "message": "",
                "gamesWithData": games_with_data,
                "totalGames": total_game_logs,
            }

        # Save to MongoDB
        prediction["_created"] = datetime.now(timezone.utc).isoformat()
        prediction["_request"] = req.model_dump()

        # Attach match stat data for frontend heat maps/visualizations
        if team_fixture_stats:
            prediction["teamMatchStats"] = team_fixture_stats
        if opponent_fixture_stats:
            prediction["opponentMatchStats"] = opponent_fixture_stats
        if historical_data.get("h2hPlayerStats"):
            prediction["h2hPlayerStats"] = historical_data["h2hPlayerStats"]
        if historical_data.get("playerGameLogs"):
            prediction["playerGameLogs"] = historical_data["playerGameLogs"]
        if gk_formula_data:
            prediction["gkFormula"] = gk_formula_data

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
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Prediction failed: {str(e)}")



class ComboRequest(BaseModel):
    leagueId: int
    player1Id: int
    player1Name: str
    player1TeamId: int
    player2Id: int
    player2Name: str
    player2TeamId: int
    opponentId: int
    opponentName: str
    venue: str = "home"
    propType: str = "pass_attempts"
    combinedLine: float = 0

@app.post("/api/predict-combo")
async def predict_combo(req: ComboRequest):
    """Start combo prediction — returns job_id immediately, frontend polls for result."""
    job_id = uuid.uuid4().hex[:12]
    
    # Store job status
    await db.combo_jobs.insert_one({
        "jobId": job_id,
        "status": "running",
        "request": req.model_dump(),
        "created": datetime.now(timezone.utc).isoformat(),
    })

    # Launch background task
    async def run_combo():
        try:
            p1_venue = req.venue
            p2_venue = req.venue
            p1_opponent_id = req.opponentId
            p1_opponent_name = req.opponentName
            p2_opponent_id = req.opponentId
            p2_opponent_name = req.opponentName

            if req.player2TeamId == req.opponentId:
                p2_venue = "away" if req.venue == "home" else "home"
                p2_opponent_id = req.player1TeamId
                p2_opponent_name = req.player1Name.split()[0] + "'s Team"

            pred_req1 = PredictionRequest(
                leagueId=req.leagueId, playerId=req.player1Id, playerName=req.player1Name,
                teamId=req.player1TeamId, opponentId=p1_opponent_id, opponentName=p1_opponent_name,
                venue=p1_venue, propType=req.propType, line=req.combinedLine / 2,
            )
            pred_req2 = PredictionRequest(
                leagueId=req.leagueId, playerId=req.player2Id, playerName=req.player2Name,
                teamId=req.player2TeamId, opponentId=p2_opponent_id, opponentName=p2_opponent_name,
                venue=p2_venue, propType=req.propType, line=req.combinedLine / 2,
            )

            result1, result2 = await aio.gather(predict(pred_req1), predict(pred_req2))

            combined_value = round((result1.get("projectedValue", 0) + result2.get("projectedValue", 0)) * 10) / 10
            avg_confidence = round((result1.get("confidenceScore", 50) + result2.get("confidenceScore", 50)) / 2)

            await db.combo_jobs.update_one({"jobId": job_id}, {"$set": {
                "status": "done",
                "result": {
                    "player1": result1,
                    "player2": result2,
                    "combined": {
                        "projectedValue": combined_value,
                        "line": req.combinedLine,
                        "recommendation": "over" if combined_value > req.combinedLine else "under" if combined_value < req.combinedLine else "push",
                        "confidenceScore": avg_confidence,
                        "confidenceLevel": "High" if avg_confidence >= 75 else "Medium" if avg_confidence >= 55 else "Low",
                    }
                }
            }})
        except Exception as e:
            await db.combo_jobs.update_one({"jobId": job_id}, {"$set": {
                "status": "error",
                "error": str(e),
            }})

    aio.create_task(run_combo())
    return {"jobId": job_id, "status": "running"}


@app.get("/api/predict-combo/{job_id}")
async def get_combo_result(job_id: str):
    """Poll for combo prediction result."""
    job = await db.combo_jobs.find_one({"jobId": job_id}, {"_id": 0})
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] == "done":
        return {"status": "done", "result": job["result"]}
    elif job["status"] == "error":
        raise HTTPException(status_code=500, detail=job.get("error", "Combo prediction failed"))
    return {"status": "running"}


# =============================================
# SCAN PROP — Image-to-Prediction
# =============================================
class ScanPropRequest(BaseModel):
    image_base64: str  # base64-encoded image data

PROP_TYPE_ALIASES = {
    "pass attempts": "pass_attempts",
    "passes attempted": "pass_attempts",
    "passes": "pass_attempts",
    "pass att": "pass_attempts",
    "shots": "shots",
    "shots on target": "shots_on_target",
    "sot": "shots_on_target",
    "tackles": "tackles",
    "key passes": "key_passes",
    "saves": "saves",
    "goalkeeper saves": "saves",
    "gk saves": "saves",
    "interceptions": "interceptions",
    "blocks": "blocks",
    "dribble attempts": "dribbles",
    "dribbles": "dribbles",
    "fouls drawn": "fouls_drawn",
    "assists": "key_passes",
}

@app.post("/api/scan-prop")
async def scan_prop(req: ScanPropRequest):
    """Use AI vision to extract player prop data from a screenshot."""
    try:
        chat = LlmChat(
            api_key=EMERGENT_LLM_KEY,
            session_id=f"scan-{uuid.uuid4().hex[:8]}",
            system_message="You are an expert at reading player prop screenshots. Extract structured data precisely."
        ).with_model("openai", "gpt-4o")

        image_content = ImageContent(image_base64=req.image_base64)

        leagues_list = ", ".join([f"{l['name']} (ID:{l['id']})" for l in SUPPORTED_LEAGUES])

        prompt = f"""Analyze this screenshot of a player prop card.

LAYOUT GUIDE:
- The player's FIRST NAME is on the top line, LAST NAME is on the second line (larger/bolder text)
- Below the name: "SOCCER • [Team Name] • [Position]"
- Below that: "vs [Opponent]" or "@ [Opponent]" with date/time
- The prop line number (e.g., 48.5) is shown prominently with the stat type below it (e.g., "Passes Attempted")
- "Less" and "More" buttons are just selection options — IGNORE them. Do NOT extract over/under from these.
- A bar chart may show the player's recent game history

CRITICAL RULES:
- Read the player name EXACTLY as shown on the card. The name displayed prominently at the top IS the player.
- Do NOT confuse player names with opponent names, team names, or any other text.
- If you see only ONE player card, return exactly ONE entry.
- IGNORE any "Less"/"More" buttons — they are irrelevant selection UI, not a prediction.

Extract for EACH player prop entry:
1. playerName — The player's full name as displayed (first + last)
2. propType — Map to one of: pass_attempts, shots, shots_on_target, tackles, key_passes, saves, interceptions, blocks, dribbles, fouls_drawn
3. line — The numerical line (e.g., 48.5)
4. opponentName — The opposing team from "vs [Team]"
5. league — Best guess league name
6. leagueId — Match to one of: {leagues_list}
7. playerTeam — The player's own team name

PROP TYPE MAPPING:
- "Passes Attempted" / "Pass Attempts" / "Passes" → pass_attempts
- "Shots" / "Shots Taken" → shots
- "Shots on Target" / "SOT" → shots_on_target
- "Tackles" → tackles
- "Key Passes" / "Assists" → key_passes
- "Saves" / "Goalkeeper Saves" → saves
- "Interceptions" → interceptions
- "Blocks" → blocks
- "Dribble Attempts" / "Dribbles" → dribbles
- "Fouls Drawn" → fouls_drawn

Return ONLY valid JSON array. Each element: {{"playerName":"...","propType":"...","line":0.0,"opponentName":"...","playerTeam":"...","venue":"home or away","league":"...","leagueId":0}}

VENUE RULES:
- If the matchup line says "@ [Team]" — the "@" means AWAY. The player's team is traveling to the opponent. venue = "away"
- If the matchup line says "vs [Team]" (no @) — the player's team is at HOME. venue = "home"
- Example: Player team is Botafogo, matchup says "@ Athletico PR" → venue = "away", opponentName = "Athletico PR"

If you cannot determine a field, use null. Always try to extract the line number.
If there's only one entry, still return it as an array with one element."""

        msg = UserMessage(text=prompt, file_contents=[image_content])
        response = await chat.send_message(msg)
        response_text = response.strip()

        # Clean markdown fences
        if response_text.startswith("```"):
            lines = response_text.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            response_text = "\n".join(lines)

        extracted = json.loads(response_text)
        if not isinstance(extracted, list):
            extracted = [extracted]

        # ── Team → League mapping for when AI can't determine league ──
        TEAM_LEAGUE_MAP = {
            # Brazil Serie A (71)
            "botafogo": 71, "flamengo": 71, "palmeiras": 71, "sao paulo": 71, "corinthians": 71,
            "atletico mineiro": 71, "atletico paranaense": 71, "athletico": 71, "athletico pr": 71,
            "gremio": 71, "internacional": 71, "cruzeiro": 71, "fluminense": 71, "santos": 71,
            "vasco": 71, "bahia": 71, "fortaleza": 71, "bragantino": 71, "juventude": 71,
            "cuiaba": 71, "goias": 71, "vitoria": 71, "sport": 71, "ceara": 71,
            # Premier League (39)
            "arsenal": 39, "chelsea": 39, "liverpool": 39, "manchester city": 39, "man city": 39,
            "manchester united": 39, "man united": 39, "tottenham": 39, "spurs": 39,
            "newcastle": 39, "aston villa": 39, "west ham": 39, "brighton": 39, "wolves": 39,
            "crystal palace": 39, "everton": 39, "fulham": 39, "brentford": 39, "bournemouth": 39,
            "nottingham forest": 39, "leicester": 39, "ipswich": 39, "southampton": 39,
            # La Liga (140)
            "real madrid": 140, "barcelona": 140, "atletico madrid": 140, "athletic bilbao": 140,
            "real sociedad": 140, "betis": 140, "villarreal": 140, "sevilla": 140, "girona": 140,
            "valencia": 140, "getafe": 140, "osasuna": 140, "celta vigo": 140, "mallorca": 140,
            "rayo vallecano": 140, "alaves": 140, "las palmas": 140, "cadiz": 140,
            # Bundesliga (78)
            "bayern munich": 78, "bayern": 78, "dortmund": 78, "borussia dortmund": 78,
            "leverkusen": 78, "bayer leverkusen": 78, "rb leipzig": 78, "leipzig": 78,
            "stuttgart": 78, "frankfurt": 78, "wolfsburg": 78, "freiburg": 78,
            # Serie A Italy (135)
            "inter milan": 135, "inter": 135, "ac milan": 135, "milan": 135, "juventus": 135,
            "napoli": 135, "roma": 135, "lazio": 135, "atalanta": 135, "fiorentina": 135,
            "bologna": 135, "torino": 135, "monza": 135, "genoa": 135, "cagliari": 135,
            # Ligue 1 (61)
            "psg": 61, "paris saint-germain": 61, "marseille": 61, "lyon": 61, "monaco": 61,
            "lille": 61, "lens": 61, "nice": 61, "rennes": 61, "strasbourg": 61,
            # MLS (253)
            "la galaxy": 253, "lafc": 253, "inter miami": 253, "atlanta united": 253,
            "new york city fc": 253, "nycfc": 253, "new york red bulls": 253, "seattle sounders": 253,
            "portland timbers": 253, "columbus crew": 253, "fc cincinnati": 253, "nashville sc": 253,
            # NWSL (254)
            "portland thorns": 254, "washington spirit": 254, "north carolina courage": 254,
            "orlando pride": 254, "gotham fc": 254, "angel city": 254, "kansas city current": 254,
            # International teams → Nations League / International (5)
            "italy": 5, "france": 5, "germany": 5, "spain": 5, "england": 5,
            "portugal": 5, "brazil": 5, "argentina": 5, "netherlands": 5, "belgium": 5,
            "croatia": 5, "usa": 5, "united states": 5, "mexico": 5, "japan": 5,
            "south korea": 5, "turkey": 5, "serbia": 5, "poland": 5, "denmark": 5,
            "sweden": 5, "norway": 5, "colombia": 5, "uruguay": 5, "chile": 5,
            "nigeria": 5, "senegal": 5, "morocco": 5, "egypt": 5, "australia": 5,
            "bosnia": 5, "bosnia & herzegovina": 5, "scotland": 5, "wales": 5,
            "switzerland": 5, "austria": 5, "czech republic": 5, "czechia": 5, "ukraine": 5,
            "romania": 5, "greece": 5, "costa rica": 5, "canada": 5, "iran": 5,
            "algeria": 5, "cameroon": 5, "ghana": 5, "ivory coast": 5, "tunisia": 5,
        }

        def infer_league_id(team_name, opponent_name, ai_league_id):
            """Infer league ID from team names when AI can't determine it."""
            if ai_league_id and ai_league_id != 39:
                # AI returned a specific non-default league, trust it
                return ai_league_id
            # Try team name
            for name in [team_name, opponent_name]:
                if not name:
                    continue
                name_lower = name.lower().strip()
                if name_lower in TEAM_LEAGUE_MAP:
                    return TEAM_LEAGUE_MAP[name_lower]
                # Partial match
                for key, lid in TEAM_LEAGUE_MAP.items():
                    if key in name_lower or name_lower in key:
                        return lid
            return ai_league_id or 71  # Default to Brasileirao if nothing found

        def strip_accents(text):
            """Remove diacritics and normalize Nordic/special chars for API search."""
            import unicodedata
            # Handle specific Nordic characters that NFKD doesn't decompose
            CHAR_MAP = {'ø': 'o', 'Ø': 'O', 'æ': 'ae', 'Æ': 'AE', 'å': 'a', 'Å': 'A',
                        'ð': 'd', 'Ð': 'D', 'þ': 'th', 'Þ': 'Th', 'ß': 'ss',
                        'ł': 'l', 'Ł': 'L', 'đ': 'd', 'Đ': 'D'}
            text = ''.join(CHAR_MAP.get(c, c) for c in text)
            nfkd = unicodedata.normalize('NFKD', text)
            return ''.join(c for c in nfkd if not unicodedata.category(c).startswith('M'))

        # Resolve each player via API-Sports search
        results = []
        for entry in extracted:
            player_name = entry.get("playerName")
            if not player_name:
                continue

            # Normalize prop type
            raw_prop = (entry.get("propType") or "").lower().strip()
            prop_type = PROP_TYPE_ALIASES.get(raw_prop, raw_prop)
            if prop_type not in ["pass_attempts", "shots", "shots_on_target", "tackles", "key_passes", "saves", "interceptions", "blocks", "dribbles", "fouls_drawn"]:
                prop_type = "pass_attempts"  # safe default

            player_team_hint = (entry.get("playerTeam") or "").lower().strip()
            opponent_hint = (entry.get("opponentName") or "").strip()
            ai_league_id = entry.get("leagueId")
            league_id = infer_league_id(entry.get("playerTeam"), opponent_hint, ai_league_id)
            league_name = entry.get("league")
            # Derive league name from ID if AI returned null
            if not league_name:
                for sl in SUPPORTED_LEAGUES:
                    if sl["id"] == league_id:
                        league_name = sl["name"]
                        break

            line = entry.get("line") or 0
            venue = (entry.get("venue") or "home").lower().strip()
            if venue not in ("home", "away"):
                venue = "home"

            # Search for player in API-Sports
            resolved_player = None
            try:
                search_query = player_name.strip()
                search_clean = strip_accents(search_query)
                # Build search variants: original, accent-stripped, last name, accent-stripped last name
                search_variants = []
                seen = set()
                for v in [search_query, search_clean]:
                    if v not in seen:
                        search_variants.append(v)
                        seen.add(v)
                name_parts = search_clean.split()
                if len(name_parts) > 1:
                    last = name_parts[-1]
                    if last not in seen:
                        search_variants.append(last)
                        seen.add(last)

                def pick_best_match(data_list, query, team_hint):
                    """Pick the best player match, preferring team name match."""
                    query_lower = strip_accents(query.lower())
                    last_name = query_lower.split()[-1] if query_lower.split() else query_lower
                    # Build team hint variants for fuzzy matching
                    team_hints = []
                    if team_hint:
                        team_hints.append(team_hint)
                        th_var = team_hint.replace("th", "t")
                        if th_var != team_hint:
                            team_hints.append(th_var)
                        # Expand abbreviations
                        for abbr, full in [("pr", "paranaense"), ("mg", "mineiro"), ("go", "goianiense")]:
                            if team_hint.endswith(f" {abbr}"):
                                expanded = team_hint[:-(len(abbr))].strip() + " " + full
                                team_hints.append(expanded)
                                team_hints.append(expanded.replace("th", "t"))
                        # Also add first word
                        team_hints.append(team_hint.split()[0])

                    candidates = []
                    for d in data_list[:20]:
                        pname = strip_accents(d["player"]["name"].lower())
                        team_name = (d.get("statistics", [{}])[0].get("team", {}).get("name") or "").lower()
                        name_match = query_lower in pname or pname in query_lower or last_name in pname
                        team_match = False
                        if team_hints:
                            for th in team_hints:
                                if th in team_name or team_name in th:
                                    team_match = True
                                    break
                        if name_match:
                            candidates.append((d, team_match))
                    # Prefer candidates where team also matches
                    if candidates:
                        team_matched = [c for c in candidates if c[1]]
                        if team_matched:
                            return team_matched[0][0]
                        # Only return non-team-matched if no team hint was provided
                        if not team_hint:
                            return candidates[0][0]
                    return None

                # International leagues where players are indexed under their CLUB, not national team
                INTERNATIONAL_LEAGUES = {1, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 15, 29, 30, 31, 32, 33, 34, 115, 960}

                # Map national teams to their most likely club leagues (players play domestically or in top-5)
                NATION_TO_LEAGUES = {
                    "italy": [135, 39, 140, 78, 61],
                    "france": [61, 39, 140, 135, 78],
                    "germany": [78, 39, 140, 135, 61],
                    "spain": [140, 39, 135, 78, 61],
                    "england": [39, 140, 135, 78, 61],
                    "portugal": [94, 39, 140, 135, 61],
                    "brazil": [71, 39, 140, 135, 61],
                    "argentina": [128, 39, 140, 135, 61],
                    "netherlands": [88, 39, 135, 78, 140],
                    "belgium": [144, 39, 135, 78, 61],
                    "usa": [253, 39, 140],
                    "united states": [253, 39, 140],
                    "mexico": [262, 253],
                    "japan": [39, 78, 135, 140, 61],
                    "south korea": [39, 78, 135, 140],
                    "turkey": [203, 39, 135],
                    "croatia": [39, 135, 78, 140, 61],
                    "serbia": [39, 135, 78, 61],
                    "poland": [39, 135, 140, 78],
                    "denmark": [61, 39, 135, 140, 78],
                    "sweden": [39, 135, 78],
                    "norway": [39, 135, 78],
                    "colombia": [71, 39, 140, 135, 61],
                    "uruguay": [140, 39, 71, 135],
                    "chile": [71, 39, 140],
                    "nigeria": [39, 135, 61],
                    "senegal": [39, 61, 135],
                    "morocco": [39, 61, 140, 135],
                    "egypt": [39, 135, 140],
                    "australia": [39, 253],
                    "saudi arabia": [307],
                    "bosnia": [135, 78, 39, 61],
                    "bosnia & herzegovina": [135, 78, 39, 61],
                    "scotland": [39, 135],
                    "wales": [39, 135],
                    "switzerland": [78, 135, 39, 61],
                    "austria": [78, 135, 39],
                    "czech republic": [78, 39, 135],
                    "czechia": [78, 39, 135],
                    "ukraine": [39, 78, 135, 61],
                    "romania": [39, 135, 78],
                    "greece": [39, 135, 78],
                    "costa rica": [253, 39],
                    "canada": [253, 39, 61],
                    "iran": [39, 78],
                    "algeria": [61, 39],
                    "cameroon": [61, 39, 135],
                    "ghana": [39, 61, 135],
                    "ivory coast": [39, 61],
                    "tunisia": [61, 39],
                }
                TOP_5_LEAGUES = [39, 140, 135, 78, 61]  # Fallback: EPL, La Liga, Serie A, Bundesliga, Ligue 1

                is_international = league_id in INTERNATIONAL_LEAGUES

                # Build league search order
                if is_international:
                    # Use national team name to narrow down which club leagues to search
                    team_lower = player_team_hint or ""
                    leagues_to_try = NATION_TO_LEAGUES.get(team_lower, TOP_5_LEAGUES)
                else:
                    leagues_to_try = [league_id]

                for variant in search_variants:
                    if resolved_player:
                        break
                    if len(variant) < 3:
                        continue
                    for try_league in leagues_to_try:
                        if resolved_player:
                            break
                        for season in [CURRENT_SEASON + 1, CURRENT_SEASON]:
                            try:
                                data = await api_football_request("players", {"search": variant, "league": try_league, "season": season})
                                if data:
                                    best = pick_best_match(data, search_query, player_team_hint)
                                    if best:
                                        resolved_player = {
                                            "playerId": best["player"]["id"],
                                            "playerName": best["player"]["name"],
                                            "photo": "",
                                            "teamId": best.get("statistics", [{}])[0].get("team", {}).get("id"),
                                            "teamName": best.get("statistics", [{}])[0].get("team", {}).get("name", ""),
                                        }
                                        actual_league = best.get("statistics", [{}])[0].get("league", {})
                                        if actual_league.get("id"):
                                            league_id = actual_league["id"]
                                            league_name = actual_league.get("name", league_name)
                                        break
                                    elif not player_team_hint or is_international:
                                        # No team hint or international (club won't match national team name) — accept first
                                        best = data[0]
                                        resolved_player = {
                                            "playerId": best["player"]["id"],
                                            "playerName": best["player"]["name"],
                                            "photo": "",
                                            "teamId": best.get("statistics", [{}])[0].get("team", {}).get("id"),
                                            "teamName": best.get("statistics", [{}])[0].get("team", {}).get("name", ""),
                                        }
                                        actual_league = best.get("statistics", [{}])[0].get("league", {})
                                        if actual_league.get("id"):
                                            league_id = actual_league["id"]
                                            league_name = actual_league.get("name", league_name)
                                        break
                            except Exception:
                                continue

                # Squad-based fallback: if player not found via search
                if not resolved_player and player_team_hint:
                    try:
                        if not is_international:
                            # Club match: resolve team and search its squad
                            team_search_variants = [player_team_hint]
                            th_variant = player_team_hint.replace("th", "t")
                            if th_variant != player_team_hint:
                                team_search_variants.append(th_variant)
                            ABBREV_MAP = {"pr": "paranaense", "mg": "mineiro", "go": "goianiense", "rj": "rio"}
                            for abbr, full in ABBREV_MAP.items():
                                if player_team_hint.endswith(f" {abbr}"):
                                    expanded = player_team_hint[:-(len(abbr))].strip() + " " + full
                                    team_search_variants.append(expanded)
                                    ev = expanded.replace("th", "t")
                                    if ev != expanded:
                                        team_search_variants.append(ev)

                            resolved_team_id = None
                            for tsv in team_search_variants:
                                if resolved_team_id:
                                    break
                                try:
                                    teams_data = await api_football_request("teams", {"search": tsv})
                                    if teams_data:
                                        for t in teams_data[:10]:
                                            tname_lower = t.get("team", {}).get("name", "").lower()
                                            is_youth = any(s in tname_lower for s in ["u20", "u23", "u21", "u19", "u18", "u17"])
                                            if not is_youth:
                                                resolved_team_id = t["team"]["id"]
                                                break
                                except Exception:
                                    continue

                            if resolved_team_id:
                                squad_data = await api_football_request("players/squads", {"team": resolved_team_id})
                                if squad_data:
                                    squad_players = squad_data[0].get("players", []) if squad_data else []
                                    search_lower = strip_accents(search_query.lower())
                                    last_name_lower = search_lower.split()[-1] if search_lower.split() else search_lower
                                    for sp in squad_players:
                                        sp_name = strip_accents(sp.get("name", "").lower())
                                        if search_lower == sp_name or last_name_lower == sp_name or search_lower in sp_name or sp_name in search_lower:
                                            resolved_player = {
                                                "playerId": sp["id"],
                                                "playerName": sp["name"],
                                                "photo": "",
                                                "teamId": resolved_team_id,
                                                "teamName": player_team_hint.title(),
                                            }
                                            break
                        else:
                            # International match: search the NATIONAL TEAM squad first
                            team_lower = player_team_hint or ""
                            search_lower = strip_accents(search_query.lower())
                            last_name_lower = search_lower.split()[-1] if search_lower.split() else search_lower

                            # Resolve the national team ID
                            nat_team_id = None
                            try:
                                teams_data = await api_football_request("teams", {"search": team_lower.title()})
                                if teams_data:
                                    for t in teams_data[:5]:
                                        # National teams have the country name as team name
                                        tname = t.get("team", {}).get("name", "").lower()
                                        if team_lower in tname or tname in team_lower:
                                            nat_team_id = t["team"]["id"]
                                            break
                            except Exception:
                                pass

                            if nat_team_id:
                                try:
                                    squad_data = await api_football_request("players/squads", {"team": nat_team_id})
                                    if squad_data:
                                        for sp in squad_data[0].get("players", []):
                                            sp_name = strip_accents(sp.get("name", "").lower())
                                            if last_name_lower in sp_name or search_lower in sp_name or sp_name in search_lower:
                                                found_player_id = sp["id"]
                                                # Now find their club team by searching player by ID
                                                club_leagues = NATION_TO_LEAGUES.get(team_lower, TOP_5_LEAGUES)
                                                club_team_id = None
                                                club_team_name = ""
                                                for cl in club_leagues[:5]:
                                                    if club_team_id:
                                                        break
                                                    for try_szn in [CURRENT_SEASON, CURRENT_SEASON - 1]:
                                                        try:
                                                            pdata = await api_football_request("players", {"id": found_player_id, "league": cl, "season": try_szn})
                                                            if pdata:
                                                                club_team_id = pdata[0].get("statistics", [{}])[0].get("team", {}).get("id")
                                                                club_team_name = pdata[0].get("statistics", [{}])[0].get("team", {}).get("name", "")
                                                                actual_league = pdata[0].get("statistics", [{}])[0].get("league", {})
                                                                if actual_league.get("id"):
                                                                    league_id = actual_league["id"]
                                                                    league_name = actual_league.get("name", league_name)
                                                                break
                                                        except Exception:
                                                            continue
                                                resolved_player = {
                                                    "playerId": found_player_id,
                                                    "playerName": sp["name"],
                                                    "photo": "",
                                                    "teamId": club_team_id or nat_team_id,
                                                    "teamName": club_team_name or team_lower.title(),
                                                }
                                                break
                                except Exception:
                                    pass
                    except Exception:
                        pass

                # Fallback: broader search without league filter
                if not resolved_player:
                    for variant in search_variants:
                        if resolved_player:
                            break
                        if len(variant) < 3:
                            continue
                        for season in [CURRENT_SEASON + 1, CURRENT_SEASON]:
                            try:
                                data = await api_football_request("players", {"search": variant, "season": season})
                                if data:
                                    best = pick_best_match(data, search_query, player_team_hint)
                                    if best:
                                        resolved_player = {
                                            "playerId": best["player"]["id"],
                                            "playerName": best["player"]["name"],
                                            "photo": "",
                                            "teamId": best.get("statistics", [{}])[0].get("team", {}).get("id"),
                                            "teamName": best.get("statistics", [{}])[0].get("team", {}).get("name", ""),
                                        }
                                        actual_league = best.get("statistics", [{}])[0].get("league", {})
                                        if actual_league.get("id"):
                                            league_id = actual_league["id"]
                                            league_name = actual_league.get("name", league_name)
                                        break
                                    elif not player_team_hint or is_international:
                                        best = data[0]
                                        resolved_player = {
                                            "playerId": best["player"]["id"],
                                            "playerName": best["player"]["name"],
                                            "photo": "",
                                            "teamId": best.get("statistics", [{}])[0].get("team", {}).get("id"),
                                            "teamName": best.get("statistics", [{}])[0].get("team", {}).get("name", ""),
                                        }
                                        actual_league = best.get("statistics", [{}])[0].get("league", {})
                                        if actual_league.get("id"):
                                            league_id = actual_league["id"]
                                            league_name = actual_league.get("name", league_name)
                                        break
                            except Exception:
                                continue
            except Exception:
                pass

            # Resolve opponent team
            resolved_opponent = None
            opponent_name = entry.get("opponentName")
            if opponent_name and resolved_player:
                try:
                    opp_lower = opponent_name.lower().strip()
                    # Try multiple search variants for the opponent
                    opp_searches = [opponent_name]
                    clean_opp = opponent_name.strip()
                    # Expand common team name abbreviations
                    TEAM_ABBREVS = {"PR": "Paranaense", "MG": "Mineiro", "GO": "Goianiense", "RJ": "Rio"}
                    for abbr, full in TEAM_ABBREVS.items():
                        if clean_opp.upper().endswith(f" {abbr}"):
                            expanded = clean_opp[:-(len(abbr))].strip() + " " + full
                            opp_searches.insert(1, expanded)
                            # Also add th→t variant of the expanded name
                            expanded_v = expanded.replace("th", "t").replace("Th", "T")
                            if expanded_v != expanded:
                                opp_searches.insert(2, expanded_v)
                    # Common spelling variants (Athletico → Atletico)
                    variant_th = clean_opp.replace("th", "t").replace("Th", "T")
                    if variant_th != clean_opp and variant_th not in opp_searches:
                        opp_searches.append(variant_th)
                    # Strip common abbreviations as last resort
                    for suffix in [" PR", " FC", " SC", " CF", " AC", " MG", " GO", " RJ"]:
                        if clean_opp.upper().endswith(suffix):
                            stripped = clean_opp[:-len(suffix)].strip()
                            stripped_variant = stripped.replace("th", "t").replace("Th", "T")
                            if stripped_variant != stripped and stripped_variant not in opp_searches:
                                opp_searches.append(stripped_variant)
                            if stripped not in opp_searches:
                                opp_searches.append(stripped)

                    best_team = None
                    teams_data = []
                    first_word = opp_lower.split()[0]
                    first_word_variant = first_word.replace("th", "t")
                    for opp_query in opp_searches:
                        if best_team:
                            break
                        teams_data = await api_football_request("teams", {"search": opp_query})
                        if teams_data:
                            for t in teams_data[:15]:
                                tname = t.get("team", {}).get("name", "")
                                tname_lower = tname.lower()
                                is_youth = any(s in tname_lower for s in ["u20", "u23", "u21", "u19", "u18", "u17", " ii", " b "])
                                is_women = tname_lower.endswith(" w")
                                name_match = first_word in tname_lower or first_word_variant in tname_lower
                                if name_match and not is_youth and not is_women:
                                    best_team = t
                                    break
                    if not best_team and teams_data:
                        best_team = teams_data[0]
                    if best_team:
                        resolved_opponent = {
                            "teamId": best_team["team"]["id"],
                            "teamName": best_team["team"]["name"],
                        }
                except Exception:
                    pass

            results.append({
                "extracted": {
                    "playerName": player_name,
                    "propType": prop_type,
                    "line": line,
                    "venue": venue,
                    "opponentName": entry.get("opponentName"),
                    "playerTeam": entry.get("playerTeam"),
                    "league": league_name or entry.get("league"),
                    "leagueId": league_id,
                },
                "resolved": resolved_player,
                "resolvedOpponent": resolved_opponent,
            })

        return {"picks": results}

    except json.JSONDecodeError:
        raise HTTPException(status_code=422, detail="AI could not parse the image. Try a clearer screenshot.")
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Scan failed: {str(e)}")



class ChatStartRequest(BaseModel):
    session_id: Optional[str] = None


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


@app.post("/api/chat/start")
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


# =============================================
# PICKS CRUD — MongoDB-backed persistence
# =============================================

class SavePickRequest(BaseModel):
    email: str
    token: str
    pick: dict

@app.post("/api/picks/save")
async def save_pick(req: SavePickRequest):
    session = await db.sessions.find_one({"email": req.email.lower(), "session_token": req.token}, {"_id": 0})
    if not session:
        raise HTTPException(status_code=401, detail="Invalid session")
    pick = req.pick
    pick_id = pick.get("id") or str(uuid.uuid4())[:8]
    doc = {
        "pickId": pick_id,
        "email": req.email.lower(),
        "playerId": pick.get("player", {}).get("id"),
        "playerName": pick.get("player", {}).get("name", ""),
        "teamName": pick.get("player", {}).get("team", ""),
        "teamId": pick.get("_request", {}).get("teamId", 0),
        "opponentId": pick.get("_request", {}).get("opponentId", 0),
        "opponentName": pick.get("opponent", ""),
        "leagueId": pick.get("_request", {}).get("leagueId", 0),
        "propType": pick.get("propType", ""),
        "line": pick.get("line", 0),
        "recommendation": pick.get("recommendation", "over"),
        "projectedValue": pick.get("projectedValue", 0),
        "confidenceScore": pick.get("confidenceScore", 50),
        "confidenceLevel": pick.get("confidenceLevel", "Medium"),
        "confidenceInterval": pick.get("confidenceInterval", []),
        "venue": pick.get("_request", {}).get("venue", "home"),
        "status": "live",
        "result": "pending",
        "actualValue": None,
        "matchScore": None,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "settledAt": None,
    }
    await db.picks.update_one({"pickId": pick_id, "email": req.email.lower()}, {"$set": doc}, upsert=True)
    return {"success": True, "pickId": pick_id}

class GetPicksRequest(BaseModel):
    email: str
    token: str

@app.post("/api/picks/list")
async def list_picks(req: GetPicksRequest):
    session = await db.sessions.find_one({"email": req.email.lower(), "session_token": req.token}, {"_id": 0})
    if not session:
        raise HTTPException(status_code=401, detail="Invalid session")
    picks = await db.picks.find({"email": req.email.lower()}, {"_id": 0}).sort("timestamp", -1).to_list(100)
    return {"picks": picks}

class DeletePickRequest(BaseModel):
    email: str
    token: str
    pickId: str

@app.post("/api/picks/delete")
async def delete_pick(req: DeletePickRequest):
    session = await db.sessions.find_one({"email": req.email.lower(), "session_token": req.token}, {"_id": 0})
    if not session:
        raise HTTPException(status_code=401, detail="Invalid session")
    await db.picks.delete_one({"pickId": req.pickId, "email": req.email.lower()})
    return {"success": True}


class CorrectPickRequest(BaseModel):
    email: str
    token: str
    pickId: str
    actualValue: float

@app.post("/api/picks/correct")
async def correct_pick(req: CorrectPickRequest):
    """Manual correction for settled picks when API data was wrong."""
    session = await db.sessions.find_one({"email": req.email.lower(), "session_token": req.token}, {"_id": 0})
    if not session:
        raise HTTPException(status_code=401, detail="Invalid session")
    pick = await db.picks.find_one({"pickId": req.pickId, "email": req.email.lower()}, {"_id": 0})
    if not pick:
        raise HTTPException(status_code=404, detail="Pick not found")
    line = pick.get("line", 0)
    rec = pick.get("recommendation", "over")
    if req.actualValue == line:
        result_str = "push"
    elif (rec == "over" and req.actualValue > line) or (rec == "under" and req.actualValue < line):
        result_str = "hit"
    else:
        result_str = "miss"
    await db.picks.update_one(
        {"pickId": req.pickId, "email": req.email.lower()},
        {"$set": {"actualValue": req.actualValue, "result": result_str, "correctedManually": True}}
    )
    return {"success": True, "result": result_str, "actualValue": req.actualValue}



# =============================================
# LIVE TRACKING — Real-time in-game stats
# =============================================

class LiveUpdateRequest(BaseModel):
    email: str
    token: str

@app.post("/api/picks/live-update")
async def live_update_picks(req: LiveUpdateRequest):
    """For each live pick, check if match is in progress or finished. Return current stats."""
    session = await db.sessions.find_one({"email": req.email.lower(), "session_token": req.token}, {"_id": 0})
    if not session:
        raise HTTPException(status_code=401, detail="Invalid session")

    live_picks = await db.picks.find({"email": req.email.lower(), "status": "live"}, {"_id": 0}).to_list(50)
    if not live_picks:
        return {"updates": []}

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

    # Group picks by team to minimize API calls
    team_picks = {}
    for pick in live_picks:
        tid = pick.get("teamId", 0)
        if tid not in team_picks:
            team_picks[tid] = []
        team_picks[tid].append(pick)

    updates = []

    async def process_team_picks(team_id, picks_for_team):
        """Find the fixture for this team and get live/final stats for each pick."""
        results = []
        try:
            # Get team's most recent/live fixtures
            fixtures = await api_football_request("fixtures", {"team": team_id, "last": 3})
            if not fixtures:
                # Also check live fixtures
                fixtures = await api_football_request("fixtures", {"live": "all"})
                if fixtures:
                    fixtures = [f for f in fixtures if
                        f.get("teams", {}).get("home", {}).get("id") == team_id or
                        f.get("teams", {}).get("away", {}).get("id") == team_id]

            if not fixtures:
                return results

            for pick in picks_for_team:
                opponent_name = pick.get("opponentName", "")
                pick_ts = pick.get("timestamp", "")

                # Find the matching fixture
                matched_fixture = None
                for f in fixtures:
                    home_name = f.get("teams", {}).get("home", {}).get("name", "")
                    away_name = f.get("teams", {}).get("away", {}).get("name", "")
                    status_short = f.get("fixture", {}).get("status", {}).get("short", "")
                    fixture_date = f.get("fixture", {}).get("date", "")

                    if not (opponent_name.lower() in home_name.lower() or opponent_name.lower() in away_name.lower()):
                        continue

                    # Must be after pick was created
                    try:
                        if pick_ts:
                            pick_dt = datetime.fromisoformat(pick_ts.replace("Z", "+00:00")) if isinstance(pick_ts, str) else datetime.fromtimestamp(pick_ts / 1000, tz=timezone.utc)
                            fix_dt = datetime.fromisoformat(fixture_date.replace("Z", "+00:00"))
                            if fix_dt < pick_dt:
                                continue
                    except Exception:
                        pass

                    matched_fixture = f
                    break

                if not matched_fixture:
                    results.append({"pickId": pick["pickId"], "matchStatus": "scheduled"})
                    continue

                fixture_id = matched_fixture.get("fixture", {}).get("id")
                status_short = matched_fixture.get("fixture", {}).get("status", {}).get("short", "")
                elapsed = matched_fixture.get("fixture", {}).get("status", {}).get("elapsed") or 0
                home_goals = matched_fixture.get("goals", {}).get("home", 0) or 0
                away_goals = matched_fixture.get("goals", {}).get("away", 0) or 0
                match_score = f"{home_goals}-{away_goals}"

                # Status categories
                live_statuses = {"1H", "2H", "ET", "BT", "P", "LIVE", "HT"}
                finished_statuses = {"FT", "AET", "PEN"}
                is_live = status_short in live_statuses
                is_finished = status_short in finished_statuses

                if not is_live and not is_finished:
                    results.append({"pickId": pick["pickId"], "matchStatus": "scheduled", "fixtureId": fixture_id})
                    continue

                # Fetch player's current in-game stats
                player_stats_data = await api_football_request("fixtures/players", {"fixture": fixture_id})
                current_value = None
                minutes_played = 0

                if player_stats_data:
                    player_id = pick.get("playerId")
                    for team_data in player_stats_data:
                        for p in team_data.get("players", []):
                            if p.get("player", {}).get("id") == player_id:
                                pstats = p.get("statistics", [{}])[0] if p.get("statistics") else {}
                                minutes_played = pstats.get("games", {}).get("minutes") or 0
                                getter = stat_map.get(pick.get("propType", ""))
                                if getter:
                                    current_value = getter(pstats)
                                break
                        if current_value is not None:
                            break

                current_value = current_value or 0
                line = pick.get("line", 0)
                recommendation = pick.get("recommendation", "over")

                # Calculate pace (extrapolate to 90 min)
                effective_elapsed = max(elapsed, 1)
                pace = round((current_value / effective_elapsed) * 90, 1) if effective_elapsed > 0 else 0

                # Calculate hit probability
                if is_finished:
                    hit_pct = 100 if ((recommendation == "over" and current_value > line) or
                                     (recommendation == "under" and current_value < line)) else 0
                    if current_value == line:
                        hit_pct = 50  # push
                else:
                    # Based on pace vs line
                    if recommendation == "over":
                        if pace > line * 1.3:
                            hit_pct = min(95, 60 + (elapsed / 90) * 35)
                        elif pace > line:
                            hit_pct = min(85, 50 + (elapsed / 90) * 30)
                        elif pace > line * 0.7:
                            hit_pct = max(15, 40 - (line - pace) / line * 30)
                        else:
                            hit_pct = max(5, 20 - (elapsed / 90) * 15)
                    else:  # under
                        if pace < line * 0.7:
                            hit_pct = min(95, 60 + (elapsed / 90) * 35)
                        elif pace < line:
                            hit_pct = min(85, 50 + (elapsed / 90) * 30)
                        elif pace < line * 1.3:
                            hit_pct = max(15, 40 - (pace - line) / max(line, 1) * 30)
                        else:
                            hit_pct = max(5, 20 - (elapsed / 90) * 15)
                    hit_pct = round(hit_pct)

                update = {
                    "pickId": pick["pickId"],
                    "matchStatus": "final" if is_finished else "live",
                    "fixtureId": fixture_id,
                    "elapsed": elapsed,
                    "currentValue": current_value,
                    "minutesPlayed": minutes_played,
                    "pace": pace,
                    "hitPct": hit_pct,
                    "matchScore": match_score,
                }

                # If finished, settle the pick in DB
                if is_finished:
                    if current_value == line:
                        result_str = "push"
                    elif (current_value > line and recommendation == "over") or \
                         (current_value < line and recommendation == "under"):
                        result_str = "hit"
                    else:
                        result_str = "miss"
                    update["result"] = result_str
                    update["actualValue"] = current_value
                    await db.picks.update_one(
                        {"pickId": pick["pickId"], "email": req.email.lower()},
                        {"$set": {"status": "settled", "result": result_str, "actualValue": current_value, "matchScore": match_score, "minutesPlayed": minutes_played, "settledAt": datetime.now(timezone.utc).isoformat()}}
                    )

                results.append(update)
        except Exception:
            pass
        return results

    # Process all teams in parallel
    tasks = [process_team_picks(tid, picks) for tid, picks in team_picks.items()]
    all_results = await aio.gather(*tasks)
    for r in all_results:
        updates.extend(r)

    return {"updates": updates}


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
            # CRITICAL: Only match fixtures that happened AFTER the pick was created
            # to avoid settling against old meetings between the same teams
            pick_timestamp = pick.get("timestamp", 0)
            pick_created = datetime.fromtimestamp(pick_timestamp / 1000, tz=timezone.utc) if pick_timestamp else datetime.min.replace(tzinfo=timezone.utc)

            recent = None
            for s in [CURRENT_SEASON + 1, CURRENT_SEASON]:
                try:
                    data = await api_football_request("fixtures", {"team": team_id, "last": 5, "season": s})
                    if data:
                        for f in data:
                            home = f.get("teams", {}).get("home", {}).get("name", "")
                            away = f.get("teams", {}).get("away", {}).get("name", "")
                            status = f.get("fixture", {}).get("status", {}).get("short", "")
                            fixture_date_str = f.get("fixture", {}).get("date", "")

                            # Only consider finished matches
                            if status not in ("FT", "AET", "PEN"):
                                continue
                            # Must match opponent name
                            if not (opponent.lower() in home.lower() or opponent.lower() in away.lower()):
                                continue
                            # MUST have occurred AFTER the pick was saved
                            try:
                                fixture_dt = datetime.fromisoformat(fixture_date_str.replace("Z", "+00:00"))
                                if fixture_dt < pick_created:
                                    continue  # This is an OLD match, skip it
                            except Exception:
                                continue  # Can't parse date, skip to be safe

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

                # Handle push (exact match on whole-number lines)
                if actual_value == line:
                    result_str = "push"
                elif (actual_value > line and recommendation == "over") or \
                     (actual_value < line and recommendation == "under"):
                    result_str = "hit"
                else:
                    result_str = "miss"

                settled.append({
                    "pickId": pick.get("id"),
                    "status": "settled",
                    "result": result_str,
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
