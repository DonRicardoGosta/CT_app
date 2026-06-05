# Bitunix Futures Trading Platform

A fast, well-documented, easily extensible trading platform for **Bitunix futures**.

The defining property of this system: **the same strategy and engine code runs in
live, dry-run and backtest mode**. Only three injected components differ between
modes — the `Clock`, the `MarketDataFeed` and the `Broker`. As a result, running a
strategy live and then re-running it in backtest produces the same decisions and
result (apart from entry slippage). See [`requirements/`](requirements/) for the full
spec — that folder is used instead of Jira.

## Architecture

```
                 ┌──────────────────────── Single React SPA ────────────────────────┐
                 │  Realtime pages (WebSocket)   History/analytics pages (REST/DB)    │
                 └───────────────┬───────────────────────────────┬───────────────────┘
                                 │ WS                             │ HTTP
                 ┌───────────────▼───────────────┐ ┌─────────────▼─────────────┐
                 │   backend-api (FastAPI)        │ │  config + history REST    │
                 │   realtime WS multiplexer      │ │  (DB queries)             │
                 └───────────────┬───────────────┘ └─────────────┬─────────────┘
                                 │                                │
   ┌──────────────┐   events  ┌──▼───────────────┐            ┌──▼──────────┐
   │ trading-worker│──────────▶│  Kafka / Redpanda │───────────▶│ db-writer   │
   │ (Engine)      │           └───────────────────┘            │ consumer    │
   └──────┬────────┘                                            └──────┬──────┘
          │ REST/WS                                                    │
   ┌──────▼────────┐                                            ┌──────▼──────┐
   │ Bitunix API   │                                            │ PostgreSQL  │
   └───────────────┘                                            └─────────────┘
```

The trading hot path never writes to PostgreSQL directly. It publishes events to
Kafka; the `db-writer` consumer persists them asynchronously so the database can
never slow down trading (REQ-004).

## Components

| Path | Purpose |
| --- | --- |
| `backend/app/domain/` | Mode-agnostic core: `Clock`, `MarketDataFeed`, `Broker`, `Engine`. |
| `backend/app/strategies/` | Pluggable strategies + registry: `autoscan_ladder`, `trend_scanner`, `guarded_ladder`. |
| `backend/app/risk/` | Capital & leverage sizing (min investment, multiplier escalation). |
| `backend/app/exchange/bitunix/` | REST signing + WS client. |
| `backend/app/events/` | Kafka event schemas, producer, consumer. |
| `backend/app/db/` | SQLAlchemy models (heavily indexed), repositories. |
| `backend/app/api/` | `realtime/` (WS), `history/` (DB REST), `config/` (CRUD REST). |
| `backend/app/workers/` | `trading_worker` (engine) and `db_writer` (Kafka→PG) entrypoints. |
| `frontend/` | The single React SPA. |
| `requirements/` | Requirement tickets (used instead of Jira). |

## Running

Everything runs in Docker:

```bash
cp .env.example .env       # only infra config (DB DSN, Kafka, encryption key)
docker compose up --build
```

Then open the frontend at http://localhost:5173 (dev) / http://localhost:8080
(compose). Configure your Bitunix API keys, strategies and risk limits entirely
from the **Settings** and **Strategies** pages — nothing trading-related is set via
environment variables (REQ-009).

## Local development (without Docker)

Backend:

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest                     # unit tests incl. backtest==dry-run equivalence
ruff check . && mypy app
```

Frontend:

```bash
cd frontend
npm install
npm run dev
```

## Strategies

| Name | Idea |
| --- | --- |
| `autoscan_ladder` | EMA(fast)/EMA(slow) cross with laddered entries. |
| `trend_scanner` | Trend regime + RSI-pullback entries with scaled exits. |
| `guarded_ladder` | Breakout/momentum trend entries with DCA add-ins, an arbitrary number of scaled take-profit legs, a multi-stage moving stop, and a **capital-drawdown kill switch**. |

### `guarded_ladder`

Designed for a small, capped account. It trades many coins, opens multiple entries
per coin (DCA on pullbacks), takes profit at an arbitrary number of scaled legs
(`tp_levels_pct` / `tp_close_pct`, comma-separated), and runs a moving stop
(initial → breakeven → trailing). Its defining feature is a **capital-drawdown kill
switch**: once equity falls `max_drawdown_pct` (default `60`) below the starting
capital, it flattens all positions and stops opening new ones for the rest of the run
("stop trading after losing 60% of capital").

Defaults were tuned against real Bitunix data (BTC/ETH/SOL/XRP/BNB/DOGE) and were net
profitable across the basket on the **5m and 15m** intervals (the recommended
timeframes); 1m whipsaws this breakout logic badly and 1h performed worse, so the
launcher defaults the candle interval to **15m**. Past backtest performance never
guarantees future results — always re-validate with a backtest before going live.

**Recommended risk preset (Risk & capital panel)** for the ~50 USD / 5 USDT-margin /
20x setup:

| Field | Value |
| --- | --- |
| `max_capital_usd` | `50` |
| `min_investment_usd` | `5` |
| `base_leverage` | `20` |
| `max_leverage` | `50` (lets the sizer escalate to meet exchange minimums) |
| `leverage_step` | `1` |
| `max_loss_usd` | `30` |

`max_capital_usd = 50` with 5 USD/step caps total deployed margin; the 60% halt lives
in the strategy (`max_drawdown_pct`). With many coins and 20x, simultaneous correlated
positions raise account drawdown — lowering `max_symbols` (e.g. to 3–5) reduces that
risk if the kill switch trips too often.

## Modes

| Mode | Clock | Feed | Broker |
| --- | --- | --- | --- |
| `live` | real time | live WS | real Bitunix REST |
| `dry-run` | real time | live WS | simulated fills at live price |
| `backtest` | simulated | historical klines | simulated fills at historical price |

## Safety

Live order placement is implemented but **dry-run is the default**. Switching a run
to `live` is an explicit action from the frontend.
