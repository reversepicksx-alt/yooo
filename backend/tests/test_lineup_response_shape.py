from routes.predict import _api_response_list, _lineup_player_status


def test_api_response_list_accepts_helper_list_output():
    payload = [{"team": {"id": 1603}, "startXI": []}]

    assert _api_response_list(payload) == payload


def test_api_response_list_accepts_raw_api_envelope():
    payload = [{"team": {"id": 1603}, "startXI": []}]

    assert _api_response_list({"response": payload}) == payload


def test_api_response_list_rejects_invalid_shapes():
    assert _api_response_list(None) == []
    assert _api_response_list({"response": {}}) == []


def test_lineup_player_status_detects_confirmed_starter_from_list():
    payload = [{
        "startXI": [{"player": {"id": 6236, "name": "Andrés Cubas"}}],
        "substitutes": [],
    }]

    assert _lineup_player_status(payload, 6236) == "starting"


def test_lineup_player_status_accepts_raw_api_envelope():
    payload = {
        "response": [{
            "startXI": [],
            "substitutes": [{"player": {"id": 6236}}],
        }]
    }

    assert _lineup_player_status(payload, 6236) == "substitute"
    assert _lineup_player_status(payload, 999999) == "not_in_squad"