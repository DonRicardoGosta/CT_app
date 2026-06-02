"""Live account balance from the exchange (REQ-009).

Reads the real Bitunix futures account using a stored, decrypted API key. Used by
the Live dashboard so the balance reflects the exchange account rather than a
simulated per-run balance.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decrypt_secret
from app.db.models import ApiKey
from app.db.session import get_session
from app.exchange.bitunix.rest import BitunixRest

router = APIRouter(prefix="/account", tags=["account"])


def _dec(data: dict[str, Any], *keys: str) -> Decimal | None:
    for k in keys:
        if k in data and data[k] is not None:
            try:
                return Decimal(str(data[k]))
            except (InvalidOperation, ValueError):
                continue
    return None


def _summarize(account: Any) -> dict[str, Any]:
    """Best-effort extraction of balance/equity from the Bitunix account payload."""
    data = account if isinstance(account, dict) else {}
    available = _dec(data, "available", "availableBalance")
    margin = _dec(data, "margin", "positionMargin", "isolationMargin")
    frozen = _dec(data, "frozen", "orderMargin")
    unrealized = _dec(data, "crossUnrealizedPNL", "unrealizedPNL", "isolationUnrealizedPNL")
    balance = _dec(data, "balance", "marginBalance", "accountBalance")
    if balance is None:
        balance = sum(
            (v for v in (available, margin, frozen) if v is not None), Decimal("0")
        ) or None
    equity = None
    if balance is not None:
        equity = balance + (unrealized or Decimal("0"))
    return {
        "margin_coin": data.get("marginCoin", "USDT"),
        "balance": str(balance) if balance is not None else None,
        "available": str(available) if available is not None else None,
        "margin": str(margin) if margin is not None else None,
        "unrealized_pnl": str(unrealized) if unrealized is not None else None,
        "equity": str(equity) if equity is not None else None,
        "raw": data,
    }


@router.get("/balance")
async def account_balance(
    api_key_id: int | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
):
    row: ApiKey | None
    if api_key_id is not None:
        row = await session.get(ApiKey, api_key_id)
    else:
        row = (
            await session.execute(
                select(ApiKey).where(ApiKey.is_active.is_(True)).order_by(ApiKey.id)
            )
        ).scalars().first()
    if row is None:
        raise HTTPException(404, "no API key configured")

    rest = BitunixRest(api_key=row.api_key, secret_key=decrypt_secret(row.secret_encrypted))
    try:
        account = await rest.get_account()
    except Exception as exc:  # noqa: BLE001 - surface a clean error to the UI
        raise HTTPException(502, f"exchange account fetch failed: {exc}") from exc
    finally:
        await rest.close()
    return _summarize(account)
