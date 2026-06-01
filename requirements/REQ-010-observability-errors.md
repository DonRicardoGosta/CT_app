# REQ-010 — Observability és hiba-naplózás

- **id**: REQ-010
- **status**: done
- **priority**: must
- **components**: backend/events, backend/db, workers/db_writer

## Igény

Minden fontos adat, beleértve a hibákat is, ami bárhonnan jöhet, legyen beleírva a DB-be.
A DB legyen jól indexelve — nem baj, ha mindent beindexelünk.

## Megoldás

- Strukturált logolás (`structlog`), ami a fontos eseményeket és **minden hibát** Kafka
  `errors` topicra is kitesz (komponens, súlyosság, üzenet, stacktrace, kontextus).
- A `db_writer` ezeket az `error_log`, `event_log`, `orders`, `fills`, `positions`,
  `signals`, `equity_snapshots` táblákba írja.
- A trading hot path-on a hiba-kibocsátás nem-blokkoló (Kafka), így nem lassít.
- Globális exception handlerek (FastAPI, worker-ek, engine loop) → minden elkapott
  hiba a `errors` topicra kerül.

## Indexelés

- Minden gyakori szűrő/lekérdezési mezőre index (időbélyeg, symbol, strategy, run_id,
  status, severity, source). Időbélyeg szerinti lekérdezésekhez BRIN/B-tree indexek.

## Elfogadási kritérium

- [x] Minden komponens hibái a DB-be kerülnek (Kafkán át).
- [x] A hot path-on a naplózás nem-blokkoló.
- [x] A fő táblák erősen indexeltek a lekérdezési mintákhoz.

## Kapcsolódó

- REQ-004, REQ-008 (Logs & Errors oldal).
