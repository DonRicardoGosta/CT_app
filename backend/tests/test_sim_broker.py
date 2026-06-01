"""SimBroker accounting: open, ladder increase, close with realized PnL."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.domain.brokers.sim import SimBroker
from app.domain.clock import SimulatedClock
from app.domain.types import OrderRequest, OrderType, PositionSide, Side


@pytest.fixture
def broker(instruments):
    clock = SimulatedClock(datetime(2024, 1, 1, tzinfo=UTC))
    return SimBroker(clock, instruments, Decimal("1000"), fee_rate=Decimal("0"))


def _open(qty: str, price: str, lev: int = 5) -> OrderRequest:
    return OrderRequest(
        symbol="BTCUSDT",
        side=Side.BUY,
        order_type=OrderType.MARKET,
        qty=Decimal(qty),
        position_side=PositionSide.LONG,
        leverage=lev,
    )


@pytest.mark.asyncio
async def test_open_and_ladder_increase(broker):
    await broker.set_mark("BTCUSDT", "100")
    await broker.submit(_open("0.1", "100"))
    await broker.set_mark("BTCUSDT", "110")
    await broker.submit(_open("0.1", "110"))

    acct = await broker.account()
    pos = acct.position("BTCUSDT", PositionSide.LONG)
    assert pos is not None
    assert pos.qty == Decimal("0.2")
    assert pos.step_count == 2
    # Weighted average entry of 100 and 110.
    assert pos.entry_price == Decimal("105")


@pytest.mark.asyncio
async def test_close_realizes_pnl(broker):
    await broker.set_mark("BTCUSDT", "100")
    await broker.submit(_open("1", "100"))
    await broker.set_mark("BTCUSDT", "120")
    close = OrderRequest(
        symbol="BTCUSDT",
        side=Side.SELL,
        order_type=OrderType.MARKET,
        qty=Decimal("1"),
        position_side=PositionSide.LONG,
        leverage=5,
        reduce_only=True,
    )
    await broker.submit(close)
    acct = await broker.account()
    assert acct.position("BTCUSDT", PositionSide.LONG) is None
    # Profit = (120-100)*1 = 20, no fees.
    assert acct.balance == Decimal("1020")
