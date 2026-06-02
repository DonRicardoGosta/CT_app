"""Event sinks: where emitted events go.

* :class:`InMemorySink` — collects events in a list (backtest, tests).
* :class:`KafkaSink` — publishes to Kafka without blocking the hot path (live/dry).

Emitting is fire-and-forget from the engine's perspective: a slow or unavailable
database can never stall trading because the DB is downstream of Kafka (REQ-004).
"""

from __future__ import annotations

import abc
from typing import TYPE_CHECKING

from app.core.logging import get_logger
from app.events.schemas import (
    BaseEvent,
    EquityEvent,
    ErrorEvent,
    EventType,
    FillEvent,
    OrderEvent,
    PositionEvent,
    RunEvent,
    SignalEvent,
)
from app.events.topics import Topics, get_topics

if TYPE_CHECKING:
    from aiokafka import AIOKafkaProducer

log = get_logger(__name__)


def topic_for(event: BaseEvent, topics: Topics) -> str:
    """Resolve the Kafka topic for an event based on its type."""
    etype = EventType(event.type)
    return {
        EventType.ORDER: topics.orders,
        EventType.FILL: topics.fills,
        EventType.POSITION: topics.positions,
        EventType.SIGNAL: topics.signals,
        EventType.EQUITY: topics.equity,
        EventType.ERROR: topics.errors,
        EventType.RUN: topics.runs,
        EventType.MARKET: topics.market,
        EventType.CANDLE: topics.candles,
        EventType.TRADE_LEVEL: topics.trade_levels,
        EventType.WATCHLIST: topics.watchlist,
        EventType.SYMBOL_SUMMARY: topics.symbol_summaries,
    }[etype]


class EventSink(abc.ABC):
    """Abstract destination for events."""

    @abc.abstractmethod
    async def emit(self, event: BaseEvent) -> None:
        """Publish a single event."""

    async def start(self) -> None:  # pragma: no cover - optional lifecycle
        return None

    async def stop(self) -> None:  # pragma: no cover - optional lifecycle
        return None


class InMemorySink(EventSink):
    """Collects events in memory. Used by backtests and unit tests."""

    def __init__(self) -> None:
        self.events: list[BaseEvent] = []

    async def emit(self, event: BaseEvent) -> None:
        self.events.append(event)

    # Convenience typed accessors for assertions/reporting.
    def of(self, etype: EventType) -> list[BaseEvent]:
        return [e for e in self.events if EventType(e.type) is etype]

    @property
    def orders(self) -> list[OrderEvent]:
        return [e for e in self.events if isinstance(e, OrderEvent)]

    @property
    def fills(self) -> list[FillEvent]:
        return [e for e in self.events if isinstance(e, FillEvent)]

    @property
    def positions(self) -> list[PositionEvent]:
        return [e for e in self.events if isinstance(e, PositionEvent)]

    @property
    def signals(self) -> list[SignalEvent]:
        return [e for e in self.events if isinstance(e, SignalEvent)]

    @property
    def equity(self) -> list[EquityEvent]:
        return [e for e in self.events if isinstance(e, EquityEvent)]

    @property
    def errors(self) -> list[ErrorEvent]:
        return [e for e in self.events if isinstance(e, ErrorEvent)]

    @property
    def runs(self) -> list[RunEvent]:
        return [e for e in self.events if isinstance(e, RunEvent)]


class KafkaSink(EventSink):
    """Publishes events to Kafka/Redpanda using aiokafka.

    Producer settings favour low hot-path latency (small linger, ``acks=1``) while
    still batching. Errors during publish are logged but never raised into the
    trading loop.
    """

    def __init__(self, bootstrap_servers: str, topics: Topics | None = None) -> None:
        self._bootstrap = bootstrap_servers
        self._topics = topics or get_topics()
        self._producer: AIOKafkaProducer | None = None

    async def start(self) -> None:
        from aiokafka import AIOKafkaProducer

        self._producer = AIOKafkaProducer(
            bootstrap_servers=self._bootstrap,
            acks=1,
            linger_ms=20,
            enable_idempotence=False,
        )
        await self._producer.start()
        log.info("kafka_producer_started", bootstrap=self._bootstrap)

    async def stop(self) -> None:
        if self._producer is not None:
            await self._producer.stop()
            self._producer = None

    async def emit(self, event: BaseEvent) -> None:
        if self._producer is None:
            log.warning("kafka_sink_not_started", event_type=event.type)
            return
        try:
            topic = topic_for(event, self._topics)
            await self._producer.send(topic, event.model_dump_json().encode())
        except Exception as exc:  # noqa: BLE001 - never break the hot path
            log.error("kafka_emit_failed", error=str(exc), event_type=event.type)


class FanoutSink(EventSink):
    """Emit to several sinks at once (e.g. Kafka + in-memory for live dashboards)."""

    def __init__(self, *sinks: EventSink) -> None:
        self._sinks = list(sinks)

    async def start(self) -> None:
        for s in self._sinks:
            await s.start()

    async def stop(self) -> None:
        for s in self._sinks:
            await s.stop()

    async def emit(self, event: BaseEvent) -> None:
        for s in self._sinks:
            await s.emit(event)
