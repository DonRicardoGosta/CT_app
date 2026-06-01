# Requirements (Jira helyett)

Ez a mappa a projekt összes igényét tartalmazza, requirementenként egy `.md` fájlban.
Ezt használjuk Jira / issue tracker helyett: minden ticketnek van azonosítója, státusza,
leírása, elfogadási kritériuma és kapcsolódó komponensei.

## Konvenciók

- Fájlnév: `REQ-XYZ-rovid-nev.md`
- Minden ticket tetején metaadat blokk (id, cím, státusz, prioritás, komponensek).
- Státuszok: `todo` → `in-progress` → `done`.
- Egy ticket egy jól körülhatárolt igényt ír le. Ha egy igény túl nagy, bontsd al-ticketekre.

## Ticketek

| ID | Cím | Státusz |
| --- | --- | --- |
| [REQ-001](REQ-001-architecture.md) | Architektúra és mode-független mag | done |
| [REQ-002](REQ-002-bitunix-integration.md) | Bitunix futures integráció | done |
| [REQ-003](REQ-003-modes-live-dry-backtest.md) | Live / Dry-run / Backtest módok | done |
| [REQ-004](REQ-004-kafka-db-pipeline.md) | Kafka → PostgreSQL pipeline | done |
| [REQ-005](REQ-005-strategy-framework.md) | Stratégia framework (plugin) | done |
| [REQ-006](REQ-006-first-strategy-autoscan.md) | Első stratégia: autoscan ladder | done |
| [REQ-007](REQ-007-risk-capital-management.md) | Risk és tőke/leverage kezelés | done |
| [REQ-008](REQ-008-frontend.md) | Frontend (egyetlen SPA) | done |
| [REQ-009](REQ-009-config-in-db.md) | Config a DB-ben, frontendről állítva | done |
| [REQ-010](REQ-010-observability-errors.md) | Observability és hiba-naplózás | done |
| [REQ-011](REQ-011-docker-deployment.md) | Docker deployment | done |

## Hogyan adj hozzá új igényt

1. Másold le egy meglévő ticket szerkezetét.
2. Adj neki új `REQ-XYZ` azonosítót.
3. Vedd fel a fenti táblázatba.
4. Tartsd a státuszt naprakészen, ahogy halad a munka.
