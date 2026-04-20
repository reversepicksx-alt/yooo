"""
Complete API-Football data cache.
Stores ALL leagues, teams, players, and national teams in MongoDB.
Auto-refreshes on a schedule to catch transfers, promotions/relegations, etc.
"""
import asyncio as aio
import html as html_module
import re
import time
from datetime import datetime, timezone
from config import db, SUPPORTED_LEAGUES, CURRENT_SEASON
from utils import api_football_request

CACHE_TTL_SECONDS    = 7 * 24 * 3600   # 7 days for general data
SQUAD_TTL_SECONDS    = 3 * 24 * 3600   # 3 days for squads (catches transfers faster)
STATS_TTL_SECONDS    = 7 * 24 * 3600   # 7 days for season stats
FIXTURE_TTL_SECONDS  = 24 * 3600       # 1 day for fixture history

# ── Collections ──
COL_LEAGUES         = "cache_leagues"       # All leagues from API
COL_TEAMS           = "cache_teams"         # All teams, indexed by league
COL_PLAYERS         = "cache_players"       # All players, indexed by team
COL_NATIONAL        = "cache_national"      # National team lookup
COL_TRANSFERS       = "cache_transfers"     # Detected transfers
COL_META            = "cache_meta"          # Timestamps, status
COL_PLAYER_STATS    = "player_season_stats" # Full season stats per player
COL_TEAM_STATS      = "team_season_stats"   # Team season stats per team×league
COL_TEAM_FIXTURES   = "team_fixture_history" # Recent fixture list per team


# ══════════════════════════════════════════════
#  LOW-LEVEL HELPERS
# ══════════════════════════════════════════════

async def _get_meta(key: str):
    doc = await db[COL_META].find_one({"_key": key}, {"_id": 0})
    return doc


async def _set_meta(key: str, data: dict):
    await db[COL_META].update_one(
        {"_key": key},
        {"$set": {**data, "_key": key, "_ts": time.time(),
                  "_updated": datetime.now(timezone.utc).isoformat()}},
        upsert=True
    )


def _is_senior_national(name: str) -> bool:
    lower = name.lower()
    skip = [r'\bu\d{2}\b', r'\bu-\d{2}\b', r'\bw$', r'\bwomen\b',
            r'\b[b-c]$', r'\bolympic\b', r'\bfutsal\b', r'\bbeach\b']
    return not any(re.search(p, lower) for p in skip)


# ══════════════════════════════════════════════
#  1. LEAGUES
# ══════════════════════════════════════════════

async def sync_leagues():
    """Fetch ALL leagues from API-Football and store in MongoDB."""
    try:
        data = await api_football_request("leagues")
        if not data:
            return 0

        ops = []
        for item in data:
            league = item.get("league", {})
            country = item.get("country", {})
            seasons = item.get("seasons", [])
            current_season = None
            for s in seasons:
                if s.get("current"):
                    current_season = s.get("year")
                    break

            doc = {
                "leagueId": league.get("id"),
                "name": league.get("name", ""),
                "type": league.get("type", ""),
                "logo": league.get("logo", ""),
                "country": country.get("name", ""),
                "countryCode": country.get("code", ""),
                "currentSeason": current_season,
                "nameLower": league.get("name", "").lower(),
            }
            ops.append(doc)

        if ops:
            await db[COL_LEAGUES].delete_many({})
            await db[COL_LEAGUES].insert_many(ops)
            await db[COL_LEAGUES].create_index("leagueId", unique=True)
            await db[COL_LEAGUES].create_index("nameLower")

        await _set_meta("leagues_sync", {"count": len(ops)})
        return len(ops)
    except Exception as e:
        print(f"[CACHE] Leagues sync error: {e}")
        return 0


# ══════════════════════════════════════════════
#  2. TEAMS (per league)
# ══════════════════════════════════════════════

async def sync_teams_for_league(league_id: int, season: int = None):
    """Fetch all teams for a specific league and store."""
    season = season or CURRENT_SEASON
    for s in [season, season - 1]:
        try:
            data = await api_football_request("teams", {"league": league_id, "season": s})
            if data:
                ops = []
                from utils import strip_accents
                for item in data:
                    team = item.get("team", {})
                    venue = item.get("venue", {})
                    tname = team.get("name", "")
                    doc = {
                        "teamId": team.get("id"),
                        "name": tname,
                        "nameLower": tname.lower(),
                        "nameClean": strip_accents(tname.lower()),
                        "code": team.get("code", ""),
                        "country": team.get("country", ""),
                        "national": team.get("national", False),
                        "logo": team.get("logo", ""),
                        "leagueId": league_id,
                        "season": s,
                        "venue": venue.get("name", ""),
                        "city": venue.get("city", ""),
                    }
                    ops.append(doc)

                if ops:
                    # Remove old entries for this league, insert fresh
                    await db[COL_TEAMS].delete_many({"leagueId": league_id})
                    await db[COL_TEAMS].insert_many(ops)
                return len(ops)
        except Exception:
            continue
    return 0


async def sync_all_teams():
    """Sync teams for all supported leagues."""
    await db[COL_TEAMS].create_index("teamId")
    await db[COL_TEAMS].create_index("leagueId")
    await db[COL_TEAMS].create_index("nameLower")
    await db[COL_TEAMS].create_index("nameClean")

    total = 0
    for league in SUPPORTED_LEAGUES:
        lid = league["id"]
        count = await sync_teams_for_league(lid)
        if count:
            total += count
            print(f"[CACHE] Teams: {league['name']} -> {count} teams")
        await aio.sleep(0.4)

    await _set_meta("teams_sync", {"count": total})
    return total


# ══════════════════════════════════════════════
#  3. PLAYERS (squad per team)
# ══════════════════════════════════════════════

_CONTINENTAL_LEAGUES = {2, 3, 11, 13, 848, 531, 480}  # UEFA CL/EL, Copa Lib/Sud, etc.

async def sync_squad(team_id: int, team_name: str = "", league_id: int = 0):
    """Fetch full squad for a team and store. Returns list of player docs.
    
    IMPORTANT: Domestic league entries are never overwritten by continental cup entries.
    This prevents Copa Libertadores (league 13) from overwriting Argentine Liga (128) data,
    which would cause player disambiguation bugs (e.g., Álvaro Montero at Vélez vs Newells).
    """
    from utils import strip_accents
    try:
        data = await api_football_request("players/squads", {"team": team_id})
        if not data or not data[0].get("players"):
            return []

        # If this sync is for a continental cup, check whether the team already has
        # a domestic league entry. If so, skip — domestic always wins.
        if league_id in _CONTINENTAL_LEAGUES:
            existing = await db[COL_PLAYERS].find_one({"teamId": team_id}, {"leagueId": 1})
            if existing and existing.get("leagueId") not in _CONTINENTAL_LEAGUES:
                # Team already has domestic players — don't overwrite with continental data
                return []

        players = data[0]["players"]
        ops = []
        for p in players:
            name = html_module.unescape(p.get("name", ""))
            doc = {
                "playerId": p.get("id"),
                "name": name,
                "nameLower": name.lower(),
                "nameClean": strip_accents(name.lower()),
                "age": p.get("age"),
                "number": p.get("number"),
                "position": p.get("position", ""),
                "photo": "",
                "teamId": team_id,
                "teamName": team_name,
                "leagueId": league_id,
            }
            ops.append(doc)

        if ops:
            # Remove old squad for this team, insert fresh
            await db[COL_PLAYERS].delete_many({"teamId": team_id})
            await db[COL_PLAYERS].insert_many(ops)

        return ops
    except Exception as e:
        print(f"[CACHE] Squad sync error for team {team_id}: {e}")
        return []


async def sync_all_squads():
    """Sync squads for all teams in the DB."""
    await db[COL_PLAYERS].create_index("playerId")
    await db[COL_PLAYERS].create_index("teamId")
    await db[COL_PLAYERS].create_index("nameLower")
    await db[COL_PLAYERS].create_index("nameClean")
    await db[COL_PLAYERS].create_index("leagueId")

    # Get all teams from cache
    teams = []
    async for team in db[COL_TEAMS].find({}, {"_id": 0, "teamId": 1, "name": 1, "leagueId": 1}):
        teams.append(team)

    total = 0
    for i, team in enumerate(teams):
        squad = await sync_squad(team["teamId"], team.get("name", ""), team.get("leagueId", 0))
        total += len(squad)
        if (i + 1) % 50 == 0:
            print(f"[CACHE] Squads: {i+1}/{len(teams)} teams processed ({total} players)")
        await aio.sleep(0.4)  # Rate limit

    await _set_meta("squads_sync", {"count": total, "teams": len(teams)})
    print(f"[CACHE] Squads: {total} players from {len(teams)} teams")
    return total


# ══════════════════════════════════════════════
#  4. NATIONAL TEAMS
# ══════════════════════════════════════════════

async def sync_national_teams():
    """Fetch national teams from international leagues and store with aliases."""
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
        await aio.sleep(0.3)

    # Build lookup with aliases
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

    docs = []
    seen_keys = set()
    for tid, info in raw_teams.items():
        name = info["name"]
        name_lower = name.lower()

        # Primary entry
        if name_lower not in seen_keys:
            docs.append({"key": name_lower, "teamId": tid, "name": name})
            seen_keys.add(name_lower)

        # Aliases
        for canonical, aliases in ALIASES.items():
            if name_lower == canonical or name_lower in aliases:
                all_names = [canonical] + aliases
                for alias in all_names:
                    if alias not in seen_keys:
                        docs.append({"key": alias, "teamId": tid, "name": name})
                        seen_keys.add(alias)

    if docs:
        await db[COL_NATIONAL].delete_many({})
        await db[COL_NATIONAL].insert_many(docs)
        await db[COL_NATIONAL].create_index("key", unique=True)
        await db[COL_NATIONAL].create_index("teamId")

    await _set_meta("national_sync", {"count": len(docs), "unique_teams": len(raw_teams)})
    print(f"[CACHE] National teams: {len(docs)} entries ({len(raw_teams)} unique teams)")
    return len(docs)


# ══════════════════════════════════════════════
#  5. TRANSFER DETECTION
# ══════════════════════════════════════════════

async def detect_transfers():
    """Compare current squads with cached data to detect transfers."""
    transfers_found = []

    # Get all teams
    teams = []
    async for team in db[COL_TEAMS].find({}, {"_id": 0, "teamId": 1, "name": 1, "leagueId": 1}):
        teams.append(team)

    for team in teams:
        tid = team["teamId"]
        team_name = team.get("name", "")

        # Get old squad from cache
        old_players = {}
        async for p in db[COL_PLAYERS].find({"teamId": tid}, {"_id": 0}):
            old_players[p["playerId"]] = p

        if not old_players:
            continue

        # Fetch fresh squad
        try:
            data = await api_football_request("players/squads", {"team": tid})
            if not data or not data[0].get("players"):
                continue

            new_squad = {p["id"]: p for p in data[0]["players"]}

            # Players who LEFT this team
            for pid, old_info in old_players.items():
                if pid not in new_squad:
                    transfers_found.append({
                        "playerId": pid,
                        "playerName": old_info.get("name", ""),
                        "fromTeamId": tid,
                        "fromTeam": team_name,
                        "type": "departure",
                        "detectedAt": datetime.now(timezone.utc).isoformat(),
                    })

            # Players who JOINED this team
            for pid, new_info in new_squad.items():
                if pid not in old_players:
                    transfers_found.append({
                        "playerId": pid,
                        "playerName": new_info.get("name", ""),
                        "toTeamId": tid,
                        "toTeam": team_name,
                        "type": "arrival",
                        "detectedAt": datetime.now(timezone.utc).isoformat(),
                    })

        except Exception:
            pass
        await aio.sleep(0.4)

    if transfers_found:
        await db[COL_TRANSFERS].insert_many(transfers_found)
        print(f"[CACHE] Transfers detected: {len(transfers_found)}")

    await _set_meta("transfer_scan", {"count": len(transfers_found)})
    return transfers_found


# ══════════════════════════════════════════════
#  LOOKUP FUNCTIONS (used by scan.py, predict.py, etc.)
# ══════════════════════════════════════════════

async def get_national_team_id(country_name: str) -> tuple:
    """Look up national team by name. Returns (team_id, canonical_name) or (None, None)."""
    doc = await db[COL_NATIONAL].find_one({"key": country_name.lower().strip()}, {"_id": 0})
    if doc:
        return doc["teamId"], doc["name"]
    return None, None


async def get_team_by_name(team_name: str, league_id: int = None) -> tuple:
    """Look up a club team by name. Returns (team_id, canonical_name) or (None, None).
    
    Search order:
    1. Exact nameLower match in cache_teams
    2. Substring regex in cache_teams
    3. Alias match in teams_master
    4. Fuzzy word-overlap match in cache_teams
    """
    from utils import strip_accents
    name_lower = strip_accents(team_name.lower().strip())

    # 1. Exact match on accent-stripped nameClean
    query = {"nameClean": name_lower}
    if league_id:
        query["leagueId"] = league_id
    doc = await db[COL_TEAMS].find_one(query, {"_id": 0})
    if doc:
        return doc["teamId"], doc["name"]

    # 1b. Exact match on nameLower (handles teams already stored without nameClean)
    query_lower = {"nameLower": name_lower}
    if league_id:
        query_lower["leagueId"] = league_id
    doc = await db[COL_TEAMS].find_one(query_lower, {"_id": 0})
    if doc:
        return doc["teamId"], doc["name"]

    # 2. Substring regex on nameClean
    fuzzy_query = {"nameClean": {"$regex": re.escape(name_lower)}}
    if league_id:
        fuzzy_query["leagueId"] = league_id
    async for doc in db[COL_TEAMS].find(fuzzy_query, {"_id": 0}).limit(1):
        return doc["teamId"], doc["name"]

    # Without league filter
    if league_id:
        async for doc in db[COL_TEAMS].find({"nameClean": {"$regex": re.escape(name_lower)}}, {"_id": 0}).limit(1):
            return doc["teamId"], doc["name"]

    # 2b. Also try nameLower regex (fallback for teams without nameClean)
    fuzzy_query_lower = {"nameLower": {"$regex": re.escape(name_lower)}}
    if league_id:
        fuzzy_query_lower["leagueId"] = league_id
    async for doc in db[COL_TEAMS].find(fuzzy_query_lower, {"_id": 0}).limit(1):
        return doc["teamId"], doc["name"]

    # 3. Alias match in teams_master
    name_normalized = name_lower.replace("-", "").replace(" ", "")
    alias_query = {"$or": [
        {"nameNormalized": {"$regex": re.escape(name_normalized)}},
        {"aliases": {"$regex": re.escape(name_lower)}},
        {"aliases": {"$regex": re.escape(name_normalized)}},
    ]}
    if league_id:
        alias_query["leagueId"] = league_id
    master_doc = await db["teams_master"].find_one(alias_query, {"_id": 0})
    if not master_doc and league_id:
        master_doc = await db["teams_master"].find_one(
            {"$or": [
                {"nameNormalized": {"$regex": re.escape(name_normalized)}},
                {"aliases": {"$regex": re.escape(name_lower)}},
                {"aliases": {"$regex": re.escape(name_normalized)}},
            ]}, {"_id": 0}
        )
    if master_doc:
        return master_doc["teamId"], master_doc["name"]

    # 4. Fuzzy word-overlap: split into words and match teams sharing key words
    words = set(name_lower.replace("-", " ").split()) - {"fc", "cf", "sc", "ac", "al", "as", "ss", "de", "la"}
    if words:
        for word in sorted(words, key=len, reverse=True):
            if len(word) >= 4:  # Only match on meaningful words
                # Try nameClean first, then nameLower
                for field in ["nameClean", "nameLower"]:
                    wq = {field: {"$regex": rf"\b{re.escape(word)}\b"}}
                    if league_id:
                        wq["leagueId"] = league_id
                    async for doc in db[COL_TEAMS].find(wq, {"_id": 0}).limit(1):
                        return doc["teamId"], doc["name"]

    # 5. Consonant-skeleton match: strips vowels to handle transliteration variants
    # "al khlood" → "l khld", "al kholood" → "l khld" → MATCH
    import re as _re
    consonants = _re.sub(r'[aeiou\s\-]', '', name_lower)
    if len(consonants) >= 4:
        # Build regex that allows optional vowels between consonants
        flex_pattern = ".*".join(re.escape(c) for c in consonants[:8])
        try:
            cq = {"nameLower": {"$regex": flex_pattern}}
            if league_id:
                cq["leagueId"] = league_id
            candidates = await db[COL_TEAMS].find(cq, {"_id": 0}).to_list(5)
            if not candidates and league_id:
                candidates = await db[COL_TEAMS].find({"nameLower": {"$regex": flex_pattern}}, {"_id": 0}).to_list(5)
            if candidates:
                # Pick the one with the shortest name (most likely match)
                best = min(candidates, key=lambda d: len(d.get("name", "")))
                return best["teamId"], best["name"]
        except Exception:
            pass

    return None, None


async def get_team_info(team_name: str) -> dict:
    """Look up a club team and return full info: {teamId, name, leagueId} or None."""
    name_lower = team_name.lower().strip()
    doc = await db[COL_TEAMS].find_one({"nameLower": name_lower}, {"_id": 0})
    if doc:
        return {"teamId": doc["teamId"], "name": doc["name"], "leagueId": doc.get("leagueId")}

    # Fuzzy: substring
    async for doc in db[COL_TEAMS].find({"nameLower": {"$regex": name_lower}}, {"_id": 0}).limit(1):
        return {"teamId": doc["teamId"], "name": doc["name"], "leagueId": doc.get("leagueId")}

    return None


async def get_player_by_name(player_name: str, team_id: int = None, league_id: int = None, team_name_hint: str = None) -> dict:
    """Look up a player by name, optionally filtered by team/league. Returns player doc or None.
    
    Handles common OCR patterns:
    - "Julian Alvarez" → matches "J. Álvarez" (first name → initial)
    - "Vitinha" with league_id → picks PSG over Genoa
    - Common last names with team_name_hint → picks the right one
    
    Search strategy (most precise → least):
    1. Exact nameClean match
    1b. First-initial pattern (Julian Alvarez → J. Alvarez)
    2. Last name at end with word boundary
    3. Full name as word-boundary substring
    4. Last name word-boundary anywhere
    """
    from utils import strip_accents
    name_clean = strip_accents(player_name.lower().strip())
    international_leagues = {1, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 15, 29, 30, 31, 32, 33, 34, 115, 960}

    # Precompute the first-initial form: "julian alvarez" → "j. alvarez"
    parts = name_clean.split()
    last_name = parts[-1] if parts else name_clean
    initial_form = None
    if len(parts) >= 2:
        initial_form = parts[0][0] + ". " + " ".join(parts[1:])

    def _q(extra: dict) -> dict:
        q = dict(extra)
        if team_id:
            q["teamId"] = team_id
        return q

    async def _best_match(query: dict) -> dict:
        """Find all matches and pick the best one based on league/team context."""
        docs = await db[COL_PLAYERS].find(query, {"_id": 0}).to_list(30)
        if not docs:
            return None
        if len(docs) == 1:
            return docs[0]

        # Multiple matches — disambiguate
        # 1. If team_name_hint provided, try to match team name
        if team_name_hint:
            hint_lower = strip_accents(team_name_hint.lower())
            hint_words = set(hint_lower.replace("-", " ").split()) - {"fc", "cf", "sc", "ac"}
            for d in docs:
                t = strip_accents((d.get("teamName") or "").lower())
                t_words = set(t.replace("-", " ").split()) - {"fc", "cf", "sc", "ac"}
                if hint_words & t_words:
                    return d

        # 2. Prefer exact league match
        if league_id:
            continental = {2, 3, 848, 531, 480}
            for d in docs:
                if d.get("leagueId") == league_id:
                    return d
            if league_id in continental:
                club_docs = [d for d in docs if d.get("leagueId") not in international_leagues]
                if club_docs:
                    return club_docs[0]

        # 3. Prefer club entry over national team
        club_docs = [d for d in docs if d.get("leagueId") not in international_leagues]
        if club_docs:
            # Among clubs, prefer top-5 leagues (39,140,135,78,61)
            top5 = {39, 140, 135, 78, 61}
            top5_docs = [d for d in club_docs if d.get("leagueId") in top5]
            if top5_docs:
                return top5_docs[0]
            return club_docs[0]
        return docs[0]

    # 1. Exact match on full cleaned name
    doc = await _best_match(_q({"nameClean": name_clean}))
    if doc:
        return doc

    # 1b. First-initial pattern: "julian alvarez" → "j. alvarez"
    if initial_form:
        doc = await _best_match(_q({"nameClean": initial_form}))
        if doc:
            return doc

    # 2. Last name at end of string with word boundary
    doc = await _best_match(_q({"nameClean": {"$regex": rf"\b{re.escape(last_name)}$"}}))
    if doc:
        return doc

    # 2b. Initial + last name regex: "j." followed by last name
    if initial_form:
        first_initial = parts[0][0]
        doc = await _best_match(_q({"nameClean": {"$regex": rf"^{re.escape(first_initial)}\.\s*{re.escape(last_name)}$"}}))
        if doc:
            return doc

    # 3. Full input as word boundary match
    if name_clean != last_name:
        doc = await _best_match(_q({"nameClean": {"$regex": rf"\b{re.escape(name_clean)}\b"}}))
        if doc:
            return doc

    # 4. Last name as word boundary anywhere (broader)
    doc = await _best_match(_q({"nameClean": {"$regex": rf"\b{re.escape(last_name)}\b"}}))
    if doc:
        return doc

    # 5. Name contains (last resort)
    doc = await _best_match(_q({"nameClean": {"$regex": re.escape(last_name)}}))
    if doc:
        return doc

    # 6. Fuzzy prefix match — handles OCR typos like doubled letters ("ferryyra" → "ferreyra")
    #    Use the first 4 characters as a stable prefix (typos usually appear mid-word),
    #    fetch candidates, then pick by minimum Levenshtein distance.
    if len(last_name) >= 4:
        prefix_len = 4
        prefix = re.escape(last_name[:prefix_len])
        candidates_q = dict(_q({"nameClean": {"$regex": rf"\b{prefix}"}}))
        candidates = await db[COL_PLAYERS].find(candidates_q, {"_id": 0}).to_list(50)
        if candidates:
            def _edit_dist(a: str, b: str) -> int:
                """Simple Levenshtein distance."""
                if len(a) > len(b):
                    a, b = b, a
                prev = list(range(len(b) + 1))
                for i, ca in enumerate(a):
                    curr = [i + 1]
                    for j, cb in enumerate(b):
                        curr.append(min(prev[j + 1] + 1, curr[j] + 1, prev[j] + (0 if ca == cb else 1)))
                    prev = curr
                return prev[-1]

            # Extract each candidate's last word and compute distance to our last_name
            scored = []
            for c in candidates:
                c_name = c.get("nameClean", "")
                c_parts = c_name.split()
                c_last = c_parts[-1] if c_parts else c_name
                dist = _edit_dist(last_name, c_last)
                # Only accept if first initial matches when we have one
                if initial_form and c_parts and c_parts[0] != parts[0][0] + ".":
                    dist += 5  # penalise wrong initial
                scored.append((dist, c))
            scored.sort(key=lambda x: x[0])
            if scored and scored[0][0] <= 3:
                return scored[0][1]

    return None


async def get_league_by_name(league_name: str) -> dict:
    """Look up a league by name. Returns {leagueId, name} or None."""
    doc = await db[COL_LEAGUES].find_one(
        {"nameLower": league_name.lower().strip()}, {"_id": 0}
    )
    if doc:
        return {"leagueId": doc["leagueId"], "name": doc["name"]}

    # Fuzzy
    async for doc in db[COL_LEAGUES].find(
        {"nameLower": {"$regex": league_name.lower().strip()}}, {"_id": 0}
    ).limit(1):
        return {"leagueId": doc["leagueId"], "name": doc["name"]}
    return None


# ══════════════════════════════════════════════
#  4. PLAYER SEASON STATS (full stats per player)
# ══════════════════════════════════════════════

async def sync_player_season_stats_for_team(team_id: int, season: int = None, league_id: int = 0) -> int:
    """
    Fetch full player stats for every player in a team for a given season.
    Uses /players?team={id}&season={year} which returns ALL players + stats in one call
    (paginated). Stores in player_season_stats collection.
    """
    season = season or CURRENT_SEASON
    stored = 0
    try:
        page = 1
        while True:
            data = await api_football_request("players", {"team": team_id, "season": season, "page": page})
            if not data:
                break
            ops = []
            for entry in data:
                player = entry.get("player", {})
                pid = player.get("id")
                if not pid:
                    continue
                doc = {
                    "_id_key": f"{pid}_{season}",
                    "playerId": pid,
                    "season": season,
                    "teamId": team_id,
                    "leagueId": league_id,
                    "player": player,
                    "statistics": entry.get("statistics", []),
                    "_ts": time.time(),
                }
                ops.append(db[COL_PLAYER_STATS].update_one(
                    {"_id_key": doc["_id_key"]},
                    {"$set": doc},
                    upsert=True
                ))
                stored += 1
            if ops:
                await aio.gather(*ops, return_exceptions=True)
            # API-Football paginates at 20 per page; stop when we get fewer
            if len(data) < 20:
                break
            page += 1
            await aio.sleep(0.2)
    except Exception as e:
        print(f"[CACHE] Player stats error for team {team_id} season {season}: {e}")
    return stored


async def sync_player_season_stats_for_all_leagues(seasons: list = None) -> int:
    """Download season stats for every player in every supported league."""
    await db[COL_PLAYER_STATS].create_index("_id_key", unique=True)
    await db[COL_PLAYER_STATS].create_index("playerId")
    await db[COL_PLAYER_STATS].create_index("season")
    await db[COL_PLAYER_STATS].create_index("teamId")
    await db[COL_PLAYER_STATS].create_index("leagueId")

    # Check if we synced recently (skip if fresh)
    meta = await _get_meta("player_stats_sync")
    if meta and (time.time() - meta.get("_ts", 0)) < STATS_TTL_SECONDS:
        print(f"[CACHE] Player stats fresh (synced {meta.get('_updated', '?')}). Skipping.")
        return 0

    seasons = seasons or [CURRENT_SEASON, CURRENT_SEASON - 1]
    total = 0
    # Collect all unique team IDs across supported leagues, with their leagueId
    team_league_map: dict[int, int] = {}  # teamId → leagueId
    async for doc in db[COL_TEAMS].find({}, {"teamId": 1, "leagueId": 1}):
        tid = doc.get("teamId")
        if tid:
            team_league_map[tid] = doc.get("leagueId", 0)

    print(f"[CACHE] Player stats: syncing {len(team_league_map)} teams × {len(seasons)} seasons")
    for season in seasons:
        for team_id, league_id in team_league_map.items():
            count = await sync_player_season_stats_for_team(team_id, season, league_id)
            total += count
            await aio.sleep(0.3)  # ~3 req/s — stay well under rate limits

    await _set_meta("player_stats_sync", {"players": total, "teams": len(team_league_map)})
    print(f"[CACHE] Player stats sync complete: {total} player-season records")
    return total


async def get_cached_player_season_stats(player_id: int, seasons: list = None):
    """Read player season stats from local DB. Returns list of API-style entries."""
    seasons = seasons or [CURRENT_SEASON + 1, CURRENT_SEASON, CURRENT_SEASON - 1, CURRENT_SEASON - 2]
    results = []
    async for doc in db[COL_PLAYER_STATS].find(
        {"playerId": player_id, "season": {"$in": seasons}},
        {"_id": 0}
    ):
        results.append({"player": doc.get("player", {}), "statistics": doc.get("statistics", [])})
    return results


# ══════════════════════════════════════════════
#  5. TEAM SEASON STATS
# ══════════════════════════════════════════════

async def sync_team_season_stats_for_all_leagues(seasons: list = None) -> int:
    """Download /teams/statistics for every team×league×season combination."""
    await db[COL_TEAM_STATS].create_index(
        [("teamId", 1), ("leagueId", 1), ("season", 1)], unique=True
    )

    meta = await _get_meta("team_stats_sync")
    if meta and (time.time() - meta.get("_ts", 0)) < STATS_TTL_SECONDS:
        print(f"[CACHE] Team stats fresh. Skipping.")
        return 0

    seasons = seasons or [CURRENT_SEASON + 1, CURRENT_SEASON, CURRENT_SEASON - 1]
    total = 0
    for league in SUPPORTED_LEAGUES:
        lid = league["id"]
        team_ids = []
        async for doc in db[COL_TEAMS].find({"leagueId": lid}, {"teamId": 1}):
            if doc.get("teamId"):
                team_ids.append(doc["teamId"])

        for team_id in team_ids:
            for season in seasons:
                try:
                    data = await api_football_request("teams/statistics", {
                        "team": team_id, "league": lid, "season": season
                    })
                    if data:
                        doc = {
                            "teamId": team_id,
                            "leagueId": lid,
                            "season": season,
                            "data": data,
                            "_ts": time.time(),
                        }
                        await db[COL_TEAM_STATS].update_one(
                            {"teamId": team_id, "leagueId": lid, "season": season},
                            {"$set": doc},
                            upsert=True
                        )
                        total += 1
                    await aio.sleep(0.2)
                except Exception as e:
                    print(f"[CACHE] Team stats error {team_id}/{lid}/{season}: {e}")
                    continue

    await _set_meta("team_stats_sync", {"records": total})
    print(f"[CACHE] Team stats sync complete: {total} records")
    return total


async def get_cached_team_season_stats(team_id: int, league_id: int, seasons: list = None):
    """Read team season stats from local DB."""
    seasons = seasons or [CURRENT_SEASON + 1, CURRENT_SEASON, CURRENT_SEASON - 1]
    async for doc in db[COL_TEAM_STATS].find(
        {"teamId": team_id, "leagueId": league_id, "season": {"$in": seasons}},
        {"_id": 0, "data": 1}
    ):
        if doc.get("data"):
            return doc["data"]
    return None


# ══════════════════════════════════════════════
#  6. TEAM FIXTURE HISTORY
# ══════════════════════════════════════════════

async def sync_team_fixture_history_for_all_leagues(count: int = 40) -> int:
    """Download the last `count` finished fixtures for every team in supported leagues."""
    await db[COL_TEAM_FIXTURES].create_index("teamId", unique=True)

    meta = await _get_meta("team_fixtures_sync")
    if meta and (time.time() - meta.get("_ts", 0)) < FIXTURE_TTL_SECONDS:
        print(f"[CACHE] Team fixture history fresh. Skipping.")
        return 0

    total = 0
    team_ids = set()
    async for doc in db[COL_TEAMS].find({}, {"teamId": 1}):
        if doc.get("teamId"):
            team_ids.add(doc["teamId"])

    print(f"[CACHE] Fixture history: syncing last {count} fixtures for {len(team_ids)} teams")
    for team_id in team_ids:
        try:
            data = await api_football_request("fixtures", {"team": team_id, "last": count, "status": "FT"})
            if data:
                await db[COL_TEAM_FIXTURES].update_one(
                    {"teamId": team_id},
                    {"$set": {"teamId": team_id, "fixtures": data, "_ts": time.time()}},
                    upsert=True
                )
                total += 1
            await aio.sleep(0.3)
        except Exception as e:
            print(f"[CACHE] Fixture history error for team {team_id}: {e}")
            continue

    await _set_meta("team_fixtures_sync", {"teams": total})
    print(f"[CACHE] Fixture history sync complete: {total} teams")
    return total


async def get_cached_team_fixtures(team_id: int) -> list:
    """Read recent fixture history for a team from local DB."""
    doc = await db[COL_TEAM_FIXTURES].find_one({"teamId": team_id}, {"_id": 0, "fixtures": 1, "_ts": 1})
    if not doc or not doc.get("fixtures"):
        return []
    # Refresh if older than TTL (return stale while noting staleness)
    if time.time() - doc.get("_ts", 0) > FIXTURE_TTL_SECONDS * 2:
        return []
    return doc["fixtures"]


# ══════════════════════════════════════════════
#  CACHE STATUS
# ══════════════════════════════════════════════

async def get_cache_status() -> dict:
    """Get overview of what's cached."""
    leagues_count = await db[COL_LEAGUES].count_documents({})
    teams_count = await db[COL_TEAMS].count_documents({})
    players_count = await db[COL_PLAYERS].count_documents({})
    national_count = await db[COL_NATIONAL].count_documents({})
    transfers_count = await db[COL_TRANSFERS].count_documents({})
    player_stats_count = await db[COL_PLAYER_STATS].count_documents({})
    team_stats_count = await db[COL_TEAM_STATS].count_documents({})
    fixture_history_count = await db[COL_TEAM_FIXTURES].count_documents({})

    meta = {}
    async for doc in db[COL_META].find({}, {"_id": 0}):
        meta[doc["_key"]] = doc.get("_updated", "never")

    return {
        "leagues": leagues_count,
        "teams": teams_count,
        "players": players_count,
        "nationalTeams": national_count,
        "transfersDetected": transfers_count,
        "playerSeasonStats": player_stats_count,
        "teamSeasonStats": team_stats_count,
        "teamFixtureHistory": fixture_history_count,
        "lastSync": meta,
    }


# ══════════════════════════════════════════════
#  FULL SYNC (startup + scheduled)
# ══════════════════════════════════════════════

async def full_sync(force: bool = False):
    """Run full data sync. Skips if recently synced unless force=True."""
    meta = await _get_meta("full_sync")
    if not force and meta and (time.time() - meta.get("_ts", 0)) < SQUAD_TTL_SECONDS:
        print(f"[CACHE] Data is fresh (synced {meta.get('_updated', '?')}). Skipping.")
        return

    print("[CACHE] Starting full data sync...")
    start = time.time()

    # 1. Leagues
    league_count = await sync_leagues()
    print(f"[CACHE] Leagues: {league_count}")

    # 2. Teams for all supported leagues
    team_count = await sync_all_teams()
    print(f"[CACHE] Teams: {team_count}")

    # 3. National teams
    national_count = await sync_national_teams()

    # 4. Squads (all players — basic name/position/team)
    player_count = await sync_all_squads()

    elapsed = round(time.time() - start, 1)
    await _set_meta("full_sync", {
        "leagues": league_count, "teams": team_count,
        "national": national_count, "players": player_count,
        "elapsed_seconds": elapsed,
    })
    print(f"[CACHE] Full sync complete in {elapsed}s: {league_count} leagues, {team_count} teams, {player_count} players, {national_count} national entries")


async def deep_stats_sync():
    """
    Downloads the heavy stat collections: player season stats, team season stats,
    and team fixture history. These are queried by predictions instead of
    calling the API live. Runs separately from full_sync to avoid blocking it.
    """
    print("[CACHE] Starting deep stats sync (player stats, team stats, fixture history)...")
    start = time.time()

    # Team fixture history — refresh daily (quick, needed for Bayesian engine)
    fixture_teams = await sync_team_fixture_history_for_all_leagues(count=40)

    # Player season stats — refresh weekly (heavier, many paginated calls)
    player_records = await sync_player_season_stats_for_all_leagues()

    # Team season stats — refresh weekly
    team_records = await sync_team_season_stats_for_all_leagues()

    elapsed = round(time.time() - start, 1)
    print(f"[CACHE] Deep stats sync done in {elapsed}s — {fixture_teams} teams' fixtures, "
          f"{player_records} player-season records, {team_records} team-stat records")


async def _sync_missing_leagues() -> list[int]:
    """
    Detect any SUPPORTED_LEAGUES that have zero entries in cache_teams.
    For each missing league, sync its teams + squads immediately so the
    rest of the pipeline (fixture history, season stats) picks them up.
    Returns list of newly-synced league IDs.
    """
    existing_leagues: set[int] = set()
    async for doc in db[COL_TEAMS].find({}, {"leagueId": 1}):
        lid = doc.get("leagueId")
        if lid:
            existing_leagues.add(lid)

    missing = [lg for lg in SUPPORTED_LEAGUES if lg["id"] not in existing_leagues]
    if not missing:
        return []

    print(f"[CACHE] Detected {len(missing)} leagues not yet in cache_teams: "
          f"{[lg['name'] for lg in missing]} — syncing now...")

    synced_ids = []
    for lg in missing:
        lid = lg["id"]
        count = await sync_teams_for_league(lid)
        print(f"[CACHE] New league sync: {lg['name']} → {count} teams")
        await aio.sleep(0.4)

        if count == 0:
            # League has no active club teams (e.g. World Cup qualifiers, tournaments).
            # Store a placeholder so we don't re-check it on every restart.
            await db[COL_TEAMS].update_one(
                {"leagueId": lid, "_placeholder": True},
                {"$set": {"leagueId": lid, "_placeholder": True, "teamId": None}},
                upsert=True
            )
            continue

        # Also sync squads for these new teams so cache_players has them
        async for team in db[COL_TEAMS].find({"leagueId": lid, "_placeholder": {"$ne": True}}, {"teamId": 1, "name": 1}):
            await sync_squad(team["teamId"], team.get("name", ""), lid)
            await aio.sleep(0.3)

        synced_ids.append(lid)

    if synced_ids:
        # Invalidate player_stats_sync and team_fixtures_sync TTLs so the
        # next deep_stats_sync actually downloads data for the new leagues.
        await db[COL_META].update_many(
            {"_key": {"$in": ["player_stats_sync", "team_fixtures_sync", "team_stats_sync"]}},
            {"$unset": {"_ts": ""}}
        )
        print(f"[CACHE] TTL reset for stats syncs — new leagues will be included next run")

    return synced_ids


async def seed_cache():
    """Non-blocking startup seed — checks if data exists, syncs if needed."""
    try:
        players_count = await db[COL_PLAYERS].count_documents({})
        if players_count > 100:
            status = await get_cache_status()
            print(f"[CACHE] Data loaded: {status['leagues']} leagues, {status['teams']} teams, "
                  f"{status['players']} players, {status['nationalTeams']} national")

            # Detect newly added leagues that are not yet in cache_teams
            new_leagues = await _sync_missing_leagues()

            # Kick off deep stats in background if not yet downloaded OR new leagues added
            ps_count = await db[COL_PLAYER_STATS].count_documents({})
            if ps_count < 100 or new_leagues:
                reason = "not yet seeded" if ps_count < 100 else f"new leagues {new_leagues}"
                print(f"[CACHE] Scheduling deep stats sync ({reason})")
                aio.ensure_future(deep_stats_sync())
            return

        await full_sync(force=True)
        # After basic sync, kick off deep stats in background
        aio.ensure_future(deep_stats_sync())
    except Exception as e:
        print(f"[CACHE] Seed error: {e}")


# ══════════════════════════════════════════════
#  BACKGROUND SCHEDULER
# ══════════════════════════════════════════════

async def background_refresh_loop():
    """
    Two-speed scheduler:
    - Every 24h: refresh team fixture history + run transfers/squad sync
    - Every 7 days: refresh player season stats + team season stats
    """
    cycle = 0
    while True:
        await aio.sleep(24 * 3600)
        cycle += 1
        try:
            print(f"[CACHE] Scheduled refresh (cycle {cycle})...")
            transfers = await detect_transfers()
            if transfers:
                print(f"[CACHE] {len(transfers)} transfers detected!")
            await full_sync(force=True)
            # Fixture history refreshes every day (daily cycle)
            await sync_team_fixture_history_for_all_leagues(count=40)
            # Player + team stats refresh every 7 days
            if cycle % 7 == 0:
                await sync_player_season_stats_for_all_leagues()
                await sync_team_season_stats_for_all_leagues()
        except Exception as e:
            print(f"[CACHE] Scheduled refresh error: {e}")
