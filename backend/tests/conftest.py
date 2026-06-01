"""Shared test fixtures and synthetic data helpers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.domain.types import Bar, Instrument


@pytest.fixture
def btc_instrument() -> Instrument:
    return Instrument(
        symbol="BTCUSDT",
        base="BTC",
        quote="USDT",
        min_trade_volume=Decimal("0.0001"),
        base_precision=4,
        quote_precision=2,
        min_leverage=1,
        max_leverage=50,
        default_leverage=10,
    )


@pytest.fixture
def instruments(btc_instrument: Instrument) -> dict[str, Instrument]:
    return {"BTCUSDT": btc_instrument}


def make_bars(symbol: str = "BTCUSDT", n: int = 120) -> list[Bar]:
    """Deterministic up-then-down price path that triggers laddered trades."""
    t0 = datetime(2024, 1, 1, tzinfo=UTC)
    bars: list[Bar] = []
    price = Decimal("100")
    for i in range(n):
        # Up for the first half, down for the second half, small oscillation.
        step = Decimal("1") if i < n // 2 else Decimal("-1")
        wobble = Decimal("0.3") if i % 2 == 0 else Decimal("-0.2")
        price = price + step + wobble
        if price < 1:
            price = Decimal("1")
        bars.append(
            Bar(
                symbol=symbol,
                interval="1m",
                open_time=t0 + timedelta(minutes=i),
                open=price,
                high=price,
                low=price,
                close=price,
                volume=Decimal("10"),
            )
        )
    return bars


@pytest.fixture
def bars() -> list[Bar]:
    return make_bars()
