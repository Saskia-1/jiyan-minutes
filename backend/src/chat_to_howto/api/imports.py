"""Local-only manual import preview and commit endpoints."""

from __future__ import annotations

from base64 import b64decode
from binascii import Error as Base64Error
from collections.abc import Callable
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, model_validator

from chat_to_howto.api.export_jobs import SafeValidationRoute
from chat_to_howto.domain.imports import (
    ImportCommitView,
    ImportOptions,
    ImportOrigin,
    ImportPlatformHint,
    ImportPreviewView,
    ImportRecoveryPage,
)
from chat_to_howto.services.import_sessions import (
    ChatImportService,
    ImportAuthorizationError,
    ImportNotReady,
)


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)


class CreateImportPreviewRequest(_Request):
    origin: ImportOrigin
    platform_hint: ImportPlatformHint = "generic"
    filename: str | None = Field(default=None, min_length=1, max_length=255)
    payload_base64: str | None = Field(default=None, max_length=90_000_000, repr=False)
    pasted_text: str | None = Field(default=None, max_length=64 * 1024 * 1024, repr=False)
    options: ImportOptions = Field(default_factory=ImportOptions)

    @model_validator(mode="after")
    def _one_supported_payload(self) -> "CreateImportPreviewRequest":
        if self.origin == "imported_file":
            if self.filename is None or self.payload_base64 is None or self.pasted_text is not None:
                raise ValueError("file imports require one encoded file payload")
        elif self.pasted_text is None or self.payload_base64 is not None or self.filename is not None:
            raise ValueError("paste imports require one text payload")
        return self

    def decoded(self) -> tuple[str, bytes]:
        if self.origin == "paste":
            assert self.pasted_text is not None
            return "pasted-chat.txt", self.pasted_text.encode("utf-8")
        assert self.filename is not None and self.payload_base64 is not None
        try:
            payload = b64decode(self.payload_base64, validate=True)
        except (Base64Error, ValueError):
            raise ValueError("import payload encoding is invalid") from None
        return self.filename, payload


class CommitImportPreviewRequest(_Request):
    accept_skipped: bool = False
    preview_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirmed: Literal[True]


class RecoverImportsRequest(_Request):
    cursor: str | None = Field(default=None, min_length=1, max_length=256)
    limit: int = Field(default=20, ge=1, le=50)


def _preview_error_code(error: Exception) -> str:
    message = str(error).casefold()
    if "default_year" in message:
        return "missing_default_year"
    if any(term in message for term in ("limit", "too large", "compression ratio", "exceeds")):
        return "limit_exceeded"
    if any(term in message for term in ("encoding", "decode", "utf-", "gb18030")):
        return "invalid_encoding"
    if any(term in message for term in ("xlsx", "zip", "workbook", "worksheet", "package", "corrupt")):
        return "corrupt_container"
    if any(term in message for term in ("mapping", "column", "field")):
        return "mapping_invalid"
    if any(term in message for term in ("unsupported", "opaque", "extension")):
        return "unsupported_format"
    return "invalid_content"


def create_imports_router(
    *,
    get_service: Callable[[], ChatImportService],
    require_session: Callable[[], str],
    require_csrf: Callable[[], object],
) -> APIRouter:
    router = APIRouter(
        prefix="/api/imports",
        tags=["imports"],
        route_class=SafeValidationRoute,
    )

    @router.post(
        "/previews",
        response_model=ImportPreviewView,
        status_code=status.HTTP_201_CREATED,
    )
    def create_preview(
        request: CreateImportPreviewRequest,
        service: ChatImportService = Depends(get_service),
        session_id: str = Depends(require_session),
        _csrf: object = Depends(require_csrf),
    ) -> ImportPreviewView:
        try:
            filename, payload = request.decoded()
            return service.create_preview(
                filename=filename,
                payload=payload,
                origin=request.origin,
                platform_hint=request.platform_hint,
                options=request.options,
                session_id=session_id,
            )
        except (ValueError, UnicodeError) as error:
            raise HTTPException(
                status_code=422, detail={"code": _preview_error_code(error)}
            ) from None

    @router.post(
        "/recovery",
        response_model=ImportRecoveryPage,
    )
    def recover_imports(
        request: RecoverImportsRequest,
        service: ChatImportService = Depends(get_service),
        session_id: str = Depends(require_session),
        _csrf: object = Depends(require_csrf),
    ) -> ImportRecoveryPage:
        try:
            return service.recover_committed(
                session_id=session_id, cursor=request.cursor, limit=request.limit
            )
        except (ImportAuthorizationError, PermissionError):
            raise HTTPException(status_code=403, detail="import recovery is unavailable") from None
        except ImportNotReady:
            raise HTTPException(status_code=409, detail="import recovery is unavailable") from None
        except ValueError:
            raise HTTPException(status_code=422, detail="import recovery is invalid") from None

    @router.post(
        "/previews/{preview_id}/commit",
        response_model=ImportCommitView,
        status_code=status.HTTP_201_CREATED,
    )
    def commit_preview(
        preview_id: str,
        request: CommitImportPreviewRequest,
        service: ChatImportService = Depends(get_service),
        session_id: str = Depends(require_session),
        _csrf: object = Depends(require_csrf),
    ) -> ImportCommitView:
        if not preview_id or len(preview_id) > 128:
            raise HTTPException(status_code=404, detail="import preview is unavailable")
        try:
            return service.commit_preview(
                preview_id,
                preview_fingerprint=request.preview_fingerprint,
                confirmed=request.confirmed,
                accept_skipped=request.accept_skipped,
                session_id=session_id,
            )
        except ImportAuthorizationError:
            raise HTTPException(status_code=403, detail="import preview is unavailable") from None
        except ImportNotReady:
            raise HTTPException(status_code=409, detail="import preview has unresolved rows") from None
        except ValueError:
            raise HTTPException(status_code=422, detail="manual import is invalid") from None

    return router
