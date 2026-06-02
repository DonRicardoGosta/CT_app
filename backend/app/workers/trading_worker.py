"""trading_worker: executes engines in response to control commands.

Listens on the control topic and uses a :class:`RunManager` to start/stop engines.
Engine events are published to Kafka via a :class:`KafkaSink`; they are consumed by
``db_writer`` (persistence) and by the API's realtime hub (browser WebSockets).
"""

from __future__ import annotations

import asyncio
import contextlib

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.events.bus import KafkaSink
from app.events.consumer import KafkaEventConsumer
from app.events.control import ControlCommand
from app.events.topics import get_topics
from app.services.run_manager import RunManager

log = get_logger(__name__)


async def run() -> None:
    settings = get_settings()
    configure_logging(level=settings.log_level, json_logs=settings.log_json)
    topics = get_topics()

    sink = KafkaSink(settings.kafka_bootstrap_servers, topics)
    await sink.start()
    manager = RunManager(sink)

    consumer = KafkaEventConsumer(
        [topics.control],
        settings.kafka_bootstrap_servers,
        group_id="trading-worker",
        auto_offset_reset="earliest",
    )
    await consumer.start()
    log.info("trading_worker_started")
    try:
        async for _topic, payload in consumer.messages():
            await _handle_command(manager, payload)
    finally:
        with contextlib.suppress(Exception):
            await manager.shutdown()
        await consumer.stop()
        await sink.stop()


async def _handle_command(manager: RunManager, payload: dict) -> None:
    try:
        command = ControlCommand.model_validate(payload)
    except Exception as exc:  # noqa: BLE001
        log.warning("bad_control_command", error=str(exc))
        return
    if command.action == "start" and command.config is not None:
        await manager.start(
            command.config, api_key=command.api_key, secret_key=command.secret_key
        )
    elif command.action == "stop" and command.run_id:
        manager.stop(command.run_id)
    else:
        log.warning("unknown_control_action", action=command.action)


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
