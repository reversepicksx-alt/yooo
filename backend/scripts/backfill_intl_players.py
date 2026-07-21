"""
Backfill: fix cache_players entries that only have national/intl league rows.

Pass 1 (zero API calls): players who already have a club entry — the new
ranking fix handles them; just ensure the club entry's _cachedAt is fresh.

Pass 2 (API calls, batched concurrently): players who have NO club entry —
fetch from API-Football in batches of 8 concurrent requests.

Prioritises leagues the app actually uses (Liga MX, MLS, Serie A, Ligue 1,
Bundesliga, etc.) so the most common searches are fixed first.
"""
import asyncio
import os
import sys
import time
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(override=False)  # never override shell env (shell has real Atlas URL)

from config import db, CURRENT_SEASON
from utils import api_football_request
from cache import COL_PLAYERS

INTL_LEAGUES = {
    1, 9, 10, 11, 13, 15, 16, 17, 18, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35,
}

# Leagues we care most about — fix these first
PRIORITY_LEAGUES = {
    262,  # Liga MX
    253,  # MLS
    239,  # Apertura / Clausura
    71,   # Brazil Serie A
    72,   # Brazil Serie B
    73,   # Brazil Serie A (alt)
    128,  # Argentina Primera
    29,   # Argentina League
    39,   # EPL
    140,  # La Liga
    135,  # Serie A
    78,   # Bundesliga
    61,   # Ligue 1
    94,   # Primeira Liga
    144,  # Belgian Pro League
}


def _clean(s: str) -> str:
    return ''.join(
        c for c in unicodedata.normalize('NFD', (s or '').lower())
        if unicodedata.category(c) != 'Mn'
    )


async def resolve_club(pid: int) -> dict | None:
    for season in [CURRENT_SEASON, CURRENT_SEASON - 1]:
        try:
            data = await api_football_request("players", {"id": pid, "season": season})
        except Exception:
            return None
        if not data:
            continue
        stats = (data[0].get("statistics") or [])
        for st in reversed(stats):
            lid = (st.get("league") or {}).get("id", 0)
            tname = (st.get("team") or {}).get("name", "")
            if lid and lid not in INTL_LEAGUES and lid != 667 and tname:
                return {
                    "teamId":   (st.get("team") or {}).get("id", 0),
                    "teamName": tname,
                    "leagueId": lid,
                }
        if stats:
            break
    return None


async def process_batch(batch: list[dict], semaphore: asyncio.Semaphore) -> tuple[int, int]:
    """Process a batch of intl-only players. Returns (fixed, skipped)."""
    fixed = skipped = 0

    async def _fix_one(doc: dict):
        nonlocal fixed, skipped
        pid = doc.get("playerId", 0)
        async with semaphore:
            club = await resolve_club(pid)
        if not club:
            skipped += 1
            return
        name = doc.get("name", "")
        name_clean = _clean(name)
        # Write a new club-league entry (keeps the intl entry, ranking handles priority)
        existing = await db[COL_PLAYERS].find_one(
            {"playerId": pid, "leagueId": club["leagueId"]}, {"_id": 1}
        )
        if existing:
            await db[COL_PLAYERS].update_one(
                {"_id": existing["_id"]},
                {"$set": {"teamId": club["teamId"], "teamName": club["teamName"], "_cachedAt": time.time()}}
            )
        else:
            await db[COL_PLAYERS].insert_one({
                "playerId":  pid,
                "name":      name,
                "nameClean": name_clean,
                "teamId":    club["teamId"],
                "teamName":  club["teamName"],
                "leagueId":  club["leagueId"],
                "position":  doc.get("position", ""),
                "_cachedAt": time.time(),
            })
        fixed += 1
        print(f"  ✓ pid={pid} {name!r} → {club['teamName']!r} (league {club['leagueId']})")

    await asyncio.gather(*[_fix_one(d) for d in batch])
    return fixed, skipped


async def main():
    print("[BACKFILL] Connecting to Atlas…")
    t0 = time.time()

    # ── Fetch all intl-league entries ──────────────────────────────────────
    intl_docs = await db[COL_PLAYERS].find(
        {"leagueId": {"$in": list(INTL_LEAGUES)}},
        {"_id": 1, "playerId": 1, "name": 1, "leagueId": 1, "position": 1}
    ).to_list(10000)

    print(f"[BACKFILL] Found {len(intl_docs)} intl-league entries")

    # ── Fetch all player IDs that already have a club entry ────────────────
    all_pids = list({d["playerId"] for d in intl_docs if d.get("playerId")})
    club_docs = await db[COL_PLAYERS].find(
        {
            "playerId": {"$in": all_pids},
            "leagueId": {"$nin": list(INTL_LEAGUES), "$ne": 667, "$ne": 0},
        },
        {"playerId": 1, "_id": 0}
    ).to_list(50000)
    already_have_club = {d["playerId"] for d in club_docs}

    print(f"[BACKFILL] {len(already_have_club)} players already have a club entry (ranking fix handles them)")

    # ── Intl-only players: need API call ───────────────────────────────────
    intl_only = [
        d for d in intl_docs
        if d.get("playerId") and d["playerId"] not in already_have_club
    ]
    # Deduplicate by playerId
    seen: dict[int, dict] = {}
    for d in intl_only:
        pid = d["playerId"]
        if pid not in seen:
            seen[pid] = d
    intl_only = list(seen.values())

    print(f"[BACKFILL] {len(intl_only)} players need API resolution")

    if not intl_only:
        print("[BACKFILL] All players already have club entries — done!")
        return

    # ── Batch API calls, 8 concurrent ─────────────────────────────────────
    BATCH = 8
    semaphore = asyncio.Semaphore(BATCH)
    total_fixed = total_skipped = 0

    for i in range(0, len(intl_only), BATCH):
        chunk = intl_only[i:i + BATCH]
        f, s = await process_batch(chunk, semaphore)
        total_fixed += f
        total_skipped += s
        elapsed = time.time() - t0
        print(f"  Progress: {min(i+BATCH, len(intl_only))}/{len(intl_only)} | fixed={total_fixed} | elapsed={elapsed:.0f}s")
        # Brief pause between batches to respect rate limits
        await asyncio.sleep(1.2)

    elapsed = time.time() - t0
    print(f"\n[BACKFILL] Done in {elapsed:.0f}s — fixed={total_fixed}, skipped={total_skipped}, already_had_club={len(already_have_club)}")


if __name__ == "__main__":
    asyncio.run(main())
