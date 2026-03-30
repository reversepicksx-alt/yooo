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

router = APIRouter(prefix="/api", tags=["scan"])

# Valid prop types for normalization
VALID_PROPS = {
    "pass_attempts", "shots", "shots_on_target", "tackles", "key_passes",
    "saves", "interceptions", "blocks", "dribbles", "fouls_drawn",
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


@router.post("/scan-prop")
async def scan_prop(req: ScanPropRequest):
    """Use AI vision to extract player prop data from a screenshot."""
    try:
        # ── Step 1: Vision AI extraction ──
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

        # ── Step 2: Resolve each extracted player ──
        results = []
        for entry in extracted:
            player_name = entry.get("playerName")
            if not player_name:
                continue

            # Normalize prop type
            raw_prop = (entry.get("propType") or "").lower().strip()
            prop_type = PROP_TYPE_ALIASES.get(raw_prop, raw_prop)
            if prop_type not in VALID_PROPS:
                prop_type = "pass_attempts"

            player_team_hint = (entry.get("playerTeam") or "").lower().strip()
            opponent_hint = (entry.get("opponentName") or "").strip()
            ai_league_id = entry.get("leagueId")
            league_id = await _infer_league_id(entry.get("playerTeam"), opponent_hint, ai_league_id)
            league_name = entry.get("league")
            if not league_name:
                for sl in SUPPORTED_LEAGUES:
                    if sl["id"] == league_id:
                        league_name = sl["name"]
                        break

            line_val = entry.get("line") or 0
            venue = (entry.get("venue") or "home").lower().strip()
            if venue not in ("home", "away"):
                venue = "home"

            is_international = league_id in INTERNATIONAL_LEAGUES
            original_league_id = league_id
            original_league_name = league_name
            original_team_name = (entry.get("playerTeam") or "").strip()

            # ── Resolve national team ID for international matches ──
            nat_team_id = None
            nat_team_canonical = None
            if is_international and player_team_hint:
                nat_team_id, nat_team_canonical = await get_national_team_id(player_team_hint)
                if not nat_team_id:
                    # Last resort: direct API search
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

            # ── PRIMARY: Resolve player via MongoDB cache ──
            resolved_player = None

            # For club matches, try to resolve team from cache to narrow player search
            cache_team_id = None
            if not is_international and player_team_hint:
                club_id, _ = await get_team_by_name(player_team_hint)
                if club_id:
                    cache_team_id = club_id

            cached_player = await _resolve_player_via_cache(player_name, cache_team_id)
            if cached_player:
                if is_international and nat_team_id:
                    # For international: use player's playerId from cache but national team context
                    resolved_player = {
                        "playerId": cached_player["playerId"],
                        "playerName": cached_player["name"],
                        "photo": "",
                        "teamId": nat_team_id,
                        "teamName": original_team_name or nat_team_canonical or player_team_hint.title(),
                    }
                else:
                    # Club match: use cache data directly
                    resolved_player = {
                        "playerId": cached_player["playerId"],
                        "playerName": cached_player["name"],
                        "photo": "",
                        "teamId": cached_player.get("teamId"),
                        "teamName": cached_player.get("teamName", player_team_hint.title()),
                    }
                    # Update league from cache if available
                    if cached_player.get("leagueId") and not is_international:
                        league_id = cached_player["leagueId"]
                        for sl in SUPPORTED_LEAGUES:
                            if sl["id"] == league_id:
                                league_name = sl["name"]
                                break
                print(f"[SCAN] Cache HIT: {player_name} -> {resolved_player['playerName']} (ID {resolved_player['playerId']})")

            # ── FALLBACK: API-Sports search ──
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

            # For international matches, always restore original context
            if is_international and original_league_id:
                league_id = original_league_id
                league_name = original_league_name

            # ── Resolve opponent ──
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
