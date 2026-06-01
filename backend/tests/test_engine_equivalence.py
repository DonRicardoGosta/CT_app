"""The headline guarantee: backtest and dry-run produce identical results on the
same price series (apart from timestamps), because they share strategy, engine and
fill model and differ only in the injected clock/feed (REQ-003)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.brokers.sim import SimBroker
from app.domain.clock import RealClock, SimulatedClock
from app.domain.engine import Engine
from app.domain.feeds.replay import ReplayFeed
from app.domain.types import Mode
from app.events.bus import InMemorySink
from app.risk.config import RiskParams
from app.risk.sizer import RiskSizer
from app.strategies import create_strategy


async def _run(mode: Mode, bars, instruments, sink: InMemorySink):
    strategy = create_strategy(
        "autoscan_ladder",
        {"ema_fast": 5, "ema_slow": 10, "ladder_steps": 3, "ladder_step_spacing_pct": "0.5"},
    )
    sizer = RiskSizer(
        RiskParams(
            min_investment_usd=Decimal("1"),
            max_capital_usd=Decimal("50"),
            base_leverage=5,
            max_leverage=20,
            fee_rate=Decimal("0.0006"),
        )
    )
    if mode is Mode.BACKTEST:
        clock = SimulatedClock(bars[0].open_time)
        feed = ReplayFeed(bars, instruments, clock=clock)
    else:  # DRY_RUN: wall clock, same bars
        clock = RealClock()
        feed = ReplayFeed(bars, instruments, clock=None)
    broker = SimBroker(clock, instruments, Decimal("1000"), fee_rate=Decimal("0.0006"))
    engine = Engine(
        mode=mode, strategy=strategy, sizer=sizer, broker=broker, feed=feed, clock=clock, sink=sink
    )
    return await engine.run()


@pytest.mark.asyncio
async def test_backtest_equals_dry_run(bars, instruments):
    bt_sink, dr_sink = InMemorySink(), InMemorySink()
    bt = await _run(Mode.BACKTEST, bars, instruments, bt_sink)
    dr = await _run(Mode.DRY_RUN, bars, instruments, dr_sink)

    # Meaningful activity occurred.
    assert bt.orders > 0

    # Identical final accounting.
    assert bt.final_balance == dr.final_balance
    assert bt.final_equity == dr.final_equity

    # Identical equity value sequence.
    bt_eq = [e.equity for e in bt_sink.equity]
    dr_eq = [e.equity for e in dr_sink.equity]
    assert bt_eq == dr_eq

    # Identical order decisions (symbol/side/qty/fill price/status).
    def sig(events):
        return [
            (o.symbol, o.side, o.qty, o.avg_fill_price, o.status) for o in events.orders
        ]

    assert sig(bt_sink) == sig(dr_sink)


@pytest.mark.asyncio
async def test_determinism_same_mode_twice(bars, instruments):
    a, b = InMemorySink(), InMemorySink()
    ra = await _run(Mode.BACKTEST, bars, instruments, a)
    rb = await _run(Mode.BACKTEST, bars, instruments, b)
    assert ra.final_equity == rb.final_equity
    assert [e.equity for e in a.equity] == [e.equity for e in b.equity]
