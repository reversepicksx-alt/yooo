"""The API must expose H/A to older native bundles without an OTA/build."""

from pathlib import Path

from routes.predict import _legacy_h2h_display_date


ROOT = Path(__file__).resolve().parents[1]
PREDICT_SOURCE = (ROOT / "routes" / "predict.py").read_text()
COMPACT_SOURCE = (ROOT.parent / "mobile" / "components" / "CompactAnalysisBars.tsx").read_text()


def test_legacy_h2h_date_carries_home_or_away_marker():
    assert _legacy_h2h_display_date("2026-08-02T00:00:00Z", "home") == "2026-08H02T00:00:00Z"
    assert _legacy_h2h_display_date("2026-08-09T00:00:00Z", "away") == "2026-08A09T00:00:00Z"
    assert _legacy_h2h_display_date("", "home") == ""
    assert _legacy_h2h_display_date("2026-08H02T00:00:00Z", "home") == "2026-08H02T00:00:00Z"


def test_h2h_player_and_team_meeting_rows_use_legacy_compatible_date():
    assert PREDICT_SOURCE.count("_legacy_h2h_display_date(") >= 3
    assert '"date": _legacy_h2h_display_date(' in PREDICT_SOURCE


def test_current_bundle_still_uses_dedicated_marker_and_date_prefix():
    assert "venueMark(rowVenue(row))" in COMPACT_SOURCE
    assert "displayH2HDate(row.date)" in COMPACT_SOURCE
    assert "2026-08H02" in COMPACT_SOURCE