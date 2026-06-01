# REQ-009 — Config a DB-ben, frontendről állítva

- **id**: REQ-009
- **status**: done
- **priority**: must
- **components**: backend/db, backend/api/config, frontend/settings

## Igény

Ne legyen olyan konfig, amit env-ből kell beállítani. Mindent a frontenden akarok
beállítani, még az API kulcsokat is. A DB connection viszont lehet env-ben.

## Megoldás

- **Csak** a DB connection string (és az érzékeny adatok titkosító kulcsa) jön env-ből.
  Minden más beállítás a DB-ben él és a frontendről CRUD-olható.
- `api_keys` tábla: a Bitunix kulcs/secret titkosítva tárolva (Fernet, kulcs env-ből).
  Az API soha nem adja vissza a plaintext secretet, csak maszkolt formát + "teszt" akciót.
- `app_settings` tábla: kulcs-érték (vagy JSON) általános beállítások.
- `strategy_configs` / `risk_configs` táblák: stratégia- és risk-paraméterek.
- Config REST API (`app/api/config`) a frontend számára.

## Elfogadási kritérium

- [x] Csak a DB DSN + titkosító kulcs env-ből; semmi más kötelező env.
- [x] API kulcsok titkosítva a DB-ben; plaintext secret nem hagyja el a backendet.
- [x] Minden beállítás a frontendről módosítható.

## Kapcsolódó

- REQ-008, REQ-002, REQ-010.
