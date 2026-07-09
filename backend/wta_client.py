"""
BallDontLie WTA Tennis API client — same API key as MLB/CS2 (MLB_BDL_API_KEY).
Base URL: https://api.balldontlie.io/wta/v1
Surface-aware match history is the primary data source for prop prediction.
"""
import asyncio
import time
import os
import logging
import re
from datetime import datetime, timezone
from typing import Optional

import httpx
from config import db

log = logging.getLogger("wta_client")

WTA_API_BASE = "https://api.balldontlie.io/wta/v1"
WTA_API_KEY  = os.environ.get("MLB_BDL_API_KEY", "")
if not WTA_API_KEY:
    print("[WTA] WARNING: MLB_BDL_API_KEY env var not set — WTA API calls will fail")

_rate_sem      = asyncio.Semaphore(6)
_last_req_time: float = 0.0
_MIN_INTERVAL  = 0.10

CACHE_TTL = {
    "player_search":  6  * 3600,
    "player_matches": 6  * 3600,
    "tournaments":    24 * 3600,
    "rankings":       3600,
    "h2h":            6  * 3600,
    "match_lookup":   180,
    "player_lookup":  600,
}


async def _get(path: str, params: dict = None, _retry: int = 0) -> dict:
    global _last_req_time
    headers = {"Authorization": WTA_API_KEY}
    url = f"{WTA_API_BASE}{path}"
    MAX_429_RETRIES = 2

    async with _rate_sem:
        elapsed = time.monotonic() - _last_req_time
        if elapsed < _MIN_INTERVAL:
            await asyncio.sleep(_MIN_INTERVAL - elapsed)
        try:
            async with httpx.AsyncClient(timeout=20) as c:
                resp = await c.get(url, headers=headers, params=params or {})
        except Exception as e:
            raise RuntimeError(f"WTA API network error: {e}")
        finally:
            _last_req_time = time.monotonic()

        if resp.status_code != 429:
            if resp.status_code >= 400:
                raise RuntimeError(f"WTA API error {resp.status_code}: {resp.text[:200]}")
            return resp.json()

    if _retry >= MAX_429_RETRIES:
        raise RuntimeError(f"WTA API 429 on {path} after {_retry} retries")
    wait = 10 * (2 ** _retry)
    log.warning(f"[WTA] 429 on {path} — waiting {wait}s before retry {_retry+1}")
    await asyncio.sleep(wait)
    return await _get(path, params, _retry=_retry + 1)


async def _cache_get(key: str) -> Optional[dict]:
    try:
        return await db.wta_cache.find_one({"key": key}, {"_id": 0})
    except Exception:
        return None


async def _cache_set(key: str, data) -> None:
    try:
        await db.wta_cache.update_one(
            {"key": key},
            {"$set": {"key": key, "data": data, "_ts": datetime.now(timezone.utc)}},
            upsert=True,
        )
    except Exception:
        pass


def _fresh(doc: Optional[dict], ttl: int) -> bool:
    if not doc or not doc.get("_ts"):
        return False
    try:
        raw = doc["_ts"]
        if isinstance(raw, datetime):
            ts = raw
        else:
            ts = datetime.fromisoformat(str(raw))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - ts).total_seconds() < ttl
    except Exception:
        return False


# ── Player search ─────────────────────────────────────────────────────────────

async def search_players(query: str) -> list:
    """Fuzzy search WTA players by name."""
    key = f"wta_psearch_{query.lower().strip()}"
    doc = await _cache_get(key)
    if _fresh(doc, CACHE_TTL["player_search"]) and doc.get("data") is not None:
        return doc["data"]

    results = []
    try:
        r = await _get("/players", {"search": query.strip(), "per_page": 25})
        for p in r.get("data", []):
            if not p.get("id"):
                continue
            first = p.get("first_name") or ""
            last  = p.get("last_name") or ""
            full  = f"{first} {last}".strip() or p.get("full_name") or ""
            results.append({
                "id":          p["id"],
                "firstName":   first,
                "lastName":    last,
                "fullName":    full,
                "country":     p.get("country") or p.get("country_code"),
                "currentRank": p.get("current_rank") or p.get("rank"),
                "isActive":    p.get("is_active", True),
            })
        await _cache_set(key, results)
    except Exception as e:
        log.error(f"WTA player search error: {e}")
    return results


async def get_player(player_id: int) -> Optional[dict]:
    key = f"wta_player_{player_id}"
    doc = await _cache_get(key)
    if _fresh(doc, CACHE_TTL["player_lookup"]) and doc.get("data") is not None:
        return doc["data"]
    try:
        r = await _get(f"/players/{player_id}")
        p = r.get("data") or r
        if p:
            await _cache_set(key, p)
        return p
    except Exception:
        return None


# ── Match history ─────────────────────────────────────────────────────────────

def _parse_match_date(m: dict) -> Optional[str]:
    for k in ("scheduled_time", "start_time", "date", "match_date"):
        v = m.get(k)
        if v:
            try:
                # Normalise to ISO date string
                if "T" in str(v):
                    return str(v).split("T")[0]
                return str(v)[:10]
            except Exception:
                continue
    return None


def _parse_set_scores(match: dict) -> list:
    """Return list of {p1_games, p2_games, p1_tb, p2_tb, set_number}."""
    raw = match.get("set_scores") or match.get("sets") or []
    out = []
    for s in raw:
        out.append({
            "setNumber": s.get("set_number") or s.get("number") or 0,
            "p1Games":   s.get("player1_games") or s.get("p1_games") or 0,
            "p2Games":   s.get("player2_games") or s.get("p2_games") or 0,
            "p1Tb":      s.get("player1_tiebreak") or s.get("p1_tiebreak"),
            "p2Tb":      s.get("player2_tiebreak") or s.get("p2_tiebreak"),
        })
    return out


def _build_match_log(match: dict, subject_id: int) -> Optional[dict]:
    """Normalise a raw API match into a stat dict from the subject player's POV."""
    p1 = match.get("player1") or {}
    p2 = match.get("player2") or {}
    p1_id, p2_id = p1.get("id"), p2.get("id")
    if subject_id not in (p1_id, p2_id):
        return None

    is_p1 = subject_id == p1_id
    opp   = p2 if is_p1 else p1
    sets  = _parse_set_scores(match)
    if not sets:
        return None

    subj_games_total = sum((s["p1Games"] if is_p1 else s["p2Games"]) for s in sets)
    opp_games_total  = sum((s["p2Games"] if is_p1 else s["p1Games"]) for s in sets)
    total_games      = subj_games_total + opp_games_total
    num_sets         = match.get("number_of_sets") or len(sets)

    winner = match.get("winner") or {}
    won    = winner.get("id") == subject_id if winner.get("id") else None

    sets_won_subj = sum(
        1 for s in sets
        if (s["p1Games"] if is_p1 else s["p2Games"]) > (s["p2Games"] if is_p1 else s["p1Games"])
    )
    sets_won_opp = len(sets) - sets_won_subj

    set1 = sets[0] if sets else {}
    set1_total = (set1.get("p1Games", 0) + set1.get("p2Games", 0))
    set1_subj  = set1.get("p1Games" if is_p1 else "p2Games", 0)
    set1_opp   = set1.get("p2Games" if is_p1 else "p1Games", 0)
    set1_winner_subj = (set1_subj > set1_opp) if (set1_subj or set1_opp) else None

    tour = match.get("tournament") or {}

    return {
        "matchId":         match.get("id"),
        "date":            _parse_match_date(match),
        "tournament":      tour.get("name", ""),
        "tournamentId":    tour.get("id"),
        "surface":         tour.get("surface", ""),
        "category":        tour.get("category", ""),
        "location":        tour.get("location", ""),
        "drawSize":        tour.get("draw_size"),
        "round":           match.get("round") or match.get("round_name") or "",
        "season":          match.get("season") or tour.get("season"),
        "opponent":        f"{opp.get('first_name','')} {opp.get('last_name','')}".strip(),
        "opponentId":      opp.get("id"),
        "opponentCountry": opp.get("country") or opp.get("country_code"),
        "opponentRank":    opp.get("current_rank") or opp.get("rank"),
        "playerGamesWon":  subj_games_total,
        "opponentGamesWon": opp_games_total,
        "totalGames":      total_games,
        "setsPlayed":      len(sets),
        "numberOfSets":    num_sets,
        "setsWon":         sets_won_subj,
        "setsLost":        sets_won_opp,
        "wonMatch":        won,
        "set1Total":       set1_total,
        "set1PlayerGames": set1_subj,
        "set1OppGames":    set1_opp,
        "set1WinnerSubject": set1_winner_subj,
        "setScores":       sets,
        "isLive":          match.get("is_live", False),
        "status":          match.get("match_status", ""),
        "scheduledTime":   match.get("scheduled_time"),
        "matchStatus":     match.get("match_status", ""),
    }


async def get_player_recent_matches(player_id: int, limit: int = 25, seasons: Optional[list] = None) -> list:
    """Fetch recent matches for a player (newest first)."""
    key = f"wta_pmatches_{player_id}_{limit}"
    doc = await _cache_get(key)
    if _fresh(doc, CACHE_TTL["player_matches"]) and doc.get("data") is not None:
        return doc["data"]

    matches = []
    try:
        # If seasons not specified, pull current + previous calendar year so
        # off-season picks still get a full sample.
        if not seasons:
            now_y   = datetime.now(timezone.utc).year
            seasons = [now_y, now_y - 1]

        params = {
            "player_ids[]": player_id,
            "per_page":     100,
        }
        # API expects repeated seasons[]= — httpx handles a list value.
        params["seasons[]"] = seasons

        r = await _get("/matches", params)
        raw = r.get("data", [])

        # Sort newest first by date
        def _sort_key(m):
            d = _parse_match_date(m) or "0"
            return d
        raw.sort(key=_sort_key, reverse=True)

        for m in raw:
            log_entry = _build_match_log(m, player_id)
            if log_entry and log_entry.get("status", "").lower() in ("", "finished", "completed", "ended"):
                matches.append(log_entry)
            if len(matches) >= limit:
                break

        await _cache_set(key, matches)
        log.info(f"[WTA] player {player_id} matches fetched: {len(matches)}")
    except Exception as e:
        log.error(f"WTA player matches error: {e}")
    return matches


# ── Head-to-head ──────────────────────────────────────────────────────────────

async def get_head_to_head(p1_id: int, p2_id: int) -> dict:
    """Return {p1_wins, p2_wins, matches: [...]} for two players."""
    a, b = sorted([int(p1_id), int(p2_id)])
    key = f"wta_h2h_{a}_{b}"
    doc = await _cache_get(key)
    if _fresh(doc, CACHE_TTL["h2h"]) and doc.get("data") is not None:
        return doc["data"]

    out = {"p1Wins": 0, "p2Wins": 0, "matches": []}
    try:
        r = await _get("/head_to_head", {"player1_id": p1_id, "player2_id": p2_id})
        data = r.get("data") or r
        # API returns either {player1_wins, player2_wins, matches} or similar
        out["p1Wins"]  = int(data.get("player1_wins") or 0)
        out["p2Wins"]  = int(data.get("player2_wins") or 0)
        out["matches"] = [_build_match_log(m, p1_id) for m in (data.get("matches") or [])]
        out["matches"] = [m for m in out["matches"] if m]
        await _cache_set(key, out)
    except Exception as e:
        log.warning(f"[WTA H2H] error: {e}")
    return out


# ── Rankings ──────────────────────────────────────────────────────────────────

async def get_rankings(limit: int = 100) -> list:
    key = "wta_rankings"
    doc = await _cache_get(key)
    if _fresh(doc, CACHE_TTL["rankings"]) and doc.get("data") is not None:
        return doc["data"][:limit]
    try:
        r = await _get("/rankings", {"per_page": limit})
        data = r.get("data", [])
        await _cache_set(key, data)
        return data
    except Exception as e:
        log.error(f"WTA rankings error: {e}")
        return []


async def get_player_next_match(player_id: int) -> dict:
    """Fetch the next upcoming/scheduled match for a WTA player."""
    cache_key = f"wta_next_{player_id}"
    doc = await _cache_get(cache_key)
    if _fresh(doc, 900) and doc.get("data") is not None:   # 15-min cache
        return doc["data"]

    result: dict = {"found": False}
    now_y = datetime.now(timezone.utc).year

    for status in ("upcoming", "not_started", "scheduled"):
        try:
            r = await _get("/matches", {
                "player_ids[]": player_id,
                "status": status,
                "seasons[]": [now_y, now_y + 1],
                "per_page": 10,
            })
            matches = r.get("data", [])
            if not matches:
                continue

            # Sort by soonest date
            def _date_key(m):
                for k in ("scheduled_time", "start_time", "date"):
                    v = m.get(k)
                    if v:
                        return str(v)
                return "9999"
            matches.sort(key=_date_key)

            m   = matches[0]
            p1  = m.get("player1") or {}
            p2  = m.get("player2") or {}
            opp = p2 if p1.get("id") == player_id else p1

            tour     = m.get("tournament") or {}
            date_raw = m.get("scheduled_time") or m.get("start_time") or m.get("date") or ""
            date_str = date_raw[:10] if date_raw else ""
            surface  = tour.get("surface") or ""
            round_raw = m.get("round") or m.get("round_name") or ""

            result = {
                "found":       True,
                "matchId":     m.get("id"),
                "opponent":    {
                    "id":   opp.get("id"),
                    "name": f"{opp.get('first_name', '')} {opp.get('last_name', '')}".strip(),
                    "rank": opp.get("current_rank") or opp.get("rank"),
                },
                "surface":     surface,
                "round":       round_raw,
                "tournament":  tour.get("name") or "",
                "tournamentId": tour.get("id"),
                "date":        date_str,
            }
            break
        except Exception as e:
            log.warning(f"[WTA] next-match fetch ({status}) error: {e}")
            continue

    await _cache_set(cache_key, result)
    return result


# ── Tournaments ───────────────────────────────────────────────────────────────

async def list_tournaments(season: Optional[int] = None) -> list:
    s = season or datetime.now(timezone.utc).year
    key = f"wta_tournaments_{s}"
    doc = await _cache_get(key)
    if _fresh(doc, CACHE_TTL["tournaments"]) and doc.get("data") is not None:
        return doc["data"]
    try:
        r = await _get("/tournaments", {"seasons[]": s, "per_page": 100})
        data = r.get("data", [])
        await _cache_set(key, data)
        return data
    except Exception as e:
        log.error(f"WTA tournaments error: {e}")
        return []


# ── Settle: find finished match between two players after timestamp ──────────

async def get_wta_completed_match_result(
    player_id: int,
    opponent_id: Optional[int],
    opponent_name: Optional[str],
    prop_type: str,
    after_iso: str,
) -> Optional[dict]:
    """
    Locate a finished match between player_id and the opponent that
    occurred at-or-after after_iso, then return the actual value for
    the requested prop_type and a printable matchScore.
    """
    try:
        after_dt = datetime.fromisoformat(after_iso.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        after_dt = None

    try:
        matches = await get_player_recent_matches(player_id, limit=30)
        if not matches:
            log.info(f"[WTA SETTLE] no recent matches for player {player_id}")
            return None

        def _norm(s: str) -> str:
            s = (s or "").lower()
            return re.sub(r"[^a-z0-9]", "", s)
        target_id   = int(opponent_id) if opponent_id else None
        target_norm = _norm(opponent_name or "")

        for m in matches:
            if target_id and m.get("opponentId") != target_id:
                pass  # don't continue — still allow name fallback
            opp_match = False
            if target_id and m.get("opponentId") == target_id:
                opp_match = True
            elif target_norm:
                opp_norm = _norm(m.get("opponent", ""))
                if opp_norm and (target_norm in opp_norm or opp_norm in target_norm):
                    opp_match = True
            if not opp_match:
                continue

            # Date guard
            d = m.get("date")
            if d and after_dt:
                try:
                    md = datetime.fromisoformat(str(d)).date()
                    if md < after_dt.date():
                        continue
                except Exception:
                    pass

            # Compute actual value per prop
            actual: Optional[float] = None
            pt = prop_type.lower()
            if pt == "total_games":
                actual = m.get("totalGames")
            elif pt == "player_games_won":
                actual = m.get("playerGamesWon")
            elif pt == "opponent_games_won":
                actual = m.get("opponentGamesWon")
            elif pt == "total_sets":
                actual = m.get("setsPlayed")
            elif pt == "player_sets_won":
                actual = m.get("setsWon")
            elif pt == "set_1_total_games":
                actual = m.get("set1Total")
            elif pt == "set_1_player_games":
                actual = m.get("set1PlayerGames")
            elif pt == "match_winner":
                # binary: 1 if subject won, 0 otherwise
                actual = 1 if m.get("wonMatch") else 0
            elif pt == "first_set_winner":
                actual = 1 if m.get("set1WinnerSubject") else 0
            else:
                actual = None

            if actual is None:
                continue

            subj_g = m.get("playerGamesWon", 0)
            opp_g  = m.get("opponentGamesWon", 0)
            score_str = " ".join(
                f"{s['p1Games']}-{s['p2Games']}" + (
                    f"({s['p1Tb']}-{s['p2Tb']})" if s.get("p1Tb") is not None and s.get("p2Tb") is not None else ""
                )
                for s in m.get("setScores", [])
            )
            return {
                "actualValue": actual,
                "matchScore":  score_str or f"{subj_g}-{opp_g}",
                "matchDate":   m.get("date"),
                "opponent":    m.get("opponent"),
                "wonMatch":    m.get("wonMatch"),
            }
    except Exception as e:
        log.error(f"[WTA SETTLE] error: {e}")

    return None
