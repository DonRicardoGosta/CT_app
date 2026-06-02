"""Kafka topic names, prefixed so multiple environments can share a cluster."""

from __future__ import annotations

from app.core.config import get_settings


class Topics:
    """Resolved topic names for a given prefix."""

    def __init__(self, prefix: str) -> None:
        self.orders = f"{prefix}.orders"
        self.fills = f"{prefix}.fills"
        self.positions = f"{prefix}.positions"
        self.signals = f"{prefix}.signals"
        self.equity = f"{prefix}.equity"
        self.errors = f"{prefix}.errors"
        self.market = f"{prefix}.market"
        self.candles = f"{prefix}.candles"
        self.trade_levels = f"{prefix}.trade_levels"
        self.symbol_summaries = f"{prefix}.symbol_summaries"
        self.runs = f"{prefix}.runs"
        # Control commands (start/stop a run) sent from the API to the worker.
        self.control = f"{prefix}.control"

    def all(self) -> list[str]:
        return [
            self.orders,
            self.fills,
            self.positions,
            self.signals,
            self.equity,
            self.errors,
            self.market,
            self.candles,
            self.trade_levels,
            self.symbol_summaries,
            self.runs,
        ]

    def realtime(self) -> list[str]:
        """Topics the API forwards to browsers over WebSocket."""
        return [
            self.orders,
            self.fills,
            self.positions,
            self.signals,
            self.equity,
            self.errors,
            self.market,
            self.candles,
            self.trade_levels,
            self.symbol_summaries,
            self.runs,
        ]


def get_topics() -> Topics:
    return Topics(get_settings().kafka_topic_prefix)
