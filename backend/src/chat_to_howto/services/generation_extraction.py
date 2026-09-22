"""Strict boundary from untrusted provider text to typed local extraction."""

from __future__ import annotations

from hashlib import sha256
import json
import logging

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from chat_to_howto.domain.generation import GenerationResult, summary_groups
from chat_to_howto.providers.base import ProviderResponse
from chat_to_howto.services.generation_planning import GenerationChunk


class ExtractionRejected(ValueError):
    """Provider output cannot safely enter merge or persistence."""


class EvidenceQuote(BaseModel):
    """Untrusted provider quote retained only until local evidence validation."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)

    message_id: str = Field(min_length=1, max_length=256)
    quote: str = Field(min_length=1, max_length=1_000_000, repr=False)

    @field_validator("message_id", "quote")
    @classmethod
    def _reject_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("evidence fields must be nonblank")
        return value


class _ProviderExtractionPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    result: GenerationResult
    evidence: tuple[EvidenceQuote, ...]


class _ProviderTopicPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    result: GenerationResult


def _topic_references(result: GenerationResult) -> tuple[str, ...]:
    references = []
    if result.summary is not None:
        for _heading, items in summary_groups(result.summary):
            references.extend(mid for item in items for mid in item.source_message_ids)
    references.extend(mid for item in (*result.preparations, *result.steps, *result.checks)
                      for mid in item.source_message_ids)
    for conflict in result.conflicts:
        references.extend((*conflict.claim_a_source_message_ids, *conflict.claim_b_source_message_ids,
                           *conflict.resolution_source_message_ids))
    return tuple(dict.fromkeys(references))


class StructuredExtraction(BaseModel):
    """Typed, content-hashed extraction; raw provider text is not retained."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)

    extraction_id: str = Field(pattern=r"^extraction:v1:[0-9a-f]{64}$")
    chunk_id: str = Field(pattern=r"^chunk:v1:[0-9a-f]{64}$")
    chunk_index: int = Field(ge=0)
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    raw_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    result: GenerationResult = Field(repr=False)
    evidence_quotes: tuple[EvidenceQuote, ...] = Field(repr=False)


def parse_structured_extraction(
    chunk: GenerationChunk,
    response: ProviderResponse,
) -> StructuredExtraction:
    """Reject truncation, wrappers, and every schema deviation with a body-free error."""
    if response.finish_reason != "stop":
        raise ExtractionRejected("Provider extraction was incomplete.")
    try:
        decoded = json.loads(response.content)
        if not isinstance(decoded, dict):
            raise TypeError
        if isinstance(decoded.get("result"), dict) and "user_text" in decoded["result"]:
            raise ValueError("provider cannot supply user-authored content")
        if json.loads(chunk.payload).get("summary_type") is not None:
            parsed = _ProviderTopicPayload.model_validate(decoded)
            local_text = {message.message_id: message.text for message in chunk.messages}
            references = _topic_references(parsed.result)
            if any(message_id not in local_text for message_id in references):
                raise ValueError("citation outside this chunk")
            evidence_quotes = tuple(EvidenceQuote(message_id=message_id, quote=local_text[message_id]) for message_id in references)
        else:
            parsed = _ProviderExtractionPayload.model_validate(decoded)
            evidence_quotes = parsed.evidence
    except ValidationError as error:
        # Diagnostic categories only: never log provider prose, IDs or inputs.
        allowed = {"result", "summary", "TopicSummary", "SummaryContent", "sections", "source_message_ids",
                   "heading", "text", "status", "title", "preparations", "steps", "checks", "conflicts",
                   "professional_extensions", "missing_information", "summary_type"}
        categories = [(tuple(part if isinstance(part, int) or part in allowed else "field" for part in item["loc"]), item["type"])
                      for item in error.errors(include_input=False, include_context=False)]
        logging.getLogger(__name__).warning("Provider schema rejected: %s", categories)
        raise ExtractionRejected("Provider extraction failed schema validation.") from None
    except (TypeError, ValueError, json.JSONDecodeError):
        logging.getLogger(__name__).warning("Provider extraction rejected: invalid JSON or reference contract")
        raise ExtractionRejected("Provider extraction failed schema validation.") from None
    raw_sha256 = sha256(response.content.encode("utf-8")).hexdigest()
    identity = sha256(
        (
            "v1\0"
            + chunk.chunk_id
            + "\0"
            + response.provider
            + "\0"
            + response.model
            + "\0"
            + raw_sha256
        ).encode("utf-8")
    ).hexdigest()
    return StructuredExtraction(
        extraction_id=f"extraction:v1:{identity}",
        chunk_id=chunk.chunk_id,
        chunk_index=chunk.index,
        provider=response.provider,
        model=response.model,
        raw_sha256=raw_sha256,
        result=parsed.result,
        evidence_quotes=evidence_quotes,
    )
