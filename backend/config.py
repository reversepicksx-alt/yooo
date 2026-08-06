import os
import asyncio as aio
import time
from datetime import datetime, timezone
import pathlib as _pathlib
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import ConfigurationError

# Load .env as fallback — only for keys not already set in the environment.
# This prevents the committed backend/.env (localhost MONGO_URL) from
# overriding the production Atlas URL injected by the deployment platform.
_ENV_FILE = _pathlib.Path(__file__).parent / ".env"
load_dotenv(dotenv_path=_ENV_FILE, override=False)

# ── Environment Variables ──
# MONGO_URL: after load_dotenv(override=False), os.environ holds the .env
# fallback value ONLY when the production secret wasn't set. If the secret
# is set, load_dotenv leaves it untouched → production Atlas URL is used.
MONGO_URL = os.environ.get("MONGO_URL", "")
DB_NAME = os.environ.get("DB_NAME")
API_FOOTBALL_KEY = os.environ.get("API_FOOTBALL_KEY") or os.environ.get("API_SPORTS_KEY")
API_FOOTBALL_BASE = "https://v3.football.api-sports.io"
API_FOOTBALL_DOCS = "https://www.api-football.com/documentation-v3"
API_FOOTBALL_PLAYER_IDS = "https://dashboard.api-football.com/soccer/ids/players"
API_FOOTBALL_TEAM_IDS = "https://dashboard.api-football.com/soccer/ids/teams"
API_FOOTBALL_LEAGUE_IDS = "https://dashboard.api-football.com/soccer/ids"
OWNER_EMAIL = (os.environ.get("OWNER_EMAIL") or "reversepicksx@gmail.com").lower().strip()
# All emails that should receive owner-level access (only the product owner)
OWNER_EMAILS = {OWNER_EMAIL}
# ── Dynamic settings (overridable via admin panel, persisted in MongoDB) ──
_dynamic_settings = {}

DYNAMIC_KEYS = [
    "API_FOOTBALL_KEY",
]

# Env fallbacks for each key
_ENV_DEFAULTS = {
    "API_FOOTBALL_KEY": API_FOOTBALL_KEY,
}

async def init_dynamic_settings():
    """Load settings overrides from MongoDB on startup. Fault-tolerant: falls back to env defaults if DB is unreachable."""
    for key in DYNAMIC_KEYS:
        _dynamic_settings[key] = _ENV_DEFAULTS.get(key, "")
    try:
        for key in DYNAMIC_KEYS:
            doc = await db.settings.find_one({"key": key}, {"_id": 0})
            if doc and doc.get("value"):
                _dynamic_settings[key] = doc["value"]
    except Exception as e:
        import logging
        logging.getLogger("uvicorn.error").warning(
            f"[CONFIG] MongoDB unreachable during startup — using env defaults. ({type(e).__name__}: {e})"
        )

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

# ── Beta / TestFlight Testers ──
# Comma-separated emails in env var BETA_TEST_EMAILS get free access (access_type="Beta")
# Use this for TestFlight testers only — revoke by removing their email from the env var.
_beta_raw = os.environ.get("BETA_TEST_EMAILS", "")
BETA_TEST_EMAILS: set = {e.strip().lower() for e in _beta_raw.split(",") if e.strip()}

# ── Lifetime VIP Emails ──
LIFETIME_SUB_EMAILS = [
    "faron2allen@gmail.com", "jossel0701@gmail.com", "josselj001@gmail.com",
    "brayanfgaleas@icloud.com", "odr310@gmail.com",
    "joseharo197@gmail.com", "rijulgauchan1@gmail.com", "gordo0210@icloud.com",
    "brianavina23@gmail.com", "andrewfitz97@yahoo.com",
    "jesselopezj@hotmail.com",
    "michael1069_6910@yahoo.com",
    "cristiang5815@gmail.com",
    "its2famous@gmail.com",
    "mendezvincent17@gmail.com",
    "817dusty@gmail.com",
    "banks.kendre@yahoo.com",
    "willmenjivar123@gmail.com",
    "adriano.velasquez10@gmail.com",
    "kevvduran2006@icloud.com",
    "roshensenha24@gmail.com",
    "alicia.thibadeau@gmail.com",
    "esol123@live.com",
    "aldk.provided381@8shield.net",
    "letwins04@gmail.com",
    "elitevinbali@gmail.com",
    "joelsem98@gmail.com",
    "jacobsierra7117@gmail.com",
    "onlylockzz0@gmail.com",
    "felix_rdz_@outlook.com",
]
LIFETIME_SUB_EMAILS = [e.lower() for e in LIFETIME_SUB_EMAILS]

# ── Complimentary Access (email → ISO expiry date, auto-expires) ──
COMPLIMENTARY_MEMBERS = {
    "xaviersteverson@gmail.com":       "2026-07-13",  # 3 months comp
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
    {"id": 94, "name": "Primeira Liga", "type": "Domestic"},
    {"id": 203, "name": "Süper Lig", "type": "Domestic"},
    {"id": 40, "name": "Championship", "type": "Domestic"},
    {"id": 188, "name": "A-League", "type": "Domestic"},
    {"id": 253, "name": "MLS", "type": "Domestic"},
    {"id": 262, "name": "Liga MX", "type": "Domestic"},
    {"id": 128, "name": "Liga Profesional Argentina", "type": "Domestic"},
    {"id": 71, "name": "Brasileirao", "type": "Domestic"},
    {"id": 242, "name": "Liga Pro Ecuador", "type": "Domestic"},
    {"id": 307, "name": "Saudi Pro League", "type": "Domestic"},
    {"id": 254, "name": "NWSL", "type": "Domestic"},
    {"id": 667, "name": "Singapore Premier League", "type": "Domestic"},
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
# NWSL is a calendar-year competition. Keep this explicit instead of changing
# CURRENT_SEASON, which is also used by European and other soccer leagues.
NWSL_LEAGUE_ID = 254
NWSL_SEASON = 2026
WOMENS_LEAGUE_IDS = {254}
TOP_5_LEAGUES = [39, 140, 135, 78, 61]

# ── Position prompt version — increment when the resolution prompt/logic changes
# to force re-resolution of any cached positions on next predict call ──
POSITION_PROMPT_VERSION = 8

# ── API-Football protection ──
# Keep background traffic opt-in. User-triggered searches/predictions and
# active-pick polling use the shared client, while bulk jobs stay disabled
# unless explicitly enabled in the environment.
api_semaphore = aio.Semaphore(4)
API_BULK_PREFETCH_ENABLED = os.environ.get("ENABLE_API_BULK_PREFETCH", "").lower() in {"1", "true", "yes"}
API_DAILY_SOFT_LIMIT = int(os.environ.get("API_DAILY_SOFT_LIMIT", "700"))

# ── Chat sessions (in-memory) ──
chat_sessions: dict = {}

# ── Database URL resolution ──
# Motor/PyMongo handles mongodb+srv:// SRV resolution internally — no manual
# DNS pre-check needed.  The pre-check was causing autoscale deployments to
# silently fall back to localhost when Atlas DNS was slow/blocked at import time.
_local_url = "mongodb://localhost:27017"
_EFFECTIVE_MONGO_URL: str = MONGO_URL if MONGO_URL else _local_url
import sys as _sys
print(f"[CONFIG] MongoDB target: {'Atlas' if 'mongodb+srv' in _EFFECTIVE_MONGO_URL else 'localhost'}", file=_sys.stderr, flush=True)
_DB_NAME = DB_NAME or "reversepicks"
# serverSelectionTimeoutMS=10000 → give Atlas replica-set elections room to
# settle before fast-failing, while still not blocking the 15s client timeout.
# 3s was too aggressive during transient Atlas primary stepdowns.
try:
    mongo_client = AsyncIOMotorClient(
        _EFFECTIVE_MONGO_URL,
        serverSelectionTimeoutMS=10000,
        retryWrites=True,
    )
except ConfigurationError as exc:
    # PyMongo resolves mongodb+srv records while constructing the client. A
    # transient DNS/SRV failure must not prevent the API process from starting
    # at all: search and provider-backed routes can still serve while Mongo is
    # unavailable, and the workflow starts a local Mongo fallback for cache
    # reads. Production keeps using Atlas whenever its SRV record resolves.
    if "mongodb+srv" not in _EFFECTIVE_MONGO_URL:
        raise
    print(
        f"[CONFIG] Atlas SRV unavailable ({exc}); falling back to local MongoDB",
        file=_sys.stderr,
        flush=True,
    )
    mongo_client = AsyncIOMotorClient(
        _local_url,
        serverSelectionTimeoutMS=3000,
        retryWrites=False,
    )
db = mongo_client[_DB_NAME]

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

# ── National-team strength tiers (fallback when no domestic/qualifying-group
# standings table exists for the opponent — e.g. friendlies, intercontinental
# playoffs, or when the historical game log spans multiple confederations
# whose group tables don't include every opponent). Curated, approximate
# FIFA-ranking-band buckets. Only listed teams get a tier this way; unlisted
# teams simply fall through to "no dot" exactly as before, so this only ADDS
# coverage — it never overrides a real standings-based rank.
NATIONAL_TEAM_TIER = {
    # ELITE (roughly top ~12)
    "argentina": "ELITE", "france": "ELITE", "spain": "ELITE", "england": "ELITE",
    "brazil": "ELITE", "portugal": "ELITE", "netherlands": "ELITE", "belgium": "ELITE",
    "germany": "ELITE", "italy": "ELITE", "croatia": "ELITE", "uruguay": "ELITE",
    "colombia": "ELITE",
    # STRONG (roughly ~13-35)
    "morocco": "STRONG", "usa": "STRONG", "united states": "STRONG", "mexico": "STRONG",
    "switzerland": "STRONG", "japan": "STRONG", "denmark": "STRONG", "senegal": "STRONG",
    "ecuador": "STRONG", "peru": "STRONG", "south korea": "STRONG", "iran": "STRONG",
    "wales": "STRONG", "poland": "STRONG", "serbia": "STRONG", "ukraine": "STRONG",
    "austria": "STRONG", "sweden": "STRONG", "tunisia": "STRONG", "ghana": "STRONG",
    "canada": "STRONG", "australia": "STRONG", "norway": "STRONG", "egypt": "STRONG",
    # MID (roughly ~36-70)
    "costa rica": "MID", "panama": "MID", "jordan": "MID", "uzbekistan": "MID",
    "paraguay": "MID", "bolivia": "MID", "venezuela": "MID", "chile": "MID",
    "jamaica": "MID", "qatar": "MID", "saudi arabia": "MID", "iraq": "MID",
    "uae": "MID", "united arab emirates": "MID", "algeria": "MID", "nigeria": "MID",
    "cameroon": "MID", "south africa": "MID", "ivory coast": "MID", "turkey": "MID",
    "greece": "MID", "scotland": "MID", "finland": "MID", "slovakia": "MID",
    "romania": "MID", "czech republic": "MID", "czechia": "MID", "bosnia": "MID",
    "bosnia & herzegovina": "MID", "iceland": "MID", "hungary": "MID", "china": "MID",
    "new zealand": "MID", "honduras": "MID", "el salvador": "MID", "curacao": "MID",
    "haiti": "MID", "guatemala": "MID",
    # WEAK (long tail — small/lower-ranked footballing nations)
    "cape verde": "WEAK", "gabon": "WEAK", "mali": "WEAK", "burkina faso": "WEAK",
    "dr congo": "WEAK", "congo dr": "WEAK", "guinea": "WEAK", "benin": "WEAK",
    "vietnam": "WEAK", "india": "WEAK", "thailand": "WEAK", "philippines": "WEAK",
    "hong kong": "WEAK", "indonesia": "WEAK", "kyrgyzstan": "WEAK", "tajikistan": "WEAK",
    "turkmenistan": "WEAK", "bahrain": "WEAK", "kuwait": "WEAK", "oman": "WEAK",
    "yemen": "WEAK", "syria": "WEAK", "palestine": "WEAK", "bermuda": "WEAK",
    "grenada": "WEAK", "guyana": "WEAK", "nicaragua": "WEAK", "cuba": "WEAK",
}

# ── Stat field maps (used in multiple places) ──
STAT_FIELD_MAP = {
    "goals": "goals_total",
    "assists": "goals_assists",
    "shots_assisted": "passes_key",
    "pass_attempts": "passes_total",
    "passes": "passes_total",
    "shots": "shots_total",
    "shots_on_target": "shots_on",
    "tackles": "tackles_total",
    "key_passes": "passes_key",
    "saves": "goals_saves",
    "goalie_saves": "goals_saves",
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
    "crosses": lambda s: s.get("passes", {}).get("crosses"),
    "clearances": lambda s: s.get("tackles", {}).get("clearances"),
    "duels_won": lambda s: s.get("duels", {}).get("won"),
    "yellow_cards": lambda s: s.get("cards", {}).get("yellow"),
}
