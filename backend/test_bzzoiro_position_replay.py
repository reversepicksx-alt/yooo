"""Focused unit tests for validate_bzzoiro_position_replay.

Covers:
1. No live-mode refined picks → CAUTION (cannot GO without liveRefinedN >= 30)
2. Sufficient live picks with good hit rate → GO
3. Top-league skew confirmed → stricter hit-rate threshold (+2 pp)
4. Top-league skew NOT confirmed → standard threshold (-2 pp)
5. GO verdict requires liveRefinedN >= 30, not just bzzoiro_valid cohort size
6. Multi-round void/repair/re-void cycles: nVoidedCovered and nRepairedInCorpus
   stay accurate at every stage of the pick lifecycle.
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


def _bzz_valid_tc() -> dict:
    """Return a tacticalContext dict with a valid Bzzoiro positionValidation snapshot."""
    return {
        "bzzoiroEnrichment": {
            "available": True,
            "positionValidation": {
                "valid": True,
                "lineupValid": True,
                "fixtureDateMatch": "exact",
            },
        },
        "player": {
            "positionSource": "bzzoiro_shadow_confirmed_lineup",
        },
    }


def _voided_bzz_row(void_reason: str = "Player only played 12 min (min 30 required)") -> dict:
    """A pick that is currently voided (no HIT/MISS result) with valid Bzzoiro coverage.

    This represents a pick at Step 1 (initial void) or Step 3 (re-void after repair)
    of a multi-round void/repair cycle.  The pick is NOT scored directionally.
    """
    return {
        "sport": "soccer",
        "status": "settled",
        "result": "dnp",          # not "hit" or "miss" → _is_scored_directional_row=False
        "voidReason": void_reason,
        "recommendation": "over",
        "fixtureId": "fv1",
        "fixtureDate": "2026-01-01",
        "playerId": "pv1",
        "playerName": "Voided Player",
        "teamId": "t1",
        "opponentId": "t2",
        "propType": "passes",
        "line": 3.5,
        "projectedValue": 3.0,
        "actualValue": None,
        "confidenceScore": 65.0,
        "settledAt": "2026-01-02T00:00:00Z",
        "leagueId": 88,
        "tacticalContext": _bzz_valid_tc(),
    }


def _repaired_bzz_row(
    result: str = "hit",
    settled_by: str = "admin_regrade_dnp",
) -> dict:
    """A pick that was originally voided and later repaired to a terminal HIT/MISS.

    This represents Step 2 (round-1 repair) or Step 4 (final repair) of a multi-round
    cycle.  The pick IS scored directionally and carries the repair provenance marker.
    _is_repaired_pick() returns True → nRepairedInCorpus is incremented.
    """
    return {
        "sport": "soccer",
        "status": "settled",
        "result": result,          # "hit" or "miss" → _is_scored_directional_row=True
        "settledBy": settled_by,   # "admin_regrade_dnp" → _is_repaired_pick=True
        "recommendation": "over",
        "fixtureId": "fr1",
        "fixtureDate": "2026-01-01",
        "playerId": "pr1",
        "playerName": "Repaired Player",
        "teamId": "t1",
        "opponentId": "t2",
        "propType": "passes",
        "line": 3.5,
        "projectedValue": 3.0,
        "actualValue": 4.0 if result == "hit" else 2.5,
        "confidenceScore": 65.0,
        "settledAt": "2026-01-02T12:00:00Z",
        "leagueId": 88,
        "tacticalContext": _bzz_valid_tc(),
    }


def _absent_voided_row(void_reason: str = "DNP") -> dict:
    """A voided pick WITHOUT Bzzoiro coverage — must not appear in nVoidedCovered."""
    return {
        "sport": "soccer",
        "status": "settled",
        "result": "dnp",
        "voidReason": void_reason,
        "recommendation": "over",
        "fixtureId": "fav1",
        "fixtureDate": "2026-01-01",
        "playerId": "pav1",
        "playerName": "Absent Voided",
        "teamId": "t3",
        "opponentId": "t4",
        "propType": "passes",
        "line": 3.5,
        "projectedValue": None,
        "actualValue": None,
        "confidenceScore": 60.0,
        "settledAt": "2026-01-02T00:00:00Z",
        "leagueId": 88,
        "tacticalContext": {},    # no Bzzoiro coverage
    }


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


# ── Tests 8–13: Multi-round void / repair / re-void lifecycle ────────────────
#
# Each test simulates a single pick's DB state at one point in the lifecycle and
# asserts that validate_bzzoiro_position_replay classifies it correctly.
#
# nVoidedCovered: picks with valid Bzzoiro coverage that are currently voided
#                 (result not "hit"/"miss", voidReason present)
# nRepairedInCorpus: picks that are now scored (result="hit"/"miss") and carry
#                    the repair provenance marker (settledBy="admin_regrade_dnp"
#                    or correctedManually=True) — goes into a metric group too.
#
# A single pick can never be counted in both counters at the same time because
# the two checks are mutually exclusive (one requires _is_scored_directional_row=False,
# the other requires it to be True).


def test_currently_voided_bzz_pick_counted_in_n_voided_covered():
    """Step 1 of cycle: pick is voided → appears in nVoidedCovered, not nRepairedInCorpus."""
    rows = _make_unique([_voided_bzz_row(void_reason="Player only played 12 min (min 30 required)")])
    result = validate_bzzoiro_position_replay(rows)

    assert result["nVoidedCovered"] == 1, (
        "A voided pick with valid Bzzoiro coverage must be counted in nVoidedCovered."
    )
    assert result["nRepairedInCorpus"] == 0, (
        "A voided pick (no HIT/MISS result) must not appear in nRepairedInCorpus."
    )
    # The voided pick has no scored direction, so it must not enter either metric group.
    assert result["bzzoiroValidN"] == 0, (
        "A voided pick must not contribute to the bzzoiro_valid scored metric group."
    )


def test_repaired_after_first_void_counted_in_n_repaired():
    """Step 2 of cycle: pick repaired (settledBy=admin_regrade_dnp, result=hit) →
    counted in nRepairedInCorpus and enters group_a; NOT in nVoidedCovered.
    """
    rows = _make_unique([_repaired_bzz_row(result="hit", settled_by="admin_regrade_dnp")])
    result = validate_bzzoiro_position_replay(rows)

    assert result["nRepairedInCorpus"] == 1, (
        "A pick repaired from a void (settledBy=admin_regrade_dnp) must be counted in nRepairedInCorpus."
    )
    assert result["nVoidedCovered"] == 0, (
        "A repaired pick (has HIT result, no voidReason) must not appear in nVoidedCovered."
    )
    # Repaired pick has a valid Bzzoiro snapshot and result=hit → enters group_a.
    assert result["bzzoiroValidN"] == 1, (
        "A repaired pick with valid Bzzoiro coverage must contribute to the bzzoiro_valid metric group."
    )


def test_revoid_after_repair_counted_in_n_voided_covered_not_repaired():
    """Step 3 of cycle: pick repaired then voided again (new voidReason, no result) →
    appears in nVoidedCovered only.  The previous repair provenance is irrelevant because
    settledBy was unset during the re-void and the pick has no scored result.
    """
    # After the re-void: settledBy is unset, result is not "hit"/"miss", voidReason is set again.
    row = _voided_bzz_row(void_reason="Stat correction: official box score revised")
    rows = _make_unique([row])
    result = validate_bzzoiro_position_replay(rows)

    assert result["nVoidedCovered"] == 1, (
        "A re-voided pick (after a prior repair cycle) must appear in nVoidedCovered."
    )
    assert result["nRepairedInCorpus"] == 0, (
        "A pick with no HIT/MISS result must not appear in nRepairedInCorpus, "
        "even if it was previously repaired."
    )
    assert result["bzzoiroValidN"] == 0, (
        "A re-voided pick must not contribute to the bzzoiro_valid scored metric group."
    )


def test_final_repair_after_two_rounds_counted_once_in_n_repaired():
    """Step 4 of cycle (final state): pick has been through two void-repair rounds.
    Final state: result='miss', settledBy='admin_regrade_dnp', no voidReason.
    Must appear exactly once in nRepairedInCorpus and the appropriate metric group.
    """
    row = _repaired_bzz_row(result="miss", settled_by="admin_regrade_dnp")
    rows = _make_unique([row])
    result = validate_bzzoiro_position_replay(rows)

    assert result["nRepairedInCorpus"] == 1, (
        "Final repaired pick (after two void-repair rounds) must be counted exactly once "
        "in nRepairedInCorpus — pick history does not create duplicate entries."
    )
    assert result["nVoidedCovered"] == 0, (
        "Final repaired pick has no voidReason; must not appear in nVoidedCovered."
    )
    assert result["bzzoiroValidN"] == 1, (
        "Final repaired pick must contribute to the bzzoiro_valid scored metric group."
    )


def test_corrected_manually_flag_also_counts_as_repaired():
    """correctedManually=True is an alternative repair provenance marker.
    A pick with correctedManually=True + result=hit must appear in nRepairedInCorpus.
    """
    row = _repaired_bzz_row(result="hit", settled_by="admin_manual")
    row["correctedManually"] = True
    row.pop("settledBy", None)  # remove settledBy to confirm the flag alone is sufficient
    rows = _make_unique([row])
    result = validate_bzzoiro_position_replay(rows)

    assert result["nRepairedInCorpus"] == 1, (
        "correctedManually=True must be treated as a repair provenance marker, "
        "counting the pick in nRepairedInCorpus."
    )
    assert result["nVoidedCovered"] == 0


def test_multi_round_batch_counts_each_pick_in_correct_bucket():
    """A batch containing picks at every stage of the void/repair cycle must
    count each pick in exactly one bucket with no double-counting.

    Corpus:
      - 2 normal scored picks (bzz_valid) → bzzoiroValidN+=2
      - 1 pick currently voided (round-1 void) → nVoidedCovered+=1
      - 1 pick repaired after round-1 void → nRepairedInCorpus+=1, bzzoiroValidN+=1
      - 1 pick re-voided after round-1 repair (round-2 void) → nVoidedCovered+=1
      - 1 pick at final repair after two rounds → nRepairedInCorpus+=1, bzzoiroValidN+=1
      - 1 absent voided pick (no Bzzoiro coverage) → counted nowhere
      - 2 absent normal picks → bzzoiroAbsentN+=2
    """
    rows = _make_unique([
        # Normal bzzoiro-valid scored picks
        _bzz_valid_row("hit", league_id=88),
        _bzz_valid_row("miss", league_id=88),
        # Round-1 void
        _voided_bzz_row("Player not in lineup"),
        # Round-1 repair
        _repaired_bzz_row("hit", "admin_regrade_dnp"),
        # Round-2 void (re-voided after first repair)
        _voided_bzz_row("Stat correction after rematch"),
        # Final repair (after two rounds)
        _repaired_bzz_row("miss", "admin_regrade_dnp"),
        # Absent voided — no Bzzoiro coverage, must not count
        _absent_voided_row("DNP"),
        # Absent normal picks
        _absent_row("hit"),
        _absent_row("miss"),
    ])
    result = validate_bzzoiro_position_replay(rows)

    # Voided picks with valid Bzzoiro coverage (round-1 + round-2 void)
    assert result["nVoidedCovered"] == 2, (
        f"Expected nVoidedCovered=2 (two picks currently voided with Bzzoiro coverage); "
        f"got {result['nVoidedCovered']}"
    )
    # Repaired picks that are now scored (round-1 repair + final repair)
    assert result["nRepairedInCorpus"] == 2, (
        f"Expected nRepairedInCorpus=2 (two picks repaired from voids); "
        f"got {result['nRepairedInCorpus']}"
    )
    # bzzoiroValidN = 2 normal + 2 repaired (all have valid Bzzoiro positionValidation)
    assert result["bzzoiroValidN"] == 4, (
        f"Expected bzzoiroValidN=4 (2 normal + 2 repaired); got {result['bzzoiroValidN']}"
    )
    # Absent scored picks (no Bzzoiro coverage)
    assert result["bzzoiroAbsentN"] == 2, (
        f"Expected bzzoiroAbsentN=2; got {result['bzzoiroAbsentN']}"
    )
    # The absent voided pick must not appear anywhere
    total_accounted = (
        result["bzzoiroValidN"]     # scored bzzoiro-valid (normal + repaired)
        + result["bzzoiroAbsentN"]  # scored absent
        + result["nVoidedCovered"]  # voided bzzoiro-valid (not scored)
    )
    # Total = 4 + 2 + 2 = 8; the absent voided pick is not in any counter
    assert total_accounted == 8, (
        f"Total accounted picks should be 8 (absent voided not counted); got {total_accounted}"
    )


def test_void_without_bzzoiro_coverage_excluded_from_n_voided_covered():
    """A voided pick that lacks valid Bzzoiro positionValidation must not appear in
    nVoidedCovered — absence of coverage is not a covered fixture.
    """
    rows = _make_unique([_absent_voided_row("Player not in squad")])
    result = validate_bzzoiro_position_replay(rows)

    assert result["nVoidedCovered"] == 0, (
        "A voided pick without Bzzoiro coverage must NOT be counted in nVoidedCovered."
    )
    assert result["nRepairedInCorpus"] == 0
    assert result["bzzoiroValidN"] == 0
    assert result["bzzoiroAbsentN"] == 0  # no scored result, so absent group is also empty


def test_no_double_counting_repaired_pick_not_in_voided_and_scored():
    """A repaired pick (result=hit, settledBy=admin_regrade_dnp) must appear in
    nRepairedInCorpus and bzzoiroValidN, but nVoidedCovered must stay 0.
    Verifies the two counters are mutually exclusive for any single pick state.
    """
    rows = _make_unique([
        _repaired_bzz_row("hit", "admin_regrade_dnp"),
        _repaired_bzz_row("miss", "admin_regrade_dnp"),
    ])
    result = validate_bzzoiro_position_replay(rows)

    assert result["nRepairedInCorpus"] == 2
    assert result["nVoidedCovered"] == 0, (
        "Repaired picks (HIT/MISS result) can never simultaneously appear in nVoidedCovered."
    )
    # Sanity: both repaired picks are scored bzzoiro-valid
    assert result["bzzoiroValidN"] == 2
