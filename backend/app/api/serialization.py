"""Helpers to serialize ORM rows to JSON-friendly dicts."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any

from app.db.base import Base


def _convert(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def row_to_dict(obj: Base, *, exclude: set[str] | None = None) -> dict[str, Any]:
    """Serialize an ORM instance using its table columns."""
    exclude = exclude or set()
    return {
        col.name: _convert(getattr(obj, col.name))
        for col in obj.__table__.columns
        if col.name not in exclude
    }


def rows_to_list(
    objs: Sequence[Base], *, exclude: set[str] | None = None
) -> list[dict[str, Any]]:
    return [row_to_dict(o, exclude=exclude) for o in objs]
