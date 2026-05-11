"""
MLB Bayesian Projection Engine

3-layer Bayesian model adapted for baseball:
  Layer 1: PRIOR    — Season average (with hyper-prior shrinkage for small samples)
  Layer 2: MOMENTUM — Exponentially-decayed recent form (last 10 games, newest first)
  Layer 3: COVARIATE — Venue (home/away) adjustment

No per-90 normalization (unlike soccer). All values are per-game raw stat values.
Monte Carlo uses Poisson for discrete count stats, Gaussian for continuous (IP).
"""

import math
import random
import statistics as stats_mod
from typing import Optional

# ── Prop type → per-game API field ──────────────────────────────────────────
BATTER_PROPS = {
    "hits":                  "hits",
    "home_runs":             "hr",
    "rbi":                   "rbi",
    "walks":                 "bb",
    "strikeouts":            "k",
    "runs":                  "runs",
    "total_bases":           "total_bases",
    "stolen_bases":          "stolen_bases",
    "doubles":               "doubles",
    "plate_appearances":     "plate_appearances",
    # Computed synthetic prop — derived from raw fields at extract time
    "hitter_fantasy_points": "__fantasy_pts__",
}

PITCHER_PROPS = {
    "pitcher_strikeouts": "p_k",
    "innings_pitched":    "ip",
    "hits_allowed":       "p_hits",
    "earned_runs":        "er",
    "walks_allowed":      "p_bb",
    "pitches_thrown":     "pitch_count",
    "batters_faced":      "batters_faced",
}

ALL_PROP_FIELDS = {**BATTER_PROPS, **PITCHER_PROPS}

# Props that use Poisson distribution (discrete counts)
# hitter_fantasy_points is continuous (Gaussian MC), so intentionally excluded
COUNT_STATS = {
    "hits", "home_runs", "rbi", "walks", "strikeouts", "runs",
    "total_bases", "stolen_bases", "doubles", "plate_appearances",
    "pitcher_strikeouts", "hits_allowed", "earned_runs", "walks_allowed",
    "pitches_thrown", "batters_faced",
}

# ── Season stats field mapping: prop_type → (total_field, games_field) ─────
SEASON_STAT_MAP = {
    "hits":               ("batting_h",   "batting_gp"),
    "home_runs":          ("batting_hr",  "batting_gp"),
    "rbi":                ("batting_rbi", "batting_gp"),
    "walks":              ("batting_bb",  "batting_gp"),
    "strikeouts":         ("batting_so",  "batting_gp"),
    "runs":               ("batting_r",   "batting_gp"),
    "total_bases":        ("batting_tb",  "batting_gp"),
    "stolen_bases":       ("batting_sb",  "batting_gp"),
    "doubles":            ("batting_2b",  "batting_gp"),
    "plate_appearances":  ("batting_ab",  "batting_gp"),  # use AB as proxy
    "pitcher_strikeouts": ("pitching_k",  "pitching_gp"),
    "innings_pitched":    ("pitching_ip", "pitching_gp"),
    "hits_allowed":       ("pitching_h",  "pitching_gp"),
    "earned_runs":        ("pitching_er", "pitching_gp"),
    "walks_allowed":      ("pitching_bb", "pitching_gp"),
    "pitches_thrown":        (None,          None),
    "batters_faced":         (None,          None),
    "hitter_fantasy_points": (None,          None),   # computed — handled separately
}

# Momentum decay weights (newest game = index 0)
BATTER_DECAY  = [1.0, 0.82, 0.68, 0.56, 0.46, 0.38, 0.31, 0.25, 0.21, 0.17]
PITCHER_DECAY = [1.0, 0.85, 0.72, 0.61, 0.52, 0.44, 0.37, 0.31, 0.26, 0.22]

# Home advantage by prop type (multiplicative)
HOME_ADJ = {
    "hits": 1.03, "home_runs": 1.04, "rbi": 1.03, "runs": 1.03,
    "walks": 1.01, "strikeouts": 0.99, "total_bases": 1.03,
    "stolen_bases": 1.02, "doubles": 1.03, "plate_appearances": 1.00,
    "hitter_fantasy_points": 1.03,
    "pitcher_strikeouts": 1.02, "innings_pitched": 1.01,
    "hits_allowed": 0.97, "earned_runs": 0.97, "walks_allowed": 0.99,
    "pitches_thrown": 1.01, "batters_faced": 1.01,
}


def _compute_fantasy_pts(game: dict) -> Optional[float]:
    """DraftKings-style hitter fantasy points from a per-game stat record.

    Scoring: 1B=+3, 2B=+5, 3B=+8 (treated as 1B — rare), HR=+10,
             RBI=+2, R=+2, BB=+2, SB=+5.
    Triples are not tracked separately in BDL so they are counted as singles
    (conservative, negligible impact on averages).
    """
    hits = game.get("hits")
    if hits is None:
        return None
    h       = float(hits)
    hr      = float(game.get("hr")           or 0)
    rbi     = float(game.get("rbi")          or 0)
    bb      = float(game.get("bb")           or 0)
    runs    = float(game.get("runs")         or 0)
    sb      = float(game.get("stolen_bases") or 0)
    doubles = float(game.get("doubles")      or 0)
    singles = max(0.0, h - doubles - hr)          # triples lumped into singles
    return round(singles * 3 + doubles * 5 + hr * 10 + rbi * 2 + runs * 2 + bb * 2 + sb * 5, 1)


def _compute_fantasy_pts_from_season(season: dict) -> Optional[float]:
    """Estimate per-game fantasy pts average from season aggregate stats."""
    gp = season.get("batting_gp")
    if not gp or int(gp) == 0:
        return None
    gp = float(gp)
    h       = float(season.get("batting_h",  0) or 0)
    hr      = float(season.get("batting_hr", 0) or 0)
    rbi     = float(season.get("batting_rbi",0) or 0)
    bb      = float(season.get("batting_bb", 0) or 0)
    runs    = float(season.get("batting_r",  0) or 0)
    sb      = float(season.get("batting_sb", 0) or 0)
    doubles = float(season.get("batting_2b", 0) or 0)
    singles = max(0.0, h - doubles - hr)
    total   = singles * 3 + doubles * 5 + hr * 10 + rbi * 2 + runs * 2 + bb * 2 + sb * 5
    return round(total / gp, 2)


def _ip_to_float(ip_val) -> Optional[float]:
    """Convert baseball IP notation (6.1 = 6⅓) to true decimal."""
    if ip_val is None:
        return None
    try:
        ip_val = float(ip_val)
    except (ValueError, TypeError):
        return None
    whole = int(ip_val)
    outs = round((ip_val - whole) * 10)
    if outs >= 3:  # already a true decimal (e.g. from season totals)
        return ip_val
    return whole + outs / 3.0


def _extract_game_val(game: dict, prop_type: str) -> Optional[float]:
    """Extract a prop value from a per-game stat record."""
    if prop_type == "hitter_fantasy_points":
        return _compute_fantasy_pts(game)
    field = ALL_PROP_FIELDS.get(prop_type)
    if not field:
        return None
    val = game.get(field)
    if val is None:
        return None
    if prop_type == "innings_pitched":
        return _ip_to_float(val)
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _is_valid_game(game: dict, prop_type: str) -> bool:
    """Check if a game log entry is relevant for this prop."""
    if prop_type in PITCHER_PROPS:
        return game.get("ip") is not None or game.get("p_k") is not None
    else:
        ab = game.get("at_bats")
        pa = game.get("plate_appearances")
        return (ab is not None and int(ab) > 0) or (pa is not None and int(pa) > 0)


def _poisson_mc(lam: float, line: float, n: int = 5000):
    """Monte Carlo Poisson simulation. Returns (p_over, p_under, ci_low, ci_high)."""
    if lam <= 0:
        return 2.0, 98.0, 0.0, 0.0
    samples = []
    over = 0
    L = math.exp(-min(lam, 700))
    for _ in range(n):
        # Knuth algorithm for small lambda; Normal approximation for large
        if lam <= 30:
            k, p = 0, 1.0
            while p > L:
                k += 1
                p *= random.random()
            val = float(k - 1)
        else:
            u1 = max(1e-12, random.random())
            u2 = random.random()
            z = math.sqrt(-2 * math.log(u1)) * math.cos(2 * math.pi * u2)
            val = max(0.0, lam + math.sqrt(lam) * z)
        samples.append(val)
        if val > line:
            over += 1
    p_over = round(over / n * 100, 1)
    p_under = round(100.0 - p_over, 1)
    s = sorted(samples)
    return p_over, p_under, s[int(0.10 * n)], s[int(0.90 * n)]


def _gaussian_mc(mean: float, std: float, line: float, n: int = 5000):
    """Monte Carlo Gaussian simulation for continuous stats (IP)."""
    if std <= 0:
        p_over = 100.0 if mean > line else 0.0
        return p_over, 100.0 - p_over, mean, mean
    samples = []
    over = 0
    for _ in range(n):
        u1 = max(1e-12, random.random())
        u2 = random.random()
        z = math.sqrt(-2 * math.log(u1)) * math.cos(2 * math.pi * u2)
        val = max(0.0, mean + std * z)
        samples.append(val)
        if val > line:
            over += 1
    p_over = round(over / n * 100, 1)
    p_under = round(100.0 - p_over, 1)
    s = sorted(samples)
    return p_over, p_under, s[int(0.10 * n)], s[int(0.90 * n)]


def compute_mlb_projection(
    game_logs: list,          # per-game dicts, any order (will sort by game_id desc)
    season_stats: Optional[dict],
    prop_type: str,
    line: float,
    venue: str,               # 'home' or 'away'
    position: str = "",
    prev_season_stats: Optional[dict] = None,
) -> dict:
    """
    Compute MLB Bayesian projection and P(over/under) for a given prop.
    Returns a dict compatible with the soccer predict response shape.
    """
    is_pitcher_prop = prop_type in PITCHER_PROPS
    is_count = prop_type in COUNT_STATS
    decay_weights = PITCHER_DECAY if is_pitcher_prop else BATTER_DECAY

    # ── Extract valid game values ────────────────────────────────────────────
    valid_games = []
    for g in game_logs:
        if not _is_valid_game(g, prop_type):
            continue
        val = _extract_game_val(g, prop_type)
        if val is None:
            continue
        valid_games.append({"val": val, "game": g})

    # Sort newest first (highest game_id = most recent)
    valid_games.sort(key=lambda x: x["game"].get("game_id", 0), reverse=True)

    n_games = len(valid_games)
    game_vals = [g["val"] for g in valid_games]

    # ── LAYER 1: PRIOR (season average) ─────────────────────────────────────
    prior_mean = None
    season_gp = 0

    if season_stats:
        if prop_type == "hitter_fantasy_points":
            computed = _compute_fantasy_pts_from_season(season_stats)
            if computed is not None:
                prior_mean = computed
                season_gp  = int(season_stats.get("batting_gp") or 0)
        else:
            stat_key, gp_key = SEASON_STAT_MAP.get(prop_type, (None, None))
            if stat_key and gp_key:
                total = season_stats.get(stat_key)
                gp = season_stats.get(gp_key)
                if total is not None and gp and int(gp) > 0:
                    if prop_type == "innings_pitched":
                        total = _ip_to_float(total) or total
                    prior_mean = float(total) / float(gp)
                    season_gp = int(gp)

    # Fallback: use game log average if no season stats
    if prior_mean is None and game_vals:
        prior_mean = stats_mod.mean(game_vals)
        season_gp = n_games

    if prior_mean is None:
        prior_mean = line  # last resort: assume line is fair

    # Hyper-prior shrinkage toward league average when sample is small
    # League priors (typical per-game averages across MLB)
    _LEAGUE_PRIORS = {
        "hits": 1.05, "home_runs": 0.18, "rbi": 0.70, "walks": 0.38,
        "strikeouts": 0.90, "runs": 0.58, "total_bases": 1.60,
        "stolen_bases": 0.12, "doubles": 0.22, "plate_appearances": 3.8,
        "hitter_fantasy_points": 8.5,   # league-average DK pts per plate appearance
        "pitcher_strikeouts": 5.8, "innings_pitched": 5.0,
        "hits_allowed": 5.2, "earned_runs": 2.5, "walks_allowed": 2.1,
        "pitches_thrown": 88.0, "batters_faced": 22.0,
    }
    league_avg = _LEAGUE_PRIORS.get(prop_type, prior_mean)
    # Shrink toward league avg when < 20 games
    shrink_weight = min(1.0, season_gp / 20.0)
    prior_mean = shrink_weight * prior_mean + (1.0 - shrink_weight) * league_avg

    prior_var = max(0.5, prior_mean * 1.2)  # Poisson-like variance

    # ── LAYER 2: MOMENTUM (exponential decay over recent games) ─────────────
    momentum_games = valid_games[:10]  # newest 10
    if momentum_games:
        weights = decay_weights[:len(momentum_games)]
        total_w = sum(weights)
        w_vals = [g["val"] * w for g, w in zip(momentum_games, weights)]
        momentum_mean = sum(w_vals) / total_w if total_w > 0 else prior_mean
        # Momentum variance from recent spread
        if len(momentum_games) >= 3:
            raw_vals = [g["val"] for g in momentum_games[:5]]
            try:
                momentum_var = max(0.5, stats_mod.variance(raw_vals))
            except Exception:
                momentum_var = prior_var
        else:
            momentum_var = prior_var
    else:
        momentum_mean = prior_mean
        momentum_var = prior_var

    # ── LAYER 3: COVARIATE (venue adjustment) ───────────────────────────────
    home_adj = HOME_ADJ.get(prop_type, 1.0)
    venue_multiplier = home_adj if venue == "home" else (2.0 - home_adj)

    # ── PRECISION-WEIGHTED COMBINATION ──────────────────────────────────────
    prior_precision = 1.0 / prior_var
    momentum_precision = max(0.5, n_games / momentum_var) if momentum_var > 0 else 1.0
    # Cap momentum precision so prior stays meaningful
    momentum_precision = min(momentum_precision, prior_precision * 3.0)
    total_precision = prior_precision + momentum_precision

    posterior_mean = (
        prior_precision * prior_mean + momentum_precision * momentum_mean
    ) / total_precision

    # Apply venue multiplier
    posterior_mean *= venue_multiplier

    # Round count stats to appropriate precision
    if is_count and prop_type not in {"innings_pitched"}:
        posterior_mean = round(posterior_mean, 1)
    else:
        posterior_mean = round(posterior_mean, 2)

    # ── EFFECTIVE STD (for CI and Gaussian MC) ───────────────────────────────
    posterior_std = math.sqrt(max(0.1, 1.0 / total_precision))
    if is_count and prop_type not in {"innings_pitched"}:
        # Use Poisson std floor: sqrt(lambda)
        posterior_std = max(posterior_std, math.sqrt(max(0.1, posterior_mean)))
    else:
        effective_std = max(posterior_std, 0.5)

    # ── MONTE CARLO ──────────────────────────────────────────────────────────
    if is_count and prop_type != "innings_pitched":
        p_over, p_under, ci_low, ci_high = _poisson_mc(posterior_mean, line)
    else:
        effective_std = max(posterior_std, posterior_mean * 0.12, 0.33)
        p_over, p_under, ci_low, ci_high = _gaussian_mc(posterior_mean, effective_std, line)

    # ── BAYESIAN TRUTH OVERRIDE ──────────────────────────────────────────────
    # Direction and confidence come from the probability — never from arbitrary rules
    recommendation = "OVER" if p_over >= p_under else "UNDER"
    raw_confidence = round(max(p_over, p_under), 1)

    # If direction reversal: flip projected value across line
    if recommendation == "OVER" and posterior_mean < line:
        posterior_mean = round(line + (line - posterior_mean) * 0.3, 2)
    elif recommendation == "UNDER" and posterior_mean > line:
        posterior_mean = round(line - (posterior_mean - line) * 0.3, 2)

    # ── CONFIDENCE CALIBRATION (empirical position caps) ────────────────────
    _POS_CAPS = {
        # Pitchers: strikeouts have high variance — cap OVER confidence
        "SP": {"pitcher_strikeouts": 72, "innings_pitched": 68},
        "RP": {"pitcher_strikeouts": 65},
    }
    pos_upper = (position or "").upper()
    cap = _POS_CAPS.get(pos_upper, {}).get(prop_type)
    confidence_score = raw_confidence
    if cap and confidence_score > cap:
        confidence_score = cap

    # Absolute floor/ceiling
    confidence_score = min(88.0, max(50.0, confidence_score))

    if confidence_score >= 70:
        conf_level = "High"
    elif confidence_score >= 60:
        conf_level = "Medium"
    else:
        conf_level = "Low"

    # ── SAMPLE QUALITY FLAGS ─────────────────────────────────────────────────
    sample_warning = None
    if n_games < 5:
        sample_warning = f"Low sample: only {n_games} relevant game(s) found."

    # ── BUILD GAME LOG FOR DISPLAY ───────────────────────────────────────────
    # Note: BDL /stats endpoint has no date or opponent data — game_id in stats
    # does NOT correspond to IDs in the /games endpoint. We use gameNumber
    # (1 = most recent) so the frontend can show meaningful context.
    display_logs = []
    for idx, entry in enumerate(valid_games[:30]):
        g = entry["game"]
        val = entry["val"]
        # Round count stats to whole numbers for display
        if prop_type in COUNT_STATS and prop_type != "innings_pitched":
            display_val = int(round(val))
        else:
            display_val = round(val, 1)
        log_entry = {
            "gameId":      g.get("game_id"),
            "gameNumber":  idx + 1,   # 1 = most recent start/game
            "value":       display_val,
            "propType":    prop_type,
            "sport":       "mlb",
        }
        # Add baseball context fields for tile display
        if prop_type in BATTER_PROPS:
            log_entry["atBats"] = g.get("at_bats")
            log_entry["hits"]   = g.get("hits")
            log_entry["hr"]     = g.get("hr")
            log_entry["rbi"]    = g.get("rbi")
            log_entry["avg"]    = g.get("avg")
        else:
            log_entry["ip"]         = g.get("ip")
            log_entry["era"]        = g.get("era")
            log_entry["pitchCount"] = g.get("pitch_count")
            log_entry["pHits"]      = g.get("p_hits")
        display_logs.append(log_entry)

    # Volatility
    if n_games >= 3:
        try:
            cv = stats_mod.stdev(game_vals[:10]) / prior_mean if prior_mean > 0 else 0
        except Exception:
            cv = 0
        if cv < 0.20:
            volatility = "LOW"
        elif cv < 0.40:
            volatility = "NORMAL"
        elif cv < 0.65:
            volatility = "HIGH"
        else:
            volatility = "EXTREME"
    else:
        volatility = "NORMAL"
        cv = 0

    # ── MOMENTUM LABEL ────────────────────────────────────────────────────────
    if prior_mean > 0:
        mom_ratio = momentum_mean / prior_mean
        if mom_ratio >= 1.08:
            momentum_label = "HOT"
        elif mom_ratio <= 0.92:
            momentum_label = "COLD"
        else:
            momentum_label = "NEUTRAL"
    else:
        momentum_label = "NEUTRAL"

    # ── COVARIATE ADJUSTMENT (venue effect in stat units) ────────────────────
    pre_venue_posterior = (
        prior_precision * prior_mean + momentum_precision * momentum_mean
    ) / total_precision
    covariate_adjustment = round(pre_venue_posterior * (venue_multiplier - 1.0), 2)

    # ── HIT RATES (fraction of recent games that went OVER the line) ─────────
    if game_vals and line is not None:
        over_count  = sum(1 for v in game_vals if v > line)
        under_count = sum(1 for v in game_vals if v <= line)
        total       = len(game_vals)
        hit_rates = {
            "over":  round(over_count  / total * 100, 1),
            "under": round(under_count / total * 100, 1),
            "n":     total,
        }
    else:
        hit_rates = None

    # ── STREAK FLAG ───────────────────────────────────────────────────────────
    recent_5 = game_vals[:5] if game_vals else []
    if len(recent_5) >= 3 and line is not None:
        over_streak  = all(v > line for v in recent_5)
        under_streak = all(v <= line for v in recent_5)
        streak_flag  = "OVER_STREAK" if over_streak else ("UNDER_STREAK" if under_streak else "MIXED")
    else:
        streak_flag = "MIXED"

    print(f"[MLB ENGINE] {prop_type} pos={position} venue={venue} "
          f"prior={prior_mean:.2f} momentum={momentum_mean:.2f} ({momentum_label}) "
          f"posterior={posterior_mean:.2f} vs line={line} "
          f"P(O)={p_over}% P(U)={p_under}% → {recommendation} ({confidence_score:.0f}%) "
          f"streak={streak_flag}")

    return {
        "sport":             "mlb",
        "propType":          prop_type,
        "line":              line,
        "projectedValue":    posterior_mean,
        "projection":        posterior_mean,
        "bayesianProjection":posterior_mean,
        "recommendation":    recommendation,
        "confidence":        round(confidence_score),
        "confidenceScore":   round(confidence_score),
        "rawConfidence":     round(raw_confidence),
        "confidenceLevel":   conf_level,
        "confidenceInterval":{"low": round(ci_low, 2), "high": round(ci_high, 2)},
        "venue":             venue,

        # ── Top-level fields that the UI REVERSE FORMULA card and analysis sections use ──
        "priorSamples":      n_games,          # triggers REVERSE FORMULA card (needs >= 3)
        "priorMean":         round(prior_mean, 2),
        "momentumMean":      round(momentum_mean, 2),
        "momentumLabel":     momentum_label,
        "covariateAdjustment": covariate_adjustment,
        "pOver":             p_over,
        "pUnder":            p_under,
        "hitRates":          hit_rates,
        "volatility":        volatility,
        "streakFlag":        streak_flag,
        "homeAvg":           None,   # BDL /stats has no per-game venue info
        "awayAvg":           None,

        "bayesianMetrics": {
            "pOver":             p_over,
            "pUnder":            p_under,
            "priorMean":         round(prior_mean, 2),
            "momentumMean":      round(momentum_mean, 2),
            "momentumLabel":     momentum_label,
            "posteriorMean":     posterior_mean,
            "sampleSize":        n_games,
            "volatility":        volatility,
            "cv":                round(cv, 3),
            "streakFlag":        streak_flag,
            "covariateAdjustment": covariate_adjustment,
            "priorPrecision":    round(prior_precision, 4),
            "momentumPrecision": round(momentum_precision, 4),
        },
        "gameLogs":        display_logs,
        "sampleSize":      n_games,
        "sampleWarning":   sample_warning,
    }
