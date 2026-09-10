"""Consent records tied to one precise local operation."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from hashlib import sha256
import json
from typing import Protocol

from pydantic import BaseModel, ConfigDict, model_validator


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
    configuration_nonce: str

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
    configuration_nonce: str

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

    @model_validator(mode="after")
    def _validate_aware_timestamps(self) -> "ConsentRecord":
        if not _is_timezone_aware(self.recorded_at) or not _is_timezone_aware(self.expires_at):
            raise ValueError("consent timestamps must be timezone-aware")
        if self.expires_at <= self.recorded_at:
            raise ValueError("consent expiry must be after its recorded time")
        return self

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
        try:
            if not (
                _is_timezone_aware(self.recorded_at)
                and _is_timezone_aware(self.expires_at)
                and _is_timezone_aware(current_time)
            ):
                return False
            return (
                self.granted
                and current_time < self.expires_at
                and self.scope_fingerprint == scope.fingerprint()
            )
        except (AttributeError, TypeError, ValueError):
            return False


def _is_timezone_aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None
