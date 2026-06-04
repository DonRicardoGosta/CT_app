"""Exchange-native TP/SL (live broker) tests."""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.brokers.live import LiveBroker
from app.domain.types import (
    Instrument,
    OrderRequest,
    OrderStatus,
    OrderType,
    PositionSide,
    ProtectionPlan,
    Side,
    TakeProfitLeg,
)
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
            "tp1_close_pct": "35",
            "tp2_pct": "2",
            "tp2_close_pct": "100",
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
    assert len(plan.take_profits) == 2
    assert plan.take_profits[0].price == Decimal("101.0")
    assert plan.take_profits[0].qty == Decimal("0.350")
    assert plan.take_profits[1].price == Decimal("102.0")
    assert plan.take_profits[1].qty == Decimal("0.650")
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
async def test_rest_place_order_includes_bundled_protection(btc_instrument: Instrument):
    rest = BitunixRest(api_key="k", secret_key="s")
    captured: list[dict] = []

    async def fake_request(_method, _path, *, body=None, **_kw):
        captured.append(body or {})
        return {"orderId": "o1"}

    rest._request = fake_request  # type: ignore[method-assign]
    plan = ProtectionPlan(
        stop_price=Decimal("98"),
        take_profits=(TakeProfitLeg(price=Decimal("101"), qty=Decimal("0.3")),),
    )
    req = OrderRequest(
        symbol="BTCUSDT",
        side=Side.BUY,
        order_type=OrderType.MARKET,
        qty=Decimal("1"),
        position_side=PositionSide.LONG,
        leverage=5,
        protection=plan,
    )
    await rest.place_order(req)
    # Only the SL is bundled with the entry; TP legs go via tpsl/place_order so
    # partial-close quantities are preserved (the trade endpoint has no tpQty).
    assert captured[0]["slPrice"] == "98"
    assert captured[0]["slStopType"] == "LAST_PRICE"
    assert captured[0]["slOrderType"] == "MARKET"
    assert "tpPrice" not in captured[0]


@pytest.mark.asyncio
async def test_submit_open_bundled_protection_verified(btc_instrument: Instrument):
    rest = MagicMock(spec=BitunixRest)
    rest.get_positions = AsyncMock(
        side_effect=[
            [],
            [
                {
                    "symbol": "BTCUSDT",
                    "qty": "1",
                    "positionSide": "LONG",
                    "positionId": "p1",
                    "avgOpenPrice": "100",
                }
            ],
        ]
    )
    rest.set_leverage = AsyncMock(return_value={})
    rest.place_order = AsyncMock(return_value={"orderId": "o1"})
    rest.get_pending_tpsl_orders = AsyncMock(return_value=[{"id": "sl-1"}])
    broker = LiveBroker(rest)
    broker._CONFIRM_ATTEMPTS = 3
    broker._CONFIRM_DELAY_S = 0
    plan = ProtectionPlan(
        stop_price=Decimal("98"),
        take_profits=(TakeProfitLeg(price=Decimal("101"), qty=Decimal("1")),),
    )
    req = OrderRequest(
        symbol="BTCUSDT",
        side=Side.BUY,
        order_type=OrderType.MARKET,
        qty=Decimal("1"),
        position_side=PositionSide.LONG,
        leverage=5,
        protection=plan,
    )
    order = await broker.submit(req)
    assert order.status is OrderStatus.FILLED
    assert order.bundled_sl is True
    assert order.bundled_tp == 0
    assert order.bundled_protection_ok is True
    sent = rest.place_order.await_args.args[0]
    assert sent.protection is plan


@pytest.mark.asyncio
async def test_place_exchange_protections_skip_sl_places_tp_only(btc_instrument: Instrument):
    rest = MagicMock(spec=BitunixRest)
    rest.get_positions = AsyncMock(
        return_value=[
            {"symbol": "BTCUSDT", "qty": "1", "positionSide": "LONG", "positionId": "p1"}
        ]
    )
    rest.place_tpsl_order = AsyncMock(return_value={"orderId": "tp-2"})
    rest.get_pending_tpsl_orders = AsyncMock(return_value=[{"id": "tp-2"}])
    broker = LiveBroker(rest)
    plan = ProtectionPlan(
        stop_price=Decimal("98"),
        take_profits=(
            TakeProfitLeg(price=Decimal("101"), qty=Decimal("0.3")),
            TakeProfitLeg(price=Decimal("102"), qty=Decimal("0.7")),
        ),
    )
    rest.place_tpsl_order = AsyncMock(
        side_effect=[{"orderId": "tp-1"}, {"orderId": "tp-2"}]
    )
    result = await broker.place_exchange_protections(
        symbol="BTCUSDT",
        position_side=PositionSide.LONG,
        plan=plan,
        instrument=btc_instrument,
        skip_sl=True,
        take_profits=plan.take_profits,
    )
    # All TP legs placed, none of them an SL call (SL was bundled with entry).
    assert rest.place_tpsl_order.await_count == 2
    for call in rest.place_tpsl_order.await_args_list:
        assert call.kwargs.get("sl_price") is None
        assert call.kwargs.get("tp_price") is not None
    assert result["sl_placed"] is True
    assert result["tp_placed"] == 2


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
    rest.get_pending_tpsl_orders = AsyncMock(return_value=[{"id": "sl-1"}, {"id": "tp-1"}])
    broker = LiveBroker(rest)
    plan = ProtectionPlan(
        stop_price=Decimal("98"),
        take_profits=(TakeProfitLeg(price=Decimal("101"), qty=Decimal("0.3")),),
    )
    result = await broker.place_exchange_protections(
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
    assert result["sl_placed"] is True
    assert result["tp_placed"] == 1
    assert result["verified_count"] == 2


@pytest.mark.asyncio
async def test_live_broker_continues_tp_when_sl_fails(btc_instrument: Instrument):
    rest = MagicMock(spec=BitunixRest)
    rest.get_positions = AsyncMock(
        return_value=[
            {"symbol": "BTCUSDT", "qty": "1", "positionSide": "LONG", "positionId": "p1"}
        ]
    )
    # SL raises, TP succeeds — TP must still be attempted.
    rest.place_tpsl_order = AsyncMock(
        side_effect=[RuntimeError("sl rejected"), {"orderId": "tp-1"}]
    )
    rest.get_pending_tpsl_orders = AsyncMock(return_value=[{"id": "tp-1"}])
    broker = LiveBroker(rest)
    plan = ProtectionPlan(
        stop_price=Decimal("98"),
        take_profits=(TakeProfitLeg(price=Decimal("101"), qty=Decimal("1")),),
    )
    result = await broker.place_exchange_protections(
        symbol="BTCUSDT",
        position_side=PositionSide.LONG,
        plan=plan,
        instrument=btc_instrument,
    )
    assert rest.place_tpsl_order.await_count == 2
    assert result["sl_placed"] is False
    assert result["tp_placed"] == 1


@pytest.mark.asyncio
async def test_submit_open_rejected_when_no_position_appears(btc_instrument: Instrument):
    from app.domain.types import OrderType, Side

    rest = MagicMock(spec=BitunixRest)
    # Position never appears -> open must be reported as REJECTED.
    rest.get_positions = AsyncMock(return_value=[])
    rest.set_leverage = AsyncMock(return_value={})
    rest.place_order = AsyncMock(return_value={"orderId": "o1"})
    broker = LiveBroker(rest)
    broker._CONFIRM_ATTEMPTS = 2
    broker._CONFIRM_DELAY_S = 0
    req = OrderRequest(
        symbol="BTCUSDT",
        side=Side.BUY,
        order_type=OrderType.MARKET,
        qty=Decimal("1"),
        position_side=PositionSide.LONG,
        leverage=5,
        tag="entry_1",
    )
    order = await broker.submit(req)
    assert order.status is OrderStatus.REJECTED
    assert "no position" in order.reason


@pytest.mark.asyncio
async def test_submit_open_confirmed_uses_exchange_qty(btc_instrument: Instrument):
    from app.domain.types import OrderType, Side

    rest = MagicMock(spec=BitunixRest)
    # Baseline empty, then the position shows up after the order.
    rest.get_positions = AsyncMock(
        side_effect=[
            [],
            [
                {
                    "symbol": "BTCUSDT",
                    "qty": "0.5",
                    "positionSide": "LONG",
                    "positionId": "p1",
                    "avgOpenPrice": "100",
                }
            ],
        ]
    )
    rest.set_leverage = AsyncMock(return_value={})
    rest.place_order = AsyncMock(return_value={"orderId": "o1"})
    broker = LiveBroker(rest)
    broker._CONFIRM_ATTEMPTS = 3
    broker._CONFIRM_DELAY_S = 0
    req = OrderRequest(
        symbol="BTCUSDT",
        side=Side.BUY,
        order_type=OrderType.MARKET,
        qty=Decimal("0.5"),
        position_side=PositionSide.LONG,
        leverage=5,
        tag="entry_1",
    )
    order = await broker.submit(req)
    assert order.status is OrderStatus.FILLED
    assert order.filled_qty == Decimal("0.5")
    assert order.avg_fill_price == Decimal("100")
