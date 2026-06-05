"""Public market-data proxy (REQ-002/008).

The chart can show any candle interval (1m/5m/15m/1h) independently of the running
strategy's decision interval, so the frontend fetches historical klines here. This
keeps Bitunix credentials server-side and lets us add a small cache later.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, HTTPException, Query

from app.core.logging import get_logger
from app.exchange.bitunix.rest import BitunixRest

router = APIRouter(prefix="/market", tags=["market"])

log = get_logger(__name__)

_ALLOWED_INTERVALS = {"1m", "5m", "15m", "30m", "1h", "2h", "4h", "1d"}


def _dec(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _ticker_row(item: dict) -> dict | None:
    """Normalize one exchange ticker into ``{symbol, last, change_24h_pct}``.

    Bitunix payloads vary in field naming, so we probe a few common keys for the
    last price, the 24h open and (if present) a precomputed percent change.
    """
    symbol = item.get("symbol")
    if not symbol:
        return None
    last = _dec(item.get("lastPrice") or item.get("last") or item.get("close"))
    open_ = _dec(item.get("open") or item.get("open24h") or item.get("o"))

    pct = _dec(item.get("priceChangePercent") or item.get("changePercent"))
    if pct is None:
        raw_change = _dec(item.get("priceChange") or item.get("change"))
        if raw_change is not None and open_ not in (None, Decimal("0")):
            # Some payloads carry a fractional 24h change (e.g. 0.0123 = 1.23%).
            pct = raw_change * Decimal("100")
    if pct is None and last is not None and open_ not in (None, Decimal("0")):
        pct = (last - open_) / open_ * Decimal("100")  # type: ignore[operator]

    return {
        "symbol": str(symbol),
        "last": str(last) if last is not None else None,
        "change_24h_pct": float(pct) if pct is not None else None,
    }


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


@router.get("/tickers")
async def get_tickers(
    symbols: str | None = Query(
        None, description="Optional comma-separated symbols; all when omitted."
    ),
) -> list[dict]:
    """Return 24h ticker data (last price + 24h percent change) per symbol.

    The frontend uses this for the true 24h change shown next to a coin's price,
    instead of deriving it from the loaded candle window.
    """
    requested = [s.strip() for s in symbols.split(",")] if symbols else None
    requested = [s for s in (requested or []) if s] or None

    rest = BitunixRest()
    try:
        tickers = await rest.get_tickers(requested)
    except Exception as exc:  # noqa: BLE001 - surface a clean error to the UI
        log.warning("tickers_proxy_failed", error=str(exc))
        raise HTTPException(502, f"ticker fetch failed: {exc}") from exc
    finally:
        await rest.close()

    rows = [_ticker_row(t) for t in tickers]
    return [r for r in rows if r is not None]
