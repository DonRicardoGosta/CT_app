"""Simulated broker.

Used by BOTH dry-run and backtest with an identical fill model. The only thing
that differs between those two modes is *where the price comes from* (live ticks
vs historical bars); the accounting here is the same, which is what makes a
backtest reproduce a dry-run on the same price series (REQ-003).

Accounting model (simplified linear USDT-margined futures):

* ``balance`` is the realized wallet balance (starts at the initial capital).
* Opening/increasing a position deducts only the trading ``fee`` from balance;
  leverage means the notional is not subtracted from the wallet.
* Closing realizes PnL into the balance and deducts the fee.
* ``equity = balance + unrealized_pnl``. Margin is a constraint enforced by the
  risk sizer, tracked per position as ``committed_margin``.
"""

from __future__ import annotations

from decimal import Decimal

from app.domain.clock import Clock
from app.domain.interfaces import Broker
from app.domain.types import (
    AccountState,
    Fill,
    Instrument,
    Order,
    OrderRequest,
    OrderStatus,
    Position,
    PositionSide,
    Side,
)


class SimBroker(Broker):
    """In-memory deterministic broker."""

    def __init__(
        self,
        clock: Clock,
        instruments: dict[str, Instrument],
        initial_balance: Decimal,
        fee_rate: Decimal = Decimal("0.0006"),
        slippage_bps: Decimal = Decimal("0"),
    ) -> None:
        self._clock = clock
        self._instruments = instruments
        self.balance = initial_balance
        self.fee_rate = fee_rate
        # Slippage applied adversely on opening (the part backtests legitimately
        # may differ on, per the spec). Default 0 for exact reproducibility.
        self.slippage_bps = slippage_bps
        self._positions: dict[tuple[str, PositionSide], Position] = {}
        self._marks: dict[str, Decimal] = {}
        self._order_seq = 0

    # -- Broker interface --------------------------------------------------- #
    async def set_mark(self, symbol: str, price: object) -> None:
        self._marks[symbol] = Decimal(str(price))
        pos_long = self._positions.get((symbol, PositionSide.LONG))
        if pos_long:
            pos_long.mark_price = self._marks[symbol]
        pos_short = self._positions.get((symbol, PositionSide.SHORT))
        if pos_short:
            pos_short.mark_price = self._marks[symbol]

    async def account(self) -> AccountState:
        return AccountState(
            ts=self._clock.now(),
            balance=self.balance,
            positions=dict(self._positions),
        )

    async def submit(self, request: OrderRequest) -> Order:
        self._order_seq += 1
        order_id = f"sim-{self._order_seq}"
        ts = self._clock.now()
        ref_price = self._marks.get(request.symbol)
        if ref_price is None or ref_price <= 0:
            return Order(
                id=order_id,
                symbol=request.symbol,
                side=request.side,
                order_type=request.order_type,
                position_side=request.position_side,
                qty=request.qty,
                leverage=request.leverage,
                status=OrderStatus.REJECTED,
                ts=ts,
                reason="no market price",
                tag=request.tag,
            )

        fill_price = self._apply_slippage(ref_price, request.side, request.reduce_only)
        fee = (fill_price * request.qty * self.fee_rate).copy_abs()
        realized = self._apply_fill(request, fill_price, fee)

        fill = Fill(
            order_id=order_id,
            symbol=request.symbol,
            side=request.side,
            position_side=request.position_side,
            qty=request.qty,
            price=fill_price,
            fee=fee,
            ts=ts,
            realized_pnl=realized,
        )
        return Order(
            id=order_id,
            symbol=request.symbol,
            side=request.side,
            order_type=request.order_type,
            position_side=request.position_side,
            qty=request.qty,
            leverage=request.leverage,
            status=OrderStatus.FILLED,
            ts=ts,
            price=request.price,
            filled_qty=request.qty,
            avg_fill_price=fill_price,
            reduce_only=request.reduce_only,
            client_id=request.client_id,
            reason=request.reason,
            tag=request.tag,
            fills=[fill],
        )

    # -- internals ---------------------------------------------------------- #
    def _apply_slippage(self, price: Decimal, side: Side, reduce_only: bool) -> Decimal:
        if self.slippage_bps == 0 or reduce_only:
            return price
        adj = price * (self.slippage_bps / Decimal("10000"))
        return price + adj if side is Side.BUY else price - adj

    def _apply_fill(self, request: OrderRequest, fill_price: Decimal, fee: Decimal) -> Decimal:
        key = (request.symbol, request.position_side)
        pos = self._positions.get(key)
        realized = Decimal("0")

        closing = request.reduce_only or self._is_closing(request)
        if closing and pos is not None:
            close_qty = min(request.qty, pos.qty)
            realized = (
                (fill_price - pos.entry_price)
                * close_qty
                * Decimal(pos.position_side.sign)
            )
            # Release committed margin proportionally.
            if pos.qty > 0:
                released = pos.committed_margin * (close_qty / pos.qty)
            else:
                released = Decimal("0")
            pos.qty -= close_qty
            pos.committed_margin -= released
            pos.realized_pnl += realized
            self.balance += realized - fee
            if pos.qty <= 0:
                self._positions.pop(key, None)
            return realized

        # Opening or increasing in the position's own direction.
        self.balance -= fee
        committed = (fill_price * request.qty) / Decimal(request.leverage)
        if pos is None:
            self._positions[key] = Position(
                symbol=request.symbol,
                position_side=request.position_side,
                qty=request.qty,
                entry_price=fill_price,
                leverage=request.leverage,
                committed_margin=committed,
                mark_price=fill_price,
                step_count=1,
            )
        else:
            total_qty = pos.qty + request.qty
            pos.entry_price = (
                pos.entry_price * pos.qty + fill_price * request.qty
            ) / total_qty
            pos.qty = total_qty
            pos.committed_margin += committed
            pos.leverage = request.leverage
            pos.step_count += 1
        return realized

    def _is_closing(self, request: OrderRequest) -> bool:
        """A non-reduce order whose side opposes an existing position closes it."""
        pos = self._positions.get((request.symbol, request.position_side))
        if pos is None:
            return False
        same_dir = (
            request.side is Side.BUY and request.position_side is PositionSide.LONG
        ) or (request.side is Side.SELL and request.position_side is PositionSide.SHORT)
        return not same_dir
