"""Regression coverage for H2H venue provenance and display contracts."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PREDICT_SOURCE = (ROOT / "routes" / "predict.py").read_text()
SCAN_SOURCE = (Path(__file__).resolve().parents[2] / "mobile" / "app" / "(tabs)" / "scan.tsx").read_text()


def test_player_h2h_rows_are_built_from_fixture_home_away_identity():
    assert 'player_is_home = (home_id == actual_team_id)' in PREDICT_SOURCE
    assert '"venue": venue_in_match' in PREDICT_SOURCE


def test_h2h_card_renders_home_away_marker():
    assert "const venue = row.venue === 'home' || row.venue === 'away'" in SCAN_SOURCE
    assert "{date} · {venue}" in SCAN_SOURCE


def test_h2h_history_uses_bounded_direct_pairing_without_recent_season_cutoff():
    function_source = PREDICT_SOURCE.split(
        "async def get_h2h_history", 1
    )[1].split("async def get_player_data", 1)[0]
    assert '"last": pair_limit' in function_source
    assert '"season": season' not in function_source
    assert "_merge_h2h_fixtures(direct_response" in function_source


def test_h2h_player_appearance_still_requires_exact_identity_and_positive_minutes():
    assert "_normalize_provider_player_id(req.playerId)" in PREDICT_SOURCE
    assert "if minutes_played <= 0:" in PREDICT_SOURCE
    assert '"verifiedAppearances": len(h2h_player_stats)' in PREDICT_SOURCE