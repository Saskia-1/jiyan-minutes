"""Session-bound P2 preview, consent, job, cancellation, and retry routes."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
import re
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Path, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from chat_to_howto.api.export_jobs import SafeValidationRoute
from chat_to_howto.domain.generation import SummaryType
from chat_to_howto.domain.mixed_sources import SourceRef
from chat_to_howto.services.generation_jobs import (
    GenerationAuthorizationError,
    GenerationConsentGrant,
    GenerationJobConflict,
    GenerationJobNotFound,
    GenerationJobOrchestrator,
    GenerationJobView,
    GenerationPreviewView,
    GenerationContextView,
)
from chat_to_howto.services.generation_planning import (
    GenerationBudgetExceeded,
    GenerationSelection,
    MixedGenerationSelection,
)
from chat_to_howto.services.topic_expansion import TopicExpansionService, TopicExpansionView


_OPAQUE_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
_OPAQUE = re.compile(_OPAQUE_PATTERN)


class _Request(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)


class SelectionGenerationRequest(_Request):
    source_id: str = Field(min_length=1, max_length=128, pattern=_OPAQUE_PATTERN)
    account_id: str = Field(min_length=1, max_length=256)
    conversation_selection_ids: tuple[str, ...] = Field(min_length=1, max_length=50)
    snapshot_id: str = Field(min_length=1, max_length=128, pattern=_OPAQUE_PATTERN)
    start_time: datetime
    end_time: datetime
    topic: str = Field(min_length=1, max_length=512, repr=False)
    mode: Literal["fast", "complete"]
    generation_kind: Literal["guide", "summary"] = "guide"
    summary_type: SummaryType | None = None
    include_undated: bool = False
    included_message_ids: tuple[str, ...] = Field(default=(), max_length=2_000)
    excluded_message_ids: tuple[str, ...] = Field(default=(), max_length=2_000)

    @field_validator("conversation_selection_ids", mode="before")
    @classmethod
    def _parse_handles(cls, value: object) -> object:
        if isinstance(value, list) and len(value) <= 50:
            return tuple(value)
        return value

    @field_validator("conversation_selection_ids")
    @classmethod
    def _validate_handles(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value) or any(
            not _OPAQUE.fullmatch(item) or item in {".", ".."} for item in value
        ):
            raise ValueError("selection handles must be unique opaque values")
        return value

    @field_validator("excluded_message_ids", "included_message_ids", mode="before")
    @classmethod
    def _parse_excluded_message_ids(cls, value: object) -> object:
        if isinstance(value, list) and len(value) <= 2_000:
            return tuple(value)
        return value

    @field_validator("excluded_message_ids", "included_message_ids")
    @classmethod
    def _validate_excluded_message_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value) or any(
            not _OPAQUE.fullmatch(item) or item in {".", ".."} for item in value
        ):
            raise ValueError("excluded message ids must be unique opaque values")
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
    def _validate_range(self) -> "SelectionGenerationRequest":
        if self.start_time >= self.end_time:
            raise ValueError("generation range must be nonempty")
        if not self.topic.strip():
            raise ValueError("generation topic must be nonblank")
        return self


class GenerationConsentRequest(_Request):
    preview_id: str = Field(min_length=1, max_length=128, pattern=_OPAQUE_PATTERN)
    confirmed: Literal[True]


class MixedGenerationRequest(_Request):
    source_refs: tuple[SourceRef, ...] = Field(min_length=1, max_length=50)
    included_unit_ids: tuple[str, ...] = Field(min_length=1, max_length=2_000)
    excluded_unit_ids: tuple[str, ...] = Field(default=(), max_length=2_000)
    topic: str = Field(min_length=1, max_length=512, repr=False)
    mode: Literal["fast", "complete"]
    summary_type: SummaryType
    include_undated: bool = False
    start_time: datetime | None = None
    end_time: datetime | None = None

    @field_validator("source_refs", "included_unit_ids", "excluded_unit_ids", mode="before")
    @classmethod
    def _parse_sequences(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value


class StartGenerationRequest(_Request):
    consent_id: str = Field(min_length=1, max_length=128, pattern=_OPAQUE_PATTERN)
    idempotency_key: str = Field(min_length=1, max_length=128, pattern=_OPAQUE_PATTERN)


class EmptyGenerationMutation(_Request):
    pass


class TopicExpansionRequest(_Request):
    topic: str = Field(min_length=1, max_length=512, repr=False)


def create_generation_router(
    *,
    get_orchestrator: Callable[[], GenerationJobOrchestrator],
    get_topic_expansion: Callable[[], TopicExpansionService],
    require_session: Callable[[], str],
    require_csrf: Callable[[], object],
    resolve_selection: Callable[[SelectionGenerationRequest, str], GenerationSelection],
    resolve_mixed_selection: Callable[[MixedGenerationRequest, str], MixedGenerationSelection] | None = None,
) -> APIRouter:
    router = APIRouter(
        prefix="/api/generation",
        tags=["generation"],
        route_class=SafeValidationRoute,
    )

    @router.post("/topic-expansion", response_model=TopicExpansionView)
    def expand_topic(
        request: TopicExpansionRequest,
        service: TopicExpansionService = Depends(get_topic_expansion),
        _session_id: str = Depends(require_session),
        _csrf: object = Depends(require_csrf),
    ) -> TopicExpansionView:
        try:
            return service.expand(request.topic)
        except ValueError:
            raise HTTPException(status_code=422, detail="invalid topic") from None

    @router.post(
        "/contexts/from-selections", response_model=GenerationContextView,
    )
    def preview_context(
        request: SelectionGenerationRequest,
        service: GenerationJobOrchestrator = Depends(get_orchestrator),
        session_id: str = Depends(require_session),
        _csrf: object = Depends(require_csrf),
    ) -> GenerationContextView:
        try:
            return service.preview_context(resolve_selection(request, session_id), session_id=session_id)
        except Exception as error:
            raise _http_error(error) from None

    @router.post(
        "/previews/from-selections",
        response_model=GenerationPreviewView,
        status_code=status.HTTP_201_CREATED,
    )
    def create_preview(
        request: SelectionGenerationRequest,
        service: GenerationJobOrchestrator = Depends(get_orchestrator),
        session_id: str = Depends(require_session),
        _csrf: object = Depends(require_csrf),
    ) -> GenerationPreviewView:
        try:
            return service.create_preview(
                resolve_selection(request, session_id), session_id=session_id
            )
        except Exception as error:
            raise _http_error(error) from None

    @router.post("/mixed/previews", response_model=GenerationPreviewView, status_code=status.HTTP_201_CREATED)
    def create_mixed_preview(
        request: MixedGenerationRequest,
        service: GenerationJobOrchestrator = Depends(get_orchestrator),
        session_id: str = Depends(require_session),
        _csrf: object = Depends(require_csrf),
    ) -> GenerationPreviewView:
        try:
            if resolve_mixed_selection is None:
                raise GenerationAuthorizationError("mixed generation is unavailable")
            return service.create_mixed_preview(resolve_mixed_selection(request, session_id), session_id=session_id)
        except Exception as error:
            raise _http_error(error) from None

    @router.post(
        "/consents",
        response_model=GenerationConsentGrant,
        status_code=status.HTTP_201_CREATED,
    )
    def create_consent(
        request: GenerationConsentRequest,
        service: GenerationJobOrchestrator = Depends(get_orchestrator),
        session_id: str = Depends(require_session),
        _csrf: object = Depends(require_csrf),
    ) -> GenerationConsentGrant:
        try:
            return service.issue_consent(
                request.preview_id,
                session_id=session_id,
                confirmed=request.confirmed,
            )
        except Exception as error:
            raise _http_error(error) from None

    @router.post(
        "/jobs",
        response_model=GenerationJobView,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def start_job(
        request: StartGenerationRequest,
        background_tasks: BackgroundTasks,
        service: GenerationJobOrchestrator = Depends(get_orchestrator),
        session_id: str = Depends(require_session),
        _csrf: object = Depends(require_csrf),
    ) -> GenerationJobView:
        try:
            job = service.start_job(
                request.consent_id,
                session_id=session_id,
                idempotency_key=request.idempotency_key,
            )
        except Exception as error:
            raise _http_error(error) from None
        if job.state == "pending":
            background_tasks.add_task(service.run_job, job.job_id)
        return job

    @router.get("/jobs/{job_id}", response_model=GenerationJobView)
    def get_job(
        job_id: Annotated[str, Path(pattern=_OPAQUE_PATTERN, max_length=128)],
        service: GenerationJobOrchestrator = Depends(get_orchestrator),
        session_id: str = Depends(require_session),
    ) -> GenerationJobView:
        try:
            return service.get_job(job_id, session_id=session_id)
        except Exception as error:
            raise _http_error(error) from None

    @router.post("/jobs/{job_id}/cancel", response_model=GenerationJobView)
    def cancel_job(
        job_id: Annotated[str, Path(pattern=_OPAQUE_PATTERN, max_length=128)],
        _request: EmptyGenerationMutation,
        service: GenerationJobOrchestrator = Depends(get_orchestrator),
        session_id: str = Depends(require_session),
        _csrf: object = Depends(require_csrf),
    ) -> GenerationJobView:
        try:
            return service.cancel_job(job_id, session_id=session_id)
        except Exception as error:
            raise _http_error(error) from None

    @router.post("/jobs/{job_id}/retry", response_model=GenerationJobView)
    def retry_job(
        job_id: Annotated[str, Path(pattern=_OPAQUE_PATTERN, max_length=128)],
        _request: EmptyGenerationMutation,
        background_tasks: BackgroundTasks,
        service: GenerationJobOrchestrator = Depends(get_orchestrator),
        session_id: str = Depends(require_session),
        _csrf: object = Depends(require_csrf),
    ) -> GenerationJobView:
        try:
            job = service.retry_job(job_id, session_id=session_id)
        except Exception as error:
            raise _http_error(error) from None
        background_tasks.add_task(service.run_job, job.job_id)
        return job

    return router


def _http_error(error: Exception) -> HTTPException:
    if isinstance(error, (GenerationAuthorizationError, PermissionError)):
        return HTTPException(status_code=403, detail="generation authorization is unavailable")
    if isinstance(error, GenerationJobNotFound):
        return HTTPException(status_code=404, detail="generation job was not found")
    if isinstance(error, GenerationJobConflict):
        return HTTPException(status_code=409, detail="generation job changed")
    if isinstance(error, (GenerationBudgetExceeded, ValueError)):
        return HTTPException(status_code=422, detail="invalid generation request")
    return HTTPException(status_code=500, detail="generation operation failed")
