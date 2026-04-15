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
API_FOOTBALL_DOCS = "https://www.api-football.com/documentation-v3"
API_FOOTBALL_PLAYER_IDS = "https://dashboard.api-football.com/soccer/ids/players"
API_FOOTBALL_TEAM_IDS = "https://dashboard.api-football.com/soccer/ids/teams"
API_FOOTBALL_LEAGUE_IDS = "https://dashboard.api-football.com/soccer/ids"
OWNER_EMAIL = (os.environ.get("OWNER_EMAIL") or "reversepicksx@gmail.com").lower().strip()
XAI_API_KEY = os.environ.get("XAI_API_KEY")
SQUARE_ACCESS_TOKEN = os.environ.get("SQUARE_ACCESS_TOKEN")
SQUARE_APPLICATION_ID = os.environ.get("SQUARE_APPLICATION_ID")
SQUARE_LOCATION_ID = os.environ.get("SQUARE_LOCATION_ID")
SQUARE_ENVIRONMENT = os.environ.get("SQUARE_ENVIRONMENT", "sandbox")
WHOP_API_KEY = os.environ.get("WHOP_API_KEY")
WHOP_COMPANY_ID = os.environ.get("WHOP_COMPANY_ID")

# ── Dynamic settings (overridable via admin panel, persisted in MongoDB) ──
_dynamic_settings = {}

DYNAMIC_KEYS = [
    "API_FOOTBALL_KEY",
    "SQUARE_ACCESS_TOKEN",
    "SQUARE_APPLICATION_ID",
    "SQUARE_LOCATION_ID",
    "SQUARE_ENVIRONMENT",
    "DISABLE_SQUARE_BILLING",
]

# Env fallbacks for each key
_ENV_DEFAULTS = {
    "API_FOOTBALL_KEY": API_FOOTBALL_KEY,
    "SQUARE_ACCESS_TOKEN": SQUARE_ACCESS_TOKEN,
    "SQUARE_APPLICATION_ID": SQUARE_APPLICATION_ID,
    "SQUARE_LOCATION_ID": SQUARE_LOCATION_ID,
    "SQUARE_ENVIRONMENT": SQUARE_ENVIRONMENT,
    # Square billing is permanently disabled — default "true" means off unless DB explicitly says otherwise
    "DISABLE_SQUARE_BILLING": os.environ.get("DISABLE_SQUARE_BILLING", "true"),
}

async def init_dynamic_settings():
    """Load settings overrides from MongoDB on startup."""
    for key in DYNAMIC_KEYS:
        doc = await db.settings.find_one({"key": key}, {"_id": 0})
        if doc and doc.get("value"):
            _dynamic_settings[key] = doc["value"]
        else:
            _dynamic_settings[key] = _ENV_DEFAULTS.get(key, "")

def get_dynamic_setting(key: str) -> str:
    """Get a dynamic setting (DB override > env)."""
    return _dynamic_settings.get(key) or _ENV_DEFAULTS.get(key, "")

def get_dynamic_api_key():
    """Get the current API-Football key (DB override > env)."""
    return get_dynamic_setting("API_FOOTBALL_KEY")

async def set_dynamic_setting(key: str, value: str):
    """Update a dynamic setting in memory + MongoDB."""
    _dynamic_settings[key] = value
    await db.settings.update_one(
        {"key": key},
        {"$set": {"key": key, "value": value}},
        upsert=True
    )

async def set_dynamic_api_key(value: str):
    """Update API-Football key in memory + MongoDB."""
    await set_dynamic_setting("API_FOOTBALL_KEY", value)

# ── Lifetime VIP Emails ──
LIFETIME_SUB_EMAILS = [
    "faron2allen@gmail.com", "jossel0701@gmail.com", "josselj001@gmail.com",
    "brayanfgaleas@icloud.com", "odr310@gmail.com",
    "joseharo197@gmail.com", "rijulgauchan1@gmail.com", "gordo0210@icloud.com",
    "brianavina23@gmail.com", "andrewfitz97@yahoo.com",
    "jose108798@gmail.com", "letwins04@gmail.com",
    "quon.qg@gmail.com", "jesselopezj@hotmail.com",
    "jaredlee0414@gmail.com",
    "michael1069_6910@yahoo.com",
    "cristiang5815@gmail.com",
    "its2famous@gmail.com",
    "mendezvincent17@gmail.com",
    "817dusty@gmail.com",
    "ferrerfroy@gmail.com"
]
LIFETIME_SUB_EMAILS = [e.lower() for e in LIFETIME_SUB_EMAILS]

# ── Complimentary Access (email → ISO expiry date, auto-expires) ──
COMPLIMENTARY_MEMBERS = {
    "xaviersteverson@gmail.com":       "2026-07-13",  # 3 months comp
    # Square members — access through their paid charged_through_date
    "veinzice@gmail.com":              "2026-04-16",
    "rayhanekobeni@gmail.com":         "2026-05-01",
    "jeffreyabega@gmail.com":          "2026-04-16",
    "ryan086b@gmail.com":              "2026-05-01",
    "trillstunna0@gmail.com":          "2026-04-16",
    "luismartinez.lm878@gmail.com":    "2026-05-03",
    "babyscar100@icloud.com":          "2026-05-04",
    "luismendoxa27@gmail.com":         "2026-05-04",
    "alvarezraul285@gmail.com":        "2026-04-16",
    "jimmy.062910@gmail.com":          "2026-05-08",
    "thundafan0@gmail.com":            "2026-04-16",
    "mathieujulens@gmail.com":         "2026-07-09",
    "josequinteros8201@gmail.com":     "2026-04-17",
    "exoticveinz7985@gmail.com":       "2026-04-17",
}

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
    {"id": 242, "name": "Liga Pro Ecuador", "type": "Domestic"},
    {"id": 307, "name": "Saudi Pro League", "type": "Domestic"},
    {"id": 254, "name": "NWSL", "type": "Domestic"},
    {"id": 2, "name": "Champions League", "type": "International Club"},
    {"id": 3, "name": "Europa League", "type": "International Club"},
    {"id": 13, "name": "Copa Libertadores", "type": "International Club"},
    {"id": 11, "name": "Copa Sudamericana", "type": "International Club"},
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
api_semaphore = aio.Semaphore(10)

# ── Chat sessions (in-memory) ──
chat_sessions: dict = {}

# ── Database ──
mongo_client = AsyncIOMotorClient(MONGO_URL)
db = mongo_client[DB_NAME]

# ── Prop type aliases (for scan) ──
PROP_TYPE_ALIASES = {
    # Goals
    "goals": "goals",
    "goal": "goals",
    "goals scored": "goals",
    "anytime goalscorer": "goals",
    # Assists
    "assists": "assists",
    "assist": "assists",
    "goal assists": "assists",
    # Shots Assisted
    "shots assisted": "shots_assisted",
    "shot assists": "shots_assisted",
    "shot assist": "shots_assisted",
    # Pass attempts
    "pass attempts": "pass_attempts",
    "passes attempted": "pass_attempts",
    "passes": "pass_attempts",
    "pass att": "pass_attempts",
    "total passes": "pass_attempts",
    # Shots
    "shots": "shots",
    "total shots": "shots",
    "shot attempts": "shots",
    # Shots on target / SOT
    "shots on target": "shots_on_target",
    "sot": "shots_on_target",
    "shots on goal": "shots_on_target",
    # Tackles
    "tackles": "tackles",
    "total tackles": "tackles",
    # Key passes
    "key passes": "key_passes",
    "chances created": "key_passes",
    # Saves
    "saves": "saves",
    "goalkeeper saves": "saves",
    "goalie saves": "saves",
    "goalie_saves": "saves",
    "gk saves": "saves",
    # Interceptions
    "interceptions": "interceptions",
    # Blocks
    "blocks": "blocks",
    # Dribbles
    "dribble attempts": "dribbles",
    "dribbles": "dribbles",
    "dribbles attempted": "dribbles",
    # Successful dribbles
    "successful dribbles": "dribbles_success",
    "dribbles completed": "dribbles_success",
    # Fouls drawn
    "fouls drawn": "fouls_drawn",
    # Fouls committed
    "fouls committed": "fouls_committed",
    "fouls": "fouls_committed",
    # Crosses
    "crosses": "crosses",
    "crosses attempted": "crosses",
    "cross attempts": "crosses",
    # Clearances
    "clearances": "clearances",
    # Duels won
    "duels won": "duels_won",
    "duels": "duels_won",
    # Cards
    "yellow cards": "yellow_cards",
    "cards": "yellow_cards",
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
    "goals": "goals_total",
    "assists": "goals_assists",
    "shots_assisted": "passes_key",
    "pass_attempts": "passes_total",
    "shots": "shots_total",
    "shots_on_target": "shots_on",
    "tackles": "tackles_total",
    "key_passes": "passes_key",
    "saves": "goals_saves",
    "interceptions": "tackles_interceptions",
    "blocks": "tackles_blocks",
    "dribbles": "dribbles_attempts",
    "dribbles_success": "dribbles_success",
    "fouls_drawn": "fouls_drawn",
    "fouls_committed": "fouls_committed",
    "crosses": "passes_crosses",
    "clearances": "tackles_clearances",
    "duels_won": "duels_won",
    "yellow_cards": "cards_yellow",
}

STAT_LAMBDA_MAP = {
    "goals": lambda s: s.get("goals", {}).get("total"),
    "assists": lambda s: s.get("goals", {}).get("assists"),
    "shots_assisted": lambda s: s.get("passes", {}).get("key"),
    "pass_attempts": lambda s: s.get("passes", {}).get("total"),
    "shots": lambda s: s.get("shots", {}).get("total"),
    "shots_on_target": lambda s: s.get("shots", {}).get("on"),
    "tackles": lambda s: s.get("tackles", {}).get("total"),
    "key_passes": lambda s: s.get("passes", {}).get("key"),
    "saves": lambda s: s.get("goals", {}).get("saves"),
    "interceptions": lambda s: s.get("tackles", {}).get("interceptions"),
    "blocks": lambda s: s.get("tackles", {}).get("blocks"),
    "dribbles": lambda s: s.get("dribbles", {}).get("attempts"),
    "dribbles_success": lambda s: s.get("dribbles", {}).get("success"),
    "fouls_drawn": lambda s: s.get("fouls", {}).get("drawn"),
    "fouls_committed": lambda s: s.get("fouls", {}).get("committed"),
    "crosses": lambda s: (s.get("passes", {}).get("crosses") if s.get("passes", {}).get("crosses") is not None else s.get("passes", {}).get("total")),
    "clearances": lambda s: s.get("tackles", {}).get("clearances"),
    "duels_won": lambda s: s.get("duels", {}).get("won"),
    "yellow_cards": lambda s: s.get("cards", {}).get("yellow"),
}
