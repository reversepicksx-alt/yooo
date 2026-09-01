from first_goal_engine import build_first_goal_market
from first_goal_engine import get_first_goal_profile


def _profile(team_first, opponent_first, no_goal, first_goal_minute, n=10):
    return {
        "available": True,
        "teamScoredFirstPct": team_first,
        "opponentScoredFirstPct": opponent_first,
        "noGoalPct": no_goal,
        "avgFirstGoalMin": first_goal_minute,
        "dataPoints": n,
    }


def test_first_goal_market_combines_two_completed_fixture_profiles():
    market, regime = build_first_goal_market(
        _profile(0.60, 0.25, 0.15, 29.0, n=12),
        _profile(0.35, 0.50, 0.15, 37.0, n=9),
        "pass_attempts",
    )

    assert market["available"] is True
    assert market["coverage"] == "two_team_profiles"
    assert market["team_scores_first_probability"] == 0.55
    assert market["opponent_scores_first_probability"] == 0.3
    assert market["no_goal_probability"] == 0.15
    assert market["sample_size"] == {"team": 12, "opponent": 9}
    assert market["projection_influence"] == "shadow_only"
    assert regime["available"] is True
    assert regime["classification"] == "team_first_lean"
    assert regime["best_case"] == "team_scores_first"
    assert regime["projection_influence"] == "shadow_only"


def test_first_goal_market_explicitly_reports_unavailable_profiles():
    market, regime = build_first_goal_market({}, {}, "pass_attempts")

    assert market["available"] is False
    assert regime["available"] is False
    assert market["projection_influence"] == "shadow_only"


def test_first_goal_profile_accepts_project_api_client_list_shape():
    class FakeCollection:
        async def find_one(self, *_args, **_kwargs):
            return None

        async def update_one(self, *_args, **_kwargs):
            return None

    class FakeDb:
        first_goal_cache = FakeCollection()

    fixtures = [
        {
            "fixture": {"id": fixture_id},
            "teams": {"home": {"id": 1}, "away": {"id": 2}},
        }
        for fixture_id in (10, 11, 12)
    ]
    events = {
        10: [{"type": "Goal", "detail": "Normal Goal", "team": {"id": 1}, "time": {"elapsed": 20}}],
        11: [{"type": "Goal", "detail": "Normal Goal", "team": {"id": 2}, "time": {"elapsed": 30}}],
        12: [],
    }

    async def api_client(endpoint, params):
        if endpoint == "fixtures":
            return fixtures
        return events[int(params["fixture"])]

    import asyncio

    profile = asyncio.run(get_first_goal_profile(1, 2026, api_client, FakeDb()))
    assert profile["available"] is True
    assert profile["dataPoints"] == 3
    assert profile["teamScoredFirstPct"] == 0.333
    assert profile["opponentScoredFirstPct"] == 0.333