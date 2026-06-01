"""Configuration CRUD (REQ-009).

API keys, risk configs, strategy configs and generic app settings all live in the
database and are edited here. API secrets are encrypted at rest and never returned
in plaintext — only a masked form, plus a credential-test action.
"""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.serialization import row_to_dict, rows_to_list
from app.core.security import decrypt_secret, encrypt_secret, mask_secret
from app.db.models import ApiKey, AppSetting, RiskConfig, StrategyConfig
from app.db.session import get_session
from app.exchange.bitunix.rest import BitunixRest
from app.strategies import available_strategies

router = APIRouter(prefix="/config", tags=["config"])


# --------------------------------------------------------------------------- #
# Strategies (JSON schema for auto-generated forms)
# --------------------------------------------------------------------------- #
@router.get("/strategies")
async def list_strategies():
    """Return ``{name: json_schema}`` for every registered strategy."""
    return available_strategies()


# --------------------------------------------------------------------------- #
# API keys
# --------------------------------------------------------------------------- #
class ApiKeyIn(BaseModel):
    name: str
    exchange: str = "bitunix"
    api_key: str
    secret: str


def _api_key_public(row: ApiKey) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "exchange": row.exchange,
        "api_key_masked": mask_secret(row.api_key),
        "is_active": row.is_active,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


@router.get("/api-keys")
async def list_api_keys(session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(select(ApiKey).order_by(ApiKey.id))).scalars().all()
    return [_api_key_public(r) for r in rows]


@router.post("/api-keys", status_code=201)
async def create_api_key(body: ApiKeyIn, session: AsyncSession = Depends(get_session)):
    row = ApiKey(
        name=body.name,
        exchange=body.exchange,
        api_key=body.api_key,
        secret_encrypted=encrypt_secret(body.secret),
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return _api_key_public(row)


@router.delete("/api-keys/{key_id}", status_code=204)
async def delete_api_key(key_id: int, session: AsyncSession = Depends(get_session)):
    await session.execute(delete(ApiKey).where(ApiKey.id == key_id))
    await session.commit()


@router.post("/api-keys/{key_id}/test")
async def test_api_key(key_id: int, session: AsyncSession = Depends(get_session)):
    row = await session.get(ApiKey, key_id)
    if row is None:
        raise HTTPException(404, "api key not found")
    secret = decrypt_secret(row.secret_encrypted)
    async with BitunixRest(api_key=row.api_key, secret_key=secret) as rest:
        ok = await rest.test_credentials()
    return {"ok": ok}


# --------------------------------------------------------------------------- #
# Risk configs
# --------------------------------------------------------------------------- #
class RiskConfigIn(BaseModel):
    name: str
    max_capital_usd: Decimal = Decimal("100")
    max_loss_usd: Decimal = Decimal("50")
    min_investment_usd: Decimal = Decimal("1")
    base_leverage: int = 1
    max_leverage: int = 20
    leverage_step: int = 1
    allow_hedge: bool = True
    fee_rate: Decimal = Decimal("0.0006")


@router.get("/risk-configs")
async def list_risk_configs(session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(select(RiskConfig).order_by(RiskConfig.id))).scalars().all()
    return rows_to_list(list(rows))


@router.post("/risk-configs", status_code=201)
async def create_risk_config(body: RiskConfigIn, session: AsyncSession = Depends(get_session)):
    row = RiskConfig(**body.model_dump())
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row_to_dict(row)


@router.put("/risk-configs/{config_id}")
async def update_risk_config(
    config_id: int, body: RiskConfigIn, session: AsyncSession = Depends(get_session)
):
    row = await session.get(RiskConfig, config_id)
    if row is None:
        raise HTTPException(404, "risk config not found")
    for key, value in body.model_dump().items():
        setattr(row, key, value)
    await session.commit()
    await session.refresh(row)
    return row_to_dict(row)


@router.delete("/risk-configs/{config_id}", status_code=204)
async def delete_risk_config(config_id: int, session: AsyncSession = Depends(get_session)):
    await session.execute(delete(RiskConfig).where(RiskConfig.id == config_id))
    await session.commit()


# --------------------------------------------------------------------------- #
# Strategy configs
# --------------------------------------------------------------------------- #
class StrategyConfigIn(BaseModel):
    name: str
    strategy: str
    params: dict = {}
    risk_config_id: int | None = None
    enabled: bool = True


@router.get("/strategy-configs")
async def list_strategy_configs(session: AsyncSession = Depends(get_session)):
    rows = (
        await session.execute(select(StrategyConfig).order_by(StrategyConfig.id))
    ).scalars().all()
    return rows_to_list(list(rows))


@router.post("/strategy-configs", status_code=201)
async def create_strategy_config(
    body: StrategyConfigIn, session: AsyncSession = Depends(get_session)
):
    row = StrategyConfig(**body.model_dump())
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row_to_dict(row)


@router.put("/strategy-configs/{config_id}")
async def update_strategy_config(
    config_id: int, body: StrategyConfigIn, session: AsyncSession = Depends(get_session)
):
    row = await session.get(StrategyConfig, config_id)
    if row is None:
        raise HTTPException(404, "strategy config not found")
    for key, value in body.model_dump().items():
        setattr(row, key, value)
    await session.commit()
    await session.refresh(row)
    return row_to_dict(row)


@router.delete("/strategy-configs/{config_id}", status_code=204)
async def delete_strategy_config(
    config_id: int, session: AsyncSession = Depends(get_session)
):
    await session.execute(delete(StrategyConfig).where(StrategyConfig.id == config_id))
    await session.commit()


# --------------------------------------------------------------------------- #
# Generic app settings
# --------------------------------------------------------------------------- #
class SettingIn(BaseModel):
    value: dict
    description: str = ""


@router.get("/settings")
async def list_settings(session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(select(AppSetting))).scalars().all()
    return rows_to_list(list(rows))


@router.put("/settings/{key}")
async def upsert_setting(
    key: str, body: SettingIn, session: AsyncSession = Depends(get_session)
):
    row = await session.get(AppSetting, key)
    if row is None:
        row = AppSetting(key=key, value=body.value, description=body.description)
        session.add(row)
    else:
        row.value = body.value
        row.description = body.description
    await session.commit()
    return {"key": key, "value": body.value}
