"""Backtest feed: a :class:`ReplayFeed` driving a simulated clock."""

from __future__ import annotations

from collections.abc import Iterable

from app.domain.clock import SimulatedClock
from app.domain.feeds.replay import ReplayFeed, merge_bars_by_time
from app.domain.types import Bar, Instrument

__all__ = ["BacktestFeed", "merge_bars_by_time"]


class BacktestFeed(ReplayFeed):
    """Replays historical bars against a :class:`SimulatedClock`."""

    def __init__(
        self,
        bars: Iterable[Bar],
        instruments: dict[str, Instrument],
        clock: SimulatedClock,
    ) -> None:
        super().__init__(bars, instruments, clock=clock)
