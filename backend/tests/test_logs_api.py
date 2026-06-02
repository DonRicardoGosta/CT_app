"""Unified log feed endpoint."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.history import router as history_router
from app.db.session import get_session


@pytest.mark.asyncio
async def test_history_logs_endpoint_forwards_filters(monkeypatch):
    captured = {}

    async def fake_list_log_entries(session, **kwargs):  # noqa: ANN001
        captured.update(kwargs)
        return [
            {
                "ts": datetime(2024, 1, 1, tzinfo=UTC),
                "severity": "warn",
                "source": "engine",
                "message": "hello foo",
                "run_id": "run-1",
                "mode": "dry_run",
                "symbol": "BTCUSDT",
                "kind": "error",
                "context": {"x": "y"},
            }
        ]

    monkeypatch.setattr(history_router.repo, "list_log_entries", fake_list_log_entries)

    app = FastAPI()

    async def fake_session():  # noqa: ANN202
        return object()

    app.dependency_overrides[get_session] = fake_session
    app.include_router(history_router.router, prefix="/api")

    with TestClient(app) as client:
        res = client.get(
            "/api/history/logs",
            params={
                "run_id": "run-1",
                "mode": "dry_run",
                "severity": "warn",
                "source": "engine",
                "q": "foo",
                "limit": "123",
            },
        )

    assert res.status_code == 200
    assert res.json()[0]["message"] == "hello foo"
    assert captured == {
        "run_id": "run-1",
        "mode": "dry_run",
        "severity": "warn",
        "source": "engine",
        "q": "foo",
        "limit": 123,
    }
