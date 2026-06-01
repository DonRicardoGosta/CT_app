"""In-process manager for running engines.

Lives inside the ``trading-worker``. Starts an :class:`Engine` as an asyncio task
per run, tracks status and supports graceful stop. Live/dry runs are long-lived;
backtests finish on their own.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.core.logging import get_logger
from app.domain.engine import Engine
from app.events.bus import EventSink
from app.events.schemas import ErrorEvent
from app.services.builder import build_engine
from app.services.run_config import RunConfig

log = get_logger(__name__)


@dataclass(slots=True)
class RunHandle:
    config: RunConfig
    engine: Engine
    task: asyncio.Task
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    status: str = "running"


class RunManager:
    """Owns and supervises running engines."""

    def __init__(self, sink: EventSink) -> None:
        self._sink = sink
        self._runs: dict[str, RunHandle] = {}

    async def start(
        self, config: RunConfig, *, api_key: str = "", secret_key: str = ""
    ) -> str:
        if config.run_id in self._runs:
            return config.run_id
        try:
            engine = await build_engine(
                config, self._sink, api_key=api_key, secret_key=secret_key
            )
        except Exception as exc:  # noqa: BLE001
            await self._emit_worker_error(config, f"Failed to build engine: {exc}")
            raise
        task = asyncio.create_task(self._supervise(config.run_id, engine))
        self._runs[config.run_id] = RunHandle(config=config, engine=engine, task=task)
        log.info("run_started", run_id=config.run_id, mode=str(config.mode))
        return config.run_id

    async def _supervise(self, run_id: str, engine: Engine) -> None:
        handle = self._runs.get(run_id)
        try:
            await engine.run()
            self._set_status(run_id, "finished")
        except asyncio.CancelledError:
            self._set_status(run_id, "stopped")
            raise
        except Exception as exc:  # noqa: BLE001
            self._set_status(run_id, "failed")
            log.error("run_failed", run_id=run_id, error=str(exc))
            if handle is not None:
                await self._emit_worker_error(handle.config, f"Run failed: {exc}")

    async def _emit_worker_error(self, config: RunConfig, message: str) -> None:
        await self._sink.emit(
            ErrorEvent(
                run_id=config.run_id,
                mode=str(config.mode),
                ts=datetime.now(UTC),
                source="trading-worker",
                severity="error",
                message=message,
            )
        )

    def stop(self, run_id: str) -> bool:
        handle = self._runs.get(run_id)
        if handle is None:
            return False
        handle.engine.request_stop()
        handle.task.cancel()
        return True

    def _set_status(self, run_id: str, status: str) -> None:
        if run_id in self._runs:
            self._runs[run_id].status = status

    def status(self) -> list[dict]:
        return [
            {
                "run_id": rid,
                "mode": str(h.config.mode),
                "strategy": h.config.strategy,
                "status": h.status,
                "started_at": h.started_at.isoformat(),
            }
            for rid, h in self._runs.items()
        ]

    async def shutdown(self) -> None:
        for handle in self._runs.values():
            handle.task.cancel()
        await asyncio.gather(
            *(h.task for h in self._runs.values()), return_exceptions=True
        )
