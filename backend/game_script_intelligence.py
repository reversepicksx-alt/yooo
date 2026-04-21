"""
Game Script Intelligence Module  v2
=====================================
Three intelligence layers:

  Layer 1 — Trailing Inflation (existing)
    How does this player's stat change when their team is chasing a game?
    Uses already-fetched player_game_logs → zero extra API calls.

  Layer 2 — Positional Trailing Depth
    Among the player's trailing/chasing games, sub-segment by opponent
    goal threat level (opponents who scored 2+ goals = "dominant" like Tigre
    vs opponents who scored ≤1 goal = "moderate"). Shows if inflation holds
    specifically against aggressive, goal-scoring teams.

  Layer 3 — Opponent Facilitation (new)
    When TODAY's opponent is winning/leading, how many passes do they
    concede to opposing players of the same position?
    Fetches opponent's last N "winning" fixtures (via halftime scores)
    and audits opposing player stats by position.
"""

import asyncio
from utils import api_football_request


# ── Position helpers ──────────────────────────────────────────────────────────

def _api_position_code(player_position: str) -> str:
    """Convert full position label to API-Football 1-letter code."""
    if not player_position:
        return ""
    p = player_position.lower()
    if any(k in p for k in ("goalkeeper", "gk", "keeper")):
        return "G"
    if any(k in p for k in ("forward", "striker", "winger", "attacker", "fw")):
        return "F"
    if any(k in p for k in ("midfielder", "mf", "mid", "pivot", "playmaker")):
        return "M"
    if any(k in p for k in ("defender", "back", "centre-back", "centerback", "cb", "lb", "rb", "df")):
        return "D"
    return ""


def _passes_from_player_stats(stats_list: list) -> float | None:
    """Extract pass count from a /fixtures/players stat block."""
    for stat in (stats_list if isinstance(stats_list, list) else [stats_list]):
        if not isinstance(stat, dict):
            continue
        passes_block = stat.get("passes") or {}
        total = passes_block.get("total")
        if total is not None:
            try:
                return float(total)
            except (TypeError, ValueError):
                pass
    return None


# ── Score parsing ─────────────────────────────────────────────────────────────

def _parse_result(score: str, venue: str) -> str | None:
    if not score or "-" not in str(score):
        return None
    try:
        parts = str(score).strip().split("-")
        home_g, away_g = int(parts[0]), int(parts[1])
        team_g, opp_g  = (home_g, away_g) if venue == "home" else (away_g, home_g)
        return "win" if team_g > opp_g else "draw" if team_g == opp_g else "loss"
    except (ValueError, IndexError):
        return None


def _opponent_goals_from_score(score: str, venue: str) -> int | None:
    """Return how many goals the OPPONENT scored in that game."""
    if not score or "-" not in str(score):
        return None
    try:
        parts = str(score).strip().split("-")
        home_g, away_g = int(parts[0]), int(parts[1])
        return away_g if venue == "home" else home_g
    except (ValueError, IndexError):
        return None


def _stat_from_log(log: dict, prop_type: str) -> float | None:
    field_map = {
        "pass_attempts": ["passes_total", "targetStatPer90"],
        "passes":        ["passes_total"],
        "saves":         ["goals_saves"],
        "shots":         ["shots_total"],
        "tackles":       ["tackles_total"],
        "interceptions": ["tackles_interceptions"],
        "clearances":    ["tackles_clearances"],
    }
    for f in field_map.get(prop_type, [prop_type.replace(".", "_")]):
        v = log.get(f)
        if v is not None:
            try:
                raw = float(v)
                if f == "targetStatPer90" and log.get("minutes", 0) < 85:
                    continue
                return raw
            except (TypeError, ValueError):
                continue
    return None


# ── Layer 3: Opponent facilitation ───────────────────────────────────────────

async def _get_opponent_facilitation(
    opponent_id: int,
    position_code: str,
    prop_type: str = "pass_attempts",
    limit: int = 10,
) -> dict:
    """
    When the opponent is WINNING (leading at HT), how many passes do they
    concede to opposing players with the same positional code?

    Returns:
      avg_allowed       – average stat for the opposing position when opp leads
      sample_size       – number of player-game samples
      fixtures_analysed – number of fixtures scanned
      facilitates       – True if avg_allowed is notably high
    """
    empty = {
        "avg_allowed": None,
        "sample_size": 0,
        "fixtures_analysed": 0,
        "facilitates": False,
        "position_label": position_code,
    }
    if not opponent_id or not position_code:
        return empty

    try:
        raw = await asyncio.wait_for(
            api_football_request("fixtures", {"team": opponent_id, "last": limit}),
            timeout=12.0
        )
    except Exception:
        return empty

    finished = {"FT", "AET", "PEN", "AWD"}
    winning_fixture_ids = []

    for fx in (raw if isinstance(raw, list) else []):
        status = (fx.get("fixture") or {}).get("status", {}).get("short", "")
        if status not in finished:
            continue
        score = fx.get("score") or {}
        ht    = score.get("halftime") or {}
        ht_h, ht_a = ht.get("home"), ht.get("away")
        if ht_h is None or ht_a is None:
            continue
        teams = fx.get("teams") or {}
        home_id = (teams.get("home") or {}).get("id")
        opp_is_home = (home_id == opponent_id)
        was_winning = (
            (opp_is_home and int(ht_h) > int(ht_a)) or
            (not opp_is_home and int(ht_a) > int(ht_h))
        )
        if was_winning:
            fid = (fx.get("fixture") or {}).get("id")
            if fid:
                winning_fixture_ids.append((fid, opponent_id, opp_is_home))

    if not winning_fixture_ids:
        return empty

    # Only fetch up to 6 fixtures to control API quota
    winning_fixture_ids = winning_fixture_ids[:6]

    async def _fetch_opposing_position_stat(fid: int, opp_id: int, opp_is_home: bool):
        try:
            players_raw = await asyncio.wait_for(
                api_football_request("fixtures/players", {"fixture": fid}),
                timeout=8.0
            )
        except Exception:
            return []
        vals = []
        for team_block in (players_raw if isinstance(players_raw, list) else []):
            team_info  = team_block.get("team") or {}
            block_team_id = team_info.get("id")
            if block_team_id == opp_id:
                continue
            for player_entry in (team_block.get("players") or []):
                pos = (player_entry.get("statistics") or [{}])[0].get("games", {}).get("position", "")
                if not pos:
                    pos = player_entry.get("player", {}).get("position", "")
                if pos and pos.upper().startswith(position_code.upper()):
                    stat_val = _passes_from_player_stats(
                        player_entry.get("statistics") or []
                    ) if prop_type == "pass_attempts" else None
                    if stat_val is not None:
                        mins = ((player_entry.get("statistics") or [{}])[0]
                                .get("games", {}).get("minutes") or 0)
                        if mins >= 70:
                            vals.append(stat_val)
        return vals

    tasks = [
        _fetch_opposing_position_stat(fid, opp_id, opp_is_home)
        for fid, opp_id, opp_is_home in winning_fixture_ids
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    all_vals = []
    for r in results:
        if isinstance(r, list):
            all_vals.extend(r)

    if not all_vals:
        return {**empty, "fixtures_analysed": len(winning_fixture_ids)}

    avg = round(sum(all_vals) / len(all_vals), 1)
    facilitates = avg >= 30 if prop_type in ("pass_attempts", "passes") else False
    return {
        "avg_allowed":       avg,
        "sample_size":       len(all_vals),
        "fixtures_analysed": len(winning_fixture_ids),
        "facilitates":       facilitates,
        "position_label":    position_code,
    }


# ── Layer 2: Positional trailing depth ───────────────────────────────────────

def _get_positional_trailing_depth(
    venue_logs: list,
    prop_type: str,
    venue: str,
) -> dict:
    """
    Sub-segment trailing/chasing games by opponent's goal threat:
      - Dominant opponents (scored ≥ 2 goals) = high-press / Tigre-like
      - Moderate opponents (scored 1 goal)
      - Defensive opponents (scored 0 goals = team was winning but drew)

    Returns avg stat per threat tier for trailing games only.
    """
    dominant_vals = []   # opp scored ≥ 2
    moderate_vals = []   # opp scored 1
    controlling_vals = []  # opp scored 0 (team equalised from 0-0)

    for log in venue_logs:
        result_str = _parse_result(log.get("score", ""), log.get("venue", venue))
        if result_str not in ("loss", "draw"):
            continue
        stat = _stat_from_log(log, prop_type)
        if stat is None:
            continue
        opp_goals = _opponent_goals_from_score(log.get("score", ""), log.get("venue", venue))
        if opp_goals is None:
            continue
        if opp_goals >= 2:
            dominant_vals.append(stat)
        elif opp_goals == 1:
            moderate_vals.append(stat)
        else:
            controlling_vals.append(stat)

    def _avg(vals):
        return round(sum(vals) / len(vals), 1) if vals else None

    return {
        "vs_dominant_trailing_avg":   _avg(dominant_vals),
        "vs_moderate_trailing_avg":   _avg(moderate_vals),
        "vs_dominant_sample":         len(dominant_vals),
        "vs_moderate_sample":         len(moderate_vals),
    }


# ── Opponent first-goal probability ──────────────────────────────────────────

async def _get_opponent_first_goal_prob(
    opponent_id: int,
    opponent_venue: str = "",   # "home" or "away" for this specific fixture
    limit: int = 20,
) -> float | None:
    """
    P(opponent scores first) based on HT lead rate, filtered by the venue
    the opponent will play in this specific fixture.
    e.g. if opponent is HOME → only look at their HOME fixtures.
    This eliminates the venue-mixing bias (away games depress home team's HT lead rate).
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
    count, total = 0, 0
    for fx in (raw if isinstance(raw, list) else []):
        status = (fx.get("fixture") or {}).get("status", {}).get("short", "")
        if status not in finished:
            continue
        ht = (fx.get("score") or {}).get("halftime") or {}
        ht_h, ht_a = ht.get("home"), ht.get("away")
        if ht_h is None or ht_a is None:
            continue
        home_id = ((fx.get("teams") or {}).get("home") or {}).get("id")
        opp_is_home = (home_id == opponent_id)

        # Venue filter: only count matching venue fixtures for a fair rate
        if opponent_venue == "home" and not opp_is_home:
            continue
        if opponent_venue == "away" and opp_is_home:
            continue

        total += 1
        if (opp_is_home and int(ht_h) > int(ht_a)) or (not opp_is_home and int(ht_a) > int(ht_h)):
            count += 1

    return round(count / total, 2) if total >= 3 else None


# ── Main function ─────────────────────────────────────────────────────────────

async def get_game_script_intel(
    player_game_logs: list,
    opponent_id: int,
    venue: str,
    prop_type: str = "pass_attempts",
    line: float = 0.0,
    player_id: int = 0,
    team_id: int = 0,
    league_id: int = 0,
    player_position: str = "",
) -> dict:
    """
    Full 3-layer game script intelligence.

    Returns:
      p_team_trails               – P(team goes behind) from venue W/D/L history
      p_opponent_scores_first     – P(opp scores first) from HT data
      trailing_avg                – player avg when chasing
      normal_avg                  – player avg when winning
      inflation_factor            – trailing / normal ratio
      script_adjusted_proj        – scenario-weighted projection
      confidence_delta            – confidence nudge (+/-)
      sample_size / trailing_sample_size
      trailing_near_line          – warning flag
      key_finding                 – human-readable insight
      scenarios                   – frontend scenario list
      positional_depth            – Layer 2 breakdown by opponent threat level
      opponent_facilitation       – Layer 3 positional pass audit when opp leads
    """
    result = {
        "p_team_trails":            None,
        "p_opponent_scores_first":  None,
        "trailing_avg":             None,
        "normal_avg":               None,
        "overall_avg":              None,
        "inflation_factor":         None,
        "inflated_proj":            None,   # overall_avg × inflation_factor (extreme trailing)
        "script_adjusted_proj":     None,
        "confidence_delta":         0,
        "sample_size":              0,
        "trailing_sample_size":     0,
        "trailing_near_line":       False,
        "key_finding":              "",
        "scenarios":                [],
        "positional_depth":         {},
        "opponent_facilitation":    {},
    }

    # ── Filter logs by venue ─────────────────────────────────────────────────
    venue_logs = [g for g in (player_game_logs or []) if g.get("venue") == venue]
    if not venue_logs:
        venue_logs = player_game_logs or []

    # ── Layer 1: Categorise by result ────────────────────────────────────────
    chasing_vals, winning_vals, draw_vals = [], [], []
    total_games = trail_count = draw_count = 0

    for log in venue_logs:
        stat = _stat_from_log(log, prop_type)
        if stat is None:
            continue
        res = _parse_result(log.get("score", ""), log.get("venue", venue))
        total_games += 1
        if res == "loss":
            chasing_vals.append(stat)
            trail_count += 1
        elif res == "win":
            winning_vals.append(stat)
        else:
            draw_vals.append(stat)
            draw_count += 1

    result["sample_size"]          = total_games
    result["trailing_sample_size"] = trail_count + draw_count

    if not total_games:
        result["key_finding"] = "No game log data available for game script analysis."
        return result

    all_chasing = chasing_vals + draw_vals
    all_vals    = chasing_vals + draw_vals + winning_vals

    if all_chasing:
        result["trailing_avg"] = round(sum(all_chasing) / len(all_chasing), 1)
    if winning_vals:
        result["normal_avg"] = round(sum(winning_vals) / len(winning_vals), 1)
    elif draw_vals:
        result["normal_avg"] = round(sum(draw_vals) / len(draw_vals), 1)
    if all_vals:
        result["overall_avg"] = round(sum(all_vals) / len(all_vals), 1)

    t_avg   = result["trailing_avg"]
    n_avg   = result["normal_avg"]
    ov_avg  = result["overall_avg"]

    # Inflation factor: how much does the stat grow in trailing vs winning games?
    # inflated_proj = overall_avg × inflation  (the key formula the user identified)
    # e.g. 39 × 1.68 = 65.5 ≈ today's actual 68
    if t_avg and n_avg and n_avg > 0:
        result["inflation_factor"] = round(t_avg / n_avg, 2)
    if ov_avg and result["inflation_factor"]:
        result["inflated_proj"] = round(ov_avg * result["inflation_factor"], 1)

    p_trail = (trail_count + 0.5 * draw_count) / total_games if total_games else 0
    result["p_team_trails"] = round(min(0.9, max(0.05, p_trail)), 2)

    # ── Layer 2: Positional trailing depth ───────────────────────────────────
    pos_depth = _get_positional_trailing_depth(venue_logs, prop_type, venue)
    result["positional_depth"] = pos_depth

    # ── First-goal probability (venue-specific: only opponent's home/away games) ─
    pos_code        = _api_position_code(player_position)
    facilitation    = {}
    # opponent's venue = opposite of player's team venue
    opponent_venue  = "home" if venue == "away" else "away"
    try:
        p_opp_first = await asyncio.wait_for(
            _get_opponent_first_goal_prob(opponent_id, opponent_venue=opponent_venue),
            timeout=12.0
        )
    except Exception:
        p_opp_first = None

    result["opponent_facilitation"]    = {}
    result["p_opponent_scores_first"]  = p_opp_first

    # ── Script-adjusted projection ───────────────────────────────────────────
    p_trail_used  = result["p_team_trails"] or 0.4
    p_opp_1st     = p_opp_first or 0.45
    combined_p    = min(0.85, max(p_trail_used, p_opp_1st * 0.75))

    inflated = result["inflated_proj"]   # overall_avg × inflation (extreme trailing)
    if inflated is not None and n_avg is not None:
        # Script proj blends: extreme-trailing projection (if chasing) vs normal avg (if winning)
        script_proj = combined_p * inflated + (1 - combined_p) * n_avg
        result["script_adjusted_proj"] = round(script_proj, 1)
        if line > 0:
            delta = 0
            if script_proj > n_avg * 1.08 and script_proj > line * 1.03:
                delta = min(8, round((script_proj / n_avg - 1) * 25))
            elif script_proj < n_avg * 0.93 and script_proj < line * 0.97:
                delta = -min(8, round((1 - script_proj / n_avg) * 25))
            result["confidence_delta"] = delta
    elif t_avg is not None and n_avg is not None:
        script_proj = combined_p * t_avg + (1 - combined_p) * n_avg
        result["script_adjusted_proj"] = round(script_proj, 1)

    # ── Danger zone: use inflated_proj for knife-edge check ───────────────────
    # If inflated_proj (extreme trailing) is near the line, that is a real warning
    # We also keep the t_avg check as a secondary signal
    check_val = inflated if inflated is not None else t_avg
    if line > 0 and check_val is not None and abs(check_val - line) / line < 0.12:
        result["trailing_near_line"] = True

    # ── Key finding ──────────────────────────────────────────────────────────
    infl_val  = result["inflation_factor"] or 1
    infl_proj = result["inflated_proj"]
    p_t_pct   = int((result["p_team_trails"] or 0) * 100)
    p_o_pct   = int((p_opp_first or 0) * 100) if p_opp_first else None
    fac_avg   = (facilitation or {}).get("avg_allowed")
    fac_n     = (facilitation or {}).get("sample_size", 0)
    dom_avg   = pos_depth.get("vs_dominant_trailing_avg")
    dom_n     = pos_depth.get("vs_dominant_sample", 0)

    parts = []
    if t_avg and n_avg and ov_avg:
        pct  = round((infl_val - 1) * 100)
        word = "inflates" if infl_val >= 1.05 else "drops" if infl_val <= 0.95 else "stays flat"
        # Show the corrected formula: overall_avg × inflation = inflated_proj
        proj_str = f" → extreme trailing proj: {ov_avg} × {infl_val} = {infl_proj}" if infl_proj else ""
        parts.append(
            f"Trailing ({venue}): {t_avg} avg trailing, {n_avg} winning, {ov_avg} overall — "
            f"stat {word} {abs(pct)}% when chasing ({p_t_pct}% of {venue} games).{proj_str}"
        )
    elif t_avg and n_avg:
        pct  = round((infl_val - 1) * 100)
        word = "inflates" if infl_val >= 1.05 else "drops" if infl_val <= 0.95 else "stays flat"
        parts.append(
            f"Trailing ({venue}): {t_avg} avg vs {n_avg} winning — "
            f"stat {word} {abs(pct)}% when chasing ({p_t_pct}% of {venue} games)."
        )
    if dom_avg and dom_n >= 2:
        parts.append(
            f"vs dominant opponents (2+ goals, n={dom_n}): {dom_avg} avg when trailing."
        )
    if p_o_pct:
        parts.append(f"Opponent scores first {p_o_pct}% of their games.")
    if result["trailing_near_line"] and infl_proj:
        gap = round(abs(infl_proj - line), 1)
        parts.append(
            f"⚠ KNIFE EDGE: extreme trailing proj {infl_proj} vs line {line} (gap={gap})."
        )
    result["key_finding"] = " ".join(parts) if parts else "Game script analysis complete."

    # ── Scenarios for frontend ───────────────────────────────────────────────
    p_trail_f = result["p_team_trails"] or 0
    scenarios = []

    # Trailing scenario uses inflated_proj (overall × inflation) — the correct formula
    trail_proj = infl_proj if infl_proj is not None else t_avg
    if trail_proj is not None:
        scenarios.append({
            "label":          "Team Trails / Chasing",
            "probability":    round(p_trail_f, 2),
            "projected_stat": trail_proj,
            "vs_line":        round(trail_proj - line, 1) if line > 0 else None,
            "direction":      "OVER" if line > 0 and trail_proj > line else "UNDER",
        })

    if n_avg is not None:
        # For away defenders/midfielders, "winning" = leading away = defensive block
        is_defender_away = (venue == "away" and pos_code in ("D", "M"))
        normal_label = "Leading Away — Defensive Block" if is_defender_away else "Normal / Winning"
        scenarios.append({
            "label":          normal_label,
            "probability":    round(1 - p_trail_f, 2),
            "projected_stat": n_avg,
            "vs_line":        round(n_avg - line, 1) if line > 0 else None,
            "direction":      "OVER" if line > 0 and n_avg > line else "UNDER",
        })
    # Layer 2 scenario: dominant opponent trailing
    if dom_avg is not None and dom_n >= 2:
        scenarios.append({
            "label":          f"vs Dominant Opp (trailing, n={dom_n})",
            "probability":    None,
            "projected_stat": dom_avg,
            "vs_line":        round(dom_avg - line, 1) if line > 0 else None,
            "direction":      "OVER" if line > 0 and dom_avg > line else "UNDER",
        })
    result["scenarios"] = scenarios

    return result
