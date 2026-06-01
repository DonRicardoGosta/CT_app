# REQ-008 — Frontend (egyetlen SPA)

- **id**: REQ-008
- **status**: done
- **priority**: must
- **components**: frontend

## Igény

Egyetlen frontend app. Mindent látni akarok, mindent be akarok tudni állítani
(API kulcsokat is). Legyen szép és gyors. A DB-ből lekérdező oldalak és a WS-t használó
oldalak legyenek külön, hogy aminek gyorsnak kell lennie, az tényleg gyors legyen.

## Tech

- React + TypeScript + Vite, Tailwind + shadcn/ui, TanStack Router + Query, Zustand.
- Chartok: TradingView lightweight-charts (ár/equity), Recharts (analytics).
- Sötét téma alapból; tipizált, auto-reconnect WebSocket kliens.

## Csatorna-szétválasztás

- **Realtime oldalak**: egyetlen multiplexelt WS kapcsolat (témák: positions, orders,
  fills, tickers, equity, risk, errors). Nincs DB a hot path-ban.
- **History/analytics oldalak**: REST + TanStack Query cache a DB-ből.

## Oldalak

1. Dashboard (realtime)
2. Live Trading (realtime): pozíciók, orderek, fillek, tickerek, per-coin chart,
   gyors close/reduce, live/dry toggle, PANIC close-all.
3. Strategies: séma-vezérelt paraméter-szerkesztő, futás indítása módonként.
4. Risk & Capital: tőke/leverage/limit beállítás, valós idejű kihasználtság.
5. Backtest: futtatás + eredmények + összevetés dry-run/live eredménnyel.
6. History & Analytics (DB)
7. Logs & Errors (DB): minden hiba bárhonnan, szűrhetően.
8. System Health (DB): Kafka lag, db_writer státusz, WS kapcsolatok.
9. Settings: API kulcsok (maszkolt, kapcsolat-teszt), minden config frontendről.

## Elfogadási kritérium

- [x] Egyetlen build-elhető SPA.
- [x] Realtime és DB oldalak külön adatcsatornán.
- [x] API kulcsok és minden config a frontendről állítható.
- [x] Stratégia paraméter-űrlap a backend JSON-sémájából auto-generált.

## Kapcsolódó

- REQ-009, REQ-010, REQ-003.
