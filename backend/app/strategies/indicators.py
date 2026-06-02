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


def sma(values: list[Decimal], period: int) -> Decimal | None:
    """Simple moving average of the last ``period`` values; ``None`` if too short."""
    if period <= 0 or len(values) < period:
        return None
    window = values[-period:]
    return sum(window, Decimal("0")) / Decimal(period)


def rsi(values: list[Decimal], period: int = 14) -> Decimal | None:
    """Wilder's RSI over the last ``period`` close-to-close changes.

    Returns a value in ``[0, 100]`` or ``None`` when there is not enough data.
    Uses Wilder smoothing seeded by the first ``period`` average gains/losses.
    """
    if period <= 0 or len(values) < period + 1:
        return None
    gains: list[Decimal] = []
    losses: list[Decimal] = []
    for i in range(1, len(values)):
        change = values[i] - values[i - 1]
        gains.append(change if change > 0 else Decimal("0"))
        losses.append(-change if change < 0 else Decimal("0"))

    # Seed with the simple average of the first ``period`` changes.
    avg_gain = sum(gains[:period], Decimal("0")) / Decimal(period)
    avg_loss = sum(losses[:period], Decimal("0")) / Decimal(period)
    # Wilder smoothing for the remaining changes.
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * Decimal(period - 1) + gains[i]) / Decimal(period)
        avg_loss = (avg_loss * Decimal(period - 1) + losses[i]) / Decimal(period)

    if avg_loss == 0:
        return Decimal("100") if avg_gain > 0 else Decimal("50")
    rs = avg_gain / avg_loss
    return Decimal("100") - (Decimal("100") / (Decimal("1") + rs))
