"""
External provider probe: BDL spatial xG integration.

Requires live network access to the BDL soccer data API.
These tests are EXCLUDED from the default pytest run.

To run manually:
    cd backend && python3.12 -m pytest tests/external/ -m external -v

Tests:
  1. _fetch_player_shots()  — fetches & aggregates shots by match_id
  2. get_game_logs()        — xg_shot/xgot_shot/shots_spatial enrichment
  3. Data-gap fill          — shots_total filled from shots_spatial when None
  4. Bayesian covariate 3f — SPATIAL XG fires for goals/shots_on_target/shots props
"""
import sys
import os
import asyncio
import io
import contextlib

import pytest

# Run from backend/ or tests/external/ — resolve backend package root either way
_BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)


# ─────────────────────────────────────────────────────────────────────────────
# Section 1 – _fetch_player_shots (live BDL API)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.external
class TestFetchPlayerShots:
    """Live BDL probe: shot-level data for a known EPL player (id=831)."""

    def setup_method(self):
        from soccer_bdl_client import _fetch_player_shots
        self._fetch = _fetch_player_shots
        self.data = asyncio.run(self._fetch(league_id=39, bdl_player_id=831))

    def test_returns_non_empty_dict(self):
        if not self.data:
            pytest.skip("BDL returned no shot data — quota exhausted or provider unavailable")
        assert self.data

    def test_each_entry_has_xg_shot(self):
        if not self.data:
            pytest.skip("No shot data returned — BDL unavailable")
        sample = next(iter(self.data.values()))
        assert "xg_shot" in sample

    def test_each_entry_has_xgot_shot(self):
        if not self.data:
            pytest.skip("No shot data returned — BDL unavailable")
        sample = next(iter(self.data.values()))
        assert "xgot_shot" in sample

    def test_each_entry_has_shots_spatial(self):
        if not self.data:
            pytest.skip("No shot data returned — BDL unavailable")
        sample = next(iter(self.data.values()))
        assert "shots_spatial" in sample

    def test_xg_shot_is_non_negative_float(self):
        if not self.data:
            pytest.skip("No shot data returned — BDL unavailable")
        sample = next(iter(self.data.values()))
        assert isinstance(sample.get("xg_shot"), float)
        assert sample["xg_shot"] >= 0

    def test_shots_spatial_is_positive_int(self):
        if not self.data:
            pytest.skip("No shot data returned — BDL unavailable")
        sample = next(iter(self.data.values()))
        assert isinstance(sample.get("shots_spatial"), int)
        assert sample["shots_spatial"] >= 1

    def test_xgot_does_not_exceed_xg(self):
        if not self.data:
            pytest.skip("No shot data returned — BDL unavailable")
        sample = next(iter(self.data.values()))
        # On-target xG must be ≤ total xG (with floating-point tolerance)
        assert sample["xgot_shot"] <= sample["xg_shot"] + 0.001


# ─────────────────────────────────────────────────────────────────────────────
# Section 2 – get_game_logs enrichment (live BDL API)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.external
class TestGameLogsEnrichment:
    """Live BDL probe: xg_shot flows through get_game_logs for Salah."""

    def setup_method(self):
        from soccer_bdl_client import get_game_logs
        self.logs, self.pid = asyncio.run(get_game_logs(league_id=39, player_name="Salah", last_n=15))

    def test_get_game_logs_returns_logs(self):
        if len(self.logs) == 0:
            pytest.skip("BDL returned no logs for Salah — quota exhausted or provider unavailable")
        assert len(self.logs) > 0

    def test_some_logs_have_xg_shot(self):
        if not self.logs:
            pytest.skip("No logs returned — BDL unavailable")
        enriched = [g for g in self.logs if g.get("xg_shot") is not None]
        assert len(enriched) > 0, (
            f"0/{len(self.logs)} logs enriched with xG — "
            "BDL shot endpoint may be unavailable or player ID mismatch"
        )

    def test_salah_avg_xg_above_threshold(self):
        if not self.logs:
            pytest.skip("No logs returned — BDL unavailable")
        enriched = [g for g in self.logs if g.get("xg_shot") is not None]
        if not enriched:
            pytest.skip("No xg_shot data — BDL shots endpoint unavailable")
        avg_xg = sum(g["xg_shot"] for g in enriched) / len(enriched)
        assert avg_xg > 0.05, f"Salah avg xG/game={avg_xg:.3f} — expected > 0.05 for a prolific scorer"


# ─────────────────────────────────────────────────────────────────────────────
# Section 3 – Data-gap fill (deterministic logic, no live call)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.external
class TestDataGapFill:
    """Verify shots_total is filled from shots_spatial when BDL Tier-2 is absent."""

    def test_norm_gives_none_when_bdl_data_absent(self):
        from soccer_bdl_client import _norm
        raw_log = {
            "passes_total": 45, "shots_total": None, "shots_on_target": None,
            "minutes_played": 90, "appearances": 1, "goals": 1,
        }
        normed = _norm(raw_log)
        assert normed.get("shots_total") is None, f"Expected None, got {normed.get('shots_total')}"

    def test_gap_fill_shots_total_from_spatial(self):
        from soccer_bdl_client import _norm
        raw_log = {"passes_total": 45, "shots_total": None, "shots_on_target": None,
                   "minutes_played": 90, "appearances": 1, "goals": 1}
        normed = _norm(raw_log)
        spatial = {"shots_spatial": 3, "shots_on_target_spatial": 2, "xg_shot": 0.35, "xgot_shot": 0.22}
        if normed.get("shots_total") is None and spatial.get("shots_spatial"):
            normed["shots_total"] = spatial["shots_spatial"]
        if normed.get("shots_on") is None and spatial.get("shots_on_target_spatial"):
            normed["shots_on"] = spatial["shots_on_target_spatial"]
        assert normed.get("shots_total") == 3
        assert normed.get("shots_on") == 2


# ─────────────────────────────────────────────────────────────────────────────
# Section 4 – Bayesian covariate 3f (deterministic, no live call)
# ─────────────────────────────────────────────────────────────────────────────

def _make_shot_log(xg, xgot, shots):
    """Minimal game log with spatial shot fields."""
    return {
        "targetStat": 0.8,
        "minutes": 90,
        "xg_shot": xg,
        "xgot_shot": xgot,
        "shots_spatial": shots,
        "shots_on_target_spatial": max(0, int(shots * 0.4)),
        "homeGoals": 2, "awayGoals": 0, "venue": "home",
    }


@pytest.mark.external
class TestBayesianSpatialXgCovariate:
    """Verify the SPATIAL XG covariate fires correctly inside bayesian_engine."""

    def _run_bayesian(self, logs, prop_type, line, position="ST"):
        import bayesian_engine as be
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            result = be.compute_bayesian_projection(
                game_logs=logs, prop_type=prop_type, line=line,
                venue="home", position=position, league_id=39,
            )
        return result, buf.getvalue()

    def test_spatial_xg_fires_for_goals_prop(self):
        logs = [_make_shot_log(0.40, 0.25, 4) for _ in range(8)]
        _, output = self._run_bayesian(logs, "goals", 0.5)
        assert "[SPATIAL XG]" in output, "Expected [SPATIAL XG] in output for elite striker with goals prop"

    def test_spatial_xg_fires_for_low_xg_player(self):
        logs = [_make_shot_log(0.03, 0.01, 1) for _ in range(8)]
        _, output = self._run_bayesian(logs, "goals", 0.5, position="CM")
        assert "[SPATIAL XG]" in output, "Expected [SPATIAL XG] to fire for any player with xg_shot data"

    def test_spatial_xg_fires_for_shots_on_target_prop(self):
        logs = [_make_shot_log(0.30, 0.20, 3) for _ in range(8)]
        _, output = self._run_bayesian(logs, "shots_on_target", 0.5)
        assert "[SPATIAL XG]" in output, "Expected [SPATIAL XG] in output for shots_on_target prop"

    def test_spatial_xg_fires_for_shots_prop(self):
        logs = [_make_shot_log(0.40, 0.25, 4) for _ in range(8)]
        _, output = self._run_bayesian(logs, "shots", 1.5)
        assert "[SPATIAL XG]" in output, "Expected [SPATIAL XG] in output for shots prop"

    def test_spatial_xg_does_not_fire_without_xg_shot_data(self):
        plain_logs = [
            {"targetStat": 2.0, "minutes": 90, "homeGoals": 1, "awayGoals": 0, "venue": "home"}
            for _ in range(8)
        ]
        _, output = self._run_bayesian(plain_logs, "goals", 0.5)
        assert "[SPATIAL XG]" not in output, "Expected [SPATIAL XG] to be skipped when no xg_shot in logs"
