"""Session-bound P3 document creation, append-only editing, and restoration."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status
from pydantic import BaseModel, ConfigDict, Field

from chat_to_howto.api.export_jobs import SafeValidationRoute
from chat_to_howto.domain.documents import DocumentRecoveryPage, HowToDocument
from chat_to_howto.domain.generation import GenerationResult
from chat_to_howto.services.documents import (
    DocumentAuthorizationError,
    DocumentConflict,
    DocumentNotFound,
    DocumentService,
)


_OPAQUE_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"


class _Request(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)


class CreateDocumentRequest(_Request):
    generation_job_id: str = Field(min_length=1, max_length=128, pattern=_OPAQUE_PATTERN)


class CreateVersionRequest(_Request):
    parent_version_id: str = Field(min_length=1, max_length=128, pattern=_OPAQUE_PATTERN)
    content: GenerationResult
    change_summary: str = Field(default="", max_length=500)


class EmptyDocumentMutation(_Request):
    pass


class RecoverDocumentsRequest(_Request):
    cursor: str | None = Field(default=None, min_length=1, max_length=256)
    limit: int = Field(default=20, ge=1, le=50)


def create_documents_router(
    *,
    get_service: Callable[[], DocumentService],
    require_session: Callable[[], str],
    require_csrf: Callable[[], object],
) -> APIRouter:
    router = APIRouter(
        prefix="/api/documents", tags=["documents"], route_class=SafeValidationRoute
    )

    @router.post(
        "/from-generation",
        response_model=HowToDocument,
        status_code=status.HTTP_201_CREATED,
    )
    def create_document(
        request: CreateDocumentRequest,
        service: DocumentService = Depends(get_service),
        session_id: str = Depends(require_session),
        _csrf: object = Depends(require_csrf),
    ) -> HowToDocument:
        try:
            return service.create_from_generation(
                request.generation_job_id, session_id=session_id
            )
        except Exception as error:
            raise _http_error(error) from None

    @router.post("/recovery", response_model=DocumentRecoveryPage)
    def recover_documents(
        request: RecoverDocumentsRequest,
        service: DocumentService = Depends(get_service),
        session_id: str = Depends(require_session),
        _csrf: object = Depends(require_csrf),
    ) -> DocumentRecoveryPage:
        try:
            return service.list_documents(
                session_id=session_id, cursor=request.cursor, limit=request.limit
            )
        except Exception as error:
            raise _http_error(error) from None

    @router.get("/{document_id}", response_model=HowToDocument)
    def get_document(
        document_id: Annotated[str, Path(pattern=_OPAQUE_PATTERN, max_length=128)],
        service: DocumentService = Depends(get_service),
        session_id: str = Depends(require_session),
    ) -> HowToDocument:
        try:
            return service.get_document(document_id, session_id=session_id)
        except Exception as error:
            raise _http_error(error) from None

    @router.post("/{document_id}/versions", response_model=HowToDocument)
    def create_version(
        document_id: Annotated[str, Path(pattern=_OPAQUE_PATTERN, max_length=128)],
        request: CreateVersionRequest,
        service: DocumentService = Depends(get_service),
        session_id: str = Depends(require_session),
        _csrf: object = Depends(require_csrf),
    ) -> HowToDocument:
        try:
            return service.create_version(
                document_id,
                parent_version_id=request.parent_version_id,
                content=request.content,
                change_summary=request.change_summary,
                session_id=session_id,
            )
        except Exception as error:
            raise _http_error(error) from None

    @router.post(
        "/{document_id}/versions/{version_id}/restore",
        response_model=HowToDocument,
    )
    def restore_version(
        document_id: Annotated[str, Path(pattern=_OPAQUE_PATTERN, max_length=128)],
        version_id: Annotated[str, Path(pattern=_OPAQUE_PATTERN, max_length=128)],
        _request: EmptyDocumentMutation,
        service: DocumentService = Depends(get_service),
        session_id: str = Depends(require_session),
        _csrf: object = Depends(require_csrf),
    ) -> HowToDocument:
        try:
            return service.restore_version(
                document_id, version_id=version_id, session_id=session_id
            )
        except Exception as error:
            raise _http_error(error) from None

    return router


def _http_error(error: Exception) -> HTTPException:
    if isinstance(error, DocumentAuthorizationError):
        return HTTPException(status_code=403, detail="document authorization is unavailable")
    if isinstance(error, DocumentNotFound):
        return HTTPException(status_code=404, detail="document was not found")
    if isinstance(error, DocumentConflict):
        return HTTPException(status_code=409, detail="document version changed or is invalid")
    if isinstance(error, ValueError):
        return HTTPException(status_code=422, detail="invalid document request")
    return HTTPException(status_code=500, detail="document operation failed")
