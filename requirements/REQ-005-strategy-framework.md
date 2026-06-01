# REQ-005 — Stratégia framework (plugin)

- **id**: REQ-005
- **status**: done
- **priority**: must
- **components**: backend/strategies

## Igény

Könnyen lehessen új stratégiát hozzáadni. A kereskedés logikai döntéseit el kell
különíteni, hogy egyszerűen cserélhető legyen.

## Megoldás

- `Strategy` absztrakt bázis: bemenet a piaci adat + portfólió állapot, kimenet
  szándékok (`Intent`/`Signal`) listája. Nincs I/O.
- `@register_strategy("nev")` dekorátor + `registry.py` a felfedezéshez.
- Minden stratégia deklarál egy **paraméter-sémát** (pydantic modell), amiből a backend
  JSON-sémát ad a frontendnek → a beállító űrlap automatikusan generálódik (REQ-008).
- Új stratégia = új fájl a `strategies/`-ben + regisztráció. Nincs engine-módosítás.

## Elfogadási kritérium

- [x] `Strategy` interfész tiszta (nincs hálózat/DB benne).
- [x] Registry-ből listázhatók és példányosíthatók a stratégiák név alapján.
- [x] Paraméter-séma kinyerhető JSON-sémaként az API-n keresztül.
- [x] Legalább egy referencia-implementáció (REQ-006).

## Kapcsolódó

- REQ-001, REQ-006, REQ-008.
