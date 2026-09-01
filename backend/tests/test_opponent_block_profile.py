from opponent_block_profile import classify_block_profile


def _metrics(*, attacking: int, middle: int, defensive: int, ppda: float):
    return {
        "coordinateMode": "team_relative",
        "pressureByThird": {
            "attacking": attacking,
            "middle": middle,
            "defensive": defensive,
        },
        "pressureEvents": attacking + middle + defensive,
        "ppda": ppda,
        "ppdaStatus": "event_derived",
    }


def test_high_block_requires_direction_normalized_event_evidence():
    packet = classify_block_profile(
        _metrics(attacking=18, middle=8, defensive=4, ppda=8.4)
    )

    assert packet["label"] == "HIGH_BLOCK"
    assert packet["status"] == "event_derived"
    assert packet["pressureShares"]["attacking"] == 0.6
    assert packet["limitations"]


def test_low_block_is_not_inferred_from_ppda_without_pressure_locations():
    packet = classify_block_profile(
        _metrics(attacking=3, middle=7, defensive=20, ppda=22.0)
    )

    assert packet["label"] == "LOW_BLOCK"
    assert packet["pressureShares"]["defensive"] == 0.667
    assert packet["pressureIntensity"] == "low"


def test_missing_direction_or_thin_event_sample_stays_unavailable():
    packet = classify_block_profile(
        {
            "coordinateMode": "unknown",
            "pressureByThird": {"attacking": 3, "middle": 2, "defensive": 1},
            "ppda": 4.0,
            "ppdaStatus": "unavailable",
        }
    )

    assert packet["label"] == "UNAVAILABLE"
    assert packet["status"] == "insufficient_event_evidence"
    assert packet["pressureShares"] is None
    assert packet["ppda"] == 4.0