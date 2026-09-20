"""Factory for P1 export-job routes; application authority is injected by the caller."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
import json
import re
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Path, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from starlette.requests import Request
from starlette.responses import JSONResponse

from chat_to_howto.domain.jobs import ExportScope
from chat_to_howto.services.export_jobs import (
    AuthorizationError,
    ConsentGrant,
    ExportJobOrchestrator,
    ExportJobView,
    JobConflict,
    JobNotFound,
)


_OPAQUE_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
_OPAQUE = re.compile(_OPAQUE_PATTERN)
_HASH_PATTERN = r"^[0-9a-f]{64}$"
_SAFE_VALIDATION_LOCATIONS = frozenset(
    {
        "body",
        "path",
        "query",
        "header",
        "cookie",
        "scope",
        "confirmed",
        "consent_id",
        "idempotency_key",
        "manifest_sha256",
        "schema_version",
        "source_id",
        "account_id",
        "source_account_namespace",
        "conversations",
        "conversation_id",
        "display_name",
        "conversation_type",
        "range_start",
        "range_end",
        "output_root_id",
        "configuration_nonce",
        "operation",
        "display_timezone",
        "client_version",
        "tool_version",
        "account_version",
        "job_id",
        "batch_id",
    }
)


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)


class ConsentRequest(_Request):
    scope: ExportScope
    confirmed: Literal[True]

    @field_validator("scope", mode="before")
    @classmethod
    def _parse_json_scope(cls, value: object) -> object:
        return _bounded_scope(value)


class StartExportRequest(_Request):
    scope: ExportScope | None = None
    consent_id: str = Field(min_length=1, max_length=128, pattern=_OPAQUE_PATTERN)
    idempotency_key: str = Field(min_length=1, max_length=128, pattern=_OPAQUE_PATTERN)

    @field_validator("scope", mode="before")
    @classmethod
    def _parse_json_scope(cls, value: object) -> object:
        if value is None:
            return None
        return _bounded_scope(value)


class SelectionExportScopeRequest(_Request):
    """Browser-visible export selection containing only opaque P0 handles."""

    source_id: str = Field(min_length=1, max_length=128, pattern=_OPAQUE_PATTERN)
    account_id: str = Field(min_length=1, max_length=256)
    conversation_selection_ids: tuple[str, ...] = Field(min_length=1, max_length=50)
    range_start: datetime
    range_end: datetime
    output_root_id: str = Field(min_length=1, max_length=128, pattern=_OPAQUE_PATTERN)
    display_timezone: str = Field(min_length=1, max_length=128)

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
            not _OPAQUE.fullmatch(item) or item in {".", ".."}
            for item in value
        ):
            raise ValueError("selection handles must be unique opaque values")
        return value

    @field_validator("range_start", "range_end", mode="before")
    @classmethod
    def _parse_time(cls, value: object) -> object:
        if isinstance(value, str) and 1 <= len(value) <= 64:
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return value
        return value

    @field_validator("range_start", "range_end")
    @classmethod
    def _normalize_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("selection times must be timezone-aware")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def _validate_range(self) -> "SelectionExportScopeRequest":
        if self.range_start >= self.range_end:
            raise ValueError("selection range must be nonempty")
        return self


class SelectionConsentRequest(_Request):
    selection: SelectionExportScopeRequest
    confirmed: Literal[True]


class AcceptLimitationsRequest(_Request):
    manifest_sha256: str = Field(pattern=_HASH_PATTERN)


class EmptyMutationRequest(_Request):
    pass


class SafeValidationRoute(APIRoute):
    """Return useful validation locations without reflecting submitted values."""

    def get_route_handler(self):
        route_handler = super().get_route_handler()

        async def safe_route_handler(request: Request):
            try:
                return await route_handler(request)
            except RequestValidationError as error:
                detail = [
                    {
                        "type": item.get("type", "value_error"),
                        "loc": _safe_validation_location(item.get("loc")),
                        "msg": item.get("msg", "invalid request"),
                    }
                    for item in error.errors()
                ]
                return JSONResponse(status_code=422, content={"detail": detail})

        return safe_route_handler


def _safe_validation_location(value: object) -> tuple[str | int, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(
        item
        if isinstance(item, int)
        or (isinstance(item, str) and item in _SAFE_VALIDATION_LOCATIONS)
        else "field"
        for item in value
    )


def create_export_jobs_router(
    *,
    get_orchestrator: Callable[[], ExportJobOrchestrator],
    require_session: Callable[[], str],
    require_csrf: Callable[[], object],
    resolve_scope: Callable[[SelectionExportScopeRequest, str], ExportScope] | None = None,
) -> APIRouter:
    """Build routes without importing or mutating the P0 application singleton."""
    router = APIRouter(
        prefix="/api/export-jobs",
        tags=["export-jobs"],
        route_class=SafeValidationRoute,
    )
    @router.post(
        "/consents",
        response_model=ConsentGrant,
        status_code=status.HTTP_201_CREATED,
    )
    def issue_consent(
        request: ConsentRequest,
        service: ExportJobOrchestrator = Depends(get_orchestrator),
        session_id: str = Depends(require_session),
        _csrf: object = Depends(require_csrf),
    ) -> ConsentGrant:
        try:
            return service.issue_consent(
                session_id=session_id,
                scope=request.scope,
                confirmed=request.confirmed,
            )
        except Exception as error:
            raise _http_error(error) from None

    @router.post(
        "/consents/from-selections",
        response_model=ConsentGrant,
        status_code=status.HTTP_201_CREATED,
    )
    def issue_selection_consent(
        request: SelectionConsentRequest,
        service: ExportJobOrchestrator = Depends(get_orchestrator),
        session_id: str = Depends(require_session),
        _csrf: object = Depends(require_csrf),
    ) -> ConsentGrant:
        if resolve_scope is None:
            raise HTTPException(status_code=404, detail="selection export is unavailable")
        try:
            scope = resolve_scope(request.selection, session_id)
            return service.issue_consent(
                session_id=session_id,
                scope=scope,
                confirmed=request.confirmed,
            )
        except Exception as error:
            raise _http_error(error) from None

    @router.post("", response_model=ExportJobView, status_code=status.HTTP_202_ACCEPTED)
    def start_export(
        request: StartExportRequest,
        background_tasks: BackgroundTasks,
        service: ExportJobOrchestrator = Depends(get_orchestrator),
        session_id: str = Depends(require_session),
        _csrf: object = Depends(require_csrf),
    ) -> ExportJobView:
        try:
            if request.scope is None:
                job = service.start_export_from_consent(
                    session_id=session_id,
                    consent_id=request.consent_id,
                    idempotency_key=request.idempotency_key,
                )
            else:
                job = service.start_export(
                    session_id=session_id,
                    scope=request.scope,
                    consent_id=request.consent_id,
                    idempotency_key=request.idempotency_key,
                )
        except Exception as error:
            raise _http_error(error) from None
        if job.state.value == "pending_authorization":
            background_tasks.add_task(service.run_job, job.job_id)
        return job

    @router.get("/{job_id}", response_model=ExportJobView)
    def get_job(
        job_id: Annotated[str, Path(pattern=_OPAQUE_PATTERN, max_length=128)],
        service: ExportJobOrchestrator = Depends(get_orchestrator),
        session_id: str = Depends(require_session),
    ) -> ExportJobView:
        try:
            return service.get_job(job_id, session_id=session_id)
        except Exception as error:
            raise _http_error(error) from None

    @router.post("/{job_id}/cancel", response_model=ExportJobView)
    def cancel_job(
        job_id: Annotated[str, Path(pattern=_OPAQUE_PATTERN, max_length=128)],
        _request: EmptyMutationRequest,
        service: ExportJobOrchestrator = Depends(get_orchestrator),
        session_id: str = Depends(require_session),
        _csrf: object = Depends(require_csrf),
    ) -> ExportJobView:
        try:
            return service.cancel_job(job_id, session_id=session_id)
        except Exception as error:
            raise _http_error(error) from None

    @router.post("/{job_id}/accept-limitations", response_model=ExportJobView)
    def accept_limitations(
        job_id: Annotated[str, Path(pattern=_OPAQUE_PATTERN, max_length=128)],
        request: AcceptLimitationsRequest,
        service: ExportJobOrchestrator = Depends(get_orchestrator),
        session_id: str = Depends(require_session),
        _csrf: object = Depends(require_csrf),
    ) -> ExportJobView:
        try:
            return service.accept_limitations(
                job_id,
                session_id=session_id,
                manifest_sha256=request.manifest_sha256,
            )
        except Exception as error:
            raise _http_error(error) from None

    @router.post("/{job_id}/retry-index", response_model=ExportJobView)
    def retry_index(
        job_id: Annotated[str, Path(pattern=_OPAQUE_PATTERN, max_length=128)],
        _request: EmptyMutationRequest,
        service: ExportJobOrchestrator = Depends(get_orchestrator),
        session_id: str = Depends(require_session),
        _csrf: object = Depends(require_csrf),
    ) -> ExportJobView:
        try:
            return service.retry_index(job_id, session_id=session_id)
        except Exception as error:
            raise _http_error(error) from None

    @router.post("/{job_id}/retry-import", response_model=ExportJobView)
    def retry_import(
        job_id: Annotated[str, Path(pattern=_OPAQUE_PATTERN, max_length=128)],
        _request: EmptyMutationRequest,
        service: ExportJobOrchestrator = Depends(get_orchestrator),
        session_id: str = Depends(require_session),
        _csrf: object = Depends(require_csrf),
    ) -> ExportJobView:
        try:
            return service.retry_import(job_id, session_id=session_id)
        except Exception as error:
            raise _http_error(error) from None

    @router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_job(
        job_id: Annotated[str, Path(pattern=_OPAQUE_PATTERN, max_length=128)],
        _request: EmptyMutationRequest | None = None,
        service: ExportJobOrchestrator = Depends(get_orchestrator),
        session_id: str = Depends(require_session),
        _csrf: object = Depends(require_csrf),
    ) -> Response:
        try:
            service.delete_job(job_id, session_id=session_id)
        except Exception as error:
            raise _http_error(error) from None
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return router


def _http_error(error: Exception) -> HTTPException:
    if isinstance(error, AuthorizationError):
        return HTTPException(status_code=403, detail=str(error))
    if isinstance(error, JobNotFound):
        return HTTPException(status_code=404, detail=str(error))
    if isinstance(error, JobConflict):
        return HTTPException(status_code=409, detail=str(error))
    if isinstance(error, ValueError):
        return HTTPException(status_code=422, detail="invalid export request")
    return HTTPException(status_code=500, detail="export operation failed")


def _bounded_scope(value: object) -> object:
    if not isinstance(value, dict):
        return value
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > 1024 * 1024:
        raise ValueError("export scope exceeds the request limit")
    scope = ExportScope.model_validate_json(encoded)
    if len(scope.conversations) > 1000:
        raise ValueError("too many selected conversations")
    values = (
        scope.source_id,
        scope.account_id,
        scope.source_account_namespace,
        scope.output_root_id,
        scope.configuration_nonce,
        scope.display_timezone,
        scope.client_version,
        scope.tool_version,
        scope.account_version,
        *(
            field
            for conversation in scope.conversations
            for field in (
                conversation.conversation_id,
                conversation.display_name,
                conversation.conversation_type,
            )
        ),
    )
    if any(value is not None and len(value) > 512 for value in values):
        raise ValueError("export scope field exceeds the request limit")
    return scope
