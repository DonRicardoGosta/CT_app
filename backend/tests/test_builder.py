from types import SimpleNamespace

from app.services.builder import _initial_live_symbols
from app.strategies import create_strategy


def test_initial_live_symbols_uses_one_scan_batch():
    strategy = create_strategy("trend_scanner", {"scan_universe": 30})
    symbols = [f"COIN{i:03d}USDT" for i in range(500)]

    selected = _initial_live_symbols(SimpleNamespace(symbols=[]), strategy, symbols)

    assert selected == symbols[:30]


def test_initial_live_symbols_keeps_explicit_symbols():
    strategy = create_strategy("trend_scanner", {"scan_universe": 30})
    symbols = ["BTCUSDT", "ETHUSDT"]

    selected = _initial_live_symbols(SimpleNamespace(symbols=symbols), strategy, symbols)

    assert selected == symbols

