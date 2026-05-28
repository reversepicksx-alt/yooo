import asyncio as aio
import re
import unicodedata
from fastapi import APIRouter

from config import CURRENT_SEASON, db
from models import PlayerSearchRequest
from utils import api_football_request

router = APIRouter(prefix="/api", tags=["players"])


async def _search_players_cache(query: str, league_id: int = None) -> list:
    """Fast MongoDB cache lookup for player search. Returns list of player dicts.

    For multi-word queries we require ALL words to appear in nameClean so that
    searching "van de ven" cannot accidentally match "Aravena" (contains "ven"
    but not "van" or "de").
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
        filt: dict = {"nameClean": {"$regex": re.escape(parts[0])}}
    else:
        filt = {"$and": [{"nameClean": {"$regex": re.escape(w)}} for w in parts]}

    if league_id:
        filt["leagueId"] = league_id

    docs = await db[COL_PLAYERS].find(filt, {"_id": 0}).limit(20).to_list(20)

    # For single-word queries without a league constraint: only use the cache if
    # it contains at least one top-5 European league player. Otherwise the cache
    # may return e.g. Ecuadorian "Caicedos" while Chelsea's Moisés Caicedo is
    # actually findable via the live API — so let Strategy 2 run instead.
    if not league_id and len(parts) == 1 and docs:
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

    # Strategy 0: MongoDB cache-first (fast, no quota usage)
    try:
        cache_results = await _search_players_cache(req.query, req.league_id)
        if cache_results:
            return {"players": cache_results[:15]}
    except Exception:
        pass

    all_players = []

    # Strategy 1: Search within specified league
    if req.league_id:
        async def search_season(s):
            try:
                data = await api_football_request("players", {"search": req.query, "league": req.league_id, "season": s})
                return [(extract_player(item), s) for item in (data or [])]
            except Exception:
                return []

        results_by_season = await aio.gather(search_season(season + 1), search_season(season))
        season_data = {}
        for season_results in results_by_season:
            for player_data, found_season in season_results:
                pid = player_data["id"]
                if pid not in season_data or found_season > season_data[pid][1]:
                    season_data[pid] = (player_data, found_season)
        all_players = [v[0] for v in season_data.values()]

        if not all_players:
            for s in [season - 1, season - 2]:
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
            results_by_season = await aio.gather(search_season_lastname(season + 1), search_season_lastname(season))
            season_data = {}
            for season_results in results_by_season:
                for player_data, found_season in season_results:
                    pid = player_data["id"]
                    if pid not in season_data or found_season > season_data[pid][1]:
                        season_data[pid] = (player_data, found_season)
            all_players = [v[0] for v in season_data.values()]
            if not all_players:
                for s in [season - 1]:
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

    # Sort — all_match is the PRIMARY criterion so a perfect name match
    # (e.g. "van de Ven") always beats a team-enriched partial match (e.g. "Aravena").
    def _strip(s):
        return ''.join(c for c in unicodedata.normalize('NFD', s) if unicodedata.category(c) != 'Mn')
    query_parts = [_strip(w.lower()) for w in req.query.strip().split()]
    _TOP5_LEAGUES = {39, 140, 135, 78, 61}   # EPL, LaLiga, SerieA, Bund., Ligue1

    def sort_key(p):
        has_team    = 0 if p["teamName"] else 1
        name_norm   = _strip(p["name"].lower())
        first_norm  = _strip((p["firstname"] or "").lower())
        all_match   = 0 if all(w in name_norm for w in query_parts) else 1
        first_match = 0 if query_parts and first_norm.startswith(query_parts[0]) else 1
        # Prefer top-5 European leagues — e.g. Chelsea's Moisés Caicedo over
        # Ecuadorian Caicedos when the user just types "caicedo".
        top_league  = 0 if p.get("leagueId") in _TOP5_LEAGUES else 1
        return (all_match, top_league, has_team, first_match, p["name"])
    players.sort(key=sort_key)

    # Quality filter: if we have any perfect-match player (all query words in name),
    # drop the partial-match players — they are almost always wrong (e.g. "Aravena"
    # when searching "van de ven" because they share the letters "ven").
    if any(sort_key(p)[0] == 0 for p in players):
        players = [p for p in players if sort_key(p)[0] == 0]

    return {"players": players[:15]}


@router.get("/leagues/search")
async def search_leagues(search: str = ""):
    """Search leagues by name from the MongoDB cache (1200+ leagues)."""
    q = search.strip()
    if len(q) < 2:
        return {"leagues": []}
    try:
        from cache import COL_LEAGUES
        q_lower = q.lower()
        docs = await db[COL_LEAGUES].find(
            {"nameLower": {"$regex": re.escape(q_lower)}},
            {"_id": 0, "leagueId": 1, "name": 1, "country": 1}
        ).limit(20).to_list(20)
        results = [
            {"id": d["leagueId"], "name": d["name"], "country": d.get("country", "")}
            for d in docs if d.get("leagueId") and d.get("name")
        ]
        return {"leagues": results}
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
