"""Tests for Bzzoiro pressure-evidence validation and promotion gate.

Covers:
  - compare_press_signals(): label comparison between Bzzoiro and API-Football
  - evaluate_bzzoiro_pressure_evidence(): settled-outcome evaluation and gate
  - Promotion gate thresholds are never automatically crossed
  - Missing/unavailable signals are handled without silent fallbacks
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from bzzoiro_client import (
    compare_press_signals,
    evaluate_bzzoiro_pressure_evidence,
    compute_press_proxy,
    _is_eligible_row,
    _is_coverage_row,
    PROMOTION_MIN_COVERED_FIXTURES,
    PROMOTION_MIN_PASS_PROP_OUTCOMES,
    PROMOTION_MIN_SIGNAL_AGREEMENT_RATE,
    PROMOTION_MIN_DIRECTION_IMPROVEMENT,
)


# ── compare_press_signals ─────────────────────────────────────────────────────


def test_compare_returns_agree_when_both_labels_match():
    result = compare_press_signals(
        {"label": "High"},
        {"label": "High"},
    )
    assert result["agreement"] == "agree"
    assert result["tierDistance"] == 0
    assert result["bzzoiroLabel"] == "High"
    assert result["apiFootballLabel"] == "High"
    assert result["shadowOnly"] is True


def test_compare_returns_adjacent_when_one_tier_apart():
    result = compare_press_signals(
        {"label": "Moderate"},
        {"label": "High"},
    )
    assert result["agreement"] == "adjacent"
    assert result["tierDistance"] == 1


def test_compare_returns_contradict_when_two_tiers_apart():
    result = compare_press_signals(
        {"label": "Low"},
        {"label": "High"},
    )
    assert result["agreement"] == "contradict"
    assert result["tierDistance"] == 2


def test_compare_returns_contradict_for_maximum_tier_distance():
    result = compare_press_signals(
        {"label": "Low"},
        {"label": "Elite"},
    )
    assert result["agreement"] == "contradict"
    assert result["tierDistance"] == 3


def test_compare_returns_unavailable_when_bzzoiro_missing():
    result = compare_press_signals(None, {"label": "Moderate"})
    assert result["agreement"] == "unavailable"
    assert result["bzzoiroLabel"] is None
    assert result["shadowOnly"] is True


def test_compare_returns_unavailable_when_apifootball_missing():
    result = compare_press_signals({"label": "High"}, None)
    assert result["agreement"] == "unavailable"
    assert result["apiFootballLabel"] is None


def test_compare_returns_unavailable_when_apifootball_unknown_label():
    """API-Football returns 'Unknown' when it has no press data — must be treated as missing."""
    result = compare_press_signals({"label": "High"}, {"label": "Unknown"})
    assert result["agreement"] == "unavailable"
    assert result["apiFootballLabel"] is None


def test_compare_returns_unavailable_when_both_missing():
    result = compare_press_signals(None, None)
    assert result["agreement"] == "unavailable"
    assert result["tierDistance"] is None


def test_compare_returns_unavailable_for_unrecognised_bzzoiro_label():
    result = compare_press_signals({"label": "Extreme"}, {"label": "High"})
    assert result["agreement"] == "unavailable"


def test_compare_accepts_compute_press_proxy_output_as_bzzoiro_input():
    """compute_press_proxy() output must be directly usable in compare_press_signals()."""
    proxy = compute_press_proxy({
        "total_tackles": 22,
        "interceptions": 13,
        "passes": 520,
        "ball_possession": 48,
    })
    assert proxy is not None
    result = compare_press_signals(proxy, {"label": "High"})
    # The proxy should produce a recognisable label (High at 35 defensive actions).
    assert result["agreement"] in {"agree", "adjacent", "contradict"}
    assert result["shadowOnly"] is True


def test_compare_signal_always_carries_shadow_only_flag():
    """agreement=agree must still carry shadowOnly=True — agreement does not promote the signal."""
    result = compare_press_signals({"label": "Elite"}, {"label": "Elite"})
    assert result["shadowOnly"] is True


# ── _is_eligible_row ──────────────────────────────────────────────────────────


def test_eligible_row_requires_recognised_bzzoiro_label():
    assert _is_eligible_row({"bzzoiro_label": "High", "apifootball_label": "High", "outcome": "HIT"})
    assert not _is_eligible_row({"bzzoiro_label": None, "apifootball_label": "High", "outcome": "HIT"})
    assert not _is_eligible_row({"bzzoiro_label": "Unknown", "apifootball_label": "High", "outcome": "HIT"})
    assert not _is_eligible_row({"bzzoiro_label": "Extreme", "apifootball_label": "High", "outcome": "HIT"})


def test_eligible_row_requires_recognised_apifootball_label():
    assert not _is_eligible_row({"bzzoiro_label": "High", "apifootball_label": None, "outcome": "HIT"})
    assert not _is_eligible_row({"bzzoiro_label": "High", "apifootball_label": "Unknown", "outcome": "HIT"})
    assert not _is_eligible_row({"bzzoiro_label": "High", "apifootball_label": "", "outcome": "HIT"})


def test_eligible_row_requires_valid_settled_outcome():
    assert not _is_eligible_row({"bzzoiro_label": "High", "apifootball_label": "High", "outcome": None})
    assert not _is_eligible_row({"bzzoiro_label": "High", "apifootball_label": "High", "outcome": "PUSH"})
    assert not _is_eligible_row({"bzzoiro_label": "High", "apifootball_label": "High", "outcome": "VOID"})
    assert _is_eligible_row({"bzzoiro_label": "High", "apifootball_label": "High", "outcome": "MISS"})


def test_eligible_row_rejects_non_dict():
    assert not _is_eligible_row(None)
    assert not _is_eligible_row("High")
    assert not _is_eligible_row(42)


# ── evaluate_bzzoiro_pressure_evidence — empty / bad input ────────────────────


def test_evaluate_returns_gate_failed_on_empty_input():
    result = evaluate_bzzoiro_pressure_evidence([])
    assert result["nCovered"] == 0
    assert result["nSupplied"] == 0
    assert result["gatePassed"] is False
    assert result["promotionStatus"] == "shadow_only"


def test_evaluate_returns_gate_failed_on_none_input():
    result = evaluate_bzzoiro_pressure_evidence(None)  # type: ignore[arg-type]
    assert result["nCovered"] == 0
    assert result["gatePassed"] is False


def test_evaluate_promotion_status_is_always_shadow_only():
    """promotionStatus must never be anything other than shadow_only regardless of gate result."""
    result = evaluate_bzzoiro_pressure_evidence([])
    assert result["promotionStatus"] == "shadow_only"


def test_evaluate_tracks_supplied_vs_covered_separately():
    """nSupplied counts all input rows; nCovered counts only eligible ones."""
    rows = [
        _make_outcome(bzzoiro_label="High", apifootball_label="High"),  # eligible
        _make_outcome(bzzoiro_label="High", apifootball_label="High"),  # eligible
        _make_outcome(bzzoiro_label=None, apifootball_label="High"),    # ineligible — no Bzzoiro label
        {"bzzoiro_label": "Unknown", "apifootball_label": "High", "prop_type": "passes", "outcome": "HIT"},  # ineligible
    ]
    result = evaluate_bzzoiro_pressure_evidence(rows)
    assert result["nSupplied"] == 4
    assert result["nCovered"] == 2


# ── evaluate_bzzoiro_pressure_evidence — agreement rate ──────────────────────


def _make_outcome(
    *,
    bzzoiro_label: str = "High",
    apifootball_label: str = "High",
    prop_type: str = "passes",
    direction: str = "UNDER",
    outcome: str = "HIT",
    baseline_correct: bool | None = None,
) -> dict:
    row = {
        "bzzoiro_label": bzzoiro_label,
        "apifootball_label": apifootball_label,
        "prop_type": prop_type,
        "direction": direction,
        "outcome": outcome,
    }
    if baseline_correct is not None:
        row["baseline_correct"] = baseline_correct
    return row


def _enough_rows(n: int, **kwargs) -> list[dict]:
    """Return n rows with defaults that satisfy all promotion-gate thresholds."""
    return [_make_outcome(**kwargs) for _ in range(n)]


# ── regression: unavailable Bzzoiro rows cannot inflate sample or gate ────────


def test_unavailable_bzzoiro_label_excluded_from_pass_sample():
    """Pass rows with missing Bzzoiro label must not be counted in nPassProps or accuracy."""
    rows = [
        # Eligible pass row
        _make_outcome(bzzoiro_label="High", apifootball_label="High", prop_type="passes",
                      direction="UNDER", outcome="HIT"),
        # Ineligible: Bzzoiro label is None — must be excluded entirely
        {"bzzoiro_label": None, "apifootball_label": "High",
         "prop_type": "passes", "direction": "UNDER", "outcome": "HIT"},
        # Ineligible: Bzzoiro label is "Unknown" — must be excluded entirely
        {"bzzoiro_label": "Unknown", "apifootball_label": "High",
         "prop_type": "passes", "direction": "UNDER", "outcome": "HIT"},
    ]
    result = evaluate_bzzoiro_pressure_evidence(rows)
    assert result["nSupplied"] == 3
    assert result["nCovered"] == 1      # only the first row is eligible
    assert result["nPassProps"] == 1    # same — only the first row counts
    assert result["directionAccuracyWithBzzoiro"] == 1.0  # 1/1


def test_unavailable_bzzoiro_label_cannot_permit_gate_to_pass():
    """A dataset full of rows with unavailable Bzzoiro labels must never satisfy the gate,
    even when they are all HIT and there are enough rows to meet other thresholds."""
    n = PROMOTION_MIN_COVERED_FIXTURES * 2
    rows = [
        {"bzzoiro_label": None, "apifootball_label": "High",
         "prop_type": "passes", "direction": "UNDER", "outcome": "HIT",
         "baseline_correct": False}
        for _ in range(n)
    ]
    result = evaluate_bzzoiro_pressure_evidence(rows)
    assert result["nCovered"] == 0     # no eligible rows
    assert result["nPassProps"] == 0
    assert result["gatePassed"] is False
    assert result["promotionStatus"] == "shadow_only"


def test_unknown_apifootball_label_excluded_from_sample():
    """Rows where API-Football label is 'Unknown' must be excluded from all calculations."""
    rows = [
        _make_outcome(bzzoiro_label="High", apifootball_label="High"),  # eligible
        _make_outcome(bzzoiro_label="High", apifootball_label="Unknown"),  # ineligible
    ]
    result = evaluate_bzzoiro_pressure_evidence(rows)
    assert result["nCovered"] == 1
    assert result["nPassProps"] == 1


def test_mixed_eligible_ineligible_rows_only_eligible_count():
    """The gate must be assessed only on eligible rows; ineligible rows are excluded silently."""
    n_eligible = PROMOTION_MIN_COVERED_FIXTURES
    rows_eligible = [_make_outcome(
        bzzoiro_label="High", apifootball_label="High",
        prop_type="passes", direction="UNDER", outcome="HIT",
        baseline_correct=False,
    ) for _ in range(n_eligible)]
    # Mix in ineligible rows that would inflate the sample if counted.
    rows_ineligible = [
        {"bzzoiro_label": None, "apifootball_label": "High",
         "prop_type": "passes", "direction": "UNDER", "outcome": "HIT"}
        for _ in range(20)
    ]
    result = evaluate_bzzoiro_pressure_evidence(rows_eligible + rows_ineligible)
    assert result["nSupplied"] == n_eligible + 20
    assert result["nCovered"] == n_eligible    # only eligible rows counted
    assert result["nPassProps"] == n_eligible  # ineligible pass rows excluded
    # Gate must pass on the eligible subset alone (all agree, all HIT, baseline=False → big lift).
    assert result["gatePassed"] is True
    assert result["promotionStatus"] == "shadow_only"


# ── evaluate_bzzoiro_pressure_evidence — agreement rate ──────────────────────


def test_evaluate_computes_perfect_agreement_rate():
    rows = _enough_rows(10, bzzoiro_label="High", apifootball_label="High")
    result = evaluate_bzzoiro_pressure_evidence(rows)
    assert result["signalAgreementRate"] == 1.0


def test_evaluate_counts_adjacent_as_agreement():
    """Adjacent tiers (distance=1) must count as agreement for the rate calculation."""
    rows = _enough_rows(10, bzzoiro_label="Moderate", apifootball_label="High")
    result = evaluate_bzzoiro_pressure_evidence(rows)
    assert result["signalAgreementRate"] == 1.0


def test_evaluate_counts_contradict_as_disagreement():
    rows = _enough_rows(10, bzzoiro_label="Low", apifootball_label="High")
    result = evaluate_bzzoiro_pressure_evidence(rows)
    assert result["signalAgreementRate"] == 0.0


def test_evaluate_excludes_unavailable_rows_from_agreement_rate():
    """Rows where one label is Unknown/None are ineligible entirely — not counted as agrees."""
    rows = [
        _make_outcome(bzzoiro_label="High", apifootball_label="High"),        # eligible/agree
        _make_outcome(bzzoiro_label="High", apifootball_label="Unknown"),     # ineligible — excluded from everything
        _make_outcome(bzzoiro_label="High", apifootball_label="High"),        # eligible/agree
    ]
    result = evaluate_bzzoiro_pressure_evidence(rows)
    # Only 2 rows eligible: 2/2 = 1.0
    assert result["nCovered"] == 2
    assert result["signalAgreementRate"] == 1.0


def test_evaluate_mixed_agreement_rate():
    rows = [
        _make_outcome(bzzoiro_label="High", apifootball_label="High"),        # agree
        _make_outcome(bzzoiro_label="Low", apifootball_label="Elite"),        # contradict
        _make_outcome(bzzoiro_label="Moderate", apifootball_label="High"),    # adjacent=agree
        _make_outcome(bzzoiro_label="Low", apifootball_label="High"),         # contradict
    ]
    result = evaluate_bzzoiro_pressure_evidence(rows)
    # agree=2, disagree=2 → 0.50
    assert result["signalAgreementRate"] == 0.5


# ── evaluate_bzzoiro_pressure_evidence — direction accuracy ───────────────────


def test_evaluate_counts_only_pass_props_for_direction_accuracy():
    """Direction accuracy must only be computed on pass_attempts and passes props."""
    rows = [
        _make_outcome(prop_type="passes", direction="UNDER", outcome="HIT"),
        _make_outcome(prop_type="pass_attempts", direction="OVER", outcome="MISS"),
        _make_outcome(prop_type="shots", direction="OVER", outcome="HIT"),   # must be excluded
        _make_outcome(prop_type="tackles", direction="OVER", outcome="HIT"), # must be excluded
    ]
    result = evaluate_bzzoiro_pressure_evidence(rows)
    assert result["nPassProps"] == 2
    # UNDER+HIT = correct, OVER+MISS = incorrect → 1/2 = 50%
    assert result["directionAccuracyWithBzzoiro"] == 0.5


def test_evaluate_under_hit_is_correct_prediction():
    rows = [_make_outcome(direction="UNDER", outcome="HIT")]
    result = evaluate_bzzoiro_pressure_evidence(rows)
    assert result["directionAccuracyWithBzzoiro"] == 1.0


def test_evaluate_over_hit_is_correct_prediction():
    rows = [_make_outcome(direction="OVER", outcome="HIT")]
    result = evaluate_bzzoiro_pressure_evidence(rows)
    assert result["directionAccuracyWithBzzoiro"] == 1.0


def test_evaluate_under_miss_is_incorrect_prediction():
    rows = [_make_outcome(direction="UNDER", outcome="MISS")]
    result = evaluate_bzzoiro_pressure_evidence(rows)
    assert result["directionAccuracyWithBzzoiro"] == 0.0


def test_evaluate_over_miss_is_incorrect_prediction():
    rows = [_make_outcome(direction="OVER", outcome="MISS")]
    result = evaluate_bzzoiro_pressure_evidence(rows)
    assert result["directionAccuracyWithBzzoiro"] == 0.0


def test_evaluate_baseline_defaults_to_same_when_not_provided():
    """When baseline_correct is absent, directionImprovement must be 0.0 — no phantom lift."""
    rows = _enough_rows(5)
    result = evaluate_bzzoiro_pressure_evidence(rows)
    assert result["directionImprovement"] == 0.0


def test_evaluate_computes_improvement_from_baseline_correct_field():
    """baseline_correct=False rows lower baseline acc, showing Bzzoiro improves direction."""
    rows = [
        _make_outcome(direction="UNDER", outcome="HIT", baseline_correct=True),   # both correct
        _make_outcome(direction="UNDER", outcome="HIT", baseline_correct=True),   # both correct
        _make_outcome(direction="UNDER", outcome="HIT", baseline_correct=False),  # bz correct, baseline wrong
        _make_outcome(direction="UNDER", outcome="HIT", baseline_correct=False),  # bz correct, baseline wrong
    ]
    result = evaluate_bzzoiro_pressure_evidence(rows)
    # bz accuracy = 4/4 = 1.0; baseline = 2/4 = 0.5
    assert result["directionAccuracyWithBzzoiro"] == 1.0
    assert result["directionAccuracyBaseline"] == 0.5
    assert result["directionImprovement"] == pytest_approx(0.5)


def pytest_approx(value, rel=1e-6):
    """Minimal approximate-equality helper (avoids importing pytest in assertions)."""
    class _Approx:
        def __eq__(self, other):
            return abs(other - value) < rel
        def __repr__(self):
            return f"≈{value}"
    return _Approx()


# ── evaluate_bzzoiro_pressure_evidence — promotion gate ──────────────────────


def test_gate_fails_on_too_few_covered_fixtures():
    n = PROMOTION_MIN_COVERED_FIXTURES - 1
    rows = _enough_rows(n, bzzoiro_label="High", apifootball_label="High", direction="UNDER", outcome="HIT")
    result = evaluate_bzzoiro_pressure_evidence(rows)
    assert result["gatePassed"] is False
    assert any("fixture" in msg.lower() for msg in result["gateFailures"])


def test_gate_fails_on_too_few_pass_prop_outcomes():
    """Enough covered fixtures but all are non-pass props → pass-prop gate fails."""
    rows = _enough_rows(
        PROMOTION_MIN_COVERED_FIXTURES,
        bzzoiro_label="High",
        apifootball_label="High",
        prop_type="shots",
        direction="OVER",
        outcome="HIT",
    )
    result = evaluate_bzzoiro_pressure_evidence(rows)
    assert result["gatePassed"] is False
    assert result["nPassProps"] == 0
    assert any("pass" in msg.lower() for msg in result["gateFailures"])


def test_gate_fails_when_agreement_rate_too_low():
    """All fixtures, pass props met, but contradicting labels → agreement gate fails."""
    rows = _enough_rows(
        PROMOTION_MIN_COVERED_FIXTURES,
        bzzoiro_label="Low",
        apifootball_label="Elite",    # contradict on every row
        direction="UNDER",
        outcome="HIT",
    )
    result = evaluate_bzzoiro_pressure_evidence(rows)
    assert result["gatePassed"] is False
    assert result["signalAgreementRate"] == 0.0
    assert any("agreement" in msg.lower() for msg in result["gateFailures"])


def test_gate_fails_when_direction_improvement_too_small():
    """All other thresholds met but Bzzoiro adds no improvement → direction gate fails."""
    n = PROMOTION_MIN_COVERED_FIXTURES
    rows = _enough_rows(
        n,
        bzzoiro_label="High",
        apifootball_label="High",
        direction="UNDER",
        outcome="HIT",
        baseline_correct=True,   # baseline is already correct → improvement = 0
    )
    result = evaluate_bzzoiro_pressure_evidence(rows)
    assert result["directionImprovement"] == 0.0
    assert result["gatePassed"] is False
    assert any("improvement" in msg.lower() for msg in result["gateFailures"])


def test_gate_passes_when_all_thresholds_met():
    """Construct a minimal dataset that satisfies every promotion threshold."""
    n = PROMOTION_MIN_COVERED_FIXTURES   # >= 30
    rows = []
    for i in range(n):
        # alternate: 2/3 baseline wrong so improvement = 1.0 - 0.33 = 0.67 >> 3pp
        baseline_ok = (i % 3 == 0)
        rows.append(_make_outcome(
            bzzoiro_label="High",
            apifootball_label="High",     # agree on every row
            prop_type="passes",
            direction="UNDER",
            outcome="HIT",               # always correct with Bzzoiro
            baseline_correct=baseline_ok,
        ))
    result = evaluate_bzzoiro_pressure_evidence(rows)
    assert result["gatePassed"] is True
    # Even when the gate passes, the signal must remain shadow_only.
    assert result["promotionStatus"] == "shadow_only"
    assert "human review" in result["promotionStatusReason"].lower()


def test_gate_passed_still_carries_shadow_only():
    """Gate passing is a documentation signal, not an automatic promotion."""
    n = PROMOTION_MIN_COVERED_FIXTURES
    rows = [_make_outcome(
        bzzoiro_label="High",
        apifootball_label="High",
        prop_type="passes",
        direction="UNDER",
        outcome="HIT",
        baseline_correct=False,  # lift = 1.0 - 0.0 = 1.0
    ) for _ in range(n)]
    result = evaluate_bzzoiro_pressure_evidence(rows)
    # Gate should pass.
    assert result["gatePassed"] is True
    # But the signal is never auto-promoted.
    assert result["promotionStatus"] == "shadow_only"


# ── promotion threshold constants are conservative ────────────────────────────


def test_promotion_thresholds_are_conservative():
    """The hardcoded promotion-gate thresholds must remain at or above the minimum levels
    required to prevent a sparse evidence base from influencing projections."""
    assert PROMOTION_MIN_COVERED_FIXTURES >= 30, (
        "Need at least one full season of Bzzoiro-covered matches before promotion."
    )
    assert PROMOTION_MIN_PASS_PROP_OUTCOMES >= 20, (
        "Need at least 20 pass-prop outcomes to assess direction quality."
    )
    assert PROMOTION_MIN_SIGNAL_AGREEMENT_RATE >= 0.60, (
        "Need at least 60% signal agreement before treating Bzzoiro as a reliable secondary signal."
    )
    assert PROMOTION_MIN_DIRECTION_IMPROVEMENT >= 0.03, (
        "Need at least a 3pp direction lift to justify enabling a new signal source."
    )


# ── evaluate output shape contract ────────────────────────────────────────────


def test_evaluate_output_always_contains_required_keys():
    required_keys = {
        "nSupplied",
        "nCovered",
        "nPassProps",
        "signalAgreementRate",
        "directionAccuracyWithBzzoiro",
        "directionAccuracyBaseline",
        "directionImprovement",
        "gatePassed",
        "gateFailures",
        "promotionStatus",
        "promotionStatusReason",
        "promotionMinCoveredFixtures",
        "promotionMinPassPropOutcomes",
        "promotionMinSignalAgreementRate",
        "promotionMinDirectionImprovement",
    }
    result = evaluate_bzzoiro_pressure_evidence([])
    assert required_keys.issubset(set(result.keys()))


def test_evaluate_output_shape_with_data():
    rows = _enough_rows(5)
    result = evaluate_bzzoiro_pressure_evidence(rows)
    assert isinstance(result["gateFailures"], list)
    assert isinstance(result["nCovered"], int)
    assert isinstance(result["promotionStatus"], str)


# ── end-to-end: compute_press_proxy → compare → evaluate ─────────────────────


def test_end_to_end_proxy_compare_evaluate():
    """Simulate the full validation pipeline on a batch of synthetic settled outcomes.

    This confirms the three functions compose correctly and that the promotion
    gate returns a structured recommendation when insufficient evidence exists.
    """
    # Build synthetic Bzzoiro press proxies for two different fixture types.
    high_press_fixture = compute_press_proxy({
        "total_tackles": 25,
        "interceptions": 13,
        "passes": 480,
        "ball_possession": 42,
    })
    low_press_fixture = compute_press_proxy({
        "total_tackles": 10,
        "interceptions": 7,
        "passes": 640,
        "ball_possession": 60,
    })

    assert high_press_fixture is not None
    assert low_press_fixture is not None

    # Verify the proxies produce recognisable labels.
    assert high_press_fixture["label"] in {"Moderate", "High", "Elite"}
    assert low_press_fixture["label"] in {"Low", "Moderate"}

    # Signal comparison: high-press Bzzoiro vs a High API-Football signal.
    cmp_high = compare_press_signals(high_press_fixture, {"label": "High"})
    assert cmp_high["agreement"] in {"agree", "adjacent"}

    # Signal comparison: low-press Bzzoiro vs a High API-Football signal.
    cmp_low = compare_press_signals(low_press_fixture, {"label": "High"})
    assert cmp_low["agreement"] in {"adjacent", "contradict"}

    # Evaluate a small batch — should fail the coverage gate.
    outcomes = [
        _make_outcome(
            bzzoiro_label=high_press_fixture["label"],
            apifootball_label="High",
            prop_type="passes",
            direction="UNDER",
            outcome="HIT",
        )
        for _ in range(5)
    ]
    eval_result = evaluate_bzzoiro_pressure_evidence(outcomes)
    assert eval_result["gatePassed"] is False
    assert eval_result["promotionStatus"] == "shadow_only"
    assert eval_result["nCovered"] == 5


# ── _is_coverage_row ──────────────────────────────────────────────────────────


def test_coverage_row_accepts_hit():
    assert _is_coverage_row({"bzzoiro_label": "High", "apifootball_label": "High", "outcome": "HIT"})


def test_coverage_row_accepts_miss():
    assert _is_coverage_row({"bzzoiro_label": "High", "apifootball_label": "High", "outcome": "MISS"})


def test_coverage_row_accepts_void():
    """Voided picks carry a real Bzzoiro-covered fixture and must be counted."""
    assert _is_coverage_row({"bzzoiro_label": "High", "apifootball_label": "High", "outcome": "VOID"})


def test_coverage_row_accepts_void_case_insensitive():
    assert _is_coverage_row({"bzzoiro_label": "High", "apifootball_label": "High", "outcome": "void"})


def test_coverage_row_rejects_missing_bzzoiro_label():
    assert not _is_coverage_row({"bzzoiro_label": None, "apifootball_label": "High", "outcome": "VOID"})


def test_coverage_row_rejects_unknown_apifootball_label():
    assert not _is_coverage_row({"bzzoiro_label": "High", "apifootball_label": "Unknown", "outcome": "VOID"})


def test_coverage_row_rejects_unrecognised_outcome():
    assert not _is_coverage_row({"bzzoiro_label": "High", "apifootball_label": "High", "outcome": "PUSH"})
    assert not _is_coverage_row({"bzzoiro_label": "High", "apifootball_label": "High", "outcome": None})


def test_coverage_row_rejects_non_dict():
    assert not _is_coverage_row(None)
    assert not _is_coverage_row("High")


def test_eligible_row_still_rejects_void():
    """_is_eligible_row is unchanged: VOID must still be rejected (direction accuracy requires HIT/MISS)."""
    assert not _is_eligible_row({"bzzoiro_label": "High", "apifootball_label": "High", "outcome": "VOID"})


# ── evaluate_bzzoiro_pressure_evidence — VOID / voided-pick behaviour ─────────


def _make_void_outcome(
    *,
    bzzoiro_label: str = "High",
    apifootball_label: str = "High",
    prop_type: str = "passes",
) -> dict:
    """A row representing a voided pick (DNP, insufficient minutes, etc.)."""
    return {
        "bzzoiro_label": bzzoiro_label,
        "apifootball_label": apifootball_label,
        "prop_type": prop_type,
        "outcome": "VOID",
    }


def test_void_rows_count_towards_n_covered():
    """VOID rows with valid labels must be counted in nCovered."""
    rows = [
        _make_outcome(bzzoiro_label="High", apifootball_label="High"),  # HIT
        _make_void_outcome(bzzoiro_label="High", apifootball_label="High"),  # VOID
    ]
    result = evaluate_bzzoiro_pressure_evidence(rows)
    assert result["nSupplied"] == 2
    assert result["nCovered"] == 2


def test_void_rows_excluded_from_pass_prop_direction_accuracy():
    """VOID rows must not contribute to nPassProps or directionAccuracyWithBzzoiro."""
    rows = [
        _make_outcome(prop_type="passes", direction="UNDER", outcome="HIT"),
        _make_void_outcome(prop_type="passes"),  # voided pass prop — no direction outcome
    ]
    result = evaluate_bzzoiro_pressure_evidence(rows)
    assert result["nCovered"] == 2       # both count for coverage
    assert result["nPassProps"] == 1    # only the HIT row has a direction outcome
    assert result["directionAccuracyWithBzzoiro"] == 1.0   # 1/1


def test_void_rows_count_for_signal_agreement():
    """Signal-agreement rate must include VOID rows (their labels are real signal)."""
    rows = [
        _make_outcome(bzzoiro_label="High", apifootball_label="High"),  # agree, HIT
        _make_void_outcome(bzzoiro_label="Low", apifootball_label="Elite"),  # contradict, VOID
    ]
    result = evaluate_bzzoiro_pressure_evidence(rows)
    assert result["nCovered"] == 2
    # 1 agree + 1 contradict → 50%
    assert result["signalAgreementRate"] == 0.5


def test_void_only_corpus_counts_coverage_but_no_direction():
    """A corpus of only VOID rows: nCovered is non-zero, direction accuracy is None."""
    rows = [_make_void_outcome() for _ in range(5)]
    result = evaluate_bzzoiro_pressure_evidence(rows)
    assert result["nSupplied"] == 5
    assert result["nCovered"] == 5
    assert result["nPassProps"] == 0
    assert result["directionAccuracyWithBzzoiro"] is None
    assert result["gatePassed"] is False  # still fails: no pass-prop direction outcomes


def test_void_rows_can_help_meet_covered_fixture_gate():
    """VOID rows count towards the nCovered threshold even though they don't improve direction accuracy."""
    n = PROMOTION_MIN_COVERED_FIXTURES
    # Mix: mostly VOID rows to pad coverage, plus enough HIT rows for direction accuracy.
    n_void = n // 2
    n_hit = n - n_void
    rows = (
        [_make_void_outcome(bzzoiro_label="High", apifootball_label="High") for _ in range(n_void)]
        + [_make_outcome(bzzoiro_label="High", apifootball_label="High",
                         prop_type="passes", direction="UNDER", outcome="HIT",
                         baseline_correct=False) for _ in range(n_hit)]
    )
    result = evaluate_bzzoiro_pressure_evidence(rows)
    # Total coverage reaches the threshold.
    assert result["nCovered"] == n
    # Direction accuracy computed on HIT rows only.
    assert result["nPassProps"] == n_hit
    assert result["directionAccuracyWithBzzoiro"] == 1.0


def test_missing_bzzoiro_enrichment_void_row_excluded():
    """A voided pick with an unrecognised Bzzoiro label must still be excluded from nCovered."""
    rows = [
        _make_outcome(),                                          # eligible
        _make_void_outcome(bzzoiro_label=None),                   # excluded — no Bzzoiro label
        _make_void_outcome(bzzoiro_label="Unknown"),              # excluded — unrecognised label
    ]
    result = evaluate_bzzoiro_pressure_evidence(rows)
    assert result["nSupplied"] == 3
    assert result["nCovered"] == 1   # only the HIT row qualifies


# ── bzzoiroEnrichment snapshot survives void + unvoid cycle ──────────────────


def test_bzzoiro_enrichment_snapshot_survives_void_unvoid_cycle():
    """Simulates what the DB update operations do: void then unvoid a pick.

    The void path writes ``voidReason`` and clears settlement fields but must
    NOT touch ``tacticalContext.bzzoiroEnrichment``.  The unvoid path removes
    ``voidReason`` and also must NOT touch ``tacticalContext.bzzoiroEnrichment``.
    Both are verified by simulating the MongoDB ``$set``/``$unset`` operations
    in pure Python so the test runs without a live database.
    """

    def _apply_update(doc: dict, update: dict) -> dict:
        """Apply a minimal MongoDB-style {$set: ..., $unset: ...} update."""
        import copy
        doc = copy.deepcopy(doc)
        for key, val in (update.get("$set") or {}).items():
            # Support nested dotted keys (e.g. "tacticalContext.bzzoiroEnrichment").
            parts = key.split(".")
            target = doc
            for p in parts[:-1]:
                target = target.setdefault(p, {})
            target[parts[-1]] = val
        for key in (update.get("$unset") or {}):
            parts = key.split(".")
            target = doc
            for p in parts[:-1]:
                target = target.get(p, {})
            target.pop(parts[-1], None)
        return doc

    enrichment_snapshot = {
        "available": True,
        "provider": "bzzoiro",
        "pressIntensity": {"label": "High", "score": 0.72},
    }

    # Initial settled pick with Bzzoiro enrichment.
    pick = {
        "pickId": "test-pick-001",
        "status": "settled",
        "result": "hit",
        "actualValue": 72,
        "hitPct": 100,
        "settledAt": "2026-08-01T12:00:00Z",
        "settledBy": "auto",
        "tacticalContext": {
            "bzzoiroEnrichment": enrichment_snapshot,
        },
    }

    # Step 1: Void the pick (as unsettle-picks endpoint does).
    void_update = {
        "$set": {"status": "live", "result": None, "actualValue": None, "hitPct": None},
        "$unset": {"settledAt": "", "settledBy": "", "voidReason": ""},
    }
    voided = _apply_update(pick, void_update)

    # Enrichment must still be present.
    assert voided["tacticalContext"]["bzzoiroEnrichment"] == enrichment_snapshot, (
        "bzzoiroEnrichment was lost during void — the void update must not touch tacticalContext."
    )

    # Step 2: Re-settle with a voidReason (e.g. DNP settlement).
    dnp_update = {
        "$set": {
            "status": "settled",
            "result": "dnp",
            "voidReason": "Player only played 12 min (min 30 required)",
            "settledAt": "2026-08-01T14:00:00Z",
            "settledBy": "auto_dnp",
        },
    }
    dnp_settled = _apply_update(voided, dnp_update)

    # Enrichment still present after DNP re-settlement.
    assert dnp_settled["tacticalContext"]["bzzoiroEnrichment"] == enrichment_snapshot, (
        "bzzoiroEnrichment was lost during DNP re-settlement."
    )

    # Step 3: Repair the settlement (as regrade-dnp-picks endpoint does).
    repair_update = {
        "$set": {
            "result": "hit",
            "hitPct": 100,
            "settledBy": "admin_regrade_dnp",
        },
        "$unset": {"voidReason": ""},
    }
    repaired = _apply_update(dnp_settled, repair_update)

    # Enrichment still present after repair.
    assert repaired["tacticalContext"]["bzzoiroEnrichment"] == enrichment_snapshot, (
        "bzzoiroEnrichment was lost during settlement repair."
    )


def test_bzzoiro_enrichment_preserved_when_voidreason_unset():
    """Unsetting voidReason (the repair path) must not clobber tacticalContext."""

    def _unset(doc: dict, *keys: str) -> dict:
        import copy
        doc = copy.deepcopy(doc)
        for key in keys:
            doc.pop(key, None)
        return doc

    pick = {
        "voidReason": "Player not in matchday squad",
        "tacticalContext": {
            "bzzoiroEnrichment": {"available": True, "provider": "bzzoiro"},
        },
    }
    repaired = _unset(pick, "voidReason")
    assert "voidReason" not in repaired
    assert repaired["tacticalContext"]["bzzoiroEnrichment"]["available"] is True


# ── multi-round void / repair / re-void cycles ────────────────────────────────


def _make_mongo_update_helper():
    """Return a minimal MongoDB-style $set/$unset update applier (no live DB required)."""
    import copy

    def _apply(doc: dict, update: dict) -> dict:
        doc = copy.deepcopy(doc)
        for key, val in (update.get("$set") or {}).items():
            parts = key.split(".")
            target = doc
            for p in parts[:-1]:
                target = target.setdefault(p, {})
            target[parts[-1]] = val
        for key in (update.get("$unset") or {}):
            parts = key.split(".")
            target = doc
            for p in parts[:-1]:
                target = target.get(p, {})
            target.pop(parts[-1], None)
        return doc

    return _apply


def test_bzzoiro_enrichment_survives_full_multi_round_cycle():
    """void → repair → re-void → final repair: enrichment must be intact at every step.

    Production picks sometimes go through multiple settlement corrections when a
    stat is first voided (player did not play), then repaired (minutes corrected),
    then voided again (second stat correction), and finally repaired to a terminal
    HIT/MISS.  This test confirms that bzzoiroEnrichment is preserved across all
    four transitions.
    """
    _apply = _make_mongo_update_helper()

    enrichment_snapshot = {
        "available": True,
        "provider": "bzzoiro",
        "pressIntensity": {"label": "Moderate", "score": 0.51},
    }

    # ── Initial settled pick ─────────────────────────────────────────────────
    pick = {
        "pickId": "multi-round-001",
        "status": "settled",
        "result": "hit",
        "actualValue": 55,
        "hitPct": 100,
        "settledAt": "2026-08-01T10:00:00Z",
        "settledBy": "auto",
        "tacticalContext": {"bzzoiroEnrichment": enrichment_snapshot},
    }

    # ── Round 1: void ────────────────────────────────────────────────────────
    voided_r1 = _apply(pick, {
        "$set": {"status": "live", "result": None, "actualValue": None, "hitPct": None},
        "$unset": {"settledAt": "", "settledBy": ""},
    })
    assert voided_r1["tacticalContext"]["bzzoiroEnrichment"] == enrichment_snapshot, (
        "Round 1 void: bzzoiroEnrichment must not be touched."
    )
    assert voided_r1["result"] is None

    # ── Round 1: repair (re-settle as HIT) ──────────────────────────────────
    repaired_r1 = _apply(voided_r1, {
        "$set": {
            "status": "settled",
            "result": "hit",
            "actualValue": 57,
            "hitPct": 100,
            "settledAt": "2026-08-01T11:00:00Z",
            "settledBy": "admin_repair_r1",
        },
        "$unset": {"voidReason": ""},
    })
    assert repaired_r1["tacticalContext"]["bzzoiroEnrichment"] == enrichment_snapshot, (
        "Round 1 repair: bzzoiroEnrichment must survive re-settlement."
    )
    assert repaired_r1["result"] == "hit"
    assert "voidReason" not in repaired_r1

    # ── Round 2: re-void ─────────────────────────────────────────────────────
    voided_r2 = _apply(repaired_r1, {
        "$set": {
            "status": "live",
            "result": None,
            "actualValue": None,
            "hitPct": None,
            "voidReason": "Stat correction: official box score revised",
        },
        "$unset": {"settledAt": "", "settledBy": ""},
    })
    assert voided_r2["tacticalContext"]["bzzoiroEnrichment"] == enrichment_snapshot, (
        "Round 2 void: bzzoiroEnrichment must survive the second void."
    )
    assert voided_r2["result"] is None
    assert voided_r2.get("voidReason") == "Stat correction: official box score revised"

    # ── Round 2: final repair ─────────────────────────────────────────────────
    final = _apply(voided_r2, {
        "$set": {
            "status": "settled",
            "result": "miss",
            "actualValue": 48,
            "hitPct": 0,
            "settledAt": "2026-08-01T13:00:00Z",
            "settledBy": "admin_repair_r2",
        },
        "$unset": {"voidReason": ""},
    })
    assert final["tacticalContext"]["bzzoiroEnrichment"] == enrichment_snapshot, (
        "Final repair: bzzoiroEnrichment must survive the full multi-round cycle."
    )
    assert final["result"] == "miss"
    assert "voidReason" not in final


def test_coverage_count_accurate_across_multi_round_void_repair_cycle():
    """_is_coverage_row and _is_eligible_row track a pick correctly through
    void → repair → re-void → final repair transitions.

    The pick has valid Bzzoiro labels throughout.  Coverage count (_is_coverage_row)
    must be True for VOID and both terminal outcomes.  Eligibility (_is_eligible_row)
    must be False for VOID and True only for HIT/MISS terminal states.
    """
    base_labels = {
        "bzzoiro_label": "High",
        "apifootball_label": "Moderate",
        "prop_type": "passes",
        "direction": "UNDER",
    }

    # ── Step 0: settled HIT (initial state) ──────────────────────────────────
    row_hit = {**base_labels, "outcome": "HIT"}
    assert _is_coverage_row(row_hit), "Initial HIT: must count for coverage."
    assert _is_eligible_row(row_hit), "Initial HIT: must be eligible for direction accuracy."

    # ── Step 1: voided (round 1) ─────────────────────────────────────────────
    row_void_r1 = {**base_labels, "outcome": "VOID"}
    assert _is_coverage_row(row_void_r1), (
        "Round 1 VOID: fixture is real; must still count for coverage."
    )
    assert not _is_eligible_row(row_void_r1), (
        "Round 1 VOID: no direction outcome; must be excluded from direction accuracy."
    )

    # ── Step 2: repaired to HIT (round 1 repair) ─────────────────────────────
    row_repaired_r1 = {**base_labels, "outcome": "HIT"}
    assert _is_coverage_row(row_repaired_r1), "Round 1 repair HIT: must count for coverage."
    assert _is_eligible_row(row_repaired_r1), "Round 1 repair HIT: must be eligible."

    # ── Step 3: re-voided (round 2) ──────────────────────────────────────────
    row_void_r2 = {**base_labels, "outcome": "VOID"}
    assert _is_coverage_row(row_void_r2), (
        "Round 2 VOID: still a real Bzzoiro-covered fixture; must count for coverage."
    )
    assert not _is_eligible_row(row_void_r2), (
        "Round 2 VOID: no direction outcome; must be excluded from direction accuracy."
    )

    # ── Step 4: final repair to MISS ─────────────────────────────────────────
    row_final_miss = {**base_labels, "outcome": "MISS"}
    assert _is_coverage_row(row_final_miss), "Final MISS: must count for coverage."
    assert _is_eligible_row(row_final_miss), "Final MISS: must be eligible for direction accuracy."


def test_nCovered_stays_accurate_across_multi_round_batch():
    """Simulate a corpus containing picks at every stage of the void/repair cycle.

    A realistic batch might contain one pick per state simultaneously (e.g. during
    a bulk repair run).  The coverage count must reflect only the real Bzzoiro
    fixtures, regardless of which settlement state each pick is in.
    """
    base = {"bzzoiro_label": "High", "apifootball_label": "High", "prop_type": "passes"}

    rows = [
        # Picks in their initial settled state — both correct predictions
        {**base, "direction": "UNDER", "outcome": "HIT"},    # eligible, correct
        {**base, "direction": "OVER",  "outcome": "HIT"},    # eligible, correct
        # Pick currently in void (round 1)
        {**base, "outcome": "VOID"},                          # coverage only, no direction
        # Pick re-settled after round-1 repair — correct prediction
        {**base, "direction": "UNDER", "outcome": "HIT"},    # eligible, correct
        # Pick in round-2 void
        {**base, "outcome": "VOID"},                          # coverage only, no direction
        # Pick at final repair — correct prediction
        {**base, "direction": "UNDER", "outcome": "HIT"},    # eligible, correct
        # Ineligible: missing Bzzoiro label (coverage gap)
        {"bzzoiro_label": None, "apifootball_label": "High", "prop_type": "passes",
         "outcome": "HIT"},
    ]

    result = evaluate_bzzoiro_pressure_evidence(rows)

    # 6 rows have valid labels (2 VOID + 4 HIT); 1 row has no Bzzoiro label.
    assert result["nSupplied"] == 7
    assert result["nCovered"] == 6, (
        "All rows with valid Bzzoiro labels count for coverage, "
        "including those currently in a VOID state."
    )
    # VOID rows do not contribute to direction accuracy.
    assert result["nPassProps"] == 4, (
        "Only the 4 HIT rows contribute to direction accuracy."
    )
    # All 4 direction rows are correct predictions (UNDER+HIT or OVER+HIT).
    assert result["directionAccuracyWithBzzoiro"] == 1.0


def test_void_outcome_case_variants_all_count_for_coverage():
    """Outcome strings returned by settlement paths can be mixed-case.
    All recognised VOID variants must count for coverage and be excluded from
    direction accuracy, matching the single-void tests for 'VOID' and 'void'.
    """
    base = {"bzzoiro_label": "High", "apifootball_label": "High",
            "prop_type": "passes", "direction": "UNDER"}

    for variant in ("VOID", "void", "Void"):
        row = {**base, "outcome": variant}
        assert _is_coverage_row(row), (
            f"outcome={variant!r} must count for coverage (_is_coverage_row)."
        )
        assert not _is_eligible_row(row), (
            f"outcome={variant!r} must be excluded from direction accuracy (_is_eligible_row)."
        )


def test_multi_round_void_rows_cannot_inflate_direction_accuracy():
    """A corpus padded with VOID rows must show the correct direction accuracy.

    If VOID rows were incorrectly treated as eligible, they could silently inflate
    or deflate directionAccuracyWithBzzoiro.  This test constructs a corpus where
    the void rows would produce an incorrect accuracy if counted.
    """
    base_eligible = {"bzzoiro_label": "High", "apifootball_label": "High",
                     "prop_type": "passes"}

    # 3 correct HIT rows + 5 VOID rows (which have no direction to count)
    rows = (
        [{**base_eligible, "direction": "UNDER", "outcome": "HIT"} for _ in range(3)]
        + [{"bzzoiro_label": "High", "apifootball_label": "High",
            "prop_type": "passes", "outcome": "VOID"} for _ in range(5)]
    )
    result = evaluate_bzzoiro_pressure_evidence(rows)

    assert result["nSupplied"] == 8
    assert result["nCovered"] == 8       # all 8 have valid labels
    assert result["nPassProps"] == 3     # only the 3 HIT rows have a direction outcome
    assert result["directionAccuracyWithBzzoiro"] == 1.0  # 3/3 correct
