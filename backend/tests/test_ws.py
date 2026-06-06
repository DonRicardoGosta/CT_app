"""Bitunix WebSocket client: application-level ping/pong keepalive.

Regression: the client ignored Bitunix's application pings and never ponged, so
the server closed the socket every ~80s (a ws_disconnected reconnect loop).
"""

from __future__ import annotations

import json

import pytest

from app.exchange.bitunix.ws import BitunixWS


class _FakeWS:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, data: str) -> None:
        self.sent.append(data)


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
