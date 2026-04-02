import json
import uuid
import traceback
from fastapi import APIRouter, HTTPException
from emergentintegrations.llm.chat import LlmChat, UserMessage, ImageContent

from config import (
    EMERGENT_LLM_KEY, SUPPORTED_LEAGUES, CURRENT_SEASON,
    PROP_TYPE_ALIASES, INTERNATIONAL_LEAGUES, NATION_TO_LEAGUES,
    TOP_5_LEAGUES, db,
)
from models import ScanPropRequest
from utils import api_football_request, strip_accents
from cache import get_national_team_id, get_player_by_name, get_team_by_name, get_team_info
from basketball_utils import search_nba_teams as search_basketball_teams_live
from basketball_cache import get_bball_team_by_name, search_bball_teams

router = APIRouter(prefix="/api", tags=["scan"])

# Valid prop types for normalization
VALID_SOCCER_PROPS = {
    "goals", "assists", "pass_attempts", "shots", "shots_on_target", "tackles",
    "key_passes", "saves", "interceptions", "blocks", "dribbles", "fouls_drawn",
    "fouls_committed", "shots_assisted", "crosses", "clearances", "duels_won",
    "yellow_cards", "dribbles_success",
}

VALID_BASKETBALL_PROPS = {
    "points", "rebounds", "assists", "pts_reb_ast", "pts_reb", "pts_ast",
    "reb_ast", "blk_stl", "three_pointers",
    "fgm", "ftm", "fga", "fta", "tpa",
    "steals", "blocks", "turnovers",
}

BASKETBALL_PROP_ALIASES = {
    "point": "points", "pts": "points", "pt": "points",
    "rebound": "rebounds", "reb": "rebounds", "total rebounds": "rebounds",
    "assist": "assists", "ast": "assists",
    # Compound: PRA
    "pts+reb+ast": "pts_reb_ast", "pra": "pts_reb_ast", "points+rebounds+assists": "pts_reb_ast",
    "pts + reb + ast": "pts_reb_ast", "pts_reb_ast": "pts_reb_ast",
    # Compound: Reb+Ast
    "rebs+asts": "reb_ast", "reb+ast": "reb_ast", "rebounds+assists": "reb_ast",
    "reb + ast": "reb_ast", "rebs + asts": "reb_ast", "ra": "reb_ast",
    "rebounds + assists": "reb_ast", "reb_ast": "reb_ast",
    # Compound: Pts+Reb
    "pts+reb": "pts_reb", "points+rebounds": "pts_reb", "pts + reb": "pts_reb",
    "pts_reb": "pts_reb", "points + rebounds": "pts_reb", "pr": "pts_reb",
    # Compound: Pts+Ast
    "pts+ast": "pts_ast", "points+assists": "pts_ast", "pts + ast": "pts_ast",
    "pts_ast": "pts_ast", "points + assists": "pts_ast", "pa": "pts_ast",
    # Compound: Blk+Stl
    "blks+stls": "blk_stl", "blk+stl": "blk_stl", "blocks+steals": "blk_stl",
    "blk + stl": "blk_stl", "blks + stls": "blk_stl", "blocks + steals": "blk_stl",
    "blk_stl": "blk_stl",
    # Singles
    "steal": "steals", "stl": "steals",
    "block": "blocks", "blk": "blocks",
    "turnover": "turnovers", "to": "turnovers", "tov": "turnovers",
    "three pointer": "three_pointers", "3pt": "three_pointers", "3pm": "three_pointers",
    "3-point fg": "three_pointers", "threes": "three_pointers", "3-pointers made": "three_pointers",
    "three pointers made": "three_pointers", "3 pointers": "three_pointers", "threes made": "three_pointers",
    "3ptm": "three_pointers", "three pointers": "three_pointers",
    "field goals made": "fgm", "fg made": "fgm", "field goal": "fgm",
    "free throws made": "ftm", "ft made": "ftm", "free throw": "ftm",
    "field goals attempted": "fga", "fg attempted": "fga",
    "free throws attempted": "fta", "ft attempted": "fta",
    "three point attempts": "tpa", "3pt attempts": "tpa", "3pa": "tpa",
}

# Hardcoded team→league fallback (used when cache misses)
TEAM_LEAGUE_MAP = {
    "botafogo": 71, "flamengo": 71, "palmeiras": 71, "sao paulo": 71, "corinthians": 71,
    "atletico mineiro": 71, "atletico mg": 71, "atletico paranaense": 71, "athletico": 71, "athletico pr": 71,
    "gremio": 71, "internacional": 71, "cruzeiro": 71, "fluminense": 71, "santos": 71,
    "vasco": 71, "bahia": 71, "fortaleza": 71, "bragantino": 71, "juventude": 71,
    "cuiaba": 71, "goias": 71, "vitoria": 71, "sport": 71, "ceara": 71,
    "coritiba": 71, "mirassol": 71, "sport recife": 71, "america mineiro": 71,
    "chapecoense": 71, "criciuma": 71, "guarani": 71, "ponte preta": 71,
    "operario": 71, "novorizontino": 71, "avai": 71, "nautico": 71,
    "londrina": 71, "vila nova": 71, "sampaio correa": 71, "ituano": 71,
    "botafogo sp": 71, "csa": 71, "abc": 71, "tombense": 71,
    "paysandu": 71, "remo": 71, "santa cruz": 71, "atletico go": 71,
    "arsenal": 39, "chelsea": 39, "liverpool": 39, "manchester city": 39, "man city": 39,
    "manchester united": 39, "man united": 39, "tottenham": 39, "spurs": 39,
    "newcastle": 39, "aston villa": 39, "west ham": 39, "brighton": 39, "wolves": 39,
    "crystal palace": 39, "everton": 39, "fulham": 39, "brentford": 39, "bournemouth": 39,
    "nottingham forest": 39, "leicester": 39, "ipswich": 39, "southampton": 39,
    "real madrid": 140, "barcelona": 140, "atletico madrid": 140, "athletic bilbao": 140,
    "real sociedad": 140, "betis": 140, "villarreal": 140, "sevilla": 140, "girona": 140,
    "valencia": 140, "getafe": 140, "osasuna": 140, "celta vigo": 140, "mallorca": 140,
    "rayo vallecano": 140, "alaves": 140, "las palmas": 140, "cadiz": 140,
    "leganes": 140, "valladolid": 140, "real valladolid": 140, "espanyol": 140,
    "real betis": 140,
    # Argentine Liga Profesional - league 128
    "independiente": 128, "boca juniors": 128, "boca": 128, "river plate": 128, "river": 128,
    "racing": 128, "racing club": 128, "san lorenzo": 128, "huracan": 128,
    "velez sarsfield": 128, "velez": 128, "argentinos juniors": 128, "argentinos jrs": 128,
    "lanus": 128, "banfield": 128, "defensa y justicia": 128, "defensa": 128,
    "tigre": 128, "platense": 128, "belgrano": 128, "talleres": 128,
    "union": 128, "union santa fe": 128, "colon": 128, "newells": 128,
    "newells old boys": 128, "rosario central": 128, "godoy cruz": 128,
    "estudiantes": 128, "gimnasia": 128, "gimnasia lp": 128,
    "atletico tucuman": 128, "central cordoba": 128, "sarmiento": 128,
    "barracas central": 128, "instituto": 128, "aldosivi": 128,
    "independiente rivadavia": 128, "riestra": 128,
    # Liga Pro Ecuador - league 242
    "barcelona sc": 242, "barcelona sporting club": 242, "emelec": 242,
    "liga de quito": 242, "ldu quito": 242, "ldu de quito": 242,
    "independiente del valle": 242, "delfin": 242, "aucas": 242,
    "deportivo cuenca": 242, "mushuc runa": 242, "orense": 242,
    "guayaquil city": 242, "tecnico universitario": 242, "el nacional": 242,
    "libertad fc": 242, "cumbaya": 242, "macara": 242,
    # A-League (Australia) - league 188
    "melbourne victory": 188, "melbourne city": 188, "sydney fc": 188, "western sydney": 188,
    "western sydney wanderers": 188, "central coast mariners": 188, "macarthur": 188,
    "macarthur fc": 188, "wellington phoenix": 188, "perth glory": 188,
    "adelaide united": 188, "newcastle jets": 188, "brisbane roar": 188,
    "auckland fc": 188,
    # MLS (USA) - league 253
    "inter miami": 253, "la galaxy": 253, "lafc": 253, "los angeles fc": 253,
    "new york city fc": 253, "nycfc": 253, "atlanta united": 253,
    "seattle sounders": 253, "portland timbers": 253, "austin fc": 253,
    "nashville sc": 253, "columbus crew": 253, "fc cincinnati": 253,
    "philadelphia union": 253, "new england revolution": 253,
    "toronto fc": 253, "cf montreal": 253, "vancouver whitecaps": 253,
    "sporting kansas city": 253, "houston dynamo": 253, "real salt lake": 253,
    "minnesota united": 253, "colorado rapids": 253, "fc dallas": 253,
    "san jose earthquakes": 253, "charlotte fc": 253, "dc united": 253,
    "new york red bulls": 253, "chicago fire": 253, "st louis city": 253,
    "san diego fc": 253,
    "bayern munich": 78, "bayern": 78, "dortmund": 78, "borussia dortmund": 78,
    "leverkusen": 78, "bayer leverkusen": 78, "rb leipzig": 78, "leipzig": 78,
    "stuttgart": 78, "frankfurt": 78, "wolfsburg": 78, "freiburg": 78,
    "union berlin": 78, "hoffenheim": 78, "mainz": 78, "augsburg": 78,
    "werder bremen": 78, "bremen": 78, "heidenheim": 78, "bochum": 78,
    "monchengladbach": 78, "borussia monchengladbach": 78, "gladbach": 78,
    "st pauli": 78, "holstein kiel": 78, "koln": 78, "cologne": 78,
    "inter milan": 135, "inter": 135, "ac milan": 135, "milan": 135, "juventus": 135,
    "napoli": 135, "roma": 135, "lazio": 135, "atalanta": 135, "fiorentina": 135,
    "bologna": 135, "torino": 135, "monza": 135, "genoa": 135, "cagliari": 135,
    "udinese": 135, "empoli": 135, "verona": 135, "hellas verona": 135,
    "lecce": 135, "sassuolo": 135, "salernitana": 135, "frosinone": 135,
    "como": 135, "venezia": 135, "parma": 135, "sampdoria": 135,
    "psg": 61, "paris saint-germain": 61, "paris sg": 61, "marseille": 61, "lyon": 61, "monaco": 61,
    "lille": 61, "lens": 61, "nice": 61, "rennes": 61, "strasbourg": 61,
    "toulouse": 61, "nantes": 61, "reims": 61, "montpellier": 61, "brest": 61,
    "le havre": 61, "clermont": 61, "metz": 61, "lorient": 61, "auxerre": 61,
    "angers": 61, "saint-etienne": 61, "st etienne": 61, "ajaccio": 61,
    "portland thorns": 254, "washington spirit": 254, "north carolina courage": 254,
    "orlando pride": 254, "gotham fc": 254, "angel city": 254, "kansas city current": 254,
    "italy": 5, "france": 5, "germany": 5, "spain": 5, "england": 5,
    "portugal": 5, "brazil": 5, "argentina": 5, "netherlands": 5, "belgium": 5,
    "croatia": 5, "usa": 5, "united states": 5, "mexico": 5, "japan": 5,
    "south korea": 5, "turkey": 5, "serbia": 5, "poland": 5, "denmark": 5,
    "sweden": 5, "norway": 5, "colombia": 5, "uruguay": 5, "chile": 5,
    "nigeria": 5, "senegal": 5, "morocco": 5, "egypt": 5, "australia": 5,
    "bosnia": 5, "bosnia & herzegovina": 5, "scotland": 5, "wales": 5,
    "switzerland": 5, "austria": 5, "czech republic": 5, "czechia": 5, "ukraine": 5,
    "romania": 5, "greece": 5, "costa rica": 5, "canada": 5, "iran": 5,
    "algeria": 5, "cameroon": 5, "ghana": 5, "ivory coast": 5, "tunisia": 5,
}


async def _infer_league_id(team_name: str, opponent_name: str, ai_league_id: int) -> int:
    """Infer league ID: check both teams for cross-country Copa/UCL detection first."""
    SOUTH_AMERICAN_LEAGUES = {71, 128, 242}  # Brasileirao, Argentine Liga, Liga Pro Ecuador
    EUROPEAN_TOP_LEAGUES = {39, 140, 135, 78, 61}  # EPL, La Liga, Serie A, Bundesliga, Ligue 1

    # Step 1: Resolve league for BOTH teams (cache + map)
    team_league = None
    opp_league = None
    for name, is_team in [(team_name, True), (opponent_name, False)]:
        if not name:
            continue
        found_lid = None
        # Try cache
        info = await get_team_info(name)
        if info and info.get("leagueId"):
            found_lid = info["leagueId"]
        # Try hardcoded map
        if not found_lid:
            name_lower = name.lower().strip()
            if name_lower in TEAM_LEAGUE_MAP:
                found_lid = TEAM_LEAGUE_MAP[name_lower]
            else:
                for key, lid in TEAM_LEAGUE_MAP.items():
                    if key in name_lower or name_lower in key:
                        found_lid = lid
                        break
        if found_lid:
            if is_team:
                team_league = found_lid
            else:
                opp_league = found_lid

    # Step 2: Cross-country detection → Copa / Champions League
    if team_league and opp_league and team_league != opp_league:
        if team_league in SOUTH_AMERICAN_LEAGUES and opp_league in SOUTH_AMERICAN_LEAGUES:
            return 13  # Copa Libertadores
        if team_league in EUROPEAN_TOP_LEAGUES and opp_league in EUROPEAN_TOP_LEAGUES:
            return 2  # Champions League

    # Step 3: Return whichever league we found
    if team_league:
        return team_league
    if opp_league:
        return opp_league

    # AI guess fallback (only if not default Premier League guess)
    if ai_league_id and ai_league_id != 39:
        return ai_league_id

    return ai_league_id or 71


async def _resolve_player_via_cache(player_name: str, team_id: int = None) -> dict:
    """Try to find a player in the MongoDB cache. Returns player doc or None."""
    player = await get_player_by_name(player_name, team_id)
    if player:
        return player
    # If team filter was used and missed, try without it
    if team_id:
        player = await get_player_by_name(player_name)
        if player:
            return player
    return None


async def _resolve_player_via_api(player_name: str, player_team_hint: str,
                                   leagues_to_try: list, is_international: bool,
                                   nat_team_id: int, original_team_name: str,
                                   opponent_hint: str = "") -> tuple:
    """Fallback: search API-Sports for a player. Returns (resolved_player, league_id, league_name) or (None, None, None)."""
    search_clean = strip_accents(player_name.strip())
    search_variants = [search_clean]
    name_parts = search_clean.split()
    if len(name_parts) > 1:
        last = name_parts[-1]
        if last != search_clean:
            search_variants.append(last)

    def pick_best(data_list, query, team_hint, opponent_hint=None):
        query_lower = strip_accents(query.lower())
        last_name = query_lower.split()[-1] if query_lower.split() else query_lower
        team_hints = []
        if team_hint:
            team_hints.append(team_hint)
            th_var = team_hint.replace("th", "t")
            if th_var != team_hint:
                team_hints.append(th_var)
            team_hints.append(team_hint.split()[0])

        candidates = []
        for d in data_list[:20]:
            pname = strip_accents(d["player"]["name"].lower())
            team_name = (d.get("statistics", [{}])[0].get("team", {}).get("name") or "").lower()
            league_name = (d.get("statistics", [{}])[0].get("league", {}).get("name") or "").lower()
            name_match = query_lower in pname or pname in query_lower or last_name in pname
            team_match = any(th in team_name or team_name in th for th in team_hints) if team_hints else False
            # Check if this player's league matches the opponent's league (disambiguation)
            league_match = False
            if opponent_hint and not team_match:
                opp_lower = opponent_hint.lower().strip()
                if opp_lower in TEAM_LEAGUE_MAP:
                    opp_league = TEAM_LEAGUE_MAP[opp_lower]
                    player_league_id = d.get("statistics", [{}])[0].get("league", {}).get("id")
                    if player_league_id == opp_league:
                        league_match = True
            if name_match:
                candidates.append((d, team_match, league_match))
        if candidates:
            # Priority: team match > league match > first result
            team_matched = [c for c in candidates if c[1]]
            if team_matched:
                return team_matched[0][0]
            league_matched = [c for c in candidates if c[2]]
            if league_matched:
                return league_matched[0][0]
            # If we have a team hint but NO candidate matched the team,
            # don't return a random wrong player — let the caller handle it
            if team_hint:
                return None
            return candidates[0][0]
        return None

    resolved = None
    found_league_id = None
    found_league_name = None

    for variant in search_variants:
        if resolved:
            break
        if len(variant) < 3:
            continue
        for try_league in leagues_to_try:
            if resolved:
                break
            for season in [CURRENT_SEASON + 1, CURRENT_SEASON]:
                try:
                    data = await api_football_request("players", {"search": variant, "league": try_league, "season": season})
                    if not data:
                        continue
                    best = pick_best(data, player_name, player_team_hint, opponent_hint)
                    if not best and (not player_team_hint or is_international):
                        best = data[0]
                    if best:
                        resolved = {
                            "playerId": best["player"]["id"],
                            "playerName": best["player"]["name"],
                            "photo": "",
                            "teamId": nat_team_id if is_international and nat_team_id else best.get("statistics", [{}])[0].get("team", {}).get("id"),
                            "teamName": (original_team_name or player_team_hint.title()) if is_international else best.get("statistics", [{}])[0].get("team", {}).get("name", ""),
                        }
                        if not is_international:
                            actual_league = best.get("statistics", [{}])[0].get("league", {})
                            if actual_league.get("id"):
                                found_league_id = actual_league["id"]
                                found_league_name = actual_league.get("name")
                        break
                except Exception:
                    continue

    # Broader search without league filter
    if not resolved:
        for variant in search_variants:
            if resolved:
                break
            if len(variant) < 3:
                continue
            for season in [CURRENT_SEASON + 1, CURRENT_SEASON]:
                try:
                    data = await api_football_request("players", {"search": variant, "season": season})
                    if not data:
                        continue
                    best = pick_best(data, player_name, player_team_hint, opponent_hint)
                    if not best and (not player_team_hint or is_international):
                        best = data[0]
                    if best:
                        resolved = {
                            "playerId": best["player"]["id"],
                            "playerName": best["player"]["name"],
                            "photo": "",
                            "teamId": nat_team_id if is_international and nat_team_id else best.get("statistics", [{}])[0].get("team", {}).get("id"),
                            "teamName": (original_team_name or player_team_hint.title()) if is_international else best.get("statistics", [{}])[0].get("team", {}).get("name", ""),
                        }
                        if not is_international:
                            actual_league = best.get("statistics", [{}])[0].get("league", {})
                            if actual_league.get("id"):
                                found_league_id = actual_league["id"]
                                found_league_name = actual_league.get("name")
                        break
                except Exception:
                    continue

    return resolved, found_league_id, found_league_name


async def _resolve_opponent(opponent_name: str, is_international: bool, league_id: int) -> dict:
    """Resolve opponent team: cache first, API-Sports fallback."""
    if not opponent_name:
        return None

    opp_lower = strip_accents(opponent_name.lower().strip())

    # 1. For international matches, try national team cache
    if is_international:
        opp_nat_id, opp_canonical = await get_national_team_id(opp_lower)
        if opp_nat_id:
            return {"teamId": opp_nat_id, "teamName": opp_canonical or opponent_name.strip()}

    # 2. Try club team cache
    club_id, club_name = await get_team_by_name(strip_accents(opponent_name), league_id if not is_international else None)
    if club_id:
        return {"teamId": club_id, "teamName": club_name}

    # 3. API-Sports fallback — search and prefer teams from same league
    opp_searches = [strip_accents(opponent_name.strip())]
    # Expand common abbreviations (e.g., "Atletico MG" → "Atletico Mineiro")
    TEAM_ABBREV_MAP = {
        "mg": "mineiro", "sp": "sao paulo", "pr": "paranaense",
        "rj": "rio de janeiro", "go": "goianiense", "ba": "bahia",
    }
    opp_words = opp_lower.split()
    if len(opp_words) >= 2:
        last_word = opp_words[-1]
        if last_word in TEAM_ABBREV_MAP:
            expanded = " ".join(opp_words[:-1]) + " " + TEAM_ABBREV_MAP[last_word]
            opp_searches.append(expanded)
    variant_th = opp_searches[0].replace("th", "t").replace("Th", "T")
    if variant_th != opp_searches[0]:
        opp_searches.append(variant_th)

    first_word = strip_accents(opp_lower.split()[0])
    first_word_variant = first_word.replace("th", "t")

    # Map league IDs to their country for disambiguation
    LEAGUE_COUNTRY = {
        39: "england", 61: "france", 71: "brazil", 78: "germany",
        135: "italy", 140: "spain", 188: "australia", 253: "usa",
        254: "usa", 262: "mexico", 128: "argentina", 169: "china",
        242: "ecuador",
        292: "south-korea", 307: "saudi-arabia", 332: "japan",
    }
    target_country = LEAGUE_COUNTRY.get(league_id, "")

    for opp_query in opp_searches:
        try:
            teams_data = await api_football_request("teams", {"search": opp_query})
            if not teams_data:
                continue

            # Filter valid matches (name match, not youth/women)
            valid = []
            for t in teams_data[:15]:
                tname = t.get("team", {}).get("name", "")
                tname_lower = strip_accents(tname.lower())
                tcountry = (t.get("team", {}).get("country") or "").lower()
                is_youth = any(s in tname_lower for s in ["u20", "u23", "u21", "u19", "u18", "u17", " ii", " b "])
                is_women = tname_lower.endswith(" w") or "women" in tname_lower
                name_match = first_word in tname_lower or first_word_variant in tname_lower
                if name_match and not is_youth and not is_women:
                    country_match = target_country and target_country in tcountry
                    valid.append({"teamId": t["team"]["id"], "teamName": tname, "country_match": country_match})

            if valid:
                # Prefer teams from the same country as the league
                country_matched = [v for v in valid if v["country_match"]]
                if country_matched:
                    print(f"[OPP RESOLVE] Country-matched: {country_matched[0]['teamName']} (league {league_id})")
                    return {"teamId": country_matched[0]["teamId"], "teamName": country_matched[0]["teamName"]}
                # Otherwise return first valid match
                return {"teamId": valid[0]["teamId"], "teamName": valid[0]["teamName"]}

            # If no filtered match, use first result
            return {"teamId": teams_data[0]["team"]["id"], "teamName": teams_data[0]["team"]["name"]}
        except Exception:
            continue

    return None


def _build_soccer_scan_prompt(leagues_list: str) -> str:
    return f"""Analyze this screenshot of a player prop card.

LAYOUT GUIDE — SINGLE PLAYER:
- The player's FIRST NAME is on the top line, LAST NAME is on the second line (larger/bolder text)
- Below the name: "SOCCER • [Team Name] • [Position]"
- Below that: "vs [Opponent]" or "@ [Opponent]" with date/time
- The prop line number (e.g., 48.5) is shown prominently with the stat type below it (e.g., "Passes Attempted")
- "Less" and "More" buttons are just selection options — IGNORE them.

LAYOUT GUIDE — COMBO PROPS (TWO players combined):
- The card shows TWO player names joined by " + "
- Teams shown as "Team1/Team2"
- The stat label includes "(Combo)"
- The line number is the COMBINED total for both players
- The two players are from OPPOSING teams in the same match

CRITICAL RULES:
- Read player names EXACTLY as shown. Do NOT confuse with opponent/team names.
- If you see " + " between two names, this is a COMBO prop — set isCombo to true.
- If "(Combo)" appears in the stat type, this is a COMBO prop.
- IGNORE any "Less"/"More" buttons.

Extract for EACH prop entry:
1. playerName — Full name(s) as displayed. For combos: "Player A + Player B"
2. propType — Map to: goals, assists, shots_assisted, pass_attempts, shots, shots_on_target, tackles, key_passes, saves, interceptions, blocks, dribbles, dribbles_success, fouls_drawn, fouls_committed, crosses, clearances, duels_won, yellow_cards
3. line — The numerical line (e.g., 48.5, 6, 5.5)
4. opponentName — The opposing team (for single props). For combos: null
5. league — Best guess league name
6. leagueId — Match to one of: {leagues_list}
7. playerTeam — The player's team
8. isCombo — true if combo, false otherwise
9. players — ONLY for combos: array of 2 objects with "name" and "team"

PROP TYPE MAPPING:
- "Goals" / "Anytime Goalscorer" → goals
- "Assists" / "Goal Assists" → assists
- "Shots Assisted" / "Shot Assists" → shots_assisted
- "Passes Attempted" / "Pass Attempts" / "Passes" → pass_attempts
- "Shots" / "Shots Taken" / "Total Shots" → shots
- "Shots on Target" / "SOT" → shots_on_target
- "Tackles" → tackles
- "Key Passes" / "Chances Created" → key_passes
- "Saves" / "Goalkeeper Saves" / "Goalie Saves" → saves
- "Interceptions" → interceptions
- "Blocks" → blocks
- "Dribble Attempts" / "Dribbles" → dribbles
- "Successful Dribbles" / "Dribbles Completed" → dribbles_success
- "Fouls Drawn" → fouls_drawn
- "Fouls Committed" / "Fouls" → fouls_committed
- "Crosses" / "Cross Attempts" → crosses
- "Clearances" → clearances
- "Duels Won" / "Duels" → duels_won
- "Yellow Cards" / "Cards" → yellow_cards

CRITICAL: "Shots Assisted" is shots_assisted (NOT shots or assists). "Goals" is goals (NOT shots_on_target).

RETURN FORMAT (JSON array):
For SINGLE: {{"playerName":"...","propType":"...","line":0.0,"opponentName":"...","playerTeam":"...","venue":"home or away","league":"...","leagueId":0,"isCombo":false}}
For COMBO: {{"playerName":"Player A + Player B","propType":"...","line":0.0,"opponentName":null,"playerTeam":"Team1/Team2","venue":null,"league":"...","leagueId":0,"isCombo":true,"players":[{{"name":"Player A","team":"Team1"}},{{"name":"Player B","team":"Team2"}}]}}

VENUE: "@ [Team]" → away, "vs [Team]" → home
If unknown, use null. Return JSON array."""


def _build_basketball_scan_prompt() -> str:
    return """Analyze this screenshot of an NBA player prop card.

LAYOUT GUIDE:
- Player's name (first + last), usually first name smaller above last name
- Below name: "BASKETBALL • [Team Name] • [Position]"
- Below that: "vs [Opponent]" or "@ [Opponent]" with date/time
- The prop line number is shown prominently with the stat type below it
- "Less" and "More" buttons are selection options — IGNORE them.

CRITICAL RULES:
- Read player names EXACTLY as shown
- IGNORE "Less"/"More" buttons
- This is BASKETBALL / NBA only

Extract:
1. playerName — Full name as displayed
2. propType — Map to one of: points, rebounds, assists, pts_reb_ast, pts_reb, pts_ast, reb_ast, blk_stl, steals, blocks, turnovers, three_pointers, fgm, ftm, fga, fta, tpa
3. line — The numerical line (e.g., 24.5, 7.5, 5.5)
4. opponentName — The opposing team
5. playerTeam — The player's team
6. venue — "home" or "away" ("@ Team" = away, "vs Team" = home)

PROP TYPE MAPPING:
- "Points" / "Pts" / "PTS" → points
- "Rebounds" / "Reb" / "Total Rebounds" → rebounds
- "Assists" / "Ast" / "AST" → assists
- "Pts+Reb+Ast" / "PRA" / "Points+Rebounds+Assists" → pts_reb_ast
- "Rebs+Asts" / "Reb+Ast" / "Rebounds+Assists" → reb_ast
- "Pts+Reb" / "Points+Rebounds" → pts_reb
- "Pts+Ast" / "Points+Assists" → pts_ast
- "Blks+Stls" / "Blocks+Steals" → blk_stl
- "Steals" / "Stl" → steals
- "Blocks" / "Blk" → blocks
- "Turnovers" / "TO" / "TOV" → turnovers
- "3-Point FG" / "3PM" / "Threes" / "Three Pointers Made" / "3-Pointers Made" / "3PTM" → three_pointers
- "FG Made" / "FGM" / "Field Goals Made" → fgm
- "FT Made" / "FTM" / "Free Throws Made" → ftm
- "FG Attempted" / "FGA" → fga
- "FT Attempted" / "FTA" → fta
- "3PT Attempts" / "3PA" → tpa

CRITICAL: "Rebs+Asts" is reb_ast (NOT pts_reb_ast). Only include Points in the combo if "Pts" appears in the label.

RETURN FORMAT (JSON array):
[{"playerName":"...","propType":"...","line":0.0,"opponentName":"...","playerTeam":"...","venue":"home or away","sport":"basketball"}]

If unknown, use null. Return JSON array."""


async def _resolve_basketball_picks(extracted: list) -> dict:
    """Resolve basketball picks: find team IDs via basketball API."""
    results = []
    for entry in extracted:
        player_name = entry.get("playerName")
        if not player_name:
            continue

        # Normalize prop type
        raw_prop = (entry.get("propType") or "").lower().strip()
        prop_type = BASKETBALL_PROP_ALIASES.get(raw_prop, raw_prop)
        if prop_type not in VALID_BASKETBALL_PROPS:
            prop_type = "points"

        line_val = entry.get("line") or 0
        venue = (entry.get("venue") or "home").lower().strip()
        if venue not in ("home", "away"):
            venue = "home"

        team_hint = (entry.get("playerTeam") or "").strip()
        opp_hint = (entry.get("opponentName") or "").strip()

        # Track league info from resolved teams
        league_id = None
        league_name = None

        # Resolve team via CACHE first, then live API fallback
        resolved_team = None
        if team_hint:
            cached = await get_bball_team_by_name(team_hint)
            if cached:
                resolved_team = {"teamId": cached["teamId"], "teamName": cached.get("name", team_hint)}
                league_id = cached.get("leagueId")
                league_name = cached.get("leagueName")
            else:
                teams = await search_bball_teams(team_hint)
                if teams:
                    best = teams[0]
                    resolved_team = {"teamId": best["teamId"], "teamName": best.get("name", team_hint)}
                    league_id = best.get("leagueId")
                    league_name = best.get("leagueName")
                else:
                    # Live API fallback
                    live_teams = await search_basketball_teams_live(team_hint)
                    if live_teams:
                        best = live_teams[0]
                        resolved_team = {"teamId": best["id"], "teamName": best.get("name", team_hint)}

        # Resolve opponent via CACHE first — MUST filter by same league as player's team
        resolved_opp = None
        if opp_hint:
            # First try: same league as player's team (prevents NBA vs WNBA cross-match)
            if league_id:
                cached = await get_bball_team_by_name(opp_hint, league_id=league_id)
            else:
                cached = await get_bball_team_by_name(opp_hint)
            if cached:
                resolved_opp = {"teamId": cached["teamId"], "teamName": cached.get("name", opp_hint)}
                if not league_id:
                    league_id = cached.get("leagueId")
                    league_name = cached.get("leagueName")
            else:
                opps = await search_bball_teams(opp_hint, league_id=league_id)
                if not opps and league_id:
                    # Fallback: broader search, but REJECT cross-league matches
                    all_opps = await search_bball_teams(opp_hint)
                    opps = [t for t in all_opps if t.get("leagueId") == league_id] if league_id else all_opps
                if opps:
                    best = opps[0]
                    resolved_opp = {"teamId": best["teamId"], "teamName": best.get("name", opp_hint)}
                    if not league_id:
                        league_id = best.get("leagueId")
                        league_name = best.get("leagueName")
                else:
                    live_opps = await search_basketball_teams_live(opp_hint)
                    if live_opps:
                        best = live_opps[0]
                        resolved_opp = {"teamId": best["id"], "teamName": best.get("name", opp_hint)}

        results.append({
            "extracted": {
                "playerName": player_name,
                "propType": prop_type,
                "line": line_val,
                "venue": venue,
                "opponentName": opp_hint,
                "playerTeam": team_hint,
                "sport": "basketball",
                "league": league_name or "NBA",
                "leagueId": league_id or 12,
                "isCombo": False,
            },
            "resolved": resolved_team,
            "resolvedOpponent": resolved_opp,
            "sport": "basketball",
        })

    return {"picks": results}



@router.post("/scan-prop")
async def scan_prop(req: ScanPropRequest):
    """Use AI vision to extract player prop data from a screenshot."""
    try:
        is_basketball = req.sport == "basketball"

        # ── Step 1: Vision AI extraction ──
        chat = LlmChat(
            api_key=EMERGENT_LLM_KEY,
            session_id=f"scan-{uuid.uuid4().hex[:8]}",
            system_message="You are an expert at reading player prop screenshots. Extract structured data precisely."
        ).with_model("openai", "gpt-4o")

        image_content = ImageContent(image_base64=req.image_base64)

        if is_basketball:
            prompt = _build_basketball_scan_prompt()
        else:
            leagues_list = ", ".join([f"{lg['name']} (ID:{lg['id']})" for lg in SUPPORTED_LEAGUES])
            prompt = _build_soccer_scan_prompt(leagues_list)

        msg = UserMessage(text=prompt, file_contents=[image_content])
        response = await chat.send_message(msg)
        response_text = response.strip()

        # Clean markdown fences
        if response_text.startswith("```"):
            lines = response_text.split("\n")
            lines = [ln for ln in lines if not ln.strip().startswith("```")]
            response_text = "\n".join(lines)

        extracted = json.loads(response_text)
        if not isinstance(extracted, list):
            extracted = [extracted]

        # ── Step 2: Route based on sport ──
        if is_basketball:
            return await _resolve_basketball_picks(extracted)

        # ── Step 2 (Soccer): Resolve each extracted entry ──
        results = []
        for entry in extracted:
            player_name = entry.get("playerName")
            if not player_name:
                continue

            # Normalize prop type
            raw_prop = (entry.get("propType") or "").lower().strip()
            prop_type = PROP_TYPE_ALIASES.get(raw_prop, raw_prop)
            if prop_type not in VALID_SOCCER_PROPS:
                prop_type = "pass_attempts"

            line_val = entry.get("line") or 0
            is_combo = entry.get("isCombo", False)
            ai_league_id = entry.get("leagueId")

            # ════════════════════════════════════
            #  COMBO PROP: two players combined
            # ════════════════════════════════════
            if is_combo and entry.get("players") and len(entry["players"]) >= 2:
                combo_players = entry["players"][:2]
                team1_name = (combo_players[0].get("team") or "").strip()
                team2_name = (combo_players[1].get("team") or "").strip()
                league_id = await _infer_league_id(team1_name, team2_name, ai_league_id)
                league_name = entry.get("league")
                if not league_name:
                    for sl in SUPPORTED_LEAGUES:
                        if sl["id"] == league_id:
                            league_name = sl["name"]
                            break

                is_international = league_id in INTERNATIONAL_LEAGUES

                resolved_players = []
                for cp in combo_players:
                    cp_name = (cp.get("name") or "").strip()
                    cp_team = (cp.get("team") or "").strip()
                    cp_team_lower = cp_team.lower().strip()

                    if not cp_name:
                        resolved_players.append(None)
                        continue

                    # Resolve the player's team
                    team_id = None
                    team_canonical = cp_team
                    if is_international:
                        nat_id, nat_can = await get_national_team_id(cp_team_lower)
                        if nat_id:
                            team_id = nat_id
                            team_canonical = nat_can or cp_team
                    else:
                        club_id, club_name = await get_team_by_name(cp_team)
                        if club_id:
                            team_id = club_id
                            team_canonical = club_name

                    # Resolve the player from cache
                    cached = await _resolve_player_via_cache(cp_name, team_id)
                    if cached:
                        resolved_players.append({
                            "playerId": cached["playerId"],
                            "playerName": cached["name"],
                            "photo": "",
                            "teamId": team_id or cached.get("teamId"),
                            "teamName": team_canonical or cached.get("teamName", cp_team),
                        })
                        print(f"[SCAN] Combo cache HIT: {cp_name} -> {cached['name']} (ID {cached['playerId']})")
                    else:
                        # API-Sports fallback for this combo player
                        print(f"[SCAN] Combo cache MISS: {cp_name}, falling back to API-Sports...")
                        leagues_to_try = NATION_TO_LEAGUES.get(cp_team_lower, TOP_5_LEAGUES) if is_international else [league_id]
                        api_p, _, _ = await _resolve_player_via_api(
                            cp_name, cp_team_lower, leagues_to_try,
                            is_international, team_id if is_international else None, cp_team
                        )
                        resolved_players.append(api_p)

                results.append({
                    "extracted": {
                        "playerName": player_name,
                        "propType": prop_type,
                        "line": line_val,
                        "venue": None,
                        "opponentName": None,
                        "playerTeam": entry.get("playerTeam"),
                        "league": league_name or entry.get("league"),
                        "leagueId": league_id,
                        "isCombo": True,
                        "players": [
                            {"name": combo_players[0].get("name", ""), "team": team1_name},
                            {"name": combo_players[1].get("name", ""), "team": team2_name},
                        ],
                    },
                    "resolved": None,
                    "resolvedPlayers": resolved_players,
                    "resolvedOpponent": None,
                })
                continue

            # ════════════════════════════════════
            #  SINGLE PLAYER PROP (existing logic)
            # ════════════════════════════════════
            player_team_hint = (entry.get("playerTeam") or "").lower().strip()
            opponent_hint = (entry.get("opponentName") or "").strip()
            league_id = await _infer_league_id(entry.get("playerTeam"), opponent_hint, ai_league_id)
            league_name = entry.get("league")
            if not league_name:
                for sl in SUPPORTED_LEAGUES:
                    if sl["id"] == league_id:
                        league_name = sl["name"]
                        break

            venue = (entry.get("venue") or "home").lower().strip()
            if venue not in ("home", "away"):
                venue = "home"

            is_international = league_id in INTERNATIONAL_LEAGUES
            original_league_id = league_id
            original_league_name = league_name
            original_team_name = (entry.get("playerTeam") or "").strip()

            # Resolve national team ID for international matches
            nat_team_id = None
            nat_team_canonical = None
            if is_international and player_team_hint:
                nat_team_id, nat_team_canonical = await get_national_team_id(player_team_hint)
                if not nat_team_id:
                    try:
                        teams_data = await api_football_request("teams", {"search": player_team_hint.title()})
                        if teams_data:
                            for t in teams_data[:5]:
                                tname = t.get("team", {}).get("name", "").lower()
                                if t.get("team", {}).get("national") and (player_team_hint in tname or tname in player_team_hint):
                                    nat_team_id = t["team"]["id"]
                                    nat_team_canonical = t["team"]["name"]
                                    break
                    except Exception:
                        pass

            # PRIMARY: Resolve player via MongoDB cache
            resolved_player = None
            cache_team_id = None
            if not is_international and player_team_hint:
                club_id, _ = await get_team_by_name(player_team_hint)
                if club_id:
                    cache_team_id = club_id

            cached_player = await _resolve_player_via_cache(player_name, cache_team_id)
            if cached_player:
                if is_international and nat_team_id:
                    resolved_player = {
                        "playerId": cached_player["playerId"],
                        "playerName": cached_player["name"],
                        "photo": "",
                        "teamId": nat_team_id,
                        "teamName": original_team_name or nat_team_canonical or player_team_hint.title(),
                    }
                else:
                    resolved_player = {
                        "playerId": cached_player["playerId"],
                        "playerName": cached_player["name"],
                        "photo": "",
                        "teamId": cached_player.get("teamId"),
                        "teamName": cached_player.get("teamName", player_team_hint.title()),
                    }
                    if cached_player.get("leagueId") and not is_international:
                        # Only use cached player's league if team name wasn't explicitly mapped
                        team_in_map = player_team_hint in TEAM_LEAGUE_MAP
                        opp_in_map = (opponent_hint.lower().strip() in TEAM_LEAGUE_MAP) if opponent_hint else False
                        if not team_in_map and not opp_in_map:
                            league_id = cached_player["leagueId"]
                            for sl in SUPPORTED_LEAGUES:
                                if sl["id"] == league_id:
                                    league_name = sl["name"]
                                    break
                print(f"[SCAN] Cache HIT: {player_name} -> {resolved_player['playerName']} (ID {resolved_player['playerId']})")

            # FALLBACK: API-Sports search
            if not resolved_player:
                print(f"[SCAN] Cache MISS for '{player_name}', falling back to API-Sports...")
                leagues_to_try = NATION_TO_LEAGUES.get(player_team_hint, TOP_5_LEAGUES) if is_international else [league_id]
                api_player, api_league_id, api_league_name = await _resolve_player_via_api(
                    player_name, player_team_hint, leagues_to_try,
                    is_international, nat_team_id, original_team_name,
                    opponent_hint=opponent_hint
                )
                if api_player:
                    resolved_player = api_player
                    if api_league_id and not is_international:
                        league_id = api_league_id
                    if api_league_name and not is_international:
                        league_name = api_league_name

            # For international matches, restore original context
            if is_international and original_league_id:
                league_id = original_league_id
                league_name = original_league_name

            # Resolve opponent
            resolved_opponent = None
            if opponent_hint and resolved_player:
                resolved_opponent = await _resolve_opponent(opponent_hint, is_international, league_id)

            # Look up cached position/role for display on scan card
            player_pos_info = {}
            if resolved_player and resolved_player.get("playerId"):
                cached_pos = await db.player_positions.find_one(
                    {"playerId": resolved_player["playerId"]},
                    {"_id": 0, "specificPosition": 1, "role": 1}
                )
                if cached_pos and cached_pos.get("specificPosition"):
                    player_pos_info = {
                        "position": cached_pos["specificPosition"],
                        "role": cached_pos.get("role", ""),
                    }

            results.append({
                "extracted": {
                    "playerName": player_name,
                    "propType": prop_type,
                    "line": line_val,
                    "venue": venue,
                    "opponentName": entry.get("opponentName"),
                    "playerTeam": entry.get("playerTeam"),
                    "league": league_name or entry.get("league"),
                    "leagueId": league_id,
                    "isCombo": False,
                    "position": player_pos_info.get("position", ""),
                    "role": player_pos_info.get("role", ""),
                },
                "resolved": resolved_player,
                "resolvedOpponent": resolved_opponent,
            })

        return {"picks": results}

    except json.JSONDecodeError:
        raise HTTPException(status_code=422, detail="AI could not parse the image. Try a clearer screenshot.")
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Scan failed: {str(e)}")


# ═══════════════════════════════════════════════════
#  RE-RESOLVE: Let users correct scan results
# ═══════════════════════════════════════════════════
from pydantic import BaseModel as PydanticBaseModel
from typing import Optional

class ReResolveRequest(PydanticBaseModel):
    playerName: str
    playerTeam: str
    opponentName: str = ""
    sport: str = "soccer"

@router.post("/re-resolve")
async def re_resolve(req: ReResolveRequest):
    """Re-resolve a player/team/opponent after user correction."""
    try:
        player_name = req.playerName.strip()
        player_team = req.playerTeam.strip()
        opponent_hint = req.opponentName.strip()
        sport = req.sport.lower()

        if not player_name or not player_team:
            raise HTTPException(status_code=400, detail="Player name and team are required")

        if sport == "basketball":
            # Basketball re-resolution
            team_data = await get_bball_team_by_name(player_team)
            resolved_player = None
            resolved_opponent = None

            if team_data:
                team_id = team_data.get("teamId")
                from basketball_cache import get_bball_player_by_name
                cached = await get_bball_player_by_name(player_name, team_id)
                if cached:
                    resolved_player = {
                        "playerId": cached["playerId"],
                        "playerName": cached["name"],
                        "photo": "",
                        "teamId": team_id,
                        "teamName": team_data.get("name", player_team),
                    }

            if opponent_hint:
                opp_data = await get_bball_team_by_name(opponent_hint)
                if opp_data:
                    resolved_opponent = {
                        "teamId": opp_data["teamId"],
                        "teamName": opp_data.get("name", opponent_hint),
                    }

            return {
                "resolved": resolved_player,
                "resolvedOpponent": resolved_opponent,
                "leagueId": None,
                "leagueName": "NBA",
            }

        # Soccer re-resolution
        player_team_lower = player_team.lower().strip()
        league_id = await _infer_league_id(player_team, opponent_hint, None)
        # Track if league was explicitly matched via TEAM_LEAGUE_MAP
        team_league_explicit = (player_team_lower in TEAM_LEAGUE_MAP) or (opponent_hint.lower().strip() in TEAM_LEAGUE_MAP if opponent_hint else False)
        league_name = None
        for sl in SUPPORTED_LEAGUES:
            if sl["id"] == league_id:
                league_name = sl["name"]
                break

        # Resolve player
        resolved_player = None
        cache_team_id = None
        club_id, _ = await get_team_by_name(player_team_lower)
        if club_id:
            cache_team_id = club_id

        cached_player = await _resolve_player_via_cache(player_name, cache_team_id)
        if cached_player:
            resolved_player = {
                "playerId": cached_player["playerId"],
                "playerName": cached_player["name"],
                "photo": "",
                "teamId": cached_player.get("teamId"),
                "teamName": cached_player.get("teamName", player_team),
            }
            # Only use cached player's leagueId if we couldn't explicitly infer from team name
            if cached_player.get("leagueId") and not team_league_explicit:
                league_id = cached_player["leagueId"]
                for sl in SUPPORTED_LEAGUES:
                    if sl["id"] == league_id:
                        league_name = sl["name"]
                        break

        if not resolved_player:
            leagues_to_try = [league_id]
            api_player, api_lid, api_lname = await _resolve_player_via_api(
                player_name, player_team_lower, leagues_to_try,
                False, None, player_team,
                opponent_hint=opponent_hint
            )
            if api_player:
                resolved_player = api_player
                if api_lid:
                    league_id = api_lid
                if api_lname:
                    league_name = api_lname

        # Resolve opponent
        resolved_opponent = None
        if opponent_hint:
            resolved_opponent = await _resolve_opponent(opponent_hint, False, league_id)

        # Position info
        position_info = {}
        if resolved_player and resolved_player.get("playerId"):
            cached_pos = await db.player_positions.find_one(
                {"playerId": resolved_player["playerId"]},
                {"_id": 0, "specificPosition": 1, "role": 1}
            )
            if cached_pos and cached_pos.get("specificPosition"):
                position_info = {
                    "position": cached_pos["specificPosition"],
                    "role": cached_pos.get("role", ""),
                }

        return {
            "resolved": resolved_player,
            "resolvedOpponent": resolved_opponent,
            "leagueId": league_id,
            "leagueName": league_name,
            "position": position_info,
        }

    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Re-resolve failed: {str(e)}")
