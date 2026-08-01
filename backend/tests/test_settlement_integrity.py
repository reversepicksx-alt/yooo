"""Regression tests for settled soccer result classification."""

from routes.picks import _has_soccer_stat_evidence, _settle_pick_result


def test_positive_pass_stat_overrides_stale_minutes_for_dnp_guard():
    pick = {
        "sport": "soccer",
        "propType": "pass_attempts",
        "actualValue": 65,
        "minutesPlayed": 19,
    }
    assert _has_soccer_stat_evidence(pick) is True
    assert _settle_pick_result(65, 56.5, {"recommendation": "under"}) == ("miss", None)


def test_zero_stat_does_not_prove_soccer_participation():
    pick = {
        "sport": "soccer",
        "propType": "passes",
        "actualValue": 0,
        "minutesPlayed": 19,
    }
    assert _has_soccer_stat_evidence(pick) is False


def test_stat_evidence_is_not_applied_to_non_soccer_picks():
    pick = {
        "sport": "cs2",
        "propType": "passes",
        "actualValue": 65,
        "minutesPlayed": 19,
    }
    assert _has_soccer_stat_evidence(pick) is False