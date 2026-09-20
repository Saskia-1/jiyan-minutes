"""Content-safe models for the secondary, manual backup-import preview flow."""

from __future__ import annotations

from datetime import datetime
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


ImportOrigin = Literal["imported_file", "paste"]
ImportPlatformHint = Literal["generic", "qq", "wechat"]


class BackupPreview(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)

    format: Literal["txt", "csv", "json", "jsonl", "xlsx"]
    encoding: Literal["utf-8", "utf-8-sig", "gb18030", "binary"]
    container: Literal["plain", "zip", "xlsx"] = "plain"
    member_name: str | None = None
    row_count: int = Field(ge=0)
    columns: tuple[str, ...]
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    byte_size: int = Field(ge=1)
    warnings: tuple[str, ...] = (
        "manual_backup_is_secondary",
        "completeness_is_not_inferred",
    )


class BackupRecord(BaseModel):
    """One inert mapped row; timestamp interpretation occurs in a later consent scope."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)

    conversation_id: str = Field(min_length=1)
    conversation_name: str | None = Field(default=None, repr=False)
    conversation_type: str | None = Field(default=None, repr=False)
    source_message_id: str | None = Field(default=None, repr=False)
    sent_at: str | None = Field(default=None, repr=False)
    sender_id: str | None = Field(default=None, repr=False)
    sender_name: str | None = Field(default=None, repr=False)
    message_type: str | None = Field(default=None, repr=False)
    text: str | None = Field(default=None, repr=False)
    reply_to_message_id: str | None = Field(default=None, repr=False)
    source_order: str | None = Field(default=None, repr=False)


class ImportOptions(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)

    mapping: dict[str, str] = Field(default_factory=dict)
    timezone: str = Field(default="Asia/Shanghai", min_length=1, max_length=128)
    default_date: str | None = Field(default=None, max_length=10)
    default_year: int | None = Field(default=None, ge=1, le=9999)
    default_conversation_id: str | None = Field(default=None, max_length=256)
    default_conversation_name: str | None = Field(default=None, max_length=512)

    @field_validator("mapping")
    @classmethod
    def _bounded_mapping(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > len(BackupRecord.model_fields) or any(
            not isinstance(key, str)
            or not isinstance(column, str)
            or not key
            or not column
            or len(key) > 64
            or len(column) > 256
            for key, column in value.items()
        ):
            raise ValueError("import mapping is invalid")
        return value


class ImportIssue(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    row_number: int = Field(ge=1)
    code: Literal[
        "conversation_unresolved",
        "timestamp_unresolved",
        "message_identity_conflict",
        "message_invalid",
    ]


class ImportMessageTypeCounts(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    text: int = Field(default=0, ge=0)
    image: int = Field(default=0, ge=0)
    file: int = Field(default=0, ge=0)
    other: int = Field(default=0, ge=0)
    unknown: int = Field(default=0, ge=0)


class ImportConversationSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    conversation_id: str
    conversation_name: str | None
    message_count: int = Field(ge=1)


class ImportSample(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    row_number: int = Field(ge=1)
    conversation_name: str | None
    sender_name: str | None
    timestamp: datetime
    message_type: str
    text: str | None = Field(default=None, max_length=500, repr=False)


class ImportAnalysis(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    total_count: int = Field(ge=0)
    parsed_count: int = Field(ge=0)
    unresolved_count: int = Field(ge=0)
    duplicate_count: int = Field(ge=0)
    sender_count: int = Field(ge=0)
    time_start: datetime | None
    time_end: datetime | None
    message_types: ImportMessageTypeCounts
    conversations: tuple[ImportConversationSummary, ...] = Field(max_length=500)
    warnings: tuple[str, ...]
    issues: tuple[ImportIssue, ...]
    samples: tuple[ImportSample, ...]


class ManualImportSelection(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    handle_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    conversation_id: str = Field(min_length=1, max_length=256)


class ManualImportCommit(BaseModel):
    """Server-owned metadata bound atomically to one sealed imported snapshot."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)

    import_id: str = Field(min_length=1, max_length=256)
    session_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_kind: ImportOrigin
    platform_hint: ImportPlatformHint
    source_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    filename: str = Field(min_length=1, max_length=255)
    analysis: ImportAnalysis
    selections: tuple[ManualImportSelection, ...] = Field(min_length=1, max_length=500)
    created_at_utc: datetime
    expires_at_utc: datetime

    @field_validator("filename")
    @classmethod
    def _filename_is_not_a_path(cls, value: str) -> str:
        if value in {".", ".."} or "/" in value or "\\" in value or re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", value):
            raise ValueError("import filename must be a basename")
        return value

    @field_validator("created_at_utc", "expires_at_utc")
    @classmethod
    def _aware_utc_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("manual import time must be timezone-aware")
        return value


class ImportPreviewView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    preview_id: str
    preview_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    expires_at_utc: datetime
    source: BackupPreview
    applied_mapping: dict[str, str]
    analysis: ImportAnalysis
    ready_to_commit: bool


class ImportConversationHandle(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    conversation_selection_id: str
    display_name: str | None
    conversation_type: str | None


class ImportCommitView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    import_id: str
    batch_id: str
    snapshot_id: str
    source_id: Literal["manual"] = "manual"
    account_id: str
    range_start: datetime
    range_end: datetime
    display_timezone: str = Field(min_length=1, max_length=128)
    conversations: tuple[ImportConversationHandle, ...]
    warnings: tuple[str, ...]


class ImportRecoveryPage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    imports: tuple[ImportCommitView, ...]
    next_cursor: str | None = None
