"""Persistence and query helpers.

``persist_events`` is used by the ``db_writer`` worker to turn Kafka events into
rows. Query helpers back the history/config REST APIs.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    EquitySnapshot,
    ErrorLog,
    FillRecord,
    OrderRecord,
    PositionSnapshot,
    Run,
    SignalRecord,
)
from app.events.schemas import (
    BaseEvent,
    EquityEvent,
    ErrorEvent,
    EventType,
    FillEvent,
    OrderEvent,
    PositionEvent,
    RunEvent,
    SignalEvent,
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


# --------------------------------------------------------------------------- #
# Unified log feed (Grafana-like Logs & Errors UI)
# --------------------------------------------------------------------------- #
def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(v) for v in value]
    return value


def _log_row(
    *,
    ts: datetime,
    severity: str,
    source: str,
    message: str,
    run_id: str | None,
    mode: str | None,
    kind: str,
    symbol: str | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "ts": ts,
        "severity": severity,
        "source": source,
        "message": message,
        "run_id": run_id,
        "mode": mode,
        "symbol": symbol,
        "kind": kind,
        "context": _json_value(context or {}),
    }


def _matches_log_filters(
    row: dict[str, Any],
    *,
    run_id: str | None,
    mode: str | None,
    severity: str | None,
    source: str | None,
    q: str | None,
) -> bool:
    if run_id and row.get("run_id") != run_id:
        return False
    if mode and row.get("mode") != mode:
        return False
    if severity and row.get("severity") != severity:
        return False
    if source and row.get("source") != source:
        return False
    if q:
        needle = q.lower()
        blob = " ".join(
            str(row.get(k, ""))
            for k in ("severity", "source", "message", "run_id", "mode", "symbol", "kind")
        )
        blob += f" {row.get('context', {})}"
        if needle not in blob.lower():
            return False
    return True


async def list_log_entries(
    session: AsyncSession,
    *,
    run_id: str | None = None,
    mode: str | None = None,
    severity: str | None = None,
    source: str | None = None,
    q: str | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """Return a normalized log stream from errors plus trading history tables.

    ``error_log`` contains explicit application log/error events. Runs/orders/
    fills/signals are also user-visible events, so the Logs UI should show them
    even when no error has occurred.
    """
    rows: list[dict[str, Any]] = []

    err_stmt = select(ErrorLog).order_by(ErrorLog.ts.desc()).limit(limit)
    run_stmt = select(Run).order_by(Run.started_at.desc()).limit(limit)
    order_stmt = select(OrderRecord).order_by(OrderRecord.ts.desc()).limit(limit)
    fill_stmt = select(FillRecord).order_by(FillRecord.ts.desc()).limit(limit)
    signal_stmt = select(SignalRecord).order_by(SignalRecord.ts.desc()).limit(limit)

    if run_id:
        err_stmt = err_stmt.where(ErrorLog.run_id == run_id)
        run_stmt = run_stmt.where(Run.id == run_id)
        order_stmt = order_stmt.where(OrderRecord.run_id == run_id)
        fill_stmt = fill_stmt.where(FillRecord.run_id == run_id)
        signal_stmt = signal_stmt.where(SignalRecord.run_id == run_id)
    if mode:
        err_stmt = err_stmt.where(ErrorLog.mode == mode)
        run_stmt = run_stmt.where(Run.mode == mode)
        order_stmt = order_stmt.where(OrderRecord.mode == mode)
        fill_stmt = fill_stmt.where(FillRecord.mode == mode)
        signal_stmt = signal_stmt.where(SignalRecord.mode == mode)

    for e in (await session.execute(err_stmt)).scalars().all():
        rows.append(
            _log_row(
                ts=e.ts,
                severity=e.severity,
                source=e.source,
                message=e.message,
                run_id=e.run_id,
                mode=e.mode,
                kind="error",
                context={"detail": e.detail, **(e.context or {})},
            )
        )

    for r in (await session.execute(run_stmt)).scalars().all():
        sev = "error" if r.status == "failed" else "info"
        ts = r.finished_at if r.finished_at is not None else r.started_at
        rows.append(
            _log_row(
                ts=ts,
                severity=sev,
                source="run",
                message=f"run {r.status}: {r.strategy} ({r.mode})",
                run_id=r.id,
                mode=r.mode,
                kind="run",
                context={"strategy": r.strategy, "status": r.status, "summary": r.summary},
            )
        )

    for o in (await session.execute(order_stmt)).scalars().all():
        rows.append(
            _log_row(
                ts=o.ts,
                severity="info",
                source="order",
                message=f"order {o.status}: {o.side} {o.symbol} qty={o.qty}",
                run_id=o.run_id,
                mode=o.mode,
                kind="order",
                symbol=o.symbol,
                context={
                    "order_id": o.order_id,
                    "position_side": o.position_side,
                    "order_type": o.order_type,
                    "price": o.price,
                    "leverage": o.leverage,
                    "filled_qty": o.filled_qty,
                    "avg_fill_price": o.avg_fill_price,
                    "reason": o.reason,
                    "tag": o.tag,
                },
            )
        )

    for f in (await session.execute(fill_stmt)).scalars().all():
        rows.append(
            _log_row(
                ts=f.ts,
                severity="info",
                source="fill",
                message=f"fill {f.side} {f.symbol} qty={f.qty} @ {f.price}",
                run_id=f.run_id,
                mode=f.mode,
                kind="fill",
                symbol=f.symbol,
                context={
                    "order_id": f.order_id,
                    "position_side": f.position_side,
                    "fee": f.fee,
                    "realized_pnl": f.realized_pnl,
                },
            )
        )

    for s in (await session.execute(signal_stmt)).scalars().all():
        rows.append(
            _log_row(
                ts=s.ts,
                severity="info",
                source="signal",
                message=f"signal {s.action}: {s.side} {s.symbol} ({s.reason})",
                run_id=s.run_id,
                mode=s.mode,
                kind="signal",
                symbol=s.symbol,
                context={
                    "strategy": s.strategy,
                    "weight": s.weight,
                    "reason": s.reason,
                    "tag": s.tag,
                },
            )
        )

    filtered = [
        row
        for row in rows
        if _matches_log_filters(
            row,
            run_id=run_id,
            mode=mode,
            severity=severity,
            source=source,
            q=q,
        )
    ]
    filtered.sort(key=lambda row: row["ts"], reverse=True)
    return filtered[:limit]
