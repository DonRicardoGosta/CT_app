# REQ-011 — Docker deployment

- **id**: REQ-011
- **status**: done
- **priority**: must
- **components**: docker-compose, backend/Dockerfile, frontend/Dockerfile

## Igény

Dockerben akarok futtatni mindent, és csak 1 frontend appom legyen.

## Megoldás

Egyetlen `docker compose up` indítja a teljes rendszert:

| Service | Szerep |
| --- | --- |
| `postgres` | PostgreSQL adatbázis |
| `redpanda` | Kafka-kompatibilis üzenetsor |
| `backend-api` | FastAPI (REST + WS) |
| `trading-worker` | a kereskedési engine (live/dry/backtest futtatás) |
| `db-writer` | Kafka → PostgreSQL consumer |
| `frontend` | az egyetlen React SPA (nginx-en kiszolgálva) |

- A `backend-api`, `trading-worker`, `db-writer` ugyanabból a backend image-ből indul,
  más-más belépési ponttal.
- A frontend külön, könnyű image (build → nginx static serve).
- Healthcheck és `depends_on` a helyes indítási sorrendért.
- Csak a DB DSN + titkosító kulcs jön env-ből (REQ-009).

## Elfogadási kritérium

- [x] `docker compose up` az egész stacket elindítja.
- [x] Pontosan egy frontend service.
- [x] A backend service-ek közös image-ből, külön entrypointtal.

## Kapcsolódó

- REQ-004, REQ-009.
