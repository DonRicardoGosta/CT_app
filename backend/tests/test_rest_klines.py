"""Recent-kline fetching widens its window until it has enough bars (illiquid coins)."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.domain.types import Bar
from app.exchange.bitunix.rest import BitunixRest

_EPOCH = datetime(2024, 1, 1, tzinfo=UTC)


def _make_bars(n: int, *, end: datetime, step_s: int = 60, gap: int = 1) -> list[Bar]:
    """Build ``n`` bars ending at ``end``; ``gap`` simulates missing minutes."""
    bars: list[Bar] = []
    for i in range(n):
        ts = end - timedelta(seconds=step_s * gap * (n - 1 - i))
        bars.append(
            Bar(
                symbol="EIGENUSDT",
                interval="1m",
                open_time=ts,
                open=Decimal("1"),
                high=Decimal("1"),
                low=Decimal("1"),
                close=Decimal("1"),
                volume=Decimal("1"),
            )
        )
    return bars


@pytest.mark.asyncio
async def test_get_recent_klines_widens_window_until_enough(monkeypatch):
    rest = BitunixRest(api_key="k", secret_key="s")
    # The coin only has ~205 sparse 1m candles (gap=3 -> a candle every 3 minutes),
    # spread over a wide time range. A tight window returns far fewer than 211.
    pool = _make_bars(205, end=datetime.now(UTC), step_s=60, gap=3)

    calls: list[float] = []

    async def fake_range(symbol, interval, start_time, end_time):
        calls.append(start_time.timestamp())
        # Return only bars at/after the requested window start.
        return [b for b in pool if b.open_time >= start_time]

    monkeypatch.setattr(rest, "_get_klines_range", fake_range)

    bars = await rest.get_recent_klines("EIGENUSDT", "1m", 211)
    await rest.close()

    # Even though the first tight window is short, widening collects all 205 bars
    # the coin actually has (>= the strategy's 201-bar requirement).
    assert len(bars) == 205
    assert len(calls) >= 2  # at least one widening retry happened


@pytest.mark.asyncio
async def test_get_recent_klines_stops_when_no_more_history(monkeypatch):
    rest = BitunixRest(api_key="k", secret_key="s")
    # Brand-new listing: only 50 candles exist no matter how wide the window.
    pool = _make_bars(50, end=datetime.now(UTC), step_s=60, gap=1)

    async def fake_range(symbol, interval, start_time, end_time):
        return [b for b in pool if b.open_time >= start_time]

    monkeypatch.setattr(rest, "_get_klines_range", fake_range)

    bars = await rest.get_recent_klines("EIGENUSDT", "1m", 211)
    await rest.close()

    # Returns what exists without looping forever.
    assert len(bars) == 50
