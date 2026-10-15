"""Unit tests for clock."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from aws_cost_cleaner.clock import FixedClock, SystemClock


def test_fixed_clock_returns_preset_datetime() -> None:
    """AC-5: FixedClock(dt).now() == dt が真である。"""
    dt = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    clock = FixedClock(dt)
    assert clock.now() == dt


def test_system_clock_returns_utc_datetime() -> None:
    """AC-6: SystemClock().now() は aware な UTC datetime を返す。"""
    clock = SystemClock()
    now = clock.now()
    assert isinstance(now, datetime)
    assert now.tzinfo is not None
    assert now.utcoffset() == timedelta(0)
