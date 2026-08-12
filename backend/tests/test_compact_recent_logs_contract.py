from pathlib import Path


COMPACT_SOURCE = (
    Path(__file__).resolve().parents[2]
    / "mobile"
    / "components"
    / "CompactAnalysisBars.tsx"
)


def test_compact_analysis_accepts_saved_player_game_log_shape():
    assert COMPACT_SOURCE.exists()
    source = COMPACT_SOURCE.read_text()

    assert "prediction.playerGameLogs?.games" in source
    assert "RECENT_LOG_VALUE_FIELDS" in source
    assert "game[targetField]" in source
    assert "RECENT MATCHES · {logs.length}" in source