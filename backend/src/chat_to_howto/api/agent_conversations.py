"""Strict, session and CSRF protected conversational agent REST boundary."""
from collections.abc import Callable
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response
from starlette.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from chat_to_howto.api.export_jobs import SafeValidationRoute
from chat_to_howto.services.conversation_agent import ConversationAgentService, ConversationError


class _Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, hide_input_in_errors=True)


class EmptyConversationMutation(_Strict):
    pass


class ConversationSource(_Strict):
    kind: Literal['chat_snapshot', 'content_batch']
    id: str = Field(min_length=1, max_length=128)
    label: str = Field(min_length=1, max_length=128)


class ConversationSources(_Strict):
    sources: list[ConversationSource] = Field(max_length=50)


class ConversationMessage(_Strict):
    automatic: bool = False
    text: str = Field(min_length=1, max_length=512, repr=False)
    client_message_id: str = Field(min_length=1, max_length=128)

    @field_validator('text', 'client_message_id')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('nonblank text required')
        return value


class ConversationApproval(_Strict):
    approval_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    preview_fingerprint: str = Field(pattern=r'^[0-9a-f]{64}$')
    confirmed: bool


class _NoStoreSafeRoute(SafeValidationRoute):
    def get_route_handler(self):
        original = super().get_route_handler()
        async def handler(request):
            try:
                response = await original(request)
            except HTTPException as error:
                response = JSONResponse(status_code=error.status_code,
                    content={'detail': error.detail}, headers=error.headers)
            response.headers['Cache-Control'] = 'no-store'
            return response
        return handler


def create_agent_conversations_router(*, get_service: Callable[[], ConversationAgentService],
        require_session: Callable, require_csrf: Callable) -> APIRouter:
    def no_store(response: Response):
        response.headers['Cache-Control'] = 'no-store'

    router = APIRouter(prefix='/api/agent/conversations', tags=['agent-conversations'],
        route_class=_NoStoreSafeRoute,
        dependencies=[Depends(require_csrf), Depends(no_store)])

    def call(method, *args, **kwargs):
        try:
            return method(*args, **kwargs)
        except LookupError:
            raise HTTPException(404, detail={'code':'conversation_not_found'}) from None
        except PermissionError:
            raise HTTPException(403, detail={'code':'agent_authorization_unavailable'}) from None
        except ConversationError as error:
            code = str(error)
            raise HTTPException(409, detail={'code': code}) from None
        except ValueError:
            raise HTTPException(422, detail={'code':'invalid_conversation_request'}) from None

    @router.get('')
    def list_conversations(service=Depends(get_service), session_id=Depends(require_session)):
        return call(service.list_conversations, session_id=session_id)

    @router.post('')
    def create_conversation(request: EmptyConversationMutation,
            service=Depends(get_service), session_id=Depends(require_session)):
        return call(service.create_conversation, session_id=session_id)

    @router.get('/{conversation_id}')
    def get_conversation(conversation_id: str,
            service=Depends(get_service), session_id=Depends(require_session)):
        return call(service.get_conversation, conversation_id, session_id=session_id)

    @router.post('/{conversation_id}/sources')
    def replace_sources(conversation_id: str, request: ConversationSources,
            service=Depends(get_service), session_id=Depends(require_session)):
        return call(service.replace_sources, conversation_id,
            sources=[source.model_dump() for source in request.sources], session_id=session_id)

    @router.post('/{conversation_id}/messages')
    def send_message(conversation_id: str, request: ConversationMessage,
            background_tasks: BackgroundTasks,
            service=Depends(get_service), session_id=Depends(require_session)):
        result = call(service.send_message, conversation_id, text=request.text,
            client_message_id=request.client_message_id, session_id=session_id, automatic=request.automatic)
        run = result.get('active_run')
        if request.automatic and run and run['state'] == 'running':
            background_tasks.add_task(service.run_automatic, conversation_id, run['run_id'],
                session_id=session_id)
        return result

    @router.post('/{conversation_id}/runs/{run_id}/approve')
    def approve(conversation_id: str, run_id: str, request: ConversationApproval,
            background_tasks: BackgroundTasks,
            service=Depends(get_service), session_id=Depends(require_session)):
        result = call(service.approve, conversation_id, run_id, approval_id=request.approval_id,
            preview_fingerprint=request.preview_fingerprint, confirmed=request.confirmed,
            session_id=session_id)
        background_tasks.add_task(service.advance, conversation_id, run_id, session_id=session_id)
        return result

    @router.post('/{conversation_id}/runs/{run_id}/cancel')
    def cancel(conversation_id: str, run_id: str, request: EmptyConversationMutation,
            service=Depends(get_service), session_id=Depends(require_session)):
        return call(service.cancel, conversation_id, run_id, session_id=session_id)

    return router
