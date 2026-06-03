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

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from app.core.logging import get_logger
from app.domain.interfaces import Broker
from app.domain.types import (
    AccountState,
    Fill,
    Instrument,
    Order,
    OrderRequest,
    OrderStatus,
    Position,
    PositionSide,
    ProtectionPlan,
    TakeProfitLeg,
)
from app.exchange.bitunix.rest import BitunixRest
from app.risk.sizer import _round_qty

log = get_logger(__name__)


def _dec(v: Any, d: str = "0") -> Decimal:
    try:
        return Decimal(str(v))
    except Exception:  # noqa: BLE001
        return Decimal(d)


class LiveBroker(Broker):
    """Routes orders to Bitunix and reflects exchange account state."""

    #: How long to wait for the exchange to reflect a freshly opened position.
    _CONFIRM_ATTEMPTS = 8
    _CONFIRM_DELAY_S = 0.4

    def __init__(self, rest: BitunixRest) -> None:
        self._rest = rest
        self._marks: dict[str, Decimal] = {}
        self._order_seq = 0
        self._tpsl_ids: dict[tuple[str, PositionSide], list[str]] = {}

    async def set_mark(self, symbol: str, price: object) -> None:
        self._marks[symbol] = Decimal(str(price))

    async def _position_snapshot(
        self, symbol: str, position_side: PositionSide
    ) -> tuple[str | None, Decimal, Decimal]:
        """Return ``(positionId, qty, entry_price)`` for the hedge leg, or zeros."""
        want_long = position_side is PositionSide.LONG
        raw = await self._rest.get_positions(symbol)
        items = raw if isinstance(raw, list) else (raw or {}).get("list") or []
        if not isinstance(items, list):
            items = [items] if items else []
        for item in items:
            if not isinstance(item, dict):
                continue
            if str(item.get("symbol")) != symbol:
                continue
            qty = _dec(item.get("qty") or item.get("size") or item.get("positionAmt"))
            if qty == 0:
                continue
            side_raw = str(item.get("side") or item.get("positionSide") or "").upper()
            is_long = "LONG" in side_raw or ("BUY" in side_raw and "SHORT" not in side_raw)
            if is_long != want_long:
                continue
            pid = item.get("positionId") or item.get("position_id") or item.get("id")
            entry = _dec(
                item.get("avgOpenPrice") or item.get("entryPrice") or item.get("entryValue")
            )
            return (str(pid) if pid is not None else None, abs(qty), entry)
        return None, Decimal("0"), Decimal("0")

    def _normalize_tp_legs(
        self, plan: ProtectionPlan, total_qty: Decimal, instrument: Instrument
    ) -> list[tuple[Decimal, Decimal]]:
        """Round and merge TP legs so every placed leg meets the exchange minimum.

        Sub-minimum legs are folded into the next leg; the final remainder is
        merged into the previous leg. Returns ``[(price, qty), ...]``.
        """
        min_qty = instrument.min_trade_volume
        prec = instrument.base_precision
        legs: list[tuple[Decimal, Decimal]] = []
        carry = Decimal("0")
        placed_total = Decimal("0")
        for leg in plan.take_profits:
            if leg.qty <= 0:
                continue
            qty = _round_qty(leg.qty + carry, prec)
            remaining = total_qty - placed_total
            qty = min(qty, remaining)
            if qty < min_qty:
                carry = leg.qty + carry
                continue
            legs.append((leg.price, qty))
            placed_total += qty
            carry = Decimal("0")
        # Fold only sub-minimum rounding dust into the last leg (never a real
        # intentionally-uncovered remainder — strategies may keep a runner).
        leftover = _round_qty(total_qty - placed_total, prec)
        if 0 < leftover < min_qty and legs:
            price, qty = legs[-1]
            legs[-1] = (price, qty + leftover)
        return legs

    async def place_exchange_protections(
        self,
        *,
        symbol: str,
        position_side: PositionSide,
        plan: ProtectionPlan,
        instrument: Instrument,
        skip_sl: bool = False,
        take_profits: tuple[TakeProfitLeg, ...] | None = None,
    ) -> dict[str, Any]:
        """Register stop-loss and scaled take-profits on Bitunix after entry.

        Each leg is placed independently so one failure does not skip the rest.
        Returns a result describing what landed, so the caller can warn the user.
        """
        tp_legs = take_profits if take_profits is not None else plan.take_profits
        position_id, pos_qty = await self._resolve_position(symbol, position_side)
        if not position_id:
            log.error("tpsl_no_position", symbol=symbol, side=position_side.value)
            return {
                "position_id": None,
                "sl_placed": skip_sl,
                "tp_placed": 0,
                "tp_expected": len(tp_legs),
                "error": "no position to attach TP/SL to",
            }

        sl_qty = _round_qty(pos_qty, instrument.base_precision)
        if sl_qty <= 0:
            sl_qty = pos_qty
        placed: list[str] = []
        sl_placed = skip_sl

        if not skip_sl:
            try:
                sl_resp = await self._rest.place_tpsl_order(
                    symbol=symbol,
                    position_id=position_id,
                    sl_price=str(plan.stop_price),
                    sl_qty=str(sl_qty),
                )
                sl_placed = True
                if isinstance(sl_resp, dict) and sl_resp.get("orderId"):
                    placed.append(str(sl_resp["orderId"]))
            except Exception as exc:  # noqa: BLE001 - keep going so TPs still get placed
                log.error(
                    "exchange_sl_failed",
                    error=str(exc),
                    symbol=symbol,
                    side=position_side.value,
                )

        remainder_plan = ProtectionPlan(stop_price=plan.stop_price, take_profits=tp_legs)
        legs = self._normalize_tp_legs(remainder_plan, pos_qty, instrument)
        tp_placed = 0
        for price, qty in legs:
            try:
                tp_resp = await self._rest.place_tpsl_order(
                    symbol=symbol,
                    position_id=position_id,
                    tp_price=str(price),
                    tp_qty=str(qty),
                )
                tp_placed += 1
                if isinstance(tp_resp, dict) and tp_resp.get("orderId"):
                    placed.append(str(tp_resp["orderId"]))
            except Exception as exc:  # noqa: BLE001 - one bad leg must not skip others
                log.error(
                    "exchange_tp_failed",
                    error=str(exc),
                    symbol=symbol,
                    side=position_side.value,
                    tp_price=str(price),
                    tp_qty=str(qty),
                )

        self._tpsl_ids[(symbol, position_side)] = placed

        # Verify what actually rests on the exchange (source of truth).
        verified = await self._verify_tpsl(symbol, position_id)
        log.info(
            "exchange_tpsl_placed",
            symbol=symbol,
            side=position_side.value,
            position_id=position_id,
            sl=str(plan.stop_price),
            tp_attempted=len(legs),
            tp_placed=tp_placed,
            verified_count=verified,
            order_ids=placed,
        )
        return {
            "position_id": position_id,
            "sl_placed": sl_placed,
            "tp_placed": tp_placed,
            "tp_expected": len(legs),
            "verified_count": verified,
            "error": None,
        }

    async def _verify_tpsl(self, symbol: str, position_id: str) -> int:
        """Return how many TP/SL orders currently rest for the position."""
        try:
            raw = await self._rest.get_pending_tpsl_orders(symbol, position_id)
        except Exception as exc:  # noqa: BLE001 - verification is best-effort
            log.warning("exchange_tpsl_verify_failed", error=str(exc), symbol=symbol)
            return -1
        items = raw if isinstance(raw, list) else (raw or {}).get("list") or []
        if not isinstance(items, list):
            items = [items] if items else []
        return len(items)

    async def _resolve_position(
        self, symbol: str, position_side: PositionSide
    ) -> tuple[str | None, Decimal]:
        """Return ``(positionId, qty)`` for the hedge leg, polling briefly after fill."""
        for _ in range(self._CONFIRM_ATTEMPTS):
            pid, qty, _entry = await self._position_snapshot(symbol, position_side)
            if pid is not None and qty > 0:
                return pid, qty
            await asyncio.sleep(self._CONFIRM_DELAY_S)
        return None, Decimal("0")

    async def _confirm_open(
        self,
        symbol: str,
        position_side: PositionSide,
        baseline_qty: Decimal,
        expected_delta: Decimal,
    ) -> tuple[Decimal, Decimal, str | None]:
        """Poll the exchange until the position grows by (close to) the order qty.

        Returns ``(filled_delta, entry_price, position_id)``. ``filled_delta`` is 0
        when the order never resulted in an actual position increase.
        """
        # Accept a small shortfall (rounding / partial) but require a real increase.
        threshold = expected_delta * Decimal("0.5")
        best_delta = Decimal("0")
        entry = Decimal("0")
        pid: str | None = None
        for _ in range(self._CONFIRM_ATTEMPTS):
            cur_pid, qty, cur_entry = await self._position_snapshot(symbol, position_side)
            delta = qty - baseline_qty
            if delta > best_delta:
                best_delta, entry, pid = delta, cur_entry, cur_pid
            if delta >= threshold and delta > 0:
                return delta, cur_entry, cur_pid
            await asyncio.sleep(self._CONFIRM_DELAY_S)
        return best_delta, entry, pid

    async def submit(self, request: OrderRequest) -> Order:
        self._order_seq += 1
        ts = datetime.now(UTC)
        local_id = f"live-{self._order_seq}"
        try:
            baseline_qty = Decimal("0")
            if not request.reduce_only:
                _pid, baseline_qty, _entry = await self._position_snapshot(
                    request.symbol, request.position_side
                )
                await self._rest.set_leverage(request.symbol, request.leverage)
            resp = await self._rest.place_order(request)
            order_id = str(resp.get("orderId", local_id)) if isinstance(resp, dict) else local_id

            bundled_sl = False
            bundled_tp = 0
            bundled_ok = True
            if not request.reduce_only:
                # Verify the order actually produced a position before reporting a
                # fill. A returned orderId only means the order was *accepted*.
                filled_qty, entry, pid = await self._confirm_open(
                    request.symbol,
                    request.position_side,
                    baseline_qty,
                    request.qty,
                )
                if filled_qty <= 0:
                    log.error(
                        "live_open_unconfirmed",
                        symbol=request.symbol,
                        side=request.position_side.value,
                        order_id=order_id,
                        requested_qty=str(request.qty),
                    )
                    return Order(
                        id=order_id,
                        symbol=request.symbol,
                        side=request.side,
                        order_type=request.order_type,
                        position_side=request.position_side,
                        qty=request.qty,
                        leverage=request.leverage,
                        status=OrderStatus.REJECTED,
                        ts=ts,
                        reason="order accepted but no position appeared on exchange",
                        tag=request.tag,
                    )
                fill_price = entry if entry > 0 else self._marks.get(
                    request.symbol, request.price or Decimal("0")
                )
                if request.protection is not None:
                    bundled_sl = True
                    bundled_tp = 1 if request.protection.take_profits else 0
                    min_resting = 1 if bundled_sl else 0
                    if pid:
                        verified = await self._verify_tpsl(request.symbol, pid)
                        bundled_ok = verified >= min_resting if min_resting else True
                    else:
                        bundled_ok = False
            else:
                filled_qty = request.qty
                fill_price = self._marks.get(request.symbol, request.price or Decimal("0"))

            fill = Fill(
                order_id=order_id,
                symbol=request.symbol,
                side=request.side,
                position_side=request.position_side,
                qty=filled_qty,
                price=fill_price,
                fee=Decimal("0"),
                ts=ts,
            )
            return Order(
                id=order_id,
                symbol=request.symbol,
                side=request.side,
                order_type=request.order_type,
                position_side=request.position_side,
                qty=filled_qty,
                leverage=request.leverage,
                status=OrderStatus.FILLED,
                ts=ts,
                price=request.price,
                filled_qty=filled_qty,
                avg_fill_price=fill_price,
                reduce_only=request.reduce_only,
                client_id=request.client_id,
                reason=request.reason,
                tag=request.tag,
                fills=[fill],
                bundled_sl=bundled_sl,
                bundled_tp=bundled_tp,
                bundled_protection_ok=bundled_ok,
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
            is_long = "LONG" in side_raw or "BUY" in side_raw
            side = PositionSide.LONG if is_long else PositionSide.SHORT
            entry = _dec(
                item.get("entryValue") or item.get("avgOpenPrice") or item.get("entryPrice")
            )
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
