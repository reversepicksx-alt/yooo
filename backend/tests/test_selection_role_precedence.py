from ai_positions import _GENERIC_TO_SPECIFIC
from models import PredictionRequest
from tactical_evidence import preserve_selection_role


def test_midfielder_provider_category_accepts_grounded_winger_positions():
    assert {"LW", "RW"} <= _GENERIC_TO_SPECIFIC["Midfielder"]


def test_grounded_selection_role_survives_predicted_striker_observation():
    packet = preserve_selection_role(
        {
            "position": "LW",
            "role": "Traditional Winger",
            "source": "gemini_web_grounded",
            "confidence": "high",
            "evidence": ["grounded profile citation"],
        },
        {
            "position": "ST",
            "role": "Complete Forward",
            "source": "predicted_lineup_grid",
        },
        "predicted",
    )
    assert packet is not None
    assert packet["position"] == "LW"
    assert packet["role"] == "Traditional Winger"
    assert any("ST" in item for item in packet["evidence"])


def test_confirmed_current_lineup_is_allowed_to_replace_selection_role():
    packet = preserve_selection_role(
        {
            "position": "LW",
            "role": "Traditional Winger",
            "source": "gemini_web_grounded",
        },
        {"position": "ST", "role": "Complete Forward"},
        "confirmed",
    )
    assert packet is None


def test_confirmed_generic_lineup_does_not_replace_grounded_exact_role():
    packet = preserve_selection_role(
        {
            "position": "LW",
            "role": "Traditional Winger",
            "source": "gemini_web_grounded",
            "evidence": ["grounded profile citation"],
        },
        {"position": "DEF", "role": None, "source": "fixture_lineup_category"},
        "confirmed",
    )
    assert packet is not None
    assert packet["position"] == "LW"
    assert packet["role"] == "Traditional Winger"
    assert any("DEF" in item for item in packet["evidence"])


def test_prediction_request_carries_selection_role_provenance():
    request = PredictionRequest(
        email="test@example.com",
        token="test-token",
        playerName="Example",
        positionOverride="LW",
        roleOverride="Traditional Winger",
        positionSourceOverride="gemini_web_grounded",
        roleSourceOverride="gemini_web_grounded",
        roleConfidenceOverride="high",
        roleEvidenceOverride=["grounded profile citation"],
    )
    assert request.positionOverride == "LW"
    assert request.roleEvidenceOverride == ["grounded profile citation"]