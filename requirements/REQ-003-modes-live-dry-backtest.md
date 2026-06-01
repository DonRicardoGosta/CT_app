# REQ-003 — Live / Dry-run / Backtest módok

- **id**: REQ-003
- **status**: done
- **priority**: must
- **components**: backend/domain

## Igény

Legyen élő kereskedés, valós idejű dry-run, és backtest. Mindhárom **ugyanazt a
stratégia- és engine-kódot** futtassa, hogy a tesztelés és az éles ne térjen el.
Ha élesben futtatok valamit és visszatesztelem, ugyanaz az eredmény (a nyitási
ár-csúszást leszámítva).

## Módok

| Mód | Clock | Feed | Broker |
| --- | --- | --- | --- |
| `live` | valós idő | élő WS | éles Bitunix REST |
| `dry-run` | valós idő | élő WS | szimulált fill az élő áron |
| `backtest` | szimulált idő | historikus kline | szimulált fill historikus áron |

## Determinizmus

- A `Strategy` nem használ `datetime.now()`-t vagy közvetlen random-ot; mindent a
  `Clock`-ból és a kapott adatokból kap.
- Ahol szükséges véletlen, ott seedelt RNG-t injektálunk.
- A dry-run és backtest broker **azonos fill-modellt** használ (slippage/fee modell),
  csak az ár forrása más (élő tick vs historikus bar). Azonos adatfolyamra azonos döntés.

## Elfogadási kritérium

- [x] Egy `RunConfig.mode` választja ki a komponenseket; a stratégia kód nem változik.
- [x] Teszt: ugyanarra a bar-sorozatra a backtest és a (szimulált tick-ből táplált)
      dry-run azonos jeleket és azonos záró equity-t ad.
- [x] A live broker ugyanazt a `Broker` interfészt valósítja meg.

## Kapcsolódó

- REQ-001, REQ-005, REQ-007.
