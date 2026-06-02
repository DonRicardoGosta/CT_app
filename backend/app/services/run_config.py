"""Run configuration: the single object that fully describes a run in any mode.

The same config shape works for live, dry-run and backtest; only ``mode`` and the
backtest-specific fields differ. This is what gets sent over the control topic and
stored on the ``runs`` row.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator

from app.core.datetime_util import ensure_utc
from app.domain.types import Mode
from app.risk.config import RiskParams


class RunConfig(BaseModel):
    """Everything needed to assemble and start an engine."""

    run_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    mode: Mode
    strategy: str
    params: dict = Field(default_factory=dict)
    risk: RiskParams = Field(default_factory=RiskParams)

    # Symbols to trade. Empty -> let the strategy auto-select (live) or use all
    # symbols present in the historical data (backtest).
    symbols: list[str] = Field(default_factory=list)
    interval: str = "1m"
    initial_capital: Decimal = Decimal("1000")

    # Live/dry-run only: which stored API key to use (live order routing).
    api_key_id: int | None = None

    # Backtest only.
    backtest_start: datetime | None = None
    backtest_end: datetime | None = None
    backtest_limit: int = 1000

    model_config = {"use_enum_values": True}

    @field_validator("backtest_start", "backtest_end", mode="after")
    @classmethod
    def _utc_backtest_dates(cls, v: datetime | None) -> datetime | None:
        return ensure_utc(v)
