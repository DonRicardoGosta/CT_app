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
| `backend/app/strategies/` | Pluggable strategies + registry, incl. `autoscan_ladder`. |
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

## Modes

| Mode | Clock | Feed | Broker |
| --- | --- | --- | --- |
| `live` | real time | live WS | real Bitunix REST |
| `dry-run` | real time | live WS | simulated fills at live price |
| `backtest` | simulated | historical klines | simulated fills at historical price |

## Safety

Live order placement is implemented but **dry-run is the default**. Switching a run
to `live` is an explicit action from the frontend.
