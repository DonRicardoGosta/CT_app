# REQ-007 — Risk és tőke/leverage kezelés

- **id**: REQ-007
- **status**: done
- **priority**: must
- **components**: backend/risk

## Igény

A live tradehez állítható legyen stratégiánként:

1. **Max. felhasználható / elveszíthető tőke** — mennyi tőkét kockáztathat a stratégia.
2. **Minimum befektetendő összeg + szorzó** — ha 1 USD-t állítok be, akkor 1 USD legyen
   befektetve, akár 5x, akár 10x a szorzó. (A "szorzó" = tőkeáttét/leverage.)
3. Ha egy coinhoz nem tudunk pozit nyitni, mert "túl kevés a tőke ehhez a szorzóhoz"
   (a notional a tőzsdei minimum order alatt van), akkor **növeljük a szorzót**.

## Modell

- `min_investment_usd` = a ténylegesen lekötött tőke (margin) USD-ben.
- `leverage` (szorzó) = tőkeáttét.
- `notional = min_investment_usd * leverage` = a pozíció névértéke.
- A pozíció mérete (base coin) = `notional / price`, a tőzsdei precízióra kerekítve.
- Ha `notional < exchange_min_notional` (vagy a méret < `minTradeVolume`), akkor a
  leverage-et lépésenként emeljük `max_leverage`-ig, amíg el nem érjük a minimumot.
  Ha `max_leverage`-nél is kevés, a pozíció nem nyitható (jelezzük, nem hibázunk).
- **Tőke-limit kapu**: a stratégia aktuális felhasznált tőkéje + új margin nem lépheti
  túl a `max_capital_usd`-t. A potenciális veszteség (stop távolság alapján) nem lépheti
  túl a `max_loss_usd`-t.

## Elfogadási kritérium

- [x] `min_investment_usd` betartva: a lekötött margin független a leverage-től.
- [x] Leverage-eszkaláció a tőzsdei minimumig, `max_leverage` korláttal.
- [x] Tőke- és veszteség-limit kapu a pozíciónyitás előtt.
- [x] Tiszta, unit-tesztelt sizing függvény (determinisztikus).

## Kapcsolódó

- REQ-006, REQ-003, REQ-002.
