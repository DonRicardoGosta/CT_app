"""Tests for the guarded_ladder strategy.

Covers TP-list parsing, breakout entry, multi-entry DCA laddering, scaled
take-profits (REDUCE intents), the moving stop, the capital-drawdown kill switch,
and a deterministic backtest that must finish net profitable on a clean trend.
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
from app.strategies.guarded_ladder import _parse_decimal_list

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


def _bars_from_prices(symbol: str, prices: list[float], interval: str = "1m") -> list[Bar]:
    out: list[Bar] = []
    for i, price in enumerate(prices):
        p = Decimal(str(round(price, 4)))
        out.append(
            Bar(
                symbol=symbol,
                interval=interval,
                open_time=_EPOCH + timedelta(minutes=i),
                open=p,
                high=p,
                low=p,
                close=p,
                volume=Decimal("100"),
            )
        )
    return out


def _test_params(**overrides) -> dict:
    base = {
        "ema_fast": 5,
        "ema_slow": 10,
        "trend_ema": 20,
        "breakout_lookback": 10,
        "max_entries": 4,
        "entry_spacing_pct": "0.3",
        "tp_levels_pct": "0.8,1.6,3.0",
        "tp_close_pct": "40,40,100",
        "stop_loss_pct": "1.2",
    }
    base.update(overrides)
    return base


async def _run(bars, instruments, sink, params=None, *, initial=Decimal("1000"), risk=None):
    strategy = create_strategy("guarded_ladder", params or _test_params())
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


# --------------------------------------------------------------------------- #
# TP-list parsing
# --------------------------------------------------------------------------- #
def test_parse_decimal_list_tolerates_noise():
    assert _parse_decimal_list("0.8, 1.6 ,3.0") == [
        Decimal("0.8"),
        Decimal("1.6"),
        Decimal("3.0"),
    ]
    assert _parse_decimal_list("1;2;abc;3") == [Decimal("1"), Decimal("2"), Decimal("3")]
    assert _parse_decimal_list("") == []


def test_tp_legs_force_full_close_on_last_leg():
    strat = create_strategy(
        "guarded_ladder", {"tp_levels_pct": "0.5,1,2,4", "tp_close_pct": "25,25,25"}
    )
    legs = strat._tp_legs
    assert [m for m, _ in legs] == [Decimal("0.5"), Decimal("1"), Decimal("2"), Decimal("4")]
    # First three close 25% of the remainder, the configured last leg closes the rest.
    assert legs[0][1] == Decimal("0.25")
    assert legs[-1][1] == Decimal("1")


def test_position_levels_are_price_based():
    strat = create_strategy("guarded_ladder", {"tp_levels_pct": "1,2.5", "tp_close_pct": "50,100"})
    lv = strat.position_levels("BTCUSDT", PositionSide.LONG, Decimal("100"), 20)
    assert lv is not None
    assert lv["take_profits"][0] == Decimal("101.0")
    assert lv["take_profits"][1] == Decimal("102.5")
    assert lv["stops"], "an initial stop level must be present"


def test_protection_plan_builds_multiple_tp_legs():
    strat = create_strategy(
        "guarded_ladder", {"tp_levels_pct": "1,2,3", "tp_close_pct": "30,30,100"}
    )
    plan = strat.protection_plan(
        "BTCUSDT", PositionSide.LONG, Decimal("100"), Decimal("0.9"), _instrument()
    )
    assert plan is not None
    assert plan.stop_price < Decimal("100")  # long stop below entry
    assert len(plan.take_profits) == 3
    # Leg quantities sum to the whole position (full coverage).
    assert sum((leg.qty for leg in plan.take_profits), Decimal("0")) == Decimal("0.9")
    assert plan.take_profits[0].price == Decimal("101.0")


# --------------------------------------------------------------------------- #
# Breakout entry, multi-entry ladder and scaled take-profits
# --------------------------------------------------------------------------- #
def _trend_with_pullbacks(symbol: str = "BTCUSDT", n: int = 200) -> list[Bar]:
    """Dominant uptrend with oscillation: breakouts to new highs + adverse dips.

    The linear slope keeps price above the trend EMA (long regime) and repeatedly
    prints new highs (breakout entries), while the sine wave creates dips that drive
    DCA add-ins and lets favourable swings reach the take-profit legs.
    """
    return _bars_from_prices(
        symbol,
        [100.0 + 0.9 * i + 6.0 * math.sin(2 * math.pi * i / 18.0) for i in range(n)],
    )


@pytest.mark.asyncio
async def test_breakout_entry_and_orders():
    bars = _trend_with_pullbacks()
    sink = InMemorySink()
    summary, _ = await _run(bars, _instruments(), sink)
    assert summary.orders > 0, "a breakout entry should have produced orders"
    entries = [s for s in sink.signals if s.action == IntentAction.OPEN.value]
    assert entries, "expected at least one OPEN signal"
    assert any(s.tag == "entry_1" for s in entries), "first entry must be tagged entry_1"


@pytest.mark.asyncio
async def test_multi_entry_and_scaled_tp():
    bars = _trend_with_pullbacks()
    sink = InMemorySink()
    await _run(bars, _instruments(), sink)
    actions = [(s.action, s.tag) for s in sink.signals]
    entries = [a for a in actions if a[0] == IntentAction.OPEN.value]
    assert len(entries) >= 2, f"expected multiple entries, got {entries}"
    reduces = [a for a in actions if a[0] == IntentAction.REDUCE.value]
    assert reduces, "expected at least one partial take-profit (REDUCE)"


@pytest.mark.asyncio
async def test_backtest_is_net_profitable_on_clean_uptrend():
    """The headline 'working strategy' gate: a clean trend must finish in profit."""
    # Steady uptrend (~0.6%/bar) so a breakout entry opens and the scaled TPs all
    # fill on the way up, fully closing the position at a profit (net of fees).
    prices = [100.0 * (1.006**i) for i in range(120)]
    bars = _bars_from_prices("BTCUSDT", prices)
    sink = InMemorySink()
    summary, _ = await _run(bars, _instruments(), sink, initial=Decimal("1000"))
    assert summary.orders > 0
    assert summary.final_equity > Decimal("1000"), (
        f"strategy should be net profitable on a clean uptrend, got {summary.final_equity}"
    )


# --------------------------------------------------------------------------- #
# Moving stop
# --------------------------------------------------------------------------- #
def test_stop_advances_to_breakeven_then_trails():
    strat = create_strategy("guarded_ladder", _test_params())
    k = ("BTCUSDT", "long")
    entry = Decimal("100")
    strat._stop[k] = strat._initial_stop(entry, PositionSide.LONG)
    assert strat._stop[k] < entry  # initial stop below entry

    # After 1 TP hit -> breakeven (breakeven_after_tp default 1).
    strat._tps_hit[k] = 1
    strat._advance_stop(k, PositionSide.LONG, entry, best=Decimal("103"))
    assert strat._stop[k] == entry  # moved to breakeven

    # After 2 TP hits -> trailing kicks in (trail_after_tp default 2).
    strat._tps_hit[k] = 2
    strat._advance_stop(k, PositionSide.LONG, entry, best=Decimal("110"))
    assert strat._stop[k] > entry  # trailed up above breakeven


# --------------------------------------------------------------------------- #
# Capital-drawdown kill switch
# --------------------------------------------------------------------------- #
def _ctx_with_losing_position(strat, *, baseline: Decimal, mark: Decimal) -> MarketEvent:
    """Build a context where equity is far below the drawdown threshold."""
    symbol = "BTCUSDT"
    market = MarketState()
    bar = _bars_from_prices(symbol, [float(mark)])[0]
    market.update_bar(bar)
    pos = Position(
        symbol=symbol,
        position_side=PositionSide.LONG,
        qty=Decimal("1"),
        entry_price=Decimal("100"),
        leverage=20,
        committed_margin=Decimal("5"),
        step_count=1,
    )
    account = AccountState(
        ts=_EPOCH,
        balance=baseline,  # realized cash unchanged; the loss is unrealized
        positions={(symbol, PositionSide.LONG): pos},
    )
    # Mark the position as one the strategy opened (so flatten will close it).
    strat._last_entry[(symbol, "long")] = Decimal("100")
    strat._stop[(symbol, "long")] = strat._initial_stop(Decimal("100"), PositionSide.LONG)
    strat._best[(symbol, "long")] = Decimal("100")
    strat._tps_hit[(symbol, "long")] = 0
    from app.strategies.base import StrategyContext

    event = MarketEvent(type=MarketEventType.BAR, ts=_EPOCH, symbol=symbol, bar=bar)
    return StrategyContext(
        event=event,
        now=_EPOCH,
        account=account,
        instruments=_instruments(),
        market=market,
    )


def test_capital_guard_halts_and_flattens():
    strat = create_strategy(
        "guarded_ladder", _test_params(capital_usd="50", max_drawdown_pct="60")
    )
    # Baseline 50 -> threshold 20. Mark 55 makes unrealized = (55-100)*1 = -45,
    # so equity = 50 - 45 = 5 <= 20 -> halt.
    ctx = _ctx_with_losing_position(strat, baseline=Decimal("50"), mark=Decimal("55"))
    intents = strat.on_event(ctx)
    assert strat._halted is True
    closes = [i for i in intents if i.action == IntentAction.CLOSE]
    assert closes, "halt should flatten open positions"
    assert all(i.tag == "halt_flatten" for i in closes)

    # A drawdown-halt log was queued for the UI.
    logs = strat.drain_scan_logs()
    assert any(log.get("context", {}).get("check") == "capital_guard_halt" for log in logs)


def test_capital_guard_blocks_new_entries_after_halt():
    strat = create_strategy("guarded_ladder", _test_params(flatten_on_halt=False))
    strat._halted = True  # already tripped
    # Even with a textbook breakout, no OPEN intent may be produced while halted.
    prices = [100.0 + i for i in range(40)]
    bars = _bars_from_prices("BTCUSDT", prices)
    market = MarketState()
    for bar in bars:
        market.update_bar(bar)
    from app.strategies.base import StrategyContext

    account = AccountState(ts=_EPOCH, balance=Decimal("50"), positions={})
    # Baseline must be set so the guard re-confirms halt without flipping it off.
    strat._baseline_equity = Decimal("50")
    ctx = StrategyContext(
        event=MarketEvent(
            type=MarketEventType.BAR, ts=_EPOCH, symbol="BTCUSDT", bar=bars[-1]
        ),
        now=_EPOCH,
        account=account,
        instruments=_instruments(),
        market=market,
    )
    intents = strat.on_event(ctx)
    assert all(i.action != IntentAction.OPEN for i in intents)


def test_no_halt_when_equity_above_threshold():
    strat = create_strategy("guarded_ladder", _test_params(capital_usd="50"))
    strat._baseline_equity = Decimal("50")
    account = AccountState(ts=_EPOCH, balance=Decimal("45"), positions={})
    market = MarketState()
    market.update_bar(_bars_from_prices("BTCUSDT", [100.0])[0])
    from app.strategies.base import StrategyContext

    ctx = StrategyContext(
        event=MarketEvent(
            type=MarketEventType.BAR,
            ts=_EPOCH,
            symbol="BTCUSDT",
            bar=_bars_from_prices("BTCUSDT", [100.0])[0],
        ),
        now=_EPOCH,
        account=account,
        instruments=_instruments(),
        market=market,
    )
    assert strat._check_capital_guard(ctx) == []
    assert strat._halted is False


def test_flatten_ignores_manual_positions():
    """The capital guard must never close a position the bot did not open."""
    strat = create_strategy("guarded_ladder", _test_params())
    manual = Position(
        symbol="BTCUSDT",
        position_side=PositionSide.LONG,
        qty=Decimal("1"),
        entry_price=Decimal("100"),
        leverage=20,
    )
    acct = AccountState(
        ts=_EPOCH, balance=Decimal("50"),
        positions={("BTCUSDT", PositionSide.LONG): manual},
    )
    # No strategy trade-state for this symbol -> the bot did not open it.
    assert strat._flatten_intents(acct) == []

    # Once the bot has opened ETHUSDT, only that one is flattened.
    strat._last_entry[("ETHUSDT", "long")] = Decimal("10")
    strat._stop[("ETHUSDT", "long")] = Decimal("9")
    acct.positions[("ETHUSDT", PositionSide.LONG)] = Position(
        symbol="ETHUSDT", position_side=PositionSide.LONG, qty=Decimal("5"),
        entry_price=Decimal("10"), leverage=20,
    )
    intents = strat._flatten_intents(acct)
    assert [i.symbol for i in intents] == ["ETHUSDT"]


# --------------------------------------------------------------------------- #
# Live moving stop (compute_live_stop)
# --------------------------------------------------------------------------- #
def test_compute_live_stop_breakeven_then_trail():
    # breakeven_after_tp=1, trail_after_tp=1, trail_pct=3.0 (defaults).
    strat = create_strategy("guarded_ladder", {"trail_pct": "3.0"})
    entry = Decimal("100")
    # No TP filled yet -> do not move the resting entry stop.
    assert strat.compute_live_stop(PositionSide.LONG, entry, entry, 0) is None
    # After TP1, best barely above entry -> stop at breakeven (>= entry).
    s1 = strat.compute_live_stop(PositionSide.LONG, entry, Decimal("100.5"), 1)
    assert s1 == entry
    # As best rises, the trail lifts the stop above entry.
    s2 = strat.compute_live_stop(PositionSide.LONG, entry, Decimal("110"), 1)
    assert s2 > entry and s2 == Decimal("110") * (Decimal(1) - Decimal("0.03"))
    # Short mirrors: stop sits above entry and trails down.
    ss = strat.compute_live_stop(PositionSide.SHORT, entry, Decimal("90"), 1)
    assert ss == Decimal("90") * (Decimal(1) + Decimal("0.03"))


# --------------------------------------------------------------------------- #
# Engine live stop management: only touches positions the engine opened
# --------------------------------------------------------------------------- #
class _StopBroker:
    """Minimal broker that records modify_stop calls and serves scripted accounts."""

    def __init__(self, snapshots: list[AccountState]):
        self._snapshots = snapshots
        self._idx = 0
        self.modify_calls: list[tuple[str, str, Decimal]] = []

    async def account(self) -> AccountState:
        snap = self._snapshots[min(self._idx, len(self._snapshots) - 1)]
        return snap

    async def set_mark(self, symbol: str, price: object) -> None:
        return None

    async def submit(self, request):  # pragma: no cover - not used here
        raise NotImplementedError

    async def modify_stop(self, *, symbol, position_side, stop_price, instrument) -> bool:
        self.modify_calls.append((symbol, position_side.value, stop_price))
        return True


def _live_engine(strategy, broker):
    from app.domain.clock import RealClock
    from app.domain.feeds.live import LiveFeed

    return Engine(
        mode=Mode.LIVE,
        strategy=strategy,
        sizer=RiskSizer(RiskParams(base_leverage=20)),
        broker=broker,
        feed=LiveFeed([], _instruments(), "15m"),
        clock=RealClock(),
        sink=InMemorySink(),
        interval="15m",
    )


def _pos(symbol, qty, entry):
    return Position(
        symbol=symbol, position_side=PositionSide.LONG, qty=Decimal(str(qty)),
        entry_price=Decimal(str(entry)), leverage=20,
    )


@pytest.mark.asyncio
async def test_engine_moves_stop_only_for_managed_positions():
    strat = create_strategy("guarded_ladder", {"trail_pct": "3.0"})
    # The account always holds a MANUAL BTC long plus our managed ETH long.
    btc = _pos("BTCUSDT", 1, 100)
    eth_after_tp1 = _pos("ETHUSDT", 1, 10)  # qty halved -> TP1 filled
    snap = AccountState(
        ts=_EPOCH, balance=Decimal("50"),
        positions={
            ("BTCUSDT", PositionSide.LONG): btc,
            ("ETHUSDT", PositionSide.LONG): eth_after_tp1,
        },
    )
    broker = _StopBroker([snap])
    engine = _live_engine(strat, broker)
    instruments = {**_instruments("BTCUSDT"), **_instruments("ETHUSDT")}

    # Register ONLY the ETH position as opened by the engine.
    from app.domain.engine import _ManagedPosition

    engine._managed[("ETHUSDT", PositionSide.LONG)] = _ManagedPosition(
        entry_price=Decimal("10"), last_qty=Decimal("2"), best_price=Decimal("10")
    )
    engine.market.update_price("ETHUSDT", Decimal("10.4"))
    engine.market.update_price("BTCUSDT", Decimal("95"))

    await engine._manage_live_protections(instruments)

    # Exactly one modify_stop, for ETH (managed), never BTC (manual).
    assert [c[0] for c in broker.modify_calls] == ["ETHUSDT"]
    # TP1 detected (qty 2 -> 1) -> stop moved to at least breakeven (entry 10).
    assert broker.modify_calls[0][2] >= Decimal("10")

