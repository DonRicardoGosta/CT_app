import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.domain.feeds.live import LiveFeed
from app.domain.types import Bar, Instrument, MarketEventType


def _closed_bars(symbol: str, opens: list[float], secs: int) -> list[Bar]:
    """Build closed candles whose open_time is `opens` seconds before now."""
    now = time.time()
    out = []
    for off in opens:
        ot = datetime.fromtimestamp(now - off, tz=UTC)
        out.append(
            Bar(
                symbol=symbol, interval="15m", open_time=ot,
                open=Decimal("1"), high=Decimal("1"), low=Decimal("1"),
                close=Decimal("1"), volume=Decimal("1"),
            )
        )
    return out


def _instrument(symbol: str) -> Instrument:
    return Instrument(
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


@pytest.mark.asyncio
async def test_live_feed_ensures_only_new_symbols():
    instruments = {sym: _instrument(sym) for sym in ["BTCUSDT", "ETHUSDT", "SOLUSDT"]}
    feed = LiveFeed(["BTCUSDT"], instruments, "1m")

    added = await feed.ensure_symbols(["BTCUSDT", "ETHUSDT", "SOLUSDT"])

    assert added == ["ETHUSDT", "SOLUSDT"]
    assert feed._symbols == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]


class _FakeRest:
    """Minimal REST stub returning synthetic klines for warmup tests."""

    def __init__(self, count: int) -> None:
        self.count = count
        self.calls: list[str] = []

    async def get_recent_klines(self, symbol: str, interval: str, count: int):
        self.calls.append(symbol)
        epoch = datetime(2024, 1, 1, tzinfo=UTC)
        return [
            Bar(
                symbol=symbol,
                interval=interval,
                open_time=epoch + timedelta(minutes=i),
                open=Decimal("100"),
                high=Decimal("100"),
                low=Decimal("100"),
                close=Decimal("100"),
                volume=Decimal("1"),
            )
            for i in range(self.count)
        ]


@pytest.mark.asyncio
async def test_warmup_logs_per_coin_fetch_and_result():
    instruments = {sym: _instrument(sym) for sym in ["BTCUSDT", "ETHUSDT"]}
    rest = _FakeRest(count=4)
    logs: list[tuple[str, str]] = []

    async def on_log(severity: str, message: str, context: dict) -> None:
        logs.append((severity, message))

    feed = LiveFeed(
        ["BTCUSDT", "ETHUSDT"], instruments, "1m",
        rest=rest, warmup_bars=4, on_log=on_log,
    )
    await feed._enqueue_warmup(["BTCUSDT", "ETHUSDT"])

    msgs = [m for _s, m in logs]
    # A "fetching" line BEFORE and a "got ... candles" line AFTER for each coin.
    assert any("warmup BTCUSDT: fetching" in m for m in msgs)
    assert any("warmup BTCUSDT: got 4 candles" in m for m in msgs)
    assert any("warmup ETHUSDT: got 4 candles" in m for m in msgs)
    assert any("warmup complete: 2/2" in m for m in msgs)


class _EmptyRest:
    async def get_recent_klines(self, symbol, interval, count):
        return []


@pytest.mark.asyncio
async def test_warmup_logs_warning_when_no_history():
    instruments = {"BTCUSDT": _instrument("BTCUSDT")}
    logs: list[tuple[str, str]] = []

    async def on_log(severity, message, context):
        logs.append((severity, message))

    feed = LiveFeed(
        ["BTCUSDT"], instruments, "1m", rest=_EmptyRest(), warmup_bars=4, on_log=on_log
    )
    await feed._enqueue_warmup(["BTCUSDT"])

    assert any(s == "warn" and "no history returned" in m for s, m in logs)
    assert any("warmup complete: 0/1" in m for _s, m in logs)


@pytest.mark.asyncio
async def test_warmup_enqueues_history_as_warmup_bars():
    instruments = {"BTCUSDT": _instrument("BTCUSDT")}
    rest = _FakeRest(count=5)
    feed = LiveFeed(["BTCUSDT"], instruments, "1m", rest=rest, warmup_bars=5)

    await feed._enqueue_warmup(["BTCUSDT"])

    # All but the last bar are queued as warmup BAR events.
    events = []
    while not feed._queue.empty():
        events.append(feed._queue.get_nowait())
    assert len(events) == 4
    assert all(e.warmup and e.type is MarketEventType.BAR for e in events)
    # The last historical bar is held as the in-progress candle.
    assert feed._in_progress["BTCUSDT"].open_time == events[-1].bar.open_time + timedelta(minutes=1)
    assert rest.calls == ["BTCUSDT"]


@pytest.mark.asyncio
async def test_poll_emits_only_newly_closed_bars_in_order():
    instruments = {"BTCUSDT": _instrument("BTCUSDT")}
    feed = LiveFeed(["BTCUSDT"], instruments, "15m")
    secs = 900
    # Three closed candles (well in the past) + one not-yet-closed (too recent).
    bars = _closed_bars("BTCUSDT", [3000, 2000, 1000, 100], secs)
    not_closed = bars[-1]  # opened 100s ago -> 15m not elapsed

    n = await feed._emit_new_closed("BTCUSDT", bars, secs)
    assert n == 3  # the 3 fully-closed ones, not the in-progress candle
    emitted = [feed._queue.get_nowait() for _ in range(3)]
    # Delivered in chronological order, as non-warmup BAR events.
    assert all(e.type is MarketEventType.BAR and not e.warmup for e in emitted)
    assert [e.bar.open_time for e in emitted] == [b.open_time for b in bars[:3]]
    # Re-polling the same data emits nothing new (dedup via _last_emitted).
    assert await feed._emit_new_closed("BTCUSDT", bars, secs) == 0
    assert feed._queue.empty()
    assert not_closed.open_time > feed._last_emitted["BTCUSDT"]


@pytest.mark.asyncio
async def test_ws_does_not_re_emit_a_bar_the_poll_delivered():
    instruments = {"BTCUSDT": _instrument("BTCUSDT")}
    feed = LiveFeed(["BTCUSDT"], instruments, "15m")
    secs = 900
    bars = _closed_bars("BTCUSDT", [2000, 1000], secs)
    await feed._emit_new_closed("BTCUSDT", bars, secs)
    while not feed._queue.empty():
        feed._queue.get_nowait()
    last = feed._last_emitted["BTCUSDT"]

    # A WS push for the next (in-progress) candle: prev == the bar the poll
    # already emitted, so the WS path must NOT re-emit it (returns a TICK/None).
    newer_open = int(last.timestamp() + secs) * 1000
    msg = {
        "ch": "market_kline_15min",
        "symbol": "BTCUSDT",
        "data": {"o": "1", "h": "1", "l": "1", "c": "1", "b": "0", "t": newer_open},
    }
    event = feed._to_event(msg)
    assert event is None or event.type is MarketEventType.TICK


@pytest.mark.asyncio
async def test_ensure_symbols_warms_up_new_batch():
    instruments = {sym: _instrument(sym) for sym in ["BTCUSDT", "ETHUSDT"]}
    rest = _FakeRest(count=3)
    feed = LiveFeed(["BTCUSDT"], instruments, "1m", rest=rest, warmup_bars=3)

    added = await feed.ensure_symbols(["ETHUSDT"])

    assert added == ["ETHUSDT"]
    assert rest.calls == ["ETHUSDT"]
    assert not feed._queue.empty()

