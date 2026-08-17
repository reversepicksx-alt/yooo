"""Regression coverage for canonical player names at prediction/save boundaries."""

from models import PredictionRequest
from routes.picks import _canonical_saved_player_name
from routes.predict import _normalize_prediction_identity


def _request(**overrides):
    values = {
        "email": "test@example.com",
        "token": "test-token",
        "playerId": 12345,
        "playerName": "Callum Craig",
        "teamId": 999,
        "teamName": "Wrexham",
    }
    values.update(overrides)
    return PredictionRequest(**values)


def test_provider_first_last_name_overrides_stale_prediction_label():
    prediction = {
        "playerName": "Callum Craig",
        "player": {
            "id": 12345,
            "name": "C. Doyle",
            "firstname": "Callum",
            "lastname": "Doyle",
        },
    }

    result = _normalize_prediction_identity(prediction, _request())

    assert result["playerName"] == "Callum Doyle"


def test_saved_pick_prefers_canonical_top_level_name_over_stale_nested_name():
    pick = {
        "canonicalPlayerName": "Callum Doyle",
        "playerName": "Callum Doyle",
        "player": {"id": 12345, "name": "Callum Craig"},
    }

    assert _canonical_saved_player_name(pick) == "Callum Doyle"


def test_saved_pick_repairs_stale_top_level_name_from_provider_name_parts():
    pick = {
        "playerName": "Callum Craig",
        "player": {
            "id": 12345,
            "name": "C. Doyle",
            "firstname": "Callum Craig",
            "lastname": "Doyle",
        },
    }

    assert _canonical_saved_player_name(pick) == "Callum Craig Doyle"