"""Bitunix futures trading platform.

The package is organized so that trading *decisions* (strategies) and the engine
loop are fully decoupled from *execution* and *I/O*. The same strategy and engine
code runs in live, dry-run and backtest mode; only the injected ``Clock``,
``MarketDataFeed`` and ``Broker`` differ. See ``requirements/REQ-001`` and
``REQ-003`` for the rationale.
"""

__version__ = "0.1.0"
