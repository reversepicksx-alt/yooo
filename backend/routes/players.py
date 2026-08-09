import asyncio as aio
import re
import time
import unicodedata
from fastapi import APIRouter

from config import CURRENT_SEASON, NWSL_LEAGUE_ID, NWSL_SEASON, db
from models import PlayerSearchRequest, PlayerRoleResolveRequest
from utils import api_football_request, priority_api_football_request, is_quota_exhausted

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

async def _search_players_cache(
    query: str,
    league_id: int = None,
    relaxed: bool = False,
    fast: bool = False,
) -> list:
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

    # Build filter: every word in the query must appear in nameClean.
    # For words ≥ 6 chars we also accept the word without its final character
    # so that a query like "aguilar" still finds nameClean entries that store
    # "aguila" (Eduardo Águila vs Paolo García Aguilar).  Using the N-1 prefix
    # catches both forms without opening up false positives for short words.
    def _flex_regex(w: str) -> str:
        return re.escape(w[:-1]) if len(w) >= 6 else re.escape(w)

    if len(parts) == 1:
        name_filt: dict = {"nameClean": {"$regex": _flex_regex(parts[0])}}
    else:
        name_filt = {"$and": [{"nameClean": {"$regex": _flex_regex(w)}} for w in parts]}

    # Don't filter cache by tournament/cup league IDs — players are stored
    # under their club leagues, not the competition they appeared in.
    effective_league_id = None if (league_id in _TOURNAMENT_LEAGUES) else league_id

    # Use the same context preference in the fast path as the full cache
    # search: a real club row should beat an international/friendly row for
    # the same player (for example Inter Miami should beat Argentina for Messi).
    _LEAGUE_RANK = {39: 0, 140: 1, 135: 2, 78: 3, 61: 4}
    def _doc_rank(d: dict) -> int:
        lg = d.get("leagueId", 0)
        if lg in _LEAGUE_RANK:
            return _LEAGUE_RANK[lg]
        if lg in _INTL_LEAGUES:
            return 98
        if lg == 667 or lg == 0:
            return 99
        return 50

    filt = dict(name_filt)
    if effective_league_id:
        filt["leagueId"] = effective_league_id

    docs = await db[COL_PLAYERS].find(filt, {"_id": 0}).limit(20).to_list(20)

    # If league-constrained search returned nothing, retry without the league filter
    if not docs and effective_league_id:
        docs = await db[COL_PLAYERS].find(name_filt, {"_id": 0}).limit(20).to_list(20)

    if fast and docs:
        # Interactive typing only needs a direct name match. The full fallback
        # tree below is valuable for maintenance/search repair, but it is too
        # expensive to put on the keystroke path.
        best_docs = {}
        for d in docs:
            pid = d.get("playerId", 0)
            if not pid:
                continue
            current = best_docs.get(pid)
            if current is None or _doc_rank(d) < _doc_rank(current):
                best_docs[pid] = d
        results = []
        for d in best_docs.values():
            name = d.get("name", "")
            results.append({
                "id": d.get("playerId", 0),
                "name": name,
                "firstname": name.split()[0] if name.split() else "",
                "lastname": name.split()[-1] if name.split() else "",
                "age": d.get("age", 0) or 0,
                "nationality": d.get("nationality", "") or "",
                "photo": d.get("photo", ""),
                "teamId": d.get("teamId", 0),
                "teamName": d.get("teamName", ""),
                "leagueId": d.get("leagueId", 0),
                "position": d.get("position", ""),
            })
        return results

    if fast and len(parts) > 1:
        # One targeted rescue for abbreviated names such as
        # "J. David" when the user typed "Jonathan David".
        first_initial = parts[0][0] if parts[0] else ""
        last_part = parts[-1]
        if first_initial:
            abbrev_pattern = rf"^{re.escape(first_initial)}\..*{re.escape(last_part)}(?:\s|$)"
            abbrev_filt: dict = {"nameClean": {"$regex": abbrev_pattern}}
            if effective_league_id:
                abbrev_filt["leagueId"] = effective_league_id
            docs = await db[COL_PLAYERS].find(
                abbrev_filt, {"_id": 0}
            ).limit(20).to_list(20)
            if not docs and effective_league_id:
                docs = await db[COL_PLAYERS].find(
                    {"nameClean": {"$regex": abbrev_pattern}}, {"_id": 0}
                ).limit(20).to_list(20)
            if docs:
                best_docs = {}
                for d in docs:
                    pid = d.get("playerId", 0)
                    if not pid:
                        continue
                    current = best_docs.get(pid)
                    if current is None or _doc_rank(d) < _doc_rank(current):
                        best_docs[pid] = d
                results = []
                for d in best_docs.values():
                    name = d.get("name", "")
                    results.append({
                        "id": d.get("playerId", 0),
                        "name": name,
                        "firstname": name.split()[0] if name.split() else "",
                        "lastname": name.split()[-1] if name.split() else "",
                        "age": d.get("age", 0) or 0,
                        "nationality": d.get("nationality", "") or "",
                        "photo": d.get("photo", ""),
                        "teamId": d.get("teamId", 0),
                        "teamName": d.get("teamName", ""),
                        "leagueId": d.get("leagueId", 0),
                        "position": d.get("position", ""),
                    })
                return results

        # No direct cache hit; let the bounded lookup try the
        # canonical full name rather than entering the broad cache tree.
        return []

    # Pass A2 — sequence regex for exactly-2-word queries.
    # Catches compound-name players where the query words are non-adjacent.
    # e.g. "Jonathan Jesus" → /jonathan.*jesus/ matches "Jonathan de Jesus Alves"
    #      "Van Ven"        → /van.*ven/        matches "Micky van de Ven"
    # Run ALWAYS (merges with AND hits) so the quality-filter dedup picks the best.
    if len(parts) == 2:
        seq_pattern = rf"{re.escape(parts[0])}.*{re.escape(parts[1])}"
        seq_filt: dict = {"nameClean": {"$regex": seq_pattern}}
        if effective_league_id:
            seq_filt["leagueId"] = effective_league_id
        seq_docs = await db[COL_PLAYERS].find(seq_filt, {"_id": 0}).limit(20).to_list(20)
        if not seq_docs and effective_league_id:
            seq_docs = await db[COL_PLAYERS].find(
                {"nameClean": {"$regex": seq_pattern}}, {"_id": 0}
            ).limit(20).to_list(20)
        docs.extend(seq_docs or [])

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

        # Pass B — targeted abbreviated initial search. The surname does not
        # have to be the final token: "N. Fernández Mercau" must be found by
        # "Nicolas Fernandez" just like "N. Fernández".
        # Catches "J. David" even when it sits beyond position 100 in Pass A.
        # The word boundary prevents "N. Fernandezson" from matching.
        # Also brings in the non-friendly leagueId entries (e.g. Canada leagueId=10)
        # so the dedup step can prefer them over friendlies (leagueId=667).
        if first_initial:
            abbrev_pattern = rf"^{re.escape(first_initial)}\..*{re.escape(last_part)}(?:\s|$)"
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

        # Pass D — first-name fallback for abbreviated/stale cache records.
        # Some squad-cache entries only contain the short display name
        # ("Jacy"), while API/profile results contain the full name
        # ("Jacy Maranhão Oliveira"). A query such as "Jacy Oliveira" must
        # still surface that cached player instead of returning unrelated
        # players whose surname happens to be Oliveira.
        first_part = parts[0]
        first_name_filt: dict = {
            "$or": [
                {"firstNameClean": {"$regex": rf"^{re.escape(first_part)}"}},
                {"nameClean": {"$regex": rf"^{re.escape(first_part)}(?:\s|$)"}},
            ]
        }
        if effective_league_id:
            first_name_filt["leagueId"] = effective_league_id
        first_docs = await db[COL_PLAYERS].find(
            first_name_filt, {"_id": 0}
        ).limit(30).to_list(30)
        if not first_docs and effective_league_id:
            first_docs = await db[COL_PLAYERS].find(
                {
                    "$or": [
                        {"firstNameClean": {"$regex": rf"^{re.escape(first_part)}"}},
                        {"nameClean": {"$regex": rf"^{re.escape(first_part)}(?:\s|$)"}},
                    ]
                },
                {"_id": 0},
            ).limit(30).to_list(30)
        docs.extend(first_docs or [])

    # Deduplicate by playerId: for each player keep the best-ranked entry.
    # Priority: top-5 club leagues > other real leagues > 667 friendlies.
    # leagueId=667 (Friendlies) entries are often "opponent team" artefacts
    # (e.g. Jonathan David shows as "Juventus" from a Canada-vs-Juventus
    # friendly because the fixture is filed under Juventus's fixture).
    # By ranking 667 lowest we keep the entry that reflects the player's
    # actual team (national team, lower league, etc.) over the opponent.
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
            "photo": d.get("photo", ""),
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
    # NWSL is a calendar-year league. Do not let a client/default 2025 season
    # hide valid 2026 NWSL player IDs from API-Football.
    season = NWSL_SEASON if req.league_id == NWSL_LEAGUE_ID else (req.season or CURRENT_SEASON)
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
            "photo": p.get("photo", ""),
            "teamId": team_id,
            "teamName": team_name,
            "leagueId": league_id,
            "position": position,
        }

    quota_gone = is_quota_exhausted()
    is_owner_search = False
    if req.email and req.token:
        try:
            session = await db.sessions.find_one(
                {
                    "email": req.email.lower().strip(),
                    "session_token": req.token,
                    "access_type": "Owner",
                },
                {"_id": 1},
            )
            is_owner_search = bool(session)
        except Exception:
            is_owner_search = False

    async def _attach_owner_media(player_list: list[dict]) -> list[dict]:
        """Add search media only to a currently authenticated owner response."""
        if not is_owner_search or not player_list:
            return player_list
        try:
            from cache import COL_PLAYERS
            player_ids = {p.get("id") or p.get("playerId") for p in player_list if p.get("id") or p.get("playerId")}
            team_ids = {p.get("teamId") for p in player_list if p.get("teamId")}
            photos: dict[int, str] = {}
            logos: dict[int, str] = {}
            if player_ids:
                async for doc in db[COL_PLAYERS].find(
                    {"playerId": {"$in": list(player_ids)}},
                    {"_id": 0, "playerId": 1, "photo": 1},
                ):
                    if doc.get("photo"):
                        photos[doc.get("playerId")] = doc["photo"]
            if team_ids:
                async for doc in db["cache_teams"].find(
                    {"teamId": {"$in": list(team_ids)}},
                    {"_id": 0, "teamId": 1, "logo": 1},
                ):
                    if doc.get("logo"):
                        logos[doc.get("teamId")] = doc["logo"]
            for player in player_list:
                pid = player.get("id") or player.get("playerId")
                tid = player.get("teamId")
                photo = player.get("photo") or photos.get(pid, "")
                logo = logos.get(tid, "")
                if photo:
                    player["ownerPlayerPhoto"] = photo
                if logo:
                    player["ownerTeamLogo"] = logo
        except Exception as exc:
            print(f"[PLAYER SEARCH] owner media skipped: {exc}")
        return player_list
    # This endpoint is called directly while a user is typing. Background
    # maintenance may consume the local soft budget, but that must not make an
    # uncached player look like a genuine no-result. Priority bypasses only the
    # local maintenance budget; api_football_request still enforces the real
    # daily provider-quota breaker.
    search_api_request = priority_api_football_request

    # Sort helpers — defined early so they can be applied to cache hits too.
    def _strip(s):
        return ''.join(c for c in unicodedata.normalize('NFD', s) if unicodedata.category(c) != 'Mn')
    query_parts = [_strip(w.lower()) for w in req.query.strip().split()]
    _TOP5_LEAGUES = {39, 140, 135, 78, 61}   # EPL, LaLiga, SerieA, Bund., Ligue1
    _NICKNAME_ALIASES = {
        "andy": {"andrew"}, "danny": {"daniel"}, "drew": {"andrew"},
        "jack": {"john", "johnathan", "johnny"}, "jake": {"jacob"},
        "jimmy": {"james"}, "joey": {"joseph"}, "jon": {"jonathan"},
        "josh": {"joshua"}, "katie": {"katherine", "kathryn"},
        "liz": {"elizabeth"}, "lizzy": {"elizabeth"}, "matt": {"matthew"},
        "matty": {"matthew"}, "mike": {"michael"}, "nick": {"nicholas"},
        "rob": {"robert"}, "sammy": {"samuel", "samantha"}, "steve": {"steven", "stephen"},
        "tom": {"thomas"}, "tommy": {"thomas"}, "will": {"william"},
    }

    def _name_matches_query(name_norm: str) -> bool:
        """Strict full-name match with a bounded first-name nickname rescue."""
        name_words = set(name_norm.split())
        if all(word in name_words for word in query_parts):
            return True
        if len(query_parts) < 2:
            return False
        first_aliases = _NICKNAME_ALIASES.get(query_parts[0], set())
        if bool(first_aliases) and any(alias in name_words for alias in first_aliases) and all(
            word in name_words for word in query_parts[1:]
        ):
            return True
        # Prefix rescue: "aguilar" should match a player whose nameClean stores
        # "aguila" (Eduardo Águila Castro).  When the query word starts with a
        # name word of ≥ 4 chars the two are treated as equivalent, so a 1-char
        # trailing suffix difference (common in Spanish name variants) does not
        # cause the correct player to be dropped in favour of a stranger whose
        # full surname happens to match exactly.
        def _prefix_ok(qw: str) -> bool:
            if qw in name_words:
                return True
            return len(qw) >= 5 and any(
                qw.startswith(nw) for nw in name_words if len(nw) >= 4
            )
        return all(_prefix_ok(qw) for qw in query_parts)

    def sort_key(p):
        has_team    = 0 if p.get("teamName") else 1
        name_norm   = _strip((p.get("name") or "").lower())
        first_norm  = _strip((p.get("firstname") or "").lower())
        name_words  = set(name_norm.split())
        nickname_rescued = (
            not all(w in name_norm for w in query_parts)
            and _name_matches_query(name_norm)
        )
        all_match   = 0 if _name_matches_query(name_norm) else 1

        # Prefix-rescue flag: query word starts with a stored name word (≥4 chars).
        # Used so "aguilar" → "aguila" is treated as a valid match in exact_word
        # (Eduardo Águila Castro should not be penalised relative to a player whose
        # surname literally is "Aguilar").
        prefix_rescued = (
            all_match == 0
            and not all(w in name_words for w in query_parts)
            and all(
                w in name_words or (
                    len(w) >= 5 and any(w.startswith(nw) for nw in name_words if len(nw) >= 4)
                )
                for w in query_parts
            )
        )

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
        exact_word  = 0 if (
            nickname_rescued
            or abbrev_rescued
            or middle_rescued
            or prefix_rescued
            or all(w in name_words for w in query_parts)
        ) else 1

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
        # A full first-name prefix is stronger than the generic top-league
        # preference. For "Jacy Oliveira", the cached short-name record
        # "Jacy" should beat unrelated top-league players named Oliveira.
        first_name_match = first_match
        top_league  = 0 if p.get("leagueId") in _TOP5_LEAGUES else 1
        # Initial-match: handles abbreviated names like "R. Jiménez" when the user
        # types "Raul Jimenez". If the stored name's first letter == query first letter,
        # rank it above other same-surname players whose initial doesn't match.
        stored_initial = name_norm.split()[0][0] if name_norm.split() else ""
        query_initial  = query_parts[0][0] if query_parts else ""
        initial_match  = 0 if (stored_initial and query_initial and stored_initial == query_initial) else 1
        return (all_match, reversed_penalty, exact_word, first_name_match, top_league, has_team,
                initial_match, first_match, p["name"])

    def _apply_sort_and_quality(player_list):
        player_list.sort(key=sort_key)
        if len(query_parts) > 1:
            # A multi-word name search is an AND query. Never show a list of
            # unrelated surname/first-name partials just because the upstream
            # provider returned something for one token (e.g. "Jonathan
            # Jesus" must not become a list of players named only Jonathan).
            def _direct_or_abbreviated_match(p):
                name_norm = _strip((p.get("name") or "").lower())
                if _name_matches_query(name_norm):
                    return True
                stored_tokens = name_norm.split()
                first_token = stored_tokens[0] if stored_tokens else ""
                is_initial = (
                    len(first_token) <= 2
                    and first_token.rstrip(".").isalpha()
                )
                return (
                    len(query_parts) >= 2
                    and is_initial
                    and first_token.rstrip(".") == query_parts[0][0]
                    and all(w in name_norm for w in query_parts[1:])
                )

            direct_matches = [p for p in player_list if _direct_or_abbreviated_match(p)]
            if direct_matches:
                # If the canonical full name exists, do not mix in middle-name
                # or surname-only rescue records such as "J. Alves".
                player_list = direct_matches
            else:
                # Preserve the intentional abbreviated cache behavior for
                # records such as "Jacy" searched as "Jacy Oliveira", but
                # only when that short first-name result is unambiguous.
                q_first = query_parts[0]
                short_first_matches = [
                    p for p in player_list
                    if _strip((p.get("name") or "").lower()).strip() == q_first
                ]
                player_list = short_first_matches if len(short_first_matches) == 1 else []
        elif any(sort_key(p)[0] == 0 for p in player_list):
            # Drop partial matches when any perfect (all-words) match exists.
            player_list = [p for p in player_list if sort_key(p)[0] == 0]
        return player_list[:15]

    def _mask_unverified_team(player_list):
        """Keep search useful without presenting cache data as current club.

        Search is an identity step. The selected player gets a synchronous
        current-club verification from /players/{id}/contexts. Until then,
        cache/provider team fields are intentionally hidden so an old club
        cannot be mistaken for a confirmed transfer destination.
        """
        for player in player_list:
            player["teamId"] = 0
            player["teamName"] = ""
            player["leagueId"] = 0
            player["teamVerified"] = False
        return player_list

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

    async def _enrich_abbreviated_player(p: dict) -> dict:
        """Replace a squad abbreviation with the provider's canonical name.

        Squad feeds commonly return ``N. Fernández Mercau`` while the player
        profile has ``Nicolás Ezequiel Fernández Mercau``.  The abbreviated
        record is still useful for finding the player, but it is not sufficient
        for UI disambiguation or reliable club selection.
        """
        name = (p.get("name") or "").strip()
        tokens = name.split()
        if not tokens or len(tokens[0].rstrip(".")) > 2 or not tokens[0].rstrip(".").isalpha():
            return p
        pid = p.get("id")
        if not pid:
            return p
        try:
            profile = None
            for season_to_try in [CURRENT_SEASON + 1, CURRENT_SEASON, CURRENT_SEASON - 1]:
                data = await priority_api_football_request(
                    "players", {"id": pid, "season": season_to_try}
                )
                if data:
                    profile = data[0]
                    break
            if not profile:
                return p

            player_info = profile.get("player") or {}
            firstname = (player_info.get("firstname") or "").strip()
            lastname = (player_info.get("lastname") or "").strip()
            canonical_name = f"{firstname} {lastname}".strip() or player_info.get("name", "")
            if not canonical_name:
                return p

            stats = profile.get("statistics") or []
            cached_team_id = p.get("teamId")
            selected_stat = next(
                (s for s in reversed(stats)
                 if cached_team_id and (s.get("team") or {}).get("id") == cached_team_id),
                None,
            )
            selected_stat = selected_stat or next(
                (s for s in reversed(stats)
                 if (s.get("team") or {}).get("id") and
                 (s.get("league") or {}).get("id") not in _INTL_LEAGUES and
                 (s.get("league") or {}).get("id") != 667),
                None,
            )
            selected_stat = selected_stat or (stats[-1] if stats else {})
            team = selected_stat.get("team") or {}
            league = selected_stat.get("league") or {}
            games = selected_stat.get("games") or {}

            enriched = dict(p)
            enriched.update({
                "name": canonical_name,
                "firstname": firstname,
                "lastname": lastname,
                "age": player_info.get("age", p.get("age", 0)),
                "photo": player_info.get("photo") or p.get("photo", ""),
                "position": games.get("position") or player_info.get("position") or p.get("position", ""),
            })
            if team.get("id"):
                enriched["teamId"] = team["id"]
                enriched["teamName"] = team.get("name", "")
            if league.get("id"):
                enriched["leagueId"] = league["id"]

            # Persist the canonical name for every context, but only create a
            # new current-team context when the profile says the player moved.
            from cache import COL_PLAYERS
            from utils import strip_accents
            name_clean = strip_accents(canonical_name.lower())
            await db[COL_PLAYERS].update_many(
                {"playerId": pid},
                {"$set": {
                    "name": canonical_name,
                    "nameLower": canonical_name.lower(),
                    "nameClean": name_clean,
                    "firstNameClean": strip_accents(firstname.lower()),
                    "age": enriched.get("age"),
                    "photo": enriched.get("photo", ""),
                }},
            )
            if team.get("id") and league.get("id"):
                await db[COL_PLAYERS].update_one(
                    {"playerId": pid, "teamId": team["id"], "leagueId": league["id"]},
                    {"$set": {
                        "name": canonical_name,
                        "nameLower": canonical_name.lower(),
                        "nameClean": name_clean,
                        "firstNameClean": strip_accents(firstname.lower()),
                        "teamName": team.get("name", ""),
                        "position": enriched.get("position", ""),
                        "_cachedAt": time.time(),
                    }},
                    upsert=True,
                )
            return enriched
        except Exception as exc:
            print(f"[PLAYER NAME ENRICH] pid={pid} err={exc}")
            return p

    # Strategy 0: MongoDB cache-first (fast, no quota usage)
    # When quota is exhausted use relaxed mode — accept any name-matched player
    # (e.g. Messi cached under Inter Miami, not World Cup league_id=1).
    try:
        # Searching while the user is typing must never wait for every cache
        # fallback or a slow Atlas query. A timed-out cache lookup falls
        # through to one bounded provider lookup below.
        cache_results = await aio.wait_for(
            _search_players_cache(
                req.query,
                req.league_id,
                relaxed=quota_gone,
                fast=True,
            ),
            timeout=0.85,
        )
        if cache_results:
            # Cache-first results can be abbreviated squad records. Resolve a
            # small bounded set after the response so profile enrichment never
            # makes the name dropdown wait on several API calls.
            if not quota_gone:
                original_cache_results = list(cache_results)
                abbreviated = [
                    p for p in cache_results
                    if (
                        len(query_parts) >= 2
                        and (p.get("name") or "").split()
                        and len((p.get("name") or "").split()[0].rstrip(".")) <= 2
                        and (p.get("name") or "").split()[0].rstrip(".").lower() == query_parts[0][0]
                        and all(
                            word in _strip((p.get("name") or "").lower())
                            for word in query_parts[1:]
                        )
                    )
                ][:20]
                if abbreviated:
                    async def _background_abbrev_enrichment(items):
                        try:
                            await aio.gather(
                                *[_enrich_abbreviated_player(p) for p in items],
                                return_exceptions=True,
                            )
                        except Exception:
                            pass
                    aio.ensure_future(_background_abbrev_enrichment(abbreviated))
            sorted_results = _apply_sort_and_quality(cache_results)
            # Older squad-cache rows may have the right club/photo but no
            # nationality. Fill that metadata with one bounded profiles
            # request, then persist it so this is not repeated on every
            # keystroke. The lookup is optional and never blocks the search
            # longer than the interactive budget.
            missing_nationality = [
                p for p in sorted_results[:15] if not p.get("nationality")
            ]
            if missing_nationality and not quota_gone:
                try:
                    profile_data = await aio.wait_for(
                        search_api_request("players/profiles", {"search": req.query}),
                        timeout=0.65,
                    )
                    profile_by_id = {}
                    for item in profile_data or []:
                        profile = extract_player(item)
                        if profile.get("id"):
                            profile_by_id[profile["id"]] = profile
                    metadata_updates = []
                    for player in missing_nationality:
                        profile = profile_by_id.get(player.get("id"))
                        if not profile:
                            continue
                        player["nationality"] = profile.get("nationality") or ""
                        player["photo"] = player.get("photo") or profile.get("photo") or ""
                        if player["nationality"]:
                            metadata_updates.append((player["id"], player["nationality"], player["photo"]))
                    if metadata_updates:
                        async def _persist_search_metadata(updates):
                            try:
                                from cache import COL_PLAYERS
                                for pid, nationality, photo in updates:
                                    fields = {"nationality": nationality}
                                    if photo:
                                        fields["photo"] = photo
                                    await db[COL_PLAYERS].update_many(
                                        {"playerId": pid},
                                        {"$set": fields},
                                    )
                            except Exception as exc:
                                print(f"[PLAYER SEARCH] metadata cache write skipped: {exc}")
                        aio.ensure_future(_persist_search_metadata(metadata_updates))
                except (aio.TimeoutError, TimeoutError):
                    print(f"[PLAYER SEARCH] metadata lookup exceeded 650ms for {req.query!r}")
                except Exception as exc:
                    print(f"[PLAYER SEARCH] metadata lookup failed for {req.query!r}: {exc}")
            # Background enrichment: if any top result still shows a national/intl
            # league entry (meaning no club entry won the dedup), fire club resolution
            # off the hot path — this request still returns quickly, but the NEXT
            # search for this player will show the correct club (permanent fix).
            if not quota_gone:
                intl_hits = [p for p in sorted_results[:5] if p.get("leagueId") in _INTL_LEAGUES]
                if intl_hits:
                    async def _bg_enrich(players: list):
                        for p in players:
                            try:
                                await _resolve_club_for_intl_player(p)
                            except Exception as _e:
                                print(f"[BG-ENRICH] pid={p.get('id')} err={_e}")
                    aio.ensure_future(_bg_enrich(intl_hits))

                # Also refresh stale club entries — catches transferred players
                # whose cache still shows the old team (e.g. Salah at Liverpool
                # after moving to Trabzonspor).  Only fire for entries older
                # than 30 days to avoid quota burn on recent/accurate results.
                _now_ts = time.time()
                stale_club_hits = [
                    p for p in sorted_results[:5]
                    if p.get("leagueId") not in _INTL_LEAGUES
                    and (_now_ts - (p.get("_cachedAt") or 0)) > 30 * 86400
                ]
                if stale_club_hits:
                    async def _bg_refresh_stale(players: list):
                        for p in players:
                            try:
                                await _resolve_club_for_intl_player(p)
                            except Exception as _e:
                                print(f"[BG-CLUB-STALE] pid={p.get('id')} err={_e}")
                    aio.ensure_future(_bg_refresh_stale(stale_club_hits))

            return {"players": await _attach_owner_media(sorted_results)}
    except (aio.TimeoutError, TimeoutError):
        print(f"[PLAYER SEARCH] cache lookup exceeded 850ms for {req.query!r}; using fast provider path")
    except Exception as exc:
        print(f"[PLAYER SEARCH] cache lookup failed for {req.query!r}: {exc}")

    # If quota is gone, try last-name cache fallback then BDL search before giving up.
    # Handles abbreviated cached names like "R. Jiménez" when user types "Raul Jimenez".
    if quota_gone:
        if " " in req.query.strip():
            last_word = req.query.strip().split()[-1]
            if len(last_word) >= 3:
                try:
                    fallback = await _search_players_cache(last_word, req.league_id, relaxed=True)
                    if fallback:
                        fallback_players = _apply_sort_and_quality(fallback)
                        return {"players": _mask_unverified_team(await _attach_owner_media(fallback_players))}
                except Exception:
                    pass
        # BDL live search — covers EPL, La Liga, Serie A, Bundesliga, Ligue 1,
        # UCL, MLS, World Cup without any API-Football dependency. Keep this
        # bounded because it is still on the typing path.
        try:
            from soccer_bdl_client import search_bdl_players
            bdl_hits = await aio.wait_for(search_bdl_players(req.query), timeout=1.25)
            if bdl_hits:
                bdl_players = _apply_sort_and_quality(bdl_hits)
                return {"players": _mask_unverified_team(await _attach_owner_media(bdl_players))}
        except (aio.TimeoutError, TimeoutError):
            print(f"[PLAYER SEARCH] BDL lookup exceeded 1250ms for {req.query!r}")
        except Exception:
            pass
        return {"players": []}

    # Fast interactive provider path. The previous implementation could issue
    # dozens of sequential league/season/profile fallbacks after a cache miss,
    # leaving users staring at a spinner for 7–40 seconds. One targeted lookup
    # is enough for the dropdown; full club/context enrichment happens after
    # the user selects the player.
    fast_params = {"search": req.query}
    # Profile search is the provider's fastest identity lookup and works for
    # both global and league-scoped typing. The old league/season request
    # could silently query a future season and return nothing (for example
    # Salah in EPL), while the exact cached context lookup below supplies the
    # club identity.
    try:
        live_data = await aio.wait_for(
            search_api_request("players/profiles", fast_params),
            timeout=1.75,
        )
    except (aio.TimeoutError, TimeoutError):
        print(f"[PLAYER SEARCH] provider lookup exceeded 1750ms for {req.query!r}")
        return {"players": []}
    except Exception as exc:
        print(f"[PLAYER SEARCH] provider lookup failed for {req.query!r}: {exc}")
        return {"players": []}

    live_players = [extract_player(item) for item in (live_data or [])]
    seen_live_ids = set()
    live_players = [
        p for p in live_players
        if p.get("id") and not (p["id"] in seen_live_ids or seen_live_ids.add(p["id"]))
    ]

    # The profiles endpoint intentionally returns identity data without
    # statistics. Recover the current/best cached club context with one
    # indexed playerId query, rather than making several provider calls or
    # re-entering the old league/season fallback chain.
    missing_context = [p for p in live_players if not p.get("teamName")]
    if missing_context:
        try:
            from cache import COL_PLAYERS
            context_ids = [p["id"] for p in missing_context[:15]]
            context_docs = await aio.wait_for(
                db[COL_PLAYERS].find(
                    {"playerId": {"$in": context_ids}},
                    {"_id": 0, "playerId": 1, "teamId": 1, "teamName": 1,
                     "leagueId": 1, "position": 1, "photo": 1,
                     "nationality": 1, "_cachedAt": 1},
                ).to_list(150),
                timeout=0.9,
            )
            _context_rank = {39: 0, 140: 1, 135: 2, 78: 3, 61: 4}

            def _context_key(d):
                league = d.get("leagueId", 0)
                if league in _context_rank:
                    rank = _context_rank[league]
                elif league in _INTL_LEAGUES or league in {667, 0}:
                    rank = 98
                else:
                    rank = 50
                return (rank, -(d.get("_cachedAt") or 0))

            best_context = {}
            for doc in context_docs or []:
                if not doc.get("teamName"):
                    continue
                pid = doc.get("playerId")
                current = best_context.get(pid)
                if current is None or _context_key(doc) < _context_key(current):
                    best_context[pid] = doc
            for player in live_players:
                context = best_context.get(player.get("id"))
                if not context:
                    continue
                player.update({
                    "teamId": context.get("teamId") or 0,
                    "teamName": context.get("teamName") or "",
                    "leagueId": context.get("leagueId") or 0,
                    "position": player.get("position") or context.get("position") or "",
                    "photo": player.get("photo") or context.get("photo") or "",
                    "nationality": player.get("nationality") or context.get("nationality") or "",
                })
        except (aio.TimeoutError, TimeoutError):
            print(f"[PLAYER SEARCH] exact context lookup exceeded 900ms for {req.query!r}")
        except Exception as exc:
            print(f"[PLAYER SEARCH] exact context lookup failed for {req.query!r}: {exc}")

    live_players = _apply_sort_and_quality(live_players)
    return {"players": _mask_unverified_team(await _attach_owner_media(live_players))}

    all_players = []

    # For World Cup (league_id=1) and other tournament leagues the relevant
    # seasons are fixed WC years, not current club season.
    _WC_SEASONS = [2026, 2022, 2018, 2014]

    # Strategy 1: Search within specified league
    if req.league_id:
        seasons_to_try = (
            _WC_SEASONS if req.league_id == 1
            else ([NWSL_SEASON, NWSL_SEASON - 1, NWSL_SEASON - 2]
                  if req.league_id == NWSL_LEAGUE_ID
                  else [season + 1, season, season - 1, season - 2])
        )

        async def search_season(s):
            try:
                data = await search_api_request("players", {"search": req.query, "league": req.league_id, "season": s})
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
                    data = await search_api_request("players", {"search": req.query, "league": req.league_id, "season": s})
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
                    data = await search_api_request("players", {"search": last_name, "league": req.league_id, "season": s})
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
                        data = await search_api_request("players", {"search": last_name, "league": req.league_id, "season": s})
                        if data:
                            all_players.extend([extract_player(item) for item in data])
                            break
                    except Exception:
                        continue

    # Strategy 2: major domestic leagues + Copa Lib/Sud + all SA leagues + women's leagues
    if not all_players and not quota_gone:
        major_leagues = [
            39, 140, 135, 78, 61,   # EPL, La Liga, Serie A, Bundesliga, Ligue 1
            253, 71, 307,            # MLS, Brasileirao, Saudi Pro
            13, 11,                  # Copa Libertadores, Copa Sudamericana
            128, 242, 239, 265,      # Argentina, Ecuador, Colombia, Chile
            270, 281, 299, 250, 21,  # Uruguay, Peru, Venezuela, Paraguay, Bolivia
            254, 172, 189,           # NWSL, WSL (England Women), A-League Women
        ]
        async def try_league(lid):
            search_seasons = (
                [NWSL_SEASON, NWSL_SEASON - 1]
                if lid == NWSL_LEAGUE_ID
                else [season + 1, season]
            )
            for s in search_seasons:
                try:
                    data = await search_api_request("players", {"search": req.query, "league": lid, "season": s})
                    if data:
                        return [extract_player(item) for item in data]
                except Exception:
                    continue
            return []
        results = await aio.gather(*[try_league(lid) for lid in major_leagues])
        for r in results:
            all_players.extend(r)

    # Strategy 2b — last-word search in major leagues.
    # When the full query "Jonathan Jesus" returns nothing (because API-Football
    # does a substring match and "Jonathan Jesus" ≠ "Jonathan de Jesus Alves"),
    # retry each major league with just the LAST word "Jesus".  The quality
    # filter downstream keeps "Jonathan de Jesus Alves" because both "jonathan"
    # and "jesus" appear in his nameClean.
    if not all_players and not quota_gone and " " in req.query:
        last_word_q = req.query.strip().split()[-1]
        if len(last_word_q) >= 3:  # skip trivially short tokens
            async def try_league_lw(lid):
                search_seasons_lw = (
                    [NWSL_SEASON, NWSL_SEASON - 1]
                    if lid == NWSL_LEAGUE_ID
                    else [season + 1, season]
                )
                for s in search_seasons_lw:
                    try:
                        data = await search_api_request(
                            "players", {"search": last_word_q, "league": lid, "season": s}
                        )
                        if data:
                            return [extract_player(item) for item in data]
                    except Exception:
                        continue
                return []
            results_2b = await aio.gather(*[try_league_lw(lid) for lid in major_leagues[:10]])
            for r in results_2b:
                all_players.extend(r)

    # Strategy 3: profiles
    if not all_players and not quota_gone:
        try:
            data = await search_api_request("players/profiles", {"search": req.query})
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
            data = await search_api_request("players/profiles", {"search": fl_query})
            if data:
                all_players.extend([extract_player(item) for item in data])
        except Exception:
            pass

    # Strategy 4: last name from profiles
    if not all_players and not quota_gone and " " in req.query:
        last_name = req.query.strip().split()[-1]
        try:
            data = await search_api_request("players/profiles", {"search": last_name})
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
                        "nationality": pl.get("nationality") or "",
                        "photo": pl.get("photo") or "",
                        "teamId": pl.get("teamId") or 0,
                        "teamName": team_name,
                        "leagueId": league_id_val,
                        "position": pl.get("position") or "",
                        "_cachedAt": time.time(),
                    })
                else:
                    # Update teamName/teamId in existing entry if they differ
                    if (existing.get("teamName") != team_name or
                            existing.get("teamId") != pl.get("teamId") or
                            existing.get("nationality") != pl.get("nationality") or
                            existing.get("photo") != pl.get("photo")):
                        await db[COL_PLAYERS].update_one(
                            {"playerId": pid, "leagueId": league_id_val},
                            {"$set": {
                                "teamName": team_name,
                                "teamId": pl.get("teamId") or 0,
                                "nationality": pl.get("nationality") or "",
                                "photo": pl.get("photo") or "",
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


@router.post("/players/resolve-role")
async def resolve_player_role_endpoint(req: PlayerRoleResolveRequest):
    """Resolve and cache a player's specific position + tactical role.

    Called from mobile immediately after a player is selected (before prediction).
    Returns a versioned grounded identity result when available; otherwise
    verifies the player with Gemini web search and caches the evidence for 7 days.

    Request body: { playerId?, playerName, teamName?, genericPosition?, stats? }
    Response:     { position, role, source, cached }
    """
    try:
        from ai_positions import resolve_player_role
        from config import POSITION_PROMPT_VERSION
        from cache import COL_PLAYERS
        from datetime import datetime, timezone

        GENERIC_TO_SPECIFIC = {
            "Goalkeeper": {"GK"},
            "Defender": {"CB", "LB", "RB", "LWB", "RWB"},
            "Midfielder": {"CDM", "CM", "CAM", "LM", "RM"},
            "Attacker": {"LW", "RW", "CF", "ST", "SS", "CAM"},
        }
        DEFAULT_POSITION_ROLE = {
            "Goalkeeper": ("GK", "Shot-Stopper"),
            "Defender": ("CB", "Stopper"),
            "Midfielder": ("CM", "Box-to-Box"),
            "Attacker": ("CF", "Complete Forward"),
        }

        # The search cache is the source for the API's generic category.  Do
        # not let an empty/stale request field make a cached ST/Poacher entry
        # win over a player record that is explicitly marked Defender.
        generic_position = (req.genericPosition or "").strip()
        if not generic_position and req.playerId:
            try:
                player_docs = await db[COL_PLAYERS].find(
                    {"playerId": req.playerId},
                    {"_id": 0, "position": 1},
                ).limit(20).to_list(20)
                for player_doc in player_docs:
                    candidate_position = (player_doc.get("position") or "").strip()
                    if candidate_position in GENERIC_TO_SPECIFIC:
                        generic_position = candidate_position
                        break
            except Exception as cache_error:
                print(f"[RESOLVE ROLE] Generic position lookup failed: {cache_error}")

        # ── Fresh resolution ─────────────────────────────────────────────────
        pos, role, source = await resolve_player_role(
            player_name=req.playerName,
            team_name=req.teamName or "",
            generic_position=generic_position,
            player_id=req.playerId or 0,
            stats=req.stats,
        )

        return {
            "position": pos,
            "role": role,
            "source": source,
            "cached": False,
        }

    except Exception as e:
        print(f"[RESOLVE ROLE] Error for {getattr(req, 'playerName', '?')}: {e}")
        return {"position": "", "role": "", "source": "error", "cached": False}
