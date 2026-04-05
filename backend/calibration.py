"""
CalibrationEngine — Learns from settled picks to improve future predictions.
Computes hit rates by prop type, recommendation, venue, and context.
Generates calibration notes injected into the AI prompt.
"""
from config import db
from collections import defaultdict
from datetime import datetime, timezone, timedelta
import math

# In-memory cache to avoid querying DB on every prediction
_cache = {"stats": None, "updated": None, "ttl_minutes": 30}


async def get_calibration_stats(sport: str = "soccer", force_refresh: bool = False) -> dict:
    """Compute accuracy stats from settled picks. Cached for 30 min."""
    now = datetime.now(timezone.utc)
    if (
        not force_refresh
        and _cache["stats"]
        and _cache["updated"]
        and (now - _cache["updated"]).total_seconds() < _cache["ttl_minutes"] * 60
    ):
        return _cache["stats"]

    picks = await db.picks.find(
        {"status": "settled", "result": {"$in": ["hit", "miss", "push"]}},
        {"_id": 0}
    ).to_list(2000)

    if not picks:
        return {}

    stats = {
        "total": len(picks),
        "by_prop": {},
        "by_prop_rec": {},
        "by_venue": {},
        "blowout_misses": [],
        "close_misses": [],
        "overall_hit_rate": 0,
        "under_hit_rate": 0,
        "over_hit_rate": 0,
    }

    total_hits = 0
    total_misses = 0
    over_hits = 0
    over_total = 0
    under_hits = 0
    under_total = 0

    for p in picks:
        pt = p.get("propType", "unknown")
        rec = p.get("recommendation", "unknown")
        res = p.get("result", "unknown")
        venue = p.get("venue", "unknown")
        proj = p.get("projectedValue", 0)
        actual = p.get("actualValue", 0)
        line = p.get("line", 0)
        score = p.get("matchScore", "")

        if res == "hit":
            total_hits += 1
        elif res == "miss":
            total_misses += 1

        # By prop type
        if pt not in stats["by_prop"]:
            stats["by_prop"][pt] = {"hit": 0, "miss": 0, "push": 0, "errors": []}
        stats["by_prop"][pt][res] = stats["by_prop"][pt].get(res, 0) + 1
        if actual and proj:
            stats["by_prop"][pt]["errors"].append(round(actual - proj, 1))

        # By prop type + recommendation
        key = f"{pt}|{rec}"
        if key not in stats["by_prop_rec"]:
            stats["by_prop_rec"][key] = {"hit": 0, "miss": 0, "push": 0}
        stats["by_prop_rec"][key][res] = stats["by_prop_rec"][key].get(res, 0) + 1

        # Over/Under tracking
        if rec == "over":
            over_total += 1
            if res == "hit":
                over_hits += 1
        elif rec == "under":
            under_total += 1
            if res == "hit":
                under_hits += 1

        # By venue
        if venue not in stats["by_venue"]:
            stats["by_venue"][venue] = {"hit": 0, "miss": 0}
        if res in ("hit", "miss"):
            stats["by_venue"][venue][res] += 1

        # Blowout detection — score differential >= 3
        if score and res == "miss":
            try:
                parts = score.split("-")
                g1, g2 = int(parts[0].strip()), int(parts[1].strip())
                diff = abs(g1 - g2)
                total_goals = g1 + g2
                miss_info = {
                    "player": p.get("playerName"),
                    "prop": pt, "rec": rec,
                    "proj": proj, "actual": actual, "line": line,
                    "score": score, "diff": diff,
                }
                if diff >= 3 or total_goals >= 6:
                    stats["blowout_misses"].append(miss_info)
                elif diff <= 1:
                    stats["close_misses"].append(miss_info)
            except Exception:
                pass

    # Compute rates
    total = total_hits + total_misses
    stats["overall_hit_rate"] = round(total_hits / total * 100, 1) if total else 0
    stats["over_hit_rate"] = round(over_hits / over_total * 100, 1) if over_total else 0
    stats["under_hit_rate"] = round(under_hits / under_total * 100, 1) if under_total else 0

    _cache["stats"] = stats
    _cache["updated"] = now
    return stats


def generate_calibration_prompt(stats: dict, prop_type: str, recommendation: str, line: float, match_odds: dict = None) -> str:
    """Generate a calibration context string to inject into the AI prediction prompt."""
    if not stats or stats.get("total", 0) < 5:
        return ""

    lines = ["[HISTORICAL CALIBRATION — from settled picks]"]

    # Overall accuracy
    lines.append(f"Overall hit rate: {stats['overall_hit_rate']}% ({stats['total']} picks)")
    if stats["over_hit_rate"] and stats["under_hit_rate"]:
        lines.append(f"OVER hit rate: {stats['over_hit_rate']}% | UNDER hit rate: {stats['under_hit_rate']}%")

    # Prop-specific accuracy
    prop_data = stats["by_prop"].get(prop_type)
    if prop_data:
        h = prop_data.get("hit", 0)
        m = prop_data.get("miss", 0)
        total = h + m
        if total >= 3:
            rate = round(h / total * 100, 1)
            avg_err = round(sum(prop_data["errors"]) / len(prop_data["errors"]), 1) if prop_data["errors"] else 0
            direction = "over-projecting" if avg_err < 0 else "under-projecting"
            lines.append(f"This prop ({prop_type}): {rate}% accuracy ({h}/{total}), avg error: {direction} by {abs(avg_err)}")

    # Prop + recommendation accuracy
    key = f"{prop_type}|{recommendation}"
    combo = stats["by_prop_rec"].get(key)
    if combo:
        h = combo.get("hit", 0)
        m = combo.get("miss", 0)
        total = h + m
        if total >= 2:
            rate = round(h / total * 100, 1)
            if rate < 50:
                lines.append(f"WARNING: {prop_type} {recommendation.upper()} has only {rate}% hit rate ({h}/{total}) — be cautious with this recommendation.")
            elif rate >= 75:
                lines.append(f"STRONG: {prop_type} {recommendation.upper()} has {rate}% hit rate ({h}/{total}) — confidence supported by history.")

    # Blowout context warning
    if stats.get("blowout_misses"):
        saves_blowout = [m for m in stats["blowout_misses"] if m["prop"] == "saves"]
        if saves_blowout and prop_type == "saves":
            lines.append(f"BLOWOUT ALERT: {len(saves_blowout)} GK saves misses in blowout games — winning GK faces fewer shots, losing GK concedes goals not saves.")

    # Binary line warning
    if line <= 0.5:
        lines.append("BINARY LINE: 0.5 means ZERO required for UNDER. One event loses. Historical data shows binary UNDERs are high-risk.")

    # Tight line warning
    if prop_type == "saves" and match_odds:
        fav = match_odds.get("favorite", "")
        if fav and recommendation == "over":
            lines.append("ODDS CONTEXT: If GK's team is heavily favored, they face fewer shots → OVER saves is risky.")

    return "\n".join(lines) if len(lines) > 1 else ""


def generate_score_context_rules(prop_type: str, recommendation: str, match_odds: dict = None, team_venue: str = "home") -> list:
    """Generate hard rules based on known bias patterns. Returns list of adjustment rules."""
    rules = []

    # Rule 1: UNDER bias — stats have positive skew
    if recommendation == "under":
        rules.append({
            "type": "UNDER_SKEW",
            "description": "Stats have positive skew (blowup potential > downside). UNDER inherently riskier.",
            "confidence_penalty": 3,
        })

    # Rule 2: GK saves in blowout-expected games
    if prop_type == "saves" and match_odds:
        fav = match_odds.get("favorite", "")
        odds_data = match_odds.get("bookmakerOdds", {})
        home_odds = odds_data.get("home", 0)
        away_odds = odds_data.get("away", 0)

        # Detect heavy favorite (odds < 1.5 = -200 or stronger)
        try:
            if fav == team_venue:
                # GK's team is favored — fewer shots faced
                gk_fav_odds = home_odds if team_venue == "home" else away_odds
                if gk_fav_odds and float(gk_fav_odds) < 1.5:
                    rules.append({
                        "type": "FAVORED_GK",
                        "description": f"GK's team is heavy favorite (odds {gk_fav_odds}). Expect fewer opponent shots → OVER saves risky.",
                        "confidence_penalty": 5 if recommendation == "over" else 0,
                    })
            else:
                # GK's team is underdog — more shots faced, but blowout risk
                opp_odds = away_odds if team_venue == "home" else home_odds
                if opp_odds and float(opp_odds) < 1.4:
                    rules.append({
                        "type": "BLOWOUT_RISK",
                        "description": f"Opponent is heavy favorite (odds {opp_odds}). Blowout risk — goals not saves.",
                        "confidence_penalty": 4 if recommendation == "over" else 0,
                    })
        except (ValueError, TypeError):
            pass

    # Rule 3: Possession-dominant matchup affecting pass props
    if prop_type in ("pass_attempts", "passes_total") and match_odds:
        fav = match_odds.get("favorite", "")
        if fav and fav != team_venue and recommendation == "over":
            rules.append({
                "type": "POSSESSION_RISK",
                "description": "Player's team may have less possession vs favored opponent → fewer pass opportunities.",
                "confidence_penalty": 3,
            })

    return rules


async def apply_calibration_guards(prediction: dict, prop_type: str, line: float, match_odds: dict = None, team_venue: str = "home") -> dict:
    """Apply calibration-based adjustments to a prediction's confidence. Modifies in place."""
    rec = prediction.get("recommendation", "over")
    conf = prediction.get("confidenceScore", 50)

    # Get score context rules
    rules = generate_score_context_rules(prop_type, rec, match_odds, team_venue)

    total_penalty = 0
    alerts = prediction.get("tacticalAlerts", [])

    for rule in rules:
        penalty = rule.get("confidence_penalty", 0)
        if penalty > 0:
            total_penalty += penalty
            alerts.append(f"[{rule['type']}] {rule['description']} (-{penalty}% conf)")
            print(f"[CALIBRATION] {rule['type']}: -{penalty}% confidence")

    if total_penalty > 0:
        new_conf = max(45, conf - total_penalty)
        prediction["confidenceScore"] = new_conf
        prediction["tacticalAlerts"] = alerts
        # Recalculate confidence level
        prediction["confidenceLevel"] = (
            "Very High" if new_conf >= 75 else
            "High" if new_conf >= 65 else
            "Medium" if new_conf >= 50 else
            "Low"
        )
        print(f"[CALIBRATION] Total penalty: -{total_penalty}% ({conf} → {new_conf})")

    return prediction
