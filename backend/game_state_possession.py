"""
Game-State Conditional Possession Engine
=========================================
Addresses a key gap in the existing possession model: the engine uses SEASON-AVERAGE
possession for both teams, but different teams behave very differently when leading vs
trailing.

  France (Deschamps era): cedes possession when ahead, happy to counter.
  Spain / Man City:       maintains or increases possession regardless of score.
  Morocco vs France:      when France leads 1-0, France's possession DROPS ~14pp,
                          Morocco's RISES ~14pp — a CDM's pass volume follows that shift.

Three independent signals (all async, combined into a single adjusted_poss):

   1. Tactical Style Flag (deterministic, cached 7 days per team)
       - possession_cede_when_leading  : 0.0 (maintain) → 1.0 (fully cede)
       - possession_chase_when_trailing: 0.0 (no change) → 1.0 (full press)

  2. Settled-Pick Possession Split (live from our picks collection)
       winning_poss / losing_poss derived from homeGoals/awayGoals + homePoss/awayPoss
       on already-settled picks. Real data beats style flags when n ≥ MIN_SETTLED_N.

  3. Fixture-Stats Split (free data already fetched per prediction — Option 4)
       Same logic applied to the team_fixture_stats list (venue + score + possession
       per recent fixture). Augments the settled-pick signal when picks are scarce.

Mode: COND_POSS_MODE env var — off | shadow | live (default: live)
  shadow: computes and logs but does NOT modify match_dominance
  live:   overwrites match_dominance["expectedPoss"] before Bayesian engine runs

Applies ONLY to soccer + pass-adjacent props (possession is the primary driver).
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Optional

# Props where team possession is the primary volume driver
PASS_ADJACENT_PROPS = {
    "pass_attempts", "passes", "key_passes", "crosses",
    "dribbles", "touches", "progressive_passes", "long_passes",
}

# Max possession swing allowed from base value (pp) — guards against AI/data noise
MAX_ADJ_PP = 15.0

# Minimum settled-game count to trust the empirical split
MIN_SETTLED_N = 3


# ─────────────────────────────────────────────────────────────────────────────
# Signal 1: deterministic tactical-style flag
# ─────────────────────────────────────────────────────────────────────────────

async def get_tactical_style(team_name: str, db) -> dict:
    """Return deterministic tactical style for a team, cached 7 days."""
    cache_key = f"tactical_style:{team_name.lower().strip()}"

    # ── Cache lookup ─────────────────────────────────────────────────────────
    try:
        doc = await db.tactical_style_cache.find_one({"_k": cache_key})
        if doc and doc.get("ts", 0) > time.time() - 7 * 86400:
            cached = doc.get("d", {}) or {}
            cached["source"] = "cache"
            return cached
    except Exception:
        pass

    return {
        "possession_cede_when_leading": 0.30,
        "possession_chase_when_trailing": 0.30,
        "style_notes": "deterministic neutral fallback",
        "source": "default",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Signal 2: Settled-Pick Possession Split
# ─────────────────────────────────────────────────────────────────────────────

async def get_settled_possession_split(team_name: str, db) -> dict:
    """
    Queries our settled picks for this team's possession split by match result.

    Return shape:
    {
        "winning_poss": float | None,
        "losing_poss":  float | None,
        "winning_n": int,
        "losing_n":  int,
    }
    """
    out = {"winning_poss": None, "losing_poss": None, "winning_n": 0, "losing_n": 0}

    try:
        cursor = db.picks.find(
            {
                "sport": "soccer",
                "status": {"$in": ["hit", "miss"]},
                "$or": [
                    {"homeTeam": {"$regex": team_name[:12], "$options": "i"}},
                    {"awayTeam": {"$regex": team_name[:12], "$options": "i"}},
                ],
                "homePoss":       {"$exists": True, "$ne": None},
                "awayPoss":       {"$exists": True, "$ne": None},
                "finalHomeGoals": {"$exists": True, "$ne": None},
                "finalAwayGoals": {"$exists": True, "$ne": None},
            },
            {"homeTeam": 1, "awayTeam": 1,
             "homePoss": 1, "awayPoss": 1,
             "finalHomeGoals": 1, "finalAwayGoals": 1},
        ).limit(60)

        winning_list: list[float] = []
        losing_list:  list[float] = []
        team_lower = team_name.lower()

        async for pick in cursor:
            h_goals = pick.get("finalHomeGoals", 0) or 0
            a_goals = pick.get("finalAwayGoals", 0) or 0
            h_poss  = pick.get("homePoss")
            a_poss  = pick.get("awayPoss")
            h_name  = (pick.get("homeTeam") or "").lower()
            a_name  = (pick.get("awayTeam") or "").lower()

            # Match team to home or away side
            if team_lower in h_name or h_name in team_lower:
                team_poss = h_poss
                team_won  = h_goals > a_goals
            elif team_lower in a_name or a_name in team_lower:
                team_poss = a_poss
                team_won  = a_goals > h_goals
            else:
                continue

            if team_poss is None:
                continue
            try:
                poss_val = float(str(team_poss).replace("%", "").strip())
            except (ValueError, TypeError):
                continue
            if not (5.0 <= poss_val <= 95.0):
                continue

            if team_won:
                winning_list.append(poss_val)
            else:
                losing_list.append(poss_val)

        if len(winning_list) >= MIN_SETTLED_N:
            out["winning_poss"] = round(sum(winning_list) / len(winning_list), 1)
            out["winning_n"]    = len(winning_list)
        if len(losing_list) >= MIN_SETTLED_N:
            out["losing_poss"] = round(sum(losing_list) / len(losing_list), 1)
            out["losing_n"]    = len(losing_list)

    except Exception as e:
        print(f"[SETTLED POSS SPLIT] Query failed for {team_name}: {e}")

    return out


# ─────────────────────────────────────────────────────────────────────────────
# Signal 3 / Option 4: Fixture-Stats Split (free data already fetched)
# ─────────────────────────────────────────────────────────────────────────────

def split_from_fixture_stats(fixture_stats: list) -> dict:
    """
    Derives possession split from team_fixture_stats (already fetched per prediction).

    Each entry is expected to have:
      - possession: "45%" or "45" (team's possession that game)
      - venue:      "home" | "away"
      - score:      "X-Y" (homeGoals-awayGoals)

    This is free — no extra API calls.
    """
    out = {"winning_poss": None, "losing_poss": None, "winning_n": 0, "losing_n": 0}
    winning_list: list[float] = []
    losing_list:  list[float] = []

    for entry in (fixture_stats or []):
        poss_raw = entry.get("possession")
        if poss_raw is None:
            continue
        try:
            poss = float(str(poss_raw).replace("%", "").strip())
        except (ValueError, TypeError):
            continue
        if not (5.0 <= poss <= 95.0):
            continue

        score = str(entry.get("score", ""))
        venue = entry.get("venue", "")
        try:
            parts = score.split("-")
            home_g, away_g = int(parts[0]), int(parts[1])
        except (ValueError, IndexError):
            continue

        if venue == "home":
            team_won = home_g > away_g
        elif venue == "away":
            team_won = away_g > home_g
        else:
            continue

        if team_won:
            winning_list.append(poss)
        else:
            losing_list.append(poss)

    if len(winning_list) >= MIN_SETTLED_N:
        out["winning_poss"] = round(sum(winning_list) / len(winning_list), 1)
        out["winning_n"]    = len(winning_list)
    if len(losing_list) >= MIN_SETTLED_N:
        out["losing_poss"] = round(sum(losing_list) / len(losing_list), 1)
        out["losing_n"]    = len(losing_list)

    return out


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _style_trailing_poss(base: float, style: dict, opp_style: dict) -> float:
    """
    Style-only estimate of player's team possession when trailing.
    Two stacked effects:
      1. Own chase tendency (player's team pushes forward)
      2. Opponent cedes possession when leading
    Each capped to prevent compounding extremes.
    """
    chase      = style.get("possession_chase_when_trailing", 0.30)
    opp_cede   = opp_style.get("possession_cede_when_leading", 0.30)
    own_boost  = chase    * 10.0   # max +10 pp from own chase
    opp_boost  = opp_cede * 7.0    # max +7 pp from opp ceding
    return min(80.0, base + own_boost + opp_boost)


def _style_leading_poss(base: float, style: dict) -> float:
    """
    Style-only estimate of player's team possession when leading.
    Teams that cede possession when ahead see it drop.
    """
    cede = style.get("possession_cede_when_leading", 0.30)
    return max(20.0, base - cede * 12.0)   # max -12 pp


def _blend(style_val: float, settled_val: float, settled_n: int) -> float:
    """
    Blend style estimate with empirical settled value.
    Settled data weight rises with sample size, capping at 70%.
    """
    w = min(settled_n, 10) / 10.0 * 0.70
    return settled_val * w + style_val * (1.0 - w)


# ─────────────────────────────────────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────────────────────────────────────

async def compute_conditional_possession(
    base_poss: float,
    p_trail: float,
    p_lead:  float,
    player_team_name: str,
    opp_team_name: str,
    db,
    team_fixture_stats: Optional[list] = None,
    opp_fixture_stats:  Optional[list] = None,
) -> dict:
    """
    Compute game-state-adjusted expected possession for the player's team.

    Parameters
    ----------
    base_poss          : season-average expected possession (from compute_match_dominance)
    p_trail            : P(player's team trails) — from implied odds
    p_lead             : P(player's team leads)  — from implied odds
    player_team_name   : e.g. "Morocco"
    opp_team_name      : e.g. "France"
    db                 : Motor AsyncIOMotorDatabase
    team_fixture_stats : already-fetched list of this team's recent fixture stat dicts
    opp_fixture_stats  : already-fetched list of opponent's recent fixture stat dicts

    Returns
    -------
    {
        "adjusted_poss":          float,
        "base_poss":              float,
        "trailing_scenario_poss": float,
        "leading_scenario_poss":  float,
        "draw_scenario_poss":     float,
        "p_trail":  float,
        "p_lead":   float,
        "p_draw":   float,
        "delta_pp": float,          # adjusted - base
        "player_style":  dict,
        "opp_style":     dict,
        "player_settled": dict,
        "method": str,
    }
    """
    base_poss = max(10.0, min(90.0, float(base_poss)))
    p_trail   = max(0.0,  min(0.90, float(p_trail)))
    p_lead    = max(0.0,  min(0.90, float(p_lead)))
    p_draw    = max(0.0,  1.0 - p_trail - p_lead)

    # Fetch style flags for both teams + player's settled split in parallel
    player_style, opp_style, player_settled = await asyncio.gather(
        get_tactical_style(player_team_name, db),
        get_tactical_style(opp_team_name, db),
        get_settled_possession_split(player_team_name, db),
        return_exceptions=True,
    )

    _ds = {"possession_cede_when_leading": 0.30, "possession_chase_when_trailing": 0.30, "source": "error"}
    _dd = {"winning_poss": None, "losing_poss": None, "winning_n": 0, "losing_n": 0}
    if isinstance(player_style,   Exception): player_style   = _ds
    if isinstance(opp_style,      Exception): opp_style      = _ds
    if isinstance(player_settled, Exception): player_settled = _dd

    # ── Option 4: augment settled split from fixture stats (free data) ───────
    if team_fixture_stats:
        fx = split_from_fixture_stats(team_fixture_stats)
        for side in ("winning", "losing"):
            poss_key = f"{side}_poss"
            n_key    = f"{side}_n"
            if fx[n_key] >= MIN_SETTLED_N:
                existing_n    = player_settled.get(n_key, 0)
                existing_poss = player_settled.get(poss_key)
                if existing_poss is None:
                    player_settled[poss_key] = fx[poss_key]
                    player_settled[n_key]    = fx[n_key]
                else:
                    n1, n2 = existing_n, fx[n_key]
                    player_settled[poss_key] = round(
                        (existing_poss * n1 + fx[poss_key] * n2) / (n1 + n2), 1
                    )
                    player_settled[n_key] = n1 + n2

    # ── Trailing scenario ─────────────────────────────────────────────────────
    style_trail = _style_trailing_poss(base_poss, player_style, opp_style)
    if (player_settled.get("losing_poss") is not None
            and player_settled.get("losing_n", 0) >= MIN_SETTLED_N):
        trailing_poss = _blend(style_trail,
                                player_settled["losing_poss"],
                                player_settled["losing_n"])
        method_trail = "settled+style"
    else:
        trailing_poss = style_trail
        method_trail  = "style_only"

    # ── Leading scenario ──────────────────────────────────────────────────────
    style_lead = _style_leading_poss(base_poss, player_style)
    if (player_settled.get("winning_poss") is not None
            and player_settled.get("winning_n", 0) >= MIN_SETTLED_N):
        leading_poss = _blend(style_lead,
                               player_settled["winning_poss"],
                               player_settled["winning_n"])
        method_lead = "settled+style"
    else:
        leading_poss = style_lead
        method_lead  = "style_only"

    # ── Draw scenario: base possession ────────────────────────────────────────
    draw_poss = base_poss

    # ── Probability-weighted blend ────────────────────────────────────────────
    adjusted_raw = (
        p_trail * trailing_poss +
        p_lead  * leading_poss  +
        p_draw  * draw_poss
    )

    # Cap swing to ±MAX_ADJ_PP
    adjusted = max(base_poss - MAX_ADJ_PP, min(base_poss + MAX_ADJ_PP, adjusted_raw))
    delta    = round(adjusted - base_poss, 1)

    print(
        f"[COND POSS] {player_team_name} vs {opp_team_name}: "
        f"base={base_poss:.0f}% → adj={adjusted:.1f}% (Δ{delta:+.1f}pp) | "
        f"trail={trailing_poss:.1f}%(p={p_trail:.2f}) lead={leading_poss:.1f}%(p={p_lead:.2f}) "
        f"draw={draw_poss:.0f}%(p={p_draw:.2f}) | "
        f"player_cede={player_style.get('possession_cede_when_leading', '?'):.2f} "
        f"player_chase={player_style.get('possession_chase_when_trailing', '?'):.2f} | "
        f"opp_cede={opp_style.get('possession_cede_when_leading', '?'):.2f} | "
        f"settled_n(win={player_settled.get('winning_n', 0)},lose={player_settled.get('losing_n', 0)})"
    )

    return {
        "adjusted_poss":          round(adjusted, 1),
        "base_poss":              base_poss,
        "trailing_scenario_poss": round(trailing_poss, 1),
        "leading_scenario_poss":  round(leading_poss, 1),
        "draw_scenario_poss":     round(draw_poss, 1),
        "p_trail":                round(p_trail, 3),
        "p_lead":                 round(p_lead,  3),
        "p_draw":                 round(p_draw,  3),
        "delta_pp":               delta,
        "player_style":           player_style,
        "opp_style":              opp_style,
        "player_settled":         {
            "winning_poss": player_settled.get("winning_poss"),
            "losing_poss":  player_settled.get("losing_poss"),
            "winning_n":    player_settled.get("winning_n", 0),
            "losing_n":     player_settled.get("losing_n", 0),
        },
        "method": f"trail={method_trail},lead={method_lead}",
    }
