import os
import json
import httpx
import uuid
import asyncio as aio
import time
import bcrypt
import statistics as stats_mod
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

        # Strategy 1b: If full name returned nothing in league, try last name within SAME league
        if not all_players and " " in req.query:
            last_name = req.query.strip().split()[-1]
            for s in [season, season - 1, season + 1]:
                try:
                    data = await api_football_request("players", {"search": last_name, "league": req.league_id, "season": s})
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

        # Tactical intel fetchers
        async def get_opponent_formations():
            """Get opponent's recent formations from last 5 fixtures"""
            try:
                fixtures = None
                for s in [CURRENT_SEASON + 1, CURRENT_SEASON]:
                    try:
                        fixtures = await api_football_request("fixtures", {"team": req.opponentId, "last": 5, "season": s})
                        if fixtures:
                            break
                    except Exception:
                        continue
                if not fixtures:
                    return []
                formations = []
                fids = [f.get("fixture", {}).get("id") for f in fixtures if f.get("fixture", {}).get("id")]
                # Fetch lineups for most recent match only (less API calls)
                if fids:
                    try:
                        lineups = await api_football_request("fixtures/lineups", {"fixture": fids[0]})
                        for l in (lineups or []):
                            if l.get("team", {}).get("id") == req.opponentId:
                                formations.append({
                                    "formation": l.get("formation", ""),
                                    "coach": l.get("coach", {}).get("name", ""),
                                })
                    except Exception:
                        pass
                return formations
            except Exception:
                return []

        async def get_prematch_prediction():
            """Get API-Sports pre-match prediction + actual bookmaker odds"""
            try:
                fixtures = None
                for s in [CURRENT_SEASON + 1, CURRENT_SEASON]:
                    try:
                        fixtures = await api_football_request("fixtures", {
                            "team": actual_team_id or 40,
                            "next": 1,
                            "season": s
                        })
                        if fixtures:
                            break
                    except Exception:
                        continue
                if not fixtures:
                    return None

                fid = fixtures[0].get("fixture", {}).get("id")
                result = {}

                # Get prediction
                try:
                    pred = await api_football_request("predictions", {"fixture": fid})
                    if pred:
                        p = pred[0]
                        result["apiPrediction"] = {
                            "winner": p.get("predictions", {}).get("winner", {}).get("name", ""),
                            "advice": p.get("predictions", {}).get("advice", ""),
                            "homeWinPct": p.get("predictions", {}).get("percent", {}).get("home", ""),
                            "drawPct": p.get("predictions", {}).get("percent", {}).get("draw", ""),
                            "awayWinPct": p.get("predictions", {}).get("percent", {}).get("away", ""),
                            "homeForm": p.get("teams", {}).get("home", {}).get("league", {}).get("form", ""),
                            "awayForm": p.get("teams", {}).get("away", {}).get("league", {}).get("form", ""),
                        }
                except Exception:
                    pass

                # Get actual bookmaker odds (MORE RELIABLE than prediction model)
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
                                    # Determine actual favorite from odds
                                    try:
                                        home_odd = float(vals.get("Home", 99))
                                        away_odd = float(vals.get("Away", 99))
                                        if home_odd < away_odd:
                                            result["actualFavorite"] = "home"
                                        else:
                                            result["actualFavorite"] = "away"
                                    except Exception:
                                        pass
                except Exception:
                    pass

                return result if result else None
            except Exception:
                return None

        # Injury fetcher — get injuries for the upcoming fixture
        async def get_fixture_injuries():
            """Get injuries/suspensions for both teams in the upcoming fixture"""
            try:
                # Find the next fixture between these teams
                fixtures = None
                for s in [CURRENT_SEASON + 1, CURRENT_SEASON]:
                    try:
                        fixtures = await api_football_request("fixtures", {
                            "team": actual_team_id or 40,
                            "next": 1,
                            "season": s
                        })
                        if fixtures:
                            break
                    except Exception:
                        continue
                if not fixtures:
                    return []
                fid = fixtures[0].get("fixture", {}).get("id")
                injuries = await api_football_request("injuries", {"fixture": fid})
                return injuries or []
            except Exception:
                return []

        # Fire ALL API calls at once — including tactical intel + injuries
        player_data_task = get_player_data()
        async def get_team_stats_multi_season(team_id, lid):
            for s in [CURRENT_SEASON + 1, CURRENT_SEASON, CURRENT_SEASON - 1]:
                result = await safe_fetch("teams/statistics", {"team": team_id, "league": lid, "season": s})
                if result:
                    return result
            return None

        team_stats_task = get_team_stats_multi_season(actual_team_id or 40, league_id)
        opponent_stats_task = get_team_stats_multi_season(req.opponentId, league_id)
        h2h_task = safe_fetch("fixtures/headtohead", {"h2h": f"{actual_team_id or 40}-{req.opponentId}", "last": 5}, [])

        async def get_standings_multi_season():
            for s in [CURRENT_SEASON + 1, CURRENT_SEASON, CURRENT_SEASON - 1]:
                result = await safe_fetch("standings", {"league": league_id, "season": s})
                if result:
                    return result
            return None

        standings_task = get_standings_multi_season()
        fixtures_task = get_recent_fixtures_fast(actual_team_id or 40, 20)
        formations_task = get_opponent_formations()
        prediction_task = get_prematch_prediction()
        injuries_task = get_fixture_injuries()

        player_stats, team_stats, opponent_stats, h2h_data, standings_raw, recent_fixtures, opponent_formations, prematch_pred, injuries_data = await aio.gather(
            player_data_task, team_stats_task, opponent_stats_task, h2h_task, standings_task, fixtures_task, formations_task, prediction_task, injuries_task
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
            """Fetch player's individual stats from each recent fixture"""
            results = []
            for fix in fixture_list[:limit]:
                fid = fix.get("fixtureId")
                if not fid:
                    continue
                try:
                    data = await api_football_request("fixtures/players", {"fixture": fid})
                    if not data:
                        continue
                    # Find our player in the fixture
                    found = False
                    for team_data in data:
                        for p in team_data.get("players", []):
                            if p.get("player", {}).get("id") == player_id:
                                stats = p.get("statistics", [{}])[0] if p.get("statistics") else {}
                                minutes = stats.get("games", {}).get("minutes") or 0
                                rating = stats.get("games", {}).get("rating")
                                # Extract ALL prop-relevant stats
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
                                }
                                # Calculate per-90 for the target stat
                                stat_field_map = {
                                    "pass_attempts": "passes_total",
                                    "shots": "shots_total",
                                    "shots_on_target": "shots_on",
                                    "tackles": "tackles_total",
                                    "key_passes": "passes_key",
                                    "interceptions": "tackles_interceptions",
                                    "blocks": "tackles_blocks",
                                    "dribbles": "dribbles_attempts",
                                    "fouls_drawn": "fouls_drawn",
                                }
                                raw_val = game_log.get(stat_field_map.get(req.propType, ""), None)
                                if raw_val is not None and minutes > 0:
                                    game_log["targetStatPer90"] = round((raw_val / minutes) * 90, 2)
                                results.append(game_log)
                                found = True
                                break
                        if found:
                            break
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
        # Player game logs: prioritize venue-matching games but include all for sample size
        venue_player_fixtures = [f for f in all_team_fixtures if f.get("venue") == player_venue]
        mixed_player_fixtures = venue_player_fixtures[:6] + [f for f in all_team_fixtures if f.get("venue") != player_venue][:4]
        player_game_logs_task = fetch_player_game_logs(mixed_player_fixtures[:10], req.playerId, 10)

        # Build a preliminary data blob for GPT to summarize while Wave 2 runs
        wave1_data = {
            "playerStats": player_stats,
            "teamStats": team_stats,
            "opponentStats": opponent_stats,
            "h2hData": h2h_data,
            "standings": standings,
            "recentFixtures": recent_fixtures,
            "opponentRecentFormations": opponent_formations,
        }
        raw_wave1_json = json.dumps(wave1_data, default=str)[:12000]

        async def gpt_summarize_data():
            """GPT-4.1-mini creates a compact data digest while Wave 2 runs"""
            try:
                summarizer = LlmChat(
                    api_key=EMERGENT_LLM_KEY,
                    session_id=f"sum-{uuid.uuid4().hex[:8]}",
                    system_message="You are a data processor. Extract and organize soccer statistics into a compact analytical brief. Numbers only, no fluff. Be extremely concise."
                )
                summarizer.with_model("openai", "gpt-4.1-mini")
                result = await summarizer.send_message(UserMessage(text=f"""Summarize this raw API data for a {req.propType} prop prediction on {req.playerName} (line {req.line}) vs {req.opponentName} ({req.venue}).

CRITICAL VENUE CONTEXT: Player is playing {player_venue.upper()}. All analysis must be venue-weighted.
- For the player's team: Focus on their {player_venue.upper()} performance
- For the opponent: Focus on their {opponent_venue.upper()} performance (how they play when {opponent_venue})

Extract ONLY what matters:
1. PLAYER: Position, per-90 rates SPLIT BY HOME vs AWAY, appearances, avg minutes
2. LAST 5 {player_venue.upper()} GAMES: {req.propType} values with opponent and minutes (prioritize venue-matching games)
3. HOME/AWAY SPLITS: Separate averages — highlight the {player_venue.upper()} average prominently
4. TEAM {player_venue.upper()} PROFILE: Possession%, goals for/against at {player_venue}
5. OPPONENT {opponent_venue.upper()} PROFILE: How they perform when {opponent_venue} — defensive stats, goals conceded, possession allowed
6. H2H: Any head-to-head data
7. STANDINGS: League positions
8. ODDS: Bookmaker odds if present

Bullet list format. Exact numbers only.

DATA:
{raw_wave1_json}"""))
                return result
            except Exception:
                return None

        # =============================================
        # TRIPLE AI: Claude Sonnet runs tactical analysis in parallel
        # =============================================
        async def claude_tactical_analysis():
            """Claude analyzes matchup, scenarios, PPDA, sub risk, game flow — the strategic brain"""
            try:
                tactical_ai = LlmChat(
                    api_key=EMERGENT_LLM_KEY,
                    session_id=f"tac-{uuid.uuid4().hex[:8]}",
                    system_message="You are an elite soccer tactical analyst. You analyze matchups, game scripts, and player props with mathematical precision. Always quote numbers. Be concise but thorough."
                )
                tactical_ai.with_model("anthropic", "claude-sonnet-4-5-20250929")

                # Build a focused tactical brief for Claude
                tactical_brief = {
                    "opponent_formations": opponent_formations,
                    "opponent_stats": opponent_stats,
                    "team_stats": team_stats,
                    "h2h": h2h_data[:3] if h2h_data else [],
                    "standings": standings[:6] if standings else [],
                    "prematch": prematch_pred,
                    "injuries": injuries_data[:10] if injuries_data else [],
                }
                tactical_json = json.dumps(tactical_brief, default=str)[:6000]

                result = await tactical_ai.send_message(UserMessage(text=f"""Analyze this matchup for a {req.propType} prop on {req.playerName} ({player_position or 'Unknown'}) playing for team vs {req.opponentName} ({req.venue}).
Line: {req.line}

CRITICAL: Player is {player_venue.upper()}. Opponent is {opponent_venue.upper()}.
ALL analysis must be venue-specific. Use {player_venue} splits for the player's team and {opponent_venue} splits for the opponent.

Do this analysis:

1. PPDA ESTIMATE: From the opponent's {opponent_venue.upper()} tackles/interceptions data, estimate their pressing intensity when {opponent_venue}.
   - Calculate: (opponent total passes) / (opponent tackles + interceptions + fouls)
   - Classify: Aggressive (6-9), Standard (10-12), Passive (13+)
   - Impact on {req.propType}: How does this pressing level shift the stat?

2. SUB RISK QUANTIFICATION: From the player's recent minutes data:
   - What % of {player_venue.upper()} games was the player subbed before 75'?
   - What's the average stat volume lost per early sub?
   - Weighted drag on projection?

3. GAME FLOW PREDICTION:
   - Who is favored? (use odds if available, else standings)
   - Expected possession split AT {player_venue.upper()} for this team
   - {player_venue.upper()} teams typically get 3-5% possession boost — factor this in
   - When the favored team scores first, what happens to {req.propType}?

4. SCENARIO ANALYSIS with probabilities:
   - Base case (most likely): probability % and expected {req.propType} value at {player_venue}
   - Blowout (team dominates at {player_venue}): probability % and value
   - Trailing (team falls behind at {player_venue}): probability % and value
   - Cagey/tight: probability % and value
   - Weighted projection = sum of (probability x value) for each scenario

5. SENSITIVITY TESTS:
   - If subbed at 60': pick survives or fails?
   - If team down 2-0 early: survives or fails?
   - If opponent parks bus: survives or fails?
   - If red card at 30': survives or fails?
   - Rating: ROBUST (3-4 pass) / MODERATE (2 pass) / FRAGILE (0-1 pass)

6. FINAL VERDICT: Over or under {req.line}? Confidence 0-100? Key risk?

DATA:
{tactical_json}"""))
                return result
            except Exception:
                return None

        gpt_summary_task = gpt_summarize_data()
        claude_tactical_task = claude_tactical_analysis()

        try:
            team_fixture_stats, opponent_fixture_stats, player_game_logs, gpt_data_summary, claude_analysis = await aio.wait_for(
                aio.gather(team_fixture_stats_task, opponent_fixture_stats_task, player_game_logs_task, gpt_summary_task, claude_tactical_task),
                timeout=30
            )
        except aio.TimeoutError:
            team_fixture_stats, opponent_fixture_stats, player_game_logs, gpt_data_summary, claude_analysis = [], [], [], None, None

        historical_data = {
            "playerStats": player_stats,
            "teamStats": team_stats,
            "opponentStats": opponent_stats,
            "h2hData": h2h_data,
            "standings": standings,
            "recentFixtures": recent_fixtures,
            "opponentRecentFormations": opponent_formations,
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
            for h in h2h_data[:3]:  # Max 3 H2H matches to avoid rate limits
                fid = h.get("fixture", {}).get("id")
                if fid:
                    h2h_fixture_ids.append((fid, h))

            async def fetch_h2h_player_stat(fid, fixture_info):
                """Fetch the target player's stats from a specific H2H fixture"""
                try:
                    pstats = await api_football_request("fixtures/players", {"fixture": fid})
                    if not pstats:
                        return None
                    # Find our player in the fixture stats
                    for team_data in pstats:
                        for p in team_data.get("players", []):
                            if p.get("player", {}).get("id") == req.playerId:
                                stats = p.get("statistics", [{}])[0] if p.get("statistics") else {}
                                minutes_played = stats.get("games", {}).get("minutes") or 0
                                # Extract the relevant stat
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
                                home_name = fixture_info.get("teams", {}).get("home", {}).get("name", "")
                                away_name = fixture_info.get("teams", {}).get("away", {}).get("name", "")
                                home_goals = fixture_info.get("goals", {}).get("home", 0)
                                away_goals = fixture_info.get("goals", {}).get("away", 0)
                                return {
                                    "date": fixture_info.get("fixture", {}).get("date", ""),
                                    "opponent": away_name if team_data.get("team", {}).get("id") == actual_team_id else home_name,
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
                for fid, fi in h2h_fixture_ids:
                    result = await fetch_h2h_player_stat(fid, fi)
                    if result:
                        h2h_player_stats.append(result)

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

        # Format injuries for prompt
        injuries_summary = []
        if injuries_data:
            for inj in injuries_data:
                injuries_summary.append({
                    "player": inj.get("player", {}).get("name", ""),
                    "team": inj.get("team", {}).get("name", ""),
                    "type": inj.get("player", {}).get("type", ""),
                    "reason": inj.get("player", {}).get("reason", ""),
                })
            historical_data["injuries"] = injuries_summary

        # Only include prematch data if it matches the requested opponent
        prematch_for_prompt = None
        if prematch_pred:
            # Check if the next fixture is actually against the requested opponent
            pred_str = json.dumps(prematch_pred, default=str).lower()
            opp_lower = req.opponentName.lower().split()[0]  # First word of opponent name
            if opp_lower in pred_str:
                prematch_for_prompt = prematch_pred
                historical_data["prematchPrediction"] = prematch_pred

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

        # 2. Send to Gemini — FINAL PREDICTOR (receives pre-analyzed intel from GPT + Claude)
        chat = LlmChat(
            api_key=EMERGENT_LLM_KEY,
            session_id=f"predict-{uuid.uuid4().hex[:8]}",
            system_message="""You are the final-stage predictor in a triple-AI pipeline. You receive:
1. A DATA SUMMARY from GPT (compact stats brief)
2. A TACTICAL ANALYSIS from Claude (matchup analysis, PPDA, scenarios, sensitivity tests, sub risk)
3. DEEP MATCH DATA (per-fixture stats and player game logs)

Your job: SYNTHESIZE all three inputs into a single, calibrated prediction JSON. Do NOT re-derive what Claude already analyzed — USE their analysis directly. Focus on:

STEP 1: VALIDATE the player's role from the data summary
STEP 2: CROSS-REFERENCE Claude's scenario analysis with the game log data — does the evidence support Claude's assessment?
STEP 3: CALIBRATE the final projected value using:
- Claude's weighted scenario projection as the starting point
- Game log data as the ground truth (per-90 rates, home/away splits)
- GPT's per-90 rates and recent form numbers
- If Claude and the data disagree, explain why and choose the more evidence-based number
STEP 4: SET CONFIDENCE based on:
- Data quality (sample size, recency)
- Claude's sensitivity test results (ROBUST = 75+, MODERATE = 55-74, FRAGILE = 40-54)
- How close the projection is to the line (within 0.5 = coin flip = max 55 confidence)

DRIBBLE-SPECIFIC: Most volatile stat. AWAY + low-block = default UNDER when line is close to average.

Return ONLY valid JSON (no markdown).

JSON structure:
{"player":{"id":int,"name":"","team":"","role":"","position":""},"opponent":"","league":"","propType":"","line":0,"projectedValue":0,"recommendation":"over|under","confidenceScore":0-100,"confidenceLevel":"Low|Medium|High|Very High","confidenceInterval":[lo,hi],"recentSamples":[{"date":"","opponent":"","value":0,"minutesPlayed":0,"matchDifficulty":"low|medium|high","venue":"home|away"}],"bayesianMetrics":{"priorMean":0,"momentumEffect":0,"covariateAdjustment":0,"reversalFlag":"stable|upward_reversal_likely|downward_reversal_likely"},"probabilityCurve":[{"value":0,"probability":0}],"tacticalAlerts":[{"type":"injury|lineup|tactical|sub_risk","message":"","severity":"low|medium|high"}],"sharpSummary":"","reasoning":"","scenarioAnalysis":"","keyEvidence":"","uncertaintyNote":"","sensitivityTests":"","subRisk":"","gameFlowDynamics":""}

Field requirements:
- sharpSummary: 2-3 sentences. Core edge. Be direct.
- reasoning: 2-3 paragraphs synthesizing all three AI inputs. Quote numbers from GPT's summary AND Claude's analysis. Cross-reference with game log data.
- scenarioAnalysis: Take Claude's scenario breakdown directly — refine if game log data contradicts it.
- keyEvidence: Quote 5-8 specific values from the data. Format: "Last 5: X (vs Team, venue), Y (vs Team, venue)..."
- sensitivityTests: Take Claude's sensitivity results directly.
- subRisk: Take Claude's sub risk quantification directly.
- gameFlowDynamics: Take Claude's game flow analysis directly.
- uncertaintyNote: Key risk factor + data limitations.
- recentSamples: 15+ from game logs with venue. If game logs unavailable, generate from season stats.
- probabilityCurve: 10 data points centered on projection."""
        )
        chat.with_model("gemini", "gemini-2.5-flash")

        # Build the data payload — use GPT summary as primary + Wave 2 deep data as supplement
        wave2_supplement = {}
        if player_game_logs:
            target_field_map = {
                "pass_attempts": "passes_total", "shots": "shots_total", "shots_on_target": "shots_on",
                "tackles": "tackles_total", "key_passes": "passes_key", "interceptions": "tackles_interceptions",
                "blocks": "tackles_blocks", "dribbles": "dribbles_attempts", "fouls_drawn": "fouls_drawn",
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

        # Compose final data: GPT summary (compact) + Claude tactical analysis + Wave 2 deep data
        final_data_parts = []
        if gpt_data_summary:
            final_data_parts.append(f"[GPT DATA SUMMARY]\n{gpt_data_summary}")
        if claude_analysis:
            final_data_parts.append(f"[CLAUDE TACTICAL ANALYSIS]\n{claude_analysis}")
        if wave2_supplement:
            final_data_parts.append(f"[DEEP MATCH DATA]\n{json.dumps(wave2_supplement, default=str)[:5000]}")

        if final_data_parts:
            final_data = "\n\n".join(final_data_parts)
        else:
            # Fallback: raw data if both AIs failed
            final_data = json.dumps(historical_data, default=str)[:18000]

        prompt = f"""TRIPLE AI PREDICTION — FINAL STAGE

Player: {req.playerName} (ID: {req.playerId}) | Position: {player_position or 'Unknown'} | Opponent: {req.opponentName} | Venue: {req.venue.upper()} | Prop: {req.propType} | Line: {req.line}

CRITICAL VENUE CONTEXT: Player is {player_venue.upper()}. ALL data below is venue-filtered:
- Team stats = their {player_venue.upper()} performance
- Opponent stats = their {opponent_venue.upper()} performance
- Player game logs = prioritized {player_venue.upper()} games
- The {player_venue.upper()} average is MORE predictive than the overall average. Weight it 60-70%.

Stat mapping: pass_attempts=passes.total, shots=shots.total, shots_on_target=shots.on, tackles=tackles.total, key_passes=passes.key, saves=goals.saves, interceptions=tackles.interceptions, blocks=tackles.blocks, dribbles=dribbles.attempts, fouls_drawn=fouls.drawn

You have received pre-analyzed intel from two AI systems:
1. GPT's DATA SUMMARY — compact statistical brief with per-90 rates, recent form, H2H, standings
2. Claude's TACTICAL ANALYSIS — PPDA estimate, sub risk quantification, scenario analysis with probabilities, sensitivity tests, game flow prediction

YOUR JOB: Synthesize both into the final calibrated prediction JSON.
- Use Claude's scenario weights as your starting framework
- Cross-check against GPT's data numbers
- If they disagree, explain why in your reasoning and go with the more evidence-based number
- Pull recentSamples from the game log data
- Take Claude's sensitivityTests, subRisk, and gameFlowDynamics assessments — refine only if game log data contradicts them

TACTICAL INTEL:
- Opponent formations: {json.dumps(opponent_formations, default=str) if opponent_formations else 'See Claude analysis'}
- Injuries: {json.dumps(injuries_summary, default=str) if injuries_summary else 'See Claude analysis'}

CRITICAL ODDS RULE: If bookmaker odds present, lower odds = favored team. Trust bookmaker odds over all other signals.

ALL PRE-ANALYZED DATA:
{final_data}

Return ONLY valid JSON. Synthesize all inputs. 15+ recentSamples with venue. 10pt probabilityCurve."""

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

        # Attach match stat data for frontend heat maps/visualizations
        if team_fixture_stats:
            prediction["teamMatchStats"] = team_fixture_stats
        if opponent_fixture_stats:
            prediction["opponentMatchStats"] = opponent_fixture_stats
        if historical_data.get("h2hPlayerStats"):
            prediction["h2hPlayerStats"] = historical_data["h2hPlayerStats"]

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
