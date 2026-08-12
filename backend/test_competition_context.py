import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from competition_context import (
    build_competition_context,
    normalize_stage,
    select_contextual_history,
)


def test_stage_normalization_is_stable():
    assert normalize_stage("Quarter-finals") == "quarter_final"
    assert normalize_stage("Regular Season - 26") == "regular_season"
    assert normalize_stage("Group Stage - 3") == "group_stage"


def test_competition_backoff_and_pass_share_are_auditable():
    logs = [
        {
            "minutes": 90,
            "passes_total": 152,
            "teamPassAttempts": 841,
            "leagueId": 2,
            "league": "UEFA Champions League",
            "round": "Quarter-finals",
            "venue": "home",
        },
        {
            "minutes": 90,
            "passes_total": 139,
            "teamPassAttempts": 746,
            "leagueId": 2,
            "league": "UEFA Champions League",
            "round": "Quarter-finals",
            "venue": "away",
        },
        {
            "minutes": 90,
            "passes_total": 90,
            "teamPassAttempts": 600,
            "leagueId": 39,
            "league": "Premier League",
            "round": "Regular Season - 1",
            "venue": "home",
        },
    ]

    packet = build_competition_context(
        logs,
        prop_type="pass_attempts",
        competition_id=2,
        competition_name="UEFA Champions League",
        round_value="Quarter-finals",
        venue="home",
        line=108,
    )

    assert packet["available"] is True
    assert packet["projectionAdjustment"] == 0.0
    assert packet["selected"]["sourceLevel"] == "competition_stage_venue"
    assert packet["buckets"][1]["sampleSize"] == 2
    assert packet["passShare"]["available"] is True
    assert packet["passShare"]["buckets"][1]["sampleSize"] == 2
    assert packet["passShare"]["buckets"][1]["average"] > 17.0


def test_super_cup_final_uses_elite_knockout_backoff():
    logs = [
        {
            "minutes": 90,
            "passes_total": 139,
            "teamPassAttempts": 746,
            "leagueId": 2,
            "league": "UEFA Champions League",
            "round": "Quarter-finals",
            "venue": "home",
        }
    ]
    packet = build_competition_context(
        logs,
        prop_type="pass_attempts",
        competition_id=531,
        competition_name="UEFA Super Cup",
        round_value="Final",
        venue="home",
    )
    stage_peer = next(
        bucket for bucket in packet["buckets"]
        if bucket["level"] == "stage_class_venue"
    )
    assert stage_peer["sampleSize"] == 1
    assert stage_peer["stageEquivalent"] is True


def test_display_history_requires_matching_venue_and_knockout_class():
    logs = [
        {
            "date": "2026-04-08",
            "passes_total": 139,
            "minutes": 90,
            "leagueId": 2,
            "league": "UEFA Champions League",
            "round": "Quarter-finals",
            "venue": "home",
        },
        {
            "date": "2026-04-14",
            "passes_total": 60,
            "minutes": 90,
            "leagueId": 2,
            "league": "UEFA Champions League",
            "round": "Quarter-finals",
            "venue": "away",
        },
        {
            "date": "2026-05-17",
            "passes_total": 111,
            "minutes": 90,
            "leagueId": 61,
            "league": "Ligue 1",
            "round": "Regular Season - 34",
            "venue": "home",
        },
    ]
    selected, context = select_contextual_history(
        logs,
        competition_id=531,
        competition_name="UEFA Super Cup",
        round_value="Final",
        venue="home",
    )
    assert [row["date"] for row in selected] == ["2026-04-08"]
    assert context["mode"] == "venue_and_knockout_stage"
    assert context["label"] == "UEFA SUPER CUP · KNOCKOUT STAGES · HOME"