"""
Bulk data prefetch — downloads all player stats for target leagues
so predictions never hit "no data" issues.

One /fixtures/players call caches ALL 22+ players from a match.
Strategy: fetch every finished fixture from the last N days,
cache all players, run on startup + every 24h.
"""
import asyncio
import time
from datetime import datetime, timezone, timedelta
from utils import api_football_request
from config import db, CURRENT_SEASON

# ── Target leagues (user-specified) ──────────────────────────────────
PREFETCH_LEAGUES = [
    39,   # Premier League
    140,  # La Liga
    135,  # Serie A
    78,   # Bundesliga
    61,   # Ligue 1
    94,   # Primeira Liga (Portugal)
    203,  # Süper Lig (Turkey)
    40,   # Championship
    188,  # A-League (Australia)
    2,    # Champions League
    3,    # Europa League
    848,  # UEFA Conference League
    71,   # Brazil Série A
    128,  # Argentina Liga Profesional
    307,  # Saudi Pro League
    253,  # MLS
    254,  # NWSL
    262,  # Liga MX
    1,    # World Cup
    4,    # Euro Championship
    32,   # WCQ UEFA
    34,   # WCQ CONMEBOL
    31,   # WCQ CONCACAF
    29,   # WCQ CAF
    30,   # WCQ AFC
    960,  # UEFA Nations League
]

_PREFETCH_SEM = asyncio.Semaphore(3)  # gentle on API — 3 concurrent fixture fetches
_STATUS_COL = "fixture_prefetch_status"


async def _is_cached(fixture_id: int) -> bool:
    doc = await db[_STATUS_COL].find_one({"_fid": fixture_id}, {"_id": 1})
    return doc is not None


async def _mark_cached(fixture_id: int):
    await db[_STATUS_COL].update_one(
        {"_fid": fixture_id},
        {"$set": {"_fid": fixture_id, "_ts": time.time()}},
        upsert=True
    )


def _build_game_log(stats: dict) -> dict:
    return {
        "minutes":                stats.get("games", {}).get("minutes") or 0,
        "rating":                 float(stats.get("games", {}).get("rating") or 0) or None,
        "passes_total":           stats.get("passes", {}).get("total"),
        "passes_key":             stats.get("passes", {}).get("key"),
        "passes_accuracy":        stats.get("passes", {}).get("accuracy"),
        "shots_total":            stats.get("shots", {}).get("total"),
        "shots_on":               stats.get("shots", {}).get("on"),
        "tackles_total":          stats.get("tackles", {}).get("total"),
        "tackles_interceptions":  stats.get("tackles", {}).get("interceptions"),
        "tackles_blocks":         stats.get("tackles", {}).get("blocks"),
        "dribbles_attempts":      stats.get("dribbles", {}).get("attempts"),
        "dribbles_success":       stats.get("dribbles", {}).get("success"),
        "fouls_drawn":            stats.get("fouls", {}).get("drawn"),
        "fouls_committed":        stats.get("fouls", {}).get("committed"),
        "duels_total":            stats.get("duels", {}).get("total"),
        "duels_won":              stats.get("duels", {}).get("won"),
        "goals_saves":            stats.get("goals", {}).get("saves"),
        "goals_total":            stats.get("goals", {}).get("total"),
        "goals_assists":          stats.get("goals", {}).get("assists"),
        "passes_crosses":         stats.get("passes", {}).get("cross"),
        "tackles_clearances":     stats.get("tackles", {}).get("clearances"),
        "cards_yellow":           stats.get("cards", {}).get("yellow"),
    }


async def _prefetch_fixture(fixture_id: int) -> int:
    """Fetch /fixtures/players for one match, cache every player. Returns count cached."""
    async with _PREFETCH_SEM:
        try:
            data = await api_football_request("fixtures/players", {"fixture": fixture_id})
            if not data:
                await _mark_cached(fixture_id)  # mark to avoid retrying empty fixtures
                return 0

            ops = []
            cached_count = 0
            for team_data in data:
                for p in team_data.get("players", []):
                    pid = p.get("player", {}).get("id")
                    if not pid:
                        continue
                    stats = p.get("statistics", [{}])[0] if p.get("statistics") else {}
                    minutes = stats.get("games", {}).get("minutes") or 0
                    if minutes == 0:
                        continue
                    gl = _build_game_log(stats)
                    k = f"fxp_{fixture_id}_{pid}"
                    ops.append(db.fixture_player_cache.update_one(
                        {"_k": k}, {"$set": {"_k": k, "d": gl}}, upsert=True
                    ))
                    cached_count += 1

            if ops:
                await asyncio.gather(*ops, return_exceptions=True)

            await _mark_cached(fixture_id)
            return cached_count

        except Exception as e:
            err = str(e).lower()
            if "quota" in err or "rate limit" in err or "too many" in err:
                print(f"[PREFETCH] API quota hit — pausing 60s")
                await asyncio.sleep(60)
            return 0


async def _get_finished_fixture_ids(league_id: int, season: int, days_back: int) -> list:
    """Get IDs of finished fixtures in the last `days_back` days for a league."""
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days_back)).strftime("%Y-%m-%d")
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        data = await api_football_request("fixtures", {
            "league": league_id,
            "season": season,
            "status": "FT",
            "from": cutoff,
            "to": today,
        })
        return [f.get("fixture", {}).get("id") for f in (data or []) if f.get("fixture", {}).get("id")]
    except Exception as e:
        print(f"[PREFETCH] Error fetching fixtures for league {league_id}: {e}")
        return []


async def bulk_prefetch_run(days_back: int = 60, max_fixtures_per_run: int = 300):
    """
    One full prefetch pass: fetch all finished fixtures from the last `days_back` days
    across all target leagues, cache every player in each uncached fixture.
    Caps at `max_fixtures_per_run` to stay within API quota.
    """
    await db[_STATUS_COL].create_index("_fid", unique=True)
    await db[_STATUS_COL].create_index("_ts")

    print(f"[PREFETCH] Starting bulk prefetch — last {days_back} days, {len(PREFETCH_LEAGUES)} leagues")
    total_fixtures = 0
    total_players = 0
    skipped = 0
    api_calls = 0

    for league_id in PREFETCH_LEAGUES:
        if total_fixtures >= max_fixtures_per_run:
            print(f"[PREFETCH] Hit max {max_fixtures_per_run} fixtures — stopping this run")
            break

        try:
            fixture_ids = await _get_finished_fixture_ids(league_id, CURRENT_SEASON, days_back)
            api_calls += 1
            uncached = []
            for fid in fixture_ids:
                if await _is_cached(fid):
                    skipped += 1
                else:
                    uncached.append(fid)

            if not uncached:
                continue

            remaining = max_fixtures_per_run - total_fixtures
            batch = uncached[:remaining]
            print(f"[PREFETCH] League {league_id}: {len(fixture_ids)} fixtures, {len(uncached)} uncached, processing {len(batch)}")

            # Process in small batches with a short pause to be kind to the API
            BATCH = 5
            for i in range(0, len(batch), BATCH):
                chunk = batch[i:i + BATCH]
                results = await asyncio.gather(*[_prefetch_fixture(fid) for fid in chunk])
                players_cached = sum(r for r in results if r)
                total_players += players_cached
                total_fixtures += len(chunk)
                api_calls += len(chunk)
                await asyncio.sleep(0.5)  # 0.5s between batches

        except Exception as e:
            print(f"[PREFETCH] League {league_id} error: {e}")
            continue

    print(f"[PREFETCH] Done — {total_fixtures} fixtures processed, {total_players} player-games cached, {skipped} already cached, {api_calls} API calls used")
    return {"fixtures": total_fixtures, "players": total_players, "skipped": skipped}


async def data_prefetch_loop():
    """
    Background loop: runs once on startup (after 60s warmup),
    then every 24h to pull new completed fixtures.
    """
    await asyncio.sleep(60)  # let the server finish warming up first

    while True:
        try:
            from utils import is_quota_exhausted
            if is_quota_exhausted():
                print("[PREFETCH] Quota exhausted — skipping prefetch run, will retry in 24h")
            else:
                # Recent pass: last 60 days, cap 300 fixtures
                await bulk_prefetch_run(days_back=60, max_fixtures_per_run=300)
        except Exception as e:
            print(f"[PREFETCH] Loop error: {e}")

        # Sleep 24h then do it again
        print("[PREFETCH] Next run in 24h")
        await asyncio.sleep(24 * 3600)
