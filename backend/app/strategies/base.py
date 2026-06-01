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

    @abc.abstractmethod
    def on_event(self, context: StrategyContext) -> list[TradeIntent]:
        """Return the intents to act on for this market event (pure, no I/O)."""
