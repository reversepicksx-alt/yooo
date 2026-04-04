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
    STRIP_PREFIXES = ("fc", "cf", "sc", "ac", "as", "us", "ss", "cd", "ca", "rc",
                      "sv", "vfb", "vfl", "rb", "tsg", "fsv", "1")
    filtered = [w for w in words if w not in STRIP_PREFIXES]
    if filtered and filtered != words:
        aliases.add(" ".join(filtered))
        # Also add just the city part if there's a compound like "eintracht frankfurt" → "frankfurt"
        for w in filtered:
            if len(w) >= 4:
                aliases.add(w)

    # Strip common team prefixes to get city names
    # "Borussia Mönchengladbach" → "monchengladbach", "gladbach"
    # "Eintracht Frankfurt" → "frankfurt"
    # "Bayer Leverkusen" → "leverkusen"
    COMMON_PREFIXES = ("borussia", "eintracht", "bayer", "deportivo", "atletico",
                       "sporting", "real", "racing", "dynamo", "cska",
                       "al", "al-")
    if len(words) >= 2 and words[0] in COMMON_PREFIXES:
        city_part = " ".join(words[1:])
        aliases.add(city_part)
        for w in words[1:]:
            if len(w) >= 4:
                aliases.add(w)

    # Handle "Al-Hilal Saudi FC" → "hilal", "al hilal"
    # Handle "Al Taawon" → "taawon", "al taawon"
    joined = " ".join(words)
    if joined.startswith("al-") or joined.startswith("al "):
        without_al = joined[3:] if joined.startswith("al-") else joined[3:]
        without_al = without_al.strip()
        aliases.add(without_al)
        # Also strip trailing qualifiers like "saudi fc", "fc", "jeddah"
        STRIP_SUFFIXES = ("saudi fc", "fc", "jeddah", "saihat")
        for sfx in STRIP_SUFFIXES:
            if without_al.endswith(sfx):
                core = without_al[:-len(sfx)].strip()
                if core and len(core) >= 3:
                    aliases.add(core)

    # "Sheffield Utd" → "sheff utd", "sheffield utd"
    # "Manchester United" → "man utd", "man united"
    SHORT_PREFIXES = {
        "sheffield": "sheff", "manchester": "man",
        "wolverhampton": "wolves", "nottingham": "notts",
        "tottenham": "spurs", "newcastle": "newc",
        "monchengladbach": "gladbach",
    }
    if words and words[0] in SHORT_PREFIXES:
        short = SHORT_PREFIXES[words[0]]
        rest = " ".join(words[1:])
        if rest:
            aliases.add(f"{short} {rest}")
        aliases.add(short)

    # Also check if any word in the name has a known short form
    for w in words:
        if w in SHORT_PREFIXES:
            aliases.add(SHORT_PREFIXES[w])

    # Handle "United" ↔ "Utd", "Wednesday" ↔ "Wed", "City" stays "City"
    WORD_ABBREVS = {"united": "utd", "wednesday": "wed", "athletic": "ath", "albion": "alb", "forest": "for"}
    expanded_aliases = set()
    for alias in list(aliases):
        alias_words = alias.split()
        for full_form, short_form in WORD_ABBREVS.items():
            if full_form in alias_words:
                new_alias = alias.replace(full_form, short_form)
                expanded_aliases.add(new_alias)
            if short_form in alias_words:
                new_alias = alias.replace(short_form, full_form)
                expanded_aliases.add(new_alias)
    aliases.update(expanded_aliases)

    return list(aliases)


# Known scan abbreviations that AI vision models commonly output
SCAN_ALIASES = {
    "mgladbach": "borussia monchengladbach",
    "gladbach": "borussia monchengladbach",
    "monchengladbach": "borussia monchengladbach",
    "b dortmund": "borussia dortmund",
    "dortmund": "borussia dortmund",
    "leverkusen": "bayer leverkusen",
    "frankfurt": "eintracht frankfurt",
    "hoffenheim": "tsg hoffenheim",
    "heidenheim": "1 fc heidenheim 1846",
    "freiburg": "sc freiburg",
    "mainz": "1 fsv mainz 05",
    "augsburg": "fc augsburg",
    "st pauli": "fc st pauli",
    "union berlin": "1 fc union berlin",
    "hertha": "hertha bsc",
    "koln": "1 fc koln",
    "cologne": "1 fc koln",
    "schalke": "fc schalke 04",
    "wolfsburg": "vfl wolfsburg",
    "bremen": "werder bremen",
    "bochum": "vfl bochum",
    "stuttgart": "vfb stuttgart",
    "atletico": "atletico madrid",
    "betis": "real betis",
    "sociedad": "real sociedad",
    "villarreal": "villarreal",
    "bilbao": "athletic club",
    "getafe": "getafe",
    "osasuna": "ca osasuna",
    "vallecano": "rayo vallecano",
    "celta": "celta vigo",
    "lyon": "olympique lyonnais",
    "marseille": "olympique de marseille",
    "monaco": "as monaco",
    "psg": "paris saint germain",
    "paris sg": "paris saint germain",
    "saint etienne": "as saint etienne",
    "lens": "rc lens",
    "nice": "ogc nice",
    "rennes": "stade rennais fc 1901",
    "strasbourg": "rc strasbourg alsace",
    "lille": "lille",
    "nantes": "fc nantes",
    "brest": "stade brestois 29",
    "inter": "inter",
    "napoli": "napoli",
    "atalanta": "atalanta",
    "lazio": "lazio",
    "fiorentina": "fiorentina",
    "roma": "as roma",
    "juventus": "juventus",
    "milan": "ac milan",
    "torino": "torino",
    "genoa": "genoa",
    "udinese": "udinese",
    "bologna": "bologna",
    "empoli": "empoli",
    "lecce": "lecce",
    "verona": "hellas verona",
    "parma": "parma",
    "cagliari": "cagliari",
    "como": "como",
    "venezia": "venezia",
    "monza": "monza",
    # Premier League common abbreviations
    "man utd": "manchester united",
    "man united": "manchester united",
    "man city": "manchester city",
    "spurs": "tottenham",
    "wolves": "wolves",
    "sheff utd": "sheffield utd",
    "newcastle": "newcastle",
    "newcastle utd": "newcastle",
    "west ham": "west ham united",
    "west brom": "west bromwich",
    "nottm forest": "nottingham forest",
    "nott forest": "nottingham forest",
    "nottingham": "nottingham forest",
    "crystal palace": "crystal palace",
    "aston villa": "aston villa",
    "leeds": "leeds united",
    "leeds utd": "leeds united",
    "leicester": "leicester city",
    "everton": "everton",
    "burnley": "burnley",
    "southampton": "southampton",
    "brighton": "brighton",
    "brentford": "brentford",
    "fulham": "fulham",
    "bournemouth": "bournemouth",
    "ipswich": "ipswich town",
    "luton": "luton town",
    # MLS common
    "lafc": "los angeles fc",
    "la galaxy": "los angeles galaxy",
    "nycfc": "new york city fc",
    "nyrb": "new york red bulls",
    "red bulls": "new york red bulls",
    "atlanta utd": "atlanta united",
    "atlanta united": "atlanta united",
    "columbus": "columbus crew",
    "portland": "portland timbers",
    "seattle": "seattle sounders",
    "miami": "inter miami",
    "inter miami": "inter miami",
    # Liga MX common
    "america": "club america",
    "guadalajara": "guadalajara",
    "chivas": "guadalajara",
    "monterrey": "monterrey",
    "tigres": "tigres uanl",
    "cruz azul": "cruz azul",
    "pumas": "pumas unam",
    "santos": "santos laguna",
    "toluca": "toluca",
    "leon": "club leon",
    "pachuca": "pachuca",
    "atlas": "atlas",
    "mazatlan": "mazatlan",
    "necaxa": "necaxa",
    "puebla": "puebla",
    "queretaro": "queretaro",
    "juarez": "fc juarez",
    "tijuana": "club tijuana",
    # NWSL common
    "houston dash": "houston dash w",
    "racing louisville": "racing louisville w",
    "racing": "racing louisville w",
    "angel city": "angel city w",
    "portland thorns": "portland thorns w",
    "thorns": "portland thorns w",
    "chicago stars": "chicago red stars w",
    "red stars": "chicago red stars w",
    "kansas city": "kansas city current w",
    "kc current": "kansas city current w",
    "orlando pride": "orlando pride w",
    "pride": "orlando pride w",
    "gotham": "gotham fc w",
    "gotham fc": "gotham fc w",
    "north carolina": "north carolina courage w",
    "nc courage": "north carolina courage w",
    "courage": "north carolina courage w",
    "washington spirit": "washington spirit w",
    "spirit": "washington spirit w",
    "san diego wave": "san diego wave w",
    "wave": "san diego wave w",
    "bay fc": "bay fc w",
    "boston legacy": "boston legacy w",
    "utah royals": "utah royals w",
    "royals": "utah royals w",
    "seattle reign": "seattle reign w",
    "reign": "seattle reign w",
    # Saudi Pro League common
    "hilal": "al-hilal saudi fc",
    "al hilal": "al-hilal saudi fc",
    "al-hilal": "al-hilal saudi fc",
    "alhilal": "al-hilal saudi fc",
    "nassr": "al-nassr",
    "al nassr": "al-nassr",
    "al-nassr": "al-nassr",
    "alnassr": "al-nassr",
    "ittihad": "al-ittihad fc",
    "al ittihad": "al-ittihad fc",
    "al-ittihad": "al-ittihad fc",
    "alittihad": "al-ittihad fc",
    "ahli": "al-ahli jeddah",
    "al ahli": "al-ahli jeddah",
    "al-ahli": "al-ahli jeddah",
    "alahli": "al-ahli jeddah",
    "al ahli jeddah": "al-ahli jeddah",
    "ettifaq": "al-ettifaq",
    "al ettifaq": "al-ettifaq",
    "al-ettifaq": "al-ettifaq",
    "alettifaq": "al-ettifaq",
    "taawon": "al taawon",
    "taawoun": "al taawon",
    "al taawon": "al taawon",
    "al taawoun": "al taawon",
    "al-taawon": "al taawon",
    "al-taawoun": "al taawon",
    "altaawon": "al taawon",
    "altaawoun": "al taawon",
    "fateh": "al-fateh",
    "al fateh": "al-fateh",
    "al-fateh": "al-fateh",
    "alfateh": "al-fateh",
    "shabab": "al shabab",
    "al shabab": "al shabab",
    "alshabab": "al shabab",
    "fayha": "al-fayha",
    "al fayha": "al-fayha",
    "al-fayha": "al-fayha",
    "alfayha": "al-fayha",
    "qadisiyah": "al-qadisiyah fc",
    "al qadisiyah": "al-qadisiyah fc",
    "al-qadisiyah": "al-qadisiyah fc",
    "alqadisiyah": "al-qadisiyah fc",
    "damac": "damac",
    "khaleej": "al khaleej saihat",
    "al khaleej": "al khaleej saihat",
    "alkhaleej": "al khaleej saihat",
    "okhdood": "al okhdood",
    "al okhdood": "al okhdood",
    "alokhdood": "al okhdood",
    "kholood": "al kholood",
    "al kholood": "al kholood",
    "alkholood": "al kholood",
    "neom": "neom",
    "riyadh": "al riyadh",
    "al riyadh": "al riyadh",
    "alriyadh": "al riyadh",
    "najma": "al najma",
    "al najma": "al najma",
    "alnajma": "al najma",
}


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
                    # Skip youth teams
                    name_lower = name.lower()
                    # For women's leagues (NWSL=254), keep women's teams
                    if lid not in (254,):
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

    # Strategy 0: Known scan aliases (AI vision model abbreviations)
    if norm in SCAN_ALIASES:
        canonical = _normalize(SCAN_ALIASES[norm])
        filt = {"nameNormalized": canonical}
        if league_id:
            filt["leagueId"] = league_id
        doc = await db[COL_TEAMS_MASTER].find_one(filt, {"_id": 0})
        if doc:
            return {"teamId": doc["teamId"], "teamName": doc["name"], "leagueId": doc["leagueId"]}
        # Try alias match on canonical
        filt2 = {"aliases": canonical}
        if league_id:
            filt2["leagueId"] = league_id
        doc = await db[COL_TEAMS_MASTER].find_one(filt2, {"_id": 0})
        if doc:
            return {"teamId": doc["teamId"], "teamName": doc["name"], "leagueId": doc["leagueId"]}
        # Try without league filter
        doc = await db[COL_TEAMS_MASTER].find_one({"nameNormalized": canonical}, {"_id": 0})
        if not doc:
            doc = await db[COL_TEAMS_MASTER].find_one({"aliases": canonical}, {"_id": 0})
        if doc:
            return {"teamId": doc["teamId"], "teamName": doc["name"], "leagueId": doc["leagueId"]}

    # Strategy 0b: Try expanding abbreviations in the query itself
    # "man utd" → try "man united", "newcastle utd" → "newcastle united"
    QUERY_EXPANSIONS = {"utd": "united", "wed": "wednesday", "ath": "athletic", "alb": "albion", "for": "forest"}
    expanded_norms = [norm]
    for abbr, full in QUERY_EXPANSIONS.items():
        if abbr in norm.split():
            expanded_norms.append(norm.replace(abbr, full))
        if full in norm.split():
            expanded_norms.append(norm.replace(full, abbr))

    norm = _normalize(query)
    if not norm:
        return None

    # Strategy 1: Exact normalized name match
    for n in expanded_norms:
        filt = {"nameNormalized": n}
        if league_id:
            filt["leagueId"] = league_id
        doc = await db[COL_TEAMS_MASTER].find_one(filt, {"_id": 0})
        if doc:
            return {"teamId": doc["teamId"], "teamName": doc["name"], "leagueId": doc["leagueId"]}

    # Strategy 2: Alias match (handles "paris sg", "psg", "sheff wed", etc.)
    for n in expanded_norms:
        filt = {"aliases": n}
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
