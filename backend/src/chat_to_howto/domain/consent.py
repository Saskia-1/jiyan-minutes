"""Consent records tied to one precise local operation."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json

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

    def fingerprint(self) -> str:
        """Return a stable digest without reusing consent across changed inputs."""
        payload = json.dumps(
            self.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return sha256(payload).hexdigest()


class ConsentRecord(BaseModel):
    """Immutable grant or decline associated with a scope fingerprint."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    scope_fingerprint: str
    granted: bool
    recorded_at: datetime

    @classmethod
    def grant(cls, scope: ConsentScope) -> "ConsentRecord":
        return cls(
            scope_fingerprint=scope.fingerprint(),
            granted=True,
            recorded_at=datetime.now(timezone.utc),
        )

    @classmethod
    def decline(cls, scope: ConsentScope) -> "ConsentRecord":
        return cls(
            scope_fingerprint=scope.fingerprint(),
            granted=False,
            recorded_at=datetime.now(timezone.utc),
        )

    def matches(self, scope: ConsentScope) -> bool:
        return self.granted and self.scope_fingerprint == scope.fingerprint()
