"""
Match Script classification — shown right after a player/match is identified,
BEFORE a stat line is entered. Answers: "what kind of game is this likely to be,
and is it a script worth trusting?"

Primary Script tiers are driven by the team's own moneyline (the most reliable,
always-available signal) cross-checked against an odds-derived expected
possession estimate (same formula the full /predict pipeline uses as its
"odds-only" fallback — see routes/predict.py compute_match_dominance).

We deliberately do NOT try to replicate the full historical-possession model
here (it's deeply embedded in the /predict pipeline and needs the player's
prop type to pick a squeeze multiplier). This card is a fast, pre-line signal;
the full prediction still runs its own richer model when the user hits Analyze.
"""

from typing import Optional

from utils import get_soccer_odds
from ai_engine import fetch_ai_press_intensity


# ── Primary Script tiers (moneyline of the analysed team; ordered favourite→underdog) ──
# min_ml/max_ml expressed the way American odds naturally sort: more negative = bigger favourite,
# more positive = bigger underdog. We store them as (lower_bound, upper_bound) on a single
# continuous number line where negative odds are converted to their "strength" for comparison.
_TIERS = [
    {
        "name": "Heavy Favorite Dominance",
        "isFavorable": True,
        "match": lambda ml: ml <= -300,
        "possRange": (62, 100),
        "description": "One team is expected to completely control the game.",
    },
    {
        "name": "Strong Favorite Control",
        "isFavorable": True,
        "match": lambda ml: -299 <= ml <= -200,
        "possRange": (57, 65),
        "description": "Clear favorite with consistent control of the game.",
    },
    {
        "name": "Moderate Favorite Control",
        "isFavorable": False,
        "match": lambda ml: -199 <= ml <= -130,
        "possRange": (53, 60),
        "description": "Favorite controls the game but the underdog has periods of possession.",
    },
    {
        "name": "Slight Favorite / Even",
        "isFavorable": False,
        "match": lambda ml: -129 <= ml <= 110,
        "possRange": (48, 55),
        "description": "Competitive and relatively balanced game.",
    },
    {
        "name": "Slight Underdog",
        "isFavorable": False,
        "match": lambda ml: 111 <= ml <= 149,
        "possRange": (45, 50),
        "description": "Underdog plays reactively and sits deeper.",
    },
    {
        "name": "Moderate Underdog",
        "isFavorable": False,
        "match": lambda ml: 150 <= ml <= 249,
        "possRange": (40, 47),
        "description": "Underdog absorbs pressure and plays on the counter.",
    },
    {
        "name": "Heavy Underdog",
        "isFavorable": False,
        "match": lambda ml: ml >= 250,
        "possRange": (0, 42),
        "description": "Underdog parks the bus and rarely has possession.",
    },
]


def _ml_to_prob(ml) -> float:
    """American moneyline -> implied win probability (0-1)."""
    try:
        ml = float(ml)
        return (-ml) / (-ml + 100.0) if ml < 0 else 100.0 / (ml + 100.0)
    except (TypeError, ValueError):
        return 0.5


def _estimate_expected_possession(team_prob: float, opp_prob: float) -> float:
    """Same odds-only possession slope used in routes/predict.py's compute_match_dominance
    fallback: 50% win-prob -> 50% poss, ~92% win-prob -> ~73% poss."""
    total = team_prob + opp_prob
    if total <= 0:
        return 50.0
    norm = team_prob / total
    return round(min(76.0, max(24.0, 50.0 + (norm - 0.5) * 50.0)), 1)


def _classify_tier(team_ml: float):
    for tier in _TIERS:
        if tier["match"](team_ml):
            return tier
    return _TIERS[3]  # Slight Favorite / Even as a safe middle default


def _tactical_modifier(press: Optional[dict], team_poss: float, is_favorite: bool) -> Optional[str]:
    """Derive a tactical modifier tag from the AI press-intensity read + possession estimate."""
    score = (press or {}).get("score")
    if score is not None:
        if score >= 0.75:
            return "High Press"
        if score >= 0.5:
            return "High Press"
        if score < 0.25 and not is_favorite:
            return "Low Block + Counter"
    if team_poss >= 60:
        return "Possession Dominant"
    if team_poss <= 40:
        return "Low Block + Counter"
    return None


def _expected_effects(tier_name: str, is_favorite: bool) -> list:
    """Canned, tier-scaled volume expectations mirroring the user-provided output format."""
    HIGH, MED, LOW = "High", "Medium", "Low"
    scale = {
        "Heavy Favorite Dominance":    (HIGH, HIGH, HIGH, HIGH),
        "Strong Favorite Control":     (HIGH, MED, MED, MED),
        "Moderate Favorite Control":   (MED, MED, MED, LOW),
        "Slight Favorite / Even":      (MED, MED, LOW, LOW),
        "Slight Underdog":             (LOW, LOW, MED, MED),
        "Moderate Underdog":           (LOW, LOW, MED, MED),
        "Heavy Underdog":              (LOW, LOW, HIGH, HIGH),
    }
    fav_mid, fav_atk, dog_gk, dog_def = scale.get(tier_name, (MED, MED, MED, MED))
    if is_favorite:
        return [
            f"This team's central midfielders: {fav_mid} passing volume",
            f"This team's wingers/strikers: {fav_atk} shooting volume",
            f"Opponent goalkeeper: {dog_gk} pass volume + shots faced",
            f"Opponent defenders: {dog_def} passing volume from resets under pressure",
        ]
    return [
        f"Opponent's central midfielders: {fav_mid} passing volume",
        f"Opponent's wingers/strikers: {fav_atk} shooting volume",
        f"This team's goalkeeper: {dog_gk} pass volume + shots faced",
        f"This team's defenders: {dog_def} passing volume from resets under pressure",
    ]


def _explanation(tier: dict, team_name: str, opp_name: str, team_ml: float, team_poss: float, is_favorite: bool) -> str:
    ml_str = f"{'+' if team_ml > 0 else ''}{int(team_ml)}"
    role = "favorite" if is_favorite else "underdog"
    return (
        f"{team_name} is priced as a {role} ({ml_str} moneyline) against {opp_name}, "
        f"with an estimated {team_poss:.0f}% expected possession. {tier['description']}"
    )


async def get_match_script(
    team_id: int,
    opponent_id: int,
    league_id: int,
    is_home: bool,
    team_name: str = "This team",
    opponent_name: str = "Opponent",
    league_name: str = "",
) -> dict:
    """Lightweight, pre-line classification of the match's expected script."""
    odds = await get_soccer_odds(team_id, opponent_id, league_id)

    home_ml = None
    away_ml = None
    if odds:
        try:
            home_ml = float(str(odds.get("homeOdds", "")).replace("+", ""))
            away_ml = float(str(odds.get("awayOdds", "")).replace("+", ""))
        except (TypeError, ValueError):
            home_ml = None
            away_ml = None

    if home_ml is None or away_ml is None:
        return {
            "available": False,
            "noCleanScript": True,
            "primaryScript": None,
            "isFavorable": False,
            "explanation": "No market data available yet for this fixture — script can't be classified confidently. Treat as a skip until odds are posted.",
            "tacticalModifier": None,
            "expectedEffects": [],
        }

    # odds are always keyed home/away on the fixture, not player's team — map to this team.
    team_ml = home_ml if is_home else away_ml
    opp_ml = away_ml if is_home else home_ml

    team_prob = _ml_to_prob(team_ml)
    opp_prob = _ml_to_prob(opp_ml)
    team_poss = _estimate_expected_possession(team_prob, opp_prob)

    # Moneyline is the primary/authoritative classifier (per the tier table).
    # expectedPossession is an odds-derived supporting estimate for context and
    # for the tactical-modifier read — NOT an independent signal to gate on,
    # since it's mathematically derived from the same odds and will naturally
    # run a bit hotter/colder than the hand-tuned possession ranges in the
    # tier table. Gating on that mismatch produced false "conflict" flags on
    # completely ordinary favorites during testing.
    tier = _classify_tier(team_ml)
    is_favorite = team_ml < 0

    # Only a genuine data-quality problem should suppress confidence: a
    # razor-thin market (near pick'em odds on both sides with a huge draw
    # price, or a fixture where the odds book looks obviously stale/broken).
    no_clean_script = abs(team_ml) < 100 and abs(opp_ml) < 100 and team_poss == 50.0

    press = None
    try:
        press = await fetch_ai_press_intensity(opponent_name, league_name)
    except Exception:
        press = None

    tactical_modifier = _tactical_modifier(press, team_poss, is_favorite)
    explanation = _explanation(tier, team_name, opponent_name, team_ml, team_poss, is_favorite)

    return {
        "available": True,
        "noCleanScript": no_clean_script,
        "primaryScript": tier["name"],
        "isFavorable": tier["isFavorable"] and not no_clean_script,
        "moneyline": int(team_ml),
        "expectedPossession": team_poss,
        "isFavoriteTeam": is_favorite,
        "explanation": explanation,
        "tacticalModifier": tactical_modifier,
        "expectedEffects": _expected_effects(tier["name"], is_favorite),
    }
