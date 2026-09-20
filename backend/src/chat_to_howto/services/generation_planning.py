"""Local-only P2 selection, deterministic chunking, and hard-budget planning."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from math import ceil
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from chat_to_howto.api.session import digest
from chat_to_howto.domain.generation import GenerationBudget, OutboundMessage
from chat_to_howto.domain.messages import StoredMessage
from chat_to_howto.services.generation_scope import GenerationScope
from chat_to_howto.services.generation_redaction import redact_outbound_text


class GenerationBudgetExceeded(ValueError):
    """A plan cannot satisfy the configured hard limits without truncation."""


class GenerationSelection(BaseModel):
    """Internal selection resolved from local UI handles before any outbound call."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)

    snapshot_id: str = Field(min_length=1, max_length=256)
    conversation_ids: tuple[str, ...] = Field(min_length=1, max_length=50)
    start_time: datetime
    end_time: datetime
    topic: str = Field(min_length=1, max_length=512, repr=False)
    mode: Literal["fast", "complete"]
    provider: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=128)
    redaction_policy_revision: str = Field(min_length=1, max_length=128)
    excluded_message_ids: tuple[str, ...] = Field(default=(), max_length=2_000)
    budget: GenerationBudget

    @field_validator("conversation_ids", "excluded_message_ids", mode="before")
    @classmethod
    def _parse_sequences(cls, value: object) -> object:
        if isinstance(value, list):
            return tuple(value)
        return value

    @field_validator("conversation_ids", "excluded_message_ids")
    @classmethod
    def _validate_opaque_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("selection IDs must be unique")
        if any(
            not item
            or len(item) > 256
            or item != item.strip()
            or any(ord(character) < 32 or ord(character) == 127 for character in item)
            for item in value
        ):
            raise ValueError("selection IDs must be bounded opaque values")
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
            raise ValueError("generation times must be timezone-aware")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def _validate_range(self) -> "GenerationSelection":
        if self.start_time >= self.end_time:
            raise ValueError("generation start must precede end")
        if not self.topic.strip():
            raise ValueError("generation topic must be nonblank")
        return self


class _SnapshotRepository(Protocol):
    def generation_snapshot_messages(
        self, *, session_digest: str, snapshot_id: str
    ) -> tuple[str, tuple[StoredMessage, ...]]:
        ...


class GenerationScopeBuilder:
    """Apply ownership, conversation, time, topic, and exclusion policy locally."""

    def __init__(self, repository: _SnapshotRepository) -> None:
        self._repository = repository

    def build(self, selection: GenerationSelection, *, session_id: str) -> GenerationScope:
        if not isinstance(session_id, str) or not session_id:
            raise PermissionError("local session is unavailable")
        batch_id, snapshot_messages = self._repository.generation_snapshot_messages(
            session_digest=digest(session_id),
            snapshot_id=selection.snapshot_id,
        )
        conversations = set(selection.conversation_ids)
        scoped = tuple(
            message
            for message in snapshot_messages
            if message.conversation_id in conversations
            and message.sent_at_utc is not None
            and selection.start_time <= message.sent_at_utc < selection.end_time
        )
        scoped_ids = {message.message_id for message in scoped}
        excluded = set(selection.excluded_message_ids)
        if not excluded <= scoped_ids:
            raise ValueError("excluded message is outside the selected local scope")

        eligible = tuple(
            message
            for message in scoped
            if message.message_id not in excluded
            and isinstance(message.text, str)
            and bool(message.text.strip())
            and (selection.mode != "fast" or selection.topic in message.text)
        )
        ordered = sorted(
            eligible,
            key=lambda message: (
                message.sent_at_utc,
                message.source_order is None,
                message.source_order if message.source_order is not None else 0,
                message.message_id,
            ),
        )
        search_run_id = (
            _search_run_id(selection) if selection.mode == "fast" else None
        )
        return GenerationScope(
            batch_id=batch_id,
            snapshot_id=selection.snapshot_id,
            search_run_id=search_run_id,
            mode=selection.mode,
            topic=selection.topic,
            provider=selection.provider,
            model=selection.model,
            redaction_policy_revision=selection.redaction_policy_revision,
            conversation_ids=selection.conversation_ids,
            range_start=selection.start_time,
            range_end=selection.end_time,
            messages=tuple(
                OutboundMessage(
                    message_id=message.message_id,
                    text=redact_outbound_text(
                        message.text,
                        policy_revision=selection.redaction_policy_revision,
                    ),
                )
                for message in ordered
            ),
            excluded_message_ids=selection.excluded_message_ids,
            budget=selection.budget,
        )


class GenerationChunk(BaseModel):
    """A stable provider-call unit that never splits or overlaps messages."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)

    chunk_id: str = Field(pattern=r"^chunk:v1:[0-9a-f]{64}$")
    index: int = Field(ge=0)
    messages: tuple[OutboundMessage, ...] = Field(min_length=1, repr=False)
    estimated_input_tokens: int = Field(gt=0)
    payload: bytes = Field(min_length=1, repr=False)


class GenerationPlan(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)

    scope_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    chunks: tuple[GenerationChunk, ...]
    total_estimated_input_tokens: int = Field(ge=0)
    total_max_output_tokens: int = Field(ge=0)


def _chunk_payload(topic: str, messages: tuple[OutboundMessage, ...]) -> bytes:
    return json.dumps(
        {
            "schema_version": 1,
            "topic": topic,
            "messages": [
                {"message_id": message.message_id, "text": message.text}
                for message in messages
            ],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def estimate_chunk_tokens(topic: str, messages: tuple[OutboundMessage, ...]) -> int:
    """Conservative deterministic estimate; it is not presented as exact billing."""
    return ceil(len(_chunk_payload(topic, messages).decode("utf-8")) / 1.5)


def build_generation_plan(
    scope: GenerationScope,
    *,
    max_chunk_input_tokens: int,
) -> GenerationPlan:
    if (
        isinstance(max_chunk_input_tokens, bool)
        or not isinstance(max_chunk_input_tokens, int)
        or max_chunk_input_tokens <= 0
        or max_chunk_input_tokens > scope.budget.max_input_tokens
    ):
        raise GenerationBudgetExceeded("chunk input limit exceeds the hard input budget")
    if not scope.messages:
        return GenerationPlan(
            scope_fingerprint=scope.fingerprint(),
            chunks=(),
            total_estimated_input_tokens=0,
            total_max_output_tokens=0,
        )

    groups: list[tuple[OutboundMessage, ...]] = []
    current: tuple[OutboundMessage, ...] = ()
    for message in scope.messages:
        candidate = (*current, message)
        if estimate_chunk_tokens(scope.topic, candidate) <= max_chunk_input_tokens:
            current = candidate
            continue
        if not current:
            raise GenerationBudgetExceeded("single message exceeds the chunk input budget")
        groups.append(current)
        current = (message,)
        if estimate_chunk_tokens(scope.topic, current) > max_chunk_input_tokens:
            raise GenerationBudgetExceeded("single message exceeds the chunk input budget")
    if current:
        groups.append(current)

    if len(groups) > scope.budget.max_provider_calls:
        raise GenerationBudgetExceeded("provider call budget would be exceeded")

    chunks: list[GenerationChunk] = []
    for index, messages in enumerate(groups):
        payload = _chunk_payload(scope.topic, messages)
        identity = json.dumps(
            {
                "version": 1,
                "scope_fingerprint": scope.fingerprint(),
                "index": index,
                "payload_sha256": sha256(payload).hexdigest(),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        chunks.append(
            GenerationChunk(
                chunk_id=f"chunk:v1:{sha256(identity).hexdigest()}",
                index=index,
                messages=messages,
                estimated_input_tokens=estimate_chunk_tokens(scope.topic, messages),
                payload=payload,
            )
        )

    total_input = sum(chunk.estimated_input_tokens for chunk in chunks)
    if total_input > scope.budget.max_input_tokens:
        raise GenerationBudgetExceeded("total input budget would be exceeded")
    return GenerationPlan(
        scope_fingerprint=scope.fingerprint(),
        chunks=tuple(chunks),
        total_estimated_input_tokens=total_input,
        total_max_output_tokens=len(chunks) * scope.budget.max_output_tokens,
    )


def _search_run_id(selection: GenerationSelection) -> str:
    payload = json.dumps(
        {
            "version": 1,
            "snapshot_id": selection.snapshot_id,
            "conversation_ids": sorted(selection.conversation_ids),
            "start_time": selection.start_time.isoformat(),
            "end_time": selection.end_time.isoformat(),
            "topic": selection.topic,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"search:v1:{sha256(payload).hexdigest()}"
