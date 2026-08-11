from pathlib import Path


PLAYERS_SOURCE = (
    Path(__file__).resolve().parents[1] / "routes" / "players.py"
).read_text()


def test_fast_single_word_search_merges_exact_word_matches_past_substring_cap():
    assert "if fast and len(parts) == 1:" in PLAYERS_SOURCE
    assert 'exact_pattern = rf"(^| ){re.escape(parts[0])}( |$)"' in PLAYERS_SOURCE
    assert "exact_docs = await db[COL_PLAYERS].find(" in PLAYERS_SOURCE
    assert "docs.extend(" in PLAYERS_SOURCE
    assert "seen_doc_keys" in PLAYERS_SOURCE


def test_full_name_search_retries_provider_surname_for_abbreviated_profiles():
    """Provider profiles may expose J. Valencia but full first/last names."""
    assert 'if len(query_parts) > 1 and not _apply_sort_and_quality(list(live_players)):' in PLAYERS_SOURCE
    assert 'last_word = query_parts[-1]' in PLAYERS_SOURCE
    assert '"players/profiles", {"search": last_word}' in PLAYERS_SOURCE
    assert "fallback_players = [extract_player(item)" in PLAYERS_SOURCE


def test_durable_identity_fallback_survives_disposable_cache_timeouts():
    """Saved soccer identities remain searchable after cache cleanup."""
    assert "async def _durable_identity_fallback()" in PLAYERS_SOURCE
    assert 'db.picks.find(' in PLAYERS_SOURCE
    assert '"sport": "soccer"' in PLAYERS_SOURCE
    assert "durable_players = await _durable_identity_fallback()" in PLAYERS_SOURCE
    assert "if durable_players:" in PLAYERS_SOURCE
