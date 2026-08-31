from pathlib import Path

from engine_base import normalize_response
from universal_evidence import ensure_universal_evidence


def test_structured_sport_gets_sport_specific_position_and_role_packet():
    response = normalize_response({
        "sport": "nfl",
        "position": "WR",
        "propType": "receiving_yards",
        "line": 68.5,
        "projection": 71.2,
        "recommendation": "over",
        "pOver": 64,
        "pUnder": 36,
        "gameLogs": [{"value": 72}],
    })

    assert response["playerPosition"] == "WR"
    assert response["playerRole"] == "pass catcher"
    assert response["positionComparison"]["sport"] == "nfl"
    assert response["positionComparison"]["positionEvidenceType"] == "exact_position"
    assert response["positionComparison"]["comparisonMode"] == "unavailable"
    assert response["roleEvidencePacket"]["status"] == "partial"
    assert response["roleEvidencePacket"]["questions"][0].startswith("Does the player's listed position")


def test_sport_without_field_position_gets_explicit_role_unavailability_boundary():
    response = normalize_response({
        "sport": "atp",
        "propType": "aces",
        "line": 8.5,
        "projection": 9.0,
        "pOver": 55,
        "pUnder": 45,
    })

    assert response["playerPosition"] == ""
    assert response["playerRole"] == "singles player"
    assert response["positionComparison"]["positionEvidenceType"] == "unavailable"
    assert response["positionComparison"]["comparisonUnavailableReason"] == (
        "no_sport_specific_comparison_sample"
    )
    assert response["roleEvidencePacket"]["projectionInfluence"] == "shadow_only"
    assert "surface" in response["roleEvidencePacket"]["questions"][1].lower()


def test_existing_verified_packets_are_preserved_and_projection_is_unchanged():
    comparison = {
        "sport": "soccer",
        "comparisonMode": "same-role",
        "positionEvidenceType": "exact_position",
        "sampleSize": 4,
        "players": [{"name": "Comparable"}],
    }
    packet = {"status": "verified", "role": "Deep-Lying Playmaker", "questions": ["verified"]}
    response = {
        "sport": "soccer",
        "projection": 42.5,
        "recommendation": "under",
        "positionComparison": comparison,
        "roleEvidencePacket": packet,
    }

    normalized = ensure_universal_evidence(response)

    assert normalized["projection"] == 42.5
    assert normalized["recommendation"] == "under"
    assert normalized["positionComparison"] is comparison
    assert normalized["roleEvidencePacket"] is packet


def test_all_prediction_routes_use_the_shared_normalizer():
    routes = Path(__file__).parents[1] / "routes"
    predict_routes = [
        "predict.py", "mlb_routes.py", "nfl_routes.py", "nba_routes.py",
        "wnba_routes.py", "nhl_routes.py", "ncaaf_routes.py", "ncaab_routes.py",
        "ncaaw_routes.py", "cbase_routes.py", "atp_routes.py", "wta_routes.py",
        "pga_routes.py", "mma_routes.py", "f1_routes.py", "dota2_routes.py",
        "lol_routes.py", "cs2_routes.py", "ai_sports_routes.py",
    ]
    for route_name in predict_routes:
        source = (routes / route_name).read_text()
        assert "normalize_response" in source or "ensure_universal_evidence" in source, route_name