"""Start/stop runs (REQ-003/011).

The API does not execute engines; it publishes a control command to Kafka and the
``trading-worker`` runs the engine. For live runs it loads and decrypts the chosen
API key here (the worker receives the credentials inline over the intra-cluster
control topic).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import decrypt_secret
from app.db.models import ApiKey, RiskConfig
from app.db.session import get_session
from app.domain.types import Mode
from app.events.control import ControlCommand
from app.events.topics import get_topics
from app.risk.config import RiskParams
from app.services.run_config import RunConfig

router = APIRouter(prefix="/control", tags=["control"])



async def require_control_token(x_control_token: str | None = Header(default=None)) -> None:
    """Optional guard for control write actions.

    Dev remains frictionless when CONTROL_API_TOKEN is empty. In production set it
    and send the same value in the X-Control-Token header before start/stop/live
    actions are accepted.
    """
    expected = get_settings().control_api_token
    if expected and x_control_token != expected:
        raise HTTPException(status_code=401, detail="invalid control token")


class StartRunIn(BaseModel):
    mode: Mode
    strategy: str
    params: dict = {}
    risk: RiskParams | None = None
    risk_config_id: int | None = None
    symbols: list[str] = []
    interval: str = "1m"
    initial_capital: Decimal = Decimal("1000")
    api_key_id: int | None = None
    backtest_start: datetime | None = None
    backtest_end: datetime | None = None
    backtest_limit: int = 1000


async def _resolve_risk(body: StartRunIn, session: AsyncSession) -> RiskParams:
    if body.risk is not None:
        return body.risk
    if body.risk_config_id is not None:
        row = await session.get(RiskConfig, body.risk_config_id)
        if row is None:
            raise HTTPException(404, "risk config not found")
        return RiskParams(
            max_capital_usd=row.max_capital_usd,
            max_loss_usd=row.max_loss_usd,
            min_investment_usd=row.min_investment_usd,
            base_leverage=row.base_leverage,
            max_leverage=row.max_leverage,
            leverage_step=row.leverage_step,
            allow_hedge=row.allow_hedge,
            fee_rate=row.fee_rate,
        )
    return RiskParams()


@router.post("/start")
async def start_run(
    body: StartRunIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _guard: None = Depends(require_control_token),
):
    risk = await _resolve_risk(body, session)
    config = RunConfig(
        mode=body.mode,
        strategy=body.strategy,
        params=body.params,
        risk=risk,
        symbols=body.symbols,
        interval=body.interval,
        initial_capital=body.initial_capital,
        api_key_id=body.api_key_id,
        backtest_start=body.backtest_start,
        backtest_end=body.backtest_end,
        backtest_limit=body.backtest_limit,
    )

    api_key = secret = ""
    if Mode(body.mode) is Mode.LIVE and body.api_key_id is not None:
        row = await session.get(ApiKey, body.api_key_id)
        if row is None:
            raise HTTPException(404, "api key not found")
        api_key = row.api_key
        secret = decrypt_secret(row.secret_encrypted)

    command = ControlCommand(
        action="start", config=config, api_key=api_key, secret_key=secret
    )
    await _publish(request, command)
    return {"run_id": config.run_id, "mode": str(config.mode)}


@router.post("/stop/{run_id}")
async def stop_run(run_id: str, request: Request, _guard: None = Depends(require_control_token)):
    await _publish(request, ControlCommand(action="stop", run_id=run_id))
    return {"run_id": run_id, "stopping": True}


async def _publish(request: Request, command: ControlCommand) -> None:
    producer = getattr(request.app.state, "control_producer", None)
    topics = get_topics()
    if producer is None:
        raise HTTPException(503, "control bus unavailable")
    await producer.send_and_wait(topics.control, command.model_dump_json().encode())
