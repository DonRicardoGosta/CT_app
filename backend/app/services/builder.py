"""Assemble an :class:`Engine` from a :class:`RunConfig`.

This is where mode selection happens — and the *only* place it happens. The engine
and strategy code below this layer are mode-agnostic (REQ-001/003).
"""

from __future__ import annotations

from app.core.logging import get_logger
from app.domain.brokers.live import LiveBroker
from app.domain.brokers.sim import SimBroker
from app.domain.clock import Clock, RealClock, SimulatedClock
from app.domain.engine import Engine
from app.domain.feeds.backtest import BacktestFeed, merge_bars_by_time
from app.domain.feeds.live import LiveFeed
from app.domain.interfaces import Broker, MarketDataFeed
from app.domain.types import Instrument, Mode
from app.events.bus import EventSink
from app.exchange.bitunix.rest import BitunixRest
from app.risk.sizer import RiskSizer
from app.services.run_config import RunConfig
from app.strategies import create_strategy

log = get_logger(__name__)


def _resolve_symbols(
    config: RunConfig, strategy, instruments: dict[str, Instrument]
) -> list[str]:
    if config.symbols:
        return [s for s in config.symbols if s in instruments] or config.symbols
    desired = strategy.desired_symbols(instruments)
    return desired or list(instruments)[:5]


async def build_engine(
    config: RunConfig,
    sink: EventSink,
    *,
    api_key: str = "",
    secret_key: str = "",
) -> Engine:
    """Build an engine wired for ``config.mode``."""
    mode = Mode(config.mode)
    strategy = create_strategy(config.strategy, config.params)
    sizer = RiskSizer(config.risk)

    rest = BitunixRest(api_key=api_key, secret_key=secret_key)
    try:
        instruments = await rest.get_trading_pairs(config.symbols or None)
    except Exception as exc:  # noqa: BLE001 - fall back to an empty universe
        log.warning("trading_pairs_fetch_failed", error=str(exc))
        instruments = {}

    symbols = _resolve_symbols(config, strategy, instruments)

    clock: Clock
    feed: MarketDataFeed
    broker: Broker

    if mode is Mode.BACKTEST:
        per_symbol = {}
        for sym in symbols:
            try:
                if config.backtest_start or config.backtest_end:
                    per_symbol[sym] = await rest.get_klines(
                        sym,
                        config.interval,
                        start_time=config.backtest_start,
                        end_time=config.backtest_end,
                    )
                else:
                    per_symbol[sym] = await rest.get_klines(
                        sym, config.interval, config.backtest_limit
                    )
            except Exception as exc:  # noqa: BLE001
                log.warning("kline_fetch_failed", symbol=sym, error=str(exc))
        bars = merge_bars_by_time(per_symbol)
        if not bars:
            log.warning(
                "backtest_no_bars",
                symbols=symbols,
                start=str(config.backtest_start),
                end=str(config.backtest_end),
            )
        start = bars[0].open_time if bars else config.backtest_start
        sim_clock = SimulatedClock(start) if start else SimulatedClock(_epoch())
        clock = sim_clock
        feed = BacktestFeed(bars, instruments, sim_clock)
        broker = SimBroker(
            sim_clock, instruments, config.initial_capital, fee_rate=config.risk.fee_rate
        )
        await rest.close()
    else:
        clock = RealClock()
        feed = LiveFeed(symbols, instruments, config.interval)
        if mode is Mode.LIVE:
            broker = LiveBroker(rest)
        else:  # DRY_RUN: simulated fills on live prices
            broker = SimBroker(
                clock, instruments, config.initial_capital, fee_rate=config.risk.fee_rate
            )
            # rest stays open only if needed; dry-run uses public feed, close it.
            await rest.close()

    return Engine(
        mode=mode,
        strategy=strategy,
        sizer=sizer,
        broker=broker,
        feed=feed,
        clock=clock,
        sink=sink,
        run_id=config.run_id,
    )


def _epoch():
    from datetime import UTC, datetime

    return datetime(1970, 1, 1, tzinfo=UTC)
