"""
BallDontLie ATP Tennis API client.
Base URL: https://api.balldontlie.io/atp/v1
Same structure as WTA client but for men's ATP Tour.
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

log = logging.getLogger("atp_client")

ATP_API_BASE = "https://api.balldontlie.io/atp/v1"
ATP_API_KEY  = os.environ.get("MLB_BDL_API_KEY", "")
if not ATP_API_KEY:
    print("[ATP] WARNING: MLB_BDL_API_KEY env var not set — ATP API calls will fail")

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
    headers = {"Authorization": ATP_API_KEY}
    url = f"{ATP_API_BASE}{path}"

    async with _rate_sem:
        elapsed = time.monotonic() - _last_req_time
        if elapsed < _MIN_INTERVAL:
            await asyncio.sleep(_MIN_INTERVAL - elapsed)
        try:
            async with httpx.AsyncClient(timeout=25) as c:
                resp = await c.get(url, headers=headers, params=params or {})
        except Exception as e:
            raise RuntimeError(f"ATP API network error: {e}")
        finally:
            _last_req_time = time.monotonic()

        if resp.status_code != 429:
            if resp.status_code >= 400:
                raise RuntimeError(f"ATP API {resp.status_code}: {resp.text[:200]}")
            return resp.json()

        retry_after = min(int(resp.headers.get("retry-after", "5")), 15)

    if _retry >= 2:
        raise RuntimeError("ATP API rate-limit exceeded after retries")
    log.warning(f"[ATP CLIENT] 429 on {path} — waiting {retry_after}s")
    await asyncio.sleep(retry_after)
    return await _get(path, params, _retry + 1)


def _cache_fresh(doc: Optional[dict], ttl_seconds: int) -> bool:
    if not doc:
        return False
    ts = doc.get("ts", "")
    try:
        age = (datetime.now(timezone.utc) -
               datetime.fromisoformat(str(ts)).replace(tzinfo=timezone.utc)).total_seconds()
        return age < ttl_seconds
    except Exception:
        return False


async def _cache_get(key: str) -> Optional[dict]:
    try:
        return await db.atp_cache.find_one({"key": key}, {"_id": 0})
    except Exception:
        return None


async def _cache_set(key: str, data) -> None:
    try:
        await db.atp_cache.update_one(
            {"key": key},
            {"$set": {"key": key, "data": data, "ts": datetime.now(timezone.utc).isoformat()}},
            upsert=True,
        )
    except Exception:
        pass


async def search_players(query: str, limit: int = 15) -> list:
    cache_key = f"ps:{query.lower()}"
    cached = await _cache_get(cache_key)
    if _cache_fresh(cached, CACHE_TTL["player_search"]):
        return cached["data"][:limit]

    results = []
    try:
        data = await _get("/players", {"search": query, "per_page": 25})
        results = data.get("data", [])
    except Exception as e:
        log.warning(f"[ATP SEARCH] {e}")

    await _cache_set(cache_key, results[:limit])
    return results[:limit]


async def get_player(player_id: int) -> Optional[dict]:
    cache_key = f"player:{player_id}"
    cached = await _cache_get(cache_key)
    if _cache_fresh(cached, CACHE_TTL["player_lookup"]):
        return cached["data"]
    try:
        data = await _get(f"/players/{player_id}")
        player = data.get("data", {})
        await _cache_set(cache_key, player)
        return player
    except Exception as e:
        log.warning(f"[ATP PLAYER] {e}")
        return None


def _parse_match_date(m: dict) -> Optional[str]:
    for k in ("scheduled_time", "start_time", "date", "match_date"):
        v = m.get(k)
        if v:
            try:
                if "T" in str(v):
                    return str(v).split("T")[0]
                return str(v)[:10]
            except Exception:
                continue
    tour = m.get("tournament") or {}
    for k in ("start_date", "date"):
        v = tour.get(k)
        if v:
            return str(v)[:10]
    return None


def _parse_set_scores(match: dict) -> list:
    raw = match.get("set_scores") or match.get("sets") or []
    out = []
    for s in raw:
        out.append({
            "setNumber": s.get("set_number") or s.get("number") or 0,
            "p1Games":   s.get("player1_games") or s.get("p1_games") or 0,
            "p2Games":   s.get("player2_games") or s.get("p2_games") or 0,
        })
    return out


def _build_match_log(match: dict, subject_id: int) -> Optional[dict]:
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

    subj_games = sum((s["p1Games"] if is_p1 else s["p2Games"]) for s in sets)
    opp_games  = sum((s["p2Games"] if is_p1 else s["p1Games"]) for s in sets)
    total      = subj_games + opp_games

    winner  = match.get("winner") or {}
    won     = winner.get("id") == subject_id if winner.get("id") else None

    sets_won = sum(
        1 for s in sets
        if (s["p1Games"] if is_p1 else s["p2Games"]) > (s["p2Games"] if is_p1 else s["p1Games"])
    )
    set1 = sets[0] if sets else {}
    set1_subj = set1.get("p1Games" if is_p1 else "p2Games", 0)
    set1_opp  = set1.get("p2Games" if is_p1 else "p1Games", 0)
    set1_total = set1_subj + set1_opp

    tour = match.get("tournament") or {}
    return {
        "date":              _parse_match_date(match),
        "surface":           tour.get("surface") or "Hard",
        "round":             match.get("round") or "",
        "tournament":        tour.get("name") or "",
        "category":          tour.get("category") or "",
        "opponent":          f"{opp.get('first_name','')} {opp.get('last_name','')}".strip() or opp.get("full_name") or "?",
        "opponentId":        opp.get("id"),
        "opponentRank":      opp.get("ranking") or opp.get("current_rank"),
        "wonMatch":          won,
        "totalGames":        total,
        "playerGamesWon":    subj_games,
        "opponentGamesWon":  opp_games,
        "setsPlayed":        len(sets),
        "setsWon":           sets_won,
        "set1Total":         set1_total,
        "set1PlayerGames":   set1_subj,
        "set1WinnerSubject": (set1_subj > set1_opp) if (set1_subj or set1_opp) else None,
        "status":            match.get("match_status") or "",
        "isLive":            match.get("is_live", False),
    }


async def get_player_match_logs(player_id: int, limit: int = 30) -> list:
    """Fetch recent finished match results for a player (newest-first)."""
    cache_key = f"atp_matches2:{player_id}:{limit}"
    cached = await _cache_get(cache_key)
    if _cache_fresh(cached, CACHE_TTL["player_matches"]):
        return cached["data"]

    from datetime import datetime
    now_y   = datetime.now(timezone.utc).year
    seasons = [now_y, now_y - 1]

    params = {
        "player_ids[]": player_id,
        "per_page":     100,
        "seasons[]":    seasons,
    }
    raw = []
    try:
        data = await _get("/matches", params)
        raw = data.get("data", [])
    except Exception as e:
        log.warning(f"[ATP MATCHES] {e}")

    raw.sort(key=lambda m: _parse_match_date(m) or "0", reverse=True)

    logs = []
    for m in raw:
        if m.get("is_live"):
            continue
        status = (m.get("match_status") or "").lower()
        if status not in ("", "finished", "completed", "ended"):
            continue
        entry = _build_match_log(m, player_id)
        if entry:
            logs.append(entry)
        if len(logs) >= limit:
            break

    await _cache_set(cache_key, logs)
    return logs


async def get_player_ranking(player_id: int) -> Optional[int]:
    cache_key = f"rank:{player_id}"
    cached = await _cache_get(cache_key)
    if _cache_fresh(cached, CACHE_TTL["rankings"]):
        return cached["data"]
    try:
        data = await _get("/rankings", {"player_id": player_id, "per_page": 1})
        rows = data.get("data", [])
        rank = rows[0].get("ranking") if rows else None
        await _cache_set(cache_key, rank)
        return rank
    except Exception:
        return None


async def get_h2h(player1_id: int, player2_id: int) -> dict:
    cache_key = f"h2h:{min(player1_id,player2_id)}:{max(player1_id,player2_id)}"
    cached = await _cache_get(cache_key)
    if _cache_fresh(cached, CACHE_TTL["h2h"]):
        return cached["data"]
    try:
        data = await _get("/matches", {"player1_id": player1_id, "player2_id": player2_id, "per_page": 25})
        matches = data.get("data", [])
        p1_wins = sum(1 for m in matches if m.get("winner_id") == player1_id)
        result = {"total": len(matches), "p1Wins": p1_wins, "p2Wins": len(matches) - p1_wins}
        await _cache_set(cache_key, result)
        return result
    except Exception:
        return {"total": 0, "p1Wins": 0, "p2Wins": 0}
