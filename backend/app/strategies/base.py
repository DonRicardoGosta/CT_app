"""Strategy base class and context.

Strategies are deterministic: given the same context they emit the same intents.
They must not call ``datetime.now()``, sleep, hit the network or the database —
all inputs arrive through :class:`StrategyContext` (REQ-001, REQ-003).
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel

from app.domain.market import MarketState
from app.domain.types import (
    AccountState,
    Instrument,
    MarketEvent,
    TradeIntent,
)


@dataclass(slots=True)
class StrategyContext:
    """Everything a strategy may read when deciding."""

    event: MarketEvent
    now: datetime
    account: AccountState
    instruments: dict[str, Instrument]
    market: MarketState
    interval: str = "1m"


class Strategy(abc.ABC):
    """Base class for all strategies.

    Subclasses set :attr:`name` and :attr:`Params` and implement :meth:`on_event`.
    """

    #: Unique strategy identifier used by the registry and the UI.
    name: str = "base"
    #: Pydantic model describing the strategy's tunable parameters.
    Params: type[BaseModel] = BaseModel

    def __init__(self, params: BaseModel | None = None) -> None:
        self.params = params if params is not None else self.Params()

    @classmethod
    def params_json_schema(cls) -> dict:
        """Return the JSON schema for this strategy's parameters (for the frontend)."""
        return cls.Params.model_json_schema()

    def desired_symbols(self, instruments: dict[str, Instrument]) -> list[str]:
        """Symbols the strategy wants to trade (for live subscription).

        Default: none — the engine/backtest supplies whatever it has. Strategies
        that auto-select coins (REQ-006) override this.
        """
        return []

    async def on_start(self, context: StrategyContext) -> None:
        """Optional hook before the first event."""
        return None

    def selection_snapshot(self, context: StrategyContext) -> dict | None:
        """Optional dynamic coin selection state for the UI.

        Return ``{"selected": [...], "scanning": [...], "target": int}`` so the
        frontend can show a "Selecting coins (N/target)" state until the strategy
        has locked in its tradeable set. ``None`` means the strategy does not do
        dynamic selection (the engine falls back to ``desired_symbols``).
        """
        return None

    def position_levels(
        self, symbol: str, side: object, entry_price: object, leverage: int
    ) -> dict | None:
        """Optional chart levels for a symbol: take-profits and stops.

        Return ``{"take_profits": [Decimal, ...], "stops": [Decimal, ...]}`` as
        price levels, or ``None`` to let the engine fall back to a single TP/SL.
        """
        return None

    def drain_scan_logs(self) -> list[dict]:
        """Optional scan diagnostics for the Logs UI.

        Strategies queue human-readable messages here (e.g. why a coin failed a
        check). The engine emits them with source ``strategy`` after each bar.
        """
        return []

    def scan_diagnostics(self, context: StrategyContext) -> None:
        """Optional hook on every market event (ticks and bars).

        Use for live/dry feedback before the first closed candle exists. Heavy
        work should stay in :meth:`on_event` on closed bars only.
        """
        return None

    def warmup_bars(self) -> int:
        """How many historical bars per symbol to preload before going live.

        Live/dry runs otherwise build history one candle at a time, so a 200-EMA
        strategy would sit idle for hours. Return the number of closed bars the
        strategy needs to start evaluating; 0 disables preloading.
        """
        return 0

    @abc.abstractmethod
    def on_event(self, context: StrategyContext) -> list[TradeIntent]:
        """Return the intents to act on for this market event (pure, no I/O)."""
