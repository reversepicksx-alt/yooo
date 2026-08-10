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