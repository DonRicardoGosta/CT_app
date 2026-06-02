"""Typed event payloads emitted to the bus and persisted to the DB.

Each event carries enough context (``run_id``, ``mode``, ``ts``) to be stored and
queried independently. Events are intentionally flat and serializable with orjson.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class EventType(StrEnum):
    ORDER = "order"
    FILL = "fill"
    POSITION = "position"
    SIGNAL = "signal"
    EQUITY = "equity"
    ERROR = "error"
    MARKET = "market"
    CANDLE = "candle"
    TRADE_LEVEL = "trade_level"
    WATCHLIST = "watchlist"
    SYMBOL_SUMMARY = "symbol_summary"
    RUN = "run"


class BaseEvent(BaseModel):
    """Common fields for every event."""

    model_config = ConfigDict(use_enum_values=True)

    type: EventType
    run_id: str
    mode: str
    ts: datetime


class OrderEvent(BaseEvent):
    type: Literal[EventType.ORDER] = EventType.ORDER
    order_id: str
    client_id: str | None = None
    symbol: str
    side: str
    position_side: str
    order_type: str
    qty: Decimal
    price: Decimal | None = None
    leverage: int
    status: str
    filled_qty: Decimal = Decimal("0")
    avg_fill_price: Decimal = Decimal("0")
    reduce_only: bool = False
    reason: str = ""
    tag: str = ""


class FillEvent(BaseEvent):
    type: Literal[EventType.FILL] = EventType.FILL
    order_id: str
    symbol: str
    side: str
    position_side: str
    qty: Decimal
    price: Decimal
    fee: Decimal
    realized_pnl: Decimal = Decimal("0")


class PositionEvent(BaseEvent):
    type: Literal[EventType.POSITION] = EventType.POSITION
    symbol: str
    position_side: str
    qty: Decimal
    entry_price: Decimal
    mark_price: Decimal
    leverage: int
    margin: Decimal
    unrealized_pnl: Decimal
    realized_pnl: Decimal
    step_count: int = 0


class SignalEvent(BaseEvent):
    type: Literal[EventType.SIGNAL] = EventType.SIGNAL
    strategy: str
    symbol: str
    side: str
    action: str
    weight: Decimal
    reason: str = ""
    tag: str = ""


class MarketPriceEvent(BaseEvent):
    """Last-price tick for a symbol, used for the live price line/ticker."""

    type: Literal[EventType.MARKET] = EventType.MARKET
    symbol: str
    price: Decimal
    source: str = "engine"


class CandleEvent(BaseEvent):
    """A closed OHLCV candle at the run's interval (for live chart updates)."""

    type: Literal[EventType.CANDLE] = EventType.CANDLE
    symbol: str
    interval: str
    open_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    closed: bool = True


class TradeLevelEvent(BaseEvent):
    """Chart overlay levels for planned/actual entry, TP and SL per symbol.

    ``take_profit``/``stop_loss`` keep the single nearest level for backward
    compatibility, while ``take_profits``/``stops`` carry all levels for strategies
    that scale out at several targets and move their stop over a trade's life.
    """

    type: Literal[EventType.TRADE_LEVEL] = EventType.TRADE_LEVEL
    symbol: str
    position_side: str | None = None
    current_price: Decimal | None = None
    planned_entry: Decimal | None = None
    actual_entry: Decimal | None = None
    take_profit: Decimal | None = None
    stop_loss: Decimal | None = None
    take_profits: list[Decimal] = Field(default_factory=list)
    stops: list[Decimal] = Field(default_factory=list)
    source: str = "engine"


class WatchlistEvent(BaseEvent):
    """The coins the strategy is trading + the ones it is still scanning.

    ``symbols`` is the *selected* (tradeable) set. While the strategy is still
    looking for setups, ``len(symbols) < target`` and ``complete`` is ``False`` so
    the UI can show a "Selecting coins (N/target)" state.
    """

    type: Literal[EventType.WATCHLIST] = EventType.WATCHLIST
    symbols: list[str] = Field(default_factory=list)
    scanning: list[str] = Field(default_factory=list)
    target: int = 0
    complete: bool = False
    interval: str = "1m"
    strategy: str = ""


class SymbolSummaryEvent(BaseEvent):
    """Compact per-symbol state for the coin cards."""

    type: Literal[EventType.SYMBOL_SUMMARY] = EventType.SYMBOL_SUMMARY
    symbol: str
    status: str = "scanning"  # scanning | pending_order | in_position
    last_price: Decimal | None = None
    position_side: str | None = None
    unrealized_pnl: Decimal | None = None
    realized_pnl: Decimal | None = None
    step_count: int | None = None
    max_steps: int | None = None
    last_signal_reason: str = ""


class EquityEvent(BaseEvent):
    type: Literal[EventType.EQUITY] = EventType.EQUITY
    balance: Decimal
    equity: Decimal
    used_margin: Decimal
    unrealized_pnl: Decimal
    open_positions: int


class ErrorEvent(BaseEvent):
    type: Literal[EventType.ERROR] = EventType.ERROR
    source: str
    severity: str = "error"
    message: str
    detail: str = ""
    context: dict[str, Any] = Field(default_factory=dict)


class RunEvent(BaseEvent):
    type: Literal[EventType.RUN] = EventType.RUN
    strategy: str
    status: str  # started | finished | failed | stopped
    detail: str = ""


# Mapping used by the consumer to route a topic's payload back to a model.
EVENT_MODELS: dict[EventType, type[BaseEvent]] = {
    EventType.ORDER: OrderEvent,
    EventType.FILL: FillEvent,
    EventType.POSITION: PositionEvent,
    EventType.SIGNAL: SignalEvent,
    EventType.EQUITY: EquityEvent,
    EventType.ERROR: ErrorEvent,
    EventType.MARKET: MarketPriceEvent,
    EventType.CANDLE: CandleEvent,
    EventType.TRADE_LEVEL: TradeLevelEvent,
    EventType.WATCHLIST: WatchlistEvent,
    EventType.SYMBOL_SUMMARY: SymbolSummaryEvent,
    EventType.RUN: RunEvent,
}
