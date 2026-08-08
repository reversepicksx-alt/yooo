from bzzoiro_client import compute_press_proxy, _find_event


def test_press_proxy_uses_bzzoiro_defensive_actions_and_marks_single_fixture():
    result = compute_press_proxy(
        {
            "total_tackles": 14,
            "interceptions": 9,
            "passes": 530,
            "ball_possession": 47,
        }
    )

    assert result["label"] == "Moderate"
    assert result["defensiveActions"] == 23
    assert result["passesPerDefensiveAction"] == 23.0
    assert result["sampleSize"] == 1
    assert result["evidenceStatus"] == "single_fixture_shadow"
    assert result["projectionAdjustmentStatus"] == "shadow_only"


def test_press_proxy_treats_missing_values_as_unavailable_not_zero():
    assert compute_press_proxy({}) is None
    assert compute_press_proxy({"total_tackles": None, "interceptions": None}) is None


def test_event_matching_requires_verified_opponent_and_prefers_exact_date():
    rows = [
        {
            "id": 1,
            "home_team_id": 293,
            "home_team": "Inter Miami CF",
            "away_team_id": 2282,
            "away_team": "CF Monterrey",
            "event_date": "2026-08-09T00:00:00+00:00",
        },
        {
            "id": 2,
            "home_team_id": 293,
            "home_team": "Inter Miami CF",
            "away_team_id": 2282,
            "away_team": "CF Monterrey",
            "event_date": "2026-08-08T00:00:00+00:00",
        },
    ]

    result = _find_event(
        rows,
        team_id=293,
        team_name="Inter Miami",
        opponent_id=2282,
        opponent_name="Monterrey",
        match_date="2026-08-09T00:00:00Z",
    )

    assert result["id"] == 1


def test_event_matching_can_bridge_provider_ids_by_verified_names():
    result = _find_event(
        [
            {
                "id": 5170,
                "home_team_id": 293,
                "home_team": "Inter Miami CF",
                "away_team_id": 295,
                "away_team": "Columbus Crew",
                "event_date": "2026-08-02T00:10:00+00:00",
            }
        ],
        team_id=9568,  # API-Football ID, intentionally not Bzzoiro's 293
        team_name="Inter Miami",
        opponent_id=161,  # API-Football ID, intentionally not Bzzoiro's 295
        opponent_name="Columbus Crew",
        match_date="2026-08-02T00:00:00Z",
    )

    assert result["id"] == 5170