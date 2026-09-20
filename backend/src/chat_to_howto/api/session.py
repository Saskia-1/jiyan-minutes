"""Opaque local session helpers; only hashes are persisted."""

from __future__ import annotations

from hashlib import sha256
import secrets


def opaque() -> str:
    return secrets.token_urlsafe(32)


def digest(value: str) -> str:
    return sha256(value.encode()).hexdigest()
