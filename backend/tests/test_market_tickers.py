"""Ticker normalization for the /market/tickers 24h-change endpoint."""

from __future__ import annotations

from app.api.market.router import _ticker_row


def test_ticker_row_uses_precomputed_percent():
    row = _ticker_row({"symbol": "BTCUSDT", "lastPrice": "100", "priceChangePercent": "2.5"})
    assert row == {"symbol": "BTCUSDT", "last": "100", "change_24h_pct": 2.5}


def test_ticker_row_derives_percent_from_open():
    row = _ticker_row({"symbol": "ETHUSDT", "last": "110", "open": "100"})
    assert row is not None
    assert row["symbol"] == "ETHUSDT"
    assert row["last"] == "110"
    assert row["change_24h_pct"] == 10.0


def test_ticker_row_handles_missing_symbol():
    assert _ticker_row({"lastPrice": "1"}) is None


def test_ticker_row_handles_missing_prices():
    row = _ticker_row({"symbol": "XYZUSDT"})
    assert row == {"symbol": "XYZUSDT", "last": None, "change_24h_pct": None}
