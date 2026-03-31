import json
import uuid
import traceback
from fastapi import APIRouter, HTTPException
from emergentintegrations.llm.chat import LlmChat, UserMessage, ImageContent

from config import (
    EMERGENT_LLM_KEY, SUPPORTED_LEAGUES, CURRENT_SEASON,
    PROP_TYPE_ALIASES, INTERNATIONAL_LEAGUES, NATION_TO_LEAGUES,
    TOP_5_LEAGUES,
)
from models import ScanPropRequest
from utils import api_football_request, strip_accents
from cache import get_national_team_id, get_player_by_name, get_team_by_name, get_team_info
from basketball_utils import search_nba_teams as search_basketball_teams

router = APIRouter(prefix="/api", tags=["scan"])

# Valid prop types for normalization
VALID_SOCCER_PROPS = {
    "pass_attempts", "shots", "shots_on_target", "tackles", "key_passes",
    "saves", "interceptions", "blocks", "dribbles", "fouls_drawn",
}

VALID_BASKETBALL_PROPS = {
    "points", "rebounds", "assists", "pts_reb_ast", "three_pointers",
    "fgm", "ftm", "fga", "fta", "tpa",
}

BASKETBALL_PROP_ALIASES = {
    "point": "points", "pts": "points", "pt": "points",
    "rebound": "rebounds", "reb": "rebounds", "total rebounds": "rebounds",
    "assist": "assists", "ast": "assists",
    "pts+reb+ast": "pts_reb_ast", "pra": "pts_reb_ast", "points+rebounds+assists": "pts_reb_ast",
    "pts + reb + ast": "pts_reb_ast", "combo": "pts_reb_ast",
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
    "atletico mineiro": 71, "atletico paranaense": 71, "athletico": 71, "athletico pr": 71,
    "gremio": 71, "internacional": 71, "cruzeiro": 71, "fluminense": 71, "santos": 71,
    "vasco": 71, "bahia": 71, "fortaleza": 71, "bragantino": 71, "juventude": 71,
    "cuiaba": 71, "goias": 71, "vitoria": 71, "sport": 71, "ceara": 71,
    "arsenal": 39, "chelsea": 39, "liverpool": 39, "manchester city": 39, "man city": 39,
    "manchester united": 39, "man united": 39, "tottenham": 39, "spurs": 39,
    "newcastle": 39, "aston villa": 39, "west ham": 39, "brighton": 39, "wolves": 39,
    "crystal palace": 39, "everton": 39, "fulham": 39, "brentford": 39, "bournemouth": 39,
    "nottingham forest": 39, "leicester": 39, "ipswich": 39, "southampton": 39,
    "real madrid": 140, "barcelona": 140, "atletico madrid": 140, "athletic bilbao": 140,
    "real sociedad": 140, "betis": 140, "villarreal": 140, "sevilla": 140, "girona": 140,
    "valencia": 140, "getafe": 140, "osasuna": 140, "celta vigo": 140, "mallorca": 140,
    "rayo vallecano": 140, "alaves": 140, "las palmas": 140, "cadiz": 140,
    "bayern munich": 78, "bayern": 78, "dortmund": 78, "borussia dortmund": 78,
    "leverkusen": 78, "bayer leverkusen": 78, "rb leipzig": 78, "leipzig": 78,
    "stuttgart": 78, "frankfurt": 78, "wolfsburg": 78, "freiburg": 78,
    "inter milan": 135, "inter": 135, "ac milan": 135, "milan": 135, "juventus": 135,
    "napoli": 135, "roma": 135, "lazio": 135, "atalanta": 135, "fiorentina": 135,
    "bologna": 135, "torino": 135, "monza": 135, "genoa": 135, "cagliari": 135,
    "psg": 61, "paris saint-germain": 61, "marseille": 61, "lyon": 61, "monaco": 61,
    "lille": 61, "lens": 61, "nice": 61, "rennes": 61, "strasbourg": 61,
    "la galaxy": 253, "lafc": 253, "inter miami": 253, "atlanta united": 253,
    "new york city fc": 253, "nycfc": 253, "new york red bulls": 253, "seattle sounders": 253,
    "portland timbers": 253, "columbus crew": 253, "fc cincinnati": 253, "nashville sc": 253,
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
    """Infer league ID: cache first, then hardcoded map, then AI guess."""
    if ai_league_id and ai_league_id != 39:
        return ai_league_id

    # Try cache for both team and opponent
    for name in [team_name, opponent_name]:
        if not name:
            continue
        info = await get_team_info(name)
        if info and info.get("leagueId"):
            return info["leagueId"]

    # Hardcoded map fallback
    for name in [team_name, opponent_name]:
        if not name:
            continue
        name_lower = name.lower().strip()
        if name_lower in TEAM_LEAGUE_MAP:
            return TEAM_LEAGUE_MAP[name_lower]
        for key, lid in TEAM_LEAGUE_MAP.items():
            if key in name_lower or name_lower in key:
                return lid

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
                                   nat_team_id: int, original_team_name: str) -> tuple:
    """Fallback: search API-Sports for a player. Returns (resolved_player, league_id, league_name) or (None, None, None)."""
    search_clean = strip_accents(player_name.strip())
    search_variants = [search_clean]
    name_parts = search_clean.split()
    if len(name_parts) > 1:
        last = name_parts[-1]
        if last != search_clean:
            search_variants.append(last)

    def pick_best(data_list, query, team_hint):
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
            name_match = query_lower in pname or pname in query_lower or last_name in pname
            team_match = any(th in team_name or team_name in th for th in team_hints) if team_hints else False
            if name_match:
                candidates.append((d, team_match))
        if candidates:
            team_matched = [c for c in candidates if c[1]]
            if team_matched:
                return team_matched[0][0]
            if not team_hint:
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
                    best = pick_best(data, player_name, player_team_hint)
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
                    best = pick_best(data, player_name, player_team_hint)
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

    opp_lower = opponent_name.lower().strip()

    # 1. For international matches, try national team cache
    if is_international:
        opp_nat_id, opp_canonical = await get_national_team_id(opp_lower)
        if opp_nat_id:
            return {"teamId": opp_nat_id, "teamName": opp_canonical or opponent_name.strip()}

    # 2. Try club team cache
    club_id, club_name = await get_team_by_name(opponent_name, league_id if not is_international else None)
    if club_id:
        return {"teamId": club_id, "teamName": club_name}

    # 3. API-Sports fallback with common spelling variants
    opp_searches = [opponent_name.strip()]
    variant_th = opponent_name.strip().replace("th", "t").replace("Th", "T")
    if variant_th != opponent_name.strip():
        opp_searches.append(variant_th)

    first_word = opp_lower.split()[0]
    first_word_variant = first_word.replace("th", "t")

    for opp_query in opp_searches:
        try:
            teams_data = await api_football_request("teams", {"search": opp_query})
            if teams_data:
                for t in teams_data[:15]:
                    tname = t.get("team", {}).get("name", "")
                    tname_lower = tname.lower()
                    is_youth = any(s in tname_lower for s in ["u20", "u23", "u21", "u19", "u18", "u17", " ii", " b "])
                    is_women = tname_lower.endswith(" w")
                    name_match = first_word in tname_lower or first_word_variant in tname_lower
                    if name_match and not is_youth and not is_women:
                        return {"teamId": t["team"]["id"], "teamName": tname}
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
2. propType — Map to: pass_attempts, shots, shots_on_target, tackles, key_passes, saves, interceptions, blocks, dribbles, fouls_drawn
3. line — The numerical line (e.g., 48.5, 6, 5.5)
4. opponentName — The opposing team (for single props). For combos: null
5. league — Best guess league name
6. leagueId — Match to one of: {leagues_list}
7. playerTeam — The player's team
8. isCombo — true if combo, false otherwise
9. players — ONLY for combos: array of 2 objects with "name" and "team"

PROP TYPE MAPPING:
- "Passes Attempted" / "Pass Attempts" / "Passes" → pass_attempts
- "Shots" / "Shots Taken" → shots
- "Shots on Target" / "SOT" → shots_on_target
- "Tackles" → tackles
- "Key Passes" / "Assists" → key_passes
- "Saves" / "Goalkeeper Saves" / "Goalie Saves" → saves
- "Interceptions" → interceptions
- "Blocks" → blocks
- "Dribble Attempts" / "Dribbles" → dribbles
- "Fouls Drawn" → fouls_drawn

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
2. propType — Map to one of: points, rebounds, assists, pts_reb_ast, three_pointers, fgm, ftm, fga, fta, tpa
3. line — The numerical line (e.g., 24.5, 7.5, 5.5)
4. opponentName — The opposing team
5. playerTeam — The player's team
6. venue — "home" or "away" ("@ Team" = away, "vs Team" = home)

PROP TYPE MAPPING:
- "Points" / "Pts" / "PTS" → points
- "Rebounds" / "Reb" / "Total Rebounds" → rebounds
- "Assists" / "Ast" / "AST" → assists
- "Pts+Reb+Ast" / "PRA" / "Points+Rebounds+Assists" → pts_reb_ast
- "3-Point FG" / "3PM" / "Threes" / "Three Pointers Made" / "3-Pointers Made" / "3PTM" → three_pointers
- "FG Made" / "FGM" / "Field Goals Made" → fgm
- "FT Made" / "FTM" / "Free Throws Made" → ftm
- "FG Attempted" / "FGA" → fga
- "FT Attempted" / "FTA" → fta
- "3PT Attempts" / "3PA" → tpa

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

        # Resolve team via basketball API
        resolved_team = None
        if team_hint:
            teams = await search_basketball_teams(team_hint)
            if teams:
                best = teams[0]
                resolved_team = {"teamId": best["id"], "teamName": best.get("name", team_hint)}

        # Resolve opponent
        resolved_opp = None
        if opp_hint:
            opps = await search_basketball_teams(opp_hint)
            if opps:
                best = opps[0]
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
                    is_international, nat_team_id, original_team_name
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
