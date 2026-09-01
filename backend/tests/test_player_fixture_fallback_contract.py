from pathlib import Path


PREDICT_SOURCE = Path(__file__).resolve().parents[1] / "routes" / "predict.py"


def test_player_history_fallback_uses_verified_team_fixture_query():
    assert PREDICT_SOURCE.exists()
    source = PREDICT_SOURCE.read_text()

    assert '"fixtures", {"player": req.playerId' not in source
    assert '"fixtures",\n                        {"team": actual_team_id, "last": 40, "status": "FT"},' in source
    assert "verified club fixtures" in source