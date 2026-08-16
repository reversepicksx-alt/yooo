from pathlib import Path

from routes.prediction_explanations import (
    _cache_key,
    _fallback,
    _generated_read_is_valid,
    _prompt,
)


ROOT = Path(__file__).resolve().parents[2]
SCAN_SOURCE = ROOT / "mobile" / "app" / "(tabs)" / "scan.tsx"


def _prediction(**overrides):
    prediction = {
        "playerName": "Leandro Cabrera",
        "playerRole": "CB / Stopper",
        "teamName": "Espanyol",
        "opponentName": "Levante",
        "venue": "home",
        "propType": "pass_attempts",
        "line": 51.5,
        "projection": 50,
        "recommendation": "UNDER",
        "confidenceScore": 59,
        "homeAvg": 46.3,
        "expectedPossession": {"home": 49.0, "away": 51.0},
        "h2hPlayerStats": {"avgVsOpponent": 41.3, "sampleSize": 3},
        "factorLedgerFingerprint": "ledger-a",
    }
    prediction.update(overrides)
    return prediction


def test_scan_read_path_calls_the_section_explanation_endpoint():
    source = SCAN_SOURCE.read_text(encoding="utf-8")

    assert "requestPredictionSectionExplanation(" in source
    assert "buildSectionExplanationSnapshot(pred as any)" in source
    assert "'read'," in source
    assert "response?.source === 'gemini'" in source
    assert "requestSectionExplanation('read', predictionState, true)" in source


def test_short_or_failed_generation_uses_deterministic_fallback_contract():
    prediction = _prediction()
    short_generation = "Leandro is projected UNDER."

    assert not _generated_read_is_valid(short_generation, prediction, "read")
    fallback = _fallback("read", prediction)
    assert "50 pass attempts" in fallback
    assert "51.5 line" in fallback
    assert "UNDER" in fallback


def test_read_prompt_freezes_final_projection_and_recommendation():
    prompt = _prompt("read", _prediction())

    assert "FINAL VERDICT ANCHOR" in prompt
    assert "never substitute the opposite side" in prompt
    assert "Never change, reverse, or second-guess" in prompt
    assert "projection stays below the line" in prompt


def test_read_prompt_allows_broad_tactical_mechanisms_without_fabrication():
    prompt = _prompt("read", _prediction())

    for term in (
        "build-up",
        "press resistance",
        "settled possession",
        "direct play",
        "defensive block",
        "passing-route availability",
        "game state",
    ):
        assert term in prompt
    assert "conditional game states" in prompt
    assert "team-level ppda or possession is not a one-to-one marking claim" in prompt.lower()


def test_explanation_cache_identity_includes_final_ledger_fingerprint():
    first = _cache_key("read", _prediction(factorLedgerFingerprint="ledger-a"))
    second = _cache_key("read", _prediction(factorLedgerFingerprint="ledger-b"))

    assert first != second


def test_incomplete_mechanism_is_not_presented_as_a_confirmed_team_fact():
    prediction = _prediction()
    generated = (
        "Leandro Cabrera projects 50 versus 51.5, so UNDER is final. "
        "If Espanyol uses direct play, pass routes could be suppressed. "
        "If Espanyol trails, circulation may rise, while the 41.3 H2H average supports "
        "the same UNDER direction."
    )

    assert _generated_read_is_valid(generated, prediction, "read")