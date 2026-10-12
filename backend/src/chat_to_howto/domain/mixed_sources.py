"""Explicit typed source selection for local mixed retrieval."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)


class SourceRef(_Model):
    kind: Literal["chat_snapshot", "content_batch"]
    id: str = Field(min_length=1, max_length=128)


class MixedSearchRequest(_Model):
    source_refs: tuple[SourceRef, ...] = Field(min_length=1, max_length=50)
    include_undated: bool = False
    start_time: datetime | None = None
    end_time: datetime | None = None
    terms: tuple[str, ...] = Field(min_length=1, max_length=10)

    @field_validator("source_refs", "terms", mode="before")
    @classmethod
    def accept_json_arrays(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @field_validator("start_time", "end_time", mode="before")
    @classmethod
    def parse_time(cls, value: object) -> object:
        if isinstance(value, str) and len(value) <= 64:
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return value
        return value

    @field_validator("start_time", "end_time")
    @classmethod
    def aware_time(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("mixed search time must have an explicit zone")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def bounded(self) -> "MixedSearchRequest":
        if len(set((ref.kind, ref.id) for ref in self.source_refs)) != len(self.source_refs):
            raise ValueError("mixed search source selection must be unique")
        if (self.start_time is None) != (self.end_time is None) or (self.start_time is not None and self.start_time >= self.end_time):
            raise ValueError("mixed search range is invalid")
        if any(len(term) > 512 or term != term.strip() for term in self.terms):
            raise ValueError("mixed search term is invalid")
        return self


class MixedSearchHit(_Model):
    unit_id: str
    source_kind: Literal["chat_message", "note_passage"]
    source_ref: SourceRef
    text: str = Field(repr=False)
    sender_name: str | None = None
    timestamp: datetime | None = None
    source_order: int | None = None
    image_id: str | None = None


class MixedSearchPage(_Model):
    hits: tuple[MixedSearchHit, ...]
