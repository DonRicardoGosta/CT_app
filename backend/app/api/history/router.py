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
    source: str | None = None,
    severity: str | None = None,
    limit: int = Query(500, le=5000),
    session: AsyncSession = Depends(get_session),
):
    return rows_to_list(await repo.list_errors(session, source, severity, limit))


@router.get("/signals")
async def get_signals(
    run_id: str | None = None,
    symbol: str | None = None,
    limit: int = Query(500, le=5000),
    session: AsyncSession = Depends(get_session),
):
    return rows_to_list(await repo.list_signals(session, run_id, symbol, limit))


@router.get("/candles")
async def get_candles(
    symbol: str,
    interval: str = "1m",
    run_id: str | None = None,
    limit: int = Query(1000, le=10000),
    session: AsyncSession = Depends(get_session),
):
    return rows_to_list(
        await repo.list_candles(
            session, run_id=run_id, symbol=symbol, interval=interval, limit=limit
        )
    )


@router.get("/trade-overlays")
async def get_trade_overlays(
    run_id: str | None = None,
    symbol: str | None = None,
    limit: int = Query(500, le=5000),
    session: AsyncSession = Depends(get_session),
):
    levels = await repo.list_trade_levels(session, run_id=run_id, symbol=symbol, limit=limit)
    return rows_to_list(levels)


@router.get("/symbol-summary")
async def get_symbol_summary(
    run_id: str | None = None,
    limit: int = Query(500, le=5000),
    session: AsyncSession = Depends(get_session),
):
    # Keep only the latest snapshot per symbol; the repository returns newest first.
    latest = {}
    for row in await repo.list_symbol_snapshots(session, run_id=run_id, limit=limit):
        latest.setdefault(row.symbol, row)
    return rows_to_list(list(latest.values()))
