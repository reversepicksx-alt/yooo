"""
First-Goal Engine

Fetches last 10+ completed fixtures for a team, extracts who scored first
from goal events, and builds a profile used to weight scenario probabilities
(Best / Base / Worst case) with real historical data.

Caching:
  - Team profile:  6h  in MongoDB `first_goal_cache`
  - Fixture events: 24h in MongoDB `first_goal_cache` (historical, immutable)
"""
from __future__ import annotations
import asyncio
from datetime import datetime, timezone
from typing import Optional

_PROFILE_TTL  = 6  * 3600   # team profile
_EVENTS_TTL   = 24 * 3600   # per-fixture events (historical → never changes)


# ── Cache helpers ─────────────────────────────────────────────────────────────

async def _cache_get(db, key: str, ttl: int) -> Optional[object]:
    try:
        doc = await db.first_goal_cache.find_one({"key": key}, {"_id": 0})
        if not doc:
            return None
        ts = doc.get("ts", "")
        if ts:
            age = (datetime.now(timezone.utc) -
                   datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)).total_seconds()
            if age < ttl:
                return doc.get("data")
    except Exception:
        pass
    return None


async def _cache_set(db, key: str, data) -> None:
    try:
        await db.first_goal_cache.update_one(
            {"key": key},
            {"$set": {"key": key, "data": data,
                      "ts": datetime.now(timezone.utc).isoformat()}},
            upsert=True,
        )
    except Exception:
        pass


# ── Fixture events fetch (per-fixture, cached 24h) ────────────────────────────

async def _fetch_events(fixture_id: int, api_fn, db) -> list:
    key = f"fge:{fixture_id}"
    cached = await _cache_get(db, key, _EVENTS_TTL)
    if cached is not None:
        return cached
    try:
        resp = await api_fn("fixtures/events", {"fixture": fixture_id})
        events = (resp or {}).get("response", [])
        await _cache_set(db, key, events)
        return events
    except Exception:
        return []


# ── Main profile builder ───────────────────────────────────────────────────────

_INERT = {
    "available": False,
    "teamScoredFirstPct": 0.50,
    "opponentScoredFirstPct": 0.35,
    "noGoalPct": 0.15,
    "avgFirstGoalMin": 35.0,
    "dataPoints": 0,
}


async def get_first_goal_profile(
    team_id: int,
    season: int,
    api_fn,
    db,
    num_fixtures: int = 12,
) -> dict:
    """
    Returns first-goal profile for `team_id` over the last `num_fixtures` completed matches.

    {
        available            bool
        teamScoredFirstPct   float  0-1
        opponentScoredFirstPct float 0-1
        noGoalPct            float  0-1
        avgFirstGoalMin      float  avg minute of first goal
        dataPoints           int
    }
    """
    if not team_id:
        return dict(_INERT)

    key = f"fg_profile:{team_id}:{season}"
    cached = await _cache_get(db, key, _PROFILE_TTL)
    if cached:
        return cached

    # Fetch recent FT fixtures for the team
    fixtures = []
    for s in [season, season - 1]:
        try:
            resp = await api_fn("fixtures", {
                "team": team_id, "season": s,
                "last": num_fixtures, "status": "FT",
            })
            fixtures = (resp or {}).get("response", [])
            if fixtures:
                break
        except Exception:
            pass

    if len(fixtures) < 3:
        await _cache_set(db, key, dict(_INERT))
        return dict(_INERT)

    # Limit to most recent fixtures
    fx_pairs = []
    for fx in fixtures[:10]:
        fid = (fx.get("fixture") or {}).get("id")
        if fid:
            fx_pairs.append((fid, fx))

    if not fx_pairs:
        await _cache_set(db, key, dict(_INERT))
        return dict(_INERT)

    # Fetch events for all fixtures concurrently
    events_list = await asyncio.gather(
        *[_fetch_events(fid, api_fn, db) for fid, _ in fx_pairs],
        return_exceptions=True,
    )

    team_first = 0
    opp_first = 0
    no_goal = 0
    first_mins: list[float] = []

    for (fid, fx), raw_events in zip(fx_pairs, events_list):
        if isinstance(raw_events, Exception):
            raw_events = []

        home_id = (fx.get("teams", {}).get("home") or {}).get("id")

        # Filter to goal events only (exclude missed penalties / own goals distort less)
        goals = [
            e for e in raw_events
            if (e.get("type", "").lower() == "goal" and
                e.get("detail", "").lower() not in ("missed penalty", "penalty missed"))
        ]
        goals.sort(key=lambda e: (e.get("time") or {}).get("elapsed") or 999)

        if not goals:
            no_goal += 1
            continue

        first = goals[0]
        elapsed = (first.get("time") or {}).get("elapsed") or 0
        first_mins.append(float(elapsed))

        scorer_team_id = (first.get("team") or {}).get("id")
        if scorer_team_id == team_id:
            team_first += 1
        else:
            opp_first += 1

    total = len(fx_pairs)
    if total == 0:
        await _cache_set(db, key, dict(_INERT))
        return dict(_INERT)

    profile = {
        "available": True,
        "teamScoredFirstPct":     round(team_first / total, 3),
        "opponentScoredFirstPct": round(opp_first  / total, 3),
        "noGoalPct":              round(no_goal    / total, 3),
        "avgFirstGoalMin":        round(sum(first_mins) / len(first_mins), 1) if first_mins else 35.0,
        "dataPoints":             total,
    }
    await _cache_set(db, key, profile)
    return profile


# ── Scenario weight calculator ─────────────────────────────────────────────────

def compute_scenario_weights(
    team_profile: dict,
    prop_type: str = "",
) -> dict:
    """
    Convert first-goal profile into Best / Base / Worst probability weights.

    For possession-based props (passes, CDM):
      Best  ← team scores first (controls game, full possession volume)
      Worst ← opponent scores first (team chases, disrupted game script)
      Base  ← competitive/no-goal game

    For saves (inverted):
      Best  ← opponent scores first (more shots in chase)
      Worst ← team scores first (opponent shells up → fewer saves)
    """
    t = team_profile.get("teamScoredFirstPct",     0.50)
    o = team_profile.get("opponentScoredFirstPct", 0.35)
    n = team_profile.get("noGoalPct",              0.15)

    invert = prop_type.lower() == "saves"

    if invert:
        p_best  = o
        p_worst = t
    else:
        p_best  = t
        p_worst = o

    p_base = max(0.05, n)

    # Renormalise to 1.0
    total = p_best + p_base + p_worst
    if total > 0:
        p_best  = round(p_best  / total, 3)
        p_base  = round(p_base  / total, 3)
        p_worst = round(p_worst / total, 3)
    else:
        p_best, p_base, p_worst = 0.40, 0.35, 0.25

    return {"best": p_best, "base": p_base, "worst": p_worst}
