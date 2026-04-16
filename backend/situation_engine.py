"""
SITUATION ENGINE — Elite Contextual Awareness for ReversePicks

Provides four types of real-world situational intelligence that the base
Bayesian + AI models cannot derive from season averages alone:

1. Knockout Aggregate Awareness  — detects 2nd-leg scenarios, computes
   aggregate score, goal deficit, and whether the home team is forced to attack.

2. Injury / Suspension Radar     — queries API Football for confirmed
   absences in the specific upcoming fixture.

3. Situational Pressure Scoring  — converts aggregate deficit + knockout
   context into concrete multipliers that adjust possession and Bayesian output.

4. (Web intel handled separately in grok_engine.py via Grok Live Search)
"""

import asyncio
import re
from datetime import datetime, timezone, timedelta
from config import CURRENT_SEASON

# ── lazy import to avoid circular deps ──────────────────────────────────────
def _af():
    from cache import api_football_request
    return api_football_request


# ═══════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════

def _detect_second_leg(match_round: str) -> bool:
    if not match_round:
        return False
    r = match_round.lower()
    return "2nd leg" in r or "second leg" in r or "leg 2" in r


def _detect_knockout(match_round: str) -> bool:
    if not match_round:
        return False
    r = match_round.lower()
    keywords = ["final", "quarter", "semi", "round of", "knockout",
                "elimination", "playoff", "2nd leg", "1st leg", "leg 1", "leg 2"]
    return any(kw in r for kw in keywords)


async def _fetch_h2h_same_competition(
    team_id: int, opponent_id: int, league_id: int, season: int
) -> list:
    """Fetch last 4 head-to-head fixtures between these two teams in the same
    league/cup (same league_id), restricted to the current season."""
    try:
        api = _af()
        data = await api("fixtures/headtohead", {
            "h2h": f"{team_id}-{opponent_id}",
            "league": league_id,
            "season": season,
            "last": 4,
        })
        return data or []
    except Exception as e:
        print(f"[SITUATION] H2H fetch error: {e}")
        return []


async def _fetch_injuries(fixture_id: int) -> list:
    """Fetch confirmed injuries + suspensions for the upcoming fixture."""
    if not fixture_id:
        return []
    try:
        api = _af()
        data = await api("injuries", {"fixture": fixture_id})
        return data or []
    except Exception as e:
        print(f"[SITUATION] Injury fetch error: {e}")
        return []


# ═══════════════════════════════════════════════════════════════════════════
# AGGREGATE SCORE PARSER
# ═══════════════════════════════════════════════════════════════════════════

def _parse_aggregate(
    h2h_fixtures: list,
    home_team_id: int,   # the player's team (who is HOME today)
    away_team_id: int,   # opponent (who is AWAY today)
    current_fixture_id: int | None = None,
) -> dict:
    """
    From head-to-head fixtures (same competition, finished), find the FIRST LEG
    (any fixture NOT equal to current_fixture_id that is already finished) and
    return an aggregate summary.

    Returns:
        {
          "firstLegFound": bool,
          "firstLegScore": str,           e.g. "PSG 2 – 0 Liverpool"
          "homeTeamAggregate": int,       goals scored by today's HOME team across both legs
          "awayTeamAggregate": int,
          "goalDeficit": int,             positive → home team leads, negative → home team trails
          "homeTeamTrailing": bool,
          "mustWinByGoals": int,          goals needed for home team to level or advance
        }
    """
    result = {
        "firstLegFound": False,
        "firstLegScore": "",
        "homeTeamAggregate": 0,
        "awayTeamAggregate": 0,
        "goalDeficit": 0,
        "homeTeamTrailing": False,
        "mustWinByGoals": 0,
    }

    finished_statuses = {"FT", "AET", "PEN", "FT_PEN"}
    first_leg = None
    today = datetime.now(timezone.utc)
    # Knockout legs are always 1–3 weeks apart. If the most recent H2H finished
    # match is older than 45 days, it is a group-stage/earlier-round result and
    # must NOT be treated as the first leg of the current knockout tie.
    MAX_FIRST_LEG_AGE_DAYS = 45

    for fx in h2h_fixtures:
        fid = fx.get("fixture", {}).get("id")
        status = fx.get("fixture", {}).get("status", {}).get("short", "")
        if status not in finished_statuses:
            continue
        if current_fixture_id and fid == current_fixture_id:
            continue  # skip today's fixture

        # Date proximity guard — reject group-stage / earlier-season matches
        date_str = fx.get("fixture", {}).get("date", "")
        if date_str:
            try:
                from datetime import datetime as dt
                match_date = dt.fromisoformat(date_str.replace("Z", "+00:00"))
                age_days = (today - match_date).days
                if age_days > MAX_FIRST_LEG_AGE_DAYS:
                    print(
                        f"[SITUATION] H2H match {fid} is {age_days}d old — "
                        f"treating as group-stage result, NOT a first leg. Skipping."
                    )
                    continue
            except Exception:
                pass

        first_leg = fx
        break  # take the most recent qualifying finished H2H match

    if not first_leg:
        return result

    result["firstLegFound"] = True

    goals = first_leg.get("goals", {})
    fl_home_goals = goals.get("home", 0) or 0
    fl_away_goals = goals.get("away", 0) or 0

    fl_home_id = first_leg.get("teams", {}).get("home", {}).get("id")
    fl_home_name = first_leg.get("teams", {}).get("home", {}).get("name", "?")
    fl_away_name = first_leg.get("teams", {}).get("away", {}).get("name", "?")

    # Map first-leg home/away goals to TODAY's home/away team perspective
    if fl_home_id == home_team_id:
        # Player's team (today HOME) was also HOME in the first leg
        player_team_first_leg_goals = fl_home_goals
        opponent_first_leg_goals = fl_away_goals
    else:
        # Player's team (today HOME) was AWAY in the first leg
        player_team_first_leg_goals = fl_away_goals
        opponent_first_leg_goals = fl_home_goals

    result["firstLegScore"] = f"{fl_home_name} {fl_home_goals}–{fl_away_goals} {fl_away_name}"
    # After 1st leg only (2nd leg not yet played)
    result["homeTeamAggregate"] = player_team_first_leg_goals
    result["awayTeamAggregate"] = opponent_first_leg_goals

    deficit = player_team_first_leg_goals - opponent_first_leg_goals  # +ve = home leads
    result["goalDeficit"] = deficit
    result["homeTeamTrailing"] = deficit < 0
    # Goals needed to level (away goals rule removed in most competitions, so just level on aggregate)
    if deficit < 0:
        result["mustWinByGoals"] = abs(deficit) + 1   # need to outscore by this many in 2nd leg
    elif deficit == 0:
        result["mustWinByGoals"] = 1  # must score to win or go to ET

    return result


# ═══════════════════════════════════════════════════════════════════════════
# INJURY PARSER
# ═══════════════════════════════════════════════════════════════════════════

def _parse_injuries(
    raw_injuries: list,
    player_team_id: int,
    opponent_id: int,
) -> dict:
    """
    Parse raw injury data into structured lists for player's team and opponent.
    Returns:
        {
          "playerTeamAbsences": [{"name", "position", "type", "reason"}],
          "opponentAbsences": [{"name", "position", "type", "reason"}],
          "summaryText": str,
        }
    """
    player_team_absences = []
    opponent_absences = []

    for entry in (raw_injuries or []):
        team_id = entry.get("team", {}).get("id")
        player_info = entry.get("player", {})
        injury_info = entry.get("injury", {})

        absence = {
            "name": player_info.get("name", "Unknown"),
            "position": player_info.get("pos", ""),
            "type": injury_info.get("type", ""),
            "reason": injury_info.get("reason", ""),
        }

        if team_id == player_team_id:
            player_team_absences.append(absence)
        elif team_id == opponent_id:
            opponent_absences.append(absence)

    def _fmt(absences):
        if not absences:
            return "None confirmed"
        lines = []
        for a in absences[:6]:
            typ = a["type"] or a["reason"] or "Out"
            lines.append(f"{a['name']} ({a['position']}) — {typ}")
        return "; ".join(lines)

    summary = ""
    if player_team_absences or opponent_absences:
        summary = f"Player's team absences: {_fmt(player_team_absences)} | Opponent absences: {_fmt(opponent_absences)}"

    return {
        "playerTeamAbsences": player_team_absences,
        "opponentAbsences": opponent_absences,
        "summaryText": summary,
    }


# ═══════════════════════════════════════════════════════════════════════════
# SITUATIONAL PRESSURE → MULTIPLIERS
# ═══════════════════════════════════════════════════════════════════════════

def _compute_pressure_multipliers(
    aggregate: dict,
    is_knockout: bool,
    is_second_leg: bool,
    is_player_home: bool,
    prop_type: str,
) -> dict:
    """
    Converts situational context into concrete prediction multipliers.

    Returns:
        {
          "possessionBoostHome": float,   % points to add to home team's expected possession
          "bayesianMultiplierHome": float, multiply Bayesian projection for home player
          "bayesianMultiplierAway": float,
          "gameTypeOverride": str | None,
          "notes": list[str],
        }
    """
    out = {
        "possessionBoostHome": 0.0,
        "bayesianMultiplierHome": 1.0,
        "bayesianMultiplierAway": 1.0,
        "gameTypeOverride": None,
        "notes": [],
    }

    if not is_knockout or not is_second_leg:
        return out

    home_trailing = aggregate.get("homeTeamTrailing", False)
    must_win_by = aggregate.get("mustWinByGoals", 0)
    deficit = aggregate.get("goalDeficit", 0)  # negative = home trails

    PASS_PROPS = {"pass_attempts", "key_passes", "crosses", "passes", "shots_assisted"}
    SHOT_PROPS = {"shots", "shots_on_target"}
    DEF_PROPS = {"tackles", "interceptions", "blocks", "clearances"}

    if home_trailing and must_win_by >= 1:
        # Home team MUST attack — forced higher possession, more passes, more shots
        urgency = min(must_win_by, 3)  # cap at 3-goal scenario

        # Possession boost scales with urgency: 1 goal needed = +8%, 2 = +13%, 3 = +17%
        poss_boost = urgency * 5.5 + 2.5
        out["possessionBoostHome"] = round(poss_boost, 1)
        out["notes"].append(
            f"MUST-WIN 2nd leg: home needs {must_win_by} goal(s) to advance → +{poss_boost:.1f}% possession boost"
        )

        # Bayesian multiplier for HOME player props
        if is_player_home:
            if prop_type in PASS_PROPS:
                mult = 1.0 + (urgency * 0.07)  # +7-21% pass volume
                out["bayesianMultiplierHome"] = round(min(mult, 1.25), 3)
                out["notes"].append(f"Pass prop boost: x{out['bayesianMultiplierHome']:.2f} (forced build-up)")
            elif prop_type in SHOT_PROPS:
                mult = 1.0 + (urgency * 0.09)  # +9-27% shot volume
                out["bayesianMultiplierHome"] = round(min(mult, 1.30), 3)
                out["notes"].append(f"Shot prop boost: x{out['bayesianMultiplierHome']:.2f} (forced attack)")
            elif prop_type in DEF_PROPS:
                # Defenders push higher → fewer defensive actions
                mult = max(0.82, 1.0 - (urgency * 0.04))
                out["bayesianMultiplierHome"] = round(mult, 3)
                out["notes"].append(f"Defensive prop reduction: x{out['bayesianMultiplierHome']:.2f} (defenders in attack)")
        else:
            # AWAY player (opponent defending a lead)
            if prop_type in PASS_PROPS:
                mult = max(0.80, 1.0 - urgency * 0.06)  # away team passes less (deep block)
                out["bayesianMultiplierAway"] = round(mult, 3)
                out["notes"].append(f"Away pass prop reduction: x{out['bayesianMultiplierAway']:.2f} (defending lead)")
            elif prop_type in DEF_PROPS:
                mult = min(1.25, 1.0 + urgency * 0.06)  # away defends more
                out["bayesianMultiplierAway"] = round(mult, 3)
                out["notes"].append(f"Away defensive boost: x{out['bayesianMultiplierAway']:.2f} (protecting lead)")

        out["gameTypeOverride"] = "high-tempo"

    elif not home_trailing and is_second_leg:
        # Home team IS LEADING on aggregate — defending a lead at home
        # deficit is positive when home leads (per _parse_aggregate convention)
        lead = deficit  # positive = how many goals the home team leads by
        if lead >= 2:
            # Comfortable cushion — home may sit back a bit
            out["possessionBoostHome"] = -3.0  # slight possession reduction (less urgency)
            out["notes"].append(
                f"Home leads by {lead} on aggregate — slight possession reduction (-3%), expect sit-back"
            )
            if is_player_home and prop_type in SHOT_PROPS:
                out["bayesianMultiplierHome"] = 0.90
                out["notes"].append("Shot reduction: home managing the game (comfortable lead)")

            # AWAY TEAM (trailing) — must chase aggressively to advance
            # This COUNTERACTS the possession squeeze applied by the Bayesian engine
            if not is_player_home:
                away_urgency = min(lead, 3)  # how many goals the away team needs
                if prop_type in PASS_PROPS:
                    mult = 1.0 + (away_urgency * 0.07)  # +7-21% pass volume (chasing)
                    out["bayesianMultiplierAway"] = round(min(mult, 1.25), 3)
                    out["notes"].append(
                        f"Away pass prop boost: x{out['bayesianMultiplierAway']:.2f} "
                        f"(must chase {away_urgency} goal(s), forced build-up)"
                    )
                elif prop_type in SHOT_PROPS:
                    mult = 1.0 + (away_urgency * 0.09)  # +9-27% shot volume (must attack)
                    out["bayesianMultiplierAway"] = round(min(mult, 1.30), 3)
                    out["notes"].append(
                        f"Away shot prop boost: x{out['bayesianMultiplierAway']:.2f} "
                        f"(must chase {away_urgency} goal(s), forced attack)"
                    )
                out["gameTypeOverride"] = "high-tempo"

        elif lead == 1:
            # Narrow lead — away team can still advance by scoring once (ET/AET)
            if not is_player_home:
                if prop_type in PASS_PROPS:
                    out["bayesianMultiplierAway"] = 1.07
                    out["notes"].append("Away pass prop boost: x1.07 (one goal away from levelling)")
                elif prop_type in SHOT_PROPS:
                    out["bayesianMultiplierAway"] = 1.09
                    out["notes"].append("Away shot prop boost: x1.09 (must attack to force ET)")
            out["gameTypeOverride"] = "high-tempo"

        elif lead == 0:
            # Level — both teams need to score to advance; high tempo expected
            out["possessionBoostHome"] = 4.0
            out["notes"].append("Aggregate level — both teams need to score, high-tempo expected")
            out["gameTypeOverride"] = "high-tempo"

    return out


# ═══════════════════════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════

async def build_game_situation(
    home_team_id: int,
    away_team_id: int,
    is_player_home: bool,
    league_id: int,
    match_round: str,
    fixture_id: int | None,
    player_team_name: str,
    opponent_name: str,
    prop_type: str,
    season: int = None,
) -> dict:
    """
    Master function — builds a comprehensive game situation context dict.
    Called once per prediction, runs in parallel with other Wave 1/2 fetches.

    Returns:
        {
          "isKnockout": bool,
          "isSecondLeg": bool,
          "aggregate": dict,         (from _parse_aggregate)
          "multipliers": dict,       (from _compute_pressure_multipliers)
          "injuries": dict,          (from _parse_injuries)
          "contextBlock": str,       ready-to-inject text for AI prompt
        }
    """
    if season is None:
        season = CURRENT_SEASON

    is_knockout = _detect_knockout(match_round)
    # Explicit 2nd leg from round name (e.g., "Round of 16 - 2nd Leg")
    is_second_leg = _detect_second_leg(match_round)

    # For knockout matches that don't explicitly say "2nd Leg" in the round name,
    # check H2H history. If there's a recent finished match between these teams in
    # the same competition this season, today is the 2nd leg.
    h2h_task = _fetch_h2h_same_competition(home_team_id, away_team_id, league_id, season) \
        if is_knockout else asyncio.sleep(0, result=[])
    injury_task = _fetch_injuries(fixture_id) if fixture_id else asyncio.sleep(0, result=[])

    h2h_raw, injury_raw = await asyncio.gather(h2h_task, injury_task, return_exceptions=True)
    if isinstance(h2h_raw, Exception):
        h2h_raw = []
    if isinstance(injury_raw, Exception):
        injury_raw = []

    # Parse aggregate (always done for knockout matches)
    aggregate = _parse_aggregate(h2h_raw, home_team_id, away_team_id, fixture_id)

    # Auto-detect 2nd leg from H2H data when round name doesn't say "2nd Leg"
    # If we found a recent first-leg result in the same competition, this is the 2nd leg
    if is_knockout and not is_second_leg and aggregate.get("firstLegFound"):
        is_second_leg = True
        print(f"[SITUATION ENGINE] 2nd leg AUTO-DETECTED via H2H history (round='{match_round}')")

    # Parse injuries
    injuries = _parse_injuries(injury_raw, home_team_id if is_player_home else away_team_id,
                               away_team_id if is_player_home else home_team_id)

    # Compute pressure multipliers
    multipliers = _compute_pressure_multipliers(aggregate, is_knockout, is_second_leg,
                                                is_player_home, prop_type)

    # Build human-readable context block for AI prompt injection
    lines = []

    if is_knockout:
        lines.append(f"[KNOCKOUT MATCH — {match_round}]")

    if is_second_leg:
        lines.append("** THIS IS A 2ND LEG **")
        if aggregate["firstLegFound"]:
            ht = player_team_name if is_player_home else opponent_name
            at = opponent_name if is_player_home else player_team_name
            lines.append(f"First leg result: {aggregate['firstLegScore']}")
            home_agg = aggregate["homeTeamAggregate"]
            away_agg = aggregate["awayTeamAggregate"]
            lines.append(f"Aggregate: {ht} {home_agg}–{away_agg} {at}")
            if aggregate["homeTeamTrailing"]:
                lines.append(
                    f">>> HOME TEAM ({ht}) TRAILS BY {abs(aggregate['goalDeficit'])} GOAL(S) ON AGGREGATE. "
                    f"They MUST score {aggregate['mustWinByGoals']} goal(s) to advance. "
                    f"FORCED HIGH ATTACKING OUTPUT — more passes, more shots, higher possession than seasonal avg. "
                    f"This OVERRIDES the model's passive prior. <<<")
            elif aggregate["goalDeficit"] == 0:
                lines.append(
                    f">>> AGGREGATE LEVEL. Both teams must score to avoid extra time. High-tempo, open game expected. <<<")
            else:
                lines.append(
                    f">>> HOME TEAM ({ht}) LEADS BY {aggregate['goalDeficit']} GOAL(S). "
                    f"May manage the game — slightly reduced urgency unless opponent scores. <<<")
        else:
            lines.append("(First leg data unavailable — treat as standard knockout with elevated intensity)")

    if multipliers["notes"]:
        lines.append("[SITUATIONAL MULTIPLIERS APPLIED TO REVERSE FORMULA]")
        for n in multipliers["notes"]:
            lines.append(f"  - {n}")

    if injuries["summaryText"]:
        lines.append(f"[CONFIRMED ABSENCES] {injuries['summaryText']}")
        if injuries["playerTeamAbsences"]:
            lines.append("  → Factor absences into role/workload — remaining players cover more ground.")
        if injuries["opponentAbsences"]:
            lines.append("  → Opponent missing players — consider which positions are weakened.")

    context_block = "\n".join(lines)

    situation = {
        "isKnockout": is_knockout,
        "isSecondLeg": is_second_leg,
        "aggregate": aggregate,
        "multipliers": multipliers,
        "injuries": injuries,
        "contextBlock": context_block,
    }

    if context_block:
        print(f"[SITUATION ENGINE] {player_team_name} vs {opponent_name}: {context_block[:200]}")

    return situation
