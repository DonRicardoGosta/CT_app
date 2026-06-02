"""The trading workspace depends on new realtime events: watchlist, market price,
candles, trade levels and symbol summaries. Verify the engine emits them."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.domain.brokers.sim import SimBroker
from app.domain.clock import SimulatedClock
from app.domain.engine import Engine
from app.domain.feeds.replay import ReplayFeed
from app.domain.types import Mode, PositionSide
from app.events.bus import InMemorySink
from app.events.schemas import (
    CandleEvent,
    ErrorEvent,
    MarketPriceEvent,
    SymbolSummaryEvent,
    TradeLevelEvent,
    WatchlistEvent,
)
from app.risk.config import RiskParams
from app.risk.sizer import RiskSizer
from app.strategies import create_strategy

_EPOCH = datetime(2024, 1, 1, tzinfo=UTC)


async def _run(bars, instruments, sink: InMemorySink):
    strategy = create_strategy(
        "autoscan_ladder",
        {"ema_fast": 5, "ema_slow": 10, "ladder_steps": 3, "ladder_step_spacing_pct": "0.5"},
    )
    sizer = RiskSizer(
        RiskParams(min_investment_usd=Decimal("1"), max_capital_usd=Decimal("50"), base_leverage=5)
    )
    clock = SimulatedClock(bars[0].open_time)
    feed = ReplayFeed(bars, instruments, clock=clock)
    broker = SimBroker(clock, instruments, Decimal("1000"), fee_rate=Decimal("0.0006"))
    engine = Engine(
        mode=Mode.BACKTEST,
        strategy=strategy,
        sizer=sizer,
        broker=broker,
        feed=feed,
        clock=clock,
        sink=sink,
    )
    return await engine.run()


@pytest.mark.asyncio
async def test_workspace_events_emitted(bars, instruments):
    sink = InMemorySink()
    await _run(bars, instruments, sink)

    def of(model):
        return [e for e in sink.events if isinstance(e, model)]

    watchlists = of(WatchlistEvent)
    assert watchlists, "a watchlist event must be emitted on start"
    assert "BTCUSDT" in watchlists[0].symbols

    assert of(MarketPriceEvent), "market price events expected per market event"
    assert of(CandleEvent), "candle events expected per closed bar"
    assert of(SymbolSummaryEvent), "symbol summaries expected (scanning + updates)"

    levels = of(TradeLevelEvent)
    assert levels, "trade level events expected once the strategy opens positions"
    # At least one level carries a take-profit and a stop-loss price.
    assert any(lvl.take_profit is not None for lvl in levels)
    assert any(lvl.stop_loss is not None for lvl in levels)

    logs = of(ErrorEvent)
    assert any(log.severity == "info" and log.source == "engine" for log in logs)
    assert any(log.message == "watchlist selected" for log in logs)


def test_take_profit_price_uses_leverage(instruments):
    """TP% is ROE on margin, so the price move must be divided by leverage."""
    strategy = create_strategy("autoscan_ladder", {"take_profit_pct": "10"})
    sizer = RiskSizer(RiskParams(base_leverage=10))
    clock = SimulatedClock(_EPOCH)
    broker = SimBroker(clock, instruments, Decimal("1000"))
    engine = Engine(
        mode=Mode.DRY_RUN,
        strategy=strategy,
        sizer=sizer,
        broker=broker,
        feed=ReplayFeed([], instruments),
        clock=clock,
        sink=InMemorySink(),
    )
    # 10% ROE at 10x => 1% price move. Long TP above entry, short TP below.
    tp_long = engine._take_profit_price(Decimal("100"), PositionSide.LONG, 10)
    tp_short = engine._take_profit_price(Decimal("100"), PositionSide.SHORT, 10)
    assert tp_long == Decimal("101.0")
    assert tp_short == Decimal("99.0")
