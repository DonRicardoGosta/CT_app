"""Bitunix WebSocket client: application-level ping/pong keepalive.

Regression: the client ignored Bitunix's application pings and never ponged, so
the server closed the socket every ~80s (a ws_disconnected reconnect loop).
"""

from __future__ import annotations

import asyncio
import json

import pytest

from app.exchange.bitunix.ws import BitunixWS


class _FakeWS:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, data: str) -> None:
        self.sent.append(data)


@pytest.mark.asyncio
async def test_heartbeat_sends_app_level_ping():
    # Tiny interval so the heartbeat fires quickly under test.
    ws = BitunixWS(heartbeat_interval=0.01)
    fake = _FakeWS()
    task = asyncio.create_task(ws._heartbeat(fake))
    await asyncio.sleep(0.05)
    task.cancel()
    with __import__("contextlib").suppress(asyncio.CancelledError):
        await task
    assert fake.sent, "heartbeat should have sent at least one ping"
    assert json.loads(fake.sent[0]) == {"op": "ping"}


@pytest.mark.asyncio
async def test_heartbeat_stops_on_send_error():
    class _BrokenWS:
        async def send(self, data: str) -> None:
            raise ConnectionError("closed")

    ws = BitunixWS(heartbeat_interval=0.01)
    # Must return (not raise) so the read loop can drive the reconnect.
    await asyncio.wait_for(ws._heartbeat(_BrokenWS()), timeout=1.0)


@pytest.mark.asyncio
async def test_responds_to_value_ping():
    ws = BitunixWS()
    fake = _FakeWS()
    await ws._handle_raw(json.dumps({"ping": 1717000000}), fake)
    assert fake.sent and json.loads(fake.sent[0]) == {"pong": 1717000000}
    assert ws._queue.empty()  # ping is not forwarded to consumers


@pytest.mark.asyncio
async def test_responds_to_op_ping():
    ws = BitunixWS()
    fake = _FakeWS()
    await ws._handle_raw(json.dumps({"op": "ping"}), fake)
    assert fake.sent and json.loads(fake.sent[0]) == {"op": "pong"}
    assert ws._queue.empty()


@pytest.mark.asyncio
async def test_pong_replies_are_ignored_not_queued():
    ws = BitunixWS()
    fake = _FakeWS()
    await ws._handle_raw(json.dumps({"pong": 123}), fake)
    await ws._handle_raw(json.dumps({"op": "pong"}), fake)
    assert ws._queue.empty()
    assert not fake.sent


@pytest.mark.asyncio
async def test_data_message_is_queued():
    ws = BitunixWS()
    fake = _FakeWS()
    msg = {"ch": "market_kline_15min", "symbol": "BTCUSDT", "data": {"close": "1"}}
    await ws._handle_raw(json.dumps(msg), fake)
    assert not fake.sent
    assert ws._queue.get_nowait() == msg
