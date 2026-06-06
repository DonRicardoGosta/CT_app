"""Live market data feed: a scheduled REST poller (REQ-002/003).

The live path is a simple, fully-logged cron. There is **no warmup phase** and no
WebSocket dependency: on every candle boundary plus a fixed offset (default 20s)
the feed fetches exactly the window of closed klines each symbol needs *right
then*, logs every step, and emits one BAR event per symbol carrying that window.
The engine rebuilds the symbol's rolling state from the window and evaluates the
latest closed candle, so each cycle is self-contained and easy to debug.

This keeps the live decision stream identical in shape to the backtest stream
(closed bars), while making the fetch-decide-act loop explicit in the logs.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime

from app.core.logging import get_logger
from app.domain.interfaces import MarketDataFeed
from app.domain.types import Instrument, MarketEvent, MarketEventType
from app.exchange.bitunix.rest import BitunixRest

log = get_logger(__name__)

_INTERVAL_SECONDS = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
}


class LiveFeed(MarketDataFeed):
    """Streams live market events by polling Bitunix REST on a fixed schedule."""

    def __init__(
        self,
        symbols: list[str],
        instruments: dict[str, Instrument],
        interval: str = "1m",
        *,
        rest: BitunixRest | None = None,
        window_bars: int = 0,
        on_log: Callable[[str, str, dict], Awaitable[None]] | None = None,
        poll_offset_s: float = 20.0,
    ) -> None:
        self._symbols: list[str] = []
        self._symbol_set: set[str] = set()
        self._instruments = instruments
        self._interval = interval
        self._rest = rest
        # How many closed klines to fetch per symbol each cycle (the strategy's
        # required history window). Fetched fresh every cycle — no warmup.
        self._window_bars = max(int(window_bars), 1)
        # Optional async sink for user-visible logs (per-cycle / per-coin steps).
        self._on_log = on_log
        # Seconds to wait past a candle boundary before fetching its close.
        self._poll_offset_s = poll_offset_s
        for sym in symbols:
            if sym not in self._symbol_set:
                self._symbol_set.add(sym)
                self._symbols.append(sym)
        # Open time of the most recent CLOSED bar already delivered per symbol,
        # so a cycle never re-evaluates a candle it has already emitted.
        self._last_emitted: dict[str, datetime] = {}
        self._queue: asyncio.Queue[MarketEvent] = asyncio.Queue()
        self._poll_task: asyncio.Task | None = None

    async def instruments(self) -> dict[str, Instrument]:
        return self._instruments

    async def ensure_symbols(self, symbols: list[str]) -> list[str]:
        """Register newly selected symbols for the scheduled poll."""
        added: list[str] = []
        for symbol in symbols:
            if symbol in self._symbol_set:
                continue
            self._symbol_set.add(symbol)
            self._symbols.append(symbol)
            added.append(symbol)
        if added:
            await self._log(
                "info",
                f"now polling {len(added)} newly selected coin(s) on the "
                f"{self._interval} schedule",
                symbols=added,
            )
        return added

    async def stream(self) -> AsyncIterator[MarketEvent]:
        if self._rest is None:
            raise RuntimeError("LiveFeed requires a REST client to poll klines")
        self._poll_task = asyncio.create_task(self._scheduled_loop())
        try:
            while True:
                yield await self._queue.get()
        finally:
            if self._poll_task is not None:
                self._poll_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._poll_task

    async def _log(self, severity: str, message: str, **context) -> None:
        """Emit a user-visible log (if a sink was provided) plus structlog."""
        if self._on_log is not None:
            with contextlib.suppress(Exception):
                await self._on_log(severity, message, context)
        getattr(log, "warning" if severity in ("warn", "error") else "info")(
            message.replace(" ", "_")[:48], **context
        )

    def _interval_seconds(self) -> int:
        return _INTERVAL_SECONDS.get(self._interval, 60)

    async def _scheduled_loop(self) -> None:
        """Wake `poll_offset_s` after each candle boundary and fetch every coin."""
        secs = self._interval_seconds()
        announced = False
        while True:
            now = time.time()
            next_boundary = (int(now // secs) + 1) * secs
            await asyncio.sleep(max(0.0, next_boundary - now) + self._poll_offset_s)
            if not announced:
                announced = True
                await self._log(
                    "info",
                    f"scheduled {self._interval} poll active: every candle close "
                    f"+{self._poll_offset_s:g}s, fetch each coin and evaluate",
                    interval=self._interval,
                    offset_s=self._poll_offset_s,
                )
            close_dt = datetime.fromtimestamp(next_boundary, tz=UTC)
            fired_dt = datetime.now(UTC)
            symbols = list(self._symbols)
            await self._log(
                "info",
                f"scheduled {self._interval} poll fired at "
                f"{fired_dt:%H:%M:%S} UTC (+{self._poll_offset_s:g}s after close "
                f"{close_dt:%H:%M}): fetching {len(symbols)} coin(s)",
                interval=self._interval,
                close_time=close_dt.isoformat(),
                coins=len(symbols),
            )
            for symbol in symbols:
                await self._fetch_and_emit(symbol, secs)

    async def _fetch_and_emit(self, symbol: str, secs: int) -> None:
        """Fetch this symbol's window, log every step, and emit the latest close."""
        if self._rest is None:
            return
        await self._log(
            "info",
            f"poll {symbol}: fetching last {self._window_bars} {self._interval} klines",
            symbol=symbol,
        )
        try:
            # Fetch one extra to cover the not-yet-closed in-progress candle.
            bars = await self._rest.get_recent_klines(
                symbol, self._interval, self._window_bars + 1
            )
        except Exception as exc:  # noqa: BLE001 - one bad coin must not stop others
            await self._log(
                "warn",
                f"poll {symbol}: history fetch failed: {exc}",
                symbol=symbol,
                error=str(exc),
            )
            return

        now = time.time()
        closed = [b for b in sorted(bars, key=lambda b: b.open_time)
                  if b.open_time.timestamp() + secs <= now + 1]
        closed = closed[-self._window_bars:]
        if not closed:
            await self._log(
                "warn",
                f"poll {symbol}: got {len(bars)} klines but no closed candle yet, skipping",
                symbol=symbol,
            )
            return

        latest = closed[-1]
        await self._log(
            "info",
            f"poll {symbol}: got {len(closed)} closed klines, latest closed "
            f"{latest.open_time:%Y-%m-%d %H:%M} close={latest.close}",
            symbol=symbol,
            candles=len(closed),
            latest_open_time=latest.open_time.isoformat(),
            close=str(latest.close),
        )

        last = self._last_emitted.get(symbol)
        if last is not None and latest.open_time <= last:
            await self._log(
                "info",
                f"poll {symbol}: no new closed candle since "
                f"{last:%H:%M}, skipping evaluation",
                symbol=symbol,
            )
            return
        self._last_emitted[symbol] = latest.open_time
        await self._log(
            "info",
            f"poll {symbol}: new closed candle {latest.open_time:%H:%M} -> evaluating",
            symbol=symbol,
        )
        await self._queue.put(
            MarketEvent(
                type=MarketEventType.BAR,
                ts=latest.open_time,
                symbol=symbol,
                bar=latest,
                window=tuple(closed),
            )
        )
