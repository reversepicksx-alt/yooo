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
