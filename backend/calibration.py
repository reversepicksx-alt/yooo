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
    # England
    "39": "Premier League", "40": "Championship", "41": "League One", "42": "League Two",
    # Spain
    "140": "La Liga", "141": "La Liga 2",
    # Italy
    "135": "Serie A", "136": "Serie B",
    # Germany
    "78": "Bundesliga", "79": "Bundesliga 2",
    # France
    "61": "Ligue 1", "62": "Ligue 2",
    # Brazil
    "71": "Brasileirão", "72": "Série B (Brazil)",
    # Americas
    "253": "MLS", "262": "Liga MX", "254": "Serie A (Brazil)",
    # Argentina
    "128": "Liga Profesional",
    # Other Europe
    "307": "Saudi Pro", "203": "Süper Lig", "94": "Primeira Liga",
    "88": "Eredivisie", "144": "Belgian Pro", "218": "Austrian Bundesliga",
    "179": "Scottish Premiership", "197": "Turkish 1. Lig",
    # International
    "10": "Friendlies", "32": "World Cup Qualifiers",
    # Cups / Continental
    "2": "Champions League", "3": "Europa League", "4": "Euro",
    "848": "NWSL", "1": "World Cup", "5": "Nations League",
    "15": "FIFA Club World Cup", "16": "AFCON",
    "188": "Liga 1 (Peru)", "960": "Copa de la Liga",
    # Basketball
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


# =====================================================================
# ELITE CALIBRATION ENGINE v3 — Post-Consensus Hard Corrections
# Research-backed: Market regression, error correction, isotonic
# confidence mapping, recommendation flip guards, edge thresholds.
# =====================================================================

MIN_SAMPLES_FOR_CORRECTION = 10
MIN_SAMPLES_FOR_FLIP = 15
MARKET_WEIGHT = 0.65  # 65% market line, 35% AI projection (nfelo research optimal)
FLIP_THRESHOLD = 0.45  # Flip recommendation if hit rate below 45%
EDGE_STRONG_PCT = 0.05  # 5% edge for STRONG recommendation
EDGE_LEAN_PCT = 0.02   # 2% edge for LEAN


def _correct_projected_value(stats: dict, prop_type: str, recommendation: str, venue: str, projected: float) -> tuple:
    """
    Correction 1: Historical Error Correction.
    If we historically over-project pass_attempts|away by 7.2, subtract 7.2.
    Uses the most specific combo available, falling back to broader buckets.
    Returns (corrected_value, correction_applied, correction_note).
    """
    # Try most specific first: prop + venue + rec direction
    combos = [
        (f"{prop_type}|{venue}", stats.get("by_prop_venue", {})),
        (f"{prop_type}|{recommendation}", stats.get("by_prop_rec", {})),
        (prop_type, stats.get("by_prop", {})),
    ]

    for key, bucket in combos:
        data = bucket.get(key)
        if not data:
            continue
        errors = data.get("errors", [])
        count = data.get("count", 0)
        if count < MIN_SAMPLES_FOR_CORRECTION or len(errors) < MIN_SAMPLES_FOR_CORRECTION:
            continue

        avg_error = sum(errors) / len(errors)
        # avg_error = actual - projected. If negative, we over-project.
        # To correct: new_proj = old_proj + avg_error (adds negative = subtracts)
        if abs(avg_error) < 0.2:
            return projected, False, "well-calibrated"

        corrected = round(projected + avg_error, 1)
        direction = "down" if avg_error < 0 else "up"
        note = f"Error correction: {direction} by {abs(avg_error):.1f} (from {key}, n={count})"
        print(f"[ELITE CAL] {note}: {projected} → {corrected}")
        return corrected, True, note

    return projected, False, ""


def _blend_with_market_line(projected: float, line: float) -> tuple:
    """
    Correction 2: Market Line Blending.
    Blend AI projection with the sportsbook line.
    Research: 65% market / 35% model minimizes Brier score.
    Returns (blended_value, blend_note).
    """
    if line <= 0:
        return projected, ""

    blended = round((1 - MARKET_WEIGHT) * projected + MARKET_WEIGHT * line, 1)
    if blended == projected:
        return projected, ""

    note = f"Market blend: {projected} × 0.35 + {line} × 0.65 = {blended}"
    print(f"[ELITE CAL] {note}")
    return blended, note


def _check_recommendation_flip(stats: dict, prop_type: str, recommendation: str) -> tuple:
    """
    Correction 3: Recommendation Flip Guard.
    If prop+rec has < 45% hit rate with 15+ samples, flip direction.
    Returns (should_flip: bool, flip_note: str).
    """
    key = f"{prop_type}|{recommendation}"
    combo = stats.get("by_prop_rec", {}).get(key)
    if not combo:
        return False, ""

    h, m = combo.get("hit", 0), combo.get("miss", 0)
    total = h + m
    if total < MIN_SAMPLES_FOR_FLIP:
        return False, ""

    rate = h / total
    if rate < FLIP_THRESHOLD:
        opposite = "under" if recommendation == "over" else "over"
        # Check if the opposite direction is actually better
        opp_key = f"{prop_type}|{opposite}"
        opp_combo = stats.get("by_prop_rec", {}).get(opp_key)
        opp_rate = 0
        if opp_combo:
            oh, om = opp_combo.get("hit", 0), opp_combo.get("miss", 0)
            ot = oh + om
            if ot >= 5:
                opp_rate = oh / ot

        # Only flip if opposite direction is meaningfully better
        if opp_rate > rate + 0.10:
            note = f"FLIP: {prop_type} {recommendation.upper()} only {rate*100:.1f}% ({h}/{total}), {opposite.upper()} is {opp_rate*100:.1f}%"
            print(f"[ELITE CAL] {note}")
            return True, note

    return False, ""


def _recalibrate_confidence(stats: dict, confidence: int) -> tuple:
    """
    Correction 4: Confidence Recalibration.
    Map AI's confidence score to actual historical accuracy for that band.
    If 70%+ confidence picks historically only hit 55%, set confidence to 55%.
    Returns (recalibrated_confidence, recal_note).
    """
    bands = stats.get("by_confidence_band", {})

    # Determine which band this confidence falls into
    if confidence >= 70:
        band_key = "high_70+"
    elif confidence >= 55:
        band_key = "medium_55-69"
    else:
        band_key = "low_<55"

    band_data = bands.get(band_key)
    if not band_data:
        return confidence, ""

    h, m = band_data.get("hit", 0), band_data.get("miss", 0)
    total = h + m
    if total < 10:
        return confidence, ""

    actual_rate = round(h / total * 100)

    # If AI is overconfident by more than 8 points, pull down
    if confidence > actual_rate + 8:
        # Don't slam it all the way — use 60% of the gap
        gap = confidence - actual_rate
        adjustment = int(gap * 0.6)
        new_conf = max(45, confidence - adjustment)
        note = f"Confidence recal: {band_key} historically hits {actual_rate}%, AI said {confidence}% → {new_conf}%"
        print(f"[ELITE CAL] {note}")
        return new_conf, note

    # If AI is underconfident, nudge up slightly
    if actual_rate > confidence + 10 and total >= 20:
        bump = min(5, int((actual_rate - confidence) * 0.3))
        new_conf = min(85, confidence + bump)
        note = f"Confidence bump: {band_key} historically hits {actual_rate}%, AI said {confidence}% → {new_conf}%"
        print(f"[ELITE CAL] {note}")
        return new_conf, note

    return confidence, ""


def _apply_edge_threshold(projected: float, line: float, confidence: int) -> tuple:
    """
    Correction 5: Minimum Edge Threshold.
    Only show STRONG if corrected projection differs from line by > 5%.
    Below 2% → LOW conviction. 2-5% → LEAN.
    Returns (edge_label, edge_note).
    """
    if line <= 0:
        return "STRONG", ""

    edge_pct = abs(projected - line) / line

    if edge_pct >= EDGE_STRONG_PCT:
        return "STRONG", ""
    elif edge_pct >= EDGE_LEAN_PCT:
        note = f"Edge {edge_pct*100:.1f}% < 5% threshold → LEAN"
        return "LEAN", note
    else:
        note = f"Edge {edge_pct*100:.1f}% < 2% → LOW conviction (near coin flip)"
        return "LOW", note


async def apply_elite_calibration(
    prediction: dict, prop_type: str, line: float,
    venue: str = "home", sport: str = "soccer"
) -> dict:
    """
    Master function: Apply all 5 elite calibration corrections post-consensus.
    Also incorporates Grok pattern mining insights when available.
    """
    cal_stats = await get_calibration_stats(sport)
    if not cal_stats or cal_stats.get("total", 0) < MIN_SAMPLES_FOR_CORRECTION:
        return prediction

    original_proj = prediction.get("projectedValue", line)
    original_rec = prediction.get("recommendation", "over")
    original_conf = prediction.get("confidenceScore", 50)
    corrections = []

    # --- Grok Pattern Mining Insights ---
    try:
        grok_insights = await db.calibration_insights.find_one(
            {"type": "pattern_mining"}, {"_id": 0, "insights": 1, "raw_stats": 1}
        )
        if grok_insights and grok_insights.get("raw_stats"):
            rs = grok_insights["raw_stats"]
            # Check if this prop type has a notably bad hit rate
            prop_stats = rs.get("by_prop", {}).get(prop_type)
            if prop_stats:
                total = prop_stats.get("hit", 0) + prop_stats.get("miss", 0)
                if total >= 10:
                    rate = prop_stats["hit"] / total
                    if rate < 0.40:
                        corrections.append(f"GROK PATTERN: {prop_type} hit rate only {rate*100:.0f}% — extra caution")
            # Check venue bias from patterns
            venue_stats = rs.get("by_venue", {}).get(venue)
            if venue_stats:
                vt = venue_stats.get("hit", 0) + venue_stats.get("miss", 0)
                if vt >= 15:
                    vrate = venue_stats["hit"] / vt
                    if vrate < 0.45:
                        corrections.append(f"GROK PATTERN: {venue} picks hit only {vrate*100:.0f}%")
    except Exception:
        pass

    # --- Correction 1: Historical Error Correction ---
    corrected_proj, was_corrected, corr_note = _correct_projected_value(
        cal_stats, prop_type, original_rec, venue, original_proj
    )
    if was_corrected:
        corrections.append(corr_note)

    # --- Correction 2: Market Line Blending ---
    blended_proj, blend_note = _blend_with_market_line(corrected_proj, line)
    if blend_note:
        corrections.append(blend_note)
    final_proj = blended_proj

    # Update recommendation based on corrected projection
    new_rec = "over" if final_proj > line else "under"

    # --- Correction 3: Recommendation Flip Guard ---
    should_flip, flip_note = _check_recommendation_flip(cal_stats, prop_type, new_rec)
    if should_flip:
        new_rec = "under" if new_rec == "over" else "over"
        corrections.append(flip_note)

    # --- Correction 4: Confidence Recalibration ---
    new_conf, recal_note = _recalibrate_confidence(cal_stats, original_conf)
    if recal_note:
        corrections.append(recal_note)

    # --- Correction 5: Edge Threshold ---
    edge_label, edge_note = _apply_edge_threshold(final_proj, line, new_conf)
    if edge_note:
        corrections.append(edge_note)

    # Apply corrections to prediction
    prediction["projectedValue"] = final_proj
    prediction["recommendation"] = new_rec
    prediction["confidenceScore"] = new_conf
    prediction["confidenceLevel"] = (
        "Very High" if new_conf >= 75 else
        "High" if new_conf >= 65 else
        "Medium" if new_conf >= 50 else
        "Low"
    )
    prediction["edgeStrength"] = edge_label

    # Store correction metadata for transparency
    if corrections:
        prediction["calibrationApplied"] = {
            "originalProjection": original_proj,
            "correctedProjection": final_proj,
            "originalRecommendation": original_rec,
            "finalRecommendation": new_rec,
            "originalConfidence": original_conf,
            "calibratedConfidence": new_conf,
            "edgeStrength": edge_label,
            "corrections": corrections,
            "sampleSize": cal_stats.get("total", 0),
        }
        alerts = prediction.get("tacticalAlerts", [])
        for c in corrections:
            alerts.append(f"[ELITE_CAL] {c}")
        prediction["tacticalAlerts"] = alerts
        print(f"[ELITE CAL] Applied {len(corrections)} corrections: proj {original_proj}→{final_proj}, rec {original_rec}→{new_rec}, conf {original_conf}→{new_conf}, edge={edge_label}")

    return prediction
