"""
CalibrationEngine — Learns from settled picks to improve future predictions.
Tracks accuracy by: sport, league, prop type, recommendation, venue,
confidence band, line range, and score context.
"""
from config import db
from collections import defaultdict
from datetime import datetime, timezone

# Sport-specific caches
_cache = {}


def _hit_rate(h, m):
    t = h + m
    return round(h / t * 100, 1) if t else 0


def _bucket_key(d, key, res):
    if key not in d:
        d[key] = {"hit": 0, "miss": 0, "push": 0, "errors": [], "count": 0}
    d[key][res] = d[key].get(res, 0) + 1
    d[key]["count"] += 1


async def get_calibration_stats(sport: str = "soccer", force_refresh: bool = False) -> dict:
    """Compute accuracy stats from settled picks, filtered by sport. Cached 30 min."""
    now = datetime.now(timezone.utc)
    cache_entry = _cache.get(sport)
    if (
        not force_refresh
        and cache_entry
        and cache_entry.get("updated")
        and (now - cache_entry["updated"]).total_seconds() < 1800
    ):
        return cache_entry["stats"]

    query = {"status": "settled", "result": {"$in": ["hit", "miss", "push"]}}
    if sport:
        query["sport"] = sport

    picks = await db.picks.find(query, {"_id": 0}).to_list(5000)
    if not picks:
        return {}

    stats = {
        "total": len(picks),
        "by_prop": {},
        "by_prop_rec": {},
        "by_venue": {},
        "by_league": {},
        "by_confidence_band": {},
        "by_line_range": {},
        "by_prop_venue": {},
        "blowout_misses": [],
        "close_game_results": {"hit": 0, "miss": 0},
        "overall_hit_rate": 0,
        "over_hit_rate": 0,
        "under_hit_rate": 0,
    }

    total_h, total_m = 0, 0
    over_h, over_t = 0, 0
    under_h, under_t = 0, 0

    for p in picks:
        pt = p.get("propType", "unknown")
        rec = p.get("recommendation", "unknown")
        res = p.get("result", "unknown")
        venue = p.get("venue", "unknown")
        league = str(p.get("leagueId", "unknown"))
        conf = p.get("confidenceScore", 50)
        line = p.get("line", 0)
        proj = p.get("projectedValue", 0)
        actual = p.get("actualValue", 0)
        score = p.get("matchScore", "")

        if res not in ("hit", "miss", "push"):
            continue

        # Overall
        if res == "hit":
            total_h += 1
        elif res == "miss":
            total_m += 1

        # By prop type
        _bucket_key(stats["by_prop"], pt, res)
        if actual and proj:
            stats["by_prop"][pt]["errors"].append(round(actual - proj, 1))

        # By prop type + recommendation
        _bucket_key(stats["by_prop_rec"], f"{pt}|{rec}", res)

        # By venue
        _bucket_key(stats["by_venue"], venue, res)

        # By league
        _bucket_key(stats["by_league"], league, res)

        # By prop + venue (e.g., saves|home, pass_attempts|away)
        _bucket_key(stats["by_prop_venue"], f"{pt}|{venue}", res)

        # By confidence band
        if conf >= 70:
            band = "high_70+"
        elif conf >= 55:
            band = "medium_55-69"
        else:
            band = "low_<55"
        _bucket_key(stats["by_confidence_band"], band, res)

        # By line range
        if line <= 0.5:
            lr = "binary_0.5"
        elif line <= 5:
            lr = "low_1-5"
        elif line <= 30:
            lr = "medium_5-30"
        else:
            lr = "high_30+"
        _bucket_key(stats["by_line_range"], lr, res)

        # Over/Under tracking
        if rec == "over":
            over_t += 1
            if res == "hit":
                over_h += 1
        elif rec == "under":
            under_t += 1
            if res == "hit":
                under_h += 1

        # Blowout / close game detection from final score
        if score and res in ("hit", "miss"):
            try:
                parts = score.replace(" ", "").split("-")
                g1, g2 = int(parts[0]), int(parts[1])
                diff = abs(g1 - g2)
                total_goals = g1 + g2
                if diff >= 3 or total_goals >= 6:
                    if res == "miss":
                        stats["blowout_misses"].append({
                            "player": p.get("playerName"), "prop": pt,
                            "rec": rec, "proj": proj, "actual": actual,
                            "line": line, "score": score,
                        })
                elif diff <= 1:
                    stats["close_game_results"][res] += 1
            except Exception:
                pass

    stats["overall_hit_rate"] = _hit_rate(total_h, total_m)
    stats["over_hit_rate"] = _hit_rate(over_h, over_t - over_h) if over_t else 0
    stats["under_hit_rate"] = _hit_rate(under_h, under_t - under_h) if under_t else 0

    _cache[sport] = {"stats": stats, "updated": now}
    return stats


def generate_calibration_prompt(
    stats: dict, prop_type: str, recommendation: str,
    line: float, match_odds: dict = None, league_id: int = None, venue: str = None
) -> str:
    """Generate calibration context string for the AI prediction prompt."""
    if not stats or stats.get("total", 0) < 5:
        return ""

    lines = ["[HISTORICAL CALIBRATION — from settled picks]"]

    # Overall accuracy
    lines.append(f"Overall: {stats['overall_hit_rate']}% hit rate ({stats['total']} picks)")
    if stats.get("over_hit_rate") or stats.get("under_hit_rate"):
        lines.append(f"OVER: {stats['over_hit_rate']}% | UNDER: {stats['under_hit_rate']}%")

    # Prop-specific accuracy
    prop_data = stats["by_prop"].get(prop_type)
    if prop_data:
        h, m = prop_data.get("hit", 0), prop_data.get("miss", 0)
        total = h + m
        if total >= 3:
            rate = _hit_rate(h, m)
            errs = prop_data.get("errors", [])
            avg_err = round(sum(errs) / len(errs), 1) if errs else 0
            direction = "over-projecting" if avg_err < 0 else "under-projecting"
            lines.append(f"{prop_type}: {rate}% accuracy ({h}/{total}), avg error: {direction} by {abs(avg_err)}")

    # Prop + recommendation
    key = f"{prop_type}|{recommendation}"
    combo = stats["by_prop_rec"].get(key)
    if combo:
        h, m = combo.get("hit", 0), combo.get("miss", 0)
        total = h + m
        if total >= 2:
            rate = _hit_rate(h, m)
            if rate < 50:
                lines.append(f"WARNING: {prop_type} {recommendation.upper()} has only {rate}% hit rate ({h}/{total}) — be very cautious.")
            elif rate >= 75:
                lines.append(f"STRONG: {prop_type} {recommendation.upper()} has {rate}% hit rate ({h}/{total}).")

    # Venue-specific for this prop
    if venue:
        pv_key = f"{prop_type}|{venue}"
        pv_data = stats["by_prop_venue"].get(pv_key)
        if pv_data:
            h, m = pv_data.get("hit", 0), pv_data.get("miss", 0)
            total = h + m
            if total >= 3:
                rate = _hit_rate(h, m)
                lines.append(f"{prop_type} at {venue.upper()}: {rate}% ({h}/{total})")

    # League accuracy
    if league_id:
        lg_data = stats["by_league"].get(str(league_id))
        if lg_data:
            h, m = lg_data.get("hit", 0), lg_data.get("miss", 0)
            total = h + m
            if total >= 3:
                rate = _hit_rate(h, m)
                if rate < 55:
                    lines.append(f"LEAGUE WARNING: League {league_id} has only {rate}% hit rate ({h}/{total}) — data may be sparse or volatile.")
                else:
                    lines.append(f"League {league_id}: {rate}% hit rate ({h}/{total})")

    # Confidence band accuracy
    for band_key, band_data in stats.get("by_confidence_band", {}).items():
        h, m = band_data.get("hit", 0), band_data.get("miss", 0)
        total = h + m
        if total >= 5:
            rate = _hit_rate(h, m)
            lines.append(f"Confidence {band_key}: {rate}% hit rate ({h}/{total})")

    # Line range
    if line <= 0.5:
        lr_data = stats["by_line_range"].get("binary_0.5")
        if lr_data:
            h, m = lr_data.get("hit", 0), lr_data.get("miss", 0)
            total = h + m
            if total >= 2:
                lines.append(f"Binary lines (0.5): {_hit_rate(h, m)}% ({h}/{total}) — UNDER = zero required, high risk.")

    # Blowout misses
    if stats.get("blowout_misses") and prop_type == "saves":
        saves_blowouts = [m for m in stats["blowout_misses"] if m["prop"] == "saves"]
        if saves_blowouts:
            lines.append(f"BLOWOUT: {len(saves_blowouts)} saves misses in blowout games — winning GK faces fewer shots, losing GK concedes goals not saves.")

    # Close game accuracy
    cg = stats.get("close_game_results", {})
    cg_h, cg_m = cg.get("hit", 0), cg.get("miss", 0)
    cg_total = cg_h + cg_m
    if cg_total >= 5:
        lines.append(f"Close games (1-goal margin): {_hit_rate(cg_h, cg_m)}% ({cg_h}/{cg_total})")

    return "\n".join(lines) if len(lines) > 1 else ""


def generate_score_context_rules(prop_type: str, recommendation: str, match_odds: dict = None, team_venue: str = "home") -> list:
    """Generate hard rules based on known bias patterns."""
    rules = []

    # Rule 1: UNDER skew
    if recommendation == "under":
        rules.append({
            "type": "UNDER_SKEW",
            "description": "Stats have positive skew (blowup > downside). UNDER inherently riskier.",
            "confidence_penalty": 3,
        })

    if not match_odds:
        return rules

    fav = match_odds.get("favorite", "")
    odds_data = match_odds.get("bookmakerOdds", {})
    home_odds = odds_data.get("home", 0)
    away_odds = odds_data.get("away", 0)

    # Rule 2: GK saves — favored team's GK faces fewer shots
    if prop_type == "saves":
        try:
            if fav == team_venue:
                gk_odds = float(home_odds if team_venue == "home" else away_odds)
                if gk_odds and gk_odds < 1.5:
                    rules.append({
                        "type": "FAVORED_GK",
                        "description": f"GK's team is heavy favorite (odds {gk_odds}). Fewer opponent shots → OVER saves risky.",
                        "confidence_penalty": 5 if recommendation == "over" else 0,
                    })
            elif fav:
                opp_odds = float(away_odds if team_venue == "home" else home_odds)
                if opp_odds and opp_odds < 1.4:
                    rules.append({
                        "type": "BLOWOUT_RISK",
                        "description": f"Opponent is heavy favorite (odds {opp_odds}). Blowout risk — goals not saves.",
                        "confidence_penalty": 4 if recommendation == "over" else 0,
                    })
        except (ValueError, TypeError):
            pass

    # Rule 3: Pass props — possession differential
    if prop_type in ("pass_attempts", "passes_total", "key_passes"):
        if fav and fav != team_venue and recommendation == "over":
            rules.append({
                "type": "POSSESSION_RISK",
                "description": "Player's team likely has less possession vs favored opponent → fewer pass opportunities.",
                "confidence_penalty": 3,
            })

    return rules


async def apply_calibration_guards(prediction: dict, prop_type: str, line: float, match_odds: dict = None, team_venue: str = "home") -> dict:
    """Apply calibration adjustments to prediction confidence. Modifies in place."""
    rec = prediction.get("recommendation", "over")
    conf = prediction.get("confidenceScore", 50)

    rules = generate_score_context_rules(prop_type, rec, match_odds, team_venue)

    total_penalty = 0
    alerts = prediction.get("tacticalAlerts", [])

    for rule in rules:
        penalty = rule.get("confidence_penalty", 0)
        if penalty > 0:
            total_penalty += penalty
            alerts.append(f"[{rule['type']}] {rule['description']} (-{penalty}%)")
            print(f"[CALIBRATION] {rule['type']}: -{penalty}% confidence")

    if total_penalty > 0:
        new_conf = max(45, conf - total_penalty)
        prediction["confidenceScore"] = new_conf
        prediction["tacticalAlerts"] = alerts
        prediction["confidenceLevel"] = (
            "Very High" if new_conf >= 75 else
            "High" if new_conf >= 65 else
            "Medium" if new_conf >= 50 else
            "Low"
        )
        print(f"[CALIBRATION] Total: -{total_penalty}% ({conf} → {new_conf})")

    return prediction
