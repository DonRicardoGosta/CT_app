import time
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.domain.feeds.live import LiveFeed
from app.domain.market import MarketState
from app.domain.types import Bar, Instrument, MarketEventType


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


def _bar(symbol: str, open_off_s: float, close: float, secs: int = 900) -> Bar:
    """A bar whose open_time is ``open_off_s`` seconds before now."""
    ot = datetime.fromtimestamp(time.time() - open_off_s, tz=UTC)
    p = Decimal(str(close))
    return Bar(
        symbol=symbol, interval="15m", open_time=ot,
        open=p, high=p, low=p, close=p, volume=Decimal("1"),
    )


class _FakeRest:
    """Returns a fixed list of bars (closed + one in-progress) for any request."""

    def __init__(self, bars: list[Bar]) -> None:
        self._bars = bars
        self.calls: list[tuple[str, int]] = []

    async def get_recent_klines(self, symbol: str, interval: str, count: int):
        self.calls.append((symbol, count))
        return list(self._bars)


class _RaisingRest:
    async def get_recent_klines(self, symbol: str, interval: str, count: int):
        raise RuntimeError("boom")


def _capture():
    logs: list[tuple[str, str]] = []

    async def on_log(severity: str, message: str, context: dict) -> None:
        logs.append((severity, message))

    return logs, on_log


@pytest.mark.asyncio
async def test_poll_fetches_window_and_emits_latest_closed():
    secs = 900
    # Three fully-closed candles + one in-progress (too recent to be closed).
    bars = [
        _bar("BTCUSDT", 3 * secs + 100, 10.0),
        _bar("BTCUSDT", 2 * secs + 100, 11.0),
        _bar("BTCUSDT", 1 * secs + 100, 12.0),
        _bar("BTCUSDT", 100, 13.0),  # in-progress
    ]
    rest = _FakeRest(bars)
    logs, on_log = _capture()
    feed = LiveFeed(
        ["BTCUSDT"], {"BTCUSDT": _instrument("BTCUSDT")}, "15m",
        rest=rest, window_bars=3, on_log=on_log,
    )

    await feed._fetch_and_emit("BTCUSDT", secs)

    # Fetched window_bars + 1 to cover the in-progress candle.
    assert rest.calls == [("BTCUSDT", 4)]
    # Exactly one evaluation event, carrying the closed window and latest close.
    assert feed._queue.qsize() == 1
    event = feed._queue.get_nowait()
    assert event.type is MarketEventType.BAR
    assert event.bar.close == Decimal("12.0")  # newest CLOSED candle, not 13.0
    assert [b.close for b in event.window] == [
        Decimal("10.0"), Decimal("11.0"), Decimal("12.0")
    ]
    # Every step is logged.
    msgs = [m for _s, m in logs]
    assert any("poll BTCUSDT: fetching last 3 15m klines" in m for m in msgs)
    assert any("got 3 closed klines" in m for m in msgs)
    assert any("-> evaluating" in m for m in msgs)


@pytest.mark.asyncio
async def test_poll_skips_when_no_new_closed_candle():
    secs = 900
    bars = [
        _bar("BTCUSDT", 2 * secs + 100, 11.0),
        _bar("BTCUSDT", 1 * secs + 100, 12.0),
        _bar("BTCUSDT", 100, 13.0),  # in-progress
    ]
    rest = _FakeRest(bars)
    logs, on_log = _capture()
    feed = LiveFeed(
        ["BTCUSDT"], {"BTCUSDT": _instrument("BTCUSDT")}, "15m",
        rest=rest, window_bars=3, on_log=on_log,
    )

    await feed._fetch_and_emit("BTCUSDT", secs)
    assert feed._queue.qsize() == 1
    feed._queue.get_nowait()

    # Polling again with the same data emits nothing (dedup) and says so.
    await feed._fetch_and_emit("BTCUSDT", secs)
    assert feed._queue.empty()
    assert any("no new closed candle" in m for _s, m in logs)


@pytest.mark.asyncio
async def test_poll_skips_when_no_closed_candle_yet():
    secs = 900
    # Only an in-progress candle -> nothing closed yet.
    rest = _FakeRest([_bar("BTCUSDT", 100, 13.0)])
    logs, on_log = _capture()
    feed = LiveFeed(
        ["BTCUSDT"], {"BTCUSDT": _instrument("BTCUSDT")}, "15m",
        rest=rest, window_bars=3, on_log=on_log,
    )

    await feed._fetch_and_emit("BTCUSDT", secs)

    assert feed._queue.empty()
    assert any(s == "warn" and "no closed candle yet" in m for s, m in logs)


@pytest.mark.asyncio
async def test_poll_fetch_failure_logs_and_continues():
    logs, on_log = _capture()
    feed = LiveFeed(
        ["BTCUSDT"], {"BTCUSDT": _instrument("BTCUSDT")}, "15m",
        rest=_RaisingRest(), window_bars=3, on_log=on_log,
    )

    await feed._fetch_and_emit("BTCUSDT", 900)

    assert feed._queue.empty()
    assert any(s == "warn" and "history fetch failed" in m for s, m in logs)


@pytest.mark.asyncio
async def test_ensure_symbols_registers_only_new():
    instruments = {sym: _instrument(sym) for sym in ["BTCUSDT", "ETHUSDT", "SOLUSDT"]}
    feed = LiveFeed(["BTCUSDT"], instruments, "15m", rest=_RaisingRest(), window_bars=3)

    added = await feed.ensure_symbols(["BTCUSDT", "ETHUSDT", "SOLUSDT"])

    assert added == ["ETHUSDT", "SOLUSDT"]
    assert feed._symbols == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]


@pytest.mark.asyncio
async def test_stream_requires_rest():
    feed = LiveFeed(["BTCUSDT"], {"BTCUSDT": _instrument("BTCUSDT")}, "15m")
    with pytest.raises(RuntimeError):
        async for _ in feed.stream():
            break


def test_market_state_set_bars_replaces_history():
    state = MarketState(max_history=1000)
    state.update_bar(_bar("BTCUSDT", 5000, 1.0))
    # set_bars replaces the rolling history with exactly the supplied window.
    window = [
        _bar("BTCUSDT", 3000, 10.0),
        _bar("BTCUSDT", 2000, 11.0),
        _bar("BTCUSDT", 1000, 12.0),
    ]
    state.set_bars("BTCUSDT", window)
    assert state.closes("BTCUSDT") == [Decimal("10.0"), Decimal("11.0"), Decimal("12.0")]
    assert state.last_price("BTCUSDT") == Decimal("12.0")
