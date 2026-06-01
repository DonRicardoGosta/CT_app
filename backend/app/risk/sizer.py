"""Position sizing and leverage escalation (REQ-007).

The sizer turns a strategy's :class:`TradeIntent` into a fully-specified
:class:`OrderRequest`, or rejects it with a reason. It is a pure function of its
inputs (deterministic), so live and backtest size identically (REQ-003).

Key rules:

* Committed margin per step == ``min_investment_usd * weight``, **independent of
  leverage**. (Set 1 USD -> 1 USD committed at any multiplier.)
* If the resulting order is below the exchange minimum size, **increase the
  leverage multiplier** step by step up to ``max_leverage`` until it qualifies.
* Capital gate: total committed margin may not exceed ``max_capital_usd``.
* Loss gate: estimated loss for the order may not exceed ``max_loss_usd``.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal

from app.domain.types import (
    AccountState,
    IntentAction,
    Instrument,
    OrderRequest,
    OrderType,
    PositionSide,
    Side,
    TradeIntent,
)
from app.risk.config import RiskParams


@dataclass(frozen=True, slots=True)
class SizingResult:
    """Outcome of sizing an intent."""

    ok: bool
    request: OrderRequest | None = None
    reason: str = ""
    leverage: int = 0
    margin: Decimal = Decimal("0")
    notional: Decimal = Decimal("0")
    qty: Decimal = Decimal("0")

    @classmethod
    def rejected(cls, reason: str) -> SizingResult:
        return cls(ok=False, reason=reason)


def _round_qty(qty: Decimal, precision: int) -> Decimal:
    """Round a quantity down to the instrument's base precision."""
    if precision <= 0:
        return qty.to_integral_value(rounding=ROUND_DOWN)
    quant = Decimal(1).scaleb(-precision)
    return qty.quantize(quant, rounding=ROUND_DOWN)


class RiskSizer:
    """Sizes intents subject to capital, loss and exchange constraints."""

    def __init__(self, params: RiskParams) -> None:
        self.params = params

    def size(
        self,
        intent: TradeIntent,
        account: AccountState,
        instrument: Instrument,
        price: Decimal,
    ) -> SizingResult:
        if price <= 0:
            return SizingResult.rejected("invalid price")
        if intent.action in (IntentAction.CLOSE, IntentAction.REDUCE):
            return self._size_close(intent, account, instrument, price)
        return self._size_open(intent, account, instrument, price)

    # ------------------------------------------------------------------ #
    def _size_open(
        self,
        intent: TradeIntent,
        account: AccountState,
        instrument: Instrument,
        price: Decimal,
    ) -> SizingResult:
        p = self.params

        if not p.allow_hedge:
            opposite = (
                PositionSide.SHORT
                if intent.position_side is PositionSide.LONG
                else PositionSide.LONG
            )
            if account.position(intent.symbol, opposite) is not None:
                return SizingResult.rejected("hedge disabled; opposite position open")

        margin = (p.min_investment_usd * intent.weight).copy_abs()
        if margin <= 0:
            return SizingResult.rejected("non-positive margin")

        # Capital gate: committed margin is independent of leverage, so we can check
        # it before escalating the multiplier.
        if account.used_margin() + margin > p.max_capital_usd:
            return SizingResult.rejected(
                f"capital limit reached (used={account.used_margin()} + {margin} "
                f"> max={p.max_capital_usd})"
            )

        # Escalate leverage until the order meets the exchange minimum size.
        leverage = max(p.base_leverage, instrument.min_leverage)
        max_lev = min(p.max_leverage, instrument.max_leverage)
        chosen: tuple[int, Decimal, Decimal] | None = None
        while leverage <= max_lev:
            notional = margin * Decimal(leverage)
            qty = _round_qty(notional / price, instrument.base_precision)
            if qty >= instrument.min_trade_volume and qty > 0:
                chosen = (leverage, qty, notional)
                break
            leverage += p.leverage_step

        if chosen is None:
            return SizingResult.rejected(
                "insufficient capital for this multiplier; even at max leverage the "
                "order is below the exchange minimum size"
            )

        leverage, qty, notional = chosen

        # Loss gate (estimated loss from stop, else assume full committed margin).
        if intent.stop_price is not None:
            est_loss = (price - intent.stop_price).copy_abs() * qty
        else:
            est_loss = margin
        if est_loss > p.max_loss_usd:
            return SizingResult.rejected(
                f"loss limit exceeded (est_loss={est_loss} > max={p.max_loss_usd})"
            )

        request = OrderRequest(
            symbol=intent.symbol,
            side=intent.side,
            order_type=OrderType.MARKET,
            qty=qty,
            position_side=intent.position_side,
            leverage=leverage,
            reduce_only=False,
            stop_price=intent.stop_price,
            reason=intent.reason,
            tag=intent.tag,
        )
        return SizingResult(
            ok=True,
            request=request,
            leverage=leverage,
            margin=margin,
            notional=notional,
            qty=qty,
        )

    # ------------------------------------------------------------------ #
    def _size_close(
        self,
        intent: TradeIntent,
        account: AccountState,
        instrument: Instrument,
        price: Decimal,
    ) -> SizingResult:
        position = account.position(intent.symbol, intent.position_side)
        if position is None or position.qty <= 0:
            return SizingResult.rejected("no position to close")

        fraction = intent.weight if intent.action is IntentAction.REDUCE else Decimal("1")
        fraction = min(fraction, Decimal("1"))
        qty = _round_qty(position.qty * fraction, instrument.base_precision)
        if qty <= 0:
            qty = position.qty  # ensure we can always fully close dust

        # Closing side is the opposite of the position direction.
        close_side = (
            Side.SELL if intent.position_side is PositionSide.LONG else Side.BUY
        )
        request = OrderRequest(
            symbol=intent.symbol,
            side=close_side,
            order_type=OrderType.MARKET,
            qty=qty,
            position_side=intent.position_side,
            leverage=position.leverage,
            reduce_only=True,
            reason=intent.reason,
            tag=intent.tag,
        )
        return SizingResult(
            ok=True,
            request=request,
            leverage=position.leverage,
            margin=Decimal("0"),
            notional=price * qty,
            qty=qty,
        )
