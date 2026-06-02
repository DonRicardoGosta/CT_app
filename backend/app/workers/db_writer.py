"""db_writer worker: consume events from Kafka and batch-persist to PostgreSQL.

This is the only writer to the trading tables. Because it sits behind Kafka, the
database can be slow or briefly unavailable without ever stalling trading: events
buffer in Kafka and drain when the DB is ready (REQ-004/010).

Events are accumulated and flushed either when a batch fills up or after a short
linger, whichever comes first.
"""

from __future__ import annotations

import asyncio
import contextlib

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.db.repositories import persist_events
from app.db.session import session_scope
from app.events.consumer import KafkaEventConsumer, parse_event
from app.events.schemas import BaseEvent
from app.events.topics import get_topics

log = get_logger(__name__)

BATCH_MAX = 500
LINGER_SECONDS = 0.5


async def _flush(buffer: list[BaseEvent]) -> None:
    if not buffer:
        return
    async with session_scope() as session:
        written = await persist_events(session, buffer)
    log.info("db_writer_flush", rows=written)
    buffer.clear()


async def run() -> None:
    settings = get_settings()
    configure_logging(level=settings.log_level, json_logs=settings.log_json)
    topics = get_topics()
    consumer = KafkaEventConsumer(
        topics.all(),
        settings.kafka_bootstrap_servers,
        group_id="db-writer",
        auto_offset_reset="earliest",
    )
    await consumer.start()
    buffer: list[BaseEvent] = []
    last_flush = asyncio.get_event_loop().time()
    log.info("db_writer_started")

    from app.services.metrics import start_sampler

    metrics_task = start_sampler("db-writer")
    try:
        async for _topic, payload in consumer.messages():
            event = parse_event(payload)
            if event is not None:
                buffer.append(event)
            now = asyncio.get_event_loop().time()
            if len(buffer) >= BATCH_MAX or (buffer and now - last_flush >= LINGER_SECONDS):
                await _flush(buffer)
                last_flush = now
    finally:
        metrics_task.cancel()
        with contextlib.suppress(Exception):
            await metrics_task
        with contextlib.suppress(Exception):
            await _flush(buffer)
        await consumer.stop()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
