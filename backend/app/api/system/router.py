"""System Health metrics: CPU/RAM history and latest snapshot per service."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import repositories as repo
from app.db.session import get_session

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/metrics")
async def system_metrics(
    range: str = Query(default="1h", pattern="^(15m|1h|6h|24h)$"),
    session: AsyncSession = Depends(get_session),
):
    return await repo.list_resource_metrics(session, range_key=range)


@router.get("/status")
async def system_status(session: AsyncSession = Depends(get_session)):
    return await repo.latest_resource_metrics(session)
