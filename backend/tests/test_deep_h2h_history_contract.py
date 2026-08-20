"""Regression coverage for older direct-pairing H2H history."""

import ast
import asyncio
from pathlib import Path

from routes.predict import _collect_partial_prediction_results

ROOT = Path(__file__).resolve().parents[1]
PREDICT_PATH = ROOT / "routes" / "predict.py"
PREDICT_SOURCE = PREDICT_PATH.read_text()
COMPACT_SOURCE = (
    Path(__file__).resolve().parents[2]
    / "mobile"
    / "components"
    / "CompactAnalysisBars.tsx"
).read_text()


def _load_merge_helper():
    tree = ast.parse(PREDICT_SOURCE)
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_merge_h2h_fixtures"
    )
    module = ast.Module(body=[function], type_ignores=[])
    namespace = {
        "H2H_FIXTURE_LIMIT": 48,
        "_H2H_FINISHED_STATUSES": {"FT", "AET", "PEN", "AWD", "WO"},
    }
    exec(compile(module, str(PREDICT_PATH), "exec"), namespace)
    return namespace["_merge_h2h_fixtures"]


def _fixture(fixture_id: int, date: str, status: str = "FT"):
    return {
        "fixture": {
            "id": fixture_id,
            "date": date,
            "status": {"short": status},
        }
    }


def test_finished_meetings_older_than_six_seasons_are_retained():
    merge = _load_merge_helper()
    rows = merge(
        [
            _fixture(1570334, "2026-08-19T00:00:00Z", "NS"),
            _fixture(9666, "2018-02-10T15:15:00Z"),
            _fixture(9853, "2017-09-16T18:45:00Z"),
            _fixture(9853, "2017-09-16T18:45:00Z"),
        ],
        limit=20,
    )

    assert [row["fixture"]["id"] for row in rows] == [9666, 9853]


def test_mobile_distinguishes_team_meetings_from_verified_player_appearances():
    assert "0 VERIFIED PLAYER APPS" in COMPACT_SOURCE
    assert "No verified player appearance in" in COMPACT_SOURCE
    assert "!isH2HFilter && venueHistoryFallback" in COMPACT_SOURCE


def test_player_stat_fanout_precedes_optional_lineup_enrichment():
    player_fetch = PREDICT_SOURCE.index(
        '"fixtures/players", {"fixture": fid}'
    )
    player_gather = PREDICT_SOURCE.index(
        '"player H2H evidence"',
        player_fetch,
    )
    lineup_fetch = PREDICT_SOURCE.index(
        '"fixtures/lineups", {"fixture": fixture_id}',
        player_gather,
    )
    assert player_fetch < player_gather < lineup_fetch


def test_h2h_line_signal_preserves_over_and_under_direction_rates():
    assert 'real_bayes["h2hLineOverRate"]' in PREDICT_SOURCE
    assert 'real_bayes["h2hLineUnderRate"]' in PREDICT_SOURCE
    assert 'real_bayes["h2hLineDirectionalHitRate"]' in PREDICT_SOURCE
    assert 'real_bayes["h2hLineWeight"] = 0' in PREDICT_SOURCE


def test_h2h_fanout_retains_completed_player_evidence_at_deadline():
    async def exercise_partial_collection():
        cancelled = asyncio.Event()

        async def fast_fixture():
            await asyncio.sleep(0.005)
            return {"fixtureId": 9666, "minutes": 90}

        async def slow_fixture():
            try:
                await asyncio.sleep(1)
                return {"fixtureId": 9853, "minutes": 90}
            except asyncio.CancelledError:
                cancelled.set()
                raise

        started = asyncio.get_running_loop().time()
        results = await _collect_partial_prediction_results(
            [fast_fixture, slow_fixture],
            started=started,
            budget=1.0,
            timeout=0.1,
            label="test H2H player evidence",
        )
        return results, cancelled.is_set()

    results, slow_was_cancelled = asyncio.run(exercise_partial_collection())
    assert results == [{"fixtureId": 9666, "minutes": 90}]
    assert slow_was_cancelled