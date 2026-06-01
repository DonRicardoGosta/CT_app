"""Live broker backed by the Bitunix REST API (REQ-002/003).

This implements the same :class:`Broker` interface as :class:`SimBroker`, so the
engine and strategies are unchanged in live mode. Order execution sends real
market orders; account/position state is read back from the exchange.

Note: a market order's exact fill price/fees arrive on the private order stream;
for synchronous engine bookkeeping we record the current mark as the fill price and
let the next ``account()`` refresh reconcile against exchange truth. Live trading is
guarded and dry-run is the default (REQ-003).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from app.core.logging import get_logger
from app.domain.interfaces import Broker
from app.domain.types import (
    AccountState,
    Fill,
    Order,
    OrderRequest,
    OrderStatus,
    Position,
    PositionSide,
)
from app.exchange.bitunix.rest import BitunixRest

log = get_logger(__name__)


def _dec(v: Any, d: str = "0") -> Decimal:
    try:
        return Decimal(str(v))
    except Exception:  # noqa: BLE001
        return Decimal(d)


class LiveBroker(Broker):
    """Routes orders to Bitunix and reflects exchange account state."""

    def __init__(self, rest: BitunixRest) -> None:
        self._rest = rest
        self._marks: dict[str, Decimal] = {}
        self._order_seq = 0

    async def set_mark(self, symbol: str, price: object) -> None:
        self._marks[symbol] = Decimal(str(price))

    async def submit(self, request: OrderRequest) -> Order:
        self._order_seq += 1
        ts = datetime.now(UTC)
        local_id = f"live-{self._order_seq}"
        try:
            if not request.reduce_only:
                await self._rest.set_leverage(request.symbol, request.leverage)
            resp = await self._rest.place_order(request)
            order_id = str(resp.get("orderId", local_id)) if isinstance(resp, dict) else local_id
            mark = self._marks.get(request.symbol, request.price or Decimal("0"))
            fill = Fill(
                order_id=order_id,
                symbol=request.symbol,
                side=request.side,
                position_side=request.position_side,
                qty=request.qty,
                price=mark,
                fee=Decimal("0"),
                ts=ts,
            )
            return Order(
                id=order_id,
                symbol=request.symbol,
                side=request.side,
                order_type=request.order_type,
                position_side=request.position_side,
                qty=request.qty,
                leverage=request.leverage,
                status=OrderStatus.FILLED,
                ts=ts,
                price=request.price,
                filled_qty=request.qty,
                avg_fill_price=mark,
                reduce_only=request.reduce_only,
                client_id=request.client_id,
                reason=request.reason,
                tag=request.tag,
                fills=[fill],
            )
        except Exception as exc:  # noqa: BLE001 - surface as a rejected order
            log.error("live_order_failed", error=str(exc), symbol=request.symbol)
            return Order(
                id=local_id,
                symbol=request.symbol,
                side=request.side,
                order_type=request.order_type,
                position_side=request.position_side,
                qty=request.qty,
                leverage=request.leverage,
                status=OrderStatus.REJECTED,
                ts=ts,
                reason=f"exchange error: {exc}",
                tag=request.tag,
            )

    async def account(self) -> AccountState:
        balance = Decimal("0")
        positions: dict[tuple[str, PositionSide], Position] = {}
        try:
            acct = await self._rest.get_account()
            if isinstance(acct, dict):
                balance = _dec(acct.get("available") or acct.get("balance") or acct.get("margin"))
            raw_positions = await self._rest.get_positions()
            for item in raw_positions or []:
                pos = self._parse_position(item)
                if pos is not None:
                    positions[(pos.symbol, pos.position_side)] = pos
        except Exception as exc:  # noqa: BLE001 - never crash the loop
            log.warning("live_account_fetch_failed", error=str(exc))
        return AccountState(ts=datetime.now(UTC), balance=balance, positions=positions)

    def _parse_position(self, item: dict[str, Any]) -> Position | None:
        try:
            qty = _dec(item.get("qty") or item.get("size") or item.get("positionAmt"))
            if qty == 0:
                return None
            side_raw = str(item.get("side") or item.get("positionSide") or "LONG").upper()
            side = PositionSide.LONG if "LONG" in side_raw or "BUY" in side_raw else PositionSide.SHORT
            entry = _dec(item.get("entryValue") or item.get("avgOpenPrice") or item.get("entryPrice"))
            leverage = int(_dec(item.get("leverage"), "1"))
            return Position(
                symbol=str(item.get("symbol")),
                position_side=side,
                qty=abs(qty),
                entry_price=entry,
                leverage=max(leverage, 1),
                committed_margin=_dec(item.get("margin")),
                mark_price=self._marks.get(str(item.get("symbol")), entry),
            )
        except Exception:  # noqa: BLE001
            return None
