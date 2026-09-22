"""Bounded local search request and response contracts."""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class SearchRequest(BaseModel):
    """One selected-conversation, half-open time-range search."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        strict=True,
        hide_input_in_errors=True,
    )

    query: str = Field(default="", max_length=512)
    conversation_ids: tuple[str, ...] = Field(min_length=1, max_length=50)
    snapshot_id: str | None = Field(default=None, min_length=1, max_length=128)
    start_time: datetime
    end_time: datetime
    limit: int = Field(default=25, ge=1, le=100)
    cursor: str | None = Field(default=None, min_length=1, max_length=2048)
    include_undated: bool = False

    @field_validator("conversation_ids", mode="before")
    @classmethod
    def _parse_conversations(cls, value: object) -> object:
        if isinstance(value, list) and len(value) <= 50:
            return tuple(value)
        return value

    @field_validator("conversation_ids")
    @classmethod
    def _validate_conversations(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("conversation IDs must be unique")
        if any(
            not item
            or len(item) > 256
            or item != item.strip()
            or any(ord(character) < 32 or ord(character) == 127 for character in item)
            for item in value
        ):
            raise ValueError("conversation IDs must be bounded opaque values")
        return value

    @field_validator("snapshot_id")
    @classmethod
    def _validate_snapshot_id(cls, value: str | None) -> str | None:
        if value is not None and (
            value != value.strip()
            or any(ord(character) < 32 or ord(character) == 127 for character in value)
        ):
            raise ValueError("snapshot ID must be a bounded opaque value")
        return value

    @field_validator("start_time", "end_time", mode="before")
    @classmethod
    def _parse_time(cls, value: object) -> object:
        if isinstance(value, str) and 1 <= len(value) <= 64:
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return value
        return value

    @field_validator("start_time", "end_time")
    @classmethod
    def _normalize_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("search times must be timezone-aware")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def _validate_range(self) -> "SearchRequest":
        if self.start_time >= self.end_time:
            raise ValueError("search start must precede end")
        return self


class SearchResult(BaseModel):
    """One deduplicated stable message with retained snapshot provenance."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    message_id: str
    conversation_id: str
    timestamp: datetime | None
    source_order: int | None = Field(default=None, exclude_if=lambda value: value is None)
    sender_id: str | None
    sender_name: str | None
    text: str | None
    batch_id: str
    snapshot_id: str


class SearchPage(BaseModel):
    """A deterministic keyset page."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    results: tuple[SearchResult, ...]
    next_cursor: str | None
