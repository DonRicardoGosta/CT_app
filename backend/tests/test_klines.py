"""Bitunix kline range fetch (timezone + pagination)."""

import asyncio
from datetime import UTC, datetime

import pytest

from app.exchange.bitunix.rest import BitunixRest


@pytest.mark.asyncio
async def test_klines_naive_datetimes_do_not_crash():
    rest = BitunixRest()
    try:
        start = datetime(2026, 5, 30, 0, 0, 0)  # naive — treated as UTC
        end = datetime(2026, 5, 30, 1, 0, 0)
        bars = await rest.get_klines("BTCUSDT", "1m", start_time=start, end_time=end)
        assert len(bars) > 0
    finally:
        await rest.close()


@pytest.mark.asyncio
async def test_klines_full_hour_more_than_one_page():
    rest = BitunixRest()
    try:
        start = datetime(2026, 5, 30, 0, 0, 0, tzinfo=UTC)
        end = datetime(2026, 5, 30, 23, 59, 59, tzinfo=UTC)
        bars = await rest.get_klines("BTCUSDT", "1m", start_time=start, end_time=end)
        # One day of 1m bars ≈ 1440; must exceed a single 200-candle page.
        assert len(bars) > 400
    finally:
        await rest.close()
