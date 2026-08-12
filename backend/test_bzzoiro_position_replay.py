"""Focused unit tests for validate_bzzoiro_position_replay.

Covers:
1. No live-mode refined picks → CAUTION (cannot GO without liveRefinedN >= 30)
2. Sufficient live picks with good hit rate → GO
3. Top-league skew confirmed → stricter hit-rate threshold (+2 pp)
4. Top-league skew NOT confirmed → standard threshold (-2 pp)
5. GO verdict requires liveRefinedN >= 30, not just bzzoiro_valid cohort size
"""
from __future__ import annotations

import pytest
from model_metrics import validate_bzzoiro_position_replay

# ── Fixture helpers ────────────────────────────────────────────────────────────

def _bzz_valid_row(
    result: str,
    pos_source: str = "bzzoiro_shadow_confirmed_lineup",
    league_id: int = 39,
    projected: float = 3.0,
    actual: float = 4.0,
    line: float = 3.5,
    recommendation: str = "over",
) -> dict:
    """Return a minimal settled pick row with valid Bzzoiro position data."""
    return {
        "sport": "soccer",
        "status": "settled",
        "result": result,
        "recommendation": recommendation,
        "fixtureId": "f1",
        "fixtureDate": "2026-01-01",
        "playerId": "p1",
        "playerName": "Test Player",
        "teamId": "t1",
        "opponentId": "t2",
        "propType": "passes",
        "line": line,
        "projectedValue": projected,
        "actualValue": actual,
        "confidenceScore": 65.0,
        "settledAt": "2026-01-02T00:00:00Z",
        "leagueId": league_id,
        "tacticalContext": {
            "bzzoiroEnrichment": {
                "available": True,
                "positionValidation": {
                    "valid": True,
                    "lineupValid": True,
                    "fixtureDateMatch": "exact",
                },
            },
            "player": {
                "positionSource": pos_source,
            },
        },
    }


def _live_refined_row(result: str, **kwargs) -> dict:
    """Shorthand for a live-mode refined pick."""
    kwargs.setdefault("pos_source", "bzzoiro_live_confirmed_lineup")
    return _bzz_valid_row(result=result, **kwargs)


def _absent_row(result: str, league_id: int = 88) -> dict:
    """Return a minimal settled pick row without Bzzoiro position data."""
    return {
        "sport": "soccer",
        "status": "settled",
        "result": result,
        "recommendation": "over",
        "fixtureId": "f2",
        "fixtureDate": "2026-01-01",
        "playerId": "p2",
        "playerName": "Other Player",
        "teamId": "t3",
        "opponentId": "t4",
        "propType": "passes",
        "line": 3.5,
        "projectedValue": 3.2,
        "actualValue": 4.1,
        "confidenceScore": 62.0,
        "settledAt": "2026-01-02T00:00:00Z",
        "leagueId": league_id,
        "tacticalContext": {},
    }


def _make_unique(rows: list[dict]) -> list[dict]:
    """Give each row a unique fixtureId + playerName combo to avoid deduplication."""
    for i, row in enumerate(rows):
        row["fixtureId"] = f"fix_{i}"
        row["playerName"] = f"Player {i}"
        row["playerId"] = f"pid_{i}"
    return rows


# ── Test 1: Zero live-mode picks → CAUTION ────────────────────────────────────

def test_zero_live_picks_gives_caution():
    """GO is impossible when no picks have gone through BZZOIRO_POSITION_LIVE=live."""
    # 40 bzzoiro-valid picks (shadow mode only) + 20 absent — plenty of broader data
    rows = _make_unique(
        [_bzz_valid_row("hit") for _ in range(30)]
        + [_bzz_valid_row("miss") for _ in range(10)]
        + [_absent_row("hit") for _ in range(10)]
        + [_absent_row("miss") for _ in range(10)]
    )
    result = validate_bzzoiro_position_replay(rows)

    assert result["liveRefinedN"] == 0
    pd = result["promotionDecision"]
    assert pd["verdict"] == "CAUTION", (
        f"Expected CAUTION when liveRefinedN=0, got {pd['verdict']}: {pd['summary']}"
    )
    # Confirm the blocking criterion is the live_sample_size check
    live_gate = next(
        (c for c in pd["criteria"] if c["check"] == "live_sample_size"), None
    )
    assert live_gate is not None
    assert live_gate["result"] == "insufficient_data"
    assert live_gate["liveRefinedN"] == 0


# ── Test 2: 30+ live picks with a good hit rate → GO ─────────────────────────

def test_sufficient_live_picks_good_hit_rate_gives_go():
    """With ≥ 30 live-refined picks and hit rate clearly above baseline, verdict is GO."""
    # 35 live-refined hits (100% hit rate) vs 20 absent picks at 50%
    rows = _make_unique(
        [_live_refined_row("hit", league_id=88) for _ in range(35)]
        + [_absent_row("hit", league_id=88) for _ in range(10)]
        + [_absent_row("miss", league_id=88) for _ in range(10)]
    )
    result = validate_bzzoiro_position_replay(rows)

    assert result["liveRefinedN"] == 35
    pd = result["promotionDecision"]
    assert pd["verdict"] == "GO", (
        f"Expected GO with 35 live picks at 100% hit rate, got {pd['verdict']}: {pd['summary']}"
    )


# ── Test 3: Top-league skew confirmed → stricter threshold blocks a borderline case

def test_top_league_skew_tightens_threshold():
    """When skew is confirmed, the hit-rate criterion requires +2 pp (not just -2 pp)."""
    # Build a scenario where:
    #   - live group: 30 picks, 60% hit rate, all EPL (league 39) → 100% top-league
    #   - absent group: 30 picks, 59% hit rate, all Eredivisie (league 88) → 0% top-league
    # Without skew: gap = +1 pp → passes the -2 pp standard threshold
    # With skew:    gap = +1 pp → fails the +2 pp tightened threshold
    live_hits = 18   # 18/30 = 60.0%
    live_misses = 12
    absent_hits = 18  # 18/30 + rounding ≈ 59%… use 17/29 for a clean sub-threshold gap
    absent_misses = 13

    rows = _make_unique(
        [_live_refined_row("hit", league_id=39) for _ in range(live_hits)]
        + [_live_refined_row("miss", league_id=39) for _ in range(live_misses)]
        + [_absent_row("hit", league_id=88) for _ in range(absent_hits)]
        + [_absent_row("miss", league_id=88) for _ in range(absent_misses)]
    )
    result = validate_bzzoiro_position_replay(rows)

    skew = result["topLeagueSkew"]
    # Should detect skew: live=100% top-league, absent=0%, gap=100pp
    assert skew["detected"], (
        f"Expected top-league skew to be detected; skew info: {skew}"
    )
    assert skew["hitRateThresholdAdjusted"] is True
    assert skew["hitRateThreshold"] == 2.0

    pd = result["promotionDecision"]
    # The hit-rate gap is ~+1 pp — passes at -2 pp but fails at +2 pp
    live_hr = result["bzzoiroLiveRefined"]["hitRate"]
    absent_hr = result["bzzoiroAbsent"]["hitRate"]
    gap = live_hr - absent_hr
    assert gap < 2.0, f"Test setup error: gap {gap} should be < 2.0 for this test to be meaningful"
    assert pd["verdict"] in {"NO_GO", "CAUTION"}, (
        f"Expected NO_GO or CAUTION when skew tightens threshold past borderline gap {gap:+.1f} pp; "
        f"got {pd['verdict']}"
    )


# ── Test 4: No top-league skew → standard -2 pp threshold passes borderline ──

def test_no_skew_uses_standard_threshold():
    """When no top-league skew, a -1 pp gap still qualifies (≥ -2 pp standard threshold)."""
    # live group: 30 picks at 59% hit rate, mixed leagues (no skew)
    # absent group: 30 picks at 60% hit rate → gap = -1 pp → passes ≥ -2 pp
    live_hits = 18   # ~60%
    live_misses = 12
    absent_hits = 18
    absent_misses = 12

    # Use mid-tier leagues for both to avoid skew
    rows = _make_unique(
        [_live_refined_row("hit", league_id=88) for _ in range(live_hits)]
        + [_live_refined_row("miss", league_id=88) for _ in range(live_misses)]
        + [_absent_row("hit", league_id=88) for _ in range(absent_hits)]
        + [_absent_row("miss", league_id=88) for _ in range(absent_misses)]
    )
    result = validate_bzzoiro_position_replay(rows)

    skew = result["topLeagueSkew"]
    assert not skew["detected"], f"Unexpected skew detected: {skew}"
    assert skew["hitRateThreshold"] == -2.0

    # Hit rate gap is 0 pp (both at 60%) — comfortably passes the -2 pp threshold
    pd = result["promotionDecision"]
    # With equal hit rates and sufficient samples the only possible blocker is MAE
    # (which is fine here since projected=3.0, actual=4.0 for both groups → same MAE)
    live_gate = next(c for c in pd["criteria"] if c["check"] == "live_hit_rate")
    assert live_gate["result"] == "pass", (
        f"Expected live_hit_rate to pass at 0 pp gap with standard threshold; "
        f"got {live_gate}"
    )


# ── Test 5: Mandatory live gate blocks GO even when broader cohort is large ───

def test_go_impossible_without_live_cohort_even_with_large_shadow_cohort():
    """A large shadow-mode bzzoiro_valid cohort cannot substitute for live evidence."""
    # 100 bzzoiro-valid shadow picks (excellent hit rate), 0 live-refined
    rows = _make_unique(
        [_bzz_valid_row("hit", pos_source="bzzoiro_shadow_confirmed_lineup", league_id=88)
         for _ in range(80)]
        + [_bzz_valid_row("miss", pos_source="bzzoiro_shadow_confirmed_lineup", league_id=88)
         for _ in range(20)]
        + [_absent_row("hit") for _ in range(5)]
        + [_absent_row("miss") for _ in range(5)]
    )
    result = validate_bzzoiro_position_replay(rows)

    # Broader bzzoiro_valid cohort is large, but liveRefinedN is 0
    assert result["bzzoiroValidN"] == 100
    assert result["liveRefinedN"] == 0

    pd = result["promotionDecision"]
    assert pd["verdict"] == "CAUTION", (
        "Expected CAUTION: shadow-only cohort cannot satisfy the live_sample_size gate; "
        f"got {pd['verdict']}"
    )
    live_gate = next(c for c in pd["criteria"] if c["check"] == "live_sample_size")
    assert live_gate["result"] == "insufficient_data"


# ── Test 6: Shadow-only top-league dilution does NOT falsely trigger skew ─────

def test_shadow_top_league_dilution_does_not_trigger_skew():
    """Shadow picks concentrated in top leagues cannot falsely flag skew for a balanced live cohort."""
    # 30 live-refined picks spread across mid-tier leagues (0% top-league)
    # 80 shadow-mode picks all from EPL (100% top-league) — dilute the broader cohort
    # 30 absent picks at 50% hit rate
    rows = _make_unique(
        # Live-refined — mid-tier leagues only
        [_live_refined_row("hit", league_id=88) for _ in range(18)]
        + [_live_refined_row("miss", league_id=88) for _ in range(12)]
        # Shadow-mode bzzoiro-valid — top league (should NOT affect skew detection)
        + [_bzz_valid_row("hit", pos_source="bzzoiro_shadow_confirmed_lineup", league_id=39)
           for _ in range(60)]
        + [_bzz_valid_row("miss", pos_source="bzzoiro_shadow_confirmed_lineup", league_id=39)
           for _ in range(20)]
        + [_absent_row("hit") for _ in range(15)]
        + [_absent_row("miss") for _ in range(15)]
    )
    result = validate_bzzoiro_position_replay(rows)

    skew = result["topLeagueSkew"]
    # live-refined has 0% top-league; shadow picks must not inflate this
    assert not skew["detected"], (
        f"Skew should NOT be detected when shadow picks are top-league but live picks are not; "
        f"skew info: {skew}"
    )
    assert skew["liveRefinedTopLeaguePct"] == 0.0, (
        f"Live-refined top-league pct should be 0 (no EPL/top picks in live cohort); "
        f"got {skew['liveRefinedTopLeaguePct']}"
    )


# ── Test 7: False-tightening prevention — shadow skew must not block balanced live cohort ─

def test_shadow_skew_does_not_block_balanced_live_cohort():
    """Even if shadow picks are all top-league, a balanced live cohort proceeds at standard threshold."""
    # Same scenario as test 6, but verify the verdict can reach GO
    # (no skew → standard -2 pp threshold → GO is reachable)
    rows = _make_unique(
        # 30 live-refined picks, mid-tier leagues, 65% hit rate (well above absent's 50%)
        [_live_refined_row("hit", league_id=88) for _ in range(20)]
        + [_live_refined_row("miss", league_id=88) for _ in range(10)]
        # Many shadow-mode EPL picks — must not trigger skew
        + [_bzz_valid_row("hit", pos_source="bzzoiro_shadow_confirmed_lineup", league_id=39)
           for _ in range(60)]
        + [_bzz_valid_row("miss", pos_source="bzzoiro_shadow_confirmed_lineup", league_id=39)
           for _ in range(20)]
        # Absent baseline: 50% hit rate
        + [_absent_row("hit", league_id=88) for _ in range(15)]
        + [_absent_row("miss", league_id=88) for _ in range(15)]
    )
    result = validate_bzzoiro_position_replay(rows)

    skew = result["topLeagueSkew"]
    assert not skew["detected"], "Shadow-only top-league picks must not trigger skew on live cohort"
    # Standard threshold applies → 65% vs 50% = +15 pp gap > -2 pp threshold → GO
    pd = result["promotionDecision"]
    assert pd["verdict"] == "GO", (
        f"Expected GO: balanced live cohort with +15 pp hit-rate advantage at standard threshold; "
        f"got {pd['verdict']}: {pd['summary']}"
    )
