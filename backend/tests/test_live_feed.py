from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.domain.feeds.live import LiveFeed
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
async def test_ensure_symbols_warms_up_new_batch():
    instruments = {sym: _instrument(sym) for sym in ["BTCUSDT", "ETHUSDT"]}
    rest = _FakeRest(count=3)
    feed = LiveFeed(["BTCUSDT"], instruments, "1m", rest=rest, warmup_bars=3)

    added = await feed.ensure_symbols(["ETHUSDT"])

    assert added == ["ETHUSDT"]
    assert rest.calls == ["ETHUSDT"]
    assert not feed._queue.empty()

