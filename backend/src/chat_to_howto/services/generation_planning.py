"""Local-only P2 selection, deterministic chunking, and hard-budget planning."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from math import ceil
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from chat_to_howto.api.session import digest
from chat_to_howto.domain.generation import GenerationBudget, GenerationResult, OutboundMessage, SummaryType
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
    generation_kind: Literal["guide", "summary"] = "guide"
    summary_type: SummaryType | None = None
    # Unknown dates are an explicit part of the frozen selection, never fabricated.
    allow_undated: bool = False
    included_message_ids: tuple[str, ...] = Field(default=(), max_length=2_000)
    provider: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=128)
    redaction_policy_revision: str = Field(min_length=1, max_length=128)
    excluded_message_ids: tuple[str, ...] = Field(default=(), max_length=2_000)
    budget: GenerationBudget

    @field_validator("conversation_ids", "excluded_message_ids", "included_message_ids", mode="before")
    @classmethod
    def _parse_sequences(cls, value: object) -> object:
        if isinstance(value, list):
            return tuple(value)
        return value

    @field_validator("conversation_ids", "excluded_message_ids", "included_message_ids")
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
        if self.generation_kind == "guide" and self.summary_type is None and self.included_message_ids:
            raise ValueError("guide generation does not accept recalled message IDs")
        if self.allow_undated and not self.included_message_ids:
            raise ValueError("undated scope requires explicit message IDs")
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

    def _scoped(self, selection: GenerationSelection, session_id: str):
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
            and ((message.sent_at_utc is not None
                  and selection.start_time <= message.sent_at_utc < selection.end_time)
                 or (selection.allow_undated and message.sent_at_utc is None))
        )
        order = source_message_order if selection.allow_undated else message_order
        return batch_id, tuple(sorted(scoped, key=order))

    def context_messages(self, selection: GenerationSelection, *, session_id: str):
        """Six neighbours and short intervening gaps, never beyond selected scope."""
        batch_id, scoped = self._scoped(selection, session_id)
        hits = set(selection.included_message_ids)
        by_id = {message.message_id: message for message in scoped}
        if not hits or not hits <= by_id.keys():
            raise ValueError("context seeds must be inside the selected scope")
        expanded = set(hits)
        for conversation in selection.conversation_ids:
            messages = [message for message in scoped if message.conversation_id == conversation]
            for index, message in enumerate(messages):
                if message.message_id not in hits:
                    continue
                expanded.update(item.message_id for item in messages[max(0, index - 6):index + 7])
                reply = by_id.get(message.reply_to_message_id)
                if reply is not None and reply.conversation_id == conversation:
                    expanded.add(reply.message_id)
            # Brief turns between two recalled passages often contain the
            # mechanism or qualification but repeat none of the keywords.
            # Fill only gaps of <= 12 messages; distant passages stay separate.
            positions = [i for i, item in enumerate(messages) if item.message_id in expanded]
            for left, right in zip(positions, positions[1:]):
                if 1 < right - left <= 13:
                    expanded.update(item.message_id for item in messages[left + 1:right])
        if len(expanded) > 2_000:
            raise GenerationBudgetExceeded("context exceeds the recalled-message limit")
        return batch_id, tuple(message for message in scoped if message.message_id in expanded), len(expanded - hits)

    def build(self, selection: GenerationSelection, *, session_id: str) -> GenerationScope:
        batch_id, scoped = self._scoped(selection, session_id)
        scoped_ids = {message.message_id for message in scoped}
        excluded = set(selection.excluded_message_ids)
        if not excluded <= scoped_ids:
            raise ValueError("excluded message is outside the selected local scope")
        included = set(selection.included_message_ids)
        use_recalled = bool(included) or selection.generation_kind == "summary"
        if use_recalled:
            if not included <= scoped_ids or not excluded <= included:
                raise ValueError("recalled messages must stay inside the selected scope")

        eligible = tuple(
            message
            for message in scoped
            if message.message_id not in excluded
            and isinstance(message.text, str)
            and bool(message.text.strip())
            and message.message_type != "placeholder"
            and (
                message.message_id in included
                if use_recalled
                else selection.mode != "fast" or selection.topic in message.text
            )
        )
        ordered = sorted(eligible, key=source_message_order if selection.allow_undated else message_order)
        speakers = {value: f"speaker-{index + 1}" for index, value in enumerate(dict.fromkeys(
            (message.conversation_id, message.sender_id or message.sender_name) for message in ordered
        ))}
        conversation_labels = {value: f"conversation-{index + 1}" for index, value in enumerate(selection.conversation_ids)}
        search_run_id = (
            _search_run_id(selection) if selection.mode == "fast" else None
        )
        return GenerationScope(
            batch_id=batch_id,
            snapshot_id=selection.snapshot_id,
            search_run_id=search_run_id,
            mode=selection.mode,
            generation_kind=selection.generation_kind,
            summary_type=selection.summary_type,
            allow_undated=selection.allow_undated,
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
                    speaker=(speakers[(message.conversation_id, message.sender_id or message.sender_name)]
                             if selection.summary_type is not None else None),
                    conversation=(conversation_labels[message.conversation_id] if selection.summary_type is not None else None),
                )
                for message in ordered
            ),
            excluded_message_ids=selection.excluded_message_ids,
            budget=selection.budget,
        )


def message_order(message: StoredMessage):
    return (message.sent_at_utc or datetime.max.replace(tzinfo=timezone.utc),
            message.source_order is None, message.source_order or 0, message.message_id)


def source_message_order(message: StoredMessage):
    return (message.source_order is None, message.source_order or 0, message.message_id)


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
    synthesis_input_tokens: int = Field(default=0, ge=0)
    max_draft_chars: int = Field(default=0, ge=0)

    @property
    def provider_calls(self) -> int:
        return len(self.chunks) + int(self.synthesis_input_tokens > 0)


def _chunk_payload(topic: str, messages: tuple[OutboundMessage, ...], generation_kind: str = "guide", summary_type: SummaryType | None = None) -> bytes:
    payload = {
        "schema_version": 1,
        "topic": topic,
        "messages": [
            message.model_dump(mode="json")
            for message in messages
        ],
    }
    if generation_kind == "summary":
        payload["generation_kind"] = "summary"
    if summary_type is not None:
        payload["summary_type"] = summary_type
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def estimate_chunk_tokens(topic: str, messages: tuple[OutboundMessage, ...], generation_kind: str = "guide", summary_type: SummaryType | None = None) -> int:
    """Conservative deterministic estimate; it is not presented as exact billing."""
    return ceil(len(_chunk_payload(topic, messages, generation_kind, summary_type).decode("utf-8")) / 1.5)


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
        if estimate_chunk_tokens(scope.topic, candidate, scope.generation_kind, scope.summary_type) <= max_chunk_input_tokens:
            current = candidate
            continue
        if not current:
            raise GenerationBudgetExceeded("single message exceeds the chunk input budget")
        groups.append(current)
        current = (message,)
        if estimate_chunk_tokens(scope.topic, current, scope.generation_kind, scope.summary_type) > max_chunk_input_tokens:
            raise GenerationBudgetExceeded("single message exceeds the chunk input budget")
    if current:
        groups.append(current)

    synthesis = scope.summary_type is not None and len(groups) > 1
    calls = len(groups) + int(synthesis)
    if calls > scope.budget.max_provider_calls:
        raise GenerationBudgetExceeded("provider call budget would be exceeded")

    chunks: list[GenerationChunk] = []
    for index, messages in enumerate(groups):
        payload = _chunk_payload(scope.topic, messages, scope.generation_kind, scope.summary_type)
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
                estimated_input_tokens=estimate_chunk_tokens(scope.topic, messages, scope.generation_kind, scope.summary_type),
                payload=payload,
            )
        )

    total_input = sum(chunk.estimated_input_tokens for chunk in chunks)
    # Citation IDs and JSON fields also consume space; reserve it before consent.
    max_draft_chars = min(12_000, scope.budget.max_output_tokens * 3) if synthesis else 0
    synthesis_input = 0
    if synthesis:
        source_chars = len(_chunk_payload(scope.topic, scope.messages, scope.generation_kind, scope.summary_type).decode("utf-8"))
        available_draft_chars = (64_000 * 3 // 2 - source_chars - 256) // len(chunks)
        max_draft_chars = min(max_draft_chars, available_draft_chars)
        if max_draft_chars < 1_000:
            raise GenerationBudgetExceeded("synthesis leaves insufficient room for bounded drafts")
        synthesis_input = ceil((source_chars + len(chunks) * max_draft_chars + 256) / 1.5)
        if synthesis_input > 64_000:
            raise GenerationBudgetExceeded("synthesis input would exceed the bounded consolidation limit")
    total_input += synthesis_input
    if scope.summary_type is not None:
        total_input += calls * 2_000  # conservative shared/mode prompt allowance
    if total_input > scope.budget.max_input_tokens:
        raise GenerationBudgetExceeded("total input budget would be exceeded")
    return GenerationPlan(
        scope_fingerprint=scope.fingerprint(),
        chunks=tuple(chunks),
        total_estimated_input_tokens=total_input,
        total_max_output_tokens=calls * scope.budget.max_output_tokens,
        synthesis_input_tokens=synthesis_input,
        max_draft_chars=max_draft_chars,
    )


def build_synthesis_chunk(scope: GenerationScope, plan: GenerationPlan, drafts: tuple[GenerationResult, ...]) -> GenerationChunk:
    """Derived content is bounded before sending; original message scope is unchanged."""
    if not plan.synthesis_input_tokens or len(drafts) != len(plan.chunks):
        raise GenerationBudgetExceeded("synthesis plan mismatch")
    if any(len(draft.model_dump_json()) > plan.max_draft_chars for draft in drafts):
        raise GenerationBudgetExceeded("intermediate draft exceeds the pre-authorized budget")
    payload = json.loads(_chunk_payload(scope.topic, scope.messages, scope.generation_kind, scope.summary_type))
    payload.update(operation="synthesize", drafts=[draft.model_dump(mode="json") for draft in drafts])
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    estimate = ceil(len(encoded.decode("utf-8")) / 1.5)
    if estimate > plan.synthesis_input_tokens:
        raise GenerationBudgetExceeded("synthesis exceeds the pre-authorized budget")
    return GenerationChunk(
        chunk_id=f"chunk:v1:{sha256(scope.fingerprint().encode() + encoded).hexdigest()}",
        index=len(plan.chunks), messages=scope.messages,
        estimated_input_tokens=estimate, payload=encoded,
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
