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
from datetime import datetime, timezone, timedelta
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
                      "ts": datetime.now(timezone.utc)}},
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
        events = _response_rows(resp)
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


def _response_rows(response) -> list:
    """Normalize both project API-client contracts to a provider row list."""
    if isinstance(response, list):
        return response
    if isinstance(response, dict):
        rows = response.get("response", [])
        return rows if isinstance(rows, list) else []
    return []


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
    # Do not turn an upstream timeout, quota pause, or old response-shape bug
    # into a six-hour false "no data" state. Only a successfully built profile
    # is durable evidence.
    if cached and cached.get("available"):
        return cached

    # Fetch recent FT fixtures for the team
    fixtures = []
    for s in [season, season - 1]:
        try:
            _fg_from = (datetime.now(timezone.utc) - timedelta(days=num_fixtures * 21)).strftime("%Y-%m-%d")
            _fg_to   = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            resp = await api_fn("fixtures", {
                "team": team_id, "season": s,
                "from": _fg_from, "to": _fg_to, "status": "FT",
            })
            fixtures = _response_rows(resp)
            if fixtures:
                break
        except Exception:
            pass

    if len(fixtures) < 3:
        return dict(_INERT)

    # Limit to most recent fixtures
    fx_pairs = []
    for fx in fixtures[:10]:
        fid = (fx.get("fixture") or {}).get("id")
        if fid:
            fx_pairs.append((fid, fx))

    if not fx_pairs:
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


def build_first_goal_market(
    team_profile: dict | None,
    opponent_profile: dict | None,
    prop_type: str = "",
) -> tuple[dict, dict]:
    """Build an explanatory first-goal market from completed-fixture profiles.

    This is deliberately a descriptive contract.  It exposes the two teams'
    observed first-goal histories and the resulting pre-match regime branches;
    it does not alter any Reverse Picks projection or recommendation.
    """
    team_profile = team_profile or {}
    opponent_profile = opponent_profile or {}
    team_available = bool(team_profile.get("available"))
    opponent_available = bool(opponent_profile.get("available"))

    if not team_available and not opponent_available:
        unavailable = {
            "available": False,
            "source": "api-football-fixtures-events",
            "reason": "Recent completed-fixture first-goal evidence was unavailable.",
            "projection_influence": "shadow_only",
        }
        regime = {
            "available": False,
            "source": "first_goal_market",
            "reason": unavailable["reason"],
            "projection_influence": "shadow_only",
        }
        return unavailable, regime

    def _mean(values: list[float], default: float) -> float:
        return sum(values) / len(values) if values else default

    # The target team's "scored first" rate is corroborated by the opponent's
    # "conceded first" rate; analogous mirrored evidence is used for the
    # opponent-first branch.  One profile is still sufficient, and the source
    # coverage says so explicitly.
    team_first_inputs = []
    opponent_first_inputs = []
    no_goal_inputs = []
    first_goal_minutes = []
    if team_available:
        team_first_inputs.append(float(team_profile.get("teamScoredFirstPct", 0)))
        opponent_first_inputs.append(float(team_profile.get("opponentScoredFirstPct", 0)))
        no_goal_inputs.append(float(team_profile.get("noGoalPct", 0)))
        first_goal_minutes.append(float(team_profile.get("avgFirstGoalMin", 35)))
    if opponent_available:
        team_first_inputs.append(float(opponent_profile.get("opponentScoredFirstPct", 0)))
        opponent_first_inputs.append(float(opponent_profile.get("teamScoredFirstPct", 0)))
        no_goal_inputs.append(float(opponent_profile.get("noGoalPct", 0)))
        first_goal_minutes.append(float(opponent_profile.get("avgFirstGoalMin", 35)))

    team_first = max(0.0, _mean(team_first_inputs, 0.50))
    opponent_first = max(0.0, _mean(opponent_first_inputs, 0.35))
    no_goal = max(0.0, _mean(no_goal_inputs, 0.15))
    total = team_first + opponent_first + no_goal
    if total <= 0:
        team_first, opponent_first, no_goal = 0.50, 0.35, 0.15
        total = 1.0

    team_first = round(team_first / total, 3)
    opponent_first = round(opponent_first / total, 3)
    no_goal = round(no_goal / total, 3)
    scenario_weights = compute_scenario_weights(
        {
            "teamScoredFirstPct": team_first,
            "opponentScoredFirstPct": opponent_first,
            "noGoalPct": no_goal,
        },
        prop_type,
    )
    is_saves = str(prop_type or "").lower() in {"saves", "goalie_saves"}
    best_case = "opponent_scores_first" if is_saves else "team_scores_first"
    worst_case = "team_scores_first" if is_saves else "opponent_scores_first"
    leading_probability = max(team_first, opponent_first, no_goal)
    if leading_probability == no_goal:
        classification = "balanced_or_goalless_lean"
    elif leading_probability == team_first:
        classification = "team_first_lean"
    else:
        classification = "opponent_first_lean"

    market = {
        "available": True,
        "source": "api-football-fixtures-events",
        "coverage": "two_team_profiles" if team_available and opponent_available else "one_team_profile",
        "team_scores_first_probability": team_first,
        "opponent_scores_first_probability": opponent_first,
        "no_goal_probability": no_goal,
        "expected_first_goal_minute": round(_mean(first_goal_minutes, 35.0), 1),
        "team_profile": team_profile if team_available else None,
        "opponent_profile": opponent_profile if opponent_available else None,
        "sample_size": {
            "team": int(team_profile.get("dataPoints", 0)) if team_available else 0,
            "opponent": int(opponent_profile.get("dataPoints", 0)) if opponent_available else 0,
        },
        "scenario_weights": scenario_weights,
        "projection_influence": "shadow_only",
    }
    regime = {
        "available": True,
        "source": "first_goal_market",
        "classification": classification,
        "best_case": best_case,
        "base_case": "no_early_goal_or_balanced_state",
        "worst_case": worst_case,
        "scenario_weights": scenario_weights,
        "reason": (
            "First-goal branches are descriptive pre-match regimes based on "
            "completed-fixture event history, not live match events."
        ),
        "projection_influence": "shadow_only",
    }
    return market, regime
