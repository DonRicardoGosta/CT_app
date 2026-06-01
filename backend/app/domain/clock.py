"""Time abstraction.

Strategies and the engine read time exclusively from a ``Clock`` so behaviour is
deterministic and identical across modes (REQ-003). In live/dry-run the clock is
the wall clock; in backtest it is driven by the data being replayed.
"""

from __future__ import annotations

import abc
import asyncio
from datetime import UTC, datetime


class Clock(abc.ABC):
    """Source of the current time and of waiting."""

    @abc.abstractmethod
    def now(self) -> datetime:
        """Return the current aware UTC time."""

    @abc.abstractmethod
    async def sleep(self, seconds: float) -> None:
        """Wait for the given duration (real or simulated)."""


class RealClock(Clock):
    """Wall-clock time. Used by live and dry-run modes."""

    def now(self) -> datetime:
        return datetime.now(UTC)

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


class SimulatedClock(Clock):
    """Deterministic clock advanced by the backtest feed.

    ``sleep`` does not actually wait; it simply advances simulated time, so a
    backtest runs as fast as the CPU allows while preserving timestamps.
    """

    def __init__(self, start: datetime) -> None:
        self._now = start

    def now(self) -> datetime:
        return self._now

    def set(self, ts: datetime) -> None:
        """Set simulated time (called by the feed as events are replayed)."""
        if ts > self._now:
            self._now = ts

    async def sleep(self, seconds: float) -> None:  # noqa: D401 - simulated
        # No real waiting in backtest; time is advanced by the feed.
        return None
