"""Control commands sent from the API to the trading worker over Kafka.

The API never runs engines itself; it publishes a command and the ``trading-worker``
acts on it. This keeps the API responsive and the heavy work isolated (REQ-004/011).
"""

from __future__ import annotations

from pydantic import BaseModel

from app.services.run_config import RunConfig


class ControlCommand(BaseModel):
    action: str  # "start" | "stop"
    run_id: str | None = None
    config: RunConfig | None = None
    # Decrypted credentials are passed inline for live runs (intra-cluster only).
    api_key: str = ""
    secret_key: str = ""


async def send_control(bootstrap_servers: str, topic: str, command: ControlCommand) -> None:
    """Publish a single control command and flush."""
    from aiokafka import AIOKafkaProducer

    producer = AIOKafkaProducer(bootstrap_servers=bootstrap_servers, acks=1)
    await producer.start()
    try:
        await producer.send_and_wait(topic, command.model_dump_json().encode())
    finally:
        await producer.stop()
