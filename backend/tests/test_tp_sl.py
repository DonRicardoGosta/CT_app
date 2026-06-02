"""Leverage-adjusted TP/SL price levels."""

from decimal import Decimal

from app.domain.tp_sl import margin_pct_to_price_move_pct, stop_loss_price, take_profit_price
from app.domain.types import PositionSide


def test_margin_pct_divided_by_leverage():
    assert margin_pct_to_price_move_pct(Decimal("2"), 10) == Decimal("0.2")


def test_long_sl_tp_at_10x():
    entry = Decimal("100")
    sl = stop_loss_price(entry, PositionSide.LONG, Decimal("2"), 10)
    tp = take_profit_price(entry, PositionSide.LONG, Decimal("3"), 10)
    assert sl == Decimal("99.8")
    assert tp == Decimal("100.3")
