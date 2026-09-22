"""Deterministic merge of typed chunk extractions with evidence preservation."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from pydantic import BaseModel, ConfigDict, Field

from chat_to_howto.domain.generation import (
    Check,
    Conflict,
    GenerationResult,
    Preparation,
    Step,
    SummaryContent,
    SummaryItem,
    TopicSummary,
    TopicSection,
)
from chat_to_howto.services.generation_extraction import EvidenceQuote, StructuredExtraction


class MergeRejected(ValueError):
    """The extraction set cannot be deterministically merged."""


class MergedGeneration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)

    source_extraction_ids: tuple[str, ...] = Field(min_length=1)
    result: GenerationResult = Field(repr=False)
    evidence_quotes: tuple[EvidenceQuote, ...] = Field(repr=False)


T = TypeVar("T")


def _union_ids(first: tuple[str, ...], second: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys((*first, *second)))


def _merge_items(
    items: list[T],
    *,
    key: Callable[[T], tuple[object, ...]],
    combine: Callable[[T, T], T],
) -> tuple[T, ...]:
    ordered: list[T] = []
    positions: dict[tuple[object, ...], int] = {}
    for item in items:
        identity = key(item)
        existing = positions.get(identity)
        if existing is None:
            positions[identity] = len(ordered)
            ordered.append(item)
        else:
            ordered[existing] = combine(ordered[existing], item)
    return tuple(ordered)


def merge_extractions(extractions: tuple[StructuredExtraction, ...]) -> MergedGeneration:
    if not extractions:
        raise MergeRejected("at least one extraction is required")
    extraction_ids = tuple(item.extraction_id for item in extractions)
    if len(set(extraction_ids)) != len(extraction_ids):
        raise MergeRejected("duplicate extraction identity")
    chunk_ids = tuple(item.chunk_id for item in extractions)
    chunk_indexes = tuple(item.chunk_index for item in extractions)
    if len(set(chunk_ids)) != len(chunk_ids) or len(set(chunk_indexes)) != len(chunk_indexes):
        raise MergeRejected("duplicate chunk identity")

    ordered = tuple(sorted(extractions, key=lambda item: (item.chunk_index, item.chunk_id)))
    complete = tuple(item for item in ordered if item.result.status == "complete")
    if not complete:
        result = GenerationResult.insufficient()
    elif isinstance(complete[0].result.summary, TopicSummary):
        direction = complete[0].result.summary.summary_type
        if any(not isinstance(item.result.summary, TopicSummary)
               or item.result.summary.summary_type != direction for item in complete):
            raise MergeRejected("generation directions cannot be mixed")
        sections = _merge_items(
            [section for extraction in complete for section in extraction.result.summary.sections],
            key=lambda item: (item.heading, item.text),
            combine=lambda first, second: TopicSection(
                heading=first.heading, text=first.text,
                source_message_ids=_union_ids(first.source_message_ids, second.source_message_ids),
            ),
        )
        result = GenerationResult(
            status="complete", title=complete[0].result.title,
            preparations=(), steps=(), checks=(), missing_information=(), conflicts=(),
            summary=TopicSummary(summary_type=direction, sections=sections),
        )
    elif complete[0].result.summary is not None:
        if any(not isinstance(item.result.summary, SummaryContent) for item in complete):
            raise MergeRejected("guide and summary chunks cannot be mixed")
        sections: dict[str, tuple[SummaryItem, ...]] = {}
        for field in ("core_summary", "discussions", "decisions", "action_items", "open_questions"):
            sections[field] = _merge_items(
                [
                    entry
                    for extraction in complete
                    for entry in getattr(extraction.result.summary, field)
                ],
                key=lambda item: (item.text,),
                combine=lambda first, second: SummaryItem(
                    text=first.text,
                    source_message_ids=_union_ids(
                        first.source_message_ids, second.source_message_ids
                    ),
                ),
            )
        result = GenerationResult(
            status="complete", title=complete[0].result.title,
            preparations=(), steps=(), checks=(), missing_information=(), conflicts=(),
            summary=SummaryContent(**sections),
        )
    else:
        if any(item.result.summary is not None for item in complete):
            raise MergeRejected("guide and summary chunks cannot be mixed")
        preparations = _merge_items(
            [item for extraction in complete for item in extraction.result.preparations],
            key=lambda item: (item.text,),
            combine=lambda first, second: Preparation(
                text=first.text,
                source_message_ids=_union_ids(
                    first.source_message_ids, second.source_message_ids
                ),
            ),
        )
        steps = _merge_items(
            [item for extraction in complete for item in extraction.result.steps],
            key=lambda item: (
                item.instruction,
                item.location,
                item.parameters,
                item.conditions,
                item.warnings,
                item.expected_result,
            ),
            combine=lambda first, second: Step(
                instruction=first.instruction,
                location=first.location,
                parameters=first.parameters,
                conditions=first.conditions,
                warnings=first.warnings,
                expected_result=first.expected_result,
                source_message_ids=_union_ids(
                    first.source_message_ids, second.source_message_ids
                ),
            ),
        )
        checks = _merge_items(
            [item for extraction in complete for item in extraction.result.checks],
            key=lambda item: (item.text,),
            combine=lambda first, second: Check(
                text=first.text,
                source_message_ids=_union_ids(
                    first.source_message_ids, second.source_message_ids
                ),
            ),
        )
        conflicts = _merge_items(
            [item for extraction in complete for item in extraction.result.conflicts],
            key=lambda item: (
                item.description,
                item.claim_a,
                item.claim_b,
                item.resolution,
            ),
            combine=_combine_conflicts,
        )
        missing = tuple(
            dict.fromkeys(
                value
                for extraction in complete
                for value in extraction.result.missing_information
            )
        )
        result = GenerationResult(
            status="complete",
            title=complete[0].result.title,
            preparations=preparations,
            steps=steps,
            checks=checks,
            missing_information=missing,
            conflicts=conflicts,
        )
    extensions = tuple(dict.fromkeys(item for extraction in complete for item in extraction.result.professional_extensions))
    if extensions:
        result = GenerationResult.model_validate({**result.model_dump(), "professional_extensions": extensions})
    return MergedGeneration(
        source_extraction_ids=tuple(item.extraction_id for item in ordered),
        result=result,
        evidence_quotes=tuple(
            dict.fromkeys(
                quote
                for extraction in ordered
                for quote in extraction.evidence_quotes
            )
        ),
    )


def _combine_conflicts(first: Conflict, second: Conflict) -> Conflict:
    return Conflict(
        description=first.description,
        claim_a=first.claim_a,
        claim_a_source_message_ids=_union_ids(
            first.claim_a_source_message_ids, second.claim_a_source_message_ids
        ),
        claim_b=first.claim_b,
        claim_b_source_message_ids=_union_ids(
            first.claim_b_source_message_ids, second.claim_b_source_message_ids
        ),
        resolution=first.resolution,
        resolution_source_message_ids=_union_ids(
            first.resolution_source_message_ids,
            second.resolution_source_message_ids,
        ),
    )
