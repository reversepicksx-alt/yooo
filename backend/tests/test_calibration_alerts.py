from calibration_alerts import _build_direction_alert


def _replay(direction: str, n: int, hits: int) -> dict:
    return {
        "byDirection": {
            direction: {
                "n": n,
                "hits": hits,
                "misses": n - hits,
                "hitRate": round(hits / n * 100, 1),
                "brierScore": 0.3,
            }
        }
    }


def test_weak_over_bucket_is_avoid():
    alert = _build_direction_alert(
        sport="soccer",
        prop_type="passes",
        direction="over",
        replay=_replay("over", 120, 62),
    )
    assert alert["alertLevel"] == "AVOID"
    assert alert["hitRate"] == 51.7
    assert alert["n"] == 120


def test_strong_under_bucket_is_preserved():
    alert = _build_direction_alert(
        sport="soccer",
        prop_type="passes",
        direction="under",
        replay=_replay("under", 120, 82),
    )
    assert alert["alertLevel"] == "OK"
    assert alert["hitRate"] == 68.3


def test_thin_over_bucket_is_noop():
    alert = _build_direction_alert(
        sport="soccer",
        prop_type="passes",
        direction="over",
        replay=_replay("over", 99, 20),
    )
    assert alert["alertLevel"] == "OK"