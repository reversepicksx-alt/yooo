"""Regression tests for the MLB projection engine contract."""

import sys

sys.path.insert(0, "/app/backend")

from mlb_engine import compute_mlb_projection


def _batter_logs():
    return [
        {
            "game_id": 100 - i,
            "hits": 1 if i % 2 == 0 else 0,
            "runs": 1 if i % 3 == 0 else 0,
            "rbi": 1 if i % 4 == 0 else 0,
            "at_bats": 4,
            "plate_appearances": 4,
            "hr": 0,
            "bb": 0,
            "stolen_bases": 0,
            "doubles": 0,
        }
        for i in range(8)
    ]


def _batter_season_stats():
    return {
        "batting_gp": 100,
        "batting_h": 100,
        "batting_r": 60,
        "batting_rbi": 70,
        "batting_hr": 20,
        "batting_bb": 40,
        "batting_sb": 5,
        "batting_2b": 20,
    }


def test_mlb_composite_prop_consumes_current_monte_carlo_contract():
    result = compute_mlb_projection(
        game_logs=_batter_logs(),
        season_stats=_batter_season_stats(),
        prop_type="hits_runs_rbis",
        line=0.5,
        venue="away",
        position="C",
        park_team="Atlanta Braves",
    )

    assert result["sport"] == "mlb"
    assert result["propType"] == "hits_runs_rbis"
    assert result["recommendation"] in {"OVER", "UNDER"}
    assert 0 <= result["pOver"] <= 100
    assert 0 <= result["pUnder"] <= 100
    assert result["confidenceInterval"]["low"] == result["range80"][0]
    assert result["confidenceInterval"]["high"] == result["range80"][1]
    assert result["range80"][0] <= result["range60"][0]
    assert result["range60"][1] <= result["range80"][1]
    assert result["distribution"]["distributionType"] == "negative_binomial"


def test_mlb_standard_count_prop_still_returns_distribution():
    result = compute_mlb_projection(
        game_logs=_batter_logs(),
        season_stats=_batter_season_stats(),
        prop_type="hits",
        line=0.5,
        venue="away",
        position="C",
    )

    assert result["distribution"]["mostLikelyValue"] == result["mostLikelyValue"]
    assert len(result["range60"]) == 2
    assert len(result["range80"]) == 2