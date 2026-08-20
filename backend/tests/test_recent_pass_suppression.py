"""Regression tests for final suppression of weak rolling pass directions."""

from prop_safety_cache import (
    should_suppress_avoided_direction,
    should_suppress_recent_direction,
)


def test_recent_direction_suppresses_break_even_or_worse_sample():
    assert should_suppress_recent_direction({"hitRate": 50.0, "n": 10}) is True
    assert should_suppress_recent_direction({"hitRate": 45.0, "n": 22}) is True


def test_recent_direction_does_not_suppress_thin_or_positive_sample():
    assert should_suppress_recent_direction({"hitRate": 50.0, "n": 9}) is False
    assert should_suppress_recent_direction({"hitRate": 50.1, "n": 40}) is False
    assert should_suppress_recent_direction(None) is False


def test_all_time_avoided_direction_requires_ten_events():
    assert should_suppress_avoided_direction({"hitRate": 44.0, "n": 10}) is True
    assert should_suppress_avoided_direction({"hitRate": 40.0, "n": 9}) is False
    assert should_suppress_avoided_direction({"hitRate": 45.0, "n": 25}) is False