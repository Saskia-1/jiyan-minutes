"""Append-only local how-to document and version contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from chat_to_howto.domain.generation import GenerationResult
from chat_to_howto.services.evidence_validation import ValidatedEvidence


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)


class DocumentVersion(_FrozenModel):
    document_id: str
    version_id: str
    ordinal: int = Field(gt=0)
    origin: Literal["ai_generated", "user_edited", "restored"]
    evidence_status: Literal["verified", "unverified"]
    parent_version_id: str | None
    restored_from_version_id: str | None
    source_generation_job_id: str
    change_summary: str
    created_at: datetime
    content: GenerationResult = Field(repr=False)


class HowToDocument(_FrozenModel):
    document_id: str
    source_generation_job_id: str
    source_snapshot_id: str
    created_at: datetime
    updated_at: datetime
    current_version: DocumentVersion = Field(repr=False)
    versions: tuple[DocumentVersion, ...] = Field(repr=False)
    source_evidence: tuple[ValidatedEvidence, ...] = Field(repr=False)


class DocumentRecoveryPage(_FrozenModel):
    documents: tuple[HowToDocument, ...]
    next_cursor: str | None = None
