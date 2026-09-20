"""Revalidate every model citation against immutable, session-owned local evidence."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from chat_to_howto.api.session import digest
from chat_to_howto.domain.generation import GenerationResult
from chat_to_howto.domain.messages import StoredMessage
from chat_to_howto.services.generation_merge import MergedGeneration
from chat_to_howto.services.generation_scope import GenerationScope
from chat_to_howto.services.generation_redaction import redact_outbound_text


class EvidenceValidationRejected(ValueError):
    """A fixed body-free rejection; the result must not be marked verified."""


class ValidatedEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)

    message_id: str
    conversation_id: str
    timestamp: datetime
    sender_id: str | None
    sender_name: str | None
    quote: str = Field(repr=False)


class ValidatedGeneration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)

    evidence_status: Literal["verified"] = "verified"
    batch_id: str
    snapshot_id: str
    scope_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_extraction_ids: tuple[str, ...]
    result: GenerationResult = Field(repr=False)
    evidence: tuple[ValidatedEvidence, ...] = Field(repr=False)


class _EvidenceRepository(Protocol):
    def generation_snapshot_messages(
        self, *, session_digest: str, snapshot_id: str
    ) -> tuple[str, tuple[StoredMessage, ...]]:
        ...


class EvidenceValidator:
    def __init__(self, repository: _EvidenceRepository) -> None:
        self._repository = repository

    def validate(
        self,
        merged: MergedGeneration,
        *,
        scope: GenerationScope,
        session_id: str,
    ) -> ValidatedGeneration:
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
        scope: GenerationScope,
        session_digest: str,
    ) -> ValidatedGeneration:
        if (
            not isinstance(session_digest, str)
            or len(session_digest) != 64
            or any(character not in "0123456789abcdef" for character in session_digest)
        ):
            raise PermissionError("local session is unavailable")
        batch_id, snapshot_messages = self._repository.generation_snapshot_messages(
            session_digest=session_digest,
            snapshot_id=scope.snapshot_id,
        )
        if batch_id != scope.batch_id:
            raise EvidenceValidationRejected("Evidence scope identity mismatch.")
        snapshot_by_id = {message.message_id: message for message in snapshot_messages}
        allowed_conversations = set(scope.conversation_ids)
        scope_by_id = {message.message_id: message for message in scope.messages}

        for outbound in scope.messages:
            stored = snapshot_by_id.get(outbound.message_id)
            if stored is None:
                raise EvidenceValidationRejected("Evidence scope message is missing.")
            if (
                stored.conversation_id not in allowed_conversations
                or stored.sent_at_utc is None
                or not scope.range_start <= stored.sent_at_utc < scope.range_end
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
                or stored.sent_at_utc is None
                or not scope.range_start <= stored.sent_at_utc < scope.range_end
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


def _referenced_message_ids(result: GenerationResult) -> tuple[str, ...]:
    ordered: list[str] = []
    for item in (*result.preparations, *result.steps, *result.checks):
        ordered.extend(item.source_message_ids)
    for conflict in result.conflicts:
        ordered.extend(conflict.claim_a_source_message_ids)
        ordered.extend(conflict.claim_b_source_message_ids)
        ordered.extend(conflict.resolution_source_message_ids)
    return tuple(dict.fromkeys(ordered))
