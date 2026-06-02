"""Public market-data proxy (REQ-002/008).

The chart can show any candle interval (1m/5m/15m/1h) independently of the running
strategy's decision interval, so the frontend fetches historical klines here. This
keeps Bitunix credentials server-side and lets us add a small cache later.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, Query

from app.core.logging import get_logger
from app.exchange.bitunix.rest import BitunixRest

router = APIRouter(prefix="/market", tags=["market"])

log = get_logger(__name__)

_ALLOWED_INTERVALS = {"1m", "5m", "15m", "30m", "1h", "2h", "4h", "1d"}


@router.get("/klines")
async def get_klines(
    symbol: str = Query(..., min_length=3),
    interval: str = Query("1m"),
    limit: int = Query(300, ge=1, le=1000),
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[dict]:
    """Return OHLC candles for a symbol/interval as chart-ready rows."""
    if interval not in _ALLOWED_INTERVALS:
        raise HTTPException(400, f"unsupported interval: {interval}")

    rest = BitunixRest()
    try:
        if start is not None or end is not None:
            bars = await rest.get_klines(
                symbol, interval, start_time=start, end_time=end
            )
        else:
            bars = await rest.get_klines(symbol, interval, limit)
    except Exception as exc:  # noqa: BLE001 - surface a clean error to the UI
        log.warning("klines_proxy_failed", symbol=symbol, error=str(exc))
        raise HTTPException(502, f"kline fetch failed: {exc}") from exc
    finally:
        await rest.close()

    return [
        {
            "t": int(b.open_time.timestamp()),
            "o": str(b.open),
            "h": str(b.high),
            "l": str(b.low),
            "c": str(b.close),
            "v": str(b.volume),
        }
        for b in bars
    ]
