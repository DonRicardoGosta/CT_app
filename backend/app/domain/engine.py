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
from decimal import Decimal

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
        self._stop = False
        self._watch_symbols: list[str] = []
        self._interval = "1m"

    def request_stop(self) -> None:
        """Ask the loop to finish after the current event (live use)."""
        self._stop = True

    async def run(self) -> EngineSummary:
        instruments = await self.feed.instruments()
        summary = EngineSummary(
            run_id=self.run_id, mode=self.mode.value, strategy=self.strategy.name
        )
        await self._emit_run("started")
        started = False
        try:
            async for event in self.feed.stream():
                if self._stop:
                    break
                if not started:
                    await self._on_start(event, instruments)
                    started = True
                await self._handle_event(event, instruments, summary)
            await self._finalize(summary)
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
        )
        await self.strategy.on_start(ctx)
        if event.bar is not None:
            self._interval = event.bar.interval
        self._watch_symbols = self.strategy.desired_symbols(instruments)
        await self._emit_watchlist()
        for sym in self._watch_symbols:
            await self._emit_symbol_summary(sym, status="scanning")

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
            await self._emit_candle(event.bar)
            if not self._watch_symbols or event.bar.symbol in self._watch_symbols:
                await self._emit_symbol_summary(event.bar.symbol)

        # 2) strategy decision (pure)
        account = await self.broker.account()
        ctx = StrategyContext(
            event=event,
            now=self.clock.now(),
            account=account,
            instruments=instruments,
            market=self.market,
        )
        intents = self.strategy.on_event(ctx)

        # 3-4) size + submit each intent
        for intent in intents:
            await self._emit_signal(intent)
            instrument = instruments.get(intent.symbol)
            if instrument is None:
                await self._emit_error("sizer", f"unknown instrument {intent.symbol}")
                continue
            price = self.market.last_price(intent.symbol)
            if price is None:
                continue
            leverage = self.sizer.params.base_leverage
            await self._emit_trade_level(
                symbol=intent.symbol,
                position_side=intent.position_side.value,
                current_price=price,
                planned_entry=price,
                stop_loss=intent.stop_price,
                take_profit=self._take_profit_price(price, intent.position_side, leverage),
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
            summary.orders += 1
            if order.status is not OrderStatus.FILLED:
                await self._emit_symbol_summary(order.symbol, status="pending_order")
            if order.status is OrderStatus.FILLED:
                summary.fills += len(order.fills)
                for fill in order.fills:
                    await self._emit_fill(fill)
                await self._emit_positions_for(order.symbol)
                entry = order.avg_fill_price or price
                await self._emit_trade_level(
                    symbol=order.symbol,
                    position_side=order.position_side.value,
                    current_price=self.market.last_price(order.symbol),
                    actual_entry=entry,
                    stop_loss=result.request.stop_price,
                    take_profit=self._take_profit_price(
                        entry, order.position_side, order.leverage
                    ),
                    source="order",
                )
                await self._emit_symbol_summary(order.symbol, status="in_position")

        # 5) equity snapshot
        await self._emit_equity()

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
                symbols=list(self._watch_symbols),
                interval=self._interval,
                strategy=self.strategy.name,
            )
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
        source: str = "engine",
    ) -> None:
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
                take_profit=take_profit,
                stop_loss=stop_loss,
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
        await self.sink.emit(
            ErrorEvent(
                run_id=self.run_id,
                mode=self.mode.value,
                ts=self.clock.now(),
                source="risk",
                severity="info",
                message=f"intent rejected: {reason}",
                context={"symbol": intent.symbol, "tag": intent.tag},
            )
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

    async def _emit_error(self, source: str, message: str, detail: str = "") -> None:
        await self.sink.emit(
            ErrorEvent(
                run_id=self.run_id,
                mode=self.mode.value,
                ts=self.clock.now(),
                source=source,
                severity="error",
                message=message,
                detail=detail,
            )
        )
