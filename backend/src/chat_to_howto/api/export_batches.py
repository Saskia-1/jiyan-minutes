"""Factory for session-bound P1 export-batch evidence routes."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path

from chat_to_howto.services.export_jobs import (
    AuthorizationError,
    ExportJobOrchestrator,
    JobNotFound,
)
from chat_to_howto.api.export_jobs import SafeValidationRoute


_OPAQUE_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"


def create_export_batches_router(
    *,
    get_orchestrator: Callable[[], ExportJobOrchestrator],
    require_session: Callable[[], str],
) -> APIRouter:
    """Build a read-only evidence router with caller-provided session authority."""
    router = APIRouter(
        prefix="/api/export-batches",
        tags=["export-batches"],
        route_class=SafeValidationRoute,
    )
    @router.get("/{batch_id}")
    def get_batch(
        batch_id: Annotated[str, Path(pattern=_OPAQUE_PATTERN, max_length=128)],
        service: ExportJobOrchestrator = Depends(get_orchestrator),
        session_id: str = Depends(require_session),
    ) -> dict[str, object]:
        try:
            return service.get_batch(batch_id, session_id=session_id)
        except AuthorizationError as error:
            raise HTTPException(status_code=403, detail=str(error)) from None
        except JobNotFound as error:
            raise HTTPException(status_code=404, detail=str(error)) from None

    return router
