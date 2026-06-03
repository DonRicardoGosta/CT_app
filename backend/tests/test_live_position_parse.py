"""LiveBroker position parsing: entry price must be a price, not notional."""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.brokers.live import LiveBroker
from app.domain.types import PositionSide
from app.exchange.bitunix.rest import BitunixRest


@pytest.mark.asyncio
async def test_account_uses_avg_open_price_not_entry_value():
    rest = MagicMock(spec=BitunixRest)
    rest.get_account = AsyncMock(return_value={"available": "30"})
    # entryValue is the position notional (price x qty), NOT the entry price.
    rest.get_positions = AsyncMock(
        return_value=[
            {
                "symbol": "ENAUSDT",
                "qty": "189",
                "side": "LONG",
                "leverage": 20,
                "avgOpenPrice": "0.105",
                "entryValue": "19.95",
                "margin": "1.01",
            }
        ]
    )
    broker = LiveBroker(rest)
    state = await broker.account()
    pos = state.position("ENAUSDT", PositionSide.LONG)
    assert pos is not None
    assert pos.entry_price == Decimal("0.105")
    # uPnL at mark 0.11 must be tiny, not thousands.
    upnl = pos.unrealized_pnl(Decimal("0.11"))
    assert abs(upnl) < Decimal("2")


@pytest.mark.asyncio
async def test_account_falls_back_to_entry_value_over_qty():
    rest = MagicMock(spec=BitunixRest)
    rest.get_account = AsyncMock(return_value={"available": "30"})
    # No avgOpenPrice -> derive price from notional / qty.
    rest.get_positions = AsyncMock(
        return_value=[
            {
                "symbol": "ENAUSDT",
                "qty": "189",
                "side": "LONG",
                "leverage": 20,
                "entryValue": "19.845",
            }
        ]
    )
    broker = LiveBroker(rest)
    state = await broker.account()
    pos = state.position("ENAUSDT", PositionSide.LONG)
    assert pos is not None
    assert pos.entry_price == Decimal("0.105")


@pytest.mark.asyncio
async def test_position_snapshot_uses_avg_open_price():
    rest = MagicMock(spec=BitunixRest)
    rest.get_positions = AsyncMock(
        return_value=[
            {
                "symbol": "ENAUSDT",
                "qty": "189",
                "positionSide": "LONG",
                "positionId": "p1",
                "avgOpenPrice": "0.105",
                "entryValue": "19.95",
            }
        ]
    )
    broker = LiveBroker(rest)
    pid, qty, entry = await broker._position_snapshot("ENAUSDT", PositionSide.LONG)
    assert pid == "p1"
    assert qty == Decimal("189")
    assert entry == Decimal("0.105")
