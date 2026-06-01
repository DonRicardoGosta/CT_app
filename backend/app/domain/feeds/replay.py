"""Replay feed: yields closed bars in time order.

This single implementation backs the backtest feed and is also used to prove the
mode-equivalence property in tests. Strategies consume *closed bars* (not raw
ticks), so the same bar sequence yields the same decisions regardless of mode.

If a :class:`SimulatedClock` is supplied, the feed advances it to each event's
timestamp before yielding, so backtests carry correct timestamps while running as
fast as the CPU allows.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable

from app.domain.clock import Clock, SimulatedClock
from app.domain.interfaces import MarketDataFeed
from app.domain.types import Bar, Instrument, MarketEvent, MarketEventType


def merge_bars_by_time(per_symbol: dict[str, list[Bar]]) -> list[Bar]:
    """Flatten per-symbol bar lists into one list ordered by ``open_time``.

    Ordering is stable on (open_time, symbol) so the sequence is deterministic.
    """
    flat: list[Bar] = [bar for bars in per_symbol.values() for bar in bars]
    flat.sort(key=lambda b: (b.open_time, b.symbol))
    return flat


class ReplayFeed(MarketDataFeed):
    """Yields a pre-sorted list of bars as BAR market events."""

    def __init__(
        self,
        bars: Iterable[Bar],
        instruments: dict[str, Instrument],
        clock: Clock | None = None,
    ) -> None:
        self._bars = list(bars)
        self._instruments = instruments
        self._clock = clock

    async def instruments(self) -> dict[str, Instrument]:
        return self._instruments

    async def stream(self) -> AsyncIterator[MarketEvent]:
        for bar in self._bars:
            if isinstance(self._clock, SimulatedClock):
                self._clock.set(bar.open_time)
            yield MarketEvent(
                type=MarketEventType.BAR,
                ts=bar.open_time,
                symbol=bar.symbol,
                bar=bar,
            )
