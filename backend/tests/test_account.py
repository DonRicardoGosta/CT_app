"""Unit tests for the live account balance summarizer."""

from __future__ import annotations

from decimal import Decimal

from app.api.account.router import _summarize


def test_summarize_uses_explicit_balance_and_adds_unrealized():
    out = _summarize(
        {
            "marginCoin": "USDT",
            "balance": "100.5",
            "available": "80",
            "margin": "20.5",
            "crossUnrealizedPNL": "2.25",
        }
    )
    assert out["margin_coin"] == "USDT"
    assert out["balance"] == "100.5"
    assert out["available"] == "80"
    # equity = balance + unrealized
    assert Decimal(out["equity"]) == Decimal("102.75")


def test_summarize_derives_balance_from_parts_when_missing():
    out = _summarize({"available": "10", "margin": "5", "frozen": "1"})
    assert Decimal(out["balance"]) == Decimal("16")
    # no unrealized -> equity == balance
    assert Decimal(out["equity"]) == Decimal("16")


def test_summarize_handles_empty_payload():
    out = _summarize({})
    assert out["balance"] is None
    assert out["equity"] is None
    assert out["margin_coin"] == "USDT"
