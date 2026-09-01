"""Regression coverage for the verified Iñigo Vicente identity."""

from ai_positions import _MANUAL_EXACT_PROFILES


def test_inigo_vicente_has_identity_keyed_winger_profile():
    profile = _MANUAL_EXACT_PROFILES[554362]

    assert profile["specificPosition"] == "LW"
    assert profile["role"] == "Traditional Winger"
    assert profile["source"] == "manual_override"