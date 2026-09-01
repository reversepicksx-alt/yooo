from prizepicks_gateway import _normalize_event, _page
from datetime import datetime, timezone


def test_page_reads_cursor_and_event_data():
    events, cursor = _page({"data": [{"eventID": "e1"}], "pagination": {"nextCursor": "abc"}})
    assert events == [{"eventID": "e1"}]
    assert cursor == "abc"


def test_normalize_event_preserves_opaque_ids_and_prizepicks_line():
    event = {
        "eventID": "opaque-event",
        "leagueID": "MLS",
        "status": {"startsAt": "2099-01-01T00:00:00Z"},
        "teams": {
            "home": {"teamID": "home-7", "names": {"long": "Home FC"}},
            "away": {"teamID": "away-8", "names": {"long": "Away FC"}},
        },
        "odds": [{
            "playerID": "player-9",
            "playerName": "A Player",
            "statID": "shots",
            "marketName": "Shots",
            "sideID": "over",
            "byBookmaker": {"prizepicks": {"line": 2.5, "available": True}},
        }],
    }
    rows = _normalize_event(event, datetime.now(timezone.utc))
    assert len(rows) == 1
    assert rows[0]["eventId"] == "opaque-event"
    assert rows[0]["playerId"] == "player-9"
    assert rows[0]["currentLine"] == 2.5
    assert rows[0]["homeTeamId"] == "home-7"