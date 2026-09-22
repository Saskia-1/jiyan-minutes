"""Schema-versioned, content-safe metadata for a committed P1 export batch."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


CompletenessStatus = Literal["source_range_verified", "partial", "unknown", "empty"]


class _FrozenModel(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        strict=True,
        hide_input_in_errors=True,
    )


class ArtifactDigest(_FrozenModel):
    relative_path: Literal["messages.jsonl", "messages.txt"]
    byte_size: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class SliceManifest(_FrozenModel):
    page: int | None = None
    cursor: str | None = None
    requested: int | None = Field(default=None, ge=0)
    returned: int = Field(ge=0)
    accepted: int = Field(ge=0)
    has_next: bool | None = None

    @model_validator(mode="after")
    def _accepted_cannot_exceed_returned(self) -> "SliceManifest":
        if self.accepted > self.returned:
            raise ValueError("accepted count cannot exceed returned count")
        return self


class ConversationEvidenceManifest(_FrozenModel):
    conversation_id: str = Field(min_length=1)
    status: CompletenessStatus
    reason: str = Field(min_length=1)
    requested_start_ms: int | None = None
    requested_end_ms: int | None = None
    observed_start_ms: int | None = None
    observed_end_ms: int | None = None
    slices: tuple[SliceManifest, ...] = ()
    fetched_count: int | None = Field(default=None, ge=0)
    accepted_count: int | None = Field(default=None, ge=0)
    duplicate_count: int | None = Field(default=None, ge=0)
    unknown_time_count: int | None = Field(default=None, ge=0)
    missing_id_count: int | None = Field(default=None, ge=0)
    parse_error_count: int | None = Field(default=None, ge=0)
    warnings: tuple[str, ...] = ()
    termination: str | None = None


class ConversationManifest(_FrozenModel):
    conversation_id: str = Field(min_length=1)
    display_name: str | None = None
    conversation_type: str | None = None


class BatchManifest(_FrozenModel):
    """The auditable manifest beside one normalized JSONL/TXT artifact pair."""

    schema_version: Literal[1] = 1
    export_batch_id: str = Field(min_length=1)
    snapshot_id: str = Field(min_length=1)
    artifact_set_id: str = Field(min_length=1)
    scope_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    output_root_id: str = Field(min_length=1)
    created_at_utc: datetime
    source_type: Literal["qq", "wechat", "synthetic", "manual"]
    source_id: str = Field(min_length=1)
    source_tool_version: str | None = None
    client_version: str | None = None
    account_id: str = Field(min_length=1)
    source_account_namespace: str | None = None
    account_version: str | None = None
    conversations: tuple[ConversationManifest, ...]
    requested_start_utc: datetime
    requested_end_utc: datetime
    observed_start_utc: datetime | None = None
    observed_end_utc: datetime | None = None
    display_timezone: str = Field(min_length=1)
    fetched_count: int | None = Field(default=None, ge=0)
    saved_count: int = Field(ge=0)
    duplicate_count: int | None = Field(default=None, ge=0)
    unknown_time_count: int | None = Field(default=None, ge=0)
    parse_error_count: int | None = Field(default=None, ge=0)
    earliest_saved_at_utc: datetime | None = None
    latest_saved_at_utc: datetime | None = None
    completeness: CompletenessStatus
    completeness_reason: str = Field(min_length=1)
    limitations: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    conversation_evidence: tuple[ConversationEvidenceManifest, ...]
    artifacts: tuple[ArtifactDigest, ...]

    @field_validator(
        "created_at_utc",
        "requested_start_utc",
        "requested_end_utc",
        "observed_start_utc",
        "observed_end_utc",
        "earliest_saved_at_utc",
        "latest_saved_at_utc",
    )
    @classmethod
    def _normalize_utc(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("manifest timestamps must be timezone-aware")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def _validate_manifest(self) -> "BatchManifest":
        if not self.conversations:
            raise ValueError("manifest must retain selected conversations")
        if self.requested_start_utc >= self.requested_end_utc:
            raise ValueError("requested range must be nonempty")
        if (self.earliest_saved_at_utc is None) != (self.latest_saved_at_utc is None):
            raise ValueError("saved timestamp bounds must both be present or absent")
        if self.saved_count == 0 and self.earliest_saved_at_utc is not None:
            raise ValueError("empty artifacts cannot have saved timestamp bounds")
        if self.saved_count > 0 and self.earliest_saved_at_utc is None and not (
            self.source_type == "manual" and self.unknown_time_count == self.saved_count
        ):
            raise ValueError("nonempty artifacts require saved timestamp bounds")
        if self.earliest_saved_at_utc and self.latest_saved_at_utc:
            if self.earliest_saved_at_utc > self.latest_saved_at_utc:
                raise ValueError("saved timestamp bounds are reversed")
        expected_ids = tuple(item.conversation_id for item in self.conversations)
        evidence_ids = tuple(item.conversation_id for item in self.conversation_evidence)
        if evidence_ids != expected_ids:
            raise ValueError("conversation evidence must match selected order")
        if {item.relative_path for item in self.artifacts} != {
            "messages.jsonl",
            "messages.txt",
        }:
            raise ValueError("manifest must describe exactly the JSONL and TXT artifacts")
        return self
