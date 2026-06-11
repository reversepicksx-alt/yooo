"""
Tests for all 6 prediction engine improvements:
  1. LUCK_STRIP_ENABLED env var is active
  2. NBA _opp_def_mult: rebounds/steals/blocks now opponent-adjusted
  3. NHL _venue_mult: goalie saves venue-split (away > home)
  4. WTA _surface_mult: player-specific surface path activates
  5. Bayesian streak window: extended to 10 games
  6. Gemini retry: 429 path exists in grok_engine
"""
import os, sys, asyncio, textwrap, inspect

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"

results = []

def check(name, condition, detail=""):
    status = PASS if condition else FAIL
    results.append(condition)
    print(f"  [{status}] {name}" + (f"  — {detail}" if detail else ""))
    return condition


# ──────────────────────────────────────────────────────────────────────────────
# 1. LUCK_STRIP_ENABLED env var
# ──────────────────────────────────────────────────────────────────────────────
print("\n── 1. LUCK_STRIP_ENABLED ─────────────────────────────────────────────")
check(
    "LUCK_STRIP_ENABLED=1 is set",
    os.environ.get("LUCK_STRIP_ENABLED") == "1",
    f"current value: {os.environ.get('LUCK_STRIP_ENABLED')!r}"
)

from sample_quality import filter_low_quality_samples
# A blowout garbage-time cameo (player played only 20 min, score was 5-0)
blowout_game = {"minutes": 20, "homeGoals": 5, "awayGoals": 0, "passes_total": 10, "venue": "away"}
normal_game  = {"minutes": 90, "homeGoals": 1, "awayGoals": 0, "passes_total": 50, "venue": "away"}
sample = [blowout_game] + [normal_game] * 9  # 10 games, 1 garbage-time blowout
filtered, reasons = filter_low_quality_samples(sample)
check(
    "filter_low_quality_samples strips blowout cameo",
    len(filtered) < len(sample),
    f"input={len(sample)}, output={len(filtered)}, reasons={reasons[:2]}"
)


# ──────────────────────────────────────────────────────────────────────────────
# 2. NBA _opp_def_mult: rebounds / steals / blocks
# ──────────────────────────────────────────────────────────────────────────────
print("\n── 2. NBA opponent defensive multiplier ─────────────────────────────")
sys.path.insert(0, os.path.dirname(__file__))
from nba_engine import _opp_def_mult

BAD_DEF  = 120.0   # well above 113 avg → weak defense
GOOD_DEF = 105.0   # below avg → strong defense

for prop, lo_mult, hi_mult in [
    ("rebounds", 0.91, 1.09),
    ("steals",   0.94, 1.06),
    ("blocks",   0.94, 1.06),
]:
    mult_bad  = _opp_def_mult(BAD_DEF,  prop)
    mult_good = _opp_def_mult(GOOD_DEF, prop)
    check(
        f"{prop}: bad defense ({BAD_DEF}) > 1.0",
        mult_bad > 1.0,
        f"mult={mult_bad:.4f}"
    )
    check(
        f"{prop}: good defense ({GOOD_DEF}) < 1.0",
        mult_good < 1.0,
        f"mult={mult_good:.4f}"
    )
    check(
        f"{prop}: capped within [{lo_mult}, {hi_mult}]",
        lo_mult <= mult_bad <= hi_mult and lo_mult <= mult_good <= hi_mult,
        f"bad={mult_bad:.4f} good={mult_good:.4f}"
    )

# Old behaviour: returns 1.0 when no rating
check(
    "rebounds/steals/blocks return 1.0 when opp_def_rating=None",
    _opp_def_mult(None, "rebounds") == 1.0 and
    _opp_def_mult(None, "steals")   == 1.0 and
    _opp_def_mult(None, "blocks")   == 1.0,
)


# ──────────────────────────────────────────────────────────────────────────────
# 3. NHL _venue_mult: goalie saves
# ──────────────────────────────────────────────────────────────────────────────
print("\n── 3. NHL goalie saves venue split ─────────────────────────────────")
from nhl_engine import _venue_mult

check(
    "saves: away > 1.0 (away goalie faces more shots)",
    _venue_mult("away", "saves") > 1.0,
    f"away={_venue_mult('away', 'saves'):.3f}"
)
check(
    "saves: home < 1.0 (home goalie faces fewer shots)",
    _venue_mult("home", "saves") < 1.0,
    f"home={_venue_mult('home', 'saves'):.3f}"
)
check(
    "saves: away=1.05, home=0.95",
    _venue_mult("away", "saves") == 1.05 and _venue_mult("home", "saves") == 0.95,
)
check(
    "goals_against follows same pattern",
    _venue_mult("away", "goals_against") == 1.05 and
    _venue_mult("home", "goals_against") == 0.95,
)
check(
    "save_pct stays neutral (rate stat, not volume)",
    _venue_mult("away", "save_pct") == 1.0 and
    _venue_mult("home", "save_pct") == 1.0,
)
check(
    "skater goals/assists still get home=1.04/away=0.96",
    _venue_mult("home", "goals") == 1.04 and
    _venue_mult("away", "goals") == 0.96,
)


# ──────────────────────────────────────────────────────────────────────────────
# 4. WTA _surface_mult: player-specific path
# ──────────────────────────────────────────────────────────────────────────────
print("\n── 4. WTA surface multiplier (player-specific) ──────────────────────")
from wta_engine import _surface_mult

# Simulate a clay specialist: scores 22 games per match on clay, only 18 on hard
clay_heavy = (
    [{"surface": "Clay",  "totalGames": 22}] * 5 +
    [{"surface": "Hard",  "totalGames": 18}] * 5
)
mult_clay = _surface_mult("Clay", clay_heavy, "total_games", "totalGames")
mult_hard = _surface_mult("Hard", clay_heavy, "total_games", "totalGames")

check(
    "clay specialist on clay: mult > 1.0",
    mult_clay > 1.0,
    f"mult={mult_clay:.4f} (expected ~{22/18:.3f})"
)
check(
    "clay specialist on hard: mult < 1.0",
    mult_hard < 1.0,
    f"mult={mult_hard:.4f} (expected ~{18/22:.3f})"
)
check(
    "player-specific mult capped at 1.15 max",
    mult_clay <= 1.15,
    f"mult={mult_clay:.4f}"
)

# Fallback: no surface tags in logs → should return 1.0 or tour-average
no_surface_logs = [{"totalGames": 21}] * 8
mult_fallback = _surface_mult("Clay", no_surface_logs, "total_games", "totalGames")
check(
    "fallback to global baseline when no surface tags",
    0.88 <= mult_fallback <= 1.12,
    f"fallback mult={mult_fallback:.4f}"
)

# No field provided: falls straight through to global baseline
mult_no_field = _surface_mult("Clay", clay_heavy, "total_games", "")
check(
    "no field arg: uses global baseline (not player-specific)",
    mult_no_field != mult_clay,
    f"no_field={mult_no_field:.4f} vs player_specific={mult_clay:.4f}"
)


# ──────────────────────────────────────────────────────────────────────────────
# 5. Bayesian streak window: 10 games
# ──────────────────────────────────────────────────────────────────────────────
print("\n── 5. Bayesian streak window (10 games) ─────────────────────────────")

# Import the full Bayesian engine to test streak via compute_bayesian_projection
# We'll test the streak logic directly by calling the internal function
import importlib
import bayesian_engine as be

# compute_bayesian_projection reads g["targetStat"] by default (stat_field="targetStat").
# Set targetStat=60 so per-90 normalised value = 60 (with minutes=90 → v*90/90 = 60).
line = 50.0
def _make_log(stat_val):
    return {
        "targetStat": stat_val, "minutes": 90,
        "homeGoals": 1, "awayGoals": 0, "venue": "home", "opponent": "Test FC",
        "goals": 0, "shots": 2, "assists": 0, "saves": 0,
        "yellow_cards": 0, "crosses": 0, "tackles": 0, "key_passes": 1,
    }

logs_10_over = [_make_log(60) for _ in range(10)]  # all 60 > line 50

result_10 = be.compute_bayesian_projection(
    game_logs=logs_10_over,
    prop_type="pass_attempts",
    line=line,
    venue="home",
    position="CM",
    league_id=39,
)
streak = result_10.get("streakFlag", "NONE")
check(
    "10 OVER games produces OVER_10 streak flag",
    streak == "OVER_10",
    f"streakFlag={streak!r}"
)

# 5 OVER games should give OVER_5
logs_5_over = [_make_log(60) for _ in range(5)]
result_5 = be.compute_bayesian_projection(
    game_logs=logs_5_over,
    prop_type="pass_attempts",
    line=line,
    venue="home",
    position="CM",
    league_id=39,
)
streak_5 = result_5.get("streakFlag", "NONE")
check(
    "5 OVER games produces OVER_5 streak flag",
    streak_5 == "OVER_5",
    f"streakFlag={streak_5!r}"
)

# Alternating 10 games (OVER/UNDER every other game) → no clean streak at any window.
# Logs are newest-first, so recent_5=[60,40,60,40,60] and recent_3=[60,40,60] —
# neither is a clean sweep, so no streak should fire.
alt_vals = [60, 40, 60, 40, 60, 40, 60, 40, 60, 40]  # alternating, newest first
logs_mixed = [_make_log(v) for v in alt_vals]
result_mixed = be.compute_bayesian_projection(
    game_logs=logs_mixed,
    prop_type="pass_attempts",
    line=line,
    venue="home",
    position="CM",
    league_id=39,
)
streak_mixed = result_mixed.get("streakFlag", "NONE")
check(
    "Alternating OVER/UNDER 10 games → no clean streak",
    streak_mixed == "NONE",
    f"streakFlag={streak_mixed!r}"
)


# ──────────────────────────────────────────────────────────────────────────────
# 6. Gemini retry: 429 path in grok_engine source
# ──────────────────────────────────────────────────────────────────────────────
print("\n── 6. Gemini retry on 429 ───────────────────────────────────────────")
import grok_engine

src_gemini        = inspect.getsource(grok_engine._gemini_call)
src_gemini_search = inspect.getsource(grok_engine._gemini_search_call)

check(
    "_gemini_call: retry loop present (range(3))",
    "range(3)" in src_gemini,
)
check(
    "_gemini_call: 429 status handled",
    "429" in src_gemini,
)
check(
    "_gemini_call: exponential backoff with asyncio.sleep",
    "asyncio.sleep" in src_gemini and "2 **" in src_gemini,
)
check(
    "_gemini_search_call: retry loop present",
    "range(3)" in src_gemini_search,
)
check(
    "_gemini_search_call: 429 handled with backoff",
    "429" in src_gemini_search and "asyncio.sleep" in src_gemini_search,
)


# ──────────────────────────────────────────────────────────────────────────────
# Summary
# ──────────────────────────────────────────────────────────────────────────────
print("\n" + "─" * 60)
passed = sum(results)
total  = len(results)
pct    = passed / total * 100
print(f"  Result: {passed}/{total} tests passed ({pct:.0f}%)")
if passed == total:
    print("  \033[92mAll checks GREEN — improvements verified.\033[0m")
else:
    print("  \033[91mSome checks FAILED — see above.\033[0m")
    sys.exit(1)
