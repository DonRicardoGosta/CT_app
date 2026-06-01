"""Bitunix double-SHA256 request signing (REQ-002).

Reference (official docs):

REST::

    queryParams = "".join(f"{k}{v}" for k, v in sorted(params))   # ASCII order
    body        = compact-json (no spaces) or ""
    digest      = SHA256(nonce + timestamp + api_key + queryParams + body)
    sign        = SHA256(digest + secret_key)

WebSocket login::

    digest = SHA256(nonce + timestamp + api_key)   # no extra params for login
    sign   = SHA256(digest + secret_key)

All hashes are hex digests. These functions are pure and unit-tested so the live
path can be trusted without hitting the network.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from collections.abc import Mapping
from typing import Any


def _sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def generate_nonce() -> str:
    """Return a 32-char random nonce."""
    return secrets.token_hex(16)


def now_ms() -> str:
    """Current timestamp in milliseconds as a string."""
    return str(int(time.time() * 1000))


def compact_json(body: Mapping[str, Any] | None) -> str:
    """Serialize a body to spaces-removed JSON (must match the request body exactly)."""
    if not body:
        return ""
    return json.dumps(body, separators=(",", ":"), ensure_ascii=False)


def query_string(params: Mapping[str, Any] | None) -> str:
    """Concatenate params as ``key+value`` pairs sorted by ASCII key order."""
    if not params:
        return ""
    return "".join(f"{k}{params[k]}" for k in sorted(params))


def sign_rest(
    api_key: str,
    secret_key: str,
    nonce: str,
    timestamp: str,
    params: Mapping[str, Any] | None = None,
    body: Mapping[str, Any] | None = None,
) -> str:
    """Return the REST ``sign`` header value."""
    digest = _sha256_hex(
        nonce + timestamp + api_key + query_string(params) + compact_json(body)
    )
    return _sha256_hex(digest + secret_key)


def sign_ws_login(api_key: str, secret_key: str, nonce: str, timestamp: str) -> str:
    """Return the WebSocket login ``sign`` value."""
    digest = _sha256_hex(nonce + timestamp + api_key)
    return _sha256_hex(digest + secret_key)


def rest_headers(
    api_key: str,
    secret_key: str,
    params: Mapping[str, Any] | None = None,
    body: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    """Build a full set of signed REST headers for a request."""
    nonce = generate_nonce()
    timestamp = now_ms()
    sign = sign_rest(api_key, secret_key, nonce, timestamp, params, body)
    return {
        "api-key": api_key,
        "nonce": nonce,
        "timestamp": timestamp,
        "sign": sign,
        "language": "en-US",
        "Content-Type": "application/json",
    }
