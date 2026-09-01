from positional_reality import build_positional_reality
from tactical_intelligence import build_tactical_intelligence


def test_home_and_away_coordinates_use_same_attacking_direction():
    home = build_positional_reality(
        player={"x": 0.5, "y": 0.2},
        position="CM",
        role="Box-to-Box",
        prop_type="passes",
        is_home=True,
        match_script={"classification": "settled_control"},
    )
    away = build_positional_reality(
        player={"x": 0.5, "y": 0.8},
        position="CM",
        role="Box-to-Box",
        prop_type="passes",
        is_home=False,
        match_script={"classification": "settled_control"},
    )

    assert home["zone"] == away["zone"] == "own_third_central"
    assert home["zoneSource"] == "lineup_provider_coordinates"


def test_robust_history_downweights_outlier_without_deleting_it():
    result = build_positional_reality(
        player={},
        position="CM",
        role="Box-to-Box",
        prop_type="passes",
        is_home=True,
        match_script={"classification": "balanced"},
        history_values=[50, 51, 52, 53, 200],
    )
    evidence = result["robustEvidence"]

    assert evidence["sampleSize"] == 5
    assert evidence["outlierCount"] == 1
    assert evidence["weightedMean"] < 100
    assert "never blanket-deleted" in evidence["policy"]


def test_broad_defender_category_does_not_infer_central_zone_or_volume():
    result = build_positional_reality(
        player={},
        position="DEF",
        role=None,
        prop_type="passes",
        is_home=True,
        match_script={"classification": "counter_defensive"},
        history_values=[39, 42, 45],
    )

    assert result["status"] == "unavailable"
    assert result["zone"] == "zone_unavailable"
    assert result["propSignal"]["shadowDirection"] == "neutral"
    assert result["robustEvidence"]["sampleSize"] == 0


def test_match_script_uses_fixture_oriented_market_and_stays_shadow_only():
    packet = build_tactical_intelligence(
        prediction={
            "isHome": False,
            "moneyline": {"home": "-300", "away": "+550"},
            "player": {"id": 7, "name": "Target"},
        },
        prop_type="tackles",
        player_position="CB",
        player_role="Stopper",
        expected_possession=36,
        possession_is_real=False,
        possession_source="odds_fallback",
        lineup={
            "status": "confirmed",
            "home": {
                "formation": "4-3-3",
                "players": [{"id": 2, "name": "Opponent", "position": "ST"}],
            },
            "away": {
                "formation": "5-4-1",
                "players": [{"id": 7, "name": "Target", "position": "CB", "x": 0.5, "y": 0.8}],
            },
        },
    )

    assert packet["matchScript"]["classification"] == "counter_defensive"
    assert packet["marketGameScript"]["classification"] == "player_team_underdog"
    assert packet["positionalReality"]["propSignal"]["shadowDirection"] == "higher_volume"
    assert packet["propMechanism"]["projectionAdjustment"] == 0.0
    assert packet["propMechanism"]["projectionAdjustmentStatus"] == "shadow_only_until_calibrated"