import asyncio

from saved_sport_analysis import merge_saved_analysis
from routes import mlb_routes, nfl_routes


class FakeCollection:
    def __init__(self, row=None):
        self.row = row

    async def find_one(self, *_args, **_kwargs):
        return self.row


class FakeDb:
    def __init__(self, *, pick, prediction=None):
        self.sessions = FakeCollection({"email": "subscriber@example.com"})
        self.picks = FakeCollection(pick)
        self.mlb_predictions = FakeCollection(prediction)
        self.predictions = FakeCollection(prediction)


def test_saved_snapshot_wins_over_rotated_prediction():
    pick = {
        "pickId": "mlb-1",
        "email": "subscriber@example.com",
        "playerName": "Saved Batter",
        "propType": "hits",
        "line": 1.5,
        "projection": 1.8,
        "recommendation": "OVER",
        "gameLogs": [{"date": "2026-08-29", "value": 2}],
    }
    rotated = {
        "playerName": "Saved Batter",
        "propType": "hits",
        "line": 0.5,
        "projection": 0.7,
        "recommendation": "UNDER",
    }

    merged = merge_saved_analysis(pick, rotated, "mlb")

    assert merged["line"] == 1.5
    assert merged["projection"] == 1.8
    assert merged["recommendation"] == "OVER"
    assert merged["gameLogs"] == pick["gameLogs"]
    assert merged["analysisSource"] == "saved_pick_snapshot"
    assert "email" not in merged


def test_mlb_analysis_endpoint_returns_saved_snapshot(monkeypatch):
    pick = {
        "pickId": "mlb-1",
        "email": "subscriber@example.com",
        "sport": "mlb",
        "playerName": "Saved Batter",
        "playerId": 42,
        "teamName": "Chicago",
        "opponentName": "Detroit",
        "propType": "hits",
        "line": 1.5,
        "projection": 1.8,
        "recommendation": "OVER",
        "position": "OF",
        "gameLogs": [{"date": "2026-08-29", "value": 2}],
    }
    monkeypatch.setattr(mlb_routes, "db", FakeDb(pick=pick))

    response = asyncio.run(mlb_routes.get_mlb_saved_analysis(
        "mlb-1", "subscriber@example.com", "session-token"
    ))

    assert response["found"] is True
    assert response["sport"] == "mlb"
    assert response["analysis"]["propType"] == "hits"
    assert response["analysis"]["line"] == 1.5
    assert response["analysis"]["gameLogs"][0]["value"] == 2


def test_nfl_analysis_endpoint_isolated_to_nfl_pick(monkeypatch):
    pick = {
        "pickId": "nfl-1",
        "email": "subscriber@example.com",
        "sport": "nfl",
        "playerName": "Saved Receiver",
        "playerId": 84,
        "teamName": "Chicago",
        "opponentName": "Green Bay",
        "propType": "receiving_yards",
        "line": 64.5,
        "projection": 71.2,
        "recommendation": "OVER",
        "position": "WR",
        "gameLogs": [{"date": "2026-08-28", "value": 88}],
        "gameTotal": 44.5,
    }
    monkeypatch.setattr(nfl_routes, "db", FakeDb(pick=pick))

    response = asyncio.run(nfl_routes.get_nfl_saved_analysis(
        "nfl-1", "subscriber@example.com", "session-token"
    ))

    assert response["found"] is True
    assert response["sport"] == "nfl"
    assert response["analysis"]["position"] == "WR"
    assert response["analysis"]["gameTotal"] == 44.5
    assert response["analysis"]["line"] == 64.5