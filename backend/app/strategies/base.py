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
    PositionSide,
    ProtectionPlan,
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
    #: When True (live runs), TP/SL are exchange orders — strategies must not
    #: emit close/reduce intents from price checks against those levels.
    exchange_protections: bool = False


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

    def protection_plan(
        self,
        symbol: str,
        side: object,
        entry_price: object,
        position_qty: object,
        instrument: Instrument,
    ) -> ProtectionPlan | None:
        """Optional TP/SL ladder for exchange placement after entry (live only)."""
        return None

    def compute_live_stop(
        self,
        side: object,
        entry_price: object,
        best_price: object,
        tps_filled: int,
    ) -> object | None:
        """Optional moving stop price for live exchange management.

        Given how many take-profit legs have filled and the best price reached,
        return the stop price the exchange SL should be moved to (e.g. breakeven
        then trailing), or ``None`` if the strategy does not move its live stop.
        The engine only ever applies this to positions the bot itself opened.
        """
        return None

    def on_open_outcome(
        self,
        symbol: str,
        position_side: PositionSide,
        *,
        success: bool,
        first_entry: bool,
    ) -> None:
        """Hook after the engine attempts a first ladder open (``entry_1`` tag)."""
        return None

    def is_full(self, account: AccountState) -> bool:
        """True when the strategy will not accept another open position symbol."""
        return False

    def release_symbol(self, symbol: str, account: AccountState) -> bool:
        """Drop a symbol from the active watchlist when it has no open position."""
        return False

    def next_scan_candidate(self, account: AccountState) -> str | None:
        """Next universe symbol to evaluate for filling a free watchlist slot."""
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
