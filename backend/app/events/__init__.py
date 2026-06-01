"""Event pipeline.

The trading hot path never touches PostgreSQL. It emits typed events through an
:class:`~app.events.bus.EventSink`. In production the sink is Kafka-backed and a
separate ``db_writer`` consumer persists events asynchronously (REQ-004, REQ-010).
In backtest an in-memory sink collects events for inspection.
"""
