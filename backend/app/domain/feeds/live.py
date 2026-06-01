"""Live market data feed backed by the Bitunix WebSocket (REQ-002/003).

Strategies act on *closed* bars, so this feed emits a BAR event when a candle
rolls over (a kline push arrives with a newer open time than the one in progress)
and a TICK event for every push in between (used for live mark-to-market). This
keeps the live decision stream identical in shape to the backtest stream.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime

from app.domain.interfaces import MarketDataFeed
from app.domain.types import Bar, Instrument, MarketEvent, MarketEventType, Tick
from app.exchange.bitunix.models import parse_kline
from app.exchange.bitunix.ws import PUBLIC_URL, BitunixWS

_INTERVAL_CHANNEL = {
    "1m": "market_kline_1min",
    "5m": "market_kline_5min",
    "15m": "market_kline_15min",
    "1h": "market_kline_60min",
}


class LiveFeed(MarketDataFeed):
    """Streams live market events from Bitunix."""

    def __init__(
        self,
        symbols: list[str],
        instruments: dict[str, Instrument],
        interval: str = "1m",
    ) -> None:
        self._symbols = symbols
        self._instruments = instruments
        self._interval = interval
        self._ws = BitunixWS(PUBLIC_URL)
        channel = _INTERVAL_CHANNEL.get(interval, "market_kline_1min")
        for sym in symbols:
            self._ws.add_subscription(channel, sym)
        self._in_progress: dict[str, Bar] = {}

    async def instruments(self) -> dict[str, Instrument]:
        return self._instruments

    async def stream(self) -> AsyncIterator[MarketEvent]:
        await self._ws.start()
        async for msg in self._ws.messages():
            event = self._to_event(msg)
            if event is not None:
                yield event

    def _to_event(self, msg: dict) -> MarketEvent | None:
        symbol = msg.get("symbol")
        data = msg.get("data")
        ch = msg.get("ch", "")
        if not symbol or data is None or "kline" not in ch:
            return None
        item = data[0] if isinstance(data, list) and data else data
        if not isinstance(item, dict):
            return None
        bar = parse_kline(symbol, self._interval, item)

        prev = self._in_progress.get(symbol)
        if prev is not None and bar.open_time > prev.open_time:
            # The previous candle just closed -> emit it as a BAR event.
            self._in_progress[symbol] = bar
            return MarketEvent(
                type=MarketEventType.BAR,
                ts=prev.open_time,
                symbol=symbol,
                bar=prev,
            )
        self._in_progress[symbol] = bar
        # Still within the current candle: emit a TICK for live mark-to-market.
        return MarketEvent(
            type=MarketEventType.TICK,
            ts=datetime.now(UTC),
            symbol=symbol,
            tick=Tick(symbol=symbol, price=bar.close, ts=datetime.now(UTC)),
        )
