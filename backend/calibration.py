"""
CalibrationEngine v2 — Granular feedback loop for AI predictions.
Tracks accuracy by: sport, league, prop type, recommendation, venue,
position, game context (blowout/close/normal), favorite/underdog,
confidence band, and line range. Generates actionable "reasons why"
for the AI prompt — not just numbers, but pattern explanations.
"""
from config import db
from datetime import datetime, timezone

_cache = {}

# Position inference from prop type
SOCCER_GK_PROPS = {"saves"}
SOCCER_DEF_PROPS = {"tackles", "interceptions", "clearances", "blocks", "duels_won"}
SOCCER_ATK_PROPS = {"goals", "shots", "shots_on_target", "dribbles", "dribbles_success"}
SOCCER_MID_PROPS = {"pass_attempts", "key_passes", "crosses", "shots_assisted", "assists"}

BBALL_BIG_PROPS = {"rebounds", "blocks"}
BBALL_GUARD_PROPS = {"three_pointers", "assists", "steals"}

LEAGUE_NAMES = {
    "39": "Premier League", "140": "La Liga", "135": "Serie A",
    "78": "Bundesliga", "61": "Ligue 1", "253": "MLS",
    "262": "Liga MX", "254": "Serie A (Brazil)", "307": "Saudi Pro",
    "2": "Champions League", "3": "Europa League", "848": "NWSL",
    "12": "NBA", "13": "WNBA",
}


def _hit_rate(h, m):
    t = h + m
    return round(h / t * 100, 1) if t else 0


def _bucket(d, key, res, error=None):
    if key not in d:
        d[key] = {"hit": 0, "miss": 0, "push": 0, "errors": [], "count": 0}
    d[key][res] = d[key].get(res, 0) + 1
    d[key]["count"] += 1
    if error is not None:
        d[key]["errors"].append(error)


def _infer_position(prop_type, sport):
    """Infer position group from prop type."""
    pt = (prop_type or "").lower()
    if sport == "basketball":
        if pt in BBALL_BIG_PROPS:
            return "big"
        if pt in BBALL_GUARD_PROPS:
            return "guard"
        return "any"
    # Soccer
    if pt in SOCCER_GK_PROPS:
        return "goalkeeper"
    if pt in SOCCER_DEF_PROPS:
        return "defender"
    if pt in SOCCER_ATK_PROPS:
        return "attacker"
    if pt in SOCCER_MID_PROPS:
        return "midfielder"
    return "any"


def _game_context(score_str, sport):
    """Classify game context from final score string."""
    if not score_str:
        return "unknown"
    try:
        parts = score_str.replace(" ", "").split("-")
        s1, s2 = int(parts[0]), int(parts[1])
        diff = abs(s1 - s2)
        total = s1 + s2
        if sport == "basketball":
            if diff >= 20:
                return "blowout"
            if diff <= 5:
                return "close"
            return "normal"
        # Soccer
        if diff >= 3 or total >= 6:
            return "blowout"
        if diff <= 1:
            return "close"
        return "normal"
    except Exception:
        return "unknown"


async def get_calibration_stats(sport: str = "soccer", force_refresh: bool = False) -> dict:
    """Compute granular accuracy stats from settled picks. Cached 30 min."""
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
        "by_position": {},
        "by_prop_venue": {},
        "by_prop_position": {},
        "by_game_context": {},
        "by_prop_context": {},
        "by_confidence_band": {},
        "by_line_range": {},
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
        position = _infer_position(pt, sport)
        context = _game_context(score, sport)

        if res not in ("hit", "miss", "push"):
            continue

        error = round(actual - proj, 1) if actual and proj else None

        if res == "hit":
            total_h += 1
        elif res == "miss":
            total_m += 1

        # Core buckets
        _bucket(stats["by_prop"], pt, res, error)
        _bucket(stats["by_prop_rec"], f"{pt}|{rec}", res, error)
        _bucket(stats["by_venue"], venue, res)
        _bucket(stats["by_league"], league, res)
        _bucket(stats["by_position"], position, res)
        _bucket(stats["by_prop_venue"], f"{pt}|{venue}", res, error)
        _bucket(stats["by_prop_position"], f"{pt}|{position}", res, error)
        _bucket(stats["by_game_context"], context, res)
        _bucket(stats["by_prop_context"], f"{pt}|{context}", res, error)

        # Confidence band
        band = "high_70+" if conf >= 70 else "medium_55-69" if conf >= 55 else "low_<55"
        _bucket(stats["by_confidence_band"], band, res)

        # Line range
        if line <= 0.5:
            lr = "binary_0.5"
        elif line <= 5:
            lr = "low_1-5"
        elif line <= 30:
            lr = "medium_5-30"
        else:
            lr = "high_30+"
        _bucket(stats["by_line_range"], lr, res)

        # Over/Under
        if rec == "over":
            over_t += 1
            if res == "hit":
                over_h += 1
        elif rec == "under":
            under_t += 1
            if res == "hit":
                under_h += 1

        # Blowout miss tracking
        if context == "blowout" and res == "miss":
            stats["blowout_misses"].append({
                "player": p.get("playerName"), "prop": pt,
                "rec": rec, "proj": proj, "actual": actual,
                "line": line, "score": score, "position": position,
            })
        if context == "close" and res in ("hit", "miss"):
            stats["close_game_results"][res] += 1

    stats["overall_hit_rate"] = _hit_rate(total_h, total_m)
    stats["over_hit_rate"] = _hit_rate(over_h, over_t - over_h) if over_t else 0
    stats["under_hit_rate"] = _hit_rate(under_h, under_t - under_h) if under_t else 0

    _cache[sport] = {"stats": stats, "updated": now}
    return stats


def _explain_error(errors, prop_type):
    """Generate a human-readable explanation of projection errors."""
    if not errors:
        return ""
    avg = round(sum(errors) / len(errors), 1)
    if abs(avg) < 0.3:
        return "projections well-calibrated"
    direction = "over-projecting" if avg < 0 else "under-projecting"
    return f"{direction} by {abs(avg)} — adjust projections {'down' if avg < 0 else 'up'}"


def _explain_context_pattern(context_data, context_name):
    """Explain what happens in specific game contexts."""
    h, m = context_data.get("hit", 0), context_data.get("miss", 0)
    total = h + m
    if total < 2:
        return ""
    rate = _hit_rate(h, m)
    if rate >= 70:
        return f"Strong in {context_name} ({rate}%, {h}/{total})"
    if rate <= 40:
        return f"Weak in {context_name} — only {rate}% ({h}/{total})"
    return ""


def generate_calibration_prompt(
    stats: dict, prop_type: str, recommendation: str,
    line: float, match_odds: dict = None, league_id: int = None,
    venue: str = None, position: str = None, sport: str = "soccer"
) -> str:
    """Generate granular calibration context with actionable explanations."""
    if not stats or stats.get("total", 0) < 3:
        return ""

    lines = ["[HISTORICAL CALIBRATION — from settled picks]"]

    # Overall
    lines.append(f"System accuracy: {stats['overall_hit_rate']}% ({stats['total']} settled picks)")
    if stats.get("over_hit_rate") or stats.get("under_hit_rate"):
        over_r, under_r = stats["over_hit_rate"], stats["under_hit_rate"]
        if over_r > under_r + 10:
            lines.append(f"OVER: {over_r}% vs UNDER: {under_r}% — system historically better at OVER picks")
        elif under_r > over_r + 10:
            lines.append(f"OVER: {over_r}% vs UNDER: {under_r}% — system historically better at UNDER picks")
        else:
            lines.append(f"OVER: {over_r}% | UNDER: {under_r}%")

    # Prop-specific accuracy + error direction
    prop_data = stats["by_prop"].get(prop_type)
    if prop_data:
        h, m = prop_data.get("hit", 0), prop_data.get("miss", 0)
        total = h + m
        if total >= 2:
            rate = _hit_rate(h, m)
            err_note = _explain_error(prop_data.get("errors", []), prop_type)
            if rate < 50:
                lines.append(f"CAUTION: {prop_type} only {rate}% accurate ({h}/{total}). {err_note}.")
            elif rate >= 70:
                lines.append(f"RELIABLE: {prop_type} at {rate}% ({h}/{total}). {err_note}.")
            else:
                lines.append(f"{prop_type}: {rate}% ({h}/{total}). {err_note}.")

    # Prop + recommendation (e.g., "saves OVER" pattern)
    key = f"{prop_type}|{recommendation}"
    combo = stats["by_prop_rec"].get(key)
    if combo:
        h, m = combo.get("hit", 0), combo.get("miss", 0)
        total = h + m
        if total >= 2:
            rate = _hit_rate(h, m)
            err_note = _explain_error(combo.get("errors", []), prop_type)
            if rate < 45:
                lines.append(f"RED FLAG: {prop_type} {recommendation.upper()} only {rate}% ({h}/{total}) — {err_note}. Consider flipping direction.")
            elif rate >= 75:
                lines.append(f"HIGH-HIT: {prop_type} {recommendation.upper()} at {rate}% ({h}/{total}). {err_note}.")

    # Position-specific accuracy
    inferred_pos = position or _infer_position(prop_type, sport)
    if inferred_pos != "any":
        pos_data = stats.get("by_position", {}).get(inferred_pos)
        if pos_data:
            h, m = pos_data.get("hit", 0), pos_data.get("miss", 0)
            total = h + m
            if total >= 3:
                rate = _hit_rate(h, m)
                pos_label = inferred_pos.upper()
                if rate < 50:
                    lines.append(f"POSITION WARNING: {pos_label} picks only {rate}% ({h}/{total}) — this position type is harder to predict.")
                elif rate >= 70:
                    lines.append(f"POSITION STRENGTH: {pos_label} picks at {rate}% ({h}/{total}).")

        # Prop + position combo (e.g., "saves for goalkeepers")
        pp_key = f"{prop_type}|{inferred_pos}"
        pp_data = stats.get("by_prop_position", {}).get(pp_key)
        if pp_data:
            h, m = pp_data.get("hit", 0), pp_data.get("miss", 0)
            total = h + m
            if total >= 2:
                rate = _hit_rate(h, m)
                err_note = _explain_error(pp_data.get("errors", []), prop_type)
                if rate != _hit_rate(prop_data.get("hit", 0), prop_data.get("miss", 0)) if prop_data else True:
                    lines.append(f"{prop_type} ({inferred_pos}): {rate}% ({h}/{total}). {err_note}.")

    # Venue-specific for this prop
    if venue:
        pv_key = f"{prop_type}|{venue}"
        pv_data = stats.get("by_prop_venue", {}).get(pv_key)
        if pv_data:
            h, m = pv_data.get("hit", 0), pv_data.get("miss", 0)
            total = h + m
            if total >= 2:
                rate = _hit_rate(h, m)
                err_note = _explain_error(pv_data.get("errors", []), prop_type)
                venue_label = "HOME" if venue == "home" else "AWAY"
                if rate < 50:
                    lines.append(f"VENUE BIAS: {prop_type} at {venue_label} only {rate}% ({h}/{total}) — {err_note}.")
                elif rate >= 70:
                    lines.append(f"VENUE EDGE: {prop_type} at {venue_label} {rate}% ({h}/{total}). {err_note}.")

    # League accuracy with name
    if league_id:
        lg_key = str(league_id)
        lg_data = stats.get("by_league", {}).get(lg_key)
        if lg_data:
            h, m = lg_data.get("hit", 0), lg_data.get("miss", 0)
            total = h + m
            if total >= 2:
                rate = _hit_rate(h, m)
                lg_name = LEAGUE_NAMES.get(lg_key, f"League {league_id}")
                if rate < 50:
                    lines.append(f"LEAGUE RISK: {lg_name} only {rate}% ({h}/{total}) — league data may be sparse or game flow unpredictable.")
                elif rate >= 70:
                    lines.append(f"LEAGUE RELIABLE: {lg_name} at {rate}% ({h}/{total}).")

    # Game context patterns (blowout/close/normal)
    for ctx in ["blowout", "close", "normal"]:
        ctx_data = stats.get("by_game_context", {}).get(ctx)
        if ctx_data:
            note = _explain_context_pattern(ctx_data, f"{ctx} games")
            if note:
                lines.append(note)

        # Prop + context (e.g., "saves in blowouts")
        pc_key = f"{prop_type}|{ctx}"
        pc_data = stats.get("by_prop_context", {}).get(pc_key)
        if pc_data:
            h, m = pc_data.get("hit", 0), pc_data.get("miss", 0)
            total = h + m
            if total >= 2:
                rate = _hit_rate(h, m)
                err_note = _explain_error(pc_data.get("errors", []), prop_type)
                if rate < 45:
                    lines.append(f"CONTEXT ALERT: {prop_type} in {ctx} games only {rate}% ({h}/{total}). WHY: {err_note}.")
                elif rate >= 75:
                    lines.append(f"CONTEXT EDGE: {prop_type} thrives in {ctx} games — {rate}% ({h}/{total}).")

    # Blowout miss details for GK saves
    if stats.get("blowout_misses") and prop_type == "saves":
        saves_blowouts = [m for m in stats["blowout_misses"] if m["prop"] == "saves"]
        if saves_blowouts:
            lines.append(
                f"BLOWOUT PATTERN: {len(saves_blowouts)} saves misses in blowout games. "
                "WHY: Winning GK faces fewer shots (opponent chasing = speculative shots, not on-target). "
                "Losing GK concedes goals not saves (shots go in, not saved)."
            )

    # Confidence band calibration
    for band_key, band_data in stats.get("by_confidence_band", {}).items():
        h, m = band_data.get("hit", 0), band_data.get("miss", 0)
        total = h + m
        if total >= 3:
            rate = _hit_rate(h, m)
            if band_key == "high_70+" and rate < 65:
                lines.append(f"OVERCONFIDENCE: High-confidence picks ({band_key}) only hit {rate}% ({h}/{total}) — system is overconfident.")
            elif band_key == "low_<55" and rate >= 55:
                lines.append(f"HIDDEN VALUE: Low-confidence picks hit {rate}% ({h}/{total}) — system may be undervaluing these.")

    # Binary line warning
    if line <= 0.5:
        lr_data = stats.get("by_line_range", {}).get("binary_0.5")
        if lr_data:
            h, m = lr_data.get("hit", 0), lr_data.get("miss", 0)
            total = h + m
            if total >= 2:
                lines.append(f"BINARY LINE: 0.5 lines hit {_hit_rate(h, m)}% ({h}/{total}). UNDER = player must record ZERO — extremely high risk.")

    return "\n".join(lines) if len(lines) > 1 else ""


def generate_score_context_rules(prop_type: str, recommendation: str, match_odds: dict = None, team_venue: str = "home") -> list:
    """Generate hard rules based on known bias patterns."""
    rules = []

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

    if prop_type == "saves":
        try:
            if fav == team_venue:
                gk_odds = float(home_odds if team_venue == "home" else away_odds)
                if gk_odds and gk_odds < 1.5:
                    rules.append({
                        "type": "FAVORED_GK",
                        "description": f"GK's team is heavy favorite (odds {gk_odds}). Fewer opponent shots means fewer save opportunities — OVER saves risky.",
                        "confidence_penalty": 5 if recommendation == "over" else 0,
                    })
            elif fav:
                opp_odds = float(away_odds if team_venue == "home" else home_odds)
                if opp_odds and opp_odds < 1.4:
                    rules.append({
                        "type": "BLOWOUT_RISK",
                        "description": f"Opponent is heavy favorite (odds {opp_odds}). Blowout risk — goals conceded, not saved. Shots go in.",
                        "confidence_penalty": 4 if recommendation == "over" else 0,
                    })
        except (ValueError, TypeError):
            pass

    if prop_type in ("pass_attempts", "passes_total", "key_passes"):
        if fav and fav != team_venue and recommendation == "over":
            rules.append({
                "type": "POSSESSION_RISK",
                "description": "Player's team likely has less possession vs favored opponent — fewer passing opportunities.",
                "confidence_penalty": 3,
            })

    # Basketball-specific: rebounds in blowouts
    if prop_type == "rebounds" and recommendation == "under":
        rules.append({
            "type": "REBOUND_FLOOR",
            "description": "Rebounds have a high floor — even in bad games, bigs collect 4-5 boards. UNDER is risky on low lines.",
            "confidence_penalty": 2,
        })

    return rules


async def apply_calibration_guards(prediction: dict, prop_type: str, line: float, match_odds: dict = None, team_venue: str = "home") -> dict:
    """Apply calibration adjustments to prediction confidence."""
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
