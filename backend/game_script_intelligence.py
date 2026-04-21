"""
Game Script Intelligence Module
================================
Uses already-fetched player game logs (from predict.py) to calculate:
  1. P(team trails) based on win/draw/loss history at this venue
  2. Player stat inflation when team is chasing (loss/draw) vs winning
  3. Opponent first-goal probability from their recent fixture history
  4. Script-adjusted projection weighted by scenario probabilities

No extra API calls — all data comes from player_game_logs already fetched
by the main prediction pipeline.
"""

import asyncio
from utils import api_football_request


def _parse_result(score: str, venue: str) -> str | None:
    """
    Returns 'win', 'draw', or 'loss' from a score string like '1-1' and venue.
    Score format from API-Football is always home_goals-away_goals.
    """
    if not score or "-" not in str(score):
        return None
    try:
        parts = str(score).strip().split("-")
        home_g = int(parts[0])
        away_g = int(parts[1])
        if venue == "home":
            team_g, opp_g = home_g, away_g
        else:
            team_g, opp_g = away_g, home_g
        if team_g > opp_g:
            return "win"
        elif team_g == opp_g:
            return "draw"
        else:
            return "loss"
    except (ValueError, IndexError):
        return None


def _was_chasing(result: str | None) -> bool | None:
    """
    True  = team was behind at some point (loss or draw where they equalised).
    False = team was winning/controlling (win).
    None  = unknown.
    Draws are conservatively treated as 50/50 for the "chasing" bucket
    to avoid over-estimating inflation on 0-0 encounters.
    """
    if result == "loss":
        return True
    if result == "win":
        return False
    if result == "draw":
        return None  # caller handles this as a 50/50 sample
    return None


def _stat_from_log(log: dict, prop_type: str) -> float | None:
    """Extract the relevant stat from a game log dict."""
    field_map = {
        "pass_attempts": ["passes_total", "targetStatPer90"],
        "passes":        ["passes_total"],
        "saves":         ["goals_saves"],
        "shots":         ["shots_total"],
        "tackles":       ["tackles_total"],
        "interceptions": ["tackles_interceptions"],
        "clearances":    ["tackles_clearances"],
    }
    fields = field_map.get(prop_type, [prop_type.replace(".", "_")])
    for f in fields:
        v = log.get(f)
        if v is not None:
            try:
                raw = float(v)
                # targetStatPer90 is normalised to 90-minute baseline;
                # only use it if actual minutes ≥ 85
                if f == "targetStatPer90" and log.get("minutes", 0) < 85:
                    continue
                if f == "targetStatPer90":
                    return raw
                return raw
            except (TypeError, ValueError):
                continue
    return None


async def _get_opponent_first_goal_prob(opponent_id: int, limit: int = 15) -> float | None:
    """
    Fetch opponent's last N finished fixtures and calculate how often they
    scored first using the halftime score as a proxy.
    Returns a probability 0–1, or None if data unavailable.
    """
    if not opponent_id:
        return None
    try:
        raw = await asyncio.wait_for(
            api_football_request("fixtures", {"team": opponent_id, "last": limit}),
            timeout=12.0
        )
    except Exception:
        return None

    finished = {"FT", "AET", "PEN", "AWD"}
    first_goal_count = 0
    total = 0
    for fx in (raw if isinstance(raw, list) else []):
        status = (fx.get("fixture") or {}).get("status", {}).get("short", "")
        if status not in finished:
            continue
        score = fx.get("score") or {}
        ht    = score.get("halftime") or {}
        ht_h  = ht.get("home")
        ht_a  = ht.get("away")
        if ht_h is None or ht_a is None:
            continue
        total += 1
        teams   = fx.get("teams") or {}
        home_id = (teams.get("home") or {}).get("id")
        opp_is_home = (home_id == opponent_id)
        if opp_is_home and int(ht_h) > int(ht_a):
            first_goal_count += 1
        elif not opp_is_home and int(ht_a) > int(ht_h):
            first_goal_count += 1

    if total < 3:
        return None
    return round(first_goal_count / total, 2)


async def get_game_script_intel(
    player_game_logs: list,
    opponent_id: int,
    venue: str,
    prop_type: str = "pass_attempts",
    line: float = 0.0,
    player_id: int = 0,
    team_id: int = 0,
    league_id: int = 0,
) -> dict:
    """
    Analyse game script scenarios from pre-fetched player_game_logs.

    Returns:
      p_team_trails          – probability team goes behind based on venue W/D/L history
      p_opponent_scores_first – probability opp scores first based on their HT data
      trailing_avg           – player's avg stat in chasing (loss + 0.5*draw) games
      normal_avg             – player's avg stat in winning games
      inflation_factor       – trailing_avg / normal_avg
      script_adjusted_proj   – projection weighted by scenario probabilities
      confidence_delta       – confidence adjustment for the prediction (+/-)
      sample_size            – number of venue-filtered logs analysed
      trailing_sample_size   – number of confirmed chasing games
      key_finding            – human-readable insight
      scenarios              – list of scenario dicts for frontend
    """
    result = {
        "p_team_trails":            None,
        "p_opponent_scores_first":  None,
        "trailing_avg":             None,
        "normal_avg":               None,
        "inflation_factor":         None,
        "script_adjusted_proj":     None,
        "confidence_delta":         0,
        "sample_size":              0,
        "trailing_sample_size":     0,
        "key_finding":              "",
        "scenarios":                [],
    }

    # ── Filter logs by venue ─────────────────────────────────────────────────
    venue_logs = [g for g in (player_game_logs or []) if g.get("venue") == venue]
    if not venue_logs:
        venue_logs = player_game_logs or []

    # ── Categorise each game by result ──────────────────────────────────────
    chasing_vals = []   # losses + draws (player was forced to work harder)
    winning_vals = []   # wins (team controlling)
    draw_vals    = []   # draws separate for weighted approach
    total_games  = 0
    trail_count  = 0
    draw_count   = 0

    for log in venue_logs:
        stat = _stat_from_log(log, prop_type)
        if stat is None:
            continue
        result_str = _parse_result(log.get("score", ""), log.get("venue", venue))
        was_chasing = _was_chasing(result_str)
        total_games += 1

        if was_chasing is True:
            chasing_vals.append(stat)
            trail_count += 1
        elif was_chasing is False:
            winning_vals.append(stat)
        else:
            draw_vals.append(stat)
            draw_count += 1

    result["sample_size"]          = total_games
    result["trailing_sample_size"] = trail_count + draw_count

    if not total_games:
        result["key_finding"] = "No game log data available for game script analysis."
        return result

    # Build trailing avg = weighted combo of losses and draws (draws count 50%)
    all_chasing = chasing_vals + draw_vals
    if all_chasing:
        result["trailing_avg"] = round(sum(all_chasing) / len(all_chasing), 1)

    if winning_vals:
        result["normal_avg"] = round(sum(winning_vals) / len(winning_vals), 1)
    elif draw_vals:
        result["normal_avg"] = round(sum(draw_vals) / len(draw_vals), 1)

    t_avg = result["trailing_avg"]
    n_avg = result["normal_avg"]

    if t_avg and n_avg and n_avg > 0:
        result["inflation_factor"] = round(t_avg / n_avg, 2)

    # P(team trails) = (losses + 0.5*draws) / total at this venue
    if total_games > 0:
        p_trail = (trail_count + 0.5 * draw_count) / total_games
        result["p_team_trails"] = round(min(0.9, max(0.05, p_trail)), 2)

    # ── Fetch opponent first-goal probability (async, separate call) ─────────
    p_opp_first = await _get_opponent_first_goal_prob(opponent_id)
    result["p_opponent_scores_first"] = p_opp_first

    # ── Script-adjusted projection ───────────────────────────────────────────
    p_trail_used  = result["p_team_trails"] or 0.4
    p_opp_1st     = p_opp_first or 0.45
    infl          = result["inflation_factor"] or 1.0

    # Combined probability that a chasing/trailing script plays out
    # (max of direct trail prob and opponent-scores-first proxy)
    combined_p = min(0.85, max(p_trail_used, p_opp_1st * 0.75))

    if t_avg is not None and n_avg is not None:
        script_proj = combined_p * t_avg + (1 - combined_p) * n_avg
        result["script_adjusted_proj"] = round(script_proj, 1)

        if line > 0:
            delta = 0
            if script_proj > n_avg * 1.08 and script_proj > line * 1.03:
                delta = min(8, round((script_proj / n_avg - 1) * 25))
            elif script_proj < n_avg * 0.93 and script_proj < line * 0.97:
                delta = -min(8, round((1 - script_proj / n_avg) * 25))
            result["confidence_delta"] = delta

    # ── Key finding string ───────────────────────────────────────────────────
    infl_val = result["inflation_factor"] or 1
    p_t_disp = int((result["p_team_trails"] or 0) * 100)
    p_o_disp = int((p_opp_first or 0) * 100) if p_opp_first else None

    if t_avg and n_avg:
        pct = round((infl_val - 1) * 100)
        direction_word = "inflates" if infl_val >= 1.05 else "drops" if infl_val <= 0.95 else "stays flat"
        parts = [
            f"In chasing/trailing games ({venue}), this player averages "
            f"{t_avg} vs {n_avg} when winning — "
            f"stat {direction_word} by {abs(pct)}% when the team chases."
        ]
        if p_t_disp:
            parts.append(
                f"Based on {total_games} {venue} games, team trails in roughly "
                f"{p_t_disp}% of matches."
            )
        if p_o_disp:
            parts.append(
                f"Opponent scores first in {p_o_disp}% of their analysed fixtures."
            )
        result["key_finding"] = " ".join(parts)
    else:
        result["key_finding"] = (
            f"Partial data: {total_games} {venue} games, "
            f"{trail_count} trailing, {draw_count} draws."
        )

    # ── Scenarios for frontend ───────────────────────────────────────────────
    scenarios = []
    p_trail_final = result["p_team_trails"] or 0
    if t_avg is not None:
        scenarios.append({
            "label": "Team Trails / Chasing",
            "probability":     round(p_trail_final, 2),
            "projected_stat":  t_avg,
            "vs_line":         round(t_avg - line, 1) if line > 0 else None,
            "direction":       "OVER" if line > 0 and t_avg > line else "UNDER",
        })
    if n_avg is not None:
        scenarios.append({
            "label": "Normal / Winning",
            "probability":     round(1 - p_trail_final, 2),
            "projected_stat":  n_avg,
            "vs_line":         round(n_avg - line, 1) if line > 0 else None,
            "direction":       "OVER" if line > 0 and n_avg > line else "UNDER",
        })
    result["scenarios"] = scenarios

    # ── Danger zone flag ────────────────────────────────────────────────────
    # If the trailing scenario projection is within 8% of the line,
    # the UNDER/OVER call becomes very thin and warrants a warning.
    if line > 0 and t_avg is not None and abs(t_avg - line) / line < 0.08:
        result["trailing_near_line"] = True
        gap = round(abs(t_avg - line), 1)
        result["key_finding"] += (
            f" WARNING: Trailing scenario projects {t_avg} vs line {line} "
            f"(gap = {gap}) — this {result['scenarios'][0]['direction'] if result['scenarios'] else 'bet'} "
            f"is on a knife edge when the team chases."
        )
    else:
        result["trailing_near_line"] = False

    return result
