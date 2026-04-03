"""
Systematic team resolver — auto-caches ALL teams from supported leagues
and provides smart fuzzy matching for any team name/abbreviation.

Fixes: "Paris SG", "Sheff Wed", "Atletico MG", "Man Utd", etc.
"""
import re
import unicodedata
from datetime import datetime, timezone
from config import db, SUPPORTED_LEAGUES, CURRENT_SEASON
from utils import api_football_request

COL_TEAMS_MASTER = "teams_master"
CACHE_TTL = 7 * 24 * 3600  # 7 days


def _strip_accents(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn"
    )


def _normalize(name: str) -> str:
    """Lowercase, strip accents, remove punctuation."""
    s = _strip_accents(name.lower().strip())
    s = re.sub(r"[^a-z0-9\s]", "", s)
    return re.sub(r"\s+", " ", s).strip()


def _generate_aliases(name: str) -> list:
    """Generate all possible search aliases for a team name."""
    norm = _normalize(name)
    words = norm.split()
    aliases = set()

    # Full normalized name
    aliases.add(norm)

    # Each meaningful word (3+ chars)
    for w in words:
        if len(w) >= 3:
            aliases.add(w)

    # First word + abbreviation of rest (e.g., "paris sg" from "Paris Saint Germain")
    if len(words) >= 2:
        abbrev = "".join(w[0] for w in words[1:])
        aliases.add(f"{words[0]} {abbrev}")
        # Also just the abbreviation letters (e.g., "psg")
        full_abbrev = "".join(w[0] for w in words)
        if len(full_abbrev) >= 2:
            aliases.add(full_abbrev)

    # Handle common patterns
    # "FC Barcelona" → "barcelona"
    # "Real Madrid CF" → "real madrid"
    filtered = [w for w in words if w not in ("fc", "cf", "sc", "ac", "as", "us", "ss", "cd", "ca", "rc")]
    if filtered and filtered != words:
        aliases.add(" ".join(filtered))

    # "Sheffield Utd" → "sheff utd", "sheffield utd"
    # "Manchester United" → "man utd", "man united"
    SHORT_PREFIXES = {
        "sheffield": "sheff", "manchester": "man",
        "wolverhampton": "wolves", "nottingham": "notts",
        "tottenham": "spurs", "newcastle": "newc",
    }
    if words and words[0] in SHORT_PREFIXES:
        short = SHORT_PREFIXES[words[0]]
        rest = " ".join(words[1:])
        if rest:
            aliases.add(f"{short} {rest}")
        aliases.add(short)

    return list(aliases)


async def build_teams_cache(force: bool = False):
    """Fetch all teams from supported domestic leagues and cache them."""
    # Check if cache is fresh
    if not force:
        meta = await db["cache_meta"].find_one({"_key": "teams_master_built"}, {"_id": 0})
        if meta and meta.get("ts"):
            ts = meta["ts"]
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - ts).total_seconds()
            if age < CACHE_TTL:
                count = await db[COL_TEAMS_MASTER].count_documents({})
                if count > 100:
                    return count

    print("[TEAM CACHE] Building master team cache from all supported leagues...")
    all_teams = []
    domestic_leagues = [lg for lg in SUPPORTED_LEAGUES if lg.get("type") == "Domestic"]

    for league in domestic_leagues:
        lid = league["id"]
        try:
            data = await api_football_request("teams", {"league": lid, "season": CURRENT_SEASON})
            if not data:
                # Try previous season
                data = await api_football_request("teams", {"league": lid, "season": CURRENT_SEASON - 1})
            if data:
                for t in data:
                    team = t.get("team", {})
                    team_id = team.get("id")
                    name = team.get("name", "")
                    country = (team.get("country") or "").lower()
                    if not team_id or not name:
                        continue
                    # Skip women/youth teams
                    name_lower = name.lower()
                    if name_lower.endswith(" w") or "women" in name_lower:
                        continue
                    if any(s in name_lower for s in ["u20", "u23", "u21", "u19", "u18", " ii", " b "]):
                        continue
                    aliases = _generate_aliases(name)
                    all_teams.append({
                        "teamId": team_id,
                        "name": name,
                        "nameNormalized": _normalize(name),
                        "aliases": aliases,
                        "leagueId": lid,
                        "leagueName": league["name"],
                        "country": country,
                    })
                print(f"  [TEAM CACHE] League {lid} ({league['name']}): {len(data)} teams")
        except Exception as e:
            print(f"  [TEAM CACHE] League {lid} error: {e}")

    if all_teams:
        # Upsert all teams
        for team in all_teams:
            await db[COL_TEAMS_MASTER].update_one(
                {"teamId": team["teamId"]},
                {"$set": team},
                upsert=True,
            )
        # Update meta
        await db["cache_meta"].update_one(
            {"_key": "teams_master_built"},
            {"$set": {"ts": datetime.now(timezone.utc), "count": len(all_teams)}},
            upsert=True,
        )
        # Create text search index
        try:
            await db[COL_TEAMS_MASTER].create_index("aliases")
            await db[COL_TEAMS_MASTER].create_index("nameNormalized")
            await db[COL_TEAMS_MASTER].create_index("leagueId")
        except Exception:
            pass

    print(f"[TEAM CACHE] Done: {len(all_teams)} teams cached")
    return len(all_teams)


async def find_team(query: str, league_id: int = None) -> dict:
    """
    Smart fuzzy search for a team name. Handles abbreviations, accents, etc.
    Returns: {"teamId": int, "teamName": str, "leagueId": int} or None
    """
    # Ensure cache exists
    count = await db[COL_TEAMS_MASTER].count_documents({})
    if count == 0:
        await build_teams_cache()

    norm = _normalize(query)
    if not norm:
        return None

    # Strategy 1: Exact normalized name match
    filt = {"nameNormalized": norm}
    if league_id:
        filt["leagueId"] = league_id
    doc = await db[COL_TEAMS_MASTER].find_one(filt, {"_id": 0})
    if doc:
        return {"teamId": doc["teamId"], "teamName": doc["name"], "leagueId": doc["leagueId"]}

    # Strategy 2: Alias match (handles "paris sg", "psg", "sheff wed", etc.)
    filt = {"aliases": norm}
    if league_id:
        filt["leagueId"] = league_id
    doc = await db[COL_TEAMS_MASTER].find_one(filt, {"_id": 0})
    if doc:
        return {"teamId": doc["teamId"], "teamName": doc["name"], "leagueId": doc["leagueId"]}

    # Strategy 3: Substring match on normalized name
    filt = {"nameNormalized": {"$regex": re.escape(norm)}}
    if league_id:
        filt["leagueId"] = league_id
    doc = await db[COL_TEAMS_MASTER].find_one(filt, {"_id": 0})
    if doc:
        return {"teamId": doc["teamId"], "teamName": doc["name"], "leagueId": doc["leagueId"]}

    # Strategy 4: Any word from query appears in any alias
    words = norm.split()
    if words:
        longest_word = max(words, key=len)
        if len(longest_word) >= 3:
            filt = {"aliases": {"$regex": re.escape(longest_word)}}
            if league_id:
                filt["leagueId"] = league_id
            candidates = await db[COL_TEAMS_MASTER].find(filt, {"_id": 0}).to_list(10)
            if candidates:
                # Score by how many query words match
                best = None
                best_score = 0
                for c in candidates:
                    score = sum(1 for w in words if any(w in a for a in c["aliases"]))
                    if score > best_score:
                        best = c
                        best_score = score
                if best:
                    return {"teamId": best["teamId"], "teamName": best["name"], "leagueId": best["leagueId"]}

    # Strategy 5: Without league filter (if we had one)
    if league_id:
        return await find_team(query, league_id=None)

    return None
