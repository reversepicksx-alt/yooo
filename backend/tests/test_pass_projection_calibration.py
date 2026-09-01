from datetime import datetime, timezone

import pytest

from config import STAT_FIELD_MAP
from routes.picks import SOCCER_STAT_MAP
from routes.predict import _fixture_matchup
from pass_projection_calibration import (
    _cache,
    _event_key,
    _norm_name,
    _role_bucket,
    lookup,
    walk_forward_validate,
)
from soccer_bdl_client import BDL_SOCCER_STAT_MAP, _norm


def test_canonical_pass_and_goalkeeper_aliases():
    assert STAT_FIELD_MAP["passes"] == "passes_total"
    assert STAT_FIELD_MAP["goalie_saves"] == "goals_saves"
    assert BDL_SOCCER_STAT_MAP["passes"] == "passes_total"
    assert BDL_SOCCER_STAT_MAP["goalie_saves"] == "goals_saves"
    sample = {"passes": {"total": 37}, "goals": {"saves": 4}}
    assert SOCCER_STAT_MAP["passes"](sample) == 37
    assert SOCCER_STAT_MAP["goalie_saves"](sample) == 4


def test_bdl_minutes_are_marked_estimated_when_missing():
    row = _norm({"appearances": 1, "passes_total": 42})
    assert row["minutes"] == 90
    assert row["_minutes_estimated"] is True
    assert row["_minutes_confirmed"] is False

    confirmed = _norm({"appearances": 1, "minutes_played": 28, "passes_total": 12})
    assert confirmed["minutes"] == 28
    assert confirmed["_minutes_estimated"] is False
    assert confirmed["_minutes_confirmed"] is True


def test_role_bucket_does_not_misclassify_short_tokens():
    assert _role_bucket("CM", "") == "CM"
    assert _role_bucket("GK", "") == "GK"
    assert _role_bucket("CB", "") == "CB_FB"
    assert _role_bucket("ST", "") == "AM_WIDE_ST"


def test_event_key_deduplicates_same_player_fixture_market():
    row = {
        "playerName": "  J. Player  ",
        "fixtureId": 123,
        "propType": "passes",
        "line": 35.5,
        "recommendation": "OVER",
    }
    assert _event_key(row) == (
        "j. player", "123", "passes", "35.5", "over"
    )


def test_calibration_falls_back_hierarchically_and_stays_inert_unloaded():
    previous = dict(_cache)
    try:
        _cache.update({
            "loaded": True,
            "buckets": {
                ("global_direction", "over"): {
                    "n": 20, "recentN": 12, "residual": -0.20, "shrink": 0.4
                }
            },
        })
        result = lookup(999, "CM", "", "over", 50)
        assert result["found"] is True
        assert result["bucket"] == ["global_direction", "over"]
        assert result["mode"] == "shadow"
        assert result["applied"] is False
        assert result["correction"] < 0
    finally:
        _cache.clear()
        _cache.update(previous)


def test_calibration_has_no_bucket_below_minimum_sample():
    previous = dict(_cache)
    try:
        _cache.update({
            "loaded": True,
            "buckets": {
                ("global_direction", "under"): {
                    "n": 9, "recentN": 9, "residual": 0.5, "shrink": 0.5
                }
            },
        })
        assert lookup(1, "CM", "", "under", 50)["found"] is False
    finally:
        _cache.clear()
        _cache.update(previous)


def test_walk_forward_validation_reports_metrics_without_leakage():
    rows = []
    for index in range(12):
        rows.append({
            "sport": "soccer",
            "playerName": f"Player {index}",
            "fixtureId": index,
            "propType": "passes",
            "line": 40,
            "recommendation": "over",
            "projectedValue": 50,
            "actualValue": 45,
            "result": "hit",
            "settledAt": datetime(2026, 1, index + 1, tzinfo=timezone.utc),
            "position": "CM",
        })

    report = walk_forward_validate(rows)
    assert report["eligibleSamples"] == 12
    assert report["evaluatedSamples"] == 12
    assert report["leakageViolations"] == 0
    assert report["raw"]["mae"] == 5.0
    assert report["calibrated"]["mae"] <= report["raw"]["mae"]
    assert report["raw"]["signedBias"] == -5.0
    assert report["directionSamples"] == 12


def test_fixture_matchup_uses_actual_fixture_opponent():
    fixture = {
        "fixture": {"id": 12345},
        "teams": {
            "home": {"id": 131, "name": "Corinthians"},
            "away": {"id": 134, "name": "Athletico-PR"},
        },
    }
    matchup = _fixture_matchup(fixture, 131)
    assert matchup["fixtureOpponentId"] == 134
    assert matchup["fixtureOpponentName"] == "Athletico-PR"
    assert matchup["playerIsHome"] is True