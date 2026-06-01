# REQ-001 — Architektúra és mode-független mag

- **id**: REQ-001
- **status**: done
- **priority**: must
- **components**: backend/domain, backend/services

## Igény

Legyen egy gyors, jól dokumentált, könnyen bővíthető Python app, ahol a kereskedés
**logikai döntései el vannak különítve** a végrehajtástól, és **ugyanaz a kód fut**
mindhárom módban (live, dry-run, backtest). Ettől garantált, hogy ha valamit élesben
futtatok, majd visszatesztelem backtesttel, ugyanaz az eredmény jön ki (a nyitási ár-
csúszást leszámítva).

## Megoldás

A magot három cserélhető absztrakció köré építjük, minden más mode-független:

- `Clock` — idő forrása (valós idő vs szimulált, gyorsított idő).
- `MarketDataFeed` — piaci adat forrása (élő WebSocket vs historikus visszajátszás).
- `Broker` — order végrehajtás (élő Bitunix REST vs szimulált fill élő/historikus áron).

Az `Engine` ciklusa és a `Strategy` kód **azonos** minden módban; a fenti három
komponenst injektáljuk módfüggően. A `Strategy` tisztán piaci adatból és portfólió-
állapotból dolgozik, **nem végez I/O-t**.

## Elfogadási kritérium

- [x] A `Strategy` interfész nem hivatkozik közvetlenül hálózatra/DB-re.
- [x] Egy `Engine` osztály mindhárom módot kiszolgálja (csak DI különbözik).
- [x] Determinisztikus döntéshozatal: azonos input → azonos jelek (lásd REQ-003).
- [x] Új komponens (feed/broker/clock) hozzáadása nem igényli az engine módosítását.

## Kapcsolódó

- REQ-003 (módok), REQ-005 (stratégia framework), REQ-002 (Bitunix).
