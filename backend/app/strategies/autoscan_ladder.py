"""First reference strategy: ``autoscan_ladder`` (REQ-006).

Behaviour:

* **Auto coin selection** — :meth:`desired_symbols` filters the available
  instruments (USDT-quoted) and picks up to ``max_symbols`` for live subscription.
  In backtest the engine supplies whichever symbols have bars.
* **Direction** — an EMA(fast)/EMA(slow) cross gives the trend direction.
* **Laddered entries** — the position is built in up to ``ladder_steps`` small
  steps; a new step is added only once price has moved ``ladder_step_spacing_pct``
  from the previous step's entry. Several steps accumulate into one position
  (more, smaller positions on the same coin).
* **Both directions** — on a trend flip the opposite side is closed, unless hedging
  is allowed, in which case opposite-direction positions may coexist.
* **Risk** — sizing, the leverage multiplier and capital/loss limits are delegated
  to the risk sizer (REQ-007). The strategy only sets ``stop_price`` so the sizer
  can estimate loss.

The decision logic is intentionally simple and isolated here so it can be swapped
for a more sophisticated one without touching the engine (REQ-001/005).
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field

from app.domain.types import (
    Instrument,
    IntentAction,
    PositionSide,
    Side,
    TradeIntent,
)
from app.strategies.base import Strategy, StrategyContext
from app.strategies.indicators import ema
from app.strategies.registry import register_strategy


class AutoscanLadderParams(BaseModel):
    """Tunable parameters (exposed to the frontend as JSON schema)."""

    max_symbols: int = Field(default=5, ge=1, le=100, description="Max coins to trade.")
    min_quote_volume: Decimal = Field(
        default=Decimal("0"),
        description="Minimum 24h quote volume filter when volume data is available.",
    )
    ema_fast: int = Field(default=12, ge=2, description="Fast EMA period.")
    ema_slow: int = Field(default=26, ge=3, description="Slow EMA period.")
    ladder_steps: int = Field(default=3, ge=1, le=20, description="Max ladder steps/side.")
    ladder_step_spacing_pct: Decimal = Field(
        default=Decimal("0.5"),
        description="Price move (percent) required before adding the next step.",
    )
    stop_loss_pct: Decimal = Field(
        default=Decimal("2.0"), description="Stop distance percent (for loss estimation)."
    )
    take_profit_pct: Decimal = Field(
        default=Decimal("3.0"), description="Take-profit percent on the aggregate position."
    )
    allow_hedge: bool = Field(
        default=True, description="Allow opposite-direction positions to coexist."
    )

    model_config = {"extra": "ignore"}


@register_strategy("autoscan_ladder")
class AutoscanLadderStrategy(Strategy):
    """See module docstring."""

    Params = AutoscanLadderParams

    def __init__(self, params: AutoscanLadderParams | None = None) -> None:
        super().__init__(params)
        self.p: AutoscanLadderParams = self.params  # type: ignore[assignment]
        # Last step entry price per (symbol, side); used to space ladder steps.
        self._last_entry: dict[tuple[str, PositionSide], Decimal] = {}

    # ------------------------------------------------------------------ #
    def desired_symbols(self, instruments: dict[str, Instrument]) -> list[str]:
        candidates = sorted(
            sym for sym, inst in instruments.items() if inst.quote.upper() == "USDT"
        )
        return candidates[: self.p.max_symbols]

    # ------------------------------------------------------------------ #
    def on_event(self, context: StrategyContext) -> list[TradeIntent]:
        bar = context.event.bar
        if bar is None:
            return []
        symbol = bar.symbol
        closes = context.market.closes(symbol)
        if len(closes) < self.p.ema_slow + 1:
            return []

        fast = ema(closes, self.p.ema_fast)
        slow = ema(closes, self.p.ema_slow)
        if fast is None or slow is None:
            return []

        price = closes[-1]
        bullish = fast > slow
        desired = PositionSide.LONG if bullish else PositionSide.SHORT
        opposite = PositionSide.SHORT if bullish else PositionSide.LONG

        intents: list[TradeIntent] = []

        # 1) manage the opposite side on a trend flip
        opp_pos = context.account.position(symbol, opposite)
        if opp_pos is not None and not self.p.allow_hedge:
            intents.append(
                TradeIntent(
                    symbol=symbol,
                    side=Side.SELL if opposite is PositionSide.LONG else Side.BUY,
                    action=IntentAction.CLOSE,
                    position_side=opposite,
                    reason="trend flip; hedge disabled",
                    tag="close_opposite",
                )
            )

        # 2) take profit on the desired side's aggregate position
        pos = context.account.position(symbol, desired)
        if pos is not None and pos.qty > 0:
            upnl_pct = (
                pos.unrealized_pnl(price) / pos.margin * Decimal(100)
                if pos.margin
                else Decimal(0)
            )
            if upnl_pct >= self.p.take_profit_pct:
                self._last_entry.pop((symbol, desired), None)
                intents.append(
                    TradeIntent(
                        symbol=symbol,
                        side=Side.SELL if desired is PositionSide.LONG else Side.BUY,
                        action=IntentAction.CLOSE,
                        position_side=desired,
                        reason=f"take profit {upnl_pct:.2f}%",
                        tag="take_profit",
                    )
                )
                return intents

        # 3) laddered entry on the desired side
        steps = pos.step_count if pos is not None else 0
        if steps < self.p.ladder_steps and self._spacing_ok(symbol, desired, price):
            entry_side = Side.BUY if desired is PositionSide.LONG else Side.SELL
            stop = self._stop_price(price, desired)
            self._last_entry[(symbol, desired)] = price
            intents.append(
                TradeIntent(
                    symbol=symbol,
                    side=entry_side,
                    action=IntentAction.OPEN,
                    position_side=desired,
                    stop_price=stop,
                    reason=f"ema cross {'bull' if bullish else 'bear'} step {steps + 1}",
                    tag=f"ladder_step_{steps + 1}",
                )
            )
        return intents

    # ------------------------------------------------------------------ #
    def _spacing_ok(self, symbol: str, side: PositionSide, price: Decimal) -> bool:
        last = self._last_entry.get((symbol, side))
        if last is None:
            return True
        move_pct = (abs(price - last) / last) * Decimal(100)
        return move_pct >= self.p.ladder_step_spacing_pct

    def _stop_price(self, price: Decimal, side: PositionSide) -> Decimal:
        delta = price * (self.p.stop_loss_pct / Decimal(100))
        return price - delta if side is PositionSide.LONG else price + delta
