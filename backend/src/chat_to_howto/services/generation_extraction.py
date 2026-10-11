"""Strict boundary from untrusted provider text to typed local extraction."""

from __future__ import annotations

from hashlib import sha256
import json
import logging

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from chat_to_howto.domain.generation import GenerationResult, TopicSummary
from chat_to_howto.providers.base import ProviderResponse
from chat_to_howto.services.generation_planning import GenerationChunk
from chat_to_howto.services.generation_references import (
    EvidenceReferenceMap,
    generation_reference_ids,
)


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


class _IdeaGroup(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    unit_ids: tuple[str, ...] = Field(min_length=1)


class _ProviderIdeasSynthesisPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    result: GenerationResult
    idea_grouping: tuple[_IdeaGroup, ...]
    discarded_unit_ids: tuple[str, ...]


def _validate_ideas_grouping(parsed: _ProviderIdeasSynthesisPayload, payload: dict[str, object]) -> None:
    """Validate only structural ownership; semantic independence still needs review."""
    raw_units = payload.get("idea_units")
    if not isinstance(raw_units, list):
        raise ValueError("idea units are missing")
    units: dict[str, set[str]] = {}
    for raw in raw_units:
        if not isinstance(raw, dict) or not isinstance(raw.get("unit_id"), str) or not isinstance(raw.get("source_message_ids"), list):
            raise ValueError("idea unit is invalid")
        unit_id = raw["unit_id"]
        if unit_id in units:
            raise ValueError("duplicate idea unit")
        units[unit_id] = set(raw["source_message_ids"])

    sections = parsed.result.summary.sections if isinstance(parsed.result.summary, TopicSummary) else ()
    if parsed.result.status == "complete" and (
        not isinstance(parsed.result.summary, TopicSummary)
        or parsed.result.summary.summary_type != "ideas"
        or len(parsed.idea_grouping) != len(sections)
    ):
        raise ValueError("idea grouping does not match the result")
    if parsed.result.status != "complete" and parsed.idea_grouping:
        raise ValueError("insufficient result cannot group ideas")

    grouped = [unit_id for group in parsed.idea_grouping for unit_id in group.unit_ids]
    discarded = parsed.discarded_unit_ids
    if (any(len(set(group.unit_ids)) != len(group.unit_ids) for group in parsed.idea_grouping)
        or len(set(discarded)) != len(discarded)
        or set(grouped) & set(discarded)
        or set(grouped) | set(discarded) != set(units)):
        raise ValueError("idea unit ownership is incomplete or inconsistent")
    for group, section in zip(parsed.idea_grouping, sections, strict=True):
        allowed = set().union(*(units[unit_id] for unit_id in group.unit_ids))
        if not set(section.source_message_ids) <= allowed:
            raise ValueError("idea section cites outside its grouped units")


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
        chunk_payload = json.loads(chunk.payload)
        if chunk_payload.get("summary_type") is not None:
            ideas_synthesis = chunk_payload.get("summary_type") == "ideas" and chunk_payload.get("operation") == "synthesize"
            parsed = (_ProviderIdeasSynthesisPayload.model_validate(decoded) if ideas_synthesis
                      else _ProviderTopicPayload.model_validate(decoded))
            if ideas_synthesis:
                _validate_ideas_grouping(parsed, chunk_payload)
            if len(chunk.reference_ids) != len(chunk.messages):
                raise ValueError("topic response has no valid reference mapping")
            reference_map = EvidenceReferenceMap.from_pairs(
                message_ids=(message.message_id for message in chunk.messages),
                reference_ids=chunk.reference_ids,
            )
            restored = reference_map.restore_result(parsed.result)
            parsed = parsed.model_copy(update={"result": restored})
            local_text = {message.message_id: message.text for message in chunk.messages}
            references = generation_reference_ids(parsed.result)
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
                   "professional_extensions", "missing_information", "summary_type", "idea_grouping",
                   "discarded_unit_ids", "unit_ids"}
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
