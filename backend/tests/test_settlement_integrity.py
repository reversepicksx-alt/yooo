"""Regression tests for settled soccer result classification."""

from routes.picks import (
    SOCCER_STAT_MAP,
    SOCCER_STAT_PATHS,
    _has_soccer_stat_evidence,
    _settle_pick_result,
    _soccer_settlement_provenance,
)


def test_pass_attempts_uses_total_attempts_not_accurate_passes():
    official_stats = {
        "passes": {
            "total": 57,
            "accuracy": 91,
            "accurate": 52,
        }
    }
    assert SOCCER_STAT_MAP["pass_attempts"](official_stats) == 57
    assert SOCCER_STAT_MAP["passes"](official_stats) == 57
    assert SOCCER_STAT_PATHS["pass_attempts"] == "statistics.passes.total"
    assert SOCCER_STAT_PATHS["passes"] == "statistics.passes.total"


def test_accurate_passes_cannot_be_substituted_for_attempts():
    official_stats = {"passes": {"total": 57, "accurate": 52}}
    assert SOCCER_STAT_MAP["pass_attempts"](official_stats) != official_stats["passes"]["accurate"]


def test_verified_settlement_provenance_is_explicit():
    source = _soccer_settlement_provenance(
        provider="api-football",
        fixture_id=123,
        player_id=456,
        prop_type="pass_attempts",
        stat_path="statistics.passes.total",
        fixture_status="FT",
    )
    assert source["verified"] is True
    assert source["fixtureId"] == 123
    assert source["playerId"] == 456
    assert source["statPath"] == "statistics.passes.total"


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