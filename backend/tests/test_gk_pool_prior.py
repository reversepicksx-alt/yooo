from bayesian_engine import compute_bayesian_projection
from gk_pool_prior import build_gk_pool_prior


def _gk_row(player_id, value, minutes=90):
    return {
        "playerId": player_id,
        "name": f"Keeper {player_id}",
        "position": "GK",
        "matchPosition": "Goalkeeper",
        "statValue": value,
        "minutes": minutes,
    }


def test_pool_only_accepts_goalkeepers_and_reports_verified_scope():
    rows = (
        [_gk_row(index, 28 + index) for index in range(5)]
        + [
            {
                "playerId": "outfield",
                "position": "CM",
                "statValue": 120,
                "minutes": 90,
            },
            {
                "playerId": "short",
                "position": "GK",
                "statValue": 200,
                "minutes": 15,
            },
        ]
    )

    result = build_gk_pool_prior(rows, player_prior_mean=30)

    assert result["status"] == "classified"
    assert result["poolRows"] == 5
    assert result["poolPlayers"] == 5
    assert result["poolMean"] == 30.0
    assert result["projectionAdjustmentStatus"] == "shadow_only"
    assert result["applied"] is False


def test_sparse_keeper_pool_is_unavailable_not_zero():
    result = build_gk_pool_prior(
        [_gk_row(index, 30) for index in range(4)],
        player_prior_mean=30,
    )

    assert result["status"] == "insufficient_evidence"
    assert result["poolMean"] is None
    assert result["poolRows"] == 4
    assert result["applied"] is False


def test_shadow_pool_does_not_change_bayesian_projection_even_if_live_requested():
    rows = [_gk_row(index, 12 + index * 10) for index in range(6)]
    shadow = build_gk_pool_prior(rows, player_prior_mean=30, mode="shadow")
    requested_live = build_gk_pool_prior(rows, player_prior_mean=30, mode="live")

    assert shadow["blendedPriorMean"] != shadow["playerPriorMean"]
    assert requested_live["livePromotionRequested"] is True
    assert requested_live["applied"] is False
    assert requested_live["projectionAdjustmentStatus"] == "shadow_only"

    # The helper is intentionally not coupled to the engine. A promotion
    # would require an explicit engine input and a separate validation change;
    # this test guards that no "live" request can claim to have applied one.
    assert "projectionAdjustment" not in requested_live
