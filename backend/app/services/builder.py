"""Assemble an :class:`Engine` from a :class:`RunConfig`.

This is where mode selection happens — and the *only* place it happens. The engine
and strategy code below this layer are mode-agnostic (REQ-001/003).
"""

from __future__ import annotations

from datetime import UTC, datetime

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
from app.events.schemas import ErrorEvent
from app.exchange.bitunix.rest import BitunixRest
from app.risk.sizer import RiskSizer
from app.services.run_config import RunConfig
from app.strategies import create_strategy

log = get_logger(__name__)

# Auto-selected universes can be large. Backtests fetch klines per symbol, so cap
# the auto set to keep runs fast when no explicit symbols are given. Live/dry
# mode subscribes only the active scan batch and expands dynamically.
BACKTEST_MAX_AUTO_SYMBOLS = 8
MAX_VOLUME_RANK = 500


async def _emit_builder_log(
    sink: EventSink,
    config: RunConfig,
    severity: str,
    message: str,
    *,
    context: dict | None = None,
) -> None:
    await sink.emit(
        ErrorEvent(
            run_id=config.run_id,
            mode=str(config.mode),
            ts=datetime.now(UTC),
            source="builder",
            severity=severity,
            message=message,
            context=context or {},
        )
    )


def _resolve_symbols(
    config: RunConfig, strategy, instruments: dict[str, Instrument]
) -> list[str]:
    if config.symbols:
        return [s for s in config.symbols if s in instruments] or config.symbols
    desired = strategy.desired_symbols(instruments)
    return desired or list(instruments)[:5]


def _initial_live_symbols(config: RunConfig, symbols: list[str]) -> list[str]:
    """Symbols the live feed subscribes to at startup.

    With explicit symbols we subscribe them directly. Otherwise the feed starts
    empty: the engine scans the ranked universe one coin at a time via REST and
    only subscribes the coins it selects for live management.
    """
    return symbols if config.symbols else []


def _reorder_instruments(
    instruments: dict[str, Instrument], ranked_symbols: list[str]
) -> dict[str, Instrument]:
    """Return instruments ordered by ``ranked_symbols`` first, preserving leftovers."""
    ranked = {sym: instruments[sym] for sym in ranked_symbols if sym in instruments}
    for sym, inst in instruments.items():
        if sym not in ranked:
            ranked[sym] = inst
    return ranked


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
        await _emit_builder_log(
            sink,
            config,
            "warn",
            f"trading pairs fetch failed: {exc}",
        )
        instruments = {}

    volume_ranked: list[str] = []
    if instruments and not config.symbols:
        try:
            volume_ranked = await rest.get_volume_ranked_symbols(
                list(instruments), limit=MAX_VOLUME_RANK
            )
            instruments = _reorder_instruments(instruments, volume_ranked)
            await _emit_builder_log(
                sink,
                config,
                "info",
                "ranked symbols by 24h quote volume",
                context={"top_symbols": volume_ranked[:30], "limit": MAX_VOLUME_RANK},
            )
        except Exception as exc:  # noqa: BLE001 - fall back to exchange order
            log.warning("ticker_volume_ranking_failed", error=str(exc))
            await _emit_builder_log(
                sink,
                config,
                "warn",
                f"volume ranking failed, falling back to exchange order: {exc}",
            )

    symbols = _resolve_symbols(config, strategy, instruments)
    feed_symbols = list(symbols)
    # Backtests load klines per symbol; cap the auto-selected universe.
    if mode is Mode.BACKTEST and not config.symbols:
        feed_symbols = symbols[:BACKTEST_MAX_AUTO_SYMBOLS]
    elif mode is not Mode.BACKTEST:
        feed_symbols = _initial_live_symbols(config, symbols)
    await _emit_builder_log(
        sink,
        config,
        "info",
        f"resolved {len(symbols)} ranked coins to scan"
        if mode is not Mode.BACKTEST and not config.symbols
        else f"resolved {len(feed_symbols)} symbols",
        context={
            "feed_symbols": feed_symbols,
            "ranked_universe": len(symbols),
            "interval": config.interval,
        },
    )

    clock: Clock
    feed: MarketDataFeed
    broker: Broker

    if mode is Mode.BACKTEST:
        per_symbol = {}
        for sym in feed_symbols:
            try:
                per_symbol[sym] = await rest.get_klines(
                    sym, config.interval, config.backtest_limit
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("kline_fetch_failed", symbol=sym, error=str(exc))
                await _emit_builder_log(
                    sink,
                    config,
                    "warn",
                    f"kline fetch failed for {sym}: {exc}",
                    context={"symbol": sym, "interval": config.interval},
                )
        bars = merge_bars_by_time(per_symbol)
        if not bars:
            await _emit_builder_log(
                sink,
                config,
                "error",
                "backtest loaded zero bars",
                context={"symbols": feed_symbols, "interval": config.interval},
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
        # The feed starts with only explicit symbols (usually none); the engine's
        # one-by-one scan subscribes the coins it selects. The REST client stays
        # open for the life of the run because both the scan and the live
        # scheduled poll fetch history from it.
        #
        # The live feed is a scheduled REST poller: every candle close +
        # ``poll_offset_s`` it fetches exactly ``window_bars`` closed klines per
        # symbol (the strategy's required history) and evaluates the latest one.
        # No warmup phase — each cycle fetches what it needs right then.
        window_bars = strategy.warmup_bars()

        async def _feed_log(severity: str, message: str, context: dict) -> None:
            await _emit_builder_log(sink, config, severity, message, context=context)

        feed = LiveFeed(
            feed_symbols,
            instruments,
            config.interval,
            rest=rest,
            window_bars=window_bars,
            on_log=_feed_log,
            poll_offset_s=config.poll_offset_s,
        )
        if mode is Mode.LIVE:
            broker = LiveBroker(rest)
        else:  # DRY_RUN: simulated fills on live prices
            broker = SimBroker(
                clock, instruments, config.initial_capital, fee_rate=config.risk.fee_rate
            )

    # Live/dry auto-scan runs get the REST client as a history provider for the
    # one-by-one scan. Backtests already have all bars; explicit-symbol runs trade
    # exactly the requested coins (no universe scan), so they need no provider.
    history = None if (mode is Mode.BACKTEST or config.symbols) else rest
    return Engine(
        mode=mode,
        strategy=strategy,
        sizer=sizer,
        broker=broker,
        feed=feed,
        clock=clock,
        sink=sink,
        run_id=config.run_id,
        history=history,
        interval=config.interval,
    )


def _epoch():
    from datetime import UTC, datetime

    return datetime(1970, 1, 1, tzinfo=UTC)
