"""Persistence and query helpers.

``persist_events`` is used by the ``db_writer`` worker to turn Kafka events into
rows. Query helpers back the history/config REST APIs.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from sqlalchemy import insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    CandleRecord,
    EquitySnapshot,
    ErrorLog,
    FillRecord,
    OrderRecord,
    PositionSnapshot,
    Run,
    SignalRecord,
    SymbolSnapshot,
    TradeLevelSnapshot,
)
from app.events.schemas import (
    BaseEvent,
    CandleEvent,
    EquityEvent,
    ErrorEvent,
    EventType,
    FillEvent,
    OrderEvent,
    PositionEvent,
    RunEvent,
    SignalEvent,
    SymbolSummaryEvent,
    TradeLevelEvent,
)


def _order_row(e: OrderEvent) -> dict[str, Any]:
    return {
        "order_id": e.order_id,
        "client_id": e.client_id,
        "run_id": e.run_id,
        "mode": e.mode,
        "ts": e.ts,
        "symbol": e.symbol,
        "side": e.side,
        "position_side": e.position_side,
        "order_type": e.order_type,
        "qty": e.qty,
        "price": e.price,
        "leverage": e.leverage,
        "status": e.status,
        "filled_qty": e.filled_qty,
        "avg_fill_price": e.avg_fill_price,
        "reduce_only": e.reduce_only,
        "reason": e.reason,
        "tag": e.tag,
    }


def _fill_row(e: FillEvent) -> dict[str, Any]:
    return {
        "order_id": e.order_id,
        "run_id": e.run_id,
        "mode": e.mode,
        "ts": e.ts,
        "symbol": e.symbol,
        "side": e.side,
        "position_side": e.position_side,
        "qty": e.qty,
        "price": e.price,
        "fee": e.fee,
        "realized_pnl": e.realized_pnl,
    }


def _position_row(e: PositionEvent) -> dict[str, Any]:
    return {
        "run_id": e.run_id,
        "mode": e.mode,
        "ts": e.ts,
        "symbol": e.symbol,
        "position_side": e.position_side,
        "qty": e.qty,
        "entry_price": e.entry_price,
        "mark_price": e.mark_price,
        "leverage": e.leverage,
        "margin": e.margin,
        "unrealized_pnl": e.unrealized_pnl,
        "realized_pnl": e.realized_pnl,
        "step_count": e.step_count,
    }


def _signal_row(e: SignalEvent) -> dict[str, Any]:
    return {
        "run_id": e.run_id,
        "mode": e.mode,
        "ts": e.ts,
        "strategy": e.strategy,
        "symbol": e.symbol,
        "side": e.side,
        "action": e.action,
        "weight": e.weight,
        "reason": e.reason,
        "tag": e.tag,
        "planned_entry": e.planned_entry,
        "stop_loss": e.stop_loss,
        "take_profit": e.take_profit,
    }


def _candle_row(e: CandleEvent) -> dict[str, Any]:
    return {
        "run_id": e.run_id,
        "mode": e.mode,
        "ts": e.ts,
        "symbol": e.symbol,
        "interval": e.interval,
        "open_time": e.open_time,
        "open": e.open,
        "high": e.high,
        "low": e.low,
        "close": e.close,
        "volume": e.volume,
        "closed": e.closed,
    }


def _trade_level_row(e: TradeLevelEvent) -> dict[str, Any]:
    return {
        "run_id": e.run_id,
        "mode": e.mode,
        "ts": e.ts,
        "symbol": e.symbol,
        "position_side": e.position_side,
        "current_price": e.current_price,
        "planned_entry": e.planned_entry,
        "actual_entry": e.actual_entry,
        "take_profit": e.take_profit,
        "stop_loss": e.stop_loss,
        "liquidation_price": e.liquidation_price,
        "source": e.source,
    }


def _symbol_summary_row(e: SymbolSummaryEvent) -> dict[str, Any]:
    return {
        "run_id": e.run_id,
        "mode": e.mode,
        "ts": e.ts,
        "symbol": e.symbol,
        "status": e.status,
        "last_price": e.last_price,
        "change_pct": e.change_pct,
        "position_side": e.position_side,
        "unrealized_pnl": e.unrealized_pnl,
        "realized_pnl": e.realized_pnl,
        "step_count": e.step_count,
        "max_steps": e.max_steps,
        "last_signal_reason": e.last_signal_reason,
    }


def _equity_row(e: EquityEvent) -> dict[str, Any]:
    return {
        "run_id": e.run_id,
        "mode": e.mode,
        "ts": e.ts,
        "balance": e.balance,
        "equity": e.equity,
        "used_margin": e.used_margin,
        "unrealized_pnl": e.unrealized_pnl,
        "open_positions": e.open_positions,
    }


def _error_row(e: ErrorEvent) -> dict[str, Any]:
    return {
        "run_id": e.run_id,
        "mode": e.mode,
        "ts": e.ts,
        "source": e.source,
        "severity": e.severity,
        "message": e.message,
        "detail": e.detail,
        "context": e.context,
    }


_MODEL_BUILDERS: dict[EventType, tuple[type, Callable[[Any], dict[str, Any]]]] = {
    EventType.ORDER: (OrderRecord, _order_row),
    EventType.FILL: (FillRecord, _fill_row),
    EventType.POSITION: (PositionSnapshot, _position_row),
    EventType.SIGNAL: (SignalRecord, _signal_row),
    EventType.CANDLE: (CandleRecord, _candle_row),
    EventType.TRADE_LEVEL: (TradeLevelSnapshot, _trade_level_row),
    EventType.SYMBOL_SUMMARY: (SymbolSnapshot, _symbol_summary_row),
    EventType.EQUITY: (EquitySnapshot, _equity_row),
    EventType.ERROR: (ErrorLog, _error_row),
}


async def persist_events(session: AsyncSession, events: list[BaseEvent]) -> int:
    """Persist a batch of events. Returns the number of rows written.

    Events are grouped by type and bulk-inserted. ``RUN`` events upsert the runs
    table so a run row reflects the latest status.
    """
    by_type: dict[EventType, list[dict[str, Any]]] = {}
    written = 0

    for event in events:
        etype = EventType(event.type)
        if etype is EventType.RUN:
            await _upsert_run(session, event)  # type: ignore[arg-type]
            written += 1
            continue
        builder = _MODEL_BUILDERS.get(etype)
        if builder is None:
            continue
        _, row_fn = builder
        by_type.setdefault(etype, []).append(row_fn(event))

    for etype, rows in by_type.items():
        if not rows:
            continue
        model, _ = _MODEL_BUILDERS[etype]
        await session.execute(insert(model), rows)
        written += len(rows)

    return written


async def _upsert_run(session: AsyncSession, e: RunEvent) -> None:
    now: datetime = e.ts
    values = {
        "id": e.run_id,
        "strategy": e.strategy,
        "mode": e.mode,
        "status": e.status,
        "started_at": now,
        "finished_at": now if e.status in {"finished", "failed", "stopped"} else None,
        "summary": {"detail": e.detail} if e.detail else {},
    }
    stmt = pg_insert(Run).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=[Run.id],
        set_={
            "status": stmt.excluded.status,
            "finished_at": stmt.excluded.finished_at,
            "summary": stmt.excluded.summary,
        },
    )
    await session.execute(stmt)


# --------------------------------------------------------------------------- #
# Query helpers (history API)
# --------------------------------------------------------------------------- #
async def list_runs(session: AsyncSession, limit: int = 100) -> list[Run]:
    res = await session.execute(select(Run).order_by(Run.started_at.desc()).limit(limit))
    return list(res.scalars().all())


async def list_orders(
    session: AsyncSession, run_id: str | None = None, limit: int = 500
) -> list[OrderRecord]:
    stmt = select(OrderRecord).order_by(OrderRecord.ts.desc()).limit(limit)
    if run_id:
        stmt = stmt.where(OrderRecord.run_id == run_id)
    res = await session.execute(stmt)
    return list(res.scalars().all())


async def list_fills(
    session: AsyncSession, run_id: str | None = None, limit: int = 500
) -> list[FillRecord]:
    stmt = select(FillRecord).order_by(FillRecord.ts.desc()).limit(limit)
    if run_id:
        stmt = stmt.where(FillRecord.run_id == run_id)
    res = await session.execute(stmt)
    return list(res.scalars().all())


async def list_equity(
    session: AsyncSession, run_id: str, limit: int = 5000
) -> list[EquitySnapshot]:
    stmt = (
        select(EquitySnapshot)
        .where(EquitySnapshot.run_id == run_id)
        .order_by(EquitySnapshot.ts.asc())
        .limit(limit)
    )
    res = await session.execute(stmt)
    return list(res.scalars().all())


async def list_errors(
    session: AsyncSession,
    source: str | None = None,
    severity: str | None = None,
    limit: int = 500,
) -> list[ErrorLog]:
    stmt = select(ErrorLog).order_by(ErrorLog.ts.desc()).limit(limit)
    if source:
        stmt = stmt.where(ErrorLog.source == source)
    if severity:
        stmt = stmt.where(ErrorLog.severity == severity)
    res = await session.execute(stmt)
    return list(res.scalars().all())


async def list_signals(
    session: AsyncSession,
    run_id: str | None = None,
    symbol: str | None = None,
    limit: int = 500,
) -> list[SignalRecord]:
    stmt = select(SignalRecord).order_by(SignalRecord.ts.desc()).limit(limit)
    if run_id:
        stmt = stmt.where(SignalRecord.run_id == run_id)
    if symbol:
        stmt = stmt.where(SignalRecord.symbol == symbol)
    res = await session.execute(stmt)
    return list(res.scalars().all())


async def list_candles(
    session: AsyncSession,
    *,
    run_id: str | None,
    symbol: str,
    interval: str,
    limit: int = 1000,
) -> list[CandleRecord]:
    stmt = (
        select(CandleRecord)
        .where(CandleRecord.symbol == symbol, CandleRecord.interval == interval)
        .order_by(CandleRecord.open_time.desc())
        .limit(limit)
    )
    if run_id:
        stmt = stmt.where(CandleRecord.run_id == run_id)
    res = await session.execute(stmt)
    return list(reversed(res.scalars().all()))


async def list_trade_levels(
    session: AsyncSession,
    *,
    run_id: str | None = None,
    symbol: str | None = None,
    limit: int = 500,
) -> list[TradeLevelSnapshot]:
    stmt = select(TradeLevelSnapshot).order_by(TradeLevelSnapshot.ts.desc()).limit(limit)
    if run_id:
        stmt = stmt.where(TradeLevelSnapshot.run_id == run_id)
    if symbol:
        stmt = stmt.where(TradeLevelSnapshot.symbol == symbol)
    res = await session.execute(stmt)
    return list(res.scalars().all())


async def list_symbol_snapshots(
    session: AsyncSession, run_id: str | None = None, limit: int = 500
) -> list[SymbolSnapshot]:
    stmt = select(SymbolSnapshot).order_by(SymbolSnapshot.ts.desc()).limit(limit)
    if run_id:
        stmt = stmt.where(SymbolSnapshot.run_id == run_id)
    res = await session.execute(stmt)
    return list(res.scalars().all())
