"""Realtime hub client registry must not require hashable clients."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.realtime.hub import Client, RealtimeHub


@pytest.mark.asyncio
async def test_connect_and_disconnect_uses_list_not_set():
    hub = RealtimeHub()
    ws = MagicMock()
    ws.accept = AsyncMock()
    client = await hub.connect(ws)  # type: ignore[arg-type]
    assert isinstance(client, Client)
    assert client in hub._clients
    await hub.disconnect(client)
    assert client not in hub._clients
