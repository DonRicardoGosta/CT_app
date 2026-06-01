"""Mode-agnostic trading core.

The same ``Engine`` and ``Strategy`` code runs in every mode. Only three injected
collaborators differ between live, dry-run and backtest:

* :class:`app.domain.clock.Clock`
* :class:`app.domain.interfaces.MarketDataFeed`
* :class:`app.domain.interfaces.Broker`

See ``requirements/REQ-001`` and ``REQ-003``.
"""
