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
    # Computed synthetic props — derived from raw fields at extract time
    "hitter_fantasy_points": "__fantasy_pts__",
    "hits_runs_rbis":        "__hits_runs_rbis__",
}

PITCHER_PROPS = {
    "pitcher_strikeouts":   "p_k",
    "innings_pitched":      "ip",
    "hits_allowed":         "p_hits",
    "earned_runs":          "er",
    "walks_allowed":        "p_bb",
    "pitches_thrown":       "pitch_count",
    "batters_faced":        "batters_faced",
    # Computed synthetic props
    "pitcher_fantasy_score": "__pitcher_fantasy__",
    "pitching_outs":         "__pitching_outs__",
}

ALL_PROP_FIELDS = {**BATTER_PROPS, **PITCHER_PROPS}

# Props that use Poisson distribution (discrete counts)
# hitter_fantasy_points, pitcher_fantasy_score are continuous (Gaussian MC)
COUNT_STATS = {
    "hits", "home_runs", "rbi", "walks", "strikeouts", "runs",
    "total_bases", "stolen_bases", "doubles", "plate_appearances",
    "pitcher_strikeouts", "hits_allowed", "earned_runs", "walks_allowed",
    "pitches_thrown", "batters_faced",
    "hits_runs_rbis", "pitching_outs",
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
    "pitches_thrown":         (None,           None),
    "batters_faced":          (None,           None),
    "hitter_fantasy_points":  (None,           None),   # computed — handled separately
    "hits_runs_rbis":         (None,           None),   # computed from H+R+RBI
    "pitcher_fantasy_score":  (None,           None),   # computed from IP/K/H/ER/BB
    "pitching_outs":          ("pitching_ip",  "pitching_gp"),  # derived from IP
}

# Momentum decay weights (newest game = index 0)
# Batters: L7 window — hot/cold streaks are recent-dominant in baseball
BATTER_DECAY  = [1.0, 0.80, 0.64, 0.51, 0.41, 0.33, 0.26]
PITCHER_DECAY = [1.0, 0.85, 0.72, 0.61, 0.52, 0.44, 0.37, 0.31, 0.26, 0.22]

# Home advantage by prop type (multiplicative) — pure travel/familiarity effect
HOME_ADJ = {
    "hits": 1.02, "home_runs": 1.03, "rbi": 1.02, "runs": 1.02,
    "walks": 1.01, "strikeouts": 0.99, "total_bases": 1.02,
    "stolen_bases": 1.01, "doubles": 1.02, "plate_appearances": 1.00,
    "hitter_fantasy_points": 1.02,
    "hits_runs_rbis": 1.02,
    "pitcher_strikeouts": 1.02, "innings_pitched": 1.01,
    "hits_allowed": 0.98, "earned_runs": 0.97, "walks_allowed": 0.99,
    "pitches_thrown": 1.01, "batters_faced": 1.01,
    "pitcher_fantasy_score": 1.01, "pitching_outs": 1.01,
}

# ── Park factors by home team (3-year MLB average, Baseball Reference) ────────
# Keyed by lowercase substring of team display_name / city.
# Values are multiplicative vs. neutral park (1.0 = perfectly neutral).
# Only batter props are significantly park-affected; pitcher props see ~half effect.
PARK_FACTORS: dict[str, dict[str, float]] = {
    # Extreme hitter parks
    "rockies":      {"hits":1.14,"home_runs":1.22,"runs":1.17,"total_bases":1.16,"rbi":1.14,"doubles":1.12,"hitter_fantasy_points":1.14},
    "cubs":         {"hits":1.08,"home_runs":1.10,"runs":1.08,"total_bases":1.09,"rbi":1.07,"doubles":1.06,"hitter_fantasy_points":1.08},
    "reds":         {"hits":1.07,"home_runs":1.11,"runs":1.09,"total_bases":1.09,"rbi":1.08,"doubles":1.05,"hitter_fantasy_points":1.08},
    "red sox":      {"hits":1.07,"home_runs":1.05,"runs":1.07,"total_bases":1.07,"rbi":1.06,"doubles":1.14,"hitter_fantasy_points":1.07},
    "phillies":     {"hits":1.05,"home_runs":1.08,"runs":1.06,"total_bases":1.07,"rbi":1.06,"doubles":1.04,"hitter_fantasy_points":1.06},
    "rangers":      {"hits":1.04,"home_runs":1.07,"runs":1.06,"total_bases":1.06,"rbi":1.05,"doubles":1.03,"hitter_fantasy_points":1.05},
    "braves":       {"hits":1.04,"home_runs":1.06,"runs":1.05,"total_bases":1.05,"rbi":1.05,"doubles":1.03,"hitter_fantasy_points":1.05},
    "diamondbacks": {"hits":1.04,"home_runs":1.07,"runs":1.06,"total_bases":1.06,"rbi":1.05,"doubles":1.03,"hitter_fantasy_points":1.05},
    "brewers":      {"hits":1.03,"home_runs":1.05,"runs":1.04,"total_bases":1.04,"rbi":1.04,"doubles":1.02,"hitter_fantasy_points":1.04},
    "yankees":      {"hits":1.02,"home_runs":1.07,"runs":1.04,"total_bases":1.05,"rbi":1.04,"doubles":1.01,"hitter_fantasy_points":1.04},
    "orioles":      {"hits":1.03,"home_runs":1.05,"runs":1.04,"total_bases":1.04,"rbi":1.04,"doubles":1.02,"hitter_fantasy_points":1.04},
    "white sox":    {"hits":1.04,"home_runs":1.06,"runs":1.04,"total_bases":1.05,"rbi":1.03,"doubles":1.02,"hitter_fantasy_points":1.04},
    # Near-neutral parks
    "pirates":      {"hits":1.01,"home_runs":0.99,"runs":1.00,"total_bases":1.00,"rbi":1.00,"doubles":1.01,"hitter_fantasy_points":1.00},
    "twins":        {"hits":1.01,"home_runs":1.02,"runs":1.01,"total_bases":1.01,"rbi":1.01,"doubles":1.00,"hitter_fantasy_points":1.01},
    "cardinals":    {"hits":1.01,"home_runs":1.00,"runs":1.01,"total_bases":1.01,"rbi":1.01,"doubles":1.01,"hitter_fantasy_points":1.01},
    "guardians":    {"hits":1.01,"home_runs":0.99,"runs":1.00,"total_bases":1.00,"rbi":1.00,"doubles":1.01,"hitter_fantasy_points":1.00},
    "blue jays":    {"hits":1.00,"home_runs":1.00,"runs":1.00,"total_bases":1.00,"rbi":1.00,"doubles":1.00,"hitter_fantasy_points":1.00},
    "athletics":    {"hits":1.00,"home_runs":1.01,"runs":1.00,"total_bases":1.00,"rbi":1.00,"doubles":1.00,"hitter_fantasy_points":1.00},
    # Pitcher-friendly parks
    "astros":       {"hits":0.97,"home_runs":0.95,"runs":0.96,"total_bases":0.96,"rbi":0.97,"doubles":0.97,"hitter_fantasy_points":0.96},
    "dodgers":      {"hits":0.97,"home_runs":0.97,"runs":0.97,"total_bases":0.97,"rbi":0.97,"doubles":0.97,"hitter_fantasy_points":0.97},
    "angels":       {"hits":0.96,"home_runs":0.94,"runs":0.96,"total_bases":0.95,"rbi":0.96,"doubles":0.96,"hitter_fantasy_points":0.96},
    "royals":       {"hits":0.97,"home_runs":0.94,"runs":0.96,"total_bases":0.96,"rbi":0.96,"doubles":0.97,"hitter_fantasy_points":0.96},
    "tigers":       {"hits":0.96,"home_runs":0.93,"runs":0.95,"total_bases":0.95,"rbi":0.95,"doubles":0.96,"hitter_fantasy_points":0.95},
    "mariners":     {"hits":0.95,"home_runs":0.92,"runs":0.94,"total_bases":0.94,"rbi":0.94,"doubles":0.95,"hitter_fantasy_points":0.94},
    "giants":       {"hits":0.95,"home_runs":0.88,"runs":0.93,"total_bases":0.93,"rbi":0.93,"doubles":0.95,"hitter_fantasy_points":0.93},
    "padres":       {"hits":0.93,"home_runs":0.90,"runs":0.92,"total_bases":0.92,"rbi":0.92,"doubles":0.93,"hitter_fantasy_points":0.92},
    "marlins":      {"hits":0.94,"home_runs":0.92,"runs":0.93,"total_bases":0.93,"rbi":0.93,"doubles":0.94,"hitter_fantasy_points":0.93},
    "nationals":    {"hits":0.96,"home_runs":0.95,"runs":0.96,"total_bases":0.96,"rbi":0.96,"doubles":0.96,"hitter_fantasy_points":0.96},
    "mets":         {"hits":0.97,"home_runs":0.95,"runs":0.96,"total_bases":0.96,"rbi":0.96,"doubles":0.97,"hitter_fantasy_points":0.96},
    "rays":         {"hits":0.95,"home_runs":0.91,"runs":0.93,"total_bases":0.93,"rbi":0.93,"doubles":0.94,"hitter_fantasy_points":0.93},
}

# Props affected by park factors (batter-side only; pitcher props see ~50% effect)
PARK_BATTER_PROPS = {
    "hits", "home_runs", "rbi", "runs", "total_bases", "doubles",
    "hitter_fantasy_points",
}
PARK_PITCHER_PROPS = {
    "hits_allowed", "earned_runs",   # park matters for pitchers too, but ~half
}


def _get_park_factor(park_team: str, prop_type: str) -> float:
    """
    Look up the park factor for the ballpark where today's game is played.
    park_team = display name of the HOME TEAM (whoever owns the stadium).
    Returns a multiplicative factor (1.0 = neutral, 1.10 = 10% hitter-friendly).
    """
    if not park_team or prop_type not in PARK_BATTER_PROPS | PARK_PITCHER_PROPS:
        return 1.0
    team_lower = park_team.lower()
    for key, factors in PARK_FACTORS.items():
        if key in team_lower:
            raw = factors.get(prop_type, 1.0)
            # Pitcher props see ~50% of the park effect (park biases batters more)
            if prop_type in PARK_PITCHER_PROPS:
                raw = 1.0 + (raw - 1.0) * 0.5
            return raw
    return 1.0


def _compute_hits_runs_rbis(game: dict) -> Optional[float]:
    """H + R + RBI combo stat per game."""
    hits = game.get("hits")
    if hits is None:
        return None
    return float(hits) + float(game.get("runs") or 0) + float(game.get("rbi") or 0)


def _compute_hits_runs_rbis_from_season(season: dict) -> Optional[float]:
    gp = season.get("batting_gp")
    if not gp or int(gp) == 0:
        return None
    gp = float(gp)
    h   = float(season.get("batting_h",   0) or 0)
    r   = float(season.get("batting_r",   0) or 0)
    rbi = float(season.get("batting_rbi", 0) or 0)
    return round((h + r + rbi) / gp, 2)


def _compute_pitcher_fantasy(game: dict) -> Optional[float]:
    """DraftKings-style pitcher fantasy points.
    Scoring: outs_recorded×1 + K×2 - H×0.6 - ER×2.25 - BB×0.6
    (Win/QS bonuses omitted — not trackable per-game from BDL)
    """
    ip = game.get("ip")
    if ip is None:
        return None
    ip_dec = _ip_to_float(ip)
    if ip_dec is None:
        return None
    k  = float(game.get("p_k")    or 0)
    h  = float(game.get("p_hits") or 0)
    er = float(game.get("er")     or 0)
    bb = float(game.get("p_bb")   or 0)
    outs = ip_dec * 3
    return round(outs + k * 2 - h * 0.6 - er * 2.25 - bb * 0.6, 1)


def _compute_pitcher_fantasy_from_season(season: dict) -> Optional[float]:
    gp = season.get("pitching_gp")
    if not gp or int(gp) == 0:
        return None
    gp  = float(gp)
    ip  = _ip_to_float(season.get("pitching_ip", 0)) or 0.0
    k   = float(season.get("pitching_k",  0) or 0)
    h   = float(season.get("pitching_h",  0) or 0)
    er  = float(season.get("pitching_er", 0) or 0)
    bb  = float(season.get("pitching_bb", 0) or 0)
    outs_total = ip * 3
    total = outs_total + k * 2 - h * 0.6 - er * 2.25 - bb * 0.6
    return round(total / gp, 2)


def _compute_pitching_outs(game: dict) -> Optional[float]:
    """Outs recorded in a start. BDL stores IP as '6.1' = 6 innings + 1 out = 19 outs."""
    ip = game.get("ip")
    if ip is None:
        return None
    try:
        parts = str(ip).split(".")
        whole = int(parts[0])
        extra = int(parts[1]) if len(parts) > 1 else 0
        return float(whole * 3 + extra)
    except (ValueError, TypeError):
        return None


def _compute_pitching_outs_from_season(season: dict) -> Optional[float]:
    gp = season.get("pitching_gp")
    if not gp or int(gp) == 0:
        return None
    gp = float(gp)
    ip = _ip_to_float(season.get("pitching_ip", 0)) or 0.0
    return round((ip * 3) / gp, 1)


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
    if prop_type == "hits_runs_rbis":
        return _compute_hits_runs_rbis(game)
    if prop_type == "pitcher_fantasy_score":
        return _compute_pitcher_fantasy(game)
    if prop_type == "pitching_outs":
        return _compute_pitching_outs(game)
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
    park_team: str = "",      # home team name → used to look up park factor
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
        elif prop_type == "hits_runs_rbis":
            computed = _compute_hits_runs_rbis_from_season(season_stats)
            if computed is not None:
                prior_mean = computed
                season_gp  = int(season_stats.get("batting_gp") or 0)
        elif prop_type == "pitcher_fantasy_score":
            computed = _compute_pitcher_fantasy_from_season(season_stats)
            if computed is not None:
                prior_mean = computed
                season_gp  = int(season_stats.get("pitching_gp") or 0)
        elif prop_type == "pitching_outs":
            computed = _compute_pitching_outs_from_season(season_stats)
            if computed is not None:
                prior_mean = computed
                season_gp  = int(season_stats.get("pitching_gp") or 0)
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
        "hitter_fantasy_points": 8.5,   # league-average DK pts per game
        "hits_runs_rbis": 2.33,         # ~1.05H + 0.58R + 0.70RBI
        "pitcher_strikeouts": 5.8, "innings_pitched": 5.0,
        "hits_allowed": 5.2, "earned_runs": 2.5, "walks_allowed": 2.1,
        "pitches_thrown": 88.0, "batters_faced": 22.0,
        "pitcher_fantasy_score": 16.6,  # outs(15)+K(11.6)-H(-3.12)-ER(-5.63)-BB(-1.26)
        "pitching_outs": 15.0,          # ~5 IP avg × 3 outs/inning
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

    # ── LAYER 4: PARK FACTOR ────────────────────────────────────────────────
    park_factor = _get_park_factor(park_team, prop_type)
    posterior_mean *= park_factor

    # Round count stats to appropriate precision
    if is_count and prop_type not in {"innings_pitched"}:
        posterior_mean = round(posterior_mean, 1)
    else:
        posterior_mean = round(posterior_mean, 2)

    # ── EARLY EXIT RISK DETECTION (pitcher strikeouts only) ─────────────────
    # MLB starters get scratched / pulled in the 1st inning ~7% of games →
    # automatic actual=0 K, killing any OVER bet. We detect this pattern from
    # the recent game log and apply a compound scratch discount to the Poisson
    # lambda before MC so the model naturally leans UNDER on fragile starters.
    early_exit_risk = False
    zero_k_count    = 0
    scratch_discount = 1.0
    if prop_type == "pitcher_strikeouts":
        recent_vals  = [g["val"] for g in valid_games[:5]]
        zero_k_count = sum(1 for v in recent_vals if v == 0)
        # Base league-wide scratch probability: ~7% → 0.93 multiplier
        base_scratch = 0.93
        if zero_k_count >= 3:
            # 3+ zero-K starts in last 5: fragile/volatile starter
            early_exit_risk  = True
            scratch_discount = base_scratch * 0.88   # total ~18% reduction
        elif zero_k_count == 2:
            early_exit_risk  = True
            scratch_discount = base_scratch * 0.92   # total ~14% reduction
        elif zero_k_count == 1:
            scratch_discount = base_scratch * 0.96   # total ~11% reduction
        else:
            scratch_discount = base_scratch          # baseline 7% reduction

    # ── EFFECTIVE STD (for CI and Gaussian MC) ───────────────────────────────
    posterior_std = math.sqrt(max(0.1, 1.0 / total_precision))
    if is_count and prop_type not in {"innings_pitched"}:
        # Use Poisson std floor: sqrt(lambda)
        posterior_std = max(posterior_std, math.sqrt(max(0.1, posterior_mean)))
    else:
        effective_std = max(posterior_std, 0.5)

    # ── MONTE CARLO ──────────────────────────────────────────────────────────
    if is_count and prop_type != "innings_pitched":
        # For pitcher strikeouts apply scratch discount to the Poisson lambda.
        # This shifts P(OVER) down without touching the direction of the projection
        # itself — it reflects the real-world probability a starter never pitches.
        mc_lambda = posterior_mean * scratch_discount if prop_type == "pitcher_strikeouts" else posterior_mean
        p_over, p_under, ci_low, ci_high = _poisson_mc(mc_lambda, line)
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

    # ── CONFIDENCE CALIBRATION (empirical + direction-specific caps) ─────────
    # Empirical hit rates from Atlas audit:
    #   pitcher_strikeouts OVER  → 36% historical  → hard cap 62%
    #   pitcher_strikeouts UNDER → 73% historical  → cap 73%
    #   innings_pitched OVER     → volatile        → cap 62%
    # Without this, the model produces 70-73% "High" confidence OVER picks
    # that lose 64% of the time — the worst possible outcome.
    _DIRECTION_CAPS: dict[str, dict[str, float]] = {
        "pitcher_strikeouts": {"OVER": 62.0, "UNDER": 73.0},
        "innings_pitched":    {"OVER": 62.0, "UNDER": 68.0},
    }
    # Position-level overlay (relievers are even more volatile)
    _POS_CAPS: dict[str, dict[str, float]] = {
        "RP": {"pitcher_strikeouts": 60.0, "innings_pitched": 58.0},
    }
    pos_upper    = (position or "").upper()
    rec_dir      = "OVER" if p_over >= p_under else "UNDER"
    dir_cap      = _DIRECTION_CAPS.get(prop_type, {}).get(rec_dir)
    pos_cap      = _POS_CAPS.get(pos_upper, {}).get(prop_type)

    confidence_score = raw_confidence
    if dir_cap is not None:
        confidence_score = min(confidence_score, dir_cap)
    if pos_cap is not None:
        confidence_score = min(confidence_score, pos_cap)

    # Absolute floor/ceiling — baseball is highly variable; cap at 73% max
    confidence_score = min(73.0, max(50.0, confidence_score))

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
    park_factor_pct = round((park_factor - 1.0) * 100, 1)   # e.g. +14.0 for Coors

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

    early_exit_note = f" ⚠ EARLY_EXIT_RISK zero_k={zero_k_count} discount={scratch_discount:.2f}" if early_exit_risk else f" scratch_discount={scratch_discount:.2f}"
    print(f"[MLB ENGINE] {prop_type} pos={position} venue={venue} "
          f"prior={prior_mean:.2f} momentum={momentum_mean:.2f} ({momentum_label}) "
          f"posterior={posterior_mean:.2f} vs line={line} "
          f"P(O)={p_over}% P(U)={p_under}% → {recommendation} ({confidence_score:.0f}%) "
          f"streak={streak_flag}{early_exit_note}")

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
            "parkFactor":        park_factor,
            "parkFactorPct":     park_factor_pct,
            "parkTeam":          park_team,
            "priorPrecision":    round(prior_precision, 4),
            "momentumPrecision": round(momentum_precision, 4),
            # Early-exit / scratch risk fields (pitcher strikeouts only)
            "earlyExitRisk":     early_exit_risk,
            "zeroKCount":        zero_k_count,
            "scratchDiscount":   round(scratch_discount, 3),
        },
        "gameLogs":        display_logs,
        "sampleSize":      n_games,
        "sampleWarning":   sample_warning,
    }
