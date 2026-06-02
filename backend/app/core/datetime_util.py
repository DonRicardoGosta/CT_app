"""Timezone helpers — Bitunix timestamps are UTC milliseconds."""

from __future__ import annotations

from datetime import UTC, datetime


def ensure_utc(dt: datetime | None) -> datetime | None:
    """Return an aware UTC datetime (naive values are treated as UTC)."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)
