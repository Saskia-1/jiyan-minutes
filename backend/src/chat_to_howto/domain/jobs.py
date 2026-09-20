"""Immutable P1 export scopes and the exact durable export-job lifecycle."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from hashlib import sha256
import json
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, field_validator, model_validator


class ExportConversation(BaseModel):
    """A stable selected conversation with optional display metadata."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)

    conversation_id: str
    display_name: str | None = None
    conversation_type: str | None = None

    @field_validator("conversation_id")
    @classmethod
    def _require_conversation_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("conversation ID must be nonempty")
        return value


class ExportScope(BaseModel):
    """The complete multi-conversation P1 authorization target, separate from P0."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)

    schema_version: Literal[1] = 1
    source_id: str
    account_id: str
    source_account_namespace: str | None = None
    conversations: tuple[ExportConversation, ...]
    range_start: datetime
    range_end: datetime
    output_root_id: str
    configuration_nonce: str
    operation: Literal["export"] = "export"
    display_timezone: str = "UTC"
    client_version: str | None = None
    tool_version: str | None = None
    account_version: str | None = None

    @field_validator("source_account_namespace")
    @classmethod
    def _validate_account_namespace(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("source account namespace must be nonempty when present")
        return value

    @model_validator(mode="after")
    def _validate_selection(self) -> "ExportScope":
        if not self.conversations:
            raise ValueError("at least one conversation is required")
        identifiers = [item.conversation_id for item in self.conversations]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("duplicate conversation IDs are not allowed")
        if not all(
            value.strip()
            for value in (self.source_id, self.account_id, self.configuration_nonce)
        ):
            raise ValueError("source, account, and configuration nonce must be nonempty")
        if (
            any(character in self.output_root_id for character in ("/", "\\"))
            or not self.output_root_id.strip()
            or self.output_root_id in {".", ".."}
        ):
            raise ValueError("opaque output root ID is required")
        try:
            ZoneInfo(self.display_timezone)
        except ZoneInfoNotFoundError as error:
            raise ValueError("display timezone is invalid") from error
        for value in (self.range_start, self.range_end):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("range timestamps must be timezone-aware")
        if self.range_start >= self.range_end:
            raise ValueError("range start must be before end")
        return self

    def fingerprint(self) -> str:
        """Bind consent to the schema version and exact batch selection/configuration."""
        payload = json.dumps(
            self.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return sha256(payload).hexdigest()


class ExportJobState(str, Enum):
    """The ten v3/P0 durable export states; aliases are intentionally absent."""

    PENDING_AUTHORIZATION = "pending_authorization"
    CONNECTING = "connecting"
    READING = "reading"
    SAVING = "saving"
    VERIFYING = "verifying"
    INDEXING = "indexing"
    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"
