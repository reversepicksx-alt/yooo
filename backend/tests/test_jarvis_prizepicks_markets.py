from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import routes.jarvis as jarvis


def _market(home: str, away: str, player: str, prop: str = "passes", key: str = "m1"):
    return {
        "marketKey": key,
        "eventId": f"event-{key}",
        "eventStart": 1787412000,
        "leagueId": "253",
        "leagueName": "MLS",
        "homeTeam": home,
        "awayTeam": away,
        "playerName": player,
        "playerProviderId": "player-1",
        "propType": prop,
        "propLabel": "Passes",
        "statId": "passes",
        "marketLine": 22.5,
        "current_line": 22.5,
        "previous_line": 21.5,
        "movement": 1.0,
        "first_seen": 1787400000,
        "last_seen": 1787412000,
        "line_history": [{"line": 21.5, "observed_at": 1787400000}],
        "analysisSupported": True,
    }


def test_saved_market_filter_supports_team_and_player_filters():
    markets = [
        _market("Inter Miami", "Toronto FC", "Dominik Sallói", key="miami"),
        _market("FC Cincinnati", "Seattle Sounders FC", "Jordan Morris", key="cincy"),
        _market("Charlotte", "DC United", "Patrick Agyemang", key="charlotte"),
    ]

    result = jarvis._filter_saved_prizepicks_markets(
        markets, home_team="FC Cincinnati", away_team="Seattle", limit=25
    )
    assert [item["marketKey"] for item in result] == ["cincy"]
    assert result[0]["current_line"] == 22.5
    assert result[0]["line_history"] == [{"line": 21.5, "observed_at": 1787400000}]

    result = jarvis._filter_saved_prizepicks_markets(
        markets, team="charlotte", player_name="agyemang", prop_type="PASS"
    )
    assert [item["marketKey"] for item in result] == ["charlotte"]


def test_saved_market_filter_enforces_limit_without_mutating_snapshot():
    markets = [_market("Inter Miami", "Toronto FC", f"Player {i}", key=f"m{i}") for i in range(125)]
    result = jarvis._filter_saved_prizepicks_markets(markets, home_team="Miami", limit=100)
    assert len(result) == 100
    assert len(markets) == 125


def test_route_reads_latest_snapshot_only_and_never_refreshes_provider(monkeypatch):
    markets = [
        _market("Inter Miami", "Toronto FC", "Player Miami", key="miami"),
        _market("FC Cincinnati", "Seattle Sounders FC", "Player Cincy", key="cincy"),
        _market("Charlotte", "DC United", "Player Charlotte", key="charlotte"),
    ]

    class SnapshotCollection:
        async def find_one(self, query, projection):
            assert query == {"_id": "latest"}
            assert projection == {"_id": 0}
            return {"source": "SportsGameOdds", "fetched_at": 1787412000, "markets": markets}

    async def provider_must_not_run(*args, **kwargs):
        raise AssertionError("filtered read must not call SportsGameOdds")

    monkeypatch.setattr(jarvis, "_require_auth", lambda authorization: None)
    monkeypatch.setattr(jarvis, "list_market_board", provider_must_not_run)
    monkeypatch.setattr(
        jarvis, "db", SimpleNamespace(jarvis_prizepicks_board=SnapshotCollection())
    )

    for home, away, expected in [
        ("Inter Miami", "Toronto FC", "miami"),
        ("FC Cincinnati", "Seattle Sounders FC", "cincy"),
        ("Charlotte", "DC United", "charlotte"),
    ]:
        response = asyncio.run(jarvis.jarvis_search_saved_prizepicks_markets(
            home_team=home,
            away_team=away,
            team=None,
            player_name=None,
            prop_type=None,
            limit=25,
            authorization="Bearer test",
        ))
        payload = json.loads(response.body)
        assert payload["market_count"] == 1
        assert payload["markets"][0]["marketKey"] == expected
        assert payload["markets"][0]["analysisSupported"] is True