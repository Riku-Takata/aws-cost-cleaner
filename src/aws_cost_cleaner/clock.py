"""Clock protocols and implementations."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    """Clock protocol.

    UTC の aware な datetime を返す。
    """

    def now(self) -> datetime:
        """UTC の aware な datetime を返す。"""
        ...


class SystemClock:
    """System clock implementation."""

    def now(self) -> datetime:
        """UTC の aware な datetime を返す。"""
        return datetime.now(UTC)


class FixedClock:
    """Fixed clock implementation for testing."""

    def __init__(self, dt: datetime) -> None:
        self._dt = dt

    def now(self) -> datetime:
        """Return the fixed datetime."""
        return self._dt
