# Backend

Python (FastAPI) backend for the Bitunix trading platform. See the repository root
`README.md` for the full architecture and the `requirements/` folder for the spec.

## Layout

- `app/domain/` — mode-agnostic core (`Clock`, `MarketDataFeed`, `Broker`, `Engine`).
- `app/strategies/` — pluggable strategies + registry (`autoscan_ladder`).
- `app/risk/` — capital & leverage sizing.
- `app/exchange/bitunix/` — REST signing, REST and WebSocket clients.
- `app/events/` — Kafka event schemas, producer, consumer, control commands.
- `app/db/` — SQLAlchemy models (heavily indexed), repositories, session.
- `app/api/` — `realtime/` (WS), `history/` (DB REST), `config/` + `control/` (REST).
- `app/services/` — run config, engine builder, run manager.
- `app/workers/` — `trading_worker` (engines) and `db_writer` (Kafka → PostgreSQL).

## Develop

```bash
python -m venv .venv && source .venv/bin/activate   # or: virtualenv .venv
pip install -e ".[dev]"
pytest               # unit tests incl. backtest == dry-run equivalence
ruff check . && mypy app
```

## Migrations

```bash
alembic upgrade head
```
