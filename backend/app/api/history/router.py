"""History/analytics endpoints (DB-backed, REQ-008/010)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.serialization import rows_to_list
from app.db import repositories as repo
from app.db.session import get_session

router = APIRouter(prefix="/history", tags=["history"])


@router.get("/runs")
async def get_runs(
    limit: int = Query(100, le=1000), session: AsyncSession = Depends(get_session)
):
    return rows_to_list(await repo.list_runs(session, limit))


@router.get("/orders")
async def get_orders(
    run_id: str | None = None,
    limit: int = Query(500, le=5000),
    session: AsyncSession = Depends(get_session),
):
    return rows_to_list(await repo.list_orders(session, run_id, limit))


@router.get("/fills")
async def get_fills(
    run_id: str | None = None,
    limit: int = Query(500, le=5000),
    session: AsyncSession = Depends(get_session),
):
    return rows_to_list(await repo.list_fills(session, run_id, limit))


@router.get("/equity")
async def get_equity(
    run_id: str,
    limit: int = Query(5000, le=50000),
    session: AsyncSession = Depends(get_session),
):
    return rows_to_list(await repo.list_equity(session, run_id, limit))


@router.get("/errors")
async def get_errors(
    run_id: str | None = None,
    source: str | None = None,
    severity: str | None = None,
    limit: int = Query(500, le=5000),
    session: AsyncSession = Depends(get_session),
):
    return rows_to_list(await repo.list_errors(session, run_id, source, severity, limit))
