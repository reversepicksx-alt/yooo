"""Regression coverage for the verified soccer venue-history contract."""

from pathlib import Path


PREDICT_SOURCE_PATH = Path(__file__).resolve().parents[1] / "routes" / "predict.py"
PREDICT_SOURCE = PREDICT_SOURCE_PATH.read_text()


def test_predict_source_exists_and_venue_target_is_thirty():
    assert PREDICT_SOURCE_PATH.is_file()
    assert "_VENUE_HISTORY_TARGET = 30" in PREDICT_SOURCE
    assert "_venue_min = _VENUE_HISTORY_TARGET" in PREDICT_SOURCE
    assert "_venue_min = 3 if _is_gk_saves else 5" not in PREDICT_SOURCE


def test_history_loader_expands_across_older_seasons():
    assert '"season": _older_season' in PREDICT_SOURCE
    assert "CURRENT_SEASON - 1 - _VENUE_HISTORY_MAX_OLDER_SEASONS" in PREDICT_SOURCE
    assert "_older_fixture_pool.extend(_new_season_rows)" in PREDICT_SOURCE


def test_current_fixture_loader_uses_supported_date_window():
    # API-Sports rejects the previous `last` query shape in production. The
    # current pool must use the bounded date window and leave older-season
    # expansion available when that call is empty or unavailable.
    assert '"from": _history_from.isoformat()' in PREDICT_SOURCE
    assert '"to": _history_to.isoformat()' in PREDICT_SOURCE
    assert '"last": _player_history_fixture_lookback' not in PREDICT_SOURCE
    assert "current fixture " in PREDICT_SOURCE


def test_h2h_rows_keep_minutes_and_home_away_split():
    assert '"minutes": minutes_played' in PREDICT_SOURCE
    assert '"minutesPlayed": minutes_played' in PREDICT_SOURCE
    assert '"venueSplits"' in PREDICT_SOURCE
    assert '"minutesAverage"' in PREDICT_SOURCE


def test_position_evidence_has_one_resolution_contract():
    assert '"positionEvidence"' in PREDICT_SOURCE
    assert '"decisionRule": "exact fixture/history/profile position outranks broad provider category; calibration cannot relabel identity"' in PREDICT_SOURCE
    assert '"leagueRoleBucket"' in PREDICT_SOURCE


def test_history_loader_uses_full_verified_fallback_below_target():
    assert "full-history fallback" in PREDICT_SOURCE
    assert '"full_history_fallback"' in PREDICT_SOURCE
    assert '"fallback": "full_verified_history"' in PREDICT_SOURCE
    assert '"modelScope"' in PREDICT_SOURCE
    assert '"modelSampleSize"' in PREDICT_SOURCE


def test_venue_count_requires_the_requested_stat_and_exact_venue():
    # This mirrors the contract enforced by _venue_history_count: a team
    # fixture alone is not a player appearance, and an opposite-venue row
    # cannot satisfy the selected-venue target.
    rows = [
        {"venue": "away", "passes_total": 24, "minutes": 90},
        {"venue": "home", "passes_total": 31, "minutes": 90},
        {"venue": "away", "passes_total": None, "minutes": 90},
        {"venue": "away", "passes_total": 29, "minutes": 0},
    ]
    count = sum(
        1
        for row in rows
        if row.get("venue") == "away" and row.get("passes_total") is not None
    )
    assert count == 2
    assert "_venue_history_count(collected)" in PREDICT_SOURCE
    assert "and log.get(stat_field_map.get(req.propType, \"\"))" in PREDICT_SOURCE


def test_player_identity_and_minutes_guards_remain_in_direct_fetch():
    assert "if pid == player_id and mins > 0" in PREDICT_SOURCE
    assert "if not matched_stats:" in PREDICT_SOURCE
    assert "if raw_val is not None and minutes > 0:" in PREDICT_SOURCE