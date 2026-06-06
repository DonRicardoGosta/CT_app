"""Bitunix WebSocket client (REQ-002).

Public market streams (klines, tickers) need no auth; private streams use a signed
login. The client auto-reconnects and re-subscribes after a drop. Incoming messages
are pushed onto an asyncio queue that callers consume.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from typing import Any

import websockets

from app.core.logging import get_logger
from app.exchange.bitunix.signing import generate_nonce, now_ms, sign_ws_login

log = get_logger(__name__)

PUBLIC_URL = "wss://fapi.bitunix.com/public/"
PRIVATE_URL = "wss://fapi.bitunix.com/private/"


class BitunixWS:
    """A resilient Bitunix WebSocket connection."""

    def __init__(
        self,
        url: str = PUBLIC_URL,
        *,
        api_key: str = "",
        secret_key: str = "",
        heartbeat_interval: float = 15.0,
    ) -> None:
        self.url = url
        self.api_key = api_key
        self.secret_key = secret_key
        # Bitunix closes connections that do not receive a periodic *application*
        # ping from the client (the library's protocol ping is not honoured), so
        # we send our own ``{"op":"ping"}`` every ``heartbeat_interval`` seconds.
        self.heartbeat_interval = heartbeat_interval
        self._subscriptions: list[dict[str, str]] = []
        self._subscription_keys: set[tuple[str, str | None]] = set()
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self._stopped = False
        self._active_ws: Any | None = None
        self._lock = asyncio.Lock()

    def add_subscription(self, channel: str, symbol: str | None = None) -> None:
        key = (channel, symbol)
        if key in self._subscription_keys:
            return
        sub: dict[str, str] = {"ch": channel}
        if symbol:
            sub["symbol"] = symbol
        self._subscriptions.append(sub)
        self._subscription_keys.add(key)

    async def subscribe(self, channel: str, symbol: str | None = None) -> bool:
        """Add a subscription and send it immediately when connected."""
        key = (channel, symbol)
        async with self._lock:
            if key in self._subscription_keys:
                return False
            self.add_subscription(channel, symbol)
            if self._active_ws is not None:
                sub: dict[str, str] = {"ch": channel}
                if symbol:
                    sub["symbol"] = symbol
                await self._active_ws.send(json.dumps({"op": "subscribe", "args": [sub]}))
        return True

    async def start(self) -> None:
        self._stopped = False
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stopped = True
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def messages(self) -> AsyncIterator[dict[str, Any]]:
        """Yield parsed messages as they arrive."""
        while not self._stopped:
            msg = await self._queue.get()
            yield msg

    # -- internals ---------------------------------------------------------- #
    async def _run(self) -> None:
        backoff = 1.0
        while not self._stopped:
            try:
                # Disable the library's protocol-level keepalive (Bitunix ignores
                # it and drops the socket); we run our own app-level heartbeat.
                async with websockets.connect(self.url, ping_interval=None) as ws:
                    self._active_ws = ws
                    log.info("ws_connected", url=self.url)
                    backoff = 1.0
                    if self.api_key and self.secret_key:
                        await self._login(ws)
                    await self._subscribe(ws)
                    log.info(
                        "ws_subscribed",
                        url=self.url,
                        subscriptions=len(self._subscriptions),
                    )
                    hb = asyncio.create_task(self._heartbeat(ws))
                    try:
                        async for raw in ws:
                            await self._handle_raw(raw, ws)
                    finally:
                        hb.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await hb
                    self._active_ws = None
            except asyncio.CancelledError:
                self._active_ws = None
                raise
            except Exception as exc:  # noqa: BLE001 - reconnect on any failure
                self._active_ws = None
                # WARNING so it surfaces in the UI Logs panel; include the reason
                # and the reconnect delay so a flaky link is visible, not silent.
                log.warning(
                    "ws_disconnected",
                    url=self.url,
                    error=str(exc) or exc.__class__.__name__,
                    reconnect_in_s=backoff,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    async def _heartbeat(self, ws: Any) -> None:
        """Send an application-level ping periodically to keep the socket alive.

        Bitunix drops connections that go ``heartbeat`` seconds without a client
        ping, which showed up as a regular ~75s ``ws_disconnected`` ("no close
        frame received or sent") loop that stopped live candles from arriving.
        """
        while True:
            await asyncio.sleep(self.heartbeat_interval)
            try:
                await ws.send(json.dumps({"op": "ping"}))
            except Exception:  # noqa: BLE001 - the read loop handles reconnects
                return

    async def _login(self, ws: Any) -> None:
        nonce = generate_nonce()
        ts = now_ms()
        sign = sign_ws_login(self.api_key, self.secret_key, nonce, ts)
        await ws.send(
            json.dumps(
                {
                    "op": "login",
                    "args": [
                        {"apiKey": self.api_key, "timestamp": ts, "nonce": nonce, "sign": sign}
                    ],
                }
            )
        )

    async def _subscribe(self, ws: Any) -> None:
        if not self._subscriptions:
            return
        await ws.send(json.dumps({"op": "subscribe", "args": self._subscriptions}))

    async def _handle_raw(self, raw: str | bytes, ws: Any | None = None) -> None:
        try:
            msg = json.loads(raw)
        except (ValueError, TypeError):
            return
        # Bitunix sends application-level pings and CLOSES the connection if we do
        # not pong back. Previously we just dropped the ping, which caused the
        # ~80s reconnect loop (repeated ws_disconnected). Reply with a pong.
        ping = msg.get("ping")
        if ping is not None:
            if ws is not None:
                with contextlib.suppress(Exception):
                    await ws.send(json.dumps({"pong": ping}))
            return
        if msg.get("op") == "ping":
            if ws is not None:
                with contextlib.suppress(Exception):
                    await ws.send(json.dumps({"op": "pong"}))
            return
        # Ignore the server's pong replies to our own keepalives.
        if msg.get("op") == "pong" or msg.get("pong") is not None:
            return
        await self._queue.put(msg)
