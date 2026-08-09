from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PICKS_SOURCE = (ROOT / "routes" / "picks.py").read_text()
CARD_SOURCE = (ROOT.parent / "mobile" / "components" / "OwnerPickCard.tsx").read_text()
TRACKER_SOURCE = (ROOT.parent / "mobile" / "components" / "LiveMatchTracker.tsx").read_text()
API_SOURCE = (ROOT.parent / "mobile" / "lib" / "api.ts").read_text()


def test_active_pick_list_never_returns_the_short_ttl_snapshot():
    """The 15-second client poll must reach the live resolver every time."""
    assert "_cached_has_active" in PICKS_SOURCE
    assert "if _cached and not _cached_has_active" in PICKS_SOURCE
    assert "p.get(\"status\") in {\"live\", \"pending\"}" in PICKS_SOURCE
    assert "p.get(\"matchStatus\") == \"live\"" in PICKS_SOURCE


def test_api_football_live_lookups_use_priority_path():
    """Maintenance work cannot consume the path used by active live cards."""
    live_start = PICKS_SOURCE.index("async def _process_api_football_live")
    live_source = PICKS_SOURCE[live_start:]
    assert "priority_api_football_request(\"fixtures\", {\"id\": fid})" in live_source
    assert "priority_api_football_request(\"fixtures/players\"" in live_source
    assert "await api_football_request(\"fixtures\", {\"id\": fid})" not in live_source


def test_live_fallback_preserves_live_status_when_provider_stats_lag():
    """A transient provider miss must not turn an active card into PENDING."""
    assert '_live_fallback = "live" if _db_status == "live" else "scheduled"' in PICKS_SOURCE
    assert 'results.append({"pickId": pick_id, "matchStatus": _live_fallback})' in PICKS_SOURCE


def test_live_card_is_status_driven_and_displays_fixture_id():
    """Fixture status and identity remain visible even before player stats arrive."""
    card_live_start = CARD_SOURCE.index("function isLive")
    card_live_source = CARD_SOURCE[card_live_start:CARD_SOURCE.index("function isPendingReview")]
    assert "p.matchStatus === 'live'" in card_live_source
    assert "p.status === 'live'" in card_live_source
    assert "MATCH ID {pick.fixtureId}" in CARD_SOURCE
    assert "compactMatchId" in CARD_SOURCE


def test_tracker_and_api_contract_keep_fixture_identity():
    assert "pick.fixtureId" in TRACKER_SOURCE
    assert "MATCH ID {pick.fixtureId}" in TRACKER_SOURCE
    assert "fixtureId" in API_SOURCE