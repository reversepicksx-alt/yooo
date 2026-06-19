"""
Positional Role Baseline — v2
Three-layer reality check on Bayesian projections:

  Layer 1 — Role-aware lookup
    CDM/Deep-Lying Playmaker ≠ CDM/Ball-Winner. Inverted Winger ≠ Traditional Winger.
    Role is already resolved by AI for every player. We use it.

  Layer 2 — Team pass-rate multiplier
    Possession % alone is misleading. Barcelona (55% poss, 720 passes/game) and
    Newcastle (52% poss, 480 passes/game) are in the same possession tier but
    their CDMs live in completely different worlds. We scale baselines up/down
    based on the team's actual average pass count per game.

  Layer 3 — Opponent press compression
    A High-press opponent shrinks the realistic range for pass-heavy props.
    When press intensity is High/Very High, p75 drops and p25 rises —
    even great playmakers produce fewer passes under relentless pressing.

Lookup priority:
  1. (pos_group, role_variant, poss_tier, prop_type)  →  role-specific row
  2. (pos_group, poss_tier, prop_type)                →  generic fallback

Squeeze fires only when posteriorMean is beyond 1.5 × IQR outside p25/p75.
Squeeze weight fades to 0 at 8+ game logs (trust player's own data fully).
"""

from __future__ import annotations

# ── Position group ────────────────────────────────────────────────────────────
_POSITION_MAP: dict[str, str] = {
    "G": "GK", "GK": "GK", "GOALKEEPER": "GK",
    "CB": "CB", "DC": "CB", "CENTREBACK": "CB", "CENTRE-BACK": "CB",
    "FB": "FB", "LB": "FB", "RB": "FB", "LWB": "FB", "RWB": "FB", "WB": "FB",
    "FULLBACK": "FB", "WINGBACK": "FB", "LEFT BACK": "FB", "RIGHT BACK": "FB",
    "LEFT-BACK": "FB", "RIGHT-BACK": "FB",
    "CDM": "CDM", "DM": "CDM", "DMF": "CDM", "DLP": "CDM",
    "BALL-WINNER": "CDM", "DEFENSIVE MIDFIELDER": "CDM", "DEFENSIVE MID": "CDM",
    "CM": "CM", "MF": "CM", "BOX-TO-BOX": "CM", "MEZZALA": "CM",
    "CENTRAL MIDFIELDER": "CM", "MIDFIELDER": "CM", "CENTRAL MID": "CM",
    "CAM": "CAM", "AM": "CAM", "OMF": "CAM", "SS": "CAM",
    "ATTACKING MIDFIELDER": "CAM", "SHADOW STRIKER": "CAM",
    "ATTACKING MID": "CAM", "NO. 10": "CAM",
    "W": "W", "LW": "W", "RW": "W", "LM": "W", "RM": "W",
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


# ── Role variant classifier ────────────────────────────────────────────────────
# Maps (position_group, role_string) → "high" | "standard" | "low"
# "high" = high-volume variant (DLP, Attacking FB, Inverted Winger, Pressing ST, Ball-Playing CB)
# "low"  = low-volume variant  (Ball-Winner, Stopper, Defensive FB, Poacher)
# "standard" = generic fallback

def _role_variant(pos_group: str, role: str) -> str:
    if not role:
        return "standard"
    r = role.lower().strip()

    if pos_group == "CDM":
        if any(k in r for k in [
            "deep-lying", "deep lying", "dlp", "regista", "playmaker",
            "tempo", "metronome", "playmaking", "orchestrat",
        ]):
            return "high"
        if any(k in r for k in [
            "ball-winner", "ball winner", "destroyer", "anchor",
            "holding", "screen", "defensive", "warrior", "workhorse",
        ]):
            return "low"
        return "standard"

    if pos_group == "CM":
        if any(k in r for k in ["mezzala", "roaming", "advanced"]):
            return "high"
        if any(k in r for k in [
            "deep-lying", "deep lying", "dlp", "regista", "holding",
        ]):
            return "low"
        return "standard"

    if pos_group == "CAM":
        if any(k in r for k in [
            "trequartista", "enganche", "playmaker", "no.10",
            "number 10", "classic", "creator",
        ]):
            return "high"
        if any(k in r for k in ["shadow striker", "second striker", "false 9", "false nine"]):
            return "low"
        return "standard"

    if pos_group == "CB":
        if any(k in r for k in [
            "ball-playing", "ball playing", "sweeper", "libero",
            "modern", "progressive",
        ]):
            return "high"
        if any(k in r for k in ["stopper", "no-nonsense", "aerial", "traditional"]):
            return "low"
        return "standard"

    if pos_group == "FB":
        if any(k in r for k in [
            "attacking", "overlapping", "wingback", "wing-back",
            "inverted wingback", "advanced",
        ]):
            return "high"
        if any(k in r for k in ["defensive", "holding", "traditional"]):
            return "low"
        return "standard"

    if pos_group == "W":
        if any(k in r for k in ["inverted", "inside forward", "inside-forward"]):
            return "high"     # high shots, cuts inside
        if any(k in r for k in ["wide midfielder", "traditional", "crossing", "orthodox"]):
            return "low"      # high crosses, standard shots
        return "standard"

    if pos_group == "ST":
        if any(k in r for k in [
            "pressing", "false", "dropping", "link-up", "complete",
            "target", "hold-up",
        ]):
            return "high"     # more involvement / passes
        if any(k in r for k in ["poacher", "penalty box", "pure striker", "finisher"]):
            return "low"      # fewer passes, more shots
        return "standard"

    return "standard"


# ── Team pass-rate multiplier ─────────────────────────────────────────────────
def _team_pass_multiplier(team_avg_passes: float | None) -> float:
    """
    Scale baselines by how many passes the team actually plays per game.
    Possession % alone doesn't distinguish tiki-taka from long-ball.
      ≥ 620 passes/game → × 1.15 (high-volume passing system)
      480–620           → × 1.00 (standard)
      < 480             → × 0.83 (direct / low-pass system)
    """
    if team_avg_passes is None:
        return 1.0
    if team_avg_passes >= 620:
        return 1.15
    if team_avg_passes >= 480:
        return 1.0
    return 0.83


# ── Press compression ─────────────────────────────────────────────────────────
def _press_compression(press_label: str | None) -> tuple[float, float]:
    """
    When facing a high-press opponent the realistic range compresses:
    both ceiling (p75) drops and floor (p25) rises slightly.
    Returns (p75_mult, p25_mult).
      Very High → p75 × 0.84, p25 × 1.10
      High      → p75 × 0.91, p25 × 1.05
      otherwise → no change
    """
    if not press_label:
        return 1.0, 1.0
    pl = press_label.lower()
    if "very high" in pl or "very_high" in pl:
        return 0.84, 1.10
    if pl == "high":
        return 0.91, 1.05
    return 1.0, 1.0


# ── Role-specific baseline table ───────────────────────────────────────────────
# (pos_group, role_variant, poss_tier, prop_type) → (p25, p50, p75)
# Only overrides where the role meaningfully changes the output range.
# Everything else falls through to the generic _BASELINES table.

_ROLE_BASELINES: dict[tuple, tuple] = {

    # ── CDM / PASS ATTEMPTS ───────────────────────────────────────────────────
    # Deep-Lying Playmaker (DLP / regista): ball always comes back to them
    ("CDM", "high", "high", "pass_attempts"): (80, 92, 108),
    ("CDM", "high", "mid",  "pass_attempts"): (65, 78,  94),
    ("CDM", "high", "low",  "pass_attempts"): (50, 62,  78),
    # Ball-Winner: wins the ball and pings it forward, not a volume passer
    ("CDM", "low",  "high", "pass_attempts"): (46, 58,  72),
    ("CDM", "low",  "mid",  "pass_attempts"): (34, 46,  58),
    ("CDM", "low",  "low",  "pass_attempts"): (24, 36,  48),

    # ── CM / PASS ATTEMPTS ────────────────────────────────────────────────────
    # Mezzala / Roaming: covers huge ground, touches ball frequently
    ("CM",  "high", "high", "pass_attempts"): (65, 78,  94),
    ("CM",  "high", "mid",  "pass_attempts"): (52, 64,  78),
    ("CM",  "high", "low",  "pass_attempts"): (38, 50,  62),
    # Deep-lying CM (holding role within CM slot)
    ("CM",  "low",  "high", "pass_attempts"): (52, 65,  80),
    ("CM",  "low",  "mid",  "pass_attempts"): (40, 52,  65),
    ("CM",  "low",  "low",  "pass_attempts"): (28, 40,  52),

    # ── CAM / PASS ATTEMPTS ───────────────────────────────────────────────────
    # Classic No.10 / Trequartista: creative hub, high touch count
    ("CAM", "high", "high", "pass_attempts"): (52, 65,  80),
    ("CAM", "high", "mid",  "pass_attempts"): (40, 52,  66),
    ("CAM", "high", "low",  "pass_attempts"): (28, 40,  54),
    # Shadow Striker: more runs in behind, fewer touches
    ("CAM", "low",  "high", "pass_attempts"): (32, 42,  54),
    ("CAM", "low",  "mid",  "pass_attempts"): (24, 34,  44),
    ("CAM", "low",  "low",  "pass_attempts"): (18, 26,  36),

    # ── CB / PASS ATTEMPTS ────────────────────────────────────────────────────
    # Ball-Playing CB: builds from deep, highest touch count among defenders
    ("CB",  "high", "high", "pass_attempts"): (72, 88, 108),
    ("CB",  "high", "mid",  "pass_attempts"): (55, 70,  88),
    ("CB",  "high", "low",  "pass_attempts"): (38, 52,  68),
    # Stopper: wins headers, clears it, not a passing outlet
    ("CB",  "low",  "high", "pass_attempts"): (45, 58,  72),
    ("CB",  "low",  "mid",  "pass_attempts"): (34, 46,  58),
    ("CB",  "low",  "low",  "pass_attempts"): (24, 34,  46),

    # ── FB / PASS ATTEMPTS ────────────────────────────────────────────────────
    # Attacking / Overlapping Fullback: high involvement, many overlapping runs
    ("FB",  "high", "high", "pass_attempts"): (60, 74,  90),
    ("FB",  "high", "mid",  "pass_attempts"): (48, 62,  76),
    ("FB",  "high", "low",  "pass_attempts"): (34, 48,  62),
    # Defensive Fullback: stays deep, limited forward involvement
    ("FB",  "low",  "high", "pass_attempts"): (40, 52,  64),
    ("FB",  "low",  "mid",  "pass_attempts"): (30, 42,  54),
    ("FB",  "low",  "low",  "pass_attempts"): (22, 32,  44),

    # ── ST / PASS ATTEMPTS ────────────────────────────────────────────────────
    # Pressing / Complete / Target / Link-Up: drops deep, combines, links play
    ("ST",  "high", "high", "pass_attempts"): (30, 42,  56),
    ("ST",  "high", "mid",  "pass_attempts"): (24, 34,  46),
    ("ST",  "high", "low",  "pass_attempts"): (18, 28,  38),
    # Poacher: lives in the box, minimal pass involvement
    ("ST",  "low",  "high", "pass_attempts"): (14, 20,  28),
    ("ST",  "low",  "mid",  "pass_attempts"): (10, 16,  22),
    ("ST",  "low",  "low",  "pass_attempts"): ( 8, 12,  18),

    # ── ST / SHOTS ────────────────────────────────────────────────────────────
    # Poacher: entire role is to finish — highest shots per touch
    ("ST",  "low",  "high", "shots"): (4.0, 5.5, 7.0),
    ("ST",  "low",  "mid",  "shots"): (3.0, 4.2, 5.5),
    ("ST",  "low",  "low",  "shots"): (2.0, 3.0, 4.0),
    # Pressing / Link-up: more involved but fewer pure shots
    ("ST",  "high", "high", "shots"): (2.0, 3.0, 4.2),
    ("ST",  "high", "mid",  "shots"): (1.5, 2.4, 3.4),
    ("ST",  "high", "low",  "shots"): (1.0, 1.8, 2.6),

    # ── ST / SHOTS ON TARGET ──────────────────────────────────────────────────
    ("ST",  "low",  "high", "shots_on_target"): (1.6, 2.4, 3.2),
    ("ST",  "low",  "mid",  "shots_on_target"): (1.2, 1.8, 2.5),
    ("ST",  "low",  "low",  "shots_on_target"): (0.8, 1.2, 1.8),
    ("ST",  "high", "high", "shots_on_target"): (0.8, 1.2, 1.8),
    ("ST",  "high", "mid",  "shots_on_target"): (0.6, 1.0, 1.5),
    ("ST",  "high", "low",  "shots_on_target"): (0.4, 0.7, 1.2),

    # ── W / SHOTS — Inverted winger (cuts inside, shoot more) ────────────────
    ("W",   "high", "high", "shots"): (2.5, 3.5, 5.0),
    ("W",   "high", "mid",  "shots"): (2.0, 2.8, 4.0),
    ("W",   "high", "low",  "shots"): (1.3, 2.0, 3.0),
    # Traditional winger (cross-first, fewer shots)
    ("W",   "low",  "high", "shots"): (1.5, 2.2, 3.2),
    ("W",   "low",  "mid",  "shots"): (1.2, 1.8, 2.6),
    ("W",   "low",  "low",  "shots"): (0.7, 1.2, 1.8),

    # ── W / SHOTS ON TARGET ───────────────────────────────────────────────────
    ("W",   "high", "high", "shots_on_target"): (1.0, 1.5, 2.2),
    ("W",   "high", "mid",  "shots_on_target"): (0.8, 1.2, 1.8),
    ("W",   "high", "low",  "shots_on_target"): (0.5, 0.9, 1.4),
    ("W",   "low",  "high", "shots_on_target"): (0.6, 1.0, 1.5),
    ("W",   "low",  "mid",  "shots_on_target"): (0.5, 0.8, 1.2),
    ("W",   "low",  "low",  "shots_on_target"): (0.3, 0.6, 0.9),

    # ── W / CROSSES — Traditional winger (cross-heavy) ────────────────────────
    ("W",   "low",  "high", "crosses"): (3.5, 5.0, 7.0),
    ("W",   "low",  "mid",  "crosses"): (2.5, 3.8, 5.5),
    ("W",   "low",  "low",  "crosses"): (1.3, 2.2, 3.5),
    # Inverted winger (cuts inside → far fewer crosses)
    ("W",   "high", "high", "crosses"): (0.5, 1.0, 1.8),
    ("W",   "high", "mid",  "crosses"): (0.3, 0.7, 1.3),
    ("W",   "high", "low",  "crosses"): (0.2, 0.5, 1.0),

    # ── FB / CROSSES — Attacking fullback (overlapping, wing dominant) ────────
    ("FB",  "high", "high", "crosses"): (3.2, 4.8, 6.5),
    ("FB",  "high", "mid",  "crosses"): (2.0, 3.2, 4.5),
    ("FB",  "high", "low",  "crosses"): (1.2, 2.0, 3.2),
    # Defensive fullback (stays deep, rarely crosses)
    ("FB",  "low",  "high", "crosses"): (0.5, 1.0, 1.8),
    ("FB",  "low",  "mid",  "crosses"): (0.3, 0.7, 1.3),
    ("FB",  "low",  "low",  "crosses"): (0.2, 0.5, 1.0),

    # ── CAM / KEY PASSES — Classic No.10 / creator ────────────────────────────
    ("CAM", "high", "high", "key_passes"): (2.5, 3.5, 4.8),
    ("CAM", "high", "mid",  "key_passes"): (1.8, 2.6, 3.6),
    ("CAM", "high", "low",  "key_passes"): (1.2, 1.8, 2.6),
    # Shadow striker (fewer key passes — more about finishing)
    ("CAM", "low",  "high", "key_passes"): (0.8, 1.3, 1.8),
    ("CAM", "low",  "mid",  "key_passes"): (0.5, 0.9, 1.3),
    ("CAM", "low",  "low",  "key_passes"): (0.3, 0.6, 0.9),

    # ── CM / KEY PASSES — Mezzala / roaming (creative range) ─────────────────
    ("CM",  "high", "high", "key_passes"): (1.5, 2.2, 3.0),
    ("CM",  "high", "mid",  "key_passes"): (1.0, 1.6, 2.2),
    ("CM",  "high", "low",  "key_passes"): (0.6, 1.0, 1.5),

    # ── W / KEY PASSES — Inverted winger (dangerous cutting inside) ───────────
    ("W",   "high", "high", "key_passes"): (1.8, 2.5, 3.5),
    ("W",   "high", "mid",  "key_passes"): (1.2, 1.8, 2.5),
    ("W",   "high", "low",  "key_passes"): (0.8, 1.2, 1.8),
}


# ── Generic fallback baseline table ───────────────────────────────────────────
# (pos_group, poss_tier, prop_type) → (p25, p50, p75)
# Used when no role-specific row matches.

_BASELINES: dict[tuple, tuple] = {

    # ── PASS ATTEMPTS ─────────────────────────────────────────────────────────
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
    ("GK",  "high", "pass_attempts"): (24, 32,  42),   # inverted: less poss → more back-passes
    ("GK",  "mid",  "pass_attempts"): (28, 36,  46),
    ("GK",  "low",  "pass_attempts"): (32, 42,  54),
    ("W",   "high", "pass_attempts"): (36, 46,  58),
    ("W",   "mid",  "pass_attempts"): (28, 38,  48),
    ("W",   "low",  "pass_attempts"): (20, 30,  40),
    ("ST",  "high", "pass_attempts"): (22, 32,  44),
    ("ST",  "mid",  "pass_attempts"): (18, 26,  36),
    ("ST",  "low",  "pass_attempts"): (14, 22,  30),

    # ── SAVES (GK — inverted possession) ──────────────────────────────────────
    ("GK",  "low",  "saves"): (2.5, 3.5, 5.0),
    ("GK",  "mid",  "saves"): (1.5, 2.5, 3.5),
    ("GK",  "high", "saves"): (0.5, 1.5, 2.5),

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

    # ── TACKLES (inverted possession) ─────────────────────────────────────────
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

    # ── CLEARANCES (strongly inverted) ────────────────────────────────────────
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

    # ── INTERCEPTIONS (inverted possession) ───────────────────────────────────
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


# ── Public API ────────────────────────────────────────────────────────────────

def get_positional_baseline(
    position: str,
    expected_poss: float,
    prop_type: str,
    role: str = "",
    team_avg_passes: float | None = None,
    press_intensity_label: str | None = None,
) -> dict | None:
    """
    Return the positional baseline for this player/context combination.

    Lookup priority:
      1. Role-specific row (_ROLE_BASELINES)
      2. Generic fallback row (_BASELINES)
      3. None — no adjustment possible

    Team pass-rate multiplier and press compression are applied on top
    of whichever row is found.
    """
    group = _pos_group(position)
    if not group:
        return None
    tier  = _poss_tier(expected_poss)

    # --- Role-specific lookup ---
    variant = _role_variant(group, role)
    result  = _ROLE_BASELINES.get((group, variant, tier, prop_type))
    used_role_variant = variant if result else "standard"

    # --- Generic fallback ---
    if result is None:
        result = _BASELINES.get((group, tier, prop_type))
    if result is None:
        return None

    p25_raw, p50_raw, p75_raw = result

    # --- Layer 2: team pass-rate multiplier (pass-relevant props only) ---
    pass_props = {
        "pass_attempts", "passes", "key_passes", "crosses",
        "dribbles", "shots", "shots_on_target",
    }
    team_mult = _team_pass_multiplier(team_avg_passes) if prop_type in pass_props else 1.0
    p25 = round(p25_raw * team_mult, 2)
    p50 = round(p50_raw * team_mult, 2)
    p75 = round(p75_raw * team_mult, 2)

    # --- Layer 3: press compression (pass-heavy props only) ---
    press_props = {"pass_attempts", "passes", "key_passes", "dribbles"}
    p75_mult, p25_mult = (1.0, 1.0)
    if prop_type in press_props:
        p75_mult, p25_mult = _press_compression(press_intensity_label)
    p75 = round(p75 * p75_mult, 2)
    p25 = round(p25 * p25_mult, 2)

    return {
        "posGroup":          group,
        "roleVariant":       used_role_variant,
        "possessionTier":    tier,
        "teamPassMult":      round(team_mult, 3),
        "pressLabel":        press_intensity_label or "unknown",
        "p25":               p25,
        "p50":               p50,
        "p75":               p75,
    }


def apply_positional_squeeze(
    posterior_mean: float,
    baseline: dict,
    n_samples: int,
) -> tuple[float, str]:
    """
    Squeeze the Bayesian posteriorMean toward the realistic range boundary
    when it falls beyond the box-plot outlier threshold (1.5 × IQR outside p25/p75).

    Squeeze weight by sample count:
      n >= 8  → 0.00  (player's own data is authoritative)
      n == 6  → 0.14
      n == 4  → 0.28
      n == 2  → 0.41
      n == 1  → 0.48
      n == 0  → 0.55
    """
    if not baseline or posterior_mean is None:
        return posterior_mean, ""

    p25 = baseline["p25"]
    p75 = baseline["p75"]
    iqr = p75 - p25
    if iqr <= 0:
        return posterior_mean, ""

    upper_outlier = p75 + 1.5 * iqr
    lower_outlier = max(0.0, p25 - 1.5 * iqr)

    if n_samples >= 8:
        return posterior_mean, ""

    squeeze_weight = round(0.55 * (1.0 - n_samples / 8.0), 3)
    role_str = f"{baseline.get('posGroup','')}/{baseline.get('roleVariant','')}"
    tier_str = f"{baseline.get('possessionTier','')} poss"
    mult_str = f"×{baseline.get('teamPassMult',1.0):.2f} team-pass-rate"
    press_str = (f" | {baseline.get('pressLabel','')} press" 
                 if baseline.get('pressLabel') not in (None, "unknown") else "")

    if posterior_mean > upper_outlier:
        target   = p75
        adjusted = round(posterior_mean * (1 - squeeze_weight) + target * squeeze_weight, 2)
        pct      = round((posterior_mean - adjusted) / max(posterior_mean, 0.01) * 100, 1)
        note = (
            f"[POS BASELINE] Ceiling squeeze "
            f"({role_str}, {tier_str}, {mult_str}{press_str}, n={n_samples}): "
            f"{posterior_mean:.1f}→{adjusted:.1f} (−{pct:.0f}%)"
        )
        return adjusted, note

    if lower_outlier > 0 and posterior_mean < lower_outlier:
        target   = p25
        adjusted = round(posterior_mean * (1 - squeeze_weight) + target * squeeze_weight, 2)
        pct      = round((adjusted - posterior_mean) / max(adjusted, 0.01) * 100, 1)
        note = (
            f"[POS BASELINE] Floor lift "
            f"({role_str}, {tier_str}, {mult_str}{press_str}, n={n_samples}): "
            f"{posterior_mean:.1f}→{adjusted:.1f} (+{pct:.0f}%)"
        )
        return adjusted, note

    return posterior_mean, ""
