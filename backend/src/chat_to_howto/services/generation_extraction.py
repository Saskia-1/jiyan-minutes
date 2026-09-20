"""Strict boundary from untrusted provider text to typed local extraction."""

from __future__ import annotations

from hashlib import sha256
import json

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from chat_to_howto.domain.generation import GenerationResult
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
        parsed = _ProviderExtractionPayload.model_validate(decoded)
    except (TypeError, ValueError, ValidationError, json.JSONDecodeError):
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
        evidence_quotes=parsed.evidence,
    )
