"""ORM models.

Indexing policy (REQ-010): every column commonly used for filtering, joining or
time-ordering is indexed. Storage is cheap relative to the value of fast queries,
so we index liberally. Money/qty/price columns use ``Numeric(38, 18)``.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin

# Reusable numeric type for monetary/quantity values.
NUM = Numeric(38, 18)


# --------------------------------------------------------------------------- #
# Configuration (lives in the DB, edited from the frontend — REQ-009)
# --------------------------------------------------------------------------- #
class AppSetting(Base, TimestampMixin):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    description: Mapped[str] = mapped_column(Text, default="")


class ApiKey(Base, TimestampMixin):
    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    exchange: Mapped[str] = mapped_column(String(32), default="bitunix", index=True)
    api_key: Mapped[str] = mapped_column(String(256), nullable=False)
    # Encrypted at rest (Fernet); plaintext never leaves the backend.
    secret_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)


class RiskConfig(Base, TimestampMixin):
    __tablename__ = "risk_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    max_capital_usd: Mapped[Decimal] = mapped_column(NUM, nullable=False)
    max_loss_usd: Mapped[Decimal] = mapped_column(NUM, nullable=False)
    min_investment_usd: Mapped[Decimal] = mapped_column(NUM, nullable=False)
    base_leverage: Mapped[int] = mapped_column(Integer, default=1)
    max_leverage: Mapped[int] = mapped_column(Integer, default=20)
    leverage_step: Mapped[int] = mapped_column(Integer, default=1)
    allow_hedge: Mapped[bool] = mapped_column(Boolean, default=True)
    fee_rate: Mapped[Decimal] = mapped_column(NUM, default=Decimal("0.0006"))


class StrategyConfig(Base, TimestampMixin):
    __tablename__ = "strategy_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    strategy: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    params: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    risk_config_id: Mapped[int | None] = mapped_column(Integer, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)


# --------------------------------------------------------------------------- #
# Runs and trading records (written by db_writer from Kafka — REQ-004)
# --------------------------------------------------------------------------- #
class Run(Base, TimestampMixin):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    strategy: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    mode: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    config: Mapped[dict] = mapped_column(JSONB, default=dict)
    summary: Mapped[dict] = mapped_column(JSONB, default=dict)


class OrderRecord(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(String(64), nullable=False)
    client_id: Mapped[str | None] = mapped_column(String(64))
    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    position_side: Mapped[str] = mapped_column(String(8), nullable=False)
    order_type: Mapped[str] = mapped_column(String(8), nullable=False)
    qty: Mapped[Decimal] = mapped_column(NUM, nullable=False)
    price: Mapped[Decimal | None] = mapped_column(NUM)
    leverage: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    filled_qty: Mapped[Decimal] = mapped_column(NUM, default=Decimal("0"))
    avg_fill_price: Mapped[Decimal] = mapped_column(NUM, default=Decimal("0"))
    reduce_only: Mapped[bool] = mapped_column(Boolean, default=False)
    reason: Mapped[str] = mapped_column(Text, default="")
    tag: Mapped[str] = mapped_column(String(64), default="")

    __table_args__ = (
        Index("ix_orders_run_ts", "run_id", "ts"),
        Index("ix_orders_symbol_ts", "symbol", "ts"),
        Index("ix_orders_status", "status"),
        Index("ix_orders_order_id", "order_id"),
        Index("ix_orders_mode", "mode"),
    )


class FillRecord(Base):
    __tablename__ = "fills"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(String(64), nullable=False)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    position_side: Mapped[str] = mapped_column(String(8), nullable=False)
    qty: Mapped[Decimal] = mapped_column(NUM, nullable=False)
    price: Mapped[Decimal] = mapped_column(NUM, nullable=False)
    fee: Mapped[Decimal] = mapped_column(NUM, default=Decimal("0"))
    realized_pnl: Mapped[Decimal] = mapped_column(NUM, default=Decimal("0"))

    __table_args__ = (
        Index("ix_fills_run_ts", "run_id", "ts"),
        Index("ix_fills_symbol_ts", "symbol", "ts"),
        Index("ix_fills_order_id", "order_id"),
    )


class PositionSnapshot(Base):
    __tablename__ = "position_snapshots"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    position_side: Mapped[str] = mapped_column(String(8), nullable=False)
    qty: Mapped[Decimal] = mapped_column(NUM, nullable=False)
    entry_price: Mapped[Decimal] = mapped_column(NUM, nullable=False)
    mark_price: Mapped[Decimal] = mapped_column(NUM, default=Decimal("0"))
    leverage: Mapped[int] = mapped_column(Integer, default=1)
    margin: Mapped[Decimal] = mapped_column(NUM, default=Decimal("0"))
    unrealized_pnl: Mapped[Decimal] = mapped_column(NUM, default=Decimal("0"))
    realized_pnl: Mapped[Decimal] = mapped_column(NUM, default=Decimal("0"))
    step_count: Mapped[int] = mapped_column(Integer, default=0)

    __table_args__ = (
        Index("ix_pos_run_ts", "run_id", "ts"),
        Index("ix_pos_symbol_ts", "symbol", "ts"),
    )


class SignalRecord(Base):
    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    strategy: Mapped[str] = mapped_column(String(64), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    action: Mapped[str] = mapped_column(String(8), nullable=False)
    weight: Mapped[Decimal] = mapped_column(NUM, default=Decimal("1"))
    reason: Mapped[str] = mapped_column(Text, default="")
    tag: Mapped[str] = mapped_column(String(64), default="")

    __table_args__ = (
        Index("ix_signals_run_ts", "run_id", "ts"),
        Index("ix_signals_symbol_ts", "symbol", "ts"),
        Index("ix_signals_strategy", "strategy"),
    )


class EquitySnapshot(Base):
    __tablename__ = "equity_snapshots"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    balance: Mapped[Decimal] = mapped_column(NUM, nullable=False)
    equity: Mapped[Decimal] = mapped_column(NUM, nullable=False)
    used_margin: Mapped[Decimal] = mapped_column(NUM, default=Decimal("0"))
    unrealized_pnl: Mapped[Decimal] = mapped_column(NUM, default=Decimal("0"))
    open_positions: Mapped[int] = mapped_column(Integer, default=0)

    __table_args__ = (Index("ix_equity_run_ts", "run_id", "ts"),)


class ErrorLog(Base):
    __tablename__ = "error_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[str | None] = mapped_column(String(64))
    mode: Mapped[str | None] = mapped_column(String(16))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), default="error")
    message: Mapped[str] = mapped_column(Text, nullable=False)
    detail: Mapped[str] = mapped_column(Text, default="")
    context: Mapped[dict] = mapped_column(JSONB, default=dict)

    __table_args__ = (
        Index("ix_errors_ts", "ts"),
        Index("ix_errors_source", "source"),
        Index("ix_errors_severity", "severity"),
        Index("ix_errors_run", "run_id"),
    )
