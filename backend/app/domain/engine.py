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
    Instrument,
    MarketEvent,
    MarketEventType,
    Mode,
    Order,
    OrderStatus,
)
from app.events.bus import EventSink
from app.domain.tp_sl import take_profit_price as calc_tp_price
from app.domain.tp_sl import stop_loss_price as calc_sl_price
from app.events.schemas import (
    EquityEvent,
    ErrorEvent,
    FillEvent,
    OrderEvent,
    PositionEvent,
    RunEvent,
    SignalEvent,
    StrategyPlanEvent,
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
            if self.mode is Mode.BACKTEST and summary.events == 0:
                await self._emit_error(
                    "engine",
                    "No bars processed — historical data was empty after load. "
                    "Try BTCUSDT with a recent date range, or check Logs for kline fetch errors.",
                    detail="backtest_empty",
                )
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
        ctx = self._make_context(event, account, instruments)
        await self.strategy.on_start(ctx)
        await self._emit_plan(ctx)

    def _make_context(
        self,
        event: MarketEvent,
        account: AccountState,
        instruments: dict[str, Instrument],
    ) -> StrategyContext:
        return StrategyContext(
            event=event,
            now=self.clock.now(),
            account=account,
            instruments=instruments,
            market=self.market,
            leverage=self.sizer.params.base_leverage,
        )

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

        # 2) strategy decision (pure)
        account = await self.broker.account()
        ctx = self._make_context(event, account, instruments)
        intents = self.strategy.on_event(ctx)

        # 3-4) size + submit each intent
        for intent in intents:
            price = self.market.last_price(intent.symbol)
            lev = self.sizer.params.base_leverage
            await self._emit_signal(intent, price=price, leverage=lev)
            instrument = instruments.get(intent.symbol)
            if instrument is None:
                await self._emit_error("sizer", f"unknown instrument {intent.symbol}")
                continue
            price = self.market.last_price(intent.symbol)
            if price is None:
                continue
            account = await self.broker.account()  # refresh between intents
            result = self.sizer.size(intent, account, instrument, price)
            if not result.ok or result.request is None:
                summary.rejected += 1
                await self._emit_signal_rejection(intent, result.reason)
                continue
            order = await self.broker.submit(result.request)
            await self._emit_order(order)
            summary.orders += 1
            if order.status is OrderStatus.FILLED:
                summary.fills += len(order.fills)
                for fill in order.fills:
                    await self._emit_fill(fill)
                await self._emit_positions_for(order.symbol)

        # 5) equity snapshot + trading plan for UI
        await self._emit_equity()
        if event.type is MarketEventType.BAR:
            await self._emit_plan(ctx)

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

    async def _emit_signal(self, intent, *, price=None, leverage: int = 1) -> None:
        mark = price
        sl = intent.stop_price
        tp = None
        if mark is not None and hasattr(self.strategy, "p"):
            p = self.strategy.p  # type: ignore[attr-defined]
            tp = calc_tp_price(mark, intent.position_side, p.take_profit_pct, leverage)
            if sl is None:
                sl = calc_sl_price(mark, intent.position_side, p.stop_loss_pct, leverage)
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
                position_side=intent.position_side.value,
                mark_price=mark,
                stop_price=sl,
                take_profit_price=tp,
                leverage=leverage,
            )
        )

    async def _emit_plan(self, ctx: StrategyContext) -> None:
        plan = self.strategy.plan_snapshot(ctx, self.sizer.params.base_leverage)
        if not plan:
            return
        await self.sink.emit(
            StrategyPlanEvent(
                run_id=self.run_id,
                mode=self.mode.value,
                ts=self.clock.now(),
                strategy=self.strategy.name,
                selected_symbols=plan.get("selected_symbols", []),
                leverage=int(plan.get("leverage", 1)),
                stop_loss_pct_margin=str(plan.get("stop_loss_pct_margin", "")),
                take_profit_pct_margin=str(plan.get("take_profit_pct_margin", "")),
                coins=plan.get("coins", []),
            )
        )

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

    async def _emit_error(
        self, source: str, message: str, detail: str = "", *, severity: str = "error"
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
            )
        )
