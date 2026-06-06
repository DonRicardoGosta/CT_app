"""``scalp_momentum`` — fast trend-filtered momentum scalping strategy.

Design goals (REQ-005/006), deliberately distinct from the existing strategies
(``trend_scanner`` trades RSI *pullbacks*, ``guarded_ladder`` trades *breakouts*):

* **Fixed coin universe** — unlike the auto/volume-scan strategies, this one only
  ever trades an explicit whitelist of liquid majors/mid-caps (``symbols``). It does
  no dynamic coin selection, so the whole whitelist is "selected" immediately and
  the engine streams exactly those coins. The whitelist is enforced twice: in
  :meth:`desired_symbols` (what the engine subscribes) and in :meth:`on_event`
  (a hard guard so a stray symbol is never traded).
* **Trend-filtered momentum entry** — only trade in the direction of the higher
  trend (price vs ``ema_trend``) and enter on a *fresh* fast/slow EMA momentum
  cross confirmed by RSI on the same side of its mid line. This fires once per
  cross, which keeps the scalp from chasing every wiggle.
* **Tight, fee-aware exits** — small scaled take-profits (TP1/TP2 as price-move
  percentages) that comfortably clear the round-trip taker fee (~0.12% at 0.06%),
  a tight stop that moves to breakeven after TP1 and trails after TP2, and a
  **time-stop** (``max_hold_bars``) so capital is freed quickly — the hallmark of
  scalping rather than position holding.
* **Quick in/out** — a single entry per coin/side by default (``max_entries`` can
  enable a small DCA add), so positions are opened and closed fast.

The logic is pure and deterministic: every input arrives via ``StrategyContext`` and
all per-trade state lives on the instance, keyed by ``(symbol, side)``. The same
code path runs live, dry-run and backtest (REQ-003). In live mode TP/SL are resting
exchange orders (see :meth:`protection_plan` / :meth:`compute_live_stop`); in
backtest/dry-run the exits are emulated on closed bars.

Profitability is never guaranteed; the defaults are a sensible scalping starting
point that must be validated with a backtest before live use.
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

#: The coins this scalp strategy trades by default — top-volume majors plus a few
#: liquid mid-caps. This is the same basket the platform UI defaults to.
DEFAULT_SCALP_SYMBOLS: list[str] = [
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "XRPUSDT",
    "BNBUSDT",
    "DOGEUSDT",
    "AAVEUSDT",
    "TONUSDT",
    "WLDUSDT",
    "LINKUSDT",
]


class ScalpMomentumParams(BaseModel):
    """Tunable parameters (exposed to the frontend as a JSON-schema form)."""

    # -- universe ------------------------------------------------------------ #
    symbols: list[str] = Field(
        default_factory=lambda: list(DEFAULT_SCALP_SYMBOLS),
        description="Exact coins to scalp. The strategy never trades anything else.",
    )

    # -- trend / regime ------------------------------------------------------ #
    ema_trend: int = Field(default=50, ge=5, description="Higher-trend regime EMA.")
    ema_fast: int = Field(default=9, ge=2, description="Fast EMA period (micro-trend).")
    ema_slow: int = Field(default=21, ge=3, description="Slow EMA period (micro-trend).")

    # -- entry trigger ------------------------------------------------------- #
    rsi_period: int = Field(default=14, ge=2, description="RSI lookback.")
    rsi_long_min: Decimal = Field(
        default=Decimal("50"),
        description="Long entries require RSI at/above this (momentum confirm).",
    )
    rsi_short_max: Decimal = Field(
        default=Decimal("50"),
        description="Short entries require RSI at/below this (momentum confirm).",
    )

    # -- multiple entries (optional small DCA) ------------------------------- #
    max_entries: int = Field(
        default=1, ge=1, le=10, description="Max entries per coin/side (1 = pure scalp)."
    )
    entry_spacing_pct: Decimal = Field(
        default=Decimal("0.5"),
        description="Adverse move (percent) required before adding another entry.",
    )

    # -- scaled take-profits (price move percent, leverage-independent) ------ #
    tp1_pct: Decimal = Field(default=Decimal("0.4"), description="TP1 price move percent.")
    tp1_close_pct: Decimal = Field(default=Decimal("50"), description="TP1 close % of position.")
    tp2_pct: Decimal = Field(default=Decimal("0.9"), description="TP2 price move percent.")
    tp2_close_pct: Decimal = Field(
        default=Decimal("100"), description="TP2 close % of the remainder."
    )

    # -- stops --------------------------------------------------------------- #
    stop_loss_pct: Decimal = Field(
        default=Decimal("0.5"), description="Initial stop distance (price percent)."
    )
    breakeven_after_tp: int = Field(
        default=1, ge=0, le=2, description="Move stop to breakeven after this many TPs (0=off)."
    )
    trail_after_tp: int = Field(
        default=2, ge=0, le=2, description="Start trailing after this many TPs (0=off)."
    )
    trail_pct: Decimal = Field(
        default=Decimal("0.5"), description="Trailing stop distance (percent) from the best price."
    )

    # -- time stop (scalp: don't hold) --------------------------------------- #
    max_hold_bars: int = Field(
        default=16,
        ge=0,
        description="Close the position after this many bars (0 disables the time-stop).",
    )

    allow_hedge: bool = Field(
        default=False, description="Allow opposite-direction positions to coexist."
    )

    model_config = {"extra": "ignore"}


def _key(symbol: str, side: PositionSide) -> tuple[str, str]:
    return (symbol, side.value)


@register_strategy("scalp_momentum")
class ScalpMomentumStrategy(Strategy):
    """See module docstring."""

    Params = ScalpMomentumParams

    def __init__(self, params: ScalpMomentumParams | None = None) -> None:
        super().__init__(params)
        self.p: ScalpMomentumParams = self.params  # type: ignore[assignment]
        # Normalised whitelist (upper-case, de-duplicated, order preserved).
        seen: set[str] = set()
        self._whitelist: list[str] = []
        for raw in self.p.symbols:
            sym = str(raw).strip().upper()
            if sym and sym not in seen:
                seen.add(sym)
                self._whitelist.append(sym)
        self._whitelist_set = set(self._whitelist)
        # Per (symbol, side) trade state.
        self._last_entry: dict[tuple[str, str], Decimal] = {}
        self._tps_hit: dict[tuple[str, str], int] = {}
        self._stop: dict[tuple[str, str], Decimal] = {}
        self._best: dict[tuple[str, str], Decimal] = {}
        self._bars_held: dict[tuple[str, str], int] = {}
        self._pending_logs: list[dict] = []
        self._last_scan_reason: dict[str, str] = {}
        self._exchange_protections = False

    async def on_start(self, context: StrategyContext) -> None:
        self._exchange_protections = context.exchange_protections

    # ------------------------------------------------------------------ #
    # Universe (fixed whitelist — no dynamic selection)
    # ------------------------------------------------------------------ #
    def desired_symbols(self, instruments: dict[str, Instrument]) -> list[str]:
        # Subscribe exactly the whitelisted coins that the exchange offers; if the
        # instrument map is empty (e.g. fetch failed) fall back to the raw list so
        # the intent is still expressed.
        available = [s for s in self._whitelist if s in instruments]
        return available or list(self._whitelist)

    def warmup_bars(self) -> int:
        # A small buffer over the strict requirement so indicators are stable on
        # the first evaluated bar.
        return self._required_history() + 10

    def _required_history(self) -> int:
        # +2 so a previous-bar EMA (cross detection) and RSI are both available.
        return max(self.p.ema_trend, self.p.ema_slow, self.p.rsi_period) + 2

    # ------------------------------------------------------------------ #
    # Scan logging (Logs UI feedback)
    # ------------------------------------------------------------------ #
    def drain_scan_logs(self) -> list[dict]:
        logs = self._pending_logs
        self._pending_logs = []
        return logs

    def _queue_scan_log(self, symbol: str, reason: str, *, check: str, **context) -> None:
        if symbol and self._last_scan_reason.get(symbol) == reason:
            return
        if symbol:
            self._last_scan_reason[symbol] = reason
        ctx = {"check": check, **context}
        if symbol:
            ctx["symbol"] = symbol
        self._pending_logs.append(
            {
                "message": f"scalp {symbol}: {reason}" if symbol else reason,
                "symbol": symbol or None,
                "severity": "info",
                "context": ctx,
            }
        )

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
        """Build exchange TP/SL legs from the entry size (live mode)."""
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

    def compute_live_stop(
        self, side: object, entry_price: object, best_price: object, tps_filled: int
    ) -> object | None:
        """Moving exchange stop: breakeven after TP1, trailing after TP2 (live)."""
        if not isinstance(side, PositionSide):
            return None
        entry = Decimal(str(entry_price))
        best = Decimal(str(best_price))
        stop: Decimal | None = None
        if self.p.breakeven_after_tp and tps_filled >= self.p.breakeven_after_tp:
            stop = entry
        if self.p.trail_after_tp and tps_filled >= self.p.trail_after_tp:
            dist = best * (self.p.trail_pct / Decimal(100))
            trail = best - dist if side is PositionSide.LONG else best + dist
            if stop is None:
                stop = trail
            else:
                stop = max(stop, trail) if side is PositionSide.LONG else min(stop, trail)
        return stop

    # ------------------------------------------------------------------ #
    # Decision
    # ------------------------------------------------------------------ #
    def on_event(self, context: StrategyContext) -> list[TradeIntent]:
        bar = context.event.bar
        if bar is None:
            return []
        symbol = bar.symbol
        # Hard whitelist guard: never trade a coin outside the configured set.
        if symbol not in self._whitelist_set:
            return []

        closes = context.market.closes(symbol)
        need = self._required_history()
        if len(closes) < need:
            self._queue_scan_log(
                symbol,
                f"not enough history ({len(closes)}/{need} bars)",
                check="insufficient_history",
                bars=len(closes),
                required=need,
            )
            return []

        price = closes[-1]
        intents: list[TradeIntent] = []

        # 1) Manage existing positions first (stop / TP / trail / time-stop).
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

        # 2) Entry — compute the momentum trigger once, act on one direction.
        fast = ema(closes, self.p.ema_fast)
        slow = ema(closes, self.p.ema_slow)
        trend = ema(closes, self.p.ema_trend)
        cur_rsi = rsi(closes, self.p.rsi_period)
        prev_fast = ema(closes[:-1], self.p.ema_fast)
        prev_slow = ema(closes[:-1], self.p.ema_slow)
        if (
            fast is None
            or slow is None
            or trend is None
            or cur_rsi is None
            or prev_fast is None
            or prev_slow is None
        ):
            return []

        long_regime = price > trend and fast > slow
        short_regime = price < trend and fast < slow
        cross_up = prev_fast <= prev_slow and fast > slow
        cross_down = prev_fast >= prev_slow and fast < slow

        triggered_long = long_regime and cross_up and cur_rsi >= self.p.rsi_long_min
        triggered_short = short_regime and cross_down and cur_rsi <= self.p.rsi_short_max

        if triggered_long or self._has_open(context.account, symbol, PositionSide.LONG):
            entry = self._entry_intent(
                symbol, PositionSide.LONG, context.account, price, triggered_long
            )
            if entry is not None:
                intents.append(entry)
        elif triggered_short or self._has_open(context.account, symbol, PositionSide.SHORT):
            entry = self._entry_intent(
                symbol, PositionSide.SHORT, context.account, price, triggered_short
            )
            if entry is not None:
                intents.append(entry)

        if not intents:
            self._record_no_entry(symbol, price, trend, fast, slow, cur_rsi)

        return intents

    # ------------------------------------------------------------------ #
    def _has_open(self, account: AccountState, symbol: str, side: PositionSide) -> bool:
        pos = account.position(symbol, side)
        return pos is not None and pos.qty > 0

    def _symbol_is_flat(self, symbol: str, account: AccountState) -> bool:
        return not (
            self._has_open(account, symbol, PositionSide.LONG)
            or self._has_open(account, symbol, PositionSide.SHORT)
        )

    def _entry_intent(
        self,
        symbol: str,
        side: PositionSide,
        account: AccountState,
        price: Decimal,
        triggered: bool,
    ) -> TradeIntent | None:
        k = _key(symbol, side)
        pos = account.position(symbol, side)
        steps = pos.step_count if pos is not None else 0
        if steps >= self.p.max_entries:
            return None

        if steps == 0:
            # First entry: requires a fresh momentum cross and a flat symbol.
            if not triggered:
                return None
            if not self._symbol_is_flat(symbol, account):
                return None
        else:
            # Additional (DCA) entries: require an adverse move since the last step.
            last = self._last_entry.get(k)
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

        self._last_entry[k] = price
        if steps == 0:
            self._tps_hit[k] = 0
            self._stop[k] = self._initial_stop(price, side)
            self._best[k] = price
            self._bars_held[k] = 0

        entry_side = Side.BUY if side is PositionSide.LONG else Side.SELL
        return TradeIntent(
            symbol=symbol,
            side=entry_side,
            action=IntentAction.OPEN,
            position_side=side,
            stop_price=self._stop.get(k),
            reason=f"scalp momentum cross entry step {steps + 1}",
            tag=f"entry_{steps + 1}",
        )

    # ------------------------------------------------------------------ #
    def _manage_position(
        self, symbol: str, side: PositionSide, pos, price: Decimal
    ) -> list[TradeIntent]:
        k = _key(symbol, side)
        intents: list[TradeIntent] = []

        # Track the best (most favourable) price for trailing.
        best = self._best.get(k, pos.entry_price)
        best = max(best, price) if side is PositionSide.LONG else min(best, price)
        self._best[k] = best

        # Live: TP/SL are resting exchange orders; do not emulate exits on bars.
        if self._exchange_protections:
            return intents

        # Count bars the position has been held (for the scalp time-stop).
        held = self._bars_held.get(k, 0) + 1
        self._bars_held[k] = held

        # 1) Stop check (uses the current, possibly moved, stop).
        stop = self._stop.get(k)
        if stop is not None:
            hit = (side is PositionSide.LONG and price <= stop) or (
                side is PositionSide.SHORT and price >= stop
            )
            if hit:
                self._reset_trade(k)
                intents.append(self._close_intent(symbol, side, "stop hit", "stop"))
                return intents

        # 2) Take-profit ladder (favourable price move from entry).
        entry = pos.entry_price
        move_pct = (
            ((price - entry) / entry * Decimal(100))
            if side is PositionSide.LONG
            else ((entry - price) / entry * Decimal(100))
        ) if entry else Decimal(0)
        hits = self._tps_hit.get(k, 0)
        levels = self._tp_levels()
        if hits < len(levels):
            target_pct, close_frac = levels[hits]
            if move_pct >= target_pct:
                self._tps_hit[k] = hits + 1
                self._advance_stop(k, side, entry, best)
                is_last = (hits + 1) >= len(levels) or close_frac >= Decimal(1)
                if is_last:
                    self._reset_trade(k)
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

        # 3) Time-stop: scalps free capital quickly.
        if self.p.max_hold_bars and held >= self.p.max_hold_bars:
            self._reset_trade(k)
            intents.append(
                self._close_intent(
                    symbol, side, f"time stop ({held} bars held)", "time_stop"
                )
            )

        return intents

    def _close_intent(
        self, symbol: str, side: PositionSide, reason: str, tag: str
    ) -> TradeIntent:
        return TradeIntent(
            symbol=symbol,
            side=Side.SELL if side is PositionSide.LONG else Side.BUY,
            action=IntentAction.CLOSE,
            position_side=side,
            reason=reason,
            tag=tag,
        )

    # ------------------------------------------------------------------ #
    def _advance_stop(
        self, k: tuple[str, str], side: PositionSide, entry: Decimal, best: Decimal
    ) -> None:
        hits = self._tps_hit.get(k, 0)
        stop = self._stop.get(k, self._initial_stop(entry, side))

        if self.p.breakeven_after_tp and hits >= self.p.breakeven_after_tp:
            stop = max(stop, entry) if side is PositionSide.LONG else min(stop, entry)

        if self.p.trail_after_tp and hits >= self.p.trail_after_tp:
            dist = best * (self.p.trail_pct / Decimal(100))
            trail = best - dist if side is PositionSide.LONG else best + dist
            stop = max(stop, trail) if side is PositionSide.LONG else min(stop, trail)

        self._stop[k] = stop

    def _reset_trade(self, k: tuple[str, str]) -> None:
        self._tps_hit.pop(k, None)
        self._stop.pop(k, None)
        self._best.pop(k, None)
        self._bars_held.pop(k, None)
        self._last_entry.pop(k, None)

    def _record_no_entry(
        self,
        symbol: str,
        price: Decimal,
        trend: Decimal,
        fast: Decimal,
        slow: Decimal,
        cur_rsi: Decimal,
    ) -> None:
        if price > trend and fast > slow:
            reason = "long regime: waiting for a fresh momentum cross"
        elif price < trend and fast < slow:
            reason = "short regime: waiting for a fresh momentum cross"
        else:
            reason = "no trend alignment (price vs trend / fast vs slow)"
        self._queue_scan_log(
            symbol,
            reason,
            check="no_entry",
            price=str(price),
            rsi=str(cur_rsi.quantize(Decimal("0.1"))),
        )
