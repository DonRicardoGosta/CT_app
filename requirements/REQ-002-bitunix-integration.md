# REQ-002 — Bitunix futures integráció

- **id**: REQ-002
- **status**: done
- **priority**: must
- **components**: backend/exchange/bitunix

## Igény

Bitunix futureson kereskedünk. Kell REST (order kezelés, account, historikus kline)
és WebSocket (élő kline, ticker, depth, privát order/pozíció push).

## Tények (hivatalos Bitunix OpenAPI alapján)

- REST base: `https://fapi.bitunix.com`
- Public WS: `wss://fapi.bitunix.com/public/`
- Private WS: `wss://fapi.bitunix.com/private/`
- Aláírás: dupla SHA256.
  - REST: `digest = SHA256(nonce + timestamp + api-key + queryParams + body)`,
    `sign = SHA256(digest + secretKey)`. queryParams ASCII kulcs szerint rendezve,
    body szóközök nélküli JSON.
  - WS: `params` mezők (kivéve `sign`) ASCII szerint rendezve, majd ugyanaz a dupla hash.
- Fő endpointok:
  - `GET /api/v1/futures/market/trading_pairs`
  - `GET /api/v1/futures/market/kline`
  - `POST /api/v1/futures/trade/place_order`
  - account / positions lekérdezés
- Rate limit: pl. 10 req/sec/ip a market endpointokon.
- Nincs hivatalos Python SDK → `httpx` (REST) + `websockets` (WS).

## Megoldás

- `app/exchange/bitunix/signing.py` — aláírás-segédek (REST + WS), unit tesztelhető.
- `app/exchange/bitunix/rest.py` — async REST kliens (httpx), rate-limit aware.
- `app/exchange/bitunix/ws.py` — async WS kliens, auto-reconnect, feliratkozás.
- `app/exchange/bitunix/models.py` — tipizált request/response sémák.

## Elfogadási kritérium

- [x] Aláírás determinisztikus és unit-tesztelt (ismert input → ismert hash).
- [x] REST kliens kezeli a fejléceket (api-key, nonce, timestamp, sign).
- [x] WS kliens újracsatlakozik és újra-feliratkozik szakadás után.
- [x] API kulcsok nem env-ből, hanem a DB-ből jönnek (REQ-009).
