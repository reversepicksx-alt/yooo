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
    assert "normalized === 'home' || normalized === 'h'" in source
    assert "normalized === 'away' || normalized === 'a'" in source
    assert "RECENT MATCHES · {logs.length}" in source


def test_analysis_endpoint_projects_and_repairs_recent_log_shapes():
    endpoint_source = (
        Path(__file__).resolve().parents[1]
        / "routes"
        / "picks.py"
    ).read_text()

    assert '"playerGameLogs": 1, "gameLogs": 1' in endpoint_source
    assert "_pick_games = _pick_logs.get(\"games\")" in endpoint_source
    assert 'prediction["gameLogs"] = _prediction_games' in endpoint_source