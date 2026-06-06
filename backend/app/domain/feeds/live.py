"""Live market data feed backed by the Bitunix WebSocket (REQ-002/003).

Strategies act on *closed* bars, so this feed emits a BAR event when a candle
rolls over (a kline push arrives with a newer open time than the one in progress)
and a TICK event for every push in between (used for live mark-to-market). This
keeps the live decision stream identical in shape to the backtest stream.

Before live data flows, the feed preloads recent historical bars per symbol (when
a REST client is supplied) and emits them as ``warmup`` BAR events. This lets a
strategy that needs a long history (e.g. a 200-EMA trend filter) start evaluating
immediately instead of idling for hours while candles accumulate one by one.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime

from app.core.logging import get_logger
from app.domain.interfaces import MarketDataFeed
from app.domain.types import Bar, Instrument, MarketEvent, MarketEventType, Tick
from app.exchange.bitunix.models import parse_kline
from app.exchange.bitunix.rest import BitunixRest
from app.exchange.bitunix.ws import PUBLIC_URL, BitunixWS

log = get_logger(__name__)

_INTERVAL_CHANNEL = {
    "1m": "market_kline_1min",
    "5m": "market_kline_5min",
    "15m": "market_kline_15min",
    "1h": "market_kline_60min",
}

_INTERVAL_SECONDS = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
}


class LiveFeed(MarketDataFeed):
    """Streams live market events from Bitunix."""

    def __init__(
        self,
        symbols: list[str],
        instruments: dict[str, Instrument],
        interval: str = "1m",
        *,
        rest: BitunixRest | None = None,
        warmup_bars: int = 0,
        on_log: Callable[[str, str, dict], Awaitable[None]] | None = None,
        poll_buffer_s: float = 4.0,
    ) -> None:
        self._symbols: list[str] = []
        self._symbol_set: set[str] = set()
        self._instruments = instruments
        self._interval = interval
        self._rest = rest
        self._warmup_bars = warmup_bars
        # Optional async sink for user-visible logs (warmup progress per coin).
        self._on_log = on_log
        # Seconds to wait past a candle boundary before REST-polling its close.
        self._poll_buffer_s = poll_buffer_s
        self._ws = BitunixWS(PUBLIC_URL)
        for sym in symbols:
            self._add_initial_symbol(sym)
        self._in_progress: dict[str, Bar] = {}
        # Open time of the most recent CLOSED bar already delivered per symbol.
        # Shared by the WS rollover path and the REST poll backstop so neither
        # emits a bar the other already did (or one already in warmup history).
        self._last_emitted: dict[str, datetime] = {}
        self._queue: asyncio.Queue[MarketEvent] = asyncio.Queue()
        self._pump_task: asyncio.Task | None = None
        self._poll_task: asyncio.Task | None = None

    @property
    def _channel(self) -> str:
        return _INTERVAL_CHANNEL.get(self._interval, "market_kline_1min")

    def _add_initial_symbol(self, symbol: str) -> None:
        if symbol in self._symbol_set:
            return
        self._symbol_set.add(symbol)
        self._symbols.append(symbol)
        self._ws.add_subscription(self._channel, symbol)

    async def instruments(self) -> dict[str, Instrument]:
        return self._instruments

    async def ensure_symbols(self, symbols: list[str]) -> list[str]:
        """Subscribe to newly opened scan-batch symbols and preload their history."""
        added: list[str] = []
        for symbol in symbols:
            if symbol in self._symbol_set:
                continue
            self._symbol_set.add(symbol)
            self._symbols.append(symbol)
            if await self._ws.subscribe(self._channel, symbol):
                added.append(symbol)
        if added:
            await self._enqueue_warmup(added)
        return added

    async def stream(self) -> AsyncIterator[MarketEvent]:
        # Preload history for the initial batch before any live data flows.
        await self._enqueue_warmup(list(self._symbols))
        await self._ws.start()
        self._pump_task = asyncio.create_task(self._pump_ws())
        # Scheduled REST poll: guarantees a closed-bar event every interval even
        # if the WebSocket is degraded, so the strategy is always evaluated on
        # schedule (the "cron" trigger). The WS still provides intrabar ticks.
        if self._rest is not None:
            self._poll_task = asyncio.create_task(self._poll_closed_bars())
        try:
            while True:
                yield await self._queue.get()
        finally:
            for task in (self._pump_task, self._poll_task):
                if task is not None:
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task

    async def _pump_ws(self) -> None:
        async for msg in self._ws.messages():
            event = self._to_event(msg)
            if event is not None:
                await self._queue.put(event)

    async def _log(self, severity: str, message: str, **context) -> None:
        """Emit a user-visible log (if a sink was provided) plus structlog."""
        if self._on_log is not None:
            with contextlib.suppress(Exception):
                await self._on_log(severity, message, context)
        getattr(log, "warning" if severity in ("warn", "error") else "info")(
            message.replace(" ", "_")[:48], **context
        )

    async def _enqueue_warmup(self, symbols: list[str]) -> None:
        """Fetch recent closed bars per symbol and queue them as warmup events.

        Each symbol is fetched one by one with a visible log BEFORE and AFTER the
        request, so a slow or empty coin is obvious (the "fetching..." line shows
        without a matching "got..." line) instead of the run silently stalling.
        """
        if self._rest is None or self._warmup_bars <= 0:
            return
        total = len(symbols)
        await self._log(
            "info",
            f"warmup: fetching history for {total} coin(s), "
            f"~{self._warmup_bars} {self._interval} candles each",
            count=total,
            warmup_bars=self._warmup_bars,
            interval=self._interval,
        )
        ready = 0
        t0 = time.monotonic()
        for idx, symbol in enumerate(symbols, start=1):
            await self._log(
                "info",
                f"warmup {symbol}: fetching history ({idx}/{total})",
                symbol=symbol,
            )
            started = time.monotonic()
            try:
                bars = await self._rest.get_recent_klines(
                    symbol, self._interval, self._warmup_bars
                )
            except Exception as exc:  # noqa: BLE001 - skip symbols we can't warm
                await self._log(
                    "warn",
                    f"warmup {symbol}: history fetch FAILED: {exc}",
                    symbol=symbol,
                    error=str(exc),
                )
                continue
            dt = time.monotonic() - started
            if not bars:
                await self._log(
                    "warn",
                    f"warmup {symbol}: no history returned (skipping for now)",
                    symbol=symbol,
                    seconds=round(dt, 2),
                )
                continue
            # All but the last become history; the last is the in-progress candle
            # so the first live rollover emits it as the first real closed bar.
            for bar in bars[:-1]:
                await self._queue.put(
                    MarketEvent(
                        type=MarketEventType.BAR,
                        ts=bar.open_time,
                        symbol=symbol,
                        bar=bar,
                        warmup=True,
                    )
                )
            self._in_progress[symbol] = bars[-1]
            # The last warmup bar is the newest closed bar the engine has seen;
            # the poll/WS backstop only emits bars strictly newer than this.
            if len(bars) >= 2:
                self._last_emitted[symbol] = bars[-2].open_time
            ready += 1
            await self._log(
                "info",
                f"warmup {symbol}: got {len(bars)} candles in {dt:.1f}s "
                f"({ready}/{total} ready)",
                symbol=symbol,
                candles=len(bars),
                seconds=round(dt, 2),
            )
        await self._log(
            "info",
            f"warmup complete: {ready}/{total} coin(s) ready in "
            f"{time.monotonic() - t0:.1f}s; waiting for the next closed "
            f"{self._interval} candle to evaluate",
            ready=ready,
            total=total,
        )

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
            # The previous candle just closed -> emit it as a BAR event, unless
            # the REST poll backstop already delivered it (shared dedup).
            self._in_progress[symbol] = bar
            last = self._last_emitted.get(symbol)
            if last is not None and prev.open_time <= last:
                return None
            self._last_emitted[symbol] = prev.open_time
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

    async def _poll_closed_bars(self) -> None:
        """Scheduled REST backstop: emit each newly closed candle per symbol.

        Wakes a few seconds after every interval boundary, fetches the latest
        closed candles via REST and enqueues any that are newer than what has
        already been delivered (gap-filling, in order). This makes the strategy's
        per-candle evaluation reliable even when the WebSocket is degraded — the
        decision cadence becomes a timer rather than a push dependency.
        """
        if self._rest is None:
            return
        secs = _INTERVAL_SECONDS.get(self._interval, 60)
        announced = False
        while True:
            now = time.time()
            next_boundary = (int(now // secs) + 1) * secs
            await asyncio.sleep(max(0.0, next_boundary - now) + self._poll_buffer_s)
            if not announced:
                announced = True
                await self._log(
                    "info",
                    f"scheduled {self._interval} poll active: will check each coin "
                    "on every candle close (WS-independent)",
                    interval=self._interval,
                )
            for symbol in list(self._symbols):
                try:
                    bars = await self._rest.get_recent_klines(symbol, self._interval, 5)
                except Exception as exc:  # noqa: BLE001 - one bad coin must not stop others
                    await self._log(
                        "warn",
                        f"poll {symbol}: history fetch failed: {exc}",
                        symbol=symbol,
                    )
                    continue
                await self._emit_new_closed(symbol, bars, secs)

    async def _emit_new_closed(
        self, symbol: str, bars: list[Bar], secs: int
    ) -> int:
        """Enqueue fully-closed bars newer than the last delivered one (in order)."""
        emitted = 0
        now = time.time()
        last = self._last_emitted.get(symbol)
        for bar in sorted(bars, key=lambda b: b.open_time):
            # Only candles whose interval has fully elapsed are "closed".
            if bar.open_time.timestamp() + secs > now + 1:
                continue
            if last is not None and bar.open_time <= last:
                continue
            self._last_emitted[symbol] = bar.open_time
            last = bar.open_time
            # Keep the WS rollover baseline at/after this bar so it does not
            # re-emit it; only a strictly newer push will roll over.
            current = self._in_progress.get(symbol)
            if current is None or bar.open_time >= current.open_time:
                self._in_progress[symbol] = bar
            await self._queue.put(
                MarketEvent(
                    type=MarketEventType.BAR,
                    ts=bar.open_time,
                    symbol=symbol,
                    bar=bar,
                    warmup=False,
                )
            )
            emitted += 1
        return emitted
