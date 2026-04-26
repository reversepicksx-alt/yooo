"""
Fuzzy search endpoints for teams and players.
Powers the autocomplete dropdowns in the scan screen.
"""
import re
from fastapi import APIRouter, Query
from team_resolver import find_team, _normalize, COL_TEAMS_MASTER, SCAN_ALIASES, _pick_best
from config import db

router = APIRouter(prefix="/api/search", tags=["search"])


def _to_result(doc: dict) -> dict:
    return {
        "teamId": doc.get("teamId", 0),
        "teamName": doc.get("name", ""),
        "leagueId": doc.get("leagueId", 0),
    }


@router.get("/teams")
async def search_teams(
    q: str = Query(..., min_length=1, max_length=60),
    league_id: int = Query(None),
):
    """Fuzzy team search — returns up to 8 candidates sorted by match quality."""
    norm = _normalize(q)
    if not norm:
        return {"results": []}

    seen = set()
    results = []

    def _add(r: dict):
        key = r.get("teamId", 0)
        if key and key not in seen:
            seen.add(key)
            results.append(r)

    # Strategy 0: SCAN_ALIASES exact hit
    if norm in SCAN_ALIASES:
        canonical = _normalize(SCAN_ALIASES[norm])
        # Try exact then alias match, with and without league filter
        for use_league in ([True, False] if league_id else [False]):
            for field in ("nameNormalized", "aliases"):
                filt: dict = {field: canonical}
                if use_league and league_id:
                    filt["leagueId"] = league_id
                docs = await db[COL_TEAMS_MASTER].find(filt, {"_id": 0}).to_list(5)
                best = _pick_best(docs)
                if best:
                    _add(_to_result(best))
            if results:
                break
        # Fallback: canonical might be stored with different suffix (e.g. "charlotte" vs "charlotte fc")
        # Try prefix substring match on the FIRST significant word of canonical
        if not results:
            canon_words = canonical.split()
            # Use first word if it's long enough, else first two words
            prefix = canon_words[0] if len(canon_words[0]) >= 5 else " ".join(canon_words[:2])
            filt = {"nameNormalized": {"$regex": f"^{re.escape(prefix)}"}}
            docs = await db[COL_TEAMS_MASTER].find(filt, {"_id": 0}).to_list(10)
            if docs:
                # Score by how closely canonical matches the full name
                def _canon_score(d: dict) -> int:
                    nn = d.get("nameNormalized", "")
                    score = len(set(canonical.split()) & set(nn.split())) * 100
                    score += d.get("leaguePriority", 30)
                    return score
                docs.sort(key=_canon_score, reverse=True)
                _add(_to_result(docs[0]))

    # Strategy 1: find_team (best single match)
    main = await find_team(q, league_id)
    if main:
        _add(main)

    # Strategy 2: Multi-result regex on normalized name + aliases
    # We do NOT filter strictly by leagueId because teams can be cached under a
    # different league than expected (e.g. Charlotte FC stored as league 667 instead of 253).
    # Instead we boost same-league teams in the score.
    if len(norm) >= 2:
        patterns = [re.escape(norm)]
        for w in norm.split():
            if len(w) >= 3:
                patterns.append(re.escape(w))

        for pat in patterns[:3]:
            filt_list = [
                {"nameNormalized": {"$regex": pat}},
                {"aliases": {"$regex": pat}},
            ]
            base_filt: dict = {"$or": filt_list}
            docs = await db[COL_TEAMS_MASTER].find(base_filt, {"_id": 0}).limit(30).to_list(30)
            scored = []
            for d in docs:
                name_n = d.get("nameNormalized", "")
                aliases = d.get("aliases", [])
                score = 0
                if name_n.startswith(norm):
                    score += 200
                elif norm in name_n:
                    score += 100
                if norm in aliases:
                    score += 150
                for w in norm.split():
                    if any(w in a for a in aliases) or w in name_n:
                        score += 20
                score += d.get("leaguePriority", 30)
                # Boost teams whose leagueId matches the requested league
                if league_id and d.get("leagueId") == league_id:
                    score += 500
                scored.append((score, d))
            scored.sort(key=lambda x: x[0], reverse=True)
            for _, d in scored:
                _add(_to_result(d))
            if len(results) >= 8:
                break

    return {"results": results[:8]}
