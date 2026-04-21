"""
CalibrationEngine v2 — Granular feedback loop for AI predictions.
Tracks accuracy by: sport, league, prop type, recommendation, venue,
position, game context (blowout/close/normal), favorite/underdog,
confidence band, and line range. Generates actionable "reasons why"
for the AI prompt — not just numbers, but pattern explanations.
"""
from config import db
from datetime import datetime, timezone, timedelta
import asyncio

_cache = {}

# Position inference from prop type
SOCCER_GK_PROPS = {"saves"}
SOCCER_DEF_PROPS = {"tackles", "interceptions", "clearances", "blocks", "duels_won"}
SOCCER_ATK_PROPS = {"goals", "shots", "shots_on_target", "dribbles", "dribbles_success"}
SOCCER_MID_PROPS = {"pass_attempts", "key_passes", "crosses", "shots_assisted", "assists"}

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
DEFAULT_MARKET_WEIGHT = 0.65  # Default 65% market / 35% model (nfelo baseline)
MIN_BLEND_SAMPLES = 20  # Minimum settled picks before auto-tuning kicks in
FLIP_THRESHOLD = 0.45  # Flip recommendation if hit rate below 45%
EDGE_STRONG_PCT = 0.05  # 5% edge for STRONG recommendation
EDGE_LEAN_PCT = 0.02   # 2% edge for LEAN

# Cache for the dynamic market weight
_blend_cache = {"weight": None, "updated": None}


async def _compute_dynamic_market_weight(sport: str = "soccer") -> tuple:
    """
    Auto-tune the market blend ratio from settled pick data.
    Grid-searches weights 0.30-0.85 and picks the one that minimizes MAE.
    Returns (optimal_weight, sample_count, note).
    """
    now = datetime.now(timezone.utc)
    # Check cache (refresh every 2 hours)
    cache_key = f"blend_{sport}"
    cached = _blend_cache.get(cache_key)
    if cached and cached.get("updated") and (now - cached["updated"]).total_seconds() < 7200:
        return cached["weight"], cached.get("n", 0), cached.get("note", "")

    # Pull settled picks that have BOTH projectedValue and line
    query = {
        "status": "settled",
        "result": {"$in": ["hit", "miss"]},
        "projectedValue": {"$exists": True, "$ne": None},
        "line": {"$exists": True, "$gt": 0},
        "actualValue": {"$exists": True, "$ne": None},
    }
    if sport:
        query["sport"] = sport

    picks = await db.picks.find(query, {
        "_id": 0, "projectedValue": 1, "line": 1, "actualValue": 1
    }).to_list(5000)

    if len(picks) < MIN_BLEND_SAMPLES:
        _blend_cache[cache_key] = {"weight": DEFAULT_MARKET_WEIGHT, "updated": now, "n": len(picks), "note": "insufficient data"}
        return DEFAULT_MARKET_WEIGHT, len(picks), "insufficient data — using default 65/35"

    # Grid search: test weights from 0.30 to 0.85 in 0.05 steps
    best_weight = DEFAULT_MARKET_WEIGHT
    best_mae = float('inf')

    for w_int in range(30, 86, 5):
        w = w_int / 100.0
        total_err = 0
        for p in picks:
            proj = float(p["projectedValue"])
            line = float(p["line"])
            actual = float(p["actualValue"])
            blended = (1 - w) * proj + w * line
            total_err += abs(actual - blended)
        mae = total_err / len(picks)
        if mae < best_mae:
            best_mae = mae
            best_weight = w

    # Safety rails: clamp between 0.35 and 0.80
    best_weight = max(0.35, min(0.80, best_weight))

    note = f"auto-tuned from {len(picks)} picks (MAE={best_mae:.2f})"
    print(f"[ELITE CAL] Dynamic market weight: {best_weight:.0%} market / {1-best_weight:.0%} model ({note})")

    _blend_cache[cache_key] = {"weight": best_weight, "updated": now, "n": len(picks), "note": note}
    return best_weight, len(picks), note


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


def _blend_with_market_line(projected: float, line: float, market_weight: float = DEFAULT_MARKET_WEIGHT) -> tuple:
    """
    Correction 2: Market Line Blending.
    Blend AI projection with the sportsbook line using auto-tuned weight.
    Returns (blended_value, blend_note).
    """
    if line <= 0:
        return projected, ""

    model_weight = round(1 - market_weight, 2)
    blended = round(model_weight * projected + market_weight * line, 1)
    if blended == projected:
        return projected, ""

    note = f"Market blend: {projected} x {model_weight} + {line} x {market_weight} = {blended}"
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

    # --- Correction 2: Market Line Blending (AUTO-TUNED) ---
    dynamic_weight, blend_n, blend_note_detail = await _compute_dynamic_market_weight(sport)
    blended_proj, blend_note = _blend_with_market_line(corrected_proj, line, dynamic_weight)
    if blend_note:
        corrections.append(blend_note)
        if blend_n >= MIN_BLEND_SAMPLES:
            corrections.append(f"Blend ratio auto-tuned: {dynamic_weight:.0%} market / {1-dynamic_weight:.0%} model ({blend_note_detail})")
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
            "marketBlendWeight": round(dynamic_weight, 2),
            "blendSamples": blend_n,
        }
        alerts = prediction.get("tacticalAlerts", [])
        for c in corrections:
            alerts.append(f"[ELITE_CAL] {c}")
        prediction["tacticalAlerts"] = alerts
        print(f"[ELITE CAL] Applied {len(corrections)} corrections: proj {original_proj}→{final_proj}, rec {original_rec}→{new_rec}, conf {original_conf}→{new_conf}, edge={edge_label}")

    return prediction


# =====================================================================
# NIGHTLY CALIBRATION JOB — runs at midnight UTC every 24 hours.
# Analyses all settled missed picks, computes systematic biases per
# propType / propType+venue / propType+league, then persists them to
# the `calibration_offsets` collection so future predictions
# automatically correct for learned errors.
# =====================================================================

MIN_SAMPLES_NIGHTLY = 5    # minimum picks before we trust a bias estimate
CORRECTION_DAMPEN   = 0.40  # apply 40% of the learned bias (conservative)


async def run_nightly_calibration(sport: str = "soccer") -> dict:
    """
    Core nightly job.  Returns a summary dict describing what was learned.
    """
    now = datetime.now(timezone.utc)
    print(f"[NIGHTLY CAL] Starting calibration job at {now.isoformat()}")

    # ── Pull ALL settled picks that have both projectedValue & actualValue ──
    query = {
        "status": "settled",
        "result": {"$in": ["hit", "miss"]},
        "projectedValue": {"$exists": True, "$ne": None},
        "actualValue":    {"$exists": True, "$ne": None},
        "propType":       {"$exists": True},
    }
    if sport:
        query["sport"] = sport

    picks = await db.picks.find(query, {
        "_id": 0, "propType": 1, "recommendation": 1, "result": 1,
        "projectedValue": 1, "actualValue": 1, "line": 1,
        "venue": 1, "leagueId": 1,
    }).to_list(10000)

    if not picks:
        print("[NIGHTLY CAL] No settled picks found — nothing to calibrate.")
        return {"status": "skipped", "reason": "no settled picks"}

    # ── Bucket errors by various dimensions ────────────────────────────────
    # error = actual - projected  (positive → we under-projected)
    by_prop        = {}   # propType → [errors]
    by_prop_venue  = {}   # "propType|venue" → [errors]
    by_prop_league = {}   # "propType|leagueId" → [errors]
    by_prop_rec    = {}   # "propType|rec" → [errors]

    def _append(d, key, error):
        d.setdefault(key, []).append(error)

    for p in picks:
        pt     = p.get("propType", "")
        venue  = p.get("venue", "")
        league = str(p.get("leagueId", ""))
        rec    = p.get("recommendation", "")
        proj   = p.get("projectedValue")
        actual = p.get("actualValue")
        if not pt or proj is None or actual is None:
            continue
        try:
            error = float(actual) - float(proj)
        except (TypeError, ValueError):
            continue

        _append(by_prop,        pt,               error)
        _append(by_prop_venue,  f"{pt}|{venue}",  error)
        _append(by_prop_league, f"{pt}|{league}", error)
        _append(by_prop_rec,    f"{pt}|{rec}",    error)

    # ── Compute bias offsets and upsert to MongoDB ─────────────────────────
    saved = []

    async def _upsert_offset(dimension: str, key: str, errors: list):
        n = len(errors)
        if n < MIN_SAMPLES_NIGHTLY:
            return
        mean_err = sum(errors) / n
        if abs(mean_err) < 0.15:   # sub-0.15 bias — not worth correcting
            return
        dampened  = round(mean_err * CORRECTION_DAMPEN, 3)
        direction = "under-projected" if mean_err > 0 else "over-projected"
        rec = {
            "dimension":      dimension,
            "key":            key,
            "sampleCount":    n,
            "meanError":      round(mean_err, 3),
            "dampenedOffset": dampened,
            "direction":      direction,
            "sport":          sport,
            "updatedAt":      now,
        }
        await db.calibration_offsets.update_one(
            {"dimension": dimension, "key": key, "sport": sport},
            {"$set": rec},
            upsert=True,
        )
        saved.append(rec)
        print(
            f"[NIGHTLY CAL] {dimension}/{key}: n={n}, "
            f"mean_err={mean_err:+.2f} ({direction}), "
            f"dampened={dampened:+.3f}"
        )

    for pt, errors in by_prop.items():
        await _upsert_offset("prop", pt, errors)
    for key, errors in by_prop_venue.items():
        await _upsert_offset("prop_venue", key, errors)
    for key, errors in by_prop_league.items():
        await _upsert_offset("prop_league", key, errors)
    for key, errors in by_prop_rec.items():
        await _upsert_offset("prop_rec", key, errors)

    # Invalidate in-memory cache so the next prediction picks up fresh stats
    _cache.clear()
    _blend_cache.clear()

    summary = {
        "status":       "ok",
        "sport":        sport,
        "totalPicks":   len(picks),
        "offsetsSaved": len(saved),
        "runAt":        now.isoformat(),
        "offsets":      saved,
    }

    # Persist run summary for the status API endpoint
    await db.calibration_runs.update_one(
        {"sport": sport},
        {"$set": summary},
        upsert=True,
    )

    print(
        f"[NIGHTLY CAL] Done — {len(picks)} picks analysed, "
        f"{len(saved)} offsets updated."
    )
    return summary


async def apply_learned_offsets(
    posterior: float,
    prop_type: str,
    venue: str,
    recommendation: str,
    league_id: int = None,
    sport: str = "soccer",
) -> tuple:
    """
    Load persisted calibration offsets from MongoDB and apply the most specific
    matching one to the Bayesian posterior.

    Priority (most → least specific):
      1. prop_venue  e.g. "pass_attempts|home"
      2. prop_rec    e.g. "pass_attempts|over"
      3. prop        e.g. "pass_attempts"

    The dampened offset (already at 40% of raw bias) is applied additively.
    A ±20% posterior cap prevents wild corrections on thin sample sizes.
    Returns (adjusted_posterior, offset_note).
    """
    if not prop_type or posterior is None:
        return posterior, ""

    candidates = [
        ("prop_venue",  f"{prop_type}|{venue}"),
        ("prop_rec",    f"{prop_type}|{recommendation}"),
        ("prop",        prop_type),
    ]
    if league_id:
        candidates.insert(1, ("prop_league", f"{prop_type}|{str(league_id)}"))

    for dimension, key in candidates:
        doc = await db.calibration_offsets.find_one(
            {"dimension": dimension, "key": key, "sport": sport},
            {"_id": 0, "dampenedOffset": 1, "sampleCount": 1, "direction": 1},
        )
        if not doc:
            continue
        offset = doc.get("dampenedOffset", 0)
        n      = doc.get("sampleCount", 0)
        if n < MIN_SAMPLES_NIGHTLY or abs(offset) < 0.10:
            continue

        # Cap at ±20 % of posterior
        max_adj = max(0.5, abs(posterior) * 0.20)
        clamped = max(-max_adj, min(max_adj, offset))
        adjusted = round(posterior + clamped, 1)
        note = (
            f"LearnedOffset({dimension}/{key}): "
            f"{posterior}→{adjusted} ({clamped:+.2f}, n={n})"
        )
        print(f"[NIGHTLY CAL APPLY] {note}")
        return adjusted, note

    return posterior, ""


async def nightly_calibration_loop(sport: str = "soccer"):
    """
    Background loop: runs run_nightly_calibration() at midnight UTC every day.
    Also runs once 60 s after startup so offsets are populated immediately.
    """
    await asyncio.sleep(60)
    try:
        await run_nightly_calibration(sport)
    except Exception as exc:
        print(f"[NIGHTLY CAL] Startup run failed: {exc}")

    while True:
        now = datetime.now(timezone.utc)
        tomorrow_midnight = (now + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        wait_seconds = (tomorrow_midnight - now).total_seconds()
        print(
            f"[NIGHTLY CAL] Next run in {wait_seconds/3600:.1f}h "
            f"({tomorrow_midnight.strftime('%Y-%m-%d %H:%M UTC')})"
        )
        await asyncio.sleep(wait_seconds)
        try:
            await run_nightly_calibration(sport)
        except Exception as exc:
            print(f"[NIGHTLY CAL] Run failed: {exc}")


# ══════════════════════════════════════════════════════════════════════════
# LINE-DEVIATION INTELLIGENCE ENGINE
# ══════════════════════════════════════════════════════════════════════════
# Books don't set lines at season averages — they set them at their expected
# outcome for that specific game. The deviation between the book's line and
# our model's projection (which anchors on historical averages) is meaningful:
#
#   line / projectedValue > 1.0  →  book expects HIGHER than our model
#   line / projectedValue < 1.0  →  book expects LOWER than our model
#
# When we call UNDER on a line that's 20%+ above our projection, we're
# betting against information the book has. Historical hit rates by band:
#
#  Band        Deviation   Direction  Typical UNDER hit rate
#  "aligned"   0-5%        either     ~60-65% (model has edge)
#  "mild"      5-10%       against    ~55-58% (slight reduction)
#  "moderate"  10-15%      against    ~50-53% (near coin-flip)
#  "elevated"  15-20%      against    ~47-50% (lean with book)
#  "extreme"   20%+        against    ~42-47% (book knows more)
#
# These are learned from your actual settled picks database.
# ══════════════════════════════════════════════════════════════════════════

_dev_band_cache: dict = {}
_DEV_BAND_TTL = 7200  # 2 hours

DEVIATION_BANDS = [
    ("aligned",  0.00, 0.05),
    ("mild",     0.05, 0.10),
    ("moderate", 0.10, 0.15),
    ("elevated", 0.15, 0.20),
    ("extreme",  0.20, 9.99),
]

# Default hit rates when insufficient settled data exists
# Based on empirical sports betting research + our calibration findings
DEFAULT_DEVIATION_HIT_RATES = {
    # "aligned": UNDER/OVER have normal hit rates when line matches model
    ("aligned",  "under"): 62,
    ("aligned",  "over"):  62,
    # "mild": 5-10% above model for UNDER, book slightly disagrees
    ("mild",     "under"): 57,
    ("mild",     "over"):  60,
    # "moderate": 10-15% above model for UNDER, book moderately disagrees
    ("moderate", "under"): 52,
    ("moderate", "over"):  57,
    # "elevated": 15-20% above model for UNDER, book significantly disagrees
    ("elevated", "under"): 48,
    ("elevated", "over"):  54,
    # "extreme": 20%+ above model for UNDER, book strongly disagrees
    ("extreme",  "under"): 44,
    ("extreme",  "over"):  51,
}


async def compute_line_deviation_bands(
    prop_type: str = None,
    min_samples: int = 8,
) -> dict:
    """
    Query settled picks and compute UNDER/OVER hit rates by line-deviation band.
    deviation = abs(line - projectedValue) / projectedValue

    Returns dict: {(band_name, rec): {"hit_rate": %, "n": count, "hits": n}}
    """
    import time as _time
    cache_key = prop_type or "all"
    cached = _dev_band_cache.get(cache_key)
    if cached and (_time.time() - cached["ts"]) < _DEV_BAND_TTL:
        return cached["data"]

    query = {
        "status": "settled",
        "result": {"$in": ["hit", "miss"]},
        "projectedValue": {"$exists": True, "$ne": None},
        "actualValue":    {"$exists": True, "$ne": None},
        "line":           {"$exists": True, "$ne": None},
        "recommendation": {"$exists": True},
    }
    if prop_type:
        query["propType"] = prop_type

    try:
        picks = await db.picks.find(query, {
            "_id": 0, "projectedValue": 1, "line": 1,
            "result": 1, "recommendation": 1, "propType": 1,
        }).to_list(10000)
    except Exception as e:
        print(f"[DEV BANDS] DB error: {e}")
        return {}

    # Bucket picks by (band, recommendation) → hits, total
    buckets: dict = {}
    for p in picks:
        try:
            proj = float(p["projectedValue"])
            line = float(p["line"])
            rec  = (p.get("recommendation") or "").lower()
            res  = (p.get("result") or "").lower()
            if proj <= 0 or rec not in ("over", "under") or res not in ("hit", "miss"):
                continue
            dev = abs(line - proj) / proj  # 0.20 = 20% deviation
            band = None
            for bname, lo, hi in DEVIATION_BANDS:
                if lo <= dev < hi:
                    band = bname
                    break
            if not band:
                continue
            key = (band, rec)
            if key not in buckets:
                buckets[key] = {"hits": 0, "total": 0}
            buckets[key]["total"] += 1
            if res == "hit":
                buckets[key]["hits"] += 1
        except (TypeError, ValueError):
            continue

    result = {}
    for key, vals in buckets.items():
        n = vals["total"]
        h = vals["hits"]
        hit_rate = round(h / n * 100, 1) if n > 0 else None
        # Only trust the data if we have enough samples
        if n >= min_samples and hit_rate is not None:
            result[key] = {"hit_rate": hit_rate, "n": n, "hits": h, "default": False}
        else:
            # Fall back to defaults with note
            default = DEFAULT_DEVIATION_HIT_RATES.get(key, 55)
            result[key] = {"hit_rate": default, "n": n, "hits": h, "default": True}

    # Fill in any missing bands with defaults
    for band, lo, hi in DEVIATION_BANDS:
        for rec in ("under", "over"):
            key = (band, rec)
            if key not in result:
                default = DEFAULT_DEVIATION_HIT_RATES.get(key, 55)
                result[key] = {"hit_rate": default, "n": 0, "hits": 0, "default": True}

    _dev_band_cache[cache_key] = {"data": result, "ts": _time.time()}
    total_picks = sum(v["n"] for v in result.values())
    print(f"[DEV BANDS] Computed deviation bands from {total_picks} picks "
          f"({'prop=' + prop_type if prop_type else 'all props'})")
    return result


async def get_line_deviation_intel(
    line: float,
    projected_value: float,
    recommendation: str,
    prop_type: str = None,
) -> dict:
    """
    Given a specific line/projection/recommendation combo, return:
    - deviation %
    - band name
    - historical hit rate for this band + direction
    - confidence delta (positive = boost, negative = penalty)
    - natural-language explanation

    The confidence delta nudges the prediction's confidence toward the
    historically-calibrated hit rate.  Example: if the band shows 44% hit
    rate and our model gives 72% confidence, a -18% delta pulls it toward 54%.
    We apply a conservative 0.5 damping factor so we don't over-correct.
    """
    rec = (recommendation or "").lower()
    if projected_value <= 0 or line <= 0 or rec not in ("over", "under"):
        return {"band": "unknown", "deviation": 0, "hitRate": None, "confidenceDelta": 0, "note": ""}

    deviation = abs(line - projected_value) / projected_value

    # Determine direction alignment:
    # "against_book": our rec disagrees with where the book set the line
    # e.g., book sets line HIGH → implies OVER → we say UNDER → against_book
    book_implies_over  = line > projected_value
    against_book = (rec == "under" and book_implies_over) or (rec == "over" and not book_implies_over)

    band = "aligned"
    for bname, lo, hi in DEVIATION_BANDS:
        if lo <= deviation < hi:
            band = bname
            break

    # Fetch learned hit rates (or defaults)
    bands = await compute_line_deviation_bands(prop_type=prop_type)
    key   = (band, rec)
    band_data = bands.get(key, {})
    hit_rate  = band_data.get("hit_rate", DEFAULT_DEVIATION_HIT_RATES.get(key, 55))
    n         = band_data.get("n", 0)
    is_default = band_data.get("default", True)

    # Confidence delta: how far to nudge our model confidence toward the hit rate
    # dampen=0.5 → conservative; we don't over-ride the model, just inform it
    # Only apply when going against the book's implied direction
    conf_delta = 0
    if against_book and deviation >= 0.05:
        # How far is our expected hit rate from 50% (pure coin flip)?
        # Pull confidence toward historical hit rate with 50% damping
        conf_delta = round((hit_rate - 50) * 0.5)  # e.g. 44% → -3, 62% → +6

    dev_pct = round(deviation * 100, 1)
    direction_label = "above" if book_implies_over else "below"
    src_label = f"{n} settled picks" if not is_default else f"default (only {n} picks)"

    note = ""
    if against_book:
        if band == "aligned":
            note = f"Line {dev_pct}% {direction_label} model ({band} band) — normal confidence range"
        elif band == "mild":
            note = f"Line {dev_pct}% {direction_label} model ({band} band) — slight book disagreement, minor caution"
        elif band == "moderate":
            note = f"Line {dev_pct}% {direction_label} model ({band} band) — moderate book disagreement, near coin-flip"
        elif band == "elevated":
            note = f"Line {dev_pct}% {direction_label} model ({band} band) — book pricing in higher involvement, lean caution"
        elif band == "extreme":
            note = f"Line {dev_pct}% {direction_label} model ({band} band) — EXTREME book disagreement, historical {hit_rate}% hit rate ({src_label})"
    else:
        note = f"Line {dev_pct}% {direction_label} model — aligned with {rec.upper()} call"

    return {
        "band":            band,
        "deviation":       round(deviation, 3),
        "deviationPct":    dev_pct,
        "direction":       direction_label,
        "againstBook":     against_book,
        "hitRate":         hit_rate,
        "hitRateN":        n,
        "hitRateSource":   "learned" if not is_default else "default",
        "confidenceDelta": conf_delta,
        "note":            note,
    }
