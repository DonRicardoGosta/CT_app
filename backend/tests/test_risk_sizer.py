"""Risk sizer: min investment, leverage escalation and limits (REQ-007)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from app.domain.types import (
    AccountState,
    Instrument,
    IntentAction,
    PositionSide,
    Side,
    TradeIntent,
)
from app.risk.config import RiskParams
from app.risk.sizer import RiskSizer


def _account() -> AccountState:
    return AccountState(ts=datetime.now(UTC), balance=Decimal("1000"))


def _intent(symbol: str = "BTCUSDT") -> TradeIntent:
    return TradeIntent(
        symbol=symbol,
        side=Side.BUY,
        action=IntentAction.OPEN,
        position_side=PositionSide.LONG,
    )


def test_committed_margin_independent_of_leverage(btc_instrument: Instrument):
    """Setting 1 USD min investment commits ~1 USD regardless of multiplier."""
    price = Decimal("100")
    for lev in (1, 5, 10, 20):
        params = RiskParams(min_investment_usd=Decimal("1"), base_leverage=lev, max_leverage=lev)
        res = RiskSizer(params).size(_intent(), _account(), btc_instrument, price)
        assert res.ok
        assert res.margin == Decimal("1")  # committed margin constant
        # Notional scales with leverage.
        assert res.notional == Decimal("1") * Decimal(lev)


def test_leverage_escalates_to_meet_min_size():
    """A tiny investment forces the multiplier up until the order qualifies."""
    # Min size 1.0 base coin at price 100 needs notional >= 100, so margin 1 USD
    # requires leverage >= 100. Cap at 200 -> escalation should find leverage 100.
    inst = Instrument(
        symbol="X",
        base="X",
        quote="USDT",
        min_trade_volume=Decimal("1"),
        base_precision=0,
        quote_precision=2,
        min_leverage=1,
        max_leverage=200,
        default_leverage=1,
    )
    params = RiskParams(min_investment_usd=Decimal("1"), base_leverage=1, max_leverage=200)
    res = RiskSizer(params).size(_intent("X"), _account(), inst, Decimal("100"))
    assert res.ok
    assert res.leverage >= 100
    assert res.qty >= Decimal("1")


def test_rejected_when_even_max_leverage_too_small():
    inst = Instrument(
        symbol="X",
        base="X",
        quote="USDT",
        min_trade_volume=Decimal("1000"),
        base_precision=0,
        quote_precision=2,
        min_leverage=1,
        max_leverage=5,
        default_leverage=1,
    )
    params = RiskParams(min_investment_usd=Decimal("1"), base_leverage=1, max_leverage=5)
    res = RiskSizer(params).size(_intent("X"), _account(), inst, Decimal("100"))
    assert not res.ok
    assert "insufficient capital" in res.reason


def test_capital_limit_blocks_open(btc_instrument: Instrument):
    params = RiskParams(min_investment_usd=Decimal("10"), max_capital_usd=Decimal("5"))
    res = RiskSizer(params).size(_intent(), _account(), btc_instrument, Decimal("100"))
    assert not res.ok
    assert "capital limit" in res.reason


def test_close_uses_opposite_side(btc_instrument: Instrument):
    from app.domain.types import Position

    acct = _account()
    acct.positions[("BTCUSDT", PositionSide.LONG)] = Position(
        symbol="BTCUSDT",
        position_side=PositionSide.LONG,
        qty=Decimal("0.5"),
        entry_price=Decimal("100"),
        leverage=5,
        committed_margin=Decimal("10"),
    )
    intent = TradeIntent(
        symbol="BTCUSDT",
        side=Side.SELL,
        action=IntentAction.CLOSE,
        position_side=PositionSide.LONG,
    )
    res = RiskSizer(RiskParams()).size(intent, acct, btc_instrument, Decimal("110"))
    assert res.ok
    assert res.request is not None
    assert res.request.reduce_only is True
    assert res.request.side is Side.SELL
    assert res.request.qty == Decimal("0.5")
