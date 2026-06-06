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
from decimal import Decimal

from app.domain.types import (
    AccountState,
    Instrument,
    MarketEvent,
    Order,
    OrderRequest,
    PositionSide,
    ProtectionPlan,
    TakeProfitLeg,
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

    async def ensure_symbols(self, symbols: list[str]) -> list[str]:
        """Ensure live market data is subscribed for ``symbols``.

        Static feeds (backtest) can ignore this. Live feeds return the symbols
        newly subscribed, which lets the engine log batch expansion.
        """
        return []


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

    async def place_exchange_protections(
        self,
        *,
        symbol: str,
        position_side: PositionSide,
        plan: ProtectionPlan,
        instrument: Instrument,
        skip_sl: bool = False,
        take_profits: tuple[TakeProfitLeg, ...] | None = None,
    ) -> dict | None:
        """After an entry fill, register TP/SL on the exchange (live broker only)."""
        return None

    async def modify_stop(
        self,
        *,
        symbol: str,
        position_side: PositionSide,
        stop_price: Decimal,
        instrument: Instrument,
    ) -> bool:
        """Move the exchange stop-loss of an open position (live broker only).

        Used to implement a breakeven/trailing stop after partial take-profits.
        Returns ``True`` when the exchange accepted the modification. Simulated
        brokers (dry-run/backtest) manage stops via strategy intents instead, so
        this is a no-op for them.
        """
        return False
