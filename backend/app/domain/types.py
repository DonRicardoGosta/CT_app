"""Core domain value types.

Money, prices and quantities use :class:`decimal.Decimal` for deterministic,
exact arithmetic (important for the live==backtest guarantee, REQ-003). Time is
always an aware UTC :class:`datetime`, sourced from the injected ``Clock`` — never
from ``datetime.now()`` inside strategies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum


class Mode(StrEnum):
    """Execution mode. Selects which collaborators are injected, nothing else."""

    LIVE = "live"
    DRY_RUN = "dry_run"
    BACKTEST = "backtest"


class Side(StrEnum):
    """Order side."""

    BUY = "buy"
    SELL = "sell"

    @property
    def sign(self) -> int:
        """+1 for BUY, -1 for SELL (signed quantity helper)."""
        return 1 if self is Side.BUY else -1


class PositionSide(StrEnum):
    """Position direction for hedge-mode accounts."""

    LONG = "long"
    SHORT = "short"

    @property
    def sign(self) -> int:
        return 1 if self is PositionSide.LONG else -1

    @classmethod
    def from_side(cls, side: Side) -> PositionSide:
        return cls.LONG if side is Side.BUY else cls.SHORT


class OrderType(StrEnum):
    MARKET = "market"
    LIMIT = "limit"


class OrderStatus(StrEnum):
    NEW = "new"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"


class IntentAction(StrEnum):
    """High-level trade intent a strategy can emit (sizing is done by risk, REQ-007)."""

    OPEN = "open"
    CLOSE = "close"
    REDUCE = "reduce"


@dataclass(frozen=True, slots=True)
class Instrument:
    """Static contract metadata for a trading pair (from Bitunix ``trading_pairs``)."""

    symbol: str
    base: str
    quote: str
    min_trade_volume: Decimal  # minimum order size in base coin
    base_precision: int  # decimals allowed on quantity
    quote_precision: int  # decimals allowed on price
    min_leverage: int
    max_leverage: int
    default_leverage: int

    @property
    def min_notional_hint(self) -> Decimal:
        """Rough minimum notional (base min * 1) — refined with price by the sizer."""
        return self.min_trade_volume


@dataclass(frozen=True, slots=True)
class Bar:
    """A closed OHLCV candle."""

    symbol: str
    interval: str
    open_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


@dataclass(frozen=True, slots=True)
class Tick:
    """A last-price update."""

    symbol: str
    price: Decimal
    ts: datetime


class MarketEventType(StrEnum):
    BAR = "bar"
    TICK = "tick"


@dataclass(frozen=True, slots=True)
class MarketEvent:
    """A single event produced by a :class:`MarketDataFeed`.

    Carries either a :class:`Bar` (closed candle) or a :class:`Tick`. The engine
    handles both uniformly so live, dry-run and backtest share one loop.
    """

    type: MarketEventType
    ts: datetime
    symbol: str
    bar: Bar | None = None
    tick: Tick | None = None
    warmup: bool = False

    @property
    def price(self) -> Decimal:
        if self.bar is not None:
            return self.bar.close
        assert self.tick is not None
        return self.tick.price


@dataclass(frozen=True, slots=True)
class TakeProfitLeg:
    """One exchange take-profit: trigger price and size in base coin."""

    price: Decimal
    qty: Decimal


@dataclass(frozen=True, slots=True)
class ProtectionPlan:
    """TP/SL orders to place on the exchange after an entry fill (live mode)."""

    stop_price: Decimal
    take_profits: tuple[TakeProfitLeg, ...] = ()


@dataclass(frozen=True, slots=True)
class TradeIntent:
    """What a strategy *wants* to do. Sizing/leverage is decided by risk (REQ-007).

    A strategy emits direction and which symbol/step; it never computes quantities
    or talks to the exchange. ``weight`` lets a strategy express relative sizing of
    one ladder step (default 1.0 == one base unit defined by the risk config).
    """

    symbol: str
    side: Side
    action: IntentAction
    position_side: PositionSide
    weight: Decimal = Decimal("1")
    stop_price: Decimal | None = None  # for risk/loss estimation
    reason: str = ""
    tag: str = ""  # e.g. ladder step id


@dataclass(frozen=True, slots=True)
class OrderRequest:
    """A fully-sized, exchange-ready order produced by the risk sizer."""

    symbol: str
    side: Side
    order_type: OrderType
    qty: Decimal
    position_side: PositionSide
    leverage: int
    price: Decimal | None = None  # required for LIMIT
    reduce_only: bool = False
    client_id: str | None = None
    stop_price: Decimal | None = None
    reason: str = ""
    tag: str = ""

    @property
    def notional_at(self) -> Decimal:
        ref = self.price if self.price is not None else Decimal("0")
        return self.qty * ref


@dataclass(frozen=True, slots=True)
class Fill:
    """An execution against an order."""

    order_id: str
    symbol: str
    side: Side
    position_side: PositionSide
    qty: Decimal
    price: Decimal
    fee: Decimal
    ts: datetime
    realized_pnl: Decimal = Decimal("0")


@dataclass(slots=True)
class Order:
    """An order and its (possibly partial) execution result."""

    id: str
    symbol: str
    side: Side
    order_type: OrderType
    position_side: PositionSide
    qty: Decimal
    leverage: int
    status: OrderStatus
    ts: datetime
    price: Decimal | None = None
    filled_qty: Decimal = Decimal("0")
    avg_fill_price: Decimal = Decimal("0")
    reduce_only: bool = False
    client_id: str | None = None
    reason: str = ""
    tag: str = ""
    fills: list[Fill] = field(default_factory=list)


@dataclass(slots=True)
class Position:
    """A single open position (one per symbol+side in hedge mode).

    Ladder steps (REQ-006) accumulate into one position per direction. We store the
    committed margin explicitly so steps opened at different leverage multipliers
    are accounted correctly.
    """

    symbol: str
    position_side: PositionSide
    qty: Decimal  # always positive; direction is in position_side
    entry_price: Decimal
    leverage: int
    committed_margin: Decimal = Decimal("0")
    realized_pnl: Decimal = Decimal("0")
    mark_price: Decimal = Decimal("0")
    # Number of ladder steps that contributed to this position (REQ-006).
    step_count: int = 0

    @property
    def margin(self) -> Decimal:
        """Initial margin committed across all ladder steps."""
        return self.committed_margin

    @property
    def notional(self) -> Decimal:
        return self.entry_price * self.qty

    def unrealized_pnl(self, mark: Decimal) -> Decimal:
        return (mark - self.entry_price) * self.qty * Decimal(self.position_side.sign)


@dataclass(slots=True)
class AccountState:
    """A snapshot of account/portfolio state used by strategy and risk."""

    ts: datetime
    balance: Decimal  # realized cash balance
    positions: dict[tuple[str, PositionSide], Position] = field(default_factory=dict)

    def position(self, symbol: str, side: PositionSide) -> Position | None:
        return self.positions.get((symbol, side))

    def used_margin(self) -> Decimal:
        return sum((p.margin for p in self.positions.values()), Decimal("0"))

    def unrealized_pnl(self, marks: dict[str, Decimal]) -> Decimal:
        total = Decimal("0")
        for pos in self.positions.values():
            mark = marks.get(pos.symbol, pos.entry_price)
            total += pos.unrealized_pnl(mark)
        return total

    def equity(self, marks: dict[str, Decimal]) -> Decimal:
        """Total equity = cash balance + unrealized PnL."""
        return self.balance + self.unrealized_pnl(marks)
