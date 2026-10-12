"""Typed, session-owned screenshot and note import contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


ContentUnitKind = Literal["chat_message", "note_passage"]
SuggestedKind = Literal["chat", "note", "uncertain"]
PreviewStatus = Literal["ready", "empty_body", "boundary_uncertain", "ocr_unavailable"]


@dataclass(frozen=True, slots=True)
class ImageUpload:
    filename: str
    payload: bytes = field(repr=False)
    input_order: int


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)


class UnitEdit(_Model):
    unit_id: str = Field(min_length=1, max_length=128)
    kind: ContentUnitKind
    group_id: str = Field(min_length=1, max_length=128)
    text: str = Field(max_length=1_048_576, repr=False)
    sender_name: str | None = Field(default=None, max_length=256, repr=False)
    sent_at: datetime | None = None
    order: int = Field(ge=0, le=10_000)

    @field_validator("sent_at", mode="before")
    @classmethod
    def parse_visible_timestamp(cls, value: object) -> object:
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value)
            except ValueError:
                raise ValueError("invalid timestamp") from None
        return value


class ImageOrderEdit(_Model):
    image_ref: str = Field(min_length=1, max_length=128)
    input_order: int = Field(ge=0, le=1000)


class PreviewImage(_Model):
    image_ref: str
    filename: str
    input_order: int
    sha256: str


class PreviewUnit(_Model):
    unit_id: str
    kind: ContentUnitKind
    group_id: str
    text: str = Field(repr=False)
    sender_name: str | None = Field(default=None, repr=False)
    sent_at: datetime | None = None
    order: int
    image_ref: str | None = None


class ContentPreviewView(_Model):
    preview_id: str
    preview_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    suggested_kind: SuggestedKind
    status: PreviewStatus
    units: tuple[PreviewUnit, ...]
    images: tuple[PreviewImage, ...]
    warnings: tuple[str, ...]
    expires_at_utc: datetime


class StoredImageView(_Model):
    image_id: str
    input_order: int
    sha256: str


class ContentBatchView(_Model):
    batch_id: str
    unit_count: int
    image_count: int
    images: tuple[StoredImageView, ...]
    created_at_utc: datetime


class ContentBatchPage(_Model):
    items: tuple[ContentBatchView, ...]
    next_cursor: str | None
