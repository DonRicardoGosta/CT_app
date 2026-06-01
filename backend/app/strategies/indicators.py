"""Small, dependency-free, deterministic indicator helpers (Decimal based)."""

from __future__ import annotations

from decimal import Decimal


def ema(values: list[Decimal], period: int) -> Decimal | None:
    """Exponential moving average of the last values; ``None`` if not enough data."""
    if period <= 0 or len(values) < period:
        return None
    k = Decimal(2) / Decimal(period + 1)
    e = values[0]
    for v in values[1:]:
        e = v * k + e * (Decimal(1) - k)
    return e


def atr_like(values: list[Decimal], period: int) -> Decimal | None:
    """A simple volatility proxy: mean absolute close-to-close change over ``period``."""
    if len(values) < period + 1:
        return None
    window = values[-(period + 1) :]
    diffs = [abs(window[i] - window[i - 1]) for i in range(1, len(window))]
    return sum(diffs, Decimal("0")) / Decimal(len(diffs))
