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


async def get_player_match_logs(player_id: int, limit: int = 30) -> list:
    """Fetch recent match results for a player (newest-first)."""
    cache_key = f"matches:{player_id}:{limit}"
    cached = await _cache_get(cache_key)
    if _cache_fresh(cached, CACHE_TTL["player_matches"]):
        return cached["data"]

    all_matches = []
    cursor = None
    for _ in range(3):
        params = {"player_id": player_id, "per_page": 25}
        if cursor:
            params["cursor"] = cursor
        try:
            data = await _get("/matches", params)
        except Exception as e:
            log.warning(f"[ATP MATCHES] {e}")
            break
        all_matches.extend(data.get("data", []))
        meta = data.get("meta", {})
        cursor = meta.get("next_cursor")
        if not cursor or len(all_matches) >= limit:
            break

    logs = []
    for m in all_matches:
        p1   = m.get("player1") or {}
        p2   = m.get("player2") or {}
        score = m.get("score") or {}
        sets = score.get("sets") or []

        subject_is_p1 = (p1.get("id") == player_id)
        subject = p1 if subject_is_p1 else p2
        opponent = p2 if subject_is_p1 else p1
        won_match = m.get("winner_id") == player_id

        total_games = 0
        player_games = 0
        opp_games    = 0
        sets_played  = len(sets)
        sets_won     = 0
        set1_total   = 0
        set1_player  = 0

        for i, s in enumerate(sets):
            sg = (s.get("player1_games") or 0) if subject_is_p1 else (s.get("player2_games") or 0)
            og = (s.get("player2_games") or 0) if subject_is_p1 else (s.get("player1_games") or 0)
            total_games  += sg + og
            player_games += sg
            opp_games    += og
            if sg > og:
                sets_won += 1
            if i == 0:
                set1_total  = sg + og
                set1_player = sg

        logs.append({
            "date":            (m.get("date") or "")[:10],
            "surface":         m.get("surface") or "Hard",
            "round":           m.get("round") or "",
            "tournament":      m.get("tournament") or "",
            "opponent":        opponent.get("full_name") or opponent.get("name") or "?",
            "opponentRank":    opponent.get("ranking"),
            "wonMatch":        won_match,
            "totalGames":      total_games,
            "playerGamesWon":  player_games,
            "opponentGamesWon": opp_games,
            "setsPlayed":      sets_played,
            "setsWon":         sets_won,
            "set1Total":       set1_total,
            "set1PlayerGames": set1_player,
            "set1WinnerSubject": set1_player > ((set1_total - set1_player) if set1_total else 0),
        })

    logs.sort(key=lambda x: x.get("date", ""), reverse=True)
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
