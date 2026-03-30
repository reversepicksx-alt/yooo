"""
Persistent lookup cache for API-Football data.
Fetches national teams, league teams on startup, stores in MongoDB.
Refreshes every 7 days.
"""
import asyncio as aio
import time
import re
from datetime import datetime, timezone
from config import db, SUPPORTED_LEAGUES, CURRENT_SEASON
from utils import api_football_request

CACHE_COLLECTION = "api_cache"
CACHE_TTL_SECONDS = 7 * 24 * 3600  # 7 days


async def _get_cache(key: str):
    doc = await db[CACHE_COLLECTION].find_one({"_key": key}, {"_id": 0})
    if doc and (time.time() - doc.get("_ts", 0)) < CACHE_TTL_SECONDS:
        return doc.get("data")
    return None


async def _set_cache(key: str, data):
    await db[CACHE_COLLECTION].update_one(
        {"_key": key},
        {"$set": {"_key": key, "data": data, "_ts": time.time(),
                  "_updated": datetime.now(timezone.utc).isoformat()}},
        upsert=True
    )


def _is_senior_national(name: str) -> bool:
    """Filter out youth, women's, and B teams."""
    lower = name.lower()
    skip_patterns = [
        r'\bu\d{2}\b', r'\bu-\d{2}\b',  # U17, U-19, U20, U21, U23
        r'\bw$', r'\bwomen\b',  # Women's teams
        r'\b[b-c]$',  # B/C teams
        r'\bolympic\b', r'\bfutsal\b', r'\bbeach\b',
    ]
    for pat in skip_patterns:
        if re.search(pat, lower):
            return False
    return True


async def fetch_national_teams() -> dict:
    """Fetch all senior national team IDs from international leagues. Returns {lowercase_name: {id, name}}"""
    cached = await _get_cache("national_teams")
    if cached:
        return cached

    international_leagues = [5, 32, 34, 31, 29, 30, 33, 4, 960, 9, 6, 115, 7, 10, 1, 13, 11, 12, 15, 8]
    raw_teams = {}

    for lg in international_leagues:
        for season in [CURRENT_SEASON, CURRENT_SEASON - 1, CURRENT_SEASON - 2]:
            try:
                data = await api_football_request("teams", {"league": lg, "season": season})
                if data:
                    for t in data:
                        tid = t["team"]["id"]
                        name = t["team"]["name"]
                        if _is_senior_national(name) and tid not in raw_teams:
                            raw_teams[tid] = {"id": tid, "name": name}
                    break
            except Exception:
                continue

    # Build lookup: multiple name variants -> team info
    lookup = {}
    ALIASES = {
        "czech republic": ["czechia"],
        "usa": ["united states", "united states of america"],
        "rep. of ireland": ["ireland", "republic of ireland", "eire"],
        "fyr macedonia": ["north macedonia", "macedonia"],
        "bosnia & herzegovina": ["bosnia", "bosnia and herzegovina"],
        "ivory coast": ["cote d'ivoire", "cote divoire"],
        "south korea": ["korea republic", "korea"],
        "turkiye": ["turkey"],
        "türkiye": ["turkey", "turkiye"],
        "congo dr": ["dr congo", "democratic republic of congo"],
        "holland": ["netherlands"],
    }

    for tid, info in raw_teams.items():
        name = info["name"]
        name_lower = name.lower()
        lookup[name_lower] = info
        # Add aliases
        for canonical, aliases in ALIASES.items():
            if name_lower == canonical:
                for alias in aliases:
                    lookup[alias] = info
            for alias in aliases:
                if name_lower == alias:
                    lookup[canonical] = info

    await _set_cache("national_teams", lookup)
    return lookup


async def fetch_league_teams(league_id: int) -> dict:
    """Fetch all teams for a league. Returns {lowercase_name: {id, name}}"""
    cache_key = f"league_teams_{league_id}"
    cached = await _get_cache(cache_key)
    if cached:
        return cached

    lookup = {}
    for season in [CURRENT_SEASON, CURRENT_SEASON - 1]:
        try:
            data = await api_football_request("teams", {"league": league_id, "season": season})
            if data:
                for t in data:
                    tid = t["team"]["id"]
                    name = t["team"]["name"]
                    lookup[name.lower()] = {"id": tid, "name": name}
                break
        except Exception:
            continue

    if lookup:
        await _set_cache(cache_key, lookup)
    return lookup


async def get_national_team_id(country_name: str) -> tuple:
    """Look up a national team by name. Returns (team_id, canonical_name) or (None, None)."""
    lookup = await fetch_national_teams()
    info = lookup.get(country_name.lower().strip())
    if info:
        return info["id"], info["name"]
    return None, None


async def get_team_id_in_league(team_name: str, league_id: int) -> tuple:
    """Look up a team in a specific league. Returns (team_id, canonical_name) or (None, None)."""
    lookup = await fetch_league_teams(league_id)
    info = lookup.get(team_name.lower().strip())
    if info:
        return info["id"], info["name"]
    # Fuzzy: check if team_name is a substring of any cached name
    team_lower = team_name.lower().strip()
    for cached_name, info in lookup.items():
        if team_lower in cached_name or cached_name in team_lower:
            return info["id"], info["name"]
    return None, None


async def seed_cache():
    """Seed the lookup cache on startup. Non-blocking, runs in background."""
    try:
        existing = await _get_cache("national_teams")
        if existing:
            print(f"[CACHE] National teams cache loaded ({len(existing)} entries)")
            return

        print("[CACHE] Seeding national teams cache from API-Football...")
        lookup = await fetch_national_teams()
        print(f"[CACHE] Cached {len(lookup)} national team entries")

        # Also cache teams for top leagues
        for league in SUPPORTED_LEAGUES[:12]:
            lid = league["id"]
            teams = await fetch_league_teams(lid)
            if teams:
                print(f"[CACHE] Cached {len(teams)} teams for {league['name']}")
            await aio.sleep(0.3)  # rate limit respect

    except Exception as e:
        print(f"[CACHE] Seed error (non-fatal): {e}")
