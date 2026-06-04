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
from app.domain.engine import Engine, EngineSummary
from app.domain.feeds.replay import ReplayFeed
from app.domain.types import (
    AccountState,
    Bar,
    Instrument,
    IntentAction,
    Mode,
    Position,
    PositionSide,
)
from app.events.bus import InMemorySink
from app.events.schemas import ErrorEvent, TradeLevelEvent, WatchlistEvent
from app.risk.config import RiskParams
from app.risk.sizer import RiskSizer
from app.strategies import create_strategy
from app.strategies.indicators import rsi, sma

_EPOCH = datetime(2024, 1, 1, tzinfo=UTC)


def _empty_account() -> AccountState:
    return AccountState(ts=_EPOCH, balance=Decimal("1000"), positions={})


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
        "not enough history" in log.message
        or "no trend" in log.message
        or "RSI" in log.message
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


def test_position_levels_are_price_based():
    strat = create_strategy("trend_scanner", {"tp1_pct": "1", "tp2_pct": "2"})
    lv = strat.position_levels("BTCUSDT", PositionSide.LONG, Decimal("100"), 10)
    assert lv is not None
    # TP1 is a 1% price move regardless of leverage -> 101.0.
    assert lv["take_profits"][0] == Decimal("101.0")
    assert lv["take_profits"][1] == Decimal("102.0")
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


def test_failed_first_entry_does_not_consume_selection_slot():
    strat = create_strategy("trend_scanner", {"max_symbols": 5})
    k = ("BTCUSDT", "long")
    strat._stop[k] = Decimal("98")
    strat._last_entry[k] = Decimal("100")
    strat.on_open_outcome(
        "BTCUSDT", PositionSide.LONG, success=False, first_entry=True
    )
    assert "BTCUSDT" not in strat._selected
    assert k not in strat._stop
    assert k not in strat._last_entry


def test_release_symbol_frees_watchlist_slot(btc_instrument):
    strat = create_strategy("trend_scanner", {"max_symbols": 5})
    strat._ensure_selected("BTCUSDT")
    strat._ensure_selected("ETHUSDT")
    empty = _empty_account()
    assert strat.release_symbol("BTCUSDT", empty) is True
    assert "BTCUSDT" not in strat._selected
    assert len(strat._selected) == 1
    assert strat._slot_free(empty) is True


def test_release_symbol_keeps_coin_when_position_open():
    strat = create_strategy("trend_scanner", {"max_symbols": 5})
    strat._ensure_selected("BTCUSDT")
    acct = AccountState(
        ts=_EPOCH,
        balance=Decimal("1000"),
        positions={
            ("BTCUSDT", PositionSide.LONG): Position(
                symbol="BTCUSDT",
                position_side=PositionSide.LONG,
                qty=Decimal("1"),
                entry_price=Decimal("100"),
                leverage=5,
            )
        },
    )
    assert strat.release_symbol("BTCUSDT", acct) is False
    assert "BTCUSDT" in strat._selected


def test_next_scan_candidate_skips_selected():
    instruments = _multi_instruments(5)
    strat = create_strategy("trend_scanner", {"max_scan_rank": 5, "max_symbols": 5})
    strat.desired_symbols(instruments)
    strat._ensure_selected(list(instruments)[0])
    empty = _empty_account()
    nxt = strat.next_scan_candidate(empty)
    assert nxt is not None
    assert nxt not in strat._selected


def test_is_full_uses_open_positions_not_selected():
    strat = create_strategy("trend_scanner", {"max_symbols": 2})
    empty = _empty_account()
    assert strat.is_full(empty) is False
    acct = AccountState(
        ts=_EPOCH,
        balance=Decimal("1000"),
        positions={
            ("BTCUSDT", PositionSide.LONG): Position(
                symbol="BTCUSDT",
                position_side=PositionSide.LONG,
                qty=Decimal("1"),
                entry_price=Decimal("100"),
                leverage=5,
            ),
            ("ETHUSDT", PositionSide.LONG): Position(
                symbol="ETHUSDT",
                position_side=PositionSide.LONG,
                qty=Decimal("1"),
                entry_price=Decimal("100"),
                leverage=5,
            ),
        },
    )
    assert strat._selected == []
    assert strat.is_full(acct) is True


def test_first_entry_blocked_when_symbol_has_open_position():
    strat = create_strategy("trend_scanner", {"max_symbols": 5})
    acct = AccountState(
        ts=_EPOCH,
        balance=Decimal("1000"),
        positions={
            ("BTCUSDT", PositionSide.LONG): Position(
                symbol="BTCUSDT",
                position_side=PositionSide.LONG,
                qty=Decimal("1"),
                entry_price=Decimal("100"),
                leverage=5,
                step_count=1,
            ),
        },
    )
    intent = strat._entry_intent(
        "BTCUSDT",
        PositionSide.LONG,
        None,
        Decimal("101"),
        Decimal("50"),
        Decimal("48"),
        acct,
    )
    assert intent is None


def test_dca_entry_allowed_when_position_exists():
    strat = create_strategy("trend_scanner", {"max_symbols": 5})
    pos = Position(
        symbol="BTCUSDT",
        position_side=PositionSide.LONG,
        qty=Decimal("1"),
        entry_price=Decimal("100"),
        leverage=5,
        step_count=1,
    )
    acct = AccountState(
        ts=_EPOCH,
        balance=Decimal("1000"),
        positions={("BTCUSDT", PositionSide.LONG): pos},
    )
    strat._last_entry[("BTCUSDT", "long")] = Decimal("100")
    intent = strat._entry_intent(
        "BTCUSDT",
        PositionSide.LONG,
        pos,
        Decimal("98"),
        Decimal("50"),
        Decimal("48"),
        acct,
    )
    assert intent is not None
    assert intent.tag == "entry_2"


def test_successful_first_entry_adds_to_selection():
    strat = create_strategy("trend_scanner", {"max_symbols": 5})
    strat.on_open_outcome(
        "BTCUSDT", PositionSide.LONG, success=True, first_entry=True
    )
    assert strat._selected == ["BTCUSDT"]


def test_selection_snapshot_lists_remaining_universe_as_scanning():
    instruments = _multi_instruments(7)
    strat = create_strategy(
        "trend_scanner", {"max_scan_rank": 7, "max_symbols": 5}
    )
    strat.desired_symbols(instruments)

    class Ctx:
        def __init__(self) -> None:
            self.instruments = instruments

    snap = strat.selection_snapshot(Ctx())
    assert snap["target"] == 5
    assert snap["selected"] == []
    # Nothing selected yet -> the whole ranked universe is "scanning".
    assert len(snap["scanning"]) == 7

    strat._ensure_selected("COIN00USDT")
    snap = strat.selection_snapshot(Ctx())
    assert snap["selected"] == ["COIN00USDT"]
    assert "COIN00USDT" not in snap["scanning"]
    assert len(snap["scanning"]) == 6


class _FakeHistory:
    """History provider returning a canned bar series per symbol."""

    def __init__(self, series: dict[str, list[Bar]]) -> None:
        self.series = series
        self.calls: list[str] = []

    async def get_recent_klines(self, symbol: str, interval: str, count: int):
        self.calls.append(symbol)
        return self.series.get(symbol, [])


class _ShortThenFullHistory:
    """Returns too few bars until the requested ``count`` reaches a threshold."""

    def __init__(self, short: int, full: int, threshold: int) -> None:
        self.short = short
        self.full = full
        self.threshold = threshold
        self.requested: list[int] = []

    async def get_recent_klines(self, symbol: str, interval: str, count: int):
        self.requested.append(count)
        n = self.full if count >= self.threshold else self.short
        return _flat_bars(symbol, n)


def _make_engine(strategy, history):
    from app.domain.clock import RealClock
    from app.domain.feeds.live import LiveFeed

    instruments = _multi_instruments(1)
    clock = RealClock()
    sizer = RiskSizer(RiskParams(base_leverage=5))
    broker = SimBroker(clock, instruments, Decimal("1000"), fee_rate=Decimal("0.0006"))
    feed = LiveFeed([], instruments, "1m")
    return Engine(
        mode=Mode.DRY_RUN,
        strategy=strategy,
        sizer=sizer,
        broker=broker,
        feed=feed,
        clock=clock,
        sink=InMemorySink(),
        history=history,
        interval="1m",
    )


@pytest.mark.asyncio
async def test_fetch_history_refetches_when_short():
    strat = create_strategy("trend_scanner", {})
    history = _ShortThenFullHistory(short=199, full=211, threshold=300)
    engine = _make_engine(strat, history)

    bars = await engine._fetch_history("BTCUSDT", 211)

    # First request (211) returned only 199, so the engine retried with a larger
    # count and got the full series.
    assert len(bars) == 211
    assert history.requested[0] == 211
    assert history.requested[-1] >= 300


def _bars_from_prices(symbol: str, prices: list[float]) -> list[Bar]:
    out = []
    for i, price in enumerate(prices):
        p = Decimal(str(round(price, 4)))
        out.append(
            Bar(
                symbol=symbol,
                interval="1m",
                open_time=_EPOCH + timedelta(minutes=i),
                open=p,
                high=p,
                low=p,
                close=p,
                volume=Decimal("1"),
            )
        )
    return out


def _flat_bars(symbol: str, n: int, price: float = 100.0) -> list[Bar]:
    return _bars_from_prices(symbol, [price] * n)


def _long_entry_series(symbol: str, n: int = 120) -> list[Bar]:
    """Uptrend that ends with a one-bar dip then a recovery (a long entry now)."""
    prices = [10.0 + i for i in range(n - 2)]
    prices.append(prices[-1] - 5.0)  # pullback dip -> RSI drops below threshold
    prices.append(prices[-1] + 6.0)  # recovery -> RSI turns up = entry trigger
    return _bars_from_prices(symbol, prices)


@pytest.mark.asyncio
async def test_prescan_evaluates_coins_one_by_one_until_target():
    from app.domain.brokers.sim import SimBroker
    from app.domain.clock import RealClock
    from app.domain.feeds.live import LiveFeed

    symbols = [f"COIN{i:02d}USDT" for i in range(6)]
    instruments = {s: _multi_instruments(6)[s] for s in symbols}

    # Two coins have an uptrend ending in a pullback+recovery (entry), the rest
    # are flat (no trend) so the scan keeps moving.
    series: dict[str, list[Bar]] = {}
    for s in symbols:
        series[s] = _flat_bars(s, 120)
    series["COIN01USDT"] = _long_entry_series("COIN01USDT", n=120)
    series["COIN03USDT"] = _long_entry_series("COIN03USDT", n=120)
    history = _FakeHistory(series)

    strat = create_strategy(
        "trend_scanner",
        {
            "max_scan_rank": 6,
            "max_symbols": 2,
            "ema_fast": 5,
            "ema_slow": 10,
            "trend_ema": 20,
            "rsi_period": 7,
            "rsi_pullback_long": "60",
        },
    )
    sizer = RiskSizer(
        RiskParams(
            min_investment_usd=Decimal("5"),
            max_capital_usd=Decimal("100"),
            base_leverage=5,
        )
    )
    clock = RealClock()
    broker = SimBroker(clock, instruments, Decimal("1000"), fee_rate=Decimal("0.0006"))
    feed = LiveFeed([], instruments, "1m")
    sink = InMemorySink()
    engine = Engine(
        mode=Mode.DRY_RUN,
        strategy=strat,
        sizer=sizer,
        broker=broker,
        feed=feed,
        clock=clock,
        sink=sink,
        history=history,
        interval="1m",
    )

    summary = EngineSummary(run_id="t", mode="dry_run", strategy="trend_scanner")
    await engine._scan_and_select(instruments, summary)

    # Coins were fetched one by one, in ranked order.
    assert history.calls[0] == "COIN00USDT"
    # We stop once the target (2) open positions is reached; we should not scan all 6.
    acct = await broker.account()
    open_syms = strat._symbols_with_open_position(acct)
    assert len(open_syms) == 2
    assert strat.is_full(acct)
    assert len(history.calls) < 6

    # Each scanned coin produced a strategy log; no "waiting for candle" noise.
    msgs = [
        e.message
        for e in sink.events
        if isinstance(e, ErrorEvent) and e.source == "strategy"
    ]
    assert any("scan COIN00USDT" in m for m in msgs)
    assert all("waiting for first closed" not in m for m in msgs)
    assert any("no trend" in m for m in msgs)
