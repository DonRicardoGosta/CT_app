"""Pluggable strategy framework (REQ-005).

A strategy is a pure decision function over market state and portfolio: it returns
:class:`TradeIntent` objects and performs no I/O. Strategies register themselves via
``@register_strategy`` and expose a pydantic parameter model so the frontend can
auto-generate their configuration form (JSON schema).
"""

from app.strategies import autoscan_ladder  # noqa: F401 - register built-in strategy
from app.strategies.base import Strategy, StrategyContext
from app.strategies.registry import (
    available_strategies,
    create_strategy,
    get_strategy_class,
    register_strategy,
)

__all__ = [
    "Strategy",
    "StrategyContext",
    "register_strategy",
    "create_strategy",
    "get_strategy_class",
    "available_strategies",
]
