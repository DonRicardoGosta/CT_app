"""Kafka consumer helpers.

Used by the ``db_writer`` (persist events) and by the API's realtime hub (forward
events to browsers). Parsing turns a raw topic payload back into a typed event
model based on its ``type`` field.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from aiokafka import AIOKafkaConsumer

from app.core.logging import get_logger
from app.events.schemas import EVENT_MODELS, BaseEvent, EventType

log = get_logger(__name__)


def parse_event(payload: bytes | str | dict[str, Any]) -> BaseEvent | None:
    """Parse a raw event payload into the matching typed model."""
    if isinstance(payload, (bytes, str)):
        try:
            data = json.loads(payload)
        except (ValueError, TypeError):
            return None
    else:
        data = payload
    try:
        etype = EventType(data["type"])
    except (KeyError, ValueError):
        return None
    model = EVENT_MODELS.get(etype)
    if model is None:
        return None
    try:
        return model.model_validate(data)
    except Exception as exc:  # noqa: BLE001
        log.warning("event_parse_failed", error=str(exc), type=str(data.get("type")))
        return None


class KafkaEventConsumer:
    """Thin wrapper around aiokafka's consumer yielding raw decoded dicts."""

    def __init__(
        self,
        topics: list[str],
        bootstrap_servers: str,
        group_id: str,
        auto_offset_reset: str = "latest",
    ) -> None:
        self._topics = topics
        self._bootstrap = bootstrap_servers
        self._group_id = group_id
        self._auto_offset_reset = auto_offset_reset
        self._consumer: AIOKafkaConsumer | None = None

    async def start(self) -> None:
        from aiokafka import AIOKafkaConsumer

        self._consumer = AIOKafkaConsumer(
            *self._topics,
            bootstrap_servers=self._bootstrap,
            group_id=self._group_id,
            auto_offset_reset=self._auto_offset_reset,
            enable_auto_commit=True,
        )
        await self._consumer.start()
        log.info("kafka_consumer_started", topics=self._topics, group=self._group_id)

    async def stop(self) -> None:
        if self._consumer is not None:
            await self._consumer.stop()
            self._consumer = None

    async def messages(self) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        assert self._consumer is not None, "consumer not started"
        async for msg in self._consumer:
            try:
                data = json.loads(msg.value)
            except (ValueError, TypeError):
                continue
            yield msg.topic, data
