"""Consent records tied to one precise local operation."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from hashlib import sha256
import json
from typing import Protocol

from pydantic import BaseModel, ConfigDict


class ConsentScope(BaseModel):
    """The source, account, range, and output selected for an operation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str
    account_id: str
    conversation_id: str
    range_start: str
    range_end: str
    output_path: str
    operation: str
    client_version: str | None = None
    tool_version: str | None = None
    account_version: str | None = None

    def fingerprint(self) -> str:
        """Return a stable digest without reusing consent across changed inputs."""
        payload = json.dumps(
            self.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return sha256(payload).hexdigest()


class SecretOperation(str, Enum):
    """The only authorized operations over a secret reference."""

    GET = "secret_get"
    SET = "secret_set"
    DELETE = "secret_delete"


class SecretAccessScope(BaseModel):
    """Exact, versioned authorization target for one secret operation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    operation: SecretOperation
    secret_reference: str
    secret_backend: str
    source_id: str
    account_id: str
    client_version: str
    tool_version: str
    account_version: str

    def fingerprint(self) -> str:
        payload = json.dumps(
            self.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return sha256(payload).hexdigest()


class FingerprintedScope(Protocol):
    def fingerprint(self) -> str: ...


class ConsentRecord(BaseModel):
    """Immutable grant or decline associated with a scope fingerprint."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    scope_fingerprint: str
    granted: bool
    recorded_at: datetime
    expires_at: datetime

    @classmethod
    def grant(
        cls,
        scope: FingerprintedScope,
        *,
        expires_at: datetime,
        now: datetime | None = None,
    ) -> "ConsentRecord":
        recorded_at = now or datetime.now(timezone.utc)
        return cls(
            scope_fingerprint=scope.fingerprint(),
            granted=True,
            recorded_at=recorded_at,
            expires_at=expires_at,
        )

    @classmethod
    def decline(
        cls,
        scope: FingerprintedScope,
        *,
        expires_at: datetime,
        now: datetime | None = None,
    ) -> "ConsentRecord":
        recorded_at = now or datetime.now(timezone.utc)
        return cls(
            scope_fingerprint=scope.fingerprint(),
            granted=False,
            recorded_at=recorded_at,
            expires_at=expires_at,
        )

    def matches(self, scope: FingerprintedScope, *, now: datetime | None = None) -> bool:
        current_time = now or datetime.now(timezone.utc)
        return (
            self.granted
            and current_time < self.expires_at
            and self.scope_fingerprint == scope.fingerprint()
        )
