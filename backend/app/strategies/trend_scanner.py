"""``trend_scanner`` — trend-following pullback strategy with scaled exits.

Design goals (REQ-005/006):

* **Dynamic coin selection** — scan a candidate universe and only "select" a coin
  once it has a valid, tradeable setup (or an open position). The watchlist is
  considered *complete* once ``max_symbols`` coins are selected; until then the UI
  shows a "scanning" state.
* **Regime filter** — only trade in the direction of the higher trend
  (price vs ``trend_ema`` and ``ema_fast`` vs ``ema_slow``).
* **Pullback entry** — enter on an RSI pullback that turns back toward the trend,
  which gives better entries than a naive moving-average cross.
* **Multiple entries (DCA ladder)** — add up to ``max_entries`` steps on further
  pullbacks spaced by ``entry_spacing_pct``.
* **Multiple take-profits** — scale out at TP1/TP2 (price-move levels),
  closing a fraction of the remaining position at each.
* **Multiple / moving stops** — an initial stop that moves to breakeven after the
  first take-profit and then trails the best price after the second. The stop
  therefore lives at several levels over the life of a trade.

The logic is pure and deterministic: all inputs arrive via ``StrategyContext`` and
all per-trade state lives on the instance, keyed by ``(symbol, side)``.

Profitability is never guaranteed; the defaults are a sensible starting point that
must be validated with a backtest before live use.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field

from app.domain.types import (
    AccountState,
    Instrument,
    IntentAction,
    PositionSide,
    ProtectionPlan,
    Side,
    TakeProfitLeg,
    TradeIntent,
)
from app.risk.sizer import _round_qty
from app.strategies.base import Strategy, StrategyContext
from app.strategies.indicators import ema, rsi
from app.strategies.registry import register_strategy


class TrendScannerParams(BaseModel):
    """Tunable parameters (exposed to the frontend as a JSON-schema form)."""

    # -- universe & selection ------------------------------------------------ #
    scan_universe: int = Field(
        default=30,
        ge=1,
        le=500,
        description="Emit a scan-progress log line every N evaluated coins.",
    )
    max_scan_rank: int = Field(
        default=500,
        ge=1,
        le=1000,
        description="How many of the top coins (by 24h volume) to scan, one by one.",
    )
    max_symbols: int = Field(
        default=5, ge=1, le=50, description="How many coins to actively trade at once."
    )

    # -- trend / regime ------------------------------------------------------ #
    ema_fast: int = Field(default=20, ge=2, description="Fast EMA period.")
    ema_slow: int = Field(default=50, ge=3, description="Slow EMA period.")
    trend_ema: int = Field(default=200, ge=5, description="Higher-trend regime EMA.")

    # -- entry trigger ------------------------------------------------------- #
    rsi_period: int = Field(default=14, ge=2, description="RSI lookback.")
    rsi_pullback_long: Decimal = Field(
        default=Decimal("45"), description="Enter long when RSI dips to/below this then turns up."
    )
    rsi_pullback_short: Decimal = Field(
        default=Decimal("55"),
        description="Enter short when RSI rises to/above this then turns down.",
    )

    # -- multiple entries (DCA ladder) -------------------------------------- #
    max_entries: int = Field(default=3, ge=1, le=20, description="Max entries per coin/side.")
    entry_spacing_pct: Decimal = Field(
        default=Decimal("0.8"),
        description="Adverse price move (percent) required before adding the next entry.",
    )

    # -- multiple take-profits (price move percent) ------------------------- #
    # Targets are price-move percentages (not ROE), so they are leverage
    # independent and directly comparable to the round-trip fee (~0.12% at a
    # 0.06% taker fee). ROE shown in the UI = price move x leverage.
    tp1_pct: Decimal = Field(default=Decimal("1.0"), description="TP1 price move percent.")
    tp1_close_pct: Decimal = Field(default=Decimal("35"), description="TP1 close % of position.")
    tp2_pct: Decimal = Field(default=Decimal("2.5"), description="TP2 price move percent.")
    tp2_close_pct: Decimal = Field(
        default=Decimal("100"), description="TP2 close % of remainder."
    )

    # -- stops --------------------------------------------------------------- #
    stop_loss_pct: Decimal = Field(
        default=Decimal("1.2"), description="Initial stop distance (price percent)."
    )
    breakeven_after_tp: int = Field(
        default=1, ge=0, le=2, description="Move stop to breakeven after this many TPs hit (0=off)."
    )
    trail_after_tp: int = Field(
        default=2, ge=0, le=2, description="Start trailing after this many TPs hit (0=off)."
    )
    trail_pct: Decimal = Field(
        default=Decimal("1.5"), description="Trailing stop distance (percent) from the best price."
    )

    allow_hedge: bool = Field(
        default=False, description="Allow opposite-direction positions to coexist."
    )

    model_config = {"extra": "ignore"}


def _key(symbol: str, side: PositionSide) -> tuple[str, str]:
    return (symbol, side.value)


@register_strategy("trend_scanner")
class TrendScannerStrategy(Strategy):
    """See module docstring."""

    Params = TrendScannerParams

    def __init__(self, params: TrendScannerParams | None = None) -> None:
        super().__init__(params)
        self.p: TrendScannerParams = self.params  # type: ignore[assignment]
        self._universe: list[str] = []
        self._selected: list[str] = []
        # Per (symbol, side) trade state.
        self._last_entry: dict[tuple[str, str], Decimal] = {}
        self._tps_hit: dict[tuple[str, str], int] = {}
        self._stop: dict[tuple[str, str], Decimal] = {}
        self._best: dict[tuple[str, str], Decimal] = {}
        self._pending_logs: list[dict] = []
        self._last_scan_reason: dict[str, str] = {}
        self._exchange_protections = False
        self._scan_cursor = 0

    async def on_start(self, context: StrategyContext) -> None:
        self._exchange_protections = context.exchange_protections

    # ------------------------------------------------------------------ #
    # Universe & selection
    # ------------------------------------------------------------------ #
    def desired_symbols(self, instruments: dict[str, Instrument]) -> list[str]:
        # Preserve instrument order. The builder pre-sorts this dict by 24h
        # quote volume, so this becomes a top-volume ranked universe. The engine
        # walks this list one coin at a time (fetch history -> evaluate -> next).
        candidates = [
            sym for sym, inst in instruments.items() if inst.quote.upper() == "USDT"
        ]
        self._universe = candidates[: self.p.max_scan_rank]
        return self._universe

    def selection_snapshot(self, context: StrategyContext) -> dict | None:
        scanning = [s for s in self._universe if s not in self._selected]
        return {
            "selected": list(self._selected),
            "scanning": scanning,
            "target": self.p.max_symbols,
        }

    def is_full(self) -> bool:
        """True once we have committed to the maximum number of coins."""
        return len(self._selected) >= self.p.max_symbols

    def on_open_outcome(
        self,
        symbol: str,
        position_side: PositionSide,
        *,
        success: bool,
        first_entry: bool,
    ) -> None:
        if not first_entry:
            return
        if success:
            self._ensure_selected(symbol)
            return
        k = _key(symbol, position_side)
        self._last_entry.pop(k, None)
        self._tps_hit.pop(k, None)
        self._stop.pop(k, None)
        self._best.pop(k, None)
        if symbol in self._selected:
            self._selected.remove(symbol)

    def _required_history(self) -> int:
        return max(self.p.ema_slow, self.p.trend_ema, self.p.rsi_period) + 1

    def warmup_bars(self) -> int:
        # A small buffer over the strict requirement so indicators are stable on
        # the first evaluated bar.
        return self._required_history() + 10

    def drain_scan_logs(self) -> list[dict]:
        logs = self._pending_logs
        self._pending_logs = []
        return logs

    def _queue_scan_log(self, symbol: str, reason: str, *, check: str, **context) -> None:
        if symbol and self._last_scan_reason.get(symbol) == reason:
            return
        if symbol:
            self._last_scan_reason[symbol] = reason
        message = f"scan {symbol}: {reason}" if symbol else reason
        ctx = {"check": check, **context}
        if symbol:
            ctx["symbol"] = symbol
        self._pending_logs.append(
            {"message": message, "symbol": symbol or None, "severity": "info", "context": ctx}
        )

    def _symbols_with_open_position(self, account: AccountState) -> set[str]:
        return {
            pos.symbol
            for pos in account.positions.values()
            if pos.qty > 0
        }

    def _symbol_is_flat(self, symbol: str, account: AccountState) -> bool:
        return symbol not in self._symbols_with_open_position(account)

    def _slot_free(self, account: AccountState) -> bool:
        return len(self._selected) < self.p.max_symbols

    def release_symbol(self, symbol: str, account: AccountState) -> bool:
        """Free a watchlist slot when the symbol has no open position."""
        if not self._symbol_is_flat(symbol, account):
            return False
        if symbol not in self._selected:
            return False
        self._selected.remove(symbol)
        for side in (PositionSide.LONG, PositionSide.SHORT):
            self._reset_trade(_key(symbol, side))
        return True

    def next_scan_candidate(self) -> str | None:
        """Return the next ranked coin not on the watchlist (round-robin)."""
        if self.is_full() or not self._universe:
            return None
        total = len(self._universe)
        for offset in range(total):
            idx = (self._scan_cursor + offset) % total
            symbol = self._universe[idx]
            if symbol not in self._selected:
                self._scan_cursor = (idx + 1) % total
                return symbol
        return None

    def _ensure_selected(self, symbol: str) -> None:
        if symbol not in self._selected:
            self._selected.append(symbol)

    def _evaluate_scan(
        self, symbol: str, context: StrategyContext
    ) -> tuple[str | None, dict]:
        """Return ``(reason, context)`` when the coin is being checked but not entering."""
        closes = context.market.closes(symbol)
        need = self._required_history()
        have = len(closes)
        if have < need:
            return (
                f"not enough history ({have}/{need} bars)",
                {"check": "insufficient_history", "bars": have, "required": need},
            )

        price = closes[-1]
        fast = ema(closes, self.p.ema_fast)
        slow = ema(closes, self.p.ema_slow)
        trend = ema(closes, self.p.trend_ema)
        cur_rsi = rsi(closes, self.p.rsi_period)
        if fast is None or slow is None or trend is None or cur_rsi is None:
            return ("indicators not ready", {"check": "indicators"})

        prev_rsi = rsi(closes[:-1], self.p.rsi_period)
        if prev_rsi is None:
            prev_rsi = cur_rsi
        ctx_base = {
            "price": str(price),
            "rsi": str(cur_rsi.quantize(Decimal("0.1"))),
            "ema_fast": str(fast.quantize(Decimal("0.0001"))),
            "ema_slow": str(slow.quantize(Decimal("0.0001"))),
            "trend_ema": str(trend.quantize(Decimal("0.0001"))),
        }

        for side in (PositionSide.LONG, PositionSide.SHORT):
            pos = context.account.position(symbol, side)
            if pos is not None and pos.qty > 0:
                return (
                    f"in {side.value} position (steps {pos.step_count}/{self.p.max_entries})",
                    {**ctx_base, "check": "in_position", "side": side.value},
                )

        if symbol in self._selected:
            return (
                "selected, waiting for entry trigger",
                {**ctx_base, "check": "selected_waiting"},
            )

        if not self._slot_free(context.account):
            return (
                f"slots full ({len(self._selected)}/{self.p.max_symbols} coins selected)",
                {
                    **ctx_base,
                    "check": "slots_full",
                    "selected": list(self._selected),
                },
            )

        long_regime = price > trend and fast > slow
        short_regime = price < trend and fast < slow

        if not long_regime and not short_regime:
            parts = []
            if price <= trend:
                parts.append("price <= trend_ema")
            else:
                parts.append("price > trend_ema")
            if fast <= slow:
                parts.append("ema_fast <= ema_slow")
            else:
                parts.append("ema_fast > ema_slow")
            return (
                f"no trend ({', '.join(parts)})",
                {**ctx_base, "check": "no_trend"},
            )

        if long_regime:
            triggered = prev_rsi <= self.p.rsi_pullback_long and cur_rsi > prev_rsi
            if not triggered:
                return (
                    "long regime: waiting RSI pullback "
                    f"(rsi {cur_rsi}, need <= {self.p.rsi_pullback_long} then turn up, "
                    f"prev {prev_rsi})",
                    {
                        **ctx_base,
                        "check": "rsi_pullback_long",
                        "rsi_prev": str(prev_rsi.quantize(Decimal("0.1"))),
                        "rsi_threshold": str(self.p.rsi_pullback_long),
                    },
                )
            return None, {}

        triggered = prev_rsi >= self.p.rsi_pullback_short and cur_rsi < prev_rsi
        if not triggered:
            return (
                "short regime: waiting RSI pullback "
                f"(rsi {cur_rsi}, need >= {self.p.rsi_pullback_short} then turn down, "
                f"prev {prev_rsi})",
                {
                    **ctx_base,
                    "check": "rsi_pullback_short",
                    "rsi_prev": str(prev_rsi.quantize(Decimal("0.1"))),
                    "rsi_threshold": str(self.p.rsi_pullback_short),
                },
            )
        return None, {}

    def _record_scan(self, symbol: str, context: StrategyContext) -> None:
        reason, ctx = self._evaluate_scan(symbol, context)
        if reason is None:
            return
        check = str(ctx.pop("check", "scan"))
        self._queue_scan_log(symbol, reason, check=check, **ctx)

    # ------------------------------------------------------------------ #
    # Take-profit / stop helpers
    # ------------------------------------------------------------------ #
    def _tp_levels(self) -> list[tuple[Decimal, Decimal]]:
        """Return ``[(price_move_pct, close_fraction_of_remainder), ...]``."""
        return [
            (self.p.tp1_pct, self.p.tp1_close_pct / Decimal(100)),
            (self.p.tp2_pct, self.p.tp2_close_pct / Decimal(100)),
        ]

    def _initial_stop(self, price: Decimal, side: PositionSide) -> Decimal:
        delta = price * (self.p.stop_loss_pct / Decimal(100))
        return price - delta if side is PositionSide.LONG else price + delta

    def position_levels(
        self, symbol: str, side: object, entry_price: object, leverage: int
    ) -> dict | None:
        """Chart levels: TP price for each price-move level + the active stop."""
        if not isinstance(side, PositionSide):
            return None
        entry = Decimal(str(entry_price))
        tps: list[Decimal] = []
        for move_pct, _close in self._tp_levels():
            move = move_pct / Decimal(100)
            if side is PositionSide.LONG:
                tps.append(entry * (Decimal(1) + move))
            else:
                tps.append(entry * (Decimal(1) - move))
        stop = self._stop.get(_key(symbol, side)) or self._initial_stop(entry, side)
        return {"take_profits": tps, "stops": [stop]}

    def protection_plan(
        self,
        symbol: str,
        side: object,
        entry_price: object,
        position_qty: object,
        instrument: Instrument,
    ) -> ProtectionPlan | None:
        """Build exchange TP/SL legs from entry size (live mode)."""
        if not isinstance(side, PositionSide):
            return None
        entry = Decimal(str(entry_price))
        qty = Decimal(str(position_qty))
        if qty <= 0:
            return None
        stop = self._stop.get(_key(symbol, side)) or self._initial_stop(entry, side)
        remaining = qty
        legs: list[TakeProfitLeg] = []
        levels = self._tp_levels()
        prec = instrument.base_precision
        for idx, (move_pct, close_frac) in enumerate(levels):
            move = move_pct / Decimal(100)
            if side is PositionSide.LONG:
                price = entry * (Decimal(1) + move)
            else:
                price = entry * (Decimal(1) - move)
            is_last = idx == len(levels) - 1
            if is_last or close_frac >= Decimal(1):
                leg_qty = remaining
            else:
                leg_qty = _round_qty(remaining * close_frac, prec)
            if leg_qty <= 0:
                continue
            leg_qty = min(leg_qty, remaining)
            legs.append(TakeProfitLeg(price=price, qty=leg_qty))
            remaining -= leg_qty
            if remaining <= 0:
                break
        return ProtectionPlan(stop_price=stop, take_profits=tuple(legs))

    # ------------------------------------------------------------------ #
    # Decision
    # ------------------------------------------------------------------ #
    def on_event(self, context: StrategyContext) -> list[TradeIntent]:
        bar = context.event.bar
        if bar is None:
            return []
        symbol = bar.symbol
        closes = context.market.closes(symbol)
        need = self._required_history()
        if len(closes) < need:
            self._record_scan(symbol, context)
            return []

        price = closes[-1]
        fast = ema(closes, self.p.ema_fast)
        slow = ema(closes, self.p.ema_slow)
        trend = ema(closes, self.p.trend_ema)
        cur_rsi = rsi(closes, self.p.rsi_period)
        if fast is None or slow is None or trend is None or cur_rsi is None:
            return []

        # Compare against the RSI one bar ago, derived from history so a single
        # evaluation (the one-by-one scan) detects the pullback turn correctly.
        prev_rsi = rsi(closes[:-1], self.p.rsi_period)
        if prev_rsi is None:
            prev_rsi = cur_rsi

        long_regime = price > trend and fast > slow
        short_regime = price < trend and fast < slow

        intents: list[TradeIntent] = []

        # Manage existing positions first (TPs and stops), both sides.
        for side in (PositionSide.LONG, PositionSide.SHORT):
            k = _key(symbol, side)
            pos = context.account.position(symbol, side)
            if pos is None or pos.qty <= 0:
                if self._exchange_protections and (
                    k in self._stop or k in self._tps_hit or k in self._best
                ):
                    self._reset_trade(k)
                continue
            intents.extend(self._manage_position(symbol, side, pos, price))

        if intents:
            return intents

        # Entry logic — only one direction per bar.
        if long_regime:
            side = PositionSide.LONG
            pos = context.account.position(symbol, side)
            entry = self._entry_intent(
                symbol, side, pos, price, cur_rsi, prev_rsi, context.account
            )
            if entry is not None:
                intents.append(entry)
        elif short_regime:
            side = PositionSide.SHORT
            pos = context.account.position(symbol, side)
            entry = self._entry_intent(
                symbol, side, pos, price, cur_rsi, prev_rsi, context.account
            )
            if entry is not None:
                intents.append(entry)

        if not intents:
            self._record_scan(symbol, context)

        return intents

    # ------------------------------------------------------------------ #
    def _entry_intent(
        self,
        symbol: str,
        side: PositionSide,
        pos,
        price: Decimal,
        cur_rsi: Decimal,
        prev_rsi: Decimal,
        account: AccountState,
    ) -> TradeIntent | None:
        steps = pos.step_count if pos is not None else 0
        if steps >= self.p.max_entries:
            return None

        # A slot is only consumed after a successful first fill (see on_open_outcome).
        if symbol not in self._selected and not self._slot_free(account):
            return None

        if steps == 0:
            # First entry: require a pullback that turns back toward the trend.
            if side is PositionSide.LONG:
                triggered = prev_rsi <= self.p.rsi_pullback_long and cur_rsi > prev_rsi
            else:
                triggered = prev_rsi >= self.p.rsi_pullback_short and cur_rsi < prev_rsi
            if not triggered:
                return None
        else:
            # Additional entries: require an adverse move (pullback) since the last.
            last = self._last_entry.get(_key(symbol, side))
            if last is None:
                return None
            move_pct = (abs(price - last) / last) * Decimal(100)
            if move_pct < self.p.entry_spacing_pct:
                return None
            pulled_back = (side is PositionSide.LONG and price < last) or (
                side is PositionSide.SHORT and price > last
            )
            if not pulled_back:
                return None

        self._last_entry[_key(symbol, side)] = price
        if steps == 0:
            # Reset per-trade state on the first entry.
            self._tps_hit[_key(symbol, side)] = 0
            self._stop[_key(symbol, side)] = self._initial_stop(price, side)
            self._best[_key(symbol, side)] = price

        entry_side = Side.BUY if side is PositionSide.LONG else Side.SELL
        return TradeIntent(
            symbol=symbol,
            side=entry_side,
            action=IntentAction.OPEN,
            position_side=side,
            stop_price=self._stop.get(_key(symbol, side)),
            reason=f"trend pullback entry step {steps + 1}",
            tag=f"entry_{steps + 1}",
        )

    # ------------------------------------------------------------------ #
    def _manage_position(
        self, symbol: str, side: PositionSide, pos, price: Decimal
    ) -> list[TradeIntent]:
        k = _key(symbol, side)
        intents: list[TradeIntent] = []

        # Live: TP/SL are resting exchange orders; do not emulate exits on bars.
        if self._exchange_protections:
            best = self._best.get(k, pos.entry_price)
            best = max(best, price) if side is PositionSide.LONG else min(best, price)
            self._best[k] = best
            return intents

        # Track the best (most favorable) price for trailing.
        best = self._best.get(k, pos.entry_price)
        best = max(best, price) if side is PositionSide.LONG else min(best, price)
        self._best[k] = best

        # 1) Stop check (uses the current, possibly moved, stop).
        stop = self._stop.get(k)
        if stop is not None:
            hit = (side is PositionSide.LONG and price <= stop) or (
                side is PositionSide.SHORT and price >= stop
            )
            if hit:
                self._reset_trade(k)
                intents.append(
                    TradeIntent(
                        symbol=symbol,
                        side=Side.SELL if side is PositionSide.LONG else Side.BUY,
                        action=IntentAction.CLOSE,
                        position_side=side,
                        reason="stop hit",
                        tag="stop",
                    )
                )
                return intents

        # 2) Take-profit ladder (favourable price move from entry).
        entry = pos.entry_price
        move_pct = (
            (price - entry) / entry * Decimal(100)
            if side is PositionSide.LONG
            else (entry - price) / entry * Decimal(100)
        ) if entry else Decimal(0)
        hits = self._tps_hit.get(k, 0)
        levels = self._tp_levels()
        if hits < len(levels):
            target_pct, close_frac = levels[hits]
            if move_pct >= target_pct:
                self._tps_hit[k] = hits + 1
                self._advance_stop(k, side, pos.entry_price, best)
                is_last = (hits + 1) >= len(levels) or close_frac >= Decimal(1)
                intents.append(
                    TradeIntent(
                        symbol=symbol,
                        side=Side.SELL if side is PositionSide.LONG else Side.BUY,
                        action=IntentAction.CLOSE if is_last else IntentAction.REDUCE,
                        position_side=side,
                        weight=close_frac,
                        reason=f"take profit {hits + 1} (+{move_pct:.2f}% price)",
                        tag=f"tp_{hits + 1}",
                    )
                )

        return intents

    # ------------------------------------------------------------------ #
    def _advance_stop(
        self, k: tuple[str, str], side: PositionSide, entry: Decimal, best: Decimal
    ) -> None:
        hits = self._tps_hit.get(k, 0)
        stop = self._stop.get(k, self._initial_stop(entry, side))

        # Move to breakeven after the configured number of TPs.
        if self.p.breakeven_after_tp and hits >= self.p.breakeven_after_tp:
            stop = max(stop, entry) if side is PositionSide.LONG else min(stop, entry)

        # Trail the best price after the configured number of TPs.
        if self.p.trail_after_tp and hits >= self.p.trail_after_tp:
            dist = best * (self.p.trail_pct / Decimal(100))
            trail = best - dist if side is PositionSide.LONG else best + dist
            stop = max(stop, trail) if side is PositionSide.LONG else min(stop, trail)

        self._stop[k] = stop

    def _reset_trade(self, k: tuple[str, str]) -> None:
        self._tps_hit.pop(k, None)
        self._stop.pop(k, None)
        self._best.pop(k, None)
        self._last_entry.pop(k, None)
