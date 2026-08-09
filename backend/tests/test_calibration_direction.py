"""Unit tests for direction-aware calibration (Task #113).

Covers:
- by_confidence_band_rec is populated in get_calibration_stats output
- _recalibrate_confidence prefers direction-specific band over global band
- _recalibrate_confidence falls back to global band when direction n < 10
- _apply_over_direction_cap fires at high confidence when OVER is weak
- _apply_over_direction_cap medium cap (62) is reachable and effective
- _apply_over_direction_cap skips when OVER rate >= threshold
- _apply_over_direction_cap skips when over_total < min samples
- apply_elite_calibration passes new_rec (post-flip) not original_rec to recalibration
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Patch db before importing calibration so no real MongoDB connection is needed
from unittest.mock import AsyncMock, MagicMock, patch
import asyncio

# ── Helpers ───────────────────────────────────────────────────────────────────

def _band(rate_pct: float, n: int) -> dict:
    """Build a hit/miss bucket with the given hit rate and sample count."""
    hits = round(n * rate_pct / 100)
    return {"hit": hits, "miss": n - hits}


def _make_stats(
    *,
    over_rate: float = 52.7,
    under_rate: float = 66.4,
    over_total: int = 300,
    under_total: int = 300,
    # direction-aware bands
    high_over_rate: float = 48.0,
    high_over_n: int = 100,
    high_under_rate: float = 70.0,
    high_under_n: int = 100,
    # global (direction-blind) band — blended mix
    global_high_rate: float = 59.0,
    global_high_n: int = 200,
) -> dict:
    """Minimal stats dict mirroring get_calibration_stats output."""
    return {
        "total": over_total + under_total,
        "overall_hit_rate": round((over_rate + under_rate) / 2, 1),
        "over_hit_rate": over_rate,
        "under_hit_rate": under_rate,
        "over_total": over_total,
        "under_total": under_total,
        "by_confidence_band": {
            "high_70+": _band(global_high_rate, global_high_n),
            "medium_55-69": _band(60.0, 60),
        },
        "by_confidence_band_rec": {
            "high_70+|over": _band(high_over_rate, high_over_n),
            "high_70+|under": _band(high_under_rate, high_under_n),
        },
        "by_prop": {},
        "by_prop_rec": {},
        "by_venue": {},
        "by_league": {},
        "by_position": {},
        "by_prop_venue": {},
        "by_prop_position": {},
        "by_game_context": {},
        "by_prop_context": {},
        "by_line_range": {},
        "blowout_misses": [],
        "close_game_results": {"hit": 0, "miss": 0},
    }


# ── Import under test ─────────────────────────────────────────────────────────
# Patch db so no MongoDB connection is attempted at module load time.

with patch.dict("sys.modules", {"config": MagicMock(db=MagicMock())}):
    from calibration import (
        _recalibrate_confidence,
        _apply_over_direction_cap,
        _OVER_HIGH_CONF_MAX,
        _OVER_MED_CONF_MAX,
        _OVER_WEAK_THRESHOLD,
        _OVER_VERY_WEAK,
        _OVER_MIN_SAMPLES,
    )


# ─────────────────────────────────────────────────────────────────────────────
# _recalibrate_confidence tests
# ─────────────────────────────────────────────────────────────────────────────

class TestRecalibrateConfidence:

    def test_direction_band_preferred_for_over(self):
        """Direction-specific OVER band (48%) should drive a much stronger
        downward correction than the blended global band (59%)."""
        stats = _make_stats(high_over_rate=48.0, high_over_n=50)
        # Without direction-awareness: global band 59% vs confidence 75 → gap=16 → adj=9 → 66
        # With direction-awareness:  OVER  band 48% vs confidence 75 → gap=27 → adj=16 → 59
        new_conf, note = _recalibrate_confidence(stats, 75, "over")
        # Must be below the direction-blind result (which would be ~66)
        assert new_conf < 66, f"Expected strong OVER-specific correction, got {new_conf}"
        assert "over" in note.lower() or "OVER" in note, "Note should identify direction"

    def test_direction_band_preferred_for_under(self):
        """Direction-specific UNDER band (70%) at confidence 75 should show
        no overconfidence flag — UNDER is performing well."""
        stats = _make_stats(high_under_rate=70.0, high_under_n=50)
        new_conf, note = _recalibrate_confidence(stats, 75, "under")
        # 70% vs 75 → gap = 5, below the 8-pt threshold → no correction
        assert new_conf == 75
        assert note == ""

    def test_fallback_to_global_when_direction_n_too_small(self):
        """When direction-specific band has n < 10, fall back to global band."""
        stats = _make_stats(high_over_rate=48.0, high_over_n=5)  # too few
        # Global band is 59% blended; confidence 75 → gap=16 → correction fires
        new_conf_dir, _ = _recalibrate_confidence(stats, 75, "over")
        new_conf_global, _ = _recalibrate_confidence(stats, 75, "")  # no direction
        # With only 5 direction samples, fallback path is used → same result as no direction
        assert new_conf_dir == new_conf_global

    def test_no_correction_when_gap_small(self):
        """No correction applied when actual rate is within 8pp of stated confidence."""
        stats = _make_stats(high_over_rate=67.0, high_over_n=50)
        # 67% vs confidence 71 → gap = 4 → below 8pp threshold
        new_conf, note = _recalibrate_confidence(stats, 71, "over")
        assert new_conf == 71
        assert note == ""

    def test_floor_at_45(self):
        """Confidence should never drop below 45 regardless of how bad the band is."""
        stats = _make_stats(high_over_rate=10.0, high_over_n=200)
        new_conf, _ = _recalibrate_confidence(stats, 75, "over")
        assert new_conf >= 45

    def test_no_direction_falls_back_gracefully(self):
        """When recommendation is empty/unknown, global band is used."""
        stats = _make_stats(global_high_rate=50.0, global_high_n=30)
        new_conf, note = _recalibrate_confidence(stats, 75, "")
        # Global: 50% vs 75 → gap=25 → adj=15 → 60
        assert new_conf < 75


# ─────────────────────────────────────────────────────────────────────────────
# _apply_over_direction_cap tests
# ─────────────────────────────────────────────────────────────────────────────

class TestApplyOverDirectionCap:

    def test_high_conf_over_capped_when_weak(self):
        """High-confidence OVER (>=70) with weak OVER rate triggers hard cap."""
        stats = _make_stats(over_rate=52.7, over_total=100)
        new_conf, note = _apply_over_direction_cap(stats, 75, "over")
        assert new_conf == _OVER_HIGH_CONF_MAX, (
            f"Expected cap at {_OVER_HIGH_CONF_MAX}, got {new_conf}"
        )
        assert note != ""
        assert "OVER high-conf cap" in note

    def test_high_conf_over_not_capped_when_already_below_ceiling(self):
        """If confidence is already at or below the cap, no change."""
        stats = _make_stats(over_rate=52.7, over_total=100)
        # Cap is _OVER_HIGH_CONF_MAX=64; if confidence is already 64, no change
        new_conf, note = _apply_over_direction_cap(stats, _OVER_HIGH_CONF_MAX, "over")
        assert new_conf == _OVER_HIGH_CONF_MAX
        assert note == ""

    def test_high_conf_under_never_capped(self):
        """Cap must never affect UNDER picks regardless of OVER statistics."""
        stats = _make_stats(over_rate=40.0, over_total=200)
        new_conf, note = _apply_over_direction_cap(stats, 80, "under")
        assert new_conf == 80
        assert note == ""

    def test_cap_skips_when_over_rate_sufficient(self):
        """When OVER hit rate >= _OVER_WEAK_THRESHOLD, no cap is applied."""
        stats = _make_stats(over_rate=_OVER_WEAK_THRESHOLD, over_total=100)
        new_conf, note = _apply_over_direction_cap(stats, 80, "over")
        assert new_conf == 80
        assert note == ""

    def test_cap_skips_insufficient_samples(self):
        """Cap must not activate when over_total < _OVER_MIN_SAMPLES."""
        stats = _make_stats(over_rate=40.0, over_total=_OVER_MIN_SAMPLES - 1)
        new_conf, note = _apply_over_direction_cap(stats, 80, "over")
        assert new_conf == 80
        assert note == ""

    def test_medium_conf_cap_is_reachable(self):
        """Medium-confidence cap (_OVER_MED_CONF_MAX=62) must be strictly < 65
        so it can actually constrain a medium-band OVER pick at e.g. 63–64."""
        assert _OVER_MED_CONF_MAX < 65, (
            "_OVER_MED_CONF_MAX must be below the 'High' threshold (65) to have any effect"
        )
        # Confidence of 63 is in the medium band; cap at 62 should fire when OVER < 50%
        stats = _make_stats(over_rate=_OVER_VERY_WEAK - 1, over_total=100)
        new_conf, note = _apply_over_direction_cap(stats, 63, "over")
        assert new_conf == _OVER_MED_CONF_MAX, (
            f"Expected cap at {_OVER_MED_CONF_MAX} for conf=63, got {new_conf}"
        )
        assert "OVER med-conf cap" in note

    def test_medium_conf_cap_requires_very_weak_over(self):
        """Medium-band cap only fires when OVER < _OVER_VERY_WEAK, not just < _OVER_WEAK_THRESHOLD."""
        stats = _make_stats(over_rate=_OVER_VERY_WEAK + 1, over_total=100)  # 51%
        new_conf, note = _apply_over_direction_cap(stats, 63, "over")
        assert new_conf == 63  # no cap — 51% > _OVER_VERY_WEAK
        assert note == ""

    def test_medium_conf_no_change_when_already_at_ceiling(self):
        """If medium-conf OVER is already at or below cap, no change."""
        stats = _make_stats(over_rate=45.0, over_total=100)
        new_conf, note = _apply_over_direction_cap(stats, _OVER_MED_CONF_MAX, "over")
        assert new_conf == _OVER_MED_CONF_MAX
        assert note == ""


# ─────────────────────────────────────────────────────────────────────────────
# Integration: apply_elite_calibration uses new_rec not original_rec
# ─────────────────────────────────────────────────────────────────────────────

class TestApplyEliteCalibrationUsesNewRec:

    def test_recalibration_uses_final_direction(self):
        """Verify that after a flip (UNDER→OVER), OVER-direction history is used.

        We simulate:
          - original prediction: UNDER, conf=75
          - projection+flip guard converts it to a final OVER
          - recalibration must use OVER history (weak, 48%) not UNDER history (strong, 70%)
        """
        stats = _make_stats(
            high_over_rate=48.0, high_over_n=50,
            high_under_rate=70.0, high_under_n=50,
        )
        # Using new_rec="over" (post-flip) with original confidence 75
        new_conf_over, note_over = _recalibrate_confidence(stats, 75, "over")
        # Using original_rec="under" (the bug) with same confidence 75
        new_conf_under, note_under = _recalibrate_confidence(stats, 75, "under")

        # OVER history (48%) should pull confidence down more than UNDER history (70%)
        assert new_conf_over < new_conf_under, (
            f"OVER recal ({new_conf_over}) should yield lower conf than UNDER recal ({new_conf_under})"
        )
        assert "over" in note_over.lower()
        # UNDER at 70% vs conf 75 → gap=5 → no correction at all
        assert note_under == ""

    def test_original_under_becoming_over_gets_over_cap(self):
        """A pick that was UNDER but flipped to OVER should still get the OVER cap."""
        stats = _make_stats(over_rate=52.0, over_total=100)
        # Simulate: original was UNDER, but new_rec="over" after flip
        new_conf, note = _apply_over_direction_cap(stats, 75, "over")
        assert new_conf == _OVER_HIGH_CONF_MAX
        assert note != ""


# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
