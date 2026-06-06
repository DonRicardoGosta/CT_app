"""``guarded_ladder`` — breakout trend strategy with a capital-drawdown kill switch.

This is a self-contained strategy (REQ-005/006) designed around a small, capped
account (the defaults target ~50 USD of capital, 5 USD committed margin per step
and a 20x multiplier). It differs from the existing strategies as follows:

* **Many coins** — dynamic, volume-ranked coin selection, scanned one by one, with
  free watchlist slots refilled as positions close (same scaffolding the engine
  drives for ``trend_scanner``).
* **Breakout / momentum entry** — the *first* entry fires when price breaks the
  highest (long) / lowest (short) close of the last ``breakout_lookback`` bars,
  filtered by an EMA regime (``price`` vs ``trend_ema`` and ``ema_fast`` vs
  ``ema_slow``). This is a momentum trigger, distinct from the RSI-pullback entry
  of ``trend_scanner`` and the bare EMA cross of ``autoscan_ladder``.
* **Many entries (DCA ladder)** — up to ``max_entries`` further entries are added
  on adverse pullbacks spaced by ``entry_spacing_pct``, lowering the average entry.
* **Many take-profits** — an arbitrary number of scaled take-profit legs, configured
  as comma-separated price-move percentages (``tp_levels_pct``) and close fractions
  (``tp_close_pct``). Legs are placed as real exchange TP orders in live mode.
* **Layered / moving stop** — an initial stop that moves to breakeven after a
  configurable number of TP legs and then trails the best price. The stop therefore
  lives at several levels over the life of a trade.
* **Capital-drawdown kill switch (new)** — the strategy tracks account equity against
  the starting capital and, once the drawdown reaches ``max_drawdown_pct`` (default
  60%), it *halts*: it flattens open positions (``flatten_on_halt``) and refuses any
  new entries for the rest of the run. This is the "stop trading after losing 60% of
  capital" behaviour and does not exist anywhere else in the engine.

The decision logic is pure and deterministic: all inputs arrive via
``StrategyContext`` and all per-trade state lives on the instance, keyed by
``(symbol, side)``. Profitability is never guaranteed; the defaults are a sensible,
backtested starting point that should always be validated before live use.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

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
from app.strategies.indicators import ema
from app.strategies.registry import register_strategy


def _parse_decimal_list(raw: str) -> list[Decimal]:
    """Parse a comma-separated list of numbers into ``Decimal`` values.

    Blank entries are skipped and malformed entries are ignored, so a stray comma
    or space in the UI text field never breaks a run.
    """
    out: list[Decimal] = []
    for chunk in str(raw).replace(";", ",").split(","):
        token = chunk.strip()
        if not token:
            continue
        try:
            value = Decimal(token)
        except (InvalidOperation, ValueError):
            continue
        out.append(value)
    return out


class GuardedLadderParams(BaseModel):
    """Tunable parameters (exposed to the frontend as a JSON-schema form).

    Take-profit legs are expressed as comma-separated text so an arbitrary number of
    levels can be configured from the auto-generated form (which renders scalars).
    """

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
        default=8, ge=1, le=50, description="How many coins to actively trade at once."
    )

    # -- trend / regime ------------------------------------------------------ #
    ema_fast: int = Field(default=20, ge=2, description="Fast EMA period.")
    ema_slow: int = Field(default=50, ge=3, description="Slow EMA period.")
    trend_ema: int = Field(default=200, ge=5, description="Higher-trend regime EMA.")

    # -- breakout entry trigger --------------------------------------------- #
    breakout_lookback: int = Field(
        default=20,
        ge=2,
        le=500,
        description="First entry fires when price breaks the high/low of the last N closed bars.",
    )

    # -- multiple entries (DCA ladder) -------------------------------------- #
    # Default to a single entry so live management keeps one clean stop to move
    # (extra DCA adds layer multiple exchange SL/TP orders and caused the larger
    # live losses). DCA is still available by raising this.
    max_entries: int = Field(default=1, ge=1, le=50, description="Max entries per coin/side.")
    entry_spacing_pct: Decimal = Field(
        default=Decimal("0.8"),
        description="Adverse price move (percent) required before adding the next entry.",
    )

    # -- multiple take-profits (price-move percent + close fraction) -------- #
    # Targets are price-move percentages (leverage independent). ROE shown in the
    # UI = price move x leverage. ``tp_close_pct`` closes that fraction of the
    # *remaining* position at each leg; the final leg always closes the rest.
    # Bank half the position at +2%, then let the trailing stop (live and
    # backtest) ride the runner; the +6% leg is just a reachable backstop. With
    # the live breakeven/trailing stop now active, the runner is protected after
    # TP1 instead of giving the gain back to the original stop.
    tp_levels_pct: str = Field(
        default="2.0,6.0",
        description="Comma-separated take-profit price-move percentages (e.g. '2.0,6.0').",
    )
    tp_close_pct: str = Field(
        default="50,100",
        description="Comma-separated close percent of the remainder at each TP leg.",
    )

    # -- layered / moving stop ---------------------------------------------- #
    stop_loss_pct: Decimal = Field(
        default=Decimal("2.0"), description="Initial stop distance (price percent)."
    )
    breakeven_after_tp: int = Field(
        default=1,
        ge=0,
        le=50,
        description="Move the stop to breakeven after this many TP legs hit (0=off).",
    )
    trail_after_tp: int = Field(
        default=1,
        ge=0,
        le=50,
        description="Start trailing the stop after this many TP legs hit (0=off).",
    )
    trail_pct: Decimal = Field(
        default=Decimal("3.0"),
        description="Trailing stop distance (percent) from the best price.",
    )

    # -- capital-drawdown kill switch --------------------------------------- #
    capital_usd: Decimal = Field(
        default=Decimal("50"),
        description="Baseline capital used for the drawdown guard if the live "
        "balance is unavailable at start.",
    )
    max_drawdown_pct: Decimal = Field(
        default=Decimal("60"),
        description="Halt all trading once equity falls this percent below the "
        "starting capital (60 = stop after losing 60%).",
    )
    flatten_on_halt: bool = Field(
        default=True,
        description="When the drawdown halt triggers, close all open positions at market.",
    )

    allow_hedge: bool = Field(
        default=False, description="Allow opposite-direction positions to coexist."
    )

    model_config = {"extra": "ignore"}


def _key(symbol: str, side: PositionSide) -> tuple[str, str]:
    return (symbol, side.value)


@register_strategy("guarded_ladder")
class GuardedLadderStrategy(Strategy):
    """See module docstring."""

    Params = GuardedLadderParams

    def __init__(self, params: GuardedLadderParams | None = None) -> None:
        super().__init__(params)
        self.p: GuardedLadderParams = self.params  # type: ignore[assignment]
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
        # Capital-drawdown kill switch state.
        self._baseline_equity: Decimal | None = None
        self._halted = False
        self._halt_logged = False
        # Parsed TP legs: list of (price_move_pct, close_fraction_of_remainder).
        self._tp_legs = self._build_tp_legs()

    async def on_start(self, context: StrategyContext) -> None:
        self._exchange_protections = context.exchange_protections
        self._capture_baseline(context.account)
        for sym in self._symbols_with_open_position(context.account):
            self._ensure_selected(sym)

    # ------------------------------------------------------------------ #
    # Take-profit leg parsing
    # ------------------------------------------------------------------ #
    def _build_tp_legs(self) -> list[tuple[Decimal, Decimal]]:
        """Parse the comma-separated TP params into ``(move_pct, close_fraction)``.

        ``tp_close_pct`` is paired positionally with ``tp_levels_pct``; missing close
        values default to a full close and the final leg always closes the rest.
        """
        moves = _parse_decimal_list(self.p.tp_levels_pct)
        closes = _parse_decimal_list(self.p.tp_close_pct)
        if not moves:
            moves = [Decimal("1.0")]
        legs: list[tuple[Decimal, Decimal]] = []
        for idx, move in enumerate(moves):
            close_pct = closes[idx] if idx < len(closes) else Decimal("100")
            frac = (close_pct / Decimal(100)).copy_abs()
            if frac <= 0:
                frac = Decimal("1")
            legs.append((move, min(frac, Decimal("1"))))
        # Force the last configured leg to fully close whatever remains.
        last_move, _ = legs[-1]
        legs[-1] = (last_move, Decimal("1"))
        return legs

    def _tp_levels(self) -> list[tuple[Decimal, Decimal]]:
        return self._tp_legs

    # ------------------------------------------------------------------ #
    # Capital-drawdown kill switch
    # ------------------------------------------------------------------ #
    def _capture_baseline(self, account: AccountState) -> None:
        if self._baseline_equity is not None:
            return
        balance = account.balance
        if balance is not None and balance > 0:
            self._baseline_equity = balance
        else:
            self._baseline_equity = self.p.capital_usd

    def _halt_threshold(self) -> Decimal | None:
        if self._baseline_equity is None or self._baseline_equity <= 0:
            return None
        keep = (Decimal(100) - self.p.max_drawdown_pct) / Decimal(100)
        return self._baseline_equity * keep

    def _check_capital_guard(self, context: StrategyContext) -> list[TradeIntent]:
        """Evaluate the drawdown guard; return flatten intents on the halting bar."""
        self._capture_baseline(context.account)
        threshold = self._halt_threshold()
        if threshold is None:
            return []
        equity = context.account.equity(context.market.marks())
        if equity > threshold:
            return []

        newly_halted = not self._halted
        self._halted = True
        if not self._halt_logged:
            self._halt_logged = True
            self._pending_logs.append(
                {
                    "message": (
                        f"capital guard: trading halted — equity {equity:.4f} <= "
                        f"{threshold:.4f} (down {self.p.max_drawdown_pct}% from "
                        f"{self._baseline_equity})"
                    ),
                    "symbol": None,
                    "severity": "error",
                    "context": {
                        "check": "capital_guard_halt",
                        "equity": str(equity),
                        "threshold": str(threshold),
                        "baseline": str(self._baseline_equity),
                        "max_drawdown_pct": str(self.p.max_drawdown_pct),
                    },
                }
            )
        if not newly_halted or not self.p.flatten_on_halt:
            return []
        return self._flatten_intents(context.account)

    def _opened_keys(self) -> set[tuple[str, str]]:
        """(symbol, side) pairs the strategy itself opened and still tracks.

        Per-trade state is only created on our own entries, so this set excludes
        any position the user opened manually on the same account.
        """
        keys: set[tuple[str, str]] = set()
        keys.update(self._last_entry)
        keys.update(self._stop)
        keys.update(self._best)
        keys.update(self._tps_hit)
        return keys

    def _flatten_intents(self, account: AccountState) -> list[TradeIntent]:
        """Close ONLY positions the bot opened (never the user's manual trades)."""
        intents: list[TradeIntent] = []
        for symbol, side_str in sorted(self._opened_keys()):
            side = PositionSide(side_str)
            pos = account.position(symbol, side)
            if pos is None or pos.qty <= 0:
                self._reset_trade((symbol, side_str))
                continue
            intents.append(
                TradeIntent(
                    symbol=symbol,
                    side=Side.SELL if side is PositionSide.LONG else Side.BUY,
                    action=IntentAction.CLOSE,
                    position_side=side,
                    reason="capital guard halt: flatten",
                    tag="halt_flatten",
                )
            )
            self._reset_trade((symbol, side_str))
        return intents

    # ------------------------------------------------------------------ #
    # Universe & selection (mirrors the engine's scan/replacement contract)
    # ------------------------------------------------------------------ #
    def desired_symbols(self, instruments: dict[str, Instrument]) -> list[str]:
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

    def _open_symbol_count(self, account: AccountState) -> int:
        return len(self._symbols_with_open_position(account))

    def is_full(self, account: AccountState) -> bool:
        return self._open_symbol_count(account) >= self.p.max_symbols

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
        self._reset_trade(_key(symbol, position_side))
        if symbol in self._selected:
            self._selected.remove(symbol)

    def _required_history(self) -> int:
        return max(self.p.ema_slow, self.p.trend_ema, self.p.breakout_lookback) + 1

    def warmup_bars(self) -> int:
        return self._required_history() + 10

    def drain_scan_logs(self) -> list[dict]:
        logs = self._pending_logs
        self._pending_logs = []
        return logs

    def _queue_scan_log(self, symbol: str, reason: str, *, check: str, **context) -> None:
        # Log the outcome on every evaluated candle (no dedup): with the scheduled
        # poll this runs once per coin per closed candle, so it is the per-candle
        # "I checked SYMBOL and here is why I did/did not trade" line the user wants.
        if symbol:
            self._last_scan_reason[symbol] = reason
        message = f"checked {symbol}: {reason}" if symbol else reason
        ctx = {"check": check, **context}
        if symbol:
            ctx["symbol"] = symbol
        self._pending_logs.append(
            {"message": message, "symbol": symbol or None, "severity": "info", "context": ctx}
        )

    def _log_decision(self, symbol: str, message: str, **context) -> None:
        """Always-on per-candle decision line (entry/manage), never deduped."""
        ctx = {"check": "decision", "symbol": symbol, **context}
        self._pending_logs.append(
            {
                "message": f"checked {symbol}: {message}",
                "symbol": symbol,
                "severity": "info",
                "context": ctx,
            }
        )

    def _symbols_with_open_position(self, account: AccountState) -> set[str]:
        return {pos.symbol for pos in account.positions.values() if pos.qty > 0}

    def _symbol_is_flat(self, symbol: str, account: AccountState) -> bool:
        return symbol not in self._symbols_with_open_position(account)

    def _slot_free(self, account: AccountState) -> bool:
        return self._open_symbol_count(account) < self.p.max_symbols

    def release_symbol(self, symbol: str, account: AccountState) -> bool:
        if not self._symbol_is_flat(symbol, account):
            return False
        if symbol not in self._selected:
            return False
        self._selected.remove(symbol)
        for side in (PositionSide.LONG, PositionSide.SHORT):
            self._reset_trade(_key(symbol, side))
        return True

    def next_scan_candidate(self, account: AccountState) -> str | None:
        if self._halted or self.is_full(account) or not self._universe:
            return None
        open_syms = self._symbols_with_open_position(account)
        total = len(self._universe)
        for offset in range(total):
            idx = (self._scan_cursor + offset) % total
            symbol = self._universe[idx]
            if symbol in self._selected or symbol in open_syms:
                continue
            self._scan_cursor = (idx + 1) % total
            return symbol
        return None

    def _ensure_selected(self, symbol: str) -> None:
        if symbol not in self._selected:
            self._selected.append(symbol)

    # ------------------------------------------------------------------ #
    # Stop / take-profit helpers
    # ------------------------------------------------------------------ #
    def _initial_stop(self, price: Decimal, side: PositionSide) -> Decimal:
        delta = price * (self.p.stop_loss_pct / Decimal(100))
        return price - delta if side is PositionSide.LONG else price + delta

    def position_levels(
        self, symbol: str, side: object, entry_price: object, leverage: int
    ) -> dict | None:
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
        if not isinstance(side, PositionSide):
            return None
        entry = Decimal(str(entry_price))
        qty = Decimal(str(position_qty))
        if qty <= 0:
            return None
        # Always derive the initial stop from the entry price passed here (the
        # actual fill price in live), not a stored value — otherwise a stale
        # scan-price stop would be placed far from the real entry.
        stop = self._initial_stop(entry, side)
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

        # Capital-drawdown kill switch runs on every closed bar. On the bar that
        # trips the guard it returns flatten intents; afterwards it stays halted.
        halt_flatten = self._check_capital_guard(context)
        if halt_flatten:
            return halt_flatten

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
        if fast is None or slow is None or trend is None:
            return []

        long_regime = price > trend and fast > slow
        short_regime = price < trend and fast < slow

        intents: list[TradeIntent] = []

        # Manage existing positions first (TP legs + stops), both sides. This runs
        # even while halted (without flatten) so resting risk is still respected.
        for side in (PositionSide.LONG, PositionSide.SHORT):
            k = _key(symbol, side)
            pos = context.account.position(symbol, side)
            if pos is None or pos.qty <= 0:
                if self._exchange_protections and (
                    k in self._stop or k in self._tps_hit or k in self._best
                ):
                    self._reset_trade(k)
                continue
            intents.extend(self._manage_position(symbol, side, pos, price, closes))

        if intents:
            for it in intents:
                self._log_decision(
                    symbol,
                    f"{it.action.value} {it.position_side.value} ({it.reason})",
                    action=it.action.value,
                    tag=it.tag,
                )
            return intents

        # No new entries once the capital guard has halted trading.
        if self._halted:
            self._record_scan(symbol, context)
            return intents

        # Entry logic — only one direction per bar.
        if long_regime:
            entry = self._entry_intent(
                symbol, PositionSide.LONG, context.account.position(symbol, PositionSide.LONG),
                price, closes, context.account,
            )
            if entry is not None:
                intents.append(entry)
        elif short_regime:
            entry = self._entry_intent(
                symbol, PositionSide.SHORT, context.account.position(symbol, PositionSide.SHORT),
                price, closes, context.account,
            )
            if entry is not None:
                intents.append(entry)

        if intents:
            for it in intents:
                self._log_decision(
                    symbol,
                    f"OPEN {it.position_side.value} {it.tag} ({it.reason})",
                    action="open",
                    tag=it.tag,
                )
        else:
            self._record_scan(symbol, context)
        return intents

    # ------------------------------------------------------------------ #
    def _breakout_triggered(
        self, side: PositionSide, closes: list[Decimal]
    ) -> bool:
        """True when the latest close breaks the prior ``breakout_lookback`` extreme."""
        lookback = self.p.breakout_lookback
        if len(closes) < lookback + 1:
            return False
        price = closes[-1]
        window = closes[-(lookback + 1):-1]
        if not window:
            return False
        if side is PositionSide.LONG:
            return price > max(window)
        return price < min(window)

    def _entry_intent(
        self,
        symbol: str,
        side: PositionSide,
        pos,
        price: Decimal,
        closes: list[Decimal],
        account: AccountState,
    ) -> TradeIntent | None:
        steps = pos.step_count if pos is not None else 0
        if steps >= self.p.max_entries:
            return None

        if steps == 0:
            if not self._symbol_is_flat(symbol, account):
                return None
            if not self._slot_free(account):
                return None
            # First entry: momentum breakout in the trend direction.
            if not self._breakout_triggered(side, closes):
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
            reason=f"breakout entry step {steps + 1}"
            if steps == 0
            else f"dca pullback entry step {steps + 1}",
            tag=f"entry_{steps + 1}",
        )

    # ------------------------------------------------------------------ #
    def _manage_position(
        self, symbol: str, side: PositionSide, pos, price: Decimal, closes: list[Decimal]
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

        # 2) Take-profit ladder (favourable price move from the average entry).
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
    def _stop_for(
        self,
        side: PositionSide,
        entry: Decimal,
        best: Decimal,
        tps_hit: int,
        current: Decimal | None = None,
    ) -> Decimal:
        """Pure moving-stop math: initial -> breakeven -> trailing.

        ``current`` is the stop already in force (never loosened below it). This is
        shared by the backtest stop manager and the live exchange stop mover so the
        two agree on where the stop should sit.
        """
        stop = current if current is not None else self._initial_stop(entry, side)

        if self.p.breakeven_after_tp and tps_hit >= self.p.breakeven_after_tp:
            stop = max(stop, entry) if side is PositionSide.LONG else min(stop, entry)

        if self.p.trail_after_tp and tps_hit >= self.p.trail_after_tp:
            dist = best * (self.p.trail_pct / Decimal(100))
            trail = best - dist if side is PositionSide.LONG else best + dist
            stop = max(stop, trail) if side is PositionSide.LONG else min(stop, trail)

        return stop

    def _advance_stop(
        self, k: tuple[str, str], side: PositionSide, entry: Decimal, best: Decimal
    ) -> None:
        hits = self._tps_hit.get(k, 0)
        self._stop[k] = self._stop_for(side, entry, best, hits, self._stop.get(k))

    def compute_live_stop(
        self,
        side: object,
        entry_price: object,
        best_price: object,
        tps_filled: int,
    ) -> Decimal | None:
        """Stop price the live exchange SL should sit at after ``tps_filled`` TPs.

        Returns ``None`` until at least one TP leg has filled (before that the
        initial entry stop already rests on the exchange and need not move).
        """
        if not isinstance(side, PositionSide):
            return None
        if tps_filled <= 0:
            return None
        entry = Decimal(str(entry_price))
        best = Decimal(str(best_price))
        return self._stop_for(side, entry, best, tps_filled, None)

    def _reset_trade(self, k: tuple[str, str]) -> None:
        self._tps_hit.pop(k, None)
        self._stop.pop(k, None)
        self._best.pop(k, None)
        self._last_entry.pop(k, None)

    # ------------------------------------------------------------------ #
    def _record_scan(self, symbol: str, context: StrategyContext) -> None:
        if self._halted:
            self._queue_scan_log(
                symbol, "capital guard halted; not trading", check="halted"
            )
            return
        closes = context.market.closes(symbol)
        need = self._required_history()
        have = len(closes)
        if have < need:
            self._queue_scan_log(
                symbol,
                f"not enough history ({have}/{need} bars)",
                check="insufficient_history",
                bars=have,
                required=need,
            )
            return
        price = closes[-1]
        fast = ema(closes, self.p.ema_fast)
        slow = ema(closes, self.p.ema_slow)
        trend = ema(closes, self.p.trend_ema)
        if fast is None or slow is None or trend is None:
            self._queue_scan_log(symbol, "indicators not ready", check="indicators")
            return

        # Already holding this symbol -> not seeking a new entry (TP/SL manage it).
        for s in (PositionSide.LONG, PositionSide.SHORT):
            held = context.account.position(symbol, s)
            if held is not None and held.qty > 0:
                self._queue_scan_log(
                    symbol,
                    f"in {s.value} position (step {held.step_count}/{self.p.max_entries}); "
                    "holding, exchange TP/SL active",
                    check="in_position",
                    side=s.value,
                )
                return
        if not self._slot_free(context.account):
            open_syms = self._symbols_with_open_position(context.account)
            self._queue_scan_log(
                symbol,
                f"no free slot ({len(open_syms)}/{self.p.max_symbols} positions open)",
                check="slots_full",
            )
            return

        long_regime = price > trend and fast > slow
        short_regime = price < trend and fast < slow
        if not long_regime and not short_regime:
            self._queue_scan_log(symbol, "no trend (regime filter)", check="no_trend")
            return
        side = PositionSide.LONG if long_regime else PositionSide.SHORT
        if not self._breakout_triggered(side, closes):
            self._queue_scan_log(
                symbol,
                f"{side.value} regime: waiting for breakout of last "
                f"{self.p.breakout_lookback} bars",
                check="waiting_breakout",
            )
