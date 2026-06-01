"""Parsing helpers between Bitunix payloads and domain types."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from app.domain.types import Bar, Instrument


def _dec(value: Any, default: str = "0") -> Decimal:
    try:
        return Decimal(str(value))
    except Exception:  # noqa: BLE001
        return Decimal(default)


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:  # noqa: BLE001
        return default


def parse_instrument(item: dict[str, Any]) -> Instrument:
    """Map a ``trading_pairs`` entry to an :class:`Instrument`."""
    return Instrument(
        symbol=item["symbol"],
        base=item.get("base", ""),
        quote=item.get("quote", "USDT"),
        min_trade_volume=_dec(item.get("minTradeVolume"), "0"),
        base_precision=_int(item.get("basePrecision"), 4),
        quote_precision=_int(item.get("quotePrecision"), 2),
        min_leverage=_int(item.get("minLeverage"), 1),
        max_leverage=_int(item.get("maxLeverage"), 20),
        default_leverage=_int(item.get("defaultLeverage"), 1),
    )


def parse_kline(symbol: str, interval: str, item: dict[str, Any]) -> Bar:
    """Map a kline entry to a :class:`Bar` (defensive about field names)."""
    ts_raw = item.get("time") or item.get("ts") or item.get("t") or 0
    ts_ms = _int(ts_raw)
    open_time = datetime.fromtimestamp(ts_ms / 1000, tz=UTC)
    return Bar(
        symbol=symbol,
        interval=interval,
        open_time=open_time,
        open=_dec(item.get("open") or item.get("o")),
        high=_dec(item.get("high") or item.get("h")),
        low=_dec(item.get("low") or item.get("l")),
        close=_dec(item.get("close") or item.get("c")),
        volume=_dec(item.get("baseVol") or item.get("volume") or item.get("v")),
    )
