"""Local planning hints protected by the existing session and CSRF boundary."""

from collections.abc import Callable

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, field_validator

from chat_to_howto.api.export_jobs import SafeValidationRoute
from chat_to_howto.services.agent_planning import AgentPlan, plan_instruction


class AgentPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    instruction: str = Field(min_length=1, max_length=512, repr=False)

    @field_validator("instruction")
    @classmethod
    def _nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("instruction must be nonblank")
        return value


def create_agent_router(
    *, require_session: Callable[[], str], require_csrf: Callable[[], object],
) -> APIRouter:
    router = APIRouter(prefix="/api/agent", tags=["agent"], route_class=SafeValidationRoute)

    @router.post("/plans", response_model=AgentPlan)
    def create_plan(
        request: AgentPlanRequest,
        _session_id: str = Depends(require_session),
        _csrf: object = Depends(require_csrf),
    ) -> AgentPlan:
        return plan_instruction(request.instruction)

    return router
