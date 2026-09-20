"""Explicit impact-preview and fingerprint-confirmed local deletion routes."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, status
from pydantic import BaseModel, ConfigDict, Field

from chat_to_howto.api.export_jobs import SafeValidationRoute
from chat_to_howto.services.deletion import DeletionConflict, DeletionPlanView, DeletionResult, DeletionService


_OPAQUE_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"


class CreateDeletionPlanRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)
    target_kind: Literal["document", "batch"]
    target_id: str = Field(min_length=1, max_length=128, pattern=_OPAQUE_PATTERN)
    include_managed_files: bool


class ConfirmDeletionRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirmed: Literal[True]


def create_deletion_router(*, get_service: Callable[[], DeletionService],
                           require_session: Callable[[], str], require_csrf: Callable[[], object]) -> APIRouter:
    router = APIRouter(prefix="/api/deletion-plans", tags=["deletion"], route_class=SafeValidationRoute)

    @router.post("", response_model=DeletionPlanView, status_code=status.HTTP_201_CREATED)
    def create_plan(request: CreateDeletionPlanRequest, service: DeletionService = Depends(get_service),
                    session_id: str = Depends(require_session), _csrf: object = Depends(require_csrf)) -> DeletionPlanView:
        try:
            return service.create_plan(target_kind=request.target_kind, target_id=request.target_id,
                include_managed_files=request.include_managed_files, session_id=session_id)
        except PermissionError:
            raise HTTPException(status_code=403, detail="deletion target is unavailable") from None
        except ValueError:
            raise HTTPException(status_code=422, detail="invalid deletion target") from None
        except Exception:
            raise HTTPException(status_code=500, detail="deletion planning failed") from None

    @router.post("/{plan_id}/confirm", response_model=DeletionResult)
    def confirm_plan(plan_id: Annotated[str, Path(pattern=_OPAQUE_PATTERN, max_length=128)],
                     request: ConfirmDeletionRequest, service: DeletionService = Depends(get_service),
                     session_id: str = Depends(require_session), _csrf: object = Depends(require_csrf)) -> DeletionResult:
        try:
            return service.confirm(plan_id, fingerprint=request.fingerprint, confirmed=request.confirmed,
                session_id=session_id)
        except PermissionError:
            raise HTTPException(status_code=403, detail="deletion authorization is unavailable") from None
        except DeletionConflict:
            raise HTTPException(status_code=409, detail="deletion target changed") from None
        except Exception:
            raise HTTPException(status_code=500, detail="deletion failed") from None

    return router
