"""The trading engine.

One loop serves live, dry-run and backtest. The mode only determines which
``Clock``, ``MarketDataFeed`` and ``Broker`` are injected (REQ-001/003). Per market
event the engine:

1. updates rolling market state and the broker's mark price,
2. asks the strategy for intents (pure decision),
3. sizes each intent via the risk sizer (REQ-007),
4. submits sized orders to the broker,
5. emits signal/order/fill/position/equity events to the bus (REQ-004/010).

The engine never touches the database; everything is published as events.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from app.core.logging import get_logger
from app.domain.clock import Clock
from app.domain.interfaces import Broker, MarketDataFeed
from app.domain.market import MarketState
from app.domain.types import (
    AccountState,
    Bar,
    Instrument,
    MarketEvent,
    MarketEventType,
    Mode,
    Order,
    OrderStatus,
    PositionSide,
)
from app.events.bus import EventSink
from app.events.schemas import (
    CandleEvent,
    EquityEvent,
    ErrorEvent,
    FillEvent,
    MarketPriceEvent,
    OrderEvent,
    PositionEvent,
    RunEvent,
    SignalEvent,
    SymbolSummaryEvent,
    TradeLevelEvent,
    WatchlistEvent,
)
from app.risk.sizer import RiskSizer
from app.strategies.base import Strategy, StrategyContext

log = get_logger(__name__)


@dataclass(slots=True)
class EngineSummary:
    """Result of a run, handy for reporting and the equivalence test."""

    run_id: str
    mode: str
    strategy: str
    events: int = 0
    orders: int = 0
    fills: int = 0
    rejected: int = 0
    final_balance: Decimal = Decimal("0")
    final_equity: Decimal = Decimal("0")
    equity_curve: list[tuple[str, Decimal]] = field(default_factory=list)


class Engine:
    """Mode-agnostic trading engine."""

    def __init__(
        self,
        *,
        mode: Mode,
        strategy: Strategy,
        sizer: RiskSizer,
        broker: Broker,
        feed: MarketDataFeed,
        clock: Clock,
        sink: EventSink,
        run_id: str | None = None,
        max_history: int = 1000,
        history: object | None = None,
        interval: str = "1m",
    ) -> None:
        self.mode = mode
        self.strategy = strategy
        self.sizer = sizer
        self.broker = broker
        self.feed = feed
        self.clock = clock
        self.sink = sink
        self.run_id = run_id or uuid.uuid4().hex
        self.market = MarketState(max_history=max_history)
        # Optional historical-kline provider (BitunixRest) used to scan coins one
        # by one before going live. ``None`` disables the pre-scan (e.g. backtest).
        self._history = history
        self._stop = False
        self._universe: list[str] = []
        self._selected: list[str] = []
        self._scanning: list[str] = []
        self._target = 0
        self._interval = interval
        self._scanned_once = False

    def request_stop(self) -> None:
        """Ask the loop to finish after the current event (live use)."""
        self._stop = True

    async def run(self) -> EngineSummary:
        instruments = await self.feed.instruments()
        summary = EngineSummary(
            run_id=self.run_id, mode=self.mode.value, strategy=self.strategy.name
        )
        await self._emit_run("started")
        await self._emit_log(
            "engine",
            "info",
            f"engine started: {self.strategy.name} ({self.mode.value})",
            context={"strategy": self.strategy.name},
        )
        started = False
        try:
            # Live/dry: scan the ranked universe one coin at a time (fetch history,
            # evaluate, decide) before streaming live data for the selected coins.
            if self.mode is not Mode.BACKTEST and self._history is not None:
                await self._scan_and_select(instruments, summary)
                started = True
            async for event in self.feed.stream():
                if self._stop:
                    break
                if not started:
                    await self._on_start(event, instruments)
                    started = True
                await self._handle_event(event, instruments, summary)
            if self.mode is Mode.BACKTEST and summary.events == 0:
                await self._emit_log(
                    "engine",
                    "error",
                    "no market events processed; check symbols, date range "
                    "and exchange connectivity",
                    context={"mode": self.mode.value},
                )
            await self._finalize(summary)
            await self._emit_log(
                "engine",
                "info",
                "engine finished: "
                f"events={summary.events} orders={summary.orders} fills={summary.fills}",
                context={
                    "events": summary.events,
                    "orders": summary.orders,
                    "fills": summary.fills,
                    "rejected": summary.rejected,
                },
            )
            await self._emit_run("finished")
        except Exception as exc:  # noqa: BLE001 - report then re-raise
            await self._emit_error("engine", str(exc))
            await self._emit_run("failed", detail=str(exc))
            raise
        return summary

    # ------------------------------------------------------------------ #
    async def _on_start(self, event: MarketEvent, instruments: dict[str, Instrument]) -> None:
        account = await self.broker.account()
        ctx = StrategyContext(
            event=event,
            now=self.clock.now(),
            account=account,
            instruments=instruments,
            market=self.market,
            interval=self._interval,
        )
        await self.strategy.on_start(ctx)
        if event.bar is not None:
            self._interval = event.bar.interval
        self._universe = self.strategy.desired_symbols(instruments)

        snap = self.strategy.selection_snapshot(ctx)
        if snap is None:
            # Strategy does not do dynamic selection: the whole universe is the
            # tradeable set and the watchlist is complete immediately.
            self._selected = list(self._universe)
            self._scanning = []
            self._target = len(self._universe)
        else:
            self._selected = list(snap.get("selected", []))
            self._scanning = list(snap.get("scanning", self._universe))
            self._target = int(snap.get("target", len(self._selected)))

        await self._emit_watchlist()
        await self._emit_log(
            "engine",
            "info",
            f"scanning {len(self._scanning)} coins "
            f"(selected {len(self._selected)}/{self._target})",
            context={
                "selected": self._selected,
                "scanning": self._scanning[:20],
                "scanning_count": len(self._scanning),
                "target": self._target,
                "interval": self._interval,
            },
        )
        for sym in self._selected:
            await self._emit_symbol_summary(sym, status="selected")

    async def _handle_event(
        self,
        event: MarketEvent,
        instruments: dict[str, Instrument],
        summary: EngineSummary,
    ) -> None:
        summary.events += 1

        # 1) update market state + broker mark
        if event.type is MarketEventType.BAR and event.bar is not None:
            self.market.update_bar(event.bar)
        else:
            self.market.update_price(event.symbol, event.price)
        await self.broker.set_mark(event.symbol, event.price)
        await self._emit_market(event)
        if event.type is MarketEventType.BAR and event.bar is not None:
            self._interval = event.bar.interval
            await self._emit_candle(event.bar)
            if event.bar.symbol in self._selected:
                await self._emit_symbol_summary(event.bar.symbol)

        # 2) strategy decision (pure)
        account = await self.broker.account()
        ctx = StrategyContext(
            event=event,
            now=self.clock.now(),
            account=account,
            instruments=instruments,
            market=self.market,
            interval=self._interval,
        )
        self.strategy.scan_diagnostics(ctx)
        await self._flush_strategy_logs()
        intents = self.strategy.on_event(ctx)
        await self._flush_strategy_logs()

        # 3-4) size + submit each intent
        await self._process_intents(intents, instruments, summary)

        # 5) refresh dynamic coin selection (may grow the watchlist)
        await self._refresh_selection(ctx)
        await self._flush_strategy_logs()
        # 6) keep TP/SL overlays live for open positions on this symbol
        if event.type is MarketEventType.BAR and event.bar is not None:
            await self._emit_open_levels(event.bar.symbol)

        # 7) equity snapshot
        await self._emit_equity()

    async def _process_intents(
        self,
        intents: list,
        instruments: dict[str, Instrument],
        summary: EngineSummary,
    ) -> None:
        for intent in intents:
            await self._emit_signal(intent)
            await self._emit_log(
                "signal",
                "info",
                f"signal {intent.action.value}: {intent.side.value} {intent.symbol}",
                context={
                    "symbol": intent.symbol,
                    "side": intent.side.value,
                    "action": intent.action.value,
                    "position_side": intent.position_side.value,
                    "reason": intent.reason,
                    "tag": intent.tag,
                },
            )
            instrument = instruments.get(intent.symbol)
            if instrument is None:
                await self._emit_error("sizer", f"unknown instrument {intent.symbol}")
                continue
            price = self.market.last_price(intent.symbol)
            if price is None:
                continue
            leverage = self.sizer.params.base_leverage
            tps, stops = self._levels(intent.symbol, intent.position_side, price, leverage)
            if not stops and intent.stop_price is not None:
                stops = [intent.stop_price]
            await self._emit_trade_level(
                symbol=intent.symbol,
                position_side=intent.position_side.value,
                current_price=price,
                planned_entry=price,
                take_profits=tps,
                stops=stops,
                source="strategy",
            )
            account = await self.broker.account()  # refresh between intents
            result = self.sizer.size(intent, account, instrument, price)
            if not result.ok or result.request is None:
                summary.rejected += 1
                await self._emit_signal_rejection(intent, result.reason)
                continue
            order = await self.broker.submit(result.request)
            await self._emit_order(order)
            await self._emit_log(
                "order",
                "info",
                f"order {order.status.value}: {order.side.value} {order.symbol} qty={order.qty}",
                context={
                    "order_id": order.id,
                    "symbol": order.symbol,
                    "side": order.side.value,
                    "position_side": order.position_side.value,
                    "qty": order.qty,
                    "leverage": order.leverage,
                    "reason": order.reason,
                    "tag": order.tag,
                },
            )
            summary.orders += 1
            if order.status is not OrderStatus.FILLED:
                await self._emit_symbol_summary(order.symbol, status="pending_order")
            if order.status is OrderStatus.FILLED:
                summary.fills += len(order.fills)
                for fill in order.fills:
                    await self._emit_fill(fill)
                    await self._emit_log(
                        "fill",
                        "info",
                        f"fill {fill.side.value}: {fill.symbol} qty={fill.qty} @ {fill.price}",
                        context={
                            "order_id": fill.order_id,
                            "symbol": fill.symbol,
                            "side": fill.side.value,
                            "position_side": fill.position_side.value,
                            "qty": fill.qty,
                            "price": fill.price,
                            "fee": fill.fee,
                            "realized_pnl": fill.realized_pnl,
                        },
                    )
                await self._emit_positions_for(order.symbol)
                entry = order.avg_fill_price or price
                tps, stops = self._levels(
                    order.symbol, order.position_side, entry, order.leverage
                )
                if not stops and result.request.stop_price is not None:
                    stops = [result.request.stop_price]
                await self._emit_trade_level(
                    symbol=order.symbol,
                    position_side=order.position_side.value,
                    current_price=self.market.last_price(order.symbol),
                    actual_entry=entry,
                    take_profits=tps,
                    stops=stops,
                    source="order",
                )
                await self._emit_symbol_summary(order.symbol, status="in_position")

    async def _scan_and_select(
        self, instruments: dict[str, Instrument], summary: EngineSummary
    ) -> None:
        """Walk the volume-ranked universe one coin at a time (live/dry).

        For each coin: fetch its recent history, evaluate the strategy on it, log
        the decision (entered / why not), and move on. Stops at ``max_symbols``
        selected or when the universe is exhausted. Runs exactly once per engine,
        so the search can never restart and overlap itself.
        """
        if self._scanned_once:
            return
        self._scanned_once = True

        account = await self.broker.account()
        placeholder = MarketEvent(
            type=MarketEventType.TICK, ts=self.clock.now(), symbol=""
        )
        ctx0 = StrategyContext(
            event=placeholder,
            now=self.clock.now(),
            account=account,
            instruments=instruments,
            market=self.market,
            interval=self._interval,
        )
        await self.strategy.on_start(ctx0)
        self._universe = self.strategy.desired_symbols(instruments)
        snap = self.strategy.selection_snapshot(ctx0)
        self._selected = list(snap.get("selected", [])) if snap else []
        self._target = int(snap.get("target", len(self._universe))) if snap else len(
            self._universe
        )
        self._scanning = [s for s in self._universe if s not in self._selected]
        await self._emit_watchlist()
        total = len(self._universe)
        await self._emit_log(
            "strategy",
            "info",
            f"scanning top {total} coins one by one by 24h volume "
            f"(target {self._target}, {self._interval} candles)",
            context={"total": total, "target": self._target, "interval": self._interval},
        )

        warmup = max(int(getattr(self.strategy, "warmup_bars", lambda: 0)() or 0), 1)
        cadence = int(getattr(self.strategy.params, "scan_universe", 30) or 30)
        scanned = 0
        for symbol in self._universe:
            if self._stop:
                break
            if self._target and len(self._selected) >= self._target:
                await self._emit_log(
                    "strategy",
                    "info",
                    f"target reached: {len(self._selected)}/{self._target} coins "
                    f"selected after scanning {scanned}/{total}",
                    context={"selected": list(self._selected)},
                )
                break
            scanned += 1
            bars = await self._fetch_history(symbol, warmup)
            if not bars:
                await self._emit_log(
                    "strategy",
                    "info",
                    f"scan {symbol}: no historical data, skipping",
                    context={"symbol": symbol, "check": "no_data"},
                )
            else:
                for bar in bars:
                    self.market.update_bar(bar)
                last = bars[-1]
                await self.broker.set_mark(symbol, last.close)
                event = MarketEvent(
                    type=MarketEventType.BAR,
                    ts=last.open_time,
                    symbol=symbol,
                    bar=last,
                )
                acct = await self.broker.account()
                ctx = StrategyContext(
                    event=event,
                    now=self.clock.now(),
                    account=acct,
                    instruments=instruments,
                    market=self.market,
                    interval=self._interval,
                )
                intents = self.strategy.on_event(ctx)
                await self._flush_strategy_logs()
                await self._process_intents(intents, instruments, summary)
                await self._refresh_selection(ctx)
            if scanned % cadence == 0:
                await self._emit_log(
                    "strategy",
                    "info",
                    f"scanned {scanned}/{total} coins, "
                    f"selected {len(self._selected)}/{self._target}",
                    context={
                        "scanned": scanned,
                        "total": total,
                        "selected": len(self._selected),
                        "target": self._target,
                    },
                )

        await self._emit_log(
            "strategy",
            "info",
            f"scan complete: evaluated {scanned}/{total} coins, "
            f"selected {len(self._selected)}/{self._target}",
            context={
                "scanned": scanned,
                "total": total,
                "selected": list(self._selected),
                "target": self._target,
            },
        )
        self._scanning = []
        await self._emit_watchlist()
        if self._selected:
            await self._ensure_feed_symbols(self._selected)
        else:
            await self._emit_log(
                "strategy",
                "warn",
                f"no tradeable setups found in the top {total} coins; "
                "nothing to trade right now",
                context={"total": total},
            )

    async def _fetch_history(self, symbol: str, count: int) -> list[Bar]:
        if self._history is None:
            return []
        try:
            bars = await self._history.get_recent_klines(symbol, self._interval, count)
        except Exception as exc:  # noqa: BLE001 - skip coins we can't fetch
            await self._emit_log(
                "strategy",
                "warn",
                f"scan {symbol}: history fetch failed: {exc}",
                context={"symbol": symbol, "check": "fetch_failed"},
            )
            return []
        return list(bars)

    async def _finalize(self, summary: EngineSummary) -> None:
        account = await self.broker.account()
        marks = self.market.marks()
        summary.final_balance = account.balance
        summary.final_equity = account.equity(marks)
        for sym in {key[0] for key in account.positions}:
            await self._emit_positions_for(sym)
        await self._emit_equity(track=summary)

    # ------------------------------------------------------------------ #
    # Emitters (all go through the event bus; never the DB)
    # ------------------------------------------------------------------ #
    async def _emit_run(self, status: str, detail: str = "") -> None:
        await self.sink.emit(
            RunEvent(
                run_id=self.run_id,
                mode=self.mode.value,
                ts=self.clock.now(),
                strategy=self.strategy.name,
                status=status,
                detail=detail,
            )
        )

    async def _emit_signal(self, intent) -> None:
        await self.sink.emit(
            SignalEvent(
                run_id=self.run_id,
                mode=self.mode.value,
                ts=self.clock.now(),
                strategy=self.strategy.name,
                symbol=intent.symbol,
                side=intent.side.value,
                action=intent.action.value,
                weight=intent.weight,
                reason=intent.reason,
                tag=intent.tag,
            )
        )

    async def _emit_market(self, event: MarketEvent) -> None:
        await self.sink.emit(
            MarketPriceEvent(
                run_id=self.run_id,
                mode=self.mode.value,
                ts=event.ts,
                symbol=event.symbol,
                price=event.price,
            )
        )

    async def _emit_candle(self, bar: Bar) -> None:
        await self.sink.emit(
            CandleEvent(
                run_id=self.run_id,
                mode=self.mode.value,
                ts=self.clock.now(),
                symbol=bar.symbol,
                interval=bar.interval,
                open_time=bar.open_time,
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                volume=bar.volume,
                closed=True,
            )
        )

    async def _emit_watchlist(self) -> None:
        await self.sink.emit(
            WatchlistEvent(
                run_id=self.run_id,
                mode=self.mode.value,
                ts=self.clock.now(),
                symbols=list(self._selected),
                scanning=list(self._scanning),
                target=self._target,
                complete=len(self._selected) >= self._target > 0,
                interval=self._interval,
                strategy=self.strategy.name,
            )
        )

    def _levels(
        self, symbol: str, side: PositionSide, entry: Decimal, leverage: int
    ) -> tuple[list[Decimal], list[Decimal]]:
        """Resolve take-profit/stop price levels for a symbol via the strategy.

        Falls back to a single leverage-adjusted TP when the strategy does not
        expose multi-level data.
        """
        lv = self.strategy.position_levels(symbol, side, entry, leverage)
        if lv is not None:
            return list(lv.get("take_profits", [])), list(lv.get("stops", []))
        tp = self._take_profit_price(entry, side, leverage)
        return ([tp] if tp is not None else []), []

    async def _refresh_selection(self, ctx: StrategyContext) -> None:
        snap = self.strategy.selection_snapshot(ctx)
        if snap is None:
            return
        selected = list(snap.get("selected", []))
        if selected == self._selected:
            return
        newly = [s for s in selected if s not in self._selected]
        self._selected = selected
        self._target = int(snap.get("target", len(selected)))
        await self._emit_watchlist()
        for sym in newly:
            await self._emit_symbol_summary(sym, status="selected")
            await self._emit_log(
                "engine",
                "info",
                f"coin selected for trading: {sym} ({len(selected)}/{self._target})",
                context={"selected": selected, "target": self._target},
            )

    async def _ensure_feed_symbols(self, symbols: list[str]) -> None:
        if not symbols:
            return
        added = await self.feed.ensure_symbols(symbols)
        if not added:
            return
        await self._emit_log(
            "strategy",
            "info",
            f"now streaming live data for {len(added)} selected coins",
            context={"symbols": added},
        )

    async def _emit_open_levels(self, symbol: str) -> None:
        """Re-emit TP/SL overlays for any open position on ``symbol`` (live stop)."""
        account = await self.broker.account()
        price = self.market.last_price(symbol)
        for (sym, side), pos in account.positions.items():
            if sym != symbol or pos.qty <= 0:
                continue
            tps, stops = self._levels(sym, side, pos.entry_price, pos.leverage)
            await self._emit_trade_level(
                symbol=sym,
                position_side=side.value,
                current_price=price,
                actual_entry=pos.entry_price,
                take_profits=tps,
                stops=stops,
                source="position",
            )

    async def _emit_trade_level(
        self,
        *,
        symbol: str,
        position_side: str | None = None,
        current_price: Decimal | None = None,
        planned_entry: Decimal | None = None,
        actual_entry: Decimal | None = None,
        take_profit: Decimal | None = None,
        stop_loss: Decimal | None = None,
        take_profits: list[Decimal] | None = None,
        stops: list[Decimal] | None = None,
        source: str = "engine",
    ) -> None:
        tps = take_profits or ([take_profit] if take_profit is not None else [])
        sls = stops or ([stop_loss] if stop_loss is not None else [])
        await self.sink.emit(
            TradeLevelEvent(
                run_id=self.run_id,
                mode=self.mode.value,
                ts=self.clock.now(),
                symbol=symbol,
                position_side=position_side,
                current_price=current_price,
                planned_entry=planned_entry,
                actual_entry=actual_entry,
                take_profit=tps[0] if tps else None,
                stop_loss=sls[0] if sls else None,
                take_profits=tps,
                stops=sls,
                source=source,
            )
        )

    async def _emit_symbol_summary(
        self, symbol: str, *, status: str | None = None, last_signal_reason: str = ""
    ) -> None:
        account = await self.broker.account()
        marks = self.market.marks()
        mark = marks.get(symbol)
        positions = [
            pos for (sym, _side), pos in account.positions.items() if sym == symbol
        ]
        pos = positions[0] if positions else None
        last_price = mark or (pos.entry_price if pos is not None else None)
        await self.sink.emit(
            SymbolSummaryEvent(
                run_id=self.run_id,
                mode=self.mode.value,
                ts=self.clock.now(),
                symbol=symbol,
                status=status
                or ("in_position" if pos is not None and pos.qty > 0 else "scanning"),
                last_price=last_price,
                position_side=pos.position_side.value if pos is not None else None,
                unrealized_pnl=pos.unrealized_pnl(last_price)
                if pos is not None and last_price is not None
                else None,
                realized_pnl=pos.realized_pnl if pos is not None else None,
                step_count=pos.step_count if pos is not None else None,
                max_steps=self._max_steps(),
                last_signal_reason=last_signal_reason,
            )
        )

    def _max_steps(self) -> int | None:
        steps = getattr(self.strategy.params, "ladder_steps", None)
        return int(steps) if steps is not None else None

    def _take_profit_price(
        self, price: Decimal | None, position_side: PositionSide, leverage: int
    ) -> Decimal | None:
        """Convert the strategy's take-profit % (ROE on margin) to a price level.

        ``take_profit_pct`` is return on margin, so the required price move is
        ``pct / leverage`` (REQ-007). Returns ``None`` if the strategy has no TP.
        """
        if price is None:
            return None
        pct = getattr(self.strategy.params, "take_profit_pct", None)
        if pct is None:
            return None
        lev = max(int(leverage), 1)
        move = (Decimal(str(pct)) / Decimal(lev)) / Decimal(100)
        if position_side is PositionSide.LONG:
            return price * (Decimal(1) + move)
        return price * (Decimal(1) - move)

    async def _emit_signal_rejection(self, intent, reason: str) -> None:
        await self._emit_log(
            "risk",
            "warn",
            f"intent rejected: {reason}",
            context={"symbol": intent.symbol, "tag": intent.tag},
        )

    async def _emit_order(self, order: Order) -> None:
        await self.sink.emit(
            OrderEvent(
                run_id=self.run_id,
                mode=self.mode.value,
                ts=order.ts,
                order_id=order.id,
                client_id=order.client_id,
                symbol=order.symbol,
                side=order.side.value,
                position_side=order.position_side.value,
                order_type=order.order_type.value,
                qty=order.qty,
                price=order.price,
                leverage=order.leverage,
                status=order.status.value,
                filled_qty=order.filled_qty,
                avg_fill_price=order.avg_fill_price,
                reduce_only=order.reduce_only,
                reason=order.reason,
                tag=order.tag,
            )
        )

    async def _emit_fill(self, fill: FillEvent | object) -> None:
        await self.sink.emit(
            FillEvent(
                run_id=self.run_id,
                mode=self.mode.value,
                ts=fill.ts,  # type: ignore[union-attr]
                order_id=fill.order_id,  # type: ignore[union-attr]
                symbol=fill.symbol,  # type: ignore[union-attr]
                side=fill.side.value,  # type: ignore[union-attr]
                position_side=fill.position_side.value,  # type: ignore[union-attr]
                qty=fill.qty,  # type: ignore[union-attr]
                price=fill.price,  # type: ignore[union-attr]
                fee=fill.fee,  # type: ignore[union-attr]
                realized_pnl=fill.realized_pnl,  # type: ignore[union-attr]
            )
        )

    async def _emit_positions_for(self, symbol: str) -> None:
        account = await self.broker.account()
        marks = self.market.marks()
        for (sym, side), pos in account.positions.items():
            if sym != symbol:
                continue
            mark = marks.get(sym, pos.entry_price)
            await self.sink.emit(
                PositionEvent(
                    run_id=self.run_id,
                    mode=self.mode.value,
                    ts=self.clock.now(),
                    symbol=sym,
                    position_side=side.value,
                    qty=pos.qty,
                    entry_price=pos.entry_price,
                    mark_price=mark,
                    leverage=pos.leverage,
                    margin=pos.margin,
                    unrealized_pnl=pos.unrealized_pnl(mark),
                    realized_pnl=pos.realized_pnl,
                    step_count=pos.step_count,
                )
            )

    async def _emit_equity(self, track: EngineSummary | None = None) -> None:
        account: AccountState = await self.broker.account()
        marks = self.market.marks()
        equity = account.equity(marks)
        ts = self.clock.now()
        await self.sink.emit(
            EquityEvent(
                run_id=self.run_id,
                mode=self.mode.value,
                ts=ts,
                balance=account.balance,
                equity=equity,
                used_margin=account.used_margin(),
                unrealized_pnl=account.unrealized_pnl(marks),
                open_positions=len(account.positions),
            )
        )
        if track is not None:
            track.equity_curve.append((ts.isoformat(), equity))

    def _log_context(self, context: dict[str, Any] | None) -> dict[str, Any]:
        def convert(value: Any) -> Any:
            if isinstance(value, Decimal):
                return str(value)
            if isinstance(value, datetime):
                return value.isoformat()
            if isinstance(value, dict):
                return {k: convert(v) for k, v in value.items()}
            if isinstance(value, (list, tuple)):
                return [convert(v) for v in value]
            return value

        return convert(context or {})

    async def _flush_strategy_logs(self) -> None:
        for entry in self.strategy.drain_scan_logs():
            ctx = dict(entry.get("context") or {})
            symbol = entry.get("symbol")
            if symbol and "symbol" not in ctx:
                ctx["symbol"] = symbol
            await self._emit_log(
                "strategy",
                str(entry.get("severity", "info")),
                str(entry["message"]),
                context=ctx,
            )

    async def _emit_log(
        self,
        source: str,
        severity: str,
        message: str,
        *,
        detail: str = "",
        context: dict[str, Any] | None = None,
    ) -> None:
        await self.sink.emit(
            ErrorEvent(
                run_id=self.run_id,
                mode=self.mode.value,
                ts=self.clock.now(),
                source=source,
                severity=severity,
                message=message,
                detail=detail,
                context=self._log_context(context),
            )
        )

    async def _emit_error(self, source: str, message: str, detail: str = "") -> None:
        await self._emit_log(source, "error", message, detail=detail)
