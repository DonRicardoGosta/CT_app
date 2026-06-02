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
    planned_entry: Decimal | None = None
    stop_loss: Decimal | None = None
    take_profit: Decimal | None = None


class MarketPriceEvent(BaseEvent):
    """Last-price update for realtime symbol cards and current price lines."""

    type: Literal[EventType.MARKET] = EventType.MARKET
    symbol: str
    price: Decimal
    source: str = "engine"


class CandleEvent(BaseEvent):
    """Closed OHLCV candle for charting and backtest replay."""

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
    """Chart overlay levels for planned/actual entry, TP and SL."""

    type: Literal[EventType.TRADE_LEVEL] = EventType.TRADE_LEVEL
    symbol: str
    position_side: str | None = None
    current_price: Decimal | None = None
    planned_entry: Decimal | None = None
    actual_entry: Decimal | None = None
    take_profit: Decimal | None = None
    stop_loss: Decimal | None = None
    liquidation_price: Decimal | None = None
    source: str = "engine"


class SymbolSummaryEvent(BaseEvent):
    """Compact per-symbol state for the five always-visible symbol cards."""

    type: Literal[EventType.SYMBOL_SUMMARY] = EventType.SYMBOL_SUMMARY
    symbol: str
    status: str
    last_price: Decimal | None = None
    change_pct: Decimal | None = None
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
    EventType.SYMBOL_SUMMARY: SymbolSummaryEvent,
    EventType.RUN: RunEvent,
}
