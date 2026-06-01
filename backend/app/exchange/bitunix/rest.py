"""Bitunix futures REST client (REQ-002).

Async, signed requests via httpx. Public endpoints (market data) are unsigned;
private endpoints (orders, account) are signed with the configured API key.
A small token-bucket limiter keeps us under the documented rate limits.
"""

from __future__ import annotations

import asyncio
import time
from decimal import Decimal
from typing import Any

import httpx

from app.core.logging import get_logger
from app.domain.types import Bar, Instrument, OrderRequest, PositionSide, Side
from app.exchange.bitunix.models import parse_instrument, parse_kline
from app.exchange.bitunix.signing import rest_headers

log = get_logger(__name__)

BASE_URL = "https://fapi.bitunix.com"


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

    async def get_klines(
        self,
        symbol: str,
        interval: str = "1m",
        limit: int = 200,
        *,
        start_time: object | None = None,
        end_time: object | None = None,
    ) -> list[Bar]:
        """Fetch klines; use ``start_time``/``end_time`` (aware datetime) for backtests."""
        if start_time is not None or end_time is not None:
            return await self._get_klines_range(symbol, interval, start_time, end_time)

        params: dict[str, object] = {
            "symbol": symbol,
            "interval": interval,
            "limit": min(limit, 200),
        }
        data = await self._request("GET", "/api/v1/futures/market/kline", params=params)
        bars = [parse_kline(symbol, interval, item) for item in (data or [])]
        bars.sort(key=lambda b: b.open_time)
        return bars

    async def _get_klines_range(
        self,
        symbol: str,
        interval: str,
        start_time: object | None,
        end_time: object | None,
    ) -> list[Bar]:
        from datetime import datetime, timedelta

        def _ms(dt: datetime) -> int:
            return int(dt.timestamp() * 1000)

        end_dt = end_time if isinstance(end_time, datetime) else datetime.now().astimezone()
        start_dt = start_time if isinstance(start_time, datetime) else None

        all_bars: list[Bar] = []
        cursor_ms: int | None = _ms(start_dt) if start_dt else None
        end_ms = _ms(end_dt)

        for _ in range(500):  # safety cap
            params: dict[str, object] = {
                "symbol": symbol,
                "interval": interval,
                "limit": 200,
                "endTime": end_ms,
            }
            if cursor_ms is not None:
                params["startTime"] = cursor_ms
            data = await self._request(
                "GET", "/api/v1/futures/market/kline", params=params
            )
            batch = [parse_kline(symbol, interval, item) for item in (data or [])]
            if not batch:
                break
            batch.sort(key=lambda b: b.open_time)
            all_bars.extend(batch)
            if len(batch) < 200:
                break
            last_ms = _ms(batch[-1].open_time)
            if cursor_ms is not None and last_ms <= cursor_ms:
                break
            cursor_ms = last_ms + 1
            if cursor_ms >= end_ms:
                break

        all_bars.sort(key=lambda b: b.open_time)
        if start_dt:
            all_bars = [b for b in all_bars if b.open_time >= start_dt]
        all_bars = [b for b in all_bars if b.open_time <= end_dt]
        return all_bars

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
