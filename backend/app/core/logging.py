"""Structured logging setup using ``structlog``.

All components log through structlog. A custom processor additionally forwards
errors to the event bus (Kafka) so they end up in the database (REQ-010). The
forwarding is best-effort and never blocks the hot path.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import logging
import sys
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from app.events.bus import EventSink

_configured = False

# --------------------------------------------------------------------------- #
# Forward WARNING/ERROR structlog records to the event bus (REQ-010).
#
# Without this, genuine exceptions logged via ``log.warning``/``log.error`` (REST
# failures, Kafka publish errors, etc.) only reach stdout and never show up in the
# Logs & Errors UI — so real problems looked the same as routine info. The
# forwarding is best-effort and never blocks the caller.
# --------------------------------------------------------------------------- #
_forward_sink: EventSink | None = None
_forward_loop: asyncio.AbstractEventLoop | None = None
# Guards against recursion: the sink may itself log on failure.
_in_forward: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "_in_forward", default=False
)

_FORWARD_LEVELS = {"warning", "warn", "error", "critical", "exception"}
_SKIP_CONTEXT_KEYS = {"event", "level", "timestamp", "logger", "logger_name"}


def set_log_event_sink(
    sink: EventSink | None, loop: asyncio.AbstractEventLoop | None = None
) -> None:
    """Register the sink that WARNING+ logs are mirrored to (process-wide)."""
    global _forward_sink, _forward_loop
    _forward_sink = sink
    if sink is not None:
        try:
            _forward_loop = loop or asyncio.get_event_loop()
        except RuntimeError:  # pragma: no cover - no running loop
            _forward_loop = None


def _normalize_severity(level: str) -> str:
    if level in ("error", "critical", "exception"):
        return "error"
    if level in ("warning", "warn"):
        return "warn"
    return "info"


def _json_safe(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _forward_to_bus_processor(
    _logger: Any, method_name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """structlog processor: mirror WARNING+ events to the event bus.

    Returns ``event_dict`` unchanged so normal rendering still happens.
    """
    sink = _forward_sink
    loop = _forward_loop
    if sink is None or loop is None or not loop.is_running():
        return event_dict
    if _in_forward.get():
        return event_dict
    level = str(event_dict.get("level") or method_name)
    if level not in _FORWARD_LEVELS:
        return event_dict

    from app.events.schemas import ErrorEvent

    context = {
        str(k): _json_safe(v)
        for k, v in event_dict.items()
        if k not in _SKIP_CONTEXT_KEYS
    }
    event = ErrorEvent(
        run_id=str(event_dict.get("run_id") or "system"),
        mode=str(event_dict.get("mode") or "system"),
        ts=datetime.now(UTC),
        source=str(event_dict.get("logger") or _logger_name(_logger) or "log"),
        severity=_normalize_severity(level),
        message=str(event_dict.get("event", "")),
        context=context,
    )

    async def _emit() -> None:
        token = _in_forward.set(True)
        try:
            await sink.emit(event)
        except Exception:  # noqa: BLE001 - forwarding must never raise
            pass
        finally:
            _in_forward.reset(token)

    with contextlib.suppress(Exception):  # scheduling must never raise
        asyncio.run_coroutine_threadsafe(_emit(), loop)
    return event_dict


def _logger_name(logger: Any) -> str | None:
    name = getattr(logger, "name", None)
    return str(name) if name else None


def configure_logging(*, level: str = "INFO", json_logs: bool = False) -> None:
    """Configure structlog + stdlib logging once per process."""
    global _configured
    if _configured:
        return

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, level.upper(), logging.INFO),
    )

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        _forward_to_bus_processor,
    ]

    renderer: Any = (
        structlog.processors.JSONRenderer()
        if json_logs
        else structlog.dev.ConsoleRenderer(colors=False)
    )

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
    _configured = True


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a bound structlog logger."""
    return structlog.get_logger(name)
