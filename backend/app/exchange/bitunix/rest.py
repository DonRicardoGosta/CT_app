"""Bitunix futures REST client (REQ-002).

Async, signed requests via httpx. Public endpoints (market data) are unsigned;
private endpoints (orders, account) are signed with the configured API key.
A small token-bucket limiter keeps us under the documented rate limits.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx

from app.core.logging import get_logger
from app.domain.types import (
    Bar,
    Instrument,
    OrderRequest,
    PositionSide,
    Side,
)
from app.exchange.bitunix.models import parse_instrument, parse_kline
from app.exchange.bitunix.signing import rest_headers

log = get_logger(__name__)

BASE_URL = "https://fapi.bitunix.com"

_INTERVAL_SECONDS = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
}


class RateLimiter:
    """Simple async token bucket (requests per second)."""

    def __init__(self, rate: int = 8) -> None:
        self._rate = rate
        self._allowance = float(rate)
        self._last = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            self._allowance = min(
                self._rate, self._allowance + (now - self._last) * self._rate
            )
            self._last = now
            if self._allowance < 1.0:
                await asyncio.sleep((1.0 - self._allowance) / self._rate)
                self._allowance = 0.0
            else:
                self._allowance -= 1.0


class BitunixRestError(RuntimeError):
    """Raised when Bitunix returns a non-zero error code."""


class BitunixRest:
    """Bitunix futures REST API."""

    def __init__(
        self,
        api_key: str = "",
        secret_key: str = "",
        base_url: str = BASE_URL,
        timeout: float = 10.0,
    ) -> None:
        self.api_key = api_key
        self.secret_key = secret_key
        self.base_url = base_url
        self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout)
        self._limiter = RateLimiter()

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> BitunixRest:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    # -- low level ---------------------------------------------------------- #
    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        signed: bool = False,
    ) -> Any:
        await self._limiter.acquire()
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if signed:
            if not self.api_key or not self.secret_key:
                raise BitunixRestError("API credentials required for signed request")
            headers = rest_headers(self.api_key, self.secret_key, params, body)
        resp = await self._client.request(
            method, path, params=params, json=body, headers=headers
        )
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict) and data.get("code") not in (0, "0", None):
            raise BitunixRestError(f"{data.get('code')}: {data.get('msg', data)}")
        return data.get("data") if isinstance(data, dict) else data

    # -- public ------------------------------------------------------------- #
    async def get_trading_pairs(self, symbols: list[str] | None = None) -> dict[str, Instrument]:
        params = {"symbols": ",".join(symbols)} if symbols else None
        data = await self._request(
            "GET", "/api/v1/futures/market/trading_pairs", params=params
        )
        out: dict[str, Instrument] = {}
        for item in data or []:
            inst = parse_instrument(item)
            out[inst.symbol] = inst
        return out

    async def get_tickers(self, symbols: list[str] | None = None) -> list[dict[str, Any]]:
        """Return 24h ticker data; includes ``quoteVol`` used for ranking."""
        params = {"symbols": ",".join(symbols)} if symbols else None
        data = await self._request(
            "GET", "/api/v1/futures/market/tickers", params=params
        )
        return list(data or [])

    async def get_volume_ranked_symbols(
        self, symbols: list[str] | None = None, limit: int = 500
    ) -> list[str]:
        """Return symbols sorted by descending 24h quote volume."""
        tickers = await self.get_tickers(symbols)

        def quote_vol(item: dict[str, Any]) -> Decimal:
            try:
                return Decimal(str(item.get("quoteVol", "0")))
            except Exception:  # noqa: BLE001 - bad exchange payload -> bottom
                return Decimal("0")

        ranked = sorted(
            (t for t in tickers if t.get("symbol")),
            key=quote_vol,
            reverse=True,
        )
        return [str(t["symbol"]) for t in ranked[:limit]]

    async def get_klines(
        self,
        symbol: str,
        interval: str = "1m",
        limit: int = 200,
        *,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> list[Bar]:
        """Fetch klines. Pass ``start_time``/``end_time`` for a historical range."""
        if start_time is not None or end_time is not None:
            return await self._get_klines_range(symbol, interval, start_time, end_time)
        params = {"symbol": symbol, "interval": interval, "limit": min(limit, 200)}
        data = await self._request("GET", "/api/v1/futures/market/kline", params=params)
        bars = [parse_kline(symbol, interval, item) for item in (data or [])]
        bars.sort(key=lambda b: b.open_time)
        return bars

    async def get_recent_klines(
        self, symbol: str, interval: str = "1m", count: int = 200
    ) -> list[Bar]:
        """Fetch the most recent ``count`` closed bars.

        Illiquid coins skip minutes (no trades -> no candle), so a window sized to
        exactly ``count`` periods returns fewer bars than requested. We grow the
        lookback window and re-query until we have ``count`` bars or the exchange
        clearly has no more history.
        """
        if count <= 200:
            bars = await self.get_klines(symbol, interval, count)
            if len(bars) >= count:
                return bars[-count:]
            # Short result (gaps / fresh listing): fall through to a wider range query.

        seconds = _INTERVAL_SECONDS.get(interval, 60)
        bars: list[Bar] = []
        # Start with a 2x margin, then double the window each retry (2x, 4x, 8x...).
        span = max(count * 2, count + 50)
        prev_len = -1
        for _ in range(6):
            start = datetime.now(UTC) - timedelta(seconds=seconds * (span + 5))
            bars = await self._get_klines_range(symbol, interval, start, None)
            if len(bars) >= count:
                break
            # No progress despite a wider window -> exchange has no more history.
            if len(bars) == prev_len:
                break
            prev_len = len(bars)
            span *= 2
        return bars[-count:]

    async def _get_klines_range(
        self,
        symbol: str,
        interval: str,
        start_time: datetime | None,
        end_time: datetime | None,
    ) -> list[Bar]:
        """Paginate klines backward (Bitunix returns candles before ``endTime``)."""

        def _utc(dt: datetime) -> datetime:
            return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)

        def _ms(dt: datetime) -> int:
            return int(_utc(dt).timestamp() * 1000)

        end_dt = _utc(end_time) if end_time else datetime.now(UTC)
        start_dt = _utc(start_time) if start_time else None
        start_ms = _ms(start_dt) if start_dt else None
        page_end_ms = _ms(end_dt)
        seen: set[int] = set()
        all_bars: list[Bar] = []

        for _ in range(500):  # safety cap
            params: dict[str, Any] = {
                "symbol": symbol,
                "interval": interval,
                "limit": 200,
                "endTime": page_end_ms,
            }
            if start_ms is not None:
                params["startTime"] = start_ms
            data = await self._request(
                "GET", "/api/v1/futures/market/kline", params=params
            )
            batch = [parse_kline(symbol, interval, item) for item in (data or [])]
            if not batch:
                break
            batch.sort(key=lambda b: b.open_time)
            for bar in batch:
                key = _ms(bar.open_time)
                if key not in seen:
                    seen.add(key)
                    all_bars.append(bar)
            oldest_ms = min(_ms(b.open_time) for b in batch)
            if (start_ms is not None and oldest_ms <= start_ms) or len(batch) < 200:
                break
            next_end = oldest_ms - 1
            if next_end >= page_end_ms:
                break
            page_end_ms = next_end
            if start_ms is not None and page_end_ms < start_ms:
                break

        all_bars.sort(key=lambda b: b.open_time)
        if start_dt:
            all_bars = [b for b in all_bars if b.open_time >= start_dt]
        return [b for b in all_bars if b.open_time <= end_dt]

    # -- private ------------------------------------------------------------ #
    async def get_account(self) -> Any:
        return await self._request(
            "GET", "/api/v1/futures/account", params={"marginCoin": "USDT"}, signed=True
        )

    async def get_positions(self, symbol: str | None = None) -> Any:
        params = {"symbol": symbol} if symbol else None
        return await self._request(
            "GET", "/api/v1/futures/position/get_pending_positions", params=params, signed=True
        )

    async def place_order(self, request: OrderRequest) -> Any:
        body: dict[str, Any] = {
            "symbol": request.symbol,
            "side": "BUY" if request.side is Side.BUY else "SELL",
            "qty": str(request.qty),
            "orderType": "MARKET" if request.order_type.value == "market" else "LIMIT",
            "tradeSide": "OPEN" if not request.reduce_only else "CLOSE",
            "positionMode": "HEDGE",
            "effect": "GTC",
            "reduceOnly": request.reduce_only,
        }
        if request.price is not None:
            body["price"] = str(request.price)
        if request.client_id:
            body["clientId"] = request.client_id
        # Hedge mode requires the position direction.
        body["positionSide"] = "LONG" if request.position_side is PositionSide.LONG else "SHORT"
        return await self._request(
            "POST", "/api/v1/futures/trade/place_order", body=body, signed=True
        )

    async def set_leverage(self, symbol: str, leverage: int) -> Any:
        body = {"symbol": symbol, "leverage": leverage, "marginCoin": "USDT"}
        return await self._request(
            "POST", "/api/v1/futures/account/change_leverage", body=body, signed=True
        )

    async def place_tpsl_order(
        self,
        *,
        symbol: str,
        position_id: str,
        sl_price: str | None = None,
        sl_qty: str | None = None,
        tp_price: str | None = None,
        tp_qty: str | None = None,
    ) -> Any:
        """Place a TP and/or SL trigger order for an open position."""
        body: dict[str, Any] = {"symbol": symbol, "positionId": position_id}
        if sl_price is not None:
            body["slPrice"] = sl_price
            body["slStopType"] = "LAST_PRICE"
            body["slOrderType"] = "MARKET"
        if sl_qty is not None:
            body["slQty"] = sl_qty
        if tp_price is not None:
            body["tpPrice"] = tp_price
            body["tpStopType"] = "LAST_PRICE"
            body["tpOrderType"] = "MARKET"
        if tp_qty is not None:
            body["tpQty"] = tp_qty
        return await self._request(
            "POST", "/api/v1/futures/tpsl/place_order", body=body, signed=True
        )

    async def get_pending_orders(
        self, symbol: str | None = None, order_id: str | None = None
    ) -> Any:
        """Return open (NEW / PART_FILLED) trade orders."""
        params: dict[str, Any] = {}
        if symbol:
            params["symbol"] = symbol
        if order_id:
            params["orderId"] = order_id
        return await self._request(
            "GET", "/api/v1/futures/trade/get_pending_orders", params=params or None, signed=True
        )

    async def get_pending_tpsl_orders(
        self, symbol: str | None = None, position_id: str | None = None
    ) -> Any:
        """Return resting TP/SL orders for the account (optionally per position)."""
        params: dict[str, Any] = {}
        if symbol:
            params["symbol"] = symbol
        if position_id:
            params["positionId"] = position_id
        return await self._request(
            "GET", "/api/v1/futures/tpsl/get_pending_orders", params=params or None, signed=True
        )

    async def modify_position_tpsl(
        self,
        *,
        symbol: str,
        position_id: str,
        sl_price: str | None = None,
        tp_price: str | None = None,
    ) -> Any:
        body: dict[str, Any] = {"symbol": symbol, "positionId": position_id}
        if sl_price is not None:
            body["slPrice"] = sl_price
            body["slStopType"] = "LAST_PRICE"
        if tp_price is not None:
            body["tpPrice"] = tp_price
            body["tpStopType"] = "LAST_PRICE"
        return await self._request(
            "POST", "/api/v1/futures/tpsl/position/modify_order", body=body, signed=True
        )

    async def test_credentials(self) -> bool:
        """Return True if the credentials authenticate (used by Settings UI)."""
        try:
            await self.get_account()
            return True
        except (BitunixRestError, httpx.HTTPError) as exc:
            log.info("credential_test_failed", error=str(exc))
            return False


def to_decimal(value: Any) -> Decimal:
    return Decimal(str(value))
