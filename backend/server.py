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

        # 2. Send to Gemini — elite chain-of-thought reasoning with tactical depth
        chat = LlmChat(
            api_key=EMERGENT_LLM_KEY,
            session_id=f"predict-{uuid.uuid4().hex[:8]}",
            system_message="""You are an elite soccer prop analyst who thinks rigorously, transparently, and step-by-step. Return ONLY valid JSON (no markdown).

REASONING METHOD — You MUST think through every prediction using this chain-of-thought:

STEP 1: IDENTIFY THE PLAYER'S ROLE (this is the #1 predictor of stat volume)
- Deep-lying playmaker (CDM/CM holding): Pass attempts 80-110+ per 90. They recycle possession constantly.
- Box-to-box midfielder: Moderate-high passes (50-80), moderate tackles/interceptions
- Attacking midfielder / #10: High key passes, moderate total passes, low tackles
- Centre-back: High passes in own half, low key passes, high blocks/interceptions
- Goalkeeper: Saves only, extremely low passes
- Winger: High dribbles, shots, low pass attempts
- Striker: High shots, low passes
Quote the player's ACTUAL position from API data. If the data says "Midfielder", infer the specific sub-role from their stat profile (high passes + low shots = holding; high key passes + moderate shots = attacking).

STEP 2: EXTRACT KEY EVIDENCE (quote specific numbers — never make vague claims)
- List the player's last 5-10 game values for this specific stat, with venue and opponent
- Calculate: season average, home average, away average, last-5 rolling average
- Identify FLOOR games (sub, injury return, blowout sub-off) and FLAG them
- Note the standard deviation — how volatile is this stat for this player?

STEP 2B: PER-90 MINUTE NORMALIZATION (CRITICAL — use this, not raw totals)
- If "per90Analysis" is present in the data, USE IT as the primary reference for the player's stat rates
- Per-90 = (raw total / total minutes) × 90. This normalizes for games where the player was subbed early
- A player with 25 passes in 65 minutes is actually a 34.6 per-90 player — very different from 25 in 90 minutes (25.0 per-90)
- Always compare per-90 rates, not raw per-game averages, when assessing volume
- Quote both: "Season per-90: 34.6 (raw per-game: 28.3 due to 73 avg minutes)"
- Use avgMinutesPerGame to flag minute risk: if avg < 80, there's consistent sub risk dragging raw numbers down

STEP 2C: HEAD-TO-HEAD OPPONENT HISTORY (the sharpest edge)
- If "h2hPlayerStats" is present in the data, THIS IS GOLD — the player's actual stats in previous meetings against this SPECIFIC opponent
- Quote exact values: "In 4 meetings vs [opponent]: 34, 28, 41, 31 passes. Avg vs this opponent: 33.5"
- Compare H2H average to season average: If H2H avg is significantly higher/lower than season avg, explain WHY (opponent style, venue, game script)
- H2H sample size matters: 4+ games = strong signal. 1-2 games = weak signal, use cautiously
- If H2H data shows a clear trend against this opponent, it should HEAVILY influence the projection (weight 30-40% of final number)

STEP 3: SUBSTITUTION RISK QUANTIFICATION
- Check the player's minutes played in recent games. How often are they subbed before 70'? Before 80'?
- Calculate the % of games where early substitution occurred and the average stat volume lost per early sub
- Example: "Subbed before 70' in 3 of last 15 games (20%). In those 3, averaged only 18 passes vs 31 in full games. That's ~13 attempts lost per early sub."
- Factor this into the projection as a weighted drag: projection = (full_game_projection × %_full_games) + (sub_projection × %_sub_games)
- If the team is heavy favorites (odds < 1.50), sub risk INCREASES because blowout subs are more likely

STEP 4: FIRST-TO-SCORE POSSESSION DYNAMICS
- Analyze from recent fixtures: When the player's team scores first, do they sit back and protect (fewer passes) or keep pressing (more passes)?
- When trailing, does the team become more direct (bypassing midfield = fewer midfielder passes, more striker shots) or keep patient buildup?
- Cross-reference with opponent tendencies: Some teams sit deep after conceding (giving the other team MORE possession), while others chase the game
- Expected game flow from odds: If the player's team is favored, the base case is they score first → apply the leading-team possession adjustment

STEP 5: PRESSING INTENSITY (PPDA APPROXIMATION)
- Estimate the opponent's Passes Per Defensive Action (PPDA) from their tackles, interceptions, and fouls data
- Low PPDA (6-9): Aggressive pressing team — they win the ball back quickly, giving LESS time on the ball to opposing players → FEWER passes, MORE turnovers, but MORE tackles/interceptions for both sides
- Medium PPDA (10-12): Standard pressing — neutral effect
- High PPDA (13+): Passive/low-block team — they let the opposition have the ball → MORE passes, MORE possession, but FEWER tackles opportunities
- This is the single best metric for predicting pass volume against a specific opponent. A CDM facing a PPDA-7 team will have 15-20% fewer passes than against a PPDA-15 team.

STEP 6: ANALYZE THE MATCHUP
- Opponent defensive style: Low-block (sit deep) → more possession/passes for dominant team. High-press → turnovers, fewer passes, more tackles.
- Opponent possession conceded: If they give up 55%+, the opposing midfielders get inflated pass numbers
- Formation matchup: 5-back = ultra-defensive = massive pass boost. 4-3-3 press = open game = fewer passes, more dribble space.
- Injuries/suspensions: If a key teammate is out, does the workload shift to this player? If an opponent's presser is out, does the pressure drop?

STEP 7: SCENARIO ANALYSIS (consider ALL plausible game flows)
- Base case (most likely): Team dominates as expected → player gets normal minutes, normal rhythm
- Blowout scenario: If team goes up big early → player might be subbed off at 60-70' → LOWER volume
- Trailing scenario: If team falls behind → more urgent attacking → different stat distribution
- Cagey/tight game: Low possession for both sides → compressed stat ceilings
- Early red card (either team): Completely changes game dynamics
- Assign rough probability weights to each scenario and factor into the projection

STEP 8: VENUE + GAME SCRIPT ADJUSTMENT
- Home vs away splits for this specific stat (home players typically get 10-20% more of most stats)
- Expected game script from bookmaker odds (favorite team = more possession = more stats for their midfielders)
- If bookmaker odds show a heavy favorite, the favorite's players get stat boosts from expected possession dominance

STEP 9: SENSITIVITY TESTS (what breaks the pick?)
- Answer these explicitly:
  * "If the player is subbed at 60', does the pick still hit?" → recalculate with 67% of projected volume
  * "If the team goes down 2-0 early, does the pick still hit?" → apply trailing-team adjustments
  * "If the opponent parks the bus from minute 1, does the pick still hit?" → apply max-possession boost but also min-shot/dribble suppression
  * "If a red card happens at 30', does the pick still hit?" → 10-man dynamics
- A ROBUST pick survives 3 of 4 sensitivity tests. A FRAGILE pick only works in the base case. Flag fragile picks explicitly.

STEP 10: FINAL CALIBRATION + UNCERTAINTY
- Compare your projected value to the line. How many standard deviations away is the line from the mean?
- If data is limited (< 5 games this season), explicitly reduce confidence
- If the line is within 0.5 of the player's average, acknowledge this is a COIN FLIP zone
- State what would need to happen for your prediction to be WRONG (the key risk factor)
- A pick that passes all sensitivity tests and has strong evidence deserves 75+ confidence. A pick that only works in the base case should be 40-55 max.

DRIBBLE-SPECIFIC RULES:
- Most volatile stat (SD typically 40-50% of mean)
- AWAY: Drop 15-30%. Low-block opponents: Drop 30-50%. High-line opponents: Increase.
- When line is close to average (within 0.5): Default UNDER for away + organized defenses
- Wide players (LW/RW) > central players for attempts

STAT-SPECIFIC NOTES:
- Shots/SOT: Check opponent's shots conceded per game
- Tackles/interceptions: Higher when team is OUT of possession more
- Key passes: Higher in open games, lower in cagey games
- Saves: Check opponent's shots per game
- Fouls drawn: Physical opponents = more, disciplined = fewer

JSON structure:
{"player":{"id":int,"name":"","team":"","role":"","position":""},"opponent":"","league":"","propType":"","line":0,"projectedValue":0,"recommendation":"over|under","confidenceScore":0-100,"confidenceLevel":"Low|Medium|High|Very High","confidenceInterval":[lo,hi],"recentSamples":[{"date":"","opponent":"","value":0,"minutesPlayed":0,"matchDifficulty":"low|medium|high","venue":"home|away"}],"bayesianMetrics":{"priorMean":0,"momentumEffect":0,"covariateAdjustment":0,"reversalFlag":"stable|upward_reversal_likely|downward_reversal_likely"},"probabilityCurve":[{"value":0,"probability":0}],"tacticalAlerts":[{"type":"injury|lineup|tactical|sub_risk","message":"","severity":"low|medium|high"}],"sharpSummary":"","reasoning":"","scenarioAnalysis":"","keyEvidence":"","uncertaintyNote":"","sensitivityTests":"","subRisk":"","gameFlowDynamics":""}

Field requirements:
- role: MUST be specific (e.g., "Deep-Lying Playmaker", "CDM Holding", "Box-to-Box CM", "Inside Forward", "Target Striker", "Sweeper Keeper")
- sharpSummary: 2-3 sharp sentences. The CORE edge — what makes this over or under the line. Be direct.
- reasoning: 3-4 rich paragraphs following the chain-of-thought: (1) ROLE identification with per-90 context (2) KEY EVIDENCE — quote the exact stat values from recent games, calculate averages (3) MATCHUP ANALYSIS — opponent style, formation, possession expectations, PPDA approximation (4) SCENARIO WEIGHTING — base case + alt scenarios, venue adjustment, final calibration
- scenarioAnalysis: 1 paragraph covering base case (60%), blowout (15%), trailing (15%), cagey (10%) — adjust percentages based on matchup. Explain how each scenario affects the specific stat.
- keyEvidence: Quote 5-8 specific stat values with dates/opponents. Example: "Last 5: 87 (vs Arsenal, H), 74 (vs Wolves, A), 91 (vs Brighton, H), 82 (vs Fulham, A), 78 (vs Palace, H). Home avg: 85.3, Away avg: 78.0. SD: 6.2"
- uncertaintyNote: 1-2 sentences on what could go wrong. Example: "Small sample (4 games this season). If subbed before 75', projection drops to 55."
- sensitivityTests: Answer 3-4 "what if" scenarios: "Sub at 60': projection drops to X (pick SURVIVES/FAILS). Down 2-0: projection shifts to Y (SURVIVES/FAILS). Opponent parks bus: Z (SURVIVES/FAILS). Red card: W (SURVIVES/FAILS)." Then state: "Pick survives X/4 tests = ROBUST/MODERATE/FRAGILE"
- subRisk: 1-2 sentences quantifying sub probability and impact. Example: "Subbed before 70' in 20% of games. Average attempts lost per early sub: 13. Weighted drag on projection: -2.6 attempts."
- gameFlowDynamics: 1-2 sentences on first-to-score impact. Example: "When leading, team averages 58% possession (+3% vs trailing). Player's passes increase by ~5 per 90 when team leads. Favored to score first (odds 1.60), boosting base case."
- recentSamples: 15-20 entries with venue tags, sorted date descending
- probabilityCurve: 10 data points
- covariateAdjustment: Factor in role + opponent PPDA + injury impact + sub risk drag (positive = stat boost, negative = stat suppression)
- tacticalAlerts: Include injury alerts, sub risk warnings, and pressing intensity notes"""
        )
        chat.with_model("gemini", "gemini-2.5-flash")

        trimmed_data = json.dumps(historical_data, default=str)[:15000]

        prompt = f"""Player: {req.playerName} (ID: {req.playerId}) | Position from API: {player_position or 'Unknown'} | Opponent: {req.opponentName} | Venue: {req.venue} | Prop: {req.propType} | Line: {req.line}

Stat mapping: pass_attempts=passes.total, shots=shots.total, shots_on_target=shots.on, tackles=tackles.total, key_passes=passes.key, saves=goals.saves, interceptions=tackles.interceptions, blocks=tackles.blocks, dribbles=dribbles.attempts, fouls_drawn=fouls.drawn

CRITICAL: The player's official position from API-Sports is "{player_position or 'Unknown'}". Use THIS as the base for role analysis. Do NOT contradict it.

THINK STEP-BY-STEP (follow all 10 steps from your instructions):
1. What is this player's specific role and how does it affect {req.propType}?
2. What are the exact stat values from recent games? (quote numbers with opponents and venues)
2B. PER-90 CHECK: What is the player's per-90 rate for {req.propType}? How does it differ from raw per-game? What's their avg minutes per game?
2C. H2H CHECK: If h2hPlayerStats data is present, what are the player's EXACT stat values against {req.opponentName} in previous meetings? How does the H2H average compare to the season average?
3. SUBSTITUTION RISK: How often is this player subbed early? What's the stat volume impact per early sub? Quantify the drag.
4. FIRST-TO-SCORE: When the team leads, does possession/stat volume go up or down? When trailing? What does the expected game flow look like based on odds?
5. PRESSING INTENSITY: Estimate the opponent's PPDA from their tackles/interceptions. Are they aggressive pressers (PPDA 6-9) or passive (PPDA 13+)? How does that shift {req.propType}?
6. What is the opponent's defensive style and formation matchup?
7. What are the plausible game scenarios (base, blowout, trailing, cagey) and how does each affect the stat?
8. What is the venue adjustment and expected game script from odds?
9. SENSITIVITY TESTS: Does this pick survive: (a) sub at 60'? (b) team down 2-0? (c) opponent parks bus? (d) red card? How many of 4 tests does it pass?
10. Final calibration: where does the projection land vs the line of {req.line}? Is this robust or fragile?

TACTICAL INTEL:
- Opponent recent formations: {json.dumps(opponent_formations, default=str) if opponent_formations else 'Not available'}
- Pre-match data (odds + prediction): {json.dumps(prematch_for_prompt, default=str) if prematch_for_prompt else 'Not available for this specific matchup'}
- Injuries/Suspensions: {json.dumps(injuries_summary, default=str) if injuries_summary else 'None reported'}

CRITICAL ODDS RULE: If "bookmakerOdds" is present, ALWAYS use it to determine the favorite. Lower odds = favored team. If the API prediction contradicts bookmaker odds, TRUST THE BOOKMAKER ODDS.

FORMATION ANALYSIS:
- 5-back (5-3-2, 5-4-1): ULTRA DEFENSIVE → massive pass boost for dominant team's midfielders, dribble suppression
- 4-4-2 compact: Mid-block → moderate pass boost, strong dribble suppression
- 4-3-3 / 4-2-3-1 high press: Open game → fewer passes (turnovers), more dribble opportunities
- Coach style matters: Defensive coaches = compact = more recycling passes for opponent CDMs

INJURY IMPACT RULES:
- If a key midfielder is OUT for the player's team → remaining midfielders get MORE passes/touches
- If an opponent's key presser is OUT → less pressure on the ball → more comfortable passing
- If the player himself has a minor knock → possible early sub → LOWER stat ceiling
- Always mention relevant injuries in tacticalAlerts

Data:
{trimmed_data}

Return ONLY valid JSON. Follow ALL 10 steps. Quote specific evidence. Include sensitivityTests, subRisk, and gameFlowDynamics fields. 15+ recentSamples with venue. 10pt probabilityCurve."""

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
