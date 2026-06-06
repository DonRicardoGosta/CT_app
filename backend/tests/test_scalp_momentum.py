"""Tests for the ``scalp_momentum`` strategy.

Covers the fixed-whitelist universe restriction (the headline requirement: only
the configured coins are ever traded), the trend-filtered momentum entry, scaled
take-profits, the stop and the scalp time-stop, the live TP/SL helpers, and a
deterministic backtest that must finish net profitable on a clean trend.

All bars are synthetic so the suite is fully deterministic and needs no network.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.domain.brokers.sim import SimBroker
from app.domain.clock import SimulatedClock
from app.domain.engine import Engine
from app.domain.feeds.replay import ReplayFeed
from app.domain.market import MarketState
from app.domain.types import (
    AccountState,
    Bar,
    Instrument,
    IntentAction,
    MarketEvent,
    MarketEventType,
    Mode,
    Position,
    PositionSide,
)
from app.events.bus import InMemorySink
from app.risk.config import RiskParams
from app.risk.sizer import RiskSizer
from app.strategies import create_strategy
from app.strategies.scalp_momentum import DEFAULT_SCALP_SYMBOLS

_EPOCH = datetime(2024, 1, 1, tzinfo=UTC)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _instrument(symbol: str = "BTCUSDT") -> Instrument:
    return Instrument(
        symbol=symbol,
        base=symbol.replace("USDT", ""),
        quote="USDT",
        min_trade_volume=Decimal("0.0001"),
        base_precision=4,
        quote_precision=2,
        min_leverage=1,
        max_leverage=50,
        default_leverage=10,
    )


def _instruments(symbol: str = "BTCUSDT") -> dict[str, Instrument]:
    return {symbol: _instrument(symbol)}


def _bars_from_prices(symbol: str, prices: list[float], interval: str = "5m") -> list[Bar]:
    out: list[Bar] = []
    for i, price in enumerate(prices):
        p = Decimal(str(round(price, 4)))
        out.append(
            Bar(
                symbol=symbol,
                interval=interval,
                open_time=_EPOCH + timedelta(minutes=5 * i),
                open=p,
                high=p,
                low=p,
                close=p,
                volume=Decimal("100"),
            )
        )
    return out


def _test_params(**overrides) -> dict:
    # Small EMA/RSI periods so a setup forms within a short synthetic series.
    base = {
        "ema_trend": 20,
        "ema_fast": 5,
        "ema_slow": 10,
        "rsi_period": 5,
        "tp1_pct": "0.4",
        "tp1_close_pct": "50",
        "tp2_pct": "0.9",
        "tp2_close_pct": "100",
        "stop_loss_pct": "0.5",
        "max_hold_bars": 50,
        "max_entries": 1,
    }
    base.update(overrides)
    return base


def _uptrend_with_crosses(symbol: str = "BTCUSDT", n: int = 240) -> list[Bar]:
    """Rising price with oscillation that prints fresh fast/slow EMA crosses.

    The positive slope keeps price above the trend EMA (long regime) while the
    sine wave dips the fast EMA below the slow EMA and back, producing the
    momentum cross-ups the scalp enters on, then continuing up into the TPs.
    """
    return _bars_from_prices(
        symbol,
        [100.0 + 0.3 * i + 8.0 * math.sin(2 * math.pi * i / 20.0) for i in range(n)],
    )


async def _run(bars, instruments, sink, params=None, *, initial=Decimal("1000"), risk=None):
    strategy = create_strategy("scalp_momentum", params or _test_params())
    sizer = RiskSizer(
        risk
        or RiskParams(
            min_investment_usd=Decimal("5"),
            max_capital_usd=Decimal("100"),
            base_leverage=5,
            max_leverage=20,
        )
    )
    clock = SimulatedClock(bars[0].open_time)
    feed = ReplayFeed(bars, instruments, clock=clock)
    broker = SimBroker(clock, instruments, initial, fee_rate=Decimal("0.0006"))
    engine = Engine(
        mode=Mode.BACKTEST,
        strategy=strategy,
        sizer=sizer,
        broker=broker,
        feed=feed,
        clock=clock,
        sink=sink,
    )
    return await engine.run(), strategy


def _context(strat, bars: list[Bar], account: AccountState):
    from app.strategies.base import StrategyContext

    market = MarketState()
    for b in bars:
        market.update_bar(b)
    return StrategyContext(
        event=MarketEvent(
            type=MarketEventType.BAR, ts=bars[-1].open_time, symbol=bars[-1].symbol, bar=bars[-1]
        ),
        now=bars[-1].open_time,
        account=account,
        instruments=_instruments(bars[-1].symbol),
        market=market,
    )


# --------------------------------------------------------------------------- #
# Universe restriction — the headline "only these coins" requirement
# --------------------------------------------------------------------------- #
def test_default_whitelist_is_the_requested_basket():
    strat = create_strategy("scalp_momentum", {})
    assert strat._whitelist == DEFAULT_SCALP_SYMBOLS
    assert DEFAULT_SCALP_SYMBOLS == [
        "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT",
        "DOGEUSDT", "AAVEUSDT", "TONUSDT", "WLDUSDT", "LINKUSDT",
    ]


def test_desired_symbols_intersects_with_available_instruments():
    strat = create_strategy("scalp_momentum", {})
    # Only BTCUSDT and LINKUSDT exist on the exchange in this scenario.
    instruments = {**_instruments("BTCUSDT"), **_instruments("LINKUSDT"), **_instruments("ZZZUSDT")}
    desired = strat.desired_symbols(instruments)
    # Returns whitelisted coins that exist, never the off-whitelist ZZZUSDT.
    assert set(desired) == {"BTCUSDT", "LINKUSDT"}
    assert "ZZZUSDT" not in desired


def test_custom_whitelist_is_honoured():
    strat = create_strategy("scalp_momentum", {"symbols": ["ethusdt", "ETHUSDT", " solusdt "]})
    # Normalised (upper-cased, trimmed, de-duplicated).
    assert strat._whitelist == ["ETHUSDT", "SOLUSDT"]


@pytest.mark.asyncio
async def test_on_event_ignores_non_whitelisted_symbol():
    """A textbook setup on a coin outside the whitelist must produce no intents."""
    strat = create_strategy("scalp_momentum", _test_params(symbols=["BTCUSDT"]))
    # FOOUSDT is NOT whitelisted, but the bars form a perfect long momentum cross.
    bars = _uptrend_with_crosses("FOOUSDT")
    account = AccountState(ts=_EPOCH, balance=Decimal("1000"), positions={})
    ctx = _context(strat, bars, account)
    assert strat.on_event(ctx) == []


# --------------------------------------------------------------------------- #
# Entry, scaled take-profits and net profitability
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_momentum_entry_emits_open_entry_1():
    bars = _uptrend_with_crosses()
    sink = InMemorySink()
    summary, _ = await _run(bars, _instruments(), sink)
    assert summary.orders > 0, "a momentum cross should have produced orders"
    entries = [s for s in sink.signals if s.action == IntentAction.OPEN.value]
    assert entries, "expected at least one OPEN signal"
    assert any(s.tag == "entry_1" for s in entries), "first entry must be tagged entry_1"


@pytest.mark.asyncio
async def test_scaled_take_profits_reduce_then_close():
    bars = _uptrend_with_crosses()
    sink = InMemorySink()
    await _run(bars, _instruments(), sink)
    actions = [(s.action, s.tag) for s in sink.signals]
    assert any(a == (IntentAction.REDUCE.value, "tp_1") for a in actions), "TP1 should REDUCE"
    assert any(a == (IntentAction.CLOSE.value, "tp_2") for a in actions), "TP2 should CLOSE"


@pytest.mark.asyncio
async def test_backtest_is_net_profitable_on_clean_uptrend():
    """The headline 'working strategy' gate: a clean trend must finish in profit."""
    bars = _uptrend_with_crosses()
    sink = InMemorySink()
    summary, _ = await _run(bars, _instruments(), sink, initial=Decimal("1000"))
    assert summary.orders > 0
    assert summary.final_equity > Decimal("1000"), (
        f"scalp should be net profitable on a clean uptrend, got {summary.final_equity}"
    )


# --------------------------------------------------------------------------- #
# Stop loss and the scalp time-stop (unit-level on _manage_position)
# --------------------------------------------------------------------------- #
def _open_position(strat, symbol: str, entry: Decimal, qty: Decimal = Decimal("1")) -> Position:
    k = (symbol, "long")
    strat._stop[k] = strat._initial_stop(entry, PositionSide.LONG)
    strat._tps_hit[k] = 0
    strat._best[k] = entry
    strat._bars_held[k] = 0
    return Position(
        symbol=symbol,
        position_side=PositionSide.LONG,
        qty=qty,
        entry_price=entry,
        leverage=10,
        committed_margin=Decimal("5"),
        step_count=1,
    )


def test_stop_loss_emits_close():
    strat = create_strategy("scalp_momentum", _test_params())
    entry = Decimal("100")
    pos = _open_position(strat, "BTCUSDT", entry)
    stop = strat._stop[("BTCUSDT", "long")]
    # Price drops just below the stop -> a full close tagged 'stop'.
    intents = strat._manage_position("BTCUSDT", PositionSide.LONG, pos, stop - Decimal("0.01"))
    assert [i.tag for i in intents] == ["stop"]
    assert intents[0].action is IntentAction.CLOSE
    # Trade state is cleared after the stop.
    assert ("BTCUSDT", "long") not in strat._stop


def test_time_stop_closes_stale_position():
    strat = create_strategy("scalp_momentum", _test_params(max_hold_bars=3))
    entry = Decimal("100")
    pos = _open_position(strat, "BTCUSDT", entry)
    # Price hovers at entry: never hits TP or stop, so only the time-stop can fire.
    out1 = strat._manage_position("BTCUSDT", PositionSide.LONG, pos, entry)
    out2 = strat._manage_position("BTCUSDT", PositionSide.LONG, pos, entry)
    assert out1 == [] and out2 == []  # 1st and 2nd bar: still holding
    out3 = strat._manage_position("BTCUSDT", PositionSide.LONG, pos, entry)
    assert [i.tag for i in out3] == ["time_stop"]
    assert out3[0].action is IntentAction.CLOSE


def test_time_stop_disabled_with_zero():
    strat = create_strategy("scalp_momentum", _test_params(max_hold_bars=0))
    entry = Decimal("100")
    pos = _open_position(strat, "BTCUSDT", entry)
    for _ in range(20):
        assert strat._manage_position("BTCUSDT", PositionSide.LONG, pos, entry) == []


# --------------------------------------------------------------------------- #
# Live TP/SL helpers (price-based levels, protection plan, moving stop)
# --------------------------------------------------------------------------- #
def test_position_levels_are_price_based():
    strat = create_strategy("scalp_momentum", _test_params())
    lv = strat.position_levels("BTCUSDT", PositionSide.LONG, Decimal("100"), 20)
    assert lv is not None
    assert lv["take_profits"][0] == Decimal("100") * (Decimal(1) + Decimal("0.4") / Decimal(100))
    assert lv["take_profits"][1] == Decimal("100") * (Decimal(1) + Decimal("0.9") / Decimal(100))
    assert lv["stops"], "an initial stop level must be present"


def test_protection_plan_builds_legs_below_above_entry():
    strat = create_strategy("scalp_momentum", _test_params())
    plan = strat.protection_plan(
        "BTCUSDT", PositionSide.LONG, Decimal("100"), Decimal("0.9"), _instrument()
    )
    assert plan is not None
    assert plan.stop_price < Decimal("100")  # long stop below entry
    assert len(plan.take_profits) == 2
    assert all(leg.price > Decimal("100") for leg in plan.take_profits)
    # Leg quantities sum to the whole position (full coverage).
    assert sum((leg.qty for leg in plan.take_profits), Decimal("0")) == Decimal("0.9")


def test_compute_live_stop_breakeven_then_trail():
    strat = create_strategy("scalp_momentum", _test_params(trail_pct="0.5"))
    entry = Decimal("100")
    # No TP filled yet -> do not move the resting entry stop.
    assert strat.compute_live_stop(PositionSide.LONG, entry, entry, 0) is None
    # After TP1 (breakeven_after_tp=1) -> stop at breakeven (entry).
    assert strat.compute_live_stop(PositionSide.LONG, entry, Decimal("100.3"), 1) == entry
    # After TP2 (trail_after_tp=2) -> trail below the best price.
    s2 = strat.compute_live_stop(PositionSide.LONG, entry, Decimal("110"), 2)
    assert s2 == Decimal("110") * (Decimal(1) - Decimal("0.5") / Decimal(100))
    # Short mirrors: stop sits above the best price.
    ss = strat.compute_live_stop(PositionSide.SHORT, entry, Decimal("90"), 2)
    assert ss == Decimal("90") * (Decimal(1) + Decimal("0.5") / Decimal(100))
