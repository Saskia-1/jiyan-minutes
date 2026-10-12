"""Revalidate every model citation against immutable, session-owned local evidence."""

from __future__ import annotations

from datetime import datetime
import json
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from chat_to_howto.api.session import digest
from chat_to_howto.domain.generation import GenerationResult, TopicSummary, summary_groups
from chat_to_howto.domain.messages import StoredMessage
from chat_to_howto.domain.mixed_sources import SourceRef
from chat_to_howto.services.generation_merge import MergedGeneration
from chat_to_howto.services.generation_scope import AnyGenerationScope, GenerationScope, MixedGenerationScope
from chat_to_howto.services.generation_redaction import redact_outbound_text
from chat_to_howto.repositories.content import ContentRepository


class EvidenceValidationRejected(ValueError):
    """A fixed body-free rejection; the result must not be marked verified."""


class ValidatedEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)

    message_id: str
    conversation_id: str | None
    timestamp: datetime | None
    sender_id: str | None
    sender_name: str | None
    quote: str = Field(repr=False)
    source_kind: Literal["chat_message", "note_passage"] | None = Field(default=None, exclude_if=lambda value: value is None)
    source_batch_id: str | None = Field(default=None, exclude_if=lambda value: value is None)
    image_id: str | None = Field(default=None, exclude_if=lambda value: value is None)


class ValidatedGeneration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)

    evidence_status: Literal["verified"] = "verified"
    batch_id: str
    snapshot_id: str
    scope_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_extraction_ids: tuple[str, ...]
    result: GenerationResult = Field(repr=False)
    evidence: tuple[ValidatedEvidence, ...] = Field(repr=False)


class ValidatedMixedGeneration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)

    schema_version: Literal[2] = 2
    evidence_status: Literal["verified"] = "verified"
    source_refs: tuple[SourceRef, ...]
    snapshot_id: str  # Internal v2 cache identity, never a real chat snapshot.
    scope_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_extraction_ids: tuple[str, ...]
    result: GenerationResult = Field(repr=False)
    evidence: tuple[ValidatedEvidence, ...] = Field(repr=False)


AnyValidatedGeneration = ValidatedGeneration | ValidatedMixedGeneration


def parse_validated_generation(value: str) -> AnyValidatedGeneration:
    raw = json.loads(value)
    if not isinstance(raw, dict):
        raise ValueError("validated generation is invalid")
    if raw.get("schema_version") == 2:
        return ValidatedMixedGeneration.model_validate_json(value)
    return ValidatedGeneration.model_validate_json(value)


class _EvidenceRepository(Protocol):
    def generation_snapshot_messages(
        self, *, session_digest: str, snapshot_id: str
    ) -> tuple[str, tuple[StoredMessage, ...]]:
        ...


class EvidenceValidator:
    def __init__(self, repository: _EvidenceRepository, content_repository: ContentRepository | None = None) -> None:
        self._repository = repository
        self._content = content_repository

    def validate(
        self,
        merged: MergedGeneration,
        *,
        scope: AnyGenerationScope,
        session_id: str,
    ) -> AnyValidatedGeneration:
        if not isinstance(session_id, str) or not session_id:
            raise PermissionError("local session is unavailable")
        return self.validate_with_digest(
            merged,
            scope=scope,
            session_digest=digest(session_id),
        )

    def validate_with_digest(
        self,
        merged: MergedGeneration,
        *,
        scope: AnyGenerationScope,
        session_digest: str,
    ) -> AnyValidatedGeneration:
        if (
            not isinstance(session_digest, str)
            or len(session_digest) != 64
            or any(character not in "0123456789abcdef" for character in session_digest)
        ):
            raise PermissionError("local session is unavailable")
        if isinstance(scope, MixedGenerationScope):
            return self._validate_mixed(merged, scope=scope, session_digest=session_digest)
        batch_id, snapshot_messages = self._repository.generation_snapshot_messages(
            session_digest=session_digest,
            snapshot_id=scope.snapshot_id,
        )
        if batch_id != scope.batch_id:
            raise EvidenceValidationRejected("Evidence scope identity mismatch.")
        snapshot_by_id = {message.message_id: message for message in snapshot_messages}
        if merged.result.user_text is not None:
            raise EvidenceValidationRejected("Generated content cannot claim user authorship.")
        allowed_conversations = set(scope.conversation_ids)
        scope_by_id = {message.message_id: message for message in scope.messages}
        if scope.summary_type is not None and merged.result.status == "complete":
            summary = merged.result.summary
            if ((scope.summary_type == "how_to" and summary is not None)
                or (scope.summary_type != "how_to" and (
                    not isinstance(summary, TopicSummary) or summary.summary_type != scope.summary_type
                ))):
                raise EvidenceValidationRejected("Generation direction does not match the frozen scope.")

        def within_range(stored: StoredMessage) -> bool:
            return (scope.allow_undated if stored.sent_at_utc is None
                    else scope.range_start <= stored.sent_at_utc < scope.range_end)

        for outbound in scope.messages:
            stored = snapshot_by_id.get(outbound.message_id)
            if stored is None:
                raise EvidenceValidationRejected("Evidence scope message is missing.")
            if (
                stored.conversation_id not in allowed_conversations
                or not within_range(stored)
                or stored.text is None
                or redact_outbound_text(
                    stored.text, policy_revision=scope.redaction_policy_revision
                ) != outbound.text
            ):
                raise EvidenceValidationRejected("Evidence scope content mismatch.")

        referenced = _referenced_message_ids(merged.result)
        referenced_set = set(referenced)
        quote_ids = tuple(quote.message_id for quote in merged.evidence_quotes)
        quote_set = set(quote_ids)
        if quote_set - referenced_set:
            raise EvidenceValidationRejected("Provider supplied unreferenced evidence.")
        if referenced_set - quote_set:
            raise EvidenceValidationRejected("Referenced evidence quote is missing.")

        grouped_quotes: dict[str, list[str]] = {}
        for quote in merged.evidence_quotes:
            grouped_quotes.setdefault(quote.message_id, []).append(quote.quote)
        if any(len(set(quotes)) != 1 or len(quotes) != 1 for quotes in grouped_quotes.values()):
            raise EvidenceValidationRejected("Evidence quote is conflicting.")

        validated: list[ValidatedEvidence] = []
        for message_id in referenced:
            stored = snapshot_by_id.get(message_id)
            if stored is None:
                raise EvidenceValidationRejected("Referenced evidence is missing.")
            if message_id not in scope_by_id:
                raise EvidenceValidationRejected("Referenced evidence is outside the allowed scope.")
            quote = grouped_quotes[message_id][0]
            if stored.text is None or quote != scope_by_id[message_id].text:
                raise EvidenceValidationRejected("Evidence quote does not match local content.")
            if (
                stored.conversation_id not in allowed_conversations
                or not within_range(stored)
            ):
                raise EvidenceValidationRejected("Referenced evidence is outside the allowed scope.")
            validated.append(
                ValidatedEvidence(
                    message_id=stored.message_id,
                    conversation_id=stored.conversation_id,
                    timestamp=stored.sent_at_utc,
                    sender_id=stored.sender_id,
                    sender_name=stored.sender_name,
                    quote=stored.text,
                )
            )
        return ValidatedGeneration(
            batch_id=batch_id,
            snapshot_id=scope.snapshot_id,
            scope_fingerprint=scope.fingerprint(),
            source_extraction_ids=merged.source_extraction_ids,
            result=merged.result,
            evidence=tuple(validated),
        )

    def _validate_mixed(
        self, merged: MergedGeneration, *, scope: MixedGenerationScope, session_digest: str,
    ) -> ValidatedMixedGeneration:
        if self._content is None:
            raise EvidenceValidationRejected("Mixed evidence source is unavailable.")
        if merged.result.user_text is not None:
            raise EvidenceValidationRejected("Generated content cannot claim user authorship.")
        summary = merged.result.summary
        if merged.result.status == "complete" and (
            (scope.summary_type == "how_to" and summary is not None)
            or (scope.summary_type != "how_to" and (
                not isinstance(summary, TopicSummary) or summary.summary_type != scope.summary_type
            ))
        ):
            raise EvidenceValidationRejected("Generation direction does not match the frozen scope.")

        original: dict[str, dict[str, object]] = {}
        for index, ref in enumerate(scope.source_refs):
            group = f"source-{index + 1}"
            if ref.kind == "chat_snapshot":
                batch_id, messages = self._repository.generation_snapshot_messages(
                    session_digest=session_digest, snapshot_id=ref.id,
                )
                entries = ({
                    "id": message.message_id, "kind": "chat_message", "batch_id": batch_id,
                    "text": message.text, "timestamp": message.sent_at_utc,
                    "sender_id": message.sender_id, "sender_name": message.sender_name,
                    "conversation_id": message.conversation_id, "image_id": None,
                    "group": group,
                } for message in messages)
            else:
                units = self._content.list_units(ref.id, session_digest=session_digest)
                if not units:
                    raise EvidenceValidationRejected("Mixed evidence source is unavailable.")
                entries = ({
                    "id": unit["unit_id"], "kind": unit["kind"], "batch_id": ref.id,
                    "text": unit["text"],
                    "timestamp": datetime.fromisoformat(unit["sent_at"]) if unit["sent_at"] else None,
                    "sender_id": None, "sender_name": unit["sender_name"],
                    "conversation_id": None, "image_id": unit["image_id"],
                    "group": group,
                } for unit in units)
            for entry in entries:
                identifier = str(entry["id"])
                if identifier in original:
                    raise EvidenceValidationRejected("Mixed evidence identity is ambiguous.")
                original[identifier] = entry

        def allowed(entry: dict[str, object]) -> bool:
            timestamp = entry["timestamp"]
            if timestamp is None:
                return scope.include_undated
            if not isinstance(timestamp, datetime):
                return False
            return scope.start_time is None or scope.start_time <= timestamp < scope.end_time

        scoped = {message.message_id: message for message in scope.messages}
        if set(scoped) != set(scope.included_unit_ids) - set(scope.excluded_unit_ids):
            raise EvidenceValidationRejected("Mixed evidence selection changed.")
        for message in scope.messages:
            entry = original.get(message.message_id)
            if (entry is None or not allowed(entry) or entry["kind"] != message.source_kind
                or entry["group"] != message.source_group or not isinstance(entry["text"], str)
                or redact_outbound_text(entry["text"], policy_revision=scope.redaction_policy_revision) != message.text):
                raise EvidenceValidationRejected("Mixed evidence scope content mismatch.")

        references = _referenced_message_ids(merged.result)
        quote_ids = tuple(quote.message_id for quote in merged.evidence_quotes)
        if len(set(quote_ids)) != len(quote_ids) or set(quote_ids) != set(references):
            raise EvidenceValidationRejected("Mixed evidence quote set is invalid.")
        by_quote = {quote.message_id: quote.quote for quote in merged.evidence_quotes}
        validated: list[ValidatedEvidence] = []
        for identifier in references:
            entry = original.get(identifier)
            message = scoped.get(identifier)
            if entry is None or message is None or by_quote[identifier] != message.text:
                raise EvidenceValidationRejected("Mixed evidence reference or quote is invalid.")
            validated.append(ValidatedEvidence(
                message_id=identifier, conversation_id=entry["conversation_id"],
                timestamp=entry["timestamp"], sender_id=entry["sender_id"],
                sender_name=entry["sender_name"], quote=entry["text"],
                source_kind=entry["kind"], source_batch_id=entry["batch_id"],
                image_id=entry["image_id"],
            ))
        return ValidatedMixedGeneration(
            source_refs=scope.source_refs, snapshot_id=scope.snapshot_id,
            scope_fingerprint=scope.fingerprint(),
            source_extraction_ids=merged.source_extraction_ids,
            result=merged.result, evidence=tuple(validated),
        )


def _referenced_message_ids(result: GenerationResult) -> tuple[str, ...]:
    ordered: list[str] = []
    if result.summary is not None:
        for _label, items in summary_groups(result.summary):
            for item in items:
                ordered.extend(item.source_message_ids)
    for item in (*result.preparations, *result.steps, *result.checks):
        ordered.extend(item.source_message_ids)
    for conflict in result.conflicts:
        ordered.extend(conflict.claim_a_source_message_ids)
        ordered.extend(conflict.claim_b_source_message_ids)
        ordered.extend(conflict.resolution_source_message_ids)
    return tuple(dict.fromkeys(ordered))
