"""Normalized, local-only message records and safe identity derivation for P1."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator


AttachmentKind = Literal[
    "image",
    "video",
    "audio",
    "file",
    "sticker",
    "link",
    "location",
    "contact",
    "other",
    "unknown",
]
AttachmentAvailability = Literal["not_read", "metadata_only"]


class AttachmentPlaceholder(BaseModel):
    """Observed attachment metadata only; no media path, URL, or content exists here."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)

    kind: AttachmentKind | None = None
    availability: AttachmentAvailability | None = None
    original_name: str | None = Field(default=None, repr=False)

    @field_validator("original_name")
    @classmethod
    def _require_plain_basename(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if (
            not value
            or value in {".", ".."}
            or "/" in value
            or "\\" in value
            or urlsplit(value).scheme
            or any(ord(character) < 32 for character in value)
        ):
            raise ValueError("attachment original name must be a plain basename")
        return value


class MessageInput(BaseModel):
    """A normalized source record whose unknown source values stay nullable.

    ``source_message_id`` is preferred but deliberately nullable. The fallback
    contains provenance only; message text never influences deduplication.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)

    source_id: str
    account_id: str
    conversation_id: str
    source_message_id: str | None
    source_file_id: str | None = Field(repr=False)
    page_slice_id: str | None = Field(repr=False)
    source_order: int | None
    workspace_id: str | None = None
    platform: str | None = None
    source_account_namespace: str | None = None
    conversation_name: str | None = None
    conversation_type: str | None = None
    sent_at_utc: datetime | None = None
    original_timestamp: str | None = None
    source_timezone: str | None = None
    sender_id: str | None = None
    sender_name: str | None = None
    message_type: str | None = None
    text: str | None = Field(default=None, repr=False)
    reply_to_message_id: str | None = None
    export_batch_id: str | None = None
    attachments: tuple[AttachmentPlaceholder, ...] | None = Field(default=None, repr=False)

    @field_validator("sent_at_utc")
    @classmethod
    def _normalize_sent_at_utc(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("sent timestamp must be timezone-aware")
        return value.astimezone(timezone.utc)

    @field_validator("source_file_id", "page_slice_id")
    @classmethod
    def _require_opaque_provenance(cls, value: str | None) -> str | None:
        if value is None:
            return None
        scheme = urlsplit(value).scheme.lower()
        if (
            not value.strip()
            or value != value.strip()
            or len(value) > 512
            or value in {".", ".."}
            or "/" in value
            or "\\" in value
            or scheme in {"data", "file", "ftp", "http", "https"}
            or any(ord(character) < 32 or ord(character) == 127 for character in value)
        ):
            raise ValueError("source provenance must be a bounded opaque identifier")
        return value

    @model_validator(mode="after")
    def _validate_identity_fields(self) -> "MessageInput":
        for value in (self.source_id, self.account_id, self.conversation_id):
            if not value.strip():
                raise ValueError("source, account, and conversation IDs must be nonempty")
        if self.source_message_id is not None and not self.source_message_id.strip():
            raise ValueError("source message ID must be nonempty when present")
        has_source_file = self.source_file_id is not None and bool(self.source_file_id.strip())
        has_page_slice = self.page_slice_id is not None and bool(self.page_slice_id.strip())
        if self.source_message_id is None and (
            self.source_order is None
            or not (has_source_file or has_page_slice)
        ):
            raise ValueError("source provenance is required without a source message ID")
        return self

    def derive_message_id(self) -> str:
        """Return a versioned deterministic local ID without using message text."""
        if self.source_message_id is not None:
            identity = {
                "version": 1,
                "kind": "source_message_id",
                "source_id": self.source_id,
                "account_id": self.account_id,
                "conversation_id": self.conversation_id,
                "source_message_id": self.source_message_id,
            }
        else:
            identity = {
                "version": 1,
                "kind": "source_position",
                "source_id": self.source_id,
                "account_id": self.account_id,
                "conversation_id": self.conversation_id,
                "source_file_id": self.source_file_id,
                "page_slice_id": self.page_slice_id,
                "source_order": self.source_order,
                "original_timestamp": self.original_timestamp,
            }
        payload = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return f"message:v1:{sha256(payload).hexdigest()}"


class StoredMessage(MessageInput):
    """A normalized message after assigning its required internal identity."""

    message_id: str

    @classmethod
    def from_input(cls, value: MessageInput) -> "StoredMessage":
        payload = value.model_dump()
        payload.pop("message_id", None)
        payload["export_batch_id"] = None
        return cls(**payload, message_id=value.derive_message_id())


def safe_validation_errors(error: ValidationError) -> tuple[dict[str, object], ...]:
    """Return JSON-safe validation details without source input or documentation URLs."""
    safe: list[dict[str, object]] = []
    for item in error.errors(include_input=False, include_url=False):
        safe.append(
            {
                key: value
                for key, value in item.items()
                if key in {"type", "loc", "msg"}
            }
        )
    return tuple(safe)
