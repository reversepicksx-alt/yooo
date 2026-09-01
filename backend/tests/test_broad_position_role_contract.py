"""Broad provider positions must never be presented as exact tactical roles."""

from pathlib import Path

from ai_positions import _stat_fingerprint_role


ROOT = Path(__file__).resolve().parents[1]
API_SOURCE = (ROOT.parent / "mobile" / "lib" / "api.ts").read_text()


def test_stat_fingerprint_rejects_all_broad_categories():
    stats = {
        "appearances": 20,
        "passes_total": 1200,
        "key_passes": 80,
        "dribbles_attempts": 80,
        "shots_total": 80,
        "goals_total": 20,
        "tackles_total": 80,
        "clearances": 80,
    }
    for category in ("Goalkeeper", "Defender", "Midfielder", "Attacker", "Forward"):
        assert _stat_fingerprint_role(category, stats) is None


def test_mobile_saved_payload_suppresses_roles_for_broad_positions():
    assert "const BROAD_POSITION_LABELS = new Set" in API_SOURCE
    assert "if (BROAD_POSITION_LABELS.has(normalizedPosition)) return undefined" in API_SOURCE
    assert "playerRole: customerRole(raw.player?.position, raw.player?.role)" in API_SOURCE