"""Local and separately authorized AI plans under the session and CSRF boundary."""

from collections.abc import Callable

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, ConfigDict, Field, field_validator

from chat_to_howto.api.export_jobs import SafeValidationRoute
from chat_to_howto.services.agent_planning import AgentPlan, plan_instruction
from chat_to_howto.services.ai_agent_planning import (
    AIAgentPlanningService, AgentPlanPreview, AgentPlanningDailyLimit, AgentPlanningFailure,
)


class AgentPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    instruction: str = Field(min_length=1, max_length=512, repr=False)

    @field_validator("instruction")
    @classmethod
    def _nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("instruction must be nonblank")
        return value


class AgentPlanAuthorization(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    preview_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    preview_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirmed: bool


class EmptyAgentMutation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)


def create_agent_router(
    *, require_session: Callable[[], str], require_csrf: Callable[[], object],
    get_ai_service: Callable[[], AIAgentPlanningService],
) -> APIRouter:
    router = APIRouter(prefix="/api/agent", tags=["agent"], route_class=SafeValidationRoute)

    @router.post("/plans", response_model=AgentPlan)
    def create_plan(
        request: AgentPlanRequest,
        _session_id: str = Depends(require_session),
        _csrf: object = Depends(require_csrf),
    ) -> AgentPlan:
        return plan_instruction(request.instruction)

    @router.post("/plan-previews", response_model=AgentPlanPreview)
    def create_ai_preview(
        request: AgentPlanRequest,
        service: AIAgentPlanningService = Depends(get_ai_service),
        session_id: str = Depends(require_session),
        _csrf: object = Depends(require_csrf),
    ) -> AgentPlanPreview:
        try:
            return service.create_preview(request.instruction, session_id=session_id)
        except ValueError:
            raise HTTPException(status_code=422, detail="invalid agent preview") from None

    @router.post("/plans/execute", response_model=AgentPlan)
    def execute_ai_plan(
        request: AgentPlanAuthorization,
        service: AIAgentPlanningService = Depends(get_ai_service),
        session_id: str = Depends(require_session),
        _csrf: object = Depends(require_csrf),
    ) -> AgentPlan:
        try:
            return service.execute_authorized(request.preview_id,
                preview_fingerprint=request.preview_fingerprint, confirmed=request.confirmed,
                session_id=session_id)
        except AgentPlanningDailyLimit:
            raise HTTPException(status_code=429, detail={"code": "daily_usage_limit"}) from None
        except PermissionError:
            raise HTTPException(status_code=403, detail={"code": "planning_authorization_unavailable"}) from None
        except AgentPlanningFailure as error:
            raise HTTPException(status_code=503, detail={"code": str(error)}) from None

    @router.post("/plan-previews/{preview_id}/cancel")
    def cancel_ai_plan(
        request: EmptyAgentMutation,
        preview_id: str = Path(pattern=r"^[0-9a-f]{32}$"),
        service: AIAgentPlanningService = Depends(get_ai_service),
        session_id: str = Depends(require_session),
        _csrf: object = Depends(require_csrf),
    ) -> dict[str, bool]:
        try:
            return {"cancelled": service.cancel(preview_id, session_id=session_id)}
        except PermissionError:
            raise HTTPException(status_code=403, detail={"code": "planning_authorization_unavailable"}) from None

    return router
