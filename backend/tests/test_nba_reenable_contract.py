"""Focused regression coverage for the re-enabled NBA prediction path."""

import asyncio
from datetime import datetime, timezone

import pytest

import ai_engine
import nba_client
import nba_engine
from routes import nba_routes
from routes import auth as auth_routes


def _logs(values=(24, 21, 27, 19, 25, 23, 26, 22)):
    return [
        {
            "date": f"2025-12-{20 - index:02d}",
            "game_id": 900 + index,
            "venue": "home" if index % 2 == 0 else "away",
            "opponent": "OPP",
            "won": index % 3 != 0,
            "home_score": 110 + index,
            "away_score": 104 + index,
            "minutes": 32.0,
            "pts": value,
            "reb": 6,
            "ast": 5,
            "stl": 1,
            "blk": 1,
            "tov": 2,
            "fg3m": 3,
            "pts_reb_ast": value + 11,
            "pts_reb": value + 6,
            "pts_ast": value + 5,
            "reb_ast": 11,
            "stl_blk": 2,
            "fantasy_pts": value + 18,
        }
        for index, value in enumerate(values)
    ]


def test_nba_engine_prop_surface_uses_canonical_keys():
    expected = {
        "points",
        "rebounds",
        "assists",
        "steals",
        "blocks",
        "three_pointers",
        "turnovers",
        "fantasy_points",
        "pts_reb_ast",
        "pts_reb",
        "pts_ast",
        "reb_ast",
        "stl_blk",
    }
    assert expected.issubset(nba_engine.NBA_PROPS)


def test_nba_projection_and_probability_keep_model_direction():
    result = nba_engine.compute_nba_projection(
        game_logs=_logs(),
        prop_type="points",
        line=40,
        venue="home",
    )

    assert "error" not in result
    assert result["projection"] < 40
    assert result["pUnder"] > result["pOver"]


def test_nba_route_returns_shared_contract_and_exact_identity(monkeypatch):
    async def run():
        async def fake_verify_session(_req):
            return {"valid": True, "access_type": "Premium"}

        player = {
            "id": 7,
            "first_name": "Test",
            "last_name": "Player",
            "position": "G",
            "team": {"id": 10, "full_name": "Test Team"},
        }
        logs = _logs()

        async def fake_get_player(_player_id):
            return player

        async def fake_logs(_player_id, _season):
            return logs

        async def fake_averages(_player_id, _season):
            return {"pts": 23.0}

        monkeypatch.setattr(auth_routes, "verify_session", fake_verify_session)
        monkeypatch.setattr(nba_routes.nba_client, "get_player", fake_get_player)
        monkeypatch.setattr(nba_routes.nba_client, "get_player_game_logs", fake_logs)
        monkeypatch.setattr(nba_routes.nba_client, "get_season_averages", fake_averages)

        response = await nba_routes.nba_predict(
            nba_routes.NbaPredictRequest(
                email="subscriber@example.com",
                token="session",
                playerName="Test Player",
                playerId=7,
                propType="points",
                line=40,
                venue="away",
                opponentName="Opponent",
                gameId=12345,
                gameDate="2025-12-20",
                season=2025,
            )
        )

        assert response["sport"] == "nba"
        assert response["gameId"] == 12345
        assert response["fixtureId"] == 12345
        assert response["season"] == 2025
        assert response["gameLogs"][0]["value"] == logs[0]["pts"]
        assert response["gameLogs"][0]["gameId"] == logs[0]["game_id"]
        assert response["matchupOverview"]["playerIsHome"] is False
        assert "riskSignals" in response
        assert "projection" in response
        assert "pOver" in response and "pUnder" in response
        assert response["recommendation"] in {"over", "under"}

    asyncio.run(run())


def test_nba_settlement_requires_and_honors_exact_game_identity(monkeypatch):
    async def run():
        logs = [
            {"game_id": 222, "date": "2025-12-21", "pts": 40},
            {"game_id": 111, "date": "2025-12-20", "pts": 25},
        ]

        async def fake_logs(_player_id, season):
            assert season == 2025
            return logs

        class FakePicks:
            def __init__(self):
                self.update = None

            async def update_one(self, _query, update):
                self.update = update

        class FakeDb:
            def __init__(self):
                self.picks = FakePicks()

        fake_db = FakeDb()
        monkeypatch.setattr(nba_client, "get_player_game_logs", fake_logs)
        monkeypatch.setattr(ai_engine, "db", fake_db)

        base_pick = {
            "pickId": "nba-test-1",
            "playerId": 7,
            "propType": "points",
            "line": 20.5,
            "recommendation": "over",
            "timestamp": datetime(2025, 12, 19, tzinfo=timezone.utc).isoformat(),
        }

        assert await ai_engine._try_settle_bdl({**base_pick}, "nba") is False
        assert await ai_engine._try_settle_bdl({**base_pick, "gameId": 999, "season": 2025}, "nba") is False

        settled = await ai_engine._try_settle_bdl(
            {**base_pick, "gameId": 111, "season": 2025},
            "nba",
        )
        assert settled is True
        assert fake_db.picks.update["$set"]["actualValue"] == 25.0
        assert fake_db.picks.update["$set"]["result"] == "hit"

    asyncio.run(run())