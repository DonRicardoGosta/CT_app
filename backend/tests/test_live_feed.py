from decimal import Decimal

import pytest

from app.domain.feeds.live import LiveFeed
from app.domain.types import Instrument


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

