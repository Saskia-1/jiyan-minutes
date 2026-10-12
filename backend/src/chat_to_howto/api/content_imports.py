"""Session-bound local screenshot and note import routes."""

from __future__ import annotations

from base64 import b64decode
from binascii import Error as Base64Error
from collections.abc import Callable
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field, model_validator

from chat_to_howto.api.export_jobs import SafeValidationRoute
from chat_to_howto.domain.content_imports import (
    ContentBatchPage, ContentBatchView, ContentPreviewView, ImageOrderEdit, ImageUpload, UnitEdit,
)
from chat_to_howto.services.content_imports import ContentImportService


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)


class _ImageRequest(_Request):
    filename: str = Field(min_length=1, max_length=255)
    payload_base64: str = Field(min_length=1, max_length=21_000_000, repr=False)
    input_order: int = Field(ge=0, le=1000)


class CreateContentPreviewRequest(_Request):
    images: list[_ImageRequest] | None = Field(default=None, max_length=20)
    pasted_note: str | None = Field(default=None, max_length=1_048_576, repr=False)

    @model_validator(mode="after")
    def one_input(self) -> "CreateContentPreviewRequest":
        if (self.images is None) == (self.pasted_note is None):
            raise ValueError("one content input required")
        return self

    def uploads(self) -> tuple[ImageUpload, ...] | None:
        if self.images is None:
            return None
        try:
            return tuple(ImageUpload(item.filename, b64decode(item.payload_base64, validate=True), item.input_order) for item in self.images)
        except (Base64Error, ValueError):
            raise ValueError("invalid_image_encoding") from None


class ReviseContentPreviewRequest(_Request):
    edits: list[UnitEdit] = Field(max_length=10_000)
    image_orders: list[ImageOrderEdit] | None = Field(default=None, max_length=20)


class CommitContentPreviewRequest(_Request):
    preview_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirmed: Literal[True]


class RecoverContentBatchesRequest(_Request):
    cursor: str | None = Field(default=None, max_length=64)
    limit: int = Field(default=20, ge=1, le=50)


def _rejected(error: Exception) -> HTTPException:
    code = str(error)
    known = {
        "image_limit_exceeded", "note_limit_exceeded", "unsupported_image",
        "invalid_image_encoding", "image_order_invalid", "preview_capacity_exceeded",
    }
    return HTTPException(status_code=422, detail={"code": code if code in known else "invalid_content"})


def create_content_imports_router(
    *, get_service: Callable[[], ContentImportService],
    require_session: Callable[[], str], require_csrf: Callable[[], object],
) -> APIRouter:
    router = APIRouter(prefix="/api/content-imports", tags=["content-imports"], route_class=SafeValidationRoute)

    @router.post("/previews", response_model=ContentPreviewView, status_code=status.HTTP_201_CREATED)
    def create_preview(request: CreateContentPreviewRequest, service: ContentImportService = Depends(get_service), session_id: str = Depends(require_session), _csrf: object = Depends(require_csrf)) -> ContentPreviewView:
        try:
            return service.create_preview(request.uploads(), request.pasted_note, session_id=session_id)
        except ValueError as error:
            raise _rejected(error) from None

    @router.patch("/previews/{preview_id}", response_model=ContentPreviewView)
    def revise_preview(preview_id: str, request: ReviseContentPreviewRequest, service: ContentImportService = Depends(get_service), session_id: str = Depends(require_session), _csrf: object = Depends(require_csrf)) -> ContentPreviewView:
        try:
            return service.revise_preview(preview_id, tuple(request.edits), session_id=session_id, image_orders=tuple(request.image_orders) if request.image_orders is not None else None)
        except PermissionError:
            raise HTTPException(status_code=403, detail="content preview unavailable") from None
        except ValueError as error:
            raise _rejected(error) from None

    @router.post("/previews/{preview_id}/commit", response_model=ContentBatchView, status_code=status.HTTP_201_CREATED)
    def commit_preview(preview_id: str, request: CommitContentPreviewRequest, service: ContentImportService = Depends(get_service), session_id: str = Depends(require_session), _csrf: object = Depends(require_csrf)) -> ContentBatchView:
        try:
            return service.commit_preview(preview_id, request.preview_fingerprint, session_id=session_id)
        except PermissionError:
            raise HTTPException(status_code=403, detail="content preview unavailable") from None
        except ValueError:
            raise HTTPException(status_code=409, detail="content preview is not ready") from None

    @router.post("/recovery", response_model=ContentBatchPage)
    def recover_batches(request: RecoverContentBatchesRequest, service: ContentImportService = Depends(get_service), session_id: str = Depends(require_session), _csrf: object = Depends(require_csrf)) -> ContentBatchPage:
        try:
            return service.recover_batches(session_id=session_id, cursor=request.cursor, limit=request.limit)
        except (PermissionError, ValueError):
            raise HTTPException(status_code=403, detail="content recovery unavailable") from None

    @router.get("/images/{image_id}")
    def get_image(image_id: str, service: ContentImportService = Depends(get_service), session_id: str = Depends(require_session)) -> Response:
        try:
            data, mime = service.get_image(image_id, session_id=session_id)
        except (PermissionError, ValueError, OSError):
            raise HTTPException(status_code=404, detail="image unavailable") from None
        return Response(content=data, media_type=mime, headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})

    return router
