"""Exchange-native TP/SL (live broker) tests."""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.brokers.live import LiveBroker
from app.domain.types import Instrument, PositionSide, ProtectionPlan, TakeProfitLeg
from app.exchange.bitunix.rest import BitunixRest
from app.strategies.registry import create_strategy


@pytest.fixture
def btc_instrument() -> Instrument:
    return Instrument(
        symbol="BTCUSDT",
        base="BTC",
        quote="USDT",
        min_trade_volume=Decimal("0.001"),
        base_precision=3,
        quote_precision=2,
        min_leverage=1,
        max_leverage=50,
        default_leverage=10,
    )


def test_protection_plan_splits_tp_qty(btc_instrument: Instrument):
    strat = create_strategy(
        "trend_scanner",
        {
            "tp1_pct": "1",
            "tp1_close_pct": "30",
            "tp2_pct": "2",
            "tp2_close_pct": "35",
            "tp3_pct": "3",
            "tp3_close_pct": "100",
            "stop_loss_pct": "1.2",
        },
    )
    plan = strat.protection_plan(
        "BTCUSDT",
        PositionSide.LONG,
        Decimal("100"),
        Decimal("1"),
        btc_instrument,
    )
    assert plan is not None
    assert plan.stop_price == Decimal("98.8")
    assert len(plan.take_profits) == 3
    assert plan.take_profits[0].price == Decimal("101.0")
    assert plan.take_profits[0].qty == Decimal("0.300")
    assert plan.take_profits[1].qty == Decimal("0.245")
    assert plan.take_profits[2].qty == Decimal("0.455")
    total_tp = sum(leg.qty for leg in plan.take_profits)
    assert total_tp == Decimal("1")


def test_manage_position_skips_software_exits_when_exchange_protections():
    strat = create_strategy("trend_scanner", {"stop_loss_pct": "1"})
    strat._exchange_protections = True
    strat._stop[("BTCUSDT", "long")] = Decimal("99")
    pos = MagicMock(entry_price=Decimal("100"), qty=Decimal("1"))
    # Price far below stop — would close in sim, but must not emit intents.
    intents = strat._manage_position("BTCUSDT", PositionSide.LONG, pos, Decimal("50"))
    assert intents == []


@pytest.mark.asyncio
async def test_live_broker_places_sl_and_tp_orders(btc_instrument: Instrument):
    rest = MagicMock(spec=BitunixRest)
    rest.get_positions = AsyncMock(
        return_value=[
            {
                "symbol": "BTCUSDT",
                "qty": "1",
                "positionSide": "LONG",
                "positionId": "pos-42",
            }
        ]
    )
    rest.place_tpsl_order = AsyncMock(side_effect=[{"orderId": "sl-1"}, {"orderId": "tp-1"}])
    broker = LiveBroker(rest)
    plan = ProtectionPlan(
        stop_price=Decimal("98"),
        take_profits=(TakeProfitLeg(price=Decimal("101"), qty=Decimal("0.3")),),
    )
    await broker.place_exchange_protections(
        symbol="BTCUSDT",
        position_side=PositionSide.LONG,
        plan=plan,
        instrument=btc_instrument,
    )
    assert rest.place_tpsl_order.await_count == 2
    sl_call = rest.place_tpsl_order.await_args_list[0].kwargs
    assert sl_call["sl_price"] == "98"
    assert sl_call["sl_qty"] == "1.000"
    tp_call = rest.place_tpsl_order.await_args_list[1].kwargs
    assert tp_call["tp_price"] == "101"
    assert tp_call["tp_qty"] == "0.300"
