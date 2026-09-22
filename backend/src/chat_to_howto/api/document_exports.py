"""Local-only Markdown and DOCX document export endpoint."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Response, status
from urllib.parse import quote
from pydantic import BaseModel, ConfigDict, Field

from chat_to_howto.api.export_jobs import SafeValidationRoute
from chat_to_howto.services.document_export import DocumentArtifact, DocumentExportService
from chat_to_howto.services.documents import DocumentAuthorizationError, DocumentNotFound


_OPAQUE_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"


class ExportDocumentRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)

    version_id: str = Field(min_length=1, max_length=128, pattern=_OPAQUE_PATTERN)
    format: Literal["markdown", "docx"]
    source_mode: Literal["body_only", "with_sources"]
    output_root_id: str = Field(min_length=1, max_length=128, pattern=_OPAQUE_PATTERN)


class DownloadDocumentRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)
    version_id: str = Field(min_length=1, max_length=128, pattern=_OPAQUE_PATTERN)
    format: Literal["markdown", "docx"]
    source_mode: Literal["body_only", "with_sources"] = "body_only"


def create_document_exports_router(
    *, get_service: Callable[[], DocumentExportService], require_session: Callable[[], str],
    require_csrf: Callable[[], object],
) -> APIRouter:
    router = APIRouter(prefix="/api/documents", tags=["document-exports"], route_class=SafeValidationRoute)

    @router.post("/{document_id}/download")
    def download_document(
        document_id: Annotated[str, Path(pattern=_OPAQUE_PATTERN, max_length=128)],
        request: DownloadDocumentRequest,
        service: DocumentExportService = Depends(get_service),
        session_id: str = Depends(require_session),
        _csrf: object = Depends(require_csrf),
    ) -> Response:
        try:
            filename, payload = service.download(document_id, version_id=request.version_id,
                format=request.format, source_mode=request.source_mode, session_id=session_id)
            return Response(payload, media_type=("text/markdown; charset=utf-8" if request.format == "markdown"
                else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
                headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
                         "Cache-Control": "no-store"})
        except DocumentAuthorizationError:
            raise HTTPException(status_code=403, detail="document authorization is unavailable") from None
        except DocumentNotFound:
            raise HTTPException(status_code=404, detail="document was not found") from None
        except ValueError:
            raise HTTPException(status_code=422, detail="invalid document download") from None

    @router.post("/{document_id}/exports", response_model=DocumentArtifact, status_code=status.HTTP_201_CREATED)
    def export_document(
        document_id: Annotated[str, Path(pattern=_OPAQUE_PATTERN, max_length=128)],
        request: ExportDocumentRequest,
        service: DocumentExportService = Depends(get_service),
        session_id: str = Depends(require_session),
        _csrf: object = Depends(require_csrf),
    ) -> DocumentArtifact:
        try:
            return service.export(document_id, version_id=request.version_id, format=request.format,
                source_mode=request.source_mode, output_root_id=request.output_root_id, session_id=session_id)
        except DocumentAuthorizationError:
            raise HTTPException(status_code=403, detail="document authorization is unavailable") from None
        except DocumentNotFound:
            raise HTTPException(status_code=404, detail="document was not found") from None
        except PermissionError:
            raise HTTPException(status_code=403, detail="document output is unavailable") from None
        except ValueError:
            raise HTTPException(status_code=422, detail="invalid document export request") from None
        except Exception:
            raise HTTPException(status_code=500, detail="document export failed") from None

    return router
