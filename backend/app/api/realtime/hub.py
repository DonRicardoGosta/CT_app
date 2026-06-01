"""Realtime hub: bridges Kafka events to browser WebSocket clients.

The hub runs one Kafka consumer (a broadcast consumer with a unique group id) and
fans messages out to connected clients. Each client subscribes to a set of channels
(order, fill, position, equity, signal, error, run) and optionally filters by
``run_id``. No database is touched on this path, keeping realtime pages fast.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from dataclasses import dataclass, field

from fastapi import WebSocket

from app.core.config import get_settings
from app.core.logging import get_logger
from app.events.consumer import KafkaEventConsumer
from app.events.topics import get_topics

log = get_logger(__name__)


@dataclass(slots=True)
class Client:
    ws: WebSocket
    channels: set[str] = field(default_factory=set)
    run_id: str | None = None


class RealtimeHub:
    """Owns WebSocket clients and the Kafka->WS broadcast loop."""

    def __init__(self) -> None:
        self._clients: set[Client] = set()
        self._consumer: KafkaEventConsumer | None = None
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        settings = get_settings()
        topics = get_topics()
        self._consumer = KafkaEventConsumer(
            topics.realtime(),
            settings.kafka_bootstrap_servers,
            group_id=f"api-rt-{uuid.uuid4().hex[:8]}",
            auto_offset_reset="latest",
        )
        try:
            await self._consumer.start()
            self._task = asyncio.create_task(self._pump())
            log.info("realtime_hub_started")
        except Exception as exc:  # noqa: BLE001 - API still usable without Kafka
            log.warning("realtime_hub_kafka_unavailable", error=str(exc))
            with contextlib.suppress(Exception):
                await self._consumer.stop()
            self._consumer = None

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        if self._consumer:
            await self._consumer.stop()

    async def connect(self, ws: WebSocket) -> Client:
        await ws.accept()
        client = Client(ws=ws, channels=set())
        async with self._lock:
            self._clients.add(client)
        return client

    async def disconnect(self, client: Client) -> None:
        async with self._lock:
            self._clients.discard(client)

    def configure(self, client: Client, message: dict) -> None:
        """Apply a subscribe/unsubscribe message from a client."""
        action = message.get("action")
        channels = set(message.get("channels", []))
        if action == "subscribe":
            client.channels |= channels
        elif action == "unsubscribe":
            client.channels -= channels
        if "run_id" in message:
            client.run_id = message.get("run_id")

    async def _pump(self) -> None:
        assert self._consumer is not None
        async for _topic, payload in self._consumer.messages():
            await self._broadcast(payload)

    async def _broadcast(self, payload: dict) -> None:
        channel = payload.get("type")
        run_id = payload.get("run_id")
        if channel is None:
            return
        data = json.dumps(payload, default=str)
        dead: list[Client] = []
        for client in list(self._clients):
            if channel not in client.channels:
                continue
            if client.run_id and run_id and client.run_id != run_id:
                continue
            try:
                await client.ws.send_text(data)
            except Exception:  # noqa: BLE001 - client gone
                dead.append(client)
        if dead:
            async with self._lock:
                for c in dead:
                    self._clients.discard(c)
