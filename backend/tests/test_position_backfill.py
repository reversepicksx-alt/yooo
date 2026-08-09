from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "position_backfill.py").read_text()
ADMIN_SOURCE = (ROOT / "routes" / "admin.py").read_text()


def test_backfill_only_persists_exact_lineup_history():
    assert 'source": "api_sports_lineup_history"' in SOURCE
    assert 'position in _EXACT_POSITIONS' in SOURCE
    assert 'infer_grid_position' in SOURCE
    assert "priority_api_football_request" in SOURCE
    assert '\"team\": team_id' in SOURCE
    assert "_team_fixture_ids" in SOURCE


def test_backfill_does_not_use_unsupported_player_fixture_filter():
    assert '"player": int(player_id)' not in SOURCE


def test_backfill_requires_repeated_observations_and_category_safety():
    assert "if count < min_observations" in SOURCE
    assert "categoryMismatch" in SOURCE
    assert "_GENERIC_TO_SPECIFIC" in SOURCE


def test_backfill_revalidates_provider_profiles_after_mapping_corrections():
    assert 'current.get("source") != "api_sports_lineup_history"' in SOURCE


def test_backfill_never_downgrades_trusted_profiles():
    assert "_record_is_trusted(current)" in SOURCE
    assert "_record_is_trusted(latest)" in SOURCE


def test_owner_backfill_endpoint_exists():
    assert '@router.post("/positions/backfill-api-sports")' in ADMIN_SOURCE