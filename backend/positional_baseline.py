"""
Positional Role Baseline — reality-checks Bayesian projections against
what players in the same position and team-context actually produce.

Logic:
  - Every position has a realistic output range per prop type per possession tier
  - If the Bayesian projection lands way outside that range, it gets squeezed back
  - Squeeze strength is inversely proportional to sample count:
      8+ game logs  → no squeeze (trust the player's own data)
      4  game logs  → mild squeeze (~20%)
      1  game log   → strong squeeze (~45%)
      0  game logs  → maximum squeeze (~55%)

This prevents:
  - A CDM who played for a high-possession club (80-pass history) being
    projected at 80 passes for a new low-possession club
  - A GK on a dominant team being projected at 4 saves (unrealistic ceiling)
  - Strikers in counter-attack systems being projected at 5 shots

Possession tiers:
  high  >= 55%   (possession-dominant — PSG, Man City, Bayern style)
  mid   47–55%   (balanced — most mid-table clubs)
  low   < 47%    (defensive / counter-attacking — sit deep, concede possession)
"""

from __future__ import annotations

# ── Position group mapping ────────────────────────────────────────────────────
_POSITION_MAP: dict[str, str] = {
    "G": "GK", "GK": "GK", "GOALKEEPER": "GK",
    "CB": "CB", "DC": "CB", "CENTREBACK": "CB", "CENTRE-BACK": "CB",
    "LB": "FB", "RB": "FB", "LWB": "FB", "RWB": "FB", "WB": "FB",
    "FULLBACK": "FB", "WINGBACK": "FB", "LEFT BACK": "FB", "RIGHT BACK": "FB",
    "LEFT-BACK": "FB", "RIGHT-BACK": "FB",
    "CDM": "CDM", "DM": "CDM", "DMF": "CDM", "DLP": "CDM",
    "BALL-WINNER": "CDM", "DEFENSIVE MIDFIELDER": "CDM", "DEFENSIVE MID": "CDM",
    "CM": "CM", "MF": "CM", "BOX-TO-BOX": "CM", "MEZZALA": "CM",
    "CENTRAL MIDFIELDER": "CM", "MIDFIELDER": "CM", "CENTRAL MID": "CM",
    "CAM": "CAM", "AM": "CAM", "OMF": "CAM", "SS": "CAM",
    "ATTACKING MIDFIELDER": "CAM", "SHADOW STRIKER": "CAM",
    "ATTACKING MID": "CAM", "NO. 10": "CAM",
    "LW": "W", "RW": "W", "LM": "W", "RM": "W",
    "WINGER": "W", "LEFT WINGER": "W", "RIGHT WINGER": "W",
    "LEFT MIDFIELD": "W", "RIGHT MIDFIELD": "W",
    "ST": "ST", "CF": "ST", "FW": "ST", "STRIKER": "ST",
    "CENTRE FORWARD": "ST", "CENTER FORWARD": "ST", "FORWARD": "ST",
}


def _pos_group(position: str) -> str | None:
    if not position:
        return None
    p = position.upper().strip()
    if p in _POSITION_MAP:
        return _POSITION_MAP[p]
    for key, group in _POSITION_MAP.items():
        if key in p:
            return group
    return None


def _poss_tier(expected_poss: float) -> str:
    if expected_poss >= 55.0:
        return "high"
    if expected_poss >= 47.0:
        return "mid"
    return "low"


# ── Baseline table ────────────────────────────────────────────────────────────
# (pos_group, poss_tier, prop_type) → (p25, p50, p75)
# All values are per-90-minute actual outputs for a starting player.
# GK save / GK pass: possession is INVERTED (low poss = under pressure = more saves/back-passes)
_BASELINES: dict[tuple, tuple] = {

    # ── PASS ATTEMPTS ────────────────────────────────────────────────────────
    ("CDM", "high", "pass_attempts"): (68, 82, 100),
    ("CDM", "mid",  "pass_attempts"): (52, 65,  80),
    ("CDM", "low",  "pass_attempts"): (38, 50,  64),

    ("CM",  "high", "pass_attempts"): (58, 72,  88),
    ("CM",  "mid",  "pass_attempts"): (46, 58,  72),
    ("CM",  "low",  "pass_attempts"): (34, 46,  58),

    ("CAM", "high", "pass_attempts"): (46, 58,  72),
    ("CAM", "mid",  "pass_attempts"): (36, 46,  58),
    ("CAM", "low",  "pass_attempts"): (26, 36,  48),

    ("CB",  "high", "pass_attempts"): (58, 72,  88),
    ("CB",  "mid",  "pass_attempts"): (44, 55,  68),
    ("CB",  "low",  "pass_attempts"): (30, 42,  54),

    ("FB",  "high", "pass_attempts"): (52, 64,  78),
    ("FB",  "mid",  "pass_attempts"): (40, 52,  65),
    ("FB",  "low",  "pass_attempts"): (28, 40,  52),

    # GK passes INCREASE when team has low possession (more back-passes under pressure)
    ("GK",  "high", "pass_attempts"): (24, 32,  42),
    ("GK",  "mid",  "pass_attempts"): (28, 36,  46),
    ("GK",  "low",  "pass_attempts"): (32, 42,  54),

    ("W",   "high", "pass_attempts"): (36, 46,  58),
    ("W",   "mid",  "pass_attempts"): (28, 38,  48),
    ("W",   "low",  "pass_attempts"): (20, 30,  40),

    ("ST",  "high", "pass_attempts"): (22, 32,  44),
    ("ST",  "mid",  "pass_attempts"): (18, 26,  36),
    ("ST",  "low",  "pass_attempts"): (14, 22,  30),

    # ── SAVES (GK only — inverted possession logic) ───────────────────────────
    ("GK",  "low",  "saves"): (2.5, 3.5, 5.0),   # under pressure, many shots faced
    ("GK",  "mid",  "saves"): (1.5, 2.5, 3.5),
    ("GK",  "high", "saves"): (0.5, 1.5, 2.5),   # dominant team, few shots faced

    # ── SHOTS ─────────────────────────────────────────────────────────────────
    ("ST",  "high", "shots"): (3.0, 4.0, 5.5),
    ("ST",  "mid",  "shots"): (2.2, 3.0, 4.2),
    ("ST",  "low",  "shots"): (1.5, 2.2, 3.2),

    ("W",   "high", "shots"): (2.0, 2.8, 4.0),
    ("W",   "mid",  "shots"): (1.5, 2.2, 3.2),
    ("W",   "low",  "shots"): (1.0, 1.6, 2.4),

    ("CAM", "high", "shots"): (1.5, 2.2, 3.2),
    ("CAM", "mid",  "shots"): (1.0, 1.6, 2.4),
    ("CAM", "low",  "shots"): (0.6, 1.2, 1.8),

    ("CM",  "high", "shots"): (0.8, 1.4, 2.0),
    ("CM",  "mid",  "shots"): (0.5, 1.0, 1.6),
    ("CM",  "low",  "shots"): (0.3, 0.7, 1.2),

    ("CDM", "high", "shots"): (0.5, 0.9, 1.4),
    ("CDM", "mid",  "shots"): (0.3, 0.7, 1.1),
    ("CDM", "low",  "shots"): (0.2, 0.5, 0.9),

    ("CB",  "high", "shots"): (0.3, 0.6, 1.0),
    ("CB",  "mid",  "shots"): (0.2, 0.5, 0.8),
    ("CB",  "low",  "shots"): (0.1, 0.3, 0.6),

    ("FB",  "high", "shots"): (0.5, 0.9, 1.4),
    ("FB",  "mid",  "shots"): (0.3, 0.7, 1.1),
    ("FB",  "low",  "shots"): (0.2, 0.5, 0.8),

    # ── SHOTS ON TARGET ───────────────────────────────────────────────────────
    ("ST",  "high", "shots_on_target"): (1.2, 1.8, 2.5),
    ("ST",  "mid",  "shots_on_target"): (0.9, 1.4, 2.0),
    ("ST",  "low",  "shots_on_target"): (0.6, 1.0, 1.5),

    ("W",   "high", "shots_on_target"): (0.8, 1.2, 1.8),
    ("W",   "mid",  "shots_on_target"): (0.6, 1.0, 1.5),
    ("W",   "low",  "shots_on_target"): (0.4, 0.7, 1.1),

    ("CAM", "high", "shots_on_target"): (0.6, 1.0, 1.5),
    ("CAM", "mid",  "shots_on_target"): (0.4, 0.7, 1.1),
    ("CAM", "low",  "shots_on_target"): (0.3, 0.5, 0.8),

    ("CM",  "high", "shots_on_target"): (0.3, 0.6, 0.9),
    ("CM",  "mid",  "shots_on_target"): (0.2, 0.4, 0.7),
    ("CM",  "low",  "shots_on_target"): (0.1, 0.3, 0.5),

    ("CDM", "high", "shots_on_target"): (0.2, 0.4, 0.6),
    ("CDM", "mid",  "shots_on_target"): (0.1, 0.3, 0.5),
    ("CDM", "low",  "shots_on_target"): (0.1, 0.2, 0.4),

    # ── TACKLES ───────────────────────────────────────────────────────────────
    # Inverted: low possession → more defending → more tackles
    ("CDM", "low",  "tackles"): (2.8, 3.8, 5.0),
    ("CDM", "mid",  "tackles"): (2.0, 2.8, 3.8),
    ("CDM", "high", "tackles"): (1.2, 1.8, 2.6),

    ("CM",  "low",  "tackles"): (1.8, 2.6, 3.5),
    ("CM",  "mid",  "tackles"): (1.2, 1.8, 2.6),
    ("CM",  "high", "tackles"): (0.8, 1.2, 1.8),

    ("CB",  "low",  "tackles"): (1.5, 2.2, 3.2),
    ("CB",  "mid",  "tackles"): (1.0, 1.5, 2.2),
    ("CB",  "high", "tackles"): (0.6, 1.0, 1.5),

    ("FB",  "low",  "tackles"): (1.5, 2.2, 3.0),
    ("FB",  "mid",  "tackles"): (1.0, 1.6, 2.2),
    ("FB",  "high", "tackles"): (0.6, 1.0, 1.5),

    ("W",   "low",  "tackles"): (0.8, 1.4, 2.0),
    ("W",   "mid",  "tackles"): (0.6, 1.0, 1.5),
    ("W",   "high", "tackles"): (0.4, 0.7, 1.1),

    # ── CLEARANCES ────────────────────────────────────────────────────────────
    ("CB",  "low",  "clearances"): (4.0, 5.5, 7.5),
    ("CB",  "mid",  "clearances"): (2.5, 3.5, 5.0),
    ("CB",  "high", "clearances"): (1.0, 1.8, 2.8),

    ("FB",  "low",  "clearances"): (2.0, 3.0, 4.5),
    ("FB",  "mid",  "clearances"): (1.2, 2.0, 3.2),
    ("FB",  "high", "clearances"): (0.5, 1.0, 1.8),

    ("CDM", "low",  "clearances"): (1.0, 1.6, 2.4),
    ("CDM", "mid",  "clearances"): (0.6, 1.0, 1.5),
    ("CDM", "high", "clearances"): (0.3, 0.6, 1.0),

    # ── KEY PASSES ────────────────────────────────────────────────────────────
    ("CAM", "high", "key_passes"): (2.0, 2.8, 3.8),
    ("CAM", "mid",  "key_passes"): (1.4, 2.0, 2.8),
    ("CAM", "low",  "key_passes"): (0.8, 1.4, 2.0),

    ("CM",  "high", "key_passes"): (1.2, 1.8, 2.6),
    ("CM",  "mid",  "key_passes"): (0.8, 1.2, 1.8),
    ("CM",  "low",  "key_passes"): (0.4, 0.8, 1.2),

    ("CDM", "high", "key_passes"): (0.6, 1.0, 1.5),
    ("CDM", "mid",  "key_passes"): (0.4, 0.7, 1.1),
    ("CDM", "low",  "key_passes"): (0.2, 0.4, 0.7),

    ("W",   "high", "key_passes"): (1.5, 2.2, 3.0),
    ("W",   "mid",  "key_passes"): (1.0, 1.5, 2.2),
    ("W",   "low",  "key_passes"): (0.6, 1.0, 1.5),

    ("FB",  "high", "key_passes"): (0.8, 1.3, 1.8),
    ("FB",  "mid",  "key_passes"): (0.5, 0.9, 1.3),
    ("FB",  "low",  "key_passes"): (0.3, 0.6, 1.0),

    ("ST",  "high", "key_passes"): (0.5, 0.9, 1.4),
    ("ST",  "mid",  "key_passes"): (0.3, 0.6, 1.0),
    ("ST",  "low",  "key_passes"): (0.2, 0.4, 0.7),

    # ── CROSSES ───────────────────────────────────────────────────────────────
    ("FB",  "high", "crosses"): (2.5, 3.8, 5.5),
    ("FB",  "mid",  "crosses"): (1.5, 2.5, 3.8),
    ("FB",  "low",  "crosses"): (0.8, 1.5, 2.5),

    ("W",   "high", "crosses"): (3.0, 4.5, 6.5),
    ("W",   "mid",  "crosses"): (2.0, 3.2, 4.8),
    ("W",   "low",  "crosses"): (1.0, 2.0, 3.2),

    ("CAM", "high", "crosses"): (0.5, 1.0, 1.8),
    ("CAM", "mid",  "crosses"): (0.3, 0.7, 1.2),
    ("CAM", "low",  "crosses"): (0.2, 0.4, 0.8),

    # ── DRIBBLES ──────────────────────────────────────────────────────────────
    ("W",   "high", "dribbles"): (2.5, 3.5, 4.8),
    ("W",   "mid",  "dribbles"): (2.0, 3.0, 4.2),
    ("W",   "low",  "dribbles"): (1.5, 2.3, 3.4),

    ("CAM", "high", "dribbles"): (1.8, 2.6, 3.6),
    ("CAM", "mid",  "dribbles"): (1.4, 2.0, 2.8),
    ("CAM", "low",  "dribbles"): (1.0, 1.6, 2.2),

    ("ST",  "high", "dribbles"): (1.2, 1.8, 2.5),
    ("ST",  "mid",  "dribbles"): (0.8, 1.4, 2.0),
    ("ST",  "low",  "dribbles"): (0.5, 1.0, 1.5),

    ("CM",  "high", "dribbles"): (1.0, 1.5, 2.2),
    ("CM",  "mid",  "dribbles"): (0.8, 1.2, 1.8),
    ("CM",  "low",  "dribbles"): (0.5, 1.0, 1.4),

    ("CDM", "high", "dribbles"): (0.5, 0.9, 1.4),
    ("CDM", "mid",  "dribbles"): (0.4, 0.7, 1.1),
    ("CDM", "low",  "dribbles"): (0.3, 0.6, 0.9),

    # ── INTERCEPTIONS ────────────────────────────────────────────────────────
    ("CDM", "low",  "interceptions"): (1.5, 2.2, 3.0),
    ("CDM", "mid",  "interceptions"): (1.0, 1.5, 2.2),
    ("CDM", "high", "interceptions"): (0.5, 1.0, 1.5),

    ("CB",  "low",  "interceptions"): (1.2, 1.8, 2.5),
    ("CB",  "mid",  "interceptions"): (0.8, 1.2, 1.8),
    ("CB",  "high", "interceptions"): (0.4, 0.8, 1.2),

    ("CM",  "low",  "interceptions"): (0.8, 1.3, 1.8),
    ("CM",  "mid",  "interceptions"): (0.5, 0.9, 1.3),
    ("CM",  "high", "interceptions"): (0.3, 0.6, 1.0),

    ("FB",  "low",  "interceptions"): (0.8, 1.2, 1.8),
    ("FB",  "mid",  "interceptions"): (0.5, 0.8, 1.2),
    ("FB",  "high", "interceptions"): (0.3, 0.5, 0.8),
}


def get_positional_baseline(
    position: str,
    expected_poss: float,
    prop_type: str,
) -> dict | None:
    """
    Return the positional baseline for this role/context combination.
    Returns None when no baseline is defined (graceful pass-through).
    """
    group = _pos_group(position)
    if not group:
        return None
    tier = _poss_tier(expected_poss)
    result = _BASELINES.get((group, tier, prop_type))
    if result is None:
        return None
    p25, p50, p75 = result
    return {
        "posGroup":      group,
        "possessionTier": tier,
        "p25":           p25,
        "p50":           p50,
        "p75":           p75,
    }


def apply_positional_squeeze(
    posterior_mean: float,
    baseline: dict,
    n_samples: int,
) -> tuple[float, str]:
    """
    Squeeze the Bayesian posteriorMean back toward the realistic range
    when it falls outside the box-plot outlier threshold (1.5 × IQR beyond p25/p75).

    Squeeze weight (how hard we pull toward the range boundary):
      n >= 8  → 0.00  (full trust in player's own data — do not touch)
      n == 6  → 0.14
      n == 4  → 0.28
      n == 2  → 0.41
      n == 1  → 0.48
      n == 0  → 0.55

    Returns (adjusted_posterior, log_note).
    """
    if not baseline or posterior_mean is None:
        return posterior_mean, ""

    p25 = baseline["p25"]
    p75 = baseline["p75"]
    iqr = p75 - p25

    upper_outlier = p75 + 1.5 * iqr
    lower_outlier = max(0.0, p25 - 1.5 * iqr)

    # Enough real data → trust the player's own history completely
    if n_samples >= 8:
        return posterior_mean, ""

    squeeze_weight = round(0.55 * (1.0 - n_samples / 8.0), 3)

    if posterior_mean > upper_outlier:
        target   = p75
        adjusted = round(posterior_mean * (1 - squeeze_weight) + target * squeeze_weight, 2)
        pct      = round((posterior_mean - adjusted) / max(posterior_mean, 0.01) * 100, 1)
        note     = (f"[POS BASELINE] Ceiling squeeze "
                    f"({baseline['posGroup']}/{baseline['possessionTier']}, n={n_samples}): "
                    f"{posterior_mean:.1f}→{adjusted:.1f} (−{pct:.0f}%)")
        return adjusted, note

    if lower_outlier > 0 and posterior_mean < lower_outlier:
        target   = p25
        adjusted = round(posterior_mean * (1 - squeeze_weight) + target * squeeze_weight, 2)
        pct      = round((adjusted - posterior_mean) / max(adjusted, 0.01) * 100, 1)
        note     = (f"[POS BASELINE] Floor lift "
                    f"({baseline['posGroup']}/{baseline['possessionTier']}, n={n_samples}): "
                    f"{posterior_mean:.1f}→{adjusted:.1f} (+{pct:.0f}%)")
        return adjusted, note

    return posterior_mean, ""
