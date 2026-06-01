# REQ-006 — Első stratégia: autoscan ladder

- **id**: REQ-006
- **status**: done
- **priority**: must
- **components**: backend/strategies/autoscan_ladder

## Igény

A legelső stratégia automatikusan találjon magának néhány coint, amivel kereskedik, és
**lépcsőzetesen** nyisson pozíciókat. Akár több kis pozíciót ugyanahhoz a coinhoz,
azonos vagy akár ellentétes irányba is. A cél: profit termelése és a risk kezelése,
hogy ne veszítsek.

## Viselkedés

1. **Coin felfedezés**: a `trading_pairs` + historikus/élő kline alapján szűr
   likviditásra és volatilitásra; kiválaszt N coint (paraméterezhető).
2. **Jel**: egyszerű, determinisztikus trend/mean-reversion jelzés (pl. EMA-kereszt +
   ATR-alapú szűrés). A logika a stratégiában van, könnyen cserélhető.
3. **Lépcsőzetes nyitás (ladder)**: a teljes szándékolt kitettséget több kisebb lépésre
   bontja; árszintenként (vagy idő-/jel-trigger) adagol. Ugyanahhoz a coinhoz több
   pozíció-lépcső is tartozhat, azonos vagy ellentétes irányban (hedge-mode támogatás).
4. **Méretezés**: a REQ-007 risk-méretezőt használja (min. befektetés USD, szorzó,
   szorzó-emelés ha a notional a tőzsdei minimum alatt van).
5. **Risk-kezelés**: stratégia-szintű max. felhasználható/elveszíthető tőke; stop/risk
   kilépés; ha a limit kimerül, nem nyit többet.

## Paraméterek (séma)

- `max_symbols`, `min_quote_volume`, `volatility_lookback`, `ema_fast`, `ema_slow`,
  `ladder_steps`, `ladder_step_spacing_pct`, `allow_hedge`, valamint a risk-paraméterek
  (REQ-007) referenciája.

## Elfogadási kritérium

- [x] Coinokat automatikusan választ paraméterezhető szűrőkkel.
- [x] Lépcsőzetes pozíciónyitás több lépésben, azonos és ellentétes irányban is.
- [x] A risk-méretezőn keresztül méretez, és tiszteletben tartja a tőke-limitet.
- [x] Determinisztikus (REQ-003), tesztelhető backtestben.

## Kapcsolódó

- REQ-005, REQ-007, REQ-003.
