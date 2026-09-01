from possession_context import (
    POSSESSION_MIN_VERIFIED_SAMPLE,
    moneyline_possession_signal,
    possession_sample_status,
    recency_weighted_average,
)


def test_possession_requires_ten_verified_rows():
    assert POSSESSION_MIN_VERIFIED_SAMPLE == 10
    assert possession_sample_status(9) == ("insufficient_sample", False)
    assert possession_sample_status(10) == ("verified", True)


def test_recency_weighted_average_favors_newest_rows():
    newest_heavy = recency_weighted_average(
        [{"value": 70}, {"value": 30}],
    )
    assert newest_heavy is not None
    assert newest_heavy > 50


def test_moneyline_signal_is_bounded_and_fixture_oriented():
    signal = moneyline_possession_signal(
        {"americanOdds": {"home": "-200", "away": "+170"}},
    )

    assert signal is not None
    assert signal["expectedHomePossession"] > 50
    assert 0.0 < signal["weight"] <= 0.18

    away_favorite = moneyline_possession_signal(
        {"bookmakerOdds": {"homeWin": 4.0, "awayWin": 1.8}},
    )
    assert away_favorite is not None
    assert away_favorite["expectedHomePossession"] < 50


def test_missing_moneyline_has_no_possession_signal():
    assert moneyline_possession_signal(None) is None
    assert moneyline_possession_signal({"bookmakerOdds": {}}) is None