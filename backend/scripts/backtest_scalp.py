#!/usr/bin/env python3
"""Real-data backtest runner for the ``scalp_momentum`` strategy.

Fetches **real Bitunix klines** for a basket of coins and replays them through the
exact production components (``BacktestFeed`` + ``SimBroker`` + ``RiskSizer`` +
``Engine``), then prints a performance report: total return, realized/fee
breakdown, round-trip trade count and win rate, and a per-symbol breakdown.

It deliberately fetches deep history via the paginating ``get_recent_klines`` so a
scalp (many short trades) has enough samples — the default backtest path caps the
plain ``get_klines`` call at 200 candles.

Usage (from the ``backend/`` directory)::

    python3 scripts/backtest_scalp.py                      # 10-coin basket, 5m, 1500 bars
    python3 scripts/backtest_scalp.py --interval 1m --limit 1000
    python3 scripts/backtest_scalp.py --symbols BTCUSDT,ETHUSDT --capital 2000

No API credentials are required — only public market-data endpoints are used.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

# Allow running directly from the repo without installing (backend/ on sys.path).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.domain.brokers.sim import SimBroker  # noqa: E402
from app.domain.clock import SimulatedClock  # noqa: E402
from app.domain.engine import Engine  # noqa: E402
from app.domain.feeds.backtest import BacktestFeed, merge_bars_by_time  # noqa: E402
from app.domain.types import Bar, Mode  # noqa: E402
from app.events.bus import InMemorySink  # noqa: E402
from app.events.schemas import FillEvent  # noqa: E402
from app.exchange.bitunix.rest import BitunixRest  # noqa: E402
from app.risk.config import RiskParams  # noqa: E402
from app.risk.sizer import RiskSizer  # noqa: E402
from app.strategies import create_strategy  # noqa: E402
from app.strategies.scalp_momentum import DEFAULT_SCALP_SYMBOLS  # noqa: E402

OPENING = {("buy", "long"), ("sell", "short")}


@dataclass
class TradeStats:
    """Round-trip trade accounting for one (symbol, side) episode."""

    realized: Decimal = Decimal("0")
    fees: Decimal = Decimal("0")
    open_qty: Decimal = Decimal("0")
    entries: int = 0


@dataclass
class SymbolReport:
    trades: int = 0
    wins: int = 0
    realized: Decimal = Decimal("0")
    fees: Decimal = Decimal("0")
    bars: int = 0
    pnls: list[Decimal] = field(default_factory=list)


def _reconstruct_trades(fills: list[FillEvent]) -> dict[str, SymbolReport]:
    """Group fills into round-trip trades per symbol (open -> full close)."""
    reports: dict[str, SymbolReport] = defaultdict(SymbolReport)
    episodes: dict[tuple[str, str], TradeStats] = defaultdict(TradeStats)
    for f in fills:
        key = (f.symbol, f.position_side)
        ep = episodes[key]
        ep.fees += f.fee
        is_open = (f.side, f.position_side) in OPENING
        if is_open:
            ep.open_qty += f.qty
            ep.entries += 1
        else:
            ep.open_qty -= f.qty
            ep.realized += f.realized_pnl
        # A trade completes when the position returns flat.
        if not is_open and ep.open_qty <= Decimal("0.000000001"):
            net = ep.realized - ep.fees
            rep = reports[f.symbol]
            rep.trades += 1
            rep.realized += ep.realized
            rep.fees += ep.fees
            rep.pnls.append(net)
            if net > 0:
                rep.wins += 1
            episodes[key] = TradeStats()
    return reports


def _max_drawdown(equity: list[Decimal]) -> Decimal:
    peak = equity[0] if equity else Decimal("0")
    worst = Decimal("0")
    for e in equity:
        peak = max(peak, e)
        if peak > 0:
            dd = (peak - e) / peak * Decimal("100")
            worst = max(worst, dd)
    return worst


async def _fetch(
    rest: BitunixRest, symbols: list[str], interval: str, limit: int
) -> tuple[dict, dict[str, list[Bar]]]:
    instruments = await rest.get_trading_pairs(symbols)
    missing = [s for s in symbols if s not in instruments]
    if missing:
        print(f"  ! not listed on Bitunix, skipped: {', '.join(missing)}")
    per_symbol: dict[str, list[Bar]] = {}
    for sym in symbols:
        if sym not in instruments:
            continue
        try:
            bars = await rest.get_recent_klines(sym, interval, limit)
        except Exception as exc:  # noqa: BLE001
            print(f"  ! kline fetch failed for {sym}: {exc}")
            continue
        per_symbol[sym] = bars
        print(f"  {sym:<10} {len(bars):>5} bars  "
              f"[{bars[0].open_time:%Y-%m-%d %H:%M} -> {bars[-1].open_time:%Y-%m-%d %H:%M}]"
              if bars else f"  {sym:<10}     0 bars")
    return instruments, per_symbol


async def run_backtest(args: argparse.Namespace) -> int:
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    risk = RiskParams(
        min_investment_usd=Decimal(str(args.min_investment)),
        max_capital_usd=Decimal(str(args.max_capital)),
        max_loss_usd=Decimal(str(args.max_loss)),
        base_leverage=args.base_leverage,
        max_leverage=args.max_leverage,
        fee_rate=Decimal(str(args.fee_rate)),
        allow_hedge=False,
    )
    initial = Decimal(str(args.capital))

    print("=" * 78)
    print(f"scalp_momentum backtest — interval={args.interval}  bars/coin={args.limit}")
    print(f"capital={initial}  margin/step={risk.min_investment_usd}  "
          f"max_capital={risk.max_capital_usd}  base_lev={risk.base_leverage}x  "
          f"fee={risk.fee_rate}")
    print("=" * 78)
    print("fetching real Bitunix klines:")

    rest = BitunixRest()
    try:
        instruments, per_symbol = await _fetch(rest, symbols, args.interval, args.limit)
    finally:
        await rest.close()

    bars = merge_bars_by_time(per_symbol)
    if not bars:
        print("\nno bars fetched — aborting.")
        return 1
    bar_counts = {s: len(b) for s, b in per_symbol.items()}

    clock = SimulatedClock(bars[0].open_time)
    strategy = create_strategy("scalp_momentum", {"symbols": list(per_symbol.keys())})
    sizer = RiskSizer(risk)
    feed = BacktestFeed(bars, instruments, clock)
    broker = SimBroker(clock, instruments, initial, fee_rate=risk.fee_rate)
    sink = InMemorySink()
    engine = Engine(
        mode=Mode.BACKTEST,
        strategy=strategy,
        sizer=sizer,
        broker=broker,
        feed=feed,
        clock=clock,
        sink=sink,
        interval=args.interval,
    )
    summary = await engine.run()

    # ---- aggregate results ------------------------------------------------- #
    fills = sink.fills
    gross = sum((f.realized_pnl for f in fills), Decimal("0"))
    fees = sum((f.fee for f in fills), Decimal("0"))
    net = gross - fees
    ret_pct = (summary.final_equity - initial) / initial * Decimal("100")
    equity_curve = [e.equity for e in sink.equity]
    mdd = _max_drawdown(equity_curve)

    reports = _reconstruct_trades(fills)
    for sym, n in bar_counts.items():
        reports[sym].bars = n
    total_trades = sum(r.trades for r in reports.values())
    total_wins = sum(r.wins for r in reports.values())
    win_rate = (
        Decimal(total_wins) / Decimal(total_trades) * Decimal("100")
        if total_trades
        else Decimal("0")
    )

    print("\n" + "-" * 78)
    print("RESULT")
    print("-" * 78)
    print(f"  bars processed     : {summary.events}")
    print(f"  orders / fills     : {summary.orders} / {summary.fills}  "
          f"(rejected {summary.rejected})")
    print(f"  round-trip trades  : {total_trades}")
    print(f"  win rate           : {win_rate:.1f}%  ({total_wins}W / {total_trades - total_wins}L)")
    print(f"  gross PnL          : {gross:+.2f} USDT")
    print(f"  fees paid          : {fees:.2f} USDT")
    print(f"  net realized PnL   : {net:+.2f} USDT")
    print(f"  start equity       : {initial:.2f} USDT")
    print(f"  final equity       : {summary.final_equity:.2f} USDT")
    print(f"  total return       : {ret_pct:+.2f}%")
    print(f"  max drawdown       : {mdd:.2f}%")

    print("\n  per-symbol breakdown:")
    print(f"    {'symbol':<10}{'bars':>6}{'trades':>8}{'win%':>7}{'net PnL':>12}")
    for sym in sorted(reports, key=lambda s: reports[s].realized - reports[s].fees):
        r = reports[sym]
        net_sym = r.realized - r.fees
        wr = (Decimal(r.wins) / Decimal(r.trades) * Decimal("100")) if r.trades else Decimal("0")
        print(f"    {sym:<10}{r.bars:>6}{r.trades:>8}{wr:>6.0f}%{net_sym:>+12.2f}")

    print("-" * 78)
    verdict = "PROFITABLE" if summary.final_equity > initial else "UNPROFITABLE"
    print(f"  verdict: {verdict} over this window")
    print("=" * 78)
    return 0


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Real-data scalp_momentum backtest")
    p.add_argument("--symbols", default=",".join(DEFAULT_SCALP_SYMBOLS),
                   help="Comma-separated coins (default: the 10-coin basket).")
    p.add_argument("--interval", default="5m", help="Candle interval (1m/5m/15m/1h).")
    p.add_argument("--limit", type=int, default=1500, help="Bars per coin to fetch.")
    p.add_argument("--capital", type=float, default=1000.0, help="Starting balance (USDT).")
    p.add_argument("--min-investment", dest="min_investment", type=float, default=5.0,
                   help="Committed margin per entry (USDT).")
    p.add_argument("--max-capital", dest="max_capital", type=float, default=100.0,
                   help="Max total committed margin (USDT).")
    p.add_argument("--max-loss", dest="max_loss", type=float, default=1000.0,
                   help="Max estimated loss budget (USDT).")
    p.add_argument("--base-leverage", dest="base_leverage", type=int, default=10)
    p.add_argument("--max-leverage", dest="max_leverage", type=int, default=20)
    p.add_argument("--fee-rate", dest="fee_rate", type=float, default=0.0006,
                   help="Taker fee rate (0.0006 = 0.06%).")
    return p.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run_backtest(_parse_args())))
