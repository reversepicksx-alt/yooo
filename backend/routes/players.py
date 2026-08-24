import asyncio as aio
import re
import time
import unicodedata
from fastapi import APIRouter

from config import CURRENT_SEASON, NWSL_LEAGUE_ID, NWSL_SEASON, db
from models import PlayerSearchRequest, PlayerRoleResolveRequest
from utils import api_football_request, priority_api_football_request, is_quota_exhausted

router = APIRouter(prefix="/api", tags=["players"])

_SEARCH_HOT_PLAYERS: dict[int, dict] = {}
_SEARCH_HOT_PLAYER_TIMES: dict[int, float] = {}
_SEARCH_HOT_QUERIES: dict[tuple[str, int | None], tuple[float, list[dict]]] = {}
_SEARCH_HOT_TTL_SECONDS = 15 * 60
_SEARCH_HOT_MAX_PLAYERS = 5000

# API-Football's global profile search can return a different player with the
# same ASCII name and no current-team statistics. Keep only corrections that
# have been independently verified by the current-club context endpoint. This
# is an identity repair, not a club display source; selection still verifies
# the live club before a matchup is chosen.
_VERIFIED_IDENTITY_SEARCH_OVERRIDES = (
    {
        "id": 118307,
        "name": "Djordje Petrovic",
        "fullName": "Djordje Petrovic",
        "firstname": "Djordje",
        "lastname": "Petrovic",
        "position": "Goalkeeper",
        "teamId": 35,
        "teamName": "Bournemouth",
        "leagueId": 39,
        "aliases": {
            "djordje petrovic",
            "dorde petrovic",
            "petrovic",
        },
    },
    {
        "id": 554362,
        "name": "Iñigo Vicente Elorduy",
        "fullName": "Iñigo Vicente Elorduy",
        "firstname": "Iñigo Vicente",
        "lastname": "Elorduy",
        "position": "Midfielder",
        "teamId": 717,
        "teamName": "Racing Santander",
        "leagueId": 141,
        "aliases": {
            "inigo vicente",
            "iñigo vicente",
            "inigo vicente elorduy",
            "iñigo vicente elorduy",
            "i vicente",
        },
    },
    {
        "id": 2799,
        "name": "Albert Gudmundsson",
        "fullName": "Albert Gudmundsson",
        "firstname": "Albert",
        "lastname": "Guðmundsson",
        "position": "Attacker",
        "teamId": 502,
        "teamName": "Fiorentina",
        "leagueId": 135,
        "aliases": {
            "albert gudmundsson",
            "albert gudmundsson",
        },
    },
)


def _verified_identity_search_override(query: str) -> list[dict]:
    """Return exact known-good identity repairs for a normalized user query."""
    normalized = _hot_query_key(query, None)[0]
    if not normalized:
        return []
    for override in _VERIFIED_IDENTITY_SEARCH_OVERRIDES:
        aliases = override.get("aliases") or set()
        if normalized not in aliases:
            continue
        result = {key: value for key, value in override.items() if key != "aliases"}
        return [result]
    return []


def _hot_query_key(query: str, league_id: int | None) -> tuple[str, int | None]:
    clean = unicodedata.normalize("NFD", query.lower().strip())
    clean = "".join(
        char for char in clean
        if unicodedata.category(char) != "Mn"
    )
    clean = re.sub(r"[^a-z0-9 ]+", " ", clean)
    return (" ".join(clean.split()), league_id)


def _initial_has_full_first_name_evidence(player: dict, query_first: str) -> bool:
    """Allow a provider's ``A. Surname`` only when its full first name is known.

    API-Football profile results sometimes expose just an initial and surname
    with no ``firstname`` metadata. Matching that row to a full-name query
    would turn every same-initial/surname player into a false identity match.
    An explicitly typed one-letter query remains valid; otherwise the provider
    must supply the full first-name token through ``firstname`` or ``fullName``.
    """
    normalized_query, _ = _hot_query_key(query_first, None)
    if len(normalized_query) <= 1:
        return bool(normalized_query)
    evidence = " ".join(
        str(player.get(field) or "")
        for field in ("firstname", "fullName")
    )
    normalized_evidence, _ = _hot_query_key(evidence, None)
    return normalized_query in normalized_evidence.split()


def _remember_hot_players(players: list[dict]) -> None:
    """Keep recently resolved identities available without another Atlas read."""
    now = time.monotonic()
    for player in players:
        pid = player.get("id") or player.get("playerId")
        if not pid or not (player.get("name") or player.get("fullName")):
            continue
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            continue
        _SEARCH_HOT_PLAYERS[pid] = dict(player)
        _SEARCH_HOT_PLAYER_TIMES[pid] = now

    if len(_SEARCH_HOT_PLAYERS) > _SEARCH_HOT_MAX_PLAYERS:
        expired = sorted(
            _SEARCH_HOT_PLAYER_TIMES,
            key=_SEARCH_HOT_PLAYER_TIMES.get,
        )[: len(_SEARCH_HOT_PLAYERS) - _SEARCH_HOT_MAX_PLAYERS]
        for pid in expired:
            _SEARCH_HOT_PLAYERS.pop(pid, None)
            _SEARCH_HOT_PLAYER_TIMES.pop(pid, None)


def _remember_hot_query(query: str, league_id: int | None, players: list[dict]) -> None:
    if players:
        _SEARCH_HOT_QUERIES[_hot_query_key(query, league_id)] = (
            time.monotonic(),
            [dict(player) for player in players],
        )


def _hot_exact_query(query: str, league_id: int | None) -> list[dict]:
    key = _hot_query_key(query, league_id)
    cached = _SEARCH_HOT_QUERIES.get(key)
    if not cached:
        return []
    updated, players = cached
    if time.monotonic() - updated > _SEARCH_HOT_TTL_SECONDS:
        _SEARCH_HOT_QUERIES.pop(key, None)
        return []
    return [dict(player) for player in players]


def _hot_player_matches(query: str, league_id: int | None) -> list[dict]:
    """Return local identity matches for a previously resolved search."""
    clean_query = unicodedata.normalize("NFD", query.lower().strip())
    clean_query = "".join(
        char for char in clean_query
        if unicodedata.category(char) != "Mn"
    )
    words = [word for word in re.sub(r"[^a-z0-9 ]+", " ", clean_query).split() if word]
    if not words:
        return []

    now = time.monotonic()
    matches: list[dict] = []
    for pid, player in list(_SEARCH_HOT_PLAYERS.items()):
        updated = _SEARCH_HOT_PLAYER_TIMES.get(pid, 0)
        if now - updated > _SEARCH_HOT_TTL_SECONDS:
            _SEARCH_HOT_PLAYERS.pop(pid, None)
            _SEARCH_HOT_PLAYER_TIMES.pop(pid, None)
            continue
        if league_id and player.get("leagueId") not in {league_id, 0, None}:
            continue
        name = player.get("name") or player.get("fullName") or ""
        name_clean = unicodedata.normalize("NFD", str(name).lower())
        name_clean = "".join(
            char for char in name_clean
            if unicodedata.category(char) != "Mn"
        )
        name_clean = re.sub(r"[^a-z0-9 ]+", " ", name_clean)
        if all(
            word in name_clean or (len(word) >= 6 and word[:-1] in name_clean)
            for word in words
        ):
            matches.append(dict(player))
    return matches


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

    # The normal cache query supports arbitrary substrings, but that forces
    # MongoDB to scan the collection. The typing path only needs token-prefix
    # identity matching; anchoring the first token lets the nameClean index
    # answer common first-name and surname searches without a collection scan.
    if fast:
        if len(parts) == 1:
            name_filt = {
                "nameClean": {
                    "$regex": rf"(^| ){_flex_regex(parts[0])}"
                }
            }
        elif parts:
            name_filt = {
                "$and": [
                    {"nameClean": {"$regex": rf"^{_flex_regex(parts[0])}"}},
                    *[
                        {"nameClean": {"$regex": _flex_regex(word)}}
                        for word in parts[1:]
                    ],
                ]
            }

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

    if fast and len(parts) == 1:
        # A substring query capped at 20 can hide a short, exact player name
        # behind longer names that happen to contain the same token. For
        # example, "Ronaldo" was returning Cristiano Ronaldo and other
        # compound names before the standalone Bahia goalkeeper. Merge a
        # bounded exact-word query so ranking sees the complete set of direct
        # matches without opening a broad unbounded cache scan.
        exact_pattern = rf"(^| ){re.escape(parts[0])}( |$)"
        exact_filt: dict = {"nameClean": {"$regex": exact_pattern}}
        if effective_league_id:
            exact_filt["leagueId"] = effective_league_id
        exact_docs = await db[COL_PLAYERS].find(
            exact_filt, {"_id": 0}
        ).limit(50).to_list(50)
        if not exact_docs and effective_league_id:
            exact_docs = await db[COL_PLAYERS].find(
                {"nameClean": {"$regex": exact_pattern}}, {"_id": 0}
            ).limit(50).to_list(50)
        if exact_docs:
            seen_doc_keys = {
                (d.get("playerId"), d.get("teamId"), d.get("leagueId"))
                for d in docs
            }
            docs.extend(
                d for d in exact_docs
                if (d.get("playerId"), d.get("teamId"), d.get("leagueId"))
                not in seen_doc_keys
            )

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
                "fullName": d.get("fullName") or name,
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
                        "fullName": d.get("fullName") or name,
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
            "fullName": d.get("fullName") or name,
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
    # API-Football rejects searches shorter than four alphanumeric characters.
    # The installed iOS client searches on every keystroke, so short prefixes
    # must be handled before touching Atlas or the provider. Otherwise typing
    # traffic can consume the interactive budget and make the eventual full
    # name appear unavailable.
    if len(re.sub(r"[^A-Za-z0-9]", "", req.query)) < 4:
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
            "fullName": display_name,
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
    # This endpoint is called directly while a user is typing. Background
    # maintenance may consume the local soft budget, but that must not make an
    # uncached player look like a genuine no-result. Priority bypasses only the
    # local maintenance budget; api_football_request still enforces the real
    # daily provider-quota breaker.
    search_api_request = priority_api_football_request

    # Sort helpers — defined early so they can be applied to cache hits too.
    def _strip(s):
        return ''.join(c for c in unicodedata.normalize('NFD', s) if unicodedata.category(c) != 'Mn')
    query_parts = [
        re.sub(r"[^a-z0-9]+", "", _strip(w.lower()))
        for w in req.query.strip().split()
    ]
    # A copied/OCR name can contain stacked initials while the provider keeps
    # only the last one (C. K. Rader → K. Rader). Drop only the redundant
    # leading initial from a 3+ part query; the remaining first-initial and
    # surname still form a strict abbreviated-name match.
    while len(query_parts) >= 3 and len(query_parts[0]) == 1 and len(query_parts[1]) == 1:
        query_parts.pop(0)
    # API-Sports rejects punctuation in its `search` parameter. Keep the
    # original normalized words for strict ranking, but send a provider-safe
    # form so OCR names such as "C. K. Rader" can still reach the bounded
    # surname recovery below.
    provider_query = re.sub(r"[^A-Za-z0-9 ]+", " ", _strip(req.query))
    provider_query = re.sub(r"\s+", " ", provider_query).strip()
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
            if (is_initial
                    and _initial_has_full_first_name_evidence(p, query_parts[0])
                    and
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
                nt[0].rstrip(".") == q_first[0] and
                _initial_has_full_first_name_evidence(p, q_first)
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
                    and _initial_has_full_first_name_evidence(p, query_parts[0])
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

    hot_league_id = None if req.league_id in _TOURNAMENT_LEAGUES else req.league_id
    verified_identity_override = _verified_identity_search_override(req.query)
    if verified_identity_override:
        _remember_hot_players(verified_identity_override)
        _remember_hot_query(req.query, hot_league_id, verified_identity_override)
        return {
            "players": _mask_unverified_team(
                await _attach_owner_media(verified_identity_override)
            )
        }

    hot_results = _hot_exact_query(req.query, hot_league_id)
    # A hot exact result with no team is often a provider profile collision
    # rather than a resolved identity (for example, API-Football can return a
    # different "Djordje Petrovic" record for the same two-word query). Let
    # the durable identity/context path disambiguate those rows instead of
    # locking the wrong player into the selection UI.
    hot_identity_only = (
        len(query_parts) >= 2
        and hot_results
        and all(not (p.get("teamId") or p.get("teamName")) for p in hot_results)
    )
    if hot_results and not hot_identity_only:
        return {"players": _mask_unverified_team(await _attach_owner_media(hot_results))}

    hot_results = _hot_player_matches(req.query, hot_league_id)
    if hot_results:
        hot_results = _apply_sort_and_quality(hot_results)
        hot_identity_only = (
            len(query_parts) >= 2
            and all(not (p.get("teamId") or p.get("teamName")) for p in hot_results)
        )
        if hot_results and not hot_identity_only:
            return {"players": _mask_unverified_team(await _attach_owner_media(hot_results))}

    async def _durable_identity_fallback() -> list[dict]:
        """Recover known player identities when provider/cache search is empty.

        Search cache rows can be regenerated and may be removed during Atlas
        quota recovery.  Saved soccer pick identity and verified player
        contexts are durable enough to restore a previously resolved identity
        without guessing a new player from an unrelated sport.
        """
        if not query_parts:
            return []
        try:
            first_word = re.escape(query_parts[0])
            last_word = re.escape(query_parts[-1])
            # Search both display names and normalized names.  The provider
            # and saved ledger can retain diacritics (Đorđe Petrović), while
            # mobile OCR/search commonly sends the ASCII form (Djordje
            # Petrovic).  nameClean is the accent-insensitive index when it
            # exists; the display-name clauses preserve compatibility with
            # older durable rows.
            clean_first = re.escape(query_parts[0])
            clean_last = re.escape(query_parts[-1])
            # A display-name regex cannot match the accented suffix in
            # "Petrović" when the query is ASCII "Petrovic". Use a bounded
            # five-character prefix as a candidate filter, then apply the
            # accent-stripped exact-name check below.
            display_last_prefix = re.escape(query_parts[-1][:5])
            name_filter = {
                "$or": [
                    {"playerName": {"$regex": rf"(^|\s){first_word}", "$options": "i"}},
                    {"playerName": {"$regex": last_word, "$options": "i"}},
                    {"playerName": {"$regex": display_last_prefix, "$options": "i"}},
                    {"nameClean": {"$regex": rf"(^|\s){clean_first}", "$options": "i"}},
                    {"nameClean": {"$regex": clean_last, "$options": "i"}},
                ]
            }
            try:
                position_docs = await aio.wait_for(
                    db.player_positions.find(
                        name_filter,
                        {
                            "_id": 0,
                            "playerId": 1,
                            "playerName": 1,
                            "teamId": 1,
                            "team": 1,
                            "specificPosition": 1,
                            "position": 1,
                        },
                    ).limit(50).to_list(50),
                    timeout=0.75,
                )
            except (aio.TimeoutError, TimeoutError):
                position_docs = []

            # player_positions is itself regenerable. The saved-pick ledger is
            # the durable identity fallback when that optional cache has been
            # purged. Restrict this to soccer and require both the first and
            # last query words so a common surname cannot leak unrelated rows.
            try:
                pick_filter = {
                    "sport": "soccer",
                    "$and": [
                        {"$or": [
                            {"playerName": {"$regex": rf"(^|\s){first_word}", "$options": "i"}},
                            {"nameClean": {"$regex": rf"(^|\s){clean_first}", "$options": "i"}},
                        ]},
                        {"$or": [
                            {"playerName": {"$regex": last_word, "$options": "i"}},
                            {"playerName": {"$regex": display_last_prefix, "$options": "i"}},
                            {"nameClean": {"$regex": clean_last, "$options": "i"}},
                        ]},
                    ],
                }
                pick_docs = await aio.wait_for(
                    db.picks.find(
                        pick_filter,
                        {
                            "_id": 0,
                            "playerId": 1,
                            "playerName": 1,
                            "teamId": 1,
                            "teamName": 1,
                            "leagueId": 1,
                            "position": 1,
                        },
                    ).sort("timestamp", -1).limit(50).to_list(50),
                    timeout=0.75,
                )
                pick_docs = pick_docs or []
            except (aio.TimeoutError, TimeoutError):
                pick_docs = []

            durable_docs = [
                *position_docs,
                *[
                    {
                        "playerId": d.get("playerId"),
                        "playerName": d.get("playerName"),
                        "teamId": d.get("teamId"),
                        "team": d.get("teamName"),
                        "position": d.get("position"),
                        "leagueId": d.get("leagueId"),
                    }
                    for d in pick_docs
                ],
            ]
            if not durable_docs:
                return []

            player_ids = [
                d.get("playerId") for d in durable_docs if d.get("playerId")
            ]
            context_by_id: dict[int, dict] = {}
            if player_ids:
                try:
                    context_docs = await aio.wait_for(
                        db.player_ctx_cache.find(
                            {"playerId": {"$in": player_ids}},
                            {"_id": 0, "playerId": 1, "contexts": 1},
                        ).to_list(50),
                        timeout=1.25,
                    )
                except (aio.TimeoutError, TimeoutError):
                    # Context enrichment is helpful but not required to
                    # restore a durable identity from the saved-pick ledger.
                    context_docs = []
                for context_doc in context_docs or []:
                    contexts = context_doc.get("contexts") or []
                    verified = next(
                        (
                            c for c in contexts
                            if c.get("verified") and not c.get("isNational")
                        ),
                        None,
                    )
                    if verified:
                        context_by_id[context_doc.get("playerId")] = verified

            recovered = []
            seen_ids = set()
            for doc in durable_docs:
                pid = doc.get("playerId")
                name = (doc.get("playerName") or "").strip()
                if not pid or not name or pid in seen_ids:
                    continue
                name_norm = _strip(name.lower())
                name_words = set(name_norm.split())
                if len(query_parts) == 1:
                    # Surname-only searches must recover a verified durable
                    # player without treating every same-surname provider row
                    # as the same identity.
                    if query_parts[0] not in name_words:
                        continue
                else:
                    # A provider/cached canonical name may omit middle or
                    # surname components typed by the user. Require the stored
                    # first name and every stored name token to be present.
                    if not name_words.issubset(set(query_parts)):
                        continue
                    if query_parts[0] != name_norm.split()[0] and not name_norm.startswith(query_parts[0]):
                        continue
                seen_ids.add(pid)
                context = context_by_id.get(pid) or {}
                recovered.append({
                    "id": pid,
                    "name": name,
                    "fullName": name,
                    "firstname": name.split()[0] if name.split() else "",
                    "lastname": name.split()[-1] if name.split() else "",
                    "age": 0,
                    "nationality": "",
                    "photo": "",
                    "teamId": context.get("teamId") or doc.get("teamId") or 0,
                    "teamName": context.get("teamName") or doc.get("team") or "",
                    "leagueId": context.get("leagueId") or doc.get("leagueId") or 0,
                    "position": doc.get("specificPosition") or doc.get("position") or "",
                })
            verified_ids = set(context_by_id)
            if verified_ids:
                recovered = [p for p in recovered if p["id"] in verified_ids]
            else:
                # If Atlas is slow for the optional context query, prefer a
                # saved row whose team is the current durable club context
                # rather than returning an arbitrary same-name player.
                current_team_ids = {
                    c.get("teamId")
                    for doc in durable_docs
                    if doc.get("playerId") in player_ids
                    for c in (context_by_id.get(doc.get("playerId")) or {},)
                    if c.get("teamId")
                }
                if current_team_ids:
                    recovered = [
                        p for p in recovered
                        if p.get("teamId") in current_team_ids
                    ]
            # The durable record may intentionally be shorter than the
            # provider's full legal name (for example, stored
            # "Jhojan Valencia" while the user types
            # "Jhojan Manuel Valencia Jimenez").  The containment check above
            # is the identity gate for this recovery path; do not run the
            # provider's stricter all-token filter again.
            recovered.sort(key=sort_key)
            return recovered[:15]
        except (aio.TimeoutError, TimeoutError):
            return []
        except Exception as exc:
            print(f"[PLAYER SEARCH] durable identity fallback failed: {exc}")
            return []

    # A previously resolved soccer player remains a bounded fallback when
    # disposable search cache rows are missing. Do not return it before the
    # normal cache/provider paths: durable rows intentionally have their club
    # fields masked, and returning them first made a valid player such as
    # Vitinha look like an unverified, context-free result even when a live
    # profile could resolve the current club.
    # Durable identity recovery is a last-resort fallback, not part of the
    # keystroke path. Its Atlas lookup can take hundreds of milliseconds even
    # when the warm identity index already has the answer.
    if len(query_parts) == 1:
        # For surname-only lookup, verified durable identities are more useful
        # than an arbitrary provider page of same-surname players. This also
        # lets a player whose provider name contains diacritics survive ASCII
        # mobile/OCR input.
        durable_players = await _durable_identity_fallback()
        if durable_players:
            _remember_hot_players(durable_players)
            _remember_hot_query(req.query, hot_league_id, durable_players)
            return {"players": _mask_unverified_team(await _attach_owner_media(durable_players))}
    durable_players: list[dict] = []

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
        # A single-word cache prefix is not sufficient to stop the provider
        # lookup. For example, "Cristiano" can hit cached "Cristian ..." rows
        # before the exact Cristiano Ronaldo row is available in the cache.
        # Let the bounded provider path resolve the literal name instead of
        # returning a misleading partial list.
        cache_has_exact_single_word = (
            len(query_parts) != 1
            or any(sort_key(player)[0] == 0 for player in cache_results)
        )
        if cache_results and cache_has_exact_single_word:
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
                    # An exact full-name query must not return the abbreviated
                    # squad label first.  That label can also carry a stale
                    # broad position (for example "I. Vicente" as Defender),
                    # while the provider profile has the canonical name and
                    # current position.  Enrich the small exact-match set
                    # before rendering; unrelated abbreviated rows remain
                    # bounded and are left alone.
                    async def _enrich_exact_abbreviated(items):
                        enriched_rows = await aio.gather(
                            *[
                                aio.wait_for(_enrich_abbreviated_player(p), timeout=1.2)
                                for p in items
                            ],
                            return_exceptions=True,
                        )
                        return [
                            row if isinstance(row, dict) else original
                            for original, row in zip(items, enriched_rows)
                        ]

                    enriched_abbreviated = await _enrich_exact_abbreviated(abbreviated)
                    by_id = {
                        p.get("id"): p
                        for p in enriched_abbreviated
                        if p.get("id")
                    }
                    cache_results = [
                        by_id.get(p.get("id"), p)
                        for p in cache_results
                    ]
            sorted_results = _apply_sort_and_quality(cache_results)
            # Do not enrich nationality/photo on the typing path. Identity,
            # club, and position are already available from the warm index;
            # optional provider metadata belongs after selection, never before
            # the dropdown can render.
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

            _remember_hot_players(sorted_results)
            _remember_hot_query(req.query, hot_league_id, sorted_results)
            return {"players": _mask_unverified_team(await _attach_owner_media(sorted_results))}
    except (aio.TimeoutError, TimeoutError):
        print(f"[PLAYER SEARCH] cache lookup exceeded 850ms for {req.query!r}; using fast provider path")
    except Exception as exc:
        print(f"[PLAYER SEARCH] cache lookup failed for {req.query!r}: {exc}")

    # If quota is gone, try last-name cache fallback then BDL search before giving up.
    # Handles abbreviated cached names like "R. Jiménez" when user types "Raul Jimenez".
    if quota_gone:
        durable_players = await _durable_identity_fallback()
        if " " in req.query.strip():
            last_word = req.query.strip().split()[-1]
            if len(last_word) >= 3:
                try:
                    fallback = await _search_players_cache(last_word, req.league_id, relaxed=True)
                    if fallback:
                        fallback_players = _apply_sort_and_quality(fallback)
                        if fallback_players:
                            return {"players": _mask_unverified_team(await _attach_owner_media(fallback_players))}
                except Exception:
                    pass
        if durable_players:
            return {"players": _mask_unverified_team(await _attach_owner_media(durable_players))}
        # BDL live search — covers EPL, La Liga, Serie A, Bundesliga, Ligue 1,
        # UCL, MLS, World Cup without any API-Football dependency. Keep this
        # bounded because it is still on the typing path.
        try:
            from soccer_bdl_client import search_bdl_players
            bdl_hits = await aio.wait_for(search_bdl_players(req.query), timeout=1.25)
            if bdl_hits:
                bdl_players = _apply_sort_and_quality(bdl_hits)
                _remember_hot_players(bdl_players)
                _remember_hot_query(req.query, hot_league_id, bdl_players)
                return {"players": _mask_unverified_team(await _attach_owner_media(bdl_players))}
        except (aio.TimeoutError, TimeoutError):
            print(f"[PLAYER SEARCH] BDL lookup exceeded 1250ms for {req.query!r}")
        except Exception:
            pass
        return {"players": []}

    if durable_players:
        return {"players": _mask_unverified_team(await _attach_owner_media(durable_players))}

    # Fast interactive provider path. The previous implementation could issue
    # dozens of sequential league/season/profile fallbacks after a cache miss,
    # leaving users staring at a spinner for 7–40 seconds. One targeted lookup
    # is enough for the dropdown; full club/context enrichment happens after
    # the user selects the player.
    fast_params = {"search": provider_query}
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

    # Before accepting an unscoped provider profile, give an exact durable
    # identity with a verified context a chance to disambiguate it. This is
    # especially important when API-Football returns a same-name profile with
    # no statistics/team (the selected ID would otherwise make the context
    # endpoint correctly report "unavailable" for the wrong person).
    if (
        len(query_parts) >= 2
        and live_players
        and all(not (p.get("teamId") or p.get("teamName")) for p in live_players)
    ):
        durable_players = await _durable_identity_fallback()
        if durable_players:
            _remember_hot_players(durable_players)
            _remember_hot_query(req.query, hot_league_id, durable_players)
            return {"players": _mask_unverified_team(await _attach_owner_media(durable_players))}

    # The search response intentionally masks club fields: the selected player
    # is verified by /players/{id}/contexts. Do not spend another 900ms on
    # context enrichment before returning rows that are already a valid
    # identity match. That extra lookup was invisible in the response but put
    # the legacy iOS client's five-second typing timeout on the edge.
    if _apply_sort_and_quality(list(live_players)):
        _remember_hot_players(live_players)
        _remember_hot_query(req.query, hot_league_id, live_players)
        return {"players": _mask_unverified_team(await _attach_owner_media(live_players))}

    # API-Football's profile search does not reliably understand a full
    # three-or-more-part name when the provider stores the display name as an
    # initial (for example, "J. Valencia" for
    # "Jhojan Manuel Valencia Jiménez").  Retry one bounded surname lookup,
    # then let the strict multi-word quality filter below select the canonical
    # full-name profile.  Without this, the empty full-name response returns
    # immediately and the universal search can only show unrelated MLB/NFL
    # surname matches.
    if len(query_parts) > 1 and not _apply_sort_and_quality(list(live_players)):
        # OCR and copied pick slips can include more than one first-name
        # initial (for example "C. K. Rader"), while API-Football may expose
        # the same player as "K. Rader". The surname lookup below is the
        # bounded recovery path for that provider naming mismatch.
        last_word = query_parts[-1]
        if len(last_word) >= 3:
            try:
                fallback_data = await aio.wait_for(
                    search_api_request("players/profiles", {"search": last_word}),
                    timeout=1.75,
                )
                fallback_players = [extract_player(item) for item in (fallback_data or [])]
                existing_ids = {p.get("id") for p in live_players}
                live_players.extend(
                    p for p in fallback_players
                    if p.get("id") and p.get("id") not in existing_ids
                )
            except (aio.TimeoutError, TimeoutError):
                print(f"[PLAYER SEARCH] surname fallback exceeded 1750ms for {req.query!r}")
            except Exception as exc:
                print(f"[PLAYER SEARCH] surname fallback failed for {req.query!r}: {exc}")

    # NWSL squad fallback: API-Football doesn't allow name-search + league together,
    # and the global profiles endpoint often omits NWSL players. When profiles +
    # surname fallbacks produce no quality match, fetch squads for all 16 NWSL
    # teams in parallel, match by name (including abbreviated forms like "Y. Ryan"),
    # and write all fetched players to cache so every future search hits cache.
    if not quota_gone and not _apply_sort_and_quality(list(live_players)):
        import time as _sq_time
        import unicodedata as _sq_uc
        def _nc(s):
            return ''.join(
                c for c in _sq_uc.normalize('NFD', (s or '').lower())
                if _sq_uc.category(c) != 'Mn'
            )

        _NWSL_TEAMS = {
            2997: "Chicago Red Stars W",   2998: "Houston Dash W",
            2999: "North Carolina Courage W", 3000: "Orlando Pride W",
            3001: "Portland Thorns W",     3002: "Seattle Reign FC W",
            3003: "NJ/NY Gotham FC W",     3004: "Utah Royals W",
            3005: "Washington Spirit W",   16487: "Kansas City W",
            16488: "Racing Louisville W",  18450: "Angel City W",
            18451: "San Diego Wave W",     22943: "Bay FC W",
            27377: "Boston Legacy W",      27378: "Denver Summit W",
        }

        async def _fetch_nwsl_squad(team_id):
            # Primary: squad endpoint
            try:
                data = await search_api_request("players/squads", {"team": team_id})
                players = data[0].get("players", []) if data else []
                if players:
                    return (team_id, players)
            except Exception:
                pass
            # Fallback for expansion teams with no squad data yet: extract
            # players from the most recent finished fixture.
            try:
                fixtures_data = await search_api_request(
                    "fixtures", {"team": team_id, "season": 2026, "last": 3}
                )
                for fix in (fixtures_data or []):
                    if fix.get("fixture", {}).get("status", {}).get("short") != "FT":
                        continue
                    fid = fix.get("fixture", {}).get("id")
                    if not fid:
                        continue
                    fp_data = await search_api_request("fixtures/players", {"fixture": fid})
                    if not fp_data:
                        continue
                    for td in fp_data:
                        if td.get("team", {}).get("id") != team_id:
                            continue
                        squad_players = [
                            {
                                "id": p["player"]["id"],
                                "name": p["player"]["name"],
                                "position": p["player"].get("position") or "",
                                "photo": p["player"].get("photo") or "",
                            }
                            for p in td.get("players", [])
                            if p.get("player", {}).get("id") and p.get("player", {}).get("name")
                        ]
                        if squad_players:
                            print(f"[NWSL SQUAD] team={team_id} used fixture {fid} fallback ({len(squad_players)} players)")
                            return (team_id, squad_players)
            except Exception as _fe:
                print(f"[NWSL SQUAD] fixture fallback for team={team_id}: {_fe}")
            return (team_id, [])

        try:
            from cache import COL_PLAYERS
            squads_raw = await aio.wait_for(
                aio.gather(*[_fetch_nwsl_squad(tid) for tid in _NWSL_TEAMS]),
                timeout=3.5,
            )

            qn = _nc(req.query)
            qw = qn.split()
            # Abbreviated match: "Y. Ryan" should match query "Yazmeen Ryan"
            def _squad_name_matches(name_clean):
                if all(w in name_clean for w in qw):
                    return True
                if len(qw) >= 2:
                    init = qw[0][0] if qw[0] else ''
                    last = qw[-1]
                    return (
                        name_clean.startswith(f"{init}.") and last in name_clean
                    )
                return False

            cache_writes = []
            for team_id, squad_players in squads_raw:
                team_name = _NWSL_TEAMS.get(team_id, "")
                for sp in squad_players:
                    pid = sp.get("id")
                    name = sp.get("name") or ""
                    if not pid or not name:
                        continue
                    name_clean = _nc(name)
                    doc = {
                        "playerId": pid,
                        "name": name,
                        "nameClean": name_clean,
                        "teamId": team_id,
                        "teamName": team_name,
                        "leagueId": NWSL_LEAGUE_ID,
                        "position": sp.get("position") or "",
                        "photo": sp.get("photo") or "",
                        "_cachedAt": _sq_time.time(),
                    }
                    cache_writes.append(doc)
                    if _squad_name_matches(name_clean) and pid not in seen_live_ids:
                        live_players.append({
                            "id": pid,
                            "name": name,
                            "firstname": sp.get("firstname") or "",
                            "lastname": sp.get("lastname") or "",
                            "age": sp.get("age") or 0,
                            "nationality": "",
                            "photo": sp.get("photo") or "",
                            "teamId": team_id,
                            "teamName": team_name,
                            "leagueId": NWSL_LEAGUE_ID,
                            "position": sp.get("position") or "",
                        })
                        seen_live_ids.add(pid)

            # Write entire squad cache in background — all NWSL players now searchable
            async def _bulk_cache(docs):
                try:
                    for doc in docs:
                        await db[COL_PLAYERS].update_one(
                            {"playerId": doc["playerId"], "leagueId": NWSL_LEAGUE_ID},
                            {"$setOnInsert": doc},
                            upsert=True,
                        )
                except Exception as _ce:
                    print(f"[NWSL CACHE] write error: {_ce}")
            if cache_writes:
                aio.ensure_future(_bulk_cache(cache_writes))
                print(f"[NWSL SQUAD] cached {len(cache_writes)} players from {len(squads_raw)} teams")

        except (aio.TimeoutError, TimeoutError):
            print(f"[PLAYER SEARCH] NWSL squad fallback exceeded 3500ms for {req.query!r}")
        except Exception as exc:
            print(f"[PLAYER SEARCH] NWSL squad fallback failed: {exc}")

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
    _remember_hot_players(live_players)
    _remember_hot_query(req.query, hot_league_id, live_players)
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
    Response:     { position, role, source, confidence, evidence, cached }
    """
    try:
        from ai_positions import resolve_player_role, _MANUAL_EXACT_PROFILES
        from config import POSITION_PROMPT_VERSION
        from cache import COL_PLAYERS
        from datetime import datetime, timezone

        GENERIC_TO_SPECIFIC = {
            "Goalkeeper": {"GK"},
            "Defender": {"CB", "LB", "RB", "LWB", "RWB"},
            "Midfielder": {"CDM", "CM", "CAM", "LM", "RM", "LW", "RW"},
            "Attacker": {"LW", "RW", "CF", "ST", "SS", "CAM"},
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

        # The resolver intentionally returns a compact tuple for its internal
        # callers. Read the persisted profile here so the selection-time API
        # also returns the evidence the next prediction must carry forward.
        profile = None
        if req.playerId:
            try:
                profile = await db.player_positions.find_one(
                    {
                        "$or": [
                            {"playerId": req.playerId},
                            {"playerId": str(req.playerId)},
                        ]
                    },
                    {
                        "_id": 0,
                        "source": 1,
                        "roleSource": 1,
                        "specificPosition": 1,
                        "role": 1,
                        "confidence": 1,
                        "evidenceSources": 1,
                        "roleEvidence": 1,
                        "roleSampleSize": 1,
                    },
                )
            except Exception as profile_error:
                # Evidence enrichment is optional. A temporary Atlas read
                # failure must not turn a successful role resolution into a
                # failed selection request.
                print(f"[RESOLVE ROLE] Evidence lookup skipped: {profile_error}")
        evidence = (
            (profile or {}).get("roleEvidence")
            or [
                f"{item.get('title') or item.get('url')}"
                for item in ((profile or {}).get("evidenceSources") or [])
                if isinstance(item, dict) and (item.get("title") or item.get("url"))
            ]
        )
        profile_source = str(
            (profile or {}).get("source")
            or (profile or {}).get("roleSource")
            or ""
        ).strip()
        profile_position = str((profile or {}).get("specificPosition") or "").strip().upper()
        profile_role = str((profile or {}).get("role") or "").strip()
        # Explicit player-ID corrections must not be clobbered by an older
        # persisted fixture-history inference for the same player.
        manual_profile = _MANUAL_EXACT_PROFILES.get(int(req.playerId or 0))
        if source == "manual_override" and manual_profile:
            profile_source = source
            profile_position = manual_profile["specificPosition"]
            profile_role = manual_profile["role"]
            evidence = list(manual_profile.get("evidence") or [])
        exact_positions = {
            "GK", "CB", "LB", "RB", "LWB", "RWB", "CDM", "CM", "CAM",
            "LM", "RM", "LW", "RW", "CF", "ST", "SS",
        }
        generic_positions = {
            "Goalkeeper": {"GK"},
            "Defender": {"CB", "LB", "RB", "LWB", "RWB"},
            "Midfielder": {"CDM", "CM", "CAM", "LM", "RM", "LW", "RW"},
            "Attacker": {"LW", "RW", "CF", "ST", "SS", "CAM"},
        }
        trusted_profile_sources = {
            "gemini_web_grounded",
            "manual_override",
            "api_sports_lineup_history",
        }
        inferred_fixture_sources = {
            "h2h_fixture_role_inferred",
            "h2h_fixture_lineup_history",
            "h2h_fixture_position_history",
        }
        # If Gemini is unavailable, the role resolver may return the broad
        # provider category even though the durable player-ID history already
        # contains an exact positive-minutes fixture position. Use that exact
        # observed history immediately at selection time, including a
        # cross-category change such as Midfielder → ST. Mark it inferred so
        # the UI and prediction provenance remain honest.
        if (
            profile_position in exact_positions
            and profile_source in (trusted_profile_sources | inferred_fixture_sources)
            and (
                profile_source in inferred_fixture_sources
                or profile_position in generic_positions.get(generic_position, set())
            )
            and (
                str(pos or "").upper() not in exact_positions
                or source == "provider_category_fallback"
            )
        ):
            pos = profile_position
            role = profile_role
            source = profile_source
            if not evidence:
                evidence = [f"exact {profile_position} from verified player fixture history"]
        response_source = profile_source or source
        role_is_inferred = response_source in inferred_fixture_sources or response_source.endswith("_inferred")
        return {
            "position": pos,
            "role": role,
            "source": response_source,
            "confidence": (profile or {}).get("confidence") or (
                "medium" if pos else "low"
            ),
            "evidence": evidence,
            "cached": source == "cache",
            "roleIsInferred": role_is_inferred,
        }

    except Exception as e:
        print(f"[RESOLVE ROLE] Error for {getattr(req, 'playerName', '?')}: {e}")
        return {"position": "", "role": "", "source": "error", "cached": False}
