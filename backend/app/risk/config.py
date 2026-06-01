"""Risk configuration model.

Loaded from the DB (``risk_configs``) and edited from the frontend. ``min_investment_usd``
is the *committed margin* in USD; it stays constant regardless of the leverage
multiplier, which is exactly the behaviour requested in REQ-007 (set 1 USD -> 1 USD
committed whether the multiplier is 5x or 10x).
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field


class RiskParams(BaseModel):
    """Per-strategy risk and capital parameters."""

    max_capital_usd: Decimal = Field(
        default=Decimal("100"),
        description="Maximum committed margin (usable/losable capital) for the strategy.",
    )
    max_loss_usd: Decimal = Field(
        default=Decimal("50"),
        description="Maximum estimated loss budget for the strategy.",
    )
    min_investment_usd: Decimal = Field(
        default=Decimal("1"),
        description="Committed margin per ladder step, independent of leverage.",
    )
    base_leverage: int = Field(default=1, ge=1, description="Starting leverage multiplier.")
    max_leverage: int = Field(default=20, ge=1, description="Cap for leverage escalation.")
    leverage_step: int = Field(default=1, ge=1, description="Increment when escalating.")
    allow_hedge: bool = Field(
        default=True, description="Allow opposite-direction positions on the same symbol."
    )
    fee_rate: Decimal = Field(
        default=Decimal("0.0006"), description="Taker fee rate used by the simulated broker."
    )

    model_config = {"extra": "ignore"}
