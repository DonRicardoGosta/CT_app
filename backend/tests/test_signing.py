"""Bitunix signing is deterministic and matches the documented canonical forms."""

from __future__ import annotations

from app.exchange.bitunix import signing


def test_query_string_sorted_ascii():
    # Docs example: id=1, uid=200 -> "id1uid200" (sorted by key, key+value concat).
    assert signing.query_string({"uid": 200, "id": 1}) == "id1uid200"


def test_compact_json_has_no_spaces():
    assert signing.compact_json({"uid": "2899"}) == '{"uid":"2899"}'
    assert signing.compact_json(None) == ""


def test_sign_rest_is_deterministic():
    a = signing.sign_rest("key", "secret", "nonce", "1700000000000", {"id": 1}, {"a": "b"})
    b = signing.sign_rest("key", "secret", "nonce", "1700000000000", {"id": 1}, {"a": "b"})
    assert a == b
    assert len(a) == 64  # sha256 hex


def test_sign_ws_login_is_deterministic():
    a = signing.sign_ws_login("key", "secret", "nonce", "1700000000000")
    b = signing.sign_ws_login("key", "secret", "nonce", "1700000000000")
    assert a == b
    assert len(a) == 64


def test_rest_headers_contains_required_fields():
    headers = signing.rest_headers("key", "secret", {"symbol": "BTCUSDT"})
    for field in ("api-key", "nonce", "timestamp", "sign", "Content-Type"):
        assert field in headers
