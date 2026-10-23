"""REST schemas, body-safe errors and per-request session/CSRF protection."""
from datetime import datetime, timezone
import json
from types import SimpleNamespace

from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient

from chat_to_howto.api.agent_conversations import create_agent_conversations_router
from chat_to_howto.api.session import digest
from chat_to_howto.providers.base import ProviderResponse
from chat_to_howto.repositories.chat import ChatRepository
from chat_to_howto.repositories.content import ContentRepository
from chat_to_howto.services.conversation_agent import ConversationAgentService
from chat_to_howto.services.mixed_search import MixedSearchService


def test_conversation_rest_requires_csrf_owner_safe_validation_and_background_advances(tmp_path):
    now=datetime(2026,10,23,tzinfo=timezone.utc)
    repo=ChatRepository(tmp_path/'rest.sqlite3')
    content=ContentRepository(repo)
    content.save_batch(batch_id='source',session_digest=digest('owner'),created_at_utc=now.isoformat(),images=[],units=[dict(unit_id='unit:1',image_id=None,kind='note_passage',group_id='g',source_order=0,text='创业合成正文',sender_name=None,sent_at=None)])
    requests=[]
    class Provider:
        name='fake'
        async def generate(self,request,cancellation):
            requests.append(request)
            return ProviderResponse(provider='fake',model=request.model,content=json.dumps({'tool':'ask_user','arguments':{'question':'请补充主题'}}),finish_reason='stop')
    service=ConversationAgentService(repository=repo,mixed_search=MixedSearchService(repo,content),generation=SimpleNamespace(),documents=SimpleNamespace(),provider_factory=lambda _:Provider(),provider='fake',model='fixture',clock=lambda:now)
    def session(x_session: str|None=Header(default=None)):
        if not x_session:raise HTTPException(401,detail={'code':'session_required'})
        return x_session
    def csrf(x_csrf: str|None=Header(default=None)):
        if x_csrf!='yes':raise HTTPException(403,detail={'code':'csrf_required'})
    app=FastAPI()
    app.include_router(create_agent_conversations_router(get_service=lambda:service,require_session=session,require_csrf=csrf))
    with TestClient(app) as client:
        headers={'X-Session':'owner','X-CSRF':'yes'}
        assert client.get('/api/agent/conversations').status_code==403
        created=client.post('/api/agent/conversations',headers=headers,json={})
        assert created.status_code==200 and created.headers['cache-control']=='no-store'
        cid=created.json()['conversation_id'];base=f'/api/agent/conversations/{cid}'
        assert client.get(base,headers={**headers,'X-Session':'other'}).status_code==404
        bad=client.post(base+'/messages',headers=headers,json={'text':'PRIVATE'+'x'*512,'client_message_id':'1'})
        assert bad.status_code==422 and 'PRIVATE' not in bad.text
        assert bad.headers['cache-control']=='no-store'
        assert client.post(base+'/sources',headers=headers,json={'sources':[dict(kind='content_batch',id='source',label='笔记')]}).status_code==200
        started=client.post(base+'/messages',headers=headers,json={'text':'创业','client_message_id':'1'}).json()
        assert not requests and 'runtime' not in started and 'state' not in started
        run=started['active_run'];a=run['approval']
        payload=dict(approval_id=a['approval_id'],preview_fingerprint=a['preview_fingerprint'],confirmed=True)
        approved=client.post(base+f"/runs/{run['run_id']}/approve",headers=headers,json=payload)
        assert approved.status_code==200 and approved.json()['active_run']['state']=='running'
        final=client.get(base,headers=headers).json()
        assert final['active_run']['state']=='needs_input' and len(requests)==1
        replay=client.post(base+f"/runs/{run['run_id']}/approve",headers=headers,json=payload)
        assert replay.status_code==403
        assert replay.headers['cache-control']=='no-store'
        assert set(final)=={'conversation_id','title','revision','created_at','updated_at','sources','messages','active_run'}
