"""Take-profit / stop-loss price levels adjusted for leverage.

``stop_loss_pct`` and ``take_profit_pct`` are defined as % return on *margin*
(ROE), not % move in the underlying price. With leverage L, the required price
move is ``margin_pct / L``.

Example: 2% SL on margin at 10x leverage → 0.2% adverse price move.
"""

from __future__ import annotations

from decimal import Decimal

from app.domain.types import PositionSide


def margin_pct_to_price_move_pct(margin_pct: Decimal, leverage: int) -> Decimal:
    lev = max(int(leverage), 1)
    return margin_pct / Decimal(lev)


def stop_loss_price(
    entry: Decimal, side: PositionSide, margin_sl_pct: Decimal, leverage: int
) -> Decimal:
    move = margin_pct_to_price_move_pct(margin_sl_pct, leverage)
    delta = entry * (move / Decimal(100))
    if side is PositionSide.LONG:
        return entry - delta
    return entry + delta


def take_profit_price(
    entry: Decimal, side: PositionSide, margin_tp_pct: Decimal, leverage: int
) -> Decimal:
    move = margin_pct_to_price_move_pct(margin_tp_pct, leverage)
    delta = entry * (move / Decimal(100))
    if side is PositionSide.LONG:
        return entry + delta
    return entry - delta
