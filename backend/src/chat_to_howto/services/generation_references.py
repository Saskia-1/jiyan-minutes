"""Deterministic task-local references for provider-facing generation payloads."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable

from chat_to_howto.domain.generation import GenerationResult, OutboundMessage, summary_groups


_SHORT_REFERENCE = re.compile(r"^E[0-9]{3,}$")
_REFERENCE_FIELDS = frozenset({
    "source_message_ids",
    "claim_a_source_message_ids",
    "claim_b_source_message_ids",
    "resolution_source_message_ids",
})


def generation_reference_ids(result: GenerationResult) -> tuple[str, ...]:
    """Return cited IDs once, preserving their first occurrence in the result."""
    references: list[str] = []
    if result.summary is not None:
        for _heading, items in summary_groups(result.summary):
            references.extend(identifier for item in items for identifier in item.source_message_ids)
    references.extend(
        identifier
        for item in (*result.preparations, *result.steps, *result.checks)
        for identifier in item.source_message_ids
    )
    for conflict in result.conflicts:
        references.extend(conflict.claim_a_source_message_ids)
        references.extend(conflict.claim_b_source_message_ids)
        references.extend(conflict.resolution_source_message_ids)
    return tuple(dict.fromkeys(references))


@dataclass(frozen=True)
class EvidenceReferenceMap:
    """A closed, deterministic mapping that is never learned from model output."""

    pairs: tuple[tuple[str, str], ...]

    @classmethod
    def from_messages(cls, messages: Iterable[OutboundMessage]) -> "EvidenceReferenceMap":
        message_ids = tuple(message.message_id for message in messages)
        width = max(3, len(str(len(message_ids))))
        return cls.from_pairs(
            message_ids=message_ids,
            reference_ids=tuple(f"E{index:0{width}d}" for index in range(1, len(message_ids) + 1)),
        )

    @classmethod
    def from_pairs(
        cls,
        *,
        message_ids: Iterable[str],
        reference_ids: Iterable[str],
    ) -> "EvidenceReferenceMap":
        real = tuple(message_ids)
        short = tuple(reference_ids)
        if len(real) != len(short):
            raise ValueError("reference mapping length mismatch")
        if len(set(real)) != len(real) or len(set(short)) != len(short):
            raise ValueError("reference mapping must be one-to-one")
        if any(not identifier.strip() for identifier in real):
            raise ValueError("real message IDs must be nonblank")
        if any(_SHORT_REFERENCE.fullmatch(reference) is None for reference in short):
            raise ValueError("short reference format is invalid")
        return cls(tuple(zip(short, real, strict=True)))

    def references_for(self, messages: Iterable[OutboundMessage]) -> tuple[str, ...]:
        real_to_short = {real: short for short, real in self.pairs}
        try:
            return tuple(real_to_short[message.message_id] for message in messages)
        except KeyError as error:
            raise ValueError("message is outside the task reference mapping") from error

    def encode_result(
        self,
        result: GenerationResult,
        *,
        allowed_message_ids: Iterable[str] | None = None,
    ) -> GenerationResult:
        real_to_short = {real: short for short, real in self.pairs}
        allowed = set(real_to_short) if allowed_message_ids is None else set(allowed_message_ids)
        return self._transform(result, mapping=real_to_short, allowed=allowed)

    def restore_result(
        self,
        result: GenerationResult,
        *,
        allowed_reference_ids: Iterable[str] | None = None,
    ) -> GenerationResult:
        short_to_real = dict(self.pairs)
        allowed = set(short_to_real) if allowed_reference_ids is None else set(allowed_reference_ids)
        return self._transform(result, mapping=short_to_real, allowed=allowed)

    @staticmethod
    def _transform(
        result: GenerationResult,
        *,
        mapping: dict[str, str],
        allowed: set[str],
    ) -> GenerationResult:
        def visit(value: object) -> object:
            if isinstance(value, dict):
                transformed: dict[str, object] = {}
                for key, item in value.items():
                    if key in _REFERENCE_FIELDS:
                        if not isinstance(item, list):
                            raise ValueError("reference list has an invalid shape")
                        resolved: list[str] = []
                        for identifier in item:
                            if not isinstance(identifier, str) or identifier not in allowed:
                                raise ValueError("reference is outside the allowed mapping")
                            try:
                                resolved.append(mapping[identifier])
                            except KeyError as error:
                                raise ValueError("reference is outside the task mapping") from error
                        transformed[key] = resolved
                    else:
                        transformed[key] = visit(item)
                return transformed
            if isinstance(value, list):
                return [visit(item) for item in value]
            return value

        transformed = visit(result.model_dump(mode="json"))
        return GenerationResult.model_validate(transformed)
