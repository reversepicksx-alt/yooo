"""
Basketball (NBA + WNBA) data cache.
Mirrors the soccer cache pattern: stores all leagues, teams, and players in MongoDB.
Auto-syncs on startup and refreshes every 24 hours.
"""
import asyncio as aio
import re
import time
from datetime import datetime, timezone
from config import db
from basketball_utils import _api_get, get_current_nba_season

# ── Collections ──
COL_BBALL_LEAGUES = "bball_cache_leagues"
COL_BBALL_TEAMS = "bball_cache_teams"
COL_BBALL_PLAYERS = "bball_cache_players"
COL_BBALL_META = "bball_cache_meta"

# NBA = league 12, WNBA (NBA W) = league 13
BBALL_LEAGUES = [
    {"id": 12, "name": "NBA", "season_format": "cross"},     # "2025-2026"
    {"id": 13, "name": "WNBA", "season_format": "single"},   # "2025"
]

CACHE_TTL_SECONDS = 3 * 24 * 3600  # 3 days


def _strip_accents(s: str) -> str:
    import unicodedata
    nfkd = unicodedata.normalize('NFKD', s)
    return ''.join(c for c in nfkd if not unicodedata.combining(c))


# ══════════════════════════════════════════════
#  META HELPERS
# ══════════════════════════════════════════════

async def _get_meta(key: str):
    return await db[COL_BBALL_META].find_one({"_key": key}, {"_id": 0})


async def _set_meta(key: str, data: dict):
    await db[COL_BBALL_META].update_one(
        {"_key": key},
        {"$set": {**data, "_key": key, "_ts": time.time(),
                  "_updated": datetime.now(timezone.utc).isoformat()}},
        upsert=True
    )


def _get_seasons_to_try(league: dict) -> list:
    """Get the season strings to try for a league."""
    now = datetime.utcnow()
    if league["season_format"] == "cross":
        # NBA: "2025-2026" style
        current = get_current_nba_season()
        year = int(current.split("-")[0])
        return [current, f"{year-1}-{year}"]
    else:
        # WNBA: "2025" style, season is May-Oct
        return [str(now.year), str(now.year - 1)]


# ══════════════════════════════════════════════
#  1. LEAGUES
# ══════════════════════════════════════════════

async def sync_bball_leagues():
    """Store NBA and WNBA league info."""
    docs = []
    for league in BBALL_LEAGUES:
        data = await _api_get("leagues", {"id": league["id"]})
        if data:
            for item in data:
                lg = item if isinstance(item, dict) and "id" in item else item
                doc = {
                    "leagueId": league["id"],
                    "name": league["name"],
                    "apiName": lg.get("name", league["name"]) if isinstance(lg, dict) else league["name"],
                    "type": "League",
                    "country": "USA",
                    "seasonFormat": league["season_format"],
                    "nameLower": league["name"].lower(),
                }
                docs.append(doc)
        await aio.sleep(0.3)

    if docs:
        await db[COL_BBALL_LEAGUES].delete_many({})
        await db[COL_BBALL_LEAGUES].insert_many(docs)
        await db[COL_BBALL_LEAGUES].create_index("leagueId", unique=True)

    await _set_meta("bball_leagues_sync", {"count": len(docs)})
    return len(docs)


# ══════════════════════════════════════════════
#  2. TEAMS (NBA + WNBA)
# ══════════════════════════════════════════════

async def sync_bball_teams_for_league(league: dict) -> int:
    """Fetch all teams for an NBA/WNBA league and store."""
    seasons = _get_seasons_to_try(league)

    for season in seasons:
        data = await _api_get("teams", {
            "league": league["id"],
            "season": season,
        })
        if data:
            docs = []
            for item in data:
                name = item.get("name", "")
                # Build clean name: strip " W" suffix for WNBA matching
                clean_name = name
                if clean_name.endswith(" W"):
                    clean_name = clean_name[:-2]

                doc = {
                    "teamId": item.get("id"),
                    "name": name,
                    "nameLower": name.lower(),
                    "nameClean": _strip_accents(name.lower()),
                    "nameShort": clean_name,
                    "nameShortLower": clean_name.lower(),
                    "logo": item.get("logo", ""),
                    "country": item.get("country", {}).get("name", "USA") if isinstance(item.get("country"), dict) else "USA",
                    "leagueId": league["id"],
                    "leagueName": league["name"],
                    "season": season,
                }
                docs.append(doc)

            if docs:
                await db[COL_BBALL_TEAMS].delete_many({"leagueId": league["id"]})
                await db[COL_BBALL_TEAMS].insert_many(docs)
            return len(docs)

        await aio.sleep(0.3)
    return 0


async def sync_all_bball_teams():
    """Sync teams for NBA and WNBA."""
    await db[COL_BBALL_TEAMS].create_index("teamId")
    await db[COL_BBALL_TEAMS].create_index("leagueId")
    await db[COL_BBALL_TEAMS].create_index("nameLower")
    await db[COL_BBALL_TEAMS].create_index("nameShortLower")

    total = 0
    for league in BBALL_LEAGUES:
        count = await sync_bball_teams_for_league(league)
        if count:
            total += count
            print(f"[BBALL CACHE] Teams: {league['name']} -> {count} teams")
        await aio.sleep(0.5)

    await _set_meta("bball_teams_sync", {"count": total})
    return total


# ══════════════════════════════════════════════
#  3. PLAYERS (per team)
# ══════════════════════════════════════════════

async def sync_bball_squad(team_id: int, team_name: str, league_id: int, season: str):
    """Fetch all players for a team and store. Tries current season, then previous."""
    seasons_to_try = [season]
    # For WNBA or any league, also try previous season if current yields no results
    if season.isdigit():
        seasons_to_try.append(str(int(season) - 1))
    elif "-" in season:
        year = int(season.split("-")[0])
        seasons_to_try.append(f"{year-1}-{year}")

    for s in seasons_to_try:
        data = await _api_get("players", {
            "team": team_id,
            "season": s,
        })
        if not data:
            await aio.sleep(0.3)
            continue

        docs = []
        for p in data:
            name = p.get("name", "")
            name_parts = name.split()
            name_reversed = " ".join(reversed(name_parts)) if len(name_parts) > 1 else name

            doc = {
                "playerId": p.get("id"),
                "name": name,
                "nameLower": name.lower(),
                "nameClean": _strip_accents(name.lower()),
                "nameReversed": name_reversed.lower(),
                "position": p.get("position", ""),
                "number": p.get("number"),
                "age": p.get("age"),
                "country": p.get("country", ""),
                "teamId": team_id,
                "teamName": team_name,
                "leagueId": league_id,
                "season": s,
            }
            docs.append(doc)

        if docs:
            await db[COL_BBALL_PLAYERS].delete_many({"teamId": team_id})
            await db[COL_BBALL_PLAYERS].insert_many(docs)
            return docs

    return []


async def sync_all_bball_players():
    """Sync players for all NBA and WNBA teams."""
    await db[COL_BBALL_PLAYERS].create_index("playerId")
    await db[COL_BBALL_PLAYERS].create_index("teamId")
    await db[COL_BBALL_PLAYERS].create_index("nameLower")
    await db[COL_BBALL_PLAYERS].create_index("nameClean")
    await db[COL_BBALL_PLAYERS].create_index("nameReversed")
    await db[COL_BBALL_PLAYERS].create_index("leagueId")

    teams = []
    async for team in db[COL_BBALL_TEAMS].find({}, {"_id": 0}):
        teams.append(team)

    total = 0
    for i, team in enumerate(teams):
        squad = await sync_bball_squad(
            team["teamId"], team.get("name", ""),
            team.get("leagueId", 0), team.get("season", "")
        )
        total += len(squad)
        if (i + 1) % 10 == 0:
            print(f"[BBALL CACHE] Squads: {i+1}/{len(teams)} teams ({total} players)")
        await aio.sleep(0.5)

    await _set_meta("bball_players_sync", {"count": total, "teams": len(teams)})
    print(f"[BBALL CACHE] Players: {total} from {len(teams)} teams")
    return total


# ══════════════════════════════════════════════
#  LOOKUP FUNCTIONS
# ══════════════════════════════════════════════

# NBA team abbreviation map (covers all 30 NBA teams + common WNBA abbreviations)
NBA_ABBREV_MAP = {
    "atl": "atlanta hawks", "bos": "boston celtics", "bkn": "brooklyn nets",
    "cha": "charlotte hornets", "chi": "chicago bulls", "cle": "cleveland cavaliers",
    "dal": "dallas mavericks", "den": "denver nuggets", "det": "detroit pistons",
    "gsw": "golden state warriors", "gs": "golden state warriors",
    "hou": "houston rockets", "ind": "indiana pacers",
    "lac": "los angeles clippers", "lal": "los angeles lakers",
    "mem": "memphis grizzlies", "mia": "miami heat", "mil": "milwaukee bucks",
    "min": "minnesota timberwolves", "nop": "new orleans pelicans",
    "no": "new orleans pelicans", "nyk": "new york knicks", "ny": "new york knicks",
    "okc": "oklahoma city thunder", "orl": "orlando magic",
    "phi": "philadelphia 76ers", "phx": "phoenix suns",
    "por": "portland trail blazers", "sac": "sacramento kings",
    "sas": "san antonio spurs", "sa": "san antonio spurs",
    "tor": "toronto raptors", "uta": "utah jazz",
    "was": "washington wizards", "wsh": "washington wizards",
}


async def get_bball_team_by_name(team_name: str, league_id: int = None) -> dict:
    """
    Look up a basketball team by name. Returns {teamId, name, leagueId, leagueName, season} or None.
    Searches abbreviation map, exact, short name (without W suffix), then fuzzy substring.
    """
    name_lower = team_name.lower().strip()
    # Remove common suffixes for matching
    clean_name = name_lower
    for suffix in [" w", " women"]:
        if clean_name.endswith(suffix):
            clean_name = clean_name[:-len(suffix)]

    query_base = {}
    if league_id:
        query_base["leagueId"] = league_id

    # 0. Abbreviation map lookup (e.g. "LAC" → "los angeles clippers")
    if clean_name in NBA_ABBREV_MAP:
        full_name = NBA_ABBREV_MAP[clean_name]
        doc = await db[COL_BBALL_TEAMS].find_one({**query_base, "nameLower": full_name}, {"_id": 0})
        if doc:
            return doc

    # 1. Exact match on full name
    doc = await db[COL_BBALL_TEAMS].find_one({**query_base, "nameLower": name_lower}, {"_id": 0})
    if doc:
        return doc

    # 2. NBA-preferred substring match on full name (catches city names like "Portland" → "Portland Trail Blazers")
    if not league_id:
        doc = await db[COL_BBALL_TEAMS].find_one(
            {"leagueId": 12, "nameLower": {"$regex": re.escape(clean_name)}}, {"_id": 0}
        )
        if doc:
            return doc

    # 3. Exact/substring match on short name
    doc = await db[COL_BBALL_TEAMS].find_one({**query_base, "nameShortLower": clean_name}, {"_id": 0})
    if doc:
        return doc

    # 4. General substring match on full name
    doc = await db[COL_BBALL_TEAMS].find_one(
        {**query_base, "nameLower": {"$regex": re.escape(clean_name)}}, {"_id": 0}
    )
    if doc:
        return doc

    # 5. Substring match on short name
    doc = await db[COL_BBALL_TEAMS].find_one(
        {**query_base, "nameShortLower": {"$regex": re.escape(clean_name)}}, {"_id": 0}
    )
    if doc:
        return doc

    return None


async def get_bball_player_by_name(player_name: str, team_id: int = None, league_id: int = None) -> dict:
    """
    Look up a basketball player by name.
    Returns player doc {playerId, name, teamId, teamName, leagueId, position, ...} or None.

    Search strategy:
    1. Exact nameClean match
    2. Reversed name match (API stores "LastName FirstName", user provides "FirstName LastName")
    3. Last name word boundary match
    4. First name + last name partial match
    """
    name_clean = _strip_accents(player_name.lower().strip())

    def _q(extra: dict) -> dict:
        q = dict(extra)
        if team_id:
            q["teamId"] = team_id
        if league_id and not team_id:
            q["leagueId"] = league_id
        return q

    # 1. Exact match
    doc = await db[COL_BBALL_PLAYERS].find_one(_q({"nameClean": name_clean}), {"_id": 0})
    if doc:
        return doc

    # 2. Reversed name (user says "Jalen Green", API stores "Green Jalen")
    parts = name_clean.split()
    if len(parts) >= 2:
        reversed_name = " ".join(reversed(parts))
        doc = await db[COL_BBALL_PLAYERS].find_one(_q({"nameClean": reversed_name}), {"_id": 0})
        if doc:
            return doc

    # 3. Last name match (word boundary)
    last_name = parts[-1] if parts else name_clean
    doc = await db[COL_BBALL_PLAYERS].find_one(
        _q({"nameClean": {"$regex": rf"\b{re.escape(last_name)}\b"}}), {"_id": 0}
    )
    if doc:
        return doc

    # 4. Check nameReversed field
    doc = await db[COL_BBALL_PLAYERS].find_one(
        _q({"nameReversed": {"$regex": rf"\b{re.escape(last_name)}\b"}}), {"_id": 0}
    )
    if doc:
        return doc

    # 5. Initial match (e.g., "C. Clark" → check for "Clark")
    if len(parts) >= 2 and len(parts[0]) <= 2:
        actual_last = parts[-1]
        doc = await db[COL_BBALL_PLAYERS].find_one(
            _q({"nameClean": {"$regex": rf"\b{re.escape(actual_last)}\b"}}), {"_id": 0}
        )
        if doc:
            return doc

    return None


async def search_bball_teams(query: str, league_id: int = None) -> list:
    """Search basketball teams by name. Returns list of team docs."""
    name_lower = query.lower().strip()
    clean = name_lower
    for suffix in [" w", " women"]:
        if clean.endswith(suffix):
            clean = clean[:-len(suffix)]

    # Check abbreviation map first
    search_term = NBA_ABBREV_MAP.get(clean, clean)

    q = {"$or": [
        {"nameLower": {"$regex": re.escape(search_term)}},
        {"nameShortLower": {"$regex": re.escape(search_term)}},
    ]}
    if league_id:
        q["leagueId"] = league_id

    results = []
    async for doc in db[COL_BBALL_TEAMS].find(q, {"_id": 0}).limit(10):
        results.append(doc)
    return results


# ══════════════════════════════════════════════
#  FULL SYNC
# ══════════════════════════════════════════════

async def bball_full_sync(force: bool = False):
    """Run full basketball data sync."""
    meta = await _get_meta("bball_full_sync")
    if not force and meta and (time.time() - meta.get("_ts", 0)) < CACHE_TTL_SECONDS:
        print(f"[BBALL CACHE] Data is fresh (synced {meta.get('_updated', '?')}). Skipping.")
        return

    print("[BBALL CACHE] Starting full basketball data sync...")
    start = time.time()

    # 1. Leagues
    league_count = await sync_bball_leagues()
    print(f"[BBALL CACHE] Leagues: {league_count}")

    # 2. Teams
    team_count = await sync_all_bball_teams()
    print(f"[BBALL CACHE] Teams: {team_count}")

    # 3. Players
    player_count = await sync_all_bball_players()

    elapsed = round(time.time() - start, 1)
    await _set_meta("bball_full_sync", {
        "leagues": league_count, "teams": team_count,
        "players": player_count, "elapsed_seconds": elapsed,
    })
    print(f"[BBALL CACHE] Full sync complete in {elapsed}s: {league_count} leagues, {team_count} teams, {player_count} players")


async def seed_bball_cache():
    """Non-blocking startup seed for basketball data."""
    try:
        players_count = await db[COL_BBALL_PLAYERS].count_documents({})
        if players_count > 500:
            teams_count = await db[COL_BBALL_TEAMS].count_documents({})
            print(f"[BBALL CACHE] Data loaded: {teams_count} teams, {players_count} players")
            return

        await bball_full_sync(force=True)
    except Exception as e:
        print(f"[BBALL CACHE] Seed error: {e}")


async def bball_background_refresh():
    """Runs every 24 hours to keep data fresh."""
    while True:
        await aio.sleep(24 * 3600)
        try:
            print("[BBALL CACHE] Running scheduled refresh...")
            await bball_full_sync(force=True)
        except Exception as e:
            print(f"[BBALL CACHE] Refresh error: {e}")


# ══════════════════════════════════════════════
#  CACHE STATUS
# ══════════════════════════════════════════════

async def get_bball_cache_status() -> dict:
    """Get overview of basketball cached data."""
    leagues = await db[COL_BBALL_LEAGUES].count_documents({})
    teams = await db[COL_BBALL_TEAMS].count_documents({})
    players = await db[COL_BBALL_PLAYERS].count_documents({})

    # Breakdown by league
    nba_teams = await db[COL_BBALL_TEAMS].count_documents({"leagueId": 12})
    wnba_teams = await db[COL_BBALL_TEAMS].count_documents({"leagueId": 13})
    nba_players = await db[COL_BBALL_PLAYERS].count_documents({"leagueId": 12})
    wnba_players = await db[COL_BBALL_PLAYERS].count_documents({"leagueId": 13})

    meta = {}
    async for doc in db[COL_BBALL_META].find({}, {"_id": 0}):
        meta[doc["_key"]] = doc.get("_updated", "never")

    return {
        "leagues": leagues,
        "teams": teams,
        "players": players,
        "nba": {"teams": nba_teams, "players": nba_players},
        "wnba": {"teams": wnba_teams, "players": wnba_players},
        "lastSync": meta,
    }
