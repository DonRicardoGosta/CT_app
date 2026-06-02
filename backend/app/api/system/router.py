"""Operational status for the UI (pipeline health, recent runs)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.serialization import rows_to_list
from app.db import repositories as repo
from app.db.session import get_session

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/status")
async def system_status(
    request: Request, session: AsyncSession = Depends(get_session)
):
    """Lightweight health snapshot for the frontend.

    The API does not run engines; ``trading-worker`` consumes control commands and
    ``db-writer`` persists events. If backtests stay empty, check those services.
    """
    hub = getattr(request.app.state, "hub", None)
    hub_ok = hub is not None and hub._consumer is not None  # noqa: SLF001
    control_ok = getattr(request.app.state, "control_producer", None) is not None
    runs = rows_to_list(await repo.list_runs(session, limit=15))

    return {
        "api": "ok",
        "control_bus": control_ok,
        "realtime_hub": hub_ok,
        "recent_runs": runs,
        "hints": [
            "Start commands go to Kafka; trading-worker must be running.",
            "Equity and errors are written by db-writer after Kafka.",
            "Live updates also stream over WebSocket when the realtime hub is up.",
        ],
    }
