"""
Basketball (NBA) Prediction Engine v2
- Advanced per-minute analytics, role classification, line proximity, blowout risk
- 4-AI consensus engine with first-3-wins pattern
- Strict <55s execution budget
"""
import json
import uuid
import asyncio as aio
import statistics as stats_mod
import traceback
import time as _t
import math
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException
from openai import OpenAI

from config import db, EMERGENT_LLM_KEY, XAI_API_KEY
from models import BasketballPredictionRequest
from basketball_utils import (
    search_nba_teams, get_player_season_stats,
    get_team_games, get_h2h, get_team_stats, get_standings,
    parse_player_stat, parse_game_for_team,
    BBALL_CURRENT_SEASON, get_basketball_odds,
)
from basketball_cache import get_bball_player_by_name, get_bball_team_by_name, search_bball_teams

router = APIRouter(prefix="/api", tags=["basketball"])

BBALL_STAT_FIELD_MAP = {
    "points": "points",
    "rebounds": "rebounds",
    "assists": "assists",
    "pts_reb_ast": None,
    "pts_reb": None,
    "pts_ast": None,
    "reb_ast": None,
    "blk_stl": None,
    "steals": "steals",
    "blocks": "blocks",
    "turnovers": "turnovers",
    "three_pointers": "tpm",
    "fgm": "fgm",
    "ftm": "ftm",
    "fga": "fga",
    "fta": "fta",
    "tpa": "tpa",
}

BBALL_PROP_LABELS = {
    "points": "Points",
    "rebounds": "Rebounds",
    "assists": "Assists",
    "pts_reb_ast": "Pts+Reb+Ast",
    "pts_reb": "Pts+Reb",
    "pts_ast": "Pts+Ast",
    "reb_ast": "Reb+Ast",
    "blk_stl": "Blk+Stl",
    "steals": "Steals",
    "blocks": "Blocks",
    "turnovers": "Turnovers",
    "three_pointers": "3-Point FG Made",
    "fgm": "FG Made",
    "ftm": "FT Made",
    "fga": "FG Attempted",
    "fta": "FT Attempted",
    "tpa": "3PT Attempted",
}


def parse_minutes_float(min_str) -> float:
    """Convert '32:15' or '32' to 32.25 float minutes."""
    if not min_str:
        return 0.0
    s = str(min_str).strip()
    if ":" in s:
        parts = s.split(":")
        try:
            return int(parts[0]) + int(parts[1]) / 60.0
        except (ValueError, IndexError):
            return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def get_stat_value(parsed: dict, prop_type: str):
    """Extract the relevant stat value from a parsed player game stat."""
    pt = prop_type.lower().replace("+", "_").replace(" ", "_").replace("-", "_")
    label_map = {
        "pts_reb_ast": "pts_reb_ast",
        "pts_reb": "pts_reb",
        "pts_ast": "pts_ast",
        "reb_ast": "reb_ast",
        "blk_stl": "blk_stl",
        "3_pointers_made": "three_pointers",
        "3_point_fg_made": "three_pointers",
        "fg_made": "fgm", "ft_made": "ftm",
        "fg_attempted": "fga", "ft_attempted": "fta",
        "3pt_attempted": "tpa",
    }
    pt = label_map.get(pt, pt)

    if pt == "pts_reb_ast":
        return (parsed.get("points", 0) or 0) + (parsed.get("rebounds", 0) or 0) + (parsed.get("assists", 0) or 0)
    if pt == "reb_ast":
        return (parsed.get("rebounds", 0) or 0) + (parsed.get("assists", 0) or 0)
    if pt == "pts_reb":
        return (parsed.get("points", 0) or 0) + (parsed.get("rebounds", 0) or 0)
    if pt == "pts_ast":
        return (parsed.get("points", 0) or 0) + (parsed.get("assists", 0) or 0)
    if pt == "blk_stl":
        return (parsed.get("blocks", 0) or 0) + (parsed.get("steals", 0) or 0)
    field = BBALL_STAT_FIELD_MAP.get(pt, pt)
    return parsed.get(field, 0) or 0 if field else 0


def compute_advanced_analytics(player_game_logs: list, prop_type: str, line: float, venue: str):
    """
    Compute advanced analytics from player game logs.
    Returns a dict with all computed metrics for the data digest.
    """
    if not player_game_logs:
        return None

    stat_values = [g["targetStat"] for g in player_game_logs if g.get("targetStat") is not None]
    if not stat_values:
        return None

    # === MINUTES ANALYSIS ===
    minutes_list = [parse_minutes_float(g.get("minutes")) for g in player_game_logs]
    minutes_played = [m for m in minutes_list if m > 0]
    avg_minutes = round(sum(minutes_played) / len(minutes_played), 1) if minutes_played else 0
    last5_minutes = minutes_played[:5]
    avg_minutes_l5 = round(sum(last5_minutes) / len(last5_minutes), 1) if last5_minutes else avg_minutes
    minutes_trend = round(avg_minutes_l5 - avg_minutes, 1)

    # === PER-MINUTE RATES ===
    per_min_values = []
    for i, g in enumerate(player_game_logs):
        mins = minutes_list[i]
        if mins >= 5:  # Only count games with meaningful minutes
            per_min_values.append(g["targetStat"] / mins)
    avg_per_min = round(sum(per_min_values) / len(per_min_values), 3) if per_min_values else 0
    projected_from_rate = round(avg_per_min * avg_minutes, 1) if avg_per_min and avg_minutes else 0

    # === ROLE CLASSIFICATION ===
    if avg_minutes >= 32:
        role = "STAR"
        role_desc = f"Primary option, {avg_minutes} min/game"
    elif avg_minutes >= 26:
        role = "STARTER"
        role_desc = f"Key starter, {avg_minutes} min/game"
    elif avg_minutes >= 16:
        role = "ROTATION"
        role_desc = f"Rotation player, {avg_minutes} min/game"
    else:
        role = "BENCH"
        role_desc = f"Limited role, {avg_minutes} min/game"

    # === BASIC STATS ===
    all_vals = stat_values
    n = len(all_vals)
    avg_all = round(sum(all_vals) / n, 1)
    recent_10 = stat_values[:10]
    recent_5 = stat_values[:5]
    avg_10 = round(sum(recent_10) / len(recent_10), 1) if recent_10 else avg_all
    avg_5 = round(sum(recent_5) / len(recent_5), 1) if recent_5 else avg_all
    min_val = min(all_vals)
    max_val = max(all_vals)
    std_dev = round(stats_mod.stdev(all_vals), 2) if n >= 3 else 0
    median_val = round(stats_mod.median(all_vals), 1)

    # === LINE PROXIMITY / Z-SCORE ===
    if std_dev > 0:
        z_score = round(abs(avg_all - line) / std_dev, 2)
    else:
        z_score = 0

    if z_score < 0.3:
        edge_signal = "COIN FLIP — NO CLEAR EDGE"
        edge_strength = "none"
    elif z_score < 0.6:
        edge_signal = "SLIGHT LEAN"
        edge_strength = "slight"
    elif z_score < 1.0:
        edge_signal = "MODERATE EDGE"
        edge_strength = "moderate"
    elif z_score < 1.5:
        edge_signal = "STRONG EDGE"
        edge_strength = "strong"
    else:
        edge_signal = "VERY STRONG EDGE"
        edge_strength = "very_strong"

    # === OVER/UNDER RATE (the most critical metric) ===
    over_count = sum(1 for v in all_vals if v > line)
    under_count = sum(1 for v in all_vals if v < line)
    push_count = n - over_count - under_count
    over_pct = round(over_count / n * 100) if n else 50
    under_pct = round(under_count / n * 100) if n else 50

    # Recent over rate (last 10)
    over_l10 = sum(1 for v in recent_10 if v > line)
    over_pct_l10 = round(over_l10 / len(recent_10) * 100) if recent_10 else 50

    # Statistical lean
    if over_pct >= 65:
        stat_lean = "OVER"
        lean_strength = "strong"
    elif over_pct >= 55:
        stat_lean = "OVER"
        lean_strength = "slight"
    elif under_pct >= 65:
        stat_lean = "UNDER"
        lean_strength = "strong"
    elif under_pct >= 55:
        stat_lean = "UNDER"
        lean_strength = "slight"
    else:
        stat_lean = "TOSS-UP"
        lean_strength = "none"

    # === CONSISTENCY SCORE ===
    within_band = sum(1 for v in all_vals if abs(v - avg_all) <= max(avg_all * 0.2, 2))
    consistency_pct = round(within_band / n * 100) if n else 0
    if consistency_pct >= 70:
        consistency_label = "VERY CONSISTENT"
    elif consistency_pct >= 50:
        consistency_label = "MODERATE"
    else:
        consistency_label = "BOOM-BUST (HIGH VARIANCE)"

    # === STREAK DETECTION ===
    current_streak = 0
    if stat_values:
        first_dir = "over" if stat_values[0] > line else ("under" if stat_values[0] < line else None)
        if first_dir == "over":
            for v in stat_values:
                if v > line:
                    current_streak += 1
                else:
                    break
        elif first_dir == "under":
            for v in stat_values:
                if v < line:
                    current_streak -= 1
                else:
                    break

    streak_label = f"{abs(current_streak)}-game {'OVER' if current_streak > 0 else 'UNDER'} streak" if current_streak != 0 else "No active streak"

    # === VENUE SPLITS ===
    home_vals = [g["targetStat"] for g in player_game_logs if g.get("venue") == "home" and g.get("targetStat") is not None]
    away_vals = [g["targetStat"] for g in player_game_logs if g.get("venue") == "away" and g.get("targetStat") is not None]
    venue_vals = [g["targetStat"] for g in player_game_logs if g.get("venue") == venue and g.get("targetStat") is not None]
    home_avg = round(sum(home_vals) / len(home_vals), 1) if home_vals else None
    away_avg = round(sum(away_vals) / len(away_vals), 1) if away_vals else None
    venue_avg = round(sum(venue_vals) / len(venue_vals), 1) if venue_vals else None
    venue_over_pct = round(sum(1 for v in venue_vals if v > line) / len(venue_vals) * 100) if venue_vals else None

    # === MOMENTUM ===
    momentum = round(avg_5 - avg_all, 1) if recent_5 else 0
    if momentum > 2:
        momentum_label = "HOT"
    elif momentum < -2:
        momentum_label = "COLD"
    else:
        momentum_label = "NEUTRAL"

    return {
        "n": n,
        "avg_all": avg_all, "avg_10": avg_10, "avg_5": avg_5,
        "median": median_val, "min_val": min_val, "max_val": max_val, "std_dev": std_dev,
        "avg_minutes": avg_minutes, "avg_minutes_l5": avg_minutes_l5, "minutes_trend": minutes_trend,
        "avg_per_min": avg_per_min, "projected_from_rate": projected_from_rate,
        "role": role, "role_desc": role_desc,
        "z_score": z_score, "edge_signal": edge_signal, "edge_strength": edge_strength,
        "over_count": over_count, "under_count": under_count, "push_count": push_count,
        "over_pct": over_pct, "under_pct": under_pct,
        "over_pct_l10": over_pct_l10,
        "stat_lean": stat_lean, "lean_strength": lean_strength,
        "consistency_pct": consistency_pct, "consistency_label": consistency_label,
        "streak_label": streak_label, "current_streak": current_streak,
        "home_avg": home_avg, "away_avg": away_avg,
        "venue_avg": venue_avg, "venue_over_pct": venue_over_pct,
        "momentum": momentum, "momentum_label": momentum_label,
        "home_vals_n": len(home_vals), "away_vals_n": len(away_vals), "venue_vals_n": len(venue_vals),
    }


def build_data_digest(analytics: dict, player_game_logs: list, prop_label: str, prop_type: str,
                       line: float, venue: str, req, team_games_parsed: list,
                       opp_games_raw: list, h2h_data: list, player_id: int, odds_data: dict = None):
    """Build the complete data digest string for the AI prompt."""
    a = analytics
    parts = []

    # ══════════════════════════════════════════
    # SECTION 1: STATISTICAL VERDICT (most important — AI reads this first)
    # ══════════════════════════════════════════
    parts.append(f"""=== STATISTICAL VERDICT for {req.playerName} {prop_label} LINE {line} ===
OVER RATE: {a['over_count']}/{a['n']} games ({a['over_pct']}%) went OVER {line}
UNDER RATE: {a['under_count']}/{a['n']} games ({a['under_pct']}%) went UNDER {line}
RECENT OVER RATE (L10): {a['over_pct_l10']}%
STATISTICAL LEAN: {a['stat_lean']} ({a['lean_strength']} signal)
EDGE SIGNAL: {a['edge_signal']} (z-score={a['z_score']})
>>> YOU MUST WEIGHT THIS HEAVILY. If over-rate is >60%, default to OVER. If under-rate is >60%, default to UNDER. Only deviate with STRONG matchup/situational evidence. <<<""")

    # ══════════════════════════════════════════
    # SECTION 1b: MONEYLINE & GAME TYPE
    # ══════════════════════════════════════════
    if odds_data and odds_data.get("homeOdds"):
        fav = odds_data.get("favorite", "Unknown")
        home_ml = odds_data.get("homeOdds", "")
        away_ml = odds_data.get("awayOdds", "")
        home_name = odds_data.get("homeName", "")
        away_name = odds_data.get("awayName", "")

        # Determine game type from odds spread
        try:
            home_val = int(home_ml.replace("+", ""))
            away_val = int(away_ml.replace("+", ""))
            spread = abs(home_val - away_val)
        except (ValueError, AttributeError):
            spread = 0

        if spread > 400:
            game_type = "HEAVY MISMATCH — expect blowout, starters pulled early in 4Q"
        elif spread > 200:
            game_type = "CLEAR FAVORITE — moderate blowout risk, could affect late-game minutes"
        elif spread > 80:
            game_type = "SLIGHT FAVORITE — competitive game expected, full minutes likely"
        else:
            game_type = "PICK'EM — very close matchup, expect full minutes and competitive game"

        parts.append(f"""=== MONEYLINE & GAME TYPE ===
{home_name}: {home_ml} | {away_name}: {away_ml}
Favorite: {fav}
Game Type: {game_type}
>>> Impact: {'Blowout risk = starters on winning team may sit 4Q. REDUCES stat totals for stars on favored team.' if spread > 200 else 'Full minutes expected for key players. Standard stat projections apply.'} <<<""")

    # ══════════════════════════════════════════
    # SECTION 2: PLAYER PROFILE & ROLE
    # ══════════════════════════════════════════
    parts.append(f"""=== PLAYER PROFILE ===
Role: {a['role']} — {a['role_desc']}
Minutes: Season avg {a['avg_minutes']} | Last 5 avg {a['avg_minutes_l5']} | Trend: {'+' if a['minutes_trend'] > 0 else ''}{a['minutes_trend']} min
Per-Minute Rate ({prop_label}): {a['avg_per_min']}/min
Rate-Based Projection: {a['avg_per_min']}/min x {a['avg_minutes']} min = {a['projected_from_rate']} {prop_label.lower()}
>>> Compare this rate-based projection to the line ({line}). If projection is clearly above line, lean OVER. <<<""")

    # ══════════════════════════════════════════
    # SECTION 3: SEASON AVERAGES & DISTRIBUTION
    # ══════════════════════════════════════════
    parts.append(f"""=== {prop_label.upper()} DISTRIBUTION ({a['n']} games) ===
Season avg: {a['avg_all']} | Median: {a['median']} | Last 10: {a['avg_10']} | Last 5: {a['avg_5']}
Range: {a['min_val']} to {a['max_val']} | StdDev: {a['std_dev']}
Momentum: {a['momentum_label']} ({'+' if a['momentum'] > 0 else ''}{a['momentum']} vs season avg)
Consistency: {a['consistency_label']} ({a['consistency_pct']}% of games within band)
Streak: {a['streak_label']}""")

    # ══════════════════════════════════════════
    # SECTION 4: VENUE SPLITS
    # ══════════════════════════════════════════
    home_str = f"{a['home_avg']} ({a['home_vals_n']} games)" if a['home_avg'] is not None else "N/A"
    away_str = f"{a['away_avg']} ({a['away_vals_n']} games)" if a['away_avg'] is not None else "N/A"
    venue_str = f"{a['venue_avg']} ({a['venue_vals_n']} games)" if a['venue_avg'] is not None else "N/A"
    venue_over_str = f"{a['venue_over_pct']}%" if a['venue_over_pct'] is not None else "N/A"
    parts.append(f"""=== VENUE SPLITS ===
Home avg: {home_str} | Away avg: {away_str}
This game venue ({venue.upper()}): avg {venue_str}, OVER rate at this venue: {venue_over_str}""")

    # ══════════════════════════════════════════
    # SECTION 5: GAME-BY-GAME LOG (recent 12)
    # ══════════════════════════════════════════
    game_lines = []
    for g in player_game_logs[:12]:
        ps = g.get("playerStats", {})
        mins = parse_minutes_float(g.get("minutes"))
        stat_val = g.get("targetStat", 0)
        hit = "OVER" if stat_val > line else "UNDER" if stat_val < line else "PUSH"
        margin = g.get("teamScore", 0) - g.get("oppScore", 0)
        margin_str = f"+{margin}" if margin > 0 else str(margin)
        game_lines.append(
            f"  {g.get('date','')} vs {g.get('opponent','')} ({g.get('venue','')}) "
            f"{g.get('result','')}({margin_str}): "
            f"{prop_label}={stat_val} [{hit}] | "
            f"PTS={ps.get('points',0)} REB={ps.get('rebounds',0)} AST={ps.get('assists',0)} "
            f"3PM={ps.get('tpm',0)} MIN={round(mins,0)}"
        )
    parts.append("=== GAME LOG (Most Recent 12) ===\n" + "\n".join(game_lines))

    # ══════════════════════════════════════════
    # SECTION 6: OPPONENT DEFENSIVE CONTEXT
    # ══════════════════════════════════════════
    if opp_games_raw:
        opp_parsed = [parse_game_for_team(g, req.opponentId) for g in opp_games_raw[:15]]
        opp_wins = sum(1 for g in opp_parsed if g.get("result") == "W")
        opp_losses = sum(1 for g in opp_parsed if g.get("result") == "L")
        opp_avg_scored = round(sum(g.get("teamScore", 0) for g in opp_parsed) / max(len(opp_parsed), 1), 1)
        opp_avg_allowed = round(sum(g.get("oppScore", 0) for g in opp_parsed) / max(len(opp_parsed), 1), 1)
        total_pace = round(opp_avg_scored + opp_avg_allowed, 1)
        pace_label = "HIGH-PACE" if total_pace > 230 else "MID-PACE" if total_pace > 215 else "LOW-PACE"

        # Blowout risk: how many games were decided by 15+?
        blowout_games = sum(1 for g in opp_parsed if abs(g.get("teamScore", 0) - g.get("oppScore", 0)) >= 15)
        blowout_pct = round(blowout_games / len(opp_parsed) * 100) if opp_parsed else 0

        parts.append(f"""=== OPPONENT {req.opponentName} DEFENSE (Last {len(opp_parsed)} games) ===
Record: {opp_wins}W-{opp_losses}L | Points scored: {opp_avg_scored} | Points ALLOWED: {opp_avg_allowed}
Game pace: {pace_label} ({total_pace} combined avg)
Blowout frequency: {blowout_pct}% of games decided by 15+
>>> {'High blowout risk = possible reduced minutes for starters on winning side.' if blowout_pct > 30 else 'Normal game flow expected.'} <<<""")

    # TEAM recent form
    if team_games_parsed:
        recent = team_games_parsed[:10]
        wins = sum(1 for g in recent if g.get("result") == "W")
        losses = sum(1 for g in recent if g.get("result") == "L")
        avg_score = round(sum(g.get("teamScore", 0) for g in recent) / max(len(recent), 1), 1)
        avg_opp = round(sum(g.get("oppScore", 0) for g in recent) / max(len(recent), 1), 1)
        team_margin = round(avg_score - avg_opp, 1)
        parts.append(f"""=== {req.teamName} RECENT FORM (Last {len(recent)}) ===
Record: {wins}W-{losses}L | Avg scored: {avg_score} | Avg allowed: {avg_opp} | Net: {'+' if team_margin > 0 else ''}{team_margin}""")

    # ══════════════════════════════════════════
    # SECTION 6b: MATCHUP & GAME TYPE CONTEXT
    # ══════════════════════════════════════════
    if team_games_parsed and opp_games_raw:
        opp_parsed_form = [parse_game_for_team(g, req.opponentId) for g in opp_games_raw[:10]]
        team_wins = sum(1 for g in team_games_parsed[:10] if g.get("result") == "W")
        team_losses = sum(1 for g in team_games_parsed[:10] if g.get("result") == "L")
        opp_wins_form = sum(1 for g in opp_parsed_form if g.get("result") == "W")
        opp_losses_form = sum(1 for g in opp_parsed_form if g.get("result") == "L")
        team_wpct = round(team_wins / max(team_wins + team_losses, 1) * 100)
        opp_wpct = round(opp_wins_form / max(opp_wins_form + opp_losses_form, 1) * 100)

        if team_wpct > opp_wpct + 20:
            favorite = req.teamName
            game_type = "MISMATCH — expect blowout risk, starters may rest in 4Q"
        elif opp_wpct > team_wpct + 20:
            favorite = req.opponentName
            game_type = "MISMATCH — expect blowout risk, starters may rest in 4Q"
        elif abs(team_wpct - opp_wpct) < 10:
            favorite = "TOSS-UP"
            game_type = "COMPETITIVE — expect full minutes, close game"
        else:
            favorite = req.teamName if team_wpct > opp_wpct else req.opponentName
            game_type = "LEAN — slight edge, likely competitive"

        parts.append(f"""=== MATCHUP & GAME TYPE ===
{req.teamName}: {team_wins}W-{team_losses}L ({team_wpct}% win rate L10)
{req.opponentName}: {opp_wins_form}W-{opp_losses_form}L ({opp_wpct}% win rate L10)
Favorite: {favorite}
Expected game type: {game_type}
>>> Impact on prop: {'Blowout risk = reduced minutes for starters on winning side. Bench gets run.' if 'MISMATCH' in game_type else 'Full minutes expected for key players.'} <<<""")

    # ══════════════════════════════════════════
    # SECTION 7: H2H
    # ══════════════════════════════════════════
    if h2h_data:
        h2h_lines = []
        for h in h2h_data[:5]:
            hp = parse_game_for_team(h, req.teamId)
            h2h_lines.append(f"  {hp.get('date','')} {hp.get('result','')} {hp.get('teamScore',0)}-{hp.get('oppScore',0)}")
        parts.append(f"=== H2H (Last {min(5, len(h2h_data))}) ===\n" + "\n".join(h2h_lines))

    # H2H player stats
    if h2h_data and player_id:
        h2h_game_ids = set(h.get("id") for h in h2h_data[:5] if h.get("id"))
        h2h_player_vals = [g.get("targetStat", 0) for g in player_game_logs if g.get("gameId") in h2h_game_ids]
        if h2h_player_vals:
            h2h_avg = round(sum(h2h_player_vals) / len(h2h_player_vals), 1)
            h2h_over = sum(1 for v in h2h_player_vals if v > line)
            parts.append(f"""=== H2H PLAYER {prop_label.upper()} vs {req.opponentName} ===
Avg: {h2h_avg} | OVER {line} in {h2h_over}/{len(h2h_player_vals)} games | Values: {h2h_player_vals}""")

    return "\n\n".join(parts)


async def build_player_game_logs(player_id: int, team_id: int, prop_type: str, team_games: list, season: str = None):
    """
    Build player game logs by:
    1. Fetching ALL player stats for the season in a SINGLE API call
    2. Cross-referencing with team games for venue/opponent context
    """
    raw_stats = await get_player_season_stats(player_id, season)
    if not raw_stats:
        return []

    # Build a game_id → game_info lookup from team games
    game_lookup = {}
    for g in team_games:
        gid = g.get("id")
        if gid:
            game_lookup[gid] = parse_game_for_team(g, team_id)

    logs = []
    for entry in raw_stats:
        parsed = parse_player_stat(entry)
        game_id = parsed.get("gameId")
        stat_val = get_stat_value(parsed, prop_type)

        # Filter out DNP / injury exit games (< 5 minutes played)
        # These 0-stat entries massively deflate averages and corrupt projections
        mins = parse_minutes_float(parsed.get("minutes", "0:00"))
        if mins < 5:
            continue

        # Get game context from team games lookup
        game_info = game_lookup.get(game_id, {})

        logs.append({
            "gameId": game_id,
            "date": game_info.get("date", ""),
            "venue": game_info.get("venue", ""),
            "opponent": game_info.get("opponent", ""),
            "result": game_info.get("result", ""),
            "teamScore": game_info.get("teamScore", 0),
            "oppScore": game_info.get("oppScore", 0),
            "targetStat": stat_val,
            "minutes": parsed.get("minutes", "0:00"),
            "playerStats": parsed,
        })

    # Sort by date descending (most recent first), filter to games with context
    logs_with_context = [lg for lg in logs if lg.get("date")]
    logs_without = [lg for lg in logs if not lg.get("date")]
    logs_with_context.sort(key=lambda x: x["date"], reverse=True)

    return logs_with_context + logs_without


@router.post("/basketball/predict")
async def basketball_predict(req: BasketballPredictionRequest):
    try:
        t0 = _t.time()

        # ═══════════════════════════════════════
        # WAVE 1: Find player + parallel data fetch
        # ═══════════════════════════════════════
        async def safe_fetch(coro):
            try:
                return await coro
            except Exception:
                return None

        # Find the player ID via CACHE (instant) with live API fallback
        player_info = await get_bball_player_by_name(req.playerName, team_id=req.teamId)
        if not player_info:
            player_info = await get_bball_player_by_name(req.playerName)
        if not player_info:
            # Last resort: live API search
            from basketball_utils import search_player
            live_player = await search_player(req.playerName, req.teamId)
            if live_player:
                player_info = {"playerId": live_player.get("id"), "name": live_player.get("name", ""), "position": live_player.get("position", "")}

        player_id = player_info.get("playerId") if player_info else None
        print(f"[BBALL] Player lookup: '{req.playerName}' -> ID={player_id} ({player_info.get('name') if player_info else 'NOT FOUND'})")

        # Now fetch everything in parallel
        team_games_task = get_team_games(req.teamId)
        h2h_task = safe_fetch(get_h2h(req.teamId, req.opponentId))
        team_stats_task = safe_fetch(get_team_stats(req.teamId))
        opp_stats_task = safe_fetch(get_team_stats(req.opponentId))
        standings_task = safe_fetch(get_standings())
        opp_games_task = safe_fetch(get_team_games(req.opponentId))
        odds_task = safe_fetch(get_basketball_odds(req.teamId, req.opponentId))

        team_games, h2h_data, team_stats, opp_stats, standings_raw, opp_games_raw, odds_data = await aio.gather(
            team_games_task, h2h_task, team_stats_task, opp_stats_task, standings_task, opp_games_task, odds_task
        )
        team_games = team_games or []
        h2h_data = h2h_data or []
        opp_games_raw = opp_games_raw or []
        odds_data = odds_data or {}

        # Sort H2H by date descending and filter to finished games only
        if h2h_data:
            h2h_data = [g for g in h2h_data if g.get("status", {}).get("short") in ("FT", "AOT")]
            h2h_data.sort(key=lambda g: g.get("date", ""), reverse=True)
            print(f"[BBALL H2H] {len(h2h_data)} finished H2H games found (most recent: {(h2h_data[0].get('date','')[:10]) if h2h_data else 'none'})")

        # Build player game logs (uses single API call for ALL season stats)
        player_game_logs = []
        prior_season_logs = []
        if player_id:
            player_game_logs = await build_player_game_logs(player_id, req.teamId, req.propType, team_games)

            # Fetch 3 prior seasons of player stats for H2H matching (parallel)
            if h2h_data:
                yr = int(BBALL_CURRENT_SEASON.split("-")[0])
                prior_seasons = [f"{yr-i}-{yr-i+1}" for i in range(1, 4)]  # e.g. 2024-2025, 2023-2024, 2022-2023

                async def _fetch_season_logs(season_str):
                    try:
                        tg = await get_team_games(req.teamId, season_str)
                        if tg:
                            return await build_player_game_logs(player_id, req.teamId, req.propType, tg, season_str)
                    except Exception:
                        pass
                    return []

                prior_results = await aio.gather(*[_fetch_season_logs(s) for s in prior_seasons])
                for logs in prior_results:
                    prior_season_logs.extend(logs)
                if prior_season_logs:
                    print(f"[BBALL H2H] Fetched {len(prior_season_logs)} logs from {len(prior_seasons)} prior seasons")

        print(f"[BBALL TIMING] Wave 1: {_t.time()-t0:.1f}s | Player logs: {len(player_game_logs)} | Team games: {len(team_games)}")

        # Player position for card display
        player_pos = player_info.get("position", "") if player_info else ""

        # ═══════════════════════════════════════
        # ADVANCED ANALYTICS & DATA DIGEST
        # ═══════════════════════════════════════
        player_venue = req.venue.lower()
        team_games_parsed = [parse_game_for_team(g, req.teamId) for g in team_games[:20]]
        prop_label = BBALL_PROP_LABELS.get(req.propType, req.propType)

        # Compute advanced analytics
        analytics = compute_advanced_analytics(player_game_logs, req.propType, req.line, player_venue)

        if analytics:
            data_digest = build_data_digest(
                analytics, player_game_logs, prop_label, req.propType,
                req.line, player_venue, req, team_games_parsed,
                opp_games_raw, h2h_data, player_id, odds_data
            )
        else:
            data_digest = f"LIMITED DATA: No game logs found for {req.playerName}. Use general knowledge only."

        print(f"[BBALL TIMING] Data digest: {_t.time()-t0:.1f}s, {len(data_digest)} chars")
        if analytics:
            print(f"[BBALL ANALYTICS] Over-rate: {analytics['over_pct']}% | Lean: {analytics['stat_lean']} | Edge: {analytics['edge_signal']} | Role: {analytics['role']} | Min: {analytics['avg_minutes']} | Rate-proj: {analytics['projected_from_rate']}")

        # =============================================
        # ═══════════════════════════════════════
        # REAL RECENT SAMPLES for frontend
        # ═══════════════════════════════════════
        real_recent_samples = []
        for g in player_game_logs[:20]:
            val = g.get("targetStat")
            if val is not None:
                real_recent_samples.append({
                    "date": g.get("date", ""),
                    "opponent": g.get("opponent", ""),
                    "value": val,
                    "minutesPlayed": g.get("minutes", "0"),
                    "venue": g.get("venue", ""),
                    "result": g.get("result", ""),
                })

        # ═══════════════════════════════════════
        # MULTI-AI CONSENSUS ENGINE (3 AIs)
        # Gemini 2.5 Pro (GE) + Grok (GK) + GPT-5.2 (GP)
        # ═══════════════════════════════════════

        # Pre-compute the statistical lean guidance for the AI
        stat_lean_guidance = ""
        if analytics:
            a = analytics
            stat_lean_guidance = f"""
CRITICAL PRE-COMPUTED SIGNALS (override gut feel with data):
- OVER-RATE: {a['over_pct']}% of {a['n']} games went OVER {req.line}. Recent (L10): {a['over_pct_l10']}%
- RATE-BASED PROJECTION: {a['projected_from_rate']} (= {a['avg_per_min']}/min × {a['avg_minutes']} avg min)
- STATISTICAL LEAN: {a['stat_lean']} ({a['lean_strength']})
- EDGE: {a['edge_signal']} (z={a['z_score']})
- PLAYER ROLE: {a['role']} ({a['avg_minutes']} min/game)

DECISION RULES (FOLLOW STRICTLY):
1. If over-rate >= 65%, your recommendation MUST be OVER unless there's a confirmed injury or rest day.
2. If under-rate >= 65%, your recommendation MUST be UNDER.
3. If |avg - line| < 0.5*stddev, this is a COIN FLIP. Set confidence to 48-52%. Do NOT give a confident call.
4. Your projected value MUST be within 15% of the rate-based projection ({a['projected_from_rate']}). Do not hallucinate.
5. If the line is 2+ standard deviations from the mean, this is a STRONG edge — confidence should be 70%+.
6. Factor blowout risk: if a team is expected to win big, starters on the winning team play fewer minutes.
"""

        # Inject calibration from settled basketball picks (feedback loop)
        calibration_context = ""
        try:
            from calibration import get_calibration_stats, generate_calibration_prompt
            cal_stats = await get_calibration_stats("basketball")
            if cal_stats:
                calibration_context = generate_calibration_prompt(
                    cal_stats, req.propType, "over",
                    req.line, match_odds,
                    league_id=12, venue=player_venue,
                    position=None, sport="basketball"
                )
        except Exception as e:
            print(f"[BBALL CALIBRATION] Error: {e}")

        PREDICTION_SYSTEM = f"""You are an elite NBA/WNBA player prop analyst. You are given pre-computed statistical analysis. Your job is to synthesize this data into a calibrated prediction.

CRITICAL: You MUST respect the pre-computed over/under rates and per-minute projections. These are computed from REAL game logs. Do NOT override them with generic basketball knowledge.
{stat_lean_guidance}

OUTPUT FORMAT — Return valid JSON only:
- "projectedValue": number (your projected stat total — MUST be close to the rate-based projection)
- "recommendation": "over" or "under" (MUST align with over-rate unless strong situational override)
- "confidenceScore": 0-100 (if edge is "COIN FLIP", max 52. if "SLIGHT", max 60. if "MODERATE", 55-70. if "STRONG", 65-80. if "VERY STRONG", 75-90)
- "confidenceLevel": "Very High"/"High"/"Medium"/"Low"
- "reasoning": 3-5 sentences citing specific numbers from the data (per-minute rate, over-rate, venue splits, minutes, opponent defense)
- "tacticalBreakdown": ~1500 char markdown with: **Verdict**, **Analysis** (cite real numbers), **Scenarios** (best/worst/likely), **Risk Factors**, **TL;DR**
- "scenarioAnalysis": 2-3 sentences (blowout scenario, close game scenario, how each affects this prop)
- "sharpSummary": 2 sentences on why your projection differs from the line
- "keyEvidence": 2-3 strongest data points as string
- "gameFlowDynamics": How pace/game flow impacts this stat (1-2 sentences)
- "sensitivityTests": 1 sentence
- "subRisk": 1 sentence
- "uncertaintyNote": 1 sentence
- "matchupOverview": {{"homeTeam":"","awayTeam":"","favorite":"","expectedGameType":"","keyMatchupFactor":""}}
- "bayesianMetrics": {{"priorMean":0,"momentumEffect":0,"covariateAdjustment":0,"reversalFlag":"stable"}}
- "probabilityCurve": []
- "recentSamples": []
- "player": {{"id":0,"name":"","team":"","position":""}}
- "opponent": ""
- "propType": ""
- "line": 0
- "confidenceInterval": [low, high]
- "tacticalAlerts": []

RULES: recentSamples=[]. No AI model names in output."""

        prompt = f"""{req.playerName} — plays for {req.teamName} ({player_venue.upper()}) | OPPONENT: {req.opponentName} | {prop_label} line {req.line}
Sport: NBA/WNBA Basketball
recentSamples=[]
{calibration_context}
{data_digest[:7000]}

Analyze the statistical verdict, per-minute projection, and over-rate FIRST. Then factor in matchup context. Return JSON only."""

        import litellm
        litellm.drop_params = True
        EMERGENT_PROXY = "https://integrations.emergentagent.com/llm"

        async def call_ai(model_name, label, provider="openai"):
            try:
                model_id = f"gemini/{model_name}" if provider == "gemini" else model_name
                resp = await aio.wait_for(
                    litellm.acompletion(
                        model=model_id,
                        messages=[
                            {"role": "system", "content": PREDICTION_SYSTEM},
                            {"role": "user", "content": prompt},
                        ],
                        api_key=EMERGENT_LLM_KEY,
                        api_base=EMERGENT_PROXY,
                        custom_llm_provider="openai",
                        max_tokens=2500,
                        temperature=0.0,
                    ),
                    timeout=40
                )
                text = resp.choices[0].message.content.strip()
                if text.startswith("```"):
                    text = "\n".join(ln for ln in text.split("\n") if not ln.strip().startswith("```"))
                start = text.find("{")
                if start >= 0:
                    for end_pos in range(len(text), start, -1):
                        if text[end_pos - 1] == "}":
                            try:
                                result = json.loads(text[start:end_pos])
                                result["_source"] = label
                                return result
                            except json.JSONDecodeError:
                                continue
                raise ValueError("No valid JSON in response")
            except Exception as e:
                print(f"[BBALL MULTI-AI] {label} failed: {e}")
                return None


        async def call_emergent_direct(model_name, label):
            """Call Claude/other models directly via OpenAI SDK to bypass litellm provider detection."""
            try:
                client = OpenAI(api_key=EMERGENT_LLM_KEY, base_url=EMERGENT_PROXY + "/v1")
                loop = aio.get_event_loop()
                def _run():
                    return client.chat.completions.create(
                        model=model_name,
                        messages=[
                            {"role": "system", "content": PREDICTION_SYSTEM},
                            {"role": "user", "content": prompt},
                        ],
                        max_tokens=2500,
                        temperature=0.0,
                    )
                resp = await aio.wait_for(loop.run_in_executor(None, _run), timeout=40)
                text = resp.choices[0].message.content.strip()
                if text.startswith("```"):
                    text = "\n".join(ln for ln in text.split("\n") if not ln.strip().startswith("```"))
                start = text.find("{")
                if start >= 0:
                    for end_pos in range(len(text), start, -1):
                        if text[end_pos - 1] == "}":
                            try:
                                result = json.loads(text[start:end_pos])
                                result["_source"] = label
                                return result
                            except json.JSONDecodeError:
                                continue
                raise ValueError("No valid JSON in response")
            except Exception as e:
                print(f"[BBALL MULTI-AI] {label} failed: {e}")
                return None

        async def call_grok(label="grok", model="grok-4-1-fast-reasoning"):
            try:
                grok_client = OpenAI(api_key=XAI_API_KEY, base_url="https://api.x.ai/v1")
                grok_messages = [
                    {"role": "system", "content": PREDICTION_SYSTEM},
                    {"role": "user", "content": prompt},
                ]
                loop = aio.get_event_loop()
                def _run():
                    return grok_client.chat.completions.create(
                        model=model,
                        messages=grok_messages,
                        max_tokens=2500,
                        temperature=0.0,
                    )
                grok_result = await aio.wait_for(loop.run_in_executor(None, _run), timeout=40)
                text = grok_result.choices[0].message.content.strip()
                if text.startswith("```"):
                    text = "\n".join(ln for ln in text.split("\n") if not ln.strip().startswith("```"))
                start = text.find("{")
                if start >= 0:
                    for end_pos in range(len(text), start, -1):
                        if text[end_pos - 1] == "}":
                            try:
                                result = json.loads(text[start:end_pos])
                                result["_source"] = label
                                return result
                            except json.JSONDecodeError:
                                continue
                raise ValueError("No valid JSON found in Grok response")
            except Exception as e:
                print(f"[BBALL MULTI-AI] {label} failed: {e}")
                return None

        ai_tasks = [
            aio.ensure_future(call_ai("gemini-2.5-pro", "gemini", "gemini")),
            aio.ensure_future(call_ai("gpt-5.2", "gpt52")),
            aio.ensure_future(call_grok("grok", "grok-4-1-fast-reasoning")),
        ]

        # FORCE-3-MODELS: Wait for ALL 3 AIs, retry failures once
        ai_results = []
        deadline = t0 + 48

        # First pass: wait for all 3 to complete
        done, pending = await aio.wait(ai_tasks, timeout=max(0.1, deadline - _t.time()))
        for t in done:
            try:
                r = t.result()
                if r and isinstance(r, dict) and r.get("projectedValue") is not None:
                    pv = r.get("projectedValue", 0)
                    if isinstance(pv, (int, float)) and pv >= 0:
                        ai_results.append(r)
            except Exception:
                pass
        for t in pending:
            t.cancel()

        # Retry any failed models (one retry each, only if time allows)
        responded_sources = {r.get("_source") for r in ai_results}
        if len(ai_results) < 3 and _t.time() < deadline - 10:
            retry_tasks = []
            if "gemini" not in responded_sources:
                retry_tasks.append(aio.ensure_future(call_ai("gemini-2.5-pro", "gemini", "gemini")))
                print("[BBALL MULTI-AI] Retrying gemini...")
            if "gpt52" not in responded_sources:
                retry_tasks.append(aio.ensure_future(call_ai("gpt-5.2", "gpt52")))
                print("[BBALL MULTI-AI] Retrying gpt52...")
            if "grok" not in responded_sources:
                retry_tasks.append(aio.ensure_future(call_grok("grok", "grok-4-1-fast-reasoning")))
                print("[BBALL MULTI-AI] Retrying grok...")

            if retry_tasks:
                done_retry, pending_retry = await aio.wait(retry_tasks, timeout=max(0.1, deadline - _t.time()))
                for t in done_retry:
                    try:
                        r = t.result()
                        if r and isinstance(r, dict) and r.get("projectedValue") is not None:
                            pv = r.get("projectedValue", 0)
                            if isinstance(pv, (int, float)) and pv >= 0:
                                ai_results.append(r)
                    except Exception:
                        pass
                for t in pending_retry:
                    t.cancel()

        print(f"[BBALL TIMING] AIs done: {_t.time()-t0:.1f}s, {len(ai_results)}/3 succeeded ({', '.join(r.get('_source','?') for r in ai_results)})")

        valid_preds = []
        for i, r in enumerate(ai_results):
            if isinstance(r, dict) and r.get("projectedValue") is not None:
                pv = r.get("projectedValue", 0)
                if isinstance(pv, (int, float)) and pv >= 0:
                    # ENFORCE: each model's recommendation MUST match its projected value vs line
                    r["recommendation"] = "over" if pv > req.line else "under"
                    valid_preds.append(r)
                    print(f"[BBALL MULTI-AI] {r.get('_source','AI'+str(i))}: proj={pv} rec={r.get('recommendation')} conf={r.get('confidenceScore')}")

        if not valid_preds:
            raise ValueError("All AI models failed to produce predictions")

        # MERGE: Weighted consensus
        prediction = valid_preds[0].copy()

        if len(valid_preds) > 1:
            proj_values = [p.get("projectedValue", 0) for p in valid_preds if p.get("projectedValue") is not None]
            avg_proj = round(sum(proj_values) / len(proj_values), 1)
            prediction["projectedValue"] = avg_proj
            prediction["recommendation"] = "over" if avg_proj > req.line else "under"

            conf_values = []
            for p in valid_preds:
                c = p.get("confidenceScore", 50)
                if isinstance(c, (int, float)):
                    conf_values.append(c * 100 if c <= 1 else c)
            prediction["confidenceScore"] = round(sum(conf_values) / len(conf_values)) if conf_values else 50

            grok_pred = next((p for p in valid_preds if p.get("_source") == "grok"), None)
            for field in ["tacticalBreakdown", "reasoning", "sharpSummary", "scenarioAnalysis", "keyEvidence"]:
                if grok_pred and len(str(grok_pred.get(field, ""))) > 50:
                    prediction[field] = grok_pred[field]
                else:
                    best = max(valid_preds, key=lambda p, f=field: len(str(p.get(f, ""))))
                    prediction[field] = best.get(field, "")

            recs = [p.get("recommendation", "over") for p in valid_preds]
            over_count = sum(1 for r in recs if r == "over")
            under_count = len(recs) - over_count
            if all(r == prediction["recommendation"] for r in recs):
                consensus = f"Unanimous {prediction['recommendation'].upper()} — {len(valid_preds)}/{len(valid_preds)} AI models agree."
            else:
                majority_rec = prediction["recommendation"]
                dissenters = [p for p in valid_preds if p.get("recommendation") != majority_rec]
                dissent_reasons = []
                for d in dissenters:
                    reason = d.get("sharpSummary") or d.get("reasoning") or ""
                    if reason:
                        dissent_reasons.append(reason[:200])
                dissent_text = " Dissent: " + " | ".join(dissent_reasons) if dissent_reasons else ""
                consensus = f"Split: {over_count}/{len(valid_preds)} OVER, {under_count}/{len(valid_preds)} UNDER. Consensus → {prediction['recommendation'].upper()}.{dissent_text}"
            prediction["consensusNote"] = consensus
        else:
            pv = prediction.get("projectedValue", req.line)
            prediction["recommendation"] = "over" if pv > req.line else "under"

        for p in valid_preds:
            p.pop("_source", None)
        prediction.pop("_source", None)

        cs = prediction.get("confidenceScore", 50)
        prediction["confidenceLevel"] = "Very High" if cs >= 75 else "High" if cs >= 65 else "Medium" if cs >= 50 else "Low"

        # ═══════════════════════════════════════
        # DATA-DRIVEN SANITY CHECK & OVERRIDE
        # ═══════════════════════════════════════
        if analytics:
            a = analytics
            ai_rec = prediction.get("recommendation", "over")
            ai_proj = prediction.get("projectedValue", req.line)

            # Override 1: If over-rate is 70%+ and AI says under, override to over
            if a["over_pct"] >= 70 and ai_rec == "under":
                prediction["recommendation"] = "over"
                prediction["projectedValue"] = max(ai_proj, a["projected_from_rate"])
                prediction["tacticalAlerts"] = prediction.get("tacticalAlerts", []) + [
                    f"DATA OVERRIDE: AI recommended UNDER but player goes OVER {req.line} in {a['over_pct']}% of games. Overridden to OVER."
                ]
                print(f"[BBALL OVERRIDE] Overrode UNDER→OVER (over-rate={a['over_pct']}%)")

            # Override 2: If under-rate is 70%+ and AI says over, override to under
            elif a["under_pct"] >= 70 and ai_rec == "over":
                prediction["recommendation"] = "under"
                prediction["projectedValue"] = min(ai_proj, a["projected_from_rate"])
                prediction["tacticalAlerts"] = prediction.get("tacticalAlerts", []) + [
                    f"DATA OVERRIDE: AI recommended OVER but player goes UNDER {req.line} in {a['under_pct']}% of games. Overridden to UNDER."
                ]
                print(f"[BBALL OVERRIDE] Overrode OVER→UNDER (under-rate={a['under_pct']}%)")

            # Override 3: Clamp projection to be reasonable (within 30% of rate projection)
            rate_proj = a["projected_from_rate"]
            if rate_proj > 0:
                lower_bound = rate_proj * 0.7
                upper_bound = rate_proj * 1.3
                clamped_proj = max(lower_bound, min(upper_bound, prediction["projectedValue"]))
                if clamped_proj != prediction["projectedValue"]:
                    print(f"[BBALL CLAMP] Clamped projection {prediction['projectedValue']} → {round(clamped_proj, 1)} (rate-based: {rate_proj})")
                    prediction["projectedValue"] = round(clamped_proj, 1)

            # Override 4: If edge is COIN FLIP, cap confidence
            if a["edge_strength"] == "none":
                prediction["confidenceScore"] = min(prediction.get("confidenceScore", 50), 52)
                prediction["confidenceLevel"] = "Low"

            # Re-determine recommendation after all overrides
            prediction["recommendation"] = "over" if prediction["projectedValue"] > req.line else "under"

        # Apply calibration guards (blowout detection, rebound floors, etc.)
        try:
            from calibration import apply_calibration_guards
            prediction = await apply_calibration_guards(
                prediction, req.propType, req.line, match_odds, player_venue
            )
        except Exception as e:
            print(f"[BBALL CALIBRATION] Guard error: {e}")

        # Force-set identity fields from REQUEST data — never trust AI output for these
        player_pos = player_info.get("position", "") if player_info else ""
        prediction["player"] = {"id": player_id or 0, "name": req.playerName, "team": req.teamName, "position": player_pos}
        prediction["opponent"] = req.opponentName
        prediction["propType"] = req.propType
        prediction["line"] = req.line
        prediction.setdefault("projectedValue", req.line)
        prediction.setdefault("recommendation", "over")
        prediction.setdefault("confidenceScore", 50)
        prediction.setdefault("confidenceLevel", "Medium")
        prediction.setdefault("confidenceInterval", [req.line * 0.8, req.line * 1.2])
        prediction.setdefault("recentSamples", [])
        prediction.setdefault("bayesianMetrics", {"priorMean": req.line, "momentumEffect": 0, "covariateAdjustment": 0, "reversalFlag": "stable"})
        prediction.setdefault("probabilityCurve", [])

        if real_recent_samples:
            prediction["recentSamples"] = real_recent_samples

        real_matchup = prediction.get("matchupOverview", {})
        real_matchup["homeTeam"] = req.teamName if player_venue == "home" else req.opponentName
        real_matchup["awayTeam"] = req.opponentName if player_venue == "home" else req.teamName
        prediction["matchupOverview"] = real_matchup

        if analytics:
            a = analytics
            prediction["playerGameLogs"] = {
                "targetProp": req.propType,
                "sampleSize": a["n"],
                "rawAvg": a["avg_all"],
                "last10Avg": a["avg_10"],
                "last5Avg": a["avg_5"],
                "rawMin": a["min_val"],
                "rawMax": a["max_val"],
                "stdDev": a["std_dev"],
                "homeAvg": a["home_avg"] or 0,
                "awayAvg": a["away_avg"] or 0,
                "overRate": a["over_pct"],
                "avgMinutes": a["avg_minutes"],
                "perMinRate": a["avg_per_min"],
                "rateProjection": a["projected_from_rate"],
                "role": a["role"],
                "edgeSignal": a["edge_signal"],
                "consistency": a["consistency_label"],
                "streak": a["streak_label"],
                "statisticalLean": a["stat_lean"],
            }

        # H2H PLAYER STATS for frontend display (player's performance vs this opponent)
        h2h_game_ids = set(g.get("id") for g in h2h_data if g.get("id")) if h2h_data else set()
        all_logs_for_h2h = player_game_logs + prior_season_logs
        h2h_player_games = [g for g in all_logs_for_h2h if g.get("gameId") in h2h_game_ids] if h2h_game_ids else []
        # Sort by date descending
        h2h_player_games.sort(key=lambda g: g.get("date", ""), reverse=True)

        if h2h_player_games:
            h2h_summary = []
            for g in h2h_player_games:
                val = g.get("targetStat")
                if val is None:
                    continue
                h2h_summary.append({
                    "date": g.get("date", ""),
                    "opponent": g.get("opponent", ""),
                    "value": val,
                    "minutesPlayed": g.get("minutes", "0"),
                    "venue": g.get("venue", ""),
                    "result": g.get("result", ""),
                    "hit": "over" if val > req.line else "under" if val < req.line else "push",
                })
            prediction["h2hGames"] = h2h_summary
        else:
            prediction["h2hGames"] = []


        # Add odds data to response
        if odds_data:
            prediction["moneyline"] = odds_data

        total_logs = len(player_game_logs)
        if total_logs >= 10:
            prediction["dataQuality"] = {"level": "good", "message": "", "gamesWithData": total_logs, "totalGames": total_logs}
        elif total_logs >= 3:
            prediction["dataQuality"] = {"level": "limited", "message": f"Only {total_logs} game logs available.", "gamesWithData": total_logs, "totalGames": total_logs}
        else:
            prediction["dataQuality"] = {"level": "low", "message": f"Only {total_logs} game logs. Limited sample size.", "gamesWithData": total_logs, "totalGames": total_logs}

        # ═══════════════════════════════════════
        # SYNTHESIS STEP
        # ═══════════════════════════════════════
        rec = prediction.get('recommendation', 'over').upper()
        proj = prediction.get('projectedValue', '?')
        conf = prediction.get('confidenceScore', '?')
        consensus_note = prediction.get('consensusNote', '')

        all_texts = []
        for p in valid_preds:
            src = p.get("_source", "AI")
            bits = []
            for field in ["tacticalBreakdown", "reasoning", "scenarioAnalysis", "keyEvidence", "sharpSummary", "gameFlowDynamics"]:
                val = p.get(field, "")
                if isinstance(val, dict):
                    val = json.dumps(val)
                if val and len(str(val)) > 10:
                    bits.append(f"{field}: {val}")
            if bits:
                all_texts.append(f"[{src}]\n" + "\n".join(bits))

        synthesis_input = "\n\n".join(all_texts)

        try:
            analytics_context = ""
            if analytics:
                a = analytics
                analytics_context = f"""
Key Stats: Season avg {a['avg_all']} | Per-min rate {a['avg_per_min']}/min | Avg minutes {a['avg_minutes']}
Over-rate vs {req.line}: {a['over_pct']}% | Rate projection: {a['projected_from_rate']} | Edge: {a['edge_signal']}
Role: {a['role']} | Consistency: {a['consistency_label']} | Streak: {a['streak_label']}
"""

            synth_prompt = f"""Synthesize multiple AI analyses into ONE elite tactical breakdown for an NBA {prop_label} prop prediction.

FINAL VERDICT: {rec} {req.line} {prop_label} (Projected: {proj}, Confidence: {conf}%, {consensus_note})
Player: {req.playerName} ({req.teamName}) vs {req.opponentName} ({player_venue.upper()})
{analytics_context}
AI analyses:
{synthesis_input[:4000]}

Write ~1500 char markdown. Format:
**Verdict: {rec} {req.line} {prop_label}**
[1-2 sentence sharp summary. Include per-minute rate projection and over-rate %]

**Analysis**
[3-4 sentences. MUST cite: season avg, per-minute rate, minutes avg, over-rate %, venue splits]

**Game Script Scenarios**
[Best case / Worst case / Most likely — with specific stat projections and minute estimates]

**Key Evidence**
[3-4 bullet points — cite over-rate, per-min rate, venue avg, opponent defense]

**Risk Radar**
[Blowout risk (minute reduction), consistency, momentum, matchup]

**TL;DR** — {rec} {req.line} at {conf}% confidence. Rate projection: {analytics['projected_from_rate'] if analytics else proj}. Over-rate: {analytics['over_pct'] if analytics else '?'}%. {consensus_note}

Rules: No AI model names. Specific numbers. Decisive."""

            synth_resp = await aio.wait_for(
                litellm.acompletion(
                    model="gemini/gemini-2.5-pro",
                    messages=[{"role": "user", "content": synth_prompt}],
                    api_key=EMERGENT_LLM_KEY,
                    api_base=EMERGENT_PROXY,
                    custom_llm_provider="openai",
                    max_tokens=1500,
                    temperature=0.2,
                ),
                timeout=10
            )
            synth_text = synth_resp.choices[0].message.content.strip()
            if synth_text and len(synth_text) > 200:
                prediction["tacticalBreakdown"] = synth_text
                print(f"[BBALL TIMING] Synthesis done: {_t.time()-t0:.1f}s, {len(synth_text)} chars")
        except Exception as synth_err:
            print(f"[BBALL SYNTHESIS] Fallback — {synth_err}")

        prediction["sport"] = "basketball"
        prediction["_created"] = datetime.now(timezone.utc).isoformat()
        prediction["_request"] = req.model_dump()
        await db.basketball_predictions.insert_one(prediction)
        prediction.pop("_id", None)

        print(f"[BBALL TIMING] TOTAL: {_t.time()-t0:.1f}s")
        return prediction

    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Basketball prediction failed: {str(e)}")


@router.post("/basketball/search-teams")
async def basketball_search_teams(req: dict):
    """Search for NBA/WNBA basketball teams via cache."""
    query = req.get("query", "")
    if not query:
        raise HTTPException(status_code=400, detail="Query required")
    # Try cache first
    teams = await search_bball_teams(query)
    if teams:
        return {"teams": [{"id": t.get("teamId"), "name": t.get("name"), "logo": t.get("logo", "")} for t in teams]}
    # Fallback to live API
    live_teams = await search_nba_teams(query)
    return {"teams": [{"id": t.get("id"), "name": t.get("name"), "logo": t.get("logo", "")} for t in live_teams]}
