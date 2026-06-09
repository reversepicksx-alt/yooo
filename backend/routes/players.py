import asyncio as aio
import re
import unicodedata
from fastapi import APIRouter

from config import CURRENT_SEASON, db
from models import PlayerSearchRequest
from utils import api_football_request, is_quota_exhausted

router = APIRouter(prefix="/api", tags=["players"])

# Tournament / cup leagues — players aren't stored in cache under these IDs
# (e.g. Messi is cached under his club league, not under World Cup league 1)
_TOURNAMENT_LEAGUES = {1, 9, 10, 11, 13, 15, 16, 17, 18}


async def _search_players_cache(query: str, league_id: int = None, relaxed: bool = False) -> list:
    """Fast MongoDB cache lookup for player search. Returns list of player dicts.

    For multi-word queries we require ALL words to appear in nameClean so that
    searching "van de ven" cannot accidentally match "Aravena" (contains "ven"
    but not "van" or "de").

    ``relaxed=True`` skips the top-5-league gate so that quota-exhausted mode
    still returns any name-matched player (e.g. Messi at Inter Miami).
    """
    from cache import COL_PLAYERS
    from utils import strip_accents
    query_clean = strip_accents(query.lower().strip())
    parts = [p for p in query_clean.split() if p]
    if not parts:
        return []

    # Top-5 European leagues — used to validate single-word cache hits
    TOP_LEAGUES = {39, 140, 135, 78, 61}

    # Build filter: every word in the query must appear in nameClean
    if len(parts) == 1:
        name_filt: dict = {"nameClean": {"$regex": re.escape(parts[0])}}
    else:
        name_filt = {"$and": [{"nameClean": {"$regex": re.escape(w)}} for w in parts]}

    # Don't filter cache by tournament/cup league IDs — players are stored
    # under their club leagues, not the competition they appeared in.
    effective_league_id = None if (league_id in _TOURNAMENT_LEAGUES) else league_id

    filt = dict(name_filt)
    if effective_league_id:
        filt["leagueId"] = effective_league_id

    docs = await db[COL_PLAYERS].find(filt, {"_id": 0}).limit(20).to_list(20)

    # If league-constrained search returned nothing, retry without the league filter
    if not docs and effective_league_id:
        docs = await db[COL_PLAYERS].find(name_filt, {"_id": 0}).limit(20).to_list(20)

    # Multi-word fallback: many cached players have abbreviated first names
    # (e.g. "R. Jiménez" for "Raul Jimenez") so the all-words filter misses them.
    # When the full-name search returns nothing, retry with just the last word
    # (usually the surname) which is almost never abbreviated.
    if not docs and len(parts) > 1:
        last_part = parts[-1]
        last_filt: dict = {"nameClean": {"$regex": re.escape(last_part)}}
        if effective_league_id:
            last_filt["leagueId"] = effective_league_id
        docs = await db[COL_PLAYERS].find(last_filt, {"_id": 0}).limit(100).to_list(100)
        if not docs and effective_league_id:
            docs = await db[COL_PLAYERS].find(
                {"nameClean": {"$regex": re.escape(last_part)}}, {"_id": 0}
            ).limit(100).to_list(100)

    # For single-word queries without a league constraint: only use the cache if
    # it contains at least one top-5 European league player — UNLESS relaxed mode
    # is on (quota exhausted) in which case we accept any cached result.
    if not effective_league_id and len(parts) == 1 and docs and not relaxed:
        if not any(d.get("leagueId") in TOP_LEAGUES for d in docs):
            return []

    results = []
    for d in docs:
        name = d.get("name", "")
        results.append({
            "id": d.get("playerId", 0),
            "name": name,
            "firstname": name.split()[0] if name.split() else "",
            "lastname": name.split()[-1] if name.split() else "",
            "age": 0,
            "nationality": "",
            "photo": "",
            "teamId": d.get("teamId", 0),
            "teamName": d.get("teamName", ""),
            "leagueId": d.get("leagueId", 0),
            "position": d.get("position", ""),
        })
    return results


@router.post("/players/search")
async def search_players(req: PlayerSearchRequest):
    if len(req.query) < 3:
        return {"players": []}
    season = req.season or CURRENT_SEASON
    query_lower = req.query.lower().strip()

    def extract_player(item):
        p = item.get("player", {})
        stats = item.get("statistics", [])
        team_id = stats[-1]["team"]["id"] if stats else 0
        team_name = stats[-1]["team"]["name"] if stats else ""
        league_id = stats[-1]["league"]["id"] if stats else 0
        position = p.get("position", "") or ""
        firstname = p.get("firstname", "") or ""
        lastname = p.get("lastname", "") or ""
        display_name = f"{firstname} {lastname}".strip() if firstname and lastname else p.get("name", "")
        return {
            "id": p.get("id", 0),
            "name": display_name,
            "firstname": firstname,
            "lastname": lastname,
            "age": p.get("age", 0),
            "nationality": p.get("nationality", ""),
            "photo": "",
            "teamId": team_id,
            "teamName": team_name,
            "leagueId": league_id,
            "position": position,
        }

    quota_gone = is_quota_exhausted()

    # Sort helpers — defined early so they can be applied to cache hits too.
    def _strip(s):
        return ''.join(c for c in unicodedata.normalize('NFD', s) if unicodedata.category(c) != 'Mn')
    query_parts = [_strip(w.lower()) for w in req.query.strip().split()]
    _TOP5_LEAGUES = {39, 140, 135, 78, 61}   # EPL, LaLiga, SerieA, Bund., Ligue1

    def sort_key(p):
        has_team    = 0 if p["teamName"] else 1
        name_norm   = _strip(p["name"].lower())
        first_norm  = _strip((p.get("firstname") or "").lower())
        name_words  = set(name_norm.split())
        all_match   = 0 if all(w in name_norm for w in query_parts) else 1
        # Exact-word match: every query part appears as a complete word in the name.
        # e.g. "messi" is a word in "l. messi" but NOT in "messias".
        exact_word  = 0 if all(w in name_words for w in query_parts) else 1
        first_match = 0 if query_parts and first_norm.startswith(query_parts[0]) else 1
        top_league  = 0 if p.get("leagueId") in _TOP5_LEAGUES else 1
        # Initial-match: handles abbreviated names like "R. Jiménez" when the user
        # types "Raul Jimenez". If the stored name's first letter == query first letter,
        # rank it above other same-surname players whose initial doesn't match.
        stored_initial = name_norm.split()[0][0] if name_norm.split() else ""
        query_initial  = query_parts[0][0] if query_parts else ""
        initial_match  = 0 if (stored_initial and query_initial and stored_initial == query_initial) else 1
        return (all_match, exact_word, top_league, has_team, initial_match, first_match, p["name"])

    def _apply_sort_and_quality(player_list):
        player_list.sort(key=sort_key)
        # Drop partial matches when any perfect (all-words) match exists
        if any(sort_key(p)[0] == 0 for p in player_list):
            player_list = [p for p in player_list if sort_key(p)[0] == 0]
        return player_list[:15]

    # Strategy 0: MongoDB cache-first (fast, no quota usage)
    # When quota is exhausted use relaxed mode — accept any name-matched player
    # (e.g. Messi cached under Inter Miami, not World Cup league_id=1).
    try:
        cache_results = await _search_players_cache(req.query, req.league_id, relaxed=quota_gone)
        if cache_results:
            return {"players": _apply_sort_and_quality(cache_results)}
    except Exception:
        pass

    # If quota is gone, try last-name-only fallback before giving up.
    # Handles abbreviated cached names like "R. Jiménez" when user types "Raul Jimenez".
    if quota_gone:
        if " " in req.query.strip():
            last_word = req.query.strip().split()[-1]
            if len(last_word) >= 3:
                try:
                    fallback = await _search_players_cache(last_word, req.league_id, relaxed=True)
                    if fallback:
                        return {"players": _apply_sort_and_quality(fallback)}
                except Exception:
                    pass
        return {"players": []}

    all_players = []

    # For World Cup (league_id=1) and other tournament leagues the relevant
    # seasons are fixed WC years, not current club season.
    _WC_SEASONS = [2026, 2022, 2018, 2014]

    # Strategy 1: Search within specified league
    if req.league_id:
        seasons_to_try = (
            _WC_SEASONS if req.league_id == 1
            else [season + 1, season, season - 1, season - 2]
        )

        async def search_season(s):
            try:
                data = await api_football_request("players", {"search": req.query, "league": req.league_id, "season": s})
                return [(extract_player(item), s) for item in (data or [])]
            except Exception:
                return []

        results_by_season = await aio.gather(*[search_season(s) for s in seasons_to_try[:2]])
        season_data = {}
        for season_results in results_by_season:
            for player_data, found_season in season_results:
                pid = player_data["id"]
                if pid not in season_data or found_season > season_data[pid][1]:
                    season_data[pid] = (player_data, found_season)
        all_players = [v[0] for v in season_data.values()]

        if not all_players:
            for s in seasons_to_try[2:]:
                try:
                    data = await api_football_request("players", {"search": req.query, "league": req.league_id, "season": s})
                    if data:
                        all_players.extend([extract_player(item) for item in data])
                        break
                except Exception:
                    continue

        # Strategy 1b: last name fallback
        if not all_players and " " in req.query:
            last_name = req.query.strip().split()[-1]
            async def search_season_lastname(s):
                try:
                    data = await api_football_request("players", {"search": last_name, "league": req.league_id, "season": s})
                    return [(extract_player(item), s) for item in (data or [])]
                except Exception:
                    return []
            results_by_season = await aio.gather(*[search_season_lastname(s) for s in seasons_to_try[:2]])
            season_data = {}
            for season_results in results_by_season:
                for player_data, found_season in season_results:
                    pid = player_data["id"]
                    if pid not in season_data or found_season > season_data[pid][1]:
                        season_data[pid] = (player_data, found_season)
            all_players = [v[0] for v in season_data.values()]
            if not all_players:
                for s in seasons_to_try[2:3]:
                    try:
                        data = await api_football_request("players", {"search": last_name, "league": req.league_id, "season": s})
                        if data:
                            all_players.extend([extract_player(item) for item in data])
                            break
                    except Exception:
                        continue

    # Strategy 2: major domestic leagues + Copa Lib/Sud + all SA leagues
    if not all_players:
        major_leagues = [
            39, 140, 135, 78, 61,   # EPL, La Liga, Serie A, Bundesliga, Ligue 1
            253, 71, 307,            # MLS, Brasileirao, Saudi Pro
            13, 11,                  # Copa Libertadores, Copa Sudamericana
            128, 242, 239, 265,      # Argentina, Ecuador, Colombia, Chile
            270, 281, 299, 250, 21,  # Uruguay, Peru, Venezuela, Paraguay, Bolivia
        ]
        async def try_league(lid):
            for s in [season + 1, season]:
                try:
                    data = await api_football_request("players", {"search": req.query, "league": lid, "season": s})
                    if data:
                        return [extract_player(item) for item in data]
                except Exception:
                    continue
            return []
        results = await aio.gather(*[try_league(lid) for lid in major_leagues])
        for r in results:
            all_players.extend(r)

    # Strategy 3: profiles
    if not all_players:
        try:
            data = await api_football_request("players/profiles", {"search": req.query})
            if data:
                all_players.extend([extract_player(item) for item in data])
        except Exception:
            pass

    # Strategy 4: last name from profiles
    if not all_players and " " in req.query:
        last_name = req.query.strip().split()[-1]
        try:
            data = await api_football_request("players/profiles", {"search": last_name})
            if data:
                all_players.extend([extract_player(item) for item in data])
        except Exception:
            pass

    # De-duplicate
    seen_ids = {}
    for p in all_players:
        pid = p["id"]
        if pid not in seen_ids:
            seen_ids[pid] = p
        elif p["teamName"] and not seen_ids[pid]["teamName"]:
            seen_ids[pid] = p
    players = list(seen_ids.values())

    # Enrich the player cache with full firstNameClean so future disambiguation
    # in get_player_by_name can use first-name matching (e.g. "Jhojan" → "Jhohan").
    # Fire-and-forget — don't block the response.
    async def _enrich_player_cache(player_list):
        try:
            from config import db
            from cache import COL_PLAYERS
            import unicodedata as _ud
            def _clean(s):
                return ''.join(c for c in _ud.normalize('NFD', (s or '').lower()) if _ud.category(c) != 'Mn')
            for pl in player_list:
                pid = pl.get("id")
                fn = pl.get("firstname") or ""
                if pid and fn:
                    fn_clean = _clean(fn)
                    await db[COL_PLAYERS].update_many(
                        {"playerId": pid},
                        {"$set": {"firstNameClean": fn_clean}},
                    )
        except Exception:
            pass

    aio.ensure_future(_enrich_player_cache(players))

    # Sort and quality-filter using helpers defined at the top of the handler.
    # all_match is the PRIMARY criterion so a perfect name match (e.g. "van de Ven")
    # always beats a team-enriched partial match (e.g. "Aravena").
    return {"players": _apply_sort_and_quality(players)}


@router.get("/leagues/search")
async def search_leagues(search: str = ""):
    """Search leagues by name or country from the MongoDB cache (1200+ leagues)."""
    q = search.strip()
    if len(q) < 2:
        return {"leagues": []}
    try:
        from cache import COL_LEAGUES
        from utils import strip_accents
        q_lower = q.lower()
        q_clean = strip_accents(q_lower)  # accent-free version for matching

        # Search both nameLower (may have accents) and country fields.
        # Also try the accent-stripped query so "curacao" matches "curaçao".
        patterns = list({re.escape(q_lower), re.escape(q_clean)})
        name_or = [{"nameLower": {"$regex": p}} for p in patterns]
        country_or = [{"country": {"$regex": p, "$options": "i"}} for p in patterns]

        docs = await db[COL_LEAGUES].find(
            {"$or": name_or + country_or},
            {"_id": 0, "leagueId": 1, "name": 1, "country": 1}
        ).limit(30).to_list(30)

        # Deduplicate (country match + name match could return same doc twice)
        seen = set()
        results = []
        for d in docs:
            lid = d.get("leagueId")
            if lid and d.get("name") and lid not in seen:
                seen.add(lid)
                results.append({"id": lid, "name": d["name"], "country": d.get("country", "")})

        return {"leagues": results[:20]}
    except Exception as e:
        print(f"[LEAGUE SEARCH] Error: {e}")
        return {"leagues": []}


@router.get("/player/{player_id}/stats")
async def get_player_stats(player_id: int, season: int = CURRENT_SEASON):
    for s in [season + 1, season, season - 1, season - 2]:
        try:
            data = await api_football_request("players", {"id": player_id, "season": s})
            if data:
                return {"stats": data[0]}
        except Exception:
            continue
    return {"stats": None}
