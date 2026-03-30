import json
import uuid
import traceback
import unicodedata
from fastapi import APIRouter, HTTPException
from emergentintegrations.llm.chat import LlmChat, UserMessage, ImageContent

from config import (
    EMERGENT_LLM_KEY, SUPPORTED_LEAGUES, CURRENT_SEASON,
    PROP_TYPE_ALIASES, INTERNATIONAL_LEAGUES, NATION_TO_LEAGUES,
    TOP_5_LEAGUES,
)
from models import ScanPropRequest
from utils import api_football_request, strip_accents

router = APIRouter(prefix="/api", tags=["scan"])

@router.post("/scan-prop")
async def scan_prop(req: ScanPropRequest):
    """Use AI vision to extract player prop data from a screenshot."""
    try:
        chat = LlmChat(
            api_key=EMERGENT_LLM_KEY,
            session_id=f"scan-{uuid.uuid4().hex[:8]}",
            system_message="You are an expert at reading player prop screenshots. Extract structured data precisely."
        ).with_model("openai", "gpt-4o")

        image_content = ImageContent(image_base64=req.image_base64)

        leagues_list = ", ".join([f"{l['name']} (ID:{l['id']})" for l in SUPPORTED_LEAGUES])

        prompt = f"""Analyze this screenshot of a player prop card.

LAYOUT GUIDE:
- The player's FIRST NAME is on the top line, LAST NAME is on the second line (larger/bolder text)
- Below the name: "SOCCER • [Team Name] • [Position]"
- Below that: "vs [Opponent]" or "@ [Opponent]" with date/time
- The prop line number (e.g., 48.5) is shown prominently with the stat type below it (e.g., "Passes Attempted")
- "Less" and "More" buttons are just selection options — IGNORE them. Do NOT extract over/under from these.
- A bar chart may show the player's recent game history

CRITICAL RULES:
- Read the player name EXACTLY as shown on the card. The name displayed prominently at the top IS the player.
- Do NOT confuse player names with opponent names, team names, or any other text.
- If you see only ONE player card, return exactly ONE entry.
- IGNORE any "Less"/"More" buttons — they are irrelevant selection UI, not a prediction.

Extract for EACH player prop entry:
1. playerName — The player's full name as displayed (first + last)
2. propType — Map to one of: pass_attempts, shots, shots_on_target, tackles, key_passes, saves, interceptions, blocks, dribbles, fouls_drawn
3. line — The numerical line (e.g., 48.5)
4. opponentName — The opposing team from "vs [Team]"
5. league — Best guess league name
6. leagueId — Match to one of: {leagues_list}
7. playerTeam — The player's own team name

PROP TYPE MAPPING:
- "Passes Attempted" / "Pass Attempts" / "Passes" → pass_attempts
- "Shots" / "Shots Taken" → shots
- "Shots on Target" / "SOT" → shots_on_target
- "Tackles" → tackles
- "Key Passes" / "Assists" → key_passes
- "Saves" / "Goalkeeper Saves" → saves
- "Interceptions" → interceptions
- "Blocks" → blocks
- "Dribble Attempts" / "Dribbles" → dribbles
- "Fouls Drawn" → fouls_drawn

Return ONLY valid JSON array. Each element: {{"playerName":"...","propType":"...","line":0.0,"opponentName":"...","playerTeam":"...","venue":"home or away","league":"...","leagueId":0}}

VENUE RULES:
- If the matchup line says "@ [Team]" — the "@" means AWAY. The player's team is traveling to the opponent. venue = "away"
- If the matchup line says "vs [Team]" (no @) — the player's team is at HOME. venue = "home"
- Example: Player team is Botafogo, matchup says "@ Athletico PR" → venue = "away", opponentName = "Athletico PR"

If you cannot determine a field, use null. Always try to extract the line number.
If there's only one entry, still return it as an array with one element."""

        msg = UserMessage(text=prompt, file_contents=[image_content])
        response = await chat.send_message(msg)
        response_text = response.strip()

        # Clean markdown fences
        if response_text.startswith("```"):
            lines = response_text.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            response_text = "\n".join(lines)

        extracted = json.loads(response_text)
        if not isinstance(extracted, list):
            extracted = [extracted]

        # ── Team → League mapping for when AI can't determine league ──
        TEAM_LEAGUE_MAP = {
            # Brazil Serie A (71)
            "botafogo": 71, "flamengo": 71, "palmeiras": 71, "sao paulo": 71, "corinthians": 71,
            "atletico mineiro": 71, "atletico paranaense": 71, "athletico": 71, "athletico pr": 71,
            "gremio": 71, "internacional": 71, "cruzeiro": 71, "fluminense": 71, "santos": 71,
            "vasco": 71, "bahia": 71, "fortaleza": 71, "bragantino": 71, "juventude": 71,
            "cuiaba": 71, "goias": 71, "vitoria": 71, "sport": 71, "ceara": 71,
            # Premier League (39)
            "arsenal": 39, "chelsea": 39, "liverpool": 39, "manchester city": 39, "man city": 39,
            "manchester united": 39, "man united": 39, "tottenham": 39, "spurs": 39,
            "newcastle": 39, "aston villa": 39, "west ham": 39, "brighton": 39, "wolves": 39,
            "crystal palace": 39, "everton": 39, "fulham": 39, "brentford": 39, "bournemouth": 39,
            "nottingham forest": 39, "leicester": 39, "ipswich": 39, "southampton": 39,
            # La Liga (140)
            "real madrid": 140, "barcelona": 140, "atletico madrid": 140, "athletic bilbao": 140,
            "real sociedad": 140, "betis": 140, "villarreal": 140, "sevilla": 140, "girona": 140,
            "valencia": 140, "getafe": 140, "osasuna": 140, "celta vigo": 140, "mallorca": 140,
            "rayo vallecano": 140, "alaves": 140, "las palmas": 140, "cadiz": 140,
            # Bundesliga (78)
            "bayern munich": 78, "bayern": 78, "dortmund": 78, "borussia dortmund": 78,
            "leverkusen": 78, "bayer leverkusen": 78, "rb leipzig": 78, "leipzig": 78,
            "stuttgart": 78, "frankfurt": 78, "wolfsburg": 78, "freiburg": 78,
            # Serie A Italy (135)
            "inter milan": 135, "inter": 135, "ac milan": 135, "milan": 135, "juventus": 135,
            "napoli": 135, "roma": 135, "lazio": 135, "atalanta": 135, "fiorentina": 135,
            "bologna": 135, "torino": 135, "monza": 135, "genoa": 135, "cagliari": 135,
            # Ligue 1 (61)
            "psg": 61, "paris saint-germain": 61, "marseille": 61, "lyon": 61, "monaco": 61,
            "lille": 61, "lens": 61, "nice": 61, "rennes": 61, "strasbourg": 61,
            # MLS (253)
            "la galaxy": 253, "lafc": 253, "inter miami": 253, "atlanta united": 253,
            "new york city fc": 253, "nycfc": 253, "new york red bulls": 253, "seattle sounders": 253,
            "portland timbers": 253, "columbus crew": 253, "fc cincinnati": 253, "nashville sc": 253,
            # NWSL (254)
            "portland thorns": 254, "washington spirit": 254, "north carolina courage": 254,
            "orlando pride": 254, "gotham fc": 254, "angel city": 254, "kansas city current": 254,
            # International teams → Nations League / International (5)
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

        def infer_league_id(team_name, opponent_name, ai_league_id):
            """Infer league ID from team names when AI can't determine it."""
            if ai_league_id and ai_league_id != 39:
                # AI returned a specific non-default league, trust it
                return ai_league_id
            # Try team name
            for name in [team_name, opponent_name]:
                if not name:
                    continue
                name_lower = name.lower().strip()
                if name_lower in TEAM_LEAGUE_MAP:
                    return TEAM_LEAGUE_MAP[name_lower]
                # Partial match
                for key, lid in TEAM_LEAGUE_MAP.items():
                    if key in name_lower or name_lower in key:
                        return lid
            return ai_league_id or 71  # Default to Brasileirao if nothing found

        def strip_accents(text):
            """Remove diacritics and normalize Nordic/special chars for API search."""
            import unicodedata
            # Handle specific Nordic characters that NFKD doesn't decompose
            CHAR_MAP = {'ø': 'o', 'Ø': 'O', 'æ': 'ae', 'Æ': 'AE', 'å': 'a', 'Å': 'A',
                        'ð': 'd', 'Ð': 'D', 'þ': 'th', 'Þ': 'Th', 'ß': 'ss',
                        'ł': 'l', 'Ł': 'L', 'đ': 'd', 'Đ': 'D'}
            text = ''.join(CHAR_MAP.get(c, c) for c in text)
            nfkd = unicodedata.normalize('NFKD', text)
            return ''.join(c for c in nfkd if not unicodedata.category(c).startswith('M'))

        # Resolve each player via API-Sports search
        results = []
        for entry in extracted:
            player_name = entry.get("playerName")
            if not player_name:
                continue

            # Normalize prop type
            raw_prop = (entry.get("propType") or "").lower().strip()
            prop_type = PROP_TYPE_ALIASES.get(raw_prop, raw_prop)
            if prop_type not in ["pass_attempts", "shots", "shots_on_target", "tackles", "key_passes", "saves", "interceptions", "blocks", "dribbles", "fouls_drawn"]:
                prop_type = "pass_attempts"  # safe default

            player_team_hint = (entry.get("playerTeam") or "").lower().strip()
            opponent_hint = (entry.get("opponentName") or "").strip()
            ai_league_id = entry.get("leagueId")
            league_id = infer_league_id(entry.get("playerTeam"), opponent_hint, ai_league_id)
            league_name = entry.get("league")
            # Derive league name from ID if AI returned null
            if not league_name:
                for sl in SUPPORTED_LEAGUES:
                    if sl["id"] == league_id:
                        league_name = sl["name"]
                        break

            line = entry.get("line") or 0
            venue = (entry.get("venue") or "home").lower().strip()
            if venue not in ("home", "away"):
                venue = "home"

            # Search for player in API-Sports
            resolved_player = None
            try:
                search_query = player_name.strip()
                search_clean = strip_accents(search_query)
                # Build search variants: accent-stripped first (API only accepts alphanumeric), then last name
                search_variants = []
                seen = set()
                for v in [search_clean, search_query]:
                    stripped = strip_accents(v)
                    if stripped not in seen:
                        search_variants.append(stripped)
                        seen.add(stripped)
                name_parts = search_clean.split()
                if len(name_parts) > 1:
                    last = name_parts[-1]
                    if last not in seen:
                        search_variants.append(last)
                        seen.add(last)

                def pick_best_match(data_list, query, team_hint):
                    """Pick the best player match, preferring team name match."""
                    query_lower = strip_accents(query.lower())
                    last_name = query_lower.split()[-1] if query_lower.split() else query_lower
                    # Build team hint variants for fuzzy matching
                    team_hints = []
                    if team_hint:
                        team_hints.append(team_hint)
                        th_var = team_hint.replace("th", "t")
                        if th_var != team_hint:
                            team_hints.append(th_var)
                        # Expand abbreviations
                        for abbr, full in [("pr", "paranaense"), ("mg", "mineiro"), ("go", "goianiense")]:
                            if team_hint.endswith(f" {abbr}"):
                                expanded = team_hint[:-(len(abbr))].strip() + " " + full
                                team_hints.append(expanded)
                                team_hints.append(expanded.replace("th", "t"))
                        # Also add first word
                        team_hints.append(team_hint.split()[0])

                    candidates = []
                    for d in data_list[:20]:
                        pname = strip_accents(d["player"]["name"].lower())
                        team_name = (d.get("statistics", [{}])[0].get("team", {}).get("name") or "").lower()
                        name_match = query_lower in pname or pname in query_lower or last_name in pname
                        team_match = False
                        if team_hints:
                            for th in team_hints:
                                if th in team_name or team_name in th:
                                    team_match = True
                                    break
                        if name_match:
                            candidates.append((d, team_match))
                    # Prefer candidates where team also matches
                    if candidates:
                        team_matched = [c for c in candidates if c[1]]
                        if team_matched:
                            return team_matched[0][0]
                        # Only return non-team-matched if no team hint was provided
                        if not team_hint:
                            return candidates[0][0]
                    return None

                # International leagues where players are indexed under their CLUB, not national team
                INTERNATIONAL_LEAGUES = {1, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 15, 29, 30, 31, 32, 33, 34, 115, 960}

                # Map national teams to their most likely club leagues (players play domestically or in top-5)
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
                TOP_5_LEAGUES = [39, 140, 135, 78, 61]  # Fallback: EPL, La Liga, Serie A, Bundesliga, Ligue 1

                is_international = league_id in INTERNATIONAL_LEAGUES

                # Build league search order
                if is_international:
                    # Use national team name to narrow down which club leagues to search
                    team_lower = player_team_hint or ""
                    leagues_to_try = NATION_TO_LEAGUES.get(team_lower, TOP_5_LEAGUES)
                else:
                    leagues_to_try = [league_id]

                # Save original international context before resolution overwrites it
                original_league_id = league_id
                original_league_name = league_name
                original_team_name = (entry.get("playerTeam") or "").strip()

                # For international matches, resolve the national team ID upfront
                nat_team_id = None
                if is_international and player_team_hint:
                    try:
                        teams_data = await api_football_request("teams", {"search": player_team_hint.title()})
                        if teams_data:
                            for t in teams_data[:5]:
                                tname = t.get("team", {}).get("name", "").lower()
                                if player_team_hint in tname or tname in player_team_hint:
                                    nat_team_id = t["team"]["id"]
                                    break
                    except Exception:
                        pass

                for variant in search_variants:
                    if resolved_player:
                        break
                    if len(variant) < 3:
                        continue
                    for try_league in leagues_to_try:
                        if resolved_player:
                            break
                        for season in [CURRENT_SEASON + 1, CURRENT_SEASON]:
                            try:
                                data = await api_football_request("players", {"search": variant, "league": try_league, "season": season})
                                if data:
                                    best = pick_best_match(data, search_query, player_team_hint)
                                    if best:
                                        resolved_player = {
                                            "playerId": best["player"]["id"],
                                            "playerName": best["player"]["name"],
                                            "photo": "",
                                            "teamId": nat_team_id if is_international and nat_team_id else best.get("statistics", [{}])[0].get("team", {}).get("id"),
                                            "teamName": (original_team_name or player_team_hint.title()) if is_international else best.get("statistics", [{}])[0].get("team", {}).get("name", ""),
                                        }
                                        if not is_international:
                                            actual_league = best.get("statistics", [{}])[0].get("league", {})
                                            if actual_league.get("id"):
                                                league_id = actual_league["id"]
                                                league_name = actual_league.get("name", league_name)
                                        break
                                    elif not player_team_hint or is_international:
                                        best = data[0]
                                        resolved_player = {
                                            "playerId": best["player"]["id"],
                                            "playerName": best["player"]["name"],
                                            "photo": "",
                                            "teamId": nat_team_id if is_international and nat_team_id else best.get("statistics", [{}])[0].get("team", {}).get("id"),
                                            "teamName": (original_team_name or player_team_hint.title()) if is_international else best.get("statistics", [{}])[0].get("team", {}).get("name", ""),
                                        }
                                        if not is_international:
                                            actual_league = best.get("statistics", [{}])[0].get("league", {})
                                            if actual_league.get("id"):
                                                league_id = actual_league["id"]
                                                league_name = actual_league.get("name", league_name)
                                        break
                            except Exception:
                                continue

                # Squad-based fallback: if player not found via search
                if not resolved_player and player_team_hint:
                    try:
                        if not is_international:
                            # Club match: resolve team and search its squad
                            team_search_variants = [player_team_hint]
                            th_variant = player_team_hint.replace("th", "t")
                            if th_variant != player_team_hint:
                                team_search_variants.append(th_variant)
                            ABBREV_MAP = {"pr": "paranaense", "mg": "mineiro", "go": "goianiense", "rj": "rio"}
                            for abbr, full in ABBREV_MAP.items():
                                if player_team_hint.endswith(f" {abbr}"):
                                    expanded = player_team_hint[:-(len(abbr))].strip() + " " + full
                                    team_search_variants.append(expanded)
                                    ev = expanded.replace("th", "t")
                                    if ev != expanded:
                                        team_search_variants.append(ev)

                            resolved_team_id = None
                            for tsv in team_search_variants:
                                if resolved_team_id:
                                    break
                                try:
                                    teams_data = await api_football_request("teams", {"search": tsv})
                                    if teams_data:
                                        for t in teams_data[:10]:
                                            tname_lower = t.get("team", {}).get("name", "").lower()
                                            is_youth = any(s in tname_lower for s in ["u20", "u23", "u21", "u19", "u18", "u17"])
                                            if not is_youth:
                                                resolved_team_id = t["team"]["id"]
                                                break
                                except Exception:
                                    continue

                            if resolved_team_id:
                                squad_data = await api_football_request("players/squads", {"team": resolved_team_id})
                                if squad_data:
                                    squad_players = squad_data[0].get("players", []) if squad_data else []
                                    search_lower = strip_accents(search_query.lower())
                                    last_name_lower = search_lower.split()[-1] if search_lower.split() else search_lower
                                    for sp in squad_players:
                                        sp_name = strip_accents(sp.get("name", "").lower())
                                        if search_lower == sp_name or last_name_lower == sp_name or search_lower in sp_name or sp_name in search_lower:
                                            resolved_player = {
                                                "playerId": sp["id"],
                                                "playerName": sp["name"],
                                                "photo": "",
                                                "teamId": resolved_team_id,
                                                "teamName": player_team_hint.title(),
                                            }
                                            break
                        else:
                            # International match: search the NATIONAL TEAM squad
                            team_lower = player_team_hint or ""
                            search_lower = strip_accents(search_query.lower())
                            last_name_lower = search_lower.split()[-1] if search_lower.split() else search_lower

                            # nat_team_id already resolved above
                            if nat_team_id:
                                try:
                                    squad_data = await api_football_request("players/squads", {"team": nat_team_id})
                                    if squad_data:
                                        for sp in squad_data[0].get("players", []):
                                            sp_name = strip_accents(sp.get("name", "").lower())
                                            if last_name_lower in sp_name or search_lower in sp_name or sp_name in search_lower:
                                                found_player_id = sp["id"]
                                                # International match: use national team ID, not club
                                                resolved_player = {
                                                    "playerId": found_player_id,
                                                    "playerName": sp["name"],
                                                    "photo": "",
                                                    "teamId": nat_team_id,
                                                    "teamName": original_team_name or team_lower.title(),
                                                }
                                                break
                                except Exception:
                                    pass
                    except Exception:
                        pass

                # Fallback: broader search without league filter
                if not resolved_player:
                    for variant in search_variants:
                        if resolved_player:
                            break
                        if len(variant) < 3:
                            continue
                        for season in [CURRENT_SEASON + 1, CURRENT_SEASON]:
                            try:
                                data = await api_football_request("players", {"search": variant, "season": season})
                                if data:
                                    best = pick_best_match(data, search_query, player_team_hint)
                                    if best:
                                        resolved_player = {
                                            "playerId": best["player"]["id"],
                                            "playerName": best["player"]["name"],
                                            "photo": "",
                                            "teamId": nat_team_id if is_international and nat_team_id else best.get("statistics", [{}])[0].get("team", {}).get("id"),
                                            "teamName": (original_team_name or player_team_hint.title()) if is_international else best.get("statistics", [{}])[0].get("team", {}).get("name", ""),
                                        }
                                        if not is_international:
                                            actual_league = best.get("statistics", [{}])[0].get("league", {})
                                            if actual_league.get("id"):
                                                league_id = actual_league["id"]
                                                league_name = actual_league.get("name", league_name)
                                        break
                                    elif not player_team_hint or is_international:
                                        best = data[0]
                                        resolved_player = {
                                            "playerId": best["player"]["id"],
                                            "playerName": best["player"]["name"],
                                            "photo": "",
                                            "teamId": nat_team_id if is_international and nat_team_id else best.get("statistics", [{}])[0].get("team", {}).get("id"),
                                            "teamName": (original_team_name or player_team_hint.title()) if is_international else best.get("statistics", [{}])[0].get("team", {}).get("name", ""),
                                        }
                                        if not is_international:
                                            actual_league = best.get("statistics", [{}])[0].get("league", {})
                                            if actual_league.get("id"):
                                                league_id = actual_league["id"]
                                                league_name = actual_league.get("name", league_name)
                                        break
                            except Exception:
                                continue
            except Exception:
                pass

            # For international matches, always restore the original league context
            if is_international and original_league_id:
                league_id = original_league_id
                league_name = original_league_name

            # Resolve opponent team
            resolved_opponent = None
            opponent_name = entry.get("opponentName")
            if opponent_name and resolved_player:
                try:
                    opp_lower = opponent_name.lower().strip()
                    # Try multiple search variants for the opponent
                    opp_searches = [opponent_name]
                    clean_opp = opponent_name.strip()
                    # Common international team name aliases
                    COUNTRY_ALIASES = {
                        "czechia": "Czech Republic", "usa": "United States",
                        "korea republic": "South Korea", "ir iran": "Iran",
                        "bosnia": "Bosnia & Herzegovina", "cote d'ivoire": "Ivory Coast",
                    }
                    alias = COUNTRY_ALIASES.get(opp_lower)
                    if alias and alias not in opp_searches:
                        opp_searches.insert(0, alias)
                    # Expand common team name abbreviations
                    TEAM_ABBREVS = {"PR": "Paranaense", "MG": "Mineiro", "GO": "Goianiense", "RJ": "Rio"}
                    for abbr, full in TEAM_ABBREVS.items():
                        if clean_opp.upper().endswith(f" {abbr}"):
                            expanded = clean_opp[:-(len(abbr))].strip() + " " + full
                            opp_searches.insert(1, expanded)
                            # Also add th→t variant of the expanded name
                            expanded_v = expanded.replace("th", "t").replace("Th", "T")
                            if expanded_v != expanded:
                                opp_searches.insert(2, expanded_v)
                    # Common spelling variants (Athletico → Atletico)
                    variant_th = clean_opp.replace("th", "t").replace("Th", "T")
                    if variant_th != clean_opp and variant_th not in opp_searches:
                        opp_searches.append(variant_th)
                    # Strip common abbreviations as last resort
                    for suffix in [" PR", " FC", " SC", " CF", " AC", " MG", " GO", " RJ"]:
                        if clean_opp.upper().endswith(suffix):
                            stripped = clean_opp[:-len(suffix)].strip()
                            stripped_variant = stripped.replace("th", "t").replace("Th", "T")
                            if stripped_variant != stripped and stripped_variant not in opp_searches:
                                opp_searches.append(stripped_variant)
                            if stripped not in opp_searches:
                                opp_searches.append(stripped)

                    best_team = None
                    teams_data = []
                    first_word = opp_lower.split()[0]
                    first_word_variant = first_word.replace("th", "t")
                    for opp_query in opp_searches:
                        if best_team:
                            break
                        teams_data = await api_football_request("teams", {"search": opp_query})
                        if teams_data:
                            for t in teams_data[:15]:
                                tname = t.get("team", {}).get("name", "")
                                tname_lower = tname.lower()
                                is_youth = any(s in tname_lower for s in ["u20", "u23", "u21", "u19", "u18", "u17", " ii", " b "])
                                is_women = tname_lower.endswith(" w")
                                name_match = first_word in tname_lower or first_word_variant in tname_lower
                                if name_match and not is_youth and not is_women:
                                    best_team = t
                                    break
                    if not best_team and teams_data:
                        best_team = teams_data[0]
                    if best_team:
                        resolved_opponent = {
                            "teamId": best_team["team"]["id"],
                            "teamName": best_team["team"]["name"],
                        }
                except Exception:
                    pass

            results.append({
                "extracted": {
                    "playerName": player_name,
                    "propType": prop_type,
                    "line": line,
                    "venue": venue,
                    "opponentName": entry.get("opponentName"),
                    "playerTeam": entry.get("playerTeam"),
                    "league": league_name or entry.get("league"),
                    "leagueId": league_id,
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



