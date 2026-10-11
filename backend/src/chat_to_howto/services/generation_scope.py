"""Immutable P2 outbound scope, preview, and expiring single-use consent."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from math import ceil
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from chat_to_howto.domain.generation import GenerationBudget, OutboundMessage, SummaryType


class GenerationScope(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        strict=True,
        hide_input_in_errors=True,
    )

    schema_version: Literal[1] = 1
    batch_id: str
    snapshot_id: str
    search_run_id: str | None
    mode: Literal["fast", "complete"]
    generation_kind: Literal["guide", "summary"] = Field(default="guide", exclude_if=lambda value: value == "guide")
    summary_type: SummaryType | None = Field(default=None, exclude_if=lambda value: value is None)
    allow_undated: bool = Field(default=False, exclude_if=lambda value: not value)
    topic: str = Field(repr=False)
    provider: str
    model: str
    thinking: Literal["disabled", "enabled"] = Field(default="disabled", exclude_if=lambda value: value == "disabled")
    reasoning_effort: Literal["low", "high", "max"] | None = Field(default=None, exclude_if=lambda value: value is None)
    redaction_policy_revision: str
    conversation_ids: tuple[str, ...]
    range_start: datetime
    range_end: datetime
    messages: tuple[OutboundMessage, ...] = Field(repr=False)
    excluded_message_ids: tuple[str, ...]
    budget: GenerationBudget

    @field_validator(
        "batch_id",
        "snapshot_id",
        "provider",
        "model",
        "redaction_policy_revision",
        "topic",
    )
    @classmethod
    def _require_nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("generation scope identifiers and topic must be nonblank")
        return value

    @model_validator(mode="after")
    def _validate_sets_and_budget(self) -> "GenerationScope":
        if (self.thinking == "enabled") != (self.reasoning_effort is not None):
            raise ValueError("thinking mode and reasoning effort must be explicitly paired")
        if not self.conversation_ids or len(set(self.conversation_ids)) != len(
            self.conversation_ids
        ):
            raise ValueError("generation conversation IDs must be nonempty and unique")
        if any(not value.strip() for value in self.conversation_ids):
            raise ValueError("generation conversation IDs must be nonblank")
        if (
            self.range_start.tzinfo is None
            or self.range_start.utcoffset() is None
            or self.range_end.tzinfo is None
            or self.range_end.utcoffset() is None
            or self.range_start >= self.range_end
        ):
            raise ValueError("generation range must be aware and ordered")
        identifiers = tuple(message.message_id for message in self.messages)
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("duplicate outbound message IDs are not allowed")
        if len(set(self.excluded_message_ids)) != len(self.excluded_message_ids):
            raise ValueError("duplicate excluded message IDs are not allowed")
        if any(not value.strip() for value in self.excluded_message_ids):
            raise ValueError("excluded message IDs must be nonblank")
        if set(identifiers).intersection(self.excluded_message_ids):
            raise ValueError("excluded messages cannot remain in the outbound payload")
        if len(self.messages) > self.budget.max_messages:
            raise ValueError("outbound messages exceed the message budget")
        conservative_estimate = ceil(
            (len(self.topic) + sum(len(message.text) for message in self.messages)) / 1.5
        )
        if conservative_estimate > self.budget.max_input_tokens:
            raise ValueError("outbound content exceeds the configured token budget estimate")
        if self.mode == "fast" and not self.search_run_id:
            raise ValueError("fast generation must bind a search run")
        return self

    def provider_payload(self) -> bytes:
        """Return the exact frozen, anonymous bytes shown before consent."""
        payload = {
            "schema_version": self.schema_version,
            "mode": self.mode,
            "topic": self.topic,
            "messages": [
                item.model_dump(mode="json")
                for item in self.messages
            ],
        }
        if self.generation_kind == "summary":
            payload["generation_kind"] = "summary"
        if self.summary_type is not None:
            payload["summary_type"] = self.summary_type
        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    def payload_sha256(self) -> str:
        return sha256(self.provider_payload()).hexdigest()

    def fingerprint(self) -> str:
        payload = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return sha256(payload).hexdigest()


class OutboundPreview(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)

    scope_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    payload: bytes = Field(repr=False)
    message_count: int = Field(ge=0)
    character_count: int = Field(ge=0)
    token_estimate_low: int = Field(ge=0)
    token_estimate_high: int = Field(ge=0)
    price_status: Literal["unknown"] = "unknown"
    estimated_cost: None = None


def build_outbound_preview(scope: GenerationScope) -> OutboundPreview:
    payload = scope.provider_payload()
    character_count = sum(len(message.text) for message in scope.messages)
    # This range is explicitly an estimate, not a tokenizer or price quote.
    low = ceil(character_count / 4) if character_count else 0
    high = ceil(character_count / 1.5) if character_count else 0
    return OutboundPreview(
        scope_fingerprint=scope.fingerprint(),
        payload_sha256=sha256(payload).hexdigest(),
        payload=payload,
        message_count=len(scope.messages),
        character_count=character_count,
        token_estimate_low=low,
        token_estimate_high=max(low, high),
    )


class OutboundConsent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)

    scope_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    recorded_at: datetime
    expires_at: datetime
    consumed_at: datetime | None = None

    @field_validator("recorded_at", "expires_at", "consumed_at")
    @classmethod
    def _normalize_utc(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("consent timestamps must be timezone-aware")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def _validate_window(self) -> "OutboundConsent":
        if self.expires_at <= self.recorded_at:
            raise ValueError("consent expiry must follow recording")
        if self.consumed_at is not None and not (
            self.recorded_at <= self.consumed_at < self.expires_at
        ):
            raise ValueError("consent consumption must occur inside its validity window")
        return self

    @classmethod
    def grant(
        cls,
        scope: GenerationScope,
        *,
        recorded_at: datetime,
        expires_at: datetime,
    ) -> "OutboundConsent":
        return cls(
            scope_fingerprint=scope.fingerprint(),
            payload_sha256=scope.payload_sha256(),
            recorded_at=recorded_at,
            expires_at=expires_at,
        )

    def matches(self, scope: GenerationScope, *, now: datetime) -> bool:
        instant = now.astimezone(timezone.utc) if now.tzinfo and now.utcoffset() is not None else None
        return bool(
            instant is not None
            and self.consumed_at is None
            and self.recorded_at <= instant < self.expires_at
            and self.scope_fingerprint == scope.fingerprint()
            and self.payload_sha256 == scope.payload_sha256()
        )

    def consume(self, scope: GenerationScope, *, now: datetime) -> "OutboundConsent":
        if not self.matches(scope, now=now):
            raise PermissionError("outbound consent is stale, expired, or already consumed")
        return self.model_copy(update={"consumed_at": now.astimezone(timezone.utc)})
