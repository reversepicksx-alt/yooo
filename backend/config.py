import os
import asyncio as aio
import time
from datetime import datetime, timezone
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv()

# ── Environment Variables ──
MONGO_URL = os.environ.get("MONGO_URL")
DB_NAME = os.environ.get("DB_NAME")
EMERGENT_LLM_KEY = os.environ.get("EMERGENT_LLM_KEY")
API_FOOTBALL_KEY = os.environ.get("API_FOOTBALL_KEY")
API_FOOTBALL_BASE = "https://v3.football.api-sports.io"
WHOP_API_KEY = os.environ.get("WHOP_API_KEY")
WHOP_COMPANY_ID = os.environ.get("WHOP_COMPANY_ID")
OWNER_EMAIL = (os.environ.get("OWNER_EMAIL") or "josselj001@gmail.com").lower().strip()
XAI_API_KEY = os.environ.get("XAI_API_KEY")

# ── Lifetime VIP Emails ──
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

# ── Supported Leagues ──
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
WOMENS_LEAGUE_IDS = {254}
TOP_5_LEAGUES = [39, 140, 135, 78, 61]

# ── Rate limiter ──
api_semaphore = aio.Semaphore(5)

# ── Chat sessions (in-memory) ──
chat_sessions: dict = {}

# ── Whop cache ──
whop_cache = None
whop_cache_time = 0

# ── Database ──
mongo_client = AsyncIOMotorClient(MONGO_URL)
db = mongo_client[DB_NAME]

# ── Prop type aliases (for scan) ──
PROP_TYPE_ALIASES = {
    "pass attempts": "pass_attempts",
    "passes attempted": "pass_attempts",
    "passes": "pass_attempts",
    "pass att": "pass_attempts",
    "shots": "shots",
    "shots on target": "shots_on_target",
    "sot": "shots_on_target",
    "shots on goal": "shots_on_target",
    "tackles": "tackles",
    "key passes": "key_passes",
    "assists": "key_passes",
    "saves": "saves",
    "goalkeeper saves": "saves",
    "interceptions": "interceptions",
    "blocks": "blocks",
    "dribble attempts": "dribbles",
    "dribbles": "dribbles",
    "fouls drawn": "fouls_drawn",
    "fouls": "fouls_drawn",
}

# ── International league IDs (players indexed under club, not national team) ──
INTERNATIONAL_LEAGUES = {1, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 15, 29, 30, 31, 32, 33, 34, 115, 960}

# ── Nation → Club league mapping ──
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

# ── Stat field maps (used in multiple places) ──
STAT_FIELD_MAP = {
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

STAT_LAMBDA_MAP = {
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
