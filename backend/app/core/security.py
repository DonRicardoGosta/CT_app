"""Secret encryption helpers (REQ-009).

API secrets are encrypted at rest with Fernet. The Fernet key comes from the
``ENCRYPTION_KEY`` environment variable. The plaintext secret never leaves the
backend: the API only ever returns a masked representation.
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings


def _derive_key(raw: str) -> bytes:
    """Return a valid urlsafe-base64 Fernet key.

    If a proper key is provided we use it directly; otherwise we deterministically
    derive one from the given string. The derived path is only for local dev.
    """
    if not raw:
        raw = "insecure-dev-key-change-me"
    try:
        # Accept a ready-made Fernet key as-is.
        if len(base64.urlsafe_b64decode(raw.encode())) == 32:
            return raw.encode()
    except Exception:  # noqa: BLE001 - fall through to derivation
        pass
    digest = hashlib.sha256(raw.encode()).digest()
    return base64.urlsafe_b64encode(digest)


def _fernet() -> Fernet:
    return Fernet(_derive_key(get_settings().encryption_key))


def encrypt_secret(plaintext: str) -> str:
    """Encrypt a secret string, returning a token safe to store in the DB."""
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_secret(token: str) -> str:
    """Decrypt a previously encrypted secret token."""
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:  # pragma: no cover - defensive
        raise ValueError("Could not decrypt secret; wrong ENCRYPTION_KEY?") from exc


def mask_secret(plaintext: str, *, visible: int = 4) -> str:
    """Return a masked form of a secret, e.g. ``abcd...wxyz``."""
    if len(plaintext) <= visible * 2:
        return "*" * len(plaintext)
    return f"{plaintext[:visible]}...{plaintext[-visible:]}"
