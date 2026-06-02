from types import SimpleNamespace

from app.services.builder import _initial_live_symbols


def test_initial_live_symbols_empty_when_auto_scanning():
    # No explicit symbols -> the feed starts empty; the engine scans one by one.
    symbols = [f"COIN{i:03d}USDT" for i in range(500)]

    selected = _initial_live_symbols(SimpleNamespace(symbols=[]), symbols)

    assert selected == []


def test_initial_live_symbols_keeps_explicit_symbols():
    symbols = ["BTCUSDT", "ETHUSDT"]

    selected = _initial_live_symbols(SimpleNamespace(symbols=symbols), symbols)

    assert selected == symbols
