"""Strategy registry.

New strategies become available simply by defining a subclass decorated with
``@register_strategy(...)`` — no engine changes required (REQ-005).
"""

from __future__ import annotations

from typing import TypeVar

from pydantic import BaseModel

from app.strategies.base import Strategy

_REGISTRY: dict[str, type[Strategy]] = {}

T = TypeVar("T", bound=Strategy)


def register_strategy(name: str):
    """Class decorator registering a strategy under ``name``."""

    def _wrap(cls: type[T]) -> type[T]:
        cls.name = name
        if name in _REGISTRY and _REGISTRY[name] is not cls:
            raise ValueError(f"strategy '{name}' already registered")
        _REGISTRY[name] = cls
        return cls

    return _wrap


def get_strategy_class(name: str) -> type[Strategy]:
    try:
        return _REGISTRY[name]
    except KeyError as exc:
        raise KeyError(f"unknown strategy '{name}'") from exc


def create_strategy(name: str, params: dict | None = None) -> Strategy:
    """Instantiate a strategy by name with raw parameter dict (validated)."""
    cls = get_strategy_class(name)
    model: BaseModel = cls.Params(**(params or {}))
    return cls(model)


def available_strategies() -> dict[str, dict]:
    """Return ``{name: json_schema}`` for every registered strategy (for the UI)."""
    return {name: cls.params_json_schema() for name, cls in sorted(_REGISTRY.items())}
