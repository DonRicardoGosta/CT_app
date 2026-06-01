"""Rolling market state maintained by the engine and read by strategies."""

from __future__ import annotations

from collections import defaultdict, deque
from decimal import Decimal

from app.domain.types import Bar


class MarketState:
    """Keeps a bounded history of bars per symbol and the latest price.

    Strategies read from this; they never fetch data themselves (REQ-001/005).
    """

    def __init__(self, max_history: int = 1000) -> None:
        self._max = max_history
        self._bars: dict[str, deque[Bar]] = defaultdict(lambda: deque(maxlen=self._max))
        self._last: dict[str, Decimal] = {}

    def update_bar(self, bar: Bar) -> None:
        self._bars[bar.symbol].append(bar)
        self._last[bar.symbol] = bar.close

    def update_price(self, symbol: str, price: Decimal) -> None:
        self._last[symbol] = price

    def bars(self, symbol: str, n: int | None = None) -> list[Bar]:
        seq = self._bars.get(symbol)
        if not seq:
            return []
        items = list(seq)
        return items if n is None else items[-n:]

    def last_price(self, symbol: str) -> Decimal | None:
        return self._last.get(symbol)

    def closes(self, symbol: str, n: int | None = None) -> list[Decimal]:
        return [b.close for b in self.bars(symbol, n)]

    def symbols(self) -> list[str]:
        return list(self._bars.keys())

    def marks(self) -> dict[str, Decimal]:
        return dict(self._last)
