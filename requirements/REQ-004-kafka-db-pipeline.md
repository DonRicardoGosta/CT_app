# REQ-004 — Kafka → PostgreSQL pipeline

- **id**: REQ-004
- **status**: done
- **priority**: must
- **components**: backend/events, workers/db_writer

## Igény

PostgreSQL-t akarok használni, de **ne lassítsa az appot**. Legyen Kafka, ami a hot
path-ról leválasztva, akkor írja a DB-be a dolgokat, amikor ráér.

## Megoldás

- A kereskedési hot path (engine, broker, feed) **nem ír közvetlenül DB-be**. Helyette
  eseményeket publikál Kafka topicokra (`aiokafka` producer, fire-and-forget, bufferelt).
- Külön `db_writer` worker fogyasztja a topicokat és batch-elve írja PostgreSQL-be.
- Ha a DB lassú vagy elérhetetlen, a Kafka pufferel; a trading nem áll meg.
- Topicok pl.: `orders`, `fills`, `positions`, `signals`, `equity`, `errors`, `market`.

## Megfontolások

- A producer `acks=1`, linger-rel batch-el, hogy minimális legyen a latency a hot path-on.
- A consumer idempotens upsertet használ (event id / natural key), hogy a re-delivery ne
  duplikáljon.
- Helyi fejlesztéshez Redpanda (Kafka-kompatibilis, könnyű) a docker-compose-ban.

## Elfogadási kritérium

- [x] Az engine kód nem hív DB-t; csak eseményt publikál.
- [x] `db_writer` consumer batch insert/upsert-tel ír.
- [x] DB leállás esetén a trading folytatódik (puffer Kafkában).

## Kapcsolódó

- REQ-010 (observability), REQ-001.
