import asyncio as aio
import re
import time
import unicodedata
from fastapi import APIRouter

from config import CURRENT_SEASON, db
from models import PlayerSearchRequest
from utils import api_football_request, is_quota_exhausted

router = APIRouter(prefix="/api", tags=["players"])

# Tournament / cup leagues — players aren't stored in cache under these IDs
# (e.g. Messi is cached under his club league, not under World Cup league 1)
_TOURNAMENT_LEAGUES = {1, 9, 10, 11, 13, 15, 16, 17, 18}

# International / national-team league IDs — when a player's best cache entry
# falls under one of these, we know teamName is a national team (e.g. "Canada"),
# not their actual club.  We enrich those players with a live API call.
_INTL_LEAGUES = {
    1,   # FIFA World Cup
    9,   # UEFA Nations League
    10,  # FIFA Friendlies / International Friendlies
    11,  # UEFA Euro
    15,  # Copa America
    16,  # African Cup of Nations
    17,  # Asian Cup
    18,  # CONCACAF Gold Cup
    29,  # UEFA U21 Championship
    30,  # FIFA U20 World Cup
    31,  # CONCACAF Nations League
    32,  # UEFA Euro Qualifiers
    33,  # Africa Cup of Nations Qualifiers
    34,  # World Cup Qualifiers - Europe
    35,  # World Cup Qualifiers - Asia
    26,  # World Cup Qualifiers - CONCACAF
    27,  # World Cup Qualifiers - South America
    28,  # World Cup Qualifiers - Africa
}


async def _search_players_cache(query: str, league_id: int = None, relaxed: bool = False) -> list:
    """Fast MongoDB cache lookup for player search. Returns list of player dicts.

    For multi-word queries we require ALL words to appear in nameClean so that
    searching "van de ven" cannot accidentally match "Aravena" (contains "ven"
    but not "van" or "de").

    ``relaxed=True`` skips the top-5-league gate so that quota-exhausted mode
    still returns any name-matched player (e.g. Messi at Inter Miami).

    IMPORTANT — abbreviated first-name merge:
    API-Football stores many players with abbreviated first names (e.g.
    "Jonathan David" → "J. David").  The all-words AND filter misses them.
    We always run the last-word (surname) fallback for 2+ word queries and
    MERGE the results with the AND hits, deduplicated by playerId.  Without
    this merge, "David Jonathan" (reversed-name false positive, all words
    present) would block "J. David" (correct abbreviated entry) from ever
    being returned.
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

    # Multi-word fallback — runs ALWAYS for 2+ word queries so that abbreviated
    # first-name cache entries (e.g. "J. David" for "Jonathan David") are merged
    # with any all-words AND hits.  Two complementary passes:
    #
    # Pass A — surname-only search (broad): catches entries where only the
    #   surname is in the index.  Capped at 100 to avoid Atlas scan timeouts.
    #   NOTE: popular surnames like "david" produce 100+ hits so J. David may
    #   fall past position 100 in natural order.  Pass B covers this case.
    #
    # Pass B — abbreviated-initial search (targeted): for query "Jonathan David"
    #   also searches nameClean matching /^j\..*david/ which hits "J. David"
    #   directly regardless of how many other "david" entries exist.  This is
    #   the reliable path for the common  "FirstName LastName" → "F. LastName"
    #   abbreviation pattern used by API-Football.
    if len(parts) > 1:
        last_part = parts[-1]
        first_initial = parts[0][0] if parts[0] else ""

        # Pass A — surname search (broad, up to 100 docs).
        # We intentionally do NOT deduplicate here — the dedup step below
        # picks the best-ranked entry per player after all passes complete.
        last_filt: dict = {"nameClean": {"$regex": re.escape(last_part)}}
        if effective_league_id:
            last_filt["leagueId"] = effective_league_id
        last_docs = await db[COL_PLAYERS].find(last_filt, {"_id": 0}).limit(100).to_list(100)
        if not last_docs and effective_league_id:
            last_docs = await db[COL_PLAYERS].find(
                {"nameClean": {"$regex": re.escape(last_part)}}, {"_id": 0}
            ).limit(100).to_list(100)
        docs.extend(last_docs or [])

        # Pass B — targeted abbreviated initial search: /^{initial}\. .* {last}$/
        # Catches "J. David" even when it sits beyond position 100 in Pass A.
        # Trailing $ prevents "J. Davidson" from matching a "david" query.
        # Also brings in the non-friendly leagueId entries (e.g. Canada leagueId=10)
        # so the dedup step can prefer them over friendlies (leagueId=667).
        if first_initial:
            abbrev_pattern = rf"^{re.escape(first_initial)}\..+{re.escape(last_part)}$"
            abbrev_filt: dict = {"nameClean": {"$regex": abbrev_pattern}}
            if effective_league_id:
                abbrev_filt["leagueId"] = effective_league_id
            abbrev_docs = await db[COL_PLAYERS].find(abbrev_filt, {"_id": 0}).limit(50).to_list(50)
            if not abbrev_docs and effective_league_id:
                abbrev_docs = await db[COL_PLAYERS].find(
                    {"nameClean": {"$regex": abbrev_pattern}}, {"_id": 0}
                ).limit(50).to_list(50)
            docs.extend(abbrev_docs or [])

        # Pass C — first+last word search: handles middle-name queries
        # e.g. "Roberto Carlos Lopes" → stored "Roberto Lopes" or "R. Lopes"
        if len(parts) >= 3:
            q_first, q_last = parts[0], parts[-1]
            # C1: first + last word (drops all middle names)
            fl_filt: dict = {"$and": [
                {"nameClean": {"$regex": re.escape(q_first)}},
                {"nameClean": {"$regex": re.escape(q_last)}},
            ]}
            if effective_league_id:
                fl_filt["leagueId"] = effective_league_id
            fl_docs = await db[COL_PLAYERS].find(fl_filt, {"_id": 0}).limit(50).to_list(50)
            if not fl_docs and effective_league_id:
                fl_docs = await db[COL_PLAYERS].find(
                    {"$and": [{"nameClean": {"$regex": re.escape(q_first)}},
                              {"nameClean": {"$regex": re.escape(q_last)}}]},
                    {"_id": 0}
                ).limit(50).to_list(50)
            docs.extend(fl_docs or [])
            # C2: initial+last for abbreviated first names with middle name
            # e.g. "Roberto Carlos Lopes" → /^r\..*lopes$/
            il_pattern = rf"^{re.escape(q_first[0])}\..+{re.escape(q_last)}$"
            il_filt: dict = {"nameClean": {"$regex": il_pattern}}
            if effective_league_id:
                il_filt["leagueId"] = effective_league_id
            il_docs = await db[COL_PLAYERS].find(il_filt, {"_id": 0}).limit(20).to_list(20)
            if not il_docs and effective_league_id:
                il_docs = await db[COL_PLAYERS].find(
                    {"nameClean": {"$regex": il_pattern}}, {"_id": 0}
                ).limit(20).to_list(20)
            docs.extend(il_docs or [])

    # Deduplicate by playerId: for each player keep the best-ranked entry.
    # Priority: top-5 club leagues > other real leagues > 667 friendlies.
    # leagueId=667 (Friendlies) entries are often "opponent team" artefacts
    # (e.g. Jonathan David shows as "Juventus" from a Canada-vs-Juventus
    # friendly because the fixture is filed under Juventus's fixture).
    # By ranking 667 lowest we keep the entry that reflects the player's
    # actual team (national team, lower league, etc.) over the opponent.
    _LEAGUE_RANK = {39: 0, 140: 1, 135: 2, 78: 3, 61: 4}  # EPL→Ligue1
    def _doc_rank(d: dict) -> int:
        lg = d.get("leagueId", 0)
        if lg in _LEAGUE_RANK:
            return _LEAGUE_RANK[lg]       # top-5 clubs: 0-4
        if lg == 667 or lg == 0:
            return 99                      # friendlies / unknown: last
        return 50                          # any other real league

    deduped: dict[int, dict] = {}
    for d in docs:
        pid = d.get("playerId", 0)
        if not pid:
            continue
        if pid not in deduped or _doc_rank(d) < _doc_rank(deduped[pid]):
            deduped[pid] = d
    docs = list(deduped.values())

    # For single-word queries without a league constraint: only use the cache if
    # it contains at least one top-5 European league player — UNLESS relaxed mode
    # is on (quota exhausted) in which case we accept any cached result.
    if not effective_league_id and len(parts) == 1 and docs and not relaxed:
        if not any(d.get("leagueId") in TOP_LEAGUES for d in docs):
            return []

    # Fire background refresh for any player whose cache entry is >60 days stale.
    # This keeps transferred players up-to-date without blocking the search response.
    from cache import PLAYER_STALE_SECONDS
    _now_ts = time.time()
    stale_pids: set[int] = set()
    for d in docs:
        cached_at = d.get("_cachedAt")
        if cached_at is None:
            # Backfill: derive from legacy _dt datetime object if present
            dt_val = d.get("_dt")
            if dt_val and hasattr(dt_val, "timestamp"):
                cached_at = dt_val.timestamp()
        if cached_at and (_now_ts - cached_at) > PLAYER_STALE_SECONDS:
            pid = d.get("playerId")
            if pid:
                stale_pids.add(pid)

    if stale_pids:
        async def _bg_refresh_stale(pids: set[int]):
            from cache import refresh_player_cache
            for pid in pids:
                try:
                    await refresh_player_cache(pid)
                except Exception:
                    pass
        aio.ensure_future(_bg_refresh_stale(stale_pids))

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
    if len(req.query) < 2:
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
        has_team    = 0 if p.get("teamName") else 1
        name_norm   = _strip((p.get("name") or "").lower())
        first_norm  = _strip((p.get("firstname") or "").lower())
        name_words  = set(name_norm.split())
        all_match   = 0 if all(w in name_norm for w in query_parts) else 1

        # Abbreviated-name rescue: "J. David" must match query "Jonathan David".
        # API-Football stores many players with abbreviated first names (e.g.
        # Jonathan David → "J. David").  Without this fix, the literal all_match
        # check fails for "jonathan" (not in "j. david"), so the player is
        # dropped by _apply_sort_and_quality even though they are the correct result.
        abbrev_rescued = False
        if all_match == 1 and len(query_parts) >= 2:
            stored_tokens = name_norm.split()
            first_token   = stored_tokens[0] if stored_tokens else ""
            # A token counts as an abbreviated initial if it is ≤2 chars and
            # consists of a letter optionally followed by a period.
            is_initial = (len(first_token) <= 2 and
                          first_token.rstrip(".").isalpha())
            if (is_initial and
                    first_token.rstrip(".") == query_parts[0][0] and
                    all(w in name_norm for w in query_parts[1:])):
                all_match      = 0
                abbrev_rescued = True

        # Middle-name rescue: "Roberto Carlos Lopes" → stored "Roberto Lopes" / "R. Lopes"
        # When the query has ≥3 parts and the stored name matches the FIRST and LAST
        # query word (ignoring middle names), treat it as a valid match.
        middle_rescued = False
        if all_match == 1 and not abbrev_rescued and len(query_parts) >= 3:
            q_first, q_last = query_parts[0], query_parts[-1]
            last_ok      = q_last in name_norm
            first_direct = q_first in name_norm
            nt = name_norm.split()
            first_initial_ok = (
                nt and len(nt[0]) <= 2 and nt[0].rstrip(".").isalpha() and
                nt[0].rstrip(".") == q_first[0]
            )
            if last_ok and (first_direct or first_initial_ok):
                all_match      = 0
                middle_rescued = True

        # Exact-word match: every query part appears as a complete word in the name.
        # e.g. "messi" is a word in "l. messi" but NOT in "messias".
        # Treat abbreviated-name rescues as exact-word matches so they sort above
        # reversed-name false positives (e.g. "David Jonathan" for "Jonathan David").
        exact_word  = 0 if (abbrev_rescued or middle_rescued or all(w in name_words for w in query_parts)) else 1

        # Reversed-name penalty: "David Jonathan" must not beat "J. David" when
        # the query is "Jonathan David".  If the stored name has all query words
        # but the first stored token matches the LAST query part (reversed order),
        # apply a penalty so correctly-ordered and abbreviated names rank first.
        name_tokens = name_norm.split()
        is_reversed = (
            len(query_parts) >= 2 and len(name_tokens) >= 2 and
            name_tokens[0] == query_parts[-1] and
            all(w in name_tokens for w in query_parts)
        )
        reversed_penalty = 1 if is_reversed else 0

        first_match = 0 if query_parts and first_norm.startswith(query_parts[0]) else 1
        top_league  = 0 if p.get("leagueId") in _TOP5_LEAGUES else 1
        # Initial-match: handles abbreviated names like "R. Jiménez" when the user
        # types "Raul Jimenez". If the stored name's first letter == query first letter,
        # rank it above other same-surname players whose initial doesn't match.
        stored_initial = name_norm.split()[0][0] if name_norm.split() else ""
        query_initial  = query_parts[0][0] if query_parts else ""
        initial_match  = 0 if (stored_initial and query_initial and stored_initial == query_initial) else 1
        return (all_match, reversed_penalty, exact_word, top_league, has_team,
                initial_match, first_match, p["name"])

    def _apply_sort_and_quality(player_list):
        player_list.sort(key=sort_key)
        # Drop partial matches when any perfect (all-words) match exists
        if any(sort_key(p)[0] == 0 for p in player_list):
            player_list = [p for p in player_list if sort_key(p)[0] == 0]
        return player_list[:15]

    async def _resolve_club_for_intl_player(p: dict) -> dict:
        """If a cache hit shows a national team, fetch the player's actual club
        from API-Football and override teamName/teamId/leagueId in the result.
        Also writes a club-league entry to cache so future searches hit directly.
        """
        pid = p.get("id")
        if not pid:
            return p
        try:
            from cache import COL_PLAYERS
            import unicodedata as _ud
            def _clean(s):
                return ''.join(
                    c for c in _ud.normalize('NFD', (s or '').lower())
                    if _ud.category(c) != 'Mn'
                )
            for s in [CURRENT_SEASON + 1, CURRENT_SEASON, CURRENT_SEASON - 1]:
                data = await api_football_request("players", {"id": pid, "season": s})
                if not data:
                    continue
                item = data[0]
                stats = item.get("statistics") or []
                # Walk stats newest-first; pick the first real club entry
                club_stat = None
                for st in reversed(stats):
                    lid = (st.get("league") or {}).get("id", 0)
                    tname = (st.get("team") or {}).get("name", "")
                    if lid and lid not in _INTL_LEAGUES and lid != 667 and tname:
                        club_stat = st
                        break
                if club_stat:
                    new_team_id   = (club_stat.get("team") or {}).get("id", 0)
                    new_team_name = (club_stat.get("team") or {}).get("name", "")
                    new_league_id = (club_stat.get("league") or {}).get("id", 0)
                    p = dict(p)
                    p["teamId"]   = new_team_id
                    p["teamName"] = new_team_name
                    p["leagueId"] = new_league_id
                    # Write club entry to cache so next search hits directly
                    name_clean = _clean(p.get("name", ""))
                    existing = await db[COL_PLAYERS].find_one(
                        {"playerId": pid, "leagueId": new_league_id}, {"_id": 0}
                    )
                    if not existing:
                        await db[COL_PLAYERS].insert_one({
                            "playerId": pid,
                            "name": p.get("name", ""),
                            "nameClean": name_clean,
                            "teamId": new_team_id,
                            "teamName": new_team_name,
                            "leagueId": new_league_id,
                            "position": p.get("position", ""),
                            "_cachedAt": time.time(),
                        })
                    else:
                        await db[COL_PLAYERS].update_one(
                            {"playerId": pid, "leagueId": new_league_id},
                            {"$set": {
                                "teamName": new_team_name,
                                "teamId": new_team_id,
                                "_cachedAt": time.time(),
                            }}
                        )
                    return p
                break  # season had data but no club stat found — don't try older seasons
        except Exception as e:
            print(f"[INTL ENRICH] pid={p.get('id')} err={e}")
        return p

    # Strategy 0: MongoDB cache-first (fast, no quota usage)
    # When quota is exhausted use relaxed mode — accept any name-matched player
    # (e.g. Messi cached under Inter Miami, not World Cup league_id=1).
    try:
        cache_results = await _search_players_cache(req.query, req.league_id, relaxed=quota_gone)
        if cache_results:
            sorted_results = _apply_sort_and_quality(cache_results)
            # NOTE: intentionally skipping _resolve_club_for_intl_player here.
            # Enrichment makes sequential API calls (up to 3 per player) which can
            # push total search time past the frontend's 15s timeout → "Search unavailable".
            # Team/league info is resolved properly via getPlayerContexts after selection.
            return {"players": sorted_results}
    except Exception:
        pass

    # If quota is gone, try last-name cache fallback then BDL search before giving up.
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
        # BDL live search — covers EPL, La Liga, Serie A, Bundesliga, Ligue 1,
        # UCL, MLS, World Cup without any API-Football dependency.
        try:
            from soccer_bdl_client import search_bdl_players
            bdl_hits = await search_bdl_players(req.query)
            if bdl_hits:
                return {"players": _apply_sort_and_quality(bdl_hits)}
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
    if not all_players and not quota_gone:
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
    if not all_players and not quota_gone:
        try:
            data = await api_football_request("players/profiles", {"search": req.query})
            if data:
                all_players.extend([extract_player(item) for item in data])
        except Exception:
            pass

    # Strategy 3b: for 3+ word queries, also search profiles by "first last" (drop middle)
    # e.g. "Roberto Carlos Lopes" → search "Roberto Lopes" directly
    if not all_players and not quota_gone and len(req.query.strip().split()) >= 3:
        parts_q = req.query.strip().split()
        fl_query = f"{parts_q[0]} {parts_q[-1]}"
        try:
            data = await api_football_request("players/profiles", {"search": fl_query})
            if data:
                all_players.extend([extract_player(item) for item in data])
        except Exception:
            pass

    # Strategy 4: last name from profiles
    if not all_players and not quota_gone and " " in req.query:
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
    final_players = _apply_sort_and_quality(players)

    # Write live API results back to cache so future cache-first searches
    # return the real club (e.g. Liverpool/Lille for Jonathan David) instead
    # of the national-team entry that may already be in cache.
    async def _write_api_results_to_cache(player_list):
        try:
            from cache import COL_PLAYERS
            from utils import strip_accents
            import unicodedata as _ud
            def _clean(s):
                return ''.join(
                    c for c in _ud.normalize('NFD', (s or '').lower())
                    if _ud.category(c) != 'Mn'
                )
            for pl in player_list:
                pid = pl.get("id")
                team_name = pl.get("teamName") or ""
                league_id_val = pl.get("leagueId") or 0
                name = pl.get("name") or ""
                if not pid or not team_name or not name:
                    continue
                # Only write back entries from real club leagues (not national teams)
                if league_id_val in _INTL_LEAGUES or league_id_val == 667:
                    continue
                name_clean = _clean(name)
                existing = await db[COL_PLAYERS].find_one(
                    {"playerId": pid, "leagueId": league_id_val}, {"_id": 0}
                )
                if not existing:
                    # Insert a new cache entry for this player×league so future
                    # cache searches find the club entry directly.
                    await db[COL_PLAYERS].insert_one({
                        "playerId": pid,
                        "name": name,
                        "nameClean": name_clean,
                        "teamId": pl.get("teamId") or 0,
                        "teamName": team_name,
                        "leagueId": league_id_val,
                        "position": pl.get("position") or "",
                        "_cachedAt": time.time(),
                    })
                else:
                    # Update teamName/teamId in existing entry if they differ
                    if (existing.get("teamName") != team_name or
                            existing.get("teamId") != pl.get("teamId")):
                        await db[COL_PLAYERS].update_one(
                            {"playerId": pid, "leagueId": league_id_val},
                            {"$set": {
                                "teamName": team_name,
                                "teamId": pl.get("teamId") or 0,
                                "_cachedAt": time.time(),
                            }}
                        )
        except Exception as e:
            print(f"[PLAYER CACHE WRITEBACK] Error: {e}")

    aio.ensure_future(_write_api_results_to_cache(final_players))
    return {"players": final_players}


@router.get("/leagues/by-id/{league_id}")
async def get_league_by_id(league_id: int):
    """Look up a single league by numeric ID from the MongoDB leagues cache."""
    from cache import COL_LEAGUES
    try:
        doc = await db[COL_LEAGUES].find_one(
            {"leagueId": league_id},
            {"_id": 0, "name": 1, "country": 1}
        )
        if doc and doc.get("name"):
            return {"id": league_id, "name": doc["name"], "country": doc.get("country", "")}
    except Exception:
        pass
    return {"id": league_id, "name": "", "country": ""}


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
