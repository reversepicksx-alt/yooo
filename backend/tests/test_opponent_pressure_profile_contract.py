"""Regression coverage for recent-opponent pressure profiles."""

from pathlib import Path

import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bayesian_engine import compute_press_intensity_score


PREDICT_SOURCE = (ROOT / "routes" / "predict.py").read_text()
COMPACT_SOURCE = (
    ROOT.parent / "mobile" / "components" / "CompactAnalysisBars.tsx"
).read_text()
ANALYSIS_SOURCE = (
    ROOT.parent / "mobile" / "components" / "AnalysisCards.tsx"
).read_text()


def test_unavailable_pressure_never_has_a_numeric_zero_score():
    packet = compute_press_intensity_score(
        [{"opponentTotalPasses": 500, "possession": 55}]
    )

    assert packet["available"] is False
    assert packet["score"] is None
    assert packet["score100"] is None


def test_recent_profiles_require_five_completed_matches_and_are_versioned():
    assert '_OPPONENT_PRESSURE_MATCH_TARGET = 5' in PREDICT_SOURCE
    assert '_OPPONENT_PRESSURE_CANDIDATE_LIMIT = 8' in PREDICT_SOURCE
    assert 'profile_version = "opponent-pressure-v4"' in PREDICT_SOURCE
    assert 'profile_cache_prefix = "opp_press_profile_v4_"' in PREDICT_SOURCE
    assert 'if len(fixture_pool) < target_matches:' in PREDICT_SOURCE
    assert '"sampleTarget": target_matches' in PREDICT_SOURCE
    assert 'done, pending_tasks = await aio.wait(' in PREDICT_SOURCE
    assert 'aio.create_task(finish_remaining())' in PREDICT_SOURCE


def test_one_profile_is_reused_for_each_history_row_of_an_opponent():
    assert 'cached_profiles.get(opponent_key)' in PREDICT_SOURCE
    assert 'for row in rows:' in PREDICT_SOURCE
    assert '"profileScope": "opponent_recent_matchups"' in PREDICT_SOURCE
    assert '"projectionInfluence": "explanation_only"' in PREDICT_SOURCE


def test_mobile_does_not_render_unavailable_pressure_as_zero_over_one_hundred():
    assert "available && Number.isFinite(Number(pressIntensity?.score100))" in ANALYSIS_SOURCE
    assert "{score != null ? `INDEX ${score}/100 · ${label}` : label}" in ANALYSIS_SOURCE
    assert "NO VERIFIED OPPONENT SAMPLE" in COMPACT_SOURCE
    assert "N=${Number(pressureProfile?.sampleTarget" in COMPACT_SOURCE
    assert "Reverse Picks Pressure Index" in COMPACT_SOURCE
    assert "raw provider statistics are audit inputs, not pressure scores" in COMPACT_SOURCE
    assert "custom Reverse Picks Pressure Index" in COMPACT_SOURCE
    assert "Inputs: ${inputParts}." not in COMPACT_SOURCE