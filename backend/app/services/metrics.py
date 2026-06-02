"""Per-service CPU/RAM sampler.

Each Python service (backend-api, trading-worker, db-writer) runs this sampler as
a background asyncio task. It records the process CPU% and resident memory (plus
the cgroup memory limit, when available) into ``resource_metrics`` so the System
Health page can chart usage over time.

Sampling is best-effort: any failure (psutil missing, DB briefly down) is logged
and skipped, never crashing the host service.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime
from pathlib import Path

from app.core.logging import get_logger
from app.db.models import ResourceMetric
from app.db.session import session_scope

log = get_logger(__name__)

try:  # psutil is optional at import time; the sampler no-ops without it.
    import psutil
except Exception:  # noqa: BLE001
    psutil = None  # type: ignore[assignment]

_BYTES_PER_MB = 1024 * 1024


def _cgroup_mem_limit_mb() -> float | None:
    """Best-effort container memory limit (cgroup v2 then v1)."""
    candidates = [
        Path("/sys/fs/cgroup/memory.max"),  # cgroup v2
        Path("/sys/fs/cgroup/memory/memory.limit_in_bytes"),  # cgroup v1
    ]
    for path in candidates:
        try:
            raw = path.read_text().strip()
        except OSError:
            continue
        if raw in ("max", ""):
            return None
        try:
            value = int(raw)
        except ValueError:
            continue
        # Unlimited shows up as a huge sentinel; treat as no limit.
        if value <= 0 or value >= 1 << 62:
            return None
        return round(value / _BYTES_PER_MB, 1)
    return None


async def _write_sample(
    service: str, cpu_pct: float, mem_mb: float, limit_mb: float | None
) -> None:
    async with session_scope() as session:
        session.add(
            ResourceMetric(
                ts=datetime.now(UTC),
                service=service,
                cpu_pct=round(cpu_pct, 2),
                mem_mb=round(mem_mb, 1),
                mem_limit_mb=limit_mb,
            )
        )


async def _sample_loop(service: str, interval: float) -> None:
    if psutil is None:
        log.warning("metrics_sampler_disabled", reason="psutil_unavailable")
        return
    proc = psutil.Process()
    proc.cpu_percent(interval=None)  # prime the CPU% baseline
    ncpu = psutil.cpu_count() or 1
    limit_mb = _cgroup_mem_limit_mb()
    while True:
        await asyncio.sleep(interval)
        try:
            # Normalise to a single-core-equivalent percentage across cores.
            cpu = proc.cpu_percent(interval=None) / ncpu
            mem_mb = proc.memory_info().rss / _BYTES_PER_MB
            await _write_sample(service, cpu, mem_mb, limit_mb)
        except Exception as exc:  # noqa: BLE001 - never crash the host service
            log.warning("metrics_sample_failed", service=service, error=str(exc))


def start_sampler(service: str, interval: float = 5.0) -> asyncio.Task:
    """Start the background sampler task for ``service``."""
    return asyncio.create_task(_sample_loop(service, interval))


@contextlib.asynccontextmanager
async def sampler(service: str, interval: float = 5.0):
    """Async context manager that runs the sampler for the block's lifetime."""
    task = start_sampler(service, interval)
    try:
        yield task
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
