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
            await self.emit_log(
                run_id=config.run_id,
                mode=str(config.mode),
                source="run_manager",
                severity="warn",
                message="duplicate start ignored",
                context={"strategy": config.strategy},
            )
            return config.run_id
        await self.emit_log(
            run_id=config.run_id,
            mode=str(config.mode),
            source="run_manager",
            severity="info",
            message=f"start accepted: {config.strategy} ({config.mode})",
            context={
                "strategy": config.strategy,
                "symbols": config.symbols,
                "interval": config.interval,
            },
        )
        try:
            engine = await build_engine(
                config, self._sink, api_key=api_key, secret_key=secret_key
            )
        except Exception as exc:  # noqa: BLE001
            await self.emit_log(
                run_id=config.run_id,
                mode=str(config.mode),
                source="run_manager",
                severity="error",
                message=f"build engine failed: {exc}",
                context={"strategy": config.strategy},
            )
            raise
        task = asyncio.create_task(self._supervise(config.run_id, engine))
        self._runs[config.run_id] = RunHandle(config=config, engine=engine, task=task)
        log.info("run_started", run_id=config.run_id, mode=str(config.mode))
        return config.run_id

    async def _supervise(self, run_id: str, engine: Engine) -> None:
        try:
            await engine.run()
            self._set_status(run_id, "finished")
            await self.emit_log(
                run_id=run_id,
                mode=engine.mode.value,
                source="run_manager",
                severity="info",
                message="run finished",
            )
        except asyncio.CancelledError:
            self._set_status(run_id, "stopped")
            await self.emit_log(
                run_id=run_id,
                mode=engine.mode.value,
                source="run_manager",
                severity="warn",
                message="run stopped",
            )
            raise
        except Exception as exc:  # noqa: BLE001
            self._set_status(run_id, "failed")
            log.error("run_failed", run_id=run_id, error=str(exc))
            await self.emit_log(
                run_id=run_id,
                mode=engine.mode.value,
                source="run_manager",
                severity="error",
                message=f"run failed: {exc}",
            )

    def stop(self, run_id: str) -> bool:
        handle = self._runs.get(run_id)
        if handle is None:
            return False
        handle.engine.request_stop()
        handle.task.cancel()
        return True

    async def emit_log(
        self,
        *,
        run_id: str,
        mode: str,
        source: str,
        severity: str,
        message: str,
        context: dict | None = None,
    ) -> None:
        await self._sink.emit(
            ErrorEvent(
                run_id=run_id,
                mode=mode,
                ts=datetime.now(UTC),
                source=source,
                severity=severity,
                message=message,
                context=context or {},
            )
        )

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
