"""First reference strategy: ``autoscan_ladder`` (REQ-006).

Auto-selects up to ``max_symbols`` USDT pairs (by recent volatility when data
exists), trades EMA trend with laddered entries, and exposes a **plan snapshot**
for the UI (selected coins, per-coin price, TP/SL, next ladder step).

TP/SL percentages are on **margin (ROE)**; price levels are divided by leverage
(see ``app.domain.tp_sl``).
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field

from app.domain.tp_sl import stop_loss_price, take_profit_price
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
        default=Decimal("2.0"),
        description="Stop-loss % on margin (ROE); price level uses leverage.",
    )
    take_profit_pct: Decimal = Field(
        default=Decimal("3.0"),
        description="Take-profit % on margin (ROE); price level uses leverage.",
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
        self._last_entry: dict[tuple[str, PositionSide], Decimal] = {}
        self._selected_symbols: list[str] = []

    def selected_symbols(self) -> list[str]:
        return list(self._selected_symbols)

    # ------------------------------------------------------------------ #
    def desired_symbols(self, instruments: dict[str, Instrument]) -> list[str]:
        if self._selected_symbols:
            return list(self._selected_symbols)
        candidates = sorted(
            sym for sym, inst in instruments.items() if inst.quote.upper() == "USDT"
        )
        return candidates[: self.p.max_symbols]

    async def on_start(self, context: StrategyContext) -> None:
        self._selected_symbols = self._rank_symbols(context.instruments, context.market)

    def _rank_symbols(self, instruments: dict[str, Instrument], market) -> list[str]:
        candidates = sorted(
            sym for sym, inst in instruments.items() if inst.quote.upper() == "USDT"
        )
        scored: list[tuple[Decimal, str]] = []
        for sym in candidates:
            closes = market.closes(sym, 40)
            if len(closes) < 10:
                continue
            vol = Decimal("0")
            for i in range(1, len(closes)):
                vol += (closes[i] - closes[i - 1]).copy_abs()
            vol_pct = (vol / closes[-1]) * Decimal(100) if closes[-1] else Decimal("0")
            scored.append((vol_pct, sym))
        scored.sort(key=lambda x: x[0], reverse=True)
        if scored:
            return [s for _, s in scored[: self.p.max_symbols]]
        return candidates[: self.p.max_symbols]

    # ------------------------------------------------------------------ #
    def plan_snapshot(self, context: StrategyContext, leverage: int) -> dict | None:
        symbols = self._rank_symbols(context.instruments, context.market)
        if symbols:
            self._selected_symbols = symbols
        lev = max(int(leverage), 1)
        coins: list[dict] = []
        for sym in self._selected_symbols:
            coin = self._coin_plan(sym, context, lev)
            if coin:
                coins.append(coin)
        return {
            "selected_symbols": self._selected_symbols,
            "leverage": lev,
            "stop_loss_pct_margin": str(self.p.stop_loss_pct),
            "take_profit_pct_margin": str(self.p.take_profit_pct),
            "coins": coins,
        }

    def _coin_plan(self, symbol: str, context: StrategyContext, leverage: int) -> dict | None:
        closes = context.market.closes(symbol)
        price = context.market.last_price(symbol)
        if not closes or price is None:
            return None

        fast = ema(closes, self.p.ema_fast)
        slow = ema(closes, self.p.ema_slow)
        if fast is None or slow is None:
            trend = "neutral"
            desired = PositionSide.LONG
        else:
            trend = "bull" if fast > slow else "bear"
            desired = PositionSide.LONG if fast > slow else PositionSide.SHORT

        pos = context.account.position(symbol, desired)
        steps = pos.step_count if pos else 0
        entry = pos.entry_price if pos and pos.qty > 0 else price

        sl = stop_loss_price(entry, desired, self.p.stop_loss_pct, leverage)
        tp = take_profit_price(entry, desired, self.p.take_profit_pct, leverage)

        last = self._last_entry.get((symbol, desired))
        if last is None:
            next_price = price
            next_reason = "ready_now (no prior ladder step)"
        else:
            if desired is PositionSide.LONG:
                next_price = last * (Decimal(1) + self.p.ladder_step_spacing_pct / Decimal(100))
            else:
                next_price = last * (Decimal(1) - self.p.ladder_step_spacing_pct / Decimal(100))
            next_reason = f"after {self.p.ladder_step_spacing_pct}% move from last step"

        can_open = steps < self.p.ladder_steps and self._spacing_ok(symbol, desired, price)
        if steps >= self.p.ladder_steps:
            open_status = "ladder_full"
        elif can_open:
            open_status = "open_now"
        else:
            open_status = "wait_spacing"

        bars = context.market.bars(symbol, 80)
        chart = [
            {
                "t": int(b.open_time.timestamp()),
                "o": str(b.open),
                "h": str(b.high),
                "l": str(b.low),
                "c": str(b.close),
            }
            for b in bars
        ]

        return {
            "symbol": symbol,
            "price": str(price),
            "trend": trend,
            "direction": desired.value,
            "ema_fast": str(fast) if fast is not None else None,
            "ema_slow": str(slow) if slow is not None else None,
            "leverage": leverage,
            "stop_loss_price": str(sl),
            "take_profit_price": str(tp),
            "stop_loss_pct_margin": str(self.p.stop_loss_pct),
            "take_profit_pct_margin": str(self.p.take_profit_pct),
            "ladder_step": steps,
            "ladder_max": self.p.ladder_steps,
            "open_status": open_status,
            "next_open_price": str(next_price),
            "next_open_reason": next_reason,
            "position_qty": str(pos.qty) if pos else "0",
            "entry_price": str(entry),
            "bars": chart,
        }

    # ------------------------------------------------------------------ #
    def on_event(self, context: StrategyContext) -> list[TradeIntent]:
        bar = context.event.bar
        if bar is None:
            return []
        symbol = bar.symbol
        if self._selected_symbols and symbol not in self._selected_symbols:
            return []

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
                        reason=f"take profit {upnl_pct:.2f}% on margin",
                        tag="take_profit",
                    )
                )
                return intents

        steps = pos.step_count if pos is not None else 0
        if steps < self.p.ladder_steps and self._spacing_ok(symbol, desired, price):
            entry_side = Side.BUY if desired is PositionSide.LONG else Side.SELL
            lev = pos.leverage if pos and pos.leverage > 0 else context.leverage
            stop = stop_loss_price(price, desired, self.p.stop_loss_pct, lev)
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

    def _spacing_ok(self, symbol: str, side: PositionSide, price: Decimal) -> bool:
        last = self._last_entry.get((symbol, side))
        if last is None:
            return True
        move_pct = (abs(price - last) / last) * Decimal(100)
        return move_pct >= self.p.ladder_step_spacing_pct
