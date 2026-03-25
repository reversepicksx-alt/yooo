import os
import json
import httpx
import uuid
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException
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

mongo_client = AsyncIOMotorClient(MONGO_URL)
db = mongo_client[DB_NAME]

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


async def get_recent_match_history(player_id: int, team_id: int, count: int = 10):
    try:
        fixtures = await api_football_request("fixtures", {"team": team_id, "last": count})
        results = []
        for f in fixtures[:count]:
            fid = f["fixture"]["id"]
            ps = await fetch_match_player_stats(fid, player_id)
            if ps:
                results.append({
                    "fixture": f["fixture"],
                    "league": f.get("league"),
                    "teams": f.get("teams"),
                    "goals": f.get("goals"),
                    "playerStats": ps
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

        match_history = await get_recent_match_history(req.playerId, actual_team_id, 10)

        league_id = req.leagueId
        if not league_id and player_stats:
            stats_list = player_stats.get("statistics", [])
            if stats_list:
                league_id = stats_list[0].get("league", {}).get("id", 39)

        team_stats = None
        opponent_stats = None
        h2h_data = []
        standings = []
        team_fixtures = []
        try:
            team_stats = await api_football_request("teams/statistics", {"team": actual_team_id, "league": league_id, "season": CURRENT_SEASON})
        except Exception:
            pass
        try:
            opponent_stats = await api_football_request("teams/statistics", {"team": req.opponentId, "league": league_id, "season": CURRENT_SEASON})
        except Exception:
            pass
        try:
            h2h_data = await api_football_request("fixtures/headtohead", {"h2h": f"{actual_team_id}-{req.opponentId}", "last": 5})
        except Exception:
            pass
        try:
            standings_raw = await api_football_request("standings", {"league": league_id, "season": CURRENT_SEASON})
            if standings_raw:
                standings = standings_raw[0].get("league", {}).get("standings", [[]])[0]
        except Exception:
            pass
        try:
            team_fixtures = await api_football_request("fixtures", {"team": actual_team_id, "last": 20})
        except Exception:
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
            system_message="""You are an elite soccer data analyst and prop betting expert. You MUST ONLY use the provided API data for your analysis. You produce structured JSON predictions with deep tactical reasoning.

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
  "explanation": string,
  "recentSamples": [{ "date": string, "opponent": string, "value": number, "minutesPlayed": number, "matchDifficulty": "low"|"medium"|"high" }],
  "tacticalAnalysis": { "pressingStyle": string, "possessionImpact": string, "spaceAndTime": string },
  "bayesianMetrics": { "priorMean": number, "momentumEffect": number, "covariateAdjustment": number, "reversalFlag": "stable"|"upward_reversal_likely"|"downward_reversal_likely" },
  "probabilityCurve": [{ "value": number, "probability": number }],
  "tacticalAlerts": [{ "type": "injury"|"lineup"|"tactical", "message": string, "severity": "low"|"medium"|"high" }],
  "tacticalInsights": string,
  "reasoning": string
}"""
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
{json.dumps(historical_data, default=str)[:15000]}

CRITICAL: 
1. Use ONLY the provided data. Extract actual stat values from match history for recentSamples.
2. For propType '{req.propType}': map to the relevant stat in the data (pass_attempts=passes.total, shots=shots.total, saves=goals.saves, clearances=tackles.blocks, tackles=tackles.total)
3. Generate a probability curve with 10-15 data points
4. Provide deep tactical analysis
5. Return ONLY valid JSON, no markdown or extra text"""

        response = await chat.send_message(UserMessage(text=prompt))
        response_text = response.strip()

        # Clean up response - remove markdown code fences if present
        if response_text.startswith("```"):
            lines = response_text.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            response_text = "\n".join(lines)

        prediction = json.loads(response_text)

        # Save to MongoDB
        prediction["_created"] = datetime.now(timezone.utc).isoformat()
        prediction["_request"] = req.model_dump()
        await db.predictions.insert_one(prediction)
        prediction.pop("_id", None)

        return prediction

    except json.JSONDecodeError as e:
        raise HTTPException(status_code=500, detail=f"AI returned invalid JSON: {str(e)}")
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
Extract: playerName, opponentName, venue (home/away), propType (pass_attempts/shots/saves/clearances/tackles), line (number).
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
