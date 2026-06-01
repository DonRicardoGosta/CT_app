"""FastAPI application: REST (history/config/control) + realtime WebSocket.

On startup it opens a Kafka producer for control commands and starts the realtime
hub (Kafka->WebSocket). The API never runs engines and only reads the DB for
history/config; realtime data is forwarded from Kafka (REQ-004/008).
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.config.router import router as config_router
from app.api.control.router import router as control_router
from app.api.history.router import router as history_router
from app.api.realtime.hub import RealtimeHub
from app.api.realtime.router import router as realtime_router
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger

log = get_logger(__name__)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(level=settings.log_level, json_logs=settings.log_json)

    # Realtime hub (Kafka -> browser WS). Degrades gracefully if Kafka is down.
    hub = RealtimeHub()
    app.state.hub = hub
    await hub.start()

    # Long-lived control producer for start/stop commands.
    app.state.control_producer = None
    with contextlib.suppress(Exception):
        from aiokafka import AIOKafkaProducer

        producer = AIOKafkaProducer(
            bootstrap_servers=settings.kafka_bootstrap_servers, acks=1
        )
        await producer.start()
        app.state.control_producer = producer
        log.info("control_producer_started")

    try:
        yield
    finally:
        await hub.stop()
        if app.state.control_producer is not None:
            await app.state.control_producer.stop()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Bitunix Trading Platform API",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health", tags=["meta"])
    async def health():
        return {"status": "ok"}

    app.include_router(history_router, prefix="/api")
    app.include_router(config_router, prefix="/api")
    app.include_router(control_router, prefix="/api")
    app.include_router(realtime_router, prefix="/api/realtime")
    return app


app = create_app()
