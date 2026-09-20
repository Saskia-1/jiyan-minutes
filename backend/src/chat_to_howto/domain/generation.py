"""Strict structured how-to output and anonymous outbound message contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)


class _EvidenceItem(_FrozenModel):
    source_message_ids: tuple[str, ...]

    @field_validator("source_message_ids")
    @classmethod
    def _require_evidence(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value or any(not item.strip() for item in value):
            raise ValueError("source_message_ids must contain real evidence IDs")
        if len(set(value)) != len(value):
            raise ValueError("duplicate source_message_ids are not allowed")
        return value


class Preparation(_EvidenceItem):
    text: str = Field(min_length=1)


class Step(_EvidenceItem):
    instruction: str = Field(min_length=1)
    location: str | None
    parameters: tuple[str, ...]
    conditions: tuple[str, ...]
    warnings: tuple[str, ...]
    expected_result: str | None


class Check(_EvidenceItem):
    text: str = Field(min_length=1)


class Conflict(_FrozenModel):
    description: str = Field(min_length=1)
    claim_a: str = Field(min_length=1)
    claim_a_source_message_ids: tuple[str, ...]
    claim_b: str = Field(min_length=1)
    claim_b_source_message_ids: tuple[str, ...]
    resolution: str | None
    resolution_source_message_ids: tuple[str, ...]

    @model_validator(mode="after")
    def _validate_conflict_evidence(self) -> "Conflict":
        for identifiers in (
            self.claim_a_source_message_ids,
            self.claim_b_source_message_ids,
        ):
            if not identifiers or any(not value.strip() for value in identifiers):
                raise ValueError("both conflict claims require evidence")
            if len(set(identifiers)) != len(identifiers):
                raise ValueError("duplicate conflict evidence is not allowed")
        if self.resolution is None and self.resolution_source_message_ids:
            raise ValueError("unresolved conflict cannot have resolution evidence")
        if self.resolution is not None and not self.resolution_source_message_ids:
            raise ValueError("resolved conflict requires resolution evidence")
        if any(not value.strip() for value in self.resolution_source_message_ids):
            raise ValueError("resolution evidence IDs must be nonempty")
        return self


class GenerationResult(_FrozenModel):
    """Only execution content; no background/reason/summary fields exist."""

    status: Literal["complete", "insufficient_evidence"]
    title: str = Field(min_length=1)
    preparations: tuple[Preparation, ...]
    steps: tuple[Step, ...]
    checks: tuple[Check, ...]
    missing_information: tuple[str, ...]
    conflicts: tuple[Conflict, ...]

    @model_validator(mode="after")
    def _validate_status_content(self) -> "GenerationResult":
        if self.status == "insufficient_evidence":
            if self.title != "选定范围内未找到足够依据":
                raise ValueError("insufficient evidence must use the fixed local message")
            if self.preparations or self.steps or self.checks or self.conflicts:
                raise ValueError("insufficient evidence cannot contain factual items")
        elif not self.steps:
            raise ValueError("a complete how-to requires at least one step")
        return self

    @classmethod
    def insufficient(cls) -> "GenerationResult":
        return cls(
            status="insufficient_evidence",
            title="选定范围内未找到足够依据",
            preparations=(),
            steps=(),
            checks=(),
            missing_information=(),
            conflicts=(),
        )


class OutboundMessage(_FrozenModel):
    """The only content unit eligible for a provider request."""

    message_id: str = Field(min_length=1)
    text: str = Field(min_length=1, repr=False)

    @field_validator("message_id", "text")
    @classmethod
    def _reject_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("outbound message fields must be nonblank")
        return value


class GenerationBudget(_FrozenModel):
    max_messages: int = Field(gt=0, le=2_000_000)
    max_input_tokens: int = Field(gt=0, le=10_000_000)
    max_provider_calls: int = Field(gt=0, le=100)
    max_output_tokens: int = Field(default=4_000, gt=0, le=1_000_000)
