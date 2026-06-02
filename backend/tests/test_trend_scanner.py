"""Tests for the trend_scanner strategy and its indicators.

Covers RSI, dynamic coin selection (scanning -> selected), multi-entry laddering,
scaled take-profits (REDUCE intents) and the moving stop.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.domain.brokers.sim import SimBroker
from app.domain.clock import SimulatedClock
from app.domain.engine import Engine
from app.domain.feeds.replay import ReplayFeed
from app.domain.types import Bar, Instrument, IntentAction, Mode, PositionSide
from app.events.bus import InMemorySink
from app.events.schemas import ErrorEvent, TradeLevelEvent, WatchlistEvent
from app.risk.config import RiskParams
from app.risk.sizer import RiskSizer
from app.strategies import create_strategy
from app.strategies.indicators import rsi, sma

_EPOCH = datetime(2024, 1, 1, tzinfo=UTC)


def test_rsi_basic_bounds_and_none():
    assert rsi([Decimal("1")] * 3, 14) is None  # not enough data
    rising = [Decimal(x) for x in range(1, 40)]
    val = rsi(rising, 14)
    assert val is not None and val > Decimal("90")  # only gains -> high RSI
    falling = [Decimal(x) for x in range(40, 1, -1)]
    val2 = rsi(falling, 14)
    assert val2 is not None and val2 < Decimal("10")  # only losses -> low RSI


def test_sma():
    assert sma([Decimal("1"), Decimal("2"), Decimal("3")], 3) == Decimal("2")
    assert sma([Decimal("1")], 3) is None


def _uptrend_then_pullback(symbol: str = "BTCUSDT", n: int = 320) -> list[Bar]:
    """Uptrend with an oscillation so RSI repeatedly pulls back then turns up.

    A dominant linear slope keeps price above the trend EMA (long regime), while a
    sine wave creates multi-bar dips that drive RSI below the pullback threshold.
    """
    bars: list[Bar] = []
    for i in range(n):
        raw = 100.0 + 0.8 * i + 8.0 * math.sin(2 * math.pi * i / 14.0)
        price = Decimal(str(round(raw, 2)))
        bars.append(
            Bar(
                symbol=symbol,
                interval="5m",
                open_time=_EPOCH + timedelta(minutes=5 * i),
                open=price,
                high=price,
                low=price,
                close=price,
                volume=Decimal("100"),
            )
        )
    return bars


def _instruments(symbol: str = "BTCUSDT") -> dict[str, Instrument]:
    return {
        symbol: Instrument(
            symbol=symbol,
            base=symbol.replace("USDT", ""),
            quote="USDT",
            min_trade_volume=Decimal("0.0001"),
            base_precision=4,
            quote_precision=2,
            min_leverage=1,
            max_leverage=50,
            default_leverage=10,
        )
    }


def _multi_instruments(n: int) -> dict[str, Instrument]:
    return {
        f"COIN{i:02d}USDT": Instrument(
            symbol=f"COIN{i:02d}USDT",
            base=f"COIN{i:02d}",
            quote="USDT",
            min_trade_volume=Decimal("0.0001"),
            base_precision=4,
            quote_precision=2,
            min_leverage=1,
            max_leverage=50,
            default_leverage=10,
        )
        for i in range(n)
    }


async def _run(bars, instruments, sink, params=None):
    strategy = create_strategy(
        "trend_scanner",
        params
        or {
            "ema_fast": 5,
            "ema_slow": 10,
            "trend_ema": 20,
            "rsi_period": 7,
            "rsi_pullback_long": "55",
            "max_entries": 3,
            "entry_spacing_pct": "0.3",
        },
    )
    sizer = RiskSizer(
        RiskParams(min_investment_usd=Decimal("5"), max_capital_usd=Decimal("100"), base_leverage=5)
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
    return await engine.run(), strategy


@pytest.mark.asyncio
async def test_scan_diagnostic_logs_emitted():
    bars = _uptrend_then_pullback(n=80)
    instruments = _instruments()
    sink = InMemorySink()
    await _run(bars, instruments, sink)

    scan_logs = [
        e
        for e in sink.events
        if isinstance(e, ErrorEvent) and e.source == "strategy" and e.severity == "info"
    ]
    assert scan_logs, "strategy scan logs expected"
    assert any("scan BTCUSDT" in log.message for log in scan_logs)
    assert any(
        "warming up" in log.message or "no trend" in log.message or "RSI" in log.message
        for log in scan_logs
    )


@pytest.mark.asyncio
async def test_selection_and_orders():
    bars = _uptrend_then_pullback()
    instruments = _instruments()
    sink = InMemorySink()
    summary, _ = await _run(bars, instruments, sink)

    # The strategy should have opened at least one position (orders happened).
    assert summary.orders > 0

    # Watchlist starts incomplete (scanning) and the coin gets selected.
    watchlists = [e for e in sink.events if isinstance(e, WatchlistEvent)]
    assert watchlists, "watchlist events expected"
    assert watchlists[0].target == 5
    assert any("BTCUSDT" in w.symbols for w in watchlists), "coin must become selected"


@pytest.mark.asyncio
async def test_multi_entry_and_scaled_tp():
    bars = _uptrend_then_pullback()
    instruments = _instruments()
    sink = InMemorySink()
    await _run(bars, instruments, sink)

    actions = [(s.action, s.tag) for s in sink.signals]
    # Multiple entries (ladder) to the same coin.
    entries = [a for a in actions if a[0] == IntentAction.OPEN.value]
    assert len(entries) >= 2, f"expected multiple entries, got {entries}"
    # Scaled take-profits: at least one REDUCE (partial close) emitted.
    reduces = [a for a in actions if a[0] == IntentAction.REDUCE.value]
    assert reduces, "expected at least one partial take-profit (REDUCE)"


@pytest.mark.asyncio
async def test_multi_level_trade_levels():
    bars = _uptrend_then_pullback()
    instruments = _instruments()
    sink = InMemorySink()
    await _run(bars, instruments, sink)

    levels = [e for e in sink.events if isinstance(e, TradeLevelEvent)]
    assert levels, "trade level events expected"
    # The scanner exposes several take-profit price lines.
    assert any(len(lv.take_profits) >= 2 for lv in levels)
    assert any(len(lv.stops) >= 1 for lv in levels)


def test_position_levels_uses_leverage():
    strat = create_strategy("trend_scanner", {"tp1_roe_pct": "10", "tp2_roe_pct": "20"})
    lv = strat.position_levels("BTCUSDT", PositionSide.LONG, Decimal("100"), 10)
    assert lv is not None
    # 10% ROE at 10x -> 1% price move -> 101.0 for the first TP.
    assert lv["take_profits"][0] == Decimal("101.0")
    assert lv["stops"], "an initial stop level must be present"


def test_desired_symbols_preserves_volume_rank_order_and_caps_to_max_rank():
    instruments = _multi_instruments(12)
    # Simulate builder volume ordering by inserting symbols in reverse rank order.
    ranked = {sym: instruments[sym] for sym in reversed(list(instruments))}
    strat = create_strategy(
        "trend_scanner",
        {"scan_universe": 3, "max_scan_rank": 7, "max_symbols": 5},
    )
    desired = strat.desired_symbols(ranked)
    assert desired == list(ranked)[:7]


def test_selection_expands_one_volume_batch_at_a_time():
    instruments = _multi_instruments(7)
    strat = create_strategy(
        "trend_scanner",
        {
            "scan_universe": 3,
            "max_scan_rank": 7,
            "max_symbols": 5,
            "ema_fast": 2,
            "ema_slow": 3,
            "trend_ema": 5,
            "rsi_period": 2,
        },
    )
    desired = strat.desired_symbols(instruments)
    assert desired == list(instruments)[:7]

    class Market:
        def __init__(self, ready: int) -> None:
            self.ready = ready

        def symbols(self):
            return list(instruments)[: self.ready]

        def closes(self, symbol: str):
            idx = list(instruments).index(symbol)
            return [Decimal("1")] * 6 if idx < self.ready else []

    class Ctx:
        def __init__(self, ready: int) -> None:
            self.instruments = instruments
            self.market = Market(ready)

    # First 3 ready -> opens next batch, but only one expansion happens.
    snap = strat.selection_snapshot(Ctx(ready=3))
    assert snap["active_limit"] == 6
    assert len(snap["scanning"]) == 3
    # Now first 6 ready -> opens final batch.
    snap = strat.selection_snapshot(Ctx(ready=6))
    assert snap["active_limit"] == 7
