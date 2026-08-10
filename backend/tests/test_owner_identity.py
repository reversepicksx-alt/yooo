from pathlib import Path
import asyncio

from owner_media import select_player_photo
from routes import picks as picks_routes


def test_photo_prefers_fixture_team_context_over_first_cache_row():
    rows = [
        {
            "playerId": 9946,
            "teamId": 646,
            "photo": "https://example.invalid/levski.png",
            "_cachedAt": 9999,
        },
        {
            "playerId": 9946,
            "teamId": 7848,
            "photo": "https://example.invalid/mirassol.png",
            "_cachedAt": 1,
        },
    ]

    assert select_player_photo(rows, player_id=9946, team_id=7848).endswith("mirassol.png")


def test_photo_never_uses_a_different_player_id():
    rows = [
        {"playerId": 9650, "teamId": 646, "photo": "https://example.invalid/wrong.png"},
    ]

    assert select_player_photo(rows, player_id=9946, team_id=646) == ""


def test_photo_matches_string_and_numeric_identity_representations():
    rows = [
        {
            "playerId": "9946",
            "teamId": "7848",
            "photo": "https://example.invalid/mirassol.png",
        },
    ]

    assert select_player_photo(rows, player_id=9946, team_id=7848).endswith("mirassol.png")


def test_profile_and_saved_pick_contracts_are_id_aware():
    api_source = (Path(__file__).resolve().parents[2] / "mobile" / "lib" / "api.ts").read_text()
    profile_source = (
        Path(__file__).resolve().parents[2]
        / "mobile"
        / "components"
        / "PlayerProfileCard.tsx"
    ).read_text()
    assert "const value = p.playerId" in api_source
    assert "const identifiedIds = new Set" in profile_source
    assert "nameMatches.filter((p) => !p.playerId || p.playerId === playerId)" in profile_source


def test_save_identity_repair_rejects_stale_nested_player_id(monkeypatch):
    class _Cursor:
        def __init__(self, rows):
            self.rows = rows

        async def to_list(self, _limit):
            return list(self.rows)

    class _Collection:
        def __init__(self, rows):
            self.rows = rows

        async def find_one(self, _query, _projection):
            return None

        def find(self, _query, _projection):
            return _Cursor(self.rows)

    class _Db:
        def __init__(self, rows):
            self.collection = _Collection(rows)

        def __getitem__(self, _name):
            return self.collection

    monkeypatch.setattr(
        picks_routes,
        "db",
        _Db(
            [
                {"playerId": 9946, "teamId": 7848, "name": "Reinaldo"},
                {"playerId": 10102, "teamId": 135, "name": "Lucas Silva"},
            ]
        ),
    )

    resolved = asyncio.run(
        picks_routes._resolve_saved_soccer_player_id(
            10102,
            "Reinaldo Manoel da Silva",
            7848,
        )
    )
    assert resolved == 9946