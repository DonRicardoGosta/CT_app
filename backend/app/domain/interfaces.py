"""The three swappable interfaces that define a mode.

* :class:`MarketDataFeed` — where market events come from.
* :class:`Broker` — where orders are executed and account state is read.
* (the :class:`~app.domain.clock.Clock` lives in ``clock.py``)

Everything else in the engine and strategies depends only on these abstractions,
which is what makes live, dry-run and backtest share the same code (REQ-001).
"""

from __future__ import annotations

import abc
from collections.abc import AsyncIterator

from app.domain.types import (
    AccountState,
    Instrument,
    MarketEvent,
    Order,
    OrderRequest,
)


class MarketDataFeed(abc.ABC):
    """Yields market events in time order.

    * ``LiveFeed`` wraps the Bitunix WebSocket (real time).
    * ``BacktestFeed`` replays historical klines (simulated time).
    """

    @abc.abstractmethod
    def stream(self) -> AsyncIterator[MarketEvent]:
        """Async-iterate market events until the feed ends (or forever, live)."""

    @abc.abstractmethod
    async def instruments(self) -> dict[str, Instrument]:
        """Return the tradable instruments known to this feed."""


class Broker(abc.ABC):
    """Executes orders and exposes account state.

    * ``LiveBroker`` calls the Bitunix REST API.
    * ``SimBroker`` simulates fills; used by both dry-run and backtest with an
      identical fill model, so the two modes agree (REQ-003).
    """

    @abc.abstractmethod
    async def submit(self, request: OrderRequest) -> Order:
        """Submit an order and return its (possibly partially filled) result."""

    @abc.abstractmethod
    async def account(self) -> AccountState:
        """Return the current account/portfolio snapshot."""

    @abc.abstractmethod
    async def set_mark(self, symbol: str, price: object) -> None:
        """Inform the broker of the latest price for mark-to-market (sim only)."""
